"""
SHADOW MODE v2: TREND_CONTINUATION Signal Collection
====================================================
Inverted approach: find M5 displacement bars first, then check if conditions
align for CONTINUATION at that moment.

Logic:
  1. Scan ALL M5 bars for valid displacement (range > 0.8*ATR_M30, directional, close near extreme)
  2. At each displacement bar, check:
     a. Phase = TREND or EXPANSION
     b. Daily trend aligned with displacement direction
     c. Price near liq_top (LONG) or liq_bot (SHORT) -- the continuation zone
     d. NOT a pullback scenario
     e. Exhaustion check (overextension)
  3. For matching signals, log full gate details
  4. Stop at 40 signals

This matches production: event_processor checks displacement when a liquidity
touch triggers a gate check. The displacement bar must exist at that moment.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import glob

M30_BOXES   = Path(r"C:\data\processed\gc_m30_boxes.parquet")
M5_BOXES    = Path(r"C:\data\processed\gc_m5_boxes.parquet")
FEATURES_V4 = Path(r"C:\data\processed\gc_ats_features_v4.parquet")
MICRO_DIR   = Path(r"C:\data\level2\_gc_xcec")

START_DATE = "2025-07-01"
TREND_ACCEPTANCE_MIN_BARS = 4
TARGET_SIGNALS = 40

# Thresholds
DISP_ATR_MULT = 0.8
DISP_MIN_DELTA = 80
DISP_CLOSE_PCT = 0.7
OVEREXT_ATR_MULT = 1.5
V1_DIST = 8.0  # pts proximity to liq level


def load_data():
    m30 = pd.read_parquet(M30_BOXES)
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30 = m30[m30.index >= START_DATE].copy()

    m5 = pd.read_parquet(M5_BOXES)
    if m5.index.tz is None:
        m5.index = m5.index.tz_localize("UTC")
    m5 = m5[m5.index >= START_DATE].copy()
    m5["range"] = m5["high"] - m5["low"]

    feat = pd.read_parquet(FEATURES_V4, columns=["daily_jac_dir"])
    if feat.index.tz is None:
        feat.index = feat.index.tz_localize("UTC")
    feat = feat[feat.index >= START_DATE]
    m30_trend = feat["daily_jac_dir"].resample("30min").ffill()
    m30_trend = m30_trend.map({"up": "long", "down": "short"})
    m30["daily_trend"] = m30_trend.reindex(m30.index, method="ffill")

    # Also propagate daily_trend to M5
    m5_trend = feat["daily_jac_dir"].resample("5min").ffill()
    m5_trend = m5_trend.map({"up": "long", "down": "short"})
    m5["daily_trend"] = m5_trend.reindex(m5.index, method="ffill")

    # Propagate M30 ATR, liq levels, box to M5
    m5["atr_m30"] = m30["atr14"].reindex(m5.index, method="ffill")
    m5["m30_liq_top"] = m30["m30_liq_top"].reindex(m5.index, method="ffill")
    m5["m30_liq_bot"] = m30["m30_liq_bot"].reindex(m5.index, method="ffill")
    m5["m30_box_high"] = m30["m30_box_high"].reindex(m5.index, method="ffill")
    m5["m30_box_low"] = m30["m30_box_low"].reindex(m5.index, method="ffill")
    m5["m30_box_confirmed"] = m30["m30_box_confirmed"].reindex(m5.index, method="ffill")
    m5["m30_box_id"] = m30["m30_box_id"].reindex(m5.index, method="ffill")
    m5["m30_fmv"] = m30["m30_fmv"].reindex(m5.index, method="ffill")

    print(f"M30: {len(m30)} bars")
    print(f"M5: {len(m5)} bars")
    return m30, m5


def load_micro_delta_m5_batch(dates):
    """Pre-load microstructure bar_delta resampled to M5 for a set of dates."""
    all_delta = []
    for date_str in dates:
        path = MICRO_DIR / f"microstructure_{date_str}.csv.gz"
        if not path.exists():
            continue
        try:
            micro = pd.read_csv(path, compression="gzip", usecols=["timestamp", "bar_delta"])
            micro["timestamp"] = pd.to_datetime(micro["timestamp"], utc=True)
            micro = micro.set_index("timestamp").sort_index()
            m5_delta = micro["bar_delta"].resample("5min").sum()
            all_delta.append(m5_delta)
        except Exception:
            continue
    if all_delta:
        return pd.concat(all_delta).sort_index()
    return pd.Series(dtype=float)


def detect_box_ladder_at_m30(m30, ts, trend_direction, min_boxes=3):
    """Check ladder using M30 data up to timestamp ts."""
    subset = m30[m30.index <= ts]
    confirmed = subset[subset["m30_box_confirmed"] == True]
    if confirmed.empty:
        return False
    boxes = confirmed.groupby("m30_box_id")["m30_fmv"].first().sort_index()
    if len(boxes) < min_boxes:
        return False
    last_n = boxes.iloc[-min_boxes:].values
    if trend_direction == "long":
        return all(last_n[i] > last_n[i-1] for i in range(1, len(last_n)))
    elif trend_direction == "short":
        return all(last_n[i] < last_n[i-1] for i in range(1, len(last_n)))
    return False


def count_bars_outside_m30(m30, ts, box_high, box_low, lookback=10):
    """Count consecutive M30 bars outside box, looking backward from ts."""
    subset = m30[m30.index < ts].tail(lookback)
    count = 0
    for i in range(len(subset) - 1, -1, -1):
        close = float(subset.iloc[i]["close"])
        if close <= 0 or box_low <= close <= box_high:
            break
        count += 1
    return count


def compute_phase_at(m30, ts, daily_trend):
    """Compute phase at a given timestamp using M30 data."""
    # Find the M30 bar at or before ts
    m30_before = m30[m30.index <= ts]
    if m30_before.empty:
        return "UNKNOWN", {}

    row = m30_before.iloc[-1]
    close = float(row["close"])
    box_high = row.get("m30_box_high")
    box_low = row.get("m30_box_low")
    confirmed = bool(row.get("m30_box_confirmed", False))

    if pd.isna(box_high) or pd.isna(box_low) or close <= 0:
        return "UNKNOWN", {}

    box_high, box_low = float(box_high), float(box_low)

    if box_low <= close <= box_high:
        return "CONTRACTION", {}

    bars_out = count_bars_outside_m30(m30, ts, box_high, box_low)

    if confirmed and daily_trend in ("long", "short"):
        has_ladder = detect_box_ladder_at_m30(m30, ts, daily_trend)
        if has_ladder and bars_out >= TREND_ACCEPTANCE_MIN_BARS:
            return "TREND", {"bars_outside": bars_out, "ladder": True}
        elif has_ladder:
            return "EXPANSION", {"bars_outside": bars_out, "ladder": True, "blocked_t4": True}
        else:
            return "EXPANSION", {"bars_outside": bars_out, "ladder": False}

    return "EXPANSION", {"bars_outside": bars_out}


def main():
    print("SHADOW MODE v2: TREND_CONTINUATION (displacement-first scan)")
    print(f"Target: {TARGET_SIGNALS} signals")
    print(f"Period: {START_DATE} to today")
    print()

    m30, m5 = load_data()

    # Step 1: Find all M5 displacement bars
    print("Step 1: Finding M5 displacement bars...")
    m5["min_range"] = m5["atr_m30"] * DISP_ATR_MULT
    m5["close_pct_long"] = (m5["close"] - m5["low"]) / m5["range"].replace(0, np.nan)
    m5["close_pct_short"] = (m5["high"] - m5["close"]) / m5["range"].replace(0, np.nan)

    # LONG displacement: bullish bar, range > threshold, close near high
    long_disp = m5[
        (m5["close"] > m5["open"]) &
        (m5["range"] >= m5["min_range"]) &
        (m5["close_pct_long"] >= DISP_CLOSE_PCT)
    ].copy()
    long_disp["disp_dir"] = "LONG"

    # SHORT displacement: bearish bar, range > threshold, close near low
    short_disp = m5[
        (m5["close"] < m5["open"]) &
        (m5["range"] >= m5["min_range"]) &
        (m5["close_pct_short"] >= DISP_CLOSE_PCT)
    ].copy()
    short_disp["disp_dir"] = "SHORT"

    all_disp = pd.concat([long_disp, short_disp]).sort_index()
    print(f"  Total displacement bars: {len(all_disp)} (LONG={len(long_disp)}, SHORT={len(short_disp)})")

    # Step 2: Load microstructure delta for dates with displacement
    disp_dates = all_disp.index.strftime("%Y-%m-%d").unique().tolist()
    print(f"  Loading microstructure delta for {len(disp_dates)} dates...")
    micro_delta = load_micro_delta_m5_batch(disp_dates)
    print(f"  Micro delta loaded: {len(micro_delta)} M5 bars")

    # Step 3: Filter displacement bars where conditions align for CONTINUATION
    print(f"\nStep 2: Filtering for CONTINUATION conditions...")
    print(f"{'='*90}")

    signals = []
    # Deduplicate: max 1 signal per M30 bar (30 min window)
    last_signal_m30 = None

    for idx, row in all_disp.iterrows():
        if len(signals) >= TARGET_SIGNALS:
            break

        ts = idx
        disp_dir = row["disp_dir"]
        close = float(row["close"])
        daily_trend = row.get("daily_trend")
        liq_top = row.get("m30_liq_top")
        liq_bot = row.get("m30_liq_bot")
        atr_m30 = row.get("atr_m30", 20.0)
        box_high = row.get("m30_box_high")
        box_low = row.get("m30_box_low")

        if pd.isna(atr_m30) or pd.isna(liq_top) or pd.isna(liq_bot):
            continue

        liq_top, liq_bot = float(liq_top), float(liq_bot)

        # Condition A: daily trend must align with displacement direction
        expected_trend = "long" if disp_dir == "LONG" else "short"
        if daily_trend != expected_trend:
            continue

        # Condition B: NOT a pullback scenario
        # LONG continuation: price should be near liq_top (continuation into strength)
        # SHORT continuation: price should be near liq_bot
        if disp_dir == "LONG":
            # Near liq_top OR above box (in trend territory)
            near_liq = abs(close - liq_top) <= V1_DIST * 2  # slightly wider for M5
            above_box = box_high is not None and not pd.isna(box_high) and close > float(box_high)
            if not near_liq and not above_box:
                continue
            level_type = "liq_top"
        else:
            near_liq = abs(close - liq_bot) <= V1_DIST * 2
            below_box = box_low is not None and not pd.isna(box_low) and close < float(box_low)
            if not near_liq and not below_box:
                continue
            level_type = "liq_bot"

        # Condition C: Phase must be TREND or EXPANSION
        phase, phase_det = compute_phase_at(m30, ts, daily_trend)
        if phase not in ("TREND", "EXPANSION"):
            continue

        # Deduplicate by M30 window
        m30_window = ts.floor("30min")
        if m30_window == last_signal_m30:
            continue
        last_signal_m30 = m30_window

        # Get delta from microstructure
        delta = 0.0
        if not micro_delta.empty and ts in micro_delta.index:
            delta = float(micro_delta.loc[ts])

        # Delta check (same as production)
        delta_pass = True
        if disp_dir == "LONG":
            if abs(delta) > 0 and delta < DISP_MIN_DELTA:
                delta_pass = False
        else:
            if abs(delta) > 0 and delta > -DISP_MIN_DELTA:
                delta_pass = False

        # Exhaustion: overextension check
        exhausted = False
        exh_reason = "not exhausted"
        overext_thr = atr_m30 * OVEREXT_ATR_MULT
        if disp_dir == "LONG" and close > liq_top:
            dist = abs(close - liq_top)
            if dist > overext_thr:
                exhausted = True
                exh_reason = f"overextended LONG: {dist:.1f}pts > {overext_thr:.1f}"
        elif disp_dir == "SHORT" and close < liq_bot:
            dist = abs(liq_bot - close)
            if dist > overext_thr:
                exhausted = True
                exh_reason = f"overextended SHORT: {dist:.1f}pts > {overext_thr:.1f}"

        # V2 (L2/DOM) - simplified: check dom_imbalance direction
        # V3 (momentum) - simplified: no live 4H delta, mark as N/A
        # V4 (iceberg) - simplified: no live iceberg data, mark as N/A

        # Decision
        if not delta_pass:
            would_action = "BLOCK"
            decision_reason = f"displacement delta too weak: {delta:+.0f} (need {'+' if disp_dir=='LONG' else ''}{DISP_MIN_DELTA if disp_dir=='LONG' else -DISP_MIN_DELTA})"
        elif exhausted:
            would_action = "BLOCK"
            decision_reason = f"exhaustion: {exh_reason}"
        else:
            would_action = "GO"
            decision_reason = f"CONTINUATION {disp_dir}: range={row['range']:.1f} delta={delta:+.0f} phase={phase}"

        signal = {
            "idx": len(signals) + 1,
            "timestamp": str(ts),
            "date": ts.strftime("%Y-%m-%d"),
            "close": close,
            "phase": phase,
            "phase_bars_outside": phase_det.get("bars_outside", 0),
            "phase_ladder": phase_det.get("ladder", False),
            "daily_trend": daily_trend,
            "direction": disp_dir,
            "level_type": level_type,
            "liq_top": liq_top,
            "liq_bot": liq_bot,
            "atr_m30": atr_m30,
            "box_high": float(box_high) if box_high and not pd.isna(box_high) else None,
            "box_low": float(box_low) if box_low and not pd.isna(box_low) else None,
            "v1_zone": "PASS",
            "v2_l2": "N/A (historical)",
            "v3_momentum": "N/A (historical)",
            "v4_iceberg": "N/A (historical)",
            "displacement_range": float(row["range"]),
            "displacement_delta": delta,
            "delta_pass": delta_pass,
            "exhausted": exhausted,
            "exhaustion_reason": exh_reason,
            "would_action": would_action,
            "decision_reason": decision_reason,
        }
        signals.append(signal)

        icon = ">> GO  " if would_action == "GO" else "BLOCK  "
        print(f"  #{signal['idx']:>2} {icon} {ts}  {phase:11s}  {disp_dir}  "
              f"close={close:.1f}  rng={row['range']:.1f}  dlt={delta:+.0f}  "
              f"exh={'Y' if exhausted else 'N'}  | {decision_reason[:55]}")

    print(f"\n{'='*90}")
    print(f"Collected {len(signals)} TREND_CONTINUATION signals")

    if not signals:
        print("No signals found!")
        return

    df = pd.DataFrame(signals)

    # =====================================================================
    # SUMMARY
    # =====================================================================
    print(f"\n{'='*90}")
    print("SUMMARY")
    print(f"{'='*90}")
    go = df[df["would_action"] == "GO"]
    block = df[df["would_action"] == "BLOCK"]
    print(f"Total signals: {len(df)}")
    print(f"  would_GO:    {len(go)} ({len(go)/len(df)*100:.1f}%)")
    print(f"  would_BLOCK: {len(block)} ({len(block)/len(df)*100:.1f}%)")

    print(f"\nBy phase:")
    for phase in ["EXPANSION", "TREND"]:
        sub = df[df["phase"] == phase]
        g = len(sub[sub["would_action"] == "GO"])
        b = len(sub[sub["would_action"] == "BLOCK"])
        print(f"  {phase:11s}: {len(sub):>3} signals  (GO={g}, BLOCK={b})")

    print(f"\nBy direction:")
    for d in sorted(df["direction"].unique()):
        sub = df[df["direction"] == d]
        g = len(sub[sub["would_action"] == "GO"])
        print(f"  {d:6s}: {len(sub):>3} signals  (GO={g})")

    # =====================================================================
    # BLOCK REASONS
    # =====================================================================
    if not block.empty:
        print(f"\nBLOCK reasons:")
        delta_fail = block[block["delta_pass"] == False]
        exh_fail = block[(block["delta_pass"] == True) & (block["exhausted"] == True)]
        print(f"  Delta too weak: {len(delta_fail)}")
        print(f"  Exhaustion:     {len(exh_fail)}")

    # =====================================================================
    # DISPLACEMENT QUALITY
    # =====================================================================
    print(f"\nDisplacement quality (all signals):")
    print(f"  Range: mean={df['displacement_range'].mean():.1f}, median={df['displacement_range'].median():.1f}")
    print(f"  Delta: mean={df['displacement_delta'].mean():+.1f}, median={df['displacement_delta'].median():+.0f}")
    if not go.empty:
        print(f"\nDisplacement quality (GO only):")
        print(f"  Range: mean={go['displacement_range'].mean():.1f}, median={go['displacement_range'].median():.1f}")
        print(f"  Delta: mean={go['displacement_delta'].mean():+.1f}, median={go['displacement_delta'].median():+.0f}")

    # =====================================================================
    # QUALITATIVE: GO signals
    # =====================================================================
    print(f"\n{'='*90}")
    print(f"QUALITATIVE ANALYSIS: {len(go)} would-GO signals")
    print(f"{'='*90}")
    if not go.empty:
        likely_valid = 0
        likely_fake = 0
        late_entry = 0

        for _, r in go.iterrows():
            notes = []
            quality = "VALID"

            # Quality heuristics
            if r["phase"] == "TREND" and r["phase_ladder"]:
                notes.append("TREND+ladder")
            elif r["phase"] == "EXPANSION":
                notes.append("EXPANSION only")

            if abs(r["displacement_delta"]) >= 200:
                notes.append(f"strong delta={r['displacement_delta']:+.0f}")
            elif abs(r["displacement_delta"]) == 0:
                notes.append("no delta data")

            # Overextension proximity
            overext_thr = r["atr_m30"] * OVEREXT_ATR_MULT
            if r["direction"] == "LONG" and r["liq_top"]:
                dist_to_overext = overext_thr - abs(r["close"] - r["liq_top"])
                if dist_to_overext < r["atr_m30"] * 0.3:
                    notes.append("NEAR overextension")
                    quality = "LATE"
                    late_entry += 1

            if quality == "VALID":
                likely_valid += 1
            else:
                likely_fake += 1

            print(f"  #{r['idx']:>2} {r['timestamp']}  {r['phase']:11s}  {r['direction']}  "
                  f"close={r['close']:.1f}  rng={r['displacement_range']:.1f}  "
                  f"dlt={r['displacement_delta']:+.0f}  [{' | '.join(notes)}]")

        print(f"\n  Assessment:")
        print(f"    Likely valid continuations: {likely_valid}")
        print(f"    Likely late/risky entries:  {late_entry}")
        print(f"    Total GO with concerns:     {likely_fake}")

    # =====================================================================
    # KEY OBSERVATIONS
    # =====================================================================
    print(f"\n{'='*90}")
    print("KEY OBSERVATIONS")
    print(f"{'='*90}")

    go_pct = len(go) / len(df) * 100 if len(df) > 0 else 0
    print(f"\n  1. GO rate: {go_pct:.1f}% ({len(go)}/{len(df)})")

    exp_count = len(df[df["phase"] == "EXPANSION"])
    trend_count = len(df[df["phase"] == "TREND"])
    print(f"  2. Phase bias: EXPANSION={exp_count}, TREND={trend_count}")
    if exp_count > trend_count * 3:
        print(f"     >> EXPANSION dominant - signals mostly before TREND confirmed")

    block_pct = len(block) / len(df) * 100 if len(df) > 0 else 0
    print(f"  3. Block rate: {block_pct:.1f}%")

    # Date distribution
    dates_with_signals = df["date"].nunique()
    print(f"  4. Spread: {dates_with_signals} unique dates with signals")

    # Direction balance
    long_go = len(go[go["direction"] == "LONG"]) if not go.empty else 0
    short_go = len(go[go["direction"] == "SHORT"]) if not go.empty else 0
    print(f"  5. GO direction: LONG={long_go}, SHORT={short_go}")

    # Exhaustion filter effectiveness
    exh_count = len(df[df["exhausted"] == True])
    print(f"  6. Exhaustion filter caught: {exh_count} signals")

    # Save
    out_path = Path(r"C:\FluxQuantumAI\data\calibration")
    out_path.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path / "shadow_trend_continuation_v2.parquet")
    df.to_csv(out_path / "shadow_trend_continuation_v2.csv", index=False)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

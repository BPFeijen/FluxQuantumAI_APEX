"""
SHADOW MODE v3: TREND_CONTINUATION - Production-faithful simulation
===================================================================
Matches EXACT production behavior of _detect_trend_displacement:
  - Reads M5 OHLCV (no bar_delta in M5 parquet -> delta=0 -> delta check SKIPPED)
  - Displacement = range >= 0.8*ATR_M30 + bullish/bearish + close near extreme
  - NO delta filtering (same as production)

Also reports: what WOULD change if delta were available (from microstructure).

Collects 40 signals then stops.
"""

import pandas as pd
import numpy as np
from pathlib import Path

M30_BOXES   = Path(r"C:\data\processed\gc_m30_boxes.parquet")
M5_BOXES    = Path(r"C:\data\processed\gc_m5_boxes.parquet")
FEATURES_V4 = Path(r"C:\data\processed\gc_ats_features_v4.parquet")
MICRO_DIR   = Path(r"C:\data\level2\_gc_xcec")

START_DATE = "2025-07-01"
TREND_ACCEPTANCE_MIN_BARS = 4
TARGET_SIGNALS = 40

DISP_ATR_MULT = 0.8
DISP_CLOSE_PCT = 0.7
OVEREXT_ATR_MULT = 1.5
V1_DIST = 8.0


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

    # Propagate M30 fields to M5
    for col in ["atr14", "m30_liq_top", "m30_liq_bot", "m30_box_high", "m30_box_low",
                 "m30_box_confirmed", "m30_box_id", "m30_fmv"]:
        m5[col if col != "atr14" else "atr_m30"] = m30[col].reindex(m5.index, method="ffill")

    m5_trend = feat["daily_jac_dir"].resample("5min").ffill().map({"up": "long", "down": "short"})
    m5["daily_trend"] = m5_trend.reindex(m5.index, method="ffill")

    print(f"M30: {len(m30)} bars | M5: {len(m5)} bars")
    return m30, m5


def load_micro_delta_batch(dates):
    all_delta = []
    for d in dates:
        path = MICRO_DIR / f"microstructure_{d}.csv.gz"
        if not path.exists():
            continue
        try:
            micro = pd.read_csv(path, compression="gzip", usecols=["timestamp", "bar_delta"])
            micro["timestamp"] = pd.to_datetime(micro["timestamp"], utc=True)
            micro = micro.set_index("timestamp").sort_index()
            all_delta.append(micro["bar_delta"].resample("5min").sum())
        except Exception:
            continue
    return pd.concat(all_delta).sort_index() if all_delta else pd.Series(dtype=float)


def detect_box_ladder(m30, ts, trend_dir, min_boxes=3):
    subset = m30[m30.index <= ts]
    confirmed = subset[subset["m30_box_confirmed"] == True]
    if confirmed.empty:
        return False
    boxes = confirmed.groupby("m30_box_id")["m30_fmv"].first().sort_index()
    if len(boxes) < min_boxes:
        return False
    last_n = boxes.iloc[-min_boxes:].values
    if trend_dir == "long":
        return all(last_n[i] > last_n[i-1] for i in range(1, len(last_n)))
    elif trend_dir == "short":
        return all(last_n[i] < last_n[i-1] for i in range(1, len(last_n)))
    return False


def count_bars_outside(m30, ts, box_high, box_low, lookback=10):
    subset = m30[m30.index < ts].tail(lookback)
    count = 0
    for i in range(len(subset) - 1, -1, -1):
        c = float(subset.iloc[i]["close"])
        if c <= 0 or box_low <= c <= box_high:
            break
        count += 1
    return count


def compute_phase(m30, ts, daily_trend):
    before = m30[m30.index <= ts]
    if before.empty:
        return "UNKNOWN", {}
    row = before.iloc[-1]
    close = float(row["close"])
    bh, bl = row.get("m30_box_high"), row.get("m30_box_low")
    confirmed = bool(row.get("m30_box_confirmed", False))
    if pd.isna(bh) or pd.isna(bl) or close <= 0:
        return "UNKNOWN", {}
    bh, bl = float(bh), float(bl)
    if bl <= close <= bh:
        return "CONTRACTION", {}
    bars_out = count_bars_outside(m30, ts, bh, bl)
    if confirmed and daily_trend in ("long", "short"):
        ladder = detect_box_ladder(m30, ts, daily_trend)
        if ladder and bars_out >= TREND_ACCEPTANCE_MIN_BARS:
            return "TREND", {"bars_outside": bars_out, "ladder": True}
        elif ladder:
            return "EXPANSION", {"bars_outside": bars_out, "ladder": True}
        return "EXPANSION", {"bars_outside": bars_out, "ladder": False}
    return "EXPANSION", {"bars_outside": bars_out}


def main():
    print("SHADOW MODE v3: TREND_CONTINUATION (production-faithful)")
    print(f"Target: {TARGET_SIGNALS} signals | Period: {START_DATE} to today")
    print(f"Delta check: SKIPPED (matches production - M5 parquet has no bar_delta)")
    print()

    m30, m5 = load_data()

    # Find M5 displacement bars (production behavior: no delta filter)
    print("\nFinding M5 displacement bars (range + direction + close position only)...")
    m5["min_range"] = m5["atr_m30"] * DISP_ATR_MULT
    m5["close_pct_long"] = (m5["close"] - m5["low"]) / m5["range"].replace(0, np.nan)
    m5["close_pct_short"] = (m5["high"] - m5["close"]) / m5["range"].replace(0, np.nan)

    long_disp = m5[
        (m5["close"] > m5["open"]) &
        (m5["range"] >= m5["min_range"]) &
        (m5["close_pct_long"] >= DISP_CLOSE_PCT)
    ].copy()
    long_disp["disp_dir"] = "LONG"

    short_disp = m5[
        (m5["close"] < m5["open"]) &
        (m5["range"] >= m5["min_range"]) &
        (m5["close_pct_short"] >= DISP_CLOSE_PCT)
    ].copy()
    short_disp["disp_dir"] = "SHORT"

    all_disp = pd.concat([long_disp, short_disp]).sort_index()
    print(f"  Displacement bars: {len(all_disp)} (LONG={len(long_disp)}, SHORT={len(short_disp)})")

    # Load micro delta for info column (not used in decision)
    disp_dates = all_disp.index.strftime("%Y-%m-%d").unique().tolist()
    print(f"  Loading microstructure delta for context ({len(disp_dates)} dates)...")
    micro_delta = load_micro_delta_batch(disp_dates)

    # Scan for CONTINUATION signals
    print(f"\nScanning for CONTINUATION conditions...")
    print(f"{'='*100}")

    signals = []
    last_m30_window = None

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
        bh = row.get("m30_box_high")
        bl = row.get("m30_box_low")

        if pd.isna(atr_m30) or pd.isna(liq_top) or pd.isna(liq_bot):
            continue
        liq_top, liq_bot = float(liq_top), float(liq_bot)

        # A: daily trend aligned
        expected = "long" if disp_dir == "LONG" else "short"
        if daily_trend != expected:
            continue

        # B: NOT pullback (continuation = at the liq zone WITH trend, not against)
        if disp_dir == "LONG":
            near_liq = abs(close - liq_top) <= V1_DIST * 2
            above_box = bh is not None and not pd.isna(bh) and close > float(bh)
            if not near_liq and not above_box:
                continue
            level_type = "liq_top"
        else:
            near_liq = abs(close - liq_bot) <= V1_DIST * 2
            below_box = bl is not None and not pd.isna(bl) and close < float(bl)
            if not near_liq and not below_box:
                continue
            level_type = "liq_bot"

        # C: Phase = TREND or EXPANSION
        phase, phase_det = compute_phase(m30, ts, daily_trend)
        if phase not in ("TREND", "EXPANSION"):
            continue

        # Deduplicate by M30 window
        m30_w = ts.floor("30min")
        if m30_w == last_m30_window:
            continue
        last_m30_window = m30_w

        # Exhaustion: overextension
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

        # Micro delta (for context only)
        micro_dlt = 0.0
        if not micro_delta.empty and ts in micro_delta.index:
            micro_dlt = float(micro_delta.loc[ts])
        # Would delta have blocked?
        delta_would_block = False
        if disp_dir == "LONG" and abs(micro_dlt) > 0 and micro_dlt < 80:
            delta_would_block = True
        elif disp_dir == "SHORT" and abs(micro_dlt) > 0 and micro_dlt > -80:
            delta_would_block = True

        # Decision (production: no delta filter)
        if exhausted:
            would_action = "BLOCK"
            decision_reason = f"exhaustion: {exh_reason}"
        else:
            would_action = "GO"
            decision_reason = f"CONTINUATION {disp_dir}: range={row['range']:.1f} phase={phase}"

        signal = {
            "idx": len(signals) + 1,
            "timestamp": str(ts),
            "date": ts.strftime("%Y-%m-%d"),
            "close": close,
            "phase": phase,
            "bars_outside": phase_det.get("bars_outside", 0),
            "ladder": phase_det.get("ladder", False),
            "daily_trend": daily_trend,
            "direction": disp_dir,
            "level_type": level_type,
            "liq_top": liq_top,
            "liq_bot": liq_bot,
            "atr_m30": atr_m30,
            "v1_zone": "PASS",
            "v2_l2": "N/A",
            "v3_momentum": "N/A",
            "v4_iceberg": "N/A",
            "displacement_range": float(row["range"]),
            "micro_delta": micro_dlt,
            "delta_would_block": delta_would_block,
            "exhausted": exhausted,
            "exhaustion_reason": exh_reason,
            "would_action": would_action,
            "decision_reason": decision_reason,
        }
        signals.append(signal)

        icon = ">> GO  " if would_action == "GO" else "BLOCK  "
        dlt_warn = " [delta_weak]" if delta_would_block and would_action == "GO" else ""
        print(f"  #{signal['idx']:>2} {icon} {ts}  {phase:11s}  {disp_dir}  "
              f"close={close:.1f}  rng={row['range']:.1f}  micro_dlt={micro_dlt:+.0f}  "
              f"exh={'Y' if exhausted else 'N'}{dlt_warn}")

    print(f"\n{'='*100}")
    df = pd.DataFrame(signals)
    print(f"Collected {len(df)} TREND_CONTINUATION signals\n")

    if df.empty:
        print("No signals found!")
        return

    go = df[df["would_action"] == "GO"]
    block = df[df["would_action"] == "BLOCK"]

    # =================================================================
    # SUMMARY
    # =================================================================
    print(f"{'='*100}")
    print("SUMMARY")
    print(f"{'='*100}")
    print(f"Total signals:     {len(df)}")
    print(f"  would_GO:        {len(go)} ({len(go)/len(df)*100:.1f}%)")
    print(f"  would_BLOCK:     {len(block)} ({len(block)/len(df)*100:.1f}%)")

    print(f"\nDistribution by phase:")
    for phase in ["EXPANSION", "TREND"]:
        sub = df[df["phase"] == phase]
        g = len(sub[sub["would_action"] == "GO"])
        b = len(sub[sub["would_action"] == "BLOCK"])
        print(f"  {phase:11s}: {len(sub):>3} total  (GO={g}, BLOCK={b})")

    print(f"\nDistribution by direction:")
    for d in sorted(df["direction"].unique()):
        sub = df[df["direction"] == d]
        g = len(sub[sub["would_action"] == "GO"])
        print(f"  {d:6s}: {len(sub):>3} total  (GO={g})")

    # =================================================================
    # BLOCK ANALYSIS
    # =================================================================
    if not block.empty:
        print(f"\nBLOCK analysis ({len(block)} signals):")
        exh_count = len(block[block["exhausted"] == True])
        print(f"  Exhaustion (overextension): {exh_count}")

    # =================================================================
    # DELTA GAP ANALYSIS
    # =================================================================
    print(f"\n{'='*100}")
    print("DELTA GAP: Production vs With-Delta behavior")
    print(f"{'='*100}")
    go_delta_would_block = go[go["delta_would_block"] == True] if not go.empty else pd.DataFrame()
    print(f"  GO signals in production (no delta):      {len(go)}")
    print(f"  Of those, delta WOULD have blocked:        {len(go_delta_would_block)}")
    print(f"  GO signals if delta were active:            {len(go) - len(go_delta_would_block)}")
    print(f"\n  >> This is a data gap: M5 parquet has no bar_delta column")
    print(f"  >> In production, displacement delta check is ALWAYS BYPASSED")
    print(f"  >> Impact: some GO signals may have weak institutional support")

    # =================================================================
    # QUALITATIVE
    # =================================================================
    print(f"\n{'='*100}")
    print(f"QUALITATIVE ANALYSIS: {len(go)} would-GO signals")
    print(f"{'='*100}")

    likely_valid = 0
    likely_fake = 0
    late_entry = 0
    weak_delta_concern = 0

    if not go.empty:
        for _, r in go.iterrows():
            notes = []

            if r["phase"] == "TREND" and r["ladder"]:
                notes.append("TREND+ladder")
            elif r["phase"] == "EXPANSION":
                notes.append("EXPANSION")

            if r["delta_would_block"]:
                notes.append(f"WEAK DELTA={r['micro_delta']:+.0f}")
                weak_delta_concern += 1

            # Overextension proximity
            overext_thr = r["atr_m30"] * OVEREXT_ATR_MULT
            if r["direction"] == "LONG" and r["liq_top"]:
                dist = abs(r["close"] - r["liq_top"])
                margin = overext_thr - dist
                if margin < r["atr_m30"] * 0.3:
                    notes.append("NEAR overext")
                    late_entry += 1
            elif r["direction"] == "SHORT" and r["liq_bot"]:
                dist = abs(r["liq_bot"] - r["close"])
                margin = overext_thr - dist
                if margin < r["atr_m30"] * 0.3:
                    notes.append("NEAR overext")
                    late_entry += 1

            if not notes or (len(notes) == 1 and "TREND+ladder" in notes[0]):
                likely_valid += 1
                quality = "VALID"
            else:
                likely_fake += 1
                quality = "CONCERN"

            print(f"  #{r['idx']:>2} {r['timestamp']}  {r['phase']:11s}  {r['direction']}  "
                  f"close={r['close']:.1f}  rng={r['displacement_range']:.1f}  "
                  f"dlt={r['micro_delta']:+.0f}  [{quality}: {' | '.join(notes) if notes else 'clean'}]")

    print(f"\n  Assessment:")
    print(f"    Likely valid:          {likely_valid}")
    print(f"    Late/risky:            {late_entry}")
    print(f"    Weak delta concern:    {weak_delta_concern}")
    print(f"    Total with concerns:   {likely_fake}")

    # =================================================================
    # KEY OBSERVATIONS
    # =================================================================
    print(f"\n{'='*100}")
    print("KEY OBSERVATIONS")
    print(f"{'='*100}")

    go_pct = len(go)/len(df)*100 if len(df) > 0 else 0
    print(f"\n  1. GO rate (production): {go_pct:.1f}% ({len(go)}/{len(df)})")

    exp_c = len(df[df['phase']=='EXPANSION'])
    trend_c = len(df[df['phase']=='TREND'])
    print(f"  2. Phase bias: EXPANSION={exp_c}, TREND={trend_c}")

    print(f"  3. Exhaustion filter: caught {len(block)} of {len(df)} candidates")

    dates_spread = df["date"].nunique()
    print(f"  4. Date spread: {dates_spread} unique dates")

    long_go = len(go[go['direction']=='LONG']) if not go.empty else 0
    short_go = len(go[go['direction']=='SHORT']) if not go.empty else 0
    print(f"  5. GO direction: LONG={long_go}, SHORT={short_go}")

    delta_gap_pct = len(go_delta_would_block)/len(go)*100 if len(go) > 0 else 0
    print(f"  6. DELTA GAP: {delta_gap_pct:.0f}% of GO signals have weak micro delta")
    print(f"     >> CRITICAL: M5 parquet missing bar_delta = delta check dead in production")

    # Save
    out = Path(r"C:\FluxQuantumAI\data\calibration")
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "shadow_trend_continuation_v3.parquet")
    df.to_csv(out / "shadow_trend_continuation_v3.csv", index=False)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

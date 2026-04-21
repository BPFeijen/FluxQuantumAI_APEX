"""
SHADOW MODE v4: TREND_CONTINUATION with FIXED delta pipeline
=============================================================
Now that bar_delta is in the M5 parquet (m5_updater fix), the displacement
delta check is ACTIVE. For historical simulation, we backfill bar_delta
from microstructure files (same source the live M5 updater uses).

This matches production behavior after the fix:
  - M5 bars have signed bar_delta from microstructure
  - _detect_trend_displacement reads bar_delta from M5 parquet
  - Delta check: abs(delta) > 0 and delta < min_delta -> skip

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
DISP_MIN_DELTA = 80
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

    # Daily trend
    feat = pd.read_parquet(FEATURES_V4, columns=["daily_jac_dir"])
    if feat.index.tz is None:
        feat.index = feat.index.tz_localize("UTC")
    feat = feat[feat.index >= START_DATE]
    m30_trend = feat["daily_jac_dir"].resample("30min").ffill().map({"up": "long", "down": "short"})
    m30["daily_trend"] = m30_trend.reindex(m30.index, method="ffill")

    # Propagate M30 to M5
    m5_trend = feat["daily_jac_dir"].resample("5min").ffill().map({"up": "long", "down": "short"})
    m5["daily_trend"] = m5_trend.reindex(m5.index, method="ffill")
    for col in ["atr14", "m30_liq_top", "m30_liq_bot", "m30_box_high", "m30_box_low",
                 "m30_box_confirmed", "m30_box_id", "m30_fmv"]:
        target = col if col != "atr14" else "atr_m30"
        m5[target] = m30[col].reindex(m5.index, method="ffill")

    print(f"M30: {len(m30)} | M5: {len(m5)}")
    print(f"M5 bar_delta present: {'bar_delta' in m5.columns}")
    print(f"M5 bar_delta non-zero: {(m5['bar_delta'] != 0).sum()}/{len(m5)}")
    return m30, m5


def backfill_m5_delta(m5):
    """
    Backfill bar_delta from microstructure for historical M5 bars where delta=0.
    This simulates what production will have (M5 updater reads microstructure daily).
    """
    needs_delta = m5[m5["bar_delta"] == 0].copy()
    if needs_delta.empty:
        return m5

    dates = needs_delta.index.strftime("%Y-%m-%d").unique()
    print(f"Backfilling bar_delta from microstructure for {len(dates)} dates...")

    filled = 0
    for d in dates:
        path = MICRO_DIR / f"microstructure_{d}.csv.gz"
        if not path.exists():
            continue
        try:
            micro = pd.read_csv(path, compression="gzip", usecols=["timestamp", "bar_delta"])
            micro["timestamp"] = pd.to_datetime(micro["timestamp"], utc=True)
            micro = micro.set_index("timestamp").sort_index()
            m5_delta = micro["bar_delta"].resample("5min").sum()

            # Update m5 bar_delta for matching timestamps
            common = m5.index.intersection(m5_delta.index)
            mask = common[m5.loc[common, "bar_delta"] == 0]
            if len(mask) > 0:
                m5.loc[mask, "bar_delta"] = m5_delta.loc[mask]
                filled += len(mask)
        except Exception:
            continue

    non_zero = (m5["bar_delta"] != 0).sum()
    print(f"  Backfilled {filled} bars. Total non-zero: {non_zero}/{len(m5)} ({non_zero/len(m5)*100:.1f}%)")
    return m5


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


def count_bars_outside(m30, ts, bh, bl, lookback=10):
    subset = m30[m30.index < ts].tail(lookback)
    count = 0
    for i in range(len(subset) - 1, -1, -1):
        c = float(subset.iloc[i]["close"])
        if c <= 0 or bl <= c <= bh:
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


def check_displacement_with_delta(m5, ts, direction, atr_m30):
    """
    Replicates _detect_trend_displacement with REAL bar_delta.
    Last 3 completed M5 bars before ts.
    """
    min_range = atr_m30 * DISP_ATR_MULT
    recent = m5[m5.index < ts].tail(3)
    if len(recent) == 0:
        return False, "no M5 bars", {}, False

    for i in range(len(recent) - 1, -1, -1):
        bar = recent.iloc[i]
        o, c, h, lo = float(bar["open"]), float(bar["close"]), float(bar["high"]), float(bar["low"])
        bar_range = h - lo
        delta = float(bar.get("bar_delta", 0))

        if bar_range < min_range:
            continue

        if direction == "LONG":
            if c <= o:
                continue
            # Delta check (now active with real data)
            if abs(delta) > 0 and delta < DISP_MIN_DELTA:
                # Bar has range but weak delta — FAIL
                return False, f"delta too weak: {delta:+.0f} < +{DISP_MIN_DELTA}", {
                    "range": bar_range, "delta": delta, "bar_ts": str(recent.index[i])
                }, True  # delta_was_checked=True
            if bar_range > 0 and (c - lo) / bar_range < DISP_CLOSE_PCT:
                continue
            close_pct = (c - lo) / bar_range if bar_range > 0 else 0
            return True, f"displacement LONG: rng={bar_range:.1f}>{min_range:.1f} dlt={delta:+.0f} cpct={close_pct:.2f}", {
                "range": bar_range, "delta": delta, "close_pct": close_pct, "bar_ts": str(recent.index[i])
            }, True

        else:  # SHORT
            if c >= o:
                continue
            if abs(delta) > 0 and delta > -DISP_MIN_DELTA:
                return False, f"delta too weak: {delta:+.0f} > -{DISP_MIN_DELTA}", {
                    "range": bar_range, "delta": delta, "bar_ts": str(recent.index[i])
                }, True
            if bar_range > 0 and (h - c) / bar_range < DISP_CLOSE_PCT:
                continue
            close_pct = (h - c) / bar_range if bar_range > 0 else 0
            return True, f"displacement SHORT: rng={bar_range:.1f}>{min_range:.1f} dlt={delta:+.0f} cpct={close_pct:.2f}", {
                "range": bar_range, "delta": delta, "close_pct": close_pct, "bar_ts": str(recent.index[i])
            }, True

    return False, "no valid displacement in last 3 M5", {}, False


def main():
    print("SHADOW MODE v4: TREND_CONTINUATION (delta pipeline FIXED)")
    print(f"Target: {TARGET_SIGNALS} signals | Period: {START_DATE} to today")
    print(f"Delta check: ACTIVE (bar_delta in M5 parquet)")
    print()

    m30, m5 = load_data()
    m5 = backfill_m5_delta(m5)

    # Pre-filter M5 displacement candidates (range + direction + close position)
    print("\nFinding M5 displacement candidates...")
    m5["min_range"] = m5["atr_m30"] * DISP_ATR_MULT
    m5["cpct_long"] = (m5["close"] - m5["low"]) / m5["range"].replace(0, np.nan)
    m5["cpct_short"] = (m5["high"] - m5["close"]) / m5["range"].replace(0, np.nan)

    long_d = m5[(m5["close"] > m5["open"]) & (m5["range"] >= m5["min_range"]) & (m5["cpct_long"] >= DISP_CLOSE_PCT)].copy()
    long_d["disp_dir"] = "LONG"
    short_d = m5[(m5["close"] < m5["open"]) & (m5["range"] >= m5["min_range"]) & (m5["cpct_short"] >= DISP_CLOSE_PCT)].copy()
    short_d["disp_dir"] = "SHORT"
    all_disp = pd.concat([long_d, short_d]).sort_index()

    # Further filter: only those with strong enough delta
    strong_long = all_disp[(all_disp["disp_dir"] == "LONG") &
                           ((all_disp["bar_delta"] >= DISP_MIN_DELTA) | (all_disp["bar_delta"] == 0))]
    strong_short = all_disp[(all_disp["disp_dir"] == "SHORT") &
                            ((all_disp["bar_delta"] <= -DISP_MIN_DELTA) | (all_disp["bar_delta"] == 0))]
    strong_disp = pd.concat([strong_long, strong_short]).sort_index()

    print(f"  All displacement bars: {len(all_disp)} (LONG={len(long_d)}, SHORT={len(short_d)})")
    print(f"  With strong delta (>={DISP_MIN_DELTA}): {len(strong_disp)}")
    print(f"  Delta filtered out: {len(all_disp) - len(strong_disp)}")

    # Scan strong displacement bars for CONTINUATION
    print(f"\n{'='*100}")
    signals = []
    last_m30_w = None

    for idx, row in strong_disp.iterrows():
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

        expected = "long" if disp_dir == "LONG" else "short"
        if daily_trend != expected:
            continue

        # Not pullback
        if disp_dir == "LONG":
            near_liq = abs(close - liq_top) <= V1_DIST * 2
            above_box = bh is not None and not pd.isna(bh) and close > float(bh)
            if not near_liq and not above_box:
                continue
            level = "liq_top"
        else:
            near_liq = abs(close - liq_bot) <= V1_DIST * 2
            below_box = bl is not None and not pd.isna(bl) and close < float(bl)
            if not near_liq and not below_box:
                continue
            level = "liq_bot"

        phase, phase_det = compute_phase(m30, ts, daily_trend)
        if phase not in ("TREND", "EXPANSION"):
            continue

        m30_w = ts.floor("30min")
        if m30_w == last_m30_w:
            continue
        last_m30_w = m30_w

        delta = float(row.get("bar_delta", 0))

        # Exhaustion
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

        if exhausted:
            would_action = "BLOCK"
            reason = f"exhaustion: {exh_reason}"
        else:
            would_action = "GO"
            reason = f"CONTINUATION {disp_dir}: rng={row['range']:.1f} dlt={delta:+.0f} phase={phase}"

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
            "level_type": level,
            "liq_top": liq_top,
            "liq_bot": liq_bot,
            "atr_m30": atr_m30,
            "v1_zone": "PASS",
            "v2_l2": "N/A",
            "v3_momentum": "N/A",
            "v4_iceberg": "N/A",
            "displacement_range": float(row["range"]),
            "displacement_delta": delta,
            "exhausted": exhausted,
            "exhaustion_reason": exh_reason,
            "would_action": would_action,
            "decision_reason": reason,
        }
        signals.append(signal)

        icon = ">> GO  " if would_action == "GO" else "BLOCK  "
        print(f"  #{signal['idx']:>2} {icon} {ts}  {phase:11s}  {disp_dir}  "
              f"close={close:.1f}  rng={row['range']:.1f}  dlt={delta:+.0f}  exh={'Y' if exhausted else 'N'}")

    print(f"\n{'='*100}")
    df = pd.DataFrame(signals)
    total = len(df)
    print(f"Collected {total} TREND_CONTINUATION signals\n")

    if df.empty:
        print("No signals found!")
        return

    go = df[df["would_action"] == "GO"]
    block = df[df["would_action"] == "BLOCK"]

    # SUMMARY
    print(f"{'='*100}")
    print("SUMMARY")
    print(f"{'='*100}")
    print(f"Total:         {total}")
    print(f"  would_GO:    {len(go)} ({len(go)/total*100:.1f}%)")
    print(f"  would_BLOCK: {len(block)} ({len(block)/total*100:.1f}%)")

    print(f"\nBy phase:")
    for p in ["EXPANSION", "TREND"]:
        sub = df[df["phase"] == p]
        g = len(sub[sub["would_action"] == "GO"])
        print(f"  {p:11s}: {len(sub):>3} (GO={g}, BLOCK={len(sub)-g})")

    print(f"\nBy direction:")
    for d in sorted(df["direction"].unique()):
        sub = df[df["direction"] == d]
        g = len(sub[sub["would_action"] == "GO"])
        print(f"  {d:6s}: {len(sub):>3} (GO={g})")

    # BLOCK analysis
    if not block.empty:
        print(f"\nBLOCK reasons:")
        exh_c = len(block[block["exhausted"] == True])
        print(f"  Exhaustion: {exh_c}")

    # Displacement quality
    print(f"\nDisplacement quality:")
    print(f"  All:  range mean={df['displacement_range'].mean():.1f}  delta mean={df['displacement_delta'].mean():+.1f}")
    if not go.empty:
        print(f"  GO:   range mean={go['displacement_range'].mean():.1f}  delta mean={go['displacement_delta'].mean():+.1f}")

    # QUALITATIVE
    print(f"\n{'='*100}")
    print(f"QUALITATIVE: {len(go)} would-GO signals")
    print(f"{'='*100}")
    valid = 0
    concern = 0
    if not go.empty:
        for _, r in go.iterrows():
            notes = []
            if r["phase"] == "TREND" and r["ladder"]:
                notes.append("TREND+ladder")
            elif r["phase"] == "EXPANSION":
                notes.append("EXPANSION")

            overext_thr = r["atr_m30"] * OVEREXT_ATR_MULT
            if r["direction"] == "LONG" and r["liq_top"]:
                dist = abs(r["close"] - r["liq_top"])
                margin = overext_thr - dist
                if margin < r["atr_m30"] * 0.3:
                    notes.append("NEAR overext")

            if abs(r["displacement_delta"]) >= 200:
                notes.append(f"STRONG delta={r['displacement_delta']:+.0f}")
            elif abs(r["displacement_delta"]) >= 100:
                notes.append(f"solid delta={r['displacement_delta']:+.0f}")

            quality = "VALID" if not any(x in str(notes) for x in ["NEAR overext"]) else "CONCERN"
            if quality == "VALID":
                valid += 1
            else:
                concern += 1

            print(f"  #{r['idx']:>2} {r['timestamp']}  {r['phase']:11s}  {r['direction']}  "
                  f"close={r['close']:.1f}  rng={r['displacement_range']:.1f}  "
                  f"dlt={r['displacement_delta']:+.0f}  [{quality}: {' | '.join(notes) if notes else 'clean'}]")

    print(f"\n  Likely valid: {valid}")
    print(f"  Concerns:     {concern}")

    # KEY OBSERVATIONS
    print(f"\n{'='*100}")
    print("KEY OBSERVATIONS")
    print(f"{'='*100}")
    print(f"  1. GO rate: {len(go)/total*100:.1f}% ({len(go)}/{total})")
    exp_c = len(df[df['phase']=='EXPANSION'])
    trend_c = len(df[df['phase']=='TREND'])
    print(f"  2. Phase: EXPANSION={exp_c}, TREND={trend_c}")
    print(f"  3. Exhaustion caught: {len(block)}/{total}")
    print(f"  4. Date spread: {df['date'].nunique()} unique dates")
    long_go = len(go[go['direction']=='LONG']) if not go.empty else 0
    short_go = len(go[go['direction']=='SHORT']) if not go.empty else 0
    print(f"  5. GO direction: LONG={long_go}, SHORT={short_go}")

    # Compare with v3 (no delta)
    print(f"\n  COMPARISON vs v3 (no delta):")
    print(f"    v3: 27 GO / 40 total (67.5%)")
    print(f"    v4: {len(go)} GO / {total} total ({len(go)/total*100:.1f}%)")
    print(f"    Delta filter impact: blocked {27 - len(go)} additional false signals" if len(go) < 27 else
          f"    Delta filter: different candidate set (displacement-first with delta)")

    # Save
    out = Path(r"C:\FluxQuantumAI\data\calibration")
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "shadow_trend_continuation_v4.parquet")
    df.to_csv(out / "shadow_trend_continuation_v4.csv", index=False)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

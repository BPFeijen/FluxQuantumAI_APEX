"""
SHADOW MODE: TREND_CONTINUATION Behavioral Validation
=====================================================
Simulates TREND_CONTINUATION signals over Jul 2025 - today.
Collects 30-40 signals then stops.

Replicates exact logic from event_processor.py:
  1. Phase = TREND or EXPANSION (with daily_trend)
  2. Level = NOT pullback (liq_top in uptrend or liq_bot in downtrend)
  3. Displacement check (M5 bars: range > 0.8*ATR, bullish/bearish close, delta)
  4. Exhaustion check (overextension only - no live decision object)

Data sources:
  - M30 boxes: phase detection, ATR, liq levels
  - M5 boxes: OHLCV for displacement (no delta in M5 parquet)
  - Microstructure 1s: bar_delta resampled to M5 for displacement delta check
  - Features V4: daily_jac_dir for daily_trend
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict
import glob
import sys

M30_BOXES   = Path(r"C:\data\processed\gc_m30_boxes.parquet")
M5_BOXES    = Path(r"C:\data\processed\gc_m5_boxes.parquet")
FEATURES_V4 = Path(r"C:\data\processed\gc_ats_features_v4.parquet")
MICRO_DIR   = Path(r"C:\data\level2\_gc_xcec")

START_DATE = "2025-07-01"
TREND_ACCEPTANCE_MIN_BARS = 4
TARGET_SIGNALS = 40

# Displacement thresholds (from settings/event_processor)
DISP_ATR_MULT = 0.8
DISP_MIN_DELTA = 80
DISP_CLOSE_PCT = 0.7

# Exhaustion: overextension
OVEREXT_ATR_MULT = 1.5


def load_m30():
    df = pd.read_parquet(M30_BOXES)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[df.index >= START_DATE].copy()
    print(f"M30: {len(df)} bars, {df.index.min()} to {df.index.max()}")
    return df


def load_m5():
    df = pd.read_parquet(M5_BOXES)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[df.index >= START_DATE].copy()
    print(f"M5: {len(df)} bars, {df.index.min()} to {df.index.max()}")
    return df


def load_daily_trend():
    feat = pd.read_parquet(FEATURES_V4, columns=["daily_jac_dir"])
    if feat.index.tz is None:
        feat.index = feat.index.tz_localize("UTC")
    feat = feat[feat.index >= START_DATE]
    m30_trend = feat["daily_jac_dir"].resample("30min").ffill()
    m30_trend = m30_trend.map({"up": "long", "down": "short"})
    return m30_trend


def load_micro_delta_m5(date_str):
    """Load microstructure for a date and resample bar_delta to M5."""
    path = MICRO_DIR / f"microstructure_{date_str}.csv.gz"
    if not path.exists():
        return None
    try:
        micro = pd.read_csv(path, compression="gzip", usecols=["timestamp", "bar_delta"])
        micro["timestamp"] = pd.to_datetime(micro["timestamp"], utc=True)
        micro = micro.set_index("timestamp").sort_index()
        # Resample to M5: sum of bar_delta
        m5_delta = micro["bar_delta"].resample("5min").sum()
        return m5_delta
    except Exception as e:
        return None


def detect_box_ladder_at(m30, idx, trend_direction, min_boxes=3):
    subset = m30.iloc[:idx+1]
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


def count_bars_outside(m30, idx, box_high, box_low, lookback=10):
    start = max(0, idx - lookback)
    recent = m30.iloc[start:idx]
    count = 0
    for i in range(len(recent) - 1, -1, -1):
        close = float(recent.iloc[i]["close"])
        if close <= 0 or box_low <= close <= box_high:
            break
        count += 1
    return count


def compute_phase(m30, idx):
    row = m30.iloc[idx]
    close = float(row["close"])
    box_high = row.get("m30_box_high")
    box_low = row.get("m30_box_low")
    confirmed = bool(row.get("m30_box_confirmed", False))
    daily_trend = row.get("daily_trend", None)

    if pd.isna(box_high) or pd.isna(box_low) or close <= 0:
        return "UNKNOWN", {}

    box_high, box_low = float(box_high), float(box_low)

    if box_low <= close <= box_high:
        return "CONTRACTION", {}

    bars_out = count_bars_outside(m30, idx, box_high, box_low)

    if confirmed and daily_trend in ("long", "short"):
        has_ladder = detect_box_ladder_at(m30, idx, daily_trend)
        if has_ladder and bars_out >= TREND_ACCEPTANCE_MIN_BARS:
            return "TREND", {"bars_outside": bars_out, "ladder": True}
        elif has_ladder:
            return "EXPANSION", {"bars_outside": bars_out, "ladder": True, "blocked_t4": True}
        else:
            return "EXPANSION", {"bars_outside": bars_out, "ladder": False}

    return "EXPANSION", {"bars_outside": bars_out, "ladder": False}


def check_displacement(m5_bars, m5_delta, ts, direction, atr_m30):
    """
    Check displacement in last 3 completed M5 bars before ts.
    Replicates _detect_trend_displacement exactly.
    """
    min_range = atr_m30 * DISP_ATR_MULT

    # Get M5 bars ending before ts
    recent = m5_bars[m5_bars.index < ts].tail(3)
    if len(recent) == 0:
        return False, "no M5 bars before timestamp", {}

    for i in range(len(recent) - 1, -1, -1):
        bar = recent.iloc[i]
        o, c, h, lo = float(bar["open"]), float(bar["close"]), float(bar["high"]), float(bar["low"])
        bar_range = h - lo

        # Get delta for this M5 bar
        bar_ts = recent.index[i]
        delta = 0.0
        if m5_delta is not None and bar_ts in m5_delta.index:
            delta = float(m5_delta.loc[bar_ts])

        if bar_range < min_range:
            continue

        if direction == "LONG":
            if c <= o:
                continue
            if abs(delta) > 0 and delta < DISP_MIN_DELTA:
                continue
            if bar_range > 0 and (c - lo) / bar_range < DISP_CLOSE_PCT:
                continue
            return True, f"displacement LONG: range={bar_range:.1f}>{min_range:.1f} delta={delta:+.0f}", {
                "bar_ts": str(bar_ts), "range": bar_range, "delta": delta, "close_pct": (c-lo)/bar_range if bar_range > 0 else 0
            }
        else:  # SHORT
            if c >= o:
                continue
            if abs(delta) > 0 and delta > -DISP_MIN_DELTA:
                continue
            if bar_range > 0 and (h - c) / bar_range < DISP_CLOSE_PCT:
                continue
            return True, f"displacement SHORT: range={bar_range:.1f}>{min_range:.1f} delta={delta:+.0f}", {
                "bar_ts": str(bar_ts), "range": bar_range, "delta": delta, "close_pct": (h-c)/bar_range if bar_range > 0 else 0
            }

    return False, "no valid displacement bar in last 3 M5", {}


def check_exhaustion(close, direction, liq_top, liq_bot, atr_m30):
    """Check overextension exhaustion (only active check without live decision)."""
    overext_thr = atr_m30 * OVEREXT_ATR_MULT

    if direction == "LONG" and liq_top and liq_top > 0:
        if close > liq_top:
            dist = abs(close - liq_top)
            if dist > overext_thr:
                return True, f"overextended LONG: {dist:.1f}pts > {overext_thr:.1f} ({OVEREXT_ATR_MULT}x ATR)"
    elif direction == "SHORT" and liq_bot and liq_bot > 0:
        if close < liq_bot:
            dist = abs(liq_bot - close)
            if dist > overext_thr:
                return True, f"overextended SHORT: {dist:.1f}pts > {overext_thr:.1f} ({OVEREXT_ATR_MULT}x ATR)"

    return False, "not exhausted"


def main():
    print("SHADOW MODE: TREND_CONTINUATION Signal Collection")
    print(f"Target: {TARGET_SIGNALS} signals")
    print(f"Period: {START_DATE} to today")
    print(f"TREND_ACCEPTANCE_MIN_BARS = {TREND_ACCEPTANCE_MIN_BARS}")
    print()

    m30 = load_m30()
    m5 = load_m5()
    daily_trend = load_daily_trend()
    m30["daily_trend"] = daily_trend.reindex(m30.index, method="ffill")

    V1_DIST = 8.0
    signals = []
    last_date_loaded = None
    m5_delta_cache = None
    dates_without_micro = set()

    print("\nScanning M30 bars for CONTINUATION candidates...")
    print(f"{'='*80}")

    for i in range(10, len(m30)):
        if len(signals) >= TARGET_SIGNALS:
            break

        row = m30.iloc[i]
        close = float(row["close"])
        liq_top = float(row["m30_liq_top"]) if pd.notna(row.get("m30_liq_top")) else None
        liq_bot = float(row["m30_liq_bot"]) if pd.notna(row.get("m30_liq_bot")) else None
        atr = float(row.get("atr14", 20.0)) if pd.notna(row.get("atr14")) else 20.0
        daily_t = row.get("daily_trend", None)
        ts = m30.index[i]

        if close <= 0 or daily_t not in ("long", "short"):
            continue

        # Check if touching a liquidity level
        touch_top = liq_top and abs(close - liq_top) <= V1_DIST
        touch_bot = liq_bot and abs(close - liq_bot) <= V1_DIST
        if not touch_top and not touch_bot:
            continue

        level_type = "liq_top" if touch_top else "liq_bot"
        trend_dir = "LONG" if daily_t == "long" else "SHORT"

        # PULLBACK check first (these are NOT continuation)
        if trend_dir == "LONG" and level_type == "liq_bot":
            continue  # This would be PULLBACK, not CONTINUATION
        if trend_dir == "SHORT" and level_type == "liq_top":
            continue  # This would be PULLBACK, not CONTINUATION

        # Only continuation candidates remain:
        # LONG trend + liq_top touch, or SHORT trend + liq_bot touch
        # These are the cases where we need to decide: CONTINUATION vs SKIP vs OVEREXTENSION

        # Phase check
        phase, phase_det = compute_phase(m30, i)
        if phase not in ("TREND", "EXPANSION"):
            continue

        # Load M5 delta for this day (cache per date)
        date_str = ts.strftime("%Y-%m-%d")
        if date_str != last_date_loaded and date_str not in dates_without_micro:
            m5_delta_cache = load_micro_delta_m5(date_str)
            if m5_delta_cache is None:
                dates_without_micro.add(date_str)
            last_date_loaded = date_str

        # Displacement check (M5 OHLCV + delta)
        disp_valid, disp_reason, disp_details = check_displacement(
            m5, m5_delta_cache, ts, trend_dir, atr)

        # Exhaustion check (overextension)
        exhausted, exh_reason = check_exhaustion(close, trend_dir, liq_top, liq_bot, atr)

        # V1 zone check
        v1_pass = True  # already filtered by V1_DIST

        # Decision
        if not disp_valid:
            would_action = "BLOCK"
            decision_reason = f"no displacement: {disp_reason}"
        elif exhausted:
            would_action = "BLOCK"
            decision_reason = f"exhaustion: {exh_reason}"
        else:
            would_action = "GO"
            decision_reason = f"CONTINUATION {trend_dir}: {disp_reason}"

        signal = {
            "idx": len(signals) + 1,
            "timestamp": str(ts),
            "date": date_str,
            "close": close,
            "phase": phase,
            "daily_trend": daily_t,
            "trend_dir": trend_dir,
            "level_type": level_type,
            "liq_top": liq_top,
            "liq_bot": liq_bot,
            "atr_m30": atr,
            "box_high": float(row["m30_box_high"]) if pd.notna(row.get("m30_box_high")) else None,
            "box_low": float(row["m30_box_low"]) if pd.notna(row.get("m30_box_low")) else None,
            "bars_outside": phase_det.get("bars_outside", 0),
            "ladder": phase_det.get("ladder", False),
            "v1_zone": "PASS",
            "displacement": disp_valid,
            "displacement_reason": disp_reason,
            "disp_delta": disp_details.get("delta", 0),
            "disp_range": disp_details.get("range", 0),
            "exhausted": exhausted,
            "exhaustion_reason": exh_reason,
            "would_action": would_action,
            "decision_reason": decision_reason,
        }
        signals.append(signal)

        # Print each signal
        icon = ">> GO  " if would_action == "GO" else "BLOCK  "
        print(f"  #{signal['idx']:>2} {icon} {ts}  {phase:11s}  {trend_dir}  "
              f"close={close:.1f}  disp={'Y' if disp_valid else 'N'}  "
              f"exh={'Y' if exhausted else 'N'}  | {decision_reason[:50]}")

    print(f"\n{'='*80}")
    print(f"Collected {len(signals)} TREND_CONTINUATION signals")

    if not signals:
        print("No signals found!")
        return

    df = pd.DataFrame(signals)

    # =========================================================
    # SUMMARY
    # =========================================================
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Total signals: {len(df)}")
    print(f"  would_GO:    {len(df[df['would_action'] == 'GO'])}")
    print(f"  would_BLOCK: {len(df[df['would_action'] == 'BLOCK'])}")

    print(f"\nBy phase:")
    for phase in ["EXPANSION", "TREND"]:
        sub = df[df["phase"] == phase]
        go = len(sub[sub["would_action"] == "GO"])
        block = len(sub[sub["would_action"] == "BLOCK"])
        print(f"  {phase:11s}: {len(sub):>3} signals  (GO={go}, BLOCK={block})")

    print(f"\nBy direction:")
    for d in df["trend_dir"].unique():
        sub = df[df["trend_dir"] == d]
        go = len(sub[sub["would_action"] == "GO"])
        print(f"  {d:6s}: {len(sub):>3} signals  (GO={go})")

    # =========================================================
    # BLOCK REASONS
    # =========================================================
    blocked = df[df["would_action"] == "BLOCK"]
    if not blocked.empty:
        print(f"\nBLOCK reasons breakdown:")
        no_disp = blocked[blocked["displacement"] == False]
        exh_only = blocked[(blocked["displacement"] == True) & (blocked["exhausted"] == True)]
        print(f"  No displacement: {len(no_disp)}")
        print(f"  Exhaustion (overextension): {len(exh_only)}")

    # =========================================================
    # DISPLACEMENT ANALYSIS
    # =========================================================
    print(f"\nDisplacement analysis:")
    disp_yes = df[df["displacement"] == True]
    disp_no = df[df["displacement"] == False]
    print(f"  Displacement found: {len(disp_yes)}/{len(df)}")
    print(f"  No displacement:    {len(disp_no)}/{len(df)}")
    if not disp_yes.empty:
        print(f"  Avg displacement delta: {disp_yes['disp_delta'].mean():+.1f}")
        print(f"  Avg displacement range: {disp_yes['disp_range'].mean():.1f}")

    # =========================================================
    # EXHAUSTION ANALYSIS
    # =========================================================
    print(f"\nExhaustion analysis:")
    exh_yes = df[df["exhausted"] == True]
    print(f"  Exhausted (overextended): {len(exh_yes)}/{len(df)}")
    if not exh_yes.empty:
        for _, r in exh_yes.iterrows():
            print(f"    #{r['idx']} {r['timestamp']}  {r['exhaustion_reason']}")

    # =========================================================
    # QUALITATIVE: GO signals detail
    # =========================================================
    go_signals = df[df["would_action"] == "GO"]
    print(f"\n{'='*80}")
    print(f"QUALITATIVE: {len(go_signals)} would-GO signals")
    print(f"{'='*80}")
    if not go_signals.empty:
        for _, r in go_signals.iterrows():
            # Evaluate quality heuristic:
            # - TREND phase + displacement + not exhausted = likely valid
            # - EXPANSION phase = possibly premature
            # - Low bars_outside = possibly early
            quality = "LIKELY VALID"
            notes = []
            if r["phase"] == "EXPANSION":
                notes.append("EXPANSION (not TREND)")
            if r["bars_outside"] < 4:
                notes.append(f"bars_out={r['bars_outside']}<4")
            if abs(r["disp_delta"]) < 150:
                notes.append(f"weak delta={r['disp_delta']:+.0f}")
            if notes:
                quality = "CHECK: " + ", ".join(notes)

            print(f"  #{r['idx']:>2} {r['timestamp']}  {r['phase']:11s}  {r['trend_dir']}  "
                  f"close={r['close']:.1f}  delta={r['disp_delta']:+.0f}  "
                  f"range={r['disp_range']:.1f}  [{quality}]")

    # =========================================================
    # KEY OBSERVATIONS
    # =========================================================
    print(f"\n{'='*80}")
    print("KEY OBSERVATIONS")
    print(f"{'='*80}")

    go_pct = len(go_signals) / len(df) * 100 if len(df) > 0 else 0
    print(f"\n  1. GO rate: {go_pct:.1f}% ({len(go_signals)}/{len(df)})")

    exp_count = len(df[df["phase"] == "EXPANSION"])
    trend_count = len(df[df["phase"] == "TREND"])
    print(f"  2. Phase bias: EXPANSION={exp_count}, TREND={trend_count}")
    if exp_count > trend_count * 3:
        print(f"     >> Heavy EXPANSION bias - most signals come before TREND confirmed")

    no_disp_pct = len(disp_no) / len(df) * 100 if len(df) > 0 else 0
    print(f"  3. Displacement filter: blocks {no_disp_pct:.1f}% of candidates")

    # Direction bias
    long_count = len(df[df["trend_dir"] == "LONG"])
    short_count = len(df[df["trend_dir"] == "SHORT"])
    print(f"  4. Direction: LONG={long_count}, SHORT={short_count}")

    # Recurring patterns in blocks
    if not blocked.empty:
        print(f"  5. Block patterns:")
        for reason, count in blocked["decision_reason"].str[:40].value_counts().head(5).items():
            print(f"     {count:>3}x  {reason}")

    # Save
    out_path = Path(r"C:\FluxQuantumAI\data\calibration")
    out_path.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path / "shadow_trend_continuation_signals.parquet")
    df.to_csv(out_path / "shadow_trend_continuation_signals.csv", index=False)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()

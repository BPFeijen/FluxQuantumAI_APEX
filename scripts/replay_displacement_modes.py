#!/usr/bin/env python3
"""
Displacement Mode Comparison — 9 months replay.

Compares 3 displacement configurations:
  CURRENT: M5 bar vs ATR M30 (1% pass rate)
  OPT_A:   M30 bar vs ATR M30 (59% pass rate)
  OPT_B:   M5 bar vs ATR M5 (62% pass rate)

For each mode, measures pass rate and impact on CONTINUATION triggers.

Usage: python scripts/replay_displacement_modes.py
"""

import sys
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

DATA_DIR = Path("C:/data/processed")
M5_PATH = DATA_DIR / "gc_m5_boxes.parquet"
M30_PATH = DATA_DIR / "gc_m30_boxes.parquet"


def check_displacement(bars_df, direction, min_range, close_pct, min_delta, require_delta):
    """Check last 3 bars for displacement. Returns (pass, reason, bar_range, atr)."""
    recent = bars_df.tail(4).iloc[:-1]
    if len(recent) == 0:
        return False, "no bars", 0, 0

    for i in range(len(recent) - 1, -1, -1):
        bar = recent.iloc[i]
        o, c = float(bar["open"]), float(bar["close"])
        h, lo = float(bar["high"]), float(bar["low"])
        bar_range = h - lo
        delta = float(bar.get("bar_delta", 0))

        if bar_range < min_range:
            continue

        if direction == "LONG":
            if c <= o:
                continue
            if require_delta and abs(delta) > 0 and delta < min_delta:
                continue
            if bar_range > 0 and (c - lo) / bar_range < close_pct:
                continue
            return True, f"range={bar_range:.1f}", bar_range, float(bar.get("atr14", 0))
        else:
            if c >= o:
                continue
            if require_delta and abs(delta) > 0 and delta > -min_delta:
                continue
            if bar_range > 0 and (h - c) / bar_range < close_pct:
                continue
            return True, f"range={bar_range:.1f}", bar_range, float(bar.get("atr14", 0))

    return False, "no valid bar", 0, 0


def main():
    print("=" * 70)
    print("  DISPLACEMENT MODE COMPARISON -- 9 months")
    print("=" * 70)

    # Load data
    print("  Loading M5 and M30...")
    m5 = pd.read_parquet(M5_PATH)
    m30 = pd.read_parquet(M30_PATH)
    if m5.index.tz is None:
        m5.index = m5.index.tz_localize("UTC")
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")

    # Filter to Jul 2025+
    m5 = m5[m5.index >= "2025-07-01"]
    m30 = m30[m30.index >= "2025-07-01"]
    print(f"  M5 bars: {len(m5):,}")
    print(f"  M30 bars: {len(m30):,}")

    disp_mult = 0.8
    min_delta = 80
    close_pct = 0.7

    # Test at every M30 bar close (structural decision points)
    m30_closes = m30.index[m30.index >= "2025-07-01"]
    print(f"  Testing at {len(m30_closes):,} M30 bar closes...")

    results = {"CURRENT": [], "OPT_A": [], "OPT_B": []}

    for i, ts in enumerate(m30_closes):
        if i % 2000 == 0:
            print(f"  {i}/{len(m30_closes)}...", flush=True)

        for direction in ["LONG", "SHORT"]:
            # ATR M30 at this point
            m30_slice = m30.loc[:ts]
            if len(m30_slice) < 15:
                continue
            atr_m30 = float(m30_slice["atr14"].iloc[-1])
            if atr_m30 <= 0 or np.isnan(atr_m30):
                continue

            # CURRENT: M5 bars, ATR M30
            m5_slice = m5.loc[:ts]
            if len(m5_slice) >= 4:
                min_range_current = atr_m30 * disp_mult
                c_pass, c_reason, c_range, _ = check_displacement(
                    m5_slice, direction, min_range_current, close_pct, min_delta, True)
                results["CURRENT"].append({
                    "ts": ts, "dir": direction, "pass": c_pass,
                    "bar_range": c_range, "threshold": min_range_current, "atr": atr_m30})

            # OPT_A: M30 bars, ATR M30
            min_range_a = atr_m30 * disp_mult
            a_pass, a_reason, a_range, _ = check_displacement(
                m30_slice, direction, min_range_a, close_pct, min_delta, False)
            results["OPT_A"].append({
                "ts": ts, "dir": direction, "pass": a_pass,
                "bar_range": a_range, "threshold": min_range_a, "atr": atr_m30})

            # OPT_B: M5 bars, ATR M5
            if len(m5_slice) >= 4:
                atr_m5 = float(m5_slice["atr14"].iloc[-1])
                if atr_m5 > 0 and not np.isnan(atr_m5):
                    min_range_b = atr_m5 * disp_mult
                    b_pass, b_reason, b_range, _ = check_displacement(
                        m5_slice, direction, min_range_b, close_pct, min_delta, True)
                    results["OPT_B"].append({
                        "ts": ts, "dir": direction, "pass": b_pass,
                        "bar_range": b_range, "threshold": min_range_b, "atr": atr_m5})

    # Analyze
    print(f"\n  1. PASS RATE BY MODE")
    print(f"     {'Mode':<25s}  {'Total':>7s}  {'Pass':>7s}  {'Rate':>7s}")
    print(f"     {'-'*25}  {'-'*7}  {'-'*7}  {'-'*7}")
    for mode in ["CURRENT", "OPT_A", "OPT_B"]:
        df = pd.DataFrame(results[mode])
        total = len(df)
        passed = df["pass"].sum()
        rate = 100 * passed / total if total else 0
        print(f"     {mode:<25s}  {total:>7,}  {passed:>7,}  {rate:>6.1f}%")

    # 2. Per direction
    print(f"\n  2. PASS RATE BY MODE + DIRECTION")
    print(f"     {'Mode':<25s}  {'Dir':>5s}  {'Total':>7s}  {'Pass':>7s}  {'Rate':>7s}")
    print(f"     {'-'*25}  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*7}")
    for mode in ["CURRENT", "OPT_A", "OPT_B"]:
        df = pd.DataFrame(results[mode])
        for d in ["LONG", "SHORT"]:
            sub = df[df["dir"] == d]
            total = len(sub)
            passed = sub["pass"].sum()
            rate = 100 * passed / total if total else 0
            print(f"     {mode:<25s}  {d:>5s}  {total:>7,}  {passed:>7,}  {rate:>6.1f}%")

    # 3. Cases where modes disagree
    print(f"\n  3. DIVERGENCE ANALYSIS")
    df_c = pd.DataFrame(results["CURRENT"]).set_index(["ts", "dir"])
    df_a = pd.DataFrame(results["OPT_A"]).set_index(["ts", "dir"])

    common = df_c.index.intersection(df_a.index)
    c_vals = df_c.loc[common, "pass"]
    a_vals = df_a.loc[common, "pass"]

    both_pass = (c_vals & a_vals).sum()
    both_fail = (~c_vals & ~a_vals).sum()
    c_only = (c_vals & ~a_vals).sum()
    a_only = (~c_vals & a_vals).sum()

    print(f"     Both PASS:           {both_pass:>7,}")
    print(f"     Both FAIL:           {both_fail:>7,}")
    print(f"     CURRENT pass only:   {c_only:>7,}  (would lose in OPT_A)")
    print(f"     OPT_A pass only:     {a_only:>7,}  (cases CURRENT is missing)")
    print(f"     Cases missed by CURRENT: {a_only}")

    # 4. OPT_A false positives check
    # Where OPT_A passes but no significant move followed
    if a_only > 0:
        a_only_idx = common[(~c_vals & a_vals)]
        print(f"\n  4. OPT_A EXCLUSIVE PASSES: {len(a_only_idx)} cases")
        print(f"     (These are displacement events that CURRENT misses)")
        # Sample 10
        sample = a_only_idx[:10]
        for ts, d in sample:
            row_a = df_a.loc[(ts, d)]
            row_c = df_c.loc[(ts, d)]
            print(f"     {ts} {d}: OPT_A range={row_a['bar_range']:.1f} thr={row_a['threshold']:.1f} | "
                  f"CURRENT thr={row_c['threshold']:.1f}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

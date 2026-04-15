#!/usr/bin/env python3
"""
FASE 1 Validation: Compare ATR proxy vs ATR14 M30 parquet.

Reads today's (or most recent) microstructure + M30 parquet and shows:
  - atr_proxy (30min range from microstructure)
  - atr_m30_parquet (ATR14 from gc_m30_boxes.parquet)
  - NEAR band with each
  - Overextension threshold with each
  - Practical difference in triggers/blocks

Usage: python scripts/validate_fase1_atr.py
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# Paths
DATA_DIR = Path("C:/data")
M30_PATH = DATA_DIR / "processed/gc_m30_boxes.parquet"
MICRO_DIR = Path("C:/data/level2/_gc_xcec")

# Constants (from event_processor.py)
NEAR_ATR_FACTOR = 1.0
NEAR_FLOOR_PTS = 5.0
OVEREXT_MULT = 1.5


def get_atr_m30_parquet():
    """Get ATR14 from M30 parquet (last row with data)."""
    df = pd.read_parquet(M30_PATH, columns=["atr14", "m30_liq_top", "m30_liq_bot", "m30_fmv"])
    df = df.dropna(subset=["atr14"])
    if df.empty:
        return None, {}
    last = df.iloc[-1]
    return float(last["atr14"]), {
        "liq_top": float(last["m30_liq_top"]) if pd.notna(last["m30_liq_top"]) else None,
        "liq_bot": float(last["m30_liq_bot"]) if pd.notna(last["m30_liq_bot"]) else None,
        "fmv": float(last["m30_fmv"]) if pd.notna(last["m30_fmv"]) else None,
    }


def get_atr_proxy_series(micro_path):
    """Compute rolling ATR proxy (30min range) from microstructure, sampled every 5 min."""
    df = pd.read_csv(micro_path, usecols=["timestamp", "mid_price"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.dropna(subset=["mid_price", "timestamp"]).set_index("timestamp").sort_index()

    if df.empty:
        return pd.DataFrame()

    # Resample to 1-min for rolling window
    m1 = df["mid_price"].resample("1min").agg(["max", "min"]).dropna()

    # Rolling 30-min range (same as event_processor: tail(1800) ~ 30min of ticks)
    m1["atr_proxy"] = m1["max"].rolling(30).max() - m1["min"].rolling(30).min()
    m1["atr_proxy"] = m1["atr_proxy"].clip(lower=5.0)

    # Sample every 5 min for readability
    result = m1["atr_proxy"].resample("5min").last().dropna()
    return result


def main():
    print("=" * 70)
    print("  FASE 1 VALIDATION: ATR proxy vs ATR14 M30 parquet")
    print("=" * 70)

    # 1. ATR M30 parquet (stable)
    atr_m30, m30_levels = get_atr_m30_parquet()
    if atr_m30 is None:
        print("ERROR: M30 parquet has no ATR14 data")
        sys.exit(1)

    print(f"\n  ATR14 M30 parquet : {atr_m30:.2f} pts")
    print(f"  M30 liq_top       : {m30_levels.get('liq_top', '?')}")
    print(f"  M30 liq_bot       : {m30_levels.get('liq_bot', '?')}")
    print(f"  M30 fmv           : {m30_levels.get('fmv', '?')}")

    # 2. ATR proxy from microstructure (volatile)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    micro_path = MICRO_DIR / f"microstructure_{today}.csv.gz"
    if not micro_path.exists():
        # Try yesterday
        files = sorted(MICRO_DIR.glob("microstructure_*.csv.gz"))
        if not files:
            print("ERROR: No microstructure files found")
            sys.exit(1)
        micro_path = files[-1]
        print(f"\n  (Today's micro not found, using: {micro_path.name})")

    proxy_series = get_atr_proxy_series(micro_path)
    if proxy_series.empty:
        print("ERROR: Could not compute ATR proxy from microstructure")
        sys.exit(1)

    print(f"\n  ATR proxy stats (from {micro_path.name}):")
    print(f"    min   : {proxy_series.min():.2f}")
    print(f"    max   : {proxy_series.max():.2f}")
    print(f"    mean  : {proxy_series.mean():.2f}")
    print(f"    std   : {proxy_series.std():.2f}")
    print(f"    last  : {proxy_series.iloc[-1]:.2f}")

    # 3. Compare NEAR band
    band_m30 = max(atr_m30 * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)
    band_proxy_min = max(proxy_series.min() * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)
    band_proxy_max = max(proxy_series.max() * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)
    band_proxy_mean = max(proxy_series.mean() * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)

    print(f"\n  NEAR band comparison (NEAR_ATR_FACTOR={NEAR_ATR_FACTOR}):")
    print(f"    M30 parquet (stable) : {band_m30:.1f} pts")
    print(f"    Proxy min            : {band_proxy_min:.1f} pts")
    print(f"    Proxy max            : {band_proxy_max:.1f} pts")
    print(f"    Proxy mean           : {band_proxy_mean:.1f} pts")

    # 4. Compare overextension threshold
    overext_m30 = atr_m30 * OVEREXT_MULT
    overext_proxy_min = proxy_series.min() * OVEREXT_MULT
    overext_proxy_max = proxy_series.max() * OVEREXT_MULT

    print(f"\n  Overextension threshold comparison (mult={OVEREXT_MULT}):")
    print(f"    M30 parquet (stable) : {overext_m30:.1f} pts")
    print(f"    Proxy min            : {overext_proxy_min:.1f} pts")
    print(f"    Proxy max            : {overext_proxy_max:.1f} pts")

    # 5. Practical impact analysis
    print(f"\n  PRACTICAL IMPACT:")

    # Count how many 5-min windows would have a DIFFERENT trigger/block outcome
    diff_near = 0
    diff_overext = 0
    total = len(proxy_series)

    for atr_p in proxy_series:
        band_p = max(atr_p * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)
        # If a price was within band_p but NOT within band_m30 (or vice versa)
        # we can't know exact prices, but we can flag when bands diverge >2pts
        if abs(band_p - band_m30) > 2.0:
            diff_near += 1

        overext_p = atr_p * OVEREXT_MULT
        if abs(overext_p - overext_m30) > 3.0:
            diff_overext += 1

    print(f"    NEAR band divergence >2pts : {diff_near}/{total} windows ({100*diff_near/total:.0f}%)")
    print(f"    Overext divergence >3pts   : {diff_overext}/{total} windows ({100*diff_overext/total:.0f}%)")

    # 6. Timeline of divergence (last 2 hours)
    recent = proxy_series.tail(24)  # 24 x 5min = 2h
    print(f"\n  Last 2h timeline (5-min samples):")
    print(f"    {'Time':>12s}  {'ATR_proxy':>10s}  {'ATR_M30':>8s}  {'Band_p':>7s}  {'Band_m30':>9s}  {'Delta':>6s}")
    for ts, atr_p in recent.items():
        bp = max(atr_p * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)
        delta = bp - band_m30
        flag = " ***" if abs(delta) > 3.0 else ""
        print(f"    {ts.strftime('%H:%M UTC'):>12s}  {atr_p:10.2f}  {atr_m30:8.2f}  {bp:7.1f}  {band_m30:9.1f}  {delta:+6.1f}{flag}")

    print()
    print("=" * 70)
    print("  *** = divergence > 3pts (would cause different trigger/block)")
    print("=" * 70)


if __name__ == "__main__":
    main()

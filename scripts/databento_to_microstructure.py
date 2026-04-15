#!/usr/bin/env python3
"""
databento_to_microstructure.py
==============================

Converts Databento MBP-10 raw files (Jul-Nov 2025) into the same
microstructure_YYYY-MM-DD.csv.gz format that Quantower produces live.

Input:  C:/data/level2/_gc_xcec/GLBX-20260407-RQ5S6KR3E5/glbx-mdp3-YYYYMMDD.mbp-10.csv.zst
Output: C:/data/level2/_gc_xcec/microstructure_YYYY-MM-DD.csv.gz

Generates all 41 columns matching the Quantower schema.
Columns derived from MBP-10 L2 data; some computed from order flow analysis.

Usage:
    python databento_to_microstructure.py                 # all files
    python databento_to_microstructure.py --date 2025-07-01  # single day
    python databento_to_microstructure.py --dry-run        # preview only
"""

from __future__ import annotations

import argparse
import gzip
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths ──────────────────────────────────────────────────────────────
RAW_DIR    = Path("C:/data/level2/_gc_xcec/GLBX-20260407-RQ5S6KR3E5")
OUTPUT_DIR = Path("C:/data/level2/_gc_xcec")

# Tick size for GC (COMEX Gold Futures)
TICK_SIZE = 0.10  # $0.10 per tick


def load_day(date_str: str) -> pd.DataFrame:
    """
    Load one day of MBP-10 data, auto-detect GC front-month (most volume), return L2 snapshots.
    """
    compact = date_str.replace("-", "")
    fp = RAW_DIR / f"glbx-mdp3-{compact}.mbp-10.csv.zst"
    if not fp.exists():
        return pd.DataFrame()

    df = pd.read_csv(fp, compression="zstd")

    # Filter single contracts only (no spreads like GCQ5-GCZ5)
    df = df[~df["symbol"].str.contains("-", na=False)]

    if df.empty:
        return pd.DataFrame()

    # Auto-detect front month: symbol with most rows (= most liquid)
    top_sym = df["symbol"].value_counts().index[0]
    df = df[df["symbol"] == top_sym]

    # Parse timestamps
    df["ts_recv"] = pd.to_datetime(df["ts_recv"], utc=True)
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)

    # Filter valid rows (bid_sz_00 > 0 AND ask_sz_00 > 0)
    df = df[(df["bid_sz_00"] > 0) & (df["ask_sz_00"] > 0)].copy()

    return df


def compute_microstructure(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """
    Convert MBP-10 snapshots to Quantower microstructure schema (41 columns).
    Resamples to ~1-second snapshots (last state per second).
    """
    if df.empty:
        return pd.DataFrame()

    # Resample to 1-second (take last snapshot per second)
    df = df.set_index("ts_recv")
    df = df.resample("1s").last().dropna(subset=["bid_px_00", "ask_px_00"])

    n = len(df)
    if n == 0:
        return pd.DataFrame()

    # ── Direct columns ─────────────────────────────────────────────
    best_bid = df["bid_px_00"].values
    best_ask = df["ask_px_00"].values
    mid_price = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid
    spread_pct = spread / mid_price * 100.0

    # Total bid/ask size (sum of 10 levels)
    bid_cols = [f"bid_sz_0{i}" for i in range(10)]
    ask_cols = [f"ask_sz_0{i}" for i in range(10)]
    total_bid = df[bid_cols].sum(axis=1).values.astype(float)
    total_ask = df[ask_cols].sum(axis=1).values.astype(float)

    # DOM imbalance = (total_bid - total_ask) / (total_bid + total_ask)
    total = total_bid + total_ask
    dom_imbalance = np.where(total > 0, (total_bid - total_ask) / total, 0.0)

    # Pressure: bid_pressure = sum(bid_sz * inverse_distance), ask_pressure similar
    bid_pressure = np.zeros(n)
    ask_pressure = np.zeros(n)
    for i in range(10):
        w = 1.0 / (i + 1)  # inverse distance weight
        bid_pressure += df[f"bid_sz_0{i}"].values * w
        ask_pressure += df[f"ask_sz_0{i}"].values * w
    pressure_ratio = np.where(bid_pressure > 0, ask_pressure / bid_pressure, 1.0)

    # Wall detection (largest size in 10 levels)
    bid_sizes = df[bid_cols].values
    ask_sizes = df[ask_cols].values
    bid_prices = df[[f"bid_px_0{i}" for i in range(10)]].values
    ask_prices = df[[f"ask_px_0{i}" for i in range(10)]].values

    wall_bid_idx = np.argmax(bid_sizes, axis=1)
    wall_ask_idx = np.argmax(ask_sizes, axis=1)
    wall_bid_size = np.array([bid_sizes[j, wall_bid_idx[j]] for j in range(n)])
    wall_ask_size = np.array([ask_sizes[j, wall_ask_idx[j]] for j in range(n)])
    wall_bid_price = np.array([bid_prices[j, wall_bid_idx[j]] for j in range(n)])
    wall_ask_price = np.array([ask_prices[j, wall_ask_idx[j]] for j in range(n)])
    wall_distance_ticks = np.abs(wall_bid_price - wall_ask_price) / TICK_SIZE

    # ── Derived columns (rolling / cumulative) ────────────────────

    # bar_delta: approximate from order flow (bid_sz changes vs ask_sz changes)
    # Since we have L2 snapshots, approximate delta as change in bid_sz_00 - change in ask_sz_00
    delta_bid = np.diff(total_bid, prepend=total_bid[0])
    delta_ask = np.diff(total_ask, prepend=total_ask[0])
    bar_delta = delta_bid - delta_ask

    # Cumulative delta
    cumulative_delta = np.cumsum(bar_delta)

    # Absorption detection: large order on one side consumed rapidly
    # Approximation: if wall_size drops >50% in one step AND opposite side grew
    absorption_detected = np.zeros(n, dtype=bool)
    absorption_ratio = np.zeros(n)
    absorption_side = np.full(n, "", dtype=object)

    for j in range(1, n):
        prev_bid_wall = wall_bid_size[j - 1]
        prev_ask_wall = wall_ask_size[j - 1]
        if prev_bid_wall > 3 and wall_bid_size[j] < prev_bid_wall * 0.5:
            absorption_detected[j] = True
            absorption_ratio[j] = prev_bid_wall / max(wall_bid_size[j], 1)
            absorption_side[j] = "bid"
        elif prev_ask_wall > 3 and wall_ask_size[j] < prev_ask_wall * 0.5:
            absorption_detected[j] = True
            absorption_ratio[j] = prev_ask_wall / max(wall_ask_size[j], 1)
            absorption_side[j] = "ask"

    # Large order detection
    bid_ct_cols = [f"bid_ct_0{i}" for i in range(10)]
    ask_ct_cols = [f"ask_ct_0{i}" for i in range(10)]
    large_bid = np.zeros(n)
    large_ask = np.zeros(n)
    if all(c in df.columns for c in bid_ct_cols):
        for i in range(10):
            large_bid += (df[f"bid_sz_0{i}"].values > 5).astype(float)
            large_ask += (df[f"ask_sz_0{i}"].values > 5).astype(float)
    large_total = large_bid + large_ask
    large_order_imbalance = np.where(large_total > 0, (large_bid - large_ask) / large_total, 0.0)

    # Sweep detection: multiple levels consumed in one step
    sweep_detected = np.zeros(n, dtype=bool)
    levels_swept = np.zeros(n, dtype=int)
    sweep_direction = np.full(n, "", dtype=object)
    for j in range(1, n):
        # Count how many bid levels went to zero
        bid_zeroed = sum(1 for i in range(10)
                        if bid_sizes[j - 1, i] > 0 and bid_sizes[j, i] == 0)
        ask_zeroed = sum(1 for i in range(10)
                        if ask_sizes[j - 1, i] > 0 and ask_sizes[j, i] == 0)
        if bid_zeroed >= 2:
            sweep_detected[j] = True
            levels_swept[j] = bid_zeroed
            sweep_direction[j] = "SELL"
        elif ask_zeroed >= 2:
            sweep_detected[j] = True
            levels_swept[j] = ask_zeroed
            sweep_direction[j] = "BUY"

    # Toxicity score: spread × imbalance × sweep
    tox_spread = np.clip(spread / np.median(spread[spread > 0]) if np.any(spread > 0) else spread, 0, 5)
    tox_imbal = np.abs(dom_imbalance)
    toxicity_score = np.clip(0.3 * tox_spread + 0.4 * tox_imbal + 0.3 * sweep_detected.astype(float), 0, 1)

    # Session volumes (cumulative from start of day)
    # Approximate from size changes
    trade_activity = np.abs(bar_delta)
    trades_per_second = trade_activity
    volume_per_second = np.abs(delta_bid) + np.abs(delta_ask)
    session_volume = np.cumsum(volume_per_second)
    session_trade_count = np.cumsum((trade_activity > 0).astype(int))
    session_buy_volume = np.cumsum(np.maximum(bar_delta, 0))
    session_sell_volume = np.cumsum(np.maximum(-bar_delta, 0))

    # Liquidity shift
    liquidity_shift = np.diff(total_bid + total_ask, prepend=total_bid[0] + total_ask[0])

    # POC: price with most volume (approximate from L2 snapshot)
    poc_prices = np.zeros(n)
    poc_volumes = np.zeros(n)
    for j in range(n):
        all_prices = np.concatenate([bid_prices[j], ask_prices[j]])
        all_sizes = np.concatenate([bid_sizes[j], ask_sizes[j]])
        valid = ~np.isnan(all_prices) & (all_sizes > 0)
        if valid.any():
            idx = np.argmax(all_sizes[valid])
            poc_prices[j] = all_prices[valid][idx]
            poc_volumes[j] = all_sizes[valid][idx]

    distance_to_poc = np.abs(mid_price - poc_prices)

    # ── Build output DataFrame ────────────────────────────────────
    ts_index = df.index
    out = pd.DataFrame({
        "recv_timestamp":       ts_index.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
        "timestamp":            ts_index.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "dom_imbalance":        dom_imbalance,
        "total_bid_size":       total_bid,
        "total_ask_size":       total_ask,
        "bar_delta":            bar_delta,
        "cumulative_delta":     cumulative_delta,
        "spread":               spread,
        "spread_percent":       spread_pct,
        "best_bid":             best_bid,
        "best_ask":             best_ask,
        "mid_price":            mid_price,
        "bid_pressure":         bid_pressure,
        "ask_pressure":         ask_pressure,
        "pressure_ratio":       pressure_ratio,
        "wall_bid_price":       wall_bid_price,
        "wall_bid_size":        wall_bid_size,
        "wall_ask_price":       wall_ask_price,
        "wall_ask_size":        wall_ask_size,
        "wall_distance_ticks":  wall_distance_ticks,
        "absorption_detected":  absorption_detected,
        "absorption_ratio":     absorption_ratio,
        "absorption_side":      absorption_side,
        "large_bid_count":      large_bid.astype(int),
        "large_ask_count":      large_ask.astype(int),
        "large_order_imbalance": large_order_imbalance,
        "trades_per_second":    trades_per_second,
        "volume_per_second":    volume_per_second,
        "liquidity_shift":      liquidity_shift,
        "sweep_detected":       sweep_detected,
        "levels_swept":         levels_swept,
        "sweep_direction":      sweep_direction,
        "toxicity_score":       toxicity_score,
        "poc_price":            poc_prices,
        "poc_volume":           poc_volumes,
        "distance_to_poc":      distance_to_poc,
        "session_volume":       session_volume,
        "session_trade_count":  session_trade_count,
        "session_buy_volume":   session_buy_volume,
        "session_sell_volume":  session_sell_volume,
        "exchange":             "CME",
    })

    return out


def process_day(date_str: str, dry_run: bool = False) -> dict:
    """Process one day and write microstructure CSV.GZ."""
    out_path = OUTPUT_DIR / f"microstructure_{date_str}.csv.gz"

    if out_path.exists():
        return {"date": date_str, "status": "exists", "rows": 0}

    t0 = time.time()
    raw = load_day(date_str)
    if raw.empty:
        return {"date": date_str, "status": "no_data", "rows": 0}

    micro = compute_microstructure(raw, date_str)
    if micro.empty:
        return {"date": date_str, "status": "empty", "rows": 0}

    if not dry_run:
        with gzip.open(out_path, "wt", encoding="utf-8") as f:
            micro.to_csv(f, index=False)

    elapsed = time.time() - t0
    return {
        "date": date_str,
        "status": "ok",
        "rows": len(micro),
        "elapsed_s": round(elapsed, 1),
        "file_mb": round(out_path.stat().st_size / 1024 / 1024, 1) if out_path.exists() else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Databento MBP-10 -> Microstructure CSV.GZ")
    parser.add_argument("--date", type=str, help="Process single date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't write files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    print("Databento MBP-10 -> Microstructure Conversion", flush=True)
    print("=" * 50, flush=True)

    if args.date:
        dates = [args.date]
    else:
        # Find all available MBP-10 files
        files = sorted(RAW_DIR.glob("glbx-mdp3-*.mbp-10.csv.zst"))
        dates = []
        for fp in files:
            compact = fp.stem.replace("glbx-mdp3-", "").replace(".mbp-10", "")
            date_str = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
            dates.append(date_str)
        print(f"Found {len(dates)} MBP-10 files", flush=True)

    ok = 0
    skip = 0
    fail = 0
    for date_str in dates:
        if not args.force:
            out_path = OUTPUT_DIR / f"microstructure_{date_str}.csv.gz"
            if out_path.exists():
                print(f"  {date_str}: exists (skip)", flush=True)
                skip += 1
                continue

        result = process_day(date_str, dry_run=args.dry_run)
        status = result["status"]
        if status == "ok":
            print(f"  {date_str}: {result['rows']:,} rows ({result['elapsed_s']}s, {result.get('file_mb', 0)}MB)", flush=True)
            ok += 1
        elif status == "exists":
            print(f"  {date_str}: exists (skip)", flush=True)
            skip += 1
        else:
            print(f"  {date_str}: {status}", flush=True)
            fail += 1

    print(f"\nDone: {ok} converted, {skip} skipped, {fail} failed", flush=True)


if __name__ == "__main__":
    main()

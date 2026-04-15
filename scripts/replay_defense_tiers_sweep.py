#!/usr/bin/env python3
"""
Defense Mode Tier Sweep — Test multiple threshold configurations.

Replays ALL microstructure files (Jul 2025 — today) through GrenadierDefenseMode
with 4 different tier 2 threshold settings. Compares protection rate vs false alarm.

Usage: python scripts/replay_defense_tiers_sweep.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))
from inference.anomaly_scorer import GrenadierDefenseMode

MICRO_DIR = Path("C:/data/level2/_gc_xcec")

# Threshold configurations to test
CONFIGS = {
    "BASELINE (2+ OR z>2x)": {"min_trig": 2, "extreme_mult": 2.0, "require_both": False},
    "TIGHT-A (3+ OR z>3x)":  {"min_trig": 3, "extreme_mult": 3.0, "require_both": False},
    "TIGHT-B (2+ AND z>2x)": {"min_trig": 2, "extreme_mult": 2.0, "require_both": True},
    "TIGHT-C (3+ OR z>4x)":  {"min_trig": 3, "extreme_mult": 4.0, "require_both": False},
}


def classify_tier(z_spread, z_bid, z_ask, z_imb, n_triggers, cfg):
    """Classify defense tier with given config."""
    thresh_s = 3.0  # base thresholds from GrenadierDefenseMode
    thresh_d = -3.0
    thresh_i = 4.0

    any_extreme = (
        z_spread > thresh_s * cfg["extreme_mult"]
        or z_bid < thresh_d * cfg["extreme_mult"]
        or z_ask < thresh_d * cfg["extreme_mult"]
        or abs(z_imb) > thresh_i * cfg["extreme_mult"]
    )
    multi = n_triggers >= cfg["min_trig"]

    if cfg["require_both"]:
        is_tier2 = multi and any_extreme
    else:
        is_tier2 = multi or any_extreme

    if is_tier2:
        return "DEFENSIVE_EXIT"
    elif n_triggers >= 1:
        return "ENTRY_BLOCK"
    return "NORMAL"


def replay_all_files(defense):
    """Replay all micro files, return DataFrame with z-scores + prices."""
    files = sorted(MICRO_DIR.glob("microstructure_*.csv.gz"))
    # Exclude .fixed duplicates
    files = [f for f in files if ".fixed." not in f.name]
    print(f"  Files to process: {len(files)} (excluding .fixed duplicates)")

    all_rows = []
    for i, path in enumerate(files):
        if i % 20 == 0:
            print(f"  Processing {i+1}/{len(files)}: {path.name}...", flush=True)
        try:
            cols_needed = ["timestamp", "mid_price", "spread",
                           "total_bid_size", "total_ask_size"]
            all_cols = pd.read_csv(path, nrows=0).columns.tolist()
            cols_use = [c for c in cols_needed if c in all_cols]
            df = pd.read_csv(path, usecols=cols_use)
            if df.empty:
                continue

            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            bid = df.get("total_bid_size", pd.Series(dtype=float)).fillna(0)
            ask = df.get("total_ask_size", pd.Series(dtype=float)).fillna(0)
            df["book_imbalance"] = (bid - ask) / (bid + ask + 1e-10)

            for j in range(0, len(df), 10):  # sample every 10 rows
                row = df.iloc[j]
                spread = float(row.get("spread", 0) or 0)
                bid_d = float(row.get("total_bid_size", 0) or 0)
                ask_d = float(row.get("total_ask_size", 0) or 0)
                imb = float(row.get("book_imbalance", 0) or 0)
                if spread <= 0 or bid_d <= 0 or ask_d <= 0:
                    continue

                result = defense.check(spread=spread, total_bid_depth=bid_d,
                                       total_ask_depth=ask_d, book_imbalance=imb)
                all_rows.append({
                    "timestamp": row["timestamp"],
                    "mid_price": float(row.get("mid_price", 0) or 0),
                    "n_triggers": result["n_triggers"],
                    "z_spread": result["z_spread"],
                    "z_bid": result["z_bid_depth"],
                    "z_ask": result["z_ask_depth"],
                    "z_imb": result["z_imbalance"],
                })
        except Exception as e:
            pass  # skip broken files silently

    return pd.DataFrame(all_rows)


def analyze_config(df, cfg_name, cfg):
    """Classify all rows with a given config, compute stats."""
    tiers = df.apply(lambda r: classify_tier(
        r["z_spread"], r["z_bid"], r["z_ask"], r["z_imb"],
        r["n_triggers"], cfg), axis=1)

    de_mask = tiers == "DEFENSIVE_EXIT"
    n_de = de_mask.sum()

    if n_de == 0:
        return {
            "config": cfg_name, "n_events": 0, "pct": 0,
            "prot_rate": 0, "false_rate": 0,
            "moves_down_15": 0, "moves_up_15": 0,
            "mean_30min": 0, "worst_30min": 0,
        }

    de_events = df[de_mask].copy()

    # Compute 30min price change for each event
    df_indexed = df.set_index("timestamp").sort_index()
    chg_30 = []
    for _, ev in de_events.iterrows():
        t0 = ev["timestamp"]
        p0 = ev["mid_price"]
        if p0 <= 0:
            continue
        t_end = t0 + pd.Timedelta(minutes=30)
        future = df_indexed.loc[t0:t_end]
        if len(future) > 1:
            p_end = float(future["mid_price"].iloc[-1])
            chg_30.append(p_end - p0)

    chg = pd.Series(chg_30) if chg_30 else pd.Series(dtype=float)
    n_valid = len(chg)

    return {
        "config": cfg_name,
        "n_events": n_de,
        "pct": 100 * n_de / len(df),
        "n_days": de_events["timestamp"].dt.date.nunique() if not de_events.empty else 0,
        "prot_rate": 100 * (chg < -15).sum() / n_valid if n_valid else 0,
        "false_rate": 100 * ((chg > -5) & (chg < 5)).sum() / n_valid if n_valid else 0,
        "moves_down_15": int((chg < -15).sum()),
        "moves_down_20": int((chg < -20).sum()),
        "moves_up_15": int((chg > 15).sum()),
        "mean_30min": round(chg.mean(), 2) if n_valid else 0,
        "median_30min": round(chg.median(), 2) if n_valid else 0,
        "worst_30min": round(chg.min(), 2) if n_valid else 0,
        "n_valid": n_valid,
    }


def main():
    print("=" * 78)
    print("  DEFENSE MODE TIER SWEEP — Full 9-month replay (Jul 2025 — Apr 2026)")
    print("=" * 78)

    defense = GrenadierDefenseMode()
    print("\n  Step 1: Replaying all microstructure files...")
    df = replay_all_files(defense)
    print(f"\n  Total samples: {len(df):,}")
    print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

    # Base stats
    tier_counts = Counter(
        classify_tier(r.z_spread, r.z_bid, r.z_ask, r.z_imb, r.n_triggers,
                       CONFIGS["BASELINE (2+ OR z>2x)"])
        for _, r in df.iterrows()
    )
    print(f"\n  Base defense_mode triggers (any): {tier_counts.get('ENTRY_BLOCK', 0) + tier_counts.get('DEFENSIVE_EXIT', 0):,}")

    print(f"\n  Step 2: Testing {len(CONFIGS)} threshold configurations...")

    results = []
    for cfg_name, cfg in CONFIGS.items():
        print(f"\n  [{cfg_name}]")
        r = analyze_config(df, cfg_name, cfg)
        results.append(r)
        print(f"    Events:          {r['n_events']:>6,}  ({r['pct']:.3f}%)")
        print(f"    Trading days:    {r.get('n_days', '?')}")
        print(f"    30min valid:     {r['n_valid']}")
        print(f"    Prot rate (>15dn): {r['prot_rate']:.1f}%  ({r['moves_down_15']} events)")
        print(f"    False alarm:     {r['false_rate']:.1f}%")
        print(f"    Mean 30min:      {r['mean_30min']:+.2f} pts")
        print(f"    Median 30min:    {r['median_30min']:+.2f} pts")
        print(f"    Worst 30min:     {r['worst_30min']:+.2f} pts")
        print(f"    Moves >20dn:      {r['moves_down_20']}")

    # Summary table
    print("\n" + "=" * 78)
    print("  SUMMARY TABLE")
    print("=" * 78)
    print(f"  {'Config':<28s}  {'Events':>7s}  {'Prot%':>6s}  {'False%':>7s}  {'Worst':>7s}  {'>20dn':>5s}")
    print(f"  {'-'*28}  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*5}")
    for r in results:
        print(f"  {r['config']:<28s}  {r['n_events']:>7,}  {r['prot_rate']:>5.1f}%  {r['false_rate']:>6.1f}%  "
              f"{r['worst_30min']:>+7.1f}  {r['moves_down_20']:>5d}")

    # Recommendation
    print(f"\n  RECOMMENDATION:")
    best = max(results, key=lambda r: r["prot_rate"] - r["false_rate"] * 0.5)
    print(f"  Best balance (prot - 0.5*false): {best['config']}")
    print(f"    Protection: {best['prot_rate']:.1f}% | False alarm: {best['false_rate']:.1f}%")
    print("=" * 78)


if __name__ == "__main__":
    main()

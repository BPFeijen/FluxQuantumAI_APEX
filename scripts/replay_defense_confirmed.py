#!/usr/bin/env python3
"""
Defense Mode Confirmed Exit Replay -- anomaly + price action + structure.

Tests 3 tiers of confirmation:
  T1: anomaly only (TIGHT-B baseline)
  T2: anomaly + adverse price move (X pts in Y seconds)
  T3: anomaly + adverse move + M30 structural level loss

Grid:
  adverse_pts: [3, 5, 8, 10, 15]
  lookback_s:  [30, 60, 120, 300]

For each combination, measures:
  - accuracy (did exit protect?)
  - false alarm rate
  - events count
  - mean/worst 30min outcome

Usage: python scripts/replay_defense_confirmed.py
"""

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))
from inference.anomaly_scorer import GrenadierDefenseMode

MICRO_DIR = Path("C:/data/level2/_gc_xcec")
M30_PATH = Path("C:/data/processed/gc_m30_boxes.parquet")

# Grid parameters
ADVERSE_PTS = [3, 5, 8, 10, 15]
LOOKBACK_S  = [30, 60, 120, 300]


def load_m30_levels():
    """Load M30 structural levels indexed by timestamp."""
    df = pd.read_parquet(M30_PATH, columns=[
        "close", "m30_liq_top", "m30_liq_bot", "m30_box_high", "m30_box_low"
    ])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def replay_all(defense):
    """Replay all micro files, return tick-level DataFrame."""
    files = sorted(MICRO_DIR.glob("microstructure_*.csv.gz"))
    files = [f for f in files if ".fixed." not in f.name]
    print(f"  Files: {len(files)}")

    rows = []
    for i, path in enumerate(files):
        if i % 30 == 0:
            print(f"  {i+1}/{len(files)}: {path.name}", flush=True)
        try:
            cols = ["timestamp", "mid_price", "spread", "total_bid_size", "total_ask_size"]
            all_c = pd.read_csv(path, nrows=0).columns.tolist()
            use_c = [c for c in cols if c in all_c]
            df = pd.read_csv(path, usecols=use_c)
            if df.empty:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            bid = df.get("total_bid_size", pd.Series(dtype=float)).fillna(0)
            ask = df.get("total_ask_size", pd.Series(dtype=float)).fillna(0)
            df["book_imbalance"] = (bid - ask) / (bid + ask + 1e-10)

            # Keep every row for price history lookback (not sampled)
            for j in range(len(df)):
                r = df.iloc[j]
                sp = float(r.get("spread", 0) or 0)
                bd = float(r.get("total_bid_size", 0) or 0)
                ad = float(r.get("total_ask_size", 0) or 0)
                im = float(r.get("book_imbalance", 0) or 0)
                mp = float(r.get("mid_price", 0) or 0)

                # Only run defense check every 10 rows (perf), but keep all prices
                tier = "SKIP"
                if j % 10 == 0 and sp > 0 and bd > 0 and ad > 0:
                    res = defense.check(spread=sp, total_bid_depth=bd,
                                        total_ask_depth=ad, book_imbalance=im)
                    tier = res["defense_tier"]

                rows.append({
                    "timestamp": r["timestamp"],
                    "mid_price": mp,
                    "tier": tier,
                })
        except Exception:
            pass
    return pd.DataFrame(rows)


def get_adverse_move(prices_df, event_ts, lookback_s):
    """
    Get max adverse move in lookback window before event.
    Returns (max_drop, max_rise) in pts from window start price.
    """
    t_start = event_ts - pd.Timedelta(seconds=lookback_s)
    window = prices_df.loc[t_start:event_ts]
    if len(window) < 2:
        return 0.0, 0.0, 0.0
    p_start = float(window["mid_price"].iloc[0])
    p_end = float(window["mid_price"].iloc[-1])
    if p_start <= 0:
        return 0.0, 0.0, 0.0
    drop = p_start - float(window["mid_price"].min())  # max drop (positive = fell)
    rise = float(window["mid_price"].max()) - p_start   # max rise (positive = rose)
    move = p_end - p_start  # signed: negative = price fell
    return move, drop, rise


def check_m30_level_lost(m30_df, event_ts, event_price):
    """
    Check if price broke below M30 liq_bot or above M30 liq_top
    at the time of the event.
    Returns (broke_support, broke_resistance).
    """
    # Find the M30 bar active at event time
    prior = m30_df.loc[:event_ts]
    if prior.empty:
        return False, False
    row = prior.iloc[-1]
    liq_bot = row.get("m30_liq_bot")
    liq_top = row.get("m30_liq_top")
    box_low = row.get("m30_box_low")
    box_high = row.get("m30_box_high")

    broke_support = False
    broke_resistance = False

    if pd.notna(liq_bot) and event_price < liq_bot:
        broke_support = True
    elif pd.notna(box_low) and event_price < box_low:
        broke_support = True

    if pd.notna(liq_top) and event_price > liq_top:
        broke_resistance = True
    elif pd.notna(box_high) and event_price > box_high:
        broke_resistance = True

    return broke_support, broke_resistance


def compute_outcome(prices_df, event_ts, minutes=30):
    """Price change N minutes after event."""
    future = prices_df.loc[event_ts:event_ts + pd.Timedelta(minutes=minutes)]
    if len(future) < 2:
        return np.nan
    return float(future["mid_price"].iloc[-1]) - float(future["mid_price"].iloc[0])


def main():
    print("=" * 78)
    print("  CONFIRMED DEFENSE EXIT REPLAY")
    print("  anomaly + adverse price + M30 structure")
    print("  9 months (Jul 2025 - Apr 2026)")
    print("=" * 78)

    defense = GrenadierDefenseMode()
    print("\n  Loading M30 levels...")
    m30 = load_m30_levels()
    print(f"  M30 bars: {len(m30)}")

    print("\n  Replaying microstructure (full tick resolution)...")
    df = replay_all(defense)
    print(f"  Total ticks: {len(df):,}")

    # Index for fast lookups
    prices = df[["timestamp", "mid_price"]].copy()
    prices = prices.set_index("timestamp").sort_index()

    # Get DEFENSIVE_EXIT events
    de = df[df["tier"] == "DEFENSIVE_EXIT"].copy()
    print(f"  DEFENSIVE_EXIT events: {len(de)}")

    if de.empty:
        print("  No events to analyze.")
        return

    # Precompute 30min outcome for all events
    print("  Computing 30min outcomes...")
    de["chg_30"] = de.apply(
        lambda r: compute_outcome(prices, r["timestamp"], 30), axis=1)

    # ── T1: ANOMALY ONLY (baseline) ──
    valid_t1 = de["chg_30"].dropna()
    t1_n = len(valid_t1)
    t1_prot_long = (valid_t1 < -5).sum()    # would have protected LONG
    t1_prot_short = (valid_t1 > 5).sum()     # would have protected SHORT
    t1_neutral = t1_n - t1_prot_long - t1_prot_short

    print(f"\n  === T1: ANOMALY ONLY (TIGHT-B baseline) ===")
    print(f"  Events: {t1_n}")
    print(f"  Protected LONG (dropped >5):  {t1_prot_long} ({100*t1_prot_long/t1_n:.1f}%)")
    print(f"  Protected SHORT (rose >5):    {t1_prot_short} ({100*t1_prot_short/t1_n:.1f}%)")
    print(f"  Neutral (<5pts move):         {t1_neutral} ({100*t1_neutral/t1_n:.1f}%)")
    print(f"  Mean 30min: {valid_t1.mean():+.2f}")

    # ── T2: ANOMALY + ADVERSE PRICE MOVE ──
    print(f"\n  === T2: ANOMALY + ADVERSE PRICE MOVE ===")
    print(f"  Computing adverse moves for grid...")

    # Precompute adverse moves for all lookback windows
    adverse_data = {}
    for lb in LOOKBACK_S:
        print(f"    Lookback {lb}s...", flush=True)
        moves = []
        for _, ev in de.iterrows():
            result = get_adverse_move(prices, ev["timestamp"], lb)
            moves.append(result)
        adverse_data[lb] = moves

    # Grid results
    print(f"\n  {'Adv_pts':>7s}  {'Lkbk_s':>6s}  {'Events':>7s}  {'ProtL%':>7s}  {'ProtS%':>7s}  {'Neut%':>6s}  {'Mean30':>7s}  {'Worst':>7s}")
    print(f"  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*7}")

    grid_results = []
    for adv_pts in ADVERSE_PTS:
        for lb in LOOKBACK_S:
            # Filter: only events where price moved adversely by adv_pts in lookback
            mask = []
            for i, (move_signed, drop, rise) in enumerate(adverse_data[lb]):
                # Adverse = price dropped OR rose by threshold
                # We check both directions since we don't know position direction
                had_adverse = (drop >= adv_pts or rise >= adv_pts)
                mask.append(had_adverse)

            filtered = de[mask].copy()
            chg = filtered["chg_30"].dropna()
            n = len(chg)
            if n == 0:
                grid_results.append({
                    "adv_pts": adv_pts, "lb": lb, "n": 0, "tier": "T2",
                    "prot_l": 0, "prot_s": 0, "neutral": 0, "mean": 0, "worst": 0})
                continue

            prot_l = (chg < -5).sum()
            prot_s = (chg > 5).sum()
            neut = n - prot_l - prot_s

            r = {
                "adv_pts": adv_pts, "lb": lb, "n": n, "tier": "T2",
                "prot_l": prot_l, "prot_s": prot_s, "neutral": neut,
                "mean": chg.mean(), "worst": chg.min(),
                "prot_l_pct": 100*prot_l/n, "prot_s_pct": 100*prot_s/n,
                "neut_pct": 100*neut/n,
            }
            grid_results.append(r)
            print(f"  {adv_pts:>7d}  {lb:>6d}  {n:>7d}  {r['prot_l_pct']:>6.1f}%  "
                  f"{r['prot_s_pct']:>6.1f}%  {r['neut_pct']:>5.1f}%  "
                  f"{r['mean']:>+7.2f}  {r['worst']:>+7.2f}")

    # ── T3: ANOMALY + ADVERSE MOVE + M30 LEVEL LOSS ──
    print(f"\n  === T3: ANOMALY + ADVERSE MOVE + M30 LEVEL LOSS ===")
    print(f"  Checking M30 structural levels...")

    # Precompute M30 level breaks for all events
    m30_breaks = []
    for _, ev in de.iterrows():
        bs, br = check_m30_level_lost(m30, ev["timestamp"], ev["mid_price"])
        m30_breaks.append({"broke_support": bs, "broke_resistance": br})
    de["broke_support"] = [b["broke_support"] for b in m30_breaks]
    de["broke_resistance"] = [b["broke_resistance"] for b in m30_breaks]
    de["broke_any"] = de["broke_support"] | de["broke_resistance"]

    n_broke = de["broke_any"].sum()
    print(f"  Events with M30 level broken: {n_broke}/{len(de)} ({100*n_broke/len(de):.1f}%)")
    print(f"    Broke support:    {de['broke_support'].sum()}")
    print(f"    Broke resistance: {de['broke_resistance'].sum()}")

    print(f"\n  {'Adv_pts':>7s}  {'Lkbk_s':>6s}  {'Events':>7s}  {'ProtL%':>7s}  {'ProtS%':>7s}  {'Neut%':>6s}  {'Mean30':>7s}  {'Worst':>7s}")
    print(f"  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*7}")

    t3_results = []
    for adv_pts in ADVERSE_PTS:
        for lb in LOOKBACK_S:
            mask = []
            for i, (move_signed, drop, rise) in enumerate(adverse_data[lb]):
                had_adverse = (drop >= adv_pts or rise >= adv_pts)
                mask.append(had_adverse)

            mask_series = pd.Series(mask, index=de.index)
            filtered = de[mask_series & de["broke_any"]].copy()
            chg = filtered["chg_30"].dropna()
            n = len(chg)
            if n == 0:
                continue

            prot_l = (chg < -5).sum()
            prot_s = (chg > 5).sum()
            neut = n - prot_l - prot_s

            r = {
                "adv_pts": adv_pts, "lb": lb, "n": n, "tier": "T3",
                "prot_l": prot_l, "prot_s": prot_s, "neutral": neut,
                "mean": chg.mean(), "worst": chg.min(),
                "prot_l_pct": 100*prot_l/n if n else 0,
                "prot_s_pct": 100*prot_s/n if n else 0,
                "neut_pct": 100*neut/n if n else 0,
            }
            t3_results.append(r)
            print(f"  {adv_pts:>7d}  {lb:>6d}  {n:>7d}  {r['prot_l_pct']:>6.1f}%  "
                  f"{r['prot_s_pct']:>6.1f}%  {r['neut_pct']:>5.1f}%  "
                  f"{r['mean']:>+7.2f}  {r['worst']:>+7.2f}")

    # ── SUMMARY: Best configs per tier ──
    print(f"\n  === BEST CONFIGS (lowest neutral = highest signal value) ===")

    all_results = grid_results + t3_results
    # Filter configs with at least 5 events
    viable = [r for r in all_results if r["n"] >= 5]
    if viable:
        # Sort by protection rate (prot_l + prot_s) / n, descending
        for r in viable:
            r["total_prot_pct"] = r.get("prot_l_pct", 0) + r.get("prot_s_pct", 0)

        viable.sort(key=lambda r: r["total_prot_pct"], reverse=True)

        print(f"\n  {'Tier':>4s}  {'Adv':>4s}  {'Lkbk':>5s}  {'N':>5s}  {'Prot%':>6s}  {'Neut%':>6s}  {'Mean30':>7s}")
        print(f"  {'-'*4}  {'-'*4}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*7}")
        for r in viable[:10]:
            print(f"  {r['tier']:>4s}  {r['adv_pts']:>4d}  {r['lb']:>5d}  {r['n']:>5d}  "
                  f"{r['total_prot_pct']:>5.1f}%  {r['neut_pct']:>5.1f}%  {r['mean']:>+7.2f}")

    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Defense Mode Tier Replay — Historical validation.

Replays microstructure data through GrenadierDefenseMode.check() with
the new tier classification (NORMAL / ENTRY_BLOCK / DEFENSIVE_EXIT).

Outputs:
  - Frequency of each tier
  - How many cases would have closed LONG / SHORT / EXIT_ALL
  - Price evolution 5/15/30 min after each DEFENSIVE_EXIT
  - Useful protection rate vs false alarm rate

Usage: python scripts/replay_defense_tiers.py [--days 30]
"""

import json
import sys
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Add anomaly package
sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))
from inference.anomaly_scorer import GrenadierDefenseMode

MICRO_DIR = Path("C:/data/level2/_gc_xcec")


def load_micro_files(days: int = 30) -> list[Path]:
    """Find microstructure files for the last N days."""
    files = sorted(MICRO_DIR.glob("microstructure_*.csv.gz"))
    if days > 0:
        files = files[-days:]
    return files


def replay_file(defense: GrenadierDefenseMode, path: Path) -> pd.DataFrame:
    """Replay one microstructure file through defense mode."""
    try:
        cols_needed = ["timestamp", "mid_price", "spread",
                       "total_bid_size", "total_ask_size", "bar_delta"]
        all_cols = pd.read_csv(path, nrows=0).columns.tolist()
        cols_use = [c for c in cols_needed if c in all_cols]

        df = pd.read_csv(path, usecols=cols_use)
        if df.empty:
            return pd.DataFrame()

        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        # Compute book_imbalance
        bid = df.get("total_bid_size", pd.Series(dtype=float)).fillna(0)
        ask = df.get("total_ask_size", pd.Series(dtype=float)).fillna(0)
        df["book_imbalance"] = (bid - ask) / (bid + ask + 1e-10)

        results = []
        for i in range(0, len(df), 10):  # sample every 10 rows (~10s)
            row = df.iloc[i]
            spread = float(row.get("spread", 0) or 0)
            bid_d = float(row.get("total_bid_size", 0) or 0)
            ask_d = float(row.get("total_ask_size", 0) or 0)
            imb = float(row.get("book_imbalance", 0) or 0)

            if spread <= 0 or bid_d <= 0 or ask_d <= 0:
                continue

            result = defense.check(
                spread=spread,
                total_bid_depth=bid_d,
                total_ask_depth=ask_d,
                book_imbalance=imb,
            )

            results.append({
                "timestamp": row["timestamp"],
                "mid_price": float(row.get("mid_price", 0) or 0),
                "tier": result["defense_tier"],
                "n_triggers": result["n_triggers"],
                "any_extreme": result["any_extreme"],
                "reason": result["trigger_reason"],
                "z_spread": result["z_spread"],
                "z_bid": result["z_bid_depth"],
                "z_ask": result["z_ask_depth"],
                "z_imb": result["z_imbalance"],
            })

        return pd.DataFrame(results)
    except Exception as e:
        print(f"  ERROR processing {path.name}: {e}")
        return pd.DataFrame()


def compute_price_evolution(df_full: pd.DataFrame, events: pd.DataFrame,
                            windows_min: list[int] = [5, 15, 30]) -> pd.DataFrame:
    """
    For each DEFENSIVE_EXIT event, compute price change at 5/15/30 min.
    df_full: all ticks with timestamp + mid_price (from replay)
    events: DEFENSIVE_EXIT rows only
    """
    if events.empty or df_full.empty:
        return pd.DataFrame()

    df_full = df_full.set_index("timestamp").sort_index()
    results = []

    for _, ev in events.iterrows():
        t0 = ev["timestamp"]
        p0 = ev["mid_price"]
        if p0 <= 0:
            continue

        row = {"timestamp": t0, "price_at_event": p0, "reason": ev["reason"]}
        for w in windows_min:
            t_end = t0 + pd.Timedelta(minutes=w)
            future = df_full.loc[t0:t_end]
            if len(future) > 1:
                p_end = float(future["mid_price"].iloc[-1])
                row[f"chg_{w}min"] = round(p_end - p0, 2)
                row[f"price_{w}min"] = round(p_end, 2)
                row[f"max_adverse_{w}min"] = round(
                    float(future["mid_price"].min()) - p0, 2)
            else:
                row[f"chg_{w}min"] = np.nan
                row[f"price_{w}min"] = np.nan
                row[f"max_adverse_{w}min"] = np.nan
        results.append(row)

    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    print("=" * 70)
    print("  DEFENSE MODE TIER REPLAY — Historical Validation")
    print(f"  Days: {args.days}")
    print("=" * 70)

    defense = GrenadierDefenseMode()
    files = load_micro_files(args.days)
    print(f"\n  Found {len(files)} microstructure files")

    all_results = []
    for f in files:
        print(f"  Processing {f.name}...", end="", flush=True)
        df = replay_file(defense, f)
        if not df.empty:
            all_results.append(df)
            tier_counts = Counter(df["tier"])
            print(f" {len(df)} ticks | "
                  f"N={tier_counts.get('NORMAL', 0)} "
                  f"EB={tier_counts.get('ENTRY_BLOCK', 0)} "
                  f"DE={tier_counts.get('DEFENSIVE_EXIT', 0)}")
        else:
            print(" (empty/error)")

    if not all_results:
        print("\n  No data to analyze.")
        return

    combined = pd.concat(all_results, ignore_index=True)
    total = len(combined)

    # 1. Tier frequency
    tier_counts = Counter(combined["tier"])
    print(f"\n  1. TIER FREQUENCY (total samples: {total})")
    print(f"     {'Tier':<20s}  {'Count':>8s}  {'Pct':>6s}")
    print(f"     {'-'*20}  {'-'*8}  {'-'*6}")
    for tier in ["NORMAL", "ENTRY_BLOCK", "DEFENSIVE_EXIT"]:
        cnt = tier_counts.get(tier, 0)
        pct = 100 * cnt / total if total else 0
        print(f"     {tier:<20s}  {cnt:>8d}  {pct:>5.2f}%")

    # 2. DEFENSIVE_EXIT events detail
    de_events = combined[combined["tier"] == "DEFENSIVE_EXIT"].copy()
    print(f"\n  2. DEFENSIVE_EXIT EVENTS: {len(de_events)} total")

    if not de_events.empty:
        # Group by session (date)
        de_events["date"] = de_events["timestamp"].dt.date
        by_date = de_events.groupby("date").size()
        print(f"     Across {len(by_date)} trading days")
        print(f"     Mean per day: {by_date.mean():.1f}")
        print(f"     Max in one day: {by_date.max()}")

        # Reason breakdown
        reason_counts = Counter(de_events["reason"])
        print(f"\n     Trigger reasons:")
        for reason, cnt in reason_counts.most_common(10):
            print(f"       {cnt:>5d}x  {reason}")

        # n_triggers distribution
        print(f"\n     n_triggers distribution:")
        for nt in sorted(de_events["n_triggers"].unique()):
            cnt = (de_events["n_triggers"] == nt).sum()
            print(f"       {nt} triggers: {cnt} events")

        # Extreme vs multi-trigger
        extreme = de_events["any_extreme"].sum()
        multi = (de_events["n_triggers"] >= 2).sum()
        both = ((de_events["any_extreme"]) & (de_events["n_triggers"] >= 2)).sum()
        print(f"\n     Extreme z-score only: {extreme - both}")
        print(f"     Multi-trigger only:   {multi - both}")
        print(f"     Both:                 {both}")

    # 3. Price evolution after DEFENSIVE_EXIT
    if not de_events.empty:
        print(f"\n  3. PRICE EVOLUTION AFTER DEFENSIVE_EXIT")
        price_evo = compute_price_evolution(combined, de_events)
        if not price_evo.empty:
            for w in [5, 15, 30]:
                col = f"chg_{w}min"
                adv_col = f"max_adverse_{w}min"
                valid = price_evo[col].dropna()
                if valid.empty:
                    continue
                print(f"\n     After {w} min (n={len(valid)}):")
                print(f"       Mean price change   : {valid.mean():+.2f} pts")
                print(f"       Median              : {valid.median():+.2f} pts")
                print(f"       Max adverse (worst) : {price_evo[adv_col].dropna().min():+.2f} pts")
                print(f"       Moves >10pts down   : {(valid < -10).sum()}")
                print(f"       Moves >10pts up     : {(valid > 10).sum()}")
                print(f"       Moves >20pts down   : {(valid < -20).sum()}")

            # 4. Protection rate
            print(f"\n  4. PROTECTION ANALYSIS (would tier 2 have helped?)")
            # Useful protection: price moved >1.5*ATR_M30 (~17pts) against within 30min
            PROTECTION_THR = 15.0  # pts
            chg30 = price_evo["chg_30min"].dropna()
            big_moves_down = (chg30 < -PROTECTION_THR).sum()
            big_moves_up = (chg30 > PROTECTION_THR).sum()
            false_alarms = ((chg30 > -5) & (chg30 < 5)).sum()

            print(f"     Large adverse moves (>{PROTECTION_THR}pts down in 30min): {big_moves_down}")
            print(f"     Large positive moves (>{PROTECTION_THR}pts up in 30min): {big_moves_up}")
            print(f"     False alarms (price <5pts change in 30min): {false_alarms}")
            if len(chg30) > 0:
                useful_rate = 100 * big_moves_down / len(chg30)
                false_rate = 100 * false_alarms / len(chg30)
                print(f"     Useful protection rate: {useful_rate:.1f}%")
                print(f"     False alarm rate:       {false_rate:.1f}%")

            # Impact per direction
            print(f"\n  5. DIRECTIONAL IMPACT")
            print(f"     (Assumes LONG open: adverse = price drops)")
            long_adverse = (chg30 < -PROTECTION_THR).sum()
            print(f"     Would have protected LONG: {long_adverse} times")
            print(f"     (Assumes SHORT open: adverse = price rises)")
            short_adverse = (chg30 > PROTECTION_THR).sum()
            print(f"     Would have protected SHORT: {short_adverse} times")
            total_exits = len(chg30)
            neutral = total_exits - long_adverse - short_adverse
            print(f"     EXIT_ALL (total closures): {total_exits}")
            print(f"     Neither direction adverse: {neutral}")

            # Examples
            print(f"\n  6. EXAMPLES (top 5 largest adverse moves after DEFENSIVE_EXIT)")
            worst = price_evo.nsmallest(5, "chg_30min")
            for _, r in worst.iterrows():
                print(f"     {r['timestamp']}  price={r['price_at_event']:.2f}"
                      f"  30min_chg={r.get('chg_30min', 0):+.1f}"
                      f"  reason={r['reason'][:60]}")
        else:
            print("     Could not compute price evolution.")
    else:
        print("\n  No DEFENSIVE_EXIT events in historical data.")

    print()
    print("=" * 70)


if __name__ == "__main__":
    main()

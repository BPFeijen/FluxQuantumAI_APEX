#!/usr/bin/env python3
"""
Directional Defense Replay -- 9 months, TIGHT-B + stress_direction.

Classifies each DEFENSIVE_EXIT as EXIT_LONG / EXIT_SHORT / EXIT_ALL
and correlates with actual price movement to measure directional accuracy.

Usage: python scripts/replay_directional_defense.py
"""

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))
from inference.anomaly_scorer import GrenadierDefenseMode

MICRO_DIR = Path("C:/data/level2/_gc_xcec")


def replay_all(defense):
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

            for j in range(0, len(df), 10):
                r = df.iloc[j]
                sp = float(r.get("spread", 0) or 0)
                bd = float(r.get("total_bid_size", 0) or 0)
                ad = float(r.get("total_ask_size", 0) or 0)
                im = float(r.get("book_imbalance", 0) or 0)
                if sp <= 0 or bd <= 0 or ad <= 0:
                    continue
                res = defense.check(spread=sp, total_bid_depth=bd,
                                    total_ask_depth=ad, book_imbalance=im)
                rows.append({
                    "timestamp": r["timestamp"],
                    "mid_price": float(r.get("mid_price", 0) or 0),
                    "tier": res["defense_tier"],
                    "stress": res["stress_direction"],
                    "reason": res["trigger_reason"],
                    "n_trig": res["n_triggers"],
                    "z_spread": res["z_spread"],
                    "z_bid": res["z_bid_depth"],
                    "z_ask": res["z_ask_depth"],
                    "z_imb": res["z_imbalance"],
                })
        except Exception:
            pass
    return pd.DataFrame(rows)


def price_change(df_all, events, minutes):
    """Compute price change N minutes after each event."""
    idx = df_all.set_index("timestamp").sort_index()
    changes = []
    for _, ev in events.iterrows():
        t0, p0 = ev["timestamp"], ev["mid_price"]
        if p0 <= 0:
            changes.append(np.nan)
            continue
        future = idx.loc[t0:t0 + pd.Timedelta(minutes=minutes)]
        if len(future) > 1:
            changes.append(float(future["mid_price"].iloc[-1]) - p0)
        else:
            changes.append(np.nan)
    return pd.Series(changes, index=events.index)


def main():
    print("=" * 70)
    print("  DIRECTIONAL DEFENSE REPLAY -- TIGHT-B + stress_direction")
    print("  9 months (Jul 2025 - Apr 2026)")
    print("=" * 70)

    defense = GrenadierDefenseMode()
    df = replay_all(defense)
    print(f"\n  Total samples: {len(df):,}")

    # 1. Tier counts
    tier_c = Counter(df["tier"])
    print(f"\n  1. TIER COUNTS")
    for t in ["NORMAL", "ENTRY_BLOCK", "DEFENSIVE_EXIT"]:
        print(f"     {t:<20s}: {tier_c.get(t, 0):>7,}  ({100*tier_c.get(t,0)/len(df):.3f}%)")

    # 2. DEFENSIVE_EXIT directional breakdown
    de = df[df["tier"] == "DEFENSIVE_EXIT"].copy()
    print(f"\n  2. DEFENSIVE_EXIT DIRECTIONAL BREAKDOWN ({len(de)} events)")
    stress_c = Counter(de["stress"])
    for s in ["EXIT_LONG", "EXIT_SHORT", "EXIT_ALL", "HOLD"]:
        cnt = stress_c.get(s, 0)
        pct = 100 * cnt / len(de) if len(de) else 0
        print(f"     {s:<15s}: {cnt:>6d}  ({pct:.1f}%)")

    # 3. Price evolution per direction
    if len(de) > 0:
        de["chg_5"] = price_change(df, de, 5)
        de["chg_15"] = price_change(df, de, 15)
        de["chg_30"] = price_change(df, de, 30)

        print(f"\n  3. PRICE CHANGE AFTER DEFENSIVE_EXIT (30min)")
        for stress_type in ["EXIT_LONG", "EXIT_SHORT", "EXIT_ALL"]:
            subset = de[de["stress"] == stress_type]
            chg = subset["chg_30"].dropna()
            if len(chg) == 0:
                print(f"\n     [{stress_type}]: no events")
                continue

            # For EXIT_LONG: protection = price dropped (chg < 0)
            # For EXIT_SHORT: protection = price rose (chg > 0)
            if stress_type == "EXIT_LONG":
                correct = (chg < -5).sum()
                wrong = (chg > 5).sum()
                big_correct = (chg < -15).sum()
            elif stress_type == "EXIT_SHORT":
                correct = (chg > 5).sum()
                wrong = (chg < -5).sum()
                big_correct = (chg > 15).sum()
            else:  # EXIT_ALL
                correct = ((chg < -5) | (chg > 5)).sum()
                wrong = 0  # can't be wrong for EXIT_ALL
                big_correct = ((chg < -15) | (chg > 15)).sum()

            neutral = len(chg) - correct - wrong
            acc = 100 * correct / len(chg) if len(chg) else 0
            false_rate = 100 * wrong / len(chg) if len(chg) else 0

            print(f"\n     [{stress_type}] ({len(chg)} events)")
            print(f"       Mean 30min chg  : {chg.mean():+.2f} pts")
            print(f"       Median          : {chg.median():+.2f} pts")
            print(f"       Worst           : {chg.min():+.2f} pts")
            print(f"       Best            : {chg.max():+.2f} pts")
            print(f"       Correct (>5pts) : {correct} ({acc:.1f}%)")
            print(f"       Big correct(>15): {big_correct}")
            print(f"       Wrong (>5pts)   : {wrong} ({false_rate:.1f}%)")
            print(f"       Neutral (<5pts) : {neutral}")

        # 4. Summary comparison: directional vs EXIT_ALL
        print(f"\n  4. DIRECTIONAL vs EXIT_ALL COMPARISON")

        # If we used directional: only close matching positions
        dir_events = de[de["stress"].isin(["EXIT_LONG", "EXIT_SHORT"])]
        all_events = de[de["stress"] == "EXIT_ALL"]

        dir_chg30 = dir_events["chg_30"].dropna()
        all_chg30 = all_events["chg_30"].dropna()

        # Directional accuracy
        dir_correct = 0
        for _, r in dir_events.iterrows():
            c = r["chg_30"]
            if pd.isna(c):
                continue
            if r["stress"] == "EXIT_LONG" and c < -5:
                dir_correct += 1
            elif r["stress"] == "EXIT_SHORT" and c > 5:
                dir_correct += 1

        dir_total = dir_chg30.notna().sum()
        print(f"     Directional events: {len(dir_events)}")
        print(f"     Directional correct: {dir_correct}/{dir_total} "
              f"({100*dir_correct/dir_total:.1f}%)" if dir_total else "")
        print(f"     EXIT_ALL events: {len(all_events)}")
        print(f"     EXIT_ALL with >15pts move: "
              f"{((all_chg30.abs() > 15).sum() if len(all_chg30) else 0)}")

        # 5. Examples
        print(f"\n  5. TOP 5 DIRECTIONAL EXIT_LONG (biggest drops after signal)")
        el = de[de["stress"] == "EXIT_LONG"].nsmallest(5, "chg_30")
        for _, r in el.iterrows():
            print(f"     {r['timestamp']}  price={r['mid_price']:.2f}  "
                  f"30min={r['chg_30']:+.1f}  reason={r['reason'][:50]}")

        print(f"\n     TOP 5 DIRECTIONAL EXIT_SHORT (biggest spikes after signal)")
        es = de[de["stress"] == "EXIT_SHORT"].nlargest(5, "chg_30")
        for _, r in es.iterrows():
            print(f"     {r['timestamp']}  price={r['mid_price']:.2f}  "
                  f"30min={r['chg_30']:+.1f}  reason={r['reason'][:50]}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

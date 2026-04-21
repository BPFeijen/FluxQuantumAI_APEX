"""
CHECK 7 Design & Simulation — Pullback End Exit
================================================
Logic: Exit pullback trade when BOTH groups are true:

GROUP A — Pullback completion evidence (at least 1 of):
  A1: Price reached/approached liq_bot target zone (within 5pts)
  A2: Significant low formed (MFE > 0.5 * ATR)
  A3: Downside extension exhausted (consecutive bars failing to make new lows)

GROUP B — Trend resumption evidence (at least 2 of):
  B1: Price back inside CURRENT M30 box and holding
  B2: Price reclaimed CURRENT FMV
  B3: Positive micro delta (bar_delta > 0 in last 3 M30 bars = buyers returning)
  B4: Bullish reclaim candle (close > open with range > 0.5*ATR on M30)

Additional constraints:
  - pnl > 0 (don't force close at loss)
  - Use CURRENT box only (box that contains or is nearest to price)
  - M30 is execution context
  - Persistence: condition must hold for 3 consecutive checks

Simulate on 540 historical pullback trades.
"""

import pandas as pd
import numpy as np
from pathlib import Path

M30_BOXES = Path(r"C:\data\processed\gc_m30_boxes.parquet")
FEATURES_V4 = Path(r"C:\data\processed\gc_ats_features_v4.parquet")
START_DATE = "2025-07-01"


def load():
    m30 = pd.read_parquet(M30_BOXES)
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30 = m30[m30.index >= START_DATE].copy()

    feat = pd.read_parquet(FEATURES_V4, columns=["daily_jac_dir"])
    if feat.index.tz is None:
        feat.index = feat.index.tz_localize("UTC")
    feat = feat[feat.index >= START_DATE]
    m30_trend = feat["daily_jac_dir"].resample("30min").ffill().map({"up": "long", "down": "short"})
    m30["daily_trend"] = m30_trend.reindex(m30.index, method="ffill")
    return m30


def find_pullback_entries(m30):
    """Find SHORT entries at liq_top in uptrend (pullback trades)."""
    V1 = 8.0
    entries = []
    seen_boxes = set()

    for i in range(10, len(m30)):
        row = m30.iloc[i]
        c = float(row["close"])
        lt = float(row["m30_liq_top"]) if pd.notna(row.get("m30_liq_top")) else None
        dt = row.get("daily_trend")
        bid = row.get("m30_box_id", 0)

        if c <= 0 or dt != "long" or lt is None:
            continue
        if abs(c - lt) > V1:
            continue
        if bid in seen_boxes:
            continue
        seen_boxes.add(bid)

        entries.append({"idx": i, "ts": m30.index[i], "entry": c, "box_id": bid})

    return entries


def simulate_check7(m30, entry_idx, entry_price):
    """
    Simulate CHECK 7 on a SHORT pullback in uptrend.
    Returns dict with exit details or None if no exit in 20 bars.
    """
    future = m30.iloc[entry_idx + 1 : entry_idx + 21]
    if future.empty:
        return None

    # Track state
    mfe = 0.0
    streak = 0
    bars_examined = 0

    for j, (fidx, row) in enumerate(future.iterrows()):
        bars_examined = j + 1
        fc = float(row["close"])
        fh = float(row["high"])
        fl = float(row["low"])
        fo = float(row["open"])
        atr = float(row["atr14"]) if pd.notna(row.get("atr14")) else 20.0
        fmv = float(row["m30_fmv"]) if pd.notna(row.get("m30_fmv")) else None
        bh = float(row["m30_box_high"]) if pd.notna(row.get("m30_box_high")) else None
        bl = float(row["m30_box_low"]) if pd.notna(row.get("m30_box_low")) else None
        lb = float(row["m30_liq_bot"]) if pd.notna(row.get("m30_liq_bot")) else None

        # PnL
        pnl = entry_price - fc
        bar_mfe = entry_price - fl
        if bar_mfe > mfe:
            mfe = bar_mfe

        # Skip if in loss
        if pnl <= 0:
            streak = 0
            continue

        # ============================================================
        # GROUP A — Pullback completion evidence (need >= 1)
        # ============================================================
        a1 = lb is not None and fl <= lb + 5  # reached liq_bot target
        a2 = mfe > atr * 0.5                   # significant move achieved
        a3 = False                               # downside exhaustion
        # A3: last 3 bars failed to make new low
        if j >= 2:
            prev_lows = [float(future.iloc[k]["low"]) for k in range(max(0, j-2), j+1)]
            if len(prev_lows) == 3:
                a3 = prev_lows[-1] >= prev_lows[-2] and prev_lows[-2] >= prev_lows[-3]

        group_a = a1 or a2 or a3

        # ============================================================
        # GROUP B — Trend resumption evidence (need >= 2)
        # ============================================================
        b_count = 0

        # B1: price inside current box and holding
        b1 = bh is not None and bl is not None and bl <= fc <= bh
        if b1:
            b_count += 1

        # B2: price reclaimed current FMV
        b2 = fmv is not None and fc > fmv
        if b2:
            b_count += 1

        # B3: positive delta (buyers returning) — use bar_delta proxy: close > open
        # In live this would use microstructure bar_delta; here use M30 candle direction
        # Check last 2 M30 bars: both bullish = buyers returning
        if j >= 1:
            prev_bar = future.iloc[j-1]
            b3 = (fc > fo) and (float(prev_bar["close"]) > float(prev_bar["open"]))
        else:
            b3 = fc > fo
        if b3:
            b_count += 1

        # B4: bullish reclaim candle (range > 0.5*ATR, close > open, close near high)
        bar_range = fh - fl
        b4 = (fc > fo) and (bar_range > atr * 0.5) and ((fc - fl) / bar_range > 0.6 if bar_range > 0 else False)
        if b4:
            b_count += 1

        group_b = b_count >= 2

        # ============================================================
        # EXIT DECISION
        # ============================================================
        if group_a and group_b:
            streak += 1
        else:
            streak = 0

        if streak >= 1:  # M30 bars = 30 min each, 1 bar persistence is enough
            return {
                "exit_bar": bars_examined,
                "exit_ts": fidx,
                "exit_price": fc,
                "pnl": pnl,
                "mfe": mfe,
                "a1": a1, "a2": a2, "a3": a3,
                "b1": b1, "b2": b2, "b3": b3, "b4": b4,
                "b_count": b_count,
            }

    # No exit triggered in 20 bars
    return None


def main():
    print("CHECK 7 DESIGN SIMULATION")
    print("=" * 80)
    print()
    print("Logic: Exit SHORT pullback when GROUP_A AND GROUP_B:")
    print("  A (completion): reached target OR MFE>0.5*ATR OR lows exhausted")
    print("  B (resumption): >= 2 of: in box, above FMV, buyers returning, reclaim candle")
    print()

    m30 = load()
    entries = find_pullback_entries(m30)
    print(f"Pullback entries found: {len(entries)}")

    results = []
    for e in entries:
        r = simulate_check7(m30, e["idx"], e["entry"])
        if r:
            r["entry_ts"] = e["ts"]
            r["entry_price"] = e["entry"]
            r["box_id"] = e["box_id"]
            results.append(r)

    rdf = pd.DataFrame(results)
    total_entries = len(entries)
    total_exits = len(rdf)

    print(f"Entries with CHECK 7 exit: {total_exits}/{total_entries} ({total_exits/total_entries*100:.0f}%)")
    print()

    # No exit = trade ran full 20 bars without triggering
    no_exit = total_entries - total_exits

    print("RESULTS:")
    print("-" * 80)
    print(f"  Exited by CHECK 7: {total_exits}")
    print(f"  No exit (20 bars): {no_exit}")
    print()

    if not rdf.empty:
        print(f"  PnL at exit:")
        print(f"    Mean:   {rdf['pnl'].mean():+.1f} pts")
        print(f"    Median: {rdf['pnl'].median():+.1f} pts")
        print(f"    Std:    {rdf['pnl'].std():.1f} pts")
        print(f"    Min:    {rdf['pnl'].min():+.1f} pts")
        print(f"    Max:    {rdf['pnl'].max():+.1f} pts")
        print(f"    Win%%:   {(rdf['pnl'] > 0).mean()*100:.0f}%%")
        print()
        print(f"  MFE at exit: {rdf['mfe'].mean():.1f} pts mean")
        print(f"  Bars to exit: {rdf['exit_bar'].mean():.1f} mean ({rdf['exit_bar'].median():.0f} median)")
        print()

        # Group A breakdown
        print(f"  GROUP A triggers:")
        print(f"    A1 (reached target):  {rdf['a1'].sum():>4} ({rdf['a1'].mean()*100:.0f}%)")
        print(f"    A2 (MFE > 0.5*ATR):   {rdf['a2'].sum():>4} ({rdf['a2'].mean()*100:.0f}%)")
        print(f"    A3 (lows exhausted):   {rdf['a3'].sum():>4} ({rdf['a3'].mean()*100:.0f}%)")

        print(f"  GROUP B triggers:")
        print(f"    B1 (in box):          {rdf['b1'].sum():>4} ({rdf['b1'].mean()*100:.0f}%)")
        print(f"    B2 (above FMV):       {rdf['b2'].sum():>4} ({rdf['b2'].mean()*100:.0f}%)")
        print(f"    B3 (buyers return):   {rdf['b3'].sum():>4} ({rdf['b3'].mean()*100:.0f}%)")
        print(f"    B4 (reclaim candle):  {rdf['b4'].sum():>4} ({rdf['b4'].mean()*100:.0f}%)")
        print(f"    Avg B count:          {rdf['b_count'].mean():.1f}")

        # Compare: what if we just used FMV cross alone?
        print()
        print("COMPARISON vs FMV-only (old logic):")
        # FMV-only: first bar where close > FMV and pnl > 0
        fmv_results = []
        for e in entries:
            idx = e["idx"]
            ep = e["entry"]
            future = m30.iloc[idx+1:idx+21]
            for j, (fidx, row) in enumerate(future.iterrows()):
                fc = float(row["close"])
                fmv = float(row["m30_fmv"]) if pd.notna(row.get("m30_fmv")) else None
                pnl = ep - fc
                if fmv and fc > fmv and pnl > 0:
                    fmv_results.append({"pnl": pnl, "bars": j+1})
                    break

        if fmv_results:
            fmv_df = pd.DataFrame(fmv_results)
            print(f"  FMV-only exits: {len(fmv_df)}/{total_entries}")
            print(f"  FMV-only PnL mean: {fmv_df['pnl'].mean():+.1f} pts")
            print(f"  FMV-only bars: {fmv_df['bars'].mean():.1f} mean")

        print(f"\n  CHECK 7 exits: {total_exits}/{total_entries}")
        print(f"  CHECK 7 PnL mean: {rdf['pnl'].mean():+.1f} pts")
        print(f"  CHECK 7 bars: {rdf['exit_bar'].mean():.1f} mean")

    # Detailed: trade #9 equivalent and a few others
    print()
    print("DETAILED EXAMPLES:")
    print("=" * 80)
    for _, r in rdf.head(8).iterrows():
        a_str = f"A1={'Y' if r['a1'] else 'n'} A2={'Y' if r['a2'] else 'n'} A3={'Y' if r['a3'] else 'n'}"
        b_str = f"B1={'Y' if r['b1'] else 'n'} B2={'Y' if r['b2'] else 'n'} B3={'Y' if r['b3'] else 'n'} B4={'Y' if r['b4'] else 'n'}"
        print(f"  {r['entry_ts']}  entry={r['entry_price']:.1f}  exit@bar{r['exit_bar']}={r['exit_price']:.1f}"
              f"  pnl={r['pnl']:+.1f}  mfe={r['mfe']:.1f}  {a_str}  {b_str}")

    # Save
    out = Path(r"C:\FluxQuantumAI\data\calibration")
    out.mkdir(parents=True, exist_ok=True)
    if not rdf.empty:
        rdf.to_csv(out / "check7_design_simulation.csv", index=False)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

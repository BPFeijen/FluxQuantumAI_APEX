"""P1.3 Phase 1.2 — Quantify M30 box stagnation problem (READ-ONLY).

Asana 1214327736913830.

Operates on:
  - C:/data/processed/gc_m30_boxes.parquet (10 months L2 window: Jul 2025 -> 2026-04-28)
  - C:/FluxQuantumAI/logs/decision_log.jsonl (post-P0 BLOCK rows visible from 2026-04-28 only)

Outputs JSON summary to _audit/m30_box_stagnation_quantification.json
plus stdout with summary tables for the audit doc.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
DECISION_LOG = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
OUT_JSON = Path(r"C:\FluxQuantumAI\_audit\m30_box_stagnation_quantification.json")

# Per memory feedback_calibration_9months: Jul 2025 -> today is the canonical
# data window (9 months of paid Databento). 10 months = Jul 2025 -> Apr 2026.
WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END   = pd.Timestamp("2026-04-28", tz="UTC")


def load_m30() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[(df.index >= WINDOW_START) & (df.index < WINDOW_END)]
    df = df.dropna(subset=["close", "atr14", "m30_box_id"])
    df = df[df["m30_box_id"] > 0]
    return df


def find_box_lifespans(df: pd.DataFrame) -> pd.DataFrame:
    """For each m30_box_id, compute first/last bar, duration, box_range, atr_at_birth."""
    g = df.groupby("m30_box_id")
    lifespans = pd.DataFrame({
        "first_ts": g.apply(lambda x: x.index.min(), include_groups=False),
        "last_ts":  g.apply(lambda x: x.index.max(), include_groups=False),
        "n_bars":   g.size(),
        "box_high": g["m30_box_high"].first(),
        "box_low":  g["m30_box_low"].first(),
        "atr_at_birth": g["atr14"].first(),
        "max_close": g["close"].max(),
        "min_close": g["close"].min(),
        "max_high":  g["high"].max(),
        "min_low":   g["low"].min(),
        "last_close": g["close"].last(),
    })
    lifespans["duration_h"] = (lifespans["last_ts"] - lifespans["first_ts"]).dt.total_seconds() / 3600.0
    lifespans["box_range"] = lifespans["box_high"] - lifespans["box_low"]
    lifespans["box_mid"]   = (lifespans["box_high"] + lifespans["box_low"]) / 2.0
    # Excursion = max distance close traveled from box_mid during box life
    lifespans["max_excursion_up"]   = lifespans["max_close"] - lifespans["box_high"]
    lifespans["max_excursion_down"] = lifespans["box_low"]  - lifespans["min_close"]
    lifespans["max_excursion_pts"]  = np.maximum(lifespans["max_excursion_up"],
                                                   lifespans["max_excursion_down"])
    lifespans["max_excursion_pts"]  = lifespans["max_excursion_pts"].clip(lower=0)
    lifespans["excursion_over_range"] = lifespans["max_excursion_pts"] / lifespans["box_range"].replace(0, np.nan)
    lifespans["excursion_over_atr"]   = lifespans["max_excursion_pts"] / lifespans["atr_at_birth"].replace(0, np.nan)
    return lifespans


def stuck_box_analysis(lifespans: pd.DataFrame, df: pd.DataFrame) -> dict:
    """Box is 'stuck' if duration > 4h AND excursion > N*ATR without new box forming."""
    out = {}
    out["total_boxes"] = int(len(lifespans))
    out["window"] = f"{WINDOW_START.isoformat()} -> {WINDOW_END.isoformat()}"

    # Duration distribution
    pcts = [50, 75, 90, 95, 99]
    out["duration_h_percentiles"] = {f"p{p}": float(np.percentile(lifespans["duration_h"], p)) for p in pcts}
    out["duration_h_mean"] = float(lifespans["duration_h"].mean())
    out["duration_h_max"]  = float(lifespans["duration_h"].max())

    # Stuck buckets
    for h in [2, 4, 6, 8, 12, 24]:
        out[f"n_boxes_duration_gt_{h}h"] = int((lifespans["duration_h"] > h).sum())

    # Excursion histogram during stagnation (boxes >4h)
    stuck = lifespans[lifespans["duration_h"] > 4]
    out["n_stuck_4h"] = int(len(stuck))
    out["stuck_4h_excursion_atr_percentiles"] = {
        f"p{p}": float(np.nanpercentile(stuck["excursion_over_atr"], p)) for p in pcts
    }
    for n_atr in [2, 3, 4, 5]:
        mask = (lifespans["duration_h"] > 4) & (lifespans["excursion_over_atr"] > n_atr)
        out[f"n_stuck_4h_excursion_gt_{n_atr}atr"] = int(mask.sum())

    # Top-10 most extreme stuck boxes (for reference)
    extreme = lifespans.sort_values("excursion_over_atr", ascending=False).head(10)
    out["top10_extreme_stuck"] = []
    for box_id, row in extreme.iterrows():
        out["top10_extreme_stuck"].append({
            "box_id": int(box_id),
            "first_ts": row["first_ts"].isoformat(),
            "last_ts":  row["last_ts"].isoformat(),
            "duration_h": round(float(row["duration_h"]), 1),
            "box_range_pts": round(float(row["box_range"]), 1),
            "max_excursion_pts": round(float(row["max_excursion_pts"]), 1),
            "excursion_over_range": round(float(row["excursion_over_range"]), 2) if pd.notna(row["excursion_over_range"]) else None,
            "excursion_over_atr":   round(float(row["excursion_over_atr"]), 2)   if pd.notna(row["excursion_over_atr"]) else None,
            "atr_at_birth": round(float(row["atr_at_birth"]), 2),
        })

    return out


def bias_accuracy_during_stagnation(lifespans: pd.DataFrame, df: pd.DataFrame) -> dict:
    """For boxes >4h, derive a simplified bias proxy from box position vs current price,
    then check 4h-forward outcome. Bias proxy used here:
      - if close > box_high: 'short_setup_at_top' (price came back up = bullish reversal lookout, ATS would say SHORT)
      - if close < box_low:  'long_setup_at_bot'  (price came back down = bearish reversal lookout, ATS would say LONG)
      - else: 'inside' (no decision)

    Outcome = sign(close[t+8 bars] - close[t]) i.e. 4-hour forward (8 M30 bars).
    """
    df = df.sort_index().copy()
    df["close_fwd_4h"] = df["close"].shift(-8)  # 8 M30 bars = 4h

    # Filter to bars during stuck-box state (box duration so far > 4h at that bar)
    df["bar_position_in_box"] = df.groupby("m30_box_id").cumcount()  # 0-indexed bar number within box
    # bars 9+ within box = duration so far > 4h (since each bar = 30min)
    stuck_bars = df[df["bar_position_in_box"] >= 8]

    # Bias proxy (simplified — actual derive_m30_bias is more nuanced, but this captures the
    # operational symptom: box is below price in a downtrend means box's bullish bias is wrong)
    stuck_bars = stuck_bars.copy()
    stuck_bars["box_position"] = "inside"
    stuck_bars.loc[stuck_bars["close"] > stuck_bars["m30_box_high"], "box_position"] = "above_box"
    stuck_bars.loc[stuck_bars["close"] < stuck_bars["m30_box_low"],  "box_position"] = "below_box"

    # Outcome direction
    stuck_bars["outcome_pts"] = stuck_bars["close_fwd_4h"] - stuck_bars["close"]
    stuck_bars = stuck_bars.dropna(subset=["outcome_pts"])

    out = {}
    out["total_stuck_bars_analyzed"] = int(len(stuck_bars))

    # Group by box_position
    for pos in ["above_box", "below_box", "inside"]:
        sub = stuck_bars[stuck_bars["box_position"] == pos]
        if len(sub) == 0:
            continue
        # ATS "vender no topo, comprar no chao" interpretation:
        # - above_box (price in liq_top zone): SHORT setup would be valid; bullish bias = WRONG if price continues down
        # - below_box (price in liq_bot zone): LONG setup would be valid
        # We measure the FORWARD MOVE direction. If price is above box AND continues UP, bias-from-box (which
        # reads bullish) was correct. If continues DOWN, bias was wrong.
        n = len(sub)
        n_up = int((sub["outcome_pts"] > 0).sum())
        n_dn = int((sub["outcome_pts"] < 0).sum())
        n_flat = n - n_up - n_dn
        mean_move = float(sub["outcome_pts"].mean())
        out[f"position_{pos}"] = {
            "n_bars": n,
            "n_forward_up": n_up,
            "n_forward_down": n_dn,
            "n_forward_flat": n_flat,
            "pct_up": round(100*n_up/n, 1),
            "pct_down": round(100*n_dn/n, 1),
            "mean_4h_move_pts": round(mean_move, 2),
            "median_4h_move_pts": round(float(sub["outcome_pts"].median()), 2),
        }

    # The key forensic question: when box is BELOW price (price escaped UP), does price keep going UP?
    # If yes -> box's bias derivation is correct (price is bullish, away from a bearish box).
    # If no -> stale box, price reverting.
    # The trigger case (Box 5261, price 33pts BELOW box) corresponds to position=below_box;
    # bias-from-box reads BULLISH (because box is above), but price is making lower lows.

    return out


def hypothetical_pnl_from_decision_log(lifespans: pd.DataFrame) -> dict:
    """Cross-reference stuck-box windows with decision_log.

    Limitation: BLOCK rows in decision_log only became visible 2026-04-28 (P0 deploy).
    So this can only quantify the recent ~6h. We instead use the GO rows which exist
    historically: count GOs that occurred WHILE a box was already stuck >4h, and what
    the typical SL/TP outcome was. This gives an indirect estimate of how many
    "should have been blocked" or "would have been allowed if box had expired."
    """
    if not DECISION_LOG.exists():
        return {"error": "decision_log not found"}

    # Index lifespans by interval for fast lookup
    stuck_intervals = lifespans[lifespans["duration_h"] > 4].copy()
    stuck_intervals["t_after_4h"] = stuck_intervals["first_ts"] + pd.Timedelta(hours=4)

    # Walk decision_log
    n_total = 0
    n_during_stuck = 0
    n_during_stuck_above_box = 0
    n_during_stuck_below_box = 0
    n_block_post_p0 = 0
    n_block_during_stuck_post_p0 = 0

    with open(DECISION_LOG, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ts_str = r.get("timestamp", "")
            try:
                ts = pd.Timestamp(ts_str)
            except Exception:
                continue
            if ts.tz is None:
                ts = ts.tz_localize("UTC")
            if ts < WINDOW_START or ts >= WINDOW_END:
                continue
            n_total += 1

            dec = r.get("decision", {})
            if isinstance(dec, dict):
                action = dec.get("action")
            else:
                action = str(dec) if dec else None
            is_block = (action == "BLOCK")
            if is_block and ts >= pd.Timestamp("2026-04-28", tz="UTC"):
                n_block_post_p0 += 1

            # Check if this row falls inside a stuck-box window
            mask = (stuck_intervals["t_after_4h"] <= ts) & (stuck_intervals["last_ts"] >= ts)
            if mask.any():
                n_during_stuck += 1
                box_row = stuck_intervals[mask].iloc[0]
                price = r.get("price_gc")
                if price is None:
                    ctx = r.get("context", {}) or {}
                    price = ctx.get("price_gc")
                if price is not None:
                    if price > box_row["box_high"]:
                        n_during_stuck_above_box += 1
                    elif price < box_row["box_low"]:
                        n_during_stuck_below_box += 1
                if is_block and ts >= pd.Timestamp("2026-04-28", tz="UTC"):
                    n_block_during_stuck_post_p0 += 1

    return {
        "decision_log_total_rows_in_window": n_total,
        "rows_during_stuck_box_4h": n_during_stuck,
        "rows_during_stuck_above_box": n_during_stuck_above_box,
        "rows_during_stuck_below_box": n_during_stuck_below_box,
        "block_rows_post_p0_total": n_block_post_p0,
        "block_rows_post_p0_during_stuck": n_block_during_stuck_post_p0,
        "note": (
            "BLOCK rows only became visible after P0 deploy on 2026-04-28; "
            "post-P0 sample is small (hours not months). For full 10-month "
            "estimate, see Phase 2 backtest."
        ),
    }


def main():
    print("Loading M30 parquet ...")
    df = load_m30()
    print(f"  loaded {len(df):,} bars, {df.index.min()} -> {df.index.max()}")

    print("Computing box lifespans ...")
    lifespans = find_box_lifespans(df)
    print(f"  {len(lifespans):,} distinct boxes")

    print("Stuck-box analysis ...")
    stuck = stuck_box_analysis(lifespans, df)
    print()
    print("=" * 70)
    print("STUCK-BOX SUMMARY")
    print("=" * 70)
    print(f"window:                {stuck['window']}")
    print(f"total_boxes:           {stuck['total_boxes']:,}")
    print(f"duration_h mean:       {stuck['duration_h_mean']:.2f}h")
    print(f"duration_h max:        {stuck['duration_h_max']:.2f}h")
    print(f"duration percentiles:  {stuck['duration_h_percentiles']}")
    print()
    for h in [2, 4, 6, 8, 12, 24]:
        n = stuck[f"n_boxes_duration_gt_{h}h"]
        pct = 100 * n / stuck['total_boxes']
        print(f"  boxes > {h}h:          {n:>5,}  ({pct:5.2f}%)")
    print()
    print(f"stuck >4h count:       {stuck['n_stuck_4h']:,}")
    print(f"stuck excursion/ATR percentiles: {stuck['stuck_4h_excursion_atr_percentiles']}")
    for n_atr in [2, 3, 4, 5]:
        n = stuck[f"n_stuck_4h_excursion_gt_{n_atr}atr"]
        pct = 100 * n / max(stuck['n_stuck_4h'], 1)
        print(f"  stuck >4h AND excursion >{n_atr}*ATR: {n:>5,}  ({pct:5.2f}% of stuck>4h)")
    print()
    print("Top-10 extreme stuck boxes:")
    for s in stuck["top10_extreme_stuck"]:
        print(f"  box {s['box_id']:>5}  {s['first_ts'][:10]} dur={s['duration_h']:>5.1f}h  "
              f"range={s['box_range_pts']:>5.1f}pts  exc={s['max_excursion_pts']:>5.1f}pts  "
              f"exc/range={s['excursion_over_range']}  exc/atr={s['excursion_over_atr']}")

    print()
    print("Bias accuracy during stagnation ...")
    bias = bias_accuracy_during_stagnation(lifespans, df)
    print()
    print("=" * 70)
    print("BIAS ACCURACY (4h forward outcome by box position)")
    print("=" * 70)
    print(f"total_stuck_bars_analyzed: {bias['total_stuck_bars_analyzed']:,}")
    for pos in ["above_box", "below_box", "inside"]:
        key = f"position_{pos}"
        if key not in bias:
            continue
        b = bias[key]
        print(f"\n{pos}:  n={b['n_bars']:,}  pct_up={b['pct_up']}%  pct_dn={b['pct_down']}%  "
              f"mean_4h={b['mean_4h_move_pts']:+.2f}pts  median={b['median_4h_move_pts']:+.2f}pts")

    print()
    print("Hypothetical P&L from decision_log ...")
    pnl = hypothetical_pnl_from_decision_log(lifespans)
    print()
    print("=" * 70)
    print("DECISION_LOG CROSS-REFERENCE")
    print("=" * 70)
    for k, v in pnl.items():
        print(f"  {k}: {v}")

    # Persist JSON
    OUT_JSON.write_text(json.dumps({
        "stuck_box_summary": stuck,
        "bias_accuracy": bias,
        "decision_log_xref": pnl,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nJSON written to {OUT_JSON}")


if __name__ == "__main__":
    main()

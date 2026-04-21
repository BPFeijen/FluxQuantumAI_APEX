#!/usr/bin/env python3
"""
Threshold Sensitivity Stress Test — ats_iceberg_v1 / Gate V4
=============================================================

Tests iceberg_proxy_threshold from 0.50 → 0.95 (step 0.05) against
9 months of GC L2 data to identify the Sweet Spot of profitability vs. risk.

Architecture:
  - V1.9 fixed rules: refill_count >= 3, Native Consistency >= 0.75
  - Variable: iceberg_proxy_threshold (MIN_CONFIDENCE_ACTIONABLE)
  - Trade simulation: TP=10pts, SL=7pts, commission=$8/rt, slippage=1tick=0.1pt
  - Dedup: one trade per (side, price_bucket=1pt, 5min_window) per day
  - Forward exit: mid_price from microstructure CSV (30min max window)
  - Baseline for Opportunity Cost calculation: threshold=0.60

Data:
  JSONL:        C:/data/iceberg/iceberg__GC_XCEC_YYYYMMDD.jsonl
  Microstructure: C:/data/level2/_gc_xcec/microstructure_YYYY-MM-DD.csv.gz
  Overlap days: 75 (Dec 2025 – Apr 2026 + sparse earlier)

Author: FluxQuantumAI ML Engineering — 2026-04-10
"""

import json
import glob
import math
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
JSONL_DIR  = Path("C:/data/iceberg")
MICRO_DIR  = Path("C:/data/level2/_gc_xcec")
OUTPUT_DIR = Path("C:/data/calibration")

# v1.9 fixed rules
FIXED_MIN_REFILLS        = 3
FIXED_NATIVE_CONSISTENCY = 0.75   # native icebergs: min prob regardless of threshold

# Trade simulation parameters (GC futures)
TP_PTS          = 10.0   # take-profit in GC points ($100/pt)
SL_PTS          = 7.0    # stop-loss in GC points
COMMISSION_PTS  = 0.08   # $8 round-trip / $100 per point
SLIPPAGE_PTS    = 0.10   # 1 tick slippage each direction
MAX_HOLD_MIN    = 30     # force-close after 30 minutes
PRICE_BUCKET    = 1.0    # 1pt price dedup bucket
TIME_BUCKET_MIN = 5      # 5-min time dedup window

NET_TP = TP_PTS - COMMISSION_PTS - SLIPPAGE_PTS   # 9.82 pts
NET_SL = SL_PTS + COMMISSION_PTS + SLIPPAGE_PTS   # 7.18 pts

# Thresholds to test
THRESHOLDS = [round(t, 2) for t in np.arange(0.50, 0.96, 0.05)]
BASELINE_THRESHOLD = 0.60   # Opportunity Cost baseline


# ─────────────────────────────────────────────────────────────────────────────
# Data loading helpers
# ─────────────────────────────────────────────────────────────────────────────

def _micro_path(date_str: str) -> Path | None:
    """Return path to microstructure file for YYYY-MM-DD, or None."""
    for suffix in [f"microstructure_{date_str}.fixed.csv.gz",
                   f"microstructure_{date_str}.csv.gz"]:
        p = MICRO_DIR / suffix
        if p.exists():
            return p
    return None


def load_microstructure(date_str: str) -> pd.DataFrame | None:
    """Load microstructure CSV for date_str. Returns None if not found."""
    path = _micro_path(date_str)
    if path is None:
        return None
    try:
        df = pd.read_csv(path, usecols=["timestamp", "mid_price"],
                         parse_dates=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        df["mid_price"] = pd.to_numeric(df["mid_price"], errors="coerce")
        return df.dropna(subset=["mid_price"])
    except Exception as e:
        return None


def load_jsonl(date_str: str) -> list[dict]:
    """Load iceberg JSONL records for date YYYYMMDD."""
    date_compact = date_str.replace("-", "")
    path = JSONL_DIR / f"iceberg__GC_XCEC_{date_compact}.jsonl"
    if not path.exists():
        return []
    records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    records.append(r)
                except Exception:
                    continue
    except Exception:
        pass
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Forward exit simulation
# ─────────────────────────────────────────────────────────────────────────────

def simulate_exit(
    entry_price: float,
    side: str,           # 'bid' = LONG, 'ask' = SHORT
    event_ts: pd.Timestamp,
    micro_df: pd.DataFrame,
) -> tuple[float, str]:
    """
    Walk forward mid_price from event_ts and return (pnl_pts, exit_reason).
    Exits: TP_PTS, SL_PTS, 30-min max, end-of-data.
    """
    cutoff = event_ts + pd.Timedelta(minutes=MAX_HOLD_MIN)
    fwd = micro_df[
        (micro_df["timestamp"] > event_ts) &
        (micro_df["timestamp"] <= cutoff)
    ]

    if len(fwd) == 0:
        return 0.0, "no_data"

    direction = 1 if side == "bid" else -1   # +1 = LONG, -1 = SHORT

    for _, row in fwd.iterrows():
        price = float(row["mid_price"])
        move  = (price - entry_price) * direction   # positive = favourable

        if move >= TP_PTS:
            return NET_TP, "TP"
        if -move >= SL_PTS:
            return -NET_SL, "SL"

    # Time exit: use last available price
    last_price = float(fwd.iloc[-1]["mid_price"])
    raw_pnl    = (last_price - entry_price) * direction
    net_pnl    = raw_pnl - COMMISSION_PTS - SLIPPAGE_PTS
    return round(net_pnl, 4), "TIME"


# ─────────────────────────────────────────────────────────────────────────────
# Per-threshold backtest
# ─────────────────────────────────────────────────────────────────────────────

def backtest_threshold(
    threshold: float,
    day_data: list[tuple[str, list[dict], pd.DataFrame | None]],
) -> dict:
    """
    Run backtest for a single threshold value.

    v1.9 filter rules:
      - refill_count >= FIXED_MIN_REFILLS (3)
      - For 'native' type: probability >= max(threshold, FIXED_NATIVE_CONSISTENCY)
      - For 'synthetic' type: probability >= threshold
    Dedup: one trade per (side, price_bucket, 5min_window) per day.

    Returns metrics dict.
    """
    trades     = []    # list of pnl_pts
    exit_types = []
    daily_equity = []  # for drawdown: daily cumulative P&L

    for date_str, records, micro_df in day_data:
        if not records:
            continue

        # ── Filter ──────────────────────────────────────────────────────────
        qualifying = []
        for r in records:
            try:
                prob     = float(r.get("probability", 0))
                refills  = int(r.get("refill_count", 0))
                ice_type = str(r.get("iceberg_type", "")).lower()
            except Exception:
                continue

            if refills < FIXED_MIN_REFILLS:
                continue

            # Native icebergs have floor of FIXED_NATIVE_CONSISTENCY
            min_prob = max(threshold, FIXED_NATIVE_CONSISTENCY) if ice_type == "native" else threshold
            if prob < min_prob:
                continue

            qualifying.append(r)

        if not qualifying:
            continue

        # ── Deduplication ───────────────────────────────────────────────────
        df_q = pd.DataFrame(qualifying)
        df_q["ts"]           = pd.to_datetime(df_q["timestamp"], utc=True, errors="coerce")
        df_q["probability"]  = pd.to_numeric(df_q["probability"], errors="coerce")
        df_q["price"]        = pd.to_numeric(df_q["price"],        errors="coerce")
        df_q = df_q.dropna(subset=["ts", "probability", "price"])
        if len(df_q) == 0:
            continue

        df_q["price_bucket"] = (df_q["price"] / PRICE_BUCKET).round(0) * PRICE_BUCKET
        df_q["time_bucket"]  = df_q["ts"].dt.floor(f"{TIME_BUCKET_MIN}min")
        # Keep highest-probability event per (side, price_bucket, time_bucket)
        df_dedup = (
            df_q.sort_values("probability", ascending=False)
            .groupby(["side", "price_bucket", "time_bucket"], sort=False)
            .first()
            .reset_index()
        )

        # ── Trade Simulation ─────────────────────────────────────────────────
        day_pnl = 0.0
        for _, row in df_dedup.iterrows():
            side        = str(row["side"]).lower()
            entry_price = float(row["price"])
            event_ts    = row["ts"]

            if micro_df is not None and len(micro_df) > 0:
                pnl, reason = simulate_exit(entry_price, side, event_ts, micro_df)
            else:
                # No microstructure: cannot simulate — skip this trade
                continue

            trades.append(pnl)
            exit_types.append(reason)
            day_pnl += pnl

        daily_equity.append(day_pnl)

    # ── Metrics ─────────────────────────────────────────────────────────────
    if not trades:
        return {
            "threshold": threshold,
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy_pts": 0.0,
            "max_drawdown_pts": 0.0,
            "total_pnl_pts": 0.0,
            "pf_x_volume": 0.0,
        }

    arr  = np.array(trades)
    wins = arr[arr > 0]
    loss = arr[arr < 0]

    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss   = float(abs(loss.sum())) if len(loss) > 0 else 1e-9
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    expectancy   = float(arr.mean())
    win_rate     = float(len(wins)) / len(arr)
    total_pnl    = float(arr.sum())

    # Max drawdown from equity curve
    equity = np.cumsum(arr)
    peak   = np.maximum.accumulate(equity)
    dd     = peak - equity
    max_dd = float(dd.max()) if len(dd) > 0 else 0.0

    tp_count   = exit_types.count("TP")
    sl_count   = exit_types.count("SL")
    time_count = exit_types.count("TIME")

    return {
        "threshold":       threshold,
        "total_trades":    len(trades),
        "win_rate":        round(win_rate, 4),
        "gross_profit":    round(gross_profit, 2),
        "gross_loss":      round(gross_loss, 2),
        "profit_factor":   round(profit_factor, 4),
        "expectancy_pts":  round(expectancy, 4),
        "max_drawdown_pts": round(max_dd, 2),
        "total_pnl_pts":   round(total_pnl, 2),
        "pf_x_volume":     round(profit_factor * len(trades), 2),
        "exit_tp":         tp_count,
        "exit_sl":         sl_count,
        "exit_time":       time_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  Threshold Sensitivity Stress Test — ats_iceberg_v1 / Gate V4")
    print(f"  Run: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 70)

    # ── Discover overlap days ────────────────────────────────────────────────
    micro_dates = set()
    for f in glob.glob(str(MICRO_DIR / "microstructure_*.csv.gz")):
        stem = Path(f).stem.replace(".fixed", "").replace(".csv", "")
        date_part = stem.replace("microstructure_", "")
        micro_dates.add(date_part)

    jsonl_dates = set()
    for f in glob.glob(str(JSONL_DIR / "iceberg__GC_XCEC_*.jsonl")):
        compact = Path(f).stem[-8:]
        iso     = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
        jsonl_dates.add(iso)

    overlap = sorted(micro_dates & jsonl_dates)
    print(f"\nData coverage: {len(overlap)} days with both JSONL + microstructure")
    print(f"  Range: {overlap[0]} to {overlap[-1]}")

    # ── Preload all data ─────────────────────────────────────────────────────
    print("\nLoading data...")
    day_data = []
    loaded = 0
    total_raw_records = 0
    for date_str in overlap:
        records   = load_jsonl(date_str)
        micro_df  = load_microstructure(date_str)
        if records and micro_df is not None:
            day_data.append((date_str, records, micro_df))
            total_raw_records += len(records)
            loaded += 1

    print(f"  Loaded {loaded} days, {total_raw_records:,} raw JSONL records")

    # ── Precompute raw counts per threshold for context ──────────────────────
    print("\nEvent counts by threshold (before dedup, across all days):")
    print(f"  {'Threshold':>10}  {'Events(raw)':>12}  {'After dedup est.':>16}")

    # ── Run backtest for each threshold ──────────────────────────────────────
    print("\nRunning backtest sweep...")
    results = []
    for t in THRESHOLDS:
        res = backtest_threshold(t, day_data)
        results.append(res)
        print(f"  T={t:.2f}: trades={res['total_trades']:4d}  "
              f"PF={res['profit_factor']:6.3f}  "
              f"E={res['expectancy_pts']:+7.4f}pts  "
              f"MaxDD={res['max_drawdown_pts']:7.2f}  "
              f"PF×Vol={res['pf_x_volume']:8.2f}")

    # ── Opportunity Cost vs baseline ─────────────────────────────────────────
    baseline = next((r for r in results if abs(r["threshold"] - BASELINE_THRESHOLD) < 0.001), None)
    for res in results:
        if baseline:
            res["opportunity_cost_pts"] = round(
                (baseline["total_pnl_pts"] - res["total_pnl_pts"]), 2
            )
        else:
            res["opportunity_cost_pts"] = 0.0

    # ── Find sweet spot ──────────────────────────────────────────────────────
    valid = [r for r in results if r["total_trades"] >= 5]
    if valid:
        sweet = max(valid, key=lambda r: r["pf_x_volume"])
        safe  = max(valid, key=lambda r: r["profit_factor"] if r["total_trades"] >= 10 else 0)
    else:
        sweet = safe = results[0]

    # ── Print Markdown table ─────────────────────────────────────────────────
    print("\n\n" + "=" * 70)
    print("  RESULTS TABLE (GC pts; 1pt = $100/contract)")
    print("=" * 70)
    print()

    header = (
        f"| {'Threshold':^10} | {'Trades':^7} | {'Win%':^6} | "
        f"{'PF':^7} | {'E(pts)':^8} | {'MaxDD':^8} | "
        f"{'TotalPnL':^9} | {'PF×Vol':^9} | {'OppCost':^9} |"
    )
    sep = "|" + "|".join(["-" * (w + 2) for w in [10, 7, 6, 7, 8, 8, 9, 9, 9]]) + "|"
    print(header)
    print(sep)

    for res in results:
        marker = " ◄ SWEET" if res == sweet else ("" if res != safe else " ◄ SAFE")
        t_str  = f"{res['threshold']:.2f}"
        if abs(res["threshold"] - 0.9150) < 0.005:
            t_str = "0.9150*"
        print(
            f"| {t_str:^10} | {res['total_trades']:^7} | "
            f"{res['win_rate']*100:^6.1f} | "
            f"{res['profit_factor']:^7.3f} | "
            f"{res['expectancy_pts']:^+8.4f} | "
            f"{res['max_drawdown_pts']:^8.2f} | "
            f"{res['total_pnl_pts']:^+9.2f} | "
            f"{res['pf_x_volume']:^9.2f} | "
            f"{res['opportunity_cost_pts']:^+9.2f} |"
            f"{marker}"
        )

    print()
    print(f"* 0.9150 = current calibrated value in settings.json")
    print()
    print("─" * 70)
    print(f"SWEET SPOT (max PF×Volume):  threshold = {sweet['threshold']:.2f}")
    print(f"  PF={sweet['profit_factor']:.3f}  "
          f"Trades={sweet['total_trades']}  "
          f"E={sweet['expectancy_pts']:+.4f}pts  "
          f"TotalPnL={sweet['total_pnl_pts']:+.2f}pts")
    print()
    print(f"SAFEST (max PF, min 10 trades): threshold = {safe['threshold']:.2f}")
    print(f"  PF={safe['profit_factor']:.3f}  "
          f"Trades={safe['total_trades']}  "
          f"E={safe['expectancy_pts']:+.4f}pts")
    print()

    if baseline:
        print(f"BASELINE (T=0.60): Trades={baseline['total_trades']}  "
              f"PnL={baseline['total_pnl_pts']:+.2f}pts  "
              f"PF={baseline['profit_factor']:.3f}")
    print()

    # ── Save JSON results ────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "threshold_sensitivity_test_results.json"
    output = {
        "generated": datetime.now().isoformat(),
        "data_days": loaded,
        "data_range": f"{overlap[0]} to {overlap[-1]}",
        "config": {
            "tp_pts": TP_PTS,
            "sl_pts": SL_PTS,
            "commission_pts": COMMISSION_PTS,
            "slippage_pts": SLIPPAGE_PTS,
            "max_hold_minutes": MAX_HOLD_MIN,
            "dedup_price_bucket_pts": PRICE_BUCKET,
            "dedup_time_bucket_min": TIME_BUCKET_MIN,
            "fixed_min_refills": FIXED_MIN_REFILLS,
            "fixed_native_consistency": FIXED_NATIVE_CONSISTENCY,
            "baseline_threshold": BASELINE_THRESHOLD,
        },
        "sweet_spot_threshold": sweet["threshold"],
        "safe_threshold": safe["threshold"],
        "current_settings_json_threshold": 0.9150,
        "results": results,
    }
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()

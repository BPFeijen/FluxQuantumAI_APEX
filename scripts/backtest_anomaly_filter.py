#!/usr/bin/env python3
"""
backtest_anomaly_filter.py — Compare: baseline vs anomaly severity >= HIGH filter
=================================================================================

Uses the same position manager simulation but splits into two groups:
  A) BASELINE: all 24 trades (no filter)
  B) FILTERED: block entry when anomaly severity >= HIGH

Same data, same position manager checks, same thresholds.
Only difference: group B skips trades that would have been blocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

RESULTS_PATH = Path("C:/FluxQuantumAI/logs/backtest_posmon_results.jsonl")
OUTPUT_PATH  = Path("C:/FluxQuantumAI/logs/backtest_anomaly_filter_comparison.json")


def load_results() -> list[dict]:
    results = []
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                results.append(json.loads(line))
    return results


def compute_stats(trades: list[dict], label: str) -> dict:
    n = len(trades)
    if n == 0:
        return {"label": label, "trades": 0, "note": "no trades"}

    pnls = [t["pnl_pts"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flat = [p for p in pnls if p == 0]

    total_pnl = sum(pnls)
    gross_win = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    pf = round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf")

    exit_reasons = {}
    for t in trades:
        er = t["exit_reason"]
        exit_reasons[er] = exit_reasons.get(er, 0) + 1

    shield_count = sum(1 for t in trades if t["shield_activated"])

    return {
        "label": label,
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(flat),
        "win_rate": round(len(wins) / n * 100, 1),
        "total_pnl_pts": round(total_pnl, 2),
        "avg_pnl_pts": round(np.mean(pnls), 2),
        "avg_win_pts": round(np.mean(wins), 2) if wins else 0,
        "avg_loss_pts": round(np.mean(losses), 2) if losses else 0,
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": pf,
        "shield_count": shield_count,
        "shield_rate": round(shield_count / n * 100, 1),
        "exit_reasons": exit_reasons,
    }


def main():
    results = load_results()
    print(f"Loaded {len(results)} trades from backtest results\n")

    # Split: baseline vs filtered
    baseline = results
    filtered = [
        t for t in results
        if t["protection"]["anomaly"]["severity"] not in ("HIGH", "CRITICAL")
    ]
    blocked = [
        t for t in results
        if t["protection"]["anomaly"]["severity"] in ("HIGH", "CRITICAL")
    ]

    stats_a = compute_stats(baseline, "A) BASELINE (all trades)")
    stats_b = compute_stats(filtered, "B) FILTERED (block anomaly >= HIGH)")
    stats_blocked = compute_stats(blocked, "BLOCKED trades (would have been avoided)")

    # Print comparison
    print("=" * 90)
    print(f"{'METRIC':<25s}  {'BASELINE':>15s}  {'FILTERED':>15s}  {'DELTA':>15s}")
    print("=" * 90)

    comparisons = [
        ("Trades",       stats_a["trades"],       stats_b["trades"],       stats_b["trades"] - stats_a["trades"]),
        ("Wins",         stats_a["wins"],          stats_b["wins"],          stats_b["wins"] - stats_a["wins"]),
        ("Losses",       stats_a["losses"],        stats_b["losses"],        stats_b["losses"] - stats_a["losses"]),
        ("Win Rate %",   stats_a["win_rate"],      stats_b["win_rate"],      stats_b["win_rate"] - stats_a["win_rate"]),
        ("Total PnL",    stats_a["total_pnl_pts"], stats_b["total_pnl_pts"], stats_b["total_pnl_pts"] - stats_a["total_pnl_pts"]),
        ("Avg PnL",      stats_a["avg_pnl_pts"],   stats_b["avg_pnl_pts"],   stats_b["avg_pnl_pts"] - stats_a["avg_pnl_pts"]),
        ("Avg Win",      stats_a["avg_win_pts"],    stats_b["avg_win_pts"],    stats_b["avg_win_pts"] - stats_a["avg_win_pts"]),
        ("Avg Loss",     stats_a["avg_loss_pts"],   stats_b["avg_loss_pts"],   stats_b["avg_loss_pts"] - stats_a["avg_loss_pts"]),
        ("Profit Factor", stats_a["profit_factor"], stats_b["profit_factor"], stats_b["profit_factor"] - stats_a["profit_factor"]),
        ("SHIELD Rate %", stats_a["shield_rate"],   stats_b["shield_rate"],   stats_b["shield_rate"] - stats_a["shield_rate"]),
    ]

    for label, val_a, val_b, delta in comparisons:
        if isinstance(val_a, float):
            col_a = f"{val_a:+.2f}" if "PnL" in label or "Avg" in label else f"{val_a:.2f}"
            col_b = f"{val_b:+.2f}" if "PnL" in label or "Avg" in label else f"{val_b:.2f}"
            col_d = f"{delta:+.2f}"
        else:
            col_a = f"{val_a}"
            col_b = f"{val_b}"
            col_d = f"{delta:+d}" if isinstance(delta, int) else f"{delta:+.1f}"
        print(f"  {label:<23s}  {col_a:>15s}  {col_b:>15s}  {col_d:>15s}")

    print("=" * 90)

    # Exit reasons comparison
    print(f"\n{'EXIT REASONS':<25s}  {'BASELINE':>15s}  {'FILTERED':>15s}")
    print("-" * 60)
    all_reasons = set(list(stats_a["exit_reasons"].keys()) + list(stats_b["exit_reasons"].keys()))
    for reason in sorted(all_reasons):
        ca = stats_a["exit_reasons"].get(reason, 0)
        cb = stats_b["exit_reasons"].get(reason, 0)
        print(f"  {reason:<23s}  {ca:>15d}  {cb:>15d}")

    # Blocked trades detail
    print(f"\n{'BLOCKED TRADES (anomaly >= HIGH)'}")
    print("-" * 90)
    for t in blocked:
        sev = t["protection"]["anomaly"]["severity"]
        reason = t["protection"]["anomaly"]["reason"]
        print(
            f"  {t['signal_ts'][:16]}  {t['direction']:5s}  "
            f"entry={t['entry_price']:.2f}  pnl={t['pnl_pts']:+.2f}  "
            f"exit={t['exit_reason']:15s}  "
            f"sev={sev:8s}  {reason}"
        )
    print(f"\n  Blocked: {len(blocked)} trades | PnL avoided: {stats_blocked['total_pnl_pts']:+.2f} pts")

    # Filtered trades detail
    if filtered:
        print(f"\n{'SURVIVING TRADES (anomaly < HIGH)'}")
        print("-" * 90)
        for t in filtered:
            sev = t["protection"]["anomaly"]["severity"]
            print(
                f"  {t['signal_ts'][:16]}  {t['direction']:5s}  "
                f"entry={t['entry_price']:.2f}  pnl={t['pnl_pts']:+.2f}  "
                f"exit={t['exit_reason']:15s}  sev={sev}"
            )

    # Save comparison
    comparison = {
        "baseline": stats_a,
        "filtered": stats_b,
        "blocked": stats_blocked,
        "filter_rule": "block entry when anomaly severity >= HIGH",
        "conclusion": {
            "pnl_improvement": round(stats_b["total_pnl_pts"] - stats_a["total_pnl_pts"], 2),
            "pf_improvement": round(stats_b["profit_factor"] - stats_a["profit_factor"], 3),
            "trades_blocked": len(blocked),
            "blocked_pnl": round(stats_blocked["total_pnl_pts"], 2),
        },
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"\n[OUTPUT] Comparison saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
backtest_anomaly_calibration.py — Calibrate anomaly filter threshold
====================================================================

Uses the 212 simulated trades from backtest_posmon_full_results.jsonl.
Re-evaluates anomaly severity with different configurations and compares.

Configurations tested:
  A) BASELINE (no filter)
  B) Block only CRITICAL
  C) Block HIGH but NOT when dom_extreme is the sole trigger
  D) Block HIGH only when 2+ conditions fired
  E) Recalibrate dom_extreme threshold: 0.85, 0.90, 0.95
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

RESULTS_PATH = Path("C:/FluxQuantumAI/logs/backtest_posmon_full_results.jsonl")
M1_L2_PATH   = Path("C:/data/processed/databento_l2_m1_features.parquet")
OUTPUT_PATH  = Path("C:/FluxQuantumAI/logs/backtest_anomaly_calibration.json")


def load_results() -> list[dict]:
    results = []
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                results.append(json.loads(line))
    return results


def recompute_anomaly(
    spread: float, dom_imb: float, bid_depth: float, ask_depth: float,
    spread_median: float, dom_threshold: float = 0.80,
) -> dict:
    """Recompute anomaly with configurable dom_extreme threshold."""
    fired = []
    if spread_median > 0 and spread / spread_median > 5.0:
        fired.append("spread_spike")
    if (bid_depth + ask_depth) < 50:
        fired.append("depth_collapse")
    if abs(dom_imb) > dom_threshold:
        fired.append("dom_extreme")
    n = len(fired)
    if n >= 2:
        sev = "CRITICAL"
    elif n == 1:
        sev = "HIGH"
    else:
        sev = "NONE"
    return {"severity": sev, "fired": fired, "n_fired": n}


def stats_for_group(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "total_pnl": 0, "avg_pnl": 0, "avg_win": 0, "avg_loss": 0,
                "pf": 0, "winners_blocked": 0, "losers_avoided": 0}
    pnls = [t["pnl_pts"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 0
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(np.mean(pnls), 2),
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
        "pf": round(gw / gl, 3) if gl > 0 else float("inf"),
    }


def main():
    results = load_results()
    print(f"Loaded {len(results)} trades\n")

    # Load M1 data for re-evaluation
    print("Loading M1 L2 data for re-evaluation...")
    m1 = pd.read_parquet(M1_L2_PATH)
    if m1.index.tz is None:
        m1.index = m1.index.tz_localize("UTC")

    # For each trade, extract microstructure at entry for re-evaluation
    print("Re-evaluating anomaly severity for each trade...")
    for r in results:
        ts = pd.Timestamp(r["signal_ts"])
        m1_win = m1[(m1.index >= ts - pd.Timedelta(minutes=30)) & (m1.index <= ts)]
        if m1_win.empty:
            r["_micro"] = None
            continue
        last = m1_win.iloc[-1]
        r["_micro"] = {
            "spread": float(last.get("spread", 0.0)),
            "dom_imb": float(last.get("dom_imbalance", 0.0)),
            "bid_depth": float(last.get("total_bid_depth", 500.0)),
            "ask_depth": float(last.get("total_ask_depth", 500.0)),
            "spread_med": float(m1_win["spread"].median()) if "spread" in m1_win.columns and len(m1_win) > 1 else max(float(last.get("spread", 0.01)), 0.01),
        }

    # ── Define filter configurations ─────────────────────────────────────
    configs = {}

    # A) BASELINE
    configs["A_BASELINE"] = {
        "desc": "No filter (all trades)",
        "filter": lambda r, anom: True,
    }

    # B) Block only CRITICAL
    configs["B_CRITICAL_ONLY"] = {
        "desc": "Block only CRITICAL (2+ rules fired)",
        "filter": lambda r, anom: anom["severity"] != "CRITICAL",
    }

    # C) Block HIGH but NOT when dom_extreme is sole trigger
    configs["C_HIGH_NO_DOM_SOLO"] = {
        "desc": "Block HIGH except when dom_extreme is sole trigger",
        "filter": lambda r, anom: not (
            anom["severity"] in ("HIGH", "CRITICAL") and
            not (anom["n_fired"] == 1 and "dom_extreme" in anom["fired"])
        ),
    }

    # D) Block HIGH only when 2+ conditions
    configs["D_HIGH_2PLUS"] = {
        "desc": "Block only when 2+ conditions fired (=CRITICAL)",
        "filter": lambda r, anom: anom["n_fired"] < 2,
    }

    # E) dom_extreme thresholds
    for thr in [0.85, 0.90, 0.95]:
        key = f"E_DOM_{int(thr*100)}"
        configs[key] = {
            "desc": f"dom_extreme threshold = {thr} (block HIGH+CRITICAL)",
            "dom_threshold": thr,
            "filter": lambda r, anom: anom["severity"] == "NONE",
        }

    # ── Evaluate each configuration ──────────────────────────────────────
    all_results = {}

    for cfg_name, cfg in configs.items():
        dom_thr = cfg.get("dom_threshold", 0.80)
        filter_fn = cfg["filter"]

        surviving = []
        blocked = []

        for r in results:
            micro = r.get("_micro")
            if micro is None:
                # No micro data (live trades etc) → recompute gives NONE → passes all filters
                anom = {"severity": "NONE", "fired": [], "n_fired": 0}
            else:
                anom = recompute_anomaly(
                    micro["spread"], micro["dom_imb"],
                    micro["bid_depth"], micro["ask_depth"],
                    micro["spread_med"], dom_threshold=dom_thr,
                )

            if filter_fn(r, anom):
                surviving.append(r)
            else:
                blocked.append(r)

        s_surv = stats_for_group(surviving)
        s_block = stats_for_group(blocked)

        # Count winners/losers blocked
        winners_blocked = sum(1 for t in blocked if t["pnl_pts"] > 0)
        losers_avoided = sum(1 for t in blocked if t["pnl_pts"] < 0)
        pnl_of_blocked_winners = sum(t["pnl_pts"] for t in blocked if t["pnl_pts"] > 0)
        pnl_of_blocked_losers = sum(t["pnl_pts"] for t in blocked if t["pnl_pts"] < 0)

        all_results[cfg_name] = {
            "desc": cfg["desc"],
            "surviving": s_surv,
            "blocked_count": len(blocked),
            "winners_blocked": winners_blocked,
            "losers_avoided": losers_avoided,
            "pnl_winners_blocked": round(pnl_of_blocked_winners, 2),
            "pnl_losers_avoided": round(pnl_of_blocked_losers, 2),
        }

    # ── Print comparison table ───────────────────────────────────────────
    header = f"{'CONFIG':<22s} {'TRADES':>6s} {'WIN%':>6s} {'TOT_PNL':>9s} {'AVG_PNL':>8s} {'PF':>7s} {'W_BLKD':>7s} {'L_AVOID':>7s} {'PNL_W_LOST':>10s} {'PNL_L_AVOID':>11s}"
    print("\n" + "=" * 120)
    print(header)
    print("=" * 120)

    for cfg_name, data in all_results.items():
        s = data["surviving"]
        print(
            f"  {cfg_name:<20s} "
            f"{s['trades']:>6d} "
            f"{s['win_rate']:>5.1f}% "
            f"{s['total_pnl']:>+9.2f} "
            f"{s['avg_pnl']:>+8.2f} "
            f"{s['pf']:>7.3f} "
            f"{data['winners_blocked']:>7d} "
            f"{data['losers_avoided']:>7d} "
            f"{data['pnl_winners_blocked']:>+10.2f} "
            f"{data['pnl_losers_avoided']:>+11.2f}"
        )

    print("=" * 120)

    # ── Detailed breakdown per config ────────────────────────────────────
    baseline = all_results["A_BASELINE"]["surviving"]
    print(f"\nBASELINE reference: {baseline['trades']} trades | PF={baseline['pf']} | PnL={baseline['total_pnl']:+.2f}")

    print("\nDelta vs BASELINE:")
    print(f"{'CONFIG':<22s} {'dTRADES':>8s} {'dWIN%':>7s} {'dPNL':>9s} {'dAVG':>8s} {'dPF':>8s}")
    print("-" * 70)
    for cfg_name, data in all_results.items():
        if cfg_name == "A_BASELINE":
            continue
        s = data["surviving"]
        dt = s["trades"] - baseline["trades"]
        dw = s["win_rate"] - baseline["win_rate"]
        dp = s["total_pnl"] - baseline["total_pnl"]
        da = s["avg_pnl"] - baseline["avg_pnl"]
        df = s["pf"] - baseline["pf"]
        flag = " ***" if df > 0 and dp > -50 else ""
        print(
            f"  {cfg_name:<20s} "
            f"{dt:>+8d} "
            f"{dw:>+6.1f}% "
            f"{dp:>+9.2f} "
            f"{da:>+8.2f} "
            f"{df:>+8.3f}{flag}"
        )

    # ── Save ─────────────────────────────────────────────────────────────
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n[OUTPUT] {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
backtest_scale_aware_calibration.py — Scale-aware anomaly filter calibration
============================================================================

Key finding: Databento dom_imbalance is RAW (range -94 to +90, median abs=8.2)
             Quantower dom_imbalance is NORMALISED (range -0.85 to +0.85, median abs=0.03)

The threshold 0.80 was designed for Quantower (P99.9=0.84).
For Databento, equivalent percentile thresholds are:
  P90 = 21.3, P95 = 26.5, P99 = 39.7

This script tests scale-aware thresholds and finds the optimal filter.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

RESULTS_PATH = Path("C:/FluxQuantumAI/logs/backtest_posmon_full_results.jsonl")
M1_L2_PATH   = Path("C:/data/processed/databento_l2_m1_features.parquet")
MICRO_DIR    = Path("C:/data/level2/_gc_xcec")


def load_results():
    results = []
    with open(RESULTS_PATH) as f:
        for line in f:
            if line.strip():
                results.append(json.loads(line))
    return results


qt_cache = {}
def read_qt(date_str):
    if date_str in qt_cache:
        return qt_cache[date_str]
    for suf in [".fixed.csv.gz", ".csv.gz"]:
        p = MICRO_DIR / f"microstructure_{date_str}{suf}"
        if p.exists():
            try:
                df = pd.read_csv(p)
                ts_col = "recv_timestamp" if "recv_timestamp" in df.columns else "timestamp"
                df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="ISO8601")
                df = df.set_index(ts_col).sort_index()
                qt_cache[date_str] = df
                return df
            except Exception:
                pass
    qt_cache[date_str] = None
    return None


def recompute(spread, dom_imb, bid_depth, ask_depth, spread_med,
              dom_thr):
    fired = []
    if spread_med > 0 and spread / spread_med > 5.0:
        fired.append("spread_spike")
    if (bid_depth + ask_depth) < 50:
        fired.append("depth_collapse")
    if abs(dom_imb) > dom_thr:
        fired.append("dom_extreme")
    n = len(fired)
    if n >= 2:
        sev = "CRITICAL"
    elif n == 1:
        sev = "HIGH"
    else:
        sev = "NONE"
    return sev, fired, n


def stats(trades):
    n = len(trades)
    if n == 0:
        return dict(trades=0, wins=0, losses=0, wr=0, pnl=0, avg=0, pf=0)
    pnls = [t["pnl_pts"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 0
    return dict(
        trades=n, wins=len(wins), losses=len(losses),
        wr=round(len(wins)/n*100, 1),
        pnl=round(sum(pnls), 2),
        avg=round(np.mean(pnls), 2),
        pf=round(gw/gl, 3) if gl > 0 else float("inf"),
    )


def main():
    results = load_results()
    print(f"Loaded {len(results)} trades")

    m1 = pd.read_parquet(M1_L2_PATH)
    if m1.index.tz is None:
        m1.index = m1.index.tz_localize("UTC")

    # Enrich each trade with micro data
    print("Enriching trades with microstructure...")
    for r in results:
        ts = pd.Timestamp(r["signal_ts"])
        date_str = str(r["signal_ts"])[:10]
        r["_is_db"] = ts < pd.Timestamp("2025-11-25", tz="UTC")

        if r["_is_db"]:
            w = m1[(m1.index >= ts - pd.Timedelta(minutes=30)) & (m1.index <= ts)]
            if not w.empty:
                last = w.iloc[-1]
                r["_m"] = dict(
                    spread=float(last.get("spread", 0)),
                    dom=float(last.get("dom_imbalance", 0)),
                    bd=float(last.get("total_bid_depth", 500)),
                    ad=float(last.get("total_ask_depth", 500)),
                    smed=float(w["spread"].median()) if len(w) > 1 else max(float(last.get("spread", 0.01)), 0.01),
                )
            else:
                r["_m"] = None
        else:
            qt = read_qt(date_str)
            if qt is not None and "dom_imbalance" in qt.columns:
                w = qt[(qt.index >= ts - pd.Timedelta(minutes=30)) & (qt.index <= ts)]
                if not w.empty:
                    last = w.iloc[-1]
                    bd_col = "total_bid_size" if "total_bid_size" in qt.columns else "total_bid_depth"
                    ad_col = "total_ask_size" if "total_ask_size" in qt.columns else "total_ask_depth"
                    r["_m"] = dict(
                        spread=float(last.get("spread", 0)),
                        dom=float(last.get("dom_imbalance", 0)),
                        bd=float(last.get(bd_col, 500)),
                        ad=float(last.get(ad_col, 500)),
                        smed=float(w["spread"].median()) if "spread" in w.columns and len(w) > 1 else 0.01,
                    )
                else:
                    r["_m"] = None
            else:
                r["_m"] = None

    # Configurations: (name, databento_dom_thr, quantower_dom_thr, filter_rule)
    configs = [
        ("A) BASELINE",           999,   999,   "none"),
        # Block HIGH + CRITICAL with scale-aware dom
        ("B) DB=P90(21) QT=0.80", 21.0,  0.80,  "block_hc"),
        ("C) DB=P95(27) QT=0.80", 26.5,  0.80,  "block_hc"),
        ("D) DB=P99(40) QT=0.80", 39.7,  0.80,  "block_hc"),
        ("E) DB=P90(21) QT=P99(0.63)", 21.0, 0.634, "block_hc"),
        ("F) DB=P95(27) QT=P99(0.63)", 26.5, 0.634, "block_hc"),
        ("G) DB=P99(40) QT=P99(0.63)", 39.7, 0.634, "block_hc"),
        # Block CRITICAL only (2+ rules)
        ("H) CRIT DB=P90 QT=0.80", 21.0,  0.80,  "block_c"),
        ("I) CRIT DB=P95 QT=0.80", 26.5,  0.80,  "block_c"),
        ("J) CRIT DB=P99 QT=0.80", 39.7,  0.80,  "block_c"),
    ]

    hdr = (
        f"  {'CONFIG':<27s} "
        f"{'TRD':>4s} {'WIN%':>6s} {'TOT_PNL':>9s} {'AVG':>7s} "
        f"{'PF':>7s} {'W_BLK':>5s} {'L_AVD':>5s} "
        f"{'PNL_W_LOST':>10s} {'PNL_L_AVD':>10s}"
    )
    print("\n" + "=" * 110)
    print(hdr)
    print("=" * 110)

    best_name = ""
    best_pf = 0
    best_pnl = 0

    for name, db_thr, qt_thr, rule in configs:
        surviving = []
        blocked = []
        for r in results:
            m = r.get("_m")
            if m is None:
                sev = "NONE"
            else:
                thr = db_thr if r["_is_db"] else qt_thr
                sev, _, n_fired = recompute(m["spread"], m["dom"], m["bd"], m["ad"], m["smed"], thr)

            if rule == "none":
                surviving.append(r)
            elif rule == "block_hc":
                (blocked if sev in ("HIGH", "CRITICAL") else surviving).append(r)
            elif rule == "block_c":
                (blocked if sev == "CRITICAL" else surviving).append(r)

        s = stats(surviving)
        wb = sum(1 for t in blocked if t["pnl_pts"] > 0)
        la = sum(1 for t in blocked if t["pnl_pts"] < 0)
        pnl_wb = sum(t["pnl_pts"] for t in blocked if t["pnl_pts"] > 0)
        pnl_la = sum(t["pnl_pts"] for t in blocked if t["pnl_pts"] < 0)

        flag = ""
        if s["pf"] > 1.328 and s["trades"] > 30:
            flag = " <-- PF UP"
            if s["pf"] > best_pf:
                best_pf = s["pf"]
                best_pnl = s["pnl"]
                best_name = name

        print(
            f"  {name:<27s} "
            f"{s['trades']:>4d} {s['wr']:>5.1f}% "
            f"{s['pnl']:>+9.2f} {s['avg']:>+7.2f} "
            f"{s['pf']:>7.3f} {wb:>5d} {la:>5d} "
            f"{pnl_wb:>+10.2f} {pnl_la:>+10.2f}{flag}"
        )

    print("=" * 110)
    if best_name:
        print(f"\n  BEST: {best_name}  PF={best_pf}  PnL={best_pnl:+.2f}")
    else:
        print("\n  No config improved PF above baseline while keeping >30 trades.")

    # Detail: for the best config, show what was blocked
    if best_name:
        print(f"\n  Detail for {best_name}:")
        # Re-run to get blocked list
        for name, db_thr, qt_thr, rule in configs:
            if name != best_name:
                continue
            blocked_detail = []
            for r in results:
                m = r.get("_m")
                if m is None:
                    continue
                thr = db_thr if r["_is_db"] else qt_thr
                sev, fired, _ = recompute(m["spread"], m["dom"], m["bd"], m["ad"], m["smed"], thr)
                is_blocked = (rule == "block_hc" and sev in ("HIGH", "CRITICAL")) or \
                             (rule == "block_c" and sev == "CRITICAL")
                if is_blocked:
                    blocked_detail.append((r, sev, fired))

            wins_b = [(r, s, f) for r, s, f in blocked_detail if r["pnl_pts"] > 0]
            loss_b = [(r, s, f) for r, s, f in blocked_detail if r["pnl_pts"] < 0]
            flat_b = [(r, s, f) for r, s, f in blocked_detail if r["pnl_pts"] == 0]
            print(f"    Blocked: {len(blocked_detail)} total ({len(wins_b)} winners, {len(loss_b)} losers, {len(flat_b)} flat)")
            print(f"    Winners lost PnL: {sum(r['pnl_pts'] for r,_,_ in wins_b):+.2f}")
            print(f"    Losers avoided PnL: {sum(r['pnl_pts'] for r,_,_ in loss_b):+.2f}")


if __name__ == "__main__":
    main()

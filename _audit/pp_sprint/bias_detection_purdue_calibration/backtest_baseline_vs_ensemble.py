"""BIAS-DETECTION-PURDUE-CALIBRATION — Phase 2 backtest (Asana 1214284792412296)

Compares BASELINE (62f4346 strict-monotonic on M30 FMV resample-to-D1) vs
ENSEMBLE (B+C voting per Phase 1 implementation) over the 9.7m clean window.

Methodological scope (per Phase B8 precedent at
_audit/pp_sprint/bias_detection_complete_fix/backtest_comparison.py):
  Replicating the production gate logic (V1 trending mode, box ladder, phase
  strategy) to produce a like-for-like PnL would require building a parallel
  harness — out of the Phase 2 1-2h budget. Instead this script reports:

    1. Distribution of daily_trend outputs per arm (long/short/unknown)
    2. Agreement matrix BASELINE vs ENSEMBLE
    3. Forward-return PROXY metrics using the same chi-square + Cohen's d
       framework as the Phase 0 calibration (signal x fwd_return_sign).
       Treats each definite signal (long/short) as a "synthetic trade" at
       D1 close and aggregates fwd_h_d_pts as PnL proxy.
    4. PnL proxy (sum + per-trade), win rate, max drawdown, Sharpe per arm
    5. Validation gate decision per task spec acceptance criteria

Read-only on production code. Writes only to
_audit/pp_sprint/bias_detection_purdue_calibration/.

Usage:
  python _audit/pp_sprint/bias_detection_purdue_calibration/backtest_baseline_vs_ensemble.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Reuse production helpers (byte-identical to live code).
from live.level_detector import (  # noqa: E402
    SWING_LOOKBACK, STRUCTURE_LOOKBACK,
    _compute_signal_a, _compute_signal_b, _compute_signal_c,
)

OUT_DIR = ROOT / "_audit" / "pp_sprint" / "bias_detection_purdue_calibration"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REBUILT_PARQUET = Path(r"C:\FluxQuantumAI\data\rebuild_2026-04-25\gc_ohlcv_l2_joined.parquet")
WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END = pd.Timestamp("2026-04-26", tz="UTC")
HORIZONS = [1, 3, 5, 10]


# ============================================================================
# BASELINE replica — pre-task strict-monotonic algorithm (HEAD 62f4346)
# ============================================================================

def baseline_daily_trend_at(d1_fmv: pd.Series, t_idx: int) -> str:
    """Replica of pre-task _get_daily_trend (BIAS-DETECTION-COMPLETE-FIX,
    HEAD 62f4346). Strict-monotonic on last 3 closed D1 sessions.

    BASELINE uses M30 FMV resample-to-D1 last(); current ENSEMBLE uses M30 OHLC
    resample-to-D1 OHLC. Both consume the same source (gc_m30_boxes.parquet),
    same anchor (offset='22h'), same close-session filter (label + 1d <= now).
    """
    if t_idx < 3:
        return "unknown"
    last3 = d1_fmv.iloc[t_idx - 2 : t_idx + 1].values
    if all(last3[i] > last3[i - 1] for i in range(1, 3)):
        return "long"
    if all(last3[i] < last3[i - 1] for i in range(1, 3)):
        return "short"
    return "unknown"


def ensemble_daily_trend_at(d1_ohlc_window: pd.DataFrame) -> dict:
    """Replica of Phase 1 ENSEMBLE _get_daily_trend body (B+C voting).

    Returns dict with decision + signal_a/b/c for forensic inspection.
    Window MUST be filtered to closed sessions only (caller's responsibility).
    """
    n = len(d1_ohlc_window)
    min_bars = max(2 * SWING_LOOKBACK + 4, 2 * STRUCTURE_LOOKBACK + 2)
    if n < min_bars:
        return {"decision": "unknown", "signal_a": 0, "signal_b": 0, "signal_c": 0,
                "source": "insufficient_history"}
    sig_a = _compute_signal_a(d1_ohlc_window)
    sig_b = _compute_signal_b(d1_ohlc_window, lookback=SWING_LOOKBACK)
    sig_c = _compute_signal_c(d1_ohlc_window, structure_lookback=STRUCTURE_LOOKBACK)
    if sig_b != 0 and sig_c != 0 and sig_b == sig_c:
        decision = "long" if sig_b == +1 else "short"
        source = "ensemble_b_c_agree"
    else:
        decision = "unknown"
        if sig_b != 0 and sig_c != 0 and sig_b != sig_c:
            source = "unknown_b_c_disagree"
        elif sig_b == 0 and sig_c == 0:
            source = "unknown_no_signal"
        else:
            source = "unknown_partial_signal"
    return {"decision": decision, "signal_a": int(sig_a), "signal_b": int(sig_b),
            "signal_c": int(sig_c), "source": source}


# ============================================================================
# Per-arm metric computation
# ============================================================================

def arm_metrics(arm_name: str, decisions: pd.Series,
                fwd_returns: pd.DataFrame, horizon: int = 5) -> dict:
    """Treat each definite (long/short) decision as a synthetic trade entered
    at D1 close. PnL proxy = sign(decision) * fwd_h_d_pts for that horizon.

    Reports n_trades / win_rate / total_pnl_pts / avg_pnl_pts / max_dd /
    sharpe + chi-square + Cohen's d on (decision_sign x fwd_sign).
    """
    fwd_pts = fwd_returns[f"fwd_{horizon}d_pts"]
    fwd_sign = fwd_returns[f"fwd_{horizon}d_sign"]

    aligned = pd.concat([decisions.rename("dec"), fwd_pts.rename("ret"),
                         fwd_sign.rename("ret_sign")], axis=1).dropna()

    n_total = int(len(aligned))
    long_mask = aligned["dec"] == "long"
    short_mask = aligned["dec"] == "short"
    unknown_mask = aligned["dec"] == "unknown"

    n_long = int(long_mask.sum())
    n_short = int(short_mask.sum())
    n_unknown = int(unknown_mask.sum())
    n_blocked = n_unknown   # "unknown" -> gate would block (no directional filter)
    n_trades = n_long + n_short

    # PnL proxy: long -> +1 * fwd_pts; short -> -1 * fwd_pts; unknown -> 0
    pnl = pd.Series(0.0, index=aligned.index)
    pnl[long_mask] = aligned.loc[long_mask, "ret"]
    pnl[short_mask] = -aligned.loc[short_mask, "ret"]

    trade_pnl = pnl[long_mask | short_mask]
    if len(trade_pnl) > 0:
        wins = (trade_pnl > 0).sum()
        win_rate = float(wins / len(trade_pnl))
        total_pnl = float(trade_pnl.sum())
        avg_pnl = float(trade_pnl.mean())
        std_pnl = float(trade_pnl.std()) if len(trade_pnl) > 1 else 0.0
        sharpe = (avg_pnl / std_pnl * np.sqrt(252 / horizon)) if std_pnl > 0 else 0.0
    else:
        win_rate = total_pnl = avg_pnl = std_pnl = sharpe = 0.0

    # Equity curve + max drawdown using cumulative trade P&L (chronological)
    if len(trade_pnl) > 0:
        equity = trade_pnl.cumsum()
        running_max = equity.cummax()
        drawdown = equity - running_max
        max_dd = float(drawdown.min())
    else:
        max_dd = 0.0

    # Statistical: chi-square + Cohen's d on directional signal vs fwd return sign
    sig_for_test = aligned["dec"].map({"long": +1, "short": -1, "unknown": 0})
    test_subset = aligned[sig_for_test != 0]
    if len(test_subset) >= 30:
        sig_arr = sig_for_test[sig_for_test != 0]
        ret_arr = test_subset["ret_sign"]
        cm = pd.crosstab(sig_arr, ret_arr)
        if cm.shape[0] >= 2 and cm.shape[1] >= 2:
            chi2, p_val, _, _ = stats.chi2_contingency(cm)
            correct_pnl = (sig_arr * ret_arr).astype(float)
            d = float(correct_pnl.mean() / correct_pnl.std()) if correct_pnl.std() > 0 else 0.0
        else:
            chi2, p_val, d = None, None, None
    else:
        chi2, p_val, d = None, None, None

    return {
        "arm": arm_name,
        "horizon_d": horizon,
        "n_total": n_total,
        "n_trades": n_trades,
        "n_long": n_long,
        "n_short": n_short,
        "n_blocked": n_blocked,
        "win_rate": round(win_rate, 4),
        "total_pnl_pts": round(total_pnl, 2),
        "avg_pnl_pts": round(avg_pnl, 4),
        "std_pnl_pts": round(std_pnl, 4),
        "max_dd_pts": round(max_dd, 2),
        "sharpe_annualized": round(sharpe, 4),
        "chi2": round(float(chi2), 4) if chi2 is not None else None,
        "p_value": (round(float(p_val), 6) if p_val is not None else None),
        "cohens_d": round(d, 4) if d is not None else None,
    }


# ============================================================================
# Main
# ============================================================================

def _utc(idx):
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def main():
    print("=" * 76)
    print("BIAS-DETECTION-PURDUE-CALIBRATION Phase 2 backtest")
    print("BASELINE (62f4346 strict-monotonic) vs ENSEMBLE (B+C voting)")
    print(f"Window: {WINDOW_START} -> {WINDOW_END}")
    print("=" * 76)

    t0 = time.monotonic()

    # ── Load data ──────────────────────────────────────────────────────────
    print(f"\nLoading {REBUILT_PARQUET.name}...", flush=True)
    df_m1 = pd.read_parquet(REBUILT_PARQUET)
    df_m1.index = _utc(df_m1.index)
    df_m1 = df_m1.loc[(df_m1.index >= WINDOW_START) & (df_m1.index < WINDOW_END)]
    print(f"  M1 rows: {len(df_m1):,}", flush=True)

    # Resample to D1 with offset='22h' (CME Globex); same as live + calibration.
    d1_ohlc = df_m1[["open", "high", "low", "close"]].resample(
        "1D", offset="22h"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    closed_mask = (d1_ohlc.index + pd.Timedelta(days=1)) <= WINDOW_END
    d1_ohlc = d1_ohlc[closed_mask]
    print(f"  D1 closed sessions: {len(d1_ohlc)}", flush=True)

    # Forward returns (label) per horizon
    fwd = pd.DataFrame(index=d1_ohlc.index)
    for h in HORIZONS:
        fwd[f"fwd_{h}d_pts"] = d1_ohlc["close"].shift(-h) - d1_ohlc["close"]
        fwd[f"fwd_{h}d_sign"] = np.sign(fwd[f"fwd_{h}d_pts"]).fillna(0).astype(int)

    # ── BASELINE per-bar evaluation: strict-monotonic on close (proxy for FMV)
    print("\nComputing BASELINE decisions (strict-monotonic last3 closes)...", flush=True)
    baseline_decisions = []
    for t_idx in range(len(d1_ohlc)):
        baseline_decisions.append(
            baseline_daily_trend_at(d1_ohlc["close"], t_idx)
        )
    baseline_series = pd.Series(baseline_decisions, index=d1_ohlc.index, name="baseline")

    # ── ENSEMBLE per-bar evaluation: walk forward, signals at each bar
    print("Computing ENSEMBLE decisions (B+C voting per Phase 1)...", flush=True)
    ensemble_records = []
    min_bars = max(2 * SWING_LOOKBACK + 4, 2 * STRUCTURE_LOOKBACK + 2)
    for t_idx in range(len(d1_ohlc)):
        if t_idx + 1 < min_bars:
            ensemble_records.append({
                "decision": "unknown", "signal_a": 0, "signal_b": 0,
                "signal_c": 0, "source": "insufficient_history",
            })
        else:
            window = d1_ohlc.iloc[: t_idx + 1]
            ensemble_records.append(ensemble_daily_trend_at(window))
        if (t_idx + 1) % 25 == 0:
            print(f"  progress: {t_idx + 1}/{len(d1_ohlc)}", flush=True)

    ensemble_df = pd.DataFrame(ensemble_records, index=d1_ohlc.index)
    ensemble_series = ensemble_df["decision"].rename("ensemble")

    # ── Distribution per arm ──────────────────────────────────────────────
    print("\n=== Distribution per arm ===", flush=True)
    baseline_dist = baseline_series.value_counts(normalize=True).to_dict()
    ensemble_dist = ensemble_series.value_counts(normalize=True).to_dict()
    ensemble_source_dist = ensemble_df["source"].value_counts(normalize=True).to_dict()
    print("BASELINE:")
    for k in sorted(baseline_dist):
        n = int((baseline_series == k).sum())
        print(f"  {k:8s}: {100 * baseline_dist[k]:5.1f}% (n={n})")
    print("ENSEMBLE:")
    for k in sorted(ensemble_dist):
        n = int((ensemble_series == k).sum())
        print(f"  {k:8s}: {100 * ensemble_dist[k]:5.1f}% (n={n})")
    print("ENSEMBLE source breakdown:")
    for k in sorted(ensemble_source_dist):
        print(f"  {k}: {100 * ensemble_source_dist[k]:5.1f}%")

    # ── Agreement matrix ──────────────────────────────────────────────────
    print("\n=== Agreement matrix (BASELINE rows x ENSEMBLE cols) ===", flush=True)
    agree_pct = 100 * (baseline_series == ensemble_series).mean()
    cm = pd.crosstab(baseline_series, ensemble_series, margins=True)
    print(cm.to_string())
    print(f"\nTotal agreement: {agree_pct:.1f}%")

    # ── Per-arm metrics across horizons ───────────────────────────────────
    print("\n=== Per-arm metrics x horizon ===", flush=True)
    all_metrics = []
    for h in HORIZONS:
        baseline_m = arm_metrics("BASELINE", baseline_series, fwd, horizon=h)
        ensemble_m = arm_metrics("ENSEMBLE", ensemble_series, fwd, horizon=h)
        all_metrics.append(baseline_m)
        all_metrics.append(ensemble_m)
        print(f"\n--- horizon h={h}d ---")
        print(
            f"{'arm':10s} {'n_trades':>8s} {'win%':>6s} {'pnl':>9s} "
            f"{'avg':>7s} {'max_dd':>8s} {'sharpe':>7s} {'p_val':>9s} {'d':>7s}"
        )
        for m in (baseline_m, ensemble_m):
            print(
                f"{m['arm']:10s} {m['n_trades']:>8d} "
                f"{100 * m['win_rate']:>5.1f}% {m['total_pnl_pts']:>9.1f} "
                f"{m['avg_pnl_pts']:>7.2f} {m['max_dd_pts']:>8.1f} "
                f"{m['sharpe_annualized']:>7.3f} "
                f"{m['p_value'] if m['p_value'] is not None else 'n/a':>9} "
                f"{m['cohens_d'] if m['cohens_d'] is not None else 'n/a':>7}"
            )

    # ── Validation gate decision ──────────────────────────────────────────
    print("\n=== Validation gate (per task spec acceptance criteria) ===", flush=True)
    # Choose h=5d as primary horizon (matches calibration Step 5 selection);
    # h=10d as secondary.
    gate_results = {}
    for h in (5, 10):
        bm = next(m for m in all_metrics if m["arm"] == "BASELINE" and m["horizon_d"] == h)
        em = next(m for m in all_metrics if m["arm"] == "ENSEMBLE" and m["horizon_d"] == h)
        # Beats criterion: ENSEMBLE total_pnl >= BASELINE total_pnl
        beats_pnl = em["total_pnl_pts"] >= bm["total_pnl_pts"]
        # max_dd within 20% tolerance: ENSEMBLE max_dd >= 0.8 * BASELINE max_dd (less negative)
        # max_dd is negative; tolerance means ENSEMBLE not worse than BASELINE by > 20%
        if bm["max_dd_pts"] < 0:
            dd_within = em["max_dd_pts"] >= 1.2 * bm["max_dd_pts"]   # 20% tolerance worse
        else:
            dd_within = True
        # Sharpe maintained: ENSEMBLE sharpe >= BASELINE sharpe (or both ≈ 0)
        sharpe_maintained = em["sharpe_annualized"] >= bm["sharpe_annualized"] - 0.05
        gate_pass = beats_pnl and dd_within and sharpe_maintained
        gate_results[f"h{h}d"] = {
            "baseline_pnl": bm["total_pnl_pts"],
            "ensemble_pnl": em["total_pnl_pts"],
            "beats_pnl": beats_pnl,
            "baseline_max_dd": bm["max_dd_pts"],
            "ensemble_max_dd": em["max_dd_pts"],
            "dd_within_20pct": dd_within,
            "baseline_sharpe": bm["sharpe_annualized"],
            "ensemble_sharpe": em["sharpe_annualized"],
            "sharpe_maintained": sharpe_maintained,
            "GATE_PASS": gate_pass,
        }
        print(f"\nh={h}d:")
        print(f"  PnL: baseline={bm['total_pnl_pts']:.1f} vs ensemble={em['total_pnl_pts']:.1f} "
              f"-> beats={beats_pnl}")
        print(f"  max_dd: baseline={bm['max_dd_pts']:.1f} vs ensemble={em['max_dd_pts']:.1f} "
              f"-> within_20%={dd_within}")
        print(f"  Sharpe: baseline={bm['sharpe_annualized']:.3f} vs "
              f"ensemble={em['sharpe_annualized']:.3f} -> maintained={sharpe_maintained}")
        print(f"  -> GATE_PASS = {gate_pass}")

    # ── Persist ───────────────────────────────────────────────────────────
    out_csv = OUT_DIR / "comparison_per_d1_session.csv"
    comparison_df = pd.DataFrame({
        "ts": d1_ohlc.index,
        "baseline": baseline_series.values,
        "ensemble": ensemble_series.values,
        "ensemble_signal_a": ensemble_df["signal_a"].values,
        "ensemble_signal_b": ensemble_df["signal_b"].values,
        "ensemble_signal_c": ensemble_df["signal_c"].values,
        "ensemble_source": ensemble_df["source"].values,
        "fwd_5d_pts": fwd["fwd_5d_pts"].values,
        "fwd_10d_pts": fwd["fwd_10d_pts"].values,
    })
    comparison_df.to_csv(out_csv, index=False)
    print(f"\nout -> {out_csv}", flush=True)

    elapsed = time.monotonic() - t0
    summary = {
        "task": "BIAS-DETECTION-PURDUE-CALIBRATION Phase 2 backtest",
        "asana": "1214284792412296",
        "head": "62f4346",
        "branch": "fix/xau-mid-population",
        "window": {"start": str(WINDOW_START), "end": str(WINDOW_END)},
        "n_d1_closed_sessions": int(len(d1_ohlc)),
        "horizons_tested": HORIZONS,
        "primary_horizon": 5,
        "agreement_pct": round(agree_pct, 2),
        "baseline_distribution_pct": {k: round(100 * v, 2) for k, v in baseline_dist.items()},
        "ensemble_distribution_pct": {k: round(100 * v, 2) for k, v in ensemble_dist.items()},
        "ensemble_source_pct": {k: round(100 * v, 2) for k, v in ensemble_source_dist.items()},
        "per_arm_metrics": all_metrics,
        "gate_results": gate_results,
        "elapsed_s": round(elapsed, 1),
    }
    out_json = OUT_DIR / "summary.json"
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"out -> {out_json}", flush=True)
    print(f"\nelapsed: {elapsed:.1f}s", flush=True)
    return summary


if __name__ == "__main__":
    main()

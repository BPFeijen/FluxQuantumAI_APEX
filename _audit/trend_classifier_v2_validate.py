"""P1.2 Phase 1 Path A — Validate current B+C ensemble vs 9.7 months L2.

Asana 1214332092113381.

Approach: walk the full 9.7m M30 parquet, resample to D1 (offset='22h'),
and at each closed bar compute Signal A/B/C using THE LIVE FUNCTIONS
(`live.level_detector._compute_signal_a/b/c`). This guarantees the backtest
exercises the exact same code as production.

5 computes:
  C1 — Ensemble agreement rate (% B==C nonzero / disagree / partial / both=0)
  C2 — Stratification (ATR bucket × session × monthly window)
  C3 — Per-signal forward accuracy (B alone, C alone, A diagnostic, ensemble)
  C4 — Current vs historical (last 30 days vs 9.7m baseline)
  C5 — Signal alignment patterns (3-way aligned vs split)

Output:
  _audit/trend_classifier_v2_validation.json  (raw)
  printed summary with all 5 computes for the audit doc

Read-only — uses LIVE level_detector functions; no edits.
"""
from __future__ import annotations

import json
import sys
import time as _time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import warnings
warnings.filterwarnings("ignore")

# Live functions — under test, not modified
from live.level_detector import (
    _compute_signal_a, _compute_signal_b, _compute_signal_c,
    SWING_LOOKBACK, STRUCTURE_LOOKBACK, _MIN_D1_BARS, CALIBRATION_VERSION,
)

PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
OUT_JSON = Path(r"C:\FluxQuantumAI\_audit\trend_classifier_v2_validation.json")

# 9.7-month L2 window per feedback_calibration_9months
WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END   = pd.Timestamp("2026-04-28", tz="UTC")

FORWARD_HORIZONS = {"h_1d": 1, "h_3d": 3, "h_5d": 5, "h_10d": 10}

# Current sample window for C4 (last 30 days)
CURRENT_DAYS = 30


def log(msg: str) -> None:
    print(f"[{_time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Step 1 — Load M30 + resample to D1 (matches live._get_daily_trend exactly)
# ---------------------------------------------------------------------------

def load_d1() -> pd.DataFrame:
    m30 = pd.read_parquet(PARQUET, columns=["open", "high", "low", "close"])
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30 = m30[(m30.index >= WINDOW_START) & (m30.index < WINDOW_END)]

    d1 = m30.resample("1D", offset="22h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna()
    log(f"M30 bars: {len(m30):,}; D1 sessions resampled: {len(d1):,}")
    return d1


# ---------------------------------------------------------------------------
# Step 2 — Walk D1 and compute signals at each closed bar
# ---------------------------------------------------------------------------

def compute_signals_history(d1: pd.DataFrame) -> pd.DataFrame:
    """For each D1 bar t (where t >= _MIN_D1_BARS-1), call the live
    _compute_signal_a/b/c on d1.iloc[:t+1]. Returns a DataFrame indexed by
    D1 timestamp with columns signal_a, signal_b, signal_c, ensemble.
    """
    rows = []
    n = len(d1)
    for t in range(_MIN_D1_BARS - 1, n):
        slice_t = d1.iloc[:t + 1]
        sa = _compute_signal_a(slice_t)
        sb = _compute_signal_b(slice_t, lookback=SWING_LOOKBACK)
        sc = _compute_signal_c(slice_t, structure_lookback=STRUCTURE_LOOKBACK)
        # Ensemble per live._get_daily_trend
        if sb != 0 and sc != 0 and sb == sc:
            ens = "long" if sb == 1 else "short"
        else:
            ens = "unknown"
        rows.append({
            "ts":         d1.index[t],
            "open":       float(slice_t.iloc[-1]["open"]),
            "high":       float(slice_t.iloc[-1]["high"]),
            "low":        float(slice_t.iloc[-1]["low"]),
            "close":      float(slice_t.iloc[-1]["close"]),
            "signal_a":   int(sa),
            "signal_b":   int(sb),
            "signal_c":   int(sc),
            "ensemble":   ens,
            # ATR proxy (true range over window of 14)
        })
    out = pd.DataFrame(rows).set_index("ts")

    # ATR(14) on D1 for stratification
    prev_c = out["close"].shift(1)
    tr = np.maximum(out["high"] - out["low"],
                     np.maximum((out["high"] - prev_c).abs(),
                                 (out["low"]  - prev_c).abs()))
    out["atr14"] = tr.rolling(14).mean()

    # Forward returns
    for label, h in FORWARD_HORIZONS.items():
        out[f"fwd_{label}"] = out["close"].shift(-h) - out["close"]

    log(f"computed signals for {len(out):,} D1 bars (skipped first {_MIN_D1_BARS - 1} for warmup)")
    return out


# ---------------------------------------------------------------------------
# C1 — Ensemble agreement rate
# ---------------------------------------------------------------------------

def compute_c1_agreement(df: pd.DataFrame) -> dict:
    n = len(df)
    nb_zero = int((df["signal_b"] == 0).sum())
    nc_zero = int((df["signal_c"] == 0).sum())

    agree = (df["signal_b"] != 0) & (df["signal_c"] != 0) & (df["signal_b"] == df["signal_c"])
    disagree = (df["signal_b"] != 0) & (df["signal_c"] != 0) & (df["signal_b"] != df["signal_c"])
    partial = ((df["signal_b"] == 0) ^ (df["signal_c"] == 0))   # XOR — exactly one is zero
    both_zero = (df["signal_b"] == 0) & (df["signal_c"] == 0)

    out = {
        "n_sessions":         n,
        "n_b_zero":           nb_zero,
        "n_c_zero":           nc_zero,
        "n_agreement":        int(agree.sum()),
        "n_disagreement":     int(disagree.sum()),
        "n_partial_signal":   int(partial.sum()),
        "n_both_zero":        int(both_zero.sum()),
        "pct_agreement":      round(100 * agree.sum() / n, 2),
        "pct_disagreement":   round(100 * disagree.sum() / n, 2),
        "pct_partial":        round(100 * partial.sum() / n, 2),
        "pct_both_zero":      round(100 * both_zero.sum() / n, 2),
        "ensemble_distribution": dict(df["ensemble"].value_counts()),
        "ensemble_pct": {k: round(100 * v / n, 2) for k, v in df["ensemble"].value_counts().items()},
    }
    return out


# ---------------------------------------------------------------------------
# C2 — Stratification
# ---------------------------------------------------------------------------

def compute_c2_stratification(df: pd.DataFrame) -> dict:
    df = df.copy()
    # ATR bucket
    p33 = df["atr14"].quantile(0.33)
    p66 = df["atr14"].quantile(0.66)
    df["atr_bucket"] = pd.cut(df["atr14"], bins=[-np.inf, p33, p66, np.inf],
                                labels=["low_vol", "med_vol", "high_vol"])
    # Monthly window
    df["month"] = df.index.to_period("M").astype(str)

    def _agreement_stats(sub: pd.DataFrame) -> dict:
        if len(sub) == 0:
            return {"n": 0}
        agree = (sub["signal_b"] != 0) & (sub["signal_c"] != 0) & (sub["signal_b"] == sub["signal_c"])
        unk = (sub["ensemble"] == "unknown")
        return {
            "n": int(len(sub)),
            "pct_agreement": round(100 * agree.sum() / len(sub), 1),
            "pct_unknown":   round(100 * unk.sum() / len(sub), 1),
        }

    by_atr = {}
    for bucket in ["low_vol", "med_vol", "high_vol"]:
        sub = df[df["atr_bucket"] == bucket]
        by_atr[bucket] = _agreement_stats(sub)

    by_month = {}
    for month, sub in df.groupby("month"):
        by_month[month] = _agreement_stats(sub)

    return {
        "atr_thresholds": {"p33": float(p33), "p66": float(p66)},
        "by_atr_bucket":  by_atr,
        "by_month":       by_month,
    }


# ---------------------------------------------------------------------------
# C3 — Per-signal forward accuracy
# ---------------------------------------------------------------------------

def _signal_accuracy(signal: pd.Series, fwd: pd.Series) -> dict:
    """Accuracy on bars where signal is non-zero AND fwd not NaN."""
    valid = (signal != 0) & fwd.notna()
    if valid.sum() == 0:
        return {"n": 0, "accuracy": np.nan}
    correct = ((signal == 1) & (fwd > 0)) | ((signal == -1) & (fwd < 0))
    n = int(valid.sum())
    acc = float(correct[valid].sum() / n)
    return {"n": n, "accuracy": round(acc, 4),
            "pct_long": round(100 * ((signal == 1) & valid).sum() / n, 1),
            "pct_short": round(100 * ((signal == -1) & valid).sum() / n, 1)}


def compute_c3_per_signal(df: pd.DataFrame) -> dict:
    out = {}
    for h_label in FORWARD_HORIZONS:
        fwd = df[f"fwd_{h_label}"]
        out[h_label] = {
            "signal_a":          _signal_accuracy(df["signal_a"], fwd),
            "signal_b":          _signal_accuracy(df["signal_b"], fwd),
            "signal_c":          _signal_accuracy(df["signal_c"], fwd),
            "ensemble_agree":    _ensemble_accuracy(df, fwd),
            "majority_vote":     _majority_vote_accuracy(df, fwd),
        }
    return out


def _ensemble_accuracy(df: pd.DataFrame, fwd: pd.Series) -> dict:
    """Accuracy when ensemble == long/short (both B and C agreed nonzero)."""
    valid = (df["ensemble"].isin(["long", "short"])) & fwd.notna()
    if valid.sum() == 0:
        return {"n": 0, "accuracy": np.nan}
    correct = ((df["ensemble"] == "long") & (fwd > 0)) | ((df["ensemble"] == "short") & (fwd < 0))
    n = int(valid.sum())
    acc = float(correct[valid].sum() / n)
    coverage = float(valid.sum() / fwd.notna().sum())
    return {"n": n, "accuracy": round(acc, 4),
            "coverage_of_valid_bars": round(coverage, 4)}


def _majority_vote_accuracy(df: pd.DataFrame, fwd: pd.Series) -> dict:
    """Counterfactual: instead of strict B+C agreement, use majority of {A,B,C}.
    Tie-break: A wins if 1-1-1 split; signal_x !=0 only counted when nonzero.
    """
    sa = df["signal_a"]
    sb = df["signal_b"]
    sc = df["signal_c"]
    score = sa + sb + sc   # in {-3,-2,-1,0,1,2,3}
    direction = pd.Series(0, index=df.index)
    direction[score >= 1] = 1
    direction[score <= -1] = -1
    valid = (direction != 0) & fwd.notna()
    if valid.sum() == 0:
        return {"n": 0, "accuracy": np.nan}
    correct = ((direction == 1) & (fwd > 0)) | ((direction == -1) & (fwd < 0))
    n = int(valid.sum())
    acc = float(correct[valid].sum() / n)
    coverage = float(valid.sum() / fwd.notna().sum())
    return {"n": n, "accuracy": round(acc, 4),
            "coverage_of_valid_bars": round(coverage, 4)}


# ---------------------------------------------------------------------------
# C4 — Current vs historical
# ---------------------------------------------------------------------------

def compute_c4_current_vs_historical(df: pd.DataFrame) -> dict:
    cutoff = df.index.max() - pd.Timedelta(days=CURRENT_DAYS)
    current = df[df.index >= cutoff]
    historical = df[df.index < cutoff]
    return {
        "cutoff_ts":         str(cutoff),
        "current_window":    {"n": len(current), "days": CURRENT_DAYS,
                                **compute_c1_agreement(current)} if len(current) else {"n": 0},
        "historical_window": {"n": len(historical),
                                **compute_c1_agreement(historical)} if len(historical) else {"n": 0},
        "delta_pct_unknown": round(
            100 * (current["ensemble"] == "unknown").sum() / max(len(current), 1)
            - 100 * (historical["ensemble"] == "unknown").sum() / max(len(historical), 1),
            2,
        ) if len(current) and len(historical) else None,
    }


# ---------------------------------------------------------------------------
# C5 — Signal alignment patterns
# ---------------------------------------------------------------------------

def compute_c5_alignment(df: pd.DataFrame) -> dict:
    n = len(df)
    sa = df["signal_a"]; sb = df["signal_b"]; sc = df["signal_c"]

    abc_aligned_pos = ((sa == 1) & (sb == 1) & (sc == 1))
    abc_aligned_neg = ((sa == -1) & (sb == -1) & (sc == -1))
    abc_aligned     = abc_aligned_pos | abc_aligned_neg

    bc_aligned_a_oppose = ((sb == sc) & (sb != 0) & (sa != 0) & (sa != sb))
    a_aligned_with_b_only = ((sa == sb) & (sa != 0) & (sa != sc))
    a_aligned_with_c_only = ((sa == sc) & (sa != 0) & (sa != sb))
    all_disagree = (sa != sb) & (sa != sc) & (sb != sc) & (sa != 0) & (sb != 0) & (sc != 0)

    # How accurate is B==C agreement when A AGREES vs A OPPOSES?
    fwd5 = df["fwd_h_5d"]
    bc_agree_mask = (sb != 0) & (sc != 0) & (sb == sc)

    a_agrees_too = bc_agree_mask & (sa == sb)
    a_neutral    = bc_agree_mask & (sa == 0)
    a_opposes    = bc_agree_mask & (sa != 0) & (sa != sb)

    def _acc_when(mask):
        valid = mask & fwd5.notna()
        if valid.sum() == 0:
            return {"n": 0, "accuracy": np.nan}
        correct = ((sb == 1) & (fwd5 > 0)) | ((sb == -1) & (fwd5 < 0))
        n = int(valid.sum())
        return {"n": n, "accuracy": round(float(correct[valid].sum() / n), 4)}

    return {
        "abc_aligned_long":   {"n": int(abc_aligned_pos.sum()), "pct": round(100 * abc_aligned_pos.sum() / n, 2)},
        "abc_aligned_short":  {"n": int(abc_aligned_neg.sum()), "pct": round(100 * abc_aligned_neg.sum() / n, 2)},
        "abc_aligned_total":  {"n": int(abc_aligned.sum()), "pct": round(100 * abc_aligned.sum() / n, 2)},
        "bc_agree_a_oppose":  {"n": int(bc_aligned_a_oppose.sum()), "pct": round(100 * bc_aligned_a_oppose.sum() / n, 2)},
        "a_b_only":           {"n": int(a_aligned_with_b_only.sum()), "pct": round(100 * a_aligned_with_b_only.sum() / n, 2)},
        "a_c_only":           {"n": int(a_aligned_with_c_only.sum()), "pct": round(100 * a_aligned_with_c_only.sum() / n, 2)},
        "all_three_disagree": {"n": int(all_disagree.sum()), "pct": round(100 * all_disagree.sum() / n, 2)},
        "ensemble_acc_h5_when_a_agrees":  _acc_when(a_agrees_too),
        "ensemble_acc_h5_when_a_neutral": _acc_when(a_neutral),
        "ensemble_acc_h5_when_a_opposes": _acc_when(a_opposes),
    }


# ---------------------------------------------------------------------------
# Step 3 — Main
# ---------------------------------------------------------------------------

def main():
    log("=" * 70)
    log("P1.2 PHASE 1 PATH A — TREND_CLASSIFIER_v2 VALIDATION")
    log("=" * 70)
    log(f"window: {WINDOW_START} -> {WINDOW_END}")
    log(f"calibration_version: {CALIBRATION_VERSION}")
    log(f"params: SWING_LOOKBACK={SWING_LOOKBACK}, STRUCTURE_LOOKBACK={STRUCTURE_LOOKBACK}, _MIN_D1_BARS={_MIN_D1_BARS}")
    log("")

    d1 = load_d1()
    df = compute_signals_history(d1)
    log("")

    log(">>> C1 — ENSEMBLE AGREEMENT RATE")
    c1 = compute_c1_agreement(df)
    log(f"  n_sessions={c1['n_sessions']}")
    log(f"  agreement (B==C, both nonzero): {c1['pct_agreement']:5.2f}%   (n={c1['n_agreement']})")
    log(f"  disagreement (B!=C, both nonzero): {c1['pct_disagreement']:5.2f}%   (n={c1['n_disagreement']})")
    log(f"  partial (one signal == 0): {c1['pct_partial']:5.2f}%   (n={c1['n_partial_signal']})")
    log(f"  both zero: {c1['pct_both_zero']:5.2f}%   (n={c1['n_both_zero']})")
    log(f"  ensemble_distribution: {c1['ensemble_distribution']}")
    log("")

    log(">>> C2 — STRATIFICATION")
    c2 = compute_c2_stratification(df)
    log(f"  ATR thresholds (p33/p66): {c2['atr_thresholds']}")
    for bucket, stats in c2["by_atr_bucket"].items():
        log(f"  {bucket:10}  n={stats.get('n',0):>4}  pct_agree={stats.get('pct_agreement','-')}%  pct_unknown={stats.get('pct_unknown','-')}%")
    log("  by_month (last 6):")
    months_sorted = sorted(c2["by_month"].keys())
    for m in months_sorted[-6:]:
        s = c2["by_month"][m]
        log(f"    {m}  n={s.get('n',0):>3}  pct_agree={s.get('pct_agreement','-')}%  pct_unknown={s.get('pct_unknown','-')}%")
    log("")

    log(">>> C3 — PER-SIGNAL FORWARD ACCURACY")
    c3 = compute_c3_per_signal(df)
    for h, sigs in c3.items():
        log(f"  {h}:")
        for k, v in sigs.items():
            cov = f" (cov={v.get('coverage_of_valid_bars','?')})" if "coverage_of_valid_bars" in v else ""
            log(f"     {k:18}  n={v.get('n',0):>4}  acc={v.get('accuracy','?')}{cov}")
    log("")

    log(">>> C4 — CURRENT vs HISTORICAL (last 30 days)")
    c4 = compute_c4_current_vs_historical(df)
    if c4['current_window'].get('n', 0) > 0:
        log(f"  current ({c4['current_window']['n']} sessions): pct_unknown={c4['current_window'].get('pct_disagreement', 0) + c4['current_window'].get('pct_partial', 0) + c4['current_window'].get('pct_both_zero', 0):.2f}%, pct_agreement={c4['current_window'].get('pct_agreement', 0):.2f}%")
    if c4['historical_window'].get('n', 0) > 0:
        log(f"  historical ({c4['historical_window']['n']} sessions): pct_unknown={c4['historical_window'].get('pct_disagreement', 0) + c4['historical_window'].get('pct_partial', 0) + c4['historical_window'].get('pct_both_zero', 0):.2f}%, pct_agreement={c4['historical_window'].get('pct_agreement', 0):.2f}%")
    log(f"  delta pct_unknown current vs historical: {c4.get('delta_pct_unknown')}")
    log("")

    log(">>> C5 — SIGNAL ALIGNMENT")
    c5 = compute_c5_alignment(df)
    log(f"  ABC all aligned long:    n={c5['abc_aligned_long']['n']:>4}  ({c5['abc_aligned_long']['pct']}%)")
    log(f"  ABC all aligned short:   n={c5['abc_aligned_short']['n']:>4}  ({c5['abc_aligned_short']['pct']}%)")
    log(f"  ABC all aligned total:   n={c5['abc_aligned_total']['n']:>4}  ({c5['abc_aligned_total']['pct']}%)")
    log(f"  BC agree, A opposes:     n={c5['bc_agree_a_oppose']['n']:>4}  ({c5['bc_agree_a_oppose']['pct']}%)")
    log(f"  All 3 mutually disagree: n={c5['all_three_disagree']['n']:>4}  ({c5['all_three_disagree']['pct']}%)")
    log("")
    log("  Ensemble (B==C) acc@5d:")
    log(f"    A also agrees:   {c5['ensemble_acc_h5_when_a_agrees']}")
    log(f"    A neutral (=0):  {c5['ensemble_acc_h5_when_a_neutral']}")
    log(f"    A opposes:       {c5['ensemble_acc_h5_when_a_opposes']}")
    log("")

    OUT_JSON.write_text(json.dumps({
        "window_start": str(WINDOW_START),
        "window_end":   str(WINDOW_END),
        "calibration_version": CALIBRATION_VERSION,
        "params": {"swing_lb": SWING_LOOKBACK, "struct_lb": STRUCTURE_LOOKBACK, "min_d1_bars": _MIN_D1_BARS},
        "n_d1_sessions_evaluated": len(df),
        "C1_agreement":      c1,
        "C2_stratification": c2,
        "C3_per_signal_accuracy": c3,
        "C4_current_vs_historical": c4,
        "C5_signal_alignment":     c5,
    }, indent=2, default=str), encoding="utf-8")
    log(f"results written to {OUT_JSON}")


if __name__ == "__main__":
    main()

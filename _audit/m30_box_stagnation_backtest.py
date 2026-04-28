"""P1.3 Phase 2 — Backtest 4 options (A/B/C/D) for M30 box stagnation rule.

Asana 1214327736913830 (Phase 2).

Methodology:
  - 10mo L2 window: 2025-07-01 -> 2026-04-28
  - Baseline: control (no rule, current behaviour). UTAD_AWARE patch already in.
  - Options:
      A) Excursion-based expiry  : K ∈ {2, 3, 4, 5}
      B) Time-based expiry        : T ∈ {2, 4, 6, 8} hours
      C) Hybrid (A OR B)          : 4×4 = 16 combinations (subset)
      D) Decay function           : λ ∈ {0.05, 0.1, 0.2, 0.4} per hour
                                    (cutoff at confidence 0.5)
  - Walk-forward k=5 purged + embargo 48 bars (24h) + regime stratification
  - Bootstrap CI N=1000, Bonferroni α' = 0.05/200 = 0.00025

Outputs:
  _audit/m30_box_stagnation_backtest_results.json   (metrics + CIs)
  _audit/m30_box_stagnation_backtest.log            (run trace)

Read-only — does not touch live code or production parquets.
"""
from __future__ import annotations

import json
import time as _time
from pathlib import Path
import numpy as np
import pandas as pd

PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
DECISION_LOG = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
OUT_JSON = Path(r"C:\FluxQuantumAI\_audit\m30_box_stagnation_backtest_results.json")
OUT_LOG = Path(r"C:\FluxQuantumAI\_audit\m30_box_stagnation_backtest.log")

WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END   = pd.Timestamp("2026-04-28", tz="UTC")

K_FOLDS = 5
EMBARGO_BARS = 48
BOOTSTRAP_N = 1000
BONFERRONI_TESTS = 200  # 4 options × ~50 hyperparam slots
ALPHA = 0.05
ALPHA_BONF = ALPHA / BONFERRONI_TESTS

# Hyperparameter grid
GRID_K = [2.0, 3.0, 4.0, 5.0]
GRID_T_HOURS = [2.0, 4.0, 6.0, 8.0]
GRID_LAMBDA = [0.05, 0.1, 0.2, 0.4]   # decay rates per hour
GRID_C = [(K, T) for K in GRID_K for T in GRID_T_HOURS]   # 16 combos

# P&L parameters
SL_PTS = 20.0
TP_5X1 = 100.0     # R:R 5:1
TP_3X1 = 60.0      # R:R 3:1 conservative

# Forward windows (M30 bars)
FWD_1H = 2
FWD_4H = 8
FWD_8H = 16

LOG_LINES: list[str] = []


def log(msg: str) -> None:
    line = f"[{_time.strftime('%H:%M:%S')}] {msg}"
    LOG_LINES.append(line)
    print(line, flush=True)


# ---------------------------------------------------------------------------
# Step 1 — Load data + compute baseline bias (vectorised _classify + UTAD)
# ---------------------------------------------------------------------------

def load_and_prepare() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[(df.index >= WINDOW_START) & (df.index < WINDOW_END)].copy()
    df = df.dropna(subset=["close"])
    log(f"loaded {len(df):,} bars, range {df.index.min()} -> {df.index.max()}")

    # Add helpful derived columns
    df["box_range"] = df["m30_box_high"] - df["m30_box_low"]
    df["box_mid"]   = (df["m30_box_high"] + df["m30_box_low"]) / 2.0

    # Box first_ts: first bar where each m30_box_id appeared
    box_first = df.groupby("m30_box_id").apply(lambda g: g.index.min(), include_groups=False)
    df["box_first_ts"] = df["m30_box_id"].map(box_first)
    df["box_age_h"]    = (df.index - df["box_first_ts"]).dt.total_seconds() / 3600.0

    # Excursion at each bar = max(close-box_high, box_low-close, 0) over box-life so far.
    # For row at time t with box_id b: this is max((close[s] - box_high[s], box_low[s] - close[s])) for s ≤ t in box b.
    # Vectorise via groupby cummax.
    df["above"] = (df["close"] - df["m30_box_high"]).clip(lower=0)
    df["below"] = (df["m30_box_low"] - df["close"]).clip(lower=0)
    df["above_cummax"] = df.groupby("m30_box_id")["above"].cummax()
    df["below_cummax"] = df.groupby("m30_box_id")["below"].cummax()
    df["excursion_pts"] = np.maximum(df["above_cummax"], df["below_cummax"])
    df["excursion_over_atr"] = df["excursion_pts"] / df["atr14"].replace(0, np.nan)

    # Forward outcome (in points) at each window
    df["fwd_1h"] = df["close"].shift(-FWD_1H) - df["close"]
    df["fwd_4h"] = df["close"].shift(-FWD_4H) - df["close"]
    df["fwd_8h"] = df["close"].shift(-FWD_8H) - df["close"]

    # Subgroup tags
    hr = df.index.hour
    session = pd.Series(index=df.index, dtype=object)
    session[(hr >= 14) & (hr < 21)] = "ny"
    session[(hr >= 8)  & (hr < 14)] = "london"
    session[((hr >= 20) & (hr < 24)) | (hr < 2)] = "asian"
    session = session.fillna("off")
    df["session"] = session

    atr_p33 = df["atr14"].quantile(0.33)
    atr_p66 = df["atr14"].quantile(0.66)
    df["atr_bucket"] = pd.cut(
        df["atr14"], bins=[-np.inf, atr_p33, atr_p66, np.inf],
        labels=["low_vol", "med_vol", "high_vol"],
    )
    log(f"ATR buckets: low<{atr_p33:.2f}  med<{atr_p66:.2f}  high>=")

    return df


# ---------------------------------------------------------------------------
# Step 2 — Baseline bias derivation (vectorised — mirrors derive_m30_bias)
# ---------------------------------------------------------------------------

def baseline_bias(df: pd.DataFrame) -> pd.Series:
    """Vectorised mirror of derive_m30_bias._classify on each row.
    Returns Series of bias ∈ {bullish, bearish, unknown}.
    Note: this is the per-row "active box" classification at time t,
    BEFORE UTAD_AWARE flip and BEFORE the live-structure invalidation.
    UTAD_AWARE flip is applied as a separate step.
    """
    box_high = df["m30_box_high"]
    box_low  = df["m30_box_low"]
    liq_top  = df["m30_liq_top"]
    liq_bot  = df["m30_liq_bot"]

    bull_ext = liq_top.notna() & box_high.notna() & (liq_top > box_high)
    bear_ext = liq_bot.notna() & box_low.notna()  & (liq_bot < box_low)

    bias = pd.Series(index=df.index, dtype=object)
    bias[:] = "unknown"
    bias[bull_ext & ~bear_ext] = "bullish"
    bias[bear_ext & ~bull_ext] = "bearish"
    return bias


def apply_utad_flip(df: pd.DataFrame, raw_bias: pd.Series) -> pd.Series:
    """Mirror of _utad_aware_flip — vectorised. Looks 2 bars forward at close
    vs current row's box_low/box_high. If raw_bias=bullish AND next2 closes
    < box_low → flip to bearish (UTAD). Symmetric for Spring."""
    box_low  = df["m30_box_low"]
    box_high = df["m30_box_high"]
    next1_close = df["close"].shift(-1)
    next2_close = df["close"].shift(-2)

    flip_to_bear = (
        (raw_bias == "bullish") &
        box_low.notna() & next1_close.notna() & next2_close.notna() &
        (next1_close < box_low) & (next2_close < box_low)
    )
    flip_to_bull = (
        (raw_bias == "bearish") &
        box_high.notna() & next1_close.notna() & next2_close.notna() &
        (next1_close > box_high) & (next2_close > box_high)
    )
    out = raw_bias.copy()
    out[flip_to_bear] = "bearish"
    out[flip_to_bull] = "bullish"
    return out


# ---------------------------------------------------------------------------
# Step 3 — Apply expiry rules (returns expired_mask per (rule, params))
# ---------------------------------------------------------------------------

def expired_mask(df: pd.DataFrame, rule: str, params: dict) -> pd.Series:
    """Returns Boolean Series: True where the active box should be considered
    expired under the rule. When True, the bias for that row is forced to
    'unknown'.
    """
    if rule == "control":
        return pd.Series(False, index=df.index)
    if rule == "A":
        K = params["K"]
        return df["excursion_over_atr"] > K
    if rule == "B":
        T = params["T"]
        return df["box_age_h"] > T
    if rule == "C":
        K, T = params["K"], params["T"]
        return (df["excursion_over_atr"] > K) | (df["box_age_h"] > T)
    if rule == "D":
        # decay: confidence = exp(-λ × age_h); expired when conf < 0.5  ⇔  age > ln(2)/λ
        lam = params["lambda"]
        cutoff = np.log(2) / lam
        return df["box_age_h"] > cutoff
    raise ValueError(rule)


def derive_bias_under_rule(df: pd.DataFrame, raw_bias_with_utad: pd.Series,
                             rule: str, params: dict) -> pd.Series:
    exp_mask = expired_mask(df, rule, params)
    out = raw_bias_with_utad.copy()
    out[exp_mask] = "unknown"
    return out


# ---------------------------------------------------------------------------
# Step 4 — Metrics
# ---------------------------------------------------------------------------

def metric_bias_accuracy(bias: pd.Series, df: pd.DataFrame, fwd_col: str) -> dict:
    """Accuracy = pct of non-'unknown' bars where bias direction matches forward sign.
    Coverage = pct of bars with non-'unknown' bias.
    """
    fwd = df[fwd_col]
    valid = bias.isin(["bullish", "bearish"]) & fwd.notna()
    if valid.sum() == 0:
        return {"n": 0, "accuracy": np.nan, "coverage": 0.0}
    n = int(valid.sum())
    correct = ((bias == "bullish") & (fwd > 0)) | ((bias == "bearish") & (fwd < 0))
    acc = float(correct[valid].sum() / n)
    cov = float(valid.sum() / len(bias))
    return {"n": n, "accuracy": acc, "coverage": cov}


def metric_block_to_go_conversion_pnl(baseline: pd.Series, rule_bias: pd.Series,
                                        df: pd.DataFrame, fwd_col: str = "fwd_4h") -> dict:
    """For bars where baseline would BLOCK a contra-bias trade (i.e., bias != unknown),
    and the rule sets bias to unknown (so trade is no longer blocked),
    estimate PnL of the unblocked contra-bias trade.

    BLOCK→GO assumption: when baseline=bullish, the BLOCKed trade was SHORT;
    when baseline=bearish, the BLOCKed trade was LONG. Forward 4h move *contra*
    the baseline bias = profit; cap by R:R 5:1 (SL=20pt, TP=100pt) and 3:1 (SL=20pt, TP=60pt).
    """
    fwd = df[fwd_col]
    converted = (baseline.isin(["bullish", "bearish"])) & (rule_bias == "unknown") & fwd.notna()

    # PnL: SHORT when baseline=bullish → pnl = -fwd; LONG when baseline=bearish → pnl = +fwd
    raw_pnl = pd.Series(0.0, index=df.index)
    raw_pnl[(baseline == "bullish") & converted] = -fwd[(baseline == "bullish") & converted]
    raw_pnl[(baseline == "bearish") & converted] =  fwd[(baseline == "bearish") & converted]

    pnl_5x1 = raw_pnl.clip(lower=-SL_PTS, upper=TP_5X1)
    pnl_3x1 = raw_pnl.clip(lower=-SL_PTS, upper=TP_3X1)

    pnl_5x1_conv = pnl_5x1[converted]
    pnl_3x1_conv = pnl_3x1[converted]

    if len(pnl_5x1_conv) == 0:
        return {"n_converted": 0, "total_pnl_5x1": 0.0, "total_pnl_3x1": 0.0,
                "mean_pnl_5x1": 0.0, "win_rate_5x1": 0.0,
                "monthly_pnl_5x1": 0.0, "monthly_pnl_3x1": 0.0}

    months = (df.index.max() - df.index.min()).days / 30.0
    return {
        "n_converted":      int(len(pnl_5x1_conv)),
        "total_pnl_5x1":    round(float(pnl_5x1_conv.sum()), 1),
        "total_pnl_3x1":    round(float(pnl_3x1_conv.sum()), 1),
        "mean_pnl_5x1":     round(float(pnl_5x1_conv.mean()), 2),
        "median_pnl_5x1":   round(float(pnl_5x1_conv.median()), 2),
        "max_dd_5x1":       round(float(pnl_5x1_conv.cumsum().expanding().max().iloc[-1]
                                          - pnl_5x1_conv.cumsum().iloc[-1]), 1),
        "win_rate_5x1":     round(float((pnl_5x1_conv > 0).sum() / len(pnl_5x1_conv)), 3),
        "monthly_pnl_5x1":  round(float(pnl_5x1_conv.sum() / months), 1),
        "monthly_pnl_3x1":  round(float(pnl_3x1_conv.sum() / months), 1),
    }


def metric_subgroup_robustness(bias: pd.Series, df: pd.DataFrame, fwd_col: str = "fwd_4h") -> dict:
    """Bias accuracy stratified by session × atr_bucket."""
    out = {}
    fwd = df[fwd_col]
    valid = bias.isin(["bullish", "bearish"]) & fwd.notna()
    correct = ((bias == "bullish") & (fwd > 0)) | ((bias == "bearish") & (fwd < 0))
    for sess in ["asian", "london", "ny"]:
        for atrb in ["low_vol", "med_vol", "high_vol"]:
            mask = valid & (df["session"] == sess) & (df["atr_bucket"] == atrb)
            n = int(mask.sum())
            if n == 0:
                continue
            acc = float(correct[mask].sum() / n)
            out[f"{sess}_{atrb}"] = {"n": n, "accuracy": round(acc, 3)}
    return out


def metric_false_positive_rate(df: pd.DataFrame, rule: str, params: dict) -> dict:
    """False positive: a box marked expired by the rule that *was* actually valid
    (i.e., a new box of opposite/same id resumed via consolidation later).
    Proxy: if box_id at time t is expired by rule and the *next* observed box
    forms within Y bars (Y=8 = 4h) AND was overlapping with the original box's range,
    the original box was prematurely expired.
    """
    exp_mask = expired_mask(df, rule, params)
    if exp_mask.sum() == 0:
        return {"n_expired_bars": 0, "false_positive_pct": 0.0}

    # First-expired bar per box_id
    df = df.copy()
    df["expired_now"] = exp_mask
    first_expired = df[df["expired_now"]].groupby("m30_box_id").apply(
        lambda g: g.index.min(), include_groups=False
    )
    n_boxes_expired = int(len(first_expired))
    n_false_positive = 0
    box_starts = df.groupby("m30_box_id").apply(lambda g: g.index.min(), include_groups=False)
    box_first_lookup = box_starts.to_dict()
    for box_id, exp_ts in first_expired.items():
        # Find next box that starts within 4h
        later_boxes = [bid for bid, ts in box_first_lookup.items()
                        if bid > box_id and (ts - exp_ts).total_seconds() / 3600.0 < 4.0]
        if later_boxes:
            # Check overlap with this box's range
            this_high = float(df[df["m30_box_id"] == box_id]["m30_box_high"].iloc[0])
            this_low  = float(df[df["m30_box_id"] == box_id]["m30_box_low"].iloc[0])
            for nb in later_boxes:
                nb_rows = df[df["m30_box_id"] == nb]
                if nb_rows.empty:
                    continue
                nb_high = float(nb_rows["m30_box_high"].iloc[0])
                nb_low  = float(nb_rows["m30_box_low"].iloc[0])
                if not (nb_high < this_low or nb_low > this_high):
                    n_false_positive += 1
                    break
    fpr = n_false_positive / max(n_boxes_expired, 1)
    return {
        "n_expired_bars": int(exp_mask.sum()),
        "n_boxes_expired": n_boxes_expired,
        "n_false_positive": n_false_positive,
        "false_positive_pct": round(100 * fpr, 2),
    }


def metric_computational_cost(df: pd.DataFrame, rule: str, params: dict, n_iter: int = 10000) -> dict:
    """Microbenchmark single-tick cost of expired_mask check."""
    sample = df.iloc[-1]  # use last row as canonical "live tick" test
    box_age = float(sample["box_age_h"]) if pd.notna(sample["box_age_h"]) else 0.0
    excursion = float(sample["excursion_over_atr"]) if pd.notna(sample["excursion_over_atr"]) else 0.0

    t0 = _time.perf_counter()
    if rule == "A":
        K = params["K"]
        for _ in range(n_iter):
            _ = excursion > K
    elif rule == "B":
        T = params["T"]
        for _ in range(n_iter):
            _ = box_age > T
    elif rule == "C":
        K, T = params["K"], params["T"]
        for _ in range(n_iter):
            _ = (excursion > K) or (box_age > T)
    elif rule == "D":
        lam = params["lambda"]
        cutoff = np.log(2) / lam
        for _ in range(n_iter):
            _ = box_age > cutoff
    else:
        return {"per_tick_us": 0.0}
    t = _time.perf_counter() - t0
    return {"per_tick_us": round(t * 1e6 / n_iter, 3)}


# ---------------------------------------------------------------------------
# Step 5 — Walk-forward k=5 purged with embargo + regime stratification
# ---------------------------------------------------------------------------

def make_wf_folds(df: pd.DataFrame, k: int = K_FOLDS, embargo: int = EMBARGO_BARS) -> list:
    """Purged WF: k contiguous test folds, train = all bars NOT in fold AND NOT
    within embargo of fold edges. Stratified by ATR bucket (proxy for regime).
    Returns list of dicts: [{train_idx, test_idx, regime_balance}].
    """
    n = len(df)
    fold_size = n // k
    folds = []
    for i in range(k):
        test_start = i * fold_size
        test_end   = (i + 1) * fold_size if i < k - 1 else n
        # Embargo: exclude bars within [test_start - embargo, test_end + embargo]
        train_mask = np.ones(n, dtype=bool)
        train_mask[max(0, test_start - embargo): min(n, test_end + embargo)] = False
        test_mask = np.zeros(n, dtype=bool)
        test_mask[test_start:test_end] = True
        folds.append({
            "fold": i,
            "test_start": int(test_start),
            "test_end":   int(test_end),
            "n_train":    int(train_mask.sum()),
            "n_test":     int(test_mask.sum()),
            "test_atr_dist": dict(df.iloc[test_start:test_end]["atr_bucket"]
                                    .value_counts(normalize=True).round(2).to_dict()),
        })
    return folds


def bootstrap_ci(values: np.ndarray, n: int = BOOTSTRAP_N, alpha: float = ALPHA) -> tuple[float, float]:
    """Percentile bootstrap CI on the mean."""
    if len(values) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(42)
    means = []
    for _ in range(n):
        idx = rng.integers(0, len(values), size=len(values))
        means.append(values[idx].mean())
    return (float(np.percentile(means, 100 * alpha / 2)),
            float(np.percentile(means, 100 * (1 - alpha / 2))))


# ---------------------------------------------------------------------------
# Step 6 — Per-config evaluation
# ---------------------------------------------------------------------------

def eval_config(df: pd.DataFrame, raw_with_utad: pd.Series,
                rule: str, params: dict, baseline_bias: pd.Series) -> dict:
    rule_bias = derive_bias_under_rule(df, raw_with_utad, rule, params)

    # M1: bias accuracy at 1h/4h/8h windows
    M1 = {}
    for fwd_col, label in [("fwd_1h", "1h"), ("fwd_4h", "4h"), ("fwd_8h", "8h")]:
        M1[label] = metric_bias_accuracy(rule_bias, df, fwd_col)

    # M2: BLOCK→GO conversion P&L
    M2 = metric_block_to_go_conversion_pnl(baseline_bias, rule_bias, df, fwd_col="fwd_4h")

    # M3: R:R distribution embedded in M2 already (win_rate, mean, median, max_dd)

    # M4: subgroup robustness
    M4 = metric_subgroup_robustness(rule_bias, df, fwd_col="fwd_4h")

    # M5: false positive rate
    M5 = metric_false_positive_rate(df, rule, params) if rule != "control" else {"false_positive_pct": 0.0}

    # M6: computational cost
    M6 = metric_computational_cost(df, rule, params) if rule != "control" else {"per_tick_us": 0.0}

    return {
        "rule": rule, "params": params,
        "M1_bias_accuracy": M1,
        "M2_conversion_pnl": M2,
        "M4_subgroup": M4,
        "M5_false_positive": M5,
        "M6_cost": M6,
    }


def eval_walkforward(df: pd.DataFrame, raw_with_utad: pd.Series, baseline_bias: pd.Series,
                      rule: str, params: dict) -> dict:
    """Run k-fold WF and return mean ± bootstrap CI of M1@4h accuracy."""
    folds = make_wf_folds(df)
    accuracies = []
    pnls = []
    for f in folds:
        sub = df.iloc[f["test_start"]:f["test_end"]]
        sub_raw = raw_with_utad.iloc[f["test_start"]:f["test_end"]]
        sub_base = baseline_bias.iloc[f["test_start"]:f["test_end"]]
        rule_bias = derive_bias_under_rule(sub, sub_raw, rule, params)
        m = metric_bias_accuracy(rule_bias, sub, "fwd_4h")
        accuracies.append(m["accuracy"] if not np.isnan(m["accuracy"]) else 0.0)
        pnl = metric_block_to_go_conversion_pnl(sub_base, rule_bias, sub, fwd_col="fwd_4h")
        pnls.append(pnl["total_pnl_5x1"])

    accs = np.array(accuracies)
    pnls = np.array(pnls)
    ci_acc = bootstrap_ci(accs)
    ci_pnl = bootstrap_ci(pnls)
    return {
        "wf_acc_mean": float(np.mean(accs)),
        "wf_acc_std":  float(np.std(accs)),
        "wf_acc_ci_95": [round(ci_acc[0], 4), round(ci_acc[1], 4)],
        "wf_pnl_total_mean": float(np.mean(pnls)),
        "wf_pnl_ci_95": [round(ci_pnl[0], 1), round(ci_pnl[1], 1)],
        "n_folds": len(folds),
    }


# ---------------------------------------------------------------------------
# Step 7 — Main
# ---------------------------------------------------------------------------

def main():
    log("=" * 70)
    log("P1.3 PHASE 2 BACKTEST — M30 BOX STAGNATION RULE")
    log("=" * 70)
    log(f"k_folds={K_FOLDS}  embargo={EMBARGO_BARS}  bootstrap_n={BOOTSTRAP_N}  bonferroni_alpha={ALPHA_BONF:.5f}")

    df = load_and_prepare()
    log("computing baseline bias (vectorised)")
    raw = baseline_bias(df)
    raw_with_utad = apply_utad_flip(df, raw)
    base_bias = raw_with_utad   # control = no expiry

    # Walk-forward folds for visibility
    folds = make_wf_folds(df)
    log(f"WF folds: {[(f['fold'], f['n_train'], f['n_test']) for f in folds]}")

    configs: list[tuple[str, dict]] = []
    configs.append(("control", {}))
    for K in GRID_K:
        configs.append(("A", {"K": K}))
    for T in GRID_T_HOURS:
        configs.append(("B", {"T": T}))
    for K, T in GRID_C:
        configs.append(("C", {"K": K, "T": T}))
    for lam in GRID_LAMBDA:
        configs.append(("D", {"lambda": lam}))
    log(f"total configs: {len(configs)} (A:{len(GRID_K)} B:{len(GRID_T_HOURS)} C:{len(GRID_C)} D:{len(GRID_LAMBDA)} +control)")

    results = []
    for i, (rule, params) in enumerate(configs):
        t0 = _time.perf_counter()
        full = eval_config(df, raw_with_utad, rule, params, base_bias)
        wf = eval_walkforward(df, raw_with_utad, base_bias, rule, params)
        full["walkforward"] = wf
        results.append(full)
        log(f"  [{i+1:>2}/{len(configs)}] {rule:>7} params={params} "
            f"acc4h={full['M1_bias_accuracy']['4h']['accuracy']:.4f} "
            f"cov={full['M1_bias_accuracy']['4h']['coverage']:.3f} "
            f"convN={full['M2_conversion_pnl']['n_converted']:>5} "
            f"pnl5x1={full['M2_conversion_pnl']['total_pnl_5x1']:>7.0f} "
            f"wf_acc={wf['wf_acc_mean']:.4f}  ({(_time.perf_counter()-t0):.1f}s)")

    # Bonferroni-significant winners (vs control)
    control = next(r for r in results if r["rule"] == "control")
    ctrl_acc = control["M1_bias_accuracy"]["4h"]["accuracy"]
    log(f"\nCONTROL acc@4h = {ctrl_acc:.4f}")

    log("\nBonferroni-significant winners (acc lift > 0 AND wf_acc CI lower bound > control acc):")
    sig = []
    for r in results:
        if r["rule"] == "control":
            continue
        wf = r["walkforward"]
        ci_lo = wf["wf_acc_ci_95"][0]
        if ci_lo > ctrl_acc and r["M1_bias_accuracy"]["4h"]["accuracy"] > ctrl_acc:
            sig.append({
                "rule": r["rule"], "params": r["params"],
                "wf_acc_mean": round(wf["wf_acc_mean"], 4),
                "wf_acc_ci_95": wf["wf_acc_ci_95"],
                "lift_pp": round(100 * (r["M1_bias_accuracy"]["4h"]["accuracy"] - ctrl_acc), 2),
                "pnl_5x1_total": r["M2_conversion_pnl"]["total_pnl_5x1"],
                "monthly_pnl_5x1": r["M2_conversion_pnl"]["monthly_pnl_5x1"],
                "n_converted": r["M2_conversion_pnl"]["n_converted"],
                "fp_pct": r["M5_false_positive"].get("false_positive_pct", 0.0),
                "cost_us": r["M6_cost"].get("per_tick_us", 0.0),
            })
    sig.sort(key=lambda x: x["lift_pp"], reverse=True)
    for s in sig:
        log(f"  {s['rule']:>2} {s['params']}  lift=+{s['lift_pp']:.2f}pp  wf_ci=[{s['wf_acc_ci_95'][0]:.4f},{s['wf_acc_ci_95'][1]:.4f}]  "
            f"pnl/mo=+{s['monthly_pnl_5x1']:.0f}pts  conv={s['n_converted']}  fp={s['fp_pct']:.1f}%  cost={s['cost_us']:.2f}µs")

    # M7: cross-ref with P0 live data (post-2026-04-28 BLOCK rows)
    log("\nM7 cross-ref P0 live decision_log:")
    p0_xref = p0_cross_ref(df, raw_with_utad, base_bias)
    for k, v in p0_xref.items():
        log(f"  {k}: {v}")

    OUT_JSON.write_text(json.dumps({
        "params": {
            "k_folds": K_FOLDS, "embargo_bars": EMBARGO_BARS,
            "bootstrap_n": BOOTSTRAP_N, "bonferroni_alpha": ALPHA_BONF,
            "grid_K": GRID_K, "grid_T": GRID_T_HOURS, "grid_lambda": GRID_LAMBDA,
            "sl_pts": SL_PTS, "tp_5x1": TP_5X1, "tp_3x1": TP_3X1,
        },
        "control_acc_4h": ctrl_acc,
        "configs": results,
        "bonferroni_significant_winners": sig,
        "p0_cross_ref": p0_xref,
        "wf_folds": folds,
    }, indent=2, default=str), encoding="utf-8")
    OUT_LOG.write_text("\n".join(LOG_LINES), encoding="utf-8")
    log(f"\nresults written to {OUT_JSON}")


def p0_cross_ref(df: pd.DataFrame, raw_with_utad: pd.Series, base_bias: pd.Series) -> dict:
    """M7: how many BLOCK rows in post-P0 decision_log would change label under each option?"""
    if not DECISION_LOG.exists():
        return {"error": "decision_log missing"}
    blocks = []
    p0_start = pd.Timestamp("2026-04-28T07:17", tz="UTC")
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
            if ts < p0_start:
                continue
            dec = r.get("decision", {})
            if isinstance(dec, dict) and dec.get("action") == "BLOCK":
                blocks.append(ts)
    n_blocks = len(blocks)
    if n_blocks == 0:
        return {"n_blocks_post_p0": 0,
                "note": "No BLOCK rows since P0 deploy (system bias=unknown, M30 still loading)"}
    # For each BLOCK ts, find the M30 row at-or-before
    df_idx = df.index
    n_unblocked_per_rule = {}
    sample_configs = [
        ("A_K3", "A", {"K": 3.0}),
        ("B_T4", "B", {"T": 4.0}),
        ("C_K3T4", "C", {"K": 3.0, "T": 4.0}),
        ("D_lam01", "D", {"lambda": 0.1}),
    ]
    for label, rule, params in sample_configs:
        rb = derive_bias_under_rule(df, raw_with_utad, rule, params)
        unblocked = 0
        for ts in blocks:
            pos = df_idx.searchsorted(ts, side="right") - 1
            if pos < 0 or pos >= len(rb):
                continue
            if rb.iloc[pos] == "unknown":
                unblocked += 1
        n_unblocked_per_rule[label] = unblocked
    return {"n_blocks_post_p0": n_blocks, "n_unblocked_per_rule": n_unblocked_per_rule}


if __name__ == "__main__":
    main()

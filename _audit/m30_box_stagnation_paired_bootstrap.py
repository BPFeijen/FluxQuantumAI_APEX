"""Phase 2 supplementary — paired bootstrap delta CI for top configs vs control.

Proper Bonferroni hypothesis test: for each candidate rule,
H0: rule_acc - control_acc = 0
Test statistic: mean of paired per-bar correctness differences.
α' = 0.05/200 = 0.00025 → 99.975% CI

Reports also: regime composition per fold, fold accuracy table.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from m30_box_stagnation_backtest import (
    load_and_prepare, baseline_bias, apply_utad_flip, derive_bias_under_rule,
    make_wf_folds, K_FOLDS, EMBARGO_BARS,
)

ALPHA_BONF = 0.05 / 200          # 0.00025
CI_LO_PCT = 100 * ALPHA_BONF / 2          # 0.0125
CI_HI_PCT = 100 * (1 - ALPHA_BONF / 2)    # 99.9875
N_BOOTSTRAP = 5000

CANDIDATES = [
    ("control",   {}),
    ("A_K2",      ("A", {"K": 2.0})),
    ("A_K3",      ("A", {"K": 3.0})),
    ("B_T4",      ("B", {"T": 4.0})),
    ("C_K2_T4",   ("C", {"K": 2.0, "T": 4.0})),
    ("C_K3_T2",   ("C", {"K": 3.0, "T": 2.0})),
    ("C_K2_T8",   ("C", {"K": 2.0, "T": 8.0})),
    ("D_lam04",   ("D", {"lambda": 0.4})),
]

OUT = Path(r"C:\FluxQuantumAI\_audit\m30_box_stagnation_paired_bootstrap.json")


def per_bar_correctness(df: pd.DataFrame, bias: pd.Series, fwd_col: str = "fwd_4h") -> pd.Series:
    """Returns 1.0 if bias was correct on that bar, 0.0 if wrong, NaN if bias=unknown or no fwd."""
    fwd = df[fwd_col]
    s = pd.Series(np.nan, index=df.index)
    valid = bias.isin(["bullish", "bearish"]) & fwd.notna()
    correct = ((bias == "bullish") & (fwd > 0)) | ((bias == "bearish") & (fwd < 0))
    s[valid] = correct[valid].astype(float)
    return s


def paired_bootstrap_delta_ci(rule_correct: pd.Series, ctrl_correct: pd.Series,
                                n: int = N_BOOTSTRAP, seed: int = 42) -> dict:
    """Unpaired population-level bootstrap: resample bar indices with replacement;
    on each resample, recompute (acc_rule - acc_ctrl) treating NaN as 'no opinion'
    (bar dropped from accuracy denominator). This yields a proper CI on the
    difference of (correct/non_unknown) ratios.
    """
    n_bars = len(rule_correct)
    rule_arr = rule_correct.values  # NaN where rule says unknown, else 0/1
    ctrl_arr = ctrl_correct.values
    if n_bars == 0:
        return {"n_paired": 0, "delta_mean": np.nan, "ci_lo": np.nan, "ci_hi": np.nan,
                "p_value_two_sided_approx": np.nan, "significant_at_alpha_bonf": False}

    # Point estimate
    r_valid = ~np.isnan(rule_arr)
    c_valid = ~np.isnan(ctrl_arr)
    rule_acc = float(np.nansum(rule_arr) / max(r_valid.sum(), 1))
    ctrl_acc = float(np.nansum(ctrl_arr) / max(c_valid.sum(), 1))
    delta_mean = rule_acc - ctrl_acc

    # Bootstrap
    rng = np.random.default_rng(seed)
    deltas = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, n_bars, size=n_bars)
        r_s = rule_arr[idx]
        c_s = ctrl_arr[idx]
        rv = ~np.isnan(r_s)
        cv = ~np.isnan(c_s)
        if rv.sum() == 0 or cv.sum() == 0:
            deltas[i] = 0.0
            continue
        deltas[i] = (np.nansum(r_s) / rv.sum()) - (np.nansum(c_s) / cv.sum())
    ci_lo = float(np.percentile(deltas, CI_LO_PCT))
    ci_hi = float(np.percentile(deltas, CI_HI_PCT))
    p_pos = float((deltas <= 0).mean())
    p_two = 2 * min(p_pos, 1 - p_pos)
    significant = (ci_lo > 0) or (ci_hi < 0)
    return {
        "n_bars": int(n_bars),
        "n_rule_valid": int(r_valid.sum()),
        "n_ctrl_valid": int(c_valid.sum()),
        "rule_acc": round(rule_acc, 5),
        "ctrl_acc": round(ctrl_acc, 5),
        "delta_mean": round(delta_mean, 5),
        "ci_lo": round(ci_lo, 5),
        "ci_hi": round(ci_hi, 5),
        "p_value_two_sided_approx": round(p_two, 5),
        "alpha_bonf": ALPHA_BONF,
        "significant_at_alpha_bonf": bool(significant),
    }


def regime_composition_per_fold(df: pd.DataFrame, folds: list) -> pd.DataFrame:
    rows = []
    for f in folds:
        sub = df.iloc[f["test_start"]:f["test_end"]]
        atr_dist = sub["atr_bucket"].value_counts(normalize=True).round(3).to_dict()
        sess_dist = sub["session"].value_counts(normalize=True).round(3).to_dict()
        rows.append({
            "fold": f["fold"], "n": f["n_test"],
            "atr_low": atr_dist.get("low_vol", 0),
            "atr_med": atr_dist.get("med_vol", 0),
            "atr_high": atr_dist.get("high_vol", 0),
            "asian": sess_dist.get("asian", 0),
            "london": sess_dist.get("london", 0),
            "ny": sess_dist.get("ny", 0),
            "off": sess_dist.get("off", 0),
        })
    return pd.DataFrame(rows)


def per_fold_accuracy(df: pd.DataFrame, bias: pd.Series, folds: list, fwd_col: str = "fwd_4h") -> list:
    """Per-fold accuracy of bias on test bars."""
    accs = []
    fwd = df[fwd_col]
    for f in folds:
        sub_idx = slice(f["test_start"], f["test_end"])
        sub_b = bias.iloc[sub_idx]
        sub_f = fwd.iloc[sub_idx]
        valid = sub_b.isin(["bullish", "bearish"]) & sub_f.notna()
        if valid.sum() == 0:
            accs.append(np.nan)
            continue
        correct = ((sub_b == "bullish") & (sub_f > 0)) | ((sub_b == "bearish") & (sub_f < 0))
        accs.append(float(correct[valid].sum() / valid.sum()))
    return accs


def main():
    df = load_and_prepare()
    raw = baseline_bias(df)
    raw_with_utad = apply_utad_flip(df, raw)
    base = raw_with_utad
    folds = make_wf_folds(df)

    # Regime composition
    comp = regime_composition_per_fold(df, folds)
    print("\nREGIME COMPOSITION PER FOLD:")
    print(comp.to_string(index=False))

    # Control correctness series
    ctrl_correct = per_bar_correctness(df, base, "fwd_4h")
    ctrl_per_fold = per_fold_accuracy(df, base, folds, "fwd_4h")
    print(f"\nCONTROL per-fold acc@4h: {[round(x,4) for x in ctrl_per_fold]}")
    print(f"CONTROL mean: {np.nanmean(ctrl_per_fold):.4f}  std: {np.nanstd(ctrl_per_fold):.4f}")

    # Per candidate
    print(f"\nPaired-bootstrap delta CI vs control (Bonferroni alpha={ALPHA_BONF}):\n")
    print(f"{'config':>14} {'wf_acc_mean':>11} {'delta_pp':>10} {'ci_lo_pp':>10} {'ci_hi_pp':>10} {'p_two':>9} {'Bonf-sig':>10}")
    print("-" * 80)
    out_results = {"control_per_fold": ctrl_per_fold,
                    "regime_composition": comp.to_dict(orient="records"),
                    "candidates": []}
    for label, spec in CANDIDATES:
        if spec == {} or label == "control":
            rule_bias = base
        else:
            rule, params = spec
            rule_bias = derive_bias_under_rule(df, raw_with_utad, rule, params)
        rule_correct = per_bar_correctness(df, rule_bias, "fwd_4h")
        rule_per_fold = per_fold_accuracy(df, rule_bias, folds, "fwd_4h")
        wf_acc_mean = float(np.nanmean(rule_per_fold))
        if label == "control":
            delta = {"delta_mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0,
                      "significant_at_alpha_bonf": False,
                      "p_value_two_sided_approx": 1.0,
                      "n_rule_valid": 0, "n_ctrl_valid": 0}
        else:
            delta = paired_bootstrap_delta_ci(rule_correct, ctrl_correct)
        sig_marker = "**YES**" if delta["significant_at_alpha_bonf"] else "no"
        print(f"{label:>14} {wf_acc_mean:>11.4f} {delta['delta_mean']*100:>9.2f}pp "
              f"{delta['ci_lo']*100:>9.2f}pp {delta['ci_hi']*100:>9.2f}pp "
              f"{delta['p_value_two_sided_approx']:>9.4f} {sig_marker:>10}")
        out_results["candidates"].append({
            "label": label, "spec": spec,
            "wf_acc_mean": round(wf_acc_mean, 4),
            "wf_per_fold": [round(x, 4) for x in rule_per_fold],
            **delta,
        })

    # Pre-Bonferroni 95% CI for context (same unpaired population test)
    print(f"\nFor reference - same deltas at 95% CI (uncorrected):")
    rng = np.random.default_rng(42)
    n_bars_total = len(ctrl_correct)
    for label, spec in CANDIDATES:
        if label == "control":
            continue
        rule, params = spec
        rule_bias = derive_bias_under_rule(df, raw_with_utad, rule, params)
        rule_correct = per_bar_correctness(df, rule_bias, "fwd_4h")
        rule_arr = rule_correct.values
        ctrl_arr = ctrl_correct.values
        deltas95 = np.empty(N_BOOTSTRAP)
        for i in range(N_BOOTSTRAP):
            idx = rng.integers(0, n_bars_total, size=n_bars_total)
            r_s = rule_arr[idx]; c_s = ctrl_arr[idx]
            rv = ~np.isnan(r_s); cv = ~np.isnan(c_s)
            if rv.sum() == 0 or cv.sum() == 0:
                deltas95[i] = 0.0
                continue
            deltas95[i] = (np.nansum(r_s)/rv.sum()) - (np.nansum(c_s)/cv.sum())
        lo95, hi95 = np.percentile(deltas95, [2.5, 97.5])
        sig95 = (lo95 > 0) or (hi95 < 0)
        delta_pt = ((np.nansum(rule_arr) / max((~np.isnan(rule_arr)).sum(), 1)) -
                     (np.nansum(ctrl_arr) / max((~np.isnan(ctrl_arr)).sum(), 1)))
        print(f"  {label:>14}  delta={delta_pt*100:+.2f}pp  95%CI=[{lo95*100:+.2f},{hi95*100:+.2f}]pp  sig95={sig95}")

    OUT.write_text(json.dumps(out_results, indent=2, default=str), encoding="utf-8")
    print(f"\nresults written to {OUT}")


if __name__ == "__main__":
    main()

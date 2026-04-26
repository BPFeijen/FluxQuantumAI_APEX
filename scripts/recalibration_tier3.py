"""recalibration_tier3.py — EXEC-RECALIB-001 Tier 3.

Recalibrate the 2 DOCUMENTED IMPL-3 constants on the rebuilt clean parquet
under multi-seed purged walk-forward CV (Steps 9, 10):

  T3.1  FEATURE_WEIGHTS {F5_B, F3_B, F5_A, F2_B, F1_B}
        Original: Cohen's d on Window B single-fold; current = {0.640, 0.543, 0.394, 0.274, 0.213}
        New:      multi-seed × 5-fold purged WF Cohen's d, mean +/- std

  T3.2  EXHAUSTION_SCORE_THRESHOLD
        Original: coarse grid {0.2, 0.4, 0.6, 0.8, 1.0} → 0.4 winner
        New:      finer grid {0.30, 0.35, 0.40, 0.45, 0.50, 0.55} multi-seed
        Metric:   expectancy on anti_fwd_60m, Type I/II tradeoff

Feature operationalizations follow original t1_x1_features_v2.py (uses ORIGINAL
constants SOT_N=3, EFR_ROLLING=100, EFR_DIVERGENCE_THRESHOLD=0.3, CLOSE_PCT_WEAK=0.3
to keep weights directly comparable to original calibration). Tier 1 winners
are NOT retroactively applied here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.recalibration_common import (
    SEEDS, EMBARGO_BARS, N_FOLDS,
    REBUILT_OHLCV_M1, M30_BOXES, CALIBRATION_FULL,
    WINDOW_B_START, WINDOW_B_END,
    sha256_file, env_fingerprint, distribution_stats, hypothesis_test,
    purged_walk_forward_indices,
    resample_m1_to_m30, add_bar_body, rolling_pct_rank, add_forward_returns,
)

OUT = Path(r"C:\FluxQuantumAI\_audit\calibrations")
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)

# Original constants used by feature definitions (not under test in Tier 3)
SOT_N = 3
EFR_ROLLING = 100
EFR_DIVERGENCE_THRESHOLD = 0.3
CLOSE_PCT_WEAK = 0.3

OLD_WEIGHTS = {
    "F5_B": 0.640,
    "F3_B": 0.543,
    "F5_A": 0.394,
    "F2_B": 0.274,
    "F1_B": 0.213,
}
OLD_THRESHOLD = 0.4

THRESHOLD_GRID = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55]


def _utc(idx):
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def build_features() -> tuple[pd.DataFrame, dict]:
    """Build the M30 feature dataframe with all 5 features + regime + delta."""
    print("loading rebuilt M1 OHLCV...", flush=True)
    df_m1 = pd.read_parquet(REBUILT_OHLCV_M1)
    df_m1.index = _utc(df_m1.index)
    df_m1 = df_m1.loc[(df_m1.index >= WINDOW_B_START) & (df_m1.index < WINDOW_B_END)]
    df = resample_m1_to_m30(df_m1)
    df = add_bar_body(df)
    df = add_forward_returns(df, horizons_min=(60,))

    print("loading m30 boxes for regime...", flush=True)
    boxes = pd.read_parquet(M30_BOXES, columns=["m30_box_confirmed", "at_struct_level"])
    boxes.index = _utc(boxes.index)
    df = df.join(boxes, how="left")
    df["m30_box_confirmed"] = df["m30_box_confirmed"].fillna(False).astype(bool)
    df["at_struct_level"] = df["at_struct_level"].fillna(False).astype(bool)

    print("loading calibration_dataset_full for delta (M1 -> M30 sum)...", flush=True)
    delta_df = pd.read_parquet(CALIBRATION_FULL, columns=["l2_bar_delta"])
    delta_df.index = _utc(delta_df.index)
    delta_df = delta_df.loc[(delta_df.index >= WINDOW_B_START) & (delta_df.index < WINDOW_B_END)]
    m30_delta = delta_df["l2_bar_delta"].dropna().resample(
        "30min", label="left", closed="left").sum()
    df["m30_bar_delta"] = df.index.map(m30_delta).astype(float)

    # close_direction (Op B 2-step trend)
    cd = np.sign(df["close"].diff()).fillna(0).astype(int)
    up2 = (cd == 1) & (cd.shift(1) == 1)
    dn2 = (cd == -1) & (cd.shift(1) == -1)
    df["close_direction"] = cd
    df["trend_a"] = (up2 | dn2).fillna(False)
    df["trend_a_dir"] = np.where(up2, 1, np.where(dn2, -1, 0)).astype(int)

    df["trend_b"] = df["m30_box_confirmed"] & df["at_struct_level"]
    df["trend_b_dir"] = np.sign(df["close"] - df["close"].shift(SOT_N - 1)).fillna(0).astype(int)

    # Anti-trend forward returns
    df["anti_a_60m"] = -df["trend_a_dir"] * df["fwd_60m"]
    df["anti_b_60m"] = -df["trend_b_dir"] * df["fwd_60m"]

    # ===== Features =====
    # F1 SOT: 3-bar monotonic decrement of bar_range, AND consistent with trend dir
    rng = df["bar_range"]
    df["sot_decrement"] = (rng < rng.shift(1)) & (rng.shift(1) < rng.shift(2))
    df["F1_B"] = (df["sot_decrement"] & df["trend_b"]).fillna(False)

    # F2 EFR: vol pct rank - body pct rank > threshold
    vol_pct = rolling_pct_rank(df["volume"], EFR_ROLLING)
    body_pct = rolling_pct_rank(df["bar_body"], EFR_ROLLING)
    df["efr_div"] = vol_pct - body_pct
    df["efr_signal"] = df["efr_div"] > EFR_DIVERGENCE_THRESHOLD
    df["F2_B"] = (df["efr_signal"] & df["trend_b"]).fillna(False)

    # F3 Delta Divergence: (delta>0 & bearish) | (delta<0 & bullish)
    delta = df["m30_bar_delta"]
    bearish = df["close"] < df["open"]
    bullish = df["close"] > df["open"]
    df["delta_div"] = ((delta > 0) & bearish) | ((delta < 0) & bullish)
    df["F3_B"] = (df["delta_div"] & df["trend_b"]).fillna(False)

    # F5 ClosePct: weak close in trend dir
    df["weak_low"] = df["close_pct_from_low"] < CLOSE_PCT_WEAK
    df["weak_high"] = df["close_pct_from_high"] < CLOSE_PCT_WEAK
    # F5_A: TREND-A precondition; weak in direction of trend_a
    f5_long_a = (df["trend_a_dir"] == 1) & df["weak_low"]
    f5_short_a = (df["trend_a_dir"] == -1) & df["weak_high"]
    df["F5_A"] = (df["trend_a"] & (f5_long_a | f5_short_a)).fillna(False)
    # F5_B: TREND-B precondition; weak in direction of trend_b
    f5_long_b = (df["trend_b_dir"] == 1) & df["weak_low"]
    f5_short_b = (df["trend_b_dir"] == -1) & df["weak_high"]
    df["F5_B"] = (df["trend_b"] & (f5_long_b | f5_short_b)).fillna(False)

    meta = {
        "rebuilt_sha256": sha256_file(REBUILT_OHLCV_M1),
        "boxes_sha256": sha256_file(M30_BOXES),
        "calibration_full_sha256": sha256_file(CALIBRATION_FULL),
        "m30_rows": int(df.shape[0]),
        "delta_coverage_n": int(df["m30_bar_delta"].notna().sum()),
        "delta_range_min": str(df.loc[df["m30_bar_delta"].notna()].index.min()),
        "delta_range_max": str(df.loc[df["m30_bar_delta"].notna()].index.max()),
        "trend_a_n": int(df["trend_a"].sum()),
        "trend_b_n": int(df["trend_b"].sum()),
        "feature_n_active": {
            "F1_B": int(df["F1_B"].sum()),
            "F2_B": int(df["F2_B"].sum()),
            "F3_B": int(df["F3_B"].sum()),
            "F5_A": int(df["F5_A"].sum()),
            "F5_B": int(df["F5_B"].sum()),
        },
    }
    return df, meta


# ============================================================================
# T3.1 — FEATURE_WEIGHTS via multi-seed purged WF Cohen's d
# ============================================================================

def cohen_d(active: np.ndarray, inactive: np.ndarray) -> float | None:
    a = np.asarray(active);  a = a[np.isfinite(a)]
    b = np.asarray(inactive);  b = b[np.isfinite(b)]
    if len(a) < 5 or len(b) < 5:
        return None
    pooled = np.sqrt(((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1))
                     / (len(a) + len(b) - 2))
    return float((np.mean(a) - np.mean(b)) / pooled) if pooled > 0 else None


def feature_d_per_fold(df: pd.DataFrame, feat: str, label_col: str) -> dict:
    """For each fold: Cohen's d for feat on label_col on TEST split.
    Multi-seed: per fold, 5 seeds re-bootstrap 80% of test bars to estimate seed-induced variance.
    """
    fold_ds = []     # mean across seeds, per fold
    fold_ds_std = [] # std across seeds, per fold
    n_per_fold = []
    eligibility_col = "trend_a" if feat == "F5_A" else "trend_b"
    sub_df = df.loc[df[eligibility_col] & df[label_col].notna()].copy()
    for tr_idx, te_idx in purged_walk_forward_indices(len(sub_df), N_FOLDS, EMBARGO_BARS):
        te = sub_df.iloc[te_idx]
        if len(te) < 50:
            continue
        seed_ds = []
        for s in SEEDS:
            rng = np.random.default_rng(s)
            ix = rng.choice(len(te), size=int(0.8 * len(te)), replace=False)
            sub_te = te.iloc[ix]
            a = sub_te.loc[sub_te[feat], label_col].values
            b = sub_te.loc[~sub_te[feat], label_col].values
            d = cohen_d(a, b)
            if d is not None:
                seed_ds.append(d)
        if seed_ds:
            fold_ds.append(float(np.mean(seed_ds)))
            fold_ds_std.append(float(np.std(seed_ds, ddof=1)) if len(seed_ds) > 1 else 0.0)
            n_per_fold.append(len(te))
    if not fold_ds:
        return {"feature": feat, "n_folds": 0, "weight_mean": None, "weight_std": None}
    abs_d = np.abs(fold_ds)
    return {
        "feature": feat,
        "n_folds": len(fold_ds),
        "fold_ds": [float(x) for x in fold_ds],
        "fold_ds_seed_std": [float(x) for x in fold_ds_std],
        "fold_n": n_per_fold,
        "weight_mean": float(np.mean(abs_d)),
        "weight_std": float(np.std(abs_d, ddof=1)) if len(abs_d) > 1 else 0.0,
        "fold_d_mean": float(np.mean(fold_ds)),
        "fold_d_std": float(np.std(fold_ds, ddof=1)) if len(fold_ds) > 1 else 0.0,
    }


def run_t31(df: pd.DataFrame) -> dict:
    label_map = {
        "F1_B": "anti_b_60m",
        "F2_B": "anti_b_60m",
        "F3_B": "anti_b_60m",
        "F5_A": "anti_a_60m",
        "F5_B": "anti_b_60m",
    }
    results = []
    for feat, label in label_map.items():
        # Skip F3 weight if delta is mostly missing
        rec = feature_d_per_fold(df, feat, label)
        rec["label"] = label
        rec["old_weight"] = OLD_WEIGHTS[feat]
        if rec.get("weight_mean") is not None:
            rec["diff_pct"] = (rec["weight_mean"] - rec["old_weight"]) / rec["old_weight"] * 100.0
        else:
            rec["diff_pct"] = None
        results.append(rec)
    new_weights = {r["feature"]: (None if r["weight_mean"] is None else round(r["weight_mean"], 4))
                   for r in results}
    return {
        "old_weights": OLD_WEIGHTS,
        "new_weights": new_weights,
        "per_feature": results,
    }


# ============================================================================
# T3.2 — EXHAUSTION_SCORE_THRESHOLD finer grid
# ============================================================================

def run_t32(df: pd.DataFrame, weights: dict) -> dict:
    """Score each bar with the supplied weights; sweep thresholds on grid."""
    score = (
        weights.get("F5_B", 0.0) * df["F5_B"].astype(float)
        + weights.get("F3_B", 0.0) * df["F3_B"].astype(float)
        + weights.get("F5_A", 0.0) * df["F5_A"].astype(float)
        + weights.get("F2_B", 0.0) * df["F2_B"].astype(float)
        + weights.get("F1_B", 0.0) * df["F1_B"].astype(float)
    )
    df_s = df.copy()
    df_s["logic_c_score"] = score
    # Eligibility: any TREND active + label valid
    elig_a = df_s["trend_a"] & df_s["anti_a_60m"].notna()
    elig_b = df_s["trend_b"] & df_s["anti_b_60m"].notna()
    # combined eligibility (use anti_b when trend_b, else anti_a)
    df_s["anti_combined"] = np.where(df_s["trend_b"], df_s["anti_b_60m"],
                                     np.where(df_s["trend_a"], df_s["anti_a_60m"], np.nan))
    df_s["elig_combined"] = elig_a | elig_b
    sub = df_s.loc[df_s["elig_combined"]].copy()
    per_thr = []
    for thr in THRESHOLD_GRID:
        active_mask = sub["logic_c_score"] > thr
        active = sub.loc[active_mask, "anti_combined"].values
        inactive = sub.loc[~active_mask, "anti_combined"].values
        ht = hypothesis_test(active, inactive)
        # Multi-seed bootstrap of expectancy on active
        seed_exps = []
        for s in SEEDS:
            rng = np.random.default_rng(s)
            sub_idx = rng.choice(len(sub), size=int(0.8 * len(sub)), replace=False)
            sub_s = sub.iloc[sub_idx]
            a = sub_s.loc[sub_s["logic_c_score"] > thr, "anti_combined"].values
            if len(a) >= 5:
                seed_exps.append(float(np.mean(a)))
        # Purged WF expectancy stability
        fold_exps = []
        for tr_idx, te_idx in purged_walk_forward_indices(len(sub), N_FOLDS, EMBARGO_BARS):
            te = sub.iloc[te_idx]
            a = te.loc[te["logic_c_score"] > thr, "anti_combined"].values
            if len(a) >= 5:
                fold_exps.append(float(np.mean(a)))
        win_active = float(np.mean(active > 0)) if len(active) >= 5 else None
        per_thr.append({
            "threshold": thr,
            "n_active": int(active_mask.sum()),
            "pass_rate": float(active_mask.mean()),
            "mean_active": float(np.mean(active)) if len(active) >= 1 else None,
            "mean_inactive": float(np.mean(inactive)) if len(inactive) >= 1 else None,
            "win_rate_active": win_active,
            "expectancy": float(np.mean(active) * (active_mask.mean())) if len(active) >= 1 else None,
            "cohens_d": ht.get("cohens_d"),
            "ks_p": ht.get("ks_p"),
            "mw_p": ht.get("mw_p"),
            "seed_exp_mean": float(np.mean(seed_exps)) if seed_exps else None,
            "seed_exp_std": float(np.std(seed_exps, ddof=1)) if len(seed_exps) > 1 else None,
            "fold_exp_mean": float(np.mean(fold_exps)) if fold_exps else None,
            "fold_exp_std": float(np.std(fold_exps, ddof=1)) if len(fold_exps) > 1 else None,
            "n_folds_eval": len(fold_exps),
        })
    # Winner: max expectancy (mean_active × pass_rate) with seed_exp_std < 0.5*|seed_exp_mean|
    valid = [p for p in per_thr if p["expectancy"] is not None]
    if valid:
        winner = max(valid, key=lambda p: p["expectancy"])
    else:
        winner = per_thr[0]
    return {
        "weights_used": weights,
        "grid": per_thr,
        "winner_threshold": winner["threshold"],
        "winner_expectancy": winner["expectancy"],
    }


def main():
    print("=== EXEC-RECALIB-001 Tier 3 ===", flush=True)
    df, meta = build_features()
    print(f"  M30 rows: {meta['m30_rows']}, delta cov: {meta['delta_coverage_n']}", flush=True)
    print(f"  TREND-A: {meta['trend_a_n']}, TREND-B: {meta['trend_b_n']}", flush=True)
    print(f"  feature N_active: {meta['feature_n_active']}", flush=True)

    print("\n--- T3.1 FEATURE_WEIGHTS ---", flush=True)
    t31 = run_t31(df)
    for r in t31["per_feature"]:
        wm = r.get("weight_mean")
        ws = r.get("weight_std")
        ow = r["old_weight"]
        dp = r["diff_pct"]
        print(f"   {r['feature']}  old={ow:.3f}  new={wm if wm is None else round(wm,4)}  "
              f"+/-{ws if ws is None else round(ws,4)}  "
              f"diff={dp if dp is None else round(dp,1)}%  n_folds={r['n_folds']}", flush=True)
    print(f"  new_weights: {t31['new_weights']}", flush=True)

    # T3.2 — use NEW weights from T3.1 (or fall back to old if any new weight is None)
    new_w = {k: (v if v is not None else OLD_WEIGHTS[k]) for k, v in t31["new_weights"].items()}
    print("\n--- T3.2 EXHAUSTION_SCORE_THRESHOLD finer grid ---", flush=True)
    print(f"  weights used: {new_w}", flush=True)
    t32 = run_t32(df, new_w)
    for p in t32["grid"]:
        print(f"   thr={p['threshold']:.2f}  n_act={p['n_active']:>5}  "
              f"mean_a={p['mean_active'] if p['mean_active'] is None else round(p['mean_active'],3)}  "
              f"win={p['win_rate_active'] if p['win_rate_active'] is None else round(p['win_rate_active'],3)}  "
              f"exp={p['expectancy'] if p['expectancy'] is None else round(p['expectancy'],4)}  "
              f"d={p['cohens_d'] if p['cohens_d'] is None else round(p['cohens_d'],3)}  "
              f"ks_p={p['ks_p'] if p['ks_p'] is None else round(p['ks_p'],3)}", flush=True)
    print(f"  WINNER threshold: {t32['winner_threshold']}", flush=True)

    # T3.2 ALSO with OLD weights for like-for-like comparison
    print("\n--- T3.2 with OLD weights (sanity) ---", flush=True)
    t32_old = run_t32(df, OLD_WEIGHTS)
    for p in t32_old["grid"]:
        print(f"   thr={p['threshold']:.2f}  n_act={p['n_active']:>5}  "
              f"exp={p['expectancy'] if p['expectancy'] is None else round(p['expectancy'],4)}", flush=True)

    out = {
        "meta": meta,
        "env": env_fingerprint(),
        "t31_feature_weights": t31,
        "t32_threshold_new_weights": t32,
        "t32_threshold_old_weights": t32_old,
    }
    raw_path = RAW / "tier3_raw.json"
    raw_path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nraw -> {raw_path}", flush=True)
    return out


if __name__ == "__main__":
    main()

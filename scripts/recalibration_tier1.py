"""recalibration_tier1.py — EXEC-RECALIB-001 Tier 1.

Recalibrate the 3 INVENTED IMPL-2 constants per Purdue v2 12-step protocol on
the rebuilt clean M1 OHLCV parquet (resampled to M30, Window B Jul 2025+):

  T1.1  EFR_ROLLING                 (current 100, candidates {25,50,75,100,150,200})
  T1.2  EFR_DIVERGENCE_THRESHOLD    (current 0.3, candidates p70..p90)
  T1.3  CLOSE_PCT_WEAK              (current 0.3, candidates p20..p40)

Plus T2 — bug reconcile vs IMPL1_METHODOLOGY_AUDIT.md:113/123 (says 0.20).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.recalibration_common import (
    SEEDS, EMBARGO_BARS, N_FOLDS, BOOTSTRAP_N,
    REBUILT_OHLCV_M1, WINDOW_B_START, WINDOW_B_END,
    sha256_file, env_fingerprint, distribution_stats, choose_transform,
    bootstrap_ci, hypothesis_test, purged_walk_forward_indices,
    robust_thresholds, resample_m1_to_m30, add_bar_body, rolling_pct_rank,
    add_forward_returns, fmt_dist,
)

OUT_DIR = Path(r"C:\FluxQuantumAI\_audit\calibrations")
OUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR = OUT_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Step 11/12 — load + sklearn-Pipeline-compliant feature build
# ============================================================================

def _ensure_utc(idx):
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def load_window_b_m30() -> tuple[pd.DataFrame, dict]:
    """Read rebuilt M1 OHLCV (clean per DATA-002 P1.5), slice Window B, resample
    to M30, compute body+close_pct, then JOIN regime preconditions from
    gc_m30_boxes.parquet (m30_box_confirmed + at_struct_level).

    TREND-A (Op B per Villahermosa): 2 successive same-direction M30 closes.
    TREND-B: m30_box_confirmed AND at_struct_level (structural gate).

    Pipeline pattern (Step 12): bar_body / close_pct are bar-local (no leakage);
    EFR rolling pct rank is computed downstream and is causal (look-back only).
    """
    from scripts.recalibration_common import M30_BOXES
    df_m1 = pd.read_parquet(REBUILT_OHLCV_M1)
    df_m1.index = _ensure_utc(df_m1.index)
    df_m1 = df_m1.loc[(df_m1.index >= WINDOW_B_START) & (df_m1.index < WINDOW_B_END)]
    df_m30 = resample_m1_to_m30(df_m1)
    df_m30 = add_bar_body(df_m30)
    df_m30 = add_forward_returns(df_m30, horizons_min=(60,))
    # TREND-A (Op B): 2-step monotonic closes
    cd = np.sign(df_m30["close"].diff()).fillna(0).astype(int)
    up2 = (cd == 1) & (cd.shift(1) == 1)
    dn2 = (cd == -1) & (cd.shift(1) == -1)
    df_m30["trend_a"] = (up2 | dn2).fillna(False)
    df_m30["trend_a_dir"] = np.where(up2, 1, np.where(dn2, -1, 0)).astype(int)

    # TREND-B from boxes parquet
    boxes = pd.read_parquet(M30_BOXES, columns=["m30_box_confirmed", "at_struct_level"])
    boxes.index = _ensure_utc(boxes.index)
    df_m30 = df_m30.join(boxes, how="left")
    df_m30["m30_box_confirmed"] = df_m30["m30_box_confirmed"].fillna(False).astype(bool)
    df_m30["at_struct_level"] = df_m30["at_struct_level"].fillna(False).astype(bool)
    df_m30["trend_b"] = df_m30["m30_box_confirmed"] & df_m30["at_struct_level"]
    SOT_N = 3
    df_m30["trend_b_dir"] = np.sign(df_m30["close"] - df_m30["close"].shift(SOT_N - 1)).fillna(0).astype(int)

    # anti-trend forward returns per precondition
    df_m30["anti_fwd_60m_a"] = -df_m30["trend_a_dir"] * df_m30["fwd_60m"]
    df_m30["anti_fwd_60m_b"] = -df_m30["trend_b_dir"] * df_m30["fwd_60m"]

    meta = {
        "rebuilt_sha256": sha256_file(REBUILT_OHLCV_M1),
        "rebuilt_path": str(REBUILT_OHLCV_M1),
        "boxes_sha256": sha256_file(M30_BOXES),
        "boxes_path": str(M30_BOXES),
        "boxes_caveat": ("gc_m30_boxes.parquet is the OLD pipeline source for "
                         "regime gating (m30_box_confirmed, at_struct_level). "
                         "Rebuilt parquet is M1 OHLCV only — does not include "
                         "regime features. OHLCV used for feature computation "
                         "is rebuilt-clean; regime gating reuses pre-rebuild "
                         "boxes. Caveat documented in artifacts."),
        "m1_rows_in_window": int(df_m1.shape[0]),
        "m30_rows": int(df_m30.shape[0]),
        "window_b_start": str(WINDOW_B_START),
        "window_b_end": str(WINDOW_B_END),
        "m30_index_min": str(df_m30.index.min()),
        "m30_index_max": str(df_m30.index.max()),
        "trend_a_active_n": int(df_m30["trend_a"].sum()),
        "trend_b_active_n": int(df_m30["trend_b"].sum()),
    }
    return df_m30, meta


# ============================================================================
# T1.1 — EFR_ROLLING calibration
# ============================================================================

EFR_ROLLING_CANDIDATES = [25, 50, 75, 100, 150, 200]


def run_t11_efr_rolling(df: pd.DataFrame) -> dict:
    """Per-candidate window stability of EFR distribution + signal strength.

    Step 9 ensemble: per (window, seed) we resample 80% of bars (deterministic
    by seed) and recompute distribution stats; report mean +/- std across seeds.
    Step 10 purged WF: per fold, we recompute rolling pct rank on train+test
    contiguously (Pipeline-style: rolling window pct rank is fit-able inline,
    no train/test fit-then-transform divergence is possible since the rank is
    purely a function of the trailing observations).
    Performance metric: lower seed*fold variance of (median, skew) = preferred.
    Higher mean |Cohen's d| (active vs inactive on anti-trend fwd return) =
    preferred for SIGNAL strength.
    """
    n = len(df)
    results = []
    raw = {}
    for w in EFR_ROLLING_CANDIDATES:
        # Compute rolling pct rank on full window for diagnostic distribution.
        vol_pct = rolling_pct_rank(df["volume"], w)
        body_pct = rolling_pct_rank(df["bar_body"], w)
        efr = (vol_pct - body_pct).dropna()
        # Step 1
        dstats = distribution_stats(efr.values)
        # Step 2
        tlabel, _, tdiag = choose_transform(efr.values)
        # Step 8 robust
        rob = robust_thresholds(efr.values, [25, 50, 75, 80, 85, 90, 95])
        # Step 9 multi-seed: bootstrap medians
        seed_medians = []
        seed_skews = []
        rng_master = np.random.default_rng(0)
        x = efr.values
        for s in SEEDS:
            rng = np.random.default_rng(s)
            sub_idx = rng.choice(len(x), size=int(0.8 * len(x)), replace=False)
            seed_medians.append(float(np.median(x[sub_idx])))
            from scipy import stats as sps
            seed_skews.append(float(sps.skew(x[sub_idx], bias=False)))
        med_mean, med_std = float(np.mean(seed_medians)), float(np.std(seed_medians, ddof=1))
        skw_mean, skw_std = float(np.mean(seed_skews)), float(np.std(seed_skews, ddof=1))
        # Step 5/6 — Cohen's d for "high EFR vs low EFR" on anti-trend fwd return
        # Use TREND-B precondition (strongest in original calibration; F2_B/F5_B
        # had |d|>=0.5). Median split at p80 is diagnostic.
        df_w = df.copy()
        df_w["efr"] = vol_pct - body_pct
        elig = df_w["trend_b"] & df_w["efr"].notna() & df_w["anti_fwd_60m_b"].notna()
        sub = df_w.loc[elig]
        if len(sub) >= 50:
            cut = float(np.quantile(sub["efr"].values, 0.80))  # diagnostic cut p80
            active = sub.loc[sub["efr"] > cut, "anti_fwd_60m_b"].values
            inactive = sub.loc[sub["efr"] <= cut, "anti_fwd_60m_b"].values
            ht = hypothesis_test(active, inactive)
        else:
            ht = {"skipped": "insufficient_n", "n": int(len(sub))}
        # Step 10 purged WF: per fold compute median EFR per fold; report variance
        per_fold = []
        try:
            for tr, te in purged_walk_forward_indices(len(efr), N_FOLDS, EMBARGO_BARS):
                test_vals = efr.iloc[te].dropna().values
                if len(test_vals) >= 20:
                    per_fold.append(float(np.median(test_vals)))
        except ValueError:
            per_fold = []
        fold_var = float(np.var(per_fold, ddof=1)) if len(per_fold) >= 2 else None
        # Step 4 bootstrap CI on median
        ci = bootstrap_ci(efr.values, np.median, n=BOOTSTRAP_N, seed=42)
        results.append({
            "window": w,
            "dist": dstats,
            "transform_diag": tdiag,
            "robust_thresh": rob,
            "seed_median_mean": med_mean,
            "seed_median_std": med_std,
            "seed_skew_mean": skw_mean,
            "seed_skew_std": skw_std,
            "cohens_d_at_p80": ht.get("cohens_d"),
            "n_active_p80": ht.get("n_a"),
            "n_inactive_p80": ht.get("n_b"),
            "ks_p_at_p80": ht.get("ks_p"),
            "fold_median_variance": fold_var,
            "bootstrap_median_ci": ci,
        })
        raw[f"window_{w}"] = {"sample_efr_head": efr.head(5).round(6).tolist()}

    # Decision: pick window maximizing |cohens_d| AND minimizing fold_var.
    # Composite score: |d| - 0.5 * sqrt(fold_var) (heuristic; documented in artifact)
    def _score(r):
        d = abs(r["cohens_d_at_p80"] or 0.0)
        v = r["fold_median_variance"] or 0.0
        return d - 0.5 * (v ** 0.5)

    ranked = sorted(results, key=_score, reverse=True)
    winner = ranked[0]["window"]

    return {
        "candidates": results,
        "winner": winner,
        "score_function": "|cohens_d_at_p80| - 0.5 * sqrt(fold_median_variance)",
        "ranked": [r["window"] for r in ranked],
    }


# ============================================================================
# T1.2 — EFR_DIVERGENCE_THRESHOLD calibration (depends on T1.1 winner)
# ============================================================================

def run_t12_efr_threshold(df: pd.DataFrame, efr_rolling_winner: int) -> dict:
    vol_pct = rolling_pct_rank(df["volume"], efr_rolling_winner)
    body_pct = rolling_pct_rank(df["bar_body"], efr_rolling_winner)
    efr = (vol_pct - body_pct)
    df_w = df.copy()
    df_w["efr"] = efr
    elig = df_w["trend_b"] & df_w["efr"].notna() & df_w["anti_fwd_60m_b"].notna()
    sub = df_w.loc[elig].copy()
    # Step 1 dist
    dstats = distribution_stats(sub["efr"].values)
    # Step 3 percentile candidates
    cand_pcts = [70, 75, 80, 85, 90]
    cand_thresholds = robust_thresholds(sub["efr"].values, cand_pcts)
    # Per candidate: hypothesis test + multi-seed bootstrap on Cohen's d
    per_cand = []
    bonferroni = 0.05 / len(cand_pcts)
    for pct in cand_pcts:
        thr = cand_thresholds[f"p{pct}"]
        active = sub.loc[sub["efr"] > thr, "anti_fwd_60m_b"].values
        inactive = sub.loc[sub["efr"] <= thr, "anti_fwd_60m_b"].values
        ht = hypothesis_test(active, inactive)
        # Step 9: multi-seed bootstrap of Cohen's d with 80% subsample
        d_seeds = []
        precision_seeds = []  # frac of active where anti_fwd_60m > 0
        for s in SEEDS:
            rng = np.random.default_rng(s)
            sub_idx = rng.choice(len(sub), size=int(0.8 * len(sub)), replace=False)
            sub_s = sub.iloc[sub_idx]
            a = sub_s.loc[sub_s["efr"] > thr, "anti_fwd_60m_b"].values
            b = sub_s.loc[sub_s["efr"] <= thr, "anti_fwd_60m_b"].values
            ht_s = hypothesis_test(a, b)
            if "cohens_d" in ht_s:
                d_seeds.append(ht_s["cohens_d"])
            if len(a) >= 5:
                precision_seeds.append(float(np.mean(a > 0)))
        # Step 10 purged WF: per fold, compute Cohen's d on test
        fold_ds = []
        try:
            for tr, te in purged_walk_forward_indices(len(sub), N_FOLDS, EMBARGO_BARS):
                tdf = sub.iloc[te]
                a = tdf.loc[tdf["efr"] > thr, "anti_fwd_60m_b"].values
                b = tdf.loc[tdf["efr"] <= thr, "anti_fwd_60m_b"].values
                ht_t = hypothesis_test(a, b)
                if "cohens_d" in ht_t and not np.isnan(ht_t["cohens_d"]):
                    fold_ds.append(ht_t["cohens_d"])
        except ValueError:
            fold_ds = []
        per_cand.append({
            "percentile": pct,
            "threshold_value": thr,
            "n_active": ht.get("n_a"),
            "n_inactive": ht.get("n_b"),
            "mean_active": ht.get("mean_a"),
            "mean_inactive": ht.get("mean_b"),
            "cohens_d_full": ht.get("cohens_d"),
            "ks_p": ht.get("ks_p"),
            "mw_p": ht.get("mw_p"),
            "passes_bonferroni": (ht.get("ks_p") or 1.0) < bonferroni,
            "seed_d_mean": float(np.mean(d_seeds)) if d_seeds else None,
            "seed_d_std": float(np.std(d_seeds, ddof=1)) if len(d_seeds) > 1 else None,
            "precision_pos_seed_mean": float(np.mean(precision_seeds)) if precision_seeds else None,
            "fold_d_mean": float(np.mean(fold_ds)) if fold_ds else None,
            "fold_d_std": float(np.std(fold_ds, ddof=1)) if len(fold_ds) > 1 else None,
            "n_folds_eval": len(fold_ds),
        })
    # winner: highest seed_d_mean × passes Bonferroni
    valid = [c for c in per_cand if c["seed_d_mean"] is not None and c["passes_bonferroni"]]
    if valid:
        winner = max(valid, key=lambda c: c["seed_d_mean"])
    else:
        winner = max(per_cand, key=lambda c: (c["seed_d_mean"] or -np.inf))
    return {
        "rolling_winner_used": efr_rolling_winner,
        "dist": dstats,
        "candidates": per_cand,
        "bonferroni_alpha": bonferroni,
        "winner_pct": winner["percentile"],
        "winner_value": winner["threshold_value"],
    }


# ============================================================================
# T1.3 — CLOSE_PCT_WEAK calibration
# ============================================================================

def run_t13_close_pct_weak(df: pd.DataFrame) -> dict:
    """For each fwd-trend direction (LONG: weak-from-low; SHORT: weak-from-high)
    evaluate threshold candidates on close_pct.

    Operationalization (per impl2_features.py): F5 fires when
       LONG  trend & close_pct_from_low  < CLOSE_PCT_WEAK
       SHORT trend & close_pct_from_high < CLOSE_PCT_WEAK
    so smaller value = stronger weakness. Candidates {p20, p25, p30, p35, p40}.
    """
    df_w = df.copy()
    long_mask  = (df_w["trend_b_dir"] == +1)
    short_mask = (df_w["trend_b_dir"] == -1)
    df_w["close_pct_eff"] = np.where(
        long_mask, df_w["close_pct_from_low"],
        np.where(short_mask, df_w["close_pct_from_high"], np.nan),
    )
    elig = df_w["trend_b"] & df_w["close_pct_eff"].notna() & df_w["anti_fwd_60m_b"].notna()
    sub = df_w.loc[elig].copy()
    dstats = distribution_stats(sub["close_pct_eff"].values)
    cand_pcts = [20, 25, 30, 35, 40]
    cand_thresholds = robust_thresholds(sub["close_pct_eff"].values, cand_pcts)
    per_cand = []
    bonferroni = 0.05 / len(cand_pcts)
    for pct in cand_pcts:
        thr = cand_thresholds[f"p{pct}"]
        active = sub.loc[sub["close_pct_eff"] < thr, "anti_fwd_60m_b"].values
        inactive = sub.loc[sub["close_pct_eff"] >= thr, "anti_fwd_60m_b"].values
        ht = hypothesis_test(active, inactive)
        # Step 9 multi-seed bootstrap
        d_seeds = []
        for s in SEEDS:
            rng = np.random.default_rng(s)
            idx = rng.choice(len(sub), size=int(0.8 * len(sub)), replace=False)
            sub_s = sub.iloc[idx]
            a = sub_s.loc[sub_s["close_pct_eff"] < thr, "anti_fwd_60m_b"].values
            b = sub_s.loc[sub_s["close_pct_eff"] >= thr, "anti_fwd_60m_b"].values
            ht_s = hypothesis_test(a, b)
            if "cohens_d" in ht_s and not np.isnan(ht_s["cohens_d"]):
                d_seeds.append(ht_s["cohens_d"])
        # Step 10 purged WF
        fold_ds = []
        try:
            for tr, te in purged_walk_forward_indices(len(sub), N_FOLDS, EMBARGO_BARS):
                tdf = sub.iloc[te]
                a = tdf.loc[tdf["close_pct_eff"] < thr, "anti_fwd_60m_b"].values
                b = tdf.loc[tdf["close_pct_eff"] >= thr, "anti_fwd_60m_b"].values
                ht_t = hypothesis_test(a, b)
                if "cohens_d" in ht_t and not np.isnan(ht_t["cohens_d"]):
                    fold_ds.append(ht_t["cohens_d"])
        except ValueError:
            fold_ds = []
        per_cand.append({
            "percentile": pct,
            "threshold_value": thr,
            "n_active": ht.get("n_a"),
            "n_inactive": ht.get("n_b"),
            "mean_active": ht.get("mean_a"),
            "mean_inactive": ht.get("mean_b"),
            "cohens_d_full": ht.get("cohens_d"),
            "ks_p": ht.get("ks_p"),
            "mw_p": ht.get("mw_p"),
            "passes_bonferroni": (ht.get("ks_p") or 1.0) < bonferroni,
            "seed_d_mean": float(np.mean(d_seeds)) if d_seeds else None,
            "seed_d_std": float(np.std(d_seeds, ddof=1)) if len(d_seeds) > 1 else None,
            "fold_d_mean": float(np.mean(fold_ds)) if fold_ds else None,
            "fold_d_std": float(np.std(fold_ds, ddof=1)) if len(fold_ds) > 1 else None,
            "n_folds_eval": len(fold_ds),
        })
    valid = [c for c in per_cand if c["seed_d_mean"] is not None and c["passes_bonferroni"]]
    if valid:
        winner = max(valid, key=lambda c: c["seed_d_mean"])
    else:
        winner = max(per_cand, key=lambda c: (c["seed_d_mean"] or -np.inf))
    return {
        "dist": dstats,
        "candidates": per_cand,
        "bonferroni_alpha": bonferroni,
        "winner_pct": winner["percentile"],
        "winner_value": winner["threshold_value"],
    }


# ============================================================================
# Main
# ============================================================================

def main():
    print("=== EXEC-RECALIB-001 Tier 1 + T2 ===", flush=True)
    print("loading rebuilt parquet + resample...", flush=True)
    df, meta = load_window_b_m30()
    print(f"  M30 rows: {meta['m30_rows']}, range {meta['m30_index_min']} -> {meta['m30_index_max']}", flush=True)
    print(f"  TREND-A (Op B 2-step) active n: {meta['trend_a_active_n']}", flush=True)
    print(f"  TREND-B (box+struct) active n: {meta['trend_b_active_n']}", flush=True)

    print("\n--- T1.1 EFR_ROLLING ---", flush=True)
    t11 = run_t11_efr_rolling(df)
    print(f"  ranked: {t11['ranked']}", flush=True)
    print(f"  WINNER: {t11['winner']}", flush=True)
    for r in t11["candidates"]:
        d = r["cohens_d_at_p80"]; v = r["fold_median_variance"]
        print(f"   w={r['window']:4d}  d_p80={d if d is None else round(d,4):>7}  fold_var={v if v is None else round(v,6):>10}  seed_med={r['seed_median_mean']:.4f}+/-{r['seed_median_std']:.4f}", flush=True)

    print("\n--- T1.2 EFR_DIVERGENCE_THRESHOLD ---", flush=True)
    t12 = run_t12_efr_threshold(df, t11["winner"])
    print(f"  WINNER pct={t12['winner_pct']} value={t12['winner_value']:.4f}", flush=True)
    for c in t12["candidates"]:
        sd = c["seed_d_mean"]; ss = c["seed_d_std"]; pb = c["passes_bonferroni"]
        print(f"   p{c['percentile']:>2d}  thr={c['threshold_value']:.4f}  n_a={c['n_active']:>5}  d_seed={sd if sd is None else round(sd,4):>7}+/-{ss if ss is None else round(ss,4)}  bonf={pb}  ks_p={c['ks_p']}", flush=True)

    print("\n--- T1.3 CLOSE_PCT_WEAK ---", flush=True)
    t13 = run_t13_close_pct_weak(df)
    print(f"  WINNER pct={t13['winner_pct']} value={t13['winner_value']:.4f}", flush=True)
    for c in t13["candidates"]:
        sd = c["seed_d_mean"]; ss = c["seed_d_std"]; pb = c["passes_bonferroni"]
        print(f"   p{c['percentile']:>2d}  thr={c['threshold_value']:.4f}  n_a={c['n_active']:>5}  d_seed={sd if sd is None else round(sd,4):>7}+/-{ss if ss is None else round(ss,4)}  bonf={pb}  ks_p={c['ks_p']}", flush=True)

    out = {
        "meta": meta,
        "env": env_fingerprint(),
        "t11_efr_rolling": t11,
        "t12_efr_threshold": t12,
        "t13_close_pct_weak": t13,
    }
    raw_path = RAW_DIR / "tier1_raw.json"
    raw_path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nraw -> {raw_path}", flush=True)
    return out


if __name__ == "__main__":
    main()

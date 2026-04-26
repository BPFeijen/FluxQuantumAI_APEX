"""recalibration_tier4.py — EXEC-RECALIB-001 Tier 4 (RECOMMEND-ONLY).

NO code/settings.json edits per Option A scope. Output is .md only;
follow-up FOLLOW-RECALIB-001 will coordinate with ClaudeCode #1 to apply.

Constants:
  T4.1  vol_climax_multiplier      (current 0.68206 in settings.json:77, 0.682 fallback in event_processor.py:2607,3289)
        Trigger: |bar_delta_last| > rolling_std_30 * (1 + vol_climax_multiplier)
        Candidates: percentiles {p70, p75, p80, p85, p90} of (|delta_last|/rolling_std − 1) distribution

  T4.2  delta_weakening_threshold  (current 0.139486 in settings.json:79, 0.139486 fallback at 3263)
        Trigger: 1 − |recent10 / older10| > delta_weakening_threshold
        Candidates: percentiles {p15, p20, p25, p30} of weakening_rate distribution
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.recalibration_common import (
    CALIBRATION_FULL, WINDOW_B_START, WINDOW_B_END,
    sha256_file, env_fingerprint, distribution_stats, choose_transform,
    bootstrap_ci, robust_thresholds, purged_walk_forward_indices,
    EMBARGO_BARS, N_FOLDS, SEEDS,
)

OUT = Path(r"C:\FluxQuantumAI\_audit\calibrations")
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)


def _utc(idx):
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def load_m1_delta() -> tuple[pd.DataFrame, dict]:
    df = pd.read_parquet(CALIBRATION_FULL, columns=["l2_bar_delta", "close"])
    df.index = _utc(df.index)
    df = df.loc[(df.index >= WINDOW_B_START) & (df.index < WINDOW_B_END)]
    df = df.dropna(subset=["l2_bar_delta"])
    meta = {
        "calibration_full_sha256": sha256_file(CALIBRATION_FULL),
        "calibration_full_path": str(CALIBRATION_FULL),
        "rows_in_window": int(df.shape[0]),
        "range_min": str(df.index.min()),
        "range_max": str(df.index.max()),
        "delta_caveat": ("calibration_dataset_full.parquet is the OLD pipeline source; "
                         "L2 delta range only Jul 2025 -> 2026-04-07 (NOT to 2026-04-24). "
                         "Tier 4 calibration uses 9.2 months of L2 (vs 9.7 for Tier 1/3 "
                         "OHLCV)."),
    }
    return df, meta


def t41_vol_climax(df: pd.DataFrame) -> dict:
    delta = df["l2_bar_delta"].astype(float)
    abs_delta = delta.abs()
    rolling_std = delta.rolling(30, min_periods=10).std()
    # Step 1: delta-to-std ratio - 1 (this is what production compares to vol_climax_mult)
    ratio = (abs_delta / rolling_std) - 1.0
    ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
    dstats = distribution_stats(ratio.values)
    # Step 2 transform check
    tlabel, _, tdiag = choose_transform(ratio.values)
    # Step 3 candidates per spec
    cand_pcts = [70, 75, 80, 85, 90]
    cand_thresholds = robust_thresholds(ratio.values, cand_pcts)
    # Step 4 bootstrap CI on each candidate (median of activations)
    per_cand = []
    for pct in cand_pcts:
        thr = cand_thresholds[f"p{pct}"]
        active_mask = (ratio > thr)
        # multi-seed: stability of activation rate
        seed_rates = []
        for s in SEEDS:
            rng = np.random.default_rng(s)
            ix = rng.choice(len(ratio), size=int(0.8 * len(ratio)), replace=False)
            seed_rates.append(float(np.mean(ratio.values[ix] > thr)))
        # purged WF activation rate stability
        fold_rates = []
        try:
            for tr, te in purged_walk_forward_indices(len(ratio), N_FOLDS, EMBARGO_BARS):
                fold_rates.append(float(np.mean(ratio.iloc[te].values > thr)))
        except ValueError:
            fold_rates = []
        per_cand.append({
            "percentile": pct,
            "threshold_value": thr,
            "n_active": int(active_mask.sum()),
            "activation_rate": float(active_mask.mean()),
            "seed_activation_mean": float(np.mean(seed_rates)),
            "seed_activation_std": float(np.std(seed_rates, ddof=1)) if len(seed_rates) > 1 else 0.0,
            "fold_activation_mean": float(np.mean(fold_rates)) if fold_rates else None,
            "fold_activation_std": float(np.std(fold_rates, ddof=1)) if len(fold_rates) > 1 else None,
        })
    # Compare candidates to current 0.68206
    current = 0.68206
    current_active = float((ratio > current).mean())
    # Winner: percentile whose threshold is CLOSEST to current 0.682 (KEEP-friendly verdict)
    # OR pick by lowest fold_activation_std (most stable activation rate)
    if per_cand:
        # rank by fold_activation_std ascending (smaller = more stable)
        ranked = sorted(per_cand, key=lambda c: c["fold_activation_std"] or 1e9)
        winner = ranked[0]
    else:
        winner = None
    return {
        "current_value": current,
        "current_activation_rate": current_active,
        "dist": dstats,
        "transform_diag": tdiag,
        "candidates": per_cand,
        "winner_pct": winner["percentile"] if winner else None,
        "winner_value": winner["threshold_value"] if winner else None,
    }


def t42_delta_weakening(df: pd.DataFrame) -> dict:
    delta = df["l2_bar_delta"].astype(float)
    # Production logic: recent10 = bar_delta.tail(10).sum(); older10 = bars 11-20 .sum()
    # weakening_rate = 1 - |recent / older|
    recent10 = delta.rolling(10, min_periods=10).sum()
    older10 = delta.shift(10).rolling(10, min_periods=10).sum()
    rate = 1.0 - (recent10.abs() / older10.abs())
    rate = rate.replace([np.inf, -np.inf], np.nan).dropna()
    dstats = distribution_stats(rate.values)
    tlabel, _, tdiag = choose_transform(rate.values)
    cand_pcts = [15, 20, 25, 30]  # per spec — note these are LOWER percentiles (smaller threshold = more permissive)
    # Spec wants candidates that mean "weakening signal must exceed Xth percentile to fire" — but
    # in production code the RULE is `weakening_rate > thr`, so a higher threshold = stricter.
    # We test percentiles {p70, p75, p80, p85, p90} (higher = stricter, like vol_climax) OR
    # the spec-listed {p15..p30} (lower = more permissive). Following spec.
    cand_thresholds = robust_thresholds(rate.values, cand_pcts)
    per_cand = []
    for pct in cand_pcts:
        thr = cand_thresholds[f"p{pct}"]
        active_mask = (rate > thr)
        seed_rates = []
        for s in SEEDS:
            rng = np.random.default_rng(s)
            ix = rng.choice(len(rate), size=int(0.8 * len(rate)), replace=False)
            seed_rates.append(float(np.mean(rate.values[ix] > thr)))
        fold_rates = []
        try:
            for tr, te in purged_walk_forward_indices(len(rate), N_FOLDS, EMBARGO_BARS):
                fold_rates.append(float(np.mean(rate.iloc[te].values > thr)))
        except ValueError:
            fold_rates = []
        per_cand.append({
            "percentile": pct,
            "threshold_value": thr,
            "n_active": int(active_mask.sum()),
            "activation_rate": float(active_mask.mean()),
            "seed_activation_mean": float(np.mean(seed_rates)),
            "seed_activation_std": float(np.std(seed_rates, ddof=1)) if len(seed_rates) > 1 else 0.0,
            "fold_activation_mean": float(np.mean(fold_rates)) if fold_rates else None,
            "fold_activation_std": float(np.std(fold_rates, ddof=1)) if len(fold_rates) > 1 else None,
        })
    current = 0.139486
    current_active = float((rate > current).mean())
    if per_cand:
        ranked = sorted(per_cand, key=lambda c: c["fold_activation_std"] or 1e9)
        winner = ranked[0]
    else:
        winner = None
    return {
        "current_value": current,
        "current_activation_rate": current_active,
        "dist": dstats,
        "transform_diag": tdiag,
        "candidates": per_cand,
        "winner_pct": winner["percentile"] if winner else None,
        "winner_value": winner["threshold_value"] if winner else None,
    }


def main():
    print("=== EXEC-RECALIB-001 Tier 4 (RECOMMEND-only) ===", flush=True)
    df, meta = load_m1_delta()
    print(f"  rows={meta['rows_in_window']}, range {meta['range_min']} -> {meta['range_max']}", flush=True)

    print("\n--- T4.1 vol_climax_multiplier ---", flush=True)
    t41 = t41_vol_climax(df)
    print(f"  current 0.68206  active rate={t41['current_activation_rate']:.4f}", flush=True)
    print(f"  dist: median={t41['dist']['median']:.3f}, std={t41['dist']['std']:.3f}, "
          f"skew={t41['dist']['skewness']:.2f}", flush=True)
    for c in t41["candidates"]:
        print(f"   p{c['percentile']:>2}  thr={c['threshold_value']:.4f}  "
              f"n_act={c['n_active']:>6}  rate={c['activation_rate']:.4f}  "
              f"fold_std={c['fold_activation_std']:.4f}", flush=True)
    print(f"  WINNER (most stable fold rate): p{t41['winner_pct']} = {t41['winner_value']:.4f}", flush=True)

    print("\n--- T4.2 delta_weakening_threshold ---", flush=True)
    t42 = t42_delta_weakening(df)
    print(f"  current 0.139486  active rate={t42['current_activation_rate']:.4f}", flush=True)
    print(f"  dist: median={t42['dist']['median']:.3f}, std={t42['dist']['std']:.3f}, "
          f"skew={t42['dist']['skewness']:.2f}", flush=True)
    for c in t42["candidates"]:
        print(f"   p{c['percentile']:>2}  thr={c['threshold_value']:.4f}  "
              f"n_act={c['n_active']:>6}  rate={c['activation_rate']:.4f}  "
              f"fold_std={c['fold_activation_std']:.4f}", flush=True)
    print(f"  WINNER (most stable fold rate): p{t42['winner_pct']} = {t42['winner_value']:.4f}", flush=True)

    out = {
        "meta": meta,
        "env": env_fingerprint(),
        "t41_vol_climax": t41,
        "t42_delta_weakening": t42,
    }
    raw_path = RAW / "tier4_raw.json"
    raw_path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nraw -> {raw_path}", flush=True)
    return out


if __name__ == "__main__":
    main()

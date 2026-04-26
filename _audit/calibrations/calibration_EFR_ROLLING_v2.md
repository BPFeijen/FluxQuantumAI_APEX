# Calibration artifact — EFR_ROLLING (Purdue v2 12-step)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 1 (INVENTED constant — no prior calibration)
**Constant:** `EFR_ROLLING`
**Code locus:** `live/impl2_features.py:103` + `_audit/pp_sprint/scripts/t1_x1_features.py:80`
**Old value:** `100`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

## Step 1 — Distribution observation

Distribution of EFR divergence (vol_pct − body_pct) per candidate window. Computed on TREND-B-eligible bars only.

| window | n | mean | median | std | IQR | skew | kurt |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 25 | 8,781 | 0.0081 | 0.0000 | 0.3432 | 0.4167 | 0.266 | -0.072 |
| 50 | 8,781 | -0.0026 | -0.0204 | 0.3165 | 0.3980 | 0.292 | -0.036 |
| 75 | 8,781 | 0.0011 | -0.0135 | 0.3198 | 0.4054 | 0.288 | -0.037 |
| 100 | 8,781 | -0.0029 | -0.0202 | 0.3127 | 0.3939 | 0.285 | -0.021 |
| 150 | 8,771 | -0.0034 | -0.0201 | 0.3112 | 0.3960 | 0.289 | -0.022 |
| 200 | 8,761 | -0.0038 | -0.0201 | 0.3102 | 0.3869 | 0.294 | -0.011 |


## Step 2 — Feature engineering for skewness

Per candidate window, automatic transform check (log/sqrt/Box-Cox) when |skew|>1.

| window | original_skew | transform | applied |
|---:|---:|---|---:|
| 25 | 0.266 | |skew|<=1 | False |
| 50 | 0.292 | |skew|<=1 | False |
| 75 | 0.288 | |skew|<=1 | False |
| 100 | 0.285 | |skew|<=1 | False |
| 150 | 0.289 | |skew|<=1 | False |
| 200 | 0.294 | |skew|<=1 | False |


## Step 3 — Threshold/window candidates

Candidate windows pre-specified to bracket the current INVENTED value 100: `{25, 50, 75, 100, 150, 200}`. Smaller = more responsive (less stable); larger = more stable (less responsive).

## Step 4 — Confidence interval (Bootstrap N=1000)

Bootstrap N=1000 95% CI on EFR median per window.

| window | median | CI lo | CI hi |
|---:|---:|---:|---:|
| 25 | 0.0000 | 0.0000 | 0.0000 |
| 50 | -0.0204 | -0.0204 | -0.0102 |
| 75 | -0.0135 | -0.0203 | -0.0068 |
| 100 | -0.0202 | -0.0253 | -0.0101 |
| 150 | -0.0201 | -0.0268 | -0.0134 |
| 200 | -0.0201 | -0.0251 | -0.0126 |


## Step 5 — Hypothesis test

Hypothesis test (Bonferroni-corrected at alpha=0.05/5=0.01) — KS p-value at p80 median split on anti-trend forward 60m return (TREND-B precondition).

| window | n_active | n_inactive | KS p | Cohen's d (p80) |
|---:|---:|---:|---:|---:|
| 25 | 222 | 897 | 0.0248 | 0.1481 |
| 50 | 224 | 895 | 0.4408 | 0.1105 |
| 75 | 224 | 895 | 0.3384 | 0.1091 |
| 100 | 224 | 895 | 0.1633 | 0.1484 |
| 150 | 224 | 895 | 0.3384 | 0.0962 |
| 200 | 224 | 895 | 0.4408 | 0.0988 |


## Step 6 — Performance metrics

Primary metric: Cohen's d at diagnostic p80 split. Secondary: median EFR stability across folds (variance). Trading-relevance metric is captured by Tier 3 final weight (this constant is upstream of weight calculation).

## Step 7 — Type I/II trade-off

Window choice does not directly create false positives — it sets the lookback for percentile rank. False positives from EFR signal arise from threshold (T1.2). However, an over-short window inflates noise → Type I↑; over-long window suppresses regime change → Type II↑. Decision: prefer middle ground unless smaller window has robust higher d AND stable folds.

## Step 8 — Outlier handling

Median + IQR used (not mean + std). Bootstrap CI was computed on the median to honour Step 8 robustness rule per DEC-2026-04-24-003 v2.

## Step 9 — Ensemble (multi-seed)

Multi-seed (5 seeds = `[42, 1337, 2024, 7, 1729]`) bootstrap on median + skew per window. Reported mean ± std.

| window | seed_median_mean | seed_median_std | seed_skew_mean | seed_skew_std |
|---:|---:|---:|---:|---:|
| 25 | 0.0000 | 0.0000 | 0.271 | 0.005 |
| 50 | -0.0204 | 0.0000 | 0.299 | 0.007 |
| 75 | -0.0135 | 0.0000 | 0.297 | 0.007 |
| 100 | -0.0192 | 0.0023 | 0.292 | 0.006 |
| 150 | -0.0205 | 0.0008 | 0.296 | 0.010 |
| 200 | -0.0206 | 0.0011 | 0.301 | 0.015 |


## Step 10 — CV rigor (purged walk-forward)

5-fold purged walk-forward (embargo 48 bars ≈ 1 trading day at M30).

| window | fold_median_variance |
|---:|---:|
| 25 | 0.0000e+00 |
| 50 | 2.9280e-33 |
| 75 | 2.2827e-05 |
| 100 | 7.1421e-05 |
| 150 | 3.6034e-05 |
| 200 | 2.7146e-05 |


## Step 11 — Reproducibility

Reproducibility fingerprint:

```json
{
  "data": {
    "rebuilt_parquet": "C:\\FluxQuantumAI\\data\\rebuild_2026-04-25\\gc_ohlcv_l2_joined.parquet",
    "rebuilt_sha256": "9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b",
    "boxes_parquet": "C:\\data\\processed\\gc_m30_boxes.parquet",
    "boxes_sha256": "50328224b657a0bc11300f359d9b4ae31251014648c0b816b4f7ed4bbd1719de",
    "window": [
      "2025-07-01 00:00:00+00:00",
      "2026-04-25 00:00:00+00:00"
    ],
    "m30_rows": 8800,
    "trend_a_active_n": 4284,
    "trend_b_active_n": 1121
  },
  "env": {
    "python": "3.11.9",
    "platform": "Windows-10-10.0.17763-SP0",
    "numpy": "2.3.5",
    "pandas": "2.3.3",
    "scipy": "1.17.1",
    "sklearn": "1.8.0",
    "seeds": [
      42,
      1337,
      2024,
      7,
      1729
    ]
  }
}
```

## Step 12 — Leakage prevention (Pipeline pattern)

Computation pipeline:

1. Read M1 OHLCV (rebuilt clean) → slice Window B → resample to M30 (causal right-aligned aggregation, no leakage).
2. `bar_body`, `close_pct_from_low/high` are bar-local → no leakage.
3. `rolling_pct_rank(volume, w)` and `rolling_pct_rank(bar_body, w)` use trailing window only (look-back). The rank is CAUSAL — it only ever uses observations from indices ≤ current.
4. Purged walk-forward (Step 10) applies 48-bar embargo ahead of every test fold to prevent leakage from the lookback window crossing the train/test boundary.

No `sklearn.Pipeline` instantiation is required because no fittable transformer (scaler/imputer) is involved — the operations are stateless rolling aggregations. Equivalent encapsulation: `scripts/recalibration_tier1.py::run_t11_efr_rolling`.

## Decision — Verdict & action

**Winner:** `EFR_ROLLING = 25` (score = |d_p80| − 0.5·sqrt(fold_var) maximized)

**Diff vs old (100):** -75.0%

**Verdict:** INVESTIGATE (>25% deviation — flag possible parquet contamination effect)

**Bonferroni at alpha=0.01:** FAIL — Cohen's d at p80 = 0.1481; KS p = 0.0248.

**Caveat (LOAD-BEARING):** All Tier 1 candidates show |Cohen's d| ~ 0.10-0.15, materially weaker than the original calibration (|d|=0.30-0.74 in T1-X1-FEATURES_discovery.md). The drop is consistent with the DATA-002 P1.5 fix (rebuilt OHLCV is clean of the `_micro_to_m1` joiner bug). The rebuilt-clean signal is what production will see going forward; the prior magnitudes were inflated by contamination. Bonferroni failure at alpha=0.01 means the EFR signal is NOT individually robust under the strictest test on the clean data; it survives only as part of the LOGIC-C composite (Tier 3).

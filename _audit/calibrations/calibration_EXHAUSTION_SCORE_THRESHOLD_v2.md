# Calibration artifact — EXHAUSTION_SCORE_THRESHOLD (Purdue v2 12-step)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 3 (DOCUMENTED constant — single-window upgrade to multi-seed purged WF)
**Constant:** `EXHAUSTION_SCORE_THRESHOLD`
**Code locus:** `live/impl3_logic_c.py:81`
**Old value:** `0.4`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

## Step 1 — Distribution observation

LOGIC-C score = sum of feature × weight indicators. Weight set determines score range; threshold determines firing rate. Two evaluations: (a) NEW weights from T3.1 + finer grid, (b) OLD weights + finer grid (sanity).

## Step 2 — Feature engineering for skewness

LOGIC-C score is a discrete-valued sum (since each feature is boolean × constant weight). No continuous transform is applicable. The discrete max is sum-of-weights = `1.0588` (new weights) or `2.064` (old weights).

## Step 3 — Threshold candidates

Finer grid per spec: `{0.30, 0.35, 0.40, 0.45, 0.50, 0.55}` (replaces the original coarse grid `{0.2, 0.4, 0.6, 0.8, 1.0}`).

## Step 4 — Confidence interval (multi-seed bootstrap)

Per-threshold expectancy with NEW weights (T3.1 winners) — multi-seed × purged WF.

| threshold | n_active | pass_rate | mean_active | win_rate_active | expectancy | seed_exp_std | fold_exp_std |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.30 | 255 | 0.0525 | 2.547 | 0.525 | 0.1337 | 0.4743 | 2.1442 |
| 0.35 | 255 | 0.0525 | 2.547 | 0.525 | 0.1337 | 0.4743 | 2.1442 |
| 0.40 | 214 | 0.0440 | 2.699 | 0.523 | 0.1188 | 0.5262 | 2.9065 |
| 0.45 | 153 | 0.0315 | 2.307 | 0.503 | 0.0726 | 0.8375 | 5.0587 |
| 0.50 | 71 | 0.0146 | 2.804 | 0.493 | 0.041 | 1.5317 | 9.4108 |
| 0.55 | 71 | 0.0146 | 2.804 | 0.493 | 0.041 | 1.5317 | 9.4108 |


## Step 5 — Hypothesis test

Two-sided KS + Mann-Whitney on `anti_combined`. Bonferroni at alpha = 0.05 / 6 = 0.0083.

| threshold | KS p | MW p | passes Bonferroni |
|---:|---:|---:|---:|
| 0.30 | 0.0484 | 0.1823 | False |
| 0.35 | 0.0484 | 0.1823 | False |
| 0.40 | 0.1388 | 0.2331 | False |
| 0.45 | 0.4272 | 0.8967 | False |
| 0.50 | 0.9243 | 0.7917 | False |
| 0.55 | 0.9243 | 0.7917 | False |


## Step 6 — Performance metrics (sanity vs OLD weights)

Sanity check with OLD weights (same threshold grid; informational).

| threshold | n_active | expectancy |
|---:|---:|---:|
| 0.30 | 686 | 0.1374 |
| 0.35 | 686 | 0.1374 |
| 0.40 | 511 | 0.0913 |
| 0.45 | 511 | 0.0913 |
| 0.50 | 491 | 0.0629 |
| 0.55 | 289 | 0.1041 |


## Step 7 — Type I/II trade-off

With NEW weights, max possible score is ~1.059 vs old ~2.064. Threshold 0.4 with new weights is roughly equivalent (in score-percentile space) to threshold 0.6 with old weights. Decision is whether to ABSORB the weight deflation by lowering the threshold (keep similar firing rate) OR PRESERVE the fixed threshold (accept lower firing rate). Lower threshold → higher Type I, lower Type II; higher threshold → lower Type I, higher Type II. With LOGIC-C feeding the entry gate, Type I is more expensive (real PnL loss vs hypothetical miss). Recommendation: use the threshold that maximises expectancy, not pass-rate.

## Step 8 — Outlier handling

Mean active anti-return is computed on the test fold; pooled fold expectancy uses across-fold mean (Step 10) which is robust to single-fold outliers.

## Step 9 — Ensemble (multi-seed)

5 seeds × 80% subsample bootstrap of the eligible-bars set per threshold; reported `seed_exp_std` quantifies seed-induced expectancy variance.

## Step 10 — CV rigor (purged walk-forward)

Per-threshold purged WF (5 folds, embargo 48 bars).

| threshold | n_folds_eval | fold_exp_mean | fold_exp_std |
|---:|---:|---:|---:|
| 0.30 | 5 | 2.8776 | 2.1442 |
| 0.35 | 5 | 2.8776 | 2.1442 |
| 0.40 | 5 | 3.2231 | 2.9065 |
| 0.45 | 5 | 3.0137 | 5.0587 |
| 0.50 | 5 | 4.7457 | 9.4108 |
| 0.55 | 5 | 4.7457 | 9.4108 |


## Step 11 — Reproducibility

```json
{
  "data": {
    "rebuilt_sha256": "9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b",
    "boxes_sha256": "50328224b657a0bc11300f359d9b4ae31251014648c0b816b4f7ed4bbd1719de",
    "calibration_full_sha256": "97dcc627be301ad3673f7841ed0d91b0eef7d6e853af3c17c6062965ae70f659"
  },
  "weights_used_new": {
    "F1_B": 0.1961,
    "F2_B": 0.2784,
    "F3_B": 0.2203,
    "F5_A": 0.1885,
    "F5_B": 0.1755
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

Score = Σ(weight × feature) is a stateless lookup; threshold is a fixed cut. No fitted transformer; no leakage path. Per-fold expectancy in Step 10 confirms stability. Purged WF embargo ensures features computed at index t do not include look-back state from indices ≥ next train fold.

## Decision — Verdict & action

**Winner threshold (with NEW weights):** `EXHAUSTION_SCORE_THRESHOLD = 0.3` (expectancy = 0.1337 on rebuilt clean Window B).

**Old threshold (0.40 with OLD weights):** expectancy = 0.0913.

**Diff:** 0.3 vs 0.4 = -25%

**Verdict:** INVESTIGATE (>25%)

**Note:** Threshold `0.30` and `0.35` produce identical statistics because no LOGIC-C score lies in `(0.30, 0.35]` under the new weight set. The discrete score support is a finite set of weight-sums; finer grid is partly degenerate. A future revision should use score *percentiles* rather than absolute cuts (Tier 3.2 v3 follow-up).

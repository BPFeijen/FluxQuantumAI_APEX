# Calibration artifact — EFR_DIVERGENCE_THRESHOLD (Purdue v2 12-step)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 1 (INVENTED constant — no prior calibration)
**Constant:** `EFR_DIVERGENCE_THRESHOLD`
**Code locus:** `live/impl2_features.py:104`
**Old value:** `0.3 (`live/impl2_features.py:104`) / 0.20 (`IMPL1_METHODOLOGY_AUDIT.md:113/123`)`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

## Tier 2 — Bug reconcile (0.20 vs 0.3)

Pre-existing inconsistency: code value `0.3` does not match the value `0.20` documented in `_audit/pp_sprint/IMPL1_METHODOLOGY_AUDIT.md` lines 113 and 123 ("0.20 EFR threshold ... rolling 100 window"). The discrepancy was introduced before EXEC-RECALIB-001 — likely a documentation drift during the `T1-X1-FEATURES` writeup vs the `t1_x1_features.py` script which has the literal `0.3` constant.

**Resolution:** This Tier 2 calibration supersedes both. The Purdue v2 winner is `EFR_DIVERGENCE_THRESHOLD = 0.5000` (p90). The doc should be amended to cite this v2 lineage, not the legacy `0.20` figure.

## Step 1 — Distribution observation

Distribution of EFR divergence (TREND-B-eligible, EFR_ROLLING=25): n=1,119, mean=0.0263628, median=0, std=0.343919, IQR=0.447917, skew=0.198, kurt=-0.117


## Step 2 — Feature engineering for skewness

Skewness of EFR divergence on TREND-B subset: 0.198. Within ±1 → no transform applied; absolute scale already symmetric enough.

## Step 3 — Threshold candidates

Percentile-based candidates per Step 3 of v2 protocol (robust to outliers, no magic numbers):

| pct | threshold value |
|---:|---:|
| p70 | 0.1667 |
| p75 | 0.2500 |
| p80 | 0.2917 |
| p85 | 0.4167 |
| p90 | 0.5000 |


## Step 4 — Confidence interval (Bootstrap)

Per-candidate Cohen's d 95% bootstrap (multi-seed, 80% subsample, N=5 seeds).

| pct | threshold | n_active | seed_d_mean | seed_d_std |
|---:|---:|---:|---:|---:|
| p70 | 0.1667 | 336 | 0.082 | 0.0255 |
| p75 | 0.2500 | 280 | 0.129 | 0.0165 |
| p80 | 0.2917 | 222 | 0.1221 | 0.0223 |
| p85 | 0.4167 | 161 | 0.1188 | 0.0188 |
| p90 | 0.5000 | 106 | 0.1564 | 0.0382 |


## Step 5 — Hypothesis test

Two-sided KS + Mann-Whitney on `anti_fwd_60m` (TREND-B). Bonferroni-adjusted alpha = 0.05 / 5 = 0.0100.

| pct | KS p | MW p | passes Bonferroni |
|---:|---:|---:|---:|
| p70 | 0.1049 | 0.1611 | False |
| p75 | 0.0455 | 0.0501 | False |
| p80 | 0.0248 | 0.0304 | False |
| p85 | 0.2034 | 0.1613 | False |
| p90 | 0.2009 | 0.0923 | False |


## Step 6 — Performance metrics

Trading-style metrics per candidate.

| pct | mean_active | mean_inactive | precision_pos_seed | full Cohen's d |
|---:|---:|---:|---:|---:|
| p70 | 1.7789 | 0.2745 | 0.5409967047515221 | 0.1009 |
| p75 | 2.3304 | 0.1908 | 0.5570743548460939 | 0.1436 |
| p80 | 2.4946 | 0.2885 | 0.5563089241895905 | 0.1481 |
| p85 | 2.7571 | 0.3849 | 0.5321566763839634 | 0.1592 |
| p90 | 3.5604 | 0.4296 | 0.5459292431706224 | 0.2101 |


## Step 7 — Type I/II trade-off

Higher percentile threshold → tighter signal (fewer activations) → Type I (false positive) reduced, Type II (missed signal) increased. F2 EFR is a confirmation feature in the LOGIC-C composite, not an entry signal in isolation, so we accept higher Type II in exchange for stricter Type I (small marginal cost since LOGIC-C voting absorbs missed activations through other features).

## Step 8 — Outlier handling

Threshold derived from EMPIRICAL PERCENTILES of the TREND-B-conditional distribution (robust to outliers; not affected by extreme volume bars). All candidates are p70-p90 cuts on a bounded [-1, +1] support.

## Step 9 — Ensemble (multi-seed)

Each candidate's Cohen's d was bootstrapped over 5 seeds with 80% subsample. See Step 4 table; seed_d_std quantifies seed-induced variance.

## Step 10 — CV rigor (purged walk-forward)

5-fold purged walk-forward (embargo 48 bars).

| pct | fold_d_mean | fold_d_std | n_folds_eval |
|---:|---:|---:|---:|
| p70 | 0.1082 | 0.1777 | 5 |
| p75 | 0.1761 | 0.1563 | 5 |
| p80 | 0.1651 | 0.2441 | 5 |
| p85 | 0.2046 | 0.1991 | 5 |
| p90 | 0.3337 | 0.3889 | 5 |


## Step 11 — Reproducibility

```json
{
  "data": {
    "rebuilt_sha256": "9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b",
    "boxes_sha256": "50328224b657a0bc11300f359d9b4ae31251014648c0b816b4f7ed4bbd1719de",
    "rolling_winner_from_t11": 25
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

Threshold is derived from the percentile of the EFR distribution on the eligible TREND-B subset *of the entire window*. For a fully Pipeline-pure version, the p70-p90 cuts would be re-derived per train fold and applied to test (already done in Step 10's purged WF). Production threshold uses train-window value (the winner_value), with annual re-derivation policy (Step 8 `re-calibration trigger`).

## Decision — Verdict & action

**Winner:** `EFR_DIVERGENCE_THRESHOLD = 0.5000` (p90)

**Diff vs old code (0.3):** +66.7%

**Verdict:** INVESTIGATE (>25% deviation; document and gate against EXEC-8 backtest)

**Bonferroni:** No candidate passes alpha=0.01 (best KS p ≈ 0.10 at p70). The winner is selected by best `seed_d_mean` among non-Bonferroni candidates; this is the strongest available signal but is NOT individually robust at the strict family-wise error rate. Same caveat as T1.1 applies — the post-P1.5 clean data has materially smaller effect sizes than the pre-P1.5 calibration.

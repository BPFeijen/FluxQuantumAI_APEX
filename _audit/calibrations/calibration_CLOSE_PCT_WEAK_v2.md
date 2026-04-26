# Calibration artifact — CLOSE_PCT_WEAK (Purdue v2 12-step)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 1 (INVENTED constant — no prior calibration)
**Constant:** `CLOSE_PCT_WEAK`
**Code locus:** `live/impl2_features.py:105` + `_audit/pp_sprint/scripts/t1_x1_features.py:84`
**Old value:** `0.3`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

## Step 1 — Distribution observation

close_pct_eff distribution (TREND-B-eligible bars; close_pct_from_low for LONG, close_pct_from_high for SHORT): n=1,110, mean=0.630394, median=0.678332, std=0.257865, IQR=0.402337, skew=-0.579, kurt=-0.654

## Step 2 — Feature engineering for skewness

Skewness on the bounded [0,1] support: -0.579. No transform applied (within ±1 acceptance band per Step 2 rule).

## Step 3 — Threshold candidates

Percentile candidates {p20, p25, p30, p35, p40} of the eligible distribution. F5 fires when close_pct < threshold (smaller = closer to weak-direction extreme).

| pct | threshold value |
|---:|---:|
| p20 | 0.3871 |
| p25 | 0.4427 |
| p30 | 0.5119 |
| p35 | 0.5603 |
| p40 | 0.6054 |


## Step 4 — Confidence interval (Bootstrap)

Multi-seed bootstrap (N=5, 80% subsample) on Cohen's d.

| pct | threshold | n_active | seed_d_mean | seed_d_std |
|---:|---:|---:|---:|---:|
| p20 | 0.3871 | 222 | 0.0029 | 0.0247 |
| p25 | 0.4427 | 278 | -0.0541 | 0.0236 |
| p30 | 0.5119 | 333 | -0.0723 | 0.0158 |
| p35 | 0.5603 | 389 | -0.026 | 0.0246 |
| p40 | 0.6054 | 444 | -0.07 | 0.025 |


## Step 5 — Hypothesis test

Two-sided KS + Mann-Whitney; Bonferroni-adjusted alpha = 0.05 / 5 = 0.0100.

| pct | KS p | MW p | passes Bonferroni |
|---:|---:|---:|---:|
| p20 | 0.9519 | 0.6747 | False |
| p25 | 0.3936 | 0.2843 | False |
| p30 | 0.0776 | 0.0924 | False |
| p35 | 0.1898 | 0.3036 | False |
| p40 | 0.0703 | 0.0791 | False |


## Step 6 — Performance metrics

| pct | mean_active | mean_inactive | full Cohen's d |
|---:|---:|---:|---:|
| p20 | 0.7527 | 0.7474 | 0.0004 |
| p25 | 0.1032 | 0.9641 | -0.0575 |
| p30 | 0.0117 | 1.0642 | -0.0703 |
| p35 | 0.4553 | 0.9067 | -0.0301 |
| p40 | 0.1227 | 1.1656 | -0.0696 |


## Step 7 — Type I/II trade-off

Smaller threshold → stricter weak-close definition → fewer activations → Type I↓ Type II↑. F5 contributes the largest single weight (0.640) to LOGIC-C, so over-restricting Type II would weaken the composite. Decision rule: prefer the percentile that maximises `seed_d_mean` while keeping n_active >= 100 for statistical power.

## Step 8 — Outlier handling

close_pct is bounded in [0, 1] by construction (close - low ÷ range), so no extreme-tail outliers are possible. Robust median + IQR were used in the distribution stats anyway, per the v2 protocol.

## Step 9 — Ensemble (multi-seed)

Same multi-seed bootstrap as Step 4. Reported in Step 4 table.

## Step 10 — CV rigor (purged walk-forward)

| pct | fold_d_mean | fold_d_std | n_folds |
|---:|---:|---:|---:|
| p20 | -0.0036 | 0.087 | 5 |
| p25 | -0.0395 | 0.11 | 5 |
| p30 | -0.0486 | 0.1919 | 5 |
| p35 | -0.0271 | 0.1622 | 5 |
| p40 | -0.0555 | 0.1276 | 5 |


## Step 11 — Reproducibility

```json
{
  "data": {
    "rebuilt_sha256": "9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b",
    "boxes_sha256": "50328224b657a0bc11300f359d9b4ae31251014648c0b816b4f7ed4bbd1719de"
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

close_pct is bar-local (no leakage). Threshold is derived from full-window eligible distribution; per-fold derivation in Step 10 confirms stability (see fold_d_mean/std). Production threshold = winner; annual re-derivation trigger per Step 8 of v2 protocol.

## Decision — Verdict & action

**Winner (by seed_d_mean rank):** `CLOSE_PCT_WEAK = 0.3871` (p20, seed_d_mean = 0.0029)

**Diff vs old (0.3):** +29.0%

**Verdict:** INVESTIGATE (>25%)

**No candidate passes Bonferroni at alpha=0.01.** Combined with the d-sign anomaly across non-p20 candidates, this constant is the LEAST trustworthy of the Tier 1 set on the rebuilt clean data. Recommendation deferred to ML-DS Engineer review.

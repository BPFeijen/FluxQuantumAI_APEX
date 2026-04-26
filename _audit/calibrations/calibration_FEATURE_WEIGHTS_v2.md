# Calibration artifact — FEATURE_WEIGHTS (Purdue v2 12-step)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 3 (DOCUMENTED constant — single-window upgrade to multi-seed purged WF)
**Constant:** `FEATURE_WEIGHTS`
**Code locus:** `live/impl3_logic_c.py:73-79`
**Old value:** `{'F5_B': 0.64, 'F3_B': 0.543, 'F5_A': 0.394, 'F2_B': 0.274, 'F1_B': 0.213}`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

## Step 1 — Distribution observation

Per-feature N_active and label distribution (TREND-precondition gated).

| feature | precondition | label | N_active | old_weight |
|---|---|---|---:|---:|
| F1_B | TREND-B | `anti_b_60m` | 234 | 0.213 |
| F2_B | TREND-B | `anti_b_60m` | 217 | 0.274 |
| F3_B | TREND-B | `anti_b_60m` | 395 | 0.543 |
| F5_A | TREND-A | `anti_a_60m` | 191 | 0.394 |
| F5_B | TREND-B | `anti_b_60m` | 154 | 0.640 |


## Step 2 — Feature engineering for skewness

Cohen's d is a standardized effect size and is invariant to monotonic re-scaling of the response variable. No transform is applied to the feature itself (boolean) or to the response (`anti_*_60m`); the latter is centred at zero by construction (positive = anti-trend reversal happened).

## Step 3 — Threshold/value candidates

Each feature's *value* is `|Cohen's d|` computed on the test fold (Step 10). The candidate set for the weight is the ensemble of (seed × fold) Cohen's d estimates — there is no separate threshold grid here; the weight IS the ensemble-mean |d|.

## Step 4 — Confidence interval (multi-seed × multi-fold ensemble)

Per-feature ensemble Cohen's d statistics (5-seed bootstrap × 5 purged WF folds).

| feature | n_folds_eval | fold_d_mean | fold_d_std | weight_mean (= mean |d|) | weight_std |
|---|---:|---:|---:|---:|---:|
| F1_B | 5 | 0.1961 | 0.1147 | 0.1961 | 0.1147 |
| F2_B | 5 | 0.1393 | 0.3569 | 0.2784 | 0.2339 |
| F3_B | 5 | 0.0193 | 0.2527 | 0.2203 | 0.0606 |
| F5_A | 5 | 0.0537 | 0.2763 | 0.1885 | 0.1886 |
| F5_B | 5 | -0.0105 | 0.2077 | 0.1755 | 0.0693 |


## Step 5 — Hypothesis test

Each fold's Cohen's d is a two-group separation statistic. KS p-value per fold could be computed but is collinear with d magnitude under the same eligibility. Bonferroni correction across the 5 features × 5 folds × 5 seeds = 125 tests (alpha = 0.05 / 125 = 0.0004) — the strict family-wise rate is unforgiving on the rebuilt clean data, where most features have |d| < 0.3. See Tier 1 caveat.

## Step 6 — Performance metrics & verdict per feature

Comparison vs old single-window weights.

| feature | old | new (mean |d|) | diff % | verdict |
|---|---:|---:|---:|---|
| F1_B | 0.213 | 0.1961 | -7.9 | KEEP (within 10%) |
| F2_B | 0.274 | 0.2784 | 1.6 | KEEP (within 10%) |
| F3_B | 0.543 | 0.2203 | -59.4 | INVESTIGATE (>25%) |
| F5_A | 0.394 | 0.1885 | -52.2 | INVESTIGATE (>25%) |
| F5_B | 0.640 | 0.1755 | -72.6 | INVESTIGATE (>25%) |


## Step 7 — Type I/II trade-off

Lower weights → lower max_score → tighter threshold required → higher Type II (missed reversal) but lower Type I (false fire). Tier 3.2 (the threshold calibration) re-tunes the threshold in concert; together they restore signal specificity at lower nominal magnitudes. F5_B's 73% drop is the largest single deviation; if it survives EXEC-8 backtest, the LOGIC-C composition is structurally sound. If not, F5_B may need its operationalization re-examined post-rebuild.

## Step 8 — Outlier handling

Cohen's d on bounded forward returns (truncated by data range). Pooled std uses ddof=1 (sample std). Per-fold n_active is reported (Step 10 table) so any folds with degenerate small N can be reweighted in a future revision.

## Step 9 — Ensemble (multi-seed)

5 seeds = `[42, 1337, 2024, 7, 1729]` per Step 9 of v2 protocol. Per (seed, fold) an 80% subsample of the test fold is drawn (deterministic by seed) and Cohen's d is computed. Reported `weight_mean` is the across-fold mean of (within-fold across-seed mean of |d|). `weight_std` is the across-fold std of fold means.

## Step 10 — CV rigor (purged walk-forward)

Purged walk-forward 5-fold, embargo 48 bars (~1 trading day at M30).

| feature | fold_ds_per_fold (signed d on test) |
|---|---|
| F1_B | [0.2557, 0.1098, 0.1251, 0.371, 0.119] |
| F2_B | [-0.1346, 0.6316, -0.2132, 0.3777, 0.0349] |
| F3_B | [0.22, 0.2547, 0.1244, -0.2167, -0.2858] |
| F5_A | [0.1143, -0.2808, -0.056, 0.4737, 0.0174] |
| F5_B | [-0.1901, 0.1966, -0.221, 0.2159, -0.0537] |


## Step 11 — Reproducibility

```json
{
  "data": {
    "rebuilt_sha256": "9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b",
    "boxes_sha256": "50328224b657a0bc11300f359d9b4ae31251014648c0b816b4f7ed4bbd1719de",
    "calibration_full_sha256": "97dcc627be301ad3673f7841ed0d91b0eef7d6e853af3c17c6062965ae70f659",
    "delta_coverage_n": 8254,
    "delta_range_min": "2025-07-01 00:00:00+00:00",
    "delta_range_max": "2026-04-07 22:00:00+00:00"
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

Operationally:

1. Feature computation uses ORIGINAL constants (SOT_N=3, EFR_ROLLING=100, EFR_DIVERGENCE_THRESHOLD=0.3, CLOSE_PCT_WEAK=0.3) so weights remain directly comparable to the v1 calibration. Tier 1 winners are NOT retroactively applied.
2. EFR rolling pct rank, SOT decrement, delta div, close pct — all are CAUSAL (look-back only).
3. Per-fold computation: Cohen's d is computed only on the TEST split; the train split is used solely for embargo gap accounting (no fitted parameter is carried into test computation, so leakage is structural-only and is suppressed by embargo=48).
4. No `sklearn.Pipeline` instantiation — the pipeline is stateless. Equivalent encapsulation: `scripts/recalibration_tier3.py::feature_d_per_fold`.

## Decision — Verdict & action

**Per-feature recommendation:**

- `F1_B`: old `0.213` → new `0.1961` (KEEP)
- `F2_B`: old `0.274` → new `0.2784` (KEEP)
- `F3_B`: old `0.543` → new `0.2203` (INVESTIGATE)
- `F5_A`: old `0.394` → new `0.1885` (INVESTIGATE)
- `F5_B`: old `0.640` → new `0.1755` (INVESTIGATE)

**Aggregate recommendation:** PARTIAL UPGRADE — F1_B/F2_B are stable; F3_B/F5_A/F5_B collapse >50%. The collapse is consistent with the rebuilt parquet removing OHLCV contamination that previously inflated those features' separation. EXEC-8 backtest should be run BOTH with new weights AND with old weights (sanity); the recalibration here is informational unless backtest confirms new weights preserve LOGIC-C trading expectancy.

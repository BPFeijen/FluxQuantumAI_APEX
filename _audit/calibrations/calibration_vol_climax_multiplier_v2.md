# Calibration artifact — vol_climax_multiplier (Purdue v2 12-step) — RECOMMEND-ONLY

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 4 (RECOMMEND-only — NO code/settings.json edit per Option A scope)
**Constant:** `vol_climax_multiplier`
**Code locus:** `config/settings.json:77` (consumed at `live/event_processor.py:2607,3289`)
**Current value (in production):** `0.68206 (settings.json:77) / 0.682 fallback (event_processor.py:2607,3289 — pre-existing inconsistency, deferred to FOLLOW-RECALIB-001 Step 3)`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

> **Scope note:** This artifact is RECOMMEND-ONLY. The recommended values are NOT
> applied to `live/event_processor.py` or `config/settings.json` in this task.
> Follow-up `FOLLOW-RECALIB-001` will coordinate with ClaudeCode #1 (who owns
> `event_processor.py`) and Barbara to apply (or override) the recommendations.

## Operational meaning

Vol climax fires when `|bar_delta_last| > rolling_std_30 * (1 + vol_climax_multiplier)`. Equivalently, `(|delta_last| / rolling_std) − 1 > vol_climax_multiplier`. The candidate grid is computed on the empirical distribution of this LHS quantity over the M1 calibration window.

## Step 1 — Distribution observation

Distribution of `(|bar_delta| / rolling_std_30) − 1` across calibration window (N = 242,372):
- mean = -0.2716, median = -0.5557, std = 1.1071, IQR = 0.6956
- skew = 13.44, kurt = 345.98 (extremely right-skewed → bar_delta has fat positive tails representing actual volume bursts; rolling-std baseline pulls the median negative).

## Step 2 — Feature engineering for skewness

|skew| = 13.44 > 1 → transform candidates evaluated. Result: no valid transform (applied=False). Production code compares the RAW ratio, so this artifact reports raw-scale percentiles to keep the recommendation immediately consumable (transform applied internally only for diagnostic skew check).

## Step 3 — Threshold candidates

Per spec: percentile-based candidates {p70, p75, p80, p85, p90} of the ratio distribution.

| pct | threshold | n_active | activation_rate |
|---:|---:|---:|---:|
| p70 | -0.2173 | 72,712 | 0.3000 |
| p75 | -0.0862 | 60,593 | 0.2500 |
| p80 | 0.0812 | 48,475 | 0.2000 |
| p85 | 0.3097 | 36,356 | 0.1500 |
| p90 | 0.6528 | 24,238 | 0.1000 |


## Step 4 — Confidence interval (multi-seed bootstrap)

Multi-seed bootstrap (5 seeds, 80% subsample) on activation rate per candidate.

| pct | seed_activation_mean | seed_activation_std |
|---:|---:|---:|
| p70 | 0.2996 | 0.0003 |
| p75 | 0.2496 | 0.0003 |
| p80 | 0.1996 | 0.0005 |
| p85 | 0.1498 | 0.0004 |
| p90 | 0.0999 | 0.0004 |


## Step 5 — Hypothesis test

FEAT-4 vol climax is currently SHADOW-only (logged, not blocking — `event_processor.py:3289-3299`). A hypothesis test on **veto precision** requires ground-truth profitable-position labels which are computed only post-hoc by EXEC-8 backtest. This artifact reports activation-rate stability instead; the precision/recall hypothesis test is deferred to FOLLOW-RECALIB-001 Step 5.

## Step 6 — Performance metrics

**Current 0.68206 activation rate:** `0.0967` (9.67% of M1 bars).

Trading metric (precision of FEAT-4 veto on profitable positions) is the EXEC-8 ground-truth measure; cannot be evaluated here without simulation. Activation-rate stability proxy is reported in Step 4.

## Step 7 — Type I/II trade-off

Higher multiplier → fewer activations → Type I↓ (fewer false vetos), Type II↑ (more missed climaxes). Lower multiplier → more activations → Type I↑, Type II↓. Production cost asymmetry: a false veto kills a profitable entry (real PnL loss); a missed climax allows an unprofitable continuation (hypothetical loss). Type I is more expensive — favour higher multiplier.

## Step 8 — Outlier handling

Heavy positive skew on (|delta|/rolling_std − 1). Median + IQR were used in the distribution stats; percentile thresholds are robust to outliers.

## Step 9 — Ensemble (multi-seed)

5 seeds × 80% subsample (Step 4 table). Stability of activation rate across seeds is the proxy metric.

## Step 10 — CV rigor (purged walk-forward)

5-fold purged WF (embargo 48 bars).

| pct | fold_activation_mean | fold_activation_std |
|---:|---:|---:|
| p70 | 0.2955 | 0.0317 |
| p75 | 0.2461 | 0.0275 |
| p80 | 0.1972 | 0.0212 |
| p85 | 0.1485 | 0.0157 |
| p90 | 0.0992 | 0.0094 |


## Step 11 — Reproducibility

```json
{
  "data": {
    "calibration_full_sha256": "97dcc627be301ad3673f7841ed0d91b0eef7d6e853af3c17c6062965ae70f659",
    "calibration_full_path": "C:\\data\\processed\\calibration_dataset_full.parquet",
    "rows_in_window": 242381,
    "delta_caveat": "calibration_dataset_full.parquet is the OLD pipeline source; L2 delta range only Jul 2025 -> 2026-04-07 (NOT to 2026-04-24). Tier 4 calibration uses 9.2 months of L2 (vs 9.7 for Tier 1/3 OHLCV)."
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

Rolling std uses look-back only (causal). Threshold derived from full-window ratio distribution; per-fold activation rate stability confirmed in Step 10. No fitted transformer; production runtime computes the same expression bar-by-bar.

## Decision — RECOMMENDATION (no code edit)

**Recommended value:** `vol_climax_multiplier = 0.6528` (most stable fold-activation: p90).

**Diff vs current 0.68206:** -4.3%

**Verdict:** KEEP (within 10% drift; current 0.68206 is consistent with p90-aligned recalibration)

**Apply via FOLLOW-RECALIB-001:** ClaudeCode #1 to update `config/settings.json:77` AND fix the pre-existing `0.682` vs `0.68206` fallback inconsistency at `event_processor.py:2607` and `:3289` (FOLLOW-RECALIB-001 Step 3 per Barbara 2026-04-26).

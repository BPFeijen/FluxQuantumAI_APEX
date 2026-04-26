# Calibration artifact — delta_weakening_threshold (Purdue v2 12-step) — RECOMMEND-ONLY

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 4 (RECOMMEND-only — NO code/settings.json edit per Option A scope)
**Constant:** `delta_weakening_threshold`
**Code locus:** `config/settings.json:79` (consumed at `live/event_processor.py:3263`)
**Current value (in production):** `0.139486 (settings.json:79; same fallback at event_processor.py:3263)`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

> **Scope note:** This artifact is RECOMMEND-ONLY. The recommended values are NOT
> applied to `live/event_processor.py` or `config/settings.json` in this task.
> Follow-up `FOLLOW-RECALIB-001` will coordinate with ClaudeCode #1 (who owns
> `event_processor.py`) and Barbara to apply (or override) the recommendations.

## Operational meaning

Delta weakening fires when `1 − |sum(delta[-10:]) / sum(delta[-20:-10])| > delta_weakening_threshold`. The LHS = `weakening_rate`. Higher = stronger weakening. Spec candidate grid is `{p15, p20, p25, p30}` of the weakening_rate distribution, which produces LOWER thresholds (more permissive — opposite of vol_climax).

## Step 1 — Distribution observation

Distribution of weakening_rate across calibration window (N = 238,231):
- mean = -2.1785, median = 0.0000, std = 11.7536, IQR = 2.1112
- skew = -25.26, kurt = 1309.01 — extreme negative skew + heavy tails. The metric `1 − |recent/older|` is unbounded below (when `|recent| >> |older|`) but bounded above at 1.0 (when recent ≈ 0).

## Step 2 — Feature engineering for skewness

|skew| = 25.26 >> 1; production code compares the raw rate so candidates are reported in raw scale per Step 2 transform-passthrough rule.

## Step 3 — Threshold candidates (per spec literal)

Per spec: percentile-based candidates `{p15, p20, p25, p30}` of weakening_rate. **Note:** these are LOW percentiles → produce LOWER thresholds → MORE permissive trigger (opposite of vol_climax which used p70-p90). The literal spec produces negative thresholds that may not match production semantics — surfaced for Barbara's adjudication.

| pct | threshold | n_active | activation_rate |
|---:|---:|---:|---:|
| p15 | -3.2200 | 202,495 | 0.8500 |
| p20 | -2.1250 | 190,558 | 0.7999 |
| p25 | -1.4862 | 178,673 | 0.7500 |
| p30 | -1.0000 | 165,412 | 0.6943 |


## Step 4 — Confidence interval (multi-seed bootstrap)

Multi-seed bootstrap (5 seeds, 80% subsample) on activation rate per candidate.

| pct | seed_activation_mean | seed_activation_std |
|---:|---:|---:|
| p15 | 0.8500 | 0.0003 |
| p20 | 0.7997 | 0.0004 |
| p25 | 0.7499 | 0.0005 |
| p30 | 0.6941 | 0.0007 |


## Step 5 — Hypothesis test

Like vol_climax, FEAT-4 delta_weakening is currently SHADOW-only (logged at `event_processor.py:3261-3278`). Precision/recall on FEAT-4 veto requires EXEC-8 ground truth — deferred to FOLLOW-RECALIB-001.

## Step 6 — Performance metrics

**Current 0.139486 activation rate:** `0.4647` (46.47% of M1 bars). This sits at roughly p26 (rough estimate given activation rate ≈ 0.46). The spec's p15-p30 candidates are MORE permissive (higher activation), which is opposite to a precision-favouring choice.

## Step 7 — Type I/II trade-off

delta_weakening is a SHADOW indicator that augments LOGIC-C; it does not block. Higher threshold → stricter signal → fewer log entries (Type I↓, Type II↑). Lower (negative) threshold from p15-p30 → very permissive (most bars qualify). Production semantics likely intends `weakening_rate > thr` to be a STRICT signal, so the recommended grid for FOLLOW-RECALIB-001 should be `{p70, p75, p80, p85, p90}` (stricter), not `{p15..p30}`. Surfaced for Barbara's call.

## Step 8 — Outlier handling

Extreme heavy-tailed distribution (skew=-25, kurt very high). Percentile-based thresholds are the only robust choice — mean ± std would be meaningless on this shape.

## Step 9 — Ensemble (multi-seed)

Step 4 table reports per-seed activation stability.

## Step 10 — CV rigor (purged walk-forward)

5-fold purged WF (embargo 48 bars).

| pct | fold_activation_mean | fold_activation_std |
|---:|---:|---:|
| p15 | 0.8518 | 0.0115 |
| p20 | 0.8015 | 0.0114 |
| p25 | 0.7507 | 0.008 |
| p30 | 0.694 | 0.0037 |


## Step 11 — Reproducibility

```json
{
  "data": {
    "calibration_full_sha256": "97dcc627be301ad3673f7841ed0d91b0eef7d6e853af3c17c6062965ae70f659",
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

Rolling sums use look-back only (causal). Threshold derived from full-window rate distribution; per-fold activation rate stability confirmed in Step 10.

## Decision — RECOMMENDATION (no code edit)

**Spec-literal winner:** `delta_weakening_threshold = -1.0000` (p30, most stable fold rate among the {p15..p30} grid).

**Caveat:** the spec-literal winner is `-1.0000`, which is **negative** and corresponds to ~70% activation. This is incompatible with the production intent of a STRICTNESS threshold (where higher = stricter signal). The current value `0.139486` (activation ~46%) is behaviourally more conservative.

**Recommendation to FOLLOW-RECALIB-001:**
1. KEEP current `0.139486` until Barbara confirms intent of the spec's `{p15..p30}` percentile grid (likely typo for `{p70..p90}` — strictness percentiles).
2. If `{p70..p90}` is the intended grid, re-run with that and pick the strictness percentile that maximises EXEC-8 backtest expectancy.
3. Activation rate stability across folds suggests the current value is on a stable plateau; aggressive change is not advised without ground-truth validation.

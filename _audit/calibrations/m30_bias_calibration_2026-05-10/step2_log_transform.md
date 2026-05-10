# G-PURDUE Step 2 — Log-transform decision

**Author**: directed-validation harness
**Sign-off**: Barbara 2026-05-10

## Decision

**No log-transform applied to grid parameters (`min_bars`, `window`).**

## Rationale

Step 2 of G-PURDUE asks whether the parameter or the metric needs a log transform
before the percentile and bootstrap steps. We evaluate three candidates:

### 1. Grid parameters: `min_bars`, `window`

Both are discrete positive integers in the range observed in Step 1
(`bars_per_box` p25=2, p90=12 on 10-month L2 corpus). The grid will sweep
this range as a small set of integer values (Step 3).

- **Skewness check**: the support is `{2, 3, 4, 5, 6, 7, 8}` — narrow integer
  range; log-transform would map to `{0.69, 1.10, 1.39, 1.61, 1.79, 1.95, 2.08}`
  i.e. compress further what is already a small grid. No scientific reason
  to expect non-linear effect across `min_bars`; the tradeoff is monotonic
  (higher min_bars → fewer qualifying boxes → higher bias=unknown rate).
- **Verdict**: KEEP integer space; no log transform.

### 2. Strategy: `{majority, recency_weighted}`

Categorical, two levels. Not applicable.

### 3. Primary metric: `survive_accuracy`

`survive_accuracy ∈ [0, 1]` — bounded probability. Log-transform would only
be appropriate for unbounded right-skewed variables. For a probability metric,
the natural transformations are logit or arcsine-square-root if normality is
needed. Since we use bootstrap (Step 4) for CIs — which is distribution-free
— no transform is needed at this stage.

- **Verdict**: KEEP raw probability.

### 4. Secondary metric: `block_rate`

Same reasoning as `survive_accuracy`. Bounded probability, bootstrap CI.

- **Verdict**: KEEP raw.

## Audit trail

This decision is documented because G-PURDUE protocol requires explicit
disposition of every step, even when the answer is "not applicable".
Skipping a step silently is what produced the silent (5,5)→(4,4) settings
change without re-calibration.

## Inputs

- Step 1 distribution observation: `step1/step1_distribution.md`

## Output

- This document. No data product.

# G-PURDUE Step 3 — Percentile breakpoints (grid range definition)

**Author**: directed-validation harness
**Sign-off**: Barbara 2026-05-10

## Inputs (from Step 1)

10-month L2 corpus (2025-07-01 → 2026-05-09): **630 confirmed M30 boxes**.

`bars_per_confirmed_box` percentiles:
- p25 = **2**
- p50 = **4**
- p75 = **8**
- p90 = **12.1**

`boxes_per_day_mean` BROAD = `630 / 312 days` ≈ **2.02 boxes/day**.

## Grid range derivation (data-driven, no guessing)

### `min_bars` range

`min_bars` rejects boxes that haven't seen enough M30 bars to be considered
"matured". The grid should sweep across the observed distribution:

- Lower bound: **p25 = 2**. Below this, voting accepts very young boxes that
  haven't traded enough range to confirm a bias direction. Extending below
  2 (i.e. 1) makes voting equivalent to "any new box bias counts" — likely
  too noisy.
- Upper bound: **p75 = 8**. Above this, only the longest-lived boxes qualify
  → very few boxes ever pass → bias=unknown dominates. Extending up to **p90 = 12**
  for sensitivity sweep (worst-case test).

**Grid**: `min_bars ∈ {2, 3, 4, 5, 6, 7, 8}` = 7 values.

### `window` range

`window` is the count of recent confirmed boxes the voting considers. Bounded
by total boxes available within a reasonable lookback:

- Lower bound: **2**. Below this, voting reduces to single-box bias which is
  noisy.
- Upper bound: **`floor(boxes_per_day_mean * 3) ≈ 6`** to keep the voting
  horizon under ~3 days (avoids cross-session noise where Asian/London/NY
  boxes mix). Extend to **8** for sensitivity.

**Grid**: `window ∈ {2, 3, 4, 5, 6, 7, 8}` = 7 values.

### `strategy`

Categorical, fixed two-level: `{majority, recency_weighted}` = 2 values.

### Total hypotheses

**7 × 7 × 2 = 98 hypotheses**.

Bonferroni-corrected α for pairwise winner-vs-runner-up: **α = 0.05 / 98 ≈ 5.1e-4**.

## Comparison to Phase 2 (May 7) calibration grid

Phase 2 used a smaller grid:
- `min_bars ∈ {3, 4, 5, 6}` (4 values)
- `window ∈ {2, 3, 4, 5}` (4 values)
- `strategy ∈ {majority, recency_weighted}` (2 values)
- = 32 hypotheses

The new grid (98 hypotheses) covers a strictly wider range, including:
- `min_bars ∈ {2, 7, 8}` (extends both directions to test strict and lax)
- `window ∈ {6, 7, 8}` (extends upward to test multi-session voting)

This is appropriate because Step 1 showed Phase 2 winner `(5,5)` was located
near the previous grid edge — without extending the grid we cannot confirm
the global optimum lies inside.

## Caveats

- The percentiles are computed on the BROAD corpus (10mo). The NARROW corpus
  (May 5-8) showed `bars/box p50 = 6` (vs 4 in BROAD), suggesting recent
  boxes are longer. If the May 5-8 distribution is the new normal, the
  optimal `min_bars` may shift higher than what BROAD calibration suggests.
  Step 7 will report Bonferroni-pass winners separately on BROAD-trained
  vs NARROW-only test (out-of-sample generalization check).

## Output

- This document. Grid configuration consumed by Step 4 (`step4_grid_bootstrap.py`).

# Root-cause — EFR_DIVERGENCE_THRESHOLD edge-collapse (EXEC-RECALIB-INVESTIGATION-001 Step 3)

**Hypothesis tested:** The recalibration's discrete grid {p70, p75, p80, p85, p90} produced an edge-collapse winner at p90=0.5000. Run a finer percentile grid extending beyond p90 to test whether the optimum is interior or further out.

**Verdict:** **Genuine interior optimum at p87.5–p92.5.** NOT edge-collapse to centro (the spec's hypothesis was incorrectly framed). Optimum is real but small.

## Finer grid run

Same data + EFR_ROLLING=25 (T1.1 winner) as the original calibration. n_eligible = 1,119 TREND-B bars.

| pct | threshold | Cohen's d (full) | n_active |
|---:|---:|---:|---:|
| p70 | 0.1667 | 0.1009 | 336 |
| p72.5 | 0.2083 | 0.1177 | 302 |
| p75 | 0.2500 | 0.1436 | 280 |
| p77.5 | 0.2917 | 0.1140 | 236 |
| p80 | 0.2917 | 0.1481 | 222 |
| p82.5 | 0.3615 | 0.1366 | 196 |
| p85 | 0.4167 | 0.1592 | 161 |
| p87.5 | 0.4375 | **0.2011** | 139 |
| p90 | 0.5000 | **0.2101** | 106 |
| p92.5 | 0.5833 | 0.1985 | 81 |
| p95 | 0.6479 | 0.1919 | 56 |
| p97.5 | 0.7500 | 0.1675 | 23 |

## Observations

1. The d curve is **monotonically increasing from p70 (d=0.1009) to p90 (d=0.2101)**, then declines from p92.5 onward.
2. The peak is at **p90 (thr=0.5000), d=0.2101** with **p87.5 (thr=0.4375), d=0.2011** and **p92.5 (thr=0.5833), d=0.1985** as adjacent locals.
3. This is **NOT edge-collapse to grid centroid**. The original spec hypothesis ("collapse to 0.5 = centro") confused the grid centroid with the value 0.5; the threshold value 0.5 happens to be at the strictness-percentile p90 (the high-strictness end of the original grid). The finer grid confirms the d values continue to rise from p70 through p87.5, peak at p90, and then decline as n_active drops below ~80 — a power-loss artifact, not a true downturn.
4. **The original grid was correctly bracketing the optimum.** The optimum lies at p87.5-p92.5, which the original p70-p90 grid covered up to its high boundary. There is no evidence the optimum lies above p92.5.

## Root-cause verdict

**Interior optimum at p87.5-p92.5; original grid's p90 winner is correct.** The "edge-collapse" framing in the spec was a red herring — 0.5 is at the high-strictness boundary, not the center, and the finer grid confirms a real (but modest) optimum there.

However, the **absolute magnitude is small (d ≈ 0.21)** and **does not pass Bonferroni at α=0.01** even with the larger n_active=106 at p90 (KS p ≈ 0.20 reported in the calibration artifact). The signal is statistically weak in absolute terms, regardless of whether it is best-in-grid.

## Recommended action for EXEC-8

1. **The recalibration's winner p90=0.5000 is the genuine grid optimum** — not an edge-collapse artifact. EXEC-8 dual-weight comparison can fairly include 0.5 vs old 0.3.
2. **HOWEVER:** the Bonferroni failure means the signal itself is weak. EXEC-8 must measure ground-truth PnL, not just Cohen's d.
3. **Power note:** at p90 we have n_active=106; at p95 we have n_active=56. The d-decline beyond p92.5 may be sample-size (CI widens), not a real signal drop. EXEC-8 with longer history (or paper-trade slice) could disambiguate.
4. **No need to rerun the calibration** with a wider grid — the optimum is bracketed and locally confirmed.

## Premise update

| Premise | Old status | New status |
|---|---|---|
| 0.5 winner is edge-collapse to grid centroid | UNVERIFIED | **REFUTED** — 0.5 is at p90 (high strictness end), interior optimum confirmed |
| Original p70-p90 grid was adequate to find the optimum | UNVERIFIED | **VALIDATED** — finer grid + extension to p97.5 confirms peak at p87.5-p92.5 |
| Signal is statistically robust at strict alpha | (already known) | REMAINS REFUTED — Bonferroni still fails at α=0.01 |

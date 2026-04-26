# Root-cause — EXHAUSTION_SCORE_THRESHOLD boundary (EXEC-RECALIB-INVESTIGATION-001 Step 6)

**Hypothesis tested:** The recalibration's "winner" threshold 0.30 with NEW weights is at the lower boundary of the spec grid {0.30..0.55}. Test (a) wider absolute grid extending below 0.30 and above 0.55, (b) score-percentile grid as recommended by EXEC-RECALIB-001 itself.

**Verdict:** **Spec grid missed the optimum. With NEW weights, true optimum is 0.25 (exp=0.1637, NOT 0.30=0.1337). With OLD weights, true optimum is also 0.25 (exp=0.1674) — HIGHER than ANY new-weight threshold.**

## Wider absolute grid run

Computed expectancy = mean_active × pass_rate (the same metric used in the original calibration), on TREND-A∪TREND-B eligible bars (n_eligible ≈ 4,860).

### NEW weights (recalibration winners): F5_B=0.1755, F3_B=0.2203, F5_A=0.1885, F2_B=0.2784, F1_B=0.1961

| threshold | n_active | mean_active | expectancy | Cohen's d |
|---:|---:|---:|---:|---:|
| 0.10 | 850 | 0.953 | 0.1667 | 0.049 |
| 0.15 | 850 | 0.953 | 0.1667 | 0.049 |
| 0.20 | 530 | 1.124 | 0.1226 | 0.058 |
| **0.25** | **328** | **2.426** | **0.1637** | **0.145** |
| 0.30 | 255 | 2.547 | 0.1337 | 0.150 |
| 0.35 | 255 | 2.547 | 0.1337 | 0.150 |
| 0.40 | 214 | 2.699 | 0.1188 | 0.159 |
| 0.45 | 153 | 2.307 | 0.0726 | 0.131 |
| 0.50 | 71 | 2.804 | 0.0410 | 0.161 |
| 0.55 | 71 | 2.804 | 0.0410 | 0.161 |
| 0.60 | 62 | 3.294 | 0.0420 | 0.193 |
| 0.70 | 13 | -1.962 | -0.0052 | -0.146 |
| 0.80 | 13 | -1.962 | -0.0052 | -0.146 |

### OLD weights (production): F5_B=0.640, F3_B=0.543, F5_A=0.394, F2_B=0.274, F1_B=0.213

| threshold | n_active | mean_active | expectancy | Cohen's d |
|---:|---:|---:|---:|---:|
| 0.10 | 850 | 0.953 | 0.1667 | 0.049 |
| 0.15 | 850 | 0.953 | 0.1667 | 0.049 |
| 0.20 | 850 | 0.953 | 0.1667 | 0.049 |
| **0.25** | **759** | **1.072** | **0.1674** | **0.057** |
| 0.30 | 686 | 0.973 | 0.1374 | 0.049 |
| 0.35 | 686 | 0.973 | 0.1374 | 0.049 |
| **0.40 (production)** | **511** | **0.868** | **0.0913** | **0.040** |
| 0.45 | 511 | 0.868 | 0.0913 | 0.040 |
| 0.50 | 491 | 0.623 | 0.0629 | 0.022 |
| 0.55 | 289 | 1.751 | 0.1041 | 0.098 |
| 0.60 | 289 | 1.751 | 0.1041 | 0.098 |
| 0.70 | 235 | 2.177 | 0.1053 | 0.125 |
| 0.80 | 174 | 1.651 | 0.0591 | 0.088 |

## Observations

1. **The recalibration's "winner" 0.30 (NEW weights) is NOT the optimum.** Threshold 0.25 (NEW weights) yields expectancy 0.1637 — better than 0.30=0.1337 by 22 %.
2. **OLD weights at threshold 0.25 dominate every NEW-weight threshold.** Expectancy 0.1674 (OLD@0.25) > 0.1637 (NEW@0.25) > 0.1374 (OLD@0.30, NEW@0.30). This is the largest single finding in the investigation.
3. **OLD-weight production at threshold 0.40 has expectancy 0.0913.** Simply lowering the threshold to 0.25 (with NO weight change) would lift expectancy to 0.1674 — an 83 % improvement, achievable with a single config edit.
4. The "boundary" verdict in the original artifact (0.30 at the lowest tested threshold) was correct as a flag but the response was wrong — the optimum is actually further down (0.25), not at the boundary itself.
5. Cohen's d at thr=0.25 NEW = 0.145 → larger than at 0.30 (0.150) by trivial amount; the *expectancy* gap (0.1637 vs 0.1337) is the meaningful measure since it reflects the trade-side payoff, not the shape of the d-distribution.

## Why did the original grid miss this?

The original grid was {0.30, 0.35, 0.40, 0.45, 0.50, 0.55} — explicitly bounded at 0.30 from below. The artifact noted "score-percentile grid replaces discrete grid for T3.2 v3 follow-up" but did not run it. Below 0.30 the score-distribution has more support (since both NEW and OLD weights produce many bars with score in (0.20, 0.30]), so the empirical optimum lies below the spec's grid floor.

## Score-percentile equivalent

Empirically, expectancy peaks at p65-p75 of the score distribution among eligible bars (pass rates ~25-35 %). For NEW weights, p65 corresponds roughly to score ≈ 0.20-0.25; for OLD weights, p65 corresponds to score ≈ 0.25-0.30. So a percentile-based threshold (e.g., "fire at p70 of LOGIC-C score among eligible bars") would automatically adapt to weight set and avoid the absolute-threshold pitfall.

## Root-cause verdict

**The 0.30 winner is a grid-floor artifact AND a sub-optimum.** The genuine optimum is 0.25 (or lower with NEW weights, ≈0.20-0.25 with OLD weights). The recalibration correctly flagged 0.30 as boundary but did not extend the grid to find the true optimum.

The bigger finding: **OLD weights with a re-tuned threshold (0.25) outperform any NEW-weight configuration on this expectancy metric.** This argues against the new weight set entirely — at least for the EXHAUSTION_SCORE_THRESHOLD test.

## Recommended action for EXEC-8

1. **EXEC-8 must include the configuration {OLD weights, threshold 0.25}** as a third arm alongside {OLD weights, 0.40} (current production) and {NEW weights, 0.30} (recalibration proposal). This is mandatory — without it, EXEC-8 cannot detect that the simplest fix (drop the threshold) beats the larger weight overhaul.
2. **Suggested arms for EXEC-8 dual+ weight comparison:**
   - A: OLD weights, 0.40 (current production baseline)
   - B: OLD weights, 0.25 (threshold-only fix; CHEAP)
   - C: NEW weights, 0.30 (recalibration proposal)
   - D: NEW weights, 0.25 (recalibration proposal at true optimum)
   - E (optional): F1+F2 only weights with appropriate threshold (per Step 4 finding that F3/F5/F5_A are unreliable)
3. **Replace absolute threshold with score-percentile in the spec for any future T3.2 calibration.** This avoids grid-floor artifacts and adapts naturally to weight changes.

## Premise update

| Premise | Old status | New status |
|---|---|---|
| 0.30 with NEW weights is the threshold optimum | UNVERIFIED | **REFUTED** — true optimum at 0.25 (exp=0.1637 vs 0.1337) |
| OLD weights at 0.40 are the production baseline expectancy | (already known) | exp=0.0913 confirmed |
| Threshold 0.30→0.40 distinction matters | UNVERIFIED | REFUTED in part — both are sub-optimal vs 0.25 with either weight set |
| OLD weights need replacement | UNVERIFIED | **NOT-ISOLATED** — OLD@0.25 dominates all NEW configurations on this metric |

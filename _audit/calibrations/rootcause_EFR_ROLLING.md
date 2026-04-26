# Root-cause — EFR_ROLLING (EXEC-RECALIB-INVESTIGATION-001 Step 2)

**Hypothesis tested:** EFR_ROLLING winner of 25 (vs old 100) reflects either (a) overfit to recent regime, (b) 100 was always excessive, or (c) genuine optimum at 25.

**Verdict:** **Tie-breaker artifact. Windows 20-100 are statistically indistinguishable.**

## Wider grid run

Computed Cohen's d at p80 split with bootstrap N=200 95 % CI, on TREND-B-eligible bars (n_eligible ≈ 1,119), on the rebuilt M1 OHLCV resampled to M30:

| window | d (p80 split) | bootstrap CI 95 % | n_active |
|---:|---:|---|---:|
| 20 | 0.1449 | [-0.0045, 0.2699] | 218 |
| **25** | **0.1481** | [0.0237, 0.3073] | 222 |
| 30 | 0.1512 | [-0.0047, 0.2998] | 222 |
| 35 | 0.1460 | [0.0146, 0.3213] | 223 |
| 50 | 0.1105 | [-0.0488, 0.2475] | 224 |
| 75 | 0.1091 | [-0.0357, 0.2505] | 224 |
| **100** | **0.1484** | [-0.0013, 0.3022] | 224 |
| 125 | 0.1117 | [-0.0579, 0.2835] | 224 |
| 150 | 0.0962 | [-0.0716, 0.2437] | 224 |
| 200 | 0.0988 | [-0.0480, 0.3164] | 224 |

## Observations

1. The recalibration's "winner" w=25 (d=0.1481) and the production value w=100 (d=0.1484) **differ by 0.0003 in absolute Cohen's d** — well within the bootstrap CI half-width.
2. All 95 % CIs cover the range roughly [-0.05, 0.31]. **No window's CI excludes any other window's point estimate.**
3. There is a soft "M-shape" — d=0.14-0.15 in the {20,25,30,35} range and at w=100, with a depression at w=50,75 and a fall-off at w≥150. This is consistent with random sampling variance, not a genuine optimal-window structure.
4. The original calibration's tie-breaker formula `|d| − 0.5·sqrt(fold_var)` separated 25 from 100 by ~0.004 (0.1481 − 0 = 0.1481 vs 0.1484 − 0.0042 = 0.1442). This is rounding noise.

## Root-cause verdict

**TIE-BREAKER ARTIFACT.** The "75 % reduction" claim is misleading — it implies a quantitatively meaningful optimization, but the underlying d statistic is the same to ~3 significant figures across {20, 25, 30, 35, 100}. The recalibration's choice of 25 over 100 is not statistically supported.

## Recommended action for EXEC-8

1. **Do NOT claim "EFR_ROLLING=25 is the new optimum"** — both 25 and 100 are equally good fits to the data within bootstrap precision.
2. **KEEP 100 in production** unless EXEC-8 dual-weight ground-truth PnL shows a measurable improvement at 25 (≥10 pts/contract on a representative slice). The bootstrap CI alone cannot decide.
3. If EXEC-8 wants to test alternatives, include w∈{25, 30, 35, 100} in the dual-weight grid — they are statistically equivalent at this sample size, so any winner is essentially a coin-flip.
4. **Re-examine the calibration grid resolution.** The original spec used {25, 50, 75, 100, 150, 200}; the wider grid here {20, 25, 30, 35, 50, 75, 100, 125, 150, 200} confirms d≈0.10-0.15 is a wide plateau, not a single optimum.

## Premise update

| Premise | Old status | New status |
|---|---|---|
| EFR_ROLLING=25 is meaningfully better than 100 on rebuild data | UNVERIFIED | **REFUTED** — same d to within bootstrap noise |
| The 75 % reduction is a real optimization | UNVERIFIED | REFUTED — tie-break artifact |

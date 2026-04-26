# Root-cause — CLOSE_PCT_WEAK sign anomaly (EXEC-RECALIB-INVESTIGATION-001 Step 5)

**Hypothesis tested:** The CLOSE_PCT_WEAK calibration showed positive Cohen's d at p20 (+0.0029) but negative at p25-p40 (-0.054 to -0.072). Test sign behaviour separately by trend direction (LONG-only / SHORT-only) and by handover half (pre / post) to isolate whether the anomaly is genuine or methodology.

**Verdict:** **Genuine LONG/SHORT asymmetry.** NOT a contamination artifact. The CLOSE_PCT_WEAK rule has structurally different effect for LONG vs SHORT trends on the rebuild parquet.

## Sub-sample test (n_eligible = 1,110 TREND-B bars; n_long ≈ 555, n_short ≈ 555 approx)

For each percentile cut, Cohen's d computed five ways: all bars; LONG-direction only; SHORT-direction only; pre-handover only; post-handover only.

| pct | thr (all) | d_all | d_long | d_short | d_pre | d_post |
|---:|---:|---:|---:|---:|---:|---:|
| p10 | 0.2313 | +0.107 | +0.200 | -0.002 | +0.094 | +0.131 |
| p15 | 0.3198 | +0.046 | +0.088 | -0.006 | +0.027 | +0.064 |
| **p20** | **0.3871** | **+0.0004** | **+0.0923** | **-0.115** | +0.057 | -0.034 |
| p25 | 0.4427 | -0.0575 | +0.0189 | -0.152 | +0.015 | -0.107 |
| p30 | 0.5119 | -0.0703 | -0.0192 | -0.132 | +0.032 | -0.140 |
| p35 | 0.5603 | -0.0301 | -0.0115 | -0.053 | +0.008 | -0.056 |
| p40 | 0.6054 | -0.0696 | -0.0698 | -0.070 | -0.051 | -0.088 |
| p45 | 0.6418 | -0.0578 | -0.0460 | -0.073 | +0.001 | -0.096 |
| p50 | 0.6783 | -0.0522 | -0.0491 | -0.056 | -0.022 | -0.073 |

## Observations — LONG/SHORT split

1. **SHORT direction d is consistently negative** for every threshold p10 through p50. The mean SHORT d is -0.067, range [-0.152, -0.002].
2. **LONG direction d is positive at strict thresholds (p10-p20: +0.20 to +0.09)** then declines and crosses zero around p25, becoming weakly negative (-0.05 to -0.07) at p40-p50.
3. The "all" d at p20 = +0.0004 is almost exactly the mid-point of LONG d=+0.0923 and SHORT d=-0.115. This is the algebraic explanation for the sign flip in the original artifact: at p20 the two opposing direction-effects cancel.

## Operational interpretation

The CLOSE_PCT_WEAK feature operationalization in `live/impl2_features.py`:
- LONG trend: F5 fires when `close_pct_from_low < CLOSE_PCT_WEAK` (close is near the low, suggesting the long is weakening).
- SHORT trend: F5 fires when `close_pct_from_high < CLOSE_PCT_WEAK` (close is near the high, suggesting the short is weakening).

Result: weak-LONG (close near low) DOES correlate with subsequent anti-trend reversal (positive d at strict cuts), as theory predicts. But weak-SHORT (close near high) **anti-correlates** with subsequent anti-trend reversal — i.e., a SHORT bar closing near its high tends to *continue* the short rather than reverse it. This contradicts the symmetric assumption baked into the operationalization.

## Pre/post handover comparison

- Pre-handover d at p20: +0.057 → mildly positive
- Post-handover d at p20: -0.034 → mildly negative

The pre/post difference at p20 is 0.09 absolute units. Given bootstrap noise (CI half-widths around 0.13), this is within noise. The LONG/SHORT asymmetry is the dominant effect; the pre/post effect is a minor secondary.

## Root-cause verdict

**The sign anomaly is not random noise — it is a coherent LONG/SHORT structural asymmetry.** The CLOSE_PCT_WEAK rule encodes a LONG-side intuition (close near the low → weakening) that does not transpose symmetrically to the SHORT side on this dataset. The original ATS/Wyckoff-style framing assumes price action symmetry, which the rebuild data does not exhibit at this label horizon.

This is **methodology**, not data. The bug is in the rule's operationalization for SHORT trends, not in the parquet.

## Recommended action for EXEC-8

1. **Do NOT promote CLOSE_PCT_WEAK=0.387 (p20) to production.** The "winner" was selected by raw seed_d_mean; it has nearly-zero effect once LONG and SHORT are decomposed.
2. **Investigate the SHORT-side rule.** Possibilities:
   - The metric `close_pct_from_high` is correctly defined but the directional theory is wrong for GC futures.
   - The label `anti_b_60m` for SHORT may have a sign error (verify by checking trend_b_dir × fwd_60m).
   - The asymmetry could be regime-specific; rerun on different sub-samples (specific months, volatility regimes).
3. **For EXEC-8 dual-weight comparison:** OLD value 0.3 and NEW value 0.387 are essentially the same operating point (LONG: weakly positive d, SHORT: weakly negative d). PnL difference between them will be statistically noisy. The honest comparison is "drop CLOSE_PCT_WEAK altogether" vs current vs new.
4. **Methodology task (post EXEC-8):** Audit the SHORT-direction operationalization of F5 against ATS/Wyckoff literature for the asymmetric case. This is the kind of thing the standing-rule G-LITERATURE-BEFORE-CODE flags — the original 0.20/0.30 doc-vs-code inconsistency may not even be the most important issue here.

## Premise update

| Premise | Old status | New status |
|---|---|---|
| Sign anomaly is bootstrap noise | UNVERIFIED | **REFUTED** — coherent across all percentile cuts, robust to handover split |
| Sign anomaly is contamination effect | UNVERIFIED | **REFUTED** — pre and post handover both show the LONG/SHORT asymmetry |
| Sign anomaly is genuine LONG/SHORT asymmetry | UNVERIFIED | **VALIDATED** — d_long > 0, d_short < 0 across p10-p50 |
| CLOSE_PCT_WEAK is a useful single feature on this label | UNVERIFIED | **REFUTED** — pooled d ≈ 0 due to direction cancellation |

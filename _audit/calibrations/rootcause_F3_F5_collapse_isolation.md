# Root-cause — F3_B / F5_A / F5_B collapse isolation (EXEC-RECALIB-INVESTIGATION-001 Step 4)

**Hypothesis tested:** F3/F5 weight collapse 50-76 % is caused by DATA-002 P1.5 fix removing OHLCV contamination. Isolation test: split rebuild parquet by HANDOVER_CUTOFF (2025-11-26). Pre-handover (kept-as-is from prod, presumed always clean) vs post-handover (was buggy under m30_updater, now rebuilt clean).

**Verdict:** **DATA-002 isolation REFUTED.** F3/F5 weakness is structural, not contamination-driven.

## Isolation test results

Cohen's d on rebuild parquet, computed three ways: full Window B, pre-handover only, post-handover only. Eligibility per feature (TREND-A or TREND-B). Sample sizes balanced — pre-handover gets 4,861 M30 bars, post-handover 3,939.

| Feature | Full d | Pre-handover d | Post-handover d | Sign change? | n_active pre / post |
|---|---:|---:|---:|---|---:|
| F1_B | 0.1649 | 0.0458 | 0.2301 | no (both ≥0) | 112 / 122 |
| F2_B | 0.1323 | 0.2011 | 0.1067 | no (both ≥0) | 117 / 100 |
| **F3_B** | **-0.0457** | **+0.1552** | **-0.1468** | **YES** | 181 / 214 |
| **F5_A** | **+0.062** | **-0.1298** | **+0.1700** | **YES** | 99 / 91 |
| F5_B | +0.0259 | +0.0068 | +0.0415 | no (both ≈0) | 87 / 67 |

## What the original calibration claimed

Original "pre-rebuild" |d|: F3_B=0.601, F5_A=0.661, F5_B=0.738. Rebuild calibration weight (mean |d| over 5 folds): F3_B=0.220, F5_A=0.189, F5_B=0.176.

If DATA-002 contamination is the cause:
- Pre-handover d should match the original high values (~0.6+) since pre-handover OHLCV is identical between old prod parquet and rebuild parquet.
- Post-handover d should be the small new values (~0.2) since post-handover is what got rebuilt clean.

## What we actually observe

1. **Pre-handover d for F3_B (+0.155), F5_A (-0.130), F5_B (+0.007) are NOT high.** They are small, comparable in magnitude to the post-handover values, and for F5_A even has the *opposite* sign. The original 0.6+ d values cannot be reproduced on the pre-handover (clean, unchanged) data.
2. **F3_B and F5_A flip sign** between pre and post handover. A genuine signal does not change direction across two contiguous time slices of the same instrument. This indicates the features are essentially **noise** on this label/eligibility combination.
3. **F5_B is noise on both halves** (pre +0.007, post +0.042). It cannot be carrying a "real" signal that contamination just inflated.

## Implication

The "DATA-002 inflated old values" narrative is incomplete. Possible explanations consistent with the data:

- **(A) Methodology drift between original calibration and recalibration.** The original calibration may have used a different label, eligibility, or scoring that produced the 0.6+ values; the recalibration's mean-|d|-over-folds is a different statistic.
- **(B) Contamination + methodology combined.** Post-handover contamination may have produced phantom-high d via a specific OHLC pattern that interacted with the original calibration's label, while clean post-handover (this run) shows the truth.
- **(C) Regime drift.** Pre-handover (Jul-Nov 2025) and post-handover (Dec 2025-Apr 2026) may simply be different regimes for the F3/F5 setup — the sign flip is direct evidence the features have no time-stable directionality.

What is RULED OUT:
- "DATA-002 alone explains the collapse" — this requires pre-handover to retain the high d, which it does NOT.
- "F3/F5 are reliable but with smaller magnitude on clean data" — sign flips between halves for F3_B and F5_A indicate the features are not directionally reliable.

## What about F1_B and F2_B?

Stable across halves (no sign flip) and consistent with the original `KEEP` verdicts on these. F1_B is the most consistent feature — d ≥ 0 in all our subsets and across all 5 calibration folds. F2_B is also stable, though pre>post.

## Root-cause verdict

**F3_B, F5_A, F5_B are directionally unreliable on the rebuild parquet.** The collapse is not a contamination artifact; it reflects features that were never as robust as the original calibration claimed (or that have undergone real regime change unrelated to DATA-002). Promoting either old or new weights to production for these three features is risky — neither set is grounded in a directionally consistent signal.

F1_B and F2_B are stable; safe to retain.

## Recommended action for EXEC-8

1. **F3_B / F5_A / F5_B: do not promote new weights, but do not trust old weights either.**
   - Treat all three as `WEIGHT=0.0` (functionally remove from LOGIC-C) for an EXEC-8 ablation arm. If LOGIC-C performance is undamaged with these features dropped, they were carrying noise.
   - Alternatively, run EXEC-8 with three weight sets: OLD, NEW, F1+F2-only. Compare ground-truth PnL.
2. **F1_B / F2_B: KEEP as-is.** The pre/post split shows stable directionality.
3. **EXHAUSTION_SCORE_THRESHOLD must be re-tuned per weight set** — see Step 6. The NEW threshold 0.30 was tuned to NEW weights; an F1+F2-only set would have a different optimal threshold (max sum of weights = 0.196 + 0.278 = 0.474).
4. **Premise** "DATA-002 explains the F3/F5 collapse" is REFUTED. Future calibration narratives should not rely on this attribution.

## Premise update

| Premise | Old status | New status |
|---|---|---|
| DATA-002 P1.5 contamination explains F3/F5 collapse | UNVERIFIED | **REFUTED** — pre-handover (clean both old and new) also shows weak/sign-flipped F3/F5 d |
| F3_B, F5_A, F5_B carry directionally consistent signal | UNVERIFIED | **REFUTED** — F3_B and F5_A flip sign between pre/post halves; F5_B is noise on both |
| F1_B, F2_B carry stable signal | (already known) | **VALIDATED** — both halves show same-sign d |
| Original 0.6+ d values were inflated by contamination | UNVERIFIED | NOT-ISOLATED — contamination cannot fully explain the difference (pre-handover does not reproduce 0.6+) |

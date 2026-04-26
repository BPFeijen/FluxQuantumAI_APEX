# EXEC-RECALIB-001 — Final Summary

**Task:** Recalibração Purdue v2 12-step de TODOS thresholds IMPL-2/3/4 (Tier 1-4) com parquet clean
**Asana:** [1214280786215897](https://app.asana.com/1/1214194575879715/project/1214204918416708/task/1214280786215897)
**Executor:** ClaudeCode #2
**Mode:** Option A (6 fully-applied calibrations + 2 RECOMMEND-only Tier 4)
**Time spent:** ~2h 30min (within 4h hard cap)
**Generated:** 2026-04-26

---

## SANITY

| Item | Value |
|---|---|
| Capture services | ✅ 8000 PID 19096 LISTEN, 8002 PID 1776 LISTEN throughout |
| HEAD | `5fea06f IMPL-4: FEAT-4 Anti-Exit + IPC shared state + 4 hook points integration` |
| Rebuilt parquet sha256 | `9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b` |
| Standing rules 11+12 | NO push, NO --amend, NO history mutation |
| File-ownership conflict | Resolved via Option A — Tier 4 RECOMMEND-only, no edits to `event_processor.py` (#1's territory) or `config/settings.json` (deferred to FOLLOW-RECALIB-001) |

## PRAC — Pre-Response Adversarial Check

1. **Overfit risk:** Calibration runs on a single 9.7-month Window B (Jul 2025 → 2026-04-25). Multi-seed bootstrap + 5-fold purged WF mitigates within-window overfit; no out-of-window holdout exists for the rebuilt-clean data (older periods predate rebuild). Future regime drift may invalidate; flag for periodic re-cal trigger per Step 8 v2 protocol.
2. **Confirmation bias:** Percentile-grid candidates are data-agnostic; Cohen's d and KS are signed/symmetric tests. Verdicts (KEEP/UPGRADE/INVESTIGATE) followed pre-specified diff bands `{≤10%, 10-25%, >25%}` per spec.
3. **Premise fragility:** Load-bearing premise = "rebuilt parquet is clean post-DATA-002 P1.5". Verified via rebuild.log + sha256 + schema check; OHLCV NaN-free; date range Jul 2025 → 2026-04-24. If a deeper contamination remains, ALL constants drift commensurately.
4. **Adversarial questions answered:**
   (a) Why no Window A sensitivity? — Spec made it optional; rebuilt parquet only spans pre-rebuild + post-rebuild differently; comparing on un-rebuilt window would conflate signal change vs cleanliness change.
   (b) Why Cohen's d label = `anti_trend_{a,b}_fwd_60m` instead of true PnL? — Maintains lineage with original calibration; PnL is EXEC-8's job.
   (c) Why use OLD `gc_m30_boxes.parquet` for regime gating in Tier 1/3? — Rebuilt parquet is M1 OHLCV only (no regime features). Caveat documented in every artifact's Step 11.
5. **Missing tests:**
   - No EXEC-8-style PnL backtest comparison (out of scope; this task feeds EXEC-8).
   - Score-percentile threshold grid for T3.2 (recommended for v3 follow-up; current finer grid `{0.30..0.55}` partly degenerate due to discrete score support).
   - vol_climax_multiplier and delta_weakening_threshold precision-of-veto evaluation requires FEAT-4 to be unblocked — currently SHADOW-only.

## PREMISES INHERITED (status verified at start)

| Premise | Source | Status |
|---|---|---|
| Rebuilt parquet clean | rebuild.log + sha256 | VALIDATED |
| Audit findings (3 INVENTED + bug + 2 DOC + 2 magic) | spec + my own audit | VALIDATED |
| 12-step Purdue binding | DEC-2026-04-24-003 v2 | VALIDATED |
| Cohen's d label = `anti_trend_*_fwd_60m` | INTERACTIONS_v2 §2.1 | VALIDATED |
| #1 owns event_processor.py / settings.json conflict | spec parallel constraint | VALIDATED |
| HEAD past EXEC-6 (IMPL-4 already shipped) | git log | VALIDATED post hoc — spec assumed pre-EXEC-6 |

## PREMISES CREATED (downstream)

- **Multi-seed list** `[42, 1337, 2024, 7, 1729]` — frozen for all future EXEC-RECALIB-* cycles.
- **Embargo size 48 bars** for purged WF — ~1 trading day at M30; informs FOLLOW-RECALIB-001.
- **Calibration artifact format** — 12-section .md template under `_audit/calibrations/calibration_<constant>_v2.md`.
- **Score-percentile follow-up flagged** for T3.2 v3 (current discrete grid is partly degenerate).

---

## RESULTS — All 8 calibrations

| Constant | Tier | Old value | New (winner) | Diff % | Verdict | Artifact |
|---|---|---:|---:|---:|---|---|
| EFR_ROLLING | T1 | 100 | 25 | -75% | INVESTIGATE | `calibration_EFR_ROLLING_v2.md` |
| EFR_DIVERGENCE_THRESHOLD | T1+T2 | 0.3 (code) / 0.20 (doc) | 0.5000 (p90) | +66.7% / +150% | INVESTIGATE | `calibration_EFR_DIVERGENCE_THRESHOLD_v2.md` |
| CLOSE_PCT_WEAK | T1 | 0.3 | 0.387 (p20) | +29% | INVESTIGATE (sign anomaly p25-p40) | `calibration_CLOSE_PCT_WEAK_v2.md` |
| Tier 2 EFR threshold bug reconcile | T2 | n/a | RESOLVED | n/a | RESOLVED | `calibration_TIER2_BUG_RECONCILE_v2.md` |
| FEATURE_WEIGHTS — F1_B | T3 | 0.213 | 0.196 ±0.115 | -7.9% | KEEP | `calibration_FEATURE_WEIGHTS_v2.md` |
| FEATURE_WEIGHTS — F2_B | T3 | 0.274 | 0.278 ±0.234 | +1.6% | KEEP | (same) |
| FEATURE_WEIGHTS — F3_B | T3 | 0.543 | 0.220 ±0.061 | -59.4% | INVESTIGATE | (same) |
| FEATURE_WEIGHTS — F5_A | T3 | 0.394 | 0.189 ±0.189 | -52.2% | INVESTIGATE | (same) |
| FEATURE_WEIGHTS — F5_B | T3 | 0.640 | 0.176 ±0.069 | -72.6% | INVESTIGATE | (same) |
| EXHAUSTION_SCORE_THRESHOLD | T3 | 0.4 | 0.30 (with new weights) | -25% | UPGRADE / INVESTIGATE boundary | `calibration_EXHAUSTION_SCORE_THRESHOLD_v2.md` |
| vol_climax_multiplier | T4 RECOMMEND | 0.68206 | 0.6528 (p90) | -4.3% | KEEP (RECOMMEND-only, no code edit) | `calibration_vol_climax_multiplier_v2.md` |
| delta_weakening_threshold | T4 RECOMMEND | 0.139486 | -1.0000 (p30) — spec-literal degenerate | -100% (literal) | KEEP current pending Barbara adjudication of grid spec | `calibration_delta_weakening_threshold_v2.md` |

## LOAD-BEARING FINDING

**All 5 IMPL-2 features and the LOGIC-C composite show materially weaker Cohen's d on the rebuilt-clean parquet vs the pre-rebuild calibration:**

| | Original (pre-rebuild) |d| | Rebuilt-clean |d| | drop |
|---|---:|---:|---:|
| F1_B | 0.213 | 0.196 | -7.9% |
| F2_B | 0.303 (TREND-B at p60) | 0.278 | -8.3% |
| F3_B | 0.601 | 0.220 | -63% |
| F5_A | 0.661 | 0.189 | -71% |
| F5_B | 0.738 | 0.176 | -76% |
| EFR/ClosePct individually | KS p ≈ 1e-6 | KS p ≈ 0.05-0.95 | none pass Bonferroni |

**Interpretation:** The DATA-002 P1.5 fix (rebuilt OHLCV from clean trades, `_micro_to_m1` joiner bug removed) materially altered bar shapes for the F3/F5 family. Pre-rebuild calibration was on contaminated OHLCV → effect sizes inflated. Post-rebuild signal is the honest baseline going to production.

**Implication:** Pre-rebuild LOGIC-C expectancy of `+0.1312` pts/bar @+60m may NOT survive on clean data. New weights at threshold 0.30 produce expectancy `+0.1337` on the new calibration (similar magnitude, different distribution). EXEC-8 ground-truth backtest on rebuilt parquet is the only reliable arbiter.

---

## FINAL RECOMMENDATION

**FIX REQUIRED FIRST — DO NOT PROCEED WITH EXEC-8 BACKTEST UNTIL:**

1. **Cross-coord with ClaudeCode #1 (FOLLOW-RECALIB-001)** to apply Tier 4 recommendations to `config/settings.json` and the `0.682`/`0.68206` event_processor.py fallback inconsistency.
2. **EXEC-8 backtest must run BOTH old and new weight sets** for ground-truth PnL comparison. Without ground-truth comparison, the INVESTIGATE-verdict constants cannot be safely promoted.
3. **For Tier 3 KEEP-verdict (F1_B, F2_B):** safe to retain old values; no code edit needed; document v2 lineage in commit message.
4. **For Tier 3 INVESTIGATE-verdict (F3_B, F5_A, F5_B):** do NOT apply new values without EXEC-8 sign-off. The rebuilt-clean weights collapse magnitudes; production expectancy is unverified.
5. **For Tier 1 INVESTIGATE-verdict:** all 3 fail Bonferroni at alpha=0.01; do not apply without EXEC-8 sign-off.
6. **For T3.2 threshold:** the finer-grid winner `0.30` is degenerate vs `0.35` due to discrete score support. A score-percentile grid is needed for a clean recalibration (T3.2 v3 follow-up).

## OUT OF SCOPE (deferred per Barbara 2026-04-26)

- `config/settings.json` edits → FOLLOW-RECALIB-001
- `0.682` vs `0.68206` event_processor.py fallback inconsistency fix → FOLLOW-RECALIB-001 Step 3
- Window A sensitivity check → optional per spec, not run

## NO push, NO amend, NO mutate history (Standing rules 11+12 honoured)

Local commit only. Files added:
- `_audit/calibrations/` — 8 .md artifacts (7 calibrations + 1 Tier 2 bug reconcile)
- `_audit/calibrations/raw/` — 3 raw JSON outputs (tier1_raw.json, tier3_raw.json, tier4_raw.json)
- `scripts/recalibration_common.py` — Pipeline + purged WF helpers
- `scripts/recalibration_tier1.py` — Tier 1 calibration runner
- `scripts/recalibration_tier1_artifacts.py` — Tier 1 artifact generator
- `scripts/recalibration_tier3.py` — Tier 3 calibration runner
- `scripts/recalibration_tier3_artifacts.py` — Tier 3 artifact generator
- `scripts/recalibration_tier4.py` — Tier 4 calibration runner
- `scripts/recalibration_tier4_artifacts.py` — Tier 4 artifact generator

No edits to `live/impl2_features.py` or `live/impl3_logic_c.py` (all verdicts INVESTIGATE or KEEP — no UPGRADE warranted production code change without EXEC-8 backtest).

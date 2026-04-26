# EXEC-RECALIB-INVESTIGATION-001 — Final Summary

**Task:** EXEC-RECALIB-INVESTIGATION-001 (Asana 1214283093312730)
**Executor:** ClaudeCode #2
**Mode:** Read-only investigation (NO production-code edits, NO config edits, NO history mutation)
**Time spent:** ~30 min (well within 90 min hard cap)
**Generated:** 2026-04-26

---

## SANITY

| Item | Value |
|---|---|
| Capture services | ✅ 8000 + 8002 LISTENING throughout (verified at start, mid-run, end) |
| HEAD at task start | `e9b5aad CTRADER-INTEGRATION-001` (parent `135c426` EXEC-RECALIB-001 unchanged) |
| Production code edits | ZERO — `live/`, `config/` untouched |
| Standing rule 11 (NO push) | honoured |
| Standing rule 12 (NO history mutation) | honoured |
| Standing rule 10 (NO touch capture) | honoured |
| Standing rule 8 (G-PREMISE-AUDIT explicit) | applied per Step (see premise tables in each rootcause_*.md) |
| New artifacts | 7 files written under `_audit/calibrations/` (6 root-cause + 1 summary) + 1 raw JSON + 1 investigation script |

---

## PRAC — Pre-Response Adversarial Check (5 checks)

1. **Did I open all 8 calibration artifacts + 7 scripts?** ✅ Yes — all 9 files in `_audit/calibrations/` (8 calibrations + 1 EXEC-RECALIB-001_summary.md) read in full; 3 of 7 scripts read (`recalibration_common.py`, `recalibration_tier1.py`, `recalibration_tier3.py`) which contain the load-bearing logic — Tier 4 RECOMMEND-only scripts deferred to lower priority since their findings were already documented as "no apply" in artifacts.
2. **Did I run actual isolation tests OR speculate?** ✅ Wrote a single 350-line investigation script `scripts/recalib_investigation_001.py`, ran it to completion, analyzed empirical Cohen's d on the rebuild parquet across (a) wider EFR_ROLLING grid, (b) finer EFR_DIVERGENCE grid extending beyond p90, (c) pre-handover vs post-handover F3/F5 split, (d) LONG-only/SHORT-only/pre/post sub-samples for CLOSE_PCT_WEAK, (e) wider EXHAUSTION_SCORE absolute and percentile grids, with multi-seed bootstrap on each.
3. **Did I touch capture services?** ✅ No — verified LISTENING at start, mid-run, end via `netstat` only. No process-restart attempted.
4. **Did I commit changes during investigation?** ✅ No commits yet — investigation files written to disk but NOT staged or committed, per spec ("Read-only investigation: NO commits expected").
5. **Did I declare 'isolated' based on correlational evidence without dual-test?** ✅ No — Step 4 explicitly tests pre-handover vs post-handover and the headline conclusion is **DATA-002 isolation REFUTED** (the dual-test contradicts the original isolation claim). Also flagged that EFR_ROLLING winner=25 is a tie-break artifact — bootstrap CIs in Step 2 cover the contradiction.

---

## RESULTS — Per-constant verdicts

| Constant | Hypothesis tested | Verdict | Recommended action for EXEC-8 |
|---|---|---|---|
| **EFR_ROLLING** | overfit recent vs always-excessive vs genuine 25-optimum | **TIE-BREAK ARTIFACT** — w∈{20,25,30,35,100} statistically indistinguishable (d=0.144-0.151, all bootstrap CIs overlap) | KEEP 100 in production; treat 25 as equivalent. EXEC-8 dual-weight need not include this dimension. |
| **EFR_DIVERGENCE_THRESHOLD** | edge-collapse to grid centroid vs interior optimum | **GENUINE INTERIOR OPTIMUM at p87.5-p92.5** (d peaks 0.20-0.21 here, monotone increase from p70). Spec hypothesis ("centro 0.5") was incorrectly framed — 0.5 is at p90, the strictness-grid high boundary. | New value 0.5 is the true grid optimum but signal is weak (Bonferroni still fails). EXEC-8 dual-weight: include OLD 0.3 vs NEW 0.5 with explicit understanding that the d-difference is small. |
| **CLOSE_PCT_WEAK** | sign anomaly — bootstrap noise vs genuine LONG/SHORT asymmetry | **GENUINE LONG/SHORT ASYMMETRY** — d_long > 0 at strict thresholds, d_short < 0 across all thresholds. Cancels at pooled p20. | Do NOT promote 0.387. Investigate SHORT-side rule operationalization (potential methodology bug). EXEC-8 honest comparison: drop CLOSE_PCT_WEAK altogether vs old (0.3) vs new (0.387). |
| **F3_B / F5_A / F5_B** | DATA-002 isolated as cause of 50-76 % collapse | **DATA-002 ISOLATION REFUTED** — pre-handover (clean both old and new) shows weak/sign-flipped d. F3_B and F5_A flip sign between pre and post halves; F5_B is noise on both. | Treat all three as candidates for `WEIGHT=0.0` ablation arm in EXEC-8. Run with OLD weights, NEW weights, and F1+F2-only. F3/F5 carry no directionally consistent signal on this dataset. |
| **EXHAUSTION_SCORE_THRESHOLD** | grid-resolution artifact vs genuine improvement | **GRID-FLOOR ARTIFACT — true optimum is 0.25, not 0.30.** And: **OLD weights @ 0.25 (exp=0.1674) DOMINATE all NEW-weight configurations.** | EXEC-8 must include arm `{OLD weights, threshold 0.25}` — a single threshold drop beats the larger weight overhaul. Replace absolute threshold with score-percentile in any future T3.2 calibration. |

## Premise table (updated post-investigation)

| Premise | Pre-investigation status | Post-investigation status |
|---|---|---|
| Rebuilt parquet is clean post-DATA-002 P1.5 | UNVERIFIED | **PARTIALLY VALIDATED** — clean over post-handover (~45 % of Window B); pre-handover (~55 %) was always clean and unchanged |
| Rebuilt parquet covers full 9.7-month window | UNVERIFIED | VALIDATED — 93.5 % weekday coverage (gaps are documented holidays + L2-inventory items) |
| F3/F5 collapse causa = DATA-002 | UNVERIFIED | **NOT-ISOLATED / REFUTED** — pre-handover (always clean) also shows weak/sign-flipped d; contamination cannot fully explain |
| EFR_ROLLING=25 is a meaningful optimization vs 100 | UNVERIFIED | **REFUTED** — same d to within bootstrap noise |
| EFR_DIVERGENCE 0.5 winner is edge-collapse | UNVERIFIED | **REFUTED** — interior optimum genuine at p87.5-p92.5 |
| CLOSE_PCT_WEAK sign anomaly is bootstrap noise | UNVERIFIED | **REFUTED** — coherent LONG/SHORT structural asymmetry |
| F1_B / F2_B carry stable signal | (already KEEP) | **VALIDATED** — both halves same-sign d |
| EXHAUSTION_SCORE 0.30 is the true optimum | UNVERIFIED | **REFUTED** — true optimum is 0.25 (NEW weights) or 0.25 (OLD weights, even higher expectancy) |
| OLD weights are inferior | UNVERIFIED | **REFUTED** for the EXHAUSTION_SCORE expectancy metric — OLD@0.25 wins |

## Premises CREATED downstream (impact on EXEC-8)

- **EXEC-8 must include arm `{OLD weights, threshold 0.25}` (cheap threshold-only fix).** Without this arm, EXEC-8 cannot detect the dominant alternative.
- **F3_B / F5_A / F5_B should have an ablation arm in EXEC-8** (`WEIGHT=0.0` to test whether they carry any signal at all).
- **CLOSE_PCT_WEAK SHORT-side rule is suspect** — flag for ATS/Wyckoff literature audit BEFORE any future calibration.
- **Score-percentile threshold replaces absolute threshold** as the recommended T3.2 v3 protocol — already flagged in EXEC-RECALIB-001 but now load-bearing.
- **EFR_ROLLING grid resolution is too coarse** — tie-break logic should be replaced with explicit equivalence-bands when Cohen's d differences are within bootstrap CI.

---

## FINAL RECOMMENDATION

### **EXEC-8 GO WITH MULTI-ARM COMPARISON (not GO/NO-GO binary).**

The original "old vs new weight set" binary was the right safeguard but is no longer sufficient. EXEC-8 should compare **at least 4 configurations**:

| Arm | Weights | Threshold | Rationale |
|---|---|---|---|
| **A** (current production) | OLD | 0.40 | Status quo |
| **B** (cheap-fix) | OLD | 0.25 | Threshold drop only — investigation Step 6 finds this dominates everything |
| **C** (recalibration proposal) | NEW | 0.30 | What EXEC-RECALIB-001 originally proposed |
| **D** (recalibration at true optimum) | NEW | 0.25 | NEW weights at the actual T3.2 optimum |
| **E** (ablation) | F1+F2 only (others=0) | re-tuned | Tests whether F3/F5/F5_A carry any signal |

If arm **B** ties or beats **C**/**D** on PnL ground-truth, the recalibration weight changes are not justified — only the threshold change is. This is the single largest finding of the investigation.

### **Constants that should NOT change in production (post-EXEC-8 sign-off):**

- **EFR_ROLLING:** keep 100 (no statistical case for 25)
- **CLOSE_PCT_WEAK:** keep 0.3 (no statistical case for 0.387; methodology bug suspected on SHORT side — flag for separate audit)
- **F3_B / F5_A / F5_B weights:** decide based on EXEC-8 arm E (ablation) — current weights cannot be defended on rebuild data, but neither can the new weights

### **Constants worth changing (with EXEC-8 sign-off):**

- **EFR_DIVERGENCE_THRESHOLD 0.3 → 0.5** is defensible (genuine grid optimum, weak signal)
- **EXHAUSTION_SCORE_THRESHOLD 0.4 → 0.25** is the highest-leverage single change — works with EITHER old or new weights

### **Pre-EXEC-8 blockers to resolve:**

None. EXEC-8 GO with the above multi-arm protocol.

### **Post-EXEC-8 follow-ups:**

1. Score-percentile T3.2 v3 calibration (already flagged in EXEC-RECALIB-001).
2. CLOSE_PCT_WEAK SHORT-side methodology audit vs ATS/Wyckoff literature.
3. F3/F5 feature-level audit: are these carrying noise on the current label, or is the operationalization specifically broken on rebuild data?
4. EFR_ROLLING grid-tie-break protocol — equivalence bands instead of absolute argmax.

---

## Reproducibility

| Field | Value |
|---|---|
| Investigation script | `scripts/recalib_investigation_001.py` |
| Raw JSON output | `_audit/calibrations/raw/investigation_001_raw.json` |
| Rebuilt parquet sha256 | `9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b` (matches calibration artifacts) |
| Boxes parquet sha256 | `50328224b657a0bc11300f359d9b4ae31251014648c0b816b4f7ed4bbd1719de` |
| Calibration_full sha256 | (read from common; same as calibration artifacts) |
| Seeds | `[42, 1337, 2024, 7, 1729]` (frozen per EXEC-RECALIB-001) |
| Embargo | 48 bars (~1 trading day at M30) |
| Window B | 2025-07-01 → 2026-04-25 UTC |

## Read-only contract

NO push, NO history mutation, NO production code changes. New files all under `_audit/calibrations/` and `scripts/`; commit (if approved) is the final step per spec.

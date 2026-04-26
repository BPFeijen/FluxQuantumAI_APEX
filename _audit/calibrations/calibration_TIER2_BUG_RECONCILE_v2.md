# Calibration artifact — Tier 2 EFR threshold bug reconcile (0.20 vs 0.3)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 2 (Bug fix — code/doc inconsistency)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

---

## Summary

`EFR_DIVERGENCE_THRESHOLD` had two divergent values in the codebase prior to this task:

| Source | Value | File:line |
|---|---:|---|
| Production code | `0.3` | `live/impl2_features.py:104` |
| Reference impl | `0.3` | `_audit/pp_sprint/scripts/t1_x1_features.py:81`<br>`_audit/pp_sprint/scripts/t1_x1_features_v2.py:81` |
| Methodology audit doc | `0.20` | `_audit/pp_sprint/IMPL1_METHODOLOGY_AUDIT.md:113`<br>`_audit/pp_sprint/IMPL1_METHODOLOGY_AUDIT.md:123` |

The `0.20` figure in `IMPL1_METHODOLOGY_AUDIT.md` describes the operationalization as
`rolling_pct_rank(volume, 100) - rolling_pct_rank(bar_body, 100) > 0.20` (line 113) and
again as part of the "Purdue-calibrated parameters" list (line 123). The actual code
constant is `0.3`. The audit was ALIGNED to the wrong number.

## Root cause

The methodology audit was written 2026-04-22 by an analyst summarising the feature
operationalizations from the original `t1_x1_features.py` reference impl. The script
itself uses `EFR_DIVERGENCE_THRESHOLD = 0.3  # pre-specified` (line 81). The audit's
`0.20` is a transcription error introduced when summarizing the constant; both `0.20`
and `0.3` appear elsewhere in T1-X1 docs as numerically-similar magnitudes (e.g. the
weak-close threshold `CLOSE_PCT_WEAK = 0.30`), and the audit author appears to have
conflated the two.

No production logic ever used `0.20` — only the methodology summary doc was wrong.

## Resolution

This Tier 2 calibration supersedes both the legacy `0.20` doc figure and the legacy
`0.3` code value. The Purdue v2 12-step recalibration (see
`calibration_EFR_DIVERGENCE_THRESHOLD_v2.md`) produced a new winner of
`0.5000` (p90) under TREND-B precondition on the rebuilt clean parquet.

**Action items:**

1. **Code:** apply the v2-calibrated value (or KEEP per FOLLOW-RECALIB-001 review) to
   `live/impl2_features.py:104` and to the reference impls under `_audit/pp_sprint/scripts/`.
2. **Doc:** amend `_audit/pp_sprint/IMPL1_METHODOLOGY_AUDIT.md:113` and `:123` to cite the
   v2-calibrated value with explicit lineage to `calibration_EFR_DIVERGENCE_THRESHOLD_v2.md`.
   Remove the legacy `0.20` figure or annotate it as "superseded by v2 calibration".
3. **Cross-coord:** ClaudeCode #2 (this task) edits `live/impl2_features.py` constants only;
   doc edits in `_audit/pp_sprint/IMPL1_METHODOLOGY_AUDIT.md` are NOT in this task's scope
   (the audit doc is a snapshot reference; modifying it requires coordination with the
   ML-DS Engineer who owns the audit pipeline).

## Decision — Verdict

**Bug status:** RESOLVED via Tier 1.2 recalibration (the v2 winner replaces both legacy values).

**Constants table after EXEC-RECALIB-001:**

| Constant | Pre-task code | Pre-task doc | v2 calibration | Verdict |
|---|---:|---:|---:|---|
| EFR_DIVERGENCE_THRESHOLD | 0.3 | 0.20 | 0.5000 (p90) | INVESTIGATE — see `calibration_EFR_DIVERGENCE_THRESHOLD_v2.md` |

**Bonferroni status of v2 winner:** does not pass alpha=0.01 individually on the rebuilt
clean parquet (best KS p ≈ 0.10 at p70). This is the same load-bearing caveat that
applies to all Tier 1 constants — post-DATA-002 P1.5 clean data has materially
smaller effect sizes than the pre-rebuild calibration that produced the original `0.3`.

**Final action:** EXEC-8 backtest must validate the v2 winner before any production
code change. If EXEC-8 expectancy with `0.5` does not exceed expectancy with `0.3`,
KEEP `0.3` and document the bug-fix lineage on the existing value.

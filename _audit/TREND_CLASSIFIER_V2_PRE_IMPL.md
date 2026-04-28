# P1.2 Phase 0 — TREND_CLASSIFIER_v2 Provenance Verification (READ-ONLY)

**Asana:** 1214332092113381 (P1.2 NEW)
**Date:** 2026-04-28
**Author:** Claude Code (Opus 4.7)
**Scope:** Phase 0 only — read-only inventory, no code changes.

---

## TL;DR — SHOWSTOPPER for the Asana task as currently framed

**The Asana premise that "implementation never aconteceu / v1 _get_daily_trend ainda activo / 45.68% disagree" is OUTDATED.**

The classifier was REPLACED on **2026-04-27 08:20 UTC** under a different
Asana umbrella (BIAS-DETECTION-PURDUE-CALIBRATION 1214284792412296,
commit `401e6da`). The old monotonic-FMV v1 is dead code; the live
`_get_daily_trend` is now a B+C ensemble.

However, **a NEW empirical issue is firing right now**: the ensemble is
returning `"unknown"` 100 % of the time over the last 399 decision_log
rows because Signal B and Signal C are persistently disagreeing
(currently B=+1 Wyckoff long, C=−1 ICT short). This produces the SAME
operational symptom Barbara observed (V1 zone defaults to RANGE, GO LONG
04:08 −21.2pts), but for a DIFFERENT root cause than "v1 broken".

**Recommendation:** halt Phase 1 of the current Asana, re-spec around
the actual root cause (B+C disagreement → too-conservative unknown), and
either (a) implement the tiered-confidence path from the original
2026-04-23 spec (HIGH/MEDIUM/LOW/NONE), or (b) add a tie-break / decay
rule under disagreement.

---

## P0.1 — TREND_CLASSIFIER_v2 spec location

| Artifact | Path | Status |
|---|---|---|
| Original spec | `_audit/pp_sprint/regime_confidence_multifeature_work/TREND_CLASSIFIER_v2_spec.md` | ✅ Exists, dated 2026-04-23, "Approved by Barbara Feijen" line |
| Methodology alignment | `_audit/pp_sprint/TREND_CLASSIFIER_METHODOLOGY_ALIGNMENT.md` | ✅ Exists (predecessor) |
| Calibration artifact | `_audit/calibrations/calibration_daily_trend_v2.md` | ✅ Exists, dated 2026-04-26 |

The 2026-04-23 spec proposes:

- **Output:** `TrendV2` dataclass with `direction`, `confidence_tier`
  (HIGH/MEDIUM/LOW/NONE), `phase_ats`, `phase_wyckoff`, `rule_fired`,
  `signals`, `legacy_binary`
- **Rules R1-R8:** ATS Trend Line as PRIMARY (R1), JAC as CONFIRMATION
  (R2), HH/HL (R3), Weekly filter (R4), agreement-tier (R5), dual phase
  taxonomy (R6), ATR regime block (R7), legacy mapping (R8)
- **Implementation:** new module `live/trend_classifier_v2.py` (preferred)
  OR extend `level_detector.py`
- **Shadow-first deploy:** 7-14 day shadow window before promotion

## P0.2 — `_get_daily_trend` callsites + ensemble implementation

### Active callsites (live/, run_live.py)

| File:line | Use |
|---|---|
| `live/level_detector.py:377` | `_get_daily_trend()` definition (the ensemble) |
| `live/event_processor.py:78` | imports `_get_daily_trend, get_daily_trend_diagnostics` |
| `live/event_processor.py:1036` | `daily_trend_diag` heartbeat field |
| `live/event_processor.py:1935` | `refreshed_daily_trend = _get_daily_trend()` (used in macro_context refresh) |
| `run_live.py:809` | `daily_trend = levels.get("daily_trend", "unknown")` (start-up) |
| `run_live.py:899` | `processor.daily_trend = new_levels.get("daily_trend", "unknown")` |
| `live/d1_h4_updater.py` | imports/uses (legacy, writer disabled per ADR-001 backlog) |
| `scripts/m30_bias_backtest.py` | offline backtest tooling |
| `scripts/compare_d1h4_vs_dailytrend.py` | offline comparison |

The 5 downstream consumers in `event_processor.py` listed in the
2026-04-23 spec §5 R8 still apply (PATCH2A, V1 zone COUNTER_HTF, FMV
progression, `_get_strategy_mode`, GAMMA check_delta_trigger).

### Active implementation — what is currently shipped

`live/level_detector.py:179-507` — full ensemble code. Header banner:

> *"BIAS-DETECTION-PURDUE-CALIBRATION (Asana 1214284792412296, 2026-04-27).
> Phase 1 implementation per Barbara GO 2026-04-27 03:50 UTC. Supersedes
> BIAS-DETECTION-COMPLETE-FIX (1214284676342353) strict-monotonic
> heuristic with empirically calibrated tri-methodological ensemble (B+C
> voting, A diagnostic-only)."*

Logic:

```python
sig_a = _compute_signal_a(d1_closed)   # ATS Trend Line — DIAGNOSTIC, not voting
sig_b = _compute_signal_b(d1_closed, lookback=3)    # Wyckoff HH/HL
sig_c = _compute_signal_c(d1_closed, structure_lookback=5)  # ICT BOS/CHoCH

if sig_b != 0 and sig_c != 0 and sig_b == sig_c:
    return "long" if sig_b == +1 else "short"
return "unknown"
```

Output is BINARY (`"long" | "short" | "unknown"`) — no tiered confidence.
This deviates from the 2026-04-23 spec's `TrendV2` dataclass output.

### Spec ↔ implementation deviation table

| 2026-04-23 spec | 2026-04-27 implementation | Reason for deviation |
|---|---|---|
| New module `live/trend_classifier_v2.py` | Replaced `_get_daily_trend()` in `level_detector.py` | Option B from spec §8 |
| `TrendV2` dataclass + tiered confidence | Binary `"long"/"short"/"unknown"` | Spec amendment 2026-04-27 00:15 UTC ("consumer contract preservation") |
| ATS Trend Line as R1 PRIMARY | Signal A is DIAGNOSTIC ONLY (zero voting weight) | Calibration empirically refuted A: Bonferroni p=0.073-0.564, walk-forward weight 0.0 |
| 8 rules R1-R8 + dual phase taxonomy | 3 signals (B+C voting, A diagnostic), no phases | Calibration result + minimum-viable scope |
| Weekly filter R4 + ATR regime block R7 | Not present | Out of scope for the ensemble deploy |
| Shadow-first 7-14 day window | Direct flip via Barbara GO 2026-04-27 03:50 UTC | Tradeoff for speed; no shadow run logged |

The deviation was **methodologically defensible** (Signal A really did fail
Bonferroni; the calibration shows it convincingly) but the result is that
the SHIPPED classifier is leaner / less expressive than the original spec.

## P0.3 — Calibration history

`_audit/calibrations/calibration_daily_trend_v2.md` (2026-04-26):

- 9.7-month window (2025-07-01 → 2026-04-26), 201 D1 sessions
- Bonferroni α' = 0.05 / 12 = 0.00417 across 3 signals × 4 horizons
- Selected (lookback) parameters: B = 3, C = 5
- Bonferroni-passing horizons:
  - signal_b_lb3: h=10d only (p=0.0008, d=+0.554 — largest effect)
  - signal_c_lb5: h=3d, 5d, 10d (effects d=+0.221 → +0.459)
  - signal_a: 0/4 horizons (refuted)
- Walk-forward CV @ h=5d: 64.8% ± 31.9% accuracy (large fold variance, n=201 marginal)
- Critical findings flagged: Signal A failure (vs Citation 1), bull-skewed
  window (66-75% positive forward returns), high test variance

`_audit/calibrations/calibration_daily_trend.md` (predecessor) is also
present (BIAS-DETECTION-COMPLETE-FIX). Both are immutable.

## P0.4 — Existing test inventory

| File | Test class / scope |
|---|---|
| `tests/test_get_daily_trend_ensemble.py` | Phase 3 smoke tests for the ensemble (B+C vote logic, edge cases) — replaces older `test_get_daily_trend_rebuild.py` |
| `tests/test_ats_trend_line.py` | ATS Trend Line module tests (Signal A internals) |
| `_audit/pp_sprint/scripts/t1_x1_pullback_revalidation.py` | T1-X1 evidence — read-only, RUN ONCE 2026-04-23 |

Smoke tests exist; they exercise the BINARY ensemble (long/short/unknown).
A re-spec with tiered output would break these tests and require new ones
matching the new contract.

## P0.5 — Premise check (G-PROVENANCE-VERIFICATION)

### "v1 broken: 45.68% disagree" — STALE

The T1-X1 evidence (2026-04-23) refers to the **dead** strict-monotonic v1
that was replaced by commit `62f4346` (BIAS-DETECTION-COMPLETE-FIX) and
then by `401e6da` (BIAS-DETECTION-PURDUE-CALIBRATION). Repeating the T1-X1
counterfactual against the current ensemble is out of scope for Phase 0
but is a recommended Phase 1 input.

### Current empirical state — `daily_trend` distribution post-deploy

Counted from `decision_log.jsonl` rows since `2026-04-27T08:20Z`
(post-`401e6da` deploy):

```text
total rows              : 399
daily_trend distribution: {'unknown': 399}
  unknown : 100.0 %
  long    :   0.0 %
  short   :   0.0 %
```

**Direct call right now**:

```text
_get_daily_trend()      = 'unknown'
n_closed_sessions       = 1624
freshness_seconds       = 45,971.4   (12.8 h stale — normal D1 cadence)
signal_a (diagnostic)   = -1         (ATS Trend Line says short)
signal_b (Wyckoff)      = +1         (HH/HL says long)
signal_c (ICT)          = -1         (BOS/CHoCH says short)
agreement_count         = 0
decision_reason         = "b=1, c=-1 (disagree)"
```

**The ensemble IS firing — it is producing `unknown` because Signals B and
C disagree.** This is operationally identical to "classifier broken" from
a downstream consumer perspective: V1 zone defaults to RANGE mode → entries
in trending markets fade boundaries → losses like GO LONG 04:08 (-21.2pt).

### Intermediate solutions / regime detectors active

- TREND-A and TREND-B detectors (commit `d721c9d`) — IMPL-1 active in
  event_processor.py via `_regime_state`. These are M30 layer (active),
  not D1-bias replacements.
- IMPL-3 LOGIC-C (`_logic_c_score` / `_feat_4_anti_exit_active`) — M30
  exhaustion detection, also active. Not a daily_trend substitute.
- `derive_h4_bias` (Sprint C v2) — H4 layer scaffolded but `d1_h4_updater`
  writer is **disabled** since 2026-04-20 (SPRINT H4-WRITER-FIX backlog).
  Cannot be relied on as a daily_trend fallback.

There is no other active classifier producing the daily_trend signal —
`_get_daily_trend` is the sole authority for downstream consumers.

---

## PRAC

- **Premise overturned (G-PREMISE-AUDIT)** — the Asana task assumes v1
  is still live; it is not. Without correcting the premise, Phase 1 would
  re-implement something that already exists (and break tests).
- **Confirmation bias** — I did NOT conclude "ensemble is bad". The
  ensemble's logic is sound; it is the disagreement-handling policy that
  appears too binary. Phase 1 backtest must measure: (a) base rate of
  B+C agreement vs disagreement on the 9.7-month window, (b) what the
  forward outcome looks like during disagreement (does picking either B
  or C help, or do you need to wait?), (c) whether tiered output reduces
  the operational impact.
- **Sample-size** — 399 rows = ~1 day of decisions (limited by P0
  observability deploy 2026-04-28 morning). Distribution may be unusually
  one-sided due to the recent regime change (sharp Asia-session decline
  from 4710 → 4628). Phase 1 should use the full 9.7m parquet window for
  the backtest baseline, not the 1-day live sample.
- **Scope discipline** — I did not touch v1 (no v1 to touch — it's gone).
  I did not touch the ensemble code. Read-only inventory.
- **Spec-vs-implementation gap** — even if Barbara wants to validate the
  current implementation (path 3), the gap (binary vs tiered) means the
  acceptance criteria from the 2026-04-23 spec do not directly apply.

---

## Recommendation for Phase 1 (Barbara's decision)

Three viable paths:

### Path A — VALIDATE current ensemble + decide on disagreement policy

- Run the 9.7m backtest from `_audit/pp_sprint/scripts/t1_x1_pullback_revalidation.py`
  (or equivalent) against the CURRENT ensemble code (not the dead v1)
- Quantify: % B+C agreement, % disagreement, forward outcome accuracy in each
- If agreement rate is high enough and forward accuracy on agreement >55 %,
  the only fix needed is the disagreement-handling policy (e.g., default
  to one signal under disagreement, or fall through to a less-strict tier)
- Time: ~1-2 h

### Path B — RE-IMPLEMENT to match the 2026-04-23 spec (tiered output)

- Build `live/trend_classifier_v2.py` with `TrendV2` dataclass output per
  spec §4
- Adapt the 5 consumers in `event_processor.py` to use the tiered output
  (HIGH/MEDIUM/LOW → "long"/"short"; NONE → "unknown")
- Migrate tests to match new contract
- Time: ~3-4 h
- Risk: 5-consumer migration is the largest source of regressions

### Path C — CLOSE the Asana as superseded

- The classifier was replaced. Acknowledge the spec deviation explicitly
  in DECISIONS_LOG.md.
- Re-file the disagreement-handling-policy issue as a NEW Asana
  ("DAILY_TREND_DISAGREEMENT_POLICY") with the operational evidence.
- Time: ~30 min

**My recommendation:** **Path A first** (1-2 h backtest), then
**Path B-or-C decision** based on what the backtest shows. If
agreement-rate is healthy and just the disagreement policy is broken,
Path A's findings drive a small, surgical fix. If the ensemble itself
underperforms, Path B becomes justified.

---

## Deliverables

- ✅ `_audit/TREND_CLASSIFIER_V2_PRE_IMPL.md` — this doc
- 🛑 **GATE — awaiting Barbara's decision on Phase 1 path (A / B / C)**

Phase 0 read-only inventory complete. Zero edits to `live/`. No commits
required at this stage (artifact-only commit when Barbara approves
direction).

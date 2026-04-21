# Phase 1 §5.2.c — Emission Site Categorization

**Source:** `phase1_emission_inventory.txt` (130 matched lines)
**Grades:** CANONICAL_OK / SILENT_RETURN / PARTIAL / UNCLEAR
**Generated:** 2026-04-21, post Pre-Flight ratification
**Sprint:** `sprint_apex_stabilization_7fixes_20260421`

---

## 1. Definitions

| grade | meaning |
|---|---|
| CANONICAL_OK | decision is followed (within ≤10 lines) by a call to `_write_decision()`, `_write_stale_block_decision()`, or `_publish_canonical_pm_event()` → writes to `decision_live.json` + `decision_log.jsonl`; Telegram picks up on subsequent read. |
| SILENT_RETURN | decision has `print(...)` / `log.info(...)` / `log.warning(...)` followed by bare `return` / `continue` with NO canonical write. Entirely invisible in `decision_log.jsonl`, `decision_live.json`, Telegram. |
| PARTIAL | writes to SOME canonical destination but not full set (e.g. writes `decision_log.jsonl` but not `decision_live.json`; or writes but skips Telegram). |
| UNCLEAR | requires manual inspection — control-flow or payload not determinable from grep. |

---

## 2. Canonical sinks inventory (confirmed)

From grep of `_write_decision(` + `_publish_canonical_pm_event(` + `_write_stale_block_decision(`:

| function | file:line | role |
|---|---|---|
| `_write_decision` | event_processor.py:676 | definition (entry-path canonical writer) |
| `_write_stale_block_decision` | event_processor.py:981 | definition (wraps `_write_decision` at 1044) |
| `_publish_canonical_pm_event` | position_monitor.py:2004 | definition (PM canonical writer) |
| **call sites of `_write_decision`** | event_processor.py:1044, 2620, 2777, 2848 | 4 |
| **call sites of `_write_stale_block_decision`** | event_processor.py:2267 | 1 |
| **call sites of `_publish_canonical_pm_event`** | position_monitor.py:2002 | 1 (single dispatcher for all PM events) |

Inline canonical-write duplicates (not going through the 3 functions above):

| location | role | canonical target hit |
|---|---|---|
| event_processor.py:1289-1339 | NEWS_EXIT PM inline emission | decision_live + decision_log |
| hedge_manager.py:432-497 | Hedge canonical PM emit | decision_live + decision_log |

---

## 3. Entry-path emission sites (event_processor.py) — graded

| file:line | site / reason | grade | complexity to unify | notes |
|---|---|---|---|---|
| event_processor.py:1044 | `_write_stale_block_decision` terminal call → `_write_decision(payload)` | **CANONICAL_OK** | n/a | baseline template |
| event_processor.py:2267 | caller path hits `_write_stale_block_decision(...)` when `m1_stale_critical=true` | **CANONICAL_OK** | n/a | |
| event_processor.py:2290 | `NEWS_VETO {direction}: {event} -> Gold {severity}` | **SILENT_RETURN** | **moderate** | variables in scope: direction, event name, severity; context via `self._metrics`; should map to BLOCK + reason "NEWS_VETO: …" |
| **event_processor.py:2393** | **M30_BIAS_BLOCK bias=bullish -> SHORT rejected** | **SILENT_RETURN** ★ | **trivial** | Phase 2 target. Mirror `_write_stale_block_decision` structure one-for-one; reason="M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)"; context already accessible on self |
| **event_processor.py:2399** | **M30_BIAS_BLOCK bias=bearish -> LONG rejected** | **SILENT_RETURN** ★ | **trivial** | Phase 2 target (mirror path). |
| event_processor.py:2438 | `operational rules blocked: {op_reason}` after `self.ops.check_can_enter` | **SILENT_RETURN** | moderate | op_reason string available; needs decision wrapper |
| event_processor.py:2445 | pre-entry gate blocked: `{block_reason}` from `_check_pre_entry_gates` | **SILENT_RETURN** | moderate | data-driven threshold failure; currently invisible |
| event_processor.py:2526 | `v1 = "ZONE_FAIL"` assignment feeds downstream `_build_decision_dict` → `_write_decision` at 2620 | **CANONICAL_OK** | n/a | zone-fail IS emitted as BLOCK; responsible for `BLOCK_V1_ZONE` rows seen in decision_log |
| event_processor.py:2620 | main gate chain terminus — `self._write_decision(_decision_dict)` (GO or BLOCK) | **CANONICAL_OK** | n/a | Fase 2 canonical path |
| event_processor.py:2623-2624 | `print("BLOCK: {reason}")` + `tg.notify_decision()` — cosmetic; 2620 already wrote canonical | **CANONICAL_OK** | n/a | second statement is the Telegram poke |
| event_processor.py:2735 | ABORT_STALE entry path: `price moved to {current_gc}, level_stale` | **SILENT_RETURN** | moderate | entry-time structure-stale; distinct from the `_write_stale_block_decision` m1_stale flow |
| event_processor.py:2777 | EXECUTED: `_decision_dict["decision"]["action"] = "EXECUTED"`; `self._write_decision(...)` | **CANONICAL_OK** | n/a | after broker success |
| event_processor.py:2848 | EXEC_FAILED: `_decision_dict["decision"]["action"] = "EXEC_FAILED"`; `self._write_decision(...)` | **CANONICAL_OK** | n/a | all brokers refused |
| event_processor.py:3844 | GAMMA ABORT_STALE (SHORT side): price already below sl_gc | **SILENT_RETURN** | moderate | position-check path, not entry; may belong in PM canonical |
| event_processor.py:3847 | GAMMA ABORT_STALE (LONG side) | **SILENT_RETURN** | moderate | same |
| event_processor.py:4142 | DELTA ABORT_STALE (SHORT side) | **SILENT_RETURN** | moderate | same |
| event_processor.py:4145 | DELTA ABORT_STALE (LONG side) | **SILENT_RETURN** | moderate | same |
| **event_processor.py:4399** | **FEED_DEAD — gate suspended** (`print + continue`) | **SILENT_RETURN** ★ | **moderate** | Phase 7 target (Fix #5). Decision direction unknown at this point (before direction resolution); reason=FEED_DEAD; may need a new BLOCK variant without direction |

---

## 4. Position-monitor emission sites (position_monitor.py) — graded

| file:line | site | grade | complexity | notes |
|---|---|---|---|---|
| position_monitor.py:2002 | `self._publish_canonical_pm_event(payload)` — single dispatcher | **CANONICAL_OK** | n/a | routes ALL PM events (SHIELD, TP1_HIT, TP2_HIT, SL_HIT, REGIME_FLIP, PULLBACK_START, PULLBACK_END_EXIT, L2_DANGER, T3_EXIT, NEWS_EXIT) |
| position_monitor.py:2004-2066 | `_publish_canonical_pm_event` body — writes decision_live + decision_log + `tg.notify_decision()` | **CANONICAL_OK** | n/a | canonical. Telegram failure → `log.debug` at line 2066 (Fix #6 target — promote to warning) |

---

## 5. Hedge + NEWS_EXIT duplicates — graded

| file:line | site | grade | complexity | notes |
|---|---|---|---|---|
| event_processor.py:1289-1339 | NEWS_EXIT inline canonical emit | **PARTIAL** | moderate | writes decision_live + decision_log directly without delegating; should call `_publish_canonical_pm_event` |
| hedge_manager.py:432-497 | Hedge PM_EVENT inline emit (Fase 5 Scope B.4) | **PARTIAL** | moderate | parallel implementation; should delegate to `_publish_canonical_pm_event` |

---

## 6. Summary counts

| grade | count |
|---|---|
| CANONICAL_OK | 10 |
| SILENT_RETURN | 11 |
| PARTIAL | 2 |
| UNCLEAR | 0 |

**SILENT_RETURN sites in current live engine: 11** — every one is invisible in the Single Source of Truth. Of these, 3 (marked ★) are explicit sprint targets (Fix #1 ×2 at 2393/2399, Fix #5 ×1 at 4399). The remaining 8 are **out-of-scope per sprint §0 mission ("Do NOT invent new patches")** but flagged for ML Engineer awareness.

### Recommendation — Phase 1 design funnel scope

Two options (as also flagged in `phase1_discovery.txt`):

**Option (b) — MINIMUM DIFF** *(aligns with §0 surgical mandate)*
- Phase 1 keeps `_write_decision` and `_publish_canonical_pm_event` as the two sanctioned canonical entry points.
- Phase 2 (Fix #1 M30_BIAS_BLOCK) and Phase 7 (Fix #5 FEED_DEAD) patch their 3 sites to call one of those two (most likely `_write_stale_block_decision` as the template since input surface matches).
- PARTIAL #3 (NEWS_EXIT inline, event_processor.py:1289) and #4 (hedge_manager.py:432) left as-is for future refactor sprint.
- **Blast radius: 3 call sites patched.**

**Option (a) — UNIFICATION** *(preferred by ratification §0 if "future-proof" is the goal)*
- Create module-level `_build_v1_canonical_payload(reason, direction, price, ..., variant={ENTRY_BLOCK, PM_EVENT, FEED_DEAD, ...}) -> dict` and `_publish_decision_canonical(payload)` helpers (either in `event_processor.py` or a new `live/canonical.py`).
- Refactor all 4 canonical writers (`_write_decision`, `_publish_canonical_pm_event`, inline NEWS_EXIT, hedge_manager inline) to call the shared helpers.
- Phase 2 + Phase 7 sites use the same helpers.
- Benefits: future SILENT_RETURN retrofits (8 extras above) become drop-in.
- **Blast radius: 4 existing writers + 3 new patch sites = 7 files of edit surface.**

**ClaudeCode default recommendation:** **Option (b)**. Consistent with §0 ("minimum diff, surgical") and the ratification's §1 ("Phase 1 NÃO cria `_publish_decision_canonical` novo"). Extras are documented here for a future refactor sprint. Awaiting ML Engineer ratification on which option to pursue.

---

## 7. Patch complexity per sprint-target site

| target | file:line | template | estimated diff | risk |
|---|---|---|---|---|
| Fix #1a | event_processor.py:2393 (SHORT) | mirror `_write_stale_block_decision` structure at 981 | ~35 lines new helper + ~10 lines edit at gate | LOW — existing `_write_stale_block_decision` carries analogous payload; reason string only changes; gates sub-dict can be omitted (telegram `.get` is resilient) |
| Fix #1b | event_processor.py:2399 (LONG) | same as 1a (second leg) | reuse same helper | LOW |
| Fix #5 | event_processor.py:4399 (FEED_DEAD) | new helper variant without `direction` (unresolved at that scope) | ~25 lines new helper + 3 lines edit | MEDIUM — FEED_DEAD fires inside outer loop before direction resolution; payload needs a direction placeholder ("UNKNOWN") and/or a new action type `FEED_DEAD` parallel to "BLOCK" |

No changes proposed in this document; purely categorization.

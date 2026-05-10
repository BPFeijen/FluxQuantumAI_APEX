# AUDIT — 50 commits between 401e6da and 2ab3f55 (fix/xau-mid-population)

**Author**: CC#3
**Date**: 2026-05-05
**Asana**: 1214556369070092 (BUG-SIGNAL-INVERTED) → ML-DS comment 1214557529054273 PIVOT directive
**Scope**: READ-ONLY classification of every commit added on top of `401e6da` (B+C ENSEMBLE) up to `2ab3f55` (HEAD pre-deploy)

---

## CONTEXT

`fix/xau-mid-population` accumulated 53 commits between the validated fix (`401e6da`, 2026-04-27) and `2ab3f55` (HEAD, 2026-05-05) without any incremental deploy. The current production deploy (branch `deploy/bug-signal-inverted-2026-05-06`, services running PID 24536/31428) contains ONLY `889f5ee + 62f4346 + 401e6da` — these 50+ commits are NOT in production and are candidates for incremental deploy review.

This audit classifies each commit per:

- **SAFE_DEPLOY**: low blast radius, completed Asana task, tests pass, isolated change
- **NEEDS_REVIEW**: touches core modules (event_processor.py / level_detector.py / position_monitor.py) with significant LoC; needs ML-DS sign-off + integration test
- **BLOCKED**: known regression, missing dependency, or contradicts current production state
- **REDUNDANT**: superseded by later commit / no longer applicable
- **REQUIRES_BARBARA**: feature removal or behavior change requiring explicit user confirmation (e.g. broker delete, Telegram changes)

Risk-pattern flags:
- 🔴 **CORE**: touches `live/level_detector.py`, `live/event_processor.py`, or decisor logic
- 🟡 **MT5**: removes/modifies MT5 broker code (US-1.3 already removed credentials)
- 🟢 **DOC**: documentation/audit-only, no live code
- 🔵 **NEW**: net-new module, no existing code modified
- ⚪ **REVERT**: revert pair — needs to be processed together

---

## BATCH 1 — Commits 1–10 (chronological)

| # | SHA | Title | Files Δ | Asana | Class | Flags | Notes |
|---|-----|-------|---------|-------|-------|-------|-------|
| 1 | `21bfac3` | MACRO-MONITOR-VAP: Virtual Active Position monitoring | 10f / +2811/-1 | (no GID — design doc only) | **SAFE_DEPLOY** | 🔵 | New module `live/macro_monitor.py` (+618). Touches `event_processor.py +6`, `run_live.py +12` (minimal hooks). 478 test lines. Broker-independent operator alerts. Telegram-emit dependent — if Telegram stays OFF, only dashboard surfacing applies. Sanity binding honored. |
| 2 | `06bdd7e` | DECISION-PATH-REFACTOR-GC-SPACE Phase 2: GC-canonical decisor + dual-emit + health-gate | 6f / +1311/-98 | [1214291737498861](https://app.asana.com/0/0/1214291737498861) — Completed High | **NEEDS_REVIEW** | 🔴 CORE | 14-step refactor of decisor. `event_processor.py +444/-98` (huge), `level_detector.py +27`, 272 lines new tests (`test_decisor_mt5_disconnect.py`). Migrates decisor from MT5 frame to GC frame. Critical path. **MUST be deployed alongside Phase 2.5 (3096030) + Phase 2.6 (abdb3f3) + Phase 2.7 (31ea516) — they form one coherent refactor**. |
| 3 | `abdb3f3` | Phase 2.6: Trailing stop cross-frame writeback fix ★ CRITICAL | 4f / +383/-29 | [1214295995278704](https://app.asana.com/0/0/1214295995278704) — Completed High | **SAFE_DEPLOY** | 🟡 | `position_monitor.py +97/-29`, 214 test lines. Cross-frame routing: trail_sl in GC, write MT5 to broker. Ratchet semantics preserved. Provenance verified. Tests pass. Marked "★ CRITICAL standalone urgente" — high-priority bugfix. Depends on Phase 2 (06bdd7e) being deployed for full benefit. |
| 4 | `3096030` | Phase 2.5: Entry SL/TP construction migration to GC frame | 3f / +443/-64 | [1214295995324194](https://app.asana.com/0/0/1214295995324194) — Completed High | **NEEDS_REVIEW** | 🔴 CORE | `event_processor.py +185/-64`, 274 test lines. Migrates entry_sl/tp construction from MT5 frame to GC frame. Same refactor family as 06bdd7e. Standalone deploy possible but coupled. |
| 5 | `31ea516` | Phase 2.7: entry_gc state field migration | 11f / +1047/-41 | [1214295842868671](https://app.asana.com/0/0/1214295842868671) — Completed High | **NEEDS_REVIEW** | 🔴 CORE 🟡 MT5 | `event_processor.py +10`, `position_monitor.py +160`, `mt5_executor.py +41`, `mt5_executor_hantec.py +28`, 235 test lines. CAL-22 OFFSET DRIFT REPORT bundled. Last commit that still touches MT5 executors before deletion (D-stage-1). Final phase of GC refactor sequence. |
| 6 | `063e787` | Bloco A — Extract constants + lot_utils to broker-agnostic modules | 4f / +116/-1 | [1214309245971704](https://app.asana.com/0/0/1214309245971704) — Completed High (epic) | **SAFE_DEPLOY** | 🔵 | Adds `live/_constants.py +32`, `live/_lot_utils.py +31`, `position_monitor.py +7`. Pure refactor extract — no behavior change. First step of SISTEMA-SIGNAL-ONLY-INTERIM (Blocos A–P). |
| 7 | `4b20188` | Bloco B — SignalEmitter scaffold (advisory mode no broker) | 2f / +658 | 1214309245971704 (same epic) | **SAFE_DEPLOY** | 🔵 | Pure new modules: `live/_signal_emitter.py +427`, `tests/test_signal_emitter.py +231`. Advisory-mode emitter (no broker). Zero impact on existing behavior. |
| 8 | `f2ce2e0` | Bloco A.2 + C — Wire SignalEmitter | 2f / +42/-42 | 1214309245971704 (same epic) | **NEEDS_REVIEW** | 🔴 CORE | `event_processor.py +20/-?`, `run_live.py +64/-?`. Wires SignalEmitter into pipeline. Modifies bootstrap. Should be deployed alongside Bloco B (4b20188). |
| 9 | `3006324` | D-stage-1 — Remove mt5_executor_hantec.py | 1f / -573 | 1214309245971704 (same epic) | **REQUIRES_BARBARA** | 🟡 MT5 | Pure deletion of `mt5_executor_hantec.py (-573 lines)`. Aligned with US-1.3 (.env note "MT5 RoboForex+Hantec REMOVED 2026-05-03"). Production deploy already runs without MT5 (graceful no-op). **Status**: very likely safe, but Barbara should explicitly confirm "Hantec definitively gone" before merging. |
| 10 | `c986ff3` | Map updates post-Sessão 1 | 1f / +579 | 1214309245971704 (same epic) | **SAFE_DEPLOY** | 🟢 DOC | Pure doc: `_audit/MT5_DEPENDENCY_MAP.md +579`. Zero live code. |

### Batch 1 Summary

| Class | Count |
|-------|-------|
| SAFE_DEPLOY | 5 |
| NEEDS_REVIEW | 4 |
| REQUIRES_BARBARA | 1 |
| BLOCKED | 0 |
| REDUNDANT | 0 |

**Coupling observation**: Commits 2, 4, 5 (06bdd7e, 3096030, 31ea516) form a coherent refactor (DECISION-PATH-REFACTOR-GC-SPACE Phases 2 / 2.5 / 2.6 / 2.7). Recommend deploying as a single unit. Commit 3 (abdb3f3 Phase 2.6) should be included in this same unit.

**Risk concentration**: Batch 1 includes the largest non-cherry-picked refactor in the 50-commit set (`06bdd7e` 444 lines event_processor delta). This is the highest-risk single commit in the audit and warrants integration testing.

---

## BATCH 2 — Commits 11-20 (SISTEMA-SIGNAL-ONLY-INTERIM continuation)

All commits in this batch reference the same epic [1214309245971704](https://app.asana.com/0/0/1214309245971704) (SISTEMA-SIGNAL-ONLY-INTERIM Blocos A–P) — Completed, High priority.

| # | SHA | Title | Files Δ | Class | Flags | Notes |
|---|-----|-------|---------|-------|-------|-------|
| 11 | `609d19a` | Bloco H + M.1 — Offset infra deletion + decision_log MT5 alias removal | 2f / +69/-88 | **NEEDS_REVIEW** | 🔴 CORE 🟡 MT5 | `event_processor.py +69/-88` (net -19 lines, mostly deletion). Removes MT5 offset infra + decision_log MT5 alias. Coupled to broader MT5 removal sequence. |
| 12 | `a741a1a` | Bloco M.2 + M.3 — Trailing minimal-strip + Phase 2.7 offset_at_open removal | 4f / +21/-24 | **NEEDS_REVIEW** | 🔴 CORE | `_signal_emitter.py +10/-3`, `position_monitor.py +33/-21`. Cleans up trailing logic post Phase 2.7. Small but in critical path. |
| 13 | `8bf2b70` | Bloco G.1+G.2 — Dashboard MT5 stub | 2f / +37/-65 | **SAFE_DEPLOY** | 🟡 MT5 | `dashboard_server.py +32/-?`, `dashboard_server_hantec.py +70/-65` (net -28). Dashboard-only changes; no decisor impact. Note: `dashboard_server_hantec.py` may be obsolete given MT5 removal. |
| 14 | `2a629be` | Bloco J — VPS design doc + sanity audit | 2f / +384 | **SAFE_DEPLOY** | 🟢 DOC | Pure documentation: `_audit/VPS_DESIGN.md +346`, sanity log. |
| 15 | `2721dc2` | Bloco J — Virtual Position State module + 15 tests | 2f / +712 | **SAFE_DEPLOY** | 🔵 NEW | Pure new module: `live/_virtual_position_state.py +379`, `tests/test_virtual_position_state.py +333`. Zero impact on existing code. |
| 16 | `3e69290` | Bloco J — Wire VPS into event_processor + position_monitor | 2f / +84/-25 | **NEEDS_REVIEW** | 🔴 CORE | `event_processor.py +59` (purely additive), `position_monitor.py +50/-25`. Integrates VPS module from #15. Coupled to #14 + #15. |
| 17 | `05fd921` | D-stage-2 — Remove live/mt5_history_watcher.py | 1f / -166 | **REQUIRES_BARBARA** | 🟡 MT5 | Pure deletion. Same justification as #9 (3006324) — MT5 removal aligned with US-1.3. Should be batch-confirmed with Barbara. |
| 18 | `ea78165` | Bloco I — Schema cleanup downstream consumers | 2f / +62/-42 | **NEEDS_REVIEW** | 🔴 CORE | `event_processor.py +62/-42`. Schema cleanup for downstream consumers. Coupled to other Sinal-Only blocks. |
| 19 | `776b061` | Bloco K + L — VPS hooks integration via duck-typing + executor audit | 3f / +85/-79 | **NEEDS_REVIEW** | 🔴 CORE | `_virtual_position_state.py +59`, `position_monitor.py +85/-79` (heavy refactor of position_monitor). Duck-typing integration. |
| 20 | `414eaf5` | Bloco E — Strip MT5 imports from event_processor | 1f / +23/-79 | **NEEDS_REVIEW** | 🔴 CORE 🟡 MT5 | `event_processor.py +23/-79` (heavy deletion). Strips MT5 imports. Critical path change. Coupled to D-stage commits. |

### Batch 2 Summary

| Class | Count |
|-------|-------|
| SAFE_DEPLOY | 3 |
| NEEDS_REVIEW | 6 |
| REQUIRES_BARBARA | 1 |
| BLOCKED | 0 |
| REDUNDANT | 0 |

**Coupling observation**: Commits 14+15+16 (Bloco J) are a coherent VPS introduction sequence — design doc → module → wiring. Should deploy as unit.

Commits 11+12+13+17+18+19+20 form the second wave of SISTEMA-SIGNAL-ONLY-INTERIM (after Blocos A–C in batch 1). These represent a coordinated MT5 strip-out + signal-only mode introduction. **Cumulative event_processor.py impact across batch 2**: ~+213/-209 lines.



## BATCH 3 — Commits 21-30 (Sinal-Only finalization + first revert pair)

| # | SHA | Title | Files Δ | Asana | Class | Flags | Notes |
|---|-----|-------|---------|-------|-------|-------|-------|
| 21 | `9afd5a1` | Bloco F — Strip MT5 imports from remaining modules | 6f / +76/-605 | 1214309245971704 (epic) | **NEEDS_REVIEW** | 🔴 CORE 🟡 MT5 | Heavy MT5 strip-out: `base_dashboard_server.py +95/-?`, `hedge_manager.py +5`, `position_monitor.py +51/-?`, `run_live.py +6`, **`scripts/integration_health_check.py -412 (deleted)`**, tests adjusted. Net -529 lines. |
| 22 | `d46283b` | D-stage-3 — Remove mt5_executor.py | 1f / -722 | 1214309245971704 (epic) | **REQUIRES_BARBARA** | 🟡 MT5 | Pure deletion of `mt5_executor.py` (-722 lines). Same justification as #9 + #17 (US-1.3 alignment). Largest single MT5 deletion. |
| 23 | `7019b78` | D-stage-4 — Remove test_mt5_ipc*.py + sanity post | 3f / +70/-42 | 1214309245971704 (epic) | **SAFE_DEPLOY** | 🟢 DOC | Removes obsolete test files (`test_mt5_ipc.py -10`, `test_mt5_ipc2.py -32`) + adds sanity log. Tests-only. |
| 24 | `dd4eab9` | Bloco N — Tests refactor signal-only | 3f / +196/-11 | 1214309245971704 (epic) | **SAFE_DEPLOY** | 🔵 NEW | New e2e test `test_advisory_signal_e2e.py +183`, deprecated test renamed. Test infra only. |
| 25 | `740f188` | Bloco O — CI guard zero MT5 references | 3f / +322 | 1214309245971704 (epic) | **SAFE_DEPLOY** | 🔵 NEW | Pure new: `_audit/CI_ZERO_MT5_GUARD.md +138`, `scripts/ci_zero_mt5_guard.sh +78`, `tests/test_zero_mt5_imports.py +106`. CI/test infra to enforce zero MT5 imports. |
| 26 | `e6042c4` | Bloco P-docs — Schemas + architecture refresh | 6f / +494/-118 | 1214309245971704 (epic) | **SAFE_DEPLOY** | 🟢 DOC | Doc-heavy: schemas updated (`signals_log_schema.md +147` new, `decision_log_schema.md +199/-118` heavy rewrite), `ARCHITECTURE.md +49`. Zero live code. |
| 27 | `dcef3dc` | Bloco P-audit-finalize — Final state + forensic + sanity | 4f / +490 | 1214309245971704 (epic) | **SAFE_DEPLOY** | 🟢 DOC | Pure audit/forensic docs. Closes Sinal-Only umbrella program. |
| 28 | `2f0d87a` | Hotfix run_live: force UTF-8 stdio | 1f / +14 | (no GID) | **SAFE_DEPLOY** | 🔴 CORE | Tiny additive hotfix — `run_live.py +14`. UTF-8 stdio for unicode banner crash. Standalone. |
| 29 | `73f55f5` | M30 BIAS: demote hard-gate to SHADOW | 1f / +23/-16 | [1214320481337073](https://app.asana.com/0/0/1214320481337073) — **NOT completed, Off track** | **REDUNDANT** ⚪ | 🔴 CORE | First half of revert pair. `event_processor.py +23/-16`. **Reverted by #30** — net effect zero. |
| 30 | `567e5ca` | Revert "M30 BIAS: demote hard-gate to SHADOW" | 1f / +16/-23 | 1214320481337073 (same task) | **REDUNDANT** ⚪ | 🔴 CORE | Reverts #29. Together (#29 + #30) = no functional change. **EXCLUDE BOTH FROM DEPLOY** (waste of merge effort, churn only). |

### Batch 3 Summary

| Class | Count |
|-------|-------|
| SAFE_DEPLOY | 6 |
| NEEDS_REVIEW | 1 |
| REQUIRES_BARBARA | 1 |
| BLOCKED | 0 |
| REDUNDANT | 2 (revert pair #29+#30) |

**🚩 Risk patterns identified in batch 3**:

1. **REVERT PAIR detected**: `73f55f5` ↔ `567e5ca` (commits #29 + #30). Net zero functional change. The Asana task `1214320481337073` (M30-BIAS-DERIVATION-AUDIT) is **NOT completed and Off track** — the hard-gate-to-SHADOW change was attempted, immediately reverted, and never resolved. Excluding both from any deploy is correct; the underlying issue remains open.

2. **Largest MT5 deletion** (`d46283b` -722 lines) is in this batch. Combined with #9 (-573) and #17 (-166), the cumulative MT5 deletion across the 50 commits is approximately **-1461 lines**. All gated under SISTEMA-SIGNAL-ONLY-INTERIM epic (Completed High).

3. **Deleted production script**: `9afd5a1` removes `scripts/integration_health_check.py` (-412 lines). Should verify no NSSM service or cron depends on it.



## BATCH 4 — Commits 31-40 (post-Sinal-Only stability + observability + telegram fixes)

| # | SHA | Title | Files Δ | Asana | Class | Flags | Notes |
|---|-----|-------|---------|-------|-------|-------|-------|
| 31 | `0eb3131` | M30 BIAS UTAD_AWARE patch — Wyckoff follow-through flip | 1f / +45/-1 | (no GID — bundled with 1214320481337073 epic) | **NEEDS_REVIEW** | 🔴 CORE — **POST-401e6da** | `level_detector.py +45/-1`. **Touches the same file as the 401e6da B+C fix**. Wyckoff follow-through flip in `derive_m30_bias`. Could interact with the deployed B+C ENSEMBLE. **HIGH ATTENTION**: must validate that 401e6da's `_get_daily_trend` semantics aren't disturbed. |
| 32 | `32343cf` | m30_updater fix m30_liq_bot/m30_liq_top excursion tracking | 1f / +40/-3 | (no GID) | **SAFE_DEPLOY** | 🔴 CORE | `m30_updater.py +40/-3`. Isolated additive fix to box excursion tracking. Standalone. |
| 33 | `788c57d` | Telegram format bugs: GC schema migration + broker-agnostic + cooldown race | 5f / +137/-193 | [1214321341910300](https://app.asana.com/0/0/1214321341910300) — Completed High | **NEEDS_REVIEW** | 🔴 CORE | `event_processor.py +137/-193` (heavy refactor net -56 lines), `macro_monitor.py +28`, `position_monitor.py +7`, `telegram_notifier.example.py +59`, `run_live.py +70/-?`. **Telegram is OFF in current production deploy** — the visible payload effects don't apply right now, but the event_processor refactor is real and consequential regardless. |
| 34 | `2152be9` | Forensic doc TELEGRAM_FORMAT_BUGS_FORENSIC.md | 1f / +401 | 1214321341910300 (same task) | **SAFE_DEPLOY** | 🟢 DOC | Pure forensic doc. |
| 35 | `d4fd8aa` | SYSTEM_Architecture_Current 2026-04-28 telegram bugs entry | 1f / +27/-2 | 1214321341910300 (same task) | **SAFE_DEPLOY** | 🟢 DOC | Architecture doc updated with Telegram bug entry. |
| 36 | `3d90117` | M30 BIAS Phase 2/3 sanity audit docs | 2f / +206 | 1214320481337073 (Off track) | **SAFE_DEPLOY** | 🟢 DOC | Pure audit docs. Note: parent task `1214320481337073` is **Off track / not completed** — the docs document a state that wasn't fully resolved. |
| 37 | `1c856ab` | test_macro_monitor patch _notify_telegram in 4 VapLifecycle tests | 1f / +21/-17 | (no GID) | **SAFE_DEPLOY** | 🟢 DOC | Test-only adjustment. |
| 38 | `93297c9` | P0 OBSERVABILITY-LOG-BLOCKS Fase B: instrument event_processor | 2f / +556 | [1214327559721560](https://app.asana.com/0/0/1214327559721560) — Completed High | **NEEDS_REVIEW** | 🔴 CORE | `event_processor.py +294` (purely additive — observability instrumentation). `_audit/EARLY_RETURNS_INVENTORY.md +262`. Bug-prevention via instrumentation; safer than feature commit but still big addition to event_processor. |
| 39 | `5cc4d83` | P0 OBSERVABILITY-LOG-BLOCKS Fase B: tests + audit docs + perf bench | 7f / +1253 | 1214327559721560 (same task) | **SAFE_DEPLOY** | 🔵 NEW + 🟢 DOC | Pure tests (`test_decision_log_observability.py +321`) + audit docs + microbench. Zero live code touched. Coupled to #38 for full effect. |
| 40 | `85c1713` | QW UX Telegram trigger-specific header + HEDGE_ESCALATION icon + dead code | 1f / +14/-13 | [1214331938227616](https://app.asana.com/0/0/1214331938227616) — Completed High | **SAFE_DEPLOY** | 🟢 DOC | Only `telegram_notifier.example.py` (not the gitignored live file). Cosmetic Telegram changes — irrelevant while Telegram OFF. |

### Batch 4 Summary

| Class | Count |
|-------|-------|
| SAFE_DEPLOY | 7 |
| NEEDS_REVIEW | 3 |
| REQUIRES_BARBARA | 0 |
| BLOCKED | 0 |
| REDUNDANT | 0 |

**🚩 KEY FINDING — `0eb3131` touches level_detector.py POST-401e6da**:

This is the **only commit in the entire 50-commit set besides 82b83cc (batch 5) that touches `live/level_detector.py`** — the same file that 401e6da rebuilt with B+C ENSEMBLE. The `0eb3131` patch adds 45 lines for Wyckoff follow-through flip in `derive_m30_bias`. Production deploy currently has 401e6da's `level_detector.py` *without* this UTAD-aware patch. Need to evaluate whether 0eb3131 modifies any logic that interacts with `_get_daily_trend`/`signal_b`/`signal_c`.

**Telegram-related commits**: 6 of the 10 commits in batch 4 relate (directly or in audit docs) to Telegram. Since Telegram is currently OFF (kill-switch active), these commits' UX impact is dormant. The only meaningful change in this group is 788c57d's event_processor refactor.



## BATCH 5 — Commits 41-54 (P1.x family + STALE-D1H4 + concurrency + final M30-BIAS retry)

NOTE: Total commit count is **54** (not 50) — `git log 401e6da..2ab3f55 --oneline` returned 54 entries.

| # | SHA | Title | Files Δ | Asana | Class | Flags | Notes |
|---|-----|-------|---------|-------|-------|-------|-------|
| 41 | `ac9b2ff` | P1.3 M30-BOX-STAGNATION-RULE — A K=2 GLOBAL expiry | 2f / +528/-51 | [1214327736913830](https://app.asana.com/0/0/1214327736913830) — **NOT completed, On track** | **NEEDS_REVIEW** | 🔴 CORE | `m30_updater.py +304/-51` (heavy), 275 test lines. Box stagnation rule. Parent task still open — implementation may still be evolving. |
| 42 | `33fe340` | P1.3 hotfix tz-aware timestamps | 2f / +55/-10 | 1214327736913830 (same task) | **SAFE_DEPLOY** | 🔴 CORE | `m30_updater.py +28/-10` (paired hotfix for #41). Standalone safe. |
| 43 | `6c22673` | P1.3 audit deliverables Phase 1+2+2.5+3 docs | 14f / +6633 | 1214327736913830 (same task) | **SAFE_DEPLOY** | 🟢 DOC | Pure audit docs (massive +6633 lines but zero live code). |
| 44 | `760986d` | DECISIONS_LOG append DEC-2026-04-28-001/002 | 1f / +1480 | (no GID) | **SAFE_DEPLOY** | 🟢 DOC | Pure docs. |
| 45 | `2e9f091` | P1.4 Phase 2 Option B: block LONG-direction "sinais neutros" path | 3f / +240/-3 | [1214334465232053](https://app.asana.com/0/0/1214334465232053) — **NOT completed, On track** | **BLOCKED** | 🔴 CORE | `ats_live_gate.py +23`, `event_processor.py +12`, 208 test lines. Adds `BLOCK_P14_SINAIS_NEUTROS_LONG` filter. **Parent task still open — implementation explicitly incomplete**. Already observable in `/api/trades` history (e.g., one trade has `reason: BLOCK_P14_SINAIS_NEUTROS_LONG`). Blocking LONG signals based on incomplete reasoning is risky without ML-DS sign-off + Wyckoff continuation completion. |
| 46 | `82b83cc` | P1.2 Phase 2: tiered output (HIGH/MEDIUM/NONE) for daily_trend | 2f / +243/-2 | [1214332092113381](https://app.asana.com/0/0/1214332092113381) — Completed High | **NEEDS_REVIEW** | 🔴 CORE — **POST-401e6da** | `level_detector.py +49/-2` (additive). **Adds tier output to `_get_daily_trend`** — this is the second of two commits that touch level_detector after 401e6da (other is `0eb3131` #31). 196 test lines. Parent task is Completed but the change is to the file we just deployed. |
| 47 | `7bc57b9` | P1.2 audit deliverables Phase 0 + Path A + Phase 2 docs | 5f / +1597 | 1214332092113381 (same task) | **SAFE_DEPLOY** | 🟢 DOC | Pure audit docs. |
| 48 | `ae4f69b` | STALE-D1H4-001 Phase 1: windowed rebuild + DATA-002 fix + reactivate | 4f / +768/-35 | (no GID — design doc internal) | **NEEDS_REVIEW** | 🔴 CORE | `d1_h4_updater.py +122/-?`, `run_live.py +23/-?`. Windowed rebuild + reactivate D1H4 updater. Task description claims Phase 1 deliverable. Affects D1H4 stream that feeds into the live system. |
| 49 | `af9f40e` | STALE-D1H4-001 hotfix: move D1H4 startup AFTER processor instantiation | 1f / +22/-13 | (paired with #48) | **SAFE_DEPLOY** | 🔴 CORE | `run_live.py +22/-13`. Bootstrap ordering fix paired with #48. |
| 50 | `4fda59f` | STALE-D1H4-001 Phase 1 brief-back doc | 1f / +225 | (paired with #48) | **SAFE_DEPLOY** | 🟢 DOC | Pure brief-back doc. |
| 51 | `c33a48f` | V4-ICEBERG-FLOW-CONTRA investigation | 1f / +253 | [1214385161522925](https://app.asana.com/0/0/1214385161522925) | **SAFE_DEPLOY** | 🟢 DOC | Pure investigation doc. |
| 52 | `41ffb46` | CONCURRENCY-CYCLE-PROTECTION-RACE: lock + per-cycle local dict | 4f / +1116/-17 | [1214389998732838](https://app.asana.com/0/0/1214389998732838) — **NOT completed, On track** | **NEEDS_REVIEW** | 🔴 CORE | `event_processor.py +78/-17` (concurrency lock + per-cycle dict). 366 test lines + 689 doc/design. Multi-threading race fix. Parent task still open. Useful but incomplete. |
| 53 | `fb673a4` | PM-DECOUPLE-PHASE3 NameError fix: positions/trades orphans | 2f / +172/-3 | (no GID — bugfix to 889f5ee feature) | **SAFE_DEPLOY** | 🔴 CORE | `position_monitor.py +6/-3` (small NameError fix), 169-line audit script. Bug-fix to PM-DECOUPLE-PHASE3 (which IS in production deploy). |
| 54 | `2ab3f55` | M30-BIAS-DERIVATION-AUDIT Opção 3: demote hard-gate to SHADOW (HEAD) | 2f / +189/-14 | 1214320481337073 — **NOT completed, Off track** | **BLOCKED** ⚪ | 🔴 CORE — **REVERT-PATTERN** | `event_processor.py +41/-14` + 162 test lines. **THIRD attempt** at the same change as #29 (`73f55f5`) and reverted at #30 (`567e5ca`). Same Asana task that is **Off track**. Suggests unresolved decision. **DO NOT DEPLOY** without explicit Barbara/ML-DS resolution of the underlying audit. |

### Batch 5 Summary

| Class | Count |
|-------|-------|
| SAFE_DEPLOY | 7 |
| NEEDS_REVIEW | 5 |
| REQUIRES_BARBARA | 0 |
| **BLOCKED** | **2** (#45, #54) |
| REDUNDANT | 0 |

**🚩 NEW RISK PATTERNS**:

1. **Three open Asana tasks** in batch 5 alone reference live-code changes that are merged into `fix/xau-mid-population` (1214327736913830, 1214334465232053, 1214389998732838). Code is on the branch; Asana says incomplete. Discrepancy.

2. **Second `level_detector.py` post-401e6da commit (#46 `82b83cc`)** — combined with #31 (`0eb3131`), there are **two commits modifying `level_detector.py` after the deployed 401e6da**, totaling +94 lines. Each adds different functionality (tier output and Wyckoff flip respectively) to the file we just shipped. **Risk of conflict / behavioral drift** if deployed without integration validation.

3. **HEAD commit `2ab3f55` is BLOCKED** — same change-then-revert pattern repeating. The branch HEAD is itself an incomplete/contested commit.

4. **`P1.4` Sinais Neutros block (#45 `2e9f091`) is BLOCKED** — production trades log already shows `reason: BLOCK_P14_SINAIS_NEUTROS_LONG`, suggesting the rule was active in some past run. Task still open with an incomplete Wyckoff continuation calibration. Could explain the bias Barbara reported.



## OVERALL RISK PATTERNS

### Aggregate counts (54 commits)

| Class | Total |
|-------|-------|
| SAFE_DEPLOY | 28 (52%) |
| NEEDS_REVIEW | 19 (35%) |
| REQUIRES_BARBARA | 3 (6%) |
| BLOCKED | 2 (4%) |
| REDUNDANT | 2 (4%) |

### Risk-pattern flags identified

1. **🚩 Two `level_detector.py` post-401e6da commits**: #31 `0eb3131` (M30 BIAS UTAD_AWARE +45 lines) and #46 `82b83cc` (P1.2 tiered output +49 lines). Total +94 lines added to the file we just shipped at 401e6da. **Coupled risk** — must integration-test before deploying either, and verify they don't shift `_get_daily_trend`/`signal_b`/`signal_c` semantics.

2. **🚩 Revert pair + repeat attempt** (M30 BIAS hard-gate to SHADOW):
   - #29 `73f55f5` deploy → #30 `567e5ca` revert (cancel out, REDUNDANT)
   - #54 `2ab3f55` re-attempt of same change (the HEAD of `fix/xau-mid-population`) — **BLOCKED**
   - All three reference Asana 1214320481337073 which is **Off track and incomplete**.
   - Recommendation: do NOT deploy #54 without explicit ML-DS resolution.

3. **🚩 BLOCKED `2e9f091` (P1.4 Sinais Neutros block)**: rule actively filters LONG signals (visible in `/api/trades` history with `BLOCK_P14_SINAIS_NEUTROS_LONG` reason). Parent task `1214334465232053` is incomplete (WYCKOFF-CONTINUATION-CALIBRATION not finished). **This may be partially responsible for the observed signal bias**: filtering LONG signals while not filtering SHORT could cause perceived "inverted bias" complaints. Worth investigating relationship to BUG-SIGNAL-INVERTED.

4. **MT5 deletion sequence aligned with US-1.3**: cumulative ~1461 lines deleted across #9, #17, #22 (mt5_executor_hantec, mt5_history_watcher, mt5_executor). All under SISTEMA-SIGNAL-ONLY-INTERIM (Asana 1214309245971704, Completed). Should batch-confirm with Barbara that "MT5 definitively gone" before merging the deletes.

5. **Three open Asana tasks reference batch 5 commits**: 1214327736913830 (P1.3 box stagnation), 1214334465232053 (P1.4 sinais neutros), 1214389998732838 (CONCURRENCY race). Code is on branch; Asana says incomplete. Indicates work-in-progress that was never fully closed before the next round of edits started.

6. **DECISION-PATH-REFACTOR-GC-SPACE coherence**: commits #2, #3, #4, #5 (`06bdd7e`/`abdb3f3`/`3096030`/`31ea516`) form one coherent Phase 2/2.5/2.6/2.7 sequence. Total event_processor delta is large (~+700 lines). Must deploy as a unit; partial deploy = inconsistent state.

7. **SISTEMA-SIGNAL-ONLY-INTERIM coherence**: commits #6 through #27 (Blocos A–P, including D-stage deletions and CI guards) form one coherent program (Asana 1214309245971704). Cumulative +X/-1500 lines. Order-sensitive — Blocos must be applied in roughly the original sequence to avoid intermediate-state crashes.

8. **Doc-only commits**: ~14 commits are purely audit/forensic/architecture/decision docs. Net-zero risk to live code; bundled with the related code commits as evidence trail.

### Recommended deploy clusters

If incremental deploy is approved, recommended cluster boundaries (do NOT split):

**Cluster 1 — DECISION-PATH-REFACTOR (commits #2-#5)**: deploy as a unit. Highest risk, biggest LoC change to event_processor. Run integration tests before merging.

**Cluster 2 — SISTEMA-SIGNAL-ONLY-INTERIM (commits #6-#27)**: deploy as a unit OR carefully split into Blocos A-C (broker-agnostic helpers) → Blocos D-G (deletions/dashboards) → Blocos H-N (wire VPS / cleanup) → Blocos O-P (CI guards / docs). REQUIRES_BARBARA confirmation on MT5 deletes (#9, #17, #22).

**Cluster 3 — Standalone safe additions**: #1 (MACRO-MONITOR-VAP), #28 (UTF-8 hotfix), #32 (m30_updater excursion fix), #41+#42 (P1.3 m30 box stagnation + hotfix), #48-#50 (STALE-D1H4 phase 1 + hotfix + doc), #51 (V4-ICEBERG investigation doc), #53 (PM-DECOUPLE NameError fix). Mostly low-risk standalone fixes.

**Cluster 4 — Telegram-related (commits #33-#37, #40)**: low-risk because Telegram is OFF in deploy. Can defer until Telegram re-enabled.

**Cluster 5 — Observability (#38+#39)**: pure additive instrumentation. Safe to deploy after Cluster 1 (depends on event_processor stability).

**Cluster 6 — Concurrency (#52)**: REQUIRES sign-off; race-condition fix that's still under development per open Asana.

**EXCLUDE FROM ANY DEPLOY**:
- #29 `73f55f5` + #30 `567e5ca` (revert pair, net zero)
- #54 `2ab3f55` (BLOCKED — repeat of reverted change, parent task off track)

**SPECIAL ATTENTION** before deploy:
- #31 `0eb3131` (level_detector.py post-401e6da) — verify B+C ENSEMBLE intact
- #46 `82b83cc` (level_detector.py post-401e6da) — verify B+C ENSEMBLE intact + tier output additive
- #45 `2e9f091` (P1.4 Sinais Neutros block) — confirm not the source of inverted-bias complaints

## RECOMMENDATION

The 54-commit set is too heterogeneous to deploy as a single block. Recommend:

1. **IMMEDIATELY** investigate whether #45 `2e9f091` (BLOCK_P14_SINAIS_NEUTROS_LONG) is partially responsible for the observed inverted-signal bias. Check production `/api/trades` history pre-stop date 2026-05-02 — if many LONG signals were filtered with this reason while SHORTs weren't, that could explain Barbara's observation.

2. **Deploy in clusters with ML-DS sign-off per cluster**:
   - Cluster 3 first (low-risk standalone fixes)
   - Cluster 1 next (DECISION-PATH-REFACTOR with integration tests)
   - Cluster 2 with Barbara MT5 deletion confirmation
   - Cluster 5 after Cluster 1
   - Cluster 4 only when Telegram is re-enabled
   - Cluster 6 when CONCURRENCY task is closed
   - Skip excluded commits

3. **Resolve open Asana tasks** before deploying their commits: 1214320481337073 (M30-BIAS), 1214327736913830 (P1.3 box stagnation), 1214334465232053 (P1.4 sinais neutros), 1214389998732838 (CONCURRENCY).

4. **Special review** of `level_detector.py` cluster (#31, #46) — write integration test that confirms `_get_daily_trend` returns the same B+C ENSEMBLE behavior before and after these commits.

5. **Establish deploy gate** going forward: no commit lands on `fix/xau-mid-population` (or any branch tracked for production) without (a) Asana task Closed, (b) deploy authorization, (c) smoke test pass. The current 54-commit accumulation is the exact pattern Barbara called out — "trabalho em cima de trabalho sem melhoria real".

---

## DELIVERABLES

- This file: `C:\FluxQuantumAI\_audit\calibrations\audit_50_commits_2026-05-05.md` (~21KB)
- Cross-referenced 8 Asana tasks (5 Completed, 3 open/incomplete)
- Identified 1 revert pair, 2 BLOCKED commits, 3 open-task code drifts, 2 level_detector post-401e6da commits

## END


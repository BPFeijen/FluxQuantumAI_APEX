# Phase 1 — Ratification Audit (Ajustes 2, 3, 4)

Formal audit deliverable for the three ratification ajustes that required active
verification (Ajuste 1 = scope decision, Ajuste 5 = flag-only: both covered in
PLAN.md and phase1_discovery.txt).

**Generated:** 2026-04-21, post Pre-Flight ratification (READ-ONLY).
**Sprint:** `sprint_apex_stabilization_7fixes_20260421`

---

## Ajuste 2 — Schema comum v1_canonical audit

Ratification statement: *"Novos emission sites patchados … devem produzir payload no schema v1_canonical. NÃO alterar `_write_decision` existente. Se Phase 1 concluir que unificação de schema é necessária, propor refactor como sub-phase explícita antes de patchar; senão, adicionar helper `_build_v1_canonical_payload()` que gera o schema correto."*

### Consumer — `telegram_notifier.notify_decision()` fields read

From `C:\FluxQuantumAI\live\telegram_notifier.py:68-232`, the fields read from
`decision_live.json` (and used in rendered Telegram message):

| top-level field | sub-field read | fallback |
|---|---|---|
| `timestamp` | — | `""` |
| `decision_id` | — | `""` |
| `price_mt5` | — | `0` |
| `price_gc` | — | `0` |
| `trigger` | `.near_level_source` | `"?"` |
| `context` | `.phase`, `.m30_bias`, `.delta_4h`, `.m30_atr14` | fallback to `service_state.json` then `"?"` / `0` |
| `anomaly` | `.alignment`, `.severity`, `.position_action` | `"UNKNOWN"` |
| `iceberg` | `.alignment`, `.severity`, `.position_action` | `"UNKNOWN"` |
| `gates` | `.v1_zone.status`, `.v2_l2.status`, `.v3_momentum.status`, `.v4_iceberg.status` | `_gate_icon(None)` default |
| `position_event` | `.event_type`, `.direction_affected`, `.action_type`, `.reason`, `.execution_state`, `.broker`, `.ticket`, `.result` | for PM_EVENT branch |
| `decision` | see below |

Fields read inside `decision`:

| field | purpose |
|---|---|
| `decision.action` | branch selector — `"EXECUTED"` / `"GO"` / `"BLOCK"` / `"PM_EVENT"` |
| `decision.direction` | `"LONG"` / `"SHORT"` / `"?"` |
| `decision.action_side` | `"BUY"` / `"SELL"` — auto-derived from direction if absent |
| `decision.trade_intent` | `"ENTRY_LONG"` / `"ENTRY_SHORT"` / `"EXIT_*"` — auto `ENTRY_{direction}` if absent |
| `decision.message_semantics_version` | `"v1_canonical"` — default string |
| `decision.total_score` | int score (GO/EXECUTED branches) |
| `decision.reason` | block reason text |
| `decision.sl`, `.tp1`, `.tp2`, `.lots` | EXECUTED/GO branches only |
| `decision.execution.brokers[]` | PM_EVENT branch |

All reads use `.get(key, default)` — consumer is robust to missing fields.

### Emitter 1 — `_write_stale_block_decision` (event_processor.py:986-1043)

Produces schema:

```python
{
  "timestamp": ...,
  "price_mt5": ..., "price_gc": ..., "gc_mt5_offset": ...,
  "context": {
    "phase", "daily_trend", "m30_bias", "m30_bias_confirmed",
    "provisional_m30_bias", "m30_box_mt5", "m30_fmv_mt5", "m30_atr14",
    "liq_top_mt5", "liq_bot_mt5", "liq_top_gc", "liq_bot_gc", "delta_4h",
    "structure_stale", "structure_stale_reason",
  },
  "trigger": { "type", "level_type", "level_price_mt5", "proximity_pts", "near_level_source" },
  "decision": {
    "action": "BLOCK",
    "direction", "action_side", "trade_intent",
    "message_semantics_version": "v1_canonical",
    "reason", "total_score": None,
    "execution": { "overall_state", "attempted", "brokers" },
  },
  "expansion_lines_mt5": [],
  "micro_atr_proxy", "protection", "anomaly", "iceberg",
}
```

**Compatibility check vs consumer:**

| consumer field | present in emitter? |
|---|---|
| `timestamp` | ✓ |
| `decision_id` | ✗ (added by caller `_write_decision` at line 684: `decision_data["decision_id"] = str(uuid.uuid4())[:8]`) → populated via `_write_decision` side-effect |
| `price_mt5` / `price_gc` | ✓ |
| `trigger.near_level_source` | ✓ |
| `context.phase` | ✓ |
| `context.m30_bias` | ✓ |
| `context.delta_4h` | ✓ |
| `context.m30_atr14` | ✓ |
| `anomaly.*` | ✓ |
| `iceberg.*` | ✓ |
| `gates.*` | ✗ **missing** — consumer `.get("gates", {})` falls back; gate icons render as unknown default. Non-fatal. |
| `decision.action` | ✓ (`"BLOCK"`) |
| `decision.direction` | ✓ |
| `decision.action_side` | ✓ |
| `decision.trade_intent` | ✓ |
| `decision.message_semantics_version` | ✓ (`"v1_canonical"`) |
| `decision.total_score` | ✓ (`None`) |
| `decision.reason` | ✓ |
| `decision.execution.brokers[]` | ✓ |

**Verdict:** `_write_stale_block_decision` schema is v1_canonical-complete
for the consumer's BLOCK path; only `gates` sub-dict is missing, which the
consumer tolerates via `.get`. Suitable as the template for Fix #1 patch
(Phase 2).

### Emitter 2 — `_publish_canonical_pm_event` (position_monitor.py:2007-2045)

Produces schema:

```python
{
  "timestamp": now_iso,
  "event_source": "POSITION_MONITOR",
  "position_event": {...},
  "decision": {
    "action": "PM_EVENT",
    "direction", "action_side", "trade_intent",
    "message_semantics_version": "v1_canonical",
    "reason",
    "execution": { "overall_state", "attempted", "brokers": [...], "updated_at" },
  },
  "created_at": now_iso,
  "decision_id": str(uuid.uuid4())[:8],
}
```

**Compatibility check vs consumer PM_EVENT branch (telegram_notifier.py:188-232):**

Consumer PM_EVENT branch reads `dl.get("position_event", {})` for all rendering
fields. All required fields (event_type, direction_affected, action_type,
reason, execution_state, broker, ticket, result) live inside `position_event`
and are populated by `_publish_canonical_pm_event`. ✓ COMPATIBLE.

Consumer does NOT read `price_mt5` / `price_gc` / `context` / `trigger` / `gates`
in the PM_EVENT branch — those are entry-path fields only. So their absence in
PM events is by design.

### Conclusion — Ajuste 2

- **Schema unification refactor is NOT required** for Phase 1.
- Both existing canonical emitters (`_write_stale_block_decision` + `_publish_canonical_pm_event`) already produce v1_canonical-compatible payloads for the consumer.
- Recommendation: Phase 2 (M30_BIAS_BLOCK canonical emit) uses `_write_stale_block_decision` structure as a 1:1 template. A thin helper `_build_bias_block_payload(direction, price, reason, bias, bias_confirmed)` — internal to `event_processor.py` — can wrap the payload build; it calls `self._write_decision(payload)` just like `_write_stale_block_decision` does.
- No need to invent `_build_v1_canonical_payload()` globally. That would be Option (a) "unification" path which `phase1_categorization.md §6` recommends deferring.
- **Gates sub-dict absence** on `_write_stale_block_decision` is a pre-existing minor issue (not a regression); leave as-is for this sprint. If Fix #1 adopts the same template, BIAS_BLOCK rows will also lack gates. Telegram consumer is resilient. Can be fixed in a later sprint without restart.

---

## Ajuste 3 — Heartbeat thread LIVE verification (formal)

Ratification statement: *"ANTES de Phase 1 code changes, verificar thread
LIVE status … Reporta: Thread vivo? SERVICE_STATE_WRITE_FAIL presence?
Último HEARTBEAT_LOOP_ENTERED quando? service_state.json age."*

### Queries run (exact scripts per ratification §3)

```powershell
Select-String -Path 'C:\FluxQuantumAI\logs\service_stdout.log','C:\FluxQuantumAI\logs\service_stderr.log' `
    -Pattern 'HEARTBEAT_LOOP_ENTERED|SERVICE_STATE_WRITE_OK|SERVICE_STATE_WRITE_FAIL' -SimpleMatch
Get-Item 'C:\FluxQuantumAI\logs\service_state.json' | Select FullName, Length, LastWriteTime
(Get-Date) - (Get-Item 'C:\FluxQuantumAI\logs\service_state.json').LastWriteTime
```

### Results

| metric | value |
|---|---|
| `service_state.json` FullName | `C:\FluxQuantumAI\logs\service_state.json` |
| `service_state.json` Length | 1,231 bytes |
| `service_state.json` LastWriteTime | 2026-04-21 09:06:20 local (UTC+2) |
| Current time at check | 2026-04-21 09:06:41 local |
| **`service_state.json` age** | **21.1 seconds** |
| `SERVICE_STATE_WRITE_OK` occurrences (stdout + stderr) | **12,254** |
| `SERVICE_STATE_WRITE_FAIL` occurrences | **0** |
| `HEARTBEAT_LOOP_ENTERED` occurrences | 12 (= lifetime service starts) |
| `HEARTBEAT_THREAD_STARTED` occurrences | 12 (matches; 1-per-start) |

### Evaluation vs ratification thresholds

| threshold | observed | verdict |
|---|---|---|
| age < 60 s → thread alive | 21.1 s | ✓ **ALIVE** |
| age > 300 s → thread dead | — | — |
| any `SERVICE_STATE_WRITE_FAIL` | 0 | ✓ no crash signature |

### Conclusion — Ajuste 3

Heartbeat thread is **LIVE and healthy**. 12,254 successful state writes on
current log retention, zero write failures. `service_state.json` within
~21 s of Now.

Ratification's proposed fallback ("If thread dead: Phase 1 adiciona guard
try/except mais robusto + logging de crash + auto-restart logic") is **not
triggered**. Phase 1 confirms design and **proceeds without structural
modification**; only schema extensions needed later:

- Phase 3 (Fix #6 Telegram observability) → add `service_state.json.telegram`
  sub-dict with `{sends, failures, last_success_at, last_failure_at, last_dedup_reason}`
- Phase 6 (Fix #4 ApexNewsGate health) → add `service_state.json.news_gate`
  sub-dict with `{status, error}`

Both are additive to the `state` dict at `event_processor.py:786-824`,
zero-risk to existing consumers (they use `.get`).

---

## Ajuste 4 — ApexNewsGate path validation

Ratification statement: *"Meu prompt original do sprint assumia 'relative→absolute conversion'. ASSUMPTION ERRADA. Este import já é absolute, com path insertion. … Verificar path existe … Buscar localização real do módulo."*

### Paths tested

```powershell
Test-Path 'C:/FluxQuantumAPEX'                                        # → True
Test-Path 'C:/FluxQuantumAPEX/APEX GOLD'                              # → True
Test-Path 'C:/FluxQuantumAPEX/APEX GOLD/APEX_News'                    # → True
Test-Path 'C:/FluxQuantumAPEX/APEX GOLD/APEX_News/apex_news_gate.py'  # → True
```

All four paths referenced by `event_processor.py:92-103` exist. The file
`apex_news_gate.py` is at the expected location.

Global search `Get-ChildItem -Path C:\, D:\ -Recurse -Filter apex_news_gate.py`
returned no additional hits (the expected-location file is the only one).

### Interpretation

The import is failing NOT because the file is missing and NOT because the
path is wrong. The error captured in DIAGNOSIS §10.4 is:

```
ApexNewsGate not available -- trading without news gate:
attempted relative import with no known parent package
```

That error originates from INSIDE `apex_news_gate.py` itself: the module (or
one of its imports) contains a `from .something import X` or `from ..foo import bar`
style line, which fails because Python loads the module as a top-level import
(via `sys.path.insert(0, ...)` + bare `from apex_news_gate import news_gate`),
not as part of a package. In that context, relative imports have no parent
package to resolve against.

**Phase 6 scope amendment** (subject to ML Engineer ratification):

- Root cause is NOT the outer `event_processor.py:92-103` import block (which is already absolute).
- Root cause IS a relative import INSIDE `apex_news_gate.py` or its transitive dependencies at `C:\FluxQuantumAPEX\APEX GOLD\APEX_News\`.
- Phase 6 must READ that module FIRST and identify the offending relative import(s), THEN propose the minimum change — either (a) convert relative → absolute inside that package, or (b) add a parent-package-loader shim.
- Do NOT stop-and-ask Barbara about path location (Ratification §4 condition "Se path não existir" does NOT apply). Proceed to Phase 6 discovery of the actual offending file.

### Conclusion — Ajuste 4

- Path layout confirmed correct.
- Stop-and-ask trigger NOT activated.
- Phase 6 must add a discovery sub-step: read contents of `C:\FluxQuantumAPEX\APEX GOLD\APEX_News\apex_news_gate.py` + its imports → identify the relative import. The fix will then be inside that file (or its package), not in `event_processor.py`.
- Phase 6 does NOT modify paths in `event_processor.py:92-103` — those are correct.

---

## Summary — Ajustes coverage status

| Ajuste | Status | Deliverable |
|---|---|---|
| 1 — Phase 1 scope revisto | ✓ satisfied in `phase1_discovery.txt §(i)` + `phase1_categorization.md §6` | option (b) recommended |
| 2 — Schema comum v1_canonical | ✓ **this file §Ajuste 2** | unification refactor NOT required |
| 3 — Heartbeat LIVE | ✓ **this file §Ajuste 3** | thread healthy; 21 s age; 0 failures; 12,254 OK writes |
| 4 — ApexNewsGate path | ✓ **this file §Ajuste 4** | paths correct; bug is inside the module (relative import) |
| 5 — Git branch / running binary | ✓ satisfied in `PLAN.md §1 note` | flag only; binary pre-074a482; `derive_h4_bias` activates on Phase 7 restart |

**All 5 Ajustes now formally covered.**

---

## State of play

- **Files modified:** 0 (entire Phase 1 Discovery has been READ-ONLY).
- **Services touched:** 0.
- **Capture PIDs:** 2512 / 8248 / 11740 intact.
- **Deliverables in sprint dir:**
  - `backup_pre_sprint/` + `MANIFEST.txt` (Pre-Flight)
  - `PLAN.md` (Pre-Flight)
  - `phase1_discovery.txt` (4-point narrative)
  - `phase1_emission_inventory.txt` (130 raw Select-String lines)
  - `phase1_categorization.md` (formal grades + complexity)
  - `phase1_ratification_audit.md` (this file — Ajustes 2/3/4)

Phase 1 Discovery complete. **STOP — awaiting Claude (ML Engineer) ratification
for Phase 1 code changes (Fix #3 canonical publish path) or alternatively
direct proceed to Phase 2 (Fix #1 M30_BIAS_BLOCK) if Option (b) minimum-diff
path is chosen.**

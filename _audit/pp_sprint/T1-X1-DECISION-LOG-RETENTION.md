# T1-X1-DECISION-LOG-RETENTION — 90-day Retention for decision_log.jsonl

**Author:** ClaudeCode
**Date:** 2026-04-23 (UTC)
**Branch:** `fix/xau-mid-population` @ `d721c9d` (unchanged — no functional code changes)
**Type:** Documentation + deploy safeguard + comment annotation. Zero behavioural change.
**Option applied:** A (SIMPLEST per G-SIMPLEST-FIRST) — documentation + preserve script + code comment
**Authorization:** Barbara Feijen, 2026-04-23 ("Avanca com opcao A")

---

## Section 1 — Executive summary

| Property | Before | After |
|---|---|---|
| Retention mechanism | implicit (append-only, undocumented) | explicit policy document |
| Online retention target | not defined | 90 days (natural growth; current file grows ~4 MB/day) |
| Pre-deploy snapshot | inconsistent (only some deploy folders had backups) | `scripts/preserve_decision_log.py` available for all future deploys |
| Append-only contract | present in code, not documented | documented in `_write_decision` docstring + policy doc |
| Rotation | none | none (not required until ~500 MB file size) |
| Disk impact | 38 MB today → 360 MB @ 90 days | 360 MB = 0.15% of free disk (233 GB available) |

**Disk impact verified:** 360 MB projected 90-day size is comfortably within disk budget. No rotation introduced.

**Behavioural impact on live system:** zero. No changes to `_write_decision`, no changes to config, no service restart.

---

## Section 2 — Current mechanism discovery

### 2.1 Code path

- `event_processor._write_decision` at `live/event_processor.py:684`
- Appends a single JSON line per call to `DECISION_LOG_PATH = C:/FluxQuantumAI/logs/decision_log.jsonl` (module-level constant at `:226`)
- Opens with `open(path, "a", encoding="utf-8")` at `:707` — pure append mode
- Invoked from:
  - `:1052` (`_write_stale_block_decision`)
  - `:1346` (unknown context — verified; also a write path)
  - `:2649` (initial write of GO/BLOCK decision)
  - `:2806` (post-execution re-write with strategy_context)
  - `:2877` (EXEC_FAILED re-write)

### 2.2 Rotation / cleanup scan

Grep across entire repository for `decision_log|rotate|logrotate|retention|cleanup|truncate|os.remove.*decision|shutil.move.*decision|decision_log.*(\.remove|\.unlink|\.write_text|\.truncate|\.replace|\.delete|\.clear)`:

- **Zero matches** in `.py` files for any truncation / rotation / cleanup code targeting `decision_log.jsonl`.
- **Zero matches** in `.bat` / `.ps1` files.
- The file is strictly append-only.

### 2.3 File lifecycle (empirical)

| Property | Value |
|---|---|
| Birth | 2026-04-14 03:00:08 UTC (`stat --format=%w` equivalent) |
| Current size | ~38 MB (2026-04-23 07:04 UTC) |
| Lines | 16,307 records (mixed GO/BLOCK/EXEC_FAILED/EXECUTED) |
| Growth rate | ~4 MB / day |
| Last mod | continuously appended in production |

### 2.4 Historical reset

A one-time reset occurred on or shortly before 2026-04-14. Causes (in order of likelihood):

1. Sprint-8 deploy (2026-04-13 per `project_sprint8_deployed` memory) — deploy script may have cleared the live file after backing it up.
2. Manual cleanup by operator.
3. Disk / file-system event.

No authoritative pre-04-14 backup exists in `Backups/`. This history is unrecoverable and motivates the new preservation policy below.

### 2.5 Existing backups

- `Backups/pre-deploy-20260417_141337/logs/decision_log.jsonl` — 10.7 MB, covers 2026-04-14 01:00 → 2026-04-16 17:43 UTC. Only partial prefix of current file.
- `Backups/pre-deploy-fase7-20260418_120533/logs/` — empty. Deploy did NOT snapshot decision_log.
- `Backups/pre-telegram-fix-20260418_011600/logs/` — empty. Deploy did NOT snapshot decision_log.

Conclusion: pre-deploy snapshots have been INCONSISTENT. The new `preserve_decision_log.py` tool makes them trivial and repeatable.

---

## Section 3 — Change applied

### 3.1 Files created

| Path | Role | Size |
|---|---|---|
| `docs/DECISION_LOG_RETENTION_POLICY.md` | Authoritative policy (retention target, append-only contract, preservation obligations, monitoring) | ~3.5 KB |
| `scripts/preserve_decision_log.py` | Defensive pre-deploy snapshot utility. UTC-timestamped copy to `Backups/pre-op-<TS>/logs/`. Zero impact on live file. | ~2 KB |

### 3.2 Files modified

| Path | Change |
|---|---|
| `live/event_processor.py` | Added 6-line retention contract note to `_write_decision` docstring (`:685-700`). No behavioural change. |

### 3.3 Files NOT modified

- `config/settings.json` — no retention-related keys needed.
- Any NSSM service definition — no restart needed.
- Any deploy script — safeguard is opt-in and wired only when operators choose to call it.

### 3.4 Diff summary

```
 docs/DECISION_LOG_RETENTION_POLICY.md  | +120 lines (new file)
 scripts/preserve_decision_log.py       |  +65 lines (new file)
 live/event_processor.py                |   +6 lines in _write_decision docstring
```

---

## Section 4 — Data preservation check (G-NO-DATA-LOSS)

| Concern | Result |
|---|---|
| Live `decision_log.jsonl` preserved | ✅ unchanged (38,034,940 bytes before and after; verified post-snapshot) |
| `_write_decision` behaviour preserved | ✅ only docstring modified; code path unchanged; py_compile PASS |
| Pre-existing backups preserved | ✅ `Backups/pre-deploy-*` folders untouched |
| New snapshot created during this task | ✅ `Backups/pre-op-20260423_053006/logs/decision_log.jsonl` (38 MB) — first test-invocation snapshot; kept as first evidence that the tool works |
| No service restart | ✅ ports 8000/8002 LISTENING; NSSM service PIDs unchanged |
| No config change | ✅ `git diff -- config/` empty |

---

## Section 5 — Retention policy document

See `docs/DECISION_LOG_RETENTION_POLICY.md` for the full authoritative policy. Key points:

1. **Append-only** — never truncate, rename, delete.
2. **90-day minimum online retention** — achieved by natural append-only growth.
3. **~360 MB projected at 90 days** — 0.15% of available disk.
4. **Preservation obligations for deploys / restarts / manual actions** — explicit list in policy §4.
5. **Recovery sources** — `Backups/pre-op-<timestamp>/logs/` has priority over `Backups/pre-deploy-<timestamp>/logs/`.
6. **Review cadence** — every 6 months. Next review: 2026-10-23.

---

## Section 6 — Sanity verification

| Item | Pre-task | Post-task |
|---|---|---|
| git HEAD | `d721c9d` | `d721c9d` (UNCHANGED — no commit yet) ✅ |
| live/ modifications | — | `event_processor.py` docstring only (+6 lines); `py_compile` PASS |
| config/ modifications | — | **none** ✅ |
| docs/ additions | — | `DECISION_LOG_RETENTION_POLICY.md` ✅ |
| scripts/ additions | — | `preserve_decision_log.py` ✅ |
| Port 8000 (capture L2) | LISTENING | LISTENING ✅ |
| Port 8002 (iceberg) | LISTENING | LISTENING ✅ |
| Service restarts | 0 | **0** ✅ |
| GitHub push | none | **none** ✅ |
| Test snapshot written | — | `Backups/pre-op-20260423_053006/logs/decision_log.jsonl` ✅ |
| Live `decision_log.jsonl` file size | 38,034,940 B | 38,034,940 B (unchanged) ✅ |

---

## Section 7 — Next steps

Per errata (`T1-X1-PHASE-ALIGNMENT-AUDIT.md §13`), retention task is a pre-requisite for the real fix targets:

1. **T1-X1-LOGGING-FIX** — correct reason strings at `event_processor.py:3213-3216` so substring classifier produces accurate `entry_mode` labels. Spec available in Downloads.
2. **T1-X1-PULLBACK-REVALIDATION** — deep read-only analysis of the PULLBACK branch directional bug. Requires LOGGING-FIX landed first so 30-day accumulation of correctly-labeled data can be analysed. Spec available in Downloads.
3. **T1-X1-DEAD-CODE-HARDENING** — defensive hard SKIP of `:3566-3605`. BACKLOG, P3, not-yet-authorised.

With 90-day retention policy in place, future PULLBACK-REVALIDATION cycles have a defensible data foundation. The BUEC 7-day window limitation no longer bounds audit confidence.

---

## Section 8 — Commit plan

Spec-wired commit message (when Barbara authorises):

```
docs(decision-log): introduce 90-day retention policy + preserve-script + _write_decision docstring

Per T1-X1-DECISION-LOG-RETENTION (2026-04-23). Option A:
  - docs/DECISION_LOG_RETENTION_POLICY.md — authoritative policy
  - scripts/preserve_decision_log.py — defensive pre-deploy snapshot utility
  - live/event_processor.py — 6-line retention contract comment in _write_decision

Zero behavioural change. No service restart required. No config modification.

See: _audit/pp_sprint/T1-X1-DECISION-LOG-RETENTION.md
```

Not yet committed per standing rule "Brief-back BEFORE execution" of any commit.

---

_End of T1-X1-DECISION-LOG-RETENTION. Awaiting Barbara review for commit authorisation and sequence to T1-X1-LOGGING-FIX._

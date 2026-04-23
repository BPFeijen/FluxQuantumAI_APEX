# Decision Log Retention Policy

**Effective date:** 2026-04-23
**Owner:** Barbara Feijen (project lead)
**Applies to:** `C:\FluxQuantumAI\logs\decision_log.jsonl`
**Related task:** `T1-X1-DECISION-LOG-RETENTION` (see `_audit/pp_sprint/T1-X1-DECISION-LOG-RETENTION.md`)

---

## 1. Scope

This policy governs the `decision_log.jsonl` file — the append-only audit trail of every gate decision written by `event_processor._write_decision` (`live/event_processor.py:684`).

It does NOT govern other log files (`continuation_trades.jsonl`, `position_decisions.log`, `hedge_events.log`, service stdout/stderr logs). Those have separate lifecycles.

---

## 2. Retention target

| Property | Value |
|---|---|
| Minimum online retention | **90 days** |
| File lifecycle | **Append-only** (no rotation, no truncation, no compaction) |
| Expected daily growth | ~4 MB/day (empirical baseline 2026-04-14 → 2026-04-23) |
| Projected 90-day size | ~360 MB |
| Disk budget | < 500 MB before review (well within current 233 GB available on C:) |

At 500 MB file size a follow-up task may introduce time-partitioned rotation (`decision_log_<YYYY-MM>.jsonl.gz` for months older than 90 days). Not required today.

---

## 3. Append-only contract

The live production path writes via `open(..., "a")` at `live/event_processor.py:707`. The file is never truncated, renamed, deleted, or replaced by any Python code in the repository.

**The codebase preserves the following invariants:**
- `_write_decision` appends a single JSON line per call.
- No rotation handler is installed in the `logging` module for this path (it is not a `logging` target — it is a direct file handle).
- No scheduled script (cron/Task Scheduler/watchdog) modifies this file.

If any future code or script needs to prune / rotate / archive this file, it **MUST** be documented here first and reviewed by Barbara before merge.

---

## 4. Preservation obligations for deploys, restarts, and infrastructure work

**Any operation that could interrupt or replace the live system MUST preserve `decision_log.jsonl`.**

### 4.1 Deploys (application code updates)

- Run `python scripts\preserve_decision_log.py` BEFORE the deploy action to snapshot the current live file into `Backups\pre-op-<YYYYMMDD_HHMMSS>\logs\decision_log.jsonl` (UTC timestamp).
- The live file must NOT be deleted, moved, or truncated by the deploy script.
- Post-deploy the service opens the existing file with `"a"` mode — append continues seamlessly.

### 4.2 Service restarts (NSSM)

- `nssm restart FluxQuantumAPEX` and `nssm restart FluxQuantumAPEX_Live` are safe and do NOT truncate the file (Python reopens with append mode).
- No special action required. The file survives restarts.

### 4.3 Manual filesystem actions

- Do NOT `del` / `erase` / `rm` the file.
- Do NOT open the file in write mode from any other tool (Excel, editors that save in-place).
- Do NOT rename the file while the service is running.

### 4.4 Incident recovery

If the file IS lost (accidental delete, disk corruption, bad deploy), recovery sources in preference order:
1. `C:\FluxQuantumAI\Backups\<most-recent-timestamp>\logs\decision_log.jsonl`  — deploy snapshots
2. The live file itself  — start again from birth (acceptable but loses history)

---

## 5. Known historical reset

The current live file has birth timestamp `2026-04-14 03:00:08 UTC`. Prior records (pre 2026-04-14) are NOT available in any known backup — the reset event on 2026-04-14 pre-dates the first recorded pre-deploy snapshot (2026-04-17).

Root cause of the 2026-04-14 reset is not definitively identified but aligns temporally with the Sprint-8 deploy (live 2026-04-13 per `project_sprint8_deployed` memory). This policy exists to prevent recurrence.

---

## 6. Monitoring

Informal monitoring SHOULD verify monthly:

```bash
ls -lh /c/FluxQuantumAI/logs/decision_log.jsonl
# Expect: size grows by ~120 MB/month. If it shrinks or resets, investigate.

head -1 /c/FluxQuantumAI/logs/decision_log.jsonl | python -c "import json,sys; print(json.loads(sys.stdin.read())['timestamp'])"
# Expect: oldest record advances over time (once >90 days of data accumulate and
#         future rotation kicks in), OR stays constant if still append-only.
```

No automated alerting is configured. If the file mtime stalls > 30 min during RTH while the service is running, that is a separate outage (not a retention issue).

---

## 7. Review

Policy review cadence: every 6 months or upon any retention-related incident.

Next review due: **2026-10-23**.

---

_Retention policy v1.0. Introduced as part of T1-X1-DECISION-LOG-RETENTION task 2026-04-23._

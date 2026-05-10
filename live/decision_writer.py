"""
Centralized writer for decision_live.json + decision_log.jsonl.

W3.1 (2026-05-09): single function with internal lock replaces 4 duplicated
tmp -> rename code blocks across event_processor, hedge_manager, position_monitor.

Design notes:
- decision_live.json: latest snapshot, atomic via tmp + rename
- decision_log.jsonl: append-only audit trail
- Lock is module-level RLock; reentrant, so nested calls in same thread don't deadlock
- All callers acquire the SAME lock so cross-module races are impossible

Usage:
    from live.decision_writer import write_decision_atomic
    write_decision_atomic(payload)              # writes both
    write_decision_atomic(payload, append_log=False)  # only live (rare)

For backwards compat, the lock itself is still importable as `WRITE_LOCK`
(used directly by position_monitor's self._canonical_lock attribute).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("apex.decision_writer")

# ---------------------------------------------------------------------------
# Singleton lock (re-entrant)
# ---------------------------------------------------------------------------
WRITE_LOCK: threading.RLock = threading.RLock()

# ---------------------------------------------------------------------------
# Canonical paths
# ---------------------------------------------------------------------------
DECISION_LIVE_PATH = Path("C:/FluxQuantumAI/logs/decision_live.json")
DECISION_LOG_PATH  = Path("C:/FluxQuantumAI/logs/decision_log.jsonl")


def write_decision_atomic(payload: dict, *, append_log: bool = True) -> bool:
    """Write payload to decision_live.json (atomic) and append to decision_log.jsonl.

    Returns True on full success, False on any I/O failure (logged). Failures in
    the log append do NOT mark overall failure (live snapshot is more critical).

    Thread-safe: acquires WRITE_LOCK internally. Callers should NOT hold the
    lock externally (RLock makes nested calls safe but adds confusion).
    """
    ok = True
    try:
        DECISION_LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with WRITE_LOCK:
            tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            tmp.replace(DECISION_LIVE_PATH)
    except Exception as e:
        log.error("decision_live.json write failed: %s", e)
        ok = False

    if append_log:
        try:
            DECISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")
        except Exception as e:
            log.error("decision_log.jsonl append failed: %s", e)
            # Do NOT mark `ok=False` — live snapshot succeeded; audit miss is recoverable
    return ok

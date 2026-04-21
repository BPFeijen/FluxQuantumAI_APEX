"""
C:\\FluxQuantumAI\\live\\signal_queue.py

Thread-safe signal queue for EA distribution.
Signals are persisted to JSON so they survive service restarts.

Signal lifecycle:
  PENDING  -> EA polls and receives it
  SENT     -> EA confirmed receipt, awaiting execution
  EXECUTED -> EA confirmed execution (ticket returned)
  FAILED   -> EA reported error
  EXPIRED  -> Signal too old (> MAX_AGE_SEC) -- discarded
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.signal_queue")

QUEUE_FILE  = Path(r"C:\FluxQuantumAI\logs\signal_queue.json")
MAX_AGE_SEC = 30   # discard signals older than this (price likely stale)

_lock  = threading.Lock()
_queue: list[dict] = []          # in-memory list, persisted on every change


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save() -> None:
    """Persist current queue to disk (called under _lock)."""
    try:
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUEUE_FILE.write_text(json.dumps(_queue, indent=2), encoding="utf-8")
    except Exception as e:
        log.error("signal_queue: save error: %s", e)


def _load() -> None:
    """Load queue from disk on startup."""
    global _queue
    if QUEUE_FILE.exists():
        try:
            _queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            log.info("signal_queue: loaded %d signals from disk", len(_queue))
        except Exception as e:
            log.warning("signal_queue: load error: %s -- starting fresh", e)
            _queue = []


def _purge_expired() -> None:
    """Remove PENDING signals older than MAX_AGE_SEC (called under _lock)."""
    global _queue
    now = datetime.now(timezone.utc)
    before = len(_queue)
    fresh = []
    for s in _queue:
        if s["status"] != "PENDING":
            fresh.append(s)
            continue
        created = datetime.fromisoformat(s["created_at"])
        age = (now - created).total_seconds()
        if age > MAX_AGE_SEC:
            log.warning("signal_queue: EXPIRED signal %s (age=%.0fs)", s["id"], age)
        else:
            fresh.append(s)
    _queue = fresh
    if len(_queue) != before:
        _save()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init() -> None:
    """Call once at startup to load persisted queue."""
    with _lock:
        _load()


def push(
    signal_type: str,          # ENTRY | EXIT | MODIFY | HEDGE
    direction: str,            # LONG | SHORT (or "" for EXIT)
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
    lot_leg1: float,
    lot_leg2: float,
    lot_runner: float,
    accounts: list[int],       # MT5 account numbers to send to
    instrument: str = "XAUUSD",
    ticket: Optional[int] = None,   # for EXIT/MODIFY -- which ticket to act on
    extra: Optional[dict] = None,
) -> str:
    """Add a signal to the queue. Returns signal ID."""
    sig_id = str(uuid.uuid4())[:8]
    signal = {
        "id":          sig_id,
        "status":      "PENDING",
        "signal_type": signal_type,
        "instrument":  instrument,
        "direction":   direction,
        "entry":       entry,
        "sl":          sl,
        "tp1":         tp1,
        "tp2":         tp2,
        "lot_leg1":    lot_leg1,
        "lot_leg2":    lot_leg2,
        "lot_runner":  lot_runner,
        "accounts":    accounts,
        "ticket":      ticket,
        "extra":       extra or {},
        "created_at":  _now_iso(),
        "sent_at":     None,
        "executed_at": None,
        "confirmations": {},      # {account: {ticket, entry, ts}}
        "error":       None,
    }
    with _lock:
        _queue.append(signal)
        _save()
    log.info("signal_queue: PUSH %s  type=%s  dir=%s  entry=%.2f  accounts=%s",
             sig_id, signal_type, direction, entry, accounts)
    return sig_id


def peek(account: int) -> Optional[dict]:
    """
    Return the next PENDING signal for this account (without removing it).
    Marks it as SENT so other EAs don't grab it simultaneously.
    """
    with _lock:
        _purge_expired()
        for sig in _queue:
            if sig["status"] != "PENDING":
                continue
            if account not in sig["accounts"]:
                continue
            # Mark as SENT for this account
            sig["status"] = "SENT"
            sig["sent_at"] = _now_iso()
            _save()
            return dict(sig)
    return None


def confirm(signal_id: str, account: int, result: dict) -> bool:
    """
    EA calls this after executing. result = {ticket, entry, error}.
    Returns True if signal found.
    """
    with _lock:
        for sig in _queue:
            if sig["id"] != signal_id:
                continue
            sig["confirmations"][str(account)] = {
                **result,
                "ts": _now_iso(),
            }
            if result.get("error"):
                sig["status"] = "FAILED"
                sig["error"]  = result["error"]
                log.error("signal_queue: FAILED %s  account=%d  error=%s",
                          signal_id, account, result["error"])
            else:
                sig["status"]      = "EXECUTED"
                sig["executed_at"] = _now_iso()
                log.info("signal_queue: EXECUTED %s  account=%d  ticket=%s  entry=%.2f",
                         signal_id, account,
                         result.get("ticket"), result.get("entry", 0))
            _save()
            return True
    log.warning("signal_queue: confirm -- signal %s not found", signal_id)
    return False


def get_all() -> list[dict]:
    """Return snapshot of full queue (for dashboard/debug)."""
    with _lock:
        _purge_expired()
        return list(_queue)


def clear_done(max_keep: int = 100) -> int:
    """Remove EXECUTED/FAILED/EXPIRED signals, keeping last max_keep. Returns removed count."""
    global _queue
    with _lock:
        done = [s for s in _queue if s["status"] in ("EXECUTED", "FAILED")]
        pending = [s for s in _queue if s["status"] not in ("EXECUTED", "FAILED")]
        keep = done[-max_keep:]
        removed = len(done) - len(keep)
        _queue = pending + keep
        if removed:
            _save()
        return removed


# Load on import
_load()

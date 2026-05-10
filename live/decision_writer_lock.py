"""
DEPRECATED 2026-05-09 (W3.1): use `live.decision_writer` instead.

This file is now a thin re-export shim of the WRITE_LOCK from
`live.decision_writer`. Originally created in W1.1 as a standalone lock module;
W3.1 extracted the full atomic-write function into decision_writer.py so this
file's only role is backwards-compat for `from live.decision_writer_lock import WRITE_LOCK`.

Direct callers should migrate to `from live.decision_writer import write_decision_atomic`.
"""
from live.decision_writer import WRITE_LOCK  # noqa: F401

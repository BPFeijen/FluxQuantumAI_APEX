"""
preserve_decision_log.py

Defensive pre-deploy snapshot of decision_log.jsonl.
Copies the live decision_log.jsonl into Backups/<timestamp>/logs/ BEFORE
any deploy / infra change that could replace or interrupt the live file.

This script DOES NOT truncate, move, or delete the live file.
It is a read-only copy operation (shutil.copy2 preserves metadata).

Usage (from any deploy script, shell, or manually):
    python C:\\FluxQuantumAI\\scripts\\preserve_decision_log.py

Exit codes:
    0  snapshot written successfully
    2  source file not found
    3  destination directory creation failed
    4  copy operation failed

Related policy: docs/DECISION_LOG_RETENTION_POLICY.md (section 4.1)
Introduced by:  T1-X1-DECISION-LOG-RETENTION (2026-04-23)
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
BACKUPS_ROOT = Path(r"C:\FluxQuantumAI\Backups")


def main() -> int:
    if not SRC.exists():
        print(f"[preserve_decision_log] ERROR: source not found: {SRC}")
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest_dir = BACKUPS_ROOT / f"pre-op-{ts}" / "logs"
    dest_file = dest_dir / "decision_log.jsonl"

    print(f"[preserve_decision_log] source      : {SRC}")
    print(f"[preserve_decision_log] destination : {dest_file}")
    print(f"[preserve_decision_log] timestamp   : {ts} (UTC)")

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[preserve_decision_log] ERROR: could not create destination directory: {e}")
        return 3

    try:
        shutil.copy2(SRC, dest_file)
    except OSError as e:
        print(f"[preserve_decision_log] ERROR: copy failed: {e}")
        return 4

    src_size = SRC.stat().st_size
    dst_size = dest_file.stat().st_size
    print(f"[preserve_decision_log] OK  snapshot written  ({dst_size:,} bytes; source {src_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

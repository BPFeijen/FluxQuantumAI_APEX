"""
C:\FluxQuantumAI\live\feed_health.py

Feed health monitor for the Quantower/dxfeed microstructure stream.

Problem addressed (2026-04-08):
  After manual restarts for threshold adjustments, the Quantower L2 stream
  did NOT reconnect automatically. The system was running but processing no
  new price data. quantower_level2_api.py (port 8000) process was alive but
  the stream was stale. This was NOT a dxfeed issue -- it was a reconnect issue
  on our side following process teardown.

Usage in run_live.py:
  monitor = FeedHealthMonitor(micro_dir=MICRO_DIR)
  monitor.start()          # background thread, checks every 5 min
  # or:
  status = monitor.check() # on-demand single check
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("apex.feed_health")

MICRO_DIR        = Path(os.environ.get("ATS_DATA_L2_DIR", r"C:\data\level2\_gc_xcec"))
STALE_WARN_SEC   = 120    # warn if no new data for 2 min
STALE_DEAD_SEC   = 300    # FEED_DEAD if no new data for 5 min
CHECK_INTERVAL_S = 120    # check every 2 min
MAX_STALE_COUNTS = 3      # 3 consecutive STALE checks -> FEED_DEAD + halt gate


def _today_micro_path(micro_dir: Path) -> Path | None:
    """Return today's microstructure csv.gz path, or None if not found."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidates = [
        micro_dir / f"microstructure_{today_str}.csv.gz",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _file_age_seconds(path: Path) -> float:
    """Seconds since path was last modified."""
    try:
        return time.time() - os.path.getmtime(str(path))
    except OSError:
        return float("inf")


class FeedHealthMonitor:
    """
    Background thread that checks microstructure file freshness every
    CHECK_INTERVAL_S seconds.

    States:
      OK        -- file updated within STALE_WARN_SEC
      STALE     -- file not updated for STALE_WARN_SEC..STALE_DEAD_SEC
      FEED_DEAD -- file not updated for > STALE_DEAD_SEC (3x consecutive)

    On FEED_DEAD: logs CRITICAL + sets self.gate_enabled = False.
    gate_enabled is read by EventProcessor before each tick to suppress
    gate checks when the system is operating blind.
    """

    def __init__(self, micro_dir: Path = MICRO_DIR):
        self.micro_dir    = micro_dir
        self.gate_enabled = True     # set False when FEED_DEAD
        self._stale_count = 0
        self._thread: threading.Thread | None = None
        self._stop_event  = threading.Event()

    def check(self) -> dict:
        """
        Single synchronous health check.
        Returns {"status": "OK"|"STALE"|"FEED_DEAD"|"NO_FILE", "age_sec": float}.
        """
        path = _today_micro_path(self.micro_dir)
        if path is None:
            log.warning("FEED_HEALTH: today's microstructure file not found in %s", self.micro_dir)
            return {"status": "NO_FILE", "age_sec": float("inf"), "path": None}

        age = _file_age_seconds(path)

        if age < STALE_WARN_SEC:
            status = "OK"
        elif age < STALE_DEAD_SEC:
            status = "STALE"
        else:
            status = "FEED_DEAD"

        return {"status": status, "age_sec": round(age, 1), "path": str(path)}

    def _monitor_loop(self) -> None:
        """Background loop -- runs every CHECK_INTERVAL_S seconds."""
        log.info("FeedHealthMonitor started (check every %ds, stale=%ds, dead=%ds)",
                 CHECK_INTERVAL_S, STALE_WARN_SEC, STALE_DEAD_SEC)
        while not self._stop_event.is_set():
            result = self.check()
            status = result["status"]
            age    = result["age_sec"]

            if status == "OK":
                self._stale_count = 0
                if not self.gate_enabled:
                    log.info("FEED_HEALTH: feed recovered (age=%.0fs) -- gate re-enabled", age)
                    self.gate_enabled = True

            elif status in ("STALE", "NO_FILE"):
                self._stale_count += 1
                log.warning("FEED_HEALTH: %s (age=%.0fs, count=%d/%d)",
                            status, age, self._stale_count, MAX_STALE_COUNTS)

                if self._stale_count >= MAX_STALE_COUNTS:
                    if self.gate_enabled:
                        log.critical(
                            "FEED_DEAD: microstructure file not updated for %.0fs "
                            "(%d consecutive checks). "
                            "Gate checks SUSPENDED -- system will not trade blind. "
                            "Check Quantower L2 stream (port 8000). "
                            "Restart: python quantower_level2_api.py",
                            age, self._stale_count,
                        )
                        self.gate_enabled = False

            elif status == "FEED_DEAD":
                self._stale_count += 1
                if self.gate_enabled:
                    log.critical(
                        "FEED_DEAD: microstructure not updated for %.0fs. "
                        "Gate checks SUSPENDED. Check quantower_level2_api.py (port 8000).",
                        age,
                    )
                    self.gate_enabled = False
                else:
                    log.warning("FEED_HEALTH: still DEAD (age=%.0fs)", age)

            self._stop_event.wait(CHECK_INTERVAL_S)

    def start(self) -> None:
        """Start background monitoring thread (daemon)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="FeedHealthMonitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Singleton -- imported by run_live.py
# ---------------------------------------------------------------------------
_feed_monitor: FeedHealthMonitor | None = None


def get_feed_monitor() -> FeedHealthMonitor:
    global _feed_monitor
    if _feed_monitor is None:
        _feed_monitor = FeedHealthMonitor()
    return _feed_monitor


# ---------------------------------------------------------------------------
# CLI: python -m live.feed_health
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [feed_health] %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    m = FeedHealthMonitor()
    r = m.check()
    print(f"Status : {r['status']}")
    print(f"Age    : {r['age_sec']:.0f}s")
    print(f"File   : {r.get('path','?')}")

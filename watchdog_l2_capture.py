"""
watchdog_l2_capture.py — FluxQuantumAI L2 Capture Watchdog

Monitors every 60 seconds:
  - Quantower (Starter.exe) — restarts if down, then waits 30s for feed
  - quantower_level2_api (uvicorn, port 8000) — restarts if down
  - iceberg_receiver.py (port 8002) — restarts if down
  - Heartbeat: warns if no new L2 data file in 10 minutes

Health check logic for port-based services:
  Port listening = healthy.  Port NOT listening = DOWN, even if process exists.
  Hung processes (alive but port dead) are killed before restart to ensure
  clean recovery.  The old cmdline-fallback behaviour masked hung processes
  and prevented auto-restart (bug fixed 2026-04-10).

Logs to: C:\\FluxQuantumAI\\logs\\watchdog.log (daily rotation)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import socket

import psutil

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PYTHON = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
CAPTURE_DIR = Path(r"C:\FluxQuantumAI")
LOG_DIR = CAPTURE_DIR / "logs"
L2_DATA_DIR = Path(r"C:\data\level2\_gc_xcec")

QUANTOWER_EXE = r"C:\Quantower\TradingPlatform\v1.145.17\Starter.exe"
QUANTOWER_PROC_NAME = "Starter"          # psutil process name (no .exe)

HEARTBEAT_WARN_SECONDS = 600            # 10 minutes
QUANTOWER_STARTUP_WAIT = 30             # seconds to wait after starting Quantower
CHECK_INTERVAL = 60                     # seconds between watchdog loops

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_DIR.mkdir(parents=True, exist_ok=True)
_log_path = LOG_DIR / "watchdog.log"

_handler = logging.handlers.TimedRotatingFileHandler(
    _log_path, when="midnight", backupCount=30, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

log = logging.getLogger("watchdog")
log.setLevel(logging.INFO)
log.addHandler(_handler)
log.addHandler(_console)


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

def _is_quantower_running() -> bool:
    """True if any process named Starter or Starter.exe is running."""
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info["name"] or "").lower()
            if "starter" in name:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _is_port_listening(port: int) -> bool:
    """True if something is listening on the given TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_port_service_healthy(marker: str, port: int) -> bool:
    """
    Health check for port-based services (L2 API, iceberg receiver).

    Port listening is the SOLE criterion — process existence is irrelevant.
    If the port is not listening but a process with *marker* exists, that
    process is hung and is killed so the caller can start a fresh instance.

    This replaces the old _is_script_running fallback that masked hung
    processes and prevented auto-restart (bug 2026-04-10).
    """
    if _is_port_listening(port):
        return True
    # Port dead — kill any hung process bearing this marker
    _kill_hung_service(marker)
    return False


def _kill_hung_service(marker: str) -> None:
    """Kill every python process whose cmdline contains *marker*."""
    for proc in psutil.process_iter(["name", "cmdline", "pid"]):
        try:
            name = (proc.info["name"] or "").lower()
            if "python" not in name:
                continue
            cmdline = " ".join(proc.info["cmdline"] or [])
            if marker in cmdline:
                log.warning(
                    "Hung service detected — killing PID=%d (%s)", proc.pid, marker
                )
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _is_script_running(marker: str, port: int | None = None) -> bool:
    """True if any python process has *marker* in its cmdline.
    Used only for non-port services (legacy). For port-based services
    use _is_port_service_healthy() instead."""
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if "python" not in name:
                continue
            cmdline = " ".join(proc.info["cmdline"] or [])
            if marker in cmdline:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _start_detached(args: list[str], cwd: str | None = None, log_name: str = "service") -> None:
    """Start a process fully detached from this watchdog (survives watchdog restart).

    stdout/stderr are redirected to a log file so that the child process has valid
    handles even when the watchdog runs as SYSTEM in session 0 (no console).
    """
    out_path = LOG_DIR / f"{log_name}_stdout.log"
    err_path = LOG_DIR / f"{log_name}_stderr.log"
    kwargs: dict = dict(
        args=args,
        cwd=cwd or str(CAPTURE_DIR),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        stdout=open(out_path, "a", encoding="utf-8"),
        stderr=open(err_path, "a", encoding="utf-8"),
        stdin=subprocess.DEVNULL,
    )
    subprocess.Popen(**kwargs)


# ---------------------------------------------------------------------------
# Restart helpers
# ---------------------------------------------------------------------------

def restart_quantower() -> None:
    log.warning("Quantower DOWN — restarting %s", QUANTOWER_EXE)
    _start_detached([QUANTOWER_EXE])
    log.info("Quantower started — waiting %ds for feed to connect", QUANTOWER_STARTUP_WAIT)
    time.sleep(QUANTOWER_STARTUP_WAIT)


def restart_l2_api() -> None:
    log.warning("L2 capture (quantower_level2_api) DOWN — restarting")
    _start_detached(
        [PYTHON, "-m", "uvicorn", "quantower_level2_api:app",
         "--host", "0.0.0.0", "--port", "8000",
         "--log-level", "info"],
        cwd=str(CAPTURE_DIR),
        log_name="quantower_level2_api",   # → logs/quantower_level2_api_stdout.log
    )
    log.info("L2 capture restarted — stdout → logs/quantower_level2_api_stdout.log")


def restart_iceberg() -> None:
    log.warning("Iceberg receiver (iceberg_receiver.py) DOWN — restarting")
    _start_detached(
        [PYTHON, str(CAPTURE_DIR / "iceberg_receiver.py")],
        cwd=str(CAPTURE_DIR),
        log_name="iceberg",
    )


# ---------------------------------------------------------------------------
# Heartbeat check
# ---------------------------------------------------------------------------

def check_heartbeat() -> float:
    """Return seconds since the most-recently-modified file in L2_DATA_DIR."""
    try:
        files = list(L2_DATA_DIR.glob("*"))
        if not files:
            return float("inf")
        newest_mtime = max(f.stat().st_mtime for f in files if f.is_file())
        return time.time() - newest_mtime
    except Exception as exc:
        log.error("Heartbeat check failed: %s", exc)
        return float("inf")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    log.info("=" * 60)
    log.info("FluxQuantumAI L2 Watchdog starting (PID=%d)", os.getpid())
    log.info("Quantower : %s", QUANTOWER_EXE)
    log.info("Python    : %s", PYTHON)
    log.info("Data dir  : %s", L2_DATA_DIR)
    log.info("=" * 60)

    while True:
        try:
            qt_ok = _is_quantower_running()
            l2_ok = _is_port_service_healthy("quantower_level2_api", port=8000)
            ice_ok = _is_port_service_healthy("iceberg_receiver", port=8002)

            # --- Quantower ---
            if not qt_ok:
                restart_quantower()
                qt_ok = _is_quantower_running()  # re-check after restart

            # --- L2 capture ---
            if not l2_ok:
                restart_l2_api()

            # --- Iceberg receiver ---
            if not ice_ok:
                restart_iceberg()

            # --- Heartbeat ---
            data_age = check_heartbeat()
            if data_age > HEARTBEAT_WARN_SECONDS:
                log.warning(
                    "WARNING: No new L2 data in %.0f minutes — capture may be stalled",
                    data_age / 60,
                )

            # --- Status line ---
            age_str = f"{data_age:.0f}s ago" if data_age < float("inf") else "unknown"
            log.info(
                "OK — Quantower: %s, L2: %s, Iceberg: %s, Last data: %s",
                "running" if qt_ok else "DOWN",
                "running" if l2_ok else "DOWN",
                "running" if ice_ok else "DOWN",
                age_str,
            )

        except Exception as exc:
            log.error("Watchdog loop error: %s", exc, exc_info=True)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()

#!/usr/bin/env python3
"""
C:\FluxQuantumAI\live\tick_breakout_monitor.py

# ============================================================
# TickBreakoutMonitor -- Real-time breakout & JAC detector
# ============================================================

Runs as a background daemon thread inside EventProcessor.

Monitors live GC mid-price (from EventProcessor._metrics["gc_mid"])
against the current M30 contraction zone (box_high_gc, box_low_gc).

When price breaks out AND sustains for BREAKOUT_CONFIRM_S seconds:
  - Immediately updates EventProcessor.liq_top_gc / liq_bot_gc
  - Calls _refresh_offset() so MT5-space levels update too
  - Logs TICK_BREAKOUT event

When JAC is confirmed (price returns inside box, sustains JAC_CONFIRM_S):
  - Logs TICK_JAC_CONFIRMED
  - Resets state, forces parquet re-read on next cycle

False-positive protection:
  - Requires BREAKOUT_CONFIRM_S (default 30s) of sustained price beyond boundary
    -> filters wick spikes (typically <5s) and news spikes (5-15s)
  - Requires JAC_CONFIRM_S (default 15s) of sustained price inside box for JAC

Box boundaries are refreshed from gc_m30_boxes.parquet every PARQUET_REFRESH_S (60s).
When the parquet detects a new box_id, state is reset automatically.

ADR-001 compliance: box detection (contraction) still uses M30 bars via m30_updater.
This module only accelerates BREAKOUT and JAC phase detection.

State machine:
  CONTRACTION
      v gc_mid > box_high (or < box_low)
  CANDIDATE_UP / CANDIDATE_DN
      v sustained BREAKOUT_CONFIRM_S
  BREAKOUT_UP / BREAKOUT_DN   -> updates liq_top/liq_bot in processor
      v gc_mid < box_low (UP) or gc_mid > box_high (DN)
  JAC_CANDIDATE
      v sustained JAC_CONFIRM_S
  CONTRACTION (reset, force parquet re-read)
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger("apex.tick_breakout")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
M30_BOXES_PATH = Path(r"C:\data\processed\gc_m30_boxes.parquet")

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------
BREAKOUT_CONFIRM_S = 30    # seconds of sustained breakout before declaring confirmed
JAC_CONFIRM_S      = 15    # seconds of sustained price inside box for JAC confirmation
CHECK_INTERVAL_S   = 1.0   # polling interval for gc_mid
PARQUET_REFRESH_S  = 60.0  # re-read parquet this often for updated box boundaries


# ---------------------------------------------------------------------------
# TickBreakoutMonitor
# ---------------------------------------------------------------------------

class TickBreakoutMonitor:
    """
    Real-time breakout and JAC detector for M30 boxes.

    Parameters
    ----------
    processor : EventProcessor
        Reference to the live EventProcessor -- levels are updated directly on it.
    """

    def __init__(self, processor) -> None:
        self._proc = processor
        self._lock = threading.Lock()

        # Current M30 contraction zone (from parquet -- original contraction boundaries)
        self._box_high_gc: float | None = None
        self._box_low_gc:  float | None = None
        self._box_id:      int          = 0

        # State machine
        self._state:            str   = "CONTRACTION"
        self._candidate_since:  float = 0.0   # monotonic ts when candidate phase started
        self._jac_since:        float = 0.0   # monotonic ts when JAC candidate started
        self._breakout_dir:     str | None = None   # "UP" or "DN"
        self._breakout_extreme: float = 0.0   # max (UP) or min (DN) price during candidate

        # Parquet refresh
        self._last_parquet_ts:  float = 0.0

    # ------------------------------------------------------------------
    # Parquet refresh
    # ------------------------------------------------------------------

    def _refresh_from_parquet(self) -> None:
        """Re-read M30 parquet and update box boundaries. Reset state on new box_id."""
        try:
            df = pd.read_parquet(M30_BOXES_PATH)
            valid = df[
                df["m30_box_high"].notna() &
                df["m30_box_low"].notna()  &
                (df["m30_box_id"] > 0)
            ]
            if valid.empty:
                return

            row     = valid.iloc[-1]
            new_id  = int(row["m30_box_id"])
            new_hi  = float(row["m30_box_high"])
            new_lo  = float(row["m30_box_low"])

            with self._lock:
                if new_id != self._box_id:
                    # New box -> reset state machine
                    log.info(
                        "TickBreakout: new box detected (id %d -> %d)"
                        "  box_high=%.2f  box_low=%.2f -- state reset",
                        self._box_id, new_id, new_hi, new_lo,
                    )
                    self._box_id        = new_id
                    self._box_high_gc   = new_hi
                    self._box_low_gc    = new_lo
                    self._state         = "CONTRACTION"
                    self._breakout_dir  = None
                    self._candidate_since = 0.0
                    self._jac_since     = 0.0
                else:
                    # Same box -- just update boundaries (m30_updater may have refined them)
                    self._box_high_gc = new_hi
                    self._box_low_gc  = new_lo

        except Exception as e:
            log.warning("TickBreakout: parquet refresh failed: %s", e)

    # ------------------------------------------------------------------
    # Level injection into EventProcessor
    # ------------------------------------------------------------------

    def _inject_levels(self, liq_top_gc: float, liq_bot_gc: float, reason: str) -> None:
        """
        Update EventProcessor liq_top_gc / liq_bot_gc and MT5-space equivalents.
        Thread-safe: acquires processor._lock.
        """
        proc = self._proc
        fmv_gc = round((liq_top_gc + liq_bot_gc) / 2.0, 2)

        with proc._lock:
            proc.liq_top_gc = liq_top_gc
            proc.liq_bot_gc = liq_bot_gc
            proc.liq_top    = round(liq_top_gc - proc._gc_xauusd_offset, 2)
            proc.liq_bot    = round(liq_bot_gc - proc._gc_xauusd_offset, 2)

        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.info(
            "TICK_BREAKOUT [%s]: liq_top_gc=%.2f  liq_bot_gc=%.2f  fmv_gc=%.2f"
            "  MT5: liq_top=%.2f  liq_bot=%.2f",
            reason, liq_top_gc, liq_bot_gc, fmv_gc, proc.liq_top, proc.liq_bot,
        )
        print(
            f"[{ts}] TICK_BREAKOUT [{reason}]:"
            f"  GC liq_top={liq_top_gc:.2f}  liq_bot={liq_bot_gc:.2f}"
            f"  MT5 liq_top={proc.liq_top:.2f}  liq_bot={proc.liq_bot:.2f}"
        )

    # ------------------------------------------------------------------
    # State machine step
    # ------------------------------------------------------------------

    def _step(self, gc_mid: float, now: float) -> None:
        """Single state-machine step for the given gc_mid price."""
        with self._lock:
            state            = self._state
            box_high         = self._box_high_gc
            box_low          = self._box_low_gc
            candidate_since  = self._candidate_since
            jac_since        = self._jac_since
            breakout_dir     = self._breakout_dir
            breakout_extreme = self._breakout_extreme

        if box_high is None or box_low is None:
            return

        # -- CONTRACTION ------------------------------------------------------
        if state == "CONTRACTION":
            if gc_mid > box_high:
                with self._lock:
                    self._state           = "CANDIDATE_UP"
                    self._candidate_since = now
                    self._breakout_extreme = gc_mid
                    self._breakout_dir    = "UP"
                log.info(
                    "TickBreakout: CANDIDATE_UP  gc_mid=%.2f  box_high=%.2f  (confirm in %ds)",
                    gc_mid, box_high, BREAKOUT_CONFIRM_S,
                )
            elif gc_mid < box_low:
                with self._lock:
                    self._state           = "CANDIDATE_DN"
                    self._candidate_since = now
                    self._breakout_extreme = gc_mid
                    self._breakout_dir    = "DN"
                log.info(
                    "TickBreakout: CANDIDATE_DN  gc_mid=%.2f  box_low=%.2f  (confirm in %ds)",
                    gc_mid, box_low, BREAKOUT_CONFIRM_S,
                )

        # -- CANDIDATE_UP -----------------------------------------------------
        elif state == "CANDIDATE_UP":
            if gc_mid <= box_high:
                # Price returned inside -- wick, cancel
                with self._lock:
                    self._state           = "CONTRACTION"
                    self._candidate_since = 0.0
                log.info(
                    "TickBreakout: CANDIDATE_UP cancelled (wick)  gc_mid=%.2f  box_high=%.2f",
                    gc_mid, box_high,
                )
            else:
                # Still above -- track extreme and check confirm window
                with self._lock:
                    self._breakout_extreme = max(self._breakout_extreme, gc_mid)
                    extreme = self._breakout_extreme

                if now - candidate_since >= BREAKOUT_CONFIRM_S:
                    new_liq_top = round(extreme, 2)
                    new_liq_bot = round(box_low,  2)
                    self._inject_levels(new_liq_top, new_liq_bot, "BREAKOUT_UP")
                    with self._lock:
                        self._state = "BREAKOUT_UP"

        # -- CANDIDATE_DN -----------------------------------------------------
        elif state == "CANDIDATE_DN":
            if gc_mid >= box_low:
                with self._lock:
                    self._state           = "CONTRACTION"
                    self._candidate_since = 0.0
                log.info(
                    "TickBreakout: CANDIDATE_DN cancelled (wick)  gc_mid=%.2f  box_low=%.2f",
                    gc_mid, box_low,
                )
            else:
                with self._lock:
                    self._breakout_extreme = min(self._breakout_extreme, gc_mid)
                    extreme = self._breakout_extreme

                if now - candidate_since >= BREAKOUT_CONFIRM_S:
                    new_liq_top = round(box_high, 2)
                    new_liq_bot = round(extreme,  2)
                    self._inject_levels(new_liq_top, new_liq_bot, "BREAKOUT_DN")
                    with self._lock:
                        self._state = "BREAKOUT_DN"

        # -- BREAKOUT_UP -- waiting for JAC (price returns below box_low) ------
        elif state == "BREAKOUT_UP":
            if gc_mid < box_low:
                with self._lock:
                    self._state     = "JAC_CANDIDATE"
                    self._jac_since = now
                log.info(
                    "TickBreakout: JAC_CANDIDATE (UP)  gc_mid=%.2f  box_low=%.2f  (confirm in %ds)",
                    gc_mid, box_low, JAC_CONFIRM_S,
                )

        # -- BREAKOUT_DN -- waiting for JAC (price returns above box_high) -----
        elif state == "BREAKOUT_DN":
            if gc_mid > box_high:
                with self._lock:
                    self._state     = "JAC_CANDIDATE"
                    self._jac_since = now
                log.info(
                    "TickBreakout: JAC_CANDIDATE (DN)  gc_mid=%.2f  box_high=%.2f  (confirm in %ds)",
                    gc_mid, box_high, JAC_CONFIRM_S,
                )

        # -- JAC_CANDIDATE ----------------------------------------------------
        elif state == "JAC_CANDIDATE":
            # Cancel if price reversed before confirmation
            if breakout_dir == "UP" and gc_mid >= box_low:
                with self._lock:
                    self._state     = "BREAKOUT_UP"
                    self._jac_since = 0.0
                log.info("TickBreakout: JAC_CANDIDATE cancelled (price back up)")

            elif breakout_dir == "DN" and gc_mid <= box_high:
                with self._lock:
                    self._state     = "BREAKOUT_DN"
                    self._jac_since = 0.0
                log.info("TickBreakout: JAC_CANDIDATE cancelled (price back down)")

            elif now - jac_since >= JAC_CONFIRM_S:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                log.info(
                    "TICK_JAC_CONFIRMED: dir=%s  gc_mid=%.2f  box_high=%.2f  box_low=%.2f",
                    breakout_dir, gc_mid, box_high, box_low,
                )
                print(
                    f"[{ts}] TICK_JAC_CONFIRMED: dir={breakout_dir}"
                    f"  gc_mid={gc_mid:.2f}  (m30_box_confirmed expected within 60s)"
                )
                # Reset -- next parquet refresh will bring the confirmed box
                with self._lock:
                    self._state          = "CONTRACTION"
                    self._breakout_dir   = None
                    self._jac_since      = 0.0
                    self._candidate_since = 0.0
                    self._last_parquet_ts = 0.0   # force immediate parquet re-read

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main monitoring loop. Runs as a daemon thread."""
        log.info(
            "TickBreakoutMonitor started  "
            "(confirm=%ds  jac=%ds  poll=%.1fs)",
            BREAKOUT_CONFIRM_S, JAC_CONFIRM_S, CHECK_INTERVAL_S,
        )

        while True:
            try:
                now = time.monotonic()

                # Refresh box boundaries from parquet
                if now - self._last_parquet_ts >= PARQUET_REFRESH_S:
                    self._refresh_from_parquet()
                    self._last_parquet_ts = now

                gc_mid = float(self._proc._metrics.get("gc_mid", 0.0))
                if gc_mid > 0:
                    self._step(gc_mid, now)

            except Exception as e:
                log.warning("TickBreakoutMonitor loop error: %s", e)

            time.sleep(CHECK_INTERVAL_S)

    # ------------------------------------------------------------------
    # Public: start as daemon thread
    # ------------------------------------------------------------------

    def start(self) -> threading.Thread:
        """Start the monitor as a background daemon thread."""
        t = threading.Thread(target=self.run, name="tick_breakout", daemon=True)
        t.start()
        return t

    # ------------------------------------------------------------------
    # Public: status for logging/dashboard
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Return current state snapshot for diagnostics."""
        with self._lock:
            return {
                "state":          self._state,
                "box_id":         self._box_id,
                "box_high_gc":    self._box_high_gc,
                "box_low_gc":     self._box_low_gc,
                "breakout_dir":   self._breakout_dir,
                "breakout_extreme": self._breakout_extreme,
            }

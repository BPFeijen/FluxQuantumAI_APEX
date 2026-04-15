#!/usr/bin/env python3
"""
C:\FluxQuantumAI\live\price_speed.py
ICT Price Speed (Displacement) tracker for GC/XAUUSD.

Displacement = institutional order flow that moves price with velocity,
confirming absorption of opposing liquidity at a structural level.

After a level touch, if price moves away at >= threshold pts/s,
it is classified as DISPLACEMENT (not noise / false breakout).

Usage:
  tracker = PriceSpeedTracker(threshold_pts_per_sec=0.8)

  # In tick loop (every ~1s):
  tracker.add_tick(current_price)

  # In gate trigger (after level touch detected):
  result = tracker.compute_speed(window_s=5.0)
  print(result.label)   # "SPD:1.42pt/s[DISP^]"

Threshold calibration note:
  GC ATR ≈ 16 pts / 30 min -> 16/1800s ≈ 0.009 pt/s normal drift
  Institutional displacement: 5-15 pts in 5s = 1.0-3.0 pt/s
  Conservative threshold: 0.8 pt/s (flags moves > 4 pts in 5s)
  Review after 50+ live trades when gate logs are available.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Default calibration constants
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_PTS_PER_SEC = 0.8   # 4 pts in 5s = conservative displacement
DEFAULT_WINDOW_S              = 5.0   # default measurement window
TICK_BUFFER_SIZE              = 300   # ~5 min at 1 tick/s


@dataclass
class DisplacementResult:
    speed_pts_per_sec: float     # |price_change| / elapsed_s
    direction: str               # "bullish" | "bearish" | "flat"
    is_displacement: bool        # True when speed >= threshold
    elapsed_s: float             # actual window measured
    price_change_pts: float      # latest_price - oldest_price in window
    threshold: float             # threshold used for classification

    @property
    def label(self) -> str:
        """
        Compact gate telemetry label, e.g.:
          "SPD:1.42pt/s[DISP^]"   displacement confirmed
          "SPD:0.31pt/s[slow^]"   below threshold
          "SPD:N/A"                not enough data
        """
        if self.elapsed_s < 0.5:
            return "SPD:N/A"
        arrow = "^" if self.direction == "bullish" else ("v" if self.direction == "bearish" else "->")
        flag  = "DISP" if self.is_displacement else "slow"
        return f"SPD:{self.speed_pts_per_sec:.2f}pt/s[{flag}{arrow}]"


class PriceSpeedTracker:
    """
    Tracks price ticks and computes displacement speed for gate confirmation.
    Thread-safe for single writer + single reader (Python GIL protects deque ops).

    Parameters
    ----------
    threshold_pts_per_sec : float
        Speed threshold for classifying a move as institutional displacement.
    maxlen : int
        Maximum tick buffer size (auto-prunes oldest ticks).
    """

    def __init__(
        self,
        threshold_pts_per_sec: float = DEFAULT_THRESHOLD_PTS_PER_SEC,
        maxlen: int = TICK_BUFFER_SIZE,
    ):
        self._buf: deque[tuple[float, float]] = deque(maxlen=maxlen)
        self.threshold = threshold_pts_per_sec

    def add_tick(self, price: float) -> None:
        """Add a price tick. Call every ~1s from the tick monitoring loop."""
        if price and price > 0:
            self._buf.append((time.monotonic(), price))

    def compute_speed(self, window_s: float = DEFAULT_WINDOW_S) -> DisplacementResult:
        """
        Compute price speed (pts/sec) over the last window_s seconds.

        Returns DisplacementResult with speed, direction, and displacement flag.
        Returns zero-speed result if buffer has < 2 ticks in the window.
        """
        _empty = DisplacementResult(
            speed_pts_per_sec=0.0,
            direction="flat",
            is_displacement=False,
            elapsed_s=0.0,
            price_change_pts=0.0,
            threshold=self.threshold,
        )

        if len(self._buf) < 2:
            return _empty

        now_ts    = time.monotonic()
        cutoff_ts = now_ts - window_s

        # Collect all ticks within the window
        window: list[tuple[float, float]] = [
            (ts, px) for ts, px in self._buf if ts >= cutoff_ts
        ]

        if len(window) < 2:
            return _empty

        oldest_ts, oldest_px = window[0]
        latest_ts, latest_px = window[-1]
        elapsed_s   = latest_ts - oldest_ts

        if elapsed_s < 0.05:
            return _empty

        price_change = latest_px - oldest_px
        speed        = abs(price_change) / elapsed_s
        direction    = (
            "bullish" if price_change > 0.1
            else ("bearish" if price_change < -0.1 else "flat")
        )

        return DisplacementResult(
            speed_pts_per_sec=round(speed, 3),
            direction=direction,
            is_displacement=(speed >= self.threshold),
            elapsed_s=round(elapsed_s, 2),
            price_change_pts=round(price_change, 2),
            threshold=self.threshold,
        )

    def displacement_at_direction(
        self, expected_direction: str, window_s: float = DEFAULT_WINDOW_S
    ) -> bool:
        """
        True if displacement is occurring in the expected direction.
        Used to confirm gate direction after level touch.
          expected_direction: "bullish" (for LONG) or "bearish" (for SHORT)
        """
        r = self.compute_speed(window_s)
        return r.is_displacement and r.direction == expected_direction

    def label(self, window_s: float = DEFAULT_WINDOW_S) -> str:
        """Shorthand: compute and return the compact telemetry label."""
        return self.compute_speed(window_s).label

    def reset(self) -> None:
        """Clear the tick buffer (e.g. after a position close)."""
        self._buf.clear()

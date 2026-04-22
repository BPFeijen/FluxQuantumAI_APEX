"""
Regime Detectors — TREND-A and TREND-B
=======================================

Implements the two regime preconditions that features F1, F2, F3, F5 depend on
for LOGIC-C composition scoring.

- TREND-A: close-over-close monotonic in same direction for n_bars+ consecutive
  CLOSED bars. Literal Wyckoff "sign of thrust" (Wyckoff §1.6: "minimum of
  three pushes in the same direction").

- TREND-B: system-confirmed structural state. True when the most recent closed
  M30 bar has both m30_box_confirmed AND at_struct_level True. Direction is
  derived from m30_bias.

Both detectors are READ-ONLY observers. They do NOT modify the market_state
they receive and have zero decision impact. Consumers (IMPL-2 features) read
the result tuples from `self._regime_state` on the MarketEventProcessor.

State contract (populated by event_processor.refresh_macro_context):

    self._regime_state = {
        "trend_a": (is_active: bool, direction: str),
        "trend_b": (is_active: bool, direction: str),
    }

    index [0] = is_active  (bool)
    index [1] = direction  (str: "LONG" | "SHORT" | "")

References:
- T1-X1-INTERACTIONS (2026-04-22): TREND-A and TREND-B validated as
  preconditions for 7-feature LOGIC-C composition score (winning variant,
  expectancy +0.1535 vs baseline +0.0996).
- Wyckoff §1.6: sign-of-thrust requires minimum three consecutive same-direction
  closes on confirmed bars.

Task: T1-X1-IMPL-1 (2026-04-22)
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("apex.regime_detectors")

__all__ = ["detect_trend_a", "detect_trend_b"]


def _get_close(bar: Any) -> float | None:
    """Extract close from a bar (dict or object). Returns None if unavailable."""
    if bar is None:
        return None
    if isinstance(bar, dict):
        val = bar.get("close")
    else:
        val = getattr(bar, "close", None)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def detect_trend_a(bar_history: list, n_bars: int = 3) -> tuple[bool, str]:
    """
    Detect TREND-A: n_bars successive bars closing in the same direction.

    Per Wyckoff 2.0 §1.6 (Villahermosa, 2021):
        "The Shortening Of the Thrust pattern can also be seen in individual
         bars as well as in movements. In this case, one would observe how
         successive bars make less and less progress."

    For algorithmic bar-by-bar scanning (Operationalization B), "n successive
    bars" requires n bars with monotonic closes (= n-1 directional steps
    between them). Op A ("minimum of three pushes") applies to movements
    between visual extrema, which this detector does NOT perform.

    Default n_bars=3 per literature minimum threshold.

    Args:
        bar_history: list of M30 bars, most recent LAST. Each bar may be a
            dict or object with 'close' attribute/key. Should contain CLOSED
            bars only (filter by m30_box_confirmed upstream; Q5 decision
            2026-04-22).
        n_bars: minimum successive bars to confirm trend (default 3).

    Returns:
        (is_trend, direction):
            is_trend: True if the last n_bars closes are strictly monotonic.
            direction: "LONG" if monotonic up, "SHORT" if monotonic down,
                "" otherwise.

    Edge cases:
        - bar_history None, empty, or shorter than n_bars: (False, "")
        - Equal consecutive closes: breaks monotonicity -> (False, "")
        - Missing 'close' on any bar: (False, "")
    """
    if not bar_history or len(bar_history) < n_bars:
        return (False, "")

    closes: list[float] = []
    for bar in bar_history[-n_bars:]:
        c = _get_close(bar)
        if c is None:
            return (False, "")
        closes.append(c)

    all_up = all(closes[i + 1] > closes[i] for i in range(len(closes) - 1))
    if all_up:
        return (True, "LONG")

    all_down = all(closes[i + 1] < closes[i] for i in range(len(closes) - 1))
    if all_down:
        return (True, "SHORT")

    return (False, "")


def detect_trend_b(market_state: Any) -> tuple[bool, str]:
    """
    Detect TREND-B: system-derived structural regime.

    True when the latest closed M30 bar has m30_box_confirmed AND at_struct_level.
    Direction is derived from m30_bias.

    Args:
        market_state: object exposing:
            - m30_box_confirmed (bool)
            - at_struct_level (bool)
            - m30_bias (str: "bullish" | "bearish" | "unknown")

    Returns:
        (is_trend, direction):
            is_trend: True only if both m30_box_confirmed AND at_struct_level.
            direction: "LONG" if m30_bias=="bullish", "SHORT" if =="bearish",
                "" otherwise.

    Edge cases:
        - Missing attributes: (False, "")
        - Only one of the two flags True: (False, "")
        - Both True but m30_bias=="unknown": (True, "") — structural alignment
          present but directionless; consumer must treat as neutral.
    """
    try:
        box_confirmed = bool(getattr(market_state, "m30_box_confirmed", False))
        at_level = bool(getattr(market_state, "at_struct_level", False))
    except Exception:  # noqa: BLE001 -- defensive against unexpected state shapes
        return (False, "")

    if not (box_confirmed and at_level):
        return (False, "")

    bias_raw = getattr(market_state, "m30_bias", None)
    bias = str(bias_raw).lower() if bias_raw is not None else ""

    if bias == "bullish":
        return (True, "LONG")
    if bias == "bearish":
        return (True, "SHORT")

    return (True, "")

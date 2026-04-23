"""
live/ats_trend_line.py — ATS Trend Line implementation (Part A)

═════════════════════════════════════════════════════════════════════════════
Textual citations (per Rule 15 G-METHODOLOGY-FIRST-WORKFLOW)
═════════════════════════════════════════════════════════════════════════════

[Citation 1 — primary directional tool]
ATS Implementation Strategic Plan §4.0 Foundational Pillar II:
    "The primary tool for this is the ATS Trend Line. All trading decisions
    on the lower timeframe must align with the direction indicated on this
    chart. No exceptions."

[Citation 2 — no user-facing settings]
Everything to Know About the ATS Trend Line:
    "the way that the indicator is designed it has no settings ... the idea
    is that you don't adjust it the idea is that you understand it and get
    familiar with how it works in its form."

[Citation 3 — detection mechanics (3-candle + group)]
Everything to Know About the ATS Trend Line:
    "the way that the indicator is programmed is it looks for inefficiencies
    what we call either three candle inefficiencies or group inefficiencies
    which is essentially a gapping action between a candle and then we have
    a traveling candle and then another group or single candle that has
    separated."

[Citation 4 — center-point tracking]
Everything to Know About the ATS Trend Line:
    "what we're looking for is we're looking for series of inefficiencies
    which is essentially consolidation. The market travels and then
    consolidates again. Every time it travels between consolidations, we
    want to mark that center point."

[Citation 5 — dot emission on new move]
Everything to Know About the ATS Trend Line:
    "when it identifies that first instance of inefficiency that's when you
    get a dot and the reason we put it there is because that really
    represents the banks and the market makers kicking off a move
    potentially so that shows kind of their intent of a potential direction
    change."

[Citation 6 — trend change on trade-through]
Everything to Know About the ATS Trend Line:
    "at the very top of this move right here you can see we trade through
    the inefficiency level which is ats trend that shows that it's
    potentially over."

[Citation 7 — non-repainting, static line]
Everything to Know About the ATS Trend Line:
    "For something that doesn't repaint and something that has no settings,
    to be able to show you accuracy in trend on a static line like this is
    absolutely incredible."

Cross-references:
    - Síntese internalizada §6 (ATS Trend Line — O oracle direccional)
    - ATS Implementation Strategic Plan §4

═════════════════════════════════════════════════════════════════════════════
Module contract
═════════════════════════════════════════════════════════════════════════════
- PURE FUNCTION: same inputs → same outputs, deterministic
- STATELESS: no module-level mutable state, no caches
- NON-REPAINTING: `compute_trend_line_state(bars, as_of=t)` never changes
  with bars added after `t` (Citation 7)
- NO USER SETTINGS: all thresholds are literature-derived constants
  (Citation 2). Group size is fixed at 2 (smallest symmetric extension of
  3-candle FVG)
- NO I/O: no parquet reads, no network, no model loading
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


# ============================================================================
# Internal constants (NOT user-facing — Citation 2)
# ============================================================================
_GROUP_SIZE = 2   # symmetric extension of 3-candle FVG; larger groups are
                  # captured by cascading 3-candle FVGs at finer granularity


# ============================================================================
# Output structures
# ============================================================================
@dataclass(frozen=True)
class Inefficiency:
    """A single detected inefficiency (3-candle or group)."""
    ts: pd.Timestamp        # timestamp of the right-side bar
    kind: str               # "3_candle_bullish" | "3_candle_bearish" | "group_bullish" | "group_bearish"
    direction: int          # +1 bullish / -1 bearish
    center: float           # midpoint of the gap (Citation 4)
    gap_low: float          # lower edge of the gap
    gap_high: float         # upper edge of the gap


@dataclass(frozen=True)
class TrendDot:
    """First inefficiency of a new trend direction (Citation 5)."""
    ts: pd.Timestamp
    direction: int          # +1 bullish / -1 bearish
    level: float            # center of the inefficiency at the dot


@dataclass(frozen=True)
class ATSTrendLineState:
    """Full ATS Trend Line state at a given point in time."""
    direction: int                              # +1 / -1 / 0 (neutral)
    last_dot: Optional[TrendDot]                # most recent direction-change dot
    last_inefficiency: Optional[Inefficiency]   # most recent inefficiency
    line_level: Optional[float]                 # current line level = last center (Citation 4)
    n_inefficiencies: int                       # total inefficiencies up to as_of
    price_through_line: bool                    # close has traded through (Citation 6)


# ============================================================================
# Detection — 3-candle inefficiency (Citation 3 part 1)
# ============================================================================
def detect_3_candle_fvg(bars: pd.DataFrame) -> list[Inefficiency]:
    """
    Detect 3-candle fair value gaps.

    Bullish FVG: bar[i-1].high < bar[i+1].low
        (gap between N-1 high and N+1 low, not filled by traveling bar N)
    Bearish FVG: bar[i-1].low > bar[i+1].high

    Strict inequality (no tolerance epsilon — Citation 2).

    Args:
        bars: DataFrame with 'high' and 'low' columns, DatetimeIndex.

    Returns:
        list of Inefficiency objects (may be empty).
    """
    if len(bars) < 3:
        return []
    idx = bars.index
    h = bars["high"].values
    low = bars["low"].values
    out: list[Inefficiency] = []
    for i in range(1, len(bars) - 1):
        h_prev, l_prev = h[i - 1], low[i - 1]
        h_next, l_next = h[i + 1], low[i + 1]
        if h_prev < l_next:
            out.append(Inefficiency(
                ts=idx[i + 1],
                kind="3_candle_bullish",
                direction=+1,
                center=(h_prev + l_next) / 2.0,
                gap_low=float(h_prev),
                gap_high=float(l_next),
            ))
        elif l_prev > h_next:
            out.append(Inefficiency(
                ts=idx[i + 1],
                kind="3_candle_bearish",
                direction=-1,
                center=(l_prev + h_next) / 2.0,
                gap_low=float(h_next),
                gap_high=float(l_prev),
            ))
    return out


# ============================================================================
# Detection — group inefficiency (Citation 3 part 2)
# ============================================================================
def detect_group_inefficiencies(bars: pd.DataFrame) -> list[Inefficiency]:
    """
    Detect group inefficiencies: gap between a group of bars (size _GROUP_SIZE)
    and a subsequent group of bars, with a single traveling bar in between.

    For _GROUP_SIZE=2 this is a 5-candle symmetric extension of the 3-candle
    FVG, capturing scenarios where a single traveling candle may nominally
    "fill" the gap but the surrounding context has clear separation.

    Bullish group FVG: max(group1.high) < min(group2.low)
    Bearish group FVG: min(group1.low) > max(group2.high)

    Group size fixed at 2 per Citation 2 (no user settings).
    Larger group separations naturally emerge as cascading 3-candle FVGs.
    """
    g = _GROUP_SIZE
    if len(bars) < 2 * g + 1:
        return []
    idx = bars.index
    h = bars["high"].values
    low = bars["low"].values
    out: list[Inefficiency] = []
    for i in range(g, len(bars) - g):
        g1_hi = h[i - g:i].max()
        g1_lo = low[i - g:i].min()
        g2_hi = h[i + 1:i + 1 + g].max()
        g2_lo = low[i + 1:i + 1 + g].min()
        if g1_hi < g2_lo:
            out.append(Inefficiency(
                ts=idx[i + 1],
                kind="group_bullish",
                direction=+1,
                center=(g1_hi + g2_lo) / 2.0,
                gap_low=float(g1_hi),
                gap_high=float(g2_lo),
            ))
        elif g1_lo > g2_hi:
            out.append(Inefficiency(
                ts=idx[i + 1],
                kind="group_bearish",
                direction=-1,
                center=(g1_lo + g2_hi) / 2.0,
                gap_low=float(g2_hi),
                gap_high=float(g1_lo),
            ))
    return out


# ============================================================================
# State computation (Citations 4, 5, 6, 7)
# ============================================================================
def compute_trend_line_state(
    bars: pd.DataFrame,
    as_of: Optional[pd.Timestamp] = None,
) -> ATSTrendLineState:
    """
    Compute ATS Trend Line state at `as_of` (default: last bar in input).

    Mechanics:
      1. Filter bars to those on/before `as_of`
      2. Detect all 3-candle + group inefficiencies (Citation 3)
      3. Walk chronologically:
         - Track current direction from most recent inefficiency (Citation 4)
         - Emit a new `TrendDot` whenever direction flips (Citation 5)
      4. Line level = center of most recent inefficiency (Citation 4)
      5. price_through_line: last close has traded opposite to current
         direction relative to line_level (Citation 6 trend-over signal)

    Non-repainting guarantee (Citation 7): this function uses only bars with
    timestamp ≤ `as_of`. Adding bars after `as_of` cannot change the returned
    state for that `as_of`.
    """
    empty = ATSTrendLineState(
        direction=0,
        last_dot=None,
        last_inefficiency=None,
        line_level=None,
        n_inefficiencies=0,
        price_through_line=False,
    )
    if bars is None or bars.empty:
        return empty

    bars_view = bars if as_of is None else bars[bars.index <= as_of]
    if len(bars_view) < 3:
        return empty

    inefficiencies = (
        detect_3_candle_fvg(bars_view) + detect_group_inefficiencies(bars_view)
    )
    if not inefficiencies:
        return empty

    # Chronological walk (ties broken by kind for determinism)
    inefficiencies.sort(key=lambda e: (e.ts, e.kind))

    current_direction = 0
    last_dot: Optional[TrendDot] = None
    for e in inefficiencies:
        if e.direction != current_direction:
            last_dot = TrendDot(ts=e.ts, direction=e.direction, level=e.center)
            current_direction = e.direction

    last_ineff = inefficiencies[-1]
    direction = last_ineff.direction
    line_level = last_ineff.center

    last_close = float(bars_view["close"].iloc[-1])
    price_through_line = bool(
        (direction > 0 and last_close < line_level)
        or (direction < 0 and last_close > line_level)
    )

    return ATSTrendLineState(
        direction=direction,
        last_dot=last_dot,
        last_inefficiency=last_ineff,
        line_level=line_level,
        n_inefficiencies=len(inefficiencies),
        price_through_line=price_through_line,
    )


# ============================================================================
# Public convenience
# ============================================================================
def get_direction(
    bars: pd.DataFrame,
    as_of: Optional[pd.Timestamp] = None,
) -> int:
    """
    Convenience wrapper returning only the direction.

    Returns +1 / -1 / 0. Non-repainting (Citation 7).
    """
    return compute_trend_line_state(bars, as_of).direction


__all__ = [
    "Inefficiency",
    "TrendDot",
    "ATSTrendLineState",
    "detect_3_candle_fvg",
    "detect_group_inefficiencies",
    "compute_trend_line_state",
    "get_direction",
]

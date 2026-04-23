"""
tests/test_ats_trend_line.py — Unit tests for live/ats_trend_line.py (Part A)

12 test cases covering:
- Detection (3-candle FVG + group inefficiency)
- State computation (direction, dot emission, line level, trend-over)
- Contract (pure function, deterministic, non-repainting)
- Edge cases (empty, too-few bars, no inefficiencies)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Make live/ importable regardless of runner cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from live.ats_trend_line import (  # noqa: E402
    ATSTrendLineState,
    Inefficiency,
    TrendDot,
    compute_trend_line_state,
    detect_3_candle_fvg,
    detect_group_inefficiencies,
    get_direction,
)


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------
def _make_bars(ohlc: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build a bars DataFrame from a list of (open, high, low, close) tuples."""
    idx = pd.date_range("2026-01-01", periods=len(ohlc), freq="1h", tz="UTC")
    return pd.DataFrame(
        ohlc, columns=["open", "high", "low", "close"], index=idx
    )


# ----------------------------------------------------------------------
# 1. Empty / insufficient input
# ----------------------------------------------------------------------
def test_empty_bars_returns_neutral_state():
    bars = pd.DataFrame(columns=["open", "high", "low", "close"])
    s = compute_trend_line_state(bars)
    assert s.direction == 0
    assert s.last_dot is None
    assert s.line_level is None
    assert s.n_inefficiencies == 0
    assert s.price_through_line is False


def test_too_few_bars_returns_neutral_state():
    bars = _make_bars([(100, 101, 99, 100), (101, 102, 100, 101)])
    s = compute_trend_line_state(bars)
    assert s.direction == 0
    assert s.n_inefficiencies == 0


# ----------------------------------------------------------------------
# 2. 3-candle inefficiency detection
# ----------------------------------------------------------------------
def test_bullish_3_candle_fvg_detected():
    # Bar 0: high=100; bar 1 traveling; bar 2: low=105 → gap [100, 105]
    bars = _make_bars([
        (98, 100, 97, 99),     # bar 0: high=100
        (103, 104, 102, 103),  # bar 1: traveling up
        (106, 107, 105, 106),  # bar 2: low=105 > bar 0 high=100 → bullish FVG
    ])
    found = detect_3_candle_fvg(bars)
    assert len(found) == 1
    assert found[0].kind == "3_candle_bullish"
    assert found[0].direction == +1
    assert found[0].center == pytest.approx((100 + 105) / 2)
    assert found[0].gap_low == 100
    assert found[0].gap_high == 105


def test_bearish_3_candle_fvg_detected():
    # Bar 0: low=105; bar 2: high=100 → bearish gap
    bars = _make_bars([
        (107, 108, 105, 106),  # bar 0: low=105
        (102, 103, 101, 102),  # bar 1: traveling down
        (99, 100, 97, 98),     # bar 2: high=100 < bar 0 low=105 → bearish FVG
    ])
    found = detect_3_candle_fvg(bars)
    assert len(found) == 1
    assert found[0].kind == "3_candle_bearish"
    assert found[0].direction == -1
    assert found[0].center == pytest.approx((105 + 100) / 2)


def test_no_fvg_smooth_price_returns_empty():
    # Smooth overlapping candles → no FVG
    bars = _make_bars([
        (100, 102, 98, 101),
        (101, 103, 99, 102),
        (102, 104, 100, 103),
        (103, 105, 101, 104),
        (104, 106, 102, 105),
    ])
    assert detect_3_candle_fvg(bars) == []
    assert detect_group_inefficiencies(bars) == []


# ----------------------------------------------------------------------
# 3. Group inefficiency detection
# ----------------------------------------------------------------------
def test_bullish_group_inefficiency_detected():
    # Group1 (bars 0-1) highs <= 100; group2 (bars 3-4) lows >= 105
    bars = _make_bars([
        (98, 100, 96, 99),     # g1 bar 0
        (97, 99, 95, 98),      # g1 bar 1 — g1 max high = 100
        (103, 104, 102, 103),  # traveling
        (106, 108, 105, 107),  # g2 bar 0 — g2 min low = 105
        (107, 109, 106, 108),  # g2 bar 1
    ])
    found = detect_group_inefficiencies(bars)
    assert any(e.kind == "group_bullish" and e.direction == +1 for e in found)


# ----------------------------------------------------------------------
# 4. State: direction + line level
# ----------------------------------------------------------------------
def test_state_direction_reflects_last_inefficiency_sign():
    # Single bullish FVG → direction +1
    bars = _make_bars([
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),
    ])
    s = compute_trend_line_state(bars)
    assert s.direction == +1
    assert s.line_level == pytest.approx((100 + 105) / 2)
    assert s.last_dot is not None
    assert s.last_dot.direction == +1


# ----------------------------------------------------------------------
# 5. Dot emission on trend change (Citation 5)
# ----------------------------------------------------------------------
def test_dot_emits_on_direction_flip():
    # First a bullish FVG, then a bearish FVG → 2 dots total, most recent bearish
    bars = _make_bars([
        # bullish FVG at index 2 (bar 0 high 100, bar 2 low 105)
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),
        (107, 108, 106, 107),   # filler
        # bearish FVG centred around here: bar 4 low >> bar 6 high
        (107, 110, 106, 109),   # bar 4: low 106... need bar 4 low > bar 6 high
        (105, 107, 104, 106),   # traveling down
        (100, 102, 99, 100),    # bar 6: high 102 < bar 4 low 106 → bearish FVG
    ])
    state = compute_trend_line_state(bars)
    assert state.direction == -1
    assert state.last_dot is not None
    assert state.last_dot.direction == -1


# ----------------------------------------------------------------------
# 6. Line level = last inefficiency center (Citation 4)
# ----------------------------------------------------------------------
def test_line_level_tracks_last_inefficiency_center():
    bars = _make_bars([
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),     # bullish FVG center = 102.5
        (108, 110, 107, 109),
        (111, 113, 110, 112),
        (114, 116, 113, 115),     # another bullish FVG: prev high 110, this low 113 → center 111.5
    ])
    s = compute_trend_line_state(bars)
    # Last inefficiency should be the later one, with center 111.5
    assert s.last_inefficiency is not None
    assert s.line_level == pytest.approx(s.last_inefficiency.center)


# ----------------------------------------------------------------------
# 7. Trend-over signal (Citation 6)
# ----------------------------------------------------------------------
def test_price_through_line_flags_when_close_crosses_opposite():
    # Bullish FVG, then price closes below line level without forming NEW FVG.
    # Key: bar 3 must overlap bar 2 (no new FVG) yet close below line_level=102.5.
    bars = _make_bars([
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),   # bullish FVG here: line level = (100 + 105)/2 = 102.5
        (105, 108, 102, 102),   # overlaps bar 2 (no new FVG) but close=102 < 102.5
    ])
    s = compute_trend_line_state(bars)
    assert s.direction == +1, f"expected bullish, got {s.direction}"
    assert s.line_level == pytest.approx(102.5)
    assert s.price_through_line is True


# ----------------------------------------------------------------------
# 8. Non-repainting (Citation 7)
# ----------------------------------------------------------------------
def test_state_is_non_repainting_past_bars_immune_to_future():
    # Build a series, compute state at ts=T, then add future bars, recompute at same T.
    bars_t = _make_bars([
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),
    ])
    as_of_t = bars_t.index[-1]
    state_t = compute_trend_line_state(bars_t, as_of=as_of_t)

    # Append future bars that would create a bearish FVG later
    future_rows = pd.DataFrame(
        [(107, 108, 106, 107), (105, 106, 95, 100), (90, 92, 85, 88)],
        columns=["open", "high", "low", "close"],
        index=pd.date_range(start=bars_t.index[-1] + pd.Timedelta("1h"), periods=3, freq="1h", tz="UTC"),
    )
    bars_ext = pd.concat([bars_t, future_rows])

    state_t_again = compute_trend_line_state(bars_ext, as_of=as_of_t)
    assert state_t == state_t_again, (
        "Non-repainting contract violated: adding future bars changed past state"
    )


# ----------------------------------------------------------------------
# 9. Pure function determinism
# ----------------------------------------------------------------------
def test_pure_function_same_inputs_produce_same_outputs():
    bars = _make_bars([
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),
    ])
    s1 = compute_trend_line_state(bars)
    s2 = compute_trend_line_state(bars)
    s3 = compute_trend_line_state(bars.copy())  # copy → same data
    assert s1 == s2 == s3


# ----------------------------------------------------------------------
# 10. as_of filtering correctness
# ----------------------------------------------------------------------
def test_as_of_filter_excludes_future_bars():
    bars = _make_bars([
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),   # bullish FVG happens here (bar 2)
    ])
    # as_of = bar 1 → only 2 bars visible → no FVG possible
    as_of_before_fvg = bars.index[1]
    s = compute_trend_line_state(bars, as_of=as_of_before_fvg)
    assert s.direction == 0
    assert s.n_inefficiencies == 0


# ----------------------------------------------------------------------
# 11. get_direction wrapper agrees with full state
# ----------------------------------------------------------------------
def test_get_direction_matches_full_state():
    bars = _make_bars([
        (98, 100, 97, 99),
        (103, 104, 102, 103),
        (106, 107, 105, 106),
    ])
    assert get_direction(bars) == compute_trend_line_state(bars).direction


# ----------------------------------------------------------------------
# 12. No settings — function signatures accept no user-tunable params
# ----------------------------------------------------------------------
def test_no_user_settings_exposed():
    """Citation 2: 'the indicator is designed it has no settings'.
    Verify public API has no tolerance / threshold / window parameters."""
    import inspect

    for fn in (detect_3_candle_fvg, detect_group_inefficiencies, get_direction,
               compute_trend_line_state):
        sig = inspect.signature(fn)
        for name, param in sig.parameters.items():
            # Allowed params: bars (data input), as_of (temporal filter for non-repainting verification)
            assert name in {"bars", "as_of"}, (
                f"{fn.__name__} exposes user setting '{name}' — violates Citation 2"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

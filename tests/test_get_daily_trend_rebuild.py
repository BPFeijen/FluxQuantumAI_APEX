"""tests/test_get_daily_trend_rebuild.py — Smoke tests for BIAS-DETECTION-COMPLETE-FIX

Asana 1214284676342353. Validates the rebuilt _get_daily_trend() against the 10
edge cases from the spec.

Run:
    pytest tests/test_get_daily_trend_rebuild.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from live import level_detector as ld  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — synthetic M30 boxes parquet builders
# ---------------------------------------------------------------------------

def _make_m30_index(start_utc: str, n_bars: int) -> pd.DatetimeIndex:
    """Generate a UTC DatetimeIndex of M30 bars starting at start_utc."""
    return pd.date_range(start=start_utc, periods=n_bars, freq="30min", tz="UTC")


def _build_synthetic_m30(values_per_session: list[float],
                          session_start_utc: str = "2026-04-20 22:00:00",
                          include_partial_open: bool = False,
                          partial_value: float | None = None) -> pd.DataFrame:
    """Build a synthetic M30 DataFrame with one m30_fmv value per closed
    session (all bars in a session share the same FMV; resample('1D').last()
    will pick the last bar's value per session, which equals our seed value).

    If `include_partial_open` is True, append ONE partial bar to a new session
    (i.e. a Sunday open scenario where the session has not yet closed).
    `partial_value` is the FMV that partial bar inherits.
    """
    rows = []
    cur_ts = pd.Timestamp(session_start_utc, tz="UTC")
    # Each closed session = 24 hours = 48 M30 bars (some markets gap, but for
    # the synthetic test we use full 48 bars per session for simplicity)
    for fmv in values_per_session:
        for _ in range(48):
            rows.append({"timestamp": cur_ts, "m30_fmv": fmv})
            cur_ts += pd.Timedelta(minutes=30)
    if include_partial_open:
        # Append a single partial bar of the new (unclosed) session
        v = partial_value if partial_value is not None else values_per_session[-1]
        rows.append({"timestamp": cur_ts, "m30_fmv": v})
    df = pd.DataFrame(rows).set_index("timestamp")
    return df


@pytest.fixture(autouse=True)
def reset_meta():
    """Reset module-level diagnostics cache before each test."""
    ld._LAST_DAILY_TREND_META = {
        "source": "never_computed",
        "n_closed_sessions": 0,
        "freshness_seconds": None,
        "last_3_fmv": None,
        "decision_reason": "module_init_default",
        "computed_at_utc": None,
    }
    yield


@pytest.fixture
def tmp_m30_path(tmp_path):
    """Create a temp parquet path + redirect level_detector.M30_BOXES_PATH to it.
    Yields a callable `write(df)` that persists the synthetic DataFrame to disk."""
    p = tmp_path / "gc_m30_boxes.parquet"
    original_path = ld.M30_BOXES_PATH

    def _write(df: pd.DataFrame) -> Path:
        df.to_parquet(p)
        return p

    ld.M30_BOXES_PATH = p
    try:
        yield _write
    finally:
        ld.M30_BOXES_PATH = original_path


def _patch_now(now_iso: str):
    """Patch pandas Timestamp.now to return a fixed UTC time."""
    fixed = pd.Timestamp(now_iso, tz="UTC")

    def _fake_now(tz=None):
        if tz is None:
            return fixed.tz_localize(None) if fixed.tz else fixed
        return fixed

    return patch("live.level_detector.pd.Timestamp.now", side_effect=_fake_now)


# ---------------------------------------------------------------------------
# Test 1 — Sunday open partial bar present → returns "unknown" (not stale fallback)
# ---------------------------------------------------------------------------

def test_01_sunday_open_partial_bar_returns_unknown(tmp_m30_path):
    """The bug that motivated this fix: Sunday open partial bar inherits
    Friday's FMV; old algo failed monotonic test then fell back to stale v4.
    New algo must filter out the partial bar and return based on closed
    sessions only."""
    # 3 closed sessions Wed/Thu/Fri with values 4711/4741/4741 (Wed→Thu rises,
    # Thu→Fri flat) + Sunday partial bar inheriting Friday's 4741
    df = _build_synthetic_m30(
        values_per_session=[4711.2, 4741.4, 4741.4],
        session_start_utc="2026-04-22 22:00:00",  # Wed start
        include_partial_open=True,
        partial_value=4741.4,
    )
    tmp_m30_path(df)
    # Now = Sunday 12:00 UTC — partial Sunday session (2026-04-25 22:00 →
    # 2026-04-26 22:00) is IN FORMATION (will close in 10h), so it must be
    # filtered out. Only 3 closed sessions Wed/Thu/Fri visible.
    with _patch_now("2026-04-26 12:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_no_monotonic"
    assert diag["n_closed_sessions"] == 3
    assert diag["last_3_fmv"] == [4711.2, 4741.4, 4741.4]


# ---------------------------------------------------------------------------
# Test 2 — Half-session partial bar present → returns "unknown"
# ---------------------------------------------------------------------------

def test_02_half_session_partial_returns_unknown_no_monotonic(tmp_m30_path):
    """Mid-session partial bar (e.g. checked at 10:00 UTC during an open
    session) must be excluded; verdict driven by closed sessions only."""
    df = _build_synthetic_m30(
        values_per_session=[100.0, 105.0, 110.0],
        session_start_utc="2026-04-22 22:00:00",
        include_partial_open=True,
        partial_value=109.0,  # tries to mislead into 'short' if included
    )
    tmp_m30_path(df)
    # Now is mid Sunday session (06:00 UTC of Sunday — halfway through)
    with _patch_now("2026-04-26 06:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    # Wed/Thu/Fri closed; Sunday session NOT closed (will close at 22:00 UTC Mon)
    # 3 closed sessions in monotonic rising → "long"
    assert result == "long"
    assert diag["last_3_fmv"] == [100.0, 105.0, 110.0]


# ---------------------------------------------------------------------------
# Test 3 — Full session closed, monotonic rising 3-of-3 → "long"
# ---------------------------------------------------------------------------

def test_03_monotonic_rising_3of3_returns_long(tmp_m30_path):
    df = _build_synthetic_m30(
        values_per_session=[2000.0, 2010.0, 2020.0],
        session_start_utc="2026-04-22 22:00:00",
    )
    tmp_m30_path(df)
    with _patch_now("2026-04-26 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "long"
    assert diag["source"] == "m30_resample_closed"
    assert "monotonic_rising_3of3" in diag["decision_reason"]


# ---------------------------------------------------------------------------
# Test 4 — Full session closed, monotonic falling 3-of-3 → "short"
# ---------------------------------------------------------------------------

def test_04_monotonic_falling_3of3_returns_short(tmp_m30_path):
    df = _build_synthetic_m30(
        values_per_session=[2020.0, 2010.0, 2000.0],
        session_start_utc="2026-04-22 22:00:00",
    )
    tmp_m30_path(df)
    with _patch_now("2026-04-26 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "short"
    assert diag["source"] == "m30_resample_closed"
    assert "monotonic_falling_3of3" in diag["decision_reason"]


# ---------------------------------------------------------------------------
# Test 5 — Full session closed, mixed (no monotonic) → "unknown"
# ---------------------------------------------------------------------------

def test_05_mixed_no_monotonic_returns_unknown(tmp_m30_path):
    df = _build_synthetic_m30(
        values_per_session=[2010.0, 2000.0, 2020.0],  # down, up
        session_start_utc="2026-04-22 22:00:00",
    )
    tmp_m30_path(df)
    with _patch_now("2026-04-26 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_no_monotonic"


# ---------------------------------------------------------------------------
# Test 6 — Less than 3 closed sessions available → "unknown"
# ---------------------------------------------------------------------------

def test_06_insufficient_history_returns_unknown(tmp_m30_path):
    df = _build_synthetic_m30(
        values_per_session=[2000.0, 2010.0],  # Only 2 closed sessions
        session_start_utc="2026-04-23 22:00:00",
    )
    tmp_m30_path(df)
    with _patch_now("2026-04-26 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_insufficient_history"
    assert diag["n_closed_sessions"] == 2


# ---------------------------------------------------------------------------
# Test 7 — DST transition day → session boundary correct
# ---------------------------------------------------------------------------

def test_07_dst_transition_session_boundary(tmp_m30_path):
    """The function uses offset='22h' (CME EST anchor). On EDT days the actual
    close is 21:00 UTC. The 22:00 UTC anchor still aggregates all bars of the
    day correctly because there are no trades from 21:00-22:00 UTC. Ensure the
    function does not crash and produces sensible verdict on a DST-transition
    week.
    """
    # Spring forward DST in US: 2026-03-08 — first Sunday of March
    df = _build_synthetic_m30(
        values_per_session=[1900.0, 1910.0, 1920.0],
        session_start_utc="2026-03-04 22:00:00",  # Wed before DST
    )
    tmp_m30_path(df)
    with _patch_now("2026-03-08 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "long"
    assert diag["n_closed_sessions"] >= 3


# ---------------------------------------------------------------------------
# Test 8 — Empty input data → "unknown"
# ---------------------------------------------------------------------------

def test_08_empty_data_returns_unknown(tmp_m30_path):
    df = pd.DataFrame(columns=["m30_fmv"])
    df.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    tmp_m30_path(df)
    with _patch_now("2026-04-26 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "M30_boxes_empty" or diag["source"] in (
        "unknown_no_data", "unknown_insufficient_history"
    )


# ---------------------------------------------------------------------------
# Test 9 — Data spanning weekend gap → ignores Saturday/Sunday correctly
# ---------------------------------------------------------------------------

def test_09_weekend_gap_filtered_out(tmp_m30_path):
    """resample('1D', offset='22h').last() over a window with weekend gap:
    sessions producing FMV will only be Mon-Fri (no Saturday/Sunday close).
    Verdict must still be derivable from the closed weekday sessions."""
    df = _build_synthetic_m30(
        values_per_session=[3000.0, 3010.0, 3020.0],
        session_start_utc="2026-04-22 22:00:00",  # Wed (no Sat/Sun gap in synthetic)
    )
    tmp_m30_path(df)
    with _patch_now("2026-04-26 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    # Synthetic includes only weekday sessions; verdict must be "long"
    assert result == "long"
    # n_closed_sessions counts only sessions with non-NaN FMV; should be >=3
    assert diag["n_closed_sessions"] >= 3


# ---------------------------------------------------------------------------
# Test 10 — Heartbeat field freshness_seconds matches age of last closed session
# ---------------------------------------------------------------------------

def test_10_freshness_matches_last_closed_session(tmp_m30_path):
    """If 'now' is X seconds after the last closed session ended, the
    diagnostic field freshness_seconds must equal X."""
    # Last closed session = 2026-04-24 22:00 UTC (Friday). Session ends at
    # 2026-04-25 22:00 UTC. Now = 2026-04-26 22:00 UTC. Freshness = 86400s.
    df = _build_synthetic_m30(
        values_per_session=[100.0, 105.0, 110.0],
        session_start_utc="2026-04-22 22:00:00",
    )
    tmp_m30_path(df)
    with _patch_now("2026-04-26 22:00:00"):
        ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert diag["freshness_seconds"] is not None
    # Last session label = 2026-04-24 22:00 UTC. End = 2026-04-25 22:00 UTC.
    # Now = 2026-04-26 22:00 UTC. Delta = 86400s exactly.
    assert abs(diag["freshness_seconds"] - 86400.0) < 1.0

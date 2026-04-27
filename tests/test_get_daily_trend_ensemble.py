"""tests/test_get_daily_trend_ensemble.py — Phase 3 smoke tests for
BIAS-DETECTION-PURDUE-CALIBRATION (Asana 1214284792412296).

Validates the rebuilt _get_daily_trend() with B+C ensemble (Phase 1) against
the spec acceptance criteria. Replaces tests/test_get_daily_trend_rebuild.py
which exercised the strict-monotonic algorithm now superseded.

Two layers:
  * Patched-signal tests: mock _compute_signal_b/_compute_signal_c directly
    to verify ensemble VOTE LOGIC in isolation (decision = f(B, C)).
  * Real-data tests: synthetic M30 OHLC parquets that exercise data-handling
    edge cases (partial bars, DST, weekend gap, insufficient history, etc.).

Run:
    pytest tests/test_get_daily_trend_ensemble.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from live import level_detector as ld  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — synthetic M30 OHLC parquet builders
# ---------------------------------------------------------------------------

def _build_synthetic_m30_ohlc(sessions: list[tuple[float, float, float, float]],
                              session_start_utc: str = "2026-01-01 22:00:00",
                              include_partial_open: bool = False,
                              partial_ohlc: tuple | None = None) -> pd.DataFrame:
    """Build synthetic M30 DataFrame from one (open, high, low, close) tuple per
    closed D1 session. All 48 M30 bars in a session share the same OHLC, so the
    D1 resample yields exactly the supplied (open, high, low, close).
    """
    rows = []
    cur_ts = pd.Timestamp(session_start_utc, tz="UTC")
    for ohlc in sessions:
        o, h, l, c = ohlc
        for _ in range(48):
            rows.append({
                "timestamp": cur_ts,
                "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            })
            cur_ts += pd.Timedelta(minutes=30)
    if include_partial_open:
        po = partial_ohlc if partial_ohlc is not None else sessions[-1]
        rows.append({
            "timestamp": cur_ts,
            "open": float(po[0]), "high": float(po[1]),
            "low": float(po[2]), "close": float(po[3]),
        })
    return pd.DataFrame(rows).set_index("timestamp")


def _bull_sessions(n: int, start: float = 100.0, step: float = 5.0) -> list[tuple]:
    """n sessions trending up cleanly: each session shifts O/H/L/C up by `step`."""
    out = []
    for i in range(n):
        base = start + i * step
        out.append((base, base + 4, base - 1, base + 3))
    return out


def _bear_sessions(n: int, start: float = 200.0, step: float = 5.0) -> list[tuple]:
    """n sessions trending down cleanly."""
    out = []
    for i in range(n):
        base = start - i * step
        out.append((base, base + 1, base - 4, base - 3))
    return out


@pytest.fixture(autouse=True)
def reset_meta():
    """Reset module-level diagnostics cache before each test."""
    ld._LAST_DAILY_TREND_META = {
        "source": "never_computed",
        "n_closed_sessions": 0,
        "freshness_seconds": None,
        "signal_a": 0,
        "signal_b": 0,
        "signal_c": 0,
        "agreement_count": 0,
        "decision_reason": "module_init_default",
        "calibration_version": ld.CALIBRATION_VERSION,
        "computed_at_utc": None,
    }
    yield


@pytest.fixture
def tmp_m30_path(tmp_path):
    """Create a temp parquet path + redirect level_detector.M30_BOXES_PATH."""
    p = tmp_path / "gc_m30_boxes.parquet"
    original = ld.M30_BOXES_PATH

    def _write(df: pd.DataFrame) -> Path:
        df.to_parquet(p)
        return p

    ld.M30_BOXES_PATH = p
    try:
        yield _write
    finally:
        ld.M30_BOXES_PATH = original


def _patch_now(now_iso: str):
    """Patch pd.Timestamp.now to return a fixed UTC time."""
    fixed = pd.Timestamp(now_iso, tz="UTC")

    def _fake_now(tz=None):
        if tz is None:
            return fixed.tz_localize(None) if fixed.tz else fixed
        return fixed

    return patch("live.level_detector.pd.Timestamp.now", side_effect=_fake_now)


def _patch_signals(sig_a: int = 0, sig_b: int = 0, sig_c: int = 0):
    """Patch _compute_signal_a/b/c to return fixed values — isolates vote logic."""
    return [
        patch.object(ld, "_compute_signal_a", return_value=sig_a),
        patch.object(ld, "_compute_signal_b", return_value=sig_b),
        patch.object(ld, "_compute_signal_c", return_value=sig_c),
    ]


# ---------------------------------------------------------------------------
# Layer 1 — patched-signal tests (vote logic isolation)
# ---------------------------------------------------------------------------

def test_01_b_plus1_c_plus1_returns_long(tmp_m30_path):
    """B==C==+1 (both definite long) -> daily_trend = 'long'."""
    df = _build_synthetic_m30_ohlc(_bull_sessions(20))
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"), \
         patch.object(ld, "_compute_signal_a", return_value=0), \
         patch.object(ld, "_compute_signal_b", return_value=+1), \
         patch.object(ld, "_compute_signal_c", return_value=+1):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "long"
    assert diag["signal_b"] == 1
    assert diag["signal_c"] == 1
    assert diag["agreement_count"] == 2
    assert diag["source"] == "ensemble_b_c_agree"
    assert "agree" in diag["decision_reason"]


def test_02_b_minus1_c_minus1_returns_short(tmp_m30_path):
    """B==C==-1 (both definite short) -> daily_trend = 'short'."""
    df = _build_synthetic_m30_ohlc(_bear_sessions(20))
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"), \
         patch.object(ld, "_compute_signal_a", return_value=0), \
         patch.object(ld, "_compute_signal_b", return_value=-1), \
         patch.object(ld, "_compute_signal_c", return_value=-1):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "short"
    assert diag["agreement_count"] == 2
    assert diag["source"] == "ensemble_b_c_agree"


def test_03_b_plus1_c_minus1_returns_unknown(tmp_m30_path):
    """B=+1 vs C=-1 (disagree) -> daily_trend = 'unknown'."""
    df = _build_synthetic_m30_ohlc(_bull_sessions(20))
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"), \
         patch.object(ld, "_compute_signal_a", return_value=0), \
         patch.object(ld, "_compute_signal_b", return_value=+1), \
         patch.object(ld, "_compute_signal_c", return_value=-1):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["agreement_count"] == 0
    assert diag["source"] == "unknown_b_c_disagree"


def test_04_b_minus1_c_plus1_returns_unknown(tmp_m30_path):
    """B=-1 vs C=+1 (disagree) -> daily_trend = 'unknown'."""
    df = _build_synthetic_m30_ohlc(_bull_sessions(20))
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"), \
         patch.object(ld, "_compute_signal_a", return_value=0), \
         patch.object(ld, "_compute_signal_b", return_value=-1), \
         patch.object(ld, "_compute_signal_c", return_value=+1):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_b_c_disagree"


def test_05_b_zero_c_plus1_returns_unknown(tmp_m30_path):
    """B=0, C=+1 (B partial) -> daily_trend = 'unknown'."""
    df = _build_synthetic_m30_ohlc(_bull_sessions(20))
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"), \
         patch.object(ld, "_compute_signal_a", return_value=0), \
         patch.object(ld, "_compute_signal_b", return_value=0), \
         patch.object(ld, "_compute_signal_c", return_value=+1):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_partial_signal"


def test_06_b_plus1_c_zero_returns_unknown(tmp_m30_path):
    """B=+1, C=0 (C partial) -> daily_trend = 'unknown'."""
    df = _build_synthetic_m30_ohlc(_bull_sessions(20))
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"), \
         patch.object(ld, "_compute_signal_a", return_value=0), \
         patch.object(ld, "_compute_signal_b", return_value=+1), \
         patch.object(ld, "_compute_signal_c", return_value=0):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_partial_signal"


def test_07_b_zero_c_zero_returns_unknown(tmp_m30_path):
    """B=0, C=0 (no signal) -> daily_trend = 'unknown'."""
    df = _build_synthetic_m30_ohlc(_bull_sessions(20))
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"), \
         patch.object(ld, "_compute_signal_a", return_value=0), \
         patch.object(ld, "_compute_signal_b", return_value=0), \
         patch.object(ld, "_compute_signal_c", return_value=0):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_no_signal"


# ---------------------------------------------------------------------------
# Layer 2 — data-handling edge cases (real signal computation)
# ---------------------------------------------------------------------------

def test_08_insufficient_history_returns_unknown(tmp_m30_path):
    """Fewer than _MIN_D1_BARS closed sessions -> 'unknown'."""
    # _MIN_D1_BARS = max(2*3+4, 2*5+2) = 12. Use 5 sessions only.
    df = _build_synthetic_m30_ohlc(_bull_sessions(5),
                                    session_start_utc="2026-01-01 22:00:00")
    tmp_m30_path(df)
    with _patch_now("2026-01-10 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] == "unknown_insufficient_history"
    assert diag["n_closed_sessions"] == 5


def test_09_empty_parquet_returns_unknown(tmp_m30_path):
    """Empty parquet -> 'unknown'."""
    df = pd.DataFrame(columns=["open", "high", "low", "close"])
    df.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result == "unknown"
    assert diag["source"] in ("M30_boxes_empty", "unknown_no_data",
                              "unknown_insufficient_history")


def test_10_parquet_missing_returns_unknown(tmp_path):
    """Parquet path doesn't exist -> 'unknown'."""
    missing = tmp_path / "does_not_exist.parquet"
    original = ld.M30_BOXES_PATH
    ld.M30_BOXES_PATH = missing
    try:
        with _patch_now("2026-01-25 22:00:00"):
            result = ld._get_daily_trend()
        diag = ld.get_daily_trend_diagnostics()
        assert result == "unknown"
        assert "missing" in diag["decision_reason"]
    finally:
        ld.M30_BOXES_PATH = original


def test_11_sunday_open_partial_bar_excluded(tmp_m30_path):
    """Partial Sunday-open bar must NOT influence the signal computation —
    only fully closed sessions enter the D1 OHLC + signals.
    """
    # 20 closed bull sessions + a partial open with a wild OHLC that would
    # break the trend if included
    sessions = _bull_sessions(20, start=100.0, step=5.0)
    partial = (50.0, 55.0, 45.0, 48.0)  # huge gap-down partial
    df = _build_synthetic_m30_ohlc(
        sessions, session_start_utc="2026-01-01 22:00:00",
        include_partial_open=True, partial_ohlc=partial,
    )
    tmp_m30_path(df)
    # 20 sessions starting 2026-01-01 22:00, last close at 2026-01-21 22:00.
    # Partial bar at 2026-01-21 22:00 is start of session 21 (closes 2026-01-22 22:00).
    # Now = 2026-01-22 12:00 — partial session NOT closed.
    with _patch_now("2026-01-22 12:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    # n_closed_sessions should be exactly 20 (partial excluded)
    assert diag["n_closed_sessions"] == 20
    # The partial value (50) didn't enter the signals — they should reflect
    # the trend of the first 20 sessions only.
    assert result in ("long", "short", "unknown")


def test_12_mid_session_bias_unchanged_from_session_start(tmp_m30_path):
    """At T0 (start of session) and T0 + 12h (mid-session, before close),
    the bias should be identical because no new closed bar arrived.
    """
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions, session_start_utc="2026-01-01 22:00:00")
    tmp_m30_path(df)
    # Last closed session ends at 2026-01-21 22:00.
    # At 2026-01-21 22:01 (just after close) and 2026-01-22 10:00 (mid-next-session),
    # the closed-set is identical -> result identical.
    with _patch_now("2026-01-21 22:01:00"):
        result_t0 = ld._get_daily_trend()
        diag_t0 = ld.get_daily_trend_diagnostics()
    with _patch_now("2026-01-22 10:00:00"):
        result_mid = ld._get_daily_trend()
        diag_mid = ld.get_daily_trend_diagnostics()
    assert result_t0 == result_mid
    assert diag_t0["n_closed_sessions"] == diag_mid["n_closed_sessions"]
    assert diag_t0["signal_b"] == diag_mid["signal_b"]
    assert diag_t0["signal_c"] == diag_mid["signal_c"]


def test_13_d1_close_plus_1s_reevaluates_with_new_bar(tmp_m30_path):
    """At D1 close + 1 second the count of closed sessions increments by 1."""
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions, session_start_utc="2026-01-01 22:00:00")
    tmp_m30_path(df)
    # 20 sessions ending 2026-01-21 22:00 UTC.
    # At 2026-01-21 21:59:59 -> only 19 closed (last one not yet closed).
    # At 2026-01-21 22:00:01 -> 20 closed (new one just closed).
    with _patch_now("2026-01-21 21:59:59"):
        ld._get_daily_trend()
        diag_before = ld.get_daily_trend_diagnostics()
    with _patch_now("2026-01-21 22:00:01"):
        ld._get_daily_trend()
        diag_after = ld.get_daily_trend_diagnostics()
    assert diag_after["n_closed_sessions"] == diag_before["n_closed_sessions"] + 1


def test_14_dst_transition_session_boundary(tmp_m30_path):
    """The function uses offset='22h' (CME Globex anchor). On EDT days the
    actual close is 21:00 UTC but the 22:00 UTC anchor still aggregates all
    bars correctly because there are no trades from 21:00-22:00 UTC. The
    function must not crash and must produce a valid result on a DST week.
    """
    # 2026-03-08 = US spring-forward DST transition
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions, session_start_utc="2026-02-15 22:00:00")
    tmp_m30_path(df)
    with _patch_now("2026-03-08 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result in ("long", "short", "unknown")
    assert diag["n_closed_sessions"] >= 12   # _MIN_D1_BARS


def test_15_weekend_gap_filtered(tmp_m30_path):
    """Synthetic continuous M30 stream (no Sat/Sun gap) — verdict still
    derives from closed sessions correctly. Real production data has gaps;
    the resample with .dropna() handles them naturally.
    """
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions, session_start_utc="2026-01-01 22:00:00")
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert diag["n_closed_sessions"] >= 12
    assert result in ("long", "short", "unknown")


# ---------------------------------------------------------------------------
# Layer 3 — contract + heartbeat invariants
# ---------------------------------------------------------------------------

def test_16_never_returns_neutral(tmp_m30_path):
    """Contract preservation: output is exactly one of long/short/unknown.
    String 'neutral' must never be emitted (would break consumer contract).
    """
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions)
    tmp_m30_path(df)
    # Cycle every (B, C) combination and confirm output is never 'neutral'
    for sig_b in (-1, 0, +1):
        for sig_c in (-1, 0, +1):
            ld._LAST_DAILY_TREND_META["source"] = "test_reset"
            with _patch_now("2026-01-25 22:00:00"), \
                 patch.object(ld, "_compute_signal_a", return_value=0), \
                 patch.object(ld, "_compute_signal_b", return_value=sig_b), \
                 patch.object(ld, "_compute_signal_c", return_value=sig_c):
                result = ld._get_daily_trend()
            assert result in ("long", "short", "unknown"), (
                f"CONTRACT VIOLATION at (B={sig_b}, C={sig_c}): result={result!r}"
            )
            assert result != "neutral"


def test_17_diagnostics_contain_all_expected_fields(tmp_m30_path):
    """Heartbeat contract: diagnostics dict has all expected fields per spec."""
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions)
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"):
        ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    required_fields = {
        "source", "n_closed_sessions", "freshness_seconds",
        "signal_a", "signal_b", "signal_c",
        "agreement_count", "decision_reason",
        "calibration_version", "computed_at_utc",
    }
    missing = required_fields - set(diag.keys())
    assert not missing, f"Missing heartbeat fields: {missing}"


def test_18_signal_a_diagnostic_only_does_not_affect_vote(tmp_m30_path):
    """Signal A is exposed in heartbeat for diagnostics but MUST NOT enter
    the ensemble vote. Verified by holding (B, C) constant and varying A."""
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions)
    tmp_m30_path(df)
    results: dict[int, str] = {}
    for sig_a in (-1, 0, +1):
        with _patch_now("2026-01-25 22:00:00"), \
             patch.object(ld, "_compute_signal_a", return_value=sig_a), \
             patch.object(ld, "_compute_signal_b", return_value=+1), \
             patch.object(ld, "_compute_signal_c", return_value=+1):
            results[sig_a] = ld._get_daily_trend()
    # B==C==+1 in all three -> daily_trend always 'long', regardless of A
    assert results[-1] == results[0] == results[+1] == "long"


def test_19_calibration_version_in_diagnostics(tmp_m30_path):
    """The diagnostic includes calibration_version per spec for forensic
    visibility. Should match the module-level CALIBRATION_VERSION constant."""
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions)
    tmp_m30_path(df)
    with _patch_now("2026-01-25 22:00:00"):
        ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert diag["calibration_version"] == ld.CALIBRATION_VERSION
    assert ld.CALIBRATION_VERSION.startswith("v2_")


def test_20_freshness_seconds_matches_last_session_close(tmp_m30_path):
    """diag['freshness_seconds'] = (now - last_session_end).total_seconds().
    Last closed session is at 2026-01-20 22:00 UTC (label) ending 2026-01-21
    22:00 UTC. Now = 2026-01-22 22:00 UTC -> 86400s exactly.
    """
    sessions = _bull_sessions(20)
    df = _build_synthetic_m30_ohlc(sessions, session_start_utc="2026-01-01 22:00:00")
    tmp_m30_path(df)
    with _patch_now("2026-01-22 22:00:00"):
        ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert diag["freshness_seconds"] is not None
    # Last session label = 2026-01-20 22:00 UTC. End = 2026-01-21 22:00 UTC.
    # Now = 2026-01-22 22:00 UTC. Delta = 86400s.
    assert abs(diag["freshness_seconds"] - 86400.0) < 1.0


def test_21_real_signals_produce_valid_decision(tmp_m30_path):
    """Integration test: REAL signal computation (no patching) on a clean
    bull-trend synthetic window. The decision should be one of the three
    contract values; no exception should escape.
    """
    sessions = _bull_sessions(30, start=100.0, step=5.0)
    df = _build_synthetic_m30_ohlc(sessions, session_start_utc="2026-01-01 22:00:00")
    tmp_m30_path(df)
    with _patch_now("2026-02-05 22:00:00"):
        result = ld._get_daily_trend()
    diag = ld.get_daily_trend_diagnostics()
    assert result in ("long", "short", "unknown")
    # All three signals computed (each in {-1, 0, +1})
    for k in ("signal_a", "signal_b", "signal_c"):
        assert diag[k] in (-1, 0, +1), f"{k}={diag[k]!r} not in {{-1, 0, +1}}"
    # When B==C and both definite, agreement_count == 2
    if diag["signal_b"] != 0 and diag["signal_b"] == diag["signal_c"]:
        assert diag["agreement_count"] == 2
        expected = "long" if diag["signal_b"] == +1 else "short"
        assert result == expected
    else:
        assert diag["agreement_count"] == 0
        assert result == "unknown"

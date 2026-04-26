"""
Unit tests for live/impl2_features.py — IMPL-2 (EXEC-4 2026-04-26).

Each of the 5 features has at least one POSITIVE case (feature must fire
under known-good conditions) and one NEGATIVE case (feature must NOT fire
when precondition or signal is absent).

Run:
    python -m pytest tests/test_impl2_features.py -v
or:
    python tests/test_impl2_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python tests/test_impl2_features.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from live.impl2_features import (
    feature_F1_B,
    feature_F2_B,
    feature_F3_B,
    feature_F5_A,
    feature_F5_B,
    feature_F4,
    evaluate_all,
    NEUTRAL_FEATURE_STATE,
    EFR_ROLLING,
    VOL_CLIMAX_WINDOW,
)


# ----------------------------------------------------------------------------
# Synthetic data builders
# ----------------------------------------------------------------------------

def _baseline_df(n: int = 120, base_close: float = 4700.0) -> pd.DataFrame:
    """N M30 bars, neutral OHLCV, no delta column (forces F3 inactive)."""
    idx = pd.date_range("2026-04-01", periods=n, freq="30min", tz="UTC")
    close = np.linspace(base_close, base_close + 1.0, n)
    return pd.DataFrame({
        "open":   close - 0.5,
        "high":   close + 1.0,
        "low":    close - 1.0,
        "close":  close,
        "volume": np.full(n, 100.0),
    }, index=idx)


def _regime(trend_a=(False, ""), trend_b=(False, "")):
    return {"trend_a": trend_a, "trend_b": trend_b}


# ============================================================================
# F1_B SOT — monotonically decreasing range over last 3 bars × TREND-B
# ============================================================================

def test_F1_B_positive_decreasing_ranges():
    df = _baseline_df(n=10).copy()
    # Force last 3 ranges = 5, 3, 1 (monotonically shrinking)
    df.loc[df.index[-3], ["high", "low"]] = [df["close"].iloc[-3] + 2.5, df["close"].iloc[-3] - 2.5]  # range=5
    df.loc[df.index[-2], ["high", "low"]] = [df["close"].iloc[-2] + 1.5, df["close"].iloc[-2] - 1.5]  # range=3
    df.loc[df.index[-1], ["high", "low"]] = [df["close"].iloc[-1] + 0.5, df["close"].iloc[-1] - 0.5]  # range=1
    assert feature_F1_B(df, _regime(trend_b=(True, "LONG"))) is True


def test_F1_B_negative_no_trend_b():
    df = _baseline_df(n=10).copy()
    df.loc[df.index[-3], ["high", "low"]] = [df["close"].iloc[-3] + 2.5, df["close"].iloc[-3] - 2.5]
    df.loc[df.index[-2], ["high", "low"]] = [df["close"].iloc[-2] + 1.5, df["close"].iloc[-2] - 1.5]
    df.loc[df.index[-1], ["high", "low"]] = [df["close"].iloc[-1] + 0.5, df["close"].iloc[-1] - 0.5]
    assert feature_F1_B(df, _regime(trend_b=(False, ""))) is False


def test_F1_B_negative_ranges_increasing():
    df = _baseline_df(n=10).copy()
    # Increasing ranges 1, 3, 5 — explicit NO SOT
    df.loc[df.index[-3], ["high", "low"]] = [df["close"].iloc[-3] + 0.5, df["close"].iloc[-3] - 0.5]
    df.loc[df.index[-2], ["high", "low"]] = [df["close"].iloc[-2] + 1.5, df["close"].iloc[-2] - 1.5]
    df.loc[df.index[-1], ["high", "low"]] = [df["close"].iloc[-1] + 2.5, df["close"].iloc[-1] - 2.5]
    assert feature_F1_B(df, _regime(trend_b=(True, "LONG"))) is False


# ============================================================================
# F2_B EFR — vol_pct − body_pct > 0.3 × TREND-B
# ============================================================================

def test_F2_B_positive_high_vol_low_body():
    df = _baseline_df(n=EFR_ROLLING + 10).copy()
    # Make all body small (low body pct), then last bar volume = huge spike
    df["close"] = df["open"] + 0.01     # tiny body throughout
    df["volume"] = np.random.RandomState(42).uniform(50, 100, len(df))
    df.iloc[-1, df.columns.get_loc("volume")] = 10_000.0  # extreme volume
    assert feature_F2_B(df, _regime(trend_b=(True, "LONG"))) is True


def test_F2_B_negative_no_trend_b():
    df = _baseline_df(n=EFR_ROLLING + 10).copy()
    df["close"] = df["open"] + 0.01
    df.iloc[-1, df.columns.get_loc("volume")] = 10_000.0
    assert feature_F2_B(df, _regime(trend_b=(False, ""))) is False


def test_F2_B_negative_balanced_vol_body():
    df = _baseline_df(n=EFR_ROLLING + 10).copy()
    # Equal vol+body percentile rank → divergence ~ 0
    rng = np.random.RandomState(7)
    df["volume"] = rng.uniform(50, 150, len(df))
    df["close"] = df["open"] + rng.uniform(-1, 1, len(df))
    assert feature_F2_B(df, _regime(trend_b=(True, "LONG"))) is False


# ============================================================================
# F3_B Delta Divergence — delta sign opposite to bar direction × TREND-B
# ============================================================================

def test_F3_B_positive_uptrend_positive_delta_bearish_bar():
    df = _baseline_df(n=10).copy()
    df["m30_bar_delta"] = 0.0
    # Last bar: bearish (close < open) but positive delta = buyers exhausted
    df.iloc[-1, df.columns.get_loc("open")]  = 4710.0
    df.iloc[-1, df.columns.get_loc("close")] = 4705.0
    df.iloc[-1, df.columns.get_loc("m30_bar_delta")] = 250.0
    # Force tdir=+1 by recent close climb (close[-1] > close[-SOT_N])
    df.iloc[-3, df.columns.get_loc("close")] = 4690.0
    assert feature_F3_B(df, _regime(trend_b=(True, "LONG"))) is True


def test_F3_B_negative_no_delta_column():
    df = _baseline_df(n=10).copy()  # no m30_bar_delta column
    assert feature_F3_B(df, _regime(trend_b=(True, "LONG"))) is False


def test_F3_B_negative_delta_aligned_with_bar():
    df = _baseline_df(n=10).copy()
    df["m30_bar_delta"] = 0.0
    df.iloc[-1, df.columns.get_loc("open")]  = 4705.0
    df.iloc[-1, df.columns.get_loc("close")] = 4710.0    # bullish bar
    df.iloc[-1, df.columns.get_loc("m30_bar_delta")] = 250.0  # positive delta — ALIGNED
    df.iloc[-3, df.columns.get_loc("close")] = 4690.0
    assert feature_F3_B(df, _regime(trend_b=(True, "LONG"))) is False


# ============================================================================
# F5_A ClosePct × TREND-A — weak close in trend direction
# ============================================================================

def test_F5_A_positive_long_weak_close():
    df = _baseline_df(n=10).copy()
    # Last bar: close near low → close_pct_from_low < 0.3 (weak for LONG)
    df.iloc[-1, df.columns.get_loc("low")]   = 4700.0
    df.iloc[-1, df.columns.get_loc("high")]  = 4710.0
    df.iloc[-1, df.columns.get_loc("close")] = 4701.0
    df.iloc[-1, df.columns.get_loc("open")]  = 4705.0
    assert feature_F5_A(df, _regime(trend_a=(True, "LONG"))) is True


def test_F5_A_negative_no_trend_a():
    df = _baseline_df(n=10).copy()
    df.iloc[-1, df.columns.get_loc("low")]   = 4700.0
    df.iloc[-1, df.columns.get_loc("high")]  = 4710.0
    df.iloc[-1, df.columns.get_loc("close")] = 4701.0
    assert feature_F5_A(df, _regime(trend_a=(False, ""))) is False


def test_F5_A_negative_strong_close_in_trend():
    df = _baseline_df(n=10).copy()
    # Strong close near high (full thrust) — NOT exhaustion
    df.iloc[-1, df.columns.get_loc("low")]   = 4700.0
    df.iloc[-1, df.columns.get_loc("high")]  = 4710.0
    df.iloc[-1, df.columns.get_loc("close")] = 4709.5
    assert feature_F5_A(df, _regime(trend_a=(True, "LONG"))) is False


# ============================================================================
# F5_B ClosePct × TREND-B — same as F5_A but TREND-B precondition
# ============================================================================

def test_F5_B_positive_short_weak_close():
    df = _baseline_df(n=10).copy()
    # SHORT direction: weak close = close near HIGH (failure to push down)
    df.iloc[-1, df.columns.get_loc("low")]   = 4700.0
    df.iloc[-1, df.columns.get_loc("high")]  = 4710.0
    df.iloc[-1, df.columns.get_loc("close")] = 4709.0
    df.iloc[-1, df.columns.get_loc("open")]  = 4705.0
    assert feature_F5_B(df, _regime(trend_b=(True, "SHORT"))) is True


def test_F5_B_negative_no_trend_b():
    df = _baseline_df(n=10).copy()
    df.iloc[-1, df.columns.get_loc("low")]   = 4700.0
    df.iloc[-1, df.columns.get_loc("high")]  = 4710.0
    df.iloc[-1, df.columns.get_loc("close")] = 4709.0
    assert feature_F5_B(df, _regime(trend_b=(False, "SHORT"))) is False


def test_F5_B_direction_fallback_when_dir_str_empty():
    """When IMPL-1 returns ('','') for direction, F5_B falls back to
    sign(close[-1] - close[-SOT_N]) per docstring."""
    df = _baseline_df(n=10).copy()
    # Force descending price across last 3 → fallback tdir = −1 (SHORT)
    df.iloc[-3, df.columns.get_loc("close")] = 4720.0
    df.iloc[-2, df.columns.get_loc("close")] = 4715.0
    df.iloc[-1, df.columns.get_loc("close")] = 4709.0
    df.iloc[-1, df.columns.get_loc("low")]   = 4700.0
    df.iloc[-1, df.columns.get_loc("high")]  = 4710.0
    assert feature_F5_B(df, _regime(trend_b=(True, ""))) is True


# ============================================================================
# F4 Volume Climax × TREND-A — anti-exit feature (EXEC-6 IMPL-4)
# ============================================================================

def test_F4_positive_climax_and_trend_a():
    df = _baseline_df(n=VOL_CLIMAX_WINDOW + 10).copy()
    # Last bar has volume far above any in window → > p95
    df.iloc[-1, df.columns.get_loc("volume")] = 100_000.0
    assert feature_F4(df, _regime(trend_a=(True, "LONG"))) is True


def test_F4_negative_no_trend_a():
    df = _baseline_df(n=VOL_CLIMAX_WINDOW + 10).copy()
    df.iloc[-1, df.columns.get_loc("volume")] = 100_000.0
    assert feature_F4(df, _regime(trend_a=(False, ""))) is False


def test_F4_negative_volume_within_normal_range():
    df = _baseline_df(n=VOL_CLIMAX_WINDOW + 10).copy()
    # All volumes constant 100 → last is NOT > p95
    assert feature_F4(df, _regime(trend_a=(True, "LONG"))) is False


def test_F4_warmup_insufficient():
    df = _baseline_df(n=10).copy()  # below warm-up of 20
    df.iloc[-1, df.columns.get_loc("volume")] = 100_000.0
    assert feature_F4(df, _regime(trend_a=(True, "LONG"))) is False


def test_F4_missing_volume_column():
    df = _baseline_df(n=VOL_CLIMAX_WINDOW + 10).copy()
    df = df.drop(columns=["volume"])
    assert feature_F4(df, _regime(trend_a=(True, "LONG"))) is False


# ============================================================================
# evaluate_all wrapper — fail-soft + stable schema
# ============================================================================

def test_evaluate_all_returns_complete_schema_for_empty_df():
    out = evaluate_all(pd.DataFrame(), _regime())
    assert out == NEUTRAL_FEATURE_STATE


def test_evaluate_all_returns_complete_schema_for_none_df():
    out = evaluate_all(None, _regime())
    assert out == NEUTRAL_FEATURE_STATE


def test_evaluate_all_keys_always_present():
    df = _baseline_df(n=5)
    out = evaluate_all(df, _regime())
    assert set(out.keys()) == {"F1_B", "F2_B", "F3_B", "F5_A", "F5_B"}
    assert all(isinstance(v, bool) for v in out.values())


# ============================================================================
# Manual runner (no pytest dependency required)
# ============================================================================

if __name__ == "__main__":
    tests = [
        # F1_B
        test_F1_B_positive_decreasing_ranges,
        test_F1_B_negative_no_trend_b,
        test_F1_B_negative_ranges_increasing,
        # F2_B
        test_F2_B_positive_high_vol_low_body,
        test_F2_B_negative_no_trend_b,
        test_F2_B_negative_balanced_vol_body,
        # F3_B
        test_F3_B_positive_uptrend_positive_delta_bearish_bar,
        test_F3_B_negative_no_delta_column,
        test_F3_B_negative_delta_aligned_with_bar,
        # F5_A
        test_F5_A_positive_long_weak_close,
        test_F5_A_negative_no_trend_a,
        test_F5_A_negative_strong_close_in_trend,
        # F5_B
        test_F5_B_positive_short_weak_close,
        test_F5_B_negative_no_trend_b,
        test_F5_B_direction_fallback_when_dir_str_empty,
        # F4 Volume Climax (EXEC-6 IMPL-4)
        test_F4_positive_climax_and_trend_a,
        test_F4_negative_no_trend_a,
        test_F4_negative_volume_within_normal_range,
        test_F4_warmup_insufficient,
        test_F4_missing_volume_column,
        # evaluate_all
        test_evaluate_all_returns_complete_schema_for_empty_df,
        test_evaluate_all_returns_complete_schema_for_none_df,
        test_evaluate_all_keys_always_present,
    ]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, "AssertionError"))
            print(f"  FAIL  {t.__name__}")
        except Exception as e:
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print()
    print(f"Total: {passed}/{len(tests)} passed")
    if failed:
        for name, why in failed:
            print(f"  FAILED: {name} — {why}")
        sys.exit(1)
    sys.exit(0)

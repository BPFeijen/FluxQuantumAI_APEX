"""
Unit tests for m30_bias F-1 + F-3 hysteresis fix (Opção B).

Spec: _audit/fixes/opcao_b_phase1_m30_bias_hysteresis_spec.md
Calibration: _audit/calibrations/m30_bias_voting_calibration.md
Calibrated defaults: min_bars=5, window=5, strategy=recency_weighted

Tests T1-T15 per spec §6.1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live import level_detector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_voting(monkeypatch, *, min_bars=5, window=5, strategy="recency_weighted"):
    """Pin voting settings without touching disk."""
    monkeypatch.setattr(
        level_detector,
        "_load_m30_bias_voting_settings",
        lambda: (min_bars, window, strategy),
    )


def _box_row(box_id, *, confirmed=True, box_high=100.0, box_low=90.0,
             liq_top=None, liq_bot=None):
    if liq_top is None:
        liq_top = box_high
    if liq_bot is None:
        liq_bot = box_low
    return {
        "m30_box_id": box_id,
        "m30_box_confirmed": confirmed,
        "m30_box_high": box_high,
        "m30_box_low": box_low,
        "m30_liq_top": liq_top,
        "m30_liq_bot": liq_bot,
    }


def _bull_box(box_id, bars=5, *, box_high=100.0, box_low=90.0, top_excursion=2.0):
    """Generate `bars` rows for a confirmed bullish box (bull_ext only)."""
    return [
        _box_row(
            box_id,
            confirmed=True,
            box_high=box_high,
            box_low=box_low,
            liq_top=box_high + top_excursion,
            liq_bot=box_low,  # equal -> NOT bear_ext
        )
        for _ in range(bars)
    ]


def _bear_box(box_id, bars=5, *, box_high=100.0, box_low=90.0, bot_excursion=2.0):
    return [
        _box_row(
            box_id,
            confirmed=True,
            box_high=box_high,
            box_low=box_low,
            liq_top=box_high,  # equal -> NOT bull_ext
            liq_bot=box_low - bot_excursion,
        )
        for _ in range(bars)
    ]


def _unknown_box(box_id, bars=5, *, box_high=100.0, box_low=90.0):
    """Both bull_ext and bear_ext -> unknown classification."""
    return [
        _box_row(
            box_id,
            confirmed=True,
            box_high=box_high,
            box_low=box_low,
            liq_top=box_high + 2.0,
            liq_bot=box_low - 2.0,
        )
        for _ in range(bars)
    ]


def _df(rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Helper-level pure tests
# ---------------------------------------------------------------------------

def test_voting_vote_majority_strict_majority_bullish():
    assert level_detector._voting_vote(["bullish", "bullish", "bearish"], "majority") == "bullish"


def test_voting_vote_recency_weighted_tied_unknown():
    # weights [1,2,3]; bullish=[0,1] -> bull_w=3; bearish=[2] -> bear_w=3
    assert level_detector._voting_vote(
        ["bullish", "bullish", "bearish"], "recency_weighted"
    ) == "unknown"


def test_classify_with_min_bars_bars_below_threshold_returns_unknown():
    row = _bull_box(99, bars=1)[0]
    assert level_detector._classify_with_min_bars(row, bars_in_box=3, min_bars=4) == "unknown"


def test_classify_with_min_bars_bars_meets_threshold_returns_classification():
    row = _bull_box(99, bars=1)[0]
    assert level_detector._classify_with_min_bars(row, bars_in_box=5, min_bars=4) == "bullish"


def test_bars_in_box_counts_rows_for_id():
    df = _df(_bull_box(1, bars=3) + _bear_box(2, bars=7))
    assert level_detector._bars_in_box(df, 1) == 3
    assert level_detector._bars_in_box(df, 2) == 7
    assert level_detector._bars_in_box(df, 999) == 0


# ---------------------------------------------------------------------------
# T1 -- T15 per spec §6.1
# ---------------------------------------------------------------------------

def test_T1_empty_df_returns_unknown(monkeypatch):
    _set_voting(monkeypatch)
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    assert level_detector.derive_m30_bias(None) == ("unknown", False)
    assert level_detector.derive_m30_bias(pd.DataFrame()) == ("unknown", False)


def test_T2_window_truncation_when_fewer_boxes(monkeypatch):
    """2 confirmed boxes, window=3 -> vote over 2."""
    _set_voting(monkeypatch, min_bars=4, window=3, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bull_box(1, bars=5) + _bull_box(2, bars=5)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=False)
    assert bias == "bullish"
    assert is_conf is True


def test_T3_single_box_min_bars_pass(monkeypatch):
    """1 box, 6 bars, bullish ext -> ('bullish', True)."""
    _set_voting(monkeypatch, min_bars=4, window=5, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bull_box(1, bars=6)
    assert level_detector.derive_m30_bias(_df(rows)) == ("bullish", True)


def test_T4_single_box_min_bars_fail(monkeypatch):
    """1 box, 3 bars, bearish ext, min_bars=4 -> ('unknown', False)."""
    _set_voting(monkeypatch, min_bars=4, window=5, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bear_box(1, bars=3)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=True)
    assert bias == "unknown"
    assert is_conf is False


def test_T5_all_unknown_returns_unknown(monkeypatch):
    """3 boxes all unknown -> unknown."""
    _set_voting(monkeypatch, min_bars=4, window=3, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _unknown_box(1, bars=5) + _unknown_box(2, bars=5) + _unknown_box(3, bars=5)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=True)
    assert bias == "unknown"
    assert is_conf is False


def test_T6_majority_strict_tie_returns_unknown(monkeypatch):
    """1 bullish, 1 bearish, 1 unknown, majority strategy -> unknown.
    NOTE: confirmed_only=True so we get the confirmed-path verdict only."""
    _set_voting(monkeypatch, min_bars=4, window=3, strategy="majority")
    # current_gc inside a price range that doesn't override
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bull_box(1, bars=5) + _bear_box(2, bars=5) + _unknown_box(3, bars=5)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=True)
    assert bias == "unknown"
    assert is_conf is False


def test_T7_recency_weighted_tie_returns_unknown(monkeypatch):
    """3 boxes [bullish, bullish, bearish]: bull_w=3 vs bear_w=3, ratio<1.5 -> unknown."""
    _set_voting(monkeypatch, min_bars=4, window=3, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bull_box(1, bars=5) + _bull_box(2, bars=5) + _bear_box(3, bars=5)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=True)
    assert bias == "unknown"
    assert is_conf is False


def test_T8_recency_weighted_all_bullish(monkeypatch):
    """3 bullish boxes -> bullish."""
    _set_voting(monkeypatch, min_bars=4, window=3, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bull_box(1, bars=5) + _bull_box(2, bars=5) + _bull_box(3, bars=5)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=False)
    assert bias == "bullish"
    assert is_conf is True


def test_T9_confirmed_only_unknown_early_exit(monkeypatch):
    """confirmed_only=True + voted=unknown -> ('unknown', False).
    Use 3 unknown boxes so vote returns unknown unambiguously."""
    _set_voting(monkeypatch, min_bars=4, window=3, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _unknown_box(1, bars=5) + _unknown_box(2, bars=5) + _unknown_box(3, bars=5)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=True)
    assert bias == "unknown"
    assert is_conf is False


def test_T10_live_structure_overrides_vote(monkeypatch):
    """Voted=bullish but price <<< box_low -> structural disagreement -> falls through.
    Provisional path then sees latest row and returns provisional bias.
    Use confirmed_only=True to assert the confirmed-path was invalidated."""
    _set_voting(monkeypatch, min_bars=4, window=3, strategy="recency_weighted")
    # current_gc << every box_low -> structural override = bearish
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 50.0)
    rows = _bull_box(1, bars=5) + _bull_box(2, bars=5)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=True)
    assert bias == "unknown"
    assert is_conf is False


def test_T11_settings_defaults_when_missing(monkeypatch, tmp_path):
    """settings.json missing -> uses defaults min_bars=5, window=5, recency_weighted."""
    missing = tmp_path / "does_not_exist.json"
    monkeypatch.setattr(level_detector, "_M30_BIAS_SETTINGS_PATH", missing)
    mb, w, s = level_detector._load_m30_bias_voting_settings()
    assert (mb, w, s) == (5, 5, "recency_weighted")


def test_T12_invalid_strategy_falls_back(monkeypatch, tmp_path, caplog):
    """strategy='invalid' -> uses recency_weighted; logs one warn."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({
        "m30_bias_min_bars": 4,
        "m30_bias_voting_window": 3,
        "m30_bias_voting_strategy": "garbage",
    }), encoding="utf-8")
    monkeypatch.setattr(level_detector, "_M30_BIAS_SETTINGS_PATH", cfg_path)
    # Reset warn dedupe so this test can assert the warning
    monkeypatch.setattr(level_detector, "_M30_BIAS_VOTING_WARN_LOGGED", set())
    mb, w, s = level_detector._load_m30_bias_voting_settings()
    assert (mb, w, s) == (4, 3, "recency_weighted")


def test_T13_box_5282_replay_blocked(monkeypatch):
    """Box 5282 episode (2026-05-07 04-05 UTC):
       last 5 confirmed boxes ~ [unknown(8), unknown(3-bar bear), unknown(2-bar bull), ...]
       under min_bars=5 + recency_weighted -> all classifications 'unknown' -> overall 'unknown'.
    Reproducible smaller-scale: 5281 (8 bars unknown) + 5282 (3 bars bear-only) + 5283 (2 bars bull-only).
    All <5 bars except 5281 (which is unknown anyway) -> all unknown -> unknown."""
    _set_voting(monkeypatch, min_bars=5, window=5, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = (
        _unknown_box(5281, bars=8)        # 8 bars, unknown
        + _bear_box(5282, bars=3)         # 3 bars, bearish but <5 -> forced unknown
        + _bull_box(5283, bars=2)         # 2 bars, bullish but <5 -> forced unknown
    )
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=True)
    assert bias == "unknown"
    assert is_conf is False


def test_T14_clear_bull_three_box_cycle_passes(monkeypatch):
    """3 boxes all bull-ext, all >=5 bars -> ('bullish', True)."""
    _set_voting(monkeypatch, min_bars=5, window=3, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bull_box(1, bars=6) + _bull_box(2, bars=5) + _bull_box(3, bars=7)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=False)
    assert bias == "bullish"
    assert is_conf is True


def test_T15_clear_bear_three_box_cycle_passes(monkeypatch):
    """3 boxes all bear-ext, all >=5 bars -> ('bearish', True)."""
    _set_voting(monkeypatch, min_bars=5, window=3, strategy="recency_weighted")
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)
    rows = _bear_box(1, bars=6) + _bear_box(2, bars=5) + _bear_box(3, bars=7)
    bias, is_conf = level_detector.derive_m30_bias(_df(rows), confirmed_only=False)
    assert bias == "bearish"
    assert is_conf is True

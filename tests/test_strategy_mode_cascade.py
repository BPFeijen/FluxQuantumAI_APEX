"""
test_strategy_mode_cascade.py — 5-layer cascade resolver tests.

Spec: _audit/fixes/bug_cascade_direction_fallback_spec.md §6.1
Asana: 1214556369070092 (BUG-SIGNAL-INVERTED) / ML-DS approval 1214587362771532

Layer matrix:
1. daily_trend (B+C ENSEMBLE) — HIGH
2. TickBreakoutMonitor.status() state ∈ (BREAKOUT_UP, BREAKOUT_DN) — HIGH
3. m30_bias_confirmed — MEDIUM
4. provisional_m30_bias (telemetry → soft-block upgrade) — LOW
5. unknown → RANGE_BOUND — NONE
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure live/ is importable
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live.event_processor import EventProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(daily_trend="unknown",
               m30_bias="unknown",
               m30_bias_confirmed=False,
               provisional_m30_bias="unknown",
               tick_breakout_state=None,
               flag_use_tick_breakout=True,
               flag_use_provisional=True):
    """Build a minimal EventProcessor stub with only fields needed for resolver."""
    proc = EventProcessor.__new__(EventProcessor)
    proc.daily_trend = daily_trend
    proc.m30_bias = m30_bias
    proc.m30_bias_confirmed = m30_bias_confirmed
    proc.provisional_m30_bias = provisional_m30_bias
    proc._thresholds = {
        "direction_fallback_use_tick_breakout": flag_use_tick_breakout,
        "direction_fallback_use_provisional_m30": flag_use_provisional,
        "dual_strategy_enabled": True,
    }
    if tick_breakout_state is not None:
        tb = MagicMock()
        if tick_breakout_state == "_RAISE":
            tb.status.side_effect = RuntimeError("simulated failure")
        else:
            tb.status.return_value = {"state": tick_breakout_state}
        proc._tick_breakout = tb
    else:
        proc._tick_breakout = None
    return proc


# ---------------------------------------------------------------------------
# Layer 1 — daily_trend (B+C ENSEMBLE) HIGH
# ---------------------------------------------------------------------------

def test_layer1_daily_trend_long():
    proc = _make_proc(daily_trend="long")
    assert proc._resolve_trend_direction() == ("long", "daily_trend_b_c_ensemble", "HIGH")


def test_layer1_daily_trend_short():
    proc = _make_proc(daily_trend="short")
    assert proc._resolve_trend_direction() == ("short", "daily_trend_b_c_ensemble", "HIGH")


# ---------------------------------------------------------------------------
# Layer 2 — TickBreakoutMonitor HIGH
# ---------------------------------------------------------------------------

def test_layer2_tick_breakout_up_when_layer1_unknown():
    proc = _make_proc(daily_trend="unknown", tick_breakout_state="BREAKOUT_UP")
    trend, source, conf = proc._resolve_trend_direction()
    assert trend == "long"
    assert source == "tick_breakout_monitor"
    assert conf == "HIGH"


def test_layer2_tick_breakout_dn_when_layer1_unknown():
    proc = _make_proc(daily_trend="unknown", tick_breakout_state="BREAKOUT_DN")
    trend, source, conf = proc._resolve_trend_direction()
    assert trend == "short"
    assert source == "tick_breakout_monitor"
    assert conf == "HIGH"


def test_layer2_skip_when_state_contraction():
    """tb.state=CONTRACTION → SKIP layer; layer 3 fires instead."""
    proc = _make_proc(
        daily_trend="unknown",
        tick_breakout_state="CONTRACTION",
        m30_bias="bullish",
        m30_bias_confirmed=True,
    )
    trend, source, conf = proc._resolve_trend_direction()
    assert source == "m30_bias_confirmed"
    assert trend == "long"
    assert conf == "MEDIUM"


def test_layer2_skip_when_state_candidate_up():
    proc = _make_proc(
        daily_trend="unknown",
        tick_breakout_state="CANDIDATE_UP",
        m30_bias="bullish",
        m30_bias_confirmed=True,
    )
    _, source, _ = proc._resolve_trend_direction()
    assert source == "m30_bias_confirmed"


def test_layer2_skip_when_module_none():
    proc = _make_proc(
        daily_trend="unknown",
        tick_breakout_state=None,  # _tick_breakout=None
        m30_bias="bearish",
        m30_bias_confirmed=True,
    )
    trend, source, _ = proc._resolve_trend_direction()
    assert source == "m30_bias_confirmed"
    assert trend == "short"


def test_layer2_skip_when_status_exception():
    proc = _make_proc(
        daily_trend="unknown",
        tick_breakout_state="_RAISE",
        m30_bias="bullish",
        m30_bias_confirmed=True,
    )
    trend, source, _ = proc._resolve_trend_direction()
    assert source == "m30_bias_confirmed"
    assert trend == "long"


def test_layer2_skip_when_flag_disabled():
    proc = _make_proc(
        daily_trend="unknown",
        tick_breakout_state="BREAKOUT_UP",
        flag_use_tick_breakout=False,
        m30_bias="bearish",
        m30_bias_confirmed=True,
    )
    trend, source, _ = proc._resolve_trend_direction()
    assert source == "m30_bias_confirmed"
    assert trend == "short"


# ---------------------------------------------------------------------------
# Layer 3 — m30_bias_confirmed MEDIUM
# ---------------------------------------------------------------------------

def test_layer3_m30_confirmed_bullish():
    proc = _make_proc(daily_trend="unknown", m30_bias="bullish", m30_bias_confirmed=True)
    assert proc._resolve_trend_direction() == ("long", "m30_bias_confirmed", "MEDIUM")


def test_layer3_m30_confirmed_bearish():
    proc = _make_proc(daily_trend="unknown", m30_bias="bearish", m30_bias_confirmed=True)
    assert proc._resolve_trend_direction() == ("short", "m30_bias_confirmed", "MEDIUM")


def test_layer3_skip_when_not_confirmed():
    """confirmed=False, layer 3 SKIP, fallback to layer 4 provisional."""
    proc = _make_proc(
        daily_trend="unknown",
        m30_bias="bullish",
        m30_bias_confirmed=False,
        provisional_m30_bias="bullish",
    )
    trend, source, conf = proc._resolve_trend_direction()
    assert source == "provisional_m30_bias"
    assert trend == "long"
    assert conf == "LOW"


def test_layer3_skip_when_neutral():
    """confirmed=True but m30_bias=neutral, skip → layer 4."""
    proc = _make_proc(
        daily_trend="unknown",
        m30_bias="neutral",
        m30_bias_confirmed=True,
        provisional_m30_bias="bearish",
    )
    trend, source, _ = proc._resolve_trend_direction()
    assert source == "provisional_m30_bias"
    assert trend == "short"


# ---------------------------------------------------------------------------
# Layer 4 — provisional_m30_bias LOW (telemetry → soft-block upgrade)
# ---------------------------------------------------------------------------

def test_layer4_provisional_bullish():
    proc = _make_proc(daily_trend="unknown", provisional_m30_bias="bullish")
    assert proc._resolve_trend_direction() == ("long", "provisional_m30_bias", "LOW")


def test_layer4_provisional_bearish():
    proc = _make_proc(daily_trend="unknown", provisional_m30_bias="bearish")
    assert proc._resolve_trend_direction() == ("short", "provisional_m30_bias", "LOW")


def test_layer4_skip_when_unknown():
    proc = _make_proc(daily_trend="unknown", provisional_m30_bias="unknown")
    assert proc._resolve_trend_direction() == ("unknown", "no_trend_signal", "NONE")


def test_layer4_skip_when_flag_disabled():
    proc = _make_proc(
        daily_trend="unknown",
        provisional_m30_bias="bullish",
        flag_use_provisional=False,
    )
    assert proc._resolve_trend_direction() == ("unknown", "no_trend_signal", "NONE")


# ---------------------------------------------------------------------------
# Layer 5 — Default (all layers exhausted)
# ---------------------------------------------------------------------------

def test_layer5_default_unknown():
    proc = _make_proc(daily_trend="unknown")
    assert proc._resolve_trend_direction() == ("unknown", "no_trend_signal", "NONE")


# ---------------------------------------------------------------------------
# Thread safety — concurrent access does not raise
# ---------------------------------------------------------------------------

def test_thread_safety():
    proc = _make_proc(daily_trend="unknown", m30_bias="bullish", m30_bias_confirmed=True)
    errors: list = []

    def worker():
        try:
            for _ in range(100):
                trend, source, conf = proc._resolve_trend_direction()
                # Mutate provisional concurrently to exercise getattr path
                proc.provisional_m30_bias = "bullish" if proc.provisional_m30_bias != "bullish" else "bearish"
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"thread safety violated: {errors[:3]}"


# ---------------------------------------------------------------------------
# Strategy-mode integration (phase × resolved interaction)
# ---------------------------------------------------------------------------

def test_strategy_mode_phase_contraction_force_range(monkeypatch):
    """Phase=CONTRACTION → always RANGE_BOUND regardless of resolved."""
    proc = _make_proc(daily_trend="long")
    monkeypatch.setattr(proc, "_get_current_phase", lambda: "CONTRACTION")
    proc._last_phase = "CONTRACTION"
    mode, direction = proc._get_strategy_mode()
    assert mode == "RANGE_BOUND"
    assert direction is None


def test_strategy_mode_phase_expansion_layer2_trending(monkeypatch):
    """Phase=EXPANSION + layer 2 HIT → TRENDING with layer 2 trend."""
    proc = _make_proc(daily_trend="unknown", tick_breakout_state="BREAKOUT_UP")
    monkeypatch.setattr(proc, "_get_current_phase", lambda: "EXPANSION")
    mode, direction = proc._get_strategy_mode()
    assert mode == "TRENDING"
    assert direction == "LONG"


def test_strategy_mode_phase_expansion_layer4_trending(monkeypatch):
    """Phase=EXPANSION + only layer 4 fires → TRENDING."""
    proc = _make_proc(daily_trend="unknown", provisional_m30_bias="bearish")
    monkeypatch.setattr(proc, "_get_current_phase", lambda: "EXPANSION")
    mode, direction = proc._get_strategy_mode()
    assert mode == "TRENDING"
    assert direction == "SHORT"


def test_strategy_mode_no_resolved_falls_to_range(monkeypatch):
    """All layers SKIP in EXPANSION → RANGE_BOUND fallback."""
    proc = _make_proc(daily_trend="unknown")
    monkeypatch.setattr(proc, "_get_current_phase", lambda: "EXPANSION")
    mode, direction = proc._get_strategy_mode()
    assert mode == "RANGE_BOUND"
    assert direction is None


# ---------------------------------------------------------------------------
# F-asymmetric bias filter (RANGE_BOUND counter-trend block)
# ML-DS 1214590148833737 — Wade canon enforcement
# ---------------------------------------------------------------------------

def _patch_range_bound(proc):
    """Force strategy_mode=RANGE_BOUND and CONTRACTION phase for these tests."""
    proc._get_strategy_mode = lambda: ("RANGE_BOUND", None)
    proc._get_current_phase = lambda: "CONTRACTION"
    proc._last_phase = "CONTRACTION"


def test_range_bias_blocks_short_when_resolved_long_via_provisional():
    """Counter-bull SHORT in CONTRACTION must be blocked when provisional bullish."""
    proc = _make_proc(
        daily_trend="unknown",
        provisional_m30_bias="bullish",
    )
    _patch_range_bound(proc)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction is None
    assert "counter-bull" in reason.lower() or "RANGE_BOUND_BIAS_BLOCK" in reason


def test_range_bias_blocks_long_when_resolved_short_via_provisional():
    """Counter-bear LONG in CONTRACTION must be blocked when provisional bearish."""
    proc = _make_proc(
        daily_trend="unknown",
        provisional_m30_bias="bearish",
    )
    _patch_range_bound(proc)
    direction, reason = proc._resolve_direction(level_type="liq_bot")
    assert direction is None
    assert "counter-bear" in reason.lower() or "RANGE_BOUND_BIAS_BLOCK" in reason


def test_range_bias_allows_aligned_short_when_resolved_short():
    """Aligned SHORT@liq_top in CONTRACTION + bearish bias → allowed."""
    proc = _make_proc(
        daily_trend="unknown",
        provisional_m30_bias="bearish",
    )
    _patch_range_bound(proc)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction == "SHORT"
    assert "RANGE_BOUND" in reason


def test_range_bias_allows_aligned_long_when_resolved_long():
    """Aligned LONG@liq_bot in CONTRACTION + bullish bias → allowed."""
    proc = _make_proc(
        daily_trend="unknown",
        provisional_m30_bias="bullish",
    )
    _patch_range_bound(proc)
    direction, reason = proc._resolve_direction(level_type="liq_bot")
    assert direction == "LONG"
    assert "RANGE_BOUND" in reason


def test_range_bias_inactive_when_resolved_unknown():
    """resolved=unknown → Strategy 1 mean-reversion preserved (no filter)."""
    proc = _make_proc(daily_trend="unknown")
    _patch_range_bound(proc)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction == "SHORT"
    assert "RANGE_BOUND" in reason


def test_range_bias_disabled_via_flag():
    """Flag off → bias filter inactive even when resolved bullish."""
    proc = _make_proc(daily_trend="unknown", provisional_m30_bias="bullish")
    proc._thresholds["range_bound_bias_filter_enabled"] = False
    _patch_range_bound(proc)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction == "SHORT"
    assert "BIAS_BLOCK" not in reason


# ---------------------------------------------------------------------------
# F-asymmetric TRENDING extension (ML-DS 1214598376829822)
# ---------------------------------------------------------------------------

def _patch_trending(proc, trend_dir, *, liq_top=4700.0, liq_bot=4680.0, xau_mid=4750.0, atr_m30=10.0):
    """Force strategy_mode=TRENDING and TREND phase, with overextended price."""
    proc._get_strategy_mode = lambda: ("TRENDING", trend_dir)
    proc._get_current_phase = lambda: "TREND"
    proc._last_phase = "TREND"
    proc.liq_top = liq_top
    proc.liq_bot = liq_bot
    proc._metrics = {"xau_mid": xau_mid, "atr_m30_parquet": atr_m30, "atr": atr_m30}
    # Force _get_trend_entry_mode to return SKIP so we hit the overextension path
    proc._get_trend_entry_mode = lambda level_type, price, td, decision=None: ("SKIP", None, "skipped for test")


def test_trending_bias_blocks_short_when_resolved_long():
    """TRENDING_LONG + overext SHORT (counter-bull) must be blocked when cascade resolves bullish."""
    proc = _make_proc(daily_trend="long")  # Layer 1 long
    _patch_trending(proc, "LONG", liq_top=4700.0, xau_mid=4720.0, atr_m30=5.0)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction is None
    assert "TRENDING_BIAS_BLOCK" in reason
    assert "counter-bull" in reason.lower()


def test_trending_bias_blocks_long_when_resolved_short():
    """TRENDING_SHORT + overext LONG (counter-bear) must be blocked when cascade resolves bearish."""
    proc = _make_proc(daily_trend="short")
    _patch_trending(proc, "SHORT", liq_bot=4700.0, xau_mid=4680.0, atr_m30=5.0)
    direction, reason = proc._resolve_direction(level_type="liq_bot")
    assert direction is None
    assert "TRENDING_BIAS_BLOCK" in reason
    assert "counter-bear" in reason.lower()


def test_trending_bias_allows_when_not_overextended():
    """TRENDING_LONG + price NOT overextended → SKIP (not BIAS_BLOCK)."""
    proc = _make_proc(daily_trend="long")
    # xau_mid only 2pts above liq_top, ATR 10 * 1.5 = 15 threshold → not overextended
    _patch_trending(proc, "LONG", liq_top=4700.0, xau_mid=4702.0, atr_m30=10.0)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction is None
    assert "BIAS_BLOCK" not in reason
    assert "SKIP" in reason or "liquidation zone" in reason


def test_trending_bias_disabled_via_flag():
    """Flag off → TRENDING bias filter inactive even when overextended counter-trend."""
    proc = _make_proc(daily_trend="long")
    proc._thresholds["range_bound_bias_filter_enabled"] = False
    _patch_trending(proc, "LONG", liq_top=4700.0, xau_mid=4720.0, atr_m30=5.0)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction == "SHORT"
    assert "BIAS_BLOCK" not in reason


def test_trending_bias_blocks_via_layer4_provisional():
    """Layer 4 provisional bullish should also trigger the TRENDING block (LOW confidence respected)."""
    proc = _make_proc(daily_trend="unknown", provisional_m30_bias="bullish")
    _patch_trending(proc, "LONG", liq_top=4700.0, xau_mid=4720.0, atr_m30=5.0)
    direction, reason = proc._resolve_direction(level_type="liq_top")
    assert direction is None
    assert "TRENDING_BIAS_BLOCK" in reason

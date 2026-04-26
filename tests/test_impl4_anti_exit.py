"""
Unit tests for IMPL-4 (EXEC-6 2026-04-26):
  - PositionMonitor.set_market_state IPC injection
  - PositionMonitor._fire_feat4_veto helper
    (covers profit gate, market_state lookup, FEAT-4 active check,
    fail-soft behaviour)

Side effects (telegram send + position_events.jsonl write) are intercepted
via monkey-patch so the tests remain self-contained.

Run:
    python tests/test_impl4_anti_exit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Stub MetaTrader5 import path used by position_monitor.py module-level imports
# is not required because PositionMonitor only imports MT5 lazily inside methods
# we don't invoke. Stub executor below provides the minimum surface.
from live.position_monitor import PositionMonitor


# ----------------------------------------------------------------------------
# Stubs
# ----------------------------------------------------------------------------

class _StubExecutor:
    is_live = False


class _MarketStateStub:
    """Mimics the subset of MarketEventProcessor that _fire_feat4_veto reads."""
    def __init__(self, active=False, patterns=None, time_limit_min=5):
        self._feat_4_anti_exit_active  = active
        self._feat_4_observed_patterns = patterns or []
        self._feat_4_time_limit_min    = time_limit_min


def _build_pm():
    """Build a PositionMonitor with stub executor (no MT5 needed)."""
    return PositionMonitor(executor=_StubExecutor(), dry_run=True,
                           lot_size=0.05, executor_live=None)


def _silence_side_effects(pm, captured):
    """Monkey-patch telegram + jsonl write so tests don't perform IO.

    captured: dict updated with what _fire_feat4_veto would emit so tests
    can assert side-effects fired. Telegram is patched at module level;
    jsonl writes are intercepted only when targetting POSITION_EVENTS_LOG
    (other open() calls are passed through to the real builtin so
    PositionMonitor.__init__ heartbeats keep working in subsequent
    test instantiations).
    """
    import live.telegram_notifier as tg
    captured["telegram_calls"] = []
    def _fake_notify(**kwargs):
        captured["telegram_calls"].append(kwargs)
    tg.notify_feat4_veto = _fake_notify

    import live.position_monitor as pm_mod
    import builtins as _bi
    captured["jsonl_writes"] = []
    _orig_open = _bi.open
    target_path = str(pm_mod.POSITION_EVENTS_LOG)
    class _StubFh:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def write(self, s): captured["jsonl_writes"].append(s)
    def _selective_open(path, *args, **kwargs):
        # Intercept only writes destined to POSITION_EVENTS_LOG; pass
        # everything else (including PositionMonitor.__init__ heartbeats
        # opened with buffering=1) through to the real builtin.
        if str(path) == target_path:
            return _StubFh()
        return _orig_open(path, *args, **kwargs)
    pm_mod.open = _selective_open


# ============================================================================
# set_market_state — IPC injection
# ============================================================================

def test_set_market_state_idempotent():
    pm = _build_pm()
    ms1 = _MarketStateStub(active=False)
    ms2 = _MarketStateStub(active=True)
    pm.set_market_state(ms1)
    assert pm.market_state is ms1
    pm.set_market_state(ms2)   # second call replaces
    assert pm.market_state is ms2


# ============================================================================
# _fire_feat4_veto — gate matrix
# ============================================================================

def test_veto_no_market_state_returns_false():
    pm = _build_pm()
    # Don't inject market_state
    pos = {"ticket": 1, "entry": 4700.0, "sl": 4690.0}
    result = pm._fire_feat4_veto("_check_l2_danger", pos, "LONG", 4710.0)
    assert result is False


def test_veto_inactive_market_state_returns_false():
    pm = _build_pm()
    pm.set_market_state(_MarketStateStub(active=False))
    pos = {"ticket": 1, "entry": 4700.0, "sl": 4690.0}
    result = pm._fire_feat4_veto("_check_l2_danger", pos, "LONG", 4710.0)
    assert result is False


def test_veto_underwater_long_blocked():
    pm = _build_pm()
    pm.set_market_state(_MarketStateStub(active=True,
                                         patterns=["Volume climax"]))
    pos = {"ticket": 99, "entry": 4700.0, "sl": 4690.0}
    # LONG underwater: cur < entry → veto must NOT fire
    captured = {}
    _silence_side_effects(pm, captured)
    result = pm._fire_feat4_veto("_check_l2_danger", pos, "LONG", 4695.0)
    assert result is False
    assert captured["telegram_calls"] == []
    assert captured["jsonl_writes"] == []


def test_veto_underwater_short_blocked():
    pm = _build_pm()
    pm.set_market_state(_MarketStateStub(active=True,
                                         patterns=["Volume climax"]))
    pos = {"ticket": 99, "entry": 4700.0, "sl": 4710.0}
    # SHORT underwater: cur > entry → no veto
    captured = {}
    _silence_side_effects(pm, captured)
    result = pm._fire_feat4_veto("_check_regime_flip", pos, "SHORT", 4705.0)
    assert result is False
    assert captured["telegram_calls"] == []


def test_veto_long_in_profit_fires():
    pm = _build_pm()
    pm.set_market_state(_MarketStateStub(active=True,
                                         patterns=["Volume climax",
                                                   "Weak close"],
                                         time_limit_min=5))
    pos = {"ticket": 1234567, "entry": 4700.0, "sl": 4690.0}
    captured = {}
    _silence_side_effects(pm, captured)
    result = pm._fire_feat4_veto("_check_cascade", pos, "LONG", 4715.50)
    assert result is True
    # Telegram fired exactly once with the correct unrealized direction
    assert len(captured["telegram_calls"]) == 1
    call = captured["telegram_calls"][0]
    assert call["signal_id"] == "1234567"
    assert call["direction"] == "LONG"
    assert call["entry_price"] == 4700.0
    assert call["current_price"] == 4715.5
    assert call["hard_stop"] == 4690.0
    assert call["observable_patterns"] == ["Volume climax", "Weak close"]
    assert call["time_limit_min"] == 5
    # jsonl wrote one event line with the right shape
    assert len(captured["jsonl_writes"]) == 1
    line = captured["jsonl_writes"][0]
    assert "FEAT_4_VETO" in line
    assert "_check_cascade" in line
    assert "1234567" in line


def test_veto_short_in_profit_fires():
    pm = _build_pm()
    pm.set_market_state(_MarketStateStub(active=True,
                                         patterns=["Volume climax"]))
    pos = {"ticket": 9876, "entry": 4750.0, "sl": 4760.0}
    captured = {}
    _silence_side_effects(pm, captured)
    # SHORT in profit: cur < entry
    result = pm._fire_feat4_veto("_check_t3_defense_exit", pos, "SHORT", 4742.0)
    assert result is True
    assert len(captured["telegram_calls"]) == 1
    assert captured["telegram_calls"][0]["direction"] == "SHORT"


def test_veto_unknown_direction_treated_as_no_profit():
    pm = _build_pm()
    pm.set_market_state(_MarketStateStub(active=True))
    pos = {"ticket": 1, "entry": 4700.0, "sl": 4690.0}
    captured = {}
    _silence_side_effects(pm, captured)
    # Direction "" / unknown — both LONG/SHORT branches false → not in profit
    result = pm._fire_feat4_veto("_check_l2_danger", pos, "UNKNOWN", 4720.0)
    assert result is False


def test_veto_telegram_failure_swallowed():
    """Telegram exception inside helper must NOT break veto verdict."""
    pm = _build_pm()
    pm.set_market_state(_MarketStateStub(active=True,
                                         patterns=["Volume climax"]))
    pos = {"ticket": 1, "entry": 4700.0, "sl": 4690.0}
    captured = {}
    _silence_side_effects(pm, captured)
    import live.telegram_notifier as tg
    def _boom(**kw): raise RuntimeError("simulated telegram down")
    tg.notify_feat4_veto = _boom
    # Should still return True (veto fires) and not raise
    result = pm._fire_feat4_veto("_check_l2_danger", pos, "LONG", 4710.0)
    assert result is True


# ============================================================================
# Manual runner
# ============================================================================

if __name__ == "__main__":
    tests = [
        test_set_market_state_idempotent,
        test_veto_no_market_state_returns_false,
        test_veto_inactive_market_state_returns_false,
        test_veto_underwater_long_blocked,
        test_veto_underwater_short_blocked,
        test_veto_long_in_profit_fires,
        test_veto_short_in_profit_fires,
        test_veto_unknown_direction_treated_as_no_profit,
        test_veto_telegram_failure_swallowed,
    ]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError:
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

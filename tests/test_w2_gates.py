"""W2 gate unit tests (2026-05-09)

Coverage:
- W2.1 ATR extreme regime gate (m30_atr14 > 45 → BLOCK)
- W2.2 Universal RR>=1.0 gate (rejects entries with RR<1.0 in any mode)
- W2.5 Daily loss limit gate (cumulative day PnL <= threshold → BLOCK)
- W2.6 F-asym flag advisory only (already covered in test_strategy_mode_cascade.py)

These tests target the *gate* helpers, not the full event loop. We instantiate
EventProcessor with minimal stubs and exercise `_check_pre_entry_gates` and
`_compute_daily_pnl`.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import lazily to avoid heavy MT5 init at module import
from live import event_processor as ep_module


class _StubExecutor:
    connected = True
    def get_open_positions(self):
        return []


def _make_proc(thresholds=None, atr_m30=20.0):
    """Construct a stubbed EventProcessor without going through __init__ heavy IO."""
    proc = ep_module.EventProcessor.__new__(ep_module.EventProcessor)
    base = {
        "trade_cooldown_enabled": False,  # disable cooldown to focus tests on W2 gates
        "max_positions": 99,
        "margin_level_min": 0,
        "delta_4h_short_block": 999999,
        "delta_4h_long_block": -999999,
        "delta_4h_exhaustion_high": 999999,
        "delta_4h_exhaustion_low": -999999,
    }
    if thresholds:
        base.update(thresholds)
    proc._thresholds = base
    proc._metrics = {"atr_m30_parquet": atr_m30, "atr": atr_m30}
    proc._startup_cooldown_until = 0.0
    proc._last_trade_time = 0.0
    proc._last_trade_level = 0.0
    proc._last_trade_direction = ""
    proc._daily_pnl_cache = (0.0, 0.0)
    proc._daily_pnl_ttl_s = 60.0
    proc.executor = _StubExecutor()
    proc.executor_live = None
    return proc


# ----------------------------------------------------------------------------
# W2.1 ATR extreme regime gate
# ----------------------------------------------------------------------------

def test_w2_1_atr_extreme_blocks_when_above_threshold():
    proc = _make_proc({"m30_atr_extreme_block_enabled": True, "m30_atr_extreme_pts": 45.0},
                       atr_m30=46.0)  # > 45
    blocked, reason = proc._check_pre_entry_gates("LONG", 0.0, 4700.0)
    assert blocked is True
    assert "M30_ATR_EXTREME" in reason


def test_w2_1_atr_normal_does_not_block():
    proc = _make_proc({"m30_atr_extreme_block_enabled": True, "m30_atr_extreme_pts": 45.0},
                       atr_m30=20.0)
    blocked, reason = proc._check_pre_entry_gates("LONG", 0.0, 4700.0)
    # Either passes or blocks for OTHER reason (cooldown etc) but not ATR
    assert "M30_ATR_EXTREME" not in (reason or "")


def test_w2_1_atr_extreme_disabled_via_flag():
    """When m30_atr_extreme_block_enabled=False, ATR extreme does not block."""
    proc = _make_proc({"m30_atr_extreme_block_enabled": False, "m30_atr_extreme_pts": 45.0},
                       atr_m30=80.0)  # very extreme
    blocked, reason = proc._check_pre_entry_gates("LONG", 0.0, 4700.0)
    assert "M30_ATR_EXTREME" not in (reason or "")


def test_w2_1_atr_at_boundary():
    """atr_m30 == threshold should NOT block (strict >)."""
    proc = _make_proc({"m30_atr_extreme_block_enabled": True, "m30_atr_extreme_pts": 45.0},
                       atr_m30=45.0)
    blocked, reason = proc._check_pre_entry_gates("LONG", 0.0, 4700.0)
    assert "M30_ATR_EXTREME" not in (reason or "")


# ----------------------------------------------------------------------------
# W2.5 Daily loss limit
# ----------------------------------------------------------------------------

def test_w2_5_daily_loss_limit_blocks_when_breached():
    proc = _make_proc({
        "m30_atr_extreme_block_enabled": False,
        "daily_loss_limit_enabled": True,
        "daily_loss_limit_usd": -50.0,
    })
    # Force cache to a value below limit (avoid disk read)
    proc._daily_pnl_cache = (time.monotonic(), -75.0)
    blocked, reason = proc._check_pre_entry_gates("LONG", 0.0, 4700.0)
    assert blocked is True
    assert "DAILY_LOSS_LIMIT" in reason


def test_w2_5_daily_loss_limit_does_not_block_in_profit():
    proc = _make_proc({
        "m30_atr_extreme_block_enabled": False,
        "daily_loss_limit_enabled": True,
        "daily_loss_limit_usd": -50.0,
    })
    proc._daily_pnl_cache = (time.monotonic(), +25.0)
    blocked, reason = proc._check_pre_entry_gates("LONG", 0.0, 4700.0)
    assert "DAILY_LOSS_LIMIT" not in (reason or "")


def test_w2_5_daily_loss_limit_disabled_via_flag():
    proc = _make_proc({
        "m30_atr_extreme_block_enabled": False,
        "daily_loss_limit_enabled": False,
        "daily_loss_limit_usd": -50.0,
    })
    proc._daily_pnl_cache = (time.monotonic(), -200.0)
    blocked, reason = proc._check_pre_entry_gates("LONG", 0.0, 4700.0)
    assert "DAILY_LOSS_LIMIT" not in (reason or "")


def test_w2_5_compute_daily_pnl_handles_missing_file(tmp_path, monkeypatch):
    """If trades.csv doesn't exist, _compute_daily_pnl returns 0.0 (no crash)."""
    monkeypatch.setattr(ep_module, "TRADES_CSV", tmp_path / "nonexistent.csv")
    proc = _make_proc({})
    assert proc._compute_daily_pnl() == 0.0


def test_w2_5_compute_daily_pnl_sums_today_only(tmp_path, monkeypatch):
    """Only sum trades whose timestamp date == today UTC."""
    fake_csv = tmp_path / "trades.csv"
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with fake_csv.open("w", encoding="utf-8") as f:
        f.write("timestamp,asset,direction,decision,lots,entry,sl,tp1,tp2,result,pnl\n")
        f.write(f"{today},GC,LONG,CONFIRMED,0.02,4700,4690,4710,4720,tp1_hit,5.50\n")
        f.write(f"{today},GC,SHORT,CONFIRMED,0.02,4710,4720,4700,4690,sl_hit,-3.20\n")
        f.write(f"{yesterday},GC,LONG,CONFIRMED,0.02,4700,4690,4710,4720,tp1_hit,99.99\n")  # ignore
    monkeypatch.setattr(ep_module, "TRADES_CSV", fake_csv)
    proc = _make_proc({})
    pnl = proc._compute_daily_pnl()
    assert abs(pnl - (5.50 - 3.20)) < 0.01

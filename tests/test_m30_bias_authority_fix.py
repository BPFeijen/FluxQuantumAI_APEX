from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from live import level_detector


def test_confirmed_bullish_invalidated_when_live_price_below_latest_box_low(monkeypatch):
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 95.0)

    m30_df = pd.DataFrame(
        [
            {
                "m30_box_id": 1,
                "m30_box_confirmed": True,
                "m30_box_high": 110.0,
                "m30_box_low": 100.0,
                "m30_liq_top": 112.0,
                "m30_liq_bot": 101.0,
            },
            {
                "m30_box_id": 2,
                "m30_box_confirmed": False,
                "m30_box_high": 108.0,
                "m30_box_low": 98.0,
                "m30_liq_top": 108.0,
                "m30_liq_bot": 98.0,
            },
        ]
    )

    assert level_detector.derive_m30_bias(m30_df, confirmed_only=True) == ("unknown", False)


def test_confirmed_bearish_invalidated_when_live_price_above_latest_box_high(monkeypatch):
    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 125.0)

    m30_df = pd.DataFrame(
        [
            {
                "m30_box_id": 1,
                "m30_box_confirmed": True,
                "m30_box_high": 120.0,
                "m30_box_low": 110.0,
                "m30_liq_top": 119.0,
                "m30_liq_bot": 108.0,
            },
            {
                "m30_box_id": 2,
                "m30_box_confirmed": False,
                "m30_box_high": 121.0,
                "m30_box_low": 111.0,
                "m30_liq_top": 121.0,
                "m30_liq_bot": 111.0,
            },
        ]
    )

    assert level_detector.derive_m30_bias(m30_df, confirmed_only=True) == ("unknown", False)


def test_provisional_bias_falls_back_to_live_price_vs_box_when_latest_row_is_ambiguous(monkeypatch):
    m30_df = pd.DataFrame(
        [
            {
                "m30_box_id": 1,
                "m30_box_confirmed": False,
                "m30_box_high": 210.0,
                "m30_box_low": 200.0,
                "m30_liq_top": 210.0,
                "m30_liq_bot": 200.0,
            }
        ]
    )

    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 211.0)
    assert level_detector.derive_m30_bias(m30_df, confirmed_only=False) == ("bullish", False)

    monkeypatch.setattr(level_detector, "_get_current_gc_price", lambda: 199.0)
    assert level_detector.derive_m30_bias(m30_df, confirmed_only=False) == ("bearish", False)


def _load_event_processor_class():
    watchdog_mod = types.ModuleType("watchdog")
    observers_mod = types.ModuleType("watchdog.observers")
    events_mod = types.ModuleType("watchdog.events")

    class _Observer:
        pass

    class _FileSystemEventHandler:
        pass

    observers_mod.Observer = _Observer
    events_mod.FileSystemEventHandler = _FileSystemEventHandler

    ats_mod = types.ModuleType("ats_live_gate")

    class _ATSLiveGate:
        pass

    ats_mod.ATSLiveGate = _ATSLiveGate

    mt5_mod = types.ModuleType("mt5_executor")

    class _MT5Executor:
        pass

    mt5_mod.MT5Executor = _MT5Executor
    mt5_mod._split_lots = lambda lots: [lots]
    mt5_mod.SYMBOL = "GC"
    mt5_mod.MAGIC = 1

    tg_mod = types.ModuleType("live.telegram_notifier")

    sys.modules.setdefault("watchdog", watchdog_mod)
    sys.modules["watchdog.observers"] = observers_mod
    sys.modules["watchdog.events"] = events_mod
    sys.modules["ats_live_gate"] = ats_mod
    sys.modules["mt5_executor"] = mt5_mod
    sys.modules["live.telegram_notifier"] = tg_mod

    mod = importlib.import_module("live.event_processor")
    return mod.EventProcessor


def test_should_not_hard_block_overextension_reversal():
    event_processor_cls = _load_event_processor_class()
    apply_block, mode = event_processor_cls._should_apply_m30_bias_block(
        object(),
        direction="SHORT",
        strategy_reason="M30 OVEREXTENDED move; reversal allowed by strategy",
        source="ICEBERG",
    )
    assert (apply_block, mode) == (False, "OVEREXTENSION_REVERSAL")


def test_should_hard_block_continuation_context():
    event_processor_cls = _load_event_processor_class()
    apply_block, mode = event_processor_cls._should_apply_m30_bias_block(
        object(),
        direction="LONG",
        strategy_reason="CONTINUATION setup aligned with trend",
        source="ICEBERG",
    )
    assert (apply_block, mode) == (True, "CONTINUATION")

"""tests/test_macro_monitor.py — Phase 3 smoke tests for MACRO-MONITOR-VAP
(Asana 1214290409737742).

Validates the live/macro_monitor.py implementation against the spec:
  Layer 1: trigger evaluation (each pure helper)
  Layer 2: VAP construction + lifecycle (HELD/EXIT_SUGGESTED/SUPERSEDED/EXPIRED)
  Layer 3: anti-spam invariant + heartbeat shape

Run:
    pytest tests/test_macro_monitor.py -v
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from live import macro_monitor as mm  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level VAP state before each test."""
    with mm._LAST_VAP_LOCK:
        mm._LAST_VAP = None
    mm._LAST_DECISION_ID_PROCESSED = ""
    yield
    with mm._LAST_VAP_LOCK:
        mm._LAST_VAP = None
    mm._LAST_DECISION_ID_PROCESSED = ""


@pytest.fixture
def tmp_paths(tmp_path):
    """Redirect log paths to tmp dir."""
    original_decision_live = mm.DECISION_LIVE_PATH
    original_decision_log = mm.DECISION_LOG_PATH
    original_service_state = mm.SERVICE_STATE_PATH
    original_position_events = mm.POSITION_EVENTS_LOG

    mm.DECISION_LIVE_PATH = tmp_path / "decision_live.json"
    mm.DECISION_LOG_PATH = tmp_path / "decision_log.jsonl"
    mm.SERVICE_STATE_PATH = tmp_path / "service_state.json"
    mm.POSITION_EVENTS_LOG = tmp_path / "position_events.jsonl"

    try:
        yield tmp_path
    finally:
        mm.DECISION_LIVE_PATH = original_decision_live
        mm.DECISION_LOG_PATH = original_decision_log
        mm.SERVICE_STATE_PATH = original_service_state
        mm.POSITION_EVENTS_LOG = original_position_events


def _go_decision(direction: str, decision_id: str = "abc123",
                 entry: float = 4720.0, sl: float = 4730.0, tp1: float = 4710.0,
                 tp2: float = 4700.0, m30_bias: str = "bearish",
                 atr_m30: float = 20.0, ts: str | None = None,
                 iceberg: dict | None = None, anomaly: dict | None = None) -> dict:
    """Build a synthetic GO decision dict matching decision_live.json shape."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    return {
        "timestamp": ts,
        "decision_id": decision_id,
        "price_mt5": entry,
        "context": {
            "m30_bias": m30_bias,
            "m30_bias_confirmed": True,
            "m30_atr14": atr_m30,
        },
        "decision": {
            "action": "GO",
            "direction": direction,
            "sl": sl, "tp1": tp1, "tp2": tp2,
        },
        "iceberg": iceberg or {"detected": False, "side": "UNKNOWN", "severity": "NONE"},
        "anomaly": anomaly or {"detected": False, "alignment": "ALIGNED", "severity": "NONE"},
    }


def _service_state(price: float = 4720.0, m30_bias: str = "bearish",
                   m30_bias_confirmed: bool = True,
                   defense_tier: str = "NORMAL",
                   stress_direction: str = "HOLD") -> dict:
    """Build a synthetic service_state.json payload."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mt5_price": price,
        "m30_bias": m30_bias,
        "m30_bias_confirmed": m30_bias_confirmed,
        "defense_tier": defense_tier,
        "stress_direction": stress_direction,
    }


# ---------------------------------------------------------------------------
# Layer 1 — pure helper logic
# ---------------------------------------------------------------------------

class TestIcebergAgainstVap:
    def test_buy_iceberg_against_short_vap(self):
        assert mm._iceberg_against_vap("BUY", "SHORT") is True

    def test_sell_iceberg_against_long_vap(self):
        assert mm._iceberg_against_vap("SELL", "LONG") is True

    def test_buy_iceberg_aligned_with_long_vap(self):
        # BUY iceberg with LONG VAP = aligned, NOT against
        assert mm._iceberg_against_vap("BUY", "LONG") is False

    def test_sell_iceberg_aligned_with_short_vap(self):
        assert mm._iceberg_against_vap("SELL", "SHORT") is False

    def test_unknown_side_returns_false(self):
        assert mm._iceberg_against_vap("UNKNOWN", "SHORT") is False
        assert mm._iceberg_against_vap("", "SHORT") is False
        assert mm._iceberg_against_vap(None, "SHORT") is False

    def test_alternative_side_aliases(self):
        # Code accepts BID as BUY-like, ASK as SELL-like
        assert mm._iceberg_against_vap("BID", "SHORT") is True
        assert mm._iceberg_against_vap("ASK", "LONG") is True


class TestM30BiasAgainstVap:
    def test_bullish_against_short(self):
        assert mm._m30_bias_against_vap("bullish", "SHORT") is True

    def test_bearish_against_long(self):
        assert mm._m30_bias_against_vap("bearish", "LONG") is True

    def test_aligned_does_not_fire(self):
        assert mm._m30_bias_against_vap("bullish", "LONG") is False
        assert mm._m30_bias_against_vap("bearish", "SHORT") is False

    def test_unknown_does_not_fire(self):
        assert mm._m30_bias_against_vap("unknown", "SHORT") is False
        assert mm._m30_bias_against_vap("", "LONG") is False


class TestPriceLevels:
    def test_short_vap_tp1_reached(self):
        vap = mm._build_vap_from_decision(_go_decision("SHORT", entry=4720, tp1=4710))
        assert mm._price_reached_tp(4709.0, vap) is True
        assert mm._price_reached_tp(4710.0, vap) is True
        assert mm._price_reached_tp(4711.0, vap) is False

    def test_long_vap_tp1_reached(self):
        vap = mm._build_vap_from_decision(_go_decision("LONG", entry=4720, tp1=4730))
        assert mm._price_reached_tp(4731.0, vap) is True
        assert mm._price_reached_tp(4730.0, vap) is True
        assert mm._price_reached_tp(4729.0, vap) is False

    def test_short_vap_sl_reached(self):
        vap = mm._build_vap_from_decision(_go_decision("SHORT", entry=4720, sl=4730))
        assert mm._price_reached_sl(4731.0, vap) is True
        assert mm._price_reached_sl(4730.0, vap) is True
        assert mm._price_reached_sl(4729.0, vap) is False

    def test_long_vap_sl_reached(self):
        vap = mm._build_vap_from_decision(_go_decision("LONG", entry=4720, sl=4710))
        assert mm._price_reached_sl(4709.0, vap) is True
        assert mm._price_reached_sl(4710.0, vap) is True
        assert mm._price_reached_sl(4711.0, vap) is False

    def test_zero_levels_dont_fire(self):
        vap = mm._build_vap_from_decision(_go_decision("SHORT", entry=4720, sl=0, tp1=0))
        assert mm._price_reached_tp(4710.0, vap) is False
        assert mm._price_reached_sl(4730.0, vap) is False


# ---------------------------------------------------------------------------
# Layer 2 — VAP construction + lifecycle
# ---------------------------------------------------------------------------

class TestBuildVap:
    def test_valid_short_decision_builds_vap(self):
        d = _go_decision("SHORT", decision_id="vap1", entry=4720, sl=4730, tp1=4710, tp2=4700)
        vap = mm._build_vap_from_decision(d)
        assert vap is not None
        assert vap.vap_id == "vap1"
        assert vap.direction == "SHORT"
        assert vap.entry_price == 4720.0
        assert vap.sl == 4730.0
        assert vap.tp1 == 4710.0
        assert vap.tp2 == 4700.0
        assert vap.status == "HELD"
        assert vap.fired_triggers == []
        assert vap.m30_bias_at_creation == "bearish"

    def test_block_decision_returns_none(self):
        d = _go_decision("SHORT")
        d["decision"]["action"] = "BLOCK"
        assert mm._build_vap_from_decision(d) is None

    def test_exec_failed_decision_returns_none(self):
        d = _go_decision("SHORT")
        d["decision"]["action"] = "EXEC_FAILED"
        assert mm._build_vap_from_decision(d) is None

    def test_unknown_direction_returns_none(self):
        d = _go_decision("SHORT")
        d["decision"]["direction"] = "UNKNOWN"
        assert mm._build_vap_from_decision(d) is None

    def test_expires_at_equals_entry_plus_lifetime(self):
        ts = "2026-04-27T02:00:00+00:00"
        d = _go_decision("SHORT", ts=ts)
        vap = mm._build_vap_from_decision(d)
        expected_expiry = datetime.fromisoformat(ts) + timedelta(minutes=mm.VAP_MAX_LIFETIME_MIN)
        actual_expiry = datetime.fromisoformat(vap.expires_at)
        assert actual_expiry == expected_expiry


class TestVapLifecycle:
    def _write_decision(self, tmp_paths: Path, decision: dict):
        with open(mm.DECISION_LIVE_PATH, "w", encoding="utf-8") as f:
            json.dump(decision, f)

    def _write_service_state(self, tmp_paths: Path, state: dict):
        with open(mm.SERVICE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def test_first_go_creates_vap(self, tmp_paths):
        monitor = mm.MacroMonitor()
        d = _go_decision("SHORT", decision_id="dec1")
        self._write_decision(tmp_paths, d)
        self._write_service_state(tmp_paths, _service_state(price=4720))
        monitor._tick()
        assert mm._LAST_VAP is not None
        assert mm._LAST_VAP.vap_id == "dec1"
        assert mm._LAST_VAP.direction == "SHORT"

    def test_opposite_direction_supersedes_vap(self, tmp_paths):
        monitor = mm.MacroMonitor()
        # First GO SHORT
        self._write_decision(tmp_paths, _go_decision("SHORT", decision_id="dec1"))
        self._write_service_state(tmp_paths, _service_state(price=4720))
        monitor._tick()
        first_vap_id = mm._LAST_VAP.vap_id
        assert first_vap_id == "dec1"

        # Then GO LONG → supersede
        self._write_decision(tmp_paths, _go_decision("LONG", decision_id="dec2", entry=4715))
        monitor._tick()
        assert mm._LAST_VAP.vap_id == "dec2"
        assert mm._LAST_VAP.direction == "LONG"

    def test_same_direction_go_keeps_existing_vap(self, tmp_paths):
        """Same direction GO should NOT reset VAP — it's a confirmation."""
        monitor = mm.MacroMonitor()
        self._write_decision(tmp_paths, _go_decision("SHORT", decision_id="dec1", entry=4720))
        self._write_service_state(tmp_paths, _service_state(price=4720))
        monitor._tick()
        original_entry = mm._LAST_VAP.entry_price
        original_id = mm._LAST_VAP.vap_id

        # Another GO SHORT with different entry → keep ORIGINAL VAP
        self._write_decision(tmp_paths, _go_decision("SHORT", decision_id="dec2", entry=4725))
        monitor._tick()
        assert mm._LAST_VAP.vap_id == original_id   # not replaced
        assert mm._LAST_VAP.entry_price == original_entry

    def test_expiry_clears_vap(self, tmp_paths):
        monitor = mm.MacroMonitor()
        # GO with old timestamp → already expired
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=mm.VAP_MAX_LIFETIME_MIN + 10)).isoformat()
        d = _go_decision("SHORT", decision_id="old", ts=old_ts)
        self._write_decision(tmp_paths, d)
        self._write_service_state(tmp_paths, _service_state(price=4720))
        monitor._tick()
        # First tick creates VAP (not yet processed expired); second tick processes expiry
        # Actually the build_vap creates with expires_at based on entry_ts+lifetime; if entry_ts is old, expires_at is in past
        monitor._tick()
        # After tick, VAP should be expired and cleared
        assert mm._LAST_VAP is None


class TestRestoreVapFromLog:
    def test_restore_picks_most_recent_go_within_lifetime(self, tmp_paths):
        # Write 3 GO entries to decision_log: old (out of lifetime), middle, latest
        log_lines = []
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=300)).isoformat()
        mid_ts = (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat()
        lat_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        log_lines.append(_go_decision("SHORT", decision_id="old", ts=old_ts))
        log_lines.append(_go_decision("SHORT", decision_id="mid", ts=mid_ts))
        log_lines.append(_go_decision("LONG", decision_id="lat", ts=lat_ts))
        with open(mm.DECISION_LOG_PATH, "w", encoding="utf-8") as f:
            for d in log_lines:
                f.write(json.dumps(d) + "\n")
        monitor = mm.MacroMonitor()
        monitor._restore_vap_from_decision_log()
        assert mm._LAST_VAP is not None
        assert mm._LAST_VAP.vap_id == "lat"
        assert mm._LAST_VAP.direction == "LONG"

    def test_no_recent_go_means_no_restore(self, tmp_paths):
        # Only old entries
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=300)).isoformat()
        with open(mm.DECISION_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps(_go_decision("SHORT", decision_id="old", ts=old_ts)) + "\n")
        monitor = mm.MacroMonitor()
        monitor._restore_vap_from_decision_log()
        assert mm._LAST_VAP is None


# ---------------------------------------------------------------------------
# Layer 3 — anti-spam + telegram + heartbeat
# ---------------------------------------------------------------------------

class TestTriggerAntiSpam:
    def _write_state(self, tmp_paths: Path, decision: dict, state: dict):
        with open(mm.DECISION_LIVE_PATH, "w", encoding="utf-8") as f:
            json.dump(decision, f)
        with open(mm.SERVICE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def test_same_trigger_fires_only_once_per_vap(self, tmp_paths):
        monitor = mm.MacroMonitor()
        d = _go_decision("SHORT", decision_id="dec1", entry=4720, sl=4730)
        # State that triggers VIRTUAL_SL (price >= sl)
        state = _service_state(price=4730.0)
        self._write_state(tmp_paths, d, state)

        with patch.object(monitor, "_notify_telegram") as mock_tg:
            monitor._tick()  # creates VAP; checks triggers; fires VIRTUAL_SL
            assert mock_tg.call_count == 1
            assert mm._LAST_VAP.fired_triggers == ["VIRTUAL_SL"]

            # Second tick with SAME state → no new fire
            monitor._tick()
            assert mock_tg.call_count == 1   # still 1, not 2
            assert mm._LAST_VAP.fired_triggers == ["VIRTUAL_SL"]

    def test_multiple_distinct_triggers_fire_independently(self, tmp_paths):
        monitor = mm.MacroMonitor()
        d = _go_decision("SHORT", decision_id="dec1", entry=4720, sl=4730, tp1=4710,
                         m30_bias="bearish")
        # State that triggers BOTH VIRTUAL_SL (price >= sl) AND REGIME_FLIP_M30
        state = _service_state(price=4730.0, m30_bias="bullish", m30_bias_confirmed=True)
        self._write_state(tmp_paths, d, state)

        with patch.object(monitor, "_notify_telegram") as mock_tg:
            monitor._tick()
            assert mock_tg.call_count == 2
            assert set(mm._LAST_VAP.fired_triggers) == {"VIRTUAL_SL", "REGIME_FLIP_M30"}

    def test_status_transitions_to_exit_suggested_on_first_trigger(self, tmp_paths):
        monitor = mm.MacroMonitor()
        d = _go_decision("SHORT", decision_id="dec1", entry=4720, sl=4730)
        state = _service_state(price=4731.0)  # SL hit
        self._write_state(tmp_paths, d, state)

        with patch.object(monitor, "_notify_telegram"):
            monitor._tick()
            assert mm._LAST_VAP.status == "EXIT_SUGGESTED"


class TestHeartbeatGetter:
    def test_returns_none_when_no_vap(self):
        with mm._LAST_VAP_LOCK:
            mm._LAST_VAP = None
        assert mm.get_active_virtual_position() is None

    def test_returns_dict_with_age_min_when_vap_active(self, tmp_paths):
        d = _go_decision("SHORT", decision_id="dec1", entry=4720)
        vap = mm._build_vap_from_decision(d)
        with mm._LAST_VAP_LOCK:
            mm._LAST_VAP = vap

        result = mm.get_active_virtual_position()
        assert result is not None
        assert result["vap_id"] == "dec1"
        assert result["direction"] == "SHORT"
        assert result["entry_price"] == 4720.0
        assert "age_min" in result
        assert isinstance(result["age_min"], int)
        assert result["age_min"] >= 0

    def test_dict_includes_all_required_fields(self, tmp_paths):
        d = _go_decision("SHORT", decision_id="dec1")
        vap = mm._build_vap_from_decision(d)
        with mm._LAST_VAP_LOCK:
            mm._LAST_VAP = vap
        result = mm.get_active_virtual_position()
        required = {
            "vap_id", "direction", "entry_price", "entry_ts",
            "sl", "tp1", "tp2", "status", "fired_triggers",
            "expires_at", "age_min",
        }
        missing = required - set(result.keys())
        assert not missing, f"missing fields: {missing}"


class TestEventEmission:
    def _write_state(self, tmp_paths: Path, decision: dict, state: dict):
        with open(mm.DECISION_LIVE_PATH, "w", encoding="utf-8") as f:
            json.dump(decision, f)
        with open(mm.SERVICE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def test_trigger_emits_canonical_event_to_jsonl(self, tmp_paths):
        monitor = mm.MacroMonitor()
        d = _go_decision("SHORT", decision_id="dec1", entry=4720, sl=4730)
        state = _service_state(price=4731.0)
        self._write_state(tmp_paths, d, state)

        with patch.object(monitor, "_notify_telegram"):
            monitor._tick()

        assert mm.POSITION_EVENTS_LOG.exists()
        with open(mm.POSITION_EVENTS_LOG, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        # Should have: MACRO_VAP_CREATED + MACRO_VIRTUAL_SL
        event_types = [e["event_type"] for e in lines]
        assert "MACRO_VAP_CREATED" in event_types
        assert "MACRO_VIRTUAL_SL" in event_types

    def test_supersede_emits_canonical_event(self, tmp_paths):
        monitor = mm.MacroMonitor()
        # Create SHORT VAP
        self._write_state(tmp_paths, _go_decision("SHORT", decision_id="dec1"),
                          _service_state(price=4720))
        with patch.object(monitor, "_notify_telegram"):
            monitor._tick()
            # Then opposite direction
            self._write_state(tmp_paths, _go_decision("LONG", decision_id="dec2", entry=4715),
                              _service_state(price=4715))
            monitor._tick()
        with open(mm.POSITION_EVENTS_LOG, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        event_types = [e["event_type"] for e in lines]
        assert "MACRO_VAP_SUPERSEDED" in event_types


class TestThreadSafety:
    def test_concurrent_get_returns_consistent_snapshot(self, tmp_paths):
        d = _go_decision("SHORT", decision_id="dec1")
        vap = mm._build_vap_from_decision(d)
        with mm._LAST_VAP_LOCK:
            mm._LAST_VAP = vap

        # Spawn 20 threads calling get; verify no exception, all return dict
        results = []
        errors = []

        def worker():
            try:
                for _ in range(50):
                    r = mm.get_active_virtual_position()
                    results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert all(r is not None for r in results)
        assert all(r["vap_id"] == "dec1" for r in results)

"""Tests for the APEX V3 Decision Bus skeleton."""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

from core.contracts.risk import PositionDecision
from core.contracts.signal import SignalCandidate
from core.decision_bus import DecisionBus, JsonlSink, MemorySink, NoOpSink


def test_publishing_position_decision_does_not_require_broker() -> None:
    sink = MemorySink()
    bus = DecisionBus(sink)

    event = bus.publish(PositionDecision(rationale="unit-test"))

    assert event.payload.rationale == "unit-test"
    assert type(event.payload).__name__ == "PositionDecision"
    assert sink.events == [event]


def test_jsonl_sink_writes_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "decision-events.jsonl"
    bus = DecisionBus(JsonlSink(path))

    event = bus.publish(SignalCandidate(), correlation_id="corr-jsonl")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["correlation_id"] == "corr-jsonl"
    assert payload["payload_type"] == "SignalCandidate"
    assert payload["event_timestamp"] == event.event_timestamp.isoformat()


def test_memory_sink_records_events() -> None:
    sink = MemorySink()
    bus = DecisionBus(sink)

    first = bus.publish(PositionDecision())
    second = bus.publish(SignalCandidate())

    assert sink.events == [first, second]


def test_no_op_sink_does_not_fail() -> None:
    bus = DecisionBus(NoOpSink())

    event = bus.publish(PositionDecision())

    assert type(event.payload).__name__ == "PositionDecision"


def test_correlation_id_is_preserved_or_generated() -> None:
    explicit = DecisionBus(MemorySink()).publish(
        PositionDecision(),
        correlation_id="corr-explicit",
    )
    generated = DecisionBus(MemorySink()).publish(PositionDecision())
    metadata = DecisionBus(MemorySink()).publish(
        PositionDecision(metadata={"correlation_id": "corr-metadata"})
    )

    assert explicit.correlation_id == "corr-explicit"
    assert generated.correlation_id
    assert metadata.correlation_id == "corr-metadata"


def test_event_timestamps_exist_and_are_utc_compatible() -> None:
    event = DecisionBus(MemorySink()).publish(PositionDecision())

    assert event.event_timestamp is not None
    assert event.event_timestamp.tzinfo is not None
    assert event.event_timestamp.utcoffset() == timezone.utc.utcoffset(event.event_timestamp)


def test_decision_bus_has_no_broker_or_symbol_references() -> None:
    decision_bus_root = Path("core/decision_bus")
    forbidden = ("XAUUSD", "MetaTrader5", "MT5", "mt5", "broker SDK", "Broker SDK")

    for path in decision_bus_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in source, f"{term!r} found in {path}"

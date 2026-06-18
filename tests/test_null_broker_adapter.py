"""Tests for the APEX V3 null broker execution boundary."""
from __future__ import annotations

from pathlib import Path

from core.contracts.enums import BrokerKind, Direction, ExecutionStatus
from core.contracts.execution import ExecutionIntent, ExecutionReport
from core.decision_bus import DecisionBus, MemorySink
from core.execution import ExecutionService, NullBrokerAdapter


FORBIDDEN_FILES = (
    "run_live.py",
    "live/event_processor.py",
    "live/position_monitor.py",
    "mt5_executor.py",
    "mt5_executor_hantec.py",
)


def test_null_broker_requires_no_connection_and_returns_report() -> None:
    intent = ExecutionIntent(direction=Direction.LONG, size=1.0)
    adapter = NullBrokerAdapter()

    report = adapter.execute(intent)

    assert isinstance(report, ExecutionReport)
    assert report.broker_kind is BrokerKind.NULL
    assert report.status is ExecutionStatus.ACCEPTED
    assert report.filled_size == 0.0
    assert report.broker_order_id is None
    assert report.metadata["simulated"] is True


def test_null_broker_rejects_non_executable_intent() -> None:
    report = NullBrokerAdapter().execute(ExecutionIntent(direction=Direction.FLAT, size=0.0))

    assert report.status is ExecutionStatus.REJECTED
    assert report.error
    assert report.metadata["simulated"] is True


def test_null_broker_preserves_correlation_id_when_available() -> None:
    intent = ExecutionIntent(
        direction=Direction.SHORT,
        size=2.0,
        metadata={"correlation_id": "corr-exec-1"},
    )

    report = NullBrokerAdapter().execute(intent)

    assert report.metadata["correlation_id"] == "corr-exec-1"


def test_execution_service_publishes_report_to_decision_bus_memory_sink() -> None:
    sink = MemorySink()
    service = ExecutionService(decision_bus=DecisionBus(sink))
    intent = ExecutionIntent(
        direction=Direction.LONG,
        size=1.0,
        metadata={"correlation_id": "corr-service-1"},
    )

    report = service.execute(intent)

    assert sink.events
    event = sink.events[0]
    assert event.payload == report
    assert event.correlation_id == "corr-service-1"
    assert report.status is ExecutionStatus.ACCEPTED


def test_core_execution_has_no_broker_or_symbol_references() -> None:
    execution_root = Path("core/execution")
    forbidden = ("XAUUSD", "MetaTrader5", "MT5", "mt5", "broker SDK", "Broker SDK")

    for path in execution_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in source, f"{term!r} found in {path}"


def test_live_runtime_files_are_not_modified() -> None:
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "refactor/apex-v3-recovery...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    changed_files = set(result.stdout.splitlines())

    assert changed_files.isdisjoint(FORBIDDEN_FILES)

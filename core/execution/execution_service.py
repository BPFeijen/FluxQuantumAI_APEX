"""Execution service boundary for canonical APEX V3 execution intents."""
from __future__ import annotations

from core.contracts.execution import ExecutionIntent, ExecutionReport
from core.decision_bus import DecisionBus

from .adapters import BrokerAdapter
from .null_broker import NullBrokerAdapter


class ExecutionService:
    """Delegate execution intents to an adapter and publish reports."""

    def __init__(
        self,
        adapter: BrokerAdapter | None = None,
        decision_bus: DecisionBus | None = None,
    ) -> None:
        self.adapter = adapter or NullBrokerAdapter()
        self.decision_bus = decision_bus or DecisionBus()

    def execute(self, intent: ExecutionIntent) -> ExecutionReport:
        """Execute an intent through the configured adapter and publish its report."""
        report = self.adapter.execute(intent)
        self.decision_bus.publish(report, correlation_id=_correlation_id(intent))
        return report


def _correlation_id(intent: ExecutionIntent) -> str | None:
    value = intent.metadata.get("correlation_id")
    return str(value) if value else None

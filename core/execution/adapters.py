"""Broker adapter interfaces for the APEX V3 execution boundary."""
from __future__ import annotations

from typing import Protocol

from core.contracts.execution import ExecutionIntent, ExecutionReport


class BrokerAdapter(Protocol):
    """Protocol for broker-agnostic execution adapters."""

    def execute(self, intent: ExecutionIntent) -> ExecutionReport:
        """Convert an execution intent into an execution report."""

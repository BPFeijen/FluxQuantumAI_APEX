"""Null broker adapter for safe APEX V3 execution boundary tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.contracts.enums import BrokerKind, Direction, ExecutionStatus
from core.contracts.execution import ExecutionIntent, ExecutionReport


class NullBrokerAdapter:
    """Adapter that simulates order acceptance without broker side effects."""

    broker_kind = BrokerKind.NULL

    def execute(self, intent: ExecutionIntent) -> ExecutionReport:
        """Return a canonical report without connecting or sending an order."""
        correlation_id = _correlation_id(intent)
        accepted = intent.direction is not Direction.FLAT and intent.size > 0
        metadata: dict[str, Any] = {
            "simulated": True,
            "adapter": "NullBrokerAdapter",
        }
        if correlation_id is not None:
            metadata["correlation_id"] = correlation_id

        return ExecutionReport(
            instrument=intent.instrument,
            timestamp=datetime.now(timezone.utc),
            broker_kind=BrokerKind.NULL,
            status=ExecutionStatus.ACCEPTED if accepted else ExecutionStatus.REJECTED,
            client_order_id=intent.client_order_id,
            broker_order_id=None,
            filled_size=0.0,
            average_fill_price=None,
            error=None if accepted else "NullBrokerAdapter rejected non-executable intent",
            metadata=metadata,
        )


def _correlation_id(intent: ExecutionIntent) -> str | None:
    value = intent.metadata.get("correlation_id")
    return str(value) if value else None

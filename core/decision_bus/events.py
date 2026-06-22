"""Canonical Decision Bus event envelope."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.contracts.execution import ExecutionIntent, ExecutionReport
from core.contracts.risk import PositionDecision
from core.contracts.signal import SignalCandidate
from core.contracts.telemetry import TelemetryEvent

CanonicalPayload = (
    SignalCandidate
    | PositionDecision
    | ExecutionIntent
    | ExecutionReport
    | TelemetryEvent
)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _payload_to_dict(payload: CanonicalPayload) -> dict[str, Any]:
    if hasattr(payload, "to_dict"):
        return payload.to_dict()
    raise TypeError(f"Unsupported Decision Bus payload: {type(payload).__name__}")


def _metadata_correlation_id(payload: CanonicalPayload) -> str | None:
    metadata = getattr(payload, "metadata", None) or getattr(payload, "payload", None)
    if isinstance(metadata, dict):
        value = metadata.get("correlation_id")
        return str(value) if value else None
    return None


@dataclass(frozen=True)
class DecisionEvent:
    """Broker-agnostic envelope for canonical APEX V3 decision payloads."""

    payload: CanonicalPayload
    correlation_id: str | None = None
    event_timestamp: datetime | None = None
    event_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.correlation_id is None:
            object.__setattr__(
                self,
                "correlation_id",
                _metadata_correlation_id(self.payload) or str(uuid4()),
            )
        if self.event_timestamp is None:
            object.__setattr__(self, "event_timestamp", utc_now())
        elif self.event_timestamp.tzinfo is None:
            object.__setattr__(
                self,
                "event_timestamp",
                self.event_timestamp.replace(tzinfo=timezone.utc),
            )
        if self.event_type is None:
            object.__setattr__(self, "event_type", type(self.payload).__name__)

    def to_dict(self) -> dict[str, Any]:
        """Convert this event to a JSON-serializable dictionary."""
        return {
            "event_type": self.event_type,
            "event_timestamp": self.event_timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "payload_type": type(self.payload).__name__,
            "payload": _payload_to_dict(self.payload),
            "metadata": dict(self.metadata),
        }

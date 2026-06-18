"""Broker-agnostic APEX V3 Decision Bus."""
from __future__ import annotations

from collections.abc import Iterable

from .events import CanonicalPayload, DecisionEvent
from .sinks import DecisionSink, NoOpSink


class DecisionBus:
    """Publish canonical decision payloads to configured sinks."""

    def __init__(self, sinks: DecisionSink | Iterable[DecisionSink] | None = None) -> None:
        if sinks is None:
            self.sinks: list[DecisionSink] = [NoOpSink()]
        elif hasattr(sinks, "publish"):
            self.sinks = [sinks]  # type: ignore[list-item]
        else:
            self.sinks = list(sinks)

    def publish(
        self,
        payload: CanonicalPayload | DecisionEvent,
        *,
        correlation_id: str | None = None,
    ) -> DecisionEvent:
        """Envelope and publish a canonical payload, returning the event."""
        event = payload if isinstance(payload, DecisionEvent) else DecisionEvent(
            payload=payload,
            correlation_id=correlation_id,
        )
        for sink in self.sinks:
            sink.publish(event)
        return event

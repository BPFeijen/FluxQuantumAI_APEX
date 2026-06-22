"""Decision Bus sink implementations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .events import DecisionEvent


class DecisionSink(Protocol):
    """Sink protocol for Decision Bus events."""

    def publish(self, event: DecisionEvent) -> None:
        """Persist, forward, or intentionally ignore an event."""


class NoOpSink:
    """Sink that accepts events without side effects."""

    def publish(self, event: DecisionEvent) -> None:
        return None


class MemorySink:
    """Sink that stores events in memory for tests and local inspection."""

    def __init__(self) -> None:
        self.events: list[DecisionEvent] = []

    def publish(self, event: DecisionEvent) -> None:
        self.events.append(event)


class JsonlSink:
    """Sink that appends Decision Bus events as JSON Lines."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def publish(self, event: DecisionEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

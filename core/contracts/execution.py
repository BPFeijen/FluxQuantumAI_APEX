"""Broker-agnostic execution intent and report contracts."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
from .enums import BrokerKind, Direction, ExecutionStatus, Instrument

def _default(v: Any) -> Any:
    if isinstance(v, datetime): return v.isoformat()
    if hasattr(v, "value"): return v.value
    raise TypeError(f"Object of type {type(v).__name__} is not JSON serializable")
def _dump(o: Any) -> dict[str, Any]: return json.loads(json.dumps(asdict(o), default=_default))

@dataclass(frozen=True)
class ExecutionIntent:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    broker_kind: BrokerKind = BrokerKind.PAPER
    direction: Direction = Direction.FLAT
    size: float = 0.0
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    client_order_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

@dataclass(frozen=True)
class ExecutionReport:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    broker_kind: BrokerKind = BrokerKind.PAPER
    status: ExecutionStatus = ExecutionStatus.PENDING
    client_order_id: str | None = None
    broker_order_id: str | None = None
    filled_size: float = 0.0
    average_fill_price: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

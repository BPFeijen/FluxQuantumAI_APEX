"""Wyckoff/ICT and order-flow context contracts."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
from .enums import Bias, Instrument, MarketBehavior

def _default(v: Any) -> Any:
    if isinstance(v, datetime): return v.isoformat()
    if hasattr(v, "value"): return v.value
    raise TypeError(f"Object of type {type(v).__name__} is not JSON serializable")
def _dump(o: Any) -> dict[str, Any]: return json.loads(json.dumps(asdict(o), default=_default))

@dataclass(frozen=True)
class WyckoffICTContext:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bias: Bias = Bias.NEUTRAL
    liquidity_sweep: bool = False
    order_block_active: bool = False
    fair_value_gap_active: bool = False
    narrative: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

@dataclass(frozen=True)
class OrderFlowBehavior:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    behavior: MarketBehavior = MarketBehavior.BALANCE
    delta: float | None = None
    absorption_detected: bool = False
    iceberg_detected: bool = False
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

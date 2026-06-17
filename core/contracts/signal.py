"""Strategy selection and signal candidate contracts."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
from .enums import Direction, Instrument, StrategyId

def _default(v: Any) -> Any:
    if isinstance(v, datetime): return v.isoformat()
    if hasattr(v, "value"): return v.value
    raise TypeError(f"Object of type {type(v).__name__} is not JSON serializable")
def _dump(o: Any) -> dict[str, Any]: return json.loads(json.dumps(asdict(o), default=_default))

@dataclass(frozen=True)
class StrategySelection:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_id: StrategyId = StrategyId.NONE
    enabled: bool = False
    confidence: float = 0.0
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

@dataclass(frozen=True)
class SignalCandidate:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_id: StrategyId = StrategyId.NONE
    direction: Direction = Direction.FLAT
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

"""Market data and ATS structure contracts."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
from .enums import Instrument, MarketCondition, Session

def _json_default(value: Any) -> Any:
    if isinstance(value, datetime): return value.isoformat()
    if hasattr(value, "value"): return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

def _dump(obj: Any) -> dict[str, Any]: return json.loads(json.dumps(asdict(obj), default=_json_default))

@dataclass(frozen=True)
class MarketSnapshot:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session: Session = Session.GLOBEX
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    condition: MarketCondition = MarketCondition.RANGING
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

@dataclass(frozen=True)
class ATSStructureSnapshot:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    liquidity_high: float | None = None
    liquidity_low: float | None = None
    fair_value: float | None = None
    structure_high: float | None = None
    structure_low: float | None = None
    condition: MarketCondition = MarketCondition.RANGING
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

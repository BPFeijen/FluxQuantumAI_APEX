"""Phase classifier output contract."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
from .enums import Instrument, Phase, Subphase

def _default(v: Any) -> Any:
    if isinstance(v, datetime): return v.isoformat()
    if hasattr(v, "value"): return v.value
    raise TypeError(f"Object of type {type(v).__name__} is not JSON serializable")
def _dump(o: Any) -> dict[str, Any]: return json.loads(json.dumps(asdict(o), default=_default))

@dataclass(frozen=True)
class PhaseClassification:
    instrument: Instrument = Instrument.GC
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    phase: Phase = Phase.UNKNOWN
    subphase: Subphase = Subphase.NONE
    confidence: float = 0.0
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict[str, Any]: return _dump(self)
    def to_json(self) -> str: return json.dumps(self.to_dict(), sort_keys=True)

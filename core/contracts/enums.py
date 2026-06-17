"""Canonical enum vocabulary for APEX V3 contracts."""

from __future__ import annotations

from enum import Enum


class SerializableEnum(str, Enum):
    """String enum with stable JSON representation."""

    def __str__(self) -> str:
        return self.value


class Instrument(SerializableEnum):
    """Canonical decision instruments. The V3 core decides on GC only."""

    GC = "GC"


class Session(SerializableEnum):
    ASIA = "asia"
    LONDON = "london"
    NEW_YORK = "new_york"
    GLOBEX = "globex"


class Direction(SerializableEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Bias(SerializableEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class MarketCondition(SerializableEnum):
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    ILLIQUID = "illiquid"


class Phase(SerializableEnum):
    ACCUMULATION = "accumulation"
    MARKUP = "markup"
    DISTRIBUTION = "distribution"
    MARKDOWN = "markdown"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class Subphase(SerializableEnum):
    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"
    NONE = "none"


class MarketBehavior(SerializableEnum):
    ABSORPTION = "absorption"
    DISPLACEMENT = "displacement"
    BREAKOUT = "breakout"
    REVERSAL = "reversal"
    BALANCE = "balance"


class StrategyId(SerializableEnum):
    NONE = "none"
    ATS_BREAKOUT = "ats_breakout"
    ICT_REVERSAL = "ict_reversal"
    WYCKOFF_CONTINUATION = "wyckoff_continuation"


class DecisionType(SerializableEnum):
    ENTER = "enter"
    HOLD = "hold"
    EXIT = "exit"
    REDUCE = "reduce"
    REJECT = "reject"


class RiskMode(SerializableEnum):
    NORMAL = "normal"
    DEFENSIVE = "defensive"
    LOCKDOWN = "lockdown"


class ExecutionStatus(SerializableEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BrokerKind(SerializableEnum):
    PAPER = "paper"
    NULL = "null"
    LEGACY_ADAPTER = "legacy_adapter"


class EventType(SerializableEnum):
    MARKET = "market"
    CONTEXT = "context"
    PHASE = "phase"
    SIGNAL = "signal"
    RISK = "risk"
    EXECUTION = "execution"
    SYSTEM = "system"

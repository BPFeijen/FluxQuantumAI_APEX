# APEX_V3 Canonical Contracts Overview

APEX_V3 must not refactor major modules before agreeing on explicit contracts.

## Required contracts

```text
MarketSnapshot
ATSStructureSnapshot
WyckoffICTContext
OrderFlowBehavior
PhaseClassification
StrategySelection
SignalCandidate
PositionDecision
ExecutionIntent
ExecutionReport
TelemetryEvent
```

## Contract principles

- Serializable.
- Replayable.
- Auditable.
- GC-only.
- Broker-agnostic.
- Safe in no-broker mode.
- Suitable for dashboard and Telegram.

## Contract ownership

- Architecture owns structure.
- Methodology owns semantic meaning.
- Quant owns validation metrics.
- Production owns implementation.

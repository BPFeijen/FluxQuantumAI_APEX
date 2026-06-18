---
name: APEX V3 Recovery Task
description: Controlled task template for FluxQuantumAI_APEX_V3 recovery
title: "[APEX V3] "
labels: []
assignees: []
---

## Objective


## Context


## Scope


## Out of Scope


## Impacted Files / Modules


## Methodology Canon

Relevant methodology sources:

```text
ATS / Master Pattern / Wyckoff / ICT / Order Flow / DOM / Iceberg / Volume Profile / Quant validation
```

## Contracts Involved

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

## Acceptance Criteria

- [ ] GC-only decision logic respected
- [ ] No MT5 import added to V3 core
- [ ] No XAUUSD decision logic added to V3 core
- [ ] Broker remains adapter-only
- [ ] Decision Bus impact is documented
- [ ] Replay/test evidence is attached

## Tests / Validation Required


## Expected Evidence


## Risks


## Dependencies


## Definition of Done

- [ ] Implementation complete
- [ ] Tests/replay complete
- [ ] Evidence attached
- [ ] Methodology review complete when applicable
- [ ] Quant validation complete when applicable
- [ ] PR reviewed and approved

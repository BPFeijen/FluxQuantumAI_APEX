---
name: APEX V3 Task
about: Use this template for FluxQuantumAI_APEX_V3 recovery work
title: "[APEX-V3] "
labels: ""
assignees: ""
---

## Objective

Describe the specific recovery/refactoring objective.

## Context

Explain why this task exists and which APEX V3 principle it supports.

## Scope

What must be done in this issue.

## Out of scope

What must not be changed.

## Impacted files/modules

List known files/modules. If unknown, state that Diagnostic RO must identify them first.

## Methodology canon reference

State the related methodology area:

- ATS Master Pattern
- Wyckoff 2.0
- ICT
- Order Flow
- DOM
- Iceberg / hidden liquidity
- Volume Profile / Market Profile
- Risk / PM governance

## Contracts involved

- [ ] MarketSnapshot
- [ ] ATSStructureSnapshot
- [ ] WyckoffICTContext
- [ ] OrderFlowBehavior
- [ ] PhaseClassification
- [ ] StrategySelection
- [ ] SignalCandidate
- [ ] PositionDecision
- [ ] ExecutionIntent
- [ ] ExecutionReport
- [ ] TelemetryEvent

## Acceptance criteria

- [ ] Criteria 1
- [ ] Criteria 2
- [ ] Criteria 3

## Required tests/evidence

- [ ] Unit test
- [ ] Replay test
- [ ] Paper/no-broker test
- [ ] Architecture check
- [ ] Methodology review
- [ ] Quant validation

## Risks

List technical, methodological, execution or live-trading risks.

## Dependencies

List prerequisite issues or documents.

## Definition of Done

- [ ] No MT5 import in core modules.
- [ ] No XAUUSD decision logic in core modules.
- [ ] Canonical contracts respected.
- [ ] Decision Bus event emitted where relevant.
- [ ] Tests/evidence attached.
- [ ] PR reviewed by the correct role.

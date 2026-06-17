# APEX_V3 Recovery Role Model

## Barbara - Product Owner / Methodology Owner

Owns business priority, methodology direction and go/no-go decisions.

## Solutions & Technical Architect Director

Owns the target architecture and recovery sequencing. Blocks changes that violate GC-only, broker-agnostic, Decision Bus, phase-driven or PM independence principles.

## Diagnostic RO

Read-only. Audits code, plans and PRs. Never writes or approves its own changes.

## Production Engineering

Implements approved issues in small PRs. Does not approve its own implementation.

## Methodology / Calibration

Validates ATS, Wyckoff, ICT, Order Flow, DOM, Iceberg and Volume Profile logic.

## Quant / Validation

Owns replay, paper, shadow, EV, MFE/MAE, win rate, profit factor, drawdown and go/no-go validation.

## Separation of duties

```text
Diagnostic RO != Production Engineering approval
Production Engineering != final reviewer of own PR
Methodology review required for phase/playbook/risk changes
Quant validation required for strategy/risk/live-readiness changes
Barbara approves final go/no-go
```

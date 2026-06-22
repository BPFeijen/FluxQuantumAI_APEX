# APEX_V3 Project Governance

## 1. Purpose

This document defines how the FluxQuantumAI_APEX_V3 recovery program is managed inside GitHub.

The recovery must be controlled as an engineering program with strict gates, not as a loose sequence of code changes.

Core rule:

```text
Issue -> Plan -> Branch -> PR -> Review -> Test Evidence -> Approval -> Merge
```

## 2. Repository strategy

Repository:

```text
BPFeijen/FluxQuantumAI_APEX
```

Branches:

```text
main                         -> stable legacy baseline
apex_legacy_mt5_before_v3     -> frozen legacy runtime branch
refactor/apex-v3-recovery     -> active recovery branch
```

No V3 refactoring must be performed directly on `main`.

## 3. Roles

### Barbara / Product Owner / Methodology Owner

- Owns business priority and final go/no-go decisions.
- Validates methodology direction.
- Approves strategic trade-offs.

### Solutions & Technical Architect Director

- Owns target architecture and technical design.
- Breaks work into epics and issues.
- Validates dependencies and sequencing.
- Blocks changes that violate GC-only, broker-agnostic, phase-driven architecture.

### Diagnostic RO

- Read-only auditor.
- Reviews current code and PR plans.
- Never writes code.
- Provides evidence with file/line references when possible.

### Production Engineering

- Implements approved issues only.
- Opens small PRs.
- Does not approve its own code.

### Methodology / Calibration

- Validates ATS, Wyckoff, ICT, Order Flow, DOM, Iceberg and Volume Profile logic.
- Ensures no strategy is introduced without canon support.

### Quant / Validation

- Owns replay, EV, MFE/MAE, win rate, profit factor, drawdown and shadow/paper gates.

## 4. Milestones

Recommended GitHub milestones:

```text
V3.0 - Safety Freeze & Governance
V3.1 - Canon + Contracts + Decision Bus
V3.2 - GC-only Foundation
V3.3 - Feature Providers
V3.4 - Phase Classifier + Strategy Selector
V3.5 - Signal Engine
V3.6 - Position/Risk Manager V2
V3.7 - Observability + Telegram/Dashboard
V3.8 - Replay / Paper / Shadow Validation
V3.9 - Broker Adapter / Live Readiness
```

## 5. Labels

Recommended labels:

```text
type:epic
type:task
type:bug
type:refactor
type:research
type:validation
type:doc

layer:foundation
layer:features
layer:phase
layer:strategy
layer:signal
layer:risk
layer:execution
layer:observability
layer:validation

risk:critical
risk:high
risk:medium
risk:low

arch:gc-only
arch:broker-agnostic
arch:no-mt5-core
arch:no-xauusd-core
arch:decision-bus
arch:phase-driven

status:ready
status:blocked
status:needs-methodology-review
status:needs-quant-validation
status:needs-production-review
status:approved
```

## 6. Definition of Ready

An issue is ready only when it has:

- objective;
- scope;
- out-of-scope section;
- impacted files/modules;
- methodology reference;
- contracts involved;
- acceptance criteria;
- test requirements;
- evidence requirements;
- known risks and dependencies.

## 7. Definition of Done

A task is done only when:

- implementation is merged through PR;
- no MT5 import exists in core modules;
- no XAUUSD reference exists in core modules;
- canonical contracts are respected;
- Decision Bus event is emitted when relevant;
- tests/replay evidence are attached;
- methodology review is complete when needed;
- quant validation is complete when needed;
- rollback path is documented.

## 8. Golden Rules

```text
1. Core is GC-only.
2. Core is broker-agnostic.
3. Broker is adapter, never decision-maker.
4. PM decides even without broker.
5. Feature is not strategy.
6. Signal is not order.
7. Phase comes before strategy.
8. Dashboard and Telegram read Decision Bus, not broker state.
9. Nothing goes live without replay, paper and shadow validation.
10. No module approves itself.
```

## 9. Delivery sequence

The recovery sequence is:

```text
1. Safety Freeze
2. Canon + Contracts
3. Decision Bus
4. GC-only Foundation
5. Broker Abstraction
6. Feature Providers
7. Phase Classifier
8. Strategy Selector
9. Signal Engine
10. Position/Risk Manager V2
11. Observability
12. Replay/Paper/Shadow Validation
13. Broker Live Readiness
```

## 10. Merge policy

All V3 work must target:

```text
refactor/apex-v3-recovery
```

No PR should target `main` until V3 has passed replay, paper, shadow and architecture gates.

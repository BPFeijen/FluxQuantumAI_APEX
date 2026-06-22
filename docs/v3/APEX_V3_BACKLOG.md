# APEX_V3 Recovery Backlog

This document mirrors the initial GitHub Issues created for the recovery program.

## Initial Epics

1. EPIC 01 - Safety Freeze & Repository Governance
2. EPIC 02 - Methodology Canon + Canonical Contracts
3. EPIC 03 - Decision Bus
4. EPIC 04 - GC-only Foundation
5. EPIC 05 - Broker Abstraction
6. EPIC 06 - Feature Providers
7. EPIC 07 - Phase Classifier + Strategy Selector
8. EPIC 08 - Signal Candidate Engine
9. EPIC 09 - Position/Risk Manager V2
10. EPIC 10 - Replay / Paper / Shadow Validation

## Management rule

Every implementation task must be linked to one of these epics and must follow:

```text
Issue -> Plan -> Branch -> PR -> Review -> Test Evidence -> Approval -> Merge
```

## First recommended execution order

```text
EPIC 01 -> EPIC 02 -> EPIC 03 -> EPIC 04 -> EPIC 05
```

No feature refactoring should begin before EPIC 02 and EPIC 03 are accepted.

# FluxQuantumAI_APEX_V3 Recovery Workspace

This directory is the official workspace for the FluxQuantumAI_APEX_V3 recovery/refactoring program.

## Recovery mandate

APEX_V3 must be recovered as a **GC futures, L2-first, methodology-driven, broker-agnostic trading system**.

The recovery does **not** attempt to patch the old monolith. It extracts the useful intelligence from the current APEX runtime and rebuilds the core using explicit contracts, phase-driven logic, and independent risk management.

## Non-negotiable principles

1. **GC-only decision core** — XAUUSD is not allowed inside the core domain.
2. **Broker-agnostic architecture** — MT5, Quantower, FIX, CQG, Rithmic or paper trading must be adapters only.
3. **Decision Bus is source of truth** — dashboard and Telegram consume canonical events, not broker state.
4. **Phase before strategy** — no strategy runs without phase/context classification.
5. **Feature is not strategy** — iceberg, DOM, delta, displacement and profile providers do not make final decisions.
6. **Signal is not order** — Signal Engine emits candidates only.
7. **PM decides even without broker** — Position/Risk Manager must emit OPEN/HOLD/PROTECT/CLOSE/NO_TRADE decisions even when execution is unavailable.
8. **No live execution before replay/paper/shadow validation**.

## Active branch

```text
refactor/apex-v3-recovery
```

## Frozen legacy branch

```text
apex_legacy_mt5_before_v3
```

## Core documents

- `FLUXQUANTUMAI_APEX_V3_REFACTORING_RECOVERY_PLAN.md` — official refactoring/recovery plan.
- `APEX_V3_PROJECT_GOVERNANCE.md` — delivery governance, roles, issue/PR process and gates.

## Target architecture

```text
GC Market Data Foundation
  -> ATS / Wyckoff / ICT Structural Context
  -> Market Behavior Classifier
  -> Phase Classifier
  -> Strategy Selector
  -> Signal Candidate Engine
  -> Position/Risk Manager
  -> Decision Bus
  -> Broker Adapters / Dashboard / Telegram / Audit
```

## Repository policy

`main` remains the stable/legacy baseline. All V3 recovery work must happen through issues and PRs against `refactor/apex-v3-recovery`.

# APEX_V3 Recovery Decision Log

This log records architectural and governance decisions made during the recovery program.

## DL-001 - Recover inside same repository

**Decision:** Use `BPFeijen/FluxQuantumAI_APEX` for V3 recovery.

**Rationale:** The reusable APEX assets, historical commits, audits and Codex work already live in this repository.

**Consequence:** V3 work must be isolated in `refactor/apex-v3-recovery`; no direct refactor on `main`.

## DL-002 - Freeze current legacy runtime

**Decision:** Create `apex_legacy_mt5_before_v3` as frozen legacy branch.

**Rationale:** Current runtime is useful as evidence/reference but must not drive V3 architecture.

## DL-003 - GC-only decision core

**Decision:** GC futures is the only canonical decision instrument.

**Rationale:** Methodology, L2, DOM, iceberg and futures order flow are GC-based. XAUUSD is adapter legacy only.

## DL-004 - Broker is adapter only

**Decision:** MT5/Quantower/FIX/CQG/Rithmic/Paper are execution adapters, not architecture drivers.

## DL-005 - Decision Bus is source of truth

**Decision:** Dashboard, Telegram, replay and audit consume canonical events, not broker state.

## DL-006 - Phase before strategy

**Decision:** No playbook or signal can operate without phase/context classification.

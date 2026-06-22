# FluxQuantumAI_APEX_V3 - Official Refactoring and Recovery Plan

**Owner:** Barbara Pereira / FluxFox Labs  
**Repository:** `BPFeijen/FluxQuantumAI_APEX`  
**Active branch:** `refactor/apex-v3-recovery`  
**Frozen legacy branch:** `apex_legacy_mt5_before_v3`  
**Status:** Official V3 recovery baseline

---

## 1. Executive Decision

FluxQuantumAI_APEX_V3 will be recovered inside the same GitHub repository, but not directly on `main`.

```text
main                         -> stable legacy baseline
apex_legacy_mt5_before_v3     -> frozen legacy runtime branch
refactor/apex-v3-recovery     -> active V3 recovery branch
docs/v3/                      -> official V3 recovery documentation
legacy/                       -> frozen legacy runtime, read-only reference
legacy_quarantine/            -> deprecated artifacts, not deleted blindly
```

A new repository is not recommended during recovery because the reusable APEX assets, audit trail and Codex analysis already live in the current repository.

---

## 2. Recovery Objective

The objective is not to patch the current APEX monolith. The objective is to extract the useful intelligence and rebuild the system as a:

```text
GC futures, L2-first, methodology-driven, phase-aware, broker-agnostic trading system.
```

The recovery must preserve:

```text
GC/L2 ingestion
DOM and order flow features
iceberg and hidden liquidity logic
absorption/delta/displacement logic
volume/profile context
risk/exit policies that can be decoupled from MT5
dashboard and Telegram observability patterns
replay/calibration scripts that validate GC/L2 hypotheses
```

The recovery must remove or isolate:

```text
MT5 inside the decision core
XAUUSD inside the decision core
monolithic EventProcessor decision/execution flow
PositionMonitor dependency on broker positions
gates acting as final strategy
Windows launchers and local operational wrappers
hardcoded C:/ paths inside the new core
broker state as source of truth for dashboard/Telegram
```

---

## 3. Non-Negotiable Architecture Principles

### 3.1 GC-only decision core

The canonical decision instrument is:

```text
GC - Gold Futures CME
```

XAUUSD may exist only in a legacy adapter or historical documentation. It is not allowed in:

```text
core/
foundation/
features/
phase/
strategies/
signals/
risk/
observability/
```

### 3.2 Broker-agnostic execution

Brokers must be adapters. The core cannot import MT5 or any broker SDK directly.

Required adapters:

```text
NullBrokerAdapter
PaperBrokerAdapter
MT5LegacyAdapter
FutureBrokerAdapter placeholder
QuantowerAdapter placeholder
```

### 3.3 Decision Bus is the source of truth

Dashboard, Telegram, replay and audit must consume canonical Decision Bus events, not broker state.

### 3.4 Phase before strategy

No strategy runs without phase/context classification.

### 3.5 Feature is not strategy

Iceberg, DOM, delta, absorption, displacement and profile components emit features/evidence only.

### 3.6 Signal is not order

Signal Engine emits `SignalCandidate`; Position/Risk Manager decides; BrokerAdapter executes.

### 3.7 PM decides even without broker

Position/Risk Manager must publish `OPEN`, `HOLD`, `PROTECT`, `CLOSE`, `NO_TRADE` or `OBSERVE` even when no broker is connected.

---

## 4. Target Architecture

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

---

## 5. Canonical Contracts

The V3 recovery must introduce explicit contracts before major refactoring.

Required contracts:

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

These contracts must be serializable, replayable and auditable.

---

## 6. Layer Responsibilities

### Layer 0 - GC Market Data Foundation

Owns data ingestion and normalization only.

Includes:

```text
Quantower L2 receiver
Iceberg receiver
Databento adapter/reconstructor
Feed health provider
Session provider
M5/M30/D1/H4 builders
```

Must not decide trades.

### Layer 1 - ATS Structural Engine

Maps:

```text
ATS boxes
expansion lines
liquidity lines
institutional levels
regression/value projection
MTF trend
```

### Layer 2 - Wyckoff / ICT Context Engine

Maps:

```text
consolidation
expansion
retracement
reversal
premium/discount
liquidity pools
stop hunts
return to fair value
PD arrays
spring/upthrust hypothesis
SOS/SOW hypothesis
```

### Layer 3 - Market Behavior Classifier

Classifies:

```text
technical market
flow-driven market
absorption vs consolidation
initiative buying/selling
finished/unfinished auction
iceberg defense/trap/confirmation/veto
liquidity pull/removal
```

### Layer 4 - Phase Classifier

Produces phase, subphase, bias, confidence, evidence and invalidation.

Allowed first-pass phases:

```text
CONTRACTION
MANIPULATION
EXPANSION
RETRACEMENT
MARKUP
MARKDOWN
DISTRIBUTION
ROTATION
NEWS_RISK
TRANSITION
UNKNOWN
```

### Layer 5 - Strategy Selector

Enables only playbooks compatible with phase/context.

Initial playbooks:

```text
markup_continuation_pullback_v1
markdown_continuation_pullback_v1
spring_test_long_v1
upthrust_utad_short_v1
breakout_acceptance_v1
rotation_value_fade_v1
news_defensive_v1
```

### Layer 6 - Signal Candidate Engine

Emits normalized signal candidates with evidence and invalidation.

Must not execute orders.

### Layer 7 - Position/Risk Manager V2

Owns final decision:

```text
OPEN
HOLD
PROTECT
MOVE_SL
SCALE_OUT
CLOSE
NO_TRADE
OBSERVE
```

Must be broker-independent.

### Layer 8 - Broker Adapters

Execute intents only. They do not decide strategy or risk.

### Layer 9 - Observability

Dashboard, Telegram, logs and replay consume Decision Bus events.

---

## 7. Reuse Strategy

### Reuse directly or with light changes

```text
quantower_level2_api.py
iceberg_receiver.py
reconstruct_icebergs_databento.py
scripts/databento_to_microstructure.py
live/feed_health.py
watchdog_l2_capture.py
live/price_speed.py
live/kill_zones.py
grenadier_guardrail.py
```

### Refactor before reuse

```text
ats_iceberg_gate.py -> features/iceberg_feature_provider.py
ats_live_gate.py -> features + legacy playbook reference + threshold reference
live/level_detector.py -> context/gc_level_provider.py
live/m5_updater.py -> foundation/bars/m5_context_builder.py
live/m30_updater.py -> foundation/bars/m30_context_builder.py
live/d1_h4_updater.py -> context/macro_context_builder.py
live/operational_rules.py -> risk/operational_policy.py
live/position_monitor.py -> risk policies + position_risk_manager
live/base_dashboard_server.py -> observability/dashboard_api.py
live/telegram_notifier.py -> observability/telegram_publisher.py
live/signal_queue.py -> execution/intent_queue.py if still needed
```

### Isolate as legacy

```text
run_live.py
mt5_executor.py
mt5_executor_hantec.py
live/dashboard_server.py
live/dashboard_server_hantec.py
```

### Quarantine / do not use in V3 core

```text
Windows .bat launchers
local logs
manual MT5 IPC tests
wrappers forcing Roboforex/Hantec execution
old SageMaker jobs unrelated to V3
external material not part of runtime
```

---

## 8. Recovery Roadmap

### Sprint 0 - Safety Freeze & Governance

- Freeze legacy branch.
- Create V3 recovery branch.
- Create governance docs.
- Define PR/Issue process.

### Sprint 1 - Canon + Contracts + Decision Bus

- Create core contracts.
- Create Decision Bus.
- Ensure no-broker/dry-run/paper emit canonical events.

### Sprint 2 - GC-only Foundation

- Extract L2/iceberg/Databento/feed/session providers.
- Remove XAUUSD from core paths.

### Sprint 3 - Broker Abstraction

- Introduce BrokerAdapter interface.
- Implement NullBroker and PaperBroker.
- Isolate MT5 legacy adapters.

### Sprint 4 - Feature Providers

- Convert gates into feature/evidence providers.
- Normalize iceberg, DOM, delta, absorption, displacement, profile and sweep features.

### Sprint 5 - Phase Classifier

- Implement ATS structure, Wyckoff/ICT context and market behavior classifier.
- Emit PhaseClassification.

### Sprint 6 - Strategy Selector + Playbooks

- Add initial phase-compatible playbooks.
- Block strategies when phase/context is incompatible.

### Sprint 7 - Signal Engine

- Emit SignalCandidate only.
- No order execution.

### Sprint 8 - Position/Risk Manager V2

- Split state, risk, sizing, exposure and exit policies.
- Emit PositionDecision even without broker.

### Sprint 9 - Observability

- Refactor dashboard and Telegram to consume Decision Bus.
- Add replay/audit traceability.

### Sprint 10 - Replay / Paper / Shadow Validation

- Validate EV, MFE/MAE, win rate, profit factor, drawdown and phase/playbook behavior.
- No live execution before approval.

---

## 9. Acceptance Gates

APEX_V3 cannot be considered recovered until:

```text
1. Core contains no MT5 imports.
2. Core contains no XAUUSD decision logic.
3. PM emits decisions without broker.
4. Every signal has phase, playbook, evidence and invalidation.
5. Dashboard/Telegram read Decision Bus.
6. Replay is deterministic.
7. Paper mode matches replay behavior.
8. Shadow live is stable.
9. EV by playbook/phase is known.
10. Broker adapter passes open/modify/close UAT.
```

---

## 10. Final Decision

The correct recovery path is:

```text
contract -> extraction -> test -> replay -> replacement -> quarantine -> live readiness
```

Not:

```text
move files -> patch monolith -> hope it works
```

APEX_V3 must be rebuilt around GC market intelligence, methodology-driven phase classification, broker-independent risk decisions and auditable execution intents.

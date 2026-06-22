# APEX_V3 Recommended Labels and Milestones

The GitHub connector used for this setup can create files and issues, but it does not expose a safe label/milestone creation endpoint. This document defines the labels and milestones that should be created manually in GitHub UI or by a maintainer script later.

## Milestones

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

## Type labels

```text
type:epic
type:task
type:bug
type:refactor
type:research
type:validation
type:doc
```

## Layer labels

```text
layer:foundation
layer:features
layer:phase
layer:strategy
layer:signal
layer:risk
layer:execution
layer:observability
layer:validation
```

## Architecture labels

```text
arch:gc-only
arch:broker-agnostic
arch:no-mt5-core
arch:no-xauusd-core
arch:decision-bus
arch:phase-driven
```

## Risk labels

```text
risk:critical
risk:high
risk:medium
risk:low
```

## Status labels

```text
status:ready
status:blocked
status:needs-methodology-review
status:needs-quant-validation
status:needs-production-review
status:approved
```

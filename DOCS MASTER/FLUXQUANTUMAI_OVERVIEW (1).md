# FluxQuantumAI

## Intelligent Trading Signal Orchestration Platform

**Version:** 3.2
**Date:** December 2025
**FluxFox Research Division**
**Update:** Hierarchical Gating Architecture + V7 Trading Rules + IcebergClassifier 99.48%

---

## Executive Summary

**FluxQuantumAI** is an advanced trading signal orchestration platform that combines multiple specialized detection engines to generate high-confidence trading signals for futures markets (MNQ, GC).

The platform integrates four core components that work together to identify institutional activity, detect market anomalies, generate strategic signals, and orchestrate final trading decisions.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│                          F L U X Q U A N T U M A I                          │
│                                                                             │
│              Intelligent Trading Signal Orchestration Platform              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Platform Architecture

```
                              ┌─────────────────────┐
                              │    MARKET DATA      │
                              │  Level 2 / Trades   │
                              └──────────┬──────────┘
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 │                       │                       │
                 ▼                       ▼                       ▼
    ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
    │                    │  │                    │  │                    │
    │    OrderStorm      │  │    AnomalyForge    │  │   Flux Signal      │
    │     Detector       │  │                    │  │     Engine         │
    │                    │  │                    │  │                    │
    │  ┌──────────────┐  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │
    │  │   Iceberg    │  │  │  │   Anomaly    │  │  │  │     DOM      │  │
    │  │  Detection   │  │  │  │   Scoring    │  │  │  │   Signals    │  │
    │  └──────────────┘  │  │  └──────────────┘  │  │  └──────────────┘  │
    │  ┌──────────────┐  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │
    │  │    Size      │  │  │  │    Regime    │  │  │  │     ORB      │  │
    │  │  Prediction  │  │  │  │  Detection   │  │  │  │   Breakout   │  │
    │  └──────────────┘  │  │  └──────────────┘  │  │  └──────────────┘  │
    │  ┌──────────────┐  │  │  ┌──────────────┐  │  │  ┌──────────────┐  │
    │  │ Institutional│  │  │  │    Halt      │  │  │  │    Mean      │  │
    │  │   Tracking   │  │  │  │  Prediction  │  │  │  │  Reversion   │  │
    │  └──────────────┘  │  │  └──────────────┘  │  │  └──────────────┘  │
    │                    │  │                    │  │                    │
    └─────────┬──────────┘  └─────────┬──────────┘  └─────────┬──────────┘
              │                       │                       │
              │    Signal Input       │    Signal Input       │    Signal Input
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │                                     │
                    │        FOXYZE MASTER ENGINE         │
                    │  (Hierarchical Gating v3.2 + V7)    │
                    │                                     │
                    │   ┌─────────────────────────────┐   │
                    │   │  Stage 1: ANOMALY VETO      │   │
                    │   │  Stage 2: NEWS VETO         │   │
                    │   │  Stage 3: L2 PRIMARY        │   │
                    │   │  Stage 4: ICEBERG CONFIRM   │   │
                    │   │  Stage 5: V7 RULES + SIZING │   │
                    │   └─────────────────────────────┘   │
                    │                                     │
                    └──────────────────┬──────────────────┘
                                       │
                                       ▼
                              ┌─────────────────┐
                              │  SIGNAL OUTPUT  │
                              │                 │
                              │  Entry / Exit   │
                              │  Direction      │
                              │  Confidence     │
                              │  Position Size  │
                              └─────────────────┘
                                       │
                                       ▼
                              ┌─────────────────┐
                              │   EXECUTION     │
                              └─────────────────┘
```

---

## Core Components

### 1. OrderStorm Detector

**Iceberg Orders Detection Engine**

```
┌─────────────────────────────────────────────────────────────────┐
│                      ORDERSTORM DETECTOR                         │
│                   "See What Others Can't See"                    │
└─────────────────────────────────────────────────────────────────┘
```

#### What Is It?

OrderStorm Detector is a specialized engine that identifies **hidden institutional orders** (iceberg orders) in real-time. Large traders often hide their true order size to avoid moving the market against them. OrderStorm reveals this hidden activity.

#### What Does It Do?

| Capability | Description |
|------------|-------------|
| **Iceberg Detection** | Identifies hidden orders that only show a fraction of their true size |
| **Type Classification** | Distinguishes between Native (exchange-managed) and Synthetic (ISV-managed) icebergs |
| **Size Prediction** | Estimates the total hidden size using Kaplan-Meier survival analysis |
| **Institutional Tracking** | Monitors where large players are positioning |

#### How Does It Work?

```
┌─────────────────────────────────────────────────────────────────┐
│                     DETECTION PROCESS                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   1. MONITOR                                                     │
│      │  Watch order book for trades that exceed visible size    │
│      ▼                                                           │
│   2. DETECT REFILL                                               │
│      │  New liquidity appears at same price within 100ms        │
│      ▼                                                           │
│   3. CLASSIFY                                                    │
│      │  Native (<10ms refill) vs Synthetic (10-100ms refill)    │
│      ▼                                                           │
│   4. CONFIRM                                                     │
│      │  Pattern repeats 2+ times = confirmed iceberg            │
│      ▼                                                           │
│   5. PREDICT SIZE                                                │
│      │  Kaplan-Meier estimates total hidden volume              │
│      ▼                                                           │
│   6. GENERATE SIGNAL                                             │
│         Iceberg on BID = Institutional buying                   │
│         Iceberg on ASK = Institutional selling                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### Key Insight

> **"Follow the smart money"** - When institutions accumulate positions using icebergs, OrderStorm detects and tracks their footprint, allowing you to align with their direction.

---

### 2. AnomalyForge

**Market Anomaly & Regime Detection Engine**

```
┌─────────────────────────────────────────────────────────────────┐
│                        ANOMALYFORGE                              │
│                   "Know Before It Happens"                       │
└─────────────────────────────────────────────────────────────────┘
```

#### What Is It?

AnomalyForge is an AI-powered engine that continuously monitors market microstructure to detect **abnormal conditions** and **regime changes**. It acts as an early warning system for dangerous market conditions.

#### What Does It Do?

| Capability | Description |
|------------|-------------|
| **Anomaly Scoring** | Real-time score (0-100%) indicating how abnormal current market conditions are |
| **Regime Detection** | Classifies market state: Normal, Trending, High Volatility, Low Liquidity, Pre-Halt |
| **Halt Prediction** | Estimates probability of trading halt before it happens |
| **Risk Assessment** | Provides market health score for position sizing decisions |

#### How Does It Work?

```
┌─────────────────────────────────────────────────────────────────┐
│                      ANOMALY DETECTION                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   MICROSTRUCTURE DATA                                            │
│   │  Price, Volume, Spread, DOM, Delta, Imbalance...            │
│   ▼                                                              │
│   ┌─────────────────────────────────────────┐                   │
│   │         AUTOENCODER NEURAL NETWORK       │                   │
│   │                                          │                   │
│   │   Input → Compress → Reconstruct → Error │                   │
│   │                                          │                   │
│   │   High reconstruction error = Anomaly    │                   │
│   └─────────────────────────────────────────┘                   │
│   │                                                              │
│   ▼                                                              │
│   ANOMALY SCORE: 0.73 (73% abnormal)                            │
│   │                                                              │
│   ▼                                                              │
│   REGIME CLASSIFICATION                                          │
│   │                                                              │
│   │   Score < 0.30  →  NORMAL                                   │
│   │   Score 0.30-0.50  →  CAUTIOUS                              │
│   │   Score 0.50-0.70  →  HIGH VOLATILITY                       │
│   │   Score 0.70-0.90  →  UNSTABLE                              │
│   │   Score > 0.90  →  PRE-HALT / CRISIS                        │
│   │                                                              │
│   ▼                                                              │
│   SIGNAL OUTPUT                                                  │
│      Normal regime → Allow entries                              │
│      High anomaly → Block entries, exit positions               │
│      Pre-halt → Emergency exit                                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### Key Insight

> **"Risk management is the ultimate edge"** - AnomalyForge protects capital by detecting dangerous conditions before they cause losses. It's the platform's safety net.

---

### 3. Flux Signal Engine

**Multi-Strategy Signal Generator**

```
┌─────────────────────────────────────────────────────────────────┐
│                     FLUX SIGNAL ENGINE                           │
│                    "Precision Entry Timing"                      │
└─────────────────────────────────────────────────────────────────┘
```

#### What Is It?

Flux Signal Engine is a multi-strategy signal generator optimized for MNQ futures. It combines four proven strategies to identify high-probability entry and exit points.

#### What Does It Do?

| Strategy | Win Rate | Description |
|----------|----------|-------------|
| **DOM Imbalance** | 60.8% | Detects buyer/seller pressure imbalance in order book |
| **ORB Breakout** | 74.6% | Captures momentum from Opening Range Breakout |
| **Mean Reversion** | 56.2% | Fades extended moves from NY open |
| **Absorption** | ~65% | Identifies large orders being absorbed without price movement |

#### How Does It Work?

```
┌─────────────────────────────────────────────────────────────────┐
│                    SIGNAL GENERATION                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────┐│
│   │     DOM     │  │     ORB     │  │    Mean     │  │ Absorp ││
│   │  Imbalance  │  │  Breakout   │  │  Reversion  │  │ -tion  ││
│   │   (30%)     │  │   (25%)     │  │   (20%)     │  │ (25%)  ││
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └───┬────┘│
│          │                │                │              │     │
│          │    LONG +0.6   │   LONG +0.8    │  NEUTRAL 0   │SHORT│
│          │                │                │              │-0.3 │
│          │                │                │              │     │
│          └────────────────┼────────────────┼──────────────┘     │
│                           │                │                     │
│                           ▼                ▼                     │
│                    ┌─────────────────────────────┐              │
│                    │    INTERNAL AGGREGATION     │              │
│                    │                             │              │
│                    │  Score = 0.30×DOM + 0.25×ORB│              │
│                    │        + 0.20×MR + 0.25×ABS │              │
│                    │                             │              │
│                    │  = 0.30×0.6 + 0.25×0.8      │              │
│                    │    + 0.20×0 + 0.25×(-0.3)   │              │
│                    │                             │              │
│                    │  = 0.18 + 0.20 + 0 - 0.075  │              │
│                    │  = 0.305 → WEAK LONG        │              │
│                    └─────────────────────────────┘              │
│                                                                  │
│   SESSION AWARENESS                                              │
│   ├── NY Session (09:30-16:00): Full signals (1.0x)             │
│   ├── London (03:00-09:30): Reduced (0.7x)                      │
│   └── Asia (18:00-02:00): Minimal (0.5x)                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### Key Insight

> **"Multiple confirmations increase probability"** - By combining four independent strategies, Flux Signal Engine filters noise and identifies setups where multiple factors align.

---

### 4. Foxyze Master Engine

**Central Signal Orchestrator - Hierarchical Gating Architecture**

```
┌─────────────────────────────────────────────────────────────────┐
│                    FOXYZE MASTER ENGINE                          │
│              "Hierarchical Gating - Not Averaging"               │
└─────────────────────────────────────────────────────────────────┘
```

#### What Is It?

Foxyze Master Engine is the central orchestrator that receives signals from all detection engines and produces the final trading decision using **Hierarchical Gating Architecture** - a 4-stage decision process where each signal has a specific ROLE.

#### Why NOT Weighted Average?

The old approach (v1.0-v2.0) used weighted averaging:
```
Score = (Anomaly × 0.3) + (L2 × 0.4) + (Iceberg × 0.3) = ???
```

**Problem:** These signals measure DIFFERENT things:
- Anomaly → RISK (0-1)
- L2 → DIRECTION (-1 to +1)
- Iceberg → CONFIRMATION (0-1)

Mixing them produces meaningless numbers!

#### How Does It Work? (4 Stages)

```
┌─────────────────────────────────────────────────────────────────┐
│              HIERARCHICAL GATING ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   STAGE 1: ANOMALY GATE (VETO POWER)                            │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                                                          │   │
│   │  Anomaly Score > 0.95  →  BLOCK TRADE COMPLETELY        │   │
│   │  Anomaly Score > 0.85  →  REDUCE SIZE TO 25%            │   │
│   │  Anomaly Score > 0.70  →  REDUCE SIZE TO 50%            │   │
│   │  Anomaly Score < 0.70  →  PROCEED (full size)           │   │
│   │                                                          │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│   STAGE 2: L2 SIGNAL (PRIMARY - DIRECTION)                      │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                                                          │   │
│   │  DOM Imbalance < -0.30  →  LONG (sellers exhausted)     │   │
│   │  DOM Imbalance > +0.30  →  SHORT (buyers exhausted)     │   │
│   │  |Signal| < 0.30        →  NO ACTION (weak signal)      │   │
│   │                                                          │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│   STAGE 3: ICEBERG CONFLUENCE (CONFIRMATION)                    │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                                                          │   │
│   │  Iceberg ALIGNED with direction   →  +30% confidence    │   │
│   │  Iceberg OPPOSED to direction     →  -25% size          │   │
│   │  No iceberg detected              →  Normal size        │   │
│   │                                                          │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│   STAGE 4: FINAL DECISION                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                                                          │   │
│   │  ╔═══════════════════════════════════════════════════╗  │   │
│   │  ║           SIGNAL OUTPUT: ENTRY LONG               ║  │   │
│   │  ╠═══════════════════════════════════════════════════╣  │   │
│   │  ║  Direction:    LONG (from L2 Signal)              ║  │   │
│   │  ║  Position:     65% (50% anomaly × 1.30 iceberg)   ║  │   │
│   │  ║  Confidence:   78% (L2 conf × iceberg bonus)      ║  │   │
│   │  ║  Stop Loss:    -8 ticks                           ║  │   │
│   │  ║  Target:       +16 ticks                          ║  │   │
│   │  ╚═══════════════════════════════════════════════════╝  │   │
│   │                                                          │   │
│   └─────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### Decision Examples

| Anomaly | L2 Signal | Iceberg | Result |
|---------|-----------|---------|--------|
| 0.40 (OK) | LONG +0.6 | BID (aligned) | ✅ ENTRY LONG, size=130% |
| 0.40 (OK) | SHORT -0.7 | ASK (aligned) | ✅ ENTRY SHORT, size=130% |
| 0.72 (MED) | LONG +0.6 | BID (aligned) | ⚠️ ENTRY LONG, size=65% |
| **0.96** | LONG +0.9 | BID (aligned) | ❌ **BLOCKED** (VETO) |
| 0.40 (OK) | WEAK +0.2 | BID | ❌ NO ACTION (weak L2) |

#### Key Insight

> **"Each signal has its ROLE, not its WEIGHT"** - Anomaly protects (VETO), L2 decides (DIRECTION), Iceberg confirms (ADJUSTMENT). This is mathematically correct for heterogeneous signals.

---

## Complete Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FLUXQUANTUMAI WORKFLOW                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        1. DATA INGESTION                              │   │
│  │                                                                       │   │
│  │    Quantower Platform                                                 │   │
│  │         │                                                             │   │
│  │         ├── Level 2 Order Book (10 levels)                           │   │
│  │         ├── Time & Sales (trades)                                    │   │
│  │         ├── Volume Profile                                           │   │
│  │         └── Market Statistics                                        │   │
│  │         │                                                             │   │
│  │         ▼                                                             │   │
│  │    HTTP POST to APIs (ports 8000, 8001, 8002)                        │   │
│  │                                                                       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                      │                                       │
│                                      ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     2. PARALLEL ANALYSIS                              │   │
│  │                                                                       │   │
│  │    ┌──────────────┐   ┌──────────────┐   ┌──────────────┐            │   │
│  │    │  OrderStorm  │   │ AnomalyForge │   │ Flux Signal  │            │   │
│  │    │              │   │              │   │   Engine     │            │   │
│  │    │  Detecting   │   │  Scoring     │   │              │            │   │
│  │    │  icebergs    │   │  anomalies   │   │  Generating  │            │   │
│  │    │  at 20125.25 │   │  score: 0.35 │   │  DOM signal  │            │   │
│  │    │              │   │  regime: OK  │   │              │            │   │
│  │    └──────┬───────┘   └──────┬───────┘   └──────┬───────┘            │   │
│  │           │                  │                  │                     │   │
│  │           └──────────────────┼──────────────────┘                     │   │
│  │                              │                                        │   │
│  └──────────────────────────────┼───────────────────────────────────────┘   │
│                                 │                                            │
│                                 ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                   3. SIGNAL ORCHESTRATION                             │   │
│  │                                                                       │   │
│  │                     Foxyze Master Engine                              │   │
│  │                              │                                        │   │
│  │            ┌─────────────────┼─────────────────┐                     │   │
│  │            │                 │                 │                      │   │
│  │            ▼                 ▼                 ▼                      │   │
│  │       ┌─────────┐      ┌─────────┐      ┌─────────┐                  │   │
│  │       │ Combine │ ──►  │Consensus│ ──►  │  Risk   │                  │   │
│  │       │ Signals │      │  Check  │      │  Check  │                  │   │
│  │       └─────────┘      └─────────┘      └─────────┘                  │   │
│  │                                               │                       │   │
│  │                                               ▼                       │   │
│  │                                         ┌──────────┐                  │   │
│  │                                         │  SIGNAL  │                  │   │
│  │                                         │  OUTPUT  │                  │   │
│  │                                         └──────────┘                  │   │
│  │                                                                       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                 │                                            │
│                                 ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                       4. EXECUTION                                    │   │
│  │                                                                       │   │
│  │    Signal: ENTRY LONG @ 20125.50                                     │   │
│  │         │                                                             │   │
│  │         ├── Validate with broker                                     │   │
│  │         ├── Calculate position size                                  │   │
│  │         ├── Place order with stop/target                             │   │
│  │         └── Monitor and manage                                       │   │
│  │                                                                       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Benefits

### 1. Institutional Edge

OrderStorm Detector reveals hidden institutional activity that retail traders cannot see, allowing you to align with smart money.

### 2. Risk Protection

AnomalyForge acts as a continuous safety net, blocking trades during dangerous conditions and forcing exits before catastrophic events.

### 3. Multiple Confirmation

Foxyze Master Engine combines signals from independent sources, only executing when multiple factors align.

### 4. Adaptive Risk Management

Position sizing automatically adjusts based on confidence, consensus, and daily performance.

### 5. Session Optimization

All components are optimized for NY session characteristics where statistical edges are strongest.

---

## Technical Specifications

| Component | Technology | Latency Target |
|-----------|------------|----------------|
| OrderStorm Detector | Python + Heuristics + Kaplan-Meier | < 50ms |
| AnomalyForge | PyTorch Autoencoder | < 100ms |
| Flux Signal Engine | Python + Strategy Logic | < 30ms |
| Foxyze Master Engine | Python Orchestrator | < 10ms |
| **Total Pipeline** | End-to-End | **< 200ms** |

---

## Provider Roles (Hierarchical Gating v3.2)

```
┌─────────────────────────────────────────────────────────────────┐
│                    HIERARCHICAL ROLES                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   AnomalyForge           ████████████████████████████  STAGE 1  │
│   ROLE: VETO POWER (blocks dangerous trades)                     │
│   - Score > 0.95 → BLOCK completely                              │
│   - Score > 0.85 → Size × 25%                                    │
│   - Score > 0.70 → Size × 50%                                    │
│                                                                  │
│   L2 Signal Generator    ████████████████████████████  STAGE 2  │
│   ROLE: PRIMARY (determines direction)                           │
│   - DOM < -0.30 → LONG                                           │
│   - DOM > +0.30 → SHORT                                          │
│                                                                  │
│   OrderStorm (Iceberg)   ████████████████████████████  STAGE 3  │
│   ROLE: CONFIRMATION (adjusts confidence)                        │
│   - Aligned → +30% size                                          │
│   - Opposed → -25% size                                          │
│                                                                  │
│   ══════════════════════════════════════════════════════════    │
│   NOTE: No more "weights" - each signal has a ROLE              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Summary

FluxQuantumAI is a complete trading signal orchestration platform that:

1. **Sees** hidden institutional orders (OrderStorm)
2. **Protects** against dangerous market conditions (AnomalyForge)
3. **Identifies** high-probability setups (Flux Signal Engine)
4. **Decides** intelligently by combining all sources (Foxyze Master Engine)

The result is a system that generates trading signals with multiple confirmations, built-in risk management, and institutional-grade detection capabilities.

---

## ML Model Performance (v3.0)

| Model | Accuracy | Description |
|-------|----------|-------------|
| **IcebergClassifier** | **99.48%** | Classifies iceberg orders |
| **MicrostructureAutoencoder** | 95%+ | Anomaly detection |
| **RegimeDetector** | ~90% | Market regime classification |

---

## References

- DXFeed Iceberg Detection Solution
- CME Iceberg Order Detection (Zotikov, 2021)
- Kaplan-Meier Survival Analysis
- Autoencoder-based Anomaly Detection
- **Hierarchical Gating Architecture (FluxFox, 2025)**

---

*FluxQuantumAI v3.2 - Hierarchical Gating Architecture + V7 Trading Rules*
*FluxFox Research Division*
*December 2025*

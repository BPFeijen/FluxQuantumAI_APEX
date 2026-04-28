# DECISIONS_LOG.md — Barbara's Architectural & Operational Decisions

**Purpose:** Fiducial record. Every architectural or operational decision made by Barbara that affects FluxQuantumAI is logged here with context, rationale, and implications. This document exists to prevent decisions from being lost across sessions, agents, or chat windows.

**Convention:** Decisions are immutable once logged. Corrections are added as new entries with reference to the original.

**Index format:** `DEC-YYYY-MM-DD-NNN` where NNN is the decision number within that day.

---

## DEC-2026-04-21-001 — ROLLBACK-001: Remove POST_VAL bypass + M30_BIAS_BYPASS

**Date decided:** 2026-04-21
**Status:** ✅ Executed (commit `fb7b42d` on `safety/rollback-20260421`)
**Context**
On 2026-04-21, two safety bypasses were active simultaneously in the live system:
1. M30 bias bypass (committed in PR #4 on 13:43)
2. POST_VAL bypass (uncommitted disk edit by Codex, later the same day)

Combined with pre-existing `dual_strategy_enabled=False`, bias check was bypassed in 100% of trades. Investigation into why POST_VAL was "blocking too much" revealed no data-driven calibration — the bypass was added without threshold justification.

**Decision**
Surgical revert of both bypasses. Pre-deploy state (`8e91465`) restored for the affected logic. Commit tagged `rollback-001-safety-tag` at `8a24bc9` as emergency undo anchor.

**Rationale**
- Barbara's operational rule: no thresholds without historical data. A bypass without calibration violates this.
- Pre-deploy behavior had been stable for 4+ days before the bypasses were added.
- MT5 execution was already broken, so the live impact was limited to Telegram signals — but that does not justify leaving known-bad logic in place.

**Implications**
- `live/event_processor.py` reverted to pre-deploy behavior for POST_VAL and M30 bias paths
- `tests/test_m30_bias_authority_fix.py` renamed to `_DEPRECATED_20260421.py` with skip marker
- System back to consistent pre-deploy behavior
- Does not address root causes of PP1/PP2/PP4 — only stabilizes baseline

**Related artifacts:** `ROLLBACK-001_surgical_revert.md`, SYNC-001 audit

---

## DEC-2026-04-22-001 — Methodology framework: Wyckoff = ATS, ICT complementary

**Date decided:** 2026-04-22
**Status:** ✅ Active (applied to all documentation and tasks)
**Context**
Literature review identified 4 relevant sources:
- Wyckoff 2.0 (Rubén Villahermosa)
- ATS Trade methodology (17 video transcripts)
- ICT SMC (David Woods)
- Volume Profile / Order Flow (Johannes Forthmann)

Early drafts of FOUNDATION.md positioned these as 4 independent frameworks, creating confusion about which takes precedence in case of conflict.

**Decision**
Treat Wyckoff and ATS as the **same methodology** with different vocabularies (Wyckoff uses classical terminology from 1920s-1940s; ATS uses modern 2020s terminology for the same concepts). ICT is **complementary** — adds liquidity hierarchy and session timing that Wyckoff doesn't explicitly cover. Forthmann provides **detection mechanics** (absorption, delta divergence, iceberg) for concepts that Wyckoff describes qualitatively.

**Rationale**
- ATS's 3-phase model (CONTRACTION, EXPANSION, TREND) maps 1:1 to Pruden's Wyckoff 5-phase (A, B, C, D, E) when you merge transitions
- ATS's boxes = Wyckoff's trading ranges
- ATS's expansion lines = Wyckoff's VPOC / value area midpoint concept
- ATS's liquidity lines = Wyckoff's range extremes / creek-ice
- The FluxQuantumAI codebase uses ATS vocabulary (phase_state, boxes, fmv, liq_top/bot), so documentation must match

**Implications**
- Cite Wyckoff and ATS together when they describe the same concept
- Do not invent artificial conflicts between sources
- ICT contributions are specific: liquidity hierarchy (Major/Medium/Minor) maps to HTF→LTF bias architecture; session timing feeds `kill_zones.py`
- Forthmann is the reference for implementing absorption/divergence detectors that Wyckoff only describes qualitatively

**Related artifacts:** `FOUNDATION_v2.md`, `PAIN_POINTS_BACKLOG.md`

---

## DEC-2026-04-22-002 — Pain Point 3 (MT5 integration) deferred

**Date decided:** 2026-04-22
**Status:** ✅ Active
**Context**
4 Pain Points were identified:
1. System without BIAS
2. Missing reversal points
3. MT5 integration broken (Hantec never worked)
4. Strategies not aligned with market phases

PP3 is a pure engineering problem — literature provides no input. Attempting to fix MT5 while PP1/PP2/PP4 still produce bad signals means automating bad signals, which amplifies loss.

**Decision**
Defer PP3 until PP1/PP2/PP4 are resolved. Signal quality first, execution second.

**Rationale**
- No point connecting a broken signal pipeline to execution
- MT5 is an engineering dependency, orthogonal to the methodology
- Fixing execution without signal quality = faster losses

**Implications**
- `FluxQuantumAPEX_Live` service remains in FAILED state (known)
- Signals are currently Telegram-only (no financial loss during this sprint)
- PP3 returns to roadmap after PP1/PP2/PP4 are validated

---

## DEC-2026-04-22-003 — NextGen multi-agent architecture deferred

**Date decided:** 2026-04-22
**Status:** ✅ Active (plan documented in `FluxQuantumAI_NextGen_Arquitetura_Detalhada.docx`)
**Context**
Barbara has a detailed architectural plan for FluxQuantumAI as a multi-agent system (Market Context Agent, Liquidity Map Agent, Structure & Phase Agent, Value & Inefficiency Agent, Session & Time Agent, Anomalies Agent, News Agent, Normal Strategy Desk, High Risk Strategy Desk, Predictive Modules, Iceberg Orders Agent, Order Flow Trigger Agent, Master Mind, Execution Engine, Position/Risk Manager, Trade Journal / Learning Agent).

**Decision**
Do not begin migration to NextGen architecture until current Pain Points are resolved. Current sprint focus: stabilize the existing system.

**Rationale**
- Current system has active bugs (PP1, PP2, PP4) that the NextGen plan assumes are absent
- Rewriting in NextGen form without first understanding the current failures risks reproducing them at architectural level
- NextGen plan is sound and will inform post-sprint roadmap

**Implications**
- ClaudeCode does not modify live code toward multi-agent patterns during this sprint
- NextGen plan is reference material only for this sprint
- After PP1/PP2/PP4 closure, NextGen migration becomes the next priority

---

## DEC-2026-04-22-004 — Scalp/Swing Mode Switch

**Date decided:** 2026-04-22
**Status:** Pending implementation (T1-NEW-SCALP)
**Context**
T0-A analysis revealed AMBIGUOUS bias signals have paradoxical behavior:
- Short horizon (+5m, +30m): mean +5.92 / +10.11 pts, WR 54.8% / 67.5% → profitable
- Long horizon (+60m, +240m): mean −3.52 / −16.57 pts, WR 35.4% / 20.3% → catastrophic

Original plan (T1-A in v1.1) was to block AMBIGUOUS signals entirely. This would eliminate signals that are profitable in the short term.

**Decision**
Implement a state machine in Master Mind that routes signals by bias confidence:

```
IF bias_confirmed == True (bullish OR bearish):
    mode = SWING
    execution_profile = swing (hold >60m, wide trailing, VP targets)
    allow_continuation: YES
    allow_fade: context-dependent

ELIF bias state in {AMBIGUOUS, STALE}:
    mode = SCALP
    execution_profile = scalp (hold <30m, tight exit, near targets)
    allow_continuation: NO
    allow_fade: conditional

ELIF bias state == BOOTSTRAP:
    mode = BLOCKED
    reason = "Insufficient data — no operation"
```

**Rationale**
- Data-driven: exploits the actual observed profitability pattern of AMBIGUOUS signals
- Recovers alpha that a naive block would throw away
- Matches Wyckoff/ATS principle: strategy must match market regime

**Implications**
- Master Mind gains a state machine component
- Position Manager needs two distinct execution profiles
- All trades gain an `entry_mode` audit field (SCALP or SWING)
- Exit hold-time for SCALP must be calibrated from SA-2 sub-analysis (inflection point analysis)
- Mode transitions within an active trade: TBD in design doc (T1-NEW-SCALP.1)

**Dependencies**
- SA-2 (AMBIGUOUS paradox analysis) must complete first to calibrate scalp hold-time
- Design doc must be approved by Barbara before implementation

**Related artifacts:** `CLAUDECODE_BRIEF_v2.md` T1-NEW-SCALP

---

## DEC-2026-04-22-005 — Position Monitor decoupling from MT5

**Date decided:** 2026-04-22
**Status:** Pending implementation (T1-NEW-PM)
**Context**
Tier 0 audit revealed that the current architecture couples Position Monitor to MT5 liveness. When MT5 is down (current state for Hantec), Position Monitor does not alert any channel — including Telegram and Dashboard. Barbara stated explicitly: "Nunca decidi por esta arquitetura."

**Decision**
Position Monitor must run **always**, independent of MT5 state. It must emit alerts to all subscriber channels (Telegram, Dashboard, Database journal) regardless of whether any downstream action (MT5 execution) succeeds or fails.

Target behavior:
- Position Monitor lifecycle independent of broker adapter lifecycle
- Alerts are best-effort per channel (one channel failing does not block others)
- If MT5 is down, alert states "signal generated, MT5 did not execute" — monitoring continues
- All signals are audit-logged in the Trade Journal regardless of execution outcome

**Rationale**
- Architectural: the NextGen design mandates Execution Engine as the only broker-facing component, Position Manager as the only risk-protection component. Current coupling violates both.
- Operational: when execution fails, Barbara still needs visibility into what signals the system wanted to take
- Evidence preservation: without independent monitoring, we lose the "ground truth" for post-mortems

**Implications**
- `live/position_monitor.py` must be refactored to remove MT5 dependency from the main loop
- Alert emission logic must be factored into a multi-subscriber broadcaster (Telegram, Dashboard, DB) — not a sequential chain
- Broker adapter (MT5 / Quantower future) is called as a best-effort subscriber, not as a prerequisite
- All current alert logic that skips on MT5 failure must be audited and rewritten

**Dependencies**
- T1-NEW-PM.1 coupling audit must complete first (map every MT5 touchpoint)
- Design doc approved by Barbara before refactor
- High risk: core runtime module. Requires comprehensive test coverage before deploy

**Related artifacts:** `CLAUDECODE_BRIEF_v2.md` T1-NEW-PM

---

## DEC-2026-04-22-006 — Documentation-First + Activation Checklist discipline

**Date decided:** 2026-04-22
**Status:** ✅ Active from this date forward
**Context**
Tier 0 (T0-E) audit revealed a concerning pattern: 7 of 9 reversal detectors are in SHADOW/DISABLED/TODO state. Detectors appear to have been developed but never promoted to live. Barbara stated: "a gente desenvolve e no final, claudecode nao ativa!" — development finishes but activation doesn't happen.

**Decision**
Two disciplines mandatory for all T1+ tasks:

**1. Documentation-First**
- Design doc written BEFORE implementation code
- Doc approved by Barbara before any code is written
- Template location: `C:\FluxQuantumAI\docs\designs\`
- Template includes: Problem, Investigation findings, Proposed solution, Alternatives considered, Risk analysis, Rollback plan, Test cases, Open questions

**2. Activation Checklist (8 checkpoints)**
No task is "done" until all 8 confirmed:
1. Implementation complete (code + tests)
2. Documentation written
3. Backtest / historical validation done
4. SHADOW vs LIVE decision documented
5. Config toggle set
6. Barbara confirmed activation
7. Live service deployment (by Barbara)
8. Post-deployment sanity check green

**Rationale**
- Forces explicit gates that prevent "finished but not activated"
- Makes documentation a first-class deliverable, not an afterthought
- Separates "code works in isolation" from "code is running in production" — which is where the gap was

**Implications**
- Every task progresses slower but with certainty
- Barbara has explicit approval points at documentation stage and at activation stage
- Historical SHADOW detectors (per T0-E) can be audited against this checklist to identify where each one stalled
- Status reporting uses `[ID] X/8 checklist complete` format

---

## DEC-2026-04-22-007 — Block `bias=unknown` for swing, route to scalp

**Date decided:** 2026-04-22
**Status:** Absorbed by DEC-2026-04-22-004
**Context**
Original T1-A plan (v1.1 brief): block all `bias=unknown` signals. Initial reasoning from T0-A data: AMBIGUOUS signals have catastrophic outcomes at long horizons. Preserved here for traceability of the evolution.

**Decision**
Absorbed into DEC-2026-04-22-004 (Scalp/Swing Mode Switch). No separate blocking rule — AMBIGUOUS signals are routed to SCALP mode instead of blocked.

**Rationale for absorption**
A blanket block would lose the +5m/+30m profitability. Routing to scalp mode preserves the alpha while bounding the downside.

---

## DEC-2026-04-22-008 — `dual_strategy_enabled` current state is True; validate, do not toggle

**Date decided:** 2026-04-22
**Status:** Pending validation (T1-D)
**Context**
v1.1 brief assumed `dual_strategy_enabled=False`. Tier 0 (T0-E config check) revealed it is actually `True` in `config/settings.json`. No record exists of when or by whom this was set to True.

**Decision**
Treat current True state as the working baseline. Validate historically that this configuration produces correct phase→strategy mapping. Do not revert; do not re-toggle.

**Rationale**
- The True setting aligns with literature (Strategy 1 in CONTRACTION, Strategy 2 in EXPANSION/TREND)
- Reverting would be undocumented tampering
- If validation shows the setting is not producing expected behavior, investigation points to downstream phase detection or strategy dispatch, not the flag itself

**Implications**
- T1-D becomes "historical validation of current setting" rather than "enable and validate"
- If validation fails, the failure mode informs separate investigation

---

## DEC-2026-04-22-009 — Bias in trading range has a value (bullish OR bearish), not unknown

**Date decided:** 2026-04-22
**Status:** ✅ Active (literature interpretation; affects all bias-related tasks)
**Context**
Question arose during Tier 0: when the market is laterally ranging (no clear directional move), should `m30_bias` be `unknown`? Villahermosa's Wyckoff 2.0 §5.7.2 and §7.1.3 were consulted.

**Decision**
Mercado lateralizado (sideways / trading range) does NOT equal `bias=unknown`. In a range, bias is determined by price position relative to the last High Volume Node (HVN):
- Price above HVN → bias = `bullish`
- Price below HVN → bias = `bearish`

What changes in a range is the **strategy** (fade extremes per Strategy 1), not the existence of bias.

**Rationale (literature, verbatim)**

Wyckoff 2.0 §7.1.3:
> "The last High Volume Node generated will be the one that determines the directional bias at least in the shortest term. As long as the price remains above, we will only propose bullish scenarios and vice versa if we find ourselves at the bottom."

Wyckoff 2.0 §5.7.2:
> "We will always favor the operation in the direction of the last high volume node that has been generated."

**Implications**
- `bias=unknown` semantically valid only in three cases:
  1. BOOTSTRAP — insufficient data to compute
  2. STALE — capture service gap or feed lag invalidated recent HVN
  3. AMBIGUOUS — multiple structural signals conflict at equilibrium, not a neutral state
- A `RANGING` value as `bias=unknown` would indicate a **bug in bias computation**, not a legitimate state
- T0-A found zero RANGING records in current code → bug not confirmed in this sample; monitor continues

**Related artifacts:** T0-A analysis, `FOUNDATION_v2.md`

---

## DEC-2026-04-22-010 — Platform must support both scalp and swing, system decides which

**Date decided:** 2026-04-22
**Status:** ✅ Active (drives DEC-2026-04-22-004 implementation)
**Context**
Clarifying question: is FluxQuantumAI a scalp-only, swing-only, or hybrid system?

**Decision**
FluxQuantumAI is a hybrid. The system itself decides, per-trade, whether the current opportunity is scalp or swing, based on bias confidence (see DEC-2026-04-22-004). The operator does not switch modes manually.

**Rationale**
- Different market regimes favor different hold times
- Automating the regime detection eliminates operator discretion, which is consistent with the broader FluxQuantumAI philosophy (system decisions, human supervision)
- T0-A evidence shows AMBIGUOUS signals have regime-specific profitability windows — aligning exit to the regime maximizes expectancy

**Implications**
- All downstream modules (Position Manager, Execution Engine, Trade Journal) must be mode-aware
- Historical analysis (e.g., hold-time calibration for scalp) can be done even without real trades, using signal forward-returns (T0 data)

---

## DEC-2026-04-24-001 — Tri-methodological foundation

**Date decided:** 2026-04-24
**Status:** ✅ Active
**Supersedes:** DEC-2026-04-22-001 (Methodology: Wyckoff=ATS, ICT complementary)
**Approved by:** Barbara Feijen

## Context

DEC-2026-04-22-001 treated Wyckoff and ATS as "same methodology with different vocabularies" and positioned ICT as "complementary". This framing was a working simplification that avoided artificial conflict between sources when they described the same concept.

On 2026-04-24, methodological audit surfaced operationally meaningful differences between Wyckoff 5-phase and ATS 3-phase that the previous framing had masked:

- Wyckoff's sub-events (Spring, Upthrust, SOS, SOW, BUEC, LPS, SC, AR, ST) have no ATS equivalent and are direct triggers for reversal entries (directly relevant to PP2).
- Wyckoff classifies accumulation vs distribution directionally from Phase A; ATS treats Contraction as directionally neutral until Expansion reveals.
- Wyckoff uses Law of Cause and Effect for targets (point-and-figure horizontal counts); ATS uses price levels (expansion line, liquidity lines, order block projections).
- Wyckoff is a-posteriori confirmation (Villahermosa explicit); ATS has real-time confirmation mechanics (box colors, confirmation rule).
- ICT covers session timing, killzones, weekly profiles, liquidity hierarchy, FVG, and news-event mechanics that neither Wyckoff nor ATS formalize.

Each methodology is strong where the others are weak. Forcing them into a single vocabulary loses information.

## Decision

The FluxQuantumAI methodological foundation is the explicit union of three complementary frameworks plus one detection-mechanics source:

- **Wyckoff 2.0** (Villahermosa + Pruden 5-phase + Schroeder primary positions)
- **ATS Master Pattern** (3-phase + boxes + expansion/liquidity/institutional/order-block lines + regression line)
- **ICT Institutional SMC** (liquidity hierarchy Major/Medium/Minor, FVG, Order Blocks, kill zones, AMD cycle, premium/discount, weekly profiles, high-impact news)
- **Forthmann + Christensen** (absorption, iceberg detection, delta mechanics — tool, not methodology)

None of the three methodologies is complete in isolation. The architecture integrates the strongest elements of each, assigned to the module where that methodology is most authoritative.

## Methodology coverage per NextGen module (authoritative)

| Module | Primary | Secondary | Detection mechanics |
|---|---|---|---|
| Market Context Agent | ATS 3-phase | Wyckoff 5-phase | — |
| Liquidity Map Agent | ICT | ATS | — |
| Structure & Phase Agent | Wyckoff | ATS | — |
| Value & Inefficiency Agent | Wyckoff (Volume Profile) | ICT (FVG + Premium/Discount) | — |
| Session & Time Agent | ICT | — | — |
| Anomalies Agent | Statistical extension | — | ML |
| News Agent | ICT | — | — |
| Normal Strategy Desk | Wyckoff playbooks | ATS Basic Strategy + ICT | — |
| High Risk Strategy Desk | ICT | — | — |
| Iceberg Orders Agent | — | — | Forthmann + Christensen + ML |
| Order Flow Trigger Agent | Wyckoff (absorption + iniciativa at key zones) | ICT (displacement at FVG) | Forthmann |
| Master Mind | Architectural | — | — |
| Execution Engine | Architectural | — | — |
| Position / Risk Manager | Wyckoff position management | ICT news-driven adjustments | — |
| Trade Journal | Architectural | — | — |

## Contract-level implications

### Structure & Phase Agent contract (critical module)

Exposes multiple taxonomies simultaneously, not as aliases but as independent fields:

```
phase_ats          : CONTRACTION | EXPANSION | TREND
phase_wyckoff      : A | B | C | D | E
structure_type     : accumulation | distribution | reaccumulation | redistribution
sub_event_wyckoff  : SC | AR | ST | SPRING | UPTHRUST | SOS | SOW | BUEC | LPS | null
phase_ict_amd      : ACCUMULATION | MANIPULATION | DISTRIBUTION
confidence         : 0-1 per field
```

Each field has independent confidence because the three frameworks resolve at different speeds (ATS = real-time, Wyckoff = a-posteriori confirmation, ICT AMD = intraday cycle).

### Playbook attribution

Playbooks within strategy desks are NOT fused across methodologies. They coexist as parallel families with explicit methodology attribution:

- "Spring reversal at range low" → Wyckoff
- "Basic Strategy S1 (range-bound)" → ATS
- "Basic Strategy S2 (trending)" → ATS
- "Premium-to-Discount run" → ICT
- "BUEC entry after breakout test" → Wyckoff
- "Displacement confirmation at FVG" → ICT
- (etc — full library in fit-gap doc)

Each playbook declares its methodology in the Strategy contract. Master Mind sees methodology as metadata when consolidating, enabling per-methodology performance tracking in Trade Journal.

### Conflict resolution rule

When methodologies appear to conflict in real-time output, conflict is first tested for resolution via methodology coverage:
- Is this a question Wyckoff answers best? Defer to Wyckoff.
- Is it a liquidity/session/news question? Defer to ICT.
- Is it a real-time structure/level question? Defer to ATS.

Only after coverage test fails is the conflict treated as real and escalated for resolution.

### Literary citations

Citations in documentation, specs, and brief-backs are per-methodology explicit:

- NOT: "the methodology says..."
- YES: "Wyckoff (Villahermosa 2.0, Phase C) says..." / "ICT (Bible R4) says..." / "ATS (Strategic Plan §4) says..."

This is enforceable under Rule 1 (G-LITERATURE-BEFORE-CODE) and Rule 15 (G-METHODOLOGY-FIRST-WORKFLOW).

## Implications for existing artifacts

- **FOUNDATION_v2.md**: needs revision to reflect tri-methodological framing (not "Wyckoff=ATS + ICT complementary")
- **PAIN_POINTS_BACKLOG.md**: each pain point should be re-tagged with which methodology provides its primary remediation
- **CLAUDECODE_BRIEF_v2.md**: standing rule set confirmed at 1-15; G-METHODOLOGY-FIRST-WORKFLOW (Rule 15) reinforced by this decision
- **Fit-gap doc (in progress)**: structured per module with 7 subsections per module — methodology primary / methodology secondary / detection mechanics / fit / gap / missing-from-literature / prioritization
- **ats_trend_line.py module**: stays — it is the ATS implementation of R1 PRIMARY per ADR-001. This decision does not override ADR-001; it clarifies that ATS is primary for directional bias (where R1 lives) while Wyckoff is primary for sub-event detection (separate concern).

## Rationale

- Operational: the missing Wyckoff sub-events (Spring, Upthrust, SOS, SOW) are direct remediation for PP2 (missing reversal points). Not recognizing their methodological uniqueness blocks their implementation.
- Architectural: explicit methodology attribution per module makes the target state diagnosable — when a module fails, it fails against a named framework, not against a vague "methodology".
- Scientific: each framework has strong published documentation; treating them as distinct allows per-framework validation against their own literature, which is more rigorous than a merged pseudo-framework.
- Practical: when ML replaces rule-based implementations, the ML still needs training labels. Per-methodology labels (phase_wyckoff, sub_event_wyckoff, phase_ict_amd) are richer training signal than a single phase_ats label.

## Related artifacts

- `FOUNDATION_v2.md` (needs revision per this decision)
- `FITGAP_MODULE_MIGRATION_2026-04-24.md` (to be produced — will apply this decision)
- `FluxQuantumAI_NextGen_Arquitetura_Detalhada.docx` (NextGen spec — 16 modules)
- `ATS Core Components — Síntese internalizada.txt`
- `Introduction To ATS Specific Price Patterns.txt`
- `METODO WYCKOFF DE TRADING.compressed.pdf` (Villahermosa book 1)
- `533397707-Ruben-Villahermosa-Wyckoff-2-0-Structures-Volume-Profile-and-Order-Flow.pdf` (Villahermosa book 2)
- `652432368-Ict-Institutional-Smc-Trading.pdf` (Woods Advanced ICT/SMC)
- `685506003-Volume-Profile-Market-Profile-Order-Flow-...-Forthmann.pdf`
- `468096490predictionofhiddenliquiditypdf_1.pdf` (Christensen)

## How this decision interacts with open items

- D1 (2026-02-13 BEAR mismatch): unaffected — investigation is about data pipeline, not methodology
- D4 (Part B v2 Trend Classifier scoping): **directly affected** — v2 spec must now expose multi-taxonomy contract per Structure & Phase Agent
- D5 watchdog follow-up: unaffected — infrastructure
- Fit-gap doc: **directly affected** — structure and content bound by this decision

---

**End of DEC-2026-04-24-001. Append to DECISIONS_LOG.md as new entry; do not modify DEC-2026-04-22-001 (immutability rule — its status changes to "Superseded by DEC-2026-04-24-001").**

---

## DEC-2026-04-24-002 — Data Integrity Boundary

**Date decided:** 2026-04-24
**Status:** ✅ Active
**Follows:** D1 brief-back (2026-02-13 BEAR mismatch investigation)
**Approved by:** Barbara Feijen

## Context

On 2026-04-24, D1 investigation revealed that the -35pt mismatch between the ATS Trend Line dot of 2026-02-13 and the Quantower visual reference was NOT caused by the `live/ats_trend_line.py` module, nor by capture-layer gaps, nor by hypothesis H1-H5 as originally framed.

Forensic evidence:
- `trades_2026-02-13.csv.gz` max price = **5069.10** at 19:10:09 UTC — matches Quantower visual reference to the second decimal.
- `gc_ohlcv_l2_joined.parquet` and `gc_m30_boxes.parquet` contain **price-shifted bars for 2026-02-13 14:33–22:00 UTC that plateau around 5030** while volumes track raw to ±3 contracts.
- The module correctly emitted the BEAR dot at 5037.975 given its (corrupted) inputs.
- Capture layer is complete (22h coverage on Feb 13 per `data_gaps_report.md`).

The failure is **downstream of capture, upstream of the module** — in the build pipeline that produces derived parquets.

This contradicts the load-bearing premise established earlier on 2026-04-24 ("Quantower/DXFeed never fails; all gaps are receiver-side kills"). The premise holds at the **tape level** but not at the **derived parquet level**. The ecosystem has a second source of truth corruption that was not previously audited.

Prior to D1, all backtests, threshold calibrations, Part A validation, and ML training data assumed derived parquets were faithful representations of tape. This assumption is now suspended pending DATA-001 reconciliation.

## Decision

Data integrity is bounded by a two-layer authority model:

**Layer 1 — Tape (authoritative unconditionally):**
- Quantower/DXFeed raw trades CSVs under `C:\data\level2\_gc_xcec\`
- Databento MBP-10 backfill under `C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5\`
- These files are source-of-truth. Any disagreement between any downstream artefact and these files is resolved in favour of these files.

**Layer 2 — Derived parquets (conditionally authoritative):**
- `gc_ohlcv_l2_joined.parquet` (1-min bars)
- `gc_m30_boxes.parquet` (M30 bars)
- `gc_d1_boxes.parquet` (D1 bars)
- `gc_ats_features_v4.parquet` (feature store)
- All other downstream parquets derived from tape

These inherit authority from tape **only for dates explicitly validated by per-day reconciliation**. Dates not yet reconciled are treated as "unverified" — not necessarily corrupt, but not trustworthy for load-bearing decisions.

## Mandatory declarations from consumers

Every consumer of data in the FluxQuantumAI codebase must explicitly declare which layer it trusts. This applies to:

- **Backtests** — declare in the script header which layer is consumed and whether reconciliation has been confirmed for the test window
- **Threshold calibration scripts** (Rule 3 G-PURDUE-CALIBRATION) — if calibrating off derived parquets, flag that calibration may require re-derivation post-DATA-001
- **v2 Trend Classifier spec (D4)** — declare input layer per phase; acceptable to scope D4-A (spec) against tape-derived series, D4-B (implementation) against reconciled parquets
- **ML training pipelines** — declare whether Iceberg / Anomalies / future classifiers trained on tape or on derived parquets; if on derived, flag for retraining post-DATA-001 if blast radius warrants
- **live/ modules** — current `live/ats_trend_line.py` consumes derived parquets; this is acceptable for production runtime (which runs against current dates, where corruption has not yet been bounded) but validation claims must cite which layer was used

## Implications for open work

| Artefact | Status under this decision |
|---|---|
| Part A 15/15 unit tests | Unchanged — unit tests don't depend on tape vs derived distinction |
| Part A 3/5 disputed dots validated at <1.3pt | **Downgraded to "validated against Quantower visual"** — not against tape. Subtle <1.3pt divergences from tape may have been masked. Re-validation recommended post-DATA-001. |
| ats_trend_line.py module | **Unchanged** — module is correct given its inputs; remediation is upstream |
| Backtests run to date | **Suspended as authoritative** for any date not yet reconciled; may be recomputed post-DATA-001 |
| DEC-2026-04-24-001 (tri-methodological foundation) | **Unaffected** — methodology decisions are data-layer agnostic |
| DEC-2026-04-22-002 (PP3 MT5 deferred) | **Unaffected** |
| D4 Part B scoping (v2 Trend Classifier) | **Split.** D4-A (spec, chat-side) proceeds against DEC-2026-04-24-001 contract. D4-B (implementation) blocked pending DATA-001 reconnaissance. D4-C (backtest) blocked pending DATA-001 fix for test window. |
| Fit-gap doc | **Unaffected** — chat-side, no data dependency |
| D5 watchdog amendment follow-up | **Unaffected** — infrastructure |

## Relation to DEC-2026-04-24-001

Tri-methodological foundation (DEC-2026-04-24-001) is unaffected. Methodology coverage decisions do not depend on the build pipeline. However, any threshold or spec within a module that is calibrated from data (per Rule 3) must declare layer source once DATA-001 reconnaissance is complete.

## Rationale

- **Diagnostic clarity:** a named boundary between tape and derived parquets makes it possible to localize future corruption claims quickly. Without the boundary, every anomaly looks like a capture issue or a module bug.
- **Trust ladder:** backtests and ML training must be rebuildable on reconciled data. Declaring the layer in consumer code makes the rebuild tractable rather than requiring a forensic investigation per-consumer.
- **Non-panic framing:** D1 found one date with clear corruption. Blast radius is unknown. Rather than treating all data as suspect (panic) or dismissing the finding as isolated (denial), a bounded model allows staged response: reconnaissance first, fix second, re-validation third.
- **Sprint continuity:** without this boundary, the correct response would be to halt all data-dependent work until everything is re-reconciled. That is too costly. Bounded trust enables parallel progress on chat-side work (fit-gap, D4-A spec, perception brainstorm) while DATA-001 runs.

## Dependencies

- **DATA-001** (parquet-vs-trades reconciliation) — reconnaissance phase is the immediate execution of this decision
- **DATA-002** (hypothetical future track) — if DATA-001 findings warrant pipeline fix, this is where that work lives

## Related artifacts

- `_audit/pp_sprint/D1_2026-02-13_BEAR_mismatch_report.md` (D1 brief-back — evidence base)
- `_audit/pp_sprint/DATA-001_raw_vs_parquet_reconciliation.md` (to be produced by ClaudeCode)
- `DECISIONS_LOG.md` (append as new entry; do not modify prior entries)
- `data_gaps_report.md` (now explicitly scoped to physical-layer gaps only; does not cover build-layer corruption)

---

**End of DEC-2026-04-24-002. Append to DECISIONS_LOG.md as new entry.**

---

## DEC-2026-04-24-003 — Purdue Calibration Protocol + Rule Ensemble Weighting

**STATUS UPDATE 2026-04-25:** SUPERSEDED by DEC-2026-04-24-003 v2 (12-step protocol below). Preserved here per immutability rule for historical lineage.

**Date decided:** 2026-04-24
**Status:** ✅ Active
**Clarifies:** Rule 3 G-PURDUE-CALIBRATION (standing rule, HANDOFF 2026-04-24)
**Approved by:** Barbara Feijen

## Context

Rule 3 G-PURDUE-CALIBRATION establishes "all thresholds data-driven; no magic numbers". The rule has been consistently applied across the sprint but without explicit operational protocol. On 2026-04-24, Purdue Data Science and Machine Learning course material (Simplilearn Data Science + ML curriculum referenced by the project) was reviewed to formalize the calibration workflow and introduce an adjacent rule for combining rules within a single agent.

Two gaps were identified:

1. **Calibration depth** — "percentile of observed distribution" is a good starting point but doesn't address confidence intervals, hypothesis testing, outlier sensitivity, or re-calibration triggers. Without these, calibration is data-driven but not statistically defensible.

2. **Rule ensemble** — agents in the NextGen architecture (DEC-2026-04-24-001) may have multiple methodologies producing concurrent outputs (Wyckoff view vs ATS view vs ICT view). The rule set had no canonical mechanism for combining them. Without one, the agent either arbitrarily picks one methodology (loses information) or concatenates all outputs (punts the decision to downstream).

Purdue material provides explicit methodology for both gaps:

- **Lesson 06 Statistics Fundamentals, Lesson 08 Advanced Statistics, Lesson 09 Data Wrangling, Lesson 10 Feature Engineering** (zip1 Data Science curriculum) — cover hypothesis testing, confidence intervals, margin of error, feature transformations, outlier handling, binning.
- **Lesson 05 Ensemble Learning** (zip2 ML curriculum) — covers hard voting, soft/weighted voting, weighted averaging, which are directly applicable as rule-combination mechanisms even outside ML contexts.

## Decision

### Part A — Purdue Calibration Protocol (clarifies Rule 3)

Every threshold in the codebase that affects trading logic must be calibrated following the 8-step Purdue protocol below. If a step is skipped, the reason is documented explicitly in the calibration artefact.

**Step 1 — Distribution observation.** Observe the feature's empirical distribution over a confirmed-healthy historical window. "Confirmed-healthy" defined per DEC-2026-04-24-002 (tape-authoritative layer, not derived parquets until DATA-001 reconciliation clears the window).

**Step 2 — Feature engineering for distribution shape.** If the feature has known skewness (e.g. ATR, volume, spread are typically log-normal or heavy-tailed), apply appropriate transformation (log, Box-Cox, or robust alternative) before threshold derivation. Calibrating a threshold on a raw log-normal feature produces unstable thresholds; transforming first is not cosmetic.

**Step 3 — Threshold proposal.** Threshold is percentile-based (p90, p95, p99, p99.9 depending on sensitivity needs) or IQR-based (Q3 + 1.5*IQR for outlier detection). Magic numbers forbidden.

**Step 4 — Confidence interval.** Compute a confidence interval for the threshold itself. Bootstrap-based CI preferred for non-parametric robustness; parametric CI acceptable if distribution family is established. Narrow CI = stable threshold; wide CI = threshold sensitive to sample, flag for caution.

**Step 5 — Hypothesis test.** Formal test that the threshold separates the two populations it's supposed to separate (e.g. "healthy volume" vs "climax volume"). Null hypothesis = no separation; alternative = separation. p-value documented. If the rule fails the hypothesis test, the rule itself is suspect — not just the threshold.

**Step 6 — Type I / Type II error trade-off.** Every threshold has a false-positive vs false-negative trade-off. Document explicitly which is more costly in the application context. For reversal-detection rules (Spring, Upthrust), false negatives likely more costly than false positives (missing a reversal = missing trade; false spring = one bad trade). For veto rules (overextension), opposite applies.

**Step 7 — Outlier handling.** If the distribution has heavy tails, the threshold may be pulled by flash crashes or data glitches. Use robust statistics (median, MAD, trimmed means) where appropriate. Explicit choice documented.

**Step 8 — Re-calibration policy.** Define triggers for re-calibration: data drift detected (distribution shift), N months elapsed, regime shift detected, or post-incident. Ties calibration to monitoring (Sprint 10.1 in Perception backlog).

**Deliverable per calibration:** a `calibration_<feature>_<rule>.md` file under `_audit/calibrations/` documenting all 8 steps with numeric evidence. This file is the artefact that makes the threshold defensible under Rule 3 and DEC-2026-04-24-001 audit trail requirements.

### Part B — Rule ensemble weighting (Rule 3.b, new sub-rule)

When multiple rules within a single agent produce concurrent output for the same field (e.g. Structure & Phase Agent has Wyckoff view, ATS view, ICT view of current phase), the final output is produced by an ensemble voting mechanism. The mechanism is declared explicitly per agent in its spec.

**Mechanism options:**

- **Hard voting** — each rule outputs a discrete class; majority wins. Ties resolved by explicit deterministic rule (e.g. prefer methodology with highest prior accuracy on the specific output type).
- **Soft voting (weighted)** — each rule outputs a probability or confidence; weighted average determines final class. Weights are calibrated (see below).
- **Weighted averaging** — for continuous outputs, not class labels.

**Weight calibration (data-driven per Rule 3):**

- Weights per methodology per agent per output type are derived from historical accuracy of that methodology on that output.
- Example: Structure & Phase Agent, phase_wyckoff output — Wyckoff rule accuracy = 0.82, ATS rule accuracy = 0.67, ICT rule accuracy = 0.58; weights = {Wyckoff: 0.45, ATS: 0.32, ICT: 0.23} (normalized from accuracy).
- Weight computation window = confirmed-healthy historical window (same requirement as Part A Step 1).
- Weights re-calibrated per Part A Step 8 re-calibration policy.

**Output format (per agent spec):**

Every agent that uses ensemble voting publishes both the final value AND the vote breakdown, for auditability:

```json
{
  "phase_wyckoff": "C",
  "phase_wyckoff_confidence": 0.71,
  "_vote_breakdown": {
    "wyckoff_rule": {"vote": "C", "confidence": 0.85, "weight": 0.45},
    "ats_rule": {"vote": "B", "confidence": 0.60, "weight": 0.32},
    "ict_rule": {"vote": "C", "confidence": 0.50, "weight": 0.23}
  }
}
```

The `_vote_breakdown` field is optional for consumers but mandatory in internal logging for debugging and calibration audits.

## Implications for open work

| Artefact | Effect |
|---|---|
| CLAUDECODE_BRIEF_v2 standing rules | Rule 3 reference now points to this DEC for the operational protocol (no edit to original file — immutability) |
| Fit-gap Perception doc (upcoming) | Every calibration effort estimate must account for the 8-step Purdue protocol time |
| Per-agent specs (upcoming) | Agent spec template adds "Ensemble mechanism" section when multiple rules compete |
| Sprint 2.2 Deterministic Label Engine | Each deterministic label with numeric threshold requires a calibration artefact per Part A |
| Sprint 3.2 Feature Store | Features with skewed distributions need transformation pipeline per Part A Step 2 |
| Sprint 4.RB Rule implementation | Each rule declares ensemble position (standalone / voter / weighted) |
| Sprint 10.1 Monitoring | Drift detection against the calibration distributions is the re-calibration trigger |

## Relation to prior decisions

- **DEC-2026-04-24-001 (tri-methodological foundation)** — Part B of this decision is the operational mechanism for combining multiple methodologies within a module that DEC-2026-04-24-001 requires.
- **DEC-2026-04-24-002 (data integrity boundary)** — Part A Step 1 "confirmed-healthy historical window" inherits the tape-vs-parquet distinction from this decision. Calibration against derived parquets requires DATA-001 reconciliation of the window.
- **DEC-2026-04-22-006 (Documentation-First + Activation Checklist)** — calibration artefacts are part of the documentation requirement before any threshold is considered "active".

## Rationale

- **Defensibility:** Purdue protocol elevates thresholds from "reasonable guess" to "statistically justified". In a project where every rule will eventually be compared against ML replacements (shadow mode), having a statistical audit trail per threshold makes the comparison meaningful, not subjective.
- **Ensemble consistency:** the tri-methodological foundation creates natural multiplicity within agents. Without a declared ensemble mechanism, each agent would invent its own — resulting in system-wide inconsistency. Declaring the mechanism as a cross-cutting rule keeps integration predictable.
- **ML migration path:** when rule-based agents are replaced by ML, the calibration artefacts (Part A) become training-label guidance, and the ensemble weights (Part B) become prior probabilities for ML model combination. Nothing built under this decision is wasted when ML arrives.
- **Non-overhead:** for simple agents with single-rule output (News Agent, Session Agent), the ensemble part is a no-op. Overhead scales with complexity, which is correct.

## Related artifacts

- `DECISIONS_LOG.md` (append this as new entry; do not modify prior entries)
- `CLAUDECODE_BRIEF_v2.md` (Rule 3 references this DEC; original file unmodified per immutability rule)
- Purdue course material (zip1 Data Science curriculum Lessons 06, 08, 09, 10; zip2 ML curriculum Lesson 05) — available to ClaudeCode via GitHub, to chat-side Claude via upload
- `_audit/calibrations/` directory (to be created by ClaudeCode when first threshold is calibrated)

---

**End of DEC-2026-04-24-003. Append to DECISIONS_LOG.md as new entry. Immutability preserved.**

---

## DEC-2026-04-24-003 v2 — Purdue Calibration Protocol (12 steps) + Rule Ensemble Weighting

**Decision ID:** DEC-2026-04-24-003 v2
**Date decided:** 2026-04-24 (v1) — **Expanded 2026-04-25 (v2)**
**Status:** ✅ Active. **SUPERSEDES** DEC-2026-04-24-003 v1.
**Approved by:** Barbara Feijen
**Author:** ML-DS Engineer (chat-side)
**v1 status change:** Marked "Superseded by DEC-2026-04-24-003 v2 (2026-04-25)". v1 NOT deleted (immutability principle from DECISIONS_LOG governance).

---

## 1. Why v2

v1 defined an 8-step Purdue calibration protocol covering: distribution observation, feature transforms, threshold derivation, confidence interval, hypothesis test, Type I/II trade-off, outlier handling, re-calibration policy.

ClaudeCode's ML-PROVENANCE-002 v1 audit (closed 2026-04-25) revealed two gaps:

1. **Ensemble learning** is not in the original 8 steps but is the entire focus of Purdue ML Lesson 05. None of the 4 audited models (AnomalyForge V2/V3/V4 + Iceberg V2) used ensemble techniques — single-model, single-seed, single-fold. This is a methodological gap of the same severity as the original 8 gaps.

2. **Cross-validation rigor, reproducibility, and leakage prevention** appear scattered through Purdue ML Lessons 03/04/05 but were not codified as standalone steps in v1. ClaudeCode found leakage in V3 and V4 (thresholds derived from data including validation/test splits) — this is a Step 3 violation under v1, but more accurately a **leakage prevention** violation requiring its own step.

Barbara approved (2026-04-25) expansion from 8 to 12 steps.

After re-reading complete Purdue curriculum (ML Lessons 03/04/05/06 + DS Lessons 06/07/08/09/10), I confirm 12 is the right number. Pipeline Pattern, Hyperparameter Tuning, and Performance Metrics integrate as sub-aspects of existing steps rather than standalone additions.

---

## 2. Decision

The Purdue Calibration Protocol consists of **12 steps**, applied to:

- **ML training** (full 12 steps mandatory)
- **Rule-based threshold calibration** (12 steps with mappings noted in §6)

Each calibration produces an artifact in `_audit/calibrations/calibration_<feature>_<rule>_v<n>.md` documenting all 12 steps. Missing artifact = rule/model fails review.

---

## 3. The 12 steps (canonical)

### Step 1 — Distribution observation in confirmed-healthy window

**What:** Before deriving any threshold or training any model, observe the distribution of the target feature on a window of data confirmed to be healthy (free from data corruption per DEC-2026-04-24-002).

**How:** Compute mean, median, std, min, max, p25, p50, p75, p95, p99, skewness, kurtosis. Visualize histogram. Compare against the population the model/rule will operate on in production.

**Purdue ref:** DS L06 §2-§5 (Measures of Central Tendency, Dispersion, Shape — Skewness, Kurtosis); DS L08 §1 (Hypothesis Testing requires understanding distribution first).

**Common failure:** training on full historical data without distinguishing healthy windows from corrupt periods. ClaudeCode found this in V2/V4 (Score: PARTIAL) and V3/Iceberg V2 (Score: ABSENT).

### Step 2 — Feature engineering for distribution shape

**What:** When |skewness| > 3 or kurtosis > 10, transform the feature before computing any threshold or feeding to a model. Standard transformations: log (for positive-only skewed), sqrt (gentler), Box-Cox (general family).

**How:** Apply transform → re-check skewness/kurtosis on transformed data → only use transformed feature in downstream calibration / training.

**Purdue ref:** DS L10 §3 (Transforming Variables — Log, Square Root, Box-Cox EXPLICIT); DS L06 §5 (Skewness/Kurtosis as motivators).

**Common failure:** using StandardScaler (mean/std normalization) on heavily skewed data. StandardScaler assumes approximately normal distribution; applying it to skewed data destroys the signal. ClaudeCode found this in ALL 4 audited models (V2/V3/V4/Iceberg V2 — Score: ABSENT). V4 specifically had 45/48 features with |skew| > 3 and 46/48 with kurt > 10, all StandardScaler-treated without prior log/Box-Cox.

### Step 3 — Threshold proposal (percentile or IQR-based, no magic numbers)

**What:** Derive thresholds from the (transformed) distribution. Use percentile cuts (p95, p99) or IQR-based (Q3 + 1.5·IQR for upper tail).

**How:** Document the method, cite the percentile chosen, justify why this percentile (e.g. "p95 chosen to capture top 5% as anomalous, matching prior false-positive budget").

**Purdue ref:** DS L09 §Handling Outliers (IQR-based); DS L06 §4 (Dispersion measures).

**Common failure:** using arbitrary numeric thresholds ("> 3.5") without derivation. Or — what ClaudeCode found in V3/V4 — deriving thresholds from data that includes validation/test (this is leakage, see Step 12). All 4 audited models scored PRESENT here, but V3/V4 had leakage variant.

### Step 4 — Confidence interval on threshold

**What:** Threshold is a sample estimate. Bootstrap CI or parametric CI must accompany it.

**How:** Resample (bootstrap N=1000) → compute threshold per resample → report 95% CI. If CI width > X% of threshold value, threshold is unstable; collect more data before deploying.

**Purdue ref:** DS L08 §3-§5 (Confidence Interval, Margin of Error, Confidence Levels EXPLICIT).

**Common failure:** point-estimate thresholds with no uncertainty quantification. ClaudeCode found this universal across all 4 audited models (Score: ABSENT all 4).

### Step 5 — Hypothesis test for population separation

**What:** Formally test that "anomalous" population (above threshold) is statistically different from "normal" population (below threshold). For ML classifiers: that classifier output discriminates positive vs negative class significantly.

**How:** T-test (continuous), Chi-square (categorical), Mann-Whitney U (non-parametric). Report test statistic, p-value, effect size (Cohen's d). Reject null only if p < 0.05 AND effect size meaningful (typically |d| > 0.5).

**Purdue ref:** DS L08 §1-§3 (Null/Alternative Hypothesis, T-test, Z-test, P-value, Decision-Making EXPLICIT). DS L08 §6 (Chi-Square). DS L08 §7 (ANOVA).

**Common failure:** declaring a feature/model "good" because intuitively the anomaly cases look different — without formal test. ClaudeCode found this universal (Score: ABSENT all 4).

### Step 6 — Type I / Type II trade-off documented

**What:** Every threshold/classifier implies a False Positive (Type I) vs False Negative (Type II) trade-off. Document the cost ratio explicitly and choose threshold to optimize accordingly.

**How:** Compute confusion matrix on calibration data. For each candidate threshold, compute precision/recall/F1. Choose based on stated FP-cost vs FN-cost ratio. Document the decision.

**Purdue ref:** DS L08 §1.3 (Type I and Type II Errors EXPLICIT); ML L04 §Performance Metrics (Confusion Matrix, precision/recall/F1).

**Common failure:** picking threshold that maximizes accuracy without considering asymmetric error costs. In trading: missing a real anomaly (FN) is much more expensive than a false alert (FP) — but most calibrations don't reflect this.

**Sub-aspect — Performance Metrics:** F1-score, precision, recall, AUC-ROC are part of this step's analysis, not separate steps.

### Step 7 — Outlier handling explicit

**What:** Decide upfront how training data outliers are handled: remove, cap (Winsorize), keep, or model separately.

**How:** Document method. If removing/capping: state cutoff (e.g. "values > p99.9 Winsorized to p99.9"). If keeping: justify why model architecture is robust to them (e.g. quantile regression, Huber loss).

**Purdue ref:** DS L09 §Handling Outliers EXPLICIT; ML L06 §6.5.1 (Isolation Forest as outlier detection); DS L06 §5.2 (Kurtosis as outlier indicator).

**Common failure:** ignoring outliers and using non-robust loss (MSE on heavy-tailed data). ClaudeCode found this PARTIAL in V2 (gradient clipping) and V3/Iceberg V2 (some), but V4 fully ABSENT despite having most extreme distributions.

### Step 8 — Re-calibration policy defined

**What:** No threshold/model is forever. Define triggers for re-calibration: time-based (every N months), drift-based (KL divergence > T), event-based (after market regime shift).

**How:** Document trigger formula. Set up monitoring to detect trigger condition. Define ownership: who runs re-calibration when triggered, what artifact gets produced.

**Purdue ref:** Implicit through curriculum — drift requires re-calibration; not a single explicit lesson.

**Common failure:** "set and forget". ClaudeCode found this universal (Score: ABSENT all 4) — no model or rule had documented re-calibration policy.

---

### Step 9 — Ensemble (NEW v2)

**What:** Single model = single point of failure. Whenever practical, combine multiple base models (or rules) via voting, averaging, bagging, boosting, or stacking. For rule-based: voting between independently-derived rules (Wyckoff vote, ATS vote, ICT vote → consensus per Rule 3.b — see §5).

**How — for ML:**
- **Voting** (classification): hard voting (majority class) or soft voting (averaged probabilities).
- **Averaging** (regression): mean prediction across base models.
- **Weighted averaging**: weights per model performance on validation.
- **Bagging**: train N models on bootstrap samples, average. Includes OOB error as built-in validation.
- **Boosting**: sequential models correcting predecessors (AdaBoost, GBM, XGBoost, CatBoost).
- **Stacking**: base models feed meta-model.
- **Snapshot ensemble** (cheap variant): average weights of best_epoch ± k checkpoints during training.

**How — for rule-based:** Per Rule 3.b (preserved from v1): when multiple rules from different methodologies (Wyckoff, ATS, ICT) compete in same agent, combine via voting. Weights data-driven from historical accuracy per methodology.

**Purdue ref:** ML L05 ENTIRE LESSON. Categories: Sequential vs Parallel ensemble. Simple techniques: Voting, Averaging, Weighted Averaging. Advanced: Bagging+OOB, Boosting (AdaBoost, GBM, XGBoost, CatBoost), Stacking.

**Common failure:** single seed, single fold, single model. ClaudeCode found this universal (4/4 audited models): V2 LSTM, V3 Transformer-AE, V4 Conv1D-AE, Iceberg V2 chain-pipeline — all single-instance, no ensemble.

**Cheap rescue path:** snapshot ensemble (average weights of best_epoch ± 5) requires NO retraining and immediately reduces variance. Apply to V4 if Cenário A chosen in ML-PROVENANCE-002 v2.

---

### Step 10 — Cross-validation rigor (NEW v2)

**What:** A single train/val/test split is fragile. Use k-fold CV (or for time series: walk-forward CV with proper purging) to estimate performance robustly. For hyperparameter selection: nested CV or careful split discipline.

**How:**
- **K-Fold (k=5 or 10)**: standard for non-temporal data.
- **Stratified K-Fold**: when classes imbalanced (preserve class proportions per fold).
- **Walk-forward CV**: for time series — folds are temporally ordered; train on past, validate on future. **NEVER use random k-fold on time series financial data — it leaks future into past.**
- **Purged CV**: for time series with overlapping samples — drop training samples within "embargo window" of validation samples to prevent leakage from autocorrelation.
- **LOOCV**: when N small.
- **Hyperparameter tuning** (sub-aspect): GridSearchCV or RandomSearchCV — both wrap CV internally. Average results across folds, not single-fold pick.

**Purdue ref:** ML L03 §Cross-Validation (K-Fold, Stratified K-Fold, LOOCV EXPLICIT; "Stratified K-Fold typically used for classification; standard K-Fold for regression"); ML L03 §Hyperparameter Tuning (GridSearchCV, RandomSearchCV, both with CV).

**Common failure (general)**: single train/val/test split treated as ground truth.

**Common failure (financial time series)**: random k-fold applied to time-ordered data — catastrophic leakage. **MANDATORY for FluxQuantumAI: walk-forward CV only for time series ML.**

ClaudeCode found: V4 = 1-fold walk-forward (better than V2/V3 which had train+val only, no fold averaging); Iceberg V2 = train+val.

**Sub-aspect — Hyperparameter Tuning:** integrated here, not standalone. GridSearchCV/RandomSearchCV always wrapped in walk-forward CV for FluxQuantumAI.

---

### Step 11 — Reproducibility (NEW v2)

**What:** Same input + same code + same seed = same output. Without reproducibility, debugging is impossible and audit trails are meaningless.

**How:**
- **Seed fixing**: set `random_state=42` (or any fixed value) on every random operation: train_test_split, KFold shuffle, model initializer, data sampling, augmentation.
- **Multi-seed evaluation** (advanced): run experiment with N seeds (e.g. 5), report mean ± std of metrics. Single-seed results are anecdotes; multi-seed results are evidence.
- **Deterministic ops**: PyTorch `torch.manual_seed()` + `torch.backends.cudnn.deterministic=True`. NumPy `np.random.seed()`. Python `random.seed()`.
- **Version pinning**: requirements.txt with exact versions (`numpy==1.24.3` not `numpy>=1.24`).
- **Data versioning**: hash the training data (e.g. MD5 of parquet), record hash in calibration artifact. If data changes, hash changes, re-calibration triggered.

**Purdue ref:** ML L03 EXPLICIT (`random_state=42`, "Ensures reproducible shuffling"); ML L05 (stacking allows custom CV strategies including reproducibility seeds). Cross-cutting principle.

**Common failure (cheap):** no seed → results vary 5% across runs, can't tell if change is real.

**Common failure (expensive):** single-seed reported as if it were robust. ClaudeCode found all 4 models used `torch.manual_seed(42)` once, no multi-seed averaging.

---

### Step 12 — Leakage prevention (NEW v2)

**What:** Information from outside the training set must NEVER influence the model. This includes future data, validation data, test data, target-derived features, and any external information unavailable at prediction time.

**How:**
- **Train/Val/Test isolation**: split FIRST, then compute statistics on training only. StandardScaler `fit_transform(X_train)` then `transform(X_val)` and `transform(X_test)` — NEVER fit on combined data.
- **Pipeline pattern (MANDATORY artifact)**: wrap feature engineering + scaling + model in sklearn `Pipeline`. The pipeline auto-handles split-aware transformations. Without Pipeline, leakage creeps in silently.
- **Look-ahead bias prevention** (time series): features at time t may only use data up to t-1 (or t depending on bar close convention). Rolling window features must use trailing window, never centered.
- **Target leakage prevention**: don't include features that are direct functions of the target (e.g. "average of past 1 day's price" when target is "next 1 day's price").
- **Threshold leakage prevention**: thresholds derived from data must be derived ONLY from training data, never validation or test (the gap ClaudeCode found in V3/V4).

**Purdue ref:** ML L03 EXPLICIT — "To avoid data leakage, the standardization of numerical features should always be performed after data splitting and only from training data" + "Data leakage occurs when information from outside the training dataset is used to create the model. This can happen if data that would not be available at the time of prediction is included in the training process. Data leakage can lead to optimistic performance estimates and models that fail to generalize well to new, unseen data" + Pipeline pattern as solution. ML L05 (stacking out-of-fold predictions to prevent overfitting/leakage).

**Common failure (cheap):** scaling fit on full data → optimistic val/test metrics.

**Common failure (expensive in finance):** rolling windows centered instead of trailing → uses future data → backtest looks great → live trading loses money.

ClaudeCode found V3/V4 had threshold leakage (Step 3 leaky variant); none used Pipeline pattern explicitly.

**Sub-aspect — Pipeline Pattern:** mandatory artifact under this step. Every ML training script for FluxQuantumAI wraps preprocessing + model in Pipeline.

---

## 4. Calibration artifact template (mandatory)

Every threshold/model produces `_audit/calibrations/calibration_<feature>_<rule>_v<n>.md`:

```
### Calibration: <feature> for <rule/model>

## Step 1 — Distribution observation
Window: <YYYY-MM-DD to YYYY-MM-DD>
Sample size: N
Stats: mean, median, std, p25, p50, p75, p95, p99, skewness, kurtosis
Visualization: link to histogram

## Step 2 — Feature engineering
Skewness: <value>; Kurtosis: <value>
Transformation applied: <none|log|sqrt|Box-Cox(lambda=X)>
Post-transform skewness: <value>

## Step 3 — Threshold proposal
Method: <percentile|IQR>
Value: <number>
Justification: <why this percentile / cutoff>

## Step 4 — Confidence interval
Method: bootstrap N=1000
95% CI: [lower, upper]
CI width / threshold value: X% (stable if <10%)

## Step 5 — Hypothesis test
Test: <T-test|Chi-square|Mann-Whitney|other>
Statistic: <value>
p-value: <value>
Effect size: <Cohen's d or equivalent>
Verdict: <reject/fail to reject H0>

## Step 6 — Type I/II trade-off
FP cost / FN cost ratio: <X:Y> (justified)
Confusion matrix on calibration data: TP/FP/TN/FN
Precision: X; Recall: Y; F1: Z
Threshold chosen optimizes for: <FP-minimization|FN-minimization|F1-balance>

## Step 7 — Outlier handling
Method: <none|remove|Winsorize|robust loss>
Cutoff (if applicable): <value>
Justification: <why>

## Step 8 — Re-calibration policy
Trigger: <time-based|drift-based|event-based>
Formula: <e.g. "every 90 days OR when KL(production_dist, calibration_dist) > 0.1">
Owner: <Barbara | scheduled job>
Last calibration: <date>
Next scheduled: <date or "trigger-based">

## Step 9 — Ensemble (if applicable)
Approach: <single|voting|bagging|boosting|stacking|snapshot>
Justification: <if single, why ensemble not used>

## Step 10 — CV rigor
CV strategy: <k-fold|stratified-k-fold|walk-forward|purged-walk-forward>
k: <value>
Hyperparameter tuning: <none|GridSearchCV|RandomSearchCV>
Per-fold scores: <list>
Mean ± std: <value>

## Step 11 — Reproducibility
Seeds: random_state=<X>, torch=<Y>, numpy=<Z>
Multi-seed: <single-seed|N seeds, mean±std reported>
Data hash: <MD5/SHA256 of input data>
Code commit: <git hash>
Versions: <link to requirements.txt or env file>

## Step 12 — Leakage prevention
Pipeline used: <yes/no — link to script>
Scaler fit scope: <training-only|leaky>
Threshold derivation scope: <training-only|leaky>
Look-ahead check: <pass/fail with evidence>
Target leakage check: <pass/fail with evidence>
```

---

## 5. Application to two contexts

### 5.1 ML training (full 12 steps mandatory)

Every ML training pipeline for FluxQuantumAI must produce calibration artifact covering all 12 steps. Models without this artifact CANNOT be promoted to production (live or shadow).

Existing models requiring retroactive remediation (per ML-PROVENANCE-002 v1 audit findings):
- AnomalyForge V2 LSTM — full 8 steps ABSENT/PARTIAL
- AnomalyForge V3 Transformer-AE — full 8 steps ABSENT/PARTIAL + leakage
- AnomalyForge V4 Conv1D-AE — Steps 2/4/5/8 ABSENT, Step 7 ABSENT despite extreme distributions, leakage in Step 3
- Iceberg V2 — full 8 steps ABSENT/PARTIAL, Frankenstein local artifacts

Action: ML-PROVENANCE-002 v2 (separate task) extends audit to Steps 9-12 + tests V4 inference + decides Cenário A/B/C.

### 5.2 Rule-based threshold calibration (12 steps with mappings)

| Step | ML version | Rule-based version |
|---|---|---|
| 1 — Distribution observation | On training data | On historical confirmed-healthy data for threshold derivation |
| 2 — Feature transforms | Pre-training transformation | Pre-threshold derivation transformation |
| 3 — Threshold proposal | Model output threshold (e.g. anomaly score cutoff) | Rule input threshold (e.g. spread spike factor) |
| 4 — Confidence interval | CI on model threshold | CI on rule threshold (identical mechanic) |
| 5 — Hypothesis test | Anomaly vs normal separation | Event vs non-event separation (signal generates legit trade vs noise) |
| 6 — Type I/II trade-off | FP/FN of classifier | FP/FN of rule (false trigger vs missed event) |
| 7 — Outlier handling | Robust loss / clipping in training | Robust statistics (median, MAD) in calibration |
| 8 — Re-calibration policy | ML drift trigger | Rule drift trigger (identical mechanic) |
| 9 — Ensemble | Bagging/voting between ML models | **Rule ensemble per Rule 3.b** — voting between rules from different methodologies (Wyckoff vs ATS vs ICT). Weights data-driven from historical per-methodology accuracy. |
| 10 — CV rigor | k-fold / walk-forward CV | Walk-forward backtest of rule (NOT standard k-fold — time series) |
| 11 — Reproducibility | Multi-seed averaging | Deterministic rule code + version pinning + data hash |
| 12 — Leakage prevention | Pipeline pattern + train-only scaler fit | Calibration window strictly separated from validation backtest window. Rule logic must not use data unavailable at decision time (no centered rolling, no future-returns features). |

### 5.3 Rule 3.b — Rule Ensemble Weighting (preserved from v1, now anchored under Step 9)

When multiple rules from different methodologies compete in the same agent (ex: Wyckoff phase classifier vs ATS phase classifier vs ICT phase classifier in Structure & Phase Agent):

**Combine via voting:**
- **Hard voting**: each methodology votes for one phase; majority wins.
- **Soft voting**: each methodology emits a probability distribution over phases; sum (weighted) and pick argmax.
- **Weighted voting**: weights derived from per-methodology historical accuracy on labeled validation set (Step 10 walk-forward CV).

**Vote breakdown exposed in agent contract output** per DEC-2026-04-24-001 (tri-methodological foundation):
```
{
  "phase_ats": "EXPANSION",
  "phase_wyckoff": "Phase D",
  "phase_ict_amd": "manipulation",
  "consensus": "EXPANSION_TO_TREND",
  "vote_breakdown": {
    "wyckoff": {"value": "Phase D", "weight": 0.34, "confidence": 0.78},
    "ats": {"value": "EXPANSION", "weight": 0.41, "confidence": 0.85},
    "ict": {"value": "manipulation", "weight": 0.25, "confidence": 0.62}
  }
}
```

Weights re-calibrated per Step 8 trigger (typically quarterly + on regime shift detection).

---

## 6. Implications

### 6.1 For existing ML models (immediate)

- AnomalyForge V2/V3 — fail v2 audit (Cenário C — REDESIGN expected per ML-PROVENANCE-002 v2)
- AnomalyForge V4 — partial pass on Steps 1/3/6/7/10 with violations; Steps 2/4/5/8/9/11/12 require remediation. Decision A vs B vs C pending V4 inference test.
- Iceberg V2 — fail v2 audit (Frankenstein local + 8/12 steps ABSENT/PARTIAL)

### 6.2 For new ML models (ongoing)

Every new ML model must produce calibration artifact covering all 12 steps BEFORE training is approved. No exceptions.

### 6.3 For rule-based thresholds (ongoing)

All rule-based detection thresholds in `live/` and `NextGen_RB/` modules must produce calibration artifact (12 steps with rule-based mappings per §5.2) within Phase 0.7 calibration window (Day 21-22 of Phase 0.7 per HEALTH_MONITORING_INFRASTRUCTURE_SPEC).

Existing rules without artifact: documented as "PLACEHOLDER pending Purdue calibration" — calibration gets explicit task in backlog.

### 6.4 For Rule Ensemble (Structure & Phase Agent)

Rule 3.b voting becomes mandatory in any agent where multiple methodologies provide overlapping phase classification. Single-methodology agents are explicitly allowed (e.g. an agent that ONLY does ATS phase detection) — rule applies only when multiple methodologies are merged.

### 6.5 For Phase 0.7 health monitoring infrastructure

All PLACEHOLDER thresholds in HEALTH_MONITORING_INFRASTRUCTURE_SPEC v1 (heartbeat-stall threshold, recovery max attempts, throttling windows, etc.) follow this 12-step protocol when calibrated in Phase 0.7 sub-task 0.7.10.

---

## 7. Dependencies

- DEC-2026-04-24-001 (Tri-methodological foundation — provides Wyckoff+ATS+ICT methodology context for Rule 3.b ensemble)
- DEC-2026-04-24-002 (Data Integrity Boundary — Step 1 "confirmed-healthy window" requires DATA-001 reconciliation per date)
- HEALTH_MONITORING_INFRASTRUCTURE_SPEC v1 (Phase 0.7 sub-task 0.7.10 implements protocol against monitoring thresholds)

---

## 8. Related artifacts

- DEC-2026-04-24-003 v1 (superseded — historical)
- ML-PROVENANCE-002 v1 report — empirical evidence motivating Steps 9-12
- ML-PROVENANCE-002 v2 instruction (separate file) — re-audit per 12 steps
- Purdue material — `Instructor_Slides_and_Notebooks.zip` (ML curriculum) + `Instructor_Slides_And_Notebook.zip` (DS curriculum) in project files

---

## 9. Self-check (PRAC for this DEC)

1. **Overfit risk** — am I overfitting protocol to AnomalyForge V4 findings? No — 12 steps come from full curriculum re-read, validated against Purdue lessons explicitly cited per step. V4 findings only confirmed gaps already implied by Purdue protocol; they didn't drive step selection.

2. **Confirmation bias** — did I look for evidence v1's 8 steps were sufficient? Yes — considered whether Pipeline Pattern, Hyperparameter Tuning, Performance Metrics deserved standalone steps. Concluded NO (sub-aspects of existing steps). Considered whether Bias-Variance and Regularization deserved steps. Concluded NO (foundational concepts that motivate combinations of existing steps).

3. **Premise fragility** — load-bearing premise: that Purdue ML/DS curriculum is the right reference for FluxQuantumAI ML rigor. This is Barbara's strategic choice. If the reference changes (e.g. switch to Hands-On ML by Géron, or López de Prado's Advances in Financial ML), protocol may need revision. Note that López de Prado specifically would add purged k-fold, fractional differentiation, and meta-labeling — which would expand to ~15 steps.

4. **Missing tests** — protocol has not been applied to existing rule-based thresholds yet. We don't know empirically how many rules pass v2 audit. Phase 0.7 sub-task 0.7.10 is the first practical application.

5. **Sample completeness** — re-read covered ML L03/04/05/06 and DS L06/07/08/09/10. Did NOT re-read ML L01/02 (intro), L07 (recommendation systems — irrelevant) or DS L01/02/03/04/05 (intro/numpy/pandas/visualization — implementation tools, not methodology). If you suspect I missed methodology in skipped lessons, flag and I'll re-read.

---

_DEC-2026-04-24-003 v2 finalized 2026-04-25 by ML-DS Engineer chat-side after re-read of Purdue ML curriculum (Lessons 03/04/05/06) + DS curriculum (Lessons 06/07/08/09/10). Approved for append to DECISIONS_LOG.md._

---

## DEC-2026-04-27-001 — MT5 extinction completo (SISTEMA-SIGNAL-ONLY-INTERIM closure)

**Date decided:** 2026-04-27
**Status:** ✅ Executed (commits `063e787` → Sessão 6 closing commits, `fix/signal-only-sessao-{1..6}` branches; Asana umbrella [1214309245971704](https://app.asana.com/0/1214204918416708/1214309245971704))

**Context**

DEC-2026-04-22-002 (Pain Point 3 — MT5 integration deferred) and
DEC-2026-04-22-005 (Position Monitor decoupling from MT5) had left MT5 as a
known-broken execution surface while the decisor evolved. By 2026-04-27 it was
clear that:

1. The MT5 RoboForex / Hantec accounts were no longer the right execution
   target (RoboForex demo not production-relevant; Hantec MT5 deprecated for
   FluxQuantumAI use).
2. cTrader was the chosen future broker but app approval is pending —
   `CTRADER-INTEGRATION-001` ([1214280895398427](https://app.asana.com/0/1214204918416708/1214280895398427)).
3. Trader-in-the-loop on Telegram + dashboard was the operational mode
   Barbara wanted **today**, not after cTrader integration.
4. STABILIZATION-MT5-DECOUPLING-CONTABO (the prior umbrella) had grown into a
   cross-frame fix initiative (Phase 2.5/2.6/2.7) that was the right
   architectural direction (GC frame canonical) but did not address the
   broker-out problem.

**Decision**

Extinguish MT5 from active code completely. System operates as a pure
**decisor + signal emitter**: every entry/permanência/saída decision is
based on L2/GC data from Quantower; signals are emitted to Telegram +
dashboard + JSONL persistence; no broker integration whatsoever in the
critical path. Future cTrader integration adds an `ExecutionLayer` behind
the existing `SignalEmitter` interface — not in front of it.

Concretely:

- Replace `mt5_executor.MT5Executor` with `live._signal_emitter.SignalEmitter`
  (advisory mode, mirrors public surface, no broker calls).
- Delete `mt5_executor.py`, `mt5_executor_hantec.py`, `live/mt5_history_watcher.py`,
  `test_mt5_ipc.py`, `test_mt5_ipc2.py`.
- Strip every `import MetaTrader5` and `import mt5_executor` from active
  paths (`live/`, `tests/`, `scripts/`, `run_live.py`, `apex_nextgen/`).
- Build a `VirtualPositionStore` (Bloco J KEYSTONE) to replace broker-poll
  position state.
- Lock the invariant via a CI guard (`scripts/ci_zero_mt5_guard.sh` +
  `tests/test_zero_mt5_imports.py`) so no future PR can silently re-introduce
  MT5 in active code.
- Preserve Phase 2.5/2.6/2.7 commits (`3096030`, `abdb3f3`, `31ea516`) in
  history — they were the right architectural direction; their MT5-side
  residue was cleaned in Bloco H+I+M, not by reverting the commits.

Sequenced across 6 sessões, 16 sub-blocos (A-P), ~24-29h total work, no
push to remote (Rule 11 local-only commits).

**Rationale**

- **Operational independence** — the system was already advisory-grade in
  practice (MT5 disconnected since 2026-04-21 ROLLBACK-001 era). Formalising
  signal-only matches the actual operational mode.
- **Architectural clarity** — adding a new broker (cTrader) on top of a
  half-deleted MT5 layer creates exactly the cross-frame bug class that
  Phase 2.5/2.6/2.7 just spent 3 weeks fixing. Cleaner to land on a
  broker-agnostic `SignalEmitter` surface and slot cTrader behind it.
- **Telegram visibility now** — Barbara needs trader-loop visibility today,
  not when cTrader app approval comes through (timeline indeterminate).
- **CI defense** — manual discipline ("don't import MT5") fails over time.
  A grep gate + always-on pytest mirror is the lowest-cost regression
  catcher and pays back the first time someone copy-pastes from a backup
  directory.

**Implications**

- DEC-2026-04-22-002 (PP3 deferred) is now **partially superseded**: the
  MT5 portion is closed (extinguished, not integrated); the cTrader portion
  remains deferred under CTRADER-INTEGRATION-001.
- DEC-2026-04-22-005 (Position Monitor decoupling) is **fully superseded**:
  Bloco J (`live/_virtual_position_state.py`) is the new source of truth
  for position state, not the MT5 polling loop the original DEC anticipated.
- Phase 2.5/2.6/2.7 commits remain canonical history. Tests that asserted
  the dual-frame broker contract are dead-by-design but retained as
  historical record (skipped via `pytestmark` or rewritten against
  `SignalEmitter` in Bloco F).
- Future cTrader integration must wire `CTraderExecutor` behind the
  `SignalEmitter` public surface — the `decision="ADVISORY_SIGNAL"` override
  in `log_trade` becomes conditional on `is_advisory`; synthetic ticket
  counter is replaced by broker-confirmed tickets; VPS remains the
  canonical position-state authority. See `_audit/SIGNAL_ONLY_FINAL_STATE.md§Handoff to CTRADER-INTEGRATION-001`.
- **Standing rules created by this program** (memory slots 23 + 24):
  - `G-OPERATIONAL-INDEPENDENCE-TEST` — before deleting any dependency,
    audit whether the system can operate without it.
  - `G-DELETE-DEPENDENCY-CHECK` — dependency-replacement table mandatory
    before any `rm`, with a verification gate that must pass empty.

**Related artifacts:**
- `_audit/MT5_DEPENDENCY_MAP.md` (foundation, frozen historical 2026-04-27)
- `_audit/SIGNAL_ONLY_FINAL_STATE.md` (closure inventory + handoff)
- `_audit/CI_ZERO_MT5_GUARD.md` (gate documentation + whitelist rationale)
- `_audit/sanity_pre_signal_only_s{1..6}.txt` + `_audit/sanity_post_signal_only_s{5,6}.txt`
- `docs/signals_log_schema.md` (replaces broker-coupled `trades_csv_schema.md` MT5 sections)
- Asana sub-tasks (Blocos A-P): see umbrella for full list

---

## DEC-2026-04-28-001 — Standing Rule 3.b extension: Ensemble methodology mandate during design

**Date decided:** 2026-04-28
**Status:** ✅ Active
**Trigger:** P1.3 M30-BOX-STAGNATION-RULE design phase ([1214327736913830](https://app.asana.com/0/1214204918416708/1214327736913830))
**Origin:** Barbara directive 2026-04-28 ~11:05 UTC (Asana comment authorizing Phase 2.5 ensemble backtest)

**Context**

Rule 3.b ("Rule ensemble weighting") already existed in the user-memories
governance framework as a directive for combining MULTIPLE RULES inside a
single agent (e.g., overextension + reversal + iceberg in the gate scorer).
The P1.3 Phase 2 backtest produced four conceptually distinct options (A
excursion / B time / C hybrid / D decay) where two of them (A K=2 and
C K=2 T=8h) exhibited COMPLEMENTARY characteristics:

- A K=2 — strict Bonferroni family-of-4 winner (p=0.012), catches the
  trigger case at age=7h ✅
- C K=2 T=8h — lowest false-positive rate (11.6%), tightest CI, but
  MISSES the trigger case (T=8h gate) ❌

Defaulting to the single Bonferroni winner (A K=2) was the obvious move,
but Barbara directed exploration of an ENSEMBLE first (tri-state
classification VALID/SUSPECT/EXPIRED) to test whether the two options
could compose into a soft-confidence signal. Phase 2.5 ran 5 validations,
3 of which failed decisively (gradient inverted, SUSPECT erratic across
regimes, P&L lost vs binary baseline), so the empirical answer was: simple
wins. The ensemble exploration was right; the ensemble itself wasn't.

**Decision**

Extend the SCOPE of Rule 3.b (verbatim from Barbara's spec):

> Ensemble não só de RULES dentro de um agente, mas também de OPTIONS
> metodológicas durante design phase.

**Trigger:** when a backtest produces multiple competitive options with
complementary characteristics (e.g., one with higher accuracy, another with
lower FP), evaluate an ENSEMBLE approach (state-decomposition, weighted
scoring, tri-state classification, confidence scoring) BEFORE defaulting to
a single winner.

**Applicability:**
- Multiple gates competing → soft-voting weights, data-driven
- Multiple thresholds with tradeoffs → ensemble with confidence score
- Classification problems with multiple algorithms → stacking / blending
- Direction signal from multiple sources → meta-classifier

**Inapplicability:**
- Optimization is obvious (1 winner clear majority, e.g. +5pp accuracy on
  a single option)
- Real-time hot path with tight latency budget (<10 µs)
- Single-rule binary decision is clear

**Rationale**

The P1.3 case proved both directions: the ensemble exploration was the
methodologically correct first move (informs the recommendation with
data, not assumption), AND the data-driven outcome of "ensemble fails →
simple wins" is a valid result of the rule, not a failure of it. The
extension formalises this into governance so that future multi-option
backtests will not skip the ensemble evaluation when the conditions are
met.

**Implications**

- Future ML-DS / ClaudeCode workflows for multi-option backtests must
  document an explicit ensemble go/no-go decision before single-winner
  selection (per the trigger / inapplicability criteria above).
- The Rule 3.b governance memory file (`feedback_purdue_methodology_selection`
  family, slot user-memories) gets a corresponding ensemble-scope note in
  next memory sync. (Memory governance is Barbara's domain — only she
  ratifies G-* binding rules per `feedback_standing_rules_governance`.)
- Phase 2.5 was the FIRST application of the extended rule and validated
  the methodology even though the empirical ensemble itself failed.

**Dependencies**

- DEC-2026-04-24-003 v2 (Purdue Calibration Protocol 12 steps + Rule
  Ensemble Weighting) — this DEC EXTENDS the scope of the ensemble portion
  to design-time option selection, not just runtime rule combination.

**Related artifacts**

- `_audit/M30_BOX_STAGNATION_BACKTEST.md` — Phase 2 of P1.3 (4 options + control)
- `_audit/M30_BOX_STAGNATION_ENSEMBLE_BRIEFBACK.md` — Phase 2.5, V2/V3/V4 fail
- `_audit/m30_box_stagnation_ensemble.py` — read-only validation script
- Asana 1214327736913830 (P1.3 umbrella, Phase 2.5 GO comment 2026-04-28T09:24Z)
- Memory feedback `feedback_recommendation_confidence_framing.md` (companion)
- Reference: Purdue ML curriculum zip2 Lesson 05 — Ensemble Learning

---

## DEC-2026-04-28-002 — M30 Box Stagnation Rule deployed (A K=2 GLOBAL)

**Date decided:** 2026-04-28
**Status:** ✅ Deployed (commits `ac9b2ff` implementation + `33fe340` tz hotfix + `6c22673` audit docs, branch `fix/xau-mid-population` local-only per Rule 11)
**Trigger:** Operational issue 2026-04-28 morning — Box 5261 stuck at high=4700.9 / low=4693.6 (formed 22:00 UTC) while price drifted to ~4628 over an overnight -50pt move. Bias derivation read "bullish" from the geometric extension of the stale box, blocking SHORT signals during a clear downtrend.

**Context**

Pre-fix `live/m30_updater.py::_detect_boxes` requires CONTRACTION (3+
bars within 1.2×ATR) to start a new box. During strong trends no
contraction forms, so the algorithm forward-fills the last box
indefinitely — the framework "freezes" while price walks away. Phase 1
quantification across 10 months L2 (Jul 2025 → Apr 2026) showed:

- **60.8%** of boxes (416/684) lasted >4h → stagnation is the norm
- **40.9%** of stuck boxes had excursion >3×ATR
- **45%** of all 22,302 decisions in 10mo occurred during a stuck box
- Bias-from-stuck-box accuracy: 51-59% (weak edge, coin-flip-ish)
- ICT/ATS/Wyckoff canon prescribes NO temporal-stagnation expiry rule
  (G-LITERATURE-BEFORE-CODE explicit gap documented)

Phase 2 backtest evaluated 4 options × hyperparameter sweep with WF
purged k=5 + bootstrap CI N=1000 + Bonferroni correction. Phase 2.5
ensemble validation (per DEC-2026-04-28-001) failed 3/5 validations,
recommending single-winner deploy.

**Decision**

Deploy **Option A (excursion-based expiry) with K = 2.0, GLOBAL** application
in `live/m30_updater.py::_detect_boxes`. Concretely:

```python
BOX_EXPIRY_K_ATR = 2.0

# In forward-fill block of _detect_boxes:
if cur_box_id > 0 and not np.isnan(cur_atr_at_creation):
    edge_excursion = max(close - cur_box_high, cur_box_low - close, 0.0)
    threshold = BOX_EXPIRY_K_ATR * cur_atr_at_creation   # fixed at box creation
    if edge_excursion > threshold:
        # Reset box state; current bar shows no active box.
        # Subsequent iterations scan for fresh contraction.
        ...
```

- New parquet column `m30_box_atr_at_creation` (atr14 at the bar a box
  was registered) — fixed for the box's lifetime, threshold does not
  follow moving ATR.
- BOX_EXPIRED telemetry: `_emit_box_expired_telemetry` writes MUTATE rows
  to `decision_log.jsonl` via the canonical P0 OBSERVABILITY-LOG-BLOCKS
  schema (Asana 1214327559721560), `reason_code=BOX_EXPIRED_EXCURSION`.
  Filter: only events newer than previous parquet's last bar (avoids
  log spam from re-rebuilding history every 30 min).
- Capture services (PIDs 6376, 13072, 20396) **never touched**;
  restart was via Task Scheduler (NSSM Session 0 bug worked around).

**Rationale**

- **Strict Bonferroni family-of-4 winner** at α' = 0.0125: A K=2 is the
  ONLY option to pass (p=0.012). Other options either marginal (C K=2 T=4h
  p=0.0152, C K=2 T=8h p=0.0128) or fail.
- **Trigger case alignment** — A K=2 catches Box 5261 at age=10.5h /
  47pt excursion. C K=2 T=8h (alternative path 2) would have missed the
  trigger case at the 04:08 UTC moment because age was only 7h08m.
- **Hypothetical P&L recovery** — +247 pts / month (5:1 R:R), or +177
  pts / month (3:1 conservative). At 1 lot Gold ≈ +$2,470/month.
- **Methodology grounding** — although no canon source prescribes
  temporal expiry, Wyckoff Phase E ("price stays out of range = invalidation")
  and ICT 3-pass-rebalance imply a finite range lifespan; A K=2 is a
  data-driven realisation of that principle.
- **Simple wins** — Phase 2.5 ensemble proved tri-state SUSPECT state was
  coin-flip random and lost ~$1,000/year vs binary baseline. Single-winner
  deploy is methodologically correct.

**Implications**

- DEC-2026-04-28-001 (Rule 3.b ensemble extension) — first empirical
  application of the rule that produced "ensemble considered, simple wins".
- `derive_m30_bias` (level_detector.py) consumes the new behaviour
  automatically: when `m30_box_id` is reset to 0 after expiry, the
  function naturally returns `unknown` (existing filter:
  `m30_box_id > 0 AND m30_liq_top.notna()`). No code change required there.
- Phase 1 quantification predicts ~5-7 expirations / week → first live
  BOX_EXPIRED telemetry expected within 24-48h post-deploy.
- 7-day monitoring window active with daily M7 cross-ref and subgroup
  tracking (`ny_med_vol`, `london_high_vol` adverse-watch). If subgroup
  degradation confirms in live data → spec gating logic in iteration.

**Dependencies**

- DEC-2026-04-24-003 v2 (Purdue Calibration Protocol 12 steps) —
  followed in Phase 2 (k=5 purged WF + embargo=48 + bootstrap CI N=1000 +
  Bonferroni correction).
- DEC-2026-04-28-001 (Rule 3.b ensemble extension) — Phase 2.5 was the
  empirical application that validated single-winner deploy.
- P0 OBSERVABILITY-LOG-BLOCKS Fase B (Asana 1214327559721560, commits
  `93297c9` + `5cc4d83`) — provides the canonical `decision_log.jsonl`
  schema that BOX_EXPIRED telemetry uses.

**Related artifacts**

- `_audit/M30_BOX_STAGNATION_QUANTIFICATION.md` (Phase 1, 9 sections)
- `_audit/M30_BOX_STAGNATION_BACKTEST.md` (Phase 2, 10 sections)
- `_audit/M30_BOX_STAGNATION_ENSEMBLE_BRIEFBACK.md` (Phase 2.5, 7 sections)
- `_audit/M30_BOX_STAGNATION_PHASE3_DEPLOY.md` (Phase 3 deploy, 10 sections)
- `_audit/m30_box_stagnation_*.py` + `*.json` (read-only scripts + raw results)
- `tests/test_m30_box_stagnation.py` — 7 tests (6 spec + 1 regression for tz bug)
- Asana 1214327736913830 (P1.3 umbrella)

---

## Open decisions (awaiting Barbara)

### OPEN-2026-04-22-A — 56 unexpected `microstructure_YYYY-MM-DD.csv.gz` files in Jul-Nov 2025

**Context:** T0-D found 56 files in a naming pattern that DATA_MANIFEST says should only appear after 26 Nov 2025. Files sized ~4.5 MB. Could be post-hoc conversions, an earlier Quantower source, or something else.
**Status:** Flagged for future decision. Not urgent. Not blocking Tier 1.

### OPEN-2026-04-22-B — "Purdue University" GitHub reference

**Context:** Barbara mentioned `github.com/BPFeijen/FluxQuantumAI_APEX.git` as "Purdue University". This is the main repo URL. Unclear whether it refers to a folder within the repo, a methodology reference, or something Barbara will clarify later.
**Status:** ClaudeCode instructed not to search speculatively. Awaiting Barbara clarification when relevant.

### OPEN-2026-04-22-C — Activation Checklist retroactive application to SHADOW detectors

**Context:** Per T0-E, 7 detectors are in SHADOW state. Applying the Activation Checklist retroactively would identify at which checkpoint each one stalled (missing backtest? missing doc? missing toggle?). This could systematically explain the "developed but not activated" pattern.
**Status:** Not yet prioritized. Candidate for Tier 2 preamble.

---

## How to add a new decision

When Barbara makes a new architectural or operational decision:
1. Assign next `DEC-YYYY-MM-DD-NNN` ID
2. Fill template: Date, Status, Context, Decision, Rationale, Implications, Dependencies (if any), Related artifacts
3. Append to this file — do not modify existing entries
4. Reference the decision ID in the relevant task brief

## How to supersede a decision

Do NOT delete or modify the original. Add a new entry with:
- Status of original changed to "Superseded by DEC-YYYY-MM-DD-NNN"
- New entry references the original in Context

---

## Index

| ID | Title | Status |
|---|---|---|
| DEC-2026-04-21-001 | ROLLBACK-001 execution | ✅ Executed |
| DEC-2026-04-22-001 | Methodology: Wyckoff=ATS, ICT complementary | 🔁 Superseded by 24-001 |
| DEC-2026-04-22-002 | Pain Point 3 deferred | ✅ Active |
| DEC-2026-04-22-003 | NextGen architecture deferred | ✅ Active |
| DEC-2026-04-22-004 | Scalp/Swing Mode Switch | Pending impl |
| DEC-2026-04-22-005 | Position Monitor decoupling from MT5 | Pending impl |
| DEC-2026-04-22-006 | Documentation-First + Activation Checklist | ✅ Active |
| DEC-2026-04-22-007 | Block bias=unknown (absorbed) | Absorbed into 004 |
| DEC-2026-04-22-008 | dual_strategy_enabled=True validation only | Pending validation |
| DEC-2026-04-22-009 | Bias in range has a value, not unknown | ✅ Active |
| DEC-2026-04-22-010 | Hybrid platform, system decides scalp vs swing | ✅ Active |
| DEC-2026-04-24-001 | Tri-methodological foundation (Wyckoff+ATS+ICT) | ✅ Active |
| DEC-2026-04-24-002 | Data Integrity Boundary (tape vs derived parquets) | ✅ Active |
| DEC-2026-04-24-003 v1 | Purdue Calibration Protocol (8 steps) | 🔁 Superseded by v2 |
| DEC-2026-04-24-003 v2 | Purdue Calibration Protocol (12 steps) + Rule Ensemble | ✅ Active (canonical) |
| DEC-2026-04-27-001 | MT5 extinction completo (SISTEMA-SIGNAL-ONLY-INTERIM closure) | ✅ Executed |
| DEC-2026-04-28-001 | Standing Rule 3.b extension: ensemble methodology mandate during design | ✅ Active |
| DEC-2026-04-28-002 | M30 Box Stagnation Rule deployed (A K=2 GLOBAL) | ✅ Deployed |

---

**End of DECISIONS_LOG.md. This is a living document — append, don't modify.**

# DESIGN DOC Nível 1 v3 — Strategy Layer Architecture

**Sprint:** `sprint_strategy_layer_architecture_20260420`
**Status:** DRAFT v3 — awaiting Barbara ratification (supersedes v1 + v2)
**Type:** Architectural design doc — NO implementation, NO thresholds chosen
**Authors:** Claude (ML/AI Engineer) — co-signed by Barbara (PO) before Nível 2
**Date:** 2026-04-20
**Supersedes:** v1 (no phase-playbook linkage), v2 (linkage added but playbook-centric, internal inconsistencies)

**What changed v2 → v3:**
- §5 reorganized **phase-centric** — one subsection per Wyckoff phase, playbook embedded
- §3.1 Market Context Agent **expanded** with concrete phase-detection rules (was outline only)
- Matrix §5.0 simplified to 2 columns (Phase → Playbook + one-line rationale), acts as index to §5
- Judas Swing canonicalized in §5.6 only (v2 had inconsistent definitions in §5.0 vs §5.4)
- Playbook definitions compact — no repeated boilerplate

**Framework reference:** `FRAMEWORK_DataDriven_Calibration_APEX_v1.1.md` Parte D template

---

## 0. TL;DR

The APEX system currently applies one execution logic regardless of market phase. The 2026-04-20 Judas Swing incident (Phase E Markdown + NY open → 8 counter-trend LONG signals) is a direct consequence of the gap FitGap v2 already identified as "mais impactante": no Phase Detection, no Strategy Adaptation.

This doc introduces a **4-layer architecture** (Perception / Strategy / Decision / Defense) organized around **Wyckoff phase as the backbone**. For each phase, literature dictates which playbook is valid — and only that playbook activates. 4 MVP playbooks:

| Phase | Playbook |
|---|---|
| Phase A (stopping action) | NONE — stand aside |
| Phase B/C (range + test) | Range Reversal |
| Phase D (SOS/SOW transition) | Breakout + Retest |
| Phase E (trend) | Trend Pullback Continuation |
| Session-transitional (any non-A phase + NY/London open) | Judas Swing Reversal |
| Undefined | NONE — stand aside |

This doc does NOT choose thresholds, specify state machines, decide Sprint C v2 disposition, or deploy anything. Those are Nível 2 / calibration / integration sprints.

---

## 1. Problem Statement

### 1.1 The 2026-04-20 incident mapped to phase

- Wyckoff phase at incident time: **Phase E Markdown** (stable `daily_trend=short`, no recent Phase A reversal signal)
- ICT session: **NY open** (~13:00 UTC)
- Move: 4816 → 4847 upward (aggressive, one-direction, session-timed → **Judas Swing candidate**)
- Extreme: 2 ASK icebergs @ 4847.6-4847.8 GC (prob 0.35) — **absorption at top**
- Stall + reverse: price failed to continue, dropped -25pts in 40 min (**real move aligned with Phase E Markdown**)

Correct action per literature: **Judas Swing Reversal SHORT** (aligned with larger Phase E Markdown).

System action: 8 GO LONG signals (exhaustion-reversal logic activated regardless of phase).

### 1.2 Why the system failed

Three universally-applied rules fired in Phase E Markdown where literature says they must not:

1. `delta_4h_inverted_fix=true` — exhaustion-reversal interpretation (Range-Bound logic, Phase B/C)
2. `overextension_atr_mult=1.5` — counter-trend reversal permission (no phase gate)
3. `v4_iceberg.aligned=True` — ASK absorption misread as LONG-aligned (no extreme-vs-level differentiation)

Each rule has valid use cases in SPECIFIC phases. Applied universally, they break in all OTHER phases.

### 1.3 Literature consensus

- **Wyckoff (Villahermosa):** "If the market is in a bearish Phase we will avoid trading long... avoid having a loss by not attempting to trade against the trend."
- **ATS Two Problems to Solve:** "Defining the market cycle on your higher timeframe... helps you pick which strategy to use."
- **ICT Module 3:** "Before a BOS on the higher timeframe, you don't have directional bias — you have consolidation. The strategies you deploy in consolidation are not the strategies you deploy post-BOS."

All three say: **strategy is conditional on phase**. The system has no phase classifier and no selector.

### 1.4 FitGap already flagged this

`APEX_GC_Methodology_FitGap_v2.md` (2026-04-09):
> **Phase Detection:** ❌ GAP CRÍTICO. **Strategy Adaptation:** ❌ GAP CRÍTICO. *"Este é o gap mais impactante."*

This doc operationalizes that closure.

---

## 2. Architecture Overview

### 2.1 Four layers

```
PERCEPTION LAYER
  Agents read raw data → structured market-state assertions. Pure observation.
         ↓
STRATEGY LAYER
  Selector uses phase-driven matrix to choose playbook from Library. No execution.
         ↓
DECISION LAYER
  Given active playbook + current tick, confirm entry or wait. Executes orders.
         ↓
DEFENSE LAYER
  Monitors position vs playbook invalidation + TP. Closes or adjusts.
```

### 2.2 Why this works

- Phase classification happens ONCE, in Perception. Selector reads it. Downstream layers don't re-interpret phase.
- Each playbook is a self-contained response to one phase (or session overlay). Adding/removing playbooks is a Library edit.
- When phase is `undefined` or `phase_a_*`, Selector returns empty set → system stands aside. This is literature-aligned ("wait for A+ setup").
- Feature flag allows shadow-mode comparison Selector vs legacy logic before enforce.

---

## 3. Perception Layer

Six Agents. Each produces structured output consumed by Strategy Selector.

### 3.1 Market Context Agent ★ (core of the architecture)

**Output:** `MarketContext { phase, phase_confidence, regime, htf_direction, session, atr_regime }`

#### 3.1.1 Phase enum (Wyckoff canonical)

| Value | Wyckoff meaning | Typical duration |
|---|---|---|
| `phase_a_accumulation` | Stopping action after downtrend: PS → SC → AR → ST | Hours to days |
| `phase_a_distribution` | Stopping action after uptrend: PSY → BC → AR → ST | Hours to days |
| `phase_b_accumulation` | Cause-building range after PS/SC — UA/mSOW tests | Days to weeks |
| `phase_b_distribution` | Cause-building range after PSY/BC — UT/mSOS tests | Days to weeks |
| `phase_c_accumulation` | Spring / Shakeout — final false break of support | Hours |
| `phase_c_distribution` | UTAD — final false break of resistance | Hours |
| `phase_d_accumulation` | SOS + BU/LPS — markup initiation | Hours to day |
| `phase_d_distribution` | SOW + LPSY — markdown initiation | Hours to day |
| `phase_e_markup` | Sustained higher highs, higher lows | Days to weeks |
| `phase_e_markdown` | Sustained lower highs, lower lows | Days to weeks |
| `undefined` | Insufficient evidence | — |

Conservative rule: when evidence ambiguous → `undefined`. Selector stands aside.

#### 3.1.2 Phase detection rules (concrete, MVP rules-based)

Inputs (all from existing v5 parquet + H4 writer post-fix):

| Signal | Source |
|---|---|
| `m30_in_contraction` | v5 parquet |
| `m30_box_confirmed` | v5 parquet |
| `m30_spring_type` ∈ {none, spring_classic, spring_ordinary, spring_terminal} | v5 parquet |
| `m30_upthrust_high` (value when UT occurred) | v5 parquet |
| `daily_trend`, `weekly_trend`, `weekly_aligned` | v5 parquet |
| `m30_ats_trend` | v5 parquet |
| `h4_box_confirmed`, `h4_jac_dir` | H4 parquet (post Sprint H4-WRITER-FIX) |
| `wyckoff_event` ∈ {PS, SC, PSY, BC, AR, ST, SOS, SOW, LPS, LPSY, Spring, UTAD, UA, UT, mSOS, mSOW, none} | **MVP addition** — Structure Agent produces |

Classification logic (MVP outline — Nível 2 calibrates thresholds for "within N bars", "sustained HH/HL", etc.):

```
IF wyckoff_event in (PS, SC, AR, ST) within last N bars AND daily_trend just flipped from bearish → undetermined:
    phase = phase_a_accumulation

IF wyckoff_event in (PSY, BC, AR, ST) within last N bars AND daily_trend just flipped from bullish:
    phase = phase_a_distribution

IF m30_in_contraction == True AND prior phase was phase_a_accumulation AND wyckoff_event in (UA, mSOW):
    phase = phase_b_accumulation

IF m30_in_contraction == True AND prior phase was phase_a_distribution AND wyckoff_event in (UT, mSOS):
    phase = phase_b_distribution

IF wyckoff_event == Spring within last N bars AND m30_in_contraction still True:
    phase = phase_c_accumulation

IF wyckoff_event == UTAD within last N bars AND m30_in_contraction still True:
    phase = phase_c_distribution

IF wyckoff_event == SOS within last N bars AND m30_in_contraction just ended:
    phase = phase_d_accumulation

IF wyckoff_event == SOW within last N bars AND m30_in_contraction just ended:
    phase = phase_d_distribution

IF daily_trend == "long" AND weekly_aligned == True AND m30_in_contraction == False AND sustained HH/HL pattern:
    phase = phase_e_markup

IF daily_trend == "short" AND weekly_aligned == True AND m30_in_contraction == False AND sustained LH/LL pattern:
    phase = phase_e_markdown

ELSE:
    phase = undefined
```

`phase_confidence` ∈ [0, 1] — how many conditions match (e.g., Phase E with weekly NOT aligned → still phase_e_* but lower confidence).

**Dependencies not yet in system:**
- Wyckoff event detector (Spring/UTAD partial in v5; SOS/SOW/LPS/LPSY/UA/UT need Structure Agent extension — D6)
- H4 box state (Sprint H4-WRITER-FIX blocks — D1)

#### 3.1.3 Other fields

- `regime` derived from phase: Phase B/C → `range_bound`; Phase E → `trending`; Phase A/D → `transitional`; undefined → `unknown`
- `htf_direction` from Sprint C v2 `derive_h4_bias` + `_get_daily_trend` (already exists)
- `session` from `kill_zones.py` promoted to canonical input
- `atr_regime` from v5 `daily_atr_regime`

### 3.2 Liquidity Map Agent

**Output:** `LiquidityMap { active_pools, recent_sweeps, equal_highs_lows }`

Currently: `liq_top`, `liq_bot` (point-in-time), `l2_sweep_detected`/`l2_sweep_direction` (v5).

MVP additions (required for Judas Swing):
- Previous-session H/L (Asian, London, Previous Day)
- Equal highs/lows detector
- Sweep classification (continuation or reversal)

### 3.3 Structure & Phase Agent

**Output:** `Structure { m30_box, h4_box, d1_box, wyckoff_events, bos_chch }`

Current: `m30_box_*`, partial Wyckoff (`m30_spring_type`, `m30_upthrust_high`). MVP extends to full Wyckoff event enum + BOS/CHoCH. **Dependency:** Sprint H4-WRITER-FIX.

### 3.4 Value & Inefficiency Agent

**Output:** `ValueMap { fmv_levels, expansion_lines, fvg_gaps, pd_arrays }`

Current: `m30_fmv`, `m30_expansion_dir`, `m30_prev_expansion`. MVP adds `fvg_gaps` (ICT 3-candle imbalance) + `pd_arrays` (Premium/Discount of recent dealing range).

### 3.5 Session & Time Agent

**Output:** `SessionContext { current_session, kill_zone_active, time_to_next_event, session_range_so_far, session_open_ts }`

Current: `session` field in decision_log; `kill_zones.py` is telemetry only. MVP **elevates kill_zones to canonical input** + adds session range tracking. **Critical for Judas Swing.**

### 3.6 Anomalies Agent

**Output:** `Anomalies { iceberg_events, absorption_events, dom_imbalances, order_flow_shifts }`

MVP fix in scope: differentiate absorption **at extreme vs at level**. Current `v4_iceberg.aligned` conflates both. Sprint D iceberg-ML replaces rule-based proxy (parallel track, not blocking).

### 3.7 Contract

```
PerceptionOutput = {
    ts, market_context, liquidity_map, structure, value_map, session_context, anomalies,
    freshness: { per-agent staleness indicator }
}
```

Consumed by Selector as immutable snapshot.

---

## 4. Strategy Layer

### 4.1 Library schema

Each playbook:

```
Playbook = {
    name, literature_refs,
    phase_constraints: [allowed phases],
    session_constraints: [allowed sessions] | null,
    activation_conditions: [predicates over PerceptionOutput],
    entry_logic, invalidation, tp_logic, sl_logic,
    execution_style: "reversal" | "continuation" | "breakout",
    direction_rule: how LONG/SHORT derived from perception,
    confidence_formula,
    min_phase_confidence (default 0.5)
}
```

Regime and HTF direction are NOT separate constraint fields — regime is derived from phase (§3.1.3), direction is derived per playbook rule.

### 4.2 Selector activation rule

Playbook P activates at tick T iff ALL hold:

1. `perception.market_context.phase` ∈ `P.phase_constraints`
2. `P.session_constraints` is null OR `perception.session_context.current_session` ∈ `P.session_constraints`
3. All `P.activation_conditions` True
4. `perception.market_context.phase_confidence` ≥ `P.min_phase_confidence`

Any failure → P excluded.

- 0 playbooks active → system stands aside (literature-aligned).
- 1 playbook active → feeds Decision Layer.
- Multiple active → highest `confidence_formula` wins; ties break toward lower-risk (reversal > breakout). Nível 2 refines.

### 4.3 Output contract

```
SelectorOutput = {
    ts,
    active_playbooks: [
        { name, confidence, phase, activation_reasons, entry_logic_ref, invalidation_ref, tp_ref, sl_ref }
    ],
    deactivated_playbooks: [
        { name, missing_conditions }
    ]
}
```

### 4.4 What Selector does NOT do

Execute. Access broker state. Modify PerceptionOutput. Invent playbooks. Adjust constraints dynamically.

---

## 5. Phase-Centric Playbook Specification

### 5.0 Matrix (index to §5)

| Wyckoff Phase | Playbook | Rationale (literature one-liner) |
|---|---|---|
| phase_a_* | **NONE** | Villahermosa: "not recommended to start positions — risk too high" |
| phase_b_* | **Range Reversal** | ATS: "values magnetic, accumulate against deviation" |
| phase_c_* | **Range Reversal** (Spring/UTAD variant) | Wyckoff: highest-probability setup |
| phase_d_* | **Breakout + Retest** | Wyckoff: SOS/SOW + BU/LPS or LPSY — markup/markdown initiation |
| phase_e_* | **Trend Pullback Continuation** | ATS: "accumulate only in direction of HTF trend" |
| any non-A phase + session ∈ {ny_open, london_open} | **Judas Swing Reversal** (session overlay) | ICT: session-timed manipulation; direction aligned with larger phase |
| undefined | **NONE** | Conservative — wait for evidence |

Sections §5.1-§5.7 develop each row.

### 5.1 Phase A — Stopping Action (no playbook)

**Literature:** Villahermosa Ch13. Wyckoff Phase A events: PS/SC/AR/ST (accumulation side), PSY/BC/AR/ST (distribution side).

**Why no playbook:** prior trend ending but new range not established. Direction ambiguous. Villahermosa: "It is not recommended to start positions at this point as the assumed risk would be too high."

**System behavior:** Selector returns empty set. Decision Layer does nothing. Journal logs "Phase A detected — standing aside".

**Exit from Phase A:** Secondary Test confirmation → Phase B begins.

### 5.2 Phase B — Cause-Building Range

**Literature:** Wyckoff Phase B (oscillation between AR high and SC low or BC high and AR low). ATS Basic Strategy 1 "Range-Bound Markets".

**Playbook: Range Reversal**

- `phase_constraints`: `phase_b_accumulation`, `phase_b_distribution`, `phase_c_accumulation`, `phase_c_distribution`
- `session_constraints`: null (any)
- `activation_conditions` (outline):
  - `structure.m30_box_confirmed == True`
  - Price at extreme of contraction box (ATR distance threshold — Nível 2)
  - `anomalies.absorption_events` contains absorption against the approach direction at the extreme
  - 3-step reversal protocol evidence (exhaustion → absorption → initiative, per Bookmap)
- `direction_rule`: accumulation phase + price at range low → LONG; distribution phase + price at range high → SHORT
- `entry_logic`: initiative move after absorption confirmation
- `invalidation`: clean break of box extreme with volume expansion (breakout, not test)
- `tp_logic`: opposite side of box (FMV or far expansion)
- `execution_style`: reversal

**Maps from current system:** ATS `RANGE_BOUND` branch in `_resolve_direction`. Current branch fires regardless of phase — matrix restricts to Phase B/C only. `delta_4h_inverted_fix` exhaustion logic, **if validated by CLEAN-UP 2.5 for range context**, becomes part of activation_conditions here. Otherwise disabled.

### 5.3 Phase C — Spring / UTAD Test

**Literature:** Wyckoff Phase C (Spring for accumulation, UTAD for distribution — final false breaks of range extremes before Phase D commits). Bookmap failed-breakout patterns.

**Playbook: Range Reversal (Spring/UTAD variant)**

Same definition as §5.2, with additional activation condition:
- `structure.wyckoff_events` contains `Spring` (accumulation) or `UTAD` (distribution) within last N bars

**Confidence boost:** `confidence_formula` produces higher value when `wyckoff_event == Spring/UTAD` — Wyckoff-canonical highest-probability setups.

**Why not a separate playbook:** mechanics are identical to Phase B Range Reversal. Only confidence differs. Avoiding playbook proliferation.

### 5.4 Phase D — SOS/SOW Transition

**Literature:** Wyckoff Phase D — SOS (Sign of Strength, break of range resistance) + BU/LPS (Back-Up / Last Point of Support — retest of broken level as new support). Mirror: SOW + LPSY for distribution. ICT BOS + retest.

**Playbook: Breakout + Retest**

- `phase_constraints`: `phase_d_accumulation`, `phase_d_distribution`
- `session_constraints`: null (any)
- `activation_conditions` (outline):
  - `structure.wyckoff_events` contains SOS or SOW within last N bars, OR `structure.bos_chch` contains recent BOS
  - Break accompanied by volume/delta expansion (NOT absorption — distinguishes from Judas)
  - Retest has completed (price returned to broken level)
  - Rejection from retest (broken level holding in new role)
- `direction_rule`: accumulation Phase D → LONG; distribution Phase D → SHORT
- `entry_logic`: on retest rejection, in break direction
- `invalidation`: price re-enters prior range (break was false)
- `tp_logic`: next liquidity pool in break direction OR measured-move projection of range
- `execution_style`: breakout

**Maps from current system:** not explicit today. Closest: `entry_trigger` in v5 parquet. MVP promotes to explicit playbook.

### 5.5 Phase E — Markup / Markdown Trend

**Literature:** Wyckoff Phase E (trend outside range, sustained). ATS Trading Strategy 2 "Trending Markets". ICT Premium/Discount in trending bias.

**Playbook: Trend Pullback Continuation**

- `phase_constraints`: `phase_e_markup`, `phase_e_markdown`
- `session_constraints`: all EXCEPT `dead_zone`
- `activation_conditions` (outline):
  - `market_context.htf_direction` aligned with phase (bullish for markup, bearish for markdown)
  - Price pulled back to value (expansion line, FMV, or discount/premium PD array)
  - Pullback depth within normal retracement range (Nível 2 calibrates — NOT 1.5×ATR magic)
- `direction_rule`: Phase E Markup → LONG only; Phase E Markdown → SHORT only
- `entry_logic`: trend-direction entry from pullback exhaustion
- `invalidation`: pullback exceeds normal depth → may be Phase E ending, stand aside
- `tp_logic`: next liquidity pool in trend direction, previous swing, or opposite PD array
- `execution_style`: continuation

**Counter-trend entries strictly forbidden in Phase E.** This is what §5.0 matrix enforces — no Range Reversal, no OVEREXTENSION reversal, no exhaustion-reversal playbook in Phase E. Today's incident would have been blocked.

**Maps from current system:** PULLBACK / CONTINUATION branches in `_resolve_direction`. Current branches fire in `TRENDING_UP` / `TRENDING_DN` regardless of structural phase. Matrix restricts to Phase E (excludes Phase B/C mislabeled as "trending" by simpler heuristics).

### 5.6 Session-Transitional — Judas Swing Reversal (session overlay) ★

**Literature:** ICT (Khan, *The ICT Bible*, Module 3). ICT Daily Cycle AMD: Accumulation → **Manipulation (Judas)** → Distribution. "The first move of the day is often the lie."

**Why this is an overlay, not a phase-specific playbook:** Judas Swing is a session-timed event that can overlay ANY non-Phase-A Wyckoff phase. Its direction is dictated by the larger phase context.

**Playbook: Judas Swing Reversal**

- `phase_constraints`: `phase_b_*`, `phase_c_*`, `phase_d_*`, `phase_e_*` (all non-A, non-undefined)
- `session_constraints`: **`ny_open` OR `london_open` only** — hard gate
- `activation_conditions` (outline):
  - `session_context.current_session` in (`ny_open`, `london_open`)
  - `session_context.session_range_so_far` shows aggressive one-direction move from session open
  - `liquidity_map.recent_sweeps` contains sweep in last N minutes
  - `anomalies.iceberg_events` contains counter-direction iceberg at sweep extreme
  - `structure.bos_chch`: NO BOS in Judas direction on H4 (distinguishes from Breakout+Retest)
  - `market_context.phase_confidence` ≥ 0.5 (need reliable phase for direction)
- `direction_rule`: **real move opposes Judas attempt, aligned with larger phase**

| Larger phase | Typical Judas attempt | Real move (entry) |
|---|---|---|
| phase_e_markdown | up (buy-side sweep) | **SHORT** |
| phase_e_markup | down (sell-side sweep) | **LONG** |
| phase_b/c_accumulation | down (sell-side sweep of range low) | **LONG** (confluent with Range Reversal) |
| phase_b/c_distribution | up (buy-side sweep of range high) | **SHORT** (confluent with Range Reversal) |
| phase_d_accumulation | down (test of broken support) | **LONG** |
| phase_d_distribution | up (test of broken resistance) | **SHORT** |

- `entry_logic`: after absorption + stall + initial move back through session mean
- `invalidation`: price breaks Judas extreme (was actually real breakout → re-evaluate as Phase D if applicable)
- `tp_logic`: session range mean → opposite side of dealing range → previous day opposite extreme
- `sl_logic`: beyond Judas extreme + buffer
- `execution_style`: reversal

**Overlap with Range Reversal in Phase B/C:** at range extremes during session opens, both playbooks can activate. They confirm each other (same direction). Selector returns both as active; Decision Layer acts on higher confidence; journal records confluence. See Open Q7.

**2026-04-20 incident mapping:** Phase E Markdown + NY open + buy-side sweep + ASK icebergs + no follow-through → Judas Swing Reversal SHORT. System did LONG. Correct playbook enforcement blocks this.

### 5.7 Undefined — stand aside

When `phase == undefined` or `phase_confidence < min` for all candidate playbooks → Selector returns empty set. System does nothing. Journal logs "undefined — standing aside" with missing-evidence reasons.

---

## 6. Decision Layer

Consumes `SelectorOutput.active_playbooks` + current tick → emits `Decision{action, direction, sl, tp}` or `NoOp`.

Current system components that move here:
- `_resolve_direction` branches become distinct playbooks (§5) — function deprecated as universal resolver
- `ats_live_gate.check()` V1-V4 gates become **playbook-parameterized** (each playbook specifies which gates it requires)
- M30 bias hard-block becomes playbook-specific (Range Reversal explicitly ignores; Trend Pullback requires alignment)

Nível 2 wires exact gate requirements per playbook.

---

## 7. Defense Layer

Post-entry monitoring. Runs active playbook's `invalidation` + `tp_logic` against position state.

Current system components that move here:
- `position_monitor.py` infrastructure — kept
- `_check_regime_flip` — becomes one of several playbook-specific exits (not universal)
- `mfe_giveback_*` (currently disabled) — activated per playbook (Range Reversal may want tight giveback; Trend Pullback may want looser)
- Defensive exits from Sprint E draft (DE-1 iceberg counter, DE-2 delta extreme, DE-3 partial H4 flip, DE-4 MFE giveback) — distributed to relevant playbooks

### Trade Journal / Learning Agent

Every entry + outcome + deactivated-playbook record logged structured. Feeds future ML training. Out of MVP scope beyond structured logging contract.

---

## 8. Mapping: current system → target

| Current | Target |
|---|---|
| `derive_h4_bias`, `derive_m30_bias`, `_get_daily_trend` (`level_detector.py`) | Perception — Market Context Agent inputs |
| `_extract_levels`, `get_current_levels` | Perception — Structure Agent |
| Parquet writers (m30/m5/d1_h4) | Perception data pipeline |
| `kill_zones.py` (telemetry) | Perception — Session Agent (**elevated**) |
| Iceberg detection (rule-based) | Perception — Anomalies Agent (ML via Sprint D parallel) |
| `news_gate` (calibrated 2026-04-19) | Perception — Session Agent adjunct |
| `ats_live_gate.py` V1-V4 universal gates | Decision — **parameterized per playbook** |
| `_resolve_direction` branches (RANGE_BOUND/TRENDING/PULLBACK/CONTINUATION/OVEREXTENSION) | Strategy Library — become distinct playbooks per §5 |
| M30 bias universal veto | Decision — **playbook-specific** |
| `delta_4h_inverted_fix=true` | **Condition inside Range Reversal only** (pending CLEAN-UP 2.5). Disabled globally. |
| `overextension_atr_mult=1.5` in TRENDING branch | **Removed from Phase E** (matrix excludes). Phase B/C may use — Nível 2 decides. |
| `position_monitor._check_regime_flip` | Defense — one of several playbook-specific exits |
| `mfe_giveback_*` (disabled) | Defense — **activated per playbook** |

---

## 9. Dependencies and Sequencing

### 9.1 Blocking for Nível 2

| # | Dependency | Blocks |
|---|---|---|
| D1 | Sprint H4-WRITER-FIX (P1 backlog) | Structure Agent H4/D1 reliability; Phase classifier |
| D2 | CLEAN-UP 2.5 CAL-03 investigation | Range Reversal's use of inverted_fix |
| D3 | Session Agent MVP (kill_zones promotion) | Judas Swing functional |
| D4 | Liquidity Map — previous-session levels | Judas Swing functional |
| D5 | Phase classifier MVP spec | Market Context Agent primary output |
| D6 | Wyckoff event detector extension (SOS/SOW/LPS/LPSY) | Phase classifier inputs |

### 9.2 Parallel tracks

- Sprint D iceberg-ML (in progress) — improves Anomalies Agent; not blocking
- News gate — calibrated 2026-04-19, no change

### 9.3 Proposed sequence

1. Ratification of this doc (Nível 1 v3)
2. Parallel: CLEAN-UP 2.5 (ClaudeCode READ-ONLY), D1 H4-WRITER-FIX scoping, D3-D6 sub-sprint scoping
3. Nível 2 Design Doc — per-playbook detailed specs, state machines, calibration targets
4. Calibration sprints per playbook (framework v1.1)
5. Implementation per playbook (ClaudeCode, shadow-first)
6. Integration Sprint — Selector into Decision Layer, feature flag live
7. Shadow 24-48h per framework v1.1 Parte B
8. Enforce per playbook, incremental
9. Legacy deprecation — remove universal `delta_4h_inverted_fix`, remove universal `overextension_atr_mult`, deprecate `_resolve_direction` branches

---

## 10. Feature Flag Strategy

```json
"strategy_layer": {
    "enabled": true,
    "enforce": false,
    "shadow_started_at": null,
    "active_playbooks_allowed": [],
    "legacy_fallback_enabled": true
}
```

- **Shadow:** Selector runs, logs would-be decisions. Legacy authoritative.
- **Enforce per playbook:** `active_playbooks_allowed: ["judas_swing_reversal"]` → Selector authoritative only when that playbook active.
- **Full enforce:** `active_playbooks_allowed: ["*"]`.

---

## 11. What this doc does NOT decide

- Thresholds (calibration scope)
- `delta_4h_inverted_fix` final disposition (CLEAN-UP 2.5)
- `overextension_atr_mult` final disposition (Nível 2)
- Sprint C v2 commit `074a482` (revert/patch/integrate — Nível 2 integration)
- ML replacements (Sprint D parallel)
- Gate wiring per playbook (Nível 2)
- Playbook implementation order (Open Q1)

---

## 12. Open Questions for Barbara

**Q1. MVP playbook implementation priority.** Suggestion: Judas Swing → Trend Pullback → Range Reversal → Breakout+Retest. Barbara decision.

**Q2. OVEREXTENSION disposition.** Matrix excludes from Phase E. Options: (a) deprecate entirely; (b) allow in Phase B/C Range Reversal only; (c) reconsider post-Nível 2 with data. Recommend (a). Barbara decision.

**Q3. Legacy fallback duration.** Recommend per-playbook 3-month sunset. Barbara decision.

**Q4. `delta_4h_inverted_fix` disposition.** Deferred to CLEAN-UP 2.5 output.

**Q5. MVP scope strict?** Recommend yes — new playbooks require Barbara sign-off. Barbara decision.

**Q6. Judas Swing calibration data.** 64 real trades insufficient alone. Acceptable to use OHLCV reconstruction + `entry_quality_dataset` 486 historic, validate against 64 real as sanity? Framework v1.1 allows with "degraded confidence" flag. Barbara decision.

**Q7. Overlapping playbooks (Phase B/C + session open).** Judas Swing + Range Reversal both activate. Recommend (a) return both, Decision Layer picks higher confidence, journal logs confluence. Barbara decision.

---

## 13. Approval

- [ ] Barbara (PO) — Date: ______________
- [ ] Claude (ML/AI Engineer) — Date: ______________

**Signing implies:**
- 4-layer architecture ratified
- Phase-centric §5 structure + matrix §5.0 ratified as authoritative
- MVP scope (4 playbooks + undefined/Phase-A stand-aside) ratified
- Dependencies D1-D6 acknowledged
- Sequence §9.3 accepted
- Q1-Q7 answered or deferred explicitly

After signing: Nível 2.

---

## 14. References

**Literature:**
- Wyckoff 2.0 (Villahermosa) — `Wyckoff-Methodology-in-Depth-Ruben-Villahermosa.pdf`
- ICT (Khan, *The ICT Bible*) — `652432368-Ict-Institutional-Smc-Trading.pdf` + Module 3
- ATS docs (project knowledge) — Basic Strategy 1, Trading Strategy 2, Two Problems to Solve, Trade System, Implementation Plan
- Bookmap Order Flow — `971796947EntryPatternsBookmap.pdf` + 5 Modules
- Lopez de Prado — *Advances in Financial Machine Learning* (framework reference)

**FluxQuantumAI:**
- `APEX_GC_Methodology_FitGap_v2.md` — Phase Detection gap
- `APEX_THRESHOLDS_INVENTORY_20260419.md`
- `FRAMEWORK_DataDriven_Calibration_APEX_v1.1.md`
- `SYSTEM_Architecture_Current_20260409.md`
- `REGIME_FLIP_FORENSIC.md` (2026-04-20)
- `CROSS_REF_REPORT.md` (2026-04-20)
- `DATA_INVENTORY.md` (2026-04-20)
- Barbara brainstorm 2026-04-20 — 4-layer architecture proposal (source of this doc's structure)

**Incident:**
- `INCIDENT_20260420_LONG_DURING_DROP/20260420_145845/INITIAL_ANALYSIS.md`

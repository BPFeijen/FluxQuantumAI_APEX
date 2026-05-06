# Investigation — Orphan Fast Direction Sources

**Author**: CC#3
**Date**: 2026-05-06
**Asana**: 1214556369070092 (BUG-SIGNAL-INVERTED) → ML-DS comments 1214584454845303 (REDIRECT) + 1214584753141868 (Milestone 1 ACK + STEPS 4-7 green-light)
**Type**: READ-ONLY investigation, zero mutation, zero design proposals
**Companion comments on Asana**: 1214584099264399 (milestone 1) + 1214585083066079 (final report)

---

## CONTEXT

Barbara rejected the cascade direction fallback spec (`bug_cascade_direction_fallback_spec.md`) because the proposed fix relied on `m30_bias_confirmed` which has ~1.5h confirmation lag — unacceptable when the system continues to emit inverted SHORT signals during fast bullish rallies (e.g., 2026-05-06 04:09–04:51 UTC: 200 SHORT @ ZR ALPHA during +91pts bull move).

ML-DS redirected to investigation phase: identify FAST direction sources already present in the codebase that may have been ignored. READ-ONLY scope, "what exists" only.

---

## STEP 1 — M5 BOX BIAS

**Module exists**: `live/m5_updater.py` (24KB, mtime Apr 15 07:49). Running per service log: `M5 Updater started (60s cadence, rebuilds gc_m5_boxes.parquet)`.

**Schema** (`gc_m5_boxes.parquet`, 17 cols):
```
open, high, low, close, volume, bar_delta, atr14,
m5_liq_top, m5_liq_bot, m5_fmv, m5_box_high, m5_box_low,
m5_box_confirmed (bool), m5_box_id, at_struct_level,
m5_phase_state, m5_value_stack
```

Last 100 bars: 53% confirmed, 47% unconfirmed.
**phase_state distribution** (last 100 bars): `CONTRACTION` 46, `EXPANSION_MULTI` 37, `EXPANSION_EARLY` 17.

🚩 **ZERO `m5_bias` derivation function**:
- `m5_updater.py` does NOT have `derive_m5_bias()` (parallel to `derive_m30_bias()`)
- `gc_m5_boxes.parquet` does NOT have `m5_jac_dir` column (M30 has `h4_jac_dir`, `d1_jac_dir`)
- `m5_phase_state` is per-bar phase, not directional bias

🚩 **ZERO consumption in `event_processor.py`**:
- `grep "m5_bias"` → 0 matches
- `grep "m5_phase_state"` → 0 matches
- `grep "m5_box_confirmed"` → 0 matches
- `grep "m5_value_stack"` → 0 matches

**Status**: M5 boxes COMPUTED + WRITTEN, but **direction signal is ORPHAN**. No consumer downstream.

---

## STEP 2 — L2 MICROSTRUCTURE DIRECTION

### 2A. TickBreakoutMonitor

`live/tick_breakout_monitor.py` (16KB).
- Imported in `event_processor.py:67`
- Started in `event_processor.py:4758`: `self._tick_breakout.start()`
- **Internal state machine**: `CONTRACTION → CANDIDATE_UP/CANDIDATE_DN → BREAKOUT_UP/BREAKOUT_DN`
- `self._breakout_dir` ∈ {`"UP"`, `"DN"`, `None`} (real-time, tick-driven)
- `status()` method exposes: `{state, box_id, box_high_gc, box_low_gc, breakout_dir, breakout_extreme}`

🚩 **STATUS NEVER CONSUMED**: `grep "tick_breakout.status\|_tick_breakout\." event_processor.py` → only `_tick_breakout.start()` line 4758. `status()` method exists but zero callers. **`breakout_dir` is REAL-TIME ORPHAN**.

(TickBreakoutMonitor injects levels via `_inject_levels` when it detects breakout, but does NOT export `breakout_dir` as a bias signal to `_get_strategy_mode`.)

### 2B. Iceberg side

- `decision_live.iceberg.side`: **`"UNKNOWN"`** (current heartbeat)
- Iceberg side classification logic exists in code (line 4498 area: "side vs direction" check)
- Alignment check: `_ice_aligned = (side in ("BUY","BID","LONG") and direction=="LONG") or (side in ("SELL","ASK","SHORT") and direction=="SHORT")`
- **Status**: side classification has logic but is consistently UNKNOWN in heartbeat — iceberg detector is NOT classifying BUY vs SELL side (iceberg_data flow direction is not being exposed)

### 2C. Iceberg flow direction

- `protection.iceberg.flow_relation` field exists: `FAVORS_LONG` / `FAVORS_SHORT` / `UNKNOWN`
- `decision_live.protection.iceberg.flow_relation`: **`"UNKNOWN"`** currently
- Source: `DEFENSE_MODE` rule-based (line 4498)

### 2D. delta_acceleration / delta_change_rate

ZERO matches in event_processor.py. **Do NOT exist**.

### 2E. L2 sweep / displacement detection

- `_detect_trend_displacement()` in `event_processor.py:3076` — used inside `_get_trend_entry_mode` (Sprint 9 CONTINUATION path)
- Returns `(disp_valid, disp_reason, disp_low, disp_high)` — exists but only consumed inside TRENDING dispatch, **not as bias source upstream**

---

## STEP 3 — ATS TREND LINE

Module: `live/ats_trend_line.py` (12.7KB).

**API**:
- `compute_trend_line_state(bars, as_of)` returns `ATSTrendLineState`
- `ATSTrendLineState.direction` field — long/short/unknown
- Helper: `compute_trend_line_state(bars, as_of).direction` exposed (line 307)
- NON-REPAINTING design

🚩 **ZERO consumption in `event_processor.py`**:
- `grep "ats_trend_line\|compute_trend_line\|TrendLine"` in event_processor.py → 0 matches
- No import
- No call site

**Settings flag**: `config/settings.json:153`: `"ats_trend_line_enabled": true` — **flag exists but NO consumer in event_processor.py**.

**Status**: ATS Trend Line is **COMPLETELY ORPHAN** in event_processor.py. Module computed/available, flag enabled, but zero strategy_mode/direction consumption.

---

## STEP 4 — provisional_m30_bias

**Where computed**:
- `live/level_detector.py:993` — `provisional_m30_bias, _ = derive_m30_bias(m30_df, confirmed_only=False)`
- Same function as `m30_bias` (confirmed) but with `confirmed_only=False` flag → captures bias **before structural confirmation**
- Updated in `event_processor.py:1706` on macro context refresh

**Where consumed in `event_processor.py`**:

| Line | Type | Effect |
|------|------|--------|
| 493 | init `"unknown"` | telemetry init |
| 841, 985, 1061 | heartbeat write | TELEMETRY (decision_log + service_state.json) |
| 2424, 2561 | decision logic | **TELEMETRY ONLY (does NOT block)** |

**Critical line 2561 detail**:
```python
if not m30_bias_confirmed:
    if provisional_m30_bias in ("bullish", "bearish"):
        log.info("M30_BIAS_PROVISIONAL_ONLY: ... -> no hard block")
        # ↑ INFO log only. Direction passes through.
else:
    if m30_bias == "bullish" and direction == "SHORT":
        return  # ← HARD BLOCK only when CONFIRMED
```

**Difference vs m30_bias_confirmed**:
- `m30_bias` (confirmed): HARD-BLOCKS counter-trend signals (lines 2571–2580)
- `provisional_m30_bias`: TELEMETRY ONLY, logs but does NOT block

🚩 **ROOT CAUSE finding**: during burst 04:44 UTC, `provisional_m30_bias=bullish` BUT `m30_bias_confirmed=False` → SHORT signals passed with only INFO log. **provisional bias is orphan-by-design for blocking purpose**.

---

## STEP 5 — GIT HISTORY

### TickBreakoutMonitor (`live/tick_breakout_monitor.py`)

| Commit | Date | Action |
|--------|------|--------|
| 3e80ed6 | 2026-04-21 | **Initial introduction** — module + import + `_tick_breakout.start()` ALL together in "Production snapshot before stabilization sprint" |
| b3da45f | later | Audit doc reference (no behavior change) |
| a2ef9a9 | later | Upload (no behavior change) |

🚩 **Critical finding** — ZERO commits add/remove a call to `tick_breakout.status()` or `breakout_dir`:
```
git log --all -G "tick_breakout.*status|breakout_dir" → 0 functional commits
```

**Verdict**: TickBreakoutMonitor was introduced COMPLETE (module + import + start) in ONE commit, but **`status()` was NEVER INTEGRATED** in event_processor for consumption. It is NOT a "disabled commit" — it is **never integrated**. ML-DS oversight confirmed.

### ATS Trend Line (`live/ats_trend_line.py`)

- Commit: `8df5db4` "feat(trend-line): add ATS Trend Line Part A (rule-based, no settings)"
- **Single commit** introducing Part A
- ZERO commits adding consumption in event_processor.py
- Settings flag `ats_trend_line_enabled: true` exists but **disconnected** — flag introduced in settings.json but no consumer code

**Verdict**: ATS Trend Line is "Part A" — implementation phase only. Part B (integration) **never delivered**.

### m5_updater / m5_bias

- `m5_updater.py` introduced in 3e80ed6 (production snapshot)
- `git log -S "m5_bias"` → **ZERO commits ever** — `derive_m5_bias()` function never existed
- `gc_m5_boxes.parquet` has `m5_phase_state` but no `m5_jac_dir` column

**Verdict**: M5 bias derivation is **not implemented**, not disabled.

### ALPHA mode 71.6%

- Search "ALPHA mode" / "71.6%" in git log → 0 contextual matches
- ML-DS earlier reference may have been confusion between ALPHA trigger TYPE (iceberg-driven) vs hypothetical "ALPHA mode" feature
- **No evidence** of historical ALPHA mode disable

---

## STEP 6 — ASANA SWEEP

Searches in FluxQuantumAI_Live project (1214204918416708):

| Query | Hits | Notes |
|-------|------|-------|
| "TickBreakoutMonitor tick_breakout" | 2 (LFP-7-CAL-4 unrelated, BUG-SIGNAL-INVERTED this task) | **ZERO integration task** |
| "M5 bias rapid fast direction real-time" | 1 (BUG-SIGNAL-INVERTED only) | **ZERO M5 bias task** |
| "ATS Trend Line integration" | 10 unrelated tasks (LFP family) | **ZERO ATS Trend Line integration task** |

**Verdict**: NO Asana task EVER requested integration of these 3 orphan sources. Historical confirmation of ML-DS oversight.

---

## STEP 7 — CONSOLIDATED MATRIX

| Source | EXISTS | COMPUTED | CONSUMED | DISABLED commit? | Latency | Reliability | Asana task | Status |
|--------|--------|----------|----------|------------------|---------|-------------|------------|--------|
| **TickBreakoutMonitor.status().breakout_dir** | ✅ YES (16KB module + state machine) | ✅ Real-time tick-driven | ❌ NO (status() never called outside diagnostics) | ❌ Never integrated (introduced 2026-04-21 already orphan) | **<1s tick-level** | HIGH (deterministic state machine, JAC-gated) | ZERO | **ORPHAN — highest value finding** |
| **M5 box phase_state** | ✅ YES (parquet 17 cols) | ✅ 60s cadence | ❌ NO bias derivation function | ❌ Never implemented (no `m5_bias` commits ever) | ~1–5min | UNKNOWN (no derivation) | ZERO | **NOT IMPLEMENTED** |
| **ATS Trend Line .direction** | ✅ YES (12.7KB module) | ✅ Bar-close non-repainting | ❌ NO (zero import in event_processor) | ❌ Part A only — Part B never delivered | M5/M30 bar close | UNKNOWN (no production data) | ZERO | **ORPHAN — Part A only** |
| **provisional_m30_bias** | ✅ YES | ✅ Every macro refresh (~30s) | ✅ PARTIAL (telemetry only, line 2561) | N/A — by design TELEMETRY | ~30s | MEDIUM (uncombined Wyckoff) | N/A | **TELEMETRY-ONLY by design** |

---

## CRITICAL FINDING — Why TickBreakoutMonitor is orphan

**Mechanical**: Commit 3e80ed6 (2026-04-21 "Production snapshot before stabilization sprint") introduced the module + import + `start()` **but did not wire `status()` consumption**. This is architectural oversight, not a bug.

**Speculative reasons** (not verifiable without interview):

1. **Half-shipped feature**: Module was designed to inject levels (which it does via `_inject_levels`) AND export breakout_dir (via `status()`), BUT only the level-injection part was wired. Possibly the developer planned to wire `status()` later but never returned.
2. **Implicit role redefinition**: TickBreakoutMonitor may have been redesigned mid-development as level-injector only, with `status()` leftover from older design.
3. **No backtest validation**: `status().breakout_dir` lacks walk-forward CV. Without validation, integration was deferred.

**Empirical**: At runtime now (PID 23000), TickBreakoutMonitor is RUNNING (`start()` called at event_processor.py:4758) and `_breakout_dir` is active in memory, but event_processor NEVER reads this field — strategy_mode dispatch ignores this fast direction.

**Empirical evidence during burst 04:44 UTC**:
- TickBreakoutMonitor was running (PID 23000 includes _tick_breakout thread)
- Likely state: `BREAKOUT_UP` or `CANDIDATE_UP` (price 4658.7 above box high)
- `breakout_dir` likely `"UP"` → consistent with bullish rally
- BUT event_processor did not consult → emitted SHORTs

**Counterfactual**: If `TickBreakoutMonitor.status().breakout_dir` had been consumed as Layer 2 of the cascade ladder, the burst at 04:44 would have been suppressed (`breakout_dir=UP` → `resolved_trend=long` → `TRENDING_LONG` → SHORT at liq_top blocked).

---

## DESIGN HINT — Source ranking (per ML-DS request — ranking only, NOT proposal)

**PRIMARY candidate** (real-time, validated logic, zero new code):

🥇 **TickBreakoutMonitor.status().breakout_dir** — <1s latency, deterministic state machine, already running, near-zero LOC integration cost (just call `status()` and map `breakout_dir → resolved_trend`). HIGHEST VALUE.

**SUPPLEMENTARY candidates** (medium latency, additional validation needed):

🥈 **provisional_m30_bias** — already computed/written, just need to upgrade from telemetry to soft-block. Lower latency (~30s) than confirmed (~5min+). Cheap to wire.

🥉 **ATS Trend Line .direction** — needs import + bar query but module exists. Medium latency (M5/M30 close).

**AVOID**:

⛔ **M5 phase_state** — no derivation function, would require NEW logic + calibration. Not "wire orphan", actually NEW work.
⛔ **delta_4h sign** — CAL-03 INVEST_01 documented unstable (per `bug_cascade_direction_fallback_spec.md` §4.3).
⛔ **Iceberg side** — heartbeat shows `side="UNKNOWN"` consistently. Source classifier not reliable.

---

## ML-DS DECISION POINTS

- Q1: Adopt TickBreakoutMonitor.status() as Layer 2 fast (replacing `m30_bias_confirmed`)?
- Q2: Upgrade `provisional_m30_bias` from telemetry to soft-block?
- Q3: Wire ATS Trend Line as additional layer or skip?
- Q4: Combine 2+3 sources for fault-tolerance, or single-source primary?
- Q5: Backtest counterfactual on 2026-05-06 burst before integration?

---

## CC#3 STATUS

ZERO mutation. ZERO design proposals. ML-DS decides path forward.

Companion file: `bug_cascade_direction_fallback_spec.md` (the spec that Barbara rejected — superseded by this investigation but kept for context).

ETA next milestone: depends on ML-DS scope decision.

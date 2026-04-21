# CLAUDECODE PROMPT — INVEST-03: V4 Iceberg `aligned` Logic Regime-Segmented Investigation

**Sprint ID:** `sprint_invest_03_iceberg_aligned_20260420`
**Authorization:** Barbara ratified 2026-04-20 (scope a+x: aligned logic only, verdict-only not fix-apply)
**Type:** **READ-ONLY** — zero code edits, zero config edits, zero restarts, zero service touches
**Duration target:** 3-4 hours
**Output:** `C:\FluxQuantumAI\sprints\sprint_invest_03_iceberg_aligned_20260420\INVEST_03_REPORT.md`
**Framework reference:** `FRAMEWORK_DataDriven_Calibration_APEX_v1.1.md`
**Design Doc context:** `DESIGN_DOC_Strategy_Layer_Architecture_v3.md` §3.6 Anomalies Agent
**Related:** INVEST-01 completed 2026-04-20 (delta_4h_inverted_fix); INVEST-02 pending (overextension)

---

## 0. Why

**INVEST-01 §7.1 discovered that 6 of 8 GO LONG entries in the 2026-04-20 incident were V4 iceberg-driven, not inverted_fix-driven.** The `v4_iceberg.aligned=True` classifier misread ASK icebergs at the extreme (absorption against continuation) as "LONG-aligned" — contributing directly to Barbara's capital loss.

Per memory context:
- `v4_iceberg.aligned=True` with ASK icebergs @ 4847.6-4847.8 (prob 0.35, at top of Judas Swing rally) → classified as LONG-aligned
- Iceberg `aligned` classifier has direct effect on execution: **aligned → bigger lot size; contra → NOT OPEN** (memory #5)
- Misclassification = signals that should have been blocked get executed instead

**Investigation question:**

> In which Wyckoff phase + ATS regime + iceberg-context combinations does the current V4 `aligned` classifier produce correct direction classification, and in which does it fail? Is the misread of "absorption at extreme" vs "iceberg at level" systematic or situational?

**Scope strictly limited to the `aligned` classifier logic.** Thresholds CAL-1 to CAL-8 (absorption=12.28, LOI=0.14, collision_band=1.60pts, etc.) are OUT OF SCOPE for INVEST-03. Those are separate calibration targets for future sprint if needed.

**This investigation produces verdict + spec only. Fix prompt is separate sprint after Barbara ratification.**

---

## 1. Hard Limits

1. **READ-ONLY.** Writes only to sprint output directory.
2. **No new thresholds invented.** Investigate the existing `aligned` classifier only.
3. **Regime tagging uses EXISTING v5 parquet columns** — same classifier as INVEST-01/02 for cross-comparability (see §3.2).
4. **L2 data coverage:** Full **10-month continuous L2** Jul 2025 → today. Databento covers Jul 2025 → 25/11/2025. dxFeed covers 26/11/2025 → today. Cross-check both sources; do NOT limit analysis to Databento only (memory #12).
5. **Stop and ask Barbara** if:
   - `aligned` classifier code cannot be located
   - Iceberg JSONL schema differs from expected
   - L2 coverage has unexpected gaps
   - Less than 100 historical iceberg events per (regime × aligned=T/F) cell for statistical minimum

---

## 2. Phase 1 — Discovery: Locate `aligned` classifier code (30 min)

### 2.1 Code search

```powershell
# Search for aligned classifier logic in V4 iceberg path
Get-ChildItem "C:\FluxQuantumAI" -Recurse -Include "*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "iceberg.*aligned|aligned.*iceberg|\.aligned\s*=|v4_iceberg|ats_iceberg_gate|iceberg_receiver|iceberg_gate" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" } |
    Out-File "C:\FluxQuantumAI\sprints\sprint_invest_03_iceberg_aligned_20260420\aligned_code_discovery.txt"
```

### 2.2 Identify the canonical `aligned` computation

From search results, locate the function/method that **sets** `v4_iceberg.aligned` (not just reads it). Report:

- File path + line number of the assignment
- Full function context (±30 lines around the assignment)
- Inputs consumed (iceberg event fields, trade direction, price level, etc.)
- Logic decision tree (what conditions make it True vs False)
- Output consumers (what downstream code uses `aligned`)

### 2.3 Document current logic in plain English

Before testing, explain in 3-5 sentences exactly what the current classifier does. Example format:

> "The V4 `aligned` classifier sets aligned=True when:
> - trade direction is LONG AND iceberg side is BID (buy-side absorption supporting long bias), OR
> - trade direction is SHORT AND iceberg side is ASK (sell-side absorption supporting short bias)
> It does NOT account for [A] / [B] / [C] when setting the flag."

This explicit articulation is the **hypothesis under test** for subsequent phases.

### 2.4 Hypothesis refinement

Based on §2.3, formulate 2-3 **testable hypotheses** about when the classifier fails. Example:

- H1: aligned classifier fails when iceberg occurs **at session extreme** (above session high for ASK, below session low for BID) — because absorption at extreme = reversal signal, not continuation
- H2: aligned classifier fails in **trending regimes** (phase_e_*) when iceberg is counter-trend — because smart money absorption against trend = reversal signal
- H3: aligned classifier fails when **prior price move was aggressive single-direction** (consistent with Judas Swing pattern)

Phases 3-4 test these hypotheses empirically.

---

## 3. Phase 2 — Regime Tagging + L2 Coverage Verification (45 min)

### 3.1 Regime classifier — SAME as INVEST-01/02

```
IF m30_in_contraction == True AND weekly_aligned == False:
    regime = "RANGE"
ELIF m30_in_contraction == False AND weekly_aligned == True AND daily_trend in ("long", "short"):
    regime = "TREND"
ELSE:
    regime = "TRANSITIONAL"
```

Direction-segmented TREND: `TREND_UP` / `TREND_DN`.

### 3.2 L2 data coverage verification (CRITICAL — memory #12)

Before analysis, confirm continuous L2 coverage:

```python
# Verify Databento + dxFeed sources both present
# Databento: Jul 2025 → ~25/11/2025
# dxFeed: ~26/11/2025 → today
# Total: 10 months continuous

# Load gc_ohlcv_l2_joined.parquet (already joined)
# Report: L2 data presence per day across Jul 2025 → today
# Flag any gaps > 4 consecutive hours during market-open times
```

If L2 coverage has unexpected gaps → STOP-AND-ASK Barbara.

### 3.3 Iceberg JSONL inventory

From `C:\data\iceberg\iceberg__*.jsonl` (386 files per DATA_INVENTORY):

For each file:
- Date range covered
- Row count
- Schema (keys per event)
- Match with L2 coverage (should be fully covered by Databento+dxFeed 10 months)

Aggregate: total iceberg events Jul 2025 → today, by side (BID/ASK), by type (if `type` field exists).

### 3.4 Walk-forward sub-periods — SAME as INVEST-01/02

- **Sprint 8 training:** 2025-07-01 → 2025-11-30
- **Post-deploy:** 2025-12-01 → 2026-03-31
- **Recent live:** 2026-04-01 → today (full 20 days, includes incident)

---

## 4. Phase 3 — Segmented Backtest of `aligned` Classifier (90 min)

### 4.1 Reconstruct aligned decisions on historical iceberg events

For each historical iceberg event in JSONL files (Jul 2025 → today):

1. Determine the "implied trade direction" at that tick based on prevailing `daily_trend`:
   - `daily_trend == "long"` → implied direction LONG
   - `daily_trend == "short"` → implied direction SHORT
   - `daily_trend == "unknown"` → skip (no direction context)

2. Apply current `aligned` classifier logic (from §2.2) to get `aligned = True/False`

3. Compute forward return at M5 close + 30 min / 60 min / 4h in the **implied trade direction**:
   - If implied LONG: `fwd_return = close(t+N) - close(t)` (positive = aligned hypothesis correct)
   - If implied SHORT: `fwd_return = close(t) - close(t+N)` (positive = aligned hypothesis correct)

4. Tag each event with: regime (§3.1), phase proxy (§3.2), aligned flag, forward returns, iceberg side/type/probability.

### 4.2 Primary result tables

**Table A — aligned=True cells:**

| Regime | N | fwd_30m mean | WR+ | CI crosses 0? | Cohen's d |
|---|---|---|---|---|---|
| RANGE | ... | ... | ... | ... | ... |
| TREND_UP | ... | ... | ... | ... | ... |
| TREND_DN | ... | ... | ... | ... | ... |
| TRANSITIONAL | ... | ... | ... | ... | ... |

**Table B — aligned=False cells (same structure).**

**Interpretation:**
- If aligned=True cells have positive mean fwd_return with d > 0.2 → classifier works in those regimes
- If aligned=True cells have **negative** mean fwd_return → **classifier actively misleads** in those regimes
- Comparison Table A vs B: is aligned=True a better predictor than aligned=False?

### 4.3 Hypothesis-specific segmentation (from §2.4)

**H1 — Iceberg at session extreme:**
Segment aligned=True events by "iceberg_at_session_extreme" (boolean: iceberg price within X pts of session H/L within last N minutes). Report fwd_returns per sub-segment.

**H2 — Counter-trend in Phase E:**
Segment by `daily_trend` vs iceberg side:
- LONG implied + BID iceberg (trend-aligned)
- LONG implied + ASK iceberg (counter-trend)
- SHORT implied + ASK iceberg (trend-aligned)
- SHORT implied + BID iceberg (counter-trend)

Report aligned flag outcome and fwd_returns per sub-segment.

**H3 — Post-aggressive-move context:**
Segment by "session_range_aggressive" (session has moved > X × ATR in single direction since open, within last M minutes). Report aligned flag and fwd_returns with/without this context.

### 4.4 Statistical rigor per framework v1.1 §A.3

- Bootstrap 95% CI (N=1000 resamples) on each mean
- Cohen's d vs baseline (baseline = aligned=False in same regime)
- Walk-forward stability across 3 sub-periods (§3.4)

### 4.5 Minimum sample check

Per framework v1.1: minimum N=100 per cell for robust statistics. Cells with N<100 → flag as preliminary, bootstrap CI still computed but with warning.

---

## 5. Phase 4 — 2026-04-20 Incident Counterfactual (20 min)

For each of the 8 GO LONG entries at 14:09-14:56 UTC (from `decision_log_last60min.jsonl`):

1. Extract `v4_iceberg.aligned` value at tick time
2. Identify the iceberg event(s) that triggered the classifier
3. Classify each event per H1/H2/H3 sub-segments
4. Compute what the **corrected classifier would have produced** if H1/H2/H3 hypotheses were applied:
   - Would aligned be True or False?
   - Would the trade have been blocked (contra → NOT OPEN) or sized down?

### 5.1 Specific focus on entries 4-6 (14:37 UTC, fwd_30m = -18.45 pts)

These 6 entries were the primary damage source (per INVEST-01 §7.1). For each:

- What iceberg event triggered aligned=True?
- Was the iceberg ASK side at high above session mean (absorption pattern)?
- Does corrected classifier (with H1/H2 applied) give aligned=False?

---

## 6. Phase 5 — Verdict (15 min)

Classify the V4 `aligned` classifier into ONE of:

- **VERDICT_A — Globally valid.** aligned=True has positive d > 0.2 across all regimes; classifier works as-is. → No change needed. (HIGHLY UNLIKELY given incident.)

- **VERDICT_B — Regime-dependent.** Classifier works in some regimes (e.g., TREND with trend-aligned icebergs) but fails in others (e.g., absorption at extreme). → Fix = add regime/context gates to classifier.

- **VERDICT_C — Missing extreme-vs-level differentiation.** Classifier fails systematically when iceberg is at session/structural extreme vs mid-level. → Fix = add "at_extreme" detector before aligned flag.

- **VERDICT_D — Direction-asymmetric.** Classifier works better for one direction (e.g., SHORT aligned works, LONG aligned fails). → Fix = direction-specific logic.

- **VERDICT_E — Classifier is fundamentally misconceived.** Current logic (direction + side) doesn't match literature (which says absorption at extreme = reversal, absorption at level = continuation). → Fix = redesign per 3-step protocol (Exhaustion + Absorption + Initiative).

- **VERDICT_F — Other** (specify).

---

## 7. Phase 6 — Output for Fix Design (15 min)

Based on verdict, produce **one** of:

### 7.1 If VERDICT B/C/D — patch-style fix spec

```yaml
current_logic: <from §2.3>
failure_conditions: <which H1/H2/H3 replicated empirically>
proposed_gate_additions:
  - condition: <predicate>
    action: <set aligned=False instead of True>
    evidence: <which Phase 3 table + Cohen's d + CI>
expected_impact:
  - entries_blocked_if_applied_to_history: <N>
  - pnl_delta_if_applied_to_incident: <X pts>
```

### 7.2 If VERDICT E — redesign spec outline

```yaml
current_logic_rejected: <why>
literature_alignment: <Bookmap 3-step protocol Exhaustion→Absorption→Initiative>
proposed_redesign:
  - new_inputs: <list>
  - new_logic: <plain English>
  - detection_checks: <extreme_detector, absorption_detector, initiative_detector>
validation_required_before_deploy:
  - historical_backtest_scope
  - shadow_mode_duration
  - success_criteria_per_framework_v1.1
```

### 7.3 If VERDICT A — no change, document why

Report with evidence that current classifier is correct and INVEST-01 §7.1 finding was situational (unlikely but honest verdict required if data shows it).

### 7.4 Blocked entries counterfactual

For whichever verdict: compute "if the proposed fix were applied retroactively, how many historical entries would have been blocked/modified? What is the estimated PnL delta across the 64 real trades (trades.csv) + 486 historic (entry_quality_dataset)?"

---

## 8. Final Report Structure

`INVEST_03_REPORT.md`:

```markdown
# INVEST-03 — V4 Iceberg aligned Classifier Regime-Segmented Investigation

## 1. Discovery — current aligned logic (§2)
## 2. L2 coverage verification + iceberg inventory (§3.2-3.3)
## 3. Regime-segmented backtest tables (§4.2) — PRIMARY RESULT
## 4. Hypothesis-specific segmentation H1/H2/H3 (§4.3)
## 5. Statistical rigor — CI, Cohen's d, walk-forward (§4.4)
## 6. 2026-04-20 incident counterfactual (§5)
## 7. VERDICT (§6)
## 8. Fix design spec (§7.1 or §7.2)
## 9. Blocked entries counterfactual — historical PnL delta (§7.4)
## 10. System state
```

---

## 9. System State

- Zero writes outside sprint directory
- Capture PIDs 2512, 8248, 11740 intact
- Service PID 4516 untouched
- No git operations
- No service restart
- No touches to `live/`, `config/`, `data/` (source)

---

## 10. Communication

Post to Barbara: §7 VERDICT letter + §7.1-7.2 fix spec summary + §7.4 expected PnL impact + any UNKNOWN flags.

**Begin when Barbara gives green light. Stop and ask if:**
- Discovery §2 cannot locate canonical `aligned` assignment
- L2 coverage has unexpected gaps in Jul 2025 → today range
- Iceberg JSONL schema differs from expected
- N<100 per regime cell despite 10 months of data (suggests data integrity issue)

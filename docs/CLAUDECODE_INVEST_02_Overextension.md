# CLAUDECODE PROMPT — INVEST-02: Overextension Reversal Regime-Segmented Investigation

**Sprint ID:** `sprint_invest_02_overextension_20260420`
**Authorization:** Barbara ratified 2026-04-20
**Type:** **READ-ONLY** — zero code edits, zero config edits, zero restarts, zero service touches
**Duration target:** 2-3 hours
**Output:** `C:\FluxQuantumAI\sprints\sprint_invest_02_overextension_20260420\INVEST_02_REPORT.md`
**Framework reference:** `FRAMEWORK_DataDriven_Calibration_APEX_v1.1.md`
**Design Doc context:** `DESIGN_DOC_Strategy_Layer_Architecture_v3.md` §5 (playbooks)
**Parallel to:** INVEST-01 (CAL-03 `delta_4h_inverted_fix`) — can run concurrent or sequential.

---

## 0. Why

The OVEREXTENSION branch in `event_processor._resolve_direction` (strategy_mode=="TRENDING", tc_mode=="SKIP" via Sprint 9 path, or legacy else fallback) emits counter-trend reversal signals when price extends > `overextension_atr_mult` × ATR beyond a liquidity level. 2026-04-20 incident: this branch contributed directly to 8 LONG signals in Phase E Markdown.

**IMPORTANT framing:** This investigation does NOT decide "keep or disable". The Strategy Layer (Design Doc v3) will either:
- Integrate overextension as conditional activation inside a specific playbook (e.g., "Failed Continuation" in future scope), OR
- Exclude from MVP if data shows no regime where it's empirically profitable.

**Question to answer:**

> In which Wyckoff phase + ATS regime combinations does overextension-reversal produce positive expected value? Is `overextension_atr_mult=1.5` the empirically optimal multiplier, or is the value itself unvalidated?

Output: evidence-based conditional specification for Nível 2 — inclusion/exclusion and calibration targets per playbook, per framework v1.1.

---

## 1. Hard Limits

1. **READ-ONLY.** Writes only to sprint output directory.
2. **No new thresholds invented.** Investigate existing mechanism.
3. **Regime tagging via EXISTING v5 parquet columns.** No new classifiers.
4. **Stop and ask Barbara** if overextension origin/rationale cannot be located.
5. Flag unknowns explicitly.

---

## 2. Phase 1 — Discovery (30 min)

### 2.1 Search for overextension origin documentation

```powershell
Get-ChildItem "C:\FluxQuantumAI\sprints","C:\FluxQuantumAI\reports","C:\FluxQuantumAI\docs","C:\FluxQuantumAI\live","C:\FluxQuantumAI" -Recurse -Include "*.md","*.txt","*.json","*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "overextension|overext|atr_mult|1\.5.*ATR|OVEREXT|counter.trend|reversal_atr|_detect_local_exhaustion" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" } |
    Out-File "C:\FluxQuantumAI\sprints\sprint_invest_02_overextension_20260420\discovery_grep.txt"
```

### 2.2 Report per match

Verbatim context ±10 lines, file mtime, linked backtests/reports.

### 2.3 Code path reconstruction

From memory + CROSS_REF_REPORT + REGIME_FLIP_FORENSIC, the 2 entry-decision branches are:
- `event_processor.py:3500-3535` (Sprint 9 path)
- `event_processor.py:3536-3573` (legacy else fallback)

For each branch, report verbatim:
- Full conditional predicate (what triggers it)
- Direction assigned (LONG when TRENDING_DN + liq_bot + overext; SHORT when TRENDING_UP + liq_top + overext)
- Any secondary conditions (iceberg alignment, score thresholds, etc.)
- Logging emitted

### 2.4 Answer 7 questions (or flag UNKNOWN)

- **Q1 Origin date:** When was OVEREXTENSION branch added? Which sprint/commit?
- **Q2 Rationale:** Why 1.5× ATR? Was a multiplier swept or is 1.5 a convention?
- **Q3 Dataset:** Which data validated the 1.5 value (if any)?
- **Q4 Metric:** Success criterion when calibrated (win rate? P&L? Sharpe?)
- **Q5 Both directions tested:** Did validation cover both TRENDING_UP→SHORT and TRENDING_DN→LONG equally, or was one direction extrapolated?
- **Q6 Regime stratification:** Was validation done per regime (RANGE vs TREND) or pooled?
- **Q7 Sprint 9 vs legacy:** When Sprint 9 added its path, was the effectiveness compared against legacy? Both kept intentionally?

If any Q unanswerable → **UNKNOWN — requires Barbara**.

---

## 3. Phase 2 — Regime Tagging of Historical Data (45 min)

### 3.1 Data sources

- `C:\data\processed\gc_ats_features_v5.parquet` (M30, regime features + `entry_trigger`, `entry_long/short`)
- `C:\data\processed\gc_ohlcv_l2_joined.parquet` (M1 OHLCV for forward returns)
- `C:\data\iceberg\iceberg__*.jsonl` (iceberg events for alignment check)

### 3.2 Ad-hoc regime classifier (same as INVEST-01 for cross-comparison)

```
IF m30_in_contraction == True AND weekly_aligned == False:
    regime = "RANGE"
ELIF m30_in_contraction == False AND weekly_aligned == True AND daily_trend in ("long", "short"):
    regime = "TREND"
ELSE:
    regime = "TRANSITIONAL"
```

Direction-segmented TREND: TREND_UP / TREND_DN.

### 3.3 Identify historical overextension events

For each M30 bar in v5:
- Compute `price_vs_liq_top`: (close - m30_liq_top) / m30_atr
- Compute `price_vs_liq_bot`: (m30_liq_bot - close) / m30_atr

Flag `overextension_up`: `price_vs_liq_top > 1.5` AND `daily_trend == "long"` (TRENDING_UP context → system would emit SHORT)
Flag `overextension_dn`: `price_vs_liq_bot > 1.5` AND `daily_trend == "short"` (TRENDING_DN context → system would emit LONG)

### 3.4 Distribution

Report per regime:
- Count of `overextension_up` events
- Count of `overextension_dn` events
- Distribution across Sprint 8 training era + post-deploy + recent

---

## 4. Phase 3 — Segmented Backtest (60 min)

### 4.1 Outcome metric

For each overextension event (§3.3):

- If `overextension_up` (system would emit SHORT): forward return at 30/60/240/1440 min
  - **short_reversal_validated** = True if forward return < 0 (SHORT profitable)
- If `overextension_dn` (system would emit LONG): forward return at 30/60/240/1440 min
  - **long_reversal_validated** = True if forward return > 0 (LONG profitable)

### 4.2 Results table — PRIMARY output

Two tables (overextension_up → SHORT hypothesis, overextension_dn → LONG hypothesis):

| Regime | N events | fwd_30 mean | WR_30 | fwd_60 mean | WR_60 | fwd_4h mean | WR_4h | fwd_24h mean | WR_24h |
|---|---|---|---|---|---|---|---|---|---|
| RANGE | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| TREND_UP | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| TREND_DN | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| TRANSITIONAL | ... | ... | ... | ... | ... | ... | ... | ... | ... |

### 4.3 Multiplier sweep (answers Q2 empirically)

Besides the current 1.5× threshold, also compute forward returns for candidate multipliers:
- `overext_thr` ∈ {0.5, 1.0, 1.5, 2.0, 2.5, 3.0}

For each (regime × multiplier), produce the same 4-horizon return table.

**Purpose:** is 1.5 empirically optimal, or would 2.0 be better (stricter threshold, fewer signals but higher quality)?

### 4.4 Statistical rigor per framework v1.1 §A.3

Per (regime × horizon × multiplier):
- Bootstrap 95% CI (N=1000)
- Cohen's d vs baseline
- Interpretation rules same as INVEST-01 §4.3

### 4.5 Walk-forward split

Same 4 sub-periods as INVEST-01:
- 2020-01 → 2024-12
- 2025-01 → 2025-06
- 2025-07 → 2025-11
- 2025-12 → 2026-04

Sign stability check per regime × multiplier.

### 4.6 Sprint 9 vs legacy path comparison

If discovery §2.3 confirms 2 branches with different conditions, replay each path separately:
- What fraction of overextension events fire via Sprint 9 path vs legacy?
- Do the 2 paths have different forward-return distributions?
- Is one strictly better than the other?

---

## 5. Phase 4 — 2026-04-20 Incident Counterfactual (20 min)

Using 8 GO LONG timestamps from incident snapshot:

For each:
- Regime label at that M30 bar
- `overextension_dn` value at that tick (price vs liq_bot in ATR)
- Which path fired (Sprint 9 or legacy)?
- Historical fwd returns in same (regime × `overextension_dn` bucket): what was the base rate?

**Direct answer:** Was today's LONG counter-evidence or consistent with historical outcomes in this regime?

---

## 6. Phase 5 — Verdict (15 min)

Classify overextension reversal mechanism:

- **VERDICT_A — Validated across all regimes.** Both SHORT (in TREND_UP) and LONG (in TREND_DN) reversals show positive expected value + significance + walk-forward stability. → Strategy Selector Nível 2: condition activates in both Trend Pullback Continuation playbook (as counter-direction exit/warning) and possibly a dedicated "Failed Continuation" playbook.

- **VERDICT_B — Validated in RANGE only.** Overextension-reversal works only when phase is actually Phase B/C mislabeled as TRENDING. → Strategy Selector Nível 2: condition activates only in Range Reversal playbook (§5.2) as a sub-condition of the reversal logic. Excluded from Phase E playbooks.

- **VERDICT_C — Asymmetric.** SHORT direction works in TREND_UP but LONG in TREND_DN doesn't (or vice versa). → Strategy Selector Nível 2: asymmetric activation, direction-specific.

- **VERDICT_D — 1.5 multiplier suboptimal.** A different multiplier (e.g., 2.5) shows clearly better returns. → Recommend re-calibration sprint with optimal multiplier per regime.

- **VERDICT_E — Era-dependent.** Validated in specific training era but flips across walk-forward windows. → Unstable signal, exclude from MVP.

- **VERDICT_F — Never validated data-driven.** Origin documentation shows 1.5 is a convention/magic number with no regime-segmented validation. → Exclude from MVP until proper calibration.

- **VERDICT_G — Other** (specify).

---

## 7. Phase 6 — Output for Nível 2 Integration (15 min)

**Deliverable:**

```markdown
## 7.1 Selector activation specification

Based on VERDICT_<X>:

Option-a (if VERDICT A/B/C): overextension as playbook sub-condition
  playbook_scope: [<which playbooks>]
  phase_scope: [<which phases>]
  regime_scope: [<which regimes>]
  direction_rule: <how direction derived>
  multiplier: <empirical optimal from §4.3>
  confidence_contribution: <how it modifies playbook confidence>

Option-b (if VERDICT D/E/F): exclusion
  exclusion_reason: <specific — "no walk-forward stability" OR "multiplier never calibrated" OR "1.5 is convention">
  recalibration_scope_if_reconsidered: <what Nível 2 calibration sprint would need>
```

**Also:** explicit comparison table — current rule (`overext > 1.5` universal, no phase gate) vs recommended conditional activation — showing fwd-return delta per regime.

---

## 8. Final Report Structure

`INVEST_02_REPORT.md`:

```markdown
# INVEST-02 — Overextension Reversal Regime-Segmented Investigation

## 1. Discovery findings + code path reconstruction (§2)
## 2. Origin reconstruction (Q1-Q7 answered or UNKNOWN)
## 3. Regime distribution of overextension events (§3.4)
## 4. Segmented forward-return tables (§4.2)
## 5. Multiplier sweep (§4.3)
## 6. Statistical rigor — CI, Cohen's d, walk-forward (§4.4-4.5)
## 7. Sprint 9 vs legacy path comparison (§4.6)
## 8. 2026-04-20 incident counterfactual (§5)
## 9. VERDICT (§6)
## 10. Selector-ready spec (§7)
## 11. System state
```

---

## 9. System State

- Zero writes outside sprint directory
- Capture PIDs 2512, 8248, 11740 intact
- Service PID 4516 untouched
- No git operations
- No service restart

---

## 10. Communication

Post to Barbara: §9 VERDICT letter + §10 activation spec summary + any UNKNOWN flags.

**Begin when Barbara gives green light.**

---

## 11. Coordination with INVEST-01

INVEST-01 and INVEST-02 can run in parallel (different data slices, no dependency) OR sequential. Both READ-ONLY, both use same regime classifier (§3.2 identical in both prompts for cross-comparability).

Shared outputs both docs can reference:
- Regime distribution across historical periods (produce once, reference both reports)
- Walk-forward sub-period definitions (same 4 periods)

If running sequential, INVEST-01 first (delta_4h is more established in literature — clearer prior), INVEST-02 second (overextension has less literature grounding, more open).

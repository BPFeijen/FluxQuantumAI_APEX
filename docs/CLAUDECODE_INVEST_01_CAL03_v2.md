# CLAUDECODE PROMPT — INVEST-01: CAL-03 `delta_4h_inverted_fix` Regime-Segmented Investigation

**Sprint ID:** `sprint_invest_01_cal03_20260420`
**Authorization:** Barbara ratified 2026-04-20
**Type:** **READ-ONLY** — zero code edits, zero config edits, zero restarts, zero service touches
**Duration target:** 2-3 hours
**Output:** `C:\FluxQuantumAI\sprints\sprint_invest_01_cal03_20260420\INVEST_01_REPORT.md`
**Framework reference:** `FRAMEWORK_DataDriven_Calibration_APEX_v1.1.md` (walk-forward + regime awareness)
**Design Doc context:** `DESIGN_DOC_Strategy_Layer_Architecture_v3.md` §5.2 Range Reversal playbook

---

## 0. Why

`delta_4h_inverted_fix=true` (Sprint 8 CAL-03) reinterpreta delta_4h extremos como "exhaustion → reversal". Hoje (2026-04-20 NY open) applied Range-Bound exhaustion logic in Phase E Markdown → 8 counter-trend LONG signals contributing to Barbara's capital loss.

**IMPORTANT framing:** This investigation does NOT decide "keep or disable". The Strategy Layer (Design Doc v3) will integrate `delta_4h_inverted_fix` as a **conditional activation** inside the **Range Reversal playbook** (§5.2). The question is:

> For which Wyckoff phase + ATS regime combinations is `delta_4h_inverted_fix` empirically valid as an exhaustion-reversal signal, and for which is it damaging?

Output: evidence-based conditional specification for Nível 2 playbook calibration — which phase/regime gates the inverted_fix condition activates, per framework v1.1 methodology.

---

## 1. Hard Limits

1. **READ-ONLY.** Writes only to sprint output directory.
2. **No new thresholds invented.** Investigate existing parameter's empirical validity.
3. **Regime tagging via EXISTING parquet columns** (v5 parquet: `m30_in_contraction`, `daily_atr_regime`, `m30_momentum_stacking`, `m30_ats_trend`, `daily_trend`, `weekly_aligned`). Do NOT invent new classifiers.
4. **Stop and ask Barbara** if CAL-03 original documentation cannot be located — do NOT assume rationale.
5. Flag unknowns explicitly. No speculation.

---

## 2. Phase 1 — Discovery (30 min)

### 2.1 Search for CAL-03 original documentation

```powershell
# Search sprints, reports, docs, code comments
Get-ChildItem "C:\FluxQuantumAI\sprints","C:\FluxQuantumAI\reports","C:\FluxQuantumAI\docs","C:\FluxQuantumAI\live","C:\FluxQuantumAI" -Recurse -Include "*.md","*.txt","*.json","*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "CAL-?03|CAL_?03|delta_4h_inverted_fix|inverted_fix|seller exhaustion|buyer exhaustion|exhaustion_low|exhaustion_high" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" } |
    Out-File "C:\FluxQuantumAI\sprints\sprint_invest_01_cal03_20260420\discovery_grep.txt"
```

### 2.2 Extract from each match found

Report verbatim per match:
- File + line + ±10 lines context
- Date (file mtime + date references in content)
- Linked artifacts (backtest CSV? report MD? commit hash?)

### 2.3 Answer 7 questions from CAL-03 docs (or flag UNKNOWN)

- **Q1 Date:** When was CAL-03 calibrated?
- **Q2 Dataset:** Which parquet/date range used?
- **Q3 Metric:** Success criterion (E[fwd] over N minutes? win rate? Sharpe?)
- **Q4 Threshold derivation:** How were -1050 / -800 / +3000 derived? Percentile? Optimization?
- **Q5 Both sides tested:** Did backtest cover `delta_4h < -1050` (seller exhaustion → LONG) AND `delta_4h > +3000` (buyer exhaustion → SHORT)? Or only one side, extrapolated?
- **Q6 Regime stratification:** Was result stratified (Range/Trend) or single global number?
- **Q7 Walk-forward:** Was there out-of-sample hold-out? Per framework v1.1 §A.2.

If any Q unanswerable → flag **UNKNOWN — requires Barbara** explicitly.

---

## 3. Phase 2 — Regime Tagging of Historical Data (45 min)

### 3.1 Data sources

- `C:\data\processed\gc_ats_features_v5.parquet` (73k M30 rows, 2020 → 2026-03-31)
- `C:\data\processed\calibration_dataset_full.parquet` (2.19M M1 rows with `rolling_delta_4h`, 2020 → 2026-04-07)
- `C:\data\processed\gc_ohlcv_l2_joined.parquet` (2.2M M1 OHLCV, 2020 → live)

### 3.2 Ad-hoc regime classifier (EXISTING columns only)

For each M30 bar in v5:

```
IF m30_in_contraction == True AND weekly_aligned == False:
    regime = "RANGE"
ELIF m30_in_contraction == False AND weekly_aligned == True AND daily_trend in ("long", "short"):
    regime = "TREND"
ELSE:
    regime = "TRANSITIONAL"
```

Also produce **direction-segmented TREND:**
- `TREND_UP`: regime=TREND AND daily_trend=="long"
- `TREND_DN`: regime=TREND AND daily_trend=="short"

### 3.3 Distribution reports

Report distribution of M30 bars across regimes for:
- **Full period** 2020-01 → 2026-03-31
- **Sprint 8 training era** (likely 2025-Jul → 2025-Nov per memory; adjust if discovery §2 shows different)
- **Post-CAL-03-deploy** 2025-Dec → 2026-Mar
- **Recent live** 2026-Apr

### 3.4 delta_4h extremes per regime

Using `calibration_dataset_full.parquet` + join to regime label:

For each M1 bar, compute:
- `delta_4h_extreme_neg`: True if `rolling_delta_4h < -1050`
- `delta_4h_extreme_pos`: True if `rolling_delta_4h > +3000`

Report distribution of extremes per regime (RANGE / TREND_UP / TREND_DN / TRANSITIONAL).

---

## 4. Phase 3 — Segmented Backtest (60 min)

### 4.1 Outcome metric (triple-barrier light)

For each M1 bar where `rolling_delta_4h < -1050` (seller exhaustion candidate):
- Forward return at 30 min, 60 min, 4h, 24h (from OHLCV `close` at t+N)
- `bullish_reversal_validated`: True if forward return > 0 (i.e., "SUPPORTS LONG" thesis correct)

Symmetric for `rolling_delta_4h > +3000` (buyer exhaustion → SHORT validation).

### 4.2 Results table — PRIMARY output

Two tables (seller exhaustion, buyer exhaustion):

| Regime | N samples | fwd_30min mean | WR_30 | fwd_60min mean | WR_60 | fwd_4h mean | WR_4h | fwd_24h mean | WR_24h |
|---|---|---|---|---|---|---|---|---|---|
| RANGE | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| TREND_UP | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| TREND_DN | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| TRANSITIONAL | ... | ... | ... | ... | ... | ... | ... | ... | ... |

### 4.3 Statistical rigor per framework v1.1 §A.3

For each regime × horizon cell:
- **Bootstrap 95% CI** on mean forward return (N=1000 resamples)
- **Cohen's d** vs baseline (regime's overall mean forward return)
- **Interpretation:**
  - CI includes 0 → signal indistinguishable from noise in this regime
  - d < 0.3 → effect weak even if statistically significant
  - d ≥ 0.3 AND CI strictly positive for bullish_reversal → signal valid in this regime

### 4.4 Walk-forward split (framework v1.1 §A.2)

Compute metrics per sub-period:
- 2020-01 → 2024-12 (historical)
- 2025-01 → 2025-06
- 2025-07 → 2025-11 (Sprint 8 training era)
- 2025-12 → 2026-04 (post-deploy)

**If sign of mean forward return flips between sub-periods in same regime → signal is regime + era dependent, not stable.**

---

## 5. Phase 4 — 2026-04-20 Incident Counterfactual (20 min)

Using 8 GO LONG timestamps from `C:\FluxQuantumAI\sprints\INCIDENT_20260420_LONG_DURING_DROP\20260420_145845\decision_log_last60min.jsonl`:

For each of the 8 timestamps:
- Compute regime label at that M30 bar (from §3.2)
- Report `rolling_delta_4h` value
- Historical behavior: in same regime, what was mean forward return when delta_4h < -1050?

**Directly answers:** Was today's LONG signal statistically supported by historical data, or counter-evidence?

---

## 6. Phase 5 — Verdict (15 min)

Classify CAL-03 `delta_4h_inverted_fix` into ONE of:

- **VERDICT_A — Validated globally (rare).** Mean forward return of seller exhaustion is positive + significant across ALL regimes in ALL walk-forward windows. Today was statistical outlier. → Strategy Selector Nível 2: condition activates in ALL playbooks that fit Range + Trend contexts.

- **VERDICT_B — Validated in RANGE only.** Positive forward return + significance in RANGE regime, zero or negative in TREND regimes. → Strategy Selector Nível 2: condition activates ONLY in Range Reversal playbook (§5.2). Excluded from Trend Pullback, Breakout, Judas Swing.

- **VERDICT_C — Asymmetric: buyer exhaustion (+d4h) validated, seller exhaustion (-d4h) not.** Only positive-side works historically. → Strategy Selector Nível 2: condition activates only for SHORT entries in Range Distribution, never for LONG entries.

- **VERDICT_D — Era-dependent.** Validated in specific training era (2025-Jul-Nov) but flips sign in other eras. → Not a stable signal; exclude from MVP Strategy Selector.

- **VERDICT_E — Never validated data-driven.** CAL-03 documentation not found OR documentation shows no regime-segmented validation. → Exclude from MVP Strategy Selector until proper calibration sprint.

- **VERDICT_F — Other** (specify).

---

## 7. Phase 6 — Output for Nível 2 Integration (15 min)

**Deliverable format:**

```markdown
## 7.1 Selector-ready activation condition specification

Based on VERDICT_<X>, the Range Reversal playbook (§5.2 Design Doc v3) activation_conditions should include:

condition_name: "delta_4h_exhaustion_extreme"
condition_predicate: <exact predicate in pseudo-code>
phase_scope: [phase_b_*, phase_c_*]  # or whatever verdict dictates
regime_scope: [range_bound]            # or whatever
direction_effect: <how it modifies LONG/SHORT direction selection>
confidence_contribution: <how it adjusts playbook confidence score>

OR

exclusion_reason: <if VERDICT_D, E, F — why excluded>
```

**Also produce: calibration targets for Nível 2** (ranges that Nível 2 calibration sprint should optimize, using framework v1.1).

---

## 8. Final Report Structure

`INVEST_01_REPORT.md`:

```markdown
# INVEST-01 — CAL-03 delta_4h_inverted_fix Regime-Segmented Investigation

## 1. Discovery findings (§2)
## 2. Calibration methodology reconstruction (Q1-Q7 answered or UNKNOWN)
## 3. Regime distribution (§3.3)
## 4. delta_4h extremes per regime (§3.4)
## 5. Segmented forward-return tables (§4.2)
## 6. Statistical rigor — CI, Cohen's d, walk-forward (§4.3-4.4)
## 7. 2026-04-20 incident counterfactual (§5)
## 8. VERDICT (§6)
## 9. Selector-ready activation spec (§7)
## 10. System state during investigation
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

Post to Barbara: §8 VERDICT letter + §9 one-line activation spec summary + any UNKNOWN flags.

**Begin when Barbara gives green light. Stop and ask if Phase 1 discovery fails to find CAL-03 docs, or if regime distribution is unexpected.**

# CLAUDECODE PROMPT — CLEAN-UP 2.5: CAL-03 `delta_4h_inverted_fix` Historical Context Investigation

**Sprint ID:** `sprint_cleanup_2_5_cal03_context_20260420`
**Authorization:** Barbara ratified 2026-04-20 after literature review
**Type:** **READ-ONLY investigation + regime-segmented backtest** — zero code edits, zero config edits, zero restarts
**Duration target:** 2-3 hours
**Output:** 1 file (`C:\FluxQuantumAI\sprints\sprint_cleanup_2_5_cal03_context_20260420\CAL03_CONTEXT_REPORT.md`)
**Blocking:** Calibration sprint + Sprint C Phase 2b resumption
**Framework reference:** `FRAMEWORK_DataDriven_Calibration_APEX_v1.1.md` (walk-forward + regime awareness)

---

## 0. Why

Literature review (Wyckoff Villahermosa, ATS Basic Strategies, Bookmap Order Flow) establishes:

- **Selling/Buying Exhaustion** is a **Phase A Wyckoff event** — fim de trend, início de Accumulation/Distribution range. NÃO é trend-active signal.
- **ATS differentiates 2 strategies:** Range-Bound (exhaustion→reversal valid) vs Trending (directional bias strict, no counter-trend).
- **Bookmap 3-step reversal protocol:** Exhaustion → Absorption → Initiative. All 3 required.
- **FitGap `APEX_GC_Methodology_FitGap_v2.md` already identified:** "Phase Detection: ❌ GAP CRÍTICO. Strategy Adaptation: ❌ GAP CRÍTICO. Sistema opera sempre da mesma forma."

Today's incident (14:30-14:56 UTC, 8 LONG signals during -25pt drop):
- `daily_trend="short"`, `phase="TREND"`, `strategy_mode="TRENDING_DN"` — clearly TRENDING context
- `delta_4h_inverted_fix=true` applied Range-Bound exhaustion logic (SUPPORTS LONG +2) in Trending context
- Result: counter-trend LONG signals → Barbara's capital loss on manual execution

**Hypothesis to test:** CAL-03 finding ("delta_4h extreme → exhaustion → reversal") is conceptually valid but was applied WITHOUT phase/regime conditional. Investigation must find:

1. Where is CAL-03 documented (original backtest, data used, regime of that data)
2. Whether the fix was validated segmented by regime or as single global rule
3. Whether inverted_fix damage in Trending periods outweighs benefit in Range periods

Output: evidence-based recommendation for Barbara — keep/modify/disable inverted_fix.

---

## 1. Hard Limits

1. **READ-ONLY.** No writes to `live/`, `config/`, `logs/`, `data/`.
2. **No new thresholds invented.** This is investigation of an existing parameter. Do not calibrate anything else.
3. **Regime tagging for segmentation uses EXISTING parquet columns** (`gc_ats_features_v5.parquet` has `m30_in_contraction`, `daily_atr_regime`, `m30_momentum_stacking`, `m30_ats_trend`). Do NOT invent new regime detectors.
4. **Stop and ask** if CAL-03 original documentation cannot be located — do NOT proceed with assumed rationale.
5. No speculation beyond data. If a question can't be answered from files, flag it as "UNKNOWN - requires Barbara".

---

## 2. Phase 1 — Discovery: Find CAL-03 original documentation

### 2.1 — Search locations

Search recursively for CAL-03 references:

```powershell
# Search in sprint directories
Get-ChildItem "C:\FluxQuantumAI\sprints" -Recurse -Include "*.md","*.txt","*.json","*.py" |
    Select-String -Pattern "CAL-?03|CAL_?03|delta_4h_inverted_fix|inverted_fix|seller exhaustion|buyer exhaustion" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }

# Search in reports, docs, backups
Get-ChildItem "C:\FluxQuantumAI\reports","C:\FluxQuantumAI\docs" -Recurse -ErrorAction SilentlyContinue |
    Select-String -Pattern "CAL-?03|delta_4h_inverted_fix" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }

# Search in live code for comments
Get-ChildItem "C:\FluxQuantumAI\live","C:\FluxQuantumAI" -File -Include "*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "CAL-?03|delta_4h_inverted_fix|CAL03" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }
```

### 2.2 — For each match found

Report verbatim:
- File path + line number
- Surrounding context (±10 lines)
- Date (file mtime + any date references in content)

### 2.3 — Extract from documentation

From CAL-03 docs, answer:

- **Date of calibration:** When was CAL-03 run?
- **Dataset used:** Which parquet/date range?
- **Metric:** What was the success criterion? (E[fwd] over forward N minutes/bars?)
- **Threshold derived:** How was -1050/-800/+3000 derived? From which percentile / optimization?
- **Both sides tested?** Did the backtest cover `delta_4h < -1050` (seller exhaustion → LONG) AND `delta_4h > +3000` (buyer exhaustion → SHORT)? Or only one?
- **Regime stratification:** Was the backtest result stratified by regime (Range/Trend) or single global number?
- **Sample size:** N of instances of delta_4h < -1050 in the training period?
- **Validation set:** Was there walk-forward / hold-out?

If any of these questions cannot be answered from docs → flag as **UNKNOWN** explicitly.

---

## 3. Phase 2 — Regime tagging of historical data

### 3.1 — Data source

`C:\data\processed\gc_ats_features_v5.parquet` (73,120 M30 rows, 2020 → 2026-03-31).

Columns available for ad-hoc regime tagging:
- `m30_in_contraction` (bool) — Contraction flag
- `daily_atr_regime` (string) — e.g., "low", "high", "normal" (verify values via unique counts)
- `m30_momentum_stacking` (likely numeric or categorical)
- `m30_ats_trend` (numeric or categorical — verify)
- `daily_trend` ("long"/"short"/"unknown")
- `weekly_trend`
- `weekly_aligned`
- `m30_in_contraction`

### 3.2 — Ad-hoc regime classifier (do NOT invent new, use existing fields)

For each M30 bar in historical data, classify into ONE of 3 regimes:

- **RANGE:** `m30_in_contraction == True` OR (`daily_trend == "unknown"` AND `weekly_aligned == False`)
- **TREND:** `m30_in_contraction == False` AND `weekly_aligned == True` AND `daily_trend in ("long", "short")`
- **TRANSITIONAL:** neither fully Range nor fully Trend

Report distribution: % of bars in each regime across:
- Full period 2020-2026
- Jul-Nov 2025 (when Sprint 8 likely trained)
- Dec 2025-Apr 2026 (live period)

**Purpose:** Understand if Sprint 8 training data was predominantly RANGE (which would support exhaustion→reversal validity) OR TREND (which would invalidate it).

### 3.3 — Cross-reference with `rolling_delta_4h`

Using `calibration_dataset_full.parquet` (has `rolling_delta_4h`):

For each M1 bar, compute:
- `delta_4h_extreme`: True if `rolling_delta_4h < -1050` OR `rolling_delta_4h > +3000`
- Regime label (joined from features_v5 at matching M30 ts)

Distribution: % of delta_4h_extreme bars in each regime.

**Expected if CAL-03 was correct globally:** uniform distribution of extremes across regimes.
**Expected if CAL-03 was regime-specific:** concentrated in RANGE regime.

---

## 4. Phase 3 — Segmented backtest of `inverted_fix` hypothesis

### 4.1 — Define outcome metric

For each M1 bar where `rolling_delta_4h < -1050` (seller exhaustion candidate):

- Forward return at 30min, 60min, 4h, 24h (from OHLCV future close)
- Direction: "bullish reversal" if forward return > 0 (SUPPORTS LONG validated)

Symmetric for `rolling_delta_4h > +3000` (buyer exhaustion candidate, forward return < 0 validates SHORT).

### 4.2 — Segmented results table

Produce this table for both sides:

| Regime | N samples | Forward 30min: mean return | Win rate | Forward 60min: mean | WR | Forward 4h: mean | WR | Forward 24h: mean | WR |
|---|---|---|---|---|---|---|---|---|---|

One table for `delta_4h < -1050` (LONG hypothesis), one for `delta_4h > +3000` (SHORT hypothesis). Both rendered for RANGE, TREND, TRANSITIONAL regimes.

### 4.3 — Statistical tests (per framework v1.1 §A.3)

For each regime-segment:
- Bootstrap 95% CI on mean forward return (N=1000 resamples)
- If CI crosses zero → signal not distinguishable from noise in that regime
- Cohen's d vs baseline (baseline = average forward return of regime overall)
- If d < 0.3 → weak signal even if significant

### 4.4 — Walk-forward split

Per framework v1.1, don't report single-window stats. Split period:
- 2020-01 → 2024-12 (training-equivalent)
- 2025-01 → 2025-06 (in-sample-equivalent)
- 2025-07 → 2025-11 (Sprint 8 training era)
- 2025-12 → 2026-04 (post-deploy period)

Compute metrics per sub-period. If inverted_fix validity flips between periods → regime-dependent signal (need conditional rule).

---

## 5. Phase 4 — Today's incident counterfactual

Using the 8 GO LONG timestamps from `decision_log_last60min.jsonl` (14:09-14:56 UTC):

For each:
- Compute `m30_in_contraction` + `m30_ats_trend` state at that time (from features_v5 or reconstructed from M30 boxes)
- Compute regime label (RANGE/TREND/TRANSITIONAL)
- Report: "In regime X, did `delta_4h < -1050` historically lead to positive forward return?"

**This directly answers: was today's LONG signal statistically supported or counter-evidence?**

---

## 6. Deliverable structure

`CAL03_CONTEXT_REPORT.md`:

```markdown
# CAL-03 `delta_4h_inverted_fix` Context Investigation

## 1. Original documentation findings (§2)
<verbatim quotes of any CAL-03 docs found>
<OR "UNKNOWN - not located" flag>

## 2. Calibration methodology reconstruction
- Date, dataset, metric, sample size, both-sides-tested, regime-stratified?
- If UNKNOWN for any, list here

## 3. Regime distribution of historical data
- Sprint 8 training era (Jul-Nov 2025): X% RANGE / Y% TREND / Z% TRANSITIONAL
- Full period 2020-2026: distribution
- Live period Dec 2025-Apr 2026: distribution

## 4. delta_4h extremes per regime
- % of delta_4h < -1050 events in each regime
- % of delta_4h > +3000 events in each regime

## 5. Forward return results (per regime)
<tables from §4.2>
<bootstrap CI, Cohen's d, walk-forward>

## 6. Today's incident counterfactual
<regime at 14:30 UTC + historical behavior of delta_4h < -1050 in that regime>

## 7. Verdict
ONE of:
- **VERDICT_A:** inverted_fix validated globally → continue as-is. Today was statistical outlier.
- **VERDICT_B:** inverted_fix valid ONLY in RANGE regime → needs conditional gate. Currently misfires in TREND.
- **VERDICT_C:** inverted_fix validated ONLY for +d4h (sell side) originally → -d4h extrapolation unsupported. Recommend revert to original block on -d4h.
- **VERDICT_D:** inverted_fix never validated data-driven → recommend disable until proper calibration.
- **VERDICT_E:** Other (describe)

## 8. Recommendation
- Concrete next sprint based on verdict
- Ordered list of actions
- No auto-apply — Barbara decides

## 9. System state
- Zero writes confirmed
- Capture PIDs intact
```

---

## 7. Communication

- Produce `CAL03_CONTEXT_REPORT.md`
- Post to Barbara: §7 verdict letter + §8 top-3 actions (5-10 lines)
- Do NOT propose code changes. Barbara decides.

**Begin when Barbara gives green light. Stop and ask if CAL-03 docs not found OR if regime tagging reveals unexpected distribution.**

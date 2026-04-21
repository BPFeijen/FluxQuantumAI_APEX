# CLAUDECODE PROMPT — CAL-CROSS-REF (Threshold Cross-Reference)

**Sprint ID:** `sprint_cal_cross_ref_20260420`
**Authorization:** Barbara ratified 2026-04-20
**Type:** **READ-ONLY cross-reference** — zero code, zero config, zero restarts
**Duration target:** 1-2 hours
**Output:** 1 file (`C:\FluxQuantumAI\sprints\sprint_cal_cross_ref_20260420\CROSS_REF_REPORT.md`)
**Prerequisite:** `C:\FluxQuantumAI\reports\APEX_THRESHOLDS_INVENTORY_20260419.md` (251 parameters, already exists)
**Prerequisite:** `C:\FluxQuantumAI\sprints\sprint_calibration_discovery_20260420\DATA_INVENTORY.md` (completed)

---

## 0. Why

Claude invented 18 thresholds this session (Sprint C v2 commit `074a482` + draft Sprint E). Suspect several duplicate existing parameters already catalogued in `APEX_THRESHOLDS_INVENTORY_20260419.md`. Before writing a calibration sprint, identify:

- Which of the 18 are **DUPLICATE** (same concept exists, possibly with different name, possibly disabled)
- Which are **GENUINELY NEW** (no equivalent in inventory)

This determines calibration scope. Fewer to calibrate = less risk.

---

## 1. Hard Limits

1. READ-ONLY. Zero writes to `live/`, `config/`, `data/`.
2. No calibration runs. No data analysis beyond reading parameter definitions.
3. No threshold value recommendations. Just mapping + classification.
4. Stop and ask if inventory file not found or schema differs from expected.

---

## 2. Task

For each of the 18 thresholds below, search `APEX_THRESHOLDS_INVENTORY_20260419.md` (251 parameters) and identify:

- **Exact duplicate:** same concept + same unit (e.g., `DEFENSIVE_EXIT_DELTA_4H_THRESHOLD` vs `delta_4h_long_block`)
- **Partial duplicate:** same concept, different implementation (e.g., enabled/disabled, different scope)
- **Genuinely new:** no equivalent found

### The 18 invented thresholds

**From Sprint C v2 commit `074a482` (LIVE in `level_detector.py`):**

| # | Name | Value | Purpose |
|---|---|---|---|
| 1 | `H4_CLOSE_PCT_BULL_THRESHOLD` | 0.75 | R_H4_1: H4 close in upper quartile → bullish |
| 2 | `H4_CLOSE_PCT_BEAR_THRESHOLD` | 0.25 | R_H4_4: H4 close in lower quartile → bearish |
| 3 | `H4_CONTINUATION_WINDOW` | 3 | R_H4_2/5: examine last N H4 candles |
| 4 | `H4_CONTINUATION_MIN_SAME` | 2 | R_H4_2/5: min same-color candles in window |
| 5 | `H4_CONF_STRONG` | 0.70 | Confidence when R_H4_1 AND R_H4_2 both fire |
| 6 | `H4_CONF_SINGLE` | 0.55 | Confidence when only one rule fires |
| 7 | `H4_CONF_JAC_CONFIRMED` | 0.95 | R_H4_3/6 via h4_jac_dir from parquet |
| 8 | `H4_MAX_STALENESS_HOURS_DEFAULT` | 6.0 | Parquet freshness threshold |

**From draft Sprint E prompt (NOT IN CODE):**

| # | Name | Value | Purpose |
|---|---|---|---|
| 9 | `D1_CLOSE_PCT_BULL_THRESHOLD` | 0.65 | D1 close position → bullish |
| 10 | `D1_CLOSE_PCT_BEAR_THRESHOLD` | 0.35 | D1 close position → bearish |
| 11 | `D1_CONF_STRONG` | 0.75 | D1 strong confidence |
| 12 | `D1_CONF_SINGLE` | 0.55 | D1 single-rule confidence |
| 13 | `PARTIAL_H4_FLIP_ATR_MULT` | 0.3 | Partial H4 bar divergence ≥ X × ATR → flip warning |
| 14 | `DIRECTION_COOLDOWN_MIN` | 10 | Block same-direction GO within X min |
| 15 | `DEFENSIVE_EXIT_MFE_GIVEBACK_PCT` | 0.40 | Close position when % of MFE peak given back |
| 16 | `DEFENSIVE_EXIT_MFE_MIN_ATR_MULT` | 1.5 | Min MFE (×ATR) before giveback rule applies |
| 17 | `DEFENSIVE_EXIT_DELTA_4H_THRESHOLD` | -800 | Exit LONG when delta_4h < X |
| 18 | `DEFENSIVE_EXIT_ICEBERG_PROXIMITY_ATR` | 0.5 | Exit when counter iceberg within X × ATR of MFE |

---

## 3. Search methodology

For each of the 18:

1. **Exact name search** in inventory table (column "Nome Técnico") — unlikely to match, names differ.
2. **Concept search** — read inventory entries and identify any parameter that controls the **same decision**:
   - Group H4/D1 candle thresholds: search for `h4`, `d1`, `daily`, `close`, `quartile`, `jac`
   - Group confidence: search for `conf`, `score`, `weight`
   - Group staleness: search for `staleness`, `freshness`, `age`, `max_age`, `hours`
   - Group direction cooldown: search for `cooldown`, `trade_cooldown`, `same_level`, `dedup`
   - Group MFE: search for `mfe`, `giveback`, `profit`, `trailing`
   - Group delta_4h: search for `delta_4h`, `short_block`, `long_block`, `exhaustion`, `resumption`
   - Group iceberg proximity: search for `iceberg`, `absorption`, `proximity`, `collision`, `zones`, `breaking_ice`
3. **Cross-check enabled/disabled state** — inventory notes which params are `null`/`false` (fail-open / disabled)

---

## 4. Output format

For each of the 18, produce a row in a table:

| Invented # | Invented name | Match class | Existing inventory entry (if any) | Inventory value | Enabled state | Notes |
|---|---|---|---|---|---|---|

**Match class values:**
- `EXACT_DUPLICATE` — same concept, same unit, exists and is active → **do not calibrate, use existing**
- `DUPLICATE_DISABLED` — same concept exists but disabled (`enabled=false` or `null`) → **do not calibrate, propose enablement instead**
- `PARTIAL_DUPLICATE` — related concept but not identical → flag for Barbara decision
- `NEW` — no equivalent found → **genuine calibration target**

---

## 5. Deliverable structure

```markdown
# CROSS_REF_REPORT — 2026-04-20

## 1. Summary
- Total invented: 18
- EXACT_DUPLICATE: X
- DUPLICATE_DISABLED: Y
- PARTIAL_DUPLICATE: Z
- NEW: W
- Calibration targets remaining: W + Z (Barbara decides on Z)

## 2. Full mapping table
<18 rows as per §4>

## 3. Actionable findings
### 3.1 Duplicates to remove from calibration scope
<list, with suggested replacement from inventory>

### 3.2 Disabled parameters worth enabling (pre-Sprint E activation)
<list + rationale; DO NOT enable, just propose>

### 3.3 Genuinely new parameters requiring calibration
<list>

### 3.4 Partial duplicates — Barbara decision required
<list + two-line explanation each>

## 4. Recommendation
<3-5 lines: what the calibration sprint actually needs to calibrate vs what can reuse existing>
```

---

## 6. Time budget

- Read inventory: 20 min
- Cross-reference 18 entries: 40 min
- Draft report: 30 min
- **Target: 90 min. Stop and report if exceeding 2h.**

---

## 7. Communication

Produce `CROSS_REF_REPORT.md`. Post to Barbara summary of §1 counts + §4 recommendation.

No further action. Next sprint (calibration) is defined by this output.

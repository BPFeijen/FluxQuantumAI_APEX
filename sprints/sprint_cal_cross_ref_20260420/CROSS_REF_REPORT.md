# CROSS_REF_REPORT — 2026-04-20

**Sprint:** sprint_cal_cross_ref_20260420
**Mode:** READ-ONLY cross-reference (zero writes to live code/config/data)
**Inputs:**
- `C:\FluxQuantumAI\reports\APEX_THRESHOLDS_INVENTORY_20260419.md` (251 params, 60.3KB, 454 lines)
- `C:\FluxQuantumAI\sprints\sprint_calibration_discovery_20260420\DATA_INVENTORY.md`

---

## 1. Summary

| Class | Count | Meaning |
|---|---|---|
| EXACT_DUPLICATE | **1** | Same concept, same unit, already active |
| DUPLICATE (diff value) | **1** | Same concept + unit but different value |
| DUPLICATE_DISABLED | **1** | Same concept exists but fail-open/disabled today |
| PARTIAL_DUPLICATE | **7** | Related concept, different scope/unit — needs Barbara call |
| NEW | **8** | No equivalent in inventory |
| **Total** | **18** | |

**Calibration scope reduction:**
- **Genuinely NEW to calibrate:** 8
- **Reuse / activate existing:** 3 (1 exact + 1 disabled + 1 diff value)
- **Barbara-call:** 7 partials
- **Minimum calibration targets:** 8 (NEW) + whichever PARTIAL are confirmed as genuinely distinct = between **8 and 15**, down from 18

---

## 2. Full mapping table

| # | Invented name | Invented value | Match class | Existing inventory entry | Inventory value | Enabled? | Notes |
|---|---|---|---|---|---|---|---|
| 1 | `H4_CLOSE_PCT_BULL_THRESHOLD` | 0.75 | **PARTIAL_DUPLICATE** | #64 `trend_cont_close_near_extreme_pct` | 0.7 | ✅ | Same idea "close near extreme of range" but applied to M30 trend-continuation barre, not H4 bias. Conceptually distinct scope but shared heuristic family. |
| 2 | `H4_CLOSE_PCT_BEAR_THRESHOLD` | 0.25 | **PARTIAL_DUPLICATE** | #64 (same as above) | 0.7 | ✅ | Symmetric. No explicit bear-side close threshold in inventory. Implicit: inventory uses `close_near_extreme` generically; bear side derives as `1 - 0.7 = 0.3`. |
| 3 | `H4_CONTINUATION_WINDOW` | 3 | **NEW** | (closest: #40 `phase_value_stacking_min_boxes`=3, #234 `H4_HYSTERESIS_BARS`=2) | — | — | No existing H4-specific N-bar window for body/close continuation. #234 is hysteresis (different semantics). |
| 4 | `H4_CONTINUATION_MIN_SAME` | 2 | **NEW** | (none) | — | — | No existing "min same-color" continuation rule in inventory. |
| 5 | `H4_CONF_STRONG` | 0.70 | **NEW** | (collides numerically with #237 `SCORE_BLOCK_ENTRY`=0.70 [news], #244 `reduced`=0.7 [news], #64 `close_near_extreme`=0.7 [trend_cont]) | — | — | No existing H4 bias confidence. Numerical coincidence with news/trend-cont but entirely different decision surface. |
| 6 | `H4_CONF_SINGLE` | 0.55 | **NEW** | (none) | — | — | Unique value in inventory — no clash. New confidence scalar. |
| 7 | `H4_CONF_JAC_CONFIRMED` | 0.95 | **NEW** | (closest: #238 `SCORE_EXIT_ALL`=0.90, #245 `blocked`=0.9) | — | — | Distinct: JAC-derived H4 bias confidence. Different decision (H4 gate vs news exit). |
| 8 | `H4_MAX_STALENESS_HOURS_DEFAULT` | 6.0 | **DUPLICATE (diff value)** | **#235 `H4_STALE_HOURS`** `d1_h4_updater.py:91` | **8.0** | ✅ | **Same concept, same unit, same file (d1_h4_updater.py)** — existing threshold for H4 parquet freshness. My 6.0 is tighter. Resolution: use existing #235 = 8.0 OR align Sprint C v2 to use `H4_STALE_HOURS` import. Calibration target becomes single value (8.0 vs 6.0), not two. |
| 9 | `D1_CLOSE_PCT_BULL_THRESHOLD` | 0.65 | **PARTIAL_DUPLICATE** | #64 `trend_cont_close_near_extreme_pct` | 0.7 | ✅ | Same family as H4. D1 specific — no dedicated D1 close rule in inventory. Need separate D1 calibration unless Barbara decides to share. |
| 10 | `D1_CLOSE_PCT_BEAR_THRESHOLD` | 0.35 | **PARTIAL_DUPLICATE** | #64 (symmetric) | 0.7 | ✅ | Symmetric of #9. |
| 11 | `D1_CONF_STRONG` | 0.75 | **NEW** | (none) | — | — | No D1 bias confidence exists. |
| 12 | `D1_CONF_SINGLE` | 0.55 | **NEW** | (matches invented #6 `H4_CONF_SINGLE`=0.55 — intentional consistency) | — | — | Distinct from H4 via decision scope. |
| 13 | `PARTIAL_H4_FLIP_ATR_MULT` | 0.3 | **PARTIAL_DUPLICATE** | **#148 `PULLBACK_MIN_ATR`** `hedge_manager.py:59` | **0.30** | ✅ | Same value and similar concept (minimum ATR-normalized adverse move to consider action). Different context (hedge justification vs H4 partial-bar regime-flip warning). Barbara decision: share value constant OR keep separate. |
| 14 | `DIRECTION_COOLDOWN_MIN` | 10 (min) | **EXACT_DUPLICATE (concept)** | **#107 `DIRECTION_LOCK_S`** `event_processor.py:241` | **300s = 5min** | ✅ | **Same concept** (cooldown same direction after GO). Different unit (min vs s). Existing value 5min is tighter than proposed 10min. **Do not calibrate new — adjust existing #107 if needed.** Also see #46 `trade_cooldown_min=30` (any level) and #47 `same_level_cooldown_min=60` (same level). |
| 15 | `DEFENSIVE_EXIT_MFE_GIVEBACK_PCT` | 0.40 | **DUPLICATE_DISABLED** | **#36 `mfe_giveback_enabled`=false**, **#37 `mfe_giveback_threshold`=0.5** | false / 0.5 | ❌ DISABLED | **Feature exists but disabled.** Proposal: enable #36 (set to true) + calibrate #37 (current 0.5 → my 0.40 ballpark). Do NOT invent new threshold. |
| 16 | `DEFENSIVE_EXIT_MFE_MIN_ATR_MULT` | 1.5 (×ATR) | **PARTIAL_DUPLICATE** | **#38 `mfe_min_profit_pts`=5.0** (pts, DISABLED via #39), #141 `pullback_mfe ATR=0.5` | 5.0 pts / 0.5 ATR | ❌ / ✅ | Same intent (minimum MFE before giveback rule applies). Different unit (pts vs ATR mult). Existing #38 in pts, disabled. Mine in ATR mult. Consolidation: convert #38 to ATR units (keep 5pts ≈ 0.25×ATR20) OR add new ATR-native threshold. |
| 17 | `DEFENSIVE_EXIT_DELTA_4H_THRESHOLD` | -800 | **PARTIAL_DUPLICATE** | **#157 `REGIME_FLIP_SHORT`=-1000** `run_live.py:156`, #8 `trend_resumption_threshold_short`=-800 (value match!), #2 `delta_4h_long_block`=-1050 | -1000 / -800 / -1050 | ✅ / ✅ / ✅ | Same family (delta_4h threshold). **Value -800 is IDENTICAL to existing #8**. #157 is regime-flip exit at -1000 (narrower). Confused territory — 3 overlapping thresholds. Barbara should consolidate these FIRST before adding new. |
| 18 | `DEFENSIVE_EXIT_ICEBERG_PROXIMITY_ATR` | 0.5 (×ATR) | **NEW** | (closest: #23 `iceberg_collision_price_band_pts`=1.6, #31 `iceberg_zones_proximity_pts`=5.0, #195 `ICEBERG_ZONES_PROX`=5.00) | pts not ATR | ✅ | All existing iceberg proximity in absolute points. No ATR-normalized iceberg threshold. Genuinely new unit — **NEW**. |

---

## 3. Actionable findings

### 3.1 Duplicates to REMOVE from calibration scope

| Invented # | Name | Action |
|---|---|---|
| #8 | `H4_MAX_STALENESS_HOURS_DEFAULT` | **Replace with `H4_STALE_HOURS`** (already in `d1_h4_updater.py:91`, value 8.0). Update Sprint C v2 commit `074a482` to import existing constant OR align values. One-value debate, not two. |
| #14 | `DIRECTION_COOLDOWN_MIN` | **Replace with existing `DIRECTION_LOCK_S`** (`event_processor.py:241`, value 300s=5min). Existing is tighter; proposed 10min would be *relaxation*. No new threshold needed. |

### 3.2 Disabled parameters worth enabling (pre-Sprint E activation proposal)

| # | Name | Current state | Proposed action |
|---|---|---|---|
| #36 / #37 | `mfe_giveback_enabled` / `mfe_giveback_threshold` | enabled=false, threshold=0.5 | **Enable + calibrate threshold** (my invented #15 value 0.40 is within range of existing 0.5; real calibration should bootstrap from 64 trades + MFE reconstruction). |
| #39 / #38 | `mfe_min_profit_enabled` / `mfe_min_profit_pts` | enabled=false, pts=5.0 | **Enable + convert to ATR unit** (#38 in pts = 5.0; ATR-normalized would be ~0.25×ATR at ATR=20pts; my #16 proposes 1.5×ATR much looser). Calibration decides. |

> **⚠️ Do NOT auto-enable these.** Proposal only. Requires Barbara ratification + shadow mode test before flipping `enabled=true`.

### 3.3 Genuinely NEW parameters requiring calibration (8)

| # | Name | Rationale | Data to use |
|---|---|---|---|
| 3 | `H4_CONTINUATION_WINDOW` | 3-bar window, no existing equivalent | Sweep 2-5 bars on historical H4 rule-fire stats |
| 4 | `H4_CONTINUATION_MIN_SAME` | 2-same requirement, no existing | Sweep 1-3 on history |
| 5 | `H4_CONF_STRONG` | Confidence scalar for H4 bias | Calibrate via outcome-weighted logistic (0.5-1.0) |
| 6 | `H4_CONF_SINGLE` | Single-rule confidence | Same as #5 |
| 7 | `H4_CONF_JAC_CONFIRMED` | JAC-derived confidence | Validate via empirical hit rate of R_H4_3/6 outcomes |
| 11 | `D1_CONF_STRONG` | D1 bias confidence strong | Analogous to #5 for D1 |
| 12 | `D1_CONF_SINGLE` | D1 bias confidence single | Analogous to #6 |
| 18 | `DEFENSIVE_EXIT_ICEBERG_PROXIMITY_ATR` | ATR-normalized iceberg proximity for exit | New concept — calibrate vs 295-day iceberg history + trade MFE reconstruction |

**8 genuine calibration targets.** Down from 18.

### 3.4 Partial duplicates — Barbara decision required (7)

| # | Name | Inventory ref | Barbara decision |
|---|---|---|---|
| 1, 2 | `H4_CLOSE_PCT_BULL/BEAR_THRESHOLD` | #64 (trend_cont) | Share `close_near_extreme_pct`=0.7 across TFs OR separate? Recommendation: **separate** (H4 vs M30 different microstructure; H4 is bias, M30 is continuation trigger). |
| 9, 10 | `D1_CLOSE_PCT_BULL/BEAR_THRESHOLD` | #64 | Same call as #1,#2 but for D1. Recommendation: **separate** (D1 noise profile yet different). |
| 13 | `PARTIAL_H4_FLIP_ATR_MULT` | #148 `PULLBACK_MIN_ATR`=0.30 | Share value constant (0.30) OR keep separate? Recommendation: **share numeric value but keep named distinctly** (hedge vs H4 gate are different decisions; 0.30 is the "minimum meaningful adverse move" heuristic). |
| 16 | `DEFENSIVE_EXIT_MFE_MIN_ATR_MULT` | #38 `mfe_min_profit_pts`=5.0 (disabled) | Convert existing #38 to ATR unit OR add new parallel threshold? Recommendation: **convert** (cleaner — one MFE-min-profit threshold, ATR-normalized). |
| 17 | `DEFENSIVE_EXIT_DELTA_4H_THRESHOLD` | #157 REGIME_FLIP_SHORT=-1000 + #8 trend_resumption=-800 (EXACT match) + #2 long_block=-1050 | **Consolidation needed FIRST.** 3 overlapping -800/-1000/-1050 thresholds with similar semantics. Do NOT add invented #17. Instead: clean up existing inventory, define authoritative defensive-exit delta_4h using one of the three (likely #157 -1000 for regime flip). Calibration then operates on the consolidated threshold. |

---

## 4. Recommendation

1. **8 thresholds to genuinely calibrate** (§3.3). Next sprint scope.
2. **2 thresholds to retire / replace** (§3.1) — Sprint C v2 commit `074a482` needs a small patch:
   - `H4_MAX_STALENESS_HOURS_DEFAULT` → import `H4_STALE_HOURS` from `d1_h4_updater.py` OR pick 6.0 vs 8.0 via calibration
   - Drop `DIRECTION_COOLDOWN_MIN` (use existing `DIRECTION_LOCK_S`)
3. **2 thresholds to enable existing infrastructure** (§3.2) — don't invent new, turn on `mfe_giveback_enabled` + `mfe_min_profit_enabled` with calibrated values.
4. **7 partial duplicates need Barbara calls** (§3.4) — especially #17 which overlaps 3 existing delta_4h thresholds that should be consolidated BEFORE adding anything new.
5. **Inventory also flags (§3/Cluster 1.5 of APEX inventory) 8 thresholds with value=1.5** — possible "convention" values never individually calibrated. Not in my 18 but worth a Sprint F audit.

**Calibration sprint can proceed on 8 NEW + whichever of the 7 PARTIAL Barbara confirms as genuinely distinct.** Minimum scope 8, maximum 15. Down from 18.

Zero writes to code/config/data. Zero service touches. Capture 3/3 intact.

---

## 5. System state

- Files modified: ZERO (this file is sole write)
- Restarts: ZERO
- Capture PIDs: untouched
- Git operations: NONE
- Time: ~45 min (within 2h budget)

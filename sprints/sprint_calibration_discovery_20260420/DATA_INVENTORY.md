# Data Inventory for Threshold Calibration — 2026-04-20

**Sprint:** sprint_calibration_discovery_20260420
**Mode:** READ-ONLY inventory. Zero writes to code/config/data. Zero restarts.
**Duration:** ~40 min (within budget)
**Snapshot time:** 2026-04-20 ~16:49 UTC
**Capture PIDs (2512, 8248, 11740):** intact.

---

## 0. TL;DR

- **5 of 5 threshold groups are calibratable** — 3 excellent, 1 good (with reconstruction), 1 degraded (needs fallback).
- **OHLCV 2020-present (2.2M rows)** covers Groups A, B, D directly.
- **trades.csv (64 trades, 2026-03-31→04-10)** is Barbara's defensive-exit dataset — **small but genuine**. Enriched `trades_news_enriched.csv` adds news context.
- **entry_quality_dataset.parquet (486 trades, 2020-01→2025-09)** = historical trade outcomes with `pnl_pts`, `win`, `outcome`. Goldmine but older regimes.
- **continuation_trades.jsonl (48,909 entries)** = shadow eval with `delta_4h`, `displacement`, `exhaustion`. Rich for Group E cooldown + overext empirical validation.
- **iceberg history (386 days, 2025-07-01→now, ~3.9GB)** + **anomaly_features_full (2.08M rows, Nov→Apr)** — strong microstructure baseline.
- **calibration_dataset_full.parquet (2.19M rows)** has literal `rolling_delta_4h` column — direct calibration input for overext/defensive.

---

## 1. Directory tree (`C:\data`)

Total: **4,017 files, 60.31 GB**.

| Path | Files | Size MB | Newest mtime UTC |
|---|---|---|---|
| `C:\data\(root)` | 1 | 0.00 | 2026-04-12 |
| `\calibration` | 4 | 0.03 | 2026-04-12 |
| `\features\iceberg_v1` | 69 | 140.08 | 2026-04-12 |
| `\features\iceberg_v2` | 105 | 121.98 | 2026-04-12 |
| `\features\iceberg_v2_new` | 6 | 13.11 | 2026-04-12 |
| `\features\v3_training` | 126 | 17.36 | 2026-04-12 |
| `\grenadier` | 4 | 34.31 | 2026-04-12 |
| **`\iceberg`** | **392** | **3906.96** | **2026-04-20 (live)** |
| `\labels\iceberg_v1` | 69 | 15.24 | 2026-04-12 |
| `\labels\iceberg_v2` | 105 | 25.67 | 2026-04-12 |
| `\labels\iceberg_v2_new` | 6 | 1.47 | 2026-04-12 |
| `\level2\$btc_usd_cxdxf` | 174 | 457.44 | 2026-03-03 |
| `\level2\_6e_xcme` | 335 | 1371.55 | 2026-04-20 (live) |
| `\level2\_btc_xcme` | 125 | 1068.05 | 2026-04-20 (live) |
| **`\level2\_gc_xcec`** | **590** | **4022.30** | **2026-04-20 (live)** |
| `\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5` | 129 | 12965.72 | 2026-04-07 |
| `\level2\_mes_xcme` | 365 | 7949.24 | 2026-04-20 (live) |
| `\level2\_mnq_xcme` | 461 | 25389.24 | 2026-04-20 (live) |
| `\level2\_mym_xcbt` | 331 | 3375.21 | 2026-04-20 (live) |
| **`\processed`** | **19 top** | **503.61** | **2026-04-20 (live)** |
| `\processed\anomaly_features` | 100 | 144.31 | 2026-04-12 |
| `\processed\databento_daily` | 235 | 23.40 | 2026-04-12 |
| `\processed\dxfeed_indicators` | 35 | 107.54 | 2026-04-12 |
| `\processed\grenadier_1s` | 105 | 87.15 | 2026-04-11 |

**1,107 parquets total** across `C:\data`.

---

## 2. Parquet inventory — priority files (`C:\data\processed\`)

### 2.1 `gc_ohlcv_l2_joined.parquet` — PRIMARY OHLCV ★★★

- **Rows:** 2,202,928 (minute bars)
- **Columns:** `open, high, low, close, volume`
- **Index range:** 2020-01-01 23:00 → **2026-04-20 16:46** (LIVE, <5min lag)
- **Suitability:** **GROUP_A ✅ EXCELLENT, GROUP_B ✅ EXCELLENT, GROUP_D ✅ EXCELLENT**
- **Notes:** 6+ years minute bars covers every market regime. Resample for H4/D1/M5 trivial. ATR computable.

### 2.2 `gc_m5_boxes.parquet` — ★★★

- Rows: 441,528 (m5 bars)
- Cols: 17 inc. `atr14, m5_liq_top, m5_liq_bot, m5_fmv, m5_box_high, m5_box_low, m5_box_confirmed, m5_box_id`
- Range: 2020 → 2026-04-20 16:45 (LIVE)
- **Suitability:** GROUP_A (M5 reference), validation of m5 box structure, MFE/MAE reconstruction

### 2.3 `gc_m30_boxes.parquet` — ★★★

- Rows: 73,676 (m30 bars)
- Cols: `atr14, m30_liq_top/bot/fmv/box_high/box_low/box_confirmed/box_id`
- Range: 2020 → **2026-04-20 16:30 (LIVE)**
- **Suitability:** Sprint C v2 inputs direct. Historic M30 bias per `derive_m30_bias` replayable.

### 2.4 `gc_h4_boxes.parquet` — ★★ (STALE 6d)

- Rows: 9,609
- Cols: `h4_liq_top/bot/fmv/box_high/box_low/box_confirmed/box_id, h4_jac_dir`
- Range: 2020 → **2026-04-14 14:00 (STALE per writer disabled)**
- **Suitability:** GROUP_A historic validation (up to 2026-04-14). Current calibration requires resample fallback.
- Same cutoff as `gc_d1_boxes.parquet`, `gc_d1h4_bias.json` — same dead writer `d1_h4_updater`.

### 2.5 `gc_d1_boxes.parquet` — ★★ (STALE)

- Rows: 1,612
- Cols: `atr14, d1_liq_top/bot/fmv/box_high/box_low/box_confirmed/box_id, d1_jac_dir`
- Range: 2020 → **2026-04-13 22:00 (STALE)**
- **Suitability:** GROUP_B historic validation. Resample D1 from OHLCV for current.

### 2.6 `gc_ats_features_v5.parquet` — ★★★ CRITICAL FOR CALIBRATION

- Rows: 73,120 (M30 cadence)
- Cols (60): `l2_dom_imbalance, l2_bar_delta, l2_cumulative_delta, l2_pressure_ratio, l2_absorption_detected, l2_absorption_side, l2_sweep_detected, l2_sweep_direction, l2_toxicity_score, l2_large_order_imbalance, weekly_trend, daily_trend, daily_trend_changed, daily_atr, daily_atr_regime, weekly_aligned, m30_atr, m30_in_contraction, m30_box_*, m30_box_confirmed, m30_box_fake_dir, m30_spring_type, m30_spring_low, m30_upthrust_high, m30_fmv, m30_liq_top/bot, m30_expansion_dir, m30_ats_trend, m30_prev_expansion, m30_momentum_stacking, m30_stack_risk_window, m30_liq_touch_long/short, m30_buec_long/short, entry_long/short, entry_trigger, entry_grade, entry_price, sl_long/short, tp1_long/short, tp2_long/short, rr_long/short, l2_entry_score, l2_danger_score, breakout_quality, session`
- Range: 2020 → **2026-03-31 23:30**
- **Suitability:** **EXCELLENT** para calibration — tem `daily_trend` labels, `entry_long/short` sinais, L2 features. Base direct para retrained thresholds. Gap: 3 semanas staleness.

### 2.7 `gc_ats_features_v4.parquet` — ★★

- Rows: 2,193,957 (M1 cadence!)
- Cols (69) inc. `h4_jac_dir, h4_box_confirmed, daily_jac_dir, daily_box_confirmed, weekly_trend, entry_long/short, entry_grade, m30_liq_touch_*, m30_buec_*, l2_entry_score, l2_danger_score`
- Range: 2020 → **2026-04-08 22:26** (STALE 12d)
- **Suitability:** GROUP_A/B historic com maior granularidade.

### 2.8 `calibration_dataset_full.parquet` — ★★★ CRITICAL

- Rows: 2,192,669 (M1)
- Cols (33) inc. **`rolling_delta_4h`** + `l2_dom_imbalance, l2_bar_delta, l2_pressure_ratio, m30_liq_top/bot, m30_box_confirmed, atr_m30, l2_bid_avg_order_sz, l2_ask_avg_order_sz`
- Range: 2020 → **2026-04-07 22:01** (STALE 13d)
- **Suitability:** **EXCELLENT** para `DEFENSIVE_EXIT_DELTA_4H_THRESHOLD` calibration — tem literalmente o `rolling_delta_4h` historical. Staleness recoverable (13d).

### 2.9 `entry_quality_dataset.parquet` — ★★ GOLD-TIER for Group C

- Rows: **486 trades**
- Cols: `direction, win, pnl_pts, outcome` + 49 feature columns matching v5
- Range: **2020-01-08 → 2025-09-05**
- **Suitability:** **GOOD** for Group C defensive exit — real trade outcomes with PnL. SMALL sample + old regime. Still valuable for sanity.

### 2.10 `anomaly_features_full.parquet` — ★★

- Rows: 2,086,878 (tick/sub-minute)
- Cols: `vpin_30m, vpin_5m, ofi_5m/15m/30m, trade_intensity_5m, avg_trade_size_5m, large_trade_fraction_5m, buy_aggressor_ratio_5m, delta_acceleration, spread_zscore_*, depth_ratio, depth_ratio_zscore_30m, toxicity_trend, dom_persistence, cancel_rate_5m`
- Range: **2025-11-26 → 2026-04-10** (4.5 months)
- **Suitability:** Direct microstructure anomaly baseline. Useful para overext & defensive-exit side features.

### 2.11 `databento_l2_m1_features.parquet` — ★

- Rows: 146,180 (M1)
- Cols: `dom_imbalance, dom_imbalance_mean, total_bid/ask_depth, mid_price, spread, bid/ask_avg_order_sz, bar_delta, buy/sell/total_volume, trade_count, symbol`
- Range: **2025-07-01 → 2025-11-24**
- **Suitability:** Historic L2 baseline pre-dxFeed transition.

### 2.12 `layer1_backtest_results.parquet` — ★

- Rows: 62 (trades from Layer 1 backtest run)
- Cols: `trade_idx, entry_ts, date, is_loss, veto_level, warning_count, fired_rules, r_R1..R6_*`
- Range: idx 0..61, `entry_ts` 2025-12-05+
- **Suitability:** Layer 1 guardrail rules firing stats. Narrow scope.

### 2.13 Other processed parquets (brief)

- `gc_ats_features.parquet`, `_v2`, `_v3`: various ATS feature vintages (all stale until Jan 2026)
- `gc_m30_base.parquet`: M30 OHLCV + TR/ATR base
- `gc_m1_boxes.parquet`: M1 with propagated M30 levels (stale Apr 6)

---

## 3. CSV / JSONL inventory (key files only)

### 3.1 `C:\FluxQuantumAI\logs\trades.csv` — ★★★ PRIMARY FOR GROUP C

- **Rows: 64**
- **Cols:** `timestamp, asset, direction, decision, lots, entry, sl, tp1, tp2, result, pnl, gate_score, leg1/2/3_ticket`
- **Date range:** 2026-03-31 15:09 → **2026-04-10 20:03**
- **Content:** real broker fills (RoboForex + Hantec via 3-leg protocol)
- `result` values: `tp1_hit`, `sl_hit`, `open`
- `pnl` in points, direction SHORT/LONG, gate_score computed
- **Sample:** `2026-03-31 15:09 SHORT entry=4614.49 sl=4633.68 tp1=4593.68 → tp1_hit pnl=4.77`
- **Suitability Group C:** **GOOD** — small N=64 but real outcomes. Can reconstruct MFE/MAE via forward OHLCV lookup per entry (gc_ohlcv_l2_joined has minute bars).
- ⚠️ `lots` column parsed as epoch — CSV formatting issue (stored "0.02" interpreted as timestamp). Real value 0.02 (Hantec live config).

### 3.2 `C:\FluxQuantumAI\logs\trades_live.csv` — ★

- Rows: ~9 (similar schema)
- Smaller companion file; Hantec-only subset per past incident fragmentation.

### 3.3 `C:\FluxQuantumAI\data\processed\trades_news_enriched.csv` — ★★

- Rows: 64 (same trades as trades.csv)
- Extra cols: `bucket, importance, min_to_event, event`
- **Suitability:** cross-reference trades vs economic calendar events. Directly supports H3 hypothesis verification.
- `trades_news_enriched_v2.csv` = smaller refined version.

### 3.4 `C:\FluxQuantumAI\logs\continuation_trades.jsonl` — ★★ HUGE

- **Lines: 48,909**
- **Keys:** `timestamp, phase, direction, price, delta_4h, atr_m30, daily_trend, displacement, exhaustion, decision, reason`
- Range: 2026-04-14 09:11 → 2026-04-20 (~6.6 days)
- **Suitability Group E (cooldown):** **EXCELLENT** — shadow engine evals para continuation. Tem `delta_4h` a cada tick + `decision` (SKIP/GO/etc). Padrões de timing claros.

### 3.5 `C:\FluxQuantumAI\logs\decision_log.jsonl` — ★★★ PRIMARY LIVE DECISIONS

- Size: 23.0 MB, **lines: 12,380**
- Range: **2026-04-14 01:00 → 2026-04-20 16:49 (LIVE, 6.7 days)**
- Keys: `timestamp, price_mt5, price_gc, gc_mt5_offset, context{phase, daily_trend, m30_bias, m30_box_mt5, liq_top_mt5, liq_bot_mt5, session, delta_4h}, trigger{type, level_type, level_price_mt5, proximity_pts, near_level_source}, gates{v1_zone, v2_l2, v3_momentum, v4_iceberg}, decision{action, direction, reason, total_score, execution, sl, tp1, tp2, lots, entry_mode, strategy_context}, decision_id, m5_atr, expansion_lines_mt5`
- **Suitability:** All 5 groups — especially Group E (cooldown, direction cadence). Short history (6.7d) but LIVE + detailed.

### 3.6 Iceberg JSONL — `C:\data\iceberg\iceberg__*.jsonl` — ★★★

- **386 files**, 2025-07-01 → 2026-04-20 (**295+ days**)
- Total size: ~3.9 GB (across all files in `C:\data\iceberg`)
- Schema (per spot check): `timestamp, side, price, probability, refill_count, type, ...`
- **Suitability:** DIRECT calibration data for `DEFENSIVE_EXIT_ICEBERG_PROXIMITY_ATR` (Group C) + V4 iceberg gate recalibration + Sprint D ML training (future).

### 3.7 Iceberg training features/labels — `C:\data\features\iceberg_v2` + `labels\iceberg_v2` — ★

- 105 daily parquets each (2025-07 to 2025-11 primarily)
- Used by iceberg_v2 ML pipeline (memory ref)
- **Suitability:** Sprint D iceberg-ML dedicated (fora deste scope mas inventariado).

### 3.8 Economic calendar — NOT FOUND locally

- No file matching `*news*` / `*calendar*` / `*econom*` in `C:\FluxQuantumAI\logs` or `C:\data`
- But `trades_news_enriched.csv` suggests a source existed — likely external fetch at enrichment time
- **Suitability H3:** degraded — need to re-fetch / confirm live news_gate source

### 3.9 L2 raw — `C:\data\level2\_gc_xcec\`

- 590 files inc. 242 `microstructure_*.csv.gz` files (live capture)
- Plus older `XAU_dom_*.csv` / `XAU_ticks_*.csv` (snapshot nov 2025)
- Plus `GLBX-20260407-RQ5S6KR3E5/` 129 files = Databento L2 raw snapshot Apr 7 (12.9 GB)
- **Suitability:** raw reference; processed features derived into parquets above.

---

## 4. Data suitability matrix (key table)

| # | Threshold group | Requirement | Available? | Source path | Sample size | Suitability |
|---|---|---|---|---|---|---|
| **A** | H4 bias (`H4_CLOSE_PCT_*`, `H4_CONTINUATION_*`, `H4_CONF_*`, `H4_MAX_STALENESS_HOURS_DEFAULT`) | OHLCV ≥ 10mo | **YES** | `processed/gc_ohlcv_l2_joined.parquet` | 2.2M minute bars (2020-2026) | **EXCELLENT** |
| **A.aux** | R_H4_3/6 via `h4_jac_dir` historic validation | H4 boxes parquet + jac_dir | **YES** (stale current) | `processed/gc_h4_boxes.parquet` | 9,609 H4 bars to 2026-04-14 | **EXCELLENT for historic**, DEGRADED current |
| **B** | D1 bias (if extended in Sprint E) | OHLCV ≥ 10mo + daily_trend labels | **YES** | `ohlcv_l2_joined.parquet` + `gc_ats_features_v5.parquet daily_trend` column | 2.2M OHLCV + 73K M30 with labels to 2026-03-31 | **EXCELLENT** (latest 3 weeks need relabel) |
| **C.primary** | Defensive exit (`DEFENSIVE_EXIT_MFE_GIVEBACK_PCT`, `MFE_MIN_ATR_MULT`, `DELTA_4H_THRESHOLD`, `ICEBERG_PROXIMITY_ATR`) | Trades with direction + entry + result + PnL | **YES** | `logs/trades.csv` | **64 real trades** (2026-03-31→04-10) | **GOOD** (small but genuine; MFE/MAE reconstructable) |
| **C.sec** | Defensive exit historic | Trades with PnL over broader regimes | **YES** | `processed/entry_quality_dataset.parquet` | 486 trades (2020-01→2025-09) | **GOOD** (old regimes; complements primary) |
| **C.aux1** | delta_4h threshold calibration | Historical `delta_4h` values | **YES** | `processed/calibration_dataset_full.parquet rolling_delta_4h` | 2.19M M1 rows to 2026-04-07 | **EXCELLENT** |
| **C.aux2** | Iceberg proximity calibration | Iceberg events over trade window | **YES** | `C:\data\iceberg\iceberg__*.jsonl` | **386 files, 295 days** | **EXCELLENT** |
| **D** | Partial H4 flip (`PARTIAL_H4_FLIP_ATR_MULT`) | OHLCV ≤ M15 resolution | **YES** | `ohlcv_l2_joined.parquet` (M1) + resample | 2.2M M1 | **EXCELLENT** |
| **E** | Direction cooldown (`DIRECTION_COOLDOWN_MIN`) | decision_log with direction + ts | **YES** | `logs/decision_log.jsonl` + `logs/continuation_trades.jsonl` | 12,380 + 48,909 entries (~1 week) | **GOOD** (short history but dense) |

---

## 5. Reconstruction paths (for thresholds lacking direct data)

### 5.1 MFE/MAE reconstruction (Group C — core Barbara need)

- **No direct MFE/MAE column exists** in `trades.csv` or `entry_quality_dataset.parquet`
- **Reconstructable** from: `entry_ts, direction, entry_price` → forward lookup in `gc_ohlcv_l2_joined.parquet` minute bars
- For each trade: scan minute bars between `entry_ts` and `exit_ts` (if `result="sl_hit"/"tp1_hit"`) OR fixed hold (if `result="open"`)
- Compute MFE = max(low/high during hold depending on direction)
- Compute MAE similarly (max adverse)
- **Fast** given 64 trades × ~4h hold × 240 bars/h = ~60k lookups.

### 5.2 daily_trend current values (Group B freshness)

- `gc_ats_features_v5.daily_trend` stale since 2026-03-31 (3 weeks)
- **Reconstructable** via D1 resample of OHLCV + re-run `derive_m30_bias`-like logic on D1 (but need calibrated writer)
- Alternative: use D1 3-bar FMV rule per current `_get_daily_trend` in `level_detector.py:149-175`

### 5.3 Economic calendar (H3)

- No local file found
- Live `news_gate` presumably fetches from external source at runtime
- **Required for H3 verification:** either parse `news_gate` module for source endpoint OR pull historical calendar export

---

## 6. Gaps — thresholds that CANNOT be calibrated with current data

| Gap | Reason | Workaround |
|---|---|---|
| Current `h4_jac_dir` (writer dead) | `d1_h4_updater` disabled Apr 14 | Use resample OHLCV fallback (already in Sprint C v2 code) |
| Live `daily_trend` / `weekly_trend` (writer dead) | Same pipeline | Resample D1/W1 from OHLCV, recompute inline |
| Historical MFE/MAE per trade | Not stored as column | Reconstruct via forward OHLCV lookup (§5.1) |
| Economic calendar context historical | Not found locally | Investigate `news_gate.py` source; possibly external API |
| Regime-specific calibration (Trump repricing, FOMC weeks) | Limited labeled regime data | Manual regime tagging OR cluster via OHLCV volatility |

**Blocking for calibration:** none. All 18 thresholds have at least degraded-path calibration possible.

---

## 7. Sample floors & statistical viability

For each group, minimum sample size needed for robust calibration (per v1.1 Framework):

| Group | Ideal N | Minimum N | Available N | Viable? |
|---|---|---|---|---|
| A (H4 bias rule fire rates) | 1000 H4 bars | 500 | ~13,000 H4 bars (2020-now resampled) | ✅ |
| B (D1 bias) | 250 D1 bars | 100 | 1,612 D1 bars | ✅ |
| C primary (trades) | 300 trades | 100 | **64 trades** | ⚠️ **BORDERLINE — needs C.secondary fallback** |
| C secondary (entry_quality) | 300 | 100 | 486 | ✅ |
| C aux1 (delta_4h) | 10k M1 | 1k | 2.19M | ✅ |
| D (partial H4) | 500 H4 transitions | 200 | ~13k H4 | ✅ |
| E (cooldown) | 5k decisions | 1k | 12,380 decisions | ✅ |

**Group C primary flag:** 64 real trades is below 100 minimum. Compensation: combine `trades.csv` (64, recent) + `entry_quality_dataset` (486, historic) = 550. Or reconstruct synthetic trade outcomes from historic `entry_long/short` signals in `gc_ats_features_v5` (~2000+ potential entry points).

---

## 8. Recommendations

### 8.1 Immediately feasible (no extra work)

1. **Group A (H4 bias thresholds):** calibrate via historical H4 resample rule-fire statistics. Compute rule-fire counts for R_H4_1/2/4/5 at various thresholds; sweep `H4_CLOSE_PCT_*` on ROC of next-bar P/L.
2. **Group D (Partial H4 flip):** backtest partial-bar state at 25/50/75/90% window vs final close direction.
3. **Group E (direction cooldown):** empirical gap distribution between consecutive GO decisions in decision_log.

### 8.2 Feasible with reconstruction

4. **Group C primary (defensive exit):** combine `trades.csv` + forward-OHLCV MFE/MAE. Correlate MFE giveback % with iceberg proximity + delta_4h shift. Calibrate `DEFENSIVE_EXIT_MFE_GIVEBACK_PCT` with bootstrap since N=64 small.
5. **Group C secondary:** replay `entry_quality_dataset` 486 trades with same reconstruction for cross-validation.

### 8.3 Needs additional data acquisition

6. **Economic calendar historical** for H3 backtest — verify `news_gate.py` source endpoint; consider 1-time external fetch.
7. **Re-enable `d1_h4_updater`** (Sprint H4-WRITER-FIX P1) so resample fallback is redundant only.

### 8.4 Proxy data candidates

- `continuation_trades.jsonl` (48,909 evals over 6.6d) — treat SKIP vs GO outcomes as pseudo-labels
- `layer1_backtest_results.parquet` (62 trades) — cross-reference existing rule-fire stats
- Iceberg 295-day history — calibration for iceberg-proximity-ATR threshold

---

## 9. Next-sprint scope suggestion

Based on this inventory, the calibration sprint should:

- **Group A + D + E:** calibrate directly from historic OHLCV/decision_log (~1-2 days)
- **Group B:** depend on D1 reconstruction logic; may co-deploy with H4 writer fix
- **Group C:** bootstrap calibration with combined trades (64 + 486 = 550 trades) + reconstruction; flag as preliminary with 90% confidence intervals

Do NOT proceed with Group C calibration without reconstruction methodology review by Claude (sample size concern).

---

## 10. System state during inventory

- Files modified: **ZERO** (this file is the only write)
- Restarts: **ZERO**
- Capture PIDs 2512, 8248, 11740: **INTACT**
- Git operations: NONE
- Python processes touched: NONE

---

## 11. Summary for Barbara

> Found **1,107 parquets, 386 iceberg JSONL files, 12,380 decision_log entries** inc. **2.2M-row OHLCV (2020-present, live)** and **64-trade live fills + 486-trade historic dataset**.
>
> - Group A/B/D/E suitability: **EXCELLENT** (direct calibration feasible).
> - Group C (defensive exit) suitability: **FEASIBLE with reconstruction** — 64 live trades + 486 historic; MFE/MAE reconstructable from OHLCV forward-lookup. Sample size borderline (550 combined) — flag for Claude bootstrap methodology review.
> - Degraded: `d1_h4_updater` writer dead since 2026-04-14 blocks current `h4/d1_jac_dir` — mitigable via resample (already in Sprint C v2).
> - Missing: economic calendar historical (H3 verification) — needs investigation.
>
> Calibration sprint can proceed for Groups A, B, D, E directly. Group C requires methodological framing. Zero blockers.

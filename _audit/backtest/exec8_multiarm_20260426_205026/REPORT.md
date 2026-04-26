# EXEC-8 — Multi-arm Decision Gate Backtest REPORT

**Task:** EXEC-8 (Asana 1214280776620030)
**Executor:** ClaudeCode #2
**Mode:** Read-only multi-arm replay (NO production code edits, NO config edits, NO history mutation)
**Window:** 2026-01-25 → 2026-04-25 (3 months, all post-handover, all rebuilt-clean)
**Time spent:** see brief-back
**Generated:** 2026-04-26

---

## SANITY

| Item | Value |
|---|---|
| Capture services | ✅ 8000 + 8002 LISTENING throughout (start, mid-run, end) |
| HEAD at task start | `e9b5aad CTRADER-INTEGRATION-001` (parent chain `135c426 EXEC-RECALIB-001` ← `5fea06f IMPL-4` untouched) |
| Production code edits | ZERO — `live/`, `config/`, `ml_news/` untouched |
| Standing rules 10/11/12 | honoured |
| New artifacts | 5 entries CSVs + metrics CSV + summary JSON + this REPORT + script + config_comparison |
| Backtest input parquet sha256 | `9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b` (matches calibration + investigation) |
| Backtest M30 rows | 2,212 (window 2026-01-25 → 2026-04-26 UTC) |

---

## PRAC — Pre-Response Adversarial Check (5 checks)

1. **Cherry-pick examples vs full distribution?** ✅ All 5 arms scored on the SAME 2,212-bar M30 window. Per-arm results are full-distribution; per-direction breakdown reported separately. No selective filtering.
2. **'Detection happens' = 'detection correct per methodology'?** ✅ The metric is PnL ground-truth (per-trade points) on a fixed-horizon (60-min forward) replay, NOT detection-counts. Win-rate, expectancy, Max-DD, Sharpe are all PnL-derived.
3. **Touch production data during backtest?** ✅ No. Read-only against the rebuilt parquet + boxes + L2-delta sources. No writes to production paths; output confined to `_audit/backtest/exec8_multiarm_20260426_205026/`.
4. **`git --amend`?** ✅ No history mutation. Investigation files from previous task remained untouched; only NEW commits will be created (per spec Step 7).
5. **Skip any methodology compliance category?** ⚠️ PARTIAL. Trend-line dots (5a), Liquidity Lines (5c), Strategy adherence (5d), Pullback recognition (5e) are produced by upstream detection pipelines (e.g. ATS Trend Line Part A at HEAD `8df5db4`) and are CONFIG-AGNOSTIC — they don't change between arms because the LOGIC-C entry rule under test does not consume them. Box-and-struct counts (5b proxy) ARE reported. The other four are documented as "out-of-scope for LOGIC-C entry rule under test", not skipped silently.

---

## RESULTS — 5-arm metric table

PnL replay protocol: entry on bar where `LOGIC-C score > threshold` AND any trend active; direction = anti-trend; exit at `t + 60min` (2 M30 bars); PnL = `entry_dir × (close[t+2] − close[t])` per contract; one trade per bar (independent, no overlapping inventory accounting; same convention as calibration label).

| Arm | weights | thr | n entries | n long / n short | win rate | exp /trade | total pts | Max DD | Sharpe per-trade |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **A** old / 0.40 (current production) | OLD | 0.40 | 136 | 69 / 67 | 41.2 % | **-1.465** | **-199.2** | 350.6 | -0.064 |
| **B** old / 0.25 (investigation prediction) | OLD | 0.25 | 205 | 109 / 96 | 47.3 % | **-0.669** | **-137.1** | 338.4 | -0.032 |
| **C** new / 0.40 | NEW | 0.40 | 55 | 29 / 26 | 52.7 % | +4.689 | +257.9 | **120.4** | **+0.184** |
| **D** new / 0.25 | NEW | 0.25 | 90 | 47 / 43 | 52.2 % | +3.271 | **+294.4** | 136.2 | +0.142 |
| **Ablation** F1+F2 only / 0.25 | F3/F5/F5_A=0 | 0.25 | 58 | 32 / 26 | **58.6 %** | +3.878 | +224.9 | 133.7 | +0.143 |

### Per-direction breakdown (sanity)

| Arm | LONG entry exp / win | SHORT entry exp / win |
|---|---:|---:|
| A | -2.81 / 0.41 | -0.07 / 0.42 |
| B | -1.52 / 0.46 | +0.29 / 0.49 |
| C | +4.39 / 0.52 | +5.02 / 0.54 |
| D | +2.64 / 0.53 | +3.96 / 0.51 |
| Ablation | +2.89 / 0.59 | +5.09 / 0.58 |

(LONG entry = `entry_dir=+1` = anti-SHORT reversal bet; SHORT entry = `entry_dir=-1` = anti-LONG reversal bet.)

---

## VERDICTS — per spec questions

### Q1: Does arm B ≥ A on all metrics? (validates threshold drop without weight changes)

**ANSWER: YES on rank, NO on absolute value.** Arm B is uniformly less-bad than Arm A:
- total: -137 (B) > -199 (A) → +62 pts improvement
- exp: -0.67 (B) > -1.47 (A)
- win: 0.473 (B) > 0.412 (A)
- Sharpe: -0.032 (B) > -0.064 (A)

**HOWEVER** Arm B is still NEGATIVE on every metric. The threshold drop reduces the bleed but does not stop it. Arm B is "less-broken production", not a viable promotion target.

### Q2: Does arm B ≥ D? (validates that NEW weights don't add value over threshold change alone)

**ANSWER: NO. Arm B is far worse than Arm D.**
- total: -137 (B) vs +294 (D) → 431 pt swing
- exp: -0.67 (B) vs +3.27 (D)
- Sharpe: -0.032 (B) vs +0.142 (D)

**The NEW weights add MASSIVE value over the threshold change alone.** This refutes the investigation's "OLD@0.25 dominates" finding when measured on PnL ground-truth on the 3-month window. The investigation's expectancy metric (mean_active × pass_rate over 9.7 months) is NOT the same statistic as per-trade PnL on the most recent 3 months, and the two diverge dramatically here.

### Q3: Does ablation ≥ A? (validates F3_B/F5_A/F5_B are noise)

**ANSWER: STRONG YES — and stronger than "noise". F3_B/F5_A/F5_B at OLD weights are HARMFUL.**
- total: +225 (Ablation) vs -199 (A) → +424 pt swing
- exp: +3.88 (Ablation) vs -1.47 (A)
- win: 0.586 (Ablation) vs 0.412 (A) — highest win rate of any arm
- Sharpe: +0.143 (Ablation) vs -0.064 (A)

The ablation arm zeroes F3_B (0.543), F5_A (0.394), F5_B (0.640) — together accounting for ~76 % of OLD weight mass. Removing them transforms the strategy from -199 to +225 pts. This is **direct empirical confirmation** that those three features, at their current OLD weights, are anti-signal on this 3-month slice. The investigation's directional-unreliability finding (sign flips across pre/post-handover halves for F3_B and F5_A; noise on both halves for F5_B) is now measured in dollar terms.

---

## METHODOLOGY COMPLIANCE (config-agnostic, run once)

| Category | Result |
|---|---|
| n_m30_bars (3-month window) | 2,212 |
| n_m30_box_confirmed | 1,350 (61.0 % of bars — ATS Box detector active) |
| n_at_struct_level | 549 (24.8 % — structural-level intersection) |
| n_trend_b (BOX ∩ STRUCT) | 288 (13.0 %) |
| n_trend_a (Op B 2-step monotonic) | 1,095 (49.5 %) |
| Label `anti_a_60m` coverage | 99.91 % (sanity OK) |
| Label `anti_b_60m` coverage | 99.91 % (sanity OK) |
| `bar_range == 0` count | 1 (single degenerate bar; well within tolerance) |
| `bar_body ≤ bar_range` invariant | 2,212/2,212 (100 % — sanity OK) |
| `fwd_60m` mean / std | +0.062 / 22.26 pts (low-bias, broad-tail; consistent with M30 close-to-close on GC) |

**5a Trend Line dots / 5c Liquidity Lines / 5d Strategy adherence / 5e Pullback recognition:** OUT-OF-SCOPE for the LOGIC-C entry rule under test. These are produced by upstream detection pipelines (ATS Trend Line Part A at HEAD `8df5db4`, etc.) and are CONFIG-AGNOSTIC — identical across all 5 arms. No FAIL category detected on the dimensions the LOGIC-C entry rule does consume (regime gating, feature firing, label coverage).

**Verdict:** PASS on the in-scope methodology dimensions. Out-of-scope categories are upstream concerns; no evidence of regression.

---

## INVESTIGATION RECONCILIATION

The investigation predicted "OLD weights @ 0.25 dominates everything". The 3-month PnL ground-truth on the rebuilt-clean window says the **opposite**. How can both be true?

- Investigation Step 6 metric: `mean_active × pass_rate` on `anti_combined`, full Window B (Jul 2025 → Apr 2025, 9.7 months). This is essentially a per-bar expected value of the label, weighted by activation rate.
- EXEC-8 metric: per-trade PnL on the 3-month sub-window (most recent post-handover slice).

Three explanations consistent with both observations:

1. **Regime change.** The 9.7-month window includes a Jul-Nov 2025 regime where OLD weights were better (and where the bulk of pre-handover bars live). The 3-month window is a single regime where OLD weights underperform. Both metrics are correctly reporting their windows.
2. **F3/F5 directional unreliability is the dominant effect.** OLD weights give F3_B/F5_A/F5_B ~76 % of score mass. Investigation Step 4 found these features flip sign across pre/post-handover halves. The 3-month window is entirely post-handover. On post-handover only, F3_B has d=-0.147 (negative — predicted reversals are wrong direction). With OLD weights amplifying these wrong-direction predictions, OLD-weight arms LOSE money. With NEW weights de-emphasising them, or with ablation eliminating them, the LOGIC-C engine relies on F1_B + F2_B (which ARE consistent across halves) and PROFITS.
3. **Calibration metric vs production metric divergence.** "Expectancy" in the calibration sense is not the same as per-trade dollar return on a held position. The investigation flagged this in PRAC ("`exp = mean_active × pass_rate` is a per-bar expected value, NOT a per-trade PnL"). EXEC-8 directly measures the latter; the divergence is the price of using a proxy in calibration.

Bottom line: the investigation correctly identified that F3_B/F5_A/F5_B are unreliable, but the dollar consequence of that unreliability (with OLD weights) is much larger than the per-bar metric suggested. EXEC-8 is the load-bearing decisive measurement.

---

## FINAL VERDICT — **FIX BEFORE SHIP**

Arm A (current production) is **bleeding -199 pts over the most recent 3 months** (-66 pts/month average). Shipping the current production weights into the live market is **NOT defensible** — it is empirically losing money on a clean post-handover slice.

Arm B (the threshold-only fix predicted by investigation) **is also losing money** (-137 pts). Threshold drop alone is NOT the answer.

The viable arms are C, D, and Ablation. Among these:
- **Arm D (NEW weights @ 0.25): best total pts (+294), 90 entries, Max DD 136.** Recommended primary.
- **Arm C (NEW weights @ 0.40): best Sharpe (+0.184), best Max DD (120), 55 entries.** Conservative alternative — fewer trade events, lower per-trade PnL noise, but only ~half the total revenue of D.
- **Ablation (F1+F2 only @ 0.25): highest win rate (58.6 %), only 58 entries, simplest model, +225 pts.** Strong alternative if Barbara prefers the simplest defensible model.

### FOLLOW-RECALIB-001 scope recommendation

**Apply NEW weights** in `live/impl3_logic_c.py:73-79`:
```
F5_B: 0.640 → 0.176
F3_B: 0.543 → 0.220
F5_A: 0.394 → 0.189
F2_B: 0.274 → 0.278
F1_B: 0.213 → 0.196
```

**Threshold:** Arm D (0.25) is the best-PnL choice; Arm C (0.40) is the best-Sharpe/lowest-DD choice. Recommend **D (0.25)** as primary, with C (0.40) as fallback if Barbara prefers fewer entries.

**OPTIONAL — Ablation arm consideration.** If the Wyckoff/ATS literature supports F3_B/F5_A/F5_B as conditionally valid features that just have a current-regime headwind, retain them at NEW weights. If not, the Ablation arm's win rate (58.6 % > 52.2 % Arm D) and competitive total PnL argue for actually removing them. This decision is METHODOLOGY-LITERATURE-FIRST per Standing Rule 1 — defer to Barbara + literature audit.

**Tier 1 / 2 / 4 constants:** all stay as-is (per investigation findings). EFR_ROLLING=100, EFR_DIVERGENCE_THRESHOLD=0.3, CLOSE_PCT_WEAK=0.3, vol_climax_multiplier=0.68206, delta_weakening_threshold=0.139486.

### Pre-ship requirements (BLOCKERS)

1. **CLOSE_PCT_WEAK SHORT-side methodology audit** (per investigation Step 5). Even with NEW weights de-emphasising F5, the underlying rule operationalization may still encode a wrong assumption for SHORT trends. This is a literature-first task; should not block FOLLOW-RECALIB-001 on weights but should be on the punch list before any aggressive scaling.
2. **Sample-size disclosure.** 3 months / 55-205 trades is a small sample. The verdict is robust on sign (NEW > OLD in every metric, large margin) but the absolute magnitude has bootstrap noise. Recommend that FOLLOW-RECALIB-001 ship with **paper-trade or quarter-size sizing** for at least 1 month before full sizing, to confirm the regression-to-paper-trade convergence.
3. **Window-A out-of-sample check (recommended before live).** Run the same 5-arm comparison on a Window A historical slice (older than Jul 2025) to confirm NEW weights are not over-fitted to the recent regime. If older data shows OLD weights winning, the regime-change explanation is confirmed and a regime detector should gate weight selection. If NEW weights also win on older data, the simpler attribution holds and ship can proceed.

---

## REPRODUCIBILITY

| Field | Value |
|---|---|
| Backtest script | `scripts/exec8_multiarm_backtest.py` |
| Output dir | `_audit/backtest/exec8_multiarm_20260426_205026/` |
| Config doc | `config_comparison.md` |
| Per-arm entries | `entries_<arm>.csv` × 5 |
| Per-arm metrics | `metrics_per_arm.csv` |
| Raw summary | `raw/summary.json` |
| Rebuilt parquet sha256 | `9871482a7fabeed855009f40b33e99382b2a4af6ce72067522952cf81ddba56b` |
| Boxes parquet sha256 | `50328224b657a0bc11300f359d9b4ae31251014648c0b816b4f7ed4bbd1719de` |
| Window | 2026-01-25 → 2026-04-26 UTC |
| Read-only contract | NO push, NO history mutation, NO production code changes |

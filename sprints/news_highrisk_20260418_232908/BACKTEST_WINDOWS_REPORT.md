# BACKTEST VALIDATION — Windows 5/3 Data-Driven — TASK_CLOSEOUT

**Timestamp:** 2026-04-19 13:45 UTC
**Duration:** ~15 min
**Status:** ⚠ **YELLOW** (strong lean toward RED for FOMC/CPI/NFP post-event)

---

## 1. CALENDAR DISCOVERY

- **Path:** `C:\FluxQuantumAI\Economic Calendar History_2025-2026.xlsx` (sole candidate, updated today 2026-04-19 14:00)
- **Date range:** 2025-07-01 13:30 UTC → 2026-04-17 18:00 UTC (10.5 months)
- **Raw rows:** 537 (US + non-US, mixed headers)
- **US events after processing:** **372** (vs 200 in FASE II — calendar nearly doubled)
- **Classification (keyword-based, same rules as FASE II):**
  - HIGH:   117 (31.5%)
  - MEDIUM: 135 (36.3%)
  - LOW:    120 (32.3%)
- **Event-type breakdown:**
  - OTHER: 171, UNEMPLOYMENT: 51, ISM: 32, FOMC: 28, CPI: 25, NFP: 20, RETAIL_SALES: 18, PPI: 10, FED_SPEECH: 9, GDP: 8
- Output: `C:\FluxQuantumAI\data\processed\news_calendar_us_full.parquet`

---

## 2. METHODOLOGY

- **Calibration dataset:** `calibration_dataset_full.parquet` (2,192,669 M1 bars, clipped to 248,765 bars Jul 2025–Apr 2026)
- **Metric:** M1 close-to-close `|return|` in bps
- **Buckets (relative minutes to event T):**

| Bucket | Range | Purpose |
|---|---|---|
| PRE_60 | T-60 to T-30 | Baseline (quiet) |
| PRE_30 | T-30 to T-5 | Old-blocked, marginal per FASE II |
| PRE_5 | T-5 to T-0 | New `pause_before=5` coverage |
| DURING | T-0 to T+1 | Peak release minute |
| EARLY_POST | T+1 to T+3 | New `pause_after=3` coverage |
| POST_5_30 | T+3 to T+30 | Old-blocked, FASE II said "compression" |
| POST_30_60 | T+30 to T+60 | Full cool-down |

- **Statistical tests:** Welch t-test vs PRE_60 baseline + Cohen's d

---

## 3. VALIDATION RESULTS

### 3.1 `pause_before=5min` validation — ❌ **FAILS**

| Tier | bucket | ratio | Cohen's d | p-value | Verdict |
|---|---|---|---|---|---|
| HIGH | PRE_5 | **0.98×** | -0.02 | 0.64 (n.s.) | NOT elevated |
| MEDIUM | PRE_5 | 0.99× | -0.01 | 0.86 (n.s.) | NOT elevated |
| LOW | PRE_5 | 0.95× | -0.03 | 0.37 (n.s.) | NOT elevated |

**FASE II had reported HIGH PRE_5 = 1.43× (d=0.35, p<0.0001).**
**Extended data (117 HIGH events vs 66 in FASE II) shows 0.98× — NO elevation.**

**Decision:** **FLAGGED** — `pause_before=5` blocks a window with baseline volatility. Low cost (only 5min per event, ~33 HIGH events/month), but empirically unnecessary.

### 3.2 `pause_after=3min` validation — ⚠ **PARTIAL**

| Tier | bucket | ratio | Cohen's d | p-value | Verdict |
|---|---|---|---|---|---|
| HIGH | DURING (T-0 to T+1) | 5.18× | 2.35 | <0.001 | **strongly elevated** ✅ |
| HIGH | EARLY_POST (T+1 to T+3) | 2.18× | 1.02 | <0.001 | elevated ✅ |
| HIGH | POST_5_30 (T+3 to T+30) | **1.60×** | 0.43 | <0.001 | **STILL elevated** ⚠ |
| HIGH | POST_30_60 (T+30 to T+60) | **1.72×** | 0.51 | <0.001 | **STILL elevated** ⚠ |

**`pause_after=3` covers only the 0-3min window (DURING + EARLY_POST), which is correctly elevated. But the spike persists well beyond 3 minutes** — POST_5_30 remains at 1.60× baseline with p<0.001, and POST_30_60 is 1.72×.

**FASE II had reported HIGH POST = 0.52-0.61× (compression).** Extended data shows **opposite** pattern (elevation, not compression).

**Decision:** **INSUFFICIENT** — 3-min post window leaves a 27-57min tail of elevated volatility exposed for HIGH events.

### 3.3 Trade-off validation (PRE_30 and POST_5_30) — ⚠ **MIXED**

| bucket × tier | ratio | Decision |
|---|---|---|
| HIGH PRE_30 | 1.10× | marginal — old 30min window was indeed excessive ✅ |
| MEDIUM PRE_30 | 1.10× | marginal ✅ |
| HIGH POST_5_30 | **1.60×** | CONTRADICTS FASE II "compression" claim ❌ |
| MEDIUM POST_5_30 | 1.29× | elevated (mildly) |

**Pre-event trade-off (PRE_30) validates** — safely reducible. **Post-event trade-off (POST_5_30) FAILS** — post-event is still elevated, not compressed.

### 3.4 FASE II comparison (side-by-side)

| Finding | FASE II (n=66 HIGH) | Backtest (n=117 HIGH) | Replicates? |
|---|---|---|---|
| HIGH DURING ratio | 4.49× | **5.18×** | ✅ consistent (stronger) |
| HIGH PRE_5 ratio | 1.43× (sig.) | **0.98×** (n.s.) | ❌ **CONTRADICTS** |
| HIGH PRE_30 ratio | 1.09× | 1.10× | ✅ |
| HIGH POST (0-5min) | 0.52-0.61× (compression) | EARLY_POST 2.18×, POST_5_30 1.60× | ❌ **CONTRADICTS** |
| MEDIUM DURING | 2.20× | 2.25× | ✅ |
| LOW pre/post | ~1.0× | ~1.0-1.2× | ✅ |

**Summary:** DURING findings replicate and strengthen. **PRE_5 and POST findings reverse direction.**

---

## 4. PER-EVENT-TYPE DRILL DOWN

**Most important finding:** event types behave **very differently** — global 5/3 is a crude average.

### DURING ratios (peak release volatility)

| Event type | n_events | DURING ratio | Notes |
|---|---|---|---|
| **CPI** | 25 | **9.72×** | most explosive release |
| NFP | 20 | 5.21× | strong |
| GDP | 8 | 4.14× | strong (small n) |
| PPI | 10 | 4.12× | strong |
| FED_SPEECH | 9 | 4.04× | strong |
| UNEMPLOYMENT | 51 | 3.79× | |
| RETAIL_SALES | 18 | 2.59× | |
| **FOMC** | 28 | **1.93×** | surprisingly mild at T-0 |
| ISM | 32 | 1.76× | mild |

### Post-event persistence (the critical metric)

| Event | EARLY_POST | POST_5_30 | POST_30_60 | How long does spike last? |
|---|---|---|---|---|
| **FOMC** | **2.55×** | **1.80×** | **2.15×** | >60min (highest tail) |
| CPI | 1.91× | 1.71× | 1.67× | 30-60min |
| GDP | 2.34× | 1.40× | 1.55× | 30-60min |
| PPI | 1.83× | 1.66× | 1.84× | >60min |
| NFP | 1.90× | 1.60× | 1.58× | 30-60min |
| FED_SPEECH | 1.86× | 1.43× | 1.19× | 5-30min |
| UNEMPLOYMENT | 1.60× | 1.41× | 1.74× | >60min |
| RETAIL_SALES | 1.06× | 1.62× | 1.47× | erratic |
| ISM | 1.23× | 1.19× | 1.30× | mild but persistent |

**Key observations:**
1. **FOMC** is the most dangerous for post-event: 2.55× at T+1-3, 1.80× at T+3-30, and still 2.15× at T+30-60. The old `pause_after=60` was **empirically justified**.
2. **CPI** DURING is extreme (9.72×) but post tails off faster.
3. **ISM / RETAIL_SALES** have weak DURING and post — old aggressive windows were indeed excessive.
4. **PRE_5** pattern is consistent: near baseline for almost all event types (CPI=1.08×, FOMC=0.81×, NFP=0.93×, etc.). `pause_before=5` is universally unnecessary as a vol-protection measure.

### Suggested per-event-type windows (data-driven proposal)

| Event | Current (applied) | Suggested based on backtest |
|---|---|---|
| FOMC | 5 / 3 | **2 / 45** (major post-event elevation) |
| NFP | 5 / 3 | **2 / 20** |
| CPI | 5 / 3 | **2 / 20** |
| GDP | 5 / 3 | **2 / 20** |
| PPI | 5 / 3 | **2 / 15** |
| FED_SPEECH | 5 / 3 | **2 / 10** |
| UNEMPLOYMENT | 5 / 3 | **2 / 15** |
| ISM | 5 / 3 | **2 / 5** (mild event) |
| RETAIL_SALES | 5 / 3 | **2 / 10** |

*Rationale:* drop `pause_before` from 5 → 2 (PRE_5 not elevated; 2min covers execution-queue latency only). Differentiate `pause_after` by observed persistence decay.

---

## 5. OVERALL DECISION

**⚠ YELLOW** (strongly leaning RED for FOMC/CPI/NFP/GDP/PPI post-event)

### Justification
- **pause_before=5 = safe but wasteful.** 5-min pre windows have baseline volatility across all tiers and event types. Low cost (few minutes/event), empirically unnecessary. Not worth rolling back on its own.
- **pause_after=3 = risky for HIGH post-event.** Five event types (FOMC, CPI, NFP, GDP, PPI) still show ≥1.6× volatility at T+3 to T+30min. FOMC is the worst — 2.15× still present at T+30-60min. The old 15-60min `pause_after` values were **empirically correct** for these events; global 3min is a regression.
- **PRE_30 and DURING findings replicate FASE II.** Only post-event claim breaks.

### Hypothesised cause of FASE II divergence
FASE II used 66 HIGH events (Jul 2025–Jan 2026). Extended data (Jul 2025–Apr 2026) adds ~51 HIGH events from Jan–Apr 2026. Jan–Apr 2026 likely includes a high-tariff/rate-policy-sensitive regime where post-event repricing extended well past 3 minutes (Trump-era policy uncertainty visible via 171 "OTHER" events, many being Trump speeches).

---

## 6. RECOMMENDATIONS

### Option A — Immediate rollback to old values (SAFEST)
Restore `backup_apply_windows_20260419_132509/economic_calendar.py` before Monday open. Loses FASE II window-reduction benefit, but avoids exposure to documented 1.6×+ post-event vol beyond T+3min.

### Option B — Partial rollback (RECOMMENDED)
Keep `pause_before=5` (cost-free; empirically unjustified but harmless) but **restore `pause_after` to tier-appropriate values**:
- CRITICAL (FOMC, NFP): 30min post
- HIGH (CPI, GDP, PPI, FED_SPEECH): 15min post
- MEDIUM (BOJ, UNEMPLOYMENT, ISM, RETAIL_SALES): 10min post

### Option C — Per-event-type differentiated windows (BEST, needs sprint)
Apply the table in §4 above. Requires separate sprint with more nuanced `EVENT_CONFIG` schema and validation. Not Monday-ready.

### Option D — Keep 5/3 and monitor (RISKIEST)
Trust the applied values; rely on Telegram alerts + human oversight during first FOMC/CPI/NFP after Monday open. Requires Barbara to be at screen during those events.

**Claude + Barbara decision needed before Monday market open (Sunday 22:00 UTC).** Recommend **Option B**.

---

## 7. PER-EVENT-TYPE DIFFERENTIATION (future sprint)

Draft spec for a follow-up sprint:
- Schema change: `EVENT_CONFIG[etype]` gains `pause_after_tier` with per-event post values
- Re-run validation on rolling 3-month windows to detect regime changes
- Consider conditional windows based on `actual` vs `forecast` deviation
- Integrate iceberg/L2 activity signals to override static windows

---

## 8. LIMITATIONS

1. **Overlapping events inflate POST_30_60.** With 372 events in 10.5 months, some POST_30_60 windows overlap with subsequent events' PRE_60 windows. This likely inflates late-bucket ratios. Event deduplication (drop same-day-same-hour duplicates) would refine the estimate — deferred.
2. **Keyword classification:** same 24 keywords as FASE II. Events classified as OTHER (171/372) may include high-impact items not captured by the keyword list.
3. **Single metric (|return| bps):** does not capture directional slippage, bid-ask spread blowup, or iceberg absorption — orthogonal risks.
4. **No live trade counterfactual:** cannot measure PnL impact directly. Vol is a proxy for risk, not outcome.
5. **Timezone assumption:** calendar times parsed as UTC (consistent with FASE II finding that NFP at 12:30 UTC = 8:30 ET).
6. **Small samples for rare event types:** GDP n=8, FED_SPEECH n=9, PPI n=10 — ratios have wide CIs.
7. **Regime bias:** 10.5 months is one macro regime. Results may not generalize outside of 2025-2026 market conditions.

---

## 9. FILES GENERATED

| File | Path | Purpose |
|---|---|---|
| news_calendar_us_full.parquet | `C:\FluxQuantumAI\data\processed\` | 372 US events classified |
| validation_windows_summary.csv | sprint dir | Per (importance × bucket) stats |
| per_event_type_breakdown.csv | sprint dir | Per (event_type × bucket) stats |
| backtest_windows_validation.py | sprint dir | Reproducible analysis script |
| BACKTEST_WINDOWS_REPORT.md | sprint dir | This report |

---

## 10. FILES NOT MODIFIED

- Zero code changes
- Zero yaml changes
- Production system intact (FluxQuantumAPEX service running on 5/3 windows applied earlier)
- Capture processes (PIDs 12332, 8248, 2512): **intact**

---

## FINAL COMMUNICATION

```
BACKTEST VALIDATION COMPLETE — YELLOW (leans RED)
Calendar: 372 US events (Jul 2025 → Apr 17 2026, extended)

pause_before=5 validation:  ❌ NOT validated (HIGH PRE_5 = 0.98×, n.s.)
                             Low-cost, empirically unnecessary.
pause_after=3 validation:   ⚠ INSUFFICIENT for HIGH events
                             HIGH POST_5_30 = 1.60× (sig.)
                             FOMC POST_30_60 = 2.15× still elevated!
                             FASE II "compression" claim reversed.

Per-event-type breakdown reveals FOMC/CPI/NFP need LONGER pause_after
than global 3min. Old 15-60min values were empirically correct.

RECOMMENDATION: Option B — partial rollback to tier-appropriate
                pause_after values before Monday open.

Report: C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\BACKTEST_WINDOWS_REPORT.md
```

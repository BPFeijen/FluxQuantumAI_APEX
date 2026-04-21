# ROLLBACK REFINADO FINAL — Windows data-driven completas — TASK_CLOSEOUT

**Timestamp:** 2026-04-19 15:10 UTC (Sunday — market closed)
**Duration:** ~4 min
**Status:** ✅ **SUCCESS**

---

## Context

Análise L2 profunda (DEEP_L2_POST_EVENT + pre-event MAE) identificou valores data-driven para **pause_before E pause_after** diferenciados por event type.

**Pre-event analysis key finding:** PPI é **outlier** — MAE pré-release persistente (8-11 bps até T-5min), sugerindo drift/leak institucional. Outros events safe em T-5 (MAE 2-4 bps).

**Post-event analysis key findings:**
- FOMC persistent até T+60 (MAE p90 85-110bps em toda a janela 0-60min)
- GDP/PPI prolonged até T+30 (sweet spot T+30 MFE/MAE 2.4× / 1.24×)
- NFP/CPI/UNEMPLOYMENT sweet spot T+15 (MFE/MAE 1.3-2.3×)
- FED_SPEECH rentável (MFE/MAE 6-13× em T+0 a T+15) — manter 3min
- MEDIUM mild events (ISM, RETAIL_SALES, BOJ) — 10min

---

## 1. BACKUP

- **Location:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\backup_rollback_refined_final_20260419_150919\`
- **Pre-rollback MD5:** `350d90f1fbada47e9386babfa3c1aa5d` (= post-hash Apply Windows, continuity confirmed)
- **Post-rollback MD5:** `bf3537ba90a7a7a430111ca5ec25120e`
- **MANIFEST.json:** present

---

## 2. CHANGES APPLIED

### 2.1 EVENT_CONFIG (11 event types)

| Event | Before (5/3) | After | Rationale |
|---|---|---|---|
| FOMC | 5/3 | **5/60** | Persistent post-event spike to T+60 (POST_30_60 = 2.15× baseline) |
| NFP | 5/3 | **5/15** | Sweet spot T+15 (MFE/MAE 2.34×) |
| CPI | 5/3 | **5/15** | Sweet spot T+15 (MFE/MAE 1.34×, DURING = 9.72× peak) |
| GDP | 5/3 | **5/30** | Prolonged post spike (T+30 MFE/MAE 2.40×) |
| **PPI** ⚠ | **5/3** | **10/30** | **Pre-leak detected (MAE 8-11bps T-30 to T-5) + prolonged post** |
| FED_SPEECH | 5/3 | 5/3 (no-op) | Event rentável — MFE/MAE 13× at T+15, skip extending post |
| ECB | 5/3 | **5/15** | HIGH tier consistency (US-only filter removes in practice) |
| BOJ | 5/3 | **5/10** | MEDIUM baseline (consistency) |
| UNEMPLOYMENT | 5/3 | **5/15** | Sweet spot T+15 (MFE/MAE 2.23×) |
| ISM | 5/3 | **5/10** | Mild event |
| RETAIL_SALES | 5/3 | **5/10** | Mild-moderate |

### 2.2 _IMPORTANCE_DEFAULTS (fallback for unrecognized events)

| key | Before | After |
|---|---|---|
| 3 (HIGH)   | 5/3 | **5/15** |
| 2 (MEDIUM) | 5/3 | **5/10** |
| 1 (LOW)    | 5/3 | **5/10** |

---

## 3. VALIDATION

### py_compile
```
py_compile: OK
```

### Import probe (strict per-event)
```
VALIDATION OK: 11 event types + 3 defaults correctly set (data-driven FINAL)
  PPI confirmed pause_before=10 (outlier)
  Other 10 events confirmed pause_before=5
news_gate.check(): score=0.0, action=NORMAL, block_entry=False, exit_all=False
```

---

## 4. SERVICE RESTART

- **Stop FluxQuantumAPEX:** success
- **Capture processes (CRITICAL — NEVER touched):**
  - PID 12332 (quantower_level2_api): ✅ intact
  - PID 8248 (iceberg_receiver):      ✅ intact
  - PID 2512 (watchdog_l2_capture):   ✅ intact
- **Start FluxQuantumAPEX:** success
- **New service PID:** 16956
- **Service status:** `Running`
- **Post-restart tracebacks (60s):** 0
- **10-min observation:** SKIPPED (weekend, no market data flowing)

---

## 5. FINAL STATE JOURNEY

| Phase | FOMC | NFP | CPI | GDP | PPI | FED_SPEECH | ECB | BOJ | UNEMP | ISM | RETAIL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Original (pre-Apply) | 30/60 | 30/30 | 30/15 | 30/15 | 15/15 | 15/30 | 30/30 | 15/15 | 15/10 | 15/10 | 15/10 |
| Apply Windows (5/3) | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 |
| **FINAL (LIVE)** | **5/60** | **5/15** | **5/15** | **5/30** | **10/30** | **5/3** | **5/15** | **5/10** | **5/15** | **5/10** | **5/10** |

---

## 6. DATA-DRIVEN RATIONALE (full summary)

**Post-event protection calibrated by L2 MFE/MAE per event type:**

| Event | Why the new `pause_after`? |
|---|---|
| FOMC **60** | MAE p90 85-110bps persists T+0 to T+60; MFE/MAE < 1 in all 60min windows; −19bps bias at T+60 |
| NFP **15** | Sweet spot T+15: MAE drops from 24→12bps, MFE similar (28bps), ratio 2.34× |
| CPI **15** | T+15 MAE 22, MFE 29, ratio 1.34; T+5 MAE 28 MFE 27 (worst — still in chaos) |
| GDP **30** | T+30 MAE 7bps p90 17bps, MFE 18bps → ratio 2.40 (best entry window) |
| PPI **30** | T+30 marginal 1.24; still negative bias (−7bps T+60) — protective |
| UNEMPLOYMENT **15** | Same profile as NFP (ratio 2.23 at T+15) |
| FED_SPEECH **3** | MFE/MAE 6-13× at T+0 to T+15 — event is a trading opportunity, don't block |
| ECB **15** | HIGH-tier consistency; US-only filter removes in production anyway |
| BOJ/ISM/RETAIL **10** | MEDIUM-tier mild events |

**Pre-event protection calibrated by pre-MAE:**

| Event | Why the `pause_before`? |
|---|---|
| **PPI** **10** | Unique outlier: MAE 8-11bps T-30 to T-5 (vs 2-4bps other events) — institutional leak/drift |
| All others **5** | MAE at T-5 is 2-4bps (near-baseline); pause_before=5 sufficient |

---

## 7. FILES MODIFIED

- `C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py` (single file)

## 8. FILES NOT MODIFIED

- `apex_news_gate.py` — SCORE_BLOCK_ENTRY=0.70, SCORE_EXIT_ALL=0.90 intactos
- `risk_calculator.py` — _ACTION_TABLE, THRESHOLD_* intactos
- `news_config.yaml` — dead config, ignorado
- `config/country_relevance_gold.json` — unchanged (US-only filter da FASE I)

---

## 9. ROLLBACK

- **Triggered:** NO
- **Available via backup if needed:**
  ```powershell
  Stop-Service FluxQuantumAPEX -Force
  Copy-Item "C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\backup_rollback_refined_final_20260419_150919\economic_calendar.py" "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py" -Force
  Start-Service FluxQuantumAPEX
  ```
  Restores to 5/3 state (state immediately prior to this rollback).

---

## 10. NEXT STEPS

1. **Audit Claude** do `economic_calendar.py` modified (diff vs `backup_rollback_refined_final_20260419_150919/`)
2. **Monday market open** — observação empírica:
   - **NFP/CPI/UNEMPLOYMENT**: Telegram BLOCK at T-5min, release at T+15min
   - **GDP**: BLOCK at T-5min, release at T+30min
   - **PPI**: BLOCK at T-10min (⚠ wider window), release at T+30min
   - **FOMC**: BLOCK at T-5min, **release at T+60min**
   - **FED_SPEECH**: BLOCK at T-5min, release at T+3min (quick re-open — event rentável)
3. **Empirical validation** (após 2-3 semanas):
   - PnL distribution pre vs post-fix
   - #trades blocked por event type (Telegram log scraping)
   - Check if PPI pause_before=10 prevents premature entries
4. **FASE III HighRisk dataset** — sprint separado
5. **Future enhancement:** migrate `EVENT_CONFIG` + `_IMPORTANCE_DEFAULTS` para `news_config.yaml` (dead config hoje) para permitir iteração sem restart

---

## COMUNICAÇÃO FINAL

```
ROLLBACK REFINADO FINAL COMPLETE (data-driven)

FOMC:         5/60   (persistent post-event spike)
NFP:          5/15   (sweet spot T+15)
CPI:          5/15   (sweet spot T+15)
GDP:          5/30   (prolonged spike)
PPI:          10/30  (pre-leak detected + prolonged post)
FED_SPEECH:   5/3    (event rentável — keep as is)
ECB:          5/15   (HIGH consistency)
BOJ:          5/10   (MEDIUM baseline)
UNEMPLOYMENT: 5/15   (sweet spot T+15)
ISM:          5/10   (mild)
RETAIL_SALES: 5/10   (mild)

_IMPORTANCE_DEFAULTS: 5/15, 5/10, 5/10

Service: Running (new PID 16956)
Capture: 3/3 intact (PIDs 12332, 8248, 2512)
Pre-hash: 350d90f1fbada47e9386babfa3c1aa5d
Post-hash: bf3537ba90a7a7a430111ca5ec25120e
Report: ROLLBACK_REFINED_FINAL_REPORT.md

Aguardo Claude audit + mercado Monday open.
```

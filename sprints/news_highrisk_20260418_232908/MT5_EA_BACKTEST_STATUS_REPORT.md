# MT5/EA Integration + Backtest Engine — Status Investigation

**Timestamp:** 2026-04-19 ~15:30 UTC
**Mode:** READ-ONLY (zero edits performed)
**Duration:** ~8 min

---

## EXECUTIVE SUMMARY

Two findings demand Barbara+Claude attention **before Monday**:

1. 🚨 **Execution has been broken since Apr 17 19:54 UTC.** Last 531 GO signals all `NOT_ATTEMPTED`. Last 254 execution attempts all FAILED (RoboForex `MT5 not connected`; Hantec `All legs failed`). **Zero successful executions in decision_log (Apr 14 → Apr 19).** Trades.csv shows last success Apr 10.
2. ⚠ **No native backtest mode in APEX.** Fase 8 POC exists (~20 KB) but is a rejected minimal engine — missing news_gate, L2 danger, regime flip, hedge manager, GAMMA/DELTA. Extending is feasible (4-8h). decision_log replay viable but only 5-day coverage.

---

## PARTE A — MT5/EA STATUS

### A.1 MT5 installations & processes

**Installations:**
- `C:\Program Files\Hantec Markets MT5 Terminal\` (4/9/2026)
- `C:\Program Files\RoboForex MT5 Terminal\` (3/27/2026)

**Running terminals (3 instances):**
| PID | Broker | Start time |
|---|---|---|
| 5016 | Hantec | 2026-04-18 19:26:29 |
| 8132 | Hantec | 2026-04-19 08:47:47 |
| 13156 | RoboForex | 2026-04-17 19:34:02 |

**MetaQuotes user profiles (9 terminal IDs):** `C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\<hash>\`

**NSSM services:**
| Name | Status | StartType |
|---|---|---|
| FluxQuantumAPEX | Running | Automatic |
| FluxQuantumAPEX_Dashboard | Running | Automatic |
| FluxQuantumAPEX_Dashboard_Hantec | Stopped | Automatic |
| FluxQuantumAPEX_Live | **Stopped** | Automatic |

**Python processes (confirmed):**
| PID | Command |
|---|---|
| 2512 | `watchdog_l2_capture.py` (CAPTURE — never touch) |
| 8248 | `iceberg_receiver.py` (CAPTURE — never touch) |
| 12332 | `quantower_level2_api:app` port 8000 (CAPTURE — never touch) |
| 6324 | `api.py` (serves port 8088 — EA signal API) |
| **9552** | **`run_live.py --execute --broker roboforex --lot_size 0.05`** (APEX live) |

### A.2 EA FluxQuantumAI_EA.mq5

**Deployed in 2 terminals:**
- `...\Terminal\0C70BAF49107A81D87101E046DBD933C\MQL5\Experts\FluxQuantumAI_EA.mq5` + `.ex5`
- `...\Terminal\5FFA568149E88FCD5B44D926DCFEAA79\MQL5\Experts\FluxQuantumAI_EA.mq5` + `.ex5`

**EA key params (source):**
| Param | Value |
|---|---|
| SignalApiUrl | `http://127.0.0.1:8088` |
| AccountNumber | 50051145 (likely RoboForex test) |
| PollIntervalMs | 1500 |
| Symbol | **XAUUSD** |
| MagicNumber | **20260409** |
| Slippage | 30 pts |
| EnableTrading | true (input) |

⚠ **Magic discrepancy:** EA uses `20260409`; `mt5_executor.py` (direct Python path) uses `MAGIC=20260331`. Two parallel paths → two magic numbers.

### A.3 Execution path actual

**There are TWO parallel execution paths** (discovered, not assumed):

**Path 1 (PRIMARY — active):** APEX Python → MetaTrader5 Python library → terminal64.exe → broker
```
run_live.py --execute --broker roboforex (PID 9552)
  └─ event_processor.py
       ├─ mt5_executor.py       (MT5Executor, account 68302120, MAGIC 20260331)
       └─ mt5_executor_hantec.py (MT5ExecutorHantec, Hantec account)
```

**Path 2 (SECONDARY — EA):** APEX → HTTP signal API → EA poll → EA sends orders via MT5 built-in
```
api.py on port 8088 (PID 6324)
  └─ FluxQuantumAI_EA.mq5 running inside terminal64.exe
       └─ CTrade.OrderSend (MT5 native)
```

Port 8088 has 1 **Established** connection from external IP `149.102.153.10` — presumably EA on a remote terminal or debug session.

### A.4 Runtime evidence (CRITICAL)

**decision_log.jsonl** metadata: 13.51 MB, 8423 lines, 2026-04-14 01:00 UTC → 2026-04-19 13:14 UTC (~5 days).

**Action distribution:**
| Action | Count | % |
|---|---|---|
| BLOCK | 7462 | 88.6% |
| GO | 531 | 6.3% |
| EXEC_FAILED | 430 | 5.1% |

**Execution outcome:**
| overall_state | Count |
|---|---|
| MISSING (schema pre-Fase 2) | 7019 |
| NOT_ATTEMPTED | 1150 |
| FAILED | 254 |
| **SUCCESS** | **0** |

**Execution attempt timeline:**
- First attempt logged: 2026-04-17 19:24:41 UTC
- Last attempt logged: 2026-04-17 19:54:12 UTC
- All 254 attempts in a ~30-min window, all FAILED.
- **After Apr 17 19:54:** all 531 subsequent GO signals show `NOT_ATTEMPTED`.

**Sample failed attempt (last):**
```
2026-04-17T19:54:12 | EXEC_FAILED | SHORT
brokers:
  - RoboForex: attempted=False, state=BROKER_DISCONNECTED, error="MT5 not connected"
  - Hantec:    attempted=True,  state=FAILED,              error="All legs failed"
```

**trades.csv** (successful trades archive): 64 trades, range 2026-03-31 15:09 UTC → 2026-04-10 20:03 UTC. **Last success: Apr 10.**

**Gap Apr 10 → Apr 14 (3 days):** no data.
**Gap Apr 14 → Apr 17 19:24 (3 days):** signals generated, `MISSING` execution state (likely pre-execution-schema era).
**Apr 17 19:24-19:54 (30min):** 254 execution attempts, 0 successes.
**Apr 17 19:54 → Apr 19 13:14 (2 days):** 531 GO signals, all `NOT_ATTEMPTED`.

⚠ **The execution path has been effectively dead for ~2 days.** Signals generated but not sent.

### A.5 PyTrader status

No `pytrader`/`PyTrader` references anywhere in live `C:\FluxQuantumAI\*.py`. Confirms memory note — not in use. XAUUSD paid-only limitation stands.

### A.6 Critical questions — answered

| # | Question | Answer |
|---|---|---|
| 1 | Execution path actual? | Primary: `run_live.py → mt5_executor.py → MT5 Python lib → terminal64.exe`. Secondary: `api.py:8088 ← EA poll ← EA CTrade`. |
| 2 | MT5 connected? | **NO.** 3 terminals running but last execution on Apr 17 returned "MT5 not connected" for RoboForex and "All legs failed" for Hantec. No successful attempts in 5-day log. |
| 3 | EA in use? | Partially — port 8088 has 1 Established external connection; no evidence EA-driven execution succeeded recently. Direct Python path is primary. |
| 4 | Known bugs status? | Port 8088 matches api.py (correct). Magic mismatch (EA 20260409 vs direct 20260331) — intentional separation but dual-magic tracking needed. |
| 5 | How were last 10 days' trades executed? | Via **Path 1** (direct Python, PID 9552 + mt5_executor.py). Successfully Mar 31 → Apr 10. After Apr 10: no successful trades recorded. |

---

## PARTE B — BACKTEST ENGINE

### B.1 Existing backtest infrastructure

**Found:**
- `C:\FluxQuantumAI\backtests\fase_8_backtest.py` (20.9 KB, 2026-04-18) — minimal engine, explicit POC
- 7 scripts in `C:\FluxQuantumAI\deploy-staging-20260417_164202\scripts\backtest_*.py` (calibration-specific)
- 2 scripts in `C:\FluxQuantumAI\scripts\backtest_*.py` (task-specific: posmon, fase4b, position_manager)
- 4 `replay_*.py` scripts (defense confirmed, tiers, directional) — event-specific replays, not full engine
- Today's work: `backtest_windows_validation.py` + `deep_l2_post_event_analysis.py` + `deep_l2_pre_event_analysis.py` (volatility-windows only)

**Files with `def backtest` / `class BacktestEngine`:**
- `C:\FluxQuantumAI\backtests\fase_8_backtest.py` (only 1 hit)
- Rest are functional scripts, not engines

**No shared `BacktestEngine` class** — each script is bespoke.

### B.2 APEX entrypoints & invocation

**Service command (NSSM wrapper):**
```
C:\tools\nssm\nssm.exe → spawns
python -u -Wignore run_live.py --execute --broker roboforex --lot_size 0.05
```

**Main entrypoints:**
- `run_live.py` (live trading — has `--dry_run` flag but still consumes realtime feed)
- `live/event_processor.py` (FluxSignalEngine)
- `backtests/fase_8_backtest.py` (POC backtest, not invoked by any service)

**Invocation of fase_8_backtest.py:** `python backtests/fase_8_backtest.py` (manual).

### B.3 Event loop structure

- `run_live.py` has `--dry_run` mode: processes live data, does NOT send orders. Not a replay — still needs live market feed.
- Event-driven architecture: APEX consumes L2 via Quantower port 8000 + iceberg receiver + M1/M5/M30 bars.
- **No flag for "replay from file" mode.** System is tightly coupled to live capture sockets.

### B.4 Fase 8 POC reuse potential

**What exists (from `fase_8_backtest.py` docstring):**
- Load `calibration_dataset_full.parquet`
- Detect ALPHA triggers (close within 2pts of m30 liq)
- Call `ATSLiveGate.check()` — this is the **actual production gate** ✅
- Simulate 3-leg fills (40/40/20, session lots)
- Walk forward 4h for TP1/TP2/SL
- SHIELD post-TP1 (SL → entry)
- Slippage + spread

**What is MISSING (per its own disclaimers):**
- GAMMA/DELTA triggers (0 historical fires — OK to skip)
- L2 danger exit (per-bar danger score needed)
- Regime flip exit
- **News gate (ApexNewsGate)** ← now that the news_gate is live, this gap is fixable
- Trailing stop post-SHIELD
- Hedge manager

**Extensibility:** skeleton is solid. News_gate integration is straightforward (call `news_gate.check()` on each bar, respect `block_entry` / `exit_all`). L2 danger and hedge are moderate effort.

### B.5 decision_log.jsonl as replay source

**Metadata:**
- Size: 13.51 MB
- Lines: 8423 decisions
- Date range: **2026-04-14 01:00 → 2026-04-19 13:14 (5.5 days)**

**Richness per decision:**
- Full context (phase, daily_trend, m30_bias, box, ATR, liq levels)
- Trigger (type, level, proximity)
- Gates v1-v4 (status, scores)
- Decision (action, direction, reason, total_score)
- Protection (anomaly, iceberg states)
- **Execution block** (attempted, brokers, ticket, error) — post-Apr 17
- All decision_id for cross-ref

**Feasibility as replay:** ✅ **very high for "what-if" counterfactuals** (e.g., what if windows 5/60 had been active — would gate decisions change?). Limitation: only 5.5 days, only one execution regime.

### B.6 Estimate

| Approach | Effort | Coverage | Blocker for Monday? |
|---|---|---|---|
| Use `fase_8_backtest.py` as-is | 0h | 10 days, no news/L2/hedge | No (rejected quality) |
| Extend Fase 8 POC with news_gate + L2 danger | 4-8h | Full backtest dataset | Probably not ready for Monday |
| decision_log replay (ex-post what-if) | 2-4h | 5.5 days only | Ready before Monday for specific questions only |
| Mock capture + full engine replay | 16-24h | Full historical | No |
| New engine from scratch | 24+h | — | No |

**Recommended path:** decision_log ex-post replay for 5-day validation of new windows, + plan Fase 8+news_gate extension for this week.

---

## OVERALL STATUS & RECOMMENDATION

### System readiness for Monday 22:00 UTC

| Component | Status | Risk if untested |
|---|---|---|
| 4 features (StatGuardrail/DefenseMode/V4/ApexNewsGate) | ✅ validated 2026-04-18 | LOW |
| Data-driven windows (FOMC 5/60, PPI 10/30, etc.) | ✅ applied 2026-04-19 15:10, ❌ backtest pending | MEDIUM |
| MT5 execution path (mt5_executor.py) | ❌ **BROKEN since Apr 17 19:54** | **HIGH — NO-GO** |
| Full backtest validation of system | ❌ missing | MEDIUM |

### Primary blocker: execution path

**Symptoms (from decision_log):**
- RoboForex: `BROKER_DISCONNECTED — MT5 not connected`
- Hantec: `All legs failed`
- 531 GO signals with `NOT_ATTEMPTED` after Apr 17 19:54

**Likely causes (hypotheses, READ-ONLY — not investigated beyond log evidence):**
1. MT5 session 0 vs user session issue (`mt5_executor.py:91` warns about this explicitly: "mt5.initialize() can segfault in Session 0")
2. `.env` with MT5 credentials missing / expired
3. Terminal login expired
4. Service restarted but terminal64.exe was never relogged

Without these hypotheses verified, **the system will probably still fail to execute Monday morning** even though signals will be emitted with the new window config.

### Recommendation

| Priority | Action | Timing |
|---|---|---|
| 🔴 P0 | **Investigate & fix MT5 execution path** (`run_live.py` → `mt5_executor.py`) before Monday 22h | **BEFORE market open** |
| 🟡 P1 | Run decision_log ex-post replay for window validation (2-4h) | Before Monday if P0 done |
| 🟡 P1 | If P0 not resolvable: Monday open in **DRY_RUN mode only** (no orders) for empirical window observation via Telegram + logs, resume --execute after fix | Monday fallback |
| 🟢 P2 | Extend Fase 8 POC with news_gate + L2 + hedge | This week |
| 🟢 P2 | Design per-event-type schema migration (yaml-backed) | Next sprint |

---

## FILES INVENTORIED

### Live paths
- `C:\FluxQuantumAI\mt5_executor.py` (RoboForex executor)
- `C:\FluxQuantumAI\mt5_executor_hantec.py` (Hantec executor)
- `C:\FluxQuantumAI\live\event_processor.py` (FluxSignalEngine)
- `C:\FluxQuantumAI\live\position_monitor.py`
- `C:\FluxQuantumAI\live\hedge_manager.py`
- `C:\FluxQuantumAI\live\mt5_history_watcher.py`
- `C:\FluxQuantumAI\run_live.py` (main entrypoint)

### Backtest paths
- `C:\FluxQuantumAI\backtests\fase_8_backtest.py`
- `C:\FluxQuantumAI\scripts\backtest_*.py` (6 files)
- `C:\FluxQuantumAI\scripts\replay_*.py` (4 files)
- `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\backtest_windows_validation.py` (today's work)

### EA paths
- `...\Terminal\0C70BAF49107A81D87101E046DBD933C\MQL5\Experts\FluxQuantumAI_EA.mq5` + `.ex5`
- `...\Terminal\5FFA568149E88FCD5B44D926DCFEAA79\MQL5\Experts\FluxQuantumAI_EA.mq5` + `.ex5`

### Signal API
- (location undetermined in this read-only sweep) — `api.py` running under PID 6324 serving port 8088 with 1 Established external connection from `149.102.153.10`

### Logs consulted
- `C:\FluxQuantumAI\logs\decision_log.jsonl` (13.51 MB, 8423 lines)
- `C:\FluxQuantumAI\logs\trades.csv` (64 trades Mar 31 → Apr 10)
- `C:\FluxQuantumAI\logs\service_stderr.log` (recent tail — no MT5/broker events post-Apr 17)

---

**NO EDITS PERFORMED. ALL READ-ONLY.**
Capture processes (PIDs 12332, 8248, 2512): NOT TOUCHED.
No service restarts. No config modifications.

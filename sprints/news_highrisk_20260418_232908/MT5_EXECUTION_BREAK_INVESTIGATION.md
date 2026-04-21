# MT5 Execution Break Investigation — READ-ONLY

**Timestamp:** 2026-04-19 ~16:00 UTC
**Mode:** READ-ONLY (zero edits, zero restarts, zero mt5.initialize() calls)
**Duration:** ~20 min

---

## EXECUTIVE SUMMARY

The earlier "execution broken since Apr 17 19:54" narrative needs revision.

**What actually happened:**
- **Apr 17 19:24-19:54** — 30-minute window of 254 execution attempts, all FAILED.
- **After Apr 17 19:54** — no further execution attempts: not because execution was disabled, but because (a) GO signals became rare/absent and (b) Friday market close at ~21:00 UTC caused m1 data to go stale, triggering `STRUCTURE_STALE_BLOCK` on all subsequent decisions (correct, conservative behaviour).
- **531 "GO + NOT_ATTEMPTED"** — ALL have reason `iceberg large_order sc=+2/+3` or `iceberg sweep sc=+2`. These are iceberg-side signals that don't flow through the production execution path (shadow/advisory). Not a bug.
- **The real break is the 30-min FAILED window on Apr 17.** All 254 attempts show identical pattern: RoboForex `BROKER_DISCONNECTED — MT5 not connected`, Hantec `FAILED — All legs failed`.

**Top hypothesis:** transient failure during a service/terminal restart sequence on Apr 17 19:24. Neither broker's terminal was fully operational at the time:
- RoboForex terminal (PID 13156) started 19:34 — 10 min AFTER failures began
- Hantec terminal was NOT running 17:25 (FluxQuantumAPEX_Live stopped) → next Hantec terminal boot was Apr 18 12:15

**Risk for Monday:** unclear. The 30-min failure was transient. But we found a **latent code bug**: `mt5_executor_hantec.py` has **module-level `mt5.initialize()`** (unlike `mt5_executor.py` which was refactored to deferred init on Apr 14 to avoid Session 0 issues). If the Hantec terminal isn't up at service start, this blocks the shared MetaTrader5 singleton and may cause RoboForex to fail silently.

---

## 1. TIMELINE

### Key chronology

| Timestamp (UTC) | Event |
|---|---|
| Apr 10 20:03 | **Last successful trade** (trades.csv final row) |
| Apr 14 01:00 | decision_log.jsonl starts current schema |
| Apr 14 10:02 | `mt5_executor_hantec.py` last modified — **module-level init** pattern |
| Apr 14 20:03 | `test_mt5_ipc.py` created |
| Apr 14 20:21 | `test_mt5_ipc2.py` created |
| Apr 14 20:49 | `mt5_executor.py` last modified — **DEFERRED init** (refactored, per explicit comment about Session 0 segfault) |
| Apr 14 20:50 | `run_apex_wrapper.py` created |
| Apr 16 17:43 | decision_log MISSING state ends (~23h gap to next decision — service down?) |
| Apr 17 16:18:11 | First `NOT_ATTEMPTED` (all BLOCK decisions with valid execution schema) |
| Apr 17 16:42 | Deploy batch: base_dashboard_server.py, d1_h4_updater.py, level_detector.py, tick_breakout_monitor.py |
| Apr 17 17:25 | `service_hantec_stderr.log` stops being written (FluxQuantumAPEX_Live service stopped) |
| Apr 17 19:18 | Previous RoboForex auth from external IP 149.102.153.10 (per terminal log) |
| Apr 17 19:24:41 | **First FAILED** execution attempt |
| Apr 17 19:34:02 | RoboForex terminal (PID 13156) fresh start in Session 0 |
| Apr 17 19:34:10 | RoboForex terminal authorized on RoboForex-Pro |
| Apr 17 19:54:12 | **Last FAILED** execution attempt |
| Apr 17 19:56:55 | Last GO decision (iceberg reason, NOT_ATTEMPTED) |
| Apr 17 21:14 | m1 data stops flowing — **market close** (GC closes 17:00 ET = 21:00 UTC Friday) |
| Apr 18 00:17 | RoboForex terminal (4C2D) disconnected + shutdown "exit 0" |
| Apr 18 12:14 | RoboForex terminal (4C2D) fresh restart + authorized |
| Apr 18 12:15 | Hantec terminal (0C70) restart + EA FluxQuantumAI loaded + 50051145 authorized |
| Apr 18 19:26 | Hantec terminal PID 5016 restart (SessionId=2 — user desktop) |
| Apr 19 08:47 | Hantec terminal PID 8132 restart (SessionId=0) |
| Apr 19 13:14 | Last decision_log entry (STRUCTURE_STALE: m1 age 144335s = 40h, normal for weekend) |
| Apr 19 15:10:44 | **Current `run_live.py` PID 9552 started** (today, during Rollback Refined) |

### File modifications Apr 10-19 (critical files)

| File | Last modified | Notes |
|---|---|---|
| `mt5_executor.py` | Apr 14 20:49 | Deferred init refactor |
| `mt5_executor_hantec.py` | Apr 14 10:02 | Module-level init **unchanged** |
| `run_live.py` | Apr 14 16:12 | |
| `live/mt5_history_watcher.py` | Apr 18 10:33 | |
| `live/position_monitor.py` | Apr 18 11:35 | |
| `live/hedge_manager.py` | Apr 18 11:35 | |
| `live/event_processor.py` | Apr 19 08:44 | (today) |
| `.env` | Apr 14 20:43 | Not touched since (817 bytes) |

Git: repo is not a git directory (`Is a git repository: false`), no commit history available — relied on file mtimes.

### decision_log state transitions

| State | First | Last | Count |
|---|---|---|---|
| MISSING (pre-schema) | 2026-04-14 01:00 | 2026-04-16 17:43 | 7019 |
| NOT_ATTEMPTED | 2026-04-17 16:18 | 2026-04-19 13:14 | 1150 |
| FAILED | 2026-04-17 19:24 | 2026-04-17 19:54 | 254 |
| **SUCCESS** | — | — | **0** |

### FAILED distribution by minute

```
19:24: 2    19:32: 21   19:40: 34   19:48: 29
19:25: 7    19:34: 3    19:42: 1    19:49: 3
19:27: 5    19:35: 13   19:43: 4    19:51: 1
19:28: 15   19:36: 20   19:44: 27   19:52: 9
19:30: 2    19:38: 1    19:46: 1    19:53: 24
19:31: 11   19:39: 7    19:47: 3    19:54: 11
```

Continuous failures for 30 min. Highest clusters at 19:40 (34) and 19:48 (29).

### Broker outcomes — unanimous

| Broker | Result | Error | Count |
|---|---|---|---|
| RoboForex | BROKER_DISCONNECTED | "MT5 not connected" | 254/254 |
| Hantec | FAILED | "All legs failed" | 254/254 |

**Both brokers failed on EVERY attempt.** No retcode, no error_code — suggests Python-side failures (not server rejects).

---

## 2. MT5 TERMINALS STATE

### Running terminals (3)

| PID | Broker | SessionId | MainWindowTitle | StartTime |
|---|---|---|---|---|
| 5016 | Hantec | **2** (user desktop) | (empty) | 2026-04-18 19:26:29 |
| 8132 | Hantec | **0** (service) | (empty) | 2026-04-19 08:47:47 |
| 13156 | RoboForex | **0** (service) | (empty) | 2026-04-17 19:34:02 |

Empty titles confirm headless Session 0 operation for PID 8132 + 13156.

### Session IDs of all processes

| PID | Process | SessionId |
|---|---|---|
| 2512 | watchdog_l2_capture (CAPTURE) | 0 |
| 8248 | iceberg_receiver (CAPTURE) | 0 |
| 12332 | quantower_level2_api:8000 (CAPTURE) | 0 |
| 6324 | api.py (EA signal API, port 8088) | 0 |
| 9552 | run_live.py --execute (TODAY) | 0 |
| 16956 | NSSM wrapper for FluxQuantumAPEX | 0 |

**All trading/execution processes in Session 0.** No cross-session issue.

### MT5 terminal logs — key extracts

**Terminal 5FFA... (RoboForex 68302120) on Apr 17:**
```
19:34:04 Terminal   RoboForex MT5 Terminal x64 build 5709 started for RoboForex Ltd
19:34:09 Window     MDI unhook failed ... error 0      (headless expected)
19:34:10 Network    '68302120': authorized on RoboForex-Pro through United Kingdom (ping 12.75 ms)
19:34:10 Network    previous successful authorization performed from 149.102.153.10 on 2026.04.17 19:18:17
19:34:10 Network    terminal synchronized with RoboForex Ltd: 0 positions, 0 orders, 94 symbols
19:34:10 Network    trading has been enabled - hedging mode
```

RoboForex terminal was **successfully logged in from 19:34:10 onwards**, yet execution attempts kept failing for 20 more minutes (until 19:54). This rules out "terminal not running" as the sole cause after 19:34:10.

**Terminal 0C70... (Hantec 50051145) on Apr 18 12:15:**
```
12:15:30 IPC        failed to initialize IPC     (benign at startup)
12:15:30 Terminal   Hantec Markets MT5 Terminal x64 build 5746 started
12:15:35 Experts    expert FluxQuantumAI_EA (XAUUSD,M30) loaded successfully
12:15:36 Network    '50051145': authorized on HantecMarketsMU-MT5 through UKDC09
12:15:36 Network    previous successful authorization performed from 149.102.153.10 on 2026.04.18 13:06:55
12:15:36 Network    terminal synchronized ... 1 positions, 0 orders, 569 symbols
12:15:36 Network    trading has been enabled - hedging mode
```

On Apr 17, the Hantec terminal was **not running** (next fresh start Apr 18 12:15). FluxQuantumAPEX_Live service had stopped at Apr 17 17:25 and never restarted. This explains Hantec "All legs failed" — the terminal wasn't there to receive orders during 19:24-19:54.

### Experts journals

EA FluxQuantumAI_EA loaded successfully in Hantec terminal Apr 18 12:15 (after the failure window). Not relevant to Apr 17 19:24-19:54.

---

## 3. CONFIGURATION

### .env

- Present: `C:\FluxQuantumAI\.env` (817 bytes, LastWriteTime Apr 14 20:43)
- `deploy-staging-20260417_164202\.env` exists (832 bytes, Apr 17 16:42) — 15-byte diff, not deployed to live location
- Content NOT inspected (credentials)

### mt5_executor.py (RoboForex) — key logic

- `SYMBOL="XAUUSD"`, `ACCOUNT=68302120`, `MAGIC=20260331`, `LOT_SIZE=0.02`, `DEVIATION=30`
- **Deferred init** at import: `"mt5.initialize() can segfault in Session 0"` (explicit comment)
- `reconnect()` called lazily before execution
- Credentials loaded from `.env` via `_load_env_robo()` — keys `ROBOFOREX_TERMINAL`, `ROBOFOREX_PASSWORD`, `ROBOFOREX_SERVER`
- Error path at line 250: `return {"success": False, "error": "MT5 not connected", ...}` ← **matches ALL 254 RoboForex FAILED samples**

### mt5_executor_hantec.py (Hantec) — key logic

- Symbol, account, password, server: `TERMINAL`, `ACCOUNT`, `PASSWORD`, `SERVER` (constants from top of file, values not inspected)
- **MODULE-LEVEL `_mt5.initialize()`** at import time (lines 86-92):
  ```python
  init_ok = _mt5.initialize(
      path=TERMINAL, login=ACCOUNT, password=PASSWORD,
      server=SERVER, timeout=15000,
  )
  ```
- If init fails → `MT5_AVAILABLE = False`, class instantiated but `"NOT connected — all calls will be no-ops"`
- But `reconnect()` exists (lines 166-190) for lazy retry
- **Critical:** **module-level init shares the single `MetaTrader5` singleton with the RoboForex executor** — a single Python process can bind the lib to only ONE terminal at a time.

### settings.json broker section — not inspected (not in task scope beyond read-only)

---

## 4. WINDOWS SERVICE CONTEXT

### NSSM configuration (FluxQuantumAPEX)

```
Application       : C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe
AppDirectory      : C:\FluxQuantumAI
AppParameters     : -u -Wignore run_live.py --execute --broker roboforex --lot_size 0.05
AppEnvironmentExtra : (empty)
ObjectName        : LocalSystem
```

Service runs as **LocalSystem** in **Session 0**, as expected. `--broker roboforex` is the sole broker flag on the command line — Hantec executor is imported as side-effect (via event_processor.py), but the primary broker arg is RoboForex.

### Event Log

Event Log query for Apr 17-19 returned no MT5/Python/terminal errors within the time window of investigation (no fatal faults logged).

### Dedicated Hantec service log

- `service_hantec_stderr.log` (2.72 MB) — last written **Apr 17 17:25**
- Last few lines show `GUARDRAIL STALE_DATA: latency=61-65s` (Hantec service was struggling with massive data staleness before stopping)
- Service `FluxQuantumAPEX_Live` has been **Stopped since Apr 17 17:25** (2+ days)

---

## 5. LAST FAILED EXECUTION — FULL DETAIL

### Sample (last FAILED, 2026-04-17T19:54:12.429 UTC)

```json
{
  "timestamp": "2026-04-17T19:54:12.429579+00:00",
  "action": "EXEC_FAILED",
  "direction": "SHORT",
  "execution": {
    "overall_state": "FAILED",
    "attempted": true,
    "brokers": [
      {
        "broker": "RoboForex",
        "account": null,
        "attempted": false,
        "result_state": "BROKER_DISCONNECTED",
        "ticket": null,
        "error_code": null,
        "error_text": "MT5 not connected"
      },
      {
        "broker": "Hantec",
        "account": null,
        "attempted": true,
        "result_state": "FAILED",
        "ticket": null,
        "error_code": null,
        "error_text": "All legs failed"
      }
    ],
    "updated_at": "2026-04-17T19:54:12.516859+00:00"
  }
}
```

**Error message sources confirmed:**
- `"MT5 not connected"` → `mt5_executor.py:250` (RoboForex, fires when `self.connected==False` after reconnect attempt)
- `"All legs failed"` → `mt5_executor_hantec.py` (Hantec, fires when all 3 `order_send` calls return non-success)

For Hantec, `attempted: true` but `error_code: null` — unusual. Typical MT5 order_send failures return a retcode (e.g., 10013 invalid request, 10019 no money, etc.). `null` retcode suggests the call returned `None` (MT5 library-level failure, not broker-level rejection) — consistent with the Hantec terminal not being running during Apr 17 19:24-19:54.

---

## 6. PYTHON / MT5 LIBRARY STATUS

Import test (no initialize call — preserving state):
```
MetaTrader5 lib imported OK
  __version__: 5.0.5640
  __file__:    C:\Users\...\site-packages\MetaTrader5\__init__.py
  initialize callable: True
  order_send callable: True
```

Library healthy, version 5.0.5640 (compatible with recent terminal builds).

---

## 7. ROOT-CAUSE HYPOTHESES (ranked)

### Hypothesis A — **Transient: no running broker terminals during Apr 17 19:24-19:54**
**Evidence FOR:**
- RoboForex terminal fresh start at 19:34 (10 min after FAILED began)
- Hantec terminal not running at all during window (service_hantec_stderr stopped Apr 17 17:25; next Hantec terminal boot Apr 18 12:15)
- Both brokers report identical failure patterns that match "no terminal to connect to" semantics

**Evidence AGAINST:**
- RoboForex terminal was authorized at 19:34:10 yet FAILED continued 20 more minutes until 19:54:12
- 20-min post-terminal-up failures cannot be explained by terminal absence alone

**Confidence:** HIGH for the first 10 min; partial explanation for remainder.

### Hypothesis B — **Latent bug: `mt5_executor_hantec.py` module-level init conflicts with RoboForex deferred init**
**Evidence FOR:**
- `mt5_executor_hantec.py` lines 86-92 — `_mt5.initialize()` at module scope
- `mt5_executor.py` has DEFERRED init with explicit comment about Session 0 segfault
- `event_processor.py` imports both executors (lines 65, 75)
- `MetaTrader5` module is a process-global singleton — one terminal connection at a time
- Import order matters: Hantec module init runs first (if terminal is up, Hantec grabs the singleton; if down, `MT5_AVAILABLE=False` for Hantec but the init attempt may leave the lib in partial state)
- Subsequent `_mt5.initialize(path=<RoboForex>)` call may silently fail because the lib was bound (or partially bound) to Hantec
- This would produce exactly the observed pattern: RoboForex "MT5 not connected" while Hantec also fails on actual order dispatch

**Evidence AGAINST:**
- Trades succeeded Mar 31 → Apr 10 with the same module-level-init Hantec executor — meaning dual-broker init was working previously
- But: Apr 14 20:49 refactor changed the RoboForex init timing — previously (pre-refactor) both had module-level init, which might have been benign via import-time ordering. Post-refactor, the ordering is different: Hantec at import, RoboForex at first use — this NEW interaction is untested.

**Confidence:** MEDIUM-HIGH as a LATENT CONTRIBUTOR, especially for the 20-min tail (19:34-19:54) after RoboForex terminal authorized yet still failed.

### Hypothesis C — Session 0 isolation
**Evidence FOR:**
- Service is LocalSystem Session 0
**Evidence AGAINST:**
- Terminal PIDs 8132 + 13156 are ALSO Session 0 — same session as service
- mt5_executor.py explicit comment addresses Session 0 already (deferred init was the fix)

**Confidence:** LOW (not the primary cause).

### Hypothesis D — Credential/.env issue
**Evidence FOR / AGAINST:**
- `.env` last modified Apr 14 20:43, not touched since
- Trades worked through Apr 10 with same credentials — but mt5_executor.py was modified Apr 14 20:49 AFTER last successful trade
- deploy-staging has a differently-sized .env (832 vs 817 bytes) but it's in staging, not production

**Confidence:** LOW-MEDIUM (possible but not confirmed).

### Hypothesis E — Apr 17 16:42 deploy broke something
**Evidence FOR:**
- Batch of 4 files deployed Apr 17 16:42 (base_dashboard_server, d1_h4_updater, level_detector, tick_breakout_monitor)
- Service restart likely followed this deploy (consistent with first NOT_ATTEMPTED at 16:18)
- Actually NOT_ATTEMPTED started at 16:18, BEFORE 16:42 deploy — so deploy is unlikely direct cause

**Evidence AGAINST:**
- None of the 4 deployed files touch MT5/execution

**Confidence:** LOW.

---

## 8. RECOMMENDED NEXT STEPS (for Claude + Barbara review)

**All below are proposals. No fix attempted in this investigation.**

### Before Monday market open

**Priority order based on Hypothesis A + B being primary:**

1. **Verify current state (live diagnostic, no changes):**
   - Check if FluxQuantumAPEX service's current instance (PID 9552, started today 15:10:44) has a working MT5 connection by inspecting `service_stderr.log` for startup messages from `mt5_executor` or `Hantec MT5 connected`
   - If no such messages: executor is silently in `NOT_CONNECTED` state right now

2. **Fix `mt5_executor_hantec.py` module-level init (P0 code change):**
   - Refactor to deferred init, mirroring `mt5_executor.py`
   - Remove module-level `_mt5.initialize()` (lines 86-92)
   - Move init into `reconnect()` method
   - Estimated effort: 15-30 min, low risk (pattern already proven in RoboForex executor)

3. **Ensure broker terminals are running BEFORE service start:**
   - RoboForex terminal autostart via Task Scheduler in Session 0
   - Hantec terminal autostart in Session 0 (current 8132 is already in Session 0, good)
   - Or: add startup delay to FluxQuantumAPEX service (NSSM AppStartupDelay) so terminals come up first

4. **Smoke test Monday:**
   - Open market, Monday 22h UTC
   - First GO signal → observe decision_log for execution block
   - If `result_state=SUCCESS` → resolved
   - If `result_state=FAILED` with same errors → apply Hypothesis B fix

### Not recommended

- Do not do fresh `mt5.initialize()` in diagnostic mode — may reset active terminal session states
- Do not restart FluxQuantumAPEX_Live (Hantec) without fixing module-level init first (will fail same way)

### Secondary (long-term)

- Move broker credentials from `.env` to Windows Credential Manager (removes plaintext file risk)
- Add a `--dry_run --broker_smoke_test` mode to run_live.py that exercises mt5 init + account_info + symbol_info without sending orders, logs results explicitly

---

## 9. SYSTEM STATE DURING INVESTIGATION

- Service FluxQuantumAPEX: **Running** (PID 16956 nssm wrapper → PID 9552 python, SessionId=0, StartTime 2026-04-19 15:10:44)
- FluxQuantumAPEX_Live: Stopped (since Apr 17 17:25)
- Capture processes: **3/3 intact** (PIDs 12332, 8248, 2512 — NOT touched)
- MT5 terminals: 3 running (PIDs 5016, 8132, 13156 — NOT touched)
- Files modified during investigation: **ZERO**
- Restarts performed: **ZERO**
- `mt5.initialize()` calls during investigation: **ZERO** (import-only test)

---

## 10. SEPARATE USER ISSUE (NOT part of this investigation)

Barbara is currently receiving Telegram alerts like:
```
⚠️ DEFENSE MODE — ENTRY_BLOCK
Microstructure anomaly detected.
Trigger: spread_widening(z=61.41)
Action: All entries blocked until resolved.
```

These are **weekend false-positives**: the GC market is closed (Sunday), spreads widen massively without liquidity, and the DefenseMode baseline is calibrated to market-hours spread distribution. Z=61.41 reflects this mismatch, not a genuine anomaly. The alerts should cease once market reopens Monday 22h UTC. Optionally: temporarily silence Telegram Defense Mode notifications until open (no code change needed), or add market-hours check to DefenseMode in a future sprint.

---

**Report:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\MT5_EXECUTION_BREAK_INVESTIGATION.md`

**NO EDITS PERFORMED. ALL READ-ONLY.**
Aguardo Claude + Barbara review antes de qualquer fix.

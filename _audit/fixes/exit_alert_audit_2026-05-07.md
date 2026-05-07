# Exit Alert Audit — Telegram emission paths post-cascade

**Author**: CC#3
**Date**: 2026-05-07 ~00:20 UTC
**Asana**: 1214556369070092 (BUG-SIGNAL-INVERTED) — follow-up audit
**Type**: READ-ONLY architectural audit
**Trigger**: Barbara directive after Telegram reactivation
**Source**: `live/telegram_notifier.py`, `live/event_processor.py`, `live/position_monitor.py`, `live/macro_monitor.py`

---

## Executive summary

Telegram now LIVE (kill switch removed, FluxQuantumAPEX restarted PID 17964). Cascade audit log shows **4,574 [STRATEGY] events** in production stdout — including verified `[STRATEGY] SKIP (RANGE_BOUND_BIAS_BLOCK: counter-bull SHORT blocked (resolved=long via tick_breakout_monitor HIGH))` confirming F-asymmetric is working.

**Original audit framing (CORRECTED below)**: I initially flagged "exit alert emission gap" because 4 functions in `telegram_notifier.py` (`notify_sl_hit`/`notify_trailing_update`/`notify_risk_exit`/`notify_trade_closed`) have ZERO callers.

**Correction (per Barbara directive)**: those 4 functions are **LEGACY MT5-era orphans**. The actual solution Barbara requested (PM-DECOUPLE + Telegram routing) IS implemented and IS live in production via:

- **PM-DECOUPLE-PHASE3** commit `889f5ee` (2026-04-26) — Position monitor read-path decoupled from MT5 (Asana 1214284177814516)
- **MACRO-MONITOR-VAP** commit `21bfac3` (2026-04-27) — Phase 1+2 (advisory emission + Telegram routing) — `live/macro_monitor.py` with 7 broker-independent exit triggers and `tg.notify_macro_exit` Telegram emission at line 515
- Both deployed in Cluster 3 (commit f17bc49) and currently RUNNING (MacroMonitor active in PID 17964)
- Active VAP at audit time: `9a9d381c` LONG @ 4671.9 entry 22:12:18 UTC, status=HELD, fired_triggers=[]

**Reference doc**: `_audit/pp_sprint/macro_monitor/MACRO_MONITOR_DESIGN_v1.md` (2026-04-27) — full design with methodology citations per trigger.

The original "gap" report below was scoped only to the legacy MT5 functions; the broker-independent advisory path is operational.

---

## 1. Telegram emission inventory

### 1.1 Notification functions DEFINED (`live/telegram_notifier.py`, 991 lines)

**Entry / decision (5 fns)**:
- `notify_decision` (line 68) — reads `decision_live.json`, anti-spam by decision_id
- `notify_execution` (235)
- `notify_entry_go` (294)
- `notify_entry_block` (299)
- `notify_logic_c_signal` (810)

**Exit / risk (5 fns) — TARGET OF THIS AUDIT**:
- `notify_sl_hit` (331) — SL fired, with PnL
- `notify_trailing_update` (342) — trailing SL adjusted
- `notify_risk_exit` (509) — momentum reversal + contra iceberg
- `notify_trade_closed` (779) — final closure with PnL
- `notify_macro_exit` (929) — VAP exit (MacroMonitor)
- `notify_macro_additional` (967) — VAP additional alerts

**Operational (8 fns)**:
- `notify_news_event`, `notify_defense_mode`, `notify_feed_dead`, `notify_feed_recovered`, `notify_iceberg_support`, `notify_iceberg_alert`, `notify_anomaly_support`, `notify_anomaly_alert`, `notify_stay_in_trade`, `notify_health_check`, `notify_startup`, `notify_status`, `notify_daily_report`, `notify_generic`

### 1.2 Notification functions CALLED (in production code)

`grep -rn "tg\.notify_\|notify_*\(" C:/FluxQuantumAI/live/`:

| Function | Caller | Purpose |
|----------|--------|---------|
| `notify_decision` | event_processor.py:1407, 2803, 2810 | Entry signals via canonical flow |
| `notify_execution` | event_processor.py:2959, 3031, 4180, 4478 | Execution result post-trade |
| `notify_defense_mode` | event_processor.py:2529 | Defense tier change |
| `notify_health_check` | event_processor.py:913 | Periodic |
| `notify_logic_c_signal` | event_processor.py:1808 | Logic C trigger |
| `notify_macro_exit` | macro_monitor.py:515 | VAP exit (TP1/SL/expiry) |

**Functions DEFINED but NEVER CALLED**:

| Function | Status |
|----------|--------|
| `notify_sl_hit` | ❌ ZERO callers in production code |
| `notify_trailing_update` | ❌ ZERO callers |
| `notify_risk_exit` | ❌ ZERO callers |
| `notify_trade_closed` | ❌ ZERO callers |
| `notify_news_event` | ❌ ZERO callers (NEWS_EXIT goes via PM_EVENT instead) |
| `notify_feed_dead` / `notify_feed_recovered` | ❌ ZERO callers |
| `notify_iceberg_support` / `notify_iceberg_alert` / `notify_anomaly_*` | ❌ ZERO callers |
| `notify_stay_in_trade` | ❌ ZERO callers |
| `notify_daily_report` / `notify_status` / `notify_startup` | ❌ ZERO callers |

---

## 2. Exit event flow — what actually happens

### 2.1 Position monitor emits PM_EVENTs (NOT Telegram)

`live/position_monitor.py` emits 8+ `_emit_position_event(...)` calls:

| Line | Event type | Trigger |
|------|-----------|---------|
| 1071 | SHIELD | TP1 hit → move SL to breakeven |
| 1124 | (similar) | continuation |
| 1677 | L2_DANGER_EXIT | delta_4h flip threshold |
| 1696 | (related) | L2 danger continuation |
| 1768 | CASCADE_EXIT | price move > N*ATR against position |
| 1783 | T3_EXIT | T3 defense exit (anomaly + adverse + M30 break) |
| 1796 | REGIME_FLIP_EXIT | regime flipped vs position |

These write to `decision_log.jsonl` and are visible via dashboard endpoint `/api/pm_events` — but **no Telegram emission** to operator.

### 2.2 Comparison: NEWS_EXIT goes via canonical PM_EVENT flow

`event_processor.py:1354` documents the alternative path: 
```
# === Fase 4 Scope B.2: Emit PM_EVENT NEWS_EXIT via canonical flow ===
# Triggers Telegram notification with 📰 NEWS_EXIT icon (Fase 2 M6.3 map).
```

This implies "canonical flow" SHOULD route PM_EVENTs → Telegram. But:
- The flow only emits NEWS_EXIT (1 event type)
- Other PM_EVENTs (SHIELD/L2_DANGER/CASCADE/T3/REGIME_FLIP) bypass canonical Telegram
- `notify_decision()` reads `decision_live.json`, NOT `decision_log.jsonl` PM_EVENTs

### 2.3 What Barbara actually receives via Telegram today

| Event | Telegram alert? | Source |
|-------|-----------------|--------|
| Entry GO signal | ✅ YES | `notify_decision` from decision_live.json |
| Entry BLOCK | ✅ YES (if ENTRY_BLOCK action) | same |
| Execution result (open/fail) | ✅ YES | `notify_execution` |
| **SL hit** | ❌ **NO** | PM_EVENT to decision_log only |
| **TP1 hit (SHIELD activation)** | ❌ **NO** | PM_EVENT only |
| **Trailing stop update** | ❌ **NO** | PM_EVENT only |
| **L2 Danger exit** | ❌ **NO** | PM_EVENT only |
| **Cascade exit** | ❌ **NO** | PM_EVENT only |
| **T3 defense exit** | ❌ **NO** | PM_EVENT only |
| **Regime flip exit** | ❌ **NO** | PM_EVENT only |
| **Trade closure final PnL** | ❌ **NO** | PM_EVENT only |
| **News event exit** | ✅ YES | event_processor.py:1407 NEWS_EXIT canonical flow |
| **VAP macro exit** | ✅ YES | macro_monitor.py:515 `tg.notify_macro_exit` |
| Defense mode change | ✅ YES | event_processor.py:2529 |
| Health check periodic | ✅ YES | event_processor.py:913 |
| Logic-C signal | ✅ YES | event_processor.py:1808 |

---

## 3. The gap

**Architectural defect**: position_monitor exit detection does not propagate to Telegram. 5 of the most operationally relevant alerts for a trader (TP1, SL, trailing, risk-exit, trade-closed) are silent on Telegram.

**Likely history**:
- Designed for MT5 era when exit was tracked via MT5 deal history
- Refactored into PM_EVENT canonical flow (Fase 4 Scope B.2 for NEWS_EXIT)
- Other PM_EVENT types never got the Telegram routing wired
- `notify_sl_hit` etc. left as orphan functions (similar to TickBreakoutMonitor.status() orphan pattern in main bug)

**Current operational impact**:
- mt5 disconnected → no real trades → no actual SL/TP hits to alert on
- Barbara operates manual via Quantower → she sees TP/SL from her Quantower terminal directly
- Dashboard shows PM_EVENTs in `/api/pm_events` for operator review

**Future operational impact** (when MT5/cTrader reconnects, real trades fire):
- Exits will be silent on Telegram
- Operator must check dashboard or decision_log to know SL hit
- Increased latency between exit and operator awareness

---

## 4. Severity & priority

| Severity | Why |
|----------|-----|
| **MEDIUM** (currently) | mt5 disconnected — no real trades to exit. PM_EVENTs still log correctly. |
| **HIGH** (when broker reconnects) | Operator awareness lag if real trades close without alert |

**Compared to the BUG-SIGNAL-INVERTED**: this gap is FAR less severe — entry signals route correctly, only post-entry exit notifications are missing. The cascade fix already deployed handles direction integrity at entry; this audit identifies a separate operational gap.

---

## 5. Recommendations (decision input — NO implementation in this audit)

### Option A — Wire missing notify_* calls in position_monitor

Add `tg.notify_*` calls inside `_emit_position_event()` or right after each PM_EVENT emission:

```python
# Pseudo:
self._emit_position_event(event_type="SHIELD", ...)
tg.notify_trailing_update(direction, new_sl, current_price)  # NEW

self._emit_position_event(event_type="L2_DANGER_EXIT", ...)
tg.notify_risk_exit(direction, reason="L2 danger")  # NEW

self._emit_position_event(event_type="CASCADE_EXIT", ...)
tg.notify_risk_exit(direction, reason="Cascade")  # NEW

# etc.
```

Risk: ~15 LoC across position_monitor (wraps with try/except), reuses existing notify_* fns.

### Option B — Extend canonical PM_EVENT flow

Modify the canonical flow that already routes NEWS_EXIT (event_processor.py:1354) to handle ALL PM_EVENT types. Single integration point, more robust.

Risk: requires understanding the "Fase 4 Scope B.2" routing layer (likely 30-40 LoC).

### Option C — Defer (today's defect deferred until broker reconnect)

Currently mt5 disconnected → no real exit events. Defer fix until broker reconnect is in scope.

Risk: gap remains; when broker reconnects this becomes urgent.

---

## 6. Constraints honored

- ✅ READ-ONLY audit (no mutation outside this file + telegram_notifier.py kill-switch revert)
- ✅ ZERO ATS Trend Line / NextGen / TradeATS / CC#1 / CC#2 references
- ✅ ZERO touch to MT5 broker code (already disabled per US-1.3)
- ✅ ZERO halt
- ✅ Spec writes confined to `_audit/fixes/`

---

## 7. Companion files

- `audit_forense_2026-05-06_today_signals.md` — entry signal audit (today's burst)
- `bug_cascade_direction_fallback_spec.md` — cascade design spec
- `2026-05-06_deploy_validation.md` — deploy validation
- This file — exit alert audit

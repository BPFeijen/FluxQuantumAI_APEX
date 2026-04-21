# DESIGN DOC — Telegram Decoupling + Position Events Full Integration

**Status:** DRAFT v1.0 — awaiting Barbara approval before implementation
**Date:** 2026-04-17
**Owner:** Barbara (PO)
**Author:** Claude (ML/AI Engineer)
**Implementer:** ClaudeCode (under supervision + Claude independent audit)
**Methodology ref:** GOLDEN RULE — no code without approved design doc + independent audit
**Estimated effort:** 20-26 hours ClaudeCode implementation + 3-4 hours Claude audit + 2 hours Barbara validation
**Target deployment:** Monday 2026-04-21 (realistic given scope)

---

## EXECUTIVE SUMMARY

This design addresses systematic failure of FluxQuantumAI to communicate critical trading events to Barbara via Telegram. Evidence from decision_log.jsonl (2026-04-17) shows:
- 288 GO decisions → **0 entry signals received**
- Many PM_EVENTs written to canonical log → **0 position event notifications received**
- System Running but silent on trading intent

**Root cause (two layers):**

**Layer 1 (architectural):** Telegram notifications coupled to broker execution. If MT5 fails, Barbara gets no GO signal.

**Layer 2 (semantic):** Multiple events detected by `position_monitor.py` write to canonical log but **never notify Telegram** because `tg.notify_decision()` is not called after `_publish_canonical_pm_event()`. Additionally, several critical events (TP2, SL, news exit, L2 DANGER, pullback START) are **not even detected**.

This design fixes both layers with surgical, well-validated changes.

---

## SCOPE

**Scope A — Decouple Telegram from Broker Execution (~6 hours)**
1. Move `notify_decision()` calls to fire immediately on GO decision (not wait for MT5)
2. Add separate `notify_execution()` for broker confirmation
3. Fix semantic bug in `telegram_notifier.py` (BLOCK hardcoded when action is GO)
4. Add `notify_decision()` calls after each `_publish_canonical_pm_event()` in position_monitor

**Scope B — Build Missing Detections + Notifications (~15-20 hours)**
5. Implement MT5 deal history polling (detects TP2, SL, manual closes, system closes)
6. Integrate news gate with position manager (auto-exit 30min pre-news)
7. Emit position event for L2 DANGER (currently silent)
8. Detect and notify PULLBACK START separately (currently only END is notified)
9. Format all Telegram messages per Barbara's approved style

---

## SCOPE A — DETAILED DESIGN

### Problem A.1 — Telegram coupled to broker execution

**Current flow (event_processor.py lines 2316-2584):**

```
Line 2316: verdict = "GO" if decision.go else "BLOCK"
Line 2359: _write_decision()                             # GO written to log
Line 2361: if not decision.go:
Line 2363:     tg.notify_decision()                     # only BLOCK notifies here
Line 2365: (GO continues to MT5 execution)
Line 2483: _exec_report = self.executor.execute()
Line 2502: if success:
Line 2509:     action = "EXECUTED"
Line 2513:     tg.notify_decision()                     # GO notifies ONLY after MT5 OK
Line 2573: else:
Line 2575:     action = "EXEC_FAILED"
Line 2584:     tg.notify_decision()                     # GO notifies ONLY after MT5 FAIL
```

**Fix A.1 — event_processor.py modifications**

**Change 1/3 — Add GO signal notification BEFORE execution (line 2365)**

**Current:**
```python
if not decision.go:
    print(f"[{ts}] BLOCK: {decision.reason}")
    tg.notify_decision()
    return

# Gate passed (GO) — lock per-direction cooldown so only GO resets it
with self._lock:
    self._last_trigger_by_dir[direction] = time.monotonic()
```

**Proposed:**
```python
if not decision.go:
    print(f"[{ts}] BLOCK: {decision.reason}")
    tg.notify_decision()
    return

# === Telegram Decoupling: notify GO signal BEFORE execution ===
# Signal is independent of broker. Barbara receives immediately.
print(f"[{ts}] GO SIGNAL: {decision.reason}")
tg.notify_decision()

# Gate passed (GO) — lock per-direction cooldown so only GO resets it
with self._lock:
    self._last_trigger_by_dir[direction] = time.monotonic()
```

**Change 2/3 — EXECUTED branch uses notify_execution (line 2513)**

**Current:**
```python
_decision_dict["decision"]["action"] = "EXECUTED"
self._write_decision(_decision_dict)
tg.notify_decision()
```

**Proposed:**
```python
_decision_dict["decision"]["action"] = "EXECUTED"
self._write_decision(_decision_dict)

# Separate execution confirmation message
tg.notify_execution()
```

**Change 3/3 — EXEC_FAILED branch uses notify_execution (line 2584)**

**Current:**
```python
_decision_dict["decision"]["action"] = "EXEC_FAILED"
# ... reason set ...
self._write_decision(_decision_dict)
log.error("EXEC_FAILED: ...")
print(f"[{ts}] EXEC_FAILED: ...")
tg.notify_decision()
```

**Proposed:**
```python
_decision_dict["decision"]["action"] = "EXEC_FAILED"
# ... reason set ...
self._write_decision(_decision_dict)
log.error("EXEC_FAILED: ...")
print(f"[{ts}] EXEC_FAILED: ...")

# Separate execution failure message
tg.notify_execution()
```

**Also apply same pattern to lines 3623 and 3920 (GAMMA and DELTA strategy branches):**
Both already have same structure, need same decoupling.

---

### Problem A.2 — Semantic bug BLOCK vs GO in telegram_notifier.py

**Current code (lines 156-183):**
```python
elif action in ("BLOCK", "GO"):
    # BLOCK or GO (not executed)
    blocked_by = ""
    for gname, gkey in [...]:
        gs = gates.get(gkey, {}).get("status", "")
        if gs and gs.upper() in ("BLOCK", "ZONE_FAIL"):
            blocked_by = gname
            break

    text = (
        f"\u26D4 <b>BLOCK \u2014 {direction}</b>\n"   # ⛔ always "BLOCK"
        ...
    )
```

**Bug:** action="GO" gets "BLOCK" label. Currently masked because notify_decision() not called for pure GO — but after Fix A.1, it WILL be called.

**Fix A.2 — Separate branches + new notify_execution()**

**Full refactor of `telegram_notifier.py`:**

```python
# Module-level (add near _last_decision_id)
_last_execution_id = ""


def notify_decision() -> bool:
    """
    Notify Telegram of DECISION event (GO or BLOCK or PM_EVENT).
    Independent of broker execution.
    """
    global _last_decision_id
    dl = _read_json(_DECISION_LIVE_PATH)
    if not dl:
        return False

    dec = dl.get("decision", {})
    dec_id = dl.get("decision_id", "")
    action = dec.get("action", "")

    # Anti-spam: don't resend same decision
    if dec_id == _last_decision_id:
        return False
    _last_decision_id = dec_id

    direction = dec.get("direction", "?")
    price_mt5 = dl.get("price_mt5", 0)
    score = dec.get("total_score", 0)
    reason = dec.get("reason", "")
    ts_str = dl.get("timestamp", "")
    ts_display = ts_str[11:19] + " UTC" if len(ts_str) > 19 else ""

    ctx = dl.get("context", {})
    ss = _read_json(_SERVICE_STATE_PATH) or {}
    phase = ctx.get("phase", ss.get("phase", "?"))
    bias = ctx.get("m30_bias", ss.get("m30_bias", "?"))
    d4h = ctx.get("delta_4h", ss.get("delta_4h", 0))

    sl = dec.get("sl", 0)
    tp1 = dec.get("tp1", 0)
    tp2 = dec.get("tp2", 0)

    # === GO — signal emitted, ready for execution ===
    if action == "GO":
        text = (
            f"\U0001F3AF <b>GO \u2014 {direction}</b>\n"
            f"{price_mt5:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f} | Runner: ON\n"
            f"Score: {score:+d} | Reason: {reason}\n"
            f"Phase: {phase} | Bias: {bias} | \u03944h: {d4h:+.0f}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    # === BLOCK — gate rejected entry ===
    elif action == "BLOCK":
        blocked_by = ""
        gates = dl.get("gates", {})
        for gname, gkey in [("V1", "v1_zone"), ("V2", "v2_l2"),
                             ("V3", "v3_momentum"), ("V4", "v4_iceberg")]:
            gs = gates.get(gkey, {}).get("status", "")
            if gs and gs.upper() in ("BLOCK", "ZONE_FAIL"):
                blocked_by = gname
                break
        blocked_by_str = f" (by {blocked_by})" if blocked_by else ""

        text = (
            f"\u26D4 <b>BLOCK \u2014 {direction}</b>\n"
            f"{price_mt5:.2f}{blocked_by_str}\n"
            f"Reason: {reason}\n"
            f"Phase: {phase} | Bias: {bias} | \u03944h: {d4h:+.0f}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    # === PM_EVENT — position event from position_monitor ===
    elif action == "PM_EVENT":
        pe = dl.get("position_event", {})
        event_type = pe.get("event_type", "?")
        direction_affected = pe.get("direction_affected", "UNKNOWN")
        action_type = pe.get("action_type", "UNKNOWN")
        pm_reason = pe.get("reason", "")
        exec_state = pe.get("execution_state", "UNKNOWN")
        broker = pe.get("broker", "UNKNOWN")
        ticket = pe.get("ticket", "?")
        result = pe.get("result", "")

        # Icon based on event type
        icon_map = {
            "SHIELD": "\U0001F6E1",          # 🛡
            "TP1_HIT": "\u2705",             # ✅
            "TP2_HIT": "\U0001F3C6",         # 🏆
            "SL_HIT": "\U0001F6D1",          # 🛑
            "REGIME_FLIP": "\U0001F504",     # 🔄
            "PULLBACK_START": "\u21A9",      # ↩
            "PULLBACK_END_EXIT": "\u21AA",   # ↪
            "L2_DANGER": "\u26A0",           # ⚠
            "T3_EXIT": "\U0001F6A8",         # 🚨
            "NEWS_EXIT": "\U0001F4F0",       # 📰
        }
        icon = icon_map.get(event_type, "\U0001F6E0")  # 🛠 default

        text = (
            f"{icon} <b>{event_type} \u2014 {direction_affected}</b>\n"
            f"Action: {action_type}\n"
            f"Reason: {pm_reason}\n"
            f"Broker: {broker} | Ticket: #{ticket}\n"
            f"Exec: {exec_state} | Result: {result}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    else:
        log.warning("notify_decision: unexpected action=%s", action)
        return False

    _send_async(text)
    return True


def notify_execution() -> bool:
    """
    Notify Telegram of EXECUTION event (EXECUTED or EXEC_FAILED).
    Separate message after broker responds.
    """
    global _last_execution_id

    dl = _read_json(_DECISION_LIVE_PATH)
    if not dl:
        return False

    dec = dl.get("decision", {})
    dec_id = dl.get("decision_id", "")
    action = dec.get("action", "")

    # Anti-spam
    if dec_id == _last_execution_id:
        return False
    _last_execution_id = dec_id

    direction = dec.get("direction", "?")
    price_mt5 = dl.get("price_mt5", 0)
    ts_str = dl.get("timestamp", "")
    ts_display = ts_str[11:19] + " UTC" if len(ts_str) > 19 else ""

    # === EXECUTED ===
    if action == "EXECUTED":
        exec_info = dec.get("execution", {})
        brokers = exec_info.get("brokers", [])
        ok_broker = "?"
        ticket = "?"
        for b in brokers:
            if b.get("result_state") == "EXECUTED" or b.get("state") == "EXECUTED":
                ok_broker = b.get("broker") or b.get("name", "?")
                ticket = b.get("ticket", "?")
                break

        text = (
            f"\u2705 <b>ORDER OPENED \u2014 {direction} @ {price_mt5:.2f}</b>\n"
            f"Broker: {ok_broker} | Ticket: #{ticket}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    # === EXEC_FAILED ===
    elif action == "EXEC_FAILED":
        reason = dec.get("reason", "")
        text = (
            f"\u274C <b>ORDER FAILED \u2014 {direction}</b>\n"
            f"{reason}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    else:
        log.warning("notify_execution: unexpected action=%s", action)
        return False

    _send_async(text)
    return True
```

---

### Problem A.3 — Position events not notified

**Evidence:** position_monitor.py calls `_publish_canonical_pm_event()` in 7 places (lines 864, 1429, 1457, 1529, 1544, 1557, plus others), but **only line 1442** calls Telegram directly (T3 defense exit). All other events write to canonical log but never notify Barbara.

**Fix A.3 — Add notify_decision() calls in position_monitor.py**

**Change to `_publish_canonical_pm_event()` function (line 1973):**

Append at end of function (after successful write):

```python
def _publish_canonical_pm_event(self, event_payload: dict) -> None:
    """Publish PositionMonitor event into canonical decision outputs."""
    # ... existing code ...
    try:
        DECISION_LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with self._canonical_lock:
            tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(decision_payload, f, indent=2, default=str)
            tmp.replace(DECISION_LIVE_PATH)
            with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(decision_payload, default=str) + "\n")

        # === NEW: notify Telegram after canonical write succeeds ===
        try:
            from live import telegram_notifier as tg
            tg.notify_decision()
        except Exception as _e:
            log.debug("telegram notify after PM_EVENT failed: %s", _e)

    except Exception as e:
        log.debug("canonical PM publish failed: %s", e)
```

**Why inside `_publish_canonical_pm_event`:** centralized. All PM events route through this function. One line added = all PM events notify.

**Also: remove the direct `tg._send_async` in line 1442** (T3 exit) since it will now be handled by the canonical flow. Prevents double-notification for T3 events.

---

## SCOPE B — DETAILED DESIGN

### Problem B.1 — TP2 hit / SL hit / manual close not detected

**Current state:** When MT5 closes a position (TP, SL, or manual), the position simply disappears from `get_open_positions()`. Position monitor doesn't explicitly detect or attribute the cause.

**Solution: B1 unified — MT5 deal history polling**

**Design:** Poll `mt5.history_deals_get()` when position count drops. Parse `DEAL_REASON` for each fill.

**New file:** `live/mt5_history_watcher.py`

```python
"""
MT5 Deal History Watcher
Detects position closures and classifies them (TP, SL, manual, system).
Called by position_monitor when position count drops.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import MetaTrader5 as mt5

log = logging.getLogger(__name__)


class MT5HistoryWatcher:
    """
    Classifies closed positions via MT5 deal history.

    Usage:
        watcher = MT5HistoryWatcher(executor)
        # When position monitor detects count drop:
        closed_deals = watcher.find_closed_since(last_check_ts)
        for deal in closed_deals:
            if deal.reason_classified == "TP":
                # emit TP event
    """

    # MT5 deal reason codes (MetaTrader5.DEAL_REASON_*)
    REASON_CLIENT = 0           # manual close from terminal
    REASON_MOBILE = 1           # manual close from mobile
    REASON_WEB = 2              # manual close from web
    REASON_EXPERT = 3           # closed by EA (our code)
    REASON_SL = 4               # stop loss hit
    REASON_TP = 5               # take profit hit
    REASON_SO = 6               # stop out (margin call)
    REASON_ROLLOVER = 7         # rollover
    REASON_VMARGIN = 8          # variation margin
    REASON_SPLIT = 9            # split

    REASON_LABELS = {
        REASON_CLIENT: "MANUAL_TERMINAL",
        REASON_MOBILE: "MANUAL_MOBILE",
        REASON_WEB: "MANUAL_WEB",
        REASON_EXPERT: "SYSTEM_CLOSE",
        REASON_SL: "SL_HIT",
        REASON_TP: "TP_HIT",
        REASON_SO: "STOP_OUT",
        REASON_ROLLOVER: "ROLLOVER",
        REASON_VMARGIN: "VMARGIN",
        REASON_SPLIT: "SPLIT",
    }

    def __init__(self, executor, magic_number: int):
        self.executor = executor
        self.magic = magic_number
        self._last_check_ts = datetime.now(timezone.utc) - timedelta(minutes=5)

    def find_closed_since_last_check(self) -> list[dict]:
        """
        Return list of deals closed since last check.
        Each deal dict has:
          - ticket (int)
          - position_id (int)
          - symbol (str)
          - type ("BUY" / "SELL")
          - volume (float)
          - price (float)
          - profit (float)
          - reason_code (int)
          - reason_label (str): "TP_HIT" | "SL_HIT" | etc.
          - time (datetime UTC)
          - comment (str)
        """
        from_date = self._last_check_ts
        to_date = datetime.now(timezone.utc)

        deals = mt5.history_deals_get(from_date, to_date)
        if deals is None:
            log.debug("history_deals_get returned None")
            return []

        result = []
        for d in deals:
            # Filter: only our deals (by magic)
            if d.magic != self.magic:
                continue
            # Filter: only exit deals (entry=1 means out)
            if d.entry != 1:
                continue

            reason_code = int(d.reason)
            result.append({
                "ticket": d.ticket,
                "position_id": d.position_id,
                "symbol": d.symbol,
                "type": "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL",
                "volume": float(d.volume),
                "price": float(d.price),
                "profit": float(d.profit),
                "reason_code": reason_code,
                "reason_label": self.REASON_LABELS.get(reason_code, f"UNKNOWN_{reason_code}"),
                "time": datetime.fromtimestamp(d.time, tz=timezone.utc),
                "comment": d.comment,
            })

        self._last_check_ts = to_date
        log.debug("history check: %d deals classified", len(result))
        return result
```

**Integration in position_monitor.py `run()` loop:**

Add at top of `PositionMonitor.__init__`:
```python
from live.mt5_history_watcher import MT5HistoryWatcher
from mt5_executor import MAGIC  # existing import
self._history_watcher = MT5HistoryWatcher(self.executor, MAGIC)
self._last_position_count = 0
```

Add to main `check()` loop (~line 540, before existing checks):
```python
# === NEW: Detect closed positions via MT5 deal history ===
current_positions = self.executor.get_open_positions() or []
current_count = len(current_positions)

if current_count < self._last_position_count:
    # Position closed — check MT5 history to classify
    closed_deals = self._history_watcher.find_closed_since_last_check()
    for deal in closed_deals:
        self._handle_closed_deal(deal)

self._last_position_count = current_count
```

Add new method `_handle_closed_deal`:
```python
def _handle_closed_deal(self, deal: dict) -> None:
    """Classify closed deal and emit position event accordingly."""
    ts = _ts()
    reason_label = deal["reason_label"]
    ticket = deal["ticket"]
    price = deal["price"]
    profit = deal["profit"]
    direction = "LONG" if deal["type"] == "SELL" else "SHORT"  # exit SELL = closing LONG

    # Map reason to event_type
    event_type_map = {
        "TP_HIT": "TP_HIT",           # but we need to distinguish TP1 vs TP2
        "SL_HIT": "SL_HIT",
        "MANUAL_TERMINAL": "MANUAL_CLOSE",
        "MANUAL_MOBILE": "MANUAL_CLOSE",
        "MANUAL_WEB": "MANUAL_CLOSE",
        "SYSTEM_CLOSE": None,  # skip — our code already emitted event
        "STOP_OUT": "STOP_OUT",
    }
    event_type = event_type_map.get(reason_label)
    if event_type is None:
        return  # system close already handled

    # Distinguish TP1 vs TP2 for TP_HIT
    if event_type == "TP_HIT":
        # TP1 is typically smaller leg closed first
        # If this deal volume matches leg1 calibration (e.g. 0.01-0.03), it's TP1
        # Otherwise TP2 (Leg2 or Runner)
        # Implementation: match against trades.csv record
        trade_rec = self._find_trade_by_ticket(deal["position_id"])
        if trade_rec:
            if int(trade_rec.get("leg1_ticket", 0)) == ticket:
                event_type = "TP1_HIT"
            elif int(trade_rec.get("leg2_ticket", 0)) == ticket:
                event_type = "TP2_HIT"
            elif int(trade_rec.get("leg3_ticket", 0)) == ticket:
                event_type = "TP2_HIT"  # runner also reports as TP2 for simplicity
        else:
            event_type = "TP_HIT"  # fallback

    # Emit position event (goes through canonical flow + Telegram)
    self._emit_position_event(
        event_type=event_type,
        direction=direction,
        ticket=ticket,
        reason=f"{reason_label} @ {price:.2f} | pnl={profit:+.2f}",
        action_taken="CLOSED_BY_BROKER",
        result=f"profit={profit:+.2f}",
        attempted=True,
        execution_state="EXECUTED",
        broker="RoboForex" if not self.executor.is_live else "Hantec",
    )


def _find_trade_by_ticket(self, position_id: int) -> Optional[dict]:
    """Find trade record where any leg matches the given ticket."""
    trades = _load_trades()
    for t in trades:
        for key in ("leg1_ticket", "leg2_ticket", "leg3_ticket"):
            if int(t.get(key, 0) or 0) == position_id:
                return t
    return None
```

---

### Problem B.2 — High-impact news exit not integrated

**Tua regra operacional:** fechar tudo 30min antes, novas posições 30min depois.

**Solution: C2 — event_processor signals position_monitor via service_state**

**Part 1: event_processor.py adds news signalling**

When ApexNewsGate score crosses threshold (e.g. >= 0.70) AND event is within 30min:
```python
# Inside gate check, when news gate blocks:
if _ng and _ng.score >= 0.70 and _ng.seconds_to_event <= 1800:
    # Signal position manager to exit ALL positions
    self._write_news_exit_signal(
        event_type=_ng.event_type,
        seconds_until=_ng.seconds_to_event,
    )


def _write_news_exit_signal(self, event_type: str, seconds_until: int) -> None:
    """Signal position monitor to close all positions due to upcoming high-impact news."""
    try:
        state = _read_json(SERVICE_STATE_PATH) or {}
        state["news_exit_alert"] = {
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "seconds_until_event": seconds_until,
            "acknowledged": False,
        }
        with open(SERVICE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log.error("failed to write news_exit_alert: %s", e)
```

**Part 2: position_monitor.py reads signal and acts**

In main `check()` loop:
```python
# === NEW: Check for news exit signal ===
ss = _read_json(SERVICE_STATE_PATH) or {}
news_alert = ss.get("news_exit_alert")
if news_alert and not news_alert.get("acknowledged", False):
    self._handle_news_exit(news_alert)


def _handle_news_exit(self, alert: dict) -> None:
    """Close all positions due to upcoming high-impact news."""
    ts = _ts()
    event_type = alert.get("event_type", "UNKNOWN")
    seconds = alert.get("seconds_until_event", 0)

    positions = self.executor.get_open_positions() or []
    if not positions:
        # No positions to close — just acknowledge
        self._acknowledge_news_alert()
        return

    print(f"[{ts}] NEWS_EXIT: closing {len(positions)} positions due to {event_type} "
          f"in {seconds}s")
    log.warning("NEWS_EXIT triggered: %s in %ds — closing %d positions",
                event_type, seconds, len(positions))

    for pos in positions:
        self._close_ticket(
            pos["ticket"],
            f"NEWS_EXIT_{event_type}",
            ts
        )

    # Emit event (will notify via canonical flow)
    self._emit_position_event(
        event_type="NEWS_EXIT",
        direction="BOTH",
        ticket=None,
        reason=f"High-impact news in {seconds}s: {event_type}",
        action_taken="CLOSE_ALL",
        result=f"positions_closed={len(positions)}",
        attempted=True,
        execution_state="EXECUTED",
    )

    self._acknowledge_news_alert()


def _acknowledge_news_alert(self) -> None:
    """Mark news alert as handled to prevent re-triggering."""
    try:
        state = _read_json(SERVICE_STATE_PATH) or {}
        if "news_exit_alert" in state:
            state["news_exit_alert"]["acknowledged"] = True
        with open(SERVICE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log.error("failed to acknowledge news alert: %s", e)
```

---

### Problem B.3 — L2 DANGER exits silently

**Current (position_monitor.py lines 878-912):** L2 DANGER closes legs but doesn't call `_emit_position_event`.

**Fix B.3 — Add emit call**

```python
def _check_l2_danger(self, pos, trade_rec, state, df_micro, direction):
    # ... existing logic ...
    state["danger_streak"] = DANGER_BARS
    ts = _ts()
    log.warning("L2 DANGER EXIT: %d consecutive danger bars (scores=%s) for %s",
                DANGER_BARS, scores, direction)
    print(f"[{ts}] L2 DANGER: closing Leg2+Leg3")

    # === NEW: Emit position event ===
    self._emit_position_event(
        event_type="L2_DANGER",
        direction=direction,
        ticket=pos.get("ticket"),
        reason=f"L2 danger scores={scores[-DANGER_BARS:]} for {DANGER_BARS} bars",
        action_taken="CLOSE_LEG2_LEG3",
        result="PENDING",
        attempted=True,
        execution_state="ATTEMPTED",
    )

    # ... existing close logic ...
```

---

### Problem B.4 — Pullback START not detected as event

**Current:** When hedge is opened (pullback detected), the hedge manager opens a contra position. But no explicit "PULLBACK_START" event is emitted to notify Barbara.

**Analysis needed:** Review hedge manager lifecycle (lines 416+). This is the moment when system recognizes pullback.

**Fix B.4 — Emit PULLBACK_START when hedge opens**

In hedge manager logic (needs exact line from position_monitor):

```python
# When hedge is opened (existing logic)
# ... code to open hedge ...

# === NEW: Emit pullback start event ===
self._emit_position_event(
    event_type="PULLBACK_START",
    direction=hedge_direction,
    ticket=hedge_ticket,
    reason=f"Iceberg contra detected | main={main_direction} | hedge opened",
    action_taken="OPEN_HEDGE",
    result="EXECUTED",
    attempted=True,
    execution_state="EXECUTED",
)
```

**Note:** I need to see exact hedge manager code to pinpoint where this goes. Currently identified lines 1529, 1544, 1557 have `_emit_position_event` calls — need to verify which corresponds to hedge opening.

---

## VALIDATION CRITERIA (empirical, not narrative)

### Test 1 — GO signal independent of broker

**Setup:** MT5 disconnected (current state).
**Expected on GO decision:**
1. `🎯 GO — SHORT ...` Telegram message (immediate)
2. `❌ ORDER FAILED — SHORT / robo=DISCONNECTED` Telegram message (seconds later)

**Validation:** cross-reference decision_log.jsonl GO entries with Telegram message count.

### Test 2 — BLOCK message correct

**Expected on BLOCK:**
- `⛔ BLOCK — SHORT` (not "BLOCK — SHORT" with "GO" text)
- Reason, gates, context all present
- Single message per BLOCK decision

### Test 3 — Position events notify

**Expected: when any of these occur, Telegram message received:**
- SHIELD (TP1 hit) → `🛡 SHIELD — LONG ...`
- REGIME FLIP → `🔄 REGIME_FLIP — LONG ...`
- PULLBACK END → `↪ PULLBACK_END_EXIT — SHORT ...`
- PULLBACK START (hedge open) → `↩ PULLBACK_START — LONG ...`
- L2 DANGER → `⚠ L2_DANGER — SHORT ...`
- T3 DEFENSE EXIT → `🚨 T3_EXIT — LONG ...`

### Test 4 — TP/SL distinction via MT5 history

**Expected: when MT5 closes position by TP or SL:**
- TP1 hit → `✅ TP1_HIT — LONG @ 4895 | pnl=+10.5`
- TP2 hit → `🏆 TP2_HIT — LONG @ 4910 | pnl=+25.5`
- SL hit → `🛑 SL_HIT — LONG @ 4882 | pnl=-7.0`
- Manual close → `MANUAL_CLOSE — LONG ...`

**Validation:** manually close a demo position → verify correct classification.

### Test 5 — News exit auto

**Setup:** Configure high-impact news event 30min in future (use economic calendar).
**Expected:**
- 30 min before event: all positions closed
- Telegram: `📰 NEWS_EXIT — BOTH | High-impact news in 1800s: NFP`
- No new positions opened until 30min after event

### Test 6 — Count matching (across all tests)

After 24h runtime:
- Count of Telegram GO messages = count of GO actions in decision_log
- Count of Telegram BLOCK messages = count of BLOCK actions in decision_log
- Count of PM_EVENT messages = count of PM_EVENT actions in decision_log
- Count of ORDER OPENED messages = count of EXECUTED actions in decision_log
- Count of ORDER FAILED messages = count of EXEC_FAILED actions in decision_log

Mismatch = bug, fix before proceeding.

---

## IMPLEMENTATION PLAN

### Phase 1: Backup (30min)

1. Create `C:\FluxQuantumAI\Backups\pre-telegram-fix-<timestamp>\`
2. Copy:
   - `live\event_processor.py`
   - `live\telegram_notifier.py`
   - `live\position_monitor.py`
3. Generate MD5 hashes of backups

### Phase 2: Scope A implementation (~6h)

1. Modify `event_processor.py` (3 locations + duplicates in GAMMA/DELTA branches)
2. Refactor `telegram_notifier.py` (new `notify_decision` with branches + new `notify_execution`)
3. Add `notify_decision()` call inside `_publish_canonical_pm_event()` in position_monitor.py
4. Remove direct `tg._send_async` from T3 exit (line 1442) — will use canonical flow now

### Phase 3: Scope B.1 implementation (~4h)

1. Create `live/mt5_history_watcher.py` (new file)
2. Integrate in `position_monitor.__init__` (instantiate watcher)
3. Add polling logic in main `check()` loop (on position count drop)
4. Add `_handle_closed_deal()` method
5. Add `_find_trade_by_ticket()` helper

### Phase 4: Scope B.2 implementation (~5h)

1. In `event_processor.py`: add `_write_news_exit_signal()` method
2. Hook into ApexNewsGate integration (when score >= 0.70 AND seconds_to_event <= 1800)
3. In `position_monitor.py`: add `_handle_news_exit()` method
4. Add `_acknowledge_news_alert()` method
5. Integrate check in main loop

### Phase 5: Scope B.3 + B.4 implementation (~2h)

1. B.3: Add `_emit_position_event(event_type="L2_DANGER")` in `_check_l2_danger()` before closes
2. B.4: Identify hedge open location in position_monitor, add `_emit_position_event(event_type="PULLBACK_START")`

### Phase 6: Claude independent audit (~3h)

Claude reads every diff ClaudeCode produces. Confirms:
- Changes match design doc
- No unintended side effects
- No scope creep
- Syntax correct (py_compile passes)

### Phase 7: Deployment (~1h)

1. Stop `FluxQuantumAPEX` service
2. py_compile all modified files
3. Copy to live
4. Start service
5. Verify Running

### Phase 8: Validation (~2h — Barbara + Claude)

Run all 6 tests above. Document results.

### Phase 9: Closeout

- `TASK_CLOSEOUT_REPORT`
- Update `SYSTEM_STATE_LOG.md`
- Update `CHANGELOG.md`

---

## RISK ASSESSMENT

| Risk | Probability | Severity | Mitigation |
|---|---|---|---|
| Telegram flood (too many messages) | MEDIUM | LOW | Anti-spam per dec_id exists; position events are infrequent |
| PM_EVENT overwrites GO decision in `_last_decision_id` | MEDIUM | MEDIUM | Both use same `_last_decision_id` — intentional (one notification per decision_id) |
| MT5 history polling performance | LOW | LOW | On-demand (only when position count drops) |
| News exit false positives | MEDIUM | HIGH | Threshold tunable; validate with historical news events |
| Hedge manager already emits duplicate events | MEDIUM | MEDIUM | Phase 5 audit — verify existing emit calls before adding PULLBACK_START |
| B.1 TP1 vs TP2 attribution wrong | MEDIUM | LOW | Cross-reference with trades.csv record; fallback to generic TP_HIT |
| Scope creep during implementation | LOW | MEDIUM | Barbara + Claude enforce GOLDEN RULE — no additions without design doc update |

---

## ROLLBACK PLAN

If any validation test fails:

```powershell
Stop-Service FluxQuantumAPEX
Copy-Item "C:\FluxQuantumAI\Backups\pre-telegram-fix-<timestamp>\live\*.py" `
          "C:\FluxQuantumAI\live\"
# For new files (mt5_history_watcher.py):
Remove-Item "C:\FluxQuantumAI\live\mt5_history_watcher.py" -ErrorAction SilentlyContinue
Start-Service FluxQuantumAPEX
```

---

## WHAT THIS DESIGN DOES NOT ADDRESS

Out of scope:

1. **MT5 RoboForex disconnection root cause** — separate investigation task
2. **Hantec NSSM error 1051** — separate task
3. **NextGen P1-P10 architectural review** — separate brainstorming
4. **Smart Exit Module** — separate spec v0.1 exists
5. **D1H4 daemon reactivation** — separate task
6. **Health check redesign with multi-timeframe bias** — separate spec v0.1 exists

Scope creep forbidden. Any additions require design doc update + Barbara approval.

---

## OPEN QUESTIONS FOR BARBARA

1. **Anti-spam window for position events?** Currently using same `_last_decision_id`. If TP1 and TP2 fire 2 seconds apart with same decision_id, only one notifies. Should PM_EVENTs use separate anti-spam? **Recommended: yes, separate `_last_pm_event_id`.**

2. **Telegram message grouping?** If 3 legs all close by TP1, that's 3 messages. Consolidate into one "TP1_HIT x3 legs"? **Recommended: one per leg for now, consolidate later.**

3. **News exit cooldown?** After news passes, how long before entries resume? **Tua regra: 30min after.** Confirm?

4. **B.4 Pullback START — need to see hedge manager code to confirm exact integration point.** Will request during Phase 2.

---

## APPROVAL SIGN-OFF

- [ ] Barbara reviewed this design doc
- [ ] Barbara approves Scope A
- [ ] Barbara approves Scope B.1 (MT5 history watcher)
- [ ] Barbara approves Scope B.2 (news exit integration)
- [ ] Barbara approves Scope B.3 (L2 DANGER emit)
- [ ] Barbara approves Scope B.4 (PULLBACK_START emit)
- [ ] Barbara confirms validation criteria
- [ ] Barbara confirms rollback plan
- [ ] Barbara confirms out-of-scope items

**Only ALL checkboxes checked → implementation begins.**

---

## POST-APPROVAL WORKFLOW

1. ClaudeCode receives approved design doc (no ambiguity possible)
2. ClaudeCode executes phases 1-5 with individual completion reports
3. Claude audits each phase BEFORE next begins
4. Barbara can pause/abort at any phase
5. Deployment (Phase 7) only after ALL audits pass
6. Validation (Phase 8) with Barbara present
7. Closeout (Phase 9) only after ALL tests green

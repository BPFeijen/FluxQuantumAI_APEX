"""
telegram_notifier.py — Telegram Signal Notifier for Manual Operators
====================================================================

Sends ALL trading decisions to the FLUXQUANTUM AI Telegram group:
  - ENTRY signals (GO + BLOCK with reasons)
  - Position management (TP1/SHIELD, TP2, SL, trailing)
  - Regime/risk events (news, defense mode, feed dead, regime flip)
  - Periodic status updates (every 30 min)

Thread-safe. Non-blocking (fire-and-forget with 5s timeout).
Never raises — all errors are logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.parse import quote

_log = logging.getLogger("apex.telegram")

# ── Config ────────────────────────────────────────────────────────────
BOT_TOKEN = "8556507495:AAG9E4_7-hR2Jp_2b4_7jS8mojLXHYTQJqk"
CHAT_ID   = "-1003418918465"
API_URL   = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
TIMEOUT   = 5  # seconds


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Send message to Telegram. Returns True on success."""
    try:
        payload = json.dumps({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = Request(API_URL, data=payload, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status == 200
    except Exception as e:
        _log.debug("Telegram send failed: %s", e)
        return False


def _send_async(text: str):
    """Fire-and-forget in background thread."""
    t = threading.Thread(target=_send, args=(text,), daemon=True)
    t.start()


# ═══════════════════════════════════════════════════════════════════════
# ENTRY / BLOCK — Single Source of Truth (Sprint Telegram Fase 2)
# Reads decision_live.json. Zero calculation.
# ═══════════════════════════════════════════════════════════════════════

# Anti-spam: track last decision_id to avoid duplicate sends
_last_decision_id: str = ""
_last_execution_id: str = ""


def notify_decision() -> bool:
    """
    Read decision_live.json and send ENTRY or BLOCK message.
    Source: decision_live.json only. Zero calculation.
    Returns True if message was sent.
    """
    global _last_decision_id

    dl = _read_json(_DECISION_LIVE_PATH)
    if dl is None:
        return False

    dec = dl.get("decision", {})
    dec_id = dl.get("decision_id", "")
    action = dec.get("action", "")

    # Anti-spam: don't resend same decision
    if dec_id == _last_decision_id:
        return False
    _last_decision_id = dec_id

    direction = dec.get("direction", "?")
    action_side = dec.get("action_side") or ("BUY" if direction == "LONG" else "SELL")
    trade_intent = dec.get("trade_intent", f"ENTRY_{direction}")
    semantics_v = dec.get("message_semantics_version", "v1_canonical")
    price_mt5 = dl.get("price_mt5", 0)
    price_gc = dl.get("price_gc", 0)
    score = dec.get("total_score", 0)
    reason = dec.get("reason", "")
    ts_str = dl.get("timestamp", "")
    ts_display = ts_str[11:19] + " UTC" if len(ts_str) > 19 else ""

    # FASE 2a: near_level_source from trigger dict
    _trigger_dict = dl.get("trigger", {})
    _nl_source = _trigger_dict.get("near_level_source", "?")

    # Context comes from canonical decision payload first; fallback to service_state
    ctx = dl.get("context", {})
    ss = _read_json(_SERVICE_STATE_PATH) or {}
    phase = ctx.get("phase", ss.get("phase", "?"))
    bias = ctx.get("m30_bias", ss.get("m30_bias", "?"))
    d4h = ctx.get("delta_4h", ss.get("delta_4h", 0))
    atr = ctx.get("m30_atr14", ss.get("atr_m30", 0))
    anomaly = dl.get("anomaly", {})
    iceberg = dl.get("iceberg", {})

    # Gates
    gates = dl.get("gates", {})
    v1 = _gate_icon(gates.get("v1_zone", {}).get("status"))
    v2 = _gate_icon(gates.get("v2_l2", {}).get("status"))
    v3 = _gate_icon(gates.get("v3_momentum", {}).get("status"))
    v4 = _gate_icon(gates.get("v4_iceberg", {}).get("status"))
    gates_line = f"V1: {v1} | V2: {v2} | V3: {v3} | V4: {v4}"

    if action == "EXECUTED":
        # ENTRY signal
        sl = dec.get("sl", 0)
        tp1 = dec.get("tp1", 0)
        tp2 = dec.get("tp2", 0)
        lots = dec.get("lots", [0, 0, 0])
        sl_dist = abs(price_mt5 - sl)
        tp1_dist = abs(tp1 - price_mt5)
        rr = tp1_dist / sl_dist if sl_dist > 0 else 0

        text = (
            f"\U0001F680 <b>ENTRY \u2014 {direction}</b>\n"
            f"Intent: {trade_intent} | Side: {action_side}\n"
            f"Price: {price_mt5:.2f}\n"
            f"SL: {sl:.2f} ({sl_dist:.1f} pts)\n"
            f"TP1: {tp1:.2f} ({tp1_dist:.1f} pts) | TP2: {tp2:.2f}\n"
            f"Runner: ON\n"
            f"Score: {score:+d} | R:R: {rr:.1f}\n"
            f"Source: {_nl_source}\n"
        )
        if lots and len(lots) >= 3:
            text += f"Lots: L1={lots[0]:.2f} L2={lots[1]:.2f} L3={lots[2]:.2f}\n"
        if price_gc > 0:
            text += f"<i>GC ref: {price_gc:.2f}</i>\n"
        text += (
            f"Context:\n"
            f"Phase: {phase} | Bias: {bias}\n"
            f"\u03944h: {d4h:+.0f} | ATR: {atr:.1f}\n"
            f"Anomaly: {anomaly.get('alignment', 'UNKNOWN')}/{anomaly.get('severity', 'NONE')} | Pos: {anomaly.get('position_action', 'UNKNOWN')}\n"
            f"Iceberg: {iceberg.get('alignment', 'UNKNOWN')}/{iceberg.get('severity', 'NONE')} | Pos: {iceberg.get('position_action', 'UNKNOWN')}\n"
            f"Gates:\n"
            f"{gates_line}\n"
            f"\U0001F194 {dec_id} | {ts_display} | {semantics_v}"
        )

    elif action == "GO":
        # GO signal — emitted BEFORE execution (Fase 2 decoupling)
        sl = dec.get("sl", 0)
        tp1 = dec.get("tp1", 0)
        tp2 = dec.get("tp2", 0)
        text = (
            f"\U0001F3AF <b>GO \u2014 {direction}</b>\n"
            f"{price_mt5:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f} | Runner: ON\n"
            f"Score: {score:+d} | Reason: {reason}\n"
            f"Phase: {phase} | Bias: {bias} | \u03944h: {d4h:+.0f}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    elif action == "BLOCK":
        # Gate rejected entry
        blocked_by = ""
        for gname, gkey in [("V1", "v1_zone"), ("V2", "v2_l2"), ("V3", "v3_momentum"), ("V4", "v4_iceberg")]:
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

        # Icon map per event type
        icon_map = {
            "SHIELD":              "\U0001F6E1",  # 🛡
            "TP1_HIT":             "\u2705",       # ✅
            "TP2_HIT":             "\U0001F3C6",  # 🏆
            "SL_HIT":              "\U0001F6D1",  # 🛑
            "REGIME_FLIP":         "\U0001F504",  # 🔄
            "PULLBACK_START":      "\u21A9",       # ↩
            "PULLBACK_END_EXIT":   "\u21AA",       # ↪
            "L2_DANGER":           "\u26A0",       # ⚠
            "T3_EXIT":             "\U0001F6A8",  # 🚨
            "NEWS_EXIT":           "\U0001F4F0",  # 📰
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
        # Unknown action — send generic
        text = (
            f"\U0001F4CB <b>DECISION \u2014 {action}</b>\n"
            f"Direction: {direction} | Price: {price_mt5:.2f}\n"
            f"Score: {score:+d}\n"
            f"Reason: {reason}\n"
            f"\U0001F194 {dec_id} | {ts_display}"
        )

    _send_async(text)
    return True


def notify_execution() -> bool:
    """
    Notify Telegram of EXECUTION event (EXECUTED or EXEC_FAILED).
    Separate message after broker responds (Fase 2 decoupling).
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


def notify_entry_go(**kwargs):
    """Legacy — reads decision_live.json instead of using parameters."""
    notify_decision()


def notify_entry_block(**kwargs):
    """Legacy — reads decision_live.json instead of using parameters."""
    notify_decision()


# ═══════════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

def notify_tp1_shield(direction: str, entry: float, tp1_price: float, gc_offset: float = 0.0):
    """TP1 hit — SHIELD activated. Operator should move SL to breakeven."""
    gc_note = f"\n<i>GC ref: entry={entry+gc_offset:.2f} tp1={tp1_price+gc_offset:.2f}</i>" if gc_offset else ""
    text = (
        f"\U0001F6E1 <b>SHIELD ACTIVATED</b>\n"
        f"TP1 hit @ {tp1_price:.2f} ({direction})\n"
        f"<b>Action:</b> Move SL to entry ({entry:.2f})\n"
        f"Leg1 closed. Leg2+Leg3 protected.{gc_note}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_tp2_hit(direction: str, tp2_price: float):
    """TP2 hit — close Leg2."""
    text = (
        f"\U0001F3AF <b>TP2 HIT</b> @ {tp2_price:.2f} ({direction})\n"
        f"<b>Action:</b> Close Leg2. Runner (Leg3) continues.\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_sl_hit(direction: str, sl_price: float, pnl: float = 0.0):
    """SL hit — position closed."""
    text = (
        f"\U0001F4A5 <b>SL HIT</b> @ {sl_price:.2f} ({direction})\n"
        f"<b>P&L:</b> {pnl:+.2f} pts\n"
        f"<b>Action:</b> All legs closed.\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_trailing_update(direction: str, new_sl: float, current_price: float):
    """Trailing stop moved."""
    text = (
        f"\U0001F4C8 <b>TRAILING UPDATE</b> ({direction})\n"
        f"New SL: {new_sl:.2f} | Price: {current_price:.2f}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


# ═══════════════════════════════════════════════════════════════════════
# REGIME / RISK EVENTS
# ═══════════════════════════════════════════════════════════════════════

def notify_news_event(event_type: str, reason: str):
    """News gate fired — block entry or exit all."""
    icon = "\U0001F4F0"  # newspaper
    text = (
        f"{icon} <b>NEWS {event_type}</b>\n"
        f"{reason}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_defense_mode(trigger_reason: str, tier: str = "ENTRY_BLOCK",
                        protection: dict = None):
    """Defense mode activated — microstructure anomaly with tier classification."""
    if tier == "DEFENSIVE_EXIT":
        action = "Entries blocked. TIER 2 SHADOW: would close all positions."
        icon = "\U0001F6A8"  # rotating light
    else:
        action = "All entries blocked until resolved."
        icon = "\U000026A0"  # warning
    prot_line = ""
    if protection:
        prot_line = (
            f"\n<b>Protection:</b> "
            f"align={protection.get('alignment', '?')} "
            f"sev={protection.get('severity', '?')} "
            f"entry={protection.get('entry_action', '?')} "
            f"pos={protection.get('position_action', '?')}"
        )
    text = (
        f"{icon} <b>DEFENSE MODE — {tier}</b>\n"
        f"Microstructure anomaly detected.\n"
        f"<b>Trigger:</b> {trigger_reason}\n"
        f"<b>Action:</b> {action}"
        f"{prot_line}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_regime_flip(old_regime: str, new_regime: str, delta_4h: float):
    """Regime changed — consider exiting positions."""
    text = (
        f"\U0001F504 <b>REGIME FLIP</b>\n"
        f"{old_regime} \u2192 {new_regime}\n"
        f"Delta 4h: {delta_4h:+.0f}\n"
        f"<b>Action:</b> Consider closing {old_regime} positions.\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_feed_dead():
    """Feed dead — system suspended trading."""
    text = (
        f"\U0001F6A8 <b>FEED DEAD</b>\n"
        f"Quantower L2 stream offline (port 8000).\n"
        f"<b>Action:</b> Trading suspended. Do NOT enter new positions.\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_feed_recovered():
    """Feed recovered."""
    text = (
        f"\U00002705 <b>FEED RECOVERED</b>\n"
        f"Quantower L2 stream back online.\n"
        f"Trading resumed.\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


# ═══════════════════════════════════════════════════════════════════════
# ICEBERG SIGNALS (Sprint Telegram Fase 3)
# ═══════════════════════════════════════════════════════════════════════

_last_iceberg_ts: float = 0.0
_ICEBERG_COOLDOWN_S = 120  # 2 min minimum between iceberg messages


def notify_iceberg_support(direction: str, side: str, strength: str = "HIGH",
                           reason: str = "Institutional absorption in favor"):
    """Iceberg detected in favor of current position."""
    global _last_iceberg_ts
    now = time.time()
    if now - _last_iceberg_ts < _ICEBERG_COOLDOWN_S:
        return
    _last_iceberg_ts = now
    text = (
        f"\U0001F9CA <b>ICEBERG SUPPORT</b>\n"
        f"Direction: {direction}\n"
        f"Side: {side}\n"
        f"Strength: {strength}\n"
        f"Action: HOLD\n"
        f"Reason:\n{reason}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_iceberg_alert(direction: str, side: str, strength: str = "HIGH",
                         reason: str = "Liquidity against position"):
    """Iceberg detected AGAINST current position — HIGH priority."""
    global _last_iceberg_ts
    # No cooldown for contra alerts — always send (HIGH priority)
    _last_iceberg_ts = time.time()
    text = (
        f"\U0001F9CA <b>ICEBERG ALERT</b>\n"
        f"Direction: {direction}\n"
        f"Side: {side}\n"
        f"Strength: {strength}\n"
        f"Action: REDUCE / PREPARE EXIT\n"
        f"Reason:\n{reason}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


# ═══════════════════════════════════════════════════════════════════════
# ANOMALY SIGNALS (Sprint Telegram Fase 3)
# ═══════════════════════════════════════════════════════════════════════

_last_anomaly_ts: float = 0.0
_ANOMALY_COOLDOWN_S = 120  # 2 min minimum


def notify_anomaly_support(direction: str, anomaly_type: str = "exhaustion"):
    """Anomaly detected in favor — HOLD."""
    global _last_anomaly_ts
    now = time.time()
    if now - _last_anomaly_ts < _ANOMALY_COOLDOWN_S:
        return
    _last_anomaly_ts = now
    text = (
        f"\U0001F4E1 <b>ANOMALY SUPPORT</b>\n"
        f"Direction: {direction}\n"
        f"Type: {anomaly_type}\n"
        f"Action: HOLD\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_anomaly_alert(anomaly_type: str = "spread_widening",
                         trigger_reason: str = ""):
    """Anomaly detected AGAINST — block entries. HIGH priority."""
    global _last_anomaly_ts
    # No cooldown for contra — HIGH priority
    _last_anomaly_ts = time.time()
    reason_line = f"\nReason: {trigger_reason}" if trigger_reason else ""
    text = (
        f"\U0001F4E1 <b>ANOMALY ALERT</b>\n"
        f"Type: {anomaly_type}\n"
        f"Action: BLOCK entries{reason_line}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


# ═══════════════════════════════════════════════════════════════════════
# RISK EXIT / CALM (Sprint Telegram Fase 3)
# ═══════════════════════════════════════════════════════════════════════

def notify_risk_exit(direction: str, reason: str = "Momentum reversal + contra iceberg"):
    """Risk exit — close positions. HIGH priority."""
    ss = _read_json(_SERVICE_STATE_PATH) or {}
    phase = ss.get("phase", "?")
    bias = ss.get("m30_bias", "?")
    d4h = ss.get("delta_4h", 0)

    dl = _read_json(_DECISION_LIVE_PATH)
    dec_id = dl.get("decision_id", "?") if dl else "?"

    text = (
        f"\U000026A0 <b>RISK EXIT \u2014 CLOSE {direction}</b>\n"
        f"Reason:\n{reason}\n"
        f"Context:\n"
        f"Phase: {phase} | Bias: {bias}\n"
        f"\u03944h: {d4h:+.0f}\n"
        f"Action: Close all positions\n"
        f"\U0001F194 {dec_id}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_stay_in_trade(direction: str, reason: str = "Iceberg + anomaly aligned"):
    """Calm signal — hold position. LOW priority."""
    text = (
        f"\U0001F7E2 <b>STAY IN TRADE</b>\n"
        f"Direction: {direction}\n"
        f"Reason:\n{reason}\n"
        f"Action: Do not exit\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


# ═══════════════════════════════════════════════════════════════════════
# DAILY REPORT (Sprint Telegram Fase 3)
# Source: service_state.json + trades data passed by caller
# ═══════════════════════════════════════════════════════════════════════

def notify_daily_report(
    date: str,
    wins: int,
    losses: int,
    total_pnl: float,
    profit_factor: float,
    best_trade: float,
    worst_trade: float,
    blocks: int = 0,
    top_block_reason: str = "",
    trades_detail: str = "",
):
    """End-of-day report. Context from service_state.json."""
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0
    icon = "\U0001F4C8" if total_pnl >= 0 else "\U0001F4C9"

    ss = _read_json(_SERVICE_STATE_PATH) or {}
    phase = ss.get("phase", "?")
    d4h = ss.get("delta_4h", 0)
    feed = ss.get("feed_status", "?")
    m5 = "OK" if ss.get("m5_ok") else "STALE"
    m30 = "OK" if ss.get("m30_ok") else "STALE"

    text = (
        f"{icon} <b>DAILY REPORT \u2014 FluxQuantumAI</b>\n"
        f"Trades: {total}\n"
        f"Wins: {wins} | Losses: {losses}\n"
        f"Winrate: {wr:.0f}%\n"
        f"PnL Total: {total_pnl:+.1f} pts\n"
        f"PF: {profit_factor:.2f}\n"
    )
    if blocks > 0:
        text += f"Blocks: {blocks}\n"
    if top_block_reason:
        text += f"Top Block: {top_block_reason}\n"
    text += (
        f"Best: {best_trade:+.1f} pts\n"
        f"Worst: {worst_trade:+.1f} pts\n"
        f"Phase: {phase}\n"
        f"\u03944h: {d4h:+.0f}\n"
        f"System Health:\n"
        f"Feed: {feed} | M5: {m5} | M30: {m30}\n"
        f"Data: {date}\n"
    )
    if trades_detail:
        text += f"\n{trades_detail}\n"

    text += f"\n<i>Report generated {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    _send_async(text)


# ═══════════════════════════════════════════════════════════════════════
# PERIODIC STATUS (LEGACY — kept for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════

def notify_status(
    gc_price: float,
    liq_top: float,
    liq_bot: float,
    phase: str = "?",
    m30_bias: str = "?",
    delta_4h: float = 0.0,
    open_positions: int = 0,
    session: str = "?",
):
    """Legacy status — use notify_health_check() instead."""
    notify_health_check()


# ═══════════════════════════════════════════════════════════════════════
# HEALTH CHECK — Single Source of Truth (Sprint Telegram Fase 1)
# ═══════════════════════════════════════════════════════════════════════

_SERVICE_STATE_PATH  = "C:/FluxQuantumAI/logs/service_state.json"
_DECISION_LIVE_PATH  = "C:/FluxQuantumAI/logs/decision_live.json"

# Anti-spam state
_last_health_ts: float = 0.0
_last_health_fingerprint: str = ""
_HEALTH_INTERVAL_S = 300  # 5 min periodic
_HEALTH_MIN_INTERVAL_S = 300  # 5 min minimum between any health msg


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _get_session() -> str:
    h = datetime.now(timezone.utc).hour
    if 0 <= h < 8:
        return "ASIAN"
    elif 8 <= h < 13:
        return "LONDON"
    elif 13 <= h < 21:
        return "NEW YORK"
    return "OFF-HOURS"


def _gate_icon(status: str) -> str:
    s = (status or "").upper()
    if s in ("PASS", "OK", "ZONE_OK"):
        return "\u2705"
    if s == "BLOCK":
        return "\u274C"
    if s == "WARN":
        return "\u26A0"
    return "\u2014"


def _health_fingerprint(ss: dict, dl: Optional[dict]) -> str:
    """Key fields that trigger a send on change."""
    gate_str = ""
    if dl and "gates" in dl:
        g = dl["gates"]
        gate_str = "|".join(
            g.get(k, {}).get("status", "")
            for k in ("v1_zone", "v2_l2", "v3_momentum", "v4_iceberg")
        )
    return (
        f"{ss.get('phase')}|{ss.get('feed_status')}|{ss.get('m30_bias')}|"
        f"{ss.get('last_gate_at')}|{gate_str}"
    )


def notify_health_check(force: bool = False) -> bool:
    """
    Health check — reads service_state.json + decision_live.json.
    Zero calculation. Sends only if:
      - force=True, OR
      - 15 min since last send, OR
      - relevant state changed (phase, feed, bias, last_gate_at, gates)
    Returns True if message was sent.
    """
    global _last_health_ts, _last_health_fingerprint

    ss = _read_json(_SERVICE_STATE_PATH)
    if ss is None:
        return False

    dl = _read_json(_DECISION_LIVE_PATH)

    now = time.time()
    fp = _health_fingerprint(ss, dl)

    # Anti-spam: skip if too soon and nothing changed
    if not force:
        elapsed = now - _last_health_ts
        changed = fp != _last_health_fingerprint
        if elapsed < _HEALTH_MIN_INTERVAL_S:
            return False
        if elapsed < _HEALTH_INTERVAL_S and not changed:
            return False

    # Build message from source files only
    phase = ss.get("phase", "?")
    bias = ss.get("m30_bias", "?")
    session = _get_session()
    feed = ss.get("feed_status", "?")
    m5 = "OK" if ss.get("m5_ok") else "STALE"
    m30 = "OK" if ss.get("m30_ok") else "STALE"
    d4h = ss.get("delta_4h", 0)
    atr = ss.get("atr_m30", 0)
    last_gate = ss.get("last_gate_at")
    last_gate_str = last_gate[11:19] + " UTC" if last_gate else "none"

    # Gates from decision_live.json
    if dl and "gates" in dl:
        g = dl["gates"]
        v1 = _gate_icon(g.get("v1_zone", {}).get("status"))
        v2 = _gate_icon(g.get("v2_l2", {}).get("status"))
        v3 = _gate_icon(g.get("v3_momentum", {}).get("status"))
        v4 = _gate_icon(g.get("v4_iceberg", {}).get("status"))
    else:
        v1 = v2 = v3 = v4 = "\u2014"

    # Decision status
    if dl and "decision" in dl:
        dec = dl["decision"]
        action = dec.get("action", "?")
        dec_id = dl.get("decision_id", "?")
        status_line = f"Last: {action} | \U0001F194 {dec_id}"
    else:
        status_line = "Status: awaiting first gate check"

    text = (
        f"\U0001F4E1 <b>HEALTH CHECK \u2014 FluxQuantumAI</b>\n"
        f"Phase: {phase}\n"
        f"Bias: {bias} | Session: {session}\n"
        f"Feed: {feed} | M5: {m5} | M30: {m30}\n"
        f"\u03944h: {d4h:+.0f} | ATR: {atr:.1f}\n"
        f"Last Gate: {last_gate_str}\n"
        f"Gates:\n"
        f"V1: {v1} | V2: {v2} | V3: {v3} | V4: {v4}\n"
        f"{status_line}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )

    _last_health_ts = now
    _last_health_fingerprint = fp
    _send_async(text)
    return True


# ═══════════════════════════════════════════════════════════════════════
# STARTUP / GENERIC
# ═══════════════════════════════════════════════════════════════════════

def notify_startup():
    """System started."""
    text = (
        f"\U0001F680 <b>FluxQuantumAI ONLINE</b>\n"
        f"Event processor started.\n"
        f"Signals will be sent to this group.\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)


def notify_generic(title: str, body: str):
    """Send any custom notification."""
    text = f"<b>{title}</b>\n{body}\n\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    _send_async(text)


# Legacy notify_trade_closed kept for backward compat
def notify_trade_closed(
    direction: str,
    entry: float,
    exit_price: float,
    pnl_pts: float,
    close_reason: str = "SL",
    leg: str = "",
):
    """Individual trade closed (TP or SL)."""
    icon = "\U00002705" if pnl_pts > 0 else "\U0000274C"
    text = (
        f"{icon} <b>TRADE CLOSED</b> ({close_reason})\n"
        f"{direction} entry={entry:.2f} exit={exit_price:.2f}\n"
        f"<b>P&L:</b> {pnl_pts:+.2f} pts {leg}\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    )
    _send_async(text)

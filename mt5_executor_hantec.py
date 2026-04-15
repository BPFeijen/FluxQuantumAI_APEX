#!/usr/bin/env python3
"""
C:\\FluxQuantumAI\\mt5_executor_hantec.py
Hantec Markets MT5 — live execution engine.

Credenciais carregadas de .env (nunca hardcoded aqui):
  HANTEC_ACCOUNT   — numero da conta
  HANTEC_PASSWORD  — senha
  HANTEC_SERVER    — HantecMarketsMU-MT5
  HANTEC_TERMINAL  — path do terminal64.exe

Comportamento identico ao mt5_executor.py (RoboForex demo),
mas com logs separados em logs/trades_live.csv e is_live=True.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.executor.hantec")

# ---------------------------------------------------------------------------
# Load .env (sem dependencia de python-dotenv)
# ---------------------------------------------------------------------------
def _load_env(path: str = ".env"):
    env_path = Path(path)
    if not env_path.exists():
        env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

_load_env()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYMBOL    = "XAUUSD"
ACCOUNT   = int(os.environ.get("HANTEC_ACCOUNT", "50051145"))
PASSWORD  = os.environ.get("HANTEC_PASSWORD", "")
SERVER    = os.environ.get("HANTEC_SERVER", "HantecMarketsMU-MT5")
TERMINAL  = os.environ.get("HANTEC_TERMINAL",
                            "C:/Program Files/Hantec Markets MT5 Terminal/terminal64.exe")

LOT_SIZE  = 0.02
COMMENT   = "APEX"
MAGIC     = 20260331
DEVIATION = 30
MIN_LOT   = 0.01

LOG_DIR        = Path(r"C:\FluxQuantumAI\logs")
TRADES_CSV     = LOG_DIR / "trades_live.csv"
GATE_CSV       = LOG_DIR / "live_log_live.csv"

TRADES_COLUMNS = [
    "timestamp", "asset", "direction", "decision",
    "lots", "entry", "sl", "tp1", "tp2",
    "result", "pnl", "gate_score",
    "leg1_ticket", "leg2_ticket", "leg3_ticket",
    "entry_mode", "daily_trend", "phase", "strategy_mode",
]
GATE_COLUMNS = [
    "timestamp", "symbol", "direction", "gate_decision",
    "score", "macro_delta", "mom_status", "v4_status",
    "reason", "trigger", "zone", "patterns",
]

# ---------------------------------------------------------------------------
# MT5 initialisation — Hantec terminal
# ---------------------------------------------------------------------------
MT5_AVAILABLE = False
mt5 = None

try:
    import MetaTrader5 as _mt5

    init_ok = _mt5.initialize(
        path=TERMINAL,
        login=ACCOUNT,
        password=PASSWORD,
        server=SERVER,
        timeout=15000,
    )

    if init_ok:
        info = _mt5.account_info()
        if info and info.login == ACCOUNT:
            MT5_AVAILABLE = True
            mt5 = _mt5
            log.info(
                "Hantec MT5 connected -- account=%d  server=%s  balance=%.2f  currency=%s",
                ACCOUNT, SERVER, info.balance, info.currency,
            )
        else:
            _mt5.shutdown()
            log.warning(
                "Hantec MT5 account mismatch -- expected %d got %s",
                ACCOUNT, info.login if info else "None",
            )
    else:
        log.warning("Hantec MT5 initialize() failed: %s", _mt5.last_error())

except ImportError:
    log.warning("MetaTrader5 not installed")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round_lot(raw: float) -> float:
    return round(round(raw / MIN_LOT) * MIN_LOT, 2)

def _split_lots(total: float) -> tuple[float, float, float]:
    l1 = _round_lot(total * 0.40)
    l2 = _round_lot(total * 0.40)
    l3 = _round_lot(total * 0.20)
    return l1, l2, l3

def _get_tick(symbol: str) -> Optional[object]:
    if not MT5_AVAILABLE:
        return None
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log.error("No tick for %s: %s", symbol, mt5.last_error())
    return tick

def _order_type_buy_sell(direction: str):
    if direction.upper() == "LONG":
        return mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_SELL
    return mt5.ORDER_TYPE_SELL, mt5.ORDER_TYPE_BUY

def _ensure_logs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_CSV.exists():
        with open(TRADES_CSV, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=TRADES_COLUMNS).writeheader()
    if not GATE_CSV.exists():
        with open(GATE_CSV, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=GATE_COLUMNS).writeheader()

# ---------------------------------------------------------------------------
# MT5ExecutorHantec
# ---------------------------------------------------------------------------

class MT5ExecutorHantec:

    is_live = True   # flag para distinguir do executor demo

    def __init__(self):
        self.connected = MT5_AVAILABLE
        _ensure_logs()
        if self.connected:
            log.info("MT5ExecutorHantec ready — LIVE account %d", ACCOUNT)
        else:
            log.warning("MT5ExecutorHantec: NOT connected — all calls will be no-ops")

    def reconnect(self) -> bool:
        """Attempt to reconnect to Hantec MT5 if disconnected."""
        if self.connected:
            return True
        global MT5_AVAILABLE, mt5
        try:
            init_ok = _mt5.initialize(
                path=TERMINAL, login=ACCOUNT, password=PASSWORD,
                server=SERVER, timeout=15000,
            )
            if init_ok:
                info = _mt5.account_info()
                if info and info.login == ACCOUNT:
                    MT5_AVAILABLE = True
                    mt5 = _mt5
                    self.connected = True
                    log.info("Hantec MT5 RECONNECTED — account %d", ACCOUNT)
                    return True
                else:
                    _mt5.shutdown()
            else:
                log.debug("Hantec reconnect failed: %s", _mt5.last_error())
        except Exception as e:
            log.debug("Hantec reconnect error: %s", e)
        return False

    # -----------------------------------------------------------------------
    # Account
    # -----------------------------------------------------------------------

    def get_balance(self) -> float:
        if not self.connected:
            return 0.0
        try:
            return round(mt5.account_info().balance, 2)
        except Exception as e:
            log.error("get_balance: %s", e)
            return 0.0

    def get_account_info(self) -> dict:
        if not self.connected:
            return {}
        try:
            info = mt5.account_info()
            return {
                "login":    info.login,
                "balance":  info.balance,
                "equity":   info.equity,
                "margin":   info.margin,
                "currency": info.currency,
                "server":   info.server,
                "leverage": info.leverage,
            }
        except Exception as e:
            log.error("get_account_info: %s", e)
            return {}

    # -----------------------------------------------------------------------
    # open_position — 3 legs
    # -----------------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        direction: str,
        lot_size: float,
        sl: float,
        tp1: float,
        tp2: Optional[float] = None,
        dry_run: bool = False,
        explicit_lots: Optional[list] = None,
    ) -> dict:
        if explicit_lots is not None:
            l1 = _round_lot(explicit_lots[0])
            l2 = _round_lot(explicit_lots[1])
            l3 = _round_lot(explicit_lots[2])
        else:
            l1, l2, l3 = _split_lots(lot_size)
        legs = 3 if l3 >= MIN_LOT else 2

        if dry_run:
            log.info("[DRY RUN LIVE] WOULD OPEN %s %s %.2f lots SL=%.2f TP1=%.2f",
                     symbol, direction, lot_size, sl, tp1)
            return {
                "success": True, "dry_run": True,
                "tickets": [0, 0, 0], "legs": legs,
                "entry": 0.0, "lots": [l1, l2, l3],
                "error": None,
            }

        if not self.connected:
            self.reconnect()  # try to reconnect before giving up
        if not self.connected:
            return {"success": False, "error": "Hantec MT5 not connected", "tickets": [0, 0, 0]}

        tick = _get_tick(symbol)
        if tick is None:
            return {"success": False, "error": "No market tick", "tickets": [0, 0, 0]}

        order_type, _ = _order_type_buy_sell(direction)
        entry_price   = tick.ask if direction.upper() == "LONG" else tick.bid

        tickets = []
        tps     = [tp1, tp2 if tp2 else 0.0, 0.0]
        lots    = [l1, l2, l3 if legs == 3 else 0.0]

        for i, (lot, tp) in enumerate(zip(lots, tps)):
            if lot < MIN_LOT:
                tickets.append(0)
                continue
            req = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       lot,
                "type":         order_type,
                "price":        entry_price,
                "sl":           sl,
                "tp":           tp if tp else 0.0,
                "deviation":    DEVIATION,
                "magic":        MAGIC,
                "comment":      "APEX_L%d" % (i + 1),
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res = mt5.order_send(req)
            if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
                err = res.comment if res else str(mt5.last_error())
                log.error("[LIVE] Leg %d failed: %s", i + 1, err)
                tickets.append(0)
            else:
                tickets.append(res.order)
                log.info("[LIVE] Leg %d opened — ticket=%d lot=%.2f price=%.2f",
                         i + 1, res.order, lot, res.price)

        success = any(t > 0 for t in tickets)
        return {
            "success": success,
            "tickets": tickets,
            "legs":    legs,
            "entry":   entry_price,
            "lots":    lots,
            "error":   None if success else "All legs failed",
        }

    # -----------------------------------------------------------------------
    # move_to_breakeven — SHIELD
    # -----------------------------------------------------------------------

    def move_to_breakeven(self, ticket_leg2: int, ticket_leg3: int, entry_price: float) -> dict:
        results = {"success": True, "modified": [], "errors": []}
        for ticket in (ticket_leg2, ticket_leg3):
            if ticket <= 0:
                continue
            ok, msg = self._modify_sl(ticket, entry_price)
            if ok:
                results["modified"].append(ticket)
                log.info("[LIVE] SHIELD: SL moved to %.2f on ticket %d", entry_price, ticket)
            else:
                results["errors"].append("ticket %d: %s" % (ticket, msg))
                results["success"] = False
        return results

    def open_single(
        self,
        symbol: str,
        direction: str,
        lot: float,
        sl: float,
        tp: float = 0.0,
        comment: str = "APEX_HEDGE",
        dry_run: bool = False,
    ) -> dict:
        if dry_run:
            return {"success": True, "dry_run": True, "ticket": 0, "entry": 0.0, "error": None}
        if not self.connected:
            return {"success": False, "ticket": 0, "entry": 0.0, "error": "Hantec MT5 not connected"}

        tick = _get_tick(symbol)
        if tick is None:
            return {"success": False, "ticket": 0, "entry": 0.0, "error": "no tick"}

        order_type, _ = _order_type_buy_sell(direction)
        entry_price   = tick.ask if direction.upper() == "LONG" else tick.bid
        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       round(lot, 2),
            "type":         order_type,
            "price":        entry_price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    DEVIATION,
            "magic":        MAGIC,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            err = res.comment if res else str(mt5.last_error())
            log.error("[LIVE] open_single failed: %s", err)
            return {"success": False, "ticket": 0, "entry": entry_price, "error": err}

        log.info("[LIVE] open_single OK ticket=%d %s %s %.2f entry=%.2f",
                 res.order, symbol, direction, lot, entry_price)
        return {"success": True, "ticket": res.order, "entry": entry_price, "error": None}

    def open_limit(
        self,
        symbol: str,
        direction: str,
        lot: float,
        limit_price: float,
        sl: float,
        tp: float = 0.0,
        comment: str = "APEX_LIMIT",
        expiry_hours: float = 4.0,
        dry_run: bool = False,
    ) -> dict:
        """Place a pending BUY_LIMIT or SELL_LIMIT on Hantec."""
        if dry_run:
            return {"success": True, "dry_run": True, "ticket": 0, "error": None}
        if not self.connected:
            return {"success": False, "ticket": 0, "error": "Hantec MT5 not connected"}

        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        order_type = (
            mt5.ORDER_TYPE_BUY_LIMIT if direction.upper() == "LONG"
            else mt5.ORDER_TYPE_SELL_LIMIT
        )
        expiry_epoch = int((_dt.now(_tz.utc) + _td(hours=expiry_hours)).timestamp())
        req = {
            "action":       mt5.TRADE_ACTION_PENDING,
            "symbol":       symbol,
            "volume":       round(lot, 2),
            "type":         order_type,
            "price":        round(limit_price, 2),
            "sl":           round(sl, 2),
            "tp":           round(tp, 2) if tp else 0.0,
            "deviation":    DEVIATION,
            "magic":        MAGIC,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_SPECIFIED,
            "expiration":   expiry_epoch,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode not in (
            mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED
        ):
            err = res.comment if res else str(mt5.last_error())
            log.error("[LIVE] open_limit %s %s @%.2f failed: %s", symbol, direction, limit_price, err)
            return {"success": False, "ticket": 0, "error": err}

        log.info("[LIVE] open_limit OK ticket=%d  %s %s %.2f  limit=%.2f", res.order, symbol, direction, lot, limit_price)
        return {"success": True, "ticket": res.order, "error": None}

    def _modify_sl(self, ticket: int, new_sl: float) -> tuple[bool, str]:
        if not self.connected:
            return False, "Hantec MT5 not connected"
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return False, "position not found"
        pos = positions[0]
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   pos.symbol,
            "position": ticket,
            "sl":       new_sl,
            "tp":       pos.tp,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            return False, res.comment if res else str(mt5.last_error())
        return True, "ok"

    # -----------------------------------------------------------------------
    # close_position
    # -----------------------------------------------------------------------

    def close_position(self, ticket: int) -> dict:
        if not self.connected:
            return {"success": False, "error": "Hantec MT5 not connected", "ticket": ticket, "pnl": 0.0}

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return {"success": False, "error": "position not found", "ticket": ticket, "pnl": 0.0}

        pos        = positions[0]
        _, close_t = _order_type_buy_sell("LONG" if pos.type == 0 else "SHORT")
        tick       = _get_tick(pos.symbol)
        if tick is None:
            return {"success": False, "error": "no tick", "ticket": ticket, "pnl": 0.0}

        close_price = tick.bid if pos.type == 0 else tick.ask
        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       pos.volume,
            "type":         close_t,
            "position":     ticket,
            "price":        close_price,
            "deviation":    DEVIATION,
            "magic":        MAGIC,
            "comment":      "APEX_CLOSE",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            err = res.comment if res else str(mt5.last_error())
            log.error("[LIVE] close_position ticket=%d failed: %s", ticket, err)
            return {"success": False, "error": err, "ticket": ticket, "pnl": 0.0}

        pnl = round(pos.profit, 2)
        log.info("[LIVE] Closed ticket=%d pnl=%.2f", ticket, pnl)
        return {"success": True, "ticket": ticket, "pnl": pnl, "error": None}

    # -----------------------------------------------------------------------
    # get_open_positions
    # -----------------------------------------------------------------------

    def get_open_positions(self) -> list[dict]:
        if not self.connected:
            return []
        try:
            positions = mt5.positions_get()
            if positions is None:
                return []
            return [
                {
                    "ticket":    pos.ticket,
                    "symbol":    pos.symbol,
                    "direction": "LONG" if pos.type == 0 else "SHORT",
                    "volume":    pos.volume,
                    "entry":     pos.price_open,
                    "sl":        pos.sl,
                    "tp":        pos.tp,
                    "pnl":       round(pos.profit, 2),
                    "comment":   pos.comment,
                    "time_open": datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
                }
                for pos in positions if pos.magic == MAGIC
            ]
        except Exception as e:
            log.error("get_open_positions: %s", e)
            return []

    # -----------------------------------------------------------------------
    # log_trade / log_gate
    # -----------------------------------------------------------------------

    def log_trade(self, *, direction, decision, lots, entry, sl, tp1,
                  tp2=0.0, result="", pnl=0.0, gate_score=0,
                  leg1_ticket=0, leg2_ticket=0, leg3_ticket=0, asset="AX1",
                  entry_mode="", daily_trend="", phase="", strategy_mode=""):
        _ensure_logs()
        row = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "asset":       asset,
            "direction":   direction,
            "decision":    decision,
            "lots":        lots,
            "entry":       entry,
            "sl":          sl,
            "tp1":         tp1,
            "tp2":         tp2,
            "result":      result,
            "pnl":         pnl,
            "gate_score":  gate_score,
            "leg1_ticket": leg1_ticket,
            "leg2_ticket": leg2_ticket,
            "leg3_ticket": leg3_ticket,
            "entry_mode":  entry_mode,
            "daily_trend": daily_trend,
            "phase":       phase,
            "strategy_mode": strategy_mode,
        }
        with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=TRADES_COLUMNS).writerow(row)

    def log_gate(self, *, symbol="AX1", direction, gate_decision, score=0,
                 macro_delta=0.0, mom_status="", v4_status="UNKNOWN",
                 reason="", trigger="liq_touch", zone="", patterns=""):
        _ensure_logs()
        row = {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "symbol":        symbol,
            "direction":     direction,
            "gate_decision": gate_decision,
            "score":         score,
            "macro_delta":   round(macro_delta, 1),
            "mom_status":    mom_status,
            "v4_status":     v4_status,
            "reason":        reason[:120],
            "trigger":       trigger,
            "zone":          zone,
            "patterns":      patterns,
        }
        with open(GATE_CSV, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=GATE_COLUMNS).writerow(row)

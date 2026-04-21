#!/usr/bin/env python3
"""
C:\FluxQuantumAI\mt5_executor.py
APEX MT5 execution engine — 3-leg position management.

Position structure (per trade):
  Leg 1: 40% of lot_size  → TP = tp1 (FMV / ANC)
  Leg 2: 40% of lot_size  → TP = tp2 (opposing liq_line)
  Leg 3: 20% of lot_size  → TP = None (runner — no hard TP)
  All legs: same SL

After Leg 1 hits TP1 → SHIELD: move Leg 2 + Leg 3 SL to entry price.
A winning trade can NEVER go negative after SHIELD is activated.

Minimum lot enforcement (XAUUSD step = 0.01):
  If Leg 3 rounds to 0.00 → open 2 legs only (0.01 + 0.01).
  Minimum total lot to open 3 legs = 0.03.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.executor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYMBOL    = "XAUUSD"
ACCOUNT   = 68302120
LOT_SIZE  = 0.02
COMMENT   = "APEX"
MAGIC     = 20260331
DEVIATION = 30          # max price deviation for market order (points)
MIN_LOT   = 0.01        # XAUUSD minimum lot size on RoboForex

LOG_DIR   = Path(r"C:\FluxQuantumAI\logs")
TRADES_CSV = LOG_DIR / "trades.csv"
GATE_CSV   = LOG_DIR / "live_log.csv"

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
# MT5 initialisation
# ---------------------------------------------------------------------------
MT5_AVAILABLE = False
mt5 = None
_mt5 = None
_init_kwargs: dict = {"timeout": 10000}

def _load_env_robo(path: str = ".env"):
    import os
    from pathlib import Path as _Path
    env_path = _Path(path)
    if not env_path.exists():
        env_path = _Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    import os as _os
                    _os.environ.setdefault(key.strip(), val.strip())

_load_env_robo()

import os as _os_robo
_ROBO_TERMINAL = _os_robo.environ.get("ROBOFOREX_TERMINAL",
                                      "C:/Program Files/RoboForex MT5 Terminal/terminal64.exe")
_ROBO_PASSWORD = _os_robo.environ.get("ROBOFOREX_PASSWORD", "")
_ROBO_SERVER   = _os_robo.environ.get("ROBOFOREX_SERVER", "RoboForex-Pro")

try:
    import MetaTrader5 as _mt5
    # Deferred initialization: do NOT call mt5.initialize() at import time.
    # mt5.initialize() can segfault in Session 0 (Windows service) when the terminal
    # is not running in the same session. Connection happens lazily via reconnect().
    if _ROBO_TERMINAL:
        _init_kwargs["path"]     = _ROBO_TERMINAL
        _init_kwargs["login"]    = ACCOUNT
        _init_kwargs["server"]   = _ROBO_SERVER
        if _ROBO_PASSWORD:
            _init_kwargs["password"] = _ROBO_PASSWORD
    mt5 = _mt5
    log.info("MT5 module loaded — connection deferred to first reconnect() call")
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 not installed — executor running in dry-run mode")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round_lot(raw: float) -> float:
    """Round lot to nearest step (0.01)."""
    return round(round(raw / MIN_LOT) * MIN_LOT, 2)


def _split_lots(total: float) -> tuple[float, float, float]:
    """
    Split total lot into 3 legs (40/40/20).
    Returns (leg1, leg2, leg3). leg3 may be 0.0 if total < 0.03.
    """
    l1 = _round_lot(total * 0.40)
    l2 = _round_lot(total * 0.40)
    l3 = _round_lot(total * 0.20)
    return l1, l2, l3


def _get_tick(symbol: str) -> Optional[object]:
    if mt5 is None:
        return None

    try:
        sinfo = mt5.symbol_info(symbol)
        if sinfo is None:
            log.error("symbol_info failed for %s", symbol)
            return None

        if not getattr(sinfo, "visible", True):
            if not mt5.symbol_select(symbol, True):
                log.error("symbol_select failed for %s: %s", symbol, mt5.last_error())
                return None

        tick = mt5.symbol_info_tick(symbol)
    except Exception as e:
        log.error("symbol tick fetch failed for %s: %s", symbol, e)
        return None

    if tick is None:
        log.error("No tick for %s: %s", symbol, mt5.last_error())
    return tick


def _order_type_buy_sell(direction: str):
    """Return MT5 order type constants for direction."""
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
# MT5Executor
# ---------------------------------------------------------------------------

class MT5Executor:

    def __init__(self):
        self.connected = False
        _ensure_logs()

    def reconnect(self) -> bool:
        """Attempt to reconnect to MT5 if disconnected. Called before execution."""
        global mt5, MT5_AVAILABLE

        if self.connected:
            return True
        try:
            if _mt5 is None:
                self.connected = False
                mt5 = None
                MT5_AVAILABLE = False
                return False

            if _mt5.initialize(**_init_kwargs):
                info = _mt5.account_info()
                if info and info.login == ACCOUNT:
                    mt5 = _mt5
                    MT5_AVAILABLE = True
                    self.connected = True
                    log.info("MT5 RECONNECTED — account %d", ACCOUNT)
                    return True

                self.connected = False
                mt5 = None
                MT5_AVAILABLE = False
                log.warning("MT5 reconnect: account mismatch")
            else:
                self.connected = False
                mt5 = None
                MT5_AVAILABLE = False
                log.debug("MT5 reconnect failed: %s", _mt5.last_error())
        except Exception as e:
            self.connected = False
            mt5 = None
            MT5_AVAILABLE = False
            log.debug("MT5 reconnect error: %s", e)
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
        """
        Open 3-leg position.

        Parameters
        ----------
        explicit_lots : list of 3 floats, optional
            If provided, use these exact [leg1, leg2, leg3] lots instead of
            splitting lot_size 40/40/20. Each value is rounded to 0.01.

        Returns
        -------
        {
            'success': bool,
            'tickets': [t1, t2, t3],  # 0 = not opened
            'legs': 2 or 3,
            'entry': float,
            'lots': [l1, l2, l3],
            'error': str or None,
        }
        """
        if explicit_lots is not None:
            l1 = _round_lot(explicit_lots[0])
            l2 = _round_lot(explicit_lots[1])
            l3 = _round_lot(explicit_lots[2])
        else:
            l1, l2, l3 = _split_lots(lot_size)
        legs = 3 if l3 >= MIN_LOT else 2

        if dry_run:
            log.info("[DRY RUN] WOULD OPEN %s %s %.2f lots — Leg1=%.2f Leg2=%.2f Leg3=%.2f",
                     symbol, direction, lot_size, l1, l2, l3)
            return {
                "success": True, "dry_run": True,
                "tickets": [0, 0, 0], "legs": legs,
                "entry": 0.0, "lots": [l1, l2, l3],
                "error": None,
            }

        if not self.connected:
            self.reconnect()  # try to reconnect before giving up
        if not self.connected:
            return {"success": False, "error": "MT5 not connected", "tickets": [0, 0, 0]}

        tick = _get_tick(symbol)
        if tick is None:
            return {"success": False, "error": "No market tick", "tickets": [0, 0, 0]}

        order_type, _ = _order_type_buy_sell(direction)
        entry_price   = tick.ask if direction.upper() == "LONG" else tick.bid

        tickets = []
        tps     = [tp1, tp2 if tp2 else 0.0, 0.0]    # leg3 has no TP (runner)
        lots    = [l1, l2, l3 if legs == 3 else 0.0]

        for i, (lot, tp) in enumerate(zip(lots, tps)):
            if lot < MIN_LOT:
                tickets.append(0)
                continue
            leg_label = "APEX_L%d" % (i + 1)
            req = {
                "action":      mt5.TRADE_ACTION_DEAL,
                "symbol":      symbol,
                "volume":      lot,
                "type":        order_type,
                "price":       entry_price,
                "sl":          sl,
                "tp":          tp if tp else 0.0,
                "deviation":   DEVIATION,
                "magic":       MAGIC,
                "comment":     leg_label,
                "type_time":   mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            chk = mt5.order_check(req)
            if chk is None or chk.retcode != mt5.TRADE_RETCODE_DONE:
                err = chk.comment if chk else str(mt5.last_error())
                log.error("Leg %d order_check failed: %s", i + 1, err)
                tickets.append(0)
                continue

            res = mt5.order_send(req)
            if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
                err = res.comment if res else str(mt5.last_error())
                log.error("Leg %d order failed: %s", i + 1, err)
                tickets.append(0)
            else:
                tickets.append(res.order)
                log.info("Leg %d opened — ticket=%d  lot=%.2f  price=%.2f",
                         i + 1, res.order, lot, res.price)

        success = any(t > 0 for t in tickets)
        return {
            "success": success,
            "tickets": tickets,
            "legs": legs,
            "entry": entry_price,
            "lots": lots,
            "error": None if success else "All legs failed",
        }

    # -----------------------------------------------------------------------
    # move_to_breakeven — SHIELD activation
    # -----------------------------------------------------------------------

    def move_to_breakeven(
        self,
        ticket_leg2: int,
        ticket_leg3: int,
        entry_price: float,
    ) -> dict:
        """
        Move SL of Leg 2 (and Leg 3 if open) to entry_price.
        Called after Leg 1 hits TP1. Trade can no longer be negative.

        Returns {'success': bool, 'modified': [ticket, ...], 'errors': []}
        """
        results = {"success": True, "modified": [], "errors": []}
        for ticket in (ticket_leg2, ticket_leg3):
            if ticket <= 0:
                continue
            ok, msg = self._modify_sl(ticket, entry_price)
            if ok:
                results["modified"].append(ticket)
                log.info("SHIELD: SL moved to entry %.2f on ticket %d", entry_price, ticket)
            else:
                results["errors"].append("ticket %d: %s" % (ticket, msg))
                results["success"] = False
                log.warning("SHIELD failed for ticket %d: %s", ticket, msg)
        return results

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
        """
        Place a pending LIMIT order (BUY_LIMIT or SELL_LIMIT).
        Auto-expires after expiry_hours to prevent stale orders.

        Returns {'success': bool, 'ticket': int, 'error': str|None}
        """
        if dry_run:
            log.info("[DRY RUN] WOULD open %s LIMIT %.2f lots @ %.2f  sl=%.2f tp=%.2f",
                     direction, lot, limit_price, sl, tp)
            return {"success": True, "dry_run": True, "ticket": 0, "error": None}

        if not self.connected:
            return {"success": False, "ticket": 0, "error": "MT5 not connected"}

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
        # Pending orders return TRADE_RETCODE_PLACED (10008) or TRADE_RETCODE_DONE (10009)
        if res is None or res.retcode not in (
            mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED
        ):
            err = res.comment if res else str(mt5.last_error())
            log.error("open_limit %s %s @%.2f failed: %s", symbol, direction, limit_price, err)
            return {"success": False, "ticket": 0, "error": err}

        log.info("open_limit OK ticket=%d  %s %s %.2f  limit=%.2f  sl=%.2f  tp=%.2f",
                 res.order, symbol, direction, lot, limit_price, sl, tp)
        return {"success": True, "ticket": res.order, "error": None}

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
        """
        Open a single-lot market order (used for hedge — not split into legs).
        Returns {'success': bool, 'ticket': int, 'entry': float, 'error': str|None}
        """
        if dry_run:
            log.info("[DRY RUN] WOULD open single %s %s %.2f lots sl=%.2f", symbol, direction, lot, sl)
            return {"success": True, "dry_run": True, "ticket": 0, "entry": 0.0, "error": None}

        if not self.connected:
            return {"success": False, "ticket": 0, "entry": 0.0, "error": "MT5 not connected"}

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
            log.error("open_single %s %s failed: %s", symbol, direction, err)
            return {"success": False, "ticket": 0, "entry": entry_price, "error": err}

        log.info("open_single OK ticket=%d  %s %s %.2f  entry=%.2f  sl=%.2f",
                 res.order, symbol, direction, lot, entry_price, sl)
        return {"success": True, "ticket": res.order, "entry": entry_price, "error": None}

    def _modify_sl(self, ticket: int, new_sl: float) -> tuple[bool, str]:
        """Modify SL of an open position by ticket. Keeps existing TP."""
        if not self.connected:
            return False, "MT5 not connected"
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
            err = res.comment if res else str(mt5.last_error())
            return False, err
        return True, "ok"

    # -----------------------------------------------------------------------
    # close_position — close single leg by ticket
    # -----------------------------------------------------------------------

    def close_position(self, ticket: int) -> dict:
        """
        Close a single open position by ticket using opposite market order.

        Returns {'success': bool, 'ticket': int, 'pnl': float, 'error': str|None}
        """
        if not self.connected:
            return {"success": False, "error": "MT5 not connected", "ticket": ticket, "pnl": 0.0}

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
            log.error("close_position ticket=%d failed: %s", ticket, err)
            return {"success": False, "error": err, "ticket": ticket, "pnl": 0.0}

        pnl = round(pos.profit, 2)
        log.info("Closed ticket=%d  pnl=%.2f", ticket, pnl)
        return {"success": True, "ticket": ticket, "pnl": pnl, "error": None}

    # -----------------------------------------------------------------------
    # get_open_positions — filtered by APEX comment + magic
    # -----------------------------------------------------------------------

    def get_open_positions(self) -> list[dict]:
        """
        Return all open APEX positions on account 68302120.
        Filters by magic=MAGIC to identify our trades.
        """
        if not self.connected:
            return []
        try:
            positions = mt5.positions_get()
            if positions is None:
                return []
            result = []
            for pos in positions:
                if pos.magic != MAGIC:
                    continue
                result.append({
                    "ticket":     pos.ticket,
                    "symbol":     pos.symbol,
                    "direction":  "LONG" if pos.type == 0 else "SHORT",
                    "volume":     pos.volume,
                    "entry":      pos.price_open,
                    "sl":         pos.sl,
                    "tp":         pos.tp,
                    "pnl":        round(pos.profit, 2),
                    "comment":    pos.comment,
                    "time_open":  datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
                })
            return result
        except Exception as e:
            log.error("get_open_positions: %s", e)
            return []

    # -----------------------------------------------------------------------
    # log_trade — append row to trades.csv
    # -----------------------------------------------------------------------

    def log_trade(
        self,
        *,
        direction: str,
        decision: str,
        lots: float,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float = 0.0,
        result: str = "",
        pnl: float = 0.0,
        gate_score: int = 0,
        leg1_ticket: int = 0,
        leg2_ticket: int = 0,
        leg3_ticket: int = 0,
        asset: str = "AX1",
        entry_mode: str = "",
        daily_trend: str = "",
        phase: str = "",
        strategy_mode: str = "",
    ) -> None:
        """Append one trade row to logs/trades.csv."""
        _ensure_logs()
        row = {
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "asset":        asset,
            "direction":    direction,
            "decision":     decision,
            "lots":         lots,
            "entry":        entry,
            "sl":           sl,
            "tp1":          tp1,
            "tp2":          tp2,
            "result":       result,
            "pnl":          pnl,
            "gate_score":   gate_score,
            "leg1_ticket":  leg1_ticket,
            "leg2_ticket":  leg2_ticket,
            "leg3_ticket":  leg3_ticket,
            "entry_mode":   entry_mode,
            "daily_trend":  daily_trend,
            "phase":        phase,
            "strategy_mode": strategy_mode,
        }
        with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=TRADES_COLUMNS).writerow(row)

    # -----------------------------------------------------------------------
    # log_gate — append row to live_log.csv
    # -----------------------------------------------------------------------

    def log_gate(
        self,
        *,
        symbol: str = "AX1",
        direction: str,
        gate_decision: str,
        score: int = 0,
        macro_delta: float = 0.0,
        mom_status: str = "",
        v4_status: str = "UNKNOWN",
        reason: str = "",
        trigger: str = "liq_touch",
        zone: str = "",
        patterns: str = "",
    ) -> None:
        """Append one gate-check row to logs/live_log.csv."""
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

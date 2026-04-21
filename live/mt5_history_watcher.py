"""
MT5 Deal History Watcher
Detects position closures and classifies them (TP, SL, manual, system).

Called by position_monitor when position count drops.
Uses MetaTrader5.history_deals_get() as ground truth.

Design decision (Barbara 2026-04-18): TP1 vs TP2 attribution via trades.csv lookup
by ticket (Option X1). Ticket is unique identifier — deterministic, not price-based.

Created: Fase 3 Scope B.1
Design doc: DESIGN_DOC_Telegram_PositionEvents_v1.md
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
        watcher = MT5HistoryWatcher(executor, magic_number)
        # When position monitor detects count drop:
        closed_deals = watcher.find_closed_since_last_check()
        for deal in closed_deals:
            if deal["reason_label"] == "TP_HIT":
                # emit TP event (caller decides TP1 vs TP2 via trades.csv lookup)
    """

    # MT5 deal reason codes (MetaTrader5.DEAL_REASON_*)
    REASON_CLIENT   = 0   # manual close from terminal
    REASON_MOBILE   = 1   # manual close from mobile
    REASON_WEB      = 2   # manual close from web
    REASON_EXPERT   = 3   # closed by EA (our code via close_position)
    REASON_SL       = 4   # stop loss hit
    REASON_TP       = 5   # take profit hit
    REASON_SO       = 6   # stop out (margin call)
    REASON_ROLLOVER = 7   # rollover
    REASON_VMARGIN  = 8   # variation margin
    REASON_SPLIT    = 9   # split

    REASON_LABELS = {
        REASON_CLIENT:   "MANUAL_TERMINAL",
        REASON_MOBILE:   "MANUAL_MOBILE",
        REASON_WEB:      "MANUAL_WEB",
        REASON_EXPERT:   "SYSTEM_CLOSE",
        REASON_SL:       "SL_HIT",
        REASON_TP:       "TP_HIT",
        REASON_SO:       "STOP_OUT",
        REASON_ROLLOVER: "ROLLOVER",
        REASON_VMARGIN:  "VMARGIN",
        REASON_SPLIT:    "SPLIT",
    }

    # Labels that qualify as a "position close" we want to emit events for
    CLOSE_LABELS = {"SL_HIT", "TP_HIT", "MANUAL_TERMINAL", "MANUAL_MOBILE",
                    "MANUAL_WEB", "STOP_OUT"}

    def __init__(self, executor, magic_number: int):
        """
        :param executor: MT5Executor instance (has connected flag)
        :param magic_number: EA magic number to filter our deals
        """
        self.executor = executor
        self.magic = int(magic_number)
        # Initialize to 5 min ago so first check catches recent closes on startup
        self._last_check_ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        log.info("MT5HistoryWatcher initialized: magic=%d, last_check=%s",
                 self.magic, self._last_check_ts.isoformat())

    def find_closed_since_last_check(self) -> list[dict]:
        """
        Return list of deals closed since last call. Advances internal cursor.

        Each deal dict has keys:
          - ticket (int): MT5 deal ticket
          - position_id (int): position identifier (groups legs)
          - symbol (str): trading symbol
          - type (str): "BUY" or "SELL"
          - volume (float): volume in lots
          - price (float): close price
          - profit (float): realized profit
          - reason_code (int): raw MT5 reason code
          - reason_label (str): human-readable label (e.g. "TP_HIT", "SL_HIT")
          - time (datetime UTC): when deal was executed
          - comment (str): deal comment
          - magic (int): EA magic number

        Returns empty list if MT5 disconnected or no new deals.
        """
        if not getattr(self.executor, "connected", False):
            log.debug("MT5 not connected — skipping history check")
            return []

        from_date = self._last_check_ts
        to_date = datetime.now(timezone.utc)

        try:
            deals = mt5.history_deals_get(from_date, to_date)
        except Exception as e:
            log.warning("history_deals_get exception: %s", e)
            return []

        if deals is None:
            last_err = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
            log.debug("history_deals_get returned None (err=%s)", last_err)
            # Still advance cursor to avoid replay on transient errors
            self._last_check_ts = to_date
            return []

        result = []
        for d in deals:
            # Filter: only OUR deals (by magic number)
            try:
                if int(d.magic) != self.magic:
                    continue
                # Filter: only EXIT deals (entry=1 means position close/exit)
                if int(d.entry) != 1:
                    continue
            except Exception:
                continue

            reason_code = int(d.reason)
            reason_label = self.REASON_LABELS.get(reason_code, f"UNKNOWN_{reason_code}")

            # Build deal dict
            result.append({
                "ticket":       int(d.ticket),
                "position_id":  int(d.position_id),
                "symbol":       str(d.symbol),
                "type":         "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL",
                "volume":       float(d.volume),
                "price":        float(d.price),
                "profit":       float(d.profit),
                "reason_code":  reason_code,
                "reason_label": reason_label,
                "time":         datetime.fromtimestamp(d.time, tz=timezone.utc),
                "comment":      str(d.comment) if d.comment else "",
                "magic":        int(d.magic),
            })

        self._last_check_ts = to_date
        if result:
            log.info("MT5 history check: %d deals classified since %s",
                     len(result), from_date.isoformat())
            for r in result:
                log.debug("  - ticket=%d pos=%d reason=%s profit=%+.2f",
                          r["ticket"], r["position_id"],
                          r["reason_label"], r["profit"])
        return result

    def is_close_event(self, deal: dict) -> bool:
        """
        Returns True if this deal represents a close we want to emit event for.
        (Filters out SYSTEM_CLOSE since position_monitor already emitted event,
        and ROLLOVER/VMARGIN/SPLIT which are not trading events.)
        """
        return deal.get("reason_label", "") in self.CLOSE_LABELS

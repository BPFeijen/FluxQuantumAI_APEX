"""BrokerMock: in-memory replacement for CTraderExecutor / MT5Executor.

Implements the public interface used by event_processor.py and
position_monitor.py. Accepts orders, returns deterministic fake tickets,
maintains an in-memory positions cache. Records every call into an
event log for post-replay forensics.

Fill semantics:
  - open_position / open_single / open_limit:
      success=True, ticket=auto-incremented, entry=current_price (from
      _set_market_price), record into _positions cache.
  - close_position: removes from cache, records exit_price=current_price.
  - _modify_sl: updates sl on the cached position; reports True/OK.

The harness drives _set_market_price(ts, price) before injecting events
so fills are deterministic against the M1 OHLC tape.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional


SYMBOL    = "GCM26"
LOT_SIZE  = 0.02
COMMENT   = "APEX"
MAGIC     = 20260331
MIN_LOT   = 0.01


def _split_lots(total: float) -> tuple[float, float, float]:
    """Mirror live behavior: 40/40/20 split into 3 legs, MIN_LOT floor."""
    l1 = round(total * 0.4, 2)
    l2 = round(total * 0.4, 2)
    l3 = round(total - l1 - l2, 2)
    if l3 < MIN_LOT:
        l3 = 0.0
    return (l1, l2, l3)


def _round_lot(x: float) -> float:
    return round(x, 2)


class BrokerMock:
    """In-memory broker replacement. Thread-safe via internal lock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next_ticket = 9_000_000_000  # distinct from any real ticket range
        self._positions: dict[int, dict] = {}
        self._market_price: float = 0.0
        self._market_ts: Optional[datetime] = None
        self.event_log: list[dict] = []  # every call captured for forensics
        self.connected = True

    # ------------- harness driver -------------
    def _set_market_price(self, ts: datetime, price: float) -> None:
        with self._lock:
            self._market_ts = ts
            self._market_price = float(price)

    def _advance_to_m1_bar(self, ts: datetime,
                            bar_high: float, bar_low: float,
                            bar_close: float) -> list[dict]:
        """Simulate broker fills against an M1 bar's high/low/close.

        For each open position:
          - LONG: SL hit if low <= sl; TP hit if high >= tp (priority SL).
          - SHORT: SL hit if high >= sl; TP hit if low <= tp (priority SL).
        On hit, position is auto-closed with the trigger price as exit.
        Records the close in event_log with kind="auto_close_sl"/"auto_close_tp".

        Returns the list of close events generated this bar.
        """
        closed: list[dict] = []
        with self._lock:
            self._market_ts = ts
            for ticket, pos in list(self._positions.items()):
                direction = pos.get("direction", "")
                sl = float(pos.get("sl", 0))
                tp = float(pos.get("tp", 0))
                entry = float(pos.get("entry", 0))
                hit_kind = None
                exit_price = None

                if direction == "LONG":
                    if sl > 0 and bar_low <= sl:
                        hit_kind = "auto_close_sl"
                        exit_price = sl
                    elif tp > 0 and bar_high >= tp:
                        hit_kind = "auto_close_tp"
                        exit_price = tp
                elif direction == "SHORT":
                    if sl > 0 and bar_high >= sl:
                        hit_kind = "auto_close_sl"
                        exit_price = sl
                    elif tp > 0 and bar_low <= tp:
                        hit_kind = "auto_close_tp"
                        exit_price = tp

                if hit_kind is not None and exit_price is not None:
                    self._positions.pop(ticket, None)
                    rec = {
                        "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                        "kind": hit_kind, "ticket": ticket,
                        "direction": direction, "entry": entry,
                        "exit": exit_price, "sl": sl, "tp": tp,
                        "leg_index": pos.get("leg_index", 0),
                        "bar_high": bar_high, "bar_low": bar_low,
                        "bar_close": bar_close,
                    }
                    self.event_log.append(rec)
                    closed.append(rec)

            # Set market price last (so any subsequent reads see post-bar value)
            self._market_price = float(bar_close)
        return closed

    def _record(self, kind: str, **payload) -> None:
        with self._lock:
            self.event_log.append({
                "ts": (self._market_ts or datetime.now(timezone.utc)).isoformat(),
                "kind": kind,
                **payload,
            })

    # ------------- broker API: required by event_processor + position_monitor -------------
    def reconnect(self) -> bool:
        self.connected = True
        return True

    def get_balance(self) -> float:
        return 1000.0  # fixed; not consumed for replay decisions

    def get_account_info(self) -> dict:
        return {"login": "MOCK", "balance": 1000.0, "equity": 1000.0,
                "currency": "USD", "leverage": 100}

    def open_position(
        self, symbol: str, direction: str, lot_size: float,
        sl: float, tp1: float, tp2: Optional[float] = None,
        dry_run: bool = False, explicit_lots: Optional[list] = None,
    ) -> dict:
        if explicit_lots is not None:
            l1, l2, l3 = (_round_lot(explicit_lots[0]),
                          _round_lot(explicit_lots[1]),
                          _round_lot(explicit_lots[2]))
        else:
            l1, l2, l3 = _split_lots(lot_size)
        legs = 3 if l3 >= MIN_LOT else 2

        with self._lock:
            entry = self._market_price
            tps = [tp1, tp2 if tp2 else 0.0, 0.0]
            lots = [l1, l2, l3 if legs == 3 else 0.0]
            tickets: list[int] = []
            for i, (lot, tp) in enumerate(zip(lots, tps)):
                if lot < MIN_LOT:
                    tickets.append(0)
                    continue
                self._next_ticket += 1
                pid = self._next_ticket
                self._positions[pid] = {
                    "ticket": pid,
                    "symbol": symbol,
                    "direction": direction.upper(),
                    "volume": lot,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "label": f"APEX_L{i+1}",
                    "comment": COMMENT,
                    "magic": MAGIC,
                    "time_open": (self._market_ts or datetime.now(timezone.utc)).isoformat(),
                    "leg_index": i + 1,
                }
                tickets.append(pid)

            self._record("open_position", direction=direction, lots=lots,
                         entry=entry, sl=sl, tp1=tp1, tp2=tp2, tickets=tickets)
            return {
                "success": any(t > 0 for t in tickets),
                "tickets": tickets, "legs": legs, "entry": entry,
                "lots": lots, "error": None,
            }

    def open_single(self, symbol: str, direction: str, lot: float,
                    sl: float, tp: float = 0.0,
                    comment: str = "APEX_HEDGE", dry_run: bool = False) -> dict:
        with self._lock:
            self._next_ticket += 1
            pid = self._next_ticket
            entry = self._market_price
            self._positions[pid] = {
                "ticket": pid, "symbol": symbol,
                "direction": direction.upper(), "volume": lot,
                "entry": entry, "sl": sl, "tp": tp,
                "label": comment[:32], "comment": comment, "magic": MAGIC,
                "time_open": (self._market_ts or datetime.now(timezone.utc)).isoformat(),
                "leg_index": 1,
            }
            self._record("open_single", direction=direction, lot=lot,
                         entry=entry, sl=sl, tp=tp, ticket=pid)
            return {"success": True, "ticket": pid, "entry": entry, "error": None}

    def open_limit(self, symbol: str, direction: str, lot: float,
                   limit_price: float, sl: float, tp: float = 0.0,
                   comment: str = "APEX_LIMIT", dry_run: bool = False) -> dict:
        # In replay, limit orders never get the real fill machinery; record and
        # treat as a queued order with limit price as nominal entry.
        with self._lock:
            self._next_ticket += 1
            pid = self._next_ticket
            self._positions[pid] = {
                "ticket": pid, "symbol": symbol,
                "direction": direction.upper(), "volume": lot,
                "entry": limit_price, "sl": sl, "tp": tp,
                "label": comment[:32], "comment": comment, "magic": MAGIC,
                "time_open": (self._market_ts or datetime.now(timezone.utc)).isoformat(),
                "leg_index": 0, "pending_limit": True,
            }
            self._record("open_limit", direction=direction, lot=lot,
                         limit=limit_price, sl=sl, tp=tp, ticket=pid)
            return {"success": True, "ticket": pid, "entry": limit_price, "error": None}

    def move_to_breakeven(self, leg2_ticket: int, leg3_ticket: int,
                          entry_price: float) -> dict:
        with self._lock:
            updated = []
            for tkt in (leg2_ticket, leg3_ticket):
                if tkt and tkt in self._positions:
                    self._positions[tkt]["sl"] = entry_price
                    updated.append(tkt)
            self._record("move_to_breakeven", entry=entry_price,
                         updated_tickets=updated)
            return {"success": bool(updated), "updated": updated}

    def _modify_sl(self, ticket: int, new_sl: float,
                   trailing: bool = False) -> tuple[bool, str]:
        with self._lock:
            if ticket in self._positions:
                old_sl = self._positions[ticket]["sl"]
                self._positions[ticket]["sl"] = new_sl
                self._record("modify_sl", ticket=ticket, old_sl=old_sl,
                             new_sl=new_sl, trailing=trailing)
                return True, "OK"
            self._record("modify_sl_FAIL", ticket=ticket, new_sl=new_sl,
                         reason="ticket not in cache")
            return False, "ticket not found"

    def close_position(self, ticket: int) -> dict:
        with self._lock:
            if ticket not in self._positions:
                self._record("close_FAIL", ticket=ticket,
                             reason="ticket not in cache")
                return {"success": False, "error": "ticket not found"}
            pos = self._positions.pop(ticket)
            exit_price = self._market_price
            self._record("close_position", ticket=ticket,
                         direction=pos["direction"], entry=pos["entry"],
                         exit=exit_price, sl=pos["sl"], tp=pos["tp"],
                         leg_index=pos.get("leg_index", 0))
            return {"success": True, "ticket": ticket,
                    "entry": pos["entry"], "exit": exit_price,
                    "direction": pos["direction"]}

    def get_open_positions(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._positions.values()]

    def enable_native_trailing(self, ticket: int) -> tuple[bool, str]:
        with self._lock:
            if ticket in self._positions:
                self._positions[ticket]["trailing_enabled"] = True
                return True, "OK"
            return False, "ticket not found"

    # ------------- logging shims -------------
    def log_trade(self, **kwargs) -> None:
        self._record("log_trade", **kwargs)

    def log_gate(self, **kwargs) -> None:
        self._record("log_gate", **kwargs)

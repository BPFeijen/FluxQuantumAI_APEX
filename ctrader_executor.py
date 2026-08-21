#!/usr/bin/env python3
"""
C:\\FluxQuantumAI\\ctrader_executor.py
APEX cTrader Open API execution engine — sync facade over Twisted async client.

Mirrors the public interface of mt5_executor.MT5Executor so it can be used
as a drop-in replacement when BROKER=ctrader. Symbol GC is NATIVE on IC
Markets cTrader (no XAUUSD mapping).

Authentication
--------------
cTrader Open API uses OAuth2 authorization-code flow.
First-time setup: run scripts/ctrader_oauth_init.py to populate
CTRADER_ACCESS_TOKEN and CTRADER_REFRESH_TOKEN in .env.

Required env vars
-----------------
CTRADER_CLIENT_ID
CTRADER_CLIENT_SECRET
CTRADER_ACCOUNT_ID_DEMO   (e.g. 9998303)
CTRADER_ACCOUNT_ID_LIVE   (e.g. 6144564)
CTRADER_ACCESS_TOKEN
CTRADER_REFRESH_TOKEN
CTRADER_ACCOUNT_MODE      ("demo" | "live", default "demo")

Architecture
------------
The cTrader Open API lib uses Twisted reactor (event-driven). To present
a synchronous interface compatible with the MT5 pattern, we run the reactor
in a dedicated daemon thread, then dispatch each request via
reactor.callFromThread + a queue that the calling thread blocks on.

Composite 3-leg APEX trade
--------------------------
cTrader has no native split-leg position with shared SL. We open 3 separate
market orders with distinct labels (APEX_L1/L2/L3). SHIELD modifies SL on
the remaining 2 legs after Leg 1 hits TP1.
"""

from __future__ import annotations

import csv
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.executor.ctrader")

# ---------------------------------------------------------------------------
# Constants — kept structurally identical to mt5_executor for compat
# ---------------------------------------------------------------------------
SYMBOL    = os.environ.get("CTRADER_SYMBOL", "GCM26")  # GC front-month futures (Jun 2026); was "GC" generic which doesn't match any account symbol. Update CTRADER_SYMBOL env var when contract rolls (e.g. GCQ26 = Aug 2026 next).
LOT_SIZE  = 0.02
COMMENT   = "APEX"
MAGIC     = 20260331
MIN_LOT   = 0.01

LOG_DIR    = Path(r"C:\FluxQuantumAI\logs")
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

# Resolved at connect-time from CTRADER_ACCOUNT_MODE
ACCOUNT: int = 0


# ---------------------------------------------------------------------------
# Lazy lib import — only when CTraderExecutor is instantiated
# ---------------------------------------------------------------------------
CTRADER_AVAILABLE = False
_lib_err: Optional[str] = None
try:
    from ctrader_open_api import Client, EndPoints, TcpProtocol  # type: ignore
    from ctrader_open_api.auth import Auth  # type: ignore
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (  # type: ignore
        ProtoOAApplicationAuthReq,
        ProtoOAAccountAuthReq,
        ProtoOASymbolsListReq,
        ProtoOASymbolByIdReq,
        ProtoOANewOrderReq,
        ProtoOAClosePositionReq,
        ProtoOAReconcileReq,
        ProtoOATraderReq,
        ProtoOAAmendPositionSLTPReq,
    )
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (  # type: ignore
        ProtoHeartbeatEvent,  # required to keep cTrader connection alive (~25s)
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (  # type: ignore
        ProtoOAOrderType,
        ProtoOATradeSide,
        ProtoOATimeInForce,
    )
    CTRADER_AVAILABLE = True
except ImportError as _e:
    _lib_err = str(_e)
    log.warning("ctrader-open-api not installed — executor running in dry-run mode: %s", _lib_err)


# ---------------------------------------------------------------------------
# Env loader (same pattern as mt5_executor._load_env_robo)
# ---------------------------------------------------------------------------

def _load_env_ct(path: str = ".env") -> None:
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

_load_env_ct()


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


def _ensure_logs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_CSV.exists():
        with open(TRADES_CSV, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=TRADES_COLUMNS).writeheader()
    if not GATE_CSV.exists():
        with open(GATE_CSV, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=GATE_COLUMNS).writeheader()


# ---------------------------------------------------------------------------
# CTraderExecutor
# ---------------------------------------------------------------------------

class CTraderExecutor:
    """Sync facade for cTrader Open API. Compatible with mt5_executor.MT5Executor."""

    def __init__(self) -> None:
        self.connected: bool = False
        self._client = None
        self._reactor_thread: Optional[threading.Thread] = None
        self._reactor = None  # twisted.internet.reactor
        self._account_id: int = 0       # ctidTraderAccountId
        self._symbols: dict[str, dict] = {}   # name -> {id, digits, lotSize, minVolume}
        self._positions_cache: dict[int, dict] = {}   # positionId -> {symbol, side, volume, entry, sl, tp, label, ts}
        self._tick_cache: dict[int, dict] = {}        # symbolId -> {bid, ask, ts}
        self._lib_err = _lib_err
        _ensure_logs()
        if not CTRADER_AVAILABLE:
            log.error("CTraderExecutor: ctrader-open-api lib unavailable (%s)", self._lib_err)
            return
        self._configure_account()

    # --- account / env -----------------------------------------------------

    def _configure_account(self) -> None:
        global ACCOUNT
        mode = os.environ.get("CTRADER_ACCOUNT_MODE", "demo").lower()
        if mode == "live":
            self._account_id = int(os.environ.get("CTRADER_ACCOUNT_ID_LIVE", "0") or 0)
            self._host = EndPoints.PROTOBUF_LIVE_HOST
        else:
            self._account_id = int(os.environ.get("CTRADER_ACCOUNT_ID_DEMO", "0") or 0)
            self._host = EndPoints.PROTOBUF_DEMO_HOST
        ACCOUNT = self._account_id
        self._port = EndPoints.PROTOBUF_PORT
        self._client_id = os.environ.get("CTRADER_CLIENT_ID", "")
        self._client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "")
        self._access_token = os.environ.get("CTRADER_ACCESS_TOKEN", "")
        self._refresh_token = os.environ.get("CTRADER_REFRESH_TOKEN", "")
        self._mode = mode

    # --- reactor lifecycle -------------------------------------------------

    def _start_reactor(self) -> None:
        """Start Twisted reactor in a dedicated daemon thread (idempotent)."""
        from twisted.internet import reactor as _reactor
        self._reactor = _reactor
        if self._reactor.running:
            return
        self._reactor_thread = threading.Thread(
            target=lambda: self._reactor.run(installSignalHandlers=False),
            name="ctrader-reactor", daemon=True,
        )
        self._reactor_thread.start()
        # Small wait for reactor to be ready
        for _ in range(50):
            if self._reactor.running:
                break
            time.sleep(0.02)

    def _sync_send(self, message, timeout: float = 10.0):
        """Block calling thread until response Deferred fires. Returns the
        TYPED protobuf response (after Protobuf.extract envelope unwrap) OR
        raises RuntimeError on failure/timeout.

        Fix 2026-05-09: cTrader Open API client.send() returns a ProtoMessage
        envelope; the typed response must be extracted via Protobuf.extract.
        Without this, callers see 'ProtoMessage object has no attribute X'."""
        if self._client is None or not self._client.isConnected:
            raise RuntimeError("cTrader client not connected")
        result_q: queue.Queue = queue.Queue(maxsize=1)

        def _submit() -> None:
            try:
                d = self._client.send(message, responseTimeoutInSeconds=int(timeout))
                d.addCallback(lambda r: result_q.put(("ok", r)))
                d.addErrback(lambda f: result_q.put(("err", f)))
            except Exception as e:
                result_q.put(("err", e))

        self._reactor.callFromThread(_submit)
        try:
            kind, val = result_q.get(timeout=timeout + 5.0)
        except queue.Empty:
            raise RuntimeError(f"cTrader request timeout after {timeout}s")
        if kind == "ok":
            try:
                from ctrader_open_api import Protobuf
                typed = Protobuf.extract(val)
            except Exception:
                return val
            # Detect error response and surface real error code/description
            type_name = type(typed).__name__
            if "ErrorRes" in type_name:
                err_code = getattr(typed, "errorCode", "?")
                desc = getattr(typed, "description", "")
                raise RuntimeError(f"cTrader API error {err_code}: {desc} (response={type_name})")
            return typed
        raise RuntimeError(f"cTrader request failed: {val}")

    # --- connect -----------------------------------------------------------

    def reconnect(self) -> bool:
        """Idempotent. Ensures full auth chain: app → account → symbols loaded.
        Returns True only when ready to trade."""
        if not CTRADER_AVAILABLE:
            return False
        if self.connected:
            return True

        # Validate credentials
        if not self._client_id or not self._client_secret:
            log.error("cTrader credentials missing — set CTRADER_CLIENT_ID/CLIENT_SECRET in .env")
            return False
        if not self._access_token:
            log.error(
                "cTrader access_token missing — run scripts/ctrader_oauth_init.py once "
                "to authorize and populate CTRADER_ACCESS_TOKEN/REFRESH_TOKEN"
            )
            return False
        if self._account_id <= 0:
            log.error("cTrader account_id missing for mode=%s", self._mode)
            return False

        try:
            self._start_reactor()
            ready_evt = threading.Event()
            connect_err = {"err": None}

            def _on_connected(_client) -> None:
                # We trigger ApplicationAuth from our own thread sequence
                ready_evt.set()

            def _on_disconnected(_client, reason) -> None:
                self.connected = False
                log.warning("cTrader disconnected: %s", reason)
                # Trigger auto-reconnect on next reconnect() call. The event
                # processor's open_position() path checks self.connected and
                # invokes reconnect() if False; without this callback flag,
                # reconnect was being skipped because some internal state
                # still thought we were connected. Clearing here ensures the
                # next caller does a fresh handshake.

            self._client = Client(self._host, self._port, TcpProtocol)
            self._client.setConnectedCallback(_on_connected)
            self._client.setDisconnectedCallback(_on_disconnected)

            self._reactor.callFromThread(self._client.startService)

            if not ready_evt.wait(timeout=15.0):
                log.error("cTrader TCP/TLS connect timeout (host=%s)", self._host)
                return False

            # 1) ApplicationAuth
            app_req = ProtoOAApplicationAuthReq()
            app_req.clientId = self._client_id
            app_req.clientSecret = self._client_secret
            self._sync_send(app_req, timeout=10.0)

            # 2) AccountAuth (with auto-refresh on token expiry)
            try:
                acc_req = ProtoOAAccountAuthReq()
                acc_req.ctidTraderAccountId = self._account_id
                acc_req.accessToken = self._access_token
                self._sync_send(acc_req, timeout=10.0)
            except RuntimeError as e:
                if self._refresh_token and "TOKEN" in str(e).upper():
                    log.info("Access token expired — attempting refresh")
                    if self._refresh_access_token():
                        acc_req = ProtoOAAccountAuthReq()
                        acc_req.ctidTraderAccountId = self._account_id
                        acc_req.accessToken = self._access_token
                        self._sync_send(acc_req, timeout=10.0)
                    else:
                        raise
                else:
                    raise

            # 3) Load symbol list (name → id, lotSize, digits)
            self._load_symbols()

            # 4) Reconcile open positions
            self._reconcile_positions()

            self.connected = True
            log.info(
                "cTrader %s account %d CONNECTED — %d symbols cached, %d open positions",
                self._mode.upper(), self._account_id, len(self._symbols), len(self._positions_cache),
            )
            # 2026-05-11: outgoing heartbeat removed. ProtoHeartbeatEvent
            # is fire-and-forget but client.send() creates a Deferred that
            # times out and triggers a disconnect cycle.

            # ---- Reconnect watchdog thread ----
            # cTrader server idle-disconnects clean after ~30-60s of inactivity.
            # _on_disconnected callback sets self.connected=False; this watchdog
            # detects the drop and triggers reconnect, so we don't have to wait
            # for the next GO signal to attempt reconnect.
            if not hasattr(self, "_watchdog_thread") or self._watchdog_thread is None or not self._watchdog_thread.is_alive():
                self._watchdog_stop = threading.Event()

                def _watchdog_loop():
                    while not self._watchdog_stop.is_set():
                        self._watchdog_stop.wait(timeout=10.0)
                        if self._watchdog_stop.is_set():
                            break
                        if not self.connected:
                            try:
                                # Tear down old client (best-effort) and
                                # call reconnect() which rebuilds state.
                                if self._client is not None:
                                    try:
                                        self._reactor.callFromThread(self._client.stopService)
                                    except Exception:
                                        pass
                                    self._client = None
                                log.info("cTrader watchdog: detected disconnect, reconnecting...")
                                self.reconnect()
                            except Exception as e:
                                log.warning("cTrader watchdog reconnect error: %s", e)

                self._watchdog_thread = threading.Thread(
                    target=_watchdog_loop, name="ctrader_watchdog", daemon=True,
                )
                self._watchdog_thread.start()
                log.info("cTrader reconnect watchdog started (10s interval)")
            return True
        except Exception as e:
            self.connected = False
            log.error("cTrader reconnect failed: %s", e)
            return False

    def _refresh_access_token(self) -> bool:
        try:
            auth = Auth(self._client_id, self._client_secret, redirectUri="http://localhost/")
            tok = auth.refreshToken(self._refresh_token)
            new_at = tok.get("accessToken") or tok.get("access_token")
            new_rt = tok.get("refreshToken") or tok.get("refresh_token") or self._refresh_token
            if not new_at:
                log.error("Token refresh response missing accessToken: %s", tok)
                return False
            self._access_token = new_at
            self._refresh_token = new_rt
            os.environ["CTRADER_ACCESS_TOKEN"] = new_at
            os.environ["CTRADER_REFRESH_TOKEN"] = new_rt
            # Persist to .env (best effort) — only the two token fields
            self._persist_tokens(new_at, new_rt)
            log.info("cTrader access_token refreshed")
            return True
        except Exception as e:
            log.error("Token refresh failed: %s", e)
            return False

    def _persist_tokens(self, access_token: str, refresh_token: str) -> None:
        env_path = Path(__file__).parent / ".env"
        if not env_path.exists():
            return
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            updated = {"CTRADER_ACCESS_TOKEN": False, "CTRADER_REFRESH_TOKEN": False}
            out = []
            for ln in lines:
                if ln.startswith("CTRADER_ACCESS_TOKEN="):
                    out.append(f"CTRADER_ACCESS_TOKEN={access_token}")
                    updated["CTRADER_ACCESS_TOKEN"] = True
                elif ln.startswith("CTRADER_REFRESH_TOKEN="):
                    out.append(f"CTRADER_REFRESH_TOKEN={refresh_token}")
                    updated["CTRADER_REFRESH_TOKEN"] = True
                else:
                    out.append(ln)
            if not updated["CTRADER_ACCESS_TOKEN"]:
                out.append(f"CTRADER_ACCESS_TOKEN={access_token}")
            if not updated["CTRADER_REFRESH_TOKEN"]:
                out.append(f"CTRADER_REFRESH_TOKEN={refresh_token}")
            env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        except Exception as e:
            log.warning("Could not persist refreshed tokens to .env: %s", e)

    # --- symbol cache ------------------------------------------------------

    def _load_symbols(self) -> None:
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = self._account_id
        req.includeArchivedSymbols = False
        resp = self._sync_send(req, timeout=15.0)

        # resp has .symbol (repeated) — light-weight entries (no lotSize yet)
        # we cache by symbolName, lotSize will be fetched lazily
        for s in resp.symbol:
            self._symbols[s.symbolName] = {
                "id": s.symbolId,
                "name": s.symbolName,
                "enabled": getattr(s, "enabled", True),
                # Heavier fields filled by _ensure_symbol_full
                "lotSize": None,
                "minVolume": None,
                "digits": None,
            }

    def _ensure_symbol_full(self, symbol_name: str) -> Optional[dict]:
        sym = self._symbols.get(symbol_name)
        if sym is None:
            log.error("cTrader symbol %s not found", symbol_name)
            return None
        if sym.get("lotSize") is not None:
            return sym
        req = ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = self._account_id
        req.symbolId.append(sym["id"])
        resp = self._sync_send(req, timeout=10.0)
        if not resp.symbol:
            log.error("ProtoOASymbolByIdReq empty for %s", symbol_name)
            return None
        s = resp.symbol[0]
        sym["lotSize"] = getattr(s, "lotSize", 100)            # cents per lot
        sym["minVolume"] = getattr(s, "minVolume", 1)          # cents
        sym["digits"] = getattr(s, "digits", 2)
        sym["pipPosition"] = getattr(s, "pipPosition", 0)
        return sym

    def _resolve_symbol(self, name: str) -> Optional[dict]:
        # Try exact match first
        if name in self._symbols:
            return self._ensure_symbol_full(name)
        # Try common variants for GC
        for variant in (name, "GC", "Gold", "GOLD", "GOLDF", "XAUUSD", "GC.Fut"):
            if variant in self._symbols:
                log.warning("cTrader symbol %s resolved as %s", name, variant)
                return self._ensure_symbol_full(variant)
        log.error("cTrader symbol %s not in symbols list", name)
        return None

    def _lots_to_volume(self, sym: dict, lot: float) -> int:
        """Convert MT5-style lot (e.g. 0.02) to cTrader volume in cents."""
        # cTrader volume = lot * lotSize (centiunits). Round to symbol's minVolume granularity.
        raw = int(round(lot * sym["lotSize"]))
        minv = max(int(sym.get("minVolume") or 1), 1)
        # Round to nearest minv multiple
        return max(minv, (raw // minv) * minv)

    # --- positions cache ---------------------------------------------------

    def _reconcile_positions(self) -> None:
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = self._account_id
        resp = self._sync_send(req, timeout=15.0)
        self._positions_cache.clear()
        # Build symbol-id → name reverse map
        id2name = {v["id"]: k for k, v in self._symbols.items()}
        for p in resp.position:
            pid = int(p.positionId)
            tdata = p.tradeData
            sym_id = int(tdata.symbolId)
            sym_name = id2name.get(sym_id, str(sym_id))
            side = "LONG" if tdata.tradeSide == ProtoOATradeSide.BUY else "SHORT"
            volume = int(tdata.volume)
            entry = float(getattr(p, "price", 0.0)) or float(getattr(tdata, "executionPrice", 0.0))
            sl = float(getattr(p, "stopLoss", 0.0))
            tp = float(getattr(p, "takeProfit", 0.0))
            self._positions_cache[pid] = {
                "symbol":    sym_name,
                "symbolId":  sym_id,
                "direction": side,
                "volume":    volume,
                "entry":     entry,
                "sl":        sl,
                "tp":        tp,
                "label":     getattr(p, "label", "") or "",
                "comment":   getattr(p, "comment", "") or "",
                "magic":     0,    # cTrader has no magic number; APEX detection via label prefix
                "time_open": datetime.fromtimestamp(
                    int(getattr(tdata, "openTimestamp", 0)) / 1000, tz=timezone.utc
                ).isoformat() if getattr(tdata, "openTimestamp", 0) else "",
            }

    # --- account info ------------------------------------------------------

    def get_balance(self) -> float:
        if not self.connected:
            return 0.0
        try:
            req = ProtoOATraderReq()
            req.ctidTraderAccountId = self._account_id
            resp = self._sync_send(req, timeout=10.0)
            t = resp.trader
            money_digits = getattr(t, "moneyDigits", 2)
            return round(t.balance / (10 ** money_digits), 2)
        except Exception as e:
            log.error("get_balance: %s", e)
            return 0.0

    # --- order submission --------------------------------------------------

    def _send_market_order(
        self, sym: dict, side_long: bool, volume_cents: int,
        sl: float, tp: float, label: str, comment: str,
    ) -> tuple[Optional[int], str]:
        """Returns (positionId|None, error_str)."""
        try:
            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = self._account_id
            req.symbolId = sym["id"]
            req.orderType = ProtoOAOrderType.MARKET
            req.tradeSide = ProtoOATradeSide.BUY if side_long else ProtoOATradeSide.SELL
            req.volume = volume_cents
            if sl > 0:
                req.stopLoss = float(sl)
            if tp > 0:
                req.takeProfit = float(tp)
            if label:
                req.label = label[:64]
            if comment:
                req.comment = comment[:128]
            resp = self._sync_send(req, timeout=15.0)
            pid = None
            if hasattr(resp, "position") and resp.position and getattr(resp.position, "positionId", 0):
                pid = int(resp.position.positionId)
            elif hasattr(resp, "order") and resp.order and getattr(resp.order, "positionId", 0):
                pid = int(resp.order.positionId)
            if pid:
                return pid, ""
            err_code = getattr(resp, "errorCode", "") or ""
            return None, str(err_code) if err_code else "no positionId in response"
        except Exception as e:
            return None, str(e)

    # --- public: open_position (3-leg APEX) -------------------------------

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
            log.info(
                "[DRY RUN] WOULD OPEN %s %s %.2f lots — Leg1=%.2f Leg2=%.2f Leg3=%.2f",
                symbol, direction, lot_size, l1, l2, l3,
            )
            return {
                "success": True, "dry_run": True,
                "tickets": [0, 0, 0], "legs": legs,
                "entry": 0.0, "lots": [l1, l2, l3], "error": None,
            }

        if not self.connected and not self.reconnect():
            return {"success": False, "error": "cTrader not connected", "tickets": [0, 0, 0]}

        sym = self._resolve_symbol(symbol)
        if sym is None:
            return {"success": False, "error": f"symbol {symbol} unavailable", "tickets": [0, 0, 0]}

        side_long = direction.upper() == "LONG"
        tps   = [tp1, tp2 if tp2 else 0.0, 0.0]
        lots  = [l1, l2, l3 if legs == 3 else 0.0]
        tickets: list[int] = []
        last_err = ""
        entry_price = 0.0

        for i, (lot, tp) in enumerate(zip(lots, tps)):
            if lot < MIN_LOT:
                tickets.append(0)
                continue
            label = "APEX_L%d" % (i + 1)
            vol_cents = self._lots_to_volume(sym, lot)
            pid, err = self._send_market_order(
                sym=sym, side_long=side_long, volume_cents=vol_cents,
                sl=sl, tp=tp, label=label, comment=COMMENT,
            )
            if pid:
                tickets.append(pid)
                # Track in cache
                self._positions_cache[pid] = {
                    "symbol":    symbol,
                    "symbolId":  sym["id"],
                    "direction": "LONG" if side_long else "SHORT",
                    "volume":    vol_cents,
                    "entry":     entry_price,   # filled later by reconcile
                    "sl":        sl,
                    "tp":        tp,
                    "label":     label,
                    "comment":   COMMENT,
                    "magic":     MAGIC,
                    "time_open": datetime.now(timezone.utc).isoformat(),
                }
                log.info("Leg %d opened — positionId=%d  lot=%.2f", i + 1, pid, lot)
            else:
                tickets.append(0)
                last_err = err
                log.error("Leg %d order failed: %s", i + 1, err)

        success = any(t > 0 for t in tickets)
        return {
            "success": success,
            "tickets": tickets,
            "legs":    legs,
            "entry":   entry_price,
            "lots":    lots,
            "error":   None if success else (last_err or "All legs failed"),
        }

    # --- public: open_single ----------------------------------------------

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
            log.info("[DRY RUN] WOULD open single %s %s %.2f lots sl=%.2f", symbol, direction, lot, sl)
            return {"success": True, "dry_run": True, "ticket": 0, "entry": 0.0, "error": None}

        if not self.connected and not self.reconnect():
            return {"success": False, "ticket": 0, "entry": 0.0, "error": "cTrader not connected"}

        sym = self._resolve_symbol(symbol)
        if sym is None:
            return {"success": False, "ticket": 0, "entry": 0.0, "error": f"symbol {symbol} unavailable"}

        side_long = direction.upper() == "LONG"
        vol_cents = self._lots_to_volume(sym, lot)
        pid, err = self._send_market_order(
            sym=sym, side_long=side_long, volume_cents=vol_cents,
            sl=sl, tp=tp, label=comment[:32], comment=comment,
        )
        if pid:
            log.info("open_single OK positionId=%d  %s %s %.2f", pid, symbol, direction, lot)
            return {"success": True, "ticket": pid, "entry": 0.0, "error": None}
        return {"success": False, "ticket": 0, "entry": 0.0, "error": err}

    # --- public: open_limit ------------------------------------------------

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
        if dry_run:
            log.info("[DRY RUN] WOULD open %s LIMIT %.2f lots @ %.2f sl=%.2f tp=%.2f",
                     direction, lot, limit_price, sl, tp)
            return {"success": True, "dry_run": True, "ticket": 0, "error": None}

        if not self.connected and not self.reconnect():
            return {"success": False, "ticket": 0, "error": "cTrader not connected"}

        sym = self._resolve_symbol(symbol)
        if sym is None:
            return {"success": False, "ticket": 0, "error": f"symbol {symbol} unavailable"}

        side_long = direction.upper() == "LONG"
        vol_cents = self._lots_to_volume(sym, lot)
        try:
            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = self._account_id
            req.symbolId = sym["id"]
            req.orderType = ProtoOAOrderType.LIMIT
            req.tradeSide = ProtoOATradeSide.BUY if side_long else ProtoOATradeSide.SELL
            req.volume = vol_cents
            req.limitPrice = float(limit_price)
            req.timeInForce = ProtoOATimeInForce.GOOD_TILL_DATE
            req.expirationTimestamp = int(time.time() * 1000) + int(expiry_hours * 3600 * 1000)
            if sl > 0:
                req.stopLoss = float(sl)
            if tp > 0:
                req.takeProfit = float(tp)
            req.label = comment[:32]
            req.comment = comment[:128]
            resp = self._sync_send(req, timeout=15.0)
            oid = 0
            if hasattr(resp, "order") and resp.order and getattr(resp.order, "orderId", 0):
                oid = int(resp.order.orderId)
            if oid:
                log.info("open_limit OK orderId=%d  %s %s %.2f limit=%.2f sl=%.2f tp=%.2f",
                         oid, symbol, direction, lot, limit_price, sl, tp)
                return {"success": True, "ticket": oid, "error": None}
            err = str(getattr(resp, "errorCode", "no orderId"))
            return {"success": False, "ticket": 0, "error": err}
        except Exception as e:
            log.error("open_limit failed: %s", e)
            return {"success": False, "ticket": 0, "error": str(e)}

    # --- public: move_to_breakeven (SHIELD) -------------------------------

    def move_to_breakeven(
        self, ticket_leg2: int, ticket_leg3: int, entry_price: float,
        enable_trailing: bool = True,
    ) -> dict:
        """SHIELD: move SL to entry price. With cTrader, optionally enable
        native server-side trailing stop in the same atomic amend (default ON).

        With enable_trailing=True: server maintains current (price - SL)
        distance as trailing buffer. As price advances, SL advances; never
        retreats. Eliminates need for manual _modify_sl polling in PM loop.
        Trailing distance = price_at_amend - entry_price (typically TP1_dist).
        """
        results: dict = {"success": True, "modified": [], "errors": []}
        for ticket in (ticket_leg2, ticket_leg3):
            if ticket <= 0:
                continue
            ok, msg = self._modify_sl(ticket, entry_price, trailing=enable_trailing)
            if ok:
                results["modified"].append(ticket)
                _trail_note = " + trailing ON" if enable_trailing else ""
                log.info("SHIELD: SL moved to entry %.2f on positionId %d%s",
                         entry_price, ticket, _trail_note)
            else:
                results["errors"].append("ticket %d: %s" % (ticket, msg))
                results["success"] = False
                log.warning("SHIELD failed for positionId %d: %s", ticket, msg)
        return results

    def enable_native_trailing(self, ticket: int) -> tuple[bool, str]:
        """Enable cTrader server-side trailing stop on existing position.
        Keeps current SL/TP, just sets trailingStopLoss=True. Server then
        adjusts SL automatically as price advances favorably.

        Use AFTER move_to_breakeven OR on positions where current SL is
        already at the desired trailing buffer distance from current price.
        """
        if not self.connected:
            return False, "cTrader not connected"
        try:
            req = ProtoOAAmendPositionSLTPReq()
            req.ctidTraderAccountId = self._account_id
            req.positionId = int(ticket)
            cached = self._positions_cache.get(int(ticket))
            if cached and cached.get("sl"):
                req.stopLoss = float(cached["sl"])
            if cached and cached.get("tp"):
                req.takeProfit = float(cached["tp"])
            req.trailingStopLoss = True
            self._sync_send(req, timeout=10.0)
            log.info("Native trailing stop ENABLED on positionId %d", ticket)
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def _modify_sl(self, ticket: int, new_sl: float, trailing: bool = False) -> tuple[bool, str]:
        """Modify SL on existing position. Optional native trailing.

        Args:
            new_sl: absolute SL price.
            trailing: if True, set trailingStopLoss=True (cTrader native
                      server-side trailing). Server maintains
                      (current_price - new_sl) distance as buffer.
        """
        if not self.connected:
            return False, "cTrader not connected"
        try:
            req = ProtoOAAmendPositionSLTPReq()
            req.ctidTraderAccountId = self._account_id
            req.positionId = int(ticket)
            req.stopLoss = float(new_sl)
            # Keep existing TP if known
            cached = self._positions_cache.get(int(ticket))
            if cached and cached.get("tp"):
                req.takeProfit = float(cached["tp"])
            if trailing:
                req.trailingStopLoss = True
            self._sync_send(req, timeout=10.0)
            if cached:
                cached["sl"] = float(new_sl)
            return True, "ok"
        except Exception as e:
            return False, str(e)

    # --- public: close_position --------------------------------------------

    def close_position(self, ticket: int) -> dict:
        if not self.connected and not self.reconnect():
            return {"success": False, "error": "cTrader not connected", "ticket": ticket, "pnl": 0.0}
        try:
            cached = self._positions_cache.get(int(ticket))
            volume = int(cached["volume"]) if cached else 0
            if not volume:
                # fallback: try reconcile to fetch volume
                self._reconcile_positions()
                cached = self._positions_cache.get(int(ticket))
                volume = int(cached["volume"]) if cached else 0
            if not volume:
                return {"success": False, "error": "position not found", "ticket": ticket, "pnl": 0.0}

            req = ProtoOAClosePositionReq()
            req.ctidTraderAccountId = self._account_id
            req.positionId = int(ticket)
            req.volume = volume
            self._sync_send(req, timeout=15.0)
            self._positions_cache.pop(int(ticket), None)
            log.info("Closed positionId=%d", ticket)
            return {"success": True, "ticket": ticket, "pnl": 0.0, "error": None}
        except Exception as e:
            log.error("close_position positionId=%d failed: %s", ticket, e)
            return {"success": False, "error": str(e), "ticket": ticket, "pnl": 0.0}

    # --- public: get_open_positions ----------------------------------------

    def get_open_positions(self) -> list[dict]:
        """Return APEX positions only (filter by label prefix `APEX`)."""
        if not self.connected:
            return []
        try:
            self._reconcile_positions()
            result = []
            for pid, p in self._positions_cache.items():
                label = (p.get("label") or "")
                comment = (p.get("comment") or "")
                if not (label.startswith("APEX") or comment.startswith("APEX")):
                    continue
                result.append({
                    "ticket":    pid,
                    "symbol":    p["symbol"],
                    "direction": p["direction"],
                    "volume":    p["volume"],
                    "entry":     p["entry"],
                    "sl":        p["sl"],
                    "tp":        p["tp"],
                    "pnl":       0.0,
                    "comment":   comment or label,
                    "time_open": p.get("time_open", ""),
                })
            return result
        except Exception as e:
            log.error("get_open_positions: %s", e)
            return []

    # --- public: log_trade / log_gate --------------------------------------

    def log_trade(self, *, direction: str, decision: str, lots: float,
                  entry: float, sl: float, tp1: float, tp2: float = 0.0,
                  result: str = "", pnl: float = 0.0, gate_score: int = 0,
                  leg1_ticket: int = 0, leg2_ticket: int = 0, leg3_ticket: int = 0,
                  asset: str = "AX1", entry_mode: str = "", daily_trend: str = "",
                  phase: str = "", strategy_mode: str = "") -> None:
        _ensure_logs()
        row = {
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "asset":         asset,
            "direction":     direction,
            "decision":      decision,
            "lots":          lots,
            "entry":         entry,
            "sl":            sl,
            "tp1":           tp1,
            "tp2":           tp2,
            "result":        result,
            "pnl":           pnl,
            "gate_score":    gate_score,
            "leg1_ticket":   leg1_ticket,
            "leg2_ticket":   leg2_ticket,
            "leg3_ticket":   leg3_ticket,
            "entry_mode":    entry_mode,
            "daily_trend":   daily_trend,
            "phase":         phase,
            "strategy_mode": strategy_mode,
        }
        with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=TRADES_COLUMNS).writerow(row)

    def log_gate(self, *, symbol: str = "AX1", direction: str = "",
                 gate_decision: str = "", score: int = 0, macro_delta: float = 0.0,
                 mom_status: str = "", v4_status: str = "UNKNOWN",
                 reason: str = "", trigger: str = "liq_touch",
                 zone: str = "", patterns: str = "") -> None:
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

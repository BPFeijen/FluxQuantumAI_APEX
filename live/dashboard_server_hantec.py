"""
dashboard_server_hantec.py -- Dashboard Live (Hantec 0.02ct).

Porta 8082 · Trades: logs/trades_live.csv · Balance inicial: $346.10
Conta MT5: 50051145 (HantecMarketsMU-MT5) -- credenciais via .env

Herda BaseDashboardHandler -- lógica comum em base_dashboard_server.py.
NextGen endpoints (/api/nextgen/*, /api/v3/*) servidos pela base com dados reais.

G-02 CORRIGIDO: eliminada dependência da porta 8088 (fantasma).
  Dados de gates/phase agora vêm directamente do NextGenDataBus.
G-03 CORRIGIDO: /api/v3/* retorna dados reais em vez de stubs.

Start standalone:  python live/dashboard_server_hantec.py
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from live.base_dashboard_server import (
    BaseDashboardHandler,
    make_server,
    start_in_thread,
    read_trades_csv,
    build_trade_stats,
)
from urllib.parse import urlparse, parse_qs

log = logging.getLogger("dashboard_hantec")

# Signal queue (shared with run_live.py)
try:
    from live.signal_queue import peek as _sq_peek, confirm as _sq_confirm
    _SIGNAL_QUEUE_OK = True
except Exception as _sqe:
    _SIGNAL_QUEUE_OK = False
    log.warning("signal_queue not available: %s", _sqe)

BASE_DIR      = Path(r"C:\FluxQuantumAI")
LOGS_DIR      = BASE_DIR / "logs"
PORT          = 8082
BALANCE_START = 346.10


# --- Env (.env) ---------------------------------------------------------------

def _load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

HANTEC_ACCOUNT  = int(os.environ.get("HANTEC_ACCOUNT",  "50051145"))
HANTEC_PASSWORD = os.environ.get("HANTEC_PASSWORD", "")
HANTEC_SERVER   = os.environ.get("HANTEC_SERVER",   "HantecMarketsMU-MT5")
HANTEC_TERMINAL = os.environ.get(
    "HANTEC_TERMINAL",
    "C:/Program Files/Hantec Markets MT5 Terminal/terminal64.exe"
)


# --- MT5 Hantec ---------------------------------------------------------------

def _get_mt5_hantec() -> dict:
    try:
        import MetaTrader5 as mt5
        ok = mt5.initialize(
            path=HANTEC_TERMINAL,
            login=HANTEC_ACCOUNT,
            password=HANTEC_PASSWORD,
            server=HANTEC_SERVER,
            timeout=8000,
        )
        if not ok:
            return {}
        info = mt5.account_info()
        if info is None or info.login != HANTEC_ACCOUNT:
            return {}
        return {
            "balance":        round(float(info.balance),     2),
            "equity":         round(float(info.equity),      2),
            "margin":         round(float(info.margin),      2),
            "free_margin":    round(float(info.margin_free), 2),
            "unrealized_pnl": round(float(info.profit),      2),
            "mt5_live":       True,
            "account":        info.login,
            "server":         info.server,
            "currency":       info.currency,
            "leverage":       info.leverage,
        }
    except Exception as e:
        log.debug("Hantec MT5 failed: %s", e)
        return {}


# --- Handler Hantec -----------------------------------------------------------

class _HantecHandler(BaseDashboardHandler):
    TRADES_CSV    = LOGS_DIR / "trades_live.csv"
    HTML_FILE     = LOGS_DIR / "index_live_hantec.html"
    BALANCE_START = BALANCE_START
    LOGS_DIR      = LOGS_DIR

    def _get_mt5_account(self) -> dict:
        return _get_mt5_hantec()

    def _read_trades_reconciled(self) -> list[dict]:
        """Override: reconcile against Hantec MT5 session (not RoboForex)."""
        from .base_dashboard_server import read_trades_csv, reconcile_trades_mt5
        trades = read_trades_csv(self.TRADES_CSV)
        try:
            import MetaTrader5 as mt5
            ok = mt5.initialize(
                path=HANTEC_TERMINAL,
                login=HANTEC_ACCOUNT,
                password=HANTEC_PASSWORD,
                server=HANTEC_SERVER,
                timeout=5000,
            )
            if ok:
                reconcile_trades_mt5(trades, self.TRADES_CSV, mt5)
        except Exception:
            pass
        return trades

    def _handle_extra_routes(self, path: str) -> bool:
        """Rotas específicas do dashboard Hantec."""

        # Alias HTML
        if path == "/index_live_hantec.html":
            if self.HTML_FILE.exists():
                self._send_file(self.HTML_FILE)
                return True
            self._send_404()
            return True

        # Ordens confirmadas (newest-first)
        if path == "/api/orders":
            trades = self._read_trades_reconciled()
            orders = [t for t in reversed(trades) if t.get("decision") == "CONFIRMED"]
            self._send_json(orders)
            return True

        # Notícias económicas -- via NextGenDataBus (G-02: porta 8088 eliminada)
        if path == "/api/news":
            self._send_json(self._get_news())
            return True

        # Signal pending -- EA polls this every 1-2s
        if path == "/api/signal/pending":
            if not _SIGNAL_QUEUE_OK:
                self._send_json({"error": "signal queue not available"}, code=503)
                return True
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            try:
                account = int(qs.get("account", [0])[0])
            except (ValueError, TypeError):
                self._send_json({"error": "invalid account"}, code=400)
                return True
            if not account:
                self._send_json({"error": "account required"}, code=400)
                return True
            signal = _sq_peek(account)
            self._send_json({"signal": signal})
            return True

        # Signal confirm -- EA calls after executing
        if path == "/api/signal/confirm":
            return True  # handled via POST in do_POST if needed

        return False

    def _get_news(self) -> dict:
        """
        Retorna eventos económicos do NextGenDataBus.
        G-02: substituiu proxy para porta 8088 (fantasma).
        Fallback gracioso se DataBus não tiver dados de news.
        """
        try:
            from apex_nextgen.core.nextgen_databus import databus
            # NewsGate armazena eventos no scorecard -- extrair se disponível
            sc   = databus.get_scorecard_today()
            news = sc.get("news_events", [])
            if news:
                return {"events": news, "source": "nextgen_databus"}
        except Exception:
            pass
        return {"events": [], "source": "offline"}

    def _handle_v3_routes(self, path: str) -> bool:
        """
        Override: adiciona campos específicos Hantec ao /api/v3/status.
        G-02: phase_name agora vem do NextGenDataBus, não da porta 8088.
        """
        if not path.startswith("/api/v3/"):
            return False

        sub = path[len("/api/v3/"):]

        # Para o status, enriquecer com dados Hantec-específicos
        if sub == "status":
            # Chamar implementação da base
            super()._handle_v3_routes(path)
            return True

        # Para todos os outros, usar a base
        return super()._handle_v3_routes(path)


def start(port: int = PORT) -> threading.Thread:
    return start_in_thread(_HantecHandler, port, "Dashboard-Hantec")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"ATS Dashboard (Hantec Live) -- http://localhost:{PORT}")
    print(f"  NextGen War Room:  http://localhost:{PORT}/api/nextgen/war-room")
    print(f"  NextGen Status:    http://localhost:{PORT}/api/nextgen/status")
    make_server(_HantecHandler, PORT).serve_forever()

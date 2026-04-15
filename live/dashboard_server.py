"""
dashboard_server.py -- Dashboard Demo (RoboForex 0.05ct).

Porta 8081 · Trades: logs/trades.csv · Balance inicial: $500.00

Herda BaseDashboardHandler -- toda a lógica comum em base_dashboard_server.py.
NextGen endpoints (/api/nextgen/*, /api/v3/*) servidos pela base automaticamente.

Start standalone:  python live/dashboard_server.py
Or import:         from live.dashboard_server import start as start_dashboard
"""

from __future__ import annotations

import logging
from pathlib import Path

from live.base_dashboard_server import (
    BaseDashboardHandler,
    make_server,
    start_in_thread,
)

log = logging.getLogger("dashboard_server")

BASE_DIR      = Path(r"C:\FluxQuantumAI")
LOGS_DIR      = BASE_DIR / "logs"
PORT          = 8081
BALANCE_START = 500.0


def _get_mt5() -> dict:
    """Conecta ao terminal MT5 Demo (sem parâmetros -- usa terminal activo)."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return {}
        info = mt5.account_info()
        if info is None:
            return {}
        return {
            "balance":        round(float(info.balance),      2),
            "equity":         round(float(info.equity),       2),
            "margin":         round(float(info.margin),       2),
            "free_margin":    round(float(info.margin_free),  2),
            "unrealized_pnl": round(float(info.profit),       2),
            "mt5_live":       True,
        }
    except Exception as e:
        log.debug("MT5 Demo account_info failed: %s", e)
        return {}


class _Handler(BaseDashboardHandler):
    TRADES_CSV    = LOGS_DIR / "trades.csv"
    HTML_FILE     = LOGS_DIR / "index_live.html"
    BALANCE_START = BALANCE_START
    LOGS_DIR      = LOGS_DIR

    def _get_mt5_account(self) -> dict:
        return _get_mt5()

    def _handle_extra_routes(self, path: str) -> bool:
        # O dashboard demo não tem rotas adicionais para além das da base
        if path in ("/index_live.html",):
            if self.HTML_FILE.exists():
                self._send_file(self.HTML_FILE)
                return True
        return False


def start(port: int = PORT) -> "threading.Thread":
    return start_in_thread(_Handler, port, "Dashboard-Demo")


if __name__ == "__main__":
    import threading
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"ATS Dashboard (Demo) -- http://localhost:{PORT}")
    print(f"  NextGen War Room:  http://localhost:{PORT}/api/nextgen/war-room")
    print(f"  NextGen Status:    http://localhost:{PORT}/api/nextgen/status")
    make_server(_Handler, PORT).serve_forever()

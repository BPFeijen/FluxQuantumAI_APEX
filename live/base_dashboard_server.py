"""
base_dashboard_server.py -- Base partilhada para os dashboards ATS.

Elimina a duplicação de código entre dashboard_server.py e
dashboard_server_hantec.py (Gap G-01).

Fornece:
  - Toda a lógica HTTP comum (routing, MIME, send helpers)
  - Trade analytics (_build_status, _build_equity_curve, _build_weekly)
  - Rotas NextGen (/api/nextgen/*) alimentadas pelo NextGenDataBus
  - Hook _handle_extra_routes() para rotas específicas de cada conta

Subclasses apenas precisam de definir as constantes de classe e
sobrescrever _get_mt5_account() e _handle_extra_routes().
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# --- NextGenDataBus -----------------------------------------------------------
# Importado aqui para que ambos os dashboards partilhem o mesmo singleton
_ROOT = Path(__file__).parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from apex_nextgen.core.nextgen_databus import databus as _databus
    _DATABUS_OK = True
except Exception as _e:
    _databus   = None
    _DATABUS_OK = False
    logging.getLogger("base_dashboard").warning(
        "NextGenDataBus não disponível -- endpoints /api/nextgen/* retornam vazios. Erro: %s", _e
    )

log = logging.getLogger("base_dashboard")

# --- MIME types ---------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
    ".json": "application/json",
    ".ico":  "image/x-icon",
}


# --- Trade analytics (partilhadas) --------------------------------------------

def build_trade_stats(trades: list[dict], balance_start: float, mt5: dict) -> dict:
    """Calcula estatísticas completas a partir do trades CSV."""
    closed  = [t for t in trades if t.get("result", "open") not in ("open", "")]
    wins    = [t for t in closed if _safe_float(t.get("pnl")) > 0]
    losses  = [t for t in closed if _safe_float(t.get("pnl")) <= 0]
    total_pnl   = sum(_safe_float(t.get("pnl")) for t in closed)
    gross_win   = sum(_safe_float(t.get("pnl")) for t in wins)
    gross_loss  = abs(sum(_safe_float(t.get("pnl")) for t in losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0
    win_rate    = round(len(wins) / len(closed) * 100, 1) if closed else 0.0

    balance   = mt5.get("balance", balance_start + total_pnl)
    equity    = mt5.get("equity",  balance)
    return_pct = round((equity - balance_start) / balance_start * 100, 2)

    confirmed = len([t for t in trades if t.get("decision") == "CONFIRMED"])
    filtered  = len([t for t in trades if t.get("decision") == "FILTERED"])

    # Max drawdown
    max_drawdown = 0.0
    peak = running = balance_start
    for t in sorted(closed, key=lambda x: x.get("timestamp", "")):
        running = round(running + _safe_float(t.get("pnl")), 2)
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, round(peak - running, 2))

    trading_started_at = None
    conf_ts = [t.get("timestamp", "") for t in trades
               if t.get("decision") == "CONFIRMED" and t.get("timestamp")]
    if conf_ts:
        trading_started_at = min(conf_ts)

    return {
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "balance":            balance,
        "equity":             equity,
        "balance_start":      balance_start,
        "unrealized_pnl":     mt5.get("unrealized_pnl", 0.0),
        "free_margin":        mt5.get("free_margin", 0.0),
        "total_pnl":          round(total_pnl, 2),
        "total_return_pct":   return_pct,
        "trades_confirmed":   confirmed,
        "trades_filtered":    filtered,
        "win_rate":           win_rate,
        "wins":               len(wins),
        "losses":             len(losses),
        "profit_factor":      profit_factor,
        "sharpe":             0.0,
        "max_drawdown":       max_drawdown,
        "trading_started_at": trading_started_at,
        "mt5_live":           mt5.get("mt5_live", False),
        "account":            mt5.get("account"),
        "server":             mt5.get("server"),
        "currency":           mt5.get("currency", "USD"),
        "leverage":           mt5.get("leverage"),
        "open_positions":     [],
    }


def build_equity_curve(trades: list[dict], balance_start: float) -> list[dict]:
    balance = balance_start
    points  = [{"t": None, "balance": balance}]
    for t in sorted(trades, key=lambda x: x.get("timestamp", "")):
        if t.get("result", "open") in ("open", ""):
            continue
        balance = round(balance + _safe_float(t.get("pnl")), 2)
        points.append({"t": t.get("timestamp"), "balance": balance})
    return points


def build_weekly(trades: list[dict]) -> list[dict]:
    weekly: dict[str, float] = {}
    for t in trades:
        if t.get("result", "open") in ("open", ""):
            continue
        try:
            dt   = datetime.fromisoformat(t["timestamp"].replace("Z", "+00:00"))
            week = dt.strftime("%Y-W%W")
            weekly[week] = round(weekly.get(week, 0.0) + _safe_float(t.get("pnl")), 2)
        except Exception:
            continue
    return [{"week": k, "pnl": v} for k, v in sorted(weekly.items())]


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def read_trades_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        log.warning("trades CSV read error [%s]: %s", path.name, e)
        return []


def read_canonical_executions(path: Path, limit: int = 200) -> list[dict]:
    """
    Read canonical execution snapshots from decision_log.jsonl.
    Returns rows compatible across brokers/accounts for dashboard rendering.
    """
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            dec = rec.get("decision", {})
            ex = dec.get("execution", {})
            for b in ex.get("brokers", []) or []:
                rows.append({
                    "decision_id": rec.get("decision_id"),
                    "broker": b.get("broker"),
                    "account": b.get("account"),
                    "direction": dec.get("direction"),
                    "action_side": dec.get("action_side"),
                    "execution_state": b.get("result_state", ex.get("overall_state", "NOT_ATTEMPTED")),
                    "ticket": b.get("ticket"),
                    "error_text": b.get("error_text", ""),
                    "created_at": rec.get("created_at") or rec.get("timestamp"),
                    "updated_at": ex.get("updated_at") or rec.get("created_at") or rec.get("timestamp"),
                })
    except Exception as e:
        log.warning("canonical execution read error [%s]: %s", path.name, e)
    return rows


def read_position_monitor_events(path: Path, limit: int = 200) -> list[dict]:
    """Read canonical POSITION_MONITOR events from decision_log.jsonl."""
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("event_source") != "POSITION_MONITOR":
                continue
            pe = rec.get("position_event", {})
            events.append({
                "decision_id": rec.get("decision_id"),
                "timestamp": rec.get("timestamp"),
                "event_type": pe.get("event_type"),
                "action_type": pe.get("action_type"),
                "direction_affected": pe.get("direction_affected"),
                "dry_run": pe.get("dry_run"),
                "t3_mode": pe.get("t3_mode"),
                "reason": pe.get("reason"),
                "ticket": pe.get("ticket"),
                "broker": pe.get("broker"),
                "account": pe.get("account"),
                "execution_state": pe.get("execution_state"),
                "execution_error": pe.get("execution_error"),
                "result": pe.get("result"),
            })
    except Exception as e:
        log.warning("position monitor event read error [%s]: %s", path.name, e)
    return events


def reconcile_trades_mt5(trades: list[dict], csv_path: Path, mt5_mod) -> None:
    """
    For trades with result='open', check MT5 deal history.
    If the leg1 ticket is no longer an open position, look up the deal
    to determine result (tp1_hit / sl_hit / closed) and PnL.
    Updates the list in-place and rewrites the CSV if anything changed.

    mt5_mod: an already-initialized MetaTrader5 module (caller handles login).
    """
    open_trades = [t for t in trades if t.get("result", "open") in ("open", "")]
    if not open_trades:
        return

    positions = mt5_mod.positions_get()
    open_tickets = {p.ticket for p in positions} if positions else set()

    from datetime import timedelta
    _date_from = datetime.now(timezone.utc) - timedelta(days=30)
    _date_to   = datetime.now(timezone.utc) + timedelta(days=1)

    changed = False
    for t in open_trades:
        leg1 = int(t.get("leg1_ticket") or 0)
        if leg1 <= 0:
            continue
        if leg1 in open_tickets:
            continue

        deals = mt5_mod.history_deals_get(position=leg1, date_from=_date_from, date_to=_date_to)
        if not deals or len(deals) < 2:
            t["result"] = "closed"
            t["pnl"] = 0.0
            changed = True
            continue

        close_deal = deals[-1]
        pnl = round(close_deal.profit + close_deal.commission + close_deal.swap, 2)
        t["pnl"] = pnl

        close_price = close_deal.price
        direction = t.get("direction", "")
        sl  = _safe_float(t.get("sl"))
        tp1 = _safe_float(t.get("tp1"))

        if direction == "LONG":
            if tp1 > 0 and close_price >= tp1 - 0.5:
                t["result"] = "tp1_hit"
            elif sl > 0 and close_price <= sl + 0.5:
                t["result"] = "sl_hit"
            else:
                t["result"] = "closed"
        elif direction == "SHORT":
            if tp1 > 0 and close_price <= tp1 + 0.5:
                t["result"] = "tp1_hit"
            elif sl > 0 and close_price >= sl - 0.5:
                t["result"] = "sl_hit"
            else:
                t["result"] = "closed"
        else:
            t["result"] = "closed"
        changed = True

    if changed:
        try:
            fieldnames = list(trades[0].keys()) if trades else []
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(trades)
            log.info("reconcile_trades_mt5: updated %s (%d trades reconciled)",
                     csv_path.name, sum(1 for t in trades if t.get("result") not in ("open", ""))
                     )
        except Exception as e:
            log.warning("trades CSV rewrite failed [%s]: %s", csv_path.name, e)


# --- HTTP Handler base --------------------------------------------------------

class BaseDashboardHandler(BaseHTTPRequestHandler):
    """
    Handler HTTP partilhado por ambos os dashboards.

    Subclasses devem definir atributos de classe:
        TRADES_CSV    : Path
        HTML_FILE     : Path
        BALANCE_START : float
        LOGS_DIR      : Path

    E podem sobrescrever:
        _get_mt5_account() -> dict
        _handle_extra_routes(path) -> bool  (True se a rota foi tratada)
    """

    # Configuração -- sobrescrever na subclasse
    TRADES_CSV:    Path  = Path("logs/trades.csv")
    HTML_FILE:     Path  = Path("logs/index_live.html")
    BALANCE_START: float = 500.0
    LOGS_DIR:      Path  = Path("logs")

    def log_message(self, fmt, *args):
        pass   # silenciar request logs por linha

    def _read_trades_reconciled(self) -> list[dict]:
        """Read trades CSV and reconcile open trades against MT5 deal history."""
        trades = read_trades_csv(self.TRADES_CSV)
        try:
            import MetaTrader5 as mt5
            if mt5.initialize():
                reconcile_trades_mt5(trades, self.TRADES_CSV, mt5)
        except Exception:
            pass
        return trades

    # -- Helpers de resposta --------------------------------------------------

    def _send_json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        mime = MIME.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _send_404(self):
        self.send_response(404)
        self.end_headers()

    # -- MT5 (override na subclasse) ------------------------------------------

    def _get_mt5_account(self) -> dict:
        """Override na subclasse para conectar à conta correcta."""
        return {}

    # -- Rotas NextGen (/api/nextgen/*) ---------------------------------------

    def _handle_nextgen_routes(self, path: str) -> bool:
        """
        Serve todos os endpoints /api/nextgen/* via NextGenDataBus.
        Retorna True se a rota foi tratada.
        """
        if not path.startswith("/api/nextgen/"):
            return False

        sub = path[len("/api/nextgen/"):]

        if not _DATABUS_OK:
            self._send_json({"error": "NextGenDataBus não disponível"}, 503)
            return True

        if sub == "status":
            self._send_json(_databus.get_nextgen_status())

        elif sub == "war-room":
            try:
                n = int(self.path.split("n=")[-1]) if "n=" in self.path else 40
            except (ValueError, IndexError):
                n = 40
            self._send_json(_databus.get_war_room(n=n))

        elif sub == "scorecard":
            self._send_json(_databus.get_scorecard_today())

        elif sub == "sizing":
            self._send_json(_databus.get_sizing_analysis())

        elif sub == "threshold-health":
            self._send_json(_databus.get_threshold_health())

        elif sub == "verdicts":
            try:
                n = int(self.path.split("n=")[-1]) if "n=" in self.path else 50
            except (ValueError, IndexError):
                n = 50
            self._send_json(_databus.get_latest_verdicts(n=n))

        else:
            self._send_json({"error": f"Unknown NextGen endpoint: {sub}"}, 404)

        return True

    # -- Rotas /api/v3/* (mapeadas para dados reais do NextGen) ---------------

    def _handle_v3_routes(self, path: str) -> bool:
        """
        Substitui os stubs /api/v3/* pelos dados reais do NextGen (G-03).
        Mantém compatibilidade de schema com o frontend existente.
        Retorna True se a rota foi tratada.
        """
        if not path.startswith("/api/v3/"):
            return False

        sub = path[len("/api/v3/"):]

        if not _DATABUS_OK:
            self._send_json(self._v3_empty(sub))
            return True

        status = _databus.get_nextgen_status()
        sc     = _databus.get_scorecard_today()
        sizing = _databus.get_sizing_analysis()

        if sub == "status":
            gates = status.get("gates", {})
            live_gates = sum(1 for g in gates.values() if not g.get("is_stub", True))
            self._send_json({
                "mode":            "shadow",
                "model_exists":    not status.get("anomaly", {}).get("is_stub", True),
                "decisions_today": sc.get("signals", 0),
                "live_active":     live_gates > 0,
                "shadow_active":   True,
                "gates_live":      live_gates,
                "gates_total":     len(gates),
                "last_ts":         status.get("last_ts"),
                "last_dir":        status.get("last_dir", "FLAT"),
                "last_contracts":  status.get("last_contracts", 0.0),
                "final_mult":      status.get("final_mult", 1.0),
                "anomaly":         status.get("anomaly", {}),
            })

        elif sub == "scorecard":
            th = _databus.get_threshold_health()
            sp = th.get("shadow_pnl", {})
            self._send_json({
                "agreement_rate_pct":  0,
                "n_disagreements":     0,
                "outcome_v3_wins":     sc.get("signals", 0),
                "outcome_v2_wins":     0,
                "signal_rate_pct":     sc.get("signal_rate_pct", 0.0),
                "hard_vetos":          sc.get("hard_vetos", 0),
                "avg_contracts":       sc.get("avg_contracts", 0.0),
                "avg_latency_ms":      sc.get("avg_latency_ms", 0.0),
                "shadow_pnl": {
                    "signals_t030": sp.get("signals_at_t030", 0),
                    "signals_t040": sp.get("signals_at_t040", 0),
                    "signals_t045": sp.get("signals_at_t045", 0),
                    "noise_only":   sp.get("only_t030_noise", 0),
                },
                "sizing": {
                    "by_regime":     sizing.get("avg_contracts_by_regime", {}),
                    "ratio_whale_noise": sizing.get("sizing_ratio_whale_vs_noise"),
                    "avg_anomaly_mult":  sizing.get("avg_anomaly_mult", 1.0),
                    "no_trade_count":    sizing.get("no_trade_zero_contracts", 0),
                },
            })

        elif sub == "action_dist":
            gates = status.get("gates", {})
            total = sc.get("total_ticks", 0)
            signals = sc.get("signals", 0)
            vetos   = sc.get("hard_vetos", 0)
            flat    = max(0, total - signals - vetos)
            pct = {}
            if total > 0:
                pct = {
                    "SIGNAL": round(signals / total * 100, 1),
                    "FLAT":   round(flat    / total * 100, 1),
                    "VETO":   round(vetos   / total * 100, 1),
                }
            self._send_json({"total": total, "pct": pct})

        elif sub == "feature_importance":
            # Usar sizing_breakdown como proxy de importância de features
            sizing_bd = status.get("sizing", {})
            features = []
            labels = [
                ("anomaly_mult", "Anomaly (G1)"),
                ("iceberg_mult", "Iceberg (G4)"),
                ("regime_mult",  "Regime (G5)"),
                ("news_mult",    "News (G2)"),
            ]
            for key, label in labels:
                val = sizing_bd.get(key, 1.0)
                if val is not None and val != 1.0:
                    features.append({"feature": label, "importance": round(abs(1.0 - val), 3)})
            features.sort(key=lambda x: x["importance"], reverse=True)
            self._send_json(features)

        elif sub == "decisions":
            # Últimas 50 mensagens do war room como "decision stream"
            self._send_json(_databus.get_war_room(n=50))

        else:
            self._send_json(self._v3_empty(sub))

        return True

    def _v3_empty(self, sub: str) -> dict:
        """Resposta vazia compatível com o frontend para sub-rotas desconhecidas."""
        defaults = {
            "status":           {"mode": "shadow", "model_exists": False, "decisions_today": 0},
            "scorecard":        {"agreement_rate_pct": 0, "n_disagreements": 0},
            "action_dist":      {"total": 0, "pct": {}},
            "feature_importance": [],
            "decisions":        [],
        }
        return defaults.get(sub, {})

    # -- Router principal -----------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path

        # 1. NextGen namespace (/api/nextgen/*)
        if self._handle_nextgen_routes(path):
            return

        # 2. V3 namespace (/api/v3/*) -- dados reais do NextGen
        if self._handle_v3_routes(path):
            return

        # 3. Single Source of Truth endpoints (read JSON files, no recalculation)
        if path == "/api/live":
            _live_path = Path(r"C:\FluxQuantumAI\logs\decision_live.json")
            if _live_path.exists():
                try:
                    self._send_json(json.loads(_live_path.read_text(encoding="utf-8")))
                except Exception as _e:
                    self._send_json({"error": "parse_error", "detail": str(_e)}, 500)
            else:
                self._send_json({"error": "not_found", "detail": "decision_live.json does not exist yet (no gate check executed)"}, 404)
            return

        if path == "/api/system_health":
            _health_path = Path(r"C:\FluxQuantumAI\logs\service_state.json")
            if _health_path.exists():
                try:
                    self._send_json(json.loads(_health_path.read_text(encoding="utf-8")))
                except Exception as _e:
                    self._send_json({"error": "parse_error", "detail": str(_e)}, 500)
            else:
                self._send_json({"error": "not_found", "detail": "service_state.json does not exist"}, 404)
            return

        if path == "/api/executions":
            _decision_log = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
            self._send_json(read_canonical_executions(_decision_log, limit=300))
            return

        if path == "/api/pm_events":
            _decision_log = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
            self._send_json(read_position_monitor_events(_decision_log, limit=300))
            return

        # 4. Production AnomalyForge V2 status (Sprint 3.2)
        if path == "/api/production/anomaly_forge":
            _af_path = Path(r"C:\FluxQuantumAI\logs\production_anomaly_forge.json")
            if _af_path.exists():
                try:
                    import json as _j
                    self._send_json(_j.loads(_af_path.read_text(encoding="utf-8")))
                except Exception:
                    self._send_json({"error": "parse error", "is_stub": True})
            else:
                self._send_json({
                    "ts": None, "score": 0.0, "level": "STUB", "veto": False,
                    "size_mult": 1.0, "mse": 0.0, "buffer_fill": 0, "is_stub": True,
                })
            return

        # 4. Rotas comuns de trading (reconcile open trades on every read)
        if path == "/api/status":
            trades = self._read_trades_reconciled()
            mt5    = self._get_mt5_account()
            self._send_json(build_trade_stats(trades, self.BALANCE_START, mt5))

        elif path == "/api/equity":
            self._send_json(build_equity_curve(self._read_trades_reconciled(), self.BALANCE_START))

        elif path == "/api/weekly":
            self._send_json(build_weekly(self._read_trades_reconciled()))

        elif path == "/api/trades":
            self._send_json(self._read_trades_reconciled())

        # 4. Rotas específicas da subclasse
        elif self._handle_extra_routes(path):
            return

        # 5. HTML principal
        elif path in ("/", "/index.html"):
            if self.HTML_FILE.exists():
                self._send_file(self.HTML_FILE)
            else:
                self._send_404()

        # 6. Ficheiros estáticos de logs/
        else:
            candidate = self.LOGS_DIR / path.lstrip("/")
            if candidate.exists() and candidate.is_file():
                self._send_file(candidate)
            else:
                self._send_404()

    def _handle_extra_routes(self, path: str) -> bool:
        """
        Override na subclasse para adicionar rotas específicas de conta.
        Retorna True se a rota foi tratada, False para continuar para ficheiros estáticos.
        """
        return False


# --- Factory de servidor ------------------------------------------------------

def make_server(handler_cls, port: int) -> ThreadingHTTPServer:
    """Cria um ThreadingHTTPServer (processa pedidos em parallel -- evita bloqueio single-thread)."""
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    srv.allow_reuse_address = True
    return srv


def start_in_thread(handler_cls, port: int, name: str) -> threading.Thread:
    """Arranca o servidor em daemon thread. Retorna a thread."""
    server = make_server(handler_cls, port)
    t = threading.Thread(target=server.serve_forever, name=name, daemon=True)
    t.start()
    log.info("%s started on http://localhost:%d", name, port)
    print(f"  {name}: http://localhost:{port}")
    return t

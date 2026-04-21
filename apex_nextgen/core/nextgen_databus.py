"""
nextgen_databus.py — Leitor otimizado dos logs NextGen para o dashboard.

Lê os ficheiros JSONL em modo tail (sem reprocessar linhas antigas).
Mantém os últimos N registos em memória com deque de tamanho fixo.
Actualiza em background a cada POLL_INTERVAL segundos.

Uso:
    from apex_nextgen.core.nextgen_databus import databus

    status  = databus.get_nextgen_status()
    war_room = databus.get_war_room(n=30)

Singleton: `databus` é a instância global partilhada por ambos os dashboards.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

_logger = logging.getLogger("nextgen.databus")

# ─── Configuração ─────────────────────────────────────────────────────────────

_NEXTGEN_LOG_DIR  = Path(r"C:\FluxQuantumAI\apex_nextgen\logs")
_PERF_LOG         = _NEXTGEN_LOG_DIR / "nextgen_performance.jsonl"
_SCORECARD_LOG    = _NEXTGEN_LOG_DIR / "daily_scorecard.jsonl"

_PERF_CACHE_SIZE  = 200   # últimos N ticks avaliados em memória
_SC_CACHE_SIZE    = 30    # últimas N sessões
_POLL_INTERVAL    = 1.5   # segundos entre polls de ficheiro
_WAR_ROOM_DEFAULT = 40    # mensagens por default no war room


# ─── NextGenDataBus ────────────────────────────────────────────────────────────

class NextGenDataBus:
    """
    Leitor contínuo dos JSONL do NextGen.

    Usa file-position tracking para ler apenas linhas novas (tail -f logic).
    Thread-safe: um lock protege todas as leituras/escritas ao cache.
    O thread de fundo é daemon — termina com o processo principal.
    """

    def __init__(self):
        self._lock   = threading.RLock()
        self._perf   : deque[dict] = deque(maxlen=_PERF_CACHE_SIZE)
        self._sc     : deque[dict] = deque(maxlen=_SC_CACHE_SIZE)
        self._pos    : Dict[str, int] = {}   # path → último byte lido
        self._ready  = False

        # Carga inicial (síncrona — garante dados ao primeiro request)
        self._load_file(_PERF_LOG,     self._perf,  initial=True)
        self._load_file(_SCORECARD_LOG, self._sc,   initial=True)
        self._ready = True

        # Thread de fundo para actualizações incrementais
        t = threading.Thread(target=self._poll_loop, name="nextgen-databus", daemon=True)
        t.start()
        _logger.info(
            "NextGenDataBus iniciado | perf=%d entries | sc=%d entries",
            len(self._perf), len(self._sc),
        )

    # ─── Leitura de ficheiros ────────────────────────────────────────────────

    def _load_file(self, path: Path, cache: deque, initial: bool = False):
        """
        Lê linhas novas de um JSONL desde a última posição conhecida.
        Na carga inicial, lê só as últimas _PERF_CACHE_SIZE linhas (evita I/O pesado).
        """
        if not path.exists():
            return

        key = str(path)
        try:
            file_size = path.stat().st_size
        except OSError:
            return

        if initial:
            # Carga inicial: tail das últimas maxlen linhas sem ler o ficheiro todo
            try:
                with open(path, "rb") as f:
                    # Estimar posição: assumir ~300 bytes/linha, ler 3× o necessário
                    seek_bytes = cache.maxlen * 350 * 3
                    f.seek(max(0, file_size - seek_bytes))
                    if max(0, file_size - seek_bytes) > 0:
                        f.readline()  # descartar linha parcial
                    lines = f.readlines()
                    pos = f.tell()

                # Tomar só as últimas maxlen
                for raw in lines[-cache.maxlen:]:
                    self._parse_append(raw, cache)

                self._pos[key] = file_size  # próxima leitura começa no fim
                return
            except Exception as e:
                _logger.debug("DataBus initial load error %s: %s", path.name, e)
                self._pos[key] = 0

        # Leitura incremental: apenas bytes novos
        last_pos = self._pos.get(key, 0)
        if file_size <= last_pos:
            return   # ficheiro não cresceu

        try:
            with open(path, "rb") as f:
                f.seek(last_pos)
                new_data = f.read(file_size - last_pos)
                new_pos  = f.tell()

            self._pos[key] = new_pos
            for raw in new_data.splitlines(keepends=True):
                if raw.strip():
                    self._parse_append(raw, cache)
        except Exception as e:
            _logger.debug("DataBus incremental read error %s: %s", path.name, e)

    def _parse_append(self, raw: bytes, cache: deque):
        try:
            record = json.loads(raw.decode("utf-8", errors="replace"))
            with self._lock:
                cache.append(record)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    def _poll_loop(self):
        import time
        while True:
            time.sleep(_POLL_INTERVAL)
            try:
                self._load_file(_PERF_LOG,      self._perf)
                self._load_file(_SCORECARD_LOG, self._sc)
            except Exception as e:
                _logger.debug("DataBus poll error: %s", e)

    # ─── API pública ──────────────────────────────────────────────────────────

    def get_latest_verdicts(self, n: int = 50) -> List[dict]:
        """Últimos N ticks avaliados pelo FoxyzeMaster."""
        with self._lock:
            return list(self._perf)[-n:]

    def get_scorecard_today(self) -> dict:
        """Scorecard da sessão de hoje (ou último disponível)."""
        today = date.today().isoformat()
        with self._lock:
            for entry in reversed(list(self._sc)):
                if entry.get("date") == today:
                    return entry
            # Fallback: último disponível
            return list(self._sc)[-1] if self._sc else {}

    def get_nextgen_status(self) -> dict:
        """
        Snapshot compacto do estado actual do NextGen.
        Inclui: estado dos gates, último sinal, métricas de sessão.
        """
        with self._lock:
            verdicts = list(self._perf)
            sc       = list(self._sc)

        if not verdicts:
            return {"status": "no_data", "gates": {}, "last_signal": None}

        last = verdicts[-1]

        # Estado de cada gate (do último tick)
        gates = {}
        for gv in last.get("gate_verdicts", []):
            gate_num = gv.get("gate")
            gates[f"g{gate_num}"] = {
                "name":       gv.get("provider", f"Gate{gate_num}"),
                "veto":       gv.get("veto", "CLEAR"),
                "size_mult":  gv.get("size_mult", 1.0),
                "is_stub":    gv.get("is_stub", True),
                "confidence": gv.get("confidence", 0.0),
                "latency_ms": gv.get("latency_ms", 0.0),
            }

        # Anomaly details do Gate 1
        anomaly = {}
        g1_meta = gates.get("g1", {})
        last_g1 = next(
            (gv for gv in last.get("gate_verdicts", []) if gv.get("gate") == 1),
            {}
        )
        if last_g1:
            m = last_g1.get("metadata", {})
            anomaly = {
                "anomaly_score":    m.get("anomaly_score"),
                "anomaly_level":    m.get("anomaly_level", "STUB"),
                "buffer_fill":      m.get("buffer_fill", 0),
                "buffer_need":      60,
                "avg_latency_ms":   m.get("avg_latency_ms"),
                "is_stub":          last_g1.get("is_stub", True),
            }

        # Métricas de sessão do scorecard mais recente
        today_sc = sc[-1] if sc else {}
        session = {
            "date":           today_sc.get("date"),
            "total_ticks":    today_sc.get("total_ticks", 0),
            "signals":        today_sc.get("signals", 0),
            "signal_rate_pct":today_sc.get("signal_rate_pct", 0.0),
            "hard_vetos":     today_sc.get("hard_vetos", 0),
            "avg_contracts":  today_sc.get("avg_contracts", 0.0),
            "avg_latency_ms": today_sc.get("avg_latency_ms", 0.0),
        }

        # Sizing breakdown do último tick
        sizing = last.get("metadata", {}).get("sizing_breakdown", {})

        return {
            "status":       "ok",
            "last_ts":      last.get("timestamp"),
            "last_dir":     last.get("direction", "FLAT"),
            "last_contracts": last.get("contracts", 0.0),
            "final_mult":   last.get("final_multiplier", 1.0),
            "block_reason": last.get("block_reason"),
            "gates":        gates,
            "anomaly":      anomaly,
            "sizing":       sizing,
            "session":      session,
        }

    def get_sizing_analysis(self) -> dict:
        """Sizing analysis do scorecard de hoje (Sprint 3)."""
        sc = self.get_scorecard_today()
        th = sc.get("threshold_health", {})
        return th.get("sizing_analysis", {
            "avg_contracts_by_regime": {},
            "avg_anomaly_mult": 1.0,
            "sizing_ratio_whale_vs_noise": None,
            "no_trade_zero_contracts": 0,
            "note": "Sem dados de sizing ainda.",
        })

    def get_threshold_health(self) -> dict:
        """Threshold health do scorecard de hoje (Sprint 2.2)."""
        sc = self.get_scorecard_today()
        return sc.get("threshold_health", {})

    # ─── War Room ─────────────────────────────────────────────────────────────

    def get_war_room(self, n: int = _WAR_ROOM_DEFAULT) -> List[dict]:
        """
        Converte os últimos N ticks avaliados num feed de mensagens estilo
        "diálogo entre os robôs" para exibição no dashboard.

        Cada mensagem tem:
            ts       — timestamp HH:MM:SS
            speaker  — nome do gate / "Master"
            gate     — 0–5 (0 = Master)
            msg      — texto legível da decisão
            tag      — "ok" | "warn" | "veto" | "signal" | "boost" | "stub" | "decision" | "block"
        """
        with self._lock:
            verdicts = list(self._perf)[-n:]

        messages = []
        for verdict in verdicts:
            try:
                msgs = self._verdict_to_messages(verdict)
                messages.extend(msgs)
            except Exception:
                pass

        # Mais recente por último → mais recente primeiro para o frontend
        return list(reversed(messages))

    def _verdict_to_messages(self, v: dict) -> List[dict]:
        """Converte um único MasterVerdict numa lista de mensagens de diálogo."""
        ts_raw = v.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw).strftime("%H:%M:%S")
        except (ValueError, TypeError):
            ts = ts_raw[-8:] if len(ts_raw) >= 8 else ts_raw

        msgs = []
        direction   = v.get("direction", "FLAT")
        contracts   = v.get("contracts", 0.0)
        final_mult  = v.get("final_multiplier", 1.0)
        blocked_at  = v.get("blocked_at_gate")
        block_reason= v.get("block_reason", "")
        latency     = v.get("latency_ms", 0.0)

        for gv in v.get("gate_verdicts", []):
            gate    = gv.get("gate", 0)
            name    = gv.get("provider", f"Gate{gate}")
            veto    = gv.get("veto", "CLEAR")
            mult    = gv.get("size_mult", 1.0)
            conf    = gv.get("confidence", 0.0)
            stub    = gv.get("is_stub", True)
            meta    = gv.get("metadata", {})
            gdir    = gv.get("direction", "NEUTRAL")

            if stub:
                msgs.append({"ts": ts, "speaker": name, "gate": gate,
                             "msg": f"STUB — aguarda modelo", "tag": "stub"})
                continue

            # Gate 2 — NewsGate
            if gate == 2:
                if veto == "HARD_VETO":
                    msgs.append({"ts": ts, "speaker": "NewsGate", "gate": 2,
                                 "msg": "NEWS VETO — HARD BLOCK", "tag": "veto"})
                elif veto == "FORCE_EXIT":
                    msgs.append({"ts": ts, "speaker": "NewsGate", "gate": 2,
                                 "msg": "NEWS FORCE_EXIT", "tag": "veto"})
                elif veto == "SOFT_VETO":
                    msgs.append({"ts": ts, "speaker": "NewsGate", "gate": 2,
                                 "msg": f"News activa · x{mult:.2f}", "tag": "warn"})
                # CLEAR: silêncio (sem ruído visual)

            # Gate 3 — FluxSignalEngine
            elif gate == 3:
                imb    = meta.get("book_imbalance", 0.0)
                delta  = meta.get("cum_delta", meta.get("cumulative_delta", 0.0))
                strats = meta.get("strategies", [])
                regime = meta.get("regime_label", "")
                shadow = meta.get("shadow_thresholds", {})

                if veto == "HARD_VETO":
                    reason = meta.get("veto_reason", "MOMENTUM_BLOCK")
                    msgs.append({"ts": ts, "speaker": "FluxSignal", "gate": 3,
                                 "msg": f"BLOCK — {reason} · delta={delta:.0f}", "tag": "block"})
                elif gdir in ("LONG", "SHORT"):
                    strat_str = "+".join(strats) if strats else "ALPHA"
                    imb_str   = f"imb={imb:+.3f}"
                    reg_str   = f" [{regime}]" if regime else ""
                    whale     = abs(imb) >= 0.45
                    tag       = "signal"
                    msg       = f"{gdir} · {imb_str} · delta={delta:.0f} · {strat_str}{reg_str}"
                    if whale:
                        msg += " [WHALE]"
                        tag  = "boost"
                    msgs.append({"ts": ts, "speaker": "FluxSignal", "gate": 3,
                                 "msg": msg, "tag": tag})
                    # Shadow thresholds
                    if shadow:
                        t040 = shadow.get("t040", {}).get("direction", "FLAT")
                        t045 = shadow.get("t045", {}).get("direction", "FLAT")
                        if t045 != "FLAT":
                            msgs.append({"ts": ts, "speaker": "Shadow[0.45]", "gate": 3,
                                         "msg": f"CONFIRMADO · imb >= 0.45 (baleia)", "tag": "boost"})
                        elif t040 == "FLAT":
                            msgs.append({"ts": ts, "speaker": "Shadow[0.40]", "gate": 3,
                                         "msg": f"REJEITARIA este sinal (zona de ruido)", "tag": "warn"})
                else:
                    # FLAT — apenas se não for o gate do block
                    pass  # silêncio

            # Gate 4 — OrderStorm
            elif gate == 4:
                score_i = meta.get("iceberg_score", 0.0)
                conf_l  = meta.get("confluence", "neutral")
                refill  = meta.get("refill_obs")
                if conf_l == "aligned":
                    msgs.append({"ts": ts, "speaker": "OrderStorm", "gate": 4,
                                 "msg": f"Iceberg ALIGNED · score={score_i:.3f} · x{mult:.2f}", "tag": "boost"})
                elif conf_l == "opposed":
                    msgs.append({"ts": ts, "speaker": "OrderStorm", "gate": 4,
                                 "msg": f"Iceberg OPOSTO · score={score_i:.3f} · x{mult:.2f}", "tag": "warn"})
                elif score_i >= 0.85:
                    msgs.append({"ts": ts, "speaker": "OrderStorm", "gate": 4,
                                 "msg": f"Iceberg NEUTRAL · score={score_i:.3f}", "tag": "neutral"})
                if refill and refill.get("prod_would_ignore"):
                    lots = refill.get("estimated_lots", 0)
                    msgs.append({"ts": ts, "speaker": "OrderStorm[REFILL]", "gate": 4,
                                 "msg": f"Refill ~{lots:.0f}lotes detectado — producao ignorou", "tag": "warn"})

            # Gate 5 — RegimeForecast
            elif gate == 5:
                session = meta.get("session", "?")
                reason  = meta.get("reason", "")
                if veto == "FORCE_EXIT":
                    msgs.append({"ts": ts, "speaker": "RegimeForecast", "gate": 5,
                                 "msg": f"FORCE_EXIT — {reason}", "tag": "veto"})
                elif veto == "SOFT_VETO":
                    msgs.append({"ts": ts, "speaker": "RegimeForecast", "gate": 5,
                                 "msg": f"{session} · {reason} · x{mult:.2f}", "tag": "warn"})
                # CLEAR com mult=1.0 → silêncio (evitar ruído visual)

        # Mensagem final do Master Engine
        if blocked_at is not None:
            msgs.append({
                "ts": ts, "speaker": "Master", "gate": 0,
                "msg": f"BLOQUEADO [G{blocked_at}] — {block_reason}",
                "tag": "block",
            })
        elif direction in ("LONG", "SHORT"):
            sizing_bd = v.get("metadata", {}).get("sizing_breakdown", {})
            breakdown = ""
            if sizing_bd:
                parts = []
                for k, label in [("anomaly_mult","A"), ("iceberg_mult","I"), ("regime_mult","R")]:
                    val = sizing_bd.get(k, 1.0)
                    if val != 1.0:
                        parts.append(f"{label}x{val:.2f}")
                if parts:
                    breakdown = " [" + " ".join(parts) + "]"
            msgs.append({
                "ts": ts, "speaker": "Master", "gate": 0,
                "msg": f"{direction} · {contracts:.1f}ct · mult=x{final_mult:.2f}{breakdown} · {latency:.1f}ms",
                "tag": "decision",
            })
        elif direction == "FLAT" and block_reason == "NO_SIGNAL":
            pass   # Silêncio — não polui o war room com FLAT normais
        elif direction == "FLAT" and block_reason == "NO_TRADE_ZERO_CONTRACTS":
            msgs.append({
                "ts": ts, "speaker": "Master", "gate": 0,
                "msg": f"NO_TRADE — contratos < 1 (soft vetos acumulados)",
                "tag": "warn",
            })

        return msgs


# ─── Singleton global ─────────────────────────────────────────────────────────

databus = NextGenDataBus()

"""
daily_scorecard.py — Shadow Execution Entry Point + The Auditor (NextGen v3.2 Sprint 2.2).

Corre em paralelo com a produção (sem interferência).
Lê os ticks do DataIngestor (watchdog) → avalia com FoxyzeMaster →
passa pelo InconsistencyDetector → loga.

Output:
    logs/nextgen_performance.jsonl         — cada tick avaliado (Gate 1-5 + shadow thresholds)
    logs/daily_scorecard.jsonl             — resumo de sessão v1 (sinais, contratos, latência)
    logs/inconsistencies_YYYY-MM-DD.jsonl  — eventos de threshold obsoleto detectados
    logs/threshold_health_YYYY-MM-DD.jsonl — relatório de saúde (Scorecard 2.0)

Uso:
    python -m apex_nextgen.daily_scorecard
    python -m apex_nextgen.daily_scorecard --watch-dir C:\\data\\level2\\_gc_xcec
    python -m apex_nextgen.daily_scorecard --replay C:\\data\\level2\\_gc_xcec\\microstructure_20260411.csv.gz

Diferença vs produção:
    - Leitura apenas (watchdog, sem writes de ordens)
    - Sizing progressivo em vez de binário
    - Todos os 5 gates avaliados (produção tem 3)
    - Shadow Thresholds paralelos (0.30 / 0.40 / 0.45) no Gate 3
    - InconsistencyDetector: detecta thresholds "lixo" em tempo real
    - Scorecard 2.0: imbalance density, shadow PnL, veto audit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Garantir que apex_nextgen está no path
_THIS_DIR = Path(__file__).parent
_ROOT_DIR = _THIS_DIR.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from apex_nextgen.common.base_provider import Direction, MarketTick, VetoStatus
from apex_nextgen.common.config import (
    PRODUCTION_L2_DIR,
    SCORECARD_LOG_PATH,
    SHADOW_LOG_DIR,
    SHADOW_LOG_PATH,
)
from apex_nextgen.common.data_ingestor import DataIngestor
from apex_nextgen.engine.foxyze_master import FoxyzeMaster, MasterVerdict
from apex_nextgen.analytics.inconsistency_detector import InconsistencyDetector

# ─── Logging ─────────────────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
_logger = logging.getLogger("nextgen.scorecard")


# ─── Session Stats ────────────────────────────────────────────────────────────

class SessionStats:
    """
    Acumula estatísticas por sessão (dia de trading).
    Sprint 2.2: inclui Threshold Health (Scorecard 2.0 — The Auditor).
    """

    def __init__(self, date_str: str):
        self.date            = date_str
        self.total_ticks     = 0
        self.signals         = 0          # ticks com direcção != FLAT
        self.longs           = 0
        self.shorts          = 0
        self.hard_vetos      = 0
        self.force_exits     = 0
        self.soft_vetos      = 0
        self.stubs_active    = 0          # gates em modo stub
        self.total_contracts = 0.0        # soma contratos sugeridos
        self.avg_latency_ms  = 0.0
        self._latencies: List[float] = []

        # ── Scorecard 2.0: Imbalance Density (Sprint 2.2) ─────────────────
        # Contar sinais por bracket de imbalance para detectar overtrading 0.30
        self.imb_bracket_below_noise = 0   # |imb| < 0.30 → FLAT (correcto)
        self.imb_bracket_noise       = 0   # |imb| 0.30–0.40 → "zona de ruído"
        self.imb_bracket_moderate    = 0   # |imb| 0.40–0.45 → sinal moderado
        self.imb_bracket_whale       = 0   # |imb| >= 0.45  → baleia confirmada

        # ── Scorecard 2.0: Shadow PnL (Sprint 2.2) ────────────────────────
        # "Como seria se usássemos threshold 0.45 em vez de 0.30?"
        # Contar sinais que existiriam em cada threshold
        self.shadow_signals_t030 = 0   # sinais activos com threshold 0.30 (produção)
        self.shadow_signals_t040 = 0   # sinais que também passariam em 0.40
        self.shadow_signals_t045 = 0   # sinais que também passariam em 0.45
        # "Only T030" = sinais que seriam cortados com threshold mais alto
        self.shadow_only_t030    = 0   # potencial overtrading da regra de Janeiro

        # ── Scorecard 2.0: Veto Audit (Sprint 2.2) ────────────────────────
        # Momentum blocks (gate 3) com imbalance forte → candidatos a recalibração
        self.momentum_block_with_strong_imb = 0  # bloqueado por delta mas |imb| >= 0.40

        # Regime labels para distribuição
        self._regime_counts: dict = defaultdict(int)

        # ── Sprint 3: Shadow Sizing Analysis ──────────────────────────────────
        # Tracking de contratos e multiplicadores por regime de imbalance
        # Prova: sizing reduz capital em NOISE, maximiza em WHALE
        self.no_trade_zero_contracts  = 0   # ticks bloqueados por zero-contract rule
        self._contracts_by_regime: dict = defaultdict(list)  # regime → [contracts, ...]
        self._anomaly_mults: list = []      # todos os anomaly_mult observados
        self._anomaly_level_counts: dict = defaultdict(int)  # "CLEAR"/"WARNING"/... → count

    def _extract_gate3_meta(self, verdict: MasterVerdict) -> dict:
        """Extrai metadata do Gate 3 para analise de threshold.
        Nota: gate_verdicts usa chave 'gate' (int), nao 'gate_number'."""
        for gv in verdict.gate_verdicts:
            if gv.get("gate") == 3:
                return gv.get("metadata", {})
        return {}

    def record(self, verdict: MasterVerdict):
        self.total_ticks += 1

        if verdict.direction == "LONG":
            self.longs   += 1
            self.signals += 1
        elif verdict.direction == "SHORT":
            self.shorts  += 1
            self.signals += 1
        elif verdict.direction == "FORCE_EXIT":
            self.force_exits += 1

        if verdict.blocked_at_gate is not None and verdict.direction == "FLAT":
            for gv in verdict.gate_verdicts:
                if gv.get("veto") == "HARD_VETO":
                    self.hard_vetos += 1
                    break

        soft = sum(
            1 for gv in verdict.gate_verdicts
            if gv.get("veto") == "SOFT_VETO"
        )
        self.soft_vetos      += soft
        self.total_contracts += verdict.contracts
        self._latencies.append(verdict.latency_ms)

        # ── Scorecard 2.0 analysis ────────────────────────────────────────
        g3_meta = self._extract_gate3_meta(verdict)
        imb     = abs(float(g3_meta.get("book_imbalance", 0.0)))

        # Imbalance density brackets
        if imb >= 0.45:
            self.imb_bracket_whale    += 1
        elif imb >= 0.40:
            self.imb_bracket_moderate += 1
        elif imb >= 0.30:
            self.imb_bracket_noise    += 1
        else:
            self.imb_bracket_below_noise += 1

        # Shadow PnL: contar por threshold activo
        shadow = g3_meta.get("shadow_thresholds", {})
        if shadow:
            t030_active = shadow.get("t030", {}).get("direction", "FLAT") != "FLAT"
            t040_active = shadow.get("t040", {}).get("direction", "FLAT") != "FLAT"
            t045_active = shadow.get("t045", {}).get("direction", "FLAT") != "FLAT"
            if t030_active:
                self.shadow_signals_t030 += 1
            if t040_active:
                self.shadow_signals_t040 += 1
            if t045_active:
                self.shadow_signals_t045 += 1
            if t030_active and not t040_active:
                self.shadow_only_t030    += 1  # sinal "de ruído" que 0.40 rejeitaria

        # Regime label
        regime = g3_meta.get("regime_label", "FLAT")
        self._regime_counts[regime] += 1

        # Veto audit: momentum block + imbalance forte
        if verdict.blocked_at_gate == 3 and imb >= 0.40:
            reason = verdict.block_reason or ""
            if "MOMENTUM_BLOCK" in reason:
                self.momentum_block_with_strong_imb += 1

        # ── Sprint 3: Shadow Sizing ────────────────────────────────────────
        # Rastrear zero-contract blocks
        if verdict.block_reason == "NO_TRADE_ZERO_CONTRACTS":
            self.no_trade_zero_contracts += 1

        # Categorizar contratos por regime de imbalance
        if verdict.direction in ("LONG", "SHORT") and verdict.contracts > 0:
            if imb >= 0.45:
                regime_label = "WHALE"
            elif imb >= 0.40:
                regime_label = "MODERATE"
            elif imb >= 0.30:
                regime_label = "NOISE"
            else:
                regime_label = "BELOW_NOISE"
            self._contracts_by_regime[regime_label].append(verdict.contracts)

        # Anomaly multiplier do Gate 1
        sizing_bd = verdict.metadata.get("sizing_breakdown", {}) if verdict.metadata else {}
        if sizing_bd:
            a_mult = sizing_bd.get("anomaly_mult", 1.0)
            self._anomaly_mults.append(float(a_mult))

        # Anomaly level do Gate 1 (CLEAR/WARNING/CRITICAL/SEVERE/HARD_VETO)
        for gv in verdict.gate_verdicts:
            if gv.get("gate") == 1:
                level = gv.get("metadata", {}).get("anomaly_level", "STUB")
                self._anomaly_level_counts[level] += 1
                break

    def finalize(self) -> dict:
        if self._latencies:
            self.avg_latency_ms = sum(self._latencies) / len(self._latencies)
        total = max(self.total_ticks, 1)

        # Shadow PnL: % de redução de overtrading se usássemos 0.45
        overtrading_reduction = (
            round((1 - self.shadow_signals_t045 / max(self.shadow_signals_t030, 1)) * 100, 1)
            if self.shadow_signals_t030 > 0 else 0.0
        )

        return {
            "date":              self.date,
            "total_ticks":       self.total_ticks,
            "signals":           self.signals,
            "longs":             self.longs,
            "shorts":            self.shorts,
            "signal_rate_pct":   round(self.signals / total * 100, 2),
            "long_short_ratio":  round(self.longs / max(self.shorts, 1), 2),
            "hard_vetos":        self.hard_vetos,
            "force_exits":       self.force_exits,
            "soft_veto_events":  self.soft_vetos,
            "avg_contracts":     round(self.total_contracts / total, 3),
            "avg_latency_ms":    round(self.avg_latency_ms, 2),
            "max_latency_ms":    round(max(self._latencies, default=0), 2),

            # ── Scorecard 2.0: Threshold Health ───────────────────────────
            "threshold_health": {
                "imbalance_density": {
                    "below_noise_pct":       round(self.imb_bracket_below_noise / total * 100, 1),
                    "noise_030_040_pct":     round(self.imb_bracket_noise       / total * 100, 1),
                    "moderate_040_045_pct":  round(self.imb_bracket_moderate    / total * 100, 1),
                    "whale_045plus_pct":     round(self.imb_bracket_whale       / total * 100, 1),
                },
                "shadow_pnl": {
                    "signals_at_t030":     self.shadow_signals_t030,
                    "signals_at_t040":     self.shadow_signals_t040,
                    "signals_at_t045":     self.shadow_signals_t045,
                    "only_t030_noise":     self.shadow_only_t030,
                    "overtrading_reduction_pct_if_t045": overtrading_reduction,
                    "note": (
                        f"Usar threshold 0.45 eliminaria ~{overtrading_reduction:.0f}% dos "
                        f"sinais actuais (0.30). {self.shadow_only_t030} sinais seriam "
                        f"cortados por serem apenas 'ruido de Janeiro'."
                    ),
                },
                "veto_audit": {
                    "momentum_blocks_with_strong_imb": self.momentum_block_with_strong_imb,
                    "note": (
                        f"{self.momentum_block_with_strong_imb} SHORTs/LONGs bloqueados "
                        f"por delta threshold mas com |imbalance| >= 0.40 — "
                        f"candidatos a recalibrar MOMENTUM_BLOCK."
                    ) if self.momentum_block_with_strong_imb > 0 else "OK — sem candidatos",
                },
                "regime_distribution":   dict(self._regime_counts),

                # ── Sprint 3: Shadow Sizing ────────────────────────────────
                "sizing_analysis": self._build_sizing_analysis(),
            },
        }

    def _build_sizing_analysis(self) -> dict:
        """
        Sprint 3: Prova que o sizing progressivo protege capital em NOISE
        e maximiza em WHALE.

        Comparação chave:
            avg_contracts[NOISE] << avg_contracts[WHALE]
            → sizing é discriminativo (não binário como produção)
        """
        # Avg contratos por regime
        avg_by_regime = {}
        for regime, conts in self._contracts_by_regime.items():
            if conts:
                avg_by_regime[regime] = round(sum(conts) / len(conts), 2)

        # Anomaly Gate 1 distribuição
        avg_anomaly_mult = (
            round(sum(self._anomaly_mults) / len(self._anomaly_mults), 3)
            if self._anomaly_mults else 1.0
        )

        # Sizing ratio: WHALE / NOISE (deve ser > 1.0 — prova de discriminação)
        noise_avg = avg_by_regime.get("NOISE", 0)
        whale_avg = avg_by_regime.get("WHALE", 0)
        sizing_ratio = round(whale_avg / noise_avg, 2) if noise_avg > 0 else None

        note_parts = []
        if sizing_ratio is not None:
            if sizing_ratio > 1.2:
                note_parts.append(
                    f"SIZING DISCRIMINATIVO: WHALE {whale_avg:.2f}ct vs NOISE {noise_avg:.2f}ct "
                    f"(ratio={sizing_ratio:.2f}x) — capital protegido em zona de ruido."
                )
            else:
                note_parts.append(
                    f"Sizing pouco discriminativo (ratio={sizing_ratio:.2f}x) — "
                    f"verificar se modelo Grenadier está em modo STUB."
                )
        if self.no_trade_zero_contracts > 0:
            note_parts.append(
                f"{self.no_trade_zero_contracts} ticks com zero-contract rule activada "
                f"(soft vetos acumulados → contratos < 1)."
            )

        return {
            "avg_contracts_by_regime":  avg_by_regime,
            "avg_anomaly_mult":         avg_anomaly_mult,
            "anomaly_level_distribution": dict(self._anomaly_level_counts),
            "sizing_ratio_whale_vs_noise": sizing_ratio,
            "no_trade_zero_contracts":  self.no_trade_zero_contracts,
            "note": " | ".join(note_parts) if note_parts else "Gate 1 em STUB — sem sizing discriminativo.",
        }


# ─── Scorecard Runner ─────────────────────────────────────────────────────────

class DailyScorecard:
    """
    Shadow runner que avalia ticks em tempo real e emite logs.

    Dois modos:
        watch  : DataIngestor watchdog (produção live)
        replay : lê CSV histórico linha a linha (backtesting rápido do NextGen)
    """

    def __init__(self, watch_dir: Path = PRODUCTION_L2_DIR):
        SHADOW_LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._master     = FoxyzeMaster()
        self._detector   = InconsistencyDetector(log_dir=SHADOW_LOG_DIR)
        self._watch_dir  = watch_dir
        self._perf_log   = open(SHADOW_LOG_PATH, "a", encoding="utf-8")
        self._sc_log     = open(SCORECARD_LOG_PATH, "a", encoding="utf-8")
        self._stats:     Dict[str, SessionStats] = {}
        self._lock       = threading.Lock()
        self._stop_evt   = threading.Event()
        self._tick_count = 0

        _logger.info(
            "DailyScorecard pronto | gates: G1-G5 | InconsistencyDetector activo | log -> %s",
            SHADOW_LOG_PATH,
        )
        self._log_gate_status()

    def _log_gate_status(self):
        """Reporta quais gates estão em stub vs. modo real."""
        gates = {
            "G1-AnomalyForgeV3":  self._master.gate1.is_ready(),
            "G2-NewsGate":        self._master.gate2.is_ready(),
            "G3-FluxSignal":      self._master.gate3.is_ready(),
            "G4-OrderStorm":      self._master.gate4.is_ready(),
            "G5-RegimeForecast":  self._master.gate5.is_ready(),
        }
        for name, ready in gates.items():
            status = "READY" if ready else "STUB"
            _logger.info("  %-25s → %s", name, status)

    def on_tick(self, tick: MarketTick, prod_decision: Optional[str] = None):
        """
        Callback do DataIngestor — chamado para cada tick novo.

        Parameters
        ----------
        prod_decision : "CONFIRMED" | "FILTERED" | None
            Se fornecido (modo comparator), activa detecção completa de inconsistências.
        """
        try:
            verdict = self._master.evaluate(tick)
            # InconsistencyDetector: O(1), sem I/O no path crítico
            self._detector.inspect(verdict, prod_decision=prod_decision)
            self._record(verdict)
        except Exception as e:
            _logger.warning("Erro ao avaliar tick: %s", e)

    def _record(self, verdict: MasterVerdict):
        date_str = verdict.timestamp.strftime("%Y-%m-%d")

        with self._lock:
            if date_str not in self._stats:
                # Nova sessão — fechar a anterior se existir
                if self._stats:
                    self._close_session(next(reversed(self._stats)))
                self._stats[date_str] = SessionStats(date_str)
                _logger.info("Nova sessão iniciada: %s", date_str)

            self._stats[date_str].record(verdict)
            self._tick_count += 1

        # Escrever ao log de performance (JSONL)
        try:
            self._perf_log.write(json.dumps(verdict.to_dict()) + "\n")
            self._perf_log.flush()
        except Exception as e:
            _logger.debug("Erro no perf log: %s", e)

        # Log periódico na consola
        if self._tick_count % 100 == 0:
            self._print_summary(date_str)

    def _close_session(self, date_str: str):
        stats = self._stats.get(date_str)
        if stats is None:
            return
        summary = stats.finalize()
        try:
            self._sc_log.write(json.dumps(summary) + "\n")
            self._sc_log.flush()
        except Exception as e:
            _logger.warning("Erro ao fechar sessao: %s", e)

        # ── Scorecard 2.0: Threshold Health Report ────────────────────────
        try:
            self._detector.save_daily_summary(date_str)
            health  = self._detector.threshold_health_report(date_str)
            th      = summary.get("threshold_health", {})
            sp      = th.get("shadow_pnl", {})
            va      = th.get("veto_audit", {})
            density = th.get("imbalance_density", {})
            _logger.info(
                "=== SCORECARD 2.0 — THRESHOLD HEALTH [%s] ===", date_str,
            )
            _logger.info(
                "  Imbalance Density: noise=%.1f%% moderate=%.1f%% whale=%.1f%%",
                density.get("noise_030_040_pct", 0),
                density.get("moderate_040_045_pct", 0),
                density.get("whale_045plus_pct", 0),
            )
            _logger.info(
                "  Shadow PnL: t030=%d sinais | t040=%d | t045=%d | "
                "ruido_exclusivo=%d | reducao_overtrading=%.1f%%",
                sp.get("signals_at_t030", 0),
                sp.get("signals_at_t040", 0),
                sp.get("signals_at_t045", 0),
                sp.get("only_t030_noise", 0),
                sp.get("overtrading_reduction_pct_if_t045", 0),
            )
            _logger.info(
                "  Veto Audit: %d bloqueios delta com imbalance forte",
                va.get("momentum_blocks_with_strong_imb", 0),
            )
            _logger.info(
                "  Inconsistencias: obsolete=%d toxicity=%d momentum_cand=%d "
                "| Thresholds lixo=%d | DoR=%s | Rec=%s",
                health.get("inconsistencies", {}).get("obsolete_threshold_blocks", 0),
                health.get("inconsistencies", {}).get("high_toxicity_ignored", 0),
                health.get("inconsistencies", {}).get("momentum_veto_candidates", 0),
                health.get("inconsistencies", {}).get("trash_threshold_count", 0),
                "MET" if health.get("dor_met") else "NOT_MET",
                health.get("recommendation", "N/A"),
            )
            # Sprint 3: Sizing Analysis
            sa = th.get("sizing_analysis", {})
            avg_regime = sa.get("avg_contracts_by_regime", {})
            _logger.info(
                "  Sizing Analysis: avg_anomaly_mult=%.3f | "
                "NOISE=%.2fct MODERATE=%.2fct WHALE=%.2fct | "
                "ratio=%.2fx | zero_contract=%d",
                sa.get("avg_anomaly_mult", 1.0),
                avg_regime.get("NOISE", 0.0),
                avg_regime.get("MODERATE", 0.0),
                avg_regime.get("WHALE", 0.0),
                sa.get("sizing_ratio_whale_vs_noise") or 0.0,
                sa.get("no_trade_zero_contracts", 0),
            )
            if sa.get("note"):
                _logger.info("  Sizing Note: %s", sa["note"])
            _logger.info("=" * 55)
        except Exception as e:
            _logger.warning("Erro no Scorecard 2.0: %s", e)

        _logger.info(
            "Sessao encerrada %s | ticks=%d signals=%d longs=%d shorts=%d "
            "hard_vetos=%d avg_cont=%.2f avg_lat=%.1fms",
            summary["date"],
            summary["total_ticks"],
            summary["signals"],
            summary["longs"],
            summary["shorts"],
            summary["hard_vetos"],
            summary["avg_contracts"],
            summary["avg_latency_ms"],
        )

    def _print_summary(self, date_str: str):
        with self._lock:
            stats = self._stats.get(date_str)
        if stats is None:
            return
        total = max(stats.total_ticks, 1)
        # Shadow PnL snapshot (inline)
        t030 = stats.shadow_signals_t030
        t045 = stats.shadow_signals_t045
        noise = stats.shadow_only_t030
        _logger.info(
            "[SHADOW] %s | ticks=%d | L=%d S=%d | vetos=%d | cont=%.2f | "
            "imb[noise=%d mod=%d whale=%d] | shadow[t030=%d t045=%d noise_only=%d]",
            date_str,
            stats.total_ticks,
            stats.longs,
            stats.shorts,
            stats.hard_vetos,
            stats.total_contracts / total,
            stats.imb_bracket_noise,
            stats.imb_bracket_moderate,
            stats.imb_bracket_whale,
            t030, t045, noise,
        )

    def run_watch(self):
        """Modo watchdog: corre indefinidamente até SIGINT."""
        ingestor = DataIngestor(callback=self.on_tick, watch_dir=self._watch_dir)
        ingestor.start()

        _logger.info("Scorecard em modo WATCH — Ctrl+C para parar")

        def _shutdown(sig, frame):
            _logger.info("Shutdown solicitado...")
            self._stop_evt.set()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        try:
            while not self._stop_evt.is_set():
                time.sleep(1)
        finally:
            ingestor.stop()
            self._cleanup()

    def run_replay(self, csv_path: Path):
        """Modo replay: processa CSV histórico linha a linha."""
        import csv as _csv

        _logger.info("Scorecard em modo REPLAY — %s", csv_path)

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for i, row in enumerate(reader):
                tick = self._row_to_tick(row, str(csv_path))
                if tick is None:
                    continue
                self.on_tick(tick)
                if i % 1000 == 0:
                    _logger.debug("Replay: %d rows processados", i)

        self._cleanup()
        _logger.info("Replay concluído — %d ticks processados", self._tick_count)

    @staticmethod
    def _row_to_tick(row: dict, source: str) -> Optional[MarketTick]:
        """Converte uma row de CSV para MarketTick (reutiliza lógica do DataIngestor)."""
        try:
            ts_raw = row.get("recv_timestamp") or row.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(str(ts_raw))
            except (ValueError, TypeError):
                ts = datetime.utcnow()

            def _f(k, d=0.0):
                try:
                    return float(row.get(k, d))
                except (ValueError, TypeError):
                    return d

            return MarketTick(
                timestamp        = ts,
                spread           = _f("spread"),
                total_bid_depth  = _f("total_bid_depth"),
                total_ask_depth  = _f("total_ask_depth"),
                book_imbalance   = _f("book_imbalance"),
                trade_volume     = _f("trade_volume"),
                cumulative_delta = _f("cumulative_delta"),
                source_file      = Path(source).name,
            )
        except Exception:
            return None

    def _cleanup(self):
        # Fechar última sessão aberta
        with self._lock:
            for date_str in list(self._stats.keys()):
                self._close_session(date_str)
        try:
            self._perf_log.close()
            self._sc_log.close()
        except Exception:
            pass


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="NextGen Daily Scorecard — shadow execution sem interferência na produção"
    )
    p.add_argument(
        "--watch-dir",
        type=Path,
        default=PRODUCTION_L2_DIR,
        help="Directório a monitorar (default: %(default)s)",
    )
    p.add_argument(
        "--replay",
        type=Path,
        default=None,
        metavar="CSV",
        help="Modo replay: processar CSV histórico em vez de watchdog",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Nível de logging (default: %(default)s)",
    )
    return p.parse_args()


def main():
    args = _parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    scorecard = DailyScorecard(watch_dir=args.watch_dir)

    if args.replay:
        if not args.replay.exists():
            _logger.error("Ficheiro de replay não encontrado: %s", args.replay)
            sys.exit(1)
        scorecard.run_replay(args.replay)
    else:
        scorecard.run_watch()


if __name__ == "__main__":
    main()

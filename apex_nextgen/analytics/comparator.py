"""
comparator.py — Comparador Shadow: Produção vs NextGen (Sprint 1.3).

Lê `live_log.csv` (decisões da produção), reconstrói o MarketTick correspondente
a partir dos `depth_snapshots_*.csv.gz`, avalia com FoxyzeMaster e compara.

Métricas produzidas:
    agreement_rate      — % decisões onde NextGen concorda com produção
    extra_signals       — sinais NextGen que produção rejeitou (oportunidades perdidas)
    blocked_by_nextgen  — sinais produção que NextGen teria bloqueado
    sizing_delta        — diferença de contratos NextGen vs produção (4 fixo)

Uso:
    comparator = ShadowComparator()
    report = comparator.run(date="2026-04-09")
    report.print_summary()
    report.save_jsonl()
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..common.base_provider import Direction, MarketTick
from ..common.config import SHADOW_LOG_DIR
from ..engine.foxyze_master import FoxyzeMaster, MasterVerdict
from .inconsistency_detector import InconsistencyDetector
from .tick_reconstructor import TickReconstructor

_logger = logging.getLogger("nextgen.comparator")

# Caminhos da produção
_LOGS_DIR        = Path(r"C:\FluxQuantumAI\logs")
_LIVE_LOG_PATH   = _LOGS_DIR / "live_log.csv"
_TRADES_CSV_PATH = _LOGS_DIR / "trades_live.csv"
_PROD_CONTRACTS  = 4  # produção usa sempre 4 contratos (sizing binário)


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ComparisonEntry:
    """Resultado da comparação para uma decisão de produção."""
    timestamp:        str
    prod_direction:   str        # LONG | SHORT | FLAT (gate_decision != CONFIRMED → FLAT)
    prod_gate_score:  float
    nextgen_direction: str
    nextgen_contracts: float
    nextgen_mult:     float
    nextgen_blocked_at: Optional[int]
    agreement:        bool       # NextGen e produção concordam na direcção
    tick_available:   bool       # Se havia depth_snapshot disponível
    spread:           float
    book_imbalance:   float
    cumulative_delta: float
    soft_veto_gates:  List[int]
    metadata:         dict = field(default_factory=dict)


@dataclass
class ComparisonReport:
    """Sumário completo de uma sessão de comparação."""
    date:              str
    total_prod_decisions: int
    confirmed_prod:    int        # produção CONFIRMED
    nextgen_signals:   int        # NextGen direcção != FLAT
    agreements:        int        # ambos concordam (dir e CONFIRMED/FLAT)
    agreement_rate:    float      # %
    extra_by_nextgen:  int        # NextGen LONG/SHORT quando produção FLAT
    blocked_by_nextgen: int       # Produção CONFIRMED mas NextGen FLAT/HARD_VETO
    avg_nextgen_contracts: float  # média de contratos NextGen em sinais confirmados
    avg_prod_contracts: float     # sempre 4 (binário)
    sizing_uplift:     float      # (avg_nextgen - avg_prod) / avg_prod %
    ticks_missing:     int        # entradas sem depth_snapshot
    threshold_health:  dict       = field(default_factory=dict)  # Sprint 2.2 — The Auditor
    sizing_analysis:   dict       = field(default_factory=dict)  # Sprint 3 — Discriminative Sizing
    entries:           List[ComparisonEntry] = field(default_factory=list)

    def print_summary(self):
        _logger.info("=" * 65)
        _logger.info("SHADOW COMPARATOR REPORT -- %s", self.date)
        _logger.info("=" * 65)
        _logger.info("  Decisoes producao:    %d  (confirmed=%d)",
                     self.total_prod_decisions, self.confirmed_prod)
        _logger.info("  Sinais NextGen:       %d", self.nextgen_signals)
        _logger.info("  Agreement rate:       %.1f%%", self.agreement_rate * 100)
        _logger.info("  Extra (NextGen only): %d", self.extra_by_nextgen)
        _logger.info("  Blocked by NextGen:   %d", self.blocked_by_nextgen)
        _logger.info("  Avg contracts:")
        _logger.info("    Producao:   %.1f (fixo)", self.avg_prod_contracts)
        _logger.info("    NextGen:    %.2f", self.avg_nextgen_contracts)
        _logger.info("    Sizing D:   %+.1f%%", self.sizing_uplift)
        _logger.info("  Ticks sem snapshot:   %d", self.ticks_missing)
        # ── Threshold Health (Sprint 2.2) ─────────────────────────────────
        if self.threshold_health:
            th   = self.threshold_health
            inc  = th.get("inconsistencies", {})
            den  = th.get("imbalance_density", {})
            div  = th.get("divergencias_lucrativas", {})
            _logger.info("  --- THRESHOLD HEALTH (The Auditor) ---")
            _logger.info("  Imbalance Density:")
            _logger.info("    noise 0.30-0.40:    %.1f%%", den.get("noise_zone_030_040_pct", 0))
            _logger.info("    moderate 0.40-0.45: %.1f%%", den.get("moderate_040_045_pct", 0))
            _logger.info("    whale 0.45+:        %.1f%%", den.get("whale_045plus_pct", 0))
            _logger.info("  Inconsistencias:")
            _logger.info("    Obsolete blocks:   %d", inc.get("obsolete_threshold_blocks", 0))
            _logger.info("    High toxicity:     %d", inc.get("high_toxicity_ignored", 0))
            _logger.info("    Momentum cand.:    %d", inc.get("momentum_veto_candidates", 0))
            _logger.info("    Thresholds lixo:   %d", inc.get("trash_threshold_count", 0))
            _logger.info("  Divergencias Lucrativas:")
            _logger.info("    Oport. perdidas (shadow 0.45 activo, prod FILTERED): %d",
                         div.get("lucro_potencial_perdido", 0))
            _logger.info("    Prod. entrou em ruido (0.30-0.40): %d",
                         div.get("ruido_prod_confirmado", 0))
            if th.get("suggested_threshold"):
                _logger.info("  Threshold sugerido: %.2f", th["suggested_threshold"])
            _logger.info("  DoR: %s | Rec: %s",
                         "MET" if th.get("dor_met") else "NOT MET",
                         th.get("recommendation", "N/A"))
            _logger.info("  >> SUMARIO EXECUTIVO: %s", th.get("executive_summary", "N/A"))
        # ── Sprint 3: Shadow Sizing ────────────────────────────────────────────
        if self.sizing_analysis:
            sa  = self.sizing_analysis
            reg = sa.get("avg_contracts_by_regime", {})
            _logger.info("  --- SIZING ANALYSIS (Sprint 3) ---")
            _logger.info("  Avg contratos por regime:")
            _logger.info("    NOISE (0.30-0.40):  %.2fct", reg.get("NOISE", 0.0))
            _logger.info("    MODERATE (0.40-0.45): %.2fct", reg.get("MODERATE", 0.0))
            _logger.info("    WHALE (0.45+):      %.2fct", reg.get("WHALE", 0.0))
            _logger.info("  Sizing ratio WHALE/NOISE: %.2fx",
                         sa.get("sizing_ratio_whale_vs_noise") or 0.0)
            _logger.info("  No-trade (zero contracts): %d",
                         sa.get("no_trade_zero_contracts", 0))
            _logger.info("  Avg anomaly mult (G1): %.3f", sa.get("avg_anomaly_mult", 1.0))
            if sa.get("note"):
                _logger.info("  >> %s", sa["note"])
        _logger.info("=" * 65)

    def save_jsonl(self, output_dir: Path = SHADOW_LOG_DIR):
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"comparison_{self.date}.jsonl"

        summary = {
            "type": "summary",
            "date": self.date,
            "total_prod_decisions": self.total_prod_decisions,
            "confirmed_prod": self.confirmed_prod,
            "nextgen_signals": self.nextgen_signals,
            "agreements": self.agreements,
            "agreement_rate": round(self.agreement_rate, 4),
            "extra_by_nextgen": self.extra_by_nextgen,
            "blocked_by_nextgen": self.blocked_by_nextgen,
            "avg_nextgen_contracts": round(self.avg_nextgen_contracts, 3),
            "avg_prod_contracts": self.avg_prod_contracts,
            "sizing_uplift_pct": round(self.sizing_uplift, 2),
            "ticks_missing": self.ticks_missing,
            "threshold_health": self.threshold_health,   # Sprint 2.2
            "sizing_analysis":  self.sizing_analysis,    # Sprint 3
        }

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
            for entry in self.entries:
                f.write(json.dumps(asdict(entry)) + "\n")

        _logger.info("Comparação salva em: %s", out_path)
        return out_path


# ─── Production Log Reader ────────────────────────────────────────────────────

def _read_live_log(date: str) -> List[dict]:
    """Lê live_log.csv filtrando para a data especificada."""
    if not _LIVE_LOG_PATH.exists():
        _logger.warning("live_log.csv não encontrado: %s", _LIVE_LOG_PATH)
        return []

    rows = []
    with open(_LIVE_LOG_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = row.get("timestamp", "")
            if ts_str.startswith(date):
                rows.append(row)

    _logger.info("live_log: %d entradas para %s", len(rows), date)
    return rows


def _prod_direction(row: dict) -> str:
    """Mapeia gate_decision + direction → LONG | SHORT | FLAT."""
    decision = row.get("gate_decision", "").upper()
    if decision != "CONFIRMED":
        return "FLAT"
    dir_ = row.get("direction", "").upper()
    if dir_ in ("LONG", "BUY"):
        return "LONG"
    elif dir_ in ("SHORT", "SELL"):
        return "SHORT"
    return "FLAT"


def _parse_ts(ts_str: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ─── ShadowComparator ─────────────────────────────────────────────────────────

class ShadowComparator:
    """
    Compara decisões da produção com decisões do NextGen para um dado dia.

    Arquitectura:
        live_log.csv ─→ _read_live_log()
                         ↓
        depth_snapshots ─→ TickReconstructor.nearest_tick()
                         ↓
        FoxyzeMaster.evaluate()
                         ↓
        ComparisonEntry por decisão
                         ↓
        ComparisonReport (sumário + entries)
    """

    def __init__(self):
        self._master     = FoxyzeMaster()
        self._detector   = InconsistencyDetector(log_dir=SHADOW_LOG_DIR)
        self._reconstructors: Dict[str, TickReconstructor] = {}

    def _get_reconstructor(self, date: str) -> TickReconstructor:
        if date not in self._reconstructors:
            self._reconstructors[date] = TickReconstructor(date)
        return self._reconstructors[date]

    def _make_fallback_tick(self, row: dict, ts: datetime) -> MarketTick:
        """
        Cria MarketTick mínimo a partir dos campos do live_log quando
        não há depth_snapshot disponível.
        Permite avaliar pelo menos Gate 3 (FluxSignal via cumulative_delta).
        """
        try:
            macro_delta = float(row.get("macro_delta", 0))
        except (ValueError, TypeError):
            macro_delta = 0.0

        return MarketTick(
            timestamp        = ts,
            spread           = 0.20,   # valor conservador — não activa veto de spread
            total_bid_depth  = 150.0,  # neutro — não activa veto de liquidez
            total_ask_depth  = 150.0,
            book_imbalance   = 0.0,    # sem dados → neutro (Gate 3 retorna FLAT)
            cumulative_delta = macro_delta,
            source_file      = "live_log_fallback",
        )

    def run(self, date: str) -> ComparisonReport:
        """
        Executa a comparação para o dia especificado.

        Parameters
        ----------
        date : str
            Data no formato "YYYY-MM-DD"
        """
        _logger.info("ShadowComparator: a processar %s", date)

        rows = _read_live_log(date)
        if not rows:
            _logger.warning("Nenhuma entrada encontrada para %s", date)
            return ComparisonReport(
                date=date,
                total_prod_decisions=0, confirmed_prod=0,
                nextgen_signals=0, agreements=0, agreement_rate=0.0,
                extra_by_nextgen=0, blocked_by_nextgen=0,
                avg_nextgen_contracts=0.0, avg_prod_contracts=_PROD_CONTRACTS,
                sizing_uplift=0.0, ticks_missing=0,
            )

        reconstructor = self._get_reconstructor(date)
        entries       = []
        ticks_missing = 0

        for row in rows:
            ts = _parse_ts(row.get("timestamp", ""))
            if ts is None:
                continue

            prod_dir = _prod_direction(row)
            try:
                macro_delta = float(row.get("macro_delta", 0))
            except (ValueError, TypeError):
                macro_delta = 0.0

            # Tentar reconstruir tick real do depth_snapshot
            tick = None
            tick_available = False
            if reconstructor.is_loaded:
                tick = reconstructor.nearest_tick(ts, override_cumulative_delta=macro_delta)
                if tick is not None:
                    tick_available = True

            if tick is None:
                tick = self._make_fallback_tick(row, ts)
                ticks_missing += 1

            # Avaliar com NextGen
            try:
                verdict = self._master.evaluate(tick)
            except Exception as e:
                _logger.warning("Erro ao avaliar tick %s: %s", ts.isoformat(), e)
                continue

            # ── InconsistencyDetector: auditoria com decisão real da produção ──
            # prod_decision conhecido aqui — ciclo fechado entre produção e NextGen
            self._detector.inspect(
                verdict,
                prod_decision="CONFIRMED" if prod_dir != "FLAT" else "FILTERED",
            )

            nextgen_dir = verdict.direction
            if nextgen_dir in ("FLAT", "FORCE_EXIT"):
                nextgen_flat = True
            else:
                nextgen_flat = False

            prod_flat = (prod_dir == "FLAT")

            # Calcular agreement (direcção)
            if prod_flat and nextgen_flat:
                agreement = True   # ambos sem sinal
            elif not prod_flat and not nextgen_flat:
                agreement = (prod_dir == nextgen_dir)  # mesmo sentido
            else:
                agreement = False  # um tem sinal, o outro não

            try:
                gate_score = float(row.get("score", 0))
            except (ValueError, TypeError):
                gate_score = 0.0

            entry = ComparisonEntry(
                timestamp         = ts.isoformat(),
                prod_direction    = prod_dir,
                prod_gate_score   = gate_score,
                nextgen_direction = nextgen_dir,
                nextgen_contracts = verdict.contracts,
                nextgen_mult      = verdict.final_multiplier,
                nextgen_blocked_at= verdict.blocked_at_gate,
                agreement         = agreement,
                tick_available    = tick_available,
                spread            = tick.spread,
                book_imbalance    = tick.book_imbalance,
                cumulative_delta  = tick.cumulative_delta,
                soft_veto_gates   = verdict.metadata.get("soft_veto_gates", []),
                metadata          = {
                    "prod_reason":    row.get("reason", ""),
                    "prod_trigger":   row.get("trigger", ""),
                    "block_reason":   verdict.block_reason,
                    "tick_source":    "depth_snapshot" if tick_available else "fallback",
                },
            )
            entries.append(entry)

        # Calcular métricas
        confirmed_prod    = sum(1 for e in entries if e.prod_direction != "FLAT")
        nextgen_signals   = sum(1 for e in entries if e.nextgen_direction not in ("FLAT", "FORCE_EXIT", "NEUTRAL"))
        agreements        = sum(1 for e in entries if e.agreement)
        extra_by_nextgen  = sum(1 for e in entries if e.prod_direction == "FLAT" and e.nextgen_direction not in ("FLAT", "FORCE_EXIT", "NEUTRAL"))
        blocked_by_nextgen= sum(1 for e in entries if e.prod_direction != "FLAT" and e.nextgen_direction in ("FLAT", "FORCE_EXIT"))

        confirmed_entries = [e for e in entries if e.prod_direction != "FLAT"]
        avg_ng_contracts  = (
            sum(e.nextgen_contracts for e in confirmed_entries) / len(confirmed_entries)
            if confirmed_entries else 0.0
        )

        sizing_uplift = (
            (avg_ng_contracts - _PROD_CONTRACTS) / _PROD_CONTRACTS * 100
            if avg_ng_contracts > 0 else 0.0
        )

        # ── Threshold Health automático (Sprint 2.2) ─────────────────────────
        health_report = self._detector.threshold_health_report(date)
        self._detector.save_daily_summary(date)

        # ── Sprint 3: Sizing Analysis ─────────────────────────────────────────
        sizing_analysis = self._build_sizing_analysis(entries)

        report = ComparisonReport(
            date                  = date,
            total_prod_decisions  = len(entries),
            confirmed_prod        = confirmed_prod,
            nextgen_signals       = nextgen_signals,
            agreements            = agreements,
            agreement_rate        = agreements / len(entries) if entries else 0.0,
            extra_by_nextgen      = extra_by_nextgen,
            blocked_by_nextgen    = blocked_by_nextgen,
            avg_nextgen_contracts = avg_ng_contracts,
            avg_prod_contracts    = float(_PROD_CONTRACTS),
            sizing_uplift         = sizing_uplift,
            ticks_missing         = ticks_missing,
            threshold_health      = health_report,
            sizing_analysis       = sizing_analysis,
            entries               = entries,
        )

        report.print_summary()
        return report

    def _build_sizing_analysis(self, entries: List[ComparisonEntry]) -> dict:
        """
        Sprint 3: Prova que o sizing progressivo é discriminativo.

        Classifica cada entrada por regime de imbalance e calcula
        avg_contracts por regime. Se WHALE >> NOISE → sizing é inteligente.
        """
        from collections import defaultdict

        contracts_by_regime: dict = defaultdict(list)
        anomaly_mults: list = []
        no_trade_count = 0

        for e in entries:
            imb = abs(e.book_imbalance)
            if e.metadata.get("block_reason") == "NO_TRADE_ZERO_CONTRACTS":
                no_trade_count += 1

            # Só contar sinais reais (não FLAT)
            if e.nextgen_direction in ("LONG", "SHORT") and e.nextgen_contracts > 0:
                if imb >= 0.45:
                    regime = "WHALE"
                elif imb >= 0.40:
                    regime = "MODERATE"
                elif imb >= 0.30:
                    regime = "NOISE"
                else:
                    regime = "BELOW_NOISE"
                contracts_by_regime[regime].append(e.nextgen_contracts)

            # Extrair anomaly_mult do sizing_breakdown (se presente no metadata)
            sizing_bd = e.metadata.get("sizing_breakdown", {})
            if sizing_bd and "anomaly_mult" in sizing_bd:
                anomaly_mults.append(float(sizing_bd["anomaly_mult"]))

        avg_by_regime = {
            r: round(sum(cs) / len(cs), 2)
            for r, cs in contracts_by_regime.items() if cs
        }

        avg_anomaly_mult = (
            round(sum(anomaly_mults) / len(anomaly_mults), 3)
            if anomaly_mults else 1.0
        )

        noise_avg = avg_by_regime.get("NOISE", 0)
        whale_avg = avg_by_regime.get("WHALE", 0)
        sizing_ratio = round(whale_avg / noise_avg, 2) if noise_avg > 0 else None

        note = ""
        if sizing_ratio is not None:
            if sizing_ratio > 1.2:
                note = (
                    f"SIZING DISCRIMINATIVO: WHALE {whale_avg:.2f}ct vs NOISE {noise_avg:.2f}ct "
                    f"(ratio={sizing_ratio:.2f}x) — capital protegido em zona ruido."
                )
            else:
                note = (
                    f"Sizing pouco discriminativo (ratio={sizing_ratio:.2f}x) — "
                    f"G1 provavelmente em STUB (sem modelo Grenadier V2)."
                )
        elif avg_anomaly_mult == 1.0:
            note = "Gate 1 em STUB — todos os multipliers=1.0, sem sizing discriminativo."

        return {
            "avg_contracts_by_regime":    avg_by_regime,
            "avg_anomaly_mult":           avg_anomaly_mult,
            "sizing_ratio_whale_vs_noise": sizing_ratio,
            "no_trade_zero_contracts":    no_trade_count,
            "note":                       note,
        }

    def run_range(self, dates: List[str]) -> List[ComparisonReport]:
        """Corre o comparador para uma lista de datas e retorna os relatórios."""
        reports = []
        for date in dates:
            report = self.run(date)
            report.save_jsonl()
            reports.append(report)
        return reports

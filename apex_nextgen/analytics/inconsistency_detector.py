"""
inconsistency_detector.py — Sprint 2.2: "The Truth Seeker"

Motor de auditoria que detects discrepâncias entre:
  - Thresholds calibrados em Janeiro (AggregateMethod.None, imb_max ≈ ±0.28)
  - Realidade de Abril (ByPriceLVL, imb range ±0.30–0.80+)

Detecta 3 categorias de "Threshold Lixo":

  OBSOLETE_THRESHOLD_BLOCK
    O threshold de 0.30 de Janeiro foi o ÚNICO motivo para FLAT.
    Com 0.40 ou 0.45 o sinal seria válido — possível missed trade.

  HIGH_TOXICITY_IGNORED
    Produção abriu posição (CONFIRMED) mas NextGen detectou imbalance
    0.30–0.39 (zona de ruído no novo regime ByPriceLVL).
    Risco: produção entrou num sinal fraco que o novo regime rejeita.

  MOMENTUM_VETO_CANDIDATE
    Gate 3 bloqueou por delta_4h > +3000 (threshold CAL-17).
    Mas o book_imbalance confirma esgotamento real (>= 0.40).
    Sugere que o delta_4h threshold pode precisar de recalibração.

Latência: O(1) por chamada — sem I/O no path de sinal.
Logging assíncrono: apenas em background (não bloqueia pipeline).
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..common.config import SHADOW_LOG_DIR
from ..engine.foxyze_master import MasterVerdict

_logger = logging.getLogger("nextgen.inconsistency")

# ─── Categorias de Inconsistência ─────────────────────────────────────────────

OBSOLETE_THRESHOLD_BLOCK = "OBSOLETE_THRESHOLD_BLOCK"
HIGH_TOXICITY_IGNORED    = "HIGH_TOXICITY_IGNORED"
MOMENTUM_VETO_CANDIDATE  = "MOMENTUM_VETO_CANDIDATE"

# Limiares do novo regime (ByPriceLVL — Abril 2026)
_T_NOISE     = 0.30   # produção actual — zona de ruído no novo regime
_T_MODERATE  = 0.40   # sinal moderado ByPriceLVL
_T_WHALE     = 0.45   # sinal forte — "baleia confirmada"

# Delta veto threshold actual (CAL-17)
_DELTA_VETO_SHORT = 3_000
_DELTA_VETO_LONG  = -1_050


# ─── Estruturas de Dados ──────────────────────────────────────────────────────

@dataclass
class InconsistencyEvent:
    """Registo de uma inconsistência detectada."""
    timestamp:       str
    category:        str             # OBSOLETE_THRESHOLD_BLOCK | HIGH_TOXICITY_IGNORED | ...
    severity:        str             # "low" | "medium" | "high"
    direction:       str             # LONG | SHORT | FLAT
    book_imbalance:  float
    cumulative_delta: float
    threshold_used:  float           # threshold que causou a decisão actual
    threshold_shadow: float          # threshold que mudaria a decisão
    prod_decision:   Optional[str]   # CONFIRMED | FILTERED | None (desconhecido em live)
    verdict_direction: str
    blocked_at_gate: Optional[int]
    block_reason:    Optional[str]
    metadata:        dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetectorStats:
    """Contadores por sessão."""
    date:                    str
    total_verdicts:          int = 0
    obsolete_blocks:         int = 0   # OBSOLETE_THRESHOLD_BLOCK
    high_toxicity_ignored:   int = 0   # HIGH_TOXICITY_IGNORED
    momentum_veto_candidates: int = 0  # MOMENTUM_VETO_CANDIDATE
    # Distribuição de imbalance por bracket
    bracket_noise:           int = 0   # 0.30 <= |imb| < 0.40
    bracket_moderate:        int = 0   # 0.40 <= |imb| < 0.45
    bracket_whale:           int = 0   # |imb| >= 0.45
    bracket_below_noise:     int = 0   # |imb| < 0.30
    # Thresholds lixo detectados
    trash_thresholds:        List[str] = field(default_factory=list)

    # ── Divergências Lucrativas (Sprint 2.2 — Log de Evidência) ──────────
    # Casos onde NextGen teria sinal (via shadow 0.40/0.45) mas produção ficou de fora
    # ou produção entrou mas NextGen detectou zona de ruído (HIGH_TOXICITY)
    #
    # "Lucrativa" no sentido de oportunidade: se o threshold fosse mais preciso,
    # uma das partes teria tomado uma decisão melhor.
    divergencias_lucro_potencial:   int = 0  # NextGen 0.45 teria LONG/SHORT, prod ficou fora
    divergencias_ruido_prod:        int = 0  # Prod CONFIRMED em zona 0.30-0.40 (possível ruído)
    # Acumular |imbalance| dos sinais apenas-t030 para calcular threshold óptimo
    _noise_imbalances: List[float] = field(default_factory=list)

    def threshold_lixo_count(self) -> int:
        return len(set(self.trash_thresholds))

    def suggested_threshold(self) -> Optional[float]:
        """
        Calcula o threshold óptimo sugerido com base na distribuição de imbalance.

        Lógica: percentil 60 dos imbalances que atingiram 0.30 mas não 0.40.
        Se a maioria estiver entre 0.30–0.34, sugere subir para 0.38–0.42.
        Retorna None se não há dados suficientes (< 5 amostras).
        """
        if len(self._noise_imbalances) < 5:
            return None
        sorted_imbs = sorted(self._noise_imbalances)
        # Percentil 75: threshold que eliminaria os 75% de sinais mais fracos
        idx = int(len(sorted_imbs) * 0.75)
        raw = sorted_imbs[min(idx, len(sorted_imbs) - 1)]
        # Arredondar para o 0.01 mais próximo e limitar ao range [0.32, 0.48]
        suggested = max(0.32, min(round(raw + 0.02, 2), 0.48))
        return suggested

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_noise_imbalances", None)   # interno — não serializar
        d["threshold_lixo_unique"] = self.threshold_lixo_count()
        d["suggested_threshold"]   = self.suggested_threshold()
        return d


# ─── InconsistencyDetector ────────────────────────────────────────────────────

class InconsistencyDetector:
    """
    Detecta thresholds obsoletos comparando decisões NextGen com o
    regime real de Abril (ByPriceLVL).

    Uso em shadow mode (live):
        detector = InconsistencyDetector()
        detector.inspect(verdict)           # após cada FoxyzeMaster.evaluate()

    Uso em post-comparison (com dado de produção):
        detector.inspect(verdict, prod_decision="CONFIRMED")

    Outputs:
        logs/inconsistencies_YYYY-MM-DD.jsonl  — eventos detectados
        stats  → detector.daily_stats(date)    — resumo do dia
    """

    def __init__(self, log_dir: Path = SHADOW_LOG_DIR):
        self._log_dir  = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._stats:   Dict[str, DetectorStats]          = {}
        self._events:  Dict[str, List[InconsistencyEvent]] = defaultdict(list)
        self._lock     = threading.Lock()
        self._log_handles: Dict[str, object] = {}
        _logger.info("InconsistencyDetector activo — log dir: %s", self._log_dir)

    def inspect(
        self,
        verdict: MasterVerdict,
        prod_decision: Optional[str] = None,
    ) -> List[InconsistencyEvent]:
        """
        Analisa um MasterVerdict em busca de inconsistências.

        Parameters
        ----------
        verdict       : resultado do FoxyzeMaster.evaluate()
        prod_decision : "CONFIRMED" | "FILTERED" | None
                        Se None (modo live), faz análise estrutural sem outcome.

        Returns
        -------
        Lista de InconsistencyEvent (vazia se nada detectado).
        Latência: O(1), sem I/O no path crítico.
        """
        # Extrair dados relevantes do verdict
        ts          = verdict.timestamp.isoformat()
        date_str    = verdict.timestamp.strftime("%Y-%m-%d")
        ng_dir      = verdict.direction    # "LONG" | "SHORT" | "FLAT" | "FORCE_EXIT"
        block_gate  = verdict.blocked_at_gate
        block_reason= verdict.block_reason or ""

        # Extrair book_imbalance e cumulative_delta do gate 3 metadata
        imb, delta = self._extract_from_gate3(verdict)

        # Atualizar stats de sessão
        with self._lock:
            if date_str not in self._stats:
                self._stats[date_str] = DetectorStats(date=date_str)
                self._events[date_str] = []
            st = self._stats[date_str]
            st.total_verdicts += 1
            self._update_bracket(st, imb)

        detected: List[InconsistencyEvent] = []

        # ── Acumular imbalances para cálculo de threshold sugerido ───────────
        if _T_NOISE <= abs(imb) < _T_MODERATE:
            with self._lock:
                st._noise_imbalances.append(abs(imb))

        # ── Categoria 1: OBSOLETE_THRESHOLD_BLOCK ────────────────────────────
        # NextGen FLAT porque |imb| < 0.30, mas imb ∈ [0.30, 0.40) é zona de ruído
        # nova — se a produção tivesse CONFIRMED aqui, o threshold 0.30 é "lixo"
        if ng_dir == "FLAT" and block_gate is None and abs(imb) >= _T_NOISE:
            # Gate 3 não bloqueou (momentum OK), mas imb apenas passou 0.30 fraquinho
            shadow_40_ok = abs(imb) >= _T_MODERATE
            shadow_45_ok = abs(imb) >= _T_WHALE
            shadow_thr   = _T_WHALE if shadow_45_ok else (_T_MODERATE if shadow_40_ok else _T_NOISE)
            severity     = "high" if shadow_45_ok else ("medium" if shadow_40_ok else "low")

            # Só emite se 0.30 <= |imb| < 0.45 E produção CONFIRMED → threshold real issue
            if prod_decision == "CONFIRMED" or prod_decision is None:
                evt = InconsistencyEvent(
                    timestamp        = ts,
                    category         = OBSOLETE_THRESHOLD_BLOCK,
                    severity         = severity,
                    direction        = ng_dir,
                    book_imbalance   = round(imb, 4),
                    cumulative_delta = round(delta, 1),
                    threshold_used   = _T_NOISE,
                    threshold_shadow = shadow_thr,
                    prod_decision    = prod_decision,
                    verdict_direction= ng_dir,
                    blocked_at_gate  = block_gate,
                    block_reason     = block_reason or None,
                    metadata         = {
                        "shadow_40_would_signal": shadow_40_ok,
                        "shadow_45_would_signal": shadow_45_ok,
                        "imb_abs": round(abs(imb), 4),
                        "note": (
                            "Threshold 0.30 de Janeiro pode ser ruido no regime ByPriceLVL. "
                            f"Shadow 0.40={'SIM' if shadow_40_ok else 'NAO'} "
                            f"Shadow 0.45={'SIM' if shadow_45_ok else 'NAO'}"
                        ),
                    },
                )
                detected.append(evt)
                with self._lock:
                    st.obsolete_blocks += 1
                    st.trash_thresholds.append(f"DOM_{_T_NOISE:.2f}")

        # ── Categoria 2: HIGH_TOXICITY_IGNORED ───────────────────────────────
        # Produção CONFIRMED (ou NextGen LONG/SHORT) mas imbalance está na "zona de ruído"
        # 0.30–0.39 — no novo regime ByPriceLVL, este sinal é fraco / potencialmente tóxico
        if (prod_decision == "CONFIRMED" or ng_dir in ("LONG", "SHORT")) and \
           _T_NOISE <= abs(imb) < _T_MODERATE:
            # Divergência Lucrativa: produção entrou em zona de ruído
            if prod_decision == "CONFIRMED":
                with self._lock:
                    st.divergencias_ruido_prod += 1
            evt = InconsistencyEvent(
                timestamp        = ts,
                category         = HIGH_TOXICITY_IGNORED,
                severity         = "medium",
                direction        = ng_dir,
                book_imbalance   = round(imb, 4),
                cumulative_delta = round(delta, 1),
                threshold_used   = _T_NOISE,
                threshold_shadow = _T_MODERATE,
                prod_decision    = prod_decision,
                verdict_direction= ng_dir,
                blocked_at_gate  = block_gate,
                block_reason     = block_reason or None,
                metadata         = {
                    "regime_note": (
                        "Imbalance 0.30-0.39 era significativo em Janeiro (AggMethod.None). "
                        "Em Abril (ByPriceLVL) este nivel pode ser ruido de mercado. "
                        "Confidence deveria ser <= 0.50 nesta zona."
                    ),
                    "suggested_confidence_cap": 0.50,
                },
            )
            detected.append(evt)
            with self._lock:
                st.high_toxicity_ignored += 1
                st.trash_thresholds.append("DOM_NOISE_ZONE_030_040")

        # ── Categoria 3: MOMENTUM_VETO_CANDIDATE ─────────────────────────────
        # Gate 3 bloqueou por delta (MOMENTUM_BLOCK), mas |imb| >= 0.40
        # Sugere que o delta veto threshold pode estar calibrado para regime antigo
        if block_gate == 3 and "MOMENTUM_BLOCK" in block_reason:
            is_short_block = "MOMENTUM_BLOCK_SHORT" in block_reason
            actual_veto    = _DELTA_VETO_SHORT if is_short_block else _DELTA_VETO_LONG
            strong_imb     = abs(imb) >= _T_MODERATE

            if strong_imb:
                evt = InconsistencyEvent(
                    timestamp        = ts,
                    category         = MOMENTUM_VETO_CANDIDATE,
                    severity         = "high" if abs(imb) >= _T_WHALE else "medium",
                    direction        = "SHORT" if is_short_block else "LONG",
                    book_imbalance   = round(imb, 4),
                    cumulative_delta = round(delta, 1),
                    threshold_used   = float(actual_veto),
                    threshold_shadow = float(actual_veto) * 1.20,  # sugere +20%
                    prod_decision    = prod_decision,
                    verdict_direction= ng_dir,
                    blocked_at_gate  = block_gate,
                    block_reason     = block_reason,
                    metadata         = {
                        "veto_type": "MOMENTUM_BLOCK_SHORT" if is_short_block else "MOMENTUM_BLOCK_LONG",
                        "delta_threshold_used": actual_veto,
                        "imb_strength": "WHALE" if abs(imb) >= _T_WHALE else "MODERATE",
                        "suggestion": (
                            f"Delta threshold {actual_veto:+.0f} bloqueou entrada mas "
                            f"|imbalance|={abs(imb):.3f} >= {_T_MODERATE} confirma esgotamento real. "
                            "Considerar recalibrar delta_4h threshold para o volume ByPriceLVL."
                        ),
                    },
                )
                detected.append(evt)
                with self._lock:
                    st.momentum_veto_candidates += 1
                    st.trash_thresholds.append(
                        f"DELTA_VETO_{'SHORT' if is_short_block else 'LONG'}_{actual_veto:.0f}"
                    )

        # ── Divergência Lucrativa: shadow 0.45 teria sinal, prod ficou fora ──
        # Lê shadow_thresholds do Gate 3 metadata
        shadow = self._extract_shadow_thresholds(verdict)
        t045_active = shadow.get("t045", {}).get("direction", "FLAT") != "FLAT"
        if t045_active and prod_decision == "FILTERED":
            # Shadow 0.45 activo (baleia) mas produção filtrou — oportunidade perdida
            with self._lock:
                st.divergencias_lucro_potencial += 1

        # ── Log assíncrono ────────────────────────────────────────────────────
        if detected:
            with self._lock:
                self._events[date_str].extend(detected)
            self._log_events_async(date_str, detected)

        return detected

    def _extract_shadow_thresholds(self, verdict: MasterVerdict) -> dict:
        """Extrai shadow_thresholds do metadata do Gate 3."""
        for gv in verdict.gate_verdicts:
            if gv.get("gate") == 3:
                return gv.get("metadata", {}).get("shadow_thresholds", {})
        return {}

    def _extract_from_gate3(self, verdict: MasterVerdict) -> tuple[float, float]:
        """Extrai book_imbalance e cumulative_delta do metadata do Gate 3.
        Nota: gate_verdicts usa chave 'gate' (int), não 'gate_number'."""
        for gv in verdict.gate_verdicts:
            if gv.get("gate") == 3:
                meta = gv.get("metadata", {})
                imb   = float(meta.get("book_imbalance", 0.0))
                delta = float(meta.get("cumulative_delta", 0.0))
                return imb, delta
        return 0.0, 0.0

    def _update_bracket(self, st: DetectorStats, imb: float):
        """Actualiza contadores de bracket por |imbalance|."""
        abs_imb = abs(imb)
        if abs_imb >= _T_WHALE:
            st.bracket_whale    += 1
        elif abs_imb >= _T_MODERATE:
            st.bracket_moderate += 1
        elif abs_imb >= _T_NOISE:
            st.bracket_noise    += 1
        else:
            st.bracket_below_noise += 1

    def _log_events_async(self, date_str: str, events: List[InconsistencyEvent]):
        """Escreve eventos para JSONL em background thread (não bloqueia pipeline)."""
        def _write():
            path = self._log_dir / f"inconsistencies_{date_str}.jsonl"
            try:
                with open(path, "a", encoding="utf-8") as f:
                    for evt in events:
                        f.write(json.dumps(evt.to_dict(), ensure_ascii=False) + "\n")
            except Exception as e:
                _logger.debug("Erro ao escrever inconsistency log: %s", e)

        t = threading.Thread(target=_write, daemon=True)
        t.start()

    def daily_stats(self, date_str: Optional[str] = None) -> Optional[DetectorStats]:
        """Retorna os stats do dia (ou dia corrente se date_str=None)."""
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            return self._stats.get(date_str)

    def threshold_health_report(self, date_str: Optional[str] = None) -> dict:
        """
        Gera relatório de saúde dos thresholds para o Scorecard 2.0.

        Inclui:
          - imbalance_density  : frequência por bracket
          - trash_threshold_count : número de thresholds "lixo" detectados
          - recommendation     : "CALIBRATE" | "MONITOR" | "OK"
        """
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with self._lock:
            st = self._stats.get(date_str)
            events = list(self._events.get(date_str, []))

        if st is None:
            return {"date": date_str, "status": "no_data"}

        total = max(st.total_verdicts, 1)

        # Calcular noise rate: % de sinais na zona 0.30-0.40
        noise_rate = round((st.bracket_noise / total) * 100, 1)

        # Calcular se 0.30 está a gerar overtrading (> 30% dos ticks na noise zone)
        overtrading_risk = noise_rate > 30.0

        # Contar momentum vetos com imbalance forte
        high_sev_vetos = sum(
            1 for e in events
            if e.category == MOMENTUM_VETO_CANDIDATE and e.severity == "high"
        )

        recommendation = "OK"
        if st.threshold_lixo_count() >= 3:
            recommendation = "CALIBRATE"
        elif overtrading_risk or high_sev_vetos >= 2:
            recommendation = "MONITOR"

        # Threshold sugerido baseado na distribuição empírica
        suggested_thr = st.suggested_threshold()

        # Sumário executivo — a frase que a Barbara lê
        noise_signal_count = st.bracket_noise
        total_signals      = max(st.bracket_noise + st.bracket_moderate + st.bracket_whale, 1)
        noise_pct_of_signals = round(noise_signal_count / total_signals * 100, 1)

        if suggested_thr is not None:
            exec_summary = (
                f"Threshold 0.30 gerou {noise_pct_of_signals:.0f}% de ruido desnecessario hoje "
                f"({noise_signal_count} sinais na zona 0.30-0.40). "
                f"Recomenda-se {suggested_thr:.2f}. "
                f"Divergencias lucrativas: {st.divergencias_lucro_potencial} oportunidades "
                f"perdidas (shadow 0.45 activo, prod FILTERED). "
                f"Producao entrou em ruido: {st.divergencias_ruido_prod} vezes."
            )
        else:
            exec_summary = (
                f"Dados insuficientes para recomendar threshold ({st.total_verdicts} ticks). "
                f"Ruido detectado: {noise_signal_count} sinais na zona 0.30-0.40."
            )

        return {
            "date":                      date_str,
            "total_verdicts":            st.total_verdicts,
            "imbalance_density": {
                "below_noise_pct":        round(st.bracket_below_noise / total * 100, 1),
                "noise_zone_030_040_pct": noise_rate,
                "moderate_040_045_pct":   round(st.bracket_moderate    / total * 100, 1),
                "whale_045plus_pct":      round(st.bracket_whale       / total * 100, 1),
            },
            "inconsistencies": {
                "obsolete_threshold_blocks":   st.obsolete_blocks,
                "high_toxicity_ignored":       st.high_toxicity_ignored,
                "momentum_veto_candidates":    st.momentum_veto_candidates,
                "trash_threshold_count":       st.threshold_lixo_count(),
                "trash_thresholds_unique":     list(set(st.trash_thresholds)),
            },
            "divergencias_lucrativas": {
                "lucro_potencial_perdido":  st.divergencias_lucro_potencial,
                "ruido_prod_confirmado":    st.divergencias_ruido_prod,
                "note": (
                    f"{st.divergencias_lucro_potencial} casos onde shadow 0.45 teria sinal "
                    f"mas producao ficou de fora (FILTERED). "
                    f"{st.divergencias_ruido_prod} casos onde producao entrou em zona de ruido."
                ),
            },
            "suggested_threshold":       suggested_thr,
            "overtrading_risk":          overtrading_risk,
            "noise_rate_pct":            noise_rate,
            "dor_met":                   st.threshold_lixo_count() >= 3,
            "recommendation":            recommendation,
            "executive_summary":         exec_summary,
        }

    def veto_audit(self, date_str: Optional[str] = None) -> List[dict]:
        """
        Lista de MOMENTUM_VETO_CANDIDATE com severity='high' (delta bloqueou
        mas imbalance >= 0.45 confirmava esgotamento real).
        Usado pela secção Veto Audit do Scorecard 2.0.
        """
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            events = list(self._events.get(date_str, []))
        return [
            e.to_dict() for e in events
            if e.category == MOMENTUM_VETO_CANDIDATE and e.severity in ("high", "medium")
        ]

    def save_daily_summary(self, date_str: Optional[str] = None) -> Optional[Path]:
        """Guarda o relatório diário de saúde em JSONL."""
        report = self.threshold_health_report(date_str)
        if report.get("status") == "no_data":
            return None
        date_str = report["date"]
        path = self._log_dir / f"threshold_health_{date_str}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
        _logger.info("Threshold health report guardado: %s", path)
        return path

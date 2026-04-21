"""
FluxSignalEngine — Gate 3: PRIMARY DIRECTION (Sprint 2.2 Adaptive Calibration Monitor)

Sprint 2.1 — Migração Cirúrgica: lógica exacta de produção (CAL-14/16/17).
Sprint 2.2 — Adaptive Calibration Monitor: Shadow Thresholds paralelos.

Decisão primária (isomórfica com produção):
    1. Momentum gate (delta proxy): HARD VETO se delta fortemente contra direcção
    2. DOM Imbalance ALPHA: determina direcção (imb > +0.30 → SHORT, < -0.30 → LONG)
    3. Score = momentum_score + delta_bonus → GO se score ≥ 0

Shadow Thresholds (Sprint 2.2 — Parallel Calculation):
    T030: threshold 0.30 (produção actual — Janeiro, AggMethod.None calibration)
    T040: threshold 0.40 (regime moderado ByPriceLVL — Abril)
    T045: threshold 0.45 (sinal forte — "baleia confirmada" ByPriceLVL)

Confidence Scoring calibrado para ByPriceLVL:
    0.30 ≤ |imb| < 0.40 → "zona de ruído no novo regime" → conf ≤ 0.50
    0.40 ≤ |imb| < 0.45 → "sinal moderado"              → conf 0.50–0.80
    |imb| ≥ 0.45        → "baleia confirmada"            → conf 0.80–1.00

Mapeamento produção → NextGen:
    delta_4h   → tick.cumulative_delta  (sessão na watchdog, macro na replay)
    MOMENTUM_BLOCK_SHORT = +3000        [ATSLiveGate line 163, CAL-17]
    MOMENTUM_BLOCK_LONG  = -1050        [ATSLiveGate line 162, CAL-16]
    MOMENTUM_WARN_SHORT  = +800         [ATSLiveGate line 165]
    MOMENTUM_WARN_LONG   = -800         [ATSLiveGate line 164]
    W_MOMENTUM_BONUS     = +2           [ATSLiveGate line 185]
    MIN_SCORE_GO         = 0            [ATSLiveGate line 191]
    DOM_THRESHOLD        = ±0.30        [CAL-14, event_processor.py]
"""

from __future__ import annotations

import logging
from typing import Optional

from ...common.base_provider import BaseProvider, Direction, MarketTick, ProviderVerdict, VetoStatus
from ...common.config import DEFAULT_ENGINE_RISK_CONFIG

_logger = logging.getLogger("nextgen.flux_signal")

# ─── Thresholds (produção-exactos, calibrados CAL-14/16/17) ──────────────────

# Momentum hard-blocks — do ATSLiveGate (via _check_pre_entry_gates e MicrostructureReader)
MOMENTUM_BLOCK_SHORT = +3_000   # delta > +3000 → HARD_VETO SHORT  [CAL-17]
MOMENTUM_BLOCK_LONG  = -1_050   # delta < -1050 → HARD_VETO LONG   [CAL-16]
MOMENTUM_WARN_SHORT  = +800     # delta > +800 → score penalizado
MOMENTUM_WARN_LONG   = -800     # delta < -800 → score penalizado

# Weights (do ATSLiveGate)
W_MOMENTUM_BONUS     = 2        # delta alinhado → +2
W_MOMENTUM_WARN      = -1       # delta contra mas dentro de warn → -1
W_MOMENTUM_BLOCK_PEN = 2        # hard-block: score equiv. = -W_MOMENTUM_BONUS = -2

# DOM thresholds (CAL-14 / event_processor: _check_pre_entry_gates, book_imbalance)
DOM_SHORT_THRESHOLD  = +0.30    # book buyer-heavy → buyers esgotados → SHORT
DOM_LONG_THRESHOLD   = -0.30    # book seller-heavy → sellers esgotados → LONG

# Gate threshold — score >= 0 → GO (produção: MIN_SCORE_GO=0)
MIN_SCORE_GO         = 0

# Confidence mínima para emitir sinal (config global)
_MIN_CONFIDENCE      = DEFAULT_ENGINE_RISK_CONFIG["min_direction_confidence"]

# Delta "extremo" para impulse-soft-veto (score penalty, não hard-block)
# Nível abaixo de WARN para mercado muito direccional mas sem DOM confirmação
_IMPULSE_SOFT_SHORT  = +1_500   # entre warn e hard-block: penalidade extra
_IMPULSE_SOFT_LONG   = -500

# ─── Shadow Thresholds (Sprint 2.2 — Adaptive Calibration Monitor) ───────────
# Calculados em paralelo ao sinal primário — NÃO alteram a decisão final.
# Servem como auditoria: "o sinal seria diferente com threshold X?"

_SHADOW_THRESHOLDS = [0.30, 0.40, 0.45]   # [produção, moderado, baleia]

# Confidence calibrada para regime ByPriceLVL (Abril 2026):
#   0.30–0.40 → zona de ruído (conf cap 0.50) — era sinal em Janeiro, agora possível ruído
#   0.40–0.45 → sinal moderado (conf 0.50–0.80)
#   0.45+     → baleia confirmada (conf 0.80–1.00)
_CONF_NOISE_CAP    = 0.50   # cap de confiança na noise zone (0.30–0.40)
_CONF_MODERATE_MIN = 0.50   # conf mínima para sinal moderado (0.40–0.45)
_CONF_WHALE_MIN    = 0.80   # conf mínima para baleia confirmada (0.45+)


class FluxSignalEngineProvider(BaseProvider):
    """
    Gate 3 — Flux Signal Engine (Sprint 2.1 Isomorphism).

    Replica a lógica de decisão de produção extraída de:
        - event_processor._check_pre_entry_gates()  → momentum hard-blocks
        - ATSLiveGate.get_momentum_signal()          → score momentum
        - ATSLiveGate.check()                        → MIN_SCORE_GO

    Estratégia ALPHA (DOM Exhaustion — isomórfica com produção):
        book_imbalance < -0.30 → sellers esgotados → LONG
        book_imbalance > +0.30 → buyers esgotados  → SHORT
        |imbalance| < 0.30     → FLAT (sem sinal direcional)

    Estratégia GAMMA (Spread + Depth — amplificador):
        Spread < 0.3 e depth > 180 → mercado comprimido → +confidence

    Estratégia DELTA (Delta Divergence — amplificador):
        cumulative_delta bullish + book seller-heavy → LONG forte
        cumulative_delta bearish + book buyer-heavy  → SHORT forte

    Momentum Gate (HARD VETO):
        delta > MOMENTUM_BLOCK_SHORT (+3000) → FLAT (bloqueia SHORT)
        delta < MOMENTUM_BLOCK_LONG (-1050)  → FLAT (bloqueia LONG)

    Score final:
        score = momentum_score (0, ±1, ±2) + delta_bonus
        GO se score >= 0  (MIN_SCORE_GO=0)
        confidence = base_alpha_conf × (1 + score × 0.15)
    """

    GATE_NUMBER   = 3
    PROVIDER_NAME = "FluxSignalEngine"

    def __init__(
        self,
        dom_long_threshold:  float = DOM_LONG_THRESHOLD,
        dom_short_threshold: float = DOM_SHORT_THRESHOLD,
        momentum_block_short: float = MOMENTUM_BLOCK_SHORT,
        momentum_block_long:  float = MOMENTUM_BLOCK_LONG,
    ):
        self._dom_long            = dom_long_threshold
        self._dom_short           = dom_short_threshold
        self._mom_block_short     = momentum_block_short
        self._mom_block_long      = momentum_block_long

    def is_ready(self) -> bool:
        return True  # stateless — sempre pronto

    # ─── Momentum Gate (lógica exacta de ATSLiveGate.get_momentum_signal) ────

    def _momentum_gate(
        self, direction: Direction, delta: float
    ) -> tuple[str, int, Optional[str]]:
        """
        Avalia o momentum gate com a lógica exacta de produção.

        Returns
        -------
        (status, score, veto_reason)
            status  : "ok" | "warn" | "block"
            score   : +2 (bonus aligned) | -1 (warn) | -2 (block-pen) | 0
            veto_reason : None ou mensagem de block
        """
        if direction == Direction.SHORT:
            if delta > self._mom_block_short:
                return (
                    "block",
                    -W_MOMENTUM_BLOCK_PEN,  # = -2
                    f"MOMENTUM_BLOCK_SHORT: delta={delta:+.0f} > {self._mom_block_short:+.0f}",
                )
            elif delta > MOMENTUM_WARN_SHORT:
                return "warn", W_MOMENTUM_WARN, None
            else:
                bonus = W_MOMENTUM_BONUS if delta < -500 else 0
                return "ok", bonus, None

        elif direction == Direction.LONG:
            if delta < self._mom_block_long:
                return (
                    "block",
                    -W_MOMENTUM_BLOCK_PEN,  # = -2
                    f"MOMENTUM_BLOCK_LONG: delta={delta:+.0f} < {self._mom_block_long:+.0f}",
                )
            elif delta < MOMENTUM_WARN_LONG:
                return "warn", W_MOMENTUM_WARN, None
            else:
                bonus = W_MOMENTUM_BONUS if delta > 500 else 0
                return "ok", bonus, None

        return "ok", 0, None

    # ─── Shadow Thresholds (Sprint 2.2) ──────────────────────────────────────

    def _shadow_evaluation(self, tick: MarketTick) -> dict:
        """
        Calcula sinal paralelo para cada shadow threshold (0.30, 0.40, 0.45).

        Retorna dict para inclusão no metadata — NÃO influencia a decisão primária.

        Confidence calibrada para regime ByPriceLVL:
            0.30 ≤ |imb| < 0.40 → conf ≤ 0.50 (zona de ruído — era sinal em Jan)
            0.40 ≤ |imb| < 0.45 → conf 0.50–0.80 (sinal moderado)
            |imb| ≥ 0.45        → conf 0.80–1.00 (baleia confirmada)

        Exemplo: imb=0.32 → t030=conf_0.50(sinal fraco), t040=FLAT, t045=FLAT
                 imb=0.45 → t030=conf_1.00, t040=conf_1.00, t045=conf_0.80+
        """
        imb     = tick.book_imbalance
        abs_imb = abs(imb)
        results = {}

        for thr in _SHADOW_THRESHOLDS:
            key = f"t{int(thr * 100):03d}"   # "t030", "t040", "t045"
            if abs_imb < thr:
                results[key] = {"direction": "FLAT", "confidence": 0.0}
                continue

            direction = "SHORT" if imb > 0 else "LONG"

            # Confidence calibrada ao regime ByPriceLVL
            if abs_imb >= 0.45:
                # Baleia confirmada — escalamento 0.80 → 1.00 em [0.45, 0.65]
                strength = min((abs_imb - 0.45) / 0.20, 1.0)
                conf = _CONF_WHALE_MIN + (1.0 - _CONF_WHALE_MIN) * strength
            elif abs_imb >= 0.40:
                # Sinal moderado — escalamento 0.50 → 0.80 em [0.40, 0.45]
                strength = min((abs_imb - 0.40) / 0.05, 1.0)
                conf = _CONF_MODERATE_MIN + (_CONF_WHALE_MIN - _CONF_MODERATE_MIN) * strength
            else:
                # Zona de ruído 0.30–0.40 — conf cap 0.50 (era sinal em Janeiro)
                strength = min((abs_imb - 0.30) / 0.10, 1.0)
                conf = min(0.30 + 0.20 * strength, _CONF_NOISE_CAP)

            results[key] = {"direction": direction, "confidence": round(conf, 3)}

        # Adicionar análise de divergência: é o sinal 0.30 potencialmente overtrading?
        t030_signal = results.get("t030", {}).get("direction") != "FLAT"
        t040_signal = results.get("t040", {}).get("direction") != "FLAT"
        t045_signal = results.get("t045", {}).get("direction") != "FLAT"

        results["_analysis"] = {
            "only_t030":       t030_signal and not t040_signal,   # 0.30 sinaliza mas 0.40 não → possível ruído
            "confirmed_whale": t045_signal,                        # 0.45 activo → baleia confirmada
            "regime_label":    (
                "WHALE"    if t045_signal else
                "MODERATE" if t040_signal else
                "NOISE"    if t030_signal else
                "FLAT"
            ),
        }
        return results

    # ─── ALPHA: DOM Imbalance Exhaustion ─────────────────────────────────────

    def _alpha_signal(self, tick: MarketTick) -> tuple[Direction, float]:
        """
        ALPHA: DOM Imbalance Exhaustion (isomórfico com produção).
        threshold ±0.30 (CAL-14).

        Sprint 2.2: Confidence calibrada ao regime ByPriceLVL.
            0.30–0.40 → conf ≤ 0.50 (zona de ruído)
            0.40–0.45 → conf 0.50–0.80 (sinal moderado)
            0.45+     → conf 0.80–1.00 (baleia confirmada)
        """
        imb     = tick.book_imbalance
        abs_imb = abs(imb)

        if imb < self._dom_long or imb > self._dom_short:
            direction = Direction.LONG if imb < self._dom_long else Direction.SHORT

            # Confidence calibrada ao regime ByPriceLVL (Sprint 2.2)
            if abs_imb >= 0.45:
                strength = min((abs_imb - 0.45) / 0.20, 1.0)
                conf = _CONF_WHALE_MIN + (1.0 - _CONF_WHALE_MIN) * strength
            elif abs_imb >= 0.40:
                strength = min((abs_imb - 0.40) / 0.05, 1.0)
                conf = _CONF_MODERATE_MIN + (_CONF_WHALE_MIN - _CONF_MODERATE_MIN) * strength
            else:
                # Zona de ruído (0.30–0.40): cap 0.50
                strength = min((abs_imb - 0.30) / 0.10, 1.0)
                conf = min(0.30 + 0.20 * strength, _CONF_NOISE_CAP)

            return direction, round(conf, 4)

        return Direction.FLAT, 0.0

    # ─── GAMMA: Spread Compression Amplifier ─────────────────────────────────

    def _gamma_signal(self, tick: MarketTick) -> float:
        """GAMMA: Spread comprimido + depth alta → +confidence (amplificador)."""
        if tick.spread <= 0:
            return 0.0
        depth_total = tick.total_bid_depth + tick.total_ask_depth
        if tick.spread < 0.3 and depth_total > 180:
            return 0.10
        return 0.0

    # ─── DELTA: Cumulative Delta Divergence Amplifier ────────────────────────

    def _delta_bonus(self, direction: Direction, tick: MarketTick) -> float:
        """
        DELTA: Confluência delta + DOM → amplifica confiança se concordantes.
        Reduz confiança se contra-direcção.
        """
        cd  = tick.cumulative_delta
        imb = tick.book_imbalance
        if direction == Direction.LONG:
            if cd > 500 and imb < self._dom_long:
                return +0.20   # bullish delta + seller-heavy DOM = LONG confluência
            elif cd < -500:
                return -0.10   # delta contra LONG
        elif direction == Direction.SHORT:
            if cd < -500 and imb > self._dom_short:
                return +0.20   # bearish delta + buyer-heavy DOM = SHORT confluência
            elif cd > 500:
                return -0.10   # delta contra SHORT
        return 0.0

    # ─── evaluate ─────────────────────────────────────────────────────────────

    def evaluate(self, tick: MarketTick) -> ProviderVerdict:
        delta = tick.cumulative_delta

        # ── Shadow Thresholds (Sprint 2.2) — calculados SEMPRE, sem custo ────
        shadow = self._shadow_evaluation(tick)

        # ── 1. ALPHA direction ───────────────────────────────────────────────
        alpha_dir, alpha_conf = self._alpha_signal(tick)

        if alpha_dir == Direction.FLAT:
            return ProviderVerdict(
                provider_name    = self.PROVIDER_NAME,
                gate_number      = self.GATE_NUMBER,
                direction        = Direction.FLAT,
                confidence_score = 0.0,
                veto_status      = VetoStatus.CLEAR,
                size_multiplier  = 1.0,
                metadata         = {
                    "strategy":         "ALPHA",
                    "book_imbalance":   round(tick.book_imbalance, 4),
                    "cumulative_delta": round(delta, 0),
                    "reason":           "imbalance within neutral zone (±0.30)",
                    "shadow_thresholds": shadow,
                },
                is_stub = False,
            )

        # ── 2. Momentum gate (HARD VETO — lógica exacta de produção) ────────
        mom_status, mom_score, veto_reason = self._momentum_gate(alpha_dir, delta)

        if mom_status == "block":
            _logger.info(
                "FluxSignalEngine: MOMENTUM_BLOCK %s | %s | imb=%.4f",
                alpha_dir, veto_reason, tick.book_imbalance,
            )
            return ProviderVerdict(
                provider_name    = self.PROVIDER_NAME,
                gate_number      = self.GATE_NUMBER,
                direction        = Direction.FLAT,
                confidence_score = 0.0,
                veto_status      = VetoStatus.HARD_VETO,
                size_multiplier  = 0.0,
                metadata         = {
                    "strategy":          "ALPHA+MOMENTUM",
                    "momentum_status":   "block",
                    "veto_reason":       veto_reason,
                    "book_imbalance":    round(tick.book_imbalance, 4),
                    "cumulative_delta":  round(delta, 0),
                    "alpha_direction":   str(alpha_dir),
                    "shadow_thresholds": shadow,
                },
                is_stub = False,
            )

        # ── 3. Score: momentum + delta bonus ─────────────────────────────────
        gamma_bonus  = self._gamma_signal(tick)
        delta_bonus  = self._delta_bonus(alpha_dir, tick)

        # Confidence ajustada pelo momentum score
        score_adj  = mom_score * 0.15
        confidence = min(max(alpha_conf + score_adj + gamma_bonus + delta_bonus, 0.0), 1.0)

        if confidence < _MIN_CONFIDENCE:
            _logger.debug(
                "FluxSignalEngine: confidence %.3f < min %.3f → FLAT | imb=%.4f delta=%.0f",
                confidence, _MIN_CONFIDENCE, tick.book_imbalance, delta,
            )
            return ProviderVerdict(
                provider_name    = self.PROVIDER_NAME,
                gate_number      = self.GATE_NUMBER,
                direction        = Direction.FLAT,
                confidence_score = round(confidence, 4),
                veto_status      = VetoStatus.CLEAR,
                size_multiplier  = 1.0,
                metadata         = {
                    "strategy":          "ALPHA",
                    "momentum_status":   mom_status,
                    "book_imbalance":    round(tick.book_imbalance, 4),
                    "cumulative_delta":  round(delta, 0),
                    "reason":            f"score insuficiente (conf={confidence:.3f} < {_MIN_CONFIDENCE})",
                    "shadow_thresholds": shadow,
                },
                is_stub = False,
            )

        # ── 4. Regime label para log ─────────────────────────────────────────
        regime_label = shadow.get("_analysis", {}).get("regime_label", "UNKNOWN")
        _logger.debug(
            "FluxSignalEngine: %s conf=%.3f regime=%s | imb=%.4f delta=%.0f mom=%s",
            alpha_dir, confidence, regime_label, tick.book_imbalance, delta, mom_status,
        )

        return ProviderVerdict(
            provider_name    = self.PROVIDER_NAME,
            gate_number      = self.GATE_NUMBER,
            direction        = alpha_dir,
            confidence_score = round(confidence, 4),
            veto_status      = VetoStatus.SOFT_VETO if mom_status == "warn" else VetoStatus.CLEAR,
            size_multiplier  = 0.75 if mom_status == "warn" else 1.0,
            metadata         = {
                "strategy":          "ALPHA+MOMENTUM",
                "momentum_status":   mom_status,
                "momentum_score":    mom_score,
                "alpha_conf":        round(alpha_conf, 3),
                "gamma_bonus":       round(gamma_bonus, 3),
                "delta_bonus":       round(delta_bonus, 3),
                "book_imbalance":    round(tick.book_imbalance, 4),
                "cumulative_delta":  round(delta, 0),
                "regime_label":      regime_label,   # NOISE | MODERATE | WHALE | FLAT
                "shadow_thresholds": shadow,
            },
            is_stub = False,
        )

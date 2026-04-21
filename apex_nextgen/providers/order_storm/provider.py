"""
OrderStorm — Gate 4: ICEBERG CONFLUENCE (Sprint 2.2 Refill Feature Bridge)

Role: Amplificar ou reduzir tamanho baseado em actividade iceberg.
Retorna: size_multiplier (1.30 aligned / 1.00 neutral / 0.75 opposed).

Sprint 2.2 — Refill Feature Bridge:
    Captura features de Refill (F13-F18 do SCHEMA_FLUXFOX_V2) de tick.fluxfox_features.
    Regista observações quando refills significativos são ignorados pela produção.
    Prepara terreno para ml_iceberg_v2 (99.48% accuracy) na Fase B.

Implementação:
    Fase A (agora): proxy score via tick.iceberg_proxy_score + refill bridge F13-F18
    Fase B (Sprint 4): ml_iceberg_v2 modelo .pt quando disponível

Convergência com CAL-20: iceberg_proxy_threshold = 0.85 (sweet spot).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ...common.base_provider import BaseProvider, Direction, MarketTick, ProviderVerdict, VetoStatus
from ...common.config import DEFAULT_ENGINE_RISK_CONFIG, ICEBERG_MODEL_PATH

_logger = logging.getLogger("nextgen.order_storm")

# Thresholds calibrados (CAL-20: T=0.85 sweet spot, PF=1.118)
_ICEBERG_THRESHOLD = 0.85   # score >= 0.85 → iceberg activo
_STRONG_THRESHOLD  = 0.93   # score >= 0.93 → iceberg forte

# ─── Refill Feature Bridge (Sprint 2.2 — SCHEMA_FLUXFOX_V2 F13-F18) ─────────
# F13: refill_count          — número de refills detectados na janela
# F14: refill_interval_ms    — intervalo médio entre refills (ms)
# F15: refill_velocity       — refills por segundo
# F16: refill_size_ratio     — tamanho médio do refill / tamanho inicial
# F17: chain_duration_ms     — duração total da cadeia iceberg (ms)
# F18: large_order_imbalance — LOI no nível de entrada (bid-heavy ou ask-heavy)

_REFILL_FEATURES = [
    "refill_count",
    "refill_interval_ms",
    "refill_velocity",
    "refill_size_ratio",
    "chain_duration_ms",
    "large_order_imbalance",
]

# Limiar de refill significativo (produção ignoraria mas NextGen regista)
_REFILL_SIGNIFICANT_COUNT = 2    # >= 2 refills = cadeia iceberg real
_REFILL_SIGNIFICANT_LOT   = 400  # >= 400 lotes = baleia
_LOI_SIGNIFICANT           = 0.50  # large_order_imbalance >= 0.50


class OrderStormProvider(BaseProvider):
    """
    Gate 4 — OrderStorm.

    Lógica de confluência:
        Iceberg activo no mesmo lado da direcção → aligned  → size × 1.30
        Iceberg activo no lado oposto            → opposed  → size × 0.75
        Sem iceberg activo (score < threshold)   → neutral  → size × 1.00

    Fase A (proxy score):
        tick.iceberg_proxy_score ∈ [0,1] — calculado pelo event_processor.py de produção
        tick.iceberg_side: "BUY" | "SELL" | None

    Refill Bridge (Sprint 2.2):
        tick.fluxfox_features: dict com F13-F18 do SCHEMA_FLUXFOX_V2
        Regista observação quando refill_count >= 2 E iceberg_proxy_score < threshold.

    Fase B (ml_iceberg_v2):
        Modelo PyTorch disponível em ICEBERG_MODEL_PATH (Sprint 4).
    """

    GATE_NUMBER   = 4
    PROVIDER_NAME = "OrderStorm"

    def __init__(
        self,
        iceberg_threshold: float = _ICEBERG_THRESHOLD,
        strong_threshold:  float = _STRONG_THRESHOLD,
    ):
        self._threshold        = iceberg_threshold
        self._strong_threshold = strong_threshold
        self._mults            = DEFAULT_ENGINE_RISK_CONFIG["iceberg_multipliers"]
        self._ml_model         = None
        self._ml_loaded        = False
        self._try_load_model()

    def _try_load_model(self):
        """Fase B: tenta carregar ml_iceberg_v2. Silencioso se não disponível."""
        if not ICEBERG_MODEL_PATH.exists():
            _logger.info(
                "OrderStorm: ml_iceberg_v2 nao disponivel — modo proxy score activo. "
                "Depositar modelo em: %s", ICEBERG_MODEL_PATH
            )
            return
        try:
            import torch
            self._ml_model  = torch.load(str(ICEBERG_MODEL_PATH), map_location="cpu")
            self._ml_loaded = True
            _logger.info("OrderStorm: ml_iceberg_v2 carregado — modo ML activo")
        except Exception as e:
            _logger.warning("OrderStorm: falha ao carregar ml_iceberg_v2 — %s", e)

    def is_ready(self) -> bool:
        return True  # proxy score sempre disponível

    # ─── Refill Feature Bridge (Sprint 2.2) ──────────────────────────────────

    def _extract_refill_features(self, tick: MarketTick) -> dict:
        """
        Extrai features F13-F18 de tick.fluxfox_features.

        Retorna dict com os valores disponíveis (0.0 para campos ausentes).
        Garante que tipos são float — nunca lança excepção.
        """
        features = getattr(tick, "fluxfox_features", {}) or {}
        result   = {}
        for key in _REFILL_FEATURES:
            try:
                result[key] = float(features.get(key, 0.0))
            except (TypeError, ValueError):
                result[key] = 0.0
        return result

    def _check_refill_observation(
        self,
        refill: dict,
        iceberg_score: float,
        signal_dir: Direction,
        tick: MarketTick,
    ) -> Optional[dict]:
        """
        Verifica se há refills significativos que a produção está a ignorar.

        A produção ignora refills quando iceberg_proxy_score < _ICEBERG_THRESHOLD (0.85).
        NextGen detecta e regista para análise futura.

        Retorna observação dict ou None se não há nada significativo.
        """
        refill_count = refill.get("refill_count", 0.0)
        loi          = refill.get("large_order_imbalance", 0.0)
        refill_size  = refill.get("refill_size_ratio", 0.0)
        velocity     = refill.get("refill_velocity", 0.0)

        # Critério: refill real detectado E produção provavelmente ignorou
        is_significant = (
            refill_count >= _REFILL_SIGNIFICANT_COUNT or
            abs(loi)      >= _LOI_SIGNIFICANT
        )
        prod_ignored = iceberg_score < self._threshold  # produção usa threshold 0.85

        if not is_significant:
            return None

        # Determinar alinhamento do refill com a direcção
        loi_aligned = (
            (signal_dir == Direction.LONG  and loi < -_LOI_SIGNIFICANT) or
            (signal_dir == Direction.SHORT and loi > +_LOI_SIGNIFICANT)
        )

        # Estimar lotes (refill_size_ratio × 400 como heurística)
        estimated_lots = max(refill_size * 400, refill_count * 10)

        obs = {
            "refill_count":       refill_count,
            "estimated_lots":     round(estimated_lots, 0),
            "loi":                round(loi, 4),
            "velocity_per_s":     round(velocity, 3),
            "chain_duration_ms":  round(refill.get("chain_duration_ms", 0.0), 1),
            "loi_aligned":        loi_aligned,
            "prod_score":         round(iceberg_score, 4),
            "prod_would_ignore":  prod_ignored,
        }

        if prod_ignored:
            # Produção ignorou — NextGen regista observação
            lot_str = f"{estimated_lots:.0f}" if estimated_lots > 0 else "?"
            dir_str = str(signal_dir).replace("Direction.", "")
            _logger.info(
                "OrderStorm [REFILL_OBS]: Detectado Refill de ~%s lotes "
                "no preco. Producao ignorou (iceberg_score=%.3f < %.2f), "
                "NextGen registou. LOI=%.3f dir=%s aligned=%s",
                lot_str, iceberg_score, self._threshold, loi, dir_str, loi_aligned,
            )

        return obs

    # ─── Iceberg Confluence ───────────────────────────────────────────────────

    def _iceberg_confluence(
        self,
        score: float,
        iceberg_side: Optional[str],
        direction: Direction,
    ) -> tuple[str, float]:
        """
        Calcula confluência entre iceberg e direcção proposta.

        Retorna (confluence_label, size_multiplier).
        """
        if score < self._threshold:
            return "neutral", self._mults["neutral"]

        if iceberg_side is None:
            return "neutral", self._mults["neutral"]

        iceberg_long  = iceberg_side.upper() in ("BUY", "BID", "LONG")
        iceberg_short = iceberg_side.upper() in ("SELL", "ASK", "SHORT")

        if direction == Direction.LONG and iceberg_long:
            return "aligned", self._mults["aligned"]
        elif direction == Direction.SHORT and iceberg_short:
            return "aligned", self._mults["aligned"]
        elif direction == Direction.LONG and iceberg_short:
            return "opposed", self._mults["opposed"]
        elif direction == Direction.SHORT and iceberg_long:
            return "opposed", self._mults["opposed"]

        return "neutral", self._mults["neutral"]

    # ─── evaluate ─────────────────────────────────────────────────────────────

    def evaluate(self, tick: MarketTick) -> ProviderVerdict:
        iceberg_score = getattr(tick, "iceberg_proxy_score", None)
        iceberg_side  = getattr(tick, "iceberg_side", None)
        signal_dir    = getattr(tick, "signal_direction", Direction.NEUTRAL)

        # ── Refill Feature Bridge (F13-F18) ──────────────────────────────────
        refill_features = self._extract_refill_features(tick)
        refill_available = any(v > 0 for v in refill_features.values())

        # Usar score zero se não disponível (stub não bloqueia pipeline)
        effective_score = iceberg_score if iceberg_score is not None else 0.0

        # Verificar refill observation (sempre, independente do proxy score)
        refill_obs = None
        if refill_available:
            refill_obs = self._check_refill_observation(
                refill_features, effective_score, signal_dir, tick
            )

        if iceberg_score is None:
            return self._stub_verdict(
                metadata={
                    "note":             "iceberg_proxy_score nao disponivel",
                    "refill_features":  refill_features,
                    "refill_obs":       refill_obs,
                }
            )

        # ── Confluence principal ──────────────────────────────────────────────
        confluence, mult = self._iceberg_confluence(iceberg_score, iceberg_side, signal_dir)

        # Score forte → boost adicional
        if iceberg_score >= self._strong_threshold and confluence == "aligned":
            mult = min(mult * 1.10, DEFAULT_ENGINE_RISK_CONFIG["max_multiplier"])

        veto = VetoStatus.CLEAR
        if confluence == "opposed" and iceberg_score >= self._strong_threshold:
            veto = VetoStatus.SOFT_VETO

        # ── Ajuste por refill F13-F18 (amplificador se disponível) ───────────
        refill_mult_adj = 1.0
        if refill_obs and refill_obs.get("loi_aligned") and effective_score < self._threshold:
            # Refill alinhado detectado mas produção ignorou → boost leve (NextGen é mais sensível)
            refill_mult_adj = 1.05
            mult = min(mult * refill_mult_adj, DEFAULT_ENGINE_RISK_CONFIG["max_multiplier"])

        return ProviderVerdict(
            provider_name    = self.PROVIDER_NAME,
            gate_number      = self.GATE_NUMBER,
            direction        = Direction.NEUTRAL,   # Gate 4 nao emite direccao
            confidence_score = round(effective_score, 4),
            veto_status      = veto,
            size_multiplier  = round(mult, 3),
            metadata         = {
                "confluence":      confluence,
                "iceberg_score":   round(effective_score, 4),
                "iceberg_side":    iceberg_side,
                "ml_mode":         self._ml_loaded,
                "refill_features": refill_features,
                "refill_obs":      refill_obs,
                "refill_mult_adj": refill_mult_adj,
            },
            is_stub = False,
        )

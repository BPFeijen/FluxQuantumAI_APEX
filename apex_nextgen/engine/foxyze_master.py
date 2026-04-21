"""
FoxyzeMaster — Orquestrador hierárquico dos 4 gates activos (NextGen v3.2).

Arquitectura de gates:
    Gate 2 — NewsGate         : NEWS VETO / FORCE_EXIT
    Gate 3 — FluxSignalEngine : PRIMARY DIRECTION (ALPHA/GAMMA/DELTA)
    Gate 4 — OrderStorm       : ICEBERG CONFLUENCE (amplificador)
    Gate 5 — RegimeForecast   : REGIME / TIMING FILTER

    Gate 1 — AnomalyForgeV3    : MICROSTRUCTURE VETO (Layer 1 Rule-Based)
              6 regras: spread spike, depth collapse, toxicity, volume spike,
              sweep detected, DOM extreme. 2+ warnings → HARD_VETO.
              (AnomalyForge V2 removido 2026-04-12: MSE ratio 0.987× — sem discriminação)

Sizing dinâmico:
    contracts = base × ∏(size_multiplier_i) para gates que não vetam
    Bounded por [min_multiplier, max_multiplier] × base_contracts

Regras de veto (por ordem de precedência):
    HARD_VETO  → stop. Não calcular gates seguintes.
    FORCE_EXIT → emitir sinal de fecho de posição.
    SOFT_VETO  → continuar pipeline mas reduzir size.

Diferença chave vs produção:
    Produção usa sizing binário (4 contratos ou 0).
    NextGen usa sizing progressivo contínuo (0.25x → 1.50x).
"""

from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from ..common.base_provider import Direction, MarketTick, ProviderVerdict, VetoStatus
from ..common.config import DEFAULT_ENGINE_RISK_CONFIG
from ..providers import (
    AnomalyForgeV3Provider,
    FluxSignalEngineProvider,
    NewsGateProvider,
    OrderStormProvider,
    RegimeForecastProvider,
)

_logger = logging.getLogger("nextgen.foxyze_master")


@dataclass
class MasterVerdict:
    """Resultado final após todos os gates."""
    timestamp:        datetime
    direction:        str                    # "LONG" | "SHORT" | "FLAT" | "FORCE_EXIT"
    contracts:        float                  # sizing calculado
    final_multiplier: float                  # produto dos multiplicadores
    blocked_at_gate:  Optional[int]          # gate que vetou (None = passou tudo)
    block_reason:     Optional[str]
    gate_verdicts:    List[dict]             # snapshot de cada gate
    latency_ms:       float
    metadata:         dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


class FoxyzeMaster:
    """
    Orquestrador dos 5 gates NextGen.

    Uso:
        master  = FoxyzeMaster()
        verdict = master.evaluate(tick)

    O resultado é um MasterVerdict com a direcção final e os contratos calculados.
    Para uso em modo shadow: verdict.to_dict() → JSONL log.
    """

    def __init__(
        self,
        base_contracts: int = DEFAULT_ENGINE_RISK_CONFIG["default_base_contracts"],
    ):
        self._base = base_contracts
        self._min_mult = DEFAULT_ENGINE_RISK_CONFIG["min_multiplier"]
        self._max_mult = DEFAULT_ENGINE_RISK_CONFIG["max_multiplier"]
        self._max_cont = DEFAULT_ENGINE_RISK_CONFIG["max_contracts"]

        # Instanciar os 5 providers activos
        self.gate1 = AnomalyForgeV3Provider()
        self.gate2 = NewsGateProvider()
        self.gate3 = FluxSignalEngineProvider()
        self.gate4 = OrderStormProvider()
        self.gate5 = RegimeForecastProvider()

        _logger.info(
            "FoxyzeMaster inicializado — %d contratos base | gates: [%s %s %s %s %s]",
            self._base,
            "G1:AnomalyForgeV3",
            "G2:NewsGate",
            "G3:FluxSignal",
            "G4:OrderStorm",
            "G5:RegimeForecast",
        )

    def _run_gate(self, provider, tick: MarketTick) -> ProviderVerdict:
        """Executa um gate com medição de latência e protecção de erro."""
        try:
            return provider._timed_evaluate(tick)
        except Exception as e:
            _logger.error("Gate %s falhou: %s — retornando stub", provider.PROVIDER_NAME, e)
            return provider._stub_verdict(metadata={"error": str(e)})

    def evaluate(self, tick: MarketTick) -> MasterVerdict:
        t0 = _time.perf_counter()
        verdicts: List[ProviderVerdict] = []
        cumulative_mult = 1.0
        direction = Direction.NEUTRAL

        # ── Gate 1: AnomalyForgeV3 (Layer 1 Rule-Based) ──────────────────────
        v1 = self._run_gate(self.gate1, tick)
        verdicts.append(v1)

        if v1.veto_status == VetoStatus.HARD_VETO:
            reason = v1.metadata.get("fired_rules") or "AnomalyForgeV3 HARD_VETO"
            return self._blocked(tick, 1, f"AnomalyForge L1 HARD_VETO fired={reason}", verdicts, t0)

        cumulative_mult *= v1.size_multiplier

        # ── Gate 2: NewsGate ──────────────────────────────────────────────────
        v2 = self._run_gate(self.gate2, tick)
        verdicts.append(v2)

        if v2.veto_status == VetoStatus.FORCE_EXIT:
            return self._force_exit(tick, 2, "NewsGate FORCE_EXIT", verdicts, t0)
        if v2.veto_status == VetoStatus.HARD_VETO:
            return self._blocked(tick, 2, "NewsGate HARD_VETO", verdicts, t0)

        cumulative_mult *= v2.size_multiplier

        # ── Gate 3: FluxSignalEngine ──────────────────────────────────────────
        v3 = self._run_gate(self.gate3, tick)
        verdicts.append(v3)
        direction = v3.direction

        if v3.veto_status == VetoStatus.HARD_VETO:
            # Momentum block — FLAT com reason explícita
            reason = v3.metadata.get("veto_reason") or "FluxSignal HARD_VETO"
            return self._blocked(tick, 3, reason, verdicts, t0)

        if direction == Direction.FLAT:
            return self._no_signal(tick, verdicts, t0, cumulative_mult)

        # Propagar direcção no tick para Gate 4 (OrderStorm usa signal_direction)
        tick.signal_direction = direction  # type: ignore[attr-defined]

        # ── Gate 4: OrderStorm ────────────────────────────────────────────────
        v4 = self._run_gate(self.gate4, tick)
        verdicts.append(v4)

        if v4.veto_status == VetoStatus.HARD_VETO:
            return self._blocked(tick, 4, "OrderStorm HARD_VETO", verdicts, t0)

        cumulative_mult *= v4.size_multiplier

        # ── Gate 5: RegimeForecast ────────────────────────────────────────────
        v5 = self._run_gate(self.gate5, tick)
        verdicts.append(v5)

        if v5.veto_status == VetoStatus.FORCE_EXIT:
            return self._force_exit(tick, 5, "RegimeForecast FORCE_EXIT", verdicts, t0)
        if v5.veto_status == VetoStatus.HARD_VETO:
            return self._blocked(tick, 5, "RegimeForecast HARD_VETO", verdicts, t0)

        cumulative_mult *= v5.size_multiplier

        # ── Sizing final ──────────────────────────────────────────────────────
        final_mult = max(self._min_mult, min(cumulative_mult, self._max_mult))
        contracts  = min(round(self._base * final_mult, 2), self._max_cont)

        latency = (_time.perf_counter() - t0) * 1000

        # Breakdown de multiplicadores por gate (Sprint 3 — transparência de sizing)
        sizing_breakdown = {
            "anomaly_mult": round(v1.size_multiplier, 3),
            "news_mult":    round(v2.size_multiplier, 3),
            "signal_mult":  round(v3.size_multiplier, 3),  # Gate 3 (usualmente 1.0)
            "iceberg_mult": round(v4.size_multiplier, 3),
            "regime_mult":  round(v5.size_multiplier, 3),
            "cumulative":   round(cumulative_mult, 4),
            "clamped":      round(final_mult, 4),
        }

        # Zero-contract rule: contratos < 1 → NO_TRADE (não abrir posição sub-lote)
        if contracts < 1.0:
            _logger.info(
                "FoxyzeMaster: NO_TRADE (contracts=%.2f < 1) dir=%s sizing=%s",
                contracts, direction.value, sizing_breakdown,
            )
            return self._no_signal(
                tick, verdicts, t0, final_mult,
                reason      = "NO_TRADE_ZERO_CONTRACTS",
                extra_meta  = {"sizing_breakdown": sizing_breakdown, "raw_contracts": contracts},
            )

        _logger.debug(
            "FoxyzeMaster: dir=%s cont=%.2f mult=%.3f lat=%.1fms",
            direction.value, contracts, final_mult, latency,
        )

        return MasterVerdict(
            timestamp        = tick.timestamp,
            direction        = direction.value,
            contracts        = contracts,
            final_multiplier = round(final_mult, 4),
            blocked_at_gate  = None,
            block_reason     = None,
            gate_verdicts    = [v.to_dict() for v in verdicts],
            latency_ms       = round(latency, 2),
            metadata         = {
                "base_contracts":  self._base,
                "sizing_breakdown": sizing_breakdown,
                "soft_veto_gates": [
                    v.gate_number for v in verdicts
                    if v.veto_status == VetoStatus.SOFT_VETO
                ],
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _blocked(
        self,
        tick: MarketTick,
        gate: int,
        reason: str,
        verdicts: List[ProviderVerdict],
        t0: float,
    ) -> MasterVerdict:
        latency = (_time.perf_counter() - t0) * 1000
        _logger.info("FoxyzeMaster BLOCKED gate=%d reason=%s lat=%.1fms", gate, reason, latency)
        return MasterVerdict(
            timestamp        = tick.timestamp,
            direction        = "FLAT",
            contracts        = 0.0,
            final_multiplier = 0.0,
            blocked_at_gate  = gate,
            block_reason     = reason,
            gate_verdicts    = [v.to_dict() for v in verdicts],
            latency_ms       = round(latency, 2),
        )

    def _force_exit(
        self,
        tick: MarketTick,
        gate: int,
        reason: str,
        verdicts: List[ProviderVerdict],
        t0: float,
    ) -> MasterVerdict:
        latency = (_time.perf_counter() - t0) * 1000
        _logger.warning("FoxyzeMaster FORCE_EXIT gate=%d reason=%s", gate, reason)
        return MasterVerdict(
            timestamp        = tick.timestamp,
            direction        = "FORCE_EXIT",
            contracts        = 0.0,
            final_multiplier = 0.0,
            blocked_at_gate  = gate,
            block_reason     = reason,
            gate_verdicts    = [v.to_dict() for v in verdicts],
            latency_ms       = round(latency, 2),
        )

    def _no_signal(
        self,
        tick: MarketTick,
        verdicts: List[ProviderVerdict],
        t0: float,
        mult: float,
        reason:     str  = "NO_SIGNAL",
        extra_meta: dict = None,
    ) -> MasterVerdict:
        latency = (_time.perf_counter() - t0) * 1000
        meta = extra_meta or {}
        return MasterVerdict(
            timestamp        = tick.timestamp,
            direction        = "FLAT",
            contracts        = 0.0,
            final_multiplier = round(mult, 4),
            blocked_at_gate  = None,
            block_reason     = reason,
            gate_verdicts    = [v.to_dict() for v in verdicts],
            latency_ms       = round(latency, 2),
            metadata         = meta,
        )

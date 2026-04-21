"""
base_provider.py — Contratos de Interface para todos os providers do NextGen.

Todos os providers herdam de BaseProvider e devem retornar um ProviderVerdict.
O FoxyzeMaster consome apenas ProviderVerdict — nunca lógica interna dos providers.

Hierarquia de Roles (Foxyze Spec v3.2):
    Gate 1 — AnomalyForge     → veto_status (HARD_VETO bloqueia tudo)
    Gate 2 — NewsGate         → veto_status (HARD_VETO + FORCE_EXIT)
    Gate 3 — FluxSignalEngine → direction (única fonte de direcção)
    Gate 4 — OrderStorm       → size_multiplier (ajusta confiança ±30%)
    Gate 5 — RegimeForecast   → confidence_score (qualidade de entrada)
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


# ─── Enums de Contrato ────────────────────────────────────────────────────────

class Direction(str, Enum):
    LONG    = "LONG"
    SHORT   = "SHORT"
    FLAT    = "FLAT"      # sem sinal / fechar posição
    NEUTRAL = "NEUTRAL"   # provider não tem opinião sobre direcção


class VetoStatus(str, Enum):
    CLEAR      = "CLEAR"       # Sem veto — prosseguir
    SOFT_VETO  = "SOFT_VETO"  # Reduzir size mas permitir entrada
    HARD_VETO  = "HARD_VETO"  # Bloquear completamente novas entradas
    FORCE_EXIT = "FORCE_EXIT" # Fechar posição activa imediatamente


# ─── MarketTick — estrutura de dados do feed de produção ─────────────────────

@dataclass
class MarketTick:
    """
    Snapshot de microestrutura de mercado vindo do DataIngestor.
    Campos extraídos dos CSVs de produção (C:/data/level2/_gc_xcec/).
    """
    timestamp:        datetime
    symbol:           str = "GC"

    # Spread e liquidez
    spread:           float = 0.0
    total_bid_depth:  float = 0.0
    total_ask_depth:  float = 0.0
    book_imbalance:   float = 0.0

    # DOM levels (10 níveis bid + ask)
    bid_levels:       Dict[int, float] = field(default_factory=dict)  # {0: size, 1: size...}
    ask_levels:       Dict[int, float] = field(default_factory=dict)

    # Trade data
    trade_volume:     float = 0.0
    trade_side:       str   = "UNKNOWN"   # "BUY" | "SELL" | "UNKNOWN"
    cumulative_delta: float = 0.0

    # SCHEMA_FLUXFOX_V2 features (preenchidos pelo DataIngestor quando disponíveis)
    fluxfox_features: Dict[str, float] = field(default_factory=dict)

    # Grenadier MSE (preenchido pelo AnomalyForge quando modelo carregado)
    grenadier_mse:    Optional[float] = None

    # Fonte e latência
    source_file:      str = ""
    ingest_latency_ms: float = 0.0


# ─── ProviderVerdict — Contrato de Output ────────────────────────────────────

@dataclass
class ProviderVerdict:
    """
    Output standardizado de todos os providers.
    O FoxyzeMaster consome apenas este objecto.
    """
    provider_name:    str
    gate_number:      int              # 1–5

    # Core signal
    direction:        Direction        # Só relevante para Gate 3 (FluxSignalEngine)
    confidence_score: float            # 0.0–1.0; stubs retornam 1.0
    veto_status:      VetoStatus       # Só relevante para Gates 1 e 2
    size_multiplier:  float            # 0.0–1.5; Gate 4 ajusta; outros retornam 1.0

    # Metadata (livre por provider — para logs e debugging)
    metadata:         Dict[str, Any] = field(default_factory=dict)

    # Auto-populated
    timestamp:        datetime = field(default_factory=datetime.utcnow)
    latency_ms:       float = 0.0
    is_stub:          bool = False     # True enquanto modelo real não está carregado

    def is_hard_vetoed(self) -> bool:
        return self.veto_status in (VetoStatus.HARD_VETO, VetoStatus.FORCE_EXIT)

    def is_force_exit(self) -> bool:
        return self.veto_status == VetoStatus.FORCE_EXIT

    def to_dict(self) -> dict:
        return {
            "provider":       self.provider_name,
            "gate":           self.gate_number,
            "direction":      self.direction.value,
            "confidence":     round(self.confidence_score, 4),
            "veto":           self.veto_status.value,
            "size_mult":      round(self.size_multiplier, 3),
            "latency_ms":     round(self.latency_ms, 2),
            "is_stub":        self.is_stub,
            "metadata":       self.metadata,
        }


# ─── BaseProvider — Classe Base Abstracta ────────────────────────────────────

class BaseProvider(ABC):
    """
    Classe base para todos os providers do NextGen.

    Implementar:
        evaluate(tick) → ProviderVerdict
        is_ready()     → bool

    Convenção de stub:
        Se o modelo/dados não estão carregados, chamar _stub_verdict()
        com os defaults apropriados para este gate.
        O FoxyzeMaster trata stubs como permissivos (não vetam, não dão direcção).
    """

    GATE_NUMBER: int = 0        # Definir em cada subclasse
    PROVIDER_NAME: str = ""     # Definir em cada subclasse

    @abstractmethod
    def evaluate(self, tick: MarketTick) -> ProviderVerdict:
        """
        Avalia o tick e retorna um ProviderVerdict.
        Deve ser O(1) ou O(small) — chamado a cada tick de produção.
        """
        ...

    @abstractmethod
    def is_ready(self) -> bool:
        """True se o provider tem todos os recursos necessários carregados."""
        ...

    def _timed_evaluate(self, tick: MarketTick) -> ProviderVerdict:
        """Wrapper que mede latência. Usar em vez de evaluate() directamente."""
        t0 = time.perf_counter()
        verdict = self.evaluate(tick)
        verdict.latency_ms = (time.perf_counter() - t0) * 1000
        return verdict

    def _stub_verdict(
        self,
        direction: Direction = Direction.NEUTRAL,
        veto_status: VetoStatus = VetoStatus.CLEAR,
        size_multiplier: float = 1.0,
        metadata: Optional[Dict] = None,
    ) -> ProviderVerdict:
        """Retorna um verdict de stub — permissivo, sem opinião real."""
        return ProviderVerdict(
            provider_name=self.PROVIDER_NAME,
            gate_number=self.GATE_NUMBER,
            direction=direction,
            confidence_score=1.0,   # stubs não bloqueiam
            veto_status=veto_status,
            size_multiplier=size_multiplier,
            metadata={"stub": True, **(metadata or {})},
            is_stub=True,
        )

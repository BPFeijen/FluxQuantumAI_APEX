"""
IcebergOutputV2 — canonical Gate 4 output schema for the iceberg detection pipeline.

Reference: Func spec §9 (FUNC_GC_Iceberg_ML_Module_v2_20260321.md).

This is the runtime signal consumed by Gate 4 of the Master Engine.
For the Parquet training label schema see tech spec §4.2.

Enums
------
IcebergType      — NONE / NATIVE / SYNTHETIC / ABSORPTION
AbsorptionState  — NONE / ACTIVE / WEAKENING / EXHAUSTED
IcebergSide      — BID / ASK / NONE

Key behaviour
-------------
IcebergOutputV2.get_confluence_multiplier(direction) → float [0.5, 1.5]
IcebergOutputV2.none_detected()  → neutral output (detected=False, multiplier=1.0)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Enumerations  (str Enum so values serialize cleanly to JSON/Parquet)
# ---------------------------------------------------------------------------

class IcebergType(str, Enum):
    """Classification of the iceberg detected."""
    NONE = "NONE"
    NATIVE = "NATIVE"           # Exchange-native HFT iceberg, refills < 10 ms
    SYNTHETIC = "SYNTHETIC"     # Manual/algo iceberg, refills 10–100 ms
    ABSORPTION = "ABSORPTION"   # Large passive absorption, no clear refill chain


class AbsorptionState(str, Enum):
    """Lifecycle state of the absorption."""
    NONE = "NONE"           # No absorption activity
    ACTIVE = "ACTIVE"       # Actively absorbing — refills continuing
    WEAKENING = "WEAKENING" # Refill rate decelerating — iceberg may be ending
    EXHAUSTED = "EXHAUSTED" # Absorption finished — level exhausted or order pulled


class IcebergSide(str, Enum):
    """Side of the passive iceberg order."""
    BID = "BID"
    ASK = "ASK"
    NONE = "NONE"   # Unknown or no detection


# ---------------------------------------------------------------------------
# Output dataclass  (Gate 4 interface — func spec §9)
# ---------------------------------------------------------------------------

@dataclass
class IcebergOutputV2:
    """
    Gate 4 output record for iceberg detection.

    One instance per inference tick. Consumed by Master Engine
    decide_from_providers() via get_confluence_multiplier(direction).

    Backward-compatible: get_confluence_multiplier() still exists but now
    returns graduated values in [0.5, 1.5] instead of binary {0.75, 1.30}.
    """

    # --- Detection (Stage 2) ---
    detected: bool = False
    side: IcebergSide = IcebergSide.NONE
    iceberg_type: IcebergType = IcebergType.NONE
    confidence: float = 0.0          # ML confidence 0.0–1.0
    price_level: Optional[float] = None
    peak_size: float = 0.0           # Estimated display quantity (lots)
    refill_count: int = 0

    # --- Prediction (Stage 3a — Kaplan-Meier) ---
    estimated_hidden_volume: float = 0.0   # Remaining hidden volume estimate
    volume_confidence: float = 0.0         # KM prediction confidence

    # --- State (Stage 3b — state machine) ---
    absorption_state: AbsorptionState = AbsorptionState.NONE
    refill_velocity: float = 0.0   # Refills/second (current window)
    refill_trend: float = 0.0      # +/- change in velocity

    # ------------------------------------------------------------------
    # Gate 4 interface
    # ------------------------------------------------------------------

    def get_confluence_multiplier(self, direction: str) -> float:
        """
        Graduated multiplier for Gate 4 position sizing.

        Parameters
        ----------
        direction : str
            Signal direction: "LONG" or "SHORT".

        Returns
        -------
        float
            Multiplier in [0.5, 1.5].
            1.0 = neutral (no iceberg influence).

        Rules
        -----
        BR-003: returns 1.0 if confidence < 0.60 OR refill_count < 3.
        BR-002: EXHAUSTED state reduces evidence regardless of alignment.
        BR-001: BID iceberg = bullish. ASK iceberg = bearish.

        Nota: A fórmula actual com valores 0.60, 0.30, 0.50 são defaults iniciais.
        No Sprint 4 (T-410), esta fórmula será substituída por um modelo treinado
        via optimização no backtest walk-forward.

        Nota (Sprint 5, 2026-03-23): confidence aqui não reflecte ainda chain_weight
        do Tranche Tree (CME Paper §4.2). Quando o KM ponderado for calibrado no
        Sprint 4, chain_weight será propagado até ao IcebergOutputV2.confidence.
        """
        if not self.detected or self.side == IcebergSide.NONE:
            return 1.0

        # BR-003: Minimum evidence threshold
        if self.confidence < 0.60 or self.refill_count < 3:
            return 1.0

        # BR-001: Alignment check
        iceberg_bullish = (self.side == IcebergSide.BID)
        signal_bullish = (direction == "LONG")
        alignment = 1.0 if (iceberg_bullish == signal_bullish) else -1.0

        # Evidence weight: confidence × normalised hidden volume
        # calibrated 2026-04-10: denominator 10→20 lots (aggressor_vol p75=20 for iceberg cands;
        # denominator=10 saturated evidence for median icebergs — too aggressive)
        evidence = self.confidence * min(self.estimated_hidden_volume / 20.0, 1.0)

        # BR-002: Absorption state modulates evidence
        if self.absorption_state == AbsorptionState.EXHAUSTED:
            evidence *= 0.3
        elif self.absorption_state == AbsorptionState.WEAKENING:
            evidence *= 0.6

        MAX_ADJUSTMENT = 0.50
        raw = 1.0 + (alignment * evidence * MAX_ADJUSTMENT)
        return max(0.5, min(1.5, round(raw, 4)))

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def none_detected(cls) -> "IcebergOutputV2":
        """Return a neutral no-detection output (multiplier = 1.0)."""
        return cls(detected=False)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Flat dict for logging / replay / Parquet storage."""
        return {
            "detected": self.detected,
            "side": self.side.value,
            "iceberg_type": self.iceberg_type.value,
            "confidence": self.confidence,
            "price_level": self.price_level,
            "peak_size": self.peak_size,
            "refill_count": self.refill_count,
            "estimated_hidden_volume": self.estimated_hidden_volume,
            "volume_confidence": self.volume_confidence,
            "absorption_state": self.absorption_state.value,
            "refill_velocity": self.refill_velocity,
            "refill_trend": self.refill_trend,
        }

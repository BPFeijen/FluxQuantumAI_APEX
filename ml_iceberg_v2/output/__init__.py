"""
output/ — Canonical output schema for the iceberg detection pipeline (Gate 4).

Func spec §9: IcebergOutputV2 is the Gate 4 interface consumed by Master Engine
decide_from_providers(). Replaces binary IcebergOutput with graduated [0.5, 1.5]
multiplier based on ML confidence, hidden volume, and absorption state.
"""

from ml_iceberg_v2.output.iceberg_output_v2 import (
    IcebergOutputV2,
    IcebergType,
    AbsorptionState,
    IcebergSide,
)

__all__ = [
    "IcebergOutputV2",
    "IcebergType",
    "AbsorptionState",
    "IcebergSide",
]

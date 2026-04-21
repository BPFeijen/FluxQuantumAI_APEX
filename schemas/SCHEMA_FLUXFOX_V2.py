"""
SCHEMA_FLUXFOX_V2 — Fonte da Verdade. FluxFox Labs / Barbara (Arquitecto).

23 features nomeadas (F01–F23), 26 colunas reais (F11a, F11b, F12a são sub-features).
Qualquer dado que não tenha estas 26 colunas está fora de conformidade.

Serve tanto para:
  - IcebergDetector (sinal de entrada)
  - GrenadierV2 LSTM-Autoencoder (sensor de anomalia via MSE)

Dataset gerado por: ml_iceberg_v2/features/feature_extractor.py (MBO Tick-Level)
"""

from __future__ import annotations
from typing import List

# ─── SCHEMA CANÓNICO ─────────────────────────────────────────────────────────

SCHEMA_FLUXFOX_V2: List[str] = [
    # Grupo 1: Dinâmica de Refill
    "refill_count",                        # F01
    "refill_speed_mean_ms",                # F02
    "refill_speed_std_ms",                 # F03
    "refill_size_ratio_mean",              # F04
    "refill_size_consistency",             # F05

    # Grupo 2: Absorção e Persistência
    "price_persistence_s",                 # F06
    "aggressor_volume_absorbed",           # F07
    "dom_imbalance_at_level",              # F08
    "book_depth_recovery_rate",            # F09

    # Grupo 3: Contexto e Volatilidade
    "trade_intensity",                     # F10
    "session_label",                       # F11
    "hour_of_day",                         # F11a
    "minute_of_hour",                      # F11b
    "volatility_bucket",                   # F12
    "atr_5min_raw",                        # F12a

    # Grupo 4: Velocidade e Fluxo
    "refill_velocity",                     # F13
    "refill_velocity_trend",               # F14
    "cumulative_absorbed_volume",          # F15
    "level_lifetime_s",                    # F16
    "time_since_last_trade_at_vanish_ms",  # F17
    "price_excursion_beyond_level_ticks",  # F18
    "underflow_volume",                    # F19
    "iceberg_sequence_direction_score",    # F20

    # Grupo 5: Features de Guerra (Payroll / Macro)
    "ofi_rate",                            # F21 — Order Flow Imbalance [lots/s]
    "vot",                                 # F22 — Velocity of Tape [monetary mass/s]
    "inst_volatility_ticks",               # F23 — Volatilidade instantânea 100ms
]

N_FEATURES: int = len(SCHEMA_FLUXFOX_V2)  # 26
assert N_FEATURES == 26, f"SCHEMA_FLUXFOX_V2 length mismatch: {N_FEATURES}"


def validate(df_columns) -> List[str]:
    """
    Verifica se um DataFrame tem todas as colunas do schema.
    Retorna lista de colunas em falta (vazia = conformidade total).
    """
    return [col for col in SCHEMA_FLUXFOX_V2 if col not in df_columns]

"""
config.py — Configuração central do NextGen (Foxyze v3.2).

DEFAULT_ENGINE_RISK_CONFIG: sizing dinâmico baseado nos multiplicadores dos providers.
Produção usa sizing binário (CAL series). NextGen testa o sizing progressivo original.
"""

from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────

# Feed de produção (microstructure CSVs escritos pelo event_processor.py)
PRODUCTION_L2_DIR         = Path(r"C:\data\level2\_gc_xcec")
MICROSTRUCTURE_FILE_GLOB  = "microstructure_*.csv.gz"   # 1s resolution, ByPriceLVL (post fix)
SHADOW_LOG_DIR    = Path(r"C:\FluxQuantumAI\apex_nextgen\logs")

ICEBERG_MODEL_PATH      = Path(r"C:\FluxQuantumAI\apex_nextgen\models\ml_iceberg_v2.pt")

# Proxy events (news gate)
PROXY_EVENTS_PATH = Path(r"C:\FluxQuantumAI\config\proxy_events.json")

# ─── Engine Risk Config (Foxyze v3.2) ────────────────────────────────────────

DEFAULT_ENGINE_RISK_CONFIG = {
    # Sizing base
    "default_base_contracts": 4,
    "min_multiplier":         0.25,
    "max_multiplier":         1.50,
    "max_contracts":          10,

    # Gate 2 — NewsGate multipliers por severidade
    "news_multipliers": {
        "LOW":      1.00,
        "MEDIUM":   0.75,
        "HIGH":     0.50,
        "CRITICAL": 0.00,   # HARD_VETO
    },

    # Gate 4 — OrderStorm confluence multipliers
    "iceberg_multipliers": {
        "aligned":  1.30,   # iceberg no mesmo lado da direcção
        "neutral":  1.00,
        "opposed":  0.75,   # iceberg contra a direcção
    },

    # Gate 5 — RegimeForecast multipliers por qualidade de entrada
    "regime_multipliers": {
        "allow_entry":  1.00,
        "reduce_size":  0.75,
        "avoid_entry":  0.25,
        "force_exit":   0.00,
    },

    # Signal Engine (Gate 3)
    "min_direction_confidence": 0.30,  # abaixo disto → NO_SIGNAL

    # Posição
    "max_drawdown_per_session_pts": 50.0,
}

# ─── Session Config ───────────────────────────────────────────────────────────

SESSION_CONFIG = {
    "ASIA":   {"start": "18:00", "end": "02:00", "multiplier": 0.50},
    "LONDON": {"start": "03:00", "end": "09:30", "multiplier": 0.70},
    "NY":     {"start": "09:30", "end": "16:00", "multiplier": 1.00},
}

# ─── Production Logs (read-only) ─────────────────────────────────────────────

PRODUCTION_LOGS_DIR   = Path(r"C:\FluxQuantumAI\logs")
LIVE_LOG_PATH         = PRODUCTION_LOGS_DIR / "live_log.csv"         # mt5_executor (demo)
LIVE_LOG_HANTEC_PATH  = PRODUCTION_LOGS_DIR / "live_log_live.csv"    # mt5_executor_hantec
TRADES_CSV_PATH       = PRODUCTION_LOGS_DIR / "trades_live.csv"      # ordens executadas

# ─── Shadow Log ───────────────────────────────────────────────────────────────

SHADOW_LOG_PATH    = SHADOW_LOG_DIR / "nextgen_performance.jsonl"
SCORECARD_LOG_PATH = SHADOW_LOG_DIR / "daily_scorecard.jsonl"

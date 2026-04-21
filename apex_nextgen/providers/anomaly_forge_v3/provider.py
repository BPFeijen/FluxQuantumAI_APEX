"""
provider.py — AnomalyForgeV3Provider (Gate 1, Layer 1)
=======================================================

Layer 1 do AnomalyForge V3: regras hardcoded de microestrutura.
Não requer modelo ML — opera sobre dados históricos locais + feed live.

Input:  microstructure_YYYY-MM-DD.csv.gz  (C:/data/level2/_gc_xcec/)
Output: ProviderVerdict com veto_status + size_multiplier

Veto logic:
    0 regras disparadas → CLEAR      (size_mult=1.0)
    1 regra disparada   → SOFT_VETO  (size_mult=0.75)
    2+ regras disparadas → HARD_VETO (size_mult=0.0)
"""

from __future__ import annotations

import gzip
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from ...common.base_provider import (
    BaseProvider,
    Direction,
    MarketTick,
    ProviderVerdict,
    VetoStatus,
)
from .layer1_rules import evaluate_all_rules, Layer1Decision

_logger = logging.getLogger("nextgen.anomaly_forge_v3")

# ── Configuração ──────────────────────────────────────────────────────────────

DATA_ROOT = Path(os.getenv("LEVEL2_DATA_DIR", "C:/data/level2/_gc_xcec"))
SYMBOL    = "_gc_xcec"

# Janela de dados usada para avaliar as regras
EVAL_WINDOW_MINUTES = 30   # últimos 30min de microestrutura

# Cache: re-ler o arquivo no máximo de N segundos em segundos
CACHE_TTL_SECONDS = 15

# Mínimo de linhas para avaliação confiável
MIN_ROWS_FOR_EVAL = 5


class AnomalyForgeV3Provider(BaseProvider):
    """
    Gate 1 — AnomalyForge V3 (Layer 1 Rule-Based).

    Avalia condições de microestrutura nos últimos EVAL_WINDOW_MINUTES
    e veta entradas em condições adversas.

    Phase 1 (actual): Layer 1 apenas (regras hardcoded).
    Phase 3 (futuro): + Layer 2 Transformer-AE + OC-SVM em latent space.
    Phase 5 (futuro): + Layer 3 XGBoost supervisionado.
    """

    GATE_NUMBER:   int = 1
    PROVIDER_NAME: str = "AnomalyForgeV3-L1"

    def __init__(self, eval_window_minutes: int = EVAL_WINDOW_MINUTES):
        self._window_min = eval_window_minutes
        self._cache_df: Optional[pd.DataFrame] = None
        self._cache_date: Optional[str] = None
        self._cache_ts:   float = 0.0

        _logger.info(
            "AnomalyForgeV3Provider (Layer1) inicializado — window=%dmin data_root=%s",
            self._window_min,
            DATA_ROOT,
        )

    # ── Interface Pública ─────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        """True se existe pelo menos um arquivo de microestrutura hoje."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        path  = DATA_ROOT / f"microstructure_{today}.csv.gz"
        return path.exists()

    def evaluate(self, tick: MarketTick) -> ProviderVerdict:
        """
        Avalia as 6 regras nos dados de microestrutura recentes.
        Chamado pelo FoxyzeMaster antes de cada gate de entrada.
        """
        df = self._load_recent_microstructure(tick.timestamp)

        if df is None or len(df) < MIN_ROWS_FOR_EVAL:
            # Dados insuficientes — CLEAR mas loggar
            _logger.debug("Layer1: dados insuficientes (rows=%d) → stub CLEAR", len(df) if df is not None else 0)
            return self._stub_verdict(metadata={"reason": "insufficient_microstructure_data"})

        decision: Layer1Decision = evaluate_all_rules(df)

        # Mapear para VetoStatus
        if decision.veto_level == "HARD_VETO":
            veto = VetoStatus.HARD_VETO
        elif decision.veto_level == "SOFT_VETO":
            veto = VetoStatus.SOFT_VETO
        else:
            veto = VetoStatus.CLEAR

        if decision.veto_level != "CLEAR":
            _logger.warning(
                "Layer1 %s — fired=%s (warnings=%d rows=%d window=%.0fmin)",
                decision.veto_level,
                decision.fired_rules,
                decision.warning_count,
                decision.data_rows,
                decision.data_minutes,
            )

        return ProviderVerdict(
            provider_name    = self.PROVIDER_NAME,
            gate_number      = self.GATE_NUMBER,
            direction        = Direction.NEUTRAL,
            confidence_score = max(0.0, 1.0 - decision.warning_count * 0.4),
            veto_status      = veto,
            size_multiplier  = decision.size_mult,
            metadata         = decision.to_dict(),
        )

    # ── Data Loading ──────────────────────────────────────────────────────────

    def _load_recent_microstructure(
        self,
        as_of: datetime,
        fallback_days: int = 1,
    ) -> Optional[pd.DataFrame]:
        """
        Carrega os últimos EVAL_WINDOW_MINUTES de dados de microestrutura.
        Usa cache com TTL de CACHE_TTL_SECONDS.

        Se o arquivo de hoje não existir, devolve None.
        Se a sessão ainda tem pouco histórico (early session), usa o que há.
        """
        import time as _time

        date_str = as_of.strftime("%Y-%m-%d")

        # Cache hit
        if (
            self._cache_df is not None
            and self._cache_date == date_str
            and (_time.time() - self._cache_ts) < CACHE_TTL_SECONDS
        ):
            return self._filter_window(self._cache_df, as_of)

        # Tentar carregar arquivo de hoje
        df = self._read_microstructure_file(date_str)

        if df is None:
            _logger.debug("Layer1: arquivo de microestrutura não encontrado para %s", date_str)
            return None

        self._cache_df   = df
        self._cache_date = date_str
        self._cache_ts   = _time.time()

        return self._filter_window(df, as_of)

    def _filter_window(self, df: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
        """Filtra apenas as últimas EVAL_WINDOW_MINUTES linhas antes de as_of."""
        cutoff = as_of - timedelta(minutes=self._window_min)
        # Tornar as_of timezone-naive se necessário
        if df.index.tz is not None:
            cutoff = pd.Timestamp(cutoff).tz_localize("UTC")
            as_of_ts = pd.Timestamp(as_of).tz_localize("UTC") if as_of.tzinfo is None else pd.Timestamp(as_of)
        else:
            as_of_ts = pd.Timestamp(as_of.replace(tzinfo=None))

        mask = (df.index >= cutoff) & (df.index <= as_of_ts)
        filtered = df[mask]
        return filtered if len(filtered) >= 1 else df.tail(MIN_ROWS_FOR_EVAL * 2)

    @staticmethod
    def _read_microstructure_file(date_str: str) -> Optional[pd.DataFrame]:
        """Lê microstructure_YYYY-MM-DD.csv.gz e retorna DataFrame."""
        path = DATA_ROOT / f"microstructure_{date_str}.csv.gz"
        if not path.exists():
            return None

        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                df = pd.read_csv(f)
            # Schema v1: recv_ts | Schema v2: recv_timestamp
            if "recv_ts" in df.columns:
                ts_col = "recv_ts"
            elif "recv_timestamp" in df.columns:
                ts_col = "recv_timestamp"
            else:
                _logger.warning("Layer1: coluna de timestamp não encontrada em %s", path)
                return None
            df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="ISO8601")
            df = df.set_index(ts_col).sort_index()
            df.index.name = "timestamp"
            return df
        except Exception as e:
            _logger.error("Layer1: erro a ler %s — %s", path, e)
            return None


# ── Utilitário para backtesting ────────────────────────────────────────────────

def load_microstructure_for_date(date_str: str) -> Optional[pd.DataFrame]:
    """
    Helper para scripts de backtest — carrega microestrutura de uma data.
    date_str: formato "YYYY-MM-DD"
    """
    return AnomalyForgeV3Provider._read_microstructure_file(date_str)


def evaluate_at_timestamp(
    date_str: str,
    timestamp: datetime,
    window_minutes: int = EVAL_WINDOW_MINUTES,
) -> Optional[Layer1Decision]:
    """
    Helper para backtesting — avalia Layer 1 num ponto temporal específico.
    Usado por scripts de análise e validação.
    """
    df_full = load_microstructure_for_date(date_str)
    if df_full is None or len(df_full) == 0:
        return None

    cutoff = pd.Timestamp(timestamp) - pd.Timedelta(minutes=window_minutes)
    if df_full.index.tz is not None:
        cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff

    window_df = df_full[df_full.index <= pd.Timestamp(timestamp)]
    window_df = window_df[window_df.index >= cutoff]

    if len(window_df) < MIN_ROWS_FOR_EVAL:
        window_df = df_full[df_full.index <= pd.Timestamp(timestamp)].tail(MIN_ROWS_FOR_EVAL * 2)

    return evaluate_all_rules(window_df)

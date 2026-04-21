"""
feature_pipeline.py — AnomalyForge V3: Phase 2 Feature Pipeline
================================================================

Extrai features de microestrutura enriquecidas a partir de:
    - trades_YYYY-MM-DD.csv.gz          (aggressor, VPIN, OFI)
    - microstructure_YYYY-MM-DD.csv.gz  (spread z-score, depth ratio, toxicity)
    - depth_updates_YYYY-MM-DD.csv.gz   (cancel rate)

Features computadas (17 total):
    VPIN / Order Flow:
        vpin_30m, vpin_5m              — Volume Probability of Informed Trading
        ofi_5m, ofi_15m, ofi_30m      — Order Flow Imbalance rolling
        trade_intensity_5m             — trades por minuto (últimos 5min)
        avg_trade_size_5m              — tamanho médio de trade
        large_trade_fraction_5m        — fracção de trades >5 contratos
        buy_aggressor_ratio_5m         — fracção de aggressores buy
        delta_acceleration             — slope do cumulative_delta

    Microestrutura enriquecida:
        spread_zscore_30m              — z-score do spread vs 30min
        spread_zscore_2h               — z-score do spread vs 2h
        depth_ratio                    — total_bid / total_ask
        depth_ratio_zscore_30m         — z-score do depth_ratio
        toxicity_trend                 — slope do toxicity_score últimas 10 leituras
        dom_persistence                — fracção de leituras com |dom| > 0.3

    Cancelamentos:
        cancel_rate_5m                 — delete / total actions nos top 5 níveis

Uso:
    extractor = MicrostructureFeatureExtractor()
    features = extractor.extract(as_of_ts)  # → dict[str, float]
"""

from __future__ import annotations

import gzip
import logging
import os
import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

_logger = logging.getLogger("nextgen.anomaly_forge_v3.features")

# ── Configuração ──────────────────────────────────────────────────────────────

DATA_ROOT = Path(os.getenv("LEVEL2_DATA_DIR", "C:/data/level2/_gc_xcec"))

# Janelas temporais
WINDOW_5M  = 5
WINDOW_15M = 15
WINDOW_30M = 30
WINDOW_2H  = 120

# VPIN: tamanho do bucket como fracção do volume médio diário
VPIN_BUCKET_FRACTION = 1 / 50  # 50 buckets por sessão (academic standard)
VPIN_APPROX_DAILY_VOL = 250_000  # GC contratos/dia (aproximação conservadora)
VPIN_BUCKET_SIZE = VPIN_APPROX_DAILY_VOL * VPIN_BUCKET_FRACTION  # 5000 contratos/bucket

LARGE_TRADE_THRESHOLD = 5.0   # contratos (>5 = "institutional-size" em GC)
DOM_EXTREME_THRESHOLD = 0.30  # |dom_imbalance| considerado significativo
CANCEL_RATE_MAX_LEVELS = 5    # top N níveis para cancel rate
TOXICITY_TREND_WINDOW = 10    # leituras para calcular slope

# Cache TTL
CACHE_TTL_SECONDS = 15

# Valor NaN para features sem dados suficientes
NAN_FILL = 0.0   # substituir NaN por 0 — conservador para o modelo


# ── Nomes das features (contrato fixo para Phase 3) ───────────────────────────

FEATURE_NAMES = [
    # VPIN / Order Flow
    "vpin_30m",
    "vpin_5m",
    "ofi_5m",
    "ofi_15m",
    "ofi_30m",
    "trade_intensity_5m",
    "avg_trade_size_5m",
    "large_trade_fraction_5m",
    "buy_aggressor_ratio_5m",
    "delta_acceleration",
    # Microestrutura enriquecida
    "spread_zscore_30m",
    "spread_zscore_2h",
    "depth_ratio",
    "depth_ratio_zscore_30m",
    "toxicity_trend",
    "dom_persistence",
    # Cancelamentos
    "cancel_rate_5m",
]


# ── Helpers de I/O ─────────────────────────────────────────────────────────────

def _read_csv_gz(path: Path, ts_col: str) -> Optional[pd.DataFrame]:
    """Lê CSV gzip e retorna DataFrame com índice DatetimeIndex UTC (sem tz)."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, compression="gzip")
        if ts_col not in df.columns:
            # Fallback: tentar nomes alternativos
            for alt in ("recv_ts", "recv_timestamp"):
                if alt in df.columns:
                    ts_col = alt
                    break
            else:
                return None
        df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="ISO8601")
        df[ts_col] = df[ts_col].dt.tz_localize(None)  # remover tz → naive UTC
        df = df.set_index(ts_col).sort_index()
        return df
    except Exception as e:
        _logger.warning("Feature pipeline: erro a ler %s — %s", path.name, e)
        return None


def _window(df: pd.DataFrame, as_of: pd.Timestamp, minutes: int) -> pd.DataFrame:
    """Filtra DataFrame para a janela [as_of-minutes, as_of]."""
    cutoff = as_of - pd.Timedelta(minutes=minutes)
    return df[(df.index >= cutoff) & (df.index <= as_of)]


def _slope(series: pd.Series) -> float:
    """Slope linear (OLS) de uma série — retorna 0 se insuficiente."""
    s = series.dropna()
    if len(s) < 3:
        return 0.0
    x = np.arange(len(s), dtype=float)
    try:
        slope = float(np.polyfit(x, s.values, 1)[0])
        return slope if np.isfinite(slope) else 0.0
    except Exception:
        return 0.0


def _zscore(value: float, series: pd.Series) -> float:
    """Z-score do value em relação à série. Retorna 0 se std=0."""
    s = series.dropna()
    if len(s) < 3:
        return 0.0
    mu, std = float(s.mean()), float(s.std())
    if std == 0:
        return 0.0
    z = (value - mu) / std
    return float(np.clip(z, -10.0, 10.0))


# ── Cálculos de Features ──────────────────────────────────────────────────────

def _compute_vpin(trades_w: pd.DataFrame, bucket_size: float = VPIN_BUCKET_SIZE) -> float:
    """
    VPIN (simplified rolling-window approximation).
    τ = |buy_vol - sell_vol| / total_vol
    Usa session_buy_volume e session_sell_volume já acumulados nas trades.
    """
    if trades_w is None or len(trades_w) < 5:
        return NAN_FILL
    try:
        # Usar sessão acumulada: diferença entre primeiro e último da janela
        col_buy = "session_buy_volume"
        col_sell = "session_sell_volume"
        if col_buy not in trades_w.columns or col_sell not in trades_w.columns:
            # Fallback: calcular directamente do aggressor
            buys  = trades_w[trades_w["aggressor"].str.lower() == "buy"]["size"].sum()
            sells = trades_w[trades_w["aggressor"].str.lower() == "sell"]["size"].sum()
        else:
            buy_start  = float(trades_w[col_buy].iloc[0])
            buy_end    = float(trades_w[col_buy].iloc[-1])
            sell_start = float(trades_w[col_sell].iloc[0])
            sell_end   = float(trades_w[col_sell].iloc[-1])
            buys  = max(0.0, buy_end  - buy_start)
            sells = max(0.0, sell_end - sell_start)

        total = buys + sells
        if total == 0:
            return NAN_FILL
        vpin = abs(buys - sells) / total
        return float(np.clip(vpin, 0.0, 1.0))
    except Exception:
        return NAN_FILL


def _compute_ofi(trades_w: pd.DataFrame) -> float:
    """Order Flow Imbalance = (buy_vol - sell_vol) / total_vol. Signed [-1, +1]."""
    if trades_w is None or len(trades_w) < 2:
        return NAN_FILL
    try:
        sizes = pd.to_numeric(trades_w["size"], errors="coerce").fillna(0)
        agg   = trades_w["aggressor"].str.lower()
        buys  = sizes[agg == "buy"].sum()
        sells = sizes[agg == "sell"].sum()
        total = buys + sells
        if total == 0:
            return NAN_FILL
        return float(np.clip((buys - sells) / total, -1.0, 1.0))
    except Exception:
        return NAN_FILL


def _compute_cancel_rate(updates_w: pd.DataFrame, max_levels: int = CANCEL_RATE_MAX_LEVELS) -> float:
    """
    Cancel rate = deletes / total actions nos top max_levels níveis.
    Aproximação: spoofing = alta taxa de delete sem fill.
    """
    if updates_w is None or len(updates_w) < 5:
        return NAN_FILL
    try:
        # Filtrar top N níveis
        if "level_index" in updates_w.columns:
            lvl = pd.to_numeric(updates_w["level_index"], errors="coerce")
            updates_w = updates_w[lvl <= max_levels]
        if len(updates_w) == 0:
            return NAN_FILL
        actions = updates_w["action"].str.lower()
        deletes = (actions == "delete").sum()
        total   = len(actions)
        return float(np.clip(deletes / total, 0.0, 1.0)) if total > 0 else NAN_FILL
    except Exception:
        return NAN_FILL


# ── Extractor Principal ───────────────────────────────────────────────────────

class MicrostructureFeatureExtractor:
    """
    Extrai 17 features de microestrutura enriquecidas para o AnomalyForge V3.

    Uso em produção (real-time):
        extractor = MicrostructureFeatureExtractor()
        features = extractor.extract(datetime.utcnow())

    Uso em backtest (historical):
        features = extractor.extract(specific_timestamp)
    """

    def __init__(self, data_root: Path = DATA_ROOT):
        self._root = data_root
        self._cache: Dict[str, Dict[str, Optional[pd.DataFrame]]] = {}
        self._cache_ts: Dict[str, float] = {}

    # ── Interface Pública ─────────────────────────────────────────────────────

    def extract(self, as_of: datetime) -> Dict[str, float]:
        """
        Extrai todas as 17 features para o timestamp as_of.
        Retorna dict {feature_name: value}. Nunca lança excepção.
        """
        try:
            return self._extract_safe(as_of)
        except Exception as e:
            _logger.error("Feature pipeline: excepção em extract(%s): %s", as_of, e)
            return {k: NAN_FILL for k in FEATURE_NAMES}

    def extract_array(self, as_of: datetime) -> np.ndarray:
        """Extrai features como array numpy ordenado por FEATURE_NAMES."""
        d = self.extract(as_of)
        return np.array([d.get(k, NAN_FILL) for k in FEATURE_NAMES], dtype=np.float32)

    # ── Implementação ─────────────────────────────────────────────────────────

    def _extract_safe(self, as_of: datetime) -> Dict[str, float]:
        date_str = as_of.strftime("%Y-%m-%d")
        as_of_ts = pd.Timestamp(as_of).tz_localize(None) if as_of.tzinfo is None \
                   else pd.Timestamp(as_of).tz_convert("UTC").tz_localize(None)

        trades, micro, updates = self._load_day(date_str)

        # Windows por duração
        t5  = _window(trades,  as_of_ts, WINDOW_5M)  if trades  is not None else pd.DataFrame()
        t15 = _window(trades,  as_of_ts, WINDOW_15M) if trades  is not None else pd.DataFrame()
        t30 = _window(trades,  as_of_ts, WINDOW_30M) if trades  is not None else pd.DataFrame()
        m30 = _window(micro,   as_of_ts, WINDOW_30M) if micro   is not None else pd.DataFrame()
        m2h = _window(micro,   as_of_ts, WINDOW_2H)  if micro   is not None else pd.DataFrame()
        u5  = _window(updates, as_of_ts, WINDOW_5M)  if updates is not None else pd.DataFrame()

        feats: Dict[str, float] = {}

        # ── VPIN ────────────────────────────────────────────────────────────
        feats["vpin_30m"] = _compute_vpin(t30)
        feats["vpin_5m"]  = _compute_vpin(t5)

        # ── OFI ─────────────────────────────────────────────────────────────
        feats["ofi_5m"]  = _compute_ofi(t5)
        feats["ofi_15m"] = _compute_ofi(t15)
        feats["ofi_30m"] = _compute_ofi(t30)

        # ── Trade intensity ──────────────────────────────────────────────────
        n5 = len(t5)
        if n5 > 0:
            feats["trade_intensity_5m"] = n5 / WINDOW_5M
            sizes5 = pd.to_numeric(t5["size"], errors="coerce").dropna()
            feats["avg_trade_size_5m"]       = float(sizes5.mean()) if len(sizes5) > 0 else NAN_FILL
            feats["large_trade_fraction_5m"] = float((sizes5 > LARGE_TRADE_THRESHOLD).mean()) if len(sizes5) > 0 else NAN_FILL
            agg5 = t5["aggressor"].str.lower()
            total5 = len(agg5)
            feats["buy_aggressor_ratio_5m"]  = float((agg5 == "buy").sum() / total5) if total5 > 0 else 0.5
        else:
            feats["trade_intensity_5m"]      = NAN_FILL
            feats["avg_trade_size_5m"]       = NAN_FILL
            feats["large_trade_fraction_5m"] = NAN_FILL
            feats["buy_aggressor_ratio_5m"]  = 0.5

        # ── Delta acceleration ───────────────────────────────────────────────
        if len(t30) >= 5 and "cumulative_delta" in t30.columns:
            delta_series = pd.to_numeric(t30["cumulative_delta"], errors="coerce")
            feats["delta_acceleration"] = _slope(delta_series)
        else:
            feats["delta_acceleration"] = NAN_FILL

        # ── Spread z-scores ──────────────────────────────────────────────────
        if "spread" in m30.columns and len(m30) >= 3:
            spread30 = pd.to_numeric(m30["spread"], errors="coerce")
            current_spread = float(spread30.iloc[-1]) if not spread30.empty else 0.0
            feats["spread_zscore_30m"] = _zscore(current_spread, spread30.iloc[:-1])
        else:
            feats["spread_zscore_30m"] = NAN_FILL

        if "spread" in m2h.columns and len(m2h) >= 5:
            spread2h = pd.to_numeric(m2h["spread"], errors="coerce")
            current_spread = float(spread2h.iloc[-1]) if not spread2h.empty else 0.0
            feats["spread_zscore_2h"] = _zscore(current_spread, spread2h.iloc[:-1])
        else:
            feats["spread_zscore_2h"] = NAN_FILL

        # ── Depth ratio + z-score ────────────────────────────────────────────
        if all(c in m30.columns for c in ("total_bid_size", "total_ask_size")) and len(m30) >= 3:
            bid30 = pd.to_numeric(m30["total_bid_size"], errors="coerce")
            ask30 = pd.to_numeric(m30["total_ask_size"], errors="coerce")
            total30 = bid30 + ask30
            nonzero = total30 > 0
            ratio30 = pd.Series(np.where(nonzero, bid30 / ask30.clip(lower=0.01), 1.0),
                                index=m30.index)
            current_ratio = float(ratio30.iloc[-1])
            feats["depth_ratio"] = current_ratio
            feats["depth_ratio_zscore_30m"] = _zscore(current_ratio, ratio30.iloc[:-1])
        else:
            feats["depth_ratio"]            = 1.0  # neutro
            feats["depth_ratio_zscore_30m"] = NAN_FILL

        # ── Toxicity trend ───────────────────────────────────────────────────
        if "toxicity_score" in m30.columns and len(m30) >= TOXICITY_TREND_WINDOW:
            tox = pd.to_numeric(m30["toxicity_score"], errors="coerce")
            feats["toxicity_trend"] = _slope(tox.tail(TOXICITY_TREND_WINDOW))
        else:
            feats["toxicity_trend"] = NAN_FILL

        # ── DOM persistence ──────────────────────────────────────────────────
        if "dom_imbalance" in m30.columns and len(m30) >= 3:
            dom30 = pd.to_numeric(m30["dom_imbalance"], errors="coerce").dropna()
            feats["dom_persistence"] = float((dom30.abs() > DOM_EXTREME_THRESHOLD).mean())
        else:
            feats["dom_persistence"] = NAN_FILL

        # ── Cancel rate ──────────────────────────────────────────────────────
        feats["cancel_rate_5m"] = _compute_cancel_rate(u5)

        # Garantir que todas as features estão presentes e são finitas
        for k in FEATURE_NAMES:
            v = feats.get(k, NAN_FILL)
            if not np.isfinite(v):
                feats[k] = NAN_FILL

        return feats

    # ── Data Loading com Cache ─────────────────────────────────────────────────

    def _load_day(
        self, date_str: str
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """Carrega trades, microstructure e depth_updates para uma data. Usa cache TTL."""
        now = _time.time()
        if (
            date_str in self._cache
            and now - self._cache_ts.get(date_str, 0) < CACHE_TTL_SECONDS
        ):
            c = self._cache[date_str]
            return c["trades"], c["micro"], c["updates"]

        trades  = self._load_trades(date_str)
        micro   = self._load_micro(date_str)
        updates = self._load_updates(date_str)

        self._cache[date_str] = {"trades": trades, "micro": micro, "updates": updates}
        self._cache_ts[date_str] = now
        return trades, micro, updates

    def _load_trades(self, date_str: str) -> Optional[pd.DataFrame]:
        path = self._root / f"trades_{date_str}.csv.gz"
        df = _read_csv_gz(path, "recv_timestamp")
        if df is None:
            return None
        return df

    def _load_micro(self, date_str: str) -> Optional[pd.DataFrame]:
        path = self._root / f"microstructure_{date_str}.csv.gz"
        df = _read_csv_gz(path, "recv_timestamp")
        if df is None:
            # Tentar schema v1
            df = _read_csv_gz(path, "recv_ts")
        return df

    def _load_updates(self, date_str: str) -> Optional[pd.DataFrame]:
        path = self._root / f"depth_updates_{date_str}.csv.gz"
        return _read_csv_gz(path, "recv_timestamp")

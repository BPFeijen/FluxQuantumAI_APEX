"""
tick_reconstructor.py — Reconstrói MarketTick a partir de microstructure_*.csv.gz.

Usa os ficheiros microstructure gerados pelo C# Quantower indicator (1s de resolução).
Estes ficheiros têm dom_imbalance, total_bid_size, total_ask_size e cumulative_delta
calculados pelo C# com AggregateMethod.ByPriceLVL (após fix 2026-04-11).

NOTA HISTÓRICA:
  Antes do fix, o C# usava AggregateMethod.None → ordens individuais (~1 contrato cada)
  → total_bid/ask ≈ 100 (100 ordens × 1ct), dom_imbalance < ±0.28.
  Com ByPriceLVL → ordens agregadas por nível → GC: 1000-5000ct/lado, imbalance real ±0.50+.

Ficheiros fonte: C:/data/level2/_gc_xcec/microstructure_YYYY-MM-DD.csv.gz
Campos usados: recv_timestamp, dom_imbalance, total_bid_size, total_ask_size,
               spread, cumulative_delta, wall_bid_size, wall_ask_size,
               sweep_detected, absorption_detected

Uso:
    reconstructor = TickReconstructor(date="2026-04-09")
    tick = reconstructor.nearest_tick(timestamp)
"""

from __future__ import annotations

import csv
import gzip
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..common.base_provider import MarketTick
from ..common.config import PRODUCTION_L2_DIR

_logger = logging.getLogger("nextgen.tick_reconstructor")


class MicrostructureSnapshot:
    """Row do ficheiro microstructure_*.csv.gz convertido em MarketTick-ready."""

    __slots__ = (
        "recv_ts", "dom_imbalance", "total_bid_size", "total_ask_size",
        "spread", "cumulative_delta", "best_bid", "best_ask",
        "wall_bid_size", "wall_ask_size", "sweep_detected", "absorption_detected",
        "large_bid_count", "large_ask_count", "source_file",
    )

    def __init__(self, row: dict, source_file: str):
        def _f(k, d=0.0):
            v = row.get(k, "")
            if v in ("", "None", "null", None):
                return d
            try:
                return float(v)
            except (ValueError, TypeError):
                return d

        def _b(k):
            return str(row.get(k, "")).lower() in ("true", "1", "yes")

        self.recv_ts         = row.get("recv_timestamp", "")
        self.dom_imbalance   = _f("dom_imbalance")
        self.total_bid_size  = _f("total_bid_size")
        self.total_ask_size  = _f("total_ask_size")
        self.spread          = _f("spread")
        self.cumulative_delta= _f("cumulative_delta")
        self.best_bid        = _f("best_bid")
        self.best_ask        = _f("best_ask")
        self.wall_bid_size   = _f("wall_bid_size")
        self.wall_ask_size   = _f("wall_ask_size")
        self.sweep_detected  = _b("sweep_detected")
        self.absorption_detected = _b("absorption_detected")
        self.large_bid_count = int(_f("large_bid_count"))
        self.large_ask_count = int(_f("large_ask_count"))
        self.source_file     = source_file

    def to_market_tick(self, override_cumulative_delta: Optional[float] = None) -> MarketTick:
        try:
            ts = datetime.fromisoformat(self.recv_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        cd = override_cumulative_delta if override_cumulative_delta is not None else self.cumulative_delta

        return MarketTick(
            timestamp        = ts,
            spread           = round(self.spread, 4),
            total_bid_depth  = round(self.total_bid_size, 1),
            total_ask_depth  = round(self.total_ask_size, 1),
            book_imbalance   = round(self.dom_imbalance, 6),
            cumulative_delta = round(cd, 1),
            source_file      = self.source_file,
        )


class TickReconstructor:
    """
    Lê microstructure_YYYY-MM-DD.csv.gz e indexa por recv_timestamp.

    Resolução: 1 segundo (MicrostructureIntervalSeconds=1 no C#).
    Lookup: O(log n) via busca binária por timestamp.

    Qualidade dos dados (pós-fix ByPriceLVL):
        dom_imbalance  : real, range esperado ±0.30 a ±0.80 em GC
        total_bid_size : agregado por nível de preço (GC: ~1000-5000 contratos)
        cumulative_delta: session cumulative desde open
    """

    def __init__(self, date: str, l2_dir: Path = PRODUCTION_L2_DIR):
        self._date      = date
        self._path      = l2_dir / f"microstructure_{date}.csv.gz"
        self._snapshots: List[MicrostructureSnapshot] = []
        self._ts_index:  List[datetime]               = []
        self._loaded    = False
        self._load()

    def _load(self):
        if not self._path.exists():
            # Tentar .fixed.csv.gz (formato alternativo)
            alt_path = self._path.parent / f"microstructure_{self._date}.fixed.csv.gz"
            if alt_path.exists():
                self._path = alt_path
            else:
                _logger.debug("microstructure não encontrado: %s", self._path)
                return

        _logger.info("TickReconstructor: a carregar %s", self._path.name)
        count = 0
        try:
            with gzip.open(self._path, "rt", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    snap = MicrostructureSnapshot(row, self._path.name)
                    ts_str = snap.recv_ts
                    if not ts_str:
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    self._snapshots.append(snap)
                    self._ts_index.append(ts)
                    count += 1
        except Exception as e:
            _logger.warning("TickReconstructor: erro a ler %s — %s", self._path.name, e)
            return

        self._loaded = True

        # Aviso sobre qualidade dos dados (detectar se ainda está com None aggregation)
        if count > 0:
            avg_bid = sum(s.total_bid_size for s in self._snapshots[:100]) / min(100, count)
            if avg_bid < 200:
                _logger.warning(
                    "TickReconstructor: total_bid_size médio=%.0f — possível AggregateMethod.None "
                    "ainda activo no C# indicator. Recarregar FluxQuantumAI.cs em Quantower.",
                    avg_bid
                )
            else:
                _logger.info(
                    "TickReconstructor: %d snapshots carregados para %s | avg_bid=%.0f (ByPriceLVL OK)",
                    count, self._date, avg_bid
                )

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def row_count(self) -> int:
        return len(self._snapshots)

    def nearest_tick(
        self,
        target: datetime,
        override_cumulative_delta: Optional[float] = None,
        max_gap_seconds: float = 30.0,
    ) -> Optional[MarketTick]:
        """
        Devolve o MarketTick do snapshot mais próximo de `target`.

        Parameters
        ----------
        target : datetime
        override_cumulative_delta : float | None
            Substituir o cumulative_delta do ficheiro pelo valor do live_log
            (útil quando o live_log tem macro_delta mais preciso)
        max_gap_seconds : float
            Tolerância máxima em segundos (default 30s — microstructure a 1s tem cobertura densa)
        """
        if not self._snapshots:
            return None

        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)

        # Busca binária
        lo, hi = 0, len(self._ts_index) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self._ts_index[mid] < target:
                lo = mid + 1
            else:
                hi = mid

        best_idx = lo
        if lo > 0:
            gap_lo   = abs((self._ts_index[lo]   - target).total_seconds())
            gap_lo_1 = abs((self._ts_index[lo-1] - target).total_seconds())
            if gap_lo_1 < gap_lo:
                best_idx = lo - 1

        gap = abs((self._ts_index[best_idx] - target).total_seconds())
        if gap > max_gap_seconds:
            _logger.debug(
                "TickReconstructor: gap %.1fs > %.1fs para %s",
                gap, max_gap_seconds, target.isoformat(),
            )
            return None

        return self._snapshots[best_idx].to_market_tick(
            override_cumulative_delta=override_cumulative_delta
        )

    def imbalance_stats(self) -> dict:
        """Retorna estatísticas de qualidade do imbalance (útil para diagnóstico)."""
        if not self._snapshots:
            return {}
        imbs = [s.dom_imbalance for s in self._snapshots]
        bids = [s.total_bid_size for s in self._snapshots]
        return {
            "rows":           len(imbs),
            "imb_min":        round(min(imbs), 4),
            "imb_max":        round(max(imbs), 4),
            "imb_mean":       round(sum(imbs) / len(imbs), 4),
            "imb_gt_30pct":   sum(1 for i in imbs if abs(i) > 0.30),
            "avg_bid_size":   round(sum(bids) / len(bids), 1),
            "data_quality":   "GOOD_ByPriceLVL" if sum(bids) / len(bids) > 200 else "DEGRADED_None",
        }

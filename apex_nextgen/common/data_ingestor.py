"""
data_ingestor.py — Opção A-Plus: watchdog sobre CSVs de produção.

Monitora o directório de microestrutura da produção via eventos de sistema de
ficheiros (inotify/FSEvents/ReadDirectoryChangesW). Zero polling. Zero risco
para a produção — apenas leitura.

Quando a produção escreve um novo row no microstructure CSV, o DataIngestor
detecta a alteração em milissegundos, lê as novas linhas e emite MarketTick
para os consumers registados (o FoxyzeMaster e o daily_scorecard).

Uso:
    ingestor = DataIngestor(callback=on_tick)
    ingestor.start()   # non-blocking
    ...
    ingestor.stop()
"""

from __future__ import annotations

import csv
import gzip
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileModifiedEvent, FileCreatedEvent, PatternMatchingEventHandler
from watchdog.observers import Observer

from .base_provider import MarketTick
from .config import PRODUCTION_L2_DIR

_logger = logging.getLogger("nextgen.ingestor")

# Colunas esperadas no microstructure CSV de produção
_SPREAD_COL     = "spread"
_BID_DEPTH_COL  = "total_bid_depth"
_ASK_DEPTH_COL  = "total_ask_depth"
_IMBALANCE_COL  = "book_imbalance"
_TIMESTAMP_COL  = "recv_timestamp"

# Aliases para todos os formatos de ficheiros suportados:
#   microstructure_*.csv.gz (PRIMARY — 1s resolution, ByPriceLVL)
#   XAU_ticks_*.csv         (LEGACY — Nov-Dez 2025)
_COL_ALIASES = {
    "book_imbalance":   ["dom_imbalance", "imbalance"],
    "total_bid_depth":  ["total_bid_size", "total_bids"],
    "total_ask_depth":  ["total_ask_size", "total_asks"],
    "cumulative_delta": ["cumulative_delta", "delta"],
    "recv_timestamp":   ["recv_timestamp", "timestamp"],
}


class _MicrostructureHandler(PatternMatchingEventHandler):
    """Handler do watchdog — invocado quando o CSV de produção é alterado."""

    def __init__(self, callback: Callable[[MarketTick], None]):
        super().__init__(
            patterns=[
                # PRIMARY: microstructure = 1s resolution, ByPriceLVL (post fix 2026-04-11)
                # dom_imbalance, total_bid_size, total_ask_size, cumulative_delta já calculados
                "*microstructure_*.csv.gz*",
                "*microstructure_*.csv*",
                # LEGACY: XAU_ticks (Nov-Dez 2025, format antigo)
                "*XAU_ticks_*.csv*",
                # depth_snapshots: desactivado como fonte primária (AggregateMethod.None bug)
                # mantido para compatibilidade retroactiva mas não deve ser usado para imbalance
                # "*depth_snapshots_*.csv.gz*",
            ],
            ignore_directories=True,
            case_sensitive=False,
        )
        self._callback     = callback
        self._file_offsets: dict[str, int] = {}  # path → bytes lidos até agora
        self._lock         = threading.Lock()

    def _read_new_rows(self, path: str) -> list[dict]:
        """Lê apenas as linhas novas desde a última leitura."""
        with self._lock:
            offset = self._file_offsets.get(path, 0)

        try:
            if path.endswith(".gz"):
                opener = lambda: gzip.open(path, "rt", encoding="utf-8")
            else:
                opener = lambda: open(path, "r", encoding="utf-8", newline="")

            rows = []
            with opener() as f:
                f.seek(offset)
                reader = csv.DictReader(f) if offset == 0 else None

                if offset == 0:
                    # Primeira leitura: saltar header e ler todas as linhas
                    for row in reader:
                        rows.append(row)
                    new_offset = f.tell()
                else:
                    # Leituras seguintes: raw lines (sem re-parsear header)
                    # Ler header da posição 0 para obter field names
                    f.seek(0)
                    header_reader = csv.DictReader(f)
                    fieldnames = header_reader.fieldnames or []
                    f.seek(offset)
                    for line in f:
                        if line.strip():
                            values = next(csv.reader([line]))
                            if len(values) == len(fieldnames):
                                rows.append(dict(zip(fieldnames, values)))
                    new_offset = f.tell()

            with self._lock:
                self._file_offsets[path] = new_offset

            return rows

        except Exception as e:
            _logger.debug("Erro a ler %s: %s", path, e)
            return []

    @staticmethod
    def _build_fluxfox_features(row: dict, ts: datetime) -> dict:
        """
        Extrai features SCHEMA_FLUXFOX_V2 disponíveis no microstructure CSV.

        O CSV de produção (~41 cols) fornece ~12 das 26 features do schema.
        Features ausentes ficam em falta no dict → AnomalyForge imputa com
        training mean (normaliza para 0.0, zero contribuição ao MSE).

        Mapeamento (fonte → SCHEMA_FLUXFOX_V2):
          dom_imbalance       → F08 dom_imbalance_at_level
          absorption_ratio    → F07 aggressor_volume_absorbed, F09 book_depth_recovery_rate
          trades_per_second   → F10 trade_intensity
          hour/minute of ts   → F11 session_label, F11a hour_of_day, F11b minute_of_hour
          cumulative_delta    → F15 cumulative_absorbed_volume
          bar_delta           → F19 underflow_volume (net flow proxy), F21 ofi_rate
          volume_per_second × mid_price → F22 vot
          spread / 0.1        → F23 inst_volatility_ticks (GC tick = $0.1)
        """
        def _fv(key: str, default: float = None):
            val = row.get(key)
            if val is None or val == "":
                return default
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        feats: dict = {}

        # F07 — aggressor_volume_absorbed
        ab = _fv("absorption_ratio")
        if ab is not None:
            feats["aggressor_volume_absorbed"] = ab

        # F08 — dom_imbalance_at_level
        di = _fv("dom_imbalance")
        if di is not None:
            feats["dom_imbalance_at_level"] = di

        # F09 — book_depth_recovery_rate (proxy: absorbed/100 normalizado)
        if ab is not None:
            feats["book_depth_recovery_rate"] = ab / 100.0

        # F10 — trade_intensity (trades/s)
        tps = _fv("trades_per_second")
        if tps is not None:
            feats["trade_intensity"] = tps

        # F11 — session_label (0=pre/overnight, 1=London 8-14h UTC, 2=NY 14-21h UTC)
        hr = ts.hour
        if 14 <= hr < 21:
            feats["session_label"] = 2.0
        elif 8 <= hr < 14:
            feats["session_label"] = 1.0
        else:
            feats["session_label"] = 0.0

        # F11a — hour_of_day, F11b — minute_of_hour
        feats["hour_of_day"]    = float(ts.hour)
        feats["minute_of_hour"] = float(ts.minute)

        # F15 — cumulative_absorbed_volume
        cd = _fv("cumulative_delta")
        if cd is not None:
            feats["cumulative_absorbed_volume"] = cd

        # F19 — underflow_volume (bar_delta proxy)
        bd = _fv("bar_delta")
        if bd is not None:
            feats["underflow_volume"] = bd

        # F21 — ofi_rate (order flow imbalance: bar_delta per second = lots/s)
        if bd is not None:
            feats["ofi_rate"] = bd

        # F22 — vot (Velocity of Tape: monetary mass/s = volume_per_second × mid_price / 1000)
        vps = _fv("volume_per_second")
        mp  = _fv("mid_price")
        if vps is not None and mp is not None and mp > 0:
            feats["vot"] = vps * mp / 1000.0

        # F23 — inst_volatility_ticks (spread in GC ticks; tick size = $0.1/oz)
        sp = _fv("spread")
        if sp is not None and sp > 0:
            feats["inst_volatility_ticks"] = sp / 0.1

        return feats

    def _row_to_tick(self, row: dict, source_file: str) -> Optional[MarketTick]:
        """Converte uma row do CSV numa MarketTick."""
        try:
            ts_raw = row.get(_TIMESTAMP_COL, row.get("timestamp", ""))
            try:
                ts = datetime.fromisoformat(str(ts_raw))
            except (ValueError, TypeError):
                ts = datetime.utcnow()

            # Bid/Ask levels (bid_0..bid_9, ask_0..ask_9)
            bid_levels = {}
            ask_levels = {}
            for i in range(10):
                bk = f"bid_{i}"
                ak = f"ask_{i}"
                if bk in row:
                    try:
                        bid_levels[i] = float(row[bk])
                    except (ValueError, TypeError):
                        pass
                if ak in row:
                    try:
                        ask_levels[i] = float(row[ak])
                    except (ValueError, TypeError):
                        pass

            def _f(key: str, default: float = 0.0) -> float:
                """Lê campo do row com suporte a aliases entre formatos de ficheiros."""
                # Tentar chave directa
                val = row.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        pass
                # Tentar aliases
                for alias in _COL_ALIASES.get(key, []):
                    val = row.get(alias)
                    if val is not None:
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            pass
                return default

            return MarketTick(
                timestamp        = ts,
                spread           = _f(_SPREAD_COL),
                total_bid_depth  = _f(_BID_DEPTH_COL),
                total_ask_depth  = _f(_ASK_DEPTH_COL),
                book_imbalance   = _f(_IMBALANCE_COL),
                bid_levels       = bid_levels,
                ask_levels       = ask_levels,
                trade_volume     = _f("trade_volume"),
                cumulative_delta = _f("cumulative_delta"),
                fluxfox_features = self._build_fluxfox_features(row, ts),
                source_file      = os.path.basename(source_file),
                ingest_latency_ms = 0.0,
            )
        except Exception as e:
            _logger.debug("Erro a converter row: %s", e)
            return None

    def on_modified(self, event: FileModifiedEvent):
        self._process(event.src_path)

    def on_created(self, event: FileCreatedEvent):
        self._process(event.src_path)

    def _process(self, path: str):
        t0 = time.perf_counter()
        rows = self._read_new_rows(path)
        for row in rows:
            tick = self._row_to_tick(row, path)
            if tick is not None:
                tick.ingest_latency_ms = (time.perf_counter() - t0) * 1000
                try:
                    self._callback(tick)
                except Exception as e:
                    _logger.warning("Erro no callback do tick: %s", e)


class DataIngestor:
    """
    Ingestor de dados em tempo real — Opção A-Plus.

    Monitora o directório de produção via watchdog e emite MarketTick
    para o callback registado assim que a produção escreve novos dados.

    Parâmetros
    ----------
    callback : Callable[[MarketTick], None]
        Função chamada para cada novo tick. Deve ser não-bloqueante.
    watch_dir : Path
        Directório a monitorar (default: PRODUCTION_L2_DIR).
    recursive : bool
        Monitorar subdirectórios também.
    """

    def __init__(
        self,
        callback: Callable[[MarketTick], None],
        watch_dir: Path = PRODUCTION_L2_DIR,
        recursive: bool = False,
    ):
        self._callback  = callback
        self._watch_dir = Path(watch_dir)
        self._recursive = recursive
        self._handler   = _MicrostructureHandler(callback=callback)
        self._observer  = Observer()
        self._running   = False

    def start(self):
        """Inicia monitoramento em background thread. Non-blocking."""
        if not self._watch_dir.exists():
            _logger.warning(
                "Directório de produção não encontrado: %s — ingestor em modo standby",
                self._watch_dir,
            )
            return

        self._observer.schedule(
            self._handler,
            str(self._watch_dir),
            recursive=self._recursive,
        )
        self._observer.start()
        self._running = True
        _logger.info(
            "DataIngestor iniciado — a monitorar %s (watchdog FSevents)",
            self._watch_dir,
        )

    def stop(self):
        """Para o monitoramento."""
        if self._running:
            self._observer.stop()
            self._observer.join()
            self._running = False
            _logger.info("DataIngestor parado.")

    @property
    def is_running(self) -> bool:
        return self._running

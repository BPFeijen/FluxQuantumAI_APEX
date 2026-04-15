"""
Quantower Level 2 Data Capture API (CANONICAL ENTRYPOINT)

Captura dados de market depth, trades e eventos do Quantower via HTTP.
Armazena em CSV com suporte a concorrência e buffering.

CANONICAL: Este é o único entrypoint oficial para captura Level2.
Outros arquivos (api.py, backup.py) devem importar deste módulo.
"""

__version__ = "2.0.0"
__build_id__ = "20251223-consolidated"

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import asyncio
import threading
import os
import csv
import gzip
import shutil
import logging
from collections import defaultdict
from pathlib import Path

# ==== CONFIGURA��O ==== #

# Path para dados - suporta Windows e Linux
DATA_DIR = os.getenv("DATA_DIR") or os.getenv("LEVEL2_DATA_DIR", "/data/level2")
BUFFER_SIZE = int(os.getenv("LEVEL2_BUFFER_SIZE", "500"))  # flush a cada N registros (maior = menos I/O)
FLUSH_INTERVAL_SECONDS = float(os.getenv("LEVEL2_FLUSH_INTERVAL", "2.0"))  # flush mais frequente
USE_COMPRESSION = os.getenv("LEVEL2_USE_COMPRESSION", "true").lower() == "true"  # gzip por padr�o
API_PORT = int(os.getenv("LEVEL2_API_PORT", "8000"))
API_HOST = os.getenv("LEVEL2_API_HOST", "0.0.0.0")

# S�mbolos permitidos (seguran�a) - aceita qualquer s�mbolo por padr�o
ALLOWED_SYMBOLS = None  # Aceita todos os s�mbolos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ==== BUFFER E LOCKS PARA ESCRITA THREAD-SAFE ==== #

class CSVBufferManager:
    """Gerencia buffers e locks para escrita thread-safe em CSVs."""

    def __init__(self, buffer_size: int = 100, flush_interval: float = 5.0):
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        self._buffers: Dict[str, List[list]] = defaultdict(list)
        self._headers: Dict[str, list] = {}
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._global_lock = threading.Lock()
        self._stats = {"rows_written": 0, "flushes": 0}

    def _get_lock(self, path: str) -> threading.Lock:
        """Retorna lock espec�fico para um arquivo."""
        with self._global_lock:
            if path not in self._locks:
                self._locks[path] = threading.Lock()
            return self._locks[path]

    def add_rows(self, path: str, header: list, rows: List[list]) -> int:
        """Adiciona rows ao buffer. Retorna n�mero de rows no buffer."""
        lock = self._get_lock(path)
        with lock:
            if path not in self._headers:
                self._headers[path] = header
            self._buffers[path].extend(rows)
            buffer_len = len(self._buffers[path])

            if buffer_len >= self.buffer_size:
                self._flush_file(path)
                return 0
            return buffer_len

    def _flush_file(self, path: str):
        """Escreve buffer em disco (deve ser chamado com lock)."""
        if path not in self._buffers or not self._buffers[path]:
            return

        rows = self._buffers[path]
        header = self._headers.get(path, [])

        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_header = not os.path.exists(path)

        open_func = gzip.open if USE_COMPRESSION else open
        mode = "at" if USE_COMPRESSION else "a"

        actual_path = f"{path}.gz" if USE_COMPRESSION else path
        write_header = not os.path.exists(actual_path)

        with open_func(actual_path, mode, newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header and header:
                writer.writerow(header)
            writer.writerows(rows)

        self._stats["rows_written"] += len(rows)
        self._stats["flushes"] += 1
        self._buffers[path].clear()
        logger.debug(f"Flushed {len(rows)} rows to {actual_path}")

    def flush_all(self):
        """For�a flush de todos os buffers."""
        with self._global_lock:
            paths = list(self._buffers.keys())

        for path in paths:
            lock = self._get_lock(path)
            with lock:
                self._flush_file(path)

    def get_stats(self) -> dict:
        """Retorna estat�sticas de escrita."""
        return {
            **self._stats,
            "pending_rows": sum(len(b) for b in self._buffers.values()),
            "active_files": len([b for b in self._buffers.values() if b])
        }


# Inst�ncia global do buffer manager
buffer_manager = CSVBufferManager(BUFFER_SIZE, FLUSH_INTERVAL_SECONDS)


# ==== BACKGROUND TASK PARA FLUSH PERI�DICO ==== #

async def periodic_flush():
    """Task que roda em background fazendo flush peri�dico."""
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        try:
            buffer_manager.flush_all()
        except Exception as e:
            logger.error(f"Error in periodic flush: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia lifecycle do app - inicia/para background tasks."""
    task = asyncio.create_task(periodic_flush())
    logger.info("Level2 API started - periodic flush enabled")
    yield
    task.cancel()
    buffer_manager.flush_all()  # flush final
    logger.info("Level2 API stopped - final flush completed")


app = FastAPI(
    title="Quantower Level 2 Capture API",
    description="API para captura de dados de market depth do Quantower",
    version="1.0.0",
    lifespan=lifespan
)

# CORS para permitir requests do Quantower (se necess�rio)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==== MODELOS DOS DADOS ==== #

class DepthSnapshotRow(BaseModel):
    symbol: str = Field(..., min_length=1, description="S�mbolo do instrumento")
    timestamp: str = Field(..., description="Timestamp ISO 8601")
    side: str  # Aceita qualquer string
    price: float  # Aceita qualquer valor
    size: float  # Aceita qualquer valor
    level_index: Optional[int] = None
    orders_count: Optional[int] = None
    exchange: Optional[str] = None


class DepthUpdateRow(BaseModel):
    symbol: str = Field(..., min_length=1)
    timestamp: str
    side: str  # Aceita qualquer string
    action: str  # Aceita qualquer string (insert/update/delete)
    price: float  # Aceita qualquer valor
    size: float  # Aceita qualquer valor
    level_index: Optional[int] = None
    orders_count: Optional[int] = None
    exchange: Optional[str] = None
    sequence: Optional[int] = None


class TradeRow(BaseModel):
    symbol: str = Field(..., min_length=1)
    timestamp: str
    price: float  # Aceita qualquer valor
    size: float  # Aceita qualquer valor
    aggressor: Optional[str] = "unknown"
    exchange: Optional[str] = None
    trade_id: Optional[str] = None
    flags: Optional[str] = None
    sequence: Optional[int] = None
    # Delta tracking fields
    delta_contribution: Optional[float] = None
    cumulative_delta: Optional[float] = None
    session_buy_volume: Optional[float] = None
    session_sell_volume: Optional[float] = None
    session_total_volume: Optional[float] = None
    session_trade_count: Optional[int] = None


class EventRow(BaseModel):
    symbol: str = Field(..., min_length=1)
    timestamp: str
    event_type: str = Field(..., min_length=1)
    details: Optional[str] = None


class MicrostructureRow(BaseModel):
    """Modelo para dados de microestrutura consolidados do Quantower."""
    symbol: str = Field(..., min_length=1)
    timestamp: str
    # DOM metrics
    dom_imbalance: Optional[float] = None
    total_bid_size: Optional[float] = None
    total_ask_size: Optional[float] = None
    # Delta metrics
    bar_delta: Optional[float] = None
    cumulative_delta: Optional[float] = None
    # Spread/Price
    spread: Optional[float] = None
    spread_percent: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    mid_price: Optional[float] = None
    # Pressure
    bid_pressure: Optional[float] = None
    ask_pressure: Optional[float] = None
    pressure_ratio: Optional[float] = None
    # Walls
    wall_bid_price: Optional[float] = None
    wall_bid_size: Optional[float] = None
    wall_ask_price: Optional[float] = None
    wall_ask_size: Optional[float] = None
    wall_distance_ticks: Optional[float] = None
    # Absorption
    absorption_detected: Optional[bool] = None
    absorption_ratio: Optional[float] = None
    absorption_side: Optional[str] = None
    # Large orders
    large_bid_count: Optional[int] = None
    large_ask_count: Optional[int] = None
    large_order_imbalance: Optional[float] = None
    # Activity
    trades_per_second: Optional[float] = None
    volume_per_second: Optional[float] = None
    # Advanced
    liquidity_shift: Optional[float] = None
    sweep_detected: Optional[bool] = None
    levels_swept: Optional[int] = None
    sweep_direction: Optional[str] = None
    toxicity_score: Optional[float] = None
    # POC
    poc_price: Optional[float] = None
    poc_volume: Optional[float] = None
    distance_to_poc: Optional[float] = None
    # Session stats
    session_volume: Optional[float] = None
    session_trade_count: Optional[int] = None
    session_buy_volume: Optional[float] = None
    session_sell_volume: Optional[float] = None
    exchange: Optional[str] = None


# ==== FUNÇÕES AUXILIARES ==== #

def extract_date_from_timestamp(timestamp_str: str) -> str:
    """Extrai data do timestamp ISO para organiza��o de arquivos."""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_file_path(symbol: str, data_type: str, date: str) -> str:
    """Gera path do arquivo CSV baseado no s�mbolo, tipo e data."""
    # Limpa caracteres inv�lidos para Windows (: / \ * ? " < > |)
    symbol_clean = symbol.lower()
    for char in [':', '/', '\\', '*', '?', '"', '<', '>', '|', ' ']:
        symbol_clean = symbol_clean.replace(char, '_')
    return os.path.join(DATA_DIR, symbol_clean, f"{data_type}_{date}.csv")


def validate_symbol(symbol: str) -> bool:
    """Valida se o s�mbolo � permitido."""
    if ALLOWED_SYMBOLS is None:
        return True  # Aceita todos
    return symbol.lower() in ALLOWED_SYMBOLS


def get_disk_usage() -> dict:
    """Retorna uso de disco do diret�rio de dados."""
    try:
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
        total, used, free = shutil.disk_usage(DATA_DIR)

        # Calcula tamanho dos dados Level 2
        data_size = 0
        for dirpath, dirnames, filenames in os.walk(DATA_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                data_size += os.path.getsize(fp)

        return {
            "disk_total_gb": round(total / (1024**3), 2),
            "disk_used_gb": round(used / (1024**3), 2),
            "disk_free_gb": round(free / (1024**3), 2),
            "disk_free_percent": round((free / total) * 100, 1),
            "level2_data_size_mb": round(data_size / (1024**2), 2)
        }
    except Exception as e:
        logger.error(f"Error getting disk usage: {e}")
        return {"error": str(e)}


def list_data_files() -> dict:
    """Lista arquivos de dados por s�mbolo."""
    result = {}
    try:
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
        for symbol_dir in Path(DATA_DIR).iterdir():
            if symbol_dir.is_dir():
                files = []
                for f in symbol_dir.iterdir():
                    if f.is_file():
                        files.append({
                            "name": f.name,
                            "size_mb": round(f.stat().st_size / (1024**2), 2),
                            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
                        })
                result[symbol_dir.name] = sorted(files, key=lambda x: x["name"], reverse=True)
    except Exception as e:
        logger.error(f"Error listing files: {e}")
    return result


# ==== ENDPOINTS ==== #

@app.get("/")
def root():
    """Endpoint raiz com info do serviço canônico."""
    return {
        "service": "Quantower Level 2 Capture API",
        "version": __version__,
        "build_id": __build_id__,
        "status": "running",
        "data_dir": DATA_DIR,
        "endpoints": [
            "GET /",
            "GET /health",
            "GET /stats",
            "GET /files",
            "POST /level2/depth-snapshot",
            "POST /level2/depth-update",
            "POST /level2/trades",
            "POST /level2/events",
            "POST /level2/microstructure",
            "POST /level2/daybar",
            "POST /level2/full-snapshot",
            "POST /level2/flush"
        ]
    }


@app.get("/health")
def health_check():
    """Health check endpoint para monitoramento."""
    stats = buffer_manager.get_stats()
    disk = get_disk_usage()

    # Alerta se disco < 10%
    disk_warning = disk.get("disk_free_percent", 100) < 10

    return {
        "status": "warning" if disk_warning else "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "buffer_stats": stats,
        "disk": disk,
        "warnings": ["Low disk space!"] if disk_warning else []
    }


@app.get("/stats")
def get_stats():
    """Retorna estat�sticas detalhadas do servi�o."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "data_dir": DATA_DIR,
            "buffer_size": BUFFER_SIZE,
            "flush_interval": FLUSH_INTERVAL_SECONDS,
            "compression": USE_COMPRESSION,
            "allowed_symbols": ALLOWED_SYMBOLS or "all"
        },
        "buffer_stats": buffer_manager.get_stats(),
        "disk": get_disk_usage()
    }


@app.get("/files")
def get_files():
    """Lista todos os arquivos de dados por s�mbolo."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_dir": DATA_DIR,
        "files": list_data_files()
    }


@app.post("/level2/flush")
def force_flush():
    """For�a flush de todos os buffers para disco."""
    buffer_manager.flush_all()
    return {"status": "flushed", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/level2/depth-snapshot")
def receive_depth_snapshot(data: List[DepthSnapshotRow]):
    """Recebe snapshot completo do order book (todos os n�veis)."""
    if not data:
        return {"status": "empty", "rows": 0}

    # Agrupa por s�mbolo e data (caso batch tenha m�ltiplos s�mbolos)
    grouped: Dict[str, List[DepthSnapshotRow]] = defaultdict(list)
    for row in data:
        date = extract_date_from_timestamp(row.timestamp)
        key = (row.symbol.lower(), date)
        grouped[key].append(row)

    total_rows = 0
    symbols_processed = []

    header = [
        "recv_timestamp", "timestamp", "side", "price", "size",
        "level_index", "orders_count", "exchange"
    ]

    recv_ts = datetime.now(timezone.utc).isoformat()

    for (symbol, date), rows in grouped.items():
        path = get_file_path(symbol, "depth_snapshots", date)
        csv_rows = [
            [
                recv_ts,
                row.timestamp,
                row.side,
                row.price,
                row.size,
                row.level_index,
                row.orders_count,
                row.exchange,
            ]
            for row in rows
        ]
        buffer_manager.add_rows(path, header, csv_rows)
        total_rows += len(csv_rows)
        if symbol not in symbols_processed:
            symbols_processed.append(symbol)

    return {
        "status": "snapshot_received",
        "rows": total_rows,
        "symbols": symbols_processed
    }


@app.post("/level2/depth-update")
def receive_depth_update(data: List[DepthUpdateRow]):
    """Recebe atualiza��es incrementais do order book."""
    if not data:
        return {"status": "empty", "rows": 0}

    grouped: Dict[str, List[DepthUpdateRow]] = defaultdict(list)
    for row in data:
        date = extract_date_from_timestamp(row.timestamp)
        key = (row.symbol.lower(), date)
        grouped[key].append(row)

    total_rows = 0
    symbols_processed = []

    header = [
        "recv_timestamp", "timestamp", "side", "action", "price", "size",
        "level_index", "orders_count", "exchange", "sequence"
    ]

    recv_ts = datetime.now(timezone.utc).isoformat()

    for (symbol, date), rows in grouped.items():
        path = get_file_path(symbol, "depth_updates", date)
        csv_rows = [
            [
                recv_ts,
                row.timestamp,
                row.side,
                row.action,
                row.price,
                row.size,
                row.level_index,
                row.orders_count,
                row.exchange,
                row.sequence,
            ]
            for row in rows
        ]
        buffer_manager.add_rows(path, header, csv_rows)
        total_rows += len(csv_rows)
        if symbol not in symbols_processed:
            symbols_processed.append(symbol)

    return {
        "status": "updates_received",
        "rows": total_rows,
        "symbols": symbols_processed
    }


@app.post("/level2/trades")
def receive_trades(data: List[TradeRow]):
    """Recebe dados de trades (Time & Sales) com delta tracking."""
    if not data:
        return {"status": "empty", "rows": 0}

    grouped: Dict[str, List[TradeRow]] = defaultdict(list)
    for row in data:
        date = extract_date_from_timestamp(row.timestamp)
        key = (row.symbol.lower(), date)
        grouped[key].append(row)

    total_rows = 0
    symbols_processed = []

    header = [
        "recv_timestamp", "timestamp", "price", "size",
        "aggressor", "exchange", "trade_id", "flags", "sequence",
        "delta_contribution", "cumulative_delta",
        "session_buy_volume", "session_sell_volume",
        "session_total_volume", "session_trade_count"
    ]

    recv_ts = datetime.now(timezone.utc).isoformat()

    for (symbol, date), rows in grouped.items():
        path = get_file_path(symbol, "trades", date)
        csv_rows = [
            [
                recv_ts,
                row.timestamp,
                row.price,
                row.size,
                row.aggressor,
                row.exchange,
                row.trade_id,
                row.flags,
                row.sequence,
                row.delta_contribution,
                row.cumulative_delta,
                row.session_buy_volume,
                row.session_sell_volume,
                row.session_total_volume,
                row.session_trade_count,
            ]
            for row in rows
        ]
        buffer_manager.add_rows(path, header, csv_rows)
        total_rows += len(csv_rows)
        if symbol not in symbols_processed:
            symbols_processed.append(symbol)

    return {
        "status": "trades_received",
        "rows": total_rows,
        "symbols": symbols_processed
    }


@app.post("/level2/events")
def receive_events(data: List[EventRow]):
    """Recebe eventos do sistema (resets, conex�es, etc)."""
    if not data:
        return {"status": "empty", "rows": 0}

    grouped: Dict[str, List[EventRow]] = defaultdict(list)
    for row in data:
        date = extract_date_from_timestamp(row.timestamp)
        key = (row.symbol.lower(), date)
        grouped[key].append(row)

    total_rows = 0
    symbols_processed = []

    header = ["recv_timestamp", "timestamp", "event_type", "details"]

    recv_ts = datetime.now(timezone.utc).isoformat()

    for (symbol, date), rows in grouped.items():
        path = get_file_path(symbol, "events", date)
        csv_rows = [
            [
                recv_ts,
                row.timestamp,
                row.event_type,
                row.details,
            ]
            for row in rows
        ]
        buffer_manager.add_rows(path, header, csv_rows)
        total_rows += len(csv_rows)
        if symbol not in symbols_processed:
            symbols_processed.append(symbol)

    return {
        "status": "events_received",
        "rows": total_rows,
        "symbols": symbols_processed
    }


@app.post("/level2/microstructure")
def receive_microstructure(data: List[MicrostructureRow]):
    """Recebe dados de microestrutura consolidados do Quantower."""
    if not data:
        return {"status": "empty", "rows": 0}

    grouped: Dict[str, List[MicrostructureRow]] = defaultdict(list)
    for row in data:
        date = extract_date_from_timestamp(row.timestamp)
        key = (row.symbol.lower(), date)
        grouped[key].append(row)

    total_rows = 0
    symbols_processed = []

    header = [
        "recv_timestamp", "timestamp", "dom_imbalance", "total_bid_size", "total_ask_size",
        "bar_delta", "cumulative_delta", "spread", "spread_percent", "best_bid", "best_ask",
        "mid_price", "bid_pressure", "ask_pressure", "pressure_ratio", "wall_bid_price",
        "wall_bid_size", "wall_ask_price", "wall_ask_size", "wall_distance_ticks",
        "absorption_detected", "absorption_ratio", "absorption_side", "large_bid_count",
        "large_ask_count", "large_order_imbalance", "trades_per_second", "volume_per_second",
        "liquidity_shift", "sweep_detected", "levels_swept", "sweep_direction",
        "toxicity_score", "poc_price", "poc_volume", "distance_to_poc", "session_volume",
        "session_trade_count", "session_buy_volume", "session_sell_volume", "exchange"
    ]

    recv_ts = datetime.now(timezone.utc).isoformat()

    for (symbol, date), rows in grouped.items():
        path = get_file_path(symbol, "microstructure", date)
        csv_rows = [
            [
                recv_ts,
                row.timestamp,
                row.dom_imbalance,
                row.total_bid_size,
                row.total_ask_size,
                row.bar_delta,
                row.cumulative_delta,
                row.spread,
                row.spread_percent,
                row.best_bid,
                row.best_ask,
                row.mid_price,
                row.bid_pressure,
                row.ask_pressure,
                row.pressure_ratio,
                row.wall_bid_price,
                row.wall_bid_size,
                row.wall_ask_price,
                row.wall_ask_size,
                row.wall_distance_ticks,
                row.absorption_detected,
                row.absorption_ratio,
                row.absorption_side,
                row.large_bid_count,
                row.large_ask_count,
                row.large_order_imbalance,
                row.trades_per_second,
                row.volume_per_second,
                row.liquidity_shift,
                row.sweep_detected,
                row.levels_swept,
                row.sweep_direction,
                row.toxicity_score,
                row.poc_price,
                row.poc_volume,
                row.distance_to_poc,
                row.session_volume,
                row.session_trade_count,
                row.session_buy_volume,
                row.session_sell_volume,
                row.exchange,
            ]
            for row in rows
        ]
        buffer_manager.add_rows(path, header, csv_rows)
        total_rows += len(csv_rows)
        if symbol not in symbols_processed:
            symbols_processed.append(symbol)

    return {
        "status": "microstructure_received",
        "rows": total_rows,
        "symbols": symbols_processed
    }


# ==== NOVOS MODELOS PARA DAYBAR E FULL-SNAPSHOT ==== #

class DayBarRow(BaseModel):
    symbol: str = Field(..., min_length=1)
    timestamp: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    delta: Optional[float] = None
    buy_volume: Optional[float] = None
    sell_volume: Optional[float] = None
    trade_count: Optional[int] = None
    vwap: Optional[float] = None
    exchange: Optional[str] = None


class FullSnapshotRow(BaseModel):
    symbol: str = Field(..., min_length=1)
    timestamp: str
    snapshot_type: str  # "dom", "trades_summary", "session_stats"
    data: Optional[str] = None  # JSON string with full snapshot data
    bid_levels: Optional[int] = None
    ask_levels: Optional[int] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread: Optional[float] = None
    cumulative_delta: Optional[float] = None
    session_volume: Optional[float] = None
    exchange: Optional[str] = None


@app.post("/level2/daybar")
def receive_daybar(data: List[DayBarRow]):
    """Recebe dados de barras di�rias com estat�sticas."""
    if not data:
        return {"status": "empty", "rows": 0}

    grouped: Dict[str, List[DayBarRow]] = defaultdict(list)
    for row in data:
        date = row.date if row.date else extract_date_from_timestamp(row.timestamp)
        key = (row.symbol.lower(), date)
        grouped[key].append(row)

    total_rows = 0
    symbols_processed = []

    header = [
        "recv_timestamp", "timestamp", "date", "open", "high", "low", "close",
        "volume", "delta", "buy_volume", "sell_volume", "trade_count", "vwap", "exchange"
    ]

    recv_ts = datetime.now(timezone.utc).isoformat()

    for (symbol, date), rows in grouped.items():
        path = get_file_path(symbol, "daybar", date)
        csv_rows = [
            [
                recv_ts,
                row.timestamp,
                row.date,
                row.open,
                row.high,
                row.low,
                row.close,
                row.volume,
                row.delta,
                row.buy_volume,
                row.sell_volume,
                row.trade_count,
                row.vwap,
                row.exchange,
            ]
            for row in rows
        ]
        buffer_manager.add_rows(path, header, csv_rows)
        total_rows += len(csv_rows)
        if symbol not in symbols_processed:
            symbols_processed.append(symbol)

    return {
        "status": "daybar_received",
        "rows": total_rows,
        "symbols": symbols_processed
    }


@app.post("/level2/full-snapshot")
def receive_full_snapshot(data: List[FullSnapshotRow]):
    """Recebe snapshot completo do estado do mercado."""
    if not data:
        return {"status": "empty", "rows": 0}

    grouped: Dict[str, List[FullSnapshotRow]] = defaultdict(list)
    for row in data:
        date = extract_date_from_timestamp(row.timestamp)
        key = (row.symbol.lower(), date)
        grouped[key].append(row)

    total_rows = 0
    symbols_processed = []

    header = [
        "recv_timestamp", "timestamp", "snapshot_type", "data",
        "bid_levels", "ask_levels", "best_bid", "best_ask", "spread",
        "cumulative_delta", "session_volume", "exchange"
    ]

    recv_ts = datetime.now(timezone.utc).isoformat()

    for (symbol, date), rows in grouped.items():
        path = get_file_path(symbol, "full_snapshots", date)
        csv_rows = [
            [
                recv_ts,
                row.timestamp,
                row.snapshot_type,
                row.data,
                row.bid_levels,
                row.ask_levels,
                row.best_bid,
                row.best_ask,
                row.spread,
                row.cumulative_delta,
                row.session_volume,
                row.exchange,
            ]
            for row in rows
        ]
        buffer_manager.add_rows(path, header, csv_rows)
        total_rows += len(csv_rows)
        if symbol not in symbols_processed:
            symbols_processed.append(symbol)

    return {
        "status": "full_snapshot_received",
        "rows": total_rows,
        "symbols": symbols_processed
    }


# ==== PARA RODAR DIRETAMENTE ==== #

if __name__ == "__main__":
    import uvicorn

    # Cria diret�rio de dados se n�o existir
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)

    logger.info(f"Starting Level2 API on {API_HOST}:{API_PORT}")
    logger.info(f"Data directory: {DATA_DIR}")
    logger.info(f"Compression: {USE_COMPRESSION}")
    logger.info(f"Allowed symbols: {ALLOWED_SYMBOLS or 'all'}")

    uvicorn.run(
        "quantower_level2_api:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        workers=1,  # CSV buffering funciona melhor com 1 worker
        access_log=True
    )
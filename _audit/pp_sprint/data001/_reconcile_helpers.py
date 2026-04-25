"""Shared helpers for DATA-001 reconciliation. READ-ONLY."""
from __future__ import annotations
import gzip
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import numpy as np

TRADES_DIR = Path(r"C:\data\level2\_gc_xcec")
DATABENTO_DIR = Path(r"C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5")
L2_PARQUET = Path(r"C:\data\processed\gc_ohlcv_l2_joined.parquet")
M30_PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
D1_PARQUET = Path(r"C:\data\processed\gc_d1_boxes.parquet")
CACHE_DIR = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\_cache_raw_1min")


def _parse_iso_nosub(ts: str) -> datetime:
    s = ts.split(".")[0]
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def raw_trades_path(date: str) -> Path:
    return TRADES_DIR / f"trades_{date}.csv.gz"


def databento_path(date: str) -> Path:
    # date format YYYY-MM-DD -> glbx-mdp3-YYYYMMDD.mbp-10.csv.zst
    y, m, d = date.split("-")
    return DATABENTO_DIR / f"glbx-mdp3-{y}{m}{d}.mbp-10.csv.zst"


def cache_path(date: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"raw_1min_{date}.parquet"


def build_raw_1min_from_trades(date: str) -> pd.DataFrame | None:
    """Build per-minute OHLCV from L2 trades CSV. Returns None if file missing."""
    cp = cache_path(date)
    if cp.exists():
        return pd.read_parquet(cp)

    src = raw_trades_path(date)
    if not src.exists():
        # Try databento for older dates
        db = databento_path(date)
        if db.exists():
            return _build_raw_from_databento(date, db, cp)
        return None

    # Pandas read_csv is much faster than line-by-line Python parsing
    with gzip.open(src, "rb") as f:
        df = pd.read_csv(
            f,
            usecols=["timestamp", "price", "size"],
            engine="c",
        )
    if df.empty:
        return None
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df[df["ts"].notna() & (df["price"] > 0) & (df["size"] > 0)]
    if df.empty:
        return None
    df["ts_min"] = df["ts"].dt.floor("1min")
    agg = df.groupby("ts_min").agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("size", "sum"),
        n_trades=("price", "count"),
    )
    agg.index.name = "timestamp"
    if agg.index.tz is None:
        agg.index = agg.index.tz_localize("UTC")
    agg.to_parquet(cp)
    return agg


_DB_SYMBOL_RE = __import__("re").compile(r"^GC[FGHJKMNQUVXZ]\d$")


def _build_raw_from_databento(date: str, db_path: Path, cp: Path) -> pd.DataFrame | None:
    """Build per-minute OHLCV from Databento MBP-10 zstd CSV.
    Filters action='T' (trades only) and picks the front-month GC contract
    (highest trade count among singleton GCM\\d symbols, excluding spreads).

    Optimized: single-pass, pandas-based. Reads only needed columns.
    """
    try:
        import zstandard as zstd
        import io
    except ImportError:
        return None

    usecols = ["ts_recv", "action", "price", "size", "symbol"]
    with open(db_path, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        with dctx.stream_reader(fh) as reader:
            df = pd.read_csv(
                reader,
                usecols=usecols,
                dtype={"action": "string", "symbol": "string"},
                engine="c",
            )

    df = df[df["action"] == "T"]
    if df.empty:
        return None
    # Single-contract symbols only (no spreads)
    df = df[df["symbol"].str.match(r"^GC[FGHJKMNQUVXZ]\d$", na=False)]
    if df.empty:
        return None

    front = df["symbol"].value_counts().idxmax()
    df = df[df["symbol"] == front].copy()
    if df.empty:
        return None

    # Parse timestamps (ISO format with trailing Z)
    df["ts"] = pd.to_datetime(df["ts_recv"], utc=True, errors="coerce")
    df = df[df["ts"].notna() & (df["price"] > 0) & (df["size"] > 0)]
    if df.empty:
        return None

    df["ts_min"] = df["ts"].dt.floor("1min")
    agg = df.groupby("ts_min").agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("size", "sum"),
        n_trades=("price", "count"),
    )
    agg.index.name = "timestamp"
    if agg.index.tz is None:
        agg.index = agg.index.tz_localize("UTC")
    agg.to_parquet(cp)
    cp.with_suffix(".frontsym.txt").write_text(front, encoding="utf-8")
    return agg


_PQ_L2_CACHE = None


def load_parquet_1min_day(date: str) -> pd.DataFrame:
    """Slice gc_ohlcv_l2_joined.parquet to a single UTC calendar day."""
    global _PQ_L2_CACHE
    if _PQ_L2_CACHE is None:
        df = pd.read_parquet(L2_PARQUET, columns=["open", "high", "low", "close", "volume"])
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        _PQ_L2_CACHE = df
    start = pd.Timestamp(date, tz="UTC")
    end = start + pd.Timedelta(days=1)
    return _PQ_L2_CACHE[(_PQ_L2_CACHE.index >= start) & (_PQ_L2_CACHE.index < end)].copy()


_PQ_M30_CACHE = None


def load_parquet_m30_day(date: str) -> pd.DataFrame:
    global _PQ_M30_CACHE
    if _PQ_M30_CACHE is None:
        df = pd.read_parquet(M30_PARQUET, columns=["open", "high", "low", "close", "volume"])
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        _PQ_M30_CACHE = df
    start = pd.Timestamp(date, tz="UTC")
    end = start + pd.Timedelta(days=1)
    return _PQ_M30_CACHE[(_PQ_M30_CACHE.index >= start) & (_PQ_M30_CACHE.index < end)].copy()


_PQ_D1_CACHE = None


def load_parquet_d1(date: str) -> pd.Series | None:
    global _PQ_D1_CACHE
    if _PQ_D1_CACHE is None:
        df = pd.read_parquet(D1_PARQUET, columns=["open", "high", "low", "close", "volume"])
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        _PQ_D1_CACHE = df
    # D1 bars are indexed at 22:00 UTC CLOSE of the ET session
    # For "date" D, we want the bar at previous day 22:00 UTC... or on D at some marker.
    # Check: scan index for single bar whose date matches.
    target = pd.Timestamp(date, tz="UTC")
    # Allow matches within ±1 day
    lo = target - pd.Timedelta(hours=12)
    hi = target + pd.Timedelta(hours=36)
    w = _PQ_D1_CACHE[(_PQ_D1_CACHE.index >= lo) & (_PQ_D1_CACHE.index < hi)]
    if w.empty:
        return None
    # Pick the bar whose index date == target date OR whose index date == target-1
    # (D1 convention 22:00 UTC close on prev calendar day)
    for idx, row in w.iterrows():
        if idx.date() == target.date() or idx.date() == (target - pd.Timedelta(days=1)).date():
            return row
    return w.iloc[0]


def reconcile_day_1min(date: str) -> dict | None:
    """Per-minute delta between raw and parquet for one date. Returns summary dict."""
    raw = build_raw_1min_from_trades(date)
    if raw is None or len(raw) == 0:
        return None
    pq = load_parquet_1min_day(date)
    if pq.empty:
        return {
            "date": date,
            "raw_rows": len(raw),
            "pq_rows": 0,
            "status": "PQ_EMPTY",
        }

    # Align to 1-min
    merged = pq.join(raw, how="outer", rsuffix="_raw")
    # Only compare rows where both exist
    both = merged.dropna(subset=["high", "high_raw"])
    if len(both) == 0:
        return {"date": date, "raw_rows": len(raw), "pq_rows": len(pq),
                "status": "NO_OVERLAP"}

    dh = (both["high_raw"] - both["high"]).abs()
    dl = (both["low_raw"] - both["low"]).abs()
    dv = (both["volume_raw"] - both["volume"]).abs()
    dv_pct = dv / both["volume_raw"].replace(0, np.nan).abs()

    # Signed deltas for direction analysis
    sh = (both["high_raw"] - both["high"])
    sl = (both["low_raw"] - both["low"])

    return {
        "date": date,
        "raw_rows": len(raw),
        "pq_rows": len(pq),
        "overlap_rows": len(both),
        "raw_H": float(raw["high"].max()),
        "raw_L": float(raw["low"].min()),
        "pq_H": float(pq["high"].max()),
        "pq_L": float(pq["low"].min()),
        "day_dH_tape_minus_pq_pts": float(raw["high"].max() - pq["high"].max()),
        "day_dL_tape_minus_pq_pts": float(raw["low"].min() - pq["low"].min()),
        "max_price_delta_high_pts": float(dh.max()),
        "max_price_delta_low_pts": float(dl.max()),
        "p50_price_delta_high_pts": float(dh.median()),
        "p95_price_delta_high_pts": float(dh.quantile(0.95)),
        "p99_price_delta_high_pts": float(dh.quantile(0.99)),
        "mean_abs_price_delta_pts": float(((dh + dl) / 2.0).mean()),
        "max_volume_delta_pct": float(dv_pct.max()) if dv_pct.notna().any() else None,
        "sum_abs_volume_delta": int(dv.sum()),
        "mean_signed_dH": float(sh.mean()),
        "mean_signed_dL": float(sl.mean()),
        "minutes_abs_dH_gt_1": int((dh > 1).sum()),
        "minutes_abs_dH_gt_5": int((dh > 5).sum()),
        "minutes_abs_dH_gt_20": int((dh > 20).sum()),
        "first_minute_dH_gt_5": (dh[dh > 5].index.min().isoformat()
                                  if (dh > 5).any() else ""),
        "last_minute_dH_gt_5": (dh[dh > 5].index.max().isoformat()
                                 if (dh > 5).any() else ""),
    }


def reconcile_day_m30(date: str) -> dict | None:
    """Compare raw-reaggregated M30 bars vs parquet M30."""
    raw = build_raw_1min_from_trades(date)
    if raw is None or len(raw) == 0:
        return None
    # Reaggregate raw to M30
    raw_m30 = pd.DataFrame({
        "open": raw["open"].resample("30min").first(),
        "high": raw["high"].resample("30min").max(),
        "low": raw["low"].resample("30min").min(),
        "close": raw["close"].resample("30min").last(),
        "volume": raw["volume"].resample("30min").sum(),
    }).dropna()
    pq = load_parquet_m30_day(date)
    if pq.empty:
        return {"date": date, "status": "PQ_EMPTY_M30"}
    merged = pq.join(raw_m30, how="outer", rsuffix="_raw")
    both = merged.dropna(subset=["high", "high_raw"])
    if both.empty:
        return {"date": date, "status": "NO_OVERLAP_M30"}
    dh = (both["high_raw"] - both["high"]).abs()
    dl = (both["low_raw"] - both["low"]).abs()
    return {
        "date": date,
        "m30_bars": len(both),
        "raw_H_m30": float(raw_m30["high"].max()),
        "pq_H_m30": float(pq["high"].max()),
        "raw_L_m30": float(raw_m30["low"].min()),
        "pq_L_m30": float(pq["low"].min()),
        "max_dH_m30": float(dh.max()),
        "max_dL_m30": float(dl.max()),
        "mean_dH_m30": float(dh.mean()),
        "mean_dL_m30": float(dl.mean()),
        "bars_dH_gt_5": int((dh > 5).sum()),
    }


__all__ = [
    "build_raw_1min_from_trades", "load_parquet_1min_day", "load_parquet_m30_day",
    "load_parquet_d1", "reconcile_day_1min", "reconcile_day_m30", "cache_path",
    "raw_trades_path", "databento_path",
]

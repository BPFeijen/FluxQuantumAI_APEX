"""ChronologicalDispatcher: yields all replay events for the May 5-8 window
in strict timestamp order.

Three event sources:
  - microstructure: microstructure_2026-05-{05..08}.csv.gz rows
        (one event per row; consumed by EventProcessor._refresh_metrics)
  - iceberg: iceberg__GC_XCEC_2026050{5..8}.jsonl events
        (one event per line; consumed by EventProcessor._on_iceberg_event)
  - m1_tick: gc_ohlcv_l2_joined.parquet bars
        (one event per minute; used by PositionMonitor for fill simulation
        and by harness for advancing market price in BrokerMock)

The dispatcher is purely a generator. It does not call into EventProcessor
or PositionMonitor. The harness consumes events from the dispatcher and
routes them appropriately.

Event schema:
    {
      "ts": pd.Timestamp (tz=UTC, sort key),
      "kind": "microstructure" | "iceberg" | "m1_tick",
      "row": dict (row data; fields depend on kind)
    }
"""
from __future__ import annotations

import gzip
import heapq
import json
from pathlib import Path
from typing import Generator, Iterator

import pandas as pd


MICRO_DIR = Path("C:/data/level2/_gc_xcec")
ICE_DIR = Path("C:/data/iceberg")
M1_PARQUET = Path("C:/data/processed/gc_ohlcv_l2_joined.parquet")


def _iter_microstructure(date_str: str) -> Iterator[dict]:
    """Yield microstructure rows for a given date as
    {"ts": Timestamp, "kind": "microstructure", "row": {...}}.

    date_str format: '2026-05-05'.
    """
    path = MICRO_DIR / f"microstructure_{date_str}.csv.gz"
    if not path.exists():
        return
    # Read in one go (~25k rows / day; ~6 MB compressed; trivial)
    df = pd.read_csv(path, compression="gzip")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    for _, row in df.iterrows():
        yield {
            "ts": row["timestamp"],
            "kind": "microstructure",
            "row": row.to_dict(),
        }


def _iter_iceberg(date_str: str) -> Iterator[dict]:
    """Yield iceberg events for a given date.

    date_str format: '20260505' (compact, matches filename).
    """
    path = ICE_DIR / f"iceberg__GC_XCEC_{date_str}.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = ev.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = pd.Timestamp(ts_raw)
                if ts.tz is None:
                    ts = ts.tz_localize("UTC")
                else:
                    ts = ts.tz_convert("UTC")
            except (ValueError, TypeError):
                continue
            yield {
                "ts": ts,
                "kind": "iceberg",
                "row": ev,
            }


def _iter_m1(window_start: pd.Timestamp, window_end: pd.Timestamp) -> Iterator[dict]:
    """Yield M1 OHLCV bars in the window as
    {"ts": bar_close_ts, "kind": "m1_tick", "row": {open, high, low, close, volume}}.

    Note: the parquet's index is bar_open. We emit at bar_close
    (open + 1 minute) to ensure the bar is realized in chronological order.
    """
    df = pd.read_parquet(M1_PARQUET)
    df.index = pd.to_datetime(df.index, utc=True)
    sub = df[(df.index >= window_start) & (df.index < window_end)]
    for ts_open, bar in sub.iterrows():
        ts_close = ts_open + pd.Timedelta(minutes=1)
        yield {
            "ts": ts_close,
            "kind": "m1_tick",
            "row": {
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume", 0) or 0),
                "bar_open": ts_open,
            },
        }


def chronological_events(
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    sources: tuple[str, ...] = ("microstructure", "iceberg", "m1_tick"),
) -> Generator[dict, None, None]:
    """Yield events from all selected sources in strict timestamp order.

    Uses heapq.merge over per-source iterators so memory stays bounded:
    iceberg + microstructure files are read row-by-row (microstructure is
    sorted in-memory per day before yielding; days are consumed sequentially).

    Parameters
    ----------
    window_start, window_end : pd.Timestamp
        UTC; window_end is exclusive.
    sources : tuple
        Subset of {"microstructure", "iceberg", "m1_tick"} to enable.
    """
    # Build per-source iterators
    iters: list[Iterator[dict]] = []
    days = pd.date_range(window_start.normalize(),
                         (window_end - pd.Timedelta(seconds=1)).normalize(),
                         freq="D")

    if "microstructure" in sources:
        for d in days:
            iters.append(_iter_microstructure(d.strftime("%Y-%m-%d")))
    if "iceberg" in sources:
        for d in days:
            iters.append(_iter_iceberg(d.strftime("%Y%m%d")))
    if "m1_tick" in sources:
        iters.append(_iter_m1(window_start, window_end))

    # heapq.merge needs comparable keys — wrap each iterator to yield (ts, ev)
    def _keyed(it):
        for ev in it:
            yield (ev["ts"], ev)

    keyed_iters = [_keyed(it) for it in iters]
    for _, ev in heapq.merge(*keyed_iters, key=lambda x: x[0]):
        if window_start <= ev["ts"] < window_end:
            yield ev


# ------------- smoke -------------
def _smoke():
    """Quick sanity: count events for May 5-8 window."""
    window_start = pd.Timestamp("2026-05-05", tz="UTC")
    window_end = pd.Timestamp("2026-05-09", tz="UTC")  # exclusive
    counts = {"microstructure": 0, "iceberg": 0, "m1_tick": 0}
    by_day: dict[str, dict[str, int]] = {}
    first_ts = None
    last_ts = None
    n = 0
    for ev in chronological_events(window_start, window_end):
        n += 1
        counts[ev["kind"]] += 1
        d = ev["ts"].strftime("%Y-%m-%d")
        by_day.setdefault(d, {"microstructure": 0, "iceberg": 0, "m1_tick": 0})
        by_day[d][ev["kind"]] += 1
        if first_ts is None:
            first_ts = ev["ts"]
        last_ts = ev["ts"]
    print(f"Total events: {n:,}")
    print(f"By kind: {counts}")
    print(f"First: {first_ts}    Last: {last_ts}")
    print()
    print("By day / kind:")
    for d in sorted(by_day.keys()):
        v = by_day[d]
        print(f"  {d}: micro={v['microstructure']:>6,d}  ice={v['iceberg']:>4,d}  m1={v['m1_tick']:>5,d}")


if __name__ == "__main__":
    _smoke()

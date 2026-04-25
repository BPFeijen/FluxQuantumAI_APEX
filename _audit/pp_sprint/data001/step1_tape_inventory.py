"""Step 1 — fast inventory via pandas read_csv. READ-ONLY."""
from __future__ import annotations
import gzip
from pathlib import Path
import csv
import re
import pandas as pd

TRADES_DIR = Path(r"C:\data\level2\_gc_xcec")
DATABENTO_DIR = Path(r"C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5")
OUT = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\tape_inventory.csv")


def inv_trades(path: Path) -> dict:
    size_mb = path.stat().st_size / 1e6
    try:
        with gzip.open(path, "rb") as f:
            df = pd.read_csv(f, usecols=["timestamp"], engine="c")
    except Exception as e:
        return {"error": str(e)}
    if df.empty:
        return None
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dropna()
    if ts.empty:
        return None
    cov_h = (ts.max() - ts.min()).total_seconds() / 3600.0
    return {
        "source": "trades_l2",
        "file_path": str(path),
        "file_size_MB": round(size_mb, 2),
        "row_count": int(len(df)),
        "first_ts_UTC": ts.min().isoformat(),
        "last_ts_UTC": ts.max().isoformat(),
        "coverage_hours": round(cov_h, 2),
    }


def inv_databento(path: Path) -> dict:
    import zstandard as zstd
    size_mb = path.stat().st_size / 1e6
    try:
        with open(path, "rb") as fh:
            dctx = zstd.ZstdDecompressor()
            with dctx.stream_reader(fh) as reader:
                df = pd.read_csv(
                    reader,
                    usecols=["ts_recv", "action", "symbol"],
                    dtype={"action": "string", "symbol": "string"},
                    engine="c",
                )
    except Exception as e:
        return {"error": str(e)}
    if df.empty:
        return None
    trades = df[df["action"] == "T"]
    ts = pd.to_datetime(trades["ts_recv"], utc=True, errors="coerce").dropna()
    if ts.empty:
        return None
    cov_h = (ts.max() - ts.min()).total_seconds() / 3600.0
    # Front-month symbol of trades
    single = trades[trades["symbol"].str.match(r"^GC[FGHJKMNQUVXZ]\d$", na=False)]
    front = single["symbol"].value_counts().idxmax() if not single.empty else ""
    return {
        "source": "databento_mbp10",
        "file_path": str(path),
        "file_size_MB": round(size_mb, 2),
        "row_count": int(len(trades)),
        "first_ts_UTC": ts.min().isoformat(),
        "last_ts_UTC": ts.max().isoformat(),
        "coverage_hours": round(cov_h, 2),
        "front_symbol": front,
    }


_TRADES_RE = re.compile(r"trades_(\d{4}-\d{2}-\d{2})\.csv\.gz$")
_DB_RE = re.compile(r"glbx-mdp3-(\d{8})\.mbp-10\.csv\.zst$")


def main():
    trades_files = sorted(TRADES_DIR.glob("trades_*.csv.gz"))
    db_files = sorted(DATABENTO_DIR.glob("glbx-mdp3-*.mbp-10.csv.zst"))
    print(f"Trades: {len(trades_files)}   Databento: {len(db_files)}", flush=True)

    rows = []
    for i, f in enumerate(trades_files):
        m = _TRADES_RE.search(f.name)
        if not m: continue
        rec = inv_trades(f) or {}
        rec["date"] = m.group(1)
        rows.append(rec)
        if (i + 1) % 10 == 0 or i + 1 == len(trades_files):
            print(f"  trades {i+1}/{len(trades_files)}", flush=True)

    for i, f in enumerate(db_files):
        m = _DB_RE.search(f.name)
        if not m: continue
        rec = inv_databento(f) or {}
        raw = m.group(1)
        rec["date"] = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        rows.append(rec)
        if (i + 1) % 10 == 0 or i + 1 == len(db_files):
            print(f"  databento {i+1}/{len(db_files)}", flush=True)

    fields = ["date", "source", "file_path", "file_size_MB", "row_count",
              "first_ts_UTC", "last_ts_UTC", "coverage_hours", "front_symbol"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda x: (x.get("date", ""), x.get("source", ""))):
            w.writerow(r)

    print(f"\nWrote {OUT}: {len(rows)} rows", flush=True)
    by_source = {}
    for r in rows:
        by_source.setdefault(r.get("source", "unknown"), []).append(r)
    for src, lst in by_source.items():
        dates = sorted(set(x.get("date", "") for x in lst if x.get("date")))
        print(f"  {src}: {len(lst)} files, {len(dates)} unique dates, "
              f"range {dates[0] if dates else '?'}..{dates[-1] if dates else '?'}")


if __name__ == "__main__":
    main()

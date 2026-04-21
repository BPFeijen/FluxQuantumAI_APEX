#!/usr/bin/env python3
"""
extract_grenadier_1s.py — Grenadier Sprint 4: Databento MBP-10 → 1-second snapshots
=====================================================================================
Lê os ficheiros raw Databento MBP-10 (.csv.zst) e produz snapshots de 1 segundo
com os 4 features do Grenadier:

    spread          = ask_px_00 - bid_px_00
    total_bid_depth = sum(bid_sz_00..09)
    total_ask_depth = sum(ask_sz_00..09)
    book_imbalance  = (bid - ask) / (bid + ask)

Output: C:/data/processed/grenadier_1s/l2_1s_YYYYMMDD.parquet
        + upload para s3://fluxquantumai-data/grenadier/features_1s/

126 ficheiros × ~2M rows/dia → ~23,400 rows/dia após resample 1s.

Usage:
    python scripts/extract_grenadier_1s.py                    # todos os dias
    python scripts/extract_grenadier_1s.py --date 2025-07-01  # dia único
    python scripts/extract_grenadier_1s.py --workers 4         # paralelo
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import logging
import os
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import zstandard as zstd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
RAW_DIR   = Path(r"C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5")
OUT_DIR   = Path(r"C:\data\processed\grenadier_1s")
S3_BUCKET = "fluxquantumai-data"
S3_PREFIX = "grenadier/features_1s"

OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("grenadier.extract")

# GC spread sanity bounds (USD/oz)
SPREAD_MIN = 0.05
SPREAD_MAX = 5.0
# GC price bounds (sanity)
PRICE_MIN  = 1000.0
PRICE_MAX  = 5000.0

# Wide columns to read
BID_SZ_COLS = [f"bid_sz_{i:02d}" for i in range(10)]
ASK_SZ_COLS = [f"ask_sz_{i:02d}" for i in range(10)]
USECOLS = (
    ["ts_event", "symbol", "bid_px_00", "ask_px_00"]
    + BID_SZ_COLS
    + ASK_SZ_COLS
)


# ---------------------------------------------------------------------------
# Dominant symbol detection (fast: sample first 50k rows)
# ---------------------------------------------------------------------------

def detect_dominant_symbol(filepath: Path) -> str:
    from collections import Counter
    sym_counter: Counter = Counter()
    with open(filepath, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        reader = io.TextIOWrapper(dctx.stream_reader(fh), encoding="utf-8")
        cr = csv.DictReader(reader)
        for i, row in enumerate(cr):
            if i > 200_000:
                break
            sym = row.get("symbol", "")
            if "GC" in sym and "-" not in sym and len(sym) <= 6:
                sym_counter[sym] += 1
    if not sym_counter:
        return ""
    return sym_counter.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Core extraction: one .zst file → 1-second parquet
# ---------------------------------------------------------------------------

def extract_day(date_str: str, upload_s3: bool = True) -> dict:
    result = {"date": date_str, "status": "ok", "rows_raw": 0, "rows_1s": 0, "elapsed": 0.0}
    t0 = time.time()

    date_nodash = date_str.replace("-", "")
    raw_path = RAW_DIR / f"glbx-mdp3-{date_nodash}.mbp-10.csv.zst"
    out_path = OUT_DIR / f"l2_1s_{date_nodash}.parquet"

    if out_path.exists():
        log.info("%s: SKIP (already exists)", date_str)
        result["status"] = "skip"
        return result

    if not raw_path.exists():
        log.warning("%s: raw file not found", date_str)
        result["status"] = "missing"
        return result

    # ── Detect dominant symbol ──────────────────────────────────────────────
    symbol = detect_dominant_symbol(raw_path)
    if not symbol:
        log.error("%s: no GC symbol found", date_str)
        result["status"] = "no_symbol"
        return result
    log.info("%s: symbol=%s", date_str, symbol)

    # ── Stream + parse ──────────────────────────────────────────────────────
    rows: list[dict] = []
    with open(raw_path, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        reader = io.TextIOWrapper(dctx.stream_reader(fh), encoding="utf-8")
        cr = csv.DictReader(reader)
        for row in cr:
            if row.get("symbol") != symbol:
                continue
            bid_px_str = row.get("bid_px_00", "")
            ask_px_str = row.get("ask_px_00", "")
            if not bid_px_str or not ask_px_str:
                continue
            try:
                bid_px = float(bid_px_str)
                ask_px = float(ask_px_str)
            except ValueError:
                continue
            if not (PRICE_MIN <= bid_px <= PRICE_MAX and PRICE_MIN <= ask_px <= PRICE_MAX):
                continue
            spread = ask_px - bid_px
            if not (SPREAD_MIN <= spread <= SPREAD_MAX):
                continue

            bid_depth = sum(
                int(row.get(c, 0) or 0) for c in BID_SZ_COLS
            )
            ask_depth = sum(
                int(row.get(c, 0) or 0) for c in ASK_SZ_COLS
            )
            if bid_depth <= 0 or ask_depth <= 0:
                continue

            rows.append({
                "ts"        : row["ts_event"],
                "spread"    : spread,
                "bid_depth" : bid_depth,
                "ask_depth" : ask_depth,
            })

    result["rows_raw"] = len(rows)
    if not rows:
        log.warning("%s: no valid rows", date_str)
        result["status"] = "no_data"
        return result

    # ── Build DataFrame ─────────────────────────────────────────────────────
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)

    # ── Resample to 1-second (last snapshot per second) ─────────────────────
    df = df.set_index("ts")
    df_1s = df.resample("1s").last().dropna()

    bid = df_1s["bid_depth"]
    ask = df_1s["ask_depth"]
    df_1s["book_imbalance"] = (bid - ask) / (bid + ask)
    df_1s["symbol"] = symbol

    # Rename to match Grenadier schema
    df_1s = df_1s.rename(columns={
        "bid_depth": "total_bid_depth",
        "ask_depth": "total_ask_depth",
    })
    df_1s.index.name = "ts"

    result["rows_1s"] = len(df_1s)
    log.info("%s: %d raw → %d 1s rows", date_str, len(rows), len(df_1s))

    # ── Save parquet ─────────────────────────────────────────────────────────
    df_1s.to_parquet(out_path)
    log.info("%s: saved → %s", date_str, out_path)

    # ── Upload to S3 ─────────────────────────────────────────────────────────
    if upload_s3:
        try:
            import boto3
            s3 = boto3.client("s3")
            s3_key = f"{S3_PREFIX}/l2_1s_{date_nodash}.parquet"
            s3.upload_file(str(out_path), S3_BUCKET, s3_key)
            log.info("%s: uploaded → s3://%s/%s", date_str, S3_BUCKET, s3_key)
        except Exception as e:
            log.warning("%s: S3 upload failed — %s", date_str, e)

    result["elapsed"] = time.time() - t0
    return result


# ---------------------------------------------------------------------------
# List available dates
# ---------------------------------------------------------------------------

def list_available_dates() -> list[str]:
    dates = []
    for f in sorted(RAW_DIR.glob("glbx-mdp3-*.mbp-10.csv.zst")):
        name = f.name  # glbx-mdp3-20250701.mbp-10.csv.zst
        date_nodash = name.split("-")[2][:8]
        try:
            dt = datetime.date(int(date_nodash[:4]), int(date_nodash[4:6]), int(date_nodash[6:8]))
            if dt.weekday() < 5:  # Mon–Fri only
                dates.append(dt.isoformat())
        except ValueError:
            pass
    return dates


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Grenadier Sprint 4 — Databento 1s feature extraction")
    parser.add_argument("--date",      help="Single date YYYY-MM-DD")
    parser.add_argument("--workers",   type=int, default=1, help="Parallel workers (default: 1)")
    parser.add_argument("--no-upload", action="store_true",  help="Skip S3 upload")
    args = parser.parse_args()

    upload_s3 = not args.no_upload

    if args.date:
        dates = [args.date]
    else:
        dates = list_available_dates()

    # Filter already done
    pending = [
        d for d in dates
        if not (OUT_DIR / f"l2_1s_{d.replace('-','')}.parquet").exists()
    ]

    log.info("Total dates: %d  |  Pending: %d", len(dates), len(pending))
    if not pending:
        log.info("Nothing to do.")
        return

    t_start = time.time()
    ok = skip = fail = 0
    total_rows = 0

    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(extract_day, d, upload_s3): d for d in pending}
            for fut in as_completed(futures):
                r = fut.result()
                if r["status"] == "ok":
                    ok += 1
                    total_rows += r["rows_1s"]
                elif r["status"] == "skip":
                    skip += 1
                else:
                    fail += 1
    else:
        for i, d in enumerate(pending, 1):
            log.info("[%d/%d] %s", i, len(pending), d)
            r = extract_day(d, upload_s3)
            if r["status"] == "ok":
                ok += 1
                total_rows += r["rows_1s"]
            elif r["status"] == "skip":
                skip += 1
            else:
                fail += 1

    elapsed = time.time() - t_start
    log.info("=" * 55)
    log.info("DONE: %d ok | %d skip | %d fail | %.1f min", ok, skip, fail, elapsed / 60)
    log.info("Total 1s rows produced: %d", total_rows)
    log.info("Output: %s", OUT_DIR)


if __name__ == "__main__":
    main()

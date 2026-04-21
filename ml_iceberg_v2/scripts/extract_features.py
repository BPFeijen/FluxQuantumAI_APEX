#!/usr/bin/env python3
"""
extract_features.py — Run refill detection + feature extraction + labeling for one day.

Reads depth_updates_YYYY-MM-DD.csv.gz + trades_YYYY-MM-DD.csv.gz from --depth-dir,
runs the full ml_iceberg_v2 pipeline, and writes:
  features_YYYY-MM-DD.parquet  →  --features-dir
  labels_YYYY-MM-DD.parquet    →  --labels-dir

Depth snapshots (for F08/F09/F19 DOM features) are loaded from --depth-dir if the
file depth_snapshots_YYYY-MM-DD.csv.gz exists; otherwise those features default to 0.

Usage:
    python -m ml_iceberg_v2.scripts.extract_features \\
        --date 2026-01-05 \\
        --depth-dir s3://fluxquantumai-data/level2/gc/ \\
        --features-dir /data/features/iceberg_v2/ \\
        --labels-dir /data/labels/iceberg_v2/

    # Or with local paths:
    python -m ml_iceberg_v2.scripts.extract_features \\
        --date 2026-01-05 \\
        --depth-dir /data/level2/gc/ \\
        --features-dir /data/features/iceberg_v2/ \\
        --labels-dir /data/labels/iceberg_v2/

    # Process all available dates in a directory:
    python -m ml_iceberg_v2.scripts.extract_features \\
        --all \\
        --depth-dir /data/level2/gc/ \\
        --features-dir /data/features/iceberg_v2/ \\
        --labels-dir /data/labels/iceberg_v2/ \\
        --workers 4
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from ml_iceberg_v2.config import (
    CHUNK_SIZE,
    OVERLAP_BUFFER_S,
    MAX_REFILL_DELAY_MS,
    TICK_SIZE_DEFAULT,
)
from ml_iceberg_v2.features.refill_detector import (
    process_day_chunked,
    detect_depth_vanish,
    _normalise_timestamps,
)
from ml_iceberg_v2.features.feature_extractor import (
    extract_features, save_features, load_surprise_windows,
)
from ml_iceberg_v2.features.label_generator import generate_labels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger(__name__)

_S3_PREFIX = "s3://"


def _is_s3(path: str) -> bool:
    return path.startswith(_S3_PREFIX)


def _s3_exists(s3_uri: str) -> bool:
    """Return True if the S3 object exists."""
    result = subprocess.run(
        ["aws", "s3", "ls", s3_uri],
        capture_output=True, text=True
    )
    return result.returncode == 0


def _s3_download(s3_uri: str) -> bytes:
    """Download S3 object to memory and return bytes."""
    result = subprocess.run(
        ["aws", "s3", "cp", s3_uri, "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise FileNotFoundError(f"S3 download failed: {s3_uri}\n{result.stderr.decode()}")
    return result.stdout


def _load_csv_gz(path_or_s3: str) -> pd.DataFrame:
    """Load a .csv.gz file from local path or S3."""
    if _is_s3(path_or_s3):
        data = _s3_download(path_or_s3)
        return pd.read_csv(io.BytesIO(data), compression="gzip")
    return pd.read_csv(path_or_s3, compression="gzip")


def _list_available_dates(depth_dir: str) -> List[str]:
    """
    Return sorted list of YYYY-MM-DD dates for which depth_updates + trades
    files both exist in depth_dir (local or S3).
    """
    pattern = re.compile(r"depth_updates_(\d{4}-\d{2}-\d{2})\.csv\.gz")

    if _is_s3(depth_dir):
        s3_dir = depth_dir.rstrip("/")
        result = subprocess.run(
            ["aws", "s3", "ls", s3_dir + "/"],
            capture_output=True, text=True,
        )
        files = result.stdout.splitlines()
        names = [line.split()[-1] for line in files if line.strip()]
    else:
        p = Path(depth_dir)
        names = [f.name for f in p.glob("depth_updates_*.csv.gz")]

    dates = []
    for name in names:
        m = pattern.match(name)
        if m:
            date = m.group(1)
            # Check trades file also exists
            trades_name = f"trades_{date}.csv.gz"
            if _is_s3(depth_dir):
                trades_uri = depth_dir.rstrip("/") + "/" + trades_name
                if _s3_exists(trades_uri):
                    dates.append(date)
            else:
                if (Path(depth_dir) / trades_name).exists():
                    dates.append(date)

    return sorted(dates)


def process_one_date(
    date: str,
    depth_dir: str,
    features_dir: str,
    labels_dir: str,
    overwrite: bool = False,
) -> Tuple[str, bool, str]:
    """
    Process one date. Returns (date, success, message).
    Designed to be called in a subprocess (ProcessPoolExecutor).
    """
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    log = logging.getLogger(f"extract.{date}")

    feat_out = Path(features_dir) / f"features_{date}.parquet"
    label_out = Path(labels_dir) / f"labels_{date}.parquet"

    if not overwrite and feat_out.exists() and label_out.exists():
        log.info("[%s] already exists — skip", date)
        return date, True, "skipped"

    try:
        depth_base = depth_dir.rstrip("/")
        depth_uri = f"{depth_base}/depth_updates_{date}.csv.gz"
        trades_uri = f"{depth_base}/trades_{date}.csv.gz"
        snap_uri = f"{depth_base}/depth_snapshots_{date}.csv.gz"

        # --- Load data ---
        log.info("[%s] loading depth_updates + trades", date)
        if _is_s3(depth_dir):
            # Write to temp files for chunked processing
            with tempfile.TemporaryDirectory() as tmp:
                depth_tmp = str(Path(tmp) / f"depth_updates_{date}.csv.gz")
                trades_tmp = str(Path(tmp) / f"trades_{date}.csv.gz")
                subprocess.run(["aws", "s3", "cp", depth_uri, depth_tmp], check=True)
                subprocess.run(["aws", "s3", "cp", trades_uri, trades_tmp], check=True)

                refills, trees, chains = process_day_chunked(
                    depth_path=depth_tmp,
                    trades_path=trades_tmp,
                    chunk_size=CHUNK_SIZE,
                    overlap_buffer_s=OVERLAP_BUFFER_S,
                    max_delay_ms=MAX_REFILL_DELAY_MS,
                )

                # Load trades for feature extraction (already in tmp)
                trades_df = pd.read_csv(trades_tmp, compression="gzip")
                trades_df = _normalise_timestamps(trades_df)

                # Load depth snapshots (optional)
                snap_df = None
                if _s3_exists(snap_uri):
                    snap_data = _s3_download(snap_uri)
                    snap_df = pd.read_csv(io.BytesIO(snap_data), compression="gzip")
                    snap_df = _normalise_timestamps(snap_df)

                # Depth vanish events (F17)
                depth_df = pd.read_csv(depth_tmp, compression="gzip")
                depth_df = _normalise_timestamps(depth_df)
        else:
            depth_path = Path(depth_dir) / f"depth_updates_{date}.csv.gz"
            trades_path = Path(depth_dir) / f"trades_{date}.csv.gz"

            refills, trees, chains = process_day_chunked(
                depth_path=str(depth_path),
                trades_path=str(trades_path),
                chunk_size=CHUNK_SIZE,
                overlap_buffer_s=OVERLAP_BUFFER_S,
                max_delay_ms=MAX_REFILL_DELAY_MS,
            )

            trades_df = pd.read_csv(trades_path, compression="gzip")
            trades_df = _normalise_timestamps(trades_df)

            snap_path = Path(depth_dir) / f"depth_snapshots_{date}.csv.gz"
            snap_df = None
            if snap_path.exists():
                snap_df = pd.read_csv(snap_path, compression="gzip")
                snap_df = _normalise_timestamps(snap_df)

            depth_df = pd.read_csv(
                Path(depth_dir) / f"depth_updates_{date}.csv.gz", compression="gzip"
            )
            depth_df = _normalise_timestamps(depth_df)

        log.info("[%s] %d refills, %d chains", date, len(refills), len(chains))

        if not refills:
            log.warning("[%s] no refills detected — skipping", date)
            return date, True, "no_refills"

        # --- Depth vanish events ---
        vanish_events = detect_depth_vanish(
            depth_updates=depth_df,
            trades=trades_df,
            refill_events=refills,
        )
        log.info("[%s] %d depth-vanish events", date, len(vanish_events))

        # --- Feature extraction ---
        # depth_df → F21 OFI (Cont et al. 2014)
        # surprise_zones → adaptive 100ms High-Res windows around Payroll/CPI/FOMC
        surprise_zones = load_surprise_windows()
        features_df = extract_features(
            refill_events=refills,
            trades=trades_df,
            depth_snapshots=snap_df,
            vanish_events=vanish_events,
            depth_updates=depth_df,
            surprise_windows_ns=surprise_zones,
        )
        log.info("[%s] %d feature windows", date, len(features_df))

        if features_df.empty:
            log.warning("[%s] no features extracted", date)
            return date, True, "no_features"

        # --- Label generation ---
        labels_df = generate_labels(
            refill_events=refills,
            chains=chains,
            feature_windows=features_df,
            trees=trees,
        )

        # --- Save ---
        feat_out.parent.mkdir(parents=True, exist_ok=True)
        label_out.parent.mkdir(parents=True, exist_ok=True)
        save_features(features_df, feat_out)
        labels_df.to_parquet(str(label_out), compression="gzip", index=False)

        label_counts = labels_df["label"].value_counts().to_dict()
        log.info("[%s] done — labels: %s → %s", date, label_counts, feat_out.name)
        return date, True, "ok"

    except Exception as exc:
        log.error("[%s] FAILED: %s", date, exc, exc_info=True)
        return date, False, str(exc)


def main():
    parser = argparse.ArgumentParser(description="Extract ml_iceberg_v2 features for GC")
    parser.add_argument("--date", help="Single date YYYY-MM-DD to process")
    parser.add_argument("--all", action="store_true", help="Process all available dates")
    parser.add_argument("--depth-dir", required=True,
                        help="Directory with depth_updates_*.csv.gz + trades_*.csv.gz "
                             "(local path or s3://bucket/prefix)")
    parser.add_argument("--features-dir", required=True, help="Output directory for feature parquets")
    parser.add_argument("--labels-dir", required=True, help="Output directory for label parquets")
    parser.add_argument("--overwrite", action="store_true", help="Re-process even if output exists")
    parser.add_argument("--workers", type=int, default=1, help="Parallel worker processes (--all mode)")
    args = parser.parse_args()

    if not args.date and not args.all:
        parser.error("Specify --date YYYY-MM-DD or --all")

    if args.all:
        dates = _list_available_dates(args.depth_dir)
        if not dates:
            _logger.error("No dates found in %s", args.depth_dir)
            sys.exit(1)
        _logger.info("Found %d dates to process (%s → %s)", len(dates), dates[0], dates[-1])
    else:
        dates = [args.date]

    ok_count, fail_count = 0, 0

    if len(dates) == 1 or args.workers <= 1:
        for date in dates:
            _, success, msg = process_one_date(
                date, args.depth_dir, args.features_dir, args.labels_dir, args.overwrite
            )
            if success:
                ok_count += 1
            else:
                fail_count += 1
                _logger.error("FAILED %s: %s", date, msg)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    process_one_date,
                    date, args.depth_dir, args.features_dir, args.labels_dir, args.overwrite
                ): date
                for date in dates
            }
            for fut in as_completed(futures):
                date = futures[fut]
                try:
                    _, success, msg = fut.result()
                    if success:
                        ok_count += 1
                    else:
                        fail_count += 1
                        _logger.error("FAILED %s: %s", date, msg)
                except Exception as exc:
                    fail_count += 1
                    _logger.error("FAILED %s: %s", date, exc)

    _logger.info("Done — %d ok, %d failed", ok_count, fail_count)
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

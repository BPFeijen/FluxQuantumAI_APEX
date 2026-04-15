"""
extract_features_databento.py
==============================
Extrai features + labels ml_iceberg_v2 a partir dos ficheiros
Databento MBP-10 (.mbp-10.csv.zst) para Jul-Nov 2025.

Estes ficheiros têm formato diferente dos Quantower CSV usados
pelo run_bulk_t107_t108.py — este script faz a adaptação.

Formato Databento MBP-10:
  - Um ficheiro por dia: glbx-mdp3-YYYYMMDD.mbp-10.csv.zst
  - Contém TODOS os contratos GC (spreads, calendars, front/back months)
  - Colunas: ts_recv, ts_event, rtype, publisher_id, instrument_id,
             action, side, depth, price, size, flags, ts_in_delta,
             sequence, bid_px_00..09, ask_px_00..09, bid_sz_00..09, ..., symbol
  - action: A=Add, M=Modify, C=Cancel, T=Trade
  - side:   B=Bid, A=Ask, N=Unknown (trades)
  - Preços com 9 casas decimais (string)

Mapeamento para refill_detector:
  depth_updates:
    timestamp = ts_event parsed to int ns
    side      = 'BID' (B) / 'ASK' (A)
    action    = 'update' (M), 'add' (A), 'cancel' (C)
    price     = float(price)
    size      = float(size)
    sequence  = int(sequence)

  trades:
    timestamp = ts_event parsed to int ns
    price     = float(price)
    size      = float(size)
    aggressor = 'BID' (B) / 'ASK' (A) / 'NONE' (N)
    sequence  = int(sequence)

Symbol selection:
  Auto-detecta o símbolo GC mais activo por dia (excluindo spreads).
  GC spreads têm '-' no símbolo (ex: GCV5-GCZ5).

Usage:
    python scripts/extract_features_databento.py
    python scripts/extract_features_databento.py --start 2025-07-01 --end 2025-09-30
    python scripts/extract_features_databento.py --date 2025-08-04
    python scripts/extract_features_databento.py --dry-run
"""

import argparse
import csv
import datetime
import io
import logging
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import zstandard as zstd

# ---------------------------------------------------------------------------
# Setup paths
# ---------------------------------------------------------------------------
REPO_ROOT    = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ml_iceberg_v2.features.refill_detector import (
    process_day_chunked,
    detect_depth_vanish,
    detect_tick_size,
)
from ml_iceberg_v2.features.feature_extractor import (
    extract_features, save_features, load_surprise_windows,
)
from ml_iceberg_v2.features.label_generator import generate_labels
from ml_iceberg_v2.config import MAX_REFILL_DELAY_MS, MIN_CHAIN_LENGTH

DATA_DIR     = Path(r"C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5")
FEATURES_DIR = REPO_ROOT / "data" / "features" / "iceberg_v2"
LABELS_DIR   = REPO_ROOT / "data" / "labels"   / "iceberg_v2"
TMP_DIR      = REPO_ROOT / "data" / "tmp_databento"

FEATURES_DIR.mkdir(parents=True, exist_ok=True)
LABELS_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Databento adapter
# ---------------------------------------------------------------------------

def _parse_ts_ns(s: str) -> int:
    """Parse Databento ts_event string to int nanoseconds."""
    s = s.strip().rstrip("Z")
    if "." in s:
        dot = s.index(".")
        frac = s[dot + 1:]
        # Pad/truncate fractional part to 9 digits (nanoseconds)
        frac = (frac + "000000000")[:9]
        s = s[:dot + 1] + frac
    else:
        s = s + ".000000000"
    # Replace T with space for pandas
    s = s.replace("T", " ")
    ts = pd.Timestamp(s, tz="UTC")
    return int(ts.value)


def detect_dominant_symbol(filepath: Path) -> str:
    """
    Read the file and return the GC spot-month symbol with the most rows.
    Excludes spreads (symbols containing '-').
    """
    sym_counter: Counter = Counter()
    with open(filepath, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        reader = io.TextIOWrapper(dctx.stream_reader(fh), encoding="utf-8")
        cr = csv.DictReader(reader)
        for row in cr:
            sym = row.get("symbol", "")
            if "GC" in sym and "-" not in sym:
                sym_counter[sym] += 1

    if not sym_counter:
        return ""
    dominant, count = sym_counter.most_common(1)[0]
    log.info("Dominant symbol: %s (%d rows)", dominant, count)
    return dominant


def read_databento_day(
    filepath: Path,
    symbol: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Read one Databento MBP-10 .csv.zst file, filter by symbol,
    and return (depth_updates, trades) DataFrames in refill_detector format.

    depth_updates columns: timestamp(int ns), side, action, price, size, sequence
    trades columns:        timestamp(int ns), price, size, aggressor, sequence
    """
    depth_rows = []
    trade_rows = []

    side_map = {"B": "BID", "A": "ASK", "N": "NONE"}
    action_map = {"M": "update", "A": "add", "C": "cancel"}

    with open(filepath, "rb") as fh:
        dctx = zstd.ZstdDecompressor()
        reader = io.TextIOWrapper(dctx.stream_reader(fh), encoding="utf-8")
        cr = csv.DictReader(reader)
        for row in cr:
            if row.get("symbol") != symbol:
                continue

            action = row.get("action", "")
            ts_str = row.get("ts_event", "")

            try:
                ts_ns = _parse_ts_ns(ts_str)
                price = float(row["price"])
                size = float(row["size"])
                seq = int(row.get("sequence", 0))
                raw_side = row.get("side", "N")
                side = side_map.get(raw_side, "NONE")
            except (ValueError, KeyError):
                continue

            if action == "T":
                # _AGGRESSOR_TO_PASSIVE in refill_detector expects "B"→ASK, "S"→BID
                # Databento: side='B' = buyer aggressor (lifts offer = ASK consumed)
                #            side='A' = seller aggressor (hits bid  = BID consumed)
                # Map: Databento 'A' → 'S' so refill_detector returns passive='BID'
                aggressor_code = "B" if raw_side == "B" else ("S" if raw_side == "A" else "NONE")
                trade_rows.append({
                    "timestamp": ts_ns,
                    "price": price,
                    "size": size,
                    "aggressor": aggressor_code,
                    "sequence": seq,
                })
            elif action in action_map:
                if side in ("BID", "ASK"):  # skip NONE side for depth
                    depth_rows.append({
                        "timestamp": ts_ns,
                        "side": side,
                        "action": action_map[action],
                        "price": price,
                        "size": size,
                        "sequence": seq,
                    })

    depth_df = pd.DataFrame(depth_rows) if depth_rows else pd.DataFrame(
        columns=["timestamp", "side", "action", "price", "size", "sequence"]
    )
    trades_df = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame(
        columns=["timestamp", "price", "size", "aggressor", "sequence"]
    )

    # Sort
    if not depth_df.empty:
        depth_df = depth_df.sort_values(["timestamp", "sequence"]).reset_index(drop=True)
    if not trades_df.empty:
        trades_df = trades_df.sort_values(["timestamp", "sequence"]).reset_index(drop=True)

    log.info(
        "Read %d depth_updates + %d trades for %s",
        len(depth_df), len(trades_df), symbol,
    )
    return depth_df, trades_df


# ---------------------------------------------------------------------------
# Day processing (adapted from run_bulk_t107_t108.py)
# ---------------------------------------------------------------------------

def process_day(date_str: str, dry_run: bool = False) -> dict:
    result = {
        "date": date_str, "status": "ok", "symbol": "",
        "refills": 0, "chains3": 0, "windows": 0,
        "label_counts": {}, "elapsed": 0.0, "error": "",
    }
    t0 = time.time()

    feat_path  = FEATURES_DIR / f"{date_str}.parquet"
    label_path = LABELS_DIR   / f"{date_str}.parquet"

    # Skip if already done
    if feat_path.exists() and label_path.exists():
        log.info("%s: SKIP (already exists)", date_str)
        result["status"] = "skip"
        return result

    # Find Databento file for this date
    date_nodash = date_str.replace("-", "")
    mbp_path = DATA_DIR / f"glbx-mdp3-{date_nodash}.mbp-10.csv.zst"
    if not mbp_path.exists():
        log.warning("%s: Databento file not found: %s", date_str, mbp_path.name)
        result["status"] = "missing"
        return result

    if dry_run:
        symbol = detect_dominant_symbol(mbp_path)
        log.info("%s: DRY-RUN — dominant=%s", date_str, symbol)
        result["status"] = "dry_run"
        result["symbol"] = symbol
        return result

    # Step 1: detect dominant symbol
    symbol = detect_dominant_symbol(mbp_path)
    if not symbol:
        log.error("%s: No GC symbol found in file", date_str)
        result["status"] = "error"
        result["error"] = "no_symbol"
        return result
    result["symbol"] = symbol

    # Step 2: read Databento data
    depth_df, trades_df = read_databento_day(mbp_path, symbol)

    if trades_df.empty:
        log.warning("%s: No trades for %s", date_str, symbol)
        result["status"] = "no_trades"
        return result

    # Step 3: save tmp CSV.gz for process_day_chunked compatibility
    # process_day_chunked reads from disk — write tmp files
    tmp_depth  = TMP_DIR / f"depth_updates_{date_str}.csv.gz"
    tmp_trades = TMP_DIR / f"trades_{date_str}.csv.gz"
    depth_df.to_csv(tmp_depth, index=False, compression="gzip")
    trades_df.to_csv(tmp_trades, index=False, compression="gzip")

    # Step 4: refill detection + chain building
    tick_size = detect_tick_size(trades_df["price"].values.astype(np.float64))
    all_refills, trees, all_chains_full = process_day_chunked(
        depth_path=tmp_depth,
        trades_path=tmp_trades,
        max_delay_ms=MAX_REFILL_DELAY_MS,
    )
    result["refills"] = len(all_refills)

    # Deduplicate chains: keep only the longest chain per (price, side) combination.
    # Tranche trees produce combinatorial explosion (2.4M chains for ~18K refills).
    # For label generation (NATIVE/SYNTHETIC/ABSORPTION) we only need the
    # representative chain per price level — not all tree paths.
    # This reduces label generation from ~12min to ~1min per day.
    seen: dict = {}
    for c in all_chains_full:
        if c.chain_length < MIN_CHAIN_LENGTH:
            continue
        key = (round(c.price / tick_size) * tick_size, c.side)
        if key not in seen or c.chain_length > seen[key].chain_length:
            seen[key] = c
    all_chains = list(seen.values())
    result["chains3"] = len(all_chains)
    log.info("%s: refills=%d  unique_chains>=3=%d (from %d full)", date_str,
             len(all_refills), len(all_chains), len(all_chains_full))

    # Step 5: depth vanish events (F17) — skip for Databento (format incompatible)
    vanish_events = []

    # Step 6: extract features
    # depth_updates (depth_df) passed for F21 OFI (Cont et al. 2014).
    # surprise_windows loaded from APEX_GC_Payroll/data/surprise_labels.jsonl
    # → activates 100ms High-Res windows around NFP/CPI/FOMC releases.
    surprise_zones = load_surprise_windows()   # [] if file not yet populated
    feature_df = extract_features(
        refill_events=all_refills,
        trades=trades_df,
        depth_snapshots=pd.DataFrame(),   # skipped (F08/F09 = 0 on Databento)
        vanish_events=vanish_events,
        depth_updates=depth_df,           # F21 OFI — raw MBO add/cancel events
        surprise_windows_ns=surprise_zones,
        tick_size=tick_size,
    )
    result["windows"] = len(feature_df)

    if feature_df.empty:
        log.warning("%s: No feature windows produced", date_str)
        result["status"] = "no_features"
        # Cleanup tmp
        tmp_depth.unlink(missing_ok=True)
        tmp_trades.unlink(missing_ok=True)
        return result

    save_features(feature_df, feat_path)

    # Step 7: generate labels
    label_df = generate_labels(
        refill_events=all_refills,
        chains=all_chains,
        feature_windows=feature_df,
        trees=trees,
    )
    label_df.to_parquet(label_path, index=False)

    # Count labels
    if "label" in label_df.columns:
        lc = label_df["label"].value_counts().to_dict()
        result["label_counts"] = {int(k): int(v) for k, v in lc.items()}

    # Cleanup tmp files
    tmp_depth.unlink(missing_ok=True)
    tmp_trades.unlink(missing_ok=True)

    result["elapsed"] = time.time() - t0
    log.info(
        "%s: DONE — windows=%d  labels=%s  elapsed=%.0fs",
        date_str, len(feature_df), result["label_counts"], result["elapsed"],
    )
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def list_available_dates(start: str, end: str) -> list[str]:
    """Return dates that have a Databento file in the data dir."""
    dt = datetime.date.fromisoformat(start)
    end_dt = datetime.date.fromisoformat(end)
    dates = []
    while dt <= end_dt:
        date_str = dt.isoformat()
        date_nodash = date_str.replace("-", "")
        mbp_path = DATA_DIR / f"glbx-mdp3-{date_nodash}.mbp-10.csv.zst"
        if mbp_path.exists():
            # Skip weekends
            if dt.weekday() < 5:
                dates.append(date_str)
        dt += datetime.timedelta(days=1)
    return dates


def main():
    parser = argparse.ArgumentParser(description="Extract ml_iceberg_v2 features from Databento MBP-10")
    parser.add_argument("--date",     help="Single date YYYY-MM-DD")
    parser.add_argument("--start",    default="2025-07-01", help="Start date (default: 2025-07-01)")
    parser.add_argument("--end",      default="2025-11-24", help="End date (default: 2025-11-24)")
    parser.add_argument("--dry-run",  action="store_true", help="Only detect symbols, no extraction")
    parser.add_argument("--workers",  type=int, default=1, help="Parallel workers (default: 1)")
    args = parser.parse_args()

    if args.date:
        dates = [args.date]
    else:
        dates = list_available_dates(args.start, args.end)

    # Filter out dates already done
    pending = []
    for d in dates:
        fp = FEATURES_DIR / f"{d}.parquet"
        lp = LABELS_DIR   / f"{d}.parquet"
        if fp.exists() and lp.exists():
            log.info("%s: already done — skip", d)
        else:
            pending.append(d)

    log.info("Dates available: %d  |  Pending: %d", len(dates), len(pending))

    t_start = time.time()
    results = []
    ok = skip = fail = 0

    for i, date_str in enumerate(pending, 1):
        log.info("[%d/%d] Processing %s ...", i, len(pending), date_str)
        try:
            r = process_day(date_str, dry_run=args.dry_run)
            results.append(r)
            if r["status"] in ("ok", "dry_run"):
                ok += 1
            elif r["status"] == "skip":
                skip += 1
            else:
                fail += 1
        except Exception as e:
            log.error("%s: EXCEPTION — %s", date_str, e)
            log.error(traceback.format_exc())
            results.append({"date": date_str, "status": "error", "error": str(e)})
            fail += 1

    total_elapsed = time.time() - t_start
    log.info("=" * 60)
    log.info("DONE: %d ok | %d skip | %d errors | total=%.1fmin", ok, skip, fail, total_elapsed / 60)

    # Global label distribution
    label_names = {0: "NOISE", 1: "NATIVE", 2: "SYNTHETIC", 3: "ABSORPTION"}
    global_counts: dict = {}
    for r in results:
        for lbl, cnt in r.get("label_counts", {}).items():
            global_counts[lbl] = global_counts.get(lbl, 0) + cnt

    total_windows = sum(global_counts.values())
    if total_windows > 0:
        log.info("Global label distribution:")
        for lbl, name in label_names.items():
            cnt = global_counts.get(lbl, 0)
            pct = 100 * cnt / total_windows
            log.info("  %-12s: %7d  (%.1f%%)", name, cnt, pct)
        log.info("  %-12s: %7d", "TOTAL", total_windows)


if __name__ == "__main__":
    main()

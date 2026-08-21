"""
Reconstroi o campo level_index em depth_updates_*.csv.gz a partir dos preços.

Bug original: o Quantower Level2DataExporter.cs gravava `quote.Position`,
que para o feed DxFeed/CME vem sempre 0. O preço, side, size e action
estão corretos — só o rótulo level_index está zerado.

Este script:
  1. Lê depth_snapshots_<date>.csv.gz como hidratação do book.
  2. Lê depth_updates_<date>.csv.gz cronologicamente.
  3. Mantém um book em memória (price -> size por side).
  4. A cada update, calcula o rank do preço no book ordenado.
  5. Reescreve depth_updates_<date>.csv.gz com level_index correto.

Uso:
  python reconstruct_level_index.py --symbol _gc_xcec --date 2026-04-15
  python reconstruct_level_index.py --symbol _gc_xcec --all
  python reconstruct_level_index.py --symbol _gc_xcec --date 2026-04-15 --dry-run
"""

import argparse
import csv
import gzip
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_DATA_DIR = r"C:\data\level2"


def rank_in_book(book: dict, price: float, side: str) -> int:
    """
    Retorna 0-based rank do preço no book ordenado.
    bid: ordenado por preço descendente (best bid = maior preço = rank 0).
    ask: ordenado por preço ascendente (best ask = menor preço = rank 0).
    Se o preço não existir no book, retorna o rank que TERIA se fosse inserido.
    """
    if side == "bid":
        return sum(1 for p in book if p > price)
    else:
        return sum(1 for p in book if p < price)


def load_snapshots(path: str) -> dict:
    """Lê snapshots agrupados por (timestamp, side) -> {price: size}."""
    snaps = defaultdict(lambda: {"bid": {}, "ask": {}})
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = row["timestamp"]
            side = row["side"]
            try:
                price = float(row["price"])
                size = float(row["size"])
            except (TypeError, ValueError):
                continue
            snaps[ts][side][price] = size
    return snaps


def reconstruct(symbol: str, date: str, data_dir: str, dry_run: bool = False) -> dict:
    sym_dir = Path(data_dir) / symbol
    updates_path = sym_dir / f"depth_updates_{date}.csv.gz"
    snapshots_path = sym_dir / f"depth_snapshots_{date}.csv.gz"

    if not updates_path.exists():
        return {"status": "skip", "reason": f"no updates file: {updates_path}"}
    if not snapshots_path.exists():
        return {"status": "skip", "reason": f"no snapshots file: {snapshots_path}"}

    snapshots = load_snapshots(str(snapshots_path))
    snap_timestamps = sorted(snapshots.keys())

    book = {"bid": {}, "ask": {}}
    snap_idx = 0
    stats = {
        "rows_total": 0,
        "rows_rewritten": 0,
        "deletes_missing_price": 0,
        "updates_on_new_price": 0,
        "max_rank_seen": 0,
        "snap_applied": 0,
    }

    tmp_path = sym_dir / f".depth_updates_{date}.recon.csv.gz"
    out_path = sym_dir / f"depth_updates_{date}.csv.gz"

    with gzip.open(str(updates_path), "rt", encoding="utf-8") as fin, \
         gzip.open(str(tmp_path), "wt", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            return {"status": "skip", "reason": "empty file"}
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            stats["rows_total"] += 1
            ts = row["timestamp"]

            # Aplica todos os snapshots cujo timestamp <= ts atual
            while snap_idx < len(snap_timestamps) and snap_timestamps[snap_idx] <= ts:
                snap_ts = snap_timestamps[snap_idx]
                book["bid"] = dict(snapshots[snap_ts]["bid"])
                book["ask"] = dict(snapshots[snap_ts]["ask"])
                snap_idx += 1
                stats["snap_applied"] += 1

            try:
                price = float(row["price"])
                size = float(row["size"])
            except (TypeError, ValueError):
                writer.writerow(row)
                continue

            side = row["side"]
            action = row.get("action", "update")

            if side not in book:
                writer.writerow(row)
                continue

            book_side = book[side]
            is_delete = action == "delete" or size == 0

            if is_delete:
                # Rank ANTES de remover
                if price in book_side:
                    rank = rank_in_book(book_side, price, side)
                    book_side.pop(price, None)
                else:
                    rank = rank_in_book(book_side, price, side)
                    stats["deletes_missing_price"] += 1
            else:
                # insert/update: aplica primeiro, calcula rank depois
                if price not in book_side:
                    stats["updates_on_new_price"] += 1
                book_side[price] = size
                rank = rank_in_book(book_side, price, side)

            row["level_index"] = rank
            if rank > stats["max_rank_seen"]:
                stats["max_rank_seen"] = rank
            writer.writerow(row)
            stats["rows_rewritten"] += 1

    if dry_run:
        # Dump preview e descarta
        try:
            os.remove(str(tmp_path))
        except OSError:
            pass
        stats["status"] = "dry_run_ok"
        stats["tmp_path"] = None
        return stats

    # Backup original e troca
    backup_path = sym_dir / f"depth_updates_{date}.csv.gz.bak_li0"
    if not backup_path.exists():
        shutil.copy2(str(out_path), str(backup_path))
    shutil.move(str(tmp_path), str(out_path))
    stats["status"] = "ok"
    stats["backup"] = str(backup_path)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="ex: _gc_xcec")
    ap.add_argument("--date", help="YYYY-MM-DD; conflita com --all")
    ap.add_argument("--all", action="store_true", help="processa todos os dias disponiveis")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ap.add_argument("--dry-run", action="store_true", help="nao escreve arquivo final")
    args = ap.parse_args()

    if not args.date and not args.all:
        ap.error("forneca --date YYYY-MM-DD ou --all")
    if args.date and args.all:
        ap.error("--date e --all sao mutuamente exclusivos")

    sym_dir = Path(args.data_dir) / args.symbol
    if not sym_dir.exists():
        print(f"ERRO: diretorio nao existe: {sym_dir}", file=sys.stderr)
        sys.exit(1)

    if args.date:
        dates = [args.date]
    else:
        dates = sorted(
            p.name.replace("depth_updates_", "").replace(".csv.gz", "")
            for p in sym_dir.glob("depth_updates_*.csv.gz")
            if not p.name.endswith(".bak_li0")
        )

    print(f"Processando {len(dates)} dia(s) em {sym_dir} (dry_run={args.dry_run})")
    grand_total = 0
    grand_rewritten = 0
    for d in dates:
        r = reconstruct(args.symbol, d, args.data_dir, dry_run=args.dry_run)
        status = r.get("status", "?")
        if status in ("ok", "dry_run_ok"):
            print(
                f"  {d}: {status}  rows={r['rows_total']}  rewritten={r['rows_rewritten']}  "
                f"snaps_applied={r['snap_applied']}  max_rank={r['max_rank_seen']}  "
                f"deletes_no_price={r['deletes_missing_price']}  "
                f"updates_on_new_price={r['updates_on_new_price']}"
            )
            grand_total += r["rows_total"]
            grand_rewritten += r["rows_rewritten"]
        else:
            print(f"  {d}: {status}  {r.get('reason','')}")

    print(f"=== TOTAL: rows={grand_total} rewritten={grand_rewritten} ===")


if __name__ == "__main__":
    main()

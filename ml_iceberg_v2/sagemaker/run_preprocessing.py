#!/usr/bin/env python3
"""
run_preprocessing.py — Feature extraction pré-SageMaker.

Executa extract_features.py para todos os dias disponíveis em S3
e faz upload dos parquets resultantes para S3.

Por defeito, usa skip inteligente: verifica S3 e salta dias que já têm
features + labels válidas (tamanho > MIN_FILE_BYTES).

Usage (local — recomendado):
    # Apenas dias em falta (modo normal):
    python ml_iceberg_v2/sagemaker/run_preprocessing.py --workers 2

    # Apenas uma data:
    python ml_iceberg_v2/sagemaker/run_preprocessing.py --date 2026-01-05

    # A partir de uma data (útil para processar gap recente):
    python ml_iceberg_v2/sagemaker/run_preprocessing.py --start-date 2026-03-27 --workers 2

    # Forçar reprocessamento mesmo que já exista:
    python ml_iceberg_v2/sagemaker/run_preprocessing.py --start-date 2026-03-27 --overwrite --workers 2

Dados de entrada (S3):
    s3://fluxquantumai-data/level2/gc/depth_updates_YYYY-MM-DD.csv.gz
    s3://fluxquantumai-data/level2/gc/trades_YYYY-MM-DD.csv.gz
    s3://fluxquantumai-data/level2/gc/depth_snapshots_YYYY-MM-DD.csv.gz  (opcional)

Saída (S3):
    s3://fluxquantumai-data/features/iceberg_v2/features_YYYY-MM-DD.parquet
    s3://fluxquantumai-data/labels/iceberg_v2/labels_YYYY-MM-DD.parquet
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger("preprocessing")

BUCKET = "fluxquantumai-data"
DEPTH_S3 = f"s3://{BUCKET}/level2/gc"
FEATURES_S3 = f"s3://{BUCKET}/features/iceberg_v2"
LABELS_S3 = f"s3://{BUCKET}/labels/iceberg_v2"

# Ficheiros abaixo deste tamanho (bytes) são considerados corrompidos/incompletos
MIN_FILE_BYTES = 50_000


def _list_s3_objects(s3_prefix: str) -> dict[str, int]:
    """
    Lista objetos em s3_prefix. Retorna dict {filename: size_bytes}.
    """
    result = subprocess.run(
        ["aws", "s3", "ls", s3_prefix.rstrip("/") + "/"],
        capture_output=True, text=True,
    )
    objects: dict[str, int] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            size = int(parts[2])
            name = parts[3]
            objects[name] = size
    return objects


def _list_depth_dates() -> list[str]:
    """Datas disponíveis em S3 que têm depth_updates + trades."""
    objects = _list_s3_objects(DEPTH_S3)
    pattern_depth = re.compile(r"depth_updates_(\d{4}-\d{2}-\d{2})\.csv\.gz")
    dates = []
    for name in objects:
        m = pattern_depth.match(name)
        if m:
            date = m.group(1)
            if f"trades_{date}.csv.gz" in objects:
                dates.append(date)
    return sorted(dates)


def _list_existing_feature_dates(overwrite: bool) -> set[str]:
    """
    Datas que já têm features + labels válidas no S3.
    Exclui ficheiros suspeitos (tamanho < MIN_FILE_BYTES).
    Se overwrite=True, retorna conjunto vazio (tudo será reprocessado).
    """
    if overwrite:
        return set()

    feat_objects = _list_s3_objects(FEATURES_S3)
    label_objects = _list_s3_objects(LABELS_S3)

    done: set[str] = set()

    # Suporta dois padrões de naming: "features_YYYY-MM-DD.parquet" e "YYYY-MM-DD.parquet"
    patterns = [
        re.compile(r"features_(\d{4}-\d{2}-\d{2})\.parquet"),
        re.compile(r"(\d{4}-\d{2}-\d{2})\.parquet"),
    ]

    for name, size in feat_objects.items():
        date = None
        for pat in patterns:
            m = pat.match(name)
            if m:
                date = m.group(1)
                break
        if date is None:
            continue

        # Verificar se o label correspondente também existe e é válido
        label_name_v1 = f"labels_{date}.parquet"
        label_name_v2 = f"{date}.parquet"
        label_size = label_objects.get(label_name_v1, label_objects.get(label_name_v2, 0))

        if size >= MIN_FILE_BYTES and label_size >= MIN_FILE_BYTES:
            done.add(date)
        else:
            reason = []
            if size < MIN_FILE_BYTES:
                reason.append(f"features={size}B")
            if label_size < MIN_FILE_BYTES:
                reason.append(f"labels={label_size}B")
            _logger.warning("  [%s] suspeito/incompleto (%s) — será reprocessado", date, ", ".join(reason))

    return done


def _process_date_subprocess(
    date: str,
    tmp_feat: str,
    tmp_label: str,
) -> tuple[str, bool, str]:
    """
    Chama extract_features para UMA data específica.
    Retorna (date, success, message).
    """
    cmd = [
        sys.executable, "-m", "ml_iceberg_v2.scripts.extract_features",
        "--date", date,
        "--depth-dir", DEPTH_S3,
        "--features-dir", tmp_feat,
        "--labels-dir", tmp_label,
    ]
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        return date, False, f"exit {result.returncode}"
    return date, True, "ok"


def _upload_date(date: str, tmp_feat: str, tmp_label: str) -> None:
    """
    Faz upload imediato dos parquets de UMA data para S3 e remove ficheiros locais.
    Upload por data evita perda total de dados se o processo falhar a meio.
    """
    feat_file = Path(tmp_feat) / f"features_{date}.parquet"
    label_file = Path(tmp_label) / f"labels_{date}.parquet"

    uploaded = 0
    for local, s3_dir in [(feat_file, FEATURES_S3), (label_file, LABELS_S3)]:
        if local.exists():
            s3_uri = f"{s3_dir}/{local.name}"
            subprocess.run(
                ["aws", "s3", "cp", str(local), s3_uri, "--no-progress"],
                check=True,
            )
            local.unlink()  # liberta espaço local
            uploaded += 1

    if uploaded:
        _logger.info("[%s] → S3 (%d ficheiros)", date, uploaded)
    else:
        _logger.warning("[%s] upload: nenhum ficheiro encontrado em %s", date, tmp_feat)


def run(args):
    # --- Determinar lista de datas a processar ---
    if args.date:
        dates_to_process = [args.date]
        _logger.info("Modo single-date: %s", args.date)
    else:
        _logger.info("A consultar S3 para datas disponíveis ...")
        all_dates = _list_depth_dates()
        if not all_dates:
            _logger.error("Nenhuma data encontrada em %s", DEPTH_S3)
            sys.exit(1)
        _logger.info("  depth_updates disponíveis: %d (%s → %s)", len(all_dates), all_dates[0], all_dates[-1])

        # Filtro por --start-date / --end-date
        if args.start_date:
            all_dates = [d for d in all_dates if d >= args.start_date]
            _logger.info("  Após --start-date %s: %d datas", args.start_date, len(all_dates))
        if args.end_date:
            all_dates = [d for d in all_dates if d <= args.end_date]
            _logger.info("  Após --end-date %s: %d datas", args.end_date, len(all_dates))

        # Skip inteligente: consultar features existentes em S3
        _logger.info("A verificar features existentes em S3 ...")
        existing = _list_existing_feature_dates(args.overwrite)
        _logger.info("  Features válidas já em S3: %d", len(existing))

        dates_to_process = [d for d in all_dates if d not in existing]
        skipped = len(all_dates) - len(dates_to_process)
        _logger.info("  Saltados (já existem): %d | A processar: %d", skipped, len(dates_to_process))

    if not dates_to_process:
        _logger.info("Nada a fazer — todos os dias já processados.")
        return

    _logger.info("Datas a processar: %s", dates_to_process)

    # --- Processar com temp dirs partilhados ---
    with tempfile.TemporaryDirectory() as tmp_root:
        tmp_feat = str(Path(tmp_root) / "features")
        tmp_label = str(Path(tmp_root) / "labels")
        Path(tmp_feat).mkdir()
        Path(tmp_label).mkdir()

        ok_count = 0
        fail_count = 0

        workers = min(args.workers, len(dates_to_process))

        if workers <= 1 or len(dates_to_process) == 1:
            for date in dates_to_process:
                _, success, msg = _process_date_subprocess(date, tmp_feat, tmp_label)
                if success:
                    _upload_date(date, tmp_feat, tmp_label)
                    ok_count += 1
                else:
                    fail_count += 1
                    _logger.error("FALHOU %s: %s", date, msg)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_process_date_subprocess, date, tmp_feat, tmp_label): date
                    for date in dates_to_process
                }
                for fut in as_completed(futures):
                    date = futures[fut]
                    try:
                        _, success, msg = fut.result()
                        if success:
                            _upload_date(date, tmp_feat, tmp_label)
                            ok_count += 1
                        else:
                            fail_count += 1
                            _logger.error("FALHOU %s: %s", date, msg)
                    except Exception as exc:
                        fail_count += 1
                        _logger.error("FALHOU %s: %s", date, exc)

        _logger.info("Concluído — %d ok, %d falhados", ok_count, fail_count)

    if fail_count > 0:
        _logger.error("%d datas falharam.", fail_count)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Pre-process features para treino SageMaker")
    parser.add_argument("--date", help="Processar apenas esta data (YYYY-MM-DD)")
    parser.add_argument("--start-date", help="Processar apenas datas >= YYYY-MM-DD")
    parser.add_argument("--end-date", help="Processar apenas datas <= YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=2,
                        help="Workers paralelos (default: 2)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Reprocessar mesmo que já exista em S3")
    args = parser.parse_args()

    if args.date and (args.start_date or args.end_date):
        parser.error("--date não pode ser usado com --start-date / --end-date")

    run(args)


if __name__ == "__main__":
    main()

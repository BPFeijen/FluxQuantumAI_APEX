#!/usr/bin/env python3
"""
train_iceberg_local.py — Treino local do ml_iceberg_v2 (Stage 1 + 2 + 3).

Uso:
    python train_iceberg_local.py

    # Só Stage 1 (teste rápido):
    python train_iceberg_local.py --skip-stage 2 --skip-stage 3 --stage1-epochs 5

    # Retomar a partir do Stage 2 (Stage 1 já concluído):
    python train_iceberg_local.py --skip-stage 1

Output:
    models/iceberg_v2/
        stage1_autoencoder.pt
        stage2_classifier.pt
        stage3_calibrated.pt
        norm_stats.json
        stage1_history.json
        stage2_history.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
_logger = logging.getLogger("train_local")

FEATURES_DIR = "data/features/iceberg_v2"
LABELS_DIR   = "data/labels/iceberg_v2"
MODELS_DIR   = "models/iceberg_v2"


def main():
    parser = argparse.ArgumentParser(description="Treino local ml_iceberg_v2")
    parser.add_argument("--stage1-epochs", type=int, default=50)
    parser.add_argument("--stage2-epochs", type=int, default=50)
    parser.add_argument("--stage1-batch",  type=int, default=512)
    parser.add_argument("--stage2-batch",  type=int, default=256)
    parser.add_argument("--stage1-lr",     type=float, default=1e-3)
    parser.add_argument("--stage2-lr",     type=float, default=3e-4)
    parser.add_argument("--skip-stage",    type=str, action="append", default=[],
                        help="Pular stage: --skip-stage 1  --skip-stage 2  --skip-stage 3")
    args = parser.parse_args()

    skip = set(args.skip_stage)
    models_dir = Path(MODELS_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)

    _logger.info("=" * 60)
    _logger.info("ml_iceberg_v2 — Treino Local")
    _logger.info("features: %s", FEATURES_DIR)
    _logger.info("labels:   %s", LABELS_DIR)
    _logger.info("models:   %s", MODELS_DIR)
    _logger.info("=" * 60)

    results = {}
    t_total = time.time()

    # ---------------------------------------------------------------
    # STAGE 1 — Autoencoder
    # ---------------------------------------------------------------
    if "1" not in skip:
        _logger.info("STAGE 1: Autoencoder (noise-only, unsupervised)")
        from ml_iceberg_v2.training.trainer_stage1 import train_stage1
        t0 = time.time()
        h1 = train_stage1(
            features_dir=FEATURES_DIR,
            labels_dir=LABELS_DIR,
            models_dir=MODELS_DIR,
            epochs=args.stage1_epochs,
            batch_size=args.stage1_batch,
            learning_rate=args.stage1_lr,
            device="cpu",
        )
        elapsed = time.time() - t0
        results["stage1"] = {"best_epoch": h1["best_epoch"],
                              "best_val_loss": min(h1["val_loss"]),
                              "elapsed_min": round(elapsed / 60, 1)}
        with open(models_dir / "stage1_history.json", "w") as f:
            json.dump(h1, f, indent=2)
        _logger.info("Stage 1 COMPLETO — best_val_loss=%.6f  tempo=%.0f min",
                     min(h1["val_loss"]), elapsed / 60)
    else:
        _logger.info("Stage 1 PULADO")

    # ---------------------------------------------------------------
    # STAGE 2 — Classifier
    # ---------------------------------------------------------------
    if "2" not in skip:
        _logger.info("STAGE 2: Classifier (supervised, 4 classes)")
        from ml_iceberg_v2.training.trainer_stage2 import train_stage2
        t0 = time.time()
        h2 = train_stage2(
            features_dir=FEATURES_DIR,
            labels_dir=LABELS_DIR,
            models_dir=MODELS_DIR,
            epochs=args.stage2_epochs,
            batch_size=args.stage2_batch,
            learning_rate=args.stage2_lr,
            device="cpu",
        )
        elapsed = time.time() - t0
        results["stage2"] = {"best_epoch": h2["best_epoch"],
                              "best_val_f1": max(h2["val_f1_macro"]),
                              "elapsed_min": round(elapsed / 60, 1)}
        with open(models_dir / "stage2_history.json", "w") as f:
            json.dump(h2, f, indent=2)
        _logger.info("Stage 2 COMPLETO — best_val_f1=%.4f  tempo=%.0f min",
                     max(h2["val_f1_macro"]), elapsed / 60)
    else:
        _logger.info("Stage 2 PULADO")

    # ---------------------------------------------------------------
    # STAGE 3 — Calibration
    # ---------------------------------------------------------------
    if "3" not in skip:
        _logger.info("STAGE 3: Temperature calibration")
        from ml_iceberg_v2.training.trainer_stage3 import calibrate_stage3
        t0 = time.time()
        cal = calibrate_stage3(
            features_dir=FEATURES_DIR,
            labels_dir=LABELS_DIR,
            models_dir=MODELS_DIR,
            device="cpu",
        )
        elapsed = time.time() - t0
        results["stage3"] = {**cal, "elapsed_min": round(elapsed / 60, 1)}
        _logger.info("Stage 3 COMPLETO — T=%.4f  ECE %.4f→%.4f  tempo=%.0f min",
                     cal["temperature"], cal["ece_before"], cal["ece_after"], elapsed / 60)
    else:
        _logger.info("Stage 3 PULADO")

    # ---------------------------------------------------------------
    # Resumo
    # ---------------------------------------------------------------
    total_min = (time.time() - t_total) / 60
    results["total_elapsed_min"] = round(total_min, 1)

    summary_path = models_dir / "training_results.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2)

    _logger.info("=" * 60)
    _logger.info("TREINO COMPLETO — %.0f min (%.1f h)", total_min, total_min / 60)
    _logger.info("Modelos em: %s", MODELS_DIR)
    _logger.info("=" * 60)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
train_entry.py — SageMaker Training Job entry point for ml_iceberg_v2.

SageMaker runs this script as:
    python train_entry.py

Environment variables set by SageMaker:
    SM_CHANNEL_FEATURES  → /opt/ml/input/data/features/
    SM_CHANNEL_LABELS    → /opt/ml/input/data/labels/
    SM_OUTPUT_DATA_DIR   → /opt/ml/output/data/
    SM_MODEL_DIR         → /opt/ml/model/
    SM_HPS               → JSON string of hyperparameters

Hyperparameters (passed via --hyperparameters in launch_job.py):
    stage1_epochs     (int,   default 50)
    stage2_epochs     (int,   default 50)
    stage1_batch      (int,   default 512)
    stage2_batch      (int,   default 256)
    stage1_lr         (float, default 1e-3)
    stage2_lr         (float, default 3e-4)
    skip_stage        (str,   default "none")  — "1", "2", "3", or "none"

Output layout (written to SM_MODEL_DIR):
    stage1_autoencoder.pt
    stage2_classifier.pt
    stage3_calibrated.pt
    norm_stats.json
    stage1_history.json
    stage2_history.json

Usage for local testing (mimicking SageMaker layout):
    SM_CHANNEL_FEATURES=/data/features/iceberg_v2 \\
    SM_CHANNEL_LABELS=/data/labels/iceberg_v2 \\
    SM_MODEL_DIR=/models/iceberg_v2 \\
    SM_HPS='{"stage1_epochs":5,"stage2_epochs":3}' \\
    python train_entry.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

# Install pyarrow before any other import — the PyTorch DLC container may not have it.
# Using subprocess avoids pip conflicts caused by a requirements.txt during container init.
def _ensure_pyarrow() -> None:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "pyarrow>=12.0.0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

_ensure_pyarrow()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
_logger = logging.getLogger("sagemaker_entry")


def _hp(key: str, default, cast=str):
    """Read a hyperparameter from SM_HPS JSON or environment."""
    hps_raw = os.environ.get("SM_HPS", "{}")
    try:
        hps = json.loads(hps_raw)
    except json.JSONDecodeError:
        hps = {}
    val = hps.get(key, os.environ.get(key.upper(), default))
    if val is None:
        return default
    try:
        return cast(val)
    except (ValueError, TypeError):
        return default


def _detect_device() -> str:
    """Return 'cuda' if GPU is available, else 'cpu'."""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def main():
    # --- Paths ---
    features_dir = os.environ.get("SM_CHANNEL_FEATURES", "/opt/ml/input/data/features")
    labels_dir = os.environ.get("SM_CHANNEL_LABELS", "/opt/ml/input/data/labels")
    model_dir = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
    output_dir = os.environ.get("SM_OUTPUT_DATA_DIR", "/opt/ml/output/data")

    _logger.info("features_dir: %s", features_dir)
    _logger.info("labels_dir:   %s", labels_dir)
    _logger.info("model_dir:    %s", model_dir)

    Path(model_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # --- Hyperparameters ---
    stage1_epochs  = _hp("stage1_epochs",  50, int)
    stage2_epochs  = _hp("stage2_epochs",  50, int)
    stage1_batch   = _hp("stage1_batch",   512, int)
    stage2_batch   = _hp("stage2_batch",   256, int)
    stage1_lr      = _hp("stage1_lr",      1e-3, float)
    stage2_lr      = _hp("stage2_lr",      3e-4, float)
    skip_stage     = _hp("skip_stage",     "none", str).lower()
    # S3 URI of a pre-trained Stage 1 checkpoint to download when skip_stage=1
    # e.g. s3://fluxquantumai-data/sagemaker/models/.../model.tar.gz
    stage1_s3_uri  = _hp("stage1_s3_uri",  "", str).strip()

    # If skip_stage=1 and stage1_s3_uri provided, download and extract checkpoint
    if skip_stage == "1" and stage1_s3_uri:
        _logger.info("Downloading Stage 1 checkpoint from %s", stage1_s3_uri)
        import boto3, tarfile as _tarfile, io as _io
        s3 = boto3.client("s3")
        bucket, _, key = stage1_s3_uri.replace("s3://", "").partition("/")
        obj = s3.get_object(Bucket=bucket, Key=key)
        tar_bytes = obj["Body"].read()
        with _tarfile.open(fileobj=_io.BytesIO(tar_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                member.name = Path(member.name).name  # strip any directory prefix
                tar.extract(member, path=model_dir)
        _logger.info("Stage 1 checkpoint extracted to %s", model_dir)

    device = _detect_device()
    _logger.info(
        "HPs: s1_epochs=%d  s2_epochs=%d  s1_batch=%d  s2_batch=%d  "
        "s1_lr=%g  s2_lr=%g  skip=%s  device=%s",
        stage1_epochs, stage2_epochs, stage1_batch, stage2_batch,
        stage1_lr, stage2_lr, skip_stage, device,
    )

    results = {}

    # ---------------------------------------------------------------
    # STAGE 1 — Autoencoder
    # ---------------------------------------------------------------
    if skip_stage != "1":
        _logger.info("=" * 60)
        _logger.info("STAGE 1: Training autoencoder (noise-only, unsupervised)")
        _logger.info("=" * 60)
        from ml_iceberg_v2.training.trainer_stage1 import train_stage1

        h1 = train_stage1(
            features_dir=features_dir,
            labels_dir=labels_dir,
            models_dir=model_dir,
            epochs=stage1_epochs,
            batch_size=stage1_batch,
            learning_rate=stage1_lr,
            device=device,
        )
        results["stage1"] = {
            "best_epoch": h1["best_epoch"],
            "best_val_loss": min(h1["val_loss"]),
        }
        # Write history JSON
        with open(Path(model_dir) / "stage1_history.json", "w") as f:
            json.dump(h1, f, indent=2)

        # SageMaker metric line (parsed by CloudWatch)
        print(f"[Stage1] best_val_loss={min(h1['val_loss']):.6f};")
    else:
        _logger.info("Skipping Stage 1 (skip_stage=%s)", skip_stage)

    # ---------------------------------------------------------------
    # STAGE 2 — Classifier
    # ---------------------------------------------------------------
    if skip_stage != "2":
        _logger.info("=" * 60)
        _logger.info("STAGE 2: Training classifier (supervised, 4 classes)")
        _logger.info("=" * 60)
        from ml_iceberg_v2.training.trainer_stage2 import train_stage2

        h2 = train_stage2(
            features_dir=features_dir,
            labels_dir=labels_dir,
            models_dir=model_dir,
            epochs=stage2_epochs,
            batch_size=stage2_batch,
            learning_rate=stage2_lr,
            device=device,
        )
        results["stage2"] = {
            "best_epoch": h2["best_epoch"],
            "best_val_f1": max(h2["val_f1_macro"]),
        }
        with open(Path(model_dir) / "stage2_history.json", "w") as f:
            json.dump(h2, f, indent=2)

        print(f"[Stage2] best_val_f1_macro={max(h2['val_f1_macro']):.4f};")
    else:
        _logger.info("Skipping Stage 2 (skip_stage=%s)", skip_stage)

    # ---------------------------------------------------------------
    # STAGE 3 — Calibration
    # ---------------------------------------------------------------
    if skip_stage != "3":
        _logger.info("=" * 60)
        _logger.info("STAGE 3: Temperature calibration")
        _logger.info("=" * 60)
        from ml_iceberg_v2.training.trainer_stage3 import calibrate_stage3

        cal = calibrate_stage3(
            features_dir=features_dir,
            labels_dir=labels_dir,
            models_dir=model_dir,
            device=device,
        )
        results["stage3"] = cal
        print(
            f"[Stage3] temperature={cal['temperature']:.4f}  "
            f"ece_before={cal['ece_before']:.4f}  ece_after={cal['ece_after']:.4f};"
        )
    else:
        _logger.info("Skipping Stage 3 (skip_stage=%s)", skip_stage)

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    results_path = Path(output_dir) / "training_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    _logger.info("=" * 60)
    _logger.info("Training complete. Results: %s", json.dumps(results, indent=2))
    _logger.info("=" * 60)


if __name__ == "__main__":
    import traceback

    failure_file = Path(os.environ.get("SM_OUTPUT_DATA_DIR", "/opt/ml/output/data")).parent / "failure"

    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        _logger.error("FATAL — training failed:\n%s", tb)
        # Write traceback to /opt/ml/output/failure so SageMaker surfaces it
        # in describe-training-job FailureReason.ErrorMessage
        try:
            failure_file.parent.mkdir(parents=True, exist_ok=True)
            with open(failure_file, "w") as _f:
                _f.write(tb[-1024:])  # SageMaker reads last 1024 bytes
        except Exception:
            pass  # best-effort; don't mask original error
        raise

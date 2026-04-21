#!/usr/bin/env python3
"""
launch_job.py — Launch ml_iceberg_v2 SageMaker Training Job.

Prerequisites:
    pip install sagemaker boto3
    AWS credentials configured (or IAM role on EC2/SageMaker Studio)

Assumptions:
    - Features parquet files are at: s3://fluxquantumai-data/features/iceberg_v2/
    - Labels  parquet files are at: s3://fluxquantumai-data/labels/iceberg_v2/
    - Source code is zipped and uploaded to S3 (done by this script)
    - SageMaker execution role ARN is set via env var SM_ROLE_ARN or hardcoded below

Usage:
    python ml_iceberg_v2/sagemaker/launch_job.py

    # Override instance / epochs:
    python ml_iceberg_v2/sagemaker/launch_job.py \\
        --instance ml.p3.2xlarge \\
        --stage1-epochs 30 --stage2-epochs 30

    # Dry-run (print config, don't submit):
    python ml_iceberg_v2/sagemaker/launch_job.py --dry-run

Data prep (run once before training if features don't exist in S3):
    # 1. Run feature extraction locally for all available days
    python -m ml_iceberg_v2.scripts.extract_features \\
        --all \\
        --depth-dir s3://fluxquantumai-data/level2/gc/ \\
        --features-dir /tmp/features/iceberg_v2/ \\
        --labels-dir  /tmp/labels/iceberg_v2/ \\
        --workers 4

    # 2. Upload to S3
    aws s3 sync /tmp/features/iceberg_v2/ s3://fluxquantumai-data/features/iceberg_v2/
    aws s3 sync /tmp/labels/iceberg_v2/   s3://fluxquantumai-data/labels/iceberg_v2/
"""

from __future__ import annotations

import argparse
import datetime
import io
import logging
import os
import subprocess
import sys
import zipfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger("launch_job")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — edit these or pass via CLI / env vars
# ─────────────────────────────────────────────────────────────────────────────
BUCKET = "fluxquantumai-data"
FEATURES_S3_URI = f"s3://{BUCKET}/features/iceberg_v2"
LABELS_S3_URI   = f"s3://{BUCKET}/labels/iceberg_v2"
CODE_S3_PREFIX  = f"s3://{BUCKET}/sagemaker/source"
MODELS_S3_PREFIX = f"s3://{BUCKET}/sagemaker/models"

# SageMaker execution role — must have S3 + SageMaker permissions
# Set via SM_ROLE_ARN env var or hardcode below
ROLE_ARN = os.environ.get(
    "SM_ROLE_ARN",
    "arn:aws:iam::116101834074:role/SageMakerExecutionRole",
)

REGION = "us-east-1"

# ─────────────────────────────────────────────────────────────────────────────

def _get_training_image(instance_type: str) -> str:
    """Return the correct PyTorch DLC image URI based on instance type (CPU vs GPU)."""
    is_gpu = any(instance_type.startswith(p) for p in ("ml.p", "ml.g"))
    if is_gpu:
        return f"763104351884.dkr.ecr.{REGION}.amazonaws.com/pytorch-training:2.1.0-gpu-py310-cu118-ubuntu20.04-sagemaker"
    else:
        return f"763104351884.dkr.ecr.{REGION}.amazonaws.com/pytorch-training:2.1.0-cpu-py310-ubuntu20.04-sagemaker"


def _zip_source(repo_root: Path) -> bytes:
    """
    Zip the ml_iceberg_v2 package + sagemaker/ directory into an in-memory archive.
    Excludes __pycache__, .pyc, .venv, and data directories.
    """
    buf = io.BytesIO()
    exclusions = {"__pycache__", ".venv", ".git", "data", "models", "artifacts", "reports", "logs"}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        pkg_root = repo_root / "ml_iceberg_v2"
        for path in sorted(pkg_root.rglob("*")):
            if any(ex in path.parts for ex in exclusions):
                continue
            if path.suffix == ".pyc":
                continue
            arcname = path.relative_to(repo_root).as_posix()
            zf.write(path, arcname)

        # Include top-level train_entry wrapper for SageMaker
        entry_wrapper = repo_root / "ml_iceberg_v2" / "sagemaker" / "train_entry.py"
        if entry_wrapper.exists():
            zf.write(entry_wrapper, "train_entry.py")  # SageMaker requires at repo root

        # NOTE: requirements.txt intentionally excluded — the PyTorch DLC container
        # already has numpy/pandas. pyarrow is installed by train_entry.py at runtime
        # via subprocess to avoid pip conflicts during container init.

    buf.seek(0)
    return buf.read()


def _upload_source(zip_bytes: bytes, job_name: str) -> str:
    """Upload source zip to S3 and return the S3 URI."""
    import boto3
    s3 = boto3.client("s3", region_name=REGION)
    key = f"sagemaker/source/{job_name}/source.tar.gz"

    # SageMaker expects tar.gz; repack as tar.gz
    import gzip, tarfile, io as _io
    tar_buf = _io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        # Write the zip contents as individual files (skip directory entries)
        with zipfile.ZipFile(_io.BytesIO(zip_bytes)) as zf:
            for member in zf.infolist():
                if member.filename.endswith("/"):
                    continue  # skip directory entries — tar auto-creates parents
                data = zf.read(member.filename)
                info = tarfile.TarInfo(name=member.filename)
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, _io.BytesIO(data))

    tar_buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=tar_buf.getvalue())
    uri = f"s3://{BUCKET}/{key}"
    _logger.info("Uploaded source → %s", uri)
    return uri


def launch(
    instance_type: str = "ml.p3.2xlarge",
    stage1_epochs: int = 50,
    stage2_epochs: int = 50,
    stage1_batch: int = 512,
    stage2_batch: int = 256,
    stage1_lr: float = 1e-3,
    stage2_lr: float = 3e-4,
    dry_run: bool = False,
    volume_size_gb: int = 50,
) -> str:
    """
    Submit a SageMaker Training Job for ml_iceberg_v2.

    Returns the job name.
    """
    import json
    import boto3

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    job_name = f"iceberg-v2-{timestamp}"

    hyperparameters = {
        "stage1_epochs": str(stage1_epochs),
        "stage2_epochs": str(stage2_epochs),
        "stage1_batch":  str(stage1_batch),
        "stage2_batch":  str(stage2_batch),
        "stage1_lr":     str(stage1_lr),
        "stage2_lr":     str(stage2_lr),
        # SageMaker PyTorch DLC toolkit reads these HPs (NOT env vars) to locate
        # and run the user entry script. Must be hyperparameters, not Environment.
        "sagemaker_program":          "train_entry.py",
        "sagemaker_submit_directory": "",  # filled in after source upload below
    }

    config = {
        "TrainingJobName": job_name,
        "RoleArn": ROLE_ARN,
        "AlgorithmSpecification": {
            "TrainingImage": _get_training_image(instance_type),
            "TrainingInputMode": "File",
        },
        "HyperParameters": hyperparameters,
        "InputDataConfig": [
            {
                "ChannelName": "features",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": FEATURES_S3_URI,
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
                "ContentType": "application/x-parquet",
                "CompressionType": "None",
            },
            {
                "ChannelName": "labels",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": LABELS_S3_URI,
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
                "ContentType": "application/x-parquet",
                "CompressionType": "None",
            },
        ],
        "OutputDataConfig": {
            "S3OutputPath": f"{MODELS_S3_PREFIX}/{job_name}/",
        },
        "ResourceConfig": {
            "InstanceType": instance_type,
            "InstanceCount": 1,
            "VolumeSizeInGB": volume_size_gb,
        },
        "StoppingCondition": {
            "MaxRuntimeInSeconds": 86400,  # 24h max
        },
        "Environment": {
            "SM_HPS": str(hyperparameters).replace("'", '"'),
        },
    }

    _logger.info("Job name:        %s", job_name)
    _logger.info("Instance type:   %s", instance_type)
    _logger.info("Features input:  %s", FEATURES_S3_URI)
    _logger.info("Labels input:    %s", LABELS_S3_URI)
    _logger.info("Model output:    %s/%s/", MODELS_S3_PREFIX, job_name)
    _logger.info("Hyperparameters: %s", hyperparameters)

    # Upload source code
    _logger.info("Packaging and uploading source code...")
    repo_root = Path(__file__).parents[2]  # FluxQuantumAI/
    zip_bytes = _zip_source(repo_root)
    source_uri = _upload_source(zip_bytes, job_name)

    # Update sagemaker_submit_directory HP now that source_uri is known
    config["HyperParameters"]["sagemaker_submit_directory"] = source_uri
    # Fix SM_HPS to proper JSON (not Python repr)
    config["Environment"]["SM_HPS"] = json.dumps(hyperparameters)

    if dry_run:
        _logger.info("[DRY RUN] Would submit job: %s", job_name)
        import json as _json
        print(_json.dumps(config, indent=2))
        return job_name

    sm = boto3.client("sagemaker", region_name=REGION)
    sm.create_training_job(**config)
    _logger.info("Job submitted: %s", job_name)
    _logger.info("Monitor at: https://console.aws.amazon.com/sagemaker/home?region=%s#/jobs/%s", REGION, job_name)

    return job_name


def main():
    parser = argparse.ArgumentParser(description="Launch ml_iceberg_v2 SageMaker training")
    parser.add_argument("--instance", default="ml.p3.2xlarge",
                        help="SageMaker instance type (default: ml.p3.2xlarge)")
    parser.add_argument("--stage1-epochs", type=int, default=50)
    parser.add_argument("--stage2-epochs", type=int, default=50)
    parser.add_argument("--stage1-batch", type=int, default=512)
    parser.add_argument("--stage2-batch", type=int, default=256)
    parser.add_argument("--stage1-lr", type=float, default=1e-3)
    parser.add_argument("--stage2-lr", type=float, default=3e-4)
    parser.add_argument("--volume-gb", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print config without submitting")
    args = parser.parse_args()

    job_name = launch(
        instance_type=args.instance,
        stage1_epochs=args.stage1_epochs,
        stage2_epochs=args.stage2_epochs,
        stage1_batch=args.stage1_batch,
        stage2_batch=args.stage2_batch,
        stage1_lr=args.stage1_lr,
        stage2_lr=args.stage2_lr,
        dry_run=args.dry_run,
        volume_size_gb=args.volume_gb,
    )

    if not args.dry_run:
        print(f"\nJob submitted: {job_name}")
        print(f"Check status: aws sagemaker describe-training-job --training-job-name {job_name}")


if __name__ == "__main__":
    main()

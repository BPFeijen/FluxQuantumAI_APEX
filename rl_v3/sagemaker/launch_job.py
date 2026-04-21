"""
launch_job.py — Submete SageMaker Training Job para RL V3 PPO.

Canais de entrada:
    features  → s3://fluxquantumai-data/features/iceberg_v2/
    labels    → s3://fluxquantumai-data/labels/iceberg_v2/
    grenadier → s3://fluxquantumai-data/sagemaker/grenadier_v2/models/{job}/output/
                (norm_stats + calibration do Grenadier V2 treinado)

Pré-requisitos:
    1. Grenadier V2 treinado — copiar artefactos para S3_GRENADIER_URI
    2. ml_iceberg_v2 validado em produção
    3. GPU quota aprovada (ml.p3.2xlarge recomendado para PPO vectorizado)

Uso:
    python rl_v3/sagemaker/launch_job.py \
        --grenadier-job grenadier-v2-20260411-131545 \
        --timesteps 2000000 \
        --n-envs 4

    python rl_v3/sagemaker/launch_job.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import io
import logging
import os
import sys
import tarfile
import zipfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
_logger = logging.getLogger("rl_launch")

BUCKET          = "fluxquantumai-data"
FEATURES_S3_URI = f"s3://{BUCKET}/features/iceberg_v2"
LABELS_S3_URI   = f"s3://{BUCKET}/labels/iceberg_v2"
MODELS_S3_URI   = f"s3://{BUCKET}/sagemaker/rl_v3/models"
REGION          = "us-east-1"
ROLE_ARN        = os.environ.get(
    "SM_ROLE_ARN",
    "arn:aws:iam::116101834074:role/SageMakerExecutionRole",
)

# Imagem PyTorch CPU/GPU
CPU_IMAGE = f"763104351884.dkr.ecr.{REGION}.amazonaws.com/pytorch-training:2.1.0-cpu-py310-ubuntu20.04-sagemaker"
GPU_IMAGE = f"763104351884.dkr.ecr.{REGION}.amazonaws.com/pytorch-training:2.1.0-gpu-py310-cu118-ubuntu20.04-sagemaker"


def _zip_source(repo_root: Path) -> bytes:
    buf = io.BytesIO()
    exclusions = {"__pycache__", ".venv", ".git", "data", "logs"}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pkg in ["rl_v3", "schemas"]:
            pkg_root = repo_root / pkg
            if not pkg_root.exists():
                continue
            for path in sorted(pkg_root.rglob("*")):
                if any(ex in path.parts for ex in exclusions):
                    continue
                if path.suffix == ".pyc":
                    continue
                zf.write(path, str(path.relative_to(repo_root)))
        entry = repo_root / "rl_v3" / "sagemaker" / "train_entry.py"
        if entry.exists():
            zf.write(entry, "train_entry.py")
    buf.seek(0)
    return buf.read()


def _upload_source(zip_bytes: bytes, job_name: str) -> str:
    import boto3, io as _io
    s3  = boto3.client("s3", region_name=REGION)
    key = f"sagemaker/rl_v3/source/{job_name}/source.tar.gz"

    tar_buf = _io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        with zipfile.ZipFile(_io.BytesIO(zip_bytes)) as zf:
            for member in zf.infolist():
                if member.filename.endswith("/"):
                    continue  # skip dir entries
                data = zf.read(member.filename)
                info = tarfile.TarInfo(name=member.filename)
                info.size = len(data)
                tar.addfile(info, _io.BytesIO(data))

    tar_buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=tar_buf.getvalue())
    uri = f"s3://{BUCKET}/{key}"
    _logger.info("Source → %s", uri)
    return uri


def launch(
    grenadier_job: str,
    instance_type: str = "ml.p3.2xlarge",
    timesteps: int = 2_000_000,
    n_envs: int = 4,
    learning_rate: float = 3e-4,
    batch_size: int = 256,
    max_drawdown_pts: float = 20.0,
    anomaly_penalty: float = 2.0,
    eval_freq: int = 50_000,
    volume_size_gb: int = 50,
    dry_run: bool = False,
) -> str:
    import boto3

    ts       = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    job_name = f"fluxquantum-rl-v3-{ts}"

    repo_root  = Path(__file__).parents[2]
    zip_bytes  = _zip_source(repo_root)
    source_uri = _upload_source(zip_bytes, job_name)

    # Canal grenadier: artefactos do Grenadier V2 treinado
    grenadier_s3 = f"s3://{BUCKET}/sagemaker/grenadier_v2/models/{grenadier_job}/output/"

    is_gpu = "p2" in instance_type or "p3" in instance_type or "p4" in instance_type
    image  = GPU_IMAGE if is_gpu else CPU_IMAGE

    hps = {
        "sagemaker_program":          "train_entry.py",
        "sagemaker_submit_directory": source_uri,
        "timesteps":                  str(timesteps),
        "n_envs":                     str(n_envs),
        "learning_rate":              str(learning_rate),
        "batch_size":                 str(batch_size),
        "max_drawdown_pts":           str(max_drawdown_pts),
        "anomaly_penalty":            str(anomaly_penalty),
        "eval_freq":                  str(eval_freq),
        "device":                     "cuda" if is_gpu else "cpu",
    }

    config = {
        "TrainingJobName": job_name,
        "RoleArn":         ROLE_ARN,
        "AlgorithmSpecification": {
            "TrainingImage":     image,
            "TrainingInputMode": "File",
        },
        "HyperParameters": hps,
        "InputDataConfig": [
            {
                "ChannelName": "features",
                "DataSource": {"S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": FEATURES_S3_URI,
                    "S3DataDistributionType": "FullyReplicated",
                }},
            },
            {
                "ChannelName": "labels",
                "DataSource": {"S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": LABELS_S3_URI,
                    "S3DataDistributionType": "FullyReplicated",
                }},
            },
            {
                "ChannelName": "grenadier",
                "DataSource": {"S3DataSource": {
                    "S3DataType": "S3Prefix",
                    "S3Uri": grenadier_s3,
                    "S3DataDistributionType": "FullyReplicated",
                }},
            },
        ],
        "OutputDataConfig": {"S3OutputPath": f"{MODELS_S3_URI}/{job_name}/"},
        "ResourceConfig": {
            "InstanceType":   instance_type,
            "InstanceCount":  1,
            "VolumeSizeInGB": volume_size_gb,
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": 86400},
    }

    _logger.info("Job:          %s", job_name)
    _logger.info("Instance:     %s  (%s)", instance_type, "GPU" if is_gpu else "CPU")
    _logger.info("Timesteps:    %d", timesteps)
    _logger.info("Grenadier:    %s", grenadier_s3)

    if dry_run:
        import json
        print(json.dumps(config, indent=2))
        return job_name

    sm = boto3.client("sagemaker", region_name=REGION)
    sm.create_training_job(**config)
    _logger.info("Job submetido: %s", job_name)
    _logger.info("Monitor: https://console.aws.amazon.com/sagemaker/home?region=%s#/jobs/%s", REGION, job_name)
    return job_name


def main():
    p = argparse.ArgumentParser(description="Launch RL V3 PPO SageMaker job")
    p.add_argument("--grenadier-job",    required=True, help="Nome do job Grenadier V2 (para carregar norm_stats)")
    p.add_argument("--instance",         default="ml.p3.2xlarge")
    p.add_argument("--timesteps",        type=int,   default=2_000_000)
    p.add_argument("--n-envs",           type=int,   default=4)
    p.add_argument("--learning-rate",    type=float, default=3e-4)
    p.add_argument("--batch-size",       type=int,   default=256)
    p.add_argument("--max-drawdown-pts", type=float, default=20.0)
    p.add_argument("--anomaly-penalty",  type=float, default=2.0)
    p.add_argument("--eval-freq",        type=int,   default=50_000)
    p.add_argument("--volume-gb",        type=int,   default=50)
    p.add_argument("--dry-run",          action="store_true")
    args = p.parse_args()

    job_name = launch(
        grenadier_job=args.grenadier_job,
        instance_type=args.instance,
        timesteps=args.timesteps,
        n_envs=args.n_envs,
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        max_drawdown_pts=args.max_drawdown_pts,
        anomaly_penalty=args.anomaly_penalty,
        eval_freq=args.eval_freq,
        volume_size_gb=args.volume_gb,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        print(f"\nJob: {job_name}")
        print(f"Status: aws sagemaker describe-training-job --training-job-name {job_name}")


if __name__ == "__main__":
    main()

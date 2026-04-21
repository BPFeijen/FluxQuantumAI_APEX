#!/usr/bin/env python3
"""
train_stage1.py — CLI: train Stage 1 autoencoder.

Usage (local):
    python -m ml_iceberg_v2.scripts.train_stage1 \\
        --features-dir /data/features/iceberg_v2/ \\
        --labels-dir /data/labels/iceberg_v2/ \\
        --output-dir /models/iceberg_v2/ \\
        --epochs 50 --device cuda

Usage (SageMaker entry point — called by train_entry.py):
    python -m ml_iceberg_v2.scripts.train_stage1 [same args]
"""

import argparse
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="Train Stage 1 autoencoder")
    parser.add_argument("--features-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    from ml_iceberg_v2.training.trainer_stage1 import train_stage1

    history = train_stage1(
        features_dir=args.features_dir,
        labels_dir=args.labels_dir,
        models_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        device=args.device,
    )

    # Write history JSON for SageMaker metrics
    import json
    from pathlib import Path
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    history_path = out / "stage1_history.json"
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)
    print(f"Stage 1 complete. History saved to {history_path}")
    print(f"Best val_loss={min(history['val_loss']):.6f} at epoch {history['best_epoch']}")


if __name__ == "__main__":
    main()

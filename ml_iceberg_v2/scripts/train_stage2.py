#!/usr/bin/env python3
"""
train_stage2.py — CLI: train Stage 2 classifier.

Usage (local):
    python -m ml_iceberg_v2.scripts.train_stage2 \\
        --features-dir /data/features/iceberg_v2/ \\
        --labels-dir /data/labels/iceberg_v2/ \\
        --output-dir /models/iceberg_v2/ \\
        --autoencoder-checkpoint /models/iceberg_v2/stage1_autoencoder.pt \\
        --epochs 50 --device cuda
"""

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="Train Stage 2 classifier")
    parser.add_argument("--features-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--autoencoder-checkpoint",
                        help="Path to stage1_autoencoder.pt (defaults to output-dir/stage1_autoencoder.pt)")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    from ml_iceberg_v2.training.trainer_stage2 import train_stage2

    history = train_stage2(
        autoencoder_checkpoint=args.autoencoder_checkpoint,
        features_dir=args.features_dir,
        labels_dir=args.labels_dir,
        models_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        device=args.device,
    )

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    history_path = out / "stage2_history.json"
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)
    print(f"Stage 2 complete. History saved to {history_path}")
    best_f1 = max(history["val_f1_macro"])
    print(f"Best val_f1_macro={best_f1:.4f} at epoch {history['best_epoch']}")


if __name__ == "__main__":
    main()

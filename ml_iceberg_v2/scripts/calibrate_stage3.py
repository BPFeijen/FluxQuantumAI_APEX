#!/usr/bin/env python3
"""
calibrate_stage3.py — CLI: Platt/temperature calibration (Stage 3).

Usage (Paperspace):
    python -m ml_iceberg_v2.scripts.calibrate_stage3 \
        --classifier-ckpt /models/iceberg_v2/classifier_best.pt \
        --features-dir /data/features/iceberg_v2/ \
        --labels-dir /data/labels/iceberg_v2/ \
        --output-dir /models/iceberg_v2/ \
        --device cuda
"""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Calibrate Stage 3 temperature scaling")
    parser.add_argument("--classifier-ckpt", required=True)
    parser.add_argument("--features-dir", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    raise NotImplementedError("Implemented in training sprint")


if __name__ == "__main__":
    main()

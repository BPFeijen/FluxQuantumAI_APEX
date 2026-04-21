#!/usr/bin/env python3
"""
run_inference.py — CLI: run full inference pipeline on a day of GC L2 data.

Usage:
    python -m ml_iceberg_v2.scripts.run_inference \
        --date 2026-03-06 \
        --depth-dir /data/level2/_gc_xcec/ \
        --autoencoder-ckpt /models/iceberg_v2/autoencoder_best.pt \
        --classifier-ckpt /models/iceberg_v2/classifier_best.pt \
        --temperature 1.05 \
        --output-dir /data/inference/iceberg_v2/

Output: iceberg_signals_<date>.parquet
"""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Run iceberg inference for one GC day")
    parser.add_argument("--date", required=True)
    parser.add_argument("--depth-dir", required=True)
    parser.add_argument("--trades-dir", required=True)
    parser.add_argument("--autoencoder-ckpt", required=True)
    parser.add_argument("--classifier-ckpt", required=True)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    raise NotImplementedError("Implemented in inference sprint")


if __name__ == "__main__":
    main()

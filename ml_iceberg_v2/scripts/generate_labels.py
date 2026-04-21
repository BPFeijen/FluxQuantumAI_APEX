#!/usr/bin/env python3
"""
generate_labels.py — CLI: generate labels from chains + features for one day.

Usage:
    python -m ml_iceberg_v2.scripts.generate_labels \
        --date 2025-12-29 \
        --features-dir /data/features/iceberg_v2/ \
        --output-dir /data/labels/iceberg_v2/
"""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Generate iceberg labels for one GC day")
    parser.add_argument("--date", required=True)
    parser.add_argument("--features-dir", required=True)
    parser.add_argument("--chains-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    raise NotImplementedError("Implemented in T-106")


if __name__ == "__main__":
    main()

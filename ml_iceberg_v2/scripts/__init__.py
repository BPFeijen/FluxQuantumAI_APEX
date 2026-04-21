"""
scripts/ — CLI entry points for the ml_iceberg_v2 pipeline.

extract_features.py   : Day-level feature extraction (T-105)
generate_labels.py    : Day-level label generation (T-106)
train_stage1.py       : Autoencoder training
train_stage2.py       : Classifier training
calibrate_stage3.py   : Temperature calibration
run_inference.py      : Full inference pipeline
"""

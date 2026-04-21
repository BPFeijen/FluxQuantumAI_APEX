"""
ml_iceberg_v2 — Iceberg detection ML module for GC (Gold Futures, XCEC).

Architecture (3-stage pipeline):
  Stage 1 — Unsupervised: Autoencoder learns normal DOM behaviour.
  Stage 2 — Supervised:   Classifier trained on labelled refill chains.
  Stage 3 — Calibration:  Platt scaling for calibrated confidence scores.

Sub-modules
-----------
features/   — RefillDetector, FeatureExtractor, LabelGenerator, DOMConventionGate
models/     — IcebergAutoencoder, IcebergClassifier
prediction/ — VolumePredictor, AbsorptionStateTracker
training/   — Dataset, Trainer (stage 1/2/3)
inference/  — IcebergInference (runtime pipeline)
output/     — IcebergOutputV2 (canonical signal schema)
validation/ — WalkForward, ReportGenerator
scripts/    — CLI entry points (extract, train, infer)

Data source: L2 depth_updates + trades CSV.GZ from DXFeed/CME, GC on XCEC.
Tick size: 0.10 USD/oz.
"""

__version__ = "2.0.0"

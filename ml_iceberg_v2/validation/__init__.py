"""
validation/ — Walk-forward evaluation and reporting for iceberg detection models.

Components
----------
walk_forward     : Implements expanding-window walk-forward validation
                   (60-day train, 5-day test). Produces per-fold metrics:
                   precision/recall/F1 per class, AUC-ROC, confusion matrix.
report_generator : Renders validation results to Markdown reports in
                   docs/validation/iceberg_ml_v2/.
"""

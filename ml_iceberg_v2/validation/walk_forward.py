"""
walk_forward — Walk-forward validation for iceberg detection models.

Implements expanding-window validation:
  - Train window: 60 days
  - Test window : 5 days
  - Folds       : determined by available labelled data range

Metrics per fold: precision/recall/F1 per class, macro AUC-ROC, confusion matrix.

Public API
----------
WalkForwardValidator(features_dir, labels_dir, models_dir)
    .run(n_folds) → List[FoldResult]
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FoldResult:
    fold_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    metrics: Dict          # precision/recall/F1 per class
    confusion_matrix: List  # 4×4 list of lists
    auc_roc_macro: float
    n_test_windows: int


class WalkForwardValidator:
    """
    Expanding-window walk-forward validator.

    Parameters
    ----------
    features_dir : Path
    labels_dir : Path
    models_dir : Path
    train_days : int  (default 60)
    test_days : int   (default 5)
    """

    def __init__(
        self,
        features_dir: Path,
        labels_dir: Path,
        models_dir: Path,
        train_days: int = 60,
        test_days: int = 5,
    ):
        raise NotImplementedError("Implemented in validation sprint")

    def run(self, n_folds: Optional[int] = None) -> List[FoldResult]:
        """
        Run walk-forward validation.

        Returns
        -------
        List[FoldResult]
            One entry per fold.
        """
        raise NotImplementedError

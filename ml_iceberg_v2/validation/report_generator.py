"""
report_generator — Renders walk-forward results to Markdown.

Output: docs/validation/iceberg_ml_v2/<report_name>.md

Public API
----------
generate_report(fold_results, output_path, title)
"""

from pathlib import Path
from typing import List

from ml_iceberg_v2.validation.walk_forward import FoldResult


def generate_report(
    fold_results: List[FoldResult],
    output_path: Path,
    title: str = "Walk-Forward Validation Report",
) -> Path:
    """
    Render validation results to a Markdown report.

    Parameters
    ----------
    fold_results : List[FoldResult]
    output_path : Path
        Destination .md file.
    title : str

    Returns
    -------
    Path
        Path to written report.
    """
    raise NotImplementedError("Implemented in validation sprint")

"""
iceberg_inference — End-to-end inference pipeline.

Orchestrates:
  1. DOM convention gate (gate_check)
  2. Feature extraction (extract_features)
  3. Autoencoder anomaly scoring
  4. Classifier prediction
  5. Temperature calibration
  6. AbsorptionState tracking
  7. IcebergOutputV2 emission

Supports both batch (offline, full day) and streaming (per-window) modes.

Public API
----------
IcebergInference(autoencoder_ckpt, classifier_ckpt, temperature)
    .run_day(depth_path, trades_path) → List[IcebergOutputV2]
    .run_window(refill_events, depth_snapshot) → IcebergOutputV2
"""

from pathlib import Path
from typing import List, Optional

from ml_iceberg_v2.output.iceberg_output_v2 import IcebergOutputV2


class IcebergInference:
    """
    Runtime inference pipeline for iceberg detection.

    Parameters
    ----------
    autoencoder_ckpt : Path
        Path to Stage 1 model checkpoint.
    classifier_ckpt : Path
        Path to Stage 2 model checkpoint.
    temperature : float
        Calibration temperature from Stage 3 (default 1.0 = no calibration).
    device : str
        "cpu" or "cuda".
    """

    def __init__(
        self,
        autoencoder_ckpt: Path,
        classifier_ckpt: Path,
        temperature: float = 1.0,
        device: str = "cpu",
    ):
        raise NotImplementedError("Implemented in inference sprint")

    def run_day(
        self,
        depth_path: Path,
        trades_path: Path,
    ) -> List[IcebergOutputV2]:
        """
        Process a full day of L2 data.

        Returns
        -------
        List[IcebergOutputV2]
            One record per detection window (sorted by window_start).
        """
        raise NotImplementedError

    def run_window(
        self,
        refill_events: list,
        depth_snapshot: Optional[object] = None,
    ) -> IcebergOutputV2:
        """
        Process a single 5-second window (streaming mode).
        """
        raise NotImplementedError

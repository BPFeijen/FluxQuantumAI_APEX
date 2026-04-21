"""
volume_predictor — Estimates total iceberg hidden volume from partial chains.

Uses exponential smoothing of observed refill sizes to predict the
remaining volume to be absorbed.

Public API
----------
VolumePredictor.update(refill_size) → estimated_peak_volume
VolumePredictor.reset()
"""


class VolumePredictor:
    """
    Online estimator of peak iceberg volume.

    After each observed refill, updates a weighted running estimate
    of the hidden order size.

    Parameters
    ----------
    alpha : float
        EMA smoothing factor (default 0.3). Higher = faster adaptation.
    """

    def __init__(self, alpha: float = 0.2):  # calibrated 2026-04-10: 0.3→0.2 (half-life 3.1 refills; size_consistency p50=0.63)
        raise NotImplementedError("Implemented in prediction sprint")

    def update(self, refill_size: float) -> float:
        """
        Process one refill and return updated peak volume estimate.

        Returns
        -------
        float
            Estimated remaining hidden volume in lots.
        """
        raise NotImplementedError

    def reset(self) -> None:
        """Reset state for new iceberg chain."""
        raise NotImplementedError

    @property
    def estimate(self) -> float:
        """Current volume estimate."""
        raise NotImplementedError

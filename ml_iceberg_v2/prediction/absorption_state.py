"""
absorption_state — Tracks AbsorptionState transitions for an active iceberg.

State machine: NONE → ACTIVE → FADING → COMPLETE

Transitions:
  NONE    → ACTIVE    : first refill detected
  ACTIVE  → FADING    : refill rate drops below 50% of peak rate
  FADING  → COMPLETE  : no refill for > 10x mean inter-refill gap
  ACTIVE  → COMPLETE  : price level shifts (trade at different price)
  any     → NONE      : explicit reset()

Public API
----------
AbsorptionStateTracker.update(refill_event) → AbsorptionState
AbsorptionStateTracker.tick(current_timestamp_ns) → AbsorptionState
AbsorptionStateTracker.reset()
"""

from ml_iceberg_v2.output.iceberg_output_v2 import AbsorptionState


class AbsorptionStateTracker:
    """
    State machine that emits AbsorptionState transitions.

    Parameters
    ----------
    fade_ratio : float
        Refill rate ratio below which state transitions ACTIVE→FADING (default 0.5).
    complete_gap_multiplier : float
        If current gap > complete_gap_multiplier * mean_gap → COMPLETE (default 10.0).
    """

    def __init__(self, fade_ratio: float = 0.5, complete_gap_multiplier: float = 10.0):
        raise NotImplementedError("Implemented in prediction sprint")

    def update(self, refill_timestamp_ns: int, refill_size: float) -> AbsorptionState:
        """
        Process a new refill event and return updated state.

        Parameters
        ----------
        refill_timestamp_ns : int
            Exchange timestamp of the refill in nanoseconds.
        refill_size : float
            Size of the refill in lots.
        """
        raise NotImplementedError

    def tick(self, current_timestamp_ns: int) -> AbsorptionState:
        """
        Check state based on elapsed time without a new refill.
        Call periodically to detect FADING → COMPLETE transitions.
        """
        raise NotImplementedError

    def reset(self) -> None:
        """Reset to NONE for new detection window."""
        raise NotImplementedError

    @property
    def state(self) -> AbsorptionState:
        raise NotImplementedError

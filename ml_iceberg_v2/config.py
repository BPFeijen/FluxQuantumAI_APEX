"""
Centralized configuration for ml_iceberg_v2.

All constants used across the module are defined here.
Override DATA_* paths via environment variables for Paperspace runs.
"""

import logging
import os
from pathlib import Path

import numpy as np

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market parameters
# ---------------------------------------------------------------------------
INSTRUMENT = "GC"
EXCHANGE = "XCEC"
TICK_SIZE_DEFAULT = 0.10  # Fallback tick size if auto-detection fails (GC USD/oz)
CONTRACT_SIZE = 100        # oz per contract


def detect_tick_size(prices: np.ndarray) -> float:
    """
    Auto-detect the minimum price increment (tick size) from observed prices.

    Uses the mode of positive price differences rather than the minimum, so
    a single anomalous sub-tick diff (noise) does not corrupt the result.

    Parameters
    ----------
    prices : np.ndarray
        Array of trade or quote prices.

    Returns
    -------
    float
        Detected tick size, or TICK_SIZE_DEFAULT if detection fails or the
        result is outside the plausible range [0.001, 100.0].
    """
    if len(prices) < 2:
        _logger.warning(
            "detect_tick_size: insufficient data (%d prices), using default %.4f",
            len(prices), TICK_SIZE_DEFAULT,
        )
        return TICK_SIZE_DEFAULT

    unique_prices = np.unique(prices)
    if len(unique_prices) < 2:
        _logger.warning(
            "detect_tick_size: all prices identical, using default %.4f",
            TICK_SIZE_DEFAULT,
        )
        return TICK_SIZE_DEFAULT

    diffs = np.diff(unique_prices)
    positive_diffs = diffs[diffs > 0]

    if len(positive_diffs) == 0:
        _logger.warning(
            "detect_tick_size: no positive diffs found, using default %.4f",
            TICK_SIZE_DEFAULT,
        )
        return TICK_SIZE_DEFAULT

    # Round to 6 decimal places to collapse floating-point noise, then find mode
    rounded = np.round(positive_diffs, 6)
    values, counts = np.unique(rounded, return_counts=True)
    tick = float(values[np.argmax(counts)])

    if tick < 0.001 or tick > 100.0:
        _logger.warning(
            "detect_tick_size: detected tick %.6f is outside plausible range, "
            "using default %.4f",
            tick, TICK_SIZE_DEFAULT,
        )
        return TICK_SIZE_DEFAULT

    return tick


# TODO(future): add detect_sessions(timestamps) that infers session boundaries
# from hourly volume patterns (peaks in activity = session opens).  This would
# complement detect_tick_size() for fully instrument-agnostic processing.

# ---------------------------------------------------------------------------
# Feature extraction windows
# ---------------------------------------------------------------------------
WINDOW_SIZE_S = 5          # Feature window duration in seconds
STEP_SIZE_S = 1            # Sliding step in seconds

# ---------------------------------------------------------------------------
# Refill detection
# ---------------------------------------------------------------------------
MAX_REFILL_DELAY_MS = 100  # Maximum ms between trade and depth_update to qualify as refill
MIN_CHAIN_LENGTH = 3       # Minimum refills to form an IcebergChain

# Labeling thresholds (aligned with CME paper Table 2)
NATIVE_MAX_DELAY_MS = 10         # Refill delay < 10ms → ICEBERG_NATIVE
SYNTHETIC_MAX_DELAY_MS = 100     # Refill delay 10–100ms → ICEBERG_SYNTHETIC
SIZE_CONSISTENCY_NATIVE = 0.70   # Minimum size consistency (1-CV) for NATIVE
SIZE_CONSISTENCY_SYNTHETIC = 0.50
ABSORPTION_PERSISTENCE_S = 3.0  # Min price persistence to label ABSORPTION
                                 # BUG FIX 2026-04-09: was 5.0 but window is 5s →
                                 # price_persistence_s max≈4.97, condition >5.0
                                 # was never True → 0 ABSORPTION labels
ABSORPTION_VOLUME_THRESHOLD = 10 # Minimum aggressor lots for ABSORPTION label
                                 # BUG FIX 2026-04-09: was 50 but GC p99=22 lots →
                                 # threshold above p99, near-zero samples

# ---------------------------------------------------------------------------
# Chunked processing
# ---------------------------------------------------------------------------
CHUNK_SIZE = 500_000       # Depth update rows per chunk
OVERLAP_BUFFER_S = 30      # Seconds of overlap kept between chunks to avoid broken chains

# ---------------------------------------------------------------------------
# Session boundaries (UTC)
# ASIA  : 22:00 – 07:00  (crosses midnight)
# EUROPE: 07:00 – 13:30
# NY    : 13:30 – 22:00
# ---------------------------------------------------------------------------
SESSIONS = {
    "ASIA":   {"start": "22:00", "end": "07:00"},
    "EUROPE": {"start": "07:00", "end": "13:30"},
    "NY":     {"start": "13:30", "end": "22:00"},
}
SESSION_LABELS = {"ASIA": 0, "EUROPE": 1, "NY": 2}

# ---------------------------------------------------------------------------
# Volatility bucketing (rolling 5-min ATR in ticks)
# ---------------------------------------------------------------------------
VOLATILITY_ATR_WINDOW_S = 300   # 5 minutes
VOLATILITY_LOW_THRESHOLD = 6.0  # ATR ticks — below this → LOW  (calibrated 2026-04-10: GC p22=6.3t → 22% LOW)
VOLATILITY_HIGH_THRESHOLD = 18.0 # ATR ticks — above this → HIGH (calibrated 2026-04-10: GC p75=18.6t → 26% HIGH)
# Between LOW and HIGH → MED  ← ~52% of windows (was 0.0%/0.9% with old 0.5/2.0 thresholds)

VOLATILITY_LABELS = {0: "LOW", 1: "MED", 2: "HIGH"}

# ---------------------------------------------------------------------------
# Data paths  (override with env vars for Paperspace)
# ---------------------------------------------------------------------------
_ROOT = Path(os.environ.get("FLUXQUANTUM_ROOT", Path(__file__).parents[1]))

DATA_L2_DIR = Path(os.environ.get(
    "GC_L2_DIR", _ROOT / "data" / "level2" / "_gc_xcec"
))
FEATURES_OUTPUT_DIR = Path(os.environ.get(
    "FEATURES_DIR", _ROOT / "data" / "features" / "iceberg_v2"
))
LABELS_OUTPUT_DIR = Path(os.environ.get(
    "LABELS_DIR", _ROOT / "data" / "labels" / "iceberg_v2"
))
MODELS_DIR = Path(os.environ.get(
    "MODELS_DIR", _ROOT / "models" / "iceberg_v2"
))
ARTIFACTS_DIR = Path(os.environ.get(
    "ARTIFACTS_DIR", _ROOT / "artifacts" / "iceberg_v2"
))
REPORTS_DIR = Path(os.environ.get(
    "REPORTS_DIR", _ROOT / "reports" / "iceberg_v2"
))

# ---------------------------------------------------------------------------
# Model hyperparameters
# ---------------------------------------------------------------------------
# Stage 1 — Autoencoder (unsupervised anomaly scoring)
STAGE1_LATENT_DIM = 8
STAGE1_EPOCHS = 50
STAGE1_BATCH_SIZE = 512        # calibrated 2026-04-10: 2.95M NOISE rows; batch 512 safe on SageMaker p3.2xl
STAGE1_LEARNING_RATE = 1e-3

# Stage 2 — Classifier (supervised label training)
STAGE2_EPOCHS = 50             # calibrated 2026-04-10: ABSORPTION ~1% of labels; needs more epochs
STAGE2_BATCH_SIZE = 256
STAGE2_LEARNING_RATE = 3e-4    # calibrated 2026-04-10: conservative LR for frozen autoencoder features

# Stage 3 — Calibration (Platt scaling / temperature)
STAGE3_EPOCHS = 15             # calibrated 2026-04-10: temperature scalar converges in <15 epochs
STAGE3_BATCH_SIZE = 512
STAGE3_LEARNING_RATE = 1e-4

# Walk-forward validation
WALK_FORWARD_TRAIN_DAYS = 45   # calibrated 2026-04-10: 45d → 12 folds (vs 9 with 60d); more homogeneous regimes
WALK_FORWARD_TEST_DAYS = 5

# Feature extraction inline constants (moved from hardcoded to config 2026-04-10)
SIDE_HISTORY_WINDOWS = 20      # F20 direction score lookback (was hardcoded 10; 20s better for GC level lifetime p50=6384s)
VELOCITY_TREND_LOOKBACK = 5    # F14 velocity trend lookback windows (confirmed adequate: 96.9% non-zero)

# ---------------------------------------------------------------------------
# Output / signal generation
# ---------------------------------------------------------------------------
MIN_CONFIDENCE_ACTIONABLE = 0.60   # Below this → signal suppressed
MIN_REFILLS_ACTIONABLE = 3

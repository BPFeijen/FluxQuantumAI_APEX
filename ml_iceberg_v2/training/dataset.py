"""
dataset — PyTorch Dataset for iceberg detection training.

Loads feature/label Parquet files, applies z-score normalisation,
and supports class-weighted sampling for imbalanced labels.

Public API
----------
IcebergDataset(features_dir, labels_dir, split)
    → PyTorch Dataset yielding (feature_tensor, label_tensor)

build_weighted_sampler(dataset) → WeightedRandomSampler
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset, Sampler, WeightedRandomSampler

_logger = logging.getLogger(__name__)

# All 26 numeric feature columns in canonical order (must match models.N_FEATURES)
# F01–F20 (original) + F21 ofi_rate + F22 vot + F23 inst_volatility_ticks
FEATURE_COLS: List[str] = [
    # F01–F10
    "refill_count",
    "refill_speed_mean_ms",
    "refill_speed_std_ms",
    "refill_size_ratio_mean",
    "refill_size_consistency",
    "price_persistence_s",
    "aggressor_volume_absorbed",
    "dom_imbalance_at_level",
    "book_depth_recovery_rate",
    "trade_intensity",
    # F11 + F11a + F11b
    "session_label",
    "hour_of_day",
    "minute_of_hour",
    # F12 + F12a
    "volatility_bucket",
    "atr_5min_raw",
    # F13–F16
    "refill_velocity",
    "refill_velocity_trend",
    "cumulative_absorbed_volume",
    "level_lifetime_s",
    # F17–F20
    "time_since_last_trade_at_vanish_ms",
    "price_excursion_beyond_level_ticks",
    "underflow_volume",
    "iceberg_sequence_direction_score",
    # F21–F23 (Payroll / macro release features — 2026-04-11)
    "ofi_rate",               # F21: Order Flow Imbalance rate [lots/s] — Cont et al. 2014
    "vot",                    # F22: Velocity of Tape [monetary mass/s]
    "inst_volatility_ticks",  # F23: within-window price std [ticks] — critical at 100ms
]
assert len(FEATURE_COLS) == 26, "FEATURE_COLS length mismatch"

LABEL_COL = "label"


class IcebergDataset(Dataset):
    """
    Dataset for iceberg detection (stages 1 and 2).

    Reads all Parquet files from features_dir / labels_dir.
    Features are z-score normalised using stats computed on the training split.

    Split is by date: train uses dates up to split_date, val uses the remainder.
    If split_date is not provided, all data is treated as train.

    Parameters
    ----------
    features_dir : Path
        Directory of features_YYYY-MM-DD.parquet files.
    labels_dir : Path
        Directory of labels_YYYY-MM-DD.parquet files.
    split : "train" | "val" | "test"
        If "train": returns all data (normalisation stats computed here).
        If "val"/"test": requires norm_stats to be passed.
    norm_stats : dict, optional
        {"mean": np.ndarray, "std": np.ndarray} — from training dataset.
        Required for val/test splits.
    noise_only : bool
        If True, only yield NOISE-labelled rows (Stage 1 autoencoder training).
    label_smoothing : float
        Not applied here; handled in loss function.
    """

    def __init__(
        self,
        features_dir: Path,
        labels_dir: Path,
        split: Literal["train", "val", "test"] = "train",
        norm_stats: Optional[Dict] = None,
        noise_only: bool = False,
    ):
        features_dir = Path(features_dir)
        labels_dir = Path(labels_dir)

        # Accept both naming conventions:
        #   YYYY-MM-DD.parquet          (existing Databento extraction)
        #   features_YYYY-MM-DD.parquet (new extraction via extract_features.py)
        feat_files = sorted(features_dir.glob("*.parquet"))
        label_files = sorted(labels_dir.glob("*.parquet"))

        if not feat_files:
            raise FileNotFoundError(f"No *.parquet found in {features_dir}")
        if not label_files:
            raise FileNotFoundError(f"No *.parquet found in {labels_dir}")

        # Match by date suffix — strip optional 'features_' / 'labels_' prefix
        def _date_key(f: Path) -> str:
            stem = f.stem
            for prefix in ("features_", "labels_"):
                if stem.startswith(prefix):
                    return stem[len(prefix):]
            return stem

        feat_dates = {_date_key(f): f for f in feat_files}
        label_dates = {_date_key(f): f for f in label_files}
        common_dates = sorted(set(feat_dates) & set(label_dates))
        if not common_dates:
            raise ValueError("No matching (features, labels) date pairs found")

        _logger.info("IcebergDataset: %d matching date pairs (split=%s)", len(common_dates), split)

        # Walk-forward split: 80% train, 20% val (by date order)
        n_total = len(common_dates)
        n_train = max(1, int(n_total * 0.8))

        if split == "train":
            selected = common_dates[:n_train]
        else:  # val or test
            selected = common_dates[n_train:]

        _logger.info("IcebergDataset: %s uses %d dates (%s -> %s)",
                     split, len(selected), selected[0] if selected else "?",
                     selected[-1] if selected else "?")

        # Load all parquets
        feat_dfs = [pd.read_parquet(feat_dates[d]) for d in selected]
        label_dfs = [pd.read_parquet(label_dates[d]) for d in selected]

        features_df = pd.concat(feat_dfs, ignore_index=True)
        labels_df = pd.concat(label_dfs, ignore_index=True)

        # Align on window_start
        merged = features_df.merge(
            labels_df[["window_start", "label", "label_confidence", "chain_weight"]],
            on="window_start",
            how="inner",
        )
        if len(merged) == 0:
            raise ValueError("Feature/label DataFrames have no matching window_start values")

        _logger.info("IcebergDataset: %d rows after merge (split=%s)", len(merged), split)

        if noise_only:
            merged = merged[merged["label"] == 0].reset_index(drop=True)
            _logger.info("IcebergDataset: %d NOISE rows (noise_only=True)", len(merged))

        # Extract feature matrix
        for col in FEATURE_COLS:
            if col not in merged.columns:
                _logger.warning("Feature column '%s' missing — filling with 0", col)
                merged[col] = 0.0

        X = merged[FEATURE_COLS].values.astype(np.float32)

        # Replace -1 sentinels in F17 with 0 before normalising
        # F17 = time_since_last_trade_at_vanish_ms: -1.0 = no vanish event
        f17_idx = FEATURE_COLS.index("time_since_last_trade_at_vanish_ms")
        X[:, f17_idx] = np.where(X[:, f17_idx] < 0, 0.0, X[:, f17_idx])

        # Z-score normalisation
        if split == "train" and norm_stats is None:
            mean = X.mean(axis=0)
            std = X.std(axis=0)
            std = np.where(std == 0, 1.0, std)
            self.norm_stats = {"mean": mean, "std": std}
        elif norm_stats is not None:
            mean = norm_stats["mean"].astype(np.float32)
            std = norm_stats["std"].astype(np.float32)
            self.norm_stats = norm_stats
        else:
            # No stats provided for val/test — compute from this split (fallback)
            mean = X.mean(axis=0)
            std = X.std(axis=0)
            std = np.where(std == 0, 1.0, std)
            self.norm_stats = {"mean": mean, "std": std}
            _logger.warning("norm_stats not provided for split=%s — computed locally", split)

        X = (X - mean) / std
        np.nan_to_num(X, copy=False, nan=0.0, posinf=5.0, neginf=-5.0)
        np.clip(X, -5.0, 5.0, out=X)  # clip finite outliers (e.g. F21-F23 in val)

        self.X = torch.from_numpy(X)
        self.y = torch.from_numpy(merged["label"].values.astype(np.int64))
        self.weights = torch.from_numpy(
            merged["chain_weight"].fillna(1.0).values.astype(np.float32)
        )
        self.split = split

    def __len__(self) -> int:
        return len(self.X)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.X[idx], self.y[idx]

    def save_norm_stats(self, path: Path) -> None:
        """Save normalisation stats to JSON for reproducibility."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        stats = {
            "mean": self.norm_stats["mean"].tolist(),
            "std": self.norm_stats["std"].tolist(),
            "feature_cols": FEATURE_COLS,
        }
        with open(path, "w") as f:
            json.dump(stats, f, indent=2)
        _logger.info("Saved norm_stats -> %s", path)

    @staticmethod
    def load_norm_stats(path: Path) -> Dict:
        """Load normalisation stats saved by save_norm_stats()."""
        with open(path) as f:
            d = json.load(f)
        return {
            "mean": np.array(d["mean"], dtype=np.float32),
            "std": np.array(d["std"], dtype=np.float32),
        }


class _NumpyWeightedSampler(Sampler):
    """
    numpy.random.choice-based weighted sampler.

    Drop-in replacement for WeightedRandomSampler that avoids
    torch.multinomial's 2^24 category limit, which triggers on large
    Stage-2 datasets where noise_only=False includes all labels.
    """

    def __init__(self, weights: np.ndarray, num_samples: int, replacement: bool = True):
        w = np.asarray(weights, dtype=np.float64)
        total = w.sum()
        self._probs = w / total if total > 0 else np.ones(len(w), dtype=np.float64) / max(len(w), 1)
        self._num_samples = num_samples
        self._replacement = replacement

    def __len__(self) -> int:
        return self._num_samples

    def __iter__(self):
        indices = np.random.choice(
            len(self._probs),
            size=self._num_samples,
            replace=self._replacement,
            p=self._probs,
        )
        return iter(indices.tolist())


def build_weighted_sampler(dataset: IcebergDataset) -> _NumpyWeightedSampler:
    """
    Build a weighted sampler to balance class frequencies.

    Combines chain_weight (KM weighting) with inverse class frequency,
    so rare classes (NATIVE, ABSORPTION) are over-sampled.

    Uses numpy.random.choice instead of torch.multinomial to avoid the
    2^24 category limit that fires on large Stage-2 datasets.

    Returns
    -------
    _NumpyWeightedSampler
    """
    labels = dataset.y.numpy()
    class_counts = np.bincount(labels, minlength=4)
    class_counts = np.where(class_counts == 0, 1, class_counts)  # avoid div/0

    # Inverse frequency weight per class
    class_weights = 1.0 / class_counts

    # Per-sample weight = class_weight × chain_weight
    sample_weights = class_weights[labels] * dataset.weights.numpy()

    return _NumpyWeightedSampler(
        weights=sample_weights,
        num_samples=len(dataset),
        replacement=True,
    )

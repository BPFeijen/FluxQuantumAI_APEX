"""
models.py — PyTorch model definitions for ml_iceberg_v2.

Stage 1: IcebergAutoencoder (unsupervised anomaly scoring on NOISE windows)
Stage 2: IcebergClassifier (supervised: NOISE / NATIVE / SYNTHETIC / ABSORPTION)
Stage 3: TemperatureScaler (Platt-style calibration)

Architecture summary (tech spec §5):
  Autoencoder input:  N_FEATURES = 26 (F01–F23 + ofi_rate + vot + inst_volatility_ticks)
  Latent dim:         8  (STAGE1_LATENT_DIM)
  Classifier input:   16 raw + 8 latent + 1 recon_error = 25
  Output:             4 classes (0=NOISE, 1=NATIVE, 2=SYNTHETIC, 3=ABSORPTION)
"""

from __future__ import annotations

import torch
import torch.nn as nn

# All 26 numeric feature columns (order must match IcebergDataset.FEATURE_COLS)
# F01–F20 (original) + F21 ofi_rate + F22 vot + F23 inst_volatility_ticks
N_FEATURES = 26

# 16 "raw" features passed directly to Stage 2 (subset of N_FEATURES)
STAGE2_RAW_COLS = [
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
    "atr_5min_raw",
    "refill_velocity",
    "refill_velocity_trend",
    "underflow_volume",
    "iceberg_sequence_direction_score",
    "price_excursion_beyond_level_ticks",
]
N_RAW = len(STAGE2_RAW_COLS)   # 16
N_LATENT = 8                    # STAGE1_LATENT_DIM
N_CLASSES = 4


class IcebergAutoencoder(nn.Module):
    """
    Symmetric autoencoder for unsupervised iceberg pattern learning.

    Trained on NOISE-labelled windows only. High reconstruction error on a
    new window → anomalous (potentially iceberg) pattern.

    Architecture:
        Encoder: 26 → 64 → 32 → 8 (latent)
        Decoder: 8  → 32 → 64 → 26 (reconstruction)
    """

    def __init__(self, n_features: int = N_FEATURES, latent_dim: int = N_LATENT):
        super().__init__()
        self.n_features = n_features
        self.latent_dim = latent_dim

        self.encoder = nn.Sequential(
            nn.Linear(n_features, 64),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(64, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Linear(32, latent_dim),
        )

        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(32, 64),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.Linear(64, n_features),
        )

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        return self.encoder(x)

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        return self.decoder(z)

    def forward(self, x: torch.Tensor):
        """Returns (reconstruction, latent_z)."""
        z = self.encode(x)
        x_hat = self.decode(z)
        return x_hat, z

    def reconstruction_error(self, x: torch.Tensor) -> torch.Tensor:
        """Per-sample MSE reconstruction error (scalar per row)."""
        x_hat, _ = self.forward(x)
        return ((x - x_hat) ** 2).mean(dim=1)


class IcebergClassifier(nn.Module):
    """
    Supervised classifier for iceberg class prediction (Stage 2).

    Input: 16 raw features + 8 autoencoder latent + 1 recon_error = 25 dims.
    Autoencoder weights are frozen during Stage 2 training.

    Architecture:
        FC: 25 → 64 → 32 → 4 (softmax)
    """

    def __init__(
        self,
        n_raw: int = N_RAW,
        latent_dim: int = N_LATENT,
        n_classes: int = N_CLASSES,
    ):
        super().__init__()
        in_dim = n_raw + latent_dim + 1  # +1 for recon_error

        self.net = nn.Sequential(
            nn.Linear(in_dim, 64),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(32, n_classes),
        )

    def forward(self, x_combined: torch.Tensor) -> torch.Tensor:
        """
        Parameters
        ----------
        x_combined : (batch, n_raw + latent_dim + 1)
            Pre-concatenated [raw_features | latent | recon_error].

        Returns
        -------
        logits : (batch, n_classes)
        """
        return self.net(x_combined)


class TemperatureScaler(nn.Module):
    """
    Single-parameter temperature scaling for post-hoc probability calibration.

    Learns T such that calibrated_logits = logits / T.
    T > 1 → softer (less confident) predictions.
    """

    def __init__(self):
        super().__init__()
        self.temperature = nn.Parameter(torch.ones(1))

    def forward(self, logits: torch.Tensor) -> torch.Tensor:
        return logits / self.temperature.clamp(min=1e-6)

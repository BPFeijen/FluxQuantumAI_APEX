"""
trainer_stage1 — Autoencoder training loop (Stage 1: unsupervised).

Trains IcebergAutoencoder on NOISE-labelled windows only.
Saves best checkpoint (lowest val reconstruction error) to MODELS_DIR.

Public API
----------
train_stage1(config_overrides) → training_history dict
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from ml_iceberg_v2.config import (
    STAGE1_BATCH_SIZE,
    STAGE1_EPOCHS,
    STAGE1_LEARNING_RATE,
    STAGE1_LATENT_DIM,
    MODELS_DIR,
    FEATURES_OUTPUT_DIR,
    LABELS_OUTPUT_DIR,
)
from ml_iceberg_v2.training.dataset import IcebergDataset, build_weighted_sampler
from ml_iceberg_v2.training.models import IcebergAutoencoder

_logger = logging.getLogger(__name__)


def train_stage1(
    features_dir: Optional[str] = None,
    labels_dir: Optional[str] = None,
    models_dir: Optional[str] = None,
    epochs: Optional[int] = None,
    batch_size: Optional[int] = None,
    learning_rate: Optional[float] = None,
    device: str = "cpu",
) -> Dict:
    """
    Train Stage 1 autoencoder.

    All path/hyperparameter arguments default to values from config.py.

    Returns
    -------
    dict
        {"train_loss": [...], "val_loss": [...], "best_epoch": int,
         "checkpoint_path": str}
    """
    features_dir = Path(features_dir or FEATURES_OUTPUT_DIR)
    labels_dir = Path(labels_dir or LABELS_OUTPUT_DIR)
    models_dir = Path(models_dir or MODELS_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)

    epochs = epochs or STAGE1_EPOCHS
    batch_size = batch_size or STAGE1_BATCH_SIZE
    lr = learning_rate or STAGE1_LEARNING_RATE

    _logger.info("Stage 1 — device=%s  epochs=%d  batch=%d  lr=%g", device, epochs, batch_size, lr)

    # Datasets (noise_only=True: autoencoder trains on background windows)
    train_ds = IcebergDataset(features_dir, labels_dir, split="train", noise_only=True)
    val_ds = IcebergDataset(
        features_dir, labels_dir, split="val",
        norm_stats=train_ds.norm_stats, noise_only=True,
    )

    # Save normalisation stats for downstream use
    train_ds.save_norm_stats(models_dir / "norm_stats.json")

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                              num_workers=0, pin_memory=(device != "cpu"))
    val_loader = DataLoader(val_ds, batch_size=batch_size * 2, shuffle=False,
                            num_workers=0, pin_memory=(device != "cpu"))

    dev = torch.device(device)
    model = IcebergAutoencoder(n_features=26, latent_dim=STAGE1_LATENT_DIM).to(dev)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5,
    )

    history = {"train_loss": [], "val_loss": [], "best_epoch": 0}
    best_val = float("inf")
    checkpoint_path = str(models_dir / "stage1_autoencoder.pt")

    for epoch in range(1, epochs + 1):
        # --- Train ---
        model.train()
        train_losses = []
        for batch in train_loader:
            X = batch[0]
            X = X.to(dev)
            optimizer.zero_grad()
            X_hat, _ = model(X)
            loss = criterion(X_hat, X)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            optimizer.step()
            train_losses.append(loss.item())

        train_loss = sum(train_losses) / max(len(train_losses), 1)

        # --- Val ---
        model.eval()
        val_losses = []
        with torch.no_grad():
            for batch in val_loader:
                X = batch[0].to(dev)
                X_hat, _ = model(X)
                val_losses.append(criterion(X_hat, X).item())
        val_loss = sum(val_losses) / max(len(val_losses), 1)

        scheduler.step(val_loss)
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)

        _logger.info("Stage1 epoch %d/%d — train=%.6f  val=%.6f", epoch, epochs, train_loss, val_loss)

        if val_loss < best_val:
            best_val = val_loss
            history["best_epoch"] = epoch
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_loss": val_loss,
                "norm_stats": train_ds.norm_stats,
            }, checkpoint_path)
            _logger.info("  -> new best (%.6f) - saved %s", best_val, checkpoint_path)

    history["checkpoint_path"] = checkpoint_path
    _logger.info("Stage 1 complete. Best val=%.6f at epoch %d", best_val, history["best_epoch"])
    return history

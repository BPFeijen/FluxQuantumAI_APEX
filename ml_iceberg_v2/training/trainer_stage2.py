"""
trainer_stage2 — Classifier training loop (Stage 2: supervised).

Trains IcebergClassifier on labelled windows.
Input features: 16 raw + 8 autoencoder latent + 1 reconstruction error = 25 dims.
Saves best checkpoint (highest macro F1 on val) to MODELS_DIR.

Public API
----------
train_stage2(autoencoder_checkpoint, config_overrides) → training_history dict
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from ml_iceberg_v2.config import (
    STAGE2_BATCH_SIZE,
    STAGE2_EPOCHS,
    STAGE2_LEARNING_RATE,
    STAGE1_LATENT_DIM,
    MODELS_DIR,
    FEATURES_OUTPUT_DIR,
    LABELS_OUTPUT_DIR,
)
from ml_iceberg_v2.training.dataset import IcebergDataset, FEATURE_COLS, build_weighted_sampler
from ml_iceberg_v2.training.models import (
    IcebergAutoencoder,
    IcebergClassifier,
    STAGE2_RAW_COLS,
    N_RAW,
    N_LATENT,
    N_CLASSES,
)

_logger = logging.getLogger(__name__)

# Indices of the 16 "raw" features within FEATURE_COLS
_RAW_INDICES = [FEATURE_COLS.index(c) for c in STAGE2_RAW_COLS]


def _build_stage2_input(
    X: torch.Tensor,
    autoencoder: IcebergAutoencoder,
    device: torch.device,
) -> torch.Tensor:
    """
    Build the 25-dim classifier input from 26-dim normalised features:
      [16 raw features | 8 latent | 1 recon_error]

    Autoencoder must be on `device` and in eval mode.
    """
    with torch.no_grad():
        X_dev = X.to(device)
        X_hat, z = autoencoder(X_dev)
        recon_err = ((X_dev - X_hat) ** 2).mean(dim=1, keepdim=True)

    raw = X_dev[:, _RAW_INDICES]          # (B, 16)
    return torch.cat([raw, z, recon_err], dim=1)  # (B, 25)


def _macro_f1(
    preds: np.ndarray,
    targets: np.ndarray,
    n_classes: int = N_CLASSES,
) -> float:
    """Compute macro-averaged F1 over all classes."""
    f1s = []
    for c in range(n_classes):
        tp = ((preds == c) & (targets == c)).sum()
        fp = ((preds == c) & (targets != c)).sum()
        fn = ((preds != c) & (targets == c)).sum()
        prec = tp / (tp + fp + 1e-9)
        rec = tp / (tp + fn + 1e-9)
        f1s.append(2 * prec * rec / (prec + rec + 1e-9))
    return float(np.mean(f1s))


def train_stage2(
    autoencoder_checkpoint: Optional[str] = None,
    features_dir: Optional[str] = None,
    labels_dir: Optional[str] = None,
    models_dir: Optional[str] = None,
    epochs: Optional[int] = None,
    batch_size: Optional[int] = None,
    learning_rate: Optional[float] = None,
    device: str = "cpu",
) -> Dict:
    """
    Train Stage 2 classifier.

    Loads frozen autoencoder to compute latent + reconstruction features.
    All path/hyperparameter arguments default to values from config.py.

    Returns
    -------
    dict
        {"train_loss": [...], "val_loss": [...], "val_f1_macro": [...],
         "best_epoch": int, "checkpoint_path": str}
    """
    features_dir = Path(features_dir or FEATURES_OUTPUT_DIR)
    labels_dir = Path(labels_dir or LABELS_OUTPUT_DIR)
    models_dir = Path(models_dir or MODELS_DIR)
    models_dir.mkdir(parents=True, exist_ok=True)

    epochs = epochs or STAGE2_EPOCHS
    batch_size = batch_size or STAGE2_BATCH_SIZE
    lr = learning_rate or STAGE2_LEARNING_RATE

    # Load norm_stats from Stage 1
    norm_stats_path = models_dir / "norm_stats.json"
    norm_stats = IcebergDataset.load_norm_stats(norm_stats_path)

    _logger.info("Stage 2 — device=%s  epochs=%d  batch=%d  lr=%g", device, epochs, batch_size, lr)

    train_ds = IcebergDataset(features_dir, labels_dir, split="train", norm_stats=norm_stats)
    val_ds = IcebergDataset(features_dir, labels_dir, split="val", norm_stats=norm_stats)

    sampler = build_weighted_sampler(train_ds)
    train_loader = DataLoader(train_ds, batch_size=batch_size, sampler=sampler,
                              num_workers=0, pin_memory=(device != "cpu"))
    val_loader = DataLoader(val_ds, batch_size=batch_size * 2, shuffle=False,
                            num_workers=0, pin_memory=(device != "cpu"))

    dev = torch.device(device)

    # Load frozen autoencoder
    autoencoder_path = autoencoder_checkpoint or str(models_dir / "stage1_autoencoder.pt")
    ckpt = torch.load(autoencoder_path, map_location=dev, weights_only=False)
    autoencoder = IcebergAutoencoder(n_features=26, latent_dim=STAGE1_LATENT_DIM).to(dev)
    autoencoder.load_state_dict(ckpt["model_state_dict"])
    autoencoder.eval()
    for p in autoencoder.parameters():
        p.requires_grad = False
    _logger.info("Loaded frozen autoencoder from %s", autoencoder_path)

    # Compute class counts for weighted cross-entropy
    all_labels = train_ds.y.numpy()
    class_counts = np.bincount(all_labels, minlength=N_CLASSES).astype(np.float32)
    class_counts = np.where(class_counts == 0, 1, class_counts)
    class_weights = torch.from_numpy(1.0 / class_counts).to(dev)
    _logger.info("Class counts: %s  weights: %s", class_counts.astype(int), class_weights.cpu().numpy().round(4))

    classifier = IcebergClassifier(n_raw=N_RAW, latent_dim=N_LATENT, n_classes=N_CLASSES).to(dev)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.Adam(classifier.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.5, patience=5,
    )

    history: Dict = {"train_loss": [], "val_loss": [], "val_f1_macro": [], "best_epoch": 0}
    best_f1 = -1.0
    checkpoint_path = str(models_dir / "stage2_classifier.pt")

    for epoch in range(1, epochs + 1):
        # --- Train ---
        classifier.train()
        train_losses = []
        for batch in train_loader:
            X, y = batch[0], batch[1]
            y = y.to(dev)
            x_in = _build_stage2_input(X, autoencoder, dev)
            optimizer.zero_grad()
            logits = classifier(x_in)
            loss = criterion(logits, y)
            loss.backward()
            nn.utils.clip_grad_norm_(classifier.parameters(), max_norm=5.0)
            optimizer.step()
            train_losses.append(loss.item())

        train_loss = sum(train_losses) / max(len(train_losses), 1)

        # --- Val ---
        classifier.eval()
        val_losses, all_preds, all_targets = [], [], []
        with torch.no_grad():
            for batch in val_loader:
                X, y = batch[0], batch[1]
                y = y.to(dev)
                x_in = _build_stage2_input(X, autoencoder, dev)
                logits = classifier(x_in)
                val_losses.append(criterion(logits, y).item())
                all_preds.extend(logits.argmax(dim=1).cpu().numpy())
                all_targets.extend(y.cpu().numpy())

        val_loss = sum(val_losses) / max(len(val_losses), 1)
        val_f1 = _macro_f1(np.array(all_preds), np.array(all_targets))

        scheduler.step(val_f1)
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_f1_macro"].append(val_f1)

        _logger.info(
            "Stage2 epoch %d/%d — train_loss=%.4f  val_loss=%.4f  val_f1=%.4f",
            epoch, epochs, train_loss, val_loss, val_f1,
        )

        if val_f1 > best_f1:
            best_f1 = val_f1
            history["best_epoch"] = epoch
            torch.save({
                "epoch": epoch,
                "model_state_dict": classifier.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_f1_macro": val_f1,
            }, checkpoint_path)
            _logger.info("  -> new best F1=%.4f - saved %s", best_f1, checkpoint_path)

    history["checkpoint_path"] = checkpoint_path
    _logger.info("Stage 2 complete. Best macro F1=%.4f at epoch %d", best_f1, history["best_epoch"])
    return history

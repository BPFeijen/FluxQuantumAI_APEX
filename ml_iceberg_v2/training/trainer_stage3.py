"""
trainer_stage3 — Platt scaling calibration (Stage 3).

Fits temperature scaling on held-out calibration set to produce
well-calibrated confidence scores.

Public API
----------
calibrate_stage3(classifier_checkpoint, config_overrides) → calibration_stats dict
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
    STAGE3_EPOCHS,
    STAGE3_BATCH_SIZE,
    STAGE3_LEARNING_RATE,
    STAGE1_LATENT_DIM,
    MODELS_DIR,
    FEATURES_OUTPUT_DIR,
    LABELS_OUTPUT_DIR,
)
from ml_iceberg_v2.training.dataset import IcebergDataset, FEATURE_COLS
from ml_iceberg_v2.training.models import (
    IcebergAutoencoder,
    IcebergClassifier,
    STAGE2_RAW_COLS,
    TemperatureScaler,
    N_RAW,
    N_LATENT,
    N_CLASSES,
)

_logger = logging.getLogger(__name__)

_RAW_INDICES = [FEATURE_COLS.index(c) for c in STAGE2_RAW_COLS]


def _expected_calibration_error(
    probs: np.ndarray,
    labels: np.ndarray,
    n_bins: int = 15,
) -> float:
    """Compute ECE (Expected Calibration Error) over n_bins confidence bins."""
    confidences = probs.max(axis=1)
    predictions = probs.argmax(axis=1)
    correct = (predictions == labels).astype(float)

    ece = 0.0
    bin_edges = np.linspace(0, 1, n_bins + 1)
    for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
        mask = (confidences >= lo) & (confidences < hi)
        if mask.sum() == 0:
            continue
        acc = correct[mask].mean()
        conf = confidences[mask].mean()
        ece += mask.mean() * abs(acc - conf)
    return float(ece)


def calibrate_stage3(
    classifier_checkpoint: Optional[str] = None,
    features_dir: Optional[str] = None,
    labels_dir: Optional[str] = None,
    models_dir: Optional[str] = None,
    device: str = "cpu",
) -> Dict:
    """
    Fit temperature scaling on calibration set (val split).

    Optimises NLL over a single scalar temperature T applied to
    classifier logits before softmax.

    Returns
    -------
    dict
        {"temperature": float, "ece_before": float, "ece_after": float,
         "checkpoint_path": str}
    """
    features_dir = Path(features_dir or FEATURES_OUTPUT_DIR)
    labels_dir = Path(labels_dir or LABELS_OUTPUT_DIR)
    models_dir = Path(models_dir or MODELS_DIR)

    dev = torch.device(device)

    # Load norm_stats
    norm_stats = IcebergDataset.load_norm_stats(models_dir / "norm_stats.json")

    # Val dataset as calibration set
    cal_ds = IcebergDataset(features_dir, labels_dir, split="val", norm_stats=norm_stats)
    cal_loader = DataLoader(cal_ds, batch_size=STAGE3_BATCH_SIZE * 2, shuffle=False, num_workers=0)

    # Load frozen autoencoder
    ae_path = models_dir / "stage1_autoencoder.pt"
    ae_ckpt = torch.load(ae_path, map_location=dev, weights_only=False)
    autoencoder = IcebergAutoencoder(n_features=26, latent_dim=STAGE1_LATENT_DIM).to(dev)
    autoencoder.load_state_dict(ae_ckpt["model_state_dict"])
    autoencoder.eval()
    for p in autoencoder.parameters():
        p.requires_grad = False

    # Load frozen classifier
    cls_path = classifier_checkpoint or str(models_dir / "stage2_classifier.pt")
    cls_ckpt = torch.load(cls_path, map_location=dev, weights_only=False)
    classifier = IcebergClassifier(n_raw=N_RAW, latent_dim=N_LATENT, n_classes=N_CLASSES).to(dev)
    classifier.load_state_dict(cls_ckpt["model_state_dict"])
    classifier.eval()
    for p in classifier.parameters():
        p.requires_grad = False

    # Collect all logits + labels on calibration set
    all_logits, all_labels = [], []
    with torch.no_grad():
        for batch in cal_loader:
            X, y = batch[0], batch[1]
            X_dev = X.to(dev)
            X_hat, z = autoencoder(X_dev)
            recon_err = ((X_dev - X_hat) ** 2).mean(dim=1, keepdim=True)
            raw = X_dev[:, _RAW_INDICES]
            x_in = torch.cat([raw, z, recon_err], dim=1)
            logits = classifier(x_in)
            all_logits.append(logits.cpu())
            all_labels.append(y.cpu())

    logits_tensor = torch.cat(all_logits)
    labels_tensor = torch.cat(all_labels)

    # ECE before calibration
    probs_before = torch.softmax(logits_tensor, dim=1).numpy()
    ece_before = _expected_calibration_error(probs_before, labels_tensor.numpy())
    _logger.info("ECE before calibration: %.4f", ece_before)

    # Fit temperature scaler
    scaler = TemperatureScaler().to(dev)
    logits_dev = logits_tensor.to(dev)
    labels_dev = labels_tensor.to(dev)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.LBFGS(
        scaler.parameters(), lr=STAGE3_LEARNING_RATE, max_iter=STAGE3_EPOCHS
    )

    def eval_closure():
        optimizer.zero_grad()
        scaled = scaler(logits_dev)
        loss = criterion(scaled, labels_dev)
        loss.backward()
        return loss

    optimizer.step(eval_closure)

    temperature = float(scaler.temperature.item())
    _logger.info("Learned temperature T=%.4f", temperature)

    # ECE after calibration
    with torch.no_grad():
        scaled_logits = scaler(logits_dev).cpu()
    probs_after = torch.softmax(scaled_logits, dim=1).numpy()
    ece_after = _expected_calibration_error(probs_after, labels_tensor.numpy())
    _logger.info("ECE after calibration: %.4f (was %.4f)", ece_after, ece_before)

    # Save calibrated model (temperature + classifier + autoencoder as bundle)
    checkpoint_path = str(models_dir / "stage3_calibrated.pt")
    torch.save({
        "temperature": temperature,
        "scaler_state_dict": scaler.state_dict(),
        "ece_before": ece_before,
        "ece_after": ece_after,
    }, checkpoint_path)
    _logger.info("Saved calibrated scaler → %s", checkpoint_path)

    return {
        "temperature": temperature,
        "ece_before": ece_before,
        "ece_after": ece_after,
        "checkpoint_path": checkpoint_path,
    }

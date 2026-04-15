#!/usr/bin/env python3
"""
train_grenadier.py — Grenadier Sprint 2 (The Brain) — Autoencoder Training

Two-phase training protocol:

  Phase 1 — Learning Normal
    Train GrenadierAutoencoder on train_normal.parquet (quiet sessions).
    Objective: minimise MSE reconstruction error on normal market microstructure.
    Output: grenadier_autoencoder.pt (weights after convergence)

  Phase 2 — Calibration
    Freeze the model (no backprop). Run inference on BOTH datasets.
    Compute MSE distribution for Normal vs Chaos.
    Store calibration buffers in the model (normal_mse_mean, normal_mse_std,
    anomaly_threshold_p95/p99).
    Output: updated grenadier_autoencoder.pt + grenadier_calibration.json

DoD validation (from spec):
    "Os logs do treinamento imprimirem a distribuição do MSE no dataset Normal vs
     Dataset Caos, provando matematicamente que o erro de reconstrução dispara
     durante eventos de notícia."

Usage:
    python train_grenadier.py
    python train_grenadier.py --epochs 200 --batch-size 512 --lr 5e-4
    python train_grenadier.py --phase2-only   # skip Phase 1, just calibrate

Spec ref: FluxQuantumAI_Anomalies_Detection_09042026.docx §3 / Sprint 2 Spec
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT          = Path(__file__).parent               # C:/FluxQuantumAI
GRENADIER_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")
OUT_DIR       = ROOT / "data" / "grenadier"

TRAIN_PARQUET = OUT_DIR / "train_normal.parquet"
CHAOS_PARQUET = OUT_DIR / "val_chaos.parquet"
MODEL_OUT     = GRENADIER_DIR / "models" / "grenadier_autoencoder.pt"
CALIB_OUT     = GRENADIER_DIR / "models" / "grenadier_calibration.json"

# Make APEX_GC_Anomaly importable
if str(GRENADIER_DIR) not in sys.path:
    sys.path.insert(0, str(GRENADIER_DIR))

from models.autoencoder import GrenadierAutoencoder  # noqa: E402

# ---------------------------------------------------------------------------
# Feature spec (must match prep_grenadier.py)
# ---------------------------------------------------------------------------
FEAT_COLS = [
    "bid_0", "bid_1", "bid_2", "bid_3", "bid_4",
    "ask_0", "ask_1", "ask_2", "ask_3", "ask_4",
    "imbalance",
]
INPUT_DIM = len(FEAT_COLS)  # 11

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("grenadier.train")


# ===========================================================================
# Data loading
# ===========================================================================

def _load_tensor(path: Path, label: str) -> torch.Tensor:
    """Load parquet → float32 tensor of shape [N, 11]."""
    if not path.exists():
        raise FileNotFoundError(
            f"{label} not found at {path}\n"
            "Run:  python data/prep_grenadier.py  first."
        )
    df = pd.read_parquet(path, columns=FEAT_COLS)
    arr = df.values.astype(np.float32)
    t = torch.from_numpy(arr)
    log.info("  Loaded %-14s — %s rows  shape=%s", label, f"{len(df):,}", tuple(t.shape))
    return t


# ===========================================================================
# Phase 1 — Training
# ===========================================================================

def train(
    model      : GrenadierAutoencoder,
    train_data : torch.Tensor,
    epochs     : int   = 100,
    batch_size : int   = 512,
    lr         : float = 1e-3,
    patience   : int   = 10,
) -> list[float]:
    """
    Train autoencoder on normal data. Returns list of per-epoch losses.
    Uses early stopping (patience = number of epochs without improvement).
    """
    device    = next(model.parameters()).device
    dataset   = TensorDataset(train_data)
    loader    = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=False)
    criterion = nn.MSELoss()
    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimiser, mode="min", factor=0.5, patience=5
    )

    losses: list[float] = []
    best_loss = float("inf")
    best_state: dict | None = None
    no_improve = 0

    log.info("=" * 60)
    log.info("PHASE 1 — Training on normal data")
    log.info("  epochs=%d  batch=%d  lr=%.1e  device=%s", epochs, batch_size, lr, device)
    log.info("  train rows=%s  batches/epoch=%d",
             f"{len(train_data):,}", len(loader))

    t0 = time.monotonic()
    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss = 0.0
        n_batches  = 0

        for (batch,) in loader:
            batch = batch.to(device)
            optimiser.zero_grad()
            recon, _ = model(batch)
            loss = criterion(recon, batch)
            loss.backward()
            optimiser.step()
            epoch_loss += loss.item()
            n_batches  += 1

        avg_loss = epoch_loss / n_batches
        losses.append(avg_loss)
        scheduler.step(avg_loss)

        if epoch % 10 == 0 or epoch <= 5:
            elapsed = time.monotonic() - t0
            lr_now  = optimiser.param_groups[0]["lr"]
            log.info("  [epoch %4d / %d]  loss=%.6f  lr=%.2e  elapsed=%.1fs",
                     epoch, epochs, avg_loss, lr_now, elapsed)

        # Early stopping
        if avg_loss < best_loss - 1e-7:
            best_loss  = avg_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                log.info("  Early stopping at epoch %d (no improvement for %d epochs)",
                         epoch, patience)
                break

    # Restore best weights
    if best_state is not None:
        model.load_state_dict(best_state)

    log.info("  Phase 1 complete — best loss=%.6f", best_loss)
    return losses


# ===========================================================================
# Phase 2 — Calibration (frozen model, no backprop)
# ===========================================================================

def calibrate(
    model        : GrenadierAutoencoder,
    normal_data  : torch.Tensor,
    chaos_data   : torch.Tensor,
    batch_size   : int = 2048,
) -> dict:
    """
    Run frozen inference on both datasets.
    Print MSE distributions and store calibration buffers in model.
    Returns calibration stats dict (for grenadier_calibration.json).
    """
    device = next(model.parameters()).device
    model.eval()

    log.info("=" * 60)
    log.info("PHASE 2 — Calibration (frozen model, inference only)")

    def _compute_errors(data: torch.Tensor, label: str) -> torch.Tensor:
        errors = []
        loader = DataLoader(TensorDataset(data), batch_size=batch_size, shuffle=False)
        with torch.no_grad():
            for (batch,) in loader:
                batch = batch.to(device)
                e = model.reconstruction_error(batch)
                errors.append(e.cpu())
        errs = torch.cat(errors)
        log.info("  %-14s  n=%s  MSE: mean=%.6f  std=%.6f  "
                 "p50=%.6f  p95=%.6f  p99=%.6f",
                 label, f"{len(errs):,}",
                 float(errs.mean()), float(errs.std()),
                 float(torch.quantile(errs, 0.50)),
                 float(torch.quantile(errs, 0.95)),
                 float(torch.quantile(errs, 0.99)),
                 )
        return errs

    normal_errors = _compute_errors(normal_data, "NORMAL")

    if len(chaos_data) > 0:
        chaos_errors = _compute_errors(chaos_data, "CHAOS")
    else:
        log.warning("  val_chaos is empty — skipping chaos calibration")
        chaos_errors = None

    # Store in model buffers + get stats dict
    stats = model.calibrate_buffers(normal_errors, chaos_errors)

    log.info("-" * 60)
    log.info("  Calibration summary:")
    log.info("    normal MSE mean   = %.6f", stats["normal_mse_mean"])
    log.info("    normal MSE std    = %.6f", stats["normal_mse_std"])
    log.info("    anomaly p95       = %.6f  ← Sprint 3 threshold candidate",
             stats["anomaly_threshold_p95"])
    log.info("    anomaly p99       = %.6f", stats["anomaly_threshold_p99"])

    if chaos_errors is not None and len(chaos_errors) > 0:
        ratio = stats.get("chaos_normal_ratio", 0)
        log.info("    chaos MSE mean    = %.6f  (%.1f× normal)",
                 stats["chaos_mse_mean"], ratio)
        if ratio >= 2.0:
            log.info("  ✓ DoD PROVEN: chaos MSE ≥ 2× normal — anomaly detection works!")
        else:
            log.warning("  ⚠ Separation ratio=%.1fx — consider more proxy events "
                        "or wider windows", ratio)

    return stats


# ===========================================================================
# Main
# ===========================================================================

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Grenadier Sprint 2 — Autoencoder Training")
    parser.add_argument("--epochs",      type=int,   default=100,  help="Max training epochs")
    parser.add_argument("--batch-size",  type=int,   default=512,  help="Batch size")
    parser.add_argument("--lr",          type=float, default=1e-3, help="Learning rate")
    parser.add_argument("--patience",    type=int,   default=10,   help="Early stopping patience")
    parser.add_argument("--phase2-only", action="store_true",
                        help="Skip Phase 1 (assume model already trained); only calibrate")
    parser.add_argument("--model-out",   type=Path,  default=MODEL_OUT,
                        help="Output path for .pt weights")
    parser.add_argument("--calib-out",   type=Path,  default=CALIB_OUT,
                        help="Output path for calibration JSON")
    args = parser.parse_args(argv)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Device: %s", device)

    # ── Load data ──────────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("Loading datasets")
    normal_data = _load_tensor(TRAIN_PARQUET, "train_normal.parquet")
    chaos_data  = _load_tensor(CHAOS_PARQUET, "val_chaos.parquet") if CHAOS_PARQUET.exists() else torch.empty(0, INPUT_DIM)

    # ── Build model ────────────────────────────────────────────────────────
    model = GrenadierAutoencoder(
        input_dim  = INPUT_DIM,
        hidden_dim = GrenadierAutoencoder.HIDDEN_DIM,
        latent_dim = GrenadierAutoencoder.LATENT_DIM,
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters())
    log.info("Model: GrenadierAutoencoder  params=%d", n_params)
    log.info("  Encoder: Linear(%d,%d)→ReLU→Linear(%d,%d)",
             INPUT_DIM, model.hidden_dim, model.hidden_dim, model.latent_dim)
    log.info("  Decoder: Linear(%d,%d)→ReLU→Linear(%d,%d)",
             model.latent_dim, model.hidden_dim, model.hidden_dim, INPUT_DIM)

    model_out = args.model_out
    calib_out = args.calib_out
    model_out.parent.mkdir(parents=True, exist_ok=True)

    # ── Phase 1 ────────────────────────────────────────────────────────────
    if args.phase2_only:
        if model_out.exists():
            log.info("Phase 1 skipped — loading existing weights from %s", model_out)
            model.load_state_dict(torch.load(model_out, map_location=device, weights_only=True))
        else:
            raise FileNotFoundError(
                f"--phase2-only but no model found at {model_out}"
            )
    else:
        losses = train(
            model,
            normal_data.to(device),
            epochs    = args.epochs,
            batch_size= args.batch_size,
            lr        = args.lr,
            patience  = args.patience,
        )

        # Save after Phase 1 (before calibration buffers are set)
        torch.save(model.state_dict(), model_out)
        log.info("  Phase 1 weights saved → %s", model_out)

    # ── Phase 2 ────────────────────────────────────────────────────────────
    calib_stats = calibrate(model, normal_data, chaos_data)

    # Save final model (with calibration buffers embedded)
    torch.save(model.state_dict(), model_out)
    log.info("  Final model (with calibration buffers) saved → %s", model_out)

    # Save calibration JSON for Sprint 3 threshold definition
    calib_full = {
        "model_path"   : str(model_out),
        "feature_cols" : FEAT_COLS,
        "input_dim"    : INPUT_DIM,
        "hidden_dim"   : GrenadierAutoencoder.HIDDEN_DIM,
        "latent_dim"   : GrenadierAutoencoder.LATENT_DIM,
        "training": {
            "epochs"    : args.epochs,
            "batch_size": args.batch_size,
            "lr"        : args.lr,
        },
        "calibration"  : calib_stats,
    }
    with open(calib_out, "w") as fh:
        json.dump(calib_full, fh, indent=2)
    log.info("  Calibration JSON saved → %s", calib_out)

    log.info("=" * 60)
    log.info("DONE — Grenadier Sprint 2 training complete")
    log.info("  Model    : %s", model_out)
    log.info("  Calib    : %s", calib_out)
    log.info("")
    log.info("Next step (Sprint 3): use anomaly_threshold_p95=%.6f as trigger.",
             calib_stats["anomaly_threshold_p95"])
    log.info("  If MSE > threshold → activate Defense Mode in event_processor.py")


if __name__ == "__main__":
    main()

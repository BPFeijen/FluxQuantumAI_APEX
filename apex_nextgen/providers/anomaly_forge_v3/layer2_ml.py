"""
layer2_ml.py — AnomalyForge V3: Layer 2 ML Inference
=====================================================

Transformer-Autoencoder + One-Class SVM dual scoring.
Model trained on SageMaker (3.5M samples, 2026-04-12).

Scoring:
    recon_mse  : reconstruction error from Transformer-AE
    ocsvm_score: distance from decision boundary (negative = anomaly)

Thresholds (from metadata.json):
    recon P95  = 5.89e-07  → flag if MSE > this
    ocsvm P5   = 7969.15   → flag if score < this
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

_logger = logging.getLogger("nextgen.anomaly_forge_v3.layer2")

MODEL_DIR = Path("C:/FluxQuantumAI/models/anomaly_forge_v3")


class _TransformerAE:
    """
    Matches the SageMaker training architecture exactly.
    State dict keys:
        encoder.input_proj.{weight,bias}       (128, 17) / (128,)
        encoder.transformer.layers.N.*         4 standard TransformerEncoder layers
        encoder.latent_proj.{weight,bias}      (32, 128) / (32,)
        encoder.norm.{weight,bias}             (32,) / (32,)
        decoder.latent_proj.{weight,bias}      (128, 32) / (128,)
        decoder.transformer.layers.N.*         4 standard TransformerEncoder layers (self-attn only)
        decoder.output_proj.{weight,bias}      (17, 128) / (17,)
    """

    def __init__(self, config: dict):
        import torch.nn as nn

        input_dim  = config["input_dim"]    # 17
        d_model    = config["d_model"]       # 128
        nhead      = config["nhead"]         # 8
        num_layers = config["num_layers"]    # 4
        latent_dim = config["latent_dim"]    # 32
        dropout    = config.get("dropout", 0.1)

        # Encoder
        enc_input_proj = nn.Linear(input_dim, d_model)
        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=d_model * 4,
            dropout=dropout, batch_first=True,
        )
        enc_transformer = nn.TransformerEncoder(enc_layer, num_layers=num_layers)
        enc_latent_proj = nn.Linear(d_model, latent_dim)
        enc_norm = nn.LayerNorm(latent_dim)

        # Decoder
        dec_latent_proj = nn.Linear(latent_dim, d_model)
        dec_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=d_model * 4,
            dropout=dropout, batch_first=True,
        )
        dec_transformer = nn.TransformerEncoder(dec_layer, num_layers=num_layers)
        dec_output_proj = nn.Linear(d_model, input_dim)

        # Assemble into ModuleDict matching state_dict keys
        self._encoder = nn.ModuleDict({
            "input_proj": enc_input_proj,
            "transformer": enc_transformer,
            "latent_proj": enc_latent_proj,
            "norm": enc_norm,
        })
        self._decoder = nn.ModuleDict({
            "latent_proj": dec_latent_proj,
            "transformer": dec_transformer,
            "output_proj": dec_output_proj,
        })
        self._model = nn.ModuleDict({
            "encoder": self._encoder,
            "decoder": self._decoder,
        })

    def load_state_dict(self, state_dict: dict):
        self._model.load_state_dict(state_dict, strict=True)

    def eval(self):
        self._model.eval()

    def encode(self, x):
        """x: (batch, 17) → (batch, 32)"""
        import torch
        with torch.no_grad():
            h = self._encoder["input_proj"](x).unsqueeze(1)   # (B, 1, 128)
            h = self._encoder["transformer"](h)                # (B, 1, 128)
            z = self._encoder["latent_proj"](h.squeeze(1))     # (B, 32)
            z = self._encoder["norm"](z)                        # (B, 32)
            return z

    def forward(self, x):
        """x: (batch, 17) → (batch, 17) reconstruction"""
        import torch
        with torch.no_grad():
            z = self.encode(x)                                  # (B, 32)
            h = self._decoder["latent_proj"](z).unsqueeze(1)   # (B, 1, 128)
            h = self._decoder["transformer"](h)                # (B, 1, 128)
            out = self._decoder["output_proj"](h.squeeze(1))   # (B, 17)
            return out


class AnomalyForgeV3ML:
    """
    Layer 2 ML scorer: Transformer-AE reconstruction + OC-SVM.
    Thread-safe for inference (eval mode, no gradients).
    """

    def __init__(self, model_dir: Path = MODEL_DIR):
        self._ready = False
        self._ae = None
        self._ocsvm = None
        self._scaler = None
        self._thr_recon = 1e-5
        self._thr_ocsvm = 0.0

        meta_path    = model_dir / "metadata.json"
        ae_path      = model_dir / "transformer_ae.pt"
        ocsvm_path   = model_dir / "ocsvm.pkl"
        scaler_path  = model_dir / "scaler.pkl"

        if not all(p.exists() for p in [meta_path, ae_path, ocsvm_path, scaler_path]):
            _logger.warning("AnomalyForge V3 Layer2: model artifacts missing in %s", model_dir)
            return

        try:
            import torch
            import joblib

            with open(meta_path) as f:
                meta = json.load(f)

            self._thr_recon = meta.get("threshold_recon_p95", 1e-5)
            self._thr_ocsvm = meta.get("threshold_ocsvm_p5", 0.0)

            ae_config = meta.get("ae_config", {})
            self._ae = _TransformerAE(ae_config)
            state = torch.load(ae_path, map_location="cpu", weights_only=False)
            self._ae.load_state_dict(state)
            self._ae.eval()

            self._scaler = joblib.load(scaler_path)
            self._ocsvm  = joblib.load(ocsvm_path)

            self._ready = True
            _logger.info(
                "AnomalyForge V3 Layer2 LOADED — %d features, "
                "thr_recon=%.2e, thr_ocsvm=%.1f, samples=%s",
                meta.get("n_features", 17),
                self._thr_recon, self._thr_ocsvm,
                meta.get("training_samples", "?"),
            )
        except Exception as e:
            _logger.error("AnomalyForge V3 Layer2 FAILED to load: %s", e, exc_info=True)

    @property
    def is_ready(self) -> bool:
        return self._ready

    def score(self, features: np.ndarray) -> dict:
        """
        Score a single observation.

        Parameters
        ----------
        features : np.ndarray of shape (17,)

        Returns
        -------
        dict with: anomaly, recon_mse, ocsvm_score, level, recon_flag, ocsvm_flag
        """
        result = {
            "anomaly": False, "recon_mse": 0.0, "ocsvm_score": 0.0,
            "level": "STUB", "recon_flag": False, "ocsvm_flag": False,
        }
        if not self._ready:
            return result

        try:
            import torch

            x_scaled = self._scaler.transform(features.reshape(1, -1))
            x_tensor = torch.tensor(x_scaled, dtype=torch.float32)

            x_recon = self._ae.forward(x_tensor)
            recon_mse = float(((x_tensor - x_recon) ** 2).mean())

            z = self._ae.encode(x_tensor)
            ocsvm_score = float(self._ocsvm.decision_function(z.numpy())[0])

            recon_flag = recon_mse > self._thr_recon
            ocsvm_flag = ocsvm_score < self._thr_ocsvm

            if recon_flag and ocsvm_flag:
                level = "ANOMALY"
                anomaly = True
            elif recon_flag or ocsvm_flag:
                level = "WARNING"
                anomaly = False
            else:
                level = "NORMAL"
                anomaly = False

            result.update({
                "anomaly": anomaly,
                "recon_mse": recon_mse,
                "ocsvm_score": ocsvm_score,
                "level": level,
                "recon_flag": recon_flag,
                "ocsvm_flag": ocsvm_flag,
            })
        except Exception as e:
            _logger.debug("Layer2 score error: %s", e)

        return result

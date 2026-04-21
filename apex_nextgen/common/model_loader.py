"""
model_loader.py — Infraestrutura de carregamento de modelos PyTorch (Sprint 3).

Garante:
  - forward pass com torch.inference_mode() (sem gradientes, máxima performance)
  - warm-up automático na carga (pré-compila JIT/graph)
  - fallback gracioso se torch não instalado
  - latência de inference registada

Uso:
    loader = ModelLoader(
        model_path   = Path("models/grenadier_lstm_autoencoder.pt"),
        model_class  = GrenadierLSTMAutoencoder,   # ou None para full-model pickle
        n_features   = 26,
        seq_len      = 60,
    )
    if loader.is_loaded:
        mse = loader.compute_mse(feature_tensor)   # tensor (1, seq_len, n_features)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Type

import numpy as np

_logger = logging.getLogger("nextgen.model_loader")

# ─── SequenceBuffer ───────────────────────────────────────────────────────────

class SequenceBuffer:
    """
    Buffer circular de vectores de feature para input de modelos sequenciais (LSTM).

    Acumula `seq_len` vectores de dimensão `n_features`.
    Quando cheio, `to_tensor()` retorna tensor pronto para inference.
    """

    def __init__(self, seq_len: int, n_features: int):
        self._seq_len   = seq_len
        self._n_features = n_features
        self._buf       = np.zeros((seq_len, n_features), dtype=np.float32)
        self._ptr       = 0      # próxima posição de escrita
        self._count     = 0      # ticks inseridos (até seq_len)

    def push(self, feature_vector: np.ndarray) -> bool:
        """
        Insere um vector de features (1D, n_features).
        Retorna True quando o buffer está cheio e pronto para inference.
        """
        if len(feature_vector) != self._n_features:
            # Truncar ou zero-pad silenciosamente
            vec = np.zeros(self._n_features, dtype=np.float32)
            n   = min(len(feature_vector), self._n_features)
            vec[:n] = feature_vector[:n]
        else:
            vec = np.asarray(feature_vector, dtype=np.float32)

        self._buf[self._ptr % self._seq_len] = vec
        self._ptr  += 1
        self._count = min(self._count + 1, self._seq_len)
        return self.is_full

    def to_tensor(self):
        """
        Converte buffer para tensor (1, seq_len, n_features) — batch size 1.
        A ordem é cronológica: índice 0 = tick mais antigo.
        """
        try:
            import torch
        except ImportError:
            raise RuntimeError("torch não instalado — instalar com: pip install torch")

        # Reordenar: o buffer é circular, ptr aponta para próxima escrita
        if self._count < self._seq_len:
            # Buffer ainda não cheio — preencher com zeros à esquerda
            arr = np.zeros((self._seq_len, self._n_features), dtype=np.float32)
            arr[-self._count:] = self._buf[:self._count]
        else:
            # Buffer cheio — reordenar a partir da posição mais antiga
            start = self._ptr % self._seq_len
            arr   = np.roll(self._buf, -start, axis=0)

        return torch.from_numpy(arr).unsqueeze(0)   # (1, seq_len, n_features)

    @property
    def is_full(self) -> bool:
        return self._count >= self._seq_len

    @property
    def fill_count(self) -> int:
        return self._count

    def reset(self):
        self._buf[:] = 0
        self._ptr    = 0
        self._count  = 0


# ─── ModelLoader ─────────────────────────────────────────────────────────────

class ModelLoader:
    """
    Carregador genérico de modelos PyTorch para inferência.

    Suporta dois formatos:
      A) state_dict: model_class instanciada + load_state_dict(checkpoint)
      B) full pickle: torch.load(path) → modelo completo

    Uso típico (AnomalyForge — Grenadier LSTM Autoencoder):
        loader = ModelLoader(
            model_path  = GRENADIER_MODEL_PATH,
            model_class = GrenadierLSTMAutoencoder,
            n_features  = 26,
            seq_len     = 60,
            norm_mean   = np.array([...]),
            norm_std    = np.array([...]),
        )
        mse = loader.compute_mse(tensor)
    """

    def __init__(
        self,
        model_path:  Path,
        model_class: Optional[Type] = None,
        n_features:  int  = 26,
        seq_len:     int  = 60,
        norm_mean:   Optional[np.ndarray] = None,
        norm_std:    Optional[np.ndarray] = None,
        device:      str  = "cpu",
        warmup_runs: int  = 3,
    ):
        self._path       = Path(model_path)
        self._model_cls  = model_class
        self.n_features  = n_features
        self.seq_len     = seq_len
        self._norm_mean  = norm_mean
        self._norm_std   = norm_std
        self._device     = device
        self._model      = None
        self._loaded     = False
        self._avg_lat_ms = 0.0
        self._inf_count  = 0

        self._load(warmup_runs)

    def _load(self, warmup_runs: int):
        if not self._path.exists():
            _logger.debug("ModelLoader: caminho nao encontrado: %s", self._path)
            return
        try:
            import torch
            if self._model_cls is not None:
                state = torch.load(str(self._path), map_location=self._device)
                # Suportar checkpoint dict com 'model_state_dict'
                if isinstance(state, dict) and "model_state_dict" in state:
                    state = state["model_state_dict"]
                self._model = self._model_cls()
                self._model.load_state_dict(state)
            else:
                self._model = torch.load(str(self._path), map_location=self._device)

            self._model.eval()
            self._loaded = True
            _logger.info(
                "ModelLoader: modelo carregado [%s] device=%s", self._path.name, self._device
            )
            self._warmup(warmup_runs)
        except Exception as e:
            _logger.warning("ModelLoader: falha ao carregar %s — %s", self._path.name, e)

    def _warmup(self, n: int):
        """Pre-compila o grafo de computação com n forward passes dummy."""
        try:
            import torch
            dummy = torch.zeros(1, self.seq_len, self.n_features)
            for _ in range(n):
                with torch.inference_mode():
                    self._model(dummy)
            _logger.info("ModelLoader: warm-up concluido (%d passes)", n)
        except Exception as e:
            _logger.debug("ModelLoader: warm-up falhou (nao critico) — %s", e)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def avg_latency_ms(self) -> float:
        return self._avg_lat_ms

    def forward(self, x) -> "torch.Tensor":
        """
        Forward pass com torch.inference_mode().
        x: tensor (batch, seq_len, n_features)
        """
        if not self._loaded:
            raise RuntimeError("Modelo nao carregado — verificar ModelLoader.is_loaded")
        import torch
        t0 = time.perf_counter()
        with torch.inference_mode():
            out = self._model(x)
        lat = (time.perf_counter() - t0) * 1000
        # Running average de latência
        self._inf_count += 1
        self._avg_lat_ms += (lat - self._avg_lat_ms) / self._inf_count
        # Modelos LSTM-Autoencoder podem retornar (reconstruction, hidden_state)
        # Normalizar sempre para tensor puro
        if isinstance(out, (tuple, list)):
            out = out[0]
        return out

    def normalize(self, arr: np.ndarray) -> np.ndarray:
        """Aplica normalização z-score se norm_mean/std fornecidos."""
        if self._norm_mean is None or self._norm_std is None:
            return arr
        std = np.where(self._norm_std == 0, 1.0, self._norm_std)
        return (arr - self._norm_mean) / std

    def compute_mse(self, x) -> float:
        """
        Convenience: forward pass num tensor de input e calcula MSE vs reconstrução.

        x: tensor (1, seq_len, n_features) ou np.ndarray equivalente
        Retorna: MSE escalar (float)
        """
        import torch
        if not isinstance(x, torch.Tensor):
            x = torch.from_numpy(np.asarray(x, dtype=np.float32))
        reconstruction = self.forward(x)
        mse = float(torch.mean((reconstruction - x) ** 2).item())
        return mse

    def make_buffer(self) -> SequenceBuffer:
        """Cria um SequenceBuffer configurado para este modelo."""
        return SequenceBuffer(seq_len=self.seq_len, n_features=self.n_features)

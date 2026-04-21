"""
flux_quantum_env.py — FluxQuantum Gym Environment para RL V3.

Estado do agente (30 features):
    - 26 features SCHEMA_FLUXFOX_V2 (normalizadas)
    -  1 grenadier_mse (normalizado, 0 se Grenadier não carregado)
    -  1 position_direction (-1=short, 0=flat, 1=long)
    -  1 unrealized_pnl_pts (PnL aberto em pontos GC)
    -  1 steps_in_position (tempo de holding normalizado)

Acções (Discrete 4):
    0 — HOLD
    1 — LONG  (entra long; ignora se já long)
    2 — SHORT (entra short; ignora se já short)
    3 — CLOSE (fecha posição; ignora se flat)

Reward:
    - Quando HOLD/na posição: unrealized PnL delta por step
    - Quando CLOSE/nova entrada: realized PnL - transaction_cost
    - Penalty: -anomaly_penalty × grenadier_mse se tenta abrir durante anomalia
    - Penalty: -max_drawdown_penalty se drawdown > max_drawdown_pts

Episódio:
    - Uma data de trading (um parquet de features)
    - Termina: fim da data OU drawdown > max_drawdown_pts

Dados esperados (parquet por data):
    features: SCHEMA_FLUXFOX_V2 + 'window_start' + 'mid_price'
    labels:   'window_start' + 'label' (0=NOISE, >0=ICEBERG)

    NOTA: 'mid_price' deve ser adicionado ao feature extractor.
    É o preço médio (bid+ask)/2 no início de cada janela.

Pré-requisitos de integração:
    - Grenadier V2 .pt + norm_stats + calibration carregados
    - ml_iceberg_v2 validado em produção
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import gymnasium as gym
from gymnasium import spaces

_logger = logging.getLogger(__name__)

# ─── Constantes GC ────────────────────────────────────────────────────────────
TICK_SIZE       = 0.1    # 1 tick GC = 0.1 pts = $10
TICK_VALUE      = 10.0   # $ por tick
TRANSACTION_COST_PTS = 0.3   # ~$3 round-trip (spread + comissão) em pontos

# ─── Acções ───────────────────────────────────────────────────────────────────
ACTION_HOLD  = 0
ACTION_LONG  = 1
ACTION_SHORT = 2
ACTION_CLOSE = 3
N_ACTIONS    = 4

# ─── Schema ───────────────────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parents[3]))
from schemas.SCHEMA_FLUXFOX_V2 import SCHEMA_FLUXFOX_V2, N_FEATURES  # 26

# ─── State dims ───────────────────────────────────────────────────────────────
N_POSITION_FEATURES  = 3   # direction, unrealized_pnl, steps_in_position
N_GRENADIER_FEATURES = 1   # mse normalizado
OBS_DIM = N_FEATURES + N_GRENADIER_FEATURES + N_POSITION_FEATURES  # 30


class FluxQuantumEnv(gym.Env):
    """
    Gymnasium Environment para treino RL V3.

    Parameters
    ----------
    features_dir : Path
        Directório com parquets de features (SCHEMA_FLUXFOX_V2 + mid_price).
    labels_dir : Path
        Directório com parquets de labels.
    dates : List[str]
        Lista de datas (YYYY-MM-DD) a usar neste split.
    norm_stats : dict
        {"mean": np.array(26,), "std": np.array(26,)}  — stats do Grenadier V2.
    grenadier_threshold : float
        MSE acima deste valor → anomalia → penalty por abrir posição.
    max_drawdown_pts : float
        Drawdown máximo por episódio antes de terminar forcibly (default 20 pts).
    anomaly_penalty : float
        Penalidade adicional por tentar abrir posição em mercado anómalo.
    max_steps_per_episode : int
        Máximo de steps por episódio (trunca se necessário).
    seed : int
        Seed para reproducibilidade.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        features_dir: Path,
        labels_dir: Path,
        dates: List[str],
        norm_stats: Optional[Dict] = None,
        grenadier_threshold: float = 1e-4,
        max_drawdown_pts: float = 20.0,
        anomaly_penalty: float = 2.0,
        max_steps_per_episode: int = 3600,
    ):
        super().__init__()

        self.features_dir   = Path(features_dir)
        self.labels_dir     = Path(labels_dir)
        self.dates          = dates
        self.norm_stats     = norm_stats
        self.grenadier_threshold  = grenadier_threshold
        self.max_drawdown_pts     = max_drawdown_pts
        self.anomaly_penalty      = anomaly_penalty
        self.max_steps_per_episode = max_steps_per_episode

        # Spaces
        self.observation_space = spaces.Box(
            low=-10.0, high=10.0,
            shape=(OBS_DIM,), dtype=np.float32,
        )
        self.action_space = spaces.Discrete(N_ACTIONS)

        # Estado do episódio (inicializado no reset)
        self._X: Optional[np.ndarray] = None    # (n_steps, 26)
        self._prices: Optional[np.ndarray] = None  # (n_steps,) mid_price
        self._mses: Optional[np.ndarray] = None    # (n_steps,) grenadier MSE
        self._n_steps = 0
        self._step_idx = 0

        # Estado da posição
        self._position    = 0     # -1, 0, 1
        self._entry_price = 0.0
        self._episode_pnl = 0.0
        self._max_pnl     = 0.0
        self._steps_in_pos = 0

    # ─────────────────────────────────────────────────────────────────────────
    def _load_date(self, date: str) -> bool:
        """Carrega features e labels de uma data. Retorna False se inválido."""
        feat_path  = self.features_dir / f"features_{date}.parquet"
        label_path = self.labels_dir   / f"labels_{date}.parquet"

        if not feat_path.exists() or not label_path.exists():
            return False

        try:
            feat_df  = pd.read_parquet(feat_path)
            label_df = pd.read_parquet(label_path)
        except Exception as e:
            _logger.warning("Erro a carregar %s: %s", date, e)
            return False

        merged = feat_df.merge(
            label_df[["window_start", "label"]],
            on="window_start", how="inner",
        ).sort_values("window_start").reset_index(drop=True)

        if len(merged) < 10:
            return False

        # Features FLUXFOX (26)
        for col in SCHEMA_FLUXFOX_V2:
            if col not in merged.columns:
                merged[col] = 0.0

        X = merged[SCHEMA_FLUXFOX_V2].values.astype(np.float32)

        # Normalizar com stats do Grenadier V2 (se disponível)
        if self.norm_stats is not None:
            mean = self.norm_stats["mean"].astype(np.float32)
            std  = self.norm_stats["std"].astype(np.float32)
            X = (X - mean) / np.where(std == 0, 1.0, std)
            np.nan_to_num(X, copy=False, nan=0.0, posinf=5.0, neginf=-5.0)

        self._X = X
        self._n_steps = len(X)

        # Preço mid (necessário para PnL real)
        if "mid_price" in merged.columns:
            self._prices = merged["mid_price"].values.astype(np.float32)
        else:
            # Fallback: simulação (APENAS para testes — adicionar mid_price ao extractor)
            _logger.warning(
                "mid_price não disponível para %s — usando simulação de preço. "
                "Adicionar mid_price ao feature extractor para produção.", date
            )
            base = 2000.0
            returns = np.random.normal(0, 0.3, self._n_steps).cumsum()
            self._prices = (base + returns).astype(np.float32)

        # Grenadier MSE (placeholder — será preenchido quando o scorer estiver integrado)
        # TODO Sprint 3: calcular MSE real com GrenadierLSTMAutoencoder
        self._mses = np.zeros(self._n_steps, dtype=np.float32)

        return True

    # ─────────────────────────────────────────────────────────────────────────
    def _get_obs(self) -> np.ndarray:
        """Constrói o vector de observação (30 features)."""
        i = self._step_idx

        # 26 features FLUXFOX
        feat = self._X[i].copy()

        # 1 Grenadier MSE normalizado [0, 1]
        mse_raw = self._mses[i]
        mse_norm = float(np.clip(mse_raw / (self.grenadier_threshold * 3 + 1e-10), 0, 1))

        # 3 Position features
        direction       = float(self._position)
        unrealized_pnl  = 0.0
        if self._position != 0 and i < len(self._prices):
            unrealized_pnl = (self._prices[i] - self._entry_price) * self._position
        unrealized_norm = float(np.clip(unrealized_pnl / 10.0, -5.0, 5.0))  # normalizado por 10 pts
        steps_norm      = float(np.clip(self._steps_in_pos / 300.0, 0.0, 1.0))  # normalizado por 300s

        obs = np.concatenate([
            feat,
            [mse_norm, direction, unrealized_norm, steps_norm],
        ]).astype(np.float32)

        return obs

    # ─────────────────────────────────────────────────────────────────────────
    def reset(
        self,
        seed: Optional[int] = None,
        options: Optional[dict] = None,
    ) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)

        # Escolhe data aleatória
        loaded = False
        attempts = 0
        while not loaded and attempts < len(self.dates):
            date = self.np_random.choice(self.dates)
            loaded = self._load_date(date)
            attempts += 1

        if not loaded:
            raise RuntimeError("Nenhuma data válida disponível no split")

        self._step_idx     = 0
        self._position     = 0
        self._entry_price  = 0.0
        self._episode_pnl  = 0.0
        self._max_pnl      = 0.0
        self._steps_in_pos = 0

        return self._get_obs(), {}

    # ─────────────────────────────────────────────────────────────────────────
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, dict]:
        i     = self._step_idx
        price = self._prices[i]
        mse   = self._mses[i]

        reward      = 0.0
        is_anomaly  = mse > self.grenadier_threshold

        # ── Processar acção ──────────────────────────────────────────────────
        if action == ACTION_LONG:
            if self._position == 0:
                if is_anomaly:
                    reward -= self.anomaly_penalty  # penaliza entrar em anomalia
                else:
                    self._position    = 1
                    self._entry_price = price
                    self._steps_in_pos = 0
                    reward -= TRANSACTION_COST_PTS  # custo de entrada
            elif self._position == -1:
                # Fechar short + abrir long
                realized = (self._entry_price - price) - TRANSACTION_COST_PTS
                reward  += realized
                self._episode_pnl += realized
                if is_anomaly:
                    reward -= self.anomaly_penalty
                    self._position = 0
                else:
                    self._position    = 1
                    self._entry_price = price
                    self._steps_in_pos = 0
                    reward -= TRANSACTION_COST_PTS

        elif action == ACTION_SHORT:
            if self._position == 0:
                if is_anomaly:
                    reward -= self.anomaly_penalty
                else:
                    self._position    = -1
                    self._entry_price = price
                    self._steps_in_pos = 0
                    reward -= TRANSACTION_COST_PTS
            elif self._position == 1:
                # Fechar long + abrir short
                realized = (price - self._entry_price) - TRANSACTION_COST_PTS
                reward  += realized
                self._episode_pnl += realized
                if is_anomaly:
                    reward -= self.anomaly_penalty
                    self._position = 0
                else:
                    self._position    = -1
                    self._entry_price = price
                    self._steps_in_pos = 0
                    reward -= TRANSACTION_COST_PTS

        elif action == ACTION_CLOSE:
            if self._position == 1:
                realized = (price - self._entry_price) - TRANSACTION_COST_PTS
                reward  += realized
                self._episode_pnl += realized
                self._position = 0
            elif self._position == -1:
                realized = (self._entry_price - price) - TRANSACTION_COST_PTS
                reward  += realized
                self._episode_pnl += realized
                self._position = 0

        # ── HOLD ou posição aberta: PnL delta ────────────────────────────────
        if self._position != 0 and action == ACTION_HOLD:
            next_i     = min(i + 1, self._n_steps - 1)
            next_price = self._prices[next_i]
            pnl_delta  = (next_price - price) * self._position
            reward    += pnl_delta

        if self._position != 0:
            self._steps_in_pos += 1

        # ── Max drawdown ──────────────────────────────────────────────────────
        self._max_pnl = max(self._max_pnl, self._episode_pnl)
        drawdown      = self._max_pnl - self._episode_pnl

        # ── Avançar ───────────────────────────────────────────────────────────
        self._step_idx += 1

        terminated = self._step_idx >= self._n_steps
        truncated  = (
            drawdown > self.max_drawdown_pts or
            self._step_idx >= self.max_steps_per_episode
        )

        # Fechar posição no fim do episódio
        if (terminated or truncated) and self._position != 0:
            last_price = self._prices[min(self._step_idx, self._n_steps - 1)]
            if self._position == 1:
                close_pnl = (last_price - self._entry_price) - TRANSACTION_COST_PTS
            else:
                close_pnl = (self._entry_price - last_price) - TRANSACTION_COST_PTS
            reward += close_pnl
            self._episode_pnl += close_pnl
            self._position = 0

        obs = self._get_obs() if not terminated else np.zeros(OBS_DIM, dtype=np.float32)

        info = {
            "episode_pnl": self._episode_pnl,
            "is_anomaly":  is_anomaly,
            "position":    self._position,
            "drawdown":    drawdown,
        }

        return obs, float(reward), terminated, truncated, info

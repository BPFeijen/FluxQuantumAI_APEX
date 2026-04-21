"""
rl_agent.py — Wrapper de inferência RL V3 para produção.

Interface para integrar o agente PPO no event_processor.py.

Uso (Sprint 3+ — quando integrar):
    agent = RLAgent.load(
        model_path  = "fluxquantum_ppo_best.zip",
        norm_stats  = "grenadier_norm_stats.json",
        threshold   = grenadier_calibration["threshold"],
    )

    # A cada tick do event_processor:
    action, info = agent.get_action(
        features       = current_fluxfox_window,   # dict ou np.array (26,)
        grenadier_mse  = current_mse,              # float
        position       = current_position,          # -1, 0, 1
        unrealized_pnl = open_pnl_pts,             # float
        steps_in_pos   = steps_since_entry,        # int
    )
    # action ∈ {0: HOLD, 1: LONG, 2: SHORT, 3: CLOSE}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

import numpy as np

_logger = logging.getLogger(__name__)

ACTION_NAMES = {0: "HOLD", 1: "LONG", 2: "SHORT", 3: "CLOSE"}


class RLAgent:
    """
    Wrapper de produção para o agente PPO treinado.

    Thread-safe para leitura (get_action é stateless).
    O estado da posição é passado como argumento — não é mantido internamente.
    """

    def __init__(
        self,
        model,                          # stable_baselines3.PPO carregado
        norm_mean: np.ndarray,          # (26,)
        norm_std:  np.ndarray,          # (26,)
        grenadier_threshold: float,
        deterministic: bool = True,
    ):
        self._model              = model
        self._norm_mean          = norm_mean
        self._norm_std           = norm_std
        self._grenadier_threshold = grenadier_threshold
        self._deterministic      = deterministic

        _logger.info(
            "RLAgent carregado | threshold=%.6f | deterministic=%s",
            grenadier_threshold, deterministic,
        )

    @classmethod
    def load(
        cls,
        model_path: Union[str, Path],
        norm_stats_path: Union[str, Path],
        grenadier_threshold: float,
        deterministic: bool = True,
    ) -> "RLAgent":
        """
        Carrega agente de ficheiros de produção.

        Parameters
        ----------
        model_path          : path para fluxquantum_ppo_best.zip
        norm_stats_path     : path para grenadier_norm_stats.json
        grenadier_threshold : threshold calibrado (grenadier_calibration.json → "threshold")
        deterministic       : True para produção (acção de maior probabilidade)
        """
        from stable_baselines3 import PPO

        model = PPO.load(str(model_path))
        _logger.info("Modelo PPO carregado: %s", model_path)

        with open(norm_stats_path) as f:
            raw = json.load(f)
        norm_mean = np.array(raw["mean"], dtype=np.float32)
        norm_std  = np.array(raw["std"],  dtype=np.float32)

        return cls(
            model=model,
            norm_mean=norm_mean,
            norm_std=norm_std,
            grenadier_threshold=grenadier_threshold,
            deterministic=deterministic,
        )

    def _build_obs(
        self,
        features: Union[Dict, np.ndarray],
        grenadier_mse: float,
        position: int,
        unrealized_pnl: float,
        steps_in_pos: int,
    ) -> np.ndarray:
        """Constrói vector de observação (30,) como no FluxQuantumEnv."""
        import sys
        sys.path.insert(0, str(Path(__file__).parents[3]))
        from schemas.SCHEMA_FLUXFOX_V2 import SCHEMA_FLUXFOX_V2

        # Features FLUXFOX (26)
        if isinstance(features, dict):
            feat = np.array(
                [float(features.get(c, 0.0)) for c in SCHEMA_FLUXFOX_V2],
                dtype=np.float32,
            )
        else:
            feat = np.array(features, dtype=np.float32)[:26]

        # Normalizar
        std = np.where(self._norm_std == 0, 1.0, self._norm_std)
        feat = (feat - self._norm_mean) / std
        np.nan_to_num(feat, copy=False, nan=0.0, posinf=5.0, neginf=-5.0)

        # Grenadier MSE normalizado
        mse_norm = float(np.clip(
            grenadier_mse / (self._grenadier_threshold * 3 + 1e-10), 0, 1
        ))

        # Position features
        direction      = float(position)
        unrealized_norm = float(np.clip(unrealized_pnl / 10.0, -5.0, 5.0))
        steps_norm     = float(np.clip(steps_in_pos / 300.0, 0.0, 1.0))

        obs = np.concatenate([
            feat,
            [mse_norm, direction, unrealized_norm, steps_norm],
        ]).astype(np.float32)

        return obs

    def get_action(
        self,
        features: Union[Dict, np.ndarray],
        grenadier_mse: float = 0.0,
        position: int = 0,
        unrealized_pnl: float = 0.0,
        steps_in_pos: int = 0,
    ) -> Tuple[int, Dict]:
        """
        Calcula a acção do agente para o estado actual.

        Parameters
        ----------
        features       : features SCHEMA_FLUXFOX_V2 (dict ou array 26,)
        grenadier_mse  : MSE de reconstrução do Grenadier V2 (0.0 se não disponível)
        position       : posição actual (-1=short, 0=flat, 1=long)
        unrealized_pnl : PnL aberto em pontos GC
        steps_in_pos   : steps desde a última entrada

        Returns
        -------
        action : int (0=HOLD, 1=LONG, 2=SHORT, 3=CLOSE)
        info   : dict com metadados (action_name, is_anomaly, obs_norm)
        """
        is_anomaly = grenadier_mse > self._grenadier_threshold

        obs = self._build_obs(
            features=features,
            grenadier_mse=grenadier_mse,
            position=position,
            unrealized_pnl=unrealized_pnl,
            steps_in_pos=steps_in_pos,
        )

        action, _states = self._model.predict(
            obs[np.newaxis, :],
            deterministic=self._deterministic,
        )
        action = int(action[0])

        # Hard override: Grenadier bloqueia novas entradas em anomalia
        # (o modelo foi penalizado durante treino, mas este override é a garantia final)
        if is_anomaly and action in (1, 2):  # LONG ou SHORT
            _logger.warning(
                "Grenadier override: agente queria %s mas MSE=%.6f > threshold=%.6f → HOLD",
                ACTION_NAMES[action], grenadier_mse, self._grenadier_threshold,
            )
            action = 0  # HOLD

        info = {
            "action_name":   ACTION_NAMES[action],
            "is_anomaly":    is_anomaly,
            "grenadier_mse": grenadier_mse,
            "position":      position,
        }

        return action, info

    def is_safe_to_trade(self, grenadier_mse: float) -> bool:
        """Conveniência para verificar se o Grenadier permite trading."""
        return grenadier_mse <= self._grenadier_threshold

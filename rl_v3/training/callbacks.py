"""
callbacks.py — SB3 Callbacks para treino RL V3.

EpisodeStatsCallback: regista PnL, WR, drawdown por episódio.
WalkForwardCallback:  avalia no val set a cada N steps e guarda melhor modelo.
"""

from __future__ import annotations

import logging
import numpy as np
from typing import List, Optional
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback

_logger = logging.getLogger(__name__)


class EpisodeStatsCallback(BaseCallback):
    """
    Regista métricas por episódio no TensorBoard / logs.

    Métricas tracked:
        train/episode_pnl       — PnL total do episódio em pontos GC
        train/episode_length    — número de steps
        train/anomaly_rate      — % de steps em anomalia (Grenadier MSE > threshold)
        train/pnl_per_step      — eficiência por step
    """

    def __init__(self, verbose: int = 0):
        super().__init__(verbose)
        self._episode_rewards: List[float] = []
        self._episode_lengths: List[int]   = []
        self._episode_pnls:    List[float] = []
        self._anomaly_steps:   int = 0
        self._total_steps:     int = 0

    def _on_step(self) -> bool:
        # Acumula info de cada step
        infos = self.locals.get("infos", [])
        for info in infos:
            self._total_steps += 1
            if info.get("is_anomaly", False):
                self._anomaly_steps += 1

        # Detecta fim de episódio
        dones = self.locals.get("dones", [])
        for i, done in enumerate(dones):
            if done:
                ep_info = self.locals.get("infos", [{}])[i].get("episode", {})
                ep_pnl  = self.locals.get("infos", [{}])[i].get("episode_pnl", 0.0)

                self._episode_pnls.append(ep_pnl)

                if ep_info:
                    r = ep_info.get("r", 0)
                    l = ep_info.get("l", 0)
                    self._episode_rewards.append(r)
                    self._episode_lengths.append(l)

                    if len(self._episode_pnls) % 10 == 0:
                        mean_pnl = np.mean(self._episode_pnls[-50:])
                        mean_len = np.mean(self._episode_lengths[-50:])
                        anomaly_rate = (
                            self._anomaly_steps / max(self._total_steps, 1)
                        )
                        _logger.info(
                            "Ep %d | mean_pnl(50)=%.2f pts | len=%.0f | anomaly=%.1f%%",
                            len(self._episode_pnls), mean_pnl, mean_len,
                            anomaly_rate * 100,
                        )

                        if self.logger:
                            self.logger.record("train/episode_pnl_mean", mean_pnl)
                            self.logger.record("train/episode_length_mean", mean_len)
                            self.logger.record("train/anomaly_rate", anomaly_rate)

        return True

    def get_summary(self) -> dict:
        if not self._episode_pnls:
            return {}
        pnls = np.array(self._episode_pnls)
        return {
            "n_episodes":    len(pnls),
            "mean_pnl":      float(pnls.mean()),
            "median_pnl":    float(np.median(pnls)),
            "win_rate":      float((pnls > 0).mean()),
            "profit_factor": float(pnls[pnls > 0].sum() / (-pnls[pnls < 0].sum() + 1e-8)),
            "max_episode_pnl": float(pnls.max()),
            "min_episode_pnl": float(pnls.min()),
        }

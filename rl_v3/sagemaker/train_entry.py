"""
train_entry.py — SageMaker entry point para RL V3 PPO.

Env vars SageMaker:
    SM_CHANNEL_FEATURES    → /opt/ml/input/data/features/
    SM_CHANNEL_LABELS      → /opt/ml/input/data/labels/
    SM_CHANNEL_GRENADIER   → /opt/ml/input/data/grenadier/  (norm_stats + calibration)
    SM_MODEL_DIR           → /opt/ml/model/
    SM_OUTPUT_DATA_DIR     → /opt/ml/output/data/
    SM_HPS                 → JSON com hyperparameters

Hyperparameters:
    timesteps          int    default 2000000
    n_envs             int    default 4
    learning_rate      float  default 3e-4
    batch_size         int    default 256
    max_drawdown_pts   float  default 20.0
    anomaly_penalty    float  default 2.0
    eval_freq          int    default 50000
    device             str    default "auto"
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
_logger = logging.getLogger("rl_entry")


def _hp(key, default, cast=str):
    hps = json.loads(os.environ.get("SM_HPS", "{}"))
    val = hps.get(key, os.environ.get(key.upper(), default))
    try:
        return cast(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def main():
    features_dir    = os.environ.get("SM_CHANNEL_FEATURES",  "/opt/ml/input/data/features")
    labels_dir      = os.environ.get("SM_CHANNEL_LABELS",    "/opt/ml/input/data/labels")
    grenadier_dir   = os.environ.get("SM_CHANNEL_GRENADIER", "/opt/ml/input/data/grenadier")
    model_dir       = os.environ.get("SM_MODEL_DIR",         "/opt/ml/model")
    output_dir      = os.environ.get("SM_OUTPUT_DATA_DIR",   "/opt/ml/output/data")

    Path(model_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timesteps        = _hp("timesteps",        2_000_000, int)
    n_envs           = _hp("n_envs",           4,         int)
    learning_rate    = _hp("learning_rate",    3e-4,      float)
    batch_size       = _hp("batch_size",       256,       int)
    max_drawdown_pts = _hp("max_drawdown_pts", 20.0,      float)
    anomaly_penalty  = _hp("anomaly_penalty",  2.0,       float)
    eval_freq        = _hp("eval_freq",        50_000,    int)
    device           = _hp("device",           "auto",    str)

    # Grenadier norm_stats e threshold
    norm_stats_path = None
    grenadier_threshold = 1e-4  # default conservador

    norm_path = Path(grenadier_dir) / "grenadier_norm_stats.json"
    cal_path  = Path(grenadier_dir) / "grenadier_calibration.json"

    if norm_path.exists():
        norm_stats_path = str(norm_path)
        _logger.info("Grenadier norm_stats: %s", norm_stats_path)
    else:
        _logger.warning("grenadier_norm_stats.json não encontrado — features não normalizadas")

    if cal_path.exists():
        with open(cal_path) as f:
            cal = json.load(f)
        grenadier_threshold = float(cal.get("threshold", grenadier_threshold))
        _logger.info("Grenadier threshold calibrado: %.6f", grenadier_threshold)
    else:
        _logger.warning("grenadier_calibration.json não encontrado — usando threshold default %.6f", grenadier_threshold)

    _logger.info("features: %s | labels: %s | model: %s", features_dir, labels_dir, model_dir)
    _logger.info(
        "HPs: timesteps=%d n_envs=%d lr=%g batch=%d drawdown=%.1f penalty=%.1f device=%s",
        timesteps, n_envs, learning_rate, batch_size, max_drawdown_pts, anomaly_penalty, device,
    )

    from rl_v3.training.train_ppo import train_ppo

    history = train_ppo(
        features_dir=features_dir,
        labels_dir=labels_dir,
        models_dir=model_dir,
        norm_stats_path=norm_stats_path,
        grenadier_threshold=grenadier_threshold,
        total_timesteps=timesteps,
        n_envs=n_envs,
        learning_rate=learning_rate,
        batch_size=batch_size,
        max_drawdown_pts=max_drawdown_pts,
        anomaly_penalty=anomaly_penalty,
        eval_freq=eval_freq,
        device=device,
    )

    results_path = Path(output_dir) / "rl_training_results.json"
    with open(results_path, "w") as f:
        json.dump(history, f, indent=2)

    _logger.info("Treino RL V3 concluído.")
    stats = history.get("episode_stats", {})
    if stats:
        _logger.info(
            "Win rate=%.1f%%  Mean PnL=%.2f pts  PF=%.3f",
            stats.get("win_rate", 0) * 100,
            stats.get("mean_pnl", 0),
            stats.get("profit_factor", 0),
        )


if __name__ == "__main__":
    main()

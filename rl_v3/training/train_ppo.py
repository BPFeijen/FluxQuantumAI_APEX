"""
train_ppo.py — Treino PPO para FluxQuantum RL V3.

Walk-forward split por datas:
    Train : primeiras 70% das datas disponíveis
    Val   : 15% seguintes  (usado para salvar melhor modelo)
    Test  : últimas 15%    (avaliação final — não tocar durante treino)

Algoritmo: PPO (stable-baselines3)
Política:  MlpPolicy (MLP feed-forward, 2 layers × 256 units)

Output:
    models_dir/
        fluxquantum_ppo_best.zip      — melhor modelo (val reward)
        fluxquantum_ppo_final.zip     — modelo no fim do treino
        training_history.json         — métricas por checkpoint

Uso:
    python train_ppo.py \
        --features-dir /data/features \
        --labels-dir   /data/labels \
        --norm-stats   /models/grenadier_norm_stats.json \
        --grenadier-threshold 1e-4 \
        --timesteps 2000000 \
        --models-dir /models

Pré-requisitos:
    pip install stable-baselines3 gymnasium pandas pyarrow
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
_logger = logging.getLogger("rl_v3.train")


def _load_dates(features_dir: Path, labels_dir: Path) -> List[str]:
    """Retorna datas com parquets em ambos os dirs, ordenadas."""
    feat_dates  = {f.stem.replace("features_", "") for f in features_dir.glob("features_*.parquet")}
    label_dates = {f.stem.replace("labels_", "")   for f in labels_dir.glob("labels_*.parquet")}
    common = sorted(feat_dates & label_dates)
    _logger.info("Datas disponíveis: %d", len(common))
    return common


def _walk_forward_split(dates: List[str], train_frac=0.70, val_frac=0.15):
    """Split temporal (sem shuffle — evitar look-ahead bias)."""
    n = len(dates)
    n_train = max(1, int(n * train_frac))
    n_val   = max(1, int(n * val_frac))
    train_dates = dates[:n_train]
    val_dates   = dates[n_train: n_train + n_val]
    test_dates  = dates[n_train + n_val:]
    _logger.info("Split: train=%d val=%d test=%d datas", len(train_dates), len(val_dates), len(test_dates))
    return train_dates, val_dates, test_dates


def train_ppo(
    features_dir: str,
    labels_dir: str,
    models_dir: str,
    norm_stats_path: Optional[str] = None,
    grenadier_threshold: float = 1e-4,
    total_timesteps: int = 2_000_000,
    n_envs: int = 4,
    learning_rate: float = 3e-4,
    n_steps: int = 2048,
    batch_size: int = 256,
    n_epochs: int = 10,
    gamma: float = 0.99,
    max_drawdown_pts: float = 20.0,
    anomaly_penalty: float = 2.0,
    eval_freq: int = 50_000,
    device: str = "auto",
) -> dict:
    """
    Treina o agente PPO no FluxQuantumEnv.

    Returns
    -------
    dict com histórico de treino e métricas finais.
    """
    from stable_baselines3 import PPO
    from stable_baselines3.common.env_util import make_vec_env
    from stable_baselines3.common.callbacks import EvalCallback, CallbackList
    from stable_baselines3.common.monitor import Monitor

    sys.path.insert(0, str(Path(__file__).parents[3]))
    from rl_v3.envs import FluxQuantumEnv
    from rl_v3.training.callbacks import EpisodeStatsCallback

    features_dir = Path(features_dir)
    labels_dir   = Path(labels_dir)
    models_dir   = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    # ── Norm stats (Grenadier V2) ─────────────────────────────────────────────
    norm_stats = None
    if norm_stats_path and Path(norm_stats_path).exists():
        with open(norm_stats_path) as f:
            raw = json.load(f)
        norm_stats = {
            "mean": np.array(raw["mean"], dtype=np.float32),
            "std":  np.array(raw["std"],  dtype=np.float32),
        }
        _logger.info("Norm stats carregados: %s", norm_stats_path)
    else:
        _logger.warning("norm_stats não disponível — features não normalizadas pelo Grenadier")

    # ── Datas e split ─────────────────────────────────────────────────────────
    all_dates = _load_dates(features_dir, labels_dir)
    if len(all_dates) < 10:
        raise ValueError(f"Poucas datas disponíveis: {len(all_dates)}. Mínimo 10.")

    train_dates, val_dates, test_dates = _walk_forward_split(all_dates)

    # Guardar split para reprodutibilidade
    split_path = models_dir / "walk_forward_split.json"
    with open(split_path, "w") as f:
        json.dump({"train": train_dates, "val": val_dates, "test": test_dates}, f, indent=2)

    # ── Environments ──────────────────────────────────────────────────────────
    env_kwargs = dict(
        features_dir=features_dir,
        labels_dir=labels_dir,
        norm_stats=norm_stats,
        grenadier_threshold=grenadier_threshold,
        max_drawdown_pts=max_drawdown_pts,
        anomaly_penalty=anomaly_penalty,
    )

    def _make_train_env():
        env = FluxQuantumEnv(dates=train_dates, **env_kwargs)
        return Monitor(env)

    def _make_val_env():
        env = FluxQuantumEnv(dates=val_dates, **env_kwargs)
        return Monitor(env)

    train_env = make_vec_env(_make_train_env, n_envs=n_envs)
    val_env   = make_vec_env(_make_val_env,   n_envs=1)

    # ── Modelo PPO ────────────────────────────────────────────────────────────
    model = PPO(
        policy="MlpPolicy",
        env=train_env,
        learning_rate=learning_rate,
        n_steps=n_steps,
        batch_size=batch_size,
        n_epochs=n_epochs,
        gamma=gamma,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,        # entropia para exploração
        vf_coef=0.5,
        max_grad_norm=0.5,
        policy_kwargs=dict(net_arch=[256, 256]),
        verbose=1,
        device=device,
        tensorboard_log=str(models_dir / "tensorboard"),
    )

    _logger.info(
        "PPO: timesteps=%d  envs=%d  lr=%g  batch=%d  device=%s",
        total_timesteps, n_envs, learning_rate, batch_size, device,
    )

    # ── Callbacks ─────────────────────────────────────────────────────────────
    best_model_path = str(models_dir / "fluxquantum_ppo_best")

    eval_callback = EvalCallback(
        eval_env=val_env,
        best_model_save_path=best_model_path,
        log_path=str(models_dir / "eval_logs"),
        eval_freq=max(eval_freq // n_envs, 1),
        n_eval_episodes=len(val_dates),
        deterministic=True,
        verbose=1,
    )

    stats_callback = EpisodeStatsCallback(verbose=0)
    callbacks = CallbackList([eval_callback, stats_callback])

    # ── Treino ────────────────────────────────────────────────────────────────
    _logger.info("=" * 60)
    _logger.info("INÍCIO TREINO PPO — %d timesteps", total_timesteps)
    _logger.info("=" * 60)

    model.learn(
        total_timesteps=total_timesteps,
        callback=callbacks,
        progress_bar=True,
        reset_num_timesteps=True,
    )

    # ── Guardar modelo final ──────────────────────────────────────────────────
    final_path = models_dir / "fluxquantum_ppo_final.zip"
    model.save(str(final_path))
    _logger.info("Modelo final → %s", final_path)

    # ── Sumário de treino ─────────────────────────────────────────────────────
    summary = stats_callback.get_summary()
    _logger.info("Sumário: %s", summary)

    history = {
        "total_timesteps": total_timesteps,
        "train_dates":     train_dates,
        "val_dates":       val_dates,
        "test_dates":      test_dates,
        "episode_stats":   summary,
        "best_model_path": best_model_path + ".zip",
        "final_model_path": str(final_path),
        "grenadier_threshold": grenadier_threshold,
        "norm_stats_used": norm_stats_path,
    }

    history_path = models_dir / "training_history.json"
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)

    _logger.info("Histórico → %s", history_path)

    train_env.close()
    val_env.close()

    return history


def main():
    p = argparse.ArgumentParser(description="RL V3 PPO Training")
    p.add_argument("--features-dir",         required=True)
    p.add_argument("--labels-dir",           required=True)
    p.add_argument("--models-dir",           required=True)
    p.add_argument("--norm-stats",           default=None,  help="grenadier_norm_stats.json")
    p.add_argument("--grenadier-threshold",  type=float, default=1e-4)
    p.add_argument("--timesteps",            type=int,   default=2_000_000)
    p.add_argument("--n-envs",               type=int,   default=4)
    p.add_argument("--learning-rate",        type=float, default=3e-4)
    p.add_argument("--batch-size",           type=int,   default=256)
    p.add_argument("--max-drawdown-pts",     type=float, default=20.0)
    p.add_argument("--anomaly-penalty",      type=float, default=2.0)
    p.add_argument("--eval-freq",            type=int,   default=50_000)
    p.add_argument("--device",               default="auto")
    args = p.parse_args()

    history = train_ppo(
        features_dir=args.features_dir,
        labels_dir=args.labels_dir,
        models_dir=args.models_dir,
        norm_stats_path=args.norm_stats,
        grenadier_threshold=args.grenadier_threshold,
        total_timesteps=args.timesteps,
        n_envs=args.n_envs,
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        max_drawdown_pts=args.max_drawdown_pts,
        anomaly_penalty=args.anomaly_penalty,
        eval_freq=args.eval_freq,
        device=args.device,
    )

    print("\n=== TREINO CONCLUÍDO ===")
    print(f"Best model: {history['best_model_path']}")
    stats = history.get("episode_stats", {})
    if stats:
        print(f"Win rate:   {stats.get('win_rate', 0):.1%}")
        print(f"Mean PnL:   {stats.get('mean_pnl', 0):.2f} pts")
        print(f"Profit Factor: {stats.get('profit_factor', 0):.3f}")


if __name__ == "__main__":
    main()

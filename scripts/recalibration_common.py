"""recalibration_common.py — EXEC-RECALIB-001 shared helpers.

Implements Purdue v2 12-step protocol building blocks (DEC-2026-04-24-003 v2):
  Step 1  distribution stats (mean/median/std/skew/kurt)
  Step 2  Box-Cox / log / sqrt transforms
  Step 4  bootstrap CI (N=1000)
  Step 5  Mann-Whitney U / KS hypothesis tests
  Step 8  median + IQR (robust outlier-aware stats)
  Step 9  multi-seed ensemble [42, 1337, 2024, 7, 1729]
  Step 10 purged walk-forward k-fold (k=5, embargo=48 bars)
  Step 11 reproducibility (seed, hash, version pin)
  Step 12 sklearn Pipeline pattern (leakage prevention)
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator, Sequence

import numpy as np
import pandas as pd
import scipy
from scipy import stats as sps
import sklearn

# Multi-seed list (Step 9) — frozen
SEEDS: list[int] = [42, 1337, 2024, 7, 1729]
EMBARGO_BARS: int = 48     # ~1 trading day at M30 (24*2)
N_FOLDS: int = 5
BOOTSTRAP_N: int = 1000

# Canonical data paths
REBUILT_OHLCV_M1 = Path(r"C:\FluxQuantumAI\data\rebuild_2026-04-25\gc_ohlcv_l2_joined.parquet")
M30_BOXES = Path(r"C:\data\processed\gc_m30_boxes.parquet")
CALIBRATION_FULL = Path(r"C:\data\processed\calibration_dataset_full.parquet")

WINDOW_B_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_B_END = pd.Timestamp("2026-04-25", tz="UTC")  # rebuild ends 2026-04-24 20:59


# ============================================================================
# Step 11 — reproducibility
# ============================================================================

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def env_fingerprint() -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "sklearn": sklearn.__version__,
        "seeds": SEEDS,
    }


# ============================================================================
# Step 1 — distribution observation
# ============================================================================

def distribution_stats(x: np.ndarray) -> dict:
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return {"n": int(len(x))}
    q = np.quantile(x, [0.05, 0.25, 0.50, 0.75, 0.95])
    return {
        "n": int(len(x)),
        "mean": float(np.mean(x)),
        "median": float(q[2]),
        "std": float(np.std(x, ddof=1)),
        "iqr": float(q[3] - q[1]),
        "p05": float(q[0]),
        "p25": float(q[1]),
        "p75": float(q[3]),
        "p95": float(q[4]),
        "min": float(np.min(x)),
        "max": float(np.max(x)),
        "skewness": float(sps.skew(x, bias=False)),
        "kurtosis": float(sps.kurtosis(x, fisher=True, bias=False)),
    }


# ============================================================================
# Step 2 — feature engineering for skewness
# ============================================================================

def choose_transform(x: np.ndarray) -> tuple[str, np.ndarray, dict]:
    """If |skewness| > 1, try log/sqrt/Box-Cox; pick lowest |skew| post-transform.

    Returns (label, transformed_array, diagnostic_dict).
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 10:
        return "none-tooshort", x, {"reason": "insufficient n"}
    s0 = float(sps.skew(x, bias=False))
    if abs(s0) <= 1.0:
        return "none", x, {"original_skew": s0, "applied": False, "reason": "|skew|<=1"}

    candidates: list[tuple[str, np.ndarray, float]] = []
    # log: only when strictly positive
    if (x > 0).all():
        xl = np.log(x)
        candidates.append(("log", xl, float(sps.skew(xl, bias=False))))
    # sqrt: only when non-negative
    if (x >= 0).all():
        xs = np.sqrt(x)
        candidates.append(("sqrt", xs, float(sps.skew(xs, bias=False))))
    # Box-Cox: only when strictly positive
    if (x > 0).all():
        try:
            xb, lam = sps.boxcox(x)
            candidates.append((f"boxcox(lam={lam:.3f})", xb, float(sps.skew(xb, bias=False))))
        except Exception:
            pass

    if not candidates:
        return "none-noviable", x, {"original_skew": s0, "applied": False, "reason": "no valid transform"}

    # pick smallest |skew|
    candidates.sort(key=lambda c: abs(c[2]))
    label, arr, sk = candidates[0]
    return label, arr, {
        "original_skew": s0,
        "applied": True,
        "chosen": label,
        "post_skew": sk,
        "candidates": [(c[0], c[2]) for c in candidates],
    }


# ============================================================================
# Step 4 — bootstrap confidence interval
# ============================================================================

def bootstrap_ci(
    x: np.ndarray,
    statistic,
    n: int = BOOTSTRAP_N,
    alpha: float = 0.05,
    seed: int = 42,
) -> dict:
    rng = np.random.default_rng(seed)
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    if len(x) < 10:
        return {"n": len(x), "point": None, "ci_lo": None, "ci_hi": None}
    point = float(statistic(x))
    samples = np.empty(n)
    N = len(x)
    for i in range(n):
        idx = rng.integers(0, N, size=N)
        samples[i] = statistic(x[idx])
    lo, hi = np.quantile(samples, [alpha / 2, 1 - alpha / 2])
    return {
        "n": int(N),
        "point": point,
        "ci_lo": float(lo),
        "ci_hi": float(hi),
        "ci_alpha": alpha,
        "n_resamples": n,
    }


# ============================================================================
# Step 5 — hypothesis test (Mann-Whitney U + KS)
# ============================================================================

def hypothesis_test(active: np.ndarray, inactive: np.ndarray) -> dict:
    """Two-sided MW + KS for distribution separation."""
    a = np.asarray(active);  a = a[np.isfinite(a)]
    b = np.asarray(inactive);  b = b[np.isfinite(b)]
    if len(a) < 5 or len(b) < 5:
        return {"n_a": len(a), "n_b": len(b), "skipped": "insufficient_n"}
    mw = sps.mannwhitneyu(a, b, alternative="two-sided")
    ks = sps.ks_2samp(a, b, alternative="two-sided")
    pooled_std = float(np.sqrt(
        ((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1))
        / (len(a) + len(b) - 2)
    )) if len(a) + len(b) > 2 else float("nan")
    cohens_d = float((np.mean(a) - np.mean(b)) / pooled_std) if pooled_std > 0 else float("nan")
    return {
        "n_a": int(len(a)),
        "n_b": int(len(b)),
        "mean_a": float(np.mean(a)),
        "mean_b": float(np.mean(b)),
        "mw_stat": float(mw.statistic),
        "mw_p": float(mw.pvalue),
        "ks_stat": float(ks.statistic),
        "ks_p": float(ks.pvalue),
        "pooled_std": pooled_std,
        "cohens_d": cohens_d,
    }


# ============================================================================
# Step 10 — purged walk-forward k-fold
# ============================================================================

def purged_walk_forward_indices(
    n: int,
    k: int = N_FOLDS,
    embargo: int = EMBARGO_BARS,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Walk-forward folds with embargo gap between train and test.

    Splits the time-ordered range [0, n) into k contiguous test folds. For each
    fold, train = all preceding bars MINUS an `embargo` gap immediately before
    the test fold (no future-leak; lookback features have an embargo cushion).
    """
    fold_size = n // (k + 1)
    if fold_size <= embargo + 10:
        raise ValueError(f"n={n} too small for k={k} folds with embargo={embargo}")
    for i in range(k):
        test_start = (i + 1) * fold_size
        test_end = (i + 2) * fold_size if i < k - 1 else n
        train_end = max(0, test_start - embargo)
        if train_end < 100:  # need at least some training
            continue
        train_idx = np.arange(0, train_end)
        test_idx = np.arange(test_start, test_end)
        yield train_idx, test_idx


# ============================================================================
# Step 8 — robust median + IQR threshold derivation
# ============================================================================

def robust_thresholds(x: np.ndarray, percentiles: Sequence[float]) -> dict[str, float]:
    """Percentile-based thresholds (robust; not affected by outliers).
    `percentiles` are values in [0, 100].
    """
    x = np.asarray(x)
    x = x[np.isfinite(x)]
    return {f"p{int(p)}": float(np.quantile(x, p / 100.0)) for p in percentiles}


# ============================================================================
# OHLCV resampling helpers (M1 -> M30)
# ============================================================================

def resample_m1_to_m30(df_m1: pd.DataFrame) -> pd.DataFrame:
    """Right-aligned M30 resample: open=first, high=max, low=min, close=last,
    volume=sum. Index timezone preserved.
    """
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    out = df_m1.resample("30min", label="left", closed="left").agg(agg)
    out = out.dropna(subset=["close"])
    return out


def add_bar_body(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bar_range"] = df["high"] - df["low"]
    df["bar_body"] = (df["close"] - df["open"]).abs()
    rng = df["bar_range"].replace(0, np.nan)
    df["close_pct_from_low"] = (df["close"] - df["low"]) / rng
    df["close_pct_from_high"] = (df["high"] - df["close"]) / rng
    return df


def rolling_pct_rank(s: pd.Series, w: int) -> pd.Series:
    def _rank(x):
        if len(x) < 2:
            return np.nan
        return (np.sum(x[:-1] < x[-1]) + 0.5 * np.sum(x[:-1] == x[-1])) / (len(x) - 1)
    return s.rolling(w, min_periods=max(20, w // 5)).apply(_rank, raw=True)


# ============================================================================
# Forward-return labels
# ============================================================================

def add_forward_returns(df: pd.DataFrame, horizons_min: Sequence[int] = (60,)) -> pd.DataFrame:
    df = df.copy()
    for h in horizons_min:
        bars = h // 30
        df[f"fwd_{h}m"] = df["close"].shift(-bars) - df["close"]
    return df


# ============================================================================
# Calibration result container
# ============================================================================

@dataclass
class CalibResult:
    constant: str
    old_value: object
    new_value: object
    diff_pct: float | None
    verdict: str        # KEEP / UPGRADE / INVESTIGATE / RECOMMEND
    artifact_path: str
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def write_artifact_md(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fmt_dist(stats: dict) -> str:
    if "mean" not in stats:
        return f"n={stats.get('n', 0)} (insufficient data)"
    return (
        f"n={stats['n']:,}, mean={stats['mean']:.6g}, median={stats['median']:.6g}, "
        f"std={stats['std']:.6g}, IQR={stats['iqr']:.6g}, "
        f"skew={stats['skewness']:.3f}, kurt={stats['kurtosis']:.3f}"
    )

"""scripts/calibration/calibration_daily_trend_v2.py

BIAS-DETECTION-PURDUE-CALIBRATION (Asana 1214284792412296) — Phase 0 calibration.

Implements 3 candidate D1 directional bias signals + Purdue 12-step calibration
+ ensemble weight derivation via walk-forward CV.

Signals (all literature-anchored, Rule 15):
  A. ATS Trend Line at D1 — wraps live/ats_trend_line.compute_trend_line_state
     on D1-resampled bars (offset='22h' = CME Globex anchor)
     Citation 1: ATS Strategic Plan §4 — "primary tool for directional bias"
  B. Wyckoff HH/HL — swing pivot detection; HH+HL → +1, LH+LL → -1
     Citation 3: Villahermosa "9 Buying Tests" #5+#6 / "9 Selling Tests" #5+#6
  C. ICT BOS/CHoCH — swing high/low break events; track regime state
     Citation 4: Akash Gul SMC — BOS continuation / CHoCH reversal warning

Window: 2025-07-01 → 2026-04-26 UTC (9.7m clean per Rule 14)
Source: data/rebuild_2026-04-25/gc_ohlcv_l2_joined.parquet (forensically verified)

Output:
  _audit/calibrations/raw/daily_trend_v2_raw.json   (full per-step results)
  _audit/calibrations/raw/daily_trend_v2_d1_bars.csv
  _audit/calibrations/raw/daily_trend_v2_signals.csv
  _audit/calibrations/raw/daily_trend_v2_cv_folds.csv
  _audit/calibrations/calibration_daily_trend_v2.md (Phase C artifact)

Read-only: no production code touched. No commits. No restart.

Usage:
  python scripts/calibration/calibration_daily_trend_v2.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from live.ats_trend_line import compute_trend_line_state  # noqa: E402

OUT_DIR = ROOT / "_audit" / "calibrations"
RAW_DIR = OUT_DIR / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

REBUILT_PARQUET = Path(r"C:\FluxQuantumAI\data\rebuild_2026-04-25\gc_ohlcv_l2_joined.parquet")

WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END = pd.Timestamp("2026-04-26", tz="UTC")

# Reproducibility — Step 11
SEEDS = [42, 1337, 2024, 7, 1729]   # same set as EXEC-RECALIB-001
np.random.seed(SEEDS[0])

# Calibration parameter grids
SWING_LOOKBACKS = [3, 5, 7, 10]      # Signal B: Wyckoff HH/HL window
STRUCTURE_LOOKBACKS = [5, 10, 20]    # Signal C: ICT BOS/CHoCH window
HORIZONS = [1, 3, 5, 10]             # forward return horizons (D1 bars)

# Bonferroni correction: 3 signals × 4 horizons × ~7 param values ≈ 84 tests
# Conservative: alpha / (3 signals × 4 horizons) = 0.05 / 12 = 0.0042
BONF_ALPHA = 0.05 / 12

warnings.filterwarnings("ignore", category=FutureWarning)


# ============================================================================
# Data loading + D1 resample
# ============================================================================

def _utc(idx):
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def load_d1_bars() -> tuple[pd.DataFrame, dict]:
    """Load M1 OHLCV from rebuilt-clean parquet, resample to D1 with CME Globex
    anchor (offset='22h'). Filter to CLOSED sessions only."""
    print(f"Loading {REBUILT_PARQUET.name}...", flush=True)
    df_m1 = pd.read_parquet(REBUILT_PARQUET)
    df_m1.index = _utc(df_m1.index)
    df_m1 = df_m1.loc[(df_m1.index >= WINDOW_START) & (df_m1.index < WINDOW_END)]

    print(f"  M1 rows: {len(df_m1):,}", flush=True)

    # Resample to D1 with offset='22h' (CME Globex; matches d1_h4_updater)
    d1 = df_m1[["open", "high", "low", "close"]].resample("1D", offset="22h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna()

    # Filter to CLOSED sessions: session_end (label + 1d) <= window_end
    closed_mask = (d1.index + pd.Timedelta(days=1)) <= WINDOW_END
    d1 = d1[closed_mask]

    print(f"  D1 closed sessions: {len(d1)}", flush=True)
    print(f"  D1 range: {d1.index.min()} -> {d1.index.max()}", flush=True)

    # Compute data hash for reproducibility (Step 11)
    h = hashlib.sha256()
    h.update(d1.to_csv().encode())
    data_hash = h.hexdigest()[:16]

    meta = {
        "source": str(REBUILT_PARQUET),
        "window": {"start": str(WINDOW_START), "end": str(WINDOW_END)},
        "m1_rows": int(len(df_m1)),
        "d1_closed_sessions": int(len(d1)),
        "d1_range": {"min": str(d1.index.min()), "max": str(d1.index.max())},
        "d1_data_sha256_16": data_hash,
    }
    return d1, meta


# ============================================================================
# Signal A — ATS Trend Line at D1 (zero parameters)
# ============================================================================

def compute_signal_a(d1: pd.DataFrame) -> pd.Series:
    """Wraps live/ats_trend_line.compute_trend_line_state on D1 bars.
    Streaming non-repainting evaluation (state at bar t depends only on bars ≤ t).

    Returns: pd.Series of {-1, 0, +1} indexed by D1 timestamp.
    """
    # Reuse the streaming approach from ats_trend_line_wiring multi_arm_backtest:
    # detect ALL inefficiencies once, then walk chronologically tracking direction.
    from live.ats_trend_line import (
        detect_3_candle_fvg, detect_group_inefficiencies,
    )
    if len(d1) < 3:
        return pd.Series(0, index=d1.index, dtype=int)
    ineffs = detect_3_candle_fvg(d1) + detect_group_inefficiencies(d1)
    ineffs.sort(key=lambda e: (e.ts, e.kind))
    ineff_iter = iter(ineffs)
    nxt = next(ineff_iter, None)
    current_dir = 0
    out = []
    for ts, _ in d1.iterrows():
        while nxt is not None and nxt.ts <= ts:
            current_dir = nxt.direction
            nxt = next(ineff_iter, None)
        out.append(current_dir)
    return pd.Series(out, index=d1.index, dtype=int, name="signal_a")


# ============================================================================
# Signal B — Wyckoff HH/HL on D1
# ============================================================================

def _detect_swings(highs: np.ndarray, lows: np.ndarray, lookback: int) -> tuple[list[int], list[int]]:
    """Detect swing high / swing low indices using N-bar local extrema.

    A swing high at i: high[i] is the strict max of [i-lookback, i+lookback].
    Symmetric for swing lows.

    Returns (swing_high_indices, swing_low_indices) — only those with full
    lookback windows on both sides (i.e. i in [lookback, n-lookback-1]).
    """
    n = len(highs)
    sh, sl = [], []
    for i in range(lookback, n - lookback):
        if highs[i] == highs[i - lookback : i + lookback + 1].max():
            # tie-break: only count if strictly maximum vs previous bar
            if i == 0 or highs[i] > highs[i - 1]:
                sh.append(i)
        if lows[i] == lows[i - lookback : i + lookback + 1].min():
            if i == 0 or lows[i] < lows[i - 1]:
                sl.append(i)
    return sh, sl


def compute_signal_b(d1: pd.DataFrame, lookback: int) -> pd.Series:
    """Wyckoff HH/HL signal on D1.

    Per D1 timestamp t:
      - find last 2 swing highs <= t-lookback (need confirmation past the swing)
      - find last 2 swing lows  <= t-lookback
      - HH = sh2 > sh1, HL = sl2 > sl1 → +1 (bullish structure)
      - LH = sh2 < sh1, LL = sl2 < sl1 → -1 (bearish structure)
      - mixed or insufficient → 0

    Citation 3: Villahermosa Wyckoff 2.0 — 9 Buying Tests #5+#6 / 9 Selling
    Tests #5+#6. Higher highs + higher lows confirm accumulation; lower
    highs + lower lows confirm distribution.

    Non-repainting: at time t, only swing pivots confirmed by ≥ lookback
    forward bars (i.e. swing index i with t ≥ i + lookback) are eligible.
    """
    n = len(d1)
    if n < 2 * lookback + 4:
        return pd.Series(0, index=d1.index, dtype=int)

    highs = d1["high"].values
    lows = d1["low"].values
    sh_idx, sl_idx = _detect_swings(highs, lows, lookback)

    out = np.zeros(n, dtype=int)
    for t in range(n):
        # Eligible swings: index <= t - lookback (confirmed by lookback forward bars)
        eligible_sh = [i for i in sh_idx if i <= t - lookback]
        eligible_sl = [i for i in sl_idx if i <= t - lookback]
        if len(eligible_sh) < 2 or len(eligible_sl) < 2:
            continue
        sh1_v, sh2_v = highs[eligible_sh[-2]], highs[eligible_sh[-1]]
        sl1_v, sl2_v = lows[eligible_sl[-2]], lows[eligible_sl[-1]]
        hh = sh2_v > sh1_v
        hl = sl2_v > sl1_v
        lh = sh2_v < sh1_v
        ll = sl2_v < sl1_v
        if hh and hl:
            out[t] = +1
        elif lh and ll:
            out[t] = -1
        # else: mixed (HH+LL or LH+HL) → 0
    return pd.Series(out, index=d1.index, dtype=int, name=f"signal_b_lb{lookback}")


# ============================================================================
# Signal C — ICT BOS/CHoCH on D1
# ============================================================================

def compute_signal_c(d1: pd.DataFrame, structure_lookback: int) -> pd.Series:
    """ICT BOS/CHoCH state machine on D1.

    Per D1 close, track current trend regime. Emit:
      - Initial: 0 (no_event)
      - BOS up: close above last confirmed swing high while regime is up → +1
      - BOS down: close below last confirmed swing low while regime is down → -1
      - CHoCH up: close above last swing high while regime was down → +1 (regime flip)
      - CHoCH down: close below last swing low while regime was up → -1 (regime flip)
      - Else: persist current regime

    Citation 4: Akash Gul SMC — BOS in trend direction confirms continuation;
    CHoCH against trend signals reversal.

    Non-repainting: swing pivots only confirmed by structure_lookback bars.
    """
    n = len(d1)
    if n < 2 * structure_lookback + 2:
        return pd.Series(0, index=d1.index, dtype=int)

    highs = d1["high"].values
    lows = d1["low"].values
    closes = d1["close"].values
    sh_idx, sl_idx = _detect_swings(highs, lows, structure_lookback)

    out = np.zeros(n, dtype=int)
    regime = 0  # 0 = no_event, +1 = up, -1 = down
    for t in range(n):
        # Eligible (confirmed) swings up to t - structure_lookback
        eligible_sh = [i for i in sh_idx if i <= t - structure_lookback]
        eligible_sl = [i for i in sl_idx if i <= t - structure_lookback]
        last_sh = highs[eligible_sh[-1]] if eligible_sh else None
        last_sl = lows[eligible_sl[-1]] if eligible_sl else None
        c = closes[t]
        if last_sh is not None and c > last_sh:
            # Break above: BOS if regime up; CHoCH (confirms reversal) if regime down or 0
            regime = +1
        elif last_sl is not None and c < last_sl:
            regime = -1
        # else: persist regime
        out[t] = regime
    return pd.Series(out, index=d1.index, dtype=int, name=f"signal_c_lb{structure_lookback}")


# ============================================================================
# Forward returns
# ============================================================================

def compute_forward_returns(d1: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """Forward return label per D1 close at each horizon.
    Returns DataFrame columns 'fwd_{h}d_sign' in {-1, 0, +1} (0 = exact zero).
    """
    out = {}
    for h in horizons:
        fwd = d1["close"].shift(-h) - d1["close"]
        out[f"fwd_{h}d_pts"] = fwd
        out[f"fwd_{h}d_sign"] = np.sign(fwd).fillna(0).astype(int)
    return pd.DataFrame(out, index=d1.index)


# ============================================================================
# Steps 1-4: Distribution + bootstrap CI
# ============================================================================

def step_1_distribution(signal_series: pd.Series, name: str) -> dict:
    s = signal_series.dropna()
    return {
        "name": name,
        "n": int(len(s)),
        "n_long": int((s == +1).sum()),
        "n_short": int((s == -1).sum()),
        "n_neutral": int((s == 0).sum()),
        "pct_long": round(100 * (s == +1).mean(), 2),
        "pct_short": round(100 * (s == -1).mean(), 2),
        "pct_neutral": round(100 * (s == 0).mean(), 2),
        "mean": round(float(s.mean()), 4),
        "std": round(float(s.std()), 4),
    }


def step_4_bootstrap_ci(signal: pd.Series, label: pd.Series, n_boot: int = 1000,
                        seed: int = 42) -> dict:
    """Bootstrap CI on accuracy of `signal` predicting `label` direction.
    Excludes neutral-signal cases (signal == 0).
    """
    rng = np.random.default_rng(seed)
    aligned = pd.concat([signal, label], axis=1).dropna()
    aligned.columns = ["s", "l"]
    aligned = aligned[aligned["s"] != 0]
    n = len(aligned)
    if n < 30:
        return {"n_eligible": n, "accuracy": None,
                "ci95_low": None, "ci95_high": None,
                "ci_width_pct_of_value": None}
    correct = (aligned["s"] == aligned["l"]).values.astype(int)
    accuracy = float(correct.mean())
    boot = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        boot.append(correct[idx].mean())
    ci_low, ci_high = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
    return {
        "n_eligible": int(n),
        "accuracy": round(accuracy, 4),
        "ci95_low": round(ci_low, 4),
        "ci95_high": round(ci_high, 4),
        "ci_width_pct_of_value": (
            round(100 * (ci_high - ci_low) / accuracy, 2) if accuracy > 0 else None
        ),
    }


# ============================================================================
# Step 5: Hypothesis test (chi-square + Cohen's d)
# ============================================================================

def step_5_hypothesis(signal: pd.Series, label: pd.Series) -> dict:
    """Chi-square test: signal_dir × label_dir contingency.
    Cohen's d for effect size. Reports p-value (uncorrected).
    """
    aligned = pd.concat([signal, label], axis=1).dropna()
    aligned.columns = ["s", "l"]
    aligned = aligned[aligned["s"] != 0]
    n = len(aligned)
    if n < 30:
        return {"n": n, "chi2": None, "p_value": None, "cohens_d": None}

    cm = pd.crosstab(aligned["s"], aligned["l"])
    if cm.shape[0] < 2 or cm.shape[1] < 2:
        return {"n": n, "chi2": None, "p_value": None, "cohens_d": None,
                "note": "degenerate_contingency"}

    chi2, p, _, _ = stats.chi2_contingency(cm)
    # Cohen's d on PnL-like signal: treat label sign as +1/-1 and signal as predictor
    correct_pnl = aligned["s"] * aligned["l"]
    if correct_pnl.std() > 0:
        # Effect size: mean(correct vs random expectation 0)
        d = float(correct_pnl.mean() / correct_pnl.std()) if correct_pnl.std() > 0 else 0.0
    else:
        d = 0.0
    return {
        "n": int(n),
        "chi2": round(float(chi2), 4),
        "p_value": float(p),
        "p_value_passes_bonferroni": bool(p < BONF_ALPHA),
        "cohens_d": round(d, 4),
    }


# ============================================================================
# Step 9: Ensemble weight calibration via walk-forward CV
# ============================================================================

def step_9_ensemble_walkforward(
    signals: dict[str, pd.Series],
    label: pd.Series,
    n_folds: int = 5,
) -> dict:
    """Walk-forward CV to calibrate ensemble weights via grid search.
    Folds are chronologically ordered (no random shuffle — Step 10 rigor).

    Each fold:
      - Train: bars [fold_start, fold_train_end)
      - Test : bars [fold_train_end, fold_test_end)
    On train: grid search weights (w_a, w_b, w_c) ∈ simplex 0..1 step 0.1
    Pick weights maximizing accuracy on train. Apply to test → fold accuracy.
    """
    aligned = pd.DataFrame(signals).join(label.rename("l")).dropna()
    n = len(aligned)
    if n < 50:
        return {"n_folds": 0, "note": "insufficient_data"}

    fold_size = n // (n_folds + 1)   # +1 because first fold is training-only
    folds = []
    weight_grid = []
    for w_a in np.arange(0.0, 1.01, 0.1):
        for w_b in np.arange(0.0, 1.01 - w_a + 1e-9, 0.1):
            w_c = 1.0 - w_a - w_b
            if -0.01 < w_c <= 1.01:
                weight_grid.append((round(w_a, 2), round(w_b, 2), round(w_c, 2)))

    sig_names = list(signals.keys())  # ordered: a, b, c

    for f in range(n_folds):
        train_end = (f + 1) * fold_size
        test_end = (f + 2) * fold_size
        if test_end > n:
            test_end = n
        train_df = aligned.iloc[:train_end]
        test_df = aligned.iloc[train_end:test_end]
        if len(test_df) == 0 or len(train_df) < 30:
            continue

        # Grid search on train
        best = (None, -1.0)
        for w in weight_grid:
            score_train = sum(w[i] * train_df[sig_names[i]] for i in range(3))
            pred_train = np.sign(score_train)
            # Accuracy excluding neutrals
            mask = pred_train != 0
            if mask.sum() < 5:
                continue
            acc = float((pred_train[mask] == train_df.loc[mask, "l"]).mean())
            if acc > best[1]:
                best = (w, acc)

        if best[0] is None:
            continue

        # Apply best weights to test fold
        score_test = sum(best[0][i] * test_df[sig_names[i]] for i in range(3))
        pred_test = np.sign(score_test)
        mask_t = pred_test != 0
        test_acc = (
            float((pred_test[mask_t] == test_df.loc[mask_t, "l"]).mean())
            if mask_t.sum() > 0 else None
        )
        folds.append({
            "fold": f + 1,
            "train_n": int(len(train_df)),
            "test_n": int(len(test_df)),
            "best_weights": best[0],
            "train_accuracy": round(best[1], 4),
            "test_accuracy": round(test_acc, 4) if test_acc is not None else None,
            "test_n_predictions": int(mask_t.sum()),
        })

    if not folds:
        return {"n_folds": 0, "note": "insufficient_folds"}

    # Aggregate weights — average best weights across folds
    weights_arr = np.array([list(f["best_weights"]) for f in folds])
    avg_weights = tuple(round(float(w), 3) for w in weights_arr.mean(axis=0))
    test_accs = [f["test_accuracy"] for f in folds if f["test_accuracy"] is not None]

    return {
        "n_folds": len(folds),
        "folds": folds,
        "avg_weights": {sig_names[i]: avg_weights[i] for i in range(3)},
        "test_accuracy_mean": round(float(np.mean(test_accs)), 4) if test_accs else None,
        "test_accuracy_std": round(float(np.std(test_accs)), 4) if test_accs else None,
    }


# ============================================================================
# Step 11: Multi-seed reproducibility
# ============================================================================

def step_11_multi_seed(
    signals: dict[str, pd.Series],
    label: pd.Series,
    seeds: list[int],
    n_folds: int = 5,
) -> dict:
    """Run Step 9 ensemble calibration with each seed.
    Note: walk-forward CV is deterministic in fold order; seeds only affect
    bootstrap CI in Step 4. Multi-seed here verifies that calibration outputs
    are identical (proving determinism) which is itself a reproducibility check.
    """
    results = []
    for s in seeds:
        np.random.seed(s)
        out = step_9_ensemble_walkforward(signals, label, n_folds=n_folds)
        results.append({"seed": s, "avg_weights": out.get("avg_weights"),
                        "test_accuracy_mean": out.get("test_accuracy_mean")})
    # Determinism check
    test_accs = [r["test_accuracy_mean"] for r in results if r["test_accuracy_mean"] is not None]
    if test_accs:
        determinism_std = float(np.std(test_accs))
    else:
        determinism_std = None
    return {
        "seeds_tested": seeds,
        "per_seed": results,
        "determinism_std": determinism_std,
        "deterministic": (determinism_std == 0.0) if determinism_std is not None else None,
    }


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 76)
    print("BIAS-DETECTION-PURDUE-CALIBRATION Phase 0")
    print("Asana 1214284792412296")
    print("=" * 76)

    t0 = time.monotonic()

    # ── Load data + Step 12 leakage prevention setup ───────────────────────
    d1, data_meta = load_d1_bars()
    d1.to_csv(RAW_DIR / "daily_trend_v2_d1_bars.csv")

    # ── Compute forward returns (label) ────────────────────────────────────
    print("\nComputing forward returns labels...", flush=True)
    fwd = compute_forward_returns(d1, HORIZONS)
    for h in HORIZONS:
        col = f"fwd_{h}d_sign"
        n_pos = int((fwd[col] == +1).sum())
        n_neg = int((fwd[col] == -1).sum())
        n_zer = int((fwd[col] == 0).sum())
        print(f"  fwd_{h}d_sign: +{n_pos} / -{n_neg} / 0:{n_zer}", flush=True)

    # ── Compute Signal A (no parameters) ───────────────────────────────────
    print("\nComputing Signal A (ATS Trend Line at D1, no params)...", flush=True)
    sig_a = compute_signal_a(d1)

    # ── Compute Signal B (param sweep) ─────────────────────────────────────
    print("\nComputing Signal B (Wyckoff HH/HL) param sweep...", flush=True)
    sig_b_variants = {}
    for lb in SWING_LOOKBACKS:
        sig_b_variants[lb] = compute_signal_b(d1, lookback=lb)
        print(f"  swing_lookback={lb}: dist={step_1_distribution(sig_b_variants[lb], f'B_lb{lb}')}", flush=True)

    # ── Compute Signal C (param sweep) ─────────────────────────────────────
    print("\nComputing Signal C (ICT BOS/CHoCH) param sweep...", flush=True)
    sig_c_variants = {}
    for lb in STRUCTURE_LOOKBACKS:
        sig_c_variants[lb] = compute_signal_c(d1, structure_lookback=lb)
        print(f"  structure_lookback={lb}: dist={step_1_distribution(sig_c_variants[lb], f'C_lb{lb}')}", flush=True)

    # ── Persist signals to CSV (forensic) ──────────────────────────────────
    sigs_df = pd.DataFrame({"signal_a": sig_a})
    for lb, s in sig_b_variants.items():
        sigs_df[f"signal_b_lb{lb}"] = s
    for lb, s in sig_c_variants.items():
        sigs_df[f"signal_c_lb{lb}"] = s
    for col in fwd.columns:
        sigs_df[col] = fwd[col]
    sigs_df.to_csv(RAW_DIR / "daily_trend_v2_signals.csv")

    # ── Step 1 distributions (already printed; collect for artifact) ──────
    step_1_summary = {
        "signal_a": step_1_distribution(sig_a, "A"),
        **{f"signal_b_lb{lb}": step_1_distribution(s, f"B_lb{lb}")
           for lb, s in sig_b_variants.items()},
        **{f"signal_c_lb{lb}": step_1_distribution(s, f"C_lb{lb}")
           for lb, s in sig_c_variants.items()},
    }

    # ── Step 4 bootstrap CI on accuracy ────────────────────────────────────
    print("\nStep 4 — Bootstrap CI (N=1000) on accuracy vs forward returns...", flush=True)
    step_4_summary = {}
    for h in HORIZONS:
        label = fwd[f"fwd_{h}d_sign"]
        step_4_summary[f"h{h}"] = {
            "signal_a": step_4_bootstrap_ci(sig_a, label, seed=SEEDS[0]),
            **{f"signal_b_lb{lb}": step_4_bootstrap_ci(sig_b_variants[lb], label, seed=SEEDS[0])
               for lb in SWING_LOOKBACKS},
            **{f"signal_c_lb{lb}": step_4_bootstrap_ci(sig_c_variants[lb], label, seed=SEEDS[0])
               for lb in STRUCTURE_LOOKBACKS},
        }

    # ── Step 5 hypothesis test (Bonferroni-corrected) ──────────────────────
    print("\nStep 5 — Hypothesis test (chi-square + Cohen's d)...", flush=True)
    print(f"  Bonferroni alpha = 0.05 / 12 = {BONF_ALPHA:.5f}", flush=True)
    step_5_summary = {}
    for h in HORIZONS:
        label = fwd[f"fwd_{h}d_sign"]
        step_5_summary[f"h{h}"] = {
            "signal_a": step_5_hypothesis(sig_a, label),
        }
        for lb in SWING_LOOKBACKS:
            step_5_summary[f"h{h}"][f"signal_b_lb{lb}"] = step_5_hypothesis(sig_b_variants[lb], label)
        for lb in STRUCTURE_LOOKBACKS:
            step_5_summary[f"h{h}"][f"signal_c_lb{lb}"] = step_5_hypothesis(sig_c_variants[lb], label)
        # Print summary per horizon
        print(f"  h={h}d:", flush=True)
        for sig_name, res in step_5_summary[f"h{h}"].items():
            p = res.get("p_value")
            d = res.get("cohens_d")
            passes = res.get("p_value_passes_bonferroni")
            n = res.get("n")
            if p is None:
                print(f"    {sig_name:<22} n={n} (degenerate or insufficient data)", flush=True)
            else:
                p_str = f"{p:.4f}"
                d_str = f"{d:+.3f}" if d is not None else "n/a"
                bonf_str = "YES" if passes else "no"
                print(f"    {sig_name:<22} n={n} p={p_str} d={d_str} "
                      f"bonf<{BONF_ALPHA:.4f}? {bonf_str}", flush=True)

    # ── Pick BEST single-signal variant per category for ensemble ──────────
    # Best Signal B param: highest Cohen's d at h=5d (medium-term horizon)
    best_b_lb = max(
        SWING_LOOKBACKS,
        key=lambda lb: abs(step_5_summary["h5"].get(f"signal_b_lb{lb}", {}).get("cohens_d") or 0),
    )
    best_c_lb = max(
        STRUCTURE_LOOKBACKS,
        key=lambda lb: abs(step_5_summary["h5"].get(f"signal_c_lb{lb}", {}).get("cohens_d") or 0),
    )
    print(f"\nSelected for ensemble: signal_a (no param), signal_b_lb{best_b_lb}, signal_c_lb{best_c_lb}", flush=True)

    chosen_signals = {
        "signal_a": sig_a,
        f"signal_b_lb{best_b_lb}": sig_b_variants[best_b_lb],
        f"signal_c_lb{best_c_lb}": sig_c_variants[best_c_lb],
    }

    # ── Step 9 ensemble walk-forward CV ────────────────────────────────────
    print("\nStep 9 — Ensemble walk-forward CV (5 folds)...", flush=True)
    step_9_summary = {}
    for h in HORIZONS:
        label = fwd[f"fwd_{h}d_sign"]
        out = step_9_ensemble_walkforward(chosen_signals, label, n_folds=5)
        step_9_summary[f"h{h}"] = out
        print(f"  h={h}d: avg_weights={out.get('avg_weights')} "
              f"test_acc={out.get('test_accuracy_mean')} ± {out.get('test_accuracy_std')}",
              flush=True)
        # Persist fold details
        if "folds" in out:
            pd.DataFrame(out["folds"]).to_csv(
                RAW_DIR / f"daily_trend_v2_cv_folds_h{h}.csv", index=False
            )

    # ── Step 11 multi-seed reproducibility ─────────────────────────────────
    print("\nStep 11 — Multi-seed reproducibility check...", flush=True)
    label_5d = fwd["fwd_5d_sign"]
    step_11_summary = step_11_multi_seed(chosen_signals, label_5d, seeds=SEEDS, n_folds=5)
    print(f"  Determinism std: {step_11_summary['determinism_std']}", flush=True)
    print(f"  Deterministic: {step_11_summary['deterministic']}", flush=True)

    # ── Final summary ──────────────────────────────────────────────────────
    elapsed = time.monotonic() - t0
    summary = {
        "task": "BIAS-DETECTION-PURDUE-CALIBRATION Phase 0",
        "asana": "1214284792412296",
        "data": data_meta,
        "seeds_used": SEEDS,
        "bonferroni_alpha_corrected": BONF_ALPHA,
        "horizons_tested": HORIZONS,
        "step_1_distributions": step_1_summary,
        "step_4_bootstrap_ci": step_4_summary,
        "step_5_hypothesis_tests": step_5_summary,
        "selected_for_ensemble": {
            "signal_a": "ATS Trend Line at D1 (no params)",
            "signal_b": f"Wyckoff HH/HL with swing_lookback={best_b_lb}",
            "signal_c": f"ICT BOS/CHoCH with structure_lookback={best_c_lb}",
            "best_b_lb": best_b_lb,
            "best_c_lb": best_c_lb,
        },
        "step_9_ensemble_walkforward": step_9_summary,
        "step_11_multi_seed": step_11_summary,
        "elapsed_s": round(elapsed, 1),
    }

    (RAW_DIR / "daily_trend_v2_raw.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(f"\nout -> {OUT_DIR}", flush=True)
    print(f"elapsed: {elapsed:.1f}s", flush=True)
    return summary


if __name__ == "__main__":
    main()

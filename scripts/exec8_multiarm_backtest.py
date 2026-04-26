"""exec8_multiarm_backtest.py — EXEC-8 multi-arm decision-gate backtest.

Read-only. Runs 5 arm configurations on the rebuilt-clean parquet over
the 3-month window 2026-01-25 → 2026-04-25 and produces:

  - per-arm entries CSV
  - per-arm PnL CSV (per-trade points + cumulative)
  - per-arm metric summary
  - cross-arm comparison table
  - methodology compliance audit (config-agnostic, run once)

NO production code changes. NO config edits. NO commits in this script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.recalibration_common import (
    REBUILT_OHLCV_M1, M30_BOXES, CALIBRATION_FULL,
    sha256_file, env_fingerprint,
    resample_m1_to_m30, add_bar_body, rolling_pct_rank, add_forward_returns,
)

# Output dir set by caller
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\FluxQuantumAI\_audit\backtest\exec8_multiarm_TBD")
OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "raw").mkdir(parents=True, exist_ok=True)

WINDOW_START = pd.Timestamp("2026-01-25", tz="UTC")
WINDOW_END = pd.Timestamp("2026-04-26", tz="UTC")  # inclusive of 2026-04-25

# Feature constants (used for feature definitions, not under test in EXEC-8)
SOT_N = 3
EFR_ROLLING = 100
EFR_DIVERGENCE_THRESHOLD = 0.3
CLOSE_PCT_WEAK = 0.3

OLD_WEIGHTS = {"F5_B": 0.640, "F3_B": 0.543, "F5_A": 0.394, "F2_B": 0.274, "F1_B": 0.213}
NEW_WEIGHTS = {"F5_B": 0.176, "F3_B": 0.220, "F5_A": 0.189, "F2_B": 0.278, "F1_B": 0.196}
ABLATION_WEIGHTS = {"F5_B": 0.0, "F3_B": 0.0, "F5_A": 0.0,
                    "F2_B": 0.274, "F1_B": 0.213}

ARMS = {
    "A_old_040": {"weights": OLD_WEIGHTS, "threshold": 0.40,
                  "label": "OLD weights @ 0.40 (current production)"},
    "B_old_025": {"weights": OLD_WEIGHTS, "threshold": 0.25,
                  "label": "OLD weights @ 0.25 (predicted winner)"},
    "C_new_040": {"weights": NEW_WEIGHTS, "threshold": 0.40,
                  "label": "NEW weights @ 0.40"},
    "D_new_025": {"weights": NEW_WEIGHTS, "threshold": 0.25,
                  "label": "NEW weights @ 0.25"},
    "ablation":  {"weights": ABLATION_WEIGHTS, "threshold": 0.25,
                  "label": "F1+F2 only @ 0.25 (F3/F5/F5_A=0)"},
}


def _utc(idx):
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def build_features() -> tuple[pd.DataFrame, dict]:
    """Build the M30 feature dataframe for the 3-month backtest window."""
    print("loading rebuilt M1 OHLCV...", flush=True)
    df_m1 = pd.read_parquet(REBUILT_OHLCV_M1)
    df_m1.index = _utc(df_m1.index)
    df_m1 = df_m1.loc[(df_m1.index >= WINDOW_START) & (df_m1.index < WINDOW_END)]
    df = resample_m1_to_m30(df_m1)
    df = add_bar_body(df)
    df = add_forward_returns(df, horizons_min=(60,))

    print("loading m30 boxes for regime...", flush=True)
    boxes = pd.read_parquet(M30_BOXES, columns=["m30_box_confirmed", "at_struct_level"])
    boxes.index = _utc(boxes.index)
    df = df.join(boxes, how="left")
    df["m30_box_confirmed"] = df["m30_box_confirmed"].fillna(False).astype(bool)
    df["at_struct_level"] = df["at_struct_level"].fillna(False).astype(bool)

    print("loading L2 delta...", flush=True)
    delta_df = pd.read_parquet(CALIBRATION_FULL, columns=["l2_bar_delta"])
    delta_df.index = _utc(delta_df.index)
    delta_df = delta_df.loc[(delta_df.index >= WINDOW_START) & (delta_df.index < WINDOW_END)]
    m30_delta = delta_df["l2_bar_delta"].dropna().resample(
        "30min", label="left", closed="left").sum()
    df["m30_bar_delta"] = df.index.map(m30_delta).astype(float)

    cd = np.sign(df["close"].diff()).fillna(0).astype(int)
    up2 = (cd == 1) & (cd.shift(1) == 1)
    dn2 = (cd == -1) & (cd.shift(1) == -1)
    df["close_direction"] = cd
    df["trend_a"] = (up2 | dn2).fillna(False)
    df["trend_a_dir"] = np.where(up2, 1, np.where(dn2, -1, 0)).astype(int)
    df["trend_b"] = df["m30_box_confirmed"] & df["at_struct_level"]
    df["trend_b_dir"] = np.sign(df["close"] - df["close"].shift(SOT_N - 1)).fillna(0).astype(int)
    df["anti_a_60m"] = -df["trend_a_dir"] * df["fwd_60m"]
    df["anti_b_60m"] = -df["trend_b_dir"] * df["fwd_60m"]

    rng = df["bar_range"]
    df["sot_decrement"] = (rng < rng.shift(1)) & (rng.shift(1) < rng.shift(2))
    df["F1_B"] = (df["sot_decrement"] & df["trend_b"]).fillna(False)

    vol_pct = rolling_pct_rank(df["volume"], EFR_ROLLING)
    body_pct = rolling_pct_rank(df["bar_body"], EFR_ROLLING)
    df["efr_div"] = vol_pct - body_pct
    df["efr_signal"] = df["efr_div"] > EFR_DIVERGENCE_THRESHOLD
    df["F2_B"] = (df["efr_signal"] & df["trend_b"]).fillna(False)

    delta = df["m30_bar_delta"]
    bearish = df["close"] < df["open"]
    bullish = df["close"] > df["open"]
    df["delta_div"] = ((delta > 0) & bearish) | ((delta < 0) & bullish)
    df["F3_B"] = (df["delta_div"] & df["trend_b"]).fillna(False)

    df["weak_low"] = df["close_pct_from_low"] < CLOSE_PCT_WEAK
    df["weak_high"] = df["close_pct_from_high"] < CLOSE_PCT_WEAK
    f5_long_a = (df["trend_a_dir"] == 1) & df["weak_low"]
    f5_short_a = (df["trend_a_dir"] == -1) & df["weak_high"]
    df["F5_A"] = (df["trend_a"] & (f5_long_a | f5_short_a)).fillna(False)
    f5_long_b = (df["trend_b_dir"] == 1) & df["weak_low"]
    f5_short_b = (df["trend_b_dir"] == -1) & df["weak_high"]
    df["F5_B"] = (df["trend_b"] & (f5_long_b | f5_short_b)).fillna(False)

    meta = {
        "window_start": str(WINDOW_START),
        "window_end": str(WINDOW_END),
        "rebuilt_sha256": sha256_file(REBUILT_OHLCV_M1),
        "boxes_sha256": sha256_file(M30_BOXES),
        "calibration_full_sha256": sha256_file(CALIBRATION_FULL),
        "m1_rows": int(df_m1.shape[0]),
        "m30_rows": int(df.shape[0]),
        "trend_a_n": int(df["trend_a"].sum()),
        "trend_b_n": int(df["trend_b"].sum()),
        "feature_n_active": {f: int(df[f].sum()) for f in
                             ["F1_B", "F2_B", "F3_B", "F5_A", "F5_B"]},
        "label_coverage": {
            "anti_a_60m_n": int(df["anti_a_60m"].notna().sum()),
            "anti_b_60m_n": int(df["anti_b_60m"].notna().sum()),
        },
    }
    return df, meta


def run_arm(df: pd.DataFrame, arm_name: str, weights: dict, threshold: float) -> dict:
    """Compute LOGIC-C score per bar, take entries where score > threshold AND
    a trend is active. Direction = anti-trend. PnL = direction × (close[t+2] - close[t])."""
    score = (weights.get("F5_B", 0.0) * df["F5_B"].astype(float)
             + weights.get("F3_B", 0.0) * df["F3_B"].astype(float)
             + weights.get("F5_A", 0.0) * df["F5_A"].astype(float)
             + weights.get("F2_B", 0.0) * df["F2_B"].astype(float)
             + weights.get("F1_B", 0.0) * df["F1_B"].astype(float))

    df_a = df.copy()
    df_a["logic_c_score"] = score

    # Eligibility: any trend active + label valid + score > threshold
    elig_mask = ((df_a["trend_a"] | df_a["trend_b"])
                 & df_a["logic_c_score"] > threshold)
    # Build entry table
    df_a["entry_dir"] = np.where(df_a["trend_b"], -df_a["trend_b_dir"],
                                 np.where(df_a["trend_a"], -df_a["trend_a_dir"], 0))
    # Forward 60m PnL = entry_dir × fwd_60m
    df_a["pnl_60m"] = df_a["entry_dir"] * df_a["fwd_60m"]

    entries_mask = ((df_a["trend_a"] | df_a["trend_b"])
                    & (df_a["logic_c_score"] > threshold)
                    & df_a["pnl_60m"].notna()
                    & (df_a["entry_dir"] != 0))
    entries = df_a.loc[entries_mask, [
        "open", "high", "low", "close", "volume",
        "trend_a", "trend_b", "trend_a_dir", "trend_b_dir",
        "logic_c_score", "entry_dir", "pnl_60m",
        "F1_B", "F2_B", "F3_B", "F5_A", "F5_B",
    ]].copy()
    entries["arm"] = arm_name

    if len(entries) == 0:
        metrics = {
            "arm": arm_name, "n_entries": 0, "win_rate": None,
            "expectancy": None, "total_pts": 0.0, "max_dd_pts": 0.0,
            "sharpe": None, "n_long": 0, "n_short": 0,
        }
        return {"entries": entries, "metrics": metrics}

    pnl = entries["pnl_60m"].values.astype(float)
    n = len(pnl)
    n_long = int((entries["entry_dir"] == 1).sum())
    n_short = int((entries["entry_dir"] == -1).sum())
    win_rate = float(np.mean(pnl > 0))
    expectancy = float(np.mean(pnl))
    total_pts = float(np.sum(pnl))
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(np.max(dd)) if len(dd) else 0.0
    # Sharpe: mean / std (per-trade scale; not annualised — comparable across arms)
    sharpe = float(expectancy / np.std(pnl, ddof=1)) if np.std(pnl, ddof=1) > 0 else None
    metrics = {
        "arm": arm_name,
        "n_entries": n,
        "n_long": n_long,
        "n_short": n_short,
        "win_rate": round(win_rate, 4),
        "expectancy": round(expectancy, 4),
        "total_pts": round(total_pts, 4),
        "max_dd_pts": round(max_dd, 4),
        "sharpe_per_trade": round(sharpe, 4) if sharpe is not None else None,
        "mean_pos_pnl": round(float(np.mean(pnl[pnl > 0])), 4) if (pnl > 0).any() else None,
        "mean_neg_pnl": round(float(np.mean(pnl[pnl < 0])), 4) if (pnl < 0).any() else None,
    }
    print(f"  {arm_name:>11}  n={n:>4}  win={win_rate:.3f}  "
          f"exp={expectancy:.3f}  total={total_pts:.1f}  "
          f"DD={max_dd:.1f}  Sharpe={sharpe if sharpe is None else round(sharpe,3)}",
          flush=True)
    return {"entries": entries, "metrics": metrics}


def methodology_compliance(df: pd.DataFrame) -> dict:
    """Config-agnostic methodology-compliance audit (run once)."""
    n_boxes = int(df["m30_box_confirmed"].sum())
    n_at_struct = int(df["at_struct_level"].sum())
    n_trend_b = int(df["trend_b"].sum())
    n_trend_a = int(df["trend_a"].sum())
    label_a_cov = float(df["anti_a_60m"].notna().mean())
    label_b_cov = float(df["anti_b_60m"].notna().mean())
    # Bar-shape sanity
    bar_range_zero = int((df["bar_range"] == 0).sum())
    body_le_range = int((df["bar_body"] <= df["bar_range"]).sum())
    # Forward return distribution (sanity)
    fwd_stats = {
        "mean_60m": float(df["fwd_60m"].mean(skipna=True)),
        "std_60m": float(df["fwd_60m"].std(skipna=True)),
    }
    return {
        "n_m30_bars": int(df.shape[0]),
        "n_m30_box_confirmed": n_boxes,
        "n_at_struct_level": n_at_struct,
        "n_trend_b": n_trend_b,
        "n_trend_a": n_trend_a,
        "label_anti_a_60m_coverage_pct": round(label_a_cov * 100, 2),
        "label_anti_b_60m_coverage_pct": round(label_b_cov * 100, 2),
        "bar_range_zero_count": bar_range_zero,
        "body_le_range_count": body_le_range,
        "fwd_60m_stats": fwd_stats,
        "notes": (
            "Trend Line dots / Liquidity Lines / Pullback recognition are "
            "produced by upstream detection pipelines (e.g. ATS Trend Line "
            "Part A, HEAD 8df5db4) and are not exercised by the LOGIC-C "
            "entry rule under test. They are CONFIG-AGNOSTIC and identical "
            "across all 5 arms. Box/struct counts above provide a sanity "
            "check on the regime-gating layer that LOGIC-C consumes."
        ),
    }


def main():
    print("=== EXEC-8 multi-arm backtest ===", flush=True)
    df, meta = build_features()
    print(f"  M30 rows: {meta['m30_rows']} ({meta['window_start']} -> "
          f"{meta['window_end']})", flush=True)
    print(f"  TREND-A: {meta['trend_a_n']}, TREND-B: {meta['trend_b_n']}", flush=True)
    print(f"  N_active features: {meta['feature_n_active']}", flush=True)

    print("\n--- per-arm runs ---", flush=True)
    arm_results = {}
    metric_rows = []
    for arm_name, cfg in ARMS.items():
        out = run_arm(df, arm_name, cfg["weights"], cfg["threshold"])
        arm_results[arm_name] = out
        metric_rows.append(out["metrics"])
        # Persist per-arm entries CSV
        entries_path = OUT_DIR / f"entries_{arm_name}.csv"
        out["entries"].to_csv(entries_path)

    # Cross-arm metric table
    metrics_df = pd.DataFrame(metric_rows)
    metrics_df.to_csv(OUT_DIR / "metrics_per_arm.csv", index=False)

    # Methodology compliance (config-agnostic)
    print("\n--- methodology compliance (config-agnostic) ---", flush=True)
    mc = methodology_compliance(df)
    for k, v in mc.items():
        if k != "notes":
            print(f"   {k}: {v}", flush=True)

    out_blob = {
        "env": env_fingerprint(),
        "feature_meta": meta,
        "arms": {k: v["metrics"] for k, v in arm_results.items()},
        "methodology_compliance": mc,
    }
    (OUT_DIR / "raw" / "summary.json").write_text(
        json.dumps(out_blob, indent=2, default=float), encoding="utf-8")
    print(f"\nout -> {OUT_DIR}", flush=True)
    return out_blob


if __name__ == "__main__":
    main()

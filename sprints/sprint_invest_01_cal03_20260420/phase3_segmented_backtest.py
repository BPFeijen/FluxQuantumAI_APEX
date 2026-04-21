"""INVEST-01 Phase 3 — Segmented Backtest of inverted_fix hypothesis.

For each M1 bar where rolling_delta_4h < -1050 (seller exhaustion → LONG hypothesis)
OR rolling_delta_4h > +3000 (buyer exhaustion → SHORT hypothesis):
compute forward returns at 30m / 60m / 4h / 24h horizons;
segment by regime × walk-forward sub-period;
bootstrap CI + Cohen's d per cell.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SPRINT_DIR = Path("C:/FluxQuantumAI/sprints/sprint_invest_01_cal03_20260420")
OHLCV_PATH = Path("C:/data/processed/gc_ohlcv_l2_joined.parquet")
CAL_WITH_REGIME = SPRINT_DIR / "phase2_cal_with_regime.parquet"

HORIZONS = {"30m": 30, "60m": 60, "4h": 240, "24h": 1440}

# Walk-forward sub-periods (prompt §4.4)
SUBPERIODS = {
    "sprint8_training_2025_Jul_Nov": ("2025-07-01", "2025-11-30"),
    "post_deploy_2025_Dec_2026_Mar": ("2025-12-01", "2026-03-31"),
    "recent_2026_Apr": ("2026-04-01", "2026-04-07"),  # cal_dataset stale 04-07
}

# Extreme definitions (+/- sides, both 800 original finding and 3000/-1050 deployed)
EXTREMES = {
    "seller_exh_lt_-1050": ("rolling_delta_4h", "<", -1050, "LONG"),     # inverted_fix: SUPPORTS LONG +2
    "buyer_exh_gt_+3000":  ("rolling_delta_4h", ">",  3000, "SHORT"),    # inverted_fix: SUPPORTS SHORT +2
    "seller_exh_lt_-800_original":  ("rolling_delta_4h", "<",  -800, "LONG"),
    "buyer_exh_gt_+800_original":   ("rolling_delta_4h", ">",   800, "SHORT"),
}


def bootstrap_ci(values: np.ndarray, n_boot: int = 1000, alpha: float = 0.05, rng=None) -> tuple[float, float]:
    if rng is None:
        rng = np.random.default_rng(42)
    if len(values) < 5:
        return (np.nan, np.nan)
    n = len(values)
    means = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        means[i] = values[idx].mean()
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return (lo, hi)


def cohens_d(values: np.ndarray, baseline: np.ndarray) -> float:
    if len(values) < 5 or len(baseline) < 5:
        return float("nan")
    m1, m2 = values.mean(), baseline.mean()
    s1, s2 = values.std(ddof=1), baseline.std(ddof=1)
    n1, n2 = len(values), len(baseline)
    pooled = np.sqrt(((n1 - 1) * s1 ** 2 + (n2 - 1) * s2 ** 2) / (n1 + n2 - 2))
    if pooled == 0:
        return float("nan")
    return float((m1 - m2) / pooled)


def fwd_return(close: pd.Series, horizon_min: int, direction: str) -> pd.Series:
    """Forward return in POINTS (not pct) at horizon.
    For LONG hypothesis: fwd_return > 0 = bullish_reversal_validated.
    For SHORT hypothesis: fwd_return < 0 = bearish_reversal_validated.
    """
    fwd = close.shift(-horizon_min) - close
    return fwd


def main() -> None:
    print("=" * 70)
    print("INVEST-01 PHASE 3 — SEGMENTED BACKTEST")
    print("=" * 70)

    # --- Load regime-labelled M1 ---
    print(f"\nLoading {CAL_WITH_REGIME} ...")
    cal = pd.read_parquet(CAL_WITH_REGIME)
    cal["ts"] = pd.to_datetime(cal["ts"], utc=True)
    print(f"  rows={len(cal):,}  range={cal['ts'].min()} → {cal['ts'].max()}")

    # --- Load OHLCV for fwd returns ---
    print(f"\nLoading {OHLCV_PATH} (for forward-return lookup) ...")
    ohlcv = pd.read_parquet(OHLCV_PATH)
    # Figure out ts column
    if "close" not in ohlcv.columns:
        raise RuntimeError(f"close missing. Have: {list(ohlcv.columns)[:20]}")
    if isinstance(ohlcv.index, pd.DatetimeIndex):
        ohlcv = ohlcv.reset_index()
    # Normalize ts col
    if "ts" not in ohlcv.columns:
        for c in ohlcv.columns:
            if c.lower() in ("timestamp", "datetime", "time", "index"):
                ohlcv = ohlcv.rename(columns={c: "ts"}); break
    ohlcv["ts"] = pd.to_datetime(ohlcv["ts"], utc=True)
    ohlcv = ohlcv[["ts", "close"]].sort_values("ts").drop_duplicates("ts").set_index("ts")
    print(f"  rows={len(ohlcv):,}  range={ohlcv.index.min()} → {ohlcv.index.max()}")

    # Compute forward returns at each horizon (on the full OHLCV series)
    print("\nComputing forward returns at 30m/60m/4h/24h ...")
    fwd = pd.DataFrame(index=ohlcv.index)
    for hname, hmin in HORIZONS.items():
        fwd[f"fwd_{hname}"] = ohlcv["close"].shift(-hmin) - ohlcv["close"]

    # Join fwd to cal via ts (M1 to M1, direct merge)
    cal = cal.set_index("ts")
    cal = cal.join(fwd, how="left")
    cal = cal.reset_index()
    print(f"  joined rows={len(cal):,}")

    # --- Segmented backtest per extreme ---
    print("\n" + "=" * 70)
    print("FORWARD RETURN TABLES PER EXTREME × REGIME × PERIOD")
    print("=" * 70)

    rng = np.random.default_rng(42)
    results = {}

    for ext_name, (col, op, thr, hypothesis_dir) in EXTREMES.items():
        print(f"\n--- {ext_name} ({col} {op} {thr}, hypothesis={hypothesis_dir}) ---")

        if op == "<":
            mask_all = cal[col] < thr
        else:
            mask_all = cal[col] > thr

        ext_df = cal[mask_all & cal["regime"].notna()].copy()
        print(f"  total events (all periods): {len(ext_df):,}")

        ext_results = {}
        for sp_name, (sp_start, sp_end) in SUBPERIODS.items():
            sp_mask = (ext_df["ts"] >= pd.Timestamp(sp_start, tz="UTC")) & (ext_df["ts"] <= pd.Timestamp(sp_end, tz="UTC"))
            sp = ext_df[sp_mask]
            if len(sp) == 0:
                ext_results[sp_name] = {"note": "no events in period", "N": 0}
                print(f"  {sp_name}: N=0")
                continue

            # baseline = all non-extreme rows in same period + regime
            baseline_pool = cal[(cal["ts"] >= pd.Timestamp(sp_start, tz="UTC")) & (cal["ts"] <= pd.Timestamp(sp_end, tz="UTC")) & cal["regime"].notna()]

            by_regime = {}
            for reg in ["RANGE", "TREND_UP", "TREND_DN", "TRANSITIONAL"]:
                if reg in ("TREND_UP", "TREND_DN"):
                    reg_mask = sp["regime_dir"] == reg
                    base_mask = baseline_pool["regime_dir"] == reg
                else:
                    reg_mask = sp["regime"] == reg
                    base_mask = baseline_pool["regime"] == reg

                sub = sp[reg_mask]
                base = baseline_pool[base_mask]

                if len(sub) < 5:
                    by_regime[reg] = {"N": int(len(sub)), "note": "insufficient events (<5)"}
                    continue

                cell = {"N": int(len(sub))}
                for hname in HORIZONS:
                    vals = sub[f"fwd_{hname}"].dropna().values
                    base_vals = base[f"fwd_{hname}"].dropna().values
                    if len(vals) < 5:
                        cell[hname] = {"note": "insufficient fwd returns"}
                        continue
                    mean = float(np.mean(vals))
                    ci_lo, ci_hi = bootstrap_ci(vals, n_boot=1000, rng=rng)
                    # Win rate per hypothesis direction
                    if hypothesis_dir == "LONG":
                        wr = float((vals > 0).mean())
                    else:  # SHORT hypothesis → reversal validated if fwd return < 0
                        wr = float((vals < 0).mean())
                    d = cohens_d(vals, base_vals)
                    cell[hname] = {
                        "N_with_fwd": int(len(vals)),
                        "mean_pts": round(mean, 3),
                        "ci95_lo": round(ci_lo, 3) if not np.isnan(ci_lo) else None,
                        "ci95_hi": round(ci_hi, 3) if not np.isnan(ci_hi) else None,
                        "ci_crosses_zero": bool((ci_lo is not None and not np.isnan(ci_lo)) and ci_lo <= 0 <= ci_hi),
                        "hypothesis_win_rate": round(wr, 4),
                        "cohens_d_vs_baseline": round(d, 4) if not np.isnan(d) else None,
                    }
                by_regime[reg] = cell

            ext_results[sp_name] = {"N_period": int(len(sp)), "by_regime": by_regime}

            # Compact print
            print(f"  {sp_name}: N={len(sp):,}")
            for reg, cell in by_regime.items():
                if cell.get("N", 0) >= 5 and "30m" in cell:
                    c30 = cell["30m"]
                    c4h = cell["4h"]
                    print(f"    {reg:14s} N={cell['N']:>6,}  30m mean={c30['mean_pts']:+.2f}pts WR={c30['hypothesis_win_rate']:.2%}  CI=[{c30['ci95_lo']:+.2f},{c30['ci95_hi']:+.2f}] crosses_0={c30['ci_crosses_zero']}  d={c30['cohens_d_vs_baseline']}  |  4h mean={c4h['mean_pts']:+.2f}pts WR={c4h['hypothesis_win_rate']:.2%}")
                elif cell.get("N", 0) > 0:
                    print(f"    {reg:14s} N={cell['N']:>6,}  {cell.get('note', '')}")

        results[ext_name] = {
            "hypothesis_dir": hypothesis_dir,
            "predicate": f"{col} {op} {thr}",
            "total_events": int(len(ext_df)),
            "by_period": ext_results,
        }

    # --- Save ---
    out_path = SPRINT_DIR / "phase3_segmented_backtest.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n✓ Saved {out_path}")

    print("\n" + "=" * 70)
    print("PHASE 3 DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()

"""recalib_investigation_001.py — EXEC-RECALIB-INVESTIGATION-001 root-cause runner.

Read-only investigation. NO production-code edits, NO config edits.
Outputs JSON dumps to _audit/calibrations/raw/investigation_*.json + per-Step .md.

Steps covered (per Asana 1214283093312730):
  Step 1 — premise: rebuild parquet coverage + handover split
  Step 2 — EFR_ROLLING: wider window grid + 25 vs 100 tie test
  Step 3 — EFR_DIVERGENCE: finer percentile grid extending beyond p90
  Step 4 — F3_B/F5_A/F5_B: pre-handover vs post-handover Cohen's d isolation
  Step 5 — CLOSE_PCT_WEAK: per-direction sub-sample sign verification
  Step 6 — EXHAUSTION_SCORE: wider absolute grid + score-percentile grid
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
    SEEDS, EMBARGO_BARS, N_FOLDS, BOOTSTRAP_N,
    REBUILT_OHLCV_M1, M30_BOXES, CALIBRATION_FULL,
    WINDOW_B_START, WINDOW_B_END,
    sha256_file, env_fingerprint, distribution_stats,
    bootstrap_ci, hypothesis_test, purged_walk_forward_indices,
    robust_thresholds, resample_m1_to_m30, add_bar_body, rolling_pct_rank,
    add_forward_returns,
)

OUT = Path(r"C:\FluxQuantumAI\_audit\calibrations")
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)

HANDOVER_CUTOFF = pd.Timestamp("2025-11-26", tz="UTC")

SOT_N = 3
EFR_ROLLING = 100
EFR_DIVERGENCE_THRESHOLD = 0.3
CLOSE_PCT_WEAK = 0.3
OLD_WEIGHTS = {"F5_B": 0.640, "F3_B": 0.543, "F5_A": 0.394, "F2_B": 0.274, "F1_B": 0.213}


def _utc(idx):
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def cohen_d(a: np.ndarray, b: np.ndarray) -> float | None:
    a = np.asarray(a)[np.isfinite(np.asarray(a, dtype=float))] if len(a) else a
    b = np.asarray(b)[np.isfinite(np.asarray(b, dtype=float))] if len(b) else b
    if len(a) < 5 or len(b) < 5:
        return None
    pooled = np.sqrt(((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1)) /
                     (len(a) + len(b) - 2))
    return float((np.mean(a) - np.mean(b)) / pooled) if pooled > 0 else None


# ============================================================================
# Step 1 — premise verification
# ============================================================================

def step1_premise_check() -> dict:
    print("\n[Step 1] premise check — rebuild parquet coverage", flush=True)
    df_m1 = pd.read_parquet(REBUILT_OHLCV_M1)
    df_m1.index = _utc(df_m1.index)
    full_min = df_m1.index.min()
    full_max = df_m1.index.max()
    full_n = len(df_m1)

    win = df_m1.loc[(df_m1.index >= WINDOW_B_START) & (df_m1.index < WINDOW_B_END)]
    win_min, win_max = win.index.min(), win.index.max()
    win_n = len(win)

    pre = win.loc[win.index < HANDOVER_CUTOFF]
    post = win.loc[win.index >= HANDOVER_CUTOFF]

    # Resample to M30 to compute coverage on the actual calibration grain
    m30 = resample_m1_to_m30(win)
    expected_m30_bars = int((WINDOW_B_END - WINDOW_B_START).total_seconds() / 1800.0)
    m30_n = len(m30)
    m30_pre = m30.loc[m30.index < HANDOVER_CUTOFF]
    m30_post = m30.loc[m30.index >= HANDOVER_CUTOFF]

    # GC market hours: weekdays only, ~23h/day, but we keep the simple expected and report
    # actual vs expected. Coverage = m30_n / expected_m30_bars.
    coverage = m30_n / expected_m30_bars if expected_m30_bars else 0.0

    # Day-by-day count
    by_day = win.assign(d=win.index.tz_convert("UTC").date).groupby("d").size()
    expected_days = pd.date_range(WINDOW_B_START, WINDOW_B_END, freq="D", tz="UTC")
    days_with_data = set(pd.to_datetime(by_day.index).date)
    expected_weekdays = [d.date() for d in expected_days if d.weekday() < 5]
    weekday_with_data = sum(1 for d in expected_weekdays if d in days_with_data)
    weekday_total = len(expected_weekdays)
    weekday_coverage = weekday_with_data / weekday_total if weekday_total else 0.0

    out = {
        "rebuilt_path": str(REBUILT_OHLCV_M1),
        "rebuilt_sha256": sha256_file(REBUILT_OHLCV_M1),
        "full_parquet": {
            "ts_min": str(full_min), "ts_max": str(full_max),
            "n_rows_m1": int(full_n),
        },
        "window_b": {
            "start": str(WINDOW_B_START), "end": str(WINDOW_B_END),
            "ts_min": str(win_min), "ts_max": str(win_max),
            "n_rows_m1": int(win_n),
            "n_rows_m30": int(m30_n),
            "expected_m30_naive": int(expected_m30_bars),
            "naive_coverage_pct": round(coverage * 100, 2),
            "weekday_coverage_pct": round(weekday_coverage * 100, 2),
            "weekdays_with_data": int(weekday_with_data),
            "weekdays_total": int(weekday_total),
        },
        "handover_split": {
            "cutoff_utc": str(HANDOVER_CUTOFF),
            "pre_n_m1": int(len(pre)),
            "post_n_m1": int(len(post)),
            "pre_n_m30": int(len(m30_pre)),
            "post_n_m30": int(len(m30_post)),
            "pre_min": str(pre.index.min()) if len(pre) else None,
            "pre_max": str(pre.index.max()) if len(pre) else None,
            "post_min": str(post.index.min()) if len(post) else None,
            "post_max": str(post.index.max()) if len(post) else None,
        },
        "interpretation": (
            "Pre-handover (kept as-is from prod parquet): " + str(len(pre)) +
            " M1 rows. Post-handover (rebuilt from raw trades): " + str(len(post)) +
            " M1 rows. The DATA-002 P1.5 fix (m30_updater.py:90 mid_price-derived OHLC bug) "
            "affects only the post-handover portion since m30_updater wrote live bars after "
            "the handover. Pre-handover was already offline-generated and is presumed clean."
        ),
    }
    print(f"  full M1 range: {full_min} -> {full_max} (n={full_n})", flush=True)
    print(f"  Window B M30: n={m30_n}, naive_cov={coverage*100:.1f}%, weekday_cov={weekday_coverage*100:.1f}%", flush=True)
    print(f"  pre-handover M30 n={len(m30_pre)}, post-handover M30 n={len(m30_post)}", flush=True)
    return out


# ============================================================================
# Build full feature dataframe (mirrors recalibration_tier3.build_features)
# ============================================================================

def build_features() -> tuple[pd.DataFrame, dict]:
    df_m1 = pd.read_parquet(REBUILT_OHLCV_M1)
    df_m1.index = _utc(df_m1.index)
    df_m1 = df_m1.loc[(df_m1.index >= WINDOW_B_START) & (df_m1.index < WINDOW_B_END)]
    df = resample_m1_to_m30(df_m1)
    df = add_bar_body(df)
    df = add_forward_returns(df, horizons_min=(60,))

    boxes = pd.read_parquet(M30_BOXES, columns=["m30_box_confirmed", "at_struct_level"])
    boxes.index = _utc(boxes.index)
    df = df.join(boxes, how="left")
    df["m30_box_confirmed"] = df["m30_box_confirmed"].fillna(False).astype(bool)
    df["at_struct_level"] = df["at_struct_level"].fillna(False).astype(bool)

    delta_df = pd.read_parquet(CALIBRATION_FULL, columns=["l2_bar_delta"])
    delta_df.index = _utc(delta_df.index)
    delta_df = delta_df.loc[(delta_df.index >= WINDOW_B_START) & (delta_df.index < WINDOW_B_END)]
    m30_delta = delta_df["l2_bar_delta"].dropna().resample("30min", label="left", closed="left").sum()
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
        "m30_rows": int(df.shape[0]),
        "trend_a_n": int(df["trend_a"].sum()),
        "trend_b_n": int(df["trend_b"].sum()),
        "feature_n_active": {f: int(df[f].sum()) for f in ["F1_B", "F2_B", "F3_B", "F5_A", "F5_B"]},
    }
    return df, meta


# ============================================================================
# Step 2 — EFR_ROLLING wider grid + tie analysis
# ============================================================================

def step2_efr_rolling(df: pd.DataFrame) -> dict:
    print("\n[Step 2] EFR_ROLLING wider grid", flush=True)
    grid = [20, 25, 30, 35, 50, 75, 100, 125, 150, 200]
    rows = []
    sub = df.loc[df["trend_b"]].copy()
    for w in grid:
        vol_pct = rolling_pct_rank(df["volume"], w)
        body_pct = rolling_pct_rank(df["bar_body"], w)
        efr = (vol_pct - body_pct)
        sub["efr_w"] = efr
        elig = sub["trend_b"] & sub["efr_w"].notna() & sub["anti_b_60m"].notna()
        s = sub.loc[elig]
        if len(s) < 50:
            continue
        cut = float(np.quantile(s["efr_w"].values, 0.80))
        a = s.loc[s["efr_w"] > cut, "anti_b_60m"].values
        b = s.loc[s["efr_w"] <= cut, "anti_b_60m"].values
        d = cohen_d(a, b)
        # Bootstrap d 95% CI
        rng = np.random.default_rng(42)
        boot_ds = []
        s_arr_a = s.loc[s["efr_w"] > cut, "anti_b_60m"].values
        s_arr_b = s.loc[s["efr_w"] <= cut, "anti_b_60m"].values
        for _ in range(200):
            ai = rng.integers(0, len(s_arr_a), len(s_arr_a))
            bi = rng.integers(0, len(s_arr_b), len(s_arr_b))
            db = cohen_d(s_arr_a[ai], s_arr_b[bi])
            if db is not None:
                boot_ds.append(db)
        if boot_ds:
            ci_lo, ci_hi = float(np.quantile(boot_ds, 0.025)), float(np.quantile(boot_ds, 0.975))
        else:
            ci_lo = ci_hi = None
        rows.append({
            "window": w,
            "n_active": int(len(a)),
            "n_inactive": int(len(b)),
            "cohens_d_p80": d,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "p80_cut": cut,
        })
        print(f"   w={w:>3}  d={d:.4f}  CI=[{ci_lo:.4f},{ci_hi:.4f}]  n_a={len(a)}", flush=True)
    return {"grid": grid, "rows": rows}


# ============================================================================
# Step 3 — EFR_DIVERGENCE finer + extended percentile grid
# ============================================================================

def step3_efr_divergence(df: pd.DataFrame) -> dict:
    print("\n[Step 3] EFR_DIVERGENCE finer grid", flush=True)
    # Use winner from T1.1 (25) per artifact
    vol_pct = rolling_pct_rank(df["volume"], 25)
    body_pct = rolling_pct_rank(df["bar_body"], 25)
    efr = (vol_pct - body_pct)
    df_w = df.copy()
    df_w["efr"] = efr
    elig = df_w["trend_b"] & df_w["efr"].notna() & df_w["anti_b_60m"].notna()
    sub = df_w.loc[elig].copy()

    pcts = [70, 72.5, 75, 77.5, 80, 82.5, 85, 87.5, 90, 92.5, 95, 97.5]
    rows = []
    for pct in pcts:
        thr = float(np.quantile(sub["efr"].values, pct / 100.0))
        a = sub.loc[sub["efr"] > thr, "anti_b_60m"].values
        b = sub.loc[sub["efr"] <= thr, "anti_b_60m"].values
        d = cohen_d(a, b)
        # Multi-seed bootstrap
        d_seeds = []
        for s in SEEDS:
            rng = np.random.default_rng(s)
            sub_idx = rng.choice(len(sub), size=int(0.8 * len(sub)), replace=False)
            sub_s = sub.iloc[sub_idx]
            aa = sub_s.loc[sub_s["efr"] > thr, "anti_b_60m"].values
            bb = sub_s.loc[sub_s["efr"] <= thr, "anti_b_60m"].values
            db = cohen_d(aa, bb)
            if db is not None:
                d_seeds.append(db)
        rows.append({
            "pct": pct, "threshold": thr, "n_active": int(len(a)),
            "cohens_d": d,
            "seed_d_mean": float(np.mean(d_seeds)) if d_seeds else None,
            "seed_d_std": float(np.std(d_seeds, ddof=1)) if len(d_seeds) > 1 else None,
        })
        print(f"   p{pct:>5}  thr={thr:.4f}  d={d if d is None else round(d,4)}  n_a={len(a)}",
              flush=True)
    return {"grid": pcts, "rows": rows}


# ============================================================================
# Step 4 — F3/F5 isolation: pre-handover vs post-handover
# ============================================================================

def step4_f3_f5_isolation(df: pd.DataFrame) -> dict:
    print("\n[Step 4] F3/F5 isolation pre vs post handover", flush=True)
    pre_mask = df.index < HANDOVER_CUTOFF
    post_mask = df.index >= HANDOVER_CUTOFF

    feats_labels = {
        "F1_B": "anti_b_60m", "F2_B": "anti_b_60m", "F3_B": "anti_b_60m",
        "F5_A": "anti_a_60m", "F5_B": "anti_b_60m",
    }
    out = {"per_feature": []}
    for feat, lab in feats_labels.items():
        elig_col = "trend_a" if feat == "F5_A" else "trend_b"
        sub_full = df.loc[df[elig_col] & df[lab].notna()]
        sub_pre = sub_full.loc[sub_full.index < HANDOVER_CUTOFF]
        sub_post = sub_full.loc[sub_full.index >= HANDOVER_CUTOFF]

        def _d_pooled(s):
            a = s.loc[s[feat], lab].values
            b = s.loc[~s[feat], lab].values
            return cohen_d(a, b), len(a), len(b)

        d_full, na_full, nb_full = _d_pooled(sub_full)
        d_pre, na_pre, nb_pre = _d_pooled(sub_pre)
        d_post, na_post, nb_post = _d_pooled(sub_post)

        # Multi-seed bootstrap on each subset for CI
        def _seed_d(s):
            ds = []
            for sd in SEEDS:
                rng = np.random.default_rng(sd)
                if len(s) < 30:
                    continue
                ix = rng.choice(len(s), int(0.8 * len(s)), replace=False)
                ss = s.iloc[ix]
                a = ss.loc[ss[feat], lab].values
                b = ss.loc[~ss[feat], lab].values
                d = cohen_d(a, b)
                if d is not None:
                    ds.append(d)
            return ds

        seed_pre = _seed_d(sub_pre)
        seed_post = _seed_d(sub_post)
        seed_full = _seed_d(sub_full)

        def _stat(ds):
            return {
                "mean": float(np.mean(ds)) if ds else None,
                "std": float(np.std(ds, ddof=1)) if len(ds) > 1 else None,
                "n": len(ds),
            }

        rec = {
            "feature": feat,
            "label": lab,
            "full": {"d": d_full, "n_active": na_full, "n_inactive": nb_full,
                     "seed_stats": _stat(seed_full)},
            "pre_handover": {"d": d_pre, "n_active": na_pre, "n_inactive": nb_pre,
                             "seed_stats": _stat(seed_pre)},
            "post_handover": {"d": d_post, "n_active": na_post, "n_inactive": nb_post,
                              "seed_stats": _stat(seed_post)},
        }
        out["per_feature"].append(rec)
        print(f"   {feat}  full d={d_full if d_full is None else round(d_full,4)}  "
              f"pre d={d_pre if d_pre is None else round(d_pre,4)} (n_a={na_pre})  "
              f"post d={d_post if d_post is None else round(d_post,4)} (n_a={na_post})",
              flush=True)
    return out


# ============================================================================
# Step 5 — CLOSE_PCT_WEAK sign anomaly investigation
# ============================================================================

def step5_close_pct_sign(df: pd.DataFrame) -> dict:
    print("\n[Step 5] CLOSE_PCT_WEAK sign sub-sample test", flush=True)
    df_w = df.copy()
    long_mask = (df_w["trend_b_dir"] == +1)
    short_mask = (df_w["trend_b_dir"] == -1)
    df_w["close_pct_eff"] = np.where(
        long_mask, df_w["close_pct_from_low"],
        np.where(short_mask, df_w["close_pct_from_high"], np.nan),
    )
    elig = df_w["trend_b"] & df_w["close_pct_eff"].notna() & df_w["anti_b_60m"].notna()
    sub_all = df_w.loc[elig].copy()

    pcts = [10, 15, 20, 25, 30, 35, 40, 45, 50]
    rows = []

    def _d_for_thr(s, thr):
        a = s.loc[s["close_pct_eff"] < thr, "anti_b_60m"].values
        b = s.loc[s["close_pct_eff"] >= thr, "anti_b_60m"].values
        d = cohen_d(a, b)
        return {"d": d, "n_a": len(a), "n_b": len(b),
                "mean_a": float(np.mean(a)) if len(a) else None,
                "mean_b": float(np.mean(b)) if len(b) else None}

    sub_long = sub_all.loc[long_mask].copy()
    sub_short = sub_all.loc[short_mask].copy()
    sub_pre = sub_all.loc[sub_all.index < HANDOVER_CUTOFF].copy()
    sub_post = sub_all.loc[sub_all.index >= HANDOVER_CUTOFF].copy()

    for pct in pcts:
        thr_all = float(np.quantile(sub_all["close_pct_eff"].values, pct / 100.0))
        rec = {
            "pct": pct, "threshold_all": thr_all,
            "all": _d_for_thr(sub_all, thr_all),
            "long_only": _d_for_thr(sub_long, thr_all),
            "short_only": _d_for_thr(sub_short, thr_all),
            "pre_handover": _d_for_thr(sub_pre, thr_all),
            "post_handover": _d_for_thr(sub_post, thr_all),
        }
        rows.append(rec)
        d_a = rec["all"]["d"]; d_l = rec["long_only"]["d"]; d_s = rec["short_only"]["d"]
        d_pr = rec["pre_handover"]["d"]; d_po = rec["post_handover"]["d"]
        print(f"   p{pct:>2}  thr={thr_all:.4f}  "
              f"all={d_a if d_a is None else round(d_a,4):>7}  "
              f"long={d_l if d_l is None else round(d_l,4):>7}  "
              f"short={d_s if d_s is None else round(d_s,4):>7}  "
              f"pre={d_pr if d_pr is None else round(d_pr,4):>7}  "
              f"post={d_po if d_po is None else round(d_po,4):>7}",
              flush=True)
    return {"grid": pcts, "rows": rows,
            "n_long": int(long_mask.sum() if long_mask.dtype == bool else 0),
            "n_short": int(short_mask.sum() if short_mask.dtype == bool else 0)}


# ============================================================================
# Step 6 — EXHAUSTION_SCORE wider grid + score-percentile grid
# ============================================================================

def step6_exhaustion_score(df: pd.DataFrame) -> dict:
    print("\n[Step 6] EXHAUSTION_SCORE wider grid + score-percentile", flush=True)

    def _score(weights):
        return (weights.get("F5_B", 0.0) * df["F5_B"].astype(float)
                + weights.get("F3_B", 0.0) * df["F3_B"].astype(float)
                + weights.get("F5_A", 0.0) * df["F5_A"].astype(float)
                + weights.get("F2_B", 0.0) * df["F2_B"].astype(float)
                + weights.get("F1_B", 0.0) * df["F1_B"].astype(float))

    # We compare under OLD weights (production) and NEW weights (recalibrated) for honesty
    new_w = {"F5_B": 0.1755, "F3_B": 0.2203, "F5_A": 0.1885, "F2_B": 0.2784, "F1_B": 0.1961}

    out = {}
    for tag, weights in [("OLD", OLD_WEIGHTS), ("NEW", new_w)]:
        score = _score(weights)
        df_s = df.copy()
        df_s["score"] = score
        df_s["anti_combined"] = np.where(df_s["trend_b"], df_s["anti_b_60m"],
                                         np.where(df_s["trend_a"], df_s["anti_a_60m"], np.nan))
        elig = df_s["anti_combined"].notna() & (df_s["trend_a"] | df_s["trend_b"])
        sub = df_s.loc[elig].copy()
        # ABSOLUTE grid wider
        abs_grid = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80]
        rows_abs = []
        for thr in abs_grid:
            a_mask = sub["score"] > thr
            a = sub.loc[a_mask, "anti_combined"].values
            b = sub.loc[~a_mask, "anti_combined"].values
            ht = hypothesis_test(a, b)
            rows_abs.append({
                "threshold": thr,
                "n_active": int(a_mask.sum()),
                "pass_rate": float(a_mask.mean()),
                "mean_active": float(np.mean(a)) if len(a) else None,
                "expectancy": float(np.mean(a) * a_mask.mean()) if len(a) else None,
                "cohens_d": ht.get("cohens_d"),
            })
        # PERCENTILE grid (over score's empirical distribution among ELIGIBLE bars)
        pcts = [50, 60, 70, 75, 80, 85, 90, 92.5, 95, 97.5]
        rows_pct = []
        for pct in pcts:
            thr = float(np.quantile(sub["score"].values, pct / 100.0))
            a_mask = sub["score"] > thr
            a = sub.loc[a_mask, "anti_combined"].values
            b = sub.loc[~a_mask, "anti_combined"].values
            ht = hypothesis_test(a, b)
            rows_pct.append({
                "pct": pct, "threshold": thr,
                "n_active": int(a_mask.sum()),
                "pass_rate": float(a_mask.mean()),
                "mean_active": float(np.mean(a)) if len(a) else None,
                "expectancy": float(np.mean(a) * a_mask.mean()) if len(a) else None,
                "cohens_d": ht.get("cohens_d"),
            })
        out[tag] = {"weights": weights, "absolute_grid": rows_abs,
                    "percentile_grid": rows_pct}
        print(f"   --- weights={tag} ---", flush=True)
        for r in rows_abs:
            ma = r["mean_active"]; ex = r["expectancy"]; d = r["cohens_d"]
            print(f"     thr={r['threshold']:.2f}  n_act={r['n_active']:>5}  "
                  f"mean_a={ma if ma is None else round(ma,3):>7}  "
                  f"exp={ex if ex is None else round(ex,4):>8}  "
                  f"d={d if d is None else round(d,3):>7}",
                  flush=True)
    return out


# ============================================================================
# Main
# ============================================================================

def main():
    print("=== EXEC-RECALIB-INVESTIGATION-001 ===", flush=True)
    s1 = step1_premise_check()
    df, meta = build_features()
    print(f"  features built: M30 rows={meta['m30_rows']}, "
          f"trend_a={meta['trend_a_n']}, trend_b={meta['trend_b_n']}", flush=True)
    print(f"  feature N_active: {meta['feature_n_active']}", flush=True)
    s2 = step2_efr_rolling(df)
    s3 = step3_efr_divergence(df)
    s4 = step4_f3_f5_isolation(df)
    s5 = step5_close_pct_sign(df)
    s6 = step6_exhaustion_score(df)

    out = {
        "env": env_fingerprint(),
        "feature_meta": meta,
        "step1_premise": s1,
        "step2_efr_rolling": s2,
        "step3_efr_divergence": s3,
        "step4_f3_f5_isolation": s4,
        "step5_close_pct_sign": s5,
        "step6_exhaustion_score": s6,
    }
    raw_path = RAW / "investigation_001_raw.json"
    raw_path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nraw -> {raw_path}", flush=True)
    return out


if __name__ == "__main__":
    main()

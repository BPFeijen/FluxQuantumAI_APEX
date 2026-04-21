"""INVEST-01 Phase 2 — Regime Tagging of Historical Data.

READ-ONLY: writes only to sprint dir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SPRINT_DIR = Path("C:/FluxQuantumAI/sprints/sprint_invest_01_cal03_20260420")
V5_PATH = Path("C:/data/processed/gc_ats_features_v5.parquet")
CAL_DATASET_PATH = Path("C:/data/processed/calibration_dataset_full.parquet")

# Walk-forward sub-periods (aligned with prompt §4.4)
PERIODS = {
    "full_2020_2026": ("2020-01-01", "2026-03-31"),
    "pre_sprint8_2020_2025H1": ("2020-01-01", "2025-06-30"),
    "sprint8_training_era_2025_Jul_Nov": ("2025-07-01", "2025-11-30"),
    "post_deploy_2025_Dec_2026_Mar": ("2025-12-01", "2026-03-31"),
    "recent_live_2026_Apr": ("2026-04-01", "2026-03-31"),  # will show empty if no data; v5 stale 2026-04-12
}


def classify_regime(row) -> str:
    contraction = bool(row["m30_in_contraction"])
    waligned = bool(row["weekly_aligned"])
    dtrend = row["daily_trend"]
    if contraction and not waligned:
        return "RANGE"
    if (not contraction) and waligned and dtrend in ("long", "short"):
        return "TREND"
    return "TRANSITIONAL"


def direction_segment(row) -> str:
    if row["regime"] != "TREND":
        return row["regime"]
    return "TREND_UP" if row["daily_trend"] == "long" else "TREND_DN"


def slice_period(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return df[(df["ts"] >= pd.Timestamp(start, tz="UTC")) & (df["ts"] <= pd.Timestamp(end, tz="UTC"))]


def distribution(df: pd.DataFrame, col: str) -> dict:
    vc = df[col].value_counts(dropna=False)
    total = int(vc.sum())
    out = {k: {"count": int(v), "pct": round(100 * v / total, 2)} for k, v in vc.items()}
    out["_total"] = total
    return out


def main() -> None:
    print("=" * 70)
    print("INVEST-01 PHASE 2 — REGIME TAGGING")
    print("=" * 70)

    # --- Load v5 ---
    print(f"\nLoading {V5_PATH} ...")
    df = pd.read_parquet(V5_PATH)
    print(f"  rows={len(df):,}  cols={len(df.columns)}")

    # Identify timestamp column
    ts_col_candidates = [c for c in df.columns if c.lower() in ("ts", "timestamp", "datetime", "date", "index")]
    if "ts" in df.columns:
        ts_col = "ts"
    elif df.index.name in ("ts", "timestamp", "datetime"):
        df = df.reset_index()
        ts_col = df.columns[0]
    elif ts_col_candidates:
        ts_col = ts_col_candidates[0]
    else:
        # Assume DatetimeIndex
        df = df.reset_index()
        ts_col = df.columns[0]
    df = df.rename(columns={ts_col: "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    print(f"  ts range: {df['ts'].min()} → {df['ts'].max()}")

    # --- Verify required columns ---
    required = ["m30_in_contraction", "weekly_aligned", "daily_trend"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"Missing required columns: {missing}. Available: {list(df.columns)[:30]}...")

    # --- Classify ---
    print("\nClassifying regimes ...")
    df["regime"] = df.apply(classify_regime, axis=1)
    df["regime_dir"] = df.apply(direction_segment, axis=1)

    # --- Overall distributions per period ---
    print("\n" + "=" * 70)
    print("REGIME DISTRIBUTION BY PERIOD (§3.3)")
    print("=" * 70)
    dists = {}
    for pname, (start, end) in PERIODS.items():
        sub = slice_period(df, start, end)
        if len(sub) == 0:
            dists[pname] = {"note": "EMPTY (no rows in period — v5 staleness 2026-03-31)", "rows": 0}
            continue
        dists[pname] = {
            "rows": len(sub),
            "range": f"{sub['ts'].min()} → {sub['ts'].max()}",
            "regime": distribution(sub, "regime"),
            "regime_dir": distribution(sub, "regime_dir"),
        }
        print(f"\n{pname}  N={len(sub):,}  range={sub['ts'].min()} → {sub['ts'].max()}")
        print(f"  regime: {dict(sub['regime'].value_counts())}")
        print(f"  regime_dir: {dict(sub['regime_dir'].value_counts())}")

    # --- delta_4h extremes per regime (§3.4) ---
    print("\n" + "=" * 70)
    print("DELTA_4H EXTREMES PER REGIME (§3.4)")
    print("=" * 70)
    print(f"\nLoading {CAL_DATASET_PATH} ...")
    cal = pd.read_parquet(CAL_DATASET_PATH)
    print(f"  rows={len(cal):,}  cols={len(cal.columns)}")

    # Handle ts
    if "ts" in cal.columns:
        cal_ts = "ts"
    else:
        cal = cal.reset_index()
        cal_ts = cal.columns[0]
    cal = cal.rename(columns={cal_ts: "ts"})
    cal["ts"] = pd.to_datetime(cal["ts"], utc=True)
    print(f"  ts range: {cal['ts'].min()} → {cal['ts'].max()}")

    if "rolling_delta_4h" not in cal.columns:
        raise RuntimeError(f"rolling_delta_4h missing. Available: {list(cal.columns)[:30]}...")

    # Flag extremes
    cal["d4h_ext_neg"] = cal["rolling_delta_4h"] < -1050
    cal["d4h_ext_pos"] = cal["rolling_delta_4h"] > 3000
    # Also flag the original 800 threshold from _cal03_finding
    cal["d4h_ext_pos_800"] = cal["rolling_delta_4h"] > 800
    cal["d4h_ext_neg_800"] = cal["rolling_delta_4h"] < -800

    # Join regime to cal via merge_asof on M30 bar (v5 is M30 cadence; cal is M1)
    print("\nJoining M1 cal to M30 regime via merge_asof ...")
    df_sorted = df[["ts", "regime", "regime_dir"]].sort_values("ts")
    cal_sorted = cal.sort_values("ts")
    cal_joined = pd.merge_asof(
        cal_sorted,
        df_sorted,
        on="ts",
        direction="backward",
        tolerance=pd.Timedelta("30min"),
    )
    matched = cal_joined["regime"].notna().sum()
    print(f"  matched M1 rows: {matched:,} / {len(cal_joined):,} ({100 * matched / len(cal_joined):.1f}%)")

    # Distribution of extremes per regime per period
    ext_tables = {}
    for pname, (start, end) in PERIODS.items():
        sub = slice_period(cal_joined, start, end)
        sub = sub[sub["regime"].notna()]
        if len(sub) == 0:
            ext_tables[pname] = {"note": "EMPTY in this period", "rows": 0}
            continue
        breakdown = {}
        for reg in ["RANGE", "TREND_UP", "TREND_DN", "TRANSITIONAL"]:
            if reg in ("TREND_UP", "TREND_DN"):
                mask = sub["regime_dir"] == reg
            else:
                mask = sub["regime"] == reg
            sub_r = sub[mask]
            if len(sub_r) == 0:
                breakdown[reg] = {"N": 0}
                continue
            breakdown[reg] = {
                "N": int(len(sub_r)),
                "d4h_ext_neg_lt_-1050": int(sub_r["d4h_ext_neg"].sum()),
                "d4h_ext_pos_gt_+3000": int(sub_r["d4h_ext_pos"].sum()),
                "d4h_ext_neg_lt_-800": int(sub_r["d4h_ext_neg_800"].sum()),
                "d4h_ext_pos_gt_+800": int(sub_r["d4h_ext_pos_800"].sum()),
            }
        ext_tables[pname] = {"rows_matched": int(len(sub)), "breakdown": breakdown}
        print(f"\n{pname}  matched N={len(sub):,}")
        for reg, stats in breakdown.items():
            if stats.get("N"):
                print(f"  {reg:14s} N={stats['N']:>10,}  <-1050:{stats['d4h_ext_neg_lt_-1050']:>6}  >+3000:{stats['d4h_ext_pos_gt_+3000']:>6}  <-800:{stats['d4h_ext_neg_lt_-800']:>6}  >+800:{stats['d4h_ext_pos_gt_+800']:>6}")

    # --- Save outputs ---
    out = {
        "source_v5": str(V5_PATH),
        "source_cal": str(CAL_DATASET_PATH),
        "v5_rows": len(df),
        "v5_range": f"{df['ts'].min()} → {df['ts'].max()}",
        "cal_rows": len(cal),
        "cal_range": f"{cal['ts'].min()} → {cal['ts'].max()}",
        "regime_classifier": {
            "RANGE": "m30_in_contraction == True AND weekly_aligned == False",
            "TREND": "m30_in_contraction == False AND weekly_aligned == True AND daily_trend in (long, short)",
            "TRANSITIONAL": "else",
        },
        "regime_distribution_by_period": dists,
        "delta_4h_extremes_per_regime_by_period": ext_tables,
    }
    out_path = SPRINT_DIR / "phase2_regime_distributions.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n✓ Saved {out_path}")

    # Save parquet with regime labels for Phase 3 reuse
    labelled_path = SPRINT_DIR / "phase2_cal_with_regime.parquet"
    cal_out = cal_joined[["ts", "rolling_delta_4h", "regime", "regime_dir"]].copy()
    # Keep only matched rows for size
    cal_out = cal_out[cal_out["regime"].notna()]
    cal_out.to_parquet(labelled_path, compression="zstd")
    print(f"✓ Saved {labelled_path} ({len(cal_out):,} labelled M1 rows)")

    print("\n" + "=" * 70)
    print("PHASE 2 DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()

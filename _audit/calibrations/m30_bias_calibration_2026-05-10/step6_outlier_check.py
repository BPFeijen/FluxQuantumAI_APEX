"""G-PURDUE Step 6 — Outlier handling robustness check.

Identify high-volatility outlier days in the corpus (events like NFP, FOMC,
CPI, geopolitical shocks) and re-run the winner candidate from Step 5
WITHOUT those days. If winner accuracy is robust, the result is reliable.
If accuracy collapses without outliers, the winner was an artifact of a
few volatile days.

Outlier identification: top-N days by intraday range (high - low) at M30.

Output: `step6/outlier_robustness.md`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:/FluxQuantumAI")
STEP4_RESULTS = (ROOT / "_audit" / "calibrations"
                 / "m30_bias_calibration_2026-05-10" / "step4"
                 / "grid_results.json")
STEP5_RESULTS = (ROOT / "_audit" / "calibrations"
                 / "m30_bias_calibration_2026-05-10" / "step5"
                 / "hypothesis_results.json")
OUT_DIR = (ROOT / "_audit" / "calibrations"
           / "m30_bias_calibration_2026-05-10" / "step6")
OUT_DIR.mkdir(parents=True, exist_ok=True)

OHLCV_PARQUET = Path(r"C:/data/processed/gc_ohlcv_l2_joined.parquet")

START = pd.Timestamp("2025-07-01", tz="UTC")
END = pd.Timestamp("2026-05-09 00:00:00", tz="UTC")
TOP_N_OUTLIERS = 10  # remove top 10 highest-range days


def main() -> int:
    print("=" * 70)
    print("G-PURDUE Step 6 - Outlier robustness check")
    print("=" * 70)

    if not STEP5_RESULTS.exists():
        print("ERROR: run Step 5 first.")
        return 1

    print("\n[1/3] Identifying outlier days by intraday range ...")
    df = pd.read_parquet(OHLCV_PARQUET, columns=["high", "low", "close"])
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= START) & (df.index < END)]
    df["date"] = df.index.date
    daily = df.groupby("date").agg(
        high=("high", "max"), low=("low", "min"),
        close_first=("close", "first"), close_last=("close", "last"),
    )
    daily["range_pts"] = daily["high"] - daily["low"]

    # Filter outliers to dates that have decisions (decisions only Apr-May)
    sys.path.insert(0, str(ROOT))
    import importlib.util as _ilu
    _step4_path = (ROOT / "_audit" / "calibrations"
                   / "m30_bias_calibration_2026-05-10"
                   / "step4_grid_bootstrap.py")
    _spec = _ilu.spec_from_file_location("_s4_local", _step4_path)
    _s4 = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_s4)
    _df_dec_temp = _s4.load_decision_log()
    _dec_dates = set(_df_dec_temp["ts"].dt.date.unique())
    daily_with_dec = daily[daily.index.isin(_dec_dates)]
    daily_with_dec = daily_with_dec.sort_values("range_pts", ascending=False)
    outlier_dates = daily_with_dec.head(TOP_N_OUTLIERS)
    print(f"      Decisions span {len(_dec_dates)} dates")
    print(f"      Top {TOP_N_OUTLIERS} outlier days WITH decisions:")
    for d, row in outlier_dates.iterrows():
        print(f"        {d}: range = {row['range_pts']:.1f} pts")

    print("\n[2/3] Loading Step 5 winner ...")
    step5 = json.loads(STEP5_RESULTS.read_text(encoding="utf-8"))
    winner = step5["winner"]
    cand = winner["candidate"]
    print(f"      Winner: ({cand['min_bars']}, {cand['window']}, "
          f"{cand['strategy']})")
    full_acc = winner["bootstrap"]["acc_mean"]
    full_lo = winner["bootstrap"]["acc_lo"]
    full_hi = winner["bootstrap"]["acc_hi"]
    print(f"      Full corpus: acc={full_acc:.4f} 95%CI=[{full_lo:.4f}, "
          f"{full_hi:.4f}]")

    # Re-run ONLY winner candidate without outlier days. We need to redo
    # decision filtering. Reuse the corpus pipeline from Step 4 imports.
    print("\n[3/3] Re-running winner without top-{} outlier days ...".format(
        TOP_N_OUTLIERS))
    s4 = _s4
    df_dec = s4.load_decision_log()
    ohlcv = pd.read_parquet(OHLCV_PARQUET, columns=["close"])
    ohlcv.index = pd.to_datetime(ohlcv.index, utc=True)
    df_dec = s4.add_realized_and_regime(df_dec, ohlcv["close"].sort_index())

    # Filter out outlier days
    outlier_set = set(outlier_dates.index)
    df_dec_clean = df_dec[~df_dec["ts"].dt.date.isin(outlier_set)].reset_index(drop=True)
    pct_removed = 100 * (1 - len(df_dec_clean) / max(1, len(df_dec)))
    print(f"      After removing outliers: "
          f"{len(df_dec_clean):,} signals ({pct_removed:.1f}% removed)")

    m30 = pd.read_parquet(r"C:/data/processed/gc_m30_boxes.parquet")
    m30.index = pd.to_datetime(m30.index, utc=True)
    m30 = m30[(m30.index >= START) & (m30.index < END)]
    box_index = s4.ConfirmedBoxIndex(m30)

    cand_tuple = (cand["min_bars"], cand["window"], cand["strategy"])
    replayed = s4.replay_for_candidate(df_dec_clean, box_index, cand_tuple)
    overall = s4.score_overall(replayed)
    boot = s4.bootstrap_ci(replayed)
    clean_acc = boot["acc_mean"]
    clean_lo = boot["acc_lo"]
    clean_hi = boot["acc_hi"]
    print(f"      Without outliers: acc={clean_acc:.4f} 95%CI=[{clean_lo:.4f}, "
          f"{clean_hi:.4f}] block={boot['block_mean']:.3f}")

    # Robustness verdict
    delta = full_acc - clean_acc
    ci_overlap = full_lo <= clean_hi and clean_lo <= full_hi
    robust = ci_overlap and abs(delta) < 0.01

    md: list[str] = []
    md.append("# G-PURDUE Step 6 - Outlier robustness check\n\n")
    md.append("## Outlier days removed\n\n")
    md.append(f"Top {TOP_N_OUTLIERS} days by intraday range (high - low):\n\n")
    md.append("| Date | Range (pts) |\n|---|---:|\n")
    for d, row in outlier_dates.iterrows():
        md.append(f"| {d} | {row['range_pts']:.1f} |\n")
    md.append("\n")

    md.append("## Winner candidate\n\n")
    md.append(f"`(min_bars={cand['min_bars']}, window={cand['window']}, "
              f"strategy={cand['strategy']})`\n\n")

    md.append("## Robustness comparison\n\n")
    md.append("| Metric | Full corpus | Without outliers | Delta |\n")
    md.append("|---|---:|---:|---:|\n")
    md.append(f"| acc_mean | {full_acc:.4f} | {clean_acc:.4f} | "
              f"{delta:+.4f} |\n")
    md.append(f"| 95% CI lo | {full_lo:.4f} | {clean_lo:.4f} | "
              f"{full_lo - clean_lo:+.4f} |\n")
    md.append(f"| 95% CI hi | {full_hi:.4f} | {clean_hi:.4f} | "
              f"{full_hi - clean_hi:+.4f} |\n")
    md.append(f"| Signals analyzed | {df_dec.shape[0]:,} | "
              f"{df_dec_clean.shape[0]:,} | {-len(df_dec) + len(df_dec_clean)} |\n")
    md.append("\n")

    md.append("## Verdict\n\n")
    if robust:
        md.append(f"**Robust to outliers.** Removing top {TOP_N_OUTLIERS} "
                  f"high-volatility days changes accuracy by {delta:+.4f} "
                  f"(within ~1pp), CIs overlap. Winner stands.\n")
    else:
        md.append(f"**Outlier-sensitive.** Removing top {TOP_N_OUTLIERS} days "
                  f"changes accuracy by {delta:+.4f}; consider whether outliers "
                  f"are representative or need separate handling.\n")

    (OUT_DIR / "outlier_robustness.md").write_text("".join(md), encoding="utf-8")
    print(f"\nReport: {OUT_DIR / 'outlier_robustness.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

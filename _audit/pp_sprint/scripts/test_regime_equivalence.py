"""
Equivalence test: analysis-script TREND-A vs live detect_trend_a.

Purpose: verify that the live detector produces the same activations as the
TREND-A tagging used by T1-X1-INTERACTIONS (which calibrated the LOGIC-C
feature weights). Divergence would mean LOGIC-C weights do not apply.

Dataset: gc_m30_boxes.parquet, Window B (2025-07-01 .. 2026-04-22),
L2 gap dates excluded per T0-D.

Both methodologies applied on the same closed-bars subset.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from live.regime_detectors import detect_trend_a  # noqa: E402

PARQUET_PATH = Path("C:/data/processed/gc_m30_boxes.parquet")

WINDOW_B_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_B_END = pd.Timestamp("2026-04-22 23:59:59", tz="UTC")

L2_GAP_DATES = [
    "2025-11-25",
    "2026-01-26", "2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30",
    "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13",
    "2026-03-26",
]


def analysis_trend_a(df: pd.DataFrame) -> pd.DataFrame:
    """Replicate analysis-script TREND-A from t1_x1_interactions.py lines 77, 99-103."""
    out = df.copy()
    out["close_direction"] = np.sign(out["close"] - out["close"].shift(1))
    cd = out["close_direction"]
    up3 = (cd == 1) & (cd.shift(1) == 1) & (cd.shift(2) == 1)
    down3 = (cd == -1) & (cd.shift(1) == -1) & (cd.shift(2) == -1)
    out["trend_a_analysis"] = (up3 | down3).fillna(False)
    out["trend_a_analysis_dir"] = np.where(up3, "LONG", np.where(down3, "SHORT", ""))
    return out


def live_trend_a_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Apply live detect_trend_a with n_bars=3 as a rolling function over CLOSED bars."""
    out = df.copy()
    is_t = np.zeros(len(out), dtype=bool)
    direction = np.empty(len(out), dtype=object)
    direction[:] = ""
    closes = out["close"].values
    for i in range(2, len(out)):
        window = [{"close": closes[i - 2]}, {"close": closes[i - 1]}, {"close": closes[i]}]
        is_trend, d = detect_trend_a(window, n_bars=3)
        is_t[i] = is_trend
        direction[i] = d
    out["trend_a_live"] = is_t
    out["trend_a_live_dir"] = direction
    return out


def main() -> int:
    if not PARQUET_PATH.exists():
        print(f"ERROR: parquet not found at {PARQUET_PATH}")
        return 2

    df = pd.read_parquet(PARQUET_PATH)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    mask_b = (df.index >= WINDOW_B_START) & (df.index <= WINDOW_B_END)
    df_b = df[mask_b].copy()
    print(f"Window B raw bars: {len(df_b)}")

    excl = pd.DatetimeIndex(L2_GAP_DATES, tz="UTC").date
    mask_keep = ~df_b.index.to_series().dt.date.isin(excl)
    df_b = df_b[mask_keep.values]
    print(f"After L2 gap exclusion ({len(L2_GAP_DATES)} dates): {len(df_b)}")

    closed = df_b[df_b["m30_box_confirmed"] == True].copy()  # noqa: E712
    print(f"Closed bars (m30_box_confirmed=True): {len(closed)}")

    closed = analysis_trend_a(closed)
    closed = live_trend_a_rolling(closed)

    closed = closed.iloc[3:].copy()
    total = len(closed)

    both_active = (closed["trend_a_live"] & closed["trend_a_analysis"]).sum()
    both_inactive = ((~closed["trend_a_live"]) & (~closed["trend_a_analysis"])).sum()
    live_only = (closed["trend_a_live"] & (~closed["trend_a_analysis"])).sum()
    ana_only = ((~closed["trend_a_live"]) & closed["trend_a_analysis"]).sum()
    disagreement = live_only + ana_only

    rate_live = 100.0 * closed["trend_a_live"].sum() / total
    rate_ana = 100.0 * closed["trend_a_analysis"].sum() / total

    both_on = closed[closed["trend_a_live"] & closed["trend_a_analysis"]]
    dir_long = (
        (both_on["trend_a_live_dir"] == "LONG")
        & (both_on["trend_a_analysis_dir"] == "LONG")
    ).sum()
    dir_short = (
        (both_on["trend_a_live_dir"] == "SHORT")
        & (both_on["trend_a_analysis_dir"] == "SHORT")
    ).sum()
    dir_conflict = (
        both_on["trend_a_live_dir"] != both_on["trend_a_analysis_dir"]
    ).sum()

    print()
    print("=" * 72)
    print("Equivalence Report — TREND-A live vs analysis")
    print("=" * 72)
    print(f"Total bars compared:               {total}")
    print(f"Both AGREE on activation:          {both_active}  ({100*both_active/total:.2f}%)")
    print(f"Both AGREE on inactivation:        {both_inactive}  ({100*both_inactive/total:.2f}%)")
    print(f"Disagreement (live=T, ana=F):      {live_only}")
    print(f"Disagreement (live=F, ana=T):      {ana_only}")
    print(f"Total disagreement:                {disagreement}  ({100*disagreement/total:.2f}%)")
    print()
    print(f"Activation rate  live:             {rate_live:.2f}%")
    print(f"Activation rate  analysis:         {rate_ana:.2f}%")
    print(f"Difference:                        {abs(rate_live - rate_ana):.2f} pp")
    print()
    print("Direction comparison (on bars where both ACTIVE):")
    print(f"  LONG  agreement:                 {dir_long}")
    print(f"  SHORT agreement:                 {dir_short}")
    print(f"  Direction conflict:              {dir_conflict}")

    disagreement_pct = 100.0 * disagreement / total
    if disagreement_pct < 5.0:
        verdict = "EQUIVALENT"
    elif disagreement_pct < 15.0:
        verdict = "MOSTLY EQUIVALENT"
    else:
        verdict = "DIVERGENT"

    print()
    print(f"Verdict: {verdict}  (disagreement {disagreement_pct:.2f}%)")

    if verdict != "EQUIVALENT":
        print()
        print("Sample 10 disagreement bars:")
        sample_cols = ["close", "trend_a_live", "trend_a_live_dir",
                       "trend_a_analysis", "trend_a_analysis_dir", "close_direction"]
        disagreement_mask = closed["trend_a_live"] != closed["trend_a_analysis"]
        sample = closed[disagreement_mask].head(10)
        print(sample[sample_cols].to_string())

    return 0 if verdict == "EQUIVALENT" else 1


if __name__ == "__main__":
    sys.exit(main())

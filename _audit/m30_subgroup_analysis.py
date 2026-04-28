"""Subgroup analysis: top candidates × session × ATR bucket.
Confirms whether the +1pp lift is robust across regimes or driven by one bucket.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from m30_box_stagnation_backtest import (
    load_and_prepare, baseline_bias, apply_utad_flip, derive_bias_under_rule,
)

CANDIDATES = [
    ("control",   None),
    ("A_K2",      ("A", {"K": 2.0})),
    ("B_T4",      ("B", {"T": 4.0})),
    ("C_K2_T4",   ("C", {"K": 2.0, "T": 4.0})),
    ("C_K2_T8",   ("C", {"K": 2.0, "T": 8.0})),
]


def acc_for(bias: pd.Series, df: pd.DataFrame, mask: pd.Series, fwd_col: str = "fwd_4h") -> tuple[float, int]:
    fwd = df[fwd_col]
    valid = mask & bias.isin(["bullish", "bearish"]) & fwd.notna()
    if valid.sum() == 0:
        return (np.nan, 0)
    correct = ((bias == "bullish") & (fwd > 0)) | ((bias == "bearish") & (fwd < 0))
    return (float(correct[valid].sum() / valid.sum()), int(valid.sum()))


def main():
    df = load_and_prepare()
    raw_with_utad = apply_utad_flip(df, baseline_bias(df))
    base_bias = raw_with_utad

    biases = {}
    for label, spec in CANDIDATES:
        if spec is None:
            biases[label] = base_bias
        else:
            biases[label] = derive_bias_under_rule(df, raw_with_utad, spec[0], spec[1])

    sessions = ["asian", "london", "ny", "off"]
    atrbs    = ["low_vol", "med_vol", "high_vol"]

    print("\nACC@4h x SUBGROUP MATRIX  (n_valid in parens)")
    header = f"{'subgroup':>22}" + "".join(f"  {l:>10}" for l, _ in CANDIDATES)
    print(header); print("-" * len(header))
    rows = []
    for sess in sessions:
        for atr in atrbs:
            mask = (df["session"] == sess) & (df["atr_bucket"] == atr)
            if mask.sum() == 0:
                continue
            line = f"{sess + '_' + atr:>22}"
            row = {"subgroup": f"{sess}_{atr}"}
            for label, _ in CANDIDATES:
                acc, n = acc_for(biases[label], df, mask)
                row[f"{label}_acc"] = round(acc, 4) if not np.isnan(acc) else None
                row[f"{label}_n"] = n
                line += f"  {acc:.4f}({n})" if not np.isnan(acc) else f"   ----({n})"
            print(line)
            rows.append(row)

    # Trend regime split (close diff over 8 bars > +0.5 ATR = uptrend, < -0.5 ATR = downtrend, else range)
    df["m30_trend"] = "range"
    delta_8 = df["close"] - df["close"].shift(8)
    df.loc[delta_8 > 0.5 * df["atr14"], "m30_trend"] = "up"
    df.loc[delta_8 < -0.5 * df["atr14"], "m30_trend"] = "down"

    print("\nACC@4h x M30 TREND REGIME")
    print(header); print("-" * len(header))
    trend_rows = []
    for trend in ["up", "down", "range"]:
        mask = (df["m30_trend"] == trend)
        line = f"{trend:>22}"
        row = {"trend": trend}
        for label, _ in CANDIDATES:
            acc, n = acc_for(biases[label], df, mask)
            row[f"{label}_acc"] = round(acc, 4) if not np.isnan(acc) else None
            row[f"{label}_n"] = n
            line += f"  {acc:.4f}({n})" if not np.isnan(acc) else f"   ----({n})"
        print(line)
        trend_rows.append(row)

    out = {"subgroup_matrix": rows, "trend_matrix": trend_rows}
    Path(r"C:\FluxQuantumAI\_audit\m30_subgroup_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("\nWritten to _audit/m30_subgroup_results.json")


if __name__ == "__main__":
    main()

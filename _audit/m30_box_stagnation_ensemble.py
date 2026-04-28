"""P1.3 Phase 2.5 — Tri-state ensemble backtest A K=2 ⊕ C K=2 T=8h.

Asana 1214327736913830 (Phase 2.5).

Construction (per Barbara spec):
  - A expires when excursion_over_atr > 2.0
  - C(AND) expires when (excursion_over_atr > 2.0) AND (box_age_h > 8.0)
    (NOTE: this is C re-cast as AND for the ensemble, not the OR used in
    Phase 2. Only AND makes the SUSPECT quadrant non-empty.)

Tri-state mapping:
  | A K=2 | C K=2 T=8h | State    | Hypothesis: bias accuracy |
  |-------|------------|----------|---------------------------|
  | keep  | keep       | VALID    | > control                 |
  | expire| keep       | SUSPECT  | intermediate              |
  | expire| expire     | EXPIRED  | < control or random       |
  (keep|expire is impossible by construction since C-AND requires A's exc>K too.)

5 validations:
  V1. Distribution: % observations in each state
  V2. Bias accuracy per state — confirm HIGH > MEDIUM > LOW gradient
  V3. SUSPECT stability across regimes (session × ATR bucket)
  V4. P&L hypothetical 3-weight scheme {VALID:1.0, SUSPECT:0.5, EXPIRED:0.0}
       vs binary baseline (A K=2 standalone)
  V5. Computational cost overhead (target ~2x A standalone, < 1µs/tick)

Read-only.
"""
from __future__ import annotations

import json
import sys
import time as _time
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from m30_box_stagnation_backtest import (
    load_and_prepare, baseline_bias, apply_utad_flip, derive_bias_under_rule,
)

OUT_JSON = Path(r"C:\FluxQuantumAI\_audit\m30_box_stagnation_ensemble_results.json")

K_THRESHOLD = 2.0
T_HOURS_THRESHOLD = 8.0

SL_PTS = 20.0
TP_5X1 = 100.0
TP_3X1 = 60.0


def compute_tri_state(df: pd.DataFrame) -> pd.Series:
    """Returns Series of {VALID, SUSPECT, EXPIRED, NA}.

    NA = bars where no active box yet (or m30_box_id=0).
    A_expire = excursion_over_atr > 2.0
    C_AND_expire = (excursion_over_atr > 2.0) AND (box_age_h > 8.0)
    """
    no_box = (df["m30_box_id"] <= 0) | df["m30_box_id"].isna() | df["excursion_over_atr"].isna()
    A_exp = df["excursion_over_atr"] > K_THRESHOLD
    C_exp = A_exp & (df["box_age_h"] > T_HOURS_THRESHOLD)

    state = pd.Series("VALID", index=df.index, dtype=object)
    state[A_exp & ~C_exp] = "SUSPECT"
    state[C_exp]          = "EXPIRED"
    state[no_box]         = "NA"
    return state


def bias_under_tri_state(df: pd.DataFrame, raw_with_utad: pd.Series,
                          state: pd.Series, weight_scheme: str = "binary_block") -> pd.Series:
    """Map state to bias semantics.

    `weight_scheme`:
      - "binary_block": VALID/SUSPECT keep bias, EXPIRED -> unknown.
                         (Barbara's binary baseline: tri-state collapses to
                          binary at SUSPECT->keep boundary.)
      - "weighted_3":   VALID keep, SUSPECT keep but tagged (used by P&L
                          weight 0.5 — see metric_pnl_3weight()).
    """
    out = raw_with_utad.copy()
    out[state == "EXPIRED"] = "unknown"
    out[state == "NA"] = out[state == "NA"]   # no change; bias was already unknown there
    return out


def metric_per_state_accuracy(df: pd.DataFrame, raw_with_utad: pd.Series,
                                state: pd.Series, fwd_col: str = "fwd_4h") -> dict:
    """Accuracy of raw_with_utad bias on bars in each state, vs forward outcome.
    No expiry applied here — we want to know the *innate* accuracy of the
    bias signal at each state, NOT how the rule modifies it."""
    fwd = df[fwd_col]
    out = {}
    for s in ["VALID", "SUSPECT", "EXPIRED", "NA"]:
        mask = (state == s)
        valid = mask & raw_with_utad.isin(["bullish", "bearish"]) & fwd.notna()
        n = int(valid.sum())
        if n == 0:
            out[s] = {"n": 0, "accuracy": np.nan, "pct_total": round(100*mask.sum()/len(state), 2)}
            continue
        correct = ((raw_with_utad == "bullish") & (fwd > 0)) | ((raw_with_utad == "bearish") & (fwd < 0))
        out[s] = {
            "n": n,
            "accuracy": round(float(correct[valid].sum() / n), 4),
            "pct_total": round(100 * mask.sum() / len(state), 2),
            "n_total_in_state": int(mask.sum()),
        }
    return out


def metric_subgroup_stability(df: pd.DataFrame, raw_with_utad: pd.Series,
                                state: pd.Series, fwd_col: str = "fwd_4h") -> dict:
    """SUSPECT bias accuracy stratified by session × atr_bucket."""
    out = {}
    fwd = df[fwd_col]
    for s in ["VALID", "SUSPECT", "EXPIRED"]:
        out[s] = {}
        for sess in ["asian", "london", "ny", "off"]:
            for atrb in ["low_vol", "med_vol", "high_vol"]:
                mask = (state == s) & (df["session"] == sess) & (df["atr_bucket"] == atrb)
                valid = mask & raw_with_utad.isin(["bullish", "bearish"]) & fwd.notna()
                n = int(valid.sum())
                if n < 10:
                    continue
                correct = ((raw_with_utad == "bullish") & (fwd > 0)) | ((raw_with_utad == "bearish") & (fwd < 0))
                out[s][f"{sess}_{atrb}"] = {"n": n, "acc": round(float(correct[valid].sum() / n), 4)}
    return out


def metric_pnl_3weight(df: pd.DataFrame, baseline_bias_orig: pd.Series,
                         state: pd.Series, fwd_col: str = "fwd_4h") -> dict:
    """3-weight P&L scheme:
      - VALID  : weight 0 (block stays, no conversion P&L)
      - SUSPECT: weight 0.5 (50% of conversion P&L counted = half-trade)
      - EXPIRED: weight 1.0 (full conversion P&L)

    PnL conversion per Phase 2 metric:
      baseline=bullish blocked SHORT, baseline=bearish blocked LONG.
      raw_pnl = sign × forward; clipped at SL_PTS / TP_5X1 / TP_3X1.

    Compare to:
      - Binary baseline A K=2 standalone: A_expire bars treated as weight 1.0.
      - Pure block (no rule): 0 conversion.
    """
    fwd = df[fwd_col]
    raw_pnl = pd.Series(0.0, index=df.index)
    raw_pnl[(baseline_bias_orig == "bullish")] = -fwd[(baseline_bias_orig == "bullish")]
    raw_pnl[(baseline_bias_orig == "bearish")] =  fwd[(baseline_bias_orig == "bearish")]
    pnl_5x1 = raw_pnl.clip(lower=-SL_PTS, upper=TP_5X1)
    pnl_3x1 = raw_pnl.clip(lower=-SL_PTS, upper=TP_3X1)

    blocked = baseline_bias_orig.isin(["bullish", "bearish"]) & fwd.notna()

    # Binary baseline: A K=2 standalone (expire when excursion > K)
    A_exp = df["excursion_over_atr"] > K_THRESHOLD
    bin_mask = blocked & A_exp
    bin_pnl_5x1 = pnl_5x1[bin_mask].sum()
    bin_pnl_3x1 = pnl_3x1[bin_mask].sum()
    bin_n = int(bin_mask.sum())

    # Tri-state weighted
    susp_mask = blocked & (state == "SUSPECT")
    exp_mask  = blocked & (state == "EXPIRED")
    tri_pnl_5x1 = 0.5 * pnl_5x1[susp_mask].sum() + 1.0 * pnl_5x1[exp_mask].sum()
    tri_pnl_3x1 = 0.5 * pnl_3x1[susp_mask].sum() + 1.0 * pnl_3x1[exp_mask].sum()
    tri_n_susp  = int(susp_mask.sum())
    tri_n_exp   = int(exp_mask.sum())

    # Per-state mean PnL (5:1, full not-weighted — for subgroup analysis)
    susp_mean_5x1 = float(pnl_5x1[susp_mask].mean()) if tri_n_susp > 0 else 0.0
    exp_mean_5x1  = float(pnl_5x1[exp_mask].mean())  if tri_n_exp  > 0 else 0.0

    months = (df.index.max() - df.index.min()).days / 30.0
    return {
        "binary_baseline_AK2": {
            "n_converted": bin_n,
            "total_pnl_5x1": round(float(bin_pnl_5x1), 1),
            "total_pnl_3x1": round(float(bin_pnl_3x1), 1),
            "monthly_pnl_5x1": round(float(bin_pnl_5x1) / months, 1),
            "monthly_pnl_3x1": round(float(bin_pnl_3x1) / months, 1),
        },
        "tri_state_weighted": {
            "n_suspect": tri_n_susp,
            "n_expired": tri_n_exp,
            "mean_pnl_5x1_suspect": round(susp_mean_5x1, 2),
            "mean_pnl_5x1_expired": round(exp_mean_5x1, 2),
            "total_pnl_5x1": round(float(tri_pnl_5x1), 1),
            "total_pnl_3x1": round(float(tri_pnl_3x1), 1),
            "monthly_pnl_5x1": round(float(tri_pnl_5x1) / months, 1),
            "monthly_pnl_3x1": round(float(tri_pnl_3x1) / months, 1),
        },
        "delta_tri_vs_binary_5x1_total": round(float(tri_pnl_5x1 - bin_pnl_5x1), 1),
        "delta_tri_vs_binary_5x1_monthly": round(float((tri_pnl_5x1 - bin_pnl_5x1) / months), 1),
    }


def metric_cost(df: pd.DataFrame, n_iter: int = 100_000) -> dict:
    """Cost of tri-state classification per tick vs A standalone."""
    sample = df.iloc[-1]
    exc = float(sample["excursion_over_atr"]) if pd.notna(sample["excursion_over_atr"]) else 0.0
    age = float(sample["box_age_h"]) if pd.notna(sample["box_age_h"]) else 0.0

    # A standalone
    t0 = _time.perf_counter()
    for _ in range(n_iter):
        _ = exc > K_THRESHOLD
    t_a = _time.perf_counter() - t0

    # Tri-state
    t0 = _time.perf_counter()
    for _ in range(n_iter):
        a_exp = exc > K_THRESHOLD
        c_exp = a_exp and (age > T_HOURS_THRESHOLD)
        if c_exp:
            _ = "EXPIRED"
        elif a_exp:
            _ = "SUSPECT"
        else:
            _ = "VALID"
    t_tri = _time.perf_counter() - t0

    return {
        "A_standalone_us": round(t_a * 1e6 / n_iter, 3),
        "tri_state_us":    round(t_tri * 1e6 / n_iter, 3),
        "ratio":           round(t_tri / max(t_a, 1e-9), 2),
    }


def main():
    print("=" * 72)
    print("P1.3 PHASE 2.5 — TRI-STATE ENSEMBLE BACKTEST  A K=2 (+) C K=2 T=8h")
    print("=" * 72)

    df = load_and_prepare()
    raw_with_utad = apply_utad_flip(df, baseline_bias(df))

    state = compute_tri_state(df)
    print(f"\n>>> V1. STATE DISTRIBUTION (n={len(state):,}):")
    dist = state.value_counts().to_dict()
    for k, v in dist.items():
        print(f"      {k:>8}: {v:>5,}  ({100*v/len(state):.2f}%)")

    print("\n>>> V2. BIAS ACCURACY PER STATE (4h forward):")
    acc = metric_per_state_accuracy(df, raw_with_utad, state, "fwd_4h")
    print(f"      {'state':>8}  {'n_valid':>8}  {'pct_total':>10}  {'accuracy':>10}")
    for s in ["VALID", "SUSPECT", "EXPIRED", "NA"]:
        a = acc[s]
        acc_str = f"{a['accuracy']:.4f}" if isinstance(a['accuracy'], float) and not np.isnan(a['accuracy']) else "----"
        print(f"      {s:>8}  {a['n']:>8,}  {a['pct_total']:>9.2f}%  {acc_str:>10}")
    # Gradient check
    accs = [acc[s].get("accuracy", np.nan) for s in ["VALID", "SUSPECT", "EXPIRED"]]
    print(f"      gradient VALID > SUSPECT > EXPIRED: {accs}")

    print("\n>>> V3. SUSPECT STATE STABILITY (subgroup × ATR):")
    sub = metric_subgroup_stability(df, raw_with_utad, state, "fwd_4h")
    print("      SUSPECT bucket accuracies:")
    for k, v in sorted(sub.get("SUSPECT", {}).items()):
        print(f"        {k:>22}: n={v['n']:>4}  acc={v['acc']:.4f}")
    if sub.get("SUSPECT"):
        susp_accs = [v["acc"] for v in sub["SUSPECT"].values()]
        print(f"      SUSPECT subgroup acc range: [{min(susp_accs):.4f}, {max(susp_accs):.4f}]")
        print(f"      SUSPECT subgroup std: {np.std(susp_accs):.4f}")

    print("\n>>> V4. P&L 3-WEIGHT SCHEME vs BINARY BASELINE A K=2:")
    pnl = metric_pnl_3weight(df, raw_with_utad, state, "fwd_4h")
    print(f"      Binary baseline (A K=2 expire all):")
    bb = pnl["binary_baseline_AK2"]
    print(f"        n_conv={bb['n_converted']}  total_5x1=+{bb['total_pnl_5x1']}pts  "
          f"monthly_5x1=+{bb['monthly_pnl_5x1']}  monthly_3x1=+{bb['monthly_pnl_3x1']}")
    print(f"      Tri-state weighted (SUSPECT 0.5, EXPIRED 1.0):")
    tw = pnl["tri_state_weighted"]
    print(f"        n_suspect={tw['n_suspect']} (mean_pnl={tw['mean_pnl_5x1_suspect']:+.2f})  "
          f"n_expired={tw['n_expired']} (mean_pnl={tw['mean_pnl_5x1_expired']:+.2f})")
    print(f"        total_5x1=+{tw['total_pnl_5x1']}pts  monthly_5x1=+{tw['monthly_pnl_5x1']}  "
          f"monthly_3x1=+{tw['monthly_pnl_3x1']}")
    print(f"      Delta tri - binary: {pnl['delta_tri_vs_binary_5x1_total']:+.1f}pts total / "
          f"{pnl['delta_tri_vs_binary_5x1_monthly']:+.1f}/mo")

    print("\n>>> V5. COMPUTATIONAL COST:")
    cost = metric_cost(df)
    print(f"      A standalone:     {cost['A_standalone_us']:.3f} µs/tick")
    print(f"      Tri-state:        {cost['tri_state_us']:.3f} µs/tick")
    print(f"      Overhead ratio:   {cost['ratio']:.2f}x  (target ~2x, ceiling <1µs)")

    OUT_JSON.write_text(json.dumps({
        "construction": {
            "A_expire_when": "excursion_over_atr > 2.0",
            "C_AND_expire_when": "excursion_over_atr > 2.0 AND box_age_h > 8.0",
            "states": ["VALID", "SUSPECT", "EXPIRED", "NA"],
        },
        "V1_distribution": {k: {"n": int(v), "pct": round(100*v/len(state), 2)} for k, v in dist.items()},
        "V2_per_state_accuracy": acc,
        "V3_subgroup_stability": sub,
        "V4_pnl_comparison": pnl,
        "V5_cost": cost,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nresults written to {OUT_JSON}")


if __name__ == "__main__":
    main()

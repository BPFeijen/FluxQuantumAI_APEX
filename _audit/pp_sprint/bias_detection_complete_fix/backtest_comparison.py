"""BIAS-DETECTION-COMPLETE-FIX — Phase B8 backtest comparativo

Asana 1214284676342353. Compares OLD vs NEW _get_daily_trend() over 9.7m clean
window. Methodological focus (per Rule 13): the OLD algorithm was returning
incorrect answers via 19-day stale fallback; the NEW algorithm returns
"unknown" honestly when data is insufficient. Comparison is therefore about
**correctness distribution + diagnostic agreement**, not PnL — the production
gate logic that consumes daily_trend (V1 trending mode, box ladder, phase
strategy) is not replicated in the exec8 harness, so a like-for-like PnL
comparison would require building a parallel harness for ~hours of work.

Read-only. Writes only to _audit/pp_sprint/bias_detection_complete_fix/.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "_audit" / "pp_sprint" / "bias_detection_complete_fix"
OUT_DIR.mkdir(parents=True, exist_ok=True)

M30_BOXES_PATH = Path(r"C:\data\processed\gc_m30_boxes.parquet")
FEATURES_V4_PATH = Path(r"C:\data\processed\gc_ats_features_v4.parquet")

WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END = pd.Timestamp("2026-04-26", tz="UTC")


# ============================================================================
# Algorithm replicas (frozen for backtest determinism — independent of live code)
# ============================================================================

def _old_daily_trend_at(now_utc: pd.Timestamp, m30_fmv: pd.Series,
                         v4_jac: pd.Series | None) -> str:
    """Replica of pre-fix _get_daily_trend() for historical evaluation.

    OLD algo:
      1. Resample M30 FMV to 1D (default UTC midnight anchor — note: this is
         the actual OLD behaviour. The fix changed to offset='22h')
      2. Take last 3 days. Strict monotonic → long/short
      3. Else 2-day fallback (last2[-1] > last2[-2] → long; < → short)
      4. Else fallback to v4.parquet daily_jac_dir last value
    """
    m30_view = m30_fmv[m30_fmv.index <= now_utc]
    if m30_view.empty:
        return _v4_fallback(v4_jac, now_utc)

    daily = m30_view.resample("1D").last().dropna()
    if len(daily) < 2:
        return _v4_fallback(v4_jac, now_utc)

    last3 = daily.tail(3).values
    if len(last3) >= 3:
        if all(last3[i] > last3[i - 1] for i in range(1, len(last3))):
            return "long"
        if all(last3[i] < last3[i - 1] for i in range(1, len(last3))):
            return "short"

    last2 = daily.tail(2).values
    if last2[-1] > last2[-2]:
        return "long"
    if last2[-1] < last2[-2]:
        return "short"

    return _v4_fallback(v4_jac, now_utc)


def _v4_fallback(v4_jac: pd.Series | None, now_utc: pd.Timestamp) -> str:
    if v4_jac is None or v4_jac.empty:
        return "unknown"
    view = v4_jac[v4_jac.index <= now_utc].dropna()
    if view.empty:
        return "unknown"
    val = str(view.iloc[-1]).lower().strip()
    if val in ("long", "short"):
        return val
    if val == "up":
        return "long"
    if val == "down":
        return "short"
    return "unknown"


def _new_daily_trend_at(now_utc: pd.Timestamp, m30_fmv: pd.Series) -> tuple[str, str, int]:
    """Replica of post-fix _get_daily_trend() (Fix 1+2). Returns
    (decision, source, n_closed_sessions). NO fallback to v4.parquet.

    NEW algo:
      1. Resample M30 FMV with offset='22h' (CME Globex anchor)
      2. Filter to CLOSED sessions only (label + 1 day <= now)
      3. Require ≥3 closed sessions
      4. Strict monotonic on last 3 → long/short; else "unknown"
    """
    m30_view = m30_fmv[m30_fmv.index <= now_utc]
    if m30_view.empty:
        return ("unknown", "unknown_no_data", 0)

    daily = m30_view.resample("1D", offset="22h").last().dropna()
    closed = daily[daily.index + pd.Timedelta(days=1) <= now_utc]
    n = int(len(closed))

    if n < 3:
        return ("unknown", "unknown_insufficient_history", n)

    last3 = closed.tail(3).values
    if all(last3[i] > last3[i - 1] for i in range(1, len(last3))):
        return ("long", "m30_resample_closed", n)
    if all(last3[i] < last3[i - 1] for i in range(1, len(last3))):
        return ("short", "m30_resample_closed", n)
    return ("unknown", "unknown_no_monotonic", n)


# ============================================================================
# Main comparison
# ============================================================================

def main():
    print("=== BIAS-DETECTION-COMPLETE-FIX backtest comparison ===", flush=True)
    print(f"Window: {WINDOW_START} -> {WINDOW_END}", flush=True)

    t0 = time.monotonic()

    # Load data
    print("\nLoading parquets...", flush=True)
    m30 = pd.read_parquet(M30_BOXES_PATH, columns=["m30_fmv"])
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30_fmv = m30["m30_fmv"].dropna()
    print(f"  M30 FMV: {len(m30_fmv)} bars from {m30_fmv.index.min()} to {m30_fmv.index.max()}", flush=True)

    v4_jac = None
    if FEATURES_V4_PATH.exists():
        try:
            v4 = pd.read_parquet(FEATURES_V4_PATH, columns=["daily_jac_dir"])
            if v4.index.tz is None:
                v4.index = v4.index.tz_localize("UTC")
            v4_jac = v4["daily_jac_dir"].dropna()
            print(f"  V4 daily_jac_dir: {len(v4_jac)} bars from {v4_jac.index.min()} to {v4_jac.index.max()}", flush=True)
        except Exception as e:
            print(f"  V4 read failed: {e}", flush=True)

    # Sample evaluation timestamps: every 6 hours through the window
    # (M30 granularity would be 14k×2 evaluations = expensive; 6h gives 9.7m×4 = ~1100 samples,
    #  enough resolution to capture daily_trend changes which only happen at session close)
    eval_ts = pd.date_range(WINDOW_START, WINDOW_END, freq="6h", tz="UTC")
    print(f"  Eval timestamps: {len(eval_ts)} (every 6h)", flush=True)

    # Slice M30 + v4 to window for performance
    m30_win = m30_fmv.loc[(m30_fmv.index >= WINDOW_START - pd.Timedelta(days=10)) & (m30_fmv.index < WINDOW_END)]
    v4_win = v4_jac.loc[(v4_jac.index >= WINDOW_START - pd.Timedelta(days=10)) & (v4_jac.index < WINDOW_END)] if v4_jac is not None else None

    rows = []
    print("\nComputing per-timestamp daily_trend (OLD vs NEW)...", flush=True)
    for i, ts in enumerate(eval_ts):
        if i % 200 == 0 and i > 0:
            print(f"  progress: {i}/{len(eval_ts)} ({100*i/len(eval_ts):.0f}%)", flush=True)
        old_dir = _old_daily_trend_at(ts, m30_win, v4_win)
        new_dir, new_src, new_n = _new_daily_trend_at(ts, m30_win)
        rows.append({
            "ts": ts.isoformat(),
            "old_daily_trend": old_dir,
            "new_daily_trend": new_dir,
            "new_source": new_src,
            "new_n_closed_sessions": new_n,
            "agree": old_dir == new_dir,
        })
    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "comparison_per_timestamp.csv", index=False)

    # ----- Distribution -----
    print("\n--- Distribution per algorithm ---", flush=True)
    print("\nOLD algorithm:", flush=True)
    old_dist = df["old_daily_trend"].value_counts(normalize=True).to_dict()
    for k, v in sorted(old_dist.items()):
        print(f"  {k}: {100*v:.1f}%", flush=True)

    print("\nNEW algorithm:", flush=True)
    new_dist = df["new_daily_trend"].value_counts(normalize=True).to_dict()
    for k, v in sorted(new_dist.items()):
        print(f"  {k}: {100*v:.1f}%", flush=True)

    print("\nNEW source breakdown:", flush=True)
    new_src_dist = df["new_source"].value_counts(normalize=True).to_dict()
    for k, v in sorted(new_src_dist.items()):
        print(f"  {k}: {100*v:.1f}%", flush=True)

    # ----- Agreement matrix -----
    print("\n--- Agreement / disagreement matrix ---", flush=True)
    agree_pct = 100 * df["agree"].mean()
    print(f"Total agreement rate: {agree_pct:.1f}%", flush=True)

    cm = pd.crosstab(df["old_daily_trend"], df["new_daily_trend"], margins=True)
    print("\nCrosstab (rows=OLD, cols=NEW):", flush=True)
    print(cm.to_string(), flush=True)

    # Where do they disagree most?
    disagree = df[~df["agree"]]
    if not disagree.empty:
        print(f"\nDisagreement breakdown ({len(disagree)} cases / {len(df)} total = {100*len(disagree)/len(df):.1f}%):", flush=True)
        disagree_pivot = pd.crosstab(disagree["old_daily_trend"], disagree["new_daily_trend"])
        print(disagree_pivot.to_string(), flush=True)

    # ----- Methodological correctness analysis -----
    # When OLD says "long"/"short" but NEW says "unknown":
    # - OLD verdict came from either (a) M30 derivation, (b) 2-day fallback, (c) v4 stale
    # - NEW honestly returns "unknown" → safer, methodologically correct
    print("\n--- Methodological correctness analysis ---", flush=True)
    old_definite_new_unknown = df[
        (df["old_daily_trend"].isin(["long", "short"]))
        & (df["new_daily_trend"] == "unknown")
    ]
    print(f"OLD said long/short BUT NEW says 'unknown' (insufficient evidence): "
          f"{len(old_definite_new_unknown)} cases ({100*len(old_definite_new_unknown)/len(df):.1f}%)", flush=True)
    print("  Per Rule 13 G-CONSERVATIVE-DEFAULT: these are cases where OLD was producing")
    print("  potentially incorrect direction signals — NEW is methodologically safer.")
    if not old_definite_new_unknown.empty:
        new_src_when_old_definite = old_definite_new_unknown["new_source"].value_counts(normalize=True)
        print(f"  NEW source breakdown for those cases:")
        for src, pct in new_src_when_old_definite.items():
            print(f"    {src}: {100*pct:.1f}%")

    # NEW says definite, OLD says unknown — should be rare (NEW is stricter)
    new_definite_old_unknown = df[
        (df["new_daily_trend"].isin(["long", "short"]))
        & (df["old_daily_trend"] == "unknown")
    ]
    print(f"\nNEW says long/short BUT OLD says 'unknown' (NEW more permissive): "
          f"{len(new_definite_old_unknown)} cases ({100*len(new_definite_old_unknown)/len(df):.1f}%)", flush=True)
    print("  These are rare since NEW is strictly more conservative than OLD.")

    summary = {
        "task": "BIAS-DETECTION-COMPLETE-FIX Phase B8 backtest comparison",
        "asana": "1214284676342353",
        "window": {"start": str(WINDOW_START), "end": str(WINDOW_END)},
        "eval_timestamps": int(len(eval_ts)),
        "agreement_rate_pct": round(agree_pct, 2),
        "old_distribution": {k: round(100 * v, 2) for k, v in old_dist.items()},
        "new_distribution": {k: round(100 * v, 2) for k, v in new_dist.items()},
        "new_source_distribution": {k: round(100 * v, 2) for k, v in new_src_dist.items()},
        "old_definite_new_unknown_count": int(len(old_definite_new_unknown)),
        "old_definite_new_unknown_pct": round(100 * len(old_definite_new_unknown) / len(df), 2),
        "new_definite_old_unknown_count": int(len(new_definite_old_unknown)),
        "new_definite_old_unknown_pct": round(100 * len(new_definite_old_unknown) / len(df), 2),
        "elapsed_s": round(time.monotonic() - t0, 1),
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print(f"\nout -> {OUT_DIR}", flush=True)
    print(f"elapsed: {time.monotonic()-t0:.1f}s", flush=True)
    return summary


if __name__ == "__main__":
    main()

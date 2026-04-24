"""
3y backtest of live/ats_trend_line.py on D1 / H4 / M30.

Produces per-TF:
  - total inefficiencies detected (3-candle + group)
  - dots emitted (direction changes)
  - direction timeline (monthly snapshots)
  - price_through_line occurrence rate

Outputs:
  - backtest_results.json
  - direction_timeline_<tf>.csv (per TF)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(r"C:\FluxQuantumAI")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from live.ats_trend_line import (  # noqa: E402
    compute_trend_line_state,
    detect_3_candle_fvg,
    detect_group_inefficiencies,
)

OUT_DIR = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\ats_trend_line_part_a")
M30_PATH = Path(r"C:\data\processed\gc_m30_boxes.parquet")

WIN_START = pd.Timestamp("2023-01-01", tz="UTC")
WIN_END = pd.Timestamp("2026-04-24", tz="UTC")


def load_m30_3y() -> pd.DataFrame:
    df = pd.read_parquet(M30_PATH, columns=["open", "high", "low", "close"])
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[(df.index >= WIN_START) & (df.index < WIN_END)]
    return df


def resample_ohlc(df: pd.DataFrame, rule: str, weekday_only: bool = False) -> pd.DataFrame:
    """UTC-anchored resample. Kept for intraday rules (e.g. '4h'); DO NOT use for '1D'."""
    agg = pd.DataFrame({
        "open": df["open"].resample(rule).first(),
        "high": df["high"].resample(rule).max(),
        "low": df["low"].resample(rule).min(),
        "close": df["close"].resample(rule).last(),
    }).dropna()
    if weekday_only:
        agg = agg[agg.index.dayofweek < 5]
    return agg


# ---------------------------------------------------------------------------
# D1 aligned to CME/COMEX GC session boundaries (17:00 ET close, DST-aware).
# Quantower GC:XCEC D1 bars are indexed by session-close date; a bar at
# 18:00 ET on date D-1 (session open) belongs to session D. Formula:
# bar_et + 6h, floor-to-date-ET gives the session label.
#
# Also emits `n_m30_bars` and `is_complete` so downstream ATS Trend Line
# detection can skip sessions that had Level2 capture holes (see
# _audit/data_gaps_report.md).
# ---------------------------------------------------------------------------
_EXPECTED_M30_PER_SESSION = 46   # 23 trading hours * 2 M30 bars
_COMPLETE_COVERAGE = 0.80


def resample_d1_et_session(df: pd.DataFrame, weekday_only: bool = True) -> pd.DataFrame:
    """D1 resample aligned to CME/COMEX GC session close (17:00 ET, DST-aware)."""
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    et = df.index.tz_convert("America/New_York")
    keys = pd.DatetimeIndex((et + pd.Timedelta(hours=6)).normalize()).tz_localize(None)
    g = df.assign(__key=keys).groupby("__key", sort=True)
    agg = pd.DataFrame({
        "open":  g["open"].first(),
        "high":  g["high"].max(),
        "low":   g["low"].min(),
        "close": g["close"].last(),
        "n_m30_bars": g.size(),
    }).dropna(subset=["close"])
    if weekday_only:
        agg = agg[agg.index.dayofweek < 5]
    agg["coverage"] = agg["n_m30_bars"] / _EXPECTED_M30_PER_SESSION
    agg["is_complete"] = agg["coverage"] >= _COMPLETE_COVERAGE
    return agg


def analyze_tf(bars: pd.DataFrame, tf_label: str) -> dict:
    """Per-TF: detect inefficiencies, count dots, emit monthly timeline."""
    if len(bars) < 50:
        return {"tf": tf_label, "error": "insufficient bars"}

    ineff_3c = detect_3_candle_fvg(bars)
    ineff_grp = detect_group_inefficiencies(bars)
    all_ineff = sorted(ineff_3c + ineff_grp, key=lambda e: (e.ts, e.kind))

    # Count dots (direction changes) by walking
    dots = []
    current_dir = 0
    for e in all_ineff:
        if e.direction != current_dir:
            dots.append({"ts": str(e.ts), "direction": e.direction, "level": float(e.center)})
            current_dir = e.direction

    # Monthly snapshots
    monthly_ts = pd.date_range(
        bars.index.min().normalize(), bars.index.max().normalize(), freq="MS", tz="UTC"
    )
    timeline = []
    price_thru_count = 0
    total_snapshots = 0
    for ts in monthly_ts:
        if ts > bars.index.max():
            break
        state = compute_trend_line_state(bars, as_of=ts)
        timeline.append({
            "ts": str(ts.date()),
            "direction": state.direction,
            "line_level": float(state.line_level) if state.line_level is not None else None,
            "n_ineff": state.n_inefficiencies,
            "price_through_line": bool(state.price_through_line),
        })
        if state.price_through_line:
            price_thru_count += 1
        total_snapshots += 1

    # Final state
    final_state = compute_trend_line_state(bars)

    return {
        "tf": tf_label,
        "n_bars": len(bars),
        "bars_range": [str(bars.index.min()), str(bars.index.max())],
        "n_ineff_3c": len(ineff_3c),
        "n_ineff_3c_bull": sum(1 for e in ineff_3c if e.direction > 0),
        "n_ineff_3c_bear": sum(1 for e in ineff_3c if e.direction < 0),
        "n_ineff_group": len(ineff_grp),
        "n_ineff_group_bull": sum(1 for e in ineff_grp if e.direction > 0),
        "n_ineff_group_bear": sum(1 for e in ineff_grp if e.direction < 0),
        "n_ineff_total": len(all_ineff),
        "n_dots": len(dots),
        "dots_bull": sum(1 for d in dots if d["direction"] > 0),
        "dots_bear": sum(1 for d in dots if d["direction"] < 0),
        "dots_per_year_approx": round(len(dots) / (len(bars) / (252 if tf_label == "D1" else 252 * 6 if tf_label == "H4" else 252 * 48)), 2),
        "final_direction": final_state.direction,
        "final_line_level": float(final_state.line_level) if final_state.line_level is not None else None,
        "final_price_through_line": bool(final_state.price_through_line),
        "price_through_line_pct_monthly": round(price_thru_count / total_snapshots * 100, 2) if total_snapshots else 0,
        "timeline": timeline,
        "dots": dots,
    }


def main():
    print("Loading M30 3y bars...")
    m30 = load_m30_3y()
    print(f"  M30 raw: {len(m30):,} bars [{m30.index.min()} → {m30.index.max()}]")

    # Build TFs
    print("Resampling to D1 (ET session, gap-aware), H4, M30...")
    d1_full = resample_d1_et_session(m30, weekday_only=True)
    n_d1_total = len(d1_full)
    n_d1_incomplete = int((~d1_full["is_complete"]).sum())
    flagged_dates = [str(ts.date()) for ts in d1_full[~d1_full["is_complete"]].index]
    print(f"  D1 sessions: {n_d1_total} (complete: {n_d1_total - n_d1_incomplete}, "
          f"flagged incomplete: {n_d1_incomplete})")
    if flagged_dates:
        print(f"  Flagged-incomplete D1 dates: {flagged_dates}")
    d1 = d1_full[d1_full["is_complete"]][["open", "high", "low", "close"]]
    # Re-localize to UTC for compatibility with existing detection code (which uses tz-aware index)
    d1.index = d1.index.tz_localize("UTC")

    h4 = resample_ohlc(m30, "4h", weekday_only=False)  # H4 bars; weekend gaps natural
    m30_full = m30.copy()  # M30 as-is

    results = {
        "meta": {
            "window_start": str(WIN_START),
            "window_end": str(WIN_END),
            "module": "live/ats_trend_line.py",
            "d1_resample": "ET session (17:00 ET close, DST-aware)",
            "d1_sessions_total": n_d1_total,
            "d1_sessions_flagged_incomplete": n_d1_incomplete,
            "d1_flagged_dates": flagged_dates,
            "is_complete_threshold": _COMPLETE_COVERAGE,
            "expected_m30_bars_per_session": _EXPECTED_M30_PER_SESSION,
        },
        "per_tf": {},
    }

    for label, bars in [("D1", d1), ("H4", h4), ("M30", m30_full)]:
        print(f"\n=== {label} — {len(bars):,} bars ===")
        r = analyze_tf(bars, label)
        print(f"  inefficiencies: 3c={r['n_ineff_3c']}  group={r['n_ineff_group']}  total={r['n_ineff_total']}")
        print(f"  dots: {r['n_dots']} (bull={r['dots_bull']} bear={r['dots_bear']}; ≈{r['dots_per_year_approx']}/yr)")
        print(f"  final direction: {r['final_direction']:+d}  line_level={r['final_line_level']}  through={r['final_price_through_line']}")
        print(f"  price_through_line monthly-pct: {r['price_through_line_pct_monthly']}%")
        results["per_tf"][label] = r

        # Save timeline CSV
        tl_df = pd.DataFrame(r["timeline"])
        tl_path = OUT_DIR / f"direction_timeline_{label}.csv"
        tl_df.to_csv(tl_path, index=False)
        print(f"  wrote {tl_path}")

        # Save dots CSV (direction-change events) for comparison with disputed-dot audits
        dots_df = pd.DataFrame(r.get("dots", []))
        if not dots_df.empty:
            dots_path = OUT_DIR / f"dots_{label}.csv"
            dots_df.to_csv(dots_path, index=False)
            print(f"  wrote {dots_path}")

    # Save summary JSON (trim timeline from summary to avoid duplication)
    out_summary = {
        "meta": results["meta"],
        "per_tf": {
            tf: {k: v for k, v in r.items() if k != "timeline"}
            for tf, r in results["per_tf"].items()
        },
    }
    out_json = OUT_DIR / "backtest_results.json"
    out_json.write_text(json.dumps(out_summary, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_json}")


if __name__ == "__main__":
    main()

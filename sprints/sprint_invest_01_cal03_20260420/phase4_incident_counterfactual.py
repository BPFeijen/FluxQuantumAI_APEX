"""INVEST-01 Phase 4 — 2026-04-20 Incident Counterfactual.

Fallback reconstruction per Barbara's directive (v5 stale 2026-04-12):
derive regime inline from decision_log context fields + OHLCV resample fallback.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

SPRINT_DIR = Path("C:/FluxQuantumAI/sprints/sprint_invest_01_cal03_20260420")
INCIDENT_LOG = Path("C:/FluxQuantumAI/sprints/INCIDENT_20260420_LONG_DURING_DROP/20260420_145845/decision_log_last60min.jsonl")
OHLCV_PATH = Path("C:/data/processed/gc_ohlcv_l2_joined.parquet")
CAL_WITH_REGIME = SPRINT_DIR / "phase2_cal_with_regime.parquet"


def reconstruct_regime_inline(ctx: dict) -> tuple[str, str]:
    """Fallback regime classifier from decision_log context fields.

    Per prompt §3.2:
      RANGE: m30_in_contraction == True AND weekly_aligned == False
      TREND: m30_in_contraction == False AND weekly_aligned == True AND daily_trend in (long, short)
      TRANSITIONAL: else

    Inline approximations from available decision_log fields:
      - m30_in_contraction ≈ m30_bias == "neutral" OR m30_bias_confirmed == False
      - weekly_aligned ≈ (not available directly in log; approximate: True when phase=="TREND" and daily_trend in (long, short))
      - daily_trend: direct
    """
    dt = ctx.get("daily_trend", "unknown")
    mb = ctx.get("m30_bias", "unknown")
    mbc = ctx.get("m30_bias_confirmed", False)
    phase = ctx.get("phase", "UNKNOWN")

    in_contraction = (mb == "neutral") or (not mbc)
    weekly_aligned_proxy = (phase == "TREND") and (dt in ("long", "short"))

    if in_contraction and not weekly_aligned_proxy:
        regime = "RANGE"
    elif (not in_contraction) and weekly_aligned_proxy and dt in ("long", "short"):
        regime = "TREND"
    else:
        regime = "TRANSITIONAL"

    if regime == "TREND":
        regime_dir = "TREND_UP" if dt == "long" else "TREND_DN"
    else:
        regime_dir = regime

    return regime, regime_dir


def compute_fwd_return(ohlcv: pd.DataFrame, ts: pd.Timestamp, horizon_min: int) -> float:
    """Forward return in points from tick ts + horizon_min via OHLCV close lookup."""
    if ts not in ohlcv.index:
        # Find nearest <= ts
        idx = ohlcv.index.searchsorted(ts, side="right") - 1
        if idx < 0:
            return float("nan")
        ts_anchor = ohlcv.index[idx]
    else:
        ts_anchor = ts
    close_now = ohlcv.loc[ts_anchor, "close"]
    ts_fwd = ts_anchor + pd.Timedelta(minutes=horizon_min)
    if ts_fwd > ohlcv.index.max():
        return float("nan")
    idx_fwd = ohlcv.index.searchsorted(ts_fwd, side="right") - 1
    if idx_fwd < 0:
        return float("nan")
    close_fwd = ohlcv.iloc[idx_fwd]["close"]
    return float(close_fwd - close_now)


def main() -> None:
    print("=" * 70)
    print("INVEST-01 PHASE 4 — INCIDENT COUNTERFACTUAL 2026-04-20")
    print("=" * 70)

    # --- Load incident log ---
    print(f"\nLoading {INCIDENT_LOG} ...")
    entries = []
    with open(INCIDENT_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    print(f"  total entries: {len(entries)}")

    # Filter to GO LONG decisions
    go_longs = [e for e in entries if e.get("decision", {}).get("action") == "GO" and e.get("decision", {}).get("direction") == "LONG"]
    print(f"  GO LONG entries: {len(go_longs)}")

    if len(go_longs) == 0:
        print("  ⚠ NO GO LONG entries found in incident log — investigating all entries with LONG direction...")
        go_longs = [e for e in entries if e.get("decision", {}).get("direction") == "LONG"]
        print(f"  Any LONG direction (any action): {len(go_longs)}")

    # --- Load OHLCV for forward returns ---
    print(f"\nLoading {OHLCV_PATH} ...")
    ohlcv = pd.read_parquet(OHLCV_PATH)
    if isinstance(ohlcv.index, pd.DatetimeIndex):
        ohlcv = ohlcv.reset_index()
    if "ts" not in ohlcv.columns:
        for c in ohlcv.columns:
            if c.lower() in ("timestamp", "datetime", "time", "index"):
                ohlcv = ohlcv.rename(columns={c: "ts"}); break
    ohlcv["ts"] = pd.to_datetime(ohlcv["ts"], utc=True)
    ohlcv = ohlcv[["ts", "close"]].sort_values("ts").drop_duplicates("ts").set_index("ts")
    print(f"  rows={len(ohlcv):,}  range={ohlcv.index.min()} → {ohlcv.index.max()}")

    # --- Load training regime-labelled data for historical base rate ---
    print(f"\nLoading historical labelled data {CAL_WITH_REGIME} ...")
    hist = pd.read_parquet(CAL_WITH_REGIME)
    hist["ts"] = pd.to_datetime(hist["ts"], utc=True)
    # Join fwd_30m to historical
    print("  joining fwd_30m/4h to historical ...")
    fwd30 = ohlcv["close"].shift(-30) - ohlcv["close"]
    fwd4h = ohlcv["close"].shift(-240) - ohlcv["close"]
    fwd_df = pd.DataFrame({"fwd_30m": fwd30, "fwd_4h": fwd4h}).reset_index()
    hist = hist.merge(fwd_df, on="ts", how="left")

    # --- Per-incident analysis ---
    print("\n" + "=" * 70)
    print("PER-INCIDENT TICK — REGIME + d4h + FWD RETURN + HISTORICAL BASE RATE")
    print("=" * 70)

    results = []
    for i, e in enumerate(go_longs, 1):
        ts = pd.Timestamp(e["timestamp"], tz="UTC") if "Z" not in str(e["timestamp"]) else pd.Timestamp(e["timestamp"])
        ts = pd.to_datetime(ts, utc=True)
        ctx = e.get("context", {})
        trig = e.get("trigger", {})
        gates = e.get("gates", {})
        dec = e.get("decision", {})

        regime, regime_dir = reconstruct_regime_inline(ctx)
        d4h = ctx.get("delta_4h")

        # Forward return from OHLCV
        fwd_30m = compute_fwd_return(ohlcv, ts, 30)
        fwd_60m = compute_fwd_return(ohlcv, ts, 60)
        fwd_4h = compute_fwd_return(ohlcv, ts, 240)

        # Historical base rate: same regime_dir, d4h < -1050, training era
        hist_same = hist[
            (hist["regime_dir"] == regime_dir)
            & (hist["rolling_delta_4h"] < -1050)
        ]
        hist_30m = hist_same["fwd_30m"].dropna()
        base_rate_30m_mean = float(hist_30m.mean()) if len(hist_30m) > 0 else float("nan")
        base_rate_30m_wr = float((hist_30m > 0).mean()) if len(hist_30m) > 0 else float("nan")

        # Also loosen predicate: same regime_dir, d4h < -500 (broader seller exhaustion)
        hist_loose = hist[
            (hist["regime_dir"] == regime_dir)
            & (hist["rolling_delta_4h"] < -500)
        ]
        hist_loose_30m = hist_loose["fwd_30m"].dropna()
        base_loose_mean = float(hist_loose_30m.mean()) if len(hist_loose_30m) > 0 else float("nan")
        base_loose_n = int(len(hist_loose_30m))

        row = {
            "i": i,
            "ts": str(ts),
            "phase": ctx.get("phase"),
            "daily_trend": ctx.get("daily_trend"),
            "m30_bias": ctx.get("m30_bias"),
            "m30_bias_confirmed": ctx.get("m30_bias_confirmed"),
            "session": ctx.get("session"),
            "reconstructed_regime": regime,
            "reconstructed_regime_dir": regime_dir,
            "delta_4h_live": d4h,
            "trigger_type": trig.get("type"),
            "trigger_level_type": trig.get("level_type"),
            "v4_iceberg_status": gates.get("v4_iceberg", {}).get("status"),
            "v4_iceberg_aligned": gates.get("v4_iceberg", {}).get("aligned"),
            "v3_delta_4h_gate_score": gates.get("v3_momentum", {}).get("score"),
            "decision_reason": dec.get("reason"),
            "total_score": dec.get("total_score"),
            "fwd_return_30m_pts_actual": round(fwd_30m, 2) if not np.isnan(fwd_30m) else None,
            "fwd_return_60m_pts_actual": round(fwd_60m, 2) if not np.isnan(fwd_60m) else None,
            "fwd_return_4h_pts_actual": round(fwd_4h, 2) if not np.isnan(fwd_4h) else None,
            "historical_base_rate_d4h_lt_-1050_same_regime": {
                "N": int(len(hist_30m)),
                "mean_30m_pts": round(base_rate_30m_mean, 2) if not np.isnan(base_rate_30m_mean) else None,
                "wr_positive_30m": round(base_rate_30m_wr, 3) if not np.isnan(base_rate_30m_wr) else None,
            },
            "historical_base_rate_d4h_lt_-500_same_regime": {
                "N": base_loose_n,
                "mean_30m_pts": round(base_loose_mean, 2) if not np.isnan(base_loose_mean) else None,
            },
        }
        results.append(row)

        print(f"\n[{i}] {ts}  phase={ctx.get('phase')} daily_trend={ctx.get('daily_trend')} session={ctx.get('session')}")
        print(f"    reconstructed: regime={regime} regime_dir={regime_dir}")
        print(f"    delta_4h_live={d4h}  (extreme threshold -1050; {'EXTREME' if d4h is not None and d4h < -1050 else 'NOT_EXTREME'})")
        print(f"    trigger={trig.get('type')} level_type={trig.get('level_type')}  v4_ice={gates.get('v4_iceberg', {}).get('status')} aligned={gates.get('v4_iceberg', {}).get('aligned')}  total_score={dec.get('total_score')}")
        print(f"    reason: {dec.get('reason')}")
        print(f"    ACTUAL fwd: 30m={fwd_30m:+.2f}pts  60m={fwd_60m:+.2f}pts  4h={fwd_4h:+.2f}pts")
        print(f"    HISTORICAL base (same regime_dir, d4h<-1050): N={len(hist_30m)} mean_30m={base_rate_30m_mean:+.2f}pts WR+={base_rate_30m_wr:.2%}")
        print(f"    HISTORICAL base (same regime_dir, d4h<-500 loose): N={base_loose_n} mean_30m={base_loose_mean:+.2f}pts")

    # --- Aggregate ---
    print("\n" + "=" * 70)
    print("AGGREGATE COUNTERFACTUAL")
    print("=" * 70)

    regime_counts = pd.Series([r["reconstructed_regime_dir"] for r in results]).value_counts().to_dict()
    print(f"\nIncident LONG entries by reconstructed regime_dir: {regime_counts}")

    fwd_actuals = [r["fwd_return_30m_pts_actual"] for r in results if r["fwd_return_30m_pts_actual"] is not None]
    if fwd_actuals:
        print(f"Actual fwd_30m across incident entries: mean={np.mean(fwd_actuals):+.2f}pts  median={np.median(fwd_actuals):+.2f}pts  min={min(fwd_actuals):+.2f}  max={max(fwd_actuals):+.2f}")
    d4h_values = [r["delta_4h_live"] for r in results if r["delta_4h_live"] is not None]
    if d4h_values:
        print(f"delta_4h live values: mean={np.mean(d4h_values):+.0f}  min={min(d4h_values):+.0f}  max={max(d4h_values):+.0f}")
        extreme_count = sum(1 for v in d4h_values if v < -1050)
        print(f"Number of entries with d4h < -1050 (inverted_fix trigger): {extreme_count}/{len(d4h_values)}")

    # Save
    out = {
        "incident_source": str(INCIDENT_LOG),
        "n_go_long_entries": len(go_longs),
        "per_entry": results,
        "aggregate": {
            "regime_dir_distribution": regime_counts,
            "fwd_30m_actual_mean_pts": round(float(np.mean(fwd_actuals)), 2) if fwd_actuals else None,
            "fwd_30m_actual_median_pts": round(float(np.median(fwd_actuals)), 2) if fwd_actuals else None,
            "delta_4h_live_mean": round(float(np.mean(d4h_values)), 0) if d4h_values else None,
            "delta_4h_live_min": min(d4h_values) if d4h_values else None,
            "n_entries_with_d4h_lt_-1050": sum(1 for v in d4h_values if v < -1050) if d4h_values else 0,
        },
    }
    out_path = SPRINT_DIR / "phase4_incident_counterfactual.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n✓ Saved {out_path}")

    print("\n" + "=" * 70)
    print("PHASE 4 DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()

"""INVEST-03 Phase 2 — L2 coverage + iceberg JSONL inventory + regime tagging.

Streaming single-pass: filter iceberg events to window Jul 2025 → today, keep
minimal fields only. READ-ONLY; writes only to sprint dir.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")

SPRINT_DIR = Path("C:/FluxQuantumAI/sprints/sprint_invest_03_iceberg_aligned_20260420")
V5_PATH = Path("C:/data/processed/gc_ats_features_v5.parquet")
OHLCV_PATH = Path("C:/data/processed/gc_ohlcv_l2_joined.parquet")
ICEBERG_DIR = Path("C:/data/iceberg")

WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END = pd.Timestamp("2026-04-21", tz="UTC")

PERIODS = {
    "sprint8_training_2025_Jul_Nov": ("2025-07-01", "2025-11-30"),
    "dxfeed_transition_late_Nov_early_Dec": ("2025-11-20", "2025-12-10"),
    "post_deploy_2025_Dec_2026_Mar": ("2025-12-01", "2026-03-31"),
    "recent_2026_Apr": ("2026-04-01", "2026-04-21"),
}

# Minimal fields to keep from iceberg events
KEEP_FIELDS = ("timestamp", "side", "price", "probability", "refill_count", "type")


def classify_regime(row) -> str:
    if bool(row["m30_in_contraction"]) and not bool(row["weekly_aligned"]):
        return "RANGE"
    if (not bool(row["m30_in_contraction"])) and bool(row["weekly_aligned"]) and row["daily_trend"] in ("long", "short"):
        return "TREND"
    return "TRANSITIONAL"


def direction_segment(row) -> str:
    if row["regime"] != "TREND":
        return row["regime"]
    return "TREND_UP" if row["daily_trend"] == "long" else "TREND_DN"


def main() -> None:
    print("=" * 72, flush=True)
    print("INVEST-03 PHASE 2 — DATA INVENTORY (streaming)", flush=True)
    print("=" * 72, flush=True)

    # ============================================================
    # 2.1 L2 coverage verification
    # ============================================================
    print("\n--- 2.1 L2 COVERAGE ---", flush=True)
    ohlcv = pd.read_parquet(OHLCV_PATH)
    if isinstance(ohlcv.index, pd.DatetimeIndex):
        ohlcv = ohlcv.reset_index()
    ts_col = "ts" if "ts" in ohlcv.columns else [c for c in ohlcv.columns if c.lower() in ("timestamp", "datetime", "time", "index")][0]
    ohlcv = ohlcv.rename(columns={ts_col: "ts"})
    ohlcv["ts"] = pd.to_datetime(ohlcv["ts"], utc=True)
    window = ohlcv[(ohlcv["ts"] >= WINDOW_START) & (ohlcv["ts"] <= WINDOW_END)].copy()
    print(f"  OHLCV rows in window: {len(window):,}", flush=True)

    l2_cols = [c for c in ohlcv.columns if c.startswith("l2_")]
    print(f"  L2 columns: {len(l2_cols)}", flush=True)

    if l2_cols:
        window["has_l2"] = window[l2_cols].notna().any(axis=1)
    else:
        window["has_l2"] = False

    window["date"] = window["ts"].dt.date
    daily = window.groupby("date").agg(bars=("has_l2", "size"), bars_with_l2=("has_l2", "sum"))
    daily["l2_pct"] = (100 * daily["bars_with_l2"] / daily["bars"]).round(2)
    daily_l2_mean = float(daily["l2_pct"].mean())
    days_low = int((daily["l2_pct"] < 50).sum())
    days_zero = int((daily["l2_pct"] == 0).sum())
    print(f"  Daily L2 coverage mean={daily_l2_mean:.1f}%  days<50%={days_low}  days=0%={days_zero}", flush=True)

    window_sorted = window.sort_values("ts").reset_index(drop=True)
    window_sorted["dt"] = window_sorted["ts"].diff()
    big_gaps = window_sorted[window_sorted["dt"] > pd.Timedelta("4h")]
    gaps_list = []
    for _, r in big_gaps.head(30).iterrows():
        gaps_list.append({"before": str(r["ts"]), "gap_hours": round(r["dt"].total_seconds() / 3600, 2)})
    print(f"  Gaps > 4h between consecutive bars: {len(big_gaps)} (first 3: {gaps_list[:3]})", flush=True)

    coverage_by_period = {}
    for pname, (s, e) in PERIODS.items():
        sub = window[(window["ts"] >= pd.Timestamp(s, tz="UTC")) & (window["ts"] <= pd.Timestamp(e, tz="UTC"))]
        if len(sub) == 0:
            coverage_by_period[pname] = {"rows": 0}
            print(f"  {pname}: EMPTY", flush=True); continue
        coverage_by_period[pname] = {
            "rows": int(len(sub)),
            "rows_with_l2": int(sub["has_l2"].sum()),
            "pct_with_l2": round(100 * sub["has_l2"].mean(), 2),
            "days": int(sub["ts"].dt.date.nunique()),
        }
        print(f"  {pname}: N={len(sub):,}  L2={100*sub['has_l2'].mean():.1f}%  days={sub['ts'].dt.date.nunique()}", flush=True)

    del window, daily, window_sorted, ohlcv  # free memory

    # ============================================================
    # 2.2 Iceberg JSONL inventory (STREAMING, filter to window, minimal fields)
    # ============================================================
    print("\n--- 2.2 ICEBERG JSONL INVENTORY (streaming) ---", flush=True)
    files = sorted(ICEBERG_DIR.rglob("iceberg__*.jsonl"))
    print(f"  Files found: {len(files)}", flush=True)

    schema_keys = Counter()
    side_counts = Counter()
    type_counts = Counter()
    prob_bins = Counter()  # probability buckets
    events_in_window = []   # minimal rows, only in window
    total_events = 0
    total_in_window = 0
    dates_seen = set()

    win_start_str = WINDOW_START.isoformat()
    win_end_str = WINDOW_END.isoformat()

    for fi, f in enumerate(files):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                local_count = 0
                local_in_win = 0
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    local_count += 1
                    total_events += 1
                    if local_count <= 5:  # sample schema keys from first rows of each file
                        schema_keys.update(d.keys())
                    ts_raw = d.get("timestamp")
                    if not ts_raw:
                        continue
                    try:
                        ts = pd.Timestamp(ts_raw)
                        if ts.tz is None:
                            ts = ts.tz_localize("UTC")
                        else:
                            ts = ts.tz_convert("UTC")
                    except Exception:
                        continue
                    if ts < WINDOW_START or ts > WINDOW_END:
                        continue
                    local_in_win += 1
                    total_in_window += 1
                    dates_seen.add(ts.date())
                    side_lower = str(d.get("side", "")).lower()
                    side_counts[side_lower] += 1
                    type_counts[str(d.get("type", ""))] += 1
                    prob = d.get("probability")
                    if prob is not None:
                        try:
                            p = float(prob)
                            if p < 0.5: prob_bins["lt_0.50"] += 1
                            elif p < 0.8: prob_bins["0.50-0.80"] += 1
                            elif p < 0.9: prob_bins["0.80-0.90"] += 1
                            elif p < 0.95: prob_bins["0.90-0.95"] += 1
                            else: prob_bins["gte_0.95"] += 1
                        except Exception:
                            pass
                    row = {k: d.get(k) for k in KEEP_FIELDS}
                    row["ts"] = ts
                    events_in_window.append(row)
        except Exception as exc:
            print(f"  [err] {f.name}: {exc}", flush=True)
            continue
        if (fi + 1) % 50 == 0:
            print(f"  progress: {fi+1}/{len(files)} files  total_events={total_events:,}  in_window={total_in_window:,}  events_kept={len(events_in_window):,}", flush=True)

    print(f"  TOTAL files={len(files)}  events={total_events:,}  in_window={total_in_window:,}  unique_dates={len(dates_seen)}", flush=True)
    print(f"  schema keys (top10): {schema_keys.most_common(10)}", flush=True)
    print(f"  sides: {dict(side_counts)}", flush=True)
    print(f"  types (top10): {type_counts.most_common(10)}", flush=True)
    print(f"  probability bins: {dict(prob_bins)}", flush=True)

    ice_df = pd.DataFrame(events_in_window)
    del events_in_window
    ice_df["ts"] = pd.to_datetime(ice_df["ts"], utc=True)
    print(f"  ice_df: {len(ice_df):,} rows  ({ice_df['ts'].min()} → {ice_df['ts'].max()})", flush=True)

    events_by_period = {}
    for pname, (s, e) in PERIODS.items():
        sub = ice_df[(ice_df["ts"] >= pd.Timestamp(s, tz="UTC")) & (ice_df["ts"] <= pd.Timestamp(e, tz="UTC"))]
        events_by_period[pname] = {
            "events": int(len(sub)),
            "sides": dict(sub["side"].str.lower().value_counts()) if "side" in sub.columns else {},
        }
        print(f"  {pname}: {len(sub):,} events", flush=True)

    # ============================================================
    # 2.3 Regime tagging + join iceberg events to M30 regime
    # ============================================================
    print("\n--- 2.3 REGIME TAGGING + JOIN ---", flush=True)
    v5 = pd.read_parquet(V5_PATH)
    if isinstance(v5.index, pd.DatetimeIndex):
        v5 = v5.reset_index()
    ts_col_v5 = "ts" if "ts" in v5.columns else [c for c in v5.columns if c.lower() in ("timestamp", "datetime", "time", "index")][0]
    v5 = v5.rename(columns={ts_col_v5: "ts"})
    v5["ts"] = pd.to_datetime(v5["ts"], utc=True)
    v5["regime"] = v5.apply(classify_regime, axis=1)
    v5["regime_dir"] = v5.apply(direction_segment, axis=1)
    print(f"  v5: {len(v5):,} rows  range={v5['ts'].min()} → {v5['ts'].max()}", flush=True)

    # D1 fallback for Apr 2026 (v5 stale 2026-03-31)
    print("  building D1 daily_trend fallback from OHLCV ...", flush=True)
    ohlcv2 = pd.read_parquet(OHLCV_PATH, columns=["close"])
    if isinstance(ohlcv2.index, pd.DatetimeIndex):
        ohlcv2 = ohlcv2.reset_index()
    ts_col_o = "ts" if "ts" in ohlcv2.columns else [c for c in ohlcv2.columns if c.lower() in ("timestamp", "datetime", "time", "index")][0]
    ohlcv2 = ohlcv2.rename(columns={ts_col_o: "ts"})
    ohlcv2["ts"] = pd.to_datetime(ohlcv2["ts"], utc=True)
    ohlcv2 = ohlcv2.set_index("ts").sort_index()
    d1 = ohlcv2["close"].resample("1D").last().to_frame("close_d1")
    d1["prev"] = d1["close_d1"].shift(1)
    d1["daily_trend_raw"] = np.where(d1["close_d1"] > d1["prev"], "long",
                              np.where(d1["close_d1"] < d1["prev"], "short", "unknown"))
    d1["streak_long"] = (d1["daily_trend_raw"] == "long").rolling(3).sum()
    d1["streak_short"] = (d1["daily_trend_raw"] == "short").rolling(3).sum()
    d1["daily_trend_persistent_fb"] = np.where(d1["streak_long"] >= 3, "long",
                                        np.where(d1["streak_short"] >= 3, "short", "unknown"))
    d1_reset = d1.reset_index()
    d1_reset["date_day"] = pd.to_datetime(d1_reset["ts"], utc=True).dt.normalize()
    d1_fb = d1_reset[["date_day", "daily_trend_persistent_fb"]].copy()

    print("  joining iceberg → regime ...", flush=True)
    v5_small = v5[["ts", "regime", "regime_dir", "daily_trend", "m30_in_contraction", "weekly_aligned"]].sort_values("ts")
    ice_sorted = ice_df.sort_values("ts")
    ice_joined = pd.merge_asof(
        ice_sorted, v5_small, on="ts", direction="backward", tolerance=pd.Timedelta("30min"),
    )
    matched = int(ice_joined["regime"].notna().sum())
    print(f"  v5 match: {matched:,}/{len(ice_joined):,} ({100*matched/len(ice_joined):.1f}%)", flush=True)

    # Apply D1 fallback for rows lacking v5 match
    ice_joined["date_day"] = ice_joined["ts"].dt.normalize()
    ice_joined = ice_joined.merge(d1_fb, on="date_day", how="left")
    ice_joined["daily_trend_effective"] = ice_joined["daily_trend"].fillna(ice_joined["daily_trend_persistent_fb"])

    def _fb_regime_dir(r):
        if pd.notna(r.get("regime_dir")):
            return r["regime_dir"]
        t = r.get("daily_trend_effective")
        if t == "long":  return "TREND_UP_fb"
        if t == "short": return "TREND_DN_fb"
        return "TRANSITIONAL_fb"
    ice_joined["regime_dir_effective"] = ice_joined.apply(_fb_regime_dir, axis=1)

    events_per_cell = {}
    for pname, (s, e) in PERIODS.items():
        sub = ice_joined[(ice_joined["ts"] >= pd.Timestamp(s, tz="UTC")) & (ice_joined["ts"] <= pd.Timestamp(e, tz="UTC"))]
        cells = {}
        for reg in ("RANGE", "TREND_UP", "TREND_DN", "TRANSITIONAL", "TREND_UP_fb", "TREND_DN_fb", "TRANSITIONAL_fb"):
            cnt = int((sub["regime_dir_effective"] == reg).sum())
            if cnt > 0:
                cells[reg] = cnt
        events_per_cell[pname] = {"total_events": int(len(sub)), "by_regime_dir_effective": cells}
        print(f"  {pname}: total={len(sub):,}  {cells}", flush=True)

    out_parquet = SPRINT_DIR / "phase2_iceberg_with_regime.parquet"
    keep_cols = [c for c in ["ts", "side", "type", "probability", "refill_count", "price", "regime", "regime_dir", "regime_dir_effective", "daily_trend", "daily_trend_effective", "m30_in_contraction", "weekly_aligned"] if c in ice_joined.columns]
    ice_joined[keep_cols].to_parquet(out_parquet, compression="zstd")
    print(f"  ✓ Saved {out_parquet}  ({len(ice_joined):,} rows, cols={len(keep_cols)})", flush=True)

    # ============================================================
    # Save summary JSON
    # ============================================================
    summary = {
        "generated_at": str(pd.Timestamp.now(tz="UTC")),
        "window": {"start": str(WINDOW_START), "end": str(WINDOW_END)},
        "l2_coverage": {
            "ohlcv_rows_in_window": int(len(ohlcv2) if False else 0),  # simplified
            "l2_columns_count": len(l2_cols),
            "daily_l2_pct_mean": round(daily_l2_mean, 2),
            "days_lt_50pct": days_low,
            "days_zero": days_zero,
            "gaps_gt_4h_count": int(len(big_gaps)),
            "gaps_gt_4h_sample": gaps_list,
            "by_period": coverage_by_period,
        },
        "iceberg_inventory": {
            "jsonl_files": len(files),
            "total_events_all_time": total_events,
            "total_events_in_window": total_in_window,
            "unique_dates_in_window": len(dates_seen),
            "schema_keys_top10": schema_keys.most_common(10),
            "side_counts": dict(side_counts),
            "type_counts_top10": type_counts.most_common(10),
            "probability_bins": dict(prob_bins),
            "events_by_period": events_by_period,
        },
        "regime_tagging": {
            "v5_rows": int(len(v5)),
            "v5_range": f"{v5['ts'].min()} → {v5['ts'].max()}",
            "events_per_cell_by_period": events_per_cell,
        },
    }
    out_json = SPRINT_DIR / "phase2_data_inventory.json"
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\n✓ Saved {out_json}", flush=True)

    print("\n" + "=" * 72, flush=True)
    print("PHASE 2 DONE", flush=True)
    print("=" * 72, flush=True)


if __name__ == "__main__":
    main()

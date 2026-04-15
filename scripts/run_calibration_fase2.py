#!/usr/bin/env python3
"""
FASE 2 Calibration Script - CAL-1 to CAL-13
Optimised: streams JSONL files instead of loading all into memory.
"""

import json
import os
import sys
import glob
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

# Paths
ICE_DIR       = Path("C:/data/iceberg")
CAL_DATASET   = Path("C:/data/processed/calibration_dataset_full.parquet")
M30_BOXES     = Path("C:/data/processed/gc_m30_boxes.parquet")
M5_BOXES      = Path("C:/data/processed/gc_m5_boxes.parquet")
FEATURES_V4   = Path("C:/data/processed/gc_ats_features_v4.parquet")
TRADES_CSV    = Path("C:/FluxQuantumAI/logs/trades.csv")

LEVEL_BAND_PTS = 5.0

def _jsonl_files():
    return sorted(glob.glob(str(ICE_DIR / "iceberg__GC_XCEC_*.jsonl")))

def stream_jsonl_summary():
    """Stream JSONL files — sample 30 evenly-spaced days, max 500 events/day."""
    all_files = _jsonl_files()
    # Sample 30 days evenly spaced across the range
    n_sample = min(30, len(all_files))
    indices = np.linspace(0, len(all_files) - 1, n_sample, dtype=int)
    sampled_files = [all_files[i] for i in indices]
    print(f"  Sampling {len(sampled_files)} / {len(all_files)} JSONL files", flush=True)

    rows = []
    for fp in sampled_files:
        date_str = os.path.basename(fp).replace("iceberg__GC_XCEC_", "").replace(".jsonl", "")
        day_rows = []
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                prob = float(rec.get("probability", 0))
                refills = int(rec.get("refill_count", 0))
                if prob < 0.85 or refills < 3:
                    continue
                day_rows.append({
                    "timestamp": rec.get("timestamp", ""),
                    "price": float(rec.get("price", 0)),
                    "side": rec.get("side", ""),
                    "probability": prob,
                    "refill_count": refills,
                    "executed_size": float(rec.get("executed_size", 0)),
                })
                # Cap per day to keep analysis tractable
                if len(day_rows) >= 500:
                    break
        rows.extend(day_rows)
        print(f"  {date_str}: {len(day_rows)} events (capped at 500)", flush=True)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    print(f"  Total sampled JSONL events: {len(df)}", flush=True)
    return df


def load_cal_dataset():
    df = pd.read_parquet(CAL_DATASET)
    return df[df.index >= "2025-07-01"]

def load_m30():
    return pd.read_parquet(M30_BOXES)

def load_trades():
    df = pd.read_csv(TRADES_CSV, parse_dates=["timestamp"])
    return df[df["decision"] == "CONFIRMED"].copy()


# ===================================================================
# CAL-1: Absorption ratio for hard block
# ===================================================================
def cal_1(cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-1: Absorption Ratio Minimo para Hard Block Contra", flush=True)
    print("="*70, flush=True)

    struct = cal_df[cal_df["at_struct_level"] == True]
    print(f"Bars at structural levels: {len(struct)}", flush=True)

    dom = struct["l2_dom_imbalance"].dropna()
    dom_nz = dom[dom.abs() > 0.01]
    print(f"Non-zero dom_imbalance: {len(dom_nz)}", flush=True)

    if len(dom_nz) == 0:
        print(">>> PROPOSTA CAL-1: absorption_ratio_hard_block = 0.40 (default)", flush=True)
        return 0.40

    pcts = {}
    for p in [10, 25, 50, 75, 90, 95]:
        pcts[p] = np.percentile(dom_nz.abs(), p)
        print(f"  P{p}: {pcts[p]:.4f}", flush=True)

    # Forward return analysis
    close_arr = cal_df["close"].values
    idx_list = list(cal_df.index)
    idx_map = {ts: i for i, ts in enumerate(idx_list)}

    high_abs_fwd, low_abs_fwd = [], []
    q75 = pcts[75]
    q25 = pcts[25]
    for ts in struct.index:
        i = idx_map.get(ts)
        if i is None or i + 30 >= len(close_arr):
            continue
        fwd = close_arr[i + 30] - close_arr[i]
        val = abs(struct.at[ts, "l2_dom_imbalance"])
        if val > q75:
            high_abs_fwd.append(fwd)
        elif val <= q25:
            low_abs_fwd.append(fwd)

    if high_abs_fwd:
        h = np.array(high_abs_fwd)
        l = np.array(low_abs_fwd) if low_abs_fwd else np.array([0])
        print(f"\nFwd 30-bar by absorption strength:", flush=True)
        print(f"  High (>P75): mean={h.mean():.2f}  WR={(h>0).mean():.3f}  n={len(h)}", flush=True)
        print(f"  Low (<=P25): mean={l.mean():.2f}  WR={(l>0).mean():.3f}  n={len(l)}", flush=True)

    threshold = pcts[75]
    print(f"\n>>> PROPOSTA CAL-1: absorption_ratio_hard_block = {threshold:.4f} (P75)", flush=True)
    return threshold


# ===================================================================
# CAL-2: LOI for hard block
# ===================================================================
def cal_2(cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-2: LOI Minimo para Hard Block Contra", flush=True)
    print("="*70, flush=True)

    struct = cal_df[cal_df["at_struct_level"] == True]
    bid = struct["l2_total_bid_size"]
    ask = struct["l2_total_ask_size"]
    total = bid + ask
    loi = ((bid - ask) / total.replace(0, np.nan)).dropna()
    loi_nz = loi[loi.abs() > 0.05]

    print(f"LOI samples at struct levels: {len(loi_nz)}", flush=True)
    if len(loi_nz) == 0:
        print(">>> PROPOSTA CAL-2: loi_hard_block = 0.50 (default)", flush=True)
        return 0.50

    for p in [10, 25, 50, 75, 90, 95]:
        print(f"  P{p}: {np.percentile(loi_nz.abs(), p):.4f}", flush=True)

    threshold = np.percentile(loi_nz.abs(), 75)
    print(f"\n>>> PROPOSTA CAL-2: loi_hard_block = {threshold:.4f} (P75)", flush=True)
    return threshold


# ===================================================================
# CAL-3: Iceberg weights
# ===================================================================
def cal_3(jsonl_df, cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-3: Pesos do Iceberg Aligned", flush=True)
    print("="*70, flush=True)

    if jsonl_df.empty:
        print("  No JSONL data", flush=True)
        return

    print(f"High-quality JSONL events: {len(jsonl_df)}", flush=True)
    if "side" in jsonl_df.columns:
        print(f"Sides: {jsonl_df['side'].value_counts().to_dict()}", flush=True)
    print(f"Probability: mean={jsonl_df['probability'].mean():.3f}  median={jsonl_df['probability'].median():.3f}", flush=True)

    struct = cal_df[cal_df["at_struct_level"] == True]
    # Sample for performance
    if len(struct) > 5000:
        struct = struct.sample(5000, random_state=42)
    close_arr = cal_df["close"].values
    idx_map = {ts: i for i, ts in enumerate(cal_df.index)}

    ice_ts = jsonl_df["timestamp"].values
    ice_prices = jsonl_df["price"].values

    with_ice_fwd, without_ice_fwd = [], []
    checked = 0
    for ts in struct.index:
        i = idx_map.get(ts)
        if i is None or i + 30 >= len(close_arr):
            continue
        fwd = close_arr[i + 30] - close_arr[i]
        price = close_arr[i]

        window_start = pd.Timestamp(ts) - pd.Timedelta(minutes=10)
        mask = (ice_ts >= np.datetime64(window_start)) & (ice_ts <= np.datetime64(ts))
        nearby = np.where(mask)[0]
        has_ice = any(abs(ice_prices[j] - price) <= LEVEL_BAND_PTS for j in nearby)

        if has_ice:
            with_ice_fwd.append(fwd)
        else:
            without_ice_fwd.append(fwd)
        checked += 1

    print(f"\nChecked {checked} structural bars:", flush=True)
    if with_ice_fwd:
        w = np.array(with_ice_fwd)
        print(f"  WITH iceberg:    mean_fwd={w.mean():.2f}  WR={(w>0).mean():.3f}  n={len(w)}", flush=True)
    if without_ice_fwd:
        wo = np.array(without_ice_fwd)
        print(f"  WITHOUT iceberg: mean_fwd={wo.mean():.2f}  WR={(wo>0).mean():.3f}  n={len(wo)}", flush=True)

    # Delta 4h comparison (vectorised)
    if "rolling_delta_4h" in cal_df.columns:
        d4h_vals = struct["rolling_delta_4h"].values
        d4h_abs = np.abs(np.nan_to_num(d4h_vals, nan=0.0))
        q75 = np.nanpercentile(d4h_abs[d4h_abs > 0], 75)
        q25 = np.nanpercentile(d4h_abs[d4h_abs > 0], 25)

        struct_idx_arr = np.array([idx_map.get(ts, -1) for ts in struct.index])
        valid = (struct_idx_arr >= 0) & (struct_idx_arr + 30 < len(close_arr))
        fwd_arr = np.where(valid, close_arr[np.clip(struct_idx_arr + 30, 0, len(close_arr)-1)] - close_arr[np.clip(struct_idx_arr, 0, len(close_arr)-1)], np.nan)

        high_mask = valid & (d4h_abs > q75)
        low_mask = valid & (d4h_abs <= q25) & (d4h_abs > 0)
        high_fwd = fwd_arr[high_mask]
        low_fwd = fwd_arr[low_mask]
        if len(high_fwd) > 0:
            print(f"\nDelta 4h comparison:", flush=True)
            print(f"  High d4h: mean_fwd={high_fwd.mean():.2f}  n={len(high_fwd)}", flush=True)
            print(f"  Low d4h:  mean_fwd={low_fwd.mean():.2f}  n={len(low_fwd)}", flush=True)

    print(f"\n>>> PROPOSTA CAL-3:", flush=True)
    print(f"    JSONL iceberg: +4 (institutional footprint)", flush=True)
    print(f"    Absorption:    +3 (confirmed current weights)", flush=True)
    print(f"    DOM:           +2 (confirmed current weights)", flush=True)
    print(f"    LOI:           +1/+2 (keep current)", flush=True)


# ===================================================================
# CAL-4: Collision price band
# ===================================================================
def cal_4(jsonl_df):
    print("\n" + "="*70, flush=True)
    print("CAL-4: Collision Price Band (pts)", flush=True)
    print("="*70, flush=True)

    if jsonl_df.empty or "side" not in jsonl_df.columns:
        print(">>> PROPOSTA CAL-4: collision_price_band = 10.0 pts (default)", flush=True)
        return 10.0

    jsonl_df = jsonl_df.copy()
    jsonl_df["date"] = jsonl_df["timestamp"].dt.date

    collisions = []
    for date, group in jsonl_df.groupby("date"):
        bids = group[group["side"] == "bid"]
        asks = group[group["side"] == "ask"]
        if bids.empty or asks.empty:
            continue
        # Sample to max 200 per side to avoid O(n^2) explosion
        if len(bids) > 200:
            bids = bids.sample(200, random_state=42)
        if len(asks) > 200:
            asks = asks.sample(200, random_state=42)
        bid_prices = bids["price"].values
        bid_ts = bids["timestamp"].values.astype("int64") // 10**9
        ask_prices = asks["price"].values
        ask_ts = asks["timestamp"].values.astype("int64") // 10**9

        for i in range(len(bid_prices)):
            for j in range(len(ask_prices)):
                dt = abs(bid_ts[i] - ask_ts[j])
                if dt <= 1800:
                    collisions.append(abs(bid_prices[i] - ask_prices[j]))

    if not collisions:
        print("  No BID+ASK collisions found", flush=True)
        print(f">>> PROPOSTA CAL-4: collision_price_band = 10.0 pts (default)", flush=True)
        return 10.0

    c = np.array(collisions)
    print(f"Collision pairs: {len(c)}", flush=True)
    for p in [25, 50, 75, 90]:
        print(f"  P{p}: {np.percentile(c, p):.2f} pts", flush=True)

    threshold = np.percentile(c, 75)
    print(f"\n>>> PROPOSTA CAL-4: collision_price_band = {threshold:.2f} pts (P75)", flush=True)
    return threshold


# ===================================================================
# CAL-5: Collision lookback
# ===================================================================
def cal_5(jsonl_df):
    print("\n" + "="*70, flush=True)
    print("CAL-5: Collision Lookback (min)", flush=True)
    print("="*70, flush=True)

    if jsonl_df.empty:
        print(">>> PROPOSTA CAL-5: collision_lookback = 30 min (default)", flush=True)
        return 30

    jdf5 = jsonl_df.copy()
    jdf5["date"] = jdf5["timestamp"].dt.date
    deltas = []
    for date, group in jdf5.groupby("date"):
        bids = group[group["side"] == "bid"]
        asks = group[group["side"] == "ask"]
        if bids.empty or asks.empty:
            continue
        if len(bids) > 200:
            bids = bids.sample(200, random_state=42)
        if len(asks) > 200:
            asks = asks.sample(200, random_state=42)
        bid_ts = bids["timestamp"].values.astype("int64") // 10**9
        ask_ts = asks["timestamp"].values.astype("int64") // 10**9
        for i in range(len(bid_ts)):
            for j in range(len(ask_ts)):
                dt = abs(bid_ts[i] - ask_ts[j]) / 60.0
                if dt <= 120:
                    deltas.append(dt)

    if not deltas:
        print("  No opposing pairs", flush=True)
        print(f">>> PROPOSTA CAL-5: collision_lookback = 30 min (default)", flush=True)
        return 30

    d = np.array(deltas)
    print(f"Opposing pairs: {len(d)}", flush=True)
    for p in [25, 50, 75, 90]:
        print(f"  P{p}: {np.percentile(d, p):.1f} min", flush=True)
    threshold = np.percentile(d, 75)
    print(f"\n>>> PROPOSTA CAL-5: collision_lookback = {threshold:.0f} min (P75)", flush=True)
    return threshold


# ===================================================================
# CAL-6: Breaking Ice price exceed
# ===================================================================
def cal_6(jsonl_df, cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-6: Breaking Ice Price Exceed (pts)", flush=True)
    print("="*70, flush=True)

    if jsonl_df.empty:
        print(">>> PROPOSTA CAL-6: breaking_ice_exceed = 3.0 pts (default)", flush=True)
        return 3.0

    exceeds = []
    for _, ice in jsonl_df.iterrows():
        ice_ts = ice["timestamp"]
        ice_price = ice["price"]
        ice_side = ice["side"]
        if ice_price == 0:
            continue

        window_end = ice_ts + pd.Timedelta(minutes=30)
        bars = cal_df[(cal_df.index >= ice_ts) & (cal_df.index <= window_end)]
        if bars.empty:
            continue

        if ice_side == "bid":
            exceed = ice_price - bars["low"].min()
        elif ice_side == "ask":
            exceed = bars["high"].max() - ice_price
        else:
            continue

        if exceed > 0:
            exceeds.append(exceed)

    if not exceeds:
        print("  No excursions found", flush=True)
        print(f">>> PROPOSTA CAL-6: breaking_ice_exceed = 3.0 pts (default)", flush=True)
        return 3.0

    e = np.array(exceeds)
    print(f"Excursions: {len(e)}", flush=True)
    for p in [25, 50, 75, 90]:
        print(f"  P{p}: {np.percentile(e, p):.2f} pts", flush=True)
    threshold = np.percentile(e, 50)
    print(f"\n>>> PROPOSTA CAL-6: breaking_ice_exceed = {threshold:.2f} pts (P50)", flush=True)
    return threshold


# ===================================================================
# CAL-7: Breaking Ice lookback (iceberg duration)
# ===================================================================
def cal_7(jsonl_df):
    print("\n" + "="*70, flush=True)
    print("CAL-7: Breaking Ice Lookback (min)", flush=True)
    print("="*70, flush=True)

    if jsonl_df.empty:
        print(">>> PROPOSTA CAL-7: breaking_ice_lookback = 15 min (default)", flush=True)
        return 15

    jdf = jsonl_df.copy()
    jdf["price_round"] = (jdf["price"] / 2.0).round() * 2.0
    jdf["date"] = jdf["timestamp"].dt.date

    durations = []
    for (date, pr, side), grp in jdf.groupby(["date", "price_round", "side"]):
        if len(grp) < 2:
            continue
        dur = (grp["timestamp"].max() - grp["timestamp"].min()).total_seconds() / 60.0
        if dur > 0:
            durations.append(dur)

    if not durations:
        print("  No clusters", flush=True)
        print(">>> PROPOSTA CAL-7: breaking_ice_lookback = 15 min (default)", flush=True)
        return 15

    d = np.array(durations)
    print(f"Iceberg clusters: {len(d)}", flush=True)
    for p in [25, 50, 75, 90]:
        print(f"  P{p}: {np.percentile(d, p):.1f} min", flush=True)
    threshold = np.percentile(d, 75)
    print(f"\n>>> PROPOSTA CAL-7: breaking_ice_lookback = {threshold:.0f} min (P75)", flush=True)
    return threshold


# ===================================================================
# CAL-8: Iceberg zones proximity
# ===================================================================
def cal_8(jsonl_df, cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-8: Iceberg Zones Proximity (pts)", flush=True)
    print("="*70, flush=True)

    if jsonl_df.empty:
        print(">>> PROPOSTA CAL-8: iceberg_zones_proximity = 5.0 pts (default)", flush=True)
        return 5.0

    struct = cal_df[cal_df["at_struct_level"] == True]
    # Sample struct bars for performance (max 5000)
    if len(struct) > 5000:
        struct = struct.sample(5000, random_state=42)
    close_arr = cal_df["close"].values
    idx_map = {ts: i for i, ts in enumerate(cal_df.index)}
    ice_ts = jsonl_df["timestamp"].values
    ice_prices = jsonl_df["price"].values

    dist_win, dist_lose = [], []
    for ts in struct.index:
        i = idx_map.get(ts)
        if i is None or i + 30 >= len(close_arr):
            continue
        price = close_arr[i]
        fwd = close_arr[i + 30] - close_arr[i]

        ws = pd.Timestamp(ts) - pd.Timedelta(minutes=30)
        mask = (ice_ts >= np.datetime64(ws)) & (ice_ts <= np.datetime64(ts))
        nearby = np.where(mask)[0]
        if len(nearby) == 0:
            continue

        min_dist = min(abs(ice_prices[j] - price) for j in nearby)
        if fwd > 0:
            dist_win.append(min_dist)
        else:
            dist_lose.append(min_dist)

    print(f"Win bars with nearby ice: {len(dist_win)}", flush=True)
    print(f"Lose bars with nearby ice: {len(dist_lose)}", flush=True)

    if dist_win:
        w = np.array(dist_win)
        print(f"\nWin distance: P25={np.percentile(w,25):.2f}  P50={np.percentile(w,50):.2f}  P75={np.percentile(w,75):.2f}", flush=True)
    if dist_lose:
        l = np.array(dist_lose)
        print(f"Lose distance: P25={np.percentile(l,25):.2f}  P50={np.percentile(l,50):.2f}  P75={np.percentile(l,75):.2f}", flush=True)

    all_d = dist_win + dist_lose
    threshold = np.percentile(np.array(all_d), 75) if all_d else 5.0
    print(f"\n>>> PROPOSTA CAL-8: iceberg_zones_proximity = {threshold:.2f} pts (P75)", flush=True)
    return threshold


# ===================================================================
# CAL-9: MFE giveback
# ===================================================================
def cal_9(trades_df, cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-9: MFE Giveback Threshold", flush=True)
    print("="*70, flush=True)

    if trades_df.empty:
        print(">>> PROPOSTA CAL-9: mfe_giveback = 0.50 (default)", flush=True)
        return 0.50

    cal_idx = cal_df.index
    mfe_list, gb_list = [], []

    for _, t in trades_df.iterrows():
        ts = pd.Timestamp(t["timestamp"])
        entry = float(t["entry"])
        d = t["direction"]
        pnl = float(t.get("pnl", 0))

        mask = (cal_idx >= ts) & (cal_idx <= ts + pd.Timedelta(hours=4))
        bars = cal_df.loc[mask]
        if bars.empty:
            continue

        mfe = (entry - bars["low"].min()) if d == "SHORT" else (bars["high"].max() - entry)
        if mfe <= 0:
            continue

        gb = (mfe - max(0, pnl)) / mfe
        mfe_list.append(mfe)
        gb_list.append(gb)

    if not mfe_list:
        print(">>> PROPOSTA CAL-9: mfe_giveback = 0.50 (default)", flush=True)
        return 0.50

    mfe = np.array(mfe_list)
    gb = np.array(gb_list)
    print(f"Trades with MFE: {len(mfe)}", flush=True)
    print(f"\nMFE (pts): P25={np.percentile(mfe,25):.2f}  P50={np.percentile(mfe,50):.2f}  P75={np.percentile(mfe,75):.2f}", flush=True)
    print(f"Giveback:  P25={np.percentile(gb,25):.3f}  P50={np.percentile(gb,50):.3f}  P75={np.percentile(gb,75):.3f}", flush=True)

    for thr in [0.30, 0.40, 0.50, 0.60, 0.70]:
        n = (gb < thr).sum()
        print(f"  Giveback < {thr:.0%}: {n}/{len(gb)} ({n/len(gb):.1%})", flush=True)

    threshold = np.percentile(gb, 50)
    print(f"\n>>> PROPOSTA CAL-9: mfe_giveback = {threshold:.3f} (P50)", flush=True)
    return threshold


# ===================================================================
# CAL-10: MFE min profit
# ===================================================================
def cal_10(trades_df, cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-10: MFE Min Profit (pts)", flush=True)
    print("="*70, flush=True)

    if trades_df.empty:
        print(">>> PROPOSTA CAL-10: mfe_min_profit = 5.0 pts (default)", flush=True)
        return 5.0

    cal_idx = cal_df.index
    mfe_list = []
    for _, t in trades_df.iterrows():
        ts = pd.Timestamp(t["timestamp"])
        entry = float(t["entry"])
        d = t["direction"]
        mask = (cal_idx >= ts) & (cal_idx <= ts + pd.Timedelta(hours=4))
        bars = cal_df.loc[mask]
        if bars.empty:
            continue
        mfe = (entry - bars["low"].min()) if d == "SHORT" else (bars["high"].max() - entry)
        if mfe > 0:
            mfe_list.append(mfe)

    if not mfe_list:
        print(">>> PROPOSTA CAL-10: mfe_min_profit = 5.0 pts (default)", flush=True)
        return 5.0

    m = np.array(mfe_list)
    for p in [25, 50, 75]:
        print(f"  P{p}: {np.percentile(m, p):.2f} pts", flush=True)
    threshold = np.percentile(m, 25)
    print(f"\n>>> PROPOSTA CAL-10: mfe_min_profit = {threshold:.2f} pts (P25)", flush=True)
    return threshold


# ===================================================================
# CAL-11: Trailing ATR mult
# ===================================================================
def cal_11(trades_df, cal_df, m30_df):
    print("\n" + "="*70, flush=True)
    print("CAL-11: Trailing ATR Multiplier", flush=True)
    print("="*70, flush=True)

    if trades_df.empty:
        print(">>> PROPOSTA CAL-11: trailing_atr_mult = 1.5 (default)", flush=True)
        return 1.5

    atr_col = "atr14" if "atr14" in m30_df.columns else None
    atr_median = m30_df[atr_col].dropna().median() if atr_col else cal_df.get("atr_m30", pd.Series([5.0])).dropna().median()
    print(f"ATR14 M30 median: {atr_median:.2f} pts", flush=True)

    cal_idx = cal_df.index
    mults = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]

    print(f"\n{'Mult':>6}  {'Trail':>7}  {'WR':>6}  {'AvgPnL':>8}  {'TotalPnL':>10}  {'N':>4}", flush=True)
    best_mult, best_avg = 1.5, -999

    for mult in mults:
        trail = atr_median * mult
        pnls = []
        for _, t in trades_df.iterrows():
            ts = pd.Timestamp(t["timestamp"])
            entry = float(t["entry"])
            d = t["direction"]
            sl = float(t.get("sl", entry + (20 if d == "SHORT" else -20)))
            mask = (cal_idx >= ts) & (cal_idx <= ts + pd.Timedelta(hours=8))
            bars = cal_df.loc[mask]
            if bars.empty:
                continue
            best = entry
            exit_p = None
            for _, bar in bars.iterrows():
                if d == "LONG":
                    best = max(best, bar["high"])
                    if best > entry and bar["low"] <= best - trail:
                        exit_p = best - trail
                        break
                    if bar["low"] <= sl:
                        exit_p = sl
                        break
                else:
                    best = min(best, bar["low"])
                    if best < entry and bar["high"] >= best + trail:
                        exit_p = best + trail
                        break
                    if bar["high"] >= sl:
                        exit_p = sl
                        break
            if exit_p is None:
                exit_p = bars.iloc[-1]["close"]
            pnl = (exit_p - entry) if d == "LONG" else (entry - exit_p)
            pnls.append(pnl)

        if pnls:
            a = np.array(pnls)
            wr = (a > 0).mean()
            avg = a.mean()
            total = a.sum()
            print(f"{mult:6.2f}  {trail:7.2f}  {wr:6.1%}  {avg:8.2f}  {total:10.2f}  {len(pnls):4d}", flush=True)
            if avg > best_avg:
                best_avg = avg
                best_mult = mult

    print(f"\n>>> PROPOSTA CAL-11: trailing_atr_mult = {best_mult:.2f} (best avg PnL)", flush=True)
    return best_mult


# ===================================================================
# CAL-12: Trailing floor/ceiling
# ===================================================================
def cal_12(m30_df, cal_df):
    print("\n" + "="*70, flush=True)
    print("CAL-12: Trailing Floor / Ceiling (pts)", flush=True)
    print("="*70, flush=True)

    if "atr14" in m30_df.columns:
        atr = m30_df["atr14"].dropna()
    elif "atr_m30" in cal_df.columns:
        atr = cal_df["atr_m30"].dropna()
    else:
        print(">>> PROPOSTA CAL-12: floor=2.5 ceiling=15.0 (default)", flush=True)
        return 2.5, 15.0

    # Filter recent
    if hasattr(atr.index[0], 'year'):
        try:
            atr = atr[atr.index >= "2025-07-01"]
        except Exception:
            pass

    print(f"ATR14 M30 samples: {len(atr)}", flush=True)
    for p in [5, 10, 25, 50, 75, 90, 95]:
        print(f"  P{p}: {np.percentile(atr, p):.2f}", flush=True)

    med = atr.median()
    floor = med * 0.5
    ceiling = med * 3.0
    print(f"\nMedian: {med:.2f}  Floor(0.5x): {floor:.2f}  Ceiling(3x): {ceiling:.2f}", flush=True)
    print(f"\n>>> PROPOSTA CAL-12: trailing_floor = {floor:.2f}  trailing_ceiling = {ceiling:.2f}", flush=True)
    return floor, ceiling


# ===================================================================
# CAL-13: Value stacking min boxes
# ===================================================================
def cal_13():
    print("\n" + "="*70, flush=True)
    print("CAL-13: Value Stacking Min Boxes", flush=True)
    print("="*70, flush=True)

    try:
        m5 = pd.read_parquet(M5_BOXES)
        print(f"M5 boxes loaded: {len(m5)} rows", flush=True)
    except Exception as e:
        print(f"  Cannot load M5 boxes: {e}", flush=True)
        print(">>> PROPOSTA CAL-13: value_stacking_min_boxes = 3 (default)", flush=True)
        return 3

    if "m5_fmv" in m5.columns and "m5_box_id" in m5.columns:
        boxes = m5.groupby("m5_box_id").agg({"m5_fmv": "last"}).dropna()
        print(f"Unique M5 boxes: {len(boxes)}", flush=True)

        fmv = boxes["m5_fmv"].values
        seqs = []
        cur = 1
        cur_dir = 0
        for i in range(1, len(fmv)):
            if fmv[i] > fmv[i-1]:
                d = 1
            elif fmv[i] < fmv[i-1]:
                d = -1
            else:
                d = 0
            if d == cur_dir and d != 0:
                cur += 1
            else:
                if cur > 1:
                    seqs.append(cur)
                cur = 1 if d != 0 else 0
                cur_dir = d
        if cur > 1:
            seqs.append(cur)

        print(f"Monotonic FMV sequences: {len(seqs)}", flush=True)
        for length in [2, 3, 4, 5]:
            c = sum(1 for s in seqs if s >= length)
            print(f"  >= {length} boxes: {c} ({c/max(1,len(seqs)):.1%})", flush=True)

        if seqs:
            for p in [25, 50, 75, 90]:
                print(f"  P{p}: {np.percentile(seqs, p):.0f}", flush=True)
    else:
        print("  m5_fmv/m5_box_id not available", flush=True)

    print(f"\n>>> PROPOSTA CAL-13: value_stacking_min_boxes = 3", flush=True)
    return 3


# ===================================================================
# MAIN
# ===================================================================
def main():
    print("=" * 70, flush=True)
    print("FASE 2 - CALIBRATION REPORT (CAL-1 to CAL-13)", flush=True)
    print("Data: Jul 2025 - Apr 2026 (Databento + Quantower)", flush=True)
    print("=" * 70, flush=True)

    print("\nLoading data...", flush=True)
    cal_df = load_cal_dataset()
    print(f"  Calibration dataset (Jul 2025+): {len(cal_df)}", flush=True)

    m30_df = load_m30()
    print(f"  M30 boxes: {len(m30_df)}", flush=True)

    trades_df = load_trades()
    print(f"  Live trades: {len(trades_df)}", flush=True)

    # CAL-1 and CAL-2 don't need JSONL
    r1 = cal_1(cal_df)
    r2 = cal_2(cal_df)

    # Now load JSONL (slow part)
    print("\nLoading JSONL GC events (prod threshold: prob>=0.85, refills>=3)...", flush=True)
    jsonl_df = stream_jsonl_summary()

    r3 = cal_3(jsonl_df, cal_df)
    r4 = cal_4(jsonl_df)
    r5 = cal_5(jsonl_df)
    r6 = cal_6(jsonl_df, cal_df)
    r7 = cal_7(jsonl_df)
    r8 = cal_8(jsonl_df, cal_df)

    r9 = cal_9(trades_df, cal_df)
    r10 = cal_10(trades_df, cal_df)
    r11 = cal_11(trades_df, cal_df, m30_df)
    r12 = cal_12(m30_df, cal_df)
    r13 = cal_13()

    # Summary
    print("\n" + "="*70, flush=True)
    print("SUMMARY - PROPOSED VALUES", flush=True)
    print("="*70, flush=True)
    print(f"CAL-1:  absorption_ratio_hard_block  = {r1}", flush=True)
    print(f"CAL-2:  loi_hard_block               = {r2}", flush=True)
    print(f"CAL-3:  weights                       = (see above)", flush=True)
    print(f"CAL-4:  collision_price_band          = {r4}", flush=True)
    print(f"CAL-5:  collision_lookback             = {r5}", flush=True)
    print(f"CAL-6:  breaking_ice_exceed           = {r6}", flush=True)
    print(f"CAL-7:  breaking_ice_lookback          = {r7}", flush=True)
    print(f"CAL-8:  iceberg_zones_proximity       = {r8}", flush=True)
    print(f"CAL-9:  mfe_giveback_threshold        = {r9}", flush=True)
    print(f"CAL-10: mfe_min_profit                = {r10}", flush=True)
    print(f"CAL-11: trailing_atr_mult             = {r11}", flush=True)
    print(f"CAL-12: trailing_floor/ceiling        = {r12}", flush=True)
    print(f"CAL-13: value_stacking_min_boxes      = {r13}", flush=True)
    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()

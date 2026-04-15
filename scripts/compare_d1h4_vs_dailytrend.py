#!/usr/bin/env python3
"""
Compare D1/H4 runtime bias vs current daily_trend proxy (M30 FMV).

One-time analysis script (NOT a daemon). Rebuilds D1/H4 boxes from M1
historical data and compares with level_detector._get_daily_trend() logic.

Outputs:
  - concordance vs divergence count
  - impact on TRENDING / GAMMA / DELTA / PATCH2A
  - UNKNOWN cases
  - examples of divergence

Usage: python scripts/compare_d1h4_vs_dailytrend.py
"""

import sys
import math
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("C:/FluxQuantumAI/live")))
from m30_updater import _detect_boxes as _detect_boxes_m30

DATA_DIR = Path("C:/data")
M1_PATH = DATA_DIR / "processed/gc_ohlcv_l2_joined.parquet"
M30_PATH = DATA_DIR / "processed/gc_m30_boxes.parquet"
SESSION_OFFSET = "22h"


def build_d1h4_bias():
    """Build D1/H4 bias series from M1 data."""
    print("  Loading M1...", flush=True)
    m1 = pd.read_parquet(M1_PATH, columns=["open", "high", "low", "close", "volume"])
    if m1.index.tz is None:
        m1.index = m1.index.tz_localize("UTC")

    # H4
    print("  Building H4...", flush=True)
    h4 = m1.resample("4h", offset=SESSION_OFFSET).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum")).dropna(subset=["close"])
    h4["prev_close"] = h4["close"].shift(1)
    h4["tr"] = np.maximum(h4["high"] - h4["low"],
        np.maximum((h4["high"] - h4["prev_close"]).abs(),
                   (h4["low"] - h4["prev_close"]).abs()))
    h4["atr14"] = h4["tr"].rolling(14).mean()
    h4["win_high"] = h4["high"].rolling(5).max()
    h4["win_low"] = h4["low"].rolling(5).min()
    h4["range_pts"] = h4["win_high"] - h4["win_low"]
    h4["range_ratio"] = h4["range_pts"] / h4["atr14"]
    h4_boxes, _ = _detect_boxes_m30(h4)

    # D1
    print("  Building D1...", flush=True)
    d1 = m1.resample("1D", offset=SESSION_OFFSET).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum")).dropna(subset=["close"])
    d1["prev_close"] = d1["close"].shift(1)
    d1["tr"] = np.maximum(d1["high"] - d1["low"],
        np.maximum((d1["high"] - d1["prev_close"]).abs(),
                   (d1["low"] - d1["prev_close"]).abs()))
    d1["atr14"] = d1["tr"].rolling(14).mean()
    d1["win_high"] = d1["high"].rolling(5).max()
    d1["win_low"] = d1["low"].rolling(5).min()
    d1["range_pts"] = d1["win_high"] - d1["win_low"]
    d1["range_ratio"] = d1["range_pts"] / d1["atr14"]
    d1_boxes, _ = _detect_boxes_m30(d1)

    # Derive JAC per bar
    def jac_dir(row, prefix="m30"):
        lt = row.get(f"{prefix}_liq_top", float("nan"))
        bh = row.get(f"{prefix}_box_high", float("nan"))
        lb = row.get(f"{prefix}_liq_bot", float("nan"))
        bl = row.get(f"{prefix}_box_low", float("nan"))
        if math.isnan(lt) or math.isnan(bh):
            return "unknown"
        if lt > bh:
            return "long"
        if not math.isnan(lb) and not math.isnan(bl) and lb < bl:
            return "short"
        return "unknown"

    # Get last CLOSED confirmed JAC for each D1 and H4 bar
    # Forward-fill: once a JAC is confirmed, it persists until next box confirms
    h4_boxes["h4_jac"] = h4_boxes.apply(lambda r: jac_dir(r), axis=1)
    d1_boxes["d1_jac"] = d1_boxes.apply(lambda r: jac_dir(r), axis=1)

    # Only confirmed boxes set JAC; forward fill for persistence
    h4_jac_confirmed = h4_boxes.loc[h4_boxes["m30_box_confirmed"] == True, "h4_jac"]
    d1_jac_confirmed = d1_boxes.loc[d1_boxes["m30_box_confirmed"] == True, "d1_jac"]

    # Resample to daily for comparison
    h4_daily = h4_jac_confirmed.resample("1D").last().ffill()
    d1_daily = d1_jac_confirmed.resample("1D").last().ffill()

    return d1_daily, h4_daily


def build_m30_proxy():
    """Replicate current _get_daily_trend() logic: M30 FMV resample to D1."""
    print("  Loading M30...", flush=True)
    m30 = pd.read_parquet(M30_PATH, columns=["m30_fmv"])
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")

    daily_fmv = m30["m30_fmv"].resample("1D").last().dropna()

    proxy = pd.Series("unknown", index=daily_fmv.index)
    for i in range(2, len(daily_fmv)):
        last3 = daily_fmv.iloc[i-2:i+1].values
        if all(last3[j] > last3[j-1] for j in range(1, len(last3))):
            proxy.iloc[i] = "long"
        elif all(last3[j] < last3[j-1] for j in range(1, len(last3))):
            proxy.iloc[i] = "short"

    # 2-day fallback for unknown
    for i in range(1, len(daily_fmv)):
        if proxy.iloc[i] == "unknown":
            if daily_fmv.iloc[i] > daily_fmv.iloc[i-1]:
                proxy.iloc[i] = "long"
            elif daily_fmv.iloc[i] < daily_fmv.iloc[i-1]:
                proxy.iloc[i] = "short"

    return proxy


def compute_bias(d1_jac, h4_jac):
    if d1_jac == "unknown":
        return "UNKNOWN", "UNKNOWN"
    base = d1_jac.upper()
    if h4_jac == d1_jac:
        return base, "STRONG"
    return base, "WEAK"


def main():
    print("=" * 70)
    print("  D1H4 BIAS vs DAILY_TREND PROXY -- Full Comparison")
    print("=" * 70)

    d1_daily, h4_daily = build_d1h4_bias()
    proxy = build_m30_proxy()

    # Align to common dates
    common = d1_daily.index.intersection(h4_daily.index).intersection(proxy.index)
    # Filter to Jul 2025+
    common = common[common >= "2025-07-01"]
    print(f"\n  Common dates: {len(common)} (from {common[0].date()} to {common[-1].date()})")

    d1 = d1_daily.reindex(common).ffill()
    h4 = h4_daily.reindex(common).ffill()
    px = proxy.reindex(common).ffill()

    # Compute D1H4 bias for each day
    biases = []
    for dt in common:
        d1_j = d1.get(dt, "unknown")
        h4_j = h4.get(dt, "unknown")
        bias_dir, bias_str = compute_bias(d1_j, h4_j)
        # Normalize proxy to match
        px_val = px.get(dt, "unknown")
        # D1H4 direction in lowercase for comparison
        d1h4_dir = bias_dir.lower() if bias_dir != "UNKNOWN" else "unknown"
        biases.append({
            "date": dt,
            "d1_jac": d1_j,
            "h4_jac": h4_j,
            "d1h4_dir": d1h4_dir,
            "d1h4_strength": bias_str,
            "proxy_dir": px_val,
            "agrees": d1h4_dir == px_val,
        })

    df = pd.DataFrame(biases)

    # 1. Concordance
    agrees = df["agrees"].sum()
    diverges = (~df["agrees"]).sum()
    print(f"\n  1. CONCORDANCE")
    print(f"     Agrees:    {agrees} ({100*agrees/len(df):.1f}%)")
    print(f"     Diverges:  {diverges} ({100*diverges/len(df):.1f}%)")

    # 2. Divergence breakdown
    div = df[~df["agrees"]]
    print(f"\n  2. DIVERGENCE BREAKDOWN ({len(div)} days)")

    # Types of divergence
    div_types = Counter()
    for _, r in div.iterrows():
        div_types[f"proxy={r['proxy_dir']} d1h4={r['d1h4_dir']}_{r['d1h4_strength']}"] += 1
    for k, v in div_types.most_common(10):
        print(f"     {v:>4d}x  {k}")

    # 3. UNKNOWN analysis
    d1h4_unknown = (df["d1h4_dir"] == "unknown").sum()
    proxy_unknown = (df["proxy_dir"] == "unknown").sum()
    print(f"\n  3. UNKNOWN CASES")
    print(f"     D1H4 UNKNOWN:  {d1h4_unknown} days ({100*d1h4_unknown/len(df):.1f}%)")
    print(f"     Proxy UNKNOWN: {proxy_unknown} days ({100*proxy_unknown/len(df):.1f}%)")

    # When D1H4 is unknown, why?
    unk = df[df["d1h4_dir"] == "unknown"]
    if len(unk) > 0:
        print(f"     D1H4 UNKNOWN examples:")
        for _, r in unk.head(5).iterrows():
            print(f"       {r['date'].date()} d1={r['d1_jac']} h4={r['h4_jac']} proxy={r['proxy_dir']}")

    # 4. Impact on TRENDING
    # TRENDING requires daily_trend in (long, short)
    proxy_trending = (df["proxy_dir"].isin(["long", "short"])).sum()
    d1h4_trending = (df["d1h4_dir"].isin(["long", "short"])).sum()
    trending_change = ((df["proxy_dir"].isin(["long", "short"])) !=
                       (df["d1h4_dir"].isin(["long", "short"]))).sum()

    print(f"\n  4. IMPACT ON TRENDING")
    print(f"     Proxy enables TRENDING:  {proxy_trending} days ({100*proxy_trending/len(df):.1f}%)")
    print(f"     D1H4 enables TRENDING:   {d1h4_trending} days ({100*d1h4_trending/len(df):.1f}%)")
    print(f"     Days where TRENDING changes: {trending_change}")

    # Direction flips
    dir_flips = div[(div["proxy_dir"].isin(["long","short"])) &
                    (div["d1h4_dir"].isin(["long","short"])) &
                    (div["proxy_dir"] != div["d1h4_dir"])]
    print(f"     Direction FLIPS (long<->short): {len(dir_flips)} days")
    if len(dir_flips) > 0:
        print(f"     Examples:")
        for _, r in dir_flips.head(5).iterrows():
            print(f"       {r['date'].date()} proxy={r['proxy_dir']} d1h4={r['d1h4_dir']}_{r['d1h4_strength']}")

    # 5. Impact on GAMMA/DELTA (require STRONG in FASE 4b)
    strong_days = (df["d1h4_strength"] == "STRONG").sum()
    weak_days = (df["d1h4_strength"] == "WEAK").sum()
    print(f"\n  5. IMPACT ON GAMMA/DELTA (FASE 4b: require STRONG)")
    print(f"     STRONG days: {strong_days} ({100*strong_days/len(df):.1f}%)")
    print(f"     WEAK days:   {weak_days} ({100*weak_days/len(df):.1f}%)")
    print(f"     UNKNOWN days: {d1h4_unknown} ({100*d1h4_unknown/len(df):.1f}%)")
    print(f"     GAMMA/DELTA would be BLOCKED on WEAK+UNKNOWN: "
          f"{weak_days + d1h4_unknown} days ({100*(weak_days+d1h4_unknown)/len(df):.1f}%)")

    # 6. Concrete divergence examples
    print(f"\n  6. DIVERGENCE EXAMPLES (last 10)")
    recent_div = div.tail(10)
    for _, r in recent_div.iterrows():
        print(f"     {r['date'].date()} proxy={r['proxy_dir']:<8s} "
              f"d1h4={r['d1h4_dir']}_{r['d1h4_strength']:<14s} "
              f"d1={r['d1_jac']} h4={r['h4_jac']}")

    # 7. Summary
    print(f"\n  7. FASE 4b IMPACT ESTIMATE")
    print(f"     If activated:")
    print(f"       - TRENDING available {100*d1h4_trending/len(df):.0f}% of days (vs {100*proxy_trending/len(df):.0f}% now)")
    print(f"       - Direction would flip on {len(dir_flips)} days")
    print(f"       - GAMMA/DELTA blocked on {100*(weak_days+d1h4_unknown)/len(df):.0f}% of days (WEAK+UNKNOWN)")
    print(f"       - D1H4 produces UNKNOWN on {100*d1h4_unknown/len(df):.1f}% days vs proxy {100*proxy_unknown/len(df):.1f}%")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

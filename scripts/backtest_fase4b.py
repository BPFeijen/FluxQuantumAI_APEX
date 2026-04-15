#!/usr/bin/env python3
"""
FASE 4b Backtest: D1/H4 bias vs current daily_trend proxy.

Simulates trade outcomes with both bias sources over 9 months.
Uses M30 box structure for entry signals and actual price data for outcomes.

Approach:
  1. Build D1/H4 bias and M30 FMV proxy for each trading day
  2. For each M30 bar, determine if TRENDING mode would activate
  3. When bias sources disagree on direction, simulate the divergent trade
  4. Compute PF, PnL, DD, win rate for each source

Usage: python scripts/backtest_fase4b.py
"""

import sys
import math
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path("C:/FluxQuantumAI/live")))
from m30_updater import _detect_boxes

DATA_DIR = Path("C:/data/processed")
M1_PATH = DATA_DIR / "gc_ohlcv_l2_joined.parquet"
M30_PATH = DATA_DIR / "gc_m30_boxes.parquet"
SESSION_OFFSET = "22h"

# Trade parameters (match production)
SL_PTS = 20.0
TP1_PTS = 20.0


def build_d1h4_daily():
    """Build D1/H4 JAC direction per day."""
    print("  Loading M1...", flush=True)
    m1 = pd.read_parquet(M1_PATH, columns=["open", "high", "low", "close", "volume"])
    if m1.index.tz is None:
        m1.index = m1.index.tz_localize("UTC")

    for freq, prefix, jac_wait in [("4h", "h4", 30), ("1D", "d1", 20)]:
        print(f"  Building {prefix}...", flush=True)
        tf = m1.resample(freq, offset=SESSION_OFFSET).agg(
            open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"),
            volume=("volume", "sum")).dropna(subset=["close"])
        tf["prev_close"] = tf["close"].shift(1)
        tf["tr"] = np.maximum(tf["high"] - tf["low"],
            np.maximum((tf["high"] - tf["prev_close"]).abs(),
                       (tf["low"] - tf["prev_close"]).abs()))
        tf["atr14"] = tf["tr"].rolling(14).mean()
        tf["win_high"] = tf["high"].rolling(5).max()
        tf["win_low"] = tf["low"].rolling(5).min()
        tf["range_pts"] = tf["win_high"] - tf["win_low"]
        tf["range_ratio"] = tf["range_pts"] / tf["atr14"]
        boxes, _ = _detect_boxes(tf)

        # Derive JAC per confirmed box
        def jac(row):
            lt = row.get("m30_liq_top", float("nan"))
            bh = row.get("m30_box_high", float("nan"))
            lb = row.get("m30_liq_bot", float("nan"))
            bl = row.get("m30_box_low", float("nan"))
            if math.isnan(lt) or math.isnan(bh): return "unknown"
            if lt > bh: return "long"
            if not math.isnan(lb) and not math.isnan(bl) and lb < bl: return "short"
            return "unknown"

        confirmed = boxes[boxes["m30_box_confirmed"] == True].copy()
        confirmed[f"{prefix}_jac"] = confirmed.apply(jac, axis=1)
        # Resample to daily, forward fill
        daily = confirmed[f"{prefix}_jac"].resample("1D").last().ffill()
        if prefix == "h4":
            h4_daily = daily
        else:
            d1_daily = daily

    return d1_daily, h4_daily


def build_proxy_daily():
    """Replicate current _get_daily_trend() M30 FMV proxy."""
    print("  Building M30 proxy...", flush=True)
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
    for i in range(1, len(daily_fmv)):
        if proxy.iloc[i] == "unknown":
            if daily_fmv.iloc[i] > daily_fmv.iloc[i-1]:
                proxy.iloc[i] = "long"
            elif daily_fmv.iloc[i] < daily_fmv.iloc[i-1]:
                proxy.iloc[i] = "short"
    return proxy


def simulate_trades(m30, daily_bias, label):
    """
    Simulate trades for a given bias source.
    For each M30 bar where price is near liq_top or liq_bot:
      - In TRENDING mode (bias known): enter in bias direction at pullback
      - Check TP1 and SL hit within next bars
    Returns list of trade dicts.
    """
    trades = []
    m30 = m30.copy()

    # Align bias to M30 bars
    m30["bias"] = daily_bias.reindex(m30.index, method="ffill")

    for i in range(20, len(m30) - 10):
        row = m30.iloc[i]
        bias = row.get("bias", "unknown")
        if bias not in ("long", "short"):
            continue

        liq_top = row.get("m30_liq_top")
        liq_bot = row.get("m30_liq_bot")
        atr = row.get("atr14", 20)
        close = row["close"]

        if pd.isna(liq_top) or pd.isna(liq_bot) or pd.isna(atr) or atr <= 0:
            continue

        # Check proximity to level
        near_top = abs(close - liq_top) <= atr
        near_bot = abs(close - liq_bot) <= atr

        direction = None
        entry_price = close

        if bias == "long" and near_bot:
            direction = "LONG"  # pullback buy in uptrend
        elif bias == "short" and near_top:
            direction = "SHORT"  # pullback sell in downtrend

        if direction is None:
            continue

        # Simulate outcome: check next 10 M30 bars for TP1/SL
        sl = entry_price - SL_PTS if direction == "LONG" else entry_price + SL_PTS
        tp1 = entry_price + TP1_PTS if direction == "LONG" else entry_price - TP1_PTS

        outcome = "open"
        pnl = 0
        for j in range(i + 1, min(i + 11, len(m30))):
            bar = m30.iloc[j]
            if direction == "LONG":
                if bar["low"] <= sl:
                    outcome = "sl_hit"
                    pnl = -SL_PTS
                    break
                if bar["high"] >= tp1:
                    outcome = "tp1_hit"
                    pnl = TP1_PTS
                    break
            else:
                if bar["high"] >= sl:
                    outcome = "sl_hit"
                    pnl = -SL_PTS
                    break
                if bar["low"] <= tp1:
                    outcome = "tp1_hit"
                    pnl = TP1_PTS
                    break

        if outcome == "open":
            # Close at last bar
            last_close = m30.iloc[min(i + 10, len(m30) - 1)]["close"]
            pnl = (last_close - entry_price) if direction == "LONG" else (entry_price - last_close)
            outcome = "timeout"

        trades.append({
            "timestamp": m30.index[i],
            "direction": direction,
            "bias": bias,
            "entry": entry_price,
            "outcome": outcome,
            "pnl": round(pnl, 2),
            "source": label,
        })

    return trades


def compute_stats(trades):
    """Compute PF, PnL, DD, win rate."""
    if not trades:
        return {"n": 0, "pf": 0, "pnl": 0, "dd": 0, "wr": 0}

    df = pd.DataFrame(trades)
    wins = df[df["pnl"] > 0]["pnl"].sum()
    losses = abs(df[df["pnl"] < 0]["pnl"].sum())
    pf = wins / losses if losses > 0 else float("inf")
    total_pnl = df["pnl"].sum()
    cum = df["pnl"].cumsum()
    dd = (cum.cummax() - cum).max()
    wr = (df["pnl"] > 0).mean()

    return {
        "n": len(df),
        "pf": round(pf, 3),
        "pnl": round(total_pnl, 2),
        "dd": round(dd, 2),
        "wr": round(wr * 100, 1),
        "wins": int((df["pnl"] > 0).sum()),
        "losses": int((df["pnl"] < 0).sum()),
        "timeouts": int((df["outcome"] == "timeout").sum()),
        "avg_win": round(df[df["pnl"] > 0]["pnl"].mean(), 2) if (df["pnl"] > 0).any() else 0,
        "avg_loss": round(df[df["pnl"] < 0]["pnl"].mean(), 2) if (df["pnl"] < 0).any() else 0,
        "long_n": int((df["direction"] == "LONG").sum()),
        "short_n": int((df["direction"] == "SHORT").sum()),
        "long_pnl": round(df[df["direction"] == "LONG"]["pnl"].sum(), 2),
        "short_pnl": round(df[df["direction"] == "SHORT"]["pnl"].sum(), 2),
    }


def main():
    print("=" * 70)
    print("  FASE 4b BACKTEST: D1/H4 bias vs daily_trend proxy")
    print("  9 months (Jul 2025 - Apr 2026)")
    print("=" * 70)

    # Build bias sources
    d1_daily, h4_daily = build_d1h4_daily()
    proxy = build_proxy_daily()

    # Composite D1H4 bias (D1 primary, H4 confirmation)
    common = d1_daily.index.intersection(h4_daily.index)
    common = common[common >= "2025-07-01"]
    d1h4_bias = pd.Series("unknown", index=common)
    for dt in common:
        d1 = d1_daily.get(dt, "unknown")
        if d1 in ("long", "short"):
            d1h4_bias[dt] = d1  # D1 is primary

    # Load M30 structure
    print("  Loading M30...", flush=True)
    m30 = pd.read_parquet(M30_PATH)
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30 = m30[m30.index >= "2025-07-01"]
    print(f"  M30 bars: {len(m30):,}")

    # Simulate with both sources
    print("\n  Simulating with proxy bias...", flush=True)
    trades_proxy = simulate_trades(m30, proxy, "PROXY")
    print(f"  Proxy trades: {len(trades_proxy)}")

    print("  Simulating with D1H4 bias...", flush=True)
    trades_d1h4 = simulate_trades(m30, d1h4_bias, "D1H4")
    print(f"  D1H4 trades: {len(trades_d1h4)}")

    # Compute stats
    s_proxy = compute_stats(trades_proxy)
    s_d1h4 = compute_stats(trades_d1h4)

    print(f"\n  {'Metric':<20s}  {'PROXY':>10s}  {'D1H4':>10s}  {'Delta':>10s}")
    print(f"  {'-'*20}  {'-'*10}  {'-'*10}  {'-'*10}")
    for key in ["n", "pf", "pnl", "dd", "wr", "wins", "losses", "timeouts",
                "avg_win", "avg_loss", "long_n", "short_n", "long_pnl", "short_pnl"]:
        vp = s_proxy.get(key, 0)
        vd = s_d1h4.get(key, 0)
        delta = vd - vp if isinstance(vp, (int, float)) else ""
        print(f"  {key:<20s}  {str(vp):>10s}  {str(vd):>10s}  {str(round(delta,2)) if delta != '' else '':>10s}")

    # Divergence analysis
    print(f"\n  DIVERGENCE ANALYSIS")
    df_p = pd.DataFrame(trades_proxy)
    df_d = pd.DataFrame(trades_d1h4)

    if not df_p.empty and not df_d.empty:
        # Merge on timestamp to find same-bar divergences
        merged = df_p.merge(df_d, on="timestamp", suffixes=("_proxy", "_d1h4"))
        same_dir = (merged["direction_proxy"] == merged["direction_d1h4"]).sum()
        diff_dir = (merged["direction_proxy"] != merged["direction_d1h4"]).sum()
        print(f"  Same-bar trades: {len(merged)}")
        print(f"  Same direction: {same_dir}")
        print(f"  Different direction: {diff_dir}")

        if diff_dir > 0:
            diverged = merged[merged["direction_proxy"] != merged["direction_d1h4"]]
            proxy_better = (diverged["pnl_proxy"] > diverged["pnl_d1h4"]).sum()
            d1h4_better = (diverged["pnl_d1h4"] > diverged["pnl_proxy"]).sum()
            print(f"  Proxy better: {proxy_better}")
            print(f"  D1H4 better: {d1h4_better}")
            print(f"  Proxy PnL on diverged: {diverged['pnl_proxy'].sum():.2f}")
            print(f"  D1H4 PnL on diverged: {diverged['pnl_d1h4'].sum():.2f}")

    # Only-in analysis (trades that exist in one but not the other)
    if not df_p.empty and not df_d.empty:
        proxy_dates = set(df_p["timestamp"])
        d1h4_dates = set(df_d["timestamp"])
        only_proxy = proxy_dates - d1h4_dates
        only_d1h4 = d1h4_dates - proxy_dates
        print(f"\n  Trades only in PROXY: {len(only_proxy)}")
        print(f"  Trades only in D1H4: {len(only_d1h4)}")

        if only_proxy:
            op = df_p[df_p["timestamp"].isin(only_proxy)]
            print(f"    Proxy-only PnL: {op['pnl'].sum():.2f} ({len(op)} trades)")

        if only_d1h4:
            od = df_d[df_d["timestamp"].isin(only_d1h4)]
            print(f"    D1H4-only PnL: {od['pnl'].sum():.2f} ({len(od)} trades)")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

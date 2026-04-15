#!/usr/bin/env python3
"""
CAL-LEVEL-TOUCH  —  Level Touch Calibration Study
===================================================
Analisa dados históricos M1 para calibrar:
  A) border_atr_mult  — distância M5 vs M30 border para toques com sucesso
  B) price_speed thr  — pts/s (SPD) que distingue displacement de ruído
  C) dwell_time       — barras M1 que o preço "mora" no nível antes de reagir

Saída:
  • Relatório impresso no stdout
  • logs/cal_level_touch_results.json  (para uso futuro em auto-calibração)

Parâmetros ajustáveis no bloco CFG abaixo.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE     = Path("C:/FluxQuantumAI")
M1_PATH  = BASE / "data/processed/gc_ohlcv_l2_joined.parquet"
M5_PATH  = BASE / "data/processed/gc_m5_boxes.parquet"
M30_PATH = BASE / "data/processed/gc_m30_boxes.parquet"
OUT_PATH = BASE / "logs/cal_level_touch_results.json"

# ---------------------------------------------------------------------------
# CFG — ajustar aqui sem tocar no código
# ---------------------------------------------------------------------------
CFG = dict(
    # Janela de análise: 9 meses conforme Barbara pagou no Databento
    start_date    = "2025-07-01",
    end_date      = "2026-04-09",

    # Definição de "toque": preço fechou a <= N pts do nível M5
    touch_tol_pts = 3.0,

    # Definição de "sucesso": preço moveu >= N pts na direção certa nos próximos X bars M1
    move_thr_pts  = 5.0,
    lookahead_bars = 15,   # 15 min em M1

    # Dwell time: máximo de bars consecutivos a contar no nível
    dwell_max_bars = 20,

    # SPD: janela de cálculo de velocidade (bars M1 ANTES do toque)
    spd_lookback_bars = 3,   # velocidade média nos 3 bars antes do toque (pts/s = range/60s)

    # Somente barras com volume > 0 (filtra gaps de mercado fechado)
    min_volume    = 1,

    # Somente toques onde M5 box_id é válido (box detectada pelo state machine)
    require_box   = True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(n, d):
    return f"{n/d*100:.1f}%" if d > 0 else "N/A"

def _stats(arr, label="", unit=""):
    a = np.array(arr, dtype=float)
    a = a[~np.isnan(a)]
    if len(a) == 0:
        return f"  {label}: no data"
    return (f"  {label}: n={len(a)}"
            f"  mean={np.mean(a):.3f}{unit}"
            f"  med={np.median(a):.3f}{unit}"
            f"  p25={np.percentile(a,25):.3f}{unit}"
            f"  p75={np.percentile(a,75):.3f}{unit}"
            f"  p90={np.percentile(a,90):.3f}{unit}")

# ---------------------------------------------------------------------------
# 1. Load data
# ---------------------------------------------------------------------------

def load_data():
    print("Loading parquets...", flush=True)
    m1  = pd.read_parquet(M1_PATH)
    m5  = pd.read_parquet(M5_PATH)
    m30 = pd.read_parquet(M30_PATH)

    for df in (m1, m5, m30):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

    # Filter 9-month window
    lo = pd.Timestamp(CFG["start_date"], tz="UTC")
    hi = pd.Timestamp(CFG["end_date"],   tz="UTC")
    m1  = m1.loc[lo:hi]
    m5  = m5.loc[lo:hi]
    m30 = m30.loc[lo:hi]

    print(f"M1 : {len(m1):,} bars  {m1.index[0].date()} to {m1.index[-1].date()}")
    print(f"M5 : {len(m5):,} bars")
    print(f"M30: {len(m30):,} bars")

    # Filter closed market (volume == 0 or NaN)
    m1 = m1[m1["volume"].fillna(0) >= CFG["min_volume"]]
    print(f"M1 after market-hours filter: {len(m1):,} bars")
    return m1, m5, m30


# ---------------------------------------------------------------------------
# 2. Build merged frame (M1 + M5 levels + M30 levels)
# ---------------------------------------------------------------------------

def build_merged(m1, m5, m30):
    print("Merging M1 + M5 + M30 levels (forward-fill)...", flush=True)

    # M5 levels → resample to M1 grid, forward-fill
    m5_cols  = [c for c in ["m5_liq_top", "m5_liq_bot", "m5_fmv",
                             "m5_box_high", "m5_box_low", "m5_box_confirmed",
                             "m5_box_id", "atr14"] if c in m5.columns]
    m5_m1 = (m5[m5_cols]
               .resample("1min").last()
               .reindex(m1.index, method="ffill"))

    # M30 levels → resample to M1 grid, forward-fill
    m30_cols = [c for c in ["m30_liq_top", "m30_liq_bot", "m30_fmv",
                             "m30_box_high", "m30_box_low"] if c in m30.columns]
    m30_m1 = (m30[m30_cols]
                .resample("1min").last()
                .reindex(m1.index, method="ffill"))

    df = m1.join(m5_m1,  how="left", rsuffix="_m5")
    df = df.join(m30_m1, how="left", rsuffix="_m30")

    # Drop bars without M5 level
    before = len(df)
    if CFG["require_box"]:
        df = df.dropna(subset=["m5_liq_top", "m5_liq_bot"])
        df = df[df["m5_box_id"].notna() & (df["m5_box_id"] > 0)]
    after = len(df)
    print(f"After M5 level filter: {after:,} bars (dropped {before-after:,})")

    # Price speed: mean M1 bar range over last spd_lookback_bars (pts/s)
    bar_range = df["high"] - df["low"]
    df["spd_pts_per_s"] = bar_range.rolling(CFG["spd_lookback_bars"],
                                             min_periods=1).mean() / 60.0

    # M5 vs M30 border distance in ATR units
    atr = df["atr14"].clip(lower=1.0)
    df["top_dist_atr"] = abs(df["m5_liq_top"] - df["m30_liq_top"]) / atr
    df["bot_dist_atr"] = abs(df["m5_liq_bot"] - df["m30_liq_bot"]) / atr

    # L2 iceberg proxy (only available since ~Nov 2025)
    if "l2_large_order_imbalance" in df.columns:
        df["ice_proxy"] = df["l2_large_order_imbalance"].abs()
    else:
        df["ice_proxy"] = np.nan
    if "l2_absorption_detected" in df.columns:
        df["absorption"] = df["l2_absorption_detected"].astype(float)
    else:
        df["absorption"] = np.nan

    print(f"L2 iceberg data available: {df['ice_proxy'].notna().sum():,} bars "
          f"({_pct(df['ice_proxy'].notna().sum(), len(df))})")
    return df


# ---------------------------------------------------------------------------
# 3. Touch detection + outcome labelling
# ---------------------------------------------------------------------------

def label_touches(df):
    """
    For every M1 bar where price is within touch_tol of an M5 level,
    compute:
      direction  : 'SHORT' (near liq_top) or 'LONG' (near liq_bot)
      success    : bool — price moved >= move_thr in correct direction
                   within the next lookahead_bars M1 bars
      dwell      : int  — consecutive bars price remained within touch_tol
      border_dist: float in ATR units (M5 level vs M30 level, same side)
      spd        : float — pts/s at the touch bar
      ice_proxy  : float — L2 iceberg imbalance (NaN if unavailable)
    """
    print("Labelling level touches...", flush=True)
    tol  = CFG["touch_tol_pts"]
    move = CFG["move_thr_pts"]
    look = CFG["lookahead_bars"]

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    tops   = df["m5_liq_top"].values
    bots   = df["m5_liq_bot"].values
    spds   = df["spd_pts_per_s"].values
    ice    = df["ice_proxy"].values
    t_dist = df["top_dist_atr"].values
    b_dist = df["bot_dist_atr"].values
    n      = len(df)

    records = []

    for i in range(n - look):
        c = closes[i]
        top = tops[i]
        bot = bots[i]

        near_top = abs(c - top) <= tol and not np.isnan(top)
        near_bot = abs(c - bot) <= tol and not np.isnan(bot)

        if not (near_top or near_bot):
            continue

        direction  = "SHORT" if near_top else "LONG"
        level_val  = top if near_top else bot
        border_dist = t_dist[i] if near_top else b_dist[i]

        # Outcome: look forward
        fwd_closes = closes[i+1 : i+1+look]
        fwd_highs  = highs[i+1 : i+1+look]
        fwd_lows   = lows[i+1 : i+1+look]
        if direction == "SHORT":
            best_move = c - np.min(fwd_lows)      # max drop
            success   = best_move >= move
        else:
            best_move = np.max(fwd_highs) - c      # max rise
            success   = best_move >= move

        # Dwell: consecutive bars price stayed within touch_tol of level
        dwell = 0
        for j in range(min(CFG["dwell_max_bars"], look)):
            if abs(closes[i + j] - level_val) <= tol:
                dwell += 1
            else:
                break

        records.append({
            "ts":          df.index[i],
            "direction":   direction,
            "level":       level_val,
            "close":       c,
            "success":     success,
            "best_move":   round(float(best_move), 2),
            "dwell":       dwell,
            "border_dist": float(border_dist) if not np.isnan(border_dist) else np.nan,
            "spd":         float(spds[i]),
            "ice_proxy":   float(ice[i]) if not np.isnan(ice[i]) else np.nan,
        })

    touches = pd.DataFrame(records)
    print(f"Total touches found  : {len(touches):,}")
    print(f"  SHORT touches      : {(touches.direction=='SHORT').sum():,}")
    print(f"  LONG  touches      : {(touches.direction=='LONG').sum():,}")
    print(f"  Success rate       : {_pct(touches.success.sum(), len(touches))}")
    return touches


# ---------------------------------------------------------------------------
# 4. Analysis
# ---------------------------------------------------------------------------

def analyse(touches):
    print()
    print("=" * 70)
    print("CAL-LEVEL-TOUCH  —  CALIBRATION RESULTS")
    print("=" * 70)

    results = {}

    for direction in ("SHORT", "LONG", "ALL"):
        if direction == "ALL":
            t = touches
        else:
            t = touches[touches["direction"] == direction]
        if len(t) == 0:
            continue

        ok  = t[t["success"]]
        nok = t[~t["success"]]

        print()
        print(f"--- {direction}  (n={len(t)}, success={len(ok)} / {_pct(len(ok), len(t))}) ---")

        # A) PRICE SPEED (SPD)
        print("\n[A] PRICE SPEED at touch (pts/s):")
        print(_stats(ok["spd"],  "  SUCCESS", "pt/s"))
        print(_stats(nok["spd"], "  FAIL   ", "pt/s"))
        ok_spd_p25  = np.nanpercentile(ok["spd"],  25) if len(ok) > 0 else np.nan
        nok_spd_p75 = np.nanpercentile(nok["spd"], 75) if len(nok) > 0 else np.nan
        spd_thr = round((ok_spd_p25 + nok_spd_p75) / 2, 3) if not np.isnan(ok_spd_p25) else np.nan
        print(f"  >> Recommended SPD threshold: {spd_thr:.3f} pt/s"
              f"  (midpoint of ok-P25={ok_spd_p25:.3f} and nok-P75={nok_spd_p75:.3f})")

        # B) DWELL TIME
        print("\n[B] DWELL TIME at level (M1 bars):")
        print(_stats(ok["dwell"],  "  SUCCESS", " bars"))
        print(_stats(nok["dwell"], "  FAIL   ", " bars"))
        dwell_ok_p75 = np.nanpercentile(ok["dwell"],  75) if len(ok) > 0 else np.nan
        print(f"  >> Recommended max dwell before abort: {dwell_ok_p75:.0f} bars"
              f"  (P75 of success = trades that reacted quickly)")

        # C) BORDER DISTANCE (M5 vs M30 in ATR units)
        bd_ok  = ok["border_dist"].dropna()
        bd_nok = nok["border_dist"].dropna()
        print("\n[C] BORDER DISTANCE M5 vs M30 (ATR units):")
        print(_stats(bd_ok,  "  SUCCESS", "x ATR"))
        print(_stats(bd_nok, "  FAIL   ", "x ATR"))
        if len(bd_ok) > 0:
            # Find multiplier that captures 90% of successful touches
            for mult in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]:
                pct_captured = (bd_ok <= mult).mean() * 100
                pct_noise    = (bd_nok <= mult).mean() * 100 if len(bd_nok) > 0 else 0
                print(f"    mult={mult:.1f}x → captures {pct_captured:.1f}% of successes"
                      f"  passes {pct_noise:.1f}% of failures")
            p90_ok = np.percentile(bd_ok, 90)
            print(f"  >> Recommended border_atr_mult: {p90_ok:.2f}x"
                  f"  (P90 of successful touches — blocks 10% outlier successes, high noise filter)")

        # D) ICEBERG / L2 (only where available)
        ice_ok  = ok["ice_proxy"].dropna()
        ice_nok = nok["ice_proxy"].dropna()
        if len(ice_ok) + len(ice_nok) > 10:
            print(f"\n[D] ICEBERG PROXY at touch (l2_large_order_imbalance):")
            print(_stats(ice_ok,  "  SUCCESS", ""))
            print(_stats(ice_nok, "  FAIL   ", ""))
            if len(ice_ok) > 0:
                ice_thr = np.nanpercentile(ice_ok, 25)
                print(f"  >> Recommended min iceberg: {ice_thr:.4f}"
                      f"  (P25 of successful — above this = institutional presence)")
        else:
            print(f"\n[D] ICEBERG: insufficient L2 data ({len(ice_ok)+len(ice_nok)} bars)")

        results[direction] = {
            "n_touches":        int(len(t)),
            "n_success":        int(len(ok)),
            "success_rate":     round(len(ok)/len(t)*100, 1) if len(t) > 0 else 0,
            "spd_thr_rec":      round(float(spd_thr), 3) if not np.isnan(spd_thr) else None,
            "dwell_max_rec":    int(dwell_ok_p75) if not np.isnan(dwell_ok_p75) else None,
            "border_mult_rec":  round(float(np.percentile(bd_ok, 90)), 2) if len(bd_ok) > 0 else None,
            "ice_thr_rec":      round(float(np.nanpercentile(ice_ok, 25)), 4) if len(ice_ok) > 10 else None,
        }

    return results


# ---------------------------------------------------------------------------
# 5. Cross-tabulation: success rate by SPD bucket + border_dist bucket
# ---------------------------------------------------------------------------

def cross_tab(touches):
    print()
    print("=" * 70)
    print("CROSS-TAB: Success Rate by SPD x Border Distance")
    print("=" * 70)

    t = touches.dropna(subset=["spd", "border_dist"])
    if len(t) < 50:
        print("  Insufficient data for cross-tab")
        return

    # SPD buckets
    spd_bins = [0, 0.3, 0.6, 0.9, 1.2, 1.5, 9999]
    spd_lbls = ["0-0.3", "0.3-0.6", "0.6-0.9", "0.9-1.2", "1.2-1.5", ">1.5"]

    # Border dist buckets
    bd_bins = [0, 0.5, 1.0, 1.5, 2.0, 9999]
    bd_lbls = ["0-0.5", "0.5-1.0", "1.0-1.5", "1.5-2.0", ">2.0"]

    t = t.copy()
    t["spd_bkt"]  = pd.cut(t["spd"],         bins=spd_bins, labels=spd_lbls, right=False)
    t["bd_bkt"]   = pd.cut(t["border_dist"],  bins=bd_bins,  labels=bd_lbls,  right=False)

    print("\nSuccess rate (%) by SPD bucket:")
    grp = t.groupby("spd_bkt", observed=True)["success"]
    for name, g in grp:
        n = len(g)
        s = g.sum()
        print(f"  SPD {name:>8} pt/s : {_pct(s,n):>6}  ({s}/{n})")

    print("\nSuccess rate (%) by border distance:")
    grp2 = t.groupby("bd_bkt", observed=True)["success"]
    for name, g in grp2:
        n = len(g)
        s = g.sum()
        print(f"  dist {name:>8} ATR : {_pct(s,n):>6}  ({s}/{n})")


# ---------------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------------

def main():
    print()
    print("=" * 70)
    print("CAL-LEVEL-TOUCH — FluxQuantumAI calibration study")
    print(f"Window: {CFG['start_date']} to {CFG['end_date']}")
    print(f"Touch tol: {CFG['touch_tol_pts']}pts | "
          f"Move thr: {CFG['move_thr_pts']}pts in {CFG['lookahead_bars']}min")
    print("=" * 70)

    m1, m5, m30 = load_data()
    df          = build_merged(m1, m5, m30)
    touches     = label_touches(df)

    if len(touches) == 0:
        print("ERROR: no touches found — check touch_tol_pts or data coverage")
        return

    results = analyse(touches)
    cross_tab(touches)

    print()
    print("=" * 70)
    print("SUMMARY — RECOMMENDED PARAMETER UPDATES")
    print("=" * 70)
    all_r = results.get("ALL", {})
    short_r = results.get("SHORT", {})
    long_r  = results.get("LONG", {})

    print(f"""
Settings to update in settings.json / event_processor.py:

  border_atr_mult    (current: 1.5x)
    ALL    rec: {all_r.get('border_mult_rec')}x ATR
    SHORT  rec: {short_r.get('border_mult_rec')}x ATR
    LONG   rec: {long_r.get('border_mult_rec')}x ATR

  price_speed threshold  (current: 0.80 pt/s — uncalibrated)
    ALL    rec: {all_r.get('spd_thr_rec')} pt/s
    SHORT  rec: {short_r.get('spd_thr_rec')} pt/s
    LONG   rec: {long_r.get('spd_thr_rec')} pt/s

  dwell_time_max_bars  (not yet implemented — gate idea)
    ALL    rec: {all_r.get('dwell_max_rec')} M1 bars
    SHORT  rec: {short_r.get('dwell_max_rec')} M1 bars
    LONG   rec: {long_r.get('dwell_max_rec')} M1 bars

  iceberg_min_proxy  (current: iceberg_proxy_threshold=0.914972)
    ALL    rec: {all_r.get('ice_thr_rec')}
    SHORT  rec: {short_r.get('ice_thr_rec')}
    LONG   rec: {long_r.get('ice_thr_rec')}
""")

    # Save JSON
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated":  pd.Timestamp.utcnow().isoformat(),
            "cfg":        CFG,
            "results":    results,
        }, f, indent=2, default=str)
    print(f"Results saved: {OUT_PATH}")


if __name__ == "__main__":
    main()

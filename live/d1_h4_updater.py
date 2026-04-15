#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C:\\FluxQuantumAI\\live\\d1_h4_updater.py

# ============================================================
# D1/H4 BIAS ENGINE (FASE 4a — Shadow Mode)
#
# Derives D1 and H4 structural bias from M1 data using the
# same box detection state machine as m30_updater.py.
#
# TIMEZONE & SESSION BOUNDARIES:
#   GC futures (CME Globex): Sun 18:00 ET - Fri 17:00 ET
#   Session break: 17:00 ET = 22:00 UTC (minimal activity)
#   D1 candle closes at 22:00 UTC
#   H4 bars: 22:00, 02:00, 06:00, 10:00, 14:00, 18:00 UTC
#   All resamples use offset='22h' to align with session.
#
# ONLY CLOSED CANDLES define bias. The current incomplete bar
# is excluded from JAC direction and bias computation.
#
# Output:
#   gc_h4_boxes.parquet — H4 box structure + h4_jac_dir
#   gc_d1_boxes.parquet — D1 box structure + d1_jac_dir
#   gc_d1h4_bias.json   — composite bias + metadata
#
# Architecture:
#   D1 = primary bias (LONG / SHORT / UNKNOWN)
#   H4 = confirmation (STRONG / WEAK)
#   Bias only changes on closed candle boundaries.
#
# FASE 4a: SHADOW ONLY — zero behavioral change.
# ============================================================

Lifecycle (background daemon thread in run_live.py):
  1. On startup: full rebuild
  2. Every 300s (5 min): rebuild
  3. Writes parquets atomically (tmp -> rename)
  4. Writes gc_d1h4_bias.json with full metadata

Standalone: python live/d1_h4_updater.py [--once]
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("apex.d1h4_updater")

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
DATA_DIR   = Path("C:/data")
M1_PATH    = DATA_DIR / "processed/gc_ohlcv_l2_joined.parquet"
MICRO_DIR  = Path("C:/data/level2/_gc_xcec")

OUTPUT_H4  = DATA_DIR / "processed/gc_h4_boxes.parquet"
OUTPUT_D1  = DATA_DIR / "processed/gc_d1_boxes.parquet"
OUTPUT_BIAS = Path("C:/FluxQuantumAI/logs/gc_d1h4_bias.json")

# Session boundary: GC futures daily close = 22:00 UTC (17:00 ET)
SESSION_OFFSET = "22h"

# Box detection parameters — IDENTICAL to m30_updater (ATR-normalized, timeframe-agnostic)
CONTRACTION_THR   = 1.2
MIN_BARS          = 3
MAX_BREAKOUT_WAIT = 20
AT_STRUCT_TOL     = 0.2
WIN               = 5

# JAC wait adjusted per timeframe (M30=40 bars=20h, H4=30 bars=5d, D1=20 bars=20d)
MAX_JAC_WAIT_H4   = 30
MAX_JAC_WAIT_D1   = 20

# H4 hysteresis: H4 must hold direction for N bars before changing STRONG/WEAK
H4_HYSTERESIS_BARS = 2  # 2 x 4h = 8h

# Update interval
UPDATE_INTERVAL_S = 300  # 5 minutes

# Staleness thresholds
H4_STALE_HOURS = 8.0   # H4 parquet older than this = stale
D1_STALE_HOURS = 48.0  # D1 parquet older than this = stale

H4_OUTPUT_COLS = [
    'open', 'high', 'low', 'close', 'volume', 'atr14',
    'h4_liq_top', 'h4_liq_bot', 'h4_fmv',
    'h4_box_high', 'h4_box_low', 'h4_box_confirmed',
    'h4_box_id', 'h4_jac_dir',
]

D1_OUTPUT_COLS = [
    'open', 'high', 'low', 'close', 'volume', 'atr14',
    'd1_liq_top', 'd1_liq_bot', 'd1_fmv',
    'd1_box_high', 'd1_box_low', 'd1_box_confirmed',
    'd1_box_id', 'd1_jac_dir',
]


# ---------------------------------------------------------------------------
# Step 1 — build M1 (same as m30_updater)
# ---------------------------------------------------------------------------

def _micro_to_m1(micro_path: Path) -> pd.DataFrame | None:
    """Reconstruct M1 OHLCV from today's live microstructure file."""
    if not micro_path.exists():
        return None
    try:
        micro = pd.read_csv(
            micro_path,
            usecols=["timestamp", "mid_price", "bar_delta"],
            dtype={"mid_price": "float64", "bar_delta": "float64"},
        )
        micro["timestamp"] = pd.to_datetime(micro["timestamp"], utc=True)
        micro = micro.dropna(subset=["mid_price", "timestamp"])
        micro = micro.set_index("timestamp").sort_index()
        if micro.empty:
            return None
        m1 = micro["mid_price"].resample("1min").ohlc()
        m1.columns = ["open", "high", "low", "close"]
        m1["volume"] = micro["bar_delta"].abs().resample("1min").sum()
        m1 = m1.dropna(subset=["close"])
        return m1
    except Exception as e:
        log.error("_micro_to_m1 failed: %s", e)
        return None


def _build_m1() -> pd.DataFrame:
    """Load historical M1 and append today's live M1."""
    hist = pd.read_parquet(M1_PATH, columns=["open", "high", "low", "close", "volume"])
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize("UTC")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    micro_path = MICRO_DIR / f"microstructure_{today}.csv.gz"
    m1_today = _micro_to_m1(micro_path)

    if m1_today is not None:
        if m1_today.index.tz is None:
            m1_today.index = m1_today.index.tz_localize("UTC")
        last_hist_ts = hist.index[-1]
        m1_today = m1_today[m1_today.index > last_hist_ts]
        if not m1_today.empty:
            combined = pd.concat([hist, m1_today]).sort_index()
            combined = combined[~combined.index.duplicated(keep="first")]
            return combined

    return hist


# ---------------------------------------------------------------------------
# Step 2 — resample M1 to H4/D1 with session-aligned boundaries
# ---------------------------------------------------------------------------

def _resample_to_tf(m1: pd.DataFrame, freq: str) -> pd.DataFrame:
    """
    Resample M1 to H4 or D1 with session-aligned boundaries.
    freq: '4h' or '1D'
    Offset '22h' aligns bars to GC session (22:00 UTC close).
    """
    tf = m1.resample(freq, offset=SESSION_OFFSET).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])

    # ATR(14)
    tf["prev_close"] = tf["close"].shift(1)
    tf["tr"] = np.maximum(
        tf["high"] - tf["low"],
        np.maximum(
            (tf["high"] - tf["prev_close"]).abs(),
            (tf["low"] - tf["prev_close"]).abs(),
        ),
    )
    tf["atr14"] = tf["tr"].rolling(14).mean()

    # Rolling range for contraction detection
    tf["win_high"]    = tf["high"].rolling(WIN).max()
    tf["win_low"]     = tf["low"].rolling(WIN).min()
    tf["range_pts"]   = tf["win_high"] - tf["win_low"]
    tf["range_ratio"] = tf["range_pts"] / tf["atr14"]

    return tf


# ---------------------------------------------------------------------------
# Step 3 — box detection (identical state machine, parameterised JAC wait)
# ---------------------------------------------------------------------------

def _detect_boxes(df: pd.DataFrame, max_jac_wait: int = 40,
                  prefix: str = "m30") -> tuple[pd.DataFrame, int]:
    """
    Box detection state machine. Identical to m30_updater._detect_boxes()
    but with configurable MAX_JAC_WAIT and column prefix.
    """
    n        = len(df)
    high_a   = df["high"].values.copy()
    low_a    = df["low"].values.copy()
    close_a  = df["close"].values.copy()
    atr_a    = df["atr14"].values.copy()

    out_liq_top   = np.full(n, np.nan)
    out_liq_bot   = np.full(n, np.nan)
    out_fmv       = np.full(n, np.nan)
    out_box_high  = np.full(n, np.nan)
    out_box_low   = np.full(n, np.nan)
    out_confirmed = np.zeros(n, dtype=bool)
    out_box_id    = np.zeros(n, dtype=np.int32)

    cur_liq_top   = np.nan
    cur_liq_bot   = np.nan
    cur_fmv       = np.nan
    cur_box_high  = np.nan
    cur_box_low   = np.nan
    cur_confirmed = False
    cur_box_id    = 0
    box_counter   = 0

    i = MIN_BARS + 14
    while i < n:
        atr = atr_a[i]

        if not np.isnan(cur_liq_top):
            out_liq_top[i]   = cur_liq_top
            out_liq_bot[i]   = cur_liq_bot
            out_fmv[i]       = cur_fmv
            out_box_high[i]  = cur_box_high
            out_box_low[i]   = cur_box_low
            out_confirmed[i] = cur_confirmed
            out_box_id[i]    = cur_box_id

        if np.isnan(atr) or atr <= 0:
            i += 1
            continue

        b_hi = high_a[i]
        b_lo = low_a[i]
        run  = 1
        j    = i - 1
        while j >= 0 and run < 40:
            b_hi2 = max(b_hi, high_a[j])
            b_lo2 = min(b_lo, low_a[j])
            if (b_hi2 - b_lo2) / atr > CONTRACTION_THR:
                break
            b_hi, b_lo = b_hi2, b_lo2
            run += 1
            j -= 1

        if run < MIN_BARS:
            i += 1
            continue

        breakout_idx = None
        breakout_dir = None
        fakeout_ext  = 0.0
        for k in range(i + 1, min(i + MAX_BREAKOUT_WAIT + 1, n)):
            if high_a[k] > b_hi:
                breakout_idx, breakout_dir, fakeout_ext = k, "UP", float(high_a[k])
                break
            elif low_a[k] < b_lo:
                breakout_idx, breakout_dir, fakeout_ext = k, "DN", float(low_a[k])
                break

        if breakout_idx is None:
            i += 1
            continue

        box_counter += 1
        fmv = (b_hi + b_lo) / 2.0
        if breakout_dir == "UP":
            new_liq_top, new_liq_bot = fakeout_ext, float(b_lo)
        else:
            new_liq_top, new_liq_bot = float(b_hi), fakeout_ext

        cur_liq_top, cur_liq_bot = new_liq_top, new_liq_bot
        cur_fmv      = fmv
        cur_box_high = float(b_hi)
        cur_box_low  = float(b_lo)
        cur_confirmed = False
        cur_box_id   = box_counter

        out_liq_top[breakout_idx]   = cur_liq_top
        out_liq_bot[breakout_idx]   = cur_liq_bot
        out_fmv[breakout_idx]       = cur_fmv
        out_box_high[breakout_idx]  = cur_box_high
        out_box_low[breakout_idx]   = cur_box_low
        out_confirmed[breakout_idx] = False
        out_box_id[breakout_idx]    = cur_box_id

        jac_found = False
        for k in range(breakout_idx + 1, min(breakout_idx + max_jac_wait + 1, n)):
            out_liq_top[k]   = cur_liq_top
            out_liq_bot[k]   = cur_liq_bot
            out_fmv[k]       = cur_fmv
            out_box_high[k]  = cur_box_high
            out_box_low[k]   = cur_box_low
            out_confirmed[k] = False
            out_box_id[k]    = cur_box_id

            if breakout_dir == "UP" and close_a[k] > b_hi:
                cur_confirmed = True
                out_confirmed[k] = True
                jac_found = True
                i = k + 1
                break
            elif breakout_dir == "DN" and close_a[k] < b_lo:
                cur_confirmed = True
                out_confirmed[k] = True
                jac_found = True
                i = k + 1
                break

        if not jac_found:
            i = breakout_idx + 1

    df = df.copy()
    df[f"{prefix}_liq_top"]       = out_liq_top
    df[f"{prefix}_liq_bot"]       = out_liq_bot
    df[f"{prefix}_fmv"]           = out_fmv
    df[f"{prefix}_box_high"]      = out_box_high
    df[f"{prefix}_box_low"]       = out_box_low
    df[f"{prefix}_box_confirmed"] = out_confirmed
    df[f"{prefix}_box_id"]        = out_box_id

    return df, box_counter


# ---------------------------------------------------------------------------
# Step 4 — derive JAC direction from last CLOSED confirmed box
# ---------------------------------------------------------------------------

def _derive_jac_dir(df: pd.DataFrame, prefix: str) -> pd.Series:
    """
    Derive JAC breakout direction for each bar from box structure.
    UP breakout (liq_top > box_high) = "long"
    DN breakout (liq_bot < box_low)  = "short"
    """
    liq_top  = df[f"{prefix}_liq_top"]
    liq_bot  = df[f"{prefix}_liq_bot"]
    box_high = df[f"{prefix}_box_high"]
    box_low  = df[f"{prefix}_box_low"]

    jac = pd.Series("unknown", index=df.index, dtype=object)
    up_mask = liq_top.notna() & box_high.notna() & (liq_top > box_high)
    dn_mask = liq_bot.notna() & box_low.notna() & (liq_bot < box_low)
    jac[up_mask] = "long"
    jac[dn_mask] = "short"
    return jac


def _get_last_closed_jac(df: pd.DataFrame, prefix: str) -> tuple[str, str]:
    """
    Get JAC direction from the last CLOSED (confirmed) box only.
    Excludes the current incomplete bar.

    Returns (jac_dir, last_closed_ts_iso)
    """
    # Exclude current (last) bar — it may be incomplete
    if len(df) < 2:
        return "unknown", ""

    closed = df.iloc[:-1]  # all bars except current
    confirmed = closed[closed[f"{prefix}_box_confirmed"] == True]

    if confirmed.empty:
        return "unknown", ""

    last = confirmed.iloc[-1]
    lt = last.get(f"{prefix}_liq_top", float("nan"))
    bh = last.get(f"{prefix}_box_high", float("nan"))
    lb = last.get(f"{prefix}_liq_bot", float("nan"))
    bl = last.get(f"{prefix}_box_low", float("nan"))

    if not math.isnan(lt) and not math.isnan(bh) and lt > bh:
        return "long", confirmed.index[-1].isoformat()
    if not math.isnan(lb) and not math.isnan(bl) and lb < bl:
        return "short", confirmed.index[-1].isoformat()
    return "unknown", confirmed.index[-1].isoformat()


# ---------------------------------------------------------------------------
# Step 5 — composite bias
# ---------------------------------------------------------------------------

def compute_bias(d1_jac: str, h4_jac: str) -> tuple[str, str]:
    """
    Compute composite bias from D1 and H4 JAC directions.

    Returns (bias_direction, bias_strength)
        bias_direction: "LONG" | "SHORT" | "UNKNOWN"
        bias_strength:  "STRONG" | "WEAK" | "UNKNOWN"
    """
    if d1_jac == "unknown":
        return "UNKNOWN", "UNKNOWN"

    base = d1_jac.upper()  # "LONG" or "SHORT"

    if h4_jac == d1_jac:
        return base, "STRONG"
    else:
        # H4 diverges or unknown — D1 prevails with reduced confidence
        return base, "WEAK"


# ---------------------------------------------------------------------------
# Step 6 — atomic write
# ---------------------------------------------------------------------------

def _write_parquet_atomic(df: pd.DataFrame, path: Path, cols: list) -> None:
    """Write parquet atomically via tmp -> rename. 3 retries for Windows locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    df[cols].to_parquet(tmp)
    for attempt in range(3):
        try:
            tmp.replace(path)
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.5)
    log.warning("%s write: rename failed after 3 attempts", path.name)


def _write_bias_json(bias_data: dict) -> None:
    """Write bias metadata JSON atomically."""
    OUTPUT_BIAS.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_BIAS.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bias_data, f, indent=2, default=str)
        tmp.replace(OUTPUT_BIAS)
    except Exception as e:
        log.error("gc_d1h4_bias.json write failed: %s", e)


# ---------------------------------------------------------------------------
# Public: run_update()
# ---------------------------------------------------------------------------

def run_update() -> dict:
    """
    Full D1/H4 rebuild cycle. Returns bias metadata dict.
    """
    t0 = time.monotonic()
    now_utc = datetime.now(timezone.utc)

    # Step 1: build M1
    m1 = _build_m1()

    # Step 2: resample
    h4_base = _resample_to_tf(m1, "4h")
    d1_base = _resample_to_tf(m1, "1D")

    # Step 3: box detection
    h4_boxes, n_h4 = _detect_boxes(h4_base, max_jac_wait=MAX_JAC_WAIT_H4, prefix="h4")
    d1_boxes, n_d1 = _detect_boxes(d1_base, max_jac_wait=MAX_JAC_WAIT_D1, prefix="d1")

    # Step 4: JAC direction (full series for parquet)
    h4_boxes["h4_jac_dir"] = _derive_jac_dir(h4_boxes, "h4")
    d1_boxes["d1_jac_dir"] = _derive_jac_dir(d1_boxes, "d1")

    # Step 4b: last CLOSED candle JAC (for bias — excludes incomplete bar)
    h4_jac, h4_last_closed_ts = _get_last_closed_jac(h4_boxes, "h4")
    d1_jac, d1_last_closed_ts = _get_last_closed_jac(d1_boxes, "d1")

    # Step 5: composite bias
    bias_dir, bias_strength = compute_bias(d1_jac, h4_jac)

    # Step 6: atomic writes
    _write_parquet_atomic(h4_boxes, OUTPUT_H4, H4_OUTPUT_COLS)
    _write_parquet_atomic(d1_boxes, OUTPUT_D1, D1_OUTPUT_COLS)

    # Staleness
    h4_age_s = -1.0
    d1_age_s = -1.0
    try:
        if OUTPUT_H4.exists():
            h4_age_s = time.time() - OUTPUT_H4.stat().st_mtime
        if OUTPUT_D1.exists():
            d1_age_s = time.time() - OUTPUT_D1.stat().st_mtime
    except Exception:
        pass

    h4_stale = h4_age_s > H4_STALE_HOURS * 3600 if h4_age_s >= 0 else True
    d1_stale = d1_age_s > D1_STALE_HOURS * 3600 if d1_age_s >= 0 else True

    elapsed = time.monotonic() - t0

    # Full metadata
    bias_data = {
        "timestamp":          now_utc.isoformat(),
        "d1_jac_dir":         d1_jac,
        "h4_jac_dir":         h4_jac,
        "bias_direction":     bias_dir,
        "bias_strength":      bias_strength,
        "bias_source":        "runtime_d1h4_updater",
        "data_freshness": {
            "h4_parquet_age_s": round(h4_age_s, 1),
            "d1_parquet_age_s": round(d1_age_s, 1),
            "h4_stale":         h4_stale,
            "d1_stale":         d1_stale,
            "m1_last_bar":      m1.index[-1].isoformat() if len(m1) else "?",
        },
        "last_closed_h4_ts":  h4_last_closed_ts,
        "last_closed_d1_ts":  d1_last_closed_ts,
        "session_boundary":   "22:00 UTC (17:00 ET)",
        "h4_bars":            f"22,02,06,10,14,18 UTC",
        "h4_total_boxes":     n_h4,
        "d1_total_boxes":     n_d1,
        "h4_last_bar":        h4_boxes.index[-1].isoformat() if len(h4_boxes) else "?",
        "d1_last_bar":        d1_boxes.index[-1].isoformat() if len(d1_boxes) else "?",
        "elapsed_s":          round(elapsed, 2),
    }

    _write_bias_json(bias_data)

    log.info(
        "D1H4_UPDATE: d1=%s h4=%s bias=%s_%s | "
        "d1_boxes=%d h4_boxes=%d | %.1fs",
        d1_jac, h4_jac, bias_dir, bias_strength,
        n_d1, n_h4, elapsed,
    )

    return bias_data


# ---------------------------------------------------------------------------
# Shadow comparison with current daily_trend
# ---------------------------------------------------------------------------

def shadow_compare(bias_data: dict, current_daily_trend: str) -> dict:
    """
    Compare runtime D1/H4 bias with current daily_trend proxy.
    Returns comparison dict for logging.
    """
    new_dir = bias_data["bias_direction"].lower()  # "long" / "short" / "unknown"
    old_dir = current_daily_trend                   # "long" / "short" / "unknown"

    agrees = (new_dir == old_dir)
    new_unknown = (new_dir == "unknown")
    old_unknown = (old_dir in ("unknown", ""))

    # Would this change TRENDING mode?
    # TRENDING requires daily_trend in ("long", "short")
    old_trending = old_dir in ("long", "short")
    new_trending = new_dir in ("long", "short")
    trending_change = old_trending != new_trending

    # Would direction flip?
    direction_flip = (
        old_dir in ("long", "short")
        and new_dir in ("long", "short")
        and old_dir != new_dir
    )

    # Impact on GAMMA/DELTA (require STRONG in FASE 4b)
    strength = bias_data["bias_strength"]
    gamma_blocked = strength != "STRONG"  # would be blocked in FASE 4b

    comparison = {
        "agrees":           agrees,
        "old_daily_trend":  old_dir,
        "new_d1h4_bias":    f"{bias_data['bias_direction']}_{bias_data['bias_strength']}",
        "new_unknown":      new_unknown,
        "old_unknown":      old_unknown,
        "trending_change":  trending_change,
        "direction_flip":   direction_flip,
        "gamma_would_block": gamma_blocked,
    }

    if not agrees:
        log.warning(
            "D1H4_SHADOW_DIVERGE: old=%s new=%s_%s | "
            "trending_change=%s direction_flip=%s gamma_block=%s",
            old_dir, bias_data["bias_direction"], strength,
            trending_change, direction_flip, gamma_blocked,
        )
    else:
        log.info(
            "D1H4_SHADOW_AGREE: bias=%s_%s",
            bias_data["bias_direction"], strength,
        )

    return comparison


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def start(get_daily_trend_fn=None) -> threading.Thread:
    """
    Start the D1/H4 updater as a background daemon thread.
    get_daily_trend_fn: callable that returns current daily_trend for shadow comparison.
    """
    def _loop():
        log.info("D1H4 updater: initial rebuild on startup...")
        try:
            bias = run_update()
            log.info("D1H4 updater startup: d1=%s h4=%s -> %s_%s (%.1fs)",
                     bias["d1_jac_dir"], bias["h4_jac_dir"],
                     bias["bias_direction"], bias["bias_strength"],
                     bias["elapsed_s"])
            if get_daily_trend_fn:
                shadow_compare(bias, get_daily_trend_fn())
        except Exception as e:
            log.error("D1H4 updater startup failed: %s", e)

        while True:
            time.sleep(UPDATE_INTERVAL_S)

            # Feed health: skip if microstructure is stale
            today_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            micro_today = MICRO_DIR / f"microstructure_{today_str}.csv.gz"
            if micro_today.exists():
                micro_age = time.time() - micro_today.stat().st_mtime
                if micro_age > 600:  # 10 min
                    log.warning("D1H4_UPDATE SKIPPED: feed stale (age=%.0fs)", micro_age)
                    continue
            # If no today file (weekend), still run with historical data

            try:
                bias = run_update()
                if get_daily_trend_fn:
                    shadow_compare(bias, get_daily_trend_fn())
            except Exception as e:
                log.error("D1H4 updater cycle failed: %s", e)

    t = threading.Thread(target=_loop, name="D1H4Updater", daemon=True)
    t.start()
    log.info("D1H4 updater thread started (every %ds, session boundary 22:00 UTC)", UPDATE_INTERVAL_S)
    return t


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=_sys.stdout,
    )

    log.info("Running D1H4 updater (standalone)...")
    bias = run_update()

    print()
    print("=" * 60)
    print("  D1/H4 BIAS ENGINE RESULT")
    print("=" * 60)
    for k, v in bias.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"    {kk:<24s}: {vv}")
        else:
            print(f"  {k:<24s}: {v}")
    print("=" * 60)

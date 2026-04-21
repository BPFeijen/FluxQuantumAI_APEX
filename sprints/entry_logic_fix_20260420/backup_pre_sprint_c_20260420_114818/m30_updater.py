#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C:\FluxQuantumAI\live\m30_updater.py

# ============================================================
# ADR-001: M30 = EXECUTION TIMEFRAME
# This module rebuilds gc_m30_boxes.parquet every 30 minutes
# by combining historical M1 data with today's live
# microstructure, then running the M30 box detection
# state machine in full.
# Source for today: microstructure_YYYY-MM-DD.csv.gz (mid_price)
# Source for history: gc_ohlcv_l2_joined.parquet
# Output: gc_m30_boxes.parquet (atomic write)
# ============================================================

Lifecycle (background daemon thread in run_live.py):
  1. On startup: run immediately (refresh stale parquet from yesterday)
  2. Every 30 min at M30 bar boundary +10s: rebuild
  3. Writes gc_m30_boxes.parquet atomically (tmp -> rename)
  4. Logs new boxes detected vs previous run

Standalone: python live/m30_updater.py [--once]
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("apex.m30_updater")

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
DATA_DIR       = Path("C:/data")
M1_PATH        = DATA_DIR / "processed/gc_ohlcv_l2_joined.parquet"
OUTPUT_M30     = DATA_DIR / "processed/gc_m30_boxes.parquet"
MICRO_DIR      = Path("C:/data/level2/_gc_xcec")

# Box detection parameters -- must match m30_box_detection.py (calibrated 2026-04-07)
CONTRACTION_THR   = 1.2
MIN_BARS          = 3
MAX_BREAKOUT_WAIT = 20
MAX_JAC_WAIT      = 40
AT_STRUCT_TOL     = 0.2
WIN               = 5       # rolling window for range_ratio

OUTPUT_COLS = [
    'open', 'high', 'low', 'close', 'volume', 'atr14',
    'm30_liq_top', 'm30_liq_bot', 'm30_fmv',
    'm30_box_high', 'm30_box_low', 'm30_box_confirmed',
    'm30_box_id', 'at_struct_level',
]


# ---------------------------------------------------------------------------
# Step 1 -- build combined M1 DataFrame (history + today)
# ---------------------------------------------------------------------------

def _micro_to_m1(micro_path: Path) -> pd.DataFrame | None:
    """
    Reconstruct M1 OHLCV from today's live microstructure file.
    Uses mid_price column (present in microstructure schema since Quantower update).
    Returns None if file missing or no usable data.
    """
    if not micro_path.exists():
        log.warning("Microstructure not found: %s", micro_path)
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


def _build_m1(micro_dir: Path = MICRO_DIR) -> pd.DataFrame:
    """
    Load historical M1 from gc_ohlcv_l2_joined.parquet and append
    ALL missing days from microstructure files (not just today).

    Fix 2026-04-15: the old code only appended today's micro, causing
    M30 boxes to go stale when gc_ohlcv_l2_joined.parquet was not updated.
    Now scans for ALL microstructure files newer than the M1 parquet.
    """
    # Historical M1
    hist = pd.read_parquet(M1_PATH, columns=["open", "high", "low", "close", "volume"])
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize("UTC")

    last_hist_ts = hist.index[-1]
    last_hist_date = last_hist_ts.date()
    today_date = datetime.now(timezone.utc).date()

    # Scan ALL micro files from last_hist_date to today
    import datetime as _dt
    chunks = []
    d = last_hist_date
    while d <= today_date:
        d_str = d.strftime("%Y-%m-%d")
        for suffix in [".csv.gz", ".fixed.csv.gz"]:
            micro_path = micro_dir / f"microstructure_{d_str}{suffix}"
            if micro_path.exists():
                m1_day = _micro_to_m1(micro_path)
                if m1_day is not None:
                    if m1_day.index.tz is None:
                        m1_day.index = m1_day.index.tz_localize("UTC")
                    # Only bars newer than current M1
                    m1_day = m1_day[m1_day.index > last_hist_ts]
                    if not m1_day.empty:
                        chunks.append(m1_day)
                        last_hist_ts = m1_day.index[-1]  # advance for next day
                break  # prefer non-fixed over fixed
        d += _dt.timedelta(days=1)

    if chunks:
        combined = pd.concat([hist] + chunks).sort_index()
        combined = combined[~combined.index.duplicated(keep="first")]
        total_added = len(combined) - len(hist)
        log.info(
            "M1 combined: %d hist + %d new (%d days) = %d total  (last bar: %s)",
            len(hist), total_added, len(chunks), len(combined),
            combined.index[-1].isoformat(),
        )
        return combined
    else:
        log.info("M1: no new bars beyond hist last=%s", hist.index[-1].isoformat())

    return hist


# ---------------------------------------------------------------------------
# Step 2 -- resample M1 -> M30 and compute features
# ---------------------------------------------------------------------------

def _m1_to_m30_base(m1: pd.DataFrame) -> pd.DataFrame:
    """Resample M1 to M30 and compute ATR14 + range_ratio (inputs for box detection)."""
    m30 = m1.resample("30min").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])

    # ATR(14)
    m30["prev_close"] = m30["close"].shift(1)
    m30["tr"] = np.maximum(
        m30["high"] - m30["low"],
        np.maximum(
            (m30["high"] - m30["prev_close"]).abs(),
            (m30["low"]  - m30["prev_close"]).abs(),
        ),
    )
    m30["atr14"] = m30["tr"].rolling(14).mean()

    # Rolling range for contraction detection
    m30["win_high"]    = m30["high"].rolling(WIN).max()
    m30["win_low"]     = m30["low"].rolling(WIN).min()
    m30["range_pts"]   = m30["win_high"] - m30["win_low"]
    m30["range_ratio"] = m30["range_pts"] / m30["atr14"]

    return m30


# ---------------------------------------------------------------------------
# Step 3 -- box detection state machine (mirrors m30_box_detection.py)
# ---------------------------------------------------------------------------

def _detect_boxes(m30: pd.DataFrame) -> pd.DataFrame:
    """
    Hybrid one-pass / two-pass M30 box detection.
    Exact copy of m30_box_detection.detect_boxes() -- kept here to avoid
    importing that script (which has module-level stdout redirection).

    Parameters: CONTRACTION_THR=1.2, MIN_BARS=3,
                MAX_BREAKOUT_WAIT=20, MAX_JAC_WAIT=40
    """
    n        = len(m30)
    high_a   = m30["high"].values.copy()
    low_a    = m30["low"].values.copy()
    close_a  = m30["close"].values.copy()
    atr_a    = m30["atr14"].values.copy()

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

    i = MIN_BARS + 14   # skip ATR warmup
    while i < n:
        atr = atr_a[i]

        # Forward-fill current levels
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

        # Build contraction block ending at i
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

        # Scan forward for first breakout
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

        # Register one-pass box
        box_counter += 1
        fmv = (b_hi + b_lo) / 2.0
        if breakout_dir == "UP":
            new_liq_top, new_liq_bot = fakeout_ext, float(b_lo)
        else:
            new_liq_top, new_liq_bot = float(b_hi), fakeout_ext

        cur_liq_top, cur_liq_bot  = new_liq_top, new_liq_bot
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

        # Scan forward for JAC confirmation
        jac_found = False
        for k in range(breakout_idx + 1, min(breakout_idx + MAX_JAC_WAIT + 1, n)):
            out_liq_top[k]   = cur_liq_top
            out_liq_bot[k]   = cur_liq_bot
            out_fmv[k]       = cur_fmv
            out_box_high[k]  = cur_box_high
            out_box_low[k]   = cur_box_low
            out_confirmed[k] = False
            out_box_id[k]    = cur_box_id

            if breakout_dir == "UP" and close_a[k] > b_hi:
                log.info("[PHASE_FIX] old=close<b_lo new=close>b_hi reason=JAC_corrected box_id=%d k=%d", cur_box_id, k)
                cur_confirmed = True
                out_confirmed[k] = True
                jac_found = True
                i = k + 1
                break
            elif breakout_dir == "DN" and close_a[k] < b_lo:
                log.info("[PHASE_FIX] old=close>b_hi new=close<b_lo reason=JAC_corrected box_id=%d k=%d", cur_box_id, k)
                cur_confirmed = True
                out_confirmed[k] = True
                jac_found = True
                i = k + 1
                break

        if not jac_found:
            i = breakout_idx + 1

    m30 = m30.copy()
    m30["m30_liq_top"]       = out_liq_top
    m30["m30_liq_bot"]       = out_liq_bot
    m30["m30_fmv"]           = out_fmv
    m30["m30_box_high"]      = out_box_high
    m30["m30_box_low"]       = out_box_low
    m30["m30_box_confirmed"] = out_confirmed
    m30["m30_box_id"]        = out_box_id

    tol = m30["atr14"] * AT_STRUCT_TOL
    near_top = ((m30["close"] - m30["m30_liq_top"]).abs() <= tol).fillna(False)
    near_bot = ((m30["close"] - m30["m30_liq_bot"]).abs() <= tol).fillna(False)
    near_fmv = ((m30["close"] - m30["m30_fmv"]).abs()     <= tol).fillna(False)
    m30["at_struct_level"] = near_top | near_bot | near_fmv

    return m30, box_counter


# ---------------------------------------------------------------------------
# Step 4 -- atomic write
# ---------------------------------------------------------------------------

def _write_atomic(m30: pd.DataFrame) -> None:
    """Write gc_m30_boxes.parquet atomically via tmp -> rename.
    Retries up to 3 times on Windows file lock errors (WinError 5/32)."""
    tmp = OUTPUT_M30.with_suffix(".tmp.parquet")
    m30[OUTPUT_COLS].to_parquet(tmp)
    for attempt in range(3):
        try:
            tmp.replace(OUTPUT_M30)
            return
        except OSError:
            if attempt < 2:
                import time as _t
                _t.sleep(0.5)
    log.warning("M30 write: rename failed after 3 attempts (file locked by reader)")


# ---------------------------------------------------------------------------
# Public: run_update()
# ---------------------------------------------------------------------------

def run_update(micro_dir: Path = MICRO_DIR) -> dict:
    """
    Full M30 rebuild cycle. Returns a summary dict for logging.
    Thread-safe: reads parquets, writes atomically.
    Also persists the updated M1 back to gc_ohlcv_l2_joined.parquet
    so it stays current throughout the day.
    """
    t0 = time.monotonic()

    m1       = _build_m1(micro_dir)

    # Persist updated M1 back to parquet (fix 2026-04-15: M1 was never written back)
    # Only write if M1 has new bars beyond what's on disk
    try:
        disk_m1 = pd.read_parquet(M1_PATH, columns=["close"])
        if m1.index[-1] > disk_m1.index[-1]:
            _m1_tmp = M1_PATH.with_suffix(".tmp.parquet")
            m1.to_parquet(_m1_tmp)
            for _attempt in range(3):
                try:
                    _m1_tmp.replace(M1_PATH)
                    break
                except OSError:
                    time.sleep(0.5)
    except Exception as _e:
        log.warning("M1 parquet persist failed: %s", _e)

    m30_base = _m1_to_m30_base(m1)
    m30, n_boxes = _detect_boxes(m30_base)

    # Read previous last box_id for change detection
    prev_box_id = 0
    try:
        prev = pd.read_parquet(OUTPUT_M30, columns=["m30_box_id"])
        prev_box_id = int(prev["m30_box_id"].max())
    except Exception:
        pass

    _write_atomic(m30)
    elapsed = time.monotonic() - t0

    # Summary
    confirmed = m30[m30["m30_box_confirmed"] == True]
    last_box_id = int(m30["m30_box_id"].max()) if n_boxes > 0 else 0
    new_boxes   = last_box_id - prev_box_id

    summary = {
        "elapsed_s":    round(elapsed, 1),
        "m30_rows":     len(m30),
        "last_bar":     m30.index[-1].isoformat() if len(m30) else "?",
        "total_boxes":  n_boxes,
        "last_box_id":  last_box_id,
        "new_boxes":    new_boxes,
        "last_liq_top": round(float(m30["m30_liq_top"].iloc[-1]), 2) if n_boxes else 0,
        "last_liq_bot": round(float(m30["m30_liq_bot"].iloc[-1]), 2) if n_boxes else 0,
        "last_fmv":     round(float(m30["m30_fmv"].iloc[-1]),     2) if n_boxes else 0,
        "confirmed_boxes": int(confirmed["m30_box_id"].nunique()) if not confirmed.empty else 0,
        "last_confirmed_id": int(confirmed["m30_box_id"].iloc[-1]) if not confirmed.empty else 0,
    }

    if new_boxes > 0:
        log.info(
            "M30 UPDATE: %d new box(es) detected  "
            "last_box_id=%d  liq_top=%.2f  liq_bot=%.2f  fmv=%.2f  "
            "(%.1fs, %d M30 bars)",
            new_boxes, last_box_id,
            summary["last_liq_top"], summary["last_liq_bot"], summary["last_fmv"],
            elapsed, len(m30),
        )
    else:
        log.info(
            "M30 update: no new boxes  last_box_id=%d  liq_top=%.2f  liq_bot=%.2f  "
            "(%.1fs)",
            last_box_id, summary["last_liq_top"], summary["last_liq_bot"], elapsed,
        )

    return summary


# ---------------------------------------------------------------------------
# Background thread (called from run_live.py)
# ---------------------------------------------------------------------------

def start(micro_dir: Path = MICRO_DIR) -> threading.Thread:
    """
    Start the M30 updater as a background daemon thread.

    Schedule:
      - Immediately on start (catches up stale parquet)
      - Every 60s thereafter (real-time cadence)
        Box detection still uses M30 resampling -- partial current bar
        has m30_box_confirmed=False; completed bars are confirmed.
        Running every 60s ensures a new confirmed box is detected within
        ~1 min of M30 bar close instead of up to 30 min (ADR-001 fix
        2026-04-10: 30-min boundary sleep caused level staleness).
    """
    def _loop():
        # --- initial run (startup) ---
        log.info("M30 updater: initial rebuild on startup...")
        try:
            s = run_update(micro_dir)
            log.info("M30 updater startup: %s", s)
        except Exception as e:
            log.error("M30 updater startup failed: %s", e)

        # --- run every 60s (real-time) ---
        while True:
            time.sleep(60)

            # Feed health check: skip update if microstructure file is stale (>5 min)
            today_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            micro_today = micro_dir / f"microstructure_{today_str}.csv.gz"
            if micro_today.exists():
                micro_age = time.time() - micro_today.stat().st_mtime
                if micro_age > 300:  # 5 min
                    log.warning(
                        "M30_UPDATE SKIPPED: feed STALE (microstructure age=%.0fs) -- "
                        "check quantower_level2_api (port 8000)", micro_age)
                    continue
            else:
                log.warning("M30_UPDATE SKIPPED: microstructure file not found for today (%s)", today_str)
                continue

            try:
                s = run_update(micro_dir)
                log.info(
                    "M30_UPDATE: boxes refreshed at %s -- %d boxes active  liq_top=%.2f  liq_bot=%.2f",
                    datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                    s["total_boxes"], s["last_liq_top"], s["last_liq_bot"],
                )
                if s["new_boxes"] > 0:
                    log.info(
                        "NEW M30 BOX: id=%d  liq_top=%.2f  liq_bot=%.2f  fmv=%.2f",
                        s["last_box_id"], s["last_liq_top"], s["last_liq_bot"], s["last_fmv"],
                    )
            except Exception as e:
                log.error("M30 updater cycle failed: %s", e)

    t = threading.Thread(target=_loop, name="M30Updater", daemon=True)
    t.start()
    log.info("M30 updater thread started (60s real-time cadence)")
    return t


# ---------------------------------------------------------------------------
# Standalone run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=_sys.stdout,
    )

    log.info("Running M30 updater (standalone)...")
    summary = run_update()

    print()
    print("=" * 60)
    print("  M30 UPDATER RESULT")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<24s}: {v}")
    print("=" * 60)

    # Show last 5 confirmed boxes
    try:
        boxes = pd.read_parquet(OUTPUT_M30)
        confirmed = boxes[boxes["m30_box_confirmed"] == True]
        unique_ids = confirmed["m30_box_id"].unique()
        print(f"\n  Last 5 confirmed boxes ({len(unique_ids)} total confirmed):")
        recent_ids = unique_ids[-5:]
        for bid in recent_ids:
            rows = confirmed[confirmed["m30_box_id"] == bid]
            r = rows.iloc[0]
            first_ts = rows.index[0]
            print(f"    box_id={bid:5d}  {first_ts}  "
                  f"liq_top={r.m30_liq_top:.2f}  "
                  f"liq_bot={r.m30_liq_bot:.2f}  "
                  f"fmv={r.m30_fmv:.3f}  "
                  f"atr14={r.atr14:.2f}")
        print()
        # Show last M30 bar
        last = boxes.iloc[-1]
        print(f"  Last M30 bar : {boxes.index[-1]}")
        print(f"  Last levels  : liq_top={last.m30_liq_top:.2f}  "
              f"liq_bot={last.m30_liq_bot:.2f}  fmv={last.m30_fmv:.3f}")
        print(f"  Last box_id  : {int(last.m30_box_id)}")
        print(f"  Confirmed    : {bool(last.m30_box_confirmed)}")
    except Exception as e:
        print(f"  (Could not read output: {e})")
    print("=" * 60)

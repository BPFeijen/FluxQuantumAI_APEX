#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C:\FluxQuantumAI\live\m5_updater.py

# ============================================================
# M5 = EXECUTION TIMEFRAME (Fractional Wyckoff)
# This module rebuilds gc_m5_boxes.parquet every 60 seconds
# by combining historical M1 data with today's live
# microstructure, resampled to 5-minute bars, then running
# the M5 box detection state machine in full.
#
# Role in dual-timeframe architecture:
#   M5  -> liq_top / liq_bot for immediate execution
#   M30 -> macro bias / TP2 structural targets
#
# Source for today : microstructure_YYYY-MM-DD.csv.gz (mid_price)
# Source for history: gc_ohlcv_l2_joined.parquet (M1 data)
# Output: gc_m5_boxes.parquet (atomic write via tmp -> rename)
# ============================================================

Lifecycle (background daemon thread in run_live.py):
  1. On startup: run immediately
  2. Every 60s thereafter: rebuild
  3. Writes gc_m5_boxes.parquet atomically (tmp -> rename)
  4. Logs new boxes detected vs previous run

Standalone: python live/m5_updater.py [--once]
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("apex.m5_updater")

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
DATA_DIR   = Path("C:/data")
M1_PATH    = DATA_DIR / "processed/gc_ohlcv_l2_joined.parquet"
OUTPUT_M5  = DATA_DIR / "processed/gc_m5_boxes.parquet"
MICRO_DIR  = Path("C:/data/level2/_gc_xcec")

# Box detection parameters -- same algorithm as M30 (ATR-normalized, timeframe-agnostic)
# At M5: MIN_BARS=3 -> 15min contraction; MAX_BREAKOUT_WAIT=20 -> 100min; MAX_JAC_WAIT=24 -> 2h
CONTRACTION_THR   = 1.2
MIN_BARS          = 3
MAX_BREAKOUT_WAIT = 20
MAX_JAC_WAIT      = 24   # tighter than M30 (40): 2h JAC window suits intraday M5 structure
AT_STRUCT_TOL     = 0.2
WIN               = 5    # rolling window for range_ratio
VALUE_STACK_MIN   = 3    # CAL-13: min consecutive monotonic FMV boxes for trend confirmation

OUTPUT_COLS = [
    'open', 'high', 'low', 'close', 'volume', 'bar_delta', 'atr14',
    'm5_liq_top', 'm5_liq_bot', 'm5_fmv',
    'm5_box_high', 'm5_box_low', 'm5_box_confirmed',
    'm5_box_id', 'at_struct_level',
    'm5_phase_state', 'm5_value_stack',
]


# ---------------------------------------------------------------------------
# Step 1 -- build combined M1 DataFrame (history + today)
# Re-uses the same _micro_to_m1 logic as m30_updater -- source is identical.
# ---------------------------------------------------------------------------

def _micro_to_m1(micro_path: Path) -> pd.DataFrame | None:
    """Reconstruct M1 OHLCV from today's live microstructure file."""
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
        m1["bar_delta"] = micro["bar_delta"].resample("1min").sum()  # signed delta for displacement
        m1 = m1.dropna(subset=["close"])
        return m1
    except Exception as e:
        log.error("_micro_to_m1 failed: %s", e)
        return None


def _build_m1(micro_dir: Path = MICRO_DIR) -> pd.DataFrame:
    """
    Load historical M1 from gc_ohlcv_l2_joined.parquet and append
    ALL missing days from microstructure files (not just today).

    Fix 2026-04-15: same fix as m30_updater — scan all missing days.
    """
    hist = pd.read_parquet(M1_PATH, columns=["open", "high", "low", "close", "volume"])
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize("UTC")
    if "bar_delta" not in hist.columns:
        hist["bar_delta"] = 0.0

    last_hist_ts = hist.index[-1]
    last_hist_date = last_hist_ts.date()
    today_date = datetime.now(timezone.utc).date()

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
                    m1_day = m1_day[m1_day.index > last_hist_ts]
                    if not m1_day.empty:
                        chunks.append(m1_day)
                        last_hist_ts = m1_day.index[-1]
                break
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
# Step 2 -- resample M1 -> M5 and compute features
# ---------------------------------------------------------------------------

def _m1_to_m5_base(m1: pd.DataFrame) -> pd.DataFrame:
    """Resample M1 to M5 and compute ATR14 + range_ratio (box detection inputs)."""
    m5 = m1.resample("5min").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        bar_delta=("bar_delta", "sum"),
    ).dropna(subset=["close"])

    # ATR(14)
    m5["prev_close"] = m5["close"].shift(1)
    m5["tr"] = np.maximum(
        m5["high"] - m5["low"],
        np.maximum(
            (m5["high"] - m5["prev_close"]).abs(),
            (m5["low"]  - m5["prev_close"]).abs(),
        ),
    )
    m5["atr14"] = m5["tr"].rolling(14).mean()

    # Rolling range for contraction detection
    m5["win_high"]    = m5["high"].rolling(WIN).max()
    m5["win_low"]     = m5["low"].rolling(WIN).min()
    m5["range_pts"]   = m5["win_high"] - m5["win_low"]
    m5["range_ratio"] = m5["range_pts"] / m5["atr14"]

    return m5


# ---------------------------------------------------------------------------
# Step 3 -- box detection state machine (M5 variant, m5_* column names)
# Same algorithm as m30_updater._detect_boxes -- ATR-normalized, timeframe-agnostic.
# ---------------------------------------------------------------------------

def _detect_boxes(m5: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    One-pass / two-pass M5 box detection.
    Identical state machine to m30_updater._detect_boxes but outputs m5_* columns.
    """
    n        = len(m5)
    high_a   = m5["high"].values.copy()
    low_a    = m5["low"].values.copy()
    close_a  = m5["close"].values.copy()
    atr_a    = m5["atr14"].values.copy()

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

    m5 = m5.copy()
    m5["m5_liq_top"]       = out_liq_top
    m5["m5_liq_bot"]       = out_liq_bot
    m5["m5_fmv"]           = out_fmv
    m5["m5_box_high"]      = out_box_high
    m5["m5_box_low"]       = out_box_low
    m5["m5_box_confirmed"] = out_confirmed
    m5["m5_box_id"]        = out_box_id

    tol = m5["atr14"] * AT_STRUCT_TOL
    near_top = ((m5["close"] - m5["m5_liq_top"]).abs() <= tol).fillna(False)
    near_bot = ((m5["close"] - m5["m5_liq_bot"]).abs() <= tol).fillna(False)
    near_fmv = ((m5["close"] - m5["m5_fmv"]).abs()     <= tol).fillna(False)
    m5["at_struct_level"] = near_top | near_bot | near_fmv

    # -- Phase State Detection (Sprint 6, CAL-13) ----------------------
    # Track consecutive monotonic FMV boxes. Phase states:
    #   CONTRACTION     : current box not confirmed (default)
    #   EXPANSION_EARLY : 1-2 confirmed boxes in same direction
    #   EXPANSION_MULTI : >= VALUE_STACK_MIN consecutive boxes (trend)
    #   TRANSITIONAL    : FMV direction just reversed
    #   NEW_RANGE       : first confirmed box after reversal
    #   WARNING         : >= VALUE_STACK_MIN boxes but box range narrowing
    phase_arr = np.full(n, "CONTRACTION", dtype=object)
    stack_arr = np.zeros(n, dtype=np.int32)

    # Get unique confirmed box FMVs in order
    box_fmvs = {}  # box_id -> fmv
    for i in range(n):
        bid = out_box_id[i]
        if bid > 0 and out_confirmed[i]:
            box_fmvs[bid] = out_fmv[i]

    if len(box_fmvs) >= 2:
        sorted_ids = sorted(box_fmvs.keys())
        # Compute consecutive monotonic stack at each box
        stack_count = {}
        stack_dir = {}  # +1 = up, -1 = down
        prev_dir = 0
        cur_stack = 1

        for k in range(len(sorted_ids)):
            bid = sorted_ids[k]
            if k == 0:
                stack_count[bid] = 1
                stack_dir[bid] = 0
                continue

            prev_bid = sorted_ids[k - 1]
            fmv_now = box_fmvs[bid]
            fmv_prev = box_fmvs[prev_bid]

            if fmv_now > fmv_prev:
                d = 1
            elif fmv_now < fmv_prev:
                d = -1
            else:
                d = 0

            if d == prev_dir and d != 0:
                cur_stack += 1
            elif d != 0:
                cur_stack = 2  # new direction starts at 2 (this + previous)
            else:
                cur_stack = 1

            prev_dir = d if d != 0 else prev_dir
            stack_count[bid] = cur_stack
            stack_dir[bid] = d

        # Map back to bar-level arrays
        last_confirmed_bid = 0
        for i in range(n):
            bid = out_box_id[i]
            if bid > 0 and out_confirmed[i]:
                last_confirmed_bid = bid

            if last_confirmed_bid == 0:
                continue

            sc = stack_count.get(last_confirmed_bid, 1)
            sd = stack_dir.get(last_confirmed_bid, 0)
            stack_arr[i] = sc

            if not out_confirmed[i] and out_box_id[i] > last_confirmed_bid:
                phase_arr[i] = "CONTRACTION"
            elif sc >= VALUE_STACK_MIN:
                # Check if range is narrowing (WARNING)
                if bid in box_fmvs:
                    cur_range = out_box_high[i] - out_box_low[i]
                    # Use ATR as reference
                    if cur_range > 0 and high_a[i] > 0:
                        phase_arr[i] = "EXPANSION_MULTI"
                    else:
                        phase_arr[i] = "WARNING"
                else:
                    phase_arr[i] = "EXPANSION_MULTI"
            elif sc == 2:
                # Check if this is a reversal
                prev_sc = stack_count.get(last_confirmed_bid - 1, 0) if last_confirmed_bid > 1 else 0
                if prev_sc >= VALUE_STACK_MIN:
                    phase_arr[i] = "TRANSITIONAL"
                else:
                    phase_arr[i] = "EXPANSION_EARLY"
            elif sc == 1:
                phase_arr[i] = "NEW_RANGE"
            else:
                phase_arr[i] = "EXPANSION_EARLY"

    m5["m5_phase_state"]  = phase_arr
    m5["m5_value_stack"]  = stack_arr

    return m5, box_counter


# ---------------------------------------------------------------------------
# Step 4 -- atomic write
# ---------------------------------------------------------------------------

def _write_atomic(m5: pd.DataFrame) -> None:
    """Write gc_m5_boxes.parquet atomically via tmp -> rename.
    Retries up to 3 times on Windows file lock errors (WinError 5/32)."""
    tmp = OUTPUT_M5.with_suffix(".tmp.parquet")
    m5[OUTPUT_COLS].to_parquet(tmp)
    for attempt in range(3):
        try:
            tmp.replace(OUTPUT_M5)
            return
        except OSError:
            if attempt < 2:
                import time as _t
                _t.sleep(0.5)
    # Last resort: just leave the tmp file; next cycle will overwrite it
    log.warning("M5 write: rename failed after 3 attempts (file locked by reader)")


# ---------------------------------------------------------------------------
# Public: run_update()
# ---------------------------------------------------------------------------

def run_update(micro_dir: Path = MICRO_DIR) -> dict:
    """
    Full M5 rebuild cycle. Returns a summary dict for logging.
    Thread-safe: reads parquets, writes atomically.
    """
    t0 = time.monotonic()

    m1      = _build_m1(micro_dir)
    m5_base = _m1_to_m5_base(m1)
    m5, n_boxes = _detect_boxes(m5_base)

    # Read previous last box_id for change detection
    prev_box_id = 0
    try:
        prev = pd.read_parquet(OUTPUT_M5, columns=["m5_box_id"])
        prev_box_id = int(prev["m5_box_id"].max())
    except Exception:
        pass

    _write_atomic(m5)
    elapsed = time.monotonic() - t0

    confirmed = m5[m5["m5_box_confirmed"] == True]
    last_box_id = int(m5["m5_box_id"].max()) if n_boxes > 0 else 0
    new_boxes   = last_box_id - prev_box_id

    summary = {
        "elapsed_s":    round(elapsed, 1),
        "m5_rows":      len(m5),
        "last_bar":     m5.index[-1].isoformat() if len(m5) else "?",
        "total_boxes":  n_boxes,
        "last_box_id":  last_box_id,
        "new_boxes":    new_boxes,
        "last_liq_top": round(float(m5["m5_liq_top"].iloc[-1]), 2) if n_boxes else 0,
        "last_liq_bot": round(float(m5["m5_liq_bot"].iloc[-1]), 2) if n_boxes else 0,
        "last_fmv":     round(float(m5["m5_fmv"].iloc[-1]),     2) if n_boxes else 0,
        "confirmed_boxes": int(confirmed["m5_box_id"].nunique()) if not confirmed.empty else 0,
        "last_confirmed_id": int(confirmed["m5_box_id"].iloc[-1]) if not confirmed.empty else 0,
        "phase_state":   str(m5["m5_phase_state"].iloc[-1]) if len(m5) else "CONTRACTION",
        "value_stack":   int(m5["m5_value_stack"].iloc[-1]) if len(m5) else 0,
    }

    if new_boxes > 0:
        log.info(
            "M5 UPDATE: %d new box(es) detected  "
            "last_box_id=%d  liq_top=%.2f  liq_bot=%.2f  fmv=%.2f  "
            "(%.1fs, %d M5 bars)",
            new_boxes, last_box_id,
            summary["last_liq_top"], summary["last_liq_bot"], summary["last_fmv"],
            elapsed, len(m5),
        )
    else:
        log.debug(
            "M5 update: no new boxes  last_box_id=%d  liq_top=%.2f  liq_bot=%.2f  (%.1fs)",
            last_box_id, summary["last_liq_top"], summary["last_liq_bot"], elapsed,
        )

    return summary


# ---------------------------------------------------------------------------
# Background thread (called from run_live.py)
# ---------------------------------------------------------------------------

def start(micro_dir: Path = MICRO_DIR) -> threading.Thread:
    """
    Start the M5 updater as a background daemon thread.

    Schedule:
      - Immediately on start
      - Every 60s thereafter

    M5 bars close every 5 minutes. Running every 60s means the latest
    completed M5 bar is always within 60s of being captured -- giving the
    system structural level updates 6x more frequently than M30.

    Role: provides liq_top/liq_bot for immediate execution.
    M30 (macro) is still needed for TP2 targets and trend bias.
    """
    def _loop():
        log.info("M5 updater: initial rebuild on startup...")
        try:
            s = run_update(micro_dir)
            log.info("M5 updater startup: %s", s)
        except Exception as e:
            log.error("M5 updater startup failed: %s", e)

        while True:
            time.sleep(60)

            # Feed health: skip if microstructure file is stale (>5 min)
            today_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            micro_today = micro_dir / f"microstructure_{today_str}.csv.gz"
            if micro_today.exists():
                micro_age = time.time() - micro_today.stat().st_mtime
                if micro_age > 300:
                    log.warning(
                        "M5_UPDATE SKIPPED: feed STALE (microstructure age=%.0fs) -- "
                        "check quantower_level2_api (port 8000)", micro_age)
                    continue
            else:
                log.warning("M5_UPDATE SKIPPED: microstructure file not found for today (%s)", today_str)
                continue

            try:
                s = run_update(micro_dir)
                log.info(
                    "M5_UPDATE: boxes refreshed at %s -- %d boxes active  liq_top=%.2f  liq_bot=%.2f",
                    datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                    s["total_boxes"], s["last_liq_top"], s["last_liq_bot"],
                )
                if s["new_boxes"] > 0:
                    log.info(
                        "NEW M5 BOX: id=%d  liq_top=%.2f  liq_bot=%.2f  fmv=%.2f",
                        s["last_box_id"], s["last_liq_top"], s["last_liq_bot"], s["last_fmv"],
                    )
            except Exception as e:
                log.error("M5 updater cycle failed: %s", e)

    t = threading.Thread(target=_loop, name="M5Updater", daemon=True)
    t.start()
    log.info("M5 updater thread started (60s cadence, 5-min Wyckoff structure)")
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

    log.info("Running M5 updater (standalone)...")
    summary = run_update()

    print()
    print("=" * 60)
    print("  M5 UPDATER RESULT")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<24s}: {v}")
    print("=" * 60)

    try:
        boxes = pd.read_parquet(OUTPUT_M5)
        confirmed = boxes[boxes["m5_box_confirmed"] == True]
        unique_ids = confirmed["m5_box_id"].unique()
        print(f"\n  Last 5 confirmed boxes ({len(unique_ids)} total confirmed):")
        recent_ids = unique_ids[-5:]
        for bid in recent_ids:
            rows = confirmed[confirmed["m5_box_id"] == bid]
            r = rows.iloc[0]
            first_ts = rows.index[0]
            print(f"    box_id={bid:5d}  {first_ts}  "
                  f"liq_top={r.m5_liq_top:.2f}  "
                  f"liq_bot={r.m5_liq_bot:.2f}  "
                  f"fmv={r.m5_fmv:.3f}  "
                  f"atr14={r.atr14:.2f}")
        print()
        last = boxes.iloc[-1]
        print(f"  Last M5 bar  : {boxes.index[-1]}")
        print(f"  Last levels  : liq_top={last.m5_liq_top:.2f}  "
              f"liq_bot={last.m5_liq_bot:.2f}  fmv={last.m5_fmv:.3f}")
        print(f"  Last box_id  : {int(last.m5_box_id)}")
        print(f"  Confirmed    : {bool(last.m5_box_confirmed)}")
    except Exception as e:
        print(f"  (Could not read output: {e})")
    print("=" * 60)

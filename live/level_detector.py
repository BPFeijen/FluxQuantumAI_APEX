#!/usr/bin/env python3
"""
C:\FluxQuantumAI\live\level_detector.py

# ============================================================
# DUAL-TIMEFRAME LEVEL DETECTOR
#
# M5  = EXECUTION TIMEFRAME  (liq_top / liq_bot / fmv returned)
# M30 = MACRO BIAS TIMEFRAME (m30_liq_top/m30_liq_bot + m30_bias in dict)
#
# Primary source : gc_m5_boxes.parquet  (rebuilt every 60s by m5_updater.py)
# Macro context  : gc_m30_boxes.parquet (rebuilt every 60s by m30_updater.py)
# D1 direction   : gc_ats_features_v4.parquet (daily_jac_dir)
#
# Fallback: if gc_m5_boxes.parquet is unavailable, falls back to M30
#           execution levels for backward compatibility (source = "m30_box").
#
# ADR-001: H4/D1 levels are NEVER used for execution.
# Violation = system goes idle in trending markets.
# ============================================================

Returns dict:
  {
    # --- Execution levels (M5) ---
    "liq_top":      float,   # GC futures price (m5_liq_top)
    "liq_bot":      float,   # GC futures price (m5_liq_bot)
    "fmv":          float,   # GC futures price (m5_fmv)
    "liq_top_mt5":  float,   # XAUUSD spot (GC - GC_MT5_OFFSET)
    "liq_bot_mt5":  float,
    "fmv_mt5":      float,
    "box_high":     float | None,
    "box_low":      float | None,
    "box_high_mt5": float | None,
    "box_low_mt5":  float | None,
    "atr_14":       float,   # ATR14 from M5 bars
    "source":       str,     # "m5_box" | "m5_box_unconfirmed" | "m30_box" | "m30_box_unconfirmed"
    "box_id":       int,     # M5 box identifier
    "box_age_h":    float,   # hours since last confirmed bar of this box

    # --- Direction filters ---
    "daily_trend":  str,     # "long" | "short" | "unknown"  (D1 direction filter)
    "m30_bias":     str,     # "bullish" | "bearish" | "unknown"  (M30 macro bias)

    # --- M30 macro context (always populated when M30 available) ---
    "m30_liq_top":  float | None,
    "m30_liq_bot":  float | None,
    "m30_fmv":      float | None,
    "m30_box_id":   int | None,
  }

GC ↔ XAUUSD offset: GC_mid - XAUUSD_mid ≈ +31 pts (carry premium).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

log = logging.getLogger("apex.level_detector")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
M5_BOXES_PATH    = Path(r"C:\data\processed\gc_m5_boxes.parquet")
M30_BOXES_PATH   = Path(r"C:\data\processed\gc_m30_boxes.parquet")
FEATURES_V4_PATH = Path(r"C:\data\processed\gc_ats_features_v4.parquet")
MICRO_DIR        = Path(os.environ.get("ATS_DATA_L2_DIR", r"C:\data\level2\_gc_xcec"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GC_MT5_OFFSET = 31.0   # GC futures carry premium over XAUUSD spot

# M5 staleness thresholds (tighter -- M5 is real-time execution)
M5_STALE_WARN_H  = 0.5   # warn if last confirmed M5 box is > 30min old
M5_FALLBACK_H    = 0.25  # use unconfirmed M5 box if confirmed is > 15min old

# M30 staleness thresholds (looser -- M30 is macro bias)
M30_STALE_WARN_H = 4.0
M30_FALLBACK_H   = 4.0

# ---------------------------------------------------------------------------
# H4 bias thresholds (Sprint C v2 -- literatura-aligned)
# CALIBRATION_TBD: heuristic values from Wyckoff strong-close / ATS continuation
# (ATS Trade System :1119 "four hour is authority"; Strategic Plan No Exceptions).
# Data-driven recalibration planned for Sprint D (post-shadow observation).
# ---------------------------------------------------------------------------
H4_CLOSE_PCT_BULL_THRESHOLD    = 0.75   # R_H4_1: close in upper quartile of H4 range
H4_CLOSE_PCT_BEAR_THRESHOLD    = 0.25   # R_H4_4: close in lower quartile
H4_CONTINUATION_WINDOW         = 3      # R_H4_2/5: examine last N completed candles
H4_CONTINUATION_MIN_SAME       = 2      # R_H4_2/5: min same-color candles in window
H4_CONF_STRONG                 = 0.70   # confidence when R_H4_1 AND R_H4_2 both fire
H4_CONF_SINGLE                 = 0.55   # confidence when only one rule fires
H4_CONF_JAC_CONFIRMED          = 0.95   # R_H4_3/6 via h4_jac_dir from parquet
H4_MAX_STALENESS_HOURS_DEFAULT = 6.0    # parquet freshness threshold for R_H4_3/6


# ---------------------------------------------------------------------------
# Bias terminology canonicalization (Sprint C v2, addendum §B.2)
#   legacy derive_m30_bias out:      "bullish" | "bearish" | "unknown"
#   gc_d1h4_bias.json bias_direction: "LONG" | "SHORT" | "?"
#   gc_d1h4_bias.json h4_jac_dir:    "long" | "short" | "?" | "UP" | "DN"
#   v2 canonical:                    "bullish" | "bearish" | "neutral"
# ---------------------------------------------------------------------------

def _canonicalize_bias(raw) -> str:
    """Normalize any bias string to v2 vocabulary: bullish | bearish | neutral.

    Case-insensitive. ``None``, empty, ``"unknown"``, ``"?"`` and anything else
    unrecognized -> ``"neutral"``. Intended for boundary normalization only --
    v2 internals should always work in canonical form.
    """
    if raw is None:
        return "neutral"
    r = str(raw).strip().upper()
    if r in ("BULLISH", "LONG", "UP"):
        return "bullish"
    if r in ("BEARISH", "SHORT", "DN", "DOWN"):
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_current_gc_price() -> float | None:
    """Get current GC mid-price from microstructure (most recent file, last row)."""
    try:
        files = sorted(MICRO_DIR.glob("microstructure_*.csv.gz"))
        if files:
            df = pd.read_csv(files[-1], usecols=["mid_price"])
            df = df.dropna(subset=["mid_price"])
            if not df.empty:
                return round(float(df["mid_price"].iloc[-1]), 2)
    except Exception as e:
        log.warning("_get_current_gc_price failed: %s", e)
    return None


def _load_parquet_with_retry(path: Path, label: str) -> pd.DataFrame | None:
    """Load a parquet file with 3 retries (handles atomic replace window)."""
    import time as _time

    if not path.exists():
        log.warning("%s parquet not found: %s", label, path)
        return None
    for attempt in range(3):
        try:
            df = pd.read_parquet(path)
            if df.empty:
                return None
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df
        except Exception as e:
            if attempt < 2:
                log.warning("%s parquet load attempt %d/3 failed: %s -- retrying", label, attempt + 1, e)
                _time.sleep(2)
            else:
                log.error("%s parquet load failed after 3 attempts: %s", label, e)
                return None


def _load_m5_boxes() -> pd.DataFrame | None:
    return _load_parquet_with_retry(M5_BOXES_PATH, "M5")


def _load_m30_boxes() -> pd.DataFrame | None:
    return _load_parquet_with_retry(M30_BOXES_PATH, "M30")


def _get_daily_trend() -> str:
    """
    Derive D1 direction bias from M30 box structure (live, updated every 60s).
    Falls back to gc_ats_features_v4.parquet if M30 derivation fails.

    Logic: resample M30 FMV to daily, check last 3 days.
    If FMVs are monotonically rising -> "long".
    If FMVs are monotonically falling -> "short".
    Otherwise -> check last 2 days, then fallback to features_v4.

    ADR-001: D1 provides direction ONLY. Never used for execution levels.
    """
    try:
        m30 = pd.read_parquet(M30_BOXES_PATH, columns=["m30_fmv"])
        if m30.index.tz is None:
            m30.index = m30.index.tz_localize("UTC")
        if m30.empty:
            return _get_daily_trend_fallback()

        # Daily FMV: last M30 FMV per trading day
        daily_fmv = m30["m30_fmv"].resample("1D").last().dropna()
        if len(daily_fmv) < 2:
            return _get_daily_trend_fallback()

        last3 = daily_fmv.tail(3).values
        if len(last3) >= 3:
            if all(last3[i] > last3[i-1] for i in range(1, len(last3))):
                return "long"
            if all(last3[i] < last3[i-1] for i in range(1, len(last3))):
                return "short"

        # 2-day fallback
        last2 = daily_fmv.tail(2).values
        if last2[-1] > last2[-2]:
            return "long"
        elif last2[-1] < last2[-2]:
            return "short"

        return _get_daily_trend_fallback()
    except Exception as e:
        log.warning("daily_trend M30 derivation failed: %s -- falling back to v4", e)
        return _get_daily_trend_fallback()


def _get_daily_trend_fallback() -> str:
    """Fallback: read from gc_ats_features_v4.parquet (static, may be stale)."""
    try:
        df = pd.read_parquet(FEATURES_V4_PATH, columns=["daily_jac_dir"])
        if df.empty:
            return "unknown"
        val = str(df["daily_jac_dir"].iloc[-1]).lower().strip()
        if val in ("long", "short"):
            return val
        if val == "up":
            return "long"
        if val == "down":
            return "short"
        return "unknown"
    except Exception as e:
        log.warning("daily_trend fallback read failed: %s", e)
        return "unknown"


def _get_m30_bias(m30_df: pd.DataFrame | None) -> str:
    """
    Derive M30 macro bias from the most recent confirmed M30 box breakout direction.

    UP breakout -> liq_top = fakeout_ext > box_high  -> bullish bias
    DN breakout -> liq_bot = fakeout_ext < box_low   -> bearish bias
    """
    try:
        bias, _is_confirmed = derive_m30_bias(m30_df, confirmed_only=False)
        return bias
    except Exception as e:
        log.warning("_get_m30_bias failed: %s", e)
        return "unknown"


def derive_m30_bias(
    m30_df: pd.DataFrame | None, confirmed_only: bool = False
) -> tuple[str, bool]:
    """
    Shared M30 bias derivation used by both entry and position-monitor paths.

    Returns
    -------
    (bias, is_confirmed_source)
      bias: "bullish" | "bearish" | "unknown"
      is_confirmed_source: True when derived from a confirmed M30 box that
      is still structurally valid against the latest price.
    """
    if m30_df is None or m30_df.empty:
        return "unknown", False

    def _classify(row) -> str:
        import math

        box_high = row.get("m30_box_high", float("nan"))
        box_low  = row.get("m30_box_low",  float("nan"))
        liq_top  = row.get("m30_liq_top",  float("nan"))
        liq_bot  = row.get("m30_liq_bot",  float("nan"))

        bull_ext = (
            not math.isnan(liq_top)
            and not math.isnan(box_high)
            and liq_top > box_high
        )
        bear_ext = (
            not math.isnan(liq_bot)
            and not math.isnan(box_low)
            and liq_bot < box_low
        )

        if bull_ext and not bear_ext:
            return "bullish"
        if bear_ext and not bull_ext:
            return "bearish"
        return "unknown"

    def _price_vs_box_bias(row, current_gc: float | None) -> str:
        if current_gc is None:
            return "unknown"

        try:
            box_high = row.get("m30_box_high", None)
            box_low  = row.get("m30_box_low", None)

            if pd.notna(box_high) and current_gc > float(box_high):
                return "bullish"
            if pd.notna(box_low) and current_gc < float(box_low):
                return "bearish"
        except Exception:
            return "unknown"

        return "unknown"

    try:
        current_gc = _get_current_gc_price()

        confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
        latest_struct = m30_df[
            (m30_df["m30_box_id"].notna()) &
            (m30_df["m30_box_id"] > 0) &
            (m30_df["m30_liq_top"].notna())
        ]

        latest_row = latest_struct.iloc[-1] if not latest_struct.empty else m30_df.iloc[-1]

        # ----- confirmed path -----
        if not confirmed.empty:
            last_confirmed = confirmed.iloc[-1]
            confirmed_bias = _classify(last_confirmed)
            structural_now = _price_vs_box_bias(latest_row, current_gc)

            # If confirmed bias is contradicted by live structure, invalidate it
            if confirmed_bias in ("bullish", "bearish"):
                if structural_now != "unknown" and structural_now != confirmed_bias:
                    if confirmed_only:
                        return "unknown", False
                else:
                    return confirmed_bias, True

        if confirmed_only:
            return "unknown", False

        # ----- live/provisional path -----
        latest_bias = _classify(latest_row)
        if latest_bias in ("bullish", "bearish"):
            return latest_bias, False

        structural_now = _price_vs_box_bias(latest_row, current_gc)
        if structural_now in ("bullish", "bearish"):
            return structural_now, False

        return "unknown", False

    except Exception as e:
        log.warning("derive_m30_bias failed: %s", e)
        return "unknown", False


# ---------------------------------------------------------------------------
# H4 bias (Sprint C v2 -- literatura-aligned, ATS "four hour is authority")
# ---------------------------------------------------------------------------

def derive_h4_bias(
    h4_candles,
    h4_box_row=None,
    max_staleness_hours: float = H4_MAX_STALENESS_HOURS_DEFAULT,
    now_utc=None,
):
    """Derive H4 directional bias per ATS Trade System :1119 ("four hour is
    authority over smaller timeframes") and Strategic Plan §4-5 ("No exceptions").

    Rules (Sprint C v2 Design Doc §2.6)
    -----------------------------------
    R_H4_3 / R_H4_6 : from ``h4_box_row.h4_jac_dir`` (when fresh + confirmed)
    R_H4_1 / R_H4_4 : body direction + close position in quartile of last bar
    R_H4_2 / R_H4_5 : 3-candle continuation (2+ same-color AND higher-highs
                      AND higher-lows, per Addendum Adjustment 1: AND not OR)
    R_H4_7          : neutral default (no rules fire, or conflicting)

    Parameters
    ----------
    h4_candles : list[dict] | pandas.DataFrame | None
        Last N COMPLETED H4 candles (current partial bar must be excluded by
        caller, per Sprint C decision 11.4). Each candle requires
        ``open, high, low, close`` keys/columns.
    h4_box_row : dict | pandas.Series | None
        Optional latest row from ``gc_h4_boxes.parquet``. If staleness
        <= ``max_staleness_hours`` AND ``h4_box_confirmed`` is True AND
        ``h4_jac_dir`` is canonicalizable to bullish/bearish, returns
        immediately via R_H4_3/R_H4_6 (highest confidence path).
        NOTE: as of 2026-04-20 the ``d1_h4_updater`` writer is disabled
        (see SPRINT H4-WRITER-FIX backlog). This path typically misses
        today; kept here so that revival automatically upgrades the bias.
    max_staleness_hours : float
        Staleness threshold for ``h4_box_row`` acceptance. Default 6h.
    now_utc : datetime | None
        Injection for test determinism. Defaults to ``datetime.now(UTC)``.

    Returns
    -------
    (bias, confidence, metadata) : tuple[str, float, dict]
        bias        : "bullish" | "bearish" | "neutral"
        confidence  : float in ``[0.0, 1.0]``
        metadata    : {
            "source"          : one of
                ``"h4_boxes_parquet"``         -- R_H4_3/6 fired (parquet fresh + confirmed)
                ``"ohlcv_resample"``           -- R_H4_1/2/4/5 fired from candles
                ``"h4_boxes_parquet_fallback"``-- box present but stale or not confirmed,
                                                   candle rules still fired
                ``"neutral_default"``          -- no rules fired (R_H4_7)
                ``"insufficient_data"``        -- candles empty AND no usable box_row
                ``"error"``                    -- unexpected exception
            "rules_fired"     : list[str]  (e.g. ["R_H4_1", "R_H4_2"])
            "staleness_hours" : float | None  (only when box_row evaluated)
            "last_h4_close_ts": str | None
            "error"           : str  (only when source == "error")
        }

    Fail-safe
    ---------
    Any exception -> ``("neutral", 0.0, {"source": "error", ...})``. Callers
    treat neutral as "no trades" (Sprint C decision 11.3), which makes error
    handling implicitly safest.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    metadata_shell = {
        "source": "neutral_default",
        "rules_fired": [],
        "staleness_hours": None,
        "last_h4_close_ts": None,
    }

    try:
        # --- R_H4_3 / R_H4_6 via h4_box_row (if fresh and confirmed) ---
        if h4_box_row is not None:
            try:
                # Accept pd.Series (has .name as index) or dict-like
                if hasattr(h4_box_row, "name") and h4_box_row.name is not None:
                    row_ts = pd.Timestamp(h4_box_row.name)
                else:
                    _ts_raw = None
                    if hasattr(h4_box_row, "get"):
                        _ts_raw = h4_box_row.get("ts") or h4_box_row.get("timestamp")
                    row_ts = pd.Timestamp(_ts_raw) if _ts_raw is not None else None

                if row_ts is not None:
                    if row_ts.tz is None:
                        row_ts = row_ts.tz_localize("UTC")
                    staleness_h = (now_utc - row_ts.to_pydatetime()).total_seconds() / 3600.0

                    def _row_get(k, default=None):
                        if hasattr(h4_box_row, "get"):
                            return h4_box_row.get(k, default)
                        return getattr(h4_box_row, k, default)

                    if staleness_h <= max_staleness_hours:
                        confirmed_flag = bool(_row_get("h4_box_confirmed", False))
                        jac_raw = _row_get("h4_jac_dir", "") or ""
                        if confirmed_flag:
                            canon = _canonicalize_bias(jac_raw)
                            if canon == "bullish":
                                return "bullish", H4_CONF_JAC_CONFIRMED, {
                                    "source": "h4_boxes_parquet",
                                    "rules_fired": ["R_H4_3"],
                                    "staleness_hours": round(staleness_h, 2),
                                    "last_h4_close_ts": str(row_ts),
                                }
                            if canon == "bearish":
                                return "bearish", H4_CONF_JAC_CONFIRMED, {
                                    "source": "h4_boxes_parquet",
                                    "rules_fired": ["R_H4_6"],
                                    "staleness_hours": round(staleness_h, 2),
                                    "last_h4_close_ts": str(row_ts),
                                }
                            # jac unknown -> fall through to candle analysis
                        # box not confirmed -> fall through
                    # stale -> fall through, but we have parquet fallback source hint
                    metadata_shell["source"] = "h4_boxes_parquet_fallback"
                    metadata_shell["staleness_hours"] = round(staleness_h, 2)
                    metadata_shell["last_h4_close_ts"] = str(row_ts)
            except Exception as _e:
                log.warning("derive_h4_bias: h4_box_row parsing failed: %s", _e)

        # --- Normalize h4_candles to list[dict] ---
        candles = []
        last_ts_fallback = None
        if h4_candles is None:
            pass
        elif hasattr(h4_candles, "iterrows"):
            try:
                for idx, row in h4_candles.iterrows():
                    candles.append({
                        "open":  float(row["open"]),
                        "high":  float(row["high"]),
                        "low":   float(row["low"]),
                        "close": float(row["close"]),
                    })
                if len(h4_candles.index) > 0:
                    last_ts_fallback = str(h4_candles.index[-1])
            except Exception as _e:
                log.warning("derive_h4_bias: DataFrame -> candles failed: %s", _e)
        else:
            try:
                for c in h4_candles:
                    if isinstance(c, dict):
                        candles.append({
                            "open":  float(c["open"]),
                            "high":  float(c["high"]),
                            "low":   float(c["low"]),
                            "close": float(c["close"]),
                        })
            except Exception as _e:
                log.warning("derive_h4_bias: iterable -> candles failed: %s", _e)

        if not candles:
            meta = dict(metadata_shell)
            if meta["source"] == "neutral_default":
                meta["source"] = "insufficient_data"
            return "neutral", 0.0, meta

        # --- R_H4_1 / R_H4_4: body + close position in last completed candle ---
        last = candles[-1]
        rng = last["high"] - last["low"]
        close_pct = 0.5 if rng == 0 else (last["close"] - last["low"]) / rng
        body_up   = last["close"] > last["open"]
        body_down = last["close"] < last["open"]

        r_h4_1 = body_up   and close_pct >= H4_CLOSE_PCT_BULL_THRESHOLD
        r_h4_4 = body_down and close_pct <= H4_CLOSE_PCT_BEAR_THRESHOLD

        # --- R_H4_2 / R_H4_5: 3-candle continuation, AND not OR (Adjustment 1) ---
        r_h4_2 = False
        r_h4_5 = False
        if len(candles) >= H4_CONTINUATION_WINDOW:
            window = candles[-H4_CONTINUATION_WINDOW:]
            greens = sum(1 for c in window if c["close"] > c["open"])
            reds   = sum(1 for c in window if c["close"] < c["open"])
            hhhl = all(
                (window[i]["high"] > window[i - 1]["high"]) and (window[i]["low"] > window[i - 1]["low"])
                for i in range(1, H4_CONTINUATION_WINDOW)
            )
            llhl = all(
                (window[i]["low"] < window[i - 1]["low"]) and (window[i]["high"] < window[i - 1]["high"])
                for i in range(1, H4_CONTINUATION_WINDOW)
            )
            r_h4_2 = greens >= H4_CONTINUATION_MIN_SAME and hhhl
            r_h4_5 = reds   >= H4_CONTINUATION_MIN_SAME and llhl

        # --- Combine ---
        bull_fired = [n for flag, n in ((r_h4_1, "R_H4_1"), (r_h4_2, "R_H4_2")) if flag]
        bear_fired = [n for flag, n in ((r_h4_4, "R_H4_4"), (r_h4_5, "R_H4_5")) if flag]

        # Source precedence: if metadata_shell already marked parquet_fallback, keep it;
        # otherwise use ohlcv_resample for rule-based outcomes.
        source_when_fired = metadata_shell["source"] if metadata_shell["source"] == "h4_boxes_parquet_fallback" else "ohlcv_resample"
        last_ts_str = metadata_shell["last_h4_close_ts"] or last_ts_fallback

        if bull_fired and not bear_fired:
            conf = H4_CONF_STRONG if (r_h4_1 and r_h4_2) else H4_CONF_SINGLE
            return "bullish", conf, {
                "source": source_when_fired,
                "rules_fired": bull_fired,
                "staleness_hours": metadata_shell["staleness_hours"],
                "last_h4_close_ts": last_ts_str,
            }
        if bear_fired and not bull_fired:
            conf = H4_CONF_STRONG if (r_h4_4 and r_h4_5) else H4_CONF_SINGLE
            return "bearish", conf, {
                "source": source_when_fired,
                "rules_fired": bear_fired,
                "staleness_hours": metadata_shell["staleness_hours"],
                "last_h4_close_ts": last_ts_str,
            }

        # Conflicting or no rules -> neutral (R_H4_7)
        return "neutral", 0.0, {
            "source": "neutral_default",
            "rules_fired": [],
            "staleness_hours": metadata_shell["staleness_hours"],
            "last_h4_close_ts": last_ts_str,
        }

    except Exception as e:
        log.warning("derive_h4_bias unexpected error: %s", e)
        return "neutral", 0.0, {
            "source": "error",
            "rules_fired": [],
            "staleness_hours": None,
            "last_h4_close_ts": None,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Level extraction helpers
# ---------------------------------------------------------------------------

def _extract_levels(row, bar_time, box_age_h: float, source: str,
                    daily_trend: str, prefix: str = "m30") -> dict:
    """
    Build levels dict from a parquet row.
    prefix: "m5" or "m30" -- determines which column names to read.
    Output keys are always the same (backward compatible).
    """
    liq_top  = float(row[f"{prefix}_liq_top"])
    liq_bot  = float(row[f"{prefix}_liq_bot"])
    fmv      = float(row[f"{prefix}_fmv"])
    atr14    = float(row["atr14"])
    box_id   = int(row[f"{prefix}_box_id"])

    bh_key = f"{prefix}_box_high"
    bl_key = f"{prefix}_box_low"
    box_high = float(row[bh_key]) if bh_key in row.index and pd.notna(row[bh_key]) else None
    box_low  = float(row[bl_key]) if bl_key in row.index and pd.notna(row[bl_key]) else None

    return {
        "liq_top":      liq_top,
        "liq_bot":      liq_bot,
        "fmv":          fmv,
        "liq_top_mt5":  round(liq_top - GC_MT5_OFFSET, 2),
        "liq_bot_mt5":  round(liq_bot - GC_MT5_OFFSET, 2),
        "fmv_mt5":      round(fmv     - GC_MT5_OFFSET, 2),
        "box_high":     box_high,
        "box_low":      box_low,
        "box_high_mt5": round(box_high - GC_MT5_OFFSET, 2) if box_high is not None else None,
        "box_low_mt5":  round(box_low  - GC_MT5_OFFSET, 2) if box_low  is not None else None,
        "atr_14":       atr14,
        "source":       source,
        "box_id":       box_id,
        "box_age_h":    round(box_age_h, 1),
        "daily_trend":  daily_trend,
    }


def _get_m30_macro_context(m30_df: pd.DataFrame | None) -> dict:
    """
    Extract M30 macro levels (liq_top/liq_bot/fmv/box_id) for context.
    Returns a dict with m30_* keys, or all None values if unavailable.
    """
    empty = {"m30_liq_top": None, "m30_liq_bot": None, "m30_fmv": None, "m30_box_id": None}
    if m30_df is None:
        return empty
    try:
        confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
        if not confirmed.empty:
            last = confirmed.iloc[-1]
        else:
            unconf = m30_df[
                (m30_df["m30_box_id"].notna()) &
                (m30_df["m30_box_id"] > 0) &
                (m30_df["m30_liq_top"].notna())
            ]
            if unconf.empty:
                return empty
            last = unconf.iloc[-1]

        return {
            "m30_liq_top": round(float(last["m30_liq_top"]), 2),
            "m30_liq_bot": round(float(last["m30_liq_bot"]), 2),
            "m30_fmv":     round(float(last["m30_fmv"]),     2),
            "m30_box_id":  int(last["m30_box_id"]),
        }
    except Exception as e:
        log.warning("_get_m30_macro_context failed: %s", e)
        return empty


def _validate_m5_vs_m30(levels: dict, m30_bias: str) -> None:
    """
    Log a warning if M5 execution levels conflict with M30 macro bias.
    This is informational only -- does not block execution.
    """
    if m30_bias == "unknown":
        return
    m5_top  = levels.get("liq_top", 0)
    m30_top = levels.get("m30_liq_top")
    m30_bot = levels.get("m30_liq_bot")
    if m30_top is None or m30_bot is None:
        return

    # M5 structure is BELOW M30 box entirely -> M5 is inside M30 DN leg
    if m30_bias == "bullish" and m5_top < m30_bot:
        log.warning(
            "M5/M30 DISCREPANCY: M30 bias=%s but M5 liq_top=%.2f is below M30 liq_bot=%.2f -- "
            "M5 structure may be a pullback within bullish M30 context",
            m30_bias, m5_top, m30_bot,
        )
    # M5 structure is ABOVE M30 box entirely -> M5 is inside M30 UP leg
    elif m30_bias == "bearish" and levels.get("liq_bot", 0) > m30_top:
        log.warning(
            "M5/M30 DISCREPANCY: M30 bias=%s but M5 liq_bot=%.2f is above M30 liq_top=%.2f -- "
            "M5 structure may be a pullback within bearish M30 context",
            m30_bias, levels.get("liq_bot", 0), m30_top,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_current_levels() -> dict | None:
    """
    Return current structural levels for GC/XAUUSD.

    Primary: M5 execution levels (gc_m5_boxes.parquet).
    Fallback: M30 execution levels if M5 unavailable (backward compatible).

    M5 confirmed box preferred. If last confirmed M5 box is > M5_FALLBACK_H (15min)
    old AND a newer unconfirmed box exists, uses unconfirmed with WARNING.

    Always enriches the result with M30 macro context (m30_liq_top, m30_liq_bot,
    m30_fmv, m30_bias) for downstream directional validation.

    Returns None only if no usable box exists in either timeframe.
    """
    m5_df  = _load_m5_boxes()
    m30_df = _load_m30_boxes()

    now_utc     = datetime.now(timezone.utc)
    daily_trend = _get_daily_trend()
    m30_bias, m30_bias_confirmed = derive_m30_bias(m30_df, confirmed_only=True)
    provisional_m30_bias, _ = derive_m30_bias(m30_df, confirmed_only=False)
    m30_macro   = _get_m30_macro_context(m30_df)

    # ------------------------------------------------------------------
    # PRIMARY PATH: M5 execution levels
    # ------------------------------------------------------------------
    if m5_df is not None:
        confirmed_m5 = m5_df[m5_df["m5_box_confirmed"] == True]

        if not confirmed_m5.empty:
            last_confirmed_row  = confirmed_m5.iloc[-1]
            last_confirmed_time = confirmed_m5.index[-1]
            last_confirmed_id   = int(last_confirmed_row["m5_box_id"])
            box_age_h = (now_utc - last_confirmed_time.to_pydatetime()).total_seconds() / 3600.0

            # FALLBACK: confirmed M5 box is stale AND newer unconfirmed exists
            if box_age_h > M5_FALLBACK_H:
                unconf = m5_df[
                    (m5_df["m5_box_confirmed"] == False) &
                    (m5_df["m5_box_id"].notna()) &
                    (m5_df["m5_box_id"] > last_confirmed_id) &
                    (m5_df["m5_liq_top"].notna())
                ]
                if not unconf.empty:
                    fb_row   = unconf.iloc[-1]
                    fb_time  = unconf.index[-1]
                    fb_age_h = (now_utc - fb_time.to_pydatetime()).total_seconds() / 3600.0
                    fb_id    = int(fb_row["m5_box_id"])
                    log.warning(
                        "M5_BOX_FALLBACK: confirmed box_%d is %.0fmin old -- "
                        "using unconfirmed box_%d (liq_top=%.2f liq_bot=%.2f).",
                        last_confirmed_id, box_age_h * 60,
                        fb_id, float(fb_row["m5_liq_top"]), float(fb_row["m5_liq_bot"]),
                    )
                    result = _extract_levels(fb_row, fb_time, fb_age_h,
                                             "m5_box_unconfirmed", daily_trend, prefix="m5")
                    result.update(m30_macro)
                    result["m30_bias"] = m30_bias
                    result["m30_bias_confirmed"] = m30_bias_confirmed
                    result["provisional_m30_bias"] = provisional_m30_bias
                    _validate_m5_vs_m30(result, m30_bias)
                    return result

            # Normal: return confirmed M5 levels
            if box_age_h > M5_STALE_WARN_H:
                log.warning(
                    "M5 box stale: box_id=%d  confirmed_bar=%s  age=%.0fmin  "
                    "(m5_updater should refresh -- is it running?)",
                    last_confirmed_id, last_confirmed_time.isoformat(), box_age_h * 60,
                )
            else:
                log.info(
                    "M5 levels (box_id=%d  age=%.0fmin) -- "
                    "liq_top=%.2f  liq_bot=%.2f  fmv=%.2f  atr14=%.2f  m30_bias=%s",
                    last_confirmed_id, box_age_h * 60,
                    float(last_confirmed_row["m5_liq_top"]),
                    float(last_confirmed_row["m5_liq_bot"]),
                    float(last_confirmed_row["m5_fmv"]),
                    float(last_confirmed_row["atr14"]),
                    m30_bias,
                )

            current_price = _get_current_gc_price()
            if current_price:
                dist = round(current_price - float(last_confirmed_row["m5_liq_top"]), 1)
                log.info(
                    "Price context: GC=%.2f  m5_liq_top=%.2f  dist_from_top=%+.1fpts  "
                    "daily_trend=%s  m30_bias=%s",
                    current_price, float(last_confirmed_row["m5_liq_top"]),
                    dist, daily_trend, m30_bias,
                )

            result = _extract_levels(last_confirmed_row, last_confirmed_time, box_age_h,
                                     "m5_box", daily_trend, prefix="m5")
            result.update(m30_macro)
            result["m30_bias"] = m30_bias
            result["m30_bias_confirmed"] = m30_bias_confirmed
            result["provisional_m30_bias"] = provisional_m30_bias
            _validate_m5_vs_m30(result, m30_bias)
            return result

        # No confirmed M5 boxes -- try any unconfirmed M5 box
        unconf_all = m5_df[
            (m5_df["m5_box_id"].notna()) &
            (m5_df["m5_box_id"] > 0) &
            (m5_df["m5_liq_top"].notna())
        ]
        if not unconf_all.empty:
            fb_row   = unconf_all.iloc[-1]
            fb_time  = unconf_all.index[-1]
            fb_age_h = (now_utc - fb_time.to_pydatetime()).total_seconds() / 3600.0
            log.warning(
                "No confirmed M5 box -- using latest unconfirmed box_%d "
                "(liq_top=%.2f liq_bot=%.2f)",
                int(fb_row["m5_box_id"]), float(fb_row["m5_liq_top"]), float(fb_row["m5_liq_bot"]),
            )
            result = _extract_levels(fb_row, fb_time, fb_age_h,
                                     "m5_box_unconfirmed", daily_trend, prefix="m5")
            result.update(m30_macro)
            result["m30_bias"] = m30_bias
            result["m30_bias_confirmed"] = m30_bias_confirmed
            result["provisional_m30_bias"] = provisional_m30_bias
            _validate_m5_vs_m30(result, m30_bias)
            return result

        log.warning("M5 boxes parquet exists but no usable box found -- falling back to M30")

    # ------------------------------------------------------------------
    # FALLBACK PATH: M30 execution levels (backward compatible)
    # Used when m5_updater hasn't run yet or M5 file is unavailable.
    # ------------------------------------------------------------------
    if m30_df is None:
        log.error("Both M5 and M30 boxes unavailable -- cannot detect levels")
        return None

    confirmed_m30 = m30_df[m30_df["m30_box_confirmed"] == True]

    if not confirmed_m30.empty:
        last_confirmed_row  = confirmed_m30.iloc[-1]
        last_confirmed_time = confirmed_m30.index[-1]
        last_confirmed_id   = int(last_confirmed_row["m30_box_id"])
        box_age_h = (now_utc - last_confirmed_time.to_pydatetime()).total_seconds() / 3600.0

        if box_age_h > M30_FALLBACK_H:
            unconf = m30_df[
                (m30_df["m30_box_confirmed"] == False) &
                (m30_df["m30_box_id"].notna()) &
                (m30_df["m30_box_id"] > last_confirmed_id) &
                (m30_df["m30_liq_top"].notna())
            ]
            if not unconf.empty:
                fb_row   = unconf.iloc[-1]
                fb_time  = unconf.index[-1]
                fb_age_h = (now_utc - fb_time.to_pydatetime()).total_seconds() / 3600.0
                log.warning(
                    "M30_BOX_FALLBACK (M5 unavailable): confirmed box_%d is %.1fh old -- "
                    "using unconfirmed box_%d (liq_top=%.2f liq_bot=%.2f)",
                    last_confirmed_id, box_age_h,
                    int(fb_row["m30_box_id"]),
                    float(fb_row["m30_liq_top"]), float(fb_row["m30_liq_bot"]),
                )
                result = _extract_levels(fb_row, fb_time, fb_age_h,
                                         "m30_box_unconfirmed", daily_trend, prefix="m30")
                result.update(m30_macro)
                result["m30_bias"] = m30_bias
                result["m30_bias_confirmed"] = m30_bias_confirmed
                result["provisional_m30_bias"] = provisional_m30_bias
                return result

        if box_age_h > M30_STALE_WARN_H:
            log.warning(
                "M30 box stale: box_id=%d  age=%.1fh  (M5 unavailable -- running on M30 fallback)",
                last_confirmed_id, box_age_h,
            )
        else:
            log.info(
                "M30 levels FALLBACK (M5 unavailable) box_id=%d  age=%.1fh -- "
                "liq_top=%.2f  liq_bot=%.2f",
                last_confirmed_id, box_age_h,
                float(last_confirmed_row["m30_liq_top"]),
                float(last_confirmed_row["m30_liq_bot"]),
            )

        result = _extract_levels(last_confirmed_row, last_confirmed_time, box_age_h,
                                 "m30_box", daily_trend, prefix="m30")
        result.update(m30_macro)
        result["m30_bias"] = m30_bias
        result["m30_bias_confirmed"] = m30_bias_confirmed
        result["provisional_m30_bias"] = provisional_m30_bias
        return result

    # No confirmed M30 boxes
    unconf_all = m30_df[
        (m30_df["m30_box_id"].notna()) &
        (m30_df["m30_box_id"] > 0) &
        (m30_df["m30_liq_top"].notna())
    ]
    if not unconf_all.empty:
        fb_row   = unconf_all.iloc[-1]
        fb_time  = unconf_all.index[-1]
        fb_age_h = (now_utc - fb_time.to_pydatetime()).total_seconds() / 3600.0
        log.warning(
            "No confirmed M30 box (M5 unavailable) -- using unconfirmed box_%d",
            int(fb_row["m30_box_id"]),
        )
        result = _extract_levels(fb_row, fb_time, fb_age_h,
                                 "m30_box_unconfirmed", daily_trend, prefix="m30")
        result.update(m30_macro)
        result["m30_bias"] = m30_bias
        result["m30_bias_confirmed"] = m30_bias_confirmed
        result["provisional_m30_bias"] = provisional_m30_bias
        return result

    log.warning("No confirmed box in M5 or M30 -- system waiting for updaters")
    return None


# ---------------------------------------------------------------------------
# Direction-aware API (Sprint A entry_logic_fix_20260420, literatura-aligned)
# ---------------------------------------------------------------------------
# Ordering rationale (Wyckoff/ICT/ATS unanimous):
#   1. is_valid_direction DESC  -- MANDATORY: bias absolute (premium->short, discount->long)
#   2. source priority DESC     -- m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed
#   3. age_min ASC              -- fresh zones have higher reaction probability
#   4. distance_to_price ASC    -- tactical tiebreaker
# ---------------------------------------------------------------------------

# Band computation (mirrors event_processor.NEAR_ATR_FACTOR / NEAR_FLOOR_PTS)
_LD_NEAR_ATR_FACTOR = 1.0
_LD_NEAR_FLOOR_PTS  = 5.0

_SOURCE_PRIORITY = {
    "m5_confirmed":    4,
    "m5_unconfirmed":  3,
    "m30_confirmed":   2,
    "m30_unconfirmed": 1,
}


@dataclass(frozen=True)
class LevelCandidate:
    """Single level candidate with directional awareness.

    Literature reference:
      - Wyckoff: Spring (near liq_bot -> LONG), UTAD (near liq_top -> SHORT)
      - ICT: Premium PD array (above price) -> SHORT; Discount PD array (below) -> LONG
      - ATS: Overvalued (above expansion line) -> SHORT in downtrend;
             Undervalued (below expansion line) -> LONG in uptrend
    """
    box_id: int
    level: float                       # liq_top for SHORT, liq_bot for LONG (MT5 space)
    level_gc: float                    # same level in GC space
    source: Literal[
        "m5_confirmed", "m5_unconfirmed",
        "m30_confirmed", "m30_unconfirmed",
    ]
    age_min: float                     # age since bar_time
    distance_to_price: float           # always >= 0; |level - price|
    is_valid_direction: bool           # SHORT: level > price. LONG: level < price. distance==0 => True.
    band: float                        # ATR-derived proximity band at the time of the box
    timeframe: Literal["M5", "M30"]
    is_touch: bool = False             # True iff distance_to_price == 0.0


def get_levels_for_direction(
    direction: Literal["SHORT", "LONG"],
    price: float,
    max_age_min: float = 15.0,
    max_distance_pts: float = 8.0,
    gc_to_mt5_offset: float | None = None,
) -> list[LevelCandidate]:
    """Return LevelCandidate list SORTED literatura-aligned.

    Sort order:
      1. is_valid_direction DESC  (valid first)
      2. source priority DESC     (m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed)
      3. age_min ASC              (fresher first)
      4. distance_to_price ASC    (tiebreaker)

    Parameters
    ----------
    direction         : "SHORT" | "LONG"
    price             : current MT5 XAUUSD price
    max_age_min       : discard candidates older than this (default 15min, aligned with M5_FALLBACK_H)
    max_distance_pts  : informational; populated in band field, not used to filter
    gc_to_mt5_offset  : GC->MT5 offset (default = module GC_MT5_OFFSET=31.0)

    Returns
    -------
    list[LevelCandidate]: possibly empty. Empty list means "no directionally valid levels".
    """
    if direction not in ("SHORT", "LONG"):
        raise ValueError(f"direction must be 'SHORT' or 'LONG', got {direction!r}")

    offset = GC_MT5_OFFSET if gc_to_mt5_offset is None else float(gc_to_mt5_offset)
    now_utc = datetime.now(timezone.utc)
    candidates: list[LevelCandidate] = []

    def _build_candidate(row, bar_time, source: str, tf: str) -> LevelCandidate | None:
        try:
            prefix = tf.lower()  # "m5" or "m30"
            col_top = f"{prefix}_liq_top"
            col_bot = f"{prefix}_liq_bot"
            col_id  = f"{prefix}_box_id"
            if pd.isna(row[col_top]) or pd.isna(row[col_bot]):
                return None

            liq_top_gc = float(row[col_top])
            liq_bot_gc = float(row[col_bot])
            box_id     = int(row[col_id])
            atr14      = float(row["atr14"]) if ("atr14" in row.index and pd.notna(row["atr14"])) else 20.0

            level_gc  = liq_top_gc if direction == "SHORT" else liq_bot_gc
            level_mt5 = round(level_gc - offset, 2)

            age_min = (now_utc - bar_time.to_pydatetime()).total_seconds() / 60.0
            if age_min > max_age_min:
                return None

            distance = abs(level_mt5 - price)
            is_touch = (distance == 0.0)
            if direction == "SHORT":
                is_valid = (level_mt5 > price) or is_touch
            else:  # LONG
                is_valid = (level_mt5 < price) or is_touch

            band = max(atr14 * _LD_NEAR_ATR_FACTOR, _LD_NEAR_FLOOR_PTS)

            return LevelCandidate(
                box_id=box_id,
                level=level_mt5,
                level_gc=round(level_gc, 2),
                source=source,  # type: ignore[arg-type]
                age_min=round(age_min, 2),
                distance_to_price=round(distance, 2),
                is_valid_direction=bool(is_valid),
                band=round(band, 2),
                timeframe=tf,  # type: ignore[arg-type]
                is_touch=is_touch,
            )
        except Exception as e:
            log.warning("get_levels_for_direction _build_candidate failed (%s): %s", source, e)
            return None

    m5_df  = _load_m5_boxes()
    m30_df = _load_m30_boxes()

    # --- M5 path ---
    last_confirmed_m5_id = -1
    if m5_df is not None:
        confirmed = m5_df[m5_df["m5_box_confirmed"] == True]
        if not confirmed.empty:
            last_confirmed_m5_id = int(confirmed.iloc[-1]["m5_box_id"])
            cand = _build_candidate(confirmed.iloc[-1], confirmed.index[-1], "m5_confirmed", "M5")
            if cand is not None:
                candidates.append(cand)

        unconf = m5_df[
            (m5_df["m5_box_confirmed"] == False) &
            (m5_df["m5_box_id"].notna()) &
            (m5_df["m5_box_id"] > last_confirmed_m5_id) &
            (m5_df["m5_liq_top"].notna())
        ]
        if not unconf.empty:
            cand = _build_candidate(unconf.iloc[-1], unconf.index[-1], "m5_unconfirmed", "M5")
            if cand is not None:
                candidates.append(cand)

    # --- M30 path ---
    last_confirmed_m30_id = -1
    if m30_df is not None:
        confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
        if not confirmed.empty:
            last_confirmed_m30_id = int(confirmed.iloc[-1]["m30_box_id"])
            cand = _build_candidate(confirmed.iloc[-1], confirmed.index[-1], "m30_confirmed", "M30")
            if cand is not None:
                candidates.append(cand)

        unconf = m30_df[
            (m30_df["m30_box_confirmed"] == False) &
            (m30_df["m30_box_id"].notna()) &
            (m30_df["m30_box_id"] > last_confirmed_m30_id) &
            (m30_df["m30_liq_top"].notna())
        ]
        if not unconf.empty:
            cand = _build_candidate(unconf.iloc[-1], unconf.index[-1], "m30_unconfirmed", "M30")
            if cand is not None:
                candidates.append(cand)

    candidates.sort(
        key=lambda c: (
            not c.is_valid_direction,            # is_valid_direction DESC
            -_SOURCE_PRIORITY[c.source],         # source priority DESC
            c.age_min,                           # age_min ASC
            c.distance_to_price,                 # distance ASC
        )
    )
    return candidates

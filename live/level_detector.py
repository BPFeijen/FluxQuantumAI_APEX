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
from datetime import datetime, timezone
from pathlib import Path

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
      is_confirmed_source: True when derived from a confirmed M30 box.
    """
    if m30_df is None:
        return "unknown", False

    def _classify(row) -> str:
        import math
        box_high = row.get("m30_box_high", float("nan"))
        box_low  = row.get("m30_box_low",  float("nan"))
        liq_top  = row.get("m30_liq_top",  float("nan"))
        liq_bot  = row.get("m30_liq_bot",  float("nan"))

        if math.isnan(liq_top) or math.isnan(box_high):
            return "unknown"
        if liq_top > box_high:
            return "bullish"
        if not math.isnan(liq_bot) and not math.isnan(box_low) and liq_bot < box_low:
            return "bearish"
        return "unknown"

    try:
        confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
        if not confirmed.empty:
            return _classify(confirmed.iloc[-1]), True

        if confirmed_only:
            return "unknown", False

        unconf = m30_df[
            (m30_df["m30_box_id"].notna()) &
            (m30_df["m30_box_id"] > 0) &
            (m30_df["m30_liq_top"].notna())
        ]
        if unconf.empty:
            return "unknown", False
        return _classify(unconf.iloc[-1]), False
    except Exception as e:
        log.warning("derive_m30_bias failed: %s", e)
        return "unknown", False


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

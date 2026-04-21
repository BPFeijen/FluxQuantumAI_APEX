#!/usr/bin/env python3
"""
backtest_position_manager.py — Position Manager Backtest (Jul 2025 → Apr 2026)
===============================================================================

Replays ATS signals from gc_ats_features_v5.parquet through the Position Manager
checks (SHIELD, L2 Danger, Regime Flip, Cascade) using historical M30/M1 data.

Also generates the new protection_advice fields (ANOMALY + ICEBERG) for each
simulated trade to validate the normalised observability schema.

Data sources:
  - gc_ats_features_v5.parquet    → entry signals with SL/TP/ATR (Jul 2025+)
  - gc_m30_boxes.parquet          → M30 OHLC + levels (regime, bias, delta_4h proxy)
  - databento_l2_m1_features.parquet → 1-min dom_imbalance, bar_delta (Jul-Nov 2025)
  - depth_snapshots (Dec 2025+)   → live microstructure continuation

Output:
  - C:/FluxQuantumAI/logs/backtest_posmon_results.jsonl (one line per trade)
  - C:/FluxQuantumAI/logs/backtest_posmon_summary.json  (aggregated stats)
  - Console: per-trade result + protection advice + final summary
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ATS_PATH      = Path("C:/data/processed/gc_ats_features_v5.parquet")
M30_PATH      = Path("C:/data/processed/gc_m30_boxes.parquet")
M1_L2_PATH    = Path("C:/data/processed/databento_l2_m1_features.parquet")
SETTINGS_PATH = Path("C:/FluxQuantumAI/config/settings.json")
OUTPUT_JSONL   = Path("C:/FluxQuantumAI/logs/backtest_posmon_results.jsonl")
OUTPUT_SUMMARY = Path("C:/FluxQuantumAI/logs/backtest_posmon_summary.json")

# ---------------------------------------------------------------------------
# Thresholds (from settings.json, same as live position_monitor.py)
# ---------------------------------------------------------------------------
def _load_settings() -> dict:
    defaults = {
        "delta_4h_short_block": 3000,
        "delta_4h_long_block": -1050,
        "trend_resumption_threshold_short": -800,
        "delta_flip_min_bars": 47,
        "trailing_stop_pts": 77,
        "DANGER_THRESHOLD": 70,
        "DANGER_BARS": 3,
        "CASCADE_ATR_FACTOR": 2.0,
        "CASCADE_WINDOW_MIN": 5,
    }
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            s = json.load(f)
        defaults.update(s)
    except Exception:
        pass
    return defaults


SETTINGS = _load_settings()

# Position manager constants (matching live)
DANGER_THRESHOLD     = int(SETTINGS.get("DANGER_THRESHOLD", 70))
DANGER_BARS          = int(SETTINGS.get("DANGER_BARS", 3))
CASCADE_ATR_FACTOR   = float(SETTINGS.get("CASCADE_ATR_FACTOR", 2.0))
CASCADE_WINDOW_MIN   = int(SETTINGS.get("CASCADE_WINDOW_MIN", 5))
TRAILING_STOP_PTS    = float(SETTINGS.get("trailing_stop_pts", 77))
DELTA_4H_LONG_BLOCK  = float(SETTINGS.get("delta_4h_long_block", -1050))
DELTA_4H_SHORT_BLOCK = float(SETTINGS.get("delta_4h_short_block", 3000))
TREND_RESUME_SHORT   = float(SETTINGS.get("trend_resumption_threshold_short", -800))
DELTA_FLIP_MIN_BARS  = int(SETTINGS.get("delta_flip_min_bars", 47))


# ---------------------------------------------------------------------------
# Protection Advice (same logic as event_processor.py — rule-based, shadow only)
# ---------------------------------------------------------------------------
def build_protection_advice(
    source: str,
    alignment: str = "UNKNOWN",
    severity: str = "NONE",
    entry_action: str = "UNKNOWN",
    position_action: str = "UNKNOWN",
    reason: str = "",
) -> dict:
    return {
        "alignment": alignment,
        "severity": severity,
        "entry_action": entry_action,
        "position_action": position_action,
        "reason": reason,
        "shadow_only": True,
        "source": source,
        "rule_based": True,
    }


def build_anomaly_protection(
    spread: float, dom_imbalance: float,
    total_bid_depth: float, total_ask_depth: float,
    spread_median: float,
) -> dict:
    """
    Simulate Defense Mode Z-score anomaly detection.
    Rule-based thresholds matching live GrenadierDefenseMode.
    """
    fired = []
    # R1: spread spike > 5x median
    if spread_median > 0 and spread / spread_median > 5.0:
        fired.append(f"spread_spike={spread/spread_median:.1f}x")
    # R2: depth collapse (bid+ask very low vs typical)
    total_depth = total_bid_depth + total_ask_depth
    if total_depth < 50:  # near-zero book
        fired.append(f"depth_collapse={total_depth:.0f}")
    # R6: DOM extreme
    if abs(dom_imbalance) > 0.80:
        fired.append(f"dom_extreme={dom_imbalance:.3f}")

    n_fired = len(fired)
    if n_fired >= 2:
        sev = "CRITICAL"
        entry = "BLOCK"
        pos_act = "EXIT"
    elif n_fired == 1:
        sev = "HIGH"
        entry = "BLOCK"
        pos_act = "TIGHTEN_SL"
    else:
        sev = "NONE"
        entry = "ALLOW"
        pos_act = "HOLD"

    return build_protection_advice(
        source="DEFENSE_MODE",
        alignment="UNKNOWN",
        severity=sev,
        entry_action=entry,
        position_action=pos_act,
        reason=", ".join(fired) if fired else "",
    )


def build_iceberg_protection(
    has_iceberg: bool, ice_side: str, direction: str,
    ice_prob: float, ice_refills: int,
) -> dict:
    """Simulate iceberg protection from historical iceberg proxy data."""
    if not has_iceberg or ice_prob < 0.50:
        return build_protection_advice("ICEBERG")

    side_up = ice_side.upper()
    is_aligned = (
        (side_up in ("BUY", "BID", "LONG") and direction == "LONG") or
        (side_up in ("SELL", "ASK", "SHORT") and direction == "SHORT")
    )
    is_opposed = (
        (side_up in ("BUY", "BID", "LONG") and direction == "SHORT") or
        (side_up in ("SELL", "ASK", "SHORT") and direction == "LONG")
    )
    align = "ALIGNED" if is_aligned else ("OPPOSED" if is_opposed else "NEUTRAL")

    if ice_prob >= 0.85 and ice_refills >= 5:
        sev = "CRITICAL"
    elif ice_prob >= 0.85:
        sev = "HIGH"
    elif ice_prob >= 0.70:
        sev = "MEDIUM"
    else:
        sev = "LOW"

    if is_opposed and sev == "CRITICAL":
        entry, pos_act = "BLOCK", "EXIT"
    elif is_opposed and sev == "HIGH":
        entry, pos_act = "REDUCE", "TIGHTEN_SL"
    else:
        entry, pos_act = "ALLOW", "HOLD"

    return build_protection_advice(
        source="ICEBERG",
        alignment=align,
        severity=sev,
        entry_action=entry,
        position_action=pos_act,
        reason=f"ice side={ice_side} prob={ice_prob:.2f} refills={ice_refills}",
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data():
    print("[LOAD] Loading datasets...")
    ats = pd.read_parquet(ATS_PATH)
    m30 = pd.read_parquet(M30_PATH)
    m1  = pd.read_parquet(M1_L2_PATH)

    # Ensure timezone-aware indices
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    if m1.index.tz is None:
        m1.index = m1.index.tz_localize("UTC")
    if ats.index.tz is None:
        ats.index = ats.index.tz_localize("UTC")

    print(f"  ATS features: {len(ats)} rows ({ats.index.min()} -> {ats.index.max()})")
    print(f"  M30 boxes:    {len(m30)} rows ({m30.index.min()} -> {m30.index.max()})")
    print(f"  M1 L2:        {len(m1)} rows  ({m1.index.min()} -> {m1.index.max()})")

    return ats, m30, m1


# ---------------------------------------------------------------------------
# Microstructure helpers (same logic as position_monitor.py)
# ---------------------------------------------------------------------------
def danger_scores_last_3_m30(
    m1_window: pd.DataFrame, direction: str,
) -> list[float]:
    """Compute danger_score for last 3 M30 bars from 1-min data."""
    if m1_window is None or m1_window.empty:
        return []
    df2 = m1_window.copy()
    df2["m30"] = df2.index.floor("30min")
    grouped = df2.groupby("m30").agg(
        bar_delta=("bar_delta", "sum"),
        dom_imbalance=("dom_imbalance", "mean"),
    ).sort_index()
    last3 = grouped.tail(3)
    scores = []
    for _, row in last3.iterrows():
        bd = float(row["bar_delta"])
        dom = float(row["dom_imbalance"])
        against = (direction == "LONG" and bd < 0) or (direction == "SHORT" and bd > 0)
        score = round(100.0 * abs(dom), 1) if against else 0.0
        scores.append(score)
    return scores


def compute_delta_4h(m1_data: pd.DataFrame, as_of: pd.Timestamp) -> float:
    """Sum bar_delta over 4 hours before as_of."""
    cutoff = as_of - pd.Timedelta(hours=4)
    window = m1_data[(m1_data.index >= cutoff) & (m1_data.index <= as_of)]
    if window.empty:
        return 0.0
    return float(window["bar_delta"].sum())


def derive_m30_bias(m30_data: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """Same logic as position_monitor._derive_m30_bias."""
    import math
    sub = m30_data[m30_data.index <= as_of]
    if sub.empty:
        return "unknown"
    try:
        confirmed = sub[sub["m30_box_confirmed"] == True]
        row = confirmed.iloc[-1] if not confirmed.empty else sub.iloc[-1]
        box_high = float(row.get("m30_box_high", float("nan")))
        box_low  = float(row.get("m30_box_low",  float("nan")))
        liq_top  = float(row.get("m30_liq_top",  float("nan")))
        liq_bot  = float(row.get("m30_liq_bot",  float("nan")))
        if math.isnan(liq_top) or math.isnan(box_high):
            return "unknown"
        if liq_top > box_high:
            return "bullish"
        if not math.isnan(liq_bot) and not math.isnan(box_low) and liq_bot < box_low:
            return "bearish"
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Position simulation
# ---------------------------------------------------------------------------
def simulate_trade(
    signal_ts: pd.Timestamp,
    direction: str,
    entry_price: float,
    sl: float,
    tp1: float,
    tp2: float,
    atr: float,
    m30: pd.DataFrame,
    m1: pd.DataFrame,
    ats_row: pd.Series,
) -> dict:
    """
    Simulate one trade through all Position Manager checks.
    Walk forward in 1-minute bars from entry until exit or max 24h.
    """
    sign = 1.0 if direction == "LONG" else -1.0

    # State (matches position_monitor state dict)
    state = {
        "shield_done": False,
        "danger_streak": 0,
        "regime_flip_streak": 0,
        "best_price": entry_price,
        "trailing_sl": None,
    }

    # Walk forward from entry in M1 bars
    entry_ts = signal_ts
    max_ts = entry_ts + pd.Timedelta(hours=24)

    # Get M1 data window (entry to +24h)
    m1_future = m1[(m1.index >= entry_ts) & (m1.index <= max_ts)]
    if m1_future.empty:
        return _build_result(
            signal_ts, direction, entry_price, sl, tp1, tp2, atr,
            "NO_DATA", entry_price, signal_ts, 0.0,
            state, [], {}, ats_row,
        )

    checks_log = []
    exit_reason = None
    exit_price = entry_price
    exit_ts = max_ts
    pnl_pts = 0.0

    for bar_ts, bar in m1_future.iterrows():
        bar_mid = float(bar.get("mid_price", entry_price))
        bar_delta = float(bar.get("bar_delta", 0.0))
        bar_dom = float(bar.get("dom_imbalance", 0.0))
        elapsed_min = (bar_ts - entry_ts).total_seconds() / 60.0

        # Price movement
        pnl_now = sign * (bar_mid - entry_price)

        # Track best price for trailing
        if pnl_now > sign * (state["best_price"] - entry_price):
            state["best_price"] = bar_mid

        # ── CHECK: SL hit ────────────────────────────────────
        if direction == "LONG" and bar_mid <= sl:
            exit_reason = "SL_HIT"
            exit_price = sl
            exit_ts = bar_ts
            break
        elif direction == "SHORT" and bar_mid >= sl:
            exit_reason = "SL_HIT"
            exit_price = sl
            exit_ts = bar_ts
            break

        # ── CHECK 1: SHIELD (TP1 hit) ────────────────────────
        if not state["shield_done"]:
            if direction == "LONG" and bar_mid >= tp1:
                state["shield_done"] = True
                sl = entry_price  # breakeven
                state["trailing_sl"] = entry_price
                checks_log.append({
                    "ts": str(bar_ts), "check": "SHIELD",
                    "detail": f"TP1 hit at {bar_mid:.2f}, SL moved to entry {entry_price:.2f}",
                    "elapsed_min": round(elapsed_min, 1),
                })
            elif direction == "SHORT" and bar_mid <= tp1:
                state["shield_done"] = True
                sl = entry_price
                state["trailing_sl"] = entry_price
                checks_log.append({
                    "ts": str(bar_ts), "check": "SHIELD",
                    "detail": f"TP1 hit at {bar_mid:.2f}, SL moved to entry {entry_price:.2f}",
                    "elapsed_min": round(elapsed_min, 1),
                })

        # ── CHECK: TP2 hit ───────────────────────────────────
        if direction == "LONG" and bar_mid >= tp2:
            exit_reason = "TP2_HIT"
            exit_price = tp2
            exit_ts = bar_ts
            break
        elif direction == "SHORT" and bar_mid <= tp2:
            exit_reason = "TP2_HIT"
            exit_price = tp2
            exit_ts = bar_ts
            break

        # ── CHECK 2: L2 Danger ───────────────────────────────
        m1_lookback = m1[(m1.index >= bar_ts - pd.Timedelta(minutes=90)) & (m1.index <= bar_ts)]
        danger_scores = danger_scores_last_3_m30(m1_lookback, direction)
        if len(danger_scores) >= DANGER_BARS:
            all_danger = all(s >= DANGER_THRESHOLD for s in danger_scores[-DANGER_BARS:])
            if all_danger:
                exit_reason = "L2_DANGER"
                exit_price = bar_mid
                exit_ts = bar_ts
                checks_log.append({
                    "ts": str(bar_ts), "check": "L2_DANGER",
                    "detail": f"danger_scores={danger_scores} >= {DANGER_THRESHOLD} for {DANGER_BARS} bars",
                    "elapsed_min": round(elapsed_min, 1),
                })
                break

        # ── CHECK 3: Regime Flip ─────────────────────────────
        if not state["shield_done"]:
            delta_4h = compute_delta_4h(m1, bar_ts)
            m30_bias = derive_m30_bias(m30, bar_ts)

            flip_condition = False
            flip_reason = ""
            if direction == "LONG" and delta_4h < DELTA_4H_LONG_BLOCK:
                flip_condition = True
                flip_reason = f"delta_4h={delta_4h:+.0f} < {DELTA_4H_LONG_BLOCK:.0f}"
            elif direction == "SHORT" and delta_4h < TREND_RESUME_SHORT and m30_bias == "bullish":
                flip_condition = True
                flip_reason = f"delta_4h={delta_4h:+.0f} < {TREND_RESUME_SHORT:.0f} AND m30_bias=bullish"

            if flip_condition:
                state["regime_flip_streak"] += 1
            else:
                state["regime_flip_streak"] = 0

            # delta_flip_min_bars: in live, each check is 2s apart.
            # In M1 backtest, each bar = 60s. Scale: 47 bars * 2s = 94s ≈ 2 M1 bars.
            min_bars_m1 = max(2, DELTA_FLIP_MIN_BARS // 30)
            if state["regime_flip_streak"] >= min_bars_m1:
                exit_reason = "REGIME_FLIP"
                exit_price = bar_mid
                exit_ts = bar_ts
                checks_log.append({
                    "ts": str(bar_ts), "check": "REGIME_FLIP",
                    "detail": flip_reason,
                    "delta_4h": round(delta_4h, 0),
                    "m30_bias": m30_bias,
                    "elapsed_min": round(elapsed_min, 1),
                })
                break

        # ── CHECK 4: Cascade ─────────────────────────────────
        if elapsed_min <= CASCADE_WINDOW_MIN:
            move_against = -sign * (bar_mid - entry_price)
            cascade_thr = CASCADE_ATR_FACTOR * atr
            if move_against > cascade_thr:
                exit_reason = "CASCADE"
                exit_price = bar_mid
                exit_ts = bar_ts
                checks_log.append({
                    "ts": str(bar_ts), "check": "CASCADE",
                    "detail": f"move_against={move_against:.2f} > {cascade_thr:.2f} (2x ATR={atr:.2f})",
                    "elapsed_min": round(elapsed_min, 1),
                })
                break

        # ── CHECK 5: Trailing stop (post-SHIELD) ─────────────
        if state["shield_done"] and state["trailing_sl"] is not None:
            trail_target = bar_mid - sign * TRAILING_STOP_PTS if direction == "LONG" \
                else bar_mid + sign * TRAILING_STOP_PTS
            # Only tighten, never loosen
            if direction == "LONG":
                new_sl = max(state["trailing_sl"], bar_mid - TRAILING_STOP_PTS)
                state["trailing_sl"] = new_sl
                if bar_mid <= new_sl:
                    exit_reason = "TRAILING_STOP"
                    exit_price = new_sl
                    exit_ts = bar_ts
                    checks_log.append({
                        "ts": str(bar_ts), "check": "TRAILING_STOP",
                        "detail": f"price={bar_mid:.2f} <= trailing_sl={new_sl:.2f}",
                        "elapsed_min": round(elapsed_min, 1),
                    })
                    break
            else:
                new_sl = min(state["trailing_sl"], bar_mid + TRAILING_STOP_PTS)
                state["trailing_sl"] = new_sl
                if bar_mid >= new_sl:
                    exit_reason = "TRAILING_STOP"
                    exit_price = new_sl
                    exit_ts = bar_ts
                    checks_log.append({
                        "ts": str(bar_ts), "check": "TRAILING_STOP",
                        "detail": f"price={bar_mid:.2f} >= trailing_sl={new_sl:.2f}",
                        "elapsed_min": round(elapsed_min, 1),
                    })
                    break

    # If no exit triggered, use last price
    if exit_reason is None:
        exit_reason = "TIMEOUT_24H"
        last_bar = m1_future.iloc[-1]
        exit_price = float(last_bar.get("mid_price", entry_price))
        exit_ts = m1_future.index[-1]

    pnl_pts = sign * (exit_price - entry_price)

    # Build protection advice for this trade
    # Use M1 data at entry time for anomaly detection
    m1_at_entry = m1[(m1.index >= entry_ts - pd.Timedelta(minutes=30)) & (m1.index <= entry_ts)]
    protection = _build_trade_protection(m1_at_entry, direction, ats_row)

    return _build_result(
        signal_ts, direction, entry_price, sl, tp1, tp2, atr,
        exit_reason, exit_price, exit_ts, pnl_pts,
        state, checks_log, protection, ats_row,
    )


def _build_trade_protection(
    m1_window: pd.DataFrame, direction: str, ats_row: pd.Series,
) -> dict:
    """Build protection advice for a simulated trade."""
    # Anomaly protection from microstructure at entry
    if m1_window is not None and not m1_window.empty:
        last = m1_window.iloc[-1]
        spread = float(last.get("spread", 0.0))
        dom_imb = float(last.get("dom_imbalance", 0.0))
        bid_depth = float(last.get("total_bid_depth", 500.0))
        ask_depth = float(last.get("total_ask_depth", 500.0))
        spread_med = float(m1_window["spread"].median()) if "spread" in m1_window.columns else spread
        anomaly = build_anomaly_protection(spread, dom_imb, bid_depth, ask_depth, spread_med)
    else:
        anomaly = build_protection_advice("DEFENSE_MODE", entry_action="ALLOW", position_action="HOLD")

    # Iceberg protection from ATS features (proxy: l2_large_order_imbalance as iceberg proxy)
    loi = float(ats_row.get("l2_large_order_imbalance", 0.0))
    has_ice = abs(loi) > 0.50
    if has_ice:
        ice_side = "BUY" if loi > 0 else "SELL"
        ice_prob = min(abs(loi), 1.0)
        ice_refills = 3 if abs(loi) > 0.70 else 2
        iceberg = build_iceberg_protection(True, ice_side, direction, ice_prob, ice_refills)
    else:
        iceberg = build_protection_advice("ICEBERG")

    return {"anomaly": anomaly, "iceberg": iceberg}


def _build_result(
    signal_ts, direction, entry_price, sl, tp1, tp2, atr,
    exit_reason, exit_price, exit_ts, pnl_pts,
    state, checks_log, protection, ats_row,
) -> dict:
    duration_min = (exit_ts - signal_ts).total_seconds() / 60.0 if hasattr(exit_ts, 'total_seconds') or isinstance(exit_ts, pd.Timestamp) else 0.0
    try:
        duration_min = (exit_ts - signal_ts).total_seconds() / 60.0
    except Exception:
        duration_min = 0.0

    return {
        "signal_ts": str(signal_ts),
        "direction": direction,
        "entry_price": round(entry_price, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "atr": round(atr, 2),
        "grade": str(ats_row.get("entry_grade", "")),
        "exit_reason": exit_reason,
        "exit_price": round(exit_price, 2),
        "exit_ts": str(exit_ts),
        "pnl_pts": round(pnl_pts, 2),
        "duration_min": round(duration_min, 1),
        "shield_activated": state.get("shield_done", False),
        "checks_fired": [c["check"] for c in checks_log],
        "checks_log": checks_log,
        "protection": protection,
        "daily_trend": str(ats_row.get("daily_trend", "")),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ats, m30, m1 = load_data()

    # Filter signals: Jul 2025 → today
    start = pd.Timestamp("2025-07-01", tz="UTC")
    signals = ats[
        (ats.index >= start) &
        ((ats["entry_long"] == True) | (ats["entry_short"] == True))
    ].copy()
    signals["direction"] = "LONG"
    signals.loc[signals["entry_short"] == True, "direction"] = "SHORT"

    print(f"\n[BACKTEST] {len(signals)} signals from {start.date()} to {ats.index.max().date()}")
    print(f"[BACKTEST] Thresholds: DANGER={DANGER_THRESHOLD}/{DANGER_BARS}bars "
          f"CASCADE={CASCADE_ATR_FACTOR}xATR/{CASCADE_WINDOW_MIN}min "
          f"TRAIL={TRAILING_STOP_PTS}pts "
          f"REGIME_FLIP: LONG<{DELTA_4H_LONG_BLOCK} SHORT<{TREND_RESUME_SHORT}+bullish")
    print("=" * 100)

    results = []
    for ts_idx, row in signals.iterrows():
        direction = row["direction"]
        entry_price = float(row["entry_price"])
        atr_val = float(row["m30_atr"])

        if direction == "LONG":
            sl  = float(row["sl_long"])  if pd.notna(row.get("sl_long"))  else entry_price - atr_val
            tp1 = float(row["tp1_long"]) if pd.notna(row.get("tp1_long")) else entry_price + atr_val
            tp2 = tp1 + atr_val  # estimate TP2 = TP1 + ATR
        else:
            sl  = float(row["sl_short"])  if pd.notna(row.get("sl_short"))  else entry_price + atr_val
            tp1 = float(row["tp1_short"]) if pd.notna(row.get("tp1_short")) else entry_price - atr_val
            tp2 = tp1 - atr_val

        result = simulate_trade(
            signal_ts=ts_idx,
            direction=direction,
            entry_price=entry_price,
            sl=sl, tp1=tp1, tp2=tp2,
            atr=atr_val,
            m30=m30, m1=m1,
            ats_row=row,
        )
        results.append(result)

        # Console output
        _pnl = result["pnl_pts"]
        _pnl_str = f"{_pnl:+.2f}" if _pnl != 0 else "0.00"
        _shield = "SHIELD" if result["shield_activated"] else "      "
        _prot_a = result["protection"].get("anomaly", {})
        _prot_i = result["protection"].get("iceberg", {})
        print(
            f"  {result['signal_ts'][:16]}  {direction:5s}  "
            f"entry={entry_price:.2f}  exit={result['exit_price']:.2f}  "
            f"pnl={_pnl_str:>7s}  {_shield}  "
            f"exit={result['exit_reason']:15s}  "
            f"dur={result['duration_min']:6.0f}min  "
            f"grade={result['grade']:2s}  "
            f"anom_sev={_prot_a.get('severity','?'):8s}  "
            f"ice_align={_prot_i.get('alignment','?'):7s}"
        )

    # Write results
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"\n[OUTPUT] Results written to {OUTPUT_JSONL}")

    # Summary
    n = len(results)
    if n == 0:
        print("[SUMMARY] No trades to summarise.")
        return

    pnls = [r["pnl_pts"] for r in results]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    exit_reasons = {}
    for r in results:
        er = r["exit_reason"]
        exit_reasons[er] = exit_reasons.get(er, 0) + 1

    checks_fired_counts = {}
    for r in results:
        for c in r["checks_fired"]:
            checks_fired_counts[c] = checks_fired_counts.get(c, 0) + 1

    shield_count = sum(1 for r in results if r["shield_activated"])

    # Protection advice stats
    anom_sevs = {}
    ice_aligns = {}
    for r in results:
        a_sev = r["protection"].get("anomaly", {}).get("severity", "?")
        anom_sevs[a_sev] = anom_sevs.get(a_sev, 0) + 1
        i_align = r["protection"].get("iceberg", {}).get("alignment", "?")
        ice_aligns[i_align] = ice_aligns.get(i_align, 0) + 1

    # Protection vs outcome cross-tab
    anom_block_trades = [r for r in results if r["protection"].get("anomaly", {}).get("entry_action") == "BLOCK"]
    anom_block_pnl = sum(r["pnl_pts"] for r in anom_block_trades)

    ice_opposed_trades = [r for r in results if r["protection"].get("iceberg", {}).get("alignment") == "OPPOSED"]
    ice_opposed_pnl = sum(r["pnl_pts"] for r in ice_opposed_trades)

    ice_aligned_trades = [r for r in results if r["protection"].get("iceberg", {}).get("alignment") == "ALIGNED"]
    ice_aligned_pnl = sum(r["pnl_pts"] for r in ice_aligned_trades)

    summary = {
        "period": f"{start.date()} -> {signals.index.max().date()}",
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n > 0 else 0,
        "total_pnl_pts": round(sum(pnls), 2),
        "avg_pnl_pts": round(np.mean(pnls), 2),
        "avg_win_pts": round(np.mean(wins), 2) if wins else 0,
        "avg_loss_pts": round(np.mean(losses), 2) if losses else 0,
        "profit_factor": round(abs(sum(wins) / sum(losses)), 3) if losses and sum(losses) != 0 else float("inf"),
        "shield_activated": shield_count,
        "shield_rate": round(shield_count / n * 100, 1),
        "exit_reasons": exit_reasons,
        "checks_fired": checks_fired_counts,
        "protection_stats": {
            "anomaly_severity": anom_sevs,
            "iceberg_alignment": ice_aligns,
            "anomaly_block_count": len(anom_block_trades),
            "anomaly_block_pnl": round(anom_block_pnl, 2),
            "iceberg_opposed_count": len(ice_opposed_trades),
            "iceberg_opposed_pnl": round(ice_opposed_pnl, 2),
            "iceberg_aligned_count": len(ice_aligned_trades),
            "iceberg_aligned_pnl": round(ice_aligned_pnl, 2),
        },
        "thresholds_used": {
            "DANGER_THRESHOLD": DANGER_THRESHOLD,
            "DANGER_BARS": DANGER_BARS,
            "CASCADE_ATR_FACTOR": CASCADE_ATR_FACTOR,
            "TRAILING_STOP_PTS": TRAILING_STOP_PTS,
            "DELTA_4H_LONG_BLOCK": DELTA_4H_LONG_BLOCK,
            "TREND_RESUME_SHORT": TREND_RESUME_SHORT,
            "DELTA_FLIP_MIN_BARS": DELTA_FLIP_MIN_BARS,
        },
    }

    with open(OUTPUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\n" + "=" * 100)
    print("[SUMMARY]")
    print(f"  Period:         {summary['period']}")
    print(f"  Total trades:   {n}")
    print(f"  Win rate:       {summary['win_rate']}% ({len(wins)}W / {len(losses)}L)")
    print(f"  Total PnL:      {summary['total_pnl_pts']:+.2f} pts")
    print(f"  Avg PnL:        {summary['avg_pnl_pts']:+.2f} pts")
    print(f"  Avg Win:        {summary['avg_win_pts']:+.2f} pts")
    print(f"  Avg Loss:       {summary['avg_loss_pts']:+.2f} pts")
    print(f"  Profit Factor:  {summary['profit_factor']}")
    print(f"  SHIELD rate:    {summary['shield_rate']}% ({shield_count}/{n})")
    print()
    print("  Exit reasons:")
    for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:20s}  {count:3d}  ({count/n*100:5.1f}%)")
    print()
    print("  Checks fired:")
    for check, count in sorted(checks_fired_counts.items(), key=lambda x: -x[1]):
        print(f"    {check:20s}  {count:3d}")
    print()
    print("  Protection Advice (shadow):")
    print(f"    Anomaly severity:    {anom_sevs}")
    print(f"    Iceberg alignment:   {ice_aligns}")
    if anom_block_trades:
        print(f"    Anomaly BLOCK trades: {len(anom_block_trades)} | PnL={anom_block_pnl:+.2f} (would have been avoided)")
    if ice_opposed_trades:
        print(f"    Iceberg OPPOSED:      {len(ice_opposed_trades)} | PnL={ice_opposed_pnl:+.2f}")
    if ice_aligned_trades:
        print(f"    Iceberg ALIGNED:      {len(ice_aligned_trades)} | PnL={ice_aligned_pnl:+.2f}")

    print(f"\n[OUTPUT] Summary written to {OUTPUT_SUMMARY}")


if __name__ == "__main__":
    main()

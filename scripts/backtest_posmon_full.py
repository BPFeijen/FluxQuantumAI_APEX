#!/usr/bin/env python3
"""
backtest_posmon_full.py — Position Manager Backtest Jul 2025 → Apr 2026 (FULL)
===============================================================================

Three signal sources stitched together:
  1. Jul-Sep 2025:  ATS features v5 (24 signals) + Databento M1 L2
  2. Oct-Nov 2025:  Level proximity signals generated from Databento M1 + M30 boxes
  3. Mar-Apr 2026:  64 real trades from trades.csv + Quantower microstructure

Position Manager checks: SHIELD, L2 Danger, Regime Flip, Cascade, Trailing Stop.
Protection Advice: Anomaly (rule-based) + Iceberg (proxy).

Output: comparison table BASELINE vs FILTERED (block anomaly >= HIGH).
"""

from __future__ import annotations

import gzip
import json
import time as _time
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
MICRO_DIR     = Path("C:/data/level2/_gc_xcec")
TRADES_CSV    = Path("C:/FluxQuantumAI/logs/trades.csv")
SETTINGS_PATH = Path("C:/FluxQuantumAI/config/settings.json")
OUTPUT_JSONL   = Path("C:/FluxQuantumAI/logs/backtest_posmon_full_results.jsonl")
OUTPUT_SUMMARY = Path("C:/FluxQuantumAI/logs/backtest_posmon_full_comparison.json")

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
def _load_settings() -> dict:
    defaults = {
        "delta_4h_short_block": 3000,
        "delta_4h_long_block": -1050,
        "trend_resumption_threshold_short": -800,
        "delta_flip_min_bars": 47,
        "trailing_stop_pts": 77,
    }
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            defaults.update(json.load(f))
    except Exception:
        pass
    return defaults

SETTINGS = _load_settings()
DANGER_THRESHOLD     = 70
DANGER_BARS          = 3
CASCADE_ATR_FACTOR   = 2.0
CASCADE_WINDOW_MIN   = 5
TRAILING_STOP_PTS    = float(SETTINGS.get("trailing_stop_pts", 77))
DELTA_4H_LONG_BLOCK  = float(SETTINGS.get("delta_4h_long_block", -1050))
TREND_RESUME_SHORT   = float(SETTINGS.get("trend_resumption_threshold_short", -800))
DELTA_FLIP_MIN_BARS  = int(SETTINGS.get("delta_flip_min_bars", 47))


# ---------------------------------------------------------------------------
# Protection Advice
# ---------------------------------------------------------------------------
def build_protection(source, **kw) -> dict:
    return {
        "alignment": kw.get("alignment", "UNKNOWN"),
        "severity": kw.get("severity", "NONE"),
        "entry_action": kw.get("entry_action", "UNKNOWN"),
        "position_action": kw.get("position_action", "UNKNOWN"),
        "reason": kw.get("reason", ""),
        "shadow_only": True, "source": source, "rule_based": True,
    }

def anomaly_from_micro(m1_win: pd.DataFrame) -> dict:
    if m1_win is None or m1_win.empty:
        return build_protection("DEFENSE_MODE", entry_action="ALLOW", position_action="HOLD")
    last = m1_win.iloc[-1]
    spread = float(last.get("spread", 0.0))
    dom = float(last.get("dom_imbalance", 0.0))
    bid_d = float(last.get("total_bid_depth", 500.0))
    ask_d = float(last.get("total_ask_depth", 500.0))
    spr_med = float(m1_win["spread"].median()) if "spread" in m1_win.columns and len(m1_win) > 1 else max(spread, 0.01)
    fired = []
    if spr_med > 0 and spread / spr_med > 5.0:
        fired.append(f"spread_spike={spread/spr_med:.1f}x")
    if (bid_d + ask_d) < 50:
        fired.append(f"depth_collapse={bid_d+ask_d:.0f}")
    if abs(dom) > 0.80:
        fired.append(f"dom_extreme={dom:.3f}")
    n = len(fired)
    if n >= 2:
        return build_protection("DEFENSE_MODE", severity="CRITICAL", entry_action="BLOCK",
                                position_action="EXIT", reason=", ".join(fired))
    elif n == 1:
        return build_protection("DEFENSE_MODE", severity="HIGH", entry_action="BLOCK",
                                position_action="TIGHTEN_SL", reason=fired[0])
    return build_protection("DEFENSE_MODE", severity="NONE", entry_action="ALLOW", position_action="HOLD")


# ---------------------------------------------------------------------------
# Microstructure reader for Quantower files (Dec 2025+)
# ---------------------------------------------------------------------------
_micro_cache = {}

def read_micro_for_date(date_str: str) -> Optional[pd.DataFrame]:
    if date_str in _micro_cache:
        return _micro_cache[date_str]
    for suf in [".fixed.csv.gz", ".csv.gz"]:
        p = MICRO_DIR / f"microstructure_{date_str}{suf}"
        if p.exists():
            try:
                df = pd.read_csv(p, usecols=lambda c: c in [
                    "recv_timestamp", "timestamp", "dom_imbalance", "bar_delta",
                    "mid_price", "spread", "total_bid_size", "total_ask_size",
                ])
                ts_col = "recv_timestamp" if "recv_timestamp" in df.columns else "timestamp"
                df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="ISO8601")
                df = df.set_index(ts_col).sort_index()
                # Rename for consistency with Databento
                rename = {}
                if "total_bid_size" in df.columns:
                    rename["total_bid_size"] = "total_bid_depth"
                if "total_ask_size" in df.columns:
                    rename["total_ask_size"] = "total_ask_depth"
                if rename:
                    df = df.rename(columns=rename)
                _micro_cache[date_str] = df
                return df
            except Exception as e:
                print(f"  [WARN] Failed to read {p.name}: {e}")
    _micro_cache[date_str] = None
    return None


# ---------------------------------------------------------------------------
# Microstructure helpers
# ---------------------------------------------------------------------------
def danger_scores_3bars(m1_win: pd.DataFrame, direction: str) -> list[float]:
    if m1_win is None or m1_win.empty:
        return []
    df2 = m1_win.copy()
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
        scores.append(round(100.0 * abs(dom), 1) if against else 0.0)
    return scores

def compute_delta_4h(m1: pd.DataFrame, as_of) -> float:
    cutoff = as_of - pd.Timedelta(hours=4)
    w = m1[(m1.index >= cutoff) & (m1.index <= as_of)]
    return float(w["bar_delta"].sum()) if not w.empty else 0.0

def derive_m30_bias(m30: pd.DataFrame, as_of) -> str:
    import math
    sub = m30[m30.index <= as_of]
    if sub.empty:
        return "unknown"
    try:
        confirmed = sub[sub["m30_box_confirmed"] == True]
        row = confirmed.iloc[-1] if not confirmed.empty else sub.iloc[-1]
        liq_top = float(row.get("m30_liq_top", float("nan")))
        box_high = float(row.get("m30_box_high", float("nan")))
        liq_bot = float(row.get("m30_liq_bot", float("nan")))
        box_low = float(row.get("m30_box_low", float("nan")))
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
# Trade simulation (same position manager logic)
# ---------------------------------------------------------------------------
def simulate_trade(
    signal_ts, direction, entry_price, sl, tp1, tp2, atr,
    m30, m1_data, source_label,
) -> dict:
    sign = 1.0 if direction == "LONG" else -1.0
    state = {"shield_done": False, "danger_streak": 0, "regime_flip_streak": 0,
             "best_price": entry_price, "trailing_sl": None}

    max_ts = signal_ts + pd.Timedelta(hours=24)
    m1_future = m1_data[(m1_data.index >= signal_ts) & (m1_data.index <= max_ts)]

    if m1_future.empty:
        return _result(signal_ts, direction, entry_price, sl, tp1, tp2, atr,
                       "NO_DATA", entry_price, signal_ts, 0.0, state, [], source_label, m1_data)

    exit_reason = None
    exit_price = entry_price
    exit_ts = max_ts
    checks_log = []

    for bar_ts, bar in m1_future.iterrows():
        bar_mid = float(bar.get("mid_price", entry_price))
        elapsed_min = (bar_ts - signal_ts).total_seconds() / 60.0
        pnl_now = sign * (bar_mid - entry_price)

        if pnl_now > sign * (state["best_price"] - entry_price):
            state["best_price"] = bar_mid

        # SL
        if (direction == "LONG" and bar_mid <= sl) or (direction == "SHORT" and bar_mid >= sl):
            exit_reason = "SL_HIT"
            exit_price = sl
            exit_ts = bar_ts
            if state["shield_done"]:
                exit_reason = "BREAKEVEN_SL"
            break

        # SHIELD (TP1)
        if not state["shield_done"]:
            if (direction == "LONG" and bar_mid >= tp1) or (direction == "SHORT" and bar_mid <= tp1):
                state["shield_done"] = True
                sl = entry_price
                state["trailing_sl"] = entry_price
                checks_log.append({"ts": str(bar_ts), "check": "SHIELD", "elapsed_min": round(elapsed_min, 1)})

        # TP2
        if (direction == "LONG" and bar_mid >= tp2) or (direction == "SHORT" and bar_mid <= tp2):
            exit_reason = "TP2_HIT"
            exit_price = tp2
            exit_ts = bar_ts
            break

        # L2 Danger
        lookback = m1_data[(m1_data.index >= bar_ts - pd.Timedelta(minutes=90)) & (m1_data.index <= bar_ts)]
        scores = danger_scores_3bars(lookback, direction)
        if len(scores) >= DANGER_BARS and all(s >= DANGER_THRESHOLD for s in scores[-DANGER_BARS:]):
            exit_reason = "L2_DANGER"
            exit_price = bar_mid
            exit_ts = bar_ts
            checks_log.append({"ts": str(bar_ts), "check": "L2_DANGER", "scores": scores, "elapsed_min": round(elapsed_min, 1)})
            break

        # Regime Flip (pre-SHIELD only)
        if not state["shield_done"]:
            d4h = compute_delta_4h(m1_data, bar_ts)
            bias = derive_m30_bias(m30, bar_ts)
            flip = False
            if direction == "LONG" and d4h < DELTA_4H_LONG_BLOCK:
                flip = True
            elif direction == "SHORT" and d4h < TREND_RESUME_SHORT and bias == "bullish":
                flip = True
            if flip:
                state["regime_flip_streak"] += 1
            else:
                state["regime_flip_streak"] = 0
            min_bars_m1 = max(2, DELTA_FLIP_MIN_BARS // 30)
            if state["regime_flip_streak"] >= min_bars_m1:
                exit_reason = "REGIME_FLIP"
                exit_price = bar_mid
                exit_ts = bar_ts
                checks_log.append({"ts": str(bar_ts), "check": "REGIME_FLIP", "d4h": round(d4h, 0), "elapsed_min": round(elapsed_min, 1)})
                break

        # Cascade
        if elapsed_min <= CASCADE_WINDOW_MIN:
            move_against = -sign * (bar_mid - entry_price)
            if move_against > CASCADE_ATR_FACTOR * atr:
                exit_reason = "CASCADE"
                exit_price = bar_mid
                exit_ts = bar_ts
                checks_log.append({"ts": str(bar_ts), "check": "CASCADE", "elapsed_min": round(elapsed_min, 1)})
                break

        # Trailing (post-SHIELD)
        if state["shield_done"] and state["trailing_sl"] is not None:
            if direction == "LONG":
                state["trailing_sl"] = max(state["trailing_sl"], bar_mid - TRAILING_STOP_PTS)
                if bar_mid <= state["trailing_sl"]:
                    exit_reason = "TRAILING_STOP"
                    exit_price = state["trailing_sl"]
                    exit_ts = bar_ts
                    checks_log.append({"ts": str(bar_ts), "check": "TRAILING_STOP", "elapsed_min": round(elapsed_min, 1)})
                    break
            else:
                state["trailing_sl"] = min(state["trailing_sl"], bar_mid + TRAILING_STOP_PTS)
                if bar_mid >= state["trailing_sl"]:
                    exit_reason = "TRAILING_STOP"
                    exit_price = state["trailing_sl"]
                    exit_ts = bar_ts
                    checks_log.append({"ts": str(bar_ts), "check": "TRAILING_STOP", "elapsed_min": round(elapsed_min, 1)})
                    break

    if exit_reason is None:
        exit_reason = "TIMEOUT_24H"
        if not m1_future.empty:
            exit_price = float(m1_future.iloc[-1].get("mid_price", entry_price))
            exit_ts = m1_future.index[-1]

    pnl_pts = sign * (exit_price - entry_price)
    return _result(signal_ts, direction, entry_price, sl, tp1, tp2, atr,
                   exit_reason, exit_price, exit_ts, pnl_pts, state, checks_log, source_label, m1_data)


def _result(ts, direction, entry, sl, tp1, tp2, atr, exit_reason, exit_price, exit_ts, pnl,
            state, checks, source, m1_data):
    try:
        dur = (exit_ts - ts).total_seconds() / 60.0
    except Exception:
        dur = 0.0
    # Anomaly protection from M1 at entry
    m1_win = m1_data[(m1_data.index >= ts - pd.Timedelta(minutes=30)) & (m1_data.index <= ts)]
    anom = anomaly_from_micro(m1_win)
    ice = build_protection("ICEBERG")
    return {
        "signal_ts": str(ts), "direction": direction,
        "entry_price": round(entry, 2), "sl": round(sl, 2),
        "tp1": round(tp1, 2), "tp2": round(tp2, 2), "atr": round(atr, 2),
        "exit_reason": exit_reason, "exit_price": round(exit_price, 2),
        "exit_ts": str(exit_ts), "pnl_pts": round(pnl, 2),
        "duration_min": round(dur, 1),
        "shield_activated": state.get("shield_done", False),
        "checks_fired": [c["check"] for c in checks],
        "source": source,
        "protection": {"anomaly": anom, "iceberg": ice},
    }


# ---------------------------------------------------------------------------
# Signal Generation
# ---------------------------------------------------------------------------
def generate_level_proximity_signals(m30, m1, start, end) -> list[dict]:
    """Generate entry signals when M1 price touches M30 structural levels."""
    signals = []
    # Resample M1 to M30 for level detection
    m1_range = m1[(m1.index >= start) & (m1.index <= end)]
    if m1_range.empty:
        return signals

    # For each M30 bar, check if price came within ATR*0.3 of liq_top or liq_bot
    m30_range = m30[(m30.index >= start) & (m30.index <= end)]
    last_signal_ts = pd.Timestamp("2000-01-01", tz="UTC")

    for m30_ts, m30_bar in m30_range.iterrows():
        liq_top = m30_bar.get("m30_liq_top")
        liq_bot = m30_bar.get("m30_liq_bot")
        atr = m30_bar.get("atr14", 20.0)
        if pd.isna(liq_top) or pd.isna(liq_bot) or atr <= 0:
            continue

        band = max(atr * 0.3, 3.0)
        m1_window = m1_range[(m1_range.index >= m30_ts) & (m1_range.index < m30_ts + pd.Timedelta(minutes=30))]

        for m1_ts, m1_bar in m1_window.iterrows():
            mid = float(m1_bar.get("mid_price", 0))
            if mid <= 0:
                continue
            # Cooldown: at least 4h between signals
            if (m1_ts - last_signal_ts).total_seconds() < 14400:
                continue

            # Near liq_top -> SHORT
            if abs(mid - liq_top) <= band:
                sl_price = mid + atr
                tp1_price = mid - atr
                tp2_price = mid - 2 * atr
                signals.append({
                    "ts": m1_ts, "direction": "SHORT",
                    "entry": round(mid, 2), "sl": round(sl_price, 2),
                    "tp1": round(tp1_price, 2), "tp2": round(tp2_price, 2),
                    "atr": round(atr, 2), "source": "LEVEL_PROX",
                })
                last_signal_ts = m1_ts
                break  # one signal per M30 bar

            # Near liq_bot -> LONG
            if abs(mid - liq_bot) <= band:
                sl_price = mid - atr
                tp1_price = mid + atr
                tp2_price = mid + 2 * atr
                signals.append({
                    "ts": m1_ts, "direction": "LONG",
                    "entry": round(mid, 2), "sl": round(sl_price, 2),
                    "tp1": round(tp1_price, 2), "tp2": round(tp2_price, 2),
                    "atr": round(atr, 2), "source": "LEVEL_PROX",
                })
                last_signal_ts = m1_ts
                break

    return signals


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def stats(trades, label):
    n = len(trades)
    if n == 0:
        return {"label": label, "trades": 0}
    pnls = [t["pnl_pts"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw = sum(wins) if wins else 0
    gl = abs(sum(losses)) if losses else 0
    return {
        "label": label, "trades": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins)/n*100, 1),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(np.mean(pnls), 2),
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
        "profit_factor": round(gw/gl, 3) if gl > 0 else float("inf"),
        "shield_count": sum(1 for t in trades if t["shield_activated"]),
        "exit_reasons": {r: sum(1 for t in trades if t["exit_reason"]==r) for r in set(t["exit_reason"] for t in trades)},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    t0 = _time.monotonic()
    print("[LOAD] Loading datasets...")
    ats = pd.read_parquet(ATS_PATH)
    m30 = pd.read_parquet(M30_PATH)
    m1_databento = pd.read_parquet(M1_L2_PATH)

    for df in [m30, m1_databento]:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
    if ats.index.tz is None:
        ats.index = ats.index.tz_localize("UTC")

    # ── SOURCE 1: ATS signals Jul-Sep 2025 ──────────────────────────────
    print("\n[SOURCE 1] ATS features Jul-Sep 2025...")
    ats_signals = ats[
        (ats.index >= "2025-07-01") &
        ((ats["entry_long"] == True) | (ats["entry_short"] == True))
    ].copy()
    ats_signals["direction"] = "LONG"
    ats_signals.loc[ats_signals["entry_short"] == True, "direction"] = "SHORT"
    print(f"  {len(ats_signals)} signals")

    all_signals = []
    for ts_idx, row in ats_signals.iterrows():
        d = row["direction"]
        e = float(row["entry_price"])
        a = float(row["m30_atr"])
        if d == "LONG":
            sl = float(row["sl_long"]) if pd.notna(row.get("sl_long")) else e - a
            tp1 = float(row["tp1_long"]) if pd.notna(row.get("tp1_long")) else e + a
        else:
            sl = float(row["sl_short"]) if pd.notna(row.get("sl_short")) else e + a
            tp1 = float(row["tp1_short"]) if pd.notna(row.get("tp1_short")) else e - a
        tp2 = tp1 + (1 if d == "LONG" else -1) * a
        all_signals.append({"ts": ts_idx, "direction": d, "entry": e,
                            "sl": sl, "tp1": tp1, "tp2": tp2, "atr": a, "source": "ATS_V5"})

    # ── SOURCE 2: Level proximity Oct-Nov 2025 ──────────────────────────
    print("[SOURCE 2] Level proximity signals Oct-Nov 2025...")
    prox_signals = generate_level_proximity_signals(
        m30, m1_databento,
        pd.Timestamp("2025-10-01", tz="UTC"),
        pd.Timestamp("2025-11-24", tz="UTC"),
    )
    all_signals.extend(prox_signals)
    print(f"  {len(prox_signals)} signals")

    # ── SOURCE 3: Real trades Mar-Apr 2026 ──────────────────────────────
    print("[SOURCE 3] Real trades Mar-Apr 2026...")
    trades_df = pd.read_csv(TRADES_CSV)
    trades_df["ts"] = pd.to_datetime(trades_df["timestamp"])
    # Deduplicate: keep first signal per 4h window
    last_ts = pd.Timestamp("2000-01-01", tz="UTC")
    for _, row in trades_df.iterrows():
        ts = row["ts"]
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        if (ts - last_ts).total_seconds() < 14400:
            continue
        d = row["direction"]
        e = float(row["entry"])
        sl_val = float(row["sl"])
        tp1_val = float(row["tp1"])
        tp2_val = float(row["tp2"])
        atr_est = abs(sl_val - e)
        all_signals.append({"ts": ts, "direction": d, "entry": e,
                            "sl": sl_val, "tp1": tp1_val, "tp2": tp2_val,
                            "atr": round(atr_est, 2), "source": "LIVE_TRADE"})
        last_ts = ts
    live_count = sum(1 for s in all_signals if s["source"] == "LIVE_TRADE")
    print(f"  {live_count} signals (deduplicated to 4h windows)")

    # Sort all signals
    all_signals.sort(key=lambda x: x["ts"])
    print(f"\n[TOTAL] {len(all_signals)} signals across all sources")
    print(f"  Range: {all_signals[0]['ts']} -> {all_signals[-1]['ts']}")
    print("=" * 100)

    # ── Run simulations ─────────────────────────────────────────────────
    results = []
    for i, sig in enumerate(all_signals):
        ts = sig["ts"]
        date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, 'strftime') else str(ts)[:10]

        # Pick M1 data source
        if ts < pd.Timestamp("2025-11-25", tz="UTC"):
            m1_data = m1_databento
        else:
            m1_data = read_micro_for_date(date_str)
            if m1_data is None:
                # Try adjacent dates
                for offset in range(1, 3):
                    alt_date = (ts - pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
                    m1_data = read_micro_for_date(alt_date)
                    if m1_data is not None:
                        break

        if m1_data is None or m1_data.empty:
            results.append(_result(
                ts, sig["direction"], sig["entry"], sig["sl"], sig["tp1"], sig["tp2"], sig["atr"],
                "NO_MICRO_DATA", sig["entry"], ts, 0.0,
                {"shield_done": False}, [], sig["source"], pd.DataFrame(),
            ))
            continue

        r = simulate_trade(
            signal_ts=ts, direction=sig["direction"],
            entry_price=sig["entry"], sl=sig["sl"], tp1=sig["tp1"], tp2=sig["tp2"],
            atr=sig["atr"], m30=m30, m1_data=m1_data, source_label=sig["source"],
        )
        results.append(r)

        # Progress
        pnl = r["pnl_pts"]
        sev = r["protection"]["anomaly"]["severity"]
        shield = "SHIELD" if r["shield_activated"] else "      "
        print(
            f"  [{i+1:3d}/{len(all_signals)}] {str(ts)[:16]}  {sig['direction']:5s}  "
            f"entry={sig['entry']:.2f}  pnl={pnl:+7.2f}  {shield}  "
            f"exit={r['exit_reason']:15s}  anom={sev:8s}  src={sig['source']}"
        )

    elapsed = _time.monotonic() - t0
    print(f"\n[DONE] {len(results)} trades simulated in {elapsed:.1f}s")

    # Write JSONL
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")

    # ── COMPARISON: Baseline vs Filtered ────────────────────────────────
    valid = [r for r in results if r["exit_reason"] != "NO_MICRO_DATA" and r["exit_reason"] != "NO_DATA"]
    filtered = [r for r in valid if r["protection"]["anomaly"]["severity"] not in ("HIGH", "CRITICAL")]
    blocked = [r for r in valid if r["protection"]["anomaly"]["severity"] in ("HIGH", "CRITICAL")]

    sa = stats(valid, "BASELINE")
    sb = stats(filtered, "FILTERED (block anomaly >= HIGH)")
    sc = stats(blocked, "BLOCKED trades")

    print("\n" + "=" * 100)
    print(f"{'METRIC':<25s}  {'BASELINE':>15s}  {'FILTERED':>15s}  {'DELTA':>15s}")
    print("=" * 100)

    rows = [
        ("Trades",       sa["trades"],       sb["trades"]),
        ("Wins",         sa["wins"],          sb["wins"]),
        ("Losses",       sa["losses"],        sb["losses"]),
        ("Win Rate %",   sa["win_rate"],      sb["win_rate"]),
        ("Total PnL",    sa["total_pnl"],     sb["total_pnl"]),
        ("Avg PnL",      sa["avg_pnl"],       sb["avg_pnl"]),
        ("Avg Win",      sa["avg_win"],        sb["avg_win"]),
        ("Avg Loss",     sa["avg_loss"],       sb["avg_loss"]),
        ("Profit Factor", sa["profit_factor"], sb["profit_factor"]),
    ]
    for label, va, vb in rows:
        delta = vb - va
        if isinstance(va, float):
            print(f"  {label:<23s}  {va:>15.2f}  {vb:>15.2f}  {delta:>+15.2f}")
        else:
            print(f"  {label:<23s}  {va:>15d}  {vb:>15d}  {delta:>+15d}")

    print("=" * 100)

    # Exit reasons
    print(f"\n{'EXIT REASONS':<25s}  {'BASELINE':>10s}  {'FILTERED':>10s}  {'BLOCKED':>10s}")
    print("-" * 65)
    all_er = set()
    for s in [sa, sb, sc]:
        if "exit_reasons" in s:
            all_er.update(s["exit_reasons"].keys())
    for er in sorted(all_er):
        ca = sa.get("exit_reasons", {}).get(er, 0)
        cb = sb.get("exit_reasons", {}).get(er, 0)
        cc = sc.get("exit_reasons", {}).get(er, 0)
        print(f"  {er:<23s}  {ca:>10d}  {cb:>10d}  {cc:>10d}")

    # Source distribution
    print(f"\n{'SOURCE':<25s}  {'ALL':>10s}  {'FILTERED':>10s}  {'BLOCKED':>10s}")
    print("-" * 60)
    for src in ["ATS_V5", "LEVEL_PROX", "LIVE_TRADE"]:
        ca = sum(1 for r in valid if r["source"] == src)
        cb = sum(1 for r in filtered if r["source"] == src)
        cc = sum(1 for r in blocked if r["source"] == src)
        print(f"  {src:<23s}  {ca:>10d}  {cb:>10d}  {cc:>10d}")

    # Blocked detail
    print(f"\nBLOCKED: {len(blocked)} trades | PnL avoided: {sc.get('total_pnl', 0):+.2f} pts")
    print(f"SURVIVING: {len(filtered)} trades | PnL: {sb.get('total_pnl', 0):+.2f} pts")

    # Save
    comparison = {"baseline": sa, "filtered": sb, "blocked": sc,
                  "filter": "block entry when anomaly severity >= HIGH",
                  "improvement": {"pnl": round(sb["total_pnl"] - sa["total_pnl"], 2),
                                  "pf": round(sb["profit_factor"] - sa["profit_factor"], 3)}}
    with open(OUTPUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"\n[OUTPUT] {OUTPUT_JSONL}")
    print(f"[OUTPUT] {OUTPUT_SUMMARY}")


if __name__ == "__main__":
    main()

"""
W5-BACKTEST-E2E: 10mo L2 backtest END-TO-END (entry + position monitor exits).

Per Barbara directive 2026-05-09:
  - 10 meses dados L2
  - End-to-end: entry gates + position monitor exits
  - Initial capital $1000

Window: 2025-07-01 → 2026-04-07 (~9.2 mo, parquet end)

vs W5_backtest_10mo_L2.py:
  Adds Position Monitor exit logic during forward simulation:
  - SHIELD breakeven on TP1 hit (was there)
  - Trailing stop after SHIELD (NEW)
  - Regime flip exit (NEW): close if rolling_delta_4h flips against position
  - L2 danger exit (NEW): dom_imbalance threshold + bar_delta adverse
  - T3 Defense Exit (NEW): defense_tier proxy (extreme bar_delta) + 3pt adverse + M30 break
  - Giveback rule (NEW): MFE retracement > 50% after MFE > 0.5×ATR

Position monitor design = same priority order as live position_monitor.py:
  CHECK 1: SHIELD (breakeven on TP1)
  CHECK 2: L2 Danger
  CHECK 3: Regime flip
  CHECK 4: Cascade protection (skipped — needs price history graph)
  CHECK T3: Defense Exit (W5.2)
  CHECK 5: Trailing stop (post-SHIELD)
  CHECK W5.3: Giveback rule
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\FluxQuantumAI")
OUT_DIR = ROOT / "_audit" / "backtest" / "W5_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SOURCE = Path(r"C:/data/processed/calibration_dataset_full.parquet")
ICEBERG_DIR = Path(r"C:/data/iceberg")

WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END = pd.Timestamp("2026-04-07", tz="UTC")

INITIAL_CAPITAL = 1000.0
LOT_LEG1 = 0.02
LOT_LEG2 = 0.02
LOT_LEG3 = 0.01
DOLLAR_PER_PT_PER_LOT = 1.0 / 0.01
DOLLAR_LEG1 = LOT_LEG1 * DOLLAR_PER_PT_PER_LOT
DOLLAR_LEG2 = LOT_LEG2 * DOLLAR_PER_PT_PER_LOT
DOLLAR_LEG3 = LOT_LEG3 * DOLLAR_PER_PT_PER_LOT
DOLLAR_TOTAL = DOLLAR_LEG1 + DOLLAR_LEG2 + DOLLAR_LEG3

# W1-W5 gate parameters
SAME_LEVEL_PROX_PTS = 5.0
SAME_LEVEL_COOLDOWN_MIN = 60
ATR_EXTREME_PTS = 45.0
SESSION_CLOSE_BLOCK_MIN = 30
SESSION_CLOSE_HOUR_UTC = 22
DAILY_RANGE_BLOCK_PCT = 0.20
DAILY_LOSS_LIMIT = -50.0
RR_MIN = 1.0
MIN_SCORE_GO = 1
LEVEL_PROX_PTS = 5.0

# SL/TP design: ATS canonical
SL_BUFFER_ATR_MULT = 1.0     # SL = level ± 1.0×ATR (more tolerant — was 0.5 too tight)
SL_BUFFER_MIN_PTS = 8.0
SL_BUFFER_MAX_PTS = 30.0     # cap to avoid huge SL when ATR>30
TP1_FMV = True
TP2_OPPOSITE_LIQ = True

# Position monitor exit thresholds
TRAILING_PTS_AFTER_SHIELD = 12.0   # trail SL by 12pts behind price after SHIELD
REGIME_FLIP_DELTA_THR = 200        # rolling_delta_4h flip magnitude triggers regime exit
L2_DANGER_DOM_THR = 30.0           # |dom_imbalance| > 30 + opposite direction = danger
L2_DANGER_BAR_DELTA_ABS = 200      # |bar_delta| > 200 in adverse direction = danger
T3_ADVERSE_PTS = 3.0
T3_WINDOW_S = 60
T3_DEFENSE_BAR_DELTA_THR = 500     # |bar_delta| > 500 ≈ "defense mode" proxy
GIVEBACK_PCT = 0.50
GIVEBACK_MFE_MIN_ATR = 0.5

# Iceberg / score
W_BREAKING_ICE_ALIGNED = 1
W_BREAKING_ICE_CONTRA = -2
W_ICEBERG_ZONE_ALIGNED = 1
W_ICEBERG_ZONE_CONTRA = -1
ICEBERG_ZONE_PROX = 5.0
BREAKING_ICE_EXCEED = 2.2
BREAKING_ICE_LOOKBACK_MIN = 4
ICEBERG_ZONE_LOOKBACK_MIN = 30
ICEBERG_MIN_PROB = 0.50
ICEBERG_MIN_REFILLS = 3

GC_MT5_OFFSET = 31.0
MAX_TRADE_HORIZON_MIN = 240


# ---------------------- Data ----------------------

def load_data():
    print("Loading data...", flush=True)
    df = pd.read_parquet(SOURCE)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= WINDOW_START) & (df.index <= WINDOW_END)].copy()
    print(f"  M1 rows: {len(df):,} ({df.index[0]} -> {df.index[-1]})", flush=True)
    return df


_iceberg_cache: dict[str, list[dict]] = {}


def load_iceberg_day(date_str: str) -> list[dict]:
    if date_str in _iceberg_cache:
        return _iceberg_cache[date_str]
    path = ICEBERG_DIR / f"iceberg__GC_XCEC_{date_str}.jsonl"
    events = []
    if path.exists() and path.stat().st_size > 0:
        try:
            with path.open(encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    prob = float(rec.get("probability", 0))
                    refills = int(rec.get("refill_count", 0))
                    if prob < ICEBERG_MIN_PROB or refills < ICEBERG_MIN_REFILLS:
                        continue
                    try:
                        rec["_ts"] = pd.Timestamp(rec.get("timestamp", ""), tz="UTC")
                    except Exception:
                        continue
                    rec["_price"] = float(rec.get("price", 0))
                    rec["_side"] = str(rec.get("side", "")).lower()
                    events.append(rec)
        except Exception:
            pass
    _iceberg_cache[date_str] = events
    return events


def iceberg_score(ts, gc_price, direction, recent_high_low_gc):
    delta = 0
    parts = []
    events = load_iceberg_day(ts.strftime("%Y%m%d"))
    if not events:
        return 0, "no_iceberg_data"
    cutoff_bi = ts - pd.Timedelta(minutes=BREAKING_ICE_LOOKBACK_MIN)
    max_h, min_l = recent_high_low_gc
    bi_side = "none"
    for ev in events:
        if ev["_ts"] < cutoff_bi or ev["_ts"] > ts:
            continue
        if abs(ev["_price"] - gc_price) > 10.0:
            continue
        if ev["_side"] == "bid":
            if (ev["_price"] - min_l) >= BREAKING_ICE_EXCEED:
                bi_side = "bearish"; break
        elif ev["_side"] == "ask":
            if (max_h - ev["_price"]) >= BREAKING_ICE_EXCEED:
                bi_side = "bullish"; break
    if bi_side != "none":
        if (bi_side == "bullish" and direction == "LONG") or (bi_side == "bearish" and direction == "SHORT"):
            delta += W_BREAKING_ICE_ALIGNED; parts.append(f"BI_ALIGN+{W_BREAKING_ICE_ALIGNED}")
        else:
            delta += W_BREAKING_ICE_CONTRA; parts.append(f"BI_CONTRA{W_BREAKING_ICE_CONTRA:+d}")
    cutoff_iz = ts - pd.Timedelta(minutes=ICEBERG_ZONE_LOOKBACK_MIN)
    min_dist = float("inf"); nearest_side = "none"
    for ev in events:
        if ev["_ts"] < cutoff_iz or ev["_ts"] > ts:
            continue
        d = abs(ev["_price"] - gc_price)
        if d < min_dist:
            min_dist = d; nearest_side = ev["_side"]
    if min_dist <= ICEBERG_ZONE_PROX and nearest_side in ("bid", "ask"):
        if (nearest_side == "bid" and direction == "LONG") or (nearest_side == "ask" and direction == "SHORT"):
            delta += W_ICEBERG_ZONE_ALIGNED; parts.append(f"IZ_ALIGN+{W_ICEBERG_ZONE_ALIGNED}")
        else:
            delta += W_ICEBERG_ZONE_CONTRA; parts.append(f"IZ_CONTRA{W_ICEBERG_ZONE_CONTRA:+d}")
    return delta, " ".join(parts) if parts else "no_modifier"


def l2_momentum_score(row, direction):
    score = 0
    delta = float(row.get("rolling_delta_4h", 0) or 0)
    dom = float(row.get("l2_dom_imbalance", 0) or 0)
    pressure = float(row.get("l2_pressure_ratio", 1.0) or 1.0)
    if direction == "LONG":
        if delta > 50: score += 1
        if delta < -200: score -= 1
        if dom > 5: score += 1
        if pressure > 1.5: score += 1
    else:
        if delta < -50: score += 1
        if delta > 200: score -= 1
        if dom < -5: score += 1
        if pressure < 0.67: score += 1
    return score


_daily_range_cache: dict = {}


def get_daily_range(df, ts):
    date_key = ts.date()
    if date_key in _daily_range_cache:
        return _daily_range_cache[date_key]
    day_slice = df[df.index.date == date_key]
    if day_slice.empty:
        return (None, None)
    hi = float(day_slice["high"].max())
    lo = float(day_slice["low"].min())
    _daily_range_cache[date_key] = (hi, lo)
    return (hi, lo)


# ---------------------- Position Monitor exits ----------------------

@dataclass
class TradeResult:
    entry_ts: pd.Timestamp
    direction: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    atr_m30: float
    score: int
    score_detail: str
    exit_ts: pd.Timestamp = None
    exit_reason: str = ""
    exit_price: float = 0.0
    pnl_usd: float = 0.0
    duration_min: int = 0
    leg1_closed_at: float = None
    mfe_pts: float = 0.0
    mae_pts: float = 0.0


def simulate_e2e(df, df_full, entry_ts, direction, entry, sl_initial, tp1, tp2, atr, m30_liq_top, m30_liq_bot):
    """End-to-end simulation: entry -> all PM checks until exit.

    PM exits considered:
      - SL_HIT
      - TP1_THEN_BE (SHIELD) + trailing stop
      - TP1_THEN_TP2
      - TP1_THEN_TRAILING (post-SHIELD trail to runner)
      - REGIME_FLIP_EXIT
      - L2_DANGER_EXIT
      - T3_DEFENSE_EXIT
      - GIVEBACK_EXIT
      - TIMEOUT
    """
    end_ts = entry_ts + pd.Timedelta(minutes=MAX_TRADE_HORIZON_MIN)
    fwd = df[(df.index > entry_ts) & (df.index <= end_ts)]
    if fwd.empty:
        return ("NO_DATA", entry_ts, entry, 0.0, 0.0, 0.0, None)

    # State
    sl = sl_initial
    shield_active = False     # set after TP1 hit
    leg1_closed_at = None
    tp1_pnl = 0.0
    mfe_pts = 0.0
    mae_pts = 0.0
    giveback_logged = False
    sign = 1 if direction == "LONG" else -1

    # T3 price history (for adverse move check)
    price_hist = []  # list of (ts_sec, price)

    # Initial delta_4h to detect flip
    initial_delta = float(df_full.loc[entry_ts]["rolling_delta_4h"]) if entry_ts in df_full.index else 0.0
    initial_delta_sign = 1 if initial_delta > 0 else (-1 if initial_delta < 0 else 0)

    for ts, bar in fwd.iterrows():
        hi = float(bar["high"]); lo = float(bar["low"]); close = float(bar["close"])
        # Track MFE/MAE
        if direction == "LONG":
            cur_mfe = hi - entry
            cur_mae = entry - lo
        else:
            cur_mfe = entry - lo
            cur_mae = hi - entry
        if cur_mfe > mfe_pts:
            mfe_pts = cur_mfe
        if cur_mae > mae_pts:
            mae_pts = cur_mae

        # Track price hist for T3 (60s window)
        ts_s = ts.value / 1e9
        price_hist.append((ts_s, close))
        # prune
        cutoff_s = ts_s - T3_WINDOW_S
        price_hist = [(t, p) for t, p in price_hist if t >= cutoff_s]

        # ---- CHECK 1: SL or TP1 (priority — if same bar, SL wins for safety) ----
        if not shield_active:
            if direction == "LONG":
                if lo <= sl:
                    pnl = -(entry - sl) * DOLLAR_TOTAL
                    return ("SL_HIT", ts, sl, pnl, mfe_pts, mae_pts, None)
                if hi >= tp1:
                    # TP1 hit → SHIELD: leg1 close at TP1, sl→entry
                    tp1_pnl = (tp1 - entry) * DOLLAR_LEG1
                    leg1_closed_at = tp1
                    sl = entry  # SHIELD
                    shield_active = True
                    # continue checking same bar for TP2/danger
            else:
                if hi >= sl:
                    pnl = -(sl - entry) * DOLLAR_TOTAL
                    return ("SL_HIT", ts, sl, pnl, mfe_pts, mae_pts, None)
                if lo <= tp1:
                    tp1_pnl = (entry - tp1) * DOLLAR_LEG1
                    leg1_closed_at = tp1
                    sl = entry
                    shield_active = True

        # ---- After SHIELD ----
        if shield_active:
            # SL (now at BE) hit
            if direction == "LONG":
                if lo <= sl:
                    rest_pnl = (sl - entry) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                    return ("TP1_THEN_BE", ts, sl, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
                if hi >= tp2:
                    rest_pnl = (tp2 - entry) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                    return ("TP1_THEN_TP2", ts, tp2, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
            else:
                if hi >= sl:
                    rest_pnl = (entry - sl) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                    return ("TP1_THEN_BE", ts, sl, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
                if lo <= tp2:
                    rest_pnl = (entry - tp2) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                    return ("TP1_THEN_TP2", ts, tp2, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
            # Trailing stop: trail by TRAILING_PTS_AFTER_SHIELD
            if direction == "LONG":
                trail_to = close - TRAILING_PTS_AFTER_SHIELD
                if trail_to > sl:
                    sl = trail_to
            else:
                trail_to = close + TRAILING_PTS_AFTER_SHIELD
                if trail_to < sl:
                    sl = trail_to

        # ---- Regime flip exit ----
        cur_delta = float(bar.get("rolling_delta_4h", 0) or 0)
        cur_delta_sign = 1 if cur_delta > 0 else (-1 if cur_delta < 0 else 0)
        if abs(cur_delta) > REGIME_FLIP_DELTA_THR and initial_delta_sign != 0 and cur_delta_sign != 0:
            if cur_delta_sign != initial_delta_sign:
                # delta flipped from initial
                # Close: if shield → leg2/leg3 at close; if not → all at close
                if shield_active:
                    rest_pnl = (close - entry) * sign * (DOLLAR_LEG2 + DOLLAR_LEG3)
                    return ("REGIME_FLIP_AFTER_SHIELD", ts, close, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
                else:
                    pnl = (close - entry) * sign * DOLLAR_TOTAL
                    return ("REGIME_FLIP_PRE_SHIELD", ts, close, pnl, mfe_pts, mae_pts, None)

        # ---- L2 Danger exit ----
        cur_dom = float(bar.get("l2_dom_imbalance", 0) or 0)
        cur_bar_delta = float(bar.get("l2_bar_delta", 0) or 0)
        l2_danger = False
        if direction == "LONG" and (cur_dom < -L2_DANGER_DOM_THR or cur_bar_delta < -L2_DANGER_BAR_DELTA_ABS):
            l2_danger = True
        elif direction == "SHORT" and (cur_dom > L2_DANGER_DOM_THR or cur_bar_delta > L2_DANGER_BAR_DELTA_ABS):
            l2_danger = True
        if l2_danger:
            if shield_active:
                rest_pnl = (close - entry) * sign * (DOLLAR_LEG2 + DOLLAR_LEG3)
                return ("L2_DANGER_AFTER_SHIELD", ts, close, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
            else:
                pnl = (close - entry) * sign * DOLLAR_TOTAL
                return ("L2_DANGER_PRE_SHIELD", ts, close, pnl, mfe_pts, mae_pts, None)

        # ---- T3 Defense Exit (W5.2) ----
        defense_active = abs(cur_bar_delta) > T3_DEFENSE_BAR_DELTA_THR
        if defense_active and len(price_hist) >= 2:
            # adverse move in last 60s
            if direction == "LONG":
                peak = max(p for _, p in price_hist)
                adverse = peak - close
            else:
                trough = min(p for _, p in price_hist)
                adverse = close - trough
            level_broken = (
                (direction == "LONG" and m30_liq_bot is not None and close < m30_liq_bot) or
                (direction == "SHORT" and m30_liq_top is not None and close > m30_liq_top)
            )
            if adverse >= T3_ADVERSE_PTS and level_broken:
                if shield_active:
                    rest_pnl = (close - entry) * sign * (DOLLAR_LEG2 + DOLLAR_LEG3)
                    return ("T3_DEFENSE_AFTER_SHIELD", ts, close, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
                else:
                    pnl = (close - entry) * sign * DOLLAR_TOTAL
                    return ("T3_DEFENSE_PRE_SHIELD", ts, close, pnl, mfe_pts, mae_pts, None)

        # ---- W5.3 Giveback rule ----
        if not giveback_logged and mfe_pts > atr * GIVEBACK_MFE_MIN_ATR:
            cur_pnl_pts = (close - entry) * sign
            if cur_pnl_pts < mfe_pts * (1.0 - GIVEBACK_PCT):
                # giveback — for backtest, treat as alert + CLOSE (simulating armed)
                if shield_active:
                    rest_pnl = (close - entry) * sign * (DOLLAR_LEG2 + DOLLAR_LEG3)
                    return ("GIVEBACK_AFTER_SHIELD", ts, close, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
                else:
                    pnl = (close - entry) * sign * DOLLAR_TOTAL
                    return ("GIVEBACK_PRE_SHIELD", ts, close, pnl, mfe_pts, mae_pts, None)
                # giveback_logged = True (unreachable since we return)

    # Timeout
    last_close = float(fwd["close"].iloc[-1])
    last_ts = fwd.index[-1]
    if shield_active:
        rest_pnl = (last_close - entry) * sign * (DOLLAR_LEG2 + DOLLAR_LEG3)
        return ("TIMEOUT_AFTER_SHIELD", last_ts, last_close, tp1_pnl + rest_pnl, mfe_pts, mae_pts, leg1_closed_at)
    else:
        pnl = (last_close - entry) * sign * DOLLAR_TOTAL
        return ("TIMEOUT_PRE_SHIELD", last_ts, last_close, pnl, mfe_pts, mae_pts, None)


# ---------------------- Main loop ----------------------

def run_backtest():
    df = load_data()
    daily_pnl = defaultdict(float)
    last_trade_at_level: dict = {}
    last_trade_ts = None
    active_until = None

    trades: list[TradeResult] = []
    blocked_reasons: dict = defaultdict(int)
    n_candidates = 0

    cand_mask = (
        df["m30_box_confirmed"].fillna(False).astype(bool) &
        df["at_struct_level"].fillna(False).astype(bool) &
        df["atr_m30"].notna() &
        df["m30_liq_top"].notna() &
        df["m30_liq_bot"].notna()
    )
    candidates = df[cand_mask]
    print(f"Candidate bars: {len(candidates):,}", flush=True)

    last_print = pd.Timestamp.now()
    for i, (ts, row) in enumerate(candidates.iterrows()):
        if i % 5000 == 0:
            now = pd.Timestamp.now()
            if (now - last_print).total_seconds() > 5:
                print(f"  progress: {i:,}/{len(candidates):,} trades={len(trades)}", flush=True)
                last_print = now

        if active_until is not None and ts < active_until:
            continue

        liq_top_mt5 = float(row["m30_liq_top"]) - GC_MT5_OFFSET
        liq_bot_mt5 = float(row["m30_liq_bot"]) - GC_MT5_OFFSET
        price = float(row["close"])
        atr = float(row["atr_m30"])

        direction = None; level_type = None; level_price = None
        if abs(price - liq_top_mt5) <= LEVEL_PROX_PTS:
            direction, level_type, level_price = "SHORT", "liq_top", liq_top_mt5
        elif abs(price - liq_bot_mt5) <= LEVEL_PROX_PTS:
            direction, level_type, level_price = "LONG", "liq_bot", liq_bot_mt5
        if direction is None:
            continue
        n_candidates += 1

        # Cooldowns
        ckey = (level_type, round(level_price / SAME_LEVEL_PROX_PTS) * SAME_LEVEL_PROX_PTS)
        last_at = last_trade_at_level.get(ckey)
        if last_at is not None and (ts - last_at).total_seconds() / 60 < SAME_LEVEL_COOLDOWN_MIN:
            blocked_reasons["same_level_cooldown"] += 1; continue
        if last_trade_ts is not None and (ts - last_trade_ts).total_seconds() / 60 < 30:
            blocked_reasons["global_cooldown"] += 1; continue

        # ATR extreme
        if atr > ATR_EXTREME_PTS:
            blocked_reasons["atr_extreme"] += 1; continue

        # Session close
        mins_to_close = (SESSION_CLOSE_HOUR_UTC * 60) - (ts.hour * 60 + ts.minute)
        if 0 < mins_to_close <= SESSION_CLOSE_BLOCK_MIN:
            blocked_reasons["session_close"] += 1; continue

        # Daily range
        hi, lo = get_daily_range(df, ts)
        if hi is not None and lo is not None and hi > lo:
            rng = hi - lo
            if direction == "LONG" and price >= hi - rng * DAILY_RANGE_BLOCK_PCT:
                blocked_reasons["daily_range_long_upper"] += 1; continue
            if direction == "SHORT" and price <= lo + rng * DAILY_RANGE_BLOCK_PCT:
                blocked_reasons["daily_range_short_lower"] += 1; continue

        # Daily loss
        date_key = ts.date()
        if daily_pnl[date_key] < DAILY_LOSS_LIMIT:
            blocked_reasons["daily_loss_limit"] += 1; continue

        # SL/TP per ATS canonical
        m30_fmv_mt5 = float(row["m30_fmv"]) - GC_MT5_OFFSET if pd.notna(row.get("m30_fmv")) else None
        sl_buffer = max(SL_BUFFER_MIN_PTS, min(SL_BUFFER_MAX_PTS, atr * SL_BUFFER_ATR_MULT))
        if direction == "LONG":
            sl = level_price - sl_buffer
            tp1 = m30_fmv_mt5 if (TP1_FMV and m30_fmv_mt5 is not None and m30_fmv_mt5 > price + 2) else price + max(atr * 0.8, 5.0)
            tp2 = liq_top_mt5 if (TP2_OPPOSITE_LIQ and liq_top_mt5 > tp1) else tp1 + atr * 0.7
        else:
            sl = level_price + sl_buffer
            tp1 = m30_fmv_mt5 if (TP1_FMV and m30_fmv_mt5 is not None and m30_fmv_mt5 < price - 2) else price - max(atr * 0.8, 5.0)
            tp2 = liq_bot_mt5 if (TP2_OPPOSITE_LIQ and liq_bot_mt5 < tp1) else tp1 - atr * 0.7

        # Sanity ordering
        if direction == "LONG" and not (sl < price < tp1 < tp2):
            blocked_reasons["invalid_sl_tp_order"] += 1; continue
        if direction == "SHORT" and not (tp2 < tp1 < price < sl):
            blocked_reasons["invalid_sl_tp_order"] += 1; continue

        # RR check
        sl_dist = abs(price - sl); tp1_dist = abs(tp1 - price)
        rr = tp1_dist / sl_dist if sl_dist > 0 else 0
        if rr < RR_MIN:
            blocked_reasons["rr_below_min"] += 1; continue

        # Score (mom + iceberg)
        mom_score = l2_momentum_score(row, direction)
        bi_window = df[(df.index > ts - pd.Timedelta(minutes=BREAKING_ICE_LOOKBACK_MIN)) & (df.index <= ts)]
        if not bi_window.empty:
            max_h_gc = float(bi_window["high"].max()) + GC_MT5_OFFSET
            min_l_gc = float(bi_window["low"].min()) + GC_MT5_OFFSET
        else:
            max_h_gc = price + GC_MT5_OFFSET
            min_l_gc = price + GC_MT5_OFFSET
        ice_s, ice_d = iceberg_score(ts, price + GC_MT5_OFFSET, direction, (max_h_gc, min_l_gc))
        total_score = mom_score + ice_s
        if total_score < MIN_SCORE_GO:
            blocked_reasons["min_score_go"] += 1; continue

        # ENTRY → e2e simulation with PM exits
        result = simulate_e2e(df, df, ts, direction, price, sl, tp1, tp2, atr, liq_top_mt5, liq_bot_mt5)
        reason, exit_ts, exit_price, pnl_usd, mfe, mae, leg1_at = result

        duration = int((exit_ts - ts).total_seconds() / 60) if exit_ts > ts else 0
        tr = TradeResult(
            entry_ts=ts, direction=direction, entry_price=price,
            sl=sl, tp1=tp1, tp2=tp2, atr_m30=atr,
            score=total_score, score_detail=f"mom{mom_score:+d} {ice_d}",
            exit_ts=exit_ts, exit_reason=reason, exit_price=exit_price,
            pnl_usd=pnl_usd, duration_min=duration,
            leg1_closed_at=leg1_at, mfe_pts=mfe, mae_pts=mae,
        )
        trades.append(tr)
        daily_pnl[date_key] += pnl_usd
        last_trade_at_level[ckey] = ts
        last_trade_ts = ts
        active_until = exit_ts

    print(f"\nDone. Candidates: {n_candidates:,} | Trades: {len(trades):,}", flush=True)
    return trades, blocked_reasons


def aggregate_metrics(trades):
    if not trades:
        return {"n": 0, "wr": 0, "pf": 0, "pnl": 0, "final_capital": INITIAL_CAPITAL}
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    flats = [t for t in trades if t.pnl_usd == 0]
    gross_win = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    pnl = sum(t.pnl_usd for t in trades)
    wr = len(wins) / len(trades)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0
    final_cap = INITIAL_CAPITAL + pnl
    capital_curve = [INITIAL_CAPITAL]
    for t in trades:
        capital_curve.append(capital_curve[-1] + t.pnl_usd)
    peak = INITIAL_CAPITAL; max_dd = 0
    for c in capital_curve:
        if c > peak: peak = c
        if peak - c > max_dd: max_dd = peak - c

    # Exit reason distribution
    exit_dist = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for t in trades:
        d = exit_dist[t.exit_reason]
        d["n"] += 1
        d["pnl"] += t.pnl_usd
        if t.pnl_usd > 0:
            d["wins"] += 1
        elif t.pnl_usd < 0:
            d["losses"] += 1

    return {
        "n_trades": len(trades), "n_wins": len(wins), "n_losses": len(losses), "n_flats": len(flats),
        "win_rate": wr, "gross_win_usd": gross_win, "gross_loss_usd": gross_loss,
        "profit_factor": pf, "pnl_usd": pnl,
        "initial_capital": INITIAL_CAPITAL, "final_capital": final_cap,
        "return_pct": (final_cap / INITIAL_CAPITAL - 1) * 100,
        "max_drawdown_usd": max_dd, "max_drawdown_pct": (max_dd / peak * 100) if peak > 0 else 0,
        "avg_win_usd": gross_win / len(wins) if wins else 0,
        "avg_loss_usd": -gross_loss / len(losses) if losses else 0,
        "avg_duration_min": sum(t.duration_min for t in trades) / len(trades),
        "exit_reason_dist": {k: dict(v) for k, v in exit_dist.items()},
    }


def write_outputs(trades, blocks, metrics):
    if trades:
        rows = []
        cap = INITIAL_CAPITAL
        for t in trades:
            cap += t.pnl_usd
            d = asdict(t)
            d["capital_after"] = cap
            rows.append(d)
        pd.DataFrame(rows).to_csv(OUT_DIR / "trades.csv", index=False)
        print(f"  wrote {OUT_DIR/'trades.csv'}", flush=True)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "window": [str(WINDOW_START), str(WINDOW_END)],
        "metrics": metrics, "blocks": dict(blocks),
        "config": {
            "initial_capital_usd": INITIAL_CAPITAL,
            "lots": [LOT_LEG1, LOT_LEG2, LOT_LEG3],
            "dollar_per_pt_total": DOLLAR_TOTAL,
            "sl_buffer_atr_mult": SL_BUFFER_ATR_MULT,
            "sl_buffer_min_pts": SL_BUFFER_MIN_PTS,
            "trailing_pts_after_shield": TRAILING_PTS_AFTER_SHIELD,
            "regime_flip_delta_thr": REGIME_FLIP_DELTA_THR,
            "l2_danger_dom_thr": L2_DANGER_DOM_THR,
            "t3_defense_bar_delta_thr": T3_DEFENSE_BAR_DELTA_THR,
            "giveback_pct": GIVEBACK_PCT,
        },
    }
    (OUT_DIR / "metrics.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"  wrote {OUT_DIR/'metrics.json'}", flush=True)

    md = []
    md.append("# W5 E2E Backtest 10mo L2 — Entry + Position Monitor exits\n")
    md.append(f"**Generated**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    md.append(f"**Window**: {WINDOW_START} → {WINDOW_END} (~9.2 mo, parquet end)\n")
    md.append(f"**Initial capital**: ${INITIAL_CAPITAL:.2f}\n")
    md.append("\n## Headline metrics\n\n")
    md.append("| Metric | Value |\n|---|---:|\n")
    md.append(f"| Trades | {metrics['n_trades']:,} |\n")
    md.append(f"| Wins | {metrics['n_wins']:,} |\n")
    md.append(f"| Losses | {metrics['n_losses']:,} |\n")
    md.append(f"| **Win rate** | **{metrics['win_rate']:.2%}** |\n")
    md.append(f"| Gross win | ${metrics['gross_win_usd']:,.2f} |\n")
    md.append(f"| Gross loss | ${metrics['gross_loss_usd']:,.2f} |\n")
    md.append(f"| **Profit factor** | **{metrics['profit_factor']:.2f}** |\n")
    md.append(f"| **PnL** | **${metrics['pnl_usd']:+,.2f}** |\n")
    md.append(f"| Final capital | ${metrics['final_capital']:,.2f} |\n")
    md.append(f"| Return | {metrics['return_pct']:+.2f}% |\n")
    md.append(f"| Max DD | ${metrics['max_drawdown_usd']:,.2f} ({metrics['max_drawdown_pct']:.2f}%) |\n")
    md.append(f"| Avg duration | {metrics['avg_duration_min']:.1f} min |\n")
    md.append("\n## Exit reason distribution\n\n")
    md.append("| Reason | n | wins | losses | total PnL$ |\n|---|---:|---:|---:|---:|\n")
    for k, v in sorted(metrics['exit_reason_dist'].items(), key=lambda x: -x[1]["n"]):
        md.append(f"| {k} | {v['n']} | {v['wins']} | {v['losses']} | ${v['pnl']:+,.2f} |\n")
    md.append("\n## Block breakdown\n\n")
    md.append("| Reason | Count |\n|---|---:|\n")
    for k, v in sorted(blocks.items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v:,} |\n")
    md.append("\n## Methodology\n")
    md.append("- Entry: M30 confirmed box at liq_top (SHORT) / liq_bot (LONG), prox ≤ 5pts\n")
    md.append("- Gates: same_level cooldown 60min, global 30min, ATR extreme >45, session close 30min,\n")
    md.append("  daily range 20% extremes, daily loss -$50, RR≥1, MIN_SCORE_GO≥1\n")
    md.append("- Score: L2 mom (delta_4h/dom/pressure) + iceberg modifiers\n")
    md.append("- SL: level ± clip(atr×1.0, 8, 30); TP1=FMV; TP2=opposite liq line\n")
    md.append("- **Position Monitor exits (END-TO-END)**:\n")
    md.append("  - SL_HIT (initial)\n")
    md.append("  - TP1_THEN_BE/TP2 (SHIELD on TP1)\n")
    md.append("  - REGIME_FLIP (rolling_delta_4h flip > 200)\n")
    md.append("  - L2_DANGER (dom_imbalance / bar_delta extreme contra)\n")
    md.append("  - T3_DEFENSE (bar_delta > 500 + 3pt adverse + M30 break)\n")
    md.append("  - GIVEBACK (MFE retraced > 50% after MFE > 0.5×ATR)\n")
    md.append("  - Trailing stop after SHIELD (12pt behind close)\n")
    md.append("  - TIMEOUT (4h)\n")

    (OUT_DIR / "REPORT.md").write_text("".join(md), encoding="utf-8")
    print(f"  wrote {OUT_DIR/'REPORT.md'}", flush=True)


if __name__ == "__main__":
    print("=" * 70, flush=True)
    print("W5 E2E Backtest — entry + position monitor exits", flush=True)
    print("=" * 70, flush=True)
    trades, blocks = run_backtest()
    metrics = aggregate_metrics(trades)
    print("\n=== METRICS ===", flush=True)
    for k, v in metrics.items():
        if k == "exit_reason_dist":
            print(f"  {k}:")
            for rk, rv in v.items():
                print(f"    {rk}: {rv}")
        else:
            print(f"  {k}: {v}")
    write_outputs(trades, blocks, metrics)
    print("\nDone.", flush=True)

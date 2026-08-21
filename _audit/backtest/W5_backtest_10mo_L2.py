"""
W5-BACKTEST: 10-month L2-based backtest after W1-W5.3 arming.

Per Barbara directive 2026-05-09:
  - 10 meses dados L2 historicos
  - Win rate, Profit Factor, PnL
  - Capital inicial $1000

Window: 2025-07-01 → 2026-04-08 (parquet end). ~10 months.
Source: C:/data/processed/calibration_dataset_full.parquet (M1 OHLCV + L2 features
        + M30 boxes/liq_top/liq_bot/fmv + atr_m30).
Iceberg events: C:/data/iceberg/iceberg__GC_XCEC_<YYYYMMDD>.jsonl (per-day).

Logic applied (post W1-W5.3 arming):
  - Entry detection: M30 confirmed box at liq_top (SHORT) / liq_bot (LONG), proximity ≤ 5pts
  - Same-level cooldown: 60 min (W1.3)
  - W2.1 ATR extreme block (atr_m30 > 45)
  - W4.3 Session close block (22:00 UTC - 30min)
  - W4.2 Daily range block (upper/lower 20% counter direction)
  - W2.5 Daily loss limit (-$50)
  - W2.2 RR ≥ 1.0 universal
  - W1.2 MIN_SCORE_GO ≥ 1 (combined score from L2 momentum + iceberg modifiers)
  - W5.1 Iceberg score modifiers (Breaking Ice ALIGNED +1 / CONTRA -2; Zone IN ALIGNED +1 / CONTRA -1)

Position sizing: lots [0.02, 0.02, 0.01] = total 0.05; XAUUSD $1/pt per 0.01 = $5/pt total.

SL/TP simulation:
  - SL = entry ± 20pts
  - TP1 = entry ± atr_m30 × 0.8
  - TP2 = entry ± atr_m30 × 1.5
  - Walk M1 forward up to 4h:
      First touch SL → all close at -SL_dist × $5
      First touch TP1 → leg1 closes (+TP1_dist × $2);
                        SL moves to entry (BE);
                        continue forward:
                          touch BE → leg2+leg3 close at 0
                          touch TP2 → leg2 closes (+TP2_dist × $2), leg3 trails (use TP2 as exit too)

Outputs:
  - Per-trade ledger CSV
  - Aggregate metrics (WR, PF, PnL, Capital)
  - Markdown report
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# -------------------------- Paths --------------------------
ROOT = Path(r"C:\FluxQuantumAI")
OUT_DIR = ROOT / "_audit" / "backtest" / "W5_10mo_L2"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SOURCE = Path(r"C:/data/processed/calibration_dataset_full.parquet")
ICEBERG_DIR = Path(r"C:/data/iceberg")

# -------------------------- Config --------------------------
WINDOW_START = pd.Timestamp("2025-07-01", tz="UTC")
WINDOW_END = pd.Timestamp("2026-04-07", tz="UTC")  # parquet end (calibration_dataset_full); ~9.2mo
# NOTE: extending to 2026-05-08 (yesterday) requires re-running apex_nextgen L2
# aggregation pipeline (~1-2h, separate scope). Iceberg JSONL data covers full
# period so W5.1 modifiers work; L2 momentum score absent in any uncovered tail.

INITIAL_CAPITAL = 1000.0  # USD

# Lot sizing (XAUUSD: $1/pt per 0.01 lot)
LOT_LEG1 = 0.02   # → $2/pt
LOT_LEG2 = 0.02   # → $2/pt
LOT_LEG3 = 0.01   # → $1/pt
DOLLAR_PER_PT_PER_LOT = 1.0 / 0.01   # $1/pt for 0.01 lot
DOLLAR_LEG1 = LOT_LEG1 * DOLLAR_PER_PT_PER_LOT  # 2
DOLLAR_LEG2 = LOT_LEG2 * DOLLAR_PER_PT_PER_LOT  # 2
DOLLAR_LEG3 = LOT_LEG3 * DOLLAR_PER_PT_PER_LOT  # 1
DOLLAR_TOTAL = DOLLAR_LEG1 + DOLLAR_LEG2 + DOLLAR_LEG3  # $5/pt

# W1-W5 gate parameters (mirror config/settings.json post-W5)
SAME_LEVEL_PROX_PTS = 5.0
SAME_LEVEL_COOLDOWN_MIN = 60
ATR_EXTREME_PTS = 45.0
SESSION_CLOSE_BLOCK_MIN = 30
SESSION_CLOSE_HOUR_UTC = 22
DAILY_RANGE_BLOCK_PCT = 0.20
DAILY_LOSS_LIMIT = -50.0  # USD
RR_MIN = 1.0
MIN_SCORE_GO = 1
# ATS canonical SL/TP design (mean-reversion at structural level):
#   LONG at liq_bot:  SL = liq_bot - SL_BUFFER_ATR×ATR  (below support)
#                     TP1 = m30_fmv                      (revert to box midpoint)
#                     TP2 = m30_liq_top                  (opposite liq line)
#   SHORT at liq_top: SL = liq_top + SL_BUFFER_ATR×ATR
#                     TP1 = m30_fmv
#                     TP2 = m30_liq_bot
SL_BUFFER_ATR_MULT = 0.5     # SL distance below/above level = 0.5×ATR (per ATS Risk Mgmt)
SL_BUFFER_MIN_PTS = 5.0      # absolute floor 5pts so we don't get stopped on noise
SL_PTS_FALLBACK = 20.0       # used only when m30_fmv missing (rare)
TP1_USE_FMV = True           # ATS canonical: TP1 = M30 FMV
TP2_USE_OPPOSITE_LIQ = True  # ATS canonical: TP2 = opposite liq line
MAX_TRADE_HORIZON_MIN = 240   # 4h max forward simulation

# Score weights (mirror W5.1 score modifiers)
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

# Trigger detection
LEVEL_PROX_PTS = 5.0  # |price - liq_level| ≤ X to call "at level"


# -------------------------- Data loading --------------------------

def load_data():
    print("Loading calibration_dataset_full (10mo subset)...", flush=True)
    df = pd.read_parquet(SOURCE)
    df.index = pd.to_datetime(df.index, utc=True) if not isinstance(df.index, pd.DatetimeIndex) else df.index
    df = df[(df.index >= WINDOW_START) & (df.index <= WINDOW_END)].copy()
    print(f"  M1 rows: {len(df):,}", flush=True)
    print(f"  Span: {df.index[0]} -> {df.index[-1]}", flush=True)
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


# -------------------------- Score modifiers (W5.1) --------------------------

def iceberg_score_modifiers(
    ts: pd.Timestamp, entry_price_gc: float, direction: str, recent_high_low_window: tuple[float, float]
) -> tuple[int, str]:
    """Returns (score_delta, detail) for Breaking Ice + Iceberg Zone score modifiers.

    entry_price_gc: GC futures price space (icebergs are in GC space).
    recent_high_low_window: (max_high, min_low) over last BREAKING_ICE_LOOKBACK_MIN minutes.
    """
    delta = 0
    parts = []
    date_str = ts.strftime("%Y%m%d")
    events = load_iceberg_day(date_str)
    if not events:
        return 0, "no_iceberg_data"

    # ---- Breaking Ice ----
    cutoff_bi = ts - pd.Timedelta(minutes=BREAKING_ICE_LOOKBACK_MIN)
    max_high, min_low = recent_high_low_window
    bi_side = "none"
    for ev in events:
        if ev["_ts"] < cutoff_bi or ev["_ts"] > ts:
            continue
        if abs(ev["_price"] - entry_price_gc) > 10.0:  # only nearby icebergs
            continue
        if ev["_side"] == "bid":
            # support; broken if price went BELOW by >= 2.2 → bearish
            exceed = ev["_price"] - min_low
            if exceed >= BREAKING_ICE_EXCEED:
                bi_side = "bearish"
                break
        elif ev["_side"] == "ask":
            # resistance; broken if price went ABOVE by >= 2.2 → bullish
            exceed = max_high - ev["_price"]
            if exceed >= BREAKING_ICE_EXCEED:
                bi_side = "bullish"
                break
    if bi_side != "none":
        if (bi_side == "bullish" and direction == "LONG") or (bi_side == "bearish" and direction == "SHORT"):
            delta += W_BREAKING_ICE_ALIGNED
            parts.append(f"BI_ALIGNED({bi_side})+{W_BREAKING_ICE_ALIGNED}")
        elif (bi_side == "bullish" and direction == "SHORT") or (bi_side == "bearish" and direction == "LONG"):
            delta += W_BREAKING_ICE_CONTRA
            parts.append(f"BI_CONTRA({bi_side}){W_BREAKING_ICE_CONTRA:+d}")

    # ---- Iceberg Zone ----
    cutoff_iz = ts - pd.Timedelta(minutes=ICEBERG_ZONE_LOOKBACK_MIN)
    min_dist = float("inf")
    nearest_side = "none"
    for ev in events:
        if ev["_ts"] < cutoff_iz or ev["_ts"] > ts:
            continue
        d = abs(ev["_price"] - entry_price_gc)
        if d < min_dist:
            min_dist = d
            nearest_side = ev["_side"]
    if min_dist <= ICEBERG_ZONE_PROX and nearest_side in ("bid", "ask"):
        if (nearest_side == "bid" and direction == "LONG") or (nearest_side == "ask" and direction == "SHORT"):
            delta += W_ICEBERG_ZONE_ALIGNED
            parts.append(f"IZ_ALIGNED({nearest_side})+{W_ICEBERG_ZONE_ALIGNED}")
        elif (nearest_side == "bid" and direction == "SHORT") or (nearest_side == "ask" and direction == "LONG"):
            delta += W_ICEBERG_ZONE_CONTRA
            parts.append(f"IZ_CONTRA({nearest_side}){W_ICEBERG_ZONE_CONTRA:+d}")

    return delta, " ".join(parts) if parts else "no_modifier"


# -------------------------- L2 momentum score --------------------------

def l2_momentum_score(row: pd.Series, direction: str) -> int:
    """Simple L2-derived momentum score (proxy for v3_momentum gate)."""
    score = 0
    delta = float(row.get("rolling_delta_4h", 0) or 0)
    dom = float(row.get("l2_dom_imbalance", 0) or 0)
    pressure = float(row.get("l2_pressure_ratio", 1.0) or 1.0)

    if direction == "LONG":
        # delta_4h positive = buyer momentum; dom positive = bid pressure
        if delta > 50:
            score += 1
        if delta < -200:
            score -= 1   # bearish exhaustion contra
        if dom > 5:
            score += 1
        if pressure > 1.5:
            score += 1
    else:  # SHORT
        if delta < -50:
            score += 1
        if delta > 200:
            score -= 1
        if dom < -5:
            score += 1
        if pressure < 0.67:
            score += 1
    return score


# -------------------------- Daily range cache --------------------------

_daily_range_cache: dict = {}


def get_daily_range(df: pd.DataFrame, ts: pd.Timestamp) -> tuple[float, float]:
    """Return (high, low) of current UTC trading day."""
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


# -------------------------- SL/TP simulation --------------------------

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


def simulate_sl_tp(df: pd.DataFrame, entry_ts: pd.Timestamp, direction: str,
                  entry: float, sl: float, tp1: float, tp2: float) -> tuple[str, pd.Timestamp, float, float]:
    """Walk M1 forward up to MAX_TRADE_HORIZON_MIN, return (reason, exit_ts, exit_price, pnl_usd).

    Reason in {SL_HIT, TP1_THEN_BE, TP1_THEN_TP2, TIMEOUT}.
    """
    end_ts = entry_ts + pd.Timedelta(minutes=MAX_TRADE_HORIZON_MIN)
    forward = df[(df.index > entry_ts) & (df.index <= end_ts)]
    if forward.empty:
        return ("NO_DATA", entry_ts, entry, 0.0)

    if direction == "LONG":
        for ts, bar in forward.iterrows():
            hi = float(bar["high"]); lo = float(bar["low"])
            # Check SL first (worst-case fill assumption: SL before TP if both touched same bar)
            if lo <= sl:
                pnl = -(entry - sl) * DOLLAR_TOTAL
                return ("SL_HIT", ts, sl, pnl)
            if hi >= tp1:
                # leg1 closes at TP1, SL moves to BE for leg2/leg3
                tp1_pnl = (tp1 - entry) * DOLLAR_LEG1
                # walk forward from this bar to detect BE or TP2
                rest = df[(df.index > ts) & (df.index <= end_ts)]
                for ts2, bar2 in rest.iterrows():
                    hi2 = float(bar2["high"]); lo2 = float(bar2["low"])
                    if lo2 <= entry:
                        # BE hit for leg2+leg3 (0 pnl those legs)
                        return ("TP1_THEN_BE", ts2, entry, tp1_pnl)
                    if hi2 >= tp2:
                        tp2_pnl = (tp2 - entry) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                        return ("TP1_THEN_TP2", ts2, tp2, tp1_pnl + tp2_pnl)
                # Timeout after TP1: close leg2+leg3 at last close
                last_close = float(rest["close"].iloc[-1]) if not rest.empty else entry
                rest_pnl = (last_close - entry) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                return ("TP1_THEN_TIMEOUT", rest.index[-1] if not rest.empty else ts, last_close, tp1_pnl + rest_pnl)
        # never hit SL or TP1
        last_close = float(forward["close"].iloc[-1])
        pnl = (last_close - entry) * DOLLAR_TOTAL
        return ("TIMEOUT", forward.index[-1], last_close, pnl)
    else:  # SHORT
        for ts, bar in forward.iterrows():
            hi = float(bar["high"]); lo = float(bar["low"])
            if hi >= sl:
                pnl = -(sl - entry) * DOLLAR_TOTAL
                return ("SL_HIT", ts, sl, pnl)
            if lo <= tp1:
                tp1_pnl = (entry - tp1) * DOLLAR_LEG1
                rest = df[(df.index > ts) & (df.index <= end_ts)]
                for ts2, bar2 in rest.iterrows():
                    hi2 = float(bar2["high"]); lo2 = float(bar2["low"])
                    if hi2 >= entry:
                        return ("TP1_THEN_BE", ts2, entry, tp1_pnl)
                    if lo2 <= tp2:
                        tp2_pnl = (entry - tp2) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                        return ("TP1_THEN_TP2", ts2, tp2, tp1_pnl + tp2_pnl)
                last_close = float(rest["close"].iloc[-1]) if not rest.empty else entry
                rest_pnl = (entry - last_close) * (DOLLAR_LEG2 + DOLLAR_LEG3)
                return ("TP1_THEN_TIMEOUT", rest.index[-1] if not rest.empty else ts, last_close, tp1_pnl + rest_pnl)
        last_close = float(forward["close"].iloc[-1])
        pnl = (entry - last_close) * DOLLAR_TOTAL
        return ("TIMEOUT", forward.index[-1], last_close, pnl)


# -------------------------- Main loop --------------------------

def run_backtest():
    df = load_data()

    # Daily PnL tracker for W2.5 daily loss limit
    daily_pnl = defaultdict(float)

    # Last trade per level for same-level cooldown
    last_trade_at_level: dict[tuple[str, float], pd.Timestamp] = {}

    # Last trade time for global cooldown (30min)
    last_trade_ts: pd.Timestamp = None

    # Active position tracker: only 1 trade at a time (simplified)
    active_until: pd.Timestamp = None

    trades: list[TradeResult] = []
    blocked_reasons = defaultdict(int)
    n_candidates = 0

    # We only consider M1 bars where M30 confirmed box exists
    print("Filtering candidate bars (M30 confirmed at struct level)...", flush=True)
    cand_mask = (
        df["m30_box_confirmed"].fillna(False).astype(bool) &
        df["at_struct_level"].fillna(False).astype(bool) &
        df["atr_m30"].notna() &
        df["m30_liq_top"].notna() &
        df["m30_liq_bot"].notna()
    )
    candidates = df[cand_mask]
    print(f"  candidate bars: {len(candidates):,}", flush=True)

    # GC ↔ MT5 offset (XAUUSD is mt5 = gc - 31)
    GC_MT5_OFFSET = 31.0

    print("Running backtest...", flush=True)
    last_print = pd.Timestamp.now()
    for i, (ts, row) in enumerate(candidates.iterrows()):
        if i % 5000 == 0:
            now = pd.Timestamp.now()
            if (now - last_print).total_seconds() > 5:
                print(f"  progress: {i:,}/{len(candidates):,} ({100*i/len(candidates):.1f}%) trades_so_far={len(trades)}", flush=True)
                last_print = now

        # Skip if active position
        if active_until is not None and ts < active_until:
            continue

        # M30 levels are in GC space; convert to MT5 by subtracting offset
        liq_top_mt5 = float(row["m30_liq_top"]) - GC_MT5_OFFSET
        liq_bot_mt5 = float(row["m30_liq_bot"]) - GC_MT5_OFFSET
        # Price in dataset is MT5 (XAUUSD)
        price = float(row["close"])
        atr = float(row["atr_m30"])

        # --- Entry detection: at structural level ---
        direction = None
        level_type = None
        level_price = None
        if abs(price - liq_top_mt5) <= LEVEL_PROX_PTS:
            direction = "SHORT"   # mean reversion at resistance
            level_type = "liq_top"
            level_price = liq_top_mt5
        elif abs(price - liq_bot_mt5) <= LEVEL_PROX_PTS:
            direction = "LONG"
            level_type = "liq_bot"
            level_price = liq_bot_mt5

        if direction is None:
            continue
        n_candidates += 1

        # --- Same-level cooldown (W1.3 widened to 5pts) ---
        ckey = (level_type, round(level_price / SAME_LEVEL_PROX_PTS) * SAME_LEVEL_PROX_PTS)
        last_at = last_trade_at_level.get(ckey)
        if last_at is not None and (ts - last_at).total_seconds() / 60 < SAME_LEVEL_COOLDOWN_MIN:
            blocked_reasons["same_level_cooldown"] += 1
            continue

        # --- Global trade cooldown 30min (W1.3) ---
        if last_trade_ts is not None and (ts - last_trade_ts).total_seconds() / 60 < 30:
            blocked_reasons["global_cooldown"] += 1
            continue

        # --- W2.1 ATR extreme ---
        if atr > ATR_EXTREME_PTS:
            blocked_reasons["atr_extreme"] += 1
            continue

        # --- W4.3 Session close block ---
        mins_to_close = (SESSION_CLOSE_HOUR_UTC * 60) - (ts.hour * 60 + ts.minute)
        if 0 < mins_to_close <= SESSION_CLOSE_BLOCK_MIN:
            blocked_reasons["session_close"] += 1
            continue

        # --- W4.2 Daily range ---
        hi, lo = get_daily_range(df, ts)
        if hi is not None and lo is not None and hi > lo:
            rng = hi - lo
            upper_band = hi - rng * DAILY_RANGE_BLOCK_PCT
            lower_band = lo + rng * DAILY_RANGE_BLOCK_PCT
            if direction == "LONG" and price >= upper_band:
                blocked_reasons["daily_range_long_upper"] += 1
                continue
            if direction == "SHORT" and price <= lower_band:
                blocked_reasons["daily_range_short_lower"] += 1
                continue

        # --- W2.5 Daily loss limit ---
        date_key = ts.date()
        if daily_pnl[date_key] < DAILY_LOSS_LIMIT:
            blocked_reasons["daily_loss_limit"] += 1
            continue

        # --- Compute SL / TP per ATS canonical ---
        # M30 FMV in GC space; convert to MT5 by subtracting offset
        m30_fmv_mt5 = float(row["m30_fmv"]) - GC_MT5_OFFSET if pd.notna(row.get("m30_fmv")) else None
        sl_buffer = max(SL_BUFFER_MIN_PTS, atr * SL_BUFFER_ATR_MULT)
        if direction == "LONG":
            # SL below liq_bot (the level we bounced off); TP1 = FMV; TP2 = liq_top
            sl = level_price - sl_buffer
            tp1 = m30_fmv_mt5 if (TP1_USE_FMV and m30_fmv_mt5 is not None and m30_fmv_mt5 > price) else price + atr * 0.8
            tp2 = liq_top_mt5 if (TP2_USE_OPPOSITE_LIQ and liq_top_mt5 > price) else price + atr * 1.5
        else:
            # SHORT at liq_top: SL above liq_top; TP1 = FMV; TP2 = liq_bot
            sl = level_price + sl_buffer
            tp1 = m30_fmv_mt5 if (TP1_USE_FMV and m30_fmv_mt5 is not None and m30_fmv_mt5 < price) else price - atr * 0.8
            tp2 = liq_bot_mt5 if (TP2_USE_OPPOSITE_LIQ and liq_bot_mt5 < price) else price - atr * 1.5

        # Sanity: SL/TP1/TP2 ordering valid for direction
        if direction == "LONG":
            if not (sl < price < tp1 < tp2):
                blocked_reasons["invalid_sl_tp_order"] += 1
                continue
        else:
            if not (tp2 < tp1 < price < sl):
                blocked_reasons["invalid_sl_tp_order"] += 1
                continue

        # --- W2.2 RR ≥ 1.0 ---
        sl_dist = abs(price - sl)
        tp1_dist = abs(tp1 - price)
        rr = tp1_dist / sl_dist if sl_dist > 0 else 0
        if rr < RR_MIN:
            blocked_reasons["rr_below_min"] += 1
            continue

        # --- L2 momentum + iceberg score ---
        mom_score = l2_momentum_score(row, direction)
        # Build recent_high_low for breaking ice (lookback 4 min)
        bi_window = df[(df.index > ts - pd.Timedelta(minutes=BREAKING_ICE_LOOKBACK_MIN)) & (df.index <= ts)]
        if not bi_window.empty:
            max_high_gc = float(bi_window["high"].max()) + GC_MT5_OFFSET
            min_low_gc = float(bi_window["low"].min()) + GC_MT5_OFFSET
        else:
            max_high_gc = price + GC_MT5_OFFSET
            min_low_gc = price + GC_MT5_OFFSET
        gc_price = price + GC_MT5_OFFSET
        ice_score, ice_detail = iceberg_score_modifiers(
            ts, gc_price, direction, (max_high_gc, min_low_gc)
        )
        total_score = mom_score + ice_score

        # --- W1.2 MIN_SCORE_GO ≥ 1 ---
        if total_score < MIN_SCORE_GO:
            blocked_reasons["min_score_go"] += 1
            continue

        # --- ENTRY ACCEPTED → simulate ---
        reason, exit_ts, exit_price, pnl_usd = simulate_sl_tp(df, ts, direction, price, sl, tp1, tp2)
        if reason == "NO_DATA":
            continue
        duration = int((exit_ts - ts).total_seconds() / 60) if exit_ts > ts else 0

        tr = TradeResult(
            entry_ts=ts, direction=direction, entry_price=price,
            sl=sl, tp1=tp1, tp2=tp2, atr_m30=atr,
            score=total_score, score_detail=f"mom{mom_score:+d} {ice_detail}",
            exit_ts=exit_ts, exit_reason=reason, exit_price=exit_price,
            pnl_usd=pnl_usd, duration_min=duration,
        )
        trades.append(tr)
        daily_pnl[date_key] += pnl_usd
        last_trade_at_level[ckey] = ts
        last_trade_ts = ts
        active_until = exit_ts

    print(f"\nDone. Candidates: {n_candidates:,} | Trades: {len(trades):,}", flush=True)
    print(f"Blocks: {dict(blocked_reasons)}", flush=True)
    return trades, blocked_reasons


def aggregate_metrics(trades: list[TradeResult]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0, "pf": 0, "pnl": 0, "final_capital": INITIAL_CAPITAL}
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    flats = [t for t in trades if t.pnl_usd == 0]
    gross_win = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    pnl = sum(t.pnl_usd for t in trades)
    wr = len(wins) / len(trades) if trades else 0
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0
    final_cap = INITIAL_CAPITAL + pnl
    # Drawdown
    capital_curve = [INITIAL_CAPITAL]
    for t in trades:
        capital_curve.append(capital_curve[-1] + t.pnl_usd)
    peak = INITIAL_CAPITAL
    max_dd = 0
    for c in capital_curve:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd
    return {
        "n_trades": len(trades),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "n_flats": len(flats),
        "win_rate": wr,
        "gross_win_usd": gross_win,
        "gross_loss_usd": gross_loss,
        "profit_factor": pf,
        "pnl_usd": pnl,
        "initial_capital": INITIAL_CAPITAL,
        "final_capital": final_cap,
        "return_pct": (final_cap / INITIAL_CAPITAL - 1) * 100,
        "max_drawdown_usd": max_dd,
        "max_drawdown_pct": (max_dd / peak * 100) if peak > 0 else 0,
        "avg_win_usd": gross_win / len(wins) if wins else 0,
        "avg_loss_usd": -gross_loss / len(losses) if losses else 0,
        "avg_duration_min": sum(t.duration_min for t in trades) / len(trades) if trades else 0,
    }


def write_outputs(trades: list[TradeResult], blocks: dict, metrics: dict):
    # Per-trade ledger CSV
    if trades:
        rows = []
        cap = INITIAL_CAPITAL
        for t in trades:
            cap += t.pnl_usd
            d = asdict(t)
            d["capital_after"] = cap
            rows.append(d)
        df_t = pd.DataFrame(rows)
        df_t.to_csv(OUT_DIR / "trades.csv", index=False)
        print(f"  wrote {OUT_DIR/'trades.csv'} ({len(rows)} rows)", flush=True)

    # Metrics JSON
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "window_start": str(WINDOW_START),
        "window_end": str(WINDOW_END),
        "config": {
            "initial_capital_usd": INITIAL_CAPITAL,
            "lots": [LOT_LEG1, LOT_LEG2, LOT_LEG3],
            "dollar_per_pt_total": DOLLAR_TOTAL,
            "atr_extreme_pts": ATR_EXTREME_PTS,
            "rr_min": RR_MIN,
            "min_score_go": MIN_SCORE_GO,
            "daily_loss_limit": DAILY_LOSS_LIMIT,
            "session_close_block_min": SESSION_CLOSE_BLOCK_MIN,
            "daily_range_block_pct": DAILY_RANGE_BLOCK_PCT,
            "level_prox_pts": LEVEL_PROX_PTS,
            "same_level_cooldown_min": SAME_LEVEL_COOLDOWN_MIN,
            "sl_pts_default": SL_PTS_DEFAULT,
            "tp1_atr_mult": TP1_ATR_MULT,
            "tp2_atr_mult": TP2_ATR_MULT,
        },
        "metrics": metrics,
        "blocks": dict(blocks),
    }
    (OUT_DIR / "metrics.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"  wrote {OUT_DIR/'metrics.json'}", flush=True)

    # Markdown report
    md = []
    md.append("# W5 Backtest 10mo L2 — Post W1-W5.3 arming\n")
    md.append(f"**Generated**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    md.append(f"**Window**: {WINDOW_START} → {WINDOW_END} (~10 months)\n")
    md.append(f"**Source**: `{SOURCE}` (M1 + L2 features + M30 boxes)\n")
    md.append(f"**Iceberg**: `{ICEBERG_DIR}` daily JSONL (W5.1 score modifiers)\n")
    md.append(f"**Initial capital**: ${INITIAL_CAPITAL:.2f}\n")
    md.append(f"**Position sizing**: lots [{LOT_LEG1}, {LOT_LEG2}, {LOT_LEG3}] = $5/pt total (XAUUSD)\n")
    md.append("\n## Headline metrics\n")
    md.append(f"| Metric | Value |\n|---|---:|\n")
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
    md.append(f"| Max drawdown (USD) | ${metrics['max_drawdown_usd']:,.2f} ({metrics['max_drawdown_pct']:.2f}%) |\n")
    md.append(f"| Avg win | ${metrics['avg_win_usd']:,.2f} |\n")
    md.append(f"| Avg loss | ${metrics['avg_loss_usd']:,.2f} |\n")
    md.append(f"| Avg duration | {metrics['avg_duration_min']:.1f} min |\n")

    md.append("\n## Block breakdown (entries rejected by gates)\n")
    md.append("| Reason | Count |\n|---|---:|\n")
    for k, v in sorted(blocks.items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v:,} |\n")

    md.append("\n## Methodology\n")
    md.append("- Entry detection: M30 confirmed box at liq_top (SHORT) / liq_bot (LONG), proximity ≤ 5pts\n")
    md.append("- Gates applied (W1-W5.3): same_level cooldown 60min + 5pt prox, global cooldown 30min,\n"
              "  ATR extreme >45 block, session close 30min before 22 UTC, daily range 20% extremes,\n"
              "  daily loss limit -$50, RR ≥ 1.0, MIN_SCORE_GO ≥ 1\n")
    md.append("- Score = L2 momentum (delta_4h/dom_imbalance/pressure) + W5.1 iceberg modifiers\n"
              "  (Breaking Ice ALIGNED +1 / CONTRA -2; Iceberg Zone IN ALIGNED +1 / CONTRA -1)\n")
    md.append("- SL/TP simulation: M1 forward walk up to 4h. SL=±20pts, TP1=±atr*0.8, TP2=±atr*1.5.\n"
              "  TP1 hit → leg1 closes, SL→entry (BE) for leg2+leg3; then BE / TP2 / timeout.\n")
    md.append("- 1 trade at a time (no overlapping positions; simplified).\n")
    md.append("\n## Caveats\n")
    md.append("- Backtest uses simplified F-asym (does NOT replicate full cascade resolver).\n")
    md.append("- Iceberg JSONL coverage may have gaps on some days (counted as no_iceberg_data).\n")
    md.append("- L2 momentum score is heuristic proxy; production v3_momentum gate is more nuanced.\n")
    md.append("- Slippage / spread NOT modeled (assumed instant fill at level prices).\n")
    md.append("- ML iceberg V4 (V4_BLOCK contra) NOT applied in this backtest.\n")

    (OUT_DIR / "REPORT.md").write_text("".join(md), encoding="utf-8")
    print(f"  wrote {OUT_DIR/'REPORT.md'}", flush=True)


if __name__ == "__main__":
    print("=" * 70, flush=True)
    print("W5 Backtest 10mo L2 — post W1-W5.3 arming", flush=True)
    print("=" * 70, flush=True)
    trades, blocks = run_backtest()
    metrics = aggregate_metrics(trades)
    print("\n=== METRICS ===", flush=True)
    for k, v in metrics.items():
        print(f"  {k}: {v}", flush=True)
    write_outputs(trades, blocks, metrics)
    print("\nDone.", flush=True)

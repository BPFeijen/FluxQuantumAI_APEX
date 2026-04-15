#!/usr/bin/env python3
"""
C:\FluxQuantumAI\live\position_monitor.py
APEX Position Monitor -- Layer 4

Runs as a background thread (every 2 seconds). For every open APEX position:

  CHECK 1 -- Breakeven (SHIELD)
    Detect when Leg 1 TP1 was hit (leg1 ticket no longer open).
    If Leg 2 and/or Leg 3 are still open: move their SL to entry price.
    Trade can no longer go negative after SHIELD activates.

  CHECK 2 -- L2 Danger exit
    Compute danger_score for last 3 M30 bars from microstructure CSV.
    danger_score = 100 * abs(dom_imbalance) when bar_delta is AGAINST position.
    If danger_score >= DANGER_THRESHOLD for 3+ consecutive bars: close Leg 2 + Leg 3.

  CHECK 3 -- Delta 4H regime flip
    If open LONG and delta_4h < REGIME_FLIP_BEAR: exit immediately.
    If open SHORT and delta_4h > REGIME_FLIP_BULL: exit immediately.

  CHECK 4 -- Cascade protection
    If price moves > CASCADE_ATR_FACTOR x ATR against position in < CASCADE_WINDOW_S:
    Close ALL open legs immediately (full position exit).

All MT5 operations delegated to MT5Executor. Never touches Layer 1 receivers.
"""

from __future__ import annotations

import csv
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from mt5_executor import MT5Executor, MAGIC, SYMBOL, LOT_SIZE, MIN_LOT, _split_lots
from live.hedge_manager import HedgeManager

# V3 RL -- lazy import so the module loads even without sb3-contrib installed
try:
    from rl.v3_agent import V3Agent as _V3Agent
except ImportError:
    _V3Agent = None  # type: ignore

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MICRO_DIR       = Path("C:/data/level2/_gc_xcec")
TRADES_CSV      = Path("C:/FluxQuantumAI/logs/trades.csv")
DECISIONS_LOG   = Path("C:/FluxQuantumAI/logs/position_decisions.log")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MONITOR_INTERVAL_S   = 2.0      # seconds between position checks
DANGER_THRESHOLD     = 70       # danger_score threshold for L2 danger exit
DANGER_BARS          = 3        # consecutive danger bars required before exit
CASCADE_ATR_FACTOR   = 2.0      # price move > N x ATR against position -> cascade exit
CASCADE_WINDOW_S     = 300      # cascade check window (5 minutes)
MAX_MICRO_ROWS       = 5_400    # ~3 hours of 2-second bars; covers last 3 M30 bars comfortably

# T3 Defense Exit (2026-04-14) — anomaly + adverse move + M30 level break
# Spec: closes open position when ALL 3 conditions are met simultaneously.
# CAN close position in profit — this is a risk exit, not a P&L exit.
# A profitable position near a broken level during anomaly is still at risk.
T3_ADVERSE_PTS       = 3.0      # minimum adverse price move (pts)
T3_WINDOW_S          = 60       # lookback window for adverse move (seconds)
T3_COOLDOWN_S        = 300      # minimum seconds between T3 exits (5 min)
T3_KILL_SWITCH       = Path("C:/FluxQuantumAI/DISABLE_T3_EXIT")
SERVICE_STATE_PATH   = Path("C:/FluxQuantumAI/logs/service_state.json")

THRESHOLDS_PATH = Path("C:/FluxQuantumAI/config/settings.json")
M30_BOXES_PATH  = Path("C:/data/processed/gc_m30_boxes.parquet")

log = logging.getLogger("apex.monitor")


def _load_thresholds() -> dict:
    """
    Load data-driven thresholds from thresholds_gc.json.
    All values derived from GC 62 trades / 115 days real data -- no hardcoded assumptions.
    Falls back to safe conservative defaults if file is missing.
    """
    defaults = {
        "delta_4h_short_block":       0,
        "delta_4h_long_block":        -600,
        "trend_resumption_signal":    "delta_4h_flip",
        "trend_resumption_threshold": None,  # 🔴 TBD -- CAL-03, fail-open until set
        "trailing_stop_pts":          77,
        "trailing_stop_activation":   "tp1_hit",
        "max_positions":              3,
        "margin_level_min":           600,
        "source":                     "defaults",
        "next_recalibration":         "after 30 new live trades",
    }
    try:
        with open(THRESHOLDS_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        defaults.update(loaded)
        log.info("thresholds loaded: source=%s", loaded.get("source", "?"))
    except FileNotFoundError:
        log.warning("thresholds file not found: %s -- using safe defaults", THRESHOLDS_PATH)
    except Exception as e:
        log.error("thresholds load error: %s -- using safe defaults", e)
    return defaults

# ---------------------------------------------------------------------------
# MT5 (read-only reference -- executor handles all MT5 writes)
# ---------------------------------------------------------------------------
_mt5 = None
try:
    import MetaTrader5 as _m
    if _m.initialize():
        _mt5 = _m
except Exception:
    pass


def _mt5_price() -> Optional[float]:
    if _mt5 is None:
        return None
    try:
        tick = _mt5.symbol_info_tick(SYMBOL)
        if tick:
            return round((tick.ask + tick.bid) / 2.0, 2)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Microstructure helpers
# ---------------------------------------------------------------------------

def _micro_path() -> Optional[Path]:
    """Find today's (or most recent) microstructure file."""
    now = datetime.now(timezone.utc)
    for offset in range(3):
        d = (now - pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
        for suf in [".fixed.csv.gz", ".csv.gz"]:
            p = MICRO_DIR / f"microstructure_{d}{suf}"
            if p.exists():
                return p
    return None


def _read_micro_tail() -> Optional[pd.DataFrame]:
    """
    Read tail of microstructure file for risk checks.
    Returns last MAX_MICRO_ROWS rows with columns:
      timestamp, mid_price, bar_delta, dom_imbalance, large_order_imbalance
    Returns None on any error.
    """
    path = _micro_path()
    if path is None:
        return None
    try:
        cols = ["timestamp", "mid_price", "bar_delta", "dom_imbalance", "large_order_imbalance"]
        df   = pd.read_csv(path, usecols=cols)
        if df.empty:
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.tail(MAX_MICRO_ROWS).reset_index(drop=True)
    except Exception as e:
        log.warning("micro read error: %s", e)
        return None


def _compute_atr(df: pd.DataFrame) -> float:
    """ATR proxy: price range over last 30 min (~1800 rows at 1s bars)."""
    prices = df.tail(1800)["mid_price"].dropna()
    atr = float(prices.max() - prices.min()) if len(prices) > 1 else 20.0
    return max(atr, 5.0)


def _compute_delta_4h(df: pd.DataFrame) -> float:
    """Sum of bar_delta over last 4 hours."""
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(hours=4)
    return float(df[df["timestamp"] >= cutoff]["bar_delta"].sum())


def _derive_m30_bias(df: pd.DataFrame) -> str:
    """
    Compute M30 macro bias from parquet OHLC structure.
    Mirrors level_detector._get_m30_bias() -- no separate column needed.

    Bullish: liq_top extended ABOVE box_high  -> UP breakout confirmed
    Bearish: liq_bot extended BELOW box_low   -> DN breakout confirmed
    Unknown: box not confirmed or levels not available
    """
    import math
    try:
        confirmed = df[df["m30_box_confirmed"] == True]
        row       = confirmed.iloc[-1] if not confirmed.empty else df.iloc[-1]

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


def _get_m30_snapshot_from_parquet() -> dict:
    """
    Read gc_m30_boxes.parquet and return regime context.
    3-retry with 0.1s sleep to handle concurrent atomic writes from m30_updater.

    Returns
    -------
    {
        "bias":    str,              "bullish" | "bearish" | "unknown"
        "liq_top": float | None,     M30 liq_top in GC space
        "liq_bot": float | None,     M30 liq_bot in GC space
        "fmv":     float | None,     M30 fair-market-value in GC space
        "bars":    pd.DataFrame | None  -- last 10 M30 bars with OHLC (GC space)
    }
    """
    result: dict = {
        "bias": "unknown", "liq_top": None, "liq_bot": None, "fmv": None, "bars": None,
    }
    for attempt in range(3):
        try:
            df = pd.read_parquet(M30_BOXES_PATH)
            if df.empty:
                return result
            last           = df.iloc[-1]
            result["bias"]    = _derive_m30_bias(df)
            result["liq_top"] = float(last.get("m30_liq_top")) if pd.notna(last.get("m30_liq_top")) else None
            result["liq_bot"] = float(last.get("m30_liq_bot")) if pd.notna(last.get("m30_liq_bot")) else None
            result["fmv"]     = float(last.get("m30_fmv"))     if pd.notna(last.get("m30_fmv"))     else None
            result["bars"]    = df.tail(10).copy()
            return result
        except Exception as e:
            if attempt < 2:
                time.sleep(0.1)
            else:
                log.warning("_get_m30_snapshot_from_parquet failed after 3 tries: %s", e)
    return result


def _get_m30_bias_from_parquet() -> str:
    """Thin wrapper -- kept for backward compatibility."""
    return _get_m30_snapshot_from_parquet()["bias"]


def _compute_fvg_m30(df_m30: pd.DataFrame, direction: str) -> Optional[dict]:
    """
    Find the most recent Fair Value Gap in M30 OHLC data.

    ICT definition -- 3-bar structure:
      Bullish FVG (for LONG): bar[i].high < bar[i+2].low
        -> unfilled gap = potential support for limit entries
      Bearish FVG (for SHORT): bar[i].low > bar[i+2].high
        -> unfilled gap = potential resistance for limit entries

    Scans backwards from the most recent bar to find the closest valid FVG.
    Returns dict with {bottom, top, midpoint} in GC price space, or None.
    """
    if df_m30 is None or len(df_m30) < 3:
        return None

    bars = df_m30[["high", "low"]].reset_index(drop=True)
    n    = len(bars)

    for i in range(n - 3, -1, -1):
        hi_i  = float(bars.iloc[i]["high"])
        lo_i  = float(bars.iloc[i]["low"])
        hi_i2 = float(bars.iloc[i + 2]["high"])
        lo_i2 = float(bars.iloc[i + 2]["low"])

        if direction == "LONG":
            # Bullish FVG: gap between bar[i] high and bar[i+2] low
            if hi_i < lo_i2:
                bottom   = hi_i
                top      = lo_i2
                midpoint = (bottom + top) / 2.0
                return {
                    "bottom":   round(bottom,   2),
                    "top":      round(top,       2),
                    "midpoint": round(midpoint,  2),
                    "bar_idx":  i,
                }
        else:  # SHORT
            # Bearish FVG: gap between bar[i+2] high and bar[i] low
            if hi_i2 < lo_i:
                bottom   = hi_i2
                top      = lo_i
                midpoint = (bottom + top) / 2.0
                return {
                    "bottom":   round(bottom,   2),
                    "top":      round(top,       2),
                    "midpoint": round(midpoint,  2),
                    "bar_idx":  i,
                }
    return None



def _danger_scores_last_3bars(df: pd.DataFrame, direction: str) -> list[float]:
    """
    Compute danger_score for the last 3 M30 bars.

    danger_score = 100 * abs(dom_imbalance)  when bar_delta is AGAINST the position.
    'AGAINST' means:
      - direction LONG  -> bar_delta < 0 (selling pressure)
      - direction SHORT -> bar_delta > 0 (buying pressure)

    Returns list of up to 3 scores (most recent last).
    """
    if df is None or df.empty:
        return []

    # Group into 30-minute bars
    df2 = df.copy()
    df2["m30"] = df2["timestamp"].dt.floor("30min")
    grouped = df2.groupby("m30").agg(
        bar_delta=("bar_delta", "sum"),
        dom_imbalance=("dom_imbalance", "mean"),
    ).reset_index().sort_values("m30")

    last3 = grouped.tail(3)
    scores = []
    for _, row in last3.iterrows():
        bd  = float(row["bar_delta"])
        dom = float(row["dom_imbalance"])
        against = (direction == "LONG" and bd < 0) or (direction == "SHORT" and bd > 0)
        score = round(100.0 * abs(dom), 1) if against else 0.0
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Trade record helpers (trades.csv)
# ---------------------------------------------------------------------------

def _load_trades() -> list[dict]:
    if not TRADES_CSV.exists():
        return []
    try:
        with open(TRADES_CSV, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        log.warning("trades.csv read error: %s", e)
        return []


def _find_trade_for_position(pos: dict, trades: list[dict]) -> Optional[dict]:
    """
    Match an open MT5 position to its trades.csv record.
    Matches on leg1_ticket, leg2_ticket, or leg3_ticket.
    Returns the most recent matching trade row.
    """
    ticket = str(pos["ticket"])
    matches = [
        t for t in trades
        if t.get("leg1_ticket") == ticket
        or t.get("leg2_ticket") == ticket
        or t.get("leg3_ticket") == ticket
    ]
    return matches[-1] if matches else None


# ---------------------------------------------------------------------------
# PositionMonitor
# ---------------------------------------------------------------------------

class PositionMonitor:
    """
    Background monitor for all open APEX positions.

    Parameters
    ----------
    executor : MT5Executor
        Shared executor instance (used for breakeven + close operations).
    dry_run : bool
        If True: log actions but do NOT send MT5 orders.
    """

    def __init__(self, executor: MT5Executor, dry_run: bool = True, v3_agent=None,
                 lot_size: float = LOT_SIZE, executor_live=None):
        self.executor       = executor
        self.executor_live  = executor_live   # Hantec live executor (optional)
        self.dry_run        = dry_run
        self.v3_agent       = v3_agent   # Optional[V3Agent]
        self._lot_size      = lot_size   # configured lot (from --lot_size arg)

        self._running   = False
        self._lock      = threading.Lock()

        # Per-position state: ticket -> state dict
        # state: {shield_done, danger_streak, entry_time, entry_price}
        self._state: dict[int, dict] = {}

        # Cascade tracking: ticket -> list of (timestamp, price) tuples
        self._price_history: dict[int, list[tuple[float, float]]] = {}

        # Offensive flip cooldown -- prevent double-flip within the same cycle
        # (can happen when 2 SHORTs are open simultaneously)
        self._offensive_flip_fired_ts: float = 0.0

        # Decision point throttle: ticket -> monotonic time of last log
        self._last_decision_log: dict[int, float] = {}

        # Decision log file handle (append mode, opened once)
        DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._dec_log_fh = open(DECISIONS_LOG, "a", encoding="utf-8", buffering=1)

        # Hedge manager -- pullback hedge lifecycle (post-SHIELD only)
        self._hedge_mgr = HedgeManager(executor, dry_run=dry_run)

        # Data-driven thresholds -- loaded from thresholds_gc.json at startup
        self._thresholds = _load_thresholds()
        self._recal_last_count = 0   # confirmed trade count at last recalibration check

        # T3 Defense Exit mode — loaded ONCE at startup (requires restart to change)
        self._t3_mode = str(self._thresholds.get("t3_exit_mode", "SHADOW")).upper()
        self._t3_last_exit_mono = 0.0
        log.info("T3 Defense Exit: mode=%s (requires restart to change)", self._t3_mode)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the monitor in a background daemon thread."""
        self._running = True
        t = threading.Thread(target=self._monitor_loop, name="position_monitor", daemon=True)
        t.start()
        log.info("PositionMonitor started (interval=%.0fs  dry_run=%s)", MONITOR_INTERVAL_S, self.dry_run)
        thr = self._thresholds
        print("DATA-DRIVEN THRESHOLDS ACTIVE -- GC calibrated")
        print(f"  source        : {thr.get('source', '?')}")
        print(f"  SHORT block   : delta_4h > {thr['delta_4h_short_block']}")
        print(f"  LONG  block   : delta_4h < {thr['delta_4h_long_block']}")
        print(f"  trailing stop : {thr['trailing_stop_pts']} pts"
              f"  (activation: {thr['trailing_stop_activation']})")
        print(f"  next recal    : {thr.get('next_recalibration', '?')}")

    def stop(self) -> None:
        self._running = False
        self._hedge_mgr.stop()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                self._run_checks()
            except Exception as e:
                import traceback as _tb
                try:
                    with open("C:/FluxQuantumAI/logs/monitor_crash.log", "a") as _f:
                        import datetime as _dt
                        _f.write(f"\n=== {_dt.datetime.utcnow()} ===\n")
                        _tb.print_exc(file=_f)
                except Exception:
                    pass
                log.error("monitor loop error: %s", e)
            elapsed   = time.monotonic() - t0
            remaining = MONITOR_INTERVAL_S - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def _run_checks(self) -> None:
        positions = self.executor.get_open_positions()
        if not positions:
            return

        trades   = _load_trades()
        df_micro = _read_micro_tail()   # ~1.2s -- acceptable at 2s interval
        now_mono = time.monotonic()
        now_ts   = time.time()

        # Price from Quantower microstructure -- MT5 is execution-only.
        # Consistent with event_processor.py architecture fix.
        price: Optional[float] = None
        if df_micro is not None and not df_micro.empty:
            try:
                price = round(float(df_micro["mid_price"].iloc[-1]), 2)
            except (TypeError, ValueError, KeyError):
                price = None

        atr      = _compute_atr(df_micro)   if df_micro is not None else 20.0
        delta_4h = _compute_delta_4h(df_micro) if df_micro is not None else 0.0

        for pos in positions:
            ticket    = pos["ticket"]
            direction = pos["direction"]
            entry     = pos["entry"]

            # Find paired trade record (before state init to read entry_mode)
            trade_rec = _find_trade_for_position(pos, trades)

            # --- Init per-position state ---
            with self._lock:
                if ticket not in self._state:
                    self._state[ticket] = {
                        "shield_done":        False,
                        "danger_streak":      0,
                        "regime_flip_streak": 0,   # CAL-03: bars flip condition must persist before firing
                        "entry_mono":         now_mono,
                        "entry_price":        entry,
                        # Strategy context from trade record (Phase 1: trade awareness)
                        "entry_mode":    trade_rec.get("entry_mode", "") if trade_rec else "",
                        "daily_trend":   trade_rec.get("daily_trend", "") if trade_rec else "",
                        "phase":         trade_rec.get("phase", "") if trade_rec else "",
                        "strategy_mode": trade_rec.get("strategy_mode", "") if trade_rec else "",
                        # Pullback end tracking
                        "pullback_end_streak": 0,
                    }
                state = self._state[ticket]

            # Update price history for cascade check
            self._update_price_history(ticket, now_ts, price)

            # --- Decision point snapshot (logged every 30s) ---
            self._log_decision_point(pos, state, delta_4h, atr, df_micro, price, now_mono)

            # ----------------------------------------------------------
            # CHECK 1 -- Breakeven (SHIELD)
            # ----------------------------------------------------------
            self._check_breakeven(pos, trade_rec, state, positions)

            # ----------------------------------------------------------
            # CHECK 2 -- L2 Danger exit
            # ----------------------------------------------------------
            self._check_l2_danger(pos, trade_rec, state, df_micro, direction)

            # ----------------------------------------------------------
            # CHECK 3 -- Delta 4H regime flip + Offensive Flip
            # ----------------------------------------------------------
            self._check_regime_flip(
                pos, trade_rec, state, delta_4h, direction,
                price=price, atr=atr, df_micro=df_micro,
            )

            # ----------------------------------------------------------
            # CHECK 4 -- Cascade protection
            # ----------------------------------------------------------
            self._check_cascade(pos, trade_rec, state, atr, direction, now_ts, now_mono)

            # ----------------------------------------------------------
            # CHECK T3 -- Defense Exit (anomaly + adverse + M30 break)
            # ----------------------------------------------------------
            self._check_t3_defense_exit(
                pos, trade_rec, state, price, direction, now_ts, now_mono)

            # ----------------------------------------------------------
            # CHECK 5 -- Trailing stop (only after SHIELD / TP1 hit)
            # ----------------------------------------------------------
            if state.get("shield_done") and price is not None:
                self._check_trailing_stop(pos, state, price, direction)

            # ----------------------------------------------------------
            # CHECK 6 -- Pullback hedge (only post-SHIELD)
            # ----------------------------------------------------------
            self._hedge_mgr.process(pos, state, price, delta_4h, atr, df_micro)

            # ----------------------------------------------------------
            # CHECK 7 -- Pullback End Exit
            # Close pullback trades when the main trend resumes.
            # ----------------------------------------------------------
            if price is not None:
                self._check_pullback_end(pos, trade_rec, state, price, direction, atr)

            # ----------------------------------------------------------
            # V3 RL management decision (shadow / live)
            # Map current V2 verdict to the signal V3 expects:
            #   danger / regime flip pending -> "REGIME_SHIFT"
            #   otherwise -> "HOLD"
            # The V3 agent handles the override internally if mode=live.
            # ----------------------------------------------------------
            if self.v3_agent is not None:
                scores    = _danger_scores_last_3bars(df_micro, direction) if df_micro is not None else []
                thr_v3    = self._thresholds
                d4h_flip  = (
                    (direction == "LONG"  and delta_4h < float(thr_v3.get("delta_4h_long_block", -600)))
                    or
                    (direction == "SHORT" and
                     thr_v3.get("trend_resumption_threshold") is not None and
                     delta_4h > float(thr_v3.get("trend_resumption_threshold", 0)))
                )
                danger_now = (len(scores) >= DANGER_BARS and
                              all(s >= DANGER_THRESHOLD for s in scores[-DANGER_BARS:]))
                v2_verdict = "REGIME_SHIFT" if (d4h_flip or danger_now) else "HOLD"
                self._v3_management_hook(
                    pos           = pos,
                    trade_rec     = trade_rec,
                    state         = state,
                    all_positions = positions,
                    price         = price,
                    atr           = atr,
                    delta_4h      = delta_4h,
                    df_micro      = df_micro,
                    v2_verdict    = v2_verdict,
                    now_ts        = now_ts,
                )

        # Clean up hedge state for positions that have closed
        self._hedge_mgr.cleanup_closed({p["ticket"] for p in positions})

        # Recalibration reminder -- checked once per cycle, not per position
        self._check_recalibration(trades)

    # ------------------------------------------------------------------
    # V3 RL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _v3_l2_snapshot_from_micro(df: Optional[pd.DataFrame], delta_4h: float) -> dict:
        """Build l2_snapshot dict for V3FeatureEngine from microstructure tail."""
        if df is not None and not df.empty:
            last       = df.iloc[-1]
            bar_delta  = float(last.get("bar_delta", 0.0) or 0.0)
            dom_imb    = float(last.get("dom_imbalance", 0.0) or 0.0)
        else:
            bar_delta  = 0.0
            dom_imb    = 0.0
        return {
            "dom_imbalance":             dom_imb,
            "delta_m1":                  bar_delta,
            "delta_5min":                delta_4h / 48.0,
            "delta_4h":                  delta_4h,
            "delta_4h_history":          [delta_4h],
            "buy_volume":                1.0,
            "sell_volume":               1.0,
            "spread":                    0.5,
            "total_bid_depth":           500.0,
            "total_ask_depth":           500.0,
            "tick_volume_m1":            0.0,
            "tick_volume_m1_avg20":      1.0,
            "bid_absorption":            0.0,
            "ask_absorption":            0.0,
            "daily_trend":               "LONG" if delta_4h > 0 else "SHORT",
            "levels_broken_contra_15min": 0,
        }

    @staticmethod
    def _v3_m30_levels_from_pos(pos: dict, atr: float) -> dict:
        """Build m30_levels dict from position entry price (best approximation live)."""
        entry = float(pos.get("entry", 0.0))
        _sl  = pos.get("sl")
        _tp1 = pos.get("tp1")
        sl   = float(_sl  if _sl  is not None else entry - atr)
        tp1  = float(_tp1 if _tp1 is not None else entry + atr)
        return {
            "m30_fmv":           entry,
            "m30_liq_top":       tp1,
            "m30_liq_bot":       sl,
            "atr_m30":           atr,
            "atr_m30_20d_avg":   atr,
            "m30_box_confirmed": False,
            "m30_box_direction": 0,
            "weekly_aligned":    False,
            "atr_d1":            atr * 3.0,
        }

    def _v3_position_dict(
        self,
        pos:       dict,
        state:     dict,
        price:     Optional[float],
        all_positions: list,
    ) -> dict:
        """Build the position dict expected by V3FeatureEngine.compute()."""
        entry     = float(pos.get("entry", 0.0))
        direction = pos.get("direction", "LONG")
        price_now = price or entry
        sign      = 1 if direction == "LONG" else -1
        pnl_pts   = sign * (price_now - entry)
        thr       = self._thresholds
        trail_pts = float(thr.get("trailing_stop_pts") or 77)

        # Count open legs for this trade group (all positions opened within 30 s)
        open_tickets = {p["ticket"] for p in all_positions}
        legs_open    = len(open_tickets)   # approximate: group count already enforced upstream

        sl  = pos.get("sl",  entry - sign * trail_pts)
        tp1 = pos.get("tp1", entry + sign * trail_pts)

        return {
            "direction":       direction,
            "entry_price":     entry,
            "pnl":             pnl_pts,
            "mfe":             max(pnl_pts, 0.0),   # live MFE not tracked here
            "mae":             max(-pnl_pts, 0.0),
            "shield_active":   state.get("shield_done", False),
            "legs_open":       legs_open,
            "hedge_active":    state.get("hedge_active", False),
            "runner_open":     True,
            "trailing_active": state.get("trailing_active", False),
            "tp1_price":       tp1,
            "sl_price":        sl,
            "entry_time":      time.time() - (time.monotonic() - state.get("entry_mono", time.monotonic())),
        }

    # ------------------------------------------------------------------
    # V3 management hook -- called once per position per monitor cycle
    # ------------------------------------------------------------------

    def _v3_management_hook(
        self,
        pos:            dict,
        trade_rec:      Optional[dict],
        state:          dict,
        all_positions:  list,
        price:          Optional[float],
        atr:            float,
        delta_4h:       float,
        df_micro:       Optional[pd.DataFrame],
        v2_verdict:     str,   # "HOLD" | "REGIME_SHIFT"
        now_ts:         float,
    ) -> None:
        """
        Ask V3 agent what to do with this position.
        In shadow mode: log decision, do nothing.
        In live mode: execute non-trivial V3 actions (EARLY_EXIT, OPEN_HEDGE, CLOSE_HEDGE).
        """
        if self.v3_agent is None:
            return

        direction = pos.get("direction", "LONG")
        ts        = _ts()

        try:
            pos_dict = self._v3_position_dict(pos, state, price, all_positions)
            result   = self.v3_agent.decide_management(
                price          = price or float(pos.get("entry", 0.0)),
                m30_levels     = self._v3_m30_levels_from_pos(pos, atr),
                l2_snapshot    = self._v3_l2_snapshot_from_micro(df_micro, delta_4h),
                iceberg_scan   = {"detected": False, "direction": "NEUTRAL", "score": 0.0},
                position       = pos_dict,
                utc_hour       = datetime.now(timezone.utc).hour,
                current_date   = datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                v2_decision    = v2_verdict,
                trade_group_id = str(pos.get("ticket", "")),
            )
        except Exception as _e:
            log.warning("V3 management hook error: %s", _e)
            return

        v3_action  = result.get("action", "HOLD")
        v3_mode    = self.v3_agent.mode
        override   = result.get("override", False)
        conf       = result.get("confidence", 0.0)

        print(f"[{ts}] V3  ticket={pos['ticket']} {direction}  action={v3_action}"
              f"  conf={conf:.2f}  override={override}  mode={v3_mode}")

        # Shadow mode: decisions are logged by explainer but not executed
        if v3_mode != "live":
            return

        # -- Live mode: act on non-trivial V3 actions -----------------
        if v3_action == "EARLY_EXIT":
            log.info("V3 LIVE: EARLY_EXIT ticket=%d", pos["ticket"])
            if trade_rec is not None:
                for col in ("leg1_ticket", "leg2_ticket", "leg3_ticket"):
                    tkt = int(trade_rec.get(col, 0) or 0)
                    if tkt > 0:
                        self._close_ticket(tkt, "V3_EARLY_EXIT", ts)
            else:
                self._close_ticket(pos["ticket"], "V3_EARLY_EXIT", ts)

        elif v3_action == "OPEN_HEDGE" and not state.get("hedge_active"):
            # Delegate to HedgeManager -- open a 0.01-lot contra
            log.info("V3 LIVE: OPEN_HEDGE ticket=%d", pos["ticket"])
            state["hedge_active"] = True   # flag for next cycle
            # Actual hedge open is handled by HedgeManager.process() on the next tick

        elif v3_action == "CLOSE_HEDGE" and state.get("hedge_active"):
            log.info("V3 LIVE: CLOSE_HEDGE ticket=%d", pos["ticket"])
            state["hedge_active"] = False

        elif v3_action in ("TIGHTEN_TRAIL", "WIDEN_TRAIL") and price is not None:
            trail_pts = float(self._thresholds.get("trailing_stop_pts") or 77)
            direction = pos.get("direction", "LONG")
            current_sl = pos.get("sl", 0.0)
            if v3_action == "TIGHTEN_TRAIL":
                # Move trail tighter by 10 pts
                new_sl = round(price - (trail_pts - 10) if direction == "LONG"
                               else price + (trail_pts - 10), 2)
            else:
                # Widen trail by 10 pts (capped at original SL distance)
                new_sl = round(price - (trail_pts + 10) if direction == "LONG"
                               else price + (trail_pts + 10), 2)
            should = (direction == "LONG" and new_sl > current_sl) or \
                     (direction == "SHORT" and new_sl < current_sl)
            if should and not self.dry_run:
                ok, msg = self.executor._modify_sl(pos["ticket"], new_sl)
                if ok:
                    log.info("V3 LIVE: %s ticket=%d sl %.2f->%.2f",
                             v3_action, pos["ticket"], current_sl, new_sl)

    # ------------------------------------------------------------------
    # CHECK 1 -- Breakeven
    # ------------------------------------------------------------------

    def _check_breakeven(
        self, pos: dict, trade_rec: Optional[dict],
        state: dict, all_positions: list[dict]
    ) -> None:
        """
        If Leg 1 TP1 was hit (ticket no longer open), move Leg 2 + Leg 3 SL to entry.
        Only fires once per trade (shield_done flag).
        """
        if state.get("shield_done"):
            return
        if trade_rec is None:
            # Fallback: infer SHIELD from SL position (e.g., after monitor restart).
            # If SL is at or within 5 pts of entry, SHIELD was already applied.
            sl = pos.get("sl", 0.0)
            if sl > 0 and abs(sl - pos["entry"]) <= 5.0:
                log.info("SHIELD inferred from SL position: ticket=%d sl=%.2f entry=%.2f",
                         pos["ticket"], sl, pos["entry"])
                state["shield_done"] = True
            return

        leg1_ticket = int(trade_rec.get("leg1_ticket", 0) or 0)
        leg2_ticket = int(trade_rec.get("leg2_ticket", 0) or 0)
        leg3_ticket = int(trade_rec.get("leg3_ticket", 0) or 0)

        if leg1_ticket <= 0:
            return  # not a 3-leg trade record or leg1 unknown

        open_tickets = {p["ticket"] for p in all_positions}
        leg1_closed  = leg1_ticket not in open_tickets
        leg2_open    = leg2_ticket in open_tickets
        leg3_open    = leg3_ticket in open_tickets

        if not leg1_closed:
            return  # Leg 1 still open -- TP1 not hit yet
        if not (leg2_open or leg3_open):
            return  # all legs closed

        entry_price = pos["entry"]
        ts = _ts()
        log.info("SHIELD ACTIVATED: leg1=%d closed, moving SL to entry=%.2f (leg2=%d leg3=%d)",
                 leg1_ticket, entry_price, leg2_ticket, leg3_ticket)
        print(f"[{ts}] SHIELD: Leg1 TP1 hit -- moving SL to entry {entry_price:.2f}")
        print(f"[{ts}]   leg2={leg2_ticket}  leg3={leg3_ticket}")

        if self.dry_run:
            print(f"[{ts}]   [DRY RUN] WOULD move SL to {entry_price:.2f}")
            state["shield_done"] = True
            return

        result = self.executor.move_to_breakeven(leg2_ticket, leg3_ticket, entry_price)
        if result["success"]:
            print(f"[{ts}]   SHIELD OK -- modified tickets: {result['modified']}")
        else:
            print(f"[{ts}]   SHIELD PARTIAL -- errors: {result['errors']}")
        state["shield_done"] = True

    # ------------------------------------------------------------------
    # CHECK 2 -- L2 Danger exit
    # ------------------------------------------------------------------

    def _check_l2_danger(
        self, pos: dict, trade_rec: Optional[dict],
        state: dict, df_micro: Optional[pd.DataFrame], direction: str
    ) -> None:
        """
        Close Leg 2 + Leg 3 if danger_score >= DANGER_THRESHOLD for DANGER_BARS consecutive bars.
        """
        if df_micro is None:
            return

        scores = _danger_scores_last_3bars(df_micro, direction)
        if len(scores) < DANGER_BARS:
            return

        all_danger = all(s >= DANGER_THRESHOLD for s in scores[-DANGER_BARS:])
        if not all_danger:
            state["danger_streak"] = sum(1 for s in reversed(scores) if s >= DANGER_THRESHOLD)
            return

        state["danger_streak"] = DANGER_BARS
        ts = _ts()
        log.warning("L2 DANGER EXIT: %d consecutive danger bars (scores=%s) for %s",
                    DANGER_BARS, scores, direction)
        print(f"[{ts}] L2 DANGER: {DANGER_BARS} consecutive danger bars {scores} -- closing Leg2+Leg3")

        if trade_rec is None:
            # No record: close this position by ticket
            self._close_ticket(pos["ticket"], "L2_DANGER", ts)
            return

        leg2_ticket = int(trade_rec.get("leg2_ticket", 0) or 0)
        leg3_ticket = int(trade_rec.get("leg3_ticket", 0) or 0)
        for tkt in (leg2_ticket, leg3_ticket):
            if tkt > 0:
                self._close_ticket(tkt, "L2_DANGER", ts)

    # ------------------------------------------------------------------
    # CHECK 3 -- Delta 4H regime flip
    # ------------------------------------------------------------------

    def _check_regime_flip(
        self, pos: dict, trade_rec: Optional[dict],
        state: dict, delta_4h: float, direction: str,
        price: Optional[float] = None,
        atr: float = 20.0,
        df_micro: Optional[pd.DataFrame] = None,
    ) -> None:
        """
        Exit immediately when regime turns against the open position.
        On confirmed flip: close all legs, then fire Offensive Flip (3-leg reversal).

        SHORT exit -- Option C confluence (CAL-03 2026-04-10):
          BOTH conditions must be true:
            1. delta_4h < trend_resumption_threshold_short (default -800)
               -> selling climax / exhaustion signal
            2. m30_bias == 'bullish'
               -> M30 Wyckoff structure already flipped to demand
          On flip confirmed: open LONG (1 Market + 2 BUY_LIMITs at M30 FVG).

        LONG exit:
          delta_4h < delta_4h_long_block (calibrated -1050)
          -> sustained sell flow confirms bear regime resumption
          On flip confirmed: open SHORT (1 Market + 2 SELL_LIMITs at M30 FVG).

        After SHIELD: runner is at breakeven SL, worst outcome is +0.
        Regime flip suppressed post-SHIELD -- trailing stop and cascade manage the runner.
        """
        if state.get("shield_done"):
            return

        thr        = self._thresholds
        block_long = float(thr.get("delta_4h_long_block") or -600)
        min_bars   = int(thr.get("delta_flip_min_bars", 47))   # CAL-03: 47 bars x 2s = 94s of sustained signal

        condition_met = False
        reason        = ""
        flip_snap     = None   # will hold M30 snapshot for offensive flip

        if direction == "SHORT":
            short_thr = float(thr.get("trend_resumption_threshold_short", -800))
            if delta_4h < short_thr:
                snap     = _get_m30_snapshot_from_parquet()
                m30_bias = snap["bias"]
                ts_log   = _ts()
                print(f"[{ts_log}] REGIME_CHECK SHORT: delta_4h={delta_4h:+.0f} < {short_thr:.0f}"
                      f" | m30_bias={m30_bias}")
                if m30_bias == "bullish":
                    condition_met = True
                    flip_snap     = snap
                    reason        = (
                        f"REGIME_EXIT SHORT: delta_4h={delta_4h:+.0f} (climax < {short_thr:.0f})"
                        f" AND m30_bias=bullish -- selling exhaustion + structural flip confirmed"
                    )
                else:
                    log.info("REGIME_CHECK SHORT: delta_4h climax but m30_bias=%s -- holding", m30_bias)

        elif direction == "LONG":
            if delta_4h < block_long:
                snap          = _get_m30_snapshot_from_parquet()
                m30_bias      = snap["bias"]
                condition_met = True
                flip_snap     = snap
                reason        = (
                    f"REGIME_EXIT LONG: delta_4h={delta_4h:+.0f} < {block_long:.0f}"
                    f" -- sustained sell flow, bear resumption (m30_bias={m30_bias})"
                )

        # --- CAL-03: delta_flip_min_bars persistence gate ---
        # Condition must persist for min_bars consecutive monitor checks (2s each)
        # before the flip fires. Prevents false exits from momentary delta spikes.
        if condition_met:
            state["regime_flip_streak"] = state.get("regime_flip_streak", 0) + 1
        else:
            prev = state.get("regime_flip_streak", 0)
            if prev > 0:
                ts_log = _ts()
                print(f"[{ts_log}] REGIME_STREAK {direction}: condition cleared -- streak reset ({prev} bars)")
            state["regime_flip_streak"] = 0
            return

        streak = state["regime_flip_streak"]
        if streak < min_bars:
            ts_log = _ts()
            print(f"[{ts_log}] REGIME_STREAK {direction}: {streak}/{min_bars} bars"
                  f" -- waiting for confirmation (d4h={delta_4h:+.0f})")
            return

        # Condition met AND streak >= min_bars -> fire flip
        state["regime_flip_streak"] = 0   # reset so it can re-arm if needed

        ts = _ts()
        log.warning("TREND RESUMPTION EXIT: %s | %s", direction, reason)
        print(f"[{ts}] {reason} -- closing ALL legs")

        # Close all legs
        if trade_rec is None:
            self._close_ticket(pos["ticket"], "REGIME_FLIP", ts)
        else:
            leg1_ticket = int(trade_rec.get("leg1_ticket", 0) or 0)
            leg2_ticket = int(trade_rec.get("leg2_ticket", 0) or 0)
            leg3_ticket = int(trade_rec.get("leg3_ticket", 0) or 0)
            for tkt in (leg1_ticket, leg2_ticket, leg3_ticket):
                if tkt > 0:
                    self._close_ticket(tkt, "REGIME_FLIP", ts)

        # Offensive flip: open 3-leg reversal position
        if price is not None and df_micro is not None and flip_snap is not None:
            self._offensive_flip(
                closed_direction=direction,
                price=price,
                atr=atr,
                df_micro=df_micro,
                m30_snap=flip_snap,
                ts=ts,
            )
        else:
            log.info("OFFENSIVE_FLIP skipped: price=%s df_micro=%s", price, "ok" if df_micro is not None else "None")

    # ------------------------------------------------------------------
    # Offensive Flip -- open 3-leg reversal after regime exit
    # ------------------------------------------------------------------

    _OFFENSIVE_FLIP_COOLDOWN_S = 30.0  # prevent double-flip if 2 positions trigger same cycle

    def _offensive_flip(
        self,
        closed_direction: str,          # direction of closed position ("SHORT" or "LONG")
        price: float,                   # current XAUUSD price from _mt5_price()
        atr: float,                     # ATR from microstructure (GC space ≈ MT5 space)
        df_micro: pd.DataFrame,         # microstructure tail for GC/MT5 offset
        m30_snap: dict,                 # from _get_m30_snapshot_from_parquet()
        ts: str,                        # timestamp string for logging
    ) -> None:
        """
        Open a 3-leg reversal position immediately after regime flip:
          Leg 1 -> Market order at current price
          Leg 2 -> Limit at FVG midpoint (retest entry)
          Leg 3 -> Limit at FVG bottom/top (deeper retest -- runner)

        FVG levels are sourced from the last 10 M30 bars in GC space,
        then converted to XAUUSD (MT5) space using the inline GC/MT5 offset.

        SL/TP:
          LONG flip: SL = price - 1.5xATR, TP1 = M30 fmv, TP2 = M30 liq_top
          SHORT flip: SL = price + 1.5xATR, TP1 = M30 fmv, TP2 = M30 liq_bot
        """
        # Cooldown: prevent two flips from two concurrent same-direction positions
        now_mono = time.monotonic()
        if now_mono - self._offensive_flip_fired_ts < self._OFFENSIVE_FLIP_COOLDOWN_S:
            log.info("OFFENSIVE_FLIP cooldown active (%.0fs) -- skip",
                     self._OFFENSIVE_FLIP_COOLDOWN_S - (now_mono - self._offensive_flip_fired_ts))
            return
        self._offensive_flip_fired_ts = now_mono

        flip_dir = "LONG" if closed_direction == "SHORT" else "SHORT"

        # Compute GC->MT5 offset from live microstructure vs MT5 tick
        gc_mid = float(df_micro["mid_price"].iloc[-1]) if not df_micro.empty else 0.0
        mt5_px = _mt5_price()
        if mt5_px is None or mt5_px <= 0:
            # Fall back to price arg (already in MT5 space) and use default offset
            mt5_px = price
        if gc_mid > 0 and mt5_px > 0:
            offset = gc_mid - mt5_px   # GC − XAUUSD (≈ +31 pts carry premium)
        else:
            offset = 31.0
        log.debug("OFFENSIVE_FLIP offset=%.2f  gc_mid=%.2f  mt5_px=%.2f", offset, gc_mid, mt5_px)

        # M30 levels converted to MT5 space
        m30_top_gc = m30_snap.get("liq_top")
        m30_bot_gc = m30_snap.get("liq_bot")
        m30_fmv_gc = m30_snap.get("fmv")
        m30_top    = round(m30_top_gc - offset, 2) if m30_top_gc else None
        m30_bot    = round(m30_bot_gc - offset, 2) if m30_bot_gc else None
        m30_fmv    = round(m30_fmv_gc - offset, 2) if m30_fmv_gc else None

        # FVG detection from M30 OHLC bars
        fvg = _compute_fvg_m30(m30_snap.get("bars"), flip_dir)

        # SL / TP / Limit level computation (all in XAUUSD MT5 space)
        if flip_dir == "LONG":
            sl    = round(mt5_px - atr * 1.5, 2)
            tp1   = round(m30_fmv or (mt5_px + atr * 0.7), 2)
            tp2   = round(m30_top or (mt5_px + atr * 2.0), 2)

            # Limit entries BELOW current price (retest on pullback)
            if fvg:
                raw_l2 = round(fvg["midpoint"] - offset, 2)
                raw_l3 = round(fvg["bottom"]   - offset, 2)
            else:
                raw_l2 = round(mt5_px - atr * 0.40, 2)
                raw_l3 = round(mt5_px - atr * 0.80, 2)

            # Safety: limits must be strictly below market, above SL
            limit2 = max(sl + atr * 0.25, min(raw_l2, mt5_px - 0.5))
            limit3 = max(sl + atr * 0.10, min(raw_l3, limit2 - 0.5))
            limit2 = round(limit2, 2)
            limit3 = round(limit3, 2)

        else:  # flip_dir == "SHORT"
            sl    = round(mt5_px + atr * 1.5, 2)
            tp1   = round(m30_fmv or (mt5_px - atr * 0.7), 2)
            tp2   = round(m30_bot or (mt5_px - atr * 2.0), 2)

            # Limit entries ABOVE current price (retest on pullback up)
            if fvg:
                raw_l2 = round(fvg["midpoint"] - offset, 2)
                raw_l3 = round(fvg["top"]      - offset, 2)
            else:
                raw_l2 = round(mt5_px + atr * 0.40, 2)
                raw_l3 = round(mt5_px + atr * 0.80, 2)

            # Safety: limits must be strictly above market, below SL
            limit2 = min(sl - atr * 0.25, max(raw_l2, mt5_px + 0.5))
            limit3 = min(sl - atr * 0.10, max(raw_l3, limit2 + 0.5))
            limit2 = round(limit2, 2)
            limit3 = round(limit3, 2)

        l1, l2, l3 = _split_lots(self._lot_size)

        # Telemetry
        fvg_info = (
            f"FVG: bot={fvg['bottom']:.2f} mid={fvg['midpoint']:.2f} top={fvg['top']:.2f}(GC)"
            if fvg else "FVG: none -- using ATR fallback"
        )
        print(f"[{ts}] OFFENSIVE_FLIP {flip_dir}: entry={mt5_px:.2f}  "
              f"SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}")
        print(f"[{ts}]   {fvg_info}")
        print(f"[{ts}]   L1=market  L2=limit@{limit2:.2f}  L3=limit@{limit3:.2f}"
              f"  offset={offset:.1f}pts")
        log.info("OFFENSIVE_FLIP %s: mt5=%.2f sl=%.2f tp1=%.2f tp2=%.2f "
                 "L2@%.2f L3@%.2f atr=%.2f fvg=%s",
                 flip_dir, mt5_px, sl, tp1, tp2, limit2, limit3, atr,
                 "yes" if fvg else "no")

        if self.dry_run:
            print(f"[{ts}]   [DRY RUN] WOULD open {flip_dir} 3-leg: "
                  f"1xmarket + 1xlimit@{limit2:.2f} + 1xlimit@{limit3:.2f}")
            return

        # --- Leg 1: Market order ---
        lot1 = l1 if l1 >= MIN_LOT else MIN_LOT
        r1   = self.executor.open_single(
            symbol=SYMBOL, direction=flip_dir,
            lot=lot1, sl=sl, tp=tp1,
            comment="APEX_FLIP_L1",
        )
        t1         = r1.get("ticket", 0) if r1.get("success") else 0
        entry_fill = r1.get("entry", mt5_px)
        if not r1.get("success"):
            log.error("OFFENSIVE_FLIP Leg1 FAILED: %s -- aborting flip", r1.get("error"))
            print(f"[{ts}]   FLIP Leg1 FAILED: {r1.get('error')} -- limits NOT placed")
            return
        print(f"[{ts}]   FLIP Leg1 OK  ticket={t1}  entry={entry_fill:.2f}")

        # --- Leg 2: Limit order ---
        t2 = 0
        if l2 >= MIN_LOT:
            r2 = self.executor.open_limit(
                symbol=SYMBOL, direction=flip_dir, lot=l2,
                limit_price=limit2, sl=sl, tp=tp1,
                comment="APEX_FLIP_L2",
                expiry_hours=4.0,
            )
            t2 = r2.get("ticket", 0) if r2.get("success") else 0
            s2 = "OK" if r2.get("success") else f"FAILED({r2.get('error')})"
            print(f"[{ts}]   FLIP Leg2 {s2}  ticket={t2}  limit={limit2:.2f}")

        # --- Leg 3: Limit order (runner) ---
        t3 = 0
        if l3 >= MIN_LOT:
            r3 = self.executor.open_limit(
                symbol=SYMBOL, direction=flip_dir, lot=l3,
                limit_price=limit3, sl=sl, tp=tp2,
                comment="APEX_FLIP_L3",
                expiry_hours=4.0,
            )
            t3 = r3.get("ticket", 0) if r3.get("success") else 0
            s3 = "OK" if r3.get("success") else f"FAILED({r3.get('error')})"
            print(f"[{ts}]   FLIP Leg3 {s3}  ticket={t3}  limit={limit3:.2f}")

        # --- Mirror flip on Hantec live executor (if connected) ---
        if self.executor_live is not None:
            lot1_live = l1 if l1 >= MIN_LOT else MIN_LOT
            rl1 = self.executor_live.open_single(
                symbol=SYMBOL, direction=flip_dir,
                lot=lot1_live, sl=sl, tp=tp1,
                comment="APEX_FLIP_L1",
            )
            if rl1.get("success"):
                print(f"[{ts}]   FLIP Leg1 [LIVE] OK  ticket={rl1.get('ticket',0)}  entry={rl1.get('entry',0):.2f}")
            else:
                log.error("OFFENSIVE_FLIP Leg1 LIVE FAILED: %s", rl1.get("error"))
                print(f"[{ts}]   FLIP Leg1 [LIVE] FAILED: {rl1.get('error')}")

            if l2 >= MIN_LOT:
                rl2 = self.executor_live.open_limit(
                    symbol=SYMBOL, direction=flip_dir, lot=l2,
                    limit_price=limit2, sl=sl, tp=tp1,
                    comment="APEX_FLIP_L2", expiry_hours=4.0,
                )
                s2l = "OK" if rl2.get("success") else f"FAILED({rl2.get('error')})"
                print(f"[{ts}]   FLIP Leg2 [LIVE] {s2l}  ticket={rl2.get('ticket',0)}  limit={limit2:.2f}")

            if l3 >= MIN_LOT:
                rl3 = self.executor_live.open_limit(
                    symbol=SYMBOL, direction=flip_dir, lot=l3,
                    limit_price=limit3, sl=sl, tp=tp2,
                    comment="APEX_FLIP_L3", expiry_hours=4.0,
                )
                s3l = "OK" if rl3.get("success") else f"FAILED({rl3.get('error')})"
                print(f"[{ts}]   FLIP Leg3 [LIVE] {s3l}  ticket={rl3.get('ticket',0)}  limit={limit3:.2f}")

        # Log to trades.csv
        self.executor.log_trade(
            direction=flip_dir,
            decision="OFFENSIVE_FLIP",
            lots=self._lot_size,
            entry=entry_fill,
            sl=sl, tp1=tp1, tp2=tp2,
            result="open", pnl=0.0, gate_score=0,
            leg1_ticket=t1, leg2_ticket=t2, leg3_ticket=t3,
        )

    # ------------------------------------------------------------------
    # CHECK 4 -- Cascade protection
    # ------------------------------------------------------------------

    def _update_price_history(
        self, ticket: int, now_ts: float, price: Optional[float]
    ) -> None:
        if price is None:
            return
        if ticket not in self._price_history:
            self._price_history[ticket] = []
        hist = self._price_history[ticket]
        hist.append((now_ts, price))
        # Prune entries older than CASCADE_WINDOW_S
        cutoff = now_ts - CASCADE_WINDOW_S
        self._price_history[ticket] = [(t, p) for t, p in hist if t >= cutoff]

    def _check_cascade(
        self, pos: dict, trade_rec: Optional[dict],
        state: dict, atr: float,
        direction: str, now_ts: float, now_mono: float
    ) -> None:
        """
        If price moves > CASCADE_ATR_FACTOR x ATR against position in CASCADE_WINDOW_S:
        close ALL legs immediately.
        """
        hist = self._price_history.get(pos["ticket"], [])
        if len(hist) < 2:
            return

        cutoff    = now_ts - CASCADE_WINDOW_S
        window    = [(t, p) for t, p in hist if t >= cutoff]
        if not window:
            return

        price_now   = window[-1][1]
        price_start = window[0][1]
        move        = price_now - price_start

        threshold = CASCADE_ATR_FACTOR * atr
        against   = (direction == "LONG" and move < -threshold) or \
                    (direction == "SHORT" and move > threshold)

        if not against:
            return

        ts = _ts()
        log.warning("CASCADE EXIT: price moved %.1f pts (threshold=%.1f) against %s in <5min",
                    abs(move), threshold, direction)
        print(f"[{ts}] CASCADE: price moved {abs(move):.1f}pts > {threshold:.1f}pts ({CASCADE_ATR_FACTOR:.0f}xATR)"
              f" against {direction} in <5min -- closing ALL legs")

        if trade_rec is None:
            self._close_ticket(pos["ticket"], "CASCADE", ts)
            return

        leg1_ticket = int(trade_rec.get("leg1_ticket", 0) or 0)
        leg2_ticket = int(trade_rec.get("leg2_ticket", 0) or 0)
        leg3_ticket = int(trade_rec.get("leg3_ticket", 0) or 0)
        for tkt in (leg1_ticket, leg2_ticket, leg3_ticket):
            if tkt > 0:
                self._close_ticket(tkt, "CASCADE", ts)

    # ------------------------------------------------------------------
    # CHECK T3 -- Defense Exit (anomaly + adverse move + M30 level break)
    # ------------------------------------------------------------------
    # Spec: CAN close position in profit. This is a RISK exit.
    # A profitable position at a broken level during anomaly is still at risk.
    # The 10:30 UTC vela on 2026-04-14 proved this: 30pts drop in 4 min.
    # ------------------------------------------------------------------

    _t3_last_exit_mono: float = 0.0  # cooldown tracker
    _t3_mode: str = "SHADOW"         # set from settings.json on init

    def _check_t3_defense_exit(
        self, pos: dict, trade_rec: Optional[dict],
        state: dict, price: Optional[float],
        direction: str, now_ts: float, now_mono: float,
    ) -> None:
        """
        T3 Defense Exit: close position when ALL 3 conditions are met:
          1. Defense mode active (ENTRY_BLOCK or DEFENSIVE_EXIT from service_state.json)
          2. Adverse price move >= T3_ADVERSE_PTS in last T3_WINDOW_S seconds
          3. M30 structural level broken (price < liq_bot for LONG, > liq_top for SHORT)

        Modes:
          SHADOW: log only, no close
          LIVE:   log + close (requires restart to activate, NOT hot-swappable)

        Kill switch: C:/FluxQuantumAI/DISABLE_T3_EXIT file disables regardless of mode.
        """
        if price is None:
            return

        try:
            # Kill switch
            if T3_KILL_SWITCH.exists():
                return

            # Cooldown
            if now_mono - self._t3_last_exit_mono < T3_COOLDOWN_S:
                return

            # ── CONDITION 1: Defense mode active ──
            defense_tier = "NORMAL"
            stress_direction = "HOLD"
            try:
                if SERVICE_STATE_PATH.exists():
                    with open(SERVICE_STATE_PATH, "r") as f:
                        ss = json.load(f)
                    defense_tier = ss.get("defense_tier", "NORMAL")
                    stress_direction = ss.get("stress_direction", "HOLD")
            except Exception:
                return  # can't read state, skip

            if defense_tier == "NORMAL":
                return

            # ── CONDITION 2: Adverse price move ──
            hist = self._price_history.get(pos["ticket"], [])
            t3_cutoff = now_ts - T3_WINDOW_S
            t3_window = [(t, p) for t, p in hist if t >= t3_cutoff]
            if len(t3_window) < 2:
                return

            if direction == "LONG":
                peak = max(p for _, p in t3_window)
                adverse_move = peak - price
            else:
                trough = min(p for _, p in t3_window)
                adverse_move = price - trough

            if adverse_move < T3_ADVERSE_PTS:
                return

            # ── CONDITION 3: M30 level broken ──
            m30_snap = _get_m30_snapshot_from_parquet()
            m30_liq_bot = m30_snap.get("liq_bot")
            m30_liq_top = m30_snap.get("liq_top")

            level_broken = False
            broken_level = ""
            if direction == "LONG" and m30_liq_bot is not None:
                if price < m30_liq_bot:
                    level_broken = True
                    broken_level = f"price {price:.2f} < m30_liq_bot {m30_liq_bot:.2f}"
            elif direction == "SHORT" and m30_liq_top is not None:
                if price > m30_liq_top:
                    level_broken = True
                    broken_level = f"price {price:.2f} > m30_liq_top {m30_liq_top:.2f}"

            if not level_broken:
                return

            # ── ALL 3 CONDITIONS MET ──
            entry_price = pos.get("entry", 0)
            current_pnl = (price - entry_price) if direction == "LONG" else (entry_price - price)
            in_profit = current_pnl > 0

            ts = _ts()
            log_msg = (
                f"position={direction} ticket={pos['ticket']} | "
                f"anomaly_tier={defense_tier} stress={stress_direction} | "
                f"adverse_move={adverse_move:.1f}pts/{T3_WINDOW_S}s | "
                f"level_break={broken_level} | "
                f"entry={entry_price:.2f} price={price:.2f} pnl={current_pnl:+.1f}pts "
                f"in_profit={in_profit} | "
                f"timestamp={datetime.now(timezone.utc).isoformat()}"
            )

            # Determine mode (LIVE requires restart — read once at startup from settings)
            mode = self._t3_mode

            if mode == "LIVE":
                # ── EXECUTE CLOSE ──
                log.warning("[T3_LIVE_EXECUTED] %s | action=CLOSE", log_msg)
                print(f"[{ts}] [T3_LIVE_EXECUTED] {log_msg}")

                if trade_rec is not None:
                    for key in ("leg1_ticket", "leg2_ticket", "leg3_ticket"):
                        tkt = int(trade_rec.get(key, 0) or 0)
                        if tkt > 0:
                            self._close_ticket(tkt, "T3_DEFENSE_EXIT", ts)
                else:
                    self._close_ticket(pos["ticket"], "T3_DEFENSE_EXIT", ts)

                self._t3_last_exit_mono = now_mono

                try:
                    from live import telegram_notifier as tg
                    tg._send_async(
                        f"\U0001F6A8 <b>T3 DEFENSE EXIT — {direction}</b>\n"
                        f"Anomaly: {defense_tier}\n"
                        f"Adverse: {adverse_move:.1f}pts in {T3_WINDOW_S}s\n"
                        f"Level: {broken_level}\n"
                        f"PnL: {current_pnl:+.1f}pts {'(profit)' if in_profit else '(loss)'}\n"
                        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
                    )
                except Exception:
                    pass
            else:
                # ── SHADOW LOG ──
                would_action = f"EXIT_{direction}"
                log.warning("[T3_SHADOW] would_exit=YES %s | action=%s", log_msg, would_action)
                print(f"[{ts}] [T3_SHADOW] would_exit=YES | {would_action} | {log_msg}")

        except Exception as e:
            log.debug("T3 check error: %s", e)

    # ------------------------------------------------------------------
    # CHECK 5 -- Trailing stop
    # ------------------------------------------------------------------

    def _check_trailing_stop(
        self, pos: dict, state: dict, price: float, direction: str
    ) -> None:
        """
        Ratchet SL toward current price after SHIELD is active (TP1 hit).
        trail_pts = P90 MAE post-TP1 from GC 62-trade study (thresholds_gc.json).
        Only ever tightens SL -- never moves it away from entry.

        SHORT: trail_sl = price + trail_pts  ->  update if trail_sl < current_sl
        LONG:  trail_sl = price - trail_pts  ->  update if trail_sl > current_sl
        """
        thr        = self._thresholds
        trail_pts  = float(thr.get("trailing_stop_pts") or 77)
        ticket     = pos["ticket"]
        current_sl = pos.get("sl", 0.0)

        # SHIELD RECOVERY: if SHIELD fired (shield_done=True) but MT5 SL is still 0
        # (modify_sl failed at SHIELD time), re-apply breakeven before any trailing.
        # Trailing with current_sl=0 would place trail_sl at price ± 77pts which can
        # be worse than entry -- nullifying SHIELD protection entirely.
        if current_sl <= 0:
            entry_price = pos["entry"]
            ts = _ts()
            log.warning("SHIELD RECOVERY: ticket=%d sl=0 -- re-applying BE=%.2f",
                        ticket, entry_price)
            print(f"[{ts}] SHIELD RECOVERY ticket={ticket}: SL missing, re-applying BE={entry_price:.2f}")
            if not self.dry_run:
                ok, msg = self.executor._modify_sl(ticket, entry_price)
                if ok:
                    print(f"[{ts}]   SHIELD RECOVERY OK -- SL={entry_price:.2f}")
                else:
                    log.error("SHIELD RECOVERY failed ticket=%d: %s", ticket, msg)
                    print(f"[{ts}]   SHIELD RECOVERY FAILED: {msg}")
            return  # Don't trail yet -- SL state uncertain; retry next cycle

        if direction == "SHORT":
            trail_sl     = round(price + trail_pts, 2)
            should_update = trail_sl < current_sl
        else:  # LONG
            trail_sl     = round(price - trail_pts, 2)
            should_update = trail_sl > current_sl

        if not should_update:
            return

        ts = _ts()
        log.info("TRAILING STOP: ticket=%d %s  price=%.2f  sl: %.2f -> %.2f  (trail=%.0fpts)",
                 ticket, direction, price, current_sl, trail_sl, trail_pts)
        print(f"[{ts}] TRAIL {direction} ticket={ticket}:"
              f"  price={price:.2f}  old_sl={current_sl:.2f}  new_sl={trail_sl:.2f}"
              f"  (trail={trail_pts:.0f}pts)")

        if self.dry_run:
            print(f"[{ts}]   [DRY RUN] WOULD update SL to {trail_sl:.2f}")
            return

        ok, msg = self.executor._modify_sl(ticket, trail_sl)
        if ok:
            print(f"[{ts}]   TRAIL SL updated: {trail_sl:.2f}")
        else:
            print(f"[{ts}]   TRAIL SL failed: {msg}")
            log.warning("trailing SL failed ticket=%d: %s", ticket, msg)

    # ------------------------------------------------------------------
    # CHECK 7 -- Pullback End Exit (Group A + Group B design)
    # ------------------------------------------------------------------
    #
    # Exit pullback trade (contra daily_trend) when:
    #   GROUP A (pullback completion, >= 1):
    #     A1: price reached liq target zone (within 5pts of liq_bot/liq_top)
    #     A2: MFE > 0.5 * ATR (significant move achieved)
    #     A3: 3 consecutive bars failed to make new extreme (exhaustion)
    #   GROUP B (trend resumption, >= 2):
    #     B1: price inside current M30 box
    #     B2: price reclaimed current FMV
    #     B3: buyers/sellers returning (2 consecutive candles in trend direction)
    #     B4: reclaim candle (range > 0.5*ATR, close near extreme in trend dir)
    #
    # Constraints: pnl > 0, current M30 box only, persistence >= 3 checks (6s)
    #
    # Calibration: 540 trades Jul2025-Apr2026
    #   PnL mean +8.7pts, win 100%, MFE captured 20.1pts, bars to exit 6.4

    _PULLBACK_END_PERSIST = 3    # consecutive 2s checks before firing

    def _check_pullback_end(
        self, pos: dict, trade_rec: Optional[dict],
        state: dict, price: float, direction: str, atr: float
    ) -> None:
        """
        Close pullback trades when both pullback completion AND trend resumption
        are confirmed. Uses current M30 box only. M30 = execution context.
        """
        entry_mode  = state.get("entry_mode", "")
        daily_trend = state.get("daily_trend", "")

        # Only applies to trades contra the daily trend
        is_contra = (
            (direction == "SHORT" and daily_trend == "long")
            or (direction == "LONG" and daily_trend == "short")
        )
        if not is_contra:
            state["pullback_end_streak"] = 0
            return

        # Read current M30 structure
        snap = _get_m30_snapshot_from_parquet()
        fmv     = snap.get("fmv")
        liq_top = snap.get("liq_top")
        liq_bot = snap.get("liq_bot")
        bars    = snap.get("bars")  # last 10 M30 bars

        if fmv is None or bars is None or bars.empty:
            return

        # Current box boundaries
        last_bar = bars.iloc[-1]
        bh = float(last_bar.get("m30_box_high", 0)) if pd.notna(last_bar.get("m30_box_high")) else None
        bl = float(last_bar.get("m30_box_low", 0)) if pd.notna(last_bar.get("m30_box_low")) else None

        # Entry price (GC space — M30 levels are GC)
        entry_price = state.get("entry_price", pos["entry"])

        # PnL (for logging only — does NOT gate the exit decision)
        if direction == "SHORT":
            pnl = entry_price - price
        else:
            pnl = price - entry_price

        # Track MFE for A2
        if "pullback_mfe" not in state:
            state["pullback_mfe"] = 0.0
        if direction == "SHORT":
            cur_mfe = entry_price - price
        else:
            cur_mfe = price - entry_price
        if cur_mfe > state["pullback_mfe"]:
            state["pullback_mfe"] = cur_mfe

        # Track recent lows/highs for A3 (from M30 bars)
        # Use last 3 bars from the snapshot
        recent_bars = bars.tail(3)

        # ==============================================================
        # GROUP A — Pullback completion evidence (need >= 1)
        # ==============================================================
        a1 = False  # reached liq target
        a2 = False  # significant MFE
        a3 = False  # exhausted (no new extremes)

        if direction == "SHORT":
            # A1: price approached liq_bot
            if liq_bot is not None:
                # Check if price ever got within 5pts of liq_bot during this trade
                lowest_in_bars = float(bars["low"].min())
                a1 = lowest_in_bars <= liq_bot + 5.0
            # A2: MFE > 0.5 * ATR
            a2 = state["pullback_mfe"] > atr * 0.5
            # A3: last 3 M30 bars failed to make new low (lows rising)
            if len(recent_bars) >= 3:
                lows = [float(recent_bars.iloc[k]["low"]) for k in range(3)]
                a3 = lows[2] >= lows[1] and lows[1] >= lows[0]
        else:  # LONG contra short trend
            if liq_top is not None:
                highest_in_bars = float(bars["high"].max())
                a1 = highest_in_bars >= liq_top - 5.0
            a2 = state["pullback_mfe"] > atr * 0.5
            if len(recent_bars) >= 3:
                highs = [float(recent_bars.iloc[k]["high"]) for k in range(3)]
                a3 = highs[2] <= highs[1] and highs[1] <= highs[0]

        group_a = a1 or a2 or a3

        if not group_a:
            state["pullback_end_streak"] = 0
            return

        # ==============================================================
        # GROUP B — Trend resumption evidence (need >= 2)
        # ==============================================================
        b_count = 0
        b_flags = []

        # B1: price inside current M30 box
        b1 = bh is not None and bl is not None and bl <= price <= bh
        if b1:
            b_count += 1
            b_flags.append("B1:in_box")

        # B2: price reclaimed current FMV (in trend direction)
        if direction == "SHORT":  # trend = long, so reclaim = price > FMV
            b2 = price > fmv
        else:  # trend = short, reclaim = price < FMV
            b2 = price < fmv
        if b2:
            b_count += 1
            b_flags.append("B2:fmv_reclaim")

        # B3: buyers/sellers returning (2 consecutive bullish/bearish M30 candles)
        b3 = False
        if len(recent_bars) >= 2:
            bar_m1 = recent_bars.iloc[-2]
            bar_m0 = recent_bars.iloc[-1]
            if direction == "SHORT":  # trend=long, look for bullish candles
                b3 = (float(bar_m0["close"]) > float(bar_m0["open"])
                       and float(bar_m1["close"]) > float(bar_m1["open"]))
            else:  # trend=short, look for bearish candles
                b3 = (float(bar_m0["close"]) < float(bar_m0["open"])
                       and float(bar_m1["close"]) < float(bar_m1["open"]))
        if b3:
            b_count += 1
            b_flags.append("B3:trend_candles")

        # B4: reclaim candle (range > 0.5*ATR, strong close in trend direction)
        b4 = False
        if len(recent_bars) >= 1:
            rc = recent_bars.iloc[-1]
            rc_range = float(rc["high"]) - float(rc["low"])
            rc_close = float(rc["close"])
            rc_open  = float(rc["open"])
            rc_high  = float(rc["high"])
            rc_low   = float(rc["low"])
            if rc_range > atr * 0.5 and rc_range > 0:
                if direction == "SHORT":  # trend=long, bullish reclaim
                    b4 = rc_close > rc_open and (rc_close - rc_low) / rc_range > 0.6
                else:  # trend=short, bearish reclaim
                    b4 = rc_close < rc_open and (rc_high - rc_close) / rc_range > 0.6
        if b4:
            b_count += 1
            b_flags.append("B4:reclaim_candle")

        group_b = b_count >= 2

        # ==============================================================
        # EXIT DECISION: Group A AND Group B
        # ==============================================================
        if group_a and group_b:
            state["pullback_end_streak"] = state.get("pullback_end_streak", 0) + 1
        else:
            state["pullback_end_streak"] = 0
            return

        streak = state["pullback_end_streak"]
        a_str = f"A1={'Y' if a1 else 'n'} A2={'Y' if a2 else 'n'} A3={'Y' if a3 else 'n'}"
        b_str = " ".join(b_flags) if b_flags else "none"

        if streak < self._PULLBACK_END_PERSIST:
            ts = _ts()
            print(f"[{ts}] PULLBACK_END {direction}: streak={streak}/{self._PULLBACK_END_PERSIST}"
                  f"  pnl={pnl:+.1f}  {a_str}  B({b_count})=[{b_str}]")
            return

        # CONFIRMED: pullback is over, close all legs
        state["pullback_end_streak"] = 0
        ts = _ts()
        reason = f"{a_str} | B({b_count})=[{b_str}] | mfe={state['pullback_mfe']:.1f}"
        log.warning("PULLBACK_END EXIT: %s pnl=%+.1f | %s", direction, pnl, reason)
        print(f"[{ts}] PULLBACK_END EXIT {direction}: pnl={pnl:+.1f}pts")
        print(f"[{ts}]   {reason}")
        print(f"[{ts}]   entry_mode={entry_mode} daily_trend={daily_trend}")

        if self.dry_run:
            print(f"[{ts}]   [DRY RUN] WOULD close all legs")
            return

        if trade_rec is None:
            self._close_ticket(pos["ticket"], "PULLBACK_END", ts)
            return

        leg1_ticket = int(trade_rec.get("leg1_ticket", 0) or 0)
        leg2_ticket = int(trade_rec.get("leg2_ticket", 0) or 0)
        leg3_ticket = int(trade_rec.get("leg3_ticket", 0) or 0)
        for tkt in (leg1_ticket, leg2_ticket, leg3_ticket):
            if tkt > 0:
                self._close_ticket(tkt, "PULLBACK_END", ts)

    # ------------------------------------------------------------------
    # Decision point logging
    # ------------------------------------------------------------------

    _DECISION_LOG_INTERVAL = 30.0   # seconds between decision snapshots per ticket

    def _log_decision_point(
        self,
        pos:      dict,
        state:    dict,
        delta_4h: float,
        atr:      float,
        df_micro: Optional[pd.DataFrame],
        price:    Optional[float],
        now_mono: float,
    ) -> None:
        """
        Write a decision-point snapshot every 30 s per open ticket.

        Columns (tab-separated):
          timestamp | ticket | direction | entry | price | unrealized_pts
          | delta_4h | d4h_thresh | d4h_verdict
          | danger_scores | danger_verdict
          | cascade_move | cascade_thresh | cascade_verdict
          | shield_done | danger_streak
          | overall_verdict
        """
        ticket = pos["ticket"]
        last   = self._last_decision_log.get(ticket, 0.0)
        if now_mono - last < self._DECISION_LOG_INTERVAL:
            return
        self._last_decision_log[ticket] = now_mono

        direction   = pos["direction"]
        entry       = pos["entry"]
        thr         = self._thresholds
        _bl = thr.get("delta_4h_long_block")
        _bs = thr.get("trend_resumption_threshold")
        block_long  = float(_bl if _bl is not None else -600)
        block_short = float(_bs if _bs is not None else 0)
        shield_done = state.get("shield_done", False)
        danger_strk = state.get("danger_streak", 0)

        # --- Unrealized PnL in pts ---
        if price is not None:
            unreal_pts = (price - entry) if direction == "LONG" else (entry - price)
        else:
            unreal_pts = float("nan")

        # --- delta_4h verdict ---
        if direction == "LONG":
            d4h_flip   = delta_4h < block_long
            d4h_thresh = block_long
        else:
            d4h_flip   = delta_4h > block_short
            d4h_thresh = block_short
        d4h_verdict = "EXIT" if d4h_flip else ("WATCH" if abs(delta_4h - d4h_thresh) < 200 else "HOLD")

        # --- Danger scores verdict ---
        scores = _danger_scores_last_3bars(df_micro, direction) if df_micro is not None else []
        if len(scores) >= DANGER_BARS and all(s >= DANGER_THRESHOLD for s in scores[-DANGER_BARS:]):
            danger_verdict = "EXIT"
        elif scores and max(scores) >= DANGER_THRESHOLD:
            danger_verdict = "WATCH"
        else:
            danger_verdict = "HOLD"
        scores_str = "[" + ",".join(f"{s:.0f}" for s in scores) + "]"

        # --- Cascade move verdict ---
        hist = self._price_history.get(ticket, [])
        if len(hist) >= 2 and price is not None:
            price_start   = hist[0][1]
            move          = price - price_start
            cascade_move  = round(move, 2)
            cascade_thresh = round(CASCADE_ATR_FACTOR * atr, 2)
            against = (direction == "LONG" and move < -cascade_thresh) or \
                      (direction == "SHORT" and move > cascade_thresh)
            cascade_verdict = "EXIT" if against else (
                "WATCH" if abs(move) > cascade_thresh * 0.6 else "HOLD"
            )
        else:
            cascade_move  = float("nan")
            cascade_thresh = round(CASCADE_ATR_FACTOR * atr, 2)
            cascade_verdict = "HOLD"

        # --- Overall verdict ---
        verdicts = [d4h_verdict, danger_verdict, cascade_verdict]
        if "EXIT" in verdicts:
            overall = "EXIT"
        elif verdicts.count("WATCH") >= 2:
            overall = "WATCH"
        else:
            overall = "HOLD"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        line = (
            f"{ts}\t{ticket}\t{direction}\t{entry:.2f}\t"
            f"{price if price is not None else 'NA'}\t"
            f"{unreal_pts:+.2f}\t"
            f"d4h={delta_4h:+.0f}(thr={d4h_thresh:.0f})->{d4h_verdict}\t"
            f"danger={scores_str}->{danger_verdict}\t"
            f"cascade={cascade_move:+.2f}(thr={cascade_thresh:.1f})->{cascade_verdict}\t"
            f"shield={shield_done}\tstreak={danger_strk}\t"
            f"VERDICT={overall}"
        )
        self._dec_log_fh.write(line + "\n")

        # Mirror to stdout when not HOLD (noteworthy)
        if overall != "HOLD":
            print(f"[{_ts()}] DECISION ticket={ticket} {direction}  {line.split(chr(9), 6)[-1]}")

    # ------------------------------------------------------------------
    # Recalibration reminder
    # ------------------------------------------------------------------

    def _check_recalibration(self, trades: list[dict]) -> None:
        """
        Count confirmed live trades. Every 30 new trades: log reminder and
        update next_recalibration field in thresholds_gc.json.
        """
        live_count = sum(1 for t in trades if t.get("decision") == "CONFIRMED")
        interval   = 30
        if live_count - self._recal_last_count < interval:
            return

        self._recal_last_count = live_count
        msg = (f"RECALIBRATION DUE -- {live_count} live trades confirmed."
               f" Run threshold study again. (source: {self._thresholds.get('source', '?')})")
        log.warning(msg)
        print(f"[{_ts()}] *** {msg} ***")

        # Update JSON so next_recalibration reflects current count
        try:
            self._thresholds["next_recalibration"] = (
                f"after {live_count + interval} live trades"
            )
            with open(THRESHOLDS_PATH, "w", encoding="utf-8") as f:
                json.dump(self._thresholds, f, indent=2)
        except Exception as e:
            log.warning("could not update thresholds file: %s", e)

    # ------------------------------------------------------------------
    # Shared close helper
    # ------------------------------------------------------------------

    def _close_ticket(self, ticket: int, reason: str, ts: str) -> None:
        if self.dry_run:
            print(f"[{ts}]   [DRY RUN] WOULD CLOSE ticket={ticket} ({reason})")
            return
        result = self.executor.close_position(ticket)
        if result["success"]:
            print(f"[{ts}]   CLOSED ticket={ticket}  pnl={result['pnl']:+.2f}  ({reason})")
            log.info("CLOSED ticket=%d pnl=%.2f reason=%s", ticket, result["pnl"], reason)
        else:
            print(f"[{ts}]   CLOSE FAILED ticket={ticket}: {result.get('error')}  ({reason})")
            log.error("close_position ticket=%d failed: %s  reason=%s",
                      ticket, result.get("error"), reason)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]

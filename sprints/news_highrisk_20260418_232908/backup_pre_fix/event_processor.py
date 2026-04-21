#!/usr/bin/env python3
"""
C:\FluxQuantumAI\live\event_processor.py
APEX Event-Driven Pipeline -- Layer 2

# ============================================================
# DUAL-TIMEFRAME ARCHITECTURE (replaces ADR-001 M30-only)
#
# M5  = EXECUTION TIMEFRAME (timing)
#   · liq_top / liq_bot / fmv for proximity checks & entry triggers
#   · Expansion bar detection (GAMMA: close > high_prev on M5)
#   · Phase detection (CONTRACTION / EXPANSION / TREND on M30 — M5 is timing only)
#   · Expansion lines for SL/zone (last 80 M5 bars ≈ 6.7h)
#   · Entry price and ATR14 for GAMMA/DELTA
#
# M30 = MACRO BIAS TIMEFRAME (structure)
#   · TP2 structural targets (liq_top / liq_bot from M30 for wider target)
#   · m30_bias: "bullish" | "bearish" from level_detector
#   · maintained by m30_updater daemon, read via M30_BOXES_PATH
#
# D1 = DIRECTION FILTER ONLY (daily_jac_dir from gc_ats_features_v4.parquet)
#
# H4 levels are NEVER used here. Violation = idle in trending markets.
# ============================================================

Architecture (per ATS Docs literature -- Forthmann, Order Flow Modules 1-5):
  Order flow signals are time-critical. Iceberg events last <1 second.
  Polling is architecturally wrong. This module is event-driven.

Two triggers:
  1. MT5 tick loop    -- every 1s, reads MT5 price (~1ms), checks proximity to level
  2. Iceberg watcher  -- watchdog on JSONL file, fires instantly on new event

Gate is triggered ONLY when price is near a structural level.
Gate reads the microstructure file internally (~1.2s) -- acceptable since triggers are rare.

Metrics cache (delta_4h, ATR, bar_delta) is refreshed every 10s in a background
thread -- never blocks the monitoring loop.

DO NOT READ microstructure file in the fast loop. Use MT5 tick only for price.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ats_live_gate import ATSLiveGate
from mt5_executor import MT5Executor, _split_lots, SYMBOL, MAGIC
from live.operational_rules import OperationalRules
from live.tick_breakout_monitor import TickBreakoutMonitor
from live.kill_zones import kill_zone_label
from live.price_speed import PriceSpeedTracker
from live.level_detector import derive_m30_bias
from live import telegram_notifier as tg

# Hantec live executor -- optional, graceful fallback
try:
    from mt5_executor_hantec import MT5ExecutorHantec as _MT5ExecutorHantec
    _hantec_executor = _MT5ExecutorHantec()
except Exception:
    _hantec_executor = None

# V3 RL -- lazy import so the module loads even without sb3-contrib installed
try:
    from rl.v3_agent import V3Agent as _V3Agent
except ImportError:
    _V3Agent = None  # type: ignore

# DELTA trend momentum detector
try:
    from rl.v3_trend_momentum import TrendMomentumDetector as _TrendMomentumDetector
except ImportError:
    _TrendMomentumDetector = None  # type: ignore

# APEX News Gate -- blocks entries and closes positions around high-risk news
try:
    import sys as _sys
    _sys.path.insert(0, str(Path("C:/FluxQuantumAPEX/APEX GOLD/APEX_GC_News")))
    from apex_news_gate import news_gate as _news_gate
    log_news = logging.getLogger("apex.news_gate")
    log_news.info("ApexNewsGate loaded into EventProcessor")
except Exception as _news_ex:
    _news_gate = None  # type: ignore
    logging.getLogger("apex.event").warning(
        "ApexNewsGate not available -- trading without news gate: %s", _news_ex
    )

# Grenadier Stat-Guardrails (Sprint 1 -- The Shield)
try:
    from grenadier_guardrail import update_guardrail as _update_guardrail
    from grenadier_guardrail import get_guardrail_status as _get_guardrail_status
    _guardrail_available = True
    logging.getLogger("apex.event").info("StatGuardrail loaded (Grenadier Sprint 1)")
except Exception as _gr_ex:
    _update_guardrail    = None  # type: ignore
    _get_guardrail_status = None  # type: ignore
    _guardrail_available  = False
    logging.getLogger("apex.event").warning(
        "StatGuardrail not available -- trading without guardrails: %s", _gr_ex
    )

# Grenadier Z-Score Defense Mode (Sprint 3 -- Hard Shield)
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))
    from inference.anomaly_scorer import GrenadierDefenseMode as _GrenadierDefenseMode
    _defense_mode = _GrenadierDefenseMode()
    _defense_available = True
    logging.getLogger("apex.event").info("GrenadierDefenseMode loaded (Sprint 3)")
except Exception as _dm_ex:
    _defense_mode      = None  # type: ignore
    _defense_available = False
    logging.getLogger("apex.event").warning(
        "GrenadierDefenseMode not available -- trading without Z-Score shield: %s", _dm_ex
    )

# AnomalyForge V2 -- DISABLED (2026-04-13)
# V2 LSTM-Autoencoder descartado (MSE ratio 0.987x, sem discriminação win/loss).
# V3 Transformer-AE testado mas não convergiu (MSE 6 ordens de magnitude acima do threshold).
# Novo spec em desenvolvimento. GrenadierDefenseMode (Z-score) + Layer 1 rules continuam activos.
_anomaly_forge_v2           = None  # type: ignore
_anomaly_forge_v2_available = False


def _build_af_features_from_series(row, ts_dt) -> dict:
    """
    Constrói fluxfox_features dict a partir da última row do microstructure CSV.
    Mapeia ~12 das 26 features SCHEMA_FLUXFOX_V2. Features ausentes -> imputação
    com training mean no AnomalyForgeProvider (_extract_features).
    """
    def _fv(key):
        try:
            v = row.get(key) if hasattr(row, "get") else getattr(row, key, None)
            return float(v) if v is not None and str(v) != "" else None
        except (TypeError, ValueError):
            return None

    feats: dict = {}

    ab = _fv("absorption_ratio")
    if ab is not None:
        feats["aggressor_volume_absorbed"] = ab        # F07
        feats["book_depth_recovery_rate"]  = ab / 100  # F09

    di = _fv("dom_imbalance")
    if di is not None:
        feats["dom_imbalance_at_level"] = di           # F08

    tps = _fv("trades_per_second")
    if tps is not None:
        feats["trade_intensity"] = tps                 # F10

    hr = ts_dt.hour
    feats["session_label"]   = 2.0 if 14 <= hr < 21 else (1.0 if 8 <= hr < 14 else 0.0)  # F11
    feats["hour_of_day"]     = float(hr)               # F11a
    feats["minute_of_hour"]  = float(ts_dt.minute)     # F11b

    cd = _fv("cumulative_delta")
    if cd is not None:
        feats["cumulative_absorbed_volume"] = cd       # F15

    bd = _fv("bar_delta")
    if bd is not None:
        feats["underflow_volume"] = bd                 # F19
        feats["ofi_rate"]         = bd                 # F21

    vps = _fv("volume_per_second")
    mp  = _fv("mid_price")
    if vps is not None and mp is not None and mp > 0:
        feats["vot"] = vps * mp / 1000.0               # F22

    sp = _fv("spread")
    if sp is not None and sp > 0:
        feats["inst_volatility_ticks"] = sp / 0.1     # F23

    return feats


# News Release Feature Flag (APEX_GC_News -- ReleaseMonitor)
# NEWS_STATE.check() returns is_news_active + gold_signal + surprise_label.
# Used as PONTO -1: veto if is_news_active AND confluence with signal direction.
try:
    import sys as _sys2
    _sys2.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD")))
    from APEX_GC_News.release_monitor import NEWS_STATE as _NEWS_STATE
    _news_state_available = True
    logging.getLogger("apex.event").info("NEWS_STATE feature flag loaded (ReleaseMonitor)")
except Exception as _ns_ex:
    _NEWS_STATE           = None  # type: ignore
    _news_state_available = False
    logging.getLogger("apex.event").warning(
        "NEWS_STATE not available -- trading without news flag: %s", _ns_ex
    )

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MICRO_DIR       = Path("C:/data/level2/_gc_xcec")
ICE_DIR         = Path("C:/data/iceberg")
THRESHOLDS_PATH = Path("C:/FluxQuantumAI/config/settings.json")
TRADES_CSV      = Path("C:/FluxQuantumAI/logs/trades.csv")
M5_BOXES_PATH   = Path("C:/data/processed/gc_m5_boxes.parquet")
M30_BOXES_PATH  = Path("C:/data/processed/gc_m30_boxes.parquet")
FEATURES_V4_PATH = Path("C:/data/processed/gc_ats_features_v4.parquet")

# ── Single Source of Truth (APEX Stabilization Plan) ─────────────────
DECISION_LIVE_PATH = Path("C:/FluxQuantumAI/logs/decision_live.json")
DECISION_LOG_PATH  = Path("C:/FluxQuantumAI/logs/decision_log.jsonl")
SERVICE_STATE_PATH = Path("C:/FluxQuantumAI/logs/service_state.json")
CONTINUATION_LOG   = Path("C:/FluxQuantumAI/logs/continuation_trades.jsonl")

# GAMMA momentum stacking constants
GAMMA_MIN_RR      = 1.5   # minimum TP2/SL R:R (condition 4: "reasonable risk window")
GAMMA_TP1_FACTOR  = 1.0   # TP1 = entry ± ATR14 x factor  (proxy for next expansion line)

# DELTA trend momentum re-alignment constants
DELTA_MIN_RR             = 1.5        # minimum TP2/SL R:R
DELTA_DIRECTION_LOCK_S   = 4 * 30 * 60  # 4 M30 bars = 2h cooldown same direction

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GATE_COOLDOWN_S   = 60.0   # restored from 5s -- minimum seconds between any gate evaluations
DIRECTION_LOCK_S  = 300.0  # per-direction lock after CONFIRMED order (5 min)
NEAR_ATR_FACTOR   = 1.0    # abs(price - level) <= ATR * factor -> NEAR  (1x ATR = ~29pts)
NEAR_FLOOR_PTS    = 5.0    # minimum proximity band regardless of ATR
METRICS_REFRESH_S = 10.0   # background metrics refresh interval
OFFSET_REFRESH_S  = 300.0  # GC/XAUUSD offset refresh interval (5 min)

log = logging.getLogger("apex.event")

# ---------------------------------------------------------------------------
# MT5
# ---------------------------------------------------------------------------
_mt5 = None
try:
    import MetaTrader5 as _m
    if _m.initialize():
        _mt5 = _m
except Exception:
    pass

_mt5_last_fail: float = 0.0
_MT5_RECONNECT_INTERVAL = 60.0   # retry MT5 init at most once per minute


def _mt5_price() -> Optional[float]:
    global _mt5, _mt5_last_fail
    import time as _time

    if _mt5 is None:
        # Attempt reconnect at most once per _MT5_RECONNECT_INTERVAL seconds
        now = _time.monotonic()
        if now - _mt5_last_fail >= _MT5_RECONNECT_INTERVAL:
            _mt5_last_fail = now
            try:
                import MetaTrader5 as _m
                if _m.initialize():
                    _mt5 = _m
                    log.info("MT5 reconnected successfully")
            except Exception as _e:
                log.warning("MT5 reconnect failed: %s", _e)
        if _mt5 is None:
            return None

    try:
        tick = _mt5.symbol_info_tick(SYMBOL)
        if tick:
            return round((tick.ask + tick.bid) / 2.0, 2)
        # Tick returned None -- MT5 session may have dropped; force reconnect next cycle
        _mt5 = None
        _mt5_last_fail = 0.0   # allow immediate retry next call
    except Exception:
        _mt5 = None
        _mt5_last_fail = 0.0
    return None


def _compute_offset(gc_mid: float, xau_mid: float) -> float:
    """offset = GC_mid - XAUUSD_mid  (futures carry premium, ~31 pts)."""
    return round(gc_mid - xau_mid, 3)


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
        "trend_resumption_threshold": 0,
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
    except FileNotFoundError:
        pass
    except Exception as e:
        log.error("thresholds load error: %s", e)
    return defaults


def _iso_to_epoch(iso_str: str) -> float:
    """Convert ISO-8601 timestamp string to Unix epoch float. Returns 0.0 on error."""
    try:
        return datetime.fromisoformat(iso_str).timestamp()
    except Exception:
        return 0.0


def _mt5_margin_level() -> float:
    """Return current margin level % from MT5. Returns 9999 if unavailable (= allow)."""
    if _mt5 is None:
        return 9999.0
    try:
        info = _mt5.account_info()
        if info and info.margin > 0:
            return round(info.margin_level, 1)
    except Exception:
        pass
    return 9999.0


def _load_trades_ep() -> list[dict]:
    """Load trades.csv to inspect open leg state (awaiting TP1 check)."""
    if not TRADES_CSV.exists():
        return []
    try:
        with open(TRADES_CSV, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Watchdog handlers (lightweight -- just set flags / tail text)
# ---------------------------------------------------------------------------

class _MicroHandler(FileSystemEventHandler):
    def __init__(self, processor: "EventProcessor"):
        self._proc = processor

    def on_modified(self, event):
        if "microstructure" in Path(event.src_path).name:
            self._proc._micro_dirty.set()


class _IcebergHandler(FileSystemEventHandler):
    def __init__(self, processor: "EventProcessor"):
        self._proc = processor

    def on_modified(self, event):
        path = Path(event.src_path)
        if path.suffix == ".jsonl" and "GC_XCEC" in path.name:
            for ev in self._proc._tail_jsonl(path):
                self._proc._on_iceberg_event(ev)


# ---------------------------------------------------------------------------
# Protection Advice — normalised observability for ICEBERG & ANOMALY
# ---------------------------------------------------------------------------

def _build_protection_advice(
    source: str,
    alignment: str = "UNKNOWN",
    severity: str = "NONE",
    flow_relation: str = "UNKNOWN",
    entry_action: str = "UNKNOWN",
    position_action: str = "UNKNOWN",
    reason: str = "",
    detected: bool = False,
    side: str = "UNKNOWN",
    confidence: float | None = None,
    refills: int | None = None,
) -> dict:
    """
    Build a normalised protection advice dict.
    Rule-based only — no ML confidence. shadow_only=True always in this phase.
    entry_action is OBSERVE or ALLOW — never BLOCK/REDUCE (backtest proved filtering hurts PF).
    position_action uses directional exits: EXIT_LONG / EXIT_SHORT / EXIT_ALL.
    """
    return {
        "detected": detected,
        "side": side,
        "alignment": alignment,
        "severity": severity,
        "flow_relation": flow_relation,
        "entry_action": entry_action,
        "position_action": position_action,
        "reason": reason,
        "confidence": confidence,
        "refills": refills,
        "shadow_only": True,
        "source": source,
        "rule_based": True,
    }


def _default_protection() -> dict:
    """Return the default protection block with both sources at UNKNOWN/default."""
    return {
        "anomaly": _build_protection_advice(
            "DEFENSE_MODE", entry_action="ALLOW", position_action="HOLD"
        ),
        "iceberg": _build_protection_advice("ICEBERG"),
    }


# ---------------------------------------------------------------------------
# EventProcessor
# ---------------------------------------------------------------------------

class EventProcessor:
    """
    Event-driven gate trigger.

    Parameters
    ----------
    liq_top, liq_bot : float
        Current structural levels (daily). Updated at startup.
    dry_run : bool
        Evaluate gate, print result -- never send MT5 orders.
    execute : bool
        Send real MT5 orders on GO.
    lot_size : float
        Total lot size per trade (split 40/40/20 into 3 legs).
    sl_pts, tp1_pts, tp2_pts : float
        Fixed SL/TP distances in points.
    """

    def __init__(
        self,
        liq_top: float,
        liq_bot: float,
        dry_run: bool = True,
        execute: bool = False,
        lot_size: float = 0.02,
        sl_pts: float = 20.0,
        tp1_pts: float = 20.0,
        tp2_pts: float = 50.0,
        v3_agent=None,        # Optional[V3Agent] -- passed from run_live.py
        feed_monitor=None,    # Optional[FeedHealthMonitor] -- gate suspended when FEED_DEAD
        box_high: float = None,   # M5 box ceiling (GC space) for V1 TRENDING zone check
        box_low: float = None,    # M5 box floor  (GC space) for V1 TRENDING zone check
        daily_trend: str = "unknown",  # "long" | "short" | "unknown"
    ):
        # liq_top / liq_bot / box_high / box_low are in GC (Quantower) price space.
        # They are converted to MT5 XAUUSD space at runtime via _gc_xauusd_offset.
        self.liq_top_gc   = liq_top
        self.liq_bot_gc   = liq_bot
        self.box_high_gc  = box_high
        self.box_low_gc   = box_low
        self.daily_trend  = daily_trend

        # -- Dual-timeframe macro state ---------------------------------------
        # M30 bias: "bullish" | "bearish" | "unknown"  (set by _refresh_levels)
        # Used as hard gate: M5 signals contra-M30 are rejected before gate.check()
        self.m30_bias       = "unknown"
        self.m30_bias_confirmed = False
        self.provisional_m30_bias = "unknown"
        # M30 structural levels in GC space (for border alignment check)
        self.m30_liq_top_gc: float | None = None
        self.m30_liq_bot_gc: float | None = None
        # M30 structural levels in MT5 space (updated by _refresh_offset)
        self.m30_liq_top:    float | None = None
        self.m30_liq_bot:    float | None = None
        # --------------------------------------------------------------------

        self.dry_run      = dry_run
        self.execute      = execute
        self.lot_size     = lot_size
        self.sl_pts       = sl_pts
        self.tp1_pts      = tp1_pts
        self.tp2_pts      = tp2_pts

        # Dynamic lot sizing (Sprint 4 C) -- session-based lots with iceberg bonus
        _cfg = self._load_lot_config()
        self._dyn_lots_enabled = _cfg["enabled"]
        self._lot_by_session   = _cfg["lots"]
        self._lot_ice_bonus    = _cfg["bonus"]

        # MT5-equivalent space (set after offset computed)
        self.liq_top  = liq_top   # updated in start() / _refresh_offset()
        self.liq_bot  = liq_bot   # updated in start() / _refresh_offset()
        self.box_high = box_high  # updated in start() / _refresh_offset()
        self.box_low  = box_low   # updated in start() / _refresh_offset()

        self.feed_monitor  = feed_monitor   # FIX 2: None = no feed health check

        # Price Speed (Displacement) tracker -- feeds off the MT5 tick loop
        self._speed_tracker = PriceSpeedTracker(threshold_pts_per_sec=0.8)

        self.gate          = ATSLiveGate()
        self.executor      = MT5Executor()
        self.executor_live = _hantec_executor   # None if not available
        self.ops      = OperationalRules()
        self.ops.log_status()
        self.v3_agent      = v3_agent   # None when disabled
        self._last_ice_event: Optional[dict] = None   # most recent iceberg event (for V3)

        # Thread-safe state
        self._lock          = threading.Lock()
        self._jsonl_lock    = threading.Lock()   # guards _jsonl_pos reads (prevent double-read)
        self._last_trigger  = 0.0          # epoch of last gate check (legacy, used by GAMMA/DELTA)
        self._last_trigger_by_dir: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0}  # per-direction cooldown
        self._micro_dirty   = threading.Event()
        self._jsonl_pos: dict[str, int] = {}
        self._running       = False
        self._last_gamma_bar: Optional[pd.Timestamp] = None  # dedup: last GAMMA trigger bar time
        self._last_delta_bar: Optional[pd.Timestamp] = None  # dedup: last DELTA trigger bar time
        self._trend_momentum_detector = (
            _TrendMomentumDetector() if _TrendMomentumDetector is not None else None
        )
        # Per-direction lock: prevents opening same direction within DIRECTION_LOCK_S
        # after a CONFIRMED order (regardless of whether position is still open).
        # Prevents rapid re-entry after breakeven SL hit.
        self._direction_lock_until: dict[str, float] = {}   # direction -> monotonic epoch
        self._macro_ctx_last_refresh: float = 0.0
        self._macro_ctx_refresh_needed: bool = True

        # Sprint 8: Trade cooldown -- prevents rapid re-entry at same/any level
        self._last_trade_time: float = 0.0          # monotonic epoch of last confirmed trade
        self._last_trade_level: float = 0.0         # price level of last trade
        self._last_trade_direction: str = ""        # direction of last trade

        # Dwell Time abort -- CAL-LEVEL-TOUCH 2026-04-10
        # If price stays within NEAR band for > DWELL_ABORT_S without reacting
        # (no move >= DWELL_MOVE_THR pts in the signal direction), the touch is
        # classified as "stale absorption" and the gate is suppressed until
        # price LEAVES the band and re-enters (new fresh touch).
        #
        # Calibration source: 9-month M1 study -- success median dwell=2bars,
        # failure median dwell=5bars. Threshold set at P75 of success=5bars≈5min.
        DWELL_ABORT_S   = float(
            _load_thresholds().get("dwell_abort_s", 300.0)   # 5 min default (CAL-LEVEL-TOUCH)
        )
        DWELL_MOVE_THR  = float(
            _load_thresholds().get("dwell_move_thr_pts", 5.0) # pts move needed to confirm reaction
        )
        self._dwell_abort_s  = DWELL_ABORT_S
        self._dwell_move_thr = DWELL_MOVE_THR
        # Per-level state: level_type -> {first_ts, ref_price, stale}
        self._dwell_state: dict[str, dict] = {}

        # Tick-level breakout & JAC monitor -- updates liq_top_gc/liq_bot_gc in real-time
        self._tick_breakout = TickBreakoutMonitor(self)

        # GC/XAUUSD offset: GC_mid - XAUUSD_mid  (~31 pts, refreshed every 5 min via MT5 calibration)
        # Default 31.0 so monitoring works immediately even if MT5 never responds.
        self._gc_xauusd_offset: float = 31.0
        self._offset_ts: float        = 0.0   # monotonic ts of last offset sample

        # Metrics cache -- updated by background thread, read by fast loop
        self._metrics: dict = {
            "delta_4h":  0.0,
            "atr":       20.0,
            "bar_delta": 0.0,
            "gc_mid":    0.0,   # latest GC price from micro file
        }

        # Data-driven thresholds -- loaded from thresholds_gc.json at startup
        self._thresholds = _load_thresholds()
        self._live_trade_count = 0   # count of CONFIRMED trades for recalibration

        # Startup metrics cooldown -- block ALL trades for 2 minutes after startup.
        # At startup, _refresh_metrics() loads stale delta_4h from the microstructure
        # file (which may be hours old or from the previous session). The metrics_loop
        # thread refreshes every 10s, so within ~10-20s metrics will be live. However,
        # the delta_4h value requires the microstructure file to be current; after a
        # service restart, the first valid snapshot may take up to 60s (m30_updater cycle).
        # 120s cooldown ensures metrics are stable before any gate check can pass.
        STARTUP_COOLDOWN_S = 120
        self._startup_cooldown_until: float = time.monotonic() + STARTUP_COOLDOWN_S
        print(
            f"[STARTUP] Metrics cooldown active for {STARTUP_COOLDOWN_S}s"
            f" -- no trades will open until metrics stabilise"
        )

        # Protection advice — transient cache, zeroed at each gate cycle.
        # Single source of truth remains decision_live.json / decision_log.jsonl.
        self._cycle_protection: dict = _default_protection()

    # ------------------------------------------------------------------
    # Dynamic lot sizing (Sprint 4 C)
    # ------------------------------------------------------------------

    @staticmethod
    def _load_lot_config() -> dict:
        """Load lot sizing config from settings.json."""
        cfg = {"enabled": False,
               "lots": {"ASIAN": [0.01, 0.01, 0.01],
                        "LONDON": [0.02, 0.02, 0.01],
                        "NY": [0.03, 0.02, 0.01]},
               "bonus": [0.01, 0.01, 0.00]}
        try:
            with open("C:/FluxQuantumAI/config/settings.json", "r", encoding="utf-8") as f:
                s = json.load(f)
            cfg["enabled"] = bool(s.get("dynamic_lot_sizing_enabled", False))
            if "lot_asian" in s:
                cfg["lots"]["ASIAN"] = [float(x) for x in s["lot_asian"]]
            if "lot_london" in s:
                cfg["lots"]["LONDON"] = [float(x) for x in s["lot_london"]]
            if "lot_ny" in s:
                cfg["lots"]["NY"] = [float(x) for x in s["lot_ny"]]
            if "lot_iceberg_aligned_bonus" in s:
                cfg["bonus"] = [float(x) for x in s["lot_iceberg_aligned_bonus"]]
        except Exception:
            pass
        return cfg

    def _compute_session_lots(self, ice_aligned: bool) -> Optional[list]:
        """
        Compute [leg1, leg2, leg3] based on current UTC session + iceberg alignment.
        Returns None if dynamic lot sizing is disabled (fallback to fixed lot_size).
        """
        if not self._dyn_lots_enabled:
            return None

        hr = datetime.now(timezone.utc).hour
        if 14 <= hr < 21:
            session = "NY"
        elif 8 <= hr < 14:
            session = "LONDON"
        else:
            session = "ASIAN"

        base = list(self._lot_by_session.get(session, [0.02, 0.02, 0.01]))

        if ice_aligned:
            bonus = self._lot_ice_bonus
            final = [
                round(base[0] + bonus[0], 2),
                round(base[1] + bonus[1], 2),
                base[2],  # Runner NEVER changes
            ]
        else:
            final = base

        total = sum(final)
        log.info("[LOT_SIZING] session=%s iceberg=%s base=%s bonus=%s final=%s total=%.2f",
                 session, "ALIGNED" if ice_aligned else "NONE", base,
                 self._lot_ice_bonus if ice_aligned else [0, 0, 0], final, total)
        return final

    # ------------------------------------------------------------------
    # Single Source of Truth: decision_live.json + decision_log.jsonl
    # ------------------------------------------------------------------

    def _write_decision(self, decision_data: dict) -> None:
        """
        Write gate decision to:
          1. decision_live.json  — latest state (atomic overwrite)
          2. decision_log.jsonl  — append-only audit trail
        Called after every gate check (GO or BLOCK).
        """
        # Add decision_id and created_at
        decision_data["decision_id"] = str(uuid.uuid4())[:8]
        decision_data["created_at"] = datetime.now(timezone.utc).isoformat()

        # 1. decision_live.json (atomic: tmp -> rename)
        DECISION_LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(decision_data, f, indent=2, default=str)
            tmp.replace(DECISION_LIVE_PATH)
        except Exception as e:
            log.error("decision_live.json write failed: %s", e)

        # 2. decision_log.jsonl (append)
        try:
            with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(decision_data, default=str) + "\n")
        except Exception as e:
            log.error("decision_log.jsonl append failed: %s", e)

    def _read_d1h4_bias_shadow(self) -> dict:
        """Read D1/H4 bias from gc_d1h4_bias.json (FASE 4a shadow). No behavioral impact."""
        _bias_path = Path("C:/FluxQuantumAI/logs/gc_d1h4_bias.json")
        try:
            if _bias_path.exists():
                with open(_bias_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {
                    "d1_jac_dir":     data.get("d1_jac_dir", "?"),
                    "h4_jac_dir":     data.get("h4_jac_dir", "?"),
                    "direction":      data.get("bias_direction", "?"),
                    "strength":       data.get("bias_strength", "?"),
                    "h4_stale":       data.get("data_freshness", {}).get("h4_stale", True),
                    "d1_stale":       data.get("data_freshness", {}).get("d1_stale", True),
                    "last_closed_h4": data.get("last_closed_h4_ts", "?"),
                    "last_closed_d1": data.get("last_closed_d1_ts", "?"),
                }
        except Exception:
            pass
        return {"d1_jac_dir": "?", "h4_jac_dir": "?", "direction": "?", "strength": "?"}

    def _write_service_state(self) -> None:
        """
        Write service health heartbeat to service_state.json.
        Called every 30s from independent heartbeat thread (NOT tick loop).
        Runs even when feed is DEAD or system is in cooldown.
        """
        now = datetime.now(timezone.utc)
        gc_mid = self._metrics.get("gc_mid", 0.0)
        offset = self._gc_xauusd_offset

        # M5 parquet age
        m5_age_s = -1.0
        try:
            if M5_BOXES_PATH.exists():
                m5_age_s = round(time.time() - M5_BOXES_PATH.stat().st_mtime, 1)
        except Exception:
            pass

        # M30 parquet age
        m30_age_s = -1.0
        try:
            if M30_BOXES_PATH.exists():
                m30_age_s = round(time.time() - M30_BOXES_PATH.stat().st_mtime, 1)
        except Exception:
            pass

        # M1 parquet staleness check (critical blocker if stale)
        m1_stale = False
        m1_age_s = -1.0
        m1_last_bar = "?"
        try:
            _m1p = Path("C:/data/processed/gc_ohlcv_l2_joined.parquet")
            if _m1p.exists():
                m1_age_s = round(time.time() - _m1p.stat().st_mtime, 1)
                m1_stale = m1_age_s > 300  # stale if not written in 5 min
        except Exception:
            m1_stale = True

        # Feed age
        feed_age_s = -1.0
        try:
            today = now.strftime("%Y-%m-%d")
            micro_path = Path(f"C:/data/level2/_gc_xcec/microstructure_{today}.csv.gz")
            if micro_path.exists():
                feed_age_s = round(time.time() - micro_path.stat().st_mtime, 1)
        except Exception:
            pass

        # Feed status
        if feed_age_s < 0:
            feed_status = "NO_FILE"
        elif feed_age_s < 120:
            feed_status = "OK"
        else:
            feed_status = "DEAD"

        def _safe_round(val, n=2):
            return round(val, n) if val is not None else None

        _exec_live = getattr(self, "executor_live", None)

        state = {
            "timestamp": now.isoformat(),
            "last_heartbeat_at": now.isoformat(),
            "status": "ALIVE",
            "pid": os.getpid(),
            "running": self._running,
            "gc_price": _safe_round(gc_mid),
            "mt5_price": _safe_round(gc_mid - offset) if gc_mid > 0 else 0.0,
            "gc_mt5_offset": _safe_round(offset),
            "liq_top_gc": _safe_round(getattr(self, "liq_top_gc", None)),
            "liq_bot_gc": _safe_round(getattr(self, "liq_bot_gc", None)),
            "liq_top_mt5": _safe_round(getattr(self, "liq_top", None)),
            "liq_bot_mt5": _safe_round(getattr(self, "liq_bot", None)),
            "m30_bias": getattr(self, "m30_bias", "unknown"),
            "m30_bias_confirmed": getattr(self, "m30_bias_confirmed", False),
            "provisional_m30_bias": getattr(self, "provisional_m30_bias", "unknown"),
            "daily_trend": getattr(self, "daily_trend", "unknown"),
            "delta_4h": _safe_round(self._metrics.get("delta_4h", 0), 0),
            "atr_m30": _safe_round(self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 0))),
            # Read cached phase from tick loop — do NOT recalculate here (race condition fix)
            "phase": getattr(self, "_last_phase", self._phase_current),
            "feed_status": feed_status,
            "feed_age_s": feed_age_s,
            "last_tick_at": getattr(self, "_last_tick_at", None),
            "last_gate_at": getattr(self, "_last_gate_at", None),
            "m5_age_s": m5_age_s,
            "m5_ok": m5_age_s >= 0 and m5_age_s < 120,
            "m30_age_s": m30_age_s,
            "m30_ok": m30_age_s >= 0 and m30_age_s < 120,
            "m1_age_s": m1_age_s,
            "m1_stale": m1_stale,
            "m1_stale_critical": m1_stale,  # BLOCKER: if True, M30/M5/bias are unreliable
            "near_level_source": getattr(self, "_near_level_source", ""),
            "defense_tier": self._metrics.get("defense_tier", "NORMAL"),
            "stress_direction": self._metrics.get("stress_direction", "HOLD"),
            "d1h4_bias": self._read_d1h4_bias_shadow(),
            "mt5_robo_connected": getattr(getattr(self, "executor", None), "connected", False),
            "mt5_hantec_connected": getattr(_exec_live, "connected", False) if _exec_live else False,
        }

        SERVICE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = SERVICE_STATE_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
            tmp.replace(SERVICE_STATE_PATH)
        except Exception as e:
            log.error("service_state.json write failed: %s", e)

    def _start_heartbeat_thread(self) -> None:
        """Start independent heartbeat thread. Runs every 30s regardless of feed/tick state."""
        def _heartbeat_loop():
            log.error("HEARTBEAT_LOOP_ENTERED pid=%d", os.getpid())
            print(f"[HEARTBEAT] Loop entered pid={os.getpid()}", flush=True)
            while self._running:
                try:
                    self._write_service_state()
                    log.error("SERVICE_STATE_WRITE_OK path=%s", SERVICE_STATE_PATH)
                except Exception as _hbe:
                    log.error("SERVICE_STATE_WRITE_FAIL: %s", _hbe)
                # Telegram health check (anti-spam inside: 15 min or state change)
                try:
                    tg.notify_health_check()
                except Exception as _tge:
                    log.debug("Telegram health check failed: %s", _tge)
                time.sleep(30)
        t = threading.Thread(target=_heartbeat_loop, name="heartbeat", daemon=True)
        t.start()
        log.error("HEARTBEAT_THREAD_STARTED pid=%d", os.getpid())
        print(f"[HEARTBEAT] Thread started pid={os.getpid()}", flush=True)

    def _build_decision_dict(
        self,
        direction: str,
        price: float,
        decision,
        trigger: str = "ALPHA",
        expansion_lines_mt5: list = None,
        sl: float = 0.0,
        tp1: float = 0.0,
        tp2: float = 0.0,
        lots: list = None,
    ) -> dict:
        """Build the decision dict matching APEX_Stabilization_Plan schema."""
        gc_mid = self._metrics.get("gc_mid", 0.0)
        offset = self._gc_xauusd_offset
        atr_m30 = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 0))

        # Determine session
        hr = datetime.now(timezone.utc).hour
        if 14 <= hr < 21:
            session = "NY"
        elif 8 <= hr < 14:
            session = "LONDON"
        else:
            session = "ASIAN"

        ice = decision.iceberg
        mom = decision.momentum

        _action = "GO" if decision.go else "BLOCK"
        _side = "BUY" if direction == "LONG" else "SELL"
        _intent = f"ENTRY_{direction}"
        _ice = {
            "detected": bool(getattr(ice, "detected", False)),
            "side": "BUY" if str(getattr(ice, "sweep_dir", "")).upper() in ("BUY", "BID", "LONG") else (
                "SELL" if str(getattr(ice, "sweep_dir", "")).upper() in ("SELL", "ASK", "SHORT") else "UNKNOWN"
            ),
            "alignment": (
                "ALIGNED" if bool(getattr(ice, "aligned", False))
                else ("OPPOSED" if getattr(ice, "detected", False) else "UNKNOWN")
            ),
            "severity": (
                "CRITICAL" if float(getattr(ice, "score", 0.0) or 0.0) >= 0.9
                else "HIGH" if float(getattr(ice, "score", 0.0) or 0.0) >= 0.75
                else "MEDIUM" if float(getattr(ice, "score", 0.0) or 0.0) >= 0.6
                else "LOW" if getattr(ice, "detected", False) else "NONE"
            ),
            "confidence": round(float(getattr(ice, "score", 0.0) or 0.0), 4),
            "refills": int(getattr(ice, "refills", 0) or 0),
            "entry_action": self._cycle_protection.get("iceberg", {}).get("entry_action", "UNKNOWN"),
            "position_action": self._cycle_protection.get("iceberg", {}).get("position_action", "UNKNOWN"),
        }

        d = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price_mt5": round(price, 2),
            "price_gc": round(gc_mid, 2),
            "gc_mt5_offset": round(offset, 2),
            "context": {
                "phase": self._get_current_phase(),
                "daily_trend": self.daily_trend,
                "m30_bias": self.m30_bias,
                "m30_bias_confirmed": self.m30_bias_confirmed,
                "provisional_m30_bias": self.provisional_m30_bias,
                "m30_box_mt5": [round(self.box_low, 2), round(self.box_high, 2)] if self.box_high else None,
                "m30_fmv_mt5": None,
                "m30_atr14": round(atr_m30, 2),
                "liq_top_mt5": round(self.liq_top, 2),
                "liq_bot_mt5": round(self.liq_bot, 2),
                "liq_top_gc": round(self.liq_top_gc, 2),
                "liq_bot_gc": round(self.liq_bot_gc, 2),
                "session": session,
                "delta_4h": round(self._metrics.get("delta_4h", 0), 0),
            },
            "trigger": {
                "type": trigger,
                "level_type": "liq_top" if direction == "SHORT" else "liq_bot",
                "level_price_mt5": round(self.liq_top if direction == "SHORT" else self.liq_bot, 2),
                "proximity_pts": round(abs(price - (self.liq_top if direction == "SHORT" else self.liq_bot)), 1),
                "near_level_source": self._near_level_source or "unknown",
            },
            "gates": {
                "v1_zone": {"status": "PASS" if decision.go or "V1" not in decision.reason else "BLOCK",
                            "reason": ""},
                "v2_l2": {"status": decision.v4_status if decision.v4_status != "UNKNOWN" else "N/A",
                           "score": decision.l2_entry_score},
                "v3_momentum": {"status": mom.status.upper(),
                                "delta_4h": round(mom.delta_4h, 0),
                                "score": mom.score},
                "v4_iceberg": {"status": decision.v4_status,
                               "score": ice.score,
                               "type": ice.primary_type if ice.detected else "none",
                               "aligned": ice.aligned if ice.detected else None},
            },
            "decision": {
                "action": _action,
                "direction": direction,
                "action_side": _side,
                "trade_intent": _intent,
                "message_semantics_version": "v1_canonical",
                "reason": decision.reason,
                "total_score": decision.total_score,
                "execution": {
                    "overall_state": "NOT_ATTEMPTED",
                    "attempted": False,
                    "brokers": [],
                },
            },
            "expansion_lines_mt5": expansion_lines_mt5 or [],
            "micro_atr_proxy": round(self._metrics.get("atr", 0), 2),
            "protection": dict(self._cycle_protection),
            "anomaly": dict(self._cycle_protection.get("anomaly", {})),
            "iceberg": _ice,
        }

        # Add execution details for GO
        if decision.go and sl > 0:
            d["decision"]["sl"] = round(sl, 2)
            d["decision"]["tp1"] = round(tp1, 2)
            d["decision"]["tp2"] = round(tp2, 2)
            d["decision"]["lots"] = lots or []

        return d

    def _write_stale_block_decision(self, direction: str, price: float, reason: str, trigger: str) -> None:
        """Write canonical decision snapshot when gate is blocked by stale structure."""
        gc_mid = self._metrics.get("gc_mid", 0.0)
        offset = self._gc_xauusd_offset
        atr_m30 = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 0))
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price_mt5": round(price, 2),
            "price_gc": round(gc_mid, 2),
            "gc_mt5_offset": round(offset, 2),
            "context": {
                "phase": self._get_current_phase(),
                "daily_trend": self.daily_trend,
                "m30_bias": self.m30_bias,
                "m30_bias_confirmed": self.m30_bias_confirmed,
                "provisional_m30_bias": self.provisional_m30_bias,
                "m30_box_mt5": [round(self.box_low, 2), round(self.box_high, 2)] if self.box_high else None,
                "m30_fmv_mt5": None,
                "m30_atr14": round(atr_m30, 2),
                "liq_top_mt5": round(self.liq_top, 2),
                "liq_bot_mt5": round(self.liq_bot, 2),
                "liq_top_gc": round(self.liq_top_gc, 2),
                "liq_bot_gc": round(self.liq_bot_gc, 2),
                "delta_4h": round(self._metrics.get("delta_4h", 0), 0),
                "structure_stale": True,
                "structure_stale_reason": reason,
            },
            "trigger": {
                "type": trigger,
                "level_type": "liq_top" if direction == "SHORT" else "liq_bot",
                "level_price_mt5": round(self.liq_top if direction == "SHORT" else self.liq_bot, 2),
                "proximity_pts": round(abs(price - (self.liq_top if direction == "SHORT" else self.liq_bot)), 1),
                "near_level_source": self._near_level_source or "unknown",
            },
            "decision": {
                "action": "BLOCK",
                "direction": direction,
                "action_side": "BUY" if direction == "LONG" else "SELL",
                "trade_intent": f"ENTRY_{direction}",
                "message_semantics_version": "v1_canonical",
                "reason": reason,
                "total_score": None,
                "execution": {
                    "overall_state": "NOT_ATTEMPTED",
                    "attempted": False,
                    "brokers": [],
                },
            },
            "expansion_lines_mt5": [],
            "micro_atr_proxy": round(self._metrics.get("atr", 0), 2),
            "protection": dict(self._cycle_protection),
            "anomaly": dict(self._cycle_protection.get("anomaly", {})),
            "iceberg": {
                "detected": False,
                "side": "UNKNOWN",
                "alignment": "UNKNOWN",
                "severity": "NONE",
                "confidence": 0.0,
                "refills": 0,
                "entry_action": "UNKNOWN",
                "position_action": "UNKNOWN",
            },
        }
        self._write_decision(payload)

    # ------------------------------------------------------------------
    # Dual-account execution helper
    # ------------------------------------------------------------------

    def _open_on_all_accounts(
        self,
        direction: str,
        sl: float,
        tp1: float,
        tp2: float,
        gate_score: int = 0,
        label: str = "",
        explicit_lots: Optional[list] = None,
        strategy_context: Optional[dict] = None,
    ) -> dict:
        """
        Open position on ALL connected accounts and return canonical execution report.
        """
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        success_any = False
        attempted_any = False
        _lot_total = sum(explicit_lots) if explicit_lots else self.lot_size
        _ctx = strategy_context or {}
        price = float(self._metrics.get("gc_mid", 0.0) - self._gc_xauusd_offset)
        brokers: list[dict] = []

        def _state_from_result(result: dict | None, connected: bool) -> tuple[str, str]:
            if not connected:
                return "BROKER_DISCONNECTED", "broker not connected"
            if not result:
                return "UNKNOWN_ERROR", "empty executor result"
            if result.get("success"):
                tickets = [int(t or 0) for t in result.get("tickets", [])]
                opened = sum(1 for t in tickets if t > 0)
                expected = 3 if int(result.get("legs", 3)) == 3 else 2
                if opened >= expected:
                    return "EXECUTED", ""
                if opened > 0:
                    return "PARTIAL", "some legs failed"
                return "FAILED", str(result.get("error", "all legs failed"))
            err = str(result.get("error", "unknown error"))
            err_l = err.lower()
            if "not connected" in err_l:
                return "BROKER_DISCONNECTED", err
            if "tick" in err_l:
                return "IPC_TIMEOUT", err
            if "symbol" in err_l:
                return "SYMBOL_ERROR", err
            if "reject" in err_l or "invalid" in err_l:
                return "REJECTED", err
            return "FAILED", err

        # -- Demo (RoboForex) --------------------------------------------
        _demo = {
            "broker": "RoboForex",
            "account": None,
            "attempted": False,
            "result_state": "NOT_ATTEMPTED",
            "ticket": None,
            "error_code": None,
            "error_text": "",
        }
        if self.executor.connected:
            attempted_any = True
            _demo["attempted"] = True
            result = self.executor.open_position(
                symbol=SYMBOL, direction=direction,
                lot_size=_lot_total, sl=sl, tp1=tp1, tp2=tp2,
                explicit_lots=explicit_lots,
            )
            _demo["result_state"], _demo["error_text"] = _state_from_result(result, True)
            _tickets = [int(t or 0) for t in result.get("tickets", [])]
            _demo["ticket"] = next((t for t in _tickets if t > 0), None)
            if result.get("success"):
                t = result["tickets"]
                prefix = f"[{label}] " if label else ""
                print(f"[{ts}] {prefix}[DEMO] OPENED tickets={t}  entry={result['entry']:.2f}")
                self.executor.log_trade(
                    direction=direction, decision="CONFIRMED",
                    lots=_lot_total, entry=result["entry"],
                    sl=sl, tp1=tp1, tp2=tp2, result="open",
                    gate_score=gate_score,
                    leg1_ticket=t[0] if len(t) > 0 else 0,
                    leg2_ticket=t[1] if len(t) > 1 else 0,
                    leg3_ticket=t[2] if len(t) > 2 else 0,
                    entry_mode=_ctx.get("entry_mode", ""),
                    daily_trend=_ctx.get("daily_trend", ""),
                    phase=_ctx.get("phase", ""),
                    strategy_mode=_ctx.get("strategy_mode", ""),
                )
                success_any = True
            else:
                print(f"[{ts}] [DEMO] ORDER FAILED: {result.get('error')}")
        else:
            print(f"[{ts}] [DEMO] MT5 not connected -- skipping RoboForex")
            _demo["result_state"] = "BROKER_DISCONNECTED"
            _demo["error_text"] = "MT5 not connected"
        brokers.append(_demo)

        # -- Live (Hantec) ------------------------------------------------
        _live = {
            "broker": "Hantec",
            "account": None,
            "attempted": False,
            "result_state": "NOT_ATTEMPTED",
            "ticket": None,
            "error_code": None,
            "error_text": "",
        }
        if self.executor_live is not None and self.executor_live.connected:
            attempted_any = True
            _live["attempted"] = True
            _sl_live, _tp1_live, _tp2_live = sl, tp1, tp2
            if not self.executor.connected:
                try:
                    import MetaTrader5 as _mt5_mod
                    _htick = _mt5_mod.symbol_info_tick(SYMBOL)
                    _hantec_price = (_htick.bid + _htick.ask) / 2 if _htick else 0
                    if _hantec_price > 0:
                        _sl_dist  = abs(price - sl)
                        _tp1_dist = abs(price - tp1)
                        _tp2_dist = abs(price - tp2)
                        if direction == "LONG":
                            _sl_live  = _hantec_price - _sl_dist
                            _tp1_live = _hantec_price + _tp1_dist
                            _tp2_live = _hantec_price + _tp2_dist
                        else:
                            _sl_live  = _hantec_price + _sl_dist
                            _tp1_live = _hantec_price - _tp1_dist
                            _tp2_live = _hantec_price - _tp2_dist
                except Exception as _hx:
                    log.warning("Hantec offset fix failed: %s — using original stops", _hx)
            result_live = self.executor_live.open_position(
                symbol=SYMBOL, direction=direction,
                lot_size=_lot_total, sl=_sl_live, tp1=_tp1_live, tp2=_tp2_live,
                explicit_lots=explicit_lots,
            )
            _live["result_state"], _live["error_text"] = _state_from_result(result_live, True)
            _tickets_live = [int(t or 0) for t in result_live.get("tickets", [])]
            _live["ticket"] = next((t for t in _tickets_live if t > 0), None)
            if result_live.get("success"):
                t = result_live["tickets"]
                prefix = f"[{label}] " if label else ""
                print(f"[{ts}] {prefix}[LIVE] OPENED tickets={t}  entry={result_live['entry']:.2f}")
                self.executor_live.log_trade(
                    direction=direction, decision="CONFIRMED",
                    lots=_lot_total, entry=result_live["entry"],
                    sl=sl, tp1=tp1, tp2=tp2, result="open",
                    gate_score=gate_score,
                    leg1_ticket=t[0] if len(t) > 0 else 0,
                    leg2_ticket=t[1] if len(t) > 1 else 0,
                    leg3_ticket=t[2] if len(t) > 2 else 0,
                    entry_mode=_ctx.get("entry_mode", ""),
                    daily_trend=_ctx.get("daily_trend", ""),
                    phase=_ctx.get("phase", ""),
                    strategy_mode=_ctx.get("strategy_mode", ""),
                )
                success_any = True
            else:
                print(f"[{ts}] [LIVE] ORDER FAILED: {result_live.get('error')}")
        else:
            print(f"[{ts}] [LIVE] Hantec not connected -- skipping live account")
            _live["result_state"] = "BROKER_DISCONNECTED"
            _live["error_text"] = "Hantec MT5 not connected"
        brokers.append(_live)

        states = {b["result_state"] for b in brokers}
        if "EXECUTED" in states and states.issubset({"EXECUTED", "NOT_ATTEMPTED"}):
            overall = "EXECUTED"
        elif "EXECUTED" in states or "PARTIAL" in states:
            overall = "PARTIAL"
        elif all(s == "BROKER_DISCONNECTED" for s in states):
            overall = "BROKER_DISCONNECTED"
        elif attempted_any:
            overall = "FAILED"
        else:
            overall = "NOT_ATTEMPTED"

        return {
            "overall_state": overall,
            "attempted": attempted_any,
            "brokers": brokers,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "success_any": success_any,
        }

    def _close_all_apex_positions(self, reason: str = "NEWS_EXIT_ALL") -> None:
        """Close all open APEX positions on all connected accounts (news gate EXIT_ALL).

        Fase 4 Scope B.2: Emits PM_EVENT NEWS_EXIT via canonical flow
        which triggers Telegram notification (Fase 2 Mudança 7).
        """
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.warning("CLOSE_ALL triggered: %s", reason)
        print(f"[{ts}] NEWS EXIT_ALL -- closing all APEX positions: {reason}")

        # Track close outcomes for aggregate event
        _total_positions_found = 0
        _total_closed_ok = 0
        _total_closed_fail = 0
        _total_pnl = 0.0
        _broker_summary = []

        for label, executor in [("DEMO", self.executor),
                                 ("LIVE", self.executor_live)]:
            if executor is None or not executor.connected:
                continue
            try:
                positions = executor.get_open_positions()
                if not positions:
                    print(f"[{ts}] [{label}] no open positions")
                    continue
                _total_positions_found += len(positions)
                for pos in positions:
                    ticket = pos.get("ticket", 0)
                    result = executor.close_position(ticket)
                    if result.get("success"):
                        pnl = result.get("pnl", 0)
                        _total_pnl += pnl
                        _total_closed_ok += 1
                        print(f"[{ts}] [{label}] closed ticket={ticket} pnl={pnl:.2f}")
                    else:
                        _total_closed_fail += 1
                        print(f"[{ts}] [{label}] close FAILED ticket={ticket}: {result.get('error')}")
                _broker_summary.append(f"{label}={len(positions)}")
            except Exception as _e:
                log.error("_close_all_apex_positions [%s]: %s", label, _e)

        # === Fase 4 Scope B.2: Emit PM_EVENT NEWS_EXIT via canonical flow ===
        # Triggers Telegram notification with 📰 NEWS_EXIT icon (Fase 2 M6.3 map).
        # Reuses module-level uuid, json, DECISION_LIVE_PATH, DECISION_LOG_PATH.
        if _total_positions_found > 0:
            try:
                _now_iso = datetime.now(timezone.utc).isoformat()
                _overall_state = "EXECUTED" if _total_closed_fail == 0 else "PARTIAL"
                _canonical_payload = {
                    "timestamp": _now_iso,
                    "event_source": "EVENT_PROCESSOR",
                    "position_event": {
                        "timestamp": _now_iso,
                        "event_source": "EVENT_PROCESSOR",
                        "event_type": "NEWS_EXIT",
                        "action_type": "CLOSE_ALL",
                        "direction_affected": "BOTH",
                        "dry_run": False,
                        "execution_state": _overall_state,
                        "attempted": True,
                        "broker": " + ".join(_broker_summary) if _broker_summary else "UNKNOWN",
                        "account": None,
                        "reason": reason,
                        "ticket": None,
                        "group": None,
                        "result": f"closed={_total_closed_ok}/{_total_positions_found} pnl={_total_pnl:+.2f}",
                    },
                    "decision": {
                        "action": "PM_EVENT",
                        "direction": "BOTH",
                        "action_side": "CLOSE_ALL",
                        "trade_intent": "EXIT_ALL",
                        "message_semantics_version": "v1_canonical",
                        "reason": reason,
                        "execution": {
                            "overall_state": _overall_state,
                            "attempted": True,
                            "brokers": [],
                            "updated_at": _now_iso,
                        },
                    },
                    "created_at": _now_iso,
                    "decision_id": str(uuid.uuid4())[:8],
                }

                DECISION_LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
                _tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
                with open(_tmp, "w", encoding="utf-8") as f:
                    json.dump(_canonical_payload, f, indent=2, default=str)
                _tmp.replace(DECISION_LIVE_PATH)
                with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(_canonical_payload, default=str) + "\n")

                # Notify Telegram
                tg.notify_decision()
                log.info("NEWS_EXIT PM_EVENT emitted: %d closed, %.2f pnl",
                         _total_closed_ok, _total_pnl)
            except Exception as _event_err:
                log.warning("NEWS_EXIT event emission failed: %s", _event_err)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _micro_path(self) -> Optional[Path]:
        now = datetime.now(timezone.utc)
        for offset in range(3):
            d = (now - pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
            for suf in [".fixed.csv.gz", ".csv.gz"]:
                p = MICRO_DIR / f"microstructure_{d}{suf}"
                if p.exists():
                    return p
        return None

    def _jsonl_path(self) -> Optional[Path]:
        d = datetime.now(timezone.utc).strftime("%Y%m%d")
        for name in [f"iceberg_GC_XCEC_{d}.jsonl", f"iceberg__GC_XCEC_{d}.jsonl"]:
            p = ICE_DIR / name
            if p.exists():
                return p
        return None

    # ------------------------------------------------------------------
    # Background: metrics refresh
    # ------------------------------------------------------------------

    def _metrics_loop(self):
        """Background thread: refresh delta_4h, ATR, gc_mid every 10s or on file change.
        Also refreshes GC/XAUUSD offset via MT5 calibration every OFFSET_REFRESH_S seconds.
        MT5 is NEVER used in the fast tick loop -- only here for offset calibration.
        """
        while self._running:
            triggered = self._micro_dirty.wait(timeout=METRICS_REFRESH_S)
            self._micro_dirty.clear()
            if not self._running:
                break
            self._refresh_metrics()
            # Offset calibration: query MT5 at most every OFFSET_REFRESH_S seconds.
            # This is the ONLY place MT5 is queried for price data.
            if (time.monotonic() - self._offset_ts) >= OFFSET_REFRESH_S:
                xau_now = _mt5_price()
                if xau_now:
                    self._refresh_offset(xau_now)

    def _refresh_metrics(self):
        path = self._micro_path()
        if path is None:
            return
        try:
            cols = [
                "timestamp", "mid_price", "bar_delta",
                "dom_imbalance", "large_order_imbalance", "spread",
                "total_bid_size", "total_ask_size",
                # AnomalyForge V2 (Sprint 3.2) -- colunas para SCHEMA_FLUXFOX_V2
                "trades_per_second", "volume_per_second",
                "absorption_ratio", "cumulative_delta",
            ]
            # usecols com subset -> silencia colunas ausentes em ficheiros antigos
            _all_cols = pd.read_csv(path, nrows=0).columns.tolist()
            _cols_use  = [c for c in cols if c in _all_cols]
            df = pd.read_csv(path, usecols=_cols_use)
            if df.empty:
                return
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

            # delta_4h: sum of bar_delta in last 4 hours
            cutoff = pd.Timestamp.utcnow() - pd.Timedelta(hours=4)
            d4h = float(df[df["timestamp"] >= cutoff]["bar_delta"].sum())

            # ATR proxy: range of mid_price over last 30 min (~1800 rows)
            prices = df.tail(1800)["mid_price"].dropna()
            atr    = float(prices.max() - prices.min()) if len(prices) > 1 else 20.0
            atr    = max(atr, 5.0)

            last       = df.iloc[-1]
            gc_mid     = float(last["mid_price"])
            spread_pts = float(last.get("spread", 0.0) or 0.0)
            bid_depth  = float(last.get("total_bid_size", 0.0) or 0.0)
            ask_depth  = float(last.get("total_ask_size", 0.0) or 0.0)
            book_imb   = (
                (bid_depth - ask_depth) / (bid_depth + ask_depth)
                if (bid_depth + ask_depth) > 0 else 0.0
            )

            with self._lock:
                self._metrics = {
                    "delta_4h":   d4h,
                    "atr":        atr,
                    "bar_delta":  float(last.get("bar_delta", 0) or 0),
                    "gc_mid":     gc_mid,
                    "spread_pts": spread_pts,
                }

            # Feed Stat-Guardrail with fresh L2 observation (Sprint 1 -- The Shield)
            if _guardrail_available:
                import time as _time
                _update_guardrail(spread_pts=spread_pts, received_at=_time.time())

            # Feed Z-Score Defense Mode with full L2 snapshot (Sprint 3 -- Hard Shield)
            if _defense_available and spread_pts > 0 and bid_depth > 0 and ask_depth > 0:
                _dm_result = _defense_mode.check(
                    spread          = spread_pts,
                    total_bid_depth = bid_depth,
                    total_ask_depth = ask_depth,
                    book_imbalance  = book_imb,
                )
                with self._lock:
                    self._metrics["defense_mode"]   = _dm_result["defense_mode"]
                    self._metrics["defense_reason"]  = _dm_result["trigger_reason"]
                    self._metrics["defense_tier"]    = _dm_result.get("defense_tier", "NORMAL")
                    self._metrics["stress_direction"] = _dm_result.get("stress_direction", "HOLD")
                    self._metrics["defense_z"]       = {
                        "spread" : _dm_result["z_spread"],
                        "bid"    : _dm_result["z_bid_depth"],
                        "ask"    : _dm_result["z_ask_depth"],
                        "imb"    : _dm_result["z_imbalance"],
                    }
                _tier = _dm_result.get("defense_tier", "NORMAL")
                if _dm_result["defense_mode"]:
                    log.warning(
                        "[DEFENSE_MODE] ACTIVE tier=%s -- %s  z=(sprd=%.2f bid=%.2f ask=%.2f imb=%.2f)",
                        _tier,
                        _dm_result["trigger_reason"],
                        _dm_result["z_spread"], _dm_result["z_bid_depth"],
                        _dm_result["z_ask_depth"], _dm_result["z_imbalance"],
                    )
                # ── TIER 2 SHADOW: DEFENSIVE_EXIT (TIGHT-B, shadow mode — log only, no close) ──
                if _tier == "DEFENSIVE_EXIT":
                    _stress_dir = _dm_result.get("stress_direction", "HOLD")
                    _open_pos = []
                    try:
                        _open_pos = self.executor.get_open_positions()
                    except Exception:
                        pass
                    _n_pos = len(_open_pos)
                    _pos_dirs = [p.get("direction", "?") for p in _open_pos]
                    _pos_longs = sum(1 for d in _pos_dirs if d == "LONG")
                    _pos_shorts = sum(1 for d in _pos_dirs if d == "SHORT")

                    # Determine what action WOULD be taken based on stress vs positions
                    _shadow_action = "HOLD"
                    if _stress_dir == "EXIT_LONG" and _pos_longs > 0:
                        _shadow_action = f"EXIT_LONG({_pos_longs})"
                    elif _stress_dir == "EXIT_SHORT" and _pos_shorts > 0:
                        _shadow_action = f"EXIT_SHORT({_pos_shorts})"
                    elif _stress_dir == "EXIT_ALL" and _n_pos > 0:
                        _shadow_action = f"EXIT_ALL({_n_pos})"
                    elif _stress_dir in ("EXIT_LONG", "EXIT_SHORT", "EXIT_ALL") and _n_pos == 0:
                        _shadow_action = "NO_POSITION"
                    # else: HOLD

                    # Build protection advice for Tier 2 shadow diagnostic
                    if _stress_dir in ("EXIT_LONG", "EXIT_SHORT", "EXIT_ALL"):
                        _t2_pos_action = _stress_dir
                    else:
                        _t2_pos_action = "TIGHTEN_SL"
                    _t2_advice = _build_protection_advice(
                        source="DEFENSE_MODE",
                        alignment="UNKNOWN",
                        severity="CRITICAL",
                        entry_action="OBSERVE",
                        position_action=_t2_pos_action,
                        reason=_dm_result["trigger_reason"],
                    )

                    log.warning(
                        "[DEFENSE_TIER2_SHADOW] tier=DEFENSIVE_EXIT stress=%s action=%s | "
                        "reason=%s | positions=%d(L=%d S=%d) | "
                        "z=(sprd=%.2f bid=%.2f ask=%.2f imb=%.2f) | "
                        "n_triggers=%d extreme=%s | gc=%.2f | "
                        "prot: align=%s sev=%s entry=%s pos=%s",
                        _stress_dir, _shadow_action,
                        _dm_result["trigger_reason"], _n_pos, _pos_longs, _pos_shorts,
                        _dm_result["z_spread"], _dm_result["z_bid_depth"],
                        _dm_result["z_ask_depth"], _dm_result["z_imbalance"],
                        _dm_result.get("n_triggers", 0), _dm_result.get("any_extreme", False),
                        self._metrics.get("gc_mid", 0),
                        _t2_advice["alignment"], _t2_advice["severity"],
                        _t2_advice["entry_action"], _t2_advice["position_action"],
                    )
                    print(
                        f"[{_ts()}] [DEFENSE_TIER2_SHADOW] stress={_stress_dir} "
                        f"action={_shadow_action} | "
                        f"pos={_n_pos}(L={_pos_longs} S={_pos_shorts}) | "
                        f"{_dm_result['trigger_reason']} | "
                        f"prot: sev={_t2_advice['severity']} "
                        f"entry={_t2_advice['entry_action']} "
                        f"pos_act={_t2_advice['position_action']}"
                    )

            # Feed AnomalyForge V2 (Sprint 3.2 -- LSTM Autoencoder Hard Shield)
            # Cada tick alimenta o SequenceBuffer (seq_len=60). Após 60 ticks (~10min),
            # o modelo começa a produzir MSE. STUB enquanto buffer não está cheio.
            if _anomaly_forge_v2_available:
                try:
                    from datetime import datetime as _dt_af
                    _ts_raw = last.get("timestamp") or last.get("recv_timestamp") or ""
                    try:
                        _tick_ts = _dt_af.fromisoformat(str(_ts_raw).replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        _tick_ts = _dt_af.utcnow()

                    _ff_feats  = _build_af_features_from_series(last, _tick_ts)
                    _af_tick   = _AfMarketTick(timestamp=_tick_ts, fluxfox_features=_ff_feats)
                    _af_verdict = _anomaly_forge_v2.evaluate(_af_tick)

                    _af_score = _af_verdict.metadata.get("anomaly_score", 0.0)
                    _af_level = _af_verdict.metadata.get("anomaly_level", "STUB")
                    _af_veto  = (_af_verdict.veto_status.value == "HARD_VETO")
                    _af_mult  = _af_verdict.size_multiplier
                    _af_mse   = _af_verdict.metadata.get("mse", 0.0)
                    _af_fill  = _af_verdict.metadata.get("buffer_fill", 0)
                    _af_stub  = _af_verdict.is_stub

                    with self._lock:
                        self._metrics["anomaly_forge_score"] = _af_score
                        self._metrics["anomaly_forge_level"] = _af_level
                        self._metrics["anomaly_forge_veto"]  = _af_veto
                        self._metrics["anomaly_forge_mult"]  = _af_mult
                        self._metrics["anomaly_forge_mse"]   = _af_mse

                    # Escrever status file para o dashboard ler (produção -> dashboard)
                    try:
                        import json as _json_af
                        _af_status = {
                            "ts":          _tick_ts.isoformat(),
                            "score":       round(_af_score, 4),
                            "level":       _af_level,
                            "veto":        _af_veto,
                            "size_mult":   round(_af_mult, 4),
                            "mse":         round(_af_mse, 6),
                            "buffer_fill": _af_fill,
                            "is_stub":     _af_stub,
                            "calib_thr":   round(_anomaly_forge_v2._calib_threshold, 6),
                        }
                        _af_status_path = Path(r"C:\FluxQuantumAI\logs\production_anomaly_forge.json")
                        _af_status_path.write_text(
                            _json_af.dumps(_af_status, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass

                    if _af_veto:
                        log.warning(
                            "[ANOMALY_FORGE] HARD_VETO -- score=%.4f mse=%.6f",
                            _af_score, _af_mse,
                        )
                except Exception as _af_err:
                    log.debug("AnomalyForge V2 feed error: %s", _af_err)

        except Exception as e:
            log.warning("metrics refresh error: %s", e)

    def refresh_macro_context(self, reason: str = "") -> None:
        """
        Refresh macro directional context (M30 bias + M30 structural levels) from parquet.
        Hard veto logic must use confirmed bias only.
        """
        try:
            if not M30_BOXES_PATH.exists():
                with self._lock:
                    self.m30_bias = "unknown"
                    self.m30_bias_confirmed = False
                    self.provisional_m30_bias = "unknown"
                return

            df = pd.read_parquet(M30_BOXES_PATH)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            if df.empty:
                return

            confirmed_bias, is_confirmed = derive_m30_bias(df, confirmed_only=True)
            provisional_bias, _ = derive_m30_bias(df, confirmed_only=False)
            row = df[df["m30_liq_top"].notna()].iloc[-1] if not df[df["m30_liq_top"].notna()].empty else df.iloc[-1]

            with self._lock:
                self.m30_bias = confirmed_bias
                self.m30_bias_confirmed = is_confirmed
                self.provisional_m30_bias = provisional_bias
                if pd.notna(row.get("m30_liq_top")):
                    self.m30_liq_top_gc = float(row.get("m30_liq_top"))
                if pd.notna(row.get("m30_liq_bot")):
                    self.m30_liq_bot_gc = float(row.get("m30_liq_bot"))
                if self.m30_liq_top_gc is not None:
                    self.m30_liq_top = round(self.m30_liq_top_gc - self._gc_xauusd_offset, 2)
                if self.m30_liq_bot_gc is not None:
                    self.m30_liq_bot = round(self.m30_liq_bot_gc - self._gc_xauusd_offset, 2)
                self._macro_ctx_last_refresh = time.monotonic()
                self._macro_ctx_refresh_needed = False

            if reason:
                log.info(
                    "MACRO_REFRESH[%s]: confirmed_bias=%s provisional_bias=%s confirmed=%s",
                    reason, confirmed_bias, provisional_bias, is_confirmed,
                )
        except Exception as e:
            log.warning("refresh_macro_context failed: %s", e)

    def request_macro_context_refresh(self, reason: str = "") -> None:
        """Mark macro context for refresh and refresh immediately when possible."""
        with self._lock:
            self._macro_ctx_refresh_needed = True
        self.refresh_macro_context(reason=reason)

    def _refresh_offset(self, xau_price: float) -> None:
        """
        Refresh GC/XAUUSD offset using latest MT5 tick and latest GC micro price.
        Uses EWM(span=3) to smooth out tick-level noise.
        Called every OFFSET_REFRESH_S seconds from the tick loop.
        """
        gc_mid = self._metrics.get("gc_mid", 0.0)
        if gc_mid <= 0 or xau_price <= 0:
            return
        raw_offset = _compute_offset(gc_mid, xau_price)
        with self._lock:
            if self._gc_xauusd_offset == 0.0:
                # First sample: use raw value directly
                self._gc_xauusd_offset = raw_offset
            else:
                # EWM update: alpha = 2/(span+1) with span=3 -> alpha=0.5
                alpha = 0.5
                self._gc_xauusd_offset = round(
                    alpha * raw_offset + (1 - alpha) * self._gc_xauusd_offset, 3
                )
            # Update MT5-equivalent levels -- M5 execution
            self.liq_top  = round(self.liq_top_gc - self._gc_xauusd_offset, 2)
            self.liq_bot  = round(self.liq_bot_gc - self._gc_xauusd_offset, 2)
            self.box_high = round(self.box_high_gc - self._gc_xauusd_offset, 2) if self.box_high_gc is not None else None
            self.box_low  = round(self.box_low_gc  - self._gc_xauusd_offset, 2) if self.box_low_gc  is not None else None
            # Update MT5-equivalent levels -- M30 macro (border alignment)
            if self.m30_liq_top_gc is not None:
                self.m30_liq_top = round(self.m30_liq_top_gc - self._gc_xauusd_offset, 2)
            if self.m30_liq_bot_gc is not None:
                self.m30_liq_bot = round(self.m30_liq_bot_gc - self._gc_xauusd_offset, 2)
            self._offset_ts = time.monotonic()
        log.debug("offset refreshed: GC-XAU=%.3f  liq_top_mt5=%.2f  liq_bot_mt5=%.2f",
                  self._gc_xauusd_offset, self.liq_top, self.liq_bot)

    # ------------------------------------------------------------------
    # V3 RL helpers
    # ------------------------------------------------------------------

    def _v3_l2_snapshot(self, direction: str, iceberg_event: Optional[dict] = None) -> dict:
        """
        Build the l2_snapshot dict for V3FeatureEngine from the metrics cache.
        Missing fields fall back to safe defaults.
        """
        m = self._metrics
        d4h = float(m.get("delta_4h", 0.0))
        return {
            "dom_imbalance":            float(m.get("bar_delta", 0.0)) / 1000.0,  # normalised proxy
            "delta_m1":                 float(m.get("bar_delta", 0.0)),
            "delta_5min":               d4h / 48.0,   # 5-min fraction of 4h delta
            "delta_4h":                 d4h,
            "delta_4h_history":         [d4h],
            "buy_volume":               1.0,           # not available in micro file
            "sell_volume":              1.0,
            "spread":                   0.5,
            "total_bid_depth":          500.0,
            "total_ask_depth":          500.0,
            "tick_volume_m1":           0.0,
            "tick_volume_m1_avg20":     1.0,
            "bid_absorption":           0.0,
            "ask_absorption":           0.0,
            "daily_trend":              "LONG" if d4h > 0 else "SHORT",
            "levels_broken_contra_15min": 0,
        }

    def _v3_m30_levels(self, price: float) -> dict:
        """Build m30_levels dict for V3FeatureEngine from current processor state."""
        m   = self._metrics
        atr = float(m.get("atr", 25.0))
        return {
            "m30_fmv":          price,   # best available -- no FMV computed here
            "m30_liq_top":      self.liq_top,
            "m30_liq_bot":      self.liq_bot,
            "atr_m30":          atr,
            "atr_m30_20d_avg":  atr,
            "m30_box_confirmed": False,
            "m30_box_direction": 0,
            "weekly_aligned":   False,
            "atr_d1":           atr * 3.0,
        }

    def _v3_iceberg_scan(self, ice_event: Optional[dict] = None) -> dict:
        """Build iceberg_scan dict from the triggering iceberg event (if any)."""
        if ice_event:
            side = str(ice_event.get("side", "")).upper()
            return {
                "detected":  True,
                "direction": side if side in ("LONG", "SHORT") else "NEUTRAL",
                "score":     float(ice_event.get("probability", ice_event.get("prob", 0.0))),
            }
        return {"detected": False, "direction": "NEUTRAL", "score": 0.0}

    # ------------------------------------------------------------------
    # Pre-entry gate -- data-driven thresholds (thresholds_gc.json)
    # ------------------------------------------------------------------

    def _check_pre_entry_gates(self, direction: str, delta_4h: float, price: float) -> tuple[bool, str]:
        """
        Run all pre-entry checks before evaluating the main gate.
        Returns (blocked, reason). If blocked=True, abort gate immediately.
        All thresholds from thresholds_gc.json -- no hardcoded values.

        Checks:
          a) delta_4h directional block  (from GC 62-trade calibration study)
          b) max open positions cap
          c) awaiting TP1 on any existing trade (leg1 still open)
          d) margin level minimum
        """
        thr = self._thresholds

        # --- 0) startup cooldown --- block ALL trades until metrics stabilise ---
        remaining = self._startup_cooldown_until - time.monotonic()
        if remaining > 0:
            return True, (
                f"STARTUP COOLDOWN: metrics stabilising, {remaining:.0f}s remaining"
                f" (delta_4h may be stale -- no trades until cooldown expires)"
            )

        # --- a0) Sprint 8: Trade cooldown ---
        if thr.get("trade_cooldown_enabled", True):
            now_mono = time.monotonic()
            cooldown_min = float(thr.get("trade_cooldown_min", 30))
            same_level_cooldown_min = float(thr.get("same_level_cooldown_min", 60))
            same_level_prox = float(thr.get("same_level_proximity_pts", 2.0))

            if self._last_trade_time > 0:
                elapsed_min = (now_mono - self._last_trade_time) / 60.0
                # Check same-level cooldown (stricter)
                if abs(price - self._last_trade_level) <= same_level_prox:
                    if elapsed_min < same_level_cooldown_min:
                        remaining = same_level_cooldown_min - elapsed_min
                        return True, (
                            f"[COOLDOWN] BLOCKED -- last trade {elapsed_min:.0f}min ago "
                            f"at same level {self._last_trade_level:.2f} "
                            f"(cooldown={same_level_cooldown_min:.0f}min). "
                            f"Next eligible: {remaining:.0f}min"
                        )
                # Check global cooldown
                elif elapsed_min < cooldown_min:
                    remaining = cooldown_min - elapsed_min
                    return True, (
                        f"[COOLDOWN] BLOCKED -- last trade {elapsed_min:.0f}min ago "
                        f"(cooldown={cooldown_min:.0f}min). "
                        f"Next eligible: {remaining:.0f}min"
                    )

        # --- a) delta_4h gate ---
        _exh_high = float(thr.get("delta_4h_exhaustion_high", 3000))
        _exh_low  = float(thr.get("delta_4h_exhaustion_low", -1050))

        if thr.get("delta_4h_inverted_fix", False):
            # Sprint 8 V2: inverted interpretation
            # Extreme delta SUPPORTS exhaustion-aligned trade, BLOCKS the opposite
            if delta_4h > _exh_high and direction == "LONG":
                return True, (f"[DELTA_4H_FIX] BLOCKED: buyer exhaustion (d4h={delta_4h:+.0f}) "
                              f"contradicts LONG entry")
            if delta_4h < _exh_low and direction == "SHORT":
                return True, (f"[DELTA_4H_FIX] BLOCKED: seller exhaustion (d4h={delta_4h:+.0f}) "
                              f"contradicts SHORT entry")
            log.debug("[DELTA_4H_FIX] pre-entry: d4h=%+.0f dir=%s -> pass", delta_4h, direction)
        else:
            # Original logic (delta_4h_inverted_fix=false)
            if direction == "SHORT" and delta_4h > _exh_high:
                return True, (f"BLOCKED: delta_4h={delta_4h:+.0f} above SHORT block"
                              f" threshold={_exh_high:.0f}")
            if direction == "LONG" and delta_4h < _exh_low:
                return True, (f"BLOCKED: delta_4h={delta_4h:+.0f} below LONG block"
                              f" threshold={_exh_low:.0f}")

        # --- b) max 2 active trade groups ---
        # Group MT5 positions by open_time proximity: positions opened within
        # GROUP_TIME_WINDOW_S of each other = same group (L1+L2+Runner opened atomically).
        # This approach is trades.csv-independent -- no race condition on log writes,
        # and correctly handles post-SHIELD state (L1 closed, L2+Runner still open).
        GROUP_TIME_WINDOW_S = 30
        open_positions = self.executor.get_open_positions()

        def _count_groups(positions: list[dict]) -> int:
            if not positions:
                return 0
            times = sorted(
                _iso_to_epoch(p.get("time_open", "")) for p in positions
            )
            groups, last_t = 1, times[0]
            for t in times[1:]:
                if t - last_t > GROUP_TIME_WINDOW_S:
                    groups += 1
                    last_t = t
            return groups

        n_groups   = _count_groups(open_positions)
        max_groups = int(thr.get("max_positions", 2))
        if n_groups >= max_groups:
            return True, (f"BLOCKED: max {max_groups} trade groups reached"
                          f" ({n_groups} groups, {len(open_positions)} legs open)")

        # --- c) no existing DIRECTION at the same zone ---
        # Block if any open position in the same direction exists.
        # Post-SHIELD (L1 closed), L2+Runner still guard the level.
        if any(p["direction"] == direction for p in open_positions):
            return True, f"BLOCKED: already have {direction} position open"

        # --- d) margin level ---
        min_margin = float(thr.get("margin_level_min", 600))
        margin     = _mt5_margin_level()
        if margin < min_margin:
            return True, (f"BLOCKED: margin {margin:.0f}%"
                          f" below minimum {min_margin:.0f}%")

        return False, ""

    # ------------------------------------------------------------------
    # PATCH 2A — Trend Continuation trigger (trend day coverage)
    # ------------------------------------------------------------------

    def _patch2a_continuation_trigger(
        self, price_mt5: float, gc_price: float, offset: float
    ) -> Optional[tuple[str, str]]:
        """
        Evaluate TREND_CONTINUATION when price is outside M30 box in trend
        direction and no liq level is nearby.

        Returns (direction, strategy_reason) or None.

        Guards:
          - trend_continuation_enabled must be True
          - DISABLE_CONTINUATION kill switch respected
          - phase must be EXPANSION or TREND
          - daily_trend must be defined
          - price must be outside box in trend direction
          - _get_trend_entry_mode must return CONTINUATION (displacement + delta + exhaustion)
          - ambiguity -> None (SKIP)
        """
        tc = self._thresholds
        if not tc.get("trend_continuation_enabled", False):
            return None

        # Kill switch
        if Path("C:/FluxQuantumAI/DISABLE_CONTINUATION").exists():
            return None

        # Phase and trend
        phase = self._get_current_phase()
        self._last_phase = phase
        if phase not in ("EXPANSION", "TREND"):
            return None
        if self.daily_trend not in ("long", "short"):
            return None

        # Box boundaries (MT5 space)
        if self.box_high is None or self.box_low is None:
            return None

        # Price must be OUTSIDE box in trend direction
        if self.daily_trend == "long":
            if price_mt5 <= self.box_high:
                return None  # not above box
            trend_dir = "LONG"
            level_type = "liq_top"
        else:
            if price_mt5 >= self.box_low:
                return None  # not below box
            trend_dir = "SHORT"
            level_type = "liq_bot"

        # Delegate to existing continuation logic (displacement + exhaustion + delta)
        entry_mode, direction, reason = self._get_trend_entry_mode(
            level_type, price_mt5, trend_dir)

        if entry_mode != "CONTINUATION" or direction is None:
            return None

        strategy_reason = f"PATCH2A_CONTINUATION {trend_dir}: {reason}"
        return (direction, strategy_reason)

    # ------------------------------------------------------------------
    # Proximity check
    # ------------------------------------------------------------------

    # FASE 2a: source classification for shadow logging
    _near_level_source: str = ""  # "m5+m30" | "m30_only" | "m5_only" | ""

    def _near_level(self, price: float) -> tuple[str, float]:
        """
        Return (level_type, level_price) if price is near a structural level.

        FASE 2a: Hierarchical check with source classification.
          1. Check M30 structural levels (primary reference)
          2. Check M5 execution levels (refinement / legacy trigger)
          3. Classify: m5+m30 / m30_only / m5_only
          4. Behavioral compatibility: returns M5 level when available (same as before)
             Source classification stored in self._near_level_source for tick loop logging.
        """
        # FASE 1 THR-1: stable ATR14 from M30 parquet
        atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
        band = max(atr * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)

        # ── Step 1: M30 structural proximity check ──
        m30_near_top = (self.m30_liq_top is not None
                        and abs(price - self.m30_liq_top) <= band)
        m30_near_bot = (self.m30_liq_bot is not None
                        and abs(price - self.m30_liq_bot) <= band)
        m30_match = "liq_top" if m30_near_top else ("liq_bot" if m30_near_bot else "")
        m30_price = (self.m30_liq_top if m30_near_top
                     else (self.m30_liq_bot if m30_near_bot else 0.0))

        # ── Step 2: M5 execution proximity check (existing logic) ──
        m5_near_top = (self.liq_top is not None and self.liq_top > 0
                       and abs(price - self.liq_top) <= band)
        m5_near_bot = (self.liq_bot is not None and self.liq_bot > 0
                       and abs(price - self.liq_bot) <= band)
        m5_match = "liq_top" if m5_near_top else ("liq_bot" if m5_near_bot else "")
        m5_price = (self.liq_top if m5_near_top
                    else (self.liq_bot if m5_near_bot else 0.0))

        # ── Step 3: Classify source ──
        if m5_match and m30_match and m5_match == m30_match:
            self._near_level_source = "m5+m30"
        elif m30_match and not m5_match:
            self._near_level_source = "m30_only"
        elif m5_match and not m30_match:
            self._near_level_source = "m5_only"
        else:
            self._near_level_source = ""

        # ── Shadow logging: every source transition ──
        if self._near_level_source:
            log.info("NEAR_LEVEL_SOURCE: %s | price=%.2f | m5=%s(%.2f) m30=%s(%.2f)",
                     self._near_level_source, price,
                     m5_match, m5_price, m30_match, m30_price)

        # ── Step 4: Return — behavioral compatibility ──
        # FASE 2a: firing logic unchanged. M5 level returned when available (as before).
        # M30-only triggers also fire (covers the blind spot).
        # Source classification is logged for FASE 2b data collection.
        if m5_match:
            # M5 available — return M5 level (same behavior as pre-FASE 2)
            return m5_match, m5_price
        if m30_match:
            # M30-only: NEW — M30 structural level triggers even without M5
            return m30_match, m30_price
        return "", 0.0

    # ------------------------------------------------------------------
    # Dwell Time tracking  (CAL-LEVEL-TOUCH 2026-04-10)
    # ------------------------------------------------------------------

    # Contextual dwell reset: partial reaction revives the dwell clock
    _DWELL_PARTIAL_REACTION_PTS = 3.0  # >= 3pts move resets dwell timer (even if < full threshold)

    def _update_dwell(self, level_type: str, price: float, direction: str) -> None:
        """
        Called every tick when price is NEAR a structural level.
        Records first-touch timestamp and reference price.
        Marks the touch as 'stale' when price has been near the level for
        > dwell_abort_s without moving >= dwell_move_thr in the signal direction.

        Contextual reset: if price makes a partial reaction move (>= 3pts)
        in the signal direction, the dwell timer resets — the market is
        still reacting, just slowly. This prevents killing valid signals
        in slow/grinding markets.
        """
        now = time.monotonic()
        state = self._dwell_state.get(level_type)

        if state is None:
            # Fresh approach -- start the dwell clock
            self._dwell_state[level_type] = {
                "first_ts":  now,
                "ref_price": price,
                "stale":     False,
                "best_move": 0.0,
            }
            return

        # Measure reaction from first-touch reference price
        ref   = state["ref_price"]
        move  = (ref - price) if direction == "SHORT" else (price - ref)

        # Track best move in signal direction
        best = state.get("best_move", 0.0)
        if move > best:
            state["best_move"] = move

        if move >= self._dwell_move_thr:
            # Full reaction confirmed -- reset this level's state (fresh touch next time)
            del self._dwell_state[level_type]
            return

        # Contextual reset: partial reaction (>= 3pts) resets the dwell timer
        # This keeps the signal alive in slow markets where price grinds
        if state.get("stale") and move >= self._DWELL_PARTIAL_REACTION_PTS:
            log.info("DWELL_REVIVE: %s partial reaction %.1fpts (>= %.1f) -- dwell timer reset",
                     level_type, move, self._DWELL_PARTIAL_REACTION_PTS)
            state["first_ts"] = now
            state["stale"]    = False
            state["ref_price"] = price  # new reference from revived position
            state["best_move"] = 0.0
            return

        if state.get("stale"):
            return  # still stale, no partial reaction -- keep suppressed

        # Check if stale window expired
        elapsed = now - state["first_ts"]
        if elapsed >= self._dwell_abort_s:
            state["stale"] = True
            log.info("DWELL_STALE: %s touched %.0fs ago -- no %.1f-pt reaction (best=%.1fpts) -- gate suppressed",
                     level_type, elapsed, self._dwell_move_thr, state.get("best_move", 0.0))

    def _is_dwell_stale(self, level_type: str) -> tuple[bool, float]:
        """
        Returns (is_stale, elapsed_seconds).
        Called before firing the gate thread.

        Auto-resets stale state after 10 minutes to prevent permanent suppression.
        Bug fix: DWELL_STALE was suppressing gates indefinitely (12000s+) because
        stale=True never cleared while price stayed near a level that kept updating.
        """
        state = self._dwell_state.get(level_type)
        if state is None:
            return False, 0.0
        elapsed = time.monotonic() - state["first_ts"]
        is_stale = state.get("stale", False)

        # Auto-reset: if stale for > 10 min, clear and allow fresh dwell cycle
        if is_stale and elapsed > 600.0:
            log.info("DWELL_RESET: %s stale for %.0fs — auto-reset to allow fresh gate checks",
                     level_type, elapsed)
            del self._dwell_state[level_type]
            return False, 0.0

        return is_stale, elapsed

    def _clear_dwell(self, level_type: Optional[str] = None) -> None:
        """
        Reset dwell state when price LEAVES the near-level band.
        Passing level_type=None clears ALL stale states (price left all bands).
        """
        if level_type is not None:
            self._dwell_state.pop(level_type, None)
        else:
            # Only clear stale entries -- non-stale fresh touches can persist
            stale_keys = [k for k, v in self._dwell_state.items() if v.get("stale")]
            for k in stale_keys:
                del self._dwell_state[k]

    # ------------------------------------------------------------------
    # Gate trigger
    # ------------------------------------------------------------------

    def _trigger_gate(self, price: float, direction: str, source: str,
                      strategy_reason: str = ""):
        """
        Evaluate gate at given price. Respects GATE_COOLDOWN_S between triggers.
        Opens position (or prints DRY RUN) on GO.

        Per-direction cooldown: LONG and SHORT have independent cooldown timers.
        BLOCK decisions do NOT reset the cooldown — only GO resets it.
        This prevents blocked SHORTs from starving LONG gate checks (and vice versa).
        """
        now_ts = time.monotonic()
        with self._lock:
            last_dir = self._last_trigger_by_dir.get(direction, 0.0)
            if now_ts - last_dir < GATE_COOLDOWN_S:
                return
            # Mark evaluation start — will only persist if GO (see below)
            self._last_trigger = now_ts  # legacy compat for GAMMA/DELTA

        ts = _ts()
        log.info("gate triggered by %s | price=%.2f dir=%s", source, price, direction)

        # Reset protection advice for this gate cycle — no carry-over from previous cycle.
        # Single source of truth is decision_live.json / decision_log.jsonl, not these vars.
        self._cycle_protection = _default_protection()

        # Per-direction lock: block if a CONFIRMED order was placed recently
        # Refresh macro context when requested or stale cache (>60s)
        if self._macro_ctx_refresh_needed or (time.monotonic() - self._macro_ctx_last_refresh > 60):
            self.refresh_macro_context(reason=f"trigger_gate:{source}")

        with self._lock:
            lock_until       = self._direction_lock_until.get(direction, 0.0)
            d4h              = self._metrics.get("delta_4h", 0.0)
            m30_bias         = self.m30_bias
            m30_bias_confirmed = self.m30_bias_confirmed
            provisional_m30_bias = self.provisional_m30_bias
            m30_top_mt5      = self.m30_liq_top
            m30_bot_mt5      = self.m30_liq_bot
            m5_top_mt5       = self.liq_top
            m5_bot_mt5       = self.liq_bot
        if time.monotonic() < lock_until:
            remaining = lock_until - time.monotonic()
            print(f"[{ts}] DIR_LOCK {direction}: {remaining:.0f}s remaining after last CONFIRMED")
            log.info("direction lock active for %s -- %.0fs remaining", direction, remaining)
            return

        # Structural staleness hard block (confirmed fix): do not trade on stale M1 context.
        try:
            if SERVICE_STATE_PATH.exists():
                _svc = json.loads(SERVICE_STATE_PATH.read_text(encoding="utf-8"))
                if bool(_svc.get("m1_stale_critical", False)):
                    m1_age = float(_svc.get("m1_age_s", -1))
                    stale_reason = f"STRUCTURE_STALE_BLOCK: m1_stale_critical=true age={m1_age:.0f}s"
                    print(f"[{ts}] {stale_reason}")
                    log.warning("STRUCTURE_STALE_BLOCK %s: m1_stale_critical=true age=%.0fs", direction, m1_age)
                    self._write_stale_block_decision(direction, price, stale_reason, source)
                    return
        except Exception as _svc_e:
            log.debug("service_state stale check failed: %s", _svc_e)

        # -- PONTO -1: NEWS RELEASE FEATURE FLAG ------------------------------
        # Se um release económico HIGH/CRITICAL aconteceu nos últimos 5 min,
        # NEWS_STATE.is_news_active=True.
        # Lógica: se o gold_signal do release é OPOSTO à direcção de entrada
        # -> veto imediato (ex: NFP BEAT = gold BEARISH -> rejeita LONG).
        # Se gold_signal=NEUTRAL ou SAME direcção -> deixa passar (warn only).
        # NOTE: não é execução por latência -- é risk veto informado.
        if _news_state_available and _NEWS_STATE is not None:
            _ns = _NEWS_STATE.check()
            if _ns["is_news_active"]:
                _gs = _ns.get("gold_signal", "NEUTRAL")
                _sl = _ns.get("surprise_label", "")
                _en = _ns.get("event_name", "?")
                _contra = (
                    (_gs == "BEARISH" and direction == "LONG") or
                    (_gs == "BULLISH" and direction == "SHORT")
                )
                if _contra:
                    print(f"[{ts}] NEWS_VETO {direction}: {_en} -> Gold {_gs} ({_sl})")
                    log.warning("NEWS_VETO %s: %s -> Gold %s (%s)", direction, _en, _gs, _sl)
                    return
                else:
                    log.info("NEWS_ACTIVE (no veto): %s -> Gold %s | dir=%s", _en, _gs, direction)

        # -- PONTO 0: Z-SCORE DEFENSE MODE (Sprint 3 -- Hard Shield) ----------
        # Veto qualquer nova entrada se a microestrutura apresentar condições
        # anómalas (spread widening, liquidity collapse, extreme imbalance).
        # Hard block -- não passa para nenhuma gate check abaixo.
        # Tier 1 (ENTRY_BLOCK) + Tier 2 (DEFENSIVE_EXIT) both block entries.
        # Tier 2 DEFENSIVE_EXIT close_all is SHADOW ONLY (2026-04-14).
        with self._lock:
            _dm_active = self._metrics.get("defense_mode", False)
            _dm_reason = self._metrics.get("defense_reason", "")
            _dm_tier   = self._metrics.get("defense_tier", "NORMAL")
            _dm_stress = self._metrics.get("stress_direction", "HOLD")

        # Build anomaly protection advice (rule-based, Z-score thresholds)
        # entry_action is OBSERVE (never BLOCK) — backtest proved filtering hurts PF.
        if _dm_tier == "DEFENSIVE_EXIT":
            _a_sev = "CRITICAL"
            _a_entry = "OBSERVE"
            if _dm_stress == "EXIT_LONG":
                _a_pos = "EXIT_LONG"
            elif _dm_stress == "EXIT_SHORT":
                _a_pos = "EXIT_SHORT"
            elif _dm_stress == "EXIT_ALL":
                _a_pos = "EXIT_ALL"
            else:
                _a_pos = "TIGHTEN_SL"
        elif _dm_tier == "ENTRY_BLOCK" or _dm_active:
            _a_sev = "HIGH"
            _a_entry = "OBSERVE"
            _a_pos = "TIGHTEN_SL"
        else:
            _a_sev = "NONE"
            _a_entry = "ALLOW"
            _a_pos = "HOLD"
        if direction == "LONG":
            _a_flow = "AGAINST_LONG" if _dm_stress in ("EXIT_LONG", "EXIT_ALL") else "FAVORS_LONG"
        elif direction == "SHORT":
            _a_flow = "AGAINST_SHORT" if _dm_stress in ("EXIT_SHORT", "EXIT_ALL") else "FAVORS_SHORT"
        else:
            _a_flow = "UNKNOWN"
        _a_align = "OPPOSED" if "AGAINST" in _a_flow else ("ALIGNED" if "FAVORS" in _a_flow else "UNKNOWN")
        self._cycle_protection["anomaly"] = _build_protection_advice(
            source="DEFENSE_MODE",
            alignment=_a_align,
            severity=_a_sev,
            flow_relation=_a_flow,
            entry_action=_a_entry,
            position_action=_a_pos,
            reason=_dm_reason,
            detected=bool(_dm_active),
            side="UNKNOWN",
            confidence=None,
        )

        if _dm_active:
            print(f"[{ts}] DEFENSE_MODE VETO {direction}: tier={_dm_tier} {_dm_reason}")
            log.warning("DEFENSE_MODE veto %s: tier=%s %s", direction, _dm_tier, _dm_reason)
            tg.notify_defense_mode(_dm_reason, tier=_dm_tier,
                                   protection=self._cycle_protection["anomaly"])
            return

        # -- PONTO 0.5: ANOMALY FORGE V2 (Sprint 3.2 -- LSTM-Autoencoder Hard Shield) -
        # Segunda camada de defesa: Grenadier LSTM-Autoencoder detecta anomalias
        # de microestrutura. Bloqueia entradas quando MSE > threshold calibrado.
        # Activo somente após buffer cheio (60 ticks ~ 10min). STUB enquanto warmup.
        if _anomaly_forge_v2_available:
            with self._lock:
                _af_veto  = self._metrics.get("anomaly_forge_veto",  False)
                _af_score = self._metrics.get("anomaly_forge_score", 0.0)
                _af_level = self._metrics.get("anomaly_forge_level", "STUB")
            if _af_veto:
                print(
                    f"[{ts}] ANOMALY_FORGE HARD_VETO {direction}: "
                    f"score={_af_score:.4f} level={_af_level}"
                )
                log.warning(
                    "ANOMALY_FORGE HARD_VETO %s: score=%.4f level=%s",
                    direction, _af_score, _af_level
                )
                return

        # -- PONTO 1: M30 BIAS HARD GATE ---------------------------------------
        # Confirmed fix:
        #   - only CONFIRMED m30_bias may hard-block
        #   - provisional/unconfirmed bias is telemetry only
        _is_patch2a = (source == "PATCH2A")
        if not m30_bias_confirmed:
            if provisional_m30_bias in ("bullish", "bearish"):
                log.info(
                    "M30_BIAS_PROVISIONAL_ONLY: src=%s dir=%s provisional=%s confirmed=%s -> no hard block",
                    source, direction, provisional_m30_bias, m30_bias
                )
                if _is_patch2a:
                    print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} provisional={provisional_m30_bias} confirmed=unknown -> PASS")
            elif _is_patch2a:
                print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} provisional=unknown confirmed=unknown -> PASS")
        else:
            if m30_bias == "bullish" and direction == "SHORT":
                print(f"[{ts}] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)")
                log.info("M30_BIAS_BLOCK: confirmed bullish M30 bias rejects SHORT at %.2f (src=%s)", price, source)
                if _is_patch2a:
                    print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias=bullish -> BLOCK")
                return
            if m30_bias == "bearish" and direction == "LONG":
                print(f"[{ts}] M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG rejected (contra-M30)")
                log.info("M30_BIAS_BLOCK: confirmed bearish M30 bias rejects LONG at %.2f (src=%s)", price, source)
                if _is_patch2a:
                    print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias=bearish -> BLOCK")
                return
            if _is_patch2a:
                print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias={m30_bias} -> PASS")

        # -- PONTO 3: M30 BORDER ALIGNMENT -- REMOVED 2026-04-14 ----------------
        # Was blocking valid entries due to stale M30 level data.
        # M30_BIAS_BLOCK (PONTO 1) already filters contra-trend direction.
        # Border alignment adds no value when M30 levels lag behind M5.
        if m30_top_mt5 is not None and m30_bot_mt5 is not None:
            # Shadow log only — no blocking
            atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
            if direction == "SHORT":
                dist = abs(m5_top_mt5 - m30_top_mt5)
            else:
                dist = abs(m5_bot_mt5 - m30_bot_mt5)
            log.info(
                "BORDER_INFO %s: M5=%.2f M30=%.2f dist=%.1f ATR=%.2f (shadow only, no block)",
                direction, m5_top_mt5 if direction == "SHORT" else m5_bot_mt5,
                m30_top_mt5 if direction == "SHORT" else m30_bot_mt5,
                dist, atr,
                )
        else:
            # M30 levels not yet available -- log skip so we know
            log.debug("BORDER_SKIP %s: m30_liq_top/bot not populated yet", direction)

        # -- P0 Operational rules (BEFORE gate chain V1-V4) --------------------
        open_positions = self.executor.get_open_positions()
        margin_level   = _mt5_margin_level()
        op_blocked, op_reason = self.ops.check_can_enter(
            open_positions   = open_positions,
            margin_level     = margin_level,
            signal_price     = price,
            signal_direction = direction,
        )
        if op_blocked:
            print(f"[{ts}] {op_reason}")
            log.info("operational rules blocked: %s", op_reason)
            return

        # Pre-entry gate: data-driven threshold checks before calling main gate
        blocked, block_reason = self._check_pre_entry_gates(direction, d4h, price)
        if blocked:
            print(f"[{ts}] {block_reason}")
            log.info("pre-entry blocked: %s", block_reason)
            return

        # -- PLACEHOLDER: CASCADE_N_LEVELS (CAL-PENDING) ----------------------
        # Calibrated: cascade_N_levels=3, cascade_window=10 (settings.json 2026-04-08)
        # Intent: if price cascaded through N structural levels within the last
        # cascade_window M1 bars, the move is classified as trending/cascade --
        # adjust gate scoring or block reversal entries against the cascade.
        # TODO: implement cascade detection in level_detector and pass cascade_active
        # flag here. Architecture decision: block reversal (BLOCK) or reduce score?
        _cascade_n      = int(self._thresholds.get("cascade_N_levels", 3))
        _cascade_window = int(self._thresholds.get("cascade_window", 10))
        # cascade_active = self._check_cascade_levels(direction, _cascade_n, _cascade_window)
        # if cascade_active:
        #     print(f"[{ts}] CASCADE_BLOCK {direction}: {_cascade_n} levels broken in {_cascade_window} bars")
        #     return
        log.debug("CASCADE placeholder: n=%d window=%d (not yet implemented)", _cascade_n, _cascade_window)

        # -- PLACEHOLDER: VOL_CLIMAX (CAL-PENDING) ----------------------------
        # Calibrated: vol_climax_multiplier=0.682, dom_imbalance_threshold=17.03
        # (raw bar_delta units -- top 1.2% of flow activity, NOT normalized dom_imbalance)
        # Intent: if current bar_delta exceeds vol_climax_multiplier x rolling_std,
        # the bar is classified as a volume climax -- potential exhaustion signal that
        # ENHANCES reversal entry (aligned with CAL-03 finding: high delta = exhaustion).
        # TODO: compute rolling bar_delta std from df_micro and compare to current bar.
        # Architecture decision: telemetry only, or +1 score bonus in gate?
        _vol_climax_mult = float(self._thresholds.get("vol_climax_multiplier", 0.682))
        _dom_thr_raw     = float(self._thresholds.get("dom_imbalance_threshold", 17.03))
        # bar_delta = self._metrics.get("bar_delta", 0.0)
        # vol_climax_active = abs(bar_delta) > _dom_thr_raw  (raw bar_delta units)
        # if vol_climax_active:
        #     print(f"[{ts}] VOL_CLIMAX {direction}: bar_delta={bar_delta:.0f} > {_dom_thr_raw:.1f}")
        log.debug("VOL_CLIMAX placeholder: mult=%.3f dom_thr=%.2f (not yet implemented)", _vol_climax_mult, _dom_thr_raw)

        try:
            _exp_lines_gc = self._compute_expansion_lines(direction)
            # Convert expansion lines from GC space to MT5 space (same as all other levels)
            _off = self._gc_xauusd_offset
            _exp_lines = [round(el - _off, 2) for el in _exp_lines_gc] if _exp_lines_gc else []
            _atr_m30   = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", None))
            decision = self.gate.check(
                entry_price=price,
                direction=direction,
                now=pd.Timestamp.utcnow(),
                liq_top=self.liq_top,
                liq_bot=self.liq_bot,
                box_high=self.box_high,
                box_low=self.box_low,
                daily_trend=self.daily_trend,
                expansion_lines=_exp_lines,
                atr_m30=_atr_m30,
            )
        except Exception as e:
            print(f"[{ts}] GATE ERROR: {e}")
            log.error("gate.check error: %s", e)
            return

        # Gate status display
        _trending_v1 = self.daily_trend in ("long", "short") and self.box_high is not None and self.box_low is not None
        if _trending_v1:
            _in_zone = self.box_low <= price <= self.box_high
            v1 = "PASS" if _in_zone else "ZONE_FAIL"
        else:
            _level_for_v1 = self.liq_top if direction == "SHORT" else self.liq_bot
            v1 = "PASS" if abs(price - _level_for_v1) <= 8.0 else "NEAR"
        v2 = ("%.0f" % decision.l2_entry_score) if decision.l2_entry_score >= 0 else "N/A"
        v3 = decision.momentum.status.upper()
        v4 = decision.v4_status
        sc = decision.total_score
        verdict = "GO" if decision.go else "BLOCK"

        _kz_label  = kill_zone_label()
        _spd_label = self._speed_tracker.label(window_s=5.0)
        print(f"[{ts}] V1={v1}  V2={v2}  V3={v3}  V4={v4}  {_kz_label}  {_spd_label}  score={sc:+d}  -> {verdict}")

        # Log gate decision
        try:
            ice = decision.iceberg
            pats = [ice.primary_type] if ice.detected else []
            if getattr(ice, "sweep_dir", "none") != "none":
                pats.append("sweep_%s" % ice.sweep_dir)
            self.executor.log_gate(
                direction=direction,
                gate_decision="CONFIRMED" if decision.go else "FILTERED",
                score=sc,
                macro_delta=decision.macro_delta,
                mom_status=decision.momentum.status,
                v4_status=v4,
                reason=decision.reason,
                zone="liq_top" if direction == "SHORT" else "liq_bot",
                patterns=",".join(pats),
            )
            if self.executor_live is not None:
                self.executor_live.log_gate(
                    direction=direction,
                    gate_decision="CONFIRMED" if decision.go else "FILTERED",
                    score=sc,
                    macro_delta=decision.macro_delta,
                    mom_status=decision.momentum.status,
                    v4_status=v4,
                    reason=decision.reason,
                    zone="liq_top" if direction == "SHORT" else "liq_bot",
                    patterns=",".join(pats),
                )
        except Exception:
            pass

        # Write decision to Single Source of Truth (BLOCK or GO)
        _decision_dict = self._build_decision_dict(
            direction=direction, price=price, decision=decision,
            trigger="ALPHA", expansion_lines_mt5=_exp_lines,
        )
        self._write_decision(_decision_dict)

        if not decision.go:
            print(f"[{ts}] BLOCK: {decision.reason}")
            tg.notify_decision()
            return

        # === Telegram Decoupling (Fase 2): notify GO signal BEFORE execution ===
        # Signal is independent of broker. Barbara receives immediately.
        # Execution result notified separately via notify_execution() below.
        print(f"[{ts}] GO SIGNAL: {decision.reason}")
        tg.notify_decision()

        # Gate passed (GO) — lock per-direction cooldown so only GO resets it
        with self._lock:
            self._last_trigger_by_dir[direction] = time.monotonic()

        # -- V3 RL entry decision (shadow / live modes) -----------------
        _v3_source = getattr(self, '_trigger_gate_source', 'QT_MICRO')  # set by caller
        _ice_event = getattr(self, '_last_ice_event', None)
        if self.v3_agent is not None:
            try:
                v3_result = self.v3_agent.decide_entry(
                    price        = price,
                    m30_levels   = self._v3_m30_levels(price),
                    l2_snapshot  = self._v3_l2_snapshot(direction, _ice_event),
                    iceberg_scan = self._v3_iceberg_scan(_ice_event),
                    utc_hour     = datetime.now(timezone.utc).hour,
                    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    trade_group_id = ts,
                )
                v3_action = v3_result.get("action", "HOLD")
                v3_mode   = self.v3_agent.mode
                print(f"[{ts}] V3={v3_action}  conf={v3_result.get('confidence', 0):.2f}"
                      f"  mode={v3_mode}")
                # In live mode: if V3 says HOLD, block the trade
                if v3_mode == "live" and v3_action == "HOLD":
                    print(f"[{ts}] V3 LIVE VETO: agent chose HOLD -- skipping execution")
                    log.info("V3 live veto: HOLD at gate PASS  price=%.2f", price)
                    return
            except Exception as _v3_e:
                log.warning("V3 entry hook error: %s", _v3_e)

        # --- Sizing (Sprint 9: adjust targets for CONTINUATION mode) ---
        _is_continuation = "CONTINUATION" in strategy_reason

        # Kill switch: check file-based disable (touch C:/FluxQuantumAI/DISABLE_CONTINUATION)
        if _is_continuation and Path("C:/FluxQuantumAI/DISABLE_CONTINUATION").exists():
            print(f"[{ts}] CONTINUATION KILL SWITCH: DISABLE_CONTINUATION file exists -- aborting")
            log.warning("CONTINUATION killed by DISABLE_CONTINUATION file")
            return

        if _is_continuation:
            _atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
            _tp1_mult = float(self._thresholds.get("trend_cont_tp1_atr_mult", 0.8))
            _tp2_mult = float(self._thresholds.get("trend_cont_tp2_atr_mult", 1.5))
            _stop_mode = self._thresholds.get("trend_cont_stop_mode", "displacement_bar")
            _tp1_pts = _atr * _tp1_mult
            _tp2_pts = _atr * _tp2_mult
            # SL: behind displacement bar or default
            if _stop_mode == "displacement_bar":
                _, _, _disp_lo, _disp_hi = self._detect_trend_displacement(direction)
                if direction == "LONG" and _disp_lo > 0:
                    sl = _disp_lo - 1.0  # 1pt buffer below displacement low
                elif direction == "SHORT" and _disp_hi > 0:
                    sl = _disp_hi + 1.0  # 1pt buffer above displacement high
                else:
                    sl = price + self.sl_pts if direction == "SHORT" else price - self.sl_pts
            else:
                sl = price + self.sl_pts if direction == "SHORT" else price - self.sl_pts
            tp1 = price - _tp1_pts if direction == "SHORT" else price + _tp1_pts
            tp2 = price - _tp2_pts if direction == "SHORT" else price + _tp2_pts
            log.info("[CONTINUATION_TARGETS] sl=%.2f tp1=%.2f tp2=%.2f atr=%.1f mode=%s",
                     sl, tp1, tp2, _atr, _stop_mode)
        else:
            sl   = price + self.sl_pts   if direction == "SHORT" else price - self.sl_pts
            tp1  = price - self.tp1_pts  if direction == "SHORT" else price + self.tp1_pts
            tp2  = price - self.tp2_pts  if direction == "SHORT" else price + self.tp2_pts
        _dyn_lots = self._compute_session_lots(
            ice_aligned=decision.iceberg.aligned if decision.iceberg.detected else False,
        )
        if _dyn_lots:
            l1, l2, l3 = _dyn_lots[0], _dyn_lots[1], _dyn_lots[2]
        else:
            l1, l2, l3 = _split_lots(self.lot_size)
        legs = 3 if l3 >= 0.01 else 2

        if self.dry_run:
            _total = sum([l1, l2, l3])
            print(f"[{ts}] DRY RUN: WOULD OPEN {direction} {_total:.2f} lots @ {price:.2f}")
            print(f"[{ts}] Leg1={l1:.2f} Leg2={l2:.2f} Leg3={l3:.2f}  SL={sl:.1f}  TP1={tp1:.1f}  TP2={tp2:.1f}")

        elif self.execute:
            # -- News Gate ------------------------------------------------
            if _news_gate is not None:
                _ng = _news_gate.check()
                if _ng.exit_all:
                    log.warning("NEWS EXIT_ALL before open: %s", _ng.reason)
                    print(f"[{ts}] NEWS EXIT_ALL -- closing all and blocking entry: {_ng.reason}")
                    self._close_all_apex_positions(reason=_ng.reason)
                    return
                if _ng.block_entry:
                    log.warning("NEWS BLOCK_ENTRY: score=%.2f  reason=%s", _ng.score, _ng.reason)
                    print(f"[{ts}] NEWS BLOCK_ENTRY (score={_ng.score:.2f}): {_ng.reason}")
                    return

            # FIX 4: Re-fetch current GC price before order -- abort if moved too far from level
            # Adaptive threshold: 0.5 * ATR_M30 (adapts to market volatility)
            current_gc = self._metrics.get("gc_mid", 0.0)
            if current_gc > 0:
                signal_level_gc = self.liq_top_gc if direction == "SHORT" else self.liq_bot_gc
                distance_to_level = abs(current_gc - signal_level_gc)
                _atr_m30 = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
                _abort_thr = max(_atr_m30 * 0.5, 3.0)  # floor at 3pts for safety
                if distance_to_level > _abort_thr:
                    print(f"[{ts}] ABORT_STALE: price moved to {current_gc:.2f},"
                          f" {distance_to_level:.1f}pts from {signal_level_gc:.2f}"
                          f" (thr={_abort_thr:.1f} = 0.5*ATR_M30={_atr_m30:.1f})")
                    log.warning("ABORT_STALE: gc_mid=%.2f  delta=%.1f  level=%.2f  thr=%.1f",
                                current_gc, distance_to_level, signal_level_gc, _abort_thr)
                    return

            _dyn_lots = self._compute_session_lots(
                ice_aligned=decision.iceberg.aligned if decision.iceberg.detected else False,
            )
            # Build strategy context for position monitor tracking
            _strategy_ctx = {
                "entry_mode": ("PULLBACK" if "PULLBACK" in strategy_reason
                               else "CONTINUATION" if "CONTINUATION" in strategy_reason
                               else "OVEREXTENSION" if "OVEREXTENDED" in strategy_reason
                               else "RANGE" if "RANGE_BOUND" in strategy_reason
                               else "ALPHA"),
                "daily_trend": self.daily_trend,
                "phase": self._last_phase,
                "strategy_mode": strategy_reason.split(":")[0] if strategy_reason else "",
            }
            _decision_dict["decision"]["entry_mode"] = _strategy_ctx["entry_mode"]
            _decision_dict["decision"]["strategy_context"] = _strategy_ctx
            _exec_report = self._open_on_all_accounts(
                direction=direction, sl=sl, tp1=tp1, tp2=tp2,
                gate_score=sc, explicit_lots=_dyn_lots,
                strategy_context=_strategy_ctx,
            )
            _decision_dict["decision"]["execution"] = {
                "overall_state": _exec_report.get("overall_state", "UNKNOWN_ERROR"),
                "attempted": bool(_exec_report.get("attempted", False)),
                "brokers": _exec_report.get("brokers", []),
                "updated_at": _exec_report.get("updated_at"),
            }
            if _exec_report.get("success_any", False):
                # Update decision with execution details
                _tg_lots = _dyn_lots if _dyn_lots else [l1, l2, l3]
                _decision_dict["decision"]["sl"] = round(sl, 2)
                _decision_dict["decision"]["tp1"] = round(tp1, 2)
                _decision_dict["decision"]["tp2"] = round(tp2, 2)
                _decision_dict["decision"]["lots"] = [round(x, 2) for x in _tg_lots]
                _decision_dict["decision"]["action"] = "EXECUTED"
                self._write_decision(_decision_dict)

                # Separate execution confirmation message (Fase 2 Telegram Decoupling)
                tg.notify_execution()

                # Dedicated continuation trade log (separate tracking)
                if _is_continuation:
                    _disp_valid, _disp_reason, _, _ = self._detect_trend_displacement(direction)
                    _exh, _exh_reason = self._detect_local_exhaustion(direction, decision)
                    _cont_record = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "decision_id": _decision_dict.get("decision_id", ""),
                        "phase": self._last_phase,
                        "direction": direction,
                        "price": round(price, 2),
                        "sl": round(sl, 2),
                        "tp1": round(tp1, 2),
                        "tp2": round(tp2, 2),
                        "lots": [round(x, 2) for x in (_dyn_lots if _dyn_lots else [l1, l2, l3])],
                        "displacement": {"valid": _disp_valid, "reason": _disp_reason},
                        "exhaustion": {"exhausted": _exh, "reason": _exh_reason},
                        "delta_4h": self._metrics.get("delta_4h", 0),
                        "atr_m30": self._metrics.get("atr_m30_parquet", 0),
                        "daily_trend": self.daily_trend,
                        "strategy_reason": strategy_reason,
                        "gate_score": sc,
                        "execution": "EXECUTED",
                    }
                    try:
                        with open(CONTINUATION_LOG, "a", encoding="utf-8") as _clf:
                            _clf.write(json.dumps(_cont_record) + "\n")
                        log.info("CONTINUATION TRADE LOGGED: %s %s price=%.2f",
                                 direction, self._last_phase, price)
                    except Exception as _cle:
                        log.warning("continuation log write failed: %s", _cle)

                # Set per-direction lock to prevent re-entry for DIRECTION_LOCK_S
                with self._lock:
                    self._direction_lock_until[direction] = (
                        time.monotonic() + DIRECTION_LOCK_S
                    )
                print(f"[{ts}] dir_lock={DIRECTION_LOCK_S:.0f}s")
                # Sprint 8: Update trade cooldown state
                self._last_trade_time = time.monotonic()
                self._last_trade_level = price
                self._last_trade_direction = direction
                # Recalibration counter
                self._live_trade_count += 1
                interval = 30
                if self._live_trade_count % interval == 0:
                    msg = (f"RECALIBRATION DUE -- {self._live_trade_count} live trades."
                           f" Run threshold study again.")
                    log.warning(msg)
                    print(f"[{ts}] *** {msg} ***")
                    try:
                        thr = dict(self._thresholds)
                        thr["next_recalibration"] = (
                            f"after {self._live_trade_count + interval} live trades"
                        )
                        with open(THRESHOLDS_PATH, "w", encoding="utf-8") as fw:
                            json.dump(thr, fw, indent=2)
                    except Exception as e:
                        log.warning("could not update thresholds file: %s", e)
            else:
                # CRITICAL: GO decision but execution failed on ALL accounts
                _decision_dict["decision"]["action"] = "EXEC_FAILED"
                _decision_dict["decision"]["reason"] = (
                    f"GO {direction} but NO broker executed: "
                    f"robo={'connected' if self.executor.connected else 'DISCONNECTED'} "
                    f"hantec={'connected' if self.executor_live is not None and self.executor_live.connected else 'DISCONNECTED'}"
                )
                self._write_decision(_decision_dict)
                log.error("EXEC_FAILED: GO %s score=%d but no broker executed", direction, sc)
                print(f"[{ts}] EXEC_FAILED: GO {direction} — NO BROKER CONNECTED")
                # Separate execution failure message (Fase 2 Telegram Decoupling)
                tg.notify_execution()

    # ------------------------------------------------------------------
    # Expansion lines helper (BUG 2 support)
    # ------------------------------------------------------------------

    def _compute_expansion_lines(self, direction: str, n: int = 8) -> list:
        """
        Return the last N expansion line levels for the given direction.
        SHORT: bars where close < low_prev  -> low_prev becomes resistance (expansion line)
        LONG:  bars where close > high_prev -> high_prev becomes support (expansion line)
        Used by gate.check() for pullback entry zone in TRENDING mode.

        Dual-timeframe: uses M5 bars (last 80 ≈ 6.7h) for finer, more recent
        expansion lines vs prior M30 (40 bars = 20h). Falls back to M30 if M5 unavailable.
        """
        for path in (M5_BOXES_PATH, M30_BOXES_PATH):
            if not path.exists():
                continue
            try:
                df = pd.read_parquet(path)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df = df.tail(80)   # 80 M5 bars ≈ 6.7h  |  80 M30 bars = 40h (fallback)
                lines = []
                for i in range(1, len(df)):
                    curr = df.iloc[i]
                    prev = df.iloc[i - 1]
                    if direction == "SHORT" and float(curr["close"]) < float(prev["low"]):
                        lines.append(float(prev["low"]))
                    elif direction == "LONG" and float(curr["close"]) > float(prev["high"]):
                        lines.append(float(prev["high"]))
                return lines[-n:]
            except Exception:
                continue
        return []

    # ------------------------------------------------------------------
    # Master Pattern Phase -- derived from M30 box state + price position
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # TREND CONTINUATION ENGINE (Sprint 9)
    # ------------------------------------------------------------------

    def _detect_trend_displacement(self, direction: str) -> tuple:
        """
        Detect if there was a recent valid displacement bar for continuation entry.

        FASE 3 LIVE (2026-04-14): Option A active — M30 bar vs ATR M30.
        Comparative logging maintained for all 3 modes (CURRENT/OPT_A/OPT_B).

        Returns:
            (valid: bool, reason: str, displacement_low: float, displacement_high: float)
        """
        tc = self._thresholds
        atr_m30 = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
        disp_mult = float(tc.get("trend_cont_displacement_atr_mult", 0.8))
        min_delta = float(tc.get("trend_cont_min_delta_1bar", 80))
        close_pct = float(tc.get("trend_cont_close_near_extreme_pct", 0.7))

        # ── CURRENT (behavioral, unchanged): M5 bar vs ATR M30 ──
        min_range_current = atr_m30 * disp_mult
        current_result = self._check_displacement_bars(
            M5_BOXES_PATH if M5_BOXES_PATH.exists() else M30_BOXES_PATH,
            direction, min_range_current, min_delta, close_pct,
            bar_delta_required=True)

        # ── SHADOW A (Option A): M30 bar vs ATR M30 ──
        min_range_a = atr_m30 * disp_mult
        shadow_a = self._check_displacement_bars(
            M30_BOXES_PATH, direction, min_range_a, min_delta, close_pct,
            bar_delta_required=False)  # M30 has no bar_delta, skip delta check

        # ── SHADOW B (Option B): M5 bar vs ATR M5 ──
        shadow_b_result = (False, "no M5 ATR", 0, 0)
        try:
            _m5_path = M5_BOXES_PATH if M5_BOXES_PATH.exists() else None
            if _m5_path:
                _m5_df = pd.read_parquet(_m5_path, columns=["atr14"])
                _m5_atr = float(_m5_df["atr14"].dropna().iloc[-1]) if not _m5_df.empty else 5.0
                min_range_b = _m5_atr * disp_mult
                shadow_b_result = self._check_displacement_bars(
                    _m5_path, direction, min_range_b, min_delta, close_pct,
                    bar_delta_required=True)
            else:
                _m5_atr = 0
                min_range_b = 0
        except Exception:
            _m5_atr = 0
            min_range_b = 0

        # ── SHADOW LOG ──
        log.info(
            "DISPLACEMENT_SHADOW: dir=%s | "
            "CURRENT(M5vsATR_M30): %s range_thr=%.1f atr=%.1f(M30) | "
            "OPT_A(M30vsATR_M30): %s range_thr=%.1f atr=%.1f(M30) | "
            "OPT_B(M5vsATR_M5): %s range_thr=%.1f atr=%.1f(M5)",
            direction,
            "PASS" if current_result[0] else "FAIL", min_range_current, atr_m30,
            "PASS" if shadow_a[0] else "FAIL", min_range_a, atr_m30,
            "PASS" if shadow_b_result[0] else "FAIL", min_range_b, _m5_atr if '_m5_atr' in dir() else 0,
        )

        # Log when modes disagree (the interesting cases)
        c_pass = current_result[0]
        a_pass = shadow_a[0]
        b_pass = shadow_b_result[0]
        if c_pass != a_pass or c_pass != b_pass:
            log.warning(
                "DISPLACEMENT_DIVERGE: dir=%s CURRENT=%s OPT_A=%s OPT_B=%s | "
                "current_reason=%s | opt_a_reason=%s | opt_b_reason=%s",
                direction,
                "PASS" if c_pass else "FAIL",
                "PASS" if a_pass else "FAIL",
                "PASS" if b_pass else "FAIL",
                current_result[1], shadow_a[1], shadow_b_result[1],
            )

        # FASE 3 LIVE: Option A (M30 bar vs ATR M30) is now the behavioral output.
        # Old CURRENT (M5 vs ATR M30) kept for comparison logging only.
        # Rollback: change shadow_a back to current_result if regression detected.
        return shadow_a

    def _check_displacement_bars(
        self, parquet_path, direction: str,
        min_range: float, min_delta: float, close_pct: float,
        bar_delta_required: bool = True,
    ) -> tuple:
        """
        Check last 3 completed bars from a parquet for displacement.
        Shared logic for all 3 displacement modes (CURRENT, OPT_A, OPT_B).

        Args:
            parquet_path: Path to M5 or M30 boxes parquet
            direction: "LONG" or "SHORT"
            min_range: minimum bar range to qualify
            min_delta: minimum |bar_delta| (skipped if bar_delta_required=False)
            close_pct: close must be within this % of the extreme
            bar_delta_required: if False, skip delta check (M30 has no bar_delta)

        Returns:
            (valid: bool, reason: str, displacement_low: float, displacement_high: float)
        """
        try:
            df = pd.read_parquet(parquet_path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            recent = df.tail(4).iloc[:-1]  # last 3 completed bars
            if len(recent) == 0:
                return (False, "no recent bars", 0, 0)
        except Exception as e:
            return (False, f"parquet error: {e}", 0, 0)

        for i in range(len(recent) - 1, -1, -1):
            bar = recent.iloc[i]
            o = float(bar.get("open", 0))
            c = float(bar.get("close", 0))
            h = float(bar.get("high", c))
            lo = float(bar.get("low", o))
            bar_range = h - lo
            delta = float(bar.get("bar_delta", bar.get("delta", 0)))

            if bar_range < min_range:
                continue

            if direction == "LONG":
                if c <= o:
                    continue
                if bar_delta_required and abs(delta) > 0 and delta < min_delta:
                    continue
                if bar_range > 0 and (c - lo) / bar_range < close_pct:
                    continue
                return (True,
                        f"displacement LONG: range={bar_range:.1f} > {min_range:.1f} delta={delta:+.0f}",
                        lo, h)
            else:
                if c >= o:
                    continue
                if bar_delta_required and abs(delta) > 0 and delta > -min_delta:
                    continue
                if bar_range > 0 and (h - c) / bar_range < close_pct:
                    continue
                return (True,
                        f"displacement SHORT: range={bar_range:.1f} > {min_range:.1f} delta={delta:+.0f}",
                        lo, h)

        return (False, "no valid displacement bar in last 3", 0, 0)

    def _detect_local_exhaustion(self, direction: str, decision=None) -> tuple:
        """
        Detect if the recent move is too exhausted for a continuation entry.
        Sprint 9 v1: conservative filter using only robust, known signals.

        ACTIVE (block):
          1. overextension_atr_mult — price too far from liq zone
          2. impulse_30min — explosive bar against entry (from V3 momentum)
          3. iceberg contra forte — strong institutional contra signal

        SHADOW (log only, for future calibration):
          1. delta_weakening
          2. delta_4h_exhaustion as local contributor
          3. vol_climax

        Returns:
            (exhausted: bool, reason: str)
        """
        tc = self._thresholds
        atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
        xau_price = self._metrics.get("xau_mid", 0.0)
        shadow_signals = []

        # ── ACTIVE 1: Overextension ──
        overext_mult = float(tc.get("overextension_atr_mult", 1.5))
        overext_thr = atr * overext_mult
        if direction == "LONG" and self.liq_top:
            dist = abs(xau_price - self.liq_top)
            if xau_price > self.liq_top and dist > overext_thr:
                return (True, f"overextended LONG: {dist:.1f}pts > {overext_thr:.1f} ({overext_mult}x ATR)")
        elif direction == "SHORT" and self.liq_bot:
            dist = abs(self.liq_bot - xau_price)
            if xau_price < self.liq_bot and dist > overext_thr:
                return (True, f"overextended SHORT: {dist:.1f}pts > {overext_thr:.1f} ({overext_mult}x ATR)")

        # ── ACTIVE 2: Impulse 30min (from V3 momentum if available) ──
        if decision is not None:
            mom = getattr(decision, "momentum", None)
            if mom and getattr(mom, "status", "ok") == "block":
                reason = getattr(mom, "reason", "")
                if "impulse" in reason.lower():
                    return (True, f"impulse_30min blocks continuation: {reason}")

        # ── ACTIVE 3: Iceberg contra forte ──
        if decision is not None:
            ice = getattr(decision, "iceberg", None)
            if ice and getattr(ice, "detected", False) and not getattr(ice, "aligned", True):
                conf = getattr(ice, "confidence", 0)
                if conf > 0.5:
                    return (True, f"iceberg contra forte: conf={conf:.2f} {getattr(ice, 'reason', '')}")

        # ── SHADOW 1: delta_weakening (log only) ──
        d4h = self._metrics.get("delta_4h", 0)
        weakening_thr = float(tc.get("delta_weakening_threshold", 0.139486))
        try:
            from live.m30_updater import _MICRO_DIR
            micro_path = _MICRO_DIR / f"microstructure_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv.gz"
            if micro_path.exists():
                _mdf = pd.read_csv(micro_path, compression="gzip")
                if "bar_delta" in _mdf.columns and len(_mdf) > 20:
                    recent_delta = _mdf["bar_delta"].tail(10).sum()
                    older_delta = _mdf["bar_delta"].tail(20).head(10).sum()
                    if older_delta != 0:
                        weakening_rate = 1.0 - abs(recent_delta / older_delta)
                        if weakening_rate > weakening_thr:
                            shadow_signals.append(
                                f"delta_weakening={weakening_rate:.3f} > {weakening_thr:.3f}")
        except Exception:
            pass

        # ── SHADOW 2: delta_4h_exhaustion as local signal ──
        exh_high = float(tc.get("delta_4h_exhaustion_high", 3000))
        exh_low = float(tc.get("delta_4h_exhaustion_low", -1050))
        if direction == "LONG" and d4h > exh_high * 0.8:
            shadow_signals.append(f"d4h_near_exhaustion={d4h:+.0f} (80% of {exh_high})")
        elif direction == "SHORT" and d4h < exh_low * 0.8:
            shadow_signals.append(f"d4h_near_exhaustion={d4h:+.0f} (80% of {exh_low})")

        # ── SHADOW 3: vol_climax ──
        vol_climax_mult = float(tc.get("vol_climax_multiplier", 0.682))
        try:
            if micro_path.exists():
                if "bar_delta" in _mdf.columns and len(_mdf) > 30:
                    rolling_std = _mdf["bar_delta"].tail(30).std()
                    last_delta = abs(float(_mdf["bar_delta"].iloc[-1]))
                    if rolling_std > 0 and last_delta > rolling_std * (1 + vol_climax_mult):
                        shadow_signals.append(
                            f"vol_climax: last_delta={last_delta:.0f} > {rolling_std*(1+vol_climax_mult):.0f}")
        except Exception:
            pass

        # Log shadow signals for future calibration
        if shadow_signals:
            log.info("[EXHAUSTION_SHADOW] direction=%s signals=[%s]",
                     direction, " | ".join(shadow_signals))

        return (False, "not exhausted (active checks passed)")

    def _log_continuation_attempt(
        self, direction: str, phase: str, price: float,
        decision: str, reason: str,
        disp_valid: bool = False, disp_reason: str = "",
        exhausted: bool = False, exh_reason: str = "",
    ) -> None:
        """Log every TREND_CONTINUATION attempt (GO or SKIP) to dedicated file."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "direction": direction,
            "price": round(price, 2),
            "delta_4h": self._metrics.get("delta_4h", 0),
            "atr_m30": round(self._metrics.get("atr_m30_parquet",
                              self._metrics.get("atr", 0)), 2),
            "daily_trend": self.daily_trend,
            "displacement": {"valid": disp_valid, "reason": disp_reason},
            "exhaustion": {"exhausted": exhausted, "reason": exh_reason},
            "decision": decision,
            "reason": reason,
        }
        try:
            with open(CONTINUATION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def _get_trend_entry_mode(self, level_type: str, price: float,
                              trend_direction: str, decision=None) -> tuple:
        """
        Determine if TRENDING mode should use PULLBACK, CONTINUATION, or SKIP.
        Sprint 9: Trend Continuation Engine.

        Returns:
            (entry_mode: str, direction: str or None, reason: str)
        """
        tc = self._thresholds

        if not tc.get("trend_continuation_enabled", False):
            # Feature OFF — behave exactly like before Sprint 9
            return ("DISABLED", None, "trend_continuation_enabled=false")

        phase = self._get_current_phase()

        # 1. Check PULLBACK first (existing logic)
        if trend_direction == "LONG" and level_type == "liq_bot":
            return ("PULLBACK", "LONG", "TRENDING_UP: liq_bot = buy the dip")
        if trend_direction == "SHORT" and level_type == "liq_top":
            return ("PULLBACK", "SHORT", "TRENDING_DN: liq_top = sell the rally")

        # 2. Check CONTINUATION conditions
        if phase not in ("TREND", "EXPANSION"):
            return ("SKIP", None, f"phase={phase} not TREND/EXPANSION")

        direction = trend_direction  # continuation follows the trend

        # 2a. Displacement check
        disp_valid, disp_reason, disp_low, disp_high = self._detect_trend_displacement(direction)
        if not disp_valid:
            self._log_continuation_attempt(
                direction, phase, price, "SKIP", f"no displacement: {disp_reason}",
                disp_valid=False, disp_reason=disp_reason)
            return ("SKIP", None, f"no displacement: {disp_reason}")

        # 2b. Exhaustion check
        exhausted, exh_reason = self._detect_local_exhaustion(direction, decision)
        if exhausted:
            self._log_continuation_attempt(
                direction, phase, price, "SKIP", f"exhaustion: {exh_reason}",
                disp_valid=True, disp_reason=disp_reason, exhausted=True, exh_reason=exh_reason)
            return ("SKIP", None, f"exhaustion: {exh_reason}")

        # 2c. Momentum check (V3 must not block)
        if tc.get("trend_cont_require_v3_pass", True) and decision is not None:
            mom_status = getattr(decision, "momentum", None)
            if mom_status and getattr(mom_status, "status", "ok") == "block":
                self._log_continuation_attempt(
                    direction, phase, price, "SKIP", f"V3 momentum: {mom_status.reason}",
                    disp_valid=True, disp_reason=disp_reason)
                return ("SKIP", None, f"V3 momentum blocks: {mom_status.reason}")

        # 2d. Iceberg contra check
        if decision is not None:
            ice = getattr(decision, "iceberg", None)
            allow_neutral = tc.get("trend_cont_allow_neutral_iceberg", True)
            if ice and getattr(ice, "detected", False):
                if not getattr(ice, "aligned", True):
                    if not allow_neutral or getattr(ice, "confidence", 0) > 0.5:
                        self._log_continuation_attempt(
                            direction, phase, price, "SKIP", f"iceberg contra: {ice.reason}",
                            disp_valid=True, disp_reason=disp_reason)
                        return ("SKIP", None, f"iceberg contra: {ice.reason}")

        reason = f"CONTINUATION {direction}: {disp_reason}"
        log.info("[STRATEGY] mode=TRENDING entry_mode=CONTINUATION direction=%s reason=%s",
                 direction, reason)
        self._log_continuation_attempt(
            direction, phase, price, "GO", reason,
            disp_valid=True, disp_reason=disp_reason)
        return ("CONTINUATION", direction, reason)

    # Phase Engine state (Sprint 9: stability)
    _phase_current: str = "NEW_RANGE"
    _phase_candidate: str = ""
    _phase_candidate_since: float = 0.0
    _PHASE_HYSTERESIS_S: float = 120.0  # 2 min minimum before phase change
    _bars_outside_box: int = 0          # consecutive M30 bars with close outside box
    _TREND_ACCEPTANCE_MIN_BARS: int = 4 # PATCH 1: calibrated CAL-PATCH1 (was 2, now 4: 83.3% fake filter, 100% valid capture)

    def _get_current_phase(self) -> str:
        """
        Derive current market phase from M30 box state + price position.
        Sprint 9: Phase Engine with hysteresis for stability.

        CONTRACTION : price within M30 box range — no expansion yet
        EXPANSION   : unconfirmed box OR price outside box without known trend
        TREND       : confirmed box + known daily_trend direction
        NEW_RANGE   : no box data available

        Stability rules:
          - Uses M30 (macro structure), NOT M5 (too noisy)
          - Phase only changes after candidate persists for 2 min (hysteresis)
          - Persists last valid phase on errors
        """
        raw_phase = self._compute_raw_phase()

        # Hysteresis: phase only changes if new phase persists for N seconds
        now = time.monotonic()
        if raw_phase == self._phase_current:
            # Same as current — reset candidate
            self._phase_candidate = ""
            self._phase_candidate_since = 0.0
            return self._phase_current

        # Different phase detected
        if raw_phase != self._phase_candidate:
            # New candidate — start timer
            self._phase_candidate = raw_phase
            self._phase_candidate_since = now
            return self._phase_current  # keep old phase

        # Same candidate persisting — check if hysteresis elapsed
        elapsed = now - self._phase_candidate_since
        if elapsed >= self._PHASE_HYSTERESIS_S:
            old = self._phase_current
            self._phase_current = raw_phase
            self._phase_candidate = ""
            self._phase_candidate_since = 0.0
            log.info("[PHASE_ENGINE] %s -> %s (after %.0fs hysteresis)", old, raw_phase, elapsed)
            return self._phase_current

        # Still within hysteresis window — keep old phase
        return self._phase_current

    def _compute_raw_phase(self) -> str:
        """
        Compute phase from M30 parquet. No persistence, no hysteresis.
        Architecture: D1/4H = directional bias, M30 = phase + structure.

        ATS Master Pattern:
          CONTRACTION : price within M30 box (buyers/sellers agree on value)
          EXPANSION   : price left box (disagreement on value, exploring)
          TREND       : sustained move — successive confirmed boxes at better prices
                        ("walking the ladder") + price accepted outside box
          NEW_RANGE   : no box data or structural reset
        """
        try:
            df = pd.read_parquet(M30_BOXES_PATH)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")

            recent = df[df["m30_liq_top"].notna()]
            if recent.empty:
                return "NEW_RANGE"

            row = recent.iloc[-1]
            box_high = float(row["m30_box_high"]) if pd.notna(row.get("m30_box_high")) else None
            box_low = float(row["m30_box_low"]) if pd.notna(row.get("m30_box_low")) else None
            confirmed = bool(row.get("m30_box_confirmed", False))
            gc_price = self._metrics.get("gc_mid", 0.0)

            if gc_price <= 0:
                return self._phase_current  # no price yet

            # ── CONTRACTION: price within current box ──
            if box_high is not None and box_low is not None:
                if box_low <= gc_price <= box_high:
                    self._bars_outside_box = 0  # reset: price returned to box
                    return "CONTRACTION"

            # ── Price is OUTSIDE the box ──
            # Count consecutive M30 bars with close outside box (temporal acceptance)
            self._bars_outside_box = self._count_bars_outside_box(df, box_high, box_low)

            # TREND requires ALL:
            #   1. Current box confirmed (multi-leg expansion validated)
            #   2. Daily trend direction known
            #   3. Structural acceptance: successive boxes with progressive FMVs
            #   4. PATCH 1: temporal acceptance — price outside box for N bars
            if confirmed and self.daily_trend in ("long", "short"):
                if self._detect_box_ladder(self.daily_trend):
                    if self._bars_outside_box >= self._TREND_ACCEPTANCE_MIN_BARS:
                        return "TREND"
                    # Ladder OK but not enough time outside box yet
                    log.debug("[PHASE_ENGINE] TREND blocked: bars_outside=%d < %d required",
                              self._bars_outside_box, self._TREND_ACCEPTANCE_MIN_BARS)
                    return "EXPANSION"
                return "EXPANSION"

            # Not confirmed = breakout in progress, JAC pending
            return "EXPANSION"

        except Exception as e:
            log.debug("_compute_raw_phase error: %s", e)
            return self._phase_current

    def _count_bars_outside_box(self, df: "pd.DataFrame",
                                box_high: float, box_low: float) -> int:
        """
        Count consecutive recent M30 bars with close OUTSIDE the box.
        Counts backwards from the most recent completed bar.

        PATCH 1: temporal acceptance for EXPANSION -> TREND transition.
        Price must stay outside box for N bars to confirm structural acceptance,
        not just a spike/wick.
        """
        if box_high is None or box_low is None:
            return 0
        # Last 10 bars (enough for counting), exclude current incomplete bar
        recent = df.tail(11).iloc[:-1]
        count = 0
        for i in range(len(recent) - 1, -1, -1):
            bar = recent.iloc[i]
            close = float(bar.get("close", 0))
            if close <= 0:
                break
            if box_low <= close <= box_high:
                break  # bar closed inside box — stop counting
            count += 1
        return count

    def _detect_box_ladder(self, trend_direction: str, min_boxes: int = 3) -> bool:
        """
        Detect "walking the ladder" — successive confirmed boxes at progressively
        better prices (higher FMVs in uptrend, lower in downtrend).

        ATS: "Each new contraction box established at better prices, with price
        holding above previous value points (uptrend) or below them (downtrend)."

        Args:
            trend_direction: "long" or "short"
            min_boxes: minimum consecutive boxes in same direction (default 3)

        Returns:
            True if box ladder pattern detected
        """
        try:
            df = pd.read_parquet(M30_BOXES_PATH)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")

            # Get distinct confirmed boxes by box_id
            box_df = df[(df["m30_box_confirmed"] == True) & (df["m30_box_id"].notna())]
            if box_df.empty:
                return False

            boxes = box_df.groupby("m30_box_id").agg({
                "m30_fmv": "first",
            }).sort_index()

            if len(boxes) < min_boxes:
                return False

            # Check last min_boxes confirmed FMVs for progression
            last_fmvs = boxes["m30_fmv"].tail(min_boxes).tolist()

            if trend_direction == "long":
                # Each FMV should be higher than previous
                rising = all(last_fmvs[i] > last_fmvs[i - 1] for i in range(1, len(last_fmvs)))
                return rising
            else:
                # Each FMV should be lower than previous
                falling = all(last_fmvs[i] < last_fmvs[i - 1] for i in range(1, len(last_fmvs)))
                return falling

        except Exception as e:
            log.debug("_detect_box_ladder error: %s", e)
            return False

    # ------------------------------------------------------------------
    # STRATEGY SELECTOR -- Sprint 8: Dual Strategy (Range + Trend)
    # ------------------------------------------------------------------

    def _get_strategy_mode(self) -> tuple:
        """
        Determine which strategy to use based on phase and daily trend.
        Source: ATS Basic Strategy - Two Problems to Solve

        Returns:
            tuple: (strategy_mode, trend_direction)
            strategy_mode: "RANGE_BOUND" | "TRENDING"
            trend_direction: "LONG" | "SHORT" | None
        """
        if not self._thresholds.get("dual_strategy_enabled", False):
            return ("RANGE_BOUND", None)  # fallback = current behaviour

        phase = self._get_current_phase()
        self._last_phase = phase
        daily_trend = self.daily_trend  # "long", "short", or ""

        # CONTRACTION: always Range-Bound (ATS Strategy 1)
        if phase == "CONTRACTION":
            return ("RANGE_BOUND", None)

        # TREND with confirmed daily_trend: Trending strategy (ATS Strategy 2)
        if phase == "TREND" and daily_trend in ("long", "short"):
            trend_dir = "LONG" if daily_trend == "long" else "SHORT"
            return ("TRENDING", trend_dir)

        # EXPANSION with daily_trend: treat as trending
        # ATS: "You can get away with trading this if you have big multi-leg expansions"
        if phase == "EXPANSION" and daily_trend in ("long", "short"):
            trend_dir = "LONG" if daily_trend == "long" else "SHORT"
            return ("TRENDING", trend_dir)

        # No clear trend: Range-Bound for safety
        return ("RANGE_BOUND", None)

    def _resolve_direction(self, level_type: str) -> tuple:
        """
        Resolve trade direction from level_type using current strategy mode.
        Source: ATS Basic Strategy - Two Problems to Solve

        Returns:
            tuple: (direction, strategy_reason) or (None, reason) if SKIP
            direction=None means do NOT enter (liquidation zone in trending mode).
        """
        strategy_mode, trend_direction = self._get_strategy_mode()
        phase = self._get_current_phase()
        self._last_phase = phase

        if strategy_mode == "RANGE_BOUND":
            # ATS Strategy 1: Market Maker / Reversal
            # "Accumulate against deviation, liquidate at value"
            direction = "SHORT" if level_type == "liq_top" else "LONG"
            reason = "RANGE_BOUND: %s -> %s (reversal to FMV)" % (level_type, direction)

        elif strategy_mode == "TRENDING":
            # Sprint 9: Try PULLBACK first, then CONTINUATION, then overextension
            xau_price = self._metrics.get("xau_mid", 0.0)

            # Step 1: Check trend continuation engine (Sprint 9)
            tc_mode, tc_dir, tc_reason = self._get_trend_entry_mode(
                level_type, xau_price, trend_direction)

            if tc_mode == "PULLBACK":
                direction = tc_dir
                reason = tc_reason
            elif tc_mode == "CONTINUATION":
                direction = tc_dir
                reason = tc_reason
            elif tc_mode == "SKIP":
                # Step 2: Overextension reversal fallback (pre-Sprint 9 logic)
                overext_mult = float(self._thresholds.get("overextension_atr_mult", 1.5))
                # FASE 1 THR-3: stable ATR14 from M30 parquet
                atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
                overext_thr = atr * overext_mult
                # Shadow logging: overextension ATR comparison
                _atr_proxy = self._metrics.get("atr", 20.0)
                _overext_thr_proxy = _atr_proxy * overext_mult
                if abs(overext_thr - _overext_thr_proxy) > 1.0:
                    log.info("OVEREXT_COMPARE: atr_m30=%.1f thr=%.1f | atr_proxy=%.1f thr_proxy=%.1f",
                             atr, overext_thr, _atr_proxy, _overext_thr_proxy)

                if trend_direction == "LONG" and level_type == "liq_top":
                    overext_pts = abs(xau_price - self.liq_top) if self.liq_top else 0
                    if overext_pts > overext_thr:
                        direction = "SHORT"
                        reason = ("TRENDING_UP: liq_top OVEREXTENDED %.1fpts > %.1f (%.1f*ATR) "
                                  "-> reversal allowed" % (overext_pts, overext_thr, overext_mult))
                    else:
                        direction = None
                        reason = ("TRENDING_UP: liq_top = liquidation zone, "
                                  "overext=%.1fpts < %.1f -> SKIP (%s)" % (overext_pts, overext_thr, tc_reason))
                elif trend_direction == "SHORT" and level_type == "liq_bot":
                    overext_pts = abs(self.liq_bot - xau_price) if self.liq_bot else 0
                    if overext_pts > overext_thr:
                        direction = "LONG"
                        reason = ("TRENDING_DN: liq_bot OVEREXTENDED %.1fpts > %.1f (%.1f*ATR) "
                                  "-> reversal allowed" % (overext_pts, overext_thr, overext_mult))
                    else:
                        direction = None
                        reason = ("TRENDING_DN: liq_bot = liquidation zone, "
                                  "overext=%.1fpts < %.1f -> SKIP (%s)" % (overext_pts, overext_thr, tc_reason))
                else:
                    direction = None
                    reason = f"TRENDING: SKIP ({tc_reason})"
            else:
                # DISABLED — behave exactly like pre-Sprint 9
                overext_mult = float(self._thresholds.get("overextension_atr_mult", 1.5))
                # FASE 1 THR-3: stable ATR14 from M30 parquet
                atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
                overext_thr = atr * overext_mult

                if trend_direction == "LONG":
                    if level_type == "liq_bot":
                        direction = "LONG"
                        reason = "TRENDING_UP: liq_bot = undervalued, buy the dip"
                    else:
                        overext_pts = abs(xau_price - self.liq_top) if self.liq_top else 0
                        if overext_pts > overext_thr:
                            direction = "SHORT"
                            reason = ("TRENDING_UP: liq_top OVEREXTENDED %.1fpts > %.1f (%.1f*ATR) "
                                      "-> reversal allowed" % (overext_pts, overext_thr, overext_mult))
                        else:
                            direction = None
                            reason = ("TRENDING_UP: liq_top = liquidation zone, "
                                      "overext=%.1fpts < %.1f -> SKIP" % (overext_pts, overext_thr))
                elif trend_direction == "SHORT":
                    if level_type == "liq_top":
                        direction = "SHORT"
                        reason = "TRENDING_DN: liq_top = overvalued, sell the rally"
                    else:
                        overext_pts = abs(self.liq_bot - xau_price) if self.liq_bot else 0
                        if overext_pts > overext_thr:
                            direction = "LONG"
                            reason = ("TRENDING_DN: liq_bot OVEREXTENDED %.1fpts > %.1f (%.1f*ATR) "
                                      "-> reversal allowed" % (overext_pts, overext_thr, overext_mult))
                        else:
                            direction = None
                            reason = ("TRENDING_DN: liq_bot = liquidation zone, "
                                      "overext=%.1fpts < %.1f -> SKIP" % (overext_pts, overext_thr))
                else:
                    direction = "SHORT" if level_type == "liq_top" else "LONG"
                    reason = "TRENDING: fallback reversal (no trend_dir)"
        else:
            direction = "SHORT" if level_type == "liq_top" else "LONG"
            reason = "FALLBACK: reversal at %s" % level_type

        log.info(
            "[STRATEGY] mode=%s phase=%s daily_trend=%s level=%s direction=%s reason=%s",
            strategy_mode, phase, self.daily_trend, level_type, direction, reason,
        )
        return (direction, reason)

    # ------------------------------------------------------------------
    # GAMMA -- Momentum Stacking (FEATURE 7, 2026-04-08)
    # ------------------------------------------------------------------
    # Per ATS docs (ATS 6 transcript, FUNC_M30_Framework sec 7.3):
    #   Entry  : new M30 expansion bar in daily_trend direction
    #   SL     : prior expansion line (prior bar high/low) -- "where stacking originates"
    #   TP1    : entry ± ATR14  (proxy for next expansion line -- unknown at entry time)
    #   TP2    : m30_liq_top (LONG) / m30_liq_bot (SHORT)  -- opposite liq line
    #   Runner : no fixed TP, trailing (SHIELD activates after TP1 hit, same as ALPHA/BETA)
    #   Reset  : if price touches prior expansion line before fill -> abort
    # ------------------------------------------------------------------

    def check_gamma_trigger(self) -> Optional[dict]:
        """
        Check if the latest completed M30 bar is a GAMMA momentum stacking trigger.

        Conditions (FUNC_M30_Framework sec 7.3):
          1. daily_trend known (long | short)
          2. weekly_aligned == True
          3. close[-1] > high[-2] (LONG) / close[-1] < low[-2] (SHORT)
          4. R:R = TP2_dist / SL_dist >= GAMMA_MIN_RR  ("reasonable risk window")

        Returns dict with entry/sl/tp1/tp2 in MT5 XAUUSD space, or None.
        """
        if self.daily_trend not in ("long", "short"):
            return None

        # T-002: Phase gate -- block GAMMA during CONTRACTION
        phase = self._get_current_phase()
        self._last_phase = phase
        if phase == "CONTRACTION":
            log.debug("PHASE=%s, GAMMA=False, DELTA=False -- skip momentum triggers", phase)
            return None
        log.debug("PHASE=%s, GAMMA=True, DELTA=True", phase)

        # --- M5: Load last 3 bars for expansion bar detection (timing) ---
        # M5 bars give finer expansion bar signals vs M30 (6x more bar events per hour).
        # Falls back to M30 if M5 parquet unavailable.
        m5_path = M5_BOXES_PATH if M5_BOXES_PATH.exists() else M30_BOXES_PATH
        m5_liq_col = "m5_liq_top" if m5_path == M5_BOXES_PATH else "m30_liq_top"
        try:
            df = pd.read_parquet(m5_path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df = df[df[m5_liq_col].notna() & df["atr14"].notna()].tail(3)
            if len(df) < 2:
                return None
        except Exception as e:
            log.warning("GAMMA: M5 parquet load failed: %s", e)
            return None

        bar_prev = df.iloc[-2]
        bar_curr = df.iloc[-1]
        bar_time = df.index[-1]

        direction  = "LONG" if self.daily_trend == "long" else "SHORT"
        close_curr = float(bar_curr["close"])
        high_prev  = float(bar_prev["high"])
        low_prev   = float(bar_prev["low"])

        # Condition 3: new expansion bar in trend direction (M5 timing)
        if direction == "LONG":
            if close_curr <= high_prev:
                return None
            sl_gc = high_prev   # prior M5 expansion line
        else:
            if close_curr >= low_prev:
                return None
            sl_gc = low_prev    # prior M5 expansion line

        # Condition 2: weekly_aligned
        try:
            feat = pd.read_parquet(FEATURES_V4_PATH, columns=["weekly_aligned"])
            if not bool(feat["weekly_aligned"].iloc[-1]):
                log.debug("GAMMA: weekly_aligned=False -- skip")
                return None
        except Exception:
            pass  # if unavailable, allow through (fail open)

        # --- M30: TP2 structural targets (macro bias) ---
        # TP2 uses M30 liq levels -- wider structural targets vs M5 execution levels.
        liq_top_gc = self.liq_top_gc   # live value from processor state (updated by level_detector)
        liq_bot_gc = self.liq_bot_gc
        try:
            df_m30 = pd.read_parquet(M30_BOXES_PATH)
            if df_m30.index.tz is None:
                df_m30.index = df_m30.index.tz_localize("UTC")
            df_m30 = df_m30[df_m30["m30_liq_top"].notna()].tail(1)
            if not df_m30.empty:
                liq_top_gc = float(df_m30.iloc[-1]["m30_liq_top"])
                liq_bot_gc = float(df_m30.iloc[-1]["m30_liq_bot"])
        except Exception:
            pass   # fall back to processor state levels

        # --- ATR14 and entry from M5 bar ---
        atr14    = float(bar_curr["atr14"])
        entry_gc   = close_curr

        if direction == "LONG":
            tp1_gc = entry_gc + atr14 * GAMMA_TP1_FACTOR
            tp2_gc = liq_top_gc
            # Fallback: if structural top already below entry (price broke through),
            # use ATR extension so TP2 stays in the correct direction.
            if tp2_gc <= entry_gc:
                # BUG 1 FIX: structural top below entry (price broke through).
                # Use 2:1 SL extension so TP2 stays above entry in LONG direction.
                tp2_gc = entry_gc + abs(entry_gc - sl_gc) * 2.0
                log.debug("GAMMA LONG: liq_top below entry -- using 2:1 extension tp2=%.1f", tp2_gc)
        else:
            tp1_gc = entry_gc - atr14 * GAMMA_TP1_FACTOR
            tp2_gc = liq_bot_gc
            # Fallback: if structural bot already above entry (price broke through),
            # use ATR extension so TP2 stays in the correct direction.
            if tp2_gc >= entry_gc:
                # BUG 1 FIX: structural bot above entry (price broke through).
                # Use 2:1 SL extension so TP2 stays below entry in SHORT direction.
                tp2_gc = entry_gc - abs(entry_gc - sl_gc) * 2.0
                log.debug("GAMMA SHORT: liq_bot above entry -- using 2:1 extension tp2=%.1f", tp2_gc)

        sl_dist  = abs(entry_gc - sl_gc)
        tp2_dist = abs(tp2_gc   - entry_gc)

        if sl_dist < 0.01:
            return None   # degenerate (bars with no range)

        # SL guard: minimum 3pts to avoid whipsaw on tight bars
        if sl_dist < 3.0:
            log.debug("GAMMA: sl_dist=%.1f < 3.0 pts -- skip (whipsaw guard)", sl_dist)
            return None

        # Condition 4: reasonable R:R
        rr = tp2_dist / sl_dist
        if rr < GAMMA_MIN_RR:
            log.debug("GAMMA: R:R=%.2f < %.2f -- skip", rr, GAMMA_MIN_RR)
            return None

        # Convert GC -> MT5 XAUUSD space
        offset = self._gc_xauusd_offset
        return {
            "direction":  direction,
            "entry_gc":   entry_gc,
            "entry":      round(entry_gc - offset, 2),
            "sl_gc":      sl_gc,
            "sl":         round(sl_gc    - offset, 2),
            "tp1":        round(tp1_gc   - offset, 2),
            "tp2":        round(tp2_gc   - offset, 2),
            "atr14":      atr14,
            "sl_dist":    round(sl_dist, 2),
            "tp2_dist":   round(tp2_dist, 2),
            "rr":         round(rr, 2),
            "bar_time":   bar_time,
        }

    def _gamma_loop(self):
        """
        Background thread: polls for GAMMA trigger at each M5 bar close.
        Waits until the next 5-min mark + 5s (bar confirm delay).

        Dual-timeframe: checks at M5 bar boundaries (every 5 min) because
        check_gamma_trigger() now reads M5 bars for expansion bar detection.
        Previously M30 (every 30 min) -- change was needed to avoid missing
        10 of 12 M5 bar closes per hour.
        """
        log.info("GAMMA monitor started (checks at M5 bar close + 5s)")
        while self._running:
            # Align to next 5-min boundary
            now      = pd.Timestamp.utcnow()
            next_bar = now.ceil("5min")
            wait_s   = (next_bar - now).total_seconds() + 5.0
            time.sleep(max(wait_s, 5.0))

            if not self._running:
                break
            if self.feed_monitor is not None and not self.feed_monitor.gate_enabled:
                continue

            try:
                trigger = self.check_gamma_trigger()
            except Exception as e:
                log.warning("check_gamma_trigger error: %s", e)
                continue

            if trigger is None:
                continue

            # Deduplicate: same bar -> same trigger
            if self._last_gamma_bar == trigger["bar_time"]:
                continue
            self._last_gamma_bar = trigger["bar_time"]

            ts = _ts()
            log.info(
                "GAMMA trigger: dir=%s  entry_gc=%.2f  sl_gc=%.2f  tp1_gc=%.2f  tp2_gc=%.2f  rr=%.1f",
                trigger["direction"], trigger["entry_gc"], trigger["sl_gc"],
                trigger["entry_gc"] + (trigger["atr14"] if trigger["direction"] == "LONG" else -trigger["atr14"]),
                trigger["entry_gc"] + (trigger["tp2_dist"] if trigger["direction"] == "LONG" else -trigger["tp2_dist"]),
                trigger["rr"],
            )
            print(
                f"[{ts}] GAMMA MOMENTUM STACK: {trigger['direction']}"
                f"  entry_gc={trigger['entry_gc']:.2f}"
                f"  sl_gc={trigger['sl_gc']:.2f}"
                f"  rr={trigger['rr']:.1f}"
                f"  atr14={trigger['atr14']:.1f}"
            )
            threading.Thread(
                target=self._trigger_gate_gamma,
                args=(trigger,),
                daemon=True,
            ).start()

    def _trigger_gate_gamma(self, trigger: dict):
        """
        Execute GAMMA entry -- same 3-leg structure (0.02+0.02+0.01) as ALPHA/BETA.
        SHIELD activates after TP1 hit, identical to existing mechanism.
        Only TP/SL levels differ from ALPHA/BETA per ATS docs:
          SL  = prior expansion line
          TP1 = entry ± ATR14   (proxy for next expansion line)
          TP2 = m30_liq_top (LONG) / m30_liq_bot (SHORT)
        """
        now_ts = time.monotonic()
        with self._lock:
            if now_ts - self._last_trigger < GATE_COOLDOWN_S:
                return
            self._last_trigger = now_ts

        direction = trigger["direction"]
        price     = trigger["entry"]
        sl        = trigger["sl"]
        tp1       = trigger["tp1"]
        tp2       = trigger["tp2"]
        ts        = _ts()

        # Operational rules (same P0 checks as _trigger_gate)
        open_positions = self.executor.get_open_positions()
        margin_level   = _mt5_margin_level()
        op_blocked, op_reason = self.ops.check_can_enter(
            open_positions   = open_positions,
            margin_level     = margin_level,
            signal_price     = price,
            signal_direction = direction,
        )
        if op_blocked:
            print(f"[{ts}] GAMMA: {op_reason}")
            log.info("GAMMA blocked by ops: %s", op_reason)
            return

        # Per-direction lock
        with self._lock:
            lock_until = self._direction_lock_until.get(direction, 0.0)
        if time.monotonic() < lock_until:
            remaining = lock_until - time.monotonic()
            print(f"[{ts}] GAMMA DIR_LOCK {direction}: {remaining:.0f}s remaining")
            return

        # Abort if GC price already past prior expansion line (trigger invalidated)
        current_gc = self._metrics.get("gc_mid", 0.0)
        if current_gc > 0:
            sl_gc = trigger["sl_gc"]
            if direction == "LONG" and current_gc < sl_gc:
                print(f"[{ts}] GAMMA ABORT_STALE: GC={current_gc:.2f} already < sl_gc={sl_gc:.2f}")
                return
            if direction == "SHORT" and current_gc > sl_gc:
                print(f"[{ts}] GAMMA ABORT_STALE: GC={current_gc:.2f} already > sl_gc={sl_gc:.2f}")
                return

        _dyn_lots_g = self._compute_session_lots(ice_aligned=False)
        if _dyn_lots_g:
            l1, l2, l3 = _dyn_lots_g[0], _dyn_lots_g[1], _dyn_lots_g[2]
        else:
            l1, l2, l3 = _split_lots(self.lot_size)
        legs = 3 if l3 >= 0.01 else 2

        print(
            f"[{ts}] GAMMA GO: {direction}"
            f"  entry={price:.2f}  SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}"
            f"  R:R={trigger['rr']:.1f}  legs={legs}"
        )

        if self.dry_run:
            _total_g = sum([l1, l2, l3])
            print(f"[{ts}] DRY RUN: GAMMA WOULD OPEN {direction} {_total_g:.2f} @ {price:.2f}")
            print(f"[{ts}]   Leg1={l1:.2f} Leg2={l2:.2f} Leg3={l3:.2f}"
                  f"  SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}")

        elif self.execute:
            # -- News Gate ------------------------------------------------
            if _news_gate is not None:
                _ng = _news_gate.check()
                if _ng.exit_all:
                    log.warning("NEWS EXIT_ALL before GAMMA open: %s", _ng.reason)
                    print(f"[{ts}] NEWS EXIT_ALL (GAMMA) -- closing all: {_ng.reason}")
                    self._close_all_apex_positions(reason=_ng.reason)
                    return
                if _ng.block_entry:
                    log.warning("NEWS BLOCK_ENTRY (GAMMA): score=%.2f  reason=%s", _ng.score, _ng.reason)
                    print(f"[{ts}] NEWS BLOCK_ENTRY GAMMA (score={_ng.score:.2f}): {_ng.reason}")
                    return

            _dyn_lots_g = self._compute_session_lots(ice_aligned=False)
            _gamma_exec = self._open_on_all_accounts(
                direction=direction, sl=sl, tp1=tp1, tp2=tp2,
                gate_score=0, label="GAMMA",
                explicit_lots=_dyn_lots_g,
            )
            if _gamma_exec.get("success_any", False):
                _tg_lots_g = _dyn_lots_g if _dyn_lots_g else [l1, l2, l3]
                # GAMMA execution result (Fase 2 Telegram Decoupling)
                tg.notify_execution()
                with self._lock:
                    self._direction_lock_until[direction] = (
                        time.monotonic() + DIRECTION_LOCK_S
                    )
                self._live_trade_count += 1

    # ------------------------------------------------------------------
    # DELTA -- Trend Momentum Re-alignment (2026-04-09)
    # ------------------------------------------------------------------
    # Per ATS docs (FUNC_M30_Framework sec 7.4):
    #   Entry  : M30 bar crosses back through last expansion line in D1 direction
    #   SL     : prior swing against trend (swing high/low from TrendMomentumDetector)
    #   TP1    : next expansion line (from detector)
    #   TP2    : liq_bot (SHORT) / liq_top (LONG)  -- opposite liquidity
    #   Runner : trailing (SHIELD after TP1), same 3-leg structure 0.02+0.02+0.01
    #   Lock   : 4 M30 bars (2h) per direction after CONFIRMED order
    # ------------------------------------------------------------------

    def check_delta_trigger(self) -> Optional[dict]:
        """
        Check if the latest completed M30 bar is a DELTA trend momentum re-alignment.

        Conditions:
          1. daily_trend known (long | short)
          2. Phase != CONTRACTION (T-002 gate)
          3. TrendMomentumDetector.compute() returns detected=True
          4. sl_dist >= 3.0 pts (whipsaw guard)
          5. R:R = TP2_dist / SL_dist >= DELTA_MIN_RR

        Returns dict with entry/sl/tp1/tp2 in MT5 XAUUSD space, or None.
        """
        if self._trend_momentum_detector is None:
            log.debug("DELTA: TrendMomentumDetector not available -- skip")
            return None

        if self.daily_trend not in ("long", "short"):
            return None

        # T-002: Phase gate -- block DELTA during CONTRACTION
        phase = self._get_current_phase()
        self._last_phase = phase
        if phase == "CONTRACTION":
            log.debug("PHASE=%s, GAMMA=False, DELTA=False -- skip momentum triggers", phase)
            return None

        d1_trend = self.daily_trend.upper()   # "LONG" or "SHORT"

        # Compute expansion lines and convert GC -> MT5 space
        _exp_gc = self._compute_expansion_lines(d1_trend)
        _off = self._gc_xauusd_offset
        expansion_lines = [round(el - _off, 2) for el in _exp_gc] if _exp_gc else []

        try:
            result = self._trend_momentum_detector.compute(
                d1_trend=d1_trend,
                expansion_lines=expansion_lines if expansion_lines else None,
            )
        except Exception as e:
            log.warning("DELTA: TrendMomentumDetector.compute() failed: %s", e)
            return None

        if not result["detected"]:
            return None

        # Extract levels -- detector returns GC-space levels
        sl_gc  = result["sl_level"]
        tp1_gc = result["tp1_level"]
        tp2_gc = result["tp2_level"]

        if any(v != v for v in (sl_gc, tp1_gc, tp2_gc)):   # NaN check
            log.debug("DELTA: detector returned NaN levels -- skip")
            return None

        # Current entry = latest M5 close (timing -- finer entry price vs M30)
        # Falls back to M30 if M5 parquet unavailable.
        m5_path = M5_BOXES_PATH if M5_BOXES_PATH.exists() else M30_BOXES_PATH
        m5_liq_col = "m5_liq_top" if m5_path == M5_BOXES_PATH else "m30_liq_top"
        try:
            df = pd.read_parquet(m5_path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            df = df[df[m5_liq_col].notna() & df["atr14"].notna()].tail(2)
            if df.empty:
                return None
            bar_curr = df.iloc[-1]
            bar_time = df.index[-1]
        except Exception as e:
            log.warning("DELTA: M5 parquet load failed: %s", e)
            return None

        entry_gc = float(bar_curr["close"])
        atr14    = float(bar_curr["atr14"])

        # TP2 fallback: if structural level is already past entry, use 2:1 SL extension
        if d1_trend == "LONG" and tp2_gc <= entry_gc:
            tp2_gc = entry_gc + abs(entry_gc - sl_gc) * 2.0
            log.debug("DELTA LONG: liq_top below entry -- using 2:1 extension tp2=%.1f", tp2_gc)
        elif d1_trend == "SHORT" and tp2_gc >= entry_gc:
            tp2_gc = entry_gc - abs(entry_gc - sl_gc) * 2.0
            log.debug("DELTA SHORT: liq_bot above entry -- using 2:1 extension tp2=%.1f", tp2_gc)

        # TP1 fallback: if detector returned same as SL or on wrong side, use ATR proxy
        if d1_trend == "LONG" and (pd.isna(tp1_gc) or tp1_gc <= entry_gc):
            tp1_gc = entry_gc + atr14 * 1.0
        elif d1_trend == "SHORT" and (pd.isna(tp1_gc) or tp1_gc >= entry_gc):
            tp1_gc = entry_gc - atr14 * 1.0

        sl_dist  = abs(entry_gc - sl_gc)
        tp2_dist = abs(tp2_gc   - entry_gc)

        if sl_dist < 0.01:
            return None   # degenerate

        # SL whipsaw guard
        if sl_dist < 3.0:
            log.debug("DELTA: sl_dist=%.1f < 3.0 pts -- skip (whipsaw guard)", sl_dist)
            return None

        # R:R check
        rr = tp2_dist / sl_dist
        if rr < DELTA_MIN_RR:
            log.debug("DELTA: R:R=%.2f < %.2f -- skip", rr, DELTA_MIN_RR)
            return None

        # Convert GC -> MT5 XAUUSD space
        offset = self._gc_xauusd_offset
        return {
            "direction":  d1_trend,
            "entry_gc":   entry_gc,
            "entry":      round(entry_gc - offset, 2),
            "sl_gc":      sl_gc,
            "sl":         round(sl_gc    - offset, 2),
            "tp1":        round(tp1_gc   - offset, 2),
            "tp2":        round(tp2_gc   - offset, 2),
            "atr14":      atr14,
            "sl_dist":    round(sl_dist, 2),
            "tp2_dist":   round(tp2_dist, 2),
            "rr":         round(rr, 2),
            "bar_time":   bar_time,
            "level":      round(result["level"], 2),  # expansion line crossed
        }

    def _delta_loop(self):
        """
        Background thread: polls for DELTA trigger at each M5 bar close.
        Waits until the next 5-min mark + 5s (bar confirm delay).

        Dual-timeframe: checks at M5 bar boundaries (every 5 min) because
        check_delta_trigger() now reads M5 bars for entry close/ATR.
        Previously M30 (every 30 min) -- change was needed to match M5 timing.
        """
        log.info("DELTA monitor started (checks at M5 bar close + 5s)")
        while self._running:
            # Align to next 5-min boundary
            now      = pd.Timestamp.utcnow()
            next_bar = now.ceil("5min")
            wait_s   = (next_bar - now).total_seconds() + 5.0
            time.sleep(max(wait_s, 5.0))

            if not self._running:
                break
            if self.feed_monitor is not None and not self.feed_monitor.gate_enabled:
                continue

            try:
                trigger = self.check_delta_trigger()
            except Exception as e:
                log.warning("check_delta_trigger error: %s", e)
                continue

            if trigger is None:
                continue

            # Deduplicate: same bar -> same trigger
            if self._last_delta_bar == trigger["bar_time"]:
                continue
            self._last_delta_bar = trigger["bar_time"]

            ts = _ts()
            log.info(
                "DELTA trigger: dir=%s  entry_gc=%.2f  level=%.2f  sl_gc=%.2f  tp1_gc=%.2f  tp2_gc=%.2f  rr=%.1f",
                trigger["direction"], trigger["entry_gc"], trigger["level"], trigger["sl_gc"],
                trigger["entry_gc"] + (trigger["atr14"] if trigger["direction"] == "LONG" else -trigger["atr14"]),
                trigger["entry_gc"] + (trigger["tp2_dist"] if trigger["direction"] == "LONG" else -trigger["tp2_dist"]),
                trigger["rr"],
            )
            print(
                f"[{ts}] DELTA TRIGGER: {trigger['direction']}"
                f"  entry_gc={trigger['entry_gc']:.2f}"
                f"  level={trigger['level']:.2f}"
                f"  sl_gc={trigger['sl_gc']:.2f}"
                f"  rr={trigger['rr']:.1f}"
                f"  atr14={trigger['atr14']:.1f}"
            )
            threading.Thread(
                target=self._trigger_gate_delta,
                args=(trigger,),
                daemon=True,
            ).start()

    def _trigger_gate_delta(self, trigger: dict):
        """
        Execute DELTA entry -- same 3-leg structure (0.02+0.02+0.01) as GAMMA.
        SL  = prior swing against trend (from TrendMomentumDetector)
        TP1 = next expansion line (detector) or ATR proxy
        TP2 = liq_bot (SHORT) / liq_top (LONG)
        SHIELD activates after TP1 hit, runner trailing.
        Direction lock: DELTA_DIRECTION_LOCK_S (4 M30 bars = 2h).
        """
        now_ts = time.monotonic()
        with self._lock:
            if now_ts - self._last_trigger < GATE_COOLDOWN_S:
                return
            self._last_trigger = now_ts

        direction = trigger["direction"]
        price     = trigger["entry"]
        sl        = trigger["sl"]
        tp1       = trigger["tp1"]
        tp2       = trigger["tp2"]
        ts        = _ts()

        # Operational rules (P0 checks -- same as GAMMA)
        open_positions = self.executor.get_open_positions()
        margin_level   = _mt5_margin_level()
        op_blocked, op_reason = self.ops.check_can_enter(
            open_positions   = open_positions,
            margin_level     = margin_level,
            signal_price     = price,
            signal_direction = direction,
        )
        if op_blocked:
            print(f"[{ts}] DELTA: {op_reason}")
            log.info("DELTA blocked by ops: %s", op_reason)
            return

        # Per-direction lock (DELTA uses 2h lock, not DIRECTION_LOCK_S)
        with self._lock:
            lock_until = self._direction_lock_until.get(f"DELTA_{direction}", 0.0)
        if time.monotonic() < lock_until:
            remaining = lock_until - time.monotonic()
            print(f"[{ts}] DELTA DIR_LOCK {direction}: {remaining:.0f}s remaining")
            return

        # Abort if GC price already past prior swing (trigger invalidated)
        current_gc = self._metrics.get("gc_mid", 0.0)
        if current_gc > 0:
            sl_gc = trigger["sl_gc"]
            if direction == "LONG" and current_gc < sl_gc:
                print(f"[{ts}] DELTA ABORT_STALE: GC={current_gc:.2f} already < sl_gc={sl_gc:.2f}")
                return
            if direction == "SHORT" and current_gc > sl_gc:
                print(f"[{ts}] DELTA ABORT_STALE: GC={current_gc:.2f} already > sl_gc={sl_gc:.2f}")
                return

        _dyn_lots_d = self._compute_session_lots(ice_aligned=False)
        if _dyn_lots_d:
            l1, l2, l3 = _dyn_lots_d[0], _dyn_lots_d[1], _dyn_lots_d[2]
        else:
            l1, l2, l3 = _split_lots(self.lot_size)
        legs = 3 if l3 >= 0.01 else 2

        print(
            f"[{ts}] DELTA GO: {direction}"
            f"  entry={price:.2f}  SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}"
            f"  R:R={trigger['rr']:.1f}  level={trigger['level']:.2f}  legs={legs}"
        )

        if self.dry_run:
            _total_d = sum([l1, l2, l3])
            print(f"[{ts}] DRY RUN: DELTA WOULD OPEN {direction} {_total_d:.2f} @ {price:.2f}")
            print(f"[{ts}]   Leg1={l1:.2f} Leg2={l2:.2f} Leg3={l3:.2f}"
                  f"  SL={sl:.2f}  TP1={tp1:.2f}  TP2={tp2:.2f}")

        elif self.execute:
            # -- News Gate ------------------------------------------------
            if _news_gate is not None:
                _ng = _news_gate.check()
                if _ng.exit_all:
                    log.warning("NEWS EXIT_ALL before DELTA open: %s", _ng.reason)
                    print(f"[{ts}] NEWS EXIT_ALL (DELTA) -- closing all: {_ng.reason}")
                    self._close_all_apex_positions(reason=_ng.reason)
                    return
                if _ng.block_entry:
                    log.warning("NEWS BLOCK_ENTRY (DELTA): score=%.2f  reason=%s", _ng.score, _ng.reason)
                    print(f"[{ts}] NEWS BLOCK_ENTRY DELTA (score={_ng.score:.2f}): {_ng.reason}")
                    return

            _dyn_lots_d = self._compute_session_lots(ice_aligned=False)
            _delta_exec = self._open_on_all_accounts(
                direction=direction, sl=sl, tp1=tp1, tp2=tp2,
                gate_score=0, label="DELTA",
                explicit_lots=_dyn_lots_d,
            )
            if _delta_exec.get("success_any", False):
                _tg_lots_d = _dyn_lots_d if _dyn_lots_d else [l1, l2, l3]
                # DELTA execution result (Fase 2 Telegram Decoupling)
                tg.notify_execution()
                with self._lock:
                    self._direction_lock_until[f"DELTA_{direction}"] = (
                        time.monotonic() + DELTA_DIRECTION_LOCK_S
                    )
                self._live_trade_count += 1

    # ------------------------------------------------------------------
    # JSONL tail reader
    # ------------------------------------------------------------------

    def _tail_jsonl(self, path: Path) -> list[dict]:
        """
        Return new lines from JSONL since last read position.
        Guarded by _jsonl_lock to prevent two on_modified events reading
        the same line twice (race condition when watchdog fires rapidly).
        """
        evts = []
        with self._jsonl_lock:
            pos = self._jsonl_pos.get(str(path), 0)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                evts.append(json.loads(line))
                            except Exception:
                                pass
                    self._jsonl_pos[str(path)] = f.tell()
            except Exception as e:
                log.warning("jsonl tail error: %s", e)
        return evts

    # ------------------------------------------------------------------
    # Iceberg event handler
    # ------------------------------------------------------------------

    def _on_iceberg_event(self, event: dict):
        """
        Process one new iceberg JSONL event (called from watchdog thread).
        JSONL price is in GC space -- convert to XAUUSD before proximity check.
        """
        gc_price = float(event.get("price", 0))
        side     = event.get("side", "")
        prob     = float(event.get("probability", event.get("prob", 0)))
        refills  = int(event.get("refill_count", event.get("refills", 0)))

        if prob < 0.50 or refills < 3:
            return

        # Convert GC price -> MT5 XAUUSD price
        offset    = self._gc_xauusd_offset
        xau_price = round(gc_price - offset, 2)

        ts = _ts()
        print(f"[{ts}] ICEBERG  GC={gc_price:.2f} | XAUUSD={xau_price:.2f}"
              f"  side={side}  prob={prob:.2f}  refills={refills}")

        level_type, level_price = self._near_level(xau_price)
        if not level_type:
            return

        direction, strategy_reason = self._resolve_direction(level_type)
        if direction is None:
            print(f"[{_ts()}] [STRATEGY] SKIP: {strategy_reason}")
            return
        delta = abs(xau_price - level_price)
        gc_level = round(level_price + offset, 2)
        print(f"[{ts}] NEAR {level_type}_mt5={level_price:.2f}(GC:{gc_level}) delta={delta:.2f} <- GATE TRIGGERED [{strategy_reason}]")

        self._last_ice_event = event   # expose to V3 entry hook

        # Build iceberg protection advice (rule-based: prob thresholds + side vs direction)
        _side_up = side.upper()
        _ice_aligned = (
            (_side_up in ("BUY", "BID", "LONG") and direction == "LONG") or
            (_side_up in ("SELL", "ASK", "SHORT") and direction == "SHORT")
        )
        _ice_opposed = (
            (_side_up in ("BUY", "BID", "LONG") and direction == "SHORT") or
            (_side_up in ("SELL", "ASK", "SHORT") and direction == "LONG")
        )
        _ice_align = "ALIGNED" if _ice_aligned else ("OPPOSED" if _ice_opposed else "NEUTRAL")

        if prob >= 0.85 and refills >= 5:
            _ice_sev = "CRITICAL"
        elif prob >= 0.85:
            _ice_sev = "HIGH"
        elif prob >= 0.70:
            _ice_sev = "MEDIUM"
        else:
            _ice_sev = "LOW"

        # entry_action is OBSERVE (never BLOCK/REDUCE) — observability only.
        if _ice_opposed and _ice_sev == "CRITICAL":
            _ice_entry = "OBSERVE"
            _ice_pos = "EXIT_LONG" if direction == "LONG" else "EXIT_SHORT"
        elif _ice_opposed and _ice_sev == "HIGH":
            _ice_entry = "OBSERVE"
            _ice_pos = "TIGHTEN_SL"
        elif _ice_aligned:
            _ice_entry = "ALLOW"
            _ice_pos = "HOLD"
        else:
            _ice_entry = "ALLOW"
            _ice_pos = "HOLD"

        self._cycle_protection["iceberg"] = _build_protection_advice(
            source="ICEBERG",
            alignment=_ice_align,
            severity=_ice_sev,
            flow_relation=(
                "FAVORS_LONG" if (_side_up in ("BUY", "BID", "LONG")) else
                "FAVORS_SHORT" if (_side_up in ("SELL", "ASK", "SHORT")) else
                "UNKNOWN"
            ),
            entry_action=_ice_entry,
            position_action=_ice_pos,
            reason=f"ice side={side} prob={prob:.2f} refills={refills}",
            detected=True,
            side="BUY" if _side_up in ("BUY", "BID", "LONG") else ("SELL" if _side_up in ("SELL", "ASK", "SHORT") else "UNKNOWN"),
            confidence=round(prob, 4),
            refills=refills,
        )

        threading.Thread(
            target=self._trigger_gate,
            args=(xau_price, direction, "ICEBERG", strategy_reason),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # MT5 tick loop -- main monitoring thread
    # ------------------------------------------------------------------

    def _tick_loop(self):
        """
        Fast loop: drives from Quantower/dxfeed GC price (cached from microstructure).
        MT5 is NOT queried here -- it is only used for order execution and offset calibration
        (which happens in _metrics_loop every OFFSET_REFRESH_S seconds).

        Price flow:
          gc_price  <- microstructure gc_mid  (Quantower/dxfeed, refreshed every ~1s on file change)
          xau_price <- gc_price - _gc_xauusd_offset  (derived; MT5 XAUUSD equivalent)
        """
        macro_cache_ts = 0.0
        _news_exit_check_ts  = 0.0
        _guardrail_log_ts    = 0.0

        while self._running:
            t_start = time.monotonic()
            metrics  = self._metrics   # atomic dict read -- no lock needed for display
            gc_price = metrics.get("gc_mid", 0.0)
            ts       = _ts()

            # Guardrail periodic status log -- every 60s (Sprint 1 -- The Shield)
            if _guardrail_available and time.monotonic() - _guardrail_log_ts > 60:
                _guardrail_log_ts = time.monotonic()
                try:
                    _gs = _get_guardrail_status()
                    if _gs.is_safe:
                        _gr_label = f"SAFE | latency={_gs.latency_ms:.0f}ms  spread={_gs.spread_ticks:.1f}tks"
                    else:
                        _gr_label = f"VETO:{_gs.veto_reason} | latency={_gs.latency_ms:.0f}ms  spread={_gs.spread_ticks:.1f}tks"
                    log.info("[GATE] Guardrail: %s", _gr_label)
                    print(f"[{ts}] [GATE] Guardrail: {_gr_label}")
                except Exception as _ge:
                    log.warning("guardrail status check error: %s", _ge)

            # News EXIT_ALL check -- every 30s (pre-news flush max delay reduced to 30s)
            if _news_gate is not None and time.monotonic() - _news_exit_check_ts > 30:
                _news_exit_check_ts = time.monotonic()
                try:
                    _ng = _news_gate.check()
                    if _ng.exit_all:
                        log.warning("NEWS EXIT_ALL (periodic): %s", _ng.reason)
                        print(f"[{ts}] NEWS EXIT_ALL (periodic): {_ng.reason}")
                        self._close_all_apex_positions(reason=_ng.reason)
                except Exception as _ne:
                    log.warning("news gate periodic check error: %s", _ne)

            # Refresh macro every 5 minutes
            if time.monotonic() - macro_cache_ts > 300:
                try:
                    self.gate.micro_reader.get_macro_delta(pd.Timestamp.utcnow())
                    macro_cache_ts = time.monotonic()
                except Exception:
                    pass

            # FIX 2: suspend gate checks when microstructure feed is dead
            if self.feed_monitor is not None and not self.feed_monitor.gate_enabled:
                print(f"[{ts}] FEED_DEAD -- gate suspended (check Quantower L2 stream port 8000)")
                elapsed = time.monotonic() - t_start
                if 1.0 - elapsed > 0:
                    time.sleep(1.0 - elapsed)
                continue

            if gc_price <= 0:
                print(f"[{ts}] Quantower data unavailable | waiting for microstructure")
            else:
                offset    = self._gc_xauusd_offset
                xau_price = round(gc_price - offset, 2)   # MT5 XAUUSD equivalent
                self._speed_tracker.add_tick(xau_price)   # feed Price Speed tracker
                level_type, level_price = self._near_level(xau_price)
                d4h = metrics.get("delta_4h", 0.0)

                if level_type:
                    direction, _strat_reason = self._resolve_direction(level_type)
                    if direction is None:
                        # TRENDING mode: this level is a liquidation zone, skip
                        gc_level = round(level_price + offset, 2)
                        print(f"[{ts}] GC={gc_price:.2f} | XAUUSD={xau_price:.2f} | offset={offset:+.2f}"
                              f" | {level_type}_mt5={level_price:.2f} <- [STRATEGY] SKIP ({_strat_reason})")
                        continue
                    delta     = abs(xau_price - level_price)

                    # DWELL_STALE REMOVED 2026-04-14: was suppressing valid signals
                    # Gate cooldown per-direction is the only rate limiter now.
                    now_check = time.monotonic()
                    _dir_last = self._last_trigger_by_dir.get(direction, 0.0)
                    cooldown_remaining = GATE_COOLDOWN_S - (now_check - _dir_last)
                    if cooldown_remaining <= 0:
                        label = "<- GATE CHECK"
                    else:
                        label = "<- NEAR (cooldown %.0fs)" % cooldown_remaining

                    # FASE 2a: source classification in tick output
                    _src = self._near_level_source or "?"
                    print(f"[{ts}] GC={gc_price:.2f} | XAUUSD={xau_price:.2f} | offset={offset:+.2f}"
                          f" | NEAR {level_type}_mt5={level_price:.2f} (delta={delta:.2f})"
                          f" [{_src}] {label}")

                    threading.Thread(
                        target=self._trigger_gate,
                        args=(xau_price, direction, "QT_MICRO",
                              f"[{_src}] {_strat_reason}"),
                        daemon=True,
                    ).start()
                else:
                    # Price left all near-level bands (no dwell tracking)

                    # ── PATCH 2A: Trend Continuation trigger ──────────
                    # When price is OUTSIDE box in trend direction and no
                    # liq level is nearby, evaluate CONTINUATION directly.
                    _p2a = self._patch2a_continuation_trigger(xau_price, gc_price, offset)
                    if _p2a:
                        _p2a_dir, _p2a_reason = _p2a
                        now_check = time.monotonic()
                        _p2a_last = self._last_trigger_by_dir.get(_p2a_dir, 0.0)
                        cooldown_remaining = GATE_COOLDOWN_S - (now_check - _p2a_last)
                        if cooldown_remaining <= 0:
                            print(f"[{ts}] GC={gc_price:.2f} | XAUUSD={xau_price:.2f}"
                                  f" | PATCH2A_CONTINUATION {_p2a_dir} <- GATE TRIGGERED")
                            threading.Thread(
                                target=self._trigger_gate,
                                args=(xau_price, _p2a_dir, "PATCH2A", _p2a_reason),
                                daemon=True,
                            ).start()
                        # else: in cooldown, skip silently
                    else:
                        print(f"[{ts}] GC={gc_price:.2f} | XAUUSD={xau_price:.2f} | offset={offset:+.2f}"
                              f" | liq_top_mt5={self.liq_top:.2f}(GC:{self.liq_top_gc:.2f})"
                              f"  liq_bot_mt5={self.liq_bot:.2f}(GC:{self.liq_bot_gc:.2f})"
                              f" | d4h={d4h:+.0f} | monitoring")

            # Sleep remainder of 1-second cycle
            elapsed = time.monotonic() - t_start
            remaining = 1.0 - elapsed
            if remaining > 0:
                time.sleep(remaining)

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start(self):
        """Start all threads and watchdog. Blocks until Ctrl+C."""
        self._running = True
        self._start_heartbeat_thread()

        # Initial metrics load (blocking, one-time)
        print("Loading microstructure metrics (one-time ~1s)...")
        self._refresh_metrics()
        self.refresh_macro_context(reason="start")
        print(f"  delta_4h={self._metrics['delta_4h']:+.0f}  ATR={self._metrics['atr']:.1f}pts"
              f"  NEAR band = {max(self._metrics['atr']*NEAR_ATR_FACTOR, NEAR_FLOOR_PTS):.1f}pts")

        # Initial GC/XAUUSD offset -- try MT5 once for calibration, fall back to default 31 pts.
        # Monitoring does NOT depend on this succeeding; offset is refined in background loop.
        xau_now = _mt5_price()
        if xau_now and self._metrics.get("gc_mid", 0) > 0:
            self._refresh_offset(xau_now)
            print(f"  GC/XAUUSD offset : {self._gc_xauusd_offset:+.3f} pts"
                  f"  (GC={self._metrics['gc_mid']:.2f}  XAUUSD={xau_now:.2f})")
        else:
            print(f"  GC/XAUUSD offset : {self._gc_xauusd_offset:+.3f} pts (default -- MT5 offline, will auto-calibrate)")
        # Convert GC structural levels to MT5 space using current offset
        with self._lock:
            self.liq_top  = round(self.liq_top_gc - self._gc_xauusd_offset, 2)
            self.liq_bot  = round(self.liq_bot_gc - self._gc_xauusd_offset, 2)
            self.box_high = round(self.box_high_gc - self._gc_xauusd_offset, 2) if self.box_high_gc is not None else None
            self.box_low  = round(self.box_low_gc  - self._gc_xauusd_offset, 2) if self.box_low_gc  is not None else None
        print(f"  liq_top : GC {self.liq_top_gc:.2f} -> MT5 {self.liq_top:.2f}")
        print(f"  liq_bot : GC {self.liq_bot_gc:.2f} -> MT5 {self.liq_bot:.2f}")

        # Background metrics refresh thread
        threading.Thread(target=self._metrics_loop, name="metrics", daemon=True).start()

        # GAMMA momentum stacking monitor (checks at each M30 bar close)
        threading.Thread(target=self._gamma_loop, name="gamma_monitor", daemon=True).start()

        # DELTA trend momentum re-alignment monitor (checks at each M30 bar close)
        threading.Thread(target=self._delta_loop, name="delta_monitor", daemon=True).start()

        # Tick-level breakout & JAC monitor (real-time level updates, <1s latency)
        self._tick_breakout.start()

        # Watchdog observers
        observer = Observer()
        observer.schedule(_MicroHandler(self),  str(MICRO_DIR), recursive=False)
        observer.schedule(_IcebergHandler(self), str(ICE_DIR),   recursive=False)
        observer.start()

        # Seek JSONL to current end -- don't replay historical events
        jp = self._jsonl_path()
        if jp and jp.exists():
            with open(jp, "r") as f:
                f.seek(0, 2)
                self._jsonl_pos[str(jp)] = f.tell()
            print(f"  JSONL watcher active: {jp.name}")

        mode = "DRY RUN" if self.dry_run else "EXECUTE" if self.execute else "ADVISORY"
        print()
        print("=" * 70)
        print("APEX EVENT-DRIVEN PIPELINE ACTIVE")
        print("DATA-DRIVEN THRESHOLDS ACTIVE -- GC calibrated")
        thr = self._thresholds
        print(f"  source       : {thr.get('source', '?')}")
        print(f"  SHORT block  : delta_4h > {thr['delta_4h_short_block']}")
        print(f"  LONG  block  : delta_4h < {thr['delta_4h_long_block']}")
        print(f"  max_positions: {thr['max_positions']}")
        print(f"  margin_min   : {thr['margin_level_min']}%")
        print(f"  next recal   : {thr.get('next_recalibration', '?')}")
        print(f"  MT5 Symbol   : {SYMBOL}  (spot gold)")
        print(f"  Micro Symbol : GC        (CME futures, Quantower/DXFeed)")
        print(f"  GC offset    : {self._gc_xauusd_offset:+.3f} pts  (refreshed every 5min)")
        print(f"  liq_top      : GC {self.liq_top_gc:.2f} -> MT5 {self.liq_top:.2f}")
        print(f"  liq_bot      : GC {self.liq_bot_gc:.2f} -> MT5 {self.liq_bot:.2f}")
        print(f"  Mode         : {mode}")
        print(f"  Lot          : {self.lot_size:.2f}  SL={self.sl_pts:.0f}pts  TP1={self.tp1_pts:.0f}pts  TP2={self.tp2_pts:.0f}pts")
        print(f"  Cooldown     : {GATE_COOLDOWN_S:.0f}s between gate triggers")
        print("=" * 70)
        print()

        try:
            self._tick_loop()           # blocks
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            self._running = False
            observer.stop()
            observer.join()


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]

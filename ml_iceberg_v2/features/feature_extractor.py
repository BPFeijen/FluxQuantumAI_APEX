"""
feature_extractor — Converts RefillEvents into feature windows for ML.

Reference: Func spec §8 (FUNC_GC_Iceberg_ML_Module_v2_20260321.md).

Window (adaptive):
  Normal  : 5s window / 1s step  — standard market hours
  High-Res: 500ms window / 100ms step — inside Surprise Windows (NFP, CPI, FOMC)

One row per (window_start, price_level, side) combination.

Features F01–F23 (26 total columns after F11/F12 split)
---------------------------------------------------------
F01  refill_count              int    — refills in window
F02  refill_speed_mean_ms      float  — mean(trade→refill delay)
F03  refill_speed_std_ms       float  — std(trade→refill delay)
F04  refill_size_ratio_mean    float  — mean(refill_size / trade_size)
F05  refill_size_consistency   float  — 1 − CV(refill sizes)
F06  price_persistence_s       float  — seconds price held at level
F07  aggressor_volume_absorbed float  — total aggressor lots at level
F08  dom_imbalance_at_level    float  — DOM imbalance from depth_snapshots [-1,1]
F09  book_depth_recovery_rate  float  — depth recovery speed after consumption
F10  trade_intensity           float  — trades/second in window
F11  session_label             int8   — 0=ASIA 1=EUROPE 2=NY (categorical, FR-003)
F11a hour_of_day               int8   — 0-23 UTC raw (continuous context)
F11b minute_of_hour            int8   — 0-59 UTC raw (continuous context)
F12  volatility_bucket         int8   — 0=LOW 1=MED 2=HIGH (categorical, rolling ATR)
F12a atr_5min_raw              float  — raw ATR in ticks (continuous, no discretisation)
F13  refill_velocity           float  — refill_count / window_duration_s
F14  refill_velocity_trend     float  — slope over last N windows
F15  cumulative_absorbed_volume float — running sum of absorbed lots at level
F16  level_lifetime_s          float  — how long level has been active
F17  time_since_last_trade_at_vanish_ms  float — time since last trade when depth
                                           vanished at this level (raw continuous ms)
                                           -1.0 = no vanish event in window
F18  price_excursion_beyond_level_ticks  float — ticks price moved beyond level
                                           after last refill (raw continuous)
                                           0.0 = no excursion in window
F19  underflow_volume          float  — sum of (trade_size - visible_book_size) where
                                        trade_size > visible_book_size (Christensen 2013)
                                        0.0 = no underflow or no depth_snapshot data
F20  iceberg_sequence_direction_score  float — (bid_count - ask_count) / total across
                                        last 10 windows; +1.0 = all BID, -1.0 = all ASK
                                        0.0 = balanced or no history
F21  ofi_rate                  float  — Order Flow Imbalance / window_s (Cont et al. 2014)
                                        = (ΔBid_vol − ΔAsk_vol) / window_s  [lots/s]
                                        Positive = net bid pressure; Negative = net ask pressure
                                        Requires depth_updates (MBO events). 0.0 if unavailable.
F22  vot                       float  — Velocity of Tape = Σ(size × price) / window_s
                                        [USD-equivalent contracts/s] — monetary mass in motion.
                                        0.0 if no trades in window.
F23  inst_volatility_ticks     float  — std(trade prices in window) / tick_size [ticks]
                                        Instantaneous volatility within the window.
                                        0.0 if fewer than 2 trades.
                                        Critical for High-Res (100ms) Payroll windows.

Adaptive Resolution:
  Outside Surprise Windows : window=5s, step=1s   (is_high_res=False)
  Inside  Surprise Windows : window=500ms, step=100ms (is_high_res=True)
  Surprise Windows defined by surprise_labels.jsonl (APEX_GC_Payroll/data/).

Session boundaries (UTC, FR-003):
  ASIA   22:00–07:00
  EUROPE 07:00–13:30
  NY     13:30–22:00

Note: F11 and F12 provide both categorical and raw continuous variants.
F17 and F18 support depth-vanish detection (CME paper Section 3.2).
F19 supports underflow/hidden volume detection (Bookmap Tracker / Christensen 2013).
F20 supports successive iceberg direction signal (Ninjacators).
F21 canonical OFI: Cont, Kukanov, Rachev (2014) "The Price Impact of Order Book Events".
F22 VOT: monetary mass velocity — complementary to F10 (trade count/s).
F23 inst_volatility_ticks: within-window price std — most informative in 100ms High-Res mode.

Output: Parquet per tech spec §4.2 schema.

Public API
----------
extract_features(refill_events, trades, depth_snapshots, vanish_events,
                 depth_updates, surprise_windows_ns,
                 window_size_s, step_size_s,
                 high_res_window_s, high_res_step_s,
                 tick_size) → pd.DataFrame
save_features(df, output_path) → None
load_surprise_windows(jsonl_path, margin_s) → List[Tuple[int, int]]
"""

from __future__ import annotations

import datetime
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ml_iceberg_v2.features.refill_detector import DepthVanishEvent, IcebergChain, RefillEvent, _normalise_timestamps

# Backward-compatible alias
CancellationEvent = DepthVanishEvent
from ml_iceberg_v2.config import (
    SESSIONS,
    SESSION_LABELS,
    VOLATILITY_ATR_WINDOW_S,
    VOLATILITY_LOW_THRESHOLD,
    VOLATILITY_HIGH_THRESHOLD,
    TICK_SIZE_DEFAULT,
    WINDOW_SIZE_S,
    STEP_SIZE_S,
    SIDE_HISTORY_WINDOWS,
    VELOCITY_TREND_LOOKBACK,
    detect_tick_size,
)

# High-resolution mode defaults (Payroll / macro release windows)
HIGH_RES_WINDOW_S    = 0.5    # 500ms window
HIGH_RES_STEP_S      = 0.1    # 100ms step
SURPRISE_MARGIN_S    = 120.0  # ±2 minutes around each surprise event

# Canonical surprise labels path (APEX_GC_Payroll)
_DEFAULT_SURPRISE_LABELS = Path(
    r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Payroll\data\surprise_labels.jsonl"
)


# ---------------------------------------------------------------------------
# Surprise window loader
# ---------------------------------------------------------------------------

def load_surprise_windows(
    jsonl_path: Path = _DEFAULT_SURPRISE_LABELS,
    margin_s: float = SURPRISE_MARGIN_S,
) -> List[Tuple[int, int]]:
    """
    Load surprise event timestamps from surprise_labels.jsonl and
    return a list of (start_ns, end_ns) nanosecond ranges representing
    the high-resolution zones around each release.

    margin_s: half-window around each event (default ±120s = ±2 minutes).

    Returns empty list if file does not exist yet (system just started).
    """
    if not Path(jsonl_path).exists():
        return []

    zones: List[Tuple[int, int]] = []
    margin_ns = int(margin_s * 1e9)

    with open(jsonl_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("ts_utc") or rec.get("scheduled_utc")
                if not ts_str:
                    continue
                ts_ns = int(pd.Timestamp(ts_str).value)
                zones.append((ts_ns - margin_ns, ts_ns + margin_ns))
            except Exception:
                continue

    return zones


# ---------------------------------------------------------------------------
# Adaptive window builder
# ---------------------------------------------------------------------------

def _build_adaptive_windows(
    t_min: int,
    t_max: int,
    normal_step_ns: int,
    normal_window_ns: int,
    high_res_step_ns: int,
    high_res_window_ns: int,
    surprise_zones: List[Tuple[int, int]],
) -> List[Tuple[int, int, bool]]:
    """
    Build (window_start_ns, window_size_ns, is_high_res) tuples.

    Strategy:
      - Normal windows (1s step, 5s window): entire day
      - High-res windows (100ms step, 500ms window): surprise zones only
      - Normal windows whose start falls inside a surprise zone are excluded
        (replaced by high-res windows with finer granularity)

    Returns list sorted by window_start_ns.
    """
    # Build surprise-zone coverage set for fast lookup
    # Use sorted list of (start, end) for binary search
    zones_sorted = sorted(surprise_zones)

    def _in_surprise(ws_ns: int) -> bool:
        """Check if ws_ns falls inside any surprise zone."""
        for sz_s, sz_e in zones_sorted:
            if sz_s <= ws_ns <= sz_e:
                return True
            if sz_s > ws_ns:
                break   # sorted — no point continuing
        return False

    # Normal windows (skip those inside surprise zones)
    normal_windows = [
        (ws, normal_window_ns, False)
        for ws in range(t_min, t_max + normal_step_ns, normal_step_ns)
        if not _in_surprise(ws)
    ]

    # High-res windows (surprise zones only, clipped to [t_min, t_max])
    hr_windows: List[Tuple[int, int, bool]] = []
    for sz_s, sz_e in zones_sorted:
        hr_t_min = max(t_min, sz_s)
        hr_t_max = min(t_max, sz_e)
        if hr_t_min >= hr_t_max:
            continue
        for ws in range(hr_t_min, hr_t_max + high_res_step_ns, high_res_step_ns):
            hr_windows.append((ws, high_res_window_ns, True))

    all_windows = sorted(normal_windows + hr_windows, key=lambda x: x[0])
    return all_windows


# ---------------------------------------------------------------------------
# Session tagging (FR-003)
# ---------------------------------------------------------------------------

def _session_label_for_hour_minute(hour: int, minute: int) -> int:
    total_min = hour * 60 + minute
    if total_min >= 22 * 60 or total_min < 7 * 60:
        return SESSION_LABELS["ASIA"]
    elif total_min < 13 * 60 + 30:
        return SESSION_LABELS["EUROPE"]
    else:
        return SESSION_LABELS["NY"]


def _session_from_ns(ts_ns: int) -> int:
    dt = datetime.datetime.utcfromtimestamp(ts_ns / 1e9)
    return _session_label_for_hour_minute(dt.hour, dt.minute)


# ---------------------------------------------------------------------------
# Volatility bucketing (F12)
# ---------------------------------------------------------------------------

def _compute_volatility_buckets(
    trades: pd.DataFrame,
    window_starts_ns: np.ndarray,
    atr_window_s: float = VOLATILITY_ATR_WINDOW_S,
    tick_size: float = TICK_SIZE_DEFAULT,
) -> np.ndarray:
    """Compute volatility bucket (0=LOW, 1=MED, 2=HIGH) for each window_start."""
    if trades.empty or len(window_starts_ns) == 0:
        return np.zeros(len(window_starts_ns), dtype=np.int8)

    atr_ns = int(atr_window_s * 1e9)
    low_thr  = VOLATILITY_LOW_THRESHOLD  * tick_size
    high_thr = VOLATILITY_HIGH_THRESHOLD * tick_size

    trade_ts = trades["timestamp"].values.astype(np.int64)
    trade_px = trades["price"].values.astype(np.float64)

    sort_idx     = np.argsort(trade_ts, kind="stable")
    trade_ts_s   = trade_ts[sort_idx]
    trade_px_s   = trade_px[sort_idx]

    buckets = np.ones(len(window_starts_ns), dtype=np.int8)
    for i, ws_ns in enumerate(window_starts_ns):
        lo = int(np.searchsorted(trade_ts_s, ws_ns - atr_ns, side="left"))
        hi = int(np.searchsorted(trade_ts_s, ws_ns,           side="left"))
        prices = trade_px_s[lo:hi]
        if len(prices) < 2:
            continue
        atr = float(np.std(prices))
        if atr < low_thr:
            buckets[i] = 0
        elif atr > high_thr:
            buckets[i] = 2
    return buckets


# ---------------------------------------------------------------------------
# Raw ATR (F12a)
# ---------------------------------------------------------------------------

def _compute_atr_raw_ticks(
    trades: pd.DataFrame,
    window_starts_ns: np.ndarray,
    atr_window_s: float = VOLATILITY_ATR_WINDOW_S,
    tick_size: float = TICK_SIZE_DEFAULT,
) -> np.ndarray:
    """Compute raw ATR in ticks for each window_start (F12a)."""
    if trades.empty or len(window_starts_ns) == 0:
        return np.ones(len(window_starts_ns), dtype=np.float64)

    atr_ns   = int(atr_window_s * 1e9)
    trade_ts = trades["timestamp"].values.astype(np.int64)
    trade_px = trades["price"].values.astype(np.float64)

    raw_atrs = np.ones(len(window_starts_ns), dtype=np.float64)
    for i, ws_ns in enumerate(window_starts_ns):
        mask   = (trade_ts >= ws_ns - atr_ns) & (trade_ts < ws_ns)
        prices = trade_px[mask]
        if len(prices) < 2:
            raw_atrs[i] = 1.0
            continue
        raw_atrs[i] = float(np.std(prices)) / tick_size
    return raw_atrs


# ---------------------------------------------------------------------------
# Level lifetime tracking
# ---------------------------------------------------------------------------

def _compute_level_lifetimes(
    refill_events: List[RefillEvent],
) -> Dict[Tuple[float, str], int]:
    first_seen: Dict[Tuple[float, str], int] = {}
    for e in refill_events:
        key = (e.price, e.side)
        if key not in first_seen or e.trade_timestamp < first_seen[key]:
            first_seen[key] = e.trade_timestamp
    return first_seen


# ---------------------------------------------------------------------------
# DOM imbalance lookup (F08 / F09)
# ---------------------------------------------------------------------------

def _dom_at_level(
    depth_snapshots: Optional[pd.DataFrame],
    price: float,
    window_start_ns: int,
    window_end_ns: int,
) -> Tuple[float, float]:
    if depth_snapshots is None or depth_snapshots.empty:
        return 0.0, 0.0

    snap = depth_snapshots[
        (depth_snapshots["timestamp"] >= window_start_ns) &
        (depth_snapshots["timestamp"] <  window_end_ns)
    ]
    if snap.empty:
        return 0.0, 0.0

    dom_col = next((c for c in ("dom_imbalance", "dom") if c in snap.columns), None)
    if dom_col is not None:
        dom_mean = float(snap[dom_col].mean())
    else:
        bid_snap = snap[snap["side"].str.lower() == "bid"]["size"] if "side" in snap.columns else None
        ask_snap = snap[snap["side"].str.lower() == "ask"]["size"] if "side" in snap.columns else None
        if bid_snap is not None and ask_snap is not None and len(bid_snap) > 0 and len(ask_snap) > 0:
            b = float(bid_snap.mean())
            a = float(ask_snap.mean())
            dom_mean = (b - a) / (b + a + 1e-9)
        else:
            return 0.0, 0.0

    bid_col = next((c for c in ("total_bid_size", "bid_size") if c in snap.columns), None)
    recovery = 0.0
    if bid_col is not None and len(snap) > 1:
        diffs    = snap[bid_col].diff().abs().dropna()
        recovery = float(diffs.mean()) if not diffs.empty else 0.0
    elif "side" in snap.columns and "size" in snap.columns:
        bid_rows = snap[snap["side"].str.lower() == "bid"]["size"]
        if len(bid_rows) > 1:
            recovery = float(bid_rows.diff().abs().dropna().mean())

    return dom_mean, recovery


# ---------------------------------------------------------------------------
# F21 — OFI precomputation (Cont et al. 2014)
# ---------------------------------------------------------------------------

def _precompute_ofi(
    depth_updates: pd.DataFrame,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Pre-sort depth_updates for binary-search OFI computation.

    OFI formula (per window):
        ΔBid = Σ(bid add sizes) − Σ(bid cancel sizes)
        ΔAsk = Σ(ask add sizes) − Σ(ask cancel sizes)
        OFI  = ΔBid − ΔAsk

    Returns (ts_sorted, signed_size_sorted, sort_idx):
        signed_size > 0 → contributes positively to OFI (bid add or ask cancel)
        signed_size < 0 → contributes negatively to OFI (ask add or bid cancel)
    """
    if depth_updates is None or depth_updates.empty:
        empty = np.array([], dtype=np.float64)
        return empty.astype(np.int64), empty, empty.astype(np.int64)

    du = depth_updates.copy()

    # Normalise timestamps
    du = _normalise_timestamps(du)

    ts_arr   = du["timestamp"].values.astype(np.int64)
    side_arr = du["side"].values        # "BID" / "ASK"
    act_arr  = du["action"].values      # "add" / "cancel" / "update"
    size_arr = du["size"].values.astype(np.float64)

    # Assign signed contribution per event:
    #   BID add    → +size   (bid volume growing  = buying pressure)
    #   BID cancel → -size   (bid volume shrinking = buying pressure lost)
    #   ASK add    → -size   (ask volume growing  = selling pressure)
    #   ASK cancel → +size   (ask volume shrinking = selling pressure lost)
    #   update (modify) → ignored: direction of size change is unknown
    signed = np.zeros(len(ts_arr), dtype=np.float64)
    for idx in range(len(ts_arr)):
        s = side_arr[idx]
        a = act_arr[idx]
        if a == "update":
            continue   # ambiguous — skip per Cont et al. strict definition
        sz = size_arr[idx]
        if s == "BID":
            signed[idx] = sz if a == "add" else -sz
        elif s == "ASK":
            signed[idx] = -sz if a == "add" else sz

    sort_idx           = np.argsort(ts_arr, kind="stable")
    ts_sorted          = ts_arr[sort_idx]
    signed_size_sorted = signed[sort_idx]
    return ts_sorted, signed_size_sorted, sort_idx


def _ofi_in_window(
    ofi_ts_sorted: np.ndarray,
    ofi_signed_sorted: np.ndarray,
    ws_ns: int,
    we_ns: int,
    window_size_s: float,
) -> float:
    """
    Compute OFI rate (lots/s) for a single window using binary search.
    Returns 0.0 if no depth_updates available.
    """
    if len(ofi_ts_sorted) == 0:
        return 0.0
    lo = int(np.searchsorted(ofi_ts_sorted, ws_ns, side="left"))
    hi = int(np.searchsorted(ofi_ts_sorted, we_ns, side="left"))
    if lo >= hi:
        return 0.0
    raw_ofi = float(np.sum(ofi_signed_sorted[lo:hi]))
    return raw_ofi / window_size_s   # normalise to lots/s


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_features(
    refill_events: List[RefillEvent],
    trades: Optional[pd.DataFrame] = None,
    depth_snapshots: Optional[pd.DataFrame] = None,
    vanish_events: Optional[List[DepthVanishEvent]] = None,
    # ── NEW: MBO order events for F21 OFI ──────────────────────────────
    depth_updates: Optional[pd.DataFrame] = None,
    # ── NEW: Adaptive resolution — Surprise Windows ─────────────────────
    surprise_windows_ns: Optional[List[Tuple[int, int]]] = None,
    high_res_window_s: float = HIGH_RES_WINDOW_S,
    high_res_step_s: float = HIGH_RES_STEP_S,
    # ── Existing parameters ─────────────────────────────────────────────
    window_size_s: float = WINDOW_SIZE_S,
    step_size_s: float = STEP_SIZE_S,
    tick_size: Optional[float] = None,
    cancellation_events: Optional[List[DepthVanishEvent]] = None,
) -> pd.DataFrame:
    """
    Compute feature windows from RefillEvents.

    Creates adaptive sliding windows:
      - Normal : window_size_s / step_size_s (default 5s / 1s)
      - High-Res: high_res_window_s / high_res_step_s (default 500ms / 100ms)
        activated inside surprise_windows_ns zones (Payroll, CPI, FOMC releases).

    For each (window, price_level, side) with at least 1 refill:
      computes F01–F23 and outputs one row.

    New parameters vs v1:
      depth_updates : pd.DataFrame
          Raw MBO add/cancel events. Columns: timestamp (ns int64), side (BID/ASK),
          action (add/cancel/update), price, size. Required for F21 (OFI).
          Falls back gracefully to 0.0 if None.
      surprise_windows_ns : List[Tuple[int, int]]
          Nanosecond (start, end) ranges for high-res mode.
          Use load_surprise_windows() to build from surprise_labels.jsonl.
      high_res_window_s : float  (default 0.5 = 500ms)
      high_res_step_s   : float  (default 0.1 = 100ms)

    Returns
    -------
    pd.DataFrame
        One row per (window_start, price_level, side).
        Columns: window_start, window_end, price_level, side, session,
                 is_high_res, + F01..F23 named columns (26 feature columns total).
    """
    if vanish_events is None and cancellation_events is not None:
        vanish_events = cancellation_events
    if not refill_events:
        return _empty_feature_df()

    # Normalise trades timestamps
    if trades is not None and not trades.empty:
        trades = _normalise_timestamps(trades)

    if tick_size is None:
        prices    = np.array([e.price for e in refill_events], dtype=np.float64)
        tick_size = detect_tick_size(prices)

    # Time range
    all_ts = [e.trade_timestamp for e in refill_events]
    t_min  = min(all_ts)
    t_max  = max(all_ts)

    normal_window_ns   = int(window_size_s    * 1e9)
    normal_step_ns     = int(step_size_s      * 1e9)
    high_res_window_ns = int(high_res_window_s * 1e9)
    high_res_step_ns   = int(high_res_step_s  * 1e9)

    # Build adaptive window list: [(ws_ns, window_ns, is_high_res), ...]
    zones = surprise_windows_ns or []
    all_windows = _build_adaptive_windows(
        t_min, t_max,
        normal_step_ns,   normal_window_ns,
        high_res_step_ns, high_res_window_ns,
        zones,
    )

    if not all_windows:
        return _empty_feature_df()

    # Pre-build lookups
    level_first_seen = _compute_level_lifetimes(refill_events)

    # Volatility buckets (F12) and raw ATR (F12a) — keyed by window_start_ns
    ws_array = np.array([w[0] for w in all_windows], dtype=np.int64)
    if trades is not None and not trades.empty:
        vol_buckets  = _compute_volatility_buckets(trades, ws_array, tick_size=tick_size)
        atr_raw_arr  = _compute_atr_raw_ticks(trades, ws_array, tick_size=tick_size)
    else:
        vol_buckets = np.ones(len(ws_array), dtype=np.int8)
        atr_raw_arr = np.ones(len(ws_array), dtype=np.float64)

    # Depth-vanish lookup (F17)
    vanish_lookup: Dict[Tuple[float, str], List[DepthVanishEvent]] = {}
    if vanish_events:
        for ve in vanish_events:
            vanish_lookup.setdefault((ve.price, ve.side), []).append(ve)

    # Pre-compute depth_snapshot arrays for F19 (underflow_volume)
    has_depth_size = (
        depth_snapshots is not None
        and not depth_snapshots.empty
        and "size"  in depth_snapshots.columns
        and "price" in depth_snapshots.columns
    )
    if has_depth_size:
        snap_ts_arr    = depth_snapshots["timestamp"].values.astype(np.int64)
        snap_price_arr = depth_snapshots["price"].values.astype(np.float64)
        snap_size_arr  = depth_snapshots["size"].values.astype(np.float64)
        snap_sort_idx  = np.argsort(snap_ts_arr, kind="stable")
        snap_ts_sorted    = snap_ts_arr[snap_sort_idx]
        snap_price_sorted = snap_price_arr[snap_sort_idx]
        snap_size_sorted  = snap_size_arr[snap_sort_idx]
    else:
        snap_ts_sorted = snap_price_sorted = snap_size_sorted = np.array([], dtype=np.float64)

    # F21 — OFI pre-sort (MBO depth_updates)
    ofi_ts_sorted, ofi_signed_sorted, _ = _precompute_ofi(depth_updates)

    # Trade lookup (F07 / F10 / F22 / F23)
    if trades is not None and not trades.empty:
        trade_ts_arr     = trades["timestamp"].values.astype(np.int64)
        trade_size_arr   = trades["size"].values.astype(np.float64)
        trade_price_arr  = trades["price"].values.astype(np.float64)
        trade_sort_idx   = np.argsort(trade_ts_arr, kind="stable")
        trade_ts_sorted  = trade_ts_arr[trade_sort_idx]
        trade_size_sorted  = trade_size_arr[trade_sort_idx]
        trade_price_sorted = trade_price_arr[trade_sort_idx]
    else:
        trade_ts_sorted    = np.array([], dtype=np.int64)
        trade_size_sorted  = np.array([], dtype=np.float64)
        trade_price_sorted = np.array([], dtype=np.float64)

    # Refill events sorted for binary search
    refill_events_sorted = sorted(refill_events, key=lambda e: e.trade_timestamp)
    refill_ts_sorted     = np.array([e.trade_timestamp for e in refill_events_sorted], dtype=np.int64)

    # State for F20 (iceberg direction history)
    iceberg_side_history: List[Tuple[int, str]] = []

    # Running state for F14 (velocity trend) and F15 (cumulative volume)
    prev_velocity:    Dict[Tuple[float, str], List[float]] = {}
    cumulative_volume: Dict[Tuple[float, str], float]       = {}

    rows = []

    for i, (ws_ns, window_ns, is_high_res) in enumerate(all_windows):
        we_ns          = ws_ns + window_ns
        window_size_s_ = window_ns / 1e9   # actual window duration (may differ from default)

        # Refills in this window
        lo_r = int(np.searchsorted(refill_ts_sorted, ws_ns, side="left"))
        hi_r = int(np.searchsorted(refill_ts_sorted, we_ns, side="left"))
        window_refills = refill_events_sorted[lo_r:hi_r]

        if not window_refills:
            continue

        # Group by (price, side)
        groups: Dict[Tuple[float, str], List[RefillEvent]] = {}
        for e in window_refills:
            groups.setdefault((e.price, e.side), []).append(e)

        # Trade window slice — once per window
        if len(trade_ts_sorted) > 0:
            lo_t = int(np.searchsorted(trade_ts_sorted, ws_ns, side="left"))
            hi_t = int(np.searchsorted(trade_ts_sorted, we_ns, side="left"))
            trades_in_window  = hi_t - lo_t
            trade_intensity   = trades_in_window / window_size_s_
            w_prices_slice    = trade_price_sorted[lo_t:hi_t]
            w_sizes_slice     = trade_size_sorted[lo_t:hi_t]
        else:
            lo_t = hi_t = 0
            trade_intensity   = 0.0
            w_prices_slice    = np.array([], dtype=np.float64)
            w_sizes_slice     = np.array([], dtype=np.float64)

        # Time features (shared across (price, side) in this window)
        _dt         = datetime.datetime.utcfromtimestamp(ws_ns / 1e9)
        session_lbl = _session_label_for_hour_minute(_dt.hour, _dt.minute)
        f11a_hour   = np.int8(_dt.hour)
        f11b_minute = np.int8(_dt.minute)
        vol_bucket  = int(vol_buckets[i])
        f12a_atr    = float(atr_raw_arr[i])

        # F20 — iceberg_sequence_direction_score (global, per window)
        for (p, s) in groups.keys():
            iceberg_side_history.append((i, s))
        cutoff = i - SIDE_HISTORY_WINDOWS
        iceberg_side_history = [(wi, s) for (wi, s) in iceberg_side_history if wi > cutoff]
        recent_sides = [s for (_, s) in iceberg_side_history]
        if recent_sides:
            bid_cnt   = sum(1 for s in recent_sides if s == "BID")
            ask_cnt   = sum(1 for s in recent_sides if s == "ASK")
            total_cnt = bid_cnt + ask_cnt
            f20 = (bid_cnt - ask_cnt) / total_cnt if total_cnt > 0 else 0.0
        else:
            f20 = 0.0

        # F21 — OFI rate [lots/s] (Cont et al. 2014)
        f21 = _ofi_in_window(ofi_ts_sorted, ofi_signed_sorted,
                              ws_ns, we_ns, window_size_s_)

        # F22 — VOT: Velocity of Tape = Σ(size × price) / window_s
        if len(w_prices_slice) > 0:
            f22 = float(np.sum(w_sizes_slice * w_prices_slice)) / window_size_s_
        else:
            f22 = 0.0

        # F23 — Instantaneous volatility in ticks (within-window std)
        if len(w_prices_slice) >= 2:
            f23 = float(np.std(w_prices_slice)) / tick_size
        else:
            f23 = 0.0

        # Per (price, side) features
        for (price, side), evts in groups.items():
            half_tick = tick_size / 2.0

            # F01 — refill count
            f01 = len(evts)

            # F02, F03 — refill speed
            delays = [e.refill_delay_ms for e in evts]
            f02 = float(statistics.mean(delays))
            f03 = float(statistics.stdev(delays)) if len(delays) > 1 else 0.0

            # F04 — refill size ratio
            ratios = [e.refill_size / e.trade_size for e in evts if e.trade_size > 0]
            f04 = float(statistics.mean(ratios)) if ratios else 0.0

            # F05 — size consistency
            sizes = [e.refill_size for e in evts]
            if len(sizes) > 1 and statistics.mean(sizes) > 0:
                cv  = statistics.stdev(sizes) / statistics.mean(sizes)
                f05 = max(0.0, 1.0 - cv)
            else:
                f05 = 1.0

            # F06 — price persistence
            first_evt_ts = min(e.trade_timestamp for e in evts)
            last_evt_ts  = max(e.trade_timestamp for e in evts)
            f06 = (last_evt_ts - first_evt_ts) / 1e9

            # F07 — aggressor volume absorbed at this level
            if len(w_prices_slice) > 0:
                price_mask = np.abs(w_prices_slice - price) <= half_tick
                f07 = float(np.sum(w_sizes_slice[price_mask]))
            else:
                f07 = sum(e.trade_size for e in evts)

            # F08, F09 — DOM features
            f08, f09 = _dom_at_level(depth_snapshots, price, ws_ns, we_ns)

            # F10 — trade intensity [trades/s]
            f10 = trade_intensity

            # F11 — session (shared)
            f11 = session_lbl

            # F12 — volatility bucket (shared)
            f12 = vol_bucket

            # F17 — time_since_last_trade_at_vanish_ms
            vanish_at_level = vanish_lookup.get((price, side), [])
            vanish_in_window = [ve for ve in vanish_at_level if ws_ns <= ve.timestamp < we_ns]
            f17 = (
                float(np.min([ve.time_since_last_trade_ms for ve in vanish_in_window]))
                if vanish_in_window else -1.0
            )

            # F19 — underflow_volume (Christensen 2013)
            f19 = 0.0
            if has_depth_size and len(snap_ts_sorted) > 0:
                for e in evts:
                    before_hi = int(np.searchsorted(snap_ts_sorted, e.trade_timestamp, side="right"))
                    if before_hi == 0:
                        continue
                    candidates_px = snap_price_sorted[:before_hi]
                    price_mask    = np.abs(candidates_px - price) <= half_tick
                    if not np.any(price_mask):
                        continue
                    last_idx = int(np.where(price_mask)[0][-1])
                    visible  = float(snap_size_sorted[last_idx])
                    if e.trade_size > visible:
                        f19 += e.trade_size - visible

            # F18 — price_excursion_beyond_level_ticks
            last_evt_ts = max(e.trade_timestamp for e in evts)
            if len(trade_ts_sorted) > 0:
                after_lo    = int(np.searchsorted(trade_ts_sorted, last_evt_ts, side="right"))
                after_hi    = int(np.searchsorted(trade_ts_sorted, we_ns,       side="left"))
                prices_after = trade_price_sorted[after_lo:after_hi]
                if len(prices_after) > 0:
                    if side == "BID":
                        excursion = max(0.0, (price - float(np.min(prices_after))) / tick_size)
                    else:
                        excursion = max(0.0, (float(np.max(prices_after)) - price) / tick_size)
                    f18 = min(excursion, 30.0)
                else:
                    f18 = 0.0
            else:
                f18 = 0.0

            # F13 — refill velocity
            f13 = f01 / window_size_s_

            # F14 — refill velocity trend
            key_lv    = (price, side)
            prev_vels = prev_velocity.get(key_lv, [])
            prev_vels.append(f13)
            if len(prev_vels) > VELOCITY_TREND_LOOKBACK:
                prev_vels = prev_vels[-VELOCITY_TREND_LOOKBACK:]
            prev_velocity[key_lv] = prev_vels
            if len(prev_vels) >= 2:
                xs  = np.arange(len(prev_vels), dtype=np.float64)
                f14 = float(np.polyfit(xs, prev_vels, 1)[0])
            else:
                f14 = 0.0

            # F15 — cumulative absorbed volume
            cumulative_volume[key_lv] = cumulative_volume.get(key_lv, 0.0) + f07
            f15 = cumulative_volume[key_lv]

            # F16 — level lifetime
            first_seen_ts = level_first_seen.get((price, side), ws_ns)
            f16 = (ws_ns - first_seen_ts) / 1e9

            session_str = {0: "ASIA", 1: "EUROPE", 2: "NY"}.get(session_lbl, "UNKNOWN")

            rows.append({
                "window_start"  : ws_ns,
                "window_end"    : we_ns,
                "price_level"   : price,
                "side"          : side,
                "session"       : session_str,
                "is_high_res"   : is_high_res,
                # F01–F10
                "refill_count"                        : f01,
                "refill_speed_mean_ms"                : f02,
                "refill_speed_std_ms"                 : f03,
                "refill_size_ratio_mean"              : f04,
                "refill_size_consistency"             : f05,
                "price_persistence_s"                 : f06,
                "aggressor_volume_absorbed"           : f07,
                "dom_imbalance_at_level"              : f08,
                "book_depth_recovery_rate"            : f09,
                "trade_intensity"                     : f10,
                # F11
                "session_label"  : np.int8(f11),
                "hour_of_day"    : f11a_hour,
                "minute_of_hour" : f11b_minute,
                # F12
                "volatility_bucket" : np.int8(f12),
                "atr_5min_raw"      : f12a_atr,
                # F13–F16
                "refill_velocity"            : f13,
                "refill_velocity_trend"      : f14,
                "cumulative_absorbed_volume" : f15,
                "level_lifetime_s"           : f16,
                # F17–F20
                "time_since_last_trade_at_vanish_ms"  : f17,
                "price_excursion_beyond_level_ticks"  : f18,
                "underflow_volume"                    : f19,
                "iceberg_sequence_direction_score"    : f20,
                # F21–F23 (new)
                "ofi_rate"              : f21,
                "vot"                   : f22,
                "inst_volatility_ticks" : f23,
            })

    if not rows:
        return _empty_feature_df()

    df = pd.DataFrame(rows)
    df["window_start"] = pd.to_datetime(df["window_start"], unit="ns", utc=True)
    df["window_end"]   = pd.to_datetime(df["window_end"],   unit="ns", utc=True)

    return df


# ---------------------------------------------------------------------------
# Empty schema
# ---------------------------------------------------------------------------

def _empty_feature_df() -> pd.DataFrame:
    """Return empty DataFrame with correct columns (F01–F23, 26 feature columns + is_high_res)."""
    cols = [
        "window_start", "window_end", "price_level", "side", "session",
        "is_high_res",
        # F01–F10
        "refill_count", "refill_speed_mean_ms", "refill_speed_std_ms",
        "refill_size_ratio_mean", "refill_size_consistency",
        "price_persistence_s", "aggressor_volume_absorbed",
        "dom_imbalance_at_level", "book_depth_recovery_rate",
        "trade_intensity",
        # F11 + F11a + F11b
        "session_label", "hour_of_day", "minute_of_hour",
        # F12 + F12a
        "volatility_bucket", "atr_5min_raw",
        # F13–F16
        "refill_velocity", "refill_velocity_trend",
        "cumulative_absorbed_volume", "level_lifetime_s",
        # F17–F20
        "time_since_last_trade_at_vanish_ms",
        "price_excursion_beyond_level_ticks",
        "underflow_volume",
        "iceberg_sequence_direction_score",
        # F21–F23
        "ofi_rate",
        "vot",
        "inst_volatility_ticks",
    ]
    return pd.DataFrame(columns=cols)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_features(df: pd.DataFrame, output_path: Path) -> None:
    """Save feature DataFrame to Parquet (gzip compressed)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(str(output_path), compression="gzip", index=False)

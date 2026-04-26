"""
live/impl2_features.py — IMPL-2 (EXEC-4 2026-04-26)

Five Wyckoff-grounded exhaustion features for the LOGIC-C composite scorer
(IMPL-3 / EXEC-5). Per Asana 1214204535586426 + T1-X1-INTERACTIONS_v2.md
(see _audit/pp_sprint/T1-X1-INTERACTIONS_v2.md §14 "Readiness for IMPL-2").

Scope of this module: the 5 features that survived hypothesis testing
under the v2 (Op B) TREND-A redefinition with weights ≥ 0.2:

  | Feature  | Precondition | Weight  |
  |----------|--------------|---------|
  | F1_B     | TREND-B      | 0.213   |
  | F2_B     | TREND-B      | 0.274   |
  | F3_B     | TREND-B      | 0.543   |
  | F5_A     | TREND-A      | 0.394   |
  | F5_B     | TREND-B      | 0.640   |

Threshold (LOGIC-C scorer, ships in EXEC-5): score > 0.4 strict.

F4 Volume Climax is intentionally OUT OF SCOPE (it is the FEAT-4 anti-exit
hook; ships in EXEC-6 IMPL-4, not IMPL-2 — see T1-X1-INTERACTIONS_v2.md
§14.3 gotcha 5).

═══════════════════════════════════════════════════════════════════════
METHODOLOGY GROUNDING (Standing Rule 15 G-METHODOLOGY-FIRST-WORKFLOW)
═══════════════════════════════════════════════════════════════════════

Each feature is grounded in published Wyckoff / order-flow literature. The
ATS Docs library lives at:
  C:\\FluxQuantumAPEX\\APEX GOLD\\APEX_Docs\\ATS Docs\\

Citations per feature (see also Section 2 of t1_x1_features_v2_discovery.md):

  - F1 SOT (Shortening Of the Thrust):
      Villahermosa, "Wyckoff 2.0: Structures, Volume Profile and Order
      Flow", Book 2 — §1.6 pp.522-570. Quote: "each new end travels a
      shorter distance than the previous end ... a minimum of three pushes
      in the direction of the trend".
      File: 533397707-Ruben-Villahermosa-Wyckoff-2-0-...-Book-2.pdf

  - F2 EFR (Effort/Result Divergence) — Wyckoff Law 3:
      Villahermosa "Wyckoff 2.0" §1.6 pp.544-546 + §4.5.3 pp.2844-2847.
      Quote: "the big effort got little reward: Effort/Result Divergence".
      Operationalized as percentile-rank gap between volume and bar body
      over a trailing 100-bar window (window pre-specified, see
      T1-X1-INTERACTIONS_v2.md Section 12 Open Question 1).

  - F3 Delta Divergence (bar-level absorption):
      Villahermosa "Wyckoff 2.0" §4.5.2 pp.2810-2833 + Modules 1-5
      Strategic / Advanced Order Flow PDFs (857175735-Module-1-Strategic-
      Order-Flow.pdf, 857175775-Module-3-Advanced-Order-Flow-Concepts.pdf).
      Quote: "negative Delta in a bullish candlestick or a positive Delta
      in a bearish candlestick ... if they appear in the right place they
      usually anticipate interesting turns".

  - F5 ClosePct (close position within bar range, weak-close as SOT proxy):
      Villahermosa §1.6 implicit (extension of bar-level SOT). Volume-Price
      close analysis: weak close in trend direction = absorption / failed
      thrust. Reference Forthmann "Volume Profile / Market Profile / Order
      Flow" (685506003-...by-Johannes-Forthmann.pdf) Section "weak close"
      definition.

═══════════════════════════════════════════════════════════════════════
INPUTS
═══════════════════════════════════════════════════════════════════════

Each feature function takes:
  df            : pandas DataFrame of recent M30 bars, sorted ascending,
                  with columns: open, high, low, close, volume.
                  m30_bar_delta is required by F3 only (returns False if
                  absent or NaN; live wiring of delta aggregation is a
                  separate task).
  regime_state  : dict {"trend_a": (bool, str), "trend_b": (bool, str)}
                  produced by live/regime_detectors.py (IMPL-1) and
                  maintained on MarketEventProcessor at self._regime_state.
                  Direction string is "LONG" | "SHORT" | "" per the
                  detect_trend_a / detect_trend_b convention.

Returns: bool (True = the feature is active for the latest closed bar).

All functions FAIL-SOFT to False on any internal error (insufficient data,
missing columns, division-by-zero) — see evaluate_all() wrapper.

═══════════════════════════════════════════════════════════════════════
PRE-SPECIFIED CONSTANTS (FROZEN per T1-X1-INTERACTIONS_v2.md §14)
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("apex.impl2")

# Pre-specified constants (T1-X1-FEATURES discovery; FROZEN — re-derive only
# if a future re-calibration cycle explicitly authorizes change).
SOT_N = 3                          # minimum 3 pushes for SOT (Wyckoff §1.6)
EFR_ROLLING = 100                  # 100-bar trailing window for EFR pct rank
EFR_DIVERGENCE_THRESHOLD = 0.3     # vol_pct - body_pct gap threshold
CLOSE_PCT_WEAK = 0.3               # weak-close threshold for F5
VOL_CLIMAX_PCT = 95                # F4 percentile threshold (Wyckoff Phase D)
VOL_CLIMAX_WINDOW = 100            # rolling window for F4 quantile

# Default neutral state when the module's evaluate_all() fails entirely.
NEUTRAL_FEATURE_STATE = {
    "F1_B": False,
    "F2_B": False,
    "F3_B": False,
    "F5_A": False,
    "F5_B": False,
}


# ============================================================================
# Internal helpers
# ============================================================================

def _trend_dir_to_int(direction_str: str) -> int:
    """Map IMPL-1 direction string to signed int.

    Convention (regime_detectors.py): "LONG" → +1, "SHORT" → −1, else 0.
    """
    if direction_str == "LONG":
        return 1
    if direction_str == "SHORT":
        return -1
    return 0


def _trend_b_dir_sign(df: pd.DataFrame) -> int:
    """Fallback trend-B direction proxy when IMPL-1 returns empty string.

    Uses sign(close[-1] − close[-SOT_N]); matches the reference offline
    impl t1_x1_features_v2.py:170.
    """
    if len(df) < SOT_N:
        return 0
    diff = float(df["close"].iloc[-1] - df["close"].iloc[-SOT_N])
    if diff > 0:
        return 1
    if diff < 0:
        return -1
    return 0


def _rolling_pct_rank_last(s: pd.Series, w: int) -> Optional[float]:
    """Percentile rank of the LAST observation within its trailing window
    of size w. Returns None when the window has insufficient observations
    (< max(20, w//5)) — same warm-up rule as the reference impl
    t1_x1_features_v2.py:191-196.
    """
    s_clean = s.dropna()
    if len(s_clean) < max(20, w // 5):
        return None
    window = s_clean.iloc[-w:] if len(s_clean) >= w else s_clean
    if len(window) < 2:
        return None
    last = float(window.iloc[-1])
    prior = window.iloc[:-1].values
    n_prior = len(prior)
    if n_prior == 0:
        return None
    return float(
        (np.sum(prior < last) + 0.5 * np.sum(prior == last)) / n_prior
    )


def _close_pct_active(last_bar: pd.Series, tdir: int) -> bool:
    """F5 inner test: weak close in the trend direction.

    Up-trend exhaustion: close near the LOW → close_pct_from_low < 0.3
    Down-trend exhaustion: close near the HIGH → close_pct_from_high < 0.3
    """
    rng = float(last_bar["high"] - last_bar["low"])
    if rng <= 0:
        return False
    if tdir > 0:
        return ((float(last_bar["close"] - last_bar["low"]) / rng)
                < CLOSE_PCT_WEAK)
    if tdir < 0:
        return ((float(last_bar["high"] - last_bar["close"]) / rng)
                < CLOSE_PCT_WEAK)
    return False


# ============================================================================
# F1_B — Shortening Of the Thrust × TREND-B (weight 0.213)
# ============================================================================

def feature_F1_B(df: pd.DataFrame, regime_state: dict) -> bool:
    """Active when TREND-B holds and the last 3 M30 bar ranges decrease
    monotonically: range[t] < range[t-1] < range[t-2].

    Wyckoff bar-level SOT (Villahermosa §1.6 pp.522-570): the stretch of
    each successive thrust shortens, signalling exhaustion of the move.
    """
    trend_b_active, _trend_b_dir = regime_state.get("trend_b", (False, ""))
    if not trend_b_active:
        return False
    if len(df) < 3:
        return False
    rng = (df["high"] - df["low"]).iloc[-3:]
    if len(rng) < 3:
        return False
    return bool(rng.iloc[2] < rng.iloc[1] < rng.iloc[0])


# ============================================================================
# F2_B — Effort/Result Divergence × TREND-B (weight 0.274)
# ============================================================================

def feature_F2_B(df: pd.DataFrame, regime_state: dict) -> bool:
    """Active when TREND-B holds and the latest bar exhibits a positive
    EFR divergence: percentile-rank(volume, 100) − percentile-rank(body, 100)
    > 0.3, computed over the trailing 100-bar window.

    Wyckoff Law 3 (Effort vs Result), Villahermosa §1.6 pp.544-546 +
    §4.5.3 pp.2844-2847. Interpretation: high effort (rare-large volume)
    that produces only ordinary result (small body) signals absorption.
    Window 100 + threshold 0.3 are pre-specified (see T1-X1-INTERACTIONS_v2
    §14.3 gotcha 3).
    """
    trend_b_active, _ = regime_state.get("trend_b", (False, ""))
    if not trend_b_active:
        return False
    # Need at least the warm-up minimum (20 bars per reference rule).
    if len(df) < max(20, EFR_ROLLING // 5):
        return False
    body = (df["close"] - df["open"]).abs()
    vol_pct = _rolling_pct_rank_last(df["volume"], EFR_ROLLING)
    body_pct = _rolling_pct_rank_last(body, EFR_ROLLING)
    if vol_pct is None or body_pct is None:
        return False
    return bool((vol_pct - body_pct) > EFR_DIVERGENCE_THRESHOLD)


# ============================================================================
# F3_B — Delta Divergence × TREND-B (weight 0.543)
# ============================================================================

def feature_F3_B(df: pd.DataFrame, regime_state: dict) -> bool:
    """Active when TREND-B holds, m30_bar_delta is available, and the
    bar's net delta is OPPOSITE to its body direction:
      up-trend:   delta > 0 AND close < open  (positive delta, bearish bar)
      down-trend: delta < 0 AND close > open  (negative delta, bullish bar)

    Villahermosa §4.5.2 pp.2810-2833 + Modules 1-5 Order Flow:
    "negative Delta in a bullish candlestick or a positive Delta in a
    bearish candlestick ... if they appear in the right place they usually
    anticipate interesting turns".

    Returns False when m30_bar_delta column is absent or NaN (live wiring
    of M30 delta aggregation is a separate task; F3_B remains inert until
    that lands).
    """
    trend_b_active, trend_b_dir_str = regime_state.get("trend_b", (False, ""))
    if not trend_b_active:
        return False
    if "m30_bar_delta" not in df.columns or len(df) == 0:
        return False
    last = df.iloc[-1]
    delta = last.get("m30_bar_delta", np.nan)
    if pd.isna(delta):
        return False
    body_signed = float(last["close"] - last["open"])
    tdir = _trend_dir_to_int(trend_b_dir_str)
    if tdir == 0:
        tdir = _trend_b_dir_sign(df)
    if tdir > 0:
        return bool(delta > 0 and body_signed < 0)
    if tdir < 0:
        return bool(delta < 0 and body_signed > 0)
    return False


# ============================================================================
# F5_A — Close% × TREND-A (weight 0.394)
# ============================================================================

def feature_F5_A(df: pd.DataFrame, regime_state: dict) -> bool:
    """Active when TREND-A holds and the latest bar's close sits near the
    extreme OPPOSITE to the trend direction (weak close):
      up-trend:   close_pct_from_low  < 0.3
      down-trend: close_pct_from_high < 0.3

    Villahermosa §1.6 implicit — close position as bar-level SOT proxy:
    less and less progress within the bar range = exhaustion.
    """
    trend_a_active, trend_a_dir_str = regime_state.get("trend_a", (False, ""))
    if not trend_a_active or len(df) == 0:
        return False
    tdir = _trend_dir_to_int(trend_a_dir_str)
    if tdir == 0:
        return False
    return _close_pct_active(df.iloc[-1], tdir)


# ============================================================================
# F5_B — Close% × TREND-B (weight 0.640) — STRONGEST SINGLE FEATURE
# ============================================================================

def feature_F5_B(df: pd.DataFrame, regime_state: dict) -> bool:
    """Active when TREND-B holds and the latest bar's close sits near the
    extreme OPPOSITE to the trend direction (weak close).

    Same logic as F5_A but conditioned on TREND-B (m30_box_confirmed AND
    at_struct_level). This is the strongest single feature in the v2
    discovery (weight 0.640, |Cohen's d| above PROMISING threshold under
    permutation-robust verdict — see T1-X1-INTERACTIONS_v2.md Section 9.2).

    Direction fallback: if regime_state direction is "" (e.g. TREND-B
    evaluated as bias=unknown), derive from sign(close[-1] − close[-SOT_N]).
    """
    trend_b_active, trend_b_dir_str = regime_state.get("trend_b", (False, ""))
    if not trend_b_active or len(df) == 0:
        return False
    tdir = _trend_dir_to_int(trend_b_dir_str)
    if tdir == 0:
        tdir = _trend_b_dir_sign(df)
    if tdir == 0:
        return False
    return _close_pct_active(df.iloc[-1], tdir)


# ============================================================================
# F4 — Volume Climax × TREND-A (FEAT-4 anti-exit hook, EXEC-6 IMPL-4)
# ============================================================================

def feature_F4(df: pd.DataFrame, regime_state: dict) -> bool:
    """Active when TREND-A holds AND the latest M30 bar's volume exceeds
    the 95th percentile of the trailing 100-bar window (Volume Climax).

    Wyckoff Phase D event: a Volume Climax marks the institutional-driven
    transfer that closes the absorption phase and opens the markup /
    markdown phase. Per Villahermosa "Wyckoff 2.0" Book 2 §4.2.4
    (volume climax during exhaustion) and Forthmann "Volume Profile /
    Market Profile / Order Flow" — the climax is a discrete event that
    invalidates many *premature* defensive exits (i.e. the move that
    triggered the defensive check is the climax itself, not the start
    of a reversal).

    Used by EXEC-6 IMPL-4 anti-exit veto (NOT in LOGIC-C scorer — see
    T1-X1-INTERACTIONS_v2.md §14.3 gotcha 5: "VolClimax (FEAT-4) is NOT
    in LOGIC-C; it's only for the INT-6 anti-exit use case").

    Constants frozen (T1-X1-FEATURES §76): VOL_CLIMAX_PCT=95,
    VOL_CLIMAX_WINDOW=100, warm-up min(20, window//5).

    Returns False when:
      - TREND-A is inactive
      - df has < warm-up minimum bars
      - latest volume is missing / NaN
    """
    trend_a_active, _trend_a_dir = regime_state.get("trend_a", (False, ""))
    if not trend_a_active:
        return False
    if df is None or len(df) < max(20, VOL_CLIMAX_WINDOW // 5):
        return False
    if "volume" not in df.columns:
        return False
    vol_series = df["volume"].dropna()
    if len(vol_series) < max(20, VOL_CLIMAX_WINDOW // 5):
        return False
    window = vol_series.iloc[-VOL_CLIMAX_WINDOW:] if len(vol_series) >= VOL_CLIMAX_WINDOW else vol_series
    last_vol = float(vol_series.iloc[-1])
    p95 = float(window.quantile(VOL_CLIMAX_PCT / 100.0))
    return bool(last_vol > p95)


# ============================================================================
# Composite evaluator — single entry-point used by event_processor.py
# ============================================================================

FEATURE_FUNCTIONS = {
    "F1_B": feature_F1_B,
    "F2_B": feature_F2_B,
    "F3_B": feature_F3_B,
    "F5_A": feature_F5_A,
    "F5_B": feature_F5_B,
}


def evaluate_all(df: pd.DataFrame, regime_state: dict) -> dict:
    """Evaluate every feature on the latest closed M30 bar. Each function
    is wrapped fail-soft so a single bad feature cannot break the whole
    evaluation — failures degrade to False and emit a debug log.

    Returned dict keys are stable: F1_B, F2_B, F3_B, F5_A, F5_B (always
    present, value type bool).
    """
    out = dict(NEUTRAL_FEATURE_STATE)
    if df is None or len(df) == 0:
        return out
    for name, fn in FEATURE_FUNCTIONS.items():
        try:
            out[name] = bool(fn(df, regime_state))
        except Exception as e:
            log.debug("IMPL-2 feature %s evaluation failed: %s", name, e)
            out[name] = False
    return out

"""
label_generator — Assigns class labels to feature windows from IcebergChains.

Reference: CME paper rules + Bookmap generalization (Func spec §9, Sprint Plan T-106).

Label classes
-------------
NOISE (0)            — window with no consistent iceberg pattern
ICEBERG_NATIVE (1)   — chain ≥ 3 refills, all < 10ms, size_consistency > 0.70
ICEBERG_SYNTHETIC(2) — chain ≥ 3 refills, 10-100ms, size_consistency > 0.50
ABSORPTION (3)       — price_persistence > 5s AND aggressor_volume > threshold
                       AND no clear refill chain

label_confidence = proportion of criteria met (e.g. 3 of 4 → 0.75).

Output schema (tech spec §4.2)
-------------------------------
window_start, label, label_confidence, refill_count_in_chain,
peak_size_observed, chain_complete,
chain_weight,           — CME Paper §4.2: 1/num_unique_length_chains (1.0 if no tree)
cancellation_detected,  — True if F17 ≥ 0 in feature_windows (depth vanish detected)
num_chains_in_tree      — total chains in the source TrancheTree (1 if no tree)

Public API
----------
generate_labels(refill_events, chains, feature_windows, trees) → pd.DataFrame
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ml_iceberg_v2.features.refill_detector import IcebergChain, RefillEvent, TrancheTree
from ml_iceberg_v2.config import (
    NATIVE_MAX_DELAY_MS,
    SYNTHETIC_MAX_DELAY_MS,
    SIZE_CONSISTENCY_NATIVE,
    SIZE_CONSISTENCY_SYNTHETIC,
    ABSORPTION_PERSISTENCE_S,
    ABSORPTION_VOLUME_THRESHOLD,
    MIN_CHAIN_LENGTH,
    TICK_SIZE_DEFAULT,
)


# ---------------------------------------------------------------------------
# Label constants
# ---------------------------------------------------------------------------

LABEL_NOISE = 0
LABEL_NATIVE = 1
LABEL_SYNTHETIC = 2
LABEL_ABSORPTION = 3


# ---------------------------------------------------------------------------
# Chain classification
# ---------------------------------------------------------------------------

@dataclass
class ChainLabel:
    """Label assigned to an IcebergChain."""
    label: int
    confidence: float
    criteria_met: int
    criteria_total: int


def _classify_chain(chain: IcebergChain) -> ChainLabel:
    """
    Apply CME paper rules to classify one IcebergChain.

    Rules:
      NATIVE     : chain_length ≥ 3, ALL delays < 10ms, size_consistency > 0.70
      SYNTHETIC  : chain_length ≥ 3, ALL delays < 100ms (and at least one ≥ 10ms),
                   size_consistency > 0.50
      NOISE      : all else

    label_confidence = fraction of criteria met.
    """
    if chain.chain_length < MIN_CHAIN_LENGTH:
        return ChainLabel(LABEL_NOISE, 0.0, 0, 3)

    delays = [e.refill_delay_ms for e in chain.refill_events]
    all_native = all(d < NATIVE_MAX_DELAY_MS for d in delays)
    all_synthetic = all(d < SYNTHETIC_MAX_DELAY_MS for d in delays)
    consistency = chain.size_consistency

    # --- NATIVE ---
    native_criteria = [
        chain.chain_length >= MIN_CHAIN_LENGTH,
        all_native,
        consistency >= SIZE_CONSISTENCY_NATIVE,
    ]
    n_native = sum(native_criteria)
    if all(native_criteria):
        return ChainLabel(LABEL_NATIVE, 1.0, n_native, len(native_criteria))

    # --- SYNTHETIC ---
    synthetic_criteria = [
        chain.chain_length >= MIN_CHAIN_LENGTH,
        all_synthetic,                       # all delays < 100ms
        not all_native,                      # at least one ≥ 10ms (else it would be NATIVE)
        consistency >= SIZE_CONSISTENCY_SYNTHETIC,
    ]
    n_synthetic = sum(synthetic_criteria)
    if all(synthetic_criteria):
        conf = n_synthetic / len(synthetic_criteria)
        return ChainLabel(LABEL_SYNTHETIC, conf, n_synthetic, len(synthetic_criteria))

    # --- Partial match → NOISE with partial confidence ---
    # Use the best partial match as a signal strength indicator
    best_partial = max(n_native / len(native_criteria),
                       n_synthetic / len(synthetic_criteria))
    return ChainLabel(LABEL_NOISE, round(best_partial, 4), 0, 3)


# ---------------------------------------------------------------------------
# Window ↔ chain matching
# ---------------------------------------------------------------------------

def _build_chain_index(
    chains: List[IcebergChain],
) -> Dict[Tuple[int, float, str], IcebergChain]:
    """
    Build a dict keyed by (trade_timestamp_ns, price, side) for fast lookup.
    Only chains with chain_length >= MIN_CHAIN_LENGTH are indexed.
    """
    idx: Dict[Tuple[int, float, str], IcebergChain] = {}
    for chain in chains:
        if chain.chain_length < MIN_CHAIN_LENGTH:
            continue
        for evt in chain.refill_events:
            idx[(evt.trade_timestamp, evt.price, evt.side)] = chain
    return idx


def _build_chain_level_index(
    chains: List[IcebergChain],
    tick_size: float = TICK_SIZE_DEFAULT,
) -> Dict[Tuple[float, str], List[IcebergChain]]:
    """
    Build index: (price_rounded, side) → list of chains at that level.
    Uses price rounding to nearest tick for reliable key lookup.
    """
    idx: Dict[Tuple[float, str], List[IcebergChain]] = {}
    inv_tick = 1.0 / tick_size
    for chain in chains:
        rounded = round(chain.price * inv_tick) / inv_tick
        key = (rounded, chain.side)
        idx.setdefault(key, []).append(chain)
    return idx


def _find_best_chain_for_window(
    window_start_ns: int,
    window_end_ns: int,
    price_level: float,
    side: str,
    chain_level_idx: Dict[Tuple[float, str], List[IcebergChain]],
    tick_size: float = TICK_SIZE_DEFAULT,
) -> Optional[IcebergChain]:
    """
    Find the longest chain whose refill events fall within the window
    at the given price/side.  Uses pre-built level index for O(log N) lookup
    instead of O(N_chains) scan.
    """
    inv_tick = 1.0 / tick_size
    rounded = round(price_level * inv_tick) / inv_tick
    candidates = chain_level_idx.get((rounded, side), [])

    best: Optional[IcebergChain] = None
    best_count = 0

    for chain in candidates:
        # Count events in window via simple scan (candidates list is short)
        count = sum(
            1 for e in chain.refill_events
            if window_start_ns <= e.trade_timestamp < window_end_ns
        )
        if count > 0 and count > best_count:
            best_count = count
            best = chain

    return best


# ---------------------------------------------------------------------------
# Absorption detection
# ---------------------------------------------------------------------------

def _is_absorption(row) -> Tuple[bool, float]:
    """
    Detect ABSORPTION pattern from feature window row.
    Accepts both pd.Series and namedtuple (itertuples) rows.

    Criteria (all must be met):
    1. price_persistence_s > ABSORPTION_PERSISTENCE_S (3s)
    2. aggressor_volume_absorbed > ABSORPTION_VOLUME_THRESHOLD (10 lots)

    Note: refill_count is NOT checked here. This function is only called when
    no valid IcebergChain was found for the window (see generate_labels).
    The refill_count in the feature row reflects raw detections that did NOT
    form a valid chain — it does not indicate a real iceberg pattern.
    Adding refill_count < MIN_CHAIN_LENGTH would incorrectly suppress windows
    where some refills were detected but no chain qualified (BUG FIX 2026-04-09:
    condition 3 was causing 0 ABSORPTION labels with 978 true candidates).

    Returns (is_absorption, confidence).
    """
    criteria = [
        float(getattr(row, "price_persistence_s", 0)) > ABSORPTION_PERSISTENCE_S,
        float(getattr(row, "aggressor_volume_absorbed", 0)) > ABSORPTION_VOLUME_THRESHOLD,
    ]
    n_met = sum(criteria)
    if all(criteria):
        return True, 1.0
    if n_met >= 1:
        return False, n_met / len(criteria)
    return False, 0.0


# ---------------------------------------------------------------------------
# Main labeling function
# ---------------------------------------------------------------------------

def generate_labels(
    refill_events: List[RefillEvent],
    chains: List[IcebergChain],
    feature_windows: pd.DataFrame,
    trees: Optional[List[TrancheTree]] = None,
) -> pd.DataFrame:
    """
    Assign labels to feature windows based on IcebergChains and TrancheTree structure.

    Algorithm per window row:
    1. Find the best matching chain (same price/side, events in window).
    2. Classify the chain: NATIVE / SYNTHETIC / NOISE.
    3. If NOISE: check for ABSORPTION pattern from feature columns.
    4. Build output label row including tree-aware columns.

    Parameters
    ----------
    refill_events : List[RefillEvent]
        Full day's refill events (used for context; chains do the labeling).
    chains : List[IcebergChain]
        Output of chain_refills() / trees_to_chains() — same day's refill_events.
    feature_windows : pd.DataFrame
        Output of extract_features() — one row per (window, price, side).
    trees : List[TrancheTree], optional
        Output of build_tranche_trees(). If provided, adds num_chains_in_tree.

    Returns
    -------
    pd.DataFrame
        Columns: window_start, label, label_confidence,
                 refill_count_in_chain, peak_size_observed, chain_complete,
                 chain_weight, cancellation_detected, num_chains_in_tree.
        Same number of rows as feature_windows, same index alignment.
    """
    if feature_windows.empty:
        return _empty_label_df()

    # Pre-classify all chains
    chain_labels: Dict[str, ChainLabel] = {
        c.chain_id: _classify_chain(c)
        for c in chains
        if c.chain_length >= MIN_CHAIN_LENGTH
    }

    # Build level index for O(1) chain lookup per (price, side) pair
    tick_size = TICK_SIZE_DEFAULT
    chain_level_idx = _build_chain_level_index(chains, tick_size=tick_size)

    # Build tree lookup: tree_id → num_chains_in_tree (for num_chains_in_tree column)
    tree_chain_count: Dict[str, int] = {}
    if trees:
        for tree in trees:
            tree_chain_count[tree.tree_id] = len(tree.all_chains())

    # Detect if F17 column is present in feature_windows (for cancellation_detected)
    has_f17 = "time_since_last_trade_at_vanish_ms" in feature_windows.columns

    rows = []
    for frow in feature_windows.itertuples(index=False):
        # Convert window_start to ns for matching
        ws = frow.window_start
        if hasattr(ws, "value"):                # pandas Timestamp
            ws_ns = int(ws.value)
            we_ns = int(frow.window_end.value)
        elif isinstance(ws, (int, float, np.integer)):
            ws_ns = int(ws)
            we_ns = int(frow.window_end)
        else:
            ws_ns = int(pd.Timestamp(ws).value)
            we_ns = int(pd.Timestamp(frow.window_end).value)

        price = float(frow.price_level)
        side = str(frow.side)

        # Find matching chain via level index — O(chains_at_level) instead of O(all_chains)
        best_chain = _find_best_chain_for_window(
            ws_ns, we_ns, price, side, chain_level_idx, tick_size=tick_size
        )

        if best_chain is not None and best_chain.chain_id in chain_labels:
            cl = chain_labels[best_chain.chain_id]
            label = cl.label
            confidence = cl.confidence
            refill_count_in_chain = best_chain.chain_length
            peak_size = best_chain.peak_size_estimated
            chain_complete = best_chain.chain_complete
            chain_weight = best_chain.chain_weight
            num_chains = tree_chain_count.get(best_chain.source_tree_id or "", 1)
        else:
            # No chain — check for absorption
            is_abs, abs_conf = _is_absorption(frow)
            if is_abs:
                label = LABEL_ABSORPTION
                confidence = abs_conf
            else:
                label = LABEL_NOISE
                confidence = 0.0
            refill_count_in_chain = int(getattr(frow, "refill_count", 0))
            peak_size = 0.0
            chain_complete = False
            chain_weight = 1.0
            num_chains = 1

        # cancellation_detected: True if F17 > 100ms
        # calibrated 2026-04-10: threshold aligned with SYNTHETIC_MAX_DELAY_MS=100ms.
        # f17 <= 100ms → vanish within refill detection window → likely consumption, not cancellation.
        # f17 > 100ms → vanish after refill window → genuine iceberg pull (cancellation).
        if has_f17:
            f17_val = float(getattr(frow, "time_since_last_trade_at_vanish_ms", -1.0))
            cancellation_detected = f17_val > 100.0
        else:
            cancellation_detected = False

        rows.append({
            "window_start":          frow.window_start,
            "label":                 np.int8(label),
            "label_confidence":      round(float(confidence), 4),
            "refill_count_in_chain": int(refill_count_in_chain),
            "peak_size_observed":    float(peak_size),
            "chain_complete":        bool(chain_complete),
            "chain_weight":          float(chain_weight),
            "cancellation_detected": bool(cancellation_detected),
            "num_chains_in_tree":    int(num_chains),
        })

    return pd.DataFrame(rows)


def _empty_label_df() -> pd.DataFrame:
    cols = [
        "window_start", "label", "label_confidence",
        "refill_count_in_chain", "peak_size_observed", "chain_complete",
        "chain_weight", "cancellation_detected", "num_chains_in_tree",
    ]
    return pd.DataFrame(columns=cols)

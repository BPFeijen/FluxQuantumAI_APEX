"""
refill_detector — Core iceberg detection algorithm.

Reference: CME/DXFeed paper (Zotikov & Antonov, 2019), Section 3 (Detection).

Detects RefillEvents by correlating depth_updates with trades:
  A trade consumes liquidity at price P/side S → if a depth_update with
  action=update appears at the same price P / same side S within
  max_delay_ms → that is a RefillEvent (potential iceberg refill).

Tranche Trees (CME paper §3.2, Fig. 2):
  A single trade may have MULTIPLE qualifying depth_update candidates within
  the dt window. Each candidate is a child node in a TrancheTree. All
  root→leaf paths are valid iceberg chain interpretations, used for weighted
  Kaplan-Meier estimation: chain_weight = 1 / num_unique_length_chains.

Cancellation detection (CME paper Section 3.2):
  Cancelled icebergs are censored observations in the Kaplan-Meier model.
  Without MBO/L3 data, cancellation is detectable by depth vanishing at an
  active iceberg level without a preceding trade. See detect_cancellations().

Key design decisions
---------------------
- Exchange timestamp used (column "timestamp", 100 ns resolution), NOT recv_timestamp.
- sequence numbers break timestamp ties.
- Chunked processing with 30-second overlap buffer to avoid splitting chains
  across chunk boundaries.
- Trades loaded in full per day (106-174K rows/day fits in RAM).
  depth_updates loaded in 500K-row chunks (~3.5-4.7M rows/day).
- detect_refills() returns ALL candidates per trade (parent_trade_key links
  candidates to their originating trade).

Public API
----------
detect_refills(depth_updates, trades, max_delay_ms, tick_size) → List[RefillEvent]
build_tranche_trees(refill_events, tick_size, max_inter_refill_gap_ms) → List[TrancheTree]
trees_to_chains(trees) → List[IcebergChain]
chain_refills(refill_events, tick_size, max_inter_refill_gap_ms) → List[IcebergChain]
    (backward-compat wrapper: build_tranche_trees + trees_to_chains)
detect_cancellations(depth_updates, trades, refill_events, tick_size)
    → List[CancellationEvent]
process_day_chunked(depth_path, trades_path, ...)
    → Tuple[List[RefillEvent], List[TrancheTree], List[IcebergChain]]
"""

from __future__ import annotations

import logging
import uuid
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
import numpy as np

from ml_iceberg_v2.config import detect_tick_size, TICK_SIZE_DEFAULT

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes (Tech spec §4.1)
# ---------------------------------------------------------------------------

@dataclass
class RefillEvent:
    """
    A single iceberg refill event.

    A trade consumed liquidity at (price, side), and a depth_update
    restored it within max_delay_ms. This is the fundamental observable
    from the CME/DXFeed paper without L3/MBO order_id.

    All timestamps are in nanoseconds (exchange time).
    sequence fields disambiguate events with identical timestamps.
    """
    trade_timestamp: int       # ns — exchange time of the consuming trade
    refill_timestamp: int      # ns — exchange time of the restoring depth_update
    price: float
    side: str                  # "BID" or "ASK"
    trade_size: float          # lots consumed by the aggressor trade
    refill_size: float         # lots restored by the depth_update
    refill_delay_ms: float     # (refill_timestamp - trade_timestamp) / 1e6
    sequence_trade: int = 0    # exchange sequence of the trade
    sequence_refill: int = 0   # exchange sequence of the depth_update
    chain_id: Optional[str] = None  # assigned by chain_refills()
    parent_trade_key: Tuple[int, int] = (0, 0)  # (trade_timestamp, sequence_trade) — links candidates to their originating trade


@dataclass
class IcebergChain:
    """
    A sequence of consecutive RefillEvents at the same price/side.

    chain_complete is set to True when:
    - Gap between consecutive refills exceeds max_inter_refill_gap_ms, OR
    - Price level shifts away.

    Peak size = max refill_size (proxy for hidden order display quantity).
    """
    chain_id: str
    price: float
    side: str
    refill_events: List[RefillEvent] = field(default_factory=list)
    chain_complete: bool = False
    chain_weight: float = 1.0                # CME Paper §4.2: 1 / num_unique_length_chains
    source_tree_id: Optional[str] = None     # reference to originating TrancheTree

    @property
    def chain_length(self) -> int:
        return len(self.refill_events)

    @property
    def peak_size_estimated(self) -> float:
        """Maximum refill size — proxy for display quantity."""
        if not self.refill_events:
            return 0.0
        return max(e.refill_size for e in self.refill_events)

    @property
    def mean_delay_ms(self) -> float:
        if not self.refill_events:
            return 0.0
        return sum(e.refill_delay_ms for e in self.refill_events) / len(self.refill_events)

    @property
    def size_consistency(self) -> float:
        """1 − CV of refill sizes. 1.0 = perfectly consistent (ideal iceberg)."""
        sizes = [e.refill_size for e in self.refill_events]
        if len(sizes) < 2 or statistics.mean(sizes) == 0:
            return 1.0
        cv = statistics.stdev(sizes) / statistics.mean(sizes)
        return max(0.0, 1.0 - cv)


@dataclass
class TrancheNode:
    """
    A node in a TrancheTree representing one specific refill candidate.

    Each trade may have multiple depth_update candidates within the dt window
    (siblings). Children represent the next trade's candidates (shared across
    all siblings, avoiding subtree duplication).

    CME Paper §3.2, Fig. 2: each root→leaf path is one valid chain interpretation.
    """
    refill_event: RefillEvent
    children: List['TrancheNode'] = field(default_factory=list)
    depth: int = 0
    trade_siblings: List['TrancheNode'] = field(default_factory=list)


@dataclass
class TrancheTree:
    """
    Tree of RefillEvent candidates for a consecutive sequence of trades at a
    price/side level (CME Paper §3.2, Fig. 2).

    Each root→leaf path represents one valid iceberg chain interpretation.
    Multiple chains per tree support weighted Kaplan-Meier estimation:
        chain_weight = 1 / num_unique_length_chains  (CME Paper §4.2)
    """
    tree_id: str
    root_trade_timestamp: int
    root_trade_price: float
    root_trade_side: str
    root: TrancheNode

    def all_chains(self) -> List[List[RefillEvent]]:
        """Return all root→leaf paths as lists of RefillEvents (DFS)."""
        results: List[List[RefillEvent]] = []
        start_nodes = [self.root] + self.root.trade_siblings

        def dfs(node: TrancheNode, path: List[RefillEvent]) -> None:
            current = path + [node.refill_event]
            if not node.children:
                results.append(current)
            else:
                for child in node.children:
                    dfs(child, current)

        for start in start_nodes:
            dfs(start, [])
        return results

    def longest_chain(self) -> List[RefillEvent]:
        """Return the longest root→leaf path."""
        chains = self.all_chains()
        if not chains:
            return []
        return max(chains, key=len)

    def num_unique_length_chains(self) -> int:
        """Count the number of distinct chain lengths across all paths."""
        chains = self.all_chains()
        if not chains:
            return 0
        return len({len(c) for c in chains})

    @property
    def chain_weights(self) -> List[float]:
        """Per-chain weight: 1 / num_unique_length_chains (CME Paper §4.2)."""
        chains = self.all_chains()
        if not chains:
            return []
        n_unique = self.num_unique_length_chains()
        w = 1.0 / n_unique if n_unique > 0 else 1.0
        return [w] * len(chains)


@dataclass
class DepthVanishEvent:
    """
    Depth disappearing at an active iceberg level (action=delete OR size drop >80%).

    CME paper Section 3.2: cancelled icebergs are censored observations in
    the Kaplan-Meier survival model. Without MBO/L3 data, cancellation is
    detectable by depth vanishing at an active iceberg level without a
    preceding trade within a short window.

    time_since_last_trade_ms is a raw continuous value — the ML model decides
    what threshold (if any) constitutes a 'true' cancellation.

    Formerly named CancellationEvent (renamed BLOCO 2 for clarity).
    """
    timestamp: int                   # ns — when depth disappeared
    price: float
    side: str                        # "BID" or "ASK"
    time_since_last_trade_ms: float  # Time since last trade at this price/side (raw, continuous)
    depth_before: float              # Size before vanish
    depth_after: float               # Size after (0 for delete, small fraction for big drop)


# Backward-compatible alias — keeps old imports working
CancellationEvent = DepthVanishEvent


# ---------------------------------------------------------------------------
# Aggressor → passive side mapping
# ---------------------------------------------------------------------------

_AGGRESSOR_TO_PASSIVE: Dict[str, str] = {
    "buy":     "ASK",   # buyer aggresses → consumes ASK side liquidity
    "sell":    "BID",   # seller aggresses → consumes BID side liquidity
    "BUY":     "ASK",
    "SELL":    "BID",
    "B":       "ASK",
    "S":       "BID",
    "1":       "ASK",   # some numeric conventions
    "-1":      "BID",
}


def _aggressor_to_passive_side(aggressor: str) -> Optional[str]:
    """Convert aggressor field to the passive side being consumed."""
    return _AGGRESSOR_TO_PASSIVE.get(str(aggressor).strip(), None)


def _depth_side_to_canonical(side: str) -> str:
    """Normalise depth_update side to 'BID' or 'ASK'."""
    s = str(side).strip().upper()
    if s in ("BID", "B", "0"):
        return "BID"
    if s in ("ASK", "A", "OFFER", "1"):
        return "ASK"
    return s


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def detect_refills(
    depth_updates: pd.DataFrame,
    trades: pd.DataFrame,
    max_delay_ms: float = 100.0,
    tick_size: Optional[float] = None,
) -> List[RefillEvent]:
    """
    Detect RefillEvents from depth_updates and trades DataFrames.

    Algorithm
    ---------
    For each trade (sorted by timestamp, sequence):
    1. Determine passive_side = opposite of trade.aggressor.
    2. Find depth_updates where:
       - action == 'update'  (new/updated resting order)
       - side == passive_side
       - |price - trade.price| <= tick_size / 2  (same level)
       - size > 0
       - timestamp in (trade.timestamp, trade.timestamp + max_delay_ms * 1e6 ns]
       - sequence > sequence_of_trade (when timestamps are equal)
    3. Pick the earliest qualifying depth_update (closest in time).
    4. Emit a RefillEvent.

    Parameters
    ----------
    depth_updates : pd.DataFrame
        Must contain columns: timestamp (int ns), side (str), action (str),
        price (float), size (float), sequence (int).
    trades : pd.DataFrame
        Must contain columns: timestamp (int ns), price (float), size (float),
        aggressor (str), sequence (int).
    max_delay_ms : float
        Maximum milliseconds between trade and refill depth_update.
    tick_size : float or None
        Minimum price increment.  If None, auto-detected from trades prices.

    Returns
    -------
    List[RefillEvent]
        Sorted by (trade_timestamp, sequence_trade).
    """
    if trades.empty or depth_updates.empty:
        return []

    if tick_size is None:
        tick_size = detect_tick_size(trades["price"].values.astype(np.float64))

    max_delay_ns = int(max_delay_ms * 1_000_000)   # ms → ns
    half_tick = tick_size / 2.0

    # Work on numpy arrays for speed
    du = depth_updates.copy()
    du["_side_canon"] = du["side"].apply(_depth_side_to_canonical)

    # Filter to only 'update' actions with size > 0
    du_updates = du[
        (du["action"].str.lower() == "update") & (du["size"] > 0)
    ].reset_index(drop=True)

    if du_updates.empty:
        return []

    # Sort du_updates by (timestamp, sequence) to enable binary search (O(log N) per trade)
    du_updates = du_updates.sort_values(["timestamp", "sequence"]).reset_index(drop=True)

    du_ts = du_updates["timestamp"].values.astype(np.int64)
    du_price = du_updates["price"].values.astype(np.float64)
    du_side = du_updates["_side_canon"].values
    du_size = du_updates["size"].values.astype(np.float64)
    du_seq = du_updates["sequence"].values.astype(np.int64)

    # Pre-extract trade arrays for vectorised access
    t_ts_arr   = trades["timestamp"].values.astype(np.int64)
    t_price_arr = trades["price"].values.astype(np.float64)
    t_size_arr  = trades["size"].values.astype(np.float64)
    t_seq_arr   = trades.get("sequence", pd.Series(range(len(trades)))).values.astype(np.int64)
    t_agg_arr   = trades["aggressor"].values

    refills: List[RefillEvent] = []

    for i in range(len(trades)):
        passive_side = _aggressor_to_passive_side(str(t_agg_arr[i]))
        if passive_side is None:
            continue

        t_ts    = int(t_ts_arr[i])
        t_price = float(t_price_arr[i])
        t_size  = float(t_size_arr[i])
        t_seq   = int(t_seq_arr[i])
        window_end = t_ts + max_delay_ns

        # Binary search: restrict to time window [t_ts, window_end]
        # Include equal-timestamp rows for tie-break logic
        lo = int(np.searchsorted(du_ts, t_ts,       side="left"))
        hi = int(np.searchsorted(du_ts, window_end,  side="right"))

        if lo >= hi:
            continue

        # Slice to window — only O(K) rows where K ≈ updates in 100ms
        w_ts    = du_ts[lo:hi]
        w_price = du_price[lo:hi]
        w_side  = du_side[lo:hi]
        w_size  = du_size[lo:hi]
        w_seq   = du_seq[lo:hi]

        # Time filter: strictly after trade, OR same timestamp with higher sequence
        mask_after = w_ts > t_ts
        mask_tie   = (w_ts == t_ts) & (w_seq > t_seq)
        mask_time  = mask_after | mask_tie

        # Price and side filter
        mask_price = np.abs(w_price - t_price) <= half_tick
        mask_side  = w_side == passive_side

        mask = mask_time & mask_price & mask_side
        candidate_idx = np.where(mask)[0]
        if len(candidate_idx) == 0:
            continue

        # Emit ALL candidates sorted by (timestamp, sequence) — tranche tree
        # uses parent_trade_key to group candidates back to their originating trade.
        sorted_local = candidate_idx[np.lexsort((w_seq[candidate_idx], w_ts[candidate_idx]))]
        snapped_price = round(t_price / tick_size) * tick_size
        for local_idx in sorted_local:
            best = lo + int(local_idx)
            refill_ts = int(du_ts[best])
            delay_ms  = (refill_ts - t_ts) / 1_000_000.0
            refills.append(RefillEvent(
                trade_timestamp=t_ts,
                refill_timestamp=refill_ts,
                price=snapped_price,
                side=passive_side,
                trade_size=t_size,
                refill_size=float(du_size[best]),
                refill_delay_ms=delay_ms,
                sequence_trade=t_seq,
                sequence_refill=int(du_seq[best]),
                parent_trade_key=(t_ts, t_seq),
            ))

    refills.sort(key=lambda r: (r.trade_timestamp, r.sequence_trade, r.sequence_refill))
    return refills


# ---------------------------------------------------------------------------
# Tranche Tree building (CME Paper §3.2)
# ---------------------------------------------------------------------------

def _build_tree_from_levels(
    levels: List[List[RefillEvent]],
    depth: int = 0,
    max_children: int = 3,
    max_depth: int = 10,
) -> Optional[TrancheNode]:
    """
    Recursively build a TrancheNode tree from a list of trade levels.

    Parameters
    ----------
    levels : List[List[RefillEvent]]
        Each inner list contains all RefillEvent candidates for one trade.
        Levels are ordered chronologically (levels[0] = first trade's candidates).
    depth : int
        Current depth in the tree (0 = root).
    max_children : int
        Maximum number of sibling candidates to keep per trade level.
        Candidates are already sorted by (refill_timestamp, sequence_refill),
        so this keeps the temporally closest ones. Default 3 per CME paper
        §3.2: real icebergs have 2–3 refill candidates per trade.
    max_depth : int
        Maximum tree depth. Stops recursion beyond this level. Default 10
        per CME paper §3.2: observed iceberg chains have depth 5–15.

    Returns
    -------
    TrancheNode or None
        Root node for levels[0]. Siblings (other candidates for the same trade)
        are stored in root.trade_siblings. Children (levels[1:]) are shared
        across all siblings — no subtree duplication.
    """
    if not levels or not levels[0]:
        return None

    # Cap depth to prevent combinatorial explosion (CME paper §3.2: depth 5–15)
    if depth >= max_depth:
        return None

    # Recursively build subtree for deeper levels
    child_root = _build_tree_from_levels(levels[1:], depth + 1, max_children, max_depth)
    children: List[TrancheNode] = []
    if child_root is not None:
        children = [child_root] + child_root.trade_siblings

    # Cap siblings per level — keep the max_children temporally closest candidates.
    # levels[0] is already sorted by (refill_timestamp, sequence_refill) at call site.
    candidates = levels[0][:max_children]

    # Create nodes for this level (one per candidate for this trade)
    nodes = [
        TrancheNode(
            refill_event=r,
            children=children,
            depth=depth,
            trade_siblings=[],  # filled below
        )
        for r in candidates
    ]

    # Cross-link siblings
    for i, node in enumerate(nodes):
        node.trade_siblings = [n for j, n in enumerate(nodes) if j != i]

    return nodes[0]  # root = first candidate


def build_tranche_trees(
    refill_events: List[RefillEvent],
    tick_size: Optional[float] = None,
    max_inter_refill_gap_ms: float = 1000.0,
    max_children: int = 3,
    max_depth: int = 10,
) -> List[TrancheTree]:
    """
    Group RefillEvents into TrancheTree structures (CME Paper §3.2, Fig. 2).

    Algorithm
    ---------
    1. Group events by (snapped_price, side).
    2. Within each group, group by parent_trade_key to identify per-trade candidates.
    3. Sort trades chronologically; split into segments when gap between consecutive
       trades exceeds max_inter_refill_gap_ms.
    4. For each segment, call _build_tree_from_levels() to construct the tree.

    Parameters
    ----------
    refill_events : List[RefillEvent]
        Output of detect_refills(). parent_trade_key must be set.
    tick_size : float or None
    max_inter_refill_gap_ms : float
        Gap threshold to split into separate trees.
    max_children : int
        Max sibling candidates per trade level (default 3). Keeps the
        temporally closest refills; discards the rest. CME paper §3.2:
        real icebergs have 2–3 candidates per trade.
    max_depth : int
        Max tree depth (default 10). Prevents combinatorial explosion on
        high-volume days. CME paper §3.2: observed depth is 5–15.

    Returns
    -------
    List[TrancheTree]
    """
    if not refill_events:
        return []

    if tick_size is None:
        prices = np.array([e.price for e in refill_events], dtype=np.float64)
        tick_size = detect_tick_size(prices)

    max_gap_ns = int(max_inter_refill_gap_ms * 1_000_000)

    # Group events by (snapped_price, side)
    price_side_groups: Dict[Tuple[float, str], List[RefillEvent]] = defaultdict(list)
    for e in refill_events:
        snapped = round(e.price / tick_size) * tick_size
        price_side_groups[(snapped, e.side)].append(e)

    trees: List[TrancheTree] = []

    for (price, side), events in price_side_groups.items():
        # Group by parent_trade_key; fall back to (trade_ts, seq_trade) if default
        trade_cands: Dict[Tuple[int, int], List[RefillEvent]] = defaultdict(list)
        for e in events:
            key = e.parent_trade_key if e.parent_trade_key != (0, 0) else (e.trade_timestamp, e.sequence_trade)
            trade_cands[key].append(e)

        # Ordered unique trade keys (by first occurrence in chronological order)
        seen_keys: List[Tuple[int, int]] = []
        seen_set: Set[Tuple[int, int]] = set()
        for e in sorted(events, key=lambda x: (x.trade_timestamp, x.sequence_trade)):
            k = e.parent_trade_key if e.parent_trade_key != (0, 0) else (e.trade_timestamp, e.sequence_trade)
            if k not in seen_set:
                seen_set.add(k)
                seen_keys.append(k)

        # Split trades into segments on gap
        segments: List[List[List[RefillEvent]]] = []
        current_segment: List[List[RefillEvent]] = []
        prev_trade_ts: Optional[int] = None

        for key in seen_keys:
            candidates = sorted(
                trade_cands[key],
                key=lambda e: (e.refill_timestamp, e.sequence_refill),
            )
            trade_ts = key[0]  # first element of key is trade_timestamp

            if prev_trade_ts is not None and (trade_ts - prev_trade_ts) > max_gap_ns:
                if current_segment:
                    segments.append(current_segment)
                current_segment = []

            current_segment.append(candidates)
            prev_trade_ts = trade_ts

        if current_segment:
            segments.append(current_segment)

        # Build a TrancheTree per segment
        for segment in segments:
            root_node = _build_tree_from_levels(segment, max_children=max_children, max_depth=max_depth)
            if root_node is None:
                continue
            first_event = segment[0][0]
            tree = TrancheTree(
                tree_id=str(uuid.uuid4()),
                root_trade_timestamp=first_event.trade_timestamp,
                root_trade_price=price,
                root_trade_side=side,
                root=root_node,
            )
            trees.append(tree)

    return trees


def trees_to_chains(trees: List[TrancheTree]) -> List[IcebergChain]:
    """
    Flatten a list of TrancheTree into IcebergChains with weighted KM support.

    Each root→leaf path in a tree becomes one IcebergChain. Chain weight is
    set per CME Paper §4.2: chain_weight = 1 / num_unique_length_chains.

    Parameters
    ----------
    trees : List[TrancheTree]

    Returns
    -------
    List[IcebergChain]
        All chains across all trees, with chain_complete=True and
        chain_weight reflecting the weighted KM estimation.
    """
    chains: List[IcebergChain] = []
    for tree in trees:
        all_paths = tree.all_chains()
        weights = tree.chain_weights
        for chain_path, weight in zip(all_paths, weights):
            if not chain_path:
                continue
            cid = str(uuid.uuid4())
            chain = IcebergChain(
                chain_id=cid,
                price=tree.root_trade_price,
                side=tree.root_trade_side,
                refill_events=chain_path,
                chain_complete=True,
                chain_weight=weight,
                source_tree_id=tree.tree_id,
            )
            for e in chain_path:
                e.chain_id = cid
            chains.append(chain)
    return chains


# ---------------------------------------------------------------------------
# Chain building
# ---------------------------------------------------------------------------

def chain_refills(
    refill_events: List[RefillEvent],
    tick_size: Optional[float] = None,
    max_inter_refill_gap_ms: float = 1000.0,
) -> List[IcebergChain]:
    """
    Group RefillEvents into IcebergChains. Backward-compatible public API.

    Implemented via build_tranche_trees() + trees_to_chains(). When a trade
    has multiple refill candidates the resulting IcebergChains carry
    chain_weight = 1 / num_unique_length_chains for weighted KM estimation
    (CME Paper §4.2). Single-candidate trees produce chain_weight=1.0.

    Parameters
    ----------
    refill_events : List[RefillEvent]
        Output of detect_refills().
    tick_size : float or None
    max_inter_refill_gap_ms : float
        Maximum gap between consecutive trade timestamps within a chain.

    Returns
    -------
    List[IcebergChain]
        All chains (including singletons). chain_complete=True for all.
    """
    if not refill_events:
        return []
    trees = build_tranche_trees(
        refill_events,
        tick_size=tick_size,
        max_inter_refill_gap_ms=max_inter_refill_gap_ms,
    )
    return trees_to_chains(trees)


# ---------------------------------------------------------------------------
# Depth vanish detection (CME paper Section 3.2) — formerly "cancellation"
# ---------------------------------------------------------------------------

def detect_depth_vanish(
    depth_updates: pd.DataFrame,
    trades: pd.DataFrame,
    refill_events: List[RefillEvent],
    tick_size: Optional[float] = None,
) -> List[DepthVanishEvent]:
    """
    Detect depth-vanish events at price/side levels with prior iceberg activity.

    Algorithm
    ---------
    For each (price, side) pair that has had at least one RefillEvent:
    1. Find depth_updates where:
       - action == 'delete'  (level removed entirely), OR
       - action == 'update' AND size dropped > 80% vs. previous size at same level.
    2. For each such event: compute time_since_last_trade_ms as the elapsed time
       since the most recent trade at this price level (raw continuous value).
    3. Emit a DepthVanishEvent with raw fields — no binary classification.

    The ML model receives time_since_last_trade_ms as a continuous feature and
    decides what threshold constitutes a genuine cancellation (vs. refresh).

    Parameters
    ----------
    depth_updates : pd.DataFrame
        Columns: timestamp (int ns), side (str), action (str), price (float),
        size (float), sequence (int).
    trades : pd.DataFrame
        Columns: timestamp (int ns), price (float), size (float).
    refill_events : List[RefillEvent]
        From detect_refills() — defines which (price, side) pairs are active.
    tick_size : float or None

    Returns
    -------
    List[DepthVanishEvent]
        Sorted by timestamp. Raw events — ML decides what is a cancellation.
    """
    if not refill_events or depth_updates.empty:
        return []

    if tick_size is None:
        prices = np.array([e.price for e in refill_events], dtype=np.float64)
        tick_size = detect_tick_size(prices)

    half_tick = tick_size / 2.0

    # Build set of active (snapped_price, side) pairs from refill history
    active_level_keys: set = {
        (round(e.price / tick_size) * tick_size, e.side) for e in refill_events
    }

    # Normalise depth_updates
    du = depth_updates.copy()
    du["_side_canon"] = du["side"].apply(_depth_side_to_canonical)
    du["_snapped_price"] = (du["price"] / tick_size).round() * tick_size

    # Prepare sorted trade arrays for fast searchsorted lookup
    if not trades.empty:
        trade_ts_raw = trades["timestamp"].values.astype(np.int64)
        trade_px_raw = trades["price"].values.astype(np.float64)
        sort_idx = np.argsort(trade_ts_raw)
        trade_ts_sorted = trade_ts_raw[sort_idx]
        trade_px_sorted = trade_px_raw[sort_idx]
    else:
        trade_ts_sorted = np.array([], dtype=np.int64)
        trade_px_sorted = np.array([], dtype=np.float64)

    vanish_events: List[DepthVanishEvent] = []

    # Group by (snapped_price, side) for vectorised vanish detection
    for (snapped_price, side), group in du.groupby(["_snapped_price", "_side_canon"]):
        if (snapped_price, side) not in active_level_keys:
            continue

        group = group.sort_values(["timestamp", "sequence"]).reset_index(drop=True)

        sizes = group["size"].fillna(0.0).values.astype(np.float64)
        actions = group["action"].str.lower().values
        timestamps = group["timestamp"].values.astype(np.int64)

        # Previous size per row (shift by 1, first row has prev_size=0)
        prev_sizes = np.zeros(len(group), dtype=np.float64)
        prev_sizes[1:] = sizes[:-1]

        # Vanish = delete action OR size dropped > 50 % vs. previous
        # calibrated 2026-04-10: GC book p50=2 lots; 80% threshold (0.20) too strict,
        # captured only 9.6% of drops. 50% (0.50) captures 40% — appropriate for thin GC book.
        is_delete = actions == "delete"
        is_big_drop = (
            (actions == "update") &
            (prev_sizes > 0) &
            (sizes <= prev_sizes * 0.5)
        )
        vanish_mask = is_delete | is_big_drop

        if not np.any(vanish_mask):
            continue

        # Precompute trades at this price level (sorted by ts)
        if len(trade_ts_sorted) > 0:
            price_trade_mask = np.abs(trade_px_sorted - snapped_price) <= half_tick
            level_trade_ts = trade_ts_sorted[price_trade_mask]
        else:
            level_trade_ts = np.array([], dtype=np.int64)

        for i in np.where(vanish_mask)[0]:
            ts = int(timestamps[i])
            depth_before = float(prev_sizes[i])
            depth_after = 0.0 if is_delete[i] else float(sizes[i])

            # Time since last trade at this price level (raw continuous)
            if len(level_trade_ts) > 0:
                idx = int(np.searchsorted(level_trade_ts, ts, side="right")) - 1
                if idx >= 0:
                    time_since_ms = (ts - int(level_trade_ts[idx])) / 1_000_000.0
                else:
                    time_since_ms = -1.0  # vanish before any trade at this level
            else:
                time_since_ms = -1.0  # no trades data for this level

            vanish_events.append(DepthVanishEvent(
                timestamp=ts,
                price=snapped_price,
                side=side,
                time_since_last_trade_ms=time_since_ms,
                depth_before=depth_before,
                depth_after=depth_after,
            ))

    vanish_events.sort(key=lambda c: c.timestamp)
    return vanish_events


# Backward-compatible alias — keeps old callers working
detect_cancellations = detect_depth_vanish


# ---------------------------------------------------------------------------
# Chunked processing
# ---------------------------------------------------------------------------

def process_day_chunked(
    depth_path: Path,
    trades_path: Path,
    chunk_size: int = 500_000,
    overlap_buffer_s: float = 30.0,
    max_delay_ms: float = 100.0,
    tick_size: Optional[float] = None,
) -> Tuple[List[RefillEvent], List[TrancheTree], List[IcebergChain]]:
    """
    Process a full day of L2 data in memory-efficient chunks.

    Strategy:
    - Load all trades into memory (106-174K rows/day — fits easily).
    - Stream depth_updates in chunks of `chunk_size` rows.
    - Keep a 30-second overlap buffer from the previous chunk so that
      refill chains spanning a chunk boundary are not broken.
    - After refill detection, build TrancheTree structures from the
      deduplicated events, then flatten to IcebergChains.

    CancellationEvent / DepthVanishEvent detection is a separate standalone
    function (detect_cancellations / detect_depth_vanish) — not called here.

    Parameters
    ----------
    depth_path : Path
        Path to depth_updates_YYYY-MM-DD.csv.gz
    trades_path : Path
        Path to trades_YYYY-MM-DD.csv.gz
    chunk_size : int
        Rows per depth_update chunk (default 500_000).
    overlap_buffer_s : float
        Seconds of depth_update rows to carry over from previous chunk.
    max_delay_ms : float
    tick_size : float or None
        If None, auto-detected from trade prices after loading.

    Returns
    -------
    (all_refills, trees, all_chains)
        Deduplicated RefillEvents, TrancheTree list, and IcebergChains
        covering the full day.
    """
    depth_path = Path(depth_path)
    trades_path = Path(trades_path)

    # Load all trades once
    trades = pd.read_csv(trades_path, compression="gzip")
    trades = _normalise_timestamps(trades)
    trades = trades.sort_values(["timestamp", "sequence"]).reset_index(drop=True)

    # Auto-detect tick size from trade prices if not provided
    if tick_size is None:
        tick_size = detect_tick_size(trades["price"].values.astype(np.float64))
    _logger.info("Auto-detected tick_size: %s", tick_size)

    overlap_buffer_ns = int(overlap_buffer_s * 1e9)

    all_refills: List[RefillEvent] = []
    overlap_rows: Optional[pd.DataFrame] = None
    processed_trade_ts_max: int = 0   # track progress to avoid re-processing trades

    for chunk in pd.read_csv(depth_path, compression="gzip", chunksize=chunk_size):
        chunk = _normalise_timestamps(chunk)

        # Prepend overlap from previous chunk
        if overlap_rows is not None and not overlap_rows.empty:
            chunk = pd.concat([overlap_rows, chunk], ignore_index=True)

        chunk = chunk.sort_values(["timestamp", "sequence"]).reset_index(drop=True)

        if chunk.empty:
            continue

        chunk_ts_min = int(chunk["timestamp"].iloc[0])
        chunk_ts_max = int(chunk["timestamp"].iloc[-1])

        # Only process trades that fall within this chunk's time window
        trades_in_chunk = trades[
            (trades["timestamp"] >= chunk_ts_min - int(max_delay_ms * 1e6)) &
            (trades["timestamp"] <= chunk_ts_max)
        ]

        if not trades_in_chunk.empty:
            chunk_refills = detect_refills(
                depth_updates=chunk,
                trades=trades_in_chunk,
                max_delay_ms=max_delay_ms,
                tick_size=tick_size,
            )
            all_refills.extend(chunk_refills)

        # Build overlap: keep last `overlap_buffer_s` seconds of depth_updates
        overlap_cutoff = chunk_ts_max - overlap_buffer_ns
        overlap_rows = chunk[chunk["timestamp"] >= overlap_cutoff].copy()

    # Deduplicate by (trade_timestamp, sequence_trade, sequence_refill) — overlap may produce duplicates
    seen: Set[Tuple[int, int, int]] = set()
    unique_refills: List[RefillEvent] = []
    for r in all_refills:
        key = (r.trade_timestamp, r.sequence_trade, r.sequence_refill)
        if key not in seen:
            seen.add(key)
            unique_refills.append(r)

    unique_refills.sort(key=lambda r: (r.trade_timestamp, r.sequence_trade, r.sequence_refill))

    # Build tranche trees and flatten to chains
    trees = build_tranche_trees(unique_refills, tick_size=tick_size)
    chains = trees_to_chains(trees)

    return unique_refills, trees, chains


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure 'timestamp' column is int64 nanoseconds and 'sequence' is int64.
    Handles both ns-int and datetime string representations.
    """
    df = df.copy()

    if "timestamp" in df.columns:
        ts_dtype = df["timestamp"].dtype
        if str(ts_dtype).startswith("datetime") or str(ts_dtype).startswith("object") or ts_dtype.kind in ("O", "U", "S"):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).astype(np.int64)
        else:
            df["timestamp"] = df["timestamp"].astype(np.int64)

    if "sequence" not in df.columns:
        df["sequence"] = range(len(df))
    else:
        df["sequence"] = df["sequence"].fillna(0).astype(np.int64)

    return df


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def load_cached_refills(refills_path: "Path") -> "List[RefillEvent]":
    """
    Reconstruct a list of RefillEvent objects from a cached Parquet file.

    The Parquet must have been written by run_bulk_t107_t108.py with columns:
        trade_timestamp, refill_timestamp, price, side, trade_size,
        refill_size, refill_delay_ms, sequence_trade, sequence_refill, chain_id

    parent_trade_key defaults to (0, 0) — not persisted to keep the schema simple.
    """
    from pathlib import Path as _Path
    df = pd.read_parquet(str(refills_path))
    return [
        RefillEvent(
            trade_timestamp=int(row.trade_timestamp),
            refill_timestamp=int(row.refill_timestamp),
            price=float(row.price),
            side=str(row.side),
            trade_size=float(row.trade_size),
            refill_size=float(row.refill_size),
            refill_delay_ms=float(row.refill_delay_ms),
            sequence_trade=int(row.sequence_trade),
            sequence_refill=int(row.sequence_refill),
            chain_id=str(row.chain_id) if row.chain_id else None,
        )
        for row in df.itertuples(index=False)
    ]

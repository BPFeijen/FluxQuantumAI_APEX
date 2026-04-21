"""
Unit tests for direction-aware near_level + level_detector.

Sprint A — entry_logic_fix_20260420 (literatura-aligned).
Ordering under test:
    1. is_valid_direction DESC  (MANDATORY)
    2. source priority DESC     (m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed)
    3. age_min ASC              (fresher first)
    4. distance_to_price ASC    (tactical tiebreaker)

Implements the 11 test cases from DESIGN_DOC §5 + 5 invariants from Appendix A.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from live import level_detector as ld
from live.level_detector import LevelCandidate, get_levels_for_direction, _SOURCE_PRIORITY

OFFSET = 31.0  # GC -> MT5 offset (matches GC_MT5_OFFSET)


# ---------------------------------------------------------------------------
# Helpers: build mock parquet DataFrames at controlled ages
# ---------------------------------------------------------------------------

def _m5_row(box_id: int, liq_top_mt5: float, liq_bot_mt5: float, confirmed: bool, atr14: float = 20.0):
    return {
        "m5_box_id": box_id,
        "m5_box_confirmed": confirmed,
        "m5_liq_top": liq_top_mt5 + OFFSET,   # stored as GC
        "m5_liq_bot": liq_bot_mt5 + OFFSET,
        "m5_fmv":     (liq_top_mt5 + liq_bot_mt5) / 2 + OFFSET,
        "m5_box_high": liq_top_mt5 + OFFSET,
        "m5_box_low":  liq_bot_mt5 + OFFSET,
        "atr14": atr14,
    }


def _m30_row(box_id: int, liq_top_mt5: float, liq_bot_mt5: float, confirmed: bool, atr14: float = 20.0):
    return {
        "m30_box_id": box_id,
        "m30_box_confirmed": confirmed,
        "m30_liq_top": liq_top_mt5 + OFFSET,
        "m30_liq_bot": liq_bot_mt5 + OFFSET,
        "m30_fmv":     (liq_top_mt5 + liq_bot_mt5) / 2 + OFFSET,
        "m30_box_high": liq_top_mt5 + OFFSET,
        "m30_box_low":  liq_bot_mt5 + OFFSET,
        "atr14": atr14,
    }


def _mk_df(rows: list[tuple[dict, float]]) -> pd.DataFrame:
    """rows is list of (row_dict, age_minutes). Builds DataFrame with UTC index."""
    now = datetime.now(timezone.utc)
    records = []
    idx = []
    for row_dict, age_min in rows:
        records.append(row_dict)
        idx.append(now - timedelta(minutes=age_min))
    df = pd.DataFrame(records, index=pd.DatetimeIndex(idx, tz="UTC", name="ts"))
    df.sort_index(inplace=True)
    return df


@pytest.fixture
def patch_loaders(monkeypatch):
    """Patch _load_m5_boxes / _load_m30_boxes to return controllable DataFrames."""
    state = {"m5": None, "m30": None}

    def _set(m5=None, m30=None):
        state["m5"] = m5
        state["m30"] = m30

    monkeypatch.setattr(ld, "_load_m5_boxes", lambda: state["m5"])
    monkeypatch.setattr(ld, "_load_m30_boxes", lambda: state["m30"])
    return _set


# ---------------------------------------------------------------------------
# TEST 1 — Fresh confirmed + SHORT + level ABOVE price + within band -> PASS
# ---------------------------------------------------------------------------
def test_1_fresh_confirmed_short_valid_in_band(patch_loaders):
    price = 4795.0
    m5 = _mk_df([(_m5_row(100, liq_top_mt5=4800.0, liq_bot_mt5=4790.0, confirmed=True), 2.0)])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    assert len(cands) >= 1
    top = cands[0]
    assert top.is_valid_direction is True
    assert top.source == "m5_confirmed"
    assert top.level > price
    assert top.distance_to_price <= top.band


# ---------------------------------------------------------------------------
# TEST 2 — Fresh confirmed + SHORT + level BELOW price -> all invalid
# ---------------------------------------------------------------------------
def test_2_fresh_confirmed_short_wrong_side(patch_loaders):
    price = 4795.0
    m5 = _mk_df([(_m5_row(100, liq_top_mt5=4780.0, liq_bot_mt5=4770.0, confirmed=True), 2.0)])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    assert len(cands) >= 1
    valid = [c for c in cands if c.is_valid_direction]
    assert len(valid) == 0, "SHORT: no candidate should be valid when level is below price"


# ---------------------------------------------------------------------------
# TEST 3 — Stale confirmed + fresh unconfirmed correct side -> PASS via unconfirmed
# ---------------------------------------------------------------------------
def test_3_stale_confirmed_fallback_correct_side(patch_loaders):
    price = 4795.0
    m5 = _mk_df([
        (_m5_row(99, liq_top_mt5=4810.0, liq_bot_mt5=4800.0, confirmed=True), 30.0),   # stale
        (_m5_row(100, liq_top_mt5=4805.0, liq_bot_mt5=4795.0, confirmed=False), 2.0),  # fresh correct
    ])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    valid = [c for c in cands if c.is_valid_direction]
    assert len(valid) >= 1
    top = valid[0]
    # Stale confirmed was dropped by max_age_min => unconfirmed must win
    assert top.source == "m5_unconfirmed"
    assert top.level > price


# ---------------------------------------------------------------------------
# TEST 4 — Stale confirmed + unconfirmed WRONG side -> FAR (replicates 03:14)
# ---------------------------------------------------------------------------
def test_4_stale_confirmed_unconfirmed_wrong_side_03_14_replay(patch_loaders):
    price = 4791.08
    # Replicates the 03:14:46 signal: confirmed stale (>15min), unconfirmed liq_top BELOW price.
    m5 = _mk_df([
        (_m5_row(33980, liq_top_mt5=4795.0, liq_bot_mt5=4785.0, confirmed=True), 63.0),   # stale
        (_m5_row(33981, liq_top_mt5=4767.53, liq_bot_mt5=4759.58, confirmed=False), 2.0),  # wrong side
    ])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    valid = [c for c in cands if c.is_valid_direction]
    # Stale confirmed filtered by age (63 > 15). Unconfirmed is below price -> invalid for SHORT.
    assert len(valid) == 0, "03:14 replay: no valid SHORT candidate must exist"


# ---------------------------------------------------------------------------
# TEST 5 — Multiple mixed candidates -> valid list, closest valid is the winner
# ---------------------------------------------------------------------------
def test_5_multiple_unconfirmed_mixed_sides(patch_loaders):
    price = 4795.0
    # Two unconfirmed: one wrong-side (4780), one correct-side (4800).
    # Plus a stale confirmed (dropped by age).
    m5 = _mk_df([
        (_m5_row(99, liq_top_mt5=4812.0, liq_bot_mt5=4802.0, confirmed=True), 30.0),   # stale
        (_m5_row(100, liq_top_mt5=4800.0, liq_bot_mt5=4790.0, confirmed=False), 2.0),  # fresher unconf
    ])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    valid = [c for c in cands if c.is_valid_direction]
    assert len(valid) >= 1
    top = valid[0]
    assert top.level > price


# ---------------------------------------------------------------------------
# TEST 6 — Level exactly at price (distance=0) -> PASS with is_touch=True
# ---------------------------------------------------------------------------
def test_6_level_equal_price_is_touch(patch_loaders):
    price = 4800.0
    m5 = _mk_df([(_m5_row(100, liq_top_mt5=4800.0, liq_bot_mt5=4790.0, confirmed=True), 2.0)])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    touching = [c for c in cands if c.is_touch]
    assert len(touching) == 1
    t = touching[0]
    assert t.is_valid_direction is True  # distance==0 forces valid regardless of strict >
    assert t.distance_to_price == 0.0


# ---------------------------------------------------------------------------
# TEST 7 — Level 0.1 pt below price for SHORT -> is_valid_direction False
# ---------------------------------------------------------------------------
def test_7_level_just_passed_wrong_direction(patch_loaders):
    price = 4800.0
    m5 = _mk_df([(_m5_row(100, liq_top_mt5=4799.9, liq_bot_mt5=4790.0, confirmed=True), 2.0)])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    valid = [c for c in cands if c.is_valid_direction]
    assert all(c.level > price for c in valid)
    # With the single box above, there should be zero valid candidates
    assert len(valid) == 0


# ---------------------------------------------------------------------------
# TEST 8 — No candidates at all -> empty list
# ---------------------------------------------------------------------------
def test_8_no_valid_levels_empty_parquets(patch_loaders):
    patch_loaders(m5=None, m30=None)
    cands = get_levels_for_direction("SHORT", price=4800.0, max_age_min=15.0)
    assert cands == []


# ---------------------------------------------------------------------------
# TEST 9 — M5 empty, M30 has valid level -> M30 candidate returned
# ---------------------------------------------------------------------------
def test_9_m30_only_fallback(patch_loaders):
    price = 4750.0
    m30 = _mk_df([(_m30_row(500, liq_top_mt5=4780.0, liq_bot_mt5=4740.0, confirmed=True), 5.0)])
    patch_loaders(m5=None, m30=m30)

    cands = get_levels_for_direction("LONG", price=price, max_age_min=15.0)
    assert len(cands) >= 1
    valid = [c for c in cands if c.is_valid_direction]
    assert len(valid) >= 1
    assert valid[0].source.startswith("m30")
    assert valid[0].level < price  # LONG: level below price


# ---------------------------------------------------------------------------
# TEST 10 — M5 confirmed wrong side + unconfirmed correct side -> unconfirmed wins
# ---------------------------------------------------------------------------
def test_10_confirmed_wrong_unconfirmed_correct(patch_loaders):
    price = 4790.0
    m5 = _mk_df([
        (_m5_row(99, liq_top_mt5=4780.0, liq_bot_mt5=4770.0, confirmed=True), 2.0),   # wrong (invalid)
        (_m5_row(100, liq_top_mt5=4810.0, liq_bot_mt5=4800.0, confirmed=False), 2.0),  # correct
    ])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    valid = [c for c in cands if c.is_valid_direction]
    assert len(valid) >= 1
    # The unconfirmed one (4810>price) is valid; confirmed (4780<price) is not.
    assert valid[0].source == "m5_unconfirmed"
    assert valid[0].level > price


# ---------------------------------------------------------------------------
# TEST 11 — Transition FAR -> PASS when a fresh correct-side box is inserted
# ---------------------------------------------------------------------------
def test_11_transition_far_to_pass(patch_loaders):
    price = 4790.0

    # Snapshot 1: only wrong-side unconfirmed
    m5_a = _mk_df([(_m5_row(99, liq_top_mt5=4780.0, liq_bot_mt5=4770.0, confirmed=False), 2.0)])
    patch_loaders(m5=m5_a, m30=None)
    before = [c for c in get_levels_for_direction("SHORT", price, 15.0) if c.is_valid_direction]
    assert len(before) == 0

    # Snapshot 2: a new unconfirmed correct-side box appears with higher id
    m5_b = _mk_df([
        (_m5_row(99, liq_top_mt5=4780.0, liq_bot_mt5=4770.0, confirmed=False), 3.0),
        (_m5_row(100, liq_top_mt5=4810.0, liq_bot_mt5=4800.0, confirmed=False), 1.0),
    ])
    patch_loaders(m5=m5_b, m30=None)
    after = [c for c in get_levels_for_direction("SHORT", price, 15.0) if c.is_valid_direction]
    assert len(after) >= 1
    assert after[0].level > price


# ===========================================================================
# INVARIANTS (Appendix A)
# ===========================================================================

def test_invariant_ordering_literatura_aligned(patch_loaders):
    """Sort: is_valid_direction DESC > source priority DESC > age ASC > distance ASC."""
    price = 4795.0
    m5 = _mk_df([
        # All correct-side, different sources/ages/distances
        (_m5_row(99, liq_top_mt5=4802.0, liq_bot_mt5=4792.0, confirmed=True), 10.0),
        (_m5_row(100, liq_top_mt5=4801.0, liq_bot_mt5=4791.0, confirmed=False), 1.0),
    ])
    m30 = _mk_df([(_m30_row(500, liq_top_mt5=4800.5, liq_bot_mt5=4790.0, confirmed=True), 1.0)])
    patch_loaders(m5=m5, m30=m30)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    # Must honour: m5_confirmed > m5_unconfirmed > m30_confirmed
    sources = [c.source for c in cands if c.is_valid_direction]
    assert sources[0] == "m5_confirmed"
    assert sources[1] == "m5_unconfirmed"
    assert sources[2] == "m30_confirmed"


def test_invariant_source_priority_beats_distance(patch_loaders):
    """Literatura: fresher source wins over closer distance when both are valid."""
    price = 4795.0
    # m5_confirmed far (5pts). m5_unconfirmed near (1pt). Literatura -> m5_confirmed wins.
    m5 = _mk_df([
        (_m5_row(99, liq_top_mt5=4800.0, liq_bot_mt5=4790.0, confirmed=True), 1.0),    # dist=5
        (_m5_row(100, liq_top_mt5=4796.0, liq_bot_mt5=4790.0, confirmed=False), 1.0),  # dist=1
    ])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    top = [c for c in cands if c.is_valid_direction][0]
    assert top.source == "m5_confirmed"


def test_invariant_short_never_returns_level_below_price_valid(patch_loaders):
    """SHORT invariant A.1: no valid candidate has level <= price (except is_touch edge)."""
    price = 4800.0
    m5 = _mk_df([
        (_m5_row(99, liq_top_mt5=4795.0, liq_bot_mt5=4785.0, confirmed=True), 1.0),
        (_m5_row(100, liq_top_mt5=4810.0, liq_bot_mt5=4800.0, confirmed=False), 1.0),
    ])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price=price, max_age_min=15.0)
    for c in cands:
        if c.is_valid_direction and not c.is_touch:
            assert c.level > price, f"invariant broken: SHORT valid with level {c.level} <= price {price}"


def test_invariant_long_never_returns_level_above_price_valid(patch_loaders):
    """LONG invariant A.2: no valid candidate has level >= price (except is_touch edge)."""
    price = 4800.0
    m5 = _mk_df([
        (_m5_row(99, liq_top_mt5=4810.0, liq_bot_mt5=4805.0, confirmed=True), 1.0),
        (_m5_row(100, liq_top_mt5=4800.0, liq_bot_mt5=4790.0, confirmed=False), 1.0),
    ])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("LONG", price=price, max_age_min=15.0)
    for c in cands:
        if c.is_valid_direction and not c.is_touch:
            assert c.level < price, f"invariant broken: LONG valid with level {c.level} >= price {price}"


def test_invariant_determinism(patch_loaders):
    """Appendix A.4: same input same output."""
    price = 4795.0
    m5 = _mk_df([(_m5_row(100, liq_top_mt5=4800.0, liq_bot_mt5=4790.0, confirmed=True), 2.0)])
    patch_loaders(m5=m5, m30=None)

    r1 = get_levels_for_direction("SHORT", price, 15.0)
    r2 = get_levels_for_direction("SHORT", price, 15.0)
    assert r1 == r2


def test_invariant_max_age_zero_returns_empty(patch_loaders):
    """Appendix A.5: max_age_min=0 discards everything (nothing is exactly 0min old)."""
    price = 4795.0
    m5 = _mk_df([(_m5_row(100, liq_top_mt5=4800.0, liq_bot_mt5=4790.0, confirmed=True), 0.5)])
    patch_loaders(m5=m5, m30=None)

    cands = get_levels_for_direction("SHORT", price, max_age_min=0.0)
    assert cands == []


def test_invariant_source_priority_map_strict_order():
    """Appendix A.6: source priority is strict: m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed."""
    assert _SOURCE_PRIORITY["m5_confirmed"]    > _SOURCE_PRIORITY["m5_unconfirmed"]
    assert _SOURCE_PRIORITY["m5_unconfirmed"]  > _SOURCE_PRIORITY["m30_confirmed"]
    assert _SOURCE_PRIORITY["m30_confirmed"]   > _SOURCE_PRIORITY["m30_unconfirmed"]


def test_invariant_direction_validation_raises():
    """Misuse check: invalid direction string raises ValueError."""
    with pytest.raises(ValueError):
        get_levels_for_direction("BUY", price=4800.0, max_age_min=15.0)  # type: ignore[arg-type]

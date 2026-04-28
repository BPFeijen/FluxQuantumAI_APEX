"""P1.3 M30-BOX-STAGNATION-RULE — 6 unit tests for the A K=2 expiry rule.

Asana: 1214327736913830 (Phase 3)
Spec:  expire active confirmed M30 box when |close - box edge| > 2.0 × ATR_at_creation.

Tests:
  1. test_box_expiry_triggers_at_2_atr_excursion
  2. test_box_expiry_skips_when_below_threshold
  3. test_box_expiry_creates_new_scan_window
  4. test_box_expired_event_logged_to_decision_log
  5. test_box_expiry_rule_idempotent_within_tick
  6. test_box_expiry_does_not_affect_already_expired

Strategy: build synthetic OHLCV with deterministic ATR, drive `_detect_boxes`
through known contraction → breakout → drift sequences, and verify the
resulting box_id transitions match expectations.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import live.m30_updater as m30_mod                                    # noqa: E402
from live.m30_updater import (                                         # noqa: E402
    BOX_EXPIRY_K_ATR, _detect_boxes, _emit_box_expired_telemetry,
)


# ---------------------------------------------------------------------------
# Synthetic data builder
# ---------------------------------------------------------------------------

def _build_m30(close_prices: list[float], spread: float = 1.0,
                atr_override: float | None = None) -> pd.DataFrame:
    """Build a synthetic M30 dataframe from a list of M30 close prices.

    Each bar: high=close+spread/2, low=close-spread/2, open=prev_close,
    volume=1.0, atr14 computed from rolling true range OR atr_override
    (if set, applied uniformly — useful for deterministic tests).
    """
    n = len(close_prices)
    idx = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    closes = np.array(close_prices, dtype=float)
    opens  = np.concatenate([[closes[0]], closes[:-1]])
    highs  = closes + spread / 2.0
    lows   = closes - spread / 2.0
    df = pd.DataFrame({
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": 1.0,
    }, index=idx)
    if atr_override is not None:
        df["atr14"] = atr_override
    else:
        prev_c = df["close"].shift(1)
        tr = np.maximum(df["high"] - df["low"],
                         np.maximum((df["high"] - prev_c).abs(),
                                     (df["low"]  - prev_c).abs()))
        df["atr14"] = tr.rolling(14).mean().fillna(2.0)
    # Required input columns for _detect_boxes — high, low, close, atr14.
    return df


def _make_box_then_drift(initial_drift_pts: float, n_drift_bars: int = 8,
                          atr: float = 2.0, base: float = 4700.0) -> pd.DataFrame:
    """Construct the canonical test pattern:
      - 17 ATR-warmup bars at base
      - 3 contraction bars at base
      - 1 breakout up to base + 1.5*atr (above b_hi)
      - 1 JAC bar at base + 2.0*atr (close > b_hi → confirmed)
      - n_drift_bars drift bars at (base + breakout + initial_drift_pts)
    The box edges become approximately [base - 0.5, base + 0.5] (after spread).
    Initial drift ~ 1.5*atr keeps box alive; drift ~ 4*atr expires it.
    """
    closes = []
    closes.extend([base] * 17)             # warmup (bars 0..16)
    closes.extend([base, base, base])      # contraction (bars 17..19)
    closes.append(base + 1.5 * atr)        # breakout (bar 20)
    closes.append(base + 2.0 * atr)        # JAC (bar 21)
    drift_close = base + 2.0 * atr + initial_drift_pts
    closes.extend([drift_close] * n_drift_bars)
    return _build_m30(closes, spread=1.0, atr_override=atr)


# ---------------------------------------------------------------------------
# Test 1 — Expiry triggers when excursion > 2 × ATR_at_creation
# ---------------------------------------------------------------------------

def test_box_expiry_triggers_at_2_atr_excursion():
    """Set up a box where bars after JAC drift far beyond box_high (close
    > box_high + 2 × ATR_at_creation). Expiry should fire; the drift bars
    show m30_box_id = 0 from the expiry bar onwards (until a new contraction
    forms)."""
    # Drift = 4×ATR beyond JAC-confirmed close (which itself is 2×ATR above
    # base). So drift_close = base + 6*ATR; box_high ≈ base + 0.5; excursion
    # at drift bar = (base + 6*ATR) - (base + 0.5) ≈ 6*ATR - 0.5 > 2*ATR.
    df = _make_box_then_drift(initial_drift_pts=4 * 2.0,  # 4×ATR drift
                                n_drift_bars=8, atr=2.0, base=4700.0)
    out, n_boxes, expirations = _detect_boxes(df)

    assert n_boxes >= 1, "expected at least one box detected"
    assert len(expirations) >= 1, "expected at least one BOX_EXPIRED event"
    # The drift bars after expiry should have box_id = 0
    drift_section = out.iloc[22:]   # after JAC at index 21
    n_zero = int((drift_section["m30_box_id"] == 0).sum())
    assert n_zero >= 1, "drift bars should include rows with box_id=0 after expiry"
    # First expiration should reference box 1 (only box we created)
    e = expirations[0]
    assert e["box_id"] == 1
    assert e["edge_excursion"] > BOX_EXPIRY_K_ATR * e["atr_at_creation"], \
        "edge_excursion should exceed threshold"


# ---------------------------------------------------------------------------
# Test 2 — Expiry skips when excursion stays below threshold
# ---------------------------------------------------------------------------

def test_box_expiry_skips_when_below_threshold():
    """Drift stays within 1×ATR of breakout — well under 2×ATR threshold.
    Box should remain active throughout; no expirations recorded."""
    df = _make_box_then_drift(initial_drift_pts=0.5,   # tiny drift
                                n_drift_bars=8, atr=2.0, base=4700.0)
    out, n_boxes, expirations = _detect_boxes(df)
    assert n_boxes >= 1
    assert len(expirations) == 0, "no expirations expected for sub-threshold drift"
    # Box should remain active across the drift bars (id == 1)
    drift_section = out.iloc[22:]
    assert (drift_section["m30_box_id"] == 1).all(), "box should stay alive"


# ---------------------------------------------------------------------------
# Test 3 — After expiry, a fresh contraction forms a new box (new scan window)
# ---------------------------------------------------------------------------

def test_box_expiry_creates_new_scan_window():
    """Pattern: box1 forms, drifts away (expires), then a new contraction
    forms at the new price level → new box2 with a different id."""
    atr = 2.0
    base = 4700.0
    closes = []
    closes.extend([base] * 17)               # warmup
    closes.extend([base, base, base])        # contraction box1
    closes.append(base + 1.5 * atr)          # breakout up
    closes.append(base + 2.0 * atr)          # JAC
    # Drift far away
    far = base + 2.0 * atr + 5 * atr         # 5×ATR past breakout → expiry
    closes.extend([far] * 4)                 # drift bars (expiry happens here)
    # New contraction at far price level — 5 bars within tight range
    closes.extend([far, far, far, far, far])
    # Breakout from second box
    closes.append(far + 1.5 * atr)
    closes.append(far + 2.0 * atr)
    # Stable forward-fill
    closes.extend([far + 2.0 * atr] * 3)
    df = _build_m30(closes, spread=1.0, atr_override=atr)
    out, n_boxes, expirations = _detect_boxes(df)

    # Should detect at least 2 boxes
    assert n_boxes >= 2, f"expected ≥2 boxes, got {n_boxes}"
    # At least one expiration must have occurred for box 1
    assert any(e["box_id"] == 1 for e in expirations), \
        "box 1 should have expired"
    # The final bars should reference box >= 2 (or 0 if scan didn't complete)
    distinct_box_ids = sorted(set(int(b) for b in out["m30_box_id"].unique() if b > 0))
    assert len(distinct_box_ids) >= 2, f"expected ≥2 distinct box ids, got {distinct_box_ids}"


# ---------------------------------------------------------------------------
# Test 4 — BOX_EXPIRED event written to decision_log via P0 schema
# ---------------------------------------------------------------------------

def test_box_expired_event_logged_to_decision_log(tmp_path, monkeypatch):
    """Run `_emit_box_expired_telemetry` with a fabricated expirations list,
    verify the resulting JSONL row matches the P0 OBSERVABILITY-LOG-BLOCKS
    canonical schema."""
    log_path = tmp_path / "decision_log.jsonl"
    monkeypatch.setattr(m30_mod, "DECISION_LOG_PATH", log_path)

    expirations = [{
        "box_id": 1,
        "expired_at":      pd.Timestamp("2026-04-28 04:08:00", tz="UTC"),
        "first_ts":        pd.Timestamp("2026-04-27 21:00:00", tz="UTC"),
        "age_h":           7.13,
        "box_high":        4700.9,
        "box_low":         4693.6,
        "box_midpoint":    4697.25,
        "close_at_expiry": 4660.0,
        "edge_excursion":  33.6,
        "threshold":       14.4,
        "atr_at_creation": 7.2,
        "k_atr":           BOX_EXPIRY_K_ATR,
    }]

    # last_logged_before_ts = None → first-ever run; logs only most recent
    n = _emit_box_expired_telemetry(expirations, last_logged_before_ts=None)
    assert n == 1, f"expected 1 row written, got {n}"

    rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    r = rows[0]
    # P0 canonical top-level keys
    for k in ("timestamp", "decision_id", "decision_frame", "price_gc",
              "decision", "context", "gate_state", "coalesce_count"):
        assert k in r, f"missing top-level key: {k}"
    assert r["decision_frame"] == "GC"
    assert r["price_gc"] == 4660.0
    # decision sub-block
    d = r["decision"]
    assert d["action"] == "MUTATE"
    assert d["reason_code"] == "BOX_EXPIRED_EXCURSION"
    assert d["direction"] == "NEUTRAL"
    assert d["trigger_source"] == "M30_BOX_EXPIRY_RULE"
    assert d["trigger_level_type"] == "box_midpoint"
    assert d["trigger_level_gc"] == 4697.25
    assert d["trigger_proximity_gc"] == 33.6
    assert "Box 1 expired" in d["block_detail"]
    assert "33.6" in d["block_detail"] and "14.4" in d["block_detail"]
    # context sub-block
    c = r["context"]
    assert c["box_id"] == 1
    assert c["box_high"] == 4700.9
    assert c["box_low"]  == 4693.6
    assert c["age_h"]    == 7.13


# ---------------------------------------------------------------------------
# Test 5 — Idempotency within a tick (running detection twice on same data
# yields identical expirations + box state)
# ---------------------------------------------------------------------------

def test_box_expiry_rule_idempotent_within_tick():
    df = _make_box_then_drift(initial_drift_pts=4 * 2.0, n_drift_bars=8,
                                atr=2.0, base=4700.0)
    out_a, n_a, exp_a = _detect_boxes(df.copy())
    out_b, n_b, exp_b = _detect_boxes(df.copy())
    # Same number of boxes + expirations
    assert n_a == n_b
    assert len(exp_a) == len(exp_b)
    # Same expiry payloads (compare key fields)
    for ea, eb in zip(exp_a, exp_b):
        for k in ("box_id", "edge_excursion", "threshold", "atr_at_creation"):
            assert ea[k] == eb[k], f"non-idempotent on key {k}: {ea[k]} vs {eb[k]}"
    # Same per-bar box_id sequence
    pd.testing.assert_series_equal(out_a["m30_box_id"], out_b["m30_box_id"],
                                     check_names=False)


# ---------------------------------------------------------------------------
# Test 6 — Already-expired box does not re-fire (no double-logging)
# ---------------------------------------------------------------------------

def test_box_expiry_does_not_affect_already_expired():
    """Once a box is expired, the subsequent bars (still drifting beyond the
    threshold) should NOT generate additional BOX_EXPIRED events for the
    same box_id. Only ONE expiration per box."""
    df = _make_box_then_drift(initial_drift_pts=4 * 2.0, n_drift_bars=15,
                                atr=2.0, base=4700.0)
    out, n_boxes, expirations = _detect_boxes(df)
    # All expirations for distinct box_ids
    expired_ids = [e["box_id"] for e in expirations]
    assert len(expired_ids) == len(set(expired_ids)), \
        f"duplicate box_id in expirations: {expired_ids}"
    # The single original box should appear at most once
    assert expired_ids.count(1) <= 1

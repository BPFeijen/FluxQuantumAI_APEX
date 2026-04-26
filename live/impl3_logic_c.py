"""
live/impl3_logic_c.py — IMPL-3 LOGIC-C Scoring Engine (EXEC-5 2026-04-26)

Convergent-evidence exhaustion scorer that consumes the 5 Wyckoff-grounded
features produced by live/impl2_features.py (IMPL-2 / EXEC-4) and emits a
single weighted score + threshold verdict.

Per Asana 1214204536528025 + T1-X1-INTERACTIONS_v2.md §14:

    FEATURE_WEIGHTS = {
        "F5_B": 0.640,   # ClosePct × TREND-B  (strongest)
        "F3_B": 0.543,   # Delta Divergence × TREND-B
        "F5_A": 0.394,   # ClosePct × TREND-A
        "F2_B": 0.274,   # EFR × TREND-B
        "F1_B": 0.213,   # SOT × TREND-B
    }
    EXHAUSTION_SCORE_THRESHOLD = 0.4    # strict (>)

    max possible score = 2.064 (sum of all 5 weights)
    threshold 0.4 ≈ 19% of max → ~2 features concurrent (typical activation)

Expectancy verified offline: +0.1312 pts @+60m (+32% vs LOGIC-A baseline),
temporally stable (zero negative months) — see §13 of T1-X1-INTERACTIONS_v2.

═══════════════════════════════════════════════════════════════════════
METHODOLOGY GROUNDING (Standing Rule 15 G-METHODOLOGY-FIRST-WORKFLOW)
═══════════════════════════════════════════════════════════════════════

The threshold 0.4 implements Wyckoff Law 1 (Supply / Demand validation
via convergent evidence). A single feature firing (max single weight
0.640 for F5_B) is enough on its own; lighter features (F1_B 0.213,
F2_B 0.274) must combine with at least one other feature to pass.

  - Wyckoff Law 1 ("Supply and Demand"):
      Villahermosa "Wyckoff 2.0", Book 2 — convergent-evidence principle:
      a single bar-level signal is rarely sufficient to call exhaustion;
      multiple independent bar/structure signals lining up (e.g. SOT +
      weak close + delta divergence) provide validation that the
      composite supply/demand balance has shifted.
      File: 533397707-Ruben-Villahermosa-Wyckoff-2-0-...-Book-2.pdf

The weights are FROZEN from the v2 hypothesis-test cycle (Cohen's d /
permutation-robust ranking on Window B Jul 2025→Apr 2026, n_bars≈12k
M30 — see T1-X1-INTERACTIONS_v2.md §14.1). They are NOT for re-tuning
in this module; any change requires a fresh hypothesis cycle.

═══════════════════════════════════════════════════════════════════════
USAGE
═══════════════════════════════════════════════════════════════════════

    from live.impl3_logic_c import is_exhaustion_signal

    # feature_state comes from impl2_features.evaluate_all(df, regime_state)
    feature_state = {"F1_B": True, "F2_B": False, "F3_B": False,
                     "F5_A": False, "F5_B": True}

    is_exh, score, active = is_exhaustion_signal(feature_state)
    # → (True, 0.853, ["F5_B", "F1_B"])  — 0.640 + 0.213 = 0.853 > 0.4
"""

from __future__ import annotations

import logging
from typing import Iterable

log = logging.getLogger("apex.impl3")


# ============================================================================
# Frozen calibration constants (T1-X1-INTERACTIONS_v2.md §14)
# ============================================================================

FEATURE_WEIGHTS = {
    "F5_B": 0.640,
    "F3_B": 0.543,
    "F5_A": 0.394,
    "F2_B": 0.274,
    "F1_B": 0.213,
}

EXHAUSTION_SCORE_THRESHOLD = 0.4   # strict >; ties at 0.4 are inactive

# Theoretical maxima (informational, not used as gates).
MAX_POSSIBLE_SCORE = sum(FEATURE_WEIGHTS.values())   # 2.064
NUM_FEATURES = len(FEATURE_WEIGHTS)                  # 5


# ============================================================================
# Public API
# ============================================================================

def compute_score(feature_state: dict) -> tuple[float, list[str]]:
    """Compute the weighted exhaustion score from the feature_state dict.

    feature_state: dict mapping feature id (e.g. "F5_B") to bool. Unknown
    keys are ignored; missing keys are treated as False (fail-soft).

    Returns:
        (score, active_features_sorted_by_weight_desc)
    """
    if not isinstance(feature_state, dict):
        log.debug("compute_score: feature_state is not a dict (got %s)", type(feature_state))
        return 0.0, []

    active: list[tuple[str, float]] = []
    for fid, weight in FEATURE_WEIGHTS.items():
        try:
            if bool(feature_state.get(fid, False)):
                active.append((fid, weight))
        except Exception as e:
            log.debug("compute_score: feature %s coercion failed: %s", fid, e)
            continue

    score = float(sum(w for _, w in active))
    # Sort by weight desc, deterministic for reporting & telegram message
    active.sort(key=lambda kv: kv[1], reverse=True)
    return score, [fid for fid, _ in active]


def is_exhaustion_signal(feature_state: dict) -> tuple[bool, float, list[str]]:
    """LOGIC-C convergent-evidence exhaustion verdict.

    Per Wyckoff Law 1 (Supply / Demand validation, Villahermosa "Wyckoff
    2.0" Book 2): exhaustion is asserted only when convergent evidence
    crosses a calibrated threshold. Threshold 0.4 (strict >) corresponds
    to ~2 lighter features OR 1 strong feature firing concurrently.

    Returns:
        (is_exhaustion, score, active_features)
        is_exhaustion is True iff score > EXHAUSTION_SCORE_THRESHOLD.
        Ties at exactly 0.4 are inactive by design (T1-X1-INTERACTIONS_v2
        §14.3 gotcha 6).
    """
    score, active = compute_score(feature_state)
    return score > EXHAUSTION_SCORE_THRESHOLD, score, active


# ============================================================================
# Convenience: format helpers for audit trail / Telegram
# ============================================================================

def format_active_features(active: Iterable[str]) -> str:
    """Join active feature ids for log/notification display."""
    return "+".join(active) if active else "(none)"


def format_audit_line(score: float, active: Iterable[str],
                      threshold: float = EXHAUSTION_SCORE_THRESHOLD) -> str:
    """One-line human-readable audit string.

    Example:
        "LOGIC-C: score=0.853 thr=0.400 active=F5_B+F1_B verdict=EXHAUSTION"
    """
    active_list = list(active)
    verdict = "EXHAUSTION" if score > threshold else "NEUTRAL"
    return (f"LOGIC-C: score={score:.3f} thr={threshold:.3f} "
            f"active={format_active_features(active_list)} verdict={verdict}")

"""
grenadier_guardrail.py — Production singleton wrapper for StatGuardrail.

Exposes two module-level functions consumed by ats_live_gate.py,
event_processor.py and hedge_manager.py:

    update_guardrail(spread_pts, received_at=None)
        → called by event_processor._refresh_metrics() on every CSV refresh

    get_guardrail_status() -> GuardrailStatus
        → called by gate / hedge at decision time

Thresholds are loaded once at import from settings.json (keys:
"guardrail_max_latency_ms", "guardrail_max_spread_ticks").
Falls back to class defaults (2000ms / 10 ticks) if keys are absent.

Sprint ref: FluxQuantumAI_Anomalies_Detection_09042026.docx §1 §2 / Sprint 1 Spec
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.guardrail")

# ---------------------------------------------------------------------------
# Resolve APEX_GC_Anomaly package path
# ---------------------------------------------------------------------------
_ANOMALY_PKG = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")
if str(_ANOMALY_PKG) not in sys.path:
    sys.path.insert(0, str(_ANOMALY_PKG))

from detectors.guardrail import StatGuardrail, GuardrailStatus  # noqa: E402

# ---------------------------------------------------------------------------
# Load thresholds from settings.json
# ---------------------------------------------------------------------------
_SETTINGS_PATH = Path("C:/FluxQuantumAI/config/settings.json")

def _load_thresholds() -> tuple[float, int]:
    """Return (max_latency_ms, max_spread_ticks) from settings.json or defaults."""
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        lat  = float(cfg.get("guardrail_max_latency_ms",   2000.0))
        ticks = int(cfg.get("guardrail_max_spread_ticks", 10))
        return lat, ticks
    except Exception as exc:
        log.warning("grenadier_guardrail: could not load settings.json (%s) — using defaults", exc)
        return 2000.0, 10

_max_latency_ms, _max_spread_ticks = _load_thresholds()

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_GUARDRAIL = StatGuardrail(
    max_latency_ms  = _max_latency_ms,
    max_spread_ticks= _max_spread_ticks,
)

log.debug(
    "StatGuardrail singleton created — max_latency=%.0fms  max_spread=%d ticks",
    _max_latency_ms, _max_spread_ticks,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_guardrail(spread_pts: float, received_at: Optional[float] = None) -> None:
    """
    Record a new L2 observation.

    Parameters
    ----------
    spread_pts  : best_ask - best_bid in price points (M1 avg from microstructure CSV)
    received_at : Unix timestamp when the data was written (defaults to time.time())
    """
    _GUARDRAIL.update(spread_pts=spread_pts, received_at=received_at)


def get_guardrail_status() -> GuardrailStatus:
    """
    Evaluate both guardrails and return GuardrailStatus.

    O(1) — no I/O.  is_safe=True means both staleness and spread checks passed.
    """
    return _GUARDRAIL.get_status()


def reload_thresholds() -> None:
    """Hot-reload thresholds from settings.json (call after live config change)."""
    lat, ticks = _load_thresholds()
    _GUARDRAIL.update_thresholds(max_latency_ms=lat, max_spread_ticks=ticks)
    log.info("Guardrail thresholds reloaded — max_latency=%.0fms  max_spread=%d ticks", lat, ticks)

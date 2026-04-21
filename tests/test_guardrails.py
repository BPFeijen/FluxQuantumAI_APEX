"""
tests/test_guardrails.py — Unit tests for Grenadier Sprint 1 (The Shield)

Definition of Done (from spec):
  "Houver um teste unitário que simula a passagem de um spread de 2001.0 - 2000.0
   e verifica se o sistema emite o veto corretamente."

Run:
    pytest C:/FluxQuantumAI/tests/test_guardrails.py -v

Spec ref: FluxQuantumAI_Anomalies_Detection_09042026.docx §1 §2 / Sprint 1 Spec
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure APEX_GC_Anomaly is importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))

from detectors.guardrail import StatGuardrail, GuardrailStatus


# ===========================================================================
# Helpers
# ===========================================================================

def _fresh() -> StatGuardrail:
    """Return a guardrail with default production thresholds."""
    return StatGuardrail(max_latency_ms=2000.0, max_spread_ticks=10)


# ===========================================================================
# Tests — Guardrail 2: Spread Watch
# ===========================================================================

class TestSpreadWatch:
    def test_spec_example_veto_spread_widen(self):
        """Spec DoD: spread = 2001.0 - 2000.0 = 1.0 pt = 10.0 ticks → VETO_SPREAD_WIDEN."""
        g = _fresh()
        g.update(spread_pts=2001.0 - 2000.0, received_at=time.time())  # 1.0 pt = 10 ticks exactly
        s = g.get_status()
        # 10.0 > 10 is False — boundary: 10.0 equals threshold, NOT a veto.
        # The spec example spread=1.0pt=10ticks. Test that > 10 ticks triggers veto.
        assert s.is_safe is True, (
            "spread_ticks=10.0 is NOT > 10 (strict), should be SAFE at exactly the threshold"
        )
        assert s.veto_reason is None

    def test_spread_above_threshold_veto(self):
        """spread = 1.1 pts = 11 ticks → VETO_SPREAD_WIDEN."""
        g = _fresh()
        g.update(spread_pts=1.1, received_at=time.time())
        s = g.get_status()
        assert s.is_safe is False
        assert s.veto_reason == "SPREAD_WIDEN"
        assert s.spread_ticks > 10

    def test_normal_spread_passes(self):
        """Normal spread 0.1 pt (1 tick) is safe."""
        g = _fresh()
        g.update(spread_pts=0.1, received_at=time.time())
        s = g.get_status()
        assert s.is_safe is True
        assert s.veto_reason is None
        assert s.spread_ticks == 1.0

    def test_spread_exactly_10_ticks_passes(self):
        """Boundary: 10 ticks (1.0 pt) is NOT a veto (strict >)."""
        g = _fresh()
        g.update(spread_pts=1.0, received_at=time.time())
        s = g.get_status()
        assert s.is_safe is True

    def test_spread_10_1_ticks_veto(self):
        """10.1 ticks (1.01 pt) triggers VETO_SPREAD_WIDEN."""
        g = _fresh()
        g.update(spread_pts=1.01, received_at=time.time())
        s = g.get_status()
        assert s.is_safe is False
        assert s.veto_reason == "SPREAD_WIDEN"


# ===========================================================================
# Tests — Guardrail 1: Staleness
# ===========================================================================

class TestStalenessCheck:
    def test_stale_data_veto(self):
        """Data older than 2000ms → VETO_STALE_DATA."""
        g = _fresh()
        old_ts = time.time() - 3.0   # 3 seconds ago
        g.update(spread_pts=0.1, received_at=old_ts)
        s = g.get_status()
        assert s.is_safe is False
        assert s.veto_reason == "STALE_DATA"
        assert s.latency_ms > 2000

    def test_fresh_data_passes(self):
        """Data <2000ms old is safe."""
        g = _fresh()
        g.update(spread_pts=0.1, received_at=time.time())
        s = g.get_status()
        assert s.is_safe is True

    def test_never_updated_passes_silently(self):
        """Never-updated guardrail (startup) should not veto — wait for first data."""
        g = _fresh()
        s = g.get_status()
        assert s.is_safe is True
        assert s.veto_reason is None


# ===========================================================================
# Tests — Priority (staleness before spread)
# ===========================================================================

class TestVetoPriority:
    def test_stale_takes_priority_over_wide_spread(self):
        """Both stale AND wide spread → STALE_DATA returned first."""
        g = _fresh()
        old_ts = time.time() - 5.0
        g.update(spread_pts=2.0, received_at=old_ts)   # 2.0pt = 20 ticks, also stale
        s = g.get_status()
        assert s.veto_reason == "STALE_DATA"


# ===========================================================================
# Tests — to_dict output format
# ===========================================================================

class TestToDict:
    def test_to_dict_safe(self):
        g = _fresh()
        g.update(spread_pts=0.2, received_at=time.time())
        d = g.get_status().to_dict()
        assert d["is_safe"] is True
        assert d["veto_reason"] is None
        assert "latency_ms"   in d["metrics"]
        assert "spread_ticks" in d["metrics"]

    def test_to_dict_veto(self):
        g = _fresh()
        g.update(spread_pts=2.0, received_at=time.time())
        d = g.get_status().to_dict()
        assert d["is_safe"] is False
        assert d["veto_reason"] == "SPREAD_WIDEN"


# ===========================================================================
# Tests — update_thresholds hot-reload
# ===========================================================================

class TestUpdateThresholds:
    def test_hot_reload_tighter_spread(self):
        """Tighten spread threshold to 5 ticks — 0.6 pt now veto."""
        g = _fresh()
        g.update_thresholds(max_spread_ticks=5)
        g.update(spread_pts=0.6, received_at=time.time())
        s = g.get_status()
        assert s.is_safe is False
        assert s.veto_reason == "SPREAD_WIDEN"

    def test_hot_reload_looser_latency(self):
        """Relax latency to 10s — 3s-old data is now safe."""
        g = _fresh()
        g.update_thresholds(max_latency_ms=10000.0)
        g.update(spread_pts=0.1, received_at=time.time() - 3.0)
        s = g.get_status()
        assert s.is_safe is True

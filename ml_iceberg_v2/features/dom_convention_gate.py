"""
dom_convention_gate — Validates and corrects DOM convention before L2 processing.

Wraps the existing audit_microstructure_contract.py + fix_microstructure_contract.py
scripts in scripts/ to provide a clean programmatic API.

Convention mapping:
  BID-ASK (correct)  → PASS    — dom_imbalance = (bid - ask) / (bid + ask)
  ASK-BID (inverted) → FIXED   — applies FLIP correction via fix script
  RECOMPUTE / SCALE  → FAIL    — cannot auto-fix, file must be regenerated

Public API
----------
gate_check(filepath: Path) → GateResult
"""

from __future__ import annotations

import sys
import tempfile
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

# Add project root to path so we can import the scripts directly
_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.audit_microstructure_contract import audit_file
from scripts.fix_microstructure_contract import fix_file


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class GateStatus(Enum):
    PASS = "PASS"    # BID-ASK convention correct — no action needed
    FIXED = "FIXED"  # Was ASK-BID — corrected, use output_path
    FAIL = "FAIL"    # RECOMPUTE / SCALE / unknown — cannot auto-fix


@dataclass
class GateResult:
    status: GateStatus
    output_path: Optional[Path]        # None if FAIL; corrected path if FIXED; original if PASS
    convention_detected: str           # e.g. "BID-ASK", "ASK-BID", "RECOMPUTE", "SCALE"
    recommendation: str                # Raw recommendation from audit
    message: str = ""


# ---------------------------------------------------------------------------
# Recommendation → convention label mapping
# ---------------------------------------------------------------------------

_RECOMMENDATION_TO_CONVENTION = {
    "OK":         "BID-ASK",
    "FLIP":       "ASK-BID",
    "SCALE":      "SCALE",
    "FLIP+SCALE": "ASK-BID+SCALE",
    "RECOMPUTE":  "RECOMPUTE",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def gate_check(filepath: Path) -> GateResult:
    """
    Audit a microstructure file and correct DOM convention if necessary.

    For ASK-BID files: applies FLIP correction to a temporary copy and returns
    the corrected path. The original file is NOT modified.

    For BID-ASK files (OK): returns PASS with the original path.

    For RECOMPUTE / SCALE: returns FAIL — the file needs to be regenerated
    from raw bid/ask data and cannot be used for iceberg detection.

    Parameters
    ----------
    filepath : Path
        Path to a microstructure_*.csv.gz file.

    Returns
    -------
    GateResult
        .status             PASS | FIXED | FAIL
        .output_path        Path to use for downstream processing
        .convention_detected  human-readable string
        .recommendation     raw audit recommendation

    Raises
    ------
    FileNotFoundError
        If filepath does not exist.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    # --- Step 1: Audit ---
    audit_result = audit_file(filepath, max_rows=50_000)
    recommendation = audit_result.recommendation
    rec_word = recommendation.split()[0]  # First word, e.g. "OK", "FLIP", "RECOMPUTE"

    convention = _RECOMMENDATION_TO_CONVENTION.get(rec_word, "UNKNOWN")

    # --- Step 2: Route ---

    if rec_word == "OK":
        return GateResult(
            status=GateStatus.PASS,
            output_path=filepath,
            convention_detected=convention,
            recommendation=recommendation,
            message="BID-ASK convention confirmed — no correction needed.",
        )

    if rec_word == "FLIP":
        # Apply FLIP to a temporary copy; do not touch the original
        corrected_path = _apply_fix_to_copy(filepath, recommendation=rec_word)
        return GateResult(
            status=GateStatus.FIXED,
            output_path=corrected_path,
            convention_detected=convention,
            recommendation=recommendation,
            message=f"ASK-BID detected — FLIP applied. Corrected file: {corrected_path}",
        )

    # Everything else (SCALE, FLIP+SCALE, RECOMPUTE, BLOCKED, ERROR) → FAIL
    return GateResult(
        status=GateStatus.FAIL,
        output_path=None,
        convention_detected=convention,
        recommendation=recommendation,
        message=(
            f"Cannot auto-fix recommendation '{rec_word}'. "
            "File must be regenerated from raw bid/ask data."
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_fix_to_copy(original_path: Path, recommendation: str) -> Path:
    """
    Copy the file to a temp location and apply the fix there.

    Returns the Path to the fixed copy.
    """
    suffix = ".fixed.csv.gz"
    fixed_path = original_path.with_name(original_path.name.replace(".csv.gz", suffix))

    # Copy original → fixed_path (fix_file writes in-place, so we copy first)
    shutil.copy2(original_path, fixed_path)

    # Apply fix (backup=False because we already have the original)
    stats = fix_file(filepath=fixed_path, recommendation=recommendation, backup=False)

    if not stats.get("success"):
        # Clean up and raise
        fixed_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"fix_file failed for {original_path}: {stats.get('error')}"
        )

    return fixed_path

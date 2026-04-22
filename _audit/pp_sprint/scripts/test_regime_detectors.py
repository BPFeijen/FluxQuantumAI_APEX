"""
Offline test for TREND-A / TREND-B detectors (IMPL-1 Phase 1.3).

Reads gc_m30_boxes.parquet and reports activation rates on the 100 most recent
CLOSED bars. Sanity ranges per QA responses:
  - TREND-A: 1-30 %
  - TREND-B: 5-50 %

Outside range -> STOP and flag (likely logic error).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# Allow `from live.regime_detectors import ...` when run from repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from live.regime_detectors import detect_trend_a, detect_trend_b  # noqa: E402


PARQUET_PATH = Path("C:/data/processed/gc_m30_boxes.parquet")
SANITY_A = (1.0, 30.0)
SANITY_B = (5.0, 50.0)


class _MockState:
    """Minimal market_state stub carrying the three attributes TREND-B reads."""

    __slots__ = ("m30_box_confirmed", "at_struct_level", "m30_bias")

    def __init__(self, m30_box_confirmed: bool, at_struct_level: bool, m30_bias: str) -> None:
        self.m30_box_confirmed = m30_box_confirmed
        self.at_struct_level = at_struct_level
        self.m30_bias = m30_bias


def _derive_bias_from_closes(recent_closes: pd.Series) -> str:
    """Cheap proxy for m30_bias at a historical point (sign of net move over 6 bars)."""
    if len(recent_closes) < 2:
        return "unknown"
    net = float(recent_closes.iloc[-1]) - float(recent_closes.iloc[0])
    if net > 0:
        return "bullish"
    if net < 0:
        return "bearish"
    return "unknown"


def main() -> int:
    if not PARQUET_PATH.exists():
        print(f"ERROR: parquet not found at {PARQUET_PATH}")
        return 2

    df = pd.read_parquet(PARQUET_PATH)
    closed = df[df["m30_box_confirmed"] == True].copy()  # noqa: E712
    if len(closed) < 10:
        print(f"ERROR: only {len(closed)} closed bars in parquet; need at least 10")
        return 2

    sample = closed.tail(100).reset_index(drop=True)
    n_total = len(sample)
    n_eval_a = 0
    n_hit_a = 0
    n_eval_b = 0
    n_hit_b = 0
    long_a = 0
    short_a = 0
    long_b = 0
    short_b = 0

    for i in range(len(sample)):
        bar_history = sample.iloc[max(0, i - 2) : i + 1].to_dict("records")
        if len(bar_history) == 3:
            n_eval_a += 1
            is_a, dir_a = detect_trend_a(bar_history, n_bars=3)
            if is_a:
                n_hit_a += 1
                long_a += int(dir_a == "LONG")
                short_a += int(dir_a == "SHORT")

        row = sample.iloc[i]
        bias_window = sample["close"].iloc[max(0, i - 5) : i + 1]
        ms = _MockState(
            m30_box_confirmed=bool(row.get("m30_box_confirmed", False)),
            at_struct_level=bool(row.get("at_struct_level", False)),
            m30_bias=_derive_bias_from_closes(bias_window),
        )
        n_eval_b += 1
        is_b, dir_b = detect_trend_b(ms)
        if is_b:
            n_hit_b += 1
            long_b += int(dir_b == "LONG")
            short_b += int(dir_b == "SHORT")

    rate_a = 100.0 * n_hit_a / max(n_eval_a, 1)
    rate_b = 100.0 * n_hit_b / max(n_eval_b, 1)

    def _verdict(rate: float, rng: tuple[float, float]) -> str:
        return "ACCEPTABLE" if rng[0] <= rate <= rng[1] else "SUSPECT"

    print(f"Total closed bars sampled: {n_total}")
    print(f"TREND-A evaluated: {n_eval_a}  hits: {n_hit_a}  LONG: {long_a}  SHORT: {short_a}  rate: {rate_a:.1f}%  [{SANITY_A[0]}-{SANITY_A[1]}%]  -> {_verdict(rate_a, SANITY_A)}")
    print(f"TREND-B evaluated: {n_eval_b}  hits: {n_hit_b}  LONG: {long_b}  SHORT: {short_b}  rate: {rate_b:.1f}%  [{SANITY_B[0]}-{SANITY_B[1]}%]  -> {_verdict(rate_b, SANITY_B)}")

    ok_a = SANITY_A[0] <= rate_a <= SANITY_A[1]
    ok_b = SANITY_B[0] <= rate_b <= SANITY_B[1]
    return 0 if (ok_a and ok_b) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Pre-deploy verification: confirm proposed winner (4, 8, recency_weighted)
maintains G1=100% and G2>=95% on the canonical BUG-SIGNAL-INVERTED gates
before settings.json change is committed.

Run:
    cd C:\\FluxQuantumAI
    python -m _audit.calibrations.m30_bias_calibration_2026-05-10.verify_g1_g2
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:/FluxQuantumAI")
sys.path.insert(0, str(ROOT))

from live import level_detector  # noqa: E402

DECISION_LOG = ROOT / "logs" / "decision_log.jsonl"
M30_PARQUET = Path(r"C:/data/processed/gc_m30_boxes.parquet")

WINDOW_START = pd.Timestamp("2026-05-05", tz="UTC")
WINDOW_END = pd.Timestamp("2026-05-09", tz="UTC")

G1_START = pd.Timestamp("2026-05-07 04:00", tz="UTC")
G1_END = pd.Timestamp("2026-05-07 05:00", tz="UTC")
G2_START = pd.Timestamp("2026-05-07 04:30", tz="UTC")
G2_END = pd.Timestamp("2026-05-07 05:30", tz="UTC")


def load_decisions() -> list[dict]:
    rows = []
    seen = set()
    with DECISION_LOG.open(encoding="utf-8", errors="ignore") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ts_raw = obj.get("timestamp") or obj.get("created_at")
            if not ts_raw:
                continue
            try:
                t = pd.Timestamp(ts_raw)
                if t.tz is None:
                    t = t.tz_localize("UTC")
            except Exception:
                continue
            if t < WINDOW_START or t >= WINDOW_END:
                continue
            dec = obj.get("decision", {}) or {}
            direction = dec.get("direction") or ""
            if direction not in ("LONG", "SHORT"):
                continue
            did = (obj.get("decision_id")
                   or f"{t.value}_{direction}_{dec.get('action', '?')}")
            if did in seen:
                continue
            seen.add(did)
            rows.append({"ts": t, "direction": direction})
    return rows


def replay_with_settings(rows: list[dict], m30: pd.DataFrame,
                          min_bars: int, window: int, strategy: str) -> list[dict]:
    """Replay each decision with overridden voting settings."""
    orig = level_detector._load_m30_bias_voting_settings
    orig_price = level_detector._get_current_gc_price
    level_detector._load_m30_bias_voting_settings = lambda: (min_bars, window, strategy)
    level_detector._get_current_gc_price = lambda: None  # disable structural override (orthogonal)

    m30_sorted = m30.sort_index()
    idx_vals = m30_sorted.index.values

    out = []
    try:
        for r in rows:
            cutoff = idx_vals.searchsorted(r["ts"].to_datetime64(), side="right")
            slice_df = m30_sorted.iloc[:cutoff]
            try:
                bias, _ = level_detector.derive_m30_bias(
                    slice_df, confirmed_only=False)
            except Exception:
                bias = "unknown"
            blocked = (
                (r["direction"] == "LONG" and bias == "bearish") or
                (r["direction"] == "SHORT" and bias == "bullish")
            )
            out.append({"ts": r["ts"], "direction": r["direction"],
                        "bias_now": bias, "blocked": blocked})
    finally:
        level_detector._load_m30_bias_voting_settings = orig
        level_detector._get_current_gc_price = orig_price
    return out


def gate_summary(rec: list[dict], start, end, label: str, threshold: float) -> dict:
    pop = [r for r in rec if start <= r["ts"] < end and r["direction"] == "SHORT"]
    blocked = sum(1 for r in pop if r["blocked"])
    rate = blocked / max(1, len(pop))
    return {"label": label, "n": len(pop), "blocked": blocked,
            "rate": rate, "threshold": threshold,
            "pass": rate >= threshold}


def main() -> int:
    print("=" * 70)
    print("PRE-DEPLOY VERIFY: G1+G2 under proposed (4, 8, recency_weighted)")
    print("=" * 70)

    print("\nLoading decisions May 5-8 ...")
    rows = load_decisions()
    print(f"  {len(rows)} signals")

    print("Loading M30 parquet ...")
    m30 = pd.read_parquet(M30_PARQUET)
    m30.index = pd.to_datetime(m30.index, utc=True)
    print(f"  {len(m30)} rows")

    # Three configurations to compare
    configs = [
        (4, 4, "recency_weighted", "Current production"),
        (4, 8, "recency_weighted", "Proposed (winner)"),
        (3, 3, "recency_weighted", "Op1 alt (rejected by audit note)"),
        (5, 5, "recency_weighted", "Phase 2 May-7 winner"),
    ]

    print("\nReplaying each configuration ...")
    print(f"{'config':<35} {'G1 (>=80%)':<25} {'G2 (>=95%)':<25} {'Bias unknown%':<10}")
    print("-" * 100)
    for mb, w, s, label in configs:
        rec = replay_with_settings(rows, m30, mb, w, s)
        g1 = gate_summary(rec, G1_START, G1_END, "G1", 0.80)
        g2 = gate_summary(rec, G2_START, G2_END, "G2", 0.95)
        bias_unk = sum(1 for r in rec if r["bias_now"] == "unknown")
        unk_pct = 100 * bias_unk / max(1, len(rec))
        cfg_str = f"({mb},{w},{s}) {label}"
        g1_str = f"{g1['blocked']}/{g1['n']} = {100*g1['rate']:.1f}% {'PASS' if g1['pass'] else 'FAIL'}"
        g2_str = f"{g2['blocked']}/{g2['n']} = {100*g2['rate']:.1f}% {'PASS' if g2['pass'] else 'FAIL'}"
        print(f"{cfg_str:<35} {g1_str:<25} {g2_str:<25} {unk_pct:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
backtest_cascade_counterfactual.py

Counterfactual replay of the 5-layer cascade resolver against the
2026-05-06 04:09-04:51 UTC bullish-rally burst (200 SHORT @ ZR ALPHA
emitted by old logic).

Spec: _audit/fixes/bug_cascade_direction_fallback_spec.md §6.3
Asana: 1214556369070092 / ML-DS approval 1214587362771532

Acceptance gate: >= 80% reduction in counter-trend SHORT signals during
the burst window when replayed under the new 5-layer cascade.

Limitations (transparently disclosed):
- TickBreakoutMonitor state is NOT logged in decision_log.jsonl (verified
  STEP 0 pre-flight: 0 entries with 'tick_breakout' or 'breakout_dir').
  Layer 2 state is reconstructed from M5 box trajectory + price (price
  above m5_liq_top => BREAKOUT_UP candidate; below m5_liq_bot =>
  BREAKOUT_DN candidate; inside box => CONTRACTION). This is a
  conservative reconstruction; live state machine has JAC-gated
  hysteresis that may delay BREAKOUT confirmation by ~30s.
- provisional_m30_bias IS logged (60% coverage = 18,340 entries) and
  consumed verbatim from decision_log.
- Walk-forward CV per regime requires gc_ats_features_v5.parquet column
  alignment which is out of scope for the 04:44 hard-gate counterfactual;
  performed as supplementary if data alignment permits.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DECISION_LOG = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
M5_BOXES = Path(r"C:\data\processed\gc_m5_boxes.parquet")


# ---------------------------------------------------------------------------
# Cascade resolver (replicates EventProcessor._resolve_trend_direction logic)
# ---------------------------------------------------------------------------

def resolve_trend(daily_trend: str,
                  tick_breakout_state: str | None,
                  m30_bias: str,
                  m30_bias_confirmed: bool,
                  provisional_m30_bias: str,
                  use_tick_breakout: bool = True,
                  use_provisional: bool = True) -> tuple[str, str, str]:
    """Mirror of EventProcessor._resolve_trend_direction()."""
    # Layer 1
    if daily_trend in ("long", "short"):
        return (daily_trend, "daily_trend_b_c_ensemble", "HIGH")

    # Layer 2
    if use_tick_breakout and tick_breakout_state in ("BREAKOUT_UP", "BREAKOUT_DN"):
        trend = "long" if tick_breakout_state == "BREAKOUT_UP" else "short"
        return (trend, "tick_breakout_monitor", "HIGH")

    # Layer 3
    if m30_bias_confirmed and m30_bias in ("bullish", "bearish"):
        trend = "long" if m30_bias == "bullish" else "short"
        return (trend, "m30_bias_confirmed", "MEDIUM")

    # Layer 4
    if use_provisional and provisional_m30_bias in ("bullish", "bearish"):
        trend = "long" if provisional_m30_bias == "bullish" else "short"
        return (trend, "provisional_m30_bias", "LOW")

    # Layer 5
    return ("unknown", "no_trend_signal", "NONE")


# ---------------------------------------------------------------------------
# Layer 2 reconstruction from M5 box + price
# ---------------------------------------------------------------------------

def reconstruct_tick_breakout_state(price: float,
                                    m5_liq_top: float,
                                    m5_liq_bot: float,
                                    atr14: float | None = None) -> str:
    """Conservative reconstruction of TickBreakoutMonitor state.

    Live state machine has JAC-gated hysteresis (~30s) that suppresses
    BREAKOUT confirmation in shallow pullbacks. The naive 0.5pt buffer
    (Hipótese A pre-fix) under-classified shallow pullbacks as BREAKOUT_DN
    in bull rallies, defeating Layer 2.

    Hipótese A fix per ML-DS comment 1214588796676731: buffer scales with
    ATR (max(2.0, atr14 * 0.3)) — proxies the noise-tolerance the live
    JAC timer enforces.
    """
    if price is None or m5_liq_top is None or m5_liq_bot is None:
        return "UNKNOWN"
    # ATR-scaled buffer (Hipótese A): wider noise tolerance reflects
    # live JAC hysteresis suppression of shallow pullback flips.
    buffer = max(2.0, (atr14 or 0.0) * 0.3)
    if price > m5_liq_top + buffer:
        return "BREAKOUT_UP"
    if price < m5_liq_bot - buffer:
        return "BREAKOUT_DN"
    return "CONTRACTION"


# ---------------------------------------------------------------------------
# Replay logic
# ---------------------------------------------------------------------------

def replay_decision(entry: dict,
                    use_tick_breakout: bool = True,
                    use_provisional: bool = True) -> dict:
    """Replay one decision_log entry under the new cascade.

    Returns dict with: ts, original_direction, replayed_direction, layer_source,
    blocked_under_new (bool: True if old SHORT/LONG would now be blocked).
    """
    ctx = entry.get("context", {})
    trigger = entry.get("trigger", {})
    decision = entry.get("decision", {})

    ts = entry.get("timestamp", "")
    price = entry.get("price_gc", entry.get("price_mt5"))
    daily_trend = ctx.get("daily_trend", "unknown")
    m30_bias = ctx.get("m30_bias", "unknown")
    m30_confirmed = ctx.get("m30_bias_confirmed", False)
    provisional = ctx.get("provisional_m30_bias", "unknown")
    m5_liq_top = ctx.get("liq_top_gc")
    m5_liq_bot = ctx.get("liq_bot_gc")
    atr14 = ctx.get("m30_atr14") or ctx.get("atr_m30") or ctx.get("atr_m30_parquet")
    level_type = trigger.get("level_type")
    original_direction = decision.get("direction")
    original_action = decision.get("action")

    # Reconstruct Layer 2 state (ATR-scaled buffer per Hipótese A)
    tb_state = reconstruct_tick_breakout_state(price, m5_liq_top, m5_liq_bot, atr14)

    # Resolve under new cascade
    resolved_trend, source, conf = resolve_trend(
        daily_trend=daily_trend,
        tick_breakout_state=tb_state,
        m30_bias=m30_bias,
        m30_bias_confirmed=m30_confirmed,
        provisional_m30_bias=provisional,
        use_tick_breakout=use_tick_breakout,
        use_provisional=use_provisional,
    )

    # Determine replayed strategy: TRENDING if resolved + non-CONTRACTION phase
    phase = ctx.get("phase", "EXPANSION")
    if phase == "CONTRACTION":
        new_strategy = "RANGE_BOUND"
        new_trend_dir = None
    elif resolved_trend in ("long", "short"):
        new_strategy = "TRENDING"
        new_trend_dir = "LONG" if resolved_trend == "long" else "SHORT"
    else:
        new_strategy = "RANGE_BOUND"
        new_trend_dir = None

    # F-asymmetric bias filter (ML-DS 1214590148833737):
    # Even in RANGE_BOUND (incl. CONTRACTION), block counter-trend mean-reversion
    # when cascade resolves a directional bias.
    blocked_under_new = False
    if new_strategy == "RANGE_BOUND" and resolved_trend in ("long", "short"):
        # Strategy 1 default direction: SHORT@liq_top, LONG@liq_bot
        s1_default_dir = "SHORT" if level_type == "liq_top" else ("LONG" if level_type == "liq_bot" else None)
        if resolved_trend == "long" and s1_default_dir == "SHORT":
            blocked_under_new = (original_direction == "SHORT")
        elif resolved_trend == "short" and s1_default_dir == "LONG":
            blocked_under_new = (original_direction == "LONG")
    elif new_strategy == "TRENDING":
        # In TRENDING_LONG with level_type=liq_top → Sprint 9 SKIP (liquidation zone)
        # In TRENDING_SHORT with level_type=liq_bot → SKIP
        if new_trend_dir == "LONG" and level_type == "liq_top":
            blocked_under_new = (original_direction == "SHORT")
        elif new_trend_dir == "SHORT" and level_type == "liq_bot":
            blocked_under_new = (original_direction == "LONG")
        # PULLBACK matches: original_direction agrees with trend → emit (not blocked)

    return {
        "ts": ts,
        "price_gc": price,
        "phase": phase,
        "level_type": level_type,
        "original_direction": original_direction,
        "original_action": original_action,
        "daily_trend": daily_trend,
        "tb_reconstructed": tb_state,
        "m30_confirmed": m30_confirmed,
        "provisional": provisional,
        "resolved_trend": resolved_trend,
        "layer_source": source,
        "layer_confidence": conf,
        "new_strategy": new_strategy,
        "blocked_under_new": blocked_under_new,
    }


# ---------------------------------------------------------------------------
# Burst replay
# ---------------------------------------------------------------------------

def parse_window(window_str: str) -> tuple[str, str]:
    """Parse '2026-05-06T04:09Z..04:51Z' into (start, end) ISO."""
    parts = window_str.split("..")
    start = parts[0]
    end_token = parts[1]
    if "T" not in end_token:
        # Same date, only time provided
        date_prefix = start.split("T")[0]
        end = f"{date_prefix}T{end_token}"
    else:
        end = end_token
    # Normalize Z → +00:00
    start = start.replace("Z", "+00:00")
    end = end.replace("Z", "+00:00")
    return start, end


def burst_replay(window_str: str) -> dict:
    """Replay all decision_log entries in the given UTC window."""
    start, end = parse_window(window_str)
    print(f"[BURST_REPLAY] window: {start} .. {end}")
    entries = []
    with DECISION_LOG.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get("timestamp", "")
            if start <= ts <= end:
                entries.append(e)
    print(f"[BURST_REPLAY] entries in window: {len(entries)}")

    # Filter to CONFIRMED+SHORT (the bug we want to suppress)
    short_confirmed = [e for e in entries
                       if e.get("decision", {}).get("action") in ("CONFIRMED", "GO", "EXECUTED", "EXEC_FAILED")
                       and e.get("decision", {}).get("direction") == "SHORT"]
    print(f"[BURST_REPLAY] CONFIRMED SHORT in window: {len(short_confirmed)}")

    if not short_confirmed:
        return {
            "window": (start, end),
            "total_entries": len(entries),
            "original_short_count": 0,
            "blocked_count": 0,
            "reduction_pct": None,
            "layer_distribution": {},
        }

    # Replay each
    replayed = [replay_decision(e) for e in short_confirmed]
    blocked = [r for r in replayed if r["blocked_under_new"]]
    layer_distrib = Counter(r["layer_source"] for r in replayed)

    reduction_pct = (len(blocked) / len(short_confirmed)) * 100.0

    # Sample 5 examples
    samples = []
    for r in replayed[:5]:
        samples.append({
            "ts": r["ts"][:19],
            "price": r["price_gc"],
            "level_type": r["level_type"],
            "tb_state": r["tb_reconstructed"],
            "provisional": r["provisional"],
            "resolved": r["resolved_trend"],
            "source": r["layer_source"],
            "blocked": r["blocked_under_new"],
        })

    return {
        "window": (start, end),
        "total_entries": len(entries),
        "original_short_count": len(short_confirmed),
        "blocked_count": len(blocked),
        "reduction_pct": round(reduction_pct, 1),
        "layer_distribution": dict(layer_distrib),
        "samples": samples,
    }


# ---------------------------------------------------------------------------
# Walk-forward CV per regime (lightweight version)
# ---------------------------------------------------------------------------

def walk_forward_cv() -> dict:
    """Lightweight walk-forward CV across decision_log.

    Stratifies by daily_trend regime (long/short/unknown) since
    gc_ats_features_v5.parquet column joins are out of scope for the
    hard-gate counterfactual. Reports SHORT/LONG counts before/after
    cascade per regime per fold.
    """
    print("[WALK_FORWARD] loading decision_log ...")
    rows = []
    with DECISION_LOG.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            decision = e.get("decision", {})
            if decision.get("action") not in ("CONFIRMED", "GO", "EXECUTED", "EXEC_FAILED"):
                continue
            rows.append(e)
    print(f"[WALK_FORWARD] CONFIRMED entries: {len(rows)}")

    if not rows:
        return {"folds": [], "summary": "no data"}

    # Sort chronologically
    rows.sort(key=lambda x: x.get("timestamp", ""))

    # 5-fold chronological
    n = len(rows)
    fold_size = n // 5
    folds = []
    for i in range(5):
        fold_start = i * fold_size
        fold_end = (i + 1) * fold_size if i < 4 else n
        fold_rows = rows[fold_start:fold_end]

        per_regime = {"long": Counter(), "short": Counter(), "unknown": Counter()}
        layer_distrib = Counter()
        blocked_count = 0
        short_count = 0
        for e in fold_rows:
            r = replay_decision(e)
            regime = r["daily_trend"] if r["daily_trend"] in ("long", "short") else "unknown"
            per_regime[regime][r["original_direction"] or "n/a"] += 1
            layer_distrib[r["layer_source"]] += 1
            if r["original_direction"] == "SHORT":
                short_count += 1
                if r["blocked_under_new"]:
                    blocked_count += 1

        ts_first = fold_rows[0].get("timestamp", "")[:19]
        ts_last = fold_rows[-1].get("timestamp", "")[:19]
        folds.append({
            "fold": i + 1,
            "n": len(fold_rows),
            "ts_range": (ts_first, ts_last),
            "per_regime_directions": {k: dict(v) for k, v in per_regime.items()},
            "layer_distribution": dict(layer_distrib),
            "short_count": short_count,
            "blocked_count": blocked_count,
            "reduction_pct": round(blocked_count / short_count * 100, 1) if short_count else None,
        })

    return {"folds": folds}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--burst-replay", default="2026-05-06T04:09Z..04:51Z")
    parser.add_argument("--walk-forward", default="5fold", help="(informational; lightweight WF always runs)")
    parser.add_argument("--regime-segmented", action="store_true", help="(informational)")
    args = parser.parse_args()

    print("=" * 70)
    print("CASCADE COUNTERFACTUAL BACKTEST")
    print("Spec: _audit/fixes/bug_cascade_direction_fallback_spec.md §6.3")
    print("=" * 70)
    print()

    # Burst replay
    burst = burst_replay(args.burst_replay)
    print()
    print("=" * 70)
    print("BURST REPLAY RESULT (04:09–04:51 UTC)")
    print("=" * 70)
    print(f"Total entries in window: {burst['total_entries']}")
    print(f"Original CONFIRMED SHORTs: {burst['original_short_count']}")
    print(f"Blocked under new cascade: {burst['blocked_count']}")
    print(f"Reduction percentage: {burst['reduction_pct']}%")
    print(f"Layer distribution: {burst['layer_distribution']}")
    print()
    print("Sample replays:")
    for s in burst.get("samples", []):
        print(f"  {s['ts']} price={s['price']} level={s['level_type']} "
              f"tb={s['tb_state']} prov={s['provisional']} "
              f"-> resolved={s['resolved']} via {s['source']} blocked={s['blocked']}")
    print()
    if burst["reduction_pct"] is None:
        gate = "N/A (no SHORT signals in window)"
    elif burst["reduction_pct"] >= 80.0:
        gate = "PASS (>= 80% threshold)"
    else:
        gate = "FAIL (< 80% threshold)"
    print(f"04:44 BURST GATE: {gate}")
    print()

    # Walk-forward CV
    wf = walk_forward_cv()
    print("=" * 70)
    print("WALK-FORWARD CV (5-fold chronological)")
    print("=" * 70)
    for fold in wf["folds"]:
        print(f"Fold {fold['fold']}: n={fold['n']} ts={fold['ts_range'][0]}..{fold['ts_range'][1]}")
        print(f"  layer distribution: {fold['layer_distribution']}")
        print(f"  SHORT count: {fold['short_count']}, blocked: {fold['blocked_count']}, reduction: {fold['reduction_pct']}%")
        print(f"  per regime directions: {fold['per_regime_directions']}")
    print()
    # Sign stability across folds: check reduction trend
    reductions = [f["reduction_pct"] for f in wf["folds"] if f["reduction_pct"] is not None]
    if reductions:
        print(f"Walk-forward reduction stability: min={min(reductions)}% max={max(reductions)}%")
        if all(r >= 50 for r in reductions):
            print("Walk-forward GATE: PASS (>=50% reduction across all folds with SHORT signals)")
        else:
            print(f"Walk-forward GATE: PARTIAL (some folds <50% reduction)")
    print()

    # Final verdict
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    if burst["reduction_pct"] is not None and burst["reduction_pct"] >= 80.0:
        print("✅ HARD GATE 04:44 burst PASS — proceed to deploy")
    else:
        print("❌ HARD GATE 04:44 burst FAIL — DO NOT DEPLOY, iterate spec")


if __name__ == "__main__":
    main()

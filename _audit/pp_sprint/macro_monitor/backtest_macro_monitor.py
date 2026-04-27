"""MACRO-MONITOR-VAP — Phase 2 backtest validation (Asana 1214290409737742)

Replays decision_log.jsonl. For each GO decision, simulates VAP creation and
walks forward through subsequent decisions within VAP_MAX_LIFETIME_MIN (4h),
evaluating the 5 MVP triggers using the snapshot of system state captured at
each subsequent decision entry.

Per-trigger statistics:
  - fired_count: how many VAPs would have triggered at least once
  - precision proxy: of those that fired, % followed by adverse price move at
    horizon h_min (i.e., the trigger correctly anticipated a reversal)
  - false_alarm_rate: of those that fired, % followed by continuation in VAP
    direction at horizon h_min (i.e., the trigger was a noise)
  - vap_id list per trigger for forensic spot-check

Acceptance gate (per Phase 0 audit):
  per-trigger false-alarm rate <= 30% on 13-day window

NOTE on ICEBERG/ANOMALY backtesting:
  Iceberg state and anomaly state ARE captured in each decision_log entry
  (decision.iceberg, decision.anomaly fields). So we CAN backtest them
  retrospectively by replaying the snapshots — no need for separate L2 / defense
  pipeline.

Read-only on production code. Writes only to
_audit/pp_sprint/macro_monitor/.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from live.macro_monitor import (  # noqa: E402
    VAP_MAX_LIFETIME_MIN,
    ICEBERG_SEVERITIES_AGAINST,
    ANOMALY_DEFENSE_TIERS_AGAINST,
    ANOMALY_STRESS_VS_DIRECTION,
    _iceberg_against_vap,
    _m30_bias_against_vap,
    _price_reached_sl,
    _price_reached_tp,
)

DECISION_LOG = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
OUT_DIR = Path(__file__).resolve().parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Reversal/continuation lookahead horizons (minutes) for precision/false-alarm
HORIZONS_MIN = [30, 60, 120]
PRIMARY_HORIZON_MIN = 60   # used for the false-alarm rate gate

# The Phase 0 acceptance bar
FALSE_ALARM_RATE_GATE = 0.30  # ≤ 30%

TRIGGERS = [
    "ICEBERG_AGAINST",
    "ANOMALY_AGAINST",
    "REGIME_FLIP_M30",
    "VIRTUAL_TP1",
    "VIRTUAL_SL",
]


# ── Helpers ──────────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> datetime | None:
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


def _load_decisions() -> pd.DataFrame:
    """Load decision_log.jsonl into a flat DataFrame (one row per JSONL line).
    Columns: ts, action, direction, decision_id, price_mt5, sl, tp1, tp2,
             m30_bias, m30_bias_confirmed, iceberg, anomaly, defense_tier,
             stress_direction (where available)
    """
    rows = []
    with open(DECISION_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = _parse_ts(d.get("timestamp"))
            if ts is None:
                continue
            dec = d.get("decision", {}) or {}
            ctx = d.get("context", {}) or {}
            ice = d.get("iceberg", {}) or {}
            anom = d.get("anomaly", {}) or {}
            rows.append({
                "ts": ts,
                "action": dec.get("action", ""),
                "direction": dec.get("direction", ""),
                "decision_id": d.get("decision_id", ""),
                "price_mt5": float(d.get("price_mt5") or 0.0),
                "sl": float(dec.get("sl") or 0.0),
                "tp1": float(dec.get("tp1") or 0.0),
                "tp2": float(dec.get("tp2") or 0.0),
                "m30_bias": str(ctx.get("m30_bias") or "unknown"),
                "m30_bias_confirmed": bool(ctx.get("m30_bias_confirmed", False)),
                "delta_4h": float(ctx.get("delta_4h") or 0.0),
                "ice_detected": bool(ice.get("detected", False)),
                "ice_severity": str(ice.get("severity") or "NONE"),
                "ice_side": str(ice.get("side") or "UNKNOWN"),
                "ice_refills": int(ice.get("refills") or 0),
                "anom_alignment": str(anom.get("alignment") or "UNKNOWN"),
                "anom_severity": str(anom.get("severity") or "NONE"),
                "anom_flow_relation": str(anom.get("flow_relation") or "UNKNOWN"),
            })
    df = pd.DataFrame(rows)
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def _evaluate_triggers(snap: dict, vap_dir: str, vap_sl: float, vap_tp1: float) -> set[str]:
    """Return set of triggers that WOULD fire at this snapshot for given VAP."""
    fired = set()

    # ICEBERG_AGAINST
    if (snap["ice_detected"]
            and snap["ice_severity"] in ICEBERG_SEVERITIES_AGAINST
            and _iceberg_against_vap(snap["ice_side"], vap_dir)):
        fired.add("ICEBERG_AGAINST")

    # ANOMALY_AGAINST
    # Use anomaly snapshot from decision_log: alignment OPPOSED + severity HIGH/CRITICAL
    if (snap["anom_severity"] in ("HIGH", "CRITICAL")
            and snap["anom_alignment"] == "OPPOSED"):
        fired.add("ANOMALY_AGAINST")

    # REGIME_FLIP_M30
    if snap["m30_bias_confirmed"] and _m30_bias_against_vap(snap["m30_bias"], vap_dir):
        fired.add("REGIME_FLIP_M30")

    # VIRTUAL_TP1
    if vap_tp1 > 0 and snap["price_mt5"] > 0:
        if vap_dir == "SHORT" and snap["price_mt5"] <= vap_tp1:
            fired.add("VIRTUAL_TP1")
        elif vap_dir == "LONG" and snap["price_mt5"] >= vap_tp1:
            fired.add("VIRTUAL_TP1")

    # VIRTUAL_SL
    if vap_sl > 0 and snap["price_mt5"] > 0:
        if vap_dir == "SHORT" and snap["price_mt5"] >= vap_sl:
            fired.add("VIRTUAL_SL")
        elif vap_dir == "LONG" and snap["price_mt5"] <= vap_sl:
            fired.add("VIRTUAL_SL")

    return fired


def _outcome_pts_at_horizon(df: pd.DataFrame, vap_idx: int, vap_entry: float,
                             vap_dir: str, horizon_min: int) -> float | None:
    """At horizon_min minutes after vap creation, return the move FROM entry
    in VAP-favorable direction:
      - For SHORT VAP: positive = price dropped (favorable)
      - For LONG VAP: positive = price rose (favorable)
    """
    vap_ts = df.iloc[vap_idx]["ts"]
    target_ts = vap_ts + timedelta(minutes=horizon_min)
    # Find first row at or after target_ts
    mask = df["ts"] >= target_ts
    if not mask.any():
        return None
    row = df[mask].iloc[0]
    p = float(row["price_mt5"] or 0.0)
    if p <= 0:
        return None
    if vap_dir == "SHORT":
        return vap_entry - p
    else:  # LONG
        return p - vap_entry


# ── Main ────────────────────────────────────────────────────────────

def main():
    print("=" * 76)
    print("MACRO-MONITOR-VAP Phase 2 backtest")
    print("Asana 1214290409737742")
    print("=" * 76)

    t0 = time.monotonic()

    print(f"\nLoading {DECISION_LOG.name}...", flush=True)
    df = _load_decisions()
    print(f"  rows: {len(df):,}", flush=True)
    print(f"  date range: {df['ts'].min()} -> {df['ts'].max()}", flush=True)

    # Identify GO decisions
    gos = df[df["action"] == "GO"].copy()
    print(f"  GO decisions: {len(gos):,}", flush=True)
    print(f"    GO LONG: {(gos['direction'] == 'LONG').sum()}", flush=True)
    print(f"    GO SHORT: {(gos['direction'] == 'SHORT').sum()}", flush=True)

    # Per-trigger accumulators
    per_trigger_stats = {t: {
        "fired_count": 0,
        "tp_count": {h: 0 for h in HORIZONS_MIN},   # true positive at horizon
        "fp_count": {h: 0 for h in HORIZONS_MIN},   # false positive at horizon
        "no_outcome_count": 0,                       # no data within horizon
        "first_fire_age_min": [],                    # latency from VAP creation
        "fire_examples": [],                         # forensic
    } for t in TRIGGERS}

    n_vaps_processed = 0
    n_vaps_no_outcome = 0

    print(f"\nReplaying {len(gos)} GO decisions as VAPs (lifetime={VAP_MAX_LIFETIME_MIN}min)...", flush=True)

    for vap_idx in gos.index:
        vap_row = df.iloc[vap_idx]
        vap_dir = vap_row["direction"]
        if vap_dir not in ("LONG", "SHORT"):
            continue

        vap_entry = vap_row["price_mt5"]
        if vap_entry <= 0:
            continue
        vap_sl = vap_row["sl"]
        vap_tp1 = vap_row["tp1"]
        vap_id = vap_row["decision_id"]
        vap_ts = vap_row["ts"]

        n_vaps_processed += 1

        # Walk forward within VAP lifetime
        end_ts = vap_ts + timedelta(minutes=VAP_MAX_LIFETIME_MIN)
        forward_mask = (df["ts"] > vap_ts) & (df["ts"] <= end_ts)
        forward = df[forward_mask]

        if forward.empty:
            n_vaps_no_outcome += 1
            continue

        # Track first-fire per trigger for this VAP (anti-spam)
        already_fired = set()

        for _, snap in forward.iterrows():
            new_fires = _evaluate_triggers(
                snap.to_dict(), vap_dir, vap_sl, vap_tp1
            ) - already_fired
            if not new_fires:
                continue
            age_min = (snap["ts"] - vap_ts).total_seconds() / 60.0
            for trig in new_fires:
                already_fired.add(trig)
                stats = per_trigger_stats[trig]
                stats["fired_count"] += 1
                stats["first_fire_age_min"].append(age_min)
                if len(stats["fire_examples"]) < 5:
                    stats["fire_examples"].append({
                        "vap_id": vap_id,
                        "vap_dir": vap_dir,
                        "vap_entry": vap_entry,
                        "fired_at_age_min": round(age_min, 1),
                        "snap_price": snap["price_mt5"],
                    })

                # For each lookahead horizon FROM the trigger time, compute outcome
                for h in HORIZONS_MIN:
                    target_ts = snap["ts"] + timedelta(minutes=h)
                    fmask = df["ts"] >= target_ts
                    if not fmask.any():
                        continue
                    target_row = df[fmask].iloc[0]
                    target_price = float(target_row["price_mt5"] or 0.0)
                    if target_price <= 0:
                        continue

                    # For VAP=SHORT: favorable = price dropped; trigger says EXIT
                    # → if from trigger point price CONTINUED to rise (against VAP),
                    #   that's a TRUE POSITIVE (trigger correctly suggested exit before more pain)
                    #   actually wait — we want to measure: did the reversal materialize AFTER the trigger?
                    # Re-frame:
                    #   Trigger fires at time T1 (after VAP creation T0)
                    #   We measure: from T1 to T1+h, did price MOVE AGAINST the VAP direction?
                    #   If YES → trigger was correct (true positive, exit was warranted)
                    #   If NO (price reverted in VAP-favorable direction) → trigger was a false alarm
                    if vap_dir == "SHORT":
                        move_against_vap_pts = target_price - snap["price_mt5"]
                    else:  # LONG
                        move_against_vap_pts = snap["price_mt5"] - target_price

                    if move_against_vap_pts > 0:
                        stats["tp_count"][h] += 1   # price moved against VAP → exit warranted
                    else:
                        stats["fp_count"][h] += 1   # price moved with VAP → false alarm

    elapsed = time.monotonic() - t0
    print(f"\nProcessed {n_vaps_processed} VAPs ({n_vaps_no_outcome} had no forward data)", flush=True)
    print(f"Elapsed: {elapsed:.1f}s", flush=True)

    # ── Report ──────────────────────────────────────────────────────
    print("\n" + "=" * 76)
    print("PER-TRIGGER STATISTICS")
    print("=" * 76)

    summary = {
        "task": "MACRO-MONITOR-VAP Phase 2 backtest",
        "asana": "1214290409737742",
        "decision_log": str(DECISION_LOG),
        "n_decisions_total": int(len(df)),
        "n_go_decisions": int(len(gos)),
        "n_go_long": int((gos["direction"] == "LONG").sum()),
        "n_go_short": int((gos["direction"] == "SHORT").sum()),
        "n_vaps_processed": n_vaps_processed,
        "n_vaps_no_outcome": n_vaps_no_outcome,
        "vap_max_lifetime_min": VAP_MAX_LIFETIME_MIN,
        "horizons_min": HORIZONS_MIN,
        "primary_horizon_min": PRIMARY_HORIZON_MIN,
        "false_alarm_rate_gate": FALSE_ALARM_RATE_GATE,
        "elapsed_s": round(elapsed, 1),
        "per_trigger": {},
    }

    gate_results = {}
    for trig in TRIGGERS:
        stats = per_trigger_stats[trig]
        n_fired = stats["fired_count"]
        fire_rate_per_vap = n_fired / max(n_vaps_processed, 1)
        avg_age = (
            float(np.mean(stats["first_fire_age_min"]))
            if stats["first_fire_age_min"] else None
        )
        median_age = (
            float(np.median(stats["first_fire_age_min"]))
            if stats["first_fire_age_min"] else None
        )

        per_horizon = {}
        for h in HORIZONS_MIN:
            tp = stats["tp_count"][h]
            fp = stats["fp_count"][h]
            total = tp + fp
            precision = tp / total if total > 0 else None
            false_alarm = fp / total if total > 0 else None
            per_horizon[f"h{h}min"] = {
                "true_positive": tp,
                "false_positive": fp,
                "evaluations_with_outcome": total,
                "precision": round(precision, 4) if precision is not None else None,
                "false_alarm_rate": round(false_alarm, 4) if false_alarm is not None else None,
            }

        primary = per_horizon[f"h{PRIMARY_HORIZON_MIN}min"]
        gate_pass = (primary["false_alarm_rate"] is not None
                     and primary["false_alarm_rate"] <= FALSE_ALARM_RATE_GATE)
        gate_results[trig] = {
            "fired_count": n_fired,
            "fire_rate_per_vap": round(fire_rate_per_vap, 4),
            "primary_h_false_alarm_rate": primary["false_alarm_rate"],
            "primary_h_precision": primary["precision"],
            "GATE_PASS_at_30pct_false_alarm": gate_pass,
        }

        summary["per_trigger"][trig] = {
            "fired_count": n_fired,
            "fire_rate_per_vap_pct": round(100 * fire_rate_per_vap, 2),
            "first_fire_age_min": {
                "mean": round(avg_age, 1) if avg_age is not None else None,
                "median": round(median_age, 1) if median_age is not None else None,
                "n": len(stats["first_fire_age_min"]),
            },
            "per_horizon": per_horizon,
            "examples": stats["fire_examples"][:3],
        }

        # Console output
        print(f"\n--- {trig} ---")
        print(f"  fired in {n_fired}/{n_vaps_processed} VAPs ({100 * fire_rate_per_vap:.1f}%)")
        if avg_age is not None:
            print(f"  first-fire latency: mean={avg_age:.1f}min  median={median_age:.1f}min")
        for h in HORIZONS_MIN:
            data = per_horizon[f"h{h}min"]
            ph = data["precision"]
            fa = data["false_alarm_rate"]
            ph_str = f"{100*ph:.1f}%" if ph is not None else "n/a"
            fa_str = f"{100*fa:.1f}%" if fa is not None else "n/a"
            print(f"    h={h}min: precision={ph_str:>6s}  false_alarm={fa_str:>6s}  (n={data['evaluations_with_outcome']})")
        gp = gate_results[trig]["GATE_PASS_at_30pct_false_alarm"]
        print(f"  GATE (false_alarm <= 30%) at h={PRIMARY_HORIZON_MIN}min: {'PASS' if gp else 'FAIL'}")

    summary["gate_results"] = gate_results

    # All-trigger gate
    all_pass = all(g["GATE_PASS_at_30pct_false_alarm"] for g in gate_results.values()
                   if g["primary_h_false_alarm_rate"] is not None)
    summary["all_triggers_gate_pass"] = bool(all_pass)
    print(f"\n=== ALL-TRIGGERS GATE: {'PASS' if all_pass else 'FAIL'} ===")

    out_json = OUT_DIR / "phase2_backtest_summary.json"
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nout -> {out_json}")
    return summary


if __name__ == "__main__":
    main()

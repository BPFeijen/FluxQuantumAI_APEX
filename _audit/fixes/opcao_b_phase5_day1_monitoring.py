"""
Phase 5 STEP 11+12 — Day 1 production monitoring after Opção B deploy.

Per ML-DS comment 1214603317697253. Run periodically over 24h to track:
- m30_bias flip frequency vs market regime
- Box 5266-style pattern: 4+ unknowns + 1 old bearish triggers bias=bearish
  (potential over-blocking flag in range markets without directional move)
- Counter-trend block rate
- Comparison to baseline (pre-deploy) period if available

Output: _audit/fixes/opcao_b_phase5_day1_report.md (refreshed each invocation)
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\FluxQuantumAI")
DECISION_LOG = ROOT / "logs" / "decision_log.jsonl"
SERVICE_STATE = ROOT / "logs" / "service_state.json"
OUT_REPORT = ROOT / "_audit" / "fixes" / "opcao_b_phase5_day1_report.md"

DEPLOY_TS = pd.Timestamp("2026-05-07 10:57:00", tz="UTC")  # nssm restart timestamp


def read_decisions_since(start_ts: pd.Timestamp) -> list[dict]:
    """Return decisions emitted since start_ts."""
    rows = []
    if not DECISION_LOG.exists():
        return rows
    with DECISION_LOG.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = obj.get("timestamp") or obj.get("created_at")
            if not ts:
                continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if t is pd.NaT or t < start_ts:
                continue
            ctx = obj.get("context", {}) or {}
            dec = obj.get("decision", {}) or {}
            rows.append({
                "ts": t,
                "phase": ctx.get("phase"),
                "daily_trend": ctx.get("daily_trend"),
                "m30_bias": ctx.get("m30_bias"),
                "delta_4h": ctx.get("delta_4h"),
                "direction": dec.get("direction"),
                "action": dec.get("action"),
                "reason": dec.get("reason", "")[:200],
            })
    return rows


def regime_from_delta_4h(d4h):
    if d4h is None or pd.isna(d4h):
        return "UNKNOWN"
    try:
        d = float(d4h)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if abs(d) < 200:
        return "RANGE"
    if d > 0:
        return "TREND_UP"
    return "TREND_DN"


def main():
    rows = read_decisions_since(DEPLOY_TS)
    df = pd.DataFrame(rows)
    n_total = len(df)
    if n_total == 0:
        OUT_REPORT.write_text(
            "# Phase 5 Day 1 monitoring — no decisions yet\n\n"
            f"Deploy at {DEPLOY_TS}.\n"
            f"As of {datetime.now(timezone.utc).isoformat()}: zero decisions in log.\n",
            encoding="utf-8",
        )
        print(f"No decisions yet since deploy {DEPLOY_TS}", flush=True)
        return

    # Annotate regime
    df["regime"] = df["delta_4h"].apply(regime_from_delta_4h)

    bias_dist = Counter(df["m30_bias"].fillna("unknown"))
    direction_dist = Counter(df["direction"].fillna("none"))
    action_dist = Counter(df["action"].fillna("none"))

    # Bias flips over time
    df_sorted = df.sort_values("ts").reset_index(drop=True)
    flips = 0
    prev = None
    flip_records = []
    for _, r in df_sorted.iterrows():
        cur = r["m30_bias"] or "unknown"
        if prev is not None and prev != cur:
            flips += 1
            flip_records.append({"ts": str(r["ts"]), "from": prev, "to": cur})
        prev = cur

    # Per-regime counter-trend block rate (proxy: action=BLOCK or SKIP and reason mentions BIAS_BLOCK)
    block_n = 0
    bias_block_n = 0
    for _, r in df_sorted.iterrows():
        if r["action"] in ("BLOCK", "SKIP"):
            block_n += 1
            if r["reason"] and ("BIAS_BLOCK" in r["reason"] or "BIAS_FILTER" in r["reason"]):
                bias_block_n += 1

    per_regime = defaultdict(lambda: {"n": 0, "bullish": 0, "bearish": 0, "unknown": 0})
    for _, r in df_sorted.iterrows():
        reg = r["regime"]
        per_regime[reg]["n"] += 1
        b = (r["m30_bias"] or "unknown").lower()
        if b in per_regime[reg]:
            per_regime[reg][b] += 1

    # Box 5266-style pattern alert: bias bearish in range market without directional move
    # Heuristic: regime=RANGE AND m30_bias=bearish counts
    range_bear_count = sum(1 for _, r in df_sorted.iterrows() if r["regime"] == "RANGE" and (r["m30_bias"] or "").lower() == "bearish")

    # Heartbeat
    try:
        state = json.loads(SERVICE_STATE.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    lines = []
    lines.append("# Opção B — Phase 5 Day 1 monitoring report")
    lines.append("")
    lines.append(f"**Generated**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append(f"**Deploy timestamp**: {DEPLOY_TS}")
    lines.append(f"**Time elapsed since deploy**: {datetime.now(timezone.utc) - DEPLOY_TS.to_pydatetime()}")
    lines.append("")
    lines.append("## Live service state")
    lines.append("")
    lines.append(f"- pid: `{state.get('pid')}`")
    lines.append(f"- status: `{state.get('status')}`")
    lines.append(f"- last heartbeat: `{state.get('last_heartbeat_at')}`")
    lines.append(f"- m30_bias: `{state.get('m30_bias')}` (confirmed: `{state.get('m30_bias_confirmed')}`)")
    lines.append(f"- provisional_m30_bias: `{state.get('provisional_m30_bias')}`")
    lines.append(f"- daily_trend: `{state.get('daily_trend')}`")
    lines.append(f"- delta_4h: `{state.get('delta_4h')}` | phase: `{state.get('phase')}`")
    lines.append(f"- feed_age_s: `{state.get('feed_age_s')}` | m30_age_s: `{state.get('m30_age_s')}`")
    lines.append("")
    lines.append("## Decision activity since deploy")
    lines.append("")
    lines.append(f"- Total decisions: **{n_total}**")
    lines.append(f"- Block/SKIP decisions: {block_n} ({100*block_n/n_total:.1f}%)")
    lines.append(f"- Of which BIAS_BLOCK/BIAS_FILTER reason: {bias_block_n}")
    lines.append("")
    lines.append("### m30_bias distribution (post-deploy)")
    lines.append("")
    for k in ("bullish", "bearish", "unknown"):
        v = bias_dist.get(k, 0)
        lines.append(f"- {k}: {v} ({100*v/n_total:.1f}%)")
    lines.append("")
    lines.append("### Direction distribution")
    lines.append("")
    for k, v in sorted(direction_dist.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("### Action distribution")
    lines.append("")
    for k, v in sorted(action_dist.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## m30_bias flip frequency")
    lines.append("")
    lines.append(f"- Total flips since deploy: **{flips}**")
    if flip_records:
        lines.append("")
        lines.append("Recent flips (last 20):")
        lines.append("")
        for r in flip_records[-20:]:
            lines.append(f"- `{r['ts']}` {r['from']} -> {r['to']}")
    lines.append("")
    lines.append("## Per-regime bias distribution")
    lines.append("")
    lines.append("| Regime | n | bullish | bearish | unknown |")
    lines.append("|---|---|---|---|---|")
    for reg in sorted(per_regime):
        m = per_regime[reg]
        lines.append(f"| {reg} | {m['n']} | {m['bullish']} | {m['bearish']} | {m['unknown']} |")
    lines.append("")
    lines.append("## Box 5266-style pattern alert")
    lines.append("")
    lines.append("**Pattern**: regime=RANGE AND m30_bias=bearish (potential over-blocking in flat market)")
    lines.append("")
    lines.append(f"- Occurrences since deploy: **{range_bear_count}**")
    if range_bear_count > 50:
        lines.append("")
        lines.append("⚠️ Above heuristic threshold (>50) — flag for ML-DS review.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Re-run this script periodically to refresh metrics through 24h Phase 5 window.*")

    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written: {OUT_REPORT}", flush=True)
    print(f"Total decisions since deploy: {n_total}", flush=True)
    print(f"  flips: {flips}, block/skip: {block_n}, bias_block: {bias_block_n}", flush=True)
    print(f"  range_bear pattern occurrences: {range_bear_count}", flush=True)


if __name__ == "__main__":
    main()

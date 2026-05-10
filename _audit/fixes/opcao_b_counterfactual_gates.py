"""
Phase 4 STEP 6+7 — dual counterfactual gates for Opção B deploy.

Per ML-DS comment 1214603317697253 + spec § 6.4.

Gate G1: 287 SHORTs 2026-05-07 04-05 UTC >=80% blocked under deployed F-1+F-3
Gate G2: Box 5282 morning rally >=95% blocked

This script imports the LIVE level_detector helpers (post-Phase 3 deploy) and
replays decisions in those windows. ZERO live mutation — read-only analysis.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\FluxQuantumAI")
sys.path.insert(0, str(ROOT))

from live import level_detector

DECISION_LOG_PATHS = [
    ROOT / "Backups" / "pre-op-20260423_053006" / "logs" / "decision_log.jsonl",
    ROOT / "logs" / "decision_log.jsonl",
]
M30_PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")

OUT_REPORT = ROOT / "_audit" / "fixes" / "opcao_b_counterfactual_gates.md"

# Gate windows
G1_START = pd.Timestamp("2026-05-07 04:00", tz="UTC")
G1_END   = pd.Timestamp("2026-05-07 05:00", tz="UTC")
# G2: Box 5282 morning rally. Box 5282 confirmed 04:30; rally was through 05:30.
G2_START = pd.Timestamp("2026-05-07 04:30", tz="UTC")
G2_END   = pd.Timestamp("2026-05-07 05:30", tz="UTC")


def load_decisions(start, end):
    rows = []
    seen = set()
    for path in DECISION_LOG_PATHS:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", errors="ignore") as f:
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
                if t is pd.NaT or t < start or t > end:
                    continue
                did = obj.get("decision_id") or f"{t.value}_{obj.get('decision', {}).get('direction')}"
                if did in seen:
                    continue
                seen.add(did)
                ctx = obj.get("context", {}) or {}
                dec = obj.get("decision", {}) or {}
                rows.append({
                    "ts": t,
                    "phase": ctx.get("phase"),
                    "daily_trend": ctx.get("daily_trend"),
                    "direction": dec.get("direction"),
                    "action": dec.get("action"),
                    "decision_id": did,
                })
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def replay_window(df_dec, m30_df, label):
    """For each decision in window: derive bias under live F-1+F-3 derive_m30_bias
    using only m30_df rows up to and including the decision timestamp.
    Tabulate biases + counter-trend block prediction."""
    out = []
    for _, row in df_dec.iterrows():
        ts = row["ts"]
        slice_df = m30_df[m30_df.index <= ts]
        bias, is_conf = level_detector.derive_m30_bias(slice_df, confirmed_only=False)
        direction = row["direction"]
        # Counter-trend block (proxy for cascade+F-asym):
        if direction == "LONG" and bias == "bearish":
            blocked = True
        elif direction == "SHORT" and bias == "bullish":
            blocked = True
        else:
            blocked = False
        out.append({
            "ts": str(ts),
            "direction": direction,
            "bias_new": bias,
            "is_conf": is_conf,
            "blocked": blocked,
        })
    return out


def summarize(records, label):
    n = len(records)
    if n == 0:
        return {"label": label, "n": 0}
    bias_dist = {}
    blocked_count = 0
    direction_dist = {}
    for r in records:
        bias_dist[r["bias_new"]] = bias_dist.get(r["bias_new"], 0) + 1
        direction_dist[r["direction"]] = direction_dist.get(r["direction"], 0) + 1
        if r["blocked"]:
            blocked_count += 1
    return {
        "label": label,
        "n": n,
        "bias_dist": bias_dist,
        "direction_dist": direction_dist,
        "blocked_count": blocked_count,
        "block_rate": blocked_count / n,
    }


def main():
    # Use live's derive_m30_bias which now includes F-1+F-3
    settings = level_detector._load_m30_bias_voting_settings()
    print(f"Live voting settings: min_bars={settings[0]} window={settings[1]} strategy={settings[2]}", flush=True)

    print("Loading m30 parquet...", flush=True)
    m30_df = pd.read_parquet(M30_PARQUET)
    m30_df.index = pd.to_datetime(m30_df.index, utc=True)
    # No-op price probe for live structural override; default to None inside derive
    # (no monkeypatch — using live _get_current_gc_price which may return None offline)
    print(f"  m30 rows: {len(m30_df)}", flush=True)

    # ----- G1 -----
    print(f"\nGate G1 window {G1_START} -> {G1_END}", flush=True)
    df_g1 = load_decisions(G1_START, G1_END)
    df_g1_signals = df_g1[df_g1["direction"].isin(["LONG", "SHORT"])].reset_index(drop=True)
    df_g1_shorts = df_g1_signals[df_g1_signals["direction"] == "SHORT"].reset_index(drop=True)
    print(f"  decisions: {len(df_g1)}; with direction: {len(df_g1_signals)}; SHORTs: {len(df_g1_shorts)}", flush=True)
    g1_records_all = replay_window(df_g1_signals, m30_df, "G1")
    g1_records_shorts = [r for r in g1_records_all if r["direction"] == "SHORT"]
    g1_summary = summarize(g1_records_shorts, "G1: 04-05 UTC SHORTs (target population)")
    print(f"  G1 summary: {g1_summary}", flush=True)

    # ----- G2 -----
    print(f"\nGate G2 window {G2_START} -> {G2_END}", flush=True)
    df_g2 = load_decisions(G2_START, G2_END)
    df_g2_signals = df_g2[df_g2["direction"].isin(["LONG", "SHORT"])].reset_index(drop=True)
    df_g2_shorts = df_g2_signals[df_g2_signals["direction"] == "SHORT"].reset_index(drop=True)
    print(f"  decisions: {len(df_g2)}; with direction: {len(df_g2_signals)}; SHORTs: {len(df_g2_shorts)}", flush=True)
    g2_records_all = replay_window(df_g2_signals, m30_df, "G2")
    g2_records_shorts = [r for r in g2_records_all if r["direction"] == "SHORT"]
    g2_summary = summarize(g2_records_shorts, "G2: Box 5282 04:30-05:30 UTC SHORTs")
    print(f"  G2 summary: {g2_summary}", flush=True)

    # ----- Acceptance -----
    g1_pass = g1_summary["n"] > 0 and g1_summary["block_rate"] >= 0.80
    g2_pass = g2_summary["n"] > 0 and g2_summary["block_rate"] >= 0.95
    overall_pass = g1_pass and g2_pass
    print(f"\nG1 PASS: {g1_pass} ({g1_summary.get('block_rate', 0):.2%}, threshold 80%)", flush=True)
    print(f"G2 PASS: {g2_pass} ({g2_summary.get('block_rate', 0):.2%}, threshold 95%)", flush=True)
    print(f"OVERALL: {'PASS - DEPLOY GREEN-LIT' if overall_pass else 'FAIL - STOP, recalibrate'}", flush=True)

    # ----- Report -----
    lines = []
    lines.append("# Opção B counterfactual gates — Phase 4 STEP 6+7")
    lines.append("")
    lines.append("**Author**: CC#3")
    lines.append("**Date**: 2026-05-07")
    lines.append("**Asana**: BUG-SIGNAL-INVERTED 1214556369070092 -> ML-DS comment 1214603317697253")
    lines.append("**Spec ref**: `_audit/fixes/opcao_b_phase1_m30_bias_hysteresis_spec.md` § 6.4")
    lines.append("**Live config**: `min_bars=" + str(settings[0]) + ", window=" + str(settings[1]) + ", strategy=" + settings[2] + "`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("- Imports `live.level_detector.derive_m30_bias` (now F-1+F-3 active)")
    lines.append("- For each historical decision in gate window: slice m30 parquet up to decision ts; call `derive_m30_bias`; mark counter-trend signals as 'blocked' (LONG with bearish bias OR SHORT with bullish bias)")
    lines.append("- ZERO live mutation; no production calls")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Gate G1 — 2026-05-07 04:00-05:00 UTC SHORTs burst")
    lines.append("")
    lines.append(f"- Population: SHORTs in window = **{g1_summary['n']}**")
    lines.append(f"- Bias distribution: `{g1_summary.get('bias_dist', {})}`")
    lines.append(f"- Blocked under live F-1+F-3: **{g1_summary.get('blocked_count', 0)}** ({g1_summary.get('block_rate', 0):.2%})")
    lines.append(f"- Threshold: >=80%")
    lines.append(f"- **Gate G1**: {'PASS' if g1_pass else 'FAIL'}")
    lines.append("")
    lines.append("## Gate G2 — Box 5282 04:30-05:30 UTC")
    lines.append("")
    lines.append(f"- Population: SHORTs in window = **{g2_summary['n']}**")
    lines.append(f"- Bias distribution: `{g2_summary.get('bias_dist', {})}`")
    lines.append(f"- Blocked under live F-1+F-3: **{g2_summary.get('blocked_count', 0)}** ({g2_summary.get('block_rate', 0):.2%})")
    lines.append(f"- Threshold: >=95%")
    lines.append(f"- **Gate G2**: {'PASS' if g2_pass else 'FAIL'}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    if overall_pass:
        lines.append("**Both gates PASS** — deploy is GREEN-LIT.")
        lines.append("")
        lines.append("Proceed to Phase 4 STEP 9: NSSM restart + 5min observation.")
    else:
        lines.append("**FAIL — STOP, recalibrate.**")
        lines.append("")
        if not g1_pass:
            lines.append(f"- G1 missed: {g1_summary.get('block_rate', 0):.2%} < 80%")
        if not g2_pass:
            lines.append(f"- G2 missed: {g2_summary.get('block_rate', 0):.2%} < 95%")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Per-decision detail (G1)")
    lines.append("")
    lines.append("| ts | direction | bias_new | is_conf | blocked |")
    lines.append("|---|---|---|---|---|")
    for r in g1_records_all[:50]:
        lines.append(f"| {r['ts'][:19]} | {r['direction']} | {r['bias_new']} | {r['is_conf']} | {r['blocked']} |")
    if len(g1_records_all) > 50:
        lines.append(f"| ... | ({len(g1_records_all) - 50} more rows) | | | |")
    lines.append("")
    lines.append("## Per-decision detail (G2)")
    lines.append("")
    lines.append("| ts | direction | bias_new | is_conf | blocked |")
    lines.append("|---|---|---|---|---|")
    for r in g2_records_all[:50]:
        lines.append(f"| {r['ts'][:19]} | {r['direction']} | {r['bias_new']} | {r['is_conf']} | {r['blocked']} |")
    if len(g2_records_all) > 50:
        lines.append(f"| ... | ({len(g2_records_all) - 50} more rows) | | | |")
    lines.append("")
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written: {OUT_REPORT}", flush=True)


if __name__ == "__main__":
    main()

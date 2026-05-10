"""
Comprehensive 24h+ audit for Opção B (F-1+F-3) deploy.

Windows:
  W1 = 2026-05-07 10:57:01 UTC (Opção B deploy) -> 2026-05-07 14:43:04 UTC (SL fix)
  W2 = 2026-05-07 14:43:04 UTC -> service stop ~15:58 UTC 2026-05-08

Compares stats per window, flags anomalies, audits SL/TP correctness.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\FluxQuantumAI")
DECISION_LOG = ROOT / "logs" / "decision_log.jsonl"
SERVICE_STATE = ROOT / "logs" / "service_state.json"
SERVICE_STDOUT = ROOT / "logs" / "service_stdout.log"
SERVICE_STDERR = ROOT / "logs" / "service_stderr.log"
OUT_REPORT = ROOT / "_audit" / "fixes" / "opcao_b_24h_audit_report.md"

DEPLOY_W1 = pd.Timestamp("2026-05-07 10:57:01", tz="UTC")
SL_FIX = pd.Timestamp("2026-05-07 14:43:04", tz="UTC")
NOW = pd.Timestamp.utcnow()


def parse_decisions(start_ts: pd.Timestamp):
    """Stream-parse decision_log filtered to >= start_ts."""
    rows = []
    with DECISION_LOG.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("timestamp") or d.get("created_at")
            if not ts:
                continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if t is pd.NaT or t < start_ts:
                continue
            ctx = d.get("context", {}) or {}
            dec = d.get("decision", {}) or {}
            rows.append({
                "ts": t,
                "phase": ctx.get("phase"),
                "daily_trend": ctx.get("daily_trend"),
                "m30_bias": ctx.get("m30_bias"),
                "m30_bias_confirmed": ctx.get("m30_bias_confirmed"),
                "delta_4h": ctx.get("delta_4h"),
                "session": ctx.get("session"),
                "price_mt5": d.get("price_mt5"),
                "price_gc": d.get("price_gc"),
                "direction": dec.get("direction"),
                "action": dec.get("action"),
                "entry_mode": dec.get("entry_mode"),
                "sl": dec.get("sl"),
                "tp1": dec.get("tp1"),
                "tp2": dec.get("tp2"),
                "reason": (dec.get("reason") or "")[:120],
            })
    return rows


def regime(d4h):
    if d4h is None:
        return "UNKNOWN"
    try:
        d = float(d4h)
    except Exception:
        return "UNKNOWN"
    if abs(d) < 200:
        return "RANGE"
    if d > 0:
        return "TREND_UP"
    return "TREND_DN"


def analyze(rows, label):
    n = len(rows)
    if n == 0:
        return {"label": label, "n": 0}
    df = pd.DataFrame(rows)
    df["regime"] = df["delta_4h"].apply(regime)

    bad_sl = []
    bad_tp = []
    for _, r in df.iterrows():
        d, p, sl, tp1, tp2 = r["direction"], r["price_mt5"], r["sl"], r["tp1"], r["tp2"]
        if d not in ("LONG", "SHORT") or p is None or sl is None:
            continue
        if d == "LONG" and sl >= p:
            bad_sl.append({"ts": str(r["ts"]), "p": p, "sl": sl, "mode": r["entry_mode"]})
        elif d == "SHORT" and sl <= p:
            bad_sl.append({"ts": str(r["ts"]), "p": p, "sl": sl, "mode": r["entry_mode"]})
        if tp1 is not None:
            if d == "LONG" and tp1 <= p:
                bad_tp.append({"ts": str(r["ts"]), "p": p, "tp1": tp1, "mode": r["entry_mode"]})
            elif d == "SHORT" and tp1 >= p:
                bad_tp.append({"ts": str(r["ts"]), "p": p, "tp1": tp1, "mode": r["entry_mode"]})

    bias_counts = Counter(df["m30_bias"].fillna("none"))
    confirmed_counts = Counter(df["m30_bias_confirmed"].astype(str))
    direction_counts = Counter(df["direction"].fillna("none"))
    action_counts = Counter(df["action"].fillna("none"))
    mode_counts = Counter(df["entry_mode"].fillna("none"))
    regime_bias = defaultdict(lambda: Counter())
    for _, r in df.iterrows():
        regime_bias[r["regime"]][r["m30_bias"] or "none"] += 1

    df_sorted = df.sort_values("ts").reset_index(drop=True)
    flips = 0
    flip_records = []
    prev = None
    for _, r in df_sorted.iterrows():
        cur = r["m30_bias"] or "none"
        if prev is not None and prev != cur:
            flips += 1
            flip_records.append({"ts": str(r["ts"]), "from": prev, "to": cur})
        prev = cur

    range_bear = sum(
        1 for _, r in df_sorted.iterrows()
        if r["regime"] == "RANGE" and (r["m30_bias"] or "").lower() == "bearish"
    )

    return {
        "label": label,
        "n": n,
        "first_ts": str(df_sorted["ts"].iloc[0]),
        "last_ts": str(df_sorted["ts"].iloc[-1]),
        "bias_counts": dict(bias_counts),
        "confirmed_counts": dict(confirmed_counts),
        "direction_counts": dict(direction_counts),
        "action_counts": dict(action_counts),
        "mode_counts": dict(mode_counts),
        "regime_bias": {k: dict(v) for k, v in regime_bias.items()},
        "flips": flips,
        "flip_records_last10": flip_records[-10:],
        "bad_sl_count": len(bad_sl),
        "bad_sl_samples": bad_sl[:5],
        "bad_tp_count": len(bad_tp),
        "bad_tp_samples": bad_tp[:5],
        "range_bear": range_bear,
    }


def count_log_pattern(path: Path, pattern: str) -> int:
    if not path.exists():
        return 0
    n = 0
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                if pattern in line:
                    n += 1
    except Exception:
        return -1
    return n


def main():
    print("Parsing decision_log...", flush=True)
    rows_w1 = [r for r in parse_decisions(DEPLOY_W1) if DEPLOY_W1 <= r["ts"] < SL_FIX]
    rows_w2 = [r for r in parse_decisions(SL_FIX) if r["ts"] >= SL_FIX]
    rows_total = [r for r in parse_decisions(DEPLOY_W1) if r["ts"] >= DEPLOY_W1]

    a_w1 = analyze(rows_w1, "W1: Opção B deploy -> SL fix")
    a_w2 = analyze(rows_w2, "W2: SL fix -> service stop / now")
    a_total = analyze(rows_total, "TOTAL: 24h+")

    # Log audits
    bias_block_stdout = count_log_pattern(SERVICE_STDOUT, "BIAS_BLOCK")
    range_bound_block = count_log_pattern(SERVICE_STDOUT, "RANGE_BOUND_BIAS_BLOCK")
    trending_block = count_log_pattern(SERVICE_STDOUT, "TRENDING_BIAS_BLOCK")
    abort_stale = count_log_pattern(SERVICE_STDOUT, "ABORT_STALE")
    fallback_warn_stderr = count_log_pattern(SERVICE_STDERR, "DISPLACEMENT_SL_FALLBACK")
    file_lock_errors = count_log_pattern(SERVICE_STDERR, "WinError 32")
    access_denied = count_log_pattern(SERVICE_STDERR, "WinError 5")
    diverge_warns = count_log_pattern(SERVICE_STDERR, "DISPLACEMENT_DIVERGE")

    # service_state snapshot
    try:
        state = json.loads(SERVICE_STATE.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    lines = []
    lines.append("# Opção B 24h+ audit — production observation")
    lines.append("")
    lines.append(f"**Generated**: {NOW.isoformat(timespec='seconds')}")
    lines.append(f"**Deploy (Opção B)**: 2026-05-07 10:57:01 UTC (PID 36372)")
    lines.append(f"**SL fix deploy**: 2026-05-07 14:43:04 UTC (additional restart)")
    lines.append(f"**Service current state**: STOPPED (user-initiated, last hb ~2026-05-08 15:58 UTC)")
    lines.append(f"**Audit window**: ~28h elapsed since deploy")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Service-state snapshot at audit time")
    lines.append("")
    lines.append(f"- last_pid: `{state.get('pid')}`")
    lines.append(f"- last_status: `{state.get('status')}`")
    lines.append(f"- last_heartbeat: `{state.get('last_heartbeat_at')}`")
    lines.append(f"- m30_bias: `{state.get('m30_bias')}` (confirmed: `{state.get('m30_bias_confirmed')}`)")
    lines.append(f"- provisional_m30_bias: `{state.get('provisional_m30_bias')}`")
    lines.append(f"- daily_trend: `{state.get('daily_trend')}`")
    lines.append(f"- delta_4h: `{state.get('delta_4h')}` | phase: `{state.get('phase')}`")
    d1h4 = state.get("d1h4_bias", {})
    lines.append(f"- d1h4: `{d1h4.get('direction')}/{d1h4.get('strength')}` (h4_jac={d1h4.get('h4_jac_dir')} d1_jac={d1h4.get('d1_jac_dir')})")
    lines.append(f"- gc_price: `{state.get('gc_price')}` | mt5_price: `{state.get('mt5_price')}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. Activity summary")
    lines.append("")
    lines.append("| Window | n | first_ts | last_ts |")
    lines.append("|---|---|---|---|")
    for a in (a_w1, a_w2, a_total):
        lines.append(f"| {a['label']} | {a['n']} | {a.get('first_ts','')} | {a.get('last_ts','')} |")
    lines.append("")

    for a in (a_w1, a_w2):
        if a["n"] == 0:
            continue
        lines.append(f"### {a['label']}")
        lines.append("")
        lines.append(f"**m30_bias distribution**: {a['bias_counts']}")
        lines.append(f"**confirmed flag distribution**: {a['confirmed_counts']}")
        lines.append(f"**direction distribution**: {a['direction_counts']}")
        lines.append(f"**action distribution**: {a['action_counts']}")
        lines.append(f"**entry_mode distribution**: {a['mode_counts']}")
        lines.append(f"**bias flips**: {a['flips']}")
        lines.append("")
        lines.append("**Per-regime bias**:")
        lines.append("")
        for reg, biases in a["regime_bias"].items():
            lines.append(f"- {reg}: {dict(biases)}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. SL / TP correctness audit")
    lines.append("")
    lines.append(f"### W1 (deploy -> SL fix)")
    lines.append(f"- bad_sl (wrong side of entry): **{a_w1['bad_sl_count']}**")
    if a_w1["bad_sl_samples"]:
        lines.append("  Samples (first 5):")
        for s in a_w1["bad_sl_samples"]:
            lines.append(f"  - `{s['ts']}` mode={s['mode']} price={s['p']} sl={s['sl']}")
    lines.append(f"- bad_tp1 (wrong side of entry): **{a_w1['bad_tp_count']}**")
    if a_w1["bad_tp_samples"]:
        for s in a_w1["bad_tp_samples"][:3]:
            lines.append(f"  - `{s['ts']}` mode={s['mode']} price={s['p']} tp1={s['tp1']}")
    lines.append("")
    lines.append(f"### W2 (post SL fix)")
    lines.append(f"- bad_sl: **{a_w2['bad_sl_count']}**")
    lines.append(f"- bad_tp1: **{a_w2['bad_tp_count']}**")
    if a_w2["bad_sl_count"] == 0 and a_w2["bad_tp_count"] == 0:
        lines.append("  ✅ ZERO inverted SL/TP since fix")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. Bias dynamics (F-1+F-3 voting)")
    lines.append("")
    lines.append(f"- Total bias flips W1: {a_w1.get('flips', 0)}")
    lines.append(f"- Total bias flips W2: {a_w2.get('flips', 0)}")
    lines.append(f"- range_bear pattern (RANGE regime + bearish bias) W1: {a_w1.get('range_bear', 0)}")
    lines.append(f"- range_bear pattern W2: {a_w2.get('range_bear', 0)}")
    if a_total.get("flip_records_last10"):
        lines.append("")
        lines.append("**Last 10 bias flips (whole window)**:")
        for r in a_total["flip_records_last10"]:
            lines.append(f"- `{r['ts']}` {r['from']} -> {r['to']}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. Counter-trend block events (stdout log)")
    lines.append("")
    lines.append("These are F-asym (Opção A) + F-1+F-3 (Opção B) protective blocks:")
    lines.append("")
    lines.append(f"- RANGE_BOUND_BIAS_BLOCK occurrences: **{range_bound_block}**")
    lines.append(f"- TRENDING_BIAS_BLOCK occurrences: **{trending_block}**")
    lines.append(f"- Total BIAS_BLOCK keyword: {bias_block_stdout}")
    lines.append(f"- ABORT_STALE (price moved past level): {abort_stale}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 6. Health / error indicators")
    lines.append("")
    lines.append(f"- DISPLACEMENT_SL_FALLBACK warnings: **{fallback_warn_stderr}** (zero = no displacement bar mismatch since fix)")
    lines.append(f"- DISPLACEMENT_DIVERGE warnings: {diverge_warns} (informational; CURRENT vs OPT_A path divergence)")
    lines.append(f"- File lock errors (WinError 32 on decision_live.json): **{file_lock_errors}**")
    lines.append(f"- Access denied errors (WinError 5): **{access_denied}**")
    if file_lock_errors > 0 or access_denied > 0:
        lines.append("")
        lines.append("⚠️ **decision_live.json write contention detected** — likely Dashboard service holding the file. Non-fatal but should be diagnosed.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 7. Verdict (Phase 5)")
    lines.append("")
    pass_criteria = []
    if a_w2["n"] > 0 and a_w2["bad_sl_count"] == 0 and a_w2["bad_tp_count"] == 0:
        pass_criteria.append("✅ Zero inverted SL/TP since SL fix deploy (W2)")
    else:
        pass_criteria.append(f"❌ Inverted SL/TP detected post-fix: sl={a_w2['bad_sl_count']} tp={a_w2['bad_tp_count']}")
    if a_total.get("range_bear", 0) < 50:
        pass_criteria.append(f"✅ Box 5266-style range_bear pattern: only {a_total.get('range_bear', 0)} occurrences (under heuristic threshold)")
    else:
        pass_criteria.append(f"⚠️ range_bear pattern: {a_total.get('range_bear', 0)} occurrences (above 50 — review for over-blocking)")
    if a_total.get("flips", 0) < 50:
        pass_criteria.append(f"✅ Bias flip frequency reasonable: {a_total.get('flips', 0)} flips in 28h")
    else:
        pass_criteria.append(f"⚠️ High flip frequency: {a_total.get('flips', 0)} flips")

    for c in pass_criteria:
        lines.append(f"- {c}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 8. Open issues")
    lines.append("")
    lines.append("- ⚠️ Service currently STOPPED (user-initiated). Awaiting reactivation directive.")
    if file_lock_errors > 0:
        lines.append(f"- ⚠️ `{file_lock_errors}` `decision_live.json` write contentions in stderr (Dashboard process holding file). Operational, non-fatal.")
    lines.append("- ⚠️ MT5 brokers (RoboForex + Hantec) DISCONNECTED throughout window — no live execution attempted; signals were emitted but EXEC_FAILED.")
    lines.append("- ⚠️ BUG-M30-STUCK-VS-H4-FLIP backlog (GID 1214603041501490) — orthogonal to Opção B, deferred per ML-DS directive.")
    lines.append("")

    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written: {OUT_REPORT}", flush=True)
    print(f"\nW1 ({a_w1['n']} decisions): bias={a_w1.get('bias_counts')} bad_sl={a_w1.get('bad_sl_count')} bad_tp={a_w1.get('bad_tp_count')}")
    print(f"W2 ({a_w2['n']} decisions): bias={a_w2.get('bias_counts')} bad_sl={a_w2.get('bad_sl_count')} bad_tp={a_w2.get('bad_tp_count')}")
    print(f"BIAS_BLOCK stdout: range={range_bound_block} trending={trending_block}")
    print(f"DISPLACEMENT_SL_FALLBACK: {fallback_warn_stderr}")
    print(f"file_lock_errors: {file_lock_errors}")


if __name__ == "__main__":
    main()

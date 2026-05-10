"""
Phase 2 sanity rerun — replace Episode A with confirmed valid bearish episode.

Confirmed bearish window: 2026-04-20 14:00-15:00 UTC (INCIDENT_20260420_LONG_DURING_DROP)
- Market dropped -25 pts in 40min
- daily_trend=short, d4h=-1121
- System emitted 8 LONGs incorrectly (the bug — those LONGs were wrong)
- Correct bias for that period = BEARISH

Acceptance: ≥80% bearish under winner config.
Output: appends "Sanity check (revised)" section to existing calibration md.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DECISION_LOG_PATHS = [
    Path(r"C:\FluxQuantumAI\Backups\pre-op-20260423_053006\logs\decision_log.jsonl"),
    Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl"),
]
M30_PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
OUT_REPORT = Path(r"C:\FluxQuantumAI\_audit\calibrations\m30_bias_voting_calibration.md")

# Winner config from calibration grid
WINNER_MIN_BARS = 5
WINNER_WINDOW = 5
WINNER_STRATEGY = "recency_weighted"

# Confirmed bearish episode
EP_START = pd.Timestamp("2026-04-20 14:00", tz="UTC")
EP_END = pd.Timestamp("2026-04-20 15:00", tz="UTC")


def classify_box_from_dict(d: dict) -> str:
    bh = d.get("box_high")
    bl = d.get("box_low")
    lt = d.get("liq_top")
    lb = d.get("liq_bot")
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (bh, bl, lt, lb)):
        return "unknown"
    bull_ext = lt > bh
    bear_ext = lb < bl
    if bull_ext and not bear_ext:
        return "bullish"
    if bear_ext and not bull_ext:
        return "bearish"
    return "unknown"


def voting_vote(classifications, strategy):
    if not classifications:
        return "unknown"
    if strategy == "majority":
        n = len(classifications)
        bull = classifications.count("bullish")
        bear = classifications.count("bearish")
        if bull > n // 2:
            return "bullish"
        if bear > n // 2:
            return "bearish"
        return "unknown"
    n = len(classifications)
    weights = list(range(1, n + 1))
    bull_w = sum(w for w, c in zip(weights, classifications) if c == "bullish")
    bear_w = sum(w for w, c in zip(weights, classifications) if c == "bearish")
    if bull_w > 1.5 * max(bear_w, 1e-9) and bull_w > 0:
        return "bullish"
    if bear_w > 1.5 * max(bull_w, 1e-9) and bear_w > 0:
        return "bearish"
    return "unknown"


class ConfirmedBoxIndex:
    def __init__(self, m30):
        confirmed_ids = m30.loc[m30["m30_box_confirmed"] == True, "m30_box_id"].dropna().unique()
        items = []
        for bid in confirmed_ids:
            sub = m30[m30["m30_box_id"] == bid]
            sub_conf = sub[sub["m30_box_confirmed"] == True]
            if sub_conf.empty:
                continue
            first_conf_ts = sub_conf.index[0]
            last_row = sub.iloc[-1]
            d = {
                "box_high": float(last_row.get("m30_box_high", float("nan"))),
                "box_low":  float(last_row.get("m30_box_low",  float("nan"))),
                "liq_top":  float(last_row.get("m30_liq_top",  float("nan"))),
                "liq_bot":  float(last_row.get("m30_liq_bot",  float("nan"))),
            }
            items.append((first_conf_ts, int(bid), classify_box_from_dict(d), len(sub)))
        items.sort(key=lambda x: x[0])
        self.ts = np.array([pd.Timestamp(x[0]).value for x in items], dtype=np.int64)
        self.box_ids = np.array([x[1] for x in items], dtype=np.int64)
        self.base_class = [x[2] for x in items]
        self.bars = np.array([x[3] for x in items], dtype=np.int32)

    def last_k_at(self, ts, k):
        ts_ns = pd.Timestamp(ts).value
        idx_end = int(np.searchsorted(self.ts, ts_ns, side="right"))
        if idx_end == 0:
            return []
        idx_start = max(0, idx_end - k)
        return list(range(idx_start, idx_end))

    def classification_with_min_bars(self, idx, min_bars):
        if int(self.bars[idx]) < min_bars:
            return "unknown"
        return self.base_class[idx]


def derive_bias_winner(box_index, ts):
    idxs = box_index.last_k_at(ts, WINNER_WINDOW)
    if not idxs:
        return "unknown"
    classifications = [box_index.classification_with_min_bars(i, WINNER_MIN_BARS) for i in idxs]
    return voting_vote(classifications, WINNER_STRATEGY)


def derive_bias_baseline(box_index, ts):
    idxs = box_index.last_k_at(ts, 1)
    if not idxs:
        return "unknown"
    return box_index.base_class[idxs[0]]


def main():
    print("Sanity rerun — confirmed bearish episode 2026-04-20 14:00-15:00 UTC", flush=True)

    print("Loading m30 parquet + building index...", flush=True)
    boxes = pd.read_parquet(M30_PARQUET)
    boxes.index = pd.to_datetime(boxes.index, utc=True)
    boxes = boxes[(boxes.index >= pd.Timestamp("2026-03-01", tz="UTC")) & (boxes.index <= pd.Timestamp("2026-05-07 23:59:59", tz="UTC"))]
    box_index = ConfirmedBoxIndex(boxes)
    print(f"  {len(box_index.ts)} confirmed boxes indexed", flush=True)

    print("Reading decision_log Apr-20 window...", flush=True)
    decisions = []
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
                if t is pd.NaT or t < EP_START or t > EP_END:
                    continue
                ctx = obj.get("context", {}) or {}
                dec = obj.get("decision", {}) or {}
                decisions.append({
                    "ts": t,
                    "phase": ctx.get("phase"),
                    "daily_trend": ctx.get("daily_trend"),
                    "m30_bias_old": ctx.get("m30_bias"),
                    "delta_4h": ctx.get("delta_4h"),
                    "action": dec.get("action"),
                    "direction": dec.get("direction"),
                })
    print(f"  decisions in window: {len(decisions)}", flush=True)

    print("Computing bias under winner config + baseline...", flush=True)
    counts_winner = {"bearish": 0, "bullish": 0, "unknown": 0}
    counts_baseline = {"bearish": 0, "bullish": 0, "unknown": 0}
    counts_old = {"bearish": 0, "bullish": 0, "unknown": 0, "other": 0}
    direction_counts = {"LONG": 0, "SHORT": 0, None: 0}
    delta4h_samples = []
    daily_trend_samples = []

    for d in decisions:
        b_w = derive_bias_winner(box_index, d["ts"])
        b_b = derive_bias_baseline(box_index, d["ts"])
        b_o = (d["m30_bias_old"] or "unknown").lower()
        counts_winner[b_w] = counts_winner.get(b_w, 0) + 1
        counts_baseline[b_b] = counts_baseline.get(b_b, 0) + 1
        if b_o in counts_old:
            counts_old[b_o] += 1
        else:
            counts_old["other"] += 1
        direction_counts[d["direction"]] = direction_counts.get(d["direction"], 0) + 1
        if d["delta_4h"] is not None:
            delta4h_samples.append(d["delta_4h"])
        if d["daily_trend"]:
            daily_trend_samples.append(d["daily_trend"])

    n = len(decisions)
    print(f"  winner: {counts_winner}", flush=True)
    print(f"  baseline: {counts_baseline}", flush=True)
    print(f"  m30_bias_old: {counts_old}", flush=True)
    print(f"  direction: {direction_counts}", flush=True)
    if delta4h_samples:
        print(f"  delta_4h: min={min(delta4h_samples)} max={max(delta4h_samples)} mean={np.mean(delta4h_samples):.1f}", flush=True)
    if daily_trend_samples:
        from collections import Counter
        print(f"  daily_trend: {Counter(daily_trend_samples).most_common()}", flush=True)

    bear_pct = counts_winner["bearish"] / max(n, 1)
    bull_pct = counts_winner["bullish"] / max(n, 1)
    unk_pct  = counts_winner["unknown"] / max(n, 1)
    threshold = 0.80
    passed = bear_pct >= threshold

    # ----- Append section to calibration md -----
    print(f"  bear_pct under winner: {bear_pct:.2%} (threshold {threshold:.0%}) -> {'PASS' if passed else 'FAIL'}", flush=True)

    section = []
    section.append("")
    section.append("---")
    section.append("")
    section.append("## 6b. Sanity check (REVISED) — confirmed bearish episode")
    section.append("")
    section.append("**Per ML-DS directive 1214600890407658 follow-up**: previous Episode A (2026-05-06 04h burst) was rejected because those 200 SHORTs were the BUG-SIGNAL-INVERTED inverted signals themselves (counter-trend during +91pt rally). Replaced with a confirmed bearish historical episode.")
    section.append("")
    section.append("### Episode selection rationale")
    section.append("")
    section.append("**Window**: 2026-04-20 14:00–15:00 UTC")
    section.append("**Source**: `sprints/INCIDENT_20260420_LONG_DURING_DROP/20260420_145845/INITIAL_ANALYSIS.md`")
    section.append("**Why valid bearish**:")
    section.append("- Market dropped **-25 pts in 40min** during this window (clear bearish action)")
    section.append("- `daily_trend = short` per decision_log context fields")
    section.append("- `delta_4h` heavily negative (~-1121 reported in incident analysis)")
    section.append("- Strong-bearish H4 momentum (full red H4 candle 12:00-16:00 in formation)")
    section.append("- The opposite of the 2026-05-06 burst: SHORTs WOULD have been correct here; the live system bug emitted LONGs instead (overextension reversal in TRENDING_DN), losing money — making this the canonical 'valid bearish, did the system get it right?' test")
    section.append("")
    section.append("### Methodology")
    section.append("")
    section.append("- Replay all decision_log entries in window with **winner config** `(min_bars=5, window=5, recency_weighted)` via `ConfirmedBoxIndex` precompute")
    section.append("- Tabulate bias counts: bearish / unknown / bullish")
    section.append("- Acceptance: bearish ≥ 80% (no over-blocking valid bearish)")
    section.append("- Comparator: baseline (single-most-recent confirmed box) and `m30_bias_old` (recorded in decision_log at original time)")
    section.append("")
    section.append("### Result")
    section.append("")
    section.append(f"**Decisions in window**: {n}")
    section.append("")
    section.append("**Bias distribution**:")
    section.append("")
    section.append("| Source | bearish | unknown | bullish |")
    section.append("|---|---|---|---|")
    section.append(f"| Winner (5,5,recency_weighted) | {counts_winner['bearish']} ({100*counts_winner['bearish']/max(n,1):.1f}%) | {counts_winner['unknown']} ({100*counts_winner['unknown']/max(n,1):.1f}%) | {counts_winner['bullish']} ({100*counts_winner['bullish']/max(n,1):.1f}%) |")
    section.append(f"| Baseline (current production) | {counts_baseline['bearish']} ({100*counts_baseline['bearish']/max(n,1):.1f}%) | {counts_baseline['unknown']} ({100*counts_baseline['unknown']/max(n,1):.1f}%) | {counts_baseline['bullish']} ({100*counts_baseline['bullish']/max(n,1):.1f}%) |")
    section.append(f"| `m30_bias_old` (recorded) | {counts_old['bearish']} ({100*counts_old['bearish']/max(n,1):.1f}%) | {counts_old['unknown']} ({100*counts_old['unknown']/max(n,1):.1f}%) | {counts_old['bullish']} ({100*counts_old['bullish']/max(n,1):.1f}%) |")
    section.append("")
    section.append("**Direction distribution at original time** (from decision_log):")
    section.append("")
    for k, v in direction_counts.items():
        section.append(f"- {k}: {v}")
    section.append("")
    if delta4h_samples:
        section.append(f"**Context**: delta_4h range [{min(delta4h_samples):.0f}, {max(delta4h_samples):.0f}], mean {np.mean(delta4h_samples):.0f}")
    if daily_trend_samples:
        from collections import Counter
        section.append(f"**daily_trend distribution**: {dict(Counter(daily_trend_samples).most_common())}")
    section.append("")
    section.append(f"### Acceptance ({threshold:.0%} bearish required)")
    section.append("")
    if passed:
        section.append(f"**Result**: ✅ **PASS** — {bear_pct:.2%} bearish under winner config (≥{threshold:.0%}). No over-blocking of valid bearish episode.")
        section.append("")
        section.append("Phase 3 implementation is **green-lit** with winner config: `(min_bars=5, window=5, recency_weighted)`.")
    else:
        section.append(f"**Result**: ❌ **FAIL** — only {bear_pct:.2%} bearish under winner config (<{threshold:.0%} threshold).")
        section.append("")
        section.append(f"Distribution: bearish={bear_pct:.1%}, unknown={unk_pct:.1%}, bullish={bull_pct:.1%}.")
        section.append("")
        section.append("**Triage required** — possible causes:")
        section.append("- Under F-1 `min_bars=5`, recent confirmed boxes during this window may have <5 bars → forced unknown")
        section.append("- F-3 `window=5` × recency_weighted may have older bullish boxes outvoting recent bearish")
        section.append("- Underlying box state at this window may not have produced bear_ext (need m30 data inspection)")
        section.append("- Possible candidate: relax to `(min_bars=4, window=3, recency_weighted)` (a top-10 grid runner) which is closer to baseline and may preserve more bearish")
        section.append("")
        section.append("ML-DS triage required before Phase 3 proceeds.")
    section.append("")
    section.append("### Final winner status")
    section.append("")
    if passed:
        section.append("**Reaffirmed**: `min_bars=5, window=5, strategy=recency_weighted` — both")
        section.append("(a) maximizes mean accuracy across 4 regimes (Section 5)")
        section.append("(b) preserves bearish in confirmed bearish episode (this section)")
    else:
        section.append("**Pending re-evaluation** — winner config did not preserve bearish in confirmed bearish episode. ML-DS to decide whether to:")
        section.append("- Adjust threshold")
        section.append("- Pick a different grid candidate (e.g., runner-up `(6,5,recency_weighted)` or one with higher TREND_DN delta)")
        section.append("- Investigate box state in window (Phase 0-style deep dive)")
    section.append("")

    existing = OUT_REPORT.read_text(encoding="utf-8")
    OUT_REPORT.write_text(existing + "\n".join(section), encoding="utf-8")
    print(f"  appended to {OUT_REPORT}", flush=True)


if __name__ == "__main__":
    main()

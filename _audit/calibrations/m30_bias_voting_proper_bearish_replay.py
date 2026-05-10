"""
Phase 2 sanity rerun PROPER — replay m30_bias under winner config on chosen
proper bearish episodes from search.

Per ML-DS comment 1214603041779694 STEP 2.

Episodes (from m30_bias_voting_proper_bearish_candidates.json):
  - Box 5237 (2026-04-17 19:30 UTC, 7 bars, prev=bearish, drop -28.8pts) [PRIMARY]
  - Box 5262 (2026-04-28 11:30 UTC, 6 bars, prev=unknown, drop -39.2pts)
  - Box 5266 (2026-04-29 06:00 UTC, 4 bars, prev=unknown, drop -25.1pts)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

M30_PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
OUT_REPORT = Path(r"C:\FluxQuantumAI\_audit\calibrations\m30_bias_voting_calibration.md")

WINNER_MIN_BARS = 5
WINNER_WINDOW = 5
WINNER_STRATEGY = "recency_weighted"

EPISODES = [
    {"box_id": 5237, "first_conf_ts": "2026-04-17 19:30:00+00:00", "bars": 7, "prev_cls": "bearish", "drop": 28.80, "label": "PRIMARY (multi-box bearish chain)"},
    {"box_id": 5262, "first_conf_ts": "2026-04-28 11:30:00+00:00", "bars": 6, "prev_cls": "unknown", "drop": 39.20, "label": "SECONDARY (largest drop)"},
    {"box_id": 5266, "first_conf_ts": "2026-04-29 06:00:00+00:00", "bars": 4, "prev_cls": "unknown", "drop": 25.10, "label": "TERTIARY (boundary-bars)"},
]

SAMPLE_HORIZON_MIN = 60
SAMPLE_INTERVAL_MIN = 5  # 12 samples per hour


def classify_box_dict(d):
    bh, bl, lt, lb = d.get("box_high"), d.get("box_low"), d.get("liq_top"), d.get("liq_bot")
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
            items.append((first_conf_ts, int(bid), classify_box_dict(d), len(sub)))
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
        return "unknown", []
    classifications = [box_index.classification_with_min_bars(i, WINNER_MIN_BARS) for i in idxs]
    return voting_vote(classifications, WINNER_STRATEGY), classifications


def derive_bias_baseline(box_index, ts):
    idxs = box_index.last_k_at(ts, 1)
    if not idxs:
        return "unknown"
    return box_index.base_class[idxs[0]]


def main():
    print("Loading m30 + building index...", flush=True)
    boxes = pd.read_parquet(M30_PARQUET)
    boxes.index = pd.to_datetime(boxes.index, utc=True)
    box_index = ConfirmedBoxIndex(boxes)
    print(f"  {len(box_index.ts)} confirmed boxes indexed", flush=True)

    results = []
    for ep in EPISODES:
        ts0 = pd.Timestamp(ep["first_conf_ts"])
        sample_ts = [ts0 + pd.Timedelta(minutes=m) for m in range(0, SAMPLE_HORIZON_MIN + 1, SAMPLE_INTERVAL_MIN)]
        counts_winner = {"bearish": 0, "bullish": 0, "unknown": 0}
        counts_baseline = {"bearish": 0, "bullish": 0, "unknown": 0}
        per_sample = []
        for ts in sample_ts:
            bias_w, classifications = derive_bias_winner(box_index, ts)
            bias_b = derive_bias_baseline(box_index, ts)
            counts_winner[bias_w] = counts_winner.get(bias_w, 0) + 1
            counts_baseline[bias_b] = counts_baseline.get(bias_b, 0) + 1
            per_sample.append({
                "ts": str(ts),
                "winner": bias_w,
                "baseline": bias_b,
                "last5_classifications": classifications,
            })

        n = len(sample_ts)
        bear_pct = counts_winner["bearish"] / n
        ep_result = {
            "box_id": ep["box_id"],
            "label": ep["label"],
            "first_conf_ts": ep["first_conf_ts"],
            "bars": ep["bars"],
            "prev_cls": ep["prev_cls"],
            "drop_pts": ep["drop"],
            "n_samples": n,
            "counts_winner": counts_winner,
            "counts_baseline": counts_baseline,
            "bear_pct_winner": bear_pct,
            "passed_80pct": bear_pct >= 0.80,
            "per_sample": per_sample,
        }
        results.append(ep_result)
        print(f"\n{ep['label']} (Box {ep['box_id']} {ep['first_conf_ts']}):", flush=True)
        print(f"  bars={ep['bars']}, prev_cls={ep['prev_cls']}, drop={ep['drop']:.1f}pts", flush=True)
        print(f"  samples: {n}, winner: {counts_winner}, baseline: {counts_baseline}", flush=True)
        print(f"  bear_pct (winner): {bear_pct:.2%} -> {'PASS' if bear_pct >= 0.80 else 'FAIL'}", flush=True)
        if per_sample:
            print(f"  first sample classifications (last 5 confirmed boxes): {per_sample[0]['last5_classifications']}", flush=True)
            print(f"  last sample classifications: {per_sample[-1]['last5_classifications']}", flush=True)

    primary = next((r for r in results if r["label"].startswith("PRIMARY")), None)
    overall_pass = primary["passed_80pct"] if primary else False

    section = []
    section.append("")
    section.append("---")
    section.append("")
    section.append("## 6c. Sanity check (REVISED x2) — proper bearish episodes")
    section.append("")
    section.append("**Per ML-DS directive 1214603041779694**: previous Section 6b 2026-04-20 episode revealed an orthogonal stuck-M30-vs-H4-flip bug class (now backlogged as **BUG-M30-STUCK-VS-H4-FLIP**, GID 1214603041501490). Section 6c replaces it with a proper bear_ext-only multi-box bearish cycle test.")
    section.append("")
    section.append("### Search methodology")
    section.append("")
    section.append("Filtered `gc_m30_boxes.parquet` Apr-May 2026 for confirmed M30 boxes meeting ALL of:")
    section.append("- `bear_ext = (m30_liq_bot < m30_box_low)` AND NOT `bull_ext` (clean bearish classification)")
    section.append("- bars >= 4 in box (passes F-1 min_bars=5 closely)")
    section.append("- previous confirmed box is bearish OR unknown (multi-box bearish/neutral cycle)")
    section.append("- subsequent price drop >= 15pts within 2h of first confirmation (validates SHORTs would be correct)")
    section.append("")
    section.append("**Result of search**: 3 candidates found (out of 8 bear_ext-only ≥4-bar boxes in window):")
    section.append("")
    section.append("| Box | first_conf_ts | bars | prev_cls | p0 | min(2h) | drop_pts |")
    section.append("|---|---|---|---|---|---|---|")
    section.append("| **5237** (PRIMARY) | 2026-04-17 19:30 UTC | 7 | bearish | 4878.35 | 4849.55 | -28.80 |")
    section.append("| 5262 (SECONDARY) | 2026-04-28 11:30 UTC | 6 | unknown | 4609.10 | 4569.90 | -39.20 |")
    section.append("| 5266 (TERTIARY) | 2026-04-29 06:00 UTC | 4 | unknown | 4606.50 | 4581.40 | -25.10 |")
    section.append("")
    section.append("Box 5237 chosen as PRIMARY because previous box was also bearish — the only candidate with multi-bearish-box chain (true bearish cycle, not just bear-after-unknown).")
    section.append("")
    section.append("### Replay methodology")
    section.append("")
    section.append("- For each episode: sample bias at first_conf_ts + {0, 5, 10, ..., 60} min (13 samples per episode)")
    section.append("- Apply winner config `(min_bars=5, window=5, recency_weighted)` via `ConfirmedBoxIndex`")
    section.append("- Tabulate bearish / unknown / bullish counts vs baseline (single-box production)")
    section.append("- Acceptance: bias bearish >= 80% under winner")
    section.append("")
    section.append("### Results")
    section.append("")
    for r in results:
        section.append(f"#### {r['label']} — Box {r['box_id']} ({r['first_conf_ts']})")
        section.append("")
        section.append(f"- bars in box: {r['bars']}, prev_cls: `{r['prev_cls']}`, subsequent drop: -{r['drop_pts']:.1f}pts")
        section.append(f"- samples: {r['n_samples']}")
        section.append("")
        section.append("| Source | bearish | unknown | bullish |")
        section.append("|---|---|---|---|")
        n = r["n_samples"]
        cw, cb = r["counts_winner"], r["counts_baseline"]
        section.append(f"| Winner (5,5,recency_weighted) | {cw['bearish']} ({100*cw['bearish']/n:.1f}%) | {cw['unknown']} ({100*cw['unknown']/n:.1f}%) | {cw['bullish']} ({100*cw['bullish']/n:.1f}%) |")
        section.append(f"| Baseline (current production) | {cb['bearish']} ({100*cb['bearish']/n:.1f}%) | {cb['unknown']} ({100*cb['unknown']/n:.1f}%) | {cb['bullish']} ({100*cb['bullish']/n:.1f}%) |")
        section.append("")
        section.append(f"- **bearish under winner: {r['bear_pct_winner']:.2%}** -> {'✅ **PASS**' if r['passed_80pct'] else '❌ **FAIL**'} (80% threshold)")
        if r["per_sample"]:
            section.append(f"- last-5 classifications first sample: `{r['per_sample'][0]['last5_classifications']}`")
            section.append(f"- last-5 classifications last sample: `{r['per_sample'][-1]['last5_classifications']}`")
        section.append("")
    section.append("### Acceptance verdict")
    section.append("")
    if primary and primary["passed_80pct"]:
        section.append(f"✅ **PASS** — PRIMARY episode (Box 5237) bearish {primary['bear_pct_winner']:.2%} (>=80%).")
        section.append("")
        section.append("Phase 3 implementation is **green-lit** with winner config:")
        section.append("```json")
        section.append('  "m30_bias_min_bars": 5,')
        section.append('  "m30_bias_voting_window": 5,')
        section.append('  "m30_bias_voting_strategy": "recency_weighted"')
        section.append("```")
    elif primary:
        section.append(f"❌ **FAIL** — PRIMARY episode (Box 5237) bearish only {primary['bear_pct_winner']:.2%} (<80%).")
        section.append("")
        section.append("ML-DS triage required. Possible interpretations:")
        section.append("- F-1 min_bars=5 too strict for this episode (Box 5237 has 7 bars but recent boxes preceding may not all qualify)")
        section.append("- F-3 voting window=5 may include older non-bearish boxes diluting signal")
        section.append("- Inspect last5_classifications above to identify which boxes diluted")
    else:
        section.append("No PRIMARY result available.")
    section.append("")
    section.append("### Final winner status (Phase 2)")
    section.append("")
    if primary and primary["passed_80pct"]:
        section.append("**Reaffirmed**: `min_bars=5, window=5, strategy=recency_weighted` — passes")
        section.append("- (a) calibration grid acceptance (no regime regress >5pp; +30.72pp TREND_UP)")
        section.append("- (b) Episode B (Box 5282 transient) — bias NOT bearish (target outcome)")
        section.append("- (c) Section 6c PRIMARY proper bearish episode — bias BEARISH (>=80%)")
        section.append("")
        section.append("Stuck-M30-vs-H4-flip mode (2026-04-20 incident class) is acknowledged orthogonal scope, tracked in **BUG-M30-STUCK-VS-H4-FLIP** (GID 1214603041501490).")
    section.append("")
    section.append("ZERO live code changes. Production cascade + F-asym (Opção A) continues running.")

    existing = OUT_REPORT.read_text(encoding="utf-8")
    OUT_REPORT.write_text(existing + "\n".join(section), encoding="utf-8")
    print(f"\nAppended Section 6c to {OUT_REPORT}", flush=True)
    print(f"\nOverall PASS: {overall_pass}", flush=True)


if __name__ == "__main__":
    main()

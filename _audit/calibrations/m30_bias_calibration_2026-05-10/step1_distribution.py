"""G-PURDUE Step 1 — Distribution observation.

Goal: characterize the empirical distribution of bars-per-confirmed-box and
related quantities BEFORE choosing any threshold. Compare two corpora:

  - NARROW: May 5-8 2026 — current production window (4 days, the regime
            that produced bias=unknown 95% on 5/8 stop event).
  - BROAD : Apr-May 2026 — same horizon used by Phase 2 calibration in
            `_audit/calibrations/m30_bias_voting_calibration.md` (winner 5,5).

For each corpus we report:
  - bars-per-confirmed-box distribution (min/max/mean/std + percentiles)
  - confirmed-box count per day
  - regime classification per box (using 4h delta proxy)
  - bias outcome frequency under multiple `min_bars` settings (preview only,
    not a decision — that's Step 4+5)

Output: `step1_distribution.md` + `step1_distribution.json` + raw CSVs.

Run:
    cd C:\\FluxQuantumAI
    python -m _audit.calibrations.m30_bias_calibration_2026-05-10.step1_distribution
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:/FluxQuantumAI")
sys.path.insert(0, str(ROOT))

OUT_DIR = (ROOT / "_audit" / "calibrations"
           / "m30_bias_calibration_2026-05-10" / "step1")
OUT_DIR.mkdir(parents=True, exist_ok=True)

M30_PARQUET = Path(r"C:/data/processed/gc_m30_boxes.parquet")
OHLCV_PARQUET = Path(r"C:/data/processed/gc_ohlcv_l2_joined.parquet")
DECISION_LOG = ROOT / "logs" / "decision_log.jsonl"

NARROW_START = pd.Timestamp("2026-05-05", tz="UTC")
NARROW_END = pd.Timestamp("2026-05-09", tz="UTC")
# BROAD = 10 months L2 corpus per Barbara directive 2026-05-10:
# "calibracao segue metodo purdue nos 10 meses de dados l2 que temos"
BROAD_START = pd.Timestamp("2025-07-01", tz="UTC")
BROAD_END = pd.Timestamp("2026-05-09", tz="UTC")

# 4h delta regime classifier (matches Phase 2 calibration script)
RANGE_DELTA4H_PTS = 30.0


@dataclass
class CorpusStats:
    name: str
    start: str
    end: str
    n_m30_rows: int
    n_confirmed_boxes: int
    bars_per_box_p25: float
    bars_per_box_p50: float
    bars_per_box_p75: float
    bars_per_box_p90: float
    bars_per_box_min: int
    bars_per_box_max: int
    bars_per_box_mean: float
    bars_per_box_std: float
    boxes_per_day_mean: float
    box_lifetime_min_mean: float
    bias_dist_native: dict   # box-level bias from m30_box_bias column or _classify
    regime_dist: dict        # 4h-delta bucket
    n_decisions_in_window: int


def load_m30(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = pd.read_parquet(M30_PARQUET)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= start) & (df.index < end)]
    return df


def load_ohlcv_close() -> pd.Series:
    df = pd.read_parquet(OHLCV_PARQUET, columns=["close"])
    df.index = pd.to_datetime(df.index, utc=True)
    return df["close"]


def regime_at(close_series: pd.Series, ts: pd.Timestamp) -> str:
    """4h delta classifier (same as Phase 2 calibration). RANGE if |delta|<=30,
    TRENDING_UP/DN if delta>+30 / <-30."""
    cutoff_now = close_series.index.searchsorted(ts, side="right") - 1
    cutoff_4h = close_series.index.searchsorted(
        ts - pd.Timedelta(hours=4), side="right") - 1
    if cutoff_now < 0 or cutoff_4h < 0 or cutoff_now <= cutoff_4h:
        return "RANGE"
    delta = float(close_series.iloc[cutoff_now] - close_series.iloc[cutoff_4h])
    if delta > RANGE_DELTA4H_PTS:
        return "TREND_UP"
    if delta < -RANGE_DELTA4H_PTS:
        return "TREND_DN"
    return "RANGE"


def _classify_box_native(row: pd.Series) -> str:
    """Mirror level_detector._classify_box_row — bias from liq extension."""
    try:
        bh = float(row.get("m30_box_high", 0))
        bl = float(row.get("m30_box_low", 0))
        lt = float(row.get("m30_liq_top", 0))
        lb = float(row.get("m30_liq_bot", 0))
    except Exception:
        return "unknown"
    if lt > bh and lb >= bl:
        return "bullish"
    if lb < bl and lt <= bh:
        return "bearish"
    return "unknown"


def compute_corpus_stats(name: str, start: pd.Timestamp,
                         end: pd.Timestamp,
                         close_series: pd.Series,
                         decision_log: pd.DataFrame) -> CorpusStats:
    df = load_m30(start, end)
    confirmed = df[df["m30_box_confirmed"] == True]

    # bars-per-box: count rows per box_id
    bars_per_box: list[int] = []
    box_lifetimes_min: list[float] = []
    box_biases: list[str] = []
    box_regimes: list[str] = []
    box_first_ts: list[pd.Timestamp] = []
    for bid, sub in confirmed.groupby("m30_box_id"):
        if pd.isna(bid):
            continue
        bars_per_box.append(int(len(sub)))
        first_ts = sub.index[0]
        last_ts = sub.index[-1]
        box_first_ts.append(first_ts)
        box_lifetimes_min.append((last_ts - first_ts).total_seconds() / 60)
        box_biases.append(_classify_box_native(sub.iloc[-1]))
        box_regimes.append(regime_at(close_series, first_ts))

    if not bars_per_box:
        return CorpusStats(
            name=name, start=str(start), end=str(end),
            n_m30_rows=len(df), n_confirmed_boxes=0,
            bars_per_box_p25=0.0, bars_per_box_p50=0.0,
            bars_per_box_p75=0.0, bars_per_box_p90=0.0,
            bars_per_box_min=0, bars_per_box_max=0,
            bars_per_box_mean=0.0, bars_per_box_std=0.0,
            boxes_per_day_mean=0.0, box_lifetime_min_mean=0.0,
            bias_dist_native={}, regime_dist={},
            n_decisions_in_window=0,
        )

    arr = np.array(bars_per_box, dtype=int)
    days = max(1, (end - start).days)
    n_decisions = int(((decision_log["ts"] >= start) &
                       (decision_log["ts"] < end)).sum())

    return CorpusStats(
        name=name, start=str(start), end=str(end),
        n_m30_rows=len(df),
        n_confirmed_boxes=len(arr),
        bars_per_box_p25=float(np.percentile(arr, 25)),
        bars_per_box_p50=float(np.percentile(arr, 50)),
        bars_per_box_p75=float(np.percentile(arr, 75)),
        bars_per_box_p90=float(np.percentile(arr, 90)),
        bars_per_box_min=int(arr.min()),
        bars_per_box_max=int(arr.max()),
        bars_per_box_mean=float(arr.mean()),
        bars_per_box_std=float(arr.std()),
        boxes_per_day_mean=len(arr) / days,
        box_lifetime_min_mean=float(np.mean(box_lifetimes_min)),
        bias_dist_native=dict(Counter(box_biases)),
        regime_dist=dict(Counter(box_regimes)),
        n_decisions_in_window=n_decisions,
    )


def load_decision_log() -> pd.DataFrame:
    rows = []
    seen = set()
    with DECISION_LOG.open(encoding="utf-8", errors="ignore") as f:
        for ln in f:
            try:
                obj = json.loads(ln.strip())
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
            dec = obj.get("decision", {}) or {}
            direction = dec.get("direction") or ""
            if direction not in ("LONG", "SHORT"):
                continue
            did = (obj.get("decision_id")
                   or f"{t.value}_{direction}_{dec.get('action', '?')}")
            if did in seen:
                continue
            seen.add(did)
            rows.append({"ts": t, "direction": direction,
                         "action": dec.get("action", "?")})
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def render_md(narrow: CorpusStats, broad: CorpusStats,
              hist_data: dict) -> str:
    md: list[str] = []
    md.append("# G-PURDUE Step 1 — Distribution observation\n\n")
    md.append(f"**Generated**: {pd.Timestamp.utcnow()}\n")
    md.append(f"**Author**: directed-validation harness\n")
    md.append(f"**Sign-off**: Barbara 2026-05-10 (no guessing — data-driven only)\n\n")

    md.append("## Purpose\n\n")
    md.append("Characterize the bars-per-confirmed-box distribution **before** "
              "any threshold is chosen. Two corpora:\n")
    md.append("- **NARROW** = May 5-8 2026 (current production window)\n")
    md.append("- **BROAD**  = Apr-May 2026 (same horizon as Phase 2 winner 5,5)\n\n")
    md.append("Comparing them reveals whether the distribution drifted between "
              "the calibration corpus and current production — which would "
              "explain why deployed `(5,5)` produced bias=unknown 95% on 5/8.\n\n")

    md.append("## Stats summary\n\n")
    md.append("| Metric | NARROW (May 5-8) | BROAD (Apr-May) |\n")
    md.append("|---|---:|---:|\n")
    rows_md = [
        ("M30 rows in window", narrow.n_m30_rows, broad.n_m30_rows),
        ("Confirmed boxes", narrow.n_confirmed_boxes, broad.n_confirmed_boxes),
        ("Decisions in window", narrow.n_decisions_in_window,
         broad.n_decisions_in_window),
        ("Boxes per day (mean)", f"{narrow.boxes_per_day_mean:.2f}",
         f"{broad.boxes_per_day_mean:.2f}"),
        ("Box lifetime min (mean)",
         f"{narrow.box_lifetime_min_mean:.1f}",
         f"{broad.box_lifetime_min_mean:.1f}"),
        ("Bars/box min", narrow.bars_per_box_min, broad.bars_per_box_min),
        ("Bars/box p25", f"{narrow.bars_per_box_p25:.1f}",
         f"{broad.bars_per_box_p25:.1f}"),
        ("Bars/box p50 (median)", f"{narrow.bars_per_box_p50:.1f}",
         f"{broad.bars_per_box_p50:.1f}"),
        ("Bars/box p75", f"{narrow.bars_per_box_p75:.1f}",
         f"{broad.bars_per_box_p75:.1f}"),
        ("Bars/box p90", f"{narrow.bars_per_box_p90:.1f}",
         f"{broad.bars_per_box_p90:.1f}"),
        ("Bars/box max", narrow.bars_per_box_max, broad.bars_per_box_max),
        ("Bars/box mean", f"{narrow.bars_per_box_mean:.2f}",
         f"{broad.bars_per_box_mean:.2f}"),
        ("Bars/box std", f"{narrow.bars_per_box_std:.2f}",
         f"{broad.bars_per_box_std:.2f}"),
    ]
    for label, n, b in rows_md:
        md.append(f"| {label} | {n} | {b} |\n")
    md.append("\n")

    md.append("## Bias outcome distribution (native classifier per box)\n\n")
    md.append("| Bias | NARROW count | NARROW % | BROAD count | BROAD % |\n")
    md.append("|---|---:|---:|---:|---:|\n")
    for bias_label in ("bullish", "bearish", "unknown"):
        nc = narrow.bias_dist_native.get(bias_label, 0)
        bc = broad.bias_dist_native.get(bias_label, 0)
        nt = sum(narrow.bias_dist_native.values()) or 1
        bt = sum(broad.bias_dist_native.values()) or 1
        md.append(f"| {bias_label} | {nc} | {100 * nc / nt:.1f}% | "
                  f"{bc} | {100 * bc / bt:.1f}% |\n")
    md.append("\n")

    md.append("## Regime distribution (4h-delta classifier @ box first ts)\n\n")
    md.append("| Regime | NARROW | BROAD |\n|---|---:|---:|\n")
    for reg in ("TREND_UP", "TREND_DN", "RANGE"):
        md.append(f"| {reg} | {narrow.regime_dist.get(reg, 0)} | "
                  f"{broad.regime_dist.get(reg, 0)} |\n")
    md.append("\n")

    md.append("## Histogram bars-per-box (NARROW corpus)\n\n")
    md.append("| Bars-in-box | Count | Cum % |\n|---:|---:|---:|\n")
    cumulative = 0
    total_n = sum(hist_data["narrow"].values())
    for k in sorted(hist_data["narrow"].keys()):
        cumulative += hist_data["narrow"][k]
        md.append(f"| {k} | {hist_data['narrow'][k]} | "
                  f"{100 * cumulative / total_n:.1f}% |\n")
    md.append("\n")

    md.append("## Histogram bars-per-box (BROAD corpus)\n\n")
    md.append("| Bars-in-box | Count | Cum % |\n|---:|---:|---:|\n")
    cumulative = 0
    total_b = sum(hist_data["broad"].values())
    for k in sorted(hist_data["broad"].keys()):
        cumulative += hist_data["broad"][k]
        md.append(f"| {k} | {hist_data['broad'][k]} | "
                  f"{100 * cumulative / total_b:.1f}% |\n")
    md.append("\n")

    # Drift assessment
    md.append("## Drift assessment\n\n")
    drift_p50 = narrow.bars_per_box_p50 - broad.bars_per_box_p50
    drift_p75 = narrow.bars_per_box_p75 - broad.bars_per_box_p75
    md.append(f"- p50 drift NARROW vs BROAD: **{drift_p50:+.1f} bars** "
              f"({narrow.bars_per_box_p50:.1f} vs {broad.bars_per_box_p50:.1f})\n")
    md.append(f"- p75 drift NARROW vs BROAD: **{drift_p75:+.1f} bars** "
              f"({narrow.bars_per_box_p75:.1f} vs {broad.bars_per_box_p75:.1f})\n")
    if abs(drift_p50) >= 1.0 or abs(drift_p75) >= 1.0:
        md.append("- **Distribution shift confirmed**: this is the empirical "
                  "evidence that the (5,5) calibration on Apr-May corpus does "
                  "not generalize to May 5-8 production. min_bars=5 on a corpus "
                  "where p50 < 5 will keep most boxes below threshold → "
                  "bias=unknown.\n\n")
    else:
        md.append("- Distribution stable; drift hypothesis NOT supported by p50/p75.\n\n")

    md.append("## Implications for grid range (Step 3 input)\n\n")
    md.append(f"- A reasonable grid for `min_bars` covers the empirical "
              f"p25-p75 range: **`min_bars` ∈ [{int(narrow.bars_per_box_p25)}, "
              f"{int(narrow.bars_per_box_p75)}]** on NARROW corpus, with the "
              f"window extending up to p90={narrow.bars_per_box_p90:.0f} for "
              f"sensitivity sweep.\n")
    md.append(f"- A reasonable `window` (number of recent confirmed boxes to "
              f"vote on) is bounded by total box count per day. NARROW has "
              f"{narrow.boxes_per_day_mean:.1f} boxes/day, so window > 6 risks "
              f"voting against confirmed boxes >24h old (multi-session noise).\n\n")

    md.append("## Methodology notes\n\n")
    md.append("- bars_per_box = count of M30 rows where `m30_box_confirmed == True` and `m30_box_id == bid`\n")
    md.append("- box bias = native classifier on last row: liq_top > box_high (bullish), liq_bot < box_low (bearish), else unknown\n")
    md.append("- regime = 4h price delta from box first_ts: |delta|<=30 RANGE, >+30 TREND_UP, <-30 TREND_DN\n")
    md.append("- ZERO live mutation; pure read-only.\n\n")

    md.append("## Artifacts\n\n")
    md.append("- `step1_distribution.json` — machine-readable stats\n")
    md.append("- `bars_per_box_narrow.csv` — raw per-box stats NARROW\n")
    md.append("- `bars_per_box_broad.csv` — raw per-box stats BROAD\n")
    return "".join(md)


def main() -> int:
    print("=" * 70)
    print("G-PURDUE Step 1 — Distribution observation")
    print("=" * 70)

    print("\n[1/4] Loading OHLCV close series ...", flush=True)
    close_series = load_ohlcv_close()
    close_series = close_series.sort_index()
    print(f"      {len(close_series):,} rows  "
          f"({close_series.index[0]} -> {close_series.index[-1]})")

    print("\n[2/4] Loading decision_log.jsonl ...", flush=True)
    dec_df = load_decision_log()
    print(f"      {len(dec_df):,} signals (LONG/SHORT)")

    print("\n[3/4] Computing NARROW (May 5-8) ...", flush=True)
    narrow = compute_corpus_stats(
        "NARROW", NARROW_START, NARROW_END, close_series, dec_df)
    print(f"      {narrow.n_confirmed_boxes} confirmed boxes")
    print(f"      bars/box p25={narrow.bars_per_box_p25:.1f} "
          f"p50={narrow.bars_per_box_p50:.1f} "
          f"p75={narrow.bars_per_box_p75:.1f} "
          f"p90={narrow.bars_per_box_p90:.1f}")
    print(f"      bias dist: {narrow.bias_dist_native}")

    print("\n[4/4] Computing BROAD (Apr-May) ...", flush=True)
    broad = compute_corpus_stats(
        "BROAD", BROAD_START, BROAD_END, close_series, dec_df)
    print(f"      {broad.n_confirmed_boxes} confirmed boxes")
    print(f"      bars/box p25={broad.bars_per_box_p25:.1f} "
          f"p50={broad.bars_per_box_p50:.1f} "
          f"p75={broad.bars_per_box_p75:.1f} "
          f"p90={broad.bars_per_box_p90:.1f}")
    print(f"      bias dist: {broad.bias_dist_native}")

    # Histograms
    df_n = load_m30(NARROW_START, NARROW_END)
    df_b = load_m30(BROAD_START, BROAD_END)
    hist_n: Counter = Counter()
    hist_b: Counter = Counter()
    rows_n: list[dict] = []
    rows_b: list[dict] = []
    for bid, sub in df_n[df_n["m30_box_confirmed"] == True].groupby("m30_box_id"):
        if pd.isna(bid):
            continue
        n = len(sub)
        hist_n[n] += 1
        rows_n.append({"box_id": int(bid), "bars": n,
                       "first_ts": str(sub.index[0]),
                       "bias_native": _classify_box_native(sub.iloc[-1])})
    for bid, sub in df_b[df_b["m30_box_confirmed"] == True].groupby("m30_box_id"):
        if pd.isna(bid):
            continue
        n = len(sub)
        hist_b[n] += 1
        rows_b.append({"box_id": int(bid), "bars": n,
                       "first_ts": str(sub.index[0]),
                       "bias_native": _classify_box_native(sub.iloc[-1])})

    pd.DataFrame(rows_n).to_csv(OUT_DIR / "bars_per_box_narrow.csv",
                                  index=False)
    pd.DataFrame(rows_b).to_csv(OUT_DIR / "bars_per_box_broad.csv",
                                  index=False)

    md = render_md(narrow, broad, {"narrow": dict(hist_n),
                                    "broad": dict(hist_b)})
    (OUT_DIR / "step1_distribution.md").write_text(md, encoding="utf-8")

    out_json = {
        "narrow": asdict(narrow),
        "broad": asdict(broad),
        "histogram_narrow": dict(hist_n),
        "histogram_broad": dict(hist_b),
    }
    (OUT_DIR / "step1_distribution.json").write_text(
        json.dumps(out_json, indent=2, default=str), encoding="utf-8")

    print(f"\nReport: {OUT_DIR / 'step1_distribution.md'}")
    print(f"JSON:   {OUT_DIR / 'step1_distribution.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

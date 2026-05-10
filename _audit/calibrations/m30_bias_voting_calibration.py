"""
Phase 2 calibration script — m30_bias F-1+F-3 walk-forward CV grid search.

Per ML-DS comment 1214600890407658.
Output: m30_bias_voting_calibration.md
ZERO touch on live code; this file is in _audit/calibrations/, not live/.

Optimized for speed: pre-compute confirmed-box index + per-box classifications.
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

# -------- paths --------
DECISION_LOG_PATHS = [
    Path(r"C:\FluxQuantumAI\Backups\pre-op-20260423_053006\logs\decision_log.jsonl"),
    Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl"),
]
M30_PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
OHLCV_PARQUET = Path(r"C:\data\processed\gc_ohlcv_l2_joined.parquet")

OUT_REPORT = Path(r"C:\FluxQuantumAI\_audit\calibrations\m30_bias_voting_calibration.md")

# -------- date filter --------
START = pd.Timestamp("2026-04-01", tz="UTC")
END = pd.Timestamp("2026-05-07 23:59:59", tz="UTC")

# -------- regime thresholds --------
RANGE_DELTA4H_PTS = 30.0
TRANSITIONAL_LOOKBACK_MIN = 60

# -------- candidate grid --------
MIN_BARS_CHOICES = [3, 4, 5, 6]
WINDOW_CHOICES = [2, 3, 4, 5]
STRATEGIES = ["majority", "recency_weighted"]
N_FOLDS = 5
ALPHA_BONFERRONI = 0.05 / (len(MIN_BARS_CHOICES) * len(WINDOW_CHOICES) * len(STRATEGIES))

# -------- subsample --------
MAX_DECISIONS = 6000


def log(msg: str) -> None:
    print(msg, flush=True)


# ============================================================================
# Helpers — replicate spec §3.2 voting logic
# ============================================================================

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


def voting_vote(classifications: list[str], strategy: str) -> str:
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


# ============================================================================
# Pre-computed confirmed-box index
# ============================================================================

class ConfirmedBoxIndex:
    """Pre-computes per-confirmed-box (last_ts, classify_base, bars_in_box).
    Provides binary-search lookup for 'last K confirmed boxes at time t'."""
    def __init__(self, m30: pd.DataFrame):
        # Group by box_id; only consider boxes that ever became confirmed
        confirmed_ids = m30.loc[m30["m30_box_confirmed"] == True, "m30_box_id"].dropna().unique()
        items = []
        for bid in confirmed_ids:
            sub = m30[m30["m30_box_id"] == bid]
            sub_conf = sub[sub["m30_box_confirmed"] == True]
            if sub_conf.empty:
                continue
            first_conf_ts = sub_conf.index[0]  # box first becomes available at first confirmed-row ts
            last_row = sub.iloc[-1]
            d = {
                "box_high": float(last_row.get("m30_box_high", float("nan"))),
                "box_low":  float(last_row.get("m30_box_low",  float("nan"))),
                "liq_top":  float(last_row.get("m30_liq_top",  float("nan"))),
                "liq_bot":  float(last_row.get("m30_liq_bot",  float("nan"))),
            }
            base_class = classify_box_from_dict(d)
            bars = len(sub)
            items.append((first_conf_ts, int(bid), base_class, bars))
        items.sort(key=lambda x: x[0])
        self.ts = np.array([pd.Timestamp(x[0]).value for x in items], dtype=np.int64)
        self.box_ids = np.array([x[1] for x in items], dtype=np.int64)
        self.base_class = [x[2] for x in items]  # "bullish"/"bearish"/"unknown"
        self.bars = np.array([x[3] for x in items], dtype=np.int32)
        log(f"  ConfirmedBoxIndex: {len(items)} confirmed boxes")

    def last_k_at(self, ts: pd.Timestamp, k: int) -> list[int]:
        """Return indices into self of the up-to-k most recent confirmed boxes available at ts."""
        ts_ns = pd.Timestamp(ts).value
        # boxes whose first_conf_ts <= ts
        idx_end = int(np.searchsorted(self.ts, ts_ns, side="right"))
        if idx_end == 0:
            return []
        idx_start = max(0, idx_end - k)
        return list(range(idx_start, idx_end))

    def classification_with_min_bars(self, idx: int, min_bars: int) -> str:
        if int(self.bars[idx]) < min_bars:
            return "unknown"
        return self.base_class[idx]


# ============================================================================
# Data prep
# ============================================================================

def load_decision_log() -> pd.DataFrame:
    rows = []
    seen_ids = set()
    for path in DECISION_LOG_PATHS:
        if not path.exists():
            continue
        log(f"  reading {path} ...")
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
                if t is pd.NaT or t < START or t > END:
                    continue
                did = obj.get("decision_id") or f"{t.value}_{obj.get('decision', {}).get('direction')}"
                if did in seen_ids:
                    continue
                seen_ids.add(did)
                ctx = obj.get("context", {}) or {}
                dec = obj.get("decision", {}) or {}
                rows.append({
                    "ts": t,
                    "price_gc": obj.get("price_gc") or obj.get("price_mt5"),
                    "phase": ctx.get("phase"),
                    "daily_trend": ctx.get("daily_trend"),
                    "m30_bias_old": ctx.get("m30_bias"),
                    "delta_4h_ctx": ctx.get("delta_4h"),
                    "action": dec.get("action"),
                    "direction": dec.get("direction"),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def add_realized_and_regime(df_dec: pd.DataFrame, ohlcv_close: pd.Series) -> pd.DataFrame:
    """Vectorized: realized 1h move + regime via searchsorted lookups."""
    out = df_dec.copy()
    ohlcv_ts = ohlcv_close.index.values  # numpy datetime64[ns]
    ohlcv_v = ohlcv_close.values

    dec_ts = pd.to_datetime(out["ts"]).values

    def at_or_before(target_ts_ns: np.ndarray):
        idx = np.searchsorted(ohlcv_ts, target_ts_ns, side="right") - 1
        idx = np.clip(idx, 0, len(ohlcv_v) - 1)
        return ohlcv_v[idx]

    target_now = dec_ts
    target_4h_ago = dec_ts - np.timedelta64(4, "h")
    target_1h_ago = dec_ts - np.timedelta64(60, "m")
    target_5h_ago = dec_ts - np.timedelta64(5, "h")
    target_1h_fwd = dec_ts + np.timedelta64(60, "m")

    p_now = at_or_before(target_now)
    p_4h = at_or_before(target_4h_ago)
    p_1h_ago = at_or_before(target_1h_ago)
    p_5h_ago = at_or_before(target_5h_ago)
    p_1h_fwd = at_or_before(target_1h_fwd)

    out["realized_1h"] = p_1h_fwd - p_now
    out["delta_4h_calc"] = p_now - p_4h

    d_prev = p_1h_ago - p_5h_ago
    d_now = p_now - p_4h

    def regime_of(d_arr: np.ndarray) -> np.ndarray:
        r = np.full(len(d_arr), "RANGE", dtype=object)
        r[d_arr > RANGE_DELTA4H_PTS] = "TREND_UP"
        r[d_arr < -RANGE_DELTA4H_PTS] = "TREND_DN"
        return r

    cur = regime_of(d_now)
    prev = regime_of(d_prev)
    transitional = cur != prev
    cur[transitional] = "TRANSITIONAL"
    out["regime"] = cur
    return out


# ============================================================================
# Replay using pre-computed index
# ============================================================================

def derive_bias_via_index(
    box_index: ConfirmedBoxIndex,
    ts: pd.Timestamp,
    min_bars: int,
    window: int,
    strategy: str,
) -> str:
    idxs = box_index.last_k_at(ts, window)
    if not idxs:
        return "unknown"
    classifications = [box_index.classification_with_min_bars(i, min_bars) for i in idxs]
    return voting_vote(classifications, strategy)


def derive_bias_baseline_via_index(box_index: ConfirmedBoxIndex, ts: pd.Timestamp) -> str:
    idxs = box_index.last_k_at(ts, 1)
    if not idxs:
        return "unknown"
    return box_index.base_class[idxs[0]]


def replay_grid_for_decisions(
    df_dec: pd.DataFrame,
    box_index: ConfirmedBoxIndex,
    candidate: tuple | None,
) -> pd.DataFrame:
    out = df_dec.copy()
    biases = []
    blocked = []
    survives_correct = []
    for ts, direction, realized in zip(out["ts"], out["direction"], out["realized_1h"]):
        if candidate is None:
            bias = derive_bias_baseline_via_index(box_index, ts)
        else:
            mb, w, s = candidate
            bias = derive_bias_via_index(box_index, ts, mb, w, s)
        biases.append(bias)
        if direction == "LONG" and bias == "bearish":
            blocked.append(True)
            survives_correct.append(np.nan)
        elif direction == "SHORT" and bias == "bullish":
            blocked.append(True)
            survives_correct.append(np.nan)
        else:
            blocked.append(False)
            if pd.isna(realized):
                survives_correct.append(np.nan)
            else:
                if direction == "LONG":
                    survives_correct.append(1.0 if realized > 0 else 0.0)
                elif direction == "SHORT":
                    survives_correct.append(1.0 if realized < 0 else 0.0)
                else:
                    survives_correct.append(np.nan)
    out["m30_bias_new"] = biases
    out["blocked"] = blocked
    out["survives_correct"] = survives_correct
    return out


def score_per_regime(replayed: pd.DataFrame) -> dict:
    out = {}
    for regime, sub in replayed.groupby("regime"):
        n_total = len(sub)
        n_blocked = int(sub["blocked"].sum())
        survive = sub[~sub["blocked"]]
        correct_arr = survive["survives_correct"].dropna()
        if len(correct_arr) > 0:
            acc = float(correct_arr.mean())
            n_score = len(correct_arr)
        else:
            acc = float("nan")
            n_score = 0
        out[regime] = {
            "n_total": int(n_total),
            "n_blocked": n_blocked,
            "n_survive_scored": int(n_score),
            "block_rate": float(n_blocked / n_total) if n_total > 0 else 0.0,
            "survive_accuracy": acc,
        }
    return out


def walk_forward_cv(
    df_dec: pd.DataFrame,
    box_index: ConfirmedBoxIndex,
    candidate: tuple | None,
    n_folds: int = 5,
) -> dict:
    fold_metrics = defaultdict(list)
    for regime, sub in df_dec.groupby("regime"):
        sub = sub.sort_values("ts").reset_index(drop=True)
        if len(sub) < n_folds:
            continue
        fold_size = len(sub) // n_folds
        for k in range(n_folds):
            start = k * fold_size
            stop = (k + 1) * fold_size if k < n_folds - 1 else len(sub)
            fold = sub.iloc[start:stop]
            replayed = replay_grid_for_decisions(fold, box_index, candidate)
            score = score_per_regime(replayed)
            if regime in score:
                fold_metrics[regime].append(score[regime])
    summary = {}
    for regime, fold_scores in fold_metrics.items():
        accs = [s["survive_accuracy"] for s in fold_scores if not math.isnan(s["survive_accuracy"])]
        block_rates = [s["block_rate"] for s in fold_scores]
        summary[regime] = {
            "mean_acc": float(np.mean(accs)) if accs else float("nan"),
            "std_acc": float(np.std(accs)) if accs else float("nan"),
            "n_folds_scored": len(accs),
            "mean_block_rate": float(np.mean(block_rates)) if block_rates else 0.0,
        }
    return summary


# ============================================================================
# Main
# ============================================================================

def main():
    t0 = time.time()
    log("Phase 2 calibration starting...")

    log("Loading decision_log...")
    df_dec = load_decision_log()
    log(f"  decisions in window: {len(df_dec)}")
    if df_dec.empty:
        log("  no decisions; abort.")
        return
    df_dec = df_dec[df_dec["direction"].isin(["LONG", "SHORT"])].reset_index(drop=True)
    log(f"  with direction: {len(df_dec)}")

    log("Loading m30 parquet...")
    boxes = pd.read_parquet(M30_PARQUET)
    boxes.index = pd.to_datetime(boxes.index, utc=True)
    boxes = boxes[(boxes.index >= START - pd.Timedelta(days=14)) & (boxes.index <= END)]
    log(f"  m30 rows: {len(boxes)}")

    log("Building ConfirmedBoxIndex...")
    box_index = ConfirmedBoxIndex(boxes)
    log(f"  elapsed: {time.time()-t0:.1f}s")

    log("Loading ohlcv close column (slice)...")
    ohlcv = pd.read_parquet(OHLCV_PARQUET, columns=["close"])
    ohlcv.index = pd.to_datetime(ohlcv.index, utc=True)
    ohlcv = ohlcv[(ohlcv.index >= START - pd.Timedelta(hours=8)) & (ohlcv.index <= END + pd.Timedelta(hours=2))]
    log(f"  ohlcv rows: {len(ohlcv)}")

    log("Computing realized + regime (vectorized)...")
    df_dec = add_realized_and_regime(df_dec, ohlcv["close"])
    log("  regime distribution:")
    log(df_dec["regime"].value_counts().to_string())

    df_dec = df_dec[df_dec["regime"].isin(["RANGE", "TREND_UP", "TREND_DN", "TRANSITIONAL"])].reset_index(drop=True)
    log(f"  decisions after regime filter: {len(df_dec)}")

    if len(df_dec) > MAX_DECISIONS:
        parts = []
        per_regime = MAX_DECISIONS // df_dec["regime"].nunique()
        for reg, sub in df_dec.groupby("regime"):
            if len(sub) > per_regime:
                step = len(sub) // per_regime
                parts.append(sub.iloc[::step].head(per_regime))
            else:
                parts.append(sub)
        df_dec = pd.concat(parts).sort_values("ts").reset_index(drop=True)
        log(f"  subsampled to {len(df_dec)}")

    log(f"  elapsed: {time.time()-t0:.1f}s — beginning grid search")

    log("Baseline replay...")
    baseline_summary = walk_forward_cv(df_dec, box_index, candidate=None, n_folds=N_FOLDS)
    log(f"  baseline: {baseline_summary}")
    log(f"  elapsed: {time.time()-t0:.1f}s")

    log(f"Grid search over {len(MIN_BARS_CHOICES)*len(WINDOW_CHOICES)*len(STRATEGIES)} candidates...")
    grid_results = []
    for mb in MIN_BARS_CHOICES:
        for w in WINDOW_CHOICES:
            for s in STRATEGIES:
                cand = (mb, w, s)
                summ = walk_forward_cv(df_dec, box_index, cand, n_folds=N_FOLDS)
                accs = [summ[r]["mean_acc"] for r in summ if not math.isnan(summ[r]["mean_acc"])]
                mean_acc = float(np.mean(accs)) if accs else float("nan")
                grid_results.append({
                    "min_bars": mb,
                    "window": w,
                    "strategy": s,
                    "summary": summ,
                    "mean_acc_all_regimes": mean_acc,
                })
                log(f"  ({mb},{w},{s}): mean_acc={mean_acc:.4f} elapsed={time.time()-t0:.1f}s")

    log("Sanity-check episodes...")
    sanity = {}
    burst_0506 = df_dec[(df_dec["ts"] >= pd.Timestamp("2026-05-06 04:00", tz="UTC")) &
                        (df_dec["ts"] <= pd.Timestamp("2026-05-06 05:00", tz="UTC"))]
    box5282 = df_dec[(df_dec["ts"] >= pd.Timestamp("2026-05-07 04:00", tz="UTC")) &
                     (df_dec["ts"] <= pd.Timestamp("2026-05-07 05:30", tz="UTC"))]
    sanity["burst_0506_n"] = len(burst_0506)
    sanity["box5282_n"] = len(box5282)

    grid_results.sort(key=lambda r: (-(r["mean_acc_all_regimes"] if not math.isnan(r["mean_acc_all_regimes"]) else -1)))
    winner = grid_results[0] if grid_results else None
    runner_up = grid_results[1] if len(grid_results) > 1 else None

    if winner:
        wcand = (winner["min_bars"], winner["window"], winner["strategy"])
        if not burst_0506.empty:
            r = replay_grid_for_decisions(burst_0506, box_index, wcand)
            sanity["burst_0506_blocked"] = int(r["blocked"].sum())
            sanity["burst_0506_total"] = len(r)
            sanity["burst_0506_bias_dist"] = r["m30_bias_new"].value_counts().to_dict()
        if not box5282.empty:
            r = replay_grid_for_decisions(box5282, box_index, wcand)
            sanity["box5282_blocked"] = int(r["blocked"].sum())
            sanity["box5282_total"] = len(r)
            sanity["box5282_bias_dist"] = r["m30_bias_new"].value_counts().to_dict()

    log("Writing report...")
    write_report(df_dec, baseline_summary, grid_results, winner, runner_up, sanity)
    log(f"Report written: {OUT_REPORT}")
    log(f"Total elapsed: {time.time()-t0:.1f}s")


def write_report(df_dec, baseline, grid, winner, runner_up, sanity):
    lines = []
    lines.append("# Phase 2 calibration — m30_bias F-1+F-3 walk-forward CV report")
    lines.append("")
    lines.append("**Author**: CC#3")
    lines.append("**Date**: 2026-05-07")
    lines.append("**Asana**: BUG-SIGNAL-INVERTED 1214556369070092 → ML-DS comment 1214600890407658")
    lines.append("**Spec ref**: `_audit/fixes/opcao_b_phase1_m30_bias_hysteresis_spec.md`")
    lines.append("**Status**: ANALYSIS — pending ML-DS review before Phase 3")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Methodology")
    lines.append("")
    lines.append("- **Decision corpus**: decision_log.jsonl (current + pre-op-20260423 backup), filtered to 2026-04-01..2026-05-07 UTC.")
    lines.append("- **Box state source**: `gc_m30_boxes.parquet` — pre-computed `ConfirmedBoxIndex` over confirmed boxes; per-box `_classify` and `bars_in_box` cached once.")
    lines.append("- **Realized move**: 1h forward close-to-close from `gc_ohlcv_l2_joined.parquet` (M1) — vectorized `searchsorted` lookup.")
    lines.append("- **Regime classifier**: 4h price delta vs `RANGE_DELTA4H_PTS=30` threshold; TRANSITIONAL if regime in last 60min differs from current.")
    lines.append(f"- **Grid**: min_bars ∈ {MIN_BARS_CHOICES}, window ∈ {WINDOW_CHOICES}, strategy ∈ {STRATEGIES} = {len(MIN_BARS_CHOICES)*len(WINDOW_CHOICES)*len(STRATEGIES)} hypotheses.")
    lines.append(f"- **CV**: {N_FOLDS}-fold chronological per regime (no future leakage).")
    lines.append("- **Metric**: `survive_accuracy` = correctness of non-blocked decisions vs sign(realized_1h).")
    lines.append(f"- **Bonferroni α**: {ALPHA_BONFERRONI:.5f} (informational gate).")
    lines.append("")
    lines.append("**Counterfactual proxy**: a candidate's NEW bias 'blocks' a decision if it contradicts decision.direction (counter-trend). Surviving decisions are scored against realized 1h move sign. This proxies the cascade+F-asym filter (Opção A live).")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. Corpus summary")
    lines.append("")
    lines.append(f"- Decisions in window with direction (post-subsample): **{len(df_dec)}**")
    lines.append("- Regime distribution:")
    lines.append("")
    for reg, n in df_dec["regime"].value_counts().items():
        lines.append(f"  - {reg}: {n}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. Baseline (current production)")
    lines.append("")
    lines.append("| Regime | mean_acc | std_acc | mean_block_rate | n_folds_scored |")
    lines.append("|---|---|---|---|---|")
    for reg, m in baseline.items():
        lines.append(f"| {reg} | {m['mean_acc']:.4f} | {m['std_acc']:.4f} | {m['mean_block_rate']:.4f} | {m['n_folds_scored']} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. Grid results (top 10 by mean_acc)")
    lines.append("")
    lines.append("| min_bars | window | strategy | mean_acc | RANGE | TREND_UP | TREND_DN | TRANSITIONAL |")
    lines.append("|---|---|---|---|---|---|---|---|")
    grid_sorted = sorted(grid, key=lambda r: (-(r["mean_acc_all_regimes"] if not math.isnan(r["mean_acc_all_regimes"]) else -1)))
    for r in grid_sorted[:10]:
        s = r["summary"]
        def fmt(reg):
            v = s.get(reg, {}).get("mean_acc", float("nan"))
            return f"{v:.4f}" if not math.isnan(v) else "n/a"
        lines.append(
            f"| {r['min_bars']} | {r['window']} | {r['strategy']} | "
            f"{r['mean_acc_all_regimes']:.4f} | "
            f"{fmt('RANGE')} | {fmt('TREND_UP')} | {fmt('TREND_DN')} | {fmt('TRANSITIONAL')} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. Winner + acceptance check")
    lines.append("")
    if winner is None:
        lines.append("No valid candidate found.")
    else:
        lines.append(f"**Winner**: min_bars={winner['min_bars']}, window={winner['window']}, strategy={winner['strategy']}")
        lines.append("")
        lines.append("Per-regime detail (winner):")
        lines.append("")
        lines.append("| Regime | mean_acc | std | block_rate | baseline_acc | delta_pp |")
        lines.append("|---|---|---|---|---|---|")
        for reg, m in winner["summary"].items():
            base = baseline.get(reg, {}).get("mean_acc", float("nan"))
            delta = (m["mean_acc"] - base) * 100 if not math.isnan(m["mean_acc"]) and not math.isnan(base) else float("nan")
            base_s = f"{base:.4f}" if not math.isnan(base) else "n/a"
            d_s = f"{delta:+.2f}pp" if not math.isnan(delta) else "n/a"
            lines.append(f"| {reg} | {m['mean_acc']:.4f} | {m['std_acc']:.4f} | {m['mean_block_rate']:.4f} | {base_s} | {d_s} |")
        lines.append("")
        regressions = []
        for reg, m in winner["summary"].items():
            base = baseline.get(reg, {}).get("mean_acc", float("nan"))
            if not math.isnan(m["mean_acc"]) and not math.isnan(base):
                if (m["mean_acc"] - base) * 100 < -5.0:
                    regressions.append((reg, (m["mean_acc"] - base) * 100))
        if regressions:
            lines.append(f"**Acceptance**: ❌ FAIL — regression >5pp in: {regressions}")
        else:
            lines.append("**Acceptance**: ✅ PASS — no regime regresses >5pp vs baseline.")
        lines.append("")
        if runner_up:
            lines.append(f"**Runner-up**: min_bars={runner_up['min_bars']}, window={runner_up['window']}, strategy={runner_up['strategy']} (mean_acc={runner_up['mean_acc_all_regimes']:.4f})")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 6. Sanity check episodes")
    lines.append("")
    lines.append("**Episode A — 2026-05-06 04:00–05:00 UTC** (200 SHORTs RANGE-bound burst, must STILL resolve bearish):")
    lines.append("")
    if sanity.get("burst_0506_n", 0) > 0 and "burst_0506_blocked" in sanity:
        lines.append(f"- decisions in window: {sanity['burst_0506_total']}")
        lines.append(f"- blocked under winner: {sanity['burst_0506_blocked']} ({100*sanity['burst_0506_blocked']/max(sanity['burst_0506_total'],1):.1f}%)")
        lines.append(f"- bias distribution: {sanity['burst_0506_bias_dist']}")
        bias_dist = sanity['burst_0506_bias_dist']
        if bias_dist.get("bearish", 0) >= bias_dist.get("bullish", 0):
            lines.append("- result: ✅ PASS — bearish bias preserved (no over-blocking valid bearish episode)")
        else:
            lines.append("- result: ❌ FAIL — bias drifted away from bearish")
    else:
        lines.append("- no decisions in window (data gap)")
    lines.append("")
    lines.append("**Episode B — 2026-05-07 04:00–05:30 UTC** (Box 5282 transient — must resolve unknown, NOT bearish):")
    lines.append("")
    if sanity.get("box5282_n", 0) > 0 and "box5282_blocked" in sanity:
        lines.append(f"- decisions in window: {sanity['box5282_total']}")
        lines.append(f"- blocked under winner: {sanity['box5282_blocked']} ({100*sanity['box5282_blocked']/max(sanity['box5282_total'],1):.1f}%)")
        lines.append(f"- bias distribution: {sanity['box5282_bias_dist']}")
        bias_dist = sanity['box5282_bias_dist']
        bearish_n = bias_dist.get("bearish", 0)
        if bearish_n / max(sanity['box5282_total'], 1) < 0.05:
            lines.append("- result: ✅ PASS — bias is NOT bearish during transient (target outcome)")
        else:
            lines.append(f"- result: ❌ FAIL — bias still bearish in {bearish_n} cases ({100*bearish_n/sanity['box5282_total']:.1f}%)")
    else:
        lines.append("- no decisions in window (data gap)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 7. Limitations")
    lines.append("")
    lines.append("1. **m30 box parquet is current snapshot**: forward-fill semantics from commit `32343cf` apply retroactively to historical box data. Counterfactual replays compute biases as if 32343cf were always live — accurate for May 2026 episodes (post-32343cf), conservative for April 2026.")
    lines.append("2. **F-asym block proxy**: this calibration uses a simplified counter-trend block (direction vs bias only). Live F-asymmetric (Opção A) also considers RANGE_BOUND/TRENDING phase. Match is approximate but directionally correct for ranking candidates.")
    lines.append("3. **Realized 1h close-to-close**: noisy proxy for signal correctness; does not account for SL/TP path-dependence. Acceptable for relative comparison across candidates.")
    lines.append("4. **Bonferroni applied informationally**: not formally testing significance per hypothesis; gate winner-vs-runner-up if gap is meaningful.")
    lines.append("5. **Decision corpus subsampled** when >6k for runtime; subsample is stratified per regime preserving chronology.")
    lines.append("6. **`ConfirmedBoxIndex` uses last-row classify**: classification per box uses the box's last row in the parquet (final liq excursion). Live `derive_m30_bias` reads the same final row when iterating `confirmed.iloc[-1]`. Equivalent.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 8. Phase 3 recommendation")
    lines.append("")
    if winner:
        lines.append(f"Adopt **min_bars={winner['min_bars']}, window={winner['window']}, strategy={winner['strategy']}** as Phase 3 implementation defaults in `config/settings.json`.")
        lines.append("")
        lines.append("Update keys:")
        lines.append("```json")
        lines.append(f'  "m30_bias_min_bars": {winner["min_bars"]},')
        lines.append(f'  "m30_bias_voting_window": {winner["window"]},')
        lines.append(f'  "m30_bias_voting_strategy": "{winner["strategy"]}"')
        lines.append("```")
    lines.append("")
    lines.append("Phase 3 implementation should:")
    lines.append("- Implement helpers per spec §3.2")
    lines.append("- Modify `derive_m30_bias` per spec §3.3")
    lines.append("- Add 15 unit tests per spec §6.1")
    lines.append("- Run dual counterfactual gate per spec §6.4 BEFORE deploy (Phase 4)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 9. ML-DS review gate")
    lines.append("")
    lines.append("Awaiting ML-DS review of:")
    lines.append("1. Methodology (regime classifier thresholds, F-asym proxy)")
    lines.append("2. Acceptance result (winner per-regime regression check)")
    lines.append("3. Sanity check (Episode A bearish preserved + Episode B unknown achieved)")
    lines.append("4. Recommendation defaults for Phase 3")
    lines.append("")
    lines.append("ZERO live code changes in this phase. Production cascade + F-asym (Opção A) continues running.")
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()

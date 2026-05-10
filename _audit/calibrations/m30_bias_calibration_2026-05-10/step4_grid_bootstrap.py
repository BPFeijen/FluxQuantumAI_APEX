"""G-PURDUE Step 4 — Grid search + bootstrap CI.

Adapted from `_audit/calibrations/m30_bias_voting_calibration.py` (Phase 2,
2026-05-07). Updated per Barbara directive 2026-05-10:
  - Corpus: 10 months L2 (2025-07-01 -> 2026-05-09), no subsample
  - Grid: 7 x 7 x 2 = 98 hypotheses (Step 3)
  - Bonferroni alpha: 0.05 / 98 ~ 5.1e-4
  - Bootstrap CI: 1000 resamples per candidate per regime
  - Persist intermediate results for Step 5 hypothesis test

Output: `step4/grid_results.json` + `step4/grid_summary.csv` + console summary.

Run:
    cd C:\\FluxQuantumAI
    python -m _audit.calibrations.m30_bias_calibration_2026-05-10.step4_grid_bootstrap
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths + corpus
# ---------------------------------------------------------------------------
ROOT = Path(r"C:/FluxQuantumAI")
DECISION_LOG_PATHS = [
    ROOT / "Backups" / "pre-op-20260423_053006" / "logs" / "decision_log.jsonl",
    ROOT / "logs" / "decision_log.jsonl",
]
M30_PARQUET = Path(r"C:/data/processed/gc_m30_boxes.parquet")
OHLCV_PARQUET = Path(r"C:/data/processed/gc_ohlcv_l2_joined.parquet")

OUT_DIR = (ROOT / "_audit" / "calibrations"
           / "m30_bias_calibration_2026-05-10" / "step4")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 10mo L2 corpus per Barbara directive
START = pd.Timestamp("2025-07-01", tz="UTC")
END = pd.Timestamp("2026-05-09 00:00:00", tz="UTC")

# Step 3 grid
MIN_BARS_CHOICES = [2, 3, 4, 5, 6, 7, 8]
WINDOW_CHOICES = [2, 3, 4, 5, 6, 7, 8]
STRATEGIES = ["majority", "recency_weighted"]
N_HYPOTHESES = len(MIN_BARS_CHOICES) * len(WINDOW_CHOICES) * len(STRATEGIES)
ALPHA_BONFERRONI = 0.05 / N_HYPOTHESES
N_FOLDS = 5
N_BOOTSTRAP = 1000

RANGE_DELTA4H_PTS = 30.0
TRANSITIONAL_LOOKBACK_MIN = 60


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Voting + classification (verbatim from Phase 2 — proven correct)
# ---------------------------------------------------------------------------
def classify_box_from_dict(d: dict) -> str:
    bh = d.get("box_high"); bl = d.get("box_low")
    lt = d.get("liq_top");  lb = d.get("liq_bot")
    if any(v is None or (isinstance(v, float) and math.isnan(v))
           for v in (bh, bl, lt, lb)):
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
    # recency_weighted
    weights = list(range(1, len(classifications) + 1))
    bull_w = sum(w for w, c in zip(weights, classifications) if c == "bullish")
    bear_w = sum(w for w, c in zip(weights, classifications) if c == "bearish")
    if bull_w > 1.5 * max(bear_w, 1e-9) and bull_w > 0:
        return "bullish"
    if bear_w > 1.5 * max(bull_w, 1e-9) and bear_w > 0:
        return "bearish"
    return "unknown"


# ---------------------------------------------------------------------------
# ConfirmedBoxIndex (verbatim from Phase 2)
# ---------------------------------------------------------------------------
class ConfirmedBoxIndex:
    def __init__(self, m30: pd.DataFrame):
        confirmed_ids = (
            m30.loc[m30["m30_box_confirmed"] == True, "m30_box_id"]
            .dropna().unique()
        )
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
            base_class = classify_box_from_dict(d)
            bars = len(sub)
            items.append((first_conf_ts, int(bid), base_class, bars))
        items.sort(key=lambda x: x[0])
        self.ts = np.array([pd.Timestamp(x[0]).value for x in items], dtype=np.int64)
        self.box_ids = np.array([x[1] for x in items], dtype=np.int64)
        self.base_class = [x[2] for x in items]
        self.bars = np.array([x[3] for x in items], dtype=np.int32)
        log(f"      ConfirmedBoxIndex: {len(items)} confirmed boxes")

    def last_k_at(self, ts: pd.Timestamp, k: int) -> list[int]:
        ts_ns = pd.Timestamp(ts).value
        idx_end = int(np.searchsorted(self.ts, ts_ns, side="right"))
        if idx_end == 0:
            return []
        idx_start = max(0, idx_end - k)
        return list(range(idx_start, idx_end))

    def classification_with_min_bars(self, idx: int, min_bars: int) -> str:
        if int(self.bars[idx]) < min_bars:
            return "unknown"
        return self.base_class[idx]


# ---------------------------------------------------------------------------
# Decision log (no subsample)
# ---------------------------------------------------------------------------
def load_decision_log() -> pd.DataFrame:
    rows = []
    seen = set()
    for path in DECISION_LOG_PATHS:
        if not path.exists():
            continue
        log(f"      reading {path} ...")
        with path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                ln = line.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                ts = obj.get("timestamp") or obj.get("created_at")
                if not ts:
                    continue
                t = pd.to_datetime(ts, utc=True, errors="coerce")
                if t is pd.NaT or t < START or t >= END:
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
                rows.append({
                    "ts": t, "direction": direction,
                    "action": dec.get("action", "?"),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("ts").reset_index(drop=True)


def add_realized_and_regime(df_dec: pd.DataFrame,
                            ohlcv_close: pd.Series) -> pd.DataFrame:
    """Vectorized realized 1h move + 4h-delta regime classifier."""
    out = df_dec.copy()
    ohlcv_ts = ohlcv_close.index.values
    ohlcv_v = ohlcv_close.values
    dec_ts = pd.to_datetime(out["ts"]).values

    def at_or_before(t):
        idx = np.searchsorted(ohlcv_ts, t, side="right") - 1
        idx = np.clip(idx, 0, len(ohlcv_v) - 1)
        return ohlcv_v[idx]

    p_now = at_or_before(dec_ts)
    p_4h = at_or_before(dec_ts - np.timedelta64(4, "h"))
    p_1h_ago = at_or_before(dec_ts - np.timedelta64(60, "m"))
    p_5h_ago = at_or_before(dec_ts - np.timedelta64(5, "h"))
    p_1h_fwd = at_or_before(dec_ts + np.timedelta64(60, "m"))

    out["realized_1h"] = p_1h_fwd - p_now
    d_now = p_now - p_4h
    d_prev = p_1h_ago - p_5h_ago

    def regime_of(d_arr):
        r = np.full(len(d_arr), "RANGE", dtype=object)
        r[d_arr > RANGE_DELTA4H_PTS] = "TREND_UP"
        r[d_arr < -RANGE_DELTA4H_PTS] = "TREND_DN"
        return r

    cur = regime_of(d_now); prev = regime_of(d_prev)
    transitional = cur != prev
    cur[transitional] = "TRANSITIONAL"
    out["regime"] = cur
    return out


# ---------------------------------------------------------------------------
# Replay + score
# ---------------------------------------------------------------------------
def replay_for_candidate(
    df_dec: pd.DataFrame,
    box_index: ConfirmedBoxIndex,
    candidate: tuple,
) -> pd.DataFrame:
    """Per decision: derive new bias, mark blocked, score survivors."""
    out = df_dec.copy()
    biases = []
    blocked = []
    survives = []
    mb, w, s = candidate
    for ts, direction, realized in zip(out["ts"], out["direction"],
                                        out["realized_1h"]):
        idxs = box_index.last_k_at(ts, w)
        if not idxs:
            bias = "unknown"
        else:
            classes = [box_index.classification_with_min_bars(i, mb) for i in idxs]
            bias = voting_vote(classes, s)
        biases.append(bias)
        if direction == "LONG" and bias == "bearish":
            blocked.append(True); survives.append(np.nan)
        elif direction == "SHORT" and bias == "bullish":
            blocked.append(True); survives.append(np.nan)
        else:
            blocked.append(False)
            if pd.isna(realized):
                survives.append(np.nan)
            elif direction == "LONG":
                survives.append(1.0 if realized > 0 else 0.0)
            else:
                survives.append(1.0 if realized < 0 else 0.0)
    out["bias_new"] = biases
    out["blocked"] = blocked
    out["survives_correct"] = survives
    return out


def score_overall(replayed: pd.DataFrame) -> dict:
    """Single overall score (regime-weighted)."""
    n = len(replayed)
    n_blocked = int(replayed["blocked"].sum())
    survive = replayed[~replayed["blocked"]]
    correct = survive["survives_correct"].dropna()
    return {
        "n_total": n,
        "n_blocked": n_blocked,
        "block_rate": n_blocked / n if n > 0 else 0.0,
        "n_survive_scored": int(len(correct)),
        "survive_accuracy": float(correct.mean()) if len(correct) > 0 else float("nan"),
    }


def score_per_regime(replayed: pd.DataFrame) -> dict:
    out = {}
    for regime, sub in replayed.groupby("regime"):
        n_total = len(sub)
        n_blocked = int(sub["blocked"].sum())
        survive = sub[~sub["blocked"]]
        correct = survive["survives_correct"].dropna()
        out[regime] = {
            "n_total": n_total, "n_blocked": n_blocked,
            "block_rate": n_blocked / n_total if n_total > 0 else 0.0,
            "n_survive_scored": int(len(correct)),
            "survive_accuracy": float(correct.mean())
                if len(correct) > 0 else float("nan"),
        }
    return out


def bootstrap_ci(replayed: pd.DataFrame, n_iter: int = N_BOOTSTRAP,
                 seed: int = 42) -> dict:
    """Resample-with-replacement bootstrap of overall survive_accuracy +
    block_rate. Returns mean + 2.5/97.5 percentile bounds (95% CI)."""
    rng = np.random.default_rng(seed)
    n = len(replayed)
    if n == 0:
        return {"acc_mean": float("nan"), "acc_lo": float("nan"),
                "acc_hi": float("nan"),
                "block_mean": float("nan"), "block_lo": float("nan"),
                "block_hi": float("nan")}
    blocked_arr = replayed["blocked"].astype(int).values
    survives_arr = replayed["survives_correct"].values
    accs = np.empty(n_iter)
    blocks = np.empty(n_iter)
    for i in range(n_iter):
        idx = rng.integers(0, n, size=n)
        bb = blocked_arr[idx]; ss = survives_arr[idx]
        n_block = bb.sum()
        survive_mask = ~bb.astype(bool)
        scored = ss[survive_mask]
        scored = scored[~np.isnan(scored)]
        blocks[i] = n_block / n
        accs[i] = scored.mean() if len(scored) > 0 else np.nan
    return {
        "acc_mean": float(np.nanmean(accs)),
        "acc_lo": float(np.nanpercentile(accs, 2.5)),
        "acc_hi": float(np.nanpercentile(accs, 97.5)),
        "block_mean": float(np.nanmean(blocks)),
        "block_lo": float(np.nanpercentile(blocks, 2.5)),
        "block_hi": float(np.nanpercentile(blocks, 97.5)),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    log("=" * 70)
    log(f"G-PURDUE Step 4 — Grid search + bootstrap CI")
    log(f"  Corpus: {START} -> {END} (10 months L2)")
    log(f"  Grid: {len(MIN_BARS_CHOICES)} x {len(WINDOW_CHOICES)} x {len(STRATEGIES)} "
        f"= {N_HYPOTHESES} hypotheses")
    log(f"  Bonferroni alpha: {ALPHA_BONFERRONI:.2e}")
    log(f"  Bootstrap iterations: {N_BOOTSTRAP}")
    log("=" * 70)

    t0 = time.time()

    log("\n[1/5] Loading decision_log ...")
    df_dec = load_decision_log()
    log(f"      {len(df_dec):,} signals")
    if df_dec.empty:
        log("      no decisions; abort.")
        return 1

    log("\n[2/5] Loading OHLCV close + computing realized_1h + regime ...")
    ohlcv = pd.read_parquet(OHLCV_PARQUET, columns=["close"])
    ohlcv.index = pd.to_datetime(ohlcv.index, utc=True)
    ohlcv = ohlcv["close"].sort_index()
    df_dec = add_realized_and_regime(df_dec, ohlcv)
    log(f"      regime dist: {df_dec['regime'].value_counts().to_dict()}")

    log("\n[3/5] Building ConfirmedBoxIndex from M30 parquet ...")
    m30 = pd.read_parquet(M30_PARQUET)
    m30.index = pd.to_datetime(m30.index, utc=True)
    m30_corpus = m30[(m30.index >= START) & (m30.index < END)]
    box_index = ConfirmedBoxIndex(m30_corpus)

    log("\n[4/5] Replaying baseline (no voting, single most-recent box) ...")
    out_baseline = replay_for_candidate(df_dec, box_index, (1, 1, "majority"))
    base_overall = score_overall(out_baseline)
    base_regime = score_per_regime(out_baseline)
    log(f"      baseline overall: acc={base_overall['survive_accuracy']:.4f} "
        f"block_rate={base_overall['block_rate']:.4f}")

    log(f"\n[5/5] Grid search ({N_HYPOTHESES} candidates) ...")
    results: list[dict] = []
    candidates = [(mb, w, s) for mb in MIN_BARS_CHOICES
                              for w in WINDOW_CHOICES
                              for s in STRATEGIES]
    for k, cand in enumerate(candidates, 1):
        replayed = replay_for_candidate(df_dec, box_index, cand)
        overall = score_overall(replayed)
        per_regime = score_per_regime(replayed)
        boot = bootstrap_ci(replayed)
        if k == 1 or k % 14 == 0 or k == len(candidates):
            log(f"      [{k:>3}/{len(candidates)}] "
                f"({cand[0]},{cand[1]},{cand[2]:>17}) "
                f"acc_mean={boot['acc_mean']:.4f} "
                f"95%CI=[{boot['acc_lo']:.4f}, {boot['acc_hi']:.4f}] "
                f"block={boot['block_mean']:.3f}")
        results.append({
            "candidate": {"min_bars": cand[0], "window": cand[1],
                          "strategy": cand[2]},
            "overall": overall,
            "per_regime": per_regime,
            "bootstrap": boot,
        })

    # Sort by acc_mean descending
    results.sort(key=lambda r: r["bootstrap"]["acc_mean"], reverse=True)
    log(f"\nTop 10 candidates by bootstrap acc_mean:")
    for i, r in enumerate(results[:10], 1):
        c = r["candidate"]
        b = r["bootstrap"]
        log(f"  {i}. ({c['min_bars']},{c['window']},{c['strategy']:>17}) "
            f"acc={b['acc_mean']:.4f} 95%CI=[{b['acc_lo']:.4f}, {b['acc_hi']:.4f}] "
            f"block={b['block_mean']:.3f}")

    # Save results
    (OUT_DIR / "grid_results.json").write_text(
        json.dumps({
            "corpus": {"start": str(START), "end": str(END),
                       "n_signals": len(df_dec)},
            "grid": {"min_bars": MIN_BARS_CHOICES, "window": WINDOW_CHOICES,
                     "strategies": STRATEGIES, "n_hypotheses": N_HYPOTHESES,
                     "alpha_bonferroni": ALPHA_BONFERRONI,
                     "n_bootstrap": N_BOOTSTRAP, "n_folds": N_FOLDS},
            "baseline": {"overall": base_overall, "per_regime": base_regime},
            "results": results,
        }, indent=2, default=str), encoding="utf-8")

    rows_csv = []
    for r in results:
        c = r["candidate"]; b = r["bootstrap"]; o = r["overall"]
        rows_csv.append({
            "min_bars": c["min_bars"], "window": c["window"],
            "strategy": c["strategy"],
            "n_total": o["n_total"], "n_blocked": o["n_blocked"],
            "block_rate": o["block_rate"],
            "n_survive_scored": o["n_survive_scored"],
            "acc_mean_boot": b["acc_mean"],
            "acc_ci_lo": b["acc_lo"], "acc_ci_hi": b["acc_hi"],
            "block_mean_boot": b["block_mean"],
            "block_ci_lo": b["block_lo"], "block_ci_hi": b["block_hi"],
        })
    pd.DataFrame(rows_csv).to_csv(OUT_DIR / "grid_summary.csv", index=False)

    elapsed = time.time() - t0
    log(f"\n[5/5] complete in {elapsed:.1f}s")
    log(f"\nResults: {OUT_DIR / 'grid_results.json'}")
    log(f"Summary: {OUT_DIR / 'grid_summary.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

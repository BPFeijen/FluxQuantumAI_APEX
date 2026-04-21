#!/usr/bin/env python3
"""
backtest_gate_v2_level2.py — Gate V2 Level 2 Impact Analysis
=============================================================
Evaluates the BLOCK_V2_NOICE rule implemented 2026-04-12.

Rule:  BLOCK when  v4_status == "NEUTRAL"  AND  ice_score <= -2

Data sources (read-only, no production files touched):
  Primary  : iceberg_data/*.jsonl    (7M raw iceberg events)
  Enriched : data/features/iceberg_v1/*.parquet  (28 features, windowed)
             data/labels/iceberg_v1/*.parquet    (labels 0=NOISE, 1=NATIVE, 2=SYNTHETIC, 3=ABSORPTION)

Proxy definitions (mirrors ats_live_gate.py _run_v4 + ats_iceberg_gate.py TYPE 4):
  v4_proxy:
    "BLOCK"   — probability >= 0.848  (V4_CONF_THRESHOLD, calibrated CAL-20)
                AND the iceberg is CONTRA to the trade direction
    "PASS"    — probability >= 0.848  AND the iceberg is ALIGNED with trade direction
    "NEUTRAL" — probability < 0.848   (anything below the confidence threshold,
                regardless of whether the detector fired — mirrors _run_v4 line:
                `if not out.detected or out.confidence < V4_CONF_THRESHOLD: return "NEUTRAL"`)

  ice_score_proxy (TYPE 4 only — JSONL contributes ±4 for refills>=3, ±2 for refills<3):
    When iceberg is CONTRA to the trade:
        refill_count >= 3  →  -4
        refill_count >= 1  →  -2
    When ALIGNED or no iceberg:  0

  "CONTRA" definition:
    SHORT trade: BID iceberg is CONTRA (buyers absorbed at resistance)
    LONG  trade: ASK iceberg is CONTRA (sellers absorbed at support)

  Since direction is not in the JSONL, we compute separately:
    - Scenario SHORT: bid = contra, ask = aligned
    - Scenario LONG : ask = contra, bid = aligned
    → Results are symmetric (bid ~49.6%, ask ~50.4%)

Output: ASCII table, no files written.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Force UTF-8 output on Windows (avoid cp1252 UnicodeEncodeError)
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT       = Path("C:/FluxQuantumAI")
JSONL_DIR  = ROOT / "iceberg_data"
FEAT_DIR   = ROOT / "data/features/iceberg_v1"
LABEL_DIR  = ROOT / "data/labels/iceberg_v1"

# ── Thresholds (mirror production) ───────────────────────────────────────────
V4_CONF_THRESHOLD = 0.848   # calibrated 2026-04-11 (CAL-20, T=0.85 stress test → 0.848 ECE)
V4_DETECTION_FLOOR = 0.50   # events below this are filtered out in _run_v4

# ── Labels (ml_iceberg_v2/training/dataset.py) ────────────────────────────────
# 0=NOISE, 1=NATIVE, 2=SYNTHETIC, 3=ABSORPTION
LABEL_NAMES = {0: "NOISE", 1: "NATIVE", 2: "SYNTHETIC", 3: "ABSORPTION"}
ICEBERG_LABELS = {1, 2, 3}  # any detected iceberg

# =============================================================================
# SECTION 1 — Load JSONL (7M events, vectorised)
# =============================================================================
def load_jsonl_vectorised() -> pd.DataFrame:
    files = sorted(JSONL_DIR.glob("iceberg__GC_XCEC_*.jsonl"))
    if not files:
        print("[ERROR] No JSONL files found in", JSONL_DIR)
        sys.exit(1)

    print(f"Loading {len(files)} JSONL files from {JSONL_DIR} ...")
    t0 = time.time()

    records = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records.append({
                        "timestamp"    : rec.get("timestamp", ""),
                        "price"        : float(rec.get("price", 0)),
                        "side"         : str(rec.get("side", "")).lower(),
                        "iceberg_type" : str(rec.get("iceberg_type", "native")).lower(),
                        "probability"  : float(rec.get("probability", 0)),
                        "refill_count" : int(rec.get("refill_count", 1)),
                        "peak_size"    : float(rec.get("peak_size", 0)),
                    })
                except Exception:
                    continue

    df = pd.DataFrame(records)
    elapsed = time.time() - t0
    print(f"  Loaded {len(df):,} events in {elapsed:.1f}s")
    return df


# =============================================================================
# SECTION 2 — Compute v4_proxy and ice_score_proxy (vectorised)
# =============================================================================
def compute_proxies(df: pd.DataFrame) -> pd.DataFrame:
    prob  = df["probability"].values
    refills = df["refill_count"].values
    side  = df["side"].values

    # ── v4_proxy ─────────────────────────────────────────────────────────────
    # Mirrors production _run_v4:
    #   if not out.detected or out.confidence < V4_CONF_THRESHOLD:
    #       return "NEUTRAL", out.confidence
    #
    # BLOCK = prob >= 0.848 AND contra direction
    # PASS  = prob >= 0.848 AND aligned direction
    # NEUTRAL = everything below threshold (includes 0.50–0.848 detected zone)
    #
    # NOTE: direction-dependent BLOCK vs PASS assignment happens below
    # when we build ice_score_short / ice_score_long.
    # For the v4_proxy column: mark prob >= 0.848 as BLOCK_CANDIDATE,
    # then split into BLOCK/PASS per direction when applying gate columns.
    v4 = np.where(prob >= V4_CONF_THRESHOLD, "BLOCK_CANDIDATE", "NEUTRAL")
    df = df.copy()
    df["v4_proxy"] = v4

    # ── ice_score_proxy: TYPE 4 contribution (depends on direction) ───────────
    # We compute for BOTH trade directions (SHORT and LONG) separately.
    #
    # SHORT trade: BID = contra (-4/-2), ASK = aligned (+4/+2)
    # LONG  trade: ASK = contra (-4/-2), BID = aligned (+4/+2)
    #
    # Conditions for TYPE 4 contribution:
    #   prob >= 0.50 AND refill_count >= 3  → magnitude 4
    #   prob >= 0.50 AND refill_count >= 1  → magnitude 2
    #   else                                → magnitude 0

    above_floor   = prob >= V4_DETECTION_FLOOR
    strong_refill = refills >= 3
    weak_refill   = (refills >= 1) & ~strong_refill
    magnitude     = np.where(above_floor & strong_refill, 4,
                    np.where(above_floor & weak_refill,   2, 0))

    # Sign depends on direction scenario:
    bid_mask = side == "bid"
    ask_mask = side == "ask"

    # SHORT scenario: bid=contra→negative, ask=aligned→positive
    df["ice_score_short"] = np.where(bid_mask, -magnitude,
                             np.where(ask_mask, +magnitude, 0)).astype(np.int8)
    # LONG scenario: ask=contra→negative, bid=aligned→positive
    df["ice_score_long"]  = np.where(ask_mask, -magnitude,
                             np.where(bid_mask, +magnitude, 0)).astype(np.int8)

    return df


# =============================================================================
# SECTION 3 — Gate columns
# =============================================================================
def apply_gate_columns(df: pd.DataFrame, ice_score_col: str, contra_side: str) -> pd.DataFrame:
    """
    Resolve direction-dependent v4 status and apply gate columns.

    BLOCK_CANDIDATE + contra side  → "BLOCK"   (V4 fires, contra detected, high conf)
    BLOCK_CANDIDATE + aligned side → "PASS"    (V4 fires, aligned, high conf — structural reversal)
    NEUTRAL                        → "NEUTRAL" (below threshold, gate does not see a clear iceberg)

    baseline_pass: NOT BLOCK  (mirrors: only V4 BLOCK hard-vetoes)
    level2_pass:   v4 == PASS  OR  (v4 == NEUTRAL AND ice_score > -2)
    """
    v4_raw   = df["v4_proxy"].values
    ice      = df[ice_score_col].values
    side     = df["side"].values

    is_cand  = v4_raw == "BLOCK_CANDIDATE"
    is_contra = side == contra_side

    # Direction-resolved v4 status
    v4_resolved = np.where(
        is_cand & is_contra,  "BLOCK",
        np.where(
        is_cand & ~is_contra, "PASS",
        "NEUTRAL"))  # everything below threshold

    baseline_pass = v4_resolved != "BLOCK"
    # level2_pass: PASS always passes; NEUTRAL passes only when ice_score > -2
    level2_pass   = (v4_resolved == "PASS") | ((v4_resolved == "NEUTRAL") & (ice > -2.0))

    df = df.copy()
    df["v4_resolved"] = v4_resolved
    df["baseline_pass"] = baseline_pass
    df["level2_pass"]   = level2_pass
    df["blocked_by_l2"] = baseline_pass & ~level2_pass
    return df


# =============================================================================
# SECTION 4 — Load parquet labels (enrichment for Win-Rate proxy)
# =============================================================================
def load_labels() -> pd.DataFrame | None:
    label_files = sorted(LABEL_DIR.glob("*.parquet"))
    if not label_files:
        return None
    print(f"\nLoading {len(label_files)} label parquets ...")
    parts = []
    for f in label_files:
        try:
            ldf = pd.read_parquet(f, columns=["window_start", "label", "label_confidence"])
            parts.append(ldf)
        except Exception:
            pass
    if not parts:
        return None
    labels = pd.concat(parts, ignore_index=True)
    labels["date"] = pd.to_datetime(labels["window_start"]).dt.date.astype(str)
    print(f"  {len(labels):,} label rows | distribution: {labels['label'].value_counts().to_dict()}")
    return labels


# =============================================================================
# SECTION 5 — Merge JSONL events with labels (by date proximity)
# =============================================================================
def merge_with_labels(df: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame | None:
    """
    Merge: group JSONL events by date, join with label windows.
    Since JSONL timestamps and label windows are different granularities,
    we compute per-date label stats (iceberg label rate) and use them
    as a proxy for signal quality.
    """
    try:
        df = df.copy()
        df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df["date"] = df["timestamp_dt"].dt.date.astype(str)

        label_stats = (
            labels.groupby("date")["label"]
            .agg(
                total_windows="count",
                iceberg_windows=lambda x: (x > 0).sum(),
                iceberg_rate=lambda x: (x > 0).mean(),
            )
            .reset_index()
        )
        merged = df.merge(label_stats, on="date", how="left")
        return merged
    except Exception as e:
        print(f"  [WARN] merge_with_labels failed: {e}")
        return None


# =============================================================================
# SECTION 6 — ASCII table printer
# =============================================================================
def sep(w=70, ch="─"):
    print(ch * w)

def header(title, w=70):
    pad = (w - len(title) - 2) // 2
    print("─" * pad + f" {title} " + "─" * (w - pad - len(title) - 2))

def row(label, value, width=38):
    print(f"  {label:<{width}} {value}")


# =============================================================================
# MAIN
# =============================================================================
def main():
    print()
    sep(70, "═")
    print("  GATE V2 LEVEL 2 — BACKTEST PROXY ANALYSIS")
    print("  Rule: BLOCK when v4=NEUTRAL AND ice_score <= -2")
    print("  Date: 2026-04-12  |  Threshold: T=0.848 (CAL-20)")
    sep(70, "═")

    # ── Load ──────────────────────────────────────────────────────────────────
    df = load_jsonl_vectorised()
    df = compute_proxies(df)

    total_events = len(df)

    # ── V4 proxy distribution ─────────────────────────────────────────────────
    n_high   = (df["probability"] >= V4_CONF_THRESHOLD).sum()
    n_sub    = ((df["probability"] >= V4_DETECTION_FLOOR) & (df["probability"] < V4_CONF_THRESHOLD)).sum()
    n_low    = (df["probability"] < V4_DETECTION_FLOOR).sum()

    print()
    header("1. DATASET OVERVIEW")
    row("Total iceberg events",              f"{total_events:>12,}")
    row("prob >= 0.848  (V4 BLOCK/PASS zone)", f"{n_high:>12,}  ({n_high/total_events*100:.1f}%)")
    row("prob 0.50-0.848 (sub-threshold)",   f"{n_sub:>12,}  ({n_sub/total_events*100:.1f}%)")
    row("prob < 0.50    (V4 NEUTRAL floor)", f"{n_low:>12,}  ({n_low/total_events*100:.1f}%)")
    row("  → V4 NEUTRAL total (prod. proxy)",f"{(n_sub+n_low):>12,}  ({(n_sub+n_low)/total_events*100:.1f}%)")
    row("Side BID",                       f"{(df['side']=='bid').sum():>12,}  ({(df['side']=='bid').mean()*100:.1f}%)")
    row("Side ASK",                       f"{(df['side']=='ask').sum():>12,}  ({(df['side']=='ask').mean()*100:.1f}%)")

    # ── Gate analysis for SHORT and LONG scenarios ────────────────────────────
    for scenario, ice_col, contra_side in [
        ("SHORT trade (BID = contra)", "ice_score_short", "bid"),
        ("LONG  trade (ASK = contra)", "ice_score_long",  "ask"),
    ]:
        dfs = apply_gate_columns(df, ice_col, contra_side)

        n_baseline = dfs["baseline_pass"].sum()
        n_l2_pass  = dfs["level2_pass"].sum()
        n_blocked  = dfs["blocked_by_l2"].sum()
        drop_rate  = n_blocked / n_baseline * 100 if n_baseline > 0 else 0.0

        # Subset: events exclusively blocked by Level 2
        l2_only = dfs[dfs["blocked_by_l2"]]

        print()
        header(f"2. GATE METRICS — {scenario}")
        row("Events passing BASELINE (not V4 BLOCK)", f"{n_baseline:>12,}")
        row("Events passing LEVEL 2",                 f"{n_l2_pass:>12,}")
        row("Events blocked by Level 2 only",         f"{n_blocked:>12,}  ({drop_rate:.2f}% drop rate)")
        print()
        row("  [BLOCKED subset — quality probe]", "")
        if len(l2_only) > 0:
            row("  Count",               f"{len(l2_only):>12,}")
            row("  probability mean",    f"{l2_only['probability'].mean():>12.4f}")
            row("  probability median",  f"{l2_only['probability'].median():>12.4f}")
            row("  refill_count mean",   f"{l2_only['refill_count'].mean():>12.2f}")
            row("  refill_count median", f"{l2_only['refill_count'].median():>12.1f}")
            row("  ice_score_proxy mean",f"{l2_only[ice_col].mean():>12.2f}")
            # Distribution of blocked events by prob bin
            bins    = [0, 0.50, 0.60, 0.70, 0.80, 0.848, 1.01]
            labels_ = ["<0.50", "0.50-0.60", "0.60-0.70", "0.70-0.80", "0.80-0.848", ">=0.848"]
            cuts    = pd.cut(l2_only["probability"], bins=bins, labels=labels_, right=False)
            dist    = cuts.value_counts().sort_index()
            print()
            row("  Probability distribution of blocked:", "")
            for lbl, cnt in dist.items():
                pct = cnt / len(l2_only) * 100
                bar = "█" * int(pct / 2)
                row(f"    {lbl}", f"{cnt:>8,}  ({pct:5.1f}%)  {bar}")
            # Refill distribution
            r_bins    = [1, 2, 3, 5, 10, 21, 9999]
            r_labels  = ["1", "2", "3-4", "5-9", "10-20", "21+"]
            r_cuts    = pd.cut(l2_only["refill_count"], bins=r_bins, labels=r_labels, right=False)
            r_dist    = r_cuts.value_counts().sort_index()
            print()
            row("  Refill count distribution of blocked:", "")
            for lbl, cnt in r_dist.items():
                pct = cnt / len(l2_only) * 100
                bar = "█" * int(pct / 2)
                row(f"    {lbl} refill(s)", f"{cnt:>8,}  ({pct:5.1f}%)  {bar}")
        else:
            row("  (no events blocked by Level 2 in this scenario)", "")

    # ── Combined expected drop rate (50/50 direction split) ───────────────────
    # In practice each JSONL event appears equally in SHORT and LONG scenarios.
    # Expected gate firing = average of both scenarios.
    dfs_s = apply_gate_columns(df, "ice_score_short", "bid")
    dfs_l = apply_gate_columns(df, "ice_score_long",  "ask")
    n_base   = dfs_s["baseline_pass"].sum()  # same for both (v4 doesn't depend on direction)
    n_blk_s  = dfs_s["blocked_by_l2"].sum()
    n_blk_l  = dfs_l["blocked_by_l2"].sum()
    n_blk_ex = (n_blk_s + n_blk_l) / 2      # expected over 50/50 direction split
    drop_ex  = n_blk_ex / n_base * 100 if n_base > 0 else 0.0

    print()
    header("3. COMBINED EXPECTED DROP RATE (50/50 direction split)")
    row("Baseline passing events",             f"{n_base:>12,}")
    row("Blocked (SHORT scenario)",            f"{n_blk_s:>12,}  ({n_blk_s/n_base*100:.2f}%)")
    row("Blocked (LONG  scenario)",            f"{n_blk_l:>12,}  ({n_blk_l/n_base*100:.2f}%)")
    row("Expected blocked (50/50 split)",      f"{n_blk_ex:>12,.0f}  ({drop_ex:.2f}% expected drop rate)")

    # ── Sub-threshold zone decomposition ──────────────────────────────────────
    # The V2 gate only sees events where V4=PASS (sub-threshold detected).
    # Break down the sub-threshold zone by refill_count to understand quality.
    # Sub-threshold = detected but below block threshold (0.50 <= prob < 0.848)
    sub = df[(df["probability"] >= V4_DETECTION_FLOOR) & (df["probability"] < V4_CONF_THRESHOLD)].copy()

    print()
    header("4. SUB-THRESHOLD ZONE (0.50 <= prob < 0.848) — ANATOMY")
    row("Events in sub-threshold zone",     f"{len(sub):>12,}  ({len(sub)/total_events*100:.1f}% of all)")
    if len(sub) > 0:
        row("  probability mean",           f"{sub['probability'].mean():>12.4f}")
        row("  refill_count >= 1",          f"{(sub['refill_count']>=1).sum():>12,}  ({(sub['refill_count']>=1).mean()*100:.1f}%)")
        row("  refill_count >= 3",          f"{(sub['refill_count']>=3).sum():>12,}  ({(sub['refill_count']>=3).mean()*100:.1f}%)")
        row("  refill_count >= 5",          f"{(sub['refill_count']>=5).sum():>12,}  ({(sub['refill_count']>=5).mean()*100:.1f}%)")
        row("  side BID",                   f"{(sub['side']=='bid').sum():>12,}  ({(sub['side']=='bid').mean()*100:.1f}%)")
        row("  side ASK",                   f"{(sub['side']=='ask').sum():>12,}  ({(sub['side']=='ask').mean()*100:.1f}%)")
        row("  iceberg_type native",        f"{(sub['iceberg_type']=='native').sum():>12,}  ({(sub['iceberg_type']=='native').mean()*100:.1f}%)")
        row("  iceberg_type synthetic",     f"{(sub['iceberg_type']=='synthetic').sum():>12,}  ({(sub['iceberg_type']=='synthetic').mean()*100:.1f}%)")

    # ── PnL / Label enrichment ─────────────────────────────────────────────────
    labels_df = load_labels()
    if labels_df is not None:
        merged = merge_with_labels(df, labels_df)
        if merged is not None:
            # Overall iceberg label rate by date — proxy for "day quality"
            # Sub-threshold events on high-iceberg-rate days vs low-iceberg-rate days
            merged_sub = merged[(merged["probability"] >= V4_DETECTION_FLOOR) & (merged["probability"] < V4_CONF_THRESHOLD)].copy()
            merged_sub = merged_sub.dropna(subset=["iceberg_rate"])

            if len(merged_sub) > 0:
                print()
                header("5. LABEL ENRICHMENT — SUB-THRESHOLD EVENTS VS DAILY ICEBERG RATE")
                row("Sub-threshold events with label data",  f"{len(merged_sub):>12,}")

                # Split by daily iceberg rate median
                med_rate = merged_sub["iceberg_rate"].median()
                hi = merged_sub[merged_sub["iceberg_rate"] >= med_rate]
                lo = merged_sub[merged_sub["iceberg_rate"] <  med_rate]

                row(f"  Median daily iceberg rate",           f"{med_rate:>12.4f}")
                row(f"  Events on HIGH iceberg-rate days",    f"{len(hi):>12,}  (iceberg_rate >= {med_rate:.3f})")
                row(f"  Events on LOW  iceberg-rate days",    f"{len(lo):>12,}  (iceberg_rate <  {med_rate:.3f})")
                row(f"  Avg prob HIGH-iceberg days",          f"{hi['probability'].mean():>12.4f}")
                row(f"  Avg prob LOW-iceberg  days",          f"{lo['probability'].mean():>12.4f}")
                print()
                row("  NOTE: JSONL has no per-event TP/SL outcome. For Win-Rate", "")
                row("  analysis use ats_trades_live_l2.parquet (62 trades) or", "")
                row("  request ml_iceberg_v2 label parquets from S3.", "")
            else:
                print()
                header("5. LABEL ENRICHMENT")
                row("  No overlapping dates between JSONL and label parquets.", "")
    else:
        print()
        header("5. LABEL ENRICHMENT (PnL)")
        row("  Label parquets not found — PnL analysis skipped.", "")

    # ── Interpretation ─────────────────────────────────────────────────────────
    print()
    header("6. INTERPRETATION")
    print("""
  The V2 Level 2 gate (BLOCK_V2_NOICE) targets the "grey zone":
  icebergs detected by the Quantower DLL (probability >= 0.50)
  but below the calibrated ML BLOCK threshold (0.848).

  These are events where:
  - The detector saw iceberg-like refill patterns
  - But the ML model lacked confidence to hard-veto
  - Yet the V1 rule-based score signals a contra pattern

  KEY INSIGHT:
  - The gate ONLY fires at structural level entries where V4=NEUTRAL
    (no events near price in last 10min, OR sub-threshold confidence)
  - Combined with ice_score <= -2 (from TYPE 4 or other rule signals)
  - This means the gate fires on "weak institutional contra signal"
    without a confirming aligned iceberg

  RISK ASSESSMENT:
  - Low drop rate implies minimal disruption to trade flow
  - High drop rate implies the gate is filtering aggressively
    (needs investigation: are we blocking real opportunities?)
  - Recommend gate replay on 62-trade dataset for Win-Rate impact
    using: python diagnostics/gate_replay_v13.py --enable-v2-level2
""")

    sep(70, "═")
    print("  END OF REPORT — read-only, no production files modified")
    sep(70, "═")
    print()


if __name__ == "__main__":
    main()

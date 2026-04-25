"""Step 6 — blast radius: cross-ref Part A dots against corrupt dates + consumer inventory."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import pandas as pd

RECON = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\daily_reconciliation_1min.csv")
DOTS_D1 = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\ats_trend_line_part_a\dots_D1.csv")
DOTS_H4 = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\ats_trend_line_part_a\dots_H4.csv")
DOTS_M30 = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\ats_trend_line_part_a\dots_M30.csv")
OUT = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\blast_radius.md")


def main():
    if not RECON.exists():
        print(f"ERROR: {RECON} not found")
        return
    recon = pd.read_csv(RECON)
    recon = recon[recon["status"] == "OK"]
    recon["date_ts"] = pd.to_datetime(recon["date"])
    # Use ROBUST reclassification if available (post-hoc fix for contaminated healthy subset)
    robust_path = RECON.parent / "daily_reconciliation_1min_robust.csv"
    if robust_path.exists():
        robust = pd.read_csv(robust_path)
        recon = recon.merge(robust[["date", "band_robust"]], on="date", how="left")
        recon["band"] = recon["band_robust"].fillna(recon["band"])
    corrupt = set(recon[recon["band"] == "CORRUPT"]["date"])
    major = set(recon[recon["band"] == "MAJOR"]["date"])
    minor = set(recon[recon["band"] == "MINOR"]["date"])
    clean = set(recon[recon["band"] == "CLEAN"]["date"])

    rows = []
    rows.append("# Step 6 — Blast Radius for Part A ATS Trend Line Dots and Downstream Consumers\n\n")
    rows.append(f"**Base set:** {len(recon)} reconciled dates\n")
    rows.append(f"- CLEAN: {len(clean)}\n- MINOR: {len(minor)}\n- MAJOR: {len(major)}\n- CORRUPT: {len(corrupt)}\n\n")

    # Part A dots
    for label, path in [("D1", DOTS_D1), ("H4", DOTS_H4), ("M30", DOTS_M30)]:
        if not path.exists():
            rows.append(f"## {label} dots — FILE MISSING {path}\n\n")
            continue
        dots = pd.read_csv(path)
        dots["ts_parsed"] = pd.to_datetime(dots["ts"])
        dots["date"] = dots["ts_parsed"].dt.strftime("%Y-%m-%d")
        n_total = len(dots)
        # Filter to within the reconciliation window (date must be in recon)
        reconciled_dates = set(recon["date"])
        dots_in_win = dots[dots["date"].isin(reconciled_dates)]
        on_corrupt = dots_in_win[dots_in_win["date"].isin(corrupt)]
        on_major = dots_in_win[dots_in_win["date"].isin(major)]
        on_minor = dots_in_win[dots_in_win["date"].isin(minor)]
        on_clean = dots_in_win[dots_in_win["date"].isin(clean)]
        rows.append(f"## {label} dots\n")
        rows.append(f"- Total dots (full 3y backtest): **{n_total}**\n")
        rows.append(f"- Dots whose date is within reconciliation window: {len(dots_in_win)}\n")
        rows.append(f"- Dots on CORRUPT dates: **{len(on_corrupt)}** ({len(on_corrupt)/max(len(dots_in_win),1)*100:.1f}% of in-window)\n")
        rows.append(f"- Dots on MAJOR dates: {len(on_major)} ({len(on_major)/max(len(dots_in_win),1)*100:.1f}% of in-window)\n")
        rows.append(f"- Dots on MINOR dates: {len(on_minor)}\n")
        rows.append(f"- Dots on CLEAN dates: {len(on_clean)}\n")
        if not on_corrupt.empty:
            rows.append(f"\n### {label} dots on CORRUPT dates\n")
            for _, r in on_corrupt.iterrows():
                rows.append(f"- {r['ts']}  dir={int(r['direction']):+d}  level={r['level']:.2f}\n")
        if not on_major.empty:
            rows.append(f"\n### {label} dots on MAJOR dates\n")
            for _, r in on_major.iterrows():
                rows.append(f"- {r['ts']}  dir={int(r['direction']):+d}  level={r['level']:.2f}\n")
        rows.append("\n")

    # Threshold-calibration scripts inventory
    rows.append("## Threshold-calibration script inventory (static grep)\n\n")
    rows.append("The following scripts read parquets to calibrate thresholds or features. ")
    rows.append("If their outputs were persisted and used downstream, those outputs are "
                "potentially tainted by any corruption in the parquet for the dates they consumed.\n\n")

    repo = Path(r"C:\FluxQuantumAI")
    pats = ["gc_m30_boxes", "gc_ohlcv_l2_joined", "gc_d1_boxes"]
    cal_scripts = []
    for py in repo.rglob("*.py"):
        if "_cache" in str(py) or "__pycache__" in str(py):
            continue
        try:
            t = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if any(p in t for p in pats):
            # Heuristic: does it also mention calibration / threshold / percentile / histogram?
            kw = ["calibrat", "threshold", "percentile", "quantile", "histogram", "distribution"]
            if any(k in t.lower() for k in kw):
                cal_scripts.append(str(py.relative_to(repo)))
    cal_scripts = sorted(set(cal_scripts))[:60]
    for s in cal_scripts:
        rows.append(f"- `{s}`\n")
    rows.append(f"\nTotal calibration-like scripts referencing parquets: {len(cal_scripts)}\n\n")

    # ML training artefacts
    rows.append("## ML training artefacts — source check\n\n")
    rows.append("Models under project memory (per user memory system):\n")
    rows.append("- `ml_iceberg_v2`: trained on S3 SageMaker 2026-04-11, input schema includes L2 features. Blast radius: ANY L2-derived feature value on a CORRUPT date is suspect. Need per-date feature reconciliation to quantify.\n")
    rows.append("- `ats_iceberg_v1`: training from iceberg-scoring JSONL proxy, not from `gc_ohlcv_l2_joined.parquet` directly. Blast radius: LOW for direct price corruption, but if the proxy OHLCV was used for feature engineering, MEDIUM.\n")
    rows.append("- `grenadier_v2` (LSTM-Autoencoder SCHEMA_FLUXFOX_V2, 26 features F01–F23): primary feature source is the microstructure-derived parquet. Blast radius: requires per-feature reconciliation to confirm.\n")
    rows.append("\nRecommendation: treat every ML training run whose input window covers a CORRUPT/MAJOR date as needing re-validation against tape-derived inputs before the next retrain cycle.\n\n")

    # Live production
    rows.append("## Live production runtime consumption\n\n")
    rows.append("- `live/ats_trend_line.py` at runtime consumes whichever parquet is current on disk. ")
    rows.append("The historical corruption pattern (per Step 5) does NOT directly imply runtime corruption, but it DOES imply the upstream build pipeline can produce shifted prices. ")
    rows.append("Runtime blast radius therefore depends on whether the same build-path executes live, and with what guardrails.\n")
    rows.append("- `m30_updater.py` (per `_audit/predeploy/run_live_*.py`) rebuilds `gc_m30_boxes.parquet` every 60s. If the same upstream bug triggers live, the direction-oracle dot can be artefactual in real-time.\n\n")
    rows.append("Recommendation: add a live reconciliation probe — every N minutes, compare the newest pq row against the same timestamp in the live trades file, alert if `|delta_h| > MAJOR threshold`.\n")

    OUT.write_text("".join(rows), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()

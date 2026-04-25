"""Step 2 + 3 — per-day 1-min reconciliation with data-driven thresholds. READ-ONLY."""
from __future__ import annotations
import sys
import csv
import json
import random
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _reconcile_helpers import (
    build_raw_1min_from_trades, load_parquet_1min_day,
    raw_trades_path, databento_path, TRADES_DIR, DATABENTO_DIR,
    L2_PARQUET,
)

OUT_CSV = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\daily_reconciliation_1min.csv")
THRESH_MD = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\divergence_thresholds_calibration.md")
HEALTHY_SPOT = ["2026-02-11", "2026-02-12", "2026-02-17", "2026-02-18",
                "2026-02-19", "2026-02-20"]
RAND_SEED = 42

# Handover date: Databento backfill ends 2025-11-25, Quantower L2 capture starts 2025-11-26.
# For Databento-era dates, the parquet was built FROM databento, so
# raw-vs-parquet reconciliation is self-referential (always ~0 delta).
# For signal, restrict the comparison to L2-era dates.
HANDOVER_DATE = "2025-11-26"
# Sample a handful of Databento-era dates as a sanity baseline (should all show 0 delta)
DATABENTO_SANITY_SAMPLE = 10


def list_l2_dates() -> list[str]:
    """Dates with trades_*.csv.gz in L2 capture."""
    dates = set()
    for f in TRADES_DIR.glob("trades_*.csv.gz"):
        dates.add(f.stem.replace("trades_", "").replace(".csv", ""))
    return sorted(dates)


def list_databento_dates() -> list[str]:
    """Dates with Databento files."""
    dates = set()
    for f in DATABENTO_DIR.glob("glbx-mdp3-*.mbp-10.csv.zst"):
        stem = f.stem.split(".")[0]
        raw = stem.replace("glbx-mdp3-", "")
        dates.add(f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}")
    return sorted(dates)


def list_available_dates() -> list[str]:
    """L2 dates + sample of databento dates for sanity."""
    l2 = list_l2_dates()
    db = list_databento_dates()
    import random
    rng = random.Random(RAND_SEED + 1)
    db_sample = rng.sample(db, min(DATABENTO_SANITY_SAMPLE, len(db)))
    return sorted(set(l2 + db_sample))


def compute_minute_deltas(date: str) -> pd.DataFrame | None:
    """Return DataFrame with per-minute (ts, dh, dl, dv, dv_pct) for date, or None."""
    raw = build_raw_1min_from_trades(date)
    if raw is None or raw.empty:
        return None
    pq = load_parquet_1min_day(date)
    if pq.empty:
        return None
    merged = pq.join(raw, how="inner", rsuffix="_raw")
    if merged.empty:
        return None
    out = pd.DataFrame(index=merged.index)
    out["dh"] = (merged["high_raw"] - merged["high"]).abs()
    out["dl"] = (merged["low_raw"] - merged["low"]).abs()
    out["dv"] = (merged["volume_raw"] - merged["volume"]).abs()
    out["dv_pct"] = out["dv"] / merged["volume_raw"].replace(0, np.nan).abs()
    out["sh"] = merged["high_raw"] - merged["high"]
    out["sl"] = merged["low_raw"] - merged["low"]
    return out


def main():
    all_dates = list_available_dates()
    print(f"Total tape dates available: {len(all_dates)}")
    print(f"Date range: {all_dates[0]} .. {all_dates[-1]}")

    # ---- STEP 2: Build healthy subset from L2 era (where reconciliation is meaningful) ----
    l2_dates = list_l2_dates()
    print(f"L2-era dates: {len(l2_dates)}  (range {l2_dates[0]}..{l2_dates[-1]})")
    rng = random.Random(RAND_SEED)
    candidate_pool = [d for d in l2_dates
                      if d not in HEALTHY_SPOT + ["2026-02-13"]]
    sample = rng.sample(candidate_pool, min(20, len(candidate_pool)))
    healthy_subset = sorted(set(HEALTHY_SPOT + sample))
    print(f"\nHealthy subset (6 spot + 20 random): {len(healthy_subset)} dates")
    for d in healthy_subset:
        print(f"  {d}")

    healthy_deltas = []
    for i, d in enumerate(healthy_subset):
        dd = compute_minute_deltas(d)
        if dd is None:
            print(f"  [{i+1}/{len(healthy_subset)}] {d}: NO DATA")
            continue
        healthy_deltas.append(dd)
        print(f"  [{i+1}/{len(healthy_subset)}] {d}: {len(dd)} min, "
              f"max_dh={dd['dh'].max():.2f}, p99_dh={dd['dh'].quantile(0.99):.3f}")

    if not healthy_deltas:
        print("ERROR: no healthy data")
        return

    H = pd.concat(healthy_deltas, axis=0)
    print(f"\nHealthy combined: {len(H)} minutes")

    pcts = [0.50, 0.75, 0.90, 0.95, 0.99, 0.995, 0.999, 0.9999]
    thresh = {}
    for metric in ["dh", "dl", "dv_pct"]:
        vals = H[metric].dropna()
        thresh[metric] = {f"p{int(p*10000)/100}": float(vals.quantile(p))
                          for p in pcts}
        thresh[metric]["max"] = float(vals.max())
        thresh[metric]["mean"] = float(vals.mean())
        thresh[metric]["median"] = float(vals.median())

    # Pick classification cutoffs per instruction §2
    p95_dh = thresh["dh"]["p95.0"]
    p99_dh = thresh["dh"]["p99.0"]
    p999_dh = thresh["dh"]["p99.9"]
    print(f"\nHealthy dh thresholds: p95={p95_dh:.3f}  p99={p99_dh:.3f}  p99.9={p999_dh:.3f}  max={thresh['dh']['max']:.2f}")

    # Write threshold calibration doc
    THRESH_MD.write_text(
        f"""# Step 2 — Data-driven divergence thresholds (per DATA-001 §3 Step 2)

**Generated:** run of step2_3_reconcile.py
**Healthy subset:** {len(healthy_subset)} dates
- 6 D1 spot-checks: {', '.join(HEALTHY_SPOT)}
- 20 random (seed=42): {', '.join(sample)}

**Healthy combined minutes:** {len(H):,}

## Per-minute delta distribution across healthy subset

### price_delta_high_pts (|tape_H - pq_H| per minute)
{pd.Series(thresh['dh']).to_string()}

### price_delta_low_pts (|tape_L - pq_L| per minute)
{pd.Series(thresh['dl']).to_string()}

### volume_delta_pct (|tape_V - pq_V| / tape_V)
{pd.Series(thresh['dv_pct']).to_string()}

## Classification bands (derived)

- CLEAN   : day max(dh) <= p95  = {p95_dh:.3f} pts
- MINOR   : p95 < day max(dh) <= p99  = {p99_dh:.3f} pts
- MAJOR   : p99 < day max(dh) <= p99.9 = {p999_dh:.3f} pts
- CORRUPT : day max(dh) > p99.9 = {p999_dh:.3f} pts

## Notes on distribution shape

- Heavy-tailed? max ({thresh['dh']['max']:.2f}) vs p99.9 ({p999_dh:.3f}): ratio {thresh['dh']['max']/max(p999_dh,0.01):.2f}
- If ratio >> 3, the healthy subset itself contains suspect minutes and thresholds are a
  conservative upper bound for "normal".
""",
        encoding="utf-8",
    )
    print(f"Wrote {THRESH_MD}")

    # ---- STEP 3: Full-series reconciliation ----
    print(f"\n\n=== Step 3: full-series reconciliation on {len(all_dates)} dates ===")
    results = []
    for i, d in enumerate(all_dates):
        dd = compute_minute_deltas(d)
        if dd is None:
            raw_path = raw_trades_path(d)
            db_path = databento_path(d)
            results.append({
                "date": d,
                "status": "NO_DATA",
                "has_trades": raw_path.exists(),
                "has_databento": db_path.exists(),
            })
            continue
        max_dh = float(dd["dh"].max())
        max_dl = float(dd["dl"].max())
        mean_dh = float(dd["dh"].mean())
        mean_dl = float(dd["dl"].mean())
        # Classification: based on day max(dh)
        if max_dh <= p95_dh:
            band = "CLEAN"
        elif max_dh <= p99_dh:
            band = "MINOR"
        elif max_dh <= p999_dh:
            band = "MAJOR"
        else:
            band = "CORRUPT"
        # Day-level H/L tape-minus-pq (from raw reconstruction)
        raw = build_raw_1min_from_trades(d)
        pq = load_parquet_1min_day(d)
        day_dH = float(raw["high"].max() - pq["high"].max())
        day_dL = float(raw["low"].min() - pq["low"].min())
        # Signed mean dH (direction bias)
        mean_sh = float(dd["sh"].mean())
        mean_sl = float(dd["sl"].mean())
        # Minutes above thresholds
        min_gt_p99 = int((dd["dh"] > p99_dh).sum())
        min_gt_p999 = int((dd["dh"] > p999_dh).sum())
        first_corrupt = dd[dd["dh"] > p999_dh].index.min()
        last_corrupt = dd[dd["dh"] > p999_dh].index.max()
        # Has both trades and databento?
        raw_path = raw_trades_path(d)
        db_path = databento_path(d)
        results.append({
            "date": d,
            "status": "OK",
            "has_trades": raw_path.exists(),
            "has_databento": db_path.exists(),
            "minutes_overlap": len(dd),
            "raw_H": float(raw["high"].max()),
            "pq_H": float(pq["high"].max()),
            "raw_L": float(raw["low"].min()),
            "pq_L": float(pq["low"].min()),
            "day_dH_tape_minus_pq_pts": day_dH,
            "day_dL_tape_minus_pq_pts": day_dL,
            "max_dh_1min_pts": max_dh,
            "max_dl_1min_pts": max_dl,
            "mean_dh_1min_pts": mean_dh,
            "mean_dl_1min_pts": mean_dl,
            "mean_signed_dH": mean_sh,
            "mean_signed_dL": mean_sl,
            "minutes_dh_gt_p99": min_gt_p99,
            "minutes_dh_gt_p99_9": min_gt_p999,
            "first_minute_corrupt": (first_corrupt.isoformat()
                                      if first_corrupt is not pd.NaT else ""),
            "last_minute_corrupt": (last_corrupt.isoformat()
                                      if last_corrupt is not pd.NaT else ""),
            "band": band,
        })
        if (i + 1) % 10 == 0 or (i + 1) == len(all_dates):
            summary = {"CLEAN": 0, "MINOR": 0, "MAJOR": 0, "CORRUPT": 0, "NO_DATA": 0}
            for r in results:
                summary[r.get("band", r.get("status", "NO_DATA"))] = summary.get(
                    r.get("band", r.get("status", "NO_DATA")), 0) + 1
            print(f"  [{i+1}/{len(all_dates)}] last={d} band={band} max_dh={max_dh:.2f} "
                  f"running={summary}")

    # Write CSV
    fields = ["date", "status", "has_trades", "has_databento", "band",
              "minutes_overlap", "raw_H", "pq_H", "raw_L", "pq_L",
              "day_dH_tape_minus_pq_pts", "day_dL_tape_minus_pq_pts",
              "max_dh_1min_pts", "max_dl_1min_pts",
              "mean_dh_1min_pts", "mean_dl_1min_pts",
              "mean_signed_dH", "mean_signed_dL",
              "minutes_dh_gt_p99", "minutes_dh_gt_p99_9",
              "first_minute_corrupt", "last_minute_corrupt"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    print(f"\nWrote {OUT_CSV}")

    # Final summary
    summary = {"CLEAN": 0, "MINOR": 0, "MAJOR": 0, "CORRUPT": 0, "NO_DATA": 0}
    for r in results:
        key = r.get("band") if r.get("status") == "OK" else r.get("status", "NO_DATA")
        summary[key] = summary.get(key, 0) + 1
    print(f"\n===== FINAL SUMMARY =====")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    # List MAJOR and CORRUPT dates
    print(f"\nMAJOR dates:")
    for r in results:
        if r.get("band") == "MAJOR":
            print(f"  {r['date']}  max_dh={r['max_dh_1min_pts']:.2f}  day_dH={r['day_dH_tape_minus_pq_pts']:.2f}  min_corrupt={r['minutes_dh_gt_p99_9']}")
    print(f"\nCORRUPT dates:")
    for r in results:
        if r.get("band") == "CORRUPT":
            print(f"  {r['date']}  max_dh={r['max_dh_1min_pts']:.2f}  day_dH={r['day_dH_tape_minus_pq_pts']:.2f}  min_corrupt={r['minutes_dh_gt_p99_9']}")


if __name__ == "__main__":
    main()

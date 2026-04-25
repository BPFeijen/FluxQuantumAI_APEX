"""Step 3b — Robust post-hoc reclassification using MAD-based thresholds.

The instruction §2 warns: if healthy subset is contaminated (it is —
2026-03-06 and 2026-04-02 polluted our random sample), percentile
thresholds derived from it are conservative upper bounds.

This script re-derives thresholds using only the 6 spot-check
D1 dates confirmed healthy by prior D1 investigation and outputs
a reclassification against those cleaner thresholds."""
from __future__ import annotations
import csv
from pathlib import Path
import pandas as pd
import numpy as np

RECON = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\daily_reconciliation_1min.csv")
THRESH_MD = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\divergence_thresholds_calibration.md")
OUT_MD = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\divergence_thresholds_robust.md")
OUT_CSV = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\daily_reconciliation_1min_robust.csv")

# Trusted healthy — the 6 spot-checks confirmed clean at <1pt day-level in D1
TRUSTED = ["2026-02-11", "2026-02-12", "2026-02-17", "2026-02-18",
           "2026-02-19", "2026-02-20"]


def main():
    df = pd.read_csv(RECON)
    df = df[df["status"] == "OK"].copy()

    # For the trusted subset, the healthy max_dh per day ranges:
    trusted = df[df["date"].isin(TRUSTED)]
    if trusted.empty:
        print("ERROR: no trusted rows")
        return

    print(f"Trusted subset: {len(trusted)} rows")
    print(trusted[["date", "max_dh_1min_pts", "day_dH_tape_minus_pq_pts"]].to_string())

    # Day-level metric: use day_max(dh_1min) as the classifier input.
    max_dh = trusted["max_dh_1min_pts"].values
    med = float(np.median(max_dh))
    mad = float(np.median(np.abs(max_dh - med)))
    p_max = float(max_dh.max())

    # Robust threshold: use MAD scale. Classical "outlier" = 3*MAD beyond median.
    # Bands:
    #   CLEAN  <= med + 2*MAD  (within 2 MADs — conservative normal)
    #   MINOR  <= med + 4*MAD
    #   MAJOR  <= med + 10*MAD  (clearly anomalous but plausibly explainable)
    #   CORRUPT > med + 10*MAD (qualitatively different from healthy)
    # Also cap by absolute: anything with day_dH > 20pt is CORRUPT regardless.
    THR_CLEAN = med + 2 * mad
    THR_MINOR = med + 4 * mad
    THR_MAJOR = med + 10 * mad
    ABS_CORRUPT = 20.0

    print(f"\nRobust thresholds from trusted subset:")
    print(f"  median(max_dh_1min) = {med:.2f}")
    print(f"  MAD                  = {mad:.2f}")
    print(f"  max of trusted      = {p_max:.2f}")
    print(f"  CLEAN  <= {THR_CLEAN:.2f}")
    print(f"  MINOR  <= {THR_MINOR:.2f}")
    print(f"  MAJOR  <= {THR_MAJOR:.2f}  (also: CORRUPT if |day_dH| > {ABS_CORRUPT:.0f})")

    # Reclassify
    def band(row):
        max_dh_d = float(row["max_dh_1min_pts"])
        day_dh = abs(float(row["day_dH_tape_minus_pq_pts"]))
        day_dl = abs(float(row["day_dL_tape_minus_pq_pts"]))
        absmax = max(day_dh, day_dl)
        if absmax > ABS_CORRUPT:
            return "CORRUPT"
        if max_dh_d <= THR_CLEAN:
            return "CLEAN"
        if max_dh_d <= THR_MINOR:
            return "MINOR"
        if max_dh_d <= THR_MAJOR:
            return "MAJOR"
        return "CORRUPT"

    df["band_robust"] = df.apply(band, axis=1)

    # Summary
    summary = df.groupby("band_robust").size()
    print(f"\nRobust classification summary:\n{summary}")

    # List all non-CLEAN dates
    print(f"\nAll non-CLEAN dates (robust reclassification):")
    nonclean = df[df["band_robust"] != "CLEAN"].sort_values("date")
    for _, r in nonclean.iterrows():
        print(f"  {r['date']}  {r['band_robust']:<8}  max_dh_1min={r['max_dh_1min_pts']:>7.2f}  "
              f"day_dH={r['day_dH_tape_minus_pq_pts']:>+7.2f}  day_dL={r['day_dL_tape_minus_pq_pts']:>+7.2f}  "
              f"min_>5pt={int(r['minutes_dh_gt_p99_9'])}")

    # Write out
    df.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV}")

    OUT_MD.write_text(
        f"""# Step 3b — Robust Reclassification (MAD-based)

**Generated:** post-hoc reclassification of daily_reconciliation_1min.csv

## Why this exists
The healthy subset used in Step 2 (divergence_thresholds_calibration.md)
contained contaminated dates (2026-03-06 max_dh=48pt; 2026-04-02 max_dh=48pt).
Percentile-based thresholds derived from it are biased conservative.

## Trusted-only subset (6 D1-spot-checked dates)
{trusted[['date', 'max_dh_1min_pts', 'day_dH_tape_minus_pq_pts']].to_string()}

## Robust statistics
- median(max_dh_1min) over trusted = {med:.3f}
- MAD (median absolute deviation)  = {mad:.3f}
- max of trusted                    = {p_max:.3f}

## Classification bands
- CLEAN   : day_max(dh_1min) <= med + 2*MAD = {THR_CLEAN:.2f}
- MINOR   : day_max(dh_1min) <= med + 4*MAD = {THR_MINOR:.2f}
- MAJOR   : day_max(dh_1min) <= med + 10*MAD = {THR_MAJOR:.2f}
- CORRUPT : above MAJOR threshold, OR |day_dH| > {ABS_CORRUPT:.0f} pts

## Robust counts
{summary.to_string()}

## Caveats
- The trusted subset has N=6 (small). MAD is noisy at small N.
- The absolute-override (|day_dH| > {ABS_CORRUPT}) is a hard backstop chosen
  because any day-level high/low mismatch > 20pt against authoritative tape
  represents a qualitatively different regime from per-minute timing jitter.
- Re-running this step after expanding the trusted subset (e.g. after Barbara
  validates more D1 highs visually) would tighten the MAD estimate.
""",
        encoding="utf-8",
    )
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()

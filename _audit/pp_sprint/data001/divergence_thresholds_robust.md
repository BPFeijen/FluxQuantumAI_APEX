# Step 3b — Robust Reclassification (MAD-based)

**Generated:** post-hoc reclassification of daily_reconciliation_1min.csv

## Why this exists
The healthy subset used in Step 2 (divergence_thresholds_calibration.md)
contained contaminated dates (2026-03-06 max_dh=48pt; 2026-04-02 max_dh=48pt).
Percentile-based thresholds derived from it are biased conservative.

## Trusted-only subset (6 D1-spot-checked dates)
          date  max_dh_1min_pts  day_dH_tape_minus_pq_pts
66  2026-02-11            10.15                      0.40
67  2026-02-12             7.45                      0.25
71  2026-02-17            13.35                      0.40
72  2026-02-18             7.90                      0.10
73  2026-02-19             8.70                      0.50
74  2026-02-20             5.05                      0.40

## Robust statistics
- median(max_dh_1min) over trusted = 8.300
- MAD (median absolute deviation)  = 1.350
- max of trusted                    = 13.350

## Classification bands
- CLEAN   : day_max(dh_1min) <= med + 2*MAD = 11.00
- MINOR   : day_max(dh_1min) <= med + 4*MAD = 13.70
- MAJOR   : day_max(dh_1min) <= med + 10*MAD = 21.80
- CORRUPT : above MAJOR threshold, OR |day_dH| > 20 pts

## Robust counts
band_robust
CLEAN      83
CORRUPT    15
MAJOR       7
MINOR       6

## Caveats
- The trusted subset has N=6 (small). MAD is noisy at small N.
- The absolute-override (|day_dH| > 20.0) is a hard backstop chosen
  because any day-level high/low mismatch > 20pt against authoritative tape
  represents a qualitatively different regime from per-minute timing jitter.
- Re-running this step after expanding the trusted subset (e.g. after Barbara
  validates more D1 highs visually) would tighten the MAD estimate.

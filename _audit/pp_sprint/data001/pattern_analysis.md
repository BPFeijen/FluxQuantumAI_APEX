# Step 5 — Pattern Analysis
**Source:** `daily_reconciliation_1min.csv` — 111 dates with OK status.

## Band distribution
band
CLEAN      25
CORRUPT     3
MAJOR       6
MINOR      77

## Monthly bucket counts (CLEAN / MINOR / MAJOR / CORRUPT)
band        CLEAN  CORRUPT  MAJOR  MINOR
year_month                              
2025-07         2        0      0      0
2025-08         2        0      0      0
2025-09         1        0      0      0
2025-10         4        0      0      0
2025-11         1        0      0      0
2025-12        14        0      1     12
2026-01         1        0      0     16
2026-02         0        0      3     17
2026-03         0        2      0     15
2026-04         0        1      2     17

## Direction bias (signed day_dH = tape_H − pq_H, by band)
         count       mean  median    min    max
band                                           
CLEAN       25  -0.944000   0.000 -23.60   0.00
CORRUPT      3   0.500000   0.400   0.05   1.05
MAJOR        6  21.408333   1.525   0.00  89.95
MINOR       77  -0.010390   0.350 -44.10   7.30

## Databento vs L2 breakdown
band     has_trades  has_databento
CLEAN    False       True             10
         True        False            15
CORRUPT  True        False             3
MAJOR    True        False             6
MINOR    True        False            77

## Pre- vs post-Databento→Quantower handover (2025-11-26)
Pre-handover:
band
CLEAN    10

Post-handover:
band
CLEAN      15
CORRUPT     3
MAJOR       6
MINOR      77

## Day-of-week distribution for MAJOR+CORRUPT
dow      Friday  Thursday  Tuesday  Wednesday
band                                         
CORRUPT       1         2        0          0
MAJOR         1         2        1          2

## First-corrupt-minute hour-of-day distribution for MAJOR+CORRUPT
first_corrupt_hour
13.0    1
14.0    1
15.0    1

## All MAJOR + CORRUPT dates with rollover proximity
| date | band | day_dH | day_dL | min_>p99.9 | nearest_rollover | days_to_rollover |
|---|---|---|---|---|---|---|
| 2025-12-10 | MAJOR | +0.00 | +0.00 | 0 | 2025-11-26 | 14 |
| 2026-02-05 | MAJOR | +0.15 | -0.50 | 0 | 2026-01-29 | 7 |
| 2026-02-13 | MAJOR | +35.30 | -0.40 | 0 | 2026-01-29 | 15 |
| 2026-02-26 | MAJOR | +0.40 | -12.75 | 0 | 2026-01-29 | 28 |
| 2026-03-06 | CORRUPT | +1.05 | -1.00 | 27 | 2026-03-27 | 21 |
| 2026-03-19 | CORRUPT | +0.40 | -0.65 | 9 | 2026-03-27 | 8 |
| 2026-04-02 | CORRUPT | +0.05 | -0.45 | 3 | 2026-03-27 | 6 |
| 2026-04-07 | MAJOR | +89.95 | -9.75 | 0 | 2026-03-27 | 11 |
| 2026-04-08 | MAJOR | +2.65 | -3.95 | 0 | 2026-03-27 | 12 |

# Step 2 — Data-driven divergence thresholds (per DATA-001 §3 Step 2)

**Generated:** run of step2_3_reconcile.py
**Healthy subset:** 26 dates
- 6 D1 spot-checks: 2026-02-11, 2026-02-12, 2026-02-17, 2026-02-18, 2026-02-19, 2026-02-20
- 20 random (seed=42): 2026-03-27, 2025-12-16, 2025-12-04, 2026-04-13, 2026-01-09, 2026-01-05, 2026-01-01, 2025-12-19, 2025-12-15, 2026-04-02, 2026-03-06, 2025-12-13, 2026-03-18, 2026-02-09, 2025-12-05, 2025-12-31, 2026-01-02, 2026-03-01, 2026-03-20, 2026-03-09

**Healthy combined minutes:** 29,518

## Per-minute delta distribution across healthy subset

### price_delta_high_pts (|tape_H - pq_H| per minute)
p50.0      0.150000
p75.0      0.300000
p90.0      0.550000
p95.0      0.900000
p99.0     24.133000
p99.5     31.920750
p99.9     38.648300
p99.99    47.269320
max       48.000000
mean       0.667559
median     0.150000

### price_delta_low_pts (|tape_L - pq_L| per minute)
p50.0      0.150000
p75.0      0.300000
p90.0      0.550000
p95.0      0.950000
p99.0     20.191500
p99.5     28.970750
p99.9     36.948300
p99.99    41.050000
max       43.000000
mean       0.635316
median     0.150000

### volume_delta_pct (|tape_V - pq_V| / tape_V)
p50.0       0.000000
p75.0       0.040000
p90.0       0.166667
p95.0       0.349177
p99.0       0.800000
p99.5       1.573958
p99.9       5.461704
p99.99     96.024150
max       267.000000
mean        0.102482
median      0.000000

## Classification bands (derived)

- CLEAN   : day max(dh) <= p95  = 0.900 pts
- MINOR   : p95 < day max(dh) <= p99  = 24.133 pts
- MAJOR   : p99 < day max(dh) <= p99.9 = 38.648 pts
- CORRUPT : day max(dh) > p99.9 = 38.648 pts

## Notes on distribution shape

- Heavy-tailed? max (48.00) vs p99.9 (38.648): ratio 1.24
- If ratio >> 3, the healthy subset itself contains suspect minutes and thresholds are a
  conservative upper bound for "normal".

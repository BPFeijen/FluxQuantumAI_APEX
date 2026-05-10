# G-PURDUE Step 6 - Outlier robustness check

## Outlier days removed

Top 10 days by intraday range (high - low):

| Date | Range (pts) |
|---|---:|
| 2026-04-21 | 163.7 |
| 2026-04-28 | 148.9 |
| 2026-05-06 | 136.3 |
| 2026-04-17 | 123.7 |
| 2026-04-30 | 106.6 |
| 2026-05-07 | 104.0 |
| 2026-05-01 | 103.0 |
| 2026-04-19 | 102.1 |
| 2026-04-29 | 101.3 |
| 2026-04-14 | 98.5 |

## Winner candidate

`(min_bars=4, window=8, strategy=recency_weighted)`

## Robustness comparison

| Metric | Full corpus | Without outliers | Delta |
|---|---:|---:|---:|
| acc_mean | 0.5393 | 0.5275 | +0.0119 |
| 95% CI lo | 0.5322 | 0.5175 | +0.0147 |
| 95% CI hi | 0.5465 | 0.5376 | +0.0088 |
| Signals analyzed | 37,234 | 20,951 | -16283 |

## Verdict

**Outlier-sensitive.** Removing top 10 days changes accuracy by +0.0119; consider whether outliers are representative or need separate handling.

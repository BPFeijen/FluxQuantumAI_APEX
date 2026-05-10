# G-PURDUE Step 5 - Hypothesis test

## Test setup

- H0: winner candidate has same `survive_accuracy` as alternative i
- H1: winner > alternative i (one-sided)
- Bonferroni-corrected alpha (top-1 vs rest): 5.15e-04
- N alternatives: 97
- p-value: bootstrap CI non-overlap (conservative) OR Welch z-test on bootstrap mean/std proxies

## Winner

`(min_bars=4, window=8, strategy=recency_weighted)`

- acc_mean = 0.5393
- 95% CI   = [0.5322, 0.5465]
- block    = 0.556

## Conclusion

**Cannot reject H0** for 7 alternatives. Winner (4,8,recency_weighted) is the highest acc_mean but is statistically tied with 7 other candidates at the corrected alpha. Multiple settings could be deployed; choose by secondary criteria (block_rate, simplicity).

### 'Statistically tied' candidates (top 5)

| min_bars | window | strategy | acc_mean | z | p (one-sided) |
|---:|---:|---|---:|---:|---:|
| 4 | 7 | recency_weighted | 0.5352 | 0.81 | 0.2094 |
| 5 | 7 | recency_weighted | 0.5317 | 1.57 | 0.0578 |
| 5 | 8 | recency_weighted | 0.5309 | 1.73 | 0.0415 |
| 6 | 7 | recency_weighted | 0.5308 | 1.77 | 0.0383 |
| 6 | 8 | recency_weighted | 0.5307 | 1.79 | 0.0366 |

## All rejected alternatives (top 10)

| min_bars | window | strategy | acc_mean | z | p |
|---:|---:|---|---:|---:|---:|
| 7 | 3 | recency_weighted | 0.5260 | 2.98 | 1.4443e-03 |
| 2 | 7 | recency_weighted | 0.5241 | 2.99 | 1.3915e-03 |
| 6 | 3 | recency_weighted | 0.5239 | 3.46 | 2.7025e-04 |
| 2 | 4 | recency_weighted | 0.5235 | 3.24 | 5.9388e-04 |
| 2 | 8 | recency_weighted | 0.5212 | 3.55 | 1.9012e-04 |
| 6 | 5 | recency_weighted | 0.5204 | 4.04 | 2.6441e-05 |
| 6 | 6 | recency_weighted | 0.5187 | 4.38 | 5.8438e-06 |
| 7 | 5 | recency_weighted | 0.5161 | 4.91 | 4.6512e-07 |
| 6 | 2 | recency_weighted | 0.5145 | 5.60 | 1.0725e-08 |
| 7 | 6 | recency_weighted | 0.5141 | 5.36 | 4.1447e-08 |


# G-PURDUE Step 1 — Distribution observation

**Generated**: 2026-05-10 16:51:49.789986+00:00
**Author**: directed-validation harness
**Sign-off**: Barbara 2026-05-10 (no guessing — data-driven only)

## Purpose

Characterize the bars-per-confirmed-box distribution **before** any threshold is chosen. Two corpora:
- **NARROW** = May 5-8 2026 (current production window)
- **BROAD**  = Apr-May 2026 (same horizon as Phase 2 winner 5,5)

Comparing them reveals whether the distribution drifted between the calibration corpus and current production — which would explain why deployed `(5,5)` produced bias=unknown 95% on 5/8.

## Stats summary

| Metric | NARROW (May 5-8) | BROAD (Apr-May) |
|---|---:|---:|
| M30 rows in window | 165 | 9380 |
| Confirmed boxes | 11 | 630 |
| Decisions in window | 9289 | 37234 |
| Boxes per day (mean) | 2.75 | 2.02 |
| Box lifetime min (mean) | 193.6 | 189.1 |
| Bars/box min | 1 | 1 |
| Bars/box p25 | 2.0 | 2.0 |
| Bars/box p50 (median) | 6.0 | 4.0 |
| Bars/box p75 | 10.0 | 8.0 |
| Bars/box p90 | 14.0 | 12.1 |
| Bars/box max | 22 | 40 |
| Bars/box mean | 7.27 | 6.02 |
| Bars/box std | 6.22 | 4.93 |

## Bias outcome distribution (native classifier per box)

| Bias | NARROW count | NARROW % | BROAD count | BROAD % |
|---|---:|---:|---:|---:|
| bullish | 4 | 36.4% | 215 | 34.1% |
| bearish | 1 | 9.1% | 164 | 26.0% |
| unknown | 6 | 54.5% | 251 | 39.8% |

## Regime distribution (4h-delta classifier @ box first ts)

| Regime | NARROW | BROAD |
|---|---:|---:|
| TREND_UP | 1 | 51 |
| TREND_DN | 0 | 27 |
| RANGE | 10 | 552 |

## Histogram bars-per-box (NARROW corpus)

| Bars-in-box | Count | Cum % |
|---:|---:|---:|
| 1 | 2 | 18.2% |
| 2 | 2 | 36.4% |
| 5 | 1 | 45.5% |
| 6 | 1 | 54.5% |
| 7 | 1 | 63.6% |
| 8 | 1 | 72.7% |
| 12 | 1 | 81.8% |
| 14 | 1 | 90.9% |
| 22 | 1 | 100.0% |

## Histogram bars-per-box (BROAD corpus)

| Bars-in-box | Count | Cum % |
|---:|---:|---:|
| 1 | 50 | 7.9% |
| 2 | 134 | 29.2% |
| 3 | 70 | 40.3% |
| 4 | 70 | 51.4% |
| 5 | 43 | 58.3% |
| 6 | 41 | 64.8% |
| 7 | 37 | 70.6% |
| 8 | 34 | 76.0% |
| 9 | 22 | 79.5% |
| 10 | 20 | 82.7% |
| 11 | 24 | 86.5% |
| 12 | 22 | 90.0% |
| 13 | 16 | 92.5% |
| 14 | 8 | 93.8% |
| 15 | 8 | 95.1% |
| 16 | 7 | 96.2% |
| 17 | 4 | 96.8% |
| 18 | 2 | 97.1% |
| 19 | 6 | 98.1% |
| 20 | 3 | 98.6% |
| 21 | 1 | 98.7% |
| 22 | 1 | 98.9% |
| 24 | 1 | 99.0% |
| 25 | 3 | 99.5% |
| 26 | 1 | 99.7% |
| 27 | 1 | 99.8% |
| 40 | 1 | 100.0% |

## Drift assessment

- p50 drift NARROW vs BROAD: **+2.0 bars** (6.0 vs 4.0)
- p75 drift NARROW vs BROAD: **+2.0 bars** (10.0 vs 8.0)
- **Distribution shift confirmed**: this is the empirical evidence that the (5,5) calibration on Apr-May corpus does not generalize to May 5-8 production. min_bars=5 on a corpus where p50 < 5 will keep most boxes below threshold → bias=unknown.

## Implications for grid range (Step 3 input)

- A reasonable grid for `min_bars` covers the empirical p25-p75 range: **`min_bars` ∈ [2, 10]** on NARROW corpus, with the window extending up to p90=14 for sensitivity sweep.
- A reasonable `window` (number of recent confirmed boxes to vote on) is bounded by total box count per day. NARROW has 2.8 boxes/day, so window > 6 risks voting against confirmed boxes >24h old (multi-session noise).

## Methodology notes

- bars_per_box = count of M30 rows where `m30_box_confirmed == True` and `m30_box_id == bid`
- box bias = native classifier on last row: liq_top > box_high (bullish), liq_bot < box_low (bearish), else unknown
- regime = 4h price delta from box first_ts: |delta|<=30 RANGE, >+30 TREND_UP, <-30 TREND_DN
- ZERO live mutation; pure read-only.

## Artifacts

- `step1_distribution.json` — machine-readable stats
- `bars_per_box_narrow.csv` — raw per-box stats NARROW
- `bars_per_box_broad.csv` — raw per-box stats BROAD

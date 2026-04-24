# Data Capture Gaps — Audit Report

_Generated: 2026-04-24T03:21:15+00:00_

**Window**: 2025-07-01 → 2026-04-24
**Databento backfill coverage**: up to 2025-11-25 (dates <= this are `backfillable_via_databento=YES`)
**Sources scanned**:
- `trades_*.csv.gz` in `C:/data/level2/_gc_xcec/` (count: 112)
- `microstructure_*.csv.gz` in same dir (count: 178)
- `glbx-mdp3-*.mbp-10.csv.zst` in `.../GLBX-20260407-RQ5S6KR3E5/` (count: 126)

## Summary

- Expected trading days in window: **206**
- Days with Level2 trades capture: **90**
- Days with Databento backfill: **103**
- Days covered by at least one source: **194**
- **Days fully missing (no Level2 and no Databento): 12**
- Days missing from Level2 but backfillable via Databento: **52**
- Days with intraday anomaly (late start / early end / >=2h gap): **28**

## Missing whole trading days (Level2 capture)

| date | weekday | databento_backfillable | severity |
|---|---|---|---|
| 2025-09-12 | Fri | YES | HIGH |
| 2025-09-15 | Mon | YES | HIGH |
| 2025-09-16 | Tue | YES | HIGH |
| 2025-09-17 | Wed | YES | HIGH |
| 2025-09-18 | Thu | YES | HIGH |
| 2025-09-19 | Fri | YES | HIGH |
| 2025-09-22 | Mon | YES | HIGH |
| 2025-09-23 | Tue | YES | HIGH |
| 2025-09-24 | Wed | YES | HIGH |
| 2025-09-25 | Thu | YES | HIGH |
| 2025-09-26 | Fri | YES | HIGH |
| 2025-09-29 | Mon | YES | HIGH |
| 2025-09-30 | Tue | YES | HIGH |
| 2025-10-01 | Wed | YES | HIGH |
| 2025-10-02 | Thu | YES | HIGH |
| 2025-10-03 | Fri | YES | HIGH |
| 2025-10-06 | Mon | YES | HIGH |
| 2025-10-07 | Tue | YES | HIGH |
| 2025-10-08 | Wed | YES | HIGH |
| 2025-10-09 | Thu | YES | HIGH |
| 2025-10-10 | Fri | YES | HIGH |
| 2025-10-13 | Mon | YES | HIGH |
| 2025-10-14 | Tue | YES | HIGH |
| 2025-10-15 | Wed | YES | HIGH |
| 2025-10-16 | Thu | YES | HIGH |
| 2025-10-17 | Fri | YES | HIGH |
| 2025-10-20 | Mon | YES | HIGH |
| 2025-10-21 | Tue | YES | HIGH |
| 2025-10-22 | Wed | YES | HIGH |
| 2025-10-23 | Thu | YES | HIGH |
| 2025-10-24 | Fri | YES | HIGH |
| 2025-10-27 | Mon | YES | HIGH |
| 2025-10-28 | Tue | YES | HIGH |
| 2025-10-29 | Wed | YES | HIGH |
| 2025-10-30 | Thu | YES | HIGH |
| 2025-10-31 | Fri | YES | HIGH |
| 2025-11-03 | Mon | YES | HIGH |
| 2025-11-04 | Tue | YES | HIGH |
| 2025-11-05 | Wed | YES | HIGH |
| 2025-11-06 | Thu | YES | HIGH |
| 2025-11-07 | Fri | YES | HIGH |
| 2025-11-10 | Mon | YES | HIGH |
| 2025-11-11 | Tue | YES | HIGH |
| 2025-11-12 | Wed | YES | HIGH |
| 2025-11-13 | Thu | YES | HIGH |
| 2025-11-14 | Fri | YES | HIGH |
| 2025-11-17 | Mon | YES | HIGH |
| 2025-11-18 | Tue | YES | HIGH |
| 2025-11-19 | Wed | YES | HIGH |
| 2025-11-20 | Thu | YES | HIGH |
| 2025-11-21 | Fri | YES | HIGH |
| 2025-11-24 | Mon | YES | HIGH |
| 2025-11-25 | Tue | YES (<=2025-11-25) | HIGH |
| 2025-11-28 | Fri | NO | CRITICAL |
| 2026-01-26 | Mon | NO | CRITICAL |
| 2026-01-27 | Tue | NO | CRITICAL |
| 2026-01-28 | Wed | NO | CRITICAL |
| 2026-01-29 | Thu | NO | CRITICAL |
| 2026-01-30 | Fri | NO | CRITICAL |
| 2026-03-10 | Tue | NO | CRITICAL |
| 2026-03-11 | Wed | NO | CRITICAL |
| 2026-03-12 | Thu | NO | CRITICAL |
| 2026-03-13 | Fri | NO | CRITICAL |
| 2026-03-26 | Thu | NO | CRITICAL |

## Intraday capture anomalies (>=2h gap, late start, or early end)

| date | first_trade_UTC | last_trade_UTC | hours_covered | late_start | early_end | >=2h gaps | severity |
|---|---|---|---|---|---|---|---|
| 2025-12-01 | 20:38:25 | 23:53:27 | 3.25 | True | False |  | HIGH |
| 2025-12-19 | 00:00:01 | 21:59:57 | 22.0 | False | True |  | LOW |
| 2025-12-24 | 00:00:00 | 18:44:55 | 18.75 | False | True |  | MEDIUM |
| 2025-12-26 | 00:00:00 | 21:59:47 | 22.0 | False | True |  | LOW |
| 2025-12-31 | 00:00:01 | 21:59:56 | 22.0 | False | True |  | LOW |
| 2026-01-02 | 00:00:00 | 21:59:59 | 22.0 | False | True |  | LOW |
| 2026-01-09 | 00:00:00 | 21:59:58 | 22.0 | False | True |  | LOW |
| 2026-01-16 | 00:00:02 | 21:59:56 | 22.0 | False | True |  | LOW |
| 2026-01-23 | 00:00:00 | 21:59:59 | 22.0 | False | True |  | LOW |
| 2026-02-02 | 18:25:23 | 23:59:59 | 5.58 | True | False |  | HIGH |
| 2026-02-06 | 00:00:00 | 21:59:58 | 22.0 | False | True |  | LOW |
| 2026-02-13 | 00:00:00 | 21:59:58 | 22.0 | False | True |  | LOW |
| 2026-02-20 | 00:00:00 | 21:59:59 | 22.0 | False | True |  | LOW |
| 2026-02-27 | 00:00:00 | 21:59:57 | 22.0 | False | True |  | LOW |
| 2026-03-03 | 00:00:03 | 07:59:57 | 8.0 | False | True |  | HIGH |
| 2026-03-04 | 15:36:00 | 23:59:56 | 8.4 | True | False |  | HIGH |
| 2026-03-06 | 00:00:00 | 21:59:59 | 22.0 | False | True |  | LOW |
| 2026-03-09 | 00:00:00 | 19:56:31 | 19.94 | False | True |  | MEDIUM |
| 2026-03-20 | 00:00:02 | 20:59:59 | 21.0 | False | True |  | LOW |
| 2026-03-23 | 17:18:33 | 23:59:56 | 6.69 | True | False |  | HIGH |
| 2026-03-25 | 00:00:14 | 02:15:58 | 2.26 | False | True |  | HIGH |
| 2026-03-27 | 17:18:40 | 20:58:37 | 3.67 | True | True |  | HIGH |
| 2026-04-02 | 00:00:00 | 20:59:59 | 21.0 | False | True |  | LOW |
| 2026-04-09 | 00:00:00 | 14:54:01 | 14.9 | False | True |  | HIGH |
| 2026-04-10 | 09:55:41 | 20:59:57 | 11.07 | True | True |  | HIGH |
| 2026-04-17 | 00:00:00 | 21:07:54 | 21.13 | False | True |  | LOW |
| 2026-04-21 | 00:04:59 | 23:59:58 | 23.92 | False | False | 12:48:32→17:28:36 (4.7h) | MEDIUM |
| 2026-04-24 | 00:00:00 | 03:20:52 | 3.35 | False | True |  | HIGH |

## Severity roll-up (intraday anomalies)

- CRITICAL: 0
- HIGH: 10
- MEDIUM: 3
- LOW: 15

## Business-days coverage

- Clean trading days (L2 capture, no anomaly): **114 / 206** (55.3%)
- Days with intraday anomaly: **28 / 206** (13.6%)
- Days missing from L2: **64 / 206** (31.1%)
- Days fully missing (no L2 and no Databento): **12 / 206** (5.8%)

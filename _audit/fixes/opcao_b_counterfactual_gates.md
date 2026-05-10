# Opção B counterfactual gates — Phase 4 STEP 6+7

**Author**: CC#3
**Date**: 2026-05-07
**Asana**: BUG-SIGNAL-INVERTED 1214556369070092 -> ML-DS comment 1214603317697253
**Spec ref**: `_audit/fixes/opcao_b_phase1_m30_bias_hysteresis_spec.md` § 6.4
**Live config**: `min_bars=4, window=4, strategy=recency_weighted`

---

## Methodology

- Imports `live.level_detector.derive_m30_bias` (now F-1+F-3 active)
- For each historical decision in gate window: slice m30 parquet up to decision ts; call `derive_m30_bias`; mark counter-trend signals as 'blocked' (LONG with bearish bias OR SHORT with bullish bias)
- ZERO live mutation; no production calls

---

## Gate G1 — 2026-05-07 04:00-05:00 UTC SHORTs burst

- Population: SHORTs in window = **371**
- Bias distribution: `{'bullish': 371}`
- Blocked under live F-1+F-3: **371** (100.00%)
- Threshold: >=80%
- **Gate G1**: PASS

## Gate G2 — Box 5282 04:30-05:30 UTC

- Population: SHORTs in window = **388**
- Bias distribution: `{'bullish': 388}`
- Blocked under live F-1+F-3: **388** (100.00%)
- Threshold: >=95%
- **Gate G2**: PASS

---

## Verdict

**Both gates PASS** — deploy is GREEN-LIT.

Proceed to Phase 4 STEP 9: NSSM restart + 5min observation.

---

## Per-decision detail (G1)

| ts | direction | bias_new | is_conf | blocked |
|---|---|---|---|---|
| 2026-05-07 04:01:29 | LONG | bullish | True | False |
| 2026-05-07 04:01:29 | LONG | bullish | True | False |
| 2026-05-07 04:01:32 | LONG | bullish | True | False |
| 2026-05-07 04:01:32 | LONG | bullish | True | False |
| 2026-05-07 04:01:33 | LONG | bullish | True | False |
| 2026-05-07 04:01:33 | LONG | bullish | True | False |
| 2026-05-07 04:01:33 | LONG | bullish | True | False |
| 2026-05-07 04:01:33 | LONG | bullish | True | False |
| 2026-05-07 04:01:37 | SHORT | bullish | True | True |
| 2026-05-07 04:01:38 | SHORT | bullish | True | True |
| 2026-05-07 04:01:39 | SHORT | bullish | True | True |
| 2026-05-07 04:01:40 | SHORT | bullish | True | True |
| 2026-05-07 04:01:41 | SHORT | bullish | True | True |
| 2026-05-07 04:01:42 | SHORT | bullish | True | True |
| 2026-05-07 04:01:43 | SHORT | bullish | True | True |
| 2026-05-07 04:01:44 | SHORT | bullish | True | True |
| 2026-05-07 04:01:45 | SHORT | bullish | True | True |
| 2026-05-07 04:01:46 | SHORT | bullish | True | True |
| 2026-05-07 04:01:47 | SHORT | bullish | True | True |
| 2026-05-07 04:01:48 | SHORT | bullish | True | True |
| 2026-05-07 04:01:49 | SHORT | bullish | True | True |
| 2026-05-07 04:01:50 | SHORT | bullish | True | True |
| 2026-05-07 04:01:55 | SHORT | bullish | True | True |
| 2026-05-07 04:01:56 | SHORT | bullish | True | True |
| 2026-05-07 04:01:57 | SHORT | bullish | True | True |
| 2026-05-07 04:01:58 | SHORT | bullish | True | True |
| 2026-05-07 04:01:59 | SHORT | bullish | True | True |
| 2026-05-07 04:02:00 | SHORT | bullish | True | True |
| 2026-05-07 04:06:07 | SHORT | bullish | True | True |
| 2026-05-07 04:06:07 | SHORT | bullish | True | True |
| 2026-05-07 04:06:17 | SHORT | bullish | True | True |
| 2026-05-07 04:06:20 | SHORT | bullish | True | True |
| 2026-05-07 04:06:21 | SHORT | bullish | True | True |
| 2026-05-07 04:06:22 | SHORT | bullish | True | True |
| 2026-05-07 04:06:23 | SHORT | bullish | True | True |
| 2026-05-07 04:06:24 | SHORT | bullish | True | True |
| 2026-05-07 04:06:25 | SHORT | bullish | True | True |
| 2026-05-07 04:06:26 | SHORT | bullish | True | True |
| 2026-05-07 04:06:27 | SHORT | bullish | True | True |
| 2026-05-07 04:06:28 | SHORT | bullish | True | True |
| 2026-05-07 04:06:29 | SHORT | bullish | True | True |
| 2026-05-07 04:06:30 | SHORT | bullish | True | True |
| 2026-05-07 04:06:31 | SHORT | bullish | True | True |
| 2026-05-07 04:06:32 | SHORT | bullish | True | True |
| 2026-05-07 04:06:33 | SHORT | bullish | True | True |
| 2026-05-07 04:06:34 | SHORT | bullish | True | True |
| 2026-05-07 04:06:35 | SHORT | bullish | True | True |
| 2026-05-07 04:06:36 | SHORT | bullish | True | True |
| 2026-05-07 04:06:37 | SHORT | bullish | True | True |
| 2026-05-07 04:06:44 | SHORT | bullish | True | True |
| ... | (331 more rows) | | | |

## Per-decision detail (G2)

| ts | direction | bias_new | is_conf | blocked |
|---|---|---|---|---|
| 2026-05-07 04:40:01 | SHORT | bullish | True | True |
| 2026-05-07 04:40:02 | SHORT | bullish | True | True |
| 2026-05-07 04:40:03 | SHORT | bullish | True | True |
| 2026-05-07 04:40:09 | SHORT | bullish | True | True |
| 2026-05-07 04:40:09 | SHORT | bullish | True | True |
| 2026-05-07 04:40:10 | SHORT | bullish | True | True |
| 2026-05-07 04:40:10 | SHORT | bullish | True | True |
| 2026-05-07 04:41:29 | SHORT | bullish | True | True |
| 2026-05-07 04:41:29 | SHORT | bullish | True | True |
| 2026-05-07 04:41:39 | SHORT | bullish | True | True |
| 2026-05-07 04:41:39 | SHORT | bullish | True | True |
| 2026-05-07 04:41:40 | SHORT | bullish | True | True |
| 2026-05-07 04:41:40 | SHORT | bullish | True | True |
| 2026-05-07 04:41:41 | SHORT | bullish | True | True |
| 2026-05-07 04:41:41 | SHORT | bullish | True | True |
| 2026-05-07 04:41:43 | SHORT | bullish | True | True |
| 2026-05-07 04:41:43 | SHORT | bullish | True | True |
| 2026-05-07 04:41:44 | SHORT | bullish | True | True |
| 2026-05-07 04:41:44 | SHORT | bullish | True | True |
| 2026-05-07 04:41:47 | SHORT | bullish | True | True |
| 2026-05-07 04:41:47 | SHORT | bullish | True | True |
| 2026-05-07 04:41:48 | SHORT | bullish | True | True |
| 2026-05-07 04:41:48 | SHORT | bullish | True | True |
| 2026-05-07 04:41:49 | SHORT | bullish | True | True |
| 2026-05-07 04:41:49 | SHORT | bullish | True | True |
| 2026-05-07 04:41:50 | SHORT | bullish | True | True |
| 2026-05-07 04:41:50 | SHORT | bullish | True | True |
| 2026-05-07 04:43:06 | SHORT | bullish | True | True |
| 2026-05-07 04:43:06 | SHORT | bullish | True | True |
| 2026-05-07 04:43:14 | SHORT | bullish | True | True |
| 2026-05-07 04:43:14 | SHORT | bullish | True | True |
| 2026-05-07 04:43:20 | SHORT | bullish | True | True |
| 2026-05-07 04:43:20 | SHORT | bullish | True | True |
| 2026-05-07 04:43:25 | SHORT | bullish | True | True |
| 2026-05-07 04:43:25 | SHORT | bullish | True | True |
| 2026-05-07 04:43:28 | SHORT | bullish | True | True |
| 2026-05-07 04:43:28 | SHORT | bullish | True | True |
| 2026-05-07 04:43:29 | SHORT | bullish | True | True |
| 2026-05-07 04:43:29 | SHORT | bullish | True | True |
| 2026-05-07 04:43:31 | SHORT | bullish | True | True |
| 2026-05-07 04:43:31 | SHORT | bullish | True | True |
| 2026-05-07 04:43:31 | SHORT | bullish | True | True |
| 2026-05-07 04:43:31 | SHORT | bullish | True | True |
| 2026-05-07 04:43:32 | SHORT | bullish | True | True |
| 2026-05-07 04:43:32 | SHORT | bullish | True | True |
| 2026-05-07 04:44:45 | SHORT | bullish | True | True |
| 2026-05-07 04:44:45 | SHORT | bullish | True | True |
| 2026-05-07 04:44:51 | SHORT | bullish | True | True |
| 2026-05-07 04:44:51 | SHORT | bullish | True | True |
| 2026-05-07 04:44:54 | SHORT | bullish | True | True |
| ... | (338 more rows) | | | |

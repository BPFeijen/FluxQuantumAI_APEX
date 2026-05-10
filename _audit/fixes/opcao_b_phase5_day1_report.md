# Opção B — Phase 5 Day 1 monitoring report

**Generated**: 2026-05-08T16:40:03+00:00
**Deploy timestamp**: 2026-05-07 10:57:00+00:00
**Time elapsed since deploy**: 1 day, 5:43:03.570018

## Live service state

- pid: `30844`
- status: `ALIVE`
- last heartbeat: `2026-05-08T15:58:38.865279+00:00`
- m30_bias: `unknown` (confirmed: `False`)
- provisional_m30_bias: `bearish`
- daily_trend: `unknown`
- delta_4h: `70.0` | phase: `EXPANSION`
- feed_age_s: `1.3` | m30_age_s: `4.0`

## Decision activity since deploy

- Total decisions: **4242**
- Block/SKIP decisions: 1294 (30.5%)
- Of which BIAS_BLOCK/BIAS_FILTER reason: 0

### m30_bias distribution (post-deploy)

- bullish: 335 (7.9%)
- bearish: 0 (0.0%)
- unknown: 3907 (92.1%)

### Direction distribution

- SHORT: 3134
- LONG: 1108

### Action distribution

- GO: 1534
- EXEC_FAILED: 1414
- BLOCK: 1294

## m30_bias flip frequency

- Total flips since deploy: **7**

Recent flips (last 20):

- `2026-05-07 17:17:03.332722+00:00` bullish -> unknown
- `2026-05-07 20:39:52.011659+00:00` unknown -> bullish
- `2026-05-07 22:40:35.942189+00:00` bullish -> unknown
- `2026-05-07 23:46:46.816336+00:00` unknown -> bullish
- `2026-05-08 00:19:10.093597+00:00` bullish -> unknown
- `2026-05-08 01:37:05.504260+00:00` unknown -> bullish
- `2026-05-08 02:35:23.622603+00:00` bullish -> unknown

## Per-regime bias distribution

| Regime | n | bullish | bearish | unknown |
|---|---|---|---|---|
| RANGE | 1271 | 63 | 0 | 1208 |
| TREND_DN | 1391 | 182 | 0 | 1209 |
| TREND_UP | 1580 | 90 | 0 | 1490 |

## Box 5266-style pattern alert

**Pattern**: regime=RANGE AND m30_bias=bearish (potential over-blocking in flat market)

- Occurrences since deploy: **0**

---

*Re-run this script periodically to refresh metrics through 24h Phase 5 window.*
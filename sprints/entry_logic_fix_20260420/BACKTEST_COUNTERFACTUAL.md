# Backtest Counterfactual -- near_level direction-aware (literatura)

Sprint: entry_logic_fix_20260420
Methodology: approximate replay using recorded trigger.level_price_mt5 vs price_mt5.
See module docstring for limitations.

Source log: `C:\FluxQuantumAI\logs\decision_log.jsonl`

## Summary stats

- **total**: 11978
- **go_signals**: 659
- **json_error**: 0
- **IDENTICAL_APPROX**: 514
- **NEW_REJECT_WRONG_SIDE**: 145
- **CANNOT_REPLAY**: 0
- **NEW_REJECT rate on GO signals**: 22.00%

## Breakdown by direction

| label | direction | count |
|---|---|---|
| IDENTICAL_APPROX | SHORT | 373 |
| IDENTICAL_APPROX | LONG | 141 |
| NEW_REJECT_WRONG_SIDE | SHORT | 103 |
| NEW_REJECT_WRONG_SIDE | LONG | 42 |

## Breakdown by trigger type

| label | trigger | count |
|---|---|---|
| IDENTICAL_APPROX | ALPHA | 514 |
| NEW_REJECT_WRONG_SIDE | ALPHA | 145 |

## Target signal 03:14:46 UTC 2026-04-20

```json
{
  "label": "NEW_REJECT_WRONG_SIDE",
  "ts": "2026-04-20T03:14:46.448214+00:00",
  "direction": "SHORT",
  "price_mt5": 4791.08,
  "level_type": "liq_top",
  "level_price_mt5": 4767.53,
  "trigger_type": "ALPHA",
  "delta_wrong_side": 23.55
}
```

**Result: REJECTED under C1 post-validation** (wrong-side level).

## Rejected samples (up to 50)

| ts | direction | level_type | level_price | price | delta_wrong_side |
|---|---|---|---|---|---|
| 2026-04-15T06:30:18.609271+00:00 | LONG | liq_bot | 4808.8 | 4807.5 | 1.3 |
| 2026-04-15T06:34:43.833387+00:00 | LONG | liq_bot | 4807.25 | 4806.45 | 0.8 |
| 2026-04-15T07:07:22.889220+00:00 | LONG | liq_bot | 4803.8 | 4803.0 | 0.8 |
| 2026-04-15T07:11:50.256007+00:00 | LONG | liq_bot | 4803.8 | 4800.45 | 3.35 |
| 2026-04-15T07:24:07.131522+00:00 | LONG | liq_bot | 4803.8 | 4802.9 | 0.9 |
| 2026-04-15T07:29:49.722425+00:00 | LONG | liq_bot | 4803.8 | 4803.3 | 0.5 |
| 2026-04-15T07:32:01.687959+00:00 | LONG | liq_bot | 4803.8 | 4801.0 | 2.8 |
| 2026-04-15T07:38:50.720690+00:00 | LONG | liq_bot | 4803.8 | 4803.4 | 0.4 |
| 2026-04-15T07:47:49.893579+00:00 | LONG | liq_bot | 4803.8 | 4801.95 | 1.85 |
| 2026-04-15T07:54:27.761479+00:00 | LONG | liq_bot | 4803.8 | 4799.55 | 4.25 |
| 2026-04-15T07:56:39.172598+00:00 | LONG | liq_bot | 4803.8 | 4800.45 | 3.35 |
| 2026-04-15T08:01:12.705182+00:00 | LONG | liq_bot | 4803.8 | 4802.1 | 1.7 |
| 2026-04-15T08:03:35.766563+00:00 | LONG | liq_bot | 4803.8 | 4802.5 | 1.3 |
| 2026-04-15T08:03:36.982007+00:00 | LONG | liq_bot | 4803.8 | 4802.5 | 1.3 |
| 2026-04-15T08:09:05.871463+00:00 | LONG | liq_bot | 4803.8 | 4802.15 | 1.65 |
| 2026-04-15T09:24:05.703750+00:00 | LONG | liq_bot | 4794.55 | 4793.5 | 1.05 |
| 2026-04-15T09:31:58.517061+00:00 | LONG | liq_bot | 4794.55 | 4792.8 | 1.75 |
| 2026-04-15T14:46:44.915841+00:00 | LONG | liq_bot | 4816.0 | 4811.9 | 4.1 |
| 2026-04-15T14:49:20.076776+00:00 | LONG | liq_bot | 4816.0 | 4810.7 | 5.3 |
| 2026-04-15T14:49:20.212643+00:00 | LONG | liq_bot | 4816.0 | 4810.7 | 5.3 |
| 2026-04-15T19:13:52.237108+00:00 | SHORT | liq_top | 4790.45 | 4792.15 | 1.7 |
| 2026-04-15T19:25:20.997682+00:00 | SHORT | liq_top | 4790.4 | 4794.4 | 4.0 |
| 2026-04-15T22:32:07.023208+00:00 | SHORT | liq_top | 4787.9 | 4791.05 | 3.15 |
| 2026-04-15T23:08:35.562618+00:00 | SHORT | liq_top | 4800.55 | 4800.7 | 0.15 |
| 2026-04-16T05:42:22.824865+00:00 | LONG | liq_bot | 4820.75 | 4818.4 | 2.35 |
| 2026-04-16T05:42:22.822299+00:00 | LONG | liq_bot | 4820.75 | 4818.4 | 2.35 |
| 2026-04-16T05:46:40.637445+00:00 | LONG | liq_bot | 4820.75 | 4818.95 | 1.8 |
| 2026-04-16T05:47:50.625063+00:00 | LONG | liq_bot | 4820.75 | 4817.85 | 2.9 |
| 2026-04-16T05:48:51.887320+00:00 | LONG | liq_bot | 4820.75 | 4817.85 | 2.9 |
| 2026-04-16T05:48:52.065765+00:00 | LONG | liq_bot | 4820.75 | 4817.85 | 2.9 |
| 2026-04-16T05:49:58.271818+00:00 | LONG | liq_bot | 4820.75 | 4819.25 | 1.5 |
| 2026-04-16T05:52:10.171584+00:00 | LONG | liq_bot | 4820.75 | 4819.75 | 1.0 |
| 2026-04-16T05:58:35.325892+00:00 | LONG | liq_bot | 4820.75 | 4816.15 | 4.6 |
| 2026-04-16T05:59:39.262002+00:00 | LONG | liq_bot | 4820.75 | 4816.15 | 4.6 |
| 2026-04-16T08:15:34.313413+00:00 | LONG | liq_bot | 4807.15 | 4803.45 | 3.7 |
| 2026-04-16T08:24:23.438389+00:00 | LONG | liq_bot | 4807.15 | 4802.05 | 5.1 |
| 2026-04-16T08:45:09.366056+00:00 | LONG | liq_bot | 4799.9 | 4799.05 | 0.85 |
| 2026-04-16T10:20:46.619331+00:00 | LONG | liq_bot | 4798.9 | 4797.8 | 1.1 |
| 2026-04-16T10:32:29.923352+00:00 | LONG | liq_bot | 4798.9 | 4798.0 | 0.9 |
| 2026-04-16T10:35:05.964752+00:00 | LONG | liq_bot | 4798.9 | 4797.15 | 1.75 |
| 2026-04-16T10:51:05.735406+00:00 | LONG | liq_bot | 4798.9 | 4797.5 | 1.4 |
| 2026-04-16T10:54:13.779946+00:00 | LONG | liq_bot | 4798.9 | 4798.2 | 0.7 |
| 2026-04-16T12:33:54.008591+00:00 | LONG | liq_bot | 4806.55 | 4806.15 | 0.4 |
| 2026-04-16T12:42:53.108884+00:00 | LONG | liq_bot | 4806.55 | 4805.65 | 0.9 |
| 2026-04-16T12:57:42.006682+00:00 | LONG | liq_bot | 4806.55 | 4805.75 | 0.8 |
| 2026-04-16T13:35:17.123379+00:00 | LONG | liq_bot | 4806.55 | 4804.65 | 1.9 |
| 2026-04-16T15:19:02.373681+00:00 | SHORT | liq_top | 4792.5 | 4799.2 | 6.7 |
| 2026-04-16T15:24:37.629991+00:00 | SHORT | liq_top | 4792.5 | 4798.5 | 6.0 |
| 2026-04-16T15:26:37.646365+00:00 | SHORT | liq_top | 4792.5 | 4795.8 | 3.3 |
| 2026-04-16T15:28:16.253305+00:00 | SHORT | liq_top | 4792.5 | 4795.8 | 3.3 |

## Interpretation

- `NEW_REJECT_WRONG_SIDE` = GO signals where the fired trigger level was on
  the literatura-invalid side for the resolved direction (SHORT with level below
  price, or LONG with level above price). Under C1 post-validation these would
  not have been emitted.
- `IDENTICAL_APPROX` = fired level was on the correct side. The C1 filter would
  typically still pass these, though full parquet replay could reveal edge cases
  where the top candidate moves out-of-band (NEAR).
- `CANNOT_REPLAY` = decision missing required fields.

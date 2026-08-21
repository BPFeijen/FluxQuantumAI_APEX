# W5 Backtest 10mo L2 — Post W1-W5.3 arming
**Generated**: 2026-05-09T12:41:41+00:00
**Window**: 2025-07-01 00:00:00+00:00 → 2026-04-07 00:00:00+00:00 (~10 months)
**Source**: `C:\data\processed\calibration_dataset_full.parquet` (M1 + L2 features + M30 boxes)
**Iceberg**: `C:\data\iceberg` daily JSONL (W5.1 score modifiers)
**Initial capital**: $1000.00
**Position sizing**: lots [0.02, 0.02, 0.01] = $5/pt total (XAUUSD)

## Headline metrics
| Metric | Value |
|---|---:|
| Trades | 9 |
| Wins | 6 |
| Losses | 3 |
| **Win rate** | **66.67%** |
| Gross win | $976.36 |
| Gross loss | $300.00 |
| **Profit factor** | **3.25** |
| **PnL** | **$+676.36** |
| Final capital | $1,676.36 |
| Return | +67.64% |
| Max drawdown (USD) | $100.00 (5.63%) |
| Avg win | $162.73 |
| Avg loss | $-100.00 |
| Avg duration | 88.9 min |

## Block breakdown (entries rejected by gates)
| Reason | Count |
|---|---:|
| rr_below_min | 724 |
| daily_range_short_lower | 103 |
| atr_extreme | 20 |
| min_score_go | 14 |
| same_level_cooldown | 5 |

## Methodology
- Entry detection: M30 confirmed box at liq_top (SHORT) / liq_bot (LONG), proximity ≤ 5pts
- Gates applied (W1-W5.3): same_level cooldown 60min + 5pt prox, global cooldown 30min,
  ATR extreme >45 block, session close 30min before 22 UTC, daily range 20% extremes,
  daily loss limit -$50, RR ≥ 1.0, MIN_SCORE_GO ≥ 1
- Score = L2 momentum (delta_4h/dom_imbalance/pressure) + W5.1 iceberg modifiers
  (Breaking Ice ALIGNED +1 / CONTRA -2; Iceberg Zone IN ALIGNED +1 / CONTRA -1)
- SL/TP simulation: M1 forward walk up to 4h. SL=±20pts, TP1=±atr*0.8, TP2=±atr*1.5.
  TP1 hit → leg1 closes, SL→entry (BE) for leg2+leg3; then BE / TP2 / timeout.
- 1 trade at a time (no overlapping positions; simplified).

## Caveats
- Backtest uses simplified F-asym (does NOT replicate full cascade resolver).
- Iceberg JSONL coverage may have gaps on some days (counted as no_iceberg_data).
- L2 momentum score is heuristic proxy; production v3_momentum gate is more nuanced.
- Slippage / spread NOT modeled (assumed instant fill at level prices).
- ML iceberg V4 (V4_BLOCK contra) NOT applied in this backtest.

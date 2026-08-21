# W5 E2E Backtest 10mo L2 — Entry + Position Monitor exits
**Generated**: 2026-05-09T12:48:47+00:00
**Window**: 2025-07-01 00:00:00+00:00 → 2026-04-07 00:00:00+00:00 (~9.2 mo, parquet end)
**Initial capital**: $1000.00

## Headline metrics

| Metric | Value |
|---|---:|
| Trades | 30 |
| Wins | 12 |
| Losses | 18 |
| **Win rate** | **40.00%** |
| Gross win | $370.10 |
| Gross loss | $947.25 |
| **Profit factor** | **0.39** |
| **PnL** | **$-577.15** |
| Final capital | $422.85 |
| Return | -57.71% |
| Max DD | $602.61 (58.77%) |
| Avg duration | 37.9 min |

## Exit reason distribution

| Reason | n | wins | losses | total PnL$ |
|---|---:|---:|---:|---:|
| SL_HIT | 12 | 0 | 12 | $-794.25 |
| L2_DANGER_PRE_SHIELD | 8 | 3 | 5 | $-112.00 |
| GIVEBACK_PRE_SHIELD | 8 | 8 | 0 | $+270.75 |
| T3_DEFENSE_PRE_SHIELD | 1 | 0 | 1 | $-4.00 |
| GIVEBACK_AFTER_SHIELD | 1 | 1 | 0 | $+62.35 |

## Block breakdown

| Reason | Count |
|---|---:|
| rr_below_min | 194 |
| daily_loss_limit | 131 |
| daily_range_short_lower | 103 |
| same_level_cooldown | 93 |
| min_score_go | 92 |
| atr_extreme | 49 |

## Methodology
- Entry: M30 confirmed box at liq_top (SHORT) / liq_bot (LONG), prox ≤ 5pts
- Gates: same_level cooldown 60min, global 30min, ATR extreme >45, session close 30min,
  daily range 20% extremes, daily loss -$50, RR≥1, MIN_SCORE_GO≥1
- Score: L2 mom (delta_4h/dom/pressure) + iceberg modifiers
- SL: level ± clip(atr×1.0, 8, 30); TP1=FMV; TP2=opposite liq line
- **Position Monitor exits (END-TO-END)**:
  - SL_HIT (initial)
  - TP1_THEN_BE/TP2 (SHIELD on TP1)
  - REGIME_FLIP (rolling_delta_4h flip > 200)
  - L2_DANGER (dom_imbalance / bar_delta extreme contra)
  - T3_DEFENSE (bar_delta > 500 + 3pt adverse + M30 break)
  - GIVEBACK (MFE retraced > 50% after MFE > 0.5×ATR)
  - Trailing stop after SHIELD (12pt behind close)
  - TIMEOUT (4h)

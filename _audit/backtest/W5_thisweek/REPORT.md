# Backtest This Week — what-if W1-W5.3 + Giveback armed

**Generated**: 2026-05-09T15:35:55+00:00
**Window**: 2026-05-04 00:00:00+00:00 → 2026-05-09 00:00:00+00:00
**Source**: `decision_log.jsonl` (production methodology stack output)
**Decisions input (deduped GO+EXEC_FAILED)**: 558
**Initial capital**: $1000.00

## Scenario A — production-as-is (every signal executed)

- n_trades: 190
- n_wins: 30
- n_losses: 160
- win_rate: 0.15789473684210525
- gross_win_usd: 4629.33000000001
- gross_loss_usd: 19200.0
- profit_factor: 0.2411109375000005
- pnl_usd: -14570.669999999991
- final_capital: -13570.669999999991
- return_pct: -1457.066999999999
- max_drawdown_usd: 14660.669999999991
- max_drawdown_pct: 1345.0155963302745
- avg_win_usd: 154.31100000000032
- avg_loss_usd: -120.0
- avg_duration_min: 7.573684210526316

### A exit reason breakdown

| Reason | n | wins | losses | total PnL$ |
|---|---:|---:|---:|---:|
| SL_HIT | 160 | 0 | 160 | $-19,200.00 |
| TP1_THEN_BE | 18 | 18 | 0 | $+2,555.85 |
| TP1_THEN_TP2 | 12 | 12 | 0 | $+2,073.48 |

## Scenario B — W1-W5.3 + Giveback ARMED

- n_trades: 22
- n_wins: 18
- n_losses: 4
- win_rate: 0.8181818181818182
- gross_win_usd: 2951.0100000000057
- gross_loss_usd: 480.0
- profit_factor: 6.147937500000012
- pnl_usd: 2471.0100000000057
- final_capital: 3471.0100000000057
- return_pct: 247.10100000000054
- max_drawdown_usd: 360.0
- max_drawdown_pct: 10.371620940302662
- avg_win_usd: 163.9450000000003
- avg_loss_usd: -120.0
- avg_duration_min: 35.0

### B exit reason breakdown

| Reason | n | wins | losses | total PnL$ |
|---|---:|---:|---:|---:|
| TP1_THEN_BE | 11 | 11 | 0 | $+1,496.79 |
| TP1_THEN_TP2 | 7 | 7 | 0 | $+1,454.22 |
| SL_HIT | 4 | 0 | 4 | $-480.00 |

### B blocks (W1-W5.3 gates rejected)

| Reason | Count |
|---|---:|
| daily_range_short_lower | 244 |
| active_position | 204 |
| daily_loss_limit | 43 |
| rr_below_min | 27 |
| same_level_cooldown | 15 |
| atr_extreme | 3 |

## Comparison

| Metric | A (production-as-is) | B (W1-W5.3+Giveback) | Delta |
|---|---:|---:|---:|
| Trades | 190 | 22 | -168 |
| WR | 15.79% | 81.82% | +66.03pp |
| PF | 0.24 | 6.15 | +5.91 |
| PnL | $-14,570.67 | $+2,471.01 | $+17,041.68 |
| Final cap | $-13,570.67 | $3,471.01 | $+17,041.68 |
| Max DD | $14,660.67 | $360.00 | $-14,300.67 |

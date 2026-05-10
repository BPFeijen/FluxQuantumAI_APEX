# Dashboard cTrader era cutoff + Telegram interval (2026-05-10)

**Status**: COMPLETE, deployed
**Trigger**: Barbara 2026-05-10 — three issues:
1. Dashboard balance/equity/win_rate/PF showed RoboForex era values (stale)
2. `balance_start = $500` displayed; cTrader DEMO baseline is `$2000`
3. Telegram health check sending every 5-7 min (Barbara had asked 30 min)

## Constraint

Barbara: **"NAO APAGAR trades.csv, eu preciso desse historico"**.

So the fix had to: preserve trades.csv intact, but exclude pre-broker-switch
rows from LIVE KPI calculations on the dashboard.

## Changes

### 1. Telegram health check 5min → 30min

`live/telegram_notifier.py` (gitignored — credentials):
```diff
-_HEALTH_INTERVAL_S = 300  # 5 min periodic
-_HEALTH_MIN_INTERVAL_S = 300  # 5 min minimum
+_HEALTH_INTERVAL_S = 1800  # 30 min (Barbara 2026-05-10)
+_HEALTH_MIN_INTERVAL_S = 1800  # 30 min
```

State-change-driven sends remain instant; only the periodic heartbeat is
throttled.

### 2. Balance baseline 500 → 2000

`C:/FluxQuantumAPEX/logs/balance_snapshot.json`:
```json
{
  "balance_start": 2000.00,
  "captured_at": "2026-05-10T20:03:42Z",
  "source": "cTrader DEMO account 47123014 (manual update per Barbara
             2026-05-10; was 500 from RoboForex era 2026-03-31)"
}
```

Old snapshot backed up at `balance_snapshot.json.bak_pre_ctrader_20260510`.

### 3. cTrader era cutoff filter in dashboard

`C:/FluxQuantumAPEX/dashboard/api.py` — added `CTRADER_ERA_CUTOFF_ISO =
"2026-05-09T00:00:00"` and helper `_is_current_broker_era(t)`.

Applied to LIVE KPI functions (filter out pre-cutoff rows):
- `get_live_balance` — sum realized PnL only from cTrader era
- `get_open_positions` — open positions only from cTrader era (excludes
  stale RoboForex `result='open'` rows that were never updated when the
  system stopped 2026-05-08)
- `get_equity_curve` — chart starts at cTrader baseline
- `compute_metrics` — accuracy, win rate, profit factor, edge ratio reflect
  cTrader trades only
- `get_weekly_pnl` — weekly PnL view filtered
- `api_metrics_mt5` route — last-30-days metrics filtered

NOT filtered (preserve historic visibility):
- `api_trades` (`/api/trades`) — operator can see ALL trade history
- `api_history` (`/api/history`) — same

## Verification (post-restart)

```
$ curl -s http://149.102.153.10:8088/api/status | jq .
{
  "balance": 2000.0,         ← cTrader baseline (was 500)
  "balance_start": 2000.0,   ← cTrader baseline
  "equity": 2000.0,
  "unrealized_pnl": 0,       ← clean (was $6681 with stale rows)
  "total_pnl": 0,
  "wins": 0, "losses": 0,    ← cleared RoboForex stats
  "win_rate": 0.0,
  "profit_factor": 0.0,
  "open_positions": []       ← stale rows excluded
  ...
}
```

When CTraderExecutor opens new trades post-22:00 UTC market open, those
rows have `timestamp >= 2026-05-09` → flow into KPIs correctly.

## Service restarts

Both services restarted to pick up changes:
- `nssm restart FluxQuantumAPEX_Dashboard` — for api.py reload
- `nssm restart FluxQuantumAPEX` — for telegram_notifier.py reload (constants
  read at module import)

## What still uses CTrader API direct (NOT this commit)

Live broker queries (real-time balance from cTrader, real-time open positions
from cTrader API) are NOT yet wired up. Dashboard derives these from
trades.csv + service_state.json which are populated by the trading service
on each execution. If the operator wants TRUE-LIVE figures (e.g. mid-trade
balance changes from broker swaps/commissions/fees), a separate sprint can
add CTrader OpenAPI live queries to the dashboard. For now, baseline +
realized PnL + estimated unrealized is sufficient.

## Sign-off

- [x] Telegram interval verified 1800s in source
- [x] balance_snapshot updated, old backed up
- [x] api.py syntax OK (ast.parse)
- [x] /api/status post-restart returns cTrader baseline values
- [ ] Visual confirmation by Barbara at http://149.102.153.10:8088/
- [ ] Confirm Telegram now arrives every 30min, not 5-7min (next observation
      window after market open)

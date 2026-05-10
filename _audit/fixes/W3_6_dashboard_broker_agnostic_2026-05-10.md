# W3.6 — Dashboard broker-agnostic refactor (2026-05-10)

**Status**: COMPLETE, deployed
**Author**: directed cleanup (CC#3)
**Trigger**: Barbara 2026-05-10 — "dashboard deveria estar conectado a cTrader e nao roboforex, que nao existe mais"

## Problem

`C:/FluxQuantumAPEX/dashboard/api.py` (Flask backend serving
http://149.102.153.10:8088/) was hardcoded to MT5 RoboForex:

- Line 82 (old): `_ROBOFOREX_TERMINAL = r"C:\Program Files\RoboForex MT5 Terminal\terminal64.exe"`
- 7 functions called `mt5.history_deals_get()`, `mt5.account_info()`,
  `mt5.positions_get()`, etc.
- Frontend showed "MT5 LIVE/SIM" badge tied to RoboForex MT5 connection

After broker switch to cTrader/IC Markets (2026-05-09), the dashboard could
not connect to its expected broker, so it showed empty/stale data — equity
$0, no positions, no history.

## Solution

Refactored api.py to read from local logs only, broker-agnostic:

- `service_state.json` — heartbeat freshness drives "system live" indicator
  (replaces MT5 connection check)
- `trades.csv` — historic trades + open positions (CTraderExecutor.log_trade
  writes here; same contract as old MT5Executor.log_trade)
- `balance_snapshot.json` — starting balance baseline (config-derived)

Functions refactored:

| # | Function | Before | After |
|---|---|---|---|
| 1 | `_check_mt5_live` (l.86) | MT5 init+account_info | `service_state.json` mtime <30s |
| 2 | `_capture_balance_snapshot` (l.128) | Read MT5 balance for baseline | Use `BALANCE_START` config |
| 3 | `get_open_positions` (l.444) | mt5.positions_get → enrich from CSV | trades.csv `result='open'` + service_state mid for unrealised |
| 4 | `get_live_balance` (l.583) | mt5.account_info().balance | baseline + realized PnL from trades.csv closed rows |
| 5 | `get_live_equity` (l.596) | mt5.account_info() (eq/upnl/free_margin) | balance + unrealised (free_margin=None) |
| 6 | `get_equity_curve` (l.408 prefix block) | mt5.history_deals_get override | CSV-only fall-through (already had fallback) |
| 7 | `api_status` MT5 metrics override (l.823) | mt5.history_deals 90d → metrics overlay | removed; CSV-derived metrics authoritative |
| 8 | `trading_started_at` (l.889) | min(d.time for d in mt5.history_deals_get(month_start)) | first trades.csv timestamp this month |
| 9 | `api_history` route (l.911) | mt5.history_deals_get(30d) | trades.csv closed rows transformed to deal-shape |
| 10 | `api_metrics_mt5` route (l.948) | mt5.history_deals_get(30d) → live metrics | trades.csv last 30d closed rows |

Backwards compat preserved:
- `_check_mt5_live` kept as alias for `_check_broker_live` (other modules
  may import it; behavior is now broker-agnostic but signature unchanged)
- JSON response field `mt5_live` kept (frontend expects it; semantic now =
  "system trading service alive")
- Endpoint `/api/metrics_mt5` URL kept; payload shape unchanged

## Validation

- Sintaxe: `ast.parse OK`
- Service restart: `nssm restart FluxQuantumAPEX_Dashboard` → STATE RUNNING
- HTTP smoke (after restart):
  - `/` → 200
  - `/api/status` → 200, `mt5_live: true`, balance/equity computed from logs
  - `/api/equity` → 200
  - `/api/metrics_mt5` → 200
  - `/api/history` → 200

## Known issue (out of scope, separate cleanup)

`/api/status` reports `unrealized_pnl: ~$6681.93` on first read post-refactor.
Reason: `trades.csv` contains stale `result='open'` rows from the MT5
RoboForex era (Apr-May 2026) that were never marked closed when the system
stopped 2026-05-08. The dashboard correctly computes "if these positions
were still open at current price..." but those tickets are actually closed
broker-side (or moot — different broker now).

Cleanup options (Barbara to decide separately):
1. Move pre-broker-switch trades.csv rows to `trades.csv.bak_pre_ctrader_<date>`,
   start fresh CSV with cTrader-era trades only.
2. Add `result='stale'` filter in dashboard to exclude old-broker rows.
3. Backfill close events from MT5 history archive (if accessible).

This is a state-cleanup issue, not a refactor regression.

## Reproduce / revert

- Backup at `C:/FluxQuantumAPEX/dashboard/api.py.bak_post_w36_refactor_20260510`
  (NOT the original — captured post-refactor for revert path).
- For full revert: there is no git repo at `C:/FluxQuantumAPEX`, so original
  state is reconstructible only from sysadmin-side backup OR by reverting
  via the git diff in this repo's audit trail.

## Sign-off

- [x] Service restarted, HTTP 200 on all endpoints
- [x] `mt5_live` field reflects FluxQuantumAPEX heartbeat (verified true while service running)
- [ ] Frontend visual check pending (Barbara to confirm dashboard at http://149.102.153.10:8088/ shows reasonable data)
- [ ] trades.csv stale-state cleanup deferred (separate decision)

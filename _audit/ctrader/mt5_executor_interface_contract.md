# mt5_executor.MT5Executor — Interface Contract for cTrader Replication
**CTRADER-INTEGRATION-001 Step 2 audit**
Date: 2026-04-26
Source: `C:\FluxQuantumAI\mt5_executor.py` (684 LOC, HEAD `135c4268`)

## Module-level surface (consumed by `live/event_processor.py:65`)

```python
from mt5_executor import MT5Executor, _split_lots, SYMBOL, MAGIC
```

| Symbol | Type | Purpose |
|---|---|---|
| `MT5Executor` | class | The executor itself |
| `_split_lots(total: float) -> tuple[float, float, float]` | function | 40/40/20 leg split, 0.01 step rounding |
| `SYMBOL` | str = `"XAUUSD"` | Default trading symbol |
| `MAGIC` | int = `20260331` | APEX magic number |
| `ACCOUNT` | int = `68302120` | RoboForex demo account |
| `LOT_SIZE` | float = `0.02` | Default lot |
| `MIN_LOT` | float = `0.01` | Step |

cTrader replica must export **all** of these names so `from ctrader_executor import …` works as a drop-in (or — chosen path — `event_processor.py` imports conditionally based on `BROKER` env var).

## Constructor

`MT5Executor()` — no args. Side effect: ensures log dir + `trades.csv` + `live_log.csv` headers exist. **Does NOT connect at __init__**. Connection deferred to `reconnect()`.

## State (instance attributes)

| Attribute | Type | Purpose |
|---|---|---|
| `connected` | bool | True after successful `reconnect()` |

`run_live.py:107` reads `_executor.connected` immediately after construction → if API requires async connect, executor must `connect_blocking()` in `__init__` and set `connected` flag synchronously, OR `_init_executor` flow must call a `connect()` method. Current MT5 pattern: `connected=False` until `reconnect()` is called.

## Public methods

```python
def reconnect(self) -> bool
    """Idempotent. Sets self.connected. Returns True if connected."""

def get_balance(self) -> float
    """Account balance, 0.0 if not connected."""

def open_position(
    symbol: str,
    direction: str,            # "LONG" | "SHORT"
    lot_size: float,           # total lot, split 40/40/20
    sl: float,
    tp1: float,
    tp2: Optional[float] = None,
    dry_run: bool = False,
    explicit_lots: Optional[list] = None,  # [l1, l2, l3] override
) -> dict
    """3-leg APEX position. Returns:
      {success: bool, tickets: [t1,t2,t3], legs: 2|3, entry: float,
       lots: [l1,l2,l3], error: str|None, dry_run?: bool}
    """

def open_single(
    symbol, direction, lot, sl, tp=0.0,
    comment="APEX_HEDGE", dry_run=False
) -> dict
    """Single-lot market order. Returns {success, ticket, entry, error, dry_run?}"""

def open_limit(
    symbol, direction, lot, limit_price, sl, tp=0.0,
    comment="APEX_LIMIT", expiry_hours=4.0, dry_run=False
) -> dict
    """Pending LIMIT with auto-expire. Returns {success, ticket, error, dry_run?}"""

def move_to_breakeven(
    ticket_leg2: int, ticket_leg3: int, entry_price: float
) -> dict
    """SHIELD: move SL of remaining legs to entry. Returns
      {success, modified: [tickets], errors: []}"""

def close_position(self, ticket: int) -> dict
    """Closes by ticket via opposite market order.
      Returns {success, ticket, pnl, error}"""

def get_open_positions(self) -> list[dict]
    """Filtered by magic=MAGIC. Each row:
      {ticket, symbol, direction: 'LONG'|'SHORT', volume, entry, sl, tp,
       pnl, comment, time_open: ISO8601 str}"""

def log_trade(*, direction, decision, lots, entry, sl, tp1, tp2=0.0,
              result="", pnl=0.0, gate_score=0,
              leg1_ticket=0, leg2_ticket=0, leg3_ticket=0,
              asset="AX1", entry_mode="", daily_trend="",
              phase="", strategy_mode="") -> None
    """Append row to logs/trades.csv (TRADES_COLUMNS schema)."""

def log_gate(*, symbol="AX1", direction, gate_decision, score=0,
             macro_delta=0.0, mom_status="", v4_status="UNKNOWN",
             reason="", trigger="liq_touch", zone="",
             patterns="") -> None
    """Append row to logs/live_log.csv (GATE_COLUMNS schema)."""

# Private but referenced
def _modify_sl(self, ticket: int, new_sl: float) -> tuple[bool, str]
```

## Integration points (from event_processor.py)

| Location | Use |
|---|---|
| `live/event_processor.py:65` | `from mt5_executor import MT5Executor, _split_lots, SYMBOL, MAGIC` |
| `live/event_processor.py:550` | `self.executor = MT5Executor()` (hardcoded) |
| `live/event_processor.py:481, 502` | `from mt5_executor import _split_lots` (re-import inside functions) |
| `run_live.py:105-107` | `from mt5_executor import MT5Executor as _MT5Executor; _executor = _MT5Executor(); EXECUTOR_AVAILABLE = _executor.connected` |
| `run_live.py:1014` | `--broker` flag (currently `roboforex|hantec`) |

## Replication strategy for cTrader

1. **Symbol/Magic/account constants** — `SYMBOL = "GC"` (NATIVE per spec; Step 1 confirmation deferred until OAuth), `MAGIC = 20260331`, `ACCOUNT = <demo|live>` from `CTRADER_ACCOUNT_ID_DEMO|LIVE` env.
2. **Sync facade over Twisted reactor** — reactor runs in dedicated thread; each public method submits a request, blocks on `threading.Event` until response/timeout.
3. **OAuth2 access_token + refresh_token** — read from `.env` (`CTRADER_ACCESS_TOKEN`, `CTRADER_REFRESH_TOKEN`). On 401 / token expiry, auto-refresh via `Auth.refreshToken()`. If both missing → `connected=False` and clear log message instructing operator to run `scripts/ctrader_oauth_init.py`.
4. **Symbol lookup cache** — `ProtoOASymbolsListReq` once on connect; map name (`"GC"`) → `symbolId`. Cache `digits`, `lotSize` (volume granularity), `minVolume` for order construction.
5. **Position id mapping** — cTrader uses `positionId` (long), MT5 uses `ticket` (int). Mapping: store `positionId` directly as `ticket` (both fit in int64). For composite 3-leg APEX trades, cTrader has no native split — open 3 separate market orders with distinct `label` ("APEX_L1/L2/L3") so they can be tracked individually.
6. **Volume conversion** — cTrader volume = `lots * symbol.lotSize` (typically lots * 100 for metals, but lookup `ProtoOASymbolByIdReq` for exact). All public methods take MT5-style `lot` and convert internally.
7. **Logging schema unchanged** — `log_trade` / `log_gate` use the same CSV columns, so downstream dashboards keep working.

## Premises (G-PREMISE-AUDIT)

- INHERITED from spec, **OVERRIDDEN**: "BROKER env-var added to event_processor.py" — `run_live.py` already has `--broker {roboforex,hantec}`; cleaner extension is to add `ctrader` choice + `_init_executor("ctrader")` branch + a small import-time switch in `event_processor.py` keyed on `BROKER` env (set by `_init_executor` before `EventProcessor` instantiation, OR by NSSM `AppEnvironment`).
- INHERITED, **VALIDATED**: Symbol GC native on IC Markets cTrader (NO `XAUUSD` mapping). Per-symbol verification deferred until OAuth.
- CREATED: cTrader Open API requires interactive OAuth2 authorization-code flow for first token — Step 4 smoke test cannot run without `access_token` in `.env`. Brief-back the gap; provide `scripts/ctrader_oauth_init.py` for one-time auth.
- CREATED: 3-leg APEX trade modelled as 3 independent cTrader market orders with distinct labels — there is no native cTrader concept of split-leg position with shared SL.

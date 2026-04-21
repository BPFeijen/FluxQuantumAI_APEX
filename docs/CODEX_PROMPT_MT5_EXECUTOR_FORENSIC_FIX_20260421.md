# Codex Task — MT5 Executor Forensic Audit + Minimal Fix

Repository: `BPFeijen/FluxQuantumAI_APEX`
Branch: `stabilization/apex-2026-04`

## Mission
Perform a **forensic audit and minimal fix** of `mt5_executor.py` only.

Goal:
- make the Python MT5 executor internally consistent,
- eliminate the broken split between `_mt5`, `mt5`, `MT5_AVAILABLE`, and `self.connected`,
- improve symbol/tick acquisition reliability,
- add pre-send validation via `order_check()`,
- keep scope minimal.

This task is **not** a redesign.
Do **not** modify unrelated trading logic.
Do **not** touch broker routing, event processor, risk logic, PM, Telegram, news, or bias logic.

---

## Context already confirmed
Current file: `mt5_executor.py`

The file currently has an internal inconsistency:
- `MetaTrader5` is imported as `_mt5`,
- other functions use `mt5`,
- `MT5_AVAILABLE` starts false and is not promoted correctly,
- `self.connected` is initialized from the wrong signal,
- `_get_tick()` is gated by `MT5_AVAILABLE` rather than actual usable module/session state.

This likely explains why connection appears to succeed but tick/order functions still fail.

---

## Files in scope
Only modify:
- `mt5_executor.py`

Optional tests or diagnostics:
- you may add a small test file under `tests/` if useful,
- but do not create a broad test framework.

---

## Required changes

## Part 1 — Fix MT5 module initialization consistency
In `mt5_executor.py`, ensure that when MetaTrader5 imports successfully:
- `mt5` points to the same module object as `_mt5`
- `MT5_AVAILABLE = True`

Replace the current import block with logic equivalent to this:

```python
try:
    import MetaTrader5 as _mt5
    mt5 = _mt5
    MT5_AVAILABLE = True

    _init_kwargs: dict = {"timeout": 10000}
    if _ROBO_TERMINAL:
        _init_kwargs["path"] = _ROBO_TERMINAL
        _init_kwargs["login"] = ACCOUNT
        _init_kwargs["server"] = _ROBO_SERVER
        if _ROBO_PASSWORD:
            _init_kwargs["password"] = _ROBO_PASSWORD

    log.info("MT5 module loaded — connection deferred to first reconnect() call")
except ImportError:
    _mt5 = None
    mt5 = None
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 not installed — executor running in dry-run mode")
```

---

## Part 2 — Fix constructor state
In `MT5Executor.__init__()`, do **not** initialize `self.connected` from `MT5_AVAILABLE`.
Importing the Python package is not the same as being connected to the terminal/account.

Required change:

```python
self.connected = False
```

---

## Part 3 — Fix `_get_tick()`
Update `_get_tick(symbol)` so that:
- it checks `mt5 is None` rather than `MT5_AVAILABLE`
- it validates the symbol with `symbol_info(symbol)`
- if symbol is not visible, it attempts `symbol_select(symbol, True)`
- only then calls `symbol_info_tick(symbol)`

Implement logic equivalent to:

```python
def _get_tick(symbol: str) -> Optional[object]:
    if mt5 is None:
        return None

    info = mt5.symbol_info(symbol)
    if info is None:
        log.error("symbol_info(%s) failed: %s", symbol, mt5.last_error())
        return None

    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            log.error("symbol_select(%s) failed: %s", symbol, mt5.last_error())
            return None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log.error("No tick for %s: %s", symbol, mt5.last_error())
    return tick
```

---

## Part 4 — Fix `reconnect()`
Update `reconnect()` so that:
- it uses the same unified `mt5` object,
- it handles the case where the module is unavailable,
- it validates `account_info()` after initialize,
- it updates `self.connected` consistently,
- it updates `MT5_AVAILABLE` only as a module availability / usable session signal when appropriate.

Implement logic equivalent to:

```python
def reconnect(self) -> bool:
    global mt5, MT5_AVAILABLE

    if mt5 is None:
        log.warning("MT5 reconnect: MetaTrader5 module unavailable")
        return False

    if self.connected:
        info = mt5.account_info()
        if info is not None:
            return True
        self.connected = False

    try:
        if mt5.initialize(**_init_kwargs):
            info = mt5.account_info()
            if info and info.login == ACCOUNT:
                self.connected = True
                MT5_AVAILABLE = True
                log.info("MT5 RECONNECTED — account %d", ACCOUNT)
                return True
            else:
                log.warning("MT5 reconnect: account mismatch or no account_info (%s)", info)
        else:
            log.warning("MT5 reconnect failed: %s", mt5.last_error())
    except Exception as e:
        log.warning("MT5 reconnect error: %s", e)

    self.connected = False
    return False
```

Do not redesign account selection logic beyond this.

---

## Part 5 — Add `order_check()` before `order_send()`
Before `mt5.order_send(req)` in `open_position()`, validate the request with `mt5.order_check(req)`.
If `order_check()` fails or returns `None`, log the failure and skip sending that leg.

Add minimal logic like:

```python
check = mt5.order_check(req)
if check is None:
    err = str(mt5.last_error())
    log.error("Leg %d order_check failed: %s", i + 1, err)
    tickets.append(0)
    continue
```

If `check` contains a retcode or comment field, log those too if easily accessible.
Do not overengineer.

---

## Part 6 — Add a small self-test helper (optional but preferred)
If appropriate, add a small non-invasive helper such as:
- `diagnose_connection()` or
- `get_connection_diagnostics()`

It should report:
- module available or not
- reconnect result
- account_info summary
- symbol_info("XAUUSD") presence
- tick availability

This helper must not place orders.
This is optional but useful.

---

## Non-negotiable rules
- No changes outside `mt5_executor.py` unless adding a tiny focused test.
- No redesign of the execution architecture.
- No broker abstraction refactor.
- No touching `mt5_executor_hantec.py` in this task.
- No touching event processor or live strategy logic.
- No hardcoded changes for Hantec here.
- Keep this patch forensic and minimal.

---

## Deliverables
Return:
1. unified diff
2. files changed
3. concise explanation of root cause
4. concise explanation of before vs after behavior
5. any assumptions made
6. if possible, a small manual smoke-test snippet using:
   - reconnect
   - account_info
   - symbol_info("XAUUSD")
   - symbol_info_tick("XAUUSD")

---

## Goal
This task succeeds if `mt5_executor.py` stops lying to itself about connection state and becomes internally consistent enough to support real debugging of RoboForex demo connectivity.

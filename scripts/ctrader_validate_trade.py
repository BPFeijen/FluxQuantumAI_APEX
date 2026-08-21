"""cTrader trade execution validator (DEMO ONLY).

Opens 1 position (lot 0.01, GC, LONG) with wide SL/TP, verifies positionId,
lists open positions, then immediately closes. Reports each step.

ABORTS if CTRADER_ACCOUNT_MODE != 'demo' as a safety check.

Usage:
  C:/FluxQuantumAI/.venv/Scripts/python.exe C:/FluxQuantumAI/scripts/ctrader_validate_trade.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ENV = Path(r"C:/FluxQuantumAI/.env")
for ln in ENV.read_text(encoding="utf-8").splitlines():
    if ln.strip() and not ln.startswith("#") and "=" in ln:
        k, _, v = ln.partition("="); os.environ.setdefault(k.strip(), v.strip())

MODE = os.environ.get("CTRADER_ACCOUNT_MODE", "demo").lower()
if MODE != "demo":
    print(f"ABORT: CTRADER_ACCOUNT_MODE={MODE!r} (must be 'demo' for safety)")
    sys.exit(1)

print("=" * 70)
print("cTRADER TRADE EXECUTION VALIDATOR (DEMO)")
print("=" * 70)
print(f"Mode: {MODE}")

sys.path.insert(0, r"C:/FluxQuantumAI")
from ctrader_executor import CTraderExecutor

ex = CTraderExecutor()
print("\n[1] Connecting...")
ok = ex.reconnect()
print(f"  reconnect() = {ok}")
if not ok:
    sys.exit(2)

print("\n[2] Account balance")
try:
    bal = ex.get_balance()
    print(f"  balance: {bal:.2f} USD")
except Exception as e:
    print(f"  WARN balance fetch failed: {e}")

print("\n[3] Symbol details: GC (Comex Gold futures)")
sym = ex._ensure_symbol_full("GC")
if sym is None:
    print("  ABORT: symbol GC not available")
    sys.exit(3)
print(f"  id={sym.get('id')}  digits={sym.get('digits')}  lotSize={sym.get('lotSize')}  minVolume={sym.get('minVolume')}")

print("\n[4] Get current tick (subscribe spot)")
try:
    tick = ex._get_tick("GC")
    if tick:
        print(f"  bid={tick.get('bid')}  ask={tick.get('ask')}")
        cur_price = float(tick.get("ask") or tick.get("bid"))
    else:
        print("  WARN: tick not available, using mid-day approximation")
        cur_price = 4700.0  # approximate fallback
except Exception as e:
    print(f"  WARN tick error: {e}, fallback price used")
    cur_price = 4700.0

print(f"\n[5] Opening LONG 0.01 lot @ market (current ~{cur_price:.2f})")
sl = round(cur_price - 50.0, 2)   # wide SL 50pts
tp = round(cur_price + 50.0, 2)   # wide TP 50pts
print(f"     SL={sl}  TP={tp}")
result = ex.open_single(
    symbol="GC", direction="LONG", lot=0.01,
    sl=sl, tp=tp, comment="APEX_VAL_TEST",
)
print(f"  result: {result}")
if not result.get("success"):
    print(f"  ABORT: open failed — {result.get('error')}")
    sys.exit(4)

ticket = int(result["ticket"])
print(f"  positionId: {ticket}")

print("\n[6] Listing open positions (should include our test)")
time.sleep(2)  # allow position to register
positions = ex.get_open_positions() or []
found = False
for p in positions:
    print(f"  - {p}")
    if int(p.get("ticket", p.get("positionId", 0))) == ticket:
        found = True
print(f"  test position found in list: {found}")

print(f"\n[7] Closing test position {ticket}")
close_result = ex.close_position(ticket)
print(f"  close result: {close_result}")
if close_result.get("success"):
    print(f"  [OK] Position {ticket} closed successfully")
else:
    print(f"  [FAIL] Close failed: {close_result.get('error')}")
    print(f"  IMPORTANT: position {ticket} may still be open — check cTrader UI")
    sys.exit(5)

print("\n[8] Verify position no longer in list")
time.sleep(2)
positions_after = ex.get_open_positions() or []
still_open = any(int(p.get("ticket", p.get("positionId", 0))) == ticket for p in positions_after)
print(f"  position {ticket} still open: {still_open}")

# Cleanup
try:
    from twisted.internet import reactor
    if reactor.running:
        reactor.callFromThread(reactor.stop)
except Exception:
    pass

print("\n" + "=" * 70)
if not still_open and close_result.get("success"):
    print("[VALIDATION OK] cTrader executor end-to-end works.")
    print(f"  - Opened position {ticket} (LONG 0.01 GC SL={sl} TP={tp})")
    print(f"  - Position appeared in get_open_positions: {found}")
    print(f"  - Closed position {ticket}: {close_result.get('success')}")
    print(f"  - Position cleared from list: {not still_open}")
    sys.exit(0)
else:
    print("[VALIDATION FAILED] see logs above")
    sys.exit(6)

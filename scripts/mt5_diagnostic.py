"""
MT5 integration diagnostic — runs all checks needed before / after MT5 reinstall.

Usage:
  C:/FluxQuantumAI/.venv/Scripts/python.exe C:/FluxQuantumAI/scripts/mt5_diagnostic.py

This script does NOT modify anything. Read-only checks.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load .env if present
ENV_PATH = Path(r"C:/FluxQuantumAI/.env")
if ENV_PATH.exists():
    with ENV_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

print("=" * 70)
print("MT5 INTEGRATION DIAGNOSTIC")
print("=" * 70)

# ---------- Layer 1: Python SDK ----------
print("\n[Layer 1] Python SDK")
try:
    import MetaTrader5 as mt5
    print(f"  ✅ MetaTrader5 SDK installed, version: {mt5.__version__}")
except ImportError as e:
    print(f"  ❌ MetaTrader5 NOT installed: {e}")
    print(f"  FIX: pip install MetaTrader5")
    sys.exit(1)

# ---------- Layer 2: Terminal installations ----------
print("\n[Layer 2] MT5 terminal installations")
common_paths = [
    Path("C:/Program Files/RoboForex MT5 Terminal/terminal64.exe"),
    Path("C:/Program Files/MetaTrader 5/terminal64.exe"),
    Path("C:/Program Files/Hantec MT5/terminal64.exe"),
    Path("C:/Program Files/Hantec Markets MT5 Terminal/terminal64.exe"),
    Path(os.path.expanduser("~/AppData/Roaming/MetaQuotes/Terminal")),
]
found_terminals = []
for p in common_paths:
    if p.exists():
        if p.is_dir():
            print(f"  ✅ Found dir: {p}")
            for child in p.iterdir():
                if child.is_dir() and len(child.name) == 32:  # MT5 terminal IDs are 32 chars
                    print(f"     terminal id: {child.name}")
        else:
            print(f"  ✅ Found exe: {p}")
            found_terminals.append(p)
    else:
        print(f"  ⬜ Not found: {p}")
if not found_terminals:
    print("  ❌ NO terminals installed in common paths.")
    print("  FIX: Install MT5 (RoboForex link / Hantec link) and login to broker accounts.")

# ---------- Layer 3: Environment vars ----------
print("\n[Layer 3] Environment variables")
env_vars = {
    "ROBOFOREX_TERMINAL": os.environ.get("ROBOFOREX_TERMINAL", ""),
    "ROBOFOREX_SERVER": os.environ.get("ROBOFOREX_SERVER", ""),
    "ROBOFOREX_PASSWORD": "***SET***" if os.environ.get("ROBOFOREX_PASSWORD") else "",
    "HANTEC_TERMINAL": os.environ.get("HANTEC_TERMINAL", ""),
    "HANTEC_SERVER": os.environ.get("HANTEC_SERVER", ""),
    "HANTEC_PASSWORD": "***SET***" if os.environ.get("HANTEC_PASSWORD") else "",
}
for k, v in env_vars.items():
    if v:
        print(f"  ✅ {k} = {v if 'PASSWORD' not in k else '***SET***'}")
    else:
        print(f"  ⬜ {k} = (empty/unset)")
print("\n  NOTE: Empty PASSWORD means SDK relies on terminal having account logged-in.")
print("        That works IF terminal is open and logged into the right account.")

# ---------- Layer 4: SDK initialize attempt ----------
print("\n[Layer 4] SDK initialize attempt")
print("  trying mt5.initialize() with no args (auto-detect)...")
result = mt5.initialize(timeout=5000)
print(f"  initialize() returned: {result}")
print(f"  last_error: {mt5.last_error()}")
if not result:
    print("  ❌ Cannot initialize.")
    print("  Common causes:")
    print("    1. MT5 terminal not running (start MT5 GUI app first)")
    print("    2. Terminal not installed")
    print("    3. AutoTrading disabled inside MT5 (toolbar button)")
    print("    4. Session 0 vs Session 2 isolation (running as Windows service)")
else:
    info = mt5.terminal_info()
    if info:
        print(f"  ✅ Terminal info:")
        print(f"     name: {info.name}")
        print(f"     path: {info.path}")
        print(f"     connected: {info.connected}")
        print(f"     trade_allowed: {info.trade_allowed}")
        print(f"     build: {info.build}")
    acct = mt5.account_info()
    if acct:
        print(f"  ✅ Account info:")
        print(f"     login: {acct.login}")
        print(f"     server: {acct.server}")
        print(f"     name: {acct.name}")
        print(f"     balance: {acct.balance} {acct.currency}")
        print(f"     trade_allowed: {acct.trade_allowed}")
    else:
        print(f"  ⚠️  No account info — terminal not logged in or wrong account")

# ---------- Layer 5: Symbol availability ----------
if result:
    print("\n[Layer 5] Symbol availability")
    SYMBOL = "XAUUSD"
    sinfo = mt5.symbol_info(SYMBOL)
    if sinfo is None:
        print(f"  ❌ Symbol {SYMBOL} not found in Market Watch.")
        print(f"  FIX: Open MT5 GUI → Market Watch → right-click → Show All → find XAUUSD → drag to watch.")
    else:
        print(f"  ✅ Symbol {SYMBOL}:")
        print(f"     visible: {sinfo.visible}")
        print(f"     trade_mode: {sinfo.trade_mode}")
        print(f"     point: {sinfo.point}")
        print(f"     volume_min: {sinfo.volume_min}")
        print(f"     volume_step: {sinfo.volume_step}")
        if not sinfo.visible:
            ok = mt5.symbol_select(SYMBOL, True)
            print(f"     trying symbol_select(True): {ok}")
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick:
            print(f"     last tick: bid={tick.bid} ask={tick.ask} time={tick.time}")
        else:
            print(f"  ⚠️  No tick data — terminal may need to subscribe symbol")

# ---------- Layer 6: NSSM service status ----------
print("\n[Layer 6] NSSM service")
try:
    import subprocess
    rc = subprocess.run([r"C:\tools\nssm\nssm.exe", "status", "FluxQuantumAPEX"],
                        capture_output=True, text=True, timeout=5)
    print(f"  Service status: {rc.stdout.strip()}")
    print(f"  ⚠️  IMPORTANT: NSSM runs in Session 0 (services session).")
    print(f"      MT5 GUI runs in Session 2 (user). They CANNOT communicate via IPC.")
    print(f"      Solutions:")
    print(f"        A) Run executor in user session (NOT as service) when broker connection needed")
    print(f"        B) NSSM 'Allow service to interact with desktop' (deprecated, may not work)")
    print(f"        C) Run MT5 portable as a service (mt5 in Session 0)")
except Exception as e:
    print(f"  Service check error: {e}")

mt5.shutdown()
print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)

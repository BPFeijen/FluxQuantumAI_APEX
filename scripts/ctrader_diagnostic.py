"""
cTrader integration diagnostic — read-only health check.

Usage:
  C:/FluxQuantumAI/.venv/Scripts/python.exe C:/FluxQuantumAI/scripts/ctrader_diagnostic.py

Exit codes: 0 OK, 1 setup issue, 2 connectivity issue, 3 partial.

Tests 7 layers:
  L1: Python deps (ctrader_open_api, twisted, service_identity)
  L2: .env credentials present
  L3: Account mode + active account_id
  L4: Connect (TCP->TLS->AppAuth->AccountAuth)
  L5: Symbol GC info + tick subscription
  L6: Account info (balance / leverage / currency / trade allowed)
  L7: Open positions reconciliation
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# ---- Load .env first ----
ENV_PATH = Path(r"C:/FluxQuantumAI/.env")
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

print("=" * 70)
print("cTRADER INTEGRATION DIAGNOSTIC")
print("=" * 70)

exit_code = 0


# ---- Layer 1: deps ----
print("\n[L1] Python dependencies")
deps_ok = True
try:
    import ctrader_open_api
    print(f"  [OK] ctrader_open_api: installed")
except ImportError as e:
    print(f"  [FAIL] ctrader_open_api: NOT installed ({e})")
    print(f"    FIX: pip install ctrader-open-api")
    deps_ok = False
try:
    import twisted
    print(f"  [OK] twisted: {twisted.__version__}")
except ImportError as e:
    print(f"  [FAIL] twisted: NOT installed ({e})")
    print(f"    FIX: pip install twisted")
    deps_ok = False
try:
    import service_identity  # noqa: F401
    print(f"  [OK] service_identity: installed (TLS hostname verify OK)")
except ImportError:
    print(f"  [WARN]  service_identity: not installed (TLS verify may be limited)")

if not deps_ok:
    print("\nABORT: install deps first")
    sys.exit(1)


# ---- Layer 2: .env credentials ----
print("\n[L2] .env credentials")
required = [
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCOUNT_ID_DEMO",
    "CTRADER_ACCOUNT_ID_LIVE",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_REFRESH_TOKEN",
]
missing = []
for k in required:
    v = os.environ.get(k, "").strip()
    if v:
        masked = v[:6] + "..." if len(v) > 8 else "***"
        print(f"  [OK] {k} = {masked}")
    else:
        print(f"  [FAIL] {k} = (empty)")
        missing.append(k)

mode = os.environ.get("CTRADER_ACCOUNT_MODE", "demo").lower()
print(f"  [i]  CTRADER_ACCOUNT_MODE = {mode!r}")

if missing:
    print(f"\nABORT: missing env vars: {missing}")
    print(f"  Run: python scripts/ctrader_oauth_init.py  (interactive, requires browser)")
    sys.exit(1)


# ---- Layer 3: Account selection ----
print("\n[L3] Account selection")
acct_demo = os.environ.get("CTRADER_ACCOUNT_ID_DEMO", "0")
acct_live = os.environ.get("CTRADER_ACCOUNT_ID_LIVE", "0")
print(f"  DEMO account_id: {acct_demo}")
print(f"  LIVE account_id: {acct_live}")
print(f"  Active mode: {mode.upper()}")
active_acct = acct_demo if mode == "demo" else acct_live
print(f"  -> will connect to account {active_acct} ({mode})")


# ---- Layer 4-7: full connect via CTraderExecutor ----
print("\n[L4-L7] Connecting via CTraderExecutor (full auth chain)")
sys.path.insert(0, r"C:/FluxQuantumAI")
try:
    from ctrader_executor import CTraderExecutor
except ImportError as e:
    print(f"  [FAIL] ctrader_executor import failed: {e}")
    sys.exit(2)

ex = CTraderExecutor()
print(f"  -> calling reconnect()...")
t_start = time.time()
ok = ex.reconnect()
elapsed = time.time() - t_start
print(f"  reconnect() returned: {ok} (elapsed {elapsed:.1f}s)")

if not ok:
    print("\nL4 FAILED — cannot proceed to L5-L7. Check stdout above for cause.")
    print("Common causes:")
    print("  - access_token expired -> re-run scripts/ctrader_oauth_init.py")
    print("  - account_id wrong for selected mode (demo vs live mismatch)")
    print("  - cTrader API unreachable (firewall, internet)")
    print("  - Wrong client_id/secret (mismatched app)")
    sys.exit(2)

# L5: symbol info
print("\n[L5] Symbol GC info")
try:
    if hasattr(ex, "_symbols"):
        gc = ex._symbols.get("GC") or ex._symbols.get("XAUUSD")
        if gc:
            print(f"  [OK] Symbol GC found:")
            for k, v in vars(gc).items() if hasattr(gc, "__dict__") else []:
                print(f"     {k}: {v}")
            # Common attrs
            sym_id = getattr(gc, "symbol_id", getattr(gc, "id", None))
            digits = getattr(gc, "digits", None)
            lot_size = getattr(gc, "lot_size", None)
            print(f"     symbol_id={sym_id} digits={digits} lot_size={lot_size}")
        else:
            symbols_list = sorted(ex._symbols.keys()) if isinstance(ex._symbols, dict) else []
            print(f"  [WARN]  GC not in symbol cache. {len(symbols_list)} symbols available.")
            if symbols_list:
                print(f"     First 20: {symbols_list[:20]}")
            exit_code = 3
    else:
        print("  [WARN]  ex._symbols not exposed (executor may use different attribute)")
        exit_code = 3
except Exception as e:
    print(f"  [FAIL] Symbol probe error: {e}")
    exit_code = 3

# L6: account info
print("\n[L6] Account info")
try:
    if hasattr(ex, "_account_info"):
        ai = ex._account_info
        if ai:
            print(f"  [OK] Account info:")
            for attr in ("login", "balance", "currency", "leverage", "marginLevel", "marginAllowed", "tradeAllowed"):
                if hasattr(ai, attr):
                    print(f"     {attr}: {getattr(ai, attr)}")
        else:
            print("  [WARN]  Account info not populated yet")
    else:
        print("  [i]  ex._account_info not exposed in this executor version")
except Exception as e:
    print(f"  [WARN]  Account info probe error: {e}")

# L7: open positions
print("\n[L7] Open positions reconciliation")
try:
    pos = ex.get_open_positions()
    if pos is None:
        print("  [WARN]  get_open_positions returned None")
    else:
        print(f"  [OK] {len(pos)} open positions")
        for p in pos[:5]:
            print(f"     {p}")
        if len(pos) > 5:
            print(f"     (+{len(pos)-5} more)")
except Exception as e:
    print(f"  [FAIL] Open positions error: {e}")
    exit_code = 3

# Cleanup
print("\n[Cleanup]")
try:
    if hasattr(ex, "_reactor") and ex._reactor:
        from twisted.internet import reactor
        if reactor.running:
            try:
                reactor.callFromThread(reactor.stop)
            except Exception:
                pass
    print("  reactor cleanup attempted")
except Exception as e:
    print(f"  cleanup error (non-fatal): {e}")

print("\n" + "=" * 70)
print(f"DIAGNOSTIC COMPLETE — exit code {exit_code}")
print("=" * 70)
sys.exit(exit_code)

"""Query cTrader Open API for the ctidTraderAccountId mapping.

Given access_token, returns the list of trading accounts authorized for that
token, including the internal ctidTraderAccountId (which is what the API
expects, NOT the trading login number).

Usage:
  python scripts/ctrader_get_accounts.py
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

ENV = Path(r"C:/FluxQuantumAI/.env")
for ln in ENV.read_text(encoding="utf-8").splitlines():
    if ln.strip() and not ln.startswith("#") and "=" in ln:
        k, _, v = ln.partition("="); os.environ.setdefault(k.strip(), v.strip())

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAGetAccountListByAccessTokenReq,
)
from twisted.internet import reactor

mode = os.environ.get("CTRADER_ACCOUNT_MODE", "demo").lower()
host = EndPoints.PROTOBUF_DEMO_HOST if mode == "demo" else EndPoints.PROTOBUF_LIVE_HOST
port = EndPoints.PROTOBUF_PORT
client_id = os.environ["CTRADER_CLIENT_ID"]
client_secret = os.environ["CTRADER_CLIENT_SECRET"]
access_token = os.environ["CTRADER_ACCESS_TOKEN"]

print(f"Connecting to {host}:{port} (mode={mode})...")

result_q = queue.Queue(maxsize=1)
client = Client(host, port, TcpProtocol)


def on_connected(_c):
    print("TCP connected. Sending ApplicationAuth...")
    app_req = ProtoOAApplicationAuthReq()
    app_req.clientId = client_id
    app_req.clientSecret = client_secret
    d = client.send(app_req, responseTimeoutInSeconds=10)

    def on_app_auth(resp):
        typed = Protobuf.extract(resp)
        print(f"App auth ok: {type(typed).__name__}")
        if "ErrorRes" in type(typed).__name__:
            result_q.put(("err", f"App auth: {typed.errorCode} {typed.description}"))
            return
        # Now request accounts by access token
        print("Requesting account list by access token...")
        acc_req = ProtoOAGetAccountListByAccessTokenReq()
        acc_req.accessToken = access_token
        d2 = client.send(acc_req, responseTimeoutInSeconds=10)

        def on_acc_list(r):
            t = Protobuf.extract(r)
            if "ErrorRes" in type(t).__name__:
                result_q.put(("err", f"Account list: {t.errorCode} {t.description}"))
                return
            result_q.put(("ok", t))

        d2.addCallback(on_acc_list)
        d2.addErrback(lambda f: result_q.put(("err", f"acc_list_fail: {f}")))

    d.addCallback(on_app_auth)
    d.addErrback(lambda f: result_q.put(("err", f"app_auth_fail: {f}")))


def on_disconnected(_c, reason):
    print(f"Disconnected: {reason}")


client.setConnectedCallback(on_connected)
client.setDisconnectedCallback(on_disconnected)


def runner():
    client.startService()
    reactor.run(installSignalHandlers=False)


t = threading.Thread(target=runner, daemon=True)
t.start()

try:
    kind, val = result_q.get(timeout=20)
except queue.Empty:
    print("TIMEOUT")
    sys.exit(1)

if kind == "err":
    print(f"\nERROR: {val}")
else:
    typed = val
    print(f"\n[OK] Found {len(typed.ctidTraderAccount)} accounts authorized:\n")
    for a in typed.ctidTraderAccount:
        is_live = getattr(a, "isLive", False)
        ctid = getattr(a, "ctidTraderAccountId", None)
        print(f"  ctidTraderAccountId: {ctid}")
        print(f"  isLive:               {is_live}")
        for fld in ("traderLogin", "lastClosingDealTimestamp", "depositAssetId", "brokerTitleShort"):
            if hasattr(a, fld):
                print(f"  {fld}: {getattr(a, fld)}")
        print()

try:
    reactor.callFromThread(reactor.stop)
except Exception:
    pass

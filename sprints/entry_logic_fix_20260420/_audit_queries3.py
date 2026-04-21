"""Deeper analysis of OVEREXT + CONTINUATION decisions + trades matching."""
import json, csv
from collections import Counter
from datetime import datetime, timedelta

PATH = r"C:\FluxQuantumAI\logs\decision_log.jsonl"
TRADES = r"C:\FluxQuantumAI\logs\trades.csv"
TRADES_LIVE = r"C:\FluxQuantumAI\logs\trades_live.csv"

def walk(o, out):
    if isinstance(o, dict):
        for v in o.values():
            walk(v, out)
    elif isinstance(o, list):
        for x in o:
            walk(x, out)
    elif isinstance(o, str):
        out.append(o)

decisions = []
with open(PATH, encoding="utf-8", errors="replace") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        decisions.append(d)

def leaf_bag(d):
    leaves = []
    walk(d, leaves)
    return " | ".join(leaves).upper()

# Collect OVEREXT and CONTINUATION sets
overext = []
cont = []
for d in decisions:
    bag = leaf_bag(d)
    if "OVEREXT" in bag:
        overext.append((d, bag))
    if "CONTINUATION" in bag:
        cont.append((d, bag))

print(f"OVEREXT rows: {len(overext)}")
print(f"CONTINUATION rows: {len(cont)}")

# Breakdown OVEREXT
print("\n=== OVEREXT action x direction breakdown ===")
cnt = Counter()
for d, _ in overext:
    dec = d.get("decision", {}) or {}
    cnt[(dec.get("action",""), dec.get("direction",""))] += 1
for k, v in sorted(cnt.items(), key=lambda x:-x[1]):
    print(f"  {k}: {v}")

print("\n=== OVEREXT daily_trend context ===")
trend_cnt = Counter()
for d, _ in overext:
    ctx = d.get("context", {}) or {}
    trend_cnt[ctx.get("daily_trend","")] += 1
print(trend_cnt)

print("\n=== OVEREXT date range ===")
ts = [d.get("timestamp","") for d, _ in overext]
dates = sorted(set(t[:10] for t in ts if t))
print(f"  first={dates[0] if dates else None}  last={dates[-1] if dates else None}  n_days={len(dates)}")

# Sample 3 GO OVEREXT rows in detail
print("\n=== 3 GO+OVEREXT rows in detail ===")
go_overext = [(d, b) for d, b in overext if (d.get("decision",{}) or {}).get("action","").upper()=="GO"]
print(f"GO+OVEREXT total: {len(go_overext)}")
for d, _ in go_overext[:5]:
    print(json.dumps({
        "ts": d.get("timestamp"),
        "trigger": d.get("trigger"),
        "decision": d.get("decision"),
        "context_daily_trend": (d.get("context",{}) or {}).get("daily_trend"),
        "context_phase": (d.get("context",{}) or {}).get("phase"),
    }, default=str, indent=2)[:600])
    print("---")

# Check CONTINUATION rows
print("\n=== CONTINUATION decisions detail ===")
for d, _ in cont[:20]:
    print(json.dumps({
        "ts": d.get("timestamp"),
        "trigger": d.get("trigger",{}).get("type") if isinstance(d.get("trigger"), dict) else d.get("trigger"),
        "action": (d.get("decision",{}) or {}).get("action"),
        "direction": (d.get("decision",{}) or {}).get("direction"),
        "reason": (d.get("decision",{}) or {}).get("reason","")[:120],
    }, default=str)[:400])

# =============== TRADES MATCHING ===============
print("\n=== TRADES CSV ===")
trades = []
with open(TRADES, newline="", encoding="utf-8", errors="replace") as f:
    rdr = csv.DictReader(f)
    for row in rdr:
        trades.append(row)
print(f"trades.csv rows: {len(trades)}")
if trades:
    print("cols:", list(trades[0].keys()))
    print("first:", dict(list(trades[0].items())[:10]))

trades_live = []
try:
    with open(TRADES_LIVE, newline="", encoding="utf-8", errors="replace") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            trades_live.append(row)
    print(f"\ntrades_live.csv rows: {len(trades_live)}")
    if trades_live:
        print("cols:", list(trades_live[0].keys()))
        print("first:", dict(list(trades_live[0].items())[:10]))
except Exception as e:
    print("trades_live read error:", e)

# Try to match OVEREXT decisions → trades by timestamp (within 60s)
def parse_ts(s):
    try:
        # handle 'Z' and offsets
        s2 = s.replace('Z','+00:00') if s else ''
        return datetime.fromisoformat(s2)
    except Exception:
        return None

# Go OVEREXT timestamps
go_overext_ts = []
for d, _ in go_overext:
    t = parse_ts(d.get("timestamp",""))
    if t: go_overext_ts.append((t, d))

# Trade timestamps - need to find column
if trades:
    ts_col = None
    for k in trades[0].keys():
        if "time" in k.lower() or k.lower() in ("ts","datetime","open_time","entry_time"):
            ts_col = k
            break
    print(f"\ntrades ts_col: {ts_col}")
    if ts_col:
        matched = 0
        for gt, gd in go_overext_ts:
            for tr in trades:
                trt = parse_ts(tr.get(ts_col, ""))
                if trt and abs((trt - gt).total_seconds()) < 120:
                    matched += 1
                    break
        print(f"GO+OVEREXT matched to trade (within 120s): {matched}/{len(go_overext_ts)}")

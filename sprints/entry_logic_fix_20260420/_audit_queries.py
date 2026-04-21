"""Read-only audit queries on decision_log.jsonl - Sprint B v2."""
import json
from collections import Counter
from datetime import datetime

PATH = r"C:\FluxQuantumAI\logs\decision_log.jsonl"

all_decisions = []
with open(PATH, encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        all_decisions.append(d)

print(f"TOTAL decisions: {len(all_decisions)}")

# Date range
ts_list = []
for d in all_decisions:
    ts = d.get("ts") or d.get("timestamp") or d.get("time")
    if ts:
        ts_list.append(ts)
if ts_list:
    print(f"FIRST ts: {min(ts_list)}")
    print(f"LAST  ts: {max(ts_list)}")

# Helper to extract all text fields
def blob(d):
    parts = []
    dec = d.get("decision", {}) or {}
    parts.append(str(dec.get("reason", "")))
    parts.append(str(dec.get("action", "")))
    parts.append(str(dec.get("direction", "")))
    parts.append(str(d.get("strat_reason", "")))
    parts.append(str(d.get("trigger", "")))
    parts.append(str(d.get("reason", "")))
    parts.append(str(d.get("strategy_reason", "")))
    return " | ".join(parts).upper()

# =============== OVEREXTENSION =================
overext = [d for d in all_decisions if any(k in blob(d) for k in ("OVEREXTEND", "OVEREXT"))]
print("\n=== OVEREXTENSION ===")
print(f"count: {len(overext)}")
action_cnt = Counter()
dir_cnt = Counter()
date_cnt = Counter()
for d in overext:
    dec = d.get("decision", {}) or {}
    action_cnt[str(dec.get("action", ""))] += 1
    dir_cnt[str(dec.get("direction", ""))] += 1
    ts = d.get("ts") or d.get("timestamp") or ""
    date_cnt[str(ts)[:10]] += 1
print(f"action: {action_cnt}")
print(f"direction: {dir_cnt}")
print(f"date range: {sorted(date_cnt.keys())[:3]} .. {sorted(date_cnt.keys())[-3:]}")
print(f"dates count: {len(date_cnt)}")

# show 3 sample overext rows
print("\n-- 3 sample OVEREXT rows --")
for d in overext[:3]:
    print(json.dumps({k: d.get(k) for k in ("ts","timestamp","trigger","decision","strat_reason","strategy_reason")}, default=str)[:500])

# Also look at GO overext
go_overext = [d for d in overext if (d.get("decision",{}) or {}).get("action","").upper() == "GO"]
print(f"\nGO overext count: {len(go_overext)}")
for d in go_overext[:5]:
    print(json.dumps({k: d.get(k) for k in ("ts","timestamp","trigger","decision","strat_reason","strategy_reason")}, default=str)[:500])

# =============== PULLBACK / DELTA / PATCH2A / CONTINUATION =================
print("\n=== PULLBACK / DELTA / PATCH2A / CONTINUATION ===")
for keyword in ("PULLBACK", "DELTA", "PATCH2A", "CONTINUATION"):
    hits = [d for d in all_decisions if keyword in blob(d)]
    actions = Counter((d.get("decision",{}) or {}).get("action","") for d in hits)
    dirs = Counter((d.get("decision",{}) or {}).get("direction","") for d in hits)
    print(f"{keyword}: count={len(hits)}  action={dict(actions)}  dir={dict(dirs)}")

# PULLBACK GO
pb_go = [d for d in all_decisions
         if "PULLBACK" in blob(d) and (d.get("decision",{}) or {}).get("action","").upper() == "GO"]
print(f"\nPULLBACK + GO: {len(pb_go)}")
for d in pb_go[:5]:
    print(json.dumps({k: d.get(k) for k in ("ts","timestamp","trigger","decision","strat_reason")}, default=str)[:400])

# DELTA GO
delta_go = [d for d in all_decisions
            if "DELTA" in blob(d) and (d.get("decision",{}) or {}).get("action","").upper() == "GO"]
print(f"\nDELTA + GO: {len(delta_go)}")
for d in delta_go[:5]:
    print(json.dumps({k: d.get(k) for k in ("ts","timestamp","trigger","decision","strat_reason")}, default=str)[:400])

# PATCH2A GO
p2a_go = [d for d in all_decisions
          if "PATCH2A" in blob(d) and (d.get("decision",{}) or {}).get("action","").upper() == "GO"]
print(f"\nPATCH2A + GO: {len(p2a_go)}")

# CONTINUATION GO
cont_go = [d for d in all_decisions
           if "CONTINUATION" in blob(d) and (d.get("decision",{}) or {}).get("action","").upper() == "GO"]
print(f"CONTINUATION + GO: {len(cont_go)}")
for d in cont_go[:5]:
    print(json.dumps({k: d.get(k) for k in ("ts","timestamp","trigger","decision","strat_reason")}, default=str)[:400])

# =============== TRIGGER field counts ===============
print("\n=== TRIGGER field distribution ===")
trig_cnt = Counter()
for d in all_decisions:
    t = d.get("trigger")
    if isinstance(t, dict):
        t = t.get("name") or t.get("type") or str(t)[:30]
    trig_cnt[str(t)] += 1
for t, c in trig_cnt.most_common(15):
    print(f"  {t[:60]}: {c}")

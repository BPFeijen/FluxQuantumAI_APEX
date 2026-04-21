"""Cross-reference continuation_trades GO with decision_log ALPHA entries."""
import json
from datetime import datetime, timedelta

D = r"C:\FluxQuantumAI\logs\decision_log.jsonl"
CT = r"C:\FluxQuantumAI\logs\continuation_trades.jsonl"

def parse(s):
    try:
        return datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception:
        return None

# Load continuation GO rows, bucketed by minute
cont_go = []
with open(CT, encoding="utf-8", errors="replace") as f:
    for ln in f:
        ln = ln.strip()
        if not ln: continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("decision") == "GO":
            t = parse(d.get("timestamp",""))
            if t: cont_go.append((t, d))

print(f"continuation GO events: {len(cont_go)}")

# Unique minutes where CONTINUATION said GO
cont_go_minutes = set(t.replace(second=0, microsecond=0) for t,_ in cont_go)
print(f"unique minutes with CONTINUATION GO: {len(cont_go_minutes)}")

# Load decision_log
ddec = []
with open(D, encoding="utf-8", errors="replace") as f:
    for ln in f:
        ln = ln.strip()
        if not ln: continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        t = parse(d.get("timestamp",""))
        if t:
            ddec.append((t, d))
print(f"decision_log rows: {len(ddec)}")

# For each CONTINUATION-GO minute, check: was there any ALPHA decision in that minute?
alpha_in_cont_minute = 0
alpha_go_in_cont_minute = 0
alpha_long_in_cont_minute = 0
alpha_long_go_in_cont_minute = 0
for t, d in ddec:
    tmin = t.replace(second=0, microsecond=0)
    if tmin in cont_go_minutes:
        trig = d.get("trigger",{}).get("type","") if isinstance(d.get("trigger"), dict) else ""
        dec = d.get("decision",{}) or {}
        if trig == "ALPHA":
            alpha_in_cont_minute += 1
            dirn = dec.get("direction","")
            if dec.get("action") in ("GO","EXEC_FAILED"):
                alpha_go_in_cont_minute += 1
                if dirn == "LONG":
                    alpha_long_go_in_cont_minute += 1
            if dirn == "LONG":
                alpha_long_in_cont_minute += 1

print(f"ALPHA decisions in CONT-GO minutes: {alpha_in_cont_minute}")
print(f"ALPHA GO/EXEC_FAILED in CONT-GO minutes: {alpha_go_in_cont_minute}")
print(f"ALPHA LONG in CONT-GO minutes (any action): {alpha_long_in_cont_minute}")
print(f"ALPHA LONG GO/EXEC_FAILED in CONT-GO minutes: {alpha_long_go_in_cont_minute}")

# Conversely: how many CONTINUATION GO ALSO have context that shows the trigger was ALPHA-only (no PATCH2A)?
# — already established: decision_log has zero PATCH2A entries.

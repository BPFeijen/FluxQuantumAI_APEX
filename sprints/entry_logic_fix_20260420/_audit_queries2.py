"""Deep search for PULLBACK/DELTA/CONTINUATION strings in ALL fields."""
import json
from collections import Counter

PATH = r"C:\FluxQuantumAI\logs\decision_log.jsonl"

def walk(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, out)
    elif isinstance(o, list):
        for x in o:
            walk(x, out)
    elif isinstance(o, str):
        out.append(o)

with open(PATH, encoding="utf-8", errors="replace") as f:
    lines = [ln for ln in f if ln.strip()]
print(f"lines: {len(lines)}")

# Sample first decision fully
d0 = json.loads(lines[0])
print("\n=== KEY TOP-LEVEL FIELDS ===")
for k in d0.keys():
    v = d0[k]
    if isinstance(v, (dict, list)):
        print(f"  {k}: {type(v).__name__} len={len(v)}")
    else:
        s = str(v)[:80]
        print(f"  {k}: {s}")

print("\n=== FULL SAMPLE d0 (keys: trigger, decision, context) ===")
for k in ("trigger", "decision", "context", "scores", "strat_reason", "strategy_reason"):
    if k in d0:
        print(f"-- {k} --")
        print(json.dumps(d0[k], default=str, indent=2)[:800])

# Now brute search: for each decision, collect all string leaves, look for keywords
kw_map = {"OVEREXTEND":0, "OVEREXT":0, "PULLBACK":0, "DELTA_TRIG":0, "PATCH2A":0, "CONTINUATION":0,
          "TRENDING_UP":0, "TRENDING_DN":0, "LIQ_BOT":0, "LIQ_TOP":0}
sample_hits = {k:[] for k in kw_map}

for i, ln in enumerate(lines):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    leaves = []
    walk(d, leaves)
    bag = " | ".join(leaves).upper()
    for kw in kw_map:
        if kw in bag:
            kw_map[kw] += 1
            if len(sample_hits[kw]) < 2:
                sample_hits[kw].append((i, bag[:500]))

print("\n=== KEYWORD COUNTS (deep walk, case insensitive) ===")
for k, v in kw_map.items():
    print(f"  {k}: {v}")

print("\n=== SAMPLE HITS ===")
for kw, hits in sample_hits.items():
    if hits:
        print(f"\n-- {kw} --")
        for i, b in hits[:1]:
            print(f"  line {i}: {b[:400]}")

# Now find rows where decision.reason contains PULLBACK etc
def dec_reason(d):
    r = (d.get("decision", {}) or {}).get("reason", "")
    return str(r).upper()

print("\n=== decision.reason distribution (top 30) ===")
reasons = Counter()
for ln in lines:
    try:
        d = json.loads(ln)
    except Exception:
        continue
    r = dec_reason(d)
    # normalize by taking first 80 chars
    reasons[r[:100]] += 1
for r, c in reasons.most_common(30):
    print(f"  {c:5d}: {r}")

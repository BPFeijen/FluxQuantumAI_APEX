"""Analyze continuation_trades.jsonl - shadow log of PATCH2A/_get_trend_entry_mode attempts."""
import json
from collections import Counter

P = r"C:\FluxQuantumAI\logs\continuation_trades.jsonl"

decision_cnt = Counter()
reason_cnt = Counter()
direction_cnt = Counter()
go_rows = []
with open(P, encoding="utf-8", errors="replace") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        dec = d.get("decision","")
        decision_cnt[dec] += 1
        reason_cnt[d.get("reason","")[:80]] += 1
        direction_cnt[(dec, d.get("direction",""))] += 1
        if dec == "GO":
            go_rows.append(d)

print(f"Total: {sum(decision_cnt.values())}")
print(f"decision: {decision_cnt}")
print(f"\ndirection x decision: {direction_cnt}")
print(f"\nTop 15 reasons:")
for r, c in reason_cnt.most_common(15):
    print(f"  {c:6d}: {r}")
print(f"\nGO rows: {len(go_rows)}")
for g in go_rows[:10]:
    print(f"  {g.get('timestamp')} dir={g.get('direction')} phase={g.get('phase')} price={g.get('price')} reason={g.get('reason','')[:120]}")

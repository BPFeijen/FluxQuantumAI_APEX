#!/usr/bin/env python3
"""
FASE 2a Post-Session Analysis: Near-Level Source Classification.

Reads the event_processor log file and extracts NEAR_LEVEL_SOURCE entries
to deliver the required metrics:
  - count of triggers by source type (m5+m30 / m30_only / m5_only)
  - percentage breakdown
  - concrete examples of each type
  - which trades would be classified differently
  - impact if m5_only were disabled

Usage: python scripts/analyze_fase2a_sources.py [log_file]
       Default: C:/FluxQuantumAI/logs/event_processor.log
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_LOG = Path("C:/FluxQuantumAI/logs/event_processor.log")


def parse_source_entries(log_path: Path) -> list[dict]:
    """Parse NEAR_LEVEL_SOURCE log entries."""
    entries = []
    pattern = re.compile(
        r"NEAR_LEVEL_SOURCE:\s+(m5\+m30|m30_only|m5_only)\s+\|\s+"
        r"price=([\d.]+)\s+\|\s+"
        r"m5=(\w*)\(([\d.]+)\)\s+"
        r"m30=(\w*)\(([\d.]+)\)"
    )
    with open(log_path, "r", errors="replace") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                # Extract timestamp from start of line
                ts_match = re.match(r"([\d\-]+\s+[\d:,]+)", line)
                ts = ts_match.group(1) if ts_match else "?"
                entries.append({
                    "ts": ts,
                    "source": m.group(1),
                    "price": float(m.group(2)),
                    "m5_type": m.group(3),
                    "m5_price": float(m.group(4)),
                    "m30_type": m.group(5),
                    "m30_price": float(m.group(6)),
                })
    return entries


def parse_gate_triggers(log_path: Path) -> list[dict]:
    """Parse gate triggered entries to correlate source with actual trades."""
    triggers = []
    pattern = re.compile(
        r"\[(m5\+m30|m30_only|m5_only)\].*"
        r"(GATE CHECK|GATE TRIGGERED)"
    )
    with open(log_path, "r", errors="replace") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                ts_match = re.match(r"([\d\-]+\s+[\d:,]+)", line)
                ts = ts_match.group(1) if ts_match else "?"
                triggers.append({
                    "ts": ts,
                    "source": m.group(1),
                    "line": line.strip(),
                })
    return triggers


def main():
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        print("Run this after a live session with FASE 2a active.")
        sys.exit(1)

    print("=" * 70)
    print("  FASE 2a POST-SESSION ANALYSIS: Near-Level Source Classification")
    print(f"  Log: {log_path}")
    print("=" * 70)

    entries = parse_source_entries(log_path)
    triggers = parse_gate_triggers(log_path)

    if not entries:
        print("\n  No NEAR_LEVEL_SOURCE entries found in log.")
        print("  Ensure FASE 2a code is deployed and a session has run.")
        sys.exit(0)

    # 1. Count by source type
    source_counts = Counter(e["source"] for e in entries)
    total = len(entries)

    print(f"\n  1. SOURCE TYPE COUNTS (total near-level ticks: {total})")
    print(f"     {'Source':<12s}  {'Count':>7s}  {'Pct':>6s}")
    print(f"     {'-'*12}  {'-'*7}  {'-'*6}")
    for src in ["m5+m30", "m30_only", "m5_only"]:
        cnt = source_counts.get(src, 0)
        pct = 100 * cnt / total if total else 0
        print(f"     {src:<12s}  {cnt:>7d}  {pct:>5.1f}%")

    # 2. Gate triggers by source
    if triggers:
        trigger_counts = Counter(t["source"] for t in triggers)
        total_trig = len(triggers)
        print(f"\n  2. GATE TRIGGERS BY SOURCE (total: {total_trig})")
        print(f"     {'Source':<12s}  {'Count':>7s}  {'Pct':>6s}")
        print(f"     {'-'*12}  {'-'*7}  {'-'*6}")
        for src in ["m5+m30", "m30_only", "m5_only"]:
            cnt = trigger_counts.get(src, 0)
            pct = 100 * cnt / total_trig if total_trig else 0
            print(f"     {src:<12s}  {cnt:>7d}  {pct:>5.1f}%")
    else:
        print("\n  2. No gate triggers with source tags found.")

    # 3. Examples of each type (first 3)
    print(f"\n  3. EXAMPLES BY TYPE")
    by_source = defaultdict(list)
    for e in entries:
        by_source[e["source"]].append(e)

    for src in ["m5+m30", "m30_only", "m5_only"]:
        examples = by_source.get(src, [])
        print(f"\n     [{src}] ({len(examples)} total)")
        for e in examples[:3]:
            print(f"       {e['ts']}  price={e['price']:.2f}"
                  f"  m5={e['m5_type']}({e['m5_price']:.2f})"
                  f"  m30={e['m30_type']}({e['m30_price']:.2f})")
        if len(examples) > 3:
            print(f"       ... and {len(examples) - 3} more")

    # 4. Impact if m5_only disabled
    m5_only_count = source_counts.get("m5_only", 0)
    m5_only_pct = 100 * m5_only_count / total if total else 0
    m5_only_triggers = sum(1 for t in triggers if t["source"] == "m5_only")

    print(f"\n  4. IMPACT IF m5_only DISABLED")
    print(f"     Near-level ticks lost : {m5_only_count} / {total} ({m5_only_pct:.1f}%)")
    print(f"     Gate triggers lost    : {m5_only_triggers}")

    if m5_only_pct < 5:
        print(f"     VERDICT: m5_only is RESIDUAL ({m5_only_pct:.1f}%) — safe to disable in FASE 2b")
    elif m5_only_pct < 20:
        print(f"     VERDICT: m5_only is MODERATE ({m5_only_pct:.1f}%) — audit case-by-case before disabling")
    else:
        print(f"     VERDICT: m5_only is SIGNIFICANT ({m5_only_pct:.1f}%) — do NOT disable without investigation")

    if m5_only_triggers > 0:
        print(f"\n     m5_only triggers that would be LOST:")
        for t in triggers:
            if t["source"] == "m5_only":
                print(f"       {t['ts']}  {t['line'][:100]}")

    print()
    print("=" * 70)


if __name__ == "__main__":
    main()

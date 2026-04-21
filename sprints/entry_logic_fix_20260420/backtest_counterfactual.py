"""Backtest counterfactual: replay decision_log.jsonl with direction-aware filter.

Sprint A entry_logic_fix_20260420 -- literatura-aligned C1 (universal post-validation).

Approximation methodology:
  For each GO signal in decision_log.jsonl, check the FIRED trigger level vs price:
    - SHORT must have level_price_mt5 > price_mt5 (or == for is_touch)
    - LONG  must have level_price_mt5 < price_mt5 (or == for is_touch)
  If the fired level is wrong-side relative to the resolved direction, the new C1
  post-validation would have suppressed the fire (either FAR because no valid candidate,
  or NEAR because the closest valid is out-of-band). We label this NEW_REJECT_WRONG_SIDE.

This is NOT a full parquet replay -- we cannot reconstruct M5/M30 box state per-tick.
What we CAN say accurately is: decisions whose fired level is literatura-invalid for the
resolved direction would NOT have been emitted under C1.

Decisions whose fired level IS literatura-valid would typically remain as GO (the new
filter still finds at least one valid candidate within band). Treated as IDENTICAL
approximation.

Output: BACKTEST_COUNTERFACTUAL.md
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

DECISION_LOG = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
OUTPUT       = Path(r"C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\BACKTEST_COUNTERFACTUAL.md")
TARGET_SIGNAL_TS_PREFIX = "2026-04-20T03:14:4"


def classify(dec: dict) -> tuple[str, dict]:
    """Classify a GO decision as IDENTICAL or NEW_REJECT_WRONG_SIDE.

    Returns (label, details_dict).
    """
    price = float(dec.get("price_mt5") or 0.0)
    direction = dec.get("decision", {}).get("direction", "")
    trg = dec.get("trigger") or {}
    level_type = trg.get("level_type") or ""
    level_price = trg.get("level_price_mt5")
    if level_price is None:
        level_price = trg.get("level_price")
    try:
        level_price = float(level_price) if level_price is not None else None
    except (TypeError, ValueError):
        level_price = None

    details = {
        "ts": dec.get("timestamp"),
        "direction": direction,
        "price_mt5": price,
        "level_type": level_type,
        "level_price_mt5": level_price,
        "trigger_type": trg.get("type"),
    }

    if direction not in ("SHORT", "LONG") or level_price is None or price <= 0:
        return ("CANNOT_REPLAY", details)

    # Direction-aware validity check (mirrors get_levels_for_direction logic)
    if direction == "SHORT":
        is_valid = (level_price > price) or (level_price == price)
    else:  # LONG
        is_valid = (level_price < price) or (level_price == price)

    if is_valid:
        return ("IDENTICAL_APPROX", details)
    else:
        details["delta_wrong_side"] = round(price - level_price if direction == "SHORT" else level_price - price, 2)
        return ("NEW_REJECT_WRONG_SIDE", details)


def main() -> int:
    if not DECISION_LOG.exists():
        print(f"decision_log not found: {DECISION_LOG}")
        return 1

    stats = Counter()
    by_direction = Counter()
    by_trigger = Counter()
    rejected_samples: list[dict] = []
    target_outcome: dict | None = None

    with DECISION_LOG.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                dec = json.loads(line)
            except json.JSONDecodeError:
                stats["json_error"] += 1
                continue
            stats["total"] += 1
            action = dec.get("decision", {}).get("action", "")
            if action != "GO":
                continue
            stats["go_signals"] += 1

            label, det = classify(dec)
            stats[label] += 1
            by_direction[(label, det["direction"])] += 1
            by_trigger[(label, det.get("trigger_type") or "?")] += 1

            if label == "NEW_REJECT_WRONG_SIDE":
                if len(rejected_samples) < 50:
                    rejected_samples.append(det)

            ts = det.get("ts") or ""
            if ts.startswith(TARGET_SIGNAL_TS_PREFIX):
                target_outcome = {"label": label, **det}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        f.write("# Backtest Counterfactual -- near_level direction-aware (literatura)\n\n")
        f.write("Sprint: entry_logic_fix_20260420\n")
        f.write("Methodology: approximate replay using recorded trigger.level_price_mt5 vs price_mt5.\n")
        f.write("See module docstring for limitations.\n\n")
        f.write(f"Source log: `{DECISION_LOG}`\n\n")

        f.write("## Summary stats\n\n")
        for k in ("total", "go_signals", "json_error", "IDENTICAL_APPROX",
                  "NEW_REJECT_WRONG_SIDE", "CANNOT_REPLAY"):
            f.write(f"- **{k}**: {stats.get(k, 0)}\n")
        if stats["go_signals"]:
            pct = 100.0 * stats["NEW_REJECT_WRONG_SIDE"] / stats["go_signals"]
            f.write(f"- **NEW_REJECT rate on GO signals**: {pct:.2f}%\n")
        f.write("\n")

        f.write("## Breakdown by direction\n\n")
        f.write("| label | direction | count |\n|---|---|---|\n")
        for (label, d), c in sorted(by_direction.items(), key=lambda x: -x[1]):
            f.write(f"| {label} | {d} | {c} |\n")
        f.write("\n")

        f.write("## Breakdown by trigger type\n\n")
        f.write("| label | trigger | count |\n|---|---|---|\n")
        for (label, t), c in sorted(by_trigger.items(), key=lambda x: -x[1]):
            f.write(f"| {label} | {t} | {c} |\n")
        f.write("\n")

        f.write("## Target signal 03:14:46 UTC 2026-04-20\n\n")
        if target_outcome:
            f.write("```json\n")
            f.write(json.dumps(target_outcome, indent=2))
            f.write("\n```\n\n")
            if target_outcome.get("label") == "NEW_REJECT_WRONG_SIDE":
                f.write("**Result: REJECTED under C1 post-validation** (wrong-side level).\n\n")
            else:
                f.write(f"**Result: {target_outcome.get('label')}** -- review needed.\n\n")
        else:
            f.write("No decision found with timestamp prefix `"
                    + TARGET_SIGNAL_TS_PREFIX + "`.\n\n")

        f.write("## Rejected samples (up to 50)\n\n")
        if rejected_samples:
            f.write("| ts | direction | level_type | level_price | price | delta_wrong_side |\n")
            f.write("|---|---|---|---|---|---|\n")
            for d in rejected_samples:
                f.write(f"| {d.get('ts')} | {d.get('direction')} | {d.get('level_type')} | "
                        f"{d.get('level_price_mt5')} | {d.get('price_mt5')} | "
                        f"{d.get('delta_wrong_side', '')} |\n")
        else:
            f.write("_None._\n")
        f.write("\n")

        f.write("## Interpretation\n\n")
        f.write("- `NEW_REJECT_WRONG_SIDE` = GO signals where the fired trigger level was on\n")
        f.write("  the literatura-invalid side for the resolved direction (SHORT with level below\n")
        f.write("  price, or LONG with level above price). Under C1 post-validation these would\n")
        f.write("  not have been emitted.\n")
        f.write("- `IDENTICAL_APPROX` = fired level was on the correct side. The C1 filter would\n")
        f.write("  typically still pass these, though full parquet replay could reveal edge cases\n")
        f.write("  where the top candidate moves out-of-band (NEAR).\n")
        f.write("- `CANNOT_REPLAY` = decision missing required fields.\n")

    print(f"Wrote {OUTPUT}")
    print(f"go_signals={stats['go_signals']} "
          f"new_reject={stats['NEW_REJECT_WRONG_SIDE']} "
          f"identical={stats['IDENTICAL_APPROX']} "
          f"cannot_replay={stats['CANNOT_REPLAY']}")
    if target_outcome:
        print(f"03:14 target: {target_outcome.get('label')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

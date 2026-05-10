"""3-day signal direction inversion audit.

For each GO signal in last 3 days, measure forward price move at multiple
horizons (5min, 15min, 30min, 60min) and classify the signal as:
  - RIGHT: direction matches subsequent price move
  - WRONG: direction opposite to subsequent price move
  - NEUTRAL: |move| < threshold (no clear winner)

Aggregates by day + direction + horizon. Identifies systemic inversion.

Source: decision_log.jsonl (signals) + Quantower microstructure_*.csv.gz (price tape)
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

DECISION_LOG = Path("C:/FluxQuantumAI/logs/decision_log.jsonl")
MICRO_DIR = Path("C:/data/level2/_gc_xcec")
DAYS_BACK = 3
HORIZONS_MIN = [5, 15, 30, 60]
NEUTRAL_PT_THRESHOLD = 2.0  # |move| < 2 pts → NEUTRAL


def load_signals(cutoff: datetime) -> list[dict]:
    """Pull all GO signals from decision_log since cutoff."""
    signals = []
    with open(DECISION_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = d.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if ts < cutoff:
                continue
            dec = d.get("decision", {})
            if dec.get("action") != "GO":
                continue
            direction = dec.get("direction")
            if direction not in ("LONG", "SHORT"):
                continue
            price_gc = d.get("price_gc")
            if price_gc is None or price_gc <= 0:
                continue
            ctx = d.get("context", {})
            signals.append({
                "ts": ts,
                "direction": direction,
                "price_gc": float(price_gc),
                "decision_id": dec.get("decision_id") or d.get("decision_id", "")[:8],
                "reason": dec.get("reason", ""),
                "score": dec.get("total_score", 0),
                "bias": ctx.get("m30_bias", "?"),
                "bias_confirmed": ctx.get("m30_bias_confirmed", False),
            })
    return signals


def load_micro_index(cutoff: datetime) -> list[tuple[datetime, float]]:
    """Build a sorted (timestamp, mid_price) index from microstructure files."""
    idx = []
    cutoff_date = cutoff.date()
    today = datetime.now(timezone.utc).date()
    d = cutoff_date
    while d <= today:
        d_str = d.strftime("%Y-%m-%d")
        for suffix in [".csv.gz", ".fixed.csv.gz"]:
            path = MICRO_DIR / f"microstructure_{d_str}{suffix}"
            if not path.exists():
                continue
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    header = f.readline().strip().split(",")
                    ts_col = header.index("timestamp")
                    px_col = header.index("mid_price")
                    for line in f:
                        parts = line.rstrip().split(",")
                        if len(parts) <= max(ts_col, px_col):
                            continue
                        try:
                            t = datetime.fromisoformat(parts[ts_col].replace("Z", "+00:00"))
                            if t.tzinfo is None:
                                t = t.replace(tzinfo=timezone.utc)
                            px = float(parts[px_col])
                            if px <= 0:
                                continue
                            idx.append((t, px))
                        except (ValueError, IndexError):
                            continue
                break
            except Exception as e:
                print(f"  warn: {path.name}: {e}")
        d = d + timedelta(days=1)
    idx.sort(key=lambda r: r[0])
    return idx


def price_at(idx: list[tuple], target: datetime) -> float | None:
    """Binary search for price at target time (forward fill)."""
    import bisect
    keys = [r[0] for r in idx]
    pos = bisect.bisect_right(keys, target)
    if pos == 0:
        return None
    return idx[pos - 1][1]


def classify(direction: str, entry_gc: float, future_gc: float | None) -> tuple[str, float]:
    if future_gc is None:
        return "NO_DATA", 0.0
    move = future_gc - entry_gc
    if abs(move) < NEUTRAL_PT_THRESHOLD:
        return "NEUTRAL", move
    if direction == "LONG":
        return ("RIGHT" if move > 0 else "WRONG"), move
    else:  # SHORT
        return ("RIGHT" if move < 0 else "WRONG"), move


def main() -> int:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)
    print(f"=== 3-day signal inversion audit ===")
    print(f"  cutoff: {cutoff.isoformat()}")
    print(f"  now:    {now.isoformat()}")
    print()

    print("Loading signals from decision_log.jsonl ...")
    signals = load_signals(cutoff)
    print(f"  {len(signals)} GO signals in last {DAYS_BACK} days")
    print()

    print("Loading microstructure price index (Quantower)...")
    micro_idx = load_micro_index(cutoff)
    print(f"  {len(micro_idx):,} ticks indexed")
    if not micro_idx:
        print("  FAIL: no micro data — cannot classify")
        return 1
    print(f"  range: {micro_idx[0][0].isoformat()} to {micro_idx[-1][0].isoformat()}")
    print()

    print(f"Classifying each signal at {HORIZONS_MIN} min horizons...")
    print(f"  NEUTRAL threshold: |move| < {NEUTRAL_PT_THRESHOLD} pts")
    print()

    # Aggregations
    by_horizon = {h: defaultdict(int) for h in HORIZONS_MIN}
    by_horizon_dir = {h: defaultdict(lambda: defaultdict(int)) for h in HORIZONS_MIN}
    by_day = defaultdict(lambda: defaultdict(int))   # day -> outcome -> count (15min horizon)
    by_day_dir = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # day -> dir -> outcome (15min)
    pts_summary = defaultdict(list)  # outcome -> list of moves (15min)

    samples_wrong = []   # save first few WRONG for inspection
    samples_right = []

    for s in signals:
        for h in HORIZONS_MIN:
            future_t = s["ts"] + timedelta(minutes=h)
            future_px = price_at(micro_idx, future_t)
            outcome, move = classify(s["direction"], s["price_gc"], future_px)
            by_horizon[h][outcome] += 1
            by_horizon_dir[h][s["direction"]][outcome] += 1
            if h == 15:
                day = s["ts"].strftime("%Y-%m-%d")
                by_day[day][outcome] += 1
                by_day_dir[day][s["direction"]][outcome] += 1
                pts_summary[outcome].append(move)
                if outcome == "WRONG" and len(samples_wrong) < 8:
                    samples_wrong.append((s, future_px, move))
                elif outcome == "RIGHT" and len(samples_right) < 4:
                    samples_right.append((s, future_px, move))

    # Report
    print("=" * 70)
    print("AGGREGATE BY HORIZON")
    print("=" * 70)
    for h in HORIZONS_MIN:
        total = sum(by_horizon[h].values())
        if total == 0:
            continue
        right = by_horizon[h].get("RIGHT", 0)
        wrong = by_horizon[h].get("WRONG", 0)
        neutral = by_horizon[h].get("NEUTRAL", 0)
        nodata = by_horizon[h].get("NO_DATA", 0)
        decided = right + wrong
        wrong_pct = wrong / decided * 100 if decided else 0
        right_pct = right / decided * 100 if decided else 0
        print(f"\nHorizon {h:>3} min:  total={total}  decided={decided}")
        print(f"  RIGHT:   {right:>5} ({right_pct:5.1f}% of decided)")
        print(f"  WRONG:   {wrong:>5} ({wrong_pct:5.1f}% of decided)")
        print(f"  NEUTRAL: {neutral:>5}  NO_DATA: {nodata:>5}")

    print()
    print("=" * 70)
    print("BY DIRECTION (15min horizon)")
    print("=" * 70)
    for direction in ("LONG", "SHORT"):
        d_data = by_horizon_dir[15][direction]
        total = sum(d_data.values())
        if total == 0:
            continue
        right = d_data.get("RIGHT", 0)
        wrong = d_data.get("WRONG", 0)
        neutral = d_data.get("NEUTRAL", 0)
        decided = right + wrong
        right_pct = right / decided * 100 if decided else 0
        wrong_pct = wrong / decided * 100 if decided else 0
        print(f"\n{direction}:  total={total}  decided={decided}")
        print(f"  RIGHT:   {right:>5} ({right_pct:5.1f}%)")
        print(f"  WRONG:   {wrong:>5} ({wrong_pct:5.1f}%)")
        print(f"  NEUTRAL: {neutral:>5}")

    print()
    print("=" * 70)
    print("BY DAY x DIRECTION (15min horizon)")
    print("=" * 70)
    for day in sorted(by_day_dir.keys()):
        print(f"\n--- {day} ---")
        for direction in ("LONG", "SHORT"):
            d_data = by_day_dir[day][direction]
            total = sum(d_data.values())
            if total == 0:
                continue
            right = d_data.get("RIGHT", 0)
            wrong = d_data.get("WRONG", 0)
            neutral = d_data.get("NEUTRAL", 0)
            decided = right + wrong
            wrong_pct = wrong / decided * 100 if decided else 0
            print(f"  {direction:5s}: total={total:>4}  RIGHT={right:>4}  WRONG={wrong:>4}  NEUT={neutral:>4}"
                  f"  WRONG%={wrong_pct:5.1f}")

    print()
    print("=" * 70)
    print("MOVE STATS @ 15min (decided signals only)")
    print("=" * 70)
    for outcome in ("RIGHT", "WRONG", "NEUTRAL"):
        moves = pts_summary[outcome]
        if not moves:
            continue
        n = len(moves)
        mean = sum(moves) / n
        abs_mean = sum(abs(m) for m in moves) / n
        print(f"  {outcome:7s}: n={n:>5}  mean_signed={mean:+6.2f}pts  mean_abs={abs_mean:5.2f}pts")

    print()
    print("=" * 70)
    print("SAMPLE WRONG SIGNALS (15min)")
    print("=" * 70)
    for s, fp, m in samples_wrong:
        print(f"  {s['ts'].strftime('%Y-%m-%d %H:%M:%S')}  {s['direction']:5}  "
              f"@{s['price_gc']:7.2f}  →15min @{fp:7.2f}  move={m:+6.2f}  "
              f"score={s['score']:+d} bias={s['bias']:7s} reason={s['reason'][:50]}")
    print()
    print("=" * 70)
    print("SAMPLE RIGHT SIGNALS (15min)")
    print("=" * 70)
    for s, fp, m in samples_right:
        print(f"  {s['ts'].strftime('%Y-%m-%d %H:%M:%S')}  {s['direction']:5}  "
              f"@{s['price_gc']:7.2f}  →15min @{fp:7.2f}  move={m:+6.2f}  "
              f"score={s['score']:+d} bias={s['bias']:7s} reason={s['reason'][:50]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Phase 2 sanity rerun PROPER — search bear_ext-only multi-box bearish cycle
with subsequent price drop confirmation.

Per ML-DS comment 1214603041779694.

Criteria:
- M30 box bear_ext=True only (NOT bull_ext) — clean bearish confirmation
- Box >=4 bars confirmed
- Multi-box bearish cycle (>=2 consecutive boxes bearish/unknown)
- Subsequent market move down >=15 pts within 1-2h
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

M30_PARQUET = Path(r"C:\data\processed\gc_m30_boxes.parquet")
OHLCV_PARQUET = Path(r"C:\data\processed\gc_ohlcv_l2_joined.parquet")
START = pd.Timestamp("2026-04-01", tz="UTC")
END = pd.Timestamp("2026-05-07 23:59:59", tz="UTC")

MIN_BARS = 4  # spec criteria
MIN_DROP_PTS = 15.0
DROP_HORIZON_HOURS = 2


def classify_box_dict(d: dict) -> str:
    bh, bl, lt, lb = d.get("box_high"), d.get("box_low"), d.get("liq_top"), d.get("liq_bot")
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (bh, bl, lt, lb)):
        return "unknown"
    bull_ext = lt > bh
    bear_ext = lb < bl
    if bull_ext and not bear_ext:
        return "bullish"
    if bear_ext and not bull_ext:
        return "bearish"
    return "unknown"


def main():
    print("Loading parquets...", flush=True)
    boxes = pd.read_parquet(M30_PARQUET)
    boxes.index = pd.to_datetime(boxes.index, utc=True)
    boxes = boxes[(boxes.index >= START) & (boxes.index <= END)]
    ohlcv = pd.read_parquet(OHLCV_PARQUET, columns=["close"])
    ohlcv.index = pd.to_datetime(ohlcv.index, utc=True)
    ohlcv = ohlcv[(ohlcv.index >= START - pd.Timedelta(hours=4)) & (ohlcv.index <= END + pd.Timedelta(hours=4))]
    ohlcv_ts = ohlcv.index.values
    ohlcv_v = ohlcv["close"].values

    def price_at(ts):
        ts_v = pd.Timestamp(ts).asm8
        idx = int(np.searchsorted(ohlcv_ts, ts_v, side="right")) - 1
        if idx < 0:
            return None
        return float(ohlcv_v[idx])

    print(f"  m30 rows: {len(boxes)}", flush=True)

    print("Building per-box summary (bear_ext only >= {} bars confirmed)...".format(MIN_BARS), flush=True)
    box_ids = boxes.loc[boxes["m30_box_confirmed"] == True, "m30_box_id"].dropna().unique()
    candidates = []
    for bid in box_ids:
        sub = boxes[boxes["m30_box_id"] == bid]
        sub_conf = sub[sub["m30_box_confirmed"] == True]
        if sub_conf.empty:
            continue
        last_row = sub.iloc[-1]
        d = {
            "box_high": float(last_row.get("m30_box_high", float("nan"))),
            "box_low":  float(last_row.get("m30_box_low",  float("nan"))),
            "liq_top":  float(last_row.get("m30_liq_top",  float("nan"))),
            "liq_bot":  float(last_row.get("m30_liq_bot",  float("nan"))),
        }
        cls = classify_box_dict(d)
        if cls != "bearish":
            continue
        bars = len(sub)
        if bars < MIN_BARS:
            continue
        first_conf_ts = sub_conf.index[0]
        last_ts = sub.index[-1]
        candidates.append({
            "box_id": int(bid),
            "first_conf_ts": first_conf_ts,
            "last_ts": last_ts,
            "bars": bars,
            "box_high": d["box_high"],
            "box_low": d["box_low"],
            "liq_top": d["liq_top"],
            "liq_bot": d["liq_bot"],
        })

    print(f"  {len(candidates)} bear_ext-only confirmed boxes >={MIN_BARS} bars in Apr-May 2026", flush=True)

    print(f"Filtering: subsequent drop >={MIN_DROP_PTS} pts within {DROP_HORIZON_HOURS}h after first_conf_ts...", flush=True)
    proper = []
    for c in candidates:
        ts0 = c["first_conf_ts"]
        p0 = price_at(ts0)
        if p0 is None:
            continue
        ts_end = ts0 + pd.Timedelta(hours=DROP_HORIZON_HOURS)
        # min price within window
        idx0 = int(np.searchsorted(ohlcv_ts, pd.Timestamp(ts0).asm8, side="left"))
        idx1 = int(np.searchsorted(ohlcv_ts, pd.Timestamp(ts_end).asm8, side="right"))
        if idx1 <= idx0:
            continue
        min_p = float(np.min(ohlcv_v[idx0:idx1]))
        drop = p0 - min_p
        c["price_at_first_conf"] = p0
        c["min_price_within_2h"] = min_p
        c["drop_pts"] = drop
        if drop >= MIN_DROP_PTS:
            proper.append(c)

    print(f"  {len(proper)} candidates with subsequent drop >={MIN_DROP_PTS}pts", flush=True)

    print("Filtering: multi-box bearish cycle (>=2 consecutive bearish/unknown)...", flush=True)
    # Compute box-level classifications by ts ordering (need ALL confirmed boxes, not only bearish)
    all_box_ids = boxes.loc[boxes["m30_box_confirmed"] == True, "m30_box_id"].dropna().unique()
    box_summary = []
    for bid in all_box_ids:
        sub = boxes[boxes["m30_box_id"] == bid]
        sub_conf = sub[sub["m30_box_confirmed"] == True]
        if sub_conf.empty:
            continue
        last_row = sub.iloc[-1]
        d = {
            "box_high": float(last_row.get("m30_box_high", float("nan"))),
            "box_low":  float(last_row.get("m30_box_low",  float("nan"))),
            "liq_top":  float(last_row.get("m30_liq_top",  float("nan"))),
            "liq_bot":  float(last_row.get("m30_liq_bot",  float("nan"))),
        }
        box_summary.append({
            "box_id": int(bid),
            "first_conf_ts": sub_conf.index[0],
            "cls": classify_box_dict(d),
            "bars": len(sub),
        })
    box_summary.sort(key=lambda x: x["first_conf_ts"])

    # Build cycle index: for each candidate, look at the previous box class
    cls_by_id = {b["box_id"]: b["cls"] for b in box_summary}
    ts_order = [b["box_id"] for b in box_summary]
    pos = {bid: i for i, bid in enumerate(ts_order)}

    final = []
    for c in proper:
        i = pos.get(c["box_id"])
        if i is None or i == 0:
            continue
        prev_bid = ts_order[i - 1]
        prev_cls = cls_by_id.get(prev_bid)
        c["prev_box_id"] = prev_bid
        c["prev_box_cls"] = prev_cls
        if prev_cls in ("bearish", "unknown"):
            final.append(c)

    print(f"  {len(final)} proper episodes (bear cycle + drop confirmation)", flush=True)
    if not final:
        print("  NO PROPER EPISODE FOUND - falls to Option 1 fallback", flush=True)
        return

    # Print top 10 by drop magnitude
    final.sort(key=lambda c: -c["drop_pts"])
    print("\nTop candidates (sorted by subsequent drop magnitude):", flush=True)
    print("box_id | first_conf_ts            | bars | prev_cls  | p0      | min_2h  | drop_pts", flush=True)
    for c in final[:10]:
        print(
            f"{c['box_id']:>5} | {str(c['first_conf_ts'])[:19]} | {c['bars']:>4} | "
            f"{c['prev_box_cls']:<8} | {c['price_at_first_conf']:>7.2f} | "
            f"{c['min_price_within_2h']:>7.2f} | {c['drop_pts']:>7.2f}",
            flush=True,
        )

    # Save full list
    out_path = Path(r"C:\FluxQuantumAI\_audit\calibrations\m30_bias_voting_proper_bearish_candidates.json")
    import json
    payload = []
    for c in final:
        payload.append({
            "box_id": c["box_id"],
            "first_conf_ts": str(c["first_conf_ts"]),
            "last_ts": str(c["last_ts"]),
            "bars": c["bars"],
            "prev_box_id": c["prev_box_id"],
            "prev_box_cls": c["prev_box_cls"],
            "box_high": c["box_high"],
            "box_low": c["box_low"],
            "liq_top": c["liq_top"],
            "liq_bot": c["liq_bot"],
            "price_at_first_conf": c["price_at_first_conf"],
            "min_price_within_2h": c["min_price_within_2h"],
            "drop_pts": c["drop_pts"],
        })
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {len(payload)} candidates to {out_path}", flush=True)


if __name__ == "__main__":
    main()

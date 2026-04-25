"""Step 4 — M30 and D1 parquet cross-check vs tape-reaggregated. READ-ONLY."""
from __future__ import annotations
import sys
import csv
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _reconcile_helpers import (
    build_raw_1min_from_trades, load_parquet_m30_day, load_parquet_d1,
    M30_PARQUET, D1_PARQUET, TRADES_DIR, DATABENTO_DIR,
)

OUT_M30 = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\daily_reconciliation_m30.csv")
OUT_D1  = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\daily_reconciliation_d1.csv")


def list_available_dates() -> list[str]:
    # Restrict to L2-era where reconciliation has signal (post-handover from databento)
    dates = set()
    for f in TRADES_DIR.glob("trades_*.csv.gz"):
        dates.add(f.stem.replace("trades_", "").replace(".csv", ""))
    return sorted(dates)


def m30_for_date(date: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    raw = build_raw_1min_from_trades(date)
    if raw is None or raw.empty:
        return None
    raw_m30 = pd.DataFrame({
        "open": raw["open"].resample("30min").first(),
        "high": raw["high"].resample("30min").max(),
        "low": raw["low"].resample("30min").min(),
        "close": raw["close"].resample("30min").last(),
        "volume": raw["volume"].resample("30min").sum(),
    }).dropna()
    pq_m30 = load_parquet_m30_day(date)
    return raw_m30, pq_m30


def main():
    dates = list_available_dates()
    print(f"Reconciling M30+D1 across {len(dates)} dates")

    m30_rows = []
    d1_rows = []

    for i, d in enumerate(dates):
        res = m30_for_date(d)
        if res is None:
            m30_rows.append({"date": d, "status": "NO_DATA"})
            d1_rows.append({"date": d, "status": "NO_DATA"})
            continue
        raw_m30, pq_m30 = res

        # M30 reconcile
        if pq_m30.empty:
            m30_rows.append({"date": d, "status": "PQ_EMPTY_M30"})
        else:
            merged = pq_m30.join(raw_m30, how="outer", rsuffix="_raw")
            both = merged.dropna(subset=["high", "high_raw"])
            if both.empty:
                m30_rows.append({"date": d, "status": "NO_OVERLAP_M30"})
            else:
                dh = (both["high_raw"] - both["high"]).abs()
                dl = (both["low_raw"] - both["low"]).abs()
                day_dH = float(raw_m30["high"].max() - pq_m30["high"].max())
                day_dL = float(raw_m30["low"].min() - pq_m30["low"].min())
                m30_rows.append({
                    "date": d, "status": "OK",
                    "m30_bars": len(both),
                    "raw_H_m30": float(raw_m30["high"].max()),
                    "pq_H_m30": float(pq_m30["high"].max()),
                    "day_dH_m30": day_dH,
                    "day_dL_m30": day_dL,
                    "max_dh_m30": float(dh.max()),
                    "max_dl_m30": float(dl.max()),
                    "mean_dh_m30": float(dh.mean()),
                    "bars_dh_gt_5": int((dh > 5).sum()),
                })

        # D1 reconcile — aggregate raw ET-session-close (CME 17:00 ET = 22:00 UTC pre-DST,
        # 21:00 UTC post-DST). We use the parquet D1 convention: bars indexed at 22:00 UTC
        # on prev calendar day == session closing on THIS date.
        raw = build_raw_1min_from_trades(d)
        d1_bar = load_parquet_d1(d)
        if d1_bar is None:
            d1_rows.append({"date": d, "status": "NO_D1_BAR"})
        else:
            raw_H = float(raw["high"].max())
            raw_L = float(raw["low"].min())
            d1_H = float(d1_bar["high"])
            d1_L = float(d1_bar["low"])
            d1_rows.append({
                "date": d, "status": "OK",
                "d1_ts": str(d1_bar.name),
                "raw_H_d1": raw_H,
                "pq_H_d1": d1_H,
                "raw_L_d1": raw_L,
                "pq_L_d1": d1_L,
                "dH_d1": raw_H - d1_H,
                "dL_d1": raw_L - d1_L,
                "abs_dH_d1": abs(raw_H - d1_H),
                "abs_dL_d1": abs(raw_L - d1_L),
            })

        if (i + 1) % 20 == 0 or (i + 1) == len(dates):
            print(f"  [{i+1}/{len(dates)}] done {d}")

    # Write CSVs
    m30_fields = sorted({k for r in m30_rows for k in r.keys()})
    with open(OUT_M30, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=m30_fields, extrasaction="ignore")
        w.writeheader()
        for r in m30_rows:
            w.writerow(r)
    print(f"Wrote {OUT_M30}")

    d1_fields = sorted({k for r in d1_rows for k in r.keys()})
    with open(OUT_D1, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=d1_fields, extrasaction="ignore")
        w.writeheader()
        for r in d1_rows:
            w.writerow(r)
    print(f"Wrote {OUT_D1}")

    # Quick summary
    m30_major = [r for r in m30_rows if r.get("status") == "OK" and r.get("max_dh_m30", 0) > 5]
    m30_corrupt = [r for r in m30_rows if r.get("status") == "OK" and r.get("max_dh_m30", 0) > 20]
    print(f"\nM30 summary: {len(m30_rows)} dates total; {len(m30_major)} with max_dh_m30 > 5pt; "
          f"{len(m30_corrupt)} with max_dh_m30 > 20pt")
    d1_major = [r for r in d1_rows if r.get("status") == "OK" and r.get("abs_dH_d1", 0) > 5]
    d1_corrupt = [r for r in d1_rows if r.get("status") == "OK" and r.get("abs_dH_d1", 0) > 20]
    print(f"D1 summary: {len(d1_rows)} dates total; {len(d1_major)} with |dH_d1| > 5pt; "
          f"{len(d1_corrupt)} with |dH_d1| > 20pt")

    # Show corrupt D1 dates with detail
    print(f"\nD1 dates with |dH_d1| > 20pt:")
    for r in d1_corrupt:
        print(f"  {r['date']}  d1_ts={r['d1_ts']}  raw_H={r['raw_H_d1']:.2f}  "
              f"pq_H={r['pq_H_d1']:.2f}  dH={r['dH_d1']:+.2f}")


if __name__ == "__main__":
    main()

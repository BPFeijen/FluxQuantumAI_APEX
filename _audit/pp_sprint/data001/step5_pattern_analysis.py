"""Step 5 — pattern analysis: temporal clustering, rollover, direction bias."""
from __future__ import annotations
import csv
from pathlib import Path
import pandas as pd
import numpy as np

RECON = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\daily_reconciliation_1min.csv")
OUT = Path(r"C:\FluxQuantumAI\_audit\pp_sprint\data001\pattern_analysis.md")

# CME GC contract expirations (approximate 3rd-last business day of contract month)
# For 2023-01 → 2026-04 window. GC is active: Feb, Apr, Jun, Aug, Oct, Dec.
# Front-month transitions (approximate date of ~max-volume crossover):
# Use mid-month: e.g. GCG (Feb) -> GCJ (Apr) transition in late Jan.
GC_ROLLOVERS = [
    # (transition_date, from, to)  — approximate rollover / first-notice
    ("2023-01-27", "GCG3", "GCJ3"),
    ("2023-03-30", "GCJ3", "GCM3"),
    ("2023-05-30", "GCM3", "GCQ3"),
    ("2023-07-28", "GCQ3", "GCZ3"),
    ("2023-09-27", "GCZ3", "GCG4"),
    ("2023-11-29", "GCG4", "GCJ4"),  # Feb 2024 becomes front late Nov
    ("2024-01-30", "GCG4", "GCJ4"),
    ("2024-03-27", "GCJ4", "GCM4"),
    ("2024-05-29", "GCM4", "GCQ4"),
    ("2024-07-30", "GCQ4", "GCZ4"),
    ("2024-09-27", "GCZ4", "GCG5"),
    ("2024-11-27", "GCG5", "GCJ5"),
    ("2025-01-29", "GCG5", "GCJ5"),
    ("2025-03-27", "GCJ5", "GCM5"),
    ("2025-05-29", "GCM5", "GCQ5"),
    ("2025-07-30", "GCQ5", "GCZ5"),
    ("2025-09-26", "GCZ5", "GCG6"),
    ("2025-11-26", "GCG6", "GCJ6"),
    ("2026-01-29", "GCG6", "GCJ6"),
    ("2026-03-27", "GCJ6", "GCM6"),
]


def _days_to_nearest_rollover(d: str) -> tuple[int, str]:
    td = pd.Timestamp(d)
    min_delta = 9999
    min_roll = ""
    for roll_date, _, _ in GC_ROLLOVERS:
        rd = pd.Timestamp(roll_date)
        delta = abs((td - rd).days)
        if delta < min_delta:
            min_delta = delta
            min_roll = roll_date
    return min_delta, min_roll


def main():
    if not RECON.exists():
        print(f"ERROR: {RECON} not found")
        return
    df = pd.read_csv(RECON)
    df_ok = df[df["status"] == "OK"].copy()
    df_ok["date_ts"] = pd.to_datetime(df_ok["date"])

    print(f"Loaded {len(df)} rows, {len(df_ok)} with OK status")

    # Summary counts
    by_band = df_ok.groupby("band").size()
    print(f"\nBand counts:\n{by_band}")

    # Temporal clustering
    df_ok["year_month"] = df_ok["date_ts"].dt.to_period("M").astype(str)
    month_counts = df_ok.groupby(["year_month", "band"]).size().unstack(fill_value=0)

    # Rollover alignment
    df_ok[["days_to_rollover", "nearest_rollover"]] = pd.DataFrame(
        df_ok["date"].apply(_days_to_nearest_rollover).tolist(),
        index=df_ok.index,
    )

    # Direction bias (signed day_dH)
    dir_stats = df_ok.groupby("band")["day_dH_tape_minus_pq_pts"].agg(["count", "mean", "median", "min", "max"])

    # Corrupted/major by days-to-rollover histogram
    suspect = df_ok[df_ok["band"].isin(["MAJOR", "CORRUPT"])].copy()
    suspect_sorted = suspect.sort_values("date")

    # Source breakdown for CORRUPT
    src_stats = df_ok.groupby(["band", "has_trades", "has_databento"]).size()

    # Day of week distribution for suspects
    suspect["dow"] = suspect["date_ts"].dt.day_name()
    dow_suspect = suspect.groupby(["band", "dow"]).size().unstack(fill_value=0)

    # Databento→Quantower handover (Nov 25 / 26, 2025)
    handover_date = pd.Timestamp("2025-11-26")
    pre = df_ok[df_ok["date_ts"] < handover_date]
    post = df_ok[df_ok["date_ts"] >= handover_date]
    pre_band = pre.groupby("band").size()
    post_band = post.groupby("band").size()

    # Time-of-day of corruption for suspects: parse first_minute_corrupt
    def _hour_of(ts):
        if pd.isna(ts) or not ts:
            return None
        try:
            return pd.Timestamp(ts).hour
        except Exception:
            return None

    suspect["first_corrupt_hour"] = suspect["first_minute_corrupt"].apply(_hour_of)
    hour_dist = suspect["first_corrupt_hour"].value_counts().sort_index()

    # Write markdown report
    rows = []
    rows.append("# Step 5 — Pattern Analysis\n")
    rows.append(f"**Source:** `{RECON.name}` — {len(df_ok)} dates with OK status.\n")
    rows.append("\n## Band distribution\n")
    rows.append(by_band.to_string())
    rows.append("\n\n## Monthly bucket counts (CLEAN / MINOR / MAJOR / CORRUPT)\n")
    rows.append(month_counts.to_string())
    rows.append("\n\n## Direction bias (signed day_dH = tape_H − pq_H, by band)\n")
    rows.append(dir_stats.to_string())
    rows.append("\n\n## Databento vs L2 breakdown\n")
    rows.append(src_stats.to_string())
    rows.append("\n\n## Pre- vs post-Databento→Quantower handover (2025-11-26)\n")
    rows.append(f"Pre-handover:\n{pre_band.to_string()}")
    rows.append(f"\n\nPost-handover:\n{post_band.to_string()}")
    rows.append("\n\n## Day-of-week distribution for MAJOR+CORRUPT\n")
    rows.append(dow_suspect.to_string())
    rows.append("\n\n## First-corrupt-minute hour-of-day distribution for MAJOR+CORRUPT\n")
    rows.append(hour_dist.to_string())
    rows.append("\n\n## All MAJOR + CORRUPT dates with rollover proximity\n")
    rows.append("| date | band | day_dH | day_dL | min_>p99.9 | nearest_rollover | days_to_rollover |\n")
    rows.append("|---|---|---|---|---|---|---|\n")
    for _, r in suspect_sorted.iterrows():
        rows.append(f"| {r['date']} | {r['band']} | {r['day_dH_tape_minus_pq_pts']:+.2f} | "
                    f"{r['day_dL_tape_minus_pq_pts']:+.2f} | {int(r['minutes_dh_gt_p99_9'])} | "
                    f"{r['nearest_rollover']} | {int(r['days_to_rollover'])} |\n")

    OUT.write_text("".join(rows), encoding="utf-8")
    print(f"\nWrote {OUT}")
    print("\n=== Suspect dates ===")
    for _, r in suspect_sorted.iterrows():
        print(f"  {r['date']}  {r['band']}  day_dH={r['day_dH_tape_minus_pq_pts']:+.2f}  "
              f"min_>p99.9={int(r['minutes_dh_gt_p99_9'])}  rollover={r['nearest_rollover']} "
              f"({int(r['days_to_rollover'])}d)")


if __name__ == "__main__":
    main()

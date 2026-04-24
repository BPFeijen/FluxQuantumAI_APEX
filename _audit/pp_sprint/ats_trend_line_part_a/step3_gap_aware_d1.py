"""
STEP 3: gap-aware D1 builder — list all sessions that would be flagged
`is_complete=False` under a <80% M30-coverage rule.

Logic:
  - Expected M30 bars per GC session = 46 (23 trading hours with 1h break)
  - threshold = 0.80 * 46 = 36.8  -> floor 37 bars minimum
  - bars counted per ET-session label (17:00 ET close)

Output: list of flagged D1 dates in 2023-01-01..today window, with bar
counts and, when possible, cause annotation (matched against data gaps
report if present).
"""
from __future__ import annotations

import pandas as pd

M30_PATH = r"C:\data\processed\gc_m30_boxes.parquet"
EXPECTED_BARS = 46          # 23 trading hours * 2 M30 per hour
COVERAGE_THRESHOLD = 0.80   # <80% -> flagged

WINDOW_START = pd.Timestamp("2023-01-01")
WINDOW_END = pd.Timestamp("2026-04-24")


def build_session_counts(df: pd.DataFrame) -> pd.DataFrame:
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    et = df.index.tz_convert("America/New_York")
    session_label = (et + pd.Timedelta(hours=6)).normalize()
    keys = pd.DatetimeIndex(session_label).tz_localize(None)
    g = df.assign(__key=keys).groupby("__key", sort=True)
    agg = pd.DataFrame({
        "open":  g["open"].first(),
        "high":  g["high"].max(),
        "low":   g["low"].min(),
        "close": g["close"].last(),
        "n_m30_bars": g.size(),
    }).dropna(subset=["close"])
    agg = agg[agg.index.dayofweek < 5]
    agg["coverage"] = agg["n_m30_bars"] / EXPECTED_BARS
    agg["is_complete"] = agg["coverage"] >= COVERAGE_THRESHOLD
    return agg


def main():
    m30 = pd.read_parquet(M30_PATH, columns=["open", "high", "low", "close"])
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30_win = m30[(m30.index >= WINDOW_START.tz_localize("UTC"))
                  & (m30.index <= WINDOW_END.tz_localize("UTC"))]
    agg = build_session_counts(m30_win)

    print(f"Total D1 sessions {WINDOW_START.date()}..{WINDOW_END.date()}: {len(agg)}")
    print(f"  complete (>={int(COVERAGE_THRESHOLD*100)}% coverage): "
          f"{int(agg.is_complete.sum())}")
    print(f"  flagged incomplete: {int((~agg.is_complete).sum())}")
    print(f"  total M30 bars: {int(agg.n_m30_bars.sum())}  "
          f"(expected if all clean: {len(agg) * EXPECTED_BARS})")
    print()

    flagged = agg[~agg.is_complete].copy()
    flagged["coverage_pct"] = (flagged.coverage * 100).round(1)

    # Annotate with year for grouping
    print(f"All D1 dates flagged incomplete ({len(flagged)}):")
    print(f"  {'date':<12} {'bars':>5} {'cov%':>6}  H       L       C")
    for ts, row in flagged.iterrows():
        print(f"  {str(ts.date()):<12} {int(row.n_m30_bars):>5} {row.coverage_pct:>6.1f}  "
              f"{row.high:.2f}  {row.low:.2f}  {row.close:.2f}")

    # Counts per year
    print()
    print("Flagged dates per year:")
    for y, cnt in flagged.index.year.value_counts().sort_index().items():
        total_y = len(agg[agg.index.year == y])
        print(f"  {y}: {cnt} / {total_y} ({cnt/total_y*100:.1f}%)")

    # Impact on 5 disputed dates
    print("\nImpact check on 5 disputed dates:")
    for d in ["2026-01-05", "2026-02-13", "2026-02-23", "2026-03-04", "2026-03-25"]:
        dt = pd.Timestamp(d)
        if dt in agg.index:
            r = agg.loc[dt]
            status = "COMPLETE" if r.is_complete else "FLAGGED incomplete"
            print(f"  {d}: n_bars={int(r.n_m30_bars)} cov={r.coverage*100:.1f}%  -> {status}")


if __name__ == "__main__":
    main()

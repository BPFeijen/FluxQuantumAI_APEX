"""
STEP 2: propose ET-session-anchored D1 resample and test on 5 disputed dates.

Current code (`_audit/pp_sprint/ats_trend_line_part_a/backtest_3y.py:47-56`):
  def resample_ohlc(df, rule, weekday_only=False):
      agg = pd.DataFrame({
          "open":  df["open"].resample(rule).first(),
          "high":  df["high"].resample(rule).max(),
          "low":   df["low"].resample(rule).min(),
          "close": df["close"].resample(rule).last(),
      }).dropna()
      if weekday_only:
          agg = agg[agg.index.dayofweek < 5]
      return agg

Proposed fix: add a D1-specific ET-session path (DST-aware).

Formula: for each UTC bar, convert to America/New_York, add 6h and floor to
date. Any bar in the GC/COMEX session closing at 17:00 ET on date D gets
label D. Handles EST/EDT transitions automatically via pytz.
"""
from __future__ import annotations

import pandas as pd

M30_PATH = r"C:\data\processed\gc_m30_boxes.parquet"

DISPUTED = {
    "2026-01-05": 4467.6,
    "2026-02-13": 5069.1,
    "2026-02-23": 5257.3,
    "2026-03-04": 5394.2,
    "2026-03-25": 4601.0,
}


# ---------- CURRENT CODE PATH (as in backtest_3y.py) ----------
def resample_current(df: pd.DataFrame, weekday_only: bool = True) -> pd.DataFrame:
    agg = pd.DataFrame({
        "open":  df["open"].resample("1D").first(),
        "high":  df["high"].resample("1D").max(),
        "low":   df["low"].resample("1D").min(),
        "close": df["close"].resample("1D").last(),
    }).dropna()
    if weekday_only:
        agg = agg[agg.index.dayofweek < 5]
    return agg


# ---------- PROPOSED PATCH ----------
def resample_et_session(df: pd.DataFrame, weekday_only: bool = True) -> pd.DataFrame:
    """
    Resample UTC-indexed OHLC bars into D1 aligned to CME/COMEX GC session
    boundaries (17:00 ET close, DST-aware).

    Rationale: Quantower GC:XCEC D1 bars are indexed by the session close
    date. A bar at 18:00 ET on date D-1 (session open) belongs to session D.

    Formula: bar_et + 6h, then floor-to-date-ET gives the session label.
    """
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    et = df.index.tz_convert("America/New_York")
    session_label = (et + pd.Timedelta(hours=6)).normalize()  # tz-aware date
    # naive for groupby keys
    keys = pd.DatetimeIndex(session_label).tz_localize(None)

    g = df.assign(__key=keys).groupby("__key", sort=True)
    agg = pd.DataFrame({
        "open":  g["open"].first(),
        "high":  g["high"].max(),
        "low":   g["low"].min(),
        "close": g["close"].last(),
    }).dropna()
    agg.index.name = None
    if weekday_only:
        agg = agg[agg.index.dayofweek < 5]
    return agg


def main():
    m30 = pd.read_parquet(M30_PATH, columns=["open", "high", "low", "close"])
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")

    d1_cur = resample_current(m30, weekday_only=True)
    d1_new = resample_et_session(m30, weekday_only=True)

    print("Resample comparison on 5 disputed dates (Quantower GC:XCEC = ground truth):\n")
    print(f"  {'date':<12} {'QT_H':>9} {'CUR_H':>9} {'NEW_H':>9} {'CUR_d':>8} {'NEW_d':>8} {'NEW_match':>10}")
    for d, qt_h in DISPUTED.items():
        dt = pd.Timestamp(d)
        cur_row = d1_cur.loc[d1_cur.index == dt] if dt in d1_cur.index else None
        new_row = d1_new.loc[d1_new.index == dt] if dt in d1_new.index else None
        cur_h = float(cur_row["high"].iloc[0]) if cur_row is not None and len(cur_row) else float("nan")
        new_h = float(new_row["high"].iloc[0]) if new_row is not None and len(new_row) else float("nan")
        cur_d = cur_h - qt_h
        new_d = new_h - qt_h
        match = "CLOSE" if abs(new_d) < 2.5 else ("NEAR" if abs(new_d) < 15 else "MISMATCH")
        print(f"  {d:<12} {qt_h:>9.2f} {cur_h:>9.2f} {new_h:>9.2f} {cur_d:>+8.2f} {new_d:>+8.2f} {match:>10}")

    print()
    print("Full OHLC under NEW (ET session) for disputed dates:")
    for d in DISPUTED:
        dt = pd.Timestamp(d)
        if dt in d1_new.index:
            r = d1_new.loc[dt]
            print(f"  {d}  O={r.open:.2f} H={r.high:.2f} L={r.low:.2f} C={r.close:.2f}")
        else:
            print(f"  {d}  NOT IN NEW INDEX")

    # Also: report number of D1 bars in each index for sanity
    print(f"\nTotal D1 bars: current={len(d1_cur)}  new={len(d1_new)}")
    print(f"Date range current: {d1_cur.index.min()} .. {d1_cur.index.max()}")
    print(f"Date range new:     {d1_new.index.min()} .. {d1_new.index.max()}")


if __name__ == "__main__":
    main()

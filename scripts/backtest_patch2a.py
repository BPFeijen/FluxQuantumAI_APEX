"""
PATCH 2A Backtest — M5 resolution, full audit
==============================================
Period: Jul 2025 - today
For every M5 bar where price is above box (uptrend) or below box (downtrend):
  Evaluate PATCH 2A CONTINUATION trigger with ALL guards.
  Log every trigger with full audit trail.
  Track outcome (MFE/MAE/P&L at +1h, +2h, +4h, EOD).

Data integrity: flag any period where M5 bar_delta is zero/missing.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json

M30_PATH = Path(r"C:\data\processed\gc_m30_boxes.parquet")
M5_PATH  = Path(r"C:\data\processed\gc_m5_boxes.parquet")
OUT_DIR  = Path(r"C:\FluxQuantumAI\data\calibration")

START = "2025-07-01"

# Thresholds (from settings.json / event_processor)
DISP_ATR_MULT  = 0.8
DISP_MIN_DELTA = 80
DISP_CLOSE_PCT = 0.7
OVEREXT_MULT   = 1.5
COOLDOWN_BARS  = 12  # 12 x 5min = 60min between signals


def load():
    m30 = pd.read_parquet(M30_PATH)
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30 = m30[m30.index >= START]

    m5 = pd.read_parquet(M5_PATH)
    if m5.index.tz is None:
        m5.index = m5.index.tz_localize("UTC")
    m5 = m5[m5.index >= START].copy()
    m5["range"] = m5["high"] - m5["low"]

    # Propagate M30 to M5
    for col in ["atr14", "m30_box_high", "m30_box_low", "m30_liq_top",
                 "m30_liq_bot", "m30_fmv", "m30_box_confirmed", "m30_box_id"]:
        target = "atr_m30" if col == "atr14" else col
        m5[target] = m30[col].reindex(m5.index, method="ffill")

    # Daily trend from M30 FMV (live derivation)
    daily_fmv = m30["m30_fmv"].resample("1D").last().dropna()
    daily_trend = pd.Series("unknown", index=daily_fmv.index)
    for i in range(2, len(daily_fmv)):
        f = daily_fmv.iloc[i-2:i+1].values
        if f[2] > f[1] > f[0]:
            daily_trend.iloc[i] = "long"
        elif f[2] < f[1] < f[0]:
            daily_trend.iloc[i] = "short"
        elif f[1] > f[0]:
            daily_trend.iloc[i] = "long"
        elif f[1] < f[0]:
            daily_trend.iloc[i] = "short"
    m5["daily_trend"] = daily_trend.reindex(m5.index, method="ffill")

    return m30, m5


def data_integrity(m5):
    """Check bar_delta integrity."""
    total = len(m5)
    nz = (m5["bar_delta"] != 0).sum()
    zero = (m5["bar_delta"] == 0).sum()

    # Per-month breakdown
    monthly = m5.resample("M").agg(
        total=("bar_delta", "count"),
        nonzero=("bar_delta", lambda x: (x != 0).sum()),
    )
    monthly["pct"] = (monthly["nonzero"] / monthly["total"] * 100).round(1)

    print("DATA INTEGRITY: M5 bar_delta")
    print("=" * 60)
    print(f"  Total bars: {total}")
    print(f"  Non-zero:   {nz} ({nz/total*100:.1f}%)")
    print(f"  Zero:        {zero} ({zero/total*100:.1f}%)")
    print()
    print("  Monthly breakdown:")
    for idx, row in monthly.iterrows():
        flag = " << INCOMPLETE" if row["pct"] < 50 else ""
        print(f"    {idx.strftime('%Y-%m')}: {int(row['nonzero'])}/{int(row['total'])} ({row['pct']}%){flag}")

    # Identify valid date range
    valid_dates = m5[m5["bar_delta"] != 0].index.strftime("%Y-%m-%d").unique()
    print(f"\n  Valid dates with delta: {len(valid_dates)}")
    print(f"  First: {valid_dates[0] if len(valid_dates) > 0 else 'N/A'}")
    print(f"  Last:  {valid_dates[-1] if len(valid_dates) > 0 else 'N/A'}")

    return set(valid_dates)


def check_displacement(m5, i, direction, atr):
    """Check displacement in last 3 M5 bars before index i."""
    min_range = atr * DISP_ATR_MULT
    start = max(0, i - 3)
    recent = m5.iloc[start:i]

    for j in range(len(recent) - 1, -1, -1):
        b = recent.iloc[j]
        r = float(b["high"]) - float(b["low"])
        d = float(b.get("bar_delta", 0))
        c = float(b["close"])
        o = float(b["open"])
        lo = float(b["low"])
        hi = float(b["high"])

        if r < min_range:
            continue

        if direction == "LONG":
            if c <= o:
                continue
            if abs(d) > 0 and d < DISP_MIN_DELTA:
                continue
            cpct = (c - lo) / r if r > 0 else 0
            if cpct < DISP_CLOSE_PCT:
                continue
            return True, r, d, cpct, str(recent.index[j])
        else:
            if c >= o:
                continue
            if abs(d) > 0 and d > -DISP_MIN_DELTA:
                continue
            cpct = (hi - c) / r if r > 0 else 0
            if cpct < DISP_CLOSE_PCT:
                continue
            return True, r, d, cpct, str(recent.index[j])

    return False, 0, 0, 0, ""


def compute_outcome(m5, entry_idx, entry_price, direction):
    """Compute MFE, MAE, P&L at +1h, +2h, +4h, EOD."""
    future = m5.iloc[entry_idx + 1:]
    if future.empty:
        return {}

    results = {}

    # Horizons: 12 bars=1h, 24=2h, 48=4h
    for label, bars in [("1h", 12), ("2h", 24), ("4h", 48)]:
        window = future.head(bars)
        if window.empty:
            continue
        if direction == "LONG":
            mfe = (window["high"] - entry_price).max()
            mae = (entry_price - window["low"]).max()
            pnl = float(window.iloc[-1]["close"]) - entry_price
        else:
            mfe = (entry_price - window["low"]).max()
            mae = (window["high"] - entry_price).max()
            pnl = entry_price - float(window.iloc[-1]["close"])
        results[f"mfe_{label}"] = round(mfe, 1)
        results[f"mae_{label}"] = round(mae, 1)
        results[f"pnl_{label}"] = round(pnl, 1)

    # EOD
    entry_date = m5.index[entry_idx].strftime("%Y-%m-%d")
    eod = future[future.index.strftime("%Y-%m-%d") == entry_date]
    if not eod.empty:
        if direction == "LONG":
            results["pnl_eod"] = round(float(eod.iloc[-1]["close"]) - entry_price, 1)
        else:
            results["pnl_eod"] = round(entry_price - float(eod.iloc[-1]["close"]), 1)

    return results


def run_backtest(m5, valid_dates):
    """Scan all M5 bars, trigger PATCH 2A where conditions met."""
    signals = []
    last_signal_bar = -COOLDOWN_BARS

    for i in range(3, len(m5)):
        row = m5.iloc[i]
        c = float(row["close"])
        bh = row.get("m30_box_high")
        bl = row.get("m30_box_low")
        lt = row.get("m30_liq_top")
        lb = row.get("m30_liq_bot")
        fmv = row.get("m30_fmv")
        atr = float(row.get("atr_m30", 20.0)) if pd.notna(row.get("atr_m30")) else 20.0
        dt = row.get("daily_trend", "unknown")
        delta_val = float(row.get("bar_delta", 0))
        ts = m5.index[i]
        date_str = ts.strftime("%Y-%m-%d")

        if pd.isna(bh) or pd.isna(bl) or atr <= 0:
            continue
        bh, bl = float(bh), float(bl)

        # Data validity check
        delta_valid = date_str in valid_dates

        # Direction from daily_trend
        if dt == "long" and c > bh:
            direction = "LONG"
            dist_from_box = c - bh
        elif dt == "short" and c < bl:
            direction = "SHORT"
            dist_from_box = bl - c
        else:
            continue

        # Cooldown
        if i - last_signal_bar < COOLDOWN_BARS:
            continue

        # Displacement check
        disp_ok, disp_range, disp_delta, disp_cpct, disp_bar = check_displacement(
            m5, i, direction, atr)

        # Exhaustion (overextension)
        exhausted = False
        exh_reason = ""
        if direction == "LONG" and pd.notna(lt):
            dist = abs(c - float(lt))
            if dist > atr * OVEREXT_MULT:
                exhausted = True
                exh_reason = f"overext {dist:.1f}>{atr*OVEREXT_MULT:.1f}"
        elif direction == "SHORT" and pd.notna(lb):
            dist = abs(float(lb) - c)
            if dist > atr * OVEREXT_MULT:
                exhausted = True
                exh_reason = f"overext {dist:.1f}>{atr*OVEREXT_MULT:.1f}"

        # Decision
        if not disp_ok:
            action = "SKIP"
            reason = "no displacement"
        elif not delta_valid:
            action = "SKIP"
            reason = "delta data invalid/zero"
        elif exhausted:
            action = "BLOCK"
            reason = exh_reason
        else:
            action = "GO"
            reason = f"displacement rng={disp_range:.1f} dlt={disp_delta:+.0f}"

        # Only log meaningful events (displacement found or GO)
        if not disp_ok:
            continue  # skip the noise of no-displacement bars

        last_signal_bar = i

        # Outcome
        outcome = {}
        if action == "GO":
            outcome = compute_outcome(m5, i, c, direction)

        sig = {
            "timestamp": str(ts),
            "date": date_str,
            "close": round(c, 1),
            "direction": direction,
            "daily_trend": dt,
            "box_high": round(bh, 1),
            "box_low": round(bl, 1),
            "dist_from_box": round(dist_from_box, 1),
            "atr_m30": round(atr, 1),
            "displacement": disp_ok,
            "disp_range": round(disp_range, 1),
            "disp_delta": round(disp_delta, 0),
            "disp_cpct": round(disp_cpct, 2),
            "disp_bar": disp_bar[:16],
            "exhausted": exhausted,
            "exh_reason": exh_reason,
            "delta_valid": delta_valid,
            "action": action,
            "reason": reason,
        }
        sig.update(outcome)
        signals.append(sig)

    return pd.DataFrame(signals)


def analyze(df):
    """Full analysis of backtest results."""
    total = len(df)
    go = df[df["action"] == "GO"]
    block = df[df["action"] == "BLOCK"]
    skip = df[df["action"] == "SKIP"]

    print(f"\n{'='*80}")
    print("PATCH 2A BACKTEST RESULTS")
    print(f"{'='*80}")
    print(f"Period: {df['date'].min()} to {df['date'].max()}")
    print(f"Total triggers (displacement found): {total}")
    print(f"  GO:    {len(go)}")
    print(f"  BLOCK: {len(block)} (exhaustion)")
    print(f"  SKIP:  {len(skip)} (delta invalid)")
    print(f"  Dates: {df['date'].nunique()} unique days")

    if go.empty:
        print("\nNo GO signals to analyze.")
        return

    print(f"\n--- GO SIGNALS PERFORMANCE ---")
    for h in ["1h", "2h", "4h", "eod"]:
        col = f"pnl_{h}"
        if col in go.columns:
            valid = go[col].dropna()
            if not valid.empty:
                win = (valid > 0).sum()
                print(f"  P&L @{h:>3}: mean={valid.mean():+.1f}  med={valid.median():+.1f}  "
                      f"win={win}/{len(valid)} ({win/len(valid)*100:.0f}%)  "
                      f"min={valid.min():+.1f}  max={valid.max():+.1f}")

    for h in ["1h", "2h", "4h"]:
        mfe_col = f"mfe_{h}"
        mae_col = f"mae_{h}"
        if mfe_col in go.columns and mae_col in go.columns:
            mfe = go[mfe_col].dropna()
            mae = go[mae_col].dropna()
            if not mfe.empty:
                print(f"  MFE @{h:>3}: mean={mfe.mean():.1f}  MAE: mean={mae.mean():.1f}")

    print(f"\n--- DIRECTION ---")
    for d in go["direction"].unique():
        sub = go[go["direction"] == d]
        print(f"  {d}: {len(sub)} signals")

    print(f"\n--- BY PHASE CONTEXT ---")
    # dist_from_box distribution
    print(f"  Distance from box: mean={go['dist_from_box'].mean():.1f}  "
          f"med={go['dist_from_box'].median():.1f}  max={go['dist_from_box'].max():.1f}")

    print(f"\n--- DISPLACEMENT QUALITY ---")
    print(f"  Range: mean={go['disp_range'].mean():.1f}  med={go['disp_range'].median():.1f}")
    print(f"  Delta: mean={go['disp_delta'].mean():+.0f}  med={go['disp_delta'].median():+.0f}")

    # Audit: individual GO trades
    print(f"\n{'='*80}")
    print(f"AUDIT: {len(go)} GO trades")
    print(f"{'='*80}")
    cols = ["timestamp", "close", "direction", "box_high", "box_low", "dist_from_box",
            "disp_range", "disp_delta", "atr_m30", "pnl_1h", "pnl_2h", "pnl_eod"]
    for _, r in go.iterrows():
        pnl_1h = r.get("pnl_1h", "-")
        pnl_2h = r.get("pnl_2h", "-")
        pnl_eod = r.get("pnl_eod", "-")
        pnl_1h_s = f"{pnl_1h:+.1f}" if isinstance(pnl_1h, (int, float)) and not pd.isna(pnl_1h) else "-"
        pnl_2h_s = f"{pnl_2h:+.1f}" if isinstance(pnl_2h, (int, float)) and not pd.isna(pnl_2h) else "-"
        pnl_eod_s = f"{pnl_eod:+.1f}" if isinstance(pnl_eod, (int, float)) and not pd.isna(pnl_eod) else "-"
        print(f"  {r['timestamp'][:16]}  {r['direction']}  C={r['close']}  "
              f"box=[{r['box_low']}-{r['box_high']}]  +{r['dist_from_box']}pts  "
              f"disp: rng={r['disp_range']} dlt={r['disp_delta']:+.0f}  "
              f"P&L: 1h={pnl_1h_s} 2h={pnl_2h_s} eod={pnl_eod_s}")

    # BLOCK audit
    if not block.empty:
        print(f"\nBLOCK trades ({len(block)}):")
        for _, r in block.iterrows():
            print(f"  {r['timestamp'][:16]}  {r['direction']}  C={r['close']}  {r['exh_reason']}")


def baseline_comparison(m5, valid_dates):
    """Compare: what does the current system (without PATCH 2A) produce?"""
    # Baseline: only triggers at liq_top/liq_bot (within 8pts)
    # PATCH 2A: triggers when above box with displacement
    # The baseline for "above box" scenarios = ZERO trades (monitoring)
    print(f"\n{'='*80}")
    print("BASELINE vs PATCH 2A")
    print(f"{'='*80}")
    print("  Baseline (current system):")
    print("    When price is above box and away from liq levels: 0 trades (monitoring)")
    print("    The system sits idle watching the trend run.")
    print()
    print("  PATCH 2A:")
    print("    Evaluates CONTINUATION when above box + displacement + delta + not exhausted")
    print("    Adds coverage for trend days that the baseline completely misses.")
    print()
    print("  This is NOT baseline vs patch on the same trades.")
    print("  This is: does PATCH 2A add VALUE in scenarios where baseline does NOTHING?")


def main():
    print("PATCH 2A BACKTEST - M5 Resolution")
    print(f"Period: {START} to today")
    print()

    m30, m5 = load()
    print(f"M30: {len(m30)} | M5: {len(m5)}")

    valid_dates = data_integrity(m5)

    print(f"\nRunning backtest...")
    df = run_backtest(m5, valid_dates)

    if df.empty:
        print("No signals found.")
        return

    analyze(df)
    baseline_comparison(m5, valid_dates)

    # Save
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "patch2a_backtest_audit.csv", index=False)
    df.to_parquet(OUT_DIR / "patch2a_backtest_audit.parquet")
    print(f"\nSaved to {OUT_DIR}")


if __name__ == "__main__":
    main()

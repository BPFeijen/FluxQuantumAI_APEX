"""
Sprint 8 Backtest: ATR multiplier sweep for overextension threshold.
Compares OLD (always reversal) vs NEW (dual strategy + overextension filter).
Data: Jul 2025 - Apr 2026 (M30 bars).
"""
import pandas as pd
import numpy as np

print("Loading data...")

m30 = pd.read_parquet("C:/data/processed/gc_m30_boxes.parquet")
if m30.index.tz is None:
    m30.index = m30.index.tz_localize("UTC")

feat = pd.read_parquet("C:/data/processed/gc_ats_features_v4.parquet",
                        columns=["open", "high", "low", "close", "daily_jac_dir"])
if feat.index.tz is None:
    feat.index = feat.index.tz_localize("UTC")

start = pd.Timestamp("2025-07-01", tz="UTC")
m30 = m30[m30.index >= start].copy()
feat = feat[feat.index >= start].copy()

print(f"M30 boxes Jul 2025+: {len(m30)} bars")
print(f"Features Jul 2025+: {len(feat)} bars")

# Resample features to M30
feat_m30 = feat.resample("30min").agg({
    "open": "first", "high": "max", "low": "min", "close": "last",
    "daily_jac_dir": "last"
}).dropna(subset=["close"])

merged = m30.join(feat_m30[["daily_jac_dir"]], how="inner")
merged = merged.dropna(subset=["m30_liq_top", "m30_liq_bot", "atr14", "close"])
merged["daily_trend"] = merged["daily_jac_dir"].str.lower().str.strip()
merged.loc[merged["daily_trend"] == "up", "daily_trend"] = "long"
merged.loc[merged["daily_trend"] == "down", "daily_trend"] = "short"

print(f"Merged M30 bars: {len(merged)}")
print(f"Daily trend distribution:")
print(merged["daily_trend"].value_counts())
print()

# Phase detection
merged["confirmed"] = merged["m30_box_confirmed"].fillna(False).astype(bool)
box_high = merged["m30_box_high"]
box_low = merged["m30_box_low"]
price = merged["close"]

merged["phase"] = "EXPANSION"
merged.loc[
    (box_high.notna()) & (box_low.notna()) & (price >= box_low) & (price <= box_high),
    "phase"
] = "CONTRACTION"
merged.loc[
    (merged["confirmed"]) & (merged["daily_trend"].isin(["long", "short"])),
    "phase"
] = "TREND"
merged.loc[
    (box_high.notna()) & (box_low.notna()) & (price >= box_low) & (price <= box_high),
    "phase"
] = "CONTRACTION"

print("Phase distribution:")
print(merged["phase"].value_counts())
print()

# liq_touch events
NEAR_BAND = 5.0
liq_top = merged["m30_liq_top"]
liq_bot = merged["m30_liq_bot"]

merged["near_top"] = (price - liq_top).abs() <= NEAR_BAND
merged["near_bot"] = (price - liq_bot).abs() <= NEAR_BAND

# Forward returns (5 bars = 2.5h)
merged["fwd_5"] = merged["close"].shift(-5) - merged["close"]
for i in range(1, 6):
    merged[f"fwd_high_{i}"] = merged["high"].shift(-i)
    merged[f"fwd_low_{i}"] = merged["low"].shift(-i)
merged["fwd_max_high"] = merged[[f"fwd_high_{i}" for i in range(1, 6)]].max(axis=1)
merged["fwd_min_low"] = merged[[f"fwd_low_{i}" for i in range(1, 6)]].min(axis=1)
merged = merged.dropna(subset=["fwd_5", "fwd_max_high", "fwd_min_low"])

touches_top = merged[merged["near_top"]].copy()
touches_bot = merged[merged["near_bot"]].copy()

print(f"Liq_top touches: {len(touches_top)}")
print(f"Liq_bot touches: {len(touches_bot)}")
print()

# Overextension: how far beyond level in ATR units
touches_top["overext"] = (touches_top["close"] - touches_top["m30_liq_top"]) / touches_top["atr14"]
touches_bot["overext"] = (touches_bot["m30_liq_bot"] - touches_bot["close"]) / touches_bot["atr14"]

SL = 20
TP = 20

def compute_pnl(row, direction):
    entry = row["close"]
    if direction == "SHORT":
        mfe = entry - row["fwd_min_low"]
        mae = row["fwd_max_high"] - entry
        if mae >= SL:
            return -SL
        if mfe >= TP:
            return TP
        return -(row["fwd_5"])  # fwd_5 = close[+5] - close[0], short pnl = -fwd
    else:
        mfe = row["fwd_max_high"] - entry
        mae = entry - row["fwd_min_low"]
        if mae >= SL:
            return -SL
        if mfe >= TP:
            return TP
        return row["fwd_5"]

# ATR multiplier sweep
print("=" * 110)
print("BACKTEST: ATR MULTIPLIER SWEEP (Jul 2025 - Apr 2026)")
print("=" * 110)
print()
header = f"{'Mode':<18} {'Trades':<8} {'WR%':<8} {'TotalPnL':<12} {'AvgPnL':<10} {'PF':<8} {'Skipped':<10} {'Skip_netPnL':<12}"
print(header)
print("-" * 110)

results = {}

for mult in [999, 0.0, 0.10, 0.25, 0.50, 0.75, 1.0, 1.25, 1.5, 2.0]:
    total_pnl = 0
    wins = 0
    losses = 0
    trades = 0
    skipped = 0
    skip_saved_pnl = 0
    gross_win = 0
    gross_loss = 0

    for level_type, touches in [("liq_top", touches_top), ("liq_bot", touches_bot)]:
        for idx, row in touches.iterrows():
            phase = row["phase"]
            trend = row["daily_trend"]
            overext = row["overext"]

            old_dir = "SHORT" if level_type == "liq_top" else "LONG"
            old_pnl = compute_pnl(row, old_dir)

            if mult == 999:
                # Baseline: always reversal
                direction = old_dir
            elif phase == "CONTRACTION":
                direction = old_dir
            elif phase in ("TREND", "EXPANSION") and trend in ("long", "short"):
                trend_dir = "LONG" if trend == "long" else "SHORT"

                if trend_dir == "LONG" and level_type == "liq_top":
                    if overext > mult:
                        direction = "SHORT"  # overextended reversal
                    else:
                        skipped += 1
                        skip_saved_pnl += old_pnl
                        continue
                elif trend_dir == "LONG" and level_type == "liq_bot":
                    direction = "LONG"  # buy the dip
                elif trend_dir == "SHORT" and level_type == "liq_bot":
                    if overext > mult:
                        direction = "LONG"  # overextended reversal
                    else:
                        skipped += 1
                        skip_saved_pnl += old_pnl
                        continue
                elif trend_dir == "SHORT" and level_type == "liq_top":
                    direction = "SHORT"  # sell the rally
                else:
                    direction = old_dir
            else:
                direction = old_dir

            pnl = compute_pnl(row, direction)
            total_pnl += pnl
            trades += 1
            if pnl > 0:
                wins += 1
                gross_win += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)

    wr = wins / max(trades, 1) * 100
    avg = total_pnl / max(trades, 1)
    pf = gross_win / max(gross_loss, 0.01)
    label = "BASELINE(old)" if mult == 999 else f"DUAL+ATR={mult:.2f}"

    results[mult] = {
        "trades": trades, "wr": wr, "pnl": total_pnl, "avg": avg, "pf": pf,
        "skipped": skipped, "skip_saved": skip_saved_pnl
    }

    print(f"{label:<18} {trades:<8} {wr:<8.1f} {total_pnl:<12.1f} {avg:<10.2f} {pf:<8.2f} {skipped:<10} {skip_saved_pnl:<+12.1f}")

# Find best
baseline = results[999]
best_mult = max([m for m in results if m != 999], key=lambda m: results[m]["pnl"])
best = results[best_mult]

print()
print("=" * 110)
print(f"BEST ATR MULTIPLIER: {best_mult:.2f}")
print(f"  BASELINE:  {baseline['trades']} trades  WR={baseline['wr']:.1f}%  PF={baseline['pf']:.2f}  P&L={baseline['pnl']:+.1f} pts")
print(f"  BEST:      {best['trades']} trades  WR={best['wr']:.1f}%  PF={best['pf']:.2f}  P&L={best['pnl']:+.1f} pts")
print(f"  DELTA:     {best['pnl'] - baseline['pnl']:+.1f} pts improvement")
print(f"  SKIPPED:   {best['skipped']} trades (net P&L of skipped: {best['skip_saved']:+.1f} pts)")
print()

# Also test pure SKIP (no overextension reversal) for comparison
pure_skip = results[0.0]
print(f"  PURE SKIP (ATR=0, never reversal in trend): {pure_skip['trades']} trades  P&L={pure_skip['pnl']:+.1f}  vs baseline {baseline['pnl']:+.1f}")
print(f"  CONCLUSION: {'Overextension filter helps' if best['pnl'] > pure_skip['pnl'] else 'Pure SKIP is better'}")

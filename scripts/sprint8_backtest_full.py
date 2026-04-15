"""
Sprint 8 Full Backtest: BASELINE vs DUAL STRATEGY (ATR=1.5)
Monthly breakdown: WR, PnL, PF
Data: Jul 2025 - Apr 2026
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

feat_m30 = feat.resample("30min").agg({
    "open": "first", "high": "max", "low": "min", "close": "last",
    "daily_jac_dir": "last"
}).dropna(subset=["close"])

merged = m30.join(feat_m30[["daily_jac_dir"]], how="inner")
merged = merged.dropna(subset=["m30_liq_top", "m30_liq_bot", "atr14", "close"])
merged["daily_trend"] = merged["daily_jac_dir"].str.lower().str.strip()
merged.loc[merged["daily_trend"] == "up", "daily_trend"] = "long"
merged.loc[merged["daily_trend"] == "down", "daily_trend"] = "short"

# Phase
merged["confirmed"] = merged["m30_box_confirmed"].fillna(False).astype(bool)
box_high = merged["m30_box_high"]
box_low = merged["m30_box_low"]
price = merged["close"]
merged["phase"] = "EXPANSION"
merged.loc[(box_high.notna()) & (box_low.notna()) & (price >= box_low) & (price <= box_high), "phase"] = "CONTRACTION"
merged.loc[(merged["confirmed"]) & (merged["daily_trend"].isin(["long", "short"])), "phase"] = "TREND"
merged.loc[(box_high.notna()) & (box_low.notna()) & (price >= box_low) & (price <= box_high), "phase"] = "CONTRACTION"

# Touches
NEAR_BAND = 5.0
merged["near_top"] = (price - merged["m30_liq_top"]).abs() <= NEAR_BAND
merged["near_bot"] = (price - merged["m30_liq_bot"]).abs() <= NEAR_BAND

# Forward returns
merged["fwd_5"] = merged["close"].shift(-5) - merged["close"]
for i in range(1, 6):
    merged[f"fwd_high_{i}"] = merged["high"].shift(-i)
    merged[f"fwd_low_{i}"] = merged["low"].shift(-i)
merged["fwd_max_high"] = merged[[f"fwd_high_{i}" for i in range(1, 6)]].max(axis=1)
merged["fwd_min_low"] = merged[[f"fwd_low_{i}" for i in range(1, 6)]].min(axis=1)
merged = merged.dropna(subset=["fwd_5", "fwd_max_high", "fwd_min_low"])

touches_top = merged[merged["near_top"]].copy()
touches_bot = merged[merged["near_bot"]].copy()
touches_top["overext"] = (touches_top["close"] - touches_top["m30_liq_top"]) / touches_top["atr14"]
touches_bot["overext"] = (touches_bot["m30_liq_bot"] - touches_bot["close"]) / touches_bot["atr14"]

SL = 20
TP = 20
OVEREXT_MULT = 1.5

def compute_pnl(row, direction):
    entry = row["close"]
    if direction == "SHORT":
        mfe = entry - row["fwd_min_low"]
        mae = row["fwd_max_high"] - entry
        if mae >= SL: return -SL
        if mfe >= TP: return TP
        return -(row["fwd_5"])
    else:
        mfe = row["fwd_max_high"] - entry
        mae = entry - row["fwd_min_low"]
        if mae >= SL: return -SL
        if mfe >= TP: return TP
        return row["fwd_5"]

# Run both strategies and collect per-trade results
trades_old = []
trades_new = []

for level_type, touches in [("liq_top", touches_top), ("liq_bot", touches_bot)]:
    for idx, row in touches.iterrows():
        phase = row["phase"]
        trend = row["daily_trend"]
        overext = row["overext"]
        month = idx.strftime("%Y-%m")

        old_dir = "SHORT" if level_type == "liq_top" else "LONG"
        old_pnl = compute_pnl(row, old_dir)
        trades_old.append({"ts": idx, "month": month, "dir": old_dir, "pnl": old_pnl,
                           "phase": phase, "level": level_type})

        # NEW logic
        if phase == "CONTRACTION":
            new_dir = old_dir
        elif phase in ("TREND", "EXPANSION") and trend in ("long", "short"):
            trend_dir = "LONG" if trend == "long" else "SHORT"
            if trend_dir == "LONG" and level_type == "liq_top":
                if overext > OVEREXT_MULT:
                    new_dir = "SHORT"
                else:
                    continue  # SKIP
            elif trend_dir == "LONG" and level_type == "liq_bot":
                new_dir = "LONG"
            elif trend_dir == "SHORT" and level_type == "liq_bot":
                if overext > OVEREXT_MULT:
                    new_dir = "LONG"
                else:
                    continue  # SKIP
            elif trend_dir == "SHORT" and level_type == "liq_top":
                new_dir = "SHORT"
            else:
                new_dir = old_dir
        else:
            new_dir = old_dir

        new_pnl = compute_pnl(row, new_dir)
        trades_new.append({"ts": idx, "month": month, "dir": new_dir, "pnl": new_pnl,
                           "phase": phase, "level": level_type})

df_old = pd.DataFrame(trades_old)
df_new = pd.DataFrame(trades_new)

def stats(df, label):
    wins = (df["pnl"] > 0).sum()
    losses = (df["pnl"] <= 0).sum()
    gross_win = df.loc[df["pnl"] > 0, "pnl"].sum()
    gross_loss = df.loc[df["pnl"] <= 0, "pnl"].abs().sum()
    wr = wins / max(len(df), 1) * 100
    pf = gross_win / max(gross_loss, 0.01)
    total = df["pnl"].sum()
    avg = df["pnl"].mean()
    return {"label": label, "trades": len(df), "wins": wins, "losses": losses,
            "wr": wr, "pf": pf, "total_pnl": total, "avg_pnl": avg,
            "gross_win": gross_win, "gross_loss": gross_loss}

# ===== OVERALL =====
print()
print("=" * 100)
print("OVERALL RESULTS (Jul 2025 - Apr 2026)")
print("=" * 100)
print()

old_s = stats(df_old, "BASELINE (always reversal)")
new_s = stats(df_new, "DUAL STRATEGY (ATR=1.5)")

header = f"{'Strategy':<35} {'Trades':<8} {'Wins':<7} {'Loss':<7} {'WR%':<8} {'PF':<8} {'PnL':<12} {'Avg':<10}"
print(header)
print("-" * 100)
for s in [old_s, new_s]:
    print(f"{s['label']:<35} {s['trades']:<8} {s['wins']:<7} {s['losses']:<7} "
          f"{s['wr']:<8.1f} {s['pf']:<8.2f} {s['total_pnl']:<+12.1f} {s['avg_pnl']:<+10.2f}")

print()
print(f"IMPROVEMENT: {new_s['total_pnl'] - old_s['total_pnl']:+.1f} pts  |  "
      f"PF: {old_s['pf']:.2f} -> {new_s['pf']:.2f}  |  "
      f"WR: {old_s['wr']:.1f}% -> {new_s['wr']:.1f}%")

# ===== MONTHLY BREAKDOWN =====
print()
print("=" * 100)
print("MONTHLY BREAKDOWN")
print("=" * 100)
print()
print(f"{'Month':<10} {'OLD_Tr':<8} {'OLD_WR':<8} {'OLD_PF':<8} {'OLD_PnL':<12} "
      f"{'NEW_Tr':<8} {'NEW_WR':<8} {'NEW_PF':<8} {'NEW_PnL':<12} {'Delta':<10}")
print("-" * 100)

months = sorted(set(df_old["month"].unique()) | set(df_new["month"].unique()))
for m in months:
    old_m = df_old[df_old["month"] == m]
    new_m = df_new[df_new["month"] == m]
    if old_m.empty:
        continue
    o = stats(old_m, "")
    n = stats(new_m, "") if not new_m.empty else {"trades": 0, "wr": 0, "pf": 0, "total_pnl": 0}
    delta = n["total_pnl"] - o["total_pnl"]
    print(f"{m:<10} {o['trades']:<8} {o['wr']:<8.1f} {o['pf']:<8.2f} {o['total_pnl']:<+12.1f} "
          f"{n['trades']:<8} {n['wr']:<8.1f} {n['pf']:<8.2f} {n['total_pnl']:<+12.1f} {delta:<+10.1f}")

# ===== BY PHASE =====
print()
print("=" * 100)
print("BY PHASE (NEW STRATEGY)")
print("=" * 100)
print()
for phase in ["CONTRACTION", "TREND", "EXPANSION"]:
    ph_df = df_new[df_new["phase"] == phase]
    if ph_df.empty:
        continue
    s = stats(ph_df, phase)
    print(f"  {phase:<15} {s['trades']:<8} WR={s['wr']:.1f}%  PF={s['pf']:.2f}  PnL={s['total_pnl']:+.1f}")

# ===== BY DIRECTION (NEW) =====
print()
print("=" * 100)
print("BY DIRECTION (NEW STRATEGY)")
print("=" * 100)
print()
for d in ["SHORT", "LONG"]:
    d_df = df_new[df_new["dir"] == d]
    if d_df.empty:
        continue
    s = stats(d_df, d)
    print(f"  {d:<10} {s['trades']:<8} WR={s['wr']:.1f}%  PF={s['pf']:.2f}  PnL={s['total_pnl']:+.1f}")

# ===== WORST/BEST MONTHS =====
print()
print("=" * 100)
print("BEST & WORST MONTHS (NEW STRATEGY)")
print("=" * 100)
monthly_pnl = df_new.groupby("month")["pnl"].sum().sort_values()
print()
print("Worst 3:")
for m, pnl in monthly_pnl.head(3).items():
    print(f"  {m}: {pnl:+.1f} pts")
print("Best 3:")
for m, pnl in monthly_pnl.tail(3).items():
    print(f"  {m}: {pnl:+.1f} pts")

# ===== EQUITY CURVE SUMMARY =====
print()
print("=" * 100)
print("CUMULATIVE P&L PROGRESSION")
print("=" * 100)
print()
df_old_sorted = df_old.sort_values("ts")
df_new_sorted = df_new.sort_values("ts")
df_old_sorted["cum_pnl"] = df_old_sorted["pnl"].cumsum()
df_new_sorted["cum_pnl"] = df_new_sorted["pnl"].cumsum()

# Print every 200th trade
for label, df in [("OLD", df_old_sorted), ("NEW", df_new_sorted)]:
    print(f"{label}:")
    step = max(len(df) // 10, 1)
    for i in range(0, len(df), step):
        r = df.iloc[i]
        print(f"  Trade {i+1:>4}/{len(df)}  {str(r['ts'])[:10]}  cum_pnl={r['cum_pnl']:+.1f}")
    r = df.iloc[-1]
    print(f"  Trade {len(df):>4}/{len(df)}  {str(r['ts'])[:10]}  cum_pnl={r['cum_pnl']:+.1f}")
    print()

# Max drawdown
for label, df in [("OLD", df_old_sorted), ("NEW", df_new_sorted)]:
    cum = df["cum_pnl"]
    peak = cum.cummax()
    dd = cum - peak
    max_dd = dd.min()
    print(f"{label} Max Drawdown: {max_dd:.1f} pts")

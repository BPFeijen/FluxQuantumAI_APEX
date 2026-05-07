#!/usr/bin/env python3
"""
audit_today_signals.py — Forensic audit of 2026-05-06 CONFIRMED signals.

Spec: STEP 7 per ML-DS comment 1214590407540485.
"""
import json
import pandas as pd
from collections import Counter, defaultdict

ohlc = pd.read_parquet(r"C:\data\processed\gc_ohlcv_l2_joined.parquet")
ohlc.index = pd.to_datetime(ohlc.index, utc=True)


def fwd_close(ts_str, minutes_offset):
    try:
        ts = pd.Timestamp(ts_str)
        target = ts + pd.Timedelta(minutes=minutes_offset)
        bar = ohlc.loc[target.floor("min"):target.floor("min") + pd.Timedelta(seconds=59)]
        if bar.empty:
            return None
        return float(bar.iloc[0]["close"])
    except Exception:
        return None


def now_close(ts_str):
    try:
        ts = pd.Timestamp(ts_str).floor("min")
        bar = ohlc.loc[ts:ts + pd.Timedelta(seconds=59)]
        if bar.empty:
            return None
        return float(bar.iloc[0]["close"])
    except Exception:
        return None


def reconstruct_tb(price, top, bot, atr):
    if price is None or top is None or bot is None:
        return "UNKNOWN"
    buffer = max(2.0, (atr or 0.0) * 0.3)
    if price > top + buffer:
        return "BREAKOUT_UP"
    if price < bot - buffer:
        return "BREAKOUT_DN"
    return "CONTRACTION"


def resolve(daily_trend, tb, m30b, m30c, prov):
    if daily_trend in ("long", "short"):
        return (daily_trend, "L1_daily", "HIGH")
    if tb in ("BREAKOUT_UP", "BREAKOUT_DN"):
        return ("long" if tb == "BREAKOUT_UP" else "short", "L2_tick", "HIGH")
    if m30c and m30b in ("bullish", "bearish"):
        return ("long" if m30b == "bullish" else "short", "L3_m30conf", "MEDIUM")
    if prov in ("bullish", "bearish"):
        return ("long" if prov == "bullish" else "short", "L4_prov", "LOW")
    return ("unknown", "L5_def", "NONE")


def f_asymmetric_block(resolved, level_type, orig_dir):
    if resolved == "unknown":
        return False
    s1_default = "SHORT" if level_type == "liq_top" else ("LONG" if level_type == "liq_bot" else None)
    if resolved == "long" and s1_default == "SHORT" and orig_dir == "SHORT":
        return True
    if resolved == "short" and s1_default == "LONG" and orig_dir == "LONG":
        return True
    return False


# Load today's CONFIRMED entries
results = []
with open(r"C:\FluxQuantumAI\logs\decision_log.jsonl", encoding="utf-8") as fh:
    for line in fh:
        try:
            e = json.loads(line.strip())
        except Exception:
            continue
        ts = e.get("timestamp", "")
        if not ts.startswith("2026-05-06"):
            continue
        decision = e.get("decision", {})
        if decision.get("action") not in ("CONFIRMED", "GO", "EXECUTED", "EXEC_FAILED"):
            continue
        ctx = e.get("context", {})
        trigger = e.get("trigger", {})
        orig_dir = decision.get("direction")
        price = e.get("price_gc") or e.get("price_mt5")
        c0 = now_close(ts)
        c30 = fwd_close(ts, 30)
        c4h = fwd_close(ts, 240)
        fwd30 = None if (c0 is None or c30 is None) else (c30 - c0 if orig_dir == "LONG" else c0 - c30)
        fwd4h = None if (c0 is None or c4h is None) else (c4h - c0 if orig_dir == "LONG" else c0 - c4h)
        tb = reconstruct_tb(price, ctx.get("liq_top_gc"), ctx.get("liq_bot_gc"), ctx.get("m30_atr14"))
        resolved, source, conf = resolve(
            ctx.get("daily_trend", "unknown"), tb, ctx.get("m30_bias", "unknown"),
            ctx.get("m30_bias_confirmed", False), ctx.get("provisional_m30_bias", "unknown"),
        )
        blocked = f_asymmetric_block(resolved, trigger.get("level_type"), orig_dir)
        results.append({
            "ts": ts, "hour": ts[11:13], "dir": orig_dir, "level": trigger.get("level_type"),
            "phase": ctx.get("phase"), "price": price, "fwd30": fwd30, "fwd4h": fwd4h,
            "resolved": resolved, "source": source, "conf": conf, "blocked": blocked,
        })

print(f"Today CONFIRMED entries: {len(results)}")
print()


def stats(rs, label):
    n = len(rs)
    fwd30 = [r["fwd30"] for r in rs if r["fwd30"] is not None]
    fwd4h = [r["fwd4h"] for r in rs if r["fwd4h"] is not None]
    if not fwd30:
        print(f"{label}: n={n} (no fwd data)")
        return
    tp1_30 = sum(1 for f in fwd30 if f >= 5)
    sl_30 = sum(1 for f in fwd30 if f <= -5)
    tp1_4h = sum(1 for f in fwd4h if f >= 5) if fwd4h else 0
    sl_4h = sum(1 for f in fwd4h if f <= -5) if fwd4h else 0
    print(f"{label}: n={n}")
    print(f"  fwd30_mean={sum(fwd30)/len(fwd30):+.2f}pts  fwd4h_mean="
          f"{sum(fwd4h)/len(fwd4h):+.2f}pts" if fwd4h else f"  fwd30_mean={sum(fwd30)/len(fwd30):+.2f}pts")
    print(f"  TP1 30m (>=+5pts): {tp1_30}/{len(fwd30)} ({tp1_30/len(fwd30)*100:.1f}%)")
    print(f"  SL 30m (<=-5pts): {sl_30}/{len(fwd30)} ({sl_30/len(fwd30)*100:.1f}%)")
    if fwd4h:
        print(f"  TP1 4h: {tp1_4h}/{len(fwd4h)} ({tp1_4h/len(fwd4h)*100:.1f}%)")
        print(f"  SL 4h: {sl_4h}/{len(fwd4h)} ({sl_4h/len(fwd4h)*100:.1f}%)")
    print()


longs = [r for r in results if r["dir"] == "LONG"]
shorts = [r for r in results if r["dir"] == "SHORT"]
print(f"LONG total: {len(longs)}, SHORT total: {len(shorts)}")
print()

stats(longs, "LONG outcomes")
stats(shorts, "SHORT outcomes")

# Hour breakdown
print("Per hour breakdown:")
print(f'{"hour":4s} {"total":>6s} {"LONG":>5s} {"SHORT":>5s}')
by_hour = defaultdict(list)
for r in results:
    by_hour[r["hour"]].append(r)
for h in sorted(by_hour.keys()):
    longs_h = sum(1 for r in by_hour[h] if r["dir"] == "LONG")
    shorts_h = sum(1 for r in by_hour[h] if r["dir"] == "SHORT")
    print(f"{h}h  {len(by_hour[h]):>6d} {longs_h:>5d} {shorts_h:>5d}")
print()

# 12-18 UTC window (Barbara's afternoon SHORT opportunity)
window_1218 = [r for r in results if r["hour"] in ("12", "13", "14", "15", "16", "17")]
print(f"12-18 UTC window (Barbara's SHORT opportunity):")
print(f"  Total: {len(window_1218)}")
print(f"  LONG: {sum(1 for r in window_1218 if r['dir']=='LONG')}")
print(f"  SHORT: {sum(1 for r in window_1218 if r['dir']=='SHORT')}")
if window_1218:
    stats(window_1218, "  12-18 outcomes")

# Counterfactual F-asymmetric
print("F-ASYMMETRIC counterfactual replay:")
blocked_longs = [r for r in longs if r["blocked"]]
blocked_shorts = [r for r in shorts if r["blocked"]]
print(f"  LONGs blocked by F-asym: {len(blocked_longs)}/{len(longs)} ({len(blocked_longs)/len(longs)*100 if longs else 0:.1f}%)")
print(f"  SHORTs blocked by F-asym: {len(blocked_shorts)}/{len(shorts)} ({len(blocked_shorts)/len(shorts)*100 if shorts else 0:.1f}%)")
print()


def pnl_total(rs, cap=5):
    fwd30 = [r["fwd30"] for r in rs if r["fwd30"] is not None]
    if not fwd30:
        return 0, 0
    clipped = [min(cap, max(-cap, f)) for f in fwd30]
    return sum(clipped), len(fwd30)


# Net P&L hypothetical (every signal entered with TP1=SL=5pts)
all_pnl, all_n = pnl_total(results)
print(f"HYPOTHETICAL P&L (lot=1, TP1=SL=5pts cap, every signal entered):")
print(f"  ALL signals: {all_n} entries, net = {all_pnl:+.0f}pts")
print()

# F-asymmetric: only allowed signals
allowed = [r for r in results if not r["blocked"]]
allowed_pnl, allowed_n = pnl_total(allowed)
print(f"With F-asymmetric (allowed only):")
print(f"  ALLOWED signals: {allowed_n} entries, net = {allowed_pnl:+.0f}pts")
print()
saved = all_pnl - allowed_pnl  # positive saved = bad signals avoided
print(f"  P&L SAVED by F-asymmetric: {-saved:+.0f}pts ({len(results)-len(allowed)} signals blocked)")
print(f"  (positive = losses avoided; negative = profits missed)")
print()

# Layer activation
layer_act = Counter(r["source"] for r in results)
print("Cascade Layer activation:")
for s, c in layer_act.most_common():
    print(f"  {s}: {c} ({c/len(results)*100:.1f}%)")
print()

# Phase distribution
phase_dist = Counter(r["phase"] for r in results)
print("Phase distribution:")
for p, c in phase_dist.most_common():
    print(f"  {p}: {c}")

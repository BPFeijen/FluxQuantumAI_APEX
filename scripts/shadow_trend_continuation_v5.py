"""
SHADOW MODE v5: TREND_CONTINUATION with full bar_delta backfill
===============================================================
M5 parquet now has real bar_delta for Jul 2025 - today (35K bars).
Delta check is ACTIVE and meaningful.

Collects 40 signals then stops.
"""

import pandas as pd
import numpy as np
from pathlib import Path

M30_BOXES = Path(r"C:\data\processed\gc_m30_boxes.parquet")
M5_BOXES  = Path(r"C:\data\processed\gc_m5_boxes.parquet")
FEATURES  = Path(r"C:\data\processed\gc_ats_features_v4.parquet")

START_DATE = "2025-07-01"
TREND_ACCEPTANCE_MIN_BARS = 4
TARGET_SIGNALS = 40

DISP_ATR_MULT = 0.8
DISP_MIN_DELTA = 80
DISP_CLOSE_PCT = 0.7
OVEREXT_ATR_MULT = 1.5
V1_DIST = 8.0


def load_data():
    m30 = pd.read_parquet(M30_BOXES)
    if m30.index.tz is None:
        m30.index = m30.index.tz_localize("UTC")
    m30 = m30[m30.index >= START_DATE].copy()

    m5 = pd.read_parquet(M5_BOXES)
    if m5.index.tz is None:
        m5.index = m5.index.tz_localize("UTC")
    m5 = m5[m5.index >= START_DATE].copy()
    m5["range"] = m5["high"] - m5["low"]

    feat = pd.read_parquet(FEATURES, columns=["daily_jac_dir"])
    if feat.index.tz is None:
        feat.index = feat.index.tz_localize("UTC")
    feat = feat[feat.index >= START_DATE]
    trend = feat["daily_jac_dir"].resample("5min").ffill().map({"up": "long", "down": "short"})
    m5["daily_trend"] = trend.reindex(m5.index, method="ffill")

    m30_trend = feat["daily_jac_dir"].resample("30min").ffill().map({"up": "long", "down": "short"})
    m30["daily_trend"] = m30_trend.reindex(m30.index, method="ffill")

    for col in ["atr14", "m30_liq_top", "m30_liq_bot", "m30_box_high", "m30_box_low",
                 "m30_box_confirmed", "m30_box_id", "m30_fmv"]:
        target = col if col != "atr14" else "atr_m30"
        m5[target] = m30[col].reindex(m5.index, method="ffill")

    nz = (m5["bar_delta"] != 0).sum()
    print(f"M30: {len(m30)} | M5: {len(m5)} | bar_delta non-zero: {nz} ({nz/len(m5)*100:.1f}%)")
    return m30, m5


def detect_box_ladder(m30, ts, trend_dir, min_boxes=3):
    subset = m30[m30.index <= ts]
    confirmed = subset[subset["m30_box_confirmed"] == True]
    if confirmed.empty:
        return False
    boxes = confirmed.groupby("m30_box_id")["m30_fmv"].first().sort_index()
    if len(boxes) < min_boxes:
        return False
    last_n = boxes.iloc[-min_boxes:].values
    if trend_dir == "long":
        return all(last_n[i] > last_n[i-1] for i in range(1, len(last_n)))
    elif trend_dir == "short":
        return all(last_n[i] < last_n[i-1] for i in range(1, len(last_n)))
    return False


def count_bars_outside(m30, ts, bh, bl, lookback=10):
    subset = m30[m30.index < ts].tail(lookback)
    count = 0
    for i in range(len(subset) - 1, -1, -1):
        c = float(subset.iloc[i]["close"])
        if c <= 0 or bl <= c <= bh:
            break
        count += 1
    return count


def compute_phase(m30, ts, daily_trend):
    before = m30[m30.index <= ts]
    if before.empty:
        return "UNKNOWN", {}
    row = before.iloc[-1]
    close = float(row["close"])
    bh, bl = row.get("m30_box_high"), row.get("m30_box_low")
    confirmed = bool(row.get("m30_box_confirmed", False))
    if pd.isna(bh) or pd.isna(bl) or close <= 0:
        return "UNKNOWN", {}
    bh, bl = float(bh), float(bl)
    if bl <= close <= bh:
        return "CONTRACTION", {}
    bars_out = count_bars_outside(m30, ts, bh, bl)
    if confirmed and daily_trend in ("long", "short"):
        ladder = detect_box_ladder(m30, ts, daily_trend)
        if ladder and bars_out >= TREND_ACCEPTANCE_MIN_BARS:
            return "TREND", {"bars_outside": bars_out, "ladder": True}
        elif ladder:
            return "EXPANSION", {"bars_outside": bars_out, "ladder": True}
        return "EXPANSION", {"bars_outside": bars_out, "ladder": False}
    return "EXPANSION", {"bars_outside": bars_out}


def main():
    print("SHADOW MODE v5: TREND_CONTINUATION (full bar_delta backfill)")
    print(f"Target: {TARGET_SIGNALS} signals | Period: {START_DATE} to today")
    print(f"Delta check: ACTIVE (real bar_delta from microstructure)")
    print()

    m30, m5 = load_data()

    # Find displacement bars: range + direction + close position + delta
    m5["min_range"] = m5["atr_m30"] * DISP_ATR_MULT

    # LONG displacement: bullish + range + close near high + delta >= 80
    long_d = m5[
        (m5["close"] > m5["open"]) &
        (m5["range"] >= m5["min_range"]) &
        ((m5["close"] - m5["low"]) / m5["range"].replace(0, np.nan) >= DISP_CLOSE_PCT) &
        (m5["bar_delta"] >= DISP_MIN_DELTA)
    ].copy()
    long_d["disp_dir"] = "LONG"

    # SHORT displacement: bearish + range + close near low + delta <= -80
    short_d = m5[
        (m5["close"] < m5["open"]) &
        (m5["range"] >= m5["min_range"]) &
        ((m5["high"] - m5["close"]) / m5["range"].replace(0, np.nan) >= DISP_CLOSE_PCT) &
        (m5["bar_delta"] <= -DISP_MIN_DELTA)
    ].copy()
    short_d["disp_dir"] = "SHORT"

    all_disp = pd.concat([long_d, short_d]).sort_index()
    print(f"Displacement bars with real delta: {len(all_disp)} (LONG={len(long_d)}, SHORT={len(short_d)})")

    # Scan for CONTINUATION signals
    print(f"\n{'='*100}")
    signals = []
    last_m30_w = None

    for idx, row in all_disp.iterrows():
        if len(signals) >= TARGET_SIGNALS:
            break

        ts = idx
        disp_dir = row["disp_dir"]
        close = float(row["close"])
        daily_trend = row.get("daily_trend")
        liq_top = row.get("m30_liq_top")
        liq_bot = row.get("m30_liq_bot")
        atr_m30 = row.get("atr_m30", 20.0)
        bh = row.get("m30_box_high")
        bl = row.get("m30_box_low")
        delta = float(row.get("bar_delta", 0))

        if pd.isna(atr_m30) or pd.isna(liq_top) or pd.isna(liq_bot):
            continue
        liq_top, liq_bot = float(liq_top), float(liq_bot)

        # Daily trend aligned
        expected = "long" if disp_dir == "LONG" else "short"
        if daily_trend != expected:
            continue

        # Not pullback (continuation = at liq zone WITH trend or above box)
        if disp_dir == "LONG":
            near_liq = abs(close - liq_top) <= V1_DIST * 2
            above_box = bh is not None and not pd.isna(bh) and close > float(bh)
            if not near_liq and not above_box:
                continue
            level = "liq_top"
        else:
            near_liq = abs(close - liq_bot) <= V1_DIST * 2
            below_box = bl is not None and not pd.isna(bl) and close < float(bl)
            if not near_liq and not below_box:
                continue
            level = "liq_bot"

        # Phase
        phase, phase_det = compute_phase(m30, ts, daily_trend)
        if phase not in ("TREND", "EXPANSION"):
            continue

        # Deduplicate by M30 window
        m30_w = ts.floor("30min")
        if m30_w == last_m30_w:
            continue
        last_m30_w = m30_w

        # Exhaustion: overextension
        exhausted = False
        exh_reason = "not exhausted"
        overext_thr = atr_m30 * OVEREXT_ATR_MULT
        if disp_dir == "LONG" and close > liq_top:
            dist = abs(close - liq_top)
            if dist > overext_thr:
                exhausted = True
                exh_reason = f"overextended LONG: {dist:.1f}pts > {overext_thr:.1f}"
        elif disp_dir == "SHORT" and close < liq_bot:
            dist = abs(liq_bot - close)
            if dist > overext_thr:
                exhausted = True
                exh_reason = f"overextended SHORT: {dist:.1f}pts > {overext_thr:.1f}"

        if exhausted:
            would_action = "BLOCK"
            reason = f"exhaustion: {exh_reason}"
        else:
            would_action = "GO"
            reason = f"CONTINUATION {disp_dir}: rng={row['range']:.1f} dlt={delta:+.0f} phase={phase}"

        signal = {
            "idx": len(signals) + 1,
            "timestamp": str(ts),
            "date": ts.strftime("%Y-%m-%d"),
            "close": close,
            "phase": phase,
            "bars_outside": phase_det.get("bars_outside", 0),
            "ladder": phase_det.get("ladder", False),
            "daily_trend": daily_trend,
            "direction": disp_dir,
            "level_type": level,
            "liq_top": liq_top,
            "liq_bot": liq_bot,
            "atr_m30": atr_m30,
            "displacement_range": float(row["range"]),
            "displacement_delta": delta,
            "exhausted": exhausted,
            "exhaustion_reason": exh_reason,
            "would_action": would_action,
            "decision_reason": reason,
        }
        signals.append(signal)

        icon = ">> GO  " if would_action == "GO" else "BLOCK  "
        print(f"  #{signal['idx']:>2} {icon} {ts}  {phase:11s}  {disp_dir}  "
              f"close={close:.1f}  rng={row['range']:.1f}  dlt={delta:+.0f}  exh={'Y' if exhausted else 'N'}")

    print(f"\n{'='*100}")
    df = pd.DataFrame(signals)
    total = len(df)
    print(f"Collected {total} TREND_CONTINUATION signals\n")

    if df.empty:
        print("No signals found!")
        return

    go = df[df["would_action"] == "GO"]
    block = df[df["would_action"] == "BLOCK"]

    # SUMMARY
    print(f"{'='*100}")
    print("SUMMARY")
    print(f"{'='*100}")
    print(f"Total:         {total}")
    print(f"  would_GO:    {len(go)} ({len(go)/total*100:.1f}%)")
    print(f"  would_BLOCK: {len(block)} ({len(block)/total*100:.1f}%)")

    print(f"\nBy phase:")
    for p in ["EXPANSION", "TREND"]:
        sub = df[df["phase"] == p]
        if not sub.empty:
            g = len(sub[sub["would_action"] == "GO"])
            print(f"  {p:11s}: {len(sub):>3} (GO={g}, BLOCK={len(sub)-g})")

    print(f"\nBy direction:")
    for d in sorted(df["direction"].unique()):
        sub = df[df["direction"] == d]
        g = len(sub[sub["would_action"] == "GO"])
        print(f"  {d:6s}: {len(sub):>3} (GO={g})")

    if not block.empty:
        print(f"\nBLOCK reasons:")
        exh_c = len(block[block["exhausted"] == True])
        print(f"  Exhaustion: {exh_c}")

    # Displacement quality
    print(f"\nDisplacement quality:")
    print(f"  All:  range={df['displacement_range'].mean():.1f}  delta={df['displacement_delta'].mean():+.1f}")
    if not go.empty:
        print(f"  GO:   range={go['displacement_range'].mean():.1f}  delta={go['displacement_delta'].mean():+.1f}")

    # QUALITATIVE
    print(f"\n{'='*100}")
    print(f"QUALITATIVE: {len(go)} would-GO signals")
    print(f"{'='*100}")
    valid = 0
    concern = 0
    if not go.empty:
        for _, r in go.iterrows():
            notes = []
            if r["phase"] == "TREND" and r["ladder"]:
                notes.append("TREND+ladder")
            elif r["phase"] == "EXPANSION":
                notes.append("EXPANSION")

            if abs(r["displacement_delta"]) >= 200:
                notes.append(f"strong dlt={r['displacement_delta']:+.0f}")
            elif abs(r["displacement_delta"]) >= 120:
                notes.append(f"solid dlt={r['displacement_delta']:+.0f}")

            overext_thr = r["atr_m30"] * OVEREXT_ATR_MULT
            if r["direction"] == "LONG" and r["liq_top"]:
                dist = abs(r["close"] - r["liq_top"])
                margin = overext_thr - dist
                if margin < r["atr_m30"] * 0.3:
                    notes.append("NEAR overext")

            quality = "VALID" if not any("NEAR overext" in n for n in notes) else "CONCERN"
            if quality == "VALID":
                valid += 1
            else:
                concern += 1

            print(f"  #{r['idx']:>2} {r['timestamp']}  {r['phase']:11s}  {r['direction']}  "
                  f"close={r['close']:.1f}  rng={r['displacement_range']:.1f}  "
                  f"dlt={r['displacement_delta']:+.0f}  [{quality}: {' | '.join(notes) if notes else 'clean'}]")

    print(f"\n  Likely valid: {valid}")
    print(f"  Concerns:     {concern}")

    # KEY OBSERVATIONS
    print(f"\n{'='*100}")
    print("KEY OBSERVATIONS")
    print(f"{'='*100}")
    print(f"  1. GO rate: {len(go)/total*100:.1f}% ({len(go)}/{total})")
    exp_c = len(df[df['phase'] == 'EXPANSION'])
    trend_c = len(df[df['phase'] == 'TREND'])
    print(f"  2. Phase: EXPANSION={exp_c}, TREND={trend_c}")
    print(f"  3. Exhaustion caught: {len(block)}/{total}")
    print(f"  4. Date spread: {df['date'].nunique()} unique dates")
    long_go = len(go[go['direction'] == 'LONG']) if not go.empty else 0
    short_go = len(go[go['direction'] == 'SHORT']) if not go.empty else 0
    print(f"  5. GO direction: LONG={long_go}, SHORT={short_go}")

    # Compare with v3 (no delta) and v4 (partial delta)
    print(f"\n  COMPARISON:")
    print(f"    v3 (no delta):     27 GO / 40 total (67.5%)")
    print(f"    v4 (partial):      32 GO / 40 total (80.0%)")
    print(f"    v5 (full delta):   {len(go)} GO / {total} total ({len(go)/total*100:.1f}%)")

    out = Path(r"C:\FluxQuantumAI\data\calibration")
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "shadow_trend_continuation_v5.parquet")
    df.to_csv(out / "shadow_trend_continuation_v5.csv", index=False)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()

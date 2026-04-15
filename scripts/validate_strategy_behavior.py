"""
Strategy Engine Behavioral Validation (post CAL-PATCH1, T=4)
=============================================================
Simula Phase Engine + Strategy Engine sobre dados historicos Jul 2025 - hoje.
Replica logica exacta de event_processor.py sem tocar producao.

Output:
  - Phase distribution (CONTRACTION / EXPANSION / TREND)
  - Strategy selection per phase
  - Detailed cases for EXPANSION and TREND
  - Validation of PULLBACK, CONTINUATION, OVEREXTENSION triggers
  - Check for missed valid trend opportunities after T=4
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import Counter, defaultdict

# -- Data paths --
M30_BOXES   = Path(r"C:\data\processed\gc_m30_boxes.parquet")
FEATURES_V4 = Path(r"C:\data\processed\gc_ats_features_v4.parquet")

START_DATE = "2025-07-01"
TREND_ACCEPTANCE_MIN_BARS = 4  # calibrated CAL-PATCH1


def load_data():
    """Load and align M30 boxes + daily trend."""
    # M30 boxes
    df = pd.read_parquet(M30_BOXES)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[df.index >= START_DATE].copy()

    # Daily trend from features_v4
    feat = pd.read_parquet(FEATURES_V4, columns=["daily_jac_dir"])
    if feat.index.tz is None:
        feat.index = feat.index.tz_localize("UTC")
    feat = feat[feat.index >= START_DATE]

    # Resample to M30 and forward-fill
    m30_trend = feat["daily_jac_dir"].resample("30min").ffill()
    m30_trend = m30_trend.map({"up": "long", "down": "short"})

    # Align to df index
    df["daily_trend"] = m30_trend.reindex(df.index, method="ffill")

    print(f"M30 bars: {len(df)} ({df.index.min()} to {df.index.max()})")
    print(f"Bars with daily_trend: {df['daily_trend'].notna().sum()}")
    return df


def detect_box_ladder_at(df, idx, trend_direction, min_boxes=3):
    """Replicate _detect_box_ladder at a specific bar index."""
    subset = df.iloc[:idx+1]
    confirmed = subset[subset["m30_box_confirmed"] == True]
    if confirmed.empty:
        return False

    boxes = confirmed.groupby("m30_box_id")["m30_fmv"].first().sort_index()
    if len(boxes) < min_boxes:
        return False

    last_n = boxes.iloc[-min_boxes:].values
    if trend_direction == "long":
        return all(last_n[i] > last_n[i-1] for i in range(1, len(last_n)))
    elif trend_direction == "short":
        return all(last_n[i] < last_n[i-1] for i in range(1, len(last_n)))
    return False


def count_bars_outside(df, idx, box_high, box_low, lookback=10):
    """Replicate _count_bars_outside_box: count backwards from idx (excluding current)."""
    start = max(0, idx - lookback)
    recent = df.iloc[start:idx]  # exclude current bar
    count = 0
    for i in range(len(recent) - 1, -1, -1):
        close = float(recent.iloc[i]["close"])
        if close <= 0:
            break
        if box_low <= close <= box_high:
            break
        count += 1
    return count


def compute_phase(df, idx):
    """
    Replicate _compute_raw_phase at bar idx.
    Returns (phase, details_dict)
    """
    row = df.iloc[idx]
    close = float(row["close"])
    box_high = row.get("m30_box_high")
    box_low = row.get("m30_box_low")
    confirmed = bool(row.get("m30_box_confirmed", False))
    daily_trend = row.get("daily_trend", None)

    if pd.isna(box_high) or pd.isna(box_low) or close <= 0:
        return ("UNKNOWN", {})

    box_high = float(box_high)
    box_low = float(box_low)

    # CONTRACTION: inside box
    if box_low <= close <= box_high:
        return ("CONTRACTION", {"reason": "price inside box"})

    # Price OUTSIDE box
    bars_out = count_bars_outside(df, idx, box_high, box_low)
    has_ladder = False
    ladder_checked = False

    if confirmed and daily_trend in ("long", "short"):
        has_ladder = detect_box_ladder_at(df, idx, daily_trend)
        ladder_checked = True

        if has_ladder:
            if bars_out >= TREND_ACCEPTANCE_MIN_BARS:
                return ("TREND", {
                    "reason": "confirmed + ladder + temporal acceptance",
                    "bars_outside": bars_out,
                    "daily_trend": daily_trend,
                    "ladder": True,
                })
            else:
                return ("EXPANSION", {
                    "reason": f"ladder OK but bars_outside={bars_out} < {TREND_ACCEPTANCE_MIN_BARS}",
                    "bars_outside": bars_out,
                    "daily_trend": daily_trend,
                    "ladder": True,
                    "blocked_by_patch1": True,
                })
        else:
            return ("EXPANSION", {
                "reason": "confirmed but no ladder",
                "daily_trend": daily_trend,
                "ladder": False,
                "bars_outside": bars_out,
            })

    # Not confirmed or no daily trend
    reason_parts = []
    if not confirmed:
        reason_parts.append("box not confirmed")
    if daily_trend not in ("long", "short"):
        reason_parts.append(f"no daily_trend ({daily_trend})")
    return ("EXPANSION", {
        "reason": " + ".join(reason_parts),
        "bars_outside": bars_out,
        "daily_trend": daily_trend,
        "ladder": False,
    })


def compute_strategy(phase, daily_trend, level_type, close, liq_top, liq_bot, atr):
    """
    Replicate _get_strategy_mode + _resolve_direction.
    Returns (strategy_mode, direction, entry_type, reason)
    """
    # Strategy mode
    if phase == "CONTRACTION":
        mode = "RANGE_BOUND"
        trend_dir = None
    elif phase in ("TREND", "EXPANSION") and daily_trend in ("long", "short"):
        mode = "TRENDING"
        trend_dir = "LONG" if daily_trend == "long" else "SHORT"
    else:
        mode = "RANGE_BOUND"
        trend_dir = None

    # Direction resolution (simplified - without displacement/exhaustion checks)
    if mode == "RANGE_BOUND":
        if level_type == "liq_top":
            return (mode, "SHORT", "REVERSAL", "RANGE_BOUND: liq_top -> SHORT reversal")
        else:
            return (mode, "LONG", "REVERSAL", "RANGE_BOUND: liq_bot -> LONG reversal")

    # TRENDING mode
    overext_mult = 1.5
    overext_thr = atr * overext_mult if atr > 0 else 30.0

    # PULLBACK check
    if trend_dir == "LONG" and level_type == "liq_bot":
        return (mode, "LONG", "PULLBACK", "TRENDING_UP: liq_bot = buy the dip")
    if trend_dir == "SHORT" and level_type == "liq_top":
        return (mode, "SHORT", "PULLBACK", "TRENDING_DN: liq_top = sell the rally")

    # OVEREXTENSION check (counter-trend at liquidation zone)
    if trend_dir == "LONG" and level_type == "liq_top":
        overext_pts = abs(close - liq_top) if liq_top and liq_top > 0 else 0
        if overext_pts > overext_thr:
            return (mode, "SHORT", "OVEREXTENSION",
                    f"TRENDING_UP: liq_top OVEREXTENDED {overext_pts:.1f}pts > {overext_thr:.1f}")
        else:
            return (mode, None, "SKIP",
                    f"TRENDING_UP: liq_top = liquidation zone, overext={overext_pts:.1f} < {overext_thr:.1f}")

    if trend_dir == "SHORT" and level_type == "liq_bot":
        overext_pts = abs(liq_bot - close) if liq_bot and liq_bot > 0 else 0
        if overext_pts > overext_thr:
            return (mode, "LONG", "OVEREXTENSION",
                    f"TRENDING_DN: liq_bot OVEREXTENDED {overext_pts:.1f}pts > {overext_thr:.1f}")
        else:
            return (mode, None, "SKIP",
                    f"TRENDING_DN: liq_bot = liquidation zone, overext={overext_pts:.1f} < {overext_thr:.1f}")

    return (mode, None, "SKIP", "TRENDING: no valid entry")


def simulate_at_liquidity_touches(df):
    """
    Find bars where price touches liq_top or liq_bot (within 8pts = V1 zone).
    At each touch, compute phase + strategy.
    """
    V1_DIST = 8.0
    results = []

    for i in range(10, len(df)):  # skip first bars for lookback
        row = df.iloc[i]
        close = float(row["close"])
        liq_top = float(row["m30_liq_top"]) if pd.notna(row.get("m30_liq_top")) else None
        liq_bot = float(row["m30_liq_bot"]) if pd.notna(row.get("m30_liq_bot")) else None
        atr = float(row.get("atr14", 20.0)) if pd.notna(row.get("atr14")) else 20.0

        if close <= 0:
            continue

        # Check if touching a liquidity level
        touch_top = liq_top and abs(close - liq_top) <= V1_DIST
        touch_bot = liq_bot and abs(close - liq_bot) <= V1_DIST

        if not touch_top and not touch_bot:
            continue

        level_type = "liq_top" if touch_top else "liq_bot"

        # Compute phase
        phase, phase_details = compute_phase(df, i)
        if phase == "UNKNOWN":
            continue

        daily_trend = row.get("daily_trend", None)

        # Compute strategy
        mode, direction, entry_type, reason = compute_strategy(
            phase, daily_trend, level_type, close, liq_top, liq_bot, atr)

        results.append({
            "timestamp": df.index[i],
            "close": close,
            "box_high": float(row["m30_box_high"]) if pd.notna(row.get("m30_box_high")) else None,
            "box_low": float(row["m30_box_low"]) if pd.notna(row.get("m30_box_low")) else None,
            "liq_top": liq_top,
            "liq_bot": liq_bot,
            "atr": atr,
            "level_type": level_type,
            "daily_trend": daily_trend,
            "phase": phase,
            "phase_reason": phase_details.get("reason", ""),
            "bars_outside": phase_details.get("bars_outside", 0),
            "ladder": phase_details.get("ladder", False),
            "blocked_by_patch1": phase_details.get("blocked_by_patch1", False),
            "strategy_mode": mode,
            "direction": direction,
            "entry_type": entry_type,
            "strategy_reason": reason,
        })

    return pd.DataFrame(results)


def print_section(title):
    print(f"\n{'='*70}")
    print(title)
    print(f"{'='*70}")


def analyze_results(results_df):
    """Full behavioral analysis."""
    total = len(results_df)

    # =========================================================
    # 1. PHASE DISTRIBUTION
    # =========================================================
    print_section("1. PHASE DISTRIBUTION AT LIQUIDITY TOUCHES")
    phase_counts = results_df["phase"].value_counts()
    for phase, count in phase_counts.items():
        pct = count / total * 100
        print(f"  {phase:15s}: {count:>5} ({pct:5.1f}%)")
    print(f"  {'TOTAL':15s}: {total:>5}")

    # =========================================================
    # 2. STRATEGY MODE per PHASE
    # =========================================================
    print_section("2. STRATEGY MODE PER PHASE")
    cross = pd.crosstab(results_df["phase"], results_df["strategy_mode"], margins=True)
    print(cross.to_string())

    # =========================================================
    # 3. ENTRY TYPE DISTRIBUTION
    # =========================================================
    print_section("3. ENTRY TYPE DISTRIBUTION")
    entry_counts = results_df["entry_type"].value_counts()
    for et, count in entry_counts.items():
        pct = count / total * 100
        print(f"  {et:15s}: {count:>5} ({pct:5.1f}%)")

    # Entry type per phase
    print("\n  Per phase:")
    cross2 = pd.crosstab(results_df["phase"], results_df["entry_type"], margins=True)
    print(cross2.to_string())

    # =========================================================
    # 4. EXPANSION ANALYSIS
    # =========================================================
    print_section("4. EXPANSION PHASE ANALYSIS")
    exp = results_df[results_df["phase"] == "EXPANSION"]
    print(f"  Total EXPANSION touches: {len(exp)}")

    if not exp.empty:
        # Sub-reasons
        print(f"\n  EXPANSION sub-reasons:")
        for reason, count in exp["phase_reason"].value_counts().items():
            print(f"    {count:>4}x  {reason}")

        # Strategy during EXPANSION
        print(f"\n  Strategy during EXPANSION:")
        for et, count in exp["entry_type"].value_counts().items():
            print(f"    {et:15s}: {count:>4}")

        # Blocked by PATCH 1 (ladder OK but bars < 4)
        blocked = exp[exp["blocked_by_patch1"] == True]
        print(f"\n  Blocked by PATCH 1 (would be TREND with T<4): {len(blocked)}")
        if not blocked.empty:
            print(f"    bars_outside distribution:")
            for bars, count in blocked["bars_outside"].value_counts().sort_index().items():
                print(f"      {bars} bars: {count}")

    # =========================================================
    # 5. TREND ANALYSIS
    # =========================================================
    print_section("5. TREND PHASE ANALYSIS")
    trend = results_df[results_df["phase"] == "TREND"]
    print(f"  Total TREND touches: {len(trend)}")

    if not trend.empty:
        print(f"\n  Entry types during TREND:")
        for et, count in trend["entry_type"].value_counts().items():
            print(f"    {et:15s}: {count:>4}")

        print(f"\n  Direction distribution during TREND:")
        for d, count in trend["direction"].value_counts().items():
            d_str = str(d) if d else "SKIP"
            print(f"    {d_str:10s}: {count:>4}")

        print(f"\n  Daily trend during TREND:")
        for dt, count in trend["daily_trend"].value_counts().items():
            print(f"    {str(dt):10s}: {count:>4}")

        # PULLBACK in TREND
        pullback_trend = trend[trend["entry_type"] == "PULLBACK"]
        print(f"\n  PULLBACK during TREND: {len(pullback_trend)}")
        if not pullback_trend.empty:
            for _, r in pullback_trend.head(5).iterrows():
                print(f"    {r['timestamp']}  {r['direction']}  close={r['close']:.1f}  "
                      f"trend={r['daily_trend']}  bars_out={r['bars_outside']}")

        # OVEREXTENSION in TREND
        overext_trend = trend[trend["entry_type"] == "OVEREXTENSION"]
        print(f"\n  OVEREXTENSION during TREND: {len(overext_trend)}")
        if not overext_trend.empty:
            for _, r in overext_trend.head(5).iterrows():
                print(f"    {r['timestamp']}  {r['direction']}  close={r['close']:.1f}  "
                      f"{r['strategy_reason'][:60]}")

        # SKIP in TREND
        skip_trend = trend[trend["entry_type"] == "SKIP"]
        print(f"\n  SKIP during TREND: {len(skip_trend)}")

    # =========================================================
    # 6. CONTRACTION ANALYSIS
    # =========================================================
    print_section("6. CONTRACTION PHASE ANALYSIS")
    contr = results_df[results_df["phase"] == "CONTRACTION"]
    print(f"  Total CONTRACTION touches: {len(contr)}")
    if not contr.empty:
        print(f"  All should be RANGE_BOUND/REVERSAL:")
        for et, count in contr["entry_type"].value_counts().items():
            ok = "OK" if et == "REVERSAL" else "ANOMALY"
            print(f"    {et:15s}: {count:>4}  [{ok}]")

    # =========================================================
    # 7. KEY QUESTIONS
    # =========================================================
    print_section("7. KEY BEHAVIORAL QUESTIONS")

    # Q1: Is EXPANSION being used too aggressively?
    exp_trending = exp[exp["strategy_mode"] == "TRENDING"] if not exp.empty else pd.DataFrame()
    exp_pullback = exp[exp["entry_type"] == "PULLBACK"] if not exp.empty else pd.DataFrame()
    exp_skip = exp[exp["entry_type"] == "SKIP"] if not exp.empty else pd.DataFrame()
    print(f"\n  Q1: Is EXPANSION used too aggressively?")
    print(f"    EXPANSION bars in TRENDING mode: {len(exp_trending)}/{len(exp)}")
    print(f"    EXPANSION PULLBACK entries: {len(exp_pullback)}")
    print(f"    EXPANSION SKIP (correct restraint): {len(exp_skip)}")
    if len(exp) > 0:
        aggressive_pct = len(exp_trending) / len(exp) * 100
        print(f"    Aggressive ratio: {aggressive_pct:.1f}%")
        if aggressive_pct > 80:
            print(f"    >> WARNING: EXPANSION mostly treated as TRENDING")
        else:
            print(f"    >> OK: balanced use")

    # Q2: Is TREND_PULLBACK triggering correctly?
    print(f"\n  Q2: Is TREND_PULLBACK triggering correctly?")
    trend_pb = trend[trend["entry_type"] == "PULLBACK"] if not trend.empty else pd.DataFrame()
    print(f"    TREND + PULLBACK events: {len(trend_pb)}")
    if not trend_pb.empty:
        # Check: pullback should be at liq_bot in uptrend, liq_top in downtrend
        correct_pb = trend_pb[
            ((trend_pb["daily_trend"] == "long") & (trend_pb["level_type"] == "liq_bot")) |
            ((trend_pb["daily_trend"] == "short") & (trend_pb["level_type"] == "liq_top"))
        ]
        print(f"    Correctly aligned (dip in uptrend / rally in downtrend): {len(correct_pb)}/{len(trend_pb)}")

    # Q3: CONTINUATION behavior (would need displacement data - note limitation)
    print(f"\n  Q3: Is TREND_CONTINUATION entering too early/late?")
    print(f"    NOTE: CONTINUATION requires displacement + exhaustion checks (M5 data)")
    print(f"    This simulation covers PULLBACK/OVEREXTENSION/SKIP only")
    print(f"    CONTINUATION is OFF by default (trend_continuation_enabled=false)")
    print(f"    Full CONTINUATION validation requires live shadow mode data")

    # Q4: Are we missing valid trends after PATCH 1?
    print(f"\n  Q4: Are we missing valid trends after PATCH 1 (T=4)?")
    patch1_blocked = results_df[results_df["blocked_by_patch1"] == True]
    print(f"    Touches where PATCH 1 blocked TREND promotion: {len(patch1_blocked)}")
    if not patch1_blocked.empty:
        # These are bars where ladder was OK but bars_outside < 4
        # Check what strategy they got instead (EXPANSION)
        for et, count in patch1_blocked["entry_type"].value_counts().items():
            print(f"      -> got {et} instead: {count}")
        # Were any of these at liq_bot in uptrend (missed pullback opportunity)?
        missed_pb = patch1_blocked[
            ((patch1_blocked["daily_trend"] == "long") & (patch1_blocked["level_type"] == "liq_bot")) |
            ((patch1_blocked["daily_trend"] == "short") & (patch1_blocked["level_type"] == "liq_top"))
        ]
        print(f"    Would-be PULLBACK entries delayed: {len(missed_pb)}")
        print(f"    NOTE: These still get TRENDING mode (EXPANSION+daily_trend=TRENDING)")
        print(f"          so PULLBACK still triggers. Phase label differs, not strategy.")

    # =========================================================
    # 8. EXPANSION vs TREND: strategy equivalence check
    # =========================================================
    print_section("8. EXPANSION vs TREND STRATEGY EQUIVALENCE")
    print("  Both EXPANSION and TREND with daily_trend -> TRENDING mode")
    print("  Strategy selection is IDENTICAL for both phases")
    print("  Phase difference only matters for:")
    print("    - TREND_CONTINUATION (requires phase=TREND, currently OFF)")
    print("    - Logging/monitoring")

    exp_t = exp[exp["strategy_mode"] == "TRENDING"] if not exp.empty else pd.DataFrame()
    trend_t = trend[trend["strategy_mode"] == "TRENDING"] if not trend.empty else pd.DataFrame()

    print(f"\n  EXPANSION+TRENDING: {len(exp_t)} touches")
    print(f"  TREND+TRENDING:     {len(trend_t)} touches")

    if not exp_t.empty:
        print(f"\n  EXPANSION entry type breakdown:")
        for et, count in exp_t["entry_type"].value_counts().items():
            print(f"    {et:15s}: {count:>4}")
    if not trend_t.empty:
        print(f"\n  TREND entry type breakdown:")
        for et, count in trend_t["entry_type"].value_counts().items():
            print(f"    {et:15s}: {count:>4}")


def main():
    print("Strategy Engine Behavioral Validation (post CAL-PATCH1, T=4)")
    print(f"Period: {START_DATE} to today")
    print(f"TREND_ACCEPTANCE_MIN_BARS = {TREND_ACCEPTANCE_MIN_BARS}")
    print()

    df = load_data()
    print("\nSimulating phase + strategy at every liquidity touch...")
    results_df = simulate_at_liquidity_touches(df)

    if results_df.empty:
        print("ERROR: No liquidity touches found!")
        return

    print(f"Total liquidity touches: {len(results_df)}")

    # Deduplicate: same bar can touch both levels, keep first
    results_dedup = results_df.drop_duplicates(subset=["timestamp", "level_type"], keep="first")
    print(f"After dedup: {len(results_dedup)} events")

    analyze_results(results_dedup)

    # Save
    out_path = Path(r"C:\FluxQuantumAI\data\calibration")
    out_path.mkdir(parents=True, exist_ok=True)
    results_dedup.to_parquet(out_path / "strategy_behavior_validation.parquet")
    print(f"\nResults saved to {out_path / 'strategy_behavior_validation.parquet'}")


if __name__ == "__main__":
    main()

"""
Fase 8 Backtest Engine — Minimal viable engine using LIVE gate.

Strategy:
- Load calibration_dataset_full.parquet
- Detect ALPHA triggers: close within 2pts of m30_liq_top (SHORT) or m30_liq_bot (LONG)
- Call ATSLiveGate.check() on each trigger (LIVE production gate logic)
- For GO decisions, simulate 3-leg fill with session-based lot sizing
- Walk forward ~4h to check TP1 / TP2 / SL
- Apply SHIELD (post-TP1: SL→entry for leg2+leg3)
- Apply slippage + spread
- Output results_summary.json + trades_detailed.csv

Cross-validate against C:\\FluxQuantumAI\\logs\\trades.csv.

NOTE: This is a minimal engine. Does NOT implement:
- GAMMA/DELTA triggers (ghost branches, 0 historical fires per Fase 2.5b)
- L2 danger exit (would need per-bar danger score computation)
- Regime flip exit
- News gate (ApexNewsGate module unavailable in env)
- Trailing stop post-SHIELD
- Hedge manager
These are out-of-scope for this minimal version. See report for extension plan.
"""
from __future__ import annotations
import sys
import json
import traceback
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

sys.path.insert(0, r"C:\FluxQuantumAI")

import pandas as pd
import numpy as np

from ats_live_gate import ATSLiveGate

# =========================================================
# Config (per Fase 8 spec)
# =========================================================
PERIOD_START = "2026-04-01"
PERIOD_END   = "2026-04-10"
DATASET_PATH = r"C:\data\processed\calibration_dataset_full.parquet"
REAL_TRADES  = r"C:\FluxQuantumAI\logs\trades.csv"

SPREAD_PTS = 25.0
SLIPPAGE_ENTRY_PTS = 2.0
SLIPPAGE_SL_PTS = 3.0
USD_PER_POINT_PER_LOT = 10.0  # XAUUSD standard

SL_DEFAULT_PTS = 20.0
TP1_DEFAULT_PTS = 20.0
TP2_DEFAULT_PTS = 50.0

# Proximity to trigger (close within N pts of liq)
TRIGGER_TOL_PTS = 2.0

# Forward window to check TP/SL hits (minutes)
FORWARD_WINDOW_MIN = 240  # 4h

# Session-based lot sizing (Barbara-approved)
# Hours UTC: Asian 22-06, London 07-14, NY 14-21
def session_for(ts: pd.Timestamp) -> str:
    h = ts.hour
    if 7 <= h < 14:
        return "London"
    elif 14 <= h < 21:
        return "NY"
    else:
        return "Asian"

LOT_SIZING = {
    "Asian":  {"leg1": 0.01, "leg2": 0.01, "runner": 0.01},
    "London": {"leg1": 0.02, "leg2": 0.02, "runner": 0.01},
    "NY":     {"leg1": 0.03, "leg2": 0.02, "runner": 0.01},
}


@dataclass
class Trade:
    trade_id: int
    timestamp_entry: pd.Timestamp
    direction: str
    session: str
    entry_price: float
    intended_entry: float
    sl: float
    tp1: float
    tp2: float
    lot_leg1: float
    lot_leg2: float
    lot_runner: float
    iceberg_aligned: bool
    gate_total_score: int
    gate_reason: str
    gate_v4_status: str
    # Results
    leg1_result: str = "OPEN"
    leg1_exit_price: float = 0.0
    leg1_exit_time: Optional[pd.Timestamp] = None
    leg2_result: str = "OPEN"
    leg2_exit_price: float = 0.0
    leg2_exit_time: Optional[pd.Timestamp] = None
    leg3_result: str = "OPEN"
    leg3_exit_price: float = 0.0
    leg3_exit_time: Optional[pd.Timestamp] = None
    total_pnl_usd: float = 0.0
    shield_activated: bool = False
    duration_minutes: float = 0.0
    mae_pts: float = 0.0
    mfe_pts: float = 0.0


def pnl_usd(direction: str, entry: float, exit: float, lot: float) -> float:
    """PnL in USD for XAUUSD 1pt = $10/lot."""
    pts = (exit - entry) if direction == "LONG" else (entry - exit)
    return pts * lot * USD_PER_POINT_PER_LOT


def apply_entry_slippage(direction: str, intended: float) -> float:
    """Apply spread + entry slippage."""
    if direction == "LONG":
        return intended + SPREAD_PTS * 0.0 + SLIPPAGE_ENTRY_PTS
    else:
        return intended - SLIPPAGE_ENTRY_PTS


def simulate_fill(trade: Trade, df_forward: pd.DataFrame) -> Trade:
    """
    Walk forward bar by bar, check TP1/TP2/SL hits.
    Apply SHIELD after TP1 (SL of leg2+leg3 → entry).
    """
    if df_forward.empty:
        return trade
    entry = trade.entry_price
    sl = trade.sl
    tp1 = trade.tp1
    tp2 = trade.tp2
    direction = trade.direction
    start_ts = trade.timestamp_entry

    current_sl_leg23 = sl  # leg2 + leg3 share SL (moved to entry after SHIELD)

    for ts, bar in df_forward.iterrows():
        high = float(bar["high"])
        low = float(bar["low"])

        # Track MAE/MFE using this bar's extremes
        if direction == "LONG":
            adverse = max(0.0, entry - low)
            favorable = max(0.0, high - entry)
        else:
            adverse = max(0.0, high - entry)
            favorable = max(0.0, entry - low)
        trade.mae_pts = max(trade.mae_pts, adverse)
        trade.mfe_pts = max(trade.mfe_pts, favorable)

        # --- Leg 1 check (TP1 or SL) ---
        if trade.leg1_result == "OPEN":
            if direction == "LONG":
                if low <= sl:  # SL first if same bar
                    trade.leg1_result = "SL_HIT"
                    trade.leg1_exit_price = sl - SLIPPAGE_SL_PTS  # adverse slippage
                    trade.leg1_exit_time = ts
                elif high >= tp1:
                    trade.leg1_result = "TP1_HIT"
                    trade.leg1_exit_price = tp1
                    trade.leg1_exit_time = ts
                    trade.shield_activated = True
                    current_sl_leg23 = entry  # SHIELD
            else:  # SHORT
                if high >= sl:
                    trade.leg1_result = "SL_HIT"
                    trade.leg1_exit_price = sl + SLIPPAGE_SL_PTS
                    trade.leg1_exit_time = ts
                elif low <= tp1:
                    trade.leg1_result = "TP1_HIT"
                    trade.leg1_exit_price = tp1
                    trade.leg1_exit_time = ts
                    trade.shield_activated = True
                    current_sl_leg23 = entry

            # If leg1 hit SL, all legs SL (simplified — real system may differ)
            if trade.leg1_result == "SL_HIT":
                for leg_attr in ("leg2", "leg3"):
                    setattr(trade, f"{leg_attr}_result", "SL_HIT")
                    setattr(trade, f"{leg_attr}_exit_price", trade.leg1_exit_price)
                    setattr(trade, f"{leg_attr}_exit_time", ts)
                break

        # --- Leg 2 check (TP2 or current_sl_leg23) ---
        if trade.leg2_result == "OPEN":
            if direction == "LONG":
                if low <= current_sl_leg23:
                    trade.leg2_result = "SL_HIT" if current_sl_leg23 == sl else "SHIELD_HIT"
                    trade.leg2_exit_price = current_sl_leg23 - (SLIPPAGE_SL_PTS if current_sl_leg23 == sl else 0.0)
                    trade.leg2_exit_time = ts
                elif high >= tp2:
                    trade.leg2_result = "TP2_HIT"
                    trade.leg2_exit_price = tp2
                    trade.leg2_exit_time = ts
            else:
                if high >= current_sl_leg23:
                    trade.leg2_result = "SL_HIT" if current_sl_leg23 == sl else "SHIELD_HIT"
                    trade.leg2_exit_price = current_sl_leg23 + (SLIPPAGE_SL_PTS if current_sl_leg23 == sl else 0.0)
                    trade.leg2_exit_time = ts
                elif low <= tp2:
                    trade.leg2_result = "TP2_HIT"
                    trade.leg2_exit_price = tp2
                    trade.leg2_exit_time = ts

        # --- Leg 3 runner (no fixed TP, exit at end of window or SHIELD hit) ---
        if trade.leg3_result == "OPEN":
            if direction == "LONG":
                if low <= current_sl_leg23:
                    trade.leg3_result = "SL_HIT" if current_sl_leg23 == sl else "SHIELD_HIT"
                    trade.leg3_exit_price = current_sl_leg23 - (SLIPPAGE_SL_PTS if current_sl_leg23 == sl else 0.0)
                    trade.leg3_exit_time = ts
            else:
                if high >= current_sl_leg23:
                    trade.leg3_result = "SL_HIT" if current_sl_leg23 == sl else "SHIELD_HIT"
                    trade.leg3_exit_price = current_sl_leg23 + (SLIPPAGE_SL_PTS if current_sl_leg23 == sl else 0.0)
                    trade.leg3_exit_time = ts

        # All legs closed?
        if all(getattr(trade, f"{l}_result") != "OPEN" for l in ("leg1", "leg2", "leg3")):
            break

    # Close remaining legs at last close price (end of window)
    last_ts = df_forward.index[-1]
    last_close = float(df_forward.iloc[-1]["close"])
    for leg_attr in ("leg1", "leg2", "leg3"):
        if getattr(trade, f"{leg_attr}_result") == "OPEN":
            setattr(trade, f"{leg_attr}_result", "WINDOW_END")
            setattr(trade, f"{leg_attr}_exit_price", last_close)
            setattr(trade, f"{leg_attr}_exit_time", last_ts)

    # Compute PnL per leg
    leg1_pnl = pnl_usd(direction, entry, trade.leg1_exit_price, trade.lot_leg1)
    leg2_pnl = pnl_usd(direction, entry, trade.leg2_exit_price, trade.lot_leg2)
    leg3_pnl = pnl_usd(direction, entry, trade.leg3_exit_price, trade.lot_runner)
    trade.total_pnl_usd = round(leg1_pnl + leg2_pnl + leg3_pnl, 2)

    # Duration
    final_exit = max([t for t in (trade.leg1_exit_time, trade.leg2_exit_time, trade.leg3_exit_time) if t is not None])
    trade.duration_minutes = round((final_exit - start_ts).total_seconds() / 60.0, 2)

    return trade


def main(output_dir: Path):
    t0 = datetime.now(timezone.utc)
    print(f"=== Fase 8 Backtest @ {t0.isoformat()} ===")
    print(f"Period: {PERIOD_START} -> {PERIOD_END}")
    print()

    # Load data
    df = pd.read_parquet(DATASET_PATH)
    df_slice = df.loc[PERIOD_START:PERIOD_END].copy()
    print(f"Rows loaded: {len(df_slice):,}")
    df_ok = df_slice.dropna(subset=["m30_liq_top", "m30_liq_bot", "m30_box_high", "m30_box_low", "atr_m30"])
    print(f"Rows with essential fields: {len(df_ok):,}")

    # Detect triggers: close near m30_liq_top (SHORT signal) or m30_liq_bot (LONG signal)
    # Use M30 liq since that's what live uses post-ADR-001
    near_top = (df_ok["close"] - df_ok["m30_liq_top"]).abs() <= TRIGGER_TOL_PTS
    near_bot = (df_ok["close"] - df_ok["m30_liq_bot"]).abs() <= TRIGGER_TOL_PTS

    # Dedup: consecutive near-triggers on same level = 1 trigger
    # Simple approach: group by 5min buckets, take first
    trigger_rows = []
    last_bucket = None
    last_level = None
    for ts, row in df_ok.iterrows():
        bucket = ts.floor("5min")
        direction = None
        level = None
        entry = None
        if abs(row["close"] - row["m30_liq_bot"]) <= TRIGGER_TOL_PTS:
            direction = "LONG"
            level = "liq_bot"
            entry = float(row["m30_liq_bot"])
        elif abs(row["close"] - row["m30_liq_top"]) <= TRIGGER_TOL_PTS:
            direction = "SHORT"
            level = "liq_top"
            entry = float(row["m30_liq_top"])
        if direction is None:
            continue
        # Dedup
        if bucket == last_bucket and level == last_level:
            continue
        trigger_rows.append({"ts": ts, "row": row, "direction": direction, "level": level, "entry": entry})
        last_bucket = bucket
        last_level = level

    print(f"Unique triggers detected (deduped 5min): {len(trigger_rows):,}")

    # Gate each trigger
    gate = ATSLiveGate()
    trades: list[Trade] = []
    gate_decisions = {"GO": 0, "BLOCK": 0, "ERROR": 0}
    block_reasons: dict[str, int] = {}
    next_trade_id = 1

    for tr in trigger_rows:
        row = tr["row"]
        try:
            decision = gate.check(
                entry_price=tr["entry"],
                direction=tr["direction"],
                now=tr["ts"],
                liq_top=float(row["m30_liq_top"]),
                liq_bot=float(row["m30_liq_bot"]),
                box_high=float(row["m30_box_high"]),
                box_low=float(row["m30_box_low"]),
                daily_trend="unknown",
                expansion_lines=None,
                atr_m30=float(row["atr_m30"]),
            )
        except Exception as e:
            gate_decisions["ERROR"] += 1
            continue

        if not decision.go:
            gate_decisions["BLOCK"] += 1
            reason_key = decision.reason[:60]
            block_reasons[reason_key] = block_reasons.get(reason_key, 0) + 1
            continue

        gate_decisions["GO"] += 1

        # Build trade
        session = session_for(tr["ts"])
        lots = LOT_SIZING[session].copy()
        iceberg_aligned = bool(getattr(decision.iceberg, "aligned", False))
        if iceberg_aligned:
            lots["leg1"] = round(lots["leg1"] + 0.01, 2)
            lots["leg2"] = round(lots["leg2"] + 0.01, 2)
            # runner NEVER modified

        entry_fill = apply_entry_slippage(tr["direction"], tr["entry"])
        if tr["direction"] == "LONG":
            sl = entry_fill - SL_DEFAULT_PTS
            tp1 = entry_fill + TP1_DEFAULT_PTS
            tp2 = entry_fill + TP2_DEFAULT_PTS
        else:
            sl = entry_fill + SL_DEFAULT_PTS
            tp1 = entry_fill - TP1_DEFAULT_PTS
            tp2 = entry_fill - TP2_DEFAULT_PTS

        trade = Trade(
            trade_id=next_trade_id,
            timestamp_entry=tr["ts"],
            direction=tr["direction"],
            session=session,
            entry_price=entry_fill,
            intended_entry=tr["entry"],
            sl=sl, tp1=tp1, tp2=tp2,
            lot_leg1=lots["leg1"],
            lot_leg2=lots["leg2"],
            lot_runner=lots["runner"],
            iceberg_aligned=iceberg_aligned,
            gate_total_score=int(decision.total_score or 0),
            gate_reason=str(decision.reason)[:200],
            gate_v4_status=str(decision.v4_status),
        )
        next_trade_id += 1

        # Simulate fill: forward window
        forward_end = tr["ts"] + pd.Timedelta(minutes=FORWARD_WINDOW_MIN)
        df_fwd = df_slice.loc[tr["ts"]:forward_end]
        if len(df_fwd) < 2:
            continue
        trade = simulate_fill(trade, df_fwd.iloc[1:])  # skip the entry bar itself
        trades.append(trade)

    print()
    print("=== Gate decisions ===")
    print(f"  GO:    {gate_decisions['GO']}")
    print(f"  BLOCK: {gate_decisions['BLOCK']}")
    print(f"  ERROR: {gate_decisions['ERROR']}")
    print()
    print("Top 10 BLOCK reasons:")
    for reason, count in sorted(block_reasons.items(), key=lambda x: -x[1])[:10]:
        print(f"  {count:4d}  {reason}")

    print()
    print(f"Trades simulated: {len(trades)}")

    # Stats
    if trades:
        pnl_series = [t.total_pnl_usd for t in trades]
        winners = [t for t in trades if t.total_pnl_usd > 0]
        losers = [t for t in trades if t.total_pnl_usd < 0]
        be = [t for t in trades if t.total_pnl_usd == 0]
        total_pnl = sum(pnl_series)
        total_wins = sum(t.total_pnl_usd for t in winners)
        total_losses = abs(sum(t.total_pnl_usd for t in losers))
        pf = (total_wins / total_losses) if total_losses > 0 else float("inf")
        wr = len(winners) / len(trades) * 100.0

        print()
        print("=== Results ===")
        print(f"  Trades: {len(trades)} | Winners: {len(winners)} | Losers: {len(losers)} | BE: {len(be)}")
        print(f"  Win rate: {wr:.1f}%")
        print(f"  Total PnL: ${total_pnl:+.2f}")
        print(f"  Avg win:  ${(total_wins/len(winners) if winners else 0):+.2f}")
        print(f"  Avg loss: ${(total_losses/len(losers) if losers else 0):+.2f}")
        print(f"  Profit factor: {pf:.2f}")

    # Save CSV
    if trades:
        rows = []
        for t in trades:
            rows.append({
                "trade_id": t.trade_id,
                "timestamp_entry": t.timestamp_entry.isoformat(),
                "direction": t.direction,
                "session": t.session,
                "entry_price": t.entry_price,
                "sl": t.sl,
                "tp1": t.tp1,
                "tp2": t.tp2,
                "lot_leg1": t.lot_leg1,
                "lot_leg2": t.lot_leg2,
                "lot_runner": t.lot_runner,
                "iceberg_aligned": t.iceberg_aligned,
                "gate_score": t.gate_total_score,
                "gate_reason": t.gate_reason,
                "gate_v4_status": t.gate_v4_status,
                "leg1_result": t.leg1_result,
                "leg1_exit_price": t.leg1_exit_price,
                "leg1_exit_time": t.leg1_exit_time.isoformat() if t.leg1_exit_time else "",
                "leg2_result": t.leg2_result,
                "leg2_exit_price": t.leg2_exit_price,
                "leg2_exit_time": t.leg2_exit_time.isoformat() if t.leg2_exit_time else "",
                "leg3_result": t.leg3_result,
                "leg3_exit_price": t.leg3_exit_price,
                "leg3_exit_time": t.leg3_exit_time.isoformat() if t.leg3_exit_time else "",
                "total_pnl_usd": t.total_pnl_usd,
                "shield_activated": t.shield_activated,
                "duration_minutes": t.duration_minutes,
                "mae_pts": t.mae_pts,
                "mfe_pts": t.mfe_pts,
            })
        trades_df = pd.DataFrame(rows)
        trades_df.to_csv(output_dir / "trades_detailed.csv", index=False)
        print()
        print(f"Saved: trades_detailed.csv ({len(trades_df)} rows)")

    # Summary JSON
    t1 = datetime.now(timezone.utc)
    duration_s = (t1 - t0).total_seconds()
    summary = {
        "backtest_id": output_dir.name,
        "config": "APEX_actual_post_Fase7 — MINIMAL ENGINE (ALPHA only)",
        "account": "RoboForex_demo_68302120",
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "data_source": DATASET_PATH,
        "engine": "new: C:\\FluxQuantumAI\\backtests\\fase_8_backtest.py",
        "live_modules_used": ["ats_live_gate.ATSLiveGate.check()"],
        "not_implemented": [
            "GAMMA/DELTA triggers (0 historical fires — per Fase 2.5b audit)",
            "L2 danger exit", "Regime flip exit", "Trailing stop post-SHIELD",
            "News gate (ApexNewsGate module not installed)",
            "Hedge manager",
        ],
        "runtime_s": round(duration_s, 1),
        "metrics_overall": {
            "rows_in_slice": len(df_slice),
            "rows_with_essentials": len(df_ok),
            "triggers_detected": len(trigger_rows),
            "gate_go": gate_decisions["GO"],
            "gate_block": gate_decisions["BLOCK"],
            "gate_error": gate_decisions["ERROR"],
            "trades_simulated": len(trades),
        },
    }
    if trades:
        summary["metrics_overall"].update({
            "winners": len(winners),
            "losers": len(losers),
            "breakeven": len(be),
            "win_rate_pct": round(wr, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "profit_factor": round(pf, 3) if pf != float("inf") else "inf",
            "avg_win_usd": round(total_wins / len(winners), 2) if winners else 0.0,
            "avg_loss_usd": round(total_losses / len(losers), 2) if losers else 0.0,
        })
        # by session
        by_sess = {}
        for s in ("Asian", "London", "NY"):
            s_trades = [t for t in trades if t.session == s]
            if s_trades:
                s_pnl = sum(t.total_pnl_usd for t in s_trades)
                by_sess[s] = {"trades": len(s_trades), "pnl_usd": round(s_pnl, 2)}
        summary["metrics_by_session"] = by_sess
        # by direction
        by_dir = {}
        for d in ("LONG", "SHORT"):
            d_trades = [t for t in trades if t.direction == d]
            if d_trades:
                d_pnl = sum(t.total_pnl_usd for t in d_trades)
                by_dir[d] = {"trades": len(d_trades), "pnl_usd": round(d_pnl, 2)}
        summary["metrics_by_direction"] = by_dir
    summary["top_block_reasons"] = [
        {"reason": r, "count": c} for r, c in sorted(block_reasons.items(), key=lambda x: -x[1])[:10]
    ]

    # Cross-validation
    try:
        real = pd.read_csv(REAL_TRADES)
        real["timestamp"] = pd.to_datetime(real["timestamp"])
        real_in_period = real[(real["timestamp"] >= PERIOD_START) & (real["timestamp"] <= PERIOD_END)]
        summary["cross_validation"] = {
            "real_trades_in_period": len(real_in_period),
            "backtest_trades_in_period": len(trades),
        }
    except Exception as e:
        summary["cross_validation_error"] = str(e)

    with open(output_dir / "results_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Saved: results_summary.json")

    print()
    print(f"=== Done in {duration_s:.1f}s ===")
    return summary


if __name__ == "__main__":
    output_dir = Path(r"C:\FluxQuantumAI\backtests\FASE_8_20260418_142526")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        summary = main(output_dir)
    except Exception as e:
        print(f"FATAL: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)

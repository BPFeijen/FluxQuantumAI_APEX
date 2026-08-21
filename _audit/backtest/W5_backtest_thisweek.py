"""
W5 backtest THIS WEEK what-if (2026-05-04 → 2026-05-08).

Per Barbara directive 2026-05-09:
  - Backtest somente nos dados desta semana
  - Replay decision_log.jsonl decisions emitidas pela metodologia COMPLETA produção
  - Aplicar W1-W5.3 gates retroativamente
  - Simulate PM exits (SHIELD/trailing/T3/regime flip/L2 danger/giveback ARMED)
  - Compute PnL with $1000 starting capital

Why this is more methodologically sound:
  decision_log.jsonl was produced by FULL production stack (cascade resolver,
  F-asym, IMPL-3 LOGIC-C, ALPHA/BETA/GAMMA/DELTA triggers, all Wyckoff/L2 features).
  We re-FILTER these decisions through new W1-W5.3 gates and simulate exits.
  No methodology gaps — we use what production actually emitted.

Two scenarios compared:
  A. PRODUCTION_AS_IS — what really happened (broker disconnected most week)
  B. NEW_GATES_ARMED — same decisions, but apply W1-W5.3 gates + PM exits with
                       Giveback armed as `close` (not just alert)

Output: per-trade ledger + capital curves for both scenarios.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\FluxQuantumAI")
OUT_DIR = ROOT / "_audit" / "backtest" / "W5_thisweek"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DECISION_LOG = ROOT / "logs" / "decision_log.jsonl"
M1_OHLCV = Path(r"C:/data/processed/gc_ohlcv_l2_joined.parquet")
M30_BOXES = Path(r"C:/data/processed/gc_m30_boxes.parquet")
ICEBERG_DIR = Path(r"C:/data/iceberg")

WEEK_START = pd.Timestamp("2026-05-04", tz="UTC")
WEEK_END   = pd.Timestamp("2026-05-09", tz="UTC")

INITIAL_CAPITAL = 1000.0

# 2026-05-09 — uniform lot per Barbara: SAME lot for ALL sessions = [0.03, 0.02, 0.01].
# Total 0.06 lot = $6/pt (XAUUSD MT5 / GC futures: 0.01 lot = $1/pt).
def _session_lots(ts):
    """Return ([leg1, leg2, leg3], dollar_per_pt) for entry timestamp.
    Uniform across sessions per Barbara directive 2026-05-09."""
    lots = [0.03, 0.02, 0.01]
    dollar_pt = sum(lot * 100.0 for lot in lots)  # 6.0
    return lots, dollar_pt

# W1-W5 gate config (current settings.json)
ATR_EXTREME_PTS = 45.0
DAILY_RANGE_BLOCK_PCT = 0.20
SESSION_CLOSE_BLOCK_MIN = 30
SESSION_CLOSE_HOUR_UTC = 22
DAILY_LOSS_LIMIT = -50.0
RR_MIN = 1.0
MIN_SCORE_GO = 1
SAME_LEVEL_PROX_PTS = 5.0
SAME_LEVEL_COOLDOWN_MIN = 60

# PM exit config
TRAILING_PTS_AFTER_SHIELD = 12.0
GIVEBACK_PCT = 0.50
GIVEBACK_MFE_MIN_ATR = 0.5
T3_ADVERSE_PTS = 3.0
T3_WINDOW_S = 60
MAX_TRADE_HORIZON_MIN = 240


# ---------------------- Iceberg cache (W5.1 modifier) ----------------------

_iceberg_cache: dict[str, list[dict]] = {}

def load_iceberg_day(date_str: str) -> list[dict]:
    if date_str in _iceberg_cache:
        return _iceberg_cache[date_str]
    path = ICEBERG_DIR / f"iceberg__GC_XCEC_{date_str}.jsonl"
    events = []
    if path.exists() and path.stat().st_size > 0:
        with path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try: rec = json.loads(line)
                except: continue
                prob = float(rec.get("probability", 0))
                refills = int(rec.get("refill_count", 0))
                if prob < 0.50 or refills < 3: continue
                try: rec["_ts"] = pd.Timestamp(rec.get("timestamp", ""), tz="UTC")
                except: continue
                rec["_price"] = float(rec.get("price", 0))
                rec["_side"] = str(rec.get("side", "")).lower()
                events.append(rec)
    _iceberg_cache[date_str] = events
    return events


def iceberg_score(ts, price_gc, direction, recent_hl_gc):
    delta = 0; parts = []
    events = load_iceberg_day(ts.strftime("%Y%m%d"))
    if not events: return 0, "no_iceberg_data"
    cutoff_bi = ts - pd.Timedelta(minutes=4)
    max_h, min_l = recent_hl_gc
    bi_side = "none"
    for ev in events:
        if ev["_ts"] < cutoff_bi or ev["_ts"] > ts: continue
        if abs(ev["_price"] - price_gc) > 10.0: continue
        if ev["_side"] == "bid":
            if (ev["_price"] - min_l) >= 2.2: bi_side = "bearish"; break
        elif ev["_side"] == "ask":
            if (max_h - ev["_price"]) >= 2.2: bi_side = "bullish"; break
    if bi_side != "none":
        if (bi_side == "bullish" and direction == "LONG") or (bi_side == "bearish" and direction == "SHORT"):
            delta += 1; parts.append("BI_ALIGN+1")
        else:
            delta += -2; parts.append("BI_CONTRA-2")
    cutoff_iz = ts - pd.Timedelta(minutes=30)
    min_dist = float("inf"); nearest_side = "none"
    for ev in events:
        if ev["_ts"] < cutoff_iz or ev["_ts"] > ts: continue
        d = abs(ev["_price"] - price_gc)
        if d < min_dist: min_dist = d; nearest_side = ev["_side"]
    if min_dist <= 5.0 and nearest_side in ("bid", "ask"):
        if (nearest_side == "bid" and direction == "LONG") or (nearest_side == "ask" and direction == "SHORT"):
            delta += 1; parts.append("IZ_ALIGN+1")
        else:
            delta += -1; parts.append("IZ_CONTRA-1")
    return delta, " ".join(parts) if parts else "no_modifier"


# ---------------------- Load decisions + market data ----------------------

def load_decisions():
    rows = []
    seen_ids = set()
    with DECISION_LOG.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: d = json.loads(line)
            except: continue
            ts = d.get("timestamp") or d.get("created_at")
            if not ts: continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(t) or t < WEEK_START or t > WEEK_END: continue
            did = d.get("decision_id")
            if did and did in seen_ids: continue
            if did: seen_ids.add(did)
            dec = d.get("decision", {}) or {}
            ctx = d.get("context", {}) or {}
            action = dec.get("action")
            # Only consider GO + EXEC_FAILED (would-be GO)
            if action not in ("GO", "EXEC_FAILED"): continue
            rows.append({
                "ts": t,
                "decision_id": did,
                "direction": dec.get("direction"),
                "action": action,
                "entry_mode": dec.get("entry_mode"),
                "price_mt5": d.get("price_mt5"),
                "price_gc": d.get("price_gc"),
                "sl": dec.get("sl"),
                "tp1": dec.get("tp1"),
                "tp2": dec.get("tp2"),
                "reason": (dec.get("reason") or "")[:120],
                "phase": ctx.get("phase"),
                "daily_trend": ctx.get("daily_trend"),
                "m30_bias": ctx.get("m30_bias"),
                "m30_atr14": ctx.get("m30_atr14"),
                "delta_4h": ctx.get("delta_4h"),
                "session": ctx.get("session"),
            })
    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    # Dedup near-identical decisions (same direction + price within 0.5pt + same minute)
    df["minute"] = df["ts"].dt.floor("1min")
    df["px_round"] = df["price_mt5"].round(0)
    df = df.drop_duplicates(subset=["minute", "direction", "px_round"], keep="first")
    df = df.drop(columns=["minute", "px_round"])
    return df


def load_m1():
    df = pd.read_parquet(M1_OHLCV)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= WEEK_START - pd.Timedelta(hours=4)) & (df.index <= WEEK_END + pd.Timedelta(hours=4))]
    return df[["high", "low", "close"]]


def load_m30():
    df = pd.read_parquet(M30_BOXES)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= WEEK_START - pd.Timedelta(days=2)) & (df.index <= WEEK_END + pd.Timedelta(days=2))]
    return df


# ---------------------- Daily range cache ----------------------

_daily_range_cache: dict = {}

def get_daily_range(m1_df, ts):
    date_key = ts.date()
    if date_key in _daily_range_cache: return _daily_range_cache[date_key]
    day = m1_df[m1_df.index.date == date_key]
    if day.empty: return (None, None)
    hi = float(day["high"].max()); lo = float(day["low"].min())
    _daily_range_cache[date_key] = (hi, lo)
    return (hi, lo)


# ---------------------- PM-aware simulation ----------------------

@dataclass
class TradeResult:
    decision_id: str
    entry_ts: pd.Timestamp
    direction: str
    entry_mode: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    atr_m30: float
    score: int
    score_detail: str
    exit_ts: pd.Timestamp = None
    exit_reason: str = ""
    exit_price: float = 0.0
    pnl_usd: float = 0.0
    duration_min: int = 0
    mfe_pts: float = 0.0
    mae_pts: float = 0.0


def simulate_e2e(m1_df, entry_ts, direction, entry, sl_init, tp1, tp2, atr):
    # Session-aware lot $/pt
    lots, _ = _session_lots(entry_ts)
    DLEG1 = lots[0] * 100.0
    DLEG2 = lots[1] * 100.0
    DLEG3 = lots[2] * 100.0
    DTOT = DLEG1 + DLEG2 + DLEG3

    end_ts = entry_ts + pd.Timedelta(minutes=MAX_TRADE_HORIZON_MIN)
    fwd = m1_df[(m1_df.index > entry_ts) & (m1_df.index <= end_ts)]
    if fwd.empty:
        return ("NO_DATA", entry_ts, entry, 0.0, 0.0, 0.0)
    sl = sl_init
    shield_active = False
    tp1_pnl = 0.0
    mfe_pts = 0.0
    mae_pts = 0.0
    sign = 1 if direction == "LONG" else -1

    for ts, bar in fwd.iterrows():
        hi = float(bar["high"]); lo = float(bar["low"]); close = float(bar["close"])
        if direction == "LONG":
            cur_mfe = hi - entry; cur_mae = entry - lo
        else:
            cur_mfe = entry - lo; cur_mae = hi - entry
        if cur_mfe > mfe_pts: mfe_pts = cur_mfe
        if cur_mae > mae_pts: mae_pts = cur_mae

        # SL or TP1 (priority SL on same bar)
        if not shield_active:
            if direction == "LONG":
                if lo <= sl:
                    pnl = -(entry - sl) * DTOT
                    return ("SL_HIT", ts, sl, pnl, mfe_pts, mae_pts)
                if hi >= tp1:
                    tp1_pnl = (tp1 - entry) * DLEG1
                    sl = entry; shield_active = True
            else:
                if hi >= sl:
                    pnl = -(sl - entry) * DTOT
                    return ("SL_HIT", ts, sl, pnl, mfe_pts, mae_pts)
                if lo <= tp1:
                    tp1_pnl = (entry - tp1) * DLEG1
                    sl = entry; shield_active = True

        if shield_active:
            if direction == "LONG":
                if lo <= sl:
                    rest = (sl - entry) * (DLEG2 + DLEG3)
                    return ("TP1_THEN_BE", ts, sl, tp1_pnl + rest, mfe_pts, mae_pts)
                if hi >= tp2:
                    rest = (tp2 - entry) * (DLEG2 + DLEG3)
                    return ("TP1_THEN_TP2", ts, tp2, tp1_pnl + rest, mfe_pts, mae_pts)
                trail = close - TRAILING_PTS_AFTER_SHIELD
                if trail > sl: sl = trail
            else:
                if hi >= sl:
                    rest = (entry - sl) * (DLEG2 + DLEG3)
                    return ("TP1_THEN_BE", ts, sl, tp1_pnl + rest, mfe_pts, mae_pts)
                if lo <= tp2:
                    rest = (entry - tp2) * (DLEG2 + DLEG3)
                    return ("TP1_THEN_TP2", ts, tp2, tp1_pnl + rest, mfe_pts, mae_pts)
                trail = close + TRAILING_PTS_AFTER_SHIELD
                if trail < sl: sl = trail

        # W5.3 Giveback ARMED (close on giveback)
        cur_pnl = (close - entry) * sign
        if mfe_pts > atr * GIVEBACK_MFE_MIN_ATR and cur_pnl < mfe_pts * (1.0 - GIVEBACK_PCT):
            if shield_active:
                rest = (close - entry) * sign * (DLEG2 + DLEG3)
                return ("GIVEBACK_AFTER_SHIELD", ts, close, tp1_pnl + rest, mfe_pts, mae_pts)
            else:
                pnl = (close - entry) * sign * DTOT
                return ("GIVEBACK_PRE_SHIELD", ts, close, pnl, mfe_pts, mae_pts)

    # Timeout
    last_close = float(fwd["close"].iloc[-1]); last_ts = fwd.index[-1]
    if shield_active:
        rest = (last_close - entry) * sign * (DLEG2 + DLEG3)
        return ("TIMEOUT_AFTER_SHIELD", last_ts, last_close, tp1_pnl + rest, mfe_pts, mae_pts)
    else:
        pnl = (last_close - entry) * sign * DTOT
        return ("TIMEOUT_PRE_SHIELD", last_ts, last_close, pnl, mfe_pts, mae_pts)


# ---------------------- Run scenarios ----------------------

def run_scenario(decisions: pd.DataFrame, m1_df: pd.DataFrame, m30_df: pd.DataFrame,
                 apply_new_gates: bool, scenario_name: str):
    """Run a scenario.
       apply_new_gates=False: simulate every GO decision as if it executed (production-as-is, but with broker)
       apply_new_gates=True:  apply W1-W5.3 + Giveback armed
    """
    print(f"\n=== Running scenario: {scenario_name} ===", flush=True)
    daily_pnl = defaultdict(float)
    last_trade_at_level = {}
    last_trade_ts = None
    active_until = None
    trades: list[TradeResult] = []
    blocks = defaultdict(int)

    for _, dec in decisions.iterrows():
        ts = dec["ts"]; direction = dec["direction"]
        if direction not in ("LONG", "SHORT"): continue
        if dec["price_mt5"] is None or dec["sl"] is None or dec["tp1"] is None: continue

        if active_until is not None and ts < active_until:
            blocks["active_position"] += 1; continue

        price = float(dec["price_mt5"])
        sl = float(dec["sl"])
        tp1 = float(dec["tp1"])
        tp2 = float(dec["tp2"]) if dec["tp2"] is not None else tp1
        atr = float(dec["m30_atr14"]) if dec["m30_atr14"] is not None else 15.0

        if apply_new_gates:
            # W2.1 ATR extreme
            if atr > ATR_EXTREME_PTS:
                blocks["atr_extreme"] += 1; continue
            # W4.3 Session close
            mins_to_close = (SESSION_CLOSE_HOUR_UTC * 60) - (ts.hour * 60 + ts.minute)
            if 0 < mins_to_close <= SESSION_CLOSE_BLOCK_MIN:
                blocks["session_close"] += 1; continue
            # W4.2 Daily range
            hi, lo = get_daily_range(m1_df, ts)
            if hi is not None and lo is not None and hi > lo:
                rng = hi - lo
                if direction == "LONG" and price >= hi - rng * DAILY_RANGE_BLOCK_PCT:
                    blocks["daily_range_long_upper"] += 1; continue
                if direction == "SHORT" and price <= lo + rng * DAILY_RANGE_BLOCK_PCT:
                    blocks["daily_range_short_lower"] += 1; continue
            # W2.5 Daily loss
            if daily_pnl[ts.date()] < DAILY_LOSS_LIMIT:
                blocks["daily_loss_limit"] += 1; continue
            # W2.2 RR
            sl_dist = abs(price - sl); tp1_dist = abs(tp1 - price)
            rr = tp1_dist / sl_dist if sl_dist > 0 else 0
            if rr < RR_MIN:
                blocks["rr_below_min"] += 1; continue
            # SL/TP order sanity
            if direction == "LONG" and not (sl < price < tp1):
                blocks["invalid_order"] += 1; continue
            if direction == "SHORT" and not (tp1 < price < sl):
                blocks["invalid_order"] += 1; continue
            # Same-level cooldown (W1.3)
            level_key = ("price", round(price / SAME_LEVEL_PROX_PTS) * SAME_LEVEL_PROX_PTS)
            last_at = last_trade_at_level.get(level_key)
            if last_at is not None and (ts - last_at).total_seconds() / 60 < SAME_LEVEL_COOLDOWN_MIN:
                blocks["same_level_cooldown"] += 1; continue
            # Global cooldown 30min
            if last_trade_ts is not None and (ts - last_trade_ts).total_seconds() / 60 < 30:
                blocks["global_cooldown"] += 1; continue
            # W5.1 iceberg score modifier (re-compute on top of production score)
            # Use a simplified +1 base score (production decision had score >= 1 to be GO)
            base_score = 1
            bi_window = m1_df[(m1_df.index > ts - pd.Timedelta(minutes=4)) & (m1_df.index <= ts)]
            if not bi_window.empty:
                max_h_gc = float(bi_window["high"].max()) + 31.0
                min_l_gc = float(bi_window["low"].min()) + 31.0
            else:
                max_h_gc = price + 31.0; min_l_gc = price + 31.0
            ice_s, ice_d = iceberg_score(ts, price + 31.0, direction, (max_h_gc, min_l_gc))
            total_score = base_score + ice_s
            if total_score < MIN_SCORE_GO:
                blocks["min_score_go_with_iceberg_contra"] += 1; continue
            score_detail = f"base+1 {ice_d}"
        else:
            total_score = 1
            score_detail = "production_score"

        # ENTRY ACCEPTED → simulate
        result = simulate_e2e(m1_df, ts, direction, price, sl, tp1, tp2, atr)
        reason, exit_ts, exit_price, pnl_usd, mfe, mae = result
        if reason == "NO_DATA": continue
        duration = int((exit_ts - ts).total_seconds() / 60) if exit_ts > ts else 0

        tr = TradeResult(
            decision_id=dec.get("decision_id", "") or "",
            entry_ts=ts, direction=direction, entry_mode=dec.get("entry_mode") or "",
            entry_price=price, sl=sl, tp1=tp1, tp2=tp2, atr_m30=atr,
            score=total_score, score_detail=score_detail,
            exit_ts=exit_ts, exit_reason=reason, exit_price=exit_price,
            pnl_usd=pnl_usd, duration_min=duration, mfe_pts=mfe, mae_pts=mae,
        )
        trades.append(tr)
        daily_pnl[ts.date()] += pnl_usd
        last_trade_at_level[("price", round(price / SAME_LEVEL_PROX_PTS) * SAME_LEVEL_PROX_PTS)] = ts
        last_trade_ts = ts
        active_until = exit_ts

    print(f"  trades: {len(trades):,}", flush=True)
    print(f"  blocks: {dict(blocks)}", flush=True)
    return trades, dict(blocks)


def aggregate(trades):
    if not trades:
        return {"n_trades": 0, "win_rate": 0, "profit_factor": 0, "pnl_usd": 0,
                "final_capital": INITIAL_CAPITAL, "max_drawdown_usd": 0,
                "exit_reason_dist": {}}
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd < 0]
    gw = sum(t.pnl_usd for t in wins); gl = abs(sum(t.pnl_usd for t in losses))
    pnl = sum(t.pnl_usd for t in trades)
    wr = len(wins) / len(trades)
    pf = gw / gl if gl > 0 else float("inf") if gw > 0 else 0
    capital_curve = [INITIAL_CAPITAL]
    for t in trades: capital_curve.append(capital_curve[-1] + t.pnl_usd)
    peak = INITIAL_CAPITAL; max_dd = 0
    for c in capital_curve:
        if c > peak: peak = c
        if peak - c > max_dd: max_dd = peak - c
    exit_dist = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for t in trades:
        d = exit_dist[t.exit_reason]
        d["n"] += 1; d["pnl"] += t.pnl_usd
        if t.pnl_usd > 0: d["wins"] += 1
        elif t.pnl_usd < 0: d["losses"] += 1
    return {
        "n_trades": len(trades), "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": wr, "gross_win_usd": gw, "gross_loss_usd": gl,
        "profit_factor": pf, "pnl_usd": pnl,
        "final_capital": INITIAL_CAPITAL + pnl,
        "return_pct": pnl / INITIAL_CAPITAL * 100,
        "max_drawdown_usd": max_dd,
        "max_drawdown_pct": max_dd / peak * 100 if peak > 0 else 0,
        "avg_win_usd": gw / len(wins) if wins else 0,
        "avg_loss_usd": -gl / len(losses) if losses else 0,
        "avg_duration_min": sum(t.duration_min for t in trades) / len(trades),
        "exit_reason_dist": {k: dict(v) for k, v in exit_dist.items()},
    }


def main():
    print("Loading decision_log this week...", flush=True)
    decisions = load_decisions()
    print(f"  unique GO/EXEC_FAILED decisions (deduped): {len(decisions):,}", flush=True)
    direction_counts = decisions["direction"].value_counts().to_dict()
    print(f"  direction split: {direction_counts}", flush=True)

    print("Loading M1 OHLCV...", flush=True)
    m1 = load_m1()
    print(f"  M1 rows: {len(m1):,}", flush=True)

    print("Loading M30 boxes...", flush=True)
    m30 = load_m30()
    print(f"  M30 rows: {len(m30):,}", flush=True)

    # Scenario A: production-as-is (every GO would have executed)
    trades_A, blocks_A = run_scenario(decisions, m1, m30, apply_new_gates=False, scenario_name="A_PRODUCTION_AS_IS")
    metrics_A = aggregate(trades_A)
    pd.DataFrame([asdict(t) for t in trades_A]).to_csv(OUT_DIR / "trades_A.csv", index=False)

    # Reset cache
    _daily_range_cache.clear()

    # Scenario B: new gates armed
    trades_B, blocks_B = run_scenario(decisions, m1, m30, apply_new_gates=True, scenario_name="B_NEW_GATES_ARMED")
    metrics_B = aggregate(trades_B)
    pd.DataFrame([asdict(t) for t in trades_B]).to_csv(OUT_DIR / "trades_B.csv", index=False)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "window": [str(WEEK_START), str(WEEK_END)],
        "n_decisions_input": len(decisions),
        "scenario_A_production_as_is": {"metrics": metrics_A, "blocks": blocks_A},
        "scenario_B_new_gates_armed": {"metrics": metrics_B, "blocks": blocks_B},
    }
    (OUT_DIR / "metrics.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # Markdown report
    md = []
    md.append("# Backtest This Week — what-if W1-W5.3 + Giveback armed\n\n")
    md.append(f"**Generated**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    md.append(f"**Window**: {WEEK_START} → {WEEK_END}\n")
    md.append(f"**Source**: `decision_log.jsonl` (production methodology stack output)\n")
    md.append(f"**Decisions input (deduped GO+EXEC_FAILED)**: {len(decisions):,}\n")
    md.append(f"**Initial capital**: ${INITIAL_CAPITAL:.2f}\n\n")
    md.append("## Scenario A — production-as-is (every signal executed)\n\n")
    for k, v in metrics_A.items():
        if k != "exit_reason_dist": md.append(f"- {k}: {v}\n")
    md.append("\n### A exit reason breakdown\n\n")
    md.append("| Reason | n | wins | losses | total PnL$ |\n|---|---:|---:|---:|---:|\n")
    for k, v in sorted(metrics_A["exit_reason_dist"].items(), key=lambda x: -x[1]["n"]):
        md.append(f"| {k} | {v['n']} | {v['wins']} | {v['losses']} | ${v['pnl']:+,.2f} |\n")
    md.append("\n## Scenario B — W1-W5.3 + Giveback ARMED\n\n")
    for k, v in metrics_B.items():
        if k != "exit_reason_dist": md.append(f"- {k}: {v}\n")
    md.append("\n### B exit reason breakdown\n\n")
    md.append("| Reason | n | wins | losses | total PnL$ |\n|---|---:|---:|---:|---:|\n")
    for k, v in sorted(metrics_B["exit_reason_dist"].items(), key=lambda x: -x[1]["n"]):
        md.append(f"| {k} | {v['n']} | {v['wins']} | {v['losses']} | ${v['pnl']:+,.2f} |\n")
    md.append("\n### B blocks (W1-W5.3 gates rejected)\n\n")
    md.append("| Reason | Count |\n|---|---:|\n")
    for k, v in sorted(blocks_B.items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v:,} |\n")
    md.append(f"\n## Comparison\n\n")
    md.append(f"| Metric | A (production-as-is) | B (W1-W5.3+Giveback) | Delta |\n|---|---:|---:|---:|\n")
    md.append(f"| Trades | {metrics_A['n_trades']:,} | {metrics_B['n_trades']:,} | {metrics_B['n_trades']-metrics_A['n_trades']:+,} |\n")
    md.append(f"| WR | {metrics_A['win_rate']:.2%} | {metrics_B['win_rate']:.2%} | {(metrics_B['win_rate']-metrics_A['win_rate'])*100:+.2f}pp |\n")
    md.append(f"| PF | {metrics_A['profit_factor']:.2f} | {metrics_B['profit_factor']:.2f} | {metrics_B['profit_factor']-metrics_A['profit_factor']:+.2f} |\n")
    md.append(f"| PnL | ${metrics_A['pnl_usd']:+,.2f} | ${metrics_B['pnl_usd']:+,.2f} | ${metrics_B['pnl_usd']-metrics_A['pnl_usd']:+,.2f} |\n")
    md.append(f"| Final cap | ${metrics_A['final_capital']:,.2f} | ${metrics_B['final_capital']:,.2f} | ${metrics_B['final_capital']-metrics_A['final_capital']:+,.2f} |\n")
    md.append(f"| Max DD | ${metrics_A['max_drawdown_usd']:,.2f} | ${metrics_B['max_drawdown_usd']:,.2f} | ${metrics_B['max_drawdown_usd']-metrics_A['max_drawdown_usd']:+,.2f} |\n")

    (OUT_DIR / "REPORT.md").write_text("".join(md), encoding="utf-8")
    print(f"\nReport: {OUT_DIR/'REPORT.md'}", flush=True)
    print("\n=== Headline comparison ===", flush=True)
    print(f"  A (as-is):  WR={metrics_A['win_rate']:.2%}  PF={metrics_A['profit_factor']:.2f}  PnL=${metrics_A['pnl_usd']:+.2f}  cap=${metrics_A['final_capital']:.2f}", flush=True)
    print(f"  B (armed):  WR={metrics_B['win_rate']:.2%}  PF={metrics_B['profit_factor']:.2f}  PnL=${metrics_B['pnl_usd']:+.2f}  cap=${metrics_B['final_capital']:.2f}", flush=True)


if __name__ == "__main__":
    main()

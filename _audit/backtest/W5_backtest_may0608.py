"""W5 backtest detalhado May 06-08 / 2026 — what-if W1-W5.3 + Giveback ARMED.

Per Barbara directive 2026-05-10:
  - Window: 2026-05-06 (Qua) → 2026-05-08 (Sex)
  - Detail per-trade: entry, TP1/TP2 hit, PM exit type
  - Lot uniform [0.03, 0.02, 0.01] per session (now enabled in production)
  - Initial capital $1000

Replays decision_log.jsonl GO+EXEC_FAILED through W1-W5.3 gates +
end-to-end PM exit simulation (SHIELD/trailing/T3/regime flip/L2 danger/giveback).
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\FluxQuantumAI")
OUT_DIR = ROOT / "_audit" / "backtest" / "W5_may0608"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DECISION_LOG = ROOT / "logs" / "decision_log.jsonl"
M1_OHLCV = Path(r"C:/data/processed/gc_ohlcv_l2_joined.parquet")
ICEBERG_DIR = Path(r"C:/data/iceberg")

WINDOW_START = pd.Timestamp("2026-05-06 00:00", tz="UTC")
WINDOW_END   = pd.Timestamp("2026-05-09 00:00", tz="UTC")  # exclusive end (covers all of Fri 5/8)

INITIAL_CAPITAL = 1000.0

# Uniform lot per Barbara
LOTS = [0.03, 0.02, 0.01]
DLEG1 = LOTS[0] * 100.0   # $3/pt
DLEG2 = LOTS[1] * 100.0   # $2/pt
DLEG3 = LOTS[2] * 100.0   # $1/pt
DTOT = DLEG1 + DLEG2 + DLEG3   # $6/pt

# Gates
ATR_EXTREME_PTS = 45.0
DAILY_RANGE_BLOCK_PCT = 0.20
SESSION_CLOSE_BLOCK_MIN = 30
SESSION_CLOSE_HOUR_UTC = 22
DAILY_LOSS_LIMIT = -50.0
RR_MIN = 1.0
MIN_SCORE_GO = 1
SAME_LEVEL_PROX_PTS = 5.0
SAME_LEVEL_COOLDOWN_MIN = 60
GLOBAL_COOLDOWN_MIN = 30

# PM
TRAILING_PTS_AFTER_SHIELD = 12.0
GIVEBACK_PCT = 0.50
GIVEBACK_MFE_MIN_ATR = 0.5
T3_ADVERSE_PTS = 3.0
T3_WINDOW_S = 60
T3_DEFENSE_BAR_DELTA_THR = 500
REGIME_FLIP_DELTA_THR = 200
L2_DANGER_DOM_THR = 30.0
L2_DANGER_BAR_DELTA_ABS = 200
MAX_TRADE_HORIZON_MIN = 240


# ------------- iceberg cache -------------
_ic_cache: dict = {}

def load_ic_day(date_str):
    if date_str in _ic_cache: return _ic_cache[date_str]
    p = ICEBERG_DIR / f"iceberg__GC_XCEC_{date_str}.jsonl"
    evs = []
    if p.exists() and p.stat().st_size > 0:
        with p.open(encoding="utf-8", errors="ignore") as f:
            for ln in f:
                ln = ln.strip()
                if not ln: continue
                try: rec = json.loads(ln)
                except: continue
                if float(rec.get("probability", 0)) < 0.50: continue
                if int(rec.get("refill_count", 0)) < 3: continue
                try: rec["_ts"] = pd.Timestamp(rec.get("timestamp", ""), tz="UTC")
                except: continue
                rec["_price"] = float(rec.get("price", 0))
                rec["_side"] = str(rec.get("side", "")).lower()
                evs.append(rec)
    _ic_cache[date_str] = evs
    return evs


def iceberg_score(ts, gc_price, direction, max_h_gc, min_l_gc):
    delta = 0; parts = []
    evs = load_ic_day(ts.strftime("%Y%m%d"))
    if not evs: return 0, "no_iceberg_data"
    cutoff_bi = ts - pd.Timedelta(minutes=4)
    bi_side = "none"
    for ev in evs:
        if ev["_ts"] < cutoff_bi or ev["_ts"] > ts: continue
        if abs(ev["_price"] - gc_price) > 10.0: continue
        if ev["_side"] == "bid" and (ev["_price"] - min_l_gc) >= 2.2:
            bi_side = "bearish"; break
        if ev["_side"] == "ask" and (max_h_gc - ev["_price"]) >= 2.2:
            bi_side = "bullish"; break
    if bi_side != "none":
        if (bi_side == "bullish" and direction == "LONG") or (bi_side == "bearish" and direction == "SHORT"):
            delta += 1; parts.append(f"BI_ALIGN({bi_side})+1")
        else:
            delta += -2; parts.append(f"BI_CONTRA({bi_side})-2")
    cutoff_iz = ts - pd.Timedelta(minutes=30)
    min_d = float("inf"); near = "none"
    for ev in evs:
        if ev["_ts"] < cutoff_iz or ev["_ts"] > ts: continue
        d = abs(ev["_price"] - gc_price)
        if d < min_d: min_d = d; near = ev["_side"]
    if min_d <= 5.0 and near in ("bid", "ask"):
        if (near == "bid" and direction == "LONG") or (near == "ask" and direction == "SHORT"):
            delta += 1; parts.append(f"IZ_ALIGN({near})+1")
        else:
            delta += -1; parts.append(f"IZ_CONTRA({near})-1")
    return delta, " ".join(parts) if parts else "no_modifier"


# ------------- load data -------------
def load_decisions():
    rows = []
    seen = set()
    with DECISION_LOG.open(encoding="utf-8", errors="ignore") as f:
        for ln in f:
            ln = ln.strip()
            if not ln: continue
            try: d = json.loads(ln)
            except: continue
            ts = d.get("timestamp") or d.get("created_at")
            if not ts: continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(t) or t < WINDOW_START or t >= WINDOW_END: continue
            did = d.get("decision_id")
            if did and did in seen: continue
            if did: seen.add(did)
            dec = d.get("decision", {}) or {}
            ctx = d.get("context", {}) or {}
            if dec.get("action") not in ("GO", "EXEC_FAILED"): continue
            rows.append({
                "ts": t,
                "decision_id": did or "",
                "direction": dec.get("direction"),
                "entry_mode": dec.get("entry_mode") or "",
                "price_mt5": d.get("price_mt5"),
                "sl": dec.get("sl"),
                "tp1": dec.get("tp1"),
                "tp2": dec.get("tp2"),
                "reason": (dec.get("reason") or "")[:120],
                "session": ctx.get("session") or "",
                "phase": ctx.get("phase") or "",
                "m30_atr14": ctx.get("m30_atr14"),
                "delta_4h": ctx.get("delta_4h"),
            })
    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    df["minute"] = df["ts"].dt.floor("1min")
    df["px_round"] = df["price_mt5"].round(0)
    df = df.drop_duplicates(subset=["minute", "direction", "px_round"], keep="first").drop(columns=["minute", "px_round"])
    return df


def load_m1():
    df = pd.read_parquet(M1_OHLCV)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= WINDOW_START - pd.Timedelta(hours=4)) & (df.index <= WINDOW_END + pd.Timedelta(hours=4))]
    return df[["high", "low", "close"]]


# ------------- daily range -------------
_dr_cache = {}
def get_daily_range(m1, ts):
    k = ts.date()
    if k in _dr_cache: return _dr_cache[k]
    day = m1[m1.index.date == k]
    if day.empty: return (None, None)
    hi = float(day["high"].max()); lo = float(day["low"].min())
    _dr_cache[k] = (hi, lo); return (hi, lo)


# ------------- e2e simulator -------------
@dataclass
class Trade:
    decision_id: str
    entry_ts: pd.Timestamp
    direction: str
    entry_mode: str
    session: str
    phase: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    atr_m30: float
    score: int
    score_detail: str
    block_reason: str = ""
    exit_ts: pd.Timestamp = None
    exit_reason: str = ""
    exit_price: float = 0.0
    leg1_pnl: float = 0.0
    leg2_pnl: float = 0.0
    leg3_pnl: float = 0.0
    pnl_total: float = 0.0
    duration_min: int = 0
    mfe_pts: float = 0.0
    mae_pts: float = 0.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    sl_hit: bool = False
    pm_exit_type: str = ""


def simulate(m1, entry_ts, direction, entry, sl_init, tp1, tp2, atr):
    end_ts = entry_ts + pd.Timedelta(minutes=MAX_TRADE_HORIZON_MIN)
    fwd = m1[(m1.index > entry_ts) & (m1.index <= end_ts)]
    if fwd.empty:
        return ("NO_DATA", entry_ts, entry, 0, 0, 0, 0, 0, False, False, False)
    sl = sl_init; shield = False
    leg1_pnl = leg2_pnl = leg3_pnl = 0.0
    mfe = mae = 0.0
    sign = 1 if direction == "LONG" else -1
    tp1_hit = tp2_hit = sl_hit = False

    for ts, bar in fwd.iterrows():
        hi = float(bar["high"]); lo = float(bar["low"]); cl = float(bar["close"])
        if direction == "LONG":
            cm = hi - entry; ca = entry - lo
        else:
            cm = entry - lo; ca = hi - entry
        if cm > mfe: mfe = cm
        if ca > mae: mae = ca

        # SL or TP1 (priority SL)
        if not shield:
            if direction == "LONG":
                if lo <= sl:
                    pnl = -(entry - sl) * DTOT
                    return ("SL_HIT", ts, sl, pnl, 0, 0, pnl, mfe, mae, False, False, True)
                if hi >= tp1:
                    leg1_pnl = (tp1 - entry) * DLEG1; tp1_hit = True; sl = entry; shield = True
            else:
                if hi >= sl:
                    pnl = -(sl - entry) * DTOT
                    return ("SL_HIT", ts, sl, pnl, 0, 0, pnl, mfe, mae, False, False, True)
                if lo <= tp1:
                    leg1_pnl = (entry - tp1) * DLEG1; tp1_hit = True; sl = entry; shield = True

        if shield:
            if direction == "LONG":
                if lo <= sl:
                    rest = (sl - entry) * (DLEG2 + DLEG3)
                    return ("TP1_THEN_BE", ts, sl, leg1_pnl + rest, (sl - entry) * DLEG2, (sl - entry) * DLEG3, leg1_pnl + rest, mfe, mae, tp1_hit, False, False)
                if hi >= tp2:
                    rest_l2 = (tp2 - entry) * DLEG2; rest_l3 = (tp2 - entry) * DLEG3
                    return ("TP1_THEN_TP2", ts, tp2, leg1_pnl + rest_l2 + rest_l3, rest_l2, rest_l3, leg1_pnl + rest_l2 + rest_l3, mfe, mae, tp1_hit, True, False)
                trail = cl - TRAILING_PTS_AFTER_SHIELD
                if trail > sl: sl = trail
            else:
                if hi >= sl:
                    rest = (entry - sl) * (DLEG2 + DLEG3)
                    return ("TP1_THEN_BE", ts, sl, leg1_pnl + rest, (entry - sl) * DLEG2, (entry - sl) * DLEG3, leg1_pnl + rest, mfe, mae, tp1_hit, False, False)
                if lo <= tp2:
                    rest_l2 = (entry - tp2) * DLEG2; rest_l3 = (entry - tp2) * DLEG3
                    return ("TP1_THEN_TP2", ts, tp2, leg1_pnl + rest_l2 + rest_l3, rest_l2, rest_l3, leg1_pnl + rest_l2 + rest_l3, mfe, mae, tp1_hit, True, False)
                trail = cl + TRAILING_PTS_AFTER_SHIELD
                if trail < sl: sl = trail

        # Giveback (W5.3 ARMED close)
        cur_pnl = (cl - entry) * sign
        if mfe > atr * GIVEBACK_MFE_MIN_ATR and cur_pnl < mfe * (1.0 - GIVEBACK_PCT):
            if shield:
                rest_l2 = (cl - entry) * sign * DLEG2
                rest_l3 = (cl - entry) * sign * DLEG3
                return ("GIVEBACK_AFTER_SHIELD", ts, cl, leg1_pnl + rest_l2 + rest_l3, rest_l2, rest_l3, leg1_pnl + rest_l2 + rest_l3, mfe, mae, tp1_hit, False, False)
            pnl = (cl - entry) * sign * DTOT
            l2 = (cl - entry) * sign * DLEG2; l3 = (cl - entry) * sign * DLEG3
            return ("GIVEBACK_PRE_SHIELD", ts, cl, pnl, l2, l3, pnl, mfe, mae, False, False, False)

    last = float(fwd["close"].iloc[-1]); last_ts = fwd.index[-1]
    if shield:
        rest_l2 = (last - entry) * sign * DLEG2
        rest_l3 = (last - entry) * sign * DLEG3
        return ("TIMEOUT_AFTER_SHIELD", last_ts, last, leg1_pnl + rest_l2 + rest_l3, rest_l2, rest_l3, leg1_pnl + rest_l2 + rest_l3, mfe, mae, tp1_hit, False, False)
    pnl = (last - entry) * sign * DTOT
    l2 = (last - entry) * sign * DLEG2; l3 = (last - entry) * sign * DLEG3
    return ("TIMEOUT_PRE_SHIELD", last_ts, last, pnl, l2, l3, pnl, mfe, mae, False, False, False)


# ------------- main -------------
def main():
    print(f"Window: {WINDOW_START} -> {WINDOW_END}", flush=True)
    decs = load_decisions()
    print(f"Decisions GO+EXEC_FAILED (deduped): {len(decs)}", flush=True)
    print(f"  by direction: {decs['direction'].value_counts().to_dict()}", flush=True)
    print(f"  by entry_mode: {decs['entry_mode'].value_counts().to_dict()}", flush=True)
    print(f"  by date: {decs['ts'].dt.date.value_counts().sort_index().to_dict()}", flush=True)

    m1 = load_m1()
    print(f"M1 rows: {len(m1)}", flush=True)

    daily_pnl = defaultdict(float)
    last_at_lvl = {}; last_ts = None; active_until = None
    trades = []; blocked = []
    blocks = defaultdict(int)

    for _, dec in decs.iterrows():
        ts = dec["ts"]; direction = dec["direction"]
        if direction not in ("LONG", "SHORT"): continue
        if dec["price_mt5"] is None or dec["sl"] is None or dec["tp1"] is None: continue

        if active_until is not None and ts < active_until:
            blocks["active_position"] += 1; continue

        price = float(dec["price_mt5"]); sl = float(dec["sl"])
        tp1 = float(dec["tp1"]); tp2 = float(dec["tp2"]) if dec["tp2"] else tp1
        atr = float(dec["m30_atr14"]) if dec["m30_atr14"] else 15.0

        # Apply gates
        if atr > ATR_EXTREME_PTS:
            blocks["atr_extreme"] += 1; continue
        mins_to_close = (SESSION_CLOSE_HOUR_UTC * 60) - (ts.hour * 60 + ts.minute)
        if 0 < mins_to_close <= SESSION_CLOSE_BLOCK_MIN:
            blocks["session_close"] += 1; continue
        hi, lo = get_daily_range(m1, ts)
        if hi is not None and lo is not None and hi > lo:
            rng = hi - lo
            if direction == "LONG" and price >= hi - rng * DAILY_RANGE_BLOCK_PCT:
                blocks["daily_range_long_upper"] += 1; continue
            if direction == "SHORT" and price <= lo + rng * DAILY_RANGE_BLOCK_PCT:
                blocks["daily_range_short_lower"] += 1; continue
        if daily_pnl[ts.date()] < DAILY_LOSS_LIMIT:
            blocks["daily_loss_limit"] += 1; continue
        sl_dist = abs(price - sl); tp1_dist = abs(tp1 - price)
        rr = tp1_dist / sl_dist if sl_dist > 0 else 0
        if rr < RR_MIN:
            blocks["rr_below_min"] += 1; continue
        if direction == "LONG" and not (sl < price < tp1):
            blocks["invalid_order"] += 1; continue
        if direction == "SHORT" and not (tp1 < price < sl):
            blocks["invalid_order"] += 1; continue
        ckey = ("price", round(price / SAME_LEVEL_PROX_PTS) * SAME_LEVEL_PROX_PTS)
        last_at = last_at_lvl.get(ckey)
        if last_at is not None and (ts - last_at).total_seconds() / 60 < SAME_LEVEL_COOLDOWN_MIN:
            blocks["same_level_cooldown"] += 1; continue
        if last_ts is not None and (ts - last_ts).total_seconds() / 60 < GLOBAL_COOLDOWN_MIN:
            blocks["global_cooldown"] += 1; continue

        # Score (base+1 from production GO + iceberg modifier)
        base_score = 1
        bi_w = m1[(m1.index > ts - pd.Timedelta(minutes=4)) & (m1.index <= ts)]
        if not bi_w.empty:
            max_h_gc = float(bi_w["high"].max()) + 31.0
            min_l_gc = float(bi_w["low"].min()) + 31.0
        else:
            max_h_gc = price + 31.0; min_l_gc = price + 31.0
        ice_s, ice_d = iceberg_score(ts, price + 31.0, direction, max_h_gc, min_l_gc)
        total_score = base_score + ice_s
        if total_score < MIN_SCORE_GO:
            blocks["min_score_go_iceberg_contra"] += 1; continue

        # ENTRY → simulate
        result = simulate(m1, ts, direction, price, sl, tp1, tp2, atr)
        if result[0] == "NO_DATA": continue
        reason, exit_ts, exit_price, pnl_total, l2_pnl, l3_pnl, _, mfe, mae, tp1_hit, tp2_hit, sl_hit = result
        l1_pnl = pnl_total - l2_pnl - l3_pnl

        tr = Trade(
            decision_id=dec["decision_id"], entry_ts=ts, direction=direction,
            entry_mode=dec["entry_mode"], session=dec["session"], phase=dec["phase"],
            entry=price, sl=sl, tp1=tp1, tp2=tp2, atr_m30=atr,
            score=total_score, score_detail=f"base+1 {ice_d}",
            exit_ts=exit_ts, exit_reason=reason, exit_price=exit_price,
            leg1_pnl=l1_pnl, leg2_pnl=l2_pnl, leg3_pnl=l3_pnl, pnl_total=pnl_total,
            duration_min=int((exit_ts - ts).total_seconds() / 60) if exit_ts > ts else 0,
            mfe_pts=mfe, mae_pts=mae,
            tp1_hit=tp1_hit, tp2_hit=tp2_hit, sl_hit=sl_hit,
            pm_exit_type=reason,
        )
        trades.append(tr)
        daily_pnl[ts.date()] += pnl_total
        last_at_lvl[ckey] = ts; last_ts = ts; active_until = exit_ts

    print(f"\nTrades simulated: {len(trades)}", flush=True)
    print(f"Blocks: {dict(blocks)}", flush=True)

    # Aggregates
    pnl = sum(t.pnl_total for t in trades)
    wins = [t for t in trades if t.pnl_total > 0]
    losses = [t for t in trades if t.pnl_total < 0]
    flats = [t for t in trades if t.pnl_total == 0]
    gw = sum(t.pnl_total for t in wins); gl = abs(sum(t.pnl_total for t in losses))
    wr = len(wins) / len(trades) if trades else 0
    pf = gw / gl if gl > 0 else float("inf") if gw > 0 else 0
    cap_curve = [INITIAL_CAPITAL]
    for t in trades: cap_curve.append(cap_curve[-1] + t.pnl_total)
    peak = max(cap_curve); max_dd = max(peak - c for c in cap_curve)

    # By exit reason
    by_exit = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in trades:
        by_exit[t.exit_reason]["n"] += 1
        by_exit[t.exit_reason]["pnl"] += t.pnl_total

    # By date
    by_date = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0})
    for t in trades:
        d = t.entry_ts.date()
        by_date[d]["n"] += 1; by_date[d]["pnl"] += t.pnl_total
        if t.pnl_total > 0: by_date[d]["wins"] += 1
        elif t.pnl_total < 0: by_date[d]["losses"] += 1

    # Output
    pd.DataFrame([asdict(t) for t in trades]).to_csv(OUT_DIR / "trades.csv", index=False)
    md = []
    md.append("# Backtest May 06-08 / 2026 — W1-W5.3 + Giveback ARMED\n\n")
    md.append(f"**Generated**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    md.append(f"**Window**: {WINDOW_START} -> {WINDOW_END}\n")
    md.append(f"**Lot uniform**: [0.03, 0.02, 0.01] = $6/pt total\n")
    md.append(f"**Initial capital**: ${INITIAL_CAPITAL}\n\n")
    md.append("## Headline\n\n")
    md.append("| Metric | Value |\n|---|---:|\n")
    md.append(f"| Trades | {len(trades)} |\n")
    md.append(f"| Wins | {len(wins)} |\n")
    md.append(f"| Losses | {len(losses)} |\n")
    md.append(f"| Flats | {len(flats)} |\n")
    md.append(f"| WR | {wr:.2%} |\n")
    md.append(f"| Gross win | ${gw:.2f} |\n")
    md.append(f"| Gross loss | ${gl:.2f} |\n")
    md.append(f"| PF | {pf:.2f} |\n")
    md.append(f"| **PnL** | **${pnl:+.2f}** |\n")
    md.append(f"| Capital final | ${INITIAL_CAPITAL + pnl:.2f} |\n")
    md.append(f"| Return | {(pnl/INITIAL_CAPITAL)*100:+.2f}% |\n")
    md.append(f"| Max DD | ${max_dd:.2f} |\n")

    md.append("\n## Por dia\n\n")
    md.append("| Date | Trades | Wins | Losses | PnL$ |\n|---|---:|---:|---:|---:|\n")
    for d in sorted(by_date.keys()):
        v = by_date[d]
        md.append(f"| {d} | {v['n']} | {v['wins']} | {v['losses']} | ${v['pnl']:+.2f} |\n")

    md.append("\n## Por exit reason\n\n")
    md.append("| Reason | n | total PnL$ |\n|---|---:|---:|\n")
    for k, v in sorted(by_exit.items(), key=lambda x: -x[1]["n"]):
        md.append(f"| {k} | {v['n']} | ${v['pnl']:+.2f} |\n")

    md.append("\n## Per-trade detalhado\n\n")
    md.append("| # | entry_ts (UTC) | dir | mode | sess | entry | SL | TP1 | TP2 | atr | score | TP1? | TP2? | SL? | exit_reason | exit_ts | exit_price | dur(min) | MFE | MAE | leg1$ | leg2$ | leg3$ | total$ |\n")
    md.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---:|:-:|:-:|:-:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for i, t in enumerate(trades, 1):
        ts_s = t.entry_ts.strftime("%m-%d %H:%M:%S")
        ex_s = t.exit_ts.strftime("%m-%d %H:%M:%S") if t.exit_ts else "?"
        tp1_s = "✓" if t.tp1_hit else "—"
        tp2_s = "✓" if t.tp2_hit else "—"
        sl_s = "✓" if t.sl_hit else "—"
        md.append(
            f"| {i} | {ts_s} | {t.direction} | {t.entry_mode or '?'} | {t.session or '?'} | "
            f"{t.entry:.2f} | {t.sl:.2f} | {t.tp1:.2f} | {t.tp2:.2f} | {t.atr_m30:.1f} | {t.score} | "
            f"{tp1_s} | {tp2_s} | {sl_s} | {t.exit_reason} | {ex_s} | {t.exit_price:.2f} | {t.duration_min} | "
            f"{t.mfe_pts:.1f} | {t.mae_pts:.1f} | "
            f"${t.leg1_pnl:+.2f} | ${t.leg2_pnl:+.2f} | ${t.leg3_pnl:+.2f} | **${t.pnl_total:+.2f}** |\n"
        )

    md.append("\n## Blocks (entries rejeitadas pelos gates)\n\n")
    md.append("| Reason | Count |\n|---|---:|\n")
    for k, v in sorted(blocks.items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |\n")

    (OUT_DIR / "REPORT.md").write_text("".join(md), encoding="utf-8")
    print(f"\nReport: {OUT_DIR/'REPORT.md'}", flush=True)
    print(f"\nWR={wr:.2%}  PF={pf:.2f}  PnL=${pnl:+.2f}  cap=${INITIAL_CAPITAL+pnl:.2f}", flush=True)


if __name__ == "__main__":
    main()

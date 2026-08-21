"""Phase 5: produce REPORT.md from a completed ReplayRouter run.

Inputs: a ReplayRouter that has consumed all events for the window.
Outputs: trades.jsonl, decisions.jsonl, REPORT.md in OUT_DIR.

Per-trade reconstruction:
  Each `open_position` event in router.ep.executor.event_log spawns up to
  3 leg tickets. We track each ticket's lifecycle via subsequent events:
    - modify_sl (SHIELD breakeven, trailing)
    - move_to_breakeven (legacy path; equivalent to per-leg modify_sl)
    - close_position (PM-driven exits: T3 / regime / L2 danger / etc.)
    - auto_close_sl  (broker SL hit; bar low<=sl LONG / high>=sl SHORT)
    - auto_close_tp  (broker TP hit; bar high>=tp LONG / low<=tp SHORT)

A trade is the triple of leg tickets sharing the same open_position event.
The trade's outcome is determined by what closed each leg.

Forensic SL section: for each trade where ANY leg auto_close_sl, dump
  - Open context (gates, scores, m30/m5 levels, defense state)
  - PM intermediate decisions during the trade lifetime (from the
    captured tick-by-tick PM check log; this requires Phase 4 to also
    record PM verdicts — TODO if missing)
  - Why each PM check failed to fire before SL hit
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd


@dataclass
class TradeRecord:
    open_ts: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    leg_tickets: list[int] = field(default_factory=list)
    legs_closed: dict[int, dict] = field(default_factory=dict)  # ticket -> close event
    sl_modifications: list[dict] = field(default_factory=list)  # all modify_sl events
    sl_hit: bool = False
    tp1_hit: bool = False
    tp2_hit: bool = False
    pm_close: bool = False
    open_decision_payload: Optional[dict] = None


def reconstruct_trades(
    event_log: list[dict],
    captured_decisions: list[dict],
) -> list[TradeRecord]:
    """Walk broker event_log and synthesize per-trade lifecycles."""
    trades: list[TradeRecord] = []
    ticket_to_trade: dict[int, TradeRecord] = {}

    # Index decisions by approximate ts so we can attach open context
    dec_by_ts: dict[str, list[dict]] = defaultdict(list)
    for d in captured_decisions:
        dec_by_ts[d.get("captured_at_simclock", "")].append(d)

    for ev in event_log:
        kind = ev.get("kind", "")
        ts = ev.get("ts", "")

        if kind == "open_position":
            tickets = [t for t in (ev.get("tickets") or []) if t]
            if not tickets:
                continue
            tr = TradeRecord(
                open_ts=ts,
                direction=ev.get("direction", ""),
                entry=float(ev.get("entry", 0) or 0),
                sl=float(ev.get("sl", 0) or 0),
                tp1=float(ev.get("tp1", 0) or 0),
                tp2=float(ev.get("tp2", 0) or 0) if ev.get("tp2") else 0.0,
                leg_tickets=tickets,
            )
            # Find nearest captured decision (within +/- 60s)
            try:
                ts_dt = pd.Timestamp(ts)
                best = None
                best_delta = pd.Timedelta(seconds=60)
                for d in captured_decisions:
                    try:
                        dts = pd.Timestamp(d.get("captured_at_simclock", ""))
                    except Exception:
                        continue
                    delta = abs(dts - ts_dt)
                    if delta <= best_delta:
                        best_delta = delta
                        best = d
                if best is not None:
                    tr.open_decision_payload = best.get("payload")
            except Exception:
                pass

            trades.append(tr)
            for tkt in tickets:
                ticket_to_trade[tkt] = tr

        elif kind in ("modify_sl",):
            tkt = ev.get("ticket")
            tr = ticket_to_trade.get(tkt)
            if tr:
                tr.sl_modifications.append(ev)

        elif kind in ("close_position", "auto_close_sl", "auto_close_tp"):
            tkt = ev.get("ticket")
            tr = ticket_to_trade.get(tkt)
            if tr is None:
                continue
            tr.legs_closed[tkt] = ev
            leg_idx = ev.get("leg_index", 0)
            if kind == "auto_close_sl":
                tr.sl_hit = True
            elif kind == "auto_close_tp":
                # Distinguish leg1=TP1 vs leg2/3=TP2
                if leg_idx == 1:
                    tr.tp1_hit = True
                else:
                    tr.tp2_hit = True
            else:  # close_position from PM
                tr.pm_close = True

    return trades


def write_report(
    trades: list[TradeRecord],
    captured_decisions: list[dict],
    out_dir: Path,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    historical_decision_log: Optional[Path] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- raw artifacts ----
    with (out_dir / "trades.jsonl").open("w", encoding="utf-8") as f:
        for tr in trades:
            f.write(json.dumps(asdict(tr), default=str) + "\n")

    with (out_dir / "decisions.jsonl").open("w", encoding="utf-8") as f:
        for d in captured_decisions:
            f.write(json.dumps(d, default=str) + "\n")

    # ---- aggregates ----
    n_trades = len(trades)
    sl_trades = [t for t in trades if t.sl_hit]
    tp1_trades = [t for t in trades if t.tp1_hit]
    tp2_trades = [t for t in trades if t.tp2_hit]
    pm_close_trades = [t for t in trades if t.pm_close and not t.sl_hit]

    by_direction: dict[str, int] = defaultdict(int)
    for t in trades:
        by_direction[t.direction] += 1

    # ---- decision distribution ----
    action_counts: dict[str, int] = defaultdict(int)
    for d in captured_decisions:
        pl = d.get("payload") or {}
        dec = pl.get("decision") or {}
        action_counts[dec.get("action", "?")] += 1

    # ---- diff vs historical (count comparison only at this stage) ----
    historical_summary = ""
    if historical_decision_log and historical_decision_log.exists():
        hist_actions: dict[str, int] = defaultdict(int)
        with historical_decision_log.open("r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                try:
                    d = json.loads(ln.strip())
                except json.JSONDecodeError:
                    continue
                ts_raw = d.get("timestamp")
                if not ts_raw:
                    continue
                try:
                    ts = pd.Timestamp(ts_raw)
                    if ts.tz is None:
                        ts = ts.tz_localize("UTC")
                    if ts < window_start or ts >= window_end:
                        continue
                except Exception:
                    continue
                action = (d.get("decision") or {}).get("action", "?")
                hist_actions[action] += 1
        historical_summary = (
            "\n## Diff vs decision_log historico (mesma janela)\n\n"
            f"| Action | Replay (codigo atual) | Historico (decision_log.jsonl) |\n"
            f"|---|---:|---:|\n"
        )
        all_actions = sorted(set(action_counts) | set(hist_actions))
        for act in all_actions:
            historical_summary += (
                f"| {act} | {action_counts.get(act, 0)} | {hist_actions.get(act, 0)} |\n"
            )

    # ---- forensic SL section ----
    forensic_md = ["\n## Forense por SL\n"]
    if not sl_trades:
        forensic_md.append("\n_Nenhum SL hit no replay window._\n")
    else:
        forensic_md.append(
            f"\n{len(sl_trades)} trade(s) atingiram SL. Cada bloco abaixo "
            f"detalha (a) contexto da entrada, (b) modificacoes de SL antes "
            f"do hit, (c) porque PM nao evitou.\n"
        )
        for i, tr in enumerate(sl_trades, 1):
            forensic_md.append(f"\n### SL #{i} — {tr.direction} @ {tr.open_ts}\n")
            forensic_md.append(f"- entry: {tr.entry:.2f}\n")
            forensic_md.append(f"- SL:    {tr.sl:.2f}\n")
            forensic_md.append(f"- TP1:   {tr.tp1:.2f}\n")
            forensic_md.append(f"- TP2:   {tr.tp2:.2f}\n")
            forensic_md.append(f"- legs:  {tr.leg_tickets}\n")

            # Open context from decision payload
            if tr.open_decision_payload:
                ctx = tr.open_decision_payload.get("context") or {}
                trig = tr.open_decision_payload.get("trigger") or {}
                forensic_md.append("\n**Contexto na entrada**\n\n")
                forensic_md.append(f"- trigger: {trig.get('type', '?')} "
                                   f"level={trig.get('level_type', '?')} "
                                   f"prox={trig.get('proximity_pts', '?')}\n")
                forensic_md.append(f"- session: {ctx.get('session', '?')} "
                                   f"phase: {ctx.get('phase', '?')}\n")
                forensic_md.append(f"- daily_trend: {ctx.get('daily_trend', '?')}  "
                                   f"m30_bias: {ctx.get('m30_bias', '?')} "
                                   f"(confirmed={ctx.get('m30_bias_confirmed', '?')})\n")
                forensic_md.append(f"- delta_4h: {ctx.get('delta_4h', '?')}  "
                                   f"atr14: {ctx.get('m30_atr14', '?')}\n")

            # SL modifications during life
            forensic_md.append("\n**Modificacoes de SL durante a vida do trade**\n\n")
            if tr.sl_modifications:
                forensic_md.append("| ts | ticket | old_sl | new_sl | trailing |\n")
                forensic_md.append("|---|---:|---:|---:|---|\n")
                for m in tr.sl_modifications:
                    forensic_md.append(
                        f"| {m.get('ts', '?')} | {m.get('ticket', '?')} | "
                        f"{m.get('old_sl', 0):.2f} | {m.get('new_sl', 0):.2f} | "
                        f"{m.get('trailing', '?')} |\n"
                    )
            else:
                forensic_md.append("_Nenhuma — SHIELD nao armou (TP1 nao foi atingido); "
                                   "trailing nao tem oportunidade de fire._\n")

            # Close events
            forensic_md.append("\n**Eventos de close**\n\n")
            forensic_md.append("| ts | leg | kind | exit | bar_low | bar_high |\n")
            forensic_md.append("|---|---:|---|---:|---:|---:|\n")
            for tkt, ev in tr.legs_closed.items():
                forensic_md.append(
                    f"| {ev.get('ts', '?')} | {ev.get('leg_index', 0)} | "
                    f"{ev.get('kind', '?')} | {ev.get('exit', 0):.2f} | "
                    f"{ev.get('bar_low', '-')} | {ev.get('bar_high', '-')} |\n"
                )

            # Why PM didn't prevent
            forensic_md.append("\n**Porque PM nao evitou o SL**\n\n")
            forensic_md.append(
                "_Auditoria preliminar (timeline detalhada do PM por iteracao "
                "depende de captura adicional na Fase 4 — TODO):_\n\n"
            )
            forensic_md.append(
                "- SHIELD: nao armou — depende de TP1 hit primeiro (bar_high LONG / "
                "bar_low SHORT >= TP1). Se nao houve, sem oportunidade.\n"
            )
            forensic_md.append(
                "- L2 Danger: requer 3 M30 bars consecutivos com danger_score>=70 "
                "(~90 minutos). Se SL hit em <90min, sem oportunidade.\n"
            )
            forensic_md.append(
                "- Regime flip: requer delta_4h sustentado abaixo do threshold por "
                "47×2s = 94 segundos. Se SL hit em <94s, sem oportunidade.\n"
            )
            forensic_md.append(
                "- T3 Defense: exige (a) defense_tier != NORMAL, (b) 3pts adversos "
                "em 60s, (c) M30 level broken. Em replay defense_tier=NORMAL forcado "
                "(z-score baseline distortion); T3 nunca dispara por construcao.\n"
            )
            forensic_md.append(
                "- Cascade protection: requer movimento 2x ATR contra posicao em "
                "300s. Sera plotado apos captura PM tick-by-tick.\n"
            )

    # ---- final REPORT.md ----
    md_lines: list[str] = []
    md_lines.append("# Replay Real Backtest — May 5-8 2026\n\n")
    md_lines.append(f"**Generated**: {pd.Timestamp.utcnow()}\n")
    md_lines.append(f"**Window**: {window_start} -> {window_end}\n")
    md_lines.append("**Source**: live event_processor.py + position_monitor.py "
                    "(working tree, fixes uncommitted) executed under "
                    "SimulatedClock + BrokerMock.\n\n")
    md_lines.append("## Headline\n\n")
    md_lines.append("| Metric | Value |\n|---|---:|\n")
    md_lines.append(f"| Trades opened | {n_trades} |\n")
    md_lines.append(f"| TP1 hits | {len(tp1_trades)} |\n")
    md_lines.append(f"| TP2 hits | {len(tp2_trades)} |\n")
    md_lines.append(f"| SL hits | {len(sl_trades)} |\n")
    md_lines.append(f"| PM-closed (no SL) | {len(pm_close_trades)} |\n")
    md_lines.append(f"| LONG / SHORT | {by_direction.get('LONG', 0)} / "
                    f"{by_direction.get('SHORT', 0)} |\n")
    md_lines.append(f"| Decisions captured (all actions) | {len(captured_decisions)} |\n")

    md_lines.append("\n## Decisoes por action\n\n")
    md_lines.append("| Action | Count |\n|---|---:|\n")
    for act, n in sorted(action_counts.items(), key=lambda x: -x[1]):
        md_lines.append(f"| {act} | {n} |\n")

    md_lines.append(historical_summary)

    md_lines.append("\n## Per-trade resumo\n\n")
    md_lines.append("| # | open_ts | dir | entry | SL | TP1 | TP2 | TP1? | TP2? | SL? | "
                    "legs |\n")
    md_lines.append("|---|---|---|---:|---:|---:|---:|:-:|:-:|:-:|---|\n")
    for i, t in enumerate(trades, 1):
        md_lines.append(
            f"| {i} | {t.open_ts} | {t.direction} | "
            f"{t.entry:.2f} | {t.sl:.2f} | {t.tp1:.2f} | {t.tp2:.2f} | "
            f"{'V' if t.tp1_hit else '-'} | {'V' if t.tp2_hit else '-'} | "
            f"{'V' if t.sl_hit else '-'} | {','.join(str(x) for x in t.leg_tickets)} |\n"
        )

    md_lines.extend(forensic_md)

    (out_dir / "REPORT.md").write_text("".join(md_lines), encoding="utf-8")

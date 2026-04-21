#!/usr/bin/env python3
"""
run_live.py -- ATS Live Gate v1.3 runner + APEX event-driven orchestrator.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EVENT-DRIVEN MODE (full live pipeline):
    python run_live.py --execute                        # RoboForex (default)
    python run_live.py --execute --broker hantec        # Hantec live account
    python run_live.py --dry_run          # AUTO LEVEL DETECTION (no orders)
    python run_live.py --liq_top 4750 --liq_bot 4580 --execute  # manual override

    Starts EventProcessor (Layer 2) + PositionMonitor (Layer 4) together.
    EventProcessor blocks the main thread (MT5 tick loop every 1s).
    PositionMonitor runs in background daemon thread (every 2s).
    Levels auto-detected from gc_ats_features_v4.parquet (ATR fallback if stale).
    Levels refresh automatically every 4 hours.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SINGLE ENTRY CHECK (fixed lot -- e.g. XAUUSD 0.03 lots):
    python run_live.py --price 2650 --dir SHORT --liq_top 2651 --liq_bot 2540
                       --sl 2670 --tp1 2620 --tp2 2580 --lot_size 0.03

ENTRY CHECK (1% risk formula with balance):
    python run_live.py --price 5400 --dir SHORT --liq_top 5401 --liq_bot 4877
                       --sl 5432 --tp1 5340 --tp2 5200 --balance 50000

ENTRY CHECK (loop -- re-checks every 30s):
    python run_live.py --price 5400 --dir SHORT --liq_top 5401 --liq_bot 4877
                       --sl 5432 --tp1 5340 --balance 50000 --loop

JSON OUTPUT (for automation):
    python run_live.py --price 5400 --dir SHORT --liq_top 5401 --liq_bot 4877
                       --sl 5432 --balance 50000 --json

POSITION MONITOR only (check exit signals for open trade):
    python run_live.py --monitor --price 5400 --dir SHORT --entry 5390
                       --liq_top 5401 --liq_bot 4877 --tp1 5340 --tp2 5200

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULES (from ATS v1.2):
    - Open trade whenever gate fires GO -- no frequency limit
    - Max simultaneous trades: --max_trades (default 3)
    - lot_size = (balance * 0.01) / sl_distance  [1% risk per trade]
    - Breakeven: move SL to entry after TP1 is hit
    - Exit: TP2 hit | gate score <= -4 for 3 bars | delta_4h regime flip

EXIT CODES:
    0 = GO / no exit signal
    1 = BLOCKED / EXIT signal
    2 = ERROR

ENVIRONMENT:
    ATS_DATA_L2_DIR  -- override microstructure dir  (default: C:/data/level2/_gc_xcec)
    ATS_ICE_DIR      -- override iceberg JSONL dir   (default: C:/data/iceberg)
    ATS_MAX_TRADES   -- override max simultaneous trades (default: 3)
    ATS_POSITIONS    -- path to open-positions JSON   (default: ./positions.json)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from ats_live_gate import ATSLiveGate

# MT5 executor -- initialized in _init_executor() after --broker arg is parsed.
# MT5 Python library only supports ONE terminal connection per process.
# With --broker roboforex: connects to RoboForex terminal.
# With --broker hantec:    connects to Hantec terminal (no RoboForex init at all).
# This prevents the account-mismatch bug caused by two initialize() calls.
_executor = None
EXECUTOR_AVAILABLE = False
_executor_live = None       # always None (kept for legacy references in non-event-driven paths)
LIVE_EXECUTOR_AVAILABLE = False


def _init_executor(broker: str) -> None:
    """Initialize the correct MT5 executor based on --broker argument."""
    global _executor, EXECUTOR_AVAILABLE
    if broker == "hantec":
        try:
            from mt5_executor_hantec import MT5ExecutorHantec as _H
            _executor = _H()
            EXECUTOR_AVAILABLE = _executor.connected
            if EXECUTOR_AVAILABLE:
                print(_color("[BROKER] Hantec live account connected (account 50051145)", _GREEN + _BOLD))
            else:
                print(_color("[BROKER] WARNING: Hantec MT5 NOT connected", _RED))
        except Exception as _he:
            _executor = None
            EXECUTOR_AVAILABLE = False
            print(_color("[BROKER] ERROR loading Hantec executor: %s" % _he, _RED))
    else:  # default: roboforex
        try:
            from mt5_executor import MT5Executor as _MT5Executor
            _executor = _MT5Executor()
            EXECUTOR_AVAILABLE = _executor.connected
            if EXECUTOR_AVAILABLE:
                print(_color("[BROKER] RoboForex demo account connected (account 68302120)", _GREEN))
            else:
                print(_color("[BROKER] WARNING: RoboForex MT5 NOT connected", _RED))
        except Exception as _re:
            _executor = None
            EXECUTOR_AVAILABLE = False
            print(_color("[BROKER] ERROR loading RoboForex executor: %s" % _re, _RED))

# Event-driven pipeline -- optional (only used in live mode)
try:
    from live.signal_queue import push as _sq_push
    _SIGNAL_QUEUE_AVAILABLE = True
except Exception:
    _sq_push = None
    _SIGNAL_QUEUE_AVAILABLE = False

try:
    from live.event_processor import EventProcessor as _EventProcessor
    from live.position_monitor import PositionMonitor as _PositionMonitor
    from live.level_detector import get_current_levels as _get_current_levels
    from live.m30_updater import start as _start_m30_updater, run_update as _run_m30_update_sync
    from live.m5_updater  import start as _start_m5_updater
    from live.d1_h4_updater import start as _start_d1h4_updater
    from live.feed_health import FeedHealthMonitor as _FeedHealthMonitor
    _LIVE_PIPELINE_AVAILABLE = True
except Exception as _ep_e:
    _EventProcessor        = None
    _PositionMonitor       = None
    _get_current_levels    = None
    _start_m30_updater     = None
    _run_m30_update_sync   = None
    _start_m5_updater      = None
    _start_d1h4_updater    = None
    _FeedHealthMonitor     = None
    _LIVE_PIPELINE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_TRADES_DEFAULT = int(os.environ.get("ATS_MAX_TRADES", "3"))
POSITIONS_FILE     = Path(os.environ.get("ATS_POSITIONS", str(Path(__file__).parent / "positions.json")))

# Exit thresholds
EXIT_SCORE_THRESH   = -4    # gate score <= this = danger signal
EXIT_SCORE_BARS     = 3     # consecutive danger bars before exit
REGIME_FLIP_LONG    = +1_000  # delta_4h crosses above this while SHORT = regime flipped
REGIME_FLIP_SHORT   = -1_000  # delta_4h crosses below this while LONG = regime flipped

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_ORANGE = "\033[33m"


def _color(text: str, code: str) -> str:
    if sys.stdout.isatty():
        return code + text + _RESET
    return text


# ---------------------------------------------------------------------------
# Position tracker (flat JSON file)
# ---------------------------------------------------------------------------

def _load_positions() -> list:
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text())
        except Exception:
            return []
    return []


def _save_positions(positions: list) -> None:
    POSITIONS_FILE.write_text(json.dumps(positions, indent=2, default=str))


def _count_open() -> int:
    return len(_load_positions())


# ---------------------------------------------------------------------------
# Lot sizing
# ---------------------------------------------------------------------------

def _lot_size(balance: float, entry: float, sl: float) -> float:
    """
    1% risk per trade.
    lot_size = (balance * 0.01) / sl_distance_in_pts
    GC: 1 lot = $100/pt  ->  lot = risk_usd / (sl_pts * 100)
    Returns lots rounded to 2 decimal places.
    """
    sl_pts = abs(entry - sl)
    if sl_pts <= 0:
        return 0.0
    risk_usd = balance * 0.01
    return round(risk_usd / (sl_pts * 100), 2)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_entry(decision, now: pd.Timestamp, args) -> str:
    status_str  = _color("  GO  ", _GREEN + _BOLD) if decision.go else _color(" BLOCK", _RED + _BOLD)
    score_color = _GREEN if decision.total_score >= 2 else (_RED if decision.total_score <= -2 else _YELLOW)
    score_str   = _color("%+d" % decision.total_score, score_color)
    mom = decision.momentum
    ice = decision.iceberg
    ts  = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "",
        _color("=" * 70, _CYAN),
        _color("  ATS LIVE GATE  v1.3", _BOLD) + "  |  " + ts,
        _color("=" * 70, _CYAN),
        "  %s  |  %s @ %.1f  |  score=%s" % (status_str, decision.direction, decision.entry_price, score_str),
        "  Reason : %s" % decision.reason,
        "",
        "  Momentum : delta_4h=%+.0f  d1bar=%+.0f  bar_pts=%+.1f  [%s]" % (
            mom.delta_4h, mom.delta_1bar, mom.price_1bar_pts, mom.status),
        "  Iceberg  : score=%+d  conf=%.2f  type=%-12s  aligned=%s" % (
            ice.score, ice.confidence, ice.primary_type, ice.aligned),
        "  Ice note : %s" % ice.reason,
    ]

    fixed_lot = getattr(args, "lot_size", None)
    has_sl    = hasattr(args, "sl") and args.sl is not None
    has_bal   = hasattr(args, "balance") and args.balance
    show_size = decision.go and has_sl and (fixed_lot or has_bal)

    if show_size:
        sl_pts = abs(args.price - args.sl)
        if fixed_lot:
            lot  = fixed_lot
            risk = lot * sl_pts * 100
            size_line = "  Lot size : %.2f lots (fixed)  |  Risk : $%.2f (%.1f%% of bal)" % (
                lot, risk, (risk / args.balance * 100) if args.balance else 0)
        else:
            lot  = _lot_size(args.balance, args.price, args.sl)
            risk = args.balance * 0.01
            size_line = "  Balance  : $%.2f  |  Risk 1%%  : $%.2f  |  Lot size : %.3f" % (
                args.balance, risk, lot)
        lines += [
            "",
            _color("  POSITION SIZING", _BOLD),
            "  SL dist  : %.1f pts" % sl_pts,
            size_line,
        ]
        if hasattr(args, "tp1") and args.tp1:
            r1 = abs(args.tp1 - args.price) / sl_pts if sl_pts > 0 else 0
            gain = abs(args.tp1 - args.price) * lot * 100
            lines.append("  TP1 dist : %.1f pts  |  RR(TP1) : 1:%.1f  |  Gain : $%.2f" % (
                abs(args.tp1 - args.price), r1, gain))
        if hasattr(args, "tp2") and args.tp2:
            r2 = abs(args.tp2 - args.price) / sl_pts if sl_pts > 0 else 0
            gain2 = abs(args.tp2 - args.price) * lot * 100
            lines.append("  TP2 dist : %.1f pts  |  RR(TP2) : 1:%.1f  |  Gain : $%.2f" % (
                abs(args.tp2 - args.price), r2, gain2))

        open_count = _count_open()
        cap_color  = _RED if open_count >= MAX_TRADES_DEFAULT else _GREEN
        lines.append("  Open pos : %s  (max %d)" % (
            _color(str(open_count), cap_color), MAX_TRADES_DEFAULT))
        if open_count >= MAX_TRADES_DEFAULT:
            lines.append(_color("  WARNING: max simultaneous trades reached -- skip entry", _RED + _BOLD))

    lines += [_color("=" * 70, _CYAN), ""]
    return "\n".join(lines)


def _format_monitor(decision, now: pd.Timestamp, args, danger_streak: int) -> str:
    mom = decision.momentum
    ice = decision.iceberg
    ts  = now.strftime("%H:%M:%S UTC")

    # Determine exit signals
    exit_signals = []
    score_danger = decision.total_score <= EXIT_SCORE_THRESH
    if score_danger:
        exit_signals.append("gate score %+d (<= %d)" % (decision.total_score, EXIT_SCORE_THRESH))

    if args.dir == "SHORT" and mom.delta_4h > REGIME_FLIP_LONG:
        exit_signals.append("regime flip: delta_4h=%+.0f (bull)" % mom.delta_4h)
    elif args.dir == "LONG" and mom.delta_4h < REGIME_FLIP_SHORT:
        exit_signals.append("regime flip: delta_4h=%+.0f (bear)" % mom.delta_4h)

    if danger_streak >= EXIT_SCORE_BARS:
        exit_signals.append("danger streak: %d bars" % danger_streak)

    # Breakeven check
    be_triggered = False
    if hasattr(args, "tp1") and args.tp1 and hasattr(args, "entry") and args.entry:
        if args.dir == "SHORT" and hasattr(args, "current_price") and args.current_price <= args.tp1:
            be_triggered = True
        elif args.dir == "LONG" and hasattr(args, "current_price") and args.current_price >= args.tp1:
            be_triggered = True

    exit_now = len(exit_signals) > 0

    status  = _color(" EXIT ", _RED + _BOLD) if exit_now else _color("  OK  ", _GREEN + _BOLD)
    d4h_str = _color("%+.0f" % mom.delta_4h, _RED if score_danger else _YELLOW)

    lines = [
        "[%s] %s  score=%+d  d4h=%s  streak=%d  mom=%s" % (
            ts, status, decision.total_score, d4h_str, danger_streak, mom.status),
    ]
    if be_triggered:
        lines.append(_color("  -> BREAKEVEN: TP1 reached -- move SL to entry %.1f" % args.entry, _YELLOW + _BOLD))
    for sig in exit_signals:
        lines.append(_color("  -> EXIT: %s" % sig, _RED + _BOLD))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------

def _to_json_entry(decision, now: pd.Timestamp, args) -> dict:
    mom = decision.momentum
    ice = decision.iceberg
    out: dict = {
        "timestamp":   now.isoformat(),
        "go":          decision.go,
        "total_score": decision.total_score,
        "reason":      decision.reason,
        "direction":   decision.direction,
        "entry_price": args.price,
        "liq_top":     args.liq_top,
        "liq_bot":     args.liq_bot,
        "momentum": {
            "status":         mom.status,
            "delta_4h":       round(mom.delta_4h, 1),
            "delta_1bar":     round(mom.delta_1bar, 1),
            "price_1bar_pts": round(mom.price_1bar_pts, 2),
            "score":          mom.score,
        },
        "iceberg": {
            "detected":   ice.detected,
            "aligned":    ice.aligned,
            "score":      ice.score,
            "confidence": round(ice.confidence, 4),
            "type":       ice.primary_type,
            "reason":     ice.reason,
        },
    }
    fixed_lot = getattr(args, "lot_size", None)
    has_sl    = hasattr(args, "sl") and args.sl
    if has_sl and (fixed_lot or (hasattr(args, "balance") and args.balance)):
        sl_pts = round(abs(args.price - args.sl), 1)
        if fixed_lot:
            lot     = fixed_lot
            risk    = round(lot * sl_pts * 100, 2)
        else:
            lot     = _lot_size(args.balance, args.price, args.sl)
            risk    = round(args.balance * 0.01, 2)
        out["sizing"] = {
            "balance":     getattr(args, "balance", None),
            "risk_usd":    risk,
            "sl_pts":      sl_pts,
            "lot_size":    lot,
            "fixed_lot":   fixed_lot is not None,
            "open_trades": _count_open(),
            "max_trades":  MAX_TRADES_DEFAULT,
            "capacity_ok": _count_open() < MAX_TRADES_DEFAULT,
        }
    return out


def _to_json_monitor(decision, now: pd.Timestamp, danger_streak: int, exit_signals: list) -> dict:
    mom = decision.momentum
    return {
        "timestamp":     now.isoformat(),
        "total_score":   decision.total_score,
        "danger_streak": danger_streak,
        "exit_now":      len(exit_signals) > 0,
        "exit_signals":  exit_signals,
        "momentum": {
            "status":   mom.status,
            "delta_4h": round(mom.delta_4h, 1),
            "score":    mom.score,
        },
        "iceberg_score": decision.iceberg.score,
    }


# ---------------------------------------------------------------------------
# Gate log writer
# ---------------------------------------------------------------------------

def _write_gate_log(decision, args) -> None:
    """Write gate decision to live_log.csv via executor."""
    if _executor is None:
        return
    try:
        ice = decision.iceberg
        patterns = []
        if ice.detected:
            patterns.append(ice.primary_type)
        if ice.absorption_side != "none":
            patterns.append("abs_%s" % ice.absorption_side)
        if getattr(ice, "sweep_dir", "none") != "none":
            patterns.append("sweep_%s" % ice.sweep_dir)
        zone = "liq_top" if args.dir == "SHORT" else "liq_bot"
        v4_status = getattr(decision, "v4_status", "UNKNOWN")
        _executor.log_gate(
            direction=decision.direction,
            gate_decision="CONFIRMED" if decision.go else "FILTERED",
            score=decision.total_score,
            macro_delta=decision.macro_delta,
            mom_status=decision.momentum.status,
            v4_status=v4_status,
            reason=decision.reason,
            zone=zone,
            patterns=",".join(patterns),
        )
    except Exception as e:
        pass  # log write errors never affect gate


# ---------------------------------------------------------------------------
# Entry check
# ---------------------------------------------------------------------------

def _run_entry_check(gate: ATSLiveGate, args, output_json: bool) -> int:
    now = pd.Timestamp.utcnow()
    dry_run = getattr(args, "dry_run", False)
    execute  = getattr(args, "execute", False)

    try:
        decision = gate.check(
            entry_price=args.price,
            direction=args.dir,
            now=now,
            liq_top=args.liq_top,
            liq_bot=args.liq_bot,
        )
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e), "go": True}, ensure_ascii=False))
        else:
            print(_color("ERROR: %s" % e, _RED), file=sys.stderr)
            print(_color("Defaulting to GO (gate error)", _YELLOW), file=sys.stderr)
        return 0  # fail-safe

    # Always write gate log
    _write_gate_log(decision, args)

    if output_json:
        print(json.dumps(_to_json_entry(decision, now, args), ensure_ascii=False))
    else:
        print(_format_entry(decision, now, args))

    # --- Execution block ---
    if decision.go and (dry_run or execute):
        fixed_lot = getattr(args, "lot_size", None)
        has_sl    = hasattr(args, "sl") and args.sl is not None
        has_bal   = hasattr(args, "balance") and args.balance

        if has_sl and (fixed_lot or has_bal):
            lot = fixed_lot if fixed_lot else _lot_size(args.balance, args.price, args.sl)
            tp1 = getattr(args, "tp1", None)
            tp2 = getattr(args, "tp2", None)
            from mt5_executor import _split_lots as _split_lots_fn
            l1, l2, l3 = _split_lots_fn(lot)
            legs = 3 if l3 >= 0.01 else 2

            if dry_run:
                print(_color("\n  [DRY RUN] WOULD OPEN %s %s %.2f lots" % (
                    "XAUUSD", args.dir, lot), _YELLOW + _BOLD))
                print("  Leg1=%.2f  Leg2=%.2f  Leg3=%.2f  (%d legs)" % (l1, l2, l3, legs))
                if has_sl:
                    print("  SL=%.1f  TP1=%s  TP2=%s" % (
                        args.sl,
                        "%.1f" % tp1 if tp1 else "n/a",
                        "%.1f" % tp2 if tp2 else "n/a",
                    ))

            elif execute:
                if _count_open() >= args.max_trades:
                    print(_color("  SKIP: max trades reached (%d)" % args.max_trades, _RED))
                else:
                    # -- Signal Queue (EA distribution) -----------------------
                    if _SIGNAL_QUEUE_AVAILABLE and _sq_push is not None:
                        from mt5_executor import _split_lots as _split_lots_sq
                        _l1, _l2, _l3 = _split_lots_sq(lot)
                        _sq_push(
                            signal_type="ENTRY",
                            direction=args.dir,
                            entry=current_price,
                            sl=args.sl,
                            tp1=tp1 or 0.0,
                            tp2=tp2 or 0.0,
                            lot_leg1=_l1,
                            lot_leg2=_l2,
                            lot_runner=_l3,
                            accounts=[68302120, 50051145],
                        )

                    # -- Demo executor (RoboForex) ----------------------------
                    if not EXECUTOR_AVAILABLE:
                        print(_color("  WARNING: RoboForex MT5 not connected", _RED))
                    else:
                        result = _executor.open_position(
                            symbol="XAUUSD",
                            direction=args.dir,
                            lot_size=lot,
                            sl=args.sl,
                            tp1=tp1 or 0.0,
                            tp2=tp2,
                            dry_run=False,
                        )
                        if result["success"]:
                            t = result["tickets"]
                            print(_color("  [DEMO] OPENED: tickets=%s  entry=%.2f" % (t, result["entry"]), _GREEN + _BOLD))
                            _executor.log_trade(
                                direction=args.dir,
                                decision="CONFIRMED",
                                lots=lot,
                                entry=result["entry"],
                                sl=args.sl,
                                tp1=tp1 or 0.0,
                                tp2=tp2 or 0.0,
                                result="open",
                                gate_score=decision.total_score,
                                leg1_ticket=t[0] if len(t) > 0 else 0,
                                leg2_ticket=t[1] if len(t) > 1 else 0,
                                leg3_ticket=t[2] if len(t) > 2 else 0,
                            )
                        else:
                            print(_color("  [DEMO] ORDER FAILED: %s" % result.get("error"), _RED))

                    # -- Live executor (Hantec) -------------------------------
                    if not LIVE_EXECUTOR_AVAILABLE:
                        print(_color("  WARNING: Hantec MT5 not connected -- skipping live", _RED))
                    else:
                        result_live = _executor_live.open_position(
                            symbol="XAUUSD",
                            direction=args.dir,
                            lot_size=lot,
                            sl=args.sl,
                            tp1=tp1 or 0.0,
                            tp2=tp2,
                            dry_run=False,
                        )
                        if result_live["success"]:
                            t = result_live["tickets"]
                            print(_color("  [LIVE] OPENED on Hantec: tickets=%s  entry=%.2f" % (t, result_live["entry"]), _GREEN + _BOLD))
                            _executor_live.log_trade(
                                direction=args.dir,
                                decision="CONFIRMED",
                                lots=lot,
                                entry=result_live["entry"],
                                sl=args.sl,
                                tp1=tp1 or 0.0,
                                tp2=tp2 or 0.0,
                                result="open",
                                gate_score=decision.total_score,
                                leg1_ticket=t[0] if len(t) > 0 else 0,
                                leg2_ticket=t[1] if len(t) > 1 else 0,
                                leg3_ticket=t[2] if len(t) > 2 else 0,
                            )
                        else:
                            print(_color("  [LIVE] ORDER FAILED: %s" % result_live.get("error"), _RED))

    return 0 if decision.go else 1


# ---------------------------------------------------------------------------
# Monitor mode (exit signal detection for open position)
# ---------------------------------------------------------------------------

def _run_monitor(gate: ATSLiveGate, args, output_json: bool, interval: int) -> int:
    danger_streak = 0
    if not output_json:
        print(_color("Monitoring %s @ %.1f  |  TP1=%.1f  TP2=%s  |  Interval=%ds  Ctrl+C to stop" % (
            args.dir, args.entry,
            args.tp1 if args.tp1 else 0,
            "%.1f" % args.tp2 if args.tp2 else "n/a",
            interval,
        ), _CYAN))

    try:
        while True:
            now = pd.Timestamp.now("UTC")
            try:
                decision = gate.check(
                    entry_price=args.entry,
                    direction=args.dir,
                    now=now,
                    liq_top=args.liq_top,
                    liq_bot=args.liq_bot,
                )
            except Exception as e:
                if not output_json:
                    print(_color("gate error: %s" % e, _RED))
                time.sleep(interval)
                continue

            mom = decision.momentum
            score_danger = decision.total_score <= EXIT_SCORE_THRESH
            danger_streak = danger_streak + 1 if score_danger else 0

            exit_signals = []
            if danger_streak >= EXIT_SCORE_BARS:
                exit_signals.append("danger streak: %d bars score<=%d" % (danger_streak, EXIT_SCORE_THRESH))
            if args.dir == "SHORT" and mom.delta_4h > REGIME_FLIP_LONG:
                exit_signals.append("regime flip SHORT->BULL delta_4h=%+.0f" % mom.delta_4h)
            elif args.dir == "LONG" and mom.delta_4h < REGIME_FLIP_SHORT:
                exit_signals.append("regime flip LONG->BEAR delta_4h=%+.0f" % mom.delta_4h)

            if output_json:
                print(json.dumps(_to_json_monitor(decision, now, danger_streak, exit_signals),
                                 ensure_ascii=False), flush=True)
            else:
                print(_format_monitor(decision, now, args, danger_streak))

            if exit_signals:
                return 1

            time.sleep(interval)

    except KeyboardInterrupt:
        if not output_json:
            print("\nMonitor stopped.")
        return 0


# ---------------------------------------------------------------------------
# Event-driven orchestrator (full live pipeline)
# ---------------------------------------------------------------------------

def _run_event_driven(args) -> None:
    """
    Start the full APEX event-driven pipeline:
      1. Auto-detect structural levels via level_detector.get_current_levels()
      2. PositionMonitor -- background daemon thread (every 2s)
      3. EventProcessor  -- main thread (blocks, MT5 tick loop every 1s)
      4. Level refresh   -- background thread (every 4h)

    Usage:
      python run_live.py --execute
      python run_live.py --dry_run
      # --liq_top / --liq_bot are optional overrides; auto-detected if omitted
    """
    if not _LIVE_PIPELINE_AVAILABLE:
        print(_color("ERROR: live pipeline not available -- check live/ imports", _RED))
        sys.exit(2)

    dry_run  = getattr(args, "dry_run", True)
    execute  = getattr(args, "execute", False)
    # Total lot per trade group: 0.05 = 0.02 (Leg1) + 0.02 (Leg2) + 0.01 (Runner).
    # BARBARA-DEFINED. _split_lots(0.05) -> 40%=0.02, 40%=0.02, 20%=0.01.
    # With 0.02 default (old), l3=round(0.004)=0.00 -> Runner never opens.
    lot_size = getattr(args, "lot_size", None) or 0.05
    sl_pts   = getattr(args, "sl_pts",  20.0)
    tp1_pts  = getattr(args, "tp1_pts", 20.0)
    tp2_pts  = getattr(args, "tp2_pts", 50.0)

    mode = "DRY RUN" if dry_run else "EXECUTE" if execute else "ADVISORY"
    print(_color("Starting APEX event-driven pipeline -- mode: %s" % mode, _CYAN))

    # --- Auto-detect levels (or use manual overrides if provided) ---
    manual_top = getattr(args, "liq_top", None)
    manual_bot = getattr(args, "liq_bot", None)

    if manual_top is not None and manual_bot is not None:
        levels = {
            "liq_top": manual_top,
            "liq_bot": manual_bot,
            "fmv":     round((manual_top + manual_bot) / 2.0, 2),
            "liq_top_mt5": round(manual_top - 31.0, 2),
            "liq_bot_mt5": round(manual_bot - 31.0, 2),
            "fmv_mt5":     round((manual_top + manual_bot) / 2.0 - 31.0, 2),
            "atr_14": 0.0,
            "source": "manual_override",
        }
        print(_color("MANUAL LEVEL OVERRIDE: liq_top=%.2f  liq_bot=%.2f" % (
            manual_top, manual_bot), _YELLOW))
    else:
        print(_color("AUTO LEVEL DETECTION ACTIVE -- detecting structural levels...", _CYAN))
        levels = _get_current_levels()
        if levels is None:
            print(_color(
                "WARNING: No confirmed box in current data -- system waiting for valid box. "
                "No signals will be generated.", _YELLOW))
            import time
            while _get_current_levels() is None:
                time.sleep(60)
            levels = _get_current_levels()
        print(_color(
            "Levels detected [%s]: liq_top=%.2f  liq_bot=%.2f  fmv=%.2f  ATR_14=%.2f" % (
                levels["source"], levels["liq_top"], levels["liq_bot"],
                levels["fmv"], levels["atr_14"],
            ), _GREEN if levels["source"] == "parquet" else _YELLOW))

    # --- Feed health monitor (FIX 2 -- 2026-04-08) ---
    # Detects stale microstructure feed after restarts.
    # If feed goes DEAD (>5 min no update), gate checks are suspended.
    feed_monitor = None
    if _FeedHealthMonitor is not None:
        feed_monitor = _FeedHealthMonitor()
        initial_health = feed_monitor.check()
        if initial_health["status"] == "OK":
            print(_color("Feed health: OK (age=%.0fs)" % initial_health["age_sec"], _GREEN))
        elif initial_health["status"] == "NO_FILE":
            print(_color("Feed health: NO_FILE -- microstructure not yet created today", _YELLOW))
        else:
            print(_color("Feed health: %s (age=%.0fs) -- check Quantower L2 stream" % (
                initial_health["status"], initial_health["age_sec"]), _YELLOW))
        feed_monitor.start()
        print(_color("FeedHealthMonitor started (check every 2 min, DEAD threshold=5 min)", _CYAN))

    # --- M30 Startup Staleness Check (MANDATORY -- blocks gate loop if stale) ---
    # System cannot start with a stale parquet (>35 min = missed at least one M30 bar).
    # Run synchronously here so fresh levels are loaded before gate loop begins.
    _M30_PARQUET = Path("C:/data/processed/gc_m30_boxes.parquet")
    _M30_STALE_SECS = 35 * 60  # 35 minutes
    if _run_m30_update_sync is not None:
        _m30_age = None
        if _M30_PARQUET.exists():
            import time as _time_tmp
            _m30_age = _time_tmp.time() - _M30_PARQUET.stat().st_mtime
        if _m30_age is None or _m30_age > _M30_STALE_SECS:
            _age_str = ("%.0f min" % (_m30_age / 60)) if _m30_age is not None else "missing"
            print(_color(
                "M30_UPDATE: parquet stale (%s) -- forcing sync rebuild before gate loop..." % _age_str,
                _YELLOW))
            try:
                _s = _run_m30_update_sync()
                print(_color(
                    "M30_UPDATE: boxes refreshed at %s -- %d boxes active  liq_top=%.2f  liq_bot=%.2f" % (
                        pd.Timestamp.utcnow().strftime("%H:%M:%S UTC"),
                        _s["total_boxes"], _s["last_liq_top"], _s["last_liq_bot"],
                    ), _GREEN))
            except Exception as _m30e:
                print(_color("M30_UPDATE: sync rebuild failed -- %s" % _m30e, _RED))
        else:
            print(_color("M30_UPDATE: parquet fresh (age=%.0fs) -- OK" % _m30_age, _GREEN))
    else:
        print(_color("WARNING: M30 sync updater not available -- parquet staleness not checked", _YELLOW))

    # --- M5 Updater (background thread -- execution timeframe) ---
    # Rebuilds gc_m5_boxes.parquet every 60s for real-time Wyckoff execution levels.
    # M5 is the primary source for liq_top/liq_bot in level_detector.get_current_levels().
    if args.no_updaters:
        print(_color("M5/M30 Updaters SKIPPED (--no-updaters) -- another service owns parquet writes", _YELLOW))
    elif _start_m5_updater is not None:
        _start_m5_updater()
        print(_color("M5 Updater started (60s cadence, rebuilds gc_m5_boxes.parquet -- execution levels)", _CYAN))
    else:
        print(_color("WARNING: M5 Updater not available -- falling back to M30 execution levels", _YELLOW))

    # --- M30 Updater (background thread -- macro bias / TP2 targets) ---
    # Rebuilds gc_m30_boxes.parquet every 60s for macro trend context and TP2 structural targets.
    # M30 is no longer the primary execution timeframe -- it provides m30_bias to level_detector.
    if not args.no_updaters and _start_m30_updater is not None:
        _start_m30_updater()
        print(_color("M30 Updater started (60s cadence, rebuilds gc_m30_boxes.parquet -- macro bias)", _CYAN))
    elif not args.no_updaters:
        print(_color("WARNING: M30 Updater not available -- m30_bias will be unknown", _YELLOW))

    # --- D1/H4 Bias Engine (FASE 4a shadow -- DISABLED) ---
    # DISABLED 2026-04-14: full M1 reload (2.2M rows) every 5min was degrading
    # server performance (1GB RAM, 90% CPU). Needs incremental/event-driven redesign.
    # Shadow bias still readable from gc_d1h4_bias.json (last standalone run).
    # TODO: redesign as incremental updater that only processes new bars.
    # if not args.no_updaters and _start_d1h4_updater is not None:
    #     _get_dt = lambda: getattr(processor, "daily_trend", "unknown")
    #     _start_d1h4_updater(get_daily_trend_fn=_get_dt)
    #     print(_color("D1H4 Updater started (300s cadence, SHADOW MODE)", _CYAN))
    print(_color("D1H4 Updater DISABLED (perf issue -- awaiting incremental redesign)", _YELLOW))

    # --- Layer 4: PositionMonitor (background thread) ---
    if _executor is not None:
        monitor = _PositionMonitor(
            executor      = _executor,
            dry_run       = dry_run,
            lot_size      = lot_size,
            executor_live = _executor_live if LIVE_EXECUTOR_AVAILABLE else None,
        )
        monitor.start()
        print(_color("PositionMonitor started (background, 2s interval)", _CYAN))
    else:
        print(_color("WARNING: MT5Executor not available -- PositionMonitor disabled", _YELLOW))

    # --- Layer 2: EventProcessor (blocks main thread) ---
    processor = _EventProcessor(
        liq_top      = levels["liq_top"],
        liq_bot      = levels["liq_bot"],
        dry_run      = dry_run,
        execute      = execute,
        lot_size     = lot_size,
        sl_pts       = sl_pts,
        tp1_pts      = tp1_pts,
        tp2_pts      = tp2_pts,
        feed_monitor = feed_monitor,   # FIX 2: gate disabled when feed is DEAD
        box_high     = levels.get("box_high"),      # FIX 6: V1 TRENDING zone check
        box_low      = levels.get("box_low"),
        daily_trend  = levels.get("daily_trend", "unknown"),
    )

    # --- Initialize M30 bias immediately (don't wait for first refresh cycle) ---
    # Without this, gates have no data for the first ~15-75s after startup.
    processor.m30_bias       = levels.get("m30_bias", "unknown")
    processor.m30_liq_top_gc = levels.get("m30_liq_top")
    processor.m30_liq_bot_gc = levels.get("m30_liq_bot")
    with processor._lock:
        _off  = processor._gc_xauusd_offset
        _m30t = levels.get("m30_liq_top")
        _m30b = levels.get("m30_liq_bot")
        processor.m30_liq_top = round(_m30t - _off, 2) if _m30t is not None else None
        processor.m30_liq_bot = round(_m30b - _off, 2) if _m30b is not None else None
    # Feed ATR immediately so BORDER gate has real tolerance from tick 0
    _startup_atr = levels.get("atr_14")
    if _startup_atr and _startup_atr > 0:
        processor._metrics["atr"] = _startup_atr
    import logging as _logging_startup
    _startup_log = _logging_startup.getLogger("apex.run_live")
    _startup_log.info(
        "Startup init: bias=%s  m30_liq_top_gc=%s  m30_liq_bot_gc=%s  ATR=%.2f",
        processor.m30_bias, processor.m30_liq_top_gc, processor.m30_liq_bot_gc,
        processor._metrics.get("atr", 0.0),
    )

    # --- Level refresh thread (every 60s -- real-time cadence) ---
    # ADR-001 fix 2026-04-10: 30-min boundary sleep caused up to 60-min level
    # staleness (m30_updater 30min + level_refresh 30min). Both now run every
    # 60s. Box detection still uses M30 resampling internally; a new confirmed
    # box is picked up within ~1 min of bar close instead of up to 30 min.
    def _refresh_levels():
        import time as _time
        import logging as _logging
        _log = _logging.getLogger("apex.run_live")

        # Brief initial delay so m30_updater finishes its startup rebuild first
        _time.sleep(15)

        while True:
            try:
                new_levels = _get_current_levels()
                if new_levels is None:
                    _log.warning("Level refresh: no confirmed M30 box yet")
                else:
                    old_top = processor.liq_top_gc
                    old_bot = processor.liq_bot_gc

                    # BREAKOUT GUARD: TickBreakoutMonitor may have set more precise levels
                    # after detecting a sustained breakout. _refresh_levels must not
                    # overwrite them with older confirmed-box levels from the parquet
                    # (which lag by up to 2 M30 bars = 60 min).
                    # Rule: if TickBreakout is in BREAKOUT_UP state, keep liq_top/liq_bot
                    # from the breakout unless the parquet provides HIGHER liq_top.
                    # Similarly for BREAKOUT_DN. Only box_high_gc/box_low_gc/daily_trend
                    # are always updated (structural boundaries, safe to refresh).
                    tb_status = processor._tick_breakout.status() if hasattr(processor, "_tick_breakout") else {}
                    tb_state = tb_status.get("state", "CONTRACTION")
                    skip_liq = False
                    if tb_state in ("BREAKOUT_UP", "JAC_CANDIDATE") and tb_status.get("breakout_dir") == "UP":
                        # Don't downgrade liq_top below breakout high
                        if new_levels["liq_top"] < old_top:
                            _log.info(
                                "Level refresh: BREAKOUT_UP active -- keeping liq_top=%.2f (parquet=%.2f, skipping downgrade)",
                                old_top, new_levels["liq_top"],
                            )
                            skip_liq = True
                    elif tb_state in ("BREAKOUT_DN", "JAC_CANDIDATE") and tb_status.get("breakout_dir") == "DN":
                        # Don't upgrade liq_bot above breakout low
                        if new_levels["liq_bot"] > old_bot:
                            _log.info(
                                "Level refresh: BREAKOUT_DN active -- keeping liq_bot=%.2f (parquet=%.2f, skipping upgrade)",
                                old_bot, new_levels["liq_bot"],
                            )
                            skip_liq = True

                    # Always update structural box boundaries and trend (safe)
                    processor.box_high_gc = new_levels.get("box_high")
                    processor.box_low_gc  = new_levels.get("box_low")
                    processor.daily_trend = new_levels.get("daily_trend", "unknown")

                    # Feed M30 ATR14 to processor for BORDER gate tolerance
                    _new_atr = new_levels.get("atr_14")
                    if _new_atr and _new_atr > 0:
                        processor._metrics["atr_m30_parquet"] = _new_atr

                    # M30 macro bias and structural levels (Ponto 1 & 3 hard gates)
                    old_bias = processor.m30_bias
                    new_bias = new_levels.get("m30_bias", "unknown")
                    processor.m30_bias       = new_bias
                    processor.m30_liq_top_gc = new_levels.get("m30_liq_top")
                    processor.m30_liq_bot_gc = new_levels.get("m30_liq_bot")
                    # Convert M30 GC levels to MT5 space immediately
                    with processor._lock:
                        off = processor._gc_xauusd_offset
                        m30t = new_levels.get("m30_liq_top")
                        m30b = new_levels.get("m30_liq_bot")
                        processor.m30_liq_top = round(m30t - off, 2) if m30t is not None else None
                        processor.m30_liq_bot = round(m30b - off, 2) if m30b is not None else None
                    if new_bias != old_bias:
                        _log.info(
                            "M30 bias changed: %s -> %s  (m30_liq_top=%.2f  m30_liq_bot=%.2f)",
                            old_bias, new_bias,
                            new_levels.get("m30_liq_top", 0),
                            new_levels.get("m30_liq_bot", 0),
                        )

                    if not skip_liq:
                        processor.liq_top_gc = new_levels["liq_top"]
                        processor.liq_bot_gc = new_levels["liq_bot"]
                    # Also update MT5-space immediately using current offset --
                    # _refresh_offset() only runs when MT5 is available; this
                    # ensures proximity checks never use stale levels.
                    with processor._lock:
                        off = processor._gc_xauusd_offset
                        if not skip_liq:
                            processor.liq_top  = round(processor.liq_top_gc - off, 2)
                            processor.liq_bot  = round(processor.liq_bot_gc - off, 2)
                        bh = new_levels.get("box_high")
                        bl = new_levels.get("box_low")
                        processor.box_high = round(bh - off, 2) if bh is not None else None
                        processor.box_low  = round(bl - off, 2) if bl is not None else None
                    changed = (not skip_liq) and abs(new_levels["liq_top"] - old_top) > 0.5
                    _log.info(
                        "Levels refresh [%s box_id=%s age=%.1fh]: liq_top=%.2f  liq_bot=%.2f%s%s",
                        new_levels["source"], new_levels.get("box_id", "?"),
                        new_levels.get("box_age_h", 0),
                        processor.liq_top_gc, processor.liq_bot_gc,
                        "  *** NEW BOX ***" if changed else "",
                        "  [breakout guard active]" if skip_liq else "",
                    )
                    if changed:
                        print(_color(
                            "NEW M30 BOX [box_id=%s]: liq_top=%.2f  liq_bot=%.2f  fmv=%.2f  age=%.1fh" % (
                                new_levels.get("box_id", "?"),
                                new_levels["liq_top"], new_levels["liq_bot"],
                                new_levels.get("fmv", 0), new_levels.get("box_age_h", 0),
                            ), _GREEN))
            except Exception as _e:
                _log.warning("Level refresh failed: %s", _e)
            _time.sleep(60)   # real-time: check every 60s

    if manual_top is None:   # only refresh if auto-detected
        _t = threading.Thread(target=_refresh_levels, daemon=True, name="LevelRefresh")
        _t.start()

    processor.start()   # blocks until Ctrl+C


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ATS Live Gate v1.2 -- entry confirmation and position monitoring.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Core entry parameters
    parser.add_argument("--price",   type=float, required=False,
                        help="Proposed entry price")
    parser.add_argument("--dir",     type=str, default=None,
                        choices=["LONG", "SHORT", "long", "short"],
                        help="Trade direction: LONG or SHORT")
    parser.add_argument("--liq_top", type=float, default=None,
                        help="Structural top level (15-day high)")
    parser.add_argument("--liq_bot", type=float, default=None,
                        help="Structural bottom level (15-day low)")

    # Risk & sizing
    parser.add_argument("--sl",       type=float, default=None,
                        help="Stop loss price (used for lot sizing)")
    parser.add_argument("--tp1",      type=float, default=None,
                        help="TP1 price (Leg 1 target)")
    parser.add_argument("--tp2",      type=float, default=None,
                        help="TP2 price (runner target)")
    parser.add_argument("--balance",  type=float, default=None,
                        help="Current account balance in USD (used for 1%% risk formula)")
    parser.add_argument("--lot_size", type=float, default=None,
                        help="Fixed lot size override (e.g. 0.03). Skips 1%% formula if set.")
    parser.add_argument("--dry_run",  action="store_true",
                        help="Evaluate gate normally; if GO print WOULD OPEN but do NOT send MT5 order.")
    parser.add_argument("--execute",  action="store_true",
                        help="If GO, actually open position via MT5Executor.")
    parser.add_argument("--broker",   type=str, default="roboforex",
                        choices=["roboforex", "hantec"],
                        help="Which MT5 broker to use: 'roboforex' (demo) or 'hantec' (live). "
                             "Each service instance must run in its own process.")
    parser.add_argument("--no-updaters", action="store_true", dest="no_updaters",
                        help="Skip M5/M30 updater threads (use when another service instance "
                             "already runs them to avoid parquet write conflicts).")
    parser.add_argument("--max_trades", type=int, default=MAX_TRADES_DEFAULT,
                        help="Max simultaneous open trades (default %d)" % MAX_TRADES_DEFAULT)

    # Event-driven pipeline SL/TP distances (pts) -- used in live mode only
    parser.add_argument("--sl_pts",  type=float, default=20.0,
                        help="SL distance in points for event-driven mode (default 20)")
    parser.add_argument("--tp1_pts", type=float, default=20.0,
                        help="TP1 distance in points for event-driven mode (default 20)")
    parser.add_argument("--tp2_pts", type=float, default=50.0,
                        help="TP2 distance in points for event-driven mode (default 50)")

    # Monitor mode
    parser.add_argument("--monitor", action="store_true",
                        help="Monitor open position for exit signals (not entry check)")
    parser.add_argument("--entry",   type=float, default=None,
                        help="Actual entry price (for monitor mode)")

    # Operation modes
    parser.add_argument("--loop",     action="store_true",
                        help="Re-check entry every --interval seconds")
    parser.add_argument("--interval", type=int, default=30,
                        help="Interval in seconds for --loop / --monitor (default 30)")
    parser.add_argument("--json",     action="store_true",
                        help="Output JSON instead of human-readable text")

    args = parser.parse_args()
    if args.dir:
        args.dir = args.dir.upper()

    # -----------------------------------------------------------------------
    # EXECUTOR INIT -- must happen before singleton lock so broker is known
    # -----------------------------------------------------------------------
    broker = getattr(args, "broker", "roboforex")
    _init_executor(broker)

    # -----------------------------------------------------------------------
    # SINGLETON LOCK -- one instance per broker (roboforex + hantec can coexist)
    # -----------------------------------------------------------------------
    _lock_handle = None
    if args.execute:
        import msvcrt
        import tempfile
        _lock_path = os.path.join(tempfile.gettempdir(),
                                  f"run_live_execute_{broker}.lock")
        try:
            _lock_handle = open(_lock_path, "w")
            _lock_handle.write(str(os.getpid()))
            _lock_handle.flush()
            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (IOError, OSError):
            print(
                f"[SINGLETON] ABORT: another run_live.py --execute --broker {broker} "
                "is already running. Only one instance per broker allowed.",
                flush=True,
            )
            sys.exit(1)

    # -----------------------------------------------------------------------
    # Route: EVENT-DRIVEN LIVE MODE
    # Triggered when --execute or --dry_run given WITHOUT --price / --monitor.
    # --liq_top / --liq_bot are optional overrides; auto-detected if omitted.
    # -----------------------------------------------------------------------
    if (
        not args.monitor
        and args.price is None
        and (args.dry_run or args.execute)
    ):
        _run_event_driven(args)
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Route: SINGLE ENTRY CHECK or MONITOR (original behaviour)
    # -----------------------------------------------------------------------

    # Validate
    if args.monitor:
        if args.entry is None or args.dir is None:
            parser.error("--monitor requires --entry and --dir")
        if args.price is None:
            args.price = args.entry  # use entry price as reference for gate
    else:
        if args.price is None or args.dir is None:
            parser.error("--price and --dir are required for entry check")

    # Initialise gate
    if not args.json:
        print(_color("Initialising ATS Live Gate v1.3...", _CYAN))
    try:
        gate = ATSLiveGate()
    except Exception as e:
        msg = {"error": "Gate init failed: %s" % e, "go": True}
        if args.json:
            print(json.dumps(msg))
        else:
            print(_color("GATE INIT ERROR: %s" % e, _RED), file=sys.stderr)
        sys.exit(2)

    # Monitor mode
    if args.monitor:
        sys.exit(_run_monitor(gate, args, args.json, args.interval))

    # Entry check -- single or loop
    if not args.loop:
        code = _run_entry_check(gate, args, args.json)
        sys.exit(code)

    if not args.json:
        print(_color("Loop mode: checking every %ds. Ctrl+C to stop.\n" % args.interval, _YELLOW))
    try:
        while True:
            code = _run_entry_check(gate, args, args.json)
            if not args.json:
                print("Next check in %ds..." % args.interval)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        if not args.json:
            print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()

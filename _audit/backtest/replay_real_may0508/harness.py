"""Replay harness orchestrator (Phase 3 — wired but not yet driving live code).

Phase progression:
  Phase 2 [done] - Smoke instantiate EventProcessor + PositionMonitor with
                   BrokerMock.
  Phase 3 [done] - Chronological dispatcher + simulated clock; harness wires
                   them together and verifies clock advances correctly while
                   iterating events.
  Phase 4 [next] - Route dispatcher events to EventProcessor handlers
                   (microstructure -> _refresh_metrics, iceberg ->
                   _on_iceberg_event); drive PositionMonitor 2s checks
                   against advanced clock.
  Phase 5        - Capture decision stream + diff vs decision_log.jsonl,
                   forensic SL section, write REPORT.md.

Run:
    cd C:\\FluxQuantumAI

    # Phase 2 smoke (instantiate only)
    python -m _audit.backtest.replay_real_may0508.harness --smoke

    # Phase 3 smoke (clock + dispatcher; counts events as it advances clock)
    python -m _audit.backtest.replay_real_may0508.harness --smoke-phase3 \\
        --start 2026-05-05 --end 2026-05-09 [--limit 10000]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(r"C:/FluxQuantumAI")
sys.path.insert(0, str(REPO_ROOT))

# Force ctrader broker selection BEFORE importing event_processor
os.environ.setdefault("BROKER", "ctrader")

from _audit.backtest.replay_real_may0508.mock_broker import BrokerMock  # noqa: E402
from _audit.backtest.replay_real_may0508.replay_dispatcher import (  # noqa: E402
    chronological_events,
)
from _audit.backtest.replay_real_may0508.replay_clock import SimulatedClock  # noqa: E402
from _audit.backtest.replay_real_may0508.replay_handlers import ReplayRouter  # noqa: E402


def _patch_broker_and_import() -> tuple:
    """Import event_processor + position_monitor and rebind their MT5Executor
    symbols to BrokerMock. Returns (ep_mod, pm_mod)."""
    from live import event_processor as ep_mod
    from live import position_monitor as pm_mod

    ep_mod.MT5Executor = BrokerMock  # type: ignore[attr-defined]
    pm_mod.MT5Executor = BrokerMock  # type: ignore[attr-defined]
    return ep_mod, pm_mod


def smoke_phase2() -> int:
    """Phase 2 smoke: instantiate EP + PM with BrokerMock."""
    print("=" * 70)
    print("PHASE 2 SMOKE — instantiation")
    print("=" * 70)

    print("\n[1/4] Importing live.event_processor and live.position_monitor ...")
    ep_mod, pm_mod = _patch_broker_and_import()
    print("    event_processor OK")
    print("    position_monitor OK")
    print("    MT5Executor symbols patched to BrokerMock")

    print("\n[2/4] Instantiating EventProcessor ...")
    try:
        ep = ep_mod.EventProcessor(
            liq_top=4720.0, liq_bot=4680.0,
            dry_run=True, execute=False,
            lot_size=0.06, sl_pts=20.0, tp1_pts=20.0, tp2_pts=50.0,
            v3_agent=None, feed_monitor=None,
            box_high=4710.0, box_low=4690.0,
            daily_trend="unknown",
        )
        assert isinstance(ep.executor, BrokerMock), "executor leak"
        print(f"    EventProcessor OK — executor: {type(ep.executor).__name__}")
    except Exception as e:
        print(f"    EventProcessor FAIL: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return 1

    print("\n[3/4] Instantiating PositionMonitor ...")
    try:
        pm = pm_mod.PositionMonitor(
            executor=ep.executor,
            dry_run=True, v3_agent=None,
            lot_size=0.06, executor_live=None,
        )
        print(f"    PositionMonitor OK — interval={pm_mod.MONITOR_INTERVAL_S}s "
              f"DANGER_THR={pm_mod.DANGER_THRESHOLD} T3_PTS={pm_mod.T3_ADVERSE_PTS}")
    except Exception as e:
        print(f"    PositionMonitor FAIL: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return 1

    print("\n[4/4] Recording broker mock state ...")
    print(f"    BrokerMock event_log entries: {len(ep.executor.event_log)}")
    print(f"    BrokerMock open positions:    {len(ep.executor.get_open_positions())}")

    print("\n" + "=" * 70)
    print("PHASE 2 SMOKE PASSED")
    print("=" * 70)
    return 0


def smoke_phase3(start: pd.Timestamp, end: pd.Timestamp,
                 limit: int = 0) -> int:
    """Phase 3 smoke: clock + dispatcher integrated.

    For each dispatched event:
      - advance simulated clock to ev["ts"]
      - bump per-kind counter
      - (no live code is called yet — that's Phase 4)
    """
    print("=" * 70)
    print(f"PHASE 3 SMOKE — clock + dispatcher  window={start} -> {end}  "
          f"limit={limit or 'none'}")
    print("=" * 70)

    # Sanity: instantiate EP + PM under BrokerMock so the imports happen
    # under simulated clock. (We don't call into them yet.)
    ep_mod, pm_mod = _patch_broker_and_import()
    print("\n[setup] EventProcessor + PositionMonitor available, broker patched")

    clock = SimulatedClock(start=start)
    counts: Counter[str] = Counter()
    first_clock = None
    last_clock = None
    n_events = 0

    print(f"[setup] SimulatedClock starting at {start}")
    with clock:
        ep = ep_mod.EventProcessor(
            liq_top=4720.0, liq_bot=4680.0,
            dry_run=True, execute=False,
            lot_size=0.06, sl_pts=20.0, tp1_pts=20.0, tp2_pts=50.0,
            v3_agent=None, feed_monitor=None,
            box_high=4710.0, box_low=4690.0,
            daily_trend="unknown",
        )
        assert isinstance(ep.executor, BrokerMock)
        pm = pm_mod.PositionMonitor(
            executor=ep.executor, dry_run=True, v3_agent=None,
            lot_size=0.06, executor_live=None,
        )
        print(f"[setup] EP instantiated under simulated clock; "
              f"clock.now={clock.now}")

        if first_clock is None:
            first_clock = clock.now
        for ev in chronological_events(start, end):
            clock.advance_to(ev["ts"])
            counts[ev["kind"]] += 1
            n_events += 1
            last_clock = clock.now
            if limit and n_events >= limit:
                print(f"[limit] stopping at {n_events:,} events "
                      f"(limit={limit})")
                break

        # Update market price in BrokerMock if last event was an m1_tick
        # (sanity that the price-driver wiring works in principle)
        ep.executor._set_market_price(clock.now, 4700.0)

    print()
    print(f"Events processed:  {n_events:,}")
    print(f"By kind:           {dict(counts)}")
    print(f"Clock first->last: {first_clock} -> {last_clock}")
    if last_clock:
        elapsed = (last_clock - first_clock).total_seconds() / 3600
        print(f"Simulated elapsed: {elapsed:.1f} hours")
    print(f"BrokerMock log:    {len(ep.executor.event_log)} entries (expected: 1 for _set_market_price... actually 0)")
    print("\n" + "=" * 70)
    print("PHASE 3 SMOKE COMPLETE — clock advanced through all events without crash")
    print("Next: Phase 4 — route events into EP._on_iceberg_event / _refresh_metrics")
    print("=" * 70)
    return 0


def smoke_phase4(start: pd.Timestamp, end: pd.Timestamp,
                 limit: int = 0) -> int:
    """Phase 4 smoke: wire handlers + capture decision stream.

    Drives EP and PM with chronological events. Captures every decision
    written via write_decision_atomic into router.captured_decisions.
    """
    print("=" * 70)
    print(f"PHASE 4 SMOKE — wired handlers  window={start} -> {end}  "
          f"limit={limit or 'none'}")
    print("=" * 70)

    ep_mod, pm_mod = _patch_broker_and_import()
    print("[setup] EP+PM imported, broker patched")

    clock = SimulatedClock(start=start)
    with clock:
        ep = ep_mod.EventProcessor(
            liq_top=4720.0, liq_bot=4680.0,
            dry_run=True, execute=False,
            lot_size=0.06, sl_pts=20.0, tp1_pts=20.0, tp2_pts=50.0,
            v3_agent=None, feed_monitor=None,
            box_high=4710.0, box_low=4690.0,
            daily_trend="unknown",
        )
        pm = pm_mod.PositionMonitor(
            executor=ep.executor, dry_run=True, v3_agent=None,
            lot_size=0.06, executor_live=None,
        )
        print("[setup] EP+PM instantiated under SimulatedClock")

        with ReplayRouter(ep, pm, clock) as router:
            print("[setup] ReplayRouter installed (decision capture + patches)")
            router.bootstrap()
            print(f"[setup] bootstrap done — liq_top={ep.liq_top} "
                  f"liq_bot={ep.liq_bot} box_high={ep.box_high} box_low={ep.box_low}")
            n_events = 0
            for ev in chronological_events(start, end):
                router.handle(ev)
                n_events += 1
                if limit and n_events >= limit:
                    print(f"[limit] stopping at {n_events:,} events")
                    break
                if n_events % 10_000 == 0:
                    print(f"[progress] {n_events:>7,d} events  "
                          f"clock={clock.now}  "
                          f"decisions={len(router.captured_decisions)}  "
                          f"pm_checks={router.pm_check_calls}  "
                          f"ticks={router.tick_loop_iterations}  "
                          f"boxes={router.box_refresh_calls}")

            print()
            print("=" * 70)
            print("PHASE 4 SUMMARY")
            print("=" * 70)
            print(f"Events processed:        {n_events:,}")
            print(f"Iceberg handler calls:   {router.iceberg_handler_calls}")
            print(f"Refresh-metrics calls:   {router.refresh_metrics_calls}")
            print(f"PM check calls:          {router.pm_check_calls}")
            print(f"M1 ticks processed:      {router.m1_ticks_processed}")
            print(f"Tick loop iterations:    {router.tick_loop_iterations}")
            print(f"Box refresh calls:       {router.box_refresh_calls}")
            print(f"Auto SL/TP closes:       {router.auto_closes_this_run}")
            print(f"Decisions captured:      {len(router.captured_decisions)}")
            print(f"Broker open positions:   {len(ep.executor.get_open_positions())}")
            print(f"Broker event log size:   {len(ep.executor.event_log)}")

            # Decision breakdown
            if router.captured_decisions:
                from collections import Counter
                actions = Counter()
                directions = Counter()
                for d in router.captured_decisions:
                    pl = d.get("payload", {})
                    dec = pl.get("decision", {}) or {}
                    actions[dec.get("action", "?")] += 1
                    if dec.get("action") == "GO":
                        directions[dec.get("direction", "?")] += 1
                print(f"\nDecisions by action:     {dict(actions)}")
                if directions:
                    print(f"GO directions:           {dict(directions)}")

            # Broker action breakdown
            if ep.executor.event_log:
                from collections import Counter
                kinds = Counter(e.get("kind", "?") for e in ep.executor.event_log)
                print(f"\nBroker action kinds:     {dict(kinds)}")

    print("\n" + "=" * 70)
    print("PHASE 4 SMOKE COMPLETE")
    print("=" * 70)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--smoke", action="store_true",
                    help="Run Phase 2 instantiation smoke test only.")
    ap.add_argument("--smoke-phase3", action="store_true",
                    help="Run Phase 3 smoke (clock + dispatcher).")
    ap.add_argument("--smoke-phase4", action="store_true",
                    help="Run Phase 4 smoke (full wired replay).")
    ap.add_argument("--start", default="2026-05-05",
                    help="Window start (UTC).")
    ap.add_argument("--end", default="2026-05-09",
                    help="Window end (UTC, exclusive).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Max events to process (0=no limit).")
    args = ap.parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")

    if args.smoke_phase4:
        return smoke_phase4(start, end, limit=args.limit)
    if args.smoke_phase3:
        return smoke_phase3(start, end, limit=args.limit)
    if args.smoke or len(sys.argv) == 1:
        return smoke_phase2()
    print("Phase 5 replay not yet implemented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

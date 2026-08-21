"""Phase 4: route dispatcher events into live event_processor + position_monitor.

This module owns:
  - Decision capture: patches write_decision_atomic so every decision the
    live code emits during replay is appended to an in-memory list (and
    NOT written to the production decision_live.json / decision_log.jsonl).
  - Microstructure handling: feeds the live `_refresh_metrics()` from a
    rate-limited buffer instead of the watchdog flag.
  - Iceberg handling: forwards directly to `EventProcessor._on_iceberg_event`.
  - M1 tick handling: updates BrokerMock market price, drives a PM check
    loop at the right cadence relative to simulated clock, and runs ONE
    iteration of EP._tick_loop body so ALPHA/BETA/GAMMA/DELTA gates
    evaluate against the just-set price.
  - Time-aware data filters: patches _micro_path / _read_micro_tail /
    _get_m30_snapshot_from_parquet so the imported live code only sees data
    AT OR BEFORE the simulated clock (otherwise a query at simulated 12:00
    would see the entire day's worth of CSV/parquet rows from the future).
  - Box refresh: every M5 minute boundary, reads gc_m5_boxes.parquet +
    gc_m30_boxes.parquet at simulated clock and updates EP's liq_top_gc /
    liq_bot_gc / box_high_gc / box_low_gc + MT5-space derivations. Without
    this, the EP keeps stale levels from __init__ and proximity gates
    never trigger.
  - Defense mode neutralization: replay rebuilds DefenseMode baseline from
    a single sequential pass which produces artificial z-score spikes; we
    force defense_tier=NORMAL after each refresh to avoid spurious
    ENTRY_BLOCK gates. Real defense behavior is tested in production
    monitoring; replay focus is on signal logic.

Callers:
    from .replay_handlers import ReplayRouter
    router = ReplayRouter(ep, pm, clock)
    router.install()
    for ev in chronological_events(...):
        router.handle(ev)
    router.uninstall()
    decisions = router.captured_decisions
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger("replay.handlers")

# Cadence: how often to call EP._refresh_metrics() during replay.
# Real system: refresh on _micro_dirty event (typically 1-2s in live). For
# replay we throttle to every N microstructure rows to keep wall-clock cost
# acceptable. 30 rows ~ 30 seconds of L2 data.
MICRO_REFRESH_EVERY_N_ROWS = 30

# Cadence: how often to call PM._run_checks() during replay (simulated seconds
# between calls). Real system runs every 2.0s. We honor that.
PM_CHECK_INTERVAL_S = 2.0


class ReplayRouter:
    """Routes chronological events into the live EP/PM and captures decisions."""

    def __init__(self, ep, pm, clock, repo_root: Path = Path("C:/FluxQuantumAI")) -> None:
        self.ep = ep
        self.pm = pm
        self.clock = clock
        self.repo_root = repo_root

        # Microstructure buffer: rows accumulated, fed to EP at refresh cadence
        self._micro_buffer: list[dict] = []
        self._micro_rows_since_refresh = 0

        # PM check loop driver
        self._pm_last_check_ts: pd.Timestamp | None = None

        # Capture state
        self.captured_decisions: list[dict] = []
        self.iceberg_handler_calls = 0
        self.refresh_metrics_calls = 0
        self.pm_check_calls = 0
        self.m1_ticks_processed = 0
        self.tick_loop_iterations = 0
        self.box_refresh_calls = 0
        self._last_box_refresh_min: int = -1
        self.auto_closes_this_run = 0

        # Patch state
        self._installed = False
        self._orig_write_decision_atomic = None
        self._orig_micro_path = None
        self._orig_read_micro_tail = None
        self._orig_get_m30_snapshot = None
        self._orig_load_trades = None
        self._orig_pm_load_trades = None
        self._tempfile_path: Path | None = None

    # ---------- patch install / uninstall ----------
    def install(self) -> None:
        if self._installed:
            return

        from live import decision_writer as dw_mod
        from live import event_processor as ep_mod
        from live import position_monitor as pm_mod

        # ===== 0. Disable optional integrations that are noisy or block in replay =====
        # _news_gate hits tradingeconomics.com (410 Gone in this env) on every
        # tick → kills replay perf. _guardrail logs STALE_DATA every cycle
        # because L2 latency is computed off wall-clock vs simulated. Defense
        # mode is already neutralized in _trigger_refresh_metrics.
        try:
            ep_mod._news_gate = None  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            ep_mod._guardrail_available = False  # type: ignore[attr-defined]
            ep_mod._update_guardrail = None  # type: ignore[attr-defined]
            ep_mod._get_guardrail_status = None  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            ep_mod._defense_mode = None  # type: ignore[attr-defined]
            ep_mod._defense_available = False  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            ep_mod._NEWS_STATE = None  # type: ignore[attr-defined]
            ep_mod._news_state_available = False  # type: ignore[attr-defined]
        except Exception:
            pass

        # ===== 1. Decision capture =====
        self._orig_write_decision_atomic = dw_mod.write_decision_atomic

        def _capture_write_decision(payload: dict, *, append_log: bool = True) -> bool:
            self.captured_decisions.append({
                "captured_at_simclock": self.clock.now.isoformat(),
                "payload": payload,
            })
            return True  # short-circuit; do NOT write to production logs

        dw_mod.write_decision_atomic = _capture_write_decision  # type: ignore[assignment]
        # event_processor imported the symbol at module load; rebind there too:
        ep_mod._write_decision_atomic = _capture_write_decision  # type: ignore[attr-defined]

        # ===== 2. Microstructure data filter (time-aware) =====
        # Strategy: write our buffer to a tempfile after each batch refresh;
        # _micro_path() returns that tempfile; live code reads naturally.
        # The buffer only grows monotonically with simulated time, so the
        # tempfile content is always <= simclock. No need to filter on read.
        self._tempfile_path = (self.repo_root / "_audit/backtest/replay_real_may0508"
                               / "_replay_micro_buffer.csv")
        self._tempfile_path.parent.mkdir(parents=True, exist_ok=True)
        if self._tempfile_path.exists():
            self._tempfile_path.unlink()

        tempfile_path = self._tempfile_path

        self._orig_micro_path = ep_mod._micro_path if hasattr(ep_mod, "_micro_path") else None

        def _patched_micro_path_ep(self_ep) -> Path | None:
            return tempfile_path if tempfile_path.exists() else None

        # event_processor._micro_path is a method on EventProcessor
        ep_mod.EventProcessor._micro_path = _patched_micro_path_ep  # type: ignore[assignment]

        # position_monitor has module-level _micro_path() function and
        # _read_micro_tail() that uses it
        self._orig_pm_micro_path = pm_mod._micro_path
        self._orig_read_micro_tail = pm_mod._read_micro_tail

        def _patched_pm_micro_path() -> Path | None:
            return tempfile_path if tempfile_path.exists() else None

        def _patched_read_micro_tail() -> pd.DataFrame | None:
            if not tempfile_path.exists():
                return None
            try:
                cols = ["timestamp", "mid_price", "bar_delta",
                        "dom_imbalance", "large_order_imbalance"]
                df = pd.read_csv(tempfile_path, usecols=cols)
                if df.empty:
                    return None
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                # Live code tails MAX_MICRO_ROWS=5400 — preserve that
                MAX_MICRO_ROWS = pm_mod.MAX_MICRO_ROWS
                return df.tail(MAX_MICRO_ROWS).reset_index(drop=True)
            except Exception as e:
                log.warning("patched _read_micro_tail error: %s", e)
                return None

        pm_mod._micro_path = _patched_pm_micro_path  # type: ignore[assignment]
        pm_mod._read_micro_tail = _patched_read_micro_tail  # type: ignore[assignment]

        # ===== 3. M30 snapshot filter (time-aware) =====
        clock_ref = self.clock
        self._orig_get_m30_snapshot = pm_mod._get_m30_snapshot_from_parquet

        def _patched_get_m30_snapshot() -> dict:
            """Read m30_boxes.parquet and filter to rows <= simulated clock."""
            try:
                df = pd.read_parquet("C:/data/processed/gc_m30_boxes.parquet")
                df.index = pd.to_datetime(df.index, utc=True)
                df = df[df.index <= clock_ref.now]
                if df.empty:
                    return {"bias": "unknown", "liq_top": None, "liq_bot": None,
                            "box_high": None, "box_low": None, "fmv": None,
                            "confirmed": False}
                last = df.iloc[-1]
                return {
                    "bias": str(last.get("m30_bias", "unknown")) or "unknown",
                    "liq_top": float(last.get("m30_liq_top", 0)) or None,
                    "liq_bot": float(last.get("m30_liq_bot", 0)) or None,
                    "box_high": float(last.get("m30_box_high", 0)) or None,
                    "box_low": float(last.get("m30_box_low", 0)) or None,
                    "fmv": float(last.get("m30_fmv", 0)) or None,
                    "confirmed": bool(last.get("m30_box_confirmed", False)),
                }
            except Exception as e:
                log.warning("patched m30 snapshot error: %s", e)
                return {"bias": "unknown", "liq_top": None, "liq_bot": None,
                        "box_high": None, "box_low": None, "fmv": None,
                        "confirmed": False}

        pm_mod._get_m30_snapshot_from_parquet = _patched_get_m30_snapshot  # type: ignore[assignment]

        # ===== 4. Trades loader: synthesize from BrokerMock state =====
        broker = self.ep.executor

        self._orig_pm_load_trades = pm_mod._load_trades

        def _patched_load_trades() -> list[dict]:
            # Build trade record list from BrokerMock event_log open_position
            # entries. PM uses these for entry_mode/daily_trend lookup per leg.
            trades: list[dict] = []
            for ev in broker.event_log:
                if ev.get("kind") != "open_position":
                    continue
                tickets = ev.get("tickets") or []
                for i, tkt in enumerate(tickets):
                    if not tkt:
                        continue
                    trades.append({
                        "leg1_ticket": tickets[0] if len(tickets) > 0 else 0,
                        "leg2_ticket": tickets[1] if len(tickets) > 1 else 0,
                        "leg3_ticket": tickets[2] if len(tickets) > 2 else 0,
                        "direction": ev.get("direction", ""),
                        "entry": ev.get("entry", 0),
                        "sl": ev.get("sl", 0),
                        "tp1": ev.get("tp1", 0),
                        "tp2": ev.get("tp2", 0),
                        "entry_mode": "",  # not set at open in current EP
                        "daily_trend": "",
                        "phase": "",
                        "strategy_mode": "",
                    })
                    break  # one record per open_position covers all legs
            return trades

        pm_mod._load_trades = _patched_load_trades  # type: ignore[assignment]

        # ===== 5. service_state.json: route to in-memory ====
        # Some PM methods (T3) read SERVICE_STATE_PATH directly. Easiest:
        # write a stub file under our control; populate from EP metrics on
        # each refresh.
        self._service_state_stub = (self.repo_root / "_audit/backtest/replay_real_may0508"
                                    / "_replay_service_state.json")
        pm_mod.SERVICE_STATE_PATH = self._service_state_stub  # type: ignore[assignment]
        self._write_service_state_stub({"defense_tier": "NORMAL",
                                        "stress_direction": "HOLD"})

        self._installed = True

    def bootstrap(self) -> None:
        """Mirror EventProcessor.start() initialization without launching threads.

        Called after install() and before iterating dispatcher events. Sets
        liq levels from parquet at simulated start time.
        """
        # Initial level load from parquet
        self._refresh_levels_from_parquet()
        # Initial macro context
        try:
            self.ep.refresh_macro_context(reason="replay-bootstrap")
        except Exception as e:
            log.debug("refresh_macro_context bootstrap error: %s", e)

    def uninstall(self) -> None:
        if not self._installed:
            return
        from live import decision_writer as dw_mod
        from live import event_processor as ep_mod
        from live import position_monitor as pm_mod

        if self._orig_write_decision_atomic is not None:
            dw_mod.write_decision_atomic = self._orig_write_decision_atomic
            ep_mod._write_decision_atomic = self._orig_write_decision_atomic
        if self._orig_pm_micro_path is not None:
            pm_mod._micro_path = self._orig_pm_micro_path
        if self._orig_read_micro_tail is not None:
            pm_mod._read_micro_tail = self._orig_read_micro_tail
        if self._orig_get_m30_snapshot is not None:
            pm_mod._get_m30_snapshot_from_parquet = self._orig_get_m30_snapshot
        if self._orig_pm_load_trades is not None:
            pm_mod._load_trades = self._orig_pm_load_trades
        # Remove tempfile
        if self._tempfile_path and self._tempfile_path.exists():
            try:
                self._tempfile_path.unlink()
            except Exception:
                pass
        if self._service_state_stub and self._service_state_stub.exists():
            try:
                self._service_state_stub.unlink()
            except Exception:
                pass
        self._installed = False

    def __enter__(self):
        self.install()
        return self

    def __exit__(self, *_):
        self.uninstall()

    # ---------- helpers ----------
    def _write_service_state_stub(self, state: dict) -> None:
        import json
        if self._service_state_stub:
            try:
                self._service_state_stub.parent.mkdir(parents=True, exist_ok=True)
                self._service_state_stub.write_text(json.dumps(state),
                                                     encoding="utf-8")
            except Exception as e:
                log.warning("service_state stub write error: %s", e)

    def _flush_micro_buffer_to_tempfile(self) -> None:
        """Append accumulated buffer rows to the tempfile."""
        if not self._micro_buffer or not self._tempfile_path:
            return
        df = pd.DataFrame(self._micro_buffer)
        write_header = not self._tempfile_path.exists()
        df.to_csv(self._tempfile_path, mode="a", header=write_header, index=False)
        self._micro_buffer.clear()

    def _trigger_refresh_metrics(self) -> None:
        """Flush buffer + call EP._refresh_metrics() so live metrics state updates.

        Updates service_state stub from the refreshed metrics (defense_tier).
        Also forces defense_tier=NORMAL because replay z-scores are
        artificially distorted by sequential baseline construction.
        """
        self._flush_micro_buffer_to_tempfile()
        try:
            self.ep._refresh_metrics()
            self.refresh_metrics_calls += 1
        except Exception as e:
            log.warning("EP._refresh_metrics error: %s", e)
            return

        # Force defense to NORMAL — replay baseline is unrepresentative
        try:
            with self.ep._lock:
                self.ep._metrics["defense_tier"] = "NORMAL"
                self.ep._metrics["stress_direction"] = "HOLD"
                self.ep._metrics["defense_mode"] = False
        except Exception:
            pass

        # Mirror EP defense state into service_state stub for PM T3 check
        try:
            self._write_service_state_stub({
                "defense_tier": "NORMAL",
                "stress_direction": "HOLD",
            })
        except Exception:
            pass

    def _refresh_levels_from_parquet(self) -> None:
        """Read M5 + M30 boxes at simulated clock and update EP liq levels.

        Mirrors what production live does on every M5 box close (M5 update
        → m5_updater.py daemon writes parquet → EP picks up). In replay the
        parquet is static and complete; we read at simulated time.
        """
        clock_now = self.clock.now
        try:
            df30 = pd.read_parquet("C:/data/processed/gc_m30_boxes.parquet")
            df30.index = pd.to_datetime(df30.index, utc=True)
            df30 = df30[df30.index <= clock_now]
            df5 = pd.read_parquet("C:/data/processed/gc_m5_boxes.parquet")
            df5.index = pd.to_datetime(df5.index, utc=True)
            df5 = df5[df5.index <= clock_now]
        except Exception as e:
            log.debug("box parquet read error: %s", e)
            return

        ep = self.ep
        # Prefer M5 (tighter), fall back to M30
        last5 = df5.iloc[-1] if not df5.empty else None
        last30 = df30.iloc[-1] if not df30.empty else None

        liq_top = liq_bot = box_high = box_low = None
        if last5 is not None:
            liq_top = float(last5.get("m5_liq_top", 0)) or None
            liq_bot = float(last5.get("m5_liq_bot", 0)) or None
            box_high = float(last5.get("m5_box_high", 0)) or None
            box_low = float(last5.get("m5_box_low", 0)) or None
        if (liq_top is None or liq_bot is None) and last30 is not None:
            liq_top = liq_top or float(last30.get("m30_liq_top", 0)) or None
            liq_bot = liq_bot or float(last30.get("m30_liq_bot", 0)) or None
            box_high = box_high or float(last30.get("m30_box_high", 0)) or None
            box_low = box_low or float(last30.get("m30_box_low", 0)) or None

        if liq_top is None or liq_bot is None:
            return

        offset = ep._gc_xauusd_offset
        with ep._lock:
            ep.liq_top_gc = liq_top
            ep.liq_bot_gc = liq_bot
            ep.box_high_gc = box_high
            ep.box_low_gc = box_low
            ep.liq_top = round(liq_top - offset, 2)
            ep.liq_bot = round(liq_bot - offset, 2)
            ep.box_high = round(box_high - offset, 2) if box_high else None
            ep.box_low = round(box_low - offset, 2) if box_low else None
        self.box_refresh_calls += 1

    def _drive_one_tick_loop(self) -> None:
        """Run ONE iteration of EP._tick_loop body so gates evaluate.

        Strategy: monkey-patch time.sleep within the run to set
        ep._running=False after the first body iteration completes.

        Suppresses stdout during the iteration to keep replay output
        manageable across thousands of iterations.
        """
        import os
        import sys
        import time as _time
        ep = self.ep
        ep._running = True
        orig_sleep = _time.sleep
        orig_stdout = sys.stdout

        def _stop_after_one(_secs):
            ep._running = False

        _time.sleep = _stop_after_one  # type: ignore[assignment]
        try:
            with open(os.devnull, "w") as devnull:
                sys.stdout = devnull
                ep._tick_loop()
            self.tick_loop_iterations += 1
        except Exception as e:
            log.debug("EP._tick_loop iter error at %s: %s", self.clock.now, e)
        finally:
            sys.stdout = orig_stdout
            _time.sleep = orig_sleep
            ep._running = False

    def _drive_pm_loop(self, until_ts: pd.Timestamp) -> None:
        """Call PM._run_checks() at PM_CHECK_INTERVAL_S cadence up to until_ts."""
        if self._pm_last_check_ts is None:
            self._pm_last_check_ts = until_ts
            return
        delta = (until_ts - self._pm_last_check_ts).total_seconds()
        n_calls = int(delta // PM_CHECK_INTERVAL_S)
        for _ in range(n_calls):
            self._pm_last_check_ts = self._pm_last_check_ts + pd.Timedelta(
                seconds=PM_CHECK_INTERVAL_S)
            self.clock.advance_to(self._pm_last_check_ts)
            try:
                self.pm._run_checks()
                self.pm_check_calls += 1
            except Exception as e:
                log.debug("PM _run_checks error at %s: %s",
                          self.pm_last_check_ts, e)

    # ---------- main entry ----------
    def handle(self, ev: dict) -> None:
        """Route a single dispatcher event."""
        ts = ev["ts"]
        kind = ev["kind"]
        row = ev["row"]

        # Always advance clock before executing handlers
        self.clock.advance_to(ts)

        if kind == "iceberg":
            try:
                self.ep._on_iceberg_event(row)
                self.iceberg_handler_calls += 1
            except Exception as e:
                log.debug("EP._on_iceberg_event error at %s: %s", ts, e)

        elif kind == "microstructure":
            self._micro_buffer.append(row)
            self._micro_rows_since_refresh += 1
            if self._micro_rows_since_refresh >= MICRO_REFRESH_EVERY_N_ROWS:
                self._micro_rows_since_refresh = 0
                self._trigger_refresh_metrics()

        elif kind == "m1_tick":
            # Advance broker against the bar — auto-close on SL/TP hit.
            # MT5 spot is in XAUUSD space; M1 parquet is GC space. Convert.
            offset = self.ep._gc_xauusd_offset
            bar_high = float(row["high"]) - offset
            bar_low = float(row["low"]) - offset
            bar_close = float(row["close"]) - offset
            closed = self.ep.executor._advance_to_m1_bar(
                ts, bar_high, bar_low, bar_close,
            )
            self.m1_ticks_processed += 1
            if closed:
                self.auto_closes_this_run += len(closed)

            # Refresh M5/M30 levels every 5 minutes of simulated time
            cur_min = ts.minute
            if cur_min % 5 == 0 and cur_min != self._last_box_refresh_min:
                self._last_box_refresh_min = cur_min
                self._refresh_levels_from_parquet()

            # Drive PM checks at 2s cadence up to this M1 close
            self._drive_pm_loop(ts)

            # Run one iteration of EP tick loop body so gates evaluate
            # the just-set price against current liq levels
            self._drive_one_tick_loop()

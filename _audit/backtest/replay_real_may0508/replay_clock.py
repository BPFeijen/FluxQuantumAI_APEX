"""SimulatedClock: makes the imported live code see virtual time.

Why we need this:
  - event_processor.py uses ``time.time()`` for cooldowns (GATE_COOLDOWN_S,
    DIRECTION_LOCK_S, OFFSET_REFRESH_S, etc.) and ``time.monotonic()`` for
    metric refresh cadence.
  - Several functions call ``pd.Timestamp.utcnow()`` (e.g. _refresh_metrics
    cutoffs at line 1478) and ``datetime.now(timezone.utc)`` (e.g. trade
    timestamps).
  - position_monitor.py uses ``time.time()`` for T3 cooldowns and price
    history windowing, and ``datetime.now(timezone.utc)`` for log lines.

If we leave wall-clock active, the live code sees real-time deltas (e.g.
"4 hours ago" cutoffs reach into 2026-05-10 wall time, and reject every
single replayed microstructure row as "too old"). The cleanest fix is to
patch the time primitives globally before the live modules consume them.

Usage:
    clock = SimulatedClock(start=pd.Timestamp("2026-05-05", tz="UTC"))
    clock.install()                # monkey-patch globally
    try:
        # ... run replay ...
        clock.advance_to(some_ts)  # virtual time = some_ts
    finally:
        clock.uninstall()          # restore real time

Restores real wall-clock on uninstall(), so the same Python process can
keep running after the replay if needed.
"""
from __future__ import annotations

import datetime as _dt_mod
import threading
import time as _time_mod
from typing import Optional

import pandas as pd


class SimulatedClock:
    """Drives a virtual clock for replay. Thread-safe."""

    def __init__(self, start: pd.Timestamp) -> None:
        if start.tz is None:
            start = start.tz_localize("UTC")
        self._lock = threading.RLock()
        self._now: pd.Timestamp = start
        self._installed = False

        # Stash originals so uninstall() restores them
        self._orig_time = _time_mod.time
        self._orig_monotonic = _time_mod.monotonic
        self._orig_datetime_class = _dt_mod.datetime
        self._orig_pd_utcnow = pd.Timestamp.utcnow

    # ------------- driver -------------
    @property
    def now(self) -> pd.Timestamp:
        with self._lock:
            return self._now

    def advance_to(self, ts: pd.Timestamp) -> None:
        with self._lock:
            if ts.tz is None:
                ts = ts.tz_localize("UTC")
            if ts < self._now:
                # Never go backwards (would corrupt cooldown windows in live code)
                return
            self._now = ts

    def advance_by(self, seconds: float) -> None:
        with self._lock:
            self._now = self._now + pd.Timedelta(seconds=seconds)

    # ------------- monkey-patch helpers -------------
    def _epoch_seconds(self) -> float:
        with self._lock:
            return float(self._now.timestamp())

    def _datetime_now(self, tz=None) -> _dt_mod.datetime:
        with self._lock:
            ts = self._now
        # Build a real datetime with the simulated time
        py_dt = ts.to_pydatetime()
        if tz is None:
            return py_dt.replace(tzinfo=None)
        return py_dt.astimezone(tz)

    def _datetime_utcnow(self) -> _dt_mod.datetime:
        with self._lock:
            ts = self._now
        return ts.to_pydatetime().replace(tzinfo=None)  # naive UTC, mimics datetime.utcnow

    def _pd_timestamp_utcnow(cls_self_ignored=None) -> pd.Timestamp:  # type: ignore[no-untyped-def]
        # Will be bound below; stand-in to satisfy module-level type hints.
        raise RuntimeError("not installed")

    # ------------- install / uninstall -------------
    def install(self) -> None:
        if self._installed:
            return
        clock = self  # capture for closures

        # Patch time module
        def _patched_time() -> float:
            return clock._epoch_seconds()

        # monotonic must remain monotonic. We anchor it at the simulated clock
        # so cooldowns based on monotonic deltas honor virtual time advances.
        def _patched_monotonic() -> float:
            return clock._epoch_seconds()

        _time_mod.time = _patched_time          # type: ignore[assignment]
        _time_mod.monotonic = _patched_monotonic  # type: ignore[assignment]

        # Patch datetime class — both `datetime.now` and `datetime.utcnow`.
        # We can't replace _dt_mod.datetime wholesale (breaks isinstance
        # checks), so we override the class methods via a subclass and swap.
        orig_dt = self._orig_datetime_class

        class _SimDateTime(orig_dt):  # type: ignore[misc,valid-type]
            @classmethod
            def now(cls, tz=None):
                return clock._datetime_now(tz)

            @classmethod
            def utcnow(cls):
                return clock._datetime_utcnow()

        _dt_mod.datetime = _SimDateTime  # type: ignore[misc]

        # Patch pd.Timestamp.utcnow — return simulated UTC Timestamp.
        def _patched_pd_utcnow():
            with clock._lock:
                return clock._now

        pd.Timestamp.utcnow = staticmethod(_patched_pd_utcnow)  # type: ignore[assignment]

        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        _time_mod.time = self._orig_time            # type: ignore[assignment]
        _time_mod.monotonic = self._orig_monotonic  # type: ignore[assignment]
        _dt_mod.datetime = self._orig_datetime_class  # type: ignore[misc]
        pd.Timestamp.utcnow = self._orig_pd_utcnow  # type: ignore[assignment]
        self._installed = False

    def __enter__(self) -> "SimulatedClock":
        self.install()
        return self

    def __exit__(self, *_) -> None:
        self.uninstall()


# ------------- smoke -------------
def _smoke() -> int:
    import datetime as dt
    start = pd.Timestamp("2026-05-05 12:00:00", tz="UTC")
    clock = SimulatedClock(start=start)

    print("Before install:")
    print(f"  time.time():               {_time_mod.time():.0f}")
    print(f"  datetime.now(UTC):         {dt.datetime.now(dt.timezone.utc)}")
    print(f"  pd.Timestamp.utcnow():     {pd.Timestamp.utcnow()}")
    print()

    with clock:
        print(f"Installed at {start}:")
        print(f"  time.time():               {_time_mod.time():.0f}  (epoch of simulated)")
        print(f"  datetime.now(UTC):         {dt.datetime.now(dt.timezone.utc)}")
        print(f"  pd.Timestamp.utcnow():     {pd.Timestamp.utcnow()}")
        print(f"  time.monotonic():          {_time_mod.monotonic():.0f}")

        clock.advance_by(3600)  # +1h simulated
        print()
        print("After advance_by(3600):")
        print(f"  pd.Timestamp.utcnow():     {pd.Timestamp.utcnow()}")
        print(f"  time.time() delta:         {_time_mod.time() - start.timestamp():.0f}s")

        clock.advance_to(pd.Timestamp("2026-05-08 20:00:00", tz="UTC"))
        print()
        print("After advance_to(2026-05-08 20:00 UTC):")
        print(f"  pd.Timestamp.utcnow():     {pd.Timestamp.utcnow()}")

    print()
    print("After uninstall (real time restored):")
    print(f"  pd.Timestamp.utcnow():     {pd.Timestamp.utcnow()}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_smoke())

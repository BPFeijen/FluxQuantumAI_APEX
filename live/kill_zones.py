#!/usr/bin/env python3
"""
C:\FluxQuantumAI\live\kill_zones.py
ICT Kill Zone detector for GC/XAUUSD.

Kill Zones are time windows of peak institutional activity where:
  - London Open (02:00-05:00 UTC): highest gold volatility; London market-makers
    position after Asian session; prime SHORT setup window for fakeouts
  - New York    (12:00-15:00 UTC): NY-London overlap; institutional distribution;
    prime entry window for trend trades with London
  - Asian       (20:00-02:00 UTC): accumulation; Judas swing (fake moves that
    will be reversed in London/NY); useful for PO3 context

Kill Zone status is telemetry-only -- NOT a hard gate. Gates may use it as a
bonus score or context signal. Usage: call kill_zone_label() in trigger_gate
to add KZ info to the gate telemetry line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Zone definitions
# UTC (name, start_hour_inclusive, end_hour_exclusive)
# ---------------------------------------------------------------------------
# NOTE: Asian spans midnight -- represented as two entries.
_ZONES: list[tuple[str, int, int]] = [
    ("asian",        20, 24),   # 20:00-00:00 UTC  (NY close / Asia open)
    ("asian",         0,  2),   # 00:00-02:00 UTC  (Asia continuation)
    ("london_open",   2,  5),   # 02:00-05:00 UTC
    ("new_york",     12, 15),   # 12:00-15:00 UTC  (London/NY overlap)
]


@dataclass
class KillZoneStatus:
    active: bool
    name: str               # "" when inactive
    start_utc: int          # start hour of the active zone
    end_utc: int            # end hour of the active zone (exclusive, 24 means midnight)
    minutes_remaining: float  # minutes until zone closes (0 when inactive)


def current_kill_zone(now: Optional[datetime] = None) -> KillZoneStatus:
    """
    Return the currently active Kill Zone (or inactive status).
    Pass `now` for testing; defaults to UTC wall clock.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    h = now.hour

    for name, start, end in _ZONES:
        if start <= h < end:
            # Compute minutes remaining until zone end
            if end == 24:
                # Zone ends at midnight -- compute relative to next midnight
                next_midnight = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                mins_remaining = (next_midnight - now).total_seconds() / 60.0
            else:
                end_dt = now.replace(hour=end, minute=0, second=0, microsecond=0)
                mins_remaining = (end_dt - now).total_seconds() / 60.0
            return KillZoneStatus(
                active=True,
                name=name,
                start_utc=start,
                end_utc=end,
                minutes_remaining=max(0.0, round(mins_remaining, 1)),
            )

    return KillZoneStatus(
        active=False, name="", start_utc=0, end_utc=0, minutes_remaining=0.0
    )


def kill_zone_label(now: Optional[datetime] = None) -> str:
    """
    Compact label for gate telemetry:
      active  -> "KZ:london_open(47min)"
      inactive -> "KZ:off"
    """
    kz = current_kill_zone(now)
    if kz.active:
        return f"KZ:{kz.name}({kz.minutes_remaining:.0f}min)"
    return "KZ:off"


def is_in_kill_zone(now: Optional[datetime] = None) -> bool:
    """True if current time is inside any Kill Zone."""
    return current_kill_zone(now).active


def is_in_kill_zone_named(name: str, now: Optional[datetime] = None) -> bool:
    """True if currently inside the named Kill Zone."""
    kz = current_kill_zone(now)
    return kz.active and kz.name == name

"""
release_monitor.py — APEX GOLD Real-Time Release Monitor
=========================================================
Detecta em tempo real quando um resultado económico é publicado
(ex: NFP, CPI, FOMC) e imprime um banner com:
  - Valor actual vs forecast
  - Surprise (desvio percentual)
  - Direcção esperada para o Gold (BULLISH / BEARISH / NEUTRAL)

Arquitectura — Event-Triggered (não polling cego):
  1. Thread principal acorda NO MOMENTO EXACTO de cada evento agendado
  2. Inicia rapid-poll (a cada RAPID_POLL_S segundos) por até RAPID_TIMEOUT_S
  3. Logo que `actual` aparece na API → banner imediato
  4. Garantia: detecção dentro de ~2–4s após publicação na API

  (vs polling cego a 30s: poderia detectar até 30s tarde — inaceitável)

Uso:
    monitor = ReleaseMonitor(calendar)
    monitor.start()
    # ... sistema a correr ...
    monitor.stop()

    # Integrado no ApexNewsGate (automático):
    gate = ApexNewsGate()
    releases = gate.get_recent_releases()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .economic_calendar import TradingEconomicsCalendar
from .events import EconomicEvent

log = logging.getLogger("apex.release_monitor")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCHEDULE_POLL_S  = 15     # how often to refresh the event schedule (15s)
RAPID_POLL_S     = 2      # seconds between polls once event time fires
RAPID_TIMEOUT_S  = 180    # give up rapid-poll after 3 minutes
PRE_FIRE_S       = 2      # start rapid-poll N seconds BEFORE scheduled time
                           # (absorbs small clock drift, API may publish slightly early)
NEWS_ACTIVE_WINDOW_M = 5  # minutes to keep is_news_active=True after release
MIN_GOLD_IMP     = 1.5    # minimum gold_importance to monitor
MAX_RELEASES_LOG = 20     # keep last N releases in memory

# Grenadier Sprint 4 — surprise labels log (canonical location: APEX_GC_Payroll/data/)
from pathlib import Path
SURPRISES_LOG_PATH = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Payroll\data\surprise_labels.jsonl")


# ---------------------------------------------------------------------------
# NewsState — Feature Flag shared with event_processor.py
# ---------------------------------------------------------------------------
# Usage in event_processor.py:
#   from APEX_GC_News.release_monitor import NEWS_STATE
#   state = NEWS_STATE.check()
#   if state["is_news_active"]:
#       ... veto or reduce size ...

class NewsState:
    """
    Thread-safe feature flag.

    Activated by ReleaseMonitor._on_release() at the moment actual appears.
    Stays active for NEWS_ACTIVE_WINDOW_M minutes.

    NOTE: This flag is NOT used to fire orders — API latency (~2-4s after
    publication) is acceptable for risk control but not for execution.
    Execution remains on the existing Quantower/dxfeed microstructure path.
    """

    def __init__(self):
        self._lock         = threading.Lock()
        self.is_news_active: bool                    = False
        self.active_event  : Optional["ReleaseResult"] = None
        self.active_until  : Optional[datetime]       = None

    def activate(self, result: "ReleaseResult", window_minutes: int = NEWS_ACTIVE_WINDOW_M) -> None:
        with self._lock:
            self.is_news_active = True
            self.active_event   = result
            self.active_until   = datetime.now(timezone.utc) + timedelta(minutes=window_minutes)
        log.warning(
            "NEWS_STATE active: %s (%s %s) — window=%dm",
            result.event_name, result.gold_strength, result.gold_signal, window_minutes,
        )

    def deactivate(self) -> None:
        with self._lock:
            self.is_news_active = False
            self.active_event   = None
            self.active_until   = None

    def check(self) -> dict:
        """
        Returns current state dict. Auto-expires after window_minutes.
        Call this from event_processor._trigger_gate() as PONTO -1.
        """
        with self._lock:
            if self.is_news_active and self.active_until:
                if datetime.now(timezone.utc) > self.active_until:
                    self.is_news_active = False
                    self.active_event   = None
                    self.active_until   = None
            ev = self.active_event
            return {
                "is_news_active" : self.is_news_active,
                "event_name"     : ev.event_name      if ev else None,
                "event_type"     : ev.event_type      if ev else None,
                "gold_signal"    : ev.gold_signal      if ev else None,
                "gold_strength"  : ev.gold_strength    if ev else None,
                "surprise_pct"   : ev.surprise_pct     if ev else None,
                "surprise_label" : ev.surprise_label   if ev else None,
                "active_until"   : self.active_until.isoformat() if self.active_until else None,
            }


# Module-level singleton — import and use anywhere in the APEX stack
NEWS_STATE = NewsState()


# ---------------------------------------------------------------------------
# Grenadier Sprint 4 — Surprise label logger
# ---------------------------------------------------------------------------

def _log_surprise(result: "ReleaseResult") -> None:
    """
    Append a surprise event to surprise_labels.jsonl.

    Each line is a JSON record with the exact release timestamp and
    all relevant fields. prep_grenadier_v2.py reads this file to
    build chaos windows for the V2 autoencoder training set:
        chaos window = [released_at - 2min, released_at + 2min]
    """
    if result.surprise_label in ("IN-LINE", "N/A", "NO FORECAST"):
        return   # only log genuine surprises

    record = {
        "ts_utc"        : result.released_at.isoformat(),
        "scheduled_utc" : result.scheduled_at.isoformat(),
        "latency_s"     : round(result.latency_s, 1),
        "event_name"    : result.event_name,
        "event_type"    : result.event_type,
        "country"       : result.country,
        "impact"        : result.impact,
        "actual"        : result.actual,
        "forecast"      : result.forecast,
        "previous"      : result.previous,
        "surprise_pct"  : round(result.surprise_pct, 4) if result.surprise_pct else None,
        "surprise_label": result.surprise_label,
        "gold_signal"   : result.gold_signal,
        "gold_strength" : result.gold_strength,
        "gold_importance": result.gold_importance,
    }
    try:
        SURPRISES_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SURPRISES_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        log.info(
            "Surprise logged → %s  [%s | %s %s]",
            SURPRISES_LOG_PATH.name, result.event_type,
            result.gold_strength, result.gold_signal,
        )
    except Exception as e:
        log.warning("Failed to write surprise log: %s", e)


# ---------------------------------------------------------------------------
# Gold direction knowledge base
# ---------------------------------------------------------------------------
# Maps event category → (beat_signal, miss_signal)
_DIRECTION_MAP = {
    "NFP"        : ("BEARISH", "BULLISH"),
    "UNEMPLOYMENT": ("BULLISH", "BEARISH"),
    "CPI"        : ("BEARISH", "BULLISH"),
    "PPI"        : ("BEARISH", "BULLISH"),
    "PCE"        : ("BEARISH", "BULLISH"),
    "GDP"        : ("BEARISH", "BULLISH"),
    "RETAIL_SALES": ("BEARISH", "BULLISH"),
    "ISM"        : ("BEARISH", "BULLISH"),
    "ADP"        : ("BEARISH", "BULLISH"),
    "JOLTS"      : ("BEARISH", "BULLISH"),
    "FOMC"       : ("BEARISH", "BULLISH"),
    "ECB"        : ("NEUTRAL",  "NEUTRAL"),
    "BOJ"        : ("NEUTRAL",  "NEUTRAL"),
    "FED_SPEECH" : ("NEUTRAL",  "NEUTRAL"),
}

SURPRISE_STRONG = 0.10   # > 10% deviation → strong signal
SURPRISE_MODEST = 0.03   # 3-10% → modest signal


# ---------------------------------------------------------------------------
# Value parser
# ---------------------------------------------------------------------------

def _parse_value(s: Optional[str]) -> Optional[float]:
    """Parse TradingEconomics value string to float.
    Handles: "256K"→256.0, "3.5%"→3.5, "1.2B"→1200.0, "" / None→None
    """
    if not s or s.strip() in ("", "N/A", "—", "-", "n/a"):
        return None
    s = s.strip().replace(",", "")
    multiplier = 1.0
    if s.endswith("%"):
        s = s[:-1]
    if s.upper().endswith("K"):
        multiplier = 1.0
        s = s[:-1]
    elif s.upper().endswith("M"):
        multiplier = 1_000.0
        s = s[:-1]
    elif s.upper().endswith("B"):
        multiplier = 1_000_000.0
        s = s[:-1]
    try:
        return float(s) * multiplier
    except ValueError:
        return None


def _classify_event_type(name: str) -> str:
    """Map event name → category key in _DIRECTION_MAP."""
    n = name.lower()
    if any(kw in n for kw in ["nonfarm", "non-farm", "payroll", "nfp"]):
        return "NFP"
    if "unemployment rate" in n:
        return "UNEMPLOYMENT"
    if any(kw in n for kw in ["fomc", "federal reserve", "fed rate",
                               "interest rate decision", "fed funds"]):
        return "FOMC"
    if any(kw in n for kw in ["consumer price", " cpi"]):
        return "CPI"
    if any(kw in n for kw in ["producer price", " ppi"]):
        return "PPI"
    if any(kw in n for kw in ["personal consumption", " pce"]):
        return "PCE"
    if any(kw in n for kw in ["gdp", "gross domestic"]):
        return "GDP"
    if "retail sales" in n:
        return "RETAIL_SALES"
    if any(kw in n for kw in ["ism ", "pmi", "purchasing managers"]):
        return "ISM"
    if any(kw in n for kw in ["adp", "adp employment"]):
        return "ADP"
    if any(kw in n for kw in ["jolts", "job opening"]):
        return "JOLTS"
    if any(kw in n for kw in ["ecb", "european central"]):
        return "ECB"
    if any(kw in n for kw in ["boj", "bank of japan"]):
        return "BOJ"
    if any(kw in n for kw in ["powell", "fed chair", "fed speak",
                               "fed governor", "waller", "jefferson"]):
        return "FED_SPEECH"
    return "OTHER"


# ---------------------------------------------------------------------------
# Release result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ReleaseResult:
    event_name    : str
    country       : str
    impact        : str
    event_type    : str
    scheduled_at  : datetime          # UTC — original scheduled time
    released_at   : datetime          # UTC — moment actual was detected
    latency_s     : float             # seconds from scheduled_at to released_at
    actual        : Optional[str]
    forecast      : Optional[str]
    previous      : Optional[str]
    actual_f      : Optional[float]
    forecast_f    : Optional[float]
    surprise_pct  : Optional[float]   # (actual - forecast) / |forecast| * 100
    surprise_label: str               # STRONG BEAT / BEAT / IN-LINE / MISS / STRONG MISS
    gold_signal   : str               # BULLISH / BEARISH / NEUTRAL
    gold_strength : str               # STRONG / MODEST / WEAK
    gold_importance: float

    def one_liner(self) -> str:
        sup = f"{self.surprise_pct:+.1f}%" if self.surprise_pct is not None else "N/A"
        fc  = self.forecast or "—"
        return (
            f"{self.event_name} ({self.country}) | "
            f"A={self.actual}  F={fc}  P={self.previous or '—'} | "
            f"Surprise={sup} → {self.surprise_label} | "
            f"Gold: {self.gold_strength} {self.gold_signal} | "
            f"latency={self.latency_s:.1f}s"
        )


def _compute_result(ev: EconomicEvent,
                    scheduled_at: datetime,
                    released_at: datetime) -> ReleaseResult:
    actual_f   = _parse_value(ev.actual)
    forecast_f = _parse_value(ev.forecast)
    event_type = _classify_event_type(ev.name)

    # Surprise
    surprise_pct   : Optional[float] = None
    surprise_label : str             = "N/A"
    if actual_f is not None and forecast_f is not None and forecast_f != 0:
        surprise_pct = (actual_f - forecast_f) / abs(forecast_f) * 100
        abs_s = abs(surprise_pct) / 100
        if abs_s >= SURPRISE_STRONG:
            surprise_label = "STRONG BEAT" if surprise_pct > 0 else "STRONG MISS"
        elif abs_s >= SURPRISE_MODEST:
            surprise_label = "BEAT"        if surprise_pct > 0 else "MISS"
        else:
            surprise_label = "IN-LINE"
    elif actual_f is not None and forecast_f is None:
        surprise_label = "NO FORECAST"

    # Gold direction
    beat_sig, miss_sig = _DIRECTION_MAP.get(event_type, ("NEUTRAL", "NEUTRAL"))
    if surprise_label in ("STRONG BEAT", "BEAT"):
        gold_signal = beat_sig
    elif surprise_label in ("STRONG MISS", "MISS"):
        gold_signal = miss_sig
    else:
        gold_signal = "NEUTRAL"

    # Strength
    if surprise_pct is not None:
        abs_s = abs(surprise_pct) / 100
        gold_strength = ("STRONG" if abs_s >= SURPRISE_STRONG
                         else "MODEST" if abs_s >= SURPRISE_MODEST
                         else "WEAK")
    else:
        gold_strength = "UNKNOWN"

    latency_s = (released_at - scheduled_at).total_seconds()

    return ReleaseResult(
        event_name    = ev.name,
        country       = ev.country,
        impact        = ev.impact,
        event_type    = event_type,
        scheduled_at  = scheduled_at,
        released_at   = released_at,
        latency_s     = latency_s,
        actual        = ev.actual,
        forecast      = ev.forecast,
        previous      = ev.previous,
        actual_f      = actual_f,
        forecast_f    = forecast_f,
        surprise_pct  = surprise_pct,
        surprise_label= surprise_label,
        gold_signal   = gold_signal,
        gold_strength = gold_strength,
        gold_importance= ev.gold_importance,
    )


def _print_banner(r: ReleaseResult) -> None:
    sched_ts = r.scheduled_at.strftime("%H:%M:%S UTC")
    rel_ts   = r.released_at.strftime("%H:%M:%S UTC")
    sup      = f"{r.surprise_pct:+.1f}%" if r.surprise_pct is not None else "N/A"
    fc       = r.forecast or "—"
    prev     = r.previous or "—"

    if r.gold_signal == "BULLISH":
        sig_tag = "▲ GOLD BULLISH"
    elif r.gold_signal == "BEARISH":
        sig_tag = "▼ GOLD BEARISH"
    else:
        sig_tag = "● GOLD NEUTRAL"

    banner = (
        f"\n{'='*62}\n"
        f"  RELEASE  sched={sched_ts}  detected={rel_ts}  (+{r.latency_s:.1f}s)\n"
        f"  {r.impact} — {r.event_type} — {r.event_name} ({r.country})\n"
        f"{'─'*62}\n"
        f"  Actual   : {r.actual or 'N/A'}\n"
        f"  Forecast : {fc}\n"
        f"  Previous : {prev}\n"
        f"  Surprise : {sup}  →  {r.surprise_label}\n"
        f"{'─'*62}\n"
        f"  {r.gold_strength} {sig_tag}\n"
        f"{'='*62}\n"
    )
    print(banner, flush=True)
    log.warning(
        "RELEASE +%.1fs | %s | %s | A=%s F=%s | surprise=%s | Gold: %s %s",
        r.latency_s, r.event_type, r.event_name,
        r.actual, fc, sup, r.gold_strength, r.gold_signal,
    )


# ---------------------------------------------------------------------------
# EventWatcher — watches ONE scheduled event with rapid polling
# ---------------------------------------------------------------------------

class _EventWatcher:
    """
    Spawned as a daemon thread for a single upcoming event.

    Timeline:
        T - PRE_FIRE_S  → thread wakes, starts fetching
        T + 0           → scheduled event time
        T + RAPID_TIMEOUT_S → give up if no actual found
    """

    def __init__(self,
                 ev: EconomicEvent,
                 calendar: TradingEconomicsCalendar,
                 on_release,          # callback(ReleaseResult)
                 scheduled_utc: datetime):
        self._ev           = ev
        self._calendar     = calendar
        self._on_release   = on_release
        self._scheduled    = scheduled_utc
        self._cid          = (ev.calendar_id
                              or f"{ev.name}|{ev.timestamp.isoformat()}")
        self._thread       = threading.Thread(
            target=self._run,
            name=f"Watcher:{ev.name[:20]}",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def _run(self):
        now_utc  = datetime.now(timezone.utc)
        fire_at  = self._scheduled - timedelta(seconds=PRE_FIRE_S)

        # Sleep until PRE_FIRE_S before scheduled time
        wait_s = (fire_at - now_utc).total_seconds()
        if wait_s > 0:
            log.info(
                "Watcher armed: %s @ %s UTC (sleeping %.0fs)",
                self._ev.name,
                self._scheduled.strftime("%H:%M:%S"),
                wait_s,
            )
            time.sleep(wait_s)

        log.info("Watcher ACTIVE: %s — rapid-poll every %ds (timeout %ds)",
                 self._ev.name, RAPID_POLL_S, RAPID_TIMEOUT_S)

        deadline = datetime.now(timezone.utc) + timedelta(seconds=RAPID_TIMEOUT_S)

        while datetime.now(timezone.utc) < deadline:
            try:
                live_ev = self._fetch_event()
                if live_ev and live_ev.actual and live_ev.actual.strip():
                    released_at = datetime.now(timezone.utc)
                    result = _compute_result(live_ev, self._scheduled, released_at)
                    _print_banner(result)
                    self._on_release(result)
                    return
            except Exception as e:
                log.warning("Watcher fetch error (%s): %s", self._ev.name, e)

            time.sleep(RAPID_POLL_S)

        log.warning(
            "Watcher TIMEOUT: %s — no actual after %ds",
            self._ev.name, RAPID_TIMEOUT_S,
        )

    def _fetch_event(self) -> Optional[EconomicEvent]:
        """Re-fetch today's calendar and find this specific event."""
        from datetime import date
        today = date.today().isoformat()
        events = self._calendar.fetch_calendar(today, today, min_importance=2)
        for ev in events:
            cid = (ev.calendar_id
                   or f"{ev.name}|{ev.timestamp.isoformat()}")
            if cid == self._cid:
                return ev
            # Fallback: same name + same day
            if ev.name == self._ev.name:
                return ev
        return None


# ---------------------------------------------------------------------------
# ReleaseMonitor — schedules EventWatchers for today's events
# ---------------------------------------------------------------------------

class ReleaseMonitor:
    """
    Real-time release monitor usando event-triggered rapid polling.

    - Agenda um EventWatcher por cada evento HIGH/CRITICAL do dia
    - Cada watcher dorme até T-2s antes do horário agendado
    - A partir daí faz polls a cada 2s até o actual aparecer
    - Banner impresso em < ~4s após publicação na API

    Thread-safe: _releases protegido por _lock.
    """

    def __init__(self, calendar: TradingEconomicsCalendar):
        self._calendar   = calendar
        self._lock       = threading.Lock()
        self._releases   : List[ReleaseResult] = []
        self._armed      : set = set()      # calendar_ids already armed/fired
        self._thread     : Optional[threading.Thread] = None
        self._running    = False
        self._schedule_count = 0
        self._last_schedule  : Optional[datetime] = None

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start scheduler thread (idempotent)."""
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._scheduler_loop,
            name="ReleaseMonitor-Scheduler",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "ReleaseMonitor started — event-triggered rapid-poll "
            "(pre_fire=%ds, rapid_poll=%ds, timeout=%ds)",
            PRE_FIRE_S, RAPID_POLL_S, RAPID_TIMEOUT_S,
        )

    def stop(self) -> None:
        """Stop scheduler thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info(
            "ReleaseMonitor stopped — schedules=%d  releases=%d",
            self._schedule_count, len(self._releases),
        )

    def get_recent_releases(self, n: int = 5) -> List[ReleaseResult]:
        """Return the N most recent release results (thread-safe)."""
        with self._lock:
            return list(self._releases[-n:])

    def get_latest(self) -> Optional[ReleaseResult]:
        with self._lock:
            return self._releases[-1] if self._releases else None

    def get_status(self) -> dict:
        latest = self.get_latest()
        return {
            "running"        : self._running,
            "schedule_count" : self._schedule_count,
            "last_schedule"  : (self._last_schedule.isoformat()
                                if self._last_schedule else None),
            "armed_events"   : len(self._armed),
            "releases_today" : len(self._releases),
            "latest_release" : {
                "event"    : latest.event_name,
                "actual"   : latest.actual,
                "forecast" : latest.forecast,
                "surprise" : (f"{latest.surprise_pct:+.1f}%"
                              if latest.surprise_pct else "N/A"),
                "gold"     : f"{latest.gold_strength} {latest.gold_signal}",
                "latency_s": latest.latency_s,
                "at"       : latest.released_at.isoformat(),
            } if latest else None,
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _scheduler_loop(self) -> None:
        """Refresh event schedule every SCHEDULE_POLL_S seconds."""
        while self._running:
            try:
                self._refresh_schedule()
            except Exception as e:
                log.warning("Scheduler error: %s", e)
            time.sleep(SCHEDULE_POLL_S)

    def _refresh_schedule(self) -> None:
        from datetime import date
        today = date.today().isoformat()
        events = self._calendar.fetch_calendar(today, today, min_importance=2)

        self._schedule_count += 1
        self._last_schedule   = datetime.now(timezone.utc)
        now_utc               = datetime.now(timezone.utc)

        new_armed = 0
        for ev in events:
            if ev.gold_importance < MIN_GOLD_IMP:
                continue

            cid = (ev.calendar_id
                   or f"{ev.name}|{ev.timestamp.isoformat()}")

            # Already armed or fired
            if cid in self._armed:
                continue

            # Convert event timestamp to UTC
            try:
                ev_ts_utc = ev.timestamp.astimezone(timezone.utc)
            except Exception:
                continue

            # Only arm future events (or events that fired < RAPID_TIMEOUT_S ago)
            seconds_until = (ev_ts_utc - now_utc).total_seconds()
            seconds_since = -seconds_until

            if seconds_until < -RAPID_TIMEOUT_S:
                # Too far in the past — mark as seen to avoid re-arming
                self._armed.add(cid)
                continue

            if seconds_until > 86_400:
                # More than 24h away — skip for now
                continue

            # Mark as armed immediately so we don't re-arm on next refresh
            self._armed.add(cid)

            # If already past scheduled time but within timeout window,
            # fire watcher immediately (scheduled_at = ev_ts_utc)
            watcher = _EventWatcher(
                ev           = ev,
                calendar     = self._calendar,
                on_release   = self._on_release,
                scheduled_utc= ev_ts_utc,
            )
            watcher.start()
            new_armed += 1

            log.info(
                "Armed watcher: %s @ %s UTC (%.0fs from now | imp=%.1f)",
                ev.name,
                ev_ts_utc.strftime("%H:%M:%S"),
                seconds_until,
                ev.gold_importance,
            )

        if new_armed:
            log.info("Schedule refresh: %d new watchers armed", new_armed)

    def _on_release(self, result: ReleaseResult) -> None:
        """Callback invoked by EventWatcher when actual appears."""
        # 1. Store release
        with self._lock:
            self._releases.append(result)
            if len(self._releases) > MAX_RELEASES_LOG:
                self._releases.pop(0)

        # 2. Activate Feature Flag (event_processor reads this for risk veto)
        NEWS_STATE.activate(result)

        # 3. Log surprise for Grenadier V2 chaos labeling
        _log_surprise(result)

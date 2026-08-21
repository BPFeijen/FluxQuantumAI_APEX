"""live/macro_monitor.py — Virtual Active Position monitoring (MACRO-MONITOR-VAP)

Asana 1214290409737742. Tracks the most recent emitted GO decision as a
"Virtual Active Position" (VAP) and alerts the operator via Telegram + heartbeat
+ position_events.jsonl when conditions reverse against the VAP direction.

Independent of broker connection / actual MT5 positions — operates purely on
emitted decisions and current market state. Complements PositionMonitor (which
monitors REAL MT5 positions when they exist).

MVP 5 triggers (zero new methodology thresholds — see
_audit/calibrations/calibration_macro_monitor_v1.md for inheritance audit):
  1. ICEBERG_AGAINST   — iceberg severity HIGH/CRITICAL on opposite side
                          (inherits event_processor.py:957-962 score >= 0.75)
  2. ANOMALY_AGAINST   — defense_tier in {ENTRY_BLOCK, DEFENSIVE_EXIT}
                          AND stress_direction implies AGAINST VAP direction
                          (inherits event_processor.py:2479-2499)
  3. REGIME_FLIP_M30   — m30_bias_confirmed=True AND m30_bias opposite to VAP
                          (inherits level_detector.derive_m30_bias logic)
  4. VIRTUAL_TP1       — current_price reached VAP.tp1 (level mirror, no threshold)
  5. VIRTUAL_SL        — current_price reached VAP.sl (level mirror, no threshold)

v2 backlog (deferred): PRICE_ACTION_STOP, REGIME_FLIP_D1, MOMENTUM_FLIP, VIRTUAL_TP2.

Anti-spam: 1 alert per (vap_id, trigger_id) pair. Subsequent triggers framed as
ADDITIONAL_TRIGGER. Lifecycle events (CREATED, SUPERSEDED, EXPIRED) emitted to
JSONL only, not Telegram.

Heartbeat field: active_virtual_position (in service_state.json, written by
event_processor heartbeat writer via get_active_virtual_position()).

Standing rules respected: 1, 8, 9, 10, 11, 13, 15.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.macro_monitor")

# ── Paths ─────────────────────────────────────────────────────────────
DECISION_LIVE_PATH  = Path(r"C:\FluxQuantumAI\logs\decision_live.json")
DECISION_LOG_PATH   = Path(r"C:\FluxQuantumAI\logs\decision_log.jsonl")
SERVICE_STATE_PATH  = Path(r"C:\FluxQuantumAI\logs\service_state.json")
POSITION_EVENTS_LOG = Path(r"C:\FluxQuantumAI\logs\position_events.jsonl")

# ── Operational config ───────────────────────────────────────────────
TICK_INTERVAL_S      = 2.0
VAP_MAX_LIFETIME_MIN = 240   # 4h — operational default; flagged for Barbara review
                              # (see _audit/calibrations/calibration_macro_monitor_v1.md)

# ── MVP trigger constants — ALL INHERITED from existing prod code ────
# Iceberg severity (event_processor.py:957-962): score >= 0.75 ⟹ HIGH; >= 0.90 ⟹ CRITICAL
ICEBERG_SEVERITIES_AGAINST = {"HIGH", "CRITICAL"}

# Anomaly: defense_tier ∈ {ENTRY_BLOCK, DEFENSIVE_EXIT} ⟹ severity HIGH/CRITICAL
# (event_processor.py:2479-2499)
ANOMALY_DEFENSE_TIERS_AGAINST = {"ENTRY_BLOCK", "DEFENSIVE_EXIT"}
ANOMALY_STRESS_VS_DIRECTION = {
    "SHORT": {"EXIT_SHORT", "EXIT_ALL"},
    "LONG":  {"EXIT_LONG",  "EXIT_ALL"},
}

# ── State (module-level, thread-safe) ────────────────────────────────

@dataclass
class VirtualActivePosition:
    vap_id: str                      # decision_id of originating GO decision
    direction: str                    # "LONG" | "SHORT"
    entry_price: float                # price_mt5 at GO emission
    entry_ts: str                     # ISO8601 from decision payload
    sl: float                         # from decision.sl
    tp1: float                        # from decision.tp1
    tp2: float                        # from decision.tp2
    m30_bias_at_creation: str         # forensic snapshot
    atr_at_creation: float            # forensic snapshot
    status: str                       # HELD | EXIT_SUGGESTED | SUPERSEDED | EXPIRED
    fired_triggers: list = field(default_factory=list)  # anti-spam set
    created_at: str = ""              # MM-side timestamp
    expires_at: str = ""              # entry_ts + VAP_MAX_LIFETIME_MIN
    last_alert_ts: Optional[str] = None


_LAST_VAP_LOCK = threading.Lock()
_LAST_VAP: Optional[VirtualActivePosition] = None
_LAST_DECISION_ID_PROCESSED: str = ""


def get_active_virtual_position() -> Optional[dict]:
    """Read by event_processor heartbeat writer for service_state.json.
    Returns shallow dict copy of current VAP, or None if no active VAP."""
    with _LAST_VAP_LOCK:
        if _LAST_VAP is None:
            return None
        d = asdict(_LAST_VAP)
        # Add derived fields useful to dashboard
        d["age_min"] = _vap_age_min(_LAST_VAP)
        return d


def _vap_age_min(vap: VirtualActivePosition) -> int:
    try:
        entry = datetime.fromisoformat(vap.entry_ts)
        now = datetime.now(timezone.utc)
        if entry.tzinfo is None:
            entry = entry.replace(tzinfo=timezone.utc)
        return int((now - entry).total_seconds() / 60)
    except Exception:
        return 0


# ── Helpers (pure functions, no state) ───────────────────────────────

def _iceberg_against_vap(ice_side: str, vap_direction: str) -> bool:
    """True iff iceberg side is opposite to VAP direction."""
    s = str(ice_side or "").upper()
    if vap_direction == "SHORT" and s in ("BUY", "BID", "LONG"):
        return True
    if vap_direction == "LONG" and s in ("SELL", "ASK", "SHORT"):
        return True
    return False


def _m30_bias_against_vap(m30_bias: str, vap_direction: str) -> bool:
    """True iff m30_bias is structurally opposite to VAP direction."""
    b = str(m30_bias or "").lower()
    if vap_direction == "SHORT" and b == "bullish":
        return True
    if vap_direction == "LONG" and b == "bearish":
        return True
    return False


def _price_reached_tp(price: float, vap: VirtualActivePosition) -> bool:
    """True iff current price has reached or exceeded the VAP's TP1 level."""
    if vap.tp1 == 0:
        return False
    if vap.direction == "SHORT":
        return price <= vap.tp1
    return price >= vap.tp1


def _price_reached_sl(price: float, vap: VirtualActivePosition) -> bool:
    """True iff current price has reached or exceeded the VAP's SL level."""
    if vap.sl == 0:
        return False
    if vap.direction == "SHORT":
        return price >= vap.sl
    return price <= vap.sl


def _unrealized_pts(vap: VirtualActivePosition, current_price: float) -> float:
    if current_price <= 0:
        return 0.0
    if vap.direction == "SHORT":
        return round(vap.entry_price - current_price, 2)
    return round(current_price - vap.entry_price, 2)


def _read_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _build_vap_from_decision(decision: dict) -> Optional[VirtualActivePosition]:
    """Build a VAP from a canonical GO decision payload."""
    dec = decision.get("decision", {}) or {}
    if dec.get("action") != "GO":
        return None
    direction = dec.get("direction")
    if direction not in ("LONG", "SHORT"):
        return None
    ctx = decision.get("context", {}) or {}
    now_dt = datetime.now(timezone.utc)
    entry_ts = decision.get("timestamp") or now_dt.isoformat()
    try:
        entry_dt = datetime.fromisoformat(entry_ts)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
    except Exception:
        entry_dt = now_dt
    expires_dt = entry_dt + timedelta(minutes=VAP_MAX_LIFETIME_MIN)
    return VirtualActivePosition(
        vap_id=decision.get("decision_id") or str(uuid.uuid4())[:8],
        direction=direction,
        entry_price=float(decision.get("price_mt5") or 0.0),
        entry_ts=entry_ts,
        sl=float(dec.get("sl") or 0.0),
        tp1=float(dec.get("tp1") or 0.0),
        tp2=float(dec.get("tp2") or 0.0),
        m30_bias_at_creation=str(ctx.get("m30_bias") or "unknown"),
        atr_at_creation=float(ctx.get("m30_atr14") or 0.0),
        status="HELD",
        fired_triggers=[],
        created_at=now_dt.isoformat(),
        expires_at=expires_dt.isoformat(),
        last_alert_ts=None,
    )


def _append_position_event(payload: dict) -> None:
    try:
        POSITION_EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(POSITION_EVENTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except Exception as e:
        log.debug("position event log write failed: %s", e)


# ── MacroMonitor class ────────────────────────────────────────────────

class MacroMonitor:
    """Background daemon thread that monitors the most recent GO decision as a
    Virtual Active Position and emits exit-suggestion alerts.

    Lifecycle:
        start() spawns daemon thread running _loop().
        _loop() ticks every TICK_INTERVAL_S seconds.
        Each tick: refresh VAP from decision_live.json, evaluate 5 triggers,
        emit alerts for any newly-fired trigger.
    """

    def __init__(self, tick_interval_s: float = TICK_INTERVAL_S) -> None:
        self.tick_interval_s = tick_interval_s
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            log.warning("MacroMonitor already running, skip start")
            return
        self._running = True
        self._restore_vap_from_decision_log()
        self._emit_lifecycle_event("MACRO_MONITOR_STARTUP", reason="MacroMonitor started")
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="macro_monitor"
        )
        self._thread.start()
        log.info("MacroMonitor started (background, %ss interval)", self.tick_interval_s)
        print(f"MacroMonitor started (background, {self.tick_interval_s}s interval)", flush=True)

    def stop(self) -> None:
        self._running = False

    # -- main loop ----------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.warning("MacroMonitor tick failed: %s", e)
            time.sleep(self.tick_interval_s)

    def _tick(self) -> None:
        global _LAST_VAP, _LAST_DECISION_ID_PROCESSED

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        decision = _read_json(DECISION_LIVE_PATH)
        if decision is None:
            return

        action = decision.get("decision", {}).get("action")
        dec_id = decision.get("decision_id", "")

        # ── 1. Handle new GO decisions (build / supersede VAP) ─────────
        # Semantics:
        #   no current VAP        -> create new VAP
        #   opposite direction GO -> supersede current + create new
        #   same direction GO     -> KEEP current VAP (treat as confirmation;
        #                             don't reset entry/SL/TP/age)
        # Operator opened SHORT at 02:00 GO; later GO SHORTs at 03:00, 04:00
        # are confirmations of the trade thesis, not new entries.
        if action == "GO" and dec_id and dec_id != _LAST_DECISION_ID_PROCESSED:
            new_vap = _build_vap_from_decision(decision)
            if new_vap is not None:
                with _LAST_VAP_LOCK:
                    if _LAST_VAP is None:
                        _LAST_VAP = new_vap
                        self._emit_lifecycle_event(
                            "MACRO_VAP_CREATED",
                            vap=_LAST_VAP,
                            reason="VAP created from GO decision",
                        )
                    elif _LAST_VAP.direction != new_vap.direction:
                        # Opposite-direction GO → supersede + replace
                        _LAST_VAP.status = "SUPERSEDED"
                        self._emit_lifecycle_event(
                            "MACRO_VAP_SUPERSEDED",
                            vap=_LAST_VAP,
                            reason=(
                                f"Opposite GO direction emitted "
                                f"(new direction={new_vap.direction})"
                            ),
                        )
                        _LAST_VAP = new_vap
                        self._emit_lifecycle_event(
                            "MACRO_VAP_CREATED",
                            vap=_LAST_VAP,
                            reason="VAP created from GO decision (post-supersede)",
                        )
                    # else: same-direction confirmation → keep existing VAP
            _LAST_DECISION_ID_PROCESSED = dec_id

        # ── 2. Snapshot current VAP ──────────────────────────────────
        with _LAST_VAP_LOCK:
            vap = _LAST_VAP

        if vap is None or vap.status in ("SUPERSEDED", "EXPIRED"):
            return

        # ── 3. Check expiry ──────────────────────────────────────────
        try:
            expires_dt = datetime.fromisoformat(vap.expires_at)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if now_dt >= expires_dt:
                with _LAST_VAP_LOCK:
                    if _LAST_VAP is not None:
                        _LAST_VAP.status = "EXPIRED"
                        self._emit_lifecycle_event(
                            "MACRO_VAP_EXPIRED",
                            vap=_LAST_VAP,
                            reason=f"Lifetime exceeded ({VAP_MAX_LIFETIME_MIN} min)",
                        )
                        _LAST_VAP = None
                return
        except Exception:
            pass

        # ── 4. Read current state ────────────────────────────────────
        service_state = _read_json(SERVICE_STATE_PATH) or {}
        current_price = float(service_state.get("mt5_price") or 0.0)
        m30_bias = service_state.get("m30_bias", "unknown")
        m30_bias_confirmed = bool(service_state.get("m30_bias_confirmed", False))
        defense_tier = service_state.get("defense_tier", "NORMAL")
        stress_direction = service_state.get("stress_direction", "HOLD")

        ice = decision.get("iceberg", {}) or {}

        # ── 5. Evaluate triggers (anti-spam: skip if already fired) ──
        triggers_to_fire = []

        # Trigger 1: ICEBERG_AGAINST
        if "ICEBERG_AGAINST" not in vap.fired_triggers:
            if (ice.get("detected")
                    and ice.get("severity") in ICEBERG_SEVERITIES_AGAINST
                    and _iceberg_against_vap(ice.get("side"), vap.direction)):
                triggers_to_fire.append({
                    "trigger": "ICEBERG_AGAINST",
                    "severity": "HIGH",
                    "reason": (
                        f"Iceberg severity={ice.get('severity')} on {ice.get('side')} side "
                        f"(refills={ice.get('refills', 0)}, score={ice.get('confidence', 0)})"
                    ),
                    "raw": {"iceberg": dict(ice)},
                })

        # Trigger 2: ANOMALY_AGAINST
        if "ANOMALY_AGAINST" not in vap.fired_triggers:
            if (defense_tier in ANOMALY_DEFENSE_TIERS_AGAINST
                    and stress_direction in ANOMALY_STRESS_VS_DIRECTION.get(vap.direction, set())):
                triggers_to_fire.append({
                    "trigger": "ANOMALY_AGAINST",
                    "severity": "HIGH",
                    "reason": (
                        f"defense_tier={defense_tier}, "
                        f"stress_direction={stress_direction} (against {vap.direction})"
                    ),
                    "raw": {"defense_tier": defense_tier, "stress_direction": stress_direction},
                })

        # Trigger 3: REGIME_FLIP_M30
        if "REGIME_FLIP_M30" not in vap.fired_triggers:
            if m30_bias_confirmed and _m30_bias_against_vap(m30_bias, vap.direction):
                triggers_to_fire.append({
                    "trigger": "REGIME_FLIP_M30",
                    "severity": "MEDIUM",
                    "reason": (
                        f"m30_bias={m30_bias} (confirmed) flipped against {vap.direction}; "
                        f"was {vap.m30_bias_at_creation} at VAP creation"
                    ),
                    "raw": {
                        "m30_bias": m30_bias,
                        "m30_bias_at_creation": vap.m30_bias_at_creation,
                    },
                })

        # Trigger 4: VIRTUAL_TP1
        if "VIRTUAL_TP1" not in vap.fired_triggers and current_price > 0:
            if _price_reached_tp(current_price, vap):
                triggers_to_fire.append({
                    "trigger": "VIRTUAL_TP1",
                    "severity": "INFO",
                    "reason": f"Price {current_price:.2f} reached TP1 {vap.tp1:.2f}",
                    "raw": {"current_price": current_price, "tp1": vap.tp1},
                })

        # Trigger 5: VIRTUAL_SL
        if "VIRTUAL_SL" not in vap.fired_triggers and current_price > 0:
            if _price_reached_sl(current_price, vap):
                triggers_to_fire.append({
                    "trigger": "VIRTUAL_SL",
                    "severity": "HIGH",
                    "reason": f"Price {current_price:.2f} reached SL {vap.sl:.2f}",
                    "raw": {"current_price": current_price, "sl": vap.sl},
                })

        # ── 6. Fire alerts ───────────────────────────────────────────
        for t in triggers_to_fire:
            self._fire_trigger(
                vap=vap,
                trigger_id=t["trigger"],
                severity=t["severity"],
                reason=t["reason"],
                raw=t["raw"],
                current_price=current_price,
                now_iso=now_iso,
            )

    # -- trigger firing ------------------------------------------------

    def _fire_trigger(
        self,
        vap: VirtualActivePosition,
        trigger_id: str,
        severity: str,
        reason: str,
        raw: dict,
        current_price: float,
        now_iso: str,
    ) -> None:
        is_first = len(vap.fired_triggers) == 0

        # Update VAP state under lock
        with _LAST_VAP_LOCK:
            if _LAST_VAP is None or _LAST_VAP.vap_id != vap.vap_id:
                # VAP changed underneath us (raced supersede); abort
                return
            _LAST_VAP.fired_triggers.append(trigger_id)
            if _LAST_VAP.status == "HELD":
                _LAST_VAP.status = "EXIT_SUGGESTED"
            _LAST_VAP.last_alert_ts = now_iso
            current_triggers = list(_LAST_VAP.fired_triggers)

        # Build event payload
        event_payload = {
            "timestamp": now_iso,
            "event_type": f"MACRO_{trigger_id}",
            "trigger": trigger_id,
            "severity": severity,
            "vap_id": vap.vap_id,
            "vap_direction": vap.direction,
            "vap_entry_price": vap.entry_price,
            "vap_entry_ts": vap.entry_ts,
            "vap_age_min": _vap_age_min(vap),
            "current_price": current_price,
            "unrealized_pts": _unrealized_pts(vap, current_price),
            "all_active_triggers": current_triggers,
            "is_first_trigger": is_first,
            "reason_human": reason,
            "reason_machine": raw,
            "vap_sl": vap.sl,
            "vap_tp1": vap.tp1,
            "vap_tp2": vap.tp2,
        }

        # Write to position_events.jsonl
        _append_position_event(event_payload)

        # Send Telegram via dedicated function
        self._notify_telegram(
            vap=vap,
            trigger_id=trigger_id,
            severity=severity,
            reason=reason,
            current_price=current_price,
            is_first=is_first,
            prior_triggers=[t for t in current_triggers if t != trigger_id],
        )

        log.info(
            "MACRO_%s fired for VAP %s %s @ %.2f (price=%.2f, severity=%s)",
            trigger_id, vap.direction, vap.vap_id, vap.entry_price,
            current_price, severity,
        )

    def _notify_telegram(
        self,
        vap: VirtualActivePosition,
        trigger_id: str,
        severity: str,
        reason: str,
        current_price: float,
        is_first: bool,
        prior_triggers: list,
    ) -> None:
        try:
            from live import telegram_notifier as tg
            if is_first:
                tg.notify_macro_exit(
                    vap_id=vap.vap_id,
                    direction=vap.direction,
                    entry_price=vap.entry_price,
                    entry_ts=vap.entry_ts,
                    current_price=current_price,
                    trigger=trigger_id,
                    severity=severity,
                    reason=reason,
                    sl=vap.sl,
                    tp1=vap.tp1,
                )
            else:
                tg.notify_macro_additional(
                    vap_id=vap.vap_id,
                    direction=vap.direction,
                    trigger=trigger_id,
                    severity=severity,
                    reason=reason,
                    prior_triggers=prior_triggers,
                )
        except Exception as e:
            log.debug("MacroMonitor telegram notify failed: %s", e)

    # -- lifecycle events ---------------------------------------------

    def _emit_lifecycle_event(
        self,
        event_type: str,
        vap: Optional[VirtualActivePosition] = None,
        reason: str = "",
    ) -> None:
        """Emit lifecycle event to position_events.jsonl only (no Telegram)."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "reason": reason,
            "tick_interval_s": self.tick_interval_s,
            "vap_max_lifetime_min": VAP_MAX_LIFETIME_MIN,
        }
        if vap is not None:
            payload.update({
                "vap_id": vap.vap_id,
                "vap_direction": vap.direction,
                "vap_entry_price": vap.entry_price,
                "vap_entry_ts": vap.entry_ts,
                "vap_status": vap.status,
                "fired_triggers": list(vap.fired_triggers),
            })
        _append_position_event(payload)

    # -- restart persistence -------------------------------------------

    def _restore_vap_from_decision_log(self) -> None:
        """On startup, scan decision_log.jsonl backwards for the most recent
        GO decision within VAP_MAX_LIFETIME_MIN. Restore VAP if found.

        Per Barbara directive: VAP persistence across restart preserves
        operator context (e.g., the system tracked a SHORT GO from 2 AM,
        operator manually opened, system restarted at 3 AM — VAP must
        survive so 4 AM iceberg-against alert still fires).
        """
        global _LAST_VAP, _LAST_DECISION_ID_PROCESSED

        if not DECISION_LOG_PATH.exists():
            return
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=VAP_MAX_LIFETIME_MIN)
            with open(DECISION_LOG_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            log.warning("MacroMonitor VAP restore read failed: %s", e)
            return

        for line in reversed(lines[-2000:]):   # cap scan to recent 2000 lines
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts_str = d.get("timestamp", "")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < cutoff:
                break  # everything older is out of scope
            action = d.get("decision", {}).get("action")
            if action != "GO":
                continue
            vap = _build_vap_from_decision(d)
            if vap is None:
                continue
            with _LAST_VAP_LOCK:
                _LAST_VAP = vap
                _LAST_DECISION_ID_PROCESSED = vap.vap_id
            log.info(
                "MacroMonitor restored VAP from decision_log: %s %s @ %.2f (age=%dmin)",
                vap.direction, vap.vap_id, vap.entry_price, _vap_age_min(vap),
            )
            return

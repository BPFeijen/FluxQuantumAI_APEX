"""
NewsGate — Gate 2: NEWS VETO / FORCE_EXIT

Role: Bloquear entradas antes de eventos macro de alto impacto.
Retorna: veto_status (HARD_VETO ou FORCE_EXIT em eventos CRITICAL).

Implementação actual: lê proxy_events.json (eventos agendados).
Evolução futura: feed real-time (Reuters/Benzinga/Econoday API).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from ...common.base_provider import BaseProvider, Direction, MarketTick, ProviderVerdict, VetoStatus
from ...common.config import DEFAULT_ENGINE_RISK_CONFIG, PROXY_EVENTS_PATH

_logger = logging.getLogger("nextgen.news_gate")


_TYPE_SEVERITY_MAP = {
    "FOMC": "CRITICAL", "NFP": "HIGH", "CPI": "HIGH",
    "PPI": "MEDIUM", "GDP": "HIGH", "JOBLESS": "MEDIUM",
    "PCE": "HIGH", "ISM": "MEDIUM", "PMI": "LOW",
}


class ScheduledEvent:
    def __init__(self, data: dict):
        self.name = data.get("name") or data.get("type", "UNKNOWN")
        # Suporta "datetime" (NewsGate nativo) e "event_utc" (proxy_events.json)
        dt_raw = data.get("datetime") or data.get("event_utc", "")
        self.datetime = datetime.fromisoformat(str(dt_raw).replace("Z", "+00:00"))
        # Severity: campo explícito ou derivado do type
        self.severity = (
            data.get("severity")
            or _TYPE_SEVERITY_MAP.get(self.name.upper(), "MEDIUM")
        ).upper()
        self.pre_mins = int(data.get("pre_blackout_minutes", 30))
        self.post_mins= int(data.get("post_blackout_minutes", 15))

    def is_active(self, now: datetime) -> bool:
        window_start = self.datetime - timedelta(minutes=self.pre_mins)
        window_end   = self.datetime + timedelta(minutes=self.post_mins)
        return window_start <= now <= window_end

    def time_to_event_min(self, now: datetime) -> float:
        return (self.datetime - now).total_seconds() / 60


class NewsGateProvider(BaseProvider):
    """
    Gate 2 — NewsGate.

    Severidades e acções:
        LOW      → CLEAR     size × 1.0
        MEDIUM   → SOFT_VETO size × 0.75
        HIGH     → SOFT_VETO size × 0.50
        CRITICAL → HARD_VETO size × 0.0  (+ FORCE_EXIT se posição activa)
    """

    GATE_NUMBER   = 2
    PROVIDER_NAME = "NewsGate"

    def __init__(self):
        self._events: List[ScheduledEvent] = []
        self._news_mults = DEFAULT_ENGINE_RISK_CONFIG["news_multipliers"]
        self._load_events()

    def _load_events(self):
        if not PROXY_EVENTS_PATH.exists():
            _logger.warning("NewsGate: proxy_events.json não encontrado — STUB activo")
            return
        try:
            with open(PROXY_EVENTS_PATH) as f:
                data = json.load(f)
            events_raw = data if isinstance(data, list) else data.get("events", [])
            self._events = [ScheduledEvent(e) for e in events_raw]
            _logger.info("NewsGate: %d eventos carregados", len(self._events))
        except Exception as e:
            _logger.warning("NewsGate: erro ao carregar eventos — %s", e)

    def is_ready(self) -> bool:
        return True  # funciona mesmo sem eventos (permissivo)

    def _active_event(self, now: datetime) -> Optional[ScheduledEvent]:
        for ev in self._events:
            if ev.is_active(now):
                return ev
        return None

    def evaluate(self, tick: MarketTick) -> ProviderVerdict:
        if not self._events:
            return self._stub_verdict()

        now   = tick.timestamp
        event = self._active_event(now)

        if event is None:
            # Verificar se há evento próximo (< 5 min) — alertar mas não vetar
            upcoming = [e for e in self._events if 0 < e.time_to_event_min(now) <= 5]
            return ProviderVerdict(
                provider_name    = self.PROVIDER_NAME,
                gate_number      = self.GATE_NUMBER,
                direction        = Direction.NEUTRAL,
                confidence_score = 1.0,
                veto_status      = VetoStatus.CLEAR,
                size_multiplier  = 1.0,
                metadata         = {
                    "upcoming_events": [e.name for e in upcoming],
                    "active_event": None,
                },
                is_stub = False,
            )

        # Evento activo
        mult = self._news_mults.get(event.severity, 1.0)

        if event.severity == "CRITICAL":
            veto = VetoStatus.FORCE_EXIT
        elif mult < 1.0:
            veto = VetoStatus.SOFT_VETO
        else:
            veto = VetoStatus.CLEAR

        return ProviderVerdict(
            provider_name    = self.PROVIDER_NAME,
            gate_number      = self.GATE_NUMBER,
            direction        = Direction.NEUTRAL,
            confidence_score = mult,
            veto_status      = veto,
            size_multiplier  = mult,
            metadata         = {
                "active_event":  event.name,
                "severity":      event.severity,
                "event_time":    event.datetime.isoformat(),
            },
            is_stub = False,
        )

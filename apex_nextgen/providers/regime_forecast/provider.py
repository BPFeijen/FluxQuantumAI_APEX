"""
RegimeForecast — Gate 5: REGIME / TIMING FILTER

Role: Filtrar entradas com base no regime de mercado e qualidade temporal.
Retorna: size_multiplier + veto_status (regime desfavorável → avoid/force_exit).

Implementação actual:
    Fase A (agora): heurísticas baseadas em volatilidade e sessão de mercado
    Fase V3: PPO RL Agent (rl_v3/inference/rl_agent.py) quando treinado

Gap actual: FitGap = 0% — este gate não existe em produção.
Objectivo: ser o primeiro gate genuinamente novo do NextGen.
"""

from __future__ import annotations

import logging
from datetime import time as dtime
from pathlib import Path
from typing import Optional

from ...common.base_provider import BaseProvider, Direction, MarketTick, ProviderVerdict, VetoStatus
from ...common.config import DEFAULT_ENGINE_RISK_CONFIG, SESSION_CONFIG

_logger = logging.getLogger("nextgen.regime_forecast")

# Caminho para o agente RL treinado (Sprint V3)
RL_AGENT_PATH = Path(r"C:\FluxQuantumAI\rl_v3\models\best_model.zip")
RL_NORM_PATH  = Path(r"C:\FluxQuantumAI\rl_v3\models\norm_stats.json")


class RegimeForecastProvider(BaseProvider):
    """
    Gate 5 — RegimeForecast.

    Fase A — Heurísticas de sessão + volatilidade:
        Sessão ASIA   → size × 0.50  (liquidez baixa)
        Sessão LONDON → size × 0.70
        Sessão NY     → size × 1.00  (peak)

        Spread > 2.0 pts (volatilidade extrema) → SOFT_VETO × 0.50
        Spread > 5.0 pts                        → HARD_VETO
        depth_total < 50 (liquidez crítica)      → SOFT_VETO × 0.25

    Fase V3 — RL PPO Agent:
        Observation: 26 FLUXFOX + Grenadier MSE + position state
        Action space: ALLOW_ENTRY / REDUCE_SIZE / AVOID_ENTRY / FORCE_EXIT
        Override hard: Grenadier MSE > threshold → sempre HARD_VETO
    """

    GATE_NUMBER   = 5
    PROVIDER_NAME = "RegimeForecast"

    _SPREAD_SOFT   = 2.0   # pts
    _SPREAD_HARD   = 5.0   # pts
    _DEPTH_CRITICAL = 50.0

    def __init__(self):
        self._rl_agent  = None
        self._rl_loaded = False
        self._mults     = DEFAULT_ENGINE_RISK_CONFIG["regime_multipliers"]
        self._try_load_rl()

    def _try_load_rl(self):
        """Fase V3: tenta carregar o agente PPO. Silencioso se não disponível."""
        if not (RL_AGENT_PATH.exists() and RL_NORM_PATH.exists()):
            _logger.info(
                "RegimeForecast: agente RL não disponível — modo heurístico activo. "
                "Treinar em: rl_v3/training/train_ppo.py"
            )
            return
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parents[4]))
            from rl_v3.inference.rl_agent import RLAgent

            self._rl_agent  = RLAgent.load(str(RL_AGENT_PATH), str(RL_NORM_PATH))
            self._rl_loaded = True
            _logger.info("RegimeForecast: agente PPO carregado — modo RL activo")
        except Exception as e:
            _logger.warning("RegimeForecast: falha ao carregar agente RL — %s — modo heurístico activo", e)

    def is_ready(self) -> bool:
        return True  # heurísticas sempre prontas

    def _session_multiplier(self, tick: MarketTick) -> tuple[str, float]:
        """Determina a sessão de mercado e o multiplicador correspondente."""
        t = tick.timestamp.time()

        def _in_session(start_str: str, end_str: str) -> bool:
            h_s, m_s = map(int, start_str.split(":"))
            h_e, m_e = map(int, end_str.split(":"))
            start = dtime(h_s, m_s)
            end   = dtime(h_e, m_e)
            if start <= end:
                return start <= t < end
            # Overnight session (wrap midnight)
            return t >= start or t < end

        for session, cfg in SESSION_CONFIG.items():
            if _in_session(cfg["start"], cfg["end"]):
                return session, cfg["multiplier"]

        return "UNKNOWN", 0.50  # fora das sessões conhecidas → conservador

    def _heuristic_evaluate(self, tick: MarketTick) -> ProviderVerdict:
        """Fase A: avaliação por heurísticas de sessão e liquidez."""
        session, session_mult = self._session_multiplier(tick)

        spread      = tick.spread
        depth_total = tick.total_bid_depth + tick.total_ask_depth

        # Spread extremo → veto independente da sessão
        if spread >= self._SPREAD_HARD:
            return ProviderVerdict(
                provider_name    = self.PROVIDER_NAME,
                gate_number      = self.GATE_NUMBER,
                direction        = Direction.NEUTRAL,
                confidence_score = 0.0,
                veto_status      = VetoStatus.HARD_VETO,
                size_multiplier  = 0.0,
                metadata         = {
                    "session": session,
                    "reason":  f"spread crítico {spread:.2f} >= {self._SPREAD_HARD}",
                    "mode":    "heuristic",
                },
                is_stub = False,
            )

        mult = session_mult

        if spread >= self._SPREAD_SOFT:
            mult = min(mult, 0.50)
            veto = VetoStatus.SOFT_VETO
            reason = f"spread elevado {spread:.2f}"
        elif depth_total < self._DEPTH_CRITICAL:
            mult = min(mult, 0.25)
            veto = VetoStatus.SOFT_VETO
            reason = f"liquidez crítica depth={depth_total:.0f}"
        else:
            veto   = VetoStatus.CLEAR
            reason = "condições normais"

        # Sessão ASIA sempre reduz (solo)
        if session == "ASIA" and veto == VetoStatus.CLEAR:
            veto = VetoStatus.SOFT_VETO  # pelo menos soft

        return ProviderVerdict(
            provider_name    = self.PROVIDER_NAME,
            gate_number      = self.GATE_NUMBER,
            direction        = Direction.NEUTRAL,
            confidence_score = round(mult, 3),
            veto_status      = veto,
            size_multiplier  = round(mult, 3),
            metadata         = {
                "session":       session,
                "session_mult":  session_mult,
                "spread":        round(spread, 4),
                "depth_total":   round(depth_total, 0),
                "reason":        reason,
                "mode":          "heuristic",
            },
            is_stub = False,
        )

    def _rl_evaluate(self, tick: MarketTick) -> Optional[ProviderVerdict]:
        """Fase V3: avaliação por agente PPO. Retorna None se falhar."""
        try:
            action, info = self._rl_agent.get_action(tick)

            action_map = {
                0: ("HOLD",         self._mults["allow_entry"],  VetoStatus.CLEAR),
                1: ("ALLOW_ENTRY",  self._mults["allow_entry"],  VetoStatus.CLEAR),
                2: ("REDUCE_SIZE",  self._mults["reduce_size"],  VetoStatus.SOFT_VETO),
                3: ("AVOID_ENTRY",  self._mults["avoid_entry"],  VetoStatus.SOFT_VETO),
                4: ("FORCE_EXIT",   self._mults["force_exit"],   VetoStatus.FORCE_EXIT),
            }
            label, mult, veto = action_map.get(action, ("AVOID_ENTRY", 0.25, VetoStatus.SOFT_VETO))

            return ProviderVerdict(
                provider_name    = self.PROVIDER_NAME,
                gate_number      = self.GATE_NUMBER,
                direction        = Direction.NEUTRAL,
                confidence_score = round(mult, 3),
                veto_status      = veto,
                size_multiplier  = round(mult, 3),
                metadata         = {
                    "rl_action":      label,
                    "rl_action_id":   action,
                    "mode":           "rl_ppo",
                },
                is_stub = False,
            )
        except Exception as e:
            _logger.warning("RegimeForecast: falha no RL evaluate — %s — fallback heuristic", e)
            return None

    def evaluate(self, tick: MarketTick) -> ProviderVerdict:
        if self._rl_loaded:
            result = self._rl_evaluate(tick)
            if result is not None:
                return result
            # Fallback silencioso para heurísticas se RL falhar

        return self._heuristic_evaluate(tick)

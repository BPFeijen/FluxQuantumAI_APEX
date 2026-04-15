"""
C:\FluxQuantumAI\live\operational_rules.py
ATS V2 -- Pre-entry Operational Rules

Replaces ad-hoc filters (5d_delta macro, 60s cooldown) with formal, spec-defined rules.
All BARBARA-DEFINED values are hardcoded. TBD values (CAL-09, CAL-10) default to None
and fail-open: if None, that specific check is skipped rather than blocking.

BARBARA-DEFINED (hardcoded, no calibration needed):
  MAX_TRADE_GROUPS = 2
  MARGIN_FLOOR_PCT = 600.0

TBD -- requires calibration data before activating:
  LEVEL_DEDUP_TOLERANCE_ATR_MULT = None  (CAL-09 -- level spacing analysis)
  COOLDOWN_EXIT_DISTANCE_ATR_MULT = None  (CAL-10 -- gate-change distance)

Usage:
    rules = OperationalRules()
    blocked, reason = rules.check_can_enter(
        open_positions=positions,        # list of MT5 position dicts
        margin_level=margin_pct,         # float from MT5 account
        signal_price=entry_price,
        signal_direction="LONG",
        existing_trades=trades,          # list of trades.csv dicts (for dedup)
        atr_m30=atr,                     # current ATR_M30 (for TBD dedup/cooldown)
    )
    if blocked:
        log("BLOCK: " + reason)
        return

V2 spec: FUNC_V2_Smart_Exit_20260407.md Section 7
         TECH_V2_Smart_Exit_20260407.md Section 4.1
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("apex.ops_rules")


class OperationalRules:
    """
    Pre-entry guards. Checks run BEFORE the gate chain (V1-V4).
    Any BLOCK here aborts gate evaluation immediately.
    """

    # ✅ BARBARA-DEFINED -- do not change without explicit instruction
    MAX_TRADE_GROUPS: int   = 2
    MARGIN_FLOOR_PCT: float = 600.0

    # 🔴 TBD -- populated from config after CAL-09 / CAL-10.
    # None = fail-open (check skipped, does NOT block).
    LEVEL_DEDUP_TOLERANCE_ATR_MULT: Optional[float] = None  # CAL-09
    COOLDOWN_EXIT_DISTANCE_ATR_MULT: Optional[float] = None  # CAL-10

    def __init__(self, config: Optional[dict] = None) -> None:
        if config:
            ops = config.get("operational_rules", {})
            # Barbara-defined -- allow override via config but keep typed
            self.MAX_TRADE_GROUPS  = int(ops.get("max_trade_groups", self.MAX_TRADE_GROUPS))
            self.MARGIN_FLOOR_PCT  = float(ops.get("margin_floor_pct", self.MARGIN_FLOOR_PCT))
            # TBD -- only activate when explicitly set (non-null)
            self.LEVEL_DEDUP_TOLERANCE_ATR_MULT = ops.get("level_dedup_tolerance_atr_mult")
            self.COOLDOWN_EXIT_DISTANCE_ATR_MULT = ops.get("cooldown_exit_distance_atr_mult")

        not_calibrated = []
        if self.LEVEL_DEDUP_TOLERANCE_ATR_MULT is None:
            not_calibrated.append("level_dedup (CAL-09)")
        if self.COOLDOWN_EXIT_DISTANCE_ATR_MULT is None:
            not_calibrated.append("cooldown_distance (CAL-10)")
        if not_calibrated:
            log.info("OperationalRules: fail-open for uncalibrated checks: %s",
                     ", ".join(not_calibrated))

    # ------------------------------------------------------------------
    # Primary check
    # ------------------------------------------------------------------

    def check_can_enter(
        self,
        open_positions: list[dict],
        margin_level: float,
        signal_price: float,
        signal_direction: str,
        existing_trades: Optional[list[dict]] = None,
        atr_m30: float = 0.0,
        last_block_price: Optional[float] = None,
    ) -> tuple[bool, str]:
        """
        Run all pre-entry checks in priority order.

        Returns
        -------
        (blocked: bool, reason: str)
            blocked=True -> abort gate chain immediately.
            blocked=False, reason="" -> proceed to gate chain.
        """

        # 1. Max trade groups (✅ BARBARA-DEFINED)
        n_open = len(open_positions)
        if n_open >= self.MAX_TRADE_GROUPS:
            reason = (f"BLOCK_MAX_GROUPS: {n_open} groups open"
                      f" (max={self.MAX_TRADE_GROUPS})")
            log.info(reason)
            return True, reason

        # 2. Margin floor (✅ BARBARA-DEFINED)
        if margin_level > 0 and margin_level < self.MARGIN_FLOOR_PCT:
            reason = (f"BLOCK_MARGIN: {margin_level:.0f}%"
                      f" < floor {self.MARGIN_FLOOR_PCT:.0f}%")
            log.info(reason)
            return True, reason

        # 3. Level dedup -- no duplicate direction at same structural level
        #    Exact dedup: same direction + any leg open -> block.
        #    ATR-based tolerance: 🔴 TBD (CAL-09). Until calibrated, exact-direction check only.
        if open_positions and existing_trades:
            open_tickets = {p["ticket"] for p in open_positions}
            for trade in existing_trades:
                if trade.get("direction", "") != signal_direction:
                    continue
                leg1 = int(trade.get("leg1_ticket", 0) or 0)
                if leg1 > 0 and leg1 in open_tickets:
                    reason = (f"BLOCK_DEDUP: already have {signal_direction}"
                              f" (leg1={leg1})")
                    log.info(reason)
                    return True, reason

            # ATR-based dedup (activates only after CAL-09)
            if self.LEVEL_DEDUP_TOLERANCE_ATR_MULT is not None and atr_m30 > 0:
                tolerance = atr_m30 * self.LEVEL_DEDUP_TOLERANCE_ATR_MULT
                for pos in open_positions:
                    if abs(pos.get("entry", 0) - signal_price) <= tolerance:
                        reason = (f"BLOCK_DEDUP_ATR: existing entry within"
                                  f" {tolerance:.1f}pts (CAL-09 tolerance)")
                        log.info(reason)
                        return True, reason

        # 4. Cooldown exit distance (🔴 TBD -- CAL-10, fail-open until calibrated)
        if (self.COOLDOWN_EXIT_DISTANCE_ATR_MULT is not None
                and last_block_price is not None
                and atr_m30 > 0):
            distance     = abs(signal_price - last_block_price)
            min_distance = atr_m30 * self.COOLDOWN_EXIT_DISTANCE_ATR_MULT
            if distance < min_distance:
                reason = (f"BLOCK_COOLDOWN: {distance:.1f}pts from last block"
                          f" < {min_distance:.1f}pts (CAL-10 distance)")
                log.info(reason)
                return True, reason

        return False, ""

    # ------------------------------------------------------------------
    # Status display (for startup logging)
    # ------------------------------------------------------------------

    def log_status(self) -> None:
        log.info("OperationalRules active:")
        log.info("  MAX_TRADE_GROUPS  : %d (BARBARA)", self.MAX_TRADE_GROUPS)
        log.info("  MARGIN_FLOOR_PCT  : %.0f%% (BARBARA)", self.MARGIN_FLOOR_PCT)
        log.info("  level_dedup_atr   : %s",
                 "%.2fxATR (CAL-09)" % self.LEVEL_DEDUP_TOLERANCE_ATR_MULT
                 if self.LEVEL_DEDUP_TOLERANCE_ATR_MULT else "FAIL-OPEN (awaiting CAL-09)")
        log.info("  cooldown_dist_atr : %s",
                 "%.2fxATR (CAL-10)" % self.COOLDOWN_EXIT_DISTANCE_ATR_MULT
                 if self.COOLDOWN_EXIT_DISTANCE_ATR_MULT else "FAIL-OPEN (awaiting CAL-10)")

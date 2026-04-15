#!/usr/bin/env python3
"""
C:\\FluxQuantumAI\\live\\hedge_manager.py
ATS V2 -- Pullback Hedge Manager

Spec: TECH_V2_Smart_Exit_20260407.md Section 4.3

Lifecycle:
  PULLBACK detected (post-SHIELD) -> open 0.01 lot counter-direction hedge
    SL = 1.5xATR from current price (NOT main entry -- that caused Invalid stops)
  -> if TREND_RESUMED (pullback ends): close hedge at profit
  -> if ESCALATION (regime shift confirmed): close hedge + escalate to full exit

Pre-conditions (all must be met to open):
  1. shield_active = True (TP1 already hit, SL moved to entry)
  2. No existing hedge on this trade group
  3. leg2 (runner) still open
  4. State.decision == "PULLBACK" (price pulled back 0.3-1.5 x ATR, delta still aligned)
  5. Max 1 hedge per trade group (per spec)

Account requirement: MT5 account must be HEDGE mode (not netting).

Lot: 0.01 (✅ BARBARA-DEFINED -- do not change without explicit instruction)

Log events:
  HEDGE_OPENED            hedge opened in PULLBACK state
  HEDGE_CLOSED_PROFIT     closed because pullback ended (TREND_RESUMED)
  HEDGE_CLOSED_ESCALATION closed because regime shift confirmed (ESCALATION)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.hedge")

# Grenadier Stat-Guardrails (Sprint 1 -- The Shield)
# Import path: grenadier_guardrail.py lives in C:/FluxQuantumAI/ (parent of live/)
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent))
_hedge_guardrail_fn = None
try:
    from grenadier_guardrail import get_guardrail_status as _hedge_guardrail_fn
    log.debug("StatGuardrail wired into HedgeManager (Grenadier Sprint 1)")
except Exception as _hg_err:
    log.warning("StatGuardrail not available in HedgeManager: %s", _hg_err)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HEDGE_LOT           = 0.01    # ✅ BARBARA-DEFINED
PULLBACK_MIN_ATR    = 0.30    # price must move at least 0.30xATR against position
PULLBACK_MAX_ATR    = 1.50    # price must not have moved more than 1.50xATR (cascade territory)
DELTA_REALIGN_BARS  = 3       # PE-1: delta_4h must re-align for N consecutive checks
HEDGE_LOG_PATH      = Path("C:/FluxQuantumAI/logs/hedge_events.log")

# ---------------------------------------------------------------------------
# State dataclass -- one per open trade group
# ---------------------------------------------------------------------------

@dataclass
class HedgeState:
    """Per-trade-group hedge state (held in memory by HedgeManager)."""
    group_key: str            # ticket of leg2 (runner) -- surrogate group ID

    # Hedge order
    hedge_ticket:     Optional[int]      = None
    hedge_direction:  Optional[str]      = None   # "BUY" or "SELL"
    hedge_entry:      Optional[float]    = None
    hedge_open_time:  Optional[datetime] = None

    # Pullback tracking
    consecutive_realigns: int = 0         # for PE-1 condition


# ---------------------------------------------------------------------------
# PullbackDecision
# ---------------------------------------------------------------------------

@dataclass
class PullbackDecision:
    """Output of evaluate_pullback(). decision: HOLD / PULLBACK / TREND_RESUMED / ESCALATION"""
    decision:          str   = "HOLD"
    pb_within_atr:     bool  = False   # PB-3: price within ATR window
    pb_delta_weakening: bool = False   # PB-1: delta_4h weaker than peak
    pb_no_contra:      bool  = False   # PB-2: no institutional contra
    pe_delta_realign:  bool  = False   # PE-1: delta realigning
    is_regime_shift:   bool  = False   # RS: would trigger full exit


# ---------------------------------------------------------------------------
# HedgeManager
# ---------------------------------------------------------------------------

class HedgeManager:
    """
    Manages pullback hedge lifecycle for all open trade groups.

    Parameters
    ----------
    executor : MT5Executor
        Shared executor -- used for open_single and close_position.
    dry_run : bool
        If True: log actions but do NOT send MT5 orders.
    """

    def __init__(self, executor, dry_run: bool = True):
        self.executor = executor
        self.dry_run  = dry_run
        self._states: dict[str, HedgeState] = {}   # group_key -> HedgeState
        self._lock    = threading.Lock()

        HEDGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(HEDGE_LOG_PATH, "a", encoding="utf-8", buffering=1)
        log.info("HedgeManager started (dry_run=%s  lot=%.2f)", dry_run, HEDGE_LOT)

    def stop(self) -> None:
        try:
            self._log_fh.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public interface -- called from position_monitor every cycle
    # ------------------------------------------------------------------

    def process(
        self,
        pos: dict,
        state: dict,
        price: Optional[float],
        delta_4h: float,
        atr: float,
        df_micro,       # pd.DataFrame | None  (microstructure tail)
    ) -> None:
        """
        Evaluate and manage hedge for a single open position.

        pos     : MT5 position dict (ticket, direction, entry, sl, ...)
        state   : position_monitor state dict (shield_done, ...)
        price   : current mid price (None if unavailable)
        delta_4h: 4h cumulative delta
        atr     : current ATR estimate (pts)
        df_micro: microstructure DataFrame or None
        """
        if price is None:
            return
        if not state.get("shield_done", False):
            return   # hedge only post-SHIELD

        group_key = str(pos["ticket"])
        with self._lock:
            if group_key not in self._states:
                self._states[group_key] = HedgeState(group_key=group_key)
            hs = self._states[group_key]

        direction = pos["direction"]
        entry     = pos["entry"]

        decision = self._evaluate_pullback(pos, hs, price, delta_4h, atr, df_micro)

        if hs.hedge_ticket is None:
            # No open hedge -- consider opening
            if decision.decision == "PULLBACK":
                self._open_hedge(pos, hs, price, entry, direction, atr)
        else:
            # Hedge is open -- consider closing
            if decision.decision in ("TREND_RESUMED", "ESCALATION"):
                self._close_hedge(hs, decision.decision, pos)
            elif decision.is_regime_shift:
                # Regime shift always closes hedge (safety)
                self._close_hedge(hs, "ESCALATION", pos)

    def cleanup_closed(self, open_tickets: set) -> None:
        """Remove state for positions that are no longer open."""
        with self._lock:
            closed = [k for k in self._states if int(k) not in open_tickets]
        for k in closed:
            with self._lock:
                del self._states[k]

    # ------------------------------------------------------------------
    # Pullback evaluation (simplified from spec SmartExitEngine)
    # ------------------------------------------------------------------

    def _evaluate_pullback(
        self,
        pos: dict,
        hs: HedgeState,
        price: float,
        delta_4h: float,
        atr: float,
        df_micro,
    ) -> PullbackDecision:
        """
        Simplified PB/RS evaluation without full SmartExitEngine.

        PB-1: delta_4h still trending in trade direction (not flipped)
        PB-2: no strong institutional contra pressure at current price
        PB-3: price within PULLBACK_MIN_ATR to PULLBACK_MAX_ATR of entry

        PE-1: price returned close to entry AND delta re-aligned
        RS:   price > PULLBACK_MAX_ATR against entry (= cascade territory)
        """
        dec = PullbackDecision()
        direction = pos["direction"]
        entry     = pos["entry"]

        # --- PB-3: price within pullback window ---
        contra_pts = (price - entry) if direction == "LONG" else (entry - price)
        # contra_pts > 0 means price moved against position (pullback/loss territory)
        # contra_pts < 0 means position is profitable
        pullback_dist = max(contra_pts, 0.0)

        dec.pb_within_atr = (
            (atr * PULLBACK_MIN_ATR) <= pullback_dist <= (atr * PULLBACK_MAX_ATR)
        )

        # RS check: price beyond max pullback = regime shift territory
        dec.is_regime_shift = pullback_dist > atr * PULLBACK_MAX_ATR

        # --- PB-1: delta_4h not flipped against position ---
        # Aligned: LONG -> delta_4h > -200 (not strongly bearish)
        #          SHORT -> delta_4h < +200 (not strongly bullish)
        if direction == "LONG":
            delta_aligned = delta_4h > -200.0
        else:
            delta_aligned = delta_4h < +200.0
        dec.pb_delta_weakening = not delta_aligned   # weakening = still aligned but not as strong

        # --- PB-2: no institutional contra (simplified: dom_imbalance not strongly against) ---
        dec.pb_no_contra = True   # default pass; full implementation needs df_micro
        if df_micro is not None and not df_micro.empty and "dom_imbalance" in df_micro.columns:
            try:
                dom_recent = float(df_micro["dom_imbalance"].tail(5).mean())
                # Contra: dom pointing against position direction
                if direction == "LONG" and dom_recent < -30.0:
                    dec.pb_no_contra = False   # strong selling pressure against LONG
                elif direction == "SHORT" and dom_recent > +30.0:
                    dec.pb_no_contra = False   # strong buying pressure against SHORT
            except Exception:
                pass

        # --- PE-1: pullback ending (price returning, delta re-aligning) ---
        if hs.hedge_ticket is not None:
            # Re-align: contra_pts shrinking (price moving back toward entry or beyond)
            returning = pullback_dist < atr * PULLBACK_MIN_ATR
            if returning and delta_aligned:
                hs.consecutive_realigns += 1
            else:
                hs.consecutive_realigns = 0
            dec.pe_delta_realign = hs.consecutive_realigns >= DELTA_REALIGN_BARS
        else:
            hs.consecutive_realigns = 0

        # --- Decision ---
        if dec.is_regime_shift:
            dec.decision = "ESCALATION"
        elif hs.hedge_ticket is not None and dec.pe_delta_realign:
            dec.decision = "TREND_RESUMED"
        elif dec.pb_within_atr and not delta_aligned is False and dec.pb_no_contra:
            # Open hedge if all pullback conditions met and no regime shift
            dec.decision = "PULLBACK"
        else:
            dec.decision = "HOLD"

        return dec

    # ------------------------------------------------------------------
    # Open hedge
    # ------------------------------------------------------------------

    def _open_hedge(
        self,
        pos: dict,
        hs: HedgeState,
        price: float,
        entry: float,
        direction: str,
        atr: float,
    ) -> None:
        """
        Open 0.01 lot counter-direction hedge order.
        SL = 1.5xATR from current price (away from hedge direction).
        TP = 0 (managed manually / by PE condition).
        """
        # Guardrail check -- do NOT open a hedge in a liquidity vacuum (spread_ticks > 10)
        # or on stale data (>2000ms). Spec: Sprint 1, Position Manager integration.
        if _hedge_guardrail_fn is not None:
            _gr = _hedge_guardrail_fn()
            if not _gr.is_safe:
                log.warning(
                    "HEDGE BLOCKED by Guardrail %s: latency=%.0fms spread=%.1ftks -- "
                    "no hedge in liquidity vacuum",
                    _gr.veto_reason, _gr.latency_ms, _gr.spread_ticks,
                )
                return

        # Hedge is counter-direction
        hedge_dir = "SHORT" if direction == "LONG" else "LONG"
        # SL must be on the losing side of the hedge, at minimum distance from current price.
        # Using main entry (post-SHIELD = near current price) caused "Invalid stops" because
        # MT5 rejects SL within minimum distance of order price.
        # Fix: ATR-based SL -- 1.5xATR away from current price in the hedge's stop direction.
        if hedge_dir == "SHORT":
            hedge_sl = round(price + atr * 1.5, 2)   # SL above current price for SHORT
        else:
            hedge_sl = round(price - atr * 1.5, 2)   # SL below current price for LONG

        ts = datetime.now(timezone.utc).isoformat()
        log.info("HEDGE: opening %s  price=%.2f  sl=%.2f(1.5xATR)  lot=%.2f  (pullback vs %s pos entry=%.2f)",
                 hedge_dir, price, hedge_sl, HEDGE_LOT, direction, entry)

        if self.dry_run:
            hs.hedge_ticket    = -1   # placeholder for dry run
            hs.hedge_direction = hedge_dir
            hs.hedge_entry     = price
            hs.hedge_open_time = datetime.now(timezone.utc)
            msg = f"[DRY RUN] WOULD open hedge {hedge_dir} 0.01 lot @ {price:.2f}  sl={hedge_sl:.2f}"
            print(f"[{ts}] HEDGE_OPENED {msg}")
            self._write_log("HEDGE_OPENED", pos["ticket"], hedge_dir, price, hedge_sl, "DRY_RUN")
            return

        result = self.executor.open_single(
            symbol    = "XAUUSD",
            direction = hedge_dir,
            lot       = HEDGE_LOT,
            sl        = hedge_sl,
            tp        = 0.0,
            comment   = f"APEX_HEDGE_{pos['ticket']}",
        )

        if result.get("success"):
            hs.hedge_ticket    = result["ticket"]
            hs.hedge_direction = hedge_dir
            hs.hedge_entry     = result.get("entry", price)
            hs.hedge_open_time = datetime.now(timezone.utc)
            print(f"[{ts}] HEDGE_OPENED: ticket={hs.hedge_ticket}  {hedge_dir} lot={HEDGE_LOT}"
                  f"  entry={hs.hedge_entry:.2f}  sl={hedge_sl:.2f}")
            self._write_log("HEDGE_OPENED", pos["ticket"], hedge_dir,
                            hs.hedge_entry, hedge_sl, str(hs.hedge_ticket))
        else:
            log.error("HEDGE open failed: %s", result.get("error"))
            print(f"[{ts}] HEDGE_OPEN FAILED: {result.get('error')}")

    # ------------------------------------------------------------------
    # Close hedge
    # ------------------------------------------------------------------

    def _close_hedge(self, hs: HedgeState, reason: str, pos: dict) -> None:
        """
        Close the open hedge position.
        reason: "TREND_RESUMED" or "ESCALATION"
        """
        ts = datetime.now(timezone.utc).isoformat()
        ticket = hs.hedge_ticket

        if ticket is None:
            return

        event = f"HEDGE_CLOSED_{reason}"
        log.info("%s: hedge_ticket=%s  reason=%s  main_ticket=%d",
                 event, ticket, reason, pos["ticket"])

        if self.dry_run or ticket == -1:
            print(f"[{ts}] {event} hedge_ticket={ticket}  reason={reason}")
            self._write_log(event, pos["ticket"], hs.hedge_direction or "?",
                            hs.hedge_entry or 0.0, 0.0, str(ticket))
            hs.hedge_ticket    = None
            hs.hedge_direction = None
            hs.hedge_entry     = None
            hs.hedge_open_time = None
            hs.consecutive_realigns = 0
            return

        result = self.executor.close_position(ticket)
        pnl = result.get("pnl", 0.0)
        print(f"[{ts}] {event}: hedge_ticket={ticket}  pnl={pnl:+.2f}  reason={reason}")
        self._write_log(event, pos["ticket"], hs.hedge_direction or "?",
                        hs.hedge_entry or 0.0, pnl, str(ticket))

        if not result.get("success"):
            log.error("HEDGE close failed ticket=%s: %s", ticket, result.get("error"))

        hs.hedge_ticket    = None
        hs.hedge_direction = None
        hs.hedge_entry     = None
        hs.hedge_open_time = None
        hs.consecutive_realigns = 0

    # ------------------------------------------------------------------
    # Event log (hedge_events.log)
    # ------------------------------------------------------------------

    def _write_log(
        self,
        event: str,
        main_ticket: int,
        hedge_dir: str,
        price: float,
        sl_or_pnl: float,
        hedge_ticket: str,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        line = (f"{ts}\t{event}\tmain={main_ticket}\t"
                f"hedge={hedge_ticket}\tdir={hedge_dir}\t"
                f"price={price:.2f}\tsl_pnl={sl_or_pnl:.2f}\n")
        try:
            self._log_fh.write(line)
            self._log_fh.flush()
        except Exception as e:
            log.warning("hedge log write failed: %s", e)

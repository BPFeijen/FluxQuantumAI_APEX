"""
APEX GOLD News Provider â€” Risk Calculator
Gold-adjusted risk scoring based on proximity to economic events.
Uses gold_importance (raw_importance * country_relevance) as the risk driver.
"""
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from .events import EconomicEvent, NewsRiskLevel
from .time_utils import minutes_diff, to_et

logger = logging.getLogger(__name__)

# Gold-adjusted importance thresholds
THRESHOLD_BLOCK   = 2.5   # BLOCK trading (US NFP/FOMC, etc.)
THRESHOLD_CAUTION = 1.5   # Reduce to 50% position
THRESHOLD_MONITOR = 0.5   # Feature for ML, no position change
THRESHOLD_IGNORE  = 0.0   # Not even a feature

# Risk score â†’ action mapping
_ACTION_TABLE = [
    (0.90, "BLOCKED",   0.00),
    (0.70, "REDUCED",   0.50),
    (0.50, "CAUTION",   0.75),
    (0.30, "NORMAL",    1.00),
    (0.00, "NORMAL",    1.00),
]


def _score_to_action(score: float) -> Tuple[str, float]:
    """Convert a risk score to (action, position_multiplier)."""
    for threshold, action, mult in _ACTION_TABLE:
        if score >= threshold:
            return action, mult
    return "NORMAL", 1.00


class NewsRiskCalculator:
    """
    Computes the current risk level for Gold trading given a list of events.

    Scoring logic:
      For each event with gold_importance >= THRESHOLD_MONITOR:
        - Inside blackout window (pause_before/after): score = 0.9-1.0
        - Approaching (within 2x pause_before): score = 0.3-0.9 (linear decay)
        - Receding (within 2x pause_after):   score = 0.3-0.9 (linear decay)
        - Far: score = 0.0

      Final score = max across all relevant events.
    """

    def compute(
        self,
        now: datetime,
        events: List[EconomicEvent],
    ) -> NewsRiskLevel:
        """
        Compute Gold risk level at `now` given `events`.

        Args:
            now: Current time (tz-aware, any tz).
            events: List of EconomicEvent (any time horizon).

        Returns:
            NewsRiskLevel with score, action, multiplier, flags.
        """
        now = to_et(now)
        relevant = [
            e for e in events if e.gold_importance >= THRESHOLD_MONITOR
        ]
        if not relevant:
            return NewsRiskLevel(
                score=0.0, action="NORMAL", position_multiplier=1.0,
                is_blocked=False,
            )

        best_score = 0.0
        best_event: Optional[EconomicEvent] = None
        nearest_upcoming: Optional[EconomicEvent] = None
        nearest_recent: Optional[EconomicEvent] = None
        mins_to_next = 9999
        mins_since_last = 9999

        for ev in relevant:
            mins = minutes_diff(now, ev.timestamp)   # +future / -past
            score = self._event_score(mins, ev)

            if score > best_score:
                best_score = score
                best_event = ev

            # Track nearest upcoming
            if mins > 0 and mins < mins_to_next:
                mins_to_next = int(mins)
                nearest_upcoming = ev

            # Track nearest recent (inside post-event window)
            if mins <= 0 and abs(mins) < mins_since_last:
                mins_since_last = int(abs(mins))
                nearest_recent = ev

        action, multiplier = _score_to_action(best_score)
        is_blocked = best_score >= 0.90

        block_reason = ""
        if is_blocked and best_event is not None:
            mins = minutes_diff(now, best_event.timestamp)
            if mins > 0:
                block_reason = (
                    f"{best_event.name} ({best_event.country}) "
                    f"in {int(mins)} min â€” gold_imp={best_event.gold_importance:.1f}"
                )
            else:
                block_reason = (
                    f"{best_event.name} ({best_event.country}) "
                    f"{int(abs(mins))} min ago â€” gold_imp={best_event.gold_importance:.1f}"
                )

        nearest = nearest_upcoming or nearest_recent or best_event

        return NewsRiskLevel(
            score=round(best_score, 4),
            action=action,
            position_multiplier=multiplier,
            is_blocked=is_blocked,
            block_reason=block_reason,
            nearest_event=nearest,
            mins_to_next=mins_to_next,
            mins_since_last=mins_since_last,
        )

    def _event_score(self, mins_to_event: float, ev: EconomicEvent) -> float:
        """
        Compute risk score for a single event.

        mins_to_event: positive = future, negative = past.
        Score tiers (based on gold_importance):
          BLOCK   (gold_imp >= 2.5): max_score=1.00, window_entry=0.90 -> BLOCKED
          CAUTION (gold_imp >= 1.5): max_score=0.85, window_entry=0.50 -> REDUCED
          MONITOR (gold_imp >= 0.5): max_score=0.45, window_entry=0.30 -> CAUTION

        Zones:
          Blackout pre-event  (0 < mins <= pb): window_entry â†’ max_score
          Blackout post-event (-pa <= mins <= 0): max_score â†’ window_entry * 0.6
          Approaching         (pb < mins <= pb*2): 0.0 â†’ window_entry
          Far                 (outside above): 0.0
        """
        pb = ev.pause_before_min
        pa = ev.pause_after_min
        gi = ev.gold_importance

        if gi >= THRESHOLD_BLOCK:
            max_score     = 1.00
            window_entry  = 0.90    # immediately BLOCKED on entering window
        elif gi >= THRESHOLD_CAUTION:
            max_score     = 0.85
            window_entry  = 0.50    # CAUTION on entering window
        elif gi >= THRESHOLD_MONITOR:
            max_score     = 0.45
            window_entry  = 0.30
        else:
            return 0.0

        # Inside blackout (pre-event window): window_entry â†’ max_score
        if 0 < mins_to_event <= pb:
            frac = (pb - mins_to_event) / pb   # 0 at window entry, 1 at event
            return min(max_score, window_entry + frac * (max_score - window_entry))

        # Inside cool-down (post-event window): max_score â†’ 0.0
        if -pa <= mins_to_event <= 0:
            frac = abs(mins_to_event) / pa     # 0 at event, 1 at end of cool-down
            return max(0.0, max_score - frac * max_score)

        # Approaching (2x pre-event buffer): 0.0 â†’ window_entry (linear ramp)
        if pb < mins_to_event <= pb * 2:
            frac = (pb * 2 - mins_to_event) / pb   # 0 at 2x, 1 at 1x
            return min(window_entry, frac * window_entry)

        return 0.0

    def compute_for_bar(
        self,
        bar_ts: datetime,
        events: List[EconomicEvent],
    ) -> dict:
        """
        Compute all news features for one M15 bar.
        Returns dict matching NewsFeatures fields.
        """
        bar_ts = to_et(bar_ts)
        risk = self.compute(bar_ts, events)

        # Nearest Gold-relevant event (gold_imp >= MONITOR)
        relevant = [e for e in events if e.gold_importance >= THRESHOLD_MONITOR]

        nearest_ev: Optional[EconomicEvent] = None
        nearest_abs_mins = float("inf")
        for ev in relevant:
            m = abs(minutes_diff(bar_ts, ev.timestamp))
            if m < nearest_abs_mins:
                nearest_abs_mins = m
                nearest_ev = ev

        nearest_name    = nearest_ev.name    if nearest_ev else ""
        nearest_country = nearest_ev.country if nearest_ev else ""
        nearest_gold_imp = nearest_ev.gold_importance    if nearest_ev else 0.0
        nearest_raw_imp  = nearest_ev.raw_importance     if nearest_ev else 0
        nearest_rel      = nearest_ev.country_relevance  if nearest_ev else 0.0

        # Events on this calendar date
        bar_date = bar_ts.date()
        today_events = [
            e for e in relevant
            if to_et(e.timestamp).date() == bar_date
        ]
        today_high = sum(1 for e in today_events if e.gold_importance >= THRESHOLD_BLOCK)
        today_med  = sum(1 for e in today_events if
                         THRESHOLD_CAUTION <= e.gold_importance < THRESHOLD_BLOCK)

        # Blackout: inside pause window of a BLOCK-tier event
        in_blackout = False
        for ev in relevant:
            if ev.gold_importance < THRESHOLD_BLOCK:
                continue
            mins = minutes_diff(bar_ts, ev.timestamp)
            if -ev.pause_after_min <= mins <= ev.pause_before_min:
                in_blackout = True
                break

        return {
            "news_gold_risk":         round(risk.score, 4),
            "news_gold_importance":   round(nearest_gold_imp, 4),
            "news_raw_importance":    nearest_raw_imp,
            "news_country_relevance": round(nearest_rel, 4),
            "news_mins_to_next":      risk.mins_to_next,
            "news_mins_since_last":   risk.mins_since_last,
            "news_in_blackout":       in_blackout,
            "news_events_today_high": today_high,
            "news_events_today_med":  today_med,
            "news_nearest_event":     nearest_name,
            "news_nearest_country":   nearest_country,
        }


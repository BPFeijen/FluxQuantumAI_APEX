"""
APEX GOLD News Provider â€” Main Interface
Entry point for ML model and live system.
Default asset: GC / XAUUSD Gold.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

import yaml

from .economic_calendar import TradingEconomicsCalendar
from .risk_calculator import NewsRiskCalculator
from .alpha_vantage import AlphaVantageProvider
from .events import EconomicEvent, NewsRiskLevel, NewsResult, NewsFeatures
from .time_utils import now_et, to_et, minutes_diff

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "news_config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class NewsProvider:
    """
    Main interface for news risk assessment in WeeklyGold.

    Usage:
        provider = NewsProvider()
        status   = provider.get_news_status()          # live check
        blocked  = provider.is_blocked()               # quick gate
        features = provider.get_features_for_bar(ts)   # backtest
    """

    def __init__(self, config_path: Optional[str] = None):
        cfg_path = Path(config_path) if config_path else _CONFIG_PATH
        with open(cfg_path, encoding="utf-8") as fh:
            self._cfg = yaml.safe_load(fh)

        av_cfg = self._cfg.get("alpha_vantage", {})
        te_cfg = self._cfg.get("tradingeconomics", {})

        self.calendar   = TradingEconomicsCalendar()
        self.risk_calc  = NewsRiskCalculator()
        self.sentiment  = AlphaVantageProvider(
            api_key=av_cfg.get("api_key", "QGKMJA8AVOFQLMDG"),
            tickers=av_cfg.get("tickers", ["GLD", "IAU"]),
            topics=av_cfg.get("topics", ["economy_fiscal", "economy_monetary"]),
            cache_minutes=av_cfg.get("cache_minutes", 30),
        )
        self._event_window_hours = 48   # fetch events Â±48h around now
        self._history_cache: Dict[str, List[EconomicEvent]] = {}

    # ------------------------------------------------------------------ #
    #  Live interface
    # ------------------------------------------------------------------ #

    def get_news_status(self, now: Optional[datetime] = None) -> NewsResult:
        """
        Returns full news status for Gold trading at `now`.
        Fetches today + tomorrow from TradingEconomics.
        """
        if now is None:
            now = now_et()
        now = to_et(now)

        events = self._fetch_window(now)
        risk   = self.risk_calc.compute(now, events)

        # Split into upcoming / recent
        upcoming = sorted(
            [e for e in events if minutes_diff(now, e.timestamp) > 0],
            key=lambda e: e.timestamp,
        )[:5]
        recent = sorted(
            [e for e in events if minutes_diff(now, e.timestamp) <= 0],
            key=lambda e: e.timestamp,
            reverse=True,
        )[:3]

        # Optional: sentiment (non-fatal)
        sent_score, sent_label, sent_avail = self.sentiment.get_sentiment()

        return NewsResult(
            timestamp=now,
            risk=risk,
            upcoming_events=upcoming,
            recent_events=recent,
            sentiment_score=sent_score,
            sentiment_label=sent_label,
            sentiment_available=sent_avail,
        )

    def is_blocked(self, now: Optional[datetime] = None) -> bool:
        """Quick check: is Gold trading blocked right now?"""
        if now is None:
            now = now_et()
        events = self._fetch_window(now)
        risk   = self.risk_calc.compute(now, events)
        return risk.is_blocked

    def get_position_multiplier(self, now: Optional[datetime] = None) -> float:
        """Returns position size multiplier (0.0 = blocked, 1.0 = full size)."""
        if now is None:
            now = now_et()
        events = self._fetch_window(now)
        risk   = self.risk_calc.compute(now, events)
        return risk.position_multiplier

    # ------------------------------------------------------------------ #
    #  Backtest interface
    # ------------------------------------------------------------------ #

    def get_features_for_bar(
        self,
        timestamp: datetime,
        events: Optional[List[EconomicEvent]] = None,
    ) -> dict:
        """
        Returns news feature dict for a single M15 bar.
        Used by add_news_features.py for backtest parquet enrichment.

        Args:
            timestamp: Bar timestamp (tz-aware or naive UTC).
            events: Pre-fetched event list (pass for bulk; None = fetch live).

        Returns:
            dict with keys matching NewsFeatures fields.
        """
        ts = to_et(timestamp)

        if events is None:
            events = self._fetch_window(ts)

        return self.risk_calc.compute_for_bar(ts, events)

    def load_history_for_period(
        self,
        start_date: str,
        end_date: str,
        min_importance: int = 2,
    ) -> List[EconomicEvent]:
        """
        Fetch and cache historical events for backtest.
        start_date / end_date: "YYYY-MM-DD"
        """
        cache_key = f"{start_date}_{end_date}_{min_importance}"
        if cache_key in self._history_cache:
            return self._history_cache[cache_key]

        events = self.calendar.fetch_historical(
            start_date, end_date, min_importance=min_importance
        )
        self._history_cache[cache_key] = events
        return events

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _fetch_window(self, now: datetime) -> List[EconomicEvent]:
        """Fetch events Â±48h around now (with today-cache)."""
        from datetime import date
        start = (now - timedelta(hours=self._event_window_hours)).date()
        end   = (now + timedelta(hours=self._event_window_hours)).date()
        return self.calendar.fetch_calendar(
            start.isoformat(), end.isoformat(), min_importance=2
        )


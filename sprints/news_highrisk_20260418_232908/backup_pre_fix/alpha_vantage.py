"""
WeeklyGold News Provider — Alpha Vantage Sentiment (Optional Layer)
Sentiment for Gold: tickers GLD, IAU. Topics: fiscal, monetary.
Rate limit: 25 req/day. Failures are non-fatal.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from .time_utils import now_utc

logger = logging.getLogger(__name__)

_API_KEY   = "QGKMJA8AVOFQLMDG"
_BASE_URL  = "https://www.alphavantage.co/query"
_TICKERS   = ["GLD", "IAU"]
_TOPICS    = ["economy_fiscal", "economy_monetary"]
_CACHE_MIN = 30   # minutes


class AlphaVantageProvider:
    """
    Optional sentiment layer from Alpha Vantage News & Sentiment API.
    Returns a float score (-1.0 bearish → +1.0 bullish) for Gold.
    If the API is unavailable, returns 0.0 (neutral) silently.
    """

    def __init__(
        self,
        api_key: str = _API_KEY,
        tickers: list = None,
        topics: list = None,
        cache_minutes: int = _CACHE_MIN,
    ):
        self.api_key = api_key
        self.tickers = tickers or _TICKERS
        self.topics  = topics  or _TOPICS
        self.cache_minutes = cache_minutes

        self._cache_score: Optional[float] = None
        self._cache_label: str = "Neutral"
        self._cache_time: Optional[datetime] = None

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def get_sentiment(self) -> tuple:
        """
        Returns (score: float, label: str, available: bool).
        score: -1.0 to +1.0
        label: Bearish / Somewhat-Bearish / Neutral / Somewhat-Bullish / Bullish
        available: False if API call failed
        """
        now = now_utc()
        if self._is_cached(now):
            return self._cache_score, self._cache_label, True

        try:
            score, label = self._fetch()
            self._cache_score = score
            self._cache_label = label
            self._cache_time  = now
            return score, label, True
        except Exception as exc:
            logger.warning("Alpha Vantage sentiment unavailable: %s", exc)
            return 0.0, "Neutral", False

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _is_cached(self, now: datetime) -> bool:
        if self._cache_score is None or self._cache_time is None:
            return False
        age_minutes = (now - self._cache_time).total_seconds() / 60
        return age_minutes < self.cache_minutes

    def _fetch(self) -> tuple:
        """Fetch and aggregate sentiment across tickers."""
        scores = []
        for ticker in self.tickers:
            s = self._fetch_ticker(ticker)
            if s is not None:
                scores.append(s)
            time.sleep(0.5)   # rate limit buffer

        if not scores:
            return 0.0, "Neutral"

        avg = sum(scores) / len(scores)
        return avg, self._score_to_label(avg)

    def _fetch_ticker(self, ticker: str) -> Optional[float]:
        """Fetch news sentiment for one ticker from Alpha Vantage."""
        topics_str = ",".join(self.topics)
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "topics": topics_str,
            "limit": 50,
            "apikey": self.api_key,
        }
        resp = requests.get(_BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if "feed" not in data:
            logger.debug("AV: no feed for %s", ticker)
            return None

        feed = data["feed"]
        if not feed:
            return None

        # Extract ticker-specific sentiment from each article
        ticker_scores = []
        for article in feed:
            for ts in article.get("ticker_sentiment", []):
                if ts.get("ticker") == ticker:
                    try:
                        score = float(ts["ticker_sentiment_score"])
                        ticker_scores.append(score)
                    except (KeyError, ValueError):
                        pass

        if not ticker_scores:
            # Fall back to overall article sentiment
            try:
                overall = [
                    float(a["overall_sentiment_score"])
                    for a in feed
                    if "overall_sentiment_score" in a
                ]
                return sum(overall) / len(overall) if overall else None
            except (ValueError, TypeError):
                return None

        return sum(ticker_scores) / len(ticker_scores)

    @staticmethod
    def _score_to_label(score: float) -> str:
        if score <= -0.35:
            return "Bearish"
        if score <= -0.15:
            return "Somewhat-Bearish"
        if score <= 0.15:
            return "Neutral"
        if score <= 0.35:
            return "Somewhat-Bullish"
        return "Bullish"

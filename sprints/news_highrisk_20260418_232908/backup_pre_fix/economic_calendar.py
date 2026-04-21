"""
APEX GOLD News Provider â€” TradingEconomics API Client
Replaces Investing.com scraper. Free tier: guest:guest (1000 req/day).

API Behaviour (guest:guest tier):
  - Single-country queries only (multi-country comma-list = 403)
  - Only "united states" reliably accessible on guest tier
  - Date range filtering works for US; other countries return 403
  - A paid API key unlocks all countries and removes per-request limits
  - System degrades gracefully: unavailable countries return []
"""
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

import requests

from .events import EconomicEvent
from .time_utils import parse_te_datetime, to_et

logger = logging.getLogger(__name__)

_COUNTRY_RELEVANCE_PATH = Path(__file__).parent / "config" / "country_relevance_gold.json"


def _load_country_relevance() -> Dict[str, float]:
    with open(_COUNTRY_RELEVANCE_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


class TradingEconomicsCalendar:
    """
    TradingEconomics API client.
    Fetches Gold-relevant economic events with country-adjusted importance.
    Queries each country individually (guest tier does not support multi-country).
    """

    BASE_URL = "https://api.tradingeconomics.com"
    API_KEY = "guest:guest"

    # Countries to fetch (Gold-relevant, queried one at a time)
    GOLD_COUNTRIES = [
        "united states", "china", "euro area",
        "united kingdom", "japan", "switzerland",
        "australia", "canada", "india",
    ]

    EVENT_CONFIG = {
        "FOMC": {
            "impact": "CRITICAL",
            "pause_before": 30,
            "pause_after": 60,
            "keywords": ["fomc", "federal reserve", "fed rate",
                         "interest rate decision", "fed funds"],
        },
        "NFP": {
            "impact": "CRITICAL",
            "pause_before": 30,
            "pause_after": 30,
            "keywords": ["nonfarm payroll", "non-farm payroll",
                         "nfp", "employment change"],
        },
        "CPI": {
            "impact": "HIGH",
            "pause_before": 30,
            "pause_after": 15,
            "keywords": ["cpi", "consumer price index", "inflation rate"],
        },
        "GDP": {
            "impact": "HIGH",
            "pause_before": 30,
            "pause_after": 15,
            "keywords": ["gdp", "gross domestic product"],
        },
        "PPI": {
            "impact": "HIGH",
            "pause_before": 15,
            "pause_after": 15,
            "keywords": ["ppi", "producer price index"],
        },
        "FED_SPEECH": {
            "impact": "HIGH",
            "pause_before": 15,
            "pause_after": 30,
            "keywords": ["fed chair", "powell", "fed speak", "fomc member",
                         "fed governor", "waller", "jefferson", "williams"],
        },
        "ECB": {
            "impact": "HIGH",
            "pause_before": 30,
            "pause_after": 30,
            "keywords": ["ecb", "european central bank", "lagarde",
                         "ecb interest rate"],
        },
        "BOJ": {
            "impact": "MEDIUM",
            "pause_before": 15,
            "pause_after": 15,
            "keywords": ["boj", "bank of japan", "ueda",
                         "japan interest rate"],
        },
        "UNEMPLOYMENT": {
            "impact": "MEDIUM",
            "pause_before": 15,
            "pause_after": 10,
            "keywords": ["unemployment", "jobless claims",
                         "initial claims", "continuing claims"],
        },
        "ISM": {
            "impact": "MEDIUM",
            "pause_before": 15,
            "pause_after": 10,
            "keywords": ["ism manufacturing", "ism services",
                         "pmi", "purchasing managers"],
        },
        "RETAIL_SALES": {
            "impact": "MEDIUM",
            "pause_before": 15,
            "pause_after": 10,
            "keywords": ["retail sales"],
        },
    }

    _IMPORTANCE_DEFAULTS = {
        3: {"impact": "HIGH",   "pause_before": 30, "pause_after": 15},
        2: {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10},
        1: {"impact": "LOW",    "pause_before": 5,  "pause_after": 5},
    }

    def __init__(self):
        self.country_relevance = _load_country_relevance()
        self._cache: Dict[str, List[EconomicEvent]] = {}
        self._cache_time: Optional[datetime] = None
        self._cache_minutes = 60

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def fetch_calendar(
        self,
        start_date: str,
        end_date: str,
        min_importance: int = 2,
    ) -> List[EconomicEvent]:
        """
        Fetch economic events for all Gold-relevant countries.
        Queries each country separately (guest tier restriction).
        Countries returning 403 are silently skipped.

        Args:
            start_date / end_date: "YYYY-MM-DD"
            min_importance: 1/2/3 (TradingEconomics importance filter)

        Returns:
            Events sorted by timestamp (ET). May be incomplete if guest tier
            restricts access to some countries.
        """
        events: List[EconomicEvent] = []

        for country in self.GOLD_COUNTRIES:
            country_events = self._fetch_single_country(
                country, start_date, end_date, min_importance
            )
            events.extend(country_events)
            if len(self.GOLD_COUNTRIES) > 1:
                time.sleep(0.3)   # polite rate-limiting between countries

        return sorted(events, key=lambda e: e.timestamp)

    def _fetch_single_country(
        self,
        country: str,
        start_date: str,
        end_date: str,
        min_importance: int = 2,
    ) -> List[EconomicEvent]:
        """
        Fetch events for one country over a date range.
        Returns [] on 403 (graceful degradation for guest tier).
        """
        url = f"{self.BASE_URL}/calendar/country/{country}/{start_date}/{end_date}"
        params = {"c": self.API_KEY, "importance": min_importance, "f": "json"}

        try:
            resp = requests.get(url, params=params, timeout=30)

            if resp.status_code == 403:
                logger.debug(
                    "TE 403 for '%s' (not available in guest tier)", country
                )
                return []

            resp.raise_for_status()
            data = resp.json()

        except requests.RequestException as exc:
            logger.warning("TE API error for '%s': %s", country, exc)
            return []
        except (ValueError, KeyError) as exc:
            logger.warning("TE parse error for '%s': %s", country, exc)
            return []

        if not isinstance(data, list):
            return []

        result: List[EconomicEvent] = []
        seen_ids = set()
        for item in data:
            # Filter by date (guest tier sometimes ignores date params)
            item_date = item.get("Date", "")[:10]
            if item_date and not (start_date <= item_date <= end_date):
                continue

            cid = item.get("CalendarId")
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)

            ev = self._parse_event(item)
            if ev is not None:
                result.append(ev)

        return result

    def fetch_today(self) -> List[EconomicEvent]:
        """Fetch today's events with 1-hour in-memory cache."""
        from datetime import date
        today = date.today().isoformat()

        now = datetime.utcnow()
        if (
            today in self._cache
            and self._cache_time is not None
            and (now - self._cache_time).total_seconds() < self._cache_minutes * 60
        ):
            return self._cache[today]

        events = self.fetch_calendar(today, today, min_importance=2)
        self._cache[today] = events
        self._cache_time = now
        return events

    def fetch_historical(
        self,
        start_date: str,
        end_date: str,
        min_importance: int = 2,
        sleep_between_chunks: float = 1.0,
    ) -> List[EconomicEvent]:
        """
        Fetch historical events in 30-day chunks for backtest use.
        Each chunk fetches all GOLD_COUNTRIES sequentially.

        Args:
            start_date / end_date: "YYYY-MM-DD"
            sleep_between_chunks: Seconds to sleep between date chunks.

        Note: guest:guest tier returns at most 3 events per request.
        Full historical data requires a paid API key.
        """
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end   = datetime.strptime(end_date,   "%Y-%m-%d").date()

        all_events: List[EconomicEvent] = []
        current = start
        chunk_num = 0

        while current <= end:
            chunk_end = min(current + timedelta(days=29), end)
            s_str = current.strftime("%Y-%m-%d")
            e_str = chunk_end.strftime("%Y-%m-%d")
            chunk_num += 1

            logger.info("Chunk %d: %s -> %s", chunk_num, s_str, e_str)
            chunk = self.fetch_calendar(s_str, e_str, min_importance=min_importance)
            all_events.extend(chunk)

            current = chunk_end + timedelta(days=1)
            if current <= end:
                time.sleep(sleep_between_chunks)

        return sorted(all_events, key=lambda e: e.timestamp)

    def fetch_all_countries_importance3(
        self,
        start_date: str,
        end_date: str,
    ) -> List[EconomicEvent]:
        """
        Fetch global calendar at importance=3 (no country filter).
        Used for country-filter validation test.
        Falls back to per-country if global endpoint returns 403.
        """
        # Try global endpoint first
        url = f"{self.BASE_URL}/calendar/{start_date}/{end_date}"
        params = {"c": self.API_KEY, "importance": 3, "f": "json"}

        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.ok:
                data = resp.json()
                if isinstance(data, list) and data:
                    result = []
                    seen_ids = set()
                    for item in data:
                        item_date = item.get("Date", "")[:10]
                        if item_date and not (start_date <= item_date <= end_date):
                            continue
                        cid = item.get("CalendarId")
                        if cid and cid in seen_ids:
                            continue
                        if cid:
                            seen_ids.add(cid)
                        ev = self._parse_event_raw(item)
                        if ev:
                            result.append(ev)
                    if result:
                        return result
        except Exception as exc:
            logger.debug("Global endpoint failed: %s", exc)

        # Fallback: per-country with importance=3
        events = []
        for country in self.GOLD_COUNTRIES:
            ev = self._fetch_single_country(country, start_date, end_date, 3)
            events.extend(ev)
            time.sleep(0.2)
        return events

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _parse_event(self, item: dict) -> Optional[EconomicEvent]:
        """Parse a TE API item into EconomicEvent, or None if irrelevant."""
        country = item.get("Country", "").strip()
        relevance = self.country_relevance.get(country, 0.0)

        if relevance == 0.0:
            return None

        raw_importance = int(item.get("Importance", 1) or 1)
        gold_importance = raw_importance * relevance

        event_name = item.get("Event", "Unknown").strip()
        classification = self._classify_event(event_name)

        if classification["impact"] == "CRITICAL":
            gold_importance = max(gold_importance, 3.0)

        return EconomicEvent(
            timestamp=parse_te_datetime(item.get("Date", "")),
            name=event_name,
            country=country,
            raw_importance=raw_importance,
            country_relevance=relevance,
            gold_importance=gold_importance,
            impact=classification["impact"],
            forecast=str(item["Forecast"]) if item.get("Forecast") is not None else None,
            previous=str(item["Previous"]) if item.get("Previous") is not None else None,
            actual=str(item["Actual"])    if item.get("Actual")   is not None else None,
            pause_before_min=classification["pause_before"],
            pause_after_min=classification["pause_after"],
            source="tradingeconomics",
            calendar_id=str(item["CalendarId"]) if item.get("CalendarId") else None,
        )

    def _parse_event_raw(self, item: dict) -> Optional[EconomicEvent]:
        """Parse without country filter (for validation / global endpoint)."""
        country = item.get("Country", "").strip()
        relevance = self.country_relevance.get(country, 0.0)
        raw_importance = int(item.get("Importance", 1) or 1)
        gold_importance = raw_importance * relevance
        event_name = item.get("Event", "Unknown").strip()
        classification = self._classify_event(event_name)
        if classification["impact"] == "CRITICAL" and relevance > 0:
            gold_importance = max(gold_importance, 3.0)

        return EconomicEvent(
            timestamp=parse_te_datetime(item.get("Date", "")),
            name=event_name,
            country=country,
            raw_importance=raw_importance,
            country_relevance=relevance,
            gold_importance=gold_importance,
            impact=classification["impact"],
            forecast=None, previous=None, actual=None,
            pause_before_min=classification["pause_before"],
            pause_after_min=classification["pause_after"],
            source="tradingeconomics",
            calendar_id=str(item["CalendarId"]) if item.get("CalendarId") else None,
        )

    def _classify_event(self, event_name: str) -> dict:
        """Match event name against EVENT_CONFIG. Returns classification dict."""
        name_lower = event_name.lower()
        for _etype, cfg in self.EVENT_CONFIG.items():
            for kw in cfg["keywords"]:
                if kw in name_lower:
                    return {
                        "impact": cfg["impact"],
                        "pause_before": cfg["pause_before"],
                        "pause_after": cfg["pause_after"],
                    }
        return self._IMPORTANCE_DEFAULTS.get(
            2,
            {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10},
        )


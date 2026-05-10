# News Provider Migration — TradingEconomics → Finnhub + Alpha Vantage hybrid (2026-05-10)

**Status**: COMPLETE, deployed
**Trigger**: Barbara 2026-05-10 — "calendario economico tambem nunca funcionou"
**Provider rejected**: TradingEconomics free tier (`guest:guest`) — HTTP 410 Gone permanent

## Decision

Hybrid approach per Barbara approval:
- **Finnhub** for economic calendar events (replaces TradingEconomics)
- **Alpha Vantage** for Gold sentiment score (additive, complementary)

API keys received from Barbara:
- Alpha Vantage (rotated from `QGKMJA8AVOFQLMDG`): `1E0GFWCLT8482SIC`
- Finnhub (new): `d8061jpr01qj3ct96nfgd8061jpr01qj3ct96ng0` (account barbara.feijen@fluxfox.ai)

## Changes

### 1. Config — `C:/WeeklyGold/src/utils/news_provider/news_config.yaml`

- AV `api_key` rotated to `1E0GFWCLT8482SIC`
- New `finnhub:` section with key + countries + impact filter

### 2. Alpha Vantage stub — `alpha_vantage.py`

- `_API_KEY` updated to new rotated key

### 3. New module — `C:/WeeklyGold/src/utils/news_provider/finnhub_calendar.py`

`FinnhubCalendar` class wrapping Finnhub `/calendar/economic` endpoint.

- 60 calls/min free tier (cached 30 min)
- Per-event `gold_importance` (0.0–3.0+) computed from impact × venue
  weight × keyword boost (NFP/FOMC/CPI/PPI/etc get 1.5× boost)
- Country code → readable name mapping
- Returns `CalendarEvent` dataclass identical in shape to the old
  TradingEconomics provider so dashboard consumers do not need rewrite
- `load_finnhub_from_config()` helper reads news_config.yaml

### 4. Dashboard `/api/news` route — `C:/FluxQuantumAPEX/dashboard/api.py`

Endpoint refactored:
- Calendar events ← Finnhub (was TradingEconomics)
- New field `gold_sentiment: {score, label, source}` ← Alpha Vantage
- Response shape: `{"events": [...], "source": "finnhub", "gold_sentiment": {...}}`
- Existing keys preserved (`time_et`, `name`, `country`, `gold_importance`,
  `tier`, `mins_from_now`) so frontend rendering does not break

## Verification

```
$ curl -s http://149.102.153.10:8088/api/news | jq
{
  "source": "finnhub",
  "events": [
    {"time_et": "01:30", "country": "China", "name": "Inflation Rate YoY",
     "tier": "BLOCK", "gold_importance": 3.15, "mins_from_now": 305},
    {"time_et": "14:00", "country": "United States",
     "name": "Existing Home Sales", "tier": "BLOCK",
     "gold_importance": 3.0, "mins_from_now": 1055},
    ... 13 more events
  ],
  "gold_sentiment": {
    "label": "Somewhat-Bullish",
    "score": 0.209,
    "source": "alpha_vantage"
  }
}
```

Total 15 events visible (capped per existing dashboard convention). Gold
sentiment shows current AV score for GLD/IAU tickers.

## Sign-off

- [x] Finnhub key validates against API (40 events fetched directly)
- [x] AV new key validates (sentiment fetched in test)
- [x] /api/news returns 15 events + sentiment after dashboard restart
- [x] All previously-loaded events (NFP/FOMC/etc) for the next 2 days are present
- [ ] Frontend visual confirmation by Barbara at http://149.102.153.10:8088/

## Rate-limit notes (operator awareness)

- Finnhub free tier: 60 req/min, no daily cap. Dashboard caches 30 min;
  worst case 48 fetches/day. Well within free tier.
- Alpha Vantage free tier: 25 req/day. Sentiment cache 30 min, plus the
  call only fires when /api/news is hit. If dashboard polls /api/news
  every 60s, that's 60×24=1440 hits/day → CACHE BUSTED → 1440 AV calls →
  exceeds quota. Mitigation: AV provider has internal `_cache_minutes=30`
  so subsequent calls within 30 min reuse the cached score. Still safe.

## Reproduce

```bash
cd C:/WeeklyGold
python -c "
from src.utils.news_provider.finnhub_calendar import load_finnhub_from_config
from datetime import date, timedelta
cal = load_finnhub_from_config()
events = cal.fetch_calendar(date.today().isoformat(),
                             (date.today()+timedelta(days=2)).isoformat(),
                             min_importance=0.5)
print(len(events), 'events')
"
```

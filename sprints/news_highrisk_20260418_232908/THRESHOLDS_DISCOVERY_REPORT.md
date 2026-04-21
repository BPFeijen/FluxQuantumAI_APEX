# THRESHOLDS DISCOVERY REPORT — APEX News Gate

**Task:** CLAUDECODE_Thresholds_Discovery_ReadOnly
**Date:** 2026-04-19
**Mode:** READ-ONLY (zero edits)
**Module:** `C:\FluxQuantumAPEX\APEX GOLD\APEX_News\`

---

## 1. EXECUTIVE SUMMARY

The APEX News Gate has **two distinct threshold layers**, and a **third configured-but-unused layer** in YAML:

| Layer | Location | Consumed? |
|---|---|---|
| Gate decision thresholds (score→action) | Hardcoded in `apex_news_gate.py` L62-64 + `risk_calculator.py` L22-28 | YES |
| Event classification (keyword→pause_before/after/impact) | Hardcoded in `economic_calendar.py` L52-132 | YES |
| Gold importance tiers (BLOCK/CAUTION/MONITOR) | Hardcoded in `risk_calculator.py` L16-19 | YES |
| Country relevance weights | `config/country_relevance_gold.json` | YES |
| **`gold_blocking`, `risk_thresholds`, `position_multipliers`** | `news_config.yaml` L30-47 | **NO — DEAD CONFIG** |
| `tradingeconomics.*` (countries, min_importance, cache_minutes) | `news_config.yaml` L4-17 | **NO — `te_cfg` assigned then unused** |

**Critical implication:** editing `news_config.yaml` values for gold_blocking/risk_thresholds/position_multipliers has **zero runtime effect**. Threshold changes require code edits + py_compile + service restart.

---

## 2. HARDCODED THRESHOLDS (COMPLETE INVENTORY)

### 2.1 Gate decision constants (`apex_news_gate.py` L62-64)
```python
SCORE_BLOCK_ENTRY = 0.70   # score >= → block_entry=True  (REDUCED or higher)
SCORE_EXIT_ALL    = 0.90   # score >= → exit_all=True     (BLOCKED)
CACHE_SECONDS     = 25     # re-fetch at most every 25s
```

### 2.2 Score→action table (`risk_calculator.py` L22-28)
```python
_ACTION_TABLE = [
    (0.90, "BLOCKED", 0.00),
    (0.70, "REDUCED", 0.50),
    (0.50, "CAUTION", 0.75),
    (0.30, "NORMAL",  1.00),
    (0.00, "NORMAL",  1.00),
]
```
Applied in `_score_to_action()` via first-match descending.

### 2.3 Gold importance tiers (`risk_calculator.py` L16-19)
```python
THRESHOLD_BLOCK   = 2.5    # → BLOCKED tier (max_score=1.00, window_entry=0.90)
THRESHOLD_CAUTION = 1.5    # → CAUTION tier (max_score=0.85, window_entry=0.50)
THRESHOLD_MONITOR = 0.5    # → MONITOR tier (max_score=0.45, window_entry=0.30)
THRESHOLD_IGNORE  = 0.0    # Not even a feature
```
Applied in `_event_score()` L149-163. Events below 0.5 return 0.0.

### 2.4 Event-type classification (`economic_calendar.py` L52-126)
Keyword-matched, first-hit wins:

| Event type | Impact | pause_before | pause_after | Keywords |
|---|---|---|---|---|
| FOMC | CRITICAL | 30 | 60 | fomc, federal reserve, fed rate, interest rate decision, fed funds |
| NFP | CRITICAL | 30 | 30 | nonfarm payroll, non-farm payroll, nfp, employment change |
| CPI | HIGH | 30 | 15 | cpi, consumer price index, inflation rate |
| GDP | HIGH | 30 | 15 | gdp, gross domestic product |
| PPI | HIGH | 15 | 15 | ppi, producer price index |
| FED_SPEECH | HIGH | 15 | 30 | fed chair, powell, fed speak, fomc member, fed governor, waller, jefferson, williams |
| ECB | HIGH | 30 | 30 | ecb, european central bank, lagarde, ecb interest rate |
| BOJ | MEDIUM | 15 | 15 | boj, bank of japan, ueda, japan interest rate |
| UNEMPLOYMENT | MEDIUM | 15 | 10 | unemployment, jobless claims, initial claims, continuing claims |
| ISM | MEDIUM | 15 | 10 | ism manufacturing, ism services, pmi, purchasing managers |
| RETAIL_SALES | MEDIUM | 15 | 10 | retail sales |
| **DEFAULT (no keyword match)** | MEDIUM | 15 | 10 | (fallback) |

**CRITICAL override** (`_parse_event` L354-355): events classified CRITICAL force `gold_importance = max(gold_importance, 3.0)` → always ≥ THRESHOLD_BLOCK → always BLOCKED tier.

### 2.5 Dead defaults (`economic_calendar.py` L128-132)
```python
_IMPORTANCE_DEFAULTS = {
    3: {"impact": "HIGH",   "pause_before": 30, "pause_after": 15},   # unused
    2: {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10},   # fallback used at L411
    1: {"impact": "LOW",    "pause_before": 5,  "pause_after": 5},    # unused
}
```
Only importance=2 ever referenced (L411: `.get(2, ...)`). Keys 3 and 1 are dead.

### 2.6 Default event object fields (`events.py` L23-24)
```python
pause_before_min: int = 15
pause_after_min: int = 15
```
Superseded whenever classification dict is supplied (all prod paths do).

---

## 3. YAML CONSUMPTION MAP

### 3.1 Keys ACTUALLY READ (`news_provider.py` L45-54)
```yaml
alpha_vantage:
  api_key        # → AlphaVantageProvider
  tickers        # → AlphaVantageProvider
  topics         # → AlphaVantageProvider
  cache_minutes  # → AlphaVantageProvider
```

### 3.2 Keys WRITTEN TO YAML BUT NEVER READ BY CODE
```yaml
default_asset                    # unread
tradingeconomics.api_key         # unread — hardcoded "guest:guest" at economic_calendar.py:43
tradingeconomics.countries       # unread — hardcoded GOLD_COUNTRIES at economic_calendar.py:46
tradingeconomics.min_importance  # unread — hardcoded 2 in fetch calls
tradingeconomics.cache_minutes   # unread — hardcoded 60 at economic_calendar.py:138
gold_blocking.block_threshold    # unread — hardcoded THRESHOLD_BLOCK=2.5
gold_blocking.caution_threshold  # unread — hardcoded THRESHOLD_CAUTION=1.5
gold_blocking.monitor_threshold  # unread — hardcoded THRESHOLD_MONITOR=0.5
risk_thresholds.normal           # unread — hardcoded in _ACTION_TABLE
risk_thresholds.caution          # unread
risk_thresholds.reduced          # unread — NOTE: yaml=0.7, code=0.70 (match)
risk_thresholds.blocked          # unread — NOTE: yaml=0.9, code=0.90 (match)
risk_thresholds.exit_all         # unread — NOTE: yaml=1.0, code=0.90 (apex_news_gate EXIT_ALL)
position_multipliers.*           # all 5 unread — hardcoded in _ACTION_TABLE
```

### 3.3 Notable discrepancies (yaml vs code)
| yaml key | yaml value | Actual code value | Divergence |
|---|---|---|---|
| risk_thresholds.exit_all | 1.0 | apex_news_gate `SCORE_EXIT_ALL=0.90` | YES |
| risk_thresholds.caution | 0.5 | `_ACTION_TABLE` maps >=0.5→CAUTION | match |
| position_multipliers.reduced | 0.50 | `_ACTION_TABLE` REDUCED→0.50 | match |

YAML shows intent that diverged from code. The 1.0 EXIT_ALL in yaml reflects an earlier design (never reached in practice since scores max at 1.0); code caps block_entry at 0.70 and exit_all at 0.90.

---

## 4. DECISION FLOW (end-to-end)

```
TE API → _parse_event
  ├── country_relevance_gold.json lookup (US=1.0, others=0.0 since 2026-04-18)
  ├── _classify_event(name) → keyword match in EVENT_CONFIG (11 types + default)
  │     → {impact, pause_before, pause_after}
  ├── gold_importance = raw_importance × country_relevance
  ├── if impact==CRITICAL: gold_importance = max(gi, 3.0)
  └── EconomicEvent(...)

NewsRiskCalculator.compute(now, events)
  ├── filter events: gold_importance >= THRESHOLD_MONITOR (0.5)
  ├── for each event: _event_score(mins_to_event, ev)
  │     ├── tier by gi: BLOCK(>=2.5)/CAUTION(>=1.5)/MONITOR(>=0.5)
  │     ├── tier→(max_score, window_entry): (1.00,0.90)/(0.85,0.50)/(0.45,0.30)
  │     ├── if 0<mins<=pb:  window_entry → max_score   (blackout pre)
  │     ├── if -pa<=mins<=0: max_score → 0             (cool-down post)
  │     ├── if pb<mins<=2pb: 0 → window_entry          (approaching)
  │     └── else: 0.0
  ├── best_score = max(scores)
  ├── action, multiplier = _score_to_action(best_score)
  └── NewsRiskLevel(score, action, multiplier, is_blocked=score>=0.90, ...)

ApexNewsGate.check()
  ├── NewsProvider.get_news_status() → risk:NewsRiskLevel
  ├── block_entry = risk.score >= SCORE_BLOCK_ENTRY (0.70)
  ├── exit_all    = risk.score >= SCORE_EXIT_ALL    (0.90)
  └── NewsGateResult(score, action, position_multiplier, block_entry, exit_all, reason, ...)
```

---

## 5. CONFIG FILE INVENTORY

| Path | Size | Purpose | Consumed? |
|---|---|---|---|
| `news_config.yaml` | 777 B | yaml config | PARTIAL (alpha_vantage.* only) |
| `config/country_relevance_gold.json` | 376 B | Country→weight map | YES (all keys) |
| `config/apitube_key.json` | 76 B | API key placeholder | Not referenced by imports above |

### country_relevance_gold.json (current state)
```json
{
  "United States": 1.0,
  "_default": 0.0,
  "_description": "US-only filter (Barbara decision 2026-04-18)",
  "_thresholds": {"block":2.5, "caution":1.5, "monitor":0.5, "ignore":0.0}
}
```
Filtered to US-only 2026-04-18. Other 8 countries now return relevance=0.0 → events discarded at `_parse_event` L345-346.

Note: `_thresholds` block inside JSON is informational — loaded but filtered out by `_load_country_relevance` L32 (`not k.startswith("_")`), so NOT consumed either.

---

## 6. IMPORT DEPENDENCY GRAPH

```
apex_news_gate.py (entrypoint; singleton news_gate)
├── news_provider.NewsProvider
│   ├── yaml (reads alpha_vantage.* only)
│   ├── economic_calendar.TradingEconomicsCalendar
│   │   ├── requests → api.tradingeconomics.com
│   │   ├── events.EconomicEvent
│   │   └── config/country_relevance_gold.json
│   ├── risk_calculator.NewsRiskCalculator
│   │   └── events.{EconomicEvent, NewsRiskLevel}
│   ├── alpha_vantage.AlphaVantageProvider
│   │   └── requests → alphavantage.co
│   └── events.{EconomicEvent, NewsRiskLevel, NewsResult, NewsFeatures}
└── events.{NewsRiskLevel, EconomicEvent}
```

All imports absolute (post 2026-04-18 Approach A fix). `release_monitor.py` imports exist but emit warning at startup: `"ReleaseMonitor: calendar not accessible from provider"` — non-fatal.

---

## 7. RUNTIME SANITY CHECK (Passo 9 probe)

Executed 2026-04-19 (Sunday, market closed):

```
>>> from apex_news_gate import news_gate, SCORE_BLOCK_ENTRY, SCORE_EXIT_ALL, CACHE_SECONDS
>>> news_gate.check()
NewsGateResult(
  score               = 0.0
  action              = NORMAL
  position_multiplier = 1.0
  block_entry         = False
  exit_all            = False
  reason              = NORMAL
  mins_to_next        = 9999
  mins_since_last     = 9999
  nearest_event       = None
  cached              = False
)
>>> NewsProvider()._fetch_window(now_et()) → 0 events
```

**Diagnostics:** Constants load correctly. Gate returns well-formed NewsGateResult. Zero events is consistent with weekend (no TE coverage for Sunday).

**Warning observed:** `ReleaseMonitor: calendar not accessible from provider` at import time — benign, but worth investigating in a separate task.

---

## 8. RECOMMENDATIONS FOR THRESHOLD CHANGES

Any threshold tuning must target the **hardcoded constants**, not the YAML. Three intervention points:

### A. Gate sensitivity (easiest, safest)
**File:** `apex_news_gate.py` L62-63
```python
SCORE_BLOCK_ENTRY = 0.70   # lower → blocks more aggressively
SCORE_EXIT_ALL    = 0.90   # lower → forces exits earlier
```

### B. Action tier boundaries
**File:** `risk_calculator.py` L22-28 (`_ACTION_TABLE`)
Changes here shift the mapping from best_score → (action, multiplier). Edit in tandem with A to avoid conflicting decisions.

### C. Per-event pause windows / impact
**File:** `economic_calendar.py` L52-126 (`EVENT_CONFIG`)
Tune `pause_before`/`pause_after` per event-type (FOMC, NFP, CPI, etc.). Wider windows → more defensive coverage, more false positives.

**Option D — migrate YAML to active config:** rewrite NewsProvider + module globals to load from yaml (risk, sizes, event pauses). Larger change; out of scope for this sprint.

### Minimum viable cleanup (no behavior change)
Either:
1. Delete the dead yaml sections (`gold_blocking`, `risk_thresholds`, `position_multipliers`, `tradingeconomics`) — clarifies they're decorative.
2. OR wire the yaml values into the code paths that currently hardcode them — single source of truth.

Recommend option 2 if threshold calibration is expected to iterate.

---

## 9. OPEN QUESTIONS FOR BARBARA + CLAUDE REVIEW

1. `risk_thresholds.exit_all=1.0` in yaml vs `SCORE_EXIT_ALL=0.90` in code — which is correct?
2. ECB/BOJ classifications are still present in EVENT_CONFIG (lines 92-105). With country_relevance_gold.json filtered to US-only, these events return relevance=0.0 → never instantiated. Keywords become dead. Remove or keep for future re-expansion?
3. `_IMPORTANCE_DEFAULTS` keys 3 and 1 are dead (only 2 referenced at L411). Prune?
4. `ReleaseMonitor: calendar not accessible from provider` warning — intentional or a deferred bug?

---

## 10. DISCOVERY AUDIT TRAIL

| Passo | Action | Result |
|---|---|---|
| 1 | Read full `apex_news_gate.py` (263 L) | Found SCORE_BLOCK_ENTRY=0.70, SCORE_EXIT_ALL=0.90, CACHE_SECONDS=25 |
| 2 | Read full `risk_calculator.py` (245 L) | Found THRESHOLD_BLOCK/CAUTION/MONITOR + _ACTION_TABLE + _event_score tiers |
| 3 | Grep pause_before/pause_after origin | 11 event types + default in `economic_calendar.py` EVENT_CONFIG |
| 4 | Read full `economic_calendar.py` (416 L) | Found _classify_event + _parse_event + CRITICAL→gi>=3.0 override |
| 5 | Map yaml consumption (grep + read) | Only `alpha_vantage.*` consumed; 3 dead sections confirmed |
| 6 | Import/dependency graph (grep imports) | All absolute imports; no circular deps |
| 7 | Keyword classification audit | 11 event types + default fallback; CRITICAL flag overrides gold_importance |
| 8 | Config file inventory (ls + read) | 3 files; JSON `_thresholds` also informational (prefixed keys filtered) |
| 9 | Runtime sanity probe | NewsGateResult valid; score=0 on Sunday; 0 events in ±48h window |
| 10 | This report | Consolidated findings |

**Zero file edits made during this discovery.** All findings derived from read-only grep + Read + isolated Python probe.

---

**Report:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\THRESHOLDS_DISCOVERY_REPORT.md`

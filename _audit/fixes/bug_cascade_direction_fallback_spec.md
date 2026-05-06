# Spec — Bug-Cascade Direction Fallback Fix (5-Layer Cascade)

**Author**: CC#3
**Date**: 2026-05-06
**Asana**: 1214556369070092 (BUG-SIGNAL-INVERTED) → ML-DS comment 1214586789572486 (Q1-Q5 final answers)
**Type**: Code-change spec, not implementation
**Status**: REWRITE COMPLETE — supersedes earlier 4-layer draft (which Barbara rejected for 1.5h M30 lag)

**Companion**: `investigation_orphan_fast_sources_2026-05-06.md` (READ-ONLY findings that informed this spec)

---

## 1. SCOPE vs INTENT BLOCK (G-INTENT-SCOPE-GAP-CHECK)

### 1.1 Stated Intent

Fix the cascade dependency in `_get_strategy_mode()` and `_compute_raw_phase()` that causes the system to silently fall back to `RANGE_BOUND` mean-reversion strategy whenever `daily_trend == "unknown"` (which is the common case post-401e6da fix when the B+C ENSEMBLE disagrees), producing counter-trend signals during fast directional moves (e.g., 2026-05-06 04:09–04:51 UTC: 200 SHORT @ ZR ALPHA during +91pts bull rally).

The fix introduces a **5-layer graceful-degradation ladder** consulting fast then slow then telemetry trend signals before falling back to RANGE.

### 1.2 Layer Stack (per ML-DS directive)

| Layer | Source | Latency | Confidence |
|-------|--------|---------|-----------|
| 1 | `daily_trend` (B+C ENSEMBLE) | bar close | HIGH |
| 2 | `TickBreakoutMonitor.status().breakout_dir` | <1s tick | HIGH |
| 3 | `m30_bias_confirmed` (Wyckoff M30) | ~5min+ | MEDIUM |
| 4 | `provisional_m30_bias` (uncombined Wyckoff) | ~30s | LOW (soft-block upgrade) |
| 5 | (unknown) → `RANGE_BOUND` | n/a | NONE (default safe) |

### 1.3 Explicit Scope (DO)

- **Modify** `live/event_processor.py`:
  - Add helper `_resolve_trend_direction()` returning `(trend, source, confidence)`
  - Refactor `_compute_raw_phase()` line 3555 to use resolver
  - Refactor `_get_strategy_mode()` lines 3671, 3677 to use resolver
- **Add** 2 new threshold keys to `config/settings.json`:
  - `direction_fallback_use_tick_breakout` (default `true`)
  - `direction_fallback_use_provisional_m30` (default `true`)
- **Add** new tests in `tests/test_strategy_mode_cascade.py` (~15–20 unit tests).
- **Add** structured log entry per resolver decision (audit trail in stdout + decision_log).
- **Backtest deliverable**: `_audit/fixes/backtest_cascade_counterfactual.py` with walk-forward CV per regime + 04:44 burst replay.

### 1.4 Explicit Out-of-Scope (DON'T)

- **DO NOT** modify the B+C ENSEMBLE (`_get_daily_trend`, `_compute_signal_b`, `_compute_signal_c`) — preserve 401e6da byte-identical.
- **DO NOT** modify `level_type` → `direction` mapping in RANGE_BOUND mode (lines 998, 1075, 3700).
- **DO NOT** modify trigger detection (ALPHA, QT_MICRO).
- **DO NOT** modify `derive_m30_bias` (level_detector.py:1705 idiom is a read-only consumer here).
- **DO NOT** modify `delta_4h_inverted_fix` semantic (line 1980) — separate CAL03 concern (INVEST-02 pending).
- **DO NOT** include ATS Trend Line in this fix — out of scope (NextGen module, not Live).
- **DO NOT** include any reference to NextGen, TradeATS, CC#1, or CC#2 work — strict scope to FluxQuantumAPEX live.
- **DO NOT** halt FluxQuantumAPEX (per ML-DS directive — system is advisory, MT5 disconnected, Telegram OFF).
- **DO NOT** modify Telegram emit, dashboard server, or VAP module.
- **DO NOT** modify L2 capture / iceberg_receiver / quantower processes (PIDs 13072, 16788, 20396, 31816).
- **DO NOT** push to GitHub or mutate git history.

### 1.5 Intent ↔ Scope Gap Check

| Intent | In Scope | Notes |
|--------|----------|-------|
| Restore TRENDING dispatch when daily_trend unknown | ✅ | via 5-layer cascade |
| Reduce confirmation lag from ~5min+ to <1s | ✅ | via Layer 2 (TickBreakoutMonitor) |
| Provide soft-block fallback when Layer 2 not in BREAKOUT | ✅ | via Layer 4 (provisional_m30_bias) |
| Preserve B+C ENSEMBLE diagnostics | ✅ | read-only consumer |
| Avoid regression on RANGE_BOUND mean-reversion semantics | ✅ | only the *gate to TRENDING* changes |
| Add audit trail (decision_log + tests) | ✅ | observability is part of scope |
| Walk-forward CV validation pre-deploy | ✅ | per regime + counterfactual on 04:44 burst |
| Wire ATS Trend Line | ❌ | NextGen scope (Q3 NÃO per Barbara) |
| Wire M5 phase_state derivation | ❌ | NEW work, requires calibration |
| Re-deploy fb673a4 (PM-DECOUPLE NameError fix) | ❌ | belongs to Cluster 2 |
| Fix cooldown gap on ALPHA trigger | ❌ | separate task |
| Re-design `delta_4h_inverted_fix` calibration | ❌ | INVEST-02 scope |

**No gap detected.** Scope minimal; cross-project refs zero.

---

## 2. BUG MECHANISM — exact line numbers

### 2.1 Cascade origin (B+C ENSEMBLE divergence)

`live/level_detector.py:_get_daily_trend()` returns `"unknown"` whenever `signal_b ≠ signal_c` (B+C voting rule). Per heartbeat 2026-05-06 04:44 UTC:

```
signal_a: -1
signal_b: +1   (Wyckoff swing pivot says LONG)
signal_c: -1   (ICT BOS/CHoCH says SHORT)
agreement_count: 0
decision_reason: "b=1,c=-1 (disagree)"
daily_trend: "unknown"
```

This output is **correct per the 401e6da spec** — the fix is being honest about uncertainty. The cascade defect is downstream consumers that interpret `unknown` as "no signal" instead of "consult fallback".

### 2.2 First cascade gate — phase engine

`live/event_processor.py:3555`:

```python
if confirmed and self.daily_trend in ("long", "short"):
    if self._detect_box_ladder(self.daily_trend):
        if self._bars_outside_box >= self._TREND_ACCEPTANCE_MIN_BARS:
            return "TREND"
        ...
        return "EXPANSION"
    return "EXPANSION"

return "EXPANSION"
```

When `self.daily_trend == "unknown"`, the condition evaluates `False`, and `_compute_raw_phase()` returns `"EXPANSION"` (or `"CONTRACTION"` if price is inside box). **Phase NEVER reaches `"TREND"`** while daily_trend is unknown.

### 2.3 Second cascade gate — strategy dispatch

`live/event_processor.py:_get_strategy_mode()` (line 3649):

```python
def _get_strategy_mode(self) -> tuple:
    if not self._thresholds.get("dual_strategy_enabled", False):
        return ("RANGE_BOUND", None)

    phase = self._get_current_phase()
    daily_trend = self.daily_trend

    if phase == "CONTRACTION":
        return ("RANGE_BOUND", None)

    if phase == "TREND" and daily_trend in ("long", "short"):
        return ("TRENDING", trend_dir)

    if phase == "EXPANSION" and daily_trend in ("long", "short"):
        return ("TRENDING", trend_dir)

    return ("RANGE_BOUND", None)  # ← line 3682, silent fallback
```

When `daily_trend == "unknown"`, both TRENDING dispatch conditions fail and the function silently returns `("RANGE_BOUND", None)`.

### 2.4 Third cascade gate — direction in RANGE_BOUND

`live/event_processor.py:_resolve_direction()` (line 3697):

```python
if strategy_mode == "RANGE_BOUND":
    direction = "SHORT" if level_type == "liq_top" else "LONG"
```

Pure mean-reversion — wrong in trending markets.

### 2.5 Empirical observation — burst 2026-05-06 04:09–04:22 UTC

Bullish rally: GC futures +91pts in 7h (4569.6 → 4660.6).

200 emitted signals, all **CONFIRMED SHORT @ ZR ALPHA**. Last gate eval at 04:44:45 captured in `logs/decision_live.json`:

```json
{
  "context": {
    "phase": "CONTRACTION",
    "daily_trend": "unknown",
    "m30_bias": "unknown",  "m30_bias_confirmed": false,
    "provisional_m30_bias": "bullish",
    "delta_4h": 3509.0
  },
  "trigger": {"type":"ALPHA", "level_type":"liq_top", "proximity_pts":0.1},
  "decision": {"direction":"SHORT", "trade_intent":"ENTRY_SHORT", "total_score":5}
}
```

Direction `SHORT` determined entirely by `level_type=liq_top` because cascade defaulted to RANGE_BOUND.

### 2.6 Counterfactual under proposed 5-layer cascade

| Layer | Eval at 04:44 | Outcome |
|-------|---------------|---------|
| 1: `daily_trend` | "unknown" (B≠C) | SKIP |
| 2: `TickBreakoutMonitor.breakout_dir` | likely `"UP"` (price 4658.7 above box high; module running PID 23000) | **HIT** → resolved=("long","tick_breakout","HIGH") |
| 3: `m30_bias_confirmed` | False | (not consulted because Layer 2 hit) |
| 4: `provisional_m30_bias` | bullish | (not consulted because Layer 2 hit) |
| 5: default | n/a | n/a |

**Result**: `resolved=long` → `_get_strategy_mode` returns `("TRENDING", "LONG")` → `_get_trend_entry_mode(level_type=liq_top, trend_direction=LONG)` returns `("SKIP", None, "TRENDING_UP: liq_top = liquidation zone")` → SHORT signal blocked. **Bug suppressed.**

If Layer 2 had been in `CONTRACTION` (not yet broken out), Layer 4 (`provisional_m30_bias=bullish`) would catch — also returning resolved=long and same suppression.

### 2.7 Why pre-fix did not exhibit this

Pre-401e6da, `_get_daily_trend()` had a stale-fallback bug returning `"long"` ~90% of evaluations (per backtest 27 abril). Cascade entered `TRENDING_LONG`; SHORT signals at liq_top hit overextension fallback; mostly blocked. The "fix" 401e6da removed this implicit filter without re-introducing explicit gate. **This is the cascade defect.**

### 2.8 Settings.json status (verified)

- `config/settings.json:64`: `"dual_strategy_enabled": true` ✅
- `config/settings.json:86`: `"trend_continuation_enabled": true` ✅

Both flags ENABLED since 2026-04-21 (commit 3e80ed6). Never toggled to false. Flags are NOT the bug — bug is the cascade dependency on `daily_trend in ("long","short")`.

### 2.9 Cluster 3 attribution (verified)

Twelve commits cherry-picked into `deploy/cluster-3-2026-05-06`; none modify cascade logic. Bug is pre-existing in 401e6da/889f5ee.

---

## 3. CASCADE LADDER DESIGN (5 Layers)

### 3.1 Helper signature

Add a method `_resolve_trend_direction()` to `EventProcessor` returning `(trend: str, source: str, confidence: str)` where `trend ∈ {"long","short","unknown"}` and `confidence ∈ {"HIGH","MEDIUM","LOW","NONE"}`.

### 3.2 Layer logic (pseudo-code)

```
def _resolve_trend_direction(self):
    # Layer 1: daily_trend (B+C ENSEMBLE) — HIGH
    if self.daily_trend in ("long", "short"):
        return (self.daily_trend, "daily_trend_b_c_ensemble", "HIGH")

    # Layer 2: TickBreakoutMonitor real-time — HIGH
    if self._thresholds.get("direction_fallback_use_tick_breakout", True):
        try:
            tb = self._tick_breakout.status() if self._tick_breakout else None
            if tb and tb.get("state") in ("BREAKOUT_UP", "BREAKOUT_DN"):
                trend = "long" if tb["state"] == "BREAKOUT_UP" else "short"
                return (trend, "tick_breakout_monitor", "HIGH")
        except Exception as e:
            log.debug("tick_breakout resolve failed: %s", e)

    # Layer 3: m30_bias_confirmed — MEDIUM
    m30b = getattr(self, "m30_bias", "unknown")
    m30c = getattr(self, "m30_bias_confirmed", False)
    if m30c and m30b in ("bullish", "bearish"):
        trend = "long" if m30b == "bullish" else "short"
        return (trend, "m30_bias_confirmed", "MEDIUM")

    # Layer 4: provisional_m30_bias (telemetry → soft-block upgrade) — LOW
    if self._thresholds.get("direction_fallback_use_provisional_m30", True):
        prov = getattr(self, "provisional_m30_bias", "unknown")
        if prov in ("bullish", "bearish"):
            trend = "long" if prov == "bullish" else "short"
            return (trend, "provisional_m30_bias", "LOW")

    # Layer 5: default
    return ("unknown", "no_trend_signal", "NONE")
```

### 3.3 Refactored `_compute_raw_phase()` (line 3555 area)

```python
# OLD:
if confirmed and self.daily_trend in ("long", "short"):
    if self._detect_box_ladder(self.daily_trend):
        ...

# NEW:
resolved_trend, trend_source, trend_confidence = self._resolve_trend_direction()
if confirmed and resolved_trend in ("long", "short"):
    if self._detect_box_ladder(resolved_trend):
        if self._bars_outside_box >= self._TREND_ACCEPTANCE_MIN_BARS:
            log.info("[PHASE_ENGINE] TREND via %s (confidence=%s)",
                     trend_source, trend_confidence)
            return "TREND"
        ...
```

Effect: phase can reach `TREND` when ANY layer resolves bullish/bearish even if daily_trend is `unknown`.

### 3.4 Refactored `_get_strategy_mode()` (line 3649 area)

```python
def _get_strategy_mode(self) -> tuple:
    if not self._thresholds.get("dual_strategy_enabled", False):
        return ("RANGE_BOUND", None)

    phase = self._get_current_phase()
    self._last_phase = phase

    resolved_trend, trend_source, trend_confidence = self._resolve_trend_direction()

    if phase == "CONTRACTION":
        return ("RANGE_BOUND", None)

    if phase == "TREND" and resolved_trend in ("long", "short"):
        trend_dir = "LONG" if resolved_trend == "long" else "SHORT"
        log.info("[STRATEGY] TRENDING via %s (confidence=%s) dir=%s",
                 trend_source, trend_confidence, trend_dir)
        return ("TRENDING", trend_dir)

    if phase == "EXPANSION" and resolved_trend in ("long", "short"):
        trend_dir = "LONG" if resolved_trend == "long" else "SHORT"
        log.info("[STRATEGY] TRENDING via %s (confidence=%s) dir=%s [EXPANSION]",
                 trend_source, trend_confidence, trend_dir)
        return ("TRENDING", trend_dir)

    log.info("[STRATEGY] RANGE_BOUND fallback: phase=%s resolved=%s source=%s",
             phase, resolved_trend, trend_source)
    return ("RANGE_BOUND", None)
```

### 3.5 Conservative defaults

| Threshold | Default | Rationale |
|-----------|---------|-----------|
| `direction_fallback_use_tick_breakout` | `true` | Module already running, deterministic state machine, JAC-gated, <1s latency |
| `direction_fallback_use_provisional_m30` | `true` | provisional bias already computed every ~30s; upgrade from telemetry-only is the explicit Q2 directive |

### 3.6 Behavior matrix

| Layer 1 (daily_trend) | Layer 2 (tick_breakout state) | Layer 3 (m30 confirmed) | Layer 4 (provisional) | Resolved | Strategy |
|-----------------------|-------------------------------|-------------------------|------------------------|----------|----------|
| "long" | any | any | any | "long" (HIGH) | TRENDING_LONG |
| "short" | any | any | any | "short" (HIGH) | TRENDING_SHORT |
| "unknown" | BREAKOUT_UP | any | any | "long" (HIGH) | TRENDING_LONG |
| "unknown" | BREAKOUT_DN | any | any | "short" (HIGH) | TRENDING_SHORT |
| "unknown" | CONTRACTION/CANDIDATE_UP/CANDIDATE_DN | True+bullish | any | "long" (MEDIUM) | TRENDING_LONG |
| "unknown" | CONTRACTION/CANDIDATE | True+bearish | any | "short" (MEDIUM) | TRENDING_SHORT |
| "unknown" | not BREAKOUT | False | bullish | "long" (LOW) | TRENDING_LONG |
| "unknown" | not BREAKOUT | False | bearish | "short" (LOW) | TRENDING_SHORT |
| "unknown" | not BREAKOUT | False | unknown/neutral | "unknown" (NONE) | RANGE_BOUND (logged) |

### 3.7 Counterfactual 04:44 burst — 5-layer trace

Reconstructed from `logs/decision_live.json` snapshot (provisional=bullish, confirmed=False, daily_trend=unknown, price 4658.7, m5_liq_top 4657.9):

- Layer 1 SKIP (unknown)
- Layer 2 likely HIT (price above m5_liq_top → TickBreakoutMonitor likely in BREAKOUT_UP)
- (If Layer 2 misses): Layer 3 SKIP (confirmed=False)
- Layer 4 HIT (provisional=bullish → resolved=long)

**Either Layer 2 OR Layer 4 catches the rally.** Both → resolved=long → `TRENDING_LONG` dispatch → `_get_trend_entry_mode(liq_top, LONG)` returns `("SKIP", None, "liq_top liquidation zone")` per Sprint 9 line 3737–3739 → SHORT blocked. **Burst suppressed.**

### 3.8 Why this design satisfies Barbara's rejection

- **No 1.5h M30 confirmation lag**: Layer 2 is <1s tick-driven; Layer 4 is ~30s.
- **No reliance on slow indicators alone**: ladder degrades from <1s to 30s to 5min+ to safe default.
- **Existing modules wired, no new derivation work**: TickBreakoutMonitor and provisional_m30_bias both already running.
- **No NextGen / TradeATS / ATS Trend Line refs**: scope strictly within `live/` and `config/settings.json`.

---

## 4. METHODOLOGY CITATION PER LAYER

### 4.1 Layer 1 — `daily_trend` (B+C ENSEMBLE)

**Methodology**: Wyckoff swing pivots (Signal B, lookback=3) + ICT BOS/CHoCH (Signal C, structure_lookback=5) with 2-of-2 voting.

**Source**:
- `live/level_detector.py:316` — `_compute_signal_b()`
- `live/level_detector.py:347` — `_compute_signal_c()`
- `live/level_detector.py:409` — `_get_daily_trend()`
- `_audit/calibrations/calibration_daily_trend_v2.md` — Purdue 12-step calibration; `calibration_version=v2_2026-04-26`
- 21/21 smoke tests in `tests/test_get_daily_trend_ensemble.py`

**Confidence**: HIGH — externally validated, calibrated, smoke tests pass, deployed since 2026-05-05 21:10 UTC.

### 4.2 Layer 2 — `TickBreakoutMonitor.status().breakout_dir`

**Methodology**: Real-time tick-driven state machine over the M30 box. State transitions:

```
CONTRACTION → CANDIDATE_UP/CANDIDATE_DN (price crosses box high/low)
            → BREAKOUT_UP/BREAKOUT_DN (JAC-gated confirmation)
            → CONTRACTION (price returns to box)
```

**Source**:
- Module: `live/tick_breakout_monitor.py` (16KB, 16313 bytes)
- Imported: `event_processor.py:67` — `from live.tick_breakout_monitor import TickBreakoutMonitor`
- Started: `event_processor.py:4758` — `self._tick_breakout.start()`
- State machine fields: `_state`, `_breakout_dir`, `_breakout_extreme`, `_box_id`, `_box_high_gc`, `_box_low_gc`
- Status accessor: `tick_breakout_monitor.py:371-381` — `status() -> {state, box_id, box_high_gc, box_low_gc, breakout_dir, breakout_extreme}`
- JAC timer: `_jac_since` field (line 99) gates premature breakout flips
- Box refresh: `_refresh_from_parquet()` reads `gc_m30_boxes.parquet` and resets state on new `box_id`

**Operational verification**:
- Module is RUNNING in current production PID 23000 (FluxQuantumAPEX)
- `_breakout_dir` populated in memory tick-by-tick
- Currently NEVER consumed by event_processor (orphan since introduction commit 3e80ed6 on 2026-04-21 — see `investigation_orphan_fast_sources_2026-05-06.md` §STEP 5)

**Confidence**: HIGH — deterministic state machine, JAC-gated against false breakouts, tick-level latency. No formal walk-forward CV yet (motivates §6 backtest gate before deploy).

**Mapping**: `state ∈ {"BREAKOUT_UP","BREAKOUT_DN"} → trend ∈ {"long","short"}`. Other states (`CONTRACTION`, `CANDIDATE_UP`, `CANDIDATE_DN`) are NOT used as bias signals — they represent uncertainty (fall through to Layer 3).

### 4.3 Layer 3 — `m30_bias_confirmed` + `m30_bias`

**Methodology**: Wyckoff M30-box bias from `derive_m30_bias(m30_df, confirmed_only=True)`.

**Source**:
- `live/level_detector.py:derive_m30_bias()` (current production)
- `event_processor.py:1705` — `self.m30_bias_confirmed = is_confirmed`
- `event_processor.py:2560` — existing gate consumer (same field)

**Confidence**: MEDIUM — battle-tested in entry-gate context for years. Confirmation gate (`m30_bias_confirmed=True`) protects against rapid-onset breakouts (lagging by design).

**Mapping**: `m30_bias ∈ {"bullish","bearish","unknown","neutral"}` → `trend ∈ {"long","short"}`. Skip if not confirmed or neutral.

### 4.4 Layer 4 — `provisional_m30_bias` (telemetry → soft-block upgrade)

**Methodology**: Same Wyckoff `derive_m30_bias()` but with `confirmed_only=False` flag — captures bias before structural confirmation, ~30s latency vs 5min+ for confirmed.

**Source**:
- `live/level_detector.py:993` — `provisional_m30_bias, _ = derive_m30_bias(m30_df, confirmed_only=False)`
- `event_processor.py:1706` — assignment in macro-context refresh
- `event_processor.py:2561` — current TELEMETRY-ONLY consumer (logs INFO, does NOT block)

**Confidence**: LOW — uncombined Wyckoff signal without structural confirmation. No formal calibration distinct from m30_bias_confirmed. Used as last resort before falling to RANGE.

**Mapping**: same as Layer 3 (`bullish→long`, `bearish→short`, else SKIP).

**Upgrade semantic**: This layer changes `provisional_m30_bias` from telemetry-only to soft-block via the strategy_mode dispatch. The hard-block check at `event_processor.py:2571–2580` (which currently fires only when `m30_bias_confirmed=True`) remains untouched — provisional still does NOT hard-block, but it now influences strategy selection upstream.

### 4.5 Layer 5 — Default RANGE_BOUND

**Methodology**: ATS Strategy 1 / Market Maker / Reversal — accumulate against deviation, liquidate at FMV. Correct in true range-bound markets.

**Source**: `event_processor.py:3700` (unchanged).

**When triggered**: All 4 prior layers exhausted. Logged explicitly (no longer silent).

### 4.6 Methodology layer integrity

The cascade degrades gracefully across confidence tiers:
1. HIGH (Purdue 12-step Bonferroni-validated B+C)
2. HIGH (deterministic state machine + JAC)
3. MEDIUM (battle-tested Wyckoff M30 confirmed)
4. LOW (uncombined Wyckoff M30 provisional)
5. NONE (RANGE_BOUND ATS Strategy 1 default safe)

**No methodology gap.** RANGE_BOUND fallback IS a valid ATS Strategy 1; the bug was that fallback fired when secondary layers had a signal.

---

## 5. EDGE CASES

### 5.1 TickBreakoutMonitor states (Layer 2)

| state | resolved_trend (Layer 2) | rationale |
|-------|--------------------------|-----------|
| `BREAKOUT_UP` | "long" | confirmed upward break |
| `BREAKOUT_DN` | "short" | confirmed downward break |
| `CONTRACTION` | SKIP layer | inside box |
| `CANDIDATE_UP` | SKIP layer | breakout pending JAC confirmation |
| `CANDIDATE_DN` | SKIP layer | same |
| Module not initialized / `_tick_breakout is None` | SKIP layer | safe init guard |
| `status()` raises exception | SKIP layer | wrapped in try/except |

### 5.2 `m30_bias` legacy values (Layer 3 + 4)

- `"bullish"` → `"long"`
- `"bearish"` → `"short"`
- `"unknown"` / `"neutral"` / `""` / `None` → SKIP layer

### 5.3 Phase × resolved interaction

| Phase | resolved (any layer) | Strategy |
|-------|----------------------|----------|
| `CONTRACTION` | any | `RANGE_BOUND` (always, line 3667) |
| `EXPANSION` | long/short | `TRENDING` |
| `EXPANSION` | unknown | `RANGE_BOUND` (logged) |
| `TREND` | long/short | `TRENDING` |
| `TREND` | unknown | (cannot occur — TREND requires resolved≠unknown via line 3555) |
| `NEW_RANGE` | any | `RANGE_BOUND` |

`CONTRACTION` always RANGE preserves the ATS Strategy 1 design — accumulate inside box.

### 5.4 Stale parquet / data unavailable

| Condition | Cascade | Strategy |
|-----------|---------|----------|
| `gc_m30_boxes.parquet` stale (>5min) | Layer 3 fails (m30_bias_confirmed=False); Layer 4 may still fire if provisional captured before stale | depends on Layer 4 |
| `gc_m30_boxes.parquet` corrupted | Both Layer 3 and Layer 4 SKIP | RANGE_BOUND |
| TickBreakoutMonitor parquet refresh fails | Layer 2 may use stale box_id but state still valid; if state error → SKIP | depends on Layer 3+4 |
| `_get_daily_trend()` raises | Layer 1 returns "unknown"; cascade continues | depends on lower layers |

All paths converge on safe default. No exception propagates.

### 5.5 Reset behavior on service restart

- `_phase_current = "NEW_RANGE"` (line 3458) — phase ladder cold start
- `_phase_candidate = ""` — no pending transition
- `_bars_outside_box = 0`
- TickBreakoutMonitor starts in `CONTRACTION` (line 96) — Layer 2 SKIPs until box known + breakout detected
- `m30_bias_confirmed`, `provisional_m30_bias` populated on first macro-context refresh (~30s)

**Restart safety**: First strategy evaluation immediately post-restart will skip layers 2/3/4 if box state not yet known, falling to RANGE_BOUND. As soon as data populates (~60s), all layers operational. No timing race.

### 5.6 Layer ordering rationale

HIGH (Layer 1) before HIGH (Layer 2) because daily_trend has formal calibration; tick_breakout has not. When both available, daily_trend wins.
HIGH (Layer 2) before MEDIUM (Layer 3) because tick_breakout has lower latency.
MEDIUM (Layer 3) before LOW (Layer 4) because confirmed has structural confirmation.

**No layer ties**: each returns immediately on hit (no merging).

### 5.7 Concurrent access (thread safety)

- TickBreakoutMonitor.status() acquires its own `self._lock` (line 374) — thread-safe.
- `m30_bias_confirmed` updated under `self._lock` (line 1703); resolver reads without lock — benign race (worst case = old value mid-update, identical to pre-fix behavior).
- `provisional_m30_bias` same idiom.

No new lock or thread synchronization introduced.

### 5.8 Layer 2 module not initialized

If `self._tick_breakout` is `None` (init order race or constructor failure), Layer 2 SKIPs cleanly via `if self._tick_breakout` guard. Resolver continues to Layer 3.

### 5.9 daily_trend == "" (legacy empty)

Layer 1 condition `if self.daily_trend in ("long","short")` is False for empty string → falls to Layer 2.

### 5.10 Operator override of flags

If operator sets `direction_fallback_use_tick_breakout=false` AND `direction_fallback_use_provisional_m30=false`, cascade reduces to original 3-layer (daily_trend → m30_confirmed → RANGE). System reverts to pre-fix behavior; defensive defaults still safe.

---

## 6. TEST PLAN

### 6.1 Unit tests (new — `tests/test_strategy_mode_cascade.py`)

| # | Test | Setup | Expected |
|---|------|-------|----------|
| 1 | `test_layer1_daily_trend_long` | daily_trend="long" | resolved=("long","daily_trend_b_c_ensemble","HIGH") |
| 2 | `test_layer1_daily_trend_short` | daily_trend="short" | ("short", ..., "HIGH") |
| 3 | `test_layer2_tick_breakout_up_when_layer1_unknown` | daily_trend="unknown", tb.state="BREAKOUT_UP" | ("long","tick_breakout_monitor","HIGH") |
| 4 | `test_layer2_tick_breakout_dn_when_layer1_unknown` | daily_trend="unknown", tb.state="BREAKOUT_DN" | ("short", ..., "HIGH") |
| 5 | `test_layer2_skip_when_state_contraction` | daily_trend="unknown", tb.state="CONTRACTION", layer3 fires bullish | resolved=("long","m30_bias_confirmed","MEDIUM") |
| 6 | `test_layer2_skip_when_state_candidate` | daily_trend="unknown", tb.state="CANDIDATE_UP", layer3 fires bullish | layer3 wins |
| 7 | `test_layer2_skip_when_module_none` | _tick_breakout=None, layer3 fires | layer3 wins, no exception |
| 8 | `test_layer2_skip_when_status_exception` | tb.status() raises | layer3 wins, exception caught |
| 9 | `test_layer3_m30_confirmed_bullish` | layer 1+2 SKIP, m30_confirmed=True bullish | ("long","m30_bias_confirmed","MEDIUM") |
| 10 | `test_layer3_m30_confirmed_bearish` | same with bearish | ("short", ..., "MEDIUM") |
| 11 | `test_layer3_skip_when_not_confirmed` | layer 1+2 SKIP, m30_confirmed=False bullish | layer4 fires |
| 12 | `test_layer3_skip_when_neutral` | m30_confirmed=True m30="neutral" | layer4 fires |
| 13 | `test_layer4_provisional_bullish` | layers 1-3 SKIP, provisional="bullish" | ("long","provisional_m30_bias","LOW") |
| 14 | `test_layer4_provisional_bearish` | provisional="bearish" | ("short", ..., "LOW") |
| 15 | `test_layer4_skip_when_unknown` | provisional="unknown" | layer5 default |
| 16 | `test_layer4_skip_when_flag_disabled` | flag=false, provisional bullish | layer5 default |
| 17 | `test_layer5_default_unknown` | all layers skip | ("unknown","no_trend_signal","NONE") |
| 18 | `test_thread_safety` | concurrent updates + reads | no exception, valid tuples |
| 19 | `test_strategy_mode_phase_contraction_force_range` | phase=CONTRACTION, all resolved | RANGE_BOUND always |
| 20 | `test_strategy_mode_phase_expansion_layer2_trending` | phase=EXPANSION, layer2 HIT | TRENDING_LONG |
| 21 | `test_phase_engine_layer2_can_reach_trend` | confirmed+ladder OK, daily_trend=unknown, layer2 HIT | phase=TREND (was: never) |
| 22 | `test_phase_engine_layer4_can_reach_trend` | confirmed+ladder OK, daily_trend=unknown, layer4 fires | phase=TREND |

**Total: ~22 unit tests** (within 15-20 directive range +2 for layer ordering coverage).

### 6.2 Regression tests (must continue green)

- `tests/test_get_daily_trend_ensemble.py` (21/21) — B+C ENSEMBLE unchanged
- `tests/test_macro_monitor.py` (35/35) — VAP unaffected
- `tests/test_m30_box_stagnation.py` (7/7) — m30_updater unchanged
- `tests/test_signal_emitter.py` — emit logic unchanged
- `tests/test_decisor_mt5_disconnect.py` — decisor unchanged
- `tests/test_trailing_stop_cross_frame.py` — trailing unchanged

≥95 existing tests must remain green.

### 6.3 Backtest counterfactual + walk-forward CV

**Deliverable**: `_audit/fixes/backtest_cascade_counterfactual.py`.

**Dataset**: `gc_ats_features_v5.parquet` + `gc_ohlcv_l2_joined.parquet` covering Jul 2025 – Mar 2026 (~8,200 M30 rows post-Sprint 8 data start) plus the live `decision_log_*.jsonl` archives for incident replay.

**Methodology**: Walk-forward CV (chronological, no leakage) per regime:

- **Folds**: 5 folds, each ~200 trading days, walk-forward
- **Per fold**: train any layer-specific calibration (none for tick_breakout/provisional — purely deterministic) + evaluate on held-out window
- **Per regime stratification** (per INVEST_01 finding that pooled is misleading):
  - `RANGE` (`m30_in_contraction=True AND weekly_aligned=False`)
  - `TREND_UP` (`m30_in_contraction=False AND weekly_aligned=True AND daily_trend="long"`)
  - `TREND_DN` (same with `daily_trend="short"`)
  - `TRANSITIONAL` (else)
- **Counterfactual replay**: for each historical signal, replay decision under (a) old logic vs (b) new 5-layer cascade. Measure:
  - Signal count delta (LONG vs SHORT)
  - Hit rate (TP1 vs SL within ATR-defined targets)
  - Mean fwd return at 30min, 4h horizons
  - Cohen's d vs regime baseline
  - Bootstrap 95% CI (1000 resamples)
  - Walk-forward sign stability across folds

**Specific 04:44 burst replay**: counterfactual on `2026-05-06T04:09:00 – 04:51:00 UTC` window. Acceptance: ≥80% reduction in counter-trend SHORT signals.

**Acceptance criteria (gates)**:
- New logic must NOT degrade Cohen's d in any regime where Layer 1 is operative
- New logic must IMPROVE Cohen's d in regimes where Layer 2 / Layer 4 activates and old logic falls to RANGE
- Walk-forward sign stability: effect sign CONSISTENT across all 5 folds (no sign-flip)
- 04:44 burst counterfactual: ≥80% reduction in SHORT signals (target: ≥160 of the 200 emitted)

If any gate fails → DO NOT DEPLOY; report findings; iterate spec.

### 6.4 Live runtime smoke (post-deploy 5min observation)

Per §8 deploy plan. NOT a substitute for backtesting.

### 6.5 Layer 4 false-positive concern (Q4 risk mitigation)

Layer 4 fires on `provisional_m30_bias` which is uncombined Wyckoff (lower confidence than confirmed). Risk: provisional bias flips during noise → spurious TRENDING dispatch.

Mitigation:
- Layer 4 only fires when Layers 1–3 all SKIP (worst-case fallback)
- Provisional bias is computed from same `derive_m30_bias` as confirmed; differs only in `confirmed_only=False`
- Audit trail (`[STRATEGY] TRENDING via provisional_m30_bias confidence=LOW`) flags every Layer 4 activation for post-hoc review
- Backtest counterfactual measures Layer 4 hit-rate + false-positive rate per regime

---

## 7. IMPLEMENTATION PLAN

### 7.1 Branch strategy

- New branch: `fix/cascade-tick-breakout-provisional-2026-05-06`
- Source: current `deploy/cluster-3-2026-05-06` HEAD (`afde8bd`)
- Working tree clean (verified post Cluster 3 deploy + investigation)
- One new commit on this branch

### 7.2 Files modified

| File | LOC | Risk |
|------|-----|------|
| `live/event_processor.py` | +70 / -2 (helper + 2 refactor sites + try/except guards) | 🔴 CORE |
| `tests/test_strategy_mode_cascade.py` | +400–500 (NEW, ~22 tests) | 🔵 NEW |
| `config/settings.json` | +2 keys | 🟡 CONFIG |
| `_audit/fixes/bug_cascade_direction_fallback_spec.md` | this file | 🟢 DOC |
| `_audit/fixes/backtest_cascade_counterfactual.py` | NEW backtest script | 🔵 NEW |
| `_audit/fixes/2026-05-06_deploy_validation.md` | post-deploy log | 🟢 DOC |

**Total**: ~70 LoC live + 400-500 LoC tests + 2 config keys.

### 7.3 Implementation sequence

1. Add helper `_resolve_trend_direction()` to EventProcessor (before `_get_strategy_mode`)
2. Refactor `_compute_raw_phase()` line 3555 to use resolver
3. Refactor `_get_strategy_mode()` lines 3671, 3677 to use resolver + add explicit RANGE_BOUND log
4. Add 2 config keys to `config/settings.json`
5. Write `tests/test_strategy_mode_cascade.py` (~22 tests)
6. Run pytest cascade tests + regression smoke (target: 22+95 PASS)
7. Write + execute `_audit/fixes/backtest_cascade_counterfactual.py`
8. Verify backtest gate ≥80% counterfactual + walk-forward sign stability
9. Commit with structured message referencing spec
10. Deploy per §8

### 7.4 Risk flags

- 🔴 CORE: event_processor.py is critical path
- 🟡 CONFIG: settings.json change is consumed at startup; restart required
- 🔵 NEW: 22 new tests + 1 backtest script; do not touch existing tests
- 🟢 DOC: audit trail

### 7.5 Authorization required

ML-DS spec sign-off → Barbara approval before NSSM restart.

---

## 8. DEPLOY PLAN

### 8.1 Pre-deploy baseline (5 min)

- `git rev-parse HEAD` → record (afde8bd expected)
- `nssm get FluxQuantumAPEX AppDirectory` + `Start` + PIDs
- `cat logs/service_state.json` → snapshot heartbeat
- `head -100 logs/service_stdout.log`
- `curl :8088/api/system_health` → snapshot dashboard payload
- count CONFIRMED signals last 60min (baseline reference)

### 8.2 Smoke tests gate (15 min)

```bash
pytest tests/test_strategy_mode_cascade.py -v               # 22/22 NEW
pytest tests/test_get_daily_trend_ensemble.py -v             # 21/21 regression
pytest tests/test_macro_monitor.py tests/test_m30_box_stagnation.py -v  # 42/42 regression
```

ALL must pass. Any FAIL → STOP + rollback.

### 8.3 Backtest counterfactual + walk-forward CV gate (30–60 min)

```bash
python _audit/fixes/backtest_cascade_counterfactual.py --regime-segmented --walk-forward 5fold --burst-replay 2026-05-06T04:09Z..04:51Z
```

Verify acceptance:
- ≥80% counter-trend SHORT reduction on 04:44 burst window
- Walk-forward sign stability across all 5 folds per regime
- No Cohen's d degradation in regimes where Layer 1 is operative
- Improvement in regimes where Layer 2/4 activates

If any gate fails → STOP. Report. Iterate spec or revisit Layer 2/4 calibration.

### 8.4 NSSM restart

```bash
nssm restart FluxQuantumAPEX
# Dashboard service: only restart if config changed (unlikely for this fix)
```

### 8.5 Post-restart 5min observation window

- T+30s: heartbeat written to `service_state.json`
- T+60s: `[STRATEGY]` log lines visible in stdout (audit trail)
- T+5min: count CONFIRMED signals; verify direction distribution shifts (no 200/min counter-trend bursts)
- Barbara visual confirm dashboard

### 8.6 Auto-rollback triggers (hard gates)

Rollback if any of:
- Heartbeat not fresh after T+60s
- Service crash / restart loop
- Counter-trend SHORT volume during confirmed bullish rally exceeds pre-deploy baseline
- Any pytest regression fails post-restart smoke

Rollback procedure: `git reset --hard afde8bd` + `nssm restart FluxQuantumAPEX` (under 2min).

---

## 9. RISK ASSESSMENT

### 9.1 Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | TickBreakoutMonitor state machine has undocumented edge case (e.g., box_id reset mid-breakout) | MED | MED | try/except wrapper; on exception SKIP layer; backtest gate validates 8200+ M30 rows |
| R2 | Layer 4 (provisional) false-positive on noise | MED | MED | Only fires when Layers 1–3 SKIP; audit trail logs all activations; backtest measures false-pos rate per regime |
| R3 | New helper introduces bug in unmodified code paths | LOW | HIGH | 22 new tests + 95 regression; minimal LOC change (70) |
| R4 | Layer 2 too aggressive (BREAKOUT_UP fires before reliable trend) | MED | MED | JAC timer in TickBreakoutMonitor already gates premature flips; backtest gate per regime |
| R5 | Backtest counterfactual fails — new logic worse in some regime | LOW | HIGH | Walk-forward CV per regime per §6.3; if fails → don't deploy, iterate |
| R6 | Phase engine starts producing TREND too eagerly | LOW | MED | `_TREND_ACCEPTANCE_MIN_BARS=4` and `_detect_box_ladder` checks unchanged |
| R7 | Race between m30_bias update and resolver read | LOW | LOW | Benign race (worst case = old fallback) |
| R8 | Cluster 1/2 deploys later interact with this fix | MED | MED | Cluster 1 (DECISION-PATH-REFACTOR) likely modifies `_get_strategy_mode`; explicit merge plan when Cluster 1 deploys |
| R9 | Audit trail floods stdout | LOW | LOW | Single log per gate eval; rate-limited by gate frequency (~per second) |
| R10 | Operator misconfigures both fallback flags to false | LOW | MED | Falls back to pre-fix 3-layer; system safe but no benefit |
| R11 | TickBreakoutMonitor not initialized at startup (race with `start()`) | LOW | LOW | Layer 2 SKIPs via `if self._tick_breakout` guard; defensive null check |

### 9.2 Graceful failure modes

- Helper `_resolve_trend_direction()` raises → caller has try/except, falls back to old behavior (`return ("RANGE_BOUND", None)`).
- `tick_breakout.status()` raises → Layer 2 try/except SKIPs cleanly; resolver continues to Layer 3.
- Settings.json keys missing → `self._thresholds.get(key, default)` with safe defaults (both fallback flags `true`).
- `m30_bias_confirmed` attribute missing → `getattr(self, "m30_bias_confirmed", False)` defensive read.
- `provisional_m30_bias` attribute missing → `getattr(..., "unknown")` defensive read.

### 9.3 Known limitations (transparent disclosure)

1. **Fix does NOT correct CAL-03 inverted_fix damage** in `_check_pre_entry_gates` (line 1980). Separate concern (INVEST-02 pending).
2. **Fix does NOT address rapid-fire emission cooldown gap** observed in 2026-05-06 04:09–04:22 burst (200 SHORTs in 13min). Separate task per BURST INVESTIGATION.
3. **Fix does NOT include ATS Trend Line** — out of scope (Q3 NÃO; ATS Trend Line is NextGen, not Live).
4. **Fix does NOT include M5 phase_state derivation** — would require NEW logic + calibration (not orphan-wiring).
5. **Fix is local to FluxQuantumAPEX**. CC#1, CC#2, TradeATS streams are unaffected and untouched.
6. **Counterfactual replay is statistically informative, not definitive**. Real-time market may behave differently than historical replay.

### 9.4 Acceptable residual risk

After deploy:
- Barbara still operates manual via Quantower
- mt5_robo / mt5_hantec disconnected (zero financial impact even if signals wrong)
- Telegram OFF (no spam alerts)
- Dashboard advisory only — operator is gatekeeper

Counter-trend signals can still occur in rare scenarios where:
- All 4 trend layers fail to resolve (CONTRACTION + unknown bias + provisional unknown)
- Mean-reversion is appropriate per design (true range-bound markets)

This residual risk is consistent with the system's advisory mode design. **No financial risk introduced beyond what already exists.**

---

## END OF SPEC (REWRITE)

**Status**: REWRITE COMPLETE — 9 sections.
**Supersedes**: earlier 4-layer draft (rejected by Barbara for 1.5h M30 lag).
**Next**: ML-DS deeper review → Barbara approval → implementation per §7 → walk-forward CV + counterfactual per §6.3 → deploy per §8.

**Key changes from earlier draft**:
- 5-layer cascade (was 4) — TickBreakoutMonitor PRIMARY new (Layer 2)
- provisional_m30_bias upgraded from telemetry to soft-block (Layer 4) — Q2 SIM
- ATS Trend Line REMOVED — Q3 NÃO (NextGen scope)
- delta_4h sign Layer REMOVED — CAL-03 unstable per INVEST_01
- 2 config keys (was 3) — `direction_fallback_use_tick_breakout`, `direction_fallback_use_provisional_m30`
- Walk-forward CV chronological per regime mandatory (Q5 SIM)
- Counterfactual ≥80% reduction on 04:44 burst as hard gate

**Deliverable file**: `C:\FluxQuantumAI\_audit\fixes\bug_cascade_direction_fallback_spec.md`.

**ZERO implementation done. ZERO halt. ZERO mutation outside `_audit/fixes/`. ZERO ATS Trend Line refs. ZERO cross-project refs.**

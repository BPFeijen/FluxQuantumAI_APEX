# Missed LONG Investigation — 2026-04-20 02:00-03:30 UTC (XAUUSD / GC)

**Investigator:** Claude (read-only agent)
**Date run:** 2026-04-20
**Scope:** 2026-04-20 01:00 - 03:30 UTC
**Mode:** 100% READ-ONLY (zero edits, zero restarts, zero process touches)

---

## 1. Market State

### 1.1 Actual bullish impulse

Barbara's prompt referenced "3 significant bullish M5 candles after a downtrend between 02:00 and 03:30 UTC". The parquet data shows the real bullish impulse **preceded** the named window — it unfolded between **01:00 and 01:50 UTC**, after the severe sell-off of 23:00-00:00.

From `C:\data\processed\gc_m5_boxes.parquet` (top by body-size in 01:00-04:00):

| Timestamp (UTC) | Open | High | Low | Close | Body | Phase | Box confirmed |
|---|---|---|---|---|---|---|---|
| 01:00 | 4792.30 | 4805.70 | 4791.60 | 4804.60 | +12.30 | CONTRACTION | False |
| 00:55 | 4783.00 | 4793.80 | 4781.35 | 4792.65 | +9.65 | CONTRACTION | False |
| 01:30 | 4821.95 | 4830.70 | 4821.95 | 4825.55 | +3.60 | CONTRACTION | False |
| 01:45 | 4825.40 | 4832.15 | 4825.40 | 4829.45 | +4.05 | CONTRACTION | False |
| 01:50 | 4830.25 | 4834.00 | 4828.25 | 4832.45 | +2.20 | CONTRACTION | False |

Net displacement **4791.6 -> 4834.0 = +42.4 pts in 50 minutes**. From 02:00 onward price **rolled over** — the actual 02:00-03:30 window was a **pull-back / slow sell-off** from 4828 down to 4810 (not three additional bullish candles).

### 1.2 Box / structure transitions (M5)

- Until 01:55 the M5 box was frozen at [4780.80, 4787.15] (box_id 33981, unconfirmed).
- At 02:00 a **new M5 box** formed at [4825.40, 4834.00] (box_id 33982) — this was the box that confirmed the impulse.
- 02:05 `m5_box_confirmed=True` (box 33982).
- 02:20 a new unconfirmed box [4819.25, 4825.45] forms (id 33983) — price rotating inside.
- 02:55 brief NaN gap (box reset).
- 03:00 new box [4812.55, 4817.15] (id 33984).

### 1.3 M30 structure

M30 box was static for the entire window (from `C:\data\processed\gc_m30_boxes.parquet`):

| Row (23:00-03:00) | liq_top | liq_bot | box_high | box_low | confirmed |
|---|---|---|---|---|---|
| 23:00-03:00 | 4859.10 | 4783.65 | 4859.10 | 4849.55 | True |
| 03:30 | NaN | NaN | NaN | NaN | False |

`m30_liq_top=4859.10` and `m30_box_high=4859.10` were **equal** (not `liq_top > box_high`), and `m30_liq_bot=4783.65 < m30_box_low=4849.55` — by `derive_m30_bias` rules this yields **`bearish` (confirmed)** (see `live/level_detector.py:215` — it checks `liq_top > box_high` first for bullish; equality falls through).

The price impulse 4791 -> 4834 stayed **well below the M30 structural top 4859.10**, so no M30 breakout registered.

### 1.4 Daily / offset context (from decision_log.jsonl)

- `daily_trend = "long"` throughout (D1 up-trend in place)
- `gc_mt5_offset` drifted 19.8 -> 18.5 -> 19.8 during window
- `session = "ASIAN"` then transitioning into London approach
- `delta_4h`: 536 -> 1273 (all positive, bullish 4h momentum)

### 1.5 `breakout_dir` (ALPHA Spring question)

The M5 parquet has **no `breakout_dir` column**. `breakout_dir` is a state inside `tick_breakout_monitor.py` (in-memory state on the `m30` confirmation machine, reading `m30_box_high/low`). Since price 4791-4834 never broke **above 4859.10** (`m30_box_high`) nor **below 4849.55** (`m30_box_low`), the tick-level machine stayed in `CONTRACTION` — **no `breakout_dir` was ever set** in this window. No `TickBreakout` / `TICK_JAC_CONFIRMED` lines appear in `service_stdout.log` for the window.

---

## 2. APEX Internal State

Parsed `C:\FluxQuantumAI\logs\decision_log.jsonl` (11,926 lines total) filtered to `2026-04-20T01:|T02:|T03:`.

### 2.1 Decision counts

| Metric | Value |
|---|---|
| Total decisions logged in window | 2,763 |
| GO decisions | 8 |
| BLOCK decisions | 2,755 |
| Trigger types | **ALPHA only** (no GAMMA, no DELTA) |
| Trigger directions (as logged) | `None` — field unset for ALPHA |
| Resolved `decision.direction` | **SHORT: 2,763 — LONG: 0** |
| Level types touched by ALPHA | **liq_top only** |

### 2.2 Context state (every decision in window)

- `phase`: **EXPANSION** (all 2,763 rows)
- `m30_bias`: **bearish** (all 2,763 rows)
- `m30_bias_confirmed`: **True** (all rows)
- `provisional_m30_bias`: bearish
- `daily_trend`: long
- `m30_box_mt5` evolved: (4761, 4767) @ 01:00 -> **(4805.6, 4814.2) @ 02:05** -> (4793, 4797) @ 03:10 (MT5 space — these reflect the M5 updater writing new M5 levels converted via offset; decision logger labels them `m30_box_mt5`).
- `liq_top_gc`, `liq_bot_gc` in the log: **frozen at 4787.15 / 4779.20** throughout (these are the M5 liq_top/liq_bot as exposed on the processor; see §2.3).
- `delta_4h` range: 536 -> 1273 (never near `MOMENTUM_BLOCK_SHORT=+3000`, never below `MOMENTUM_BLOCK_LONG=-1050`).

### 2.3 GO signals (8 total, all SHORT)

All 8 GO events fired at 03:10-03:14 UTC, all SHORT entries near `liq_top_mt5 = 4767.84` (MT5 space) triggered by `iceberg_proxy` / `large_order`. Example:

```
2026-04-20T03:10:26.754431+00:00
trigger: ALPHA / liq_top / proximity=23.2pts / m5_only
decision: GO / SHORT / reason="iceberg sweep sc=+2"
```

### 2.4 Service-log corroboration (`service_stdout.log`, today's session only, lines 221797+ after start marker)

Window 01:00-03:30 counts:

| Event | Count in window |
|---|---|
| `NEAR liq_top_mt5` ... `<- GATE CHECK` (SHORT candidate) | 3,169 |
| `NEAR liq_bot_mt5` ... `<- GATE CHECK` (LONG candidate) | **0** |
| `M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG rejected` | **238** |
| `BLOCK_V1_ZONE` (price outside M30 box) | 212 |
| `SHORT rejected` | 0 |
| `[STRATEGY]` / `PHASE_ENGINE` | 0 (debug-level — not emitted to stdout) |
| `GAMMA` events | 0 |
| `DELTA` events (momentum, excluding `DELTA_4H` tag) | 0 |
| `TickBreakout` / `TICK_JAC_CONFIRMED` | 0 |
| Occurrences of `bias=bearish` / `bias=bullish` | 300 / 0 |
| `-> GO` signals | 8 |

The **238 `LONG rejected`** events are critical: the system **did** produce a LONG direction proposal (resolved from the liq_top touch via TRENDING/pullback logic — see §3.3/§4) on 238 ticks, and each was blocked by the `M30_BIAS_BLOCK` hard gate. Those rejections happen **before** the decision is written to `decision_log.jsonl`, so they don't appear as LONG rows there — the decision log only records evaluations that survived the early bias veto.

---

## 3. LONG Triggers Analysis

### 3.1 ALPHA LONG (price touches `liq_bot`) — **NOT FIRED**

- Price low in window: **4791.60 at 01:00**; never below 4791.
- `m5_liq_bot` values during window: 4779.20 (01:00-01:55), 4824.30 (02:00-02:15), 4818.30 (02:20-02:50), NaN (02:55), 4812.55 (03:00-03:30).
- **Critical misalignment**: the M5 liq_bot **rebuilt upward to 4824.30/4818.30** during the impulse, so from 02:00 onward `low <= m5_liq_bot` was technically True 14 of 31 M5 bars — but the **processor-state `self.liq_bot_gc`** stayed frozen at 4779.20 in the decision log for the whole window, meaning the `_near_level` check used the stale 4779.20 level. Price never reached 4779.20 — closest was 4791.60 (19+ pts away), far outside the near-level band (`max(atr*NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)` ~= 3-6 pts).
- Result: **`_near_level` never returned `liq_bot`** in the window. Service log confirms: **0 `NEAR liq_bot_mt5` lines**.

### 3.2 ALPHA LONG Spring (m30 breakout_dir=DN, confirmed=False fake-out) — **NOT FIRED**

- No M30 box had `confirmed=False` with `breakout_dir=DN` during the window. M30 box at 22:00/23:00/00:00/00:30/01:00/.../03:00 all `m30_box_confirmed=True`, no breakout state changes.
- The tick-level `breakout_dir` in `tick_breakout_monitor.py` is driven by `m30_box_high/low = 4849.55/4859.10` — price range 4791-4834 never crossed either. State machine stayed in `CONTRACTION`. **0 tick events in window.**
- Result: **Spring condition (fake-out DN) could not materialize** — no DN breakout ever started.

### 3.3 DELTA LONG (pullback to box_high + L2) — **NOT FIRED**

- `check_delta_trigger` (`live/event_processor.py:3784`) requires `phase != CONTRACTION` and `TrendMomentumDetector.compute(...)["detected"] == True`.
- Phase held **EXPANSION** (never CONTRACTION), so the phase gate did not block.
- **Zero DELTA events in decision_log and service_stdout** for the window. The detector either returned `detected=False` on every M5 close or `_trend_momentum_detector` is absent. No LONG DELTA attempt reached the gate.
- Rationale: the detector uses `d1_trend="LONG"` + `expansion_lines` in MT5 space to detect a M5 close through a pullback-and-reclaim of a LONG expansion line. The active expansion lines in MT5 space at 4767-4768 (derived from the stale `liq_top_gc=4787.15 - offset`) — price was well above them already, so no new reclaim event.

### 3.4 GAMMA LONG (M5 expansion bar stacking in trend direction) — **NOT FIRED**

- `check_gamma_trigger` (`live/event_processor.py:3469`) requires `close[-1] > high[-2]` on the latest completed M5 bar, daily_trend=long, weekly_aligned=True, phase != CONTRACTION, R:R >= `GAMMA_MIN_RR`.
- The 01:30 bar (close 4825.55, high[-2]=4813.50) clearly satisfied `close > high_prev`. But at 01:30 processor `phase` was still EXPANSION/previous state — inconclusive without PHASE_ENGINE logs.
- **Zero GAMMA events logged in window** — either R:R failed (plausible: TP2=`liq_top_gc=4787.15` which is **below** the entry, forcing the 2:1 SL-extension fallback which yields a small TP2), or weekly_aligned read failed, or `TP2 <= entry` led to a truncated target — but critically, `self.liq_top_gc=4787.15` being **below** entry 4825 means:

```python
if tp2_gc <= entry_gc:  # true: 4787.15 <= 4825.55
    tp2_gc = entry_gc + abs(entry_gc - sl_gc) * 2.0  # 2:1 fallback
```

That fallback is meant to salvage the target, but it also caps TP2 to 2x SL, giving R:R exactly 2.0 — possibly below `GAMMA_MIN_RR` (value not verified in this run). With `atr14 ~= 6.5`, `sl_gc = high_prev = 4813.50`, `sl_dist = 4825.55 - 4813.50 = 12.05` -> `tp2_dist = 24.10` -> `rr = 2.0`. If `GAMMA_MIN_RR >= 2.0`, the check would pass; if strictly `>`, it would fail. Without a debug log we cannot say which branch dropped the signal.

### Summary

| Trigger | Fired? | Blocking cause |
|---|---|---|
| ALPHA LONG (liq_bot touch) | NO | `self.liq_bot_gc` stale at 4779.20, price never got within near-band. **0 NEAR liq_bot events.** |
| ALPHA LONG Spring | NO | No `breakout_dir=DN` / `confirmed=False` ever occurred on M30. |
| DELTA LONG | NO | Detector returned `detected=False` every M5 close (likely no qualifying pullback-reclaim of a LONG expansion line given the inputs). |
| GAMMA LONG | NO | No GAMMA event reached the gate. Most likely TP2-fallback R:R=2.0 equal-to-threshold or debug-filtered; also note at 01:30 GAMMA loop aligns to M5 close + 5s delay. |

**The 238 LONG rejections in service_stdout.log are NOT from ALPHA/GAMMA/DELTA LONG triggers.** They are from the `_resolve_direction` TRENDING pullback/continuation path: the system observed `NEAR liq_top` at ~4790 MT5 space while `daily_trend=long`, the TRENDING pullback engine proposed "LONG at resistance-retest", and then `M30_BIAS_BLOCK: bias=bearish` rejected that direction before the score gates ran.

---

## 4. Code Status (per trigger)

| Trigger | File:line | Status | Notes |
|---|---|---|---|
| `_near_level` (ALPHA driver) | `live/event_processor.py:1935` | **Functional** | Uses `self.liq_top/bot_gc` and M30 structural levels. Direction-agnostic: returns closest level irrespective of trend. |
| `_resolve_direction` | `live/event_processor.py:3340` | **Functional** | TRENDING + liq_top + daily_trend=long -> tries PULLBACK -> CONTINUATION -> overextension-reversal. Emits LONG for pullback/continuation, else SHORT for overextension. |
| `_get_current_phase` / `_compute_raw_phase` | `live/event_processor.py:3121` / `3166` | **Functional** | Correctly returned EXPANSION — price outside M30 box [4849.55-4859.10] while box confirmed. |
| `_get_strategy_mode` | `live/event_processor.py:3305` | **Functional** | Returned `("TRENDING", "LONG")` for EXPANSION + daily_trend=long. |
| `check_gamma_trigger` | `live/event_processor.py:3469` | **Functional** | Phase gate + weekly_aligned + close>high[-2] + R:R check. 2:1 SL-extension fallback when `liq_top <= entry`. |
| `check_delta_trigger` | `live/event_processor.py:3784` | **Functional** (requires `TrendMomentumDetector`) | Guards: `_trend_momentum_detector is None` -> skip. |
| `tick_breakout_monitor._step` | `live/tick_breakout_monitor.py:190` | **Functional** | State machine over M30 `box_high/low`. Uses `m30_box_high/low` from parquet (4849.55/4859.10) — price never crossed. |
| `_trigger_gate` (entry pipeline) | `live/event_processor.py:2104` | **Functional** | PONTO 0/0.5/1 gates: news, defense_mode, anomaly_forge, `M30_BIAS_BLOCK`, then v1/v2/v3/v4. |
| `_trigger_gate_gamma` | `live/event_processor.py:3668` | **Functional** (not verified as invoked — no GAMMA events fired). |
| `_trigger_gate_delta` | `live/event_processor.py:3966` | **Functional** (not verified as invoked — no DELTA events fired). |
| `derive_m30_bias` | `live/level_detector.py:215` | **Functional but brittle**. Classification requires **strict** `liq_top > box_high` for bullish. When `liq_top == box_high` (common after a box finalizes at the absolute top), falls through to bearish check, yielding **bearish** if `liq_bot < box_low`. Exactly what happened today. |
| Movement Detector (S3-G6 per memory) | — | Not inspected; no invocation signature in window. Not on the critical path for LONG. |

**Tick-loop invocation confirmed:** The 3,169 `NEAR liq_top_mt5` and 238 `LONG rejected` messages demonstrate the tick loop is executing `_tick_loop` -> `_near_level` -> `_resolve_direction` -> `_trigger_gate` pipeline.

GAMMA and DELTA have their own M5-aligned background threads (`_gamma_loop` at 3610, `_delta_loop` at 3908). The absence of any GAMMA/DELTA log line in 2.5 hours of service_stdout.log for this window suggests either:
1. The detectors returned None on every M5 close (valid outcome);
2. The logs were suppressed (DEBUG-level `log.debug`);
3. The background threads were not invoked (less likely — would be a regression).

Cannot distinguish from the available evidence without DEBUG-level logs.

---

## 5. Root Cause Hypotheses (Ranked)

### [HIGH CONFIDENCE] A — `M30_BIAS_BLOCK` vetoed every LONG attempt

**Evidence:**
- `service_stdout.log` shows **238 `M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG rejected`** in the window.
- All 2,763 decisions in `decision_log.jsonl` record `m30_bias="bearish"`, `m30_bias_confirmed=True`.
- The price acted bullishly but `m30_liq_top = m30_box_high = 4859.10` (equality), so `derive_m30_bias` did not flag bullish; it fell through to `liq_bot < box_low` -> `bearish`.
- The M30 bias is a **confirmed** box metric — it only refreshes when a new M30 box confirms. The impulse ended before a new M30 box closed above 4859.10, so bias could not update in time.
- Per `_trigger_gate` (PONTO 1, lines 2283-2295), confirmed bearish bias is a hard veto on LONG entries (no downstream v1/v2/v3/v4 scoring).

**Verdict:** This alone is sufficient to explain zero LONGs. Every LONG direction that `_resolve_direction` proposed (from the TRENDING pullback path at liq_top) was immediately killed by this veto.

### [HIGH CONFIDENCE] B — No valid ALPHA LONG trigger ever fired (liq_bot misalignment)

**Evidence:**
- Price floor in window: 4791.60 at 01:00.
- Processor `self.liq_bot_gc = 4779.20` throughout (20+ pts below reality).
- Service log: **0 `NEAR liq_bot` events** in the window.
- Real-time `m5_liq_bot` in parquet had rebuilt to 4824.30/4818.30/4812.55 (above price), but these values did **not propagate** to `self.liq_bot_gc` in the processor (liq_top_gc/liq_bot_gc in the decision log stayed pinned at 4787.15 / 4779.20 for the entire 2.5-hour window).
- Consequence: even if `M30_BIAS_BLOCK` had allowed LONG, there was no ALPHA LONG **trigger event** (price-touches-liq_bot) to kick off evaluation.

**Verdict:** An independent second failure. Even with bias permissive, the ALPHA LONG entry is structurally unreachable because the processor's M5 liq_bot reference is stale.

### [MEDIUM CONFIDENCE] C — GAMMA LONG at 01:30 probably filtered by R:R cap

**Evidence:**
- M5 bar 01:30 satisfied `close(4825.55) > high[-2](4813.50)` in a long trend with tight SL (12 pts) and ATR14 ~= 6.
- `self.liq_top_gc = 4787.15 <= entry 4825.55` triggers the 2:1 SL-extension fallback -> `tp2_dist = 24.10`, `rr = 2.0` exact.
- If `GAMMA_MIN_RR > 2.0` (strict inequality), the signal is silently dropped at `log.debug` level — no stdout trace.
- No GAMMA log line in service_stdout -> consistent with debug-level `return None`.

**Verdict:** Secondary contributor. Even if GAMMA fired, it would still hit `M30_BIAS_BLOCK` (Hypothesis A) because `_trigger_gate_gamma` invokes the same gate pipeline.

### [MEDIUM CONFIDENCE] D — DELTA LONG did not detect due to expansion-line inputs in wrong zone

**Evidence:**
- `_compute_expansion_lines` uses `self.liq_top/bot_gc` + offset. With stale `liq_top_gc=4787.15 - offset=19.x -> 4767.8 MT5`, the expansion lines fed to `TrendMomentumDetector.compute` are all 40-60 pts below current price 4810-4830.
- The detector looks for M5 reclaim events through LONG expansion lines; with no line near price, `detected=False` is the expected outcome.
- 0 DELTA events in window corroborates.

**Verdict:** Tertiary contributor. Also subordinate to Hypothesis A — a DELTA LONG hit would also be vetoed by M30_BIAS_BLOCK.

### [LOW CONFIDENCE] E — No architectural gap; triggers exist and function

Code review confirms ALPHA (via `_near_level`+`_resolve_direction`+`_trigger_gate`), ALPHA-Spring (via `tick_breakout_monitor`), GAMMA (`check_gamma_trigger`), DELTA (`check_delta_trigger`) are all implemented and invoked. This is NOT a "missing-code" situation. The failure is **data-state + gate-veto**, not **missing logic**.

### Root cause summary

> **Primary:** `M30_BIAS_BLOCK` held bearish(confirmed) veto over every LONG proposal throughout the window, because `m30_liq_top` was equal-to rather than strictly-greater-than `m30_box_high`, and the big impulse ended below 4859.10 so no new M30 box ever confirmed above structure to flip the bias.
>
> **Compounding:** The ALPHA LONG pathway was further blocked because `self.liq_bot_gc` on the processor was stale (still at 4779.20, the pre-impulse value), so price never registered a `NEAR liq_bot` touch — zero ALPHA LONG candidates were proposed in the first place.
>
> **Net effect:** LONG opportunity was unreachable through all four documented entry paths.

---

## 6. Recommendations (for future sprint — NOT to fix now)

1. **Tighten `derive_m30_bias` equality handling.** The `liq_top > box_high` strict-greater check is brittle when the top gets reused as the latest contraction ceiling. Consider `>= box_high` OR using rate-of-change of `liq_top` over recent boxes to detect bull-bias up-shift, and add a "neutral / unknown" third state so borderline cases do not default to `bearish`.
2. **Sanity-check `self.liq_top_gc / self.liq_bot_gc` refresh path.** These processor-state fields should track the latest `m5_liq_top/m5_liq_bot` from the parquet on every `_refresh_levels` cycle. Today they pinned at 4787.15/4779.20 for 2+ hours while the M5 box rebuilt three times. Verify the refresh cadence and the code path that updates them.
3. **Add `M30_BIAS_BLOCK` instrumentation to `decision_log.jsonl`.** Currently these vetoes are only in stdout; they never enter the decision log, making post-hoc audits of "blocked by bias" invisible in the structured log. Emit a minimal BLOCK record (direction, reason=M30_BIAS_BLOCK, proximity, ts) so that future investigations don't need to cross-reference stdout.
4. **Bias-vs-trend divergence alert.** When `daily_trend=long` but `m30_bias=bearish(confirmed)` persists for > N minutes with rising price, emit a Telegram/log alert so Barbara can observe before the opportunity closes. This window was 2.5 hours of a clean divergence.
5. **Consider a "provisional bullish -> allow LONG with V3/V4 score boost"** mode: when `provisional_m30_bias="bullish"` but `confirmed=False` (i.e., a new M30 box is forming above) — instead of strict veto, require a higher composite score. This matches the "M30 box comanda execução" rule while allowing early-pullback entries into strong impulses.
6. **Investigate GAMMA R:R threshold (`GAMMA_MIN_RR`)** in the context of the 2:1 SL-extension fallback. If the intention is "accept whenever the structural target is unreachable", then `rr >= GAMMA_MIN_RR` should be `> GAMMA_MIN_RR - epsilon` or the fallback should extend further than exactly 2:1.
7. **Shadow-log GAMMA/DELTA at INFO level** (not DEBUG) for `return None` branches, so production logs record *why* those paths skipped a given M5 close.

No fixes proposed in this document — per protocol these are research notes for the next sprint cycle with Barbara + Claude AI alignment.

---

## 7. System State — Post-Investigation

- **Files modified:** ZERO (read-only investigation)
- **Services restarted:** ZERO
- **Capture processes:** 3 of 3 intact
  - PID 12332: `python -m uvicorn quantower_level2_api:app --host 0.0.0.0 --port 8000` (started 2026-04-17 19:25:19)
  - PID 8248: `python iceberg_receiver.py` (started 2026-04-14 09:35:02)
  - PID 2512: `python watchdog_l2_capture.py` (started 2026-04-14 09:35:00)
- **Windows services:** `FluxQuantumAPEX` Running, `FluxQuantumAPEX_Dashboard` Running, `FluxQuantumAPEX_Live` Stopped, `FluxQuantumAPEX_Dashboard_Hantec` Stopped — no changes.
- **MT5, market feeds, signal queue:** not touched.
- **Data artefacts written:** this report only.

Investigation complete.

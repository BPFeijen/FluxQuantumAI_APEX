# P1.2 Phase 2 — TREND_CLASSIFIER_v2 Tiered Output: Post-Deploy

**Asana:** 1214332092113381
**Date:** 2026-04-28
**Author:** Claude Code (Opus 4.7)
**Branch:** `fix/xau-mid-population` (local commit only, Rule 11)
**Commit:** `82b83cc` (continuation of `2e9f091` Terminal 2 P1.4 Phase 2 Option B)

---

## TL;DR

Tiered output (HIGH / MEDIUM / NONE) deployed live in
`live/level_detector.py::_get_daily_trend()`. Implementation is **purely
additive** — the binary contract for the 5 existing consumers is preserved,
and no consumer behaviour was changed in this iteration. Tier is exposed
via:

1. `_LAST_DAILY_TREND_META["tier"]` — the existing diagnostics meta dict
2. `get_daily_trend_diagnostics()["tier"]` — heartbeat consumer (already wired)
3. `get_daily_trend_with_tier()` — new public accessor for opt-in consumers

Post-deploy live check confirms tier computation works correctly:
`direction='unknown' tier='NONE'` (current B+C disagreement state). The
system is healthy; no regressions; no changes to position sizing or
consumer routing. **First operational use of tier (e.g., reduced sizing
on MEDIUM) is deferred to a follow-up iteration with explicit Barbara
sign-off on MEDIUM semantics.**

---

## 1. Implementation summary

### Files changed

| File | LoC | Purpose |
|------|----:|---------|
| `live/level_detector.py` | +47 −2 | Tier computation in `_get_daily_trend`; new accessor `get_daily_trend_with_tier()`; `tier` field added to `_LAST_DAILY_TREND_META` |
| `tests/test_get_daily_trend_tiered.py` | +196 (new) | 8 unit tests covering tier logic + backwards-compat |

### Tier semantics (per Path A validation, full 9.7m window n=196)

| Tier | Logic | N (full window) | % | Accuracy h=5d (95% CI mental) |
|------|-------|----------------:|---:|-------------------------------|
| **HIGH** | B==C agree AND (A==B OR A==0) | 88 | 44.9 % | **0.7386** [64.7 %, 83.1 %] |
| **MEDIUM** | B==C agree AND A opposes | 40 | 20.4 % | 0.5750 [42.0 %, 73.0 %] |
| **NONE** | B!=C disagree, partial, or both zero | 68 | 34.7 % | n/a |

The MEDIUM tier's CI includes 50 % — it is **not statistically
distinguishable from coin-flip** at this sample size. Documented in the
Path A brief-back; tier MEDIUM is exposed but consumers should treat it
as a soft warning, not actionable.

### Backwards-compat (G-DELETE-DEPENDENCY-CHECK)

The 5 existing consumers in `event_processor.py` were **NOT modified**:

| Callsite | Function | Contract preserved |
|----------|----------|--------------------|
| `event_processor.py:1940` | PATCH2A continuation direction | binary string |
| `event_processor.py:2548-2549` | V1 zone COUNTER_HTF warning | binary string |
| `event_processor.py:3454-3461` | FMV progression | binary string |
| `event_processor.py:3484-3499` | `_get_strategy_mode` (PRIMARY) | binary string |
| `event_processor.py:3678` | GAMMA check_delta_trigger | binary string |

`_get_daily_trend()` continues to return `"long" | "short" | "unknown"`.
Tier is **additive** via the meta dict / new accessor.

### Heartbeat exposure

The heartbeat writer at `event_processor.py:1036` already calls
`get_daily_trend_diagnostics()` and writes the result under `daily_trend_diag`
in `decision_log.jsonl`. Since `tier` is now in the meta dict, it appears
automatically in every heartbeat row — **zero code change at the
heartbeat layer**.

---

## 2. Tests — 8/8 pass

`tests/test_get_daily_trend_tiered.py`:

| # | Test | Status |
|---|------|--------|
| 1 | `test_high_tier_when_abc_aligned_long` | ✅ |
| 2 | `test_high_tier_when_a_neutral` (A=0 treated as agreement) | ✅ |
| 3 | `test_medium_tier_when_a_opposes_bc` (long ensemble) | ✅ |
| 4 | `test_medium_tier_when_a_opposes_bc_short` (short ensemble) | ✅ |
| 5 | `test_none_tier_when_bc_disagree` | ✅ |
| 6 | `test_none_tier_when_partial_signal` | ✅ |
| 7 | `test_backwards_compat_binary_consumer` | ✅ |
| 8 | `test_diagnostics_dict_includes_tier` | ✅ |

**Full suite: 237 passed (+8 from 229 baseline post-Terminal-2 P1.4),
0 regressions.** 15 pre-existing `test_impl3_logic_c` failures unchanged
(EXEC-8 weight drift, out of scope).

---

## 3. Pre / post deploy sanity

### Pre-restart
- HEAD `2e9f091` (Terminal 2 P1.4 Phase 2 Option B)
- run_live PID 22924 (started 14:14 local = 12:14 UTC)
- Capture services 6376 / 13072 / 20396 alive (Apr 25-26 boot times)

### Post-restart (commit `82b83cc`)
- HEAD `82b83cc`
- run_live PID 16512 (started 14:34:03 UTC; uptime ~3 min at write time)
- **Capture services 6376 / 13072 / 20396 invariant** (same Apr 25-26 boot times)
- Boot log confirms healthy startup:
  - `M30_UPDATE: parquet fresh (age=222s) -- OK`
  - `M5 / M30 Updaters started`
  - `PositionMonitor + MacroMonitor + HEARTBEAT_LOOP_ENTERED`
  - `DATA-DRIVEN THRESHOLDS ACTIVE -- GC calibrated`

### Live tier verification (direct Python call)

```text
direction = 'unknown'
tier      = 'NONE'
signal_a  = -1   (ATS Trend Line: short)
signal_b  = +1   (Wyckoff HH/HL: long)
signal_c  = -1   (ICT BOS/CHoCH: short)
source    = 'unknown_b_c_disagree'
reason    = 'b=1,c=-1 (disagree)'
```

This matches the operational state surfaced in the Path A validation —
**B+C are currently disagreeing**, so the ensemble doesn't fire and tier
defaults to NONE. The new code is operating exactly as designed.

### Restart sequence
```
1. Identify pre-restart PID 22924 via Win32_Process query
2. Stop-ScheduledTask FluxQuantumAPEX_TaskScheduler
3. Stop-Process -Force 22924 (orphan handling per known Windows quirk)
4. Start-ScheduledTask FluxQuantumAPEX_TaskScheduler
5. Verify new PID 16512 + capture invariant
6. Wait for boot completion (~60-90 s for parquet load)
```

---

## 4. Operational impact

### Today's distribution (live state at deploy time)

The current 9.7m-validated breakdown and the Q2-2026 anomalous regime
(Apr 2026 = 76 % unknown) carry forward unchanged — **deploying tiered
output does NOT change the unknown rate today**. What changes:

- **Visibility:** every heartbeat row in `decision_log.jsonl` now exposes
  `daily_trend_diag.tier` ∈ {HIGH, MEDIUM, NONE}. Operators (Barbara,
  dashboard, future Claude) can grep/filter by tier.
- **Discriminability:** when the regime returns to a state where B==C
  agree (~65 % of historical), tier separates the strong-confidence
  88-session population (HIGH, 73.9 % accuracy) from the weak 40-session
  population (MEDIUM, 57.5 %).
- **Optionality for consumers:** any new logic that wants to degrade
  behaviour under MEDIUM (e.g., reduced position sizing, stricter
  confirmation gates) can opt-in via `get_daily_trend_with_tier()`
  without touching the 5 existing consumers.

### Out of scope for this iteration

Per ML-DS spec Q2 (MEDIUM semantics: option a / b / c) — **deferred**.
The first iteration only exposes tier; it does NOT yet wire any consumer
to vary behaviour by tier. This is intentional:

- **Risk minimisation:** the 5-consumer rewire is the largest
  regression-risk surface in this task family. Doing it as a separate
  follow-up keeps blast radius small.
- **Empirical confidence:** post-deploy 24h sample will show whether the
  tier distribution matches the 9.7m baseline OR whether the recent
  regime continues to dominate — that data should drive the consumer
  rewire decision.
- **Sample-size honesty:** MEDIUM CI [42 %, 73 %] cannot anchor a sizing
  decision today. A 24-48h live sample post-deploy adds incremental data.

The follow-up iteration spec will be drafted after Barbara reviews:

- `event_processor.py:_get_strategy_mode` is the highest-leverage
  consumer for the original operational issue (V1 zone defaulting to
  RANGE → fade-the-boundary losses); MEDIUM tier could allow trending
  behaviour with reduced confidence
- Position sizing logic (HIGH=1.0, MEDIUM=0.5, NONE=current) is the most
  granular but also touches the largest blast radius

---

## 5. Definition of Done — Phase 2

✅ `_get_daily_trend()` returns binary string (preserved)
✅ Tier computed and stored in `_LAST_DAILY_TREND_META`
✅ New accessor `get_daily_trend_with_tier()` for opt-in consumers
✅ Heartbeat exposes tier via existing `daily_trend_diag` wiring
✅ 8 unit tests pass; full suite 237 pass with 0 regressions
✅ Local commit on `fix/xau-mid-population` (commit `82b83cc`)
✅ Restart + sanity check: capture invariant preserved, new PID 16512 healthy
✅ Live tier computation verified (matches validation findings)
✅ This brief-back doc

⏸ **Sample 24h tier distribution post-deploy** — pending; will append to
this doc when sufficient data accumulates
⏸ **Consumer rewire (MEDIUM semantics)** — deferred to follow-up
iteration with explicit Barbara sign-off

---

## 6. PRAC

- **Confirmation bias** — implementation proceeded conservatively even
  though Path A validation pointed to consumer rewires. Decision: ship
  the visibility layer first, observe live, then decide on rewires with
  more data. This is the more rigorous path even though it means
  *no operational behaviour change today*.
- **Sample-size acknowledgement** — MEDIUM tier CI includes 50 %; we
  must NOT operationally treat MEDIUM as actionable until either (a) more
  live samples accumulate or (b) 3y OHLCV re-validation tightens the CI
  (deferred per Barbara — 9.7m L2 is the canonical window).
- **Bull-skewed window** — 0 short ABC-alignments in the validation
  sample. The HIGH-tier accuracy (73.9 %) is bull-only; bear-regime
  validity is unknown.
- **Backwards-compat verified** — `test_backwards_compat_binary_consumer`
  asserts the legacy contract holds. The 5 existing consumers in
  `event_processor.py` continue to call `_get_daily_trend()` and receive
  the same string they always have. Zero behavioural change.

---

## 7. Brief-back summary

✅ Phase 2 deployed (commit `82b83cc`)
✅ 0 regressions; 8 new tests pass; full suite 237/237 (excluding 15 pre-existing impl3 failures)
✅ Capture services invariant; run_live PID 22924 → 16512 (clean restart)
✅ Live tier computation verified: direction='unknown' tier='NONE' (B+C currently disagree, matches Path A live state)
⏸ Tier visibility live; first heartbeat rows post-restart will show `daily_trend_diag.tier` once cooldown completes
⏸ Consumer rewire deferred to follow-up; today's iteration is purely
   additive visibility — no operational behaviour change

🛑 **Awaiting authorization to start P1.6 Phase 0.**

# BUG-GO-LONG-TP1-INVERTED + BIAS-UNKNOWN-CONFIRMED — Phase 0 Investigation

**Task**: Asana 1214627990283129 (ML-DS comments 1214627791101106 + 1214627957756950)
**Author**: ClaudeCode Instance #1
**Date**: 2026-05-08
**Scope**: investigation only — root-cause + scope; NO implementation, NO halt
**Real-money loss attached** — Barbara entered LONG @ 4722.20 GC on signal `ecc33d2f`; price reverted past SL.

---

## STEP 1 — Signal `ecc33d2f` raw values

Source: `C:/FluxQuantumAI/logs/decision_log.jsonl` line for `decision_id="ecc33d2f"` @ 2026-05-08T04:52:09Z.

```
timestamp:        2026-05-08T04:52:09.914724+00:00 UTC
direction:        LONG    action_side: BUY    trade_intent: ENTRY_LONG
price_mt5:        4690.35
price_gc:         4722.20
gc_mt5_offset:    31.0
sl:               4670.35      ← stored as MT5 (entry_mt5 − 20)
tp1:              4710.35      ← stored as MT5 (entry_mt5 + 20)
tp2:              4740.35      ← stored as MT5 (entry_mt5 + 50)
sl_gc / tp1_gc / tp2_gc: not present (legacy schema, fallback only)

context.phase:                CONTRACTION
context.daily_trend:          unknown
context.m30_bias:             unknown
context.m30_bias_confirmed:   false
context.provisional_m30_bias: unknown
context.session:              ASIAN
context.delta_4h:             259.0
context.m30_atr14:            23.75
trigger.type:                 ALPHA
trigger.level_type:           liq_bot

gates.v1_zone:        PASS  (reason="")
gates.v2_l2:          NEUTRAL  (score=-1)
gates.v3_momentum:    OK  (delta_4h=259, score=0)
gates.v4_iceberg:     NEUTRAL  (score=3, type=large_order, aligned=true)

decision.action:        GO
decision.total_score:   3
decision.reason:        "iceberg large_order sc=+3"
decision.lots:          [0.02, 0.02, 0.01]

iceberg.detected:       true
iceberg.severity:       CRITICAL
iceberg.confidence:     3.0
iceberg.refills:        0
```

Risk-plan internal consistency (MT5 frame, entry=4690.35):
- SL: 4670.35 → −20.00 below entry  ✓ (correct LONG stop)
- TP1: 4710.35 → +20.00 above entry ✓ (correct LONG target)
- TP2: 4740.35 → +50.00 above entry ✓ (correct LONG runner)

The risk plan is CORRECT in MT5 space (symmetric +20/+50, −20).

---

## STEP 2 — Bug A TP1 calc trace

### Calculation in `event_processor.py`

LONG/SHORT branches (4 occurrences, all correct):
- Line 2784, 2788, 2883, 2889:
  ```python
  tp1 = price - tp1_pts if direction == "SHORT" else price + tp1_pts
  ```
  Where `price` = `entry_mt5` and `tp1_pts` = 20 (default `self.tp1_pts`).

For LONG: `tp1 = entry_mt5 + 20` → `4690.35 + 20 = 4710.35` ✓ matches log.

### `_risk_field` helper

The helper requested by ML-DS does NOT exist as a standalone function inside `event_processor.py`. It exists ONLY as a NESTED function inside `live/telegram_notifier.py:131-132`:

```python
def _risk_field(name: str, default=0):
    return dec.get(name, dec.get(f"{name}_gc", default))
```

It reads canonical `sl`/`tp1`/`tp2` from the decision dict, falling back to legacy `sl_gc`/`tp1_gc`/`tp2_gc`. The values stored in `decision_log.jsonl` are MT5 (`tp1 = round(tp1_gc - offset, 2)` per `event_processor.py:4055` for GAMMA, `:4352` for DELTA; and per the `tp1 = price + tp1_pts` form for ALPHA where `price` is the MT5-frame entry).

### TRUE BUG A — display space mismatch in Telegram message

`live/telegram_notifier.py::notify_decision()`:

```python
# Line 99-101 (SISTEMA-SIGNAL-ONLY-INTERIM Bloco I comment):
# "GC is sole canonical price frame (price_mt5 was removed from
#  decision_live.json schema)."
price = dl.get("price_gc", 0)            # ← 4722.20  (GC space)

# Line 131-132:
def _risk_field(name, default=0):
    return dec.get(name, dec.get(f"{name}_gc", default))
                          # ↑ returns sl=4670.35, tp1=4710.35, tp2=4740.35
                          #   stored as MT5 by event_processor

# Line 174 (GO action message):
f"{price:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f} | Runner: ON"
```

What Barbara received:
```
GO — LONG
4722.20 | SL: 4670.35 | TP1: 4710.35 | TP2: 4740.35 | Runner: ON
```

Asymmetric apparent inversion (offset = +31 GC↔MT5):
- SL appears `−51.85` from price_gc (looks normal: large LONG stop)
- TP1 appears `−11.85` from price_gc (BELOW entry — appears INVERTED for LONG)
- TP2 appears `+18.15` from price_gc (looks normal: small LONG target)

Why only TP1 looks inverted: TP1 distance in MT5 is **+20**, but GC offset is **+31**. Net effect when the MT5 value is read against GC entry: `entry_mt5 + 20 − entry_gc` = `entry_gc − 31 + 20 − entry_gc = −11`. SL distance in MT5 is **−20**; net read against GC: `−20 − 31 = −51`, which still looks like a SL. TP2 distance **+50** in MT5; net read: `+50 − 31 = +19`, still looks like a TP. **TP1 is the only level whose MT5 magnitude (20) is smaller than the offset (31)** — that's why only TP1 flips to the wrong side.

If Barbara entered at GC price 4722.20 on a platform interpreting SL/TP as GC values, the broker would either reject TP1 (LONG TP must be > entry) or accept it and instantly close on the favourable side, leaving the position with only TP2 (+18 pts). When price reverted past SL she got stopped out.

### Regression vs pre-existing

The `price = dl.get("price_gc", 0)` line is annotated as part of "SISTEMA-SIGNAL-ONLY-INTERIM Bloco I" — a recent change that promoted GC to the sole canonical price frame in the message and REMOVED `price_mt5` from `decision_live.json`. The risk-plan fields `sl`/`tp1`/`tp2` were NOT updated to GC during the same change. Result: a partial migration — entry price is now GC, risk levels are still MT5.

Telegram has a comment dated **2026-05-07** ("Bug fix 2026-05-07: previously fell through to generic branch without SL/TP1/TP2"), so the EXEC_FAILED branch was modified yesterday with the same `_risk_field` pattern.

**Regression boundary** (best estimate without full git blame): the GC-canonical message change landed within the last few days. The MT5-stored risk fields predate it. The 7-day cross-reference below confirms the bug is present from at least 2026-05-05 onwards.

---

## STEP 3 — Bug A 7-day cross-reference

Scanned `decision_log.jsonl` for last 7 days, action=GO, comparing `tp1` against `price_gc`:

```
Total GO signals 7d:                     1972
LONG  with tp1 < price_gc (inverted):    826 / 839   (98.5%)
SHORT with tp1 > price_gc (inverted):      0 / 1133  (0.0%)
```

**Verdict: SYSTEMIC, LONG-only.**

- Of 839 LONG signals, 826 (98.5%) display TP1 below the GC entry price. The 13 NON-inverted LONG signals are likely CONTINUATION-mode entries where `tp1_pts = atr_m30 × 0.8`; if ATR > 38.75 then `tp1_pts > offset (31)`, so the inversion vanishes.
- All 1133 SHORT signals show TP1 below entry (correct for SHORT). No inversion observed because for SHORT the formula is `tp1 = entry_mt5 − 20`; reading against `entry_gc` gives `−20 − 31 = −51`, still below entry — looks like a normal SHORT TP.

Sample (first 5 of 826):
```
2026-05-05T22:03:55  ab0be8aa  pgc=4561.55  sl=4512.15  tp1=4552.15  tp2=4582.15  Δtp1_vs_pgc=−9.40
2026-05-05T22:03:59  754e5600  pgc=4561.55  sl=4512.15  tp1=4552.15  tp2=4582.15  Δtp1_vs_pgc=−9.40
2026-05-05T22:04:02  6082b493  pgc=4561.55  sl=4512.15  tp1=4552.15  tp2=4582.15  Δtp1_vs_pgc=−9.40
2026-05-05T22:04:12  924b97f7  pgc=4561.55  sl=4512.15  tp1=4552.15  tp2=4582.15  Δtp1_vs_pgc=−9.40
2026-05-05T22:04:15  48cfa80d  pgc=4561.55  sl=4512.15  tp1=4552.15  tp2=4582.15  Δtp1_vs_pgc=−9.40
```

---

## STEP 4 — Bug B GO threshold analysis

### Why GO confirmed with bias=unknown

**Threshold** (`ats_live_gate.py:220`):
```python
MIN_SCORE_GO = 0   # score >= 0 → GO (neutral or positive)
```

**Decision logic** (`ats_live_gate.py:892-913`):
```python
if hard_block:
    go = False
elif total_score >= MIN_SCORE_GO:   # 0
    go = True
else:
    go = False
```

**Bias-unknown handling** (`event_processor.py:2560-2585`):
```python
if not m30_bias_confirmed:
    if provisional_m30_bias in ("bullish", "bearish"):
        log.info("M30_BIAS_PROVISIONAL_ONLY: ... -> no hard block")
    elif _is_patch2a:
        print("PATCH2A_BIAS_CHECK: ... provisional=unknown confirmed=unknown -> PASS")
    # implicit fall-through: NO hard block
else:
    # only blocks when CONFIRMED bias is contrary to direction
    if m30_bias == "bullish" and direction == "SHORT":  ...
    if m30_bias == "bearish" and direction == "LONG":   ...
```

**The two conditions combine to permit the signal**:
1. `m30_bias_confirmed = False` AND `provisional_m30_bias = "unknown"` → bias gate falls through with NO hard block (only logs).
2. `total_score = ice.score (=3) + mom.score (=0) = 3 >= MIN_SCORE_GO (=0)` → GO.

A single iceberg +3 score is the ONLY positive contributor, yet it dispatches GO without bias confirmation, without momentum, and without multi-confluence.

### V2 / V4 status reading

For `ecc33d2f`:
- **v1_zone PASS**: ALPHA trigger zone validated (price within proximity of `liq_bot`, score 0).
- **v2_l2 NEUTRAL/score=−1**: L2 microstructure score slightly negative; gate is NEUTRAL (does not hard-block; only fires V2_NOICE block if `v4_status==NEUTRAL AND ice.score <= -2` per line 885 — not the case here, `ice.score = +3`).
- **v3_momentum OK/score=0**: `delta_4h=259` is within "ok" range; not contributing to score either way.
- **v4_iceberg NEUTRAL/score=+3**: iceberg detector returned `large_order` aligned=true, confidence=3.0 (CRITICAL severity per protection block). Score contribution +3.

The reason `v2 — v4 —` annotation in Barbara's note matches: V2 shows NEUTRAL (no hard fail, no positive contribution), V4 shows NEUTRAL (no hard fail) but contributes +3 to total_score.

### Decision threshold logic — explicit handling de bias=unknown?

**No.** The decision threshold (`ats_live_gate._evaluate`) has NO special handling for bias=unknown. It only checks `total_score >= MIN_SCORE_GO (0)` and `not hard_block`. The bias-unknown handling lives upstream in `event_processor.py` and chooses NOT to hard-block when bias is fully unknown.

### Bug B 7-day cross-reference

Scan of 1972 GO signals last 7d:
```
bias=unknown OR not confirmed:     1147 / 1972  (58.2%)
   → LONG: 184    SHORT: 963
   → reason includes "iceberg large_order sc=+3" (single-signal pattern):  288
bias confirmed:                     825 / 1972  (41.8%)
```

**58% of all GO signals fire under bias=unknown.** The single-iceberg `sc=+3` pattern (same shape as `ecc33d2f`) accounts for 288 signals.

---

## Root causes (summary)

### Bug A — TP1 inverted display (asymmetric LONG-only)

**Root cause**: `live/telegram_notifier.py::notify_decision` mixes price frames in the message body — `price = dl.get("price_gc", ...)` (GC) but `_risk_field("sl|tp1|tp2")` returns MT5-stored values. The asymmetric inversion (LONG-only, TP1-only) is a numerical consequence of the +31 GC↔MT5 offset combined with the default `tp1_pts = 20` (MT5 magnitude < offset → flips sign when read against GC entry).

**Code locations**:
- `live/telegram_notifier.py:101` — `price = dl.get("price_gc", 0)` (uses GC frame)
- `live/telegram_notifier.py:131-132` — `_risk_field` reads MT5-frame values from `decision`
- `live/telegram_notifier.py:174` — message body interleaves both frames
- `live/event_processor.py:4055, 4352` — risk fields stored as `tp1_gc - offset` (MT5)
- `live/event_processor.py:2784, 2788, 2883, 2889` — ALPHA path stores `entry_mt5 ± tp1_pts` (MT5)

**Regression**: introduced by "SISTEMA-SIGNAL-ONLY-INTERIM Bloco I" which moved entry price to GC frame in the message but did NOT migrate sl/tp1/tp2.

**Scope**: SYSTEMIC. 826/839 LONG GO messages last 7 days display inverted TP1.

### Bug B — GO confirmed with bias=unknown + single-signal threshold

**Root cause**: `MIN_SCORE_GO = 0` is too permissive for bias-unknown contexts. Combined with `event_processor.py:2560-2569` letting `provisional_m30_bias == "unknown"` AND `m30_bias_confirmed == False` fall through without a hard block, a single-signal +3 iceberg score satisfies the threshold and dispatches GO.

**Code locations**:
- `ats_live_gate.py:220` — `MIN_SCORE_GO = 0`
- `ats_live_gate.py:896` — `elif total_score >= MIN_SCORE_GO: go = True`
- `event_processor.py:2560-2585` — bias gate only blocks on CONFIRMED contrary bias

**Regression**: `MIN_SCORE_GO = 0` and the bias-unknown fall-through both pre-exist for an extended period; this is a long-standing PERMISSIVE design rather than a recent regression. ML-DS hypothesis "context loss" is consistent with the threshold being below the floor needed when `m30_bias` is fully unknown.

**Scope**: SYSTEMIC. 1147/1972 GO signals last 7d (58.2%) fire under bias=unknown. 288 of those are the single-iceberg pattern matching `ecc33d2f`.

---

## Fix recommendations (TBD by spec — NOT implemented)

### Bug A — TP1 display

Two viable fixes (ML-DS to choose):

**Option 1 (recommended) — fix telegram message to read MT5 entry price**:
- Restore `price_mt5` to `decision_live.json` (was REMOVED by SISTEMA-SIGNAL-ONLY-INTERIM Bloco I)
- Change `live/telegram_notifier.py:101` → `price = dl.get("price_mt5", dl.get("price_gc", 0))`
- All four message branches (EXECUTED, GO, EXEC_FAILED, BLOCK) use the MT5 price consistently with MT5 sl/tp1/tp2.
- Lowest-risk: matches the broker space the operator manually mirrors.

**Option 2 — store risk plan in GC frame**:
- Modify `event_processor.py` to store `sl_gc/tp1_gc/tp2_gc` as canonical (drop the `- offset` conversion at lines 4055, 4352, and the `entry_mt5 ± pts` form for ALPHA at 2784/2788/2883/2889).
- Higher risk: every consumer (executor, position_monitor, hedge_manager, telegram message tier 2) must be migrated atomically. Mixed-space consumers will silently break.

**Option 3 (minimal) — display both frames in the message**:
- Append `(GC: sl=… tp1=… tp2=…)` line computed by adding offset.
- Operator-facing fix only; underlying schema mismatch persists. Lowest implementation cost; easiest to roll back.

### Bug B — GO threshold under bias=unknown

Two viable fixes (ML-DS to choose):

**Option 1 (recommended) — bias-unknown specific threshold**:
- Add to `ats_live_gate._evaluate` (or upstream in `event_processor.py`) a condition: if `m30_bias_confirmed == False`, require `total_score >= MIN_SCORE_GO_UNKNOWN_BIAS` (e.g., +5) instead of the generic `MIN_SCORE_GO = 0`.
- Preserves current behaviour when bias IS confirmed; only tightens the unknown-bias path.

**Option 2 — multi-confluence requirement under bias=unknown**:
- When bias is unknown, require at least 2 of {momentum.score > 0, iceberg.detected+aligned, V1 high-confidence zone}. A single iceberg signal is no longer sufficient.

**Option 3 — global threshold tightening**:
- Raise `MIN_SCORE_GO` from 0 to e.g., +2. Affects all signals, not just bias-unknown. Higher false-negative cost but simpler.

---

## Cross-reference with parallel work

- **BUG-SIGNAL-INVERTED Phase 5 obs** (paralelo): NOT touched. May share root cause with Bug A (price-frame mismatch); note that here for ML-DS to evaluate consolidation.
- **BUG-M30-STUCK-VS-H4-FLIP backlog**: NOT touched.

---

## Hand-off

Phase 0 investigation complete. Two bugs root-caused, scope quantified. ZERO implementation, ZERO halt. Awaiting ML-DS triage + Phase 1 fix spec.

Bug A urgency: **HIGH** — affects 98.5% of LONG GO messages; Barbara has real-money loss attached. Recommend Option 1 (restore `price_mt5` to message) as fastest reversible patch.
Bug B urgency: **MEDIUM** — long-standing permissive design, but compounded with Bug A delivers operator-confusing signals at scale (288 single-iceberg unknown-bias signals last 7d).

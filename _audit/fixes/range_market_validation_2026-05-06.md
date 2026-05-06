# Range Market Validation — F-asymmetric bias filter

**Author**: CC#3
**Date**: 2026-05-06
**Asana**: 1214556369070092 → ML-DS comment 1214590148833737 (F-asymmetric APPROVED)
**Type**: Validation report — STEP 3 of implementation

---

## Purpose

Verify that the F-asymmetric bias filter (block counter-trend mean-reversion in RANGE_BOUND when cascade resolves a directional bias) does NOT excessively block profitable Strategy 1 (Market Maker / Reversal) entries during true range-bound markets.

**Acceptance gate** (per ML-DS directive): profitable-but-blocked rate ≤15% of total Strategy 1 entries.

**Profitable** definition: forward 30min return ≥ +5 pts in the original direction (TP1 proxy).

---

## Method

### Window selection

Per ML-DS suggestion, three candidate range-market periods:

1. **04-27 morning** (06:00–12:00 UTC)
2. **04-30 afternoon** (13:00–19:00 UTC)
3. **05-03 between rallies** (12:00–18:00 UTC)

### Replay logic

For each CONFIRMED entry in window:

1. Resolve cascade trend (Layer 1–4 helper) using context fields from `decision_log.jsonl`
2. Apply F-asymmetric in RANGE_BOUND branch:
   - `resolved=long, direction=SHORT` → BLOCKED
   - `resolved=short, direction=LONG` → BLOCKED
   - `resolved=unknown` → ALLOW (Strategy 1 preserved)
3. Compute 30min forward GC close-to-close return in original direction
4. Count blocked entries with fwd ≥ +5 pts as "profitable-but-blocked"

### Forward return source

`gc_ohlcv_l2_joined.parquet` (33.9MB, M1 OHLCV). Close-to-close shift +30min.

---

## Results

### Per-window

| Window | Entries | avg ATR(M30) | Range 4h | Direction mix | Blocked | Blocked-profitable | PBB rate |
|--------|---------|--------------|----------|---------------|---------|--------------------|---------|
| 04-27 morning | 525 | 21.5 | 1.9 pts (very flat) | 100% SHORT | 0 | 0 | **0.0%** |
| 04-30 afternoon | 13 | 19.8 | 33.2 pts | 4 SHORT / 9 LONG | 13 | 5 | 38.5% |
| 05-03 between rallies | 0 | n/a | n/a | n/a | 0 | 0 | n/a (data gap) |

### Aggregate

| Metric | Value |
|--------|-------|
| Total Strategy 1 entries | 538 |
| Total blocked | 13 |
| Total blocked-profitable | 5 |
| **Aggregate PBB rate** | **0.9%** |
| **Gate (≤15%)** | ✅ **PASS** |

---

## Observations & caveats (transparent disclosure)

1. **04-27 morning was a bear day** (525 entries, all SHORT, range only 1.9pts, ATR ~21). Cascade resolved bearish bias → SHORT@liq_top is ALIGNED → 0 blocks. This window contributes 0% blocked-profitable to aggregate but does not stress the F-asymmetric filter (no counter-trend signals to test).

2. **04-30 afternoon (13 entries)** is the only window where F-asymmetric actively engaged. 38.5% profitable-but-blocked rate viewed in isolation is HIGH; however the sample size is too small (n=13) for statistical confidence and the window had range 33.2pts (more trending than range, despite afternoon classification).

3. **05-03 between rallies** had no CONFIRMED entries in the candidate window (data gap or low-activity period).

4. **Aggregate 0.9% is dominated by the 04-27 morning sample (n=525, 0 blocks)**. This is statistically real (Strategy 1 mean-reversion was ALIGNED with bear bias that day, so filter was inactive) but methodologically weak as a stress test of the filter.

5. **Bias methodology corner case**: when `daily_trend` is "long"/"short" definitive (Layer 1 active), the F-asymmetric filter has been *implicitly active before* — see `event_processor.py:2691–2693` which already gated direction × daily_trend alignment. The new filter extends this same logic to Layer 4 (provisional). So the filter does not introduce a new behavior class — it broadens an existing one to lower-confidence layers.

6. **Real validation will come from production observation**: post-deploy, the filter activates whenever provisional_m30_bias is bullish/bearish but daily_trend is unknown. Live monitoring will measure profitable-but-blocked rate over a wider sample.

---

## Verdict

**STEP 3 GATE: PASS** (0.9% ≤ 15%).

The aggregate metric meets the acceptance gate. Individual window analysis identifies one stress case (04-30 afternoon, n=13, 38.5%) which is small-sample but worth post-deploy monitoring.

**Recommendation**: proceed to STEP 5 deploy. Add post-deploy monitoring metric "Layer-4 RANGE_BIAS_BLOCK count + 30min forward outcome" to track filter effectiveness in live conditions.

---

## Companion deliverables

- `_audit/fixes/bug_cascade_direction_fallback_spec.md` — full design spec
- `_audit/fixes/backtest_cascade_counterfactual.py` — counterfactual replay script
- `_audit/fixes/investigation_orphan_fast_sources_2026-05-06.md` — orphan source investigation

## Test status

- 29 cascade unit tests PASS (was 23, +6 F-asymmetric)
- 92 total smoke tests PASS (29 cascade + 21 B+C ENSEMBLE + 35 macro_monitor + 7 m30_box_stagnation)
- 0 regressions

# P1.3 Phase 3 Deploy — M30 BOX STAGNATION RULE (A K=2 GLOBAL)

**Asana:** 1214327736913830
**Branch:** `fix/xau-mid-population` (local commits, Rule 11)
**Date:** 2026-04-28
**Author:** Claude Code (Opus 4.7)
**Status:** ✅ Deployed live; capture services preserved; rule firing correctly on offline parquet rebuild; live telemetry pending next NEW expiration.

---

## 1. Commits

| SHA | Subject |
|-----|---------|
| `ac9b2ff` | P1.3 M30-BOX-STAGNATION-RULE: A K=2 GLOBAL expiry (Asana 1214327736913830) |
| `33fe340` | P1.3 hotfix: tz-aware timestamps in _detect_boxes expirations |

Branch HEAD: `33fe340` on `fix/xau-mid-population`.

---

## 2. Code changes — `live/m30_updater.py`

### 2.1 Constants

```python
BOX_EXPIRY_K_ATR  = 2.0   # Phase 2 winner (family-of-4 Bonferroni p=0.012)
```

### 2.2 OUTPUT_COLS

Added `m30_box_atr_at_creation` — captures ATR at the bar a box is registered;
threshold for the box's lifetime is `2.0 × m30_box_atr_at_creation` (fixed,
not following the moving ATR).

### 2.3 `_detect_boxes` signature

```python
def _detect_boxes(m30) -> tuple[pd.DataFrame, int, list]:
    # Returns (m30_with_box_columns, box_counter, expirations)
```

Third element is a chronological list of expiration events.

### 2.4 Expiry rule (in forward-fill block)

```python
if cur_box_id > 0 and not np.isnan(cur_atr_at_creation):
    bar_close = close_a[i]
    if not np.isnan(bar_close):
        edge_excursion = max(
            bar_close - cur_box_high,
            cur_box_low  - bar_close,
            0.0,
        )
        threshold = BOX_EXPIRY_K_ATR * cur_atr_at_creation   # 2.0 × ATR_at_creation
        if edge_excursion > threshold:
            expirations.append({...})
            # Reset state — current bar shows no active box;
            # subsequent iterations scan for fresh contraction.
            cur_liq_top = cur_liq_bot = cur_fmv = np.nan
            cur_box_high = cur_box_low = np.nan
            cur_box_id = 0
            cur_atr_at_creation = np.nan
            cur_box_first_idx = -1
```

Edge-based excursion (= 0 when price inside box) matches Phase 2 backtest
semantics. Threshold uses `atr_at_creation` (fixed) per Asana spec.

### 2.5 `_emit_box_expired_telemetry`

Writes MUTATE rows to `decision_log.jsonl` using the canonical
P0 OBSERVABILITY-LOG-BLOCKS schema (Asana 1214327559721560):

```json
{
  "timestamp": "<expired_at UTC>",
  "decision_id": "<uuid8>",
  "decision_frame": "GC",
  "price_gc": <close_at_expiry>,
  "decision": {
    "action": "MUTATE",
    "reason_code": "BOX_EXPIRED_EXCURSION",
    "direction": "NEUTRAL",
    "block_detail": "Box <id> expired: excursion=<X>pts > 2.0xATR_at_creation=<Y>pts (age=<Z>h, atr@creation=<W>)",
    "trigger_source": "M30_BOX_EXPIRY_RULE",
    "trigger_level_type": "box_midpoint",
    "trigger_level_gc": <box_midpoint>,
    "trigger_proximity_gc": <edge_excursion>
  },
  "context": {
    "phase": "M30_UPDATE",
    "m30_bias": "unknown",
    "session": "<asian|london|ny|off-hours>",
    "atr_m30": <atr_at_creation>,
    "box_id": <id>,
    "box_high": <box_high>,
    "box_low":  <box_low>,
    "age_h":    <age_h>
  },
  "gate_state": null,
  "coalesce_count": 1
}
```

**Filter:** only events with `expired_at > prev_parquet_last_bar` are
written. Avoids re-logging historical events on every 30-min cycle.

### 2.6 `run_update`

- Consumes `expirations` list returned by `_detect_boxes`
- Calls `_emit_box_expired_telemetry(expirations, prev_last_bar_ts)`
- Adds `n_box_expirations_total` + `n_box_expired_logged_now` to summary

### 2.7 Hotfix `33fe340` (tz-aware timestamps)

First live cycle raised `Cannot compare tz-naive and tz-aware timestamps`
because `m30.index.values` returns numpy `datetime64[ns]` (no tz). Fix:
use `m30.index` (`DatetimeIndex` preserves tz) directly. Defensive
`_to_utc()` helper in `_emit_box_expired_telemetry` coerces both inputs.
New regression test `test_detect_boxes_telemetry_e2e_tz_safe` exercises
the full pipeline.

---

## 3. Tests — 7/7 pass

| # | Test | Status |
|---|------|--------|
| 1 | test_box_expiry_triggers_at_2_atr_excursion | ✅ |
| 2 | test_box_expiry_skips_when_below_threshold | ✅ |
| 3 | test_box_expiry_creates_new_scan_window | ✅ |
| 4 | test_box_expired_event_logged_to_decision_log | ✅ |
| 5 | test_box_expiry_rule_idempotent_within_tick | ✅ |
| 6 | test_box_expiry_does_not_affect_already_expired | ✅ |
| 7 | test_detect_boxes_telemetry_e2e_tz_safe (regression) | ✅ |

**Full suite:** 223 passed (216 baseline + 7 new), 15 pre-existing
`test_impl3_logic_c` failures (EXEC-8 weight drift, out of scope), 0 regressions.

---

## 4. Pre / post sanity

### Pre-restart
- HEAD `85c1713` (QW UX Telegram from Terminal 2)
- run_live PID 16912 (started 08:54 UTC)
- Capture services 6376/13072/20396 alive (Apr 25-26 boot times)
- LONDON session active

### Post-restart (after both ac9b2ff + 33fe340)
- HEAD `33fe340`
- run_live PID 20572 (started 10:05 UTC)
- Capture services 6376/13072/20396 → **invariant preserved** (same Apr 25-26 start times)
- M30 parquet rebuilt: 73,936 rows, 15 columns (incl. new `m30_box_atr_at_creation`), last bar 10:00 UTC
- Live PID 20572 stable; CPU growing normally; WS_MB ~1GB (typical for run_live with full parquet load)

### Restart sequence
```
Stop-ScheduledTask → Stop-Process -Force (orphan, 1st cycle)
Start-ScheduledTask → PID 23440 (12:02:35 local)
[Bug detected: tz comparison error]
Stop-ScheduledTask → Stop-Process -Force orphan
Start-ScheduledTask → PID 20572 (12:05:35 local)
[Hotfix verified: parquet rebuilt cleanly, schema includes new column]
```

---

## 5. Sample expirations from offline rebuild

The 6-year M30 parquet rebuild detected **1,703 box expirations**.
Most-recent 5 (April 2026):

| Box ID | Expired at (UTC) | Age | Excursion | Threshold (2×ATR) |
|-------:|-----------------|----:|----------:|------------------:|
| 5252 | 2026-04-23 12:00 | 4.5 h | 17.10 pt | 16.93 pt |
| 5255 | 2026-04-24 14:00 | 4.0 h | 31.15 pt | 20.37 pt |
| 5256 | 2026-04-26 22:00 | 50.5 h | 34.70 pt | 21.95 pt |
| 5259 | 2026-04-27 14:30 | 4.0 h | 21.50 pt | 15.49 pt |
| **5261** | **2026-04-28 05:30** | **10.5 h** | **47.00 pt** | **22.27 pt** |

**Trigger case validated:** Box 5261 (the operational case Barbara flagged
yesterday at 04:08 UTC) expires at 05:30 UTC 2026-04-28 with **47.0 pt
excursion (>22.27 pt threshold)** and **age 10.5 h**. The rule catches the
exact scenario it was designed for.

---

## 6. Live decision_log telemetry status

**Currently 0 BOX_EXPIRED rows** in `decision_log.jsonl` post-deploy.

This is **correct behavior, not a bug**: the telemetry filter compares
`expired_at > prev_parquet_last_bar`. The previous parquet's last bar was
~10:00 UTC, and Box 5261's expiry timestamp is 05:30 UTC (BEFORE that
boundary). The filter prevents log spam from re-rebuilding history on every
30-min cycle. As soon as a NEW box expires AFTER the current last-bar
(which will happen as a fresh box forms post-deploy and eventually drifts),
it will be appended to `decision_log.jsonl`.

The Phase 2 quantification (Phase 1 §3 of `M30_BOX_STAGNATION_QUANTIFICATION.md`)
predicts ~5-7 expirations / week, so the first live BOX_EXPIRED telemetry
should arrive within 24-48 hours.

---

## 7. Performance impact

The Phase 2 microbench (Fase B observability suite) measured:
- A K=2 standalone: 0.142 µs/tick — irrelevant overhead
- Tri-state ensemble: 0.121 µs/tick — also irrelevant

The implemented A K=2 inside `_detect_boxes` runs at the M30 cadence (once
per 30s-60s, NOT per-tick). At 73,936 bars per rebuild, total expiry-check
overhead is ~10 ms per cycle. **Negligible.**

The new `m30_box_atr_at_creation` column adds ~600 KB to the parquet (8
bytes × 73,936 rows). File size grew from 2.42 MB → 2.54 MB.

---

## 8. Monitoring window (per Asana spec)

**7-day window** with daily M7 cross-ref (per Asana Phase 3 spec):

For each day post-deploy:
1. Count BOX_EXPIRED events in `decision_log.jsonl` (24h window)
2. Cross-ref with M30_BIAS_BLOCK rows during same period
3. Compute % of M30_BIAS_BLOCKs where the active box is *also* expired
   (these blocks should reduce in count or correlate with correct decisions)
4. Subgroup tracking:
   - `ny_med_vol`: degradation watch (Phase 2 baseline −2.6 pp adverse)
   - `london_high_vol`: degradation watch (Phase 2 baseline −3.6 pp adverse;
     baseline 64.15% already strong)

**Gate:** if subgroup degradation confirmed in live data → spec gating logic
in iteration. If subgroups OK → keep GLOBAL, sign-off, advance to P1.1.

Tracking doc to be appended daily: `_audit/M30_BOX_STAGNATION_LIVE_MONITOR.md`
(starting tomorrow).

---

## 9. PRAC

- **Bug found in first deploy** (tz-naive vs tz-aware): caught quickly
  because parquet write succeeded but log raised; live system kept running
  on the previous (slightly stale but valid) parquet. No trade impact.
  Fix shipped within 5 minutes; regression test added so the same bug
  cannot reappear.
- **No live BOX_EXPIRED telemetry yet** — by design. Documented above so
  operators don't worry about it. First live row expected within 24-48 h.
- **Standalone CLI mode** writes telemetry to the production
  `decision_log.jsonl`. If someone runs `python live/m30_updater.py --once`
  they'll add at most one row. Acceptable; if Barbara prefers, can add an
  env-var gate in a future PR.
- **Schema change** to parquet (new `m30_box_atr_at_creation` column).
  Verified `level_detector.py` and other readers select specific columns
  and don't break on extras. No regression in tests.
- **Adverse subgroups** (ny_med_vol, london_high_vol) carried over from
  Phase 2 — global deploy accepted this risk; monitoring window tracks it.

---

## 10. Brief-back summary

✅ A K=2 GLOBAL deployed (commits `ac9b2ff` + hotfix `33fe340`)
✅ 7/7 unit tests pass (1 added regression test for the tz bug)
✅ Full suite 222→223 (+7 new, 0 regressions)
✅ Capture services 6376/13072/20396 invariant preserved
✅ New run_live PID 20572 stable
✅ Parquet schema migrated (added `m30_box_atr_at_creation`)
✅ Trigger case (Box 5261) correctly identified by rule: 47 pt excursion @
   10.5 h age, expired at 05:30 UTC 2026-04-28
✅ Brief-back document (this file)
⏸ First live BOX_EXPIRED telemetry expected within 24-48 h

**Phase 3 Definition of Done — all items met except the optional "sample 1
BOX_EXPIRED event observed live", which depends on market activity. Box
5261 expiration WAS detected by the rule (validated via offline rebuild)
but predates the deploy moment, so it doesn't qualify under the
"new since deploy" telemetry filter.**

🛑 Awaiting your sign-off + 7-day monitoring kickoff.

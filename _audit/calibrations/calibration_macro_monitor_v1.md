# Calibration: MacroMonitor v1 (MVP 5 triggers)

**Task:** MACRO-MONITOR-VAP (Asana 1214290409737742)
**Date:** 2026-04-27
**Author:** ClaudeCode
**Branch:** fix/xau-mid-population at HEAD `401e6da`
**Barbara directive (2026-04-27):** *"se o threshold é o que temos no sistema, significa que está calibrado"* — inherit existing prod thresholds; calibrate ONLY truly new parameters.

**Scope:** MVP 5 triggers per Asana task ML-DS recommendation #5. v2 backlog (PRICE_ACTION_STOP, REGIME_FLIP_D1, MOMENTUM_FLIP, VIRTUAL_TP2) deferred.

---

## Spec amendment from design doc v1

| Trigger | Original v1 design | MVP final | Reason |
|---|---|---|---|
| 1 | ICEBERG_AGAINST | unchanged | inherits existing |
| 2 | ANOMALY_AGAINST | unchanged | inherits existing |
| 3 | REGIME_FLIP_M30 | unchanged | flip event, no threshold |
| 4 | (was REGIME_FLIP_D1) | **DEFERRED to v2** | depends on B+C ensemble bedded in for >1 calibration cycle |
| 5 | (was MOMENTUM_FLIP) | **DEFERRED to v2** | new threshold (`min_magnitude`) violates Barbara directive |
| 6 | PRICE_ACTION_STOP | **DEFERRED to v2** | requires new `atr_multiple` threshold; superseded by VIRTUAL_SL for MVP |
| 7 | VIRTUAL_TP1 | unchanged | level mirror |
| 8 | (was VIRTUAL_TP2) | **DEFERRED to v2** | redundant with TP1 for MVP |
| 9 | VIRTUAL_SL | **PROMOTED to MVP** | level mirror; critical "stop reached" alert |

**Final MVP 5 triggers:** ICEBERG_AGAINST, ANOMALY_AGAINST, REGIME_FLIP_M30, VIRTUAL_TP1, VIRTUAL_SL.

**Net result of Barbara directive:** ZERO new methodology thresholds need empirical calibration in Phase 0. Only 1 operational parameter (VAP lifetime) is genuinely new — and it's operational, not methodology.

---

## Per-trigger threshold inheritance audit

### Trigger 1 — ICEBERG_AGAINST

**Condition:**
```
iceberg.detected == True
AND iceberg.severity ∈ {HIGH, CRITICAL}
AND iceberg.side opposite VAP.direction
  (VAP=SHORT → iceberg.side=="BUY"; VAP=LONG → iceberg.side=="SELL")
```

**Severity threshold inheritance:**
Source: `live/event_processor.py:957-962` (in current production):
```python
"severity": (
    "CRITICAL" if iceberg.score >= 0.90
    else "HIGH"  if iceberg.score >= 0.75
    else "MEDIUM" if iceberg.score >= 0.60
    else "LOW"   if iceberg.detected else "NONE"
),
```

→ MM-VAP `min_severity = HIGH` ⟺ `iceberg.score >= 0.75`. **INHERITED — calibrated by being in prod.**

**Refills threshold inheritance:**
Source: `apex_nextgen/providers/order_storm/provider.py:52`:
```python
_REFILL_SIGNIFICANT_COUNT = 2    # >= 2 refills = cadeia iceberg real
```

→ MM-VAP optional `min_refills = 2` (additive filter, not replacement). **INHERITED.** *Note: for MVP not enforced; severity≥HIGH already implies real iceberg via score gate.*

**Side determination:** read `iceberg.side` directly from `decision_live.json.iceberg.side`. Values: `BUY` / `SELL` / `UNKNOWN`. Set in `event_processor.py:950-952` from `ice.sweep_dir`.

**Status:** ZERO NEW THRESHOLDS.

---

### Trigger 2 — ANOMALY_AGAINST

**Condition:**
```
anomaly.detected == True (defense_mode active)
AND anomaly.severity ∈ {HIGH, CRITICAL}
AND stress_direction implies AGAINST VAP.direction
  (VAP=SHORT → stress_direction ∈ {EXIT_SHORT, EXIT_ALL};
   VAP=LONG  → stress_direction ∈ {EXIT_LONG,  EXIT_ALL})
```

**Severity threshold inheritance:**
Source: `live/event_processor.py:2481-2499`:
```python
if _dm_tier == "DEFENSIVE_EXIT":
    _a_sev = "CRITICAL"
elif _dm_tier == "ENTRY_BLOCK" or _dm_active:
    _a_sev = "HIGH"
else:
    _a_sev = "NONE"
```

→ MM-VAP `min_severity = HIGH` ⟺ `defense_tier ∈ {ENTRY_BLOCK, DEFENSIVE_EXIT}` OR `defense_mode active`. **INHERITED.**

**Direction matching inheritance:**
Source: `live/event_processor.py:2500-2506`:
```python
if direction == "LONG":
    _a_flow = "AGAINST_LONG" if _dm_stress in ("EXIT_LONG", "EXIT_ALL") else "FAVORS_LONG"
elif direction == "SHORT":
    _a_flow = "AGAINST_SHORT" if _dm_stress in ("EXIT_SHORT", "EXIT_ALL") else "FAVORS_SHORT"
```

→ MM-VAP re-derives `flow_relation` relative to VAP direction (not current decision direction). Same `stress_direction` mapping. **INHERITED.**

**Read sources** in `service_state.json` (already exposed in heartbeat):
- `defense_tier` field
- `stress_direction` field

**Status:** ZERO NEW THRESHOLDS.

---

### Trigger 3 — REGIME_FLIP_M30

**Condition:**
```
m30_bias_confirmed == True
AND m30_bias is opposite to VAP.direction
  (VAP=SHORT → m30_bias=="bullish"; VAP=LONG → m30_bias=="bearish")
```

**No severity threshold needed** — m30_bias is categorical {bullish, bearish, unknown}.

**Confirmation flag inheritance:**
Source: `live/level_detector.py:430` (`derive_m30_bias` returns `(bias, is_confirmed_source)`).

The `m30_bias_confirmed=True` requirement ensures we only fire on STRUCTURALLY validated bias flips — the same rule the production gate uses. **INHERITED.**

**Bias derivation inheritance:**
Source: `live/level_detector.py:351-451` (`derive_m30_bias`).

The classification logic (bullish_extension via `liq_top > box_high`, bearish_extension via `liq_bot < box_low`) is the production logic. **INHERITED unchanged.**

**Status:** ZERO NEW THRESHOLDS. ZERO NEW LOGIC.

---

### Trigger 4 — VIRTUAL_TP1

**Condition:**
```
VAP=SHORT → current_price <= VAP.tp1
VAP=LONG  → current_price >= VAP.tp1
```

**No threshold** — pure level mirror. `VAP.tp1` read directly from `decision.tp1` in the originating GO decision payload.

**TP1 calculation inheritance:**
Source: `live/event_processor.py:2755-2756, 2847-2848`:
```python
_tp1_mult_pre = float(self._thresholds.get("trend_cont_tp1_atr_mult", 0.8))
```

The TP1 distance from entry = `0.8 × ATR(M30)`. Already calibrated in production. MM-VAP simply mirrors the resulting price level. **INHERITED.**

**Status:** ZERO NEW THRESHOLDS.

---

### Trigger 5 — VIRTUAL_SL

**Condition:**
```
VAP=SHORT → current_price >= VAP.sl
VAP=LONG  → current_price <= VAP.sl
```

**No threshold** — pure level mirror. `VAP.sl` read directly from `decision.sl` in the originating GO decision payload.

**SL calculation inheritance:**
Source: production code computes `sl` per existing risk plan (varies by entry_mode RANGE/TRENDING). MM-VAP simply mirrors the resulting price level. **INHERITED.**

**Status:** ZERO NEW THRESHOLDS.

---

## ONE genuinely new operational parameter

### `vap_max_lifetime_min`

**Purpose:** how long to keep a VAP active (alerting) before declaring it expired and removing it from heartbeat.

**Default proposed:** **240 minutes (4 hours)**

**Rationale:**
- Not a methodology threshold — operational/UX parameter
- 4h covers a full Asia session OR a full London open without re-evaluation
- Beyond 4h, the original entry signal has materially aged (M30 boxes have rebuilt 8+ times; ATR has evolved); continuing to alert on it is noise
- No existing system parameter to inherit from (PositionMonitor monitors REAL positions which have no time bound — they exit on SL/TP)

**Per Barbara directive applied strictly:** since no existing threshold exists for this concept, this parameter is genuinely new. Treat as operational default; flag for Barbara review.

**Alternative interpretations Barbara may prefer:**
- **A. 4h fixed default** (proposed)
- **B. Until next opposite GO** (no time bound; supersede-only)
- **C. Until end of trading session** (e.g., midnight UTC)
- **D. Configurable per VAP based on entry_mode** (RANGE vs TRENDING)

If Barbara wants different, change `MACRO_MONITOR_CONFIG["vap_max_lifetime_min"]` in Phase 1 implementation. No code logic change needed.

---

## Anti-spam (operational, no calibration needed)

**Per spec:** 1 alert per `(vap_id, trigger_id)` pair. Implementation: in-memory set on the MacroMonitor instance.

This is operational logic (not a threshold), no calibration applies.

---

## Data sources audit

All data sources already exist in production. No new data pipelines needed.

| Data | Source | Refresh cadence |
|---|---|---|
| Current decision (GO/BLOCK/EXEC_FAILED) | `logs/decision_live.json` | per gate fire (~1-2s) |
| Decision history (for VAP rebuild on restart) | `logs/decision_log.jsonl` | append-only |
| Current price (MT5 spot) | `logs/service_state.json:mt5_price` | 30s heartbeat |
| iceberg state | `logs/decision_live.json:iceberg` | per gate fire |
| anomaly state | `logs/decision_live.json:anomaly` (also `service_state.json:defense_tier/stress_direction`) | per gate fire |
| m30_bias + confirmed flag | `logs/service_state.json:m30_bias / m30_bias_confirmed` | 30s heartbeat |
| ATR(M30) | `logs/service_state.json:atr_m30` | 30s heartbeat |

**No new data ingestion** — MM is a pure consumer of existing artifacts.

---

## Phase 0 acceptance criteria — STATUS

- [x] All MVP 5 triggers identified
- [x] Per-trigger threshold sources documented (existing prod inheritance)
- [x] Each inherited threshold cites file:line in production code
- [x] Single new operational parameter (`vap_max_lifetime_min`) flagged for Barbara review
- [x] Data sources audited (zero new pipelines)
- [x] Anti-spam strategy documented (operational, no threshold)
- [x] v2 backlog scope (4 deferred triggers) explicitly listed
- [N/A] Empirical calibration window — not needed; per Barbara directive, prod thresholds are calibrated by being in prod
- [N/A] Bonferroni-corrected p-values — not needed; no new statistical claims being made
- [N/A] False-alarm rate gate — moved to Phase 2 as backtest output, not Phase 0 calibration target

---

## G-PREMISE-AUDIT

**PREMISES INHERITED (from prior commits + Barbara directive):**
- iceberg.severity classification at score thresholds {0.90, 0.75, 0.60} — VALIDATED in prod since pre-2026-04-26
- anomaly.severity tied to defense_tier — VALIDATED in prod via DEFENSE_MODE
- m30_bias_confirmed semantics — VALIDATED via PM-DECOUPLE-PHASE3 (commit 889f5ee)
- decision.tp1, decision.sl, decision.tp2 already in canonical decision payload — VALIDATED via current decision_live.json shape
- service_state.json already exposes defense_tier, stress_direction, m30_bias, m30_bias_confirmed, atr_m30 — VALIDATED via current heartbeat
- Barbara directive: "se o threshold é o que temos no sistema, significa que está calibrado" — VALIDATED 2026-04-27

**PREMISES CREATED (new in this calibration):**
- vap_max_lifetime_min = 240 (4h) operational default — flagged for Barbara review (UNVERIFIED on Phase 0; will not affect correctness, only alert duration)
- MVP scope replaces PRICE_ACTION_STOP with VIRTUAL_SL — DOWNSTREAM IMPACT: PRICE_ACTION_STOP early-warning value lost; VIRTUAL_SL still alerts when stop reached

---

## PRAC

1. **Did I respect Barbara directive?** YES — inherited every existing threshold; only 1 operational parameter is new.
2. **Did I introduce magic numbers?** NO — each threshold cites `file:line` in production code or is the operational default (4h) flagged for review.
3. **Did I cherry-pick triggers?** Documented MVP scope amendment explicitly with reasoning per trigger.
4. **Did I overclaim calibration?** NO — Phase 0 is pure audit + documentation. Phase 2 backtest will produce empirical false-alarm/precision/recall numbers as a SECONDARY validation, not a calibration gate.
5. **Could I be missing a trigger that operator critically needs?** Possibly — PRICE_ACTION_STOP would alert before SL is reached. Trade-off: ship MVP fast (5 triggers, zero new thresholds) vs include PRICE_ACTION_STOP with a new calibrated threshold (extra ~2h Phase 0 work). Per Barbara directive, MVP wins.

---

## Sanity (Phase 0 time)

- HEAD `401e6da` (BIAS-DETECTION-PURDUE-CALIBRATION) unchanged on disk
- PID 16936 untouched (still running 401e6da)
- Capture services 8000 (PID 6376) + 8002 (PID 20396) LISTENING throughout
- 0 commits, 0 pushes, 0 restarts during Phase 0
- Phase 0 elapsed: ~30 min (vs 4-5h estimate, due to Barbara inheritance directive)

---

*End of Phase 0 calibration. Phase 1 implementation next: `live/macro_monitor.py` + telegram_notifier extension + run_live wiring.*

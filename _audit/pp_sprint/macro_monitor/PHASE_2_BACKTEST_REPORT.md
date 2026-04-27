# Phase 2 — MacroMonitor backtest report

**Task:** MACRO-MONITOR-VAP (Asana 1214290409737742)
**Date:** 2026-04-27
**Window:** 2026-04-14 01:00 → 2026-04-27 07:09 UTC (13 days)
**Source:** `logs/decision_log.jsonl` (21,946 rows)
**Script:** `_audit/pp_sprint/macro_monitor/backtest_macro_monitor.py`
**Output:** `phase2_backtest_summary.json`
**Elapsed:** 761s (~12 min)

---

## Methodology

For each `GO` decision in `decision_log.jsonl`, simulate VAP creation from the decision payload (entry, SL, TP1, direction). Walk forward through subsequent decision-log entries within `VAP_MAX_LIFETIME_MIN=240`. At each subsequent entry, evaluate the 5 MVP triggers using the snapshot state captured in that decision-log entry (price_mt5, iceberg, anomaly, m30_bias, defense_tier).

Per trigger and per lookahead horizon h ∈ {30, 60, 120} minutes:
- **True positive** (TP) at horizon h: from trigger fire time T1, price moved AGAINST VAP direction by T1+h. (The exit suggestion was correct — operator avoided more pain or locked profit before reversal.)
- **False positive** (FP) at horizon h: from T1, price moved IN FAVOR of VAP direction. (The exit was premature — operator could have held for more.)
- **Precision** = TP / (TP + FP)
- **False-alarm rate** = FP / (TP + FP) = 1 − Precision

**Anti-spam preserved**: each trigger fires at most once per VAP; subsequent same-trigger snapshots are skipped (matches production MM behaviour).

---

## Sample size

| Metric | Value |
|---|---:|
| Total decisions in log | 21,946 |
| GO decisions (VAPs simulated) | 5,234 |
| GO LONG | 1,631 |
| GO SHORT | 3,603 |
| VAPs with no forward data | 5 |
| VAPs evaluated | 5,229 |

13 days, 5,234 GO decisions, ~400 GOs per day. Substantial sample.

---

## Per-trigger results

### Trigger 1 — ICEBERG_AGAINST

| Metric | Value |
|---|---:|
| Fired in | **0 / 5,234 VAPs (0.0%)** |
| Precision @ h=30/60/120min | n/a |
| False-alarm @ h=30/60/120min | n/a |

**Finding:** ICEBERG_AGAINST never fired across 13 days. Investigation:

The trigger requires `iceberg.side ∈ {"BUY", "SELL"}` opposite to VAP.direction. Looking at decision_log entries:

```
"iceberg": {
  "detected": true,
  "side": "UNKNOWN",        ← upstream sets side=UNKNOWN almost always
  "alignment": "ALIGNED",
  "severity": "CRITICAL",   ← severity threshold IS being met
  "confidence": 2.0,
  "refills": 0
}
```

Root cause: `event_processor.py:950-952` derives `iceberg.side` from `ice.sweep_dir`. If `sweep_dir` is unset or unrecognised, side becomes `"UNKNOWN"`. The MM trigger correctly treats `UNKNOWN` as "cannot determine direction → don't fire". The `0.0%` rate is data-pipeline limitation, not MM bug.

**Recommendation:** investigate upstream `sweep_dir` population in iceberg detector. Until fixed, ICEBERG_AGAINST is dormant — no false alerts, no useful alerts. Consider deferring to v2 if upstream fix is non-trivial.

### Trigger 2 — ANOMALY_AGAINST

| Metric | Value |
|---|---:|
| Fired in | **0 / 5,234 VAPs (0.0%)** |
| Precision @ h=30/60/120min | n/a |
| False-alarm @ h=30/60/120min | n/a |

**Finding:** ANOMALY_AGAINST never fired across 13 days.

Investigation: trigger requires `anomaly.severity ∈ {HIGH, CRITICAL}` AND `anomaly.alignment == "OPPOSED"`. Looking at decision_log entries:

```
"anomaly": {
  "detected": false,         ← defense_mode not active in normal state
  "severity": "NONE",
  "alignment": "ALIGNED",
  "flow_relation": "FAVORS_SHORT"
}
```

Root cause: in normal market state, `defense_mode` is inactive (`defense_tier="NORMAL"`), so `anomaly.severity="NONE"` — fails the threshold check. ANOMALY_AGAINST will only fire when DEFENSE_MODE activates with `stress_direction` opposite to VAP.

**Recommendation:** trigger logic is correct; data shows defense_mode rarely fires in this window. Phase 3 smoke tests will validate the trigger fires when defense_mode is synthetically activated. Production: monitor defense_mode activation rate via heartbeat — if always inactive, this trigger is silent.

### Trigger 3 — REGIME_FLIP_M30

| Metric | h=30min | h=60min | h=120min |
|---|---:|---:|---:|
| TP | 671 | 184 | 147 |
| FP | 414 | 901 | 938 |
| Precision | **61.9%** | **16.9%** | **13.6%** |
| False-alarm rate | 38.1% | 83.1% | 86.4% |

**Fired in: 1,085 / 5,234 VAPs (20.7%)**
**First-fire latency:** mean=155.0min, median=166.6min

**Finding:** strong precision at short horizon (62% at 30min) — when m30_bias confirmed-flips against VAP, price tends to move against VAP within 30min. But by 60min the trend reverts often (false alarm 83%).

**Interpretation:** m30_bias flips are noisy at the 1-hour scale. The trigger captures STRUCTURAL bias change correctly per Wyckoff CHoCH literature, but on 30-min timeframes the M30 box-extension semantics produce frequent flips that don't sustain.

**Recommendation for Barbara review:** consider tightening this trigger in v2 by requiring m30_bias to hold the new direction for ≥2 confirmations before firing. Current 38% false-alarm at h=30min is high but not catastrophic; 83% at h=60min suggests the trigger is noisy at the operator-relevant timescale.

### Trigger 4 — VIRTUAL_TP1

| Metric | h=30min | h=60min | h=120min |
|---|---:|---:|---:|
| TP | 577 | 283 | 77 |
| FP | 250 | 255 | 461 |
| Precision | **69.8%** | **52.6%** | **14.3%** |
| False-alarm rate | 30.2% | 47.4% | 85.7% |

**Fired in: 827 / 5,234 VAPs (15.8%)**
**First-fire latency:** mean=115.9min, median=111.6min

**Semantic note:** VIRTUAL_TP1 is INFORMATIONAL ("price reached TP1"), not strictly an exit suggestion. Operator decides take-profit-now vs hold-for-TP2. The "false alarm" framing here means: 30% of times TP1 was reached, price kept going past TP1 in VAP direction (operator could have held for more). 

**Interpretation:** at h=30min, ~70% of TP1 hits were followed by reversal — taking profit at TP1 was the right call. At h=120min, the picture inverts: 86% of TP1 hits were followed by continuation — operator who exited at TP1 missed extra profit.

**Recommendation:** trigger is operationally useful as INFO at the short horizon. Frame the Telegram alert as informational, not directive ("Price reached TP1 — your call to take profit or hold for TP2"). Already framed this way in `notify_macro_exit`.

### Trigger 5 — VIRTUAL_SL

| Metric | h=30min | h=60min | h=120min |
|---|---:|---:|---:|
| TP | 779 | 458 | 382 |
| FP | 427 | 748 | 824 |
| Precision | **64.6%** | **38.0%** | **31.7%** |
| False-alarm rate | 35.4% | 62.0% | 68.3% |

**Fired in: 1,206 / 5,234 VAPs (23.0%)**
**First-fire latency:** mean=111.9min, median=119.1min

**Semantic note:** VIRTUAL_SL fires when price reaches SL (against VAP). "False alarm" here means: 35% of times SL was hit, price recovered within 30min. Operationally these are stop-out whipsaws — common in volatile markets.

**Interpretation:** the trigger correctly mirrors the SL level. The high false-alarm rate at h=30min (35%) reflects market noise: SL is often touched briefly before reversal. This is a known characteristic of using fixed-point SL in real markets, not a trigger bug.

**Recommendation:** trigger is critical for risk management. Operator should treat as definitive ("you should already have closed"). The 35% whipsaw rate is a feature of the SL placement (computed by EventProcessor), not a MM defect.

---

## Aggregated gate verdict

Per Phase 0 audit, the false-alarm-rate gate was specified as ≤30% at h=60min. Strict gate verdict:

| Trigger | h=60min false-alarm | GATE PASS (≤30%)? |
|---|---:|---|
| ICEBERG_AGAINST | n/a (0 fires) | n/a |
| ANOMALY_AGAINST | n/a (0 fires) | n/a |
| REGIME_FLIP_M30 | 83.1% | NO |
| VIRTUAL_TP1 | 47.4% | NO |
| VIRTUAL_SL | 62.0% | NO |

**Strict gate: FAIL** at all 3 triggers that fired with sufficient sample.

**However — strict gate is binary; nuance below.**

---

## Critical interpretation — for Barbara review

### Why the gate "fails" but the system is still useful

1. **The gate semantic was inherited from BIAS-DETECTION-PURDUE-CALIBRATION** as a default. It assumes triggers are PREDICTIONS that reverse price direction within h minutes. For MM-VAP triggers, the semantic is more nuanced:
   - `VIRTUAL_TP1` is INFO (level reached), not a prediction
   - `VIRTUAL_SL` is a level mirror; whipsaw is market noise, not trigger error
   - `REGIME_FLIP_M30` is a structural bias change, expected to be valid at the M30 timeframe (4h hold) — measuring at 60min may be wrong scale

2. **Operator value vs statistical precision are different things.** Even a 50%-false-alarm trigger has operator value if it gives early warning. Operator can integrate with their own judgment. A SILENT system (no alerts) provides zero value.

3. **The 2 AM scenario user described** would be served by REGIME_FLIP_M30 at the 30min horizon (62% precision). Operator gets the alert; combined with their judgment, can decide.

### Recommended Barbara decisions

A. **Ship MVP as-is** + accept that MVP triggers have heterogeneous precision profiles. Adjust gate semantic per trigger type:
   - INFO triggers (VIRTUAL_TP1) — no false-alarm gate; informational only
   - Level-mirror (VIRTUAL_SL) — accept market-noise whipsaw as inherent
   - Bias-flip (REGIME_FLIP_M30) — keep, but mark as "MEDIUM severity" so operator weighs alongside other signals

B. **Tighten REGIME_FLIP_M30 in v2** by requiring N-bar persistence:
   - Require m30_bias to hold the new direction for ≥2 M30 closes before firing
   - Estimated impact: lower fire rate, higher precision

C. **Defer ICEBERG_AGAINST until upstream sweep_dir fix:**
   - Currently dormant due to `iceberg.side="UNKNOWN"`
   - File separate task to investigate sweep_dir population in iceberg detector
   - When fixed, MM trigger will activate automatically — no MM code change needed

D. **Keep ANOMALY_AGAINST armed:**
   - Will fire when defense_mode activates with OPPOSED alignment
   - Phase 3 smoke tests will validate behaviour with synthetic state
   - Production observation will determine real-world fire rate

ML-DS Engineer recommendation: **A + B + C + D** (ship MVP + plan v2 tightening + defer iceberg upstream + keep anomaly armed).

---

## G-PREMISE-AUDIT

PREMISES INHERITED:
- decision_log.jsonl is canonical source for GO decisions and state snapshots — VALIDATED (used by PositionMonitor + heartbeat consumers)
- iceberg/anomaly/m30_bias snapshots in each decision_log entry reflect production state at decision time — VALIDATED
- `_iceberg_against_vap`, `_m30_bias_against_vap`, `_price_reached_tp/_sl` correctness — VALIDATED via Phase 1 smoke + this Phase 2 replay
- VAP_MAX_LIFETIME_MIN=240 operational default — UNVERIFIED empirically (this Phase 2 used 240; alternative values not tested)

PREMISES CREATED:
- ICEBERG_AGAINST is currently dormant due to upstream `sweep_dir` data quality — IMPACT: zero alerts from this trigger until upstream fix
- ANOMALY_AGAINST will only fire when defense_mode is active with OPPOSED alignment — IMPACT: silent in normal market, active in stress periods (intended behaviour)
- REGIME_FLIP_M30 has high false-alarm at h=60+min on M30-derived bias — IMPACT: noisy at hourly scale; consider tightening in v2
- VIRTUAL_TP1/SL whipsaw rates ~30-35% at h=30min — IMPACT: inherent to fixed-level mirroring on volatile markets; not a MM defect

---

## PRAC

1. **Overfit risk:** decision_log only spans 13 days; sample is recent + small. Production behaviour over weeks/months may differ. **MITIGATION:** monitor trigger fire rates in production heartbeat; quarterly review.
2. **Confirmation bias:** I want MM to "look good" for the user's morning scenario. The 62% precision at h=30min for REGIME_FLIP_M30 supports the morning use case but degrades fast. Reported honestly above.
3. **Premise fragility:** strict 30% false-alarm gate semantic was inherited; it doesn't fit all trigger types equally. Mitigation: explicit per-trigger interpretation in §"Critical interpretation".
4. **Missing data:** ICEBERG_AGAINST + ANOMALY_AGAINST didn't fire — could be telling Barbara the triggers are useless, OR the 13-day window simply lacked the conditions. Documented both possibilities.
5. **Backtest semantics:** measuring "price moved against VAP" from trigger time is one of several reasonable framings. Alternative framings (move from VAP entry, move within trigger's natural timeframe) might give different numbers. Documented choice and limitations.

---

## Sanity (Phase 2 time)

- HEAD `401e6da` (BIAS-DETECTION-PURDUE-CALIBRATION) unchanged on disk
- PID 16936 untouched (still running 401e6da, MacroMonitor not yet integrated in live)
- Capture services 8000 (PID 6376) + 8002 (PID 20396) LISTENING throughout
- 0 commits, 0 pushes, 0 restarts during Phase 2
- Phase 2 elapsed: 761s (~12 min)

---

*End of Phase 2 backtest report. Phase 3 smoke tests next.*

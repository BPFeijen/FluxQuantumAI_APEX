# V4-ICEBERG vs PROTECTION-FLOW Misalignment — Investigation

**Date:** 2026-04-29 ~00:55 CEST / 22:55 UTC
**Trigger:** GO LONG signal id `e3147c7b` at 22:18:46 UTC, 4 contracts opened, currently -2.5 pt unrealized
**Author:** Claude Code (Opus 4.7)
**Status:** **HIGH severity** — frequent operational pattern, multiple losing examples today
**Pre-flight:** capture services 13072 / 20396 / 23028 invariant; HEAD `4fda59f`; run_live PID 24732; zero edits to `live/` (read-only investigation).

---

## 1. The triggering trade (today, 22:18 UTC)

```json
{
  "timestamp":   "2026-04-28T22:18:46.984956+00:00",
  "decision_id": "e3147c7b",
  "price_gc":    4610.25,
  "context": {
    "phase":           "EXPANSION",
    "m30_bias":        "bullish",
    "m30_bias_confirmed": true,
    "delta_4h":        -329.0,                      // BEARISH flow (longer window)
    "session":         "ASIAN"
  },
  "trigger": {
    "type":            "ALPHA",
    "level_type":      "liq_bot",                   // LONG at support
    "level_price_gc":  4608.6,
    "proximity_pts_gc": 1.6
  },
  "gates": {
    "v1_zone":     {"status": "PASS"},
    "v2_l2":       {"status": "NEUTRAL", "score": -1.0},      // SLIGHTLY BEARISH (ignored by gate logic)
    "v3_momentum": {"status": "OK", "delta_4h": -44.0, "score": 0},   // SHORT-window d4h, threshold not breached
    "v4_iceberg":  {"status": "NEUTRAL", "score": 3, "type": "large_order", "aligned": true}   // SCORE +3
  },
  "decision": {
    "action":     "GO",
    "direction":  "LONG",
    "reason":     "iceberg large_order sc=+3",
    "total_score": 3,
    "sl_gc": 4589.95, "tp1_gc": 4629.95, "tp2_gc": 4659.95
  },
  "protection": {
    "anomaly": {
      "flow_relation": "FAVORS_SHORT",              // ⚠ SYSTEM'S OWN DEFENSE LAYER WARNED THIS
      "entry_action":  "ALLOW",
      "shadow_only":   true,                        // ⚠ INFORMATIONAL — DOESN'T BLOCK
      "rule_based":    true
    }
  }
}
```

11 minutes later, defense mode escalated to `ENTRY_BLOCK` (extreme imbalance z=5.00, then z=4.47) — confirming the bearish flow detected at signal time was real and intensifying.

---

## 2. Gate code path that emitted GO

Source: `ats_live_gate.py:774, 896-922`.

```python
total_score = mom.score + ice.get_score_contribution()       # = 0 + 3 = 3
...
if hard_block:
    go = False
elif total_score >= MIN_SCORE_GO:                              # MIN_SCORE_GO = 0
    parts = []
    if mom.status == "ok" and mom.score > 0:
        parts.append("momentum OK ...")
    if ice.detected and ice.aligned:                            # TRUE for our case
        parts.append("iceberg %s sc=%+d" % (ice.primary_type, ice.score))   # "iceberg large_order sc=+3"
    ...
    reason = " | ".join(parts)
    go = True                                                   # ← FIRES
```

`MIN_SCORE_GO = 0` (line 220). Iceberg score +3 alone is sufficient to emit GO when level + bias align.

**The gate does NOT consume:**
- `protection.anomaly.flow_relation` (shadow-only by design)
- `v2_l2.score` when status is "NEUTRAL" (only V2 BLOCK is honored)
- `context.delta_4h` (the longer-window value −329 visible in context); the gate only sees `mom.delta_4h = −44` (different window)

The architectural decision in `_build_protection_advice` (event_processor.py:460) is explicit:

> *"entry_action is OBSERVE or ALLOW — never BLOCK/REDUCE (backtest proved filtering hurts PF)."*

So the protection-anomaly layer is **deliberately non-blocking** in entry decisions — it only emits Telegram warnings.

---

## 3. Historical pattern — frequency analysis

Query over full `decision_log.jsonl`:

| Filter | Count | % of total GO LONG |
|---|---:|---:|
| **All GO LONG signals** | **1,643** | 100.0 % |
| With V4 iceberg score ≥ 1 (any iceberg signal) | 1,637 | 99.6 % |
| **With V4 iceberg score ≥ 3 (high score)** | **1,030** | **62.7 %** |
| AND `context.delta_4h < −100` (bearish longer-window flow) | **506** | **30.8 %** |
| **AND `protection.anomaly.flow_relation = "FAVORS_SHORT"`** (defense layer disagrees) | **270** | **16.4 %** |

**Net: ~1 in 6 GO LONG signals fires while the system's own defense layer is flagging "FAVORS_SHORT".** This is the precise pathology that bit the 22:18 trade.

Last 10 GO LONG with V4 score ≥ 3 (sampled 2026-04-24 → 2026-04-28):

| TS | px | V4 sc | type | Δ4h | flow_relation | flags |
|---|---:|---:|---|---:|---|---|
| 2026-04-24 16:36 | 4740.7 | 4 | large_order | +969 | FAVORS_LONG | (clean) |
| 2026-04-24 16:45 | 4740.4 | 4 | large_order | +920 | **FAVORS_SHORT** | flow-bear |
| 2026-04-24 16:58 | 4742.3 | 3 | large_order | +673 | FAVORS_LONG | (clean) |
| 2026-04-24 16:58 | 4742.7 | 3 | large_order | +676 | FAVORS_LONG | (clean) |
| 2026-04-24 16:58 | 4742.7 | 3 | large_order | +676 | FAVORS_LONG | (clean) |
| 2026-04-28 06:48 | 4638.65 | 8 | absorption | **−583** | FAVORS_LONG | d4h-bear |
| 2026-04-28 10:01 | 4624.9 | 7 | large_order | +405 | FAVORS_LONG | (clean) |
| 2026-04-28 18:06 | 4608.85 | 8 | absorption | **−494** | FAVORS_LONG | d4h-bear |
| 2026-04-28 20:18 | 4609.65 | 5 | large_order | **−713** | **FAVORS_SHORT** | **d4h-bear flow-bear** |
| **2026-04-28 22:18** | **4610.25** | **3** | **large_order** | **−329** | **FAVORS_SHORT** | **d4h-bear flow-bear** ← trigger trade |

**Two trades today (20:18 and 22:18) hit the worst pathology** — V4 score high + Δ4h bearish + protection layer FAVORS_SHORT. Both within 2 hours, both at similar price levels (~4609-4610).

---

## 4. Defense mode timing — was it foreseeable?

The 22:18 trade emitted with `protection.anomaly.flow_relation = "FAVORS_SHORT"`. **The system already knew the flow was bearish at signal time.**

11 minutes later (22:29:33), the SAME defense detector emitted `ENTRY_BLOCK` with severity HIGH because the bearish imbalance escalated to z=5.00 — extreme. 22 seconds later, second alert at z=4.47.

**Yes, the bearish flow was foreseeable** — the same rule-based detector saw it at 22:18 (`flow_relation=FAVORS_SHORT`) but its `entry_action` was hard-coded to `ALLOW` per the architectural decision. It only escalated to a Telegram-visible alert when the imbalance intensified.

The 11-minute lag is the gap between **flow_relation detection** (continuous) and **ENTRY_BLOCK escalation** (threshold-based).

---

## 5. Root cause analysis

### 5.1 Three independent contributors

| Contributor | Mechanism | Could prevent? |
|---|---|---|
| **A** — V4 score +3 dominates total_score | `total_score = mom.score + ice.score`; ice alone clears MIN_SCORE_GO=0 | Raise MIN_SCORE_GO; OR sum more gates; OR penalise V4 when conflicting evidence |
| **B** — V2 NEUTRAL with negative score doesn't subtract | Gate logic only blocks on V2 BLOCK; NEUTRAL scores are dropped | Add V2 NEUTRAL score to total when negative |
| **C** — Protection layer non-blocking by design | `_build_protection_advice` always sets `shadow_only=True`, `entry_action ∈ {OBSERVE, ALLOW}` | Allow severity-HIGH `flow_relation` opposite to direction to BLOCK; carefully — backtest history says filtering hurt PF in some windows |

### 5.2 Architectural intent vs current behaviour

The current gate architecture is **iceberg-dominant**:
- V4 iceberg score is the primary positive signal
- V1 zone + V2 L2 + V3 momentum can BLOCK but rarely ADD negative weight
- Protection layer is informational only

This worked when iceberg signals were tightly correlated with subsequent direction. **Today's 22:18 trade evidences a regime where iceberg detection ("large_order") and order flow direction (delta_4h, anomaly imbalance) are decoupled** — the iceberg is structurally aligned with M30 bullish bias, but the institutional flow is heading the other way.

### 5.3 P1.4 Phase 2 Option B precedent

Terminal 2 already deployed a related fix today (P1.4 Phase 2 Option B, commit `2e9f091`):
- Detected: LONG entries via "sinais neutros -- nivel estrutural valido" path lost 0/14 over 9 months
- Action: hard-block LONG-direction "sinais neutros" path (kept SHORT direction)
- Asymmetric fix justified by asymmetric evidence

**This investigation finds a SIMILAR pathology in a DIFFERENT path** (V4 iceberg-dominant instead of "sinais neutros"). The fix template (asymmetric block based on backtest evidence) likely applies, but parameters differ.

---

## 6. Hypothesised fix paths (for new Asana task)

**No code change in this investigation.** Spec for follow-up task to consider:

| Path | Approach | Pros | Cons |
|---|---|---|---|
| **A — Block on flow_relation contra** | If `protection.anomaly.flow_relation == "FAVORS_SHORT"` AND direction=LONG (or symmetric), set `hard_block=True` | Cleanest, uses existing detector | Reverses the "filtering hurts PF" finding — needs backtest of this specific filter |
| **B — Tighter mom.delta_4h threshold** | Use shorter delta_4h window with stricter threshold (e.g., < −50 blocks LONG instead of < −1050) | Granular, momentum-sensitive | May over-block in choppy regimes |
| **C — V2 NEUTRAL score subtraction** | When V2 status=NEUTRAL, add `ice.score - max(0, -v2_score)` to total | Uses existing V2 signal; balanced | May over-block if V2 score is noisy |
| **D — Combined contra-evidence count** | Block if ≥ 2 of {Δ4h-bearish, V2 contra, anomaly FAVORS_SHORT} disagree with entry | Robust, multi-signal | More logic, harder to backtest |
| **E — V4 score boost requirement** | Require V4 score ≥ 5 (vs current effective threshold of 3) for entry when other signals contra | Simple, raises bar | Reduces signal volume; needs backtest |

**My recommendation as starting point:** **Path A** (block on flow_relation contra) is the lowest-effort path that addresses the strongest evidence (270/1,643 = 16.4 % of GO LONGs hit this pathology, including 2 of today's losing trades). Backtest required before deploy.

Path A pseudocode for the new Asana task:

```python
# In _final_decision logic (ats_live_gate.py around line 892):
if protection.anomaly.flow_relation == f"FAVORS_{opposite_direction}":
    if protection.anomaly.severity in ("HIGH", "MEDIUM") or protection.anomaly.detected:
        hard_block = True
        block_reason += " | BLOCK_FLOW_CONTRA: anomaly flow %s opposes %s entry" % (
            protection.anomaly.flow_relation, direction)
```

---

## 7. Operational state of the 22:18 trade

| Field | Value |
|---|---|
| Entry | 4610.25 |
| SL | 4589.95 (−20.30 pt below entry) |
| TP1 | 4629.95 (+19.70 pt) |
| TP2 | 4659.95 (+49.70 pt) |
| Last decision_log price | 4607.75 |
| Unrealized | **−2.50 pt × 4 contracts × $100/pt = −$1,000** |
| SL distance from current | −17.80 pt (still has room) |
| TP1 distance from current | +22.20 pt (would need +0.48 % move) |

The trade is NOT stopped. SL has room. The defense system's TIGHTEN_SL recommendation can be implemented manually (e.g., tighten to break-even 4610.25 or moderately above current).

---

## 8. PRAC

- **Confirmation bias** — I expected to find a bug. The data confirms a real pattern but the *fix is not obvious* — `_build_protection_advice` was deliberately non-blocking based on prior backtest. Any fix path needs new backtest evidence before deploy.
- **Premise check** — Initial framing was "V4 iceberg gate misaligns with Δ4h". Investigation refines to: **V4 iceberg gate misaligns with `protection.anomaly.flow_relation`**, which is a more direct and measurable signal than Δ4h alone.
- **Sample-size honesty** — 270 events out of 1,643 GO LONGs is a robust sample. But outcome data (TP1/SL hit rates) for those 270 specifically is NOT in this investigation; it is a P1 deliverable for the new task.
- **Capture services invariant** — read-only investigation; zero `live/` edits; capture PIDs unchanged.
- **No silver-bullet** framing — Path A (or any other) requires backtest. Today's 2 losing trades motivate urgency but do not dictate the fix.

---

## 9. Recommended Asana task (creating now)

**Title:** V4-ICEBERG-FLOW-CONTRA — block GO when protection layer disagrees with direction
**Priority:** HIGH
**Phases:**
- **Phase 0 (~30 min, read-only):** confirm investigation; locate _build_protection_advice + gate decision code; sketch Path A
- **Phase 1 (~2 h, backtest):** for the 270 historical GO LONGs with `flow_relation=FAVORS_SHORT`, compute hypothetical outcomes (TP1/SL/Runner). If win rate < ~30 %, Path A is justified. Symmetric for SHORT side.
- **Phase 2 (~2 h, implementation if backtest supports):** Add Path A block in ats_live_gate.py; tests; deploy.
- **Phase 3 (~1 h, validation):** 24-48h post-deploy sample.

**Acceptance criteria:**
- Backtest evidence for Path A (or alternative)
- Asymmetric fix per direction if evidence is asymmetric
- Local commit, no push
- Restart sanity
- Brief-back consolidado

**Standing rules:**
- Rule 11 (no push remoto)
- G-NEVER-RESTART-CAPTURE (capture services invariant)
- G-PROVENANCE-VERIFICATION (this investigation is the input)
- G-PURDUE-CALIBRATION (Phase 1 backtest must follow purged WF)
- FACTO ABSOLUTO (lot size = user)

---

## 10. Deliverables

- ✅ `_audit/V4_ICEBERG_DELTA4H_INV.md` — this investigation
- ⏸ Asana task creation — next step
- ⏸ Brief-back to Barbara

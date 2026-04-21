# CLAUDECODE PROMPT — DIAGNOSIS: Sprint C BUG #1 — M30 Bias Stuck (Live-Impacting)

**Sprint ID:** `sprint_diagnosis_m30_bias_stuck_20260421`
**Authorization:** Barbara ratified 2026-04-21 (same session as INVEST-03 running)
**Type:** **READ-ONLY diagnosis** — zero code edits, zero config edits, zero service touches
**Duration target:** 15-30 min
**Output:** `C:\FluxQuantumAI\sprints\sprint_diagnosis_m30_bias_stuck_20260421\DIAGNOSIS_REPORT.md`
**Priority:** URGENT — bug ACTIVE in production ≥100 hours, currently costing Barbara trading opportunities
**Parallel to:** INVEST-03 (V4 iceberg) currently running — different data slices, zero conflict

---

## 0. Critical context — ClaudeCode does NOT have memory of this bug

Read carefully before proceeding. This bug was identified 2026-04-20 by a prior ClaudeCode session but fix was never deployed (paused for Strategy Layer architectural work).

### 0.1 Bug summary (as documented 2026-04-20)

**Sprint C BUG #1** — `derive_m30_bias` in `C:\FluxQuantumAI\live\level_detector.py` (around line 241, may have shifted) contains an **equality comparison bug**: uses `>` where should be `>=` (or vice versa). Specifically:

- When `liq_top == box_high` in the M30 parquet row, function returns `bearish` as default
- **44.7% of parquet M30 rows** have `liq_top == box_high` exactly (due to how box boundaries are computed)
- Result: function returns bearish stuck in ~45% of evaluations

### 0.2 Live symptoms — two opposite manifestations already observed

**Symptom A (2026-04-20 morning):**
- Live decision_log showed **69 SHORTs 0 LONGs in 8 hours** during a clear uptrend
- 3 H4 candles bullish on chart, system stuck emitting SHORTs
- Violated literature directly (ATS: "H4 is the authority. No exceptions.")
- Memory tag: "90.8% decisões hoje bearish stuck, 78% distribution bearish, 91h stuck desde 16 Abril"
- Report previously written: `C:\FluxQuantumAI\sprints\<find_by_name>\BIAS_STUCK_INVESTIGATION.md`

**Symptom B (2026-04-21 04:00-05:43 UTC, CURRENT, Barbara reporting LIVE):**
- XAUUSD M30 clear bearish move: **4828 → 4793 (-35 pts in ~2 hours)**
- RSI(14) = 36.18 confirming bearish
- M30 clearly broke below accumulation range
- **System emitted ZERO SHORT signals** despite textbook-trending-down conditions
- Barbara's quote: "1h até este momento bearish e não teve um sinal de SHORT?"

### 0.3 Why Symptom B despite bearish bias stuck

Hypotheses (ClaudeCode must test):
- **H1:** Bias still stuck bearish (same bug as A) but a separate gate is blocking SHORT triggers now (iceberg counter-trend? proximity? something else that shifted?)
- **H2:** Bias unstuck somehow but new bug is blocking entry direction resolution
- **H3:** M30 bias is correctly bearish but entry trigger logic requires conditions that aren't firing (e.g., price near liq_bot + absorption signal + X other)
- **H4:** Something changed between 2026-04-20 morning (69 SHORTs) and 2026-04-21 04:00 (0 SHORTs) — config? restart? service health?
- **H5:** Other

**This diagnosis identifies which hypothesis is correct. Does NOT apply fix.** Fix is separate sprint after Barbara ratifies.

### 0.4 Sprint C v2 state (to help you orient)

- Commit `074a482` (Sprint C Phase 2a) **LIVE** — added `derive_h4_bias` and 8 CALIBRATION_TBD constants in `level_detector.py`
- Commit `acecefe` — backlog entry
- **Phase 2b (fix for `derive_m30_bias` equality bug) was PLANNED but NEVER WRITTEN.** The bug this sprint diagnoses is in the legacy `derive_m30_bias` function, NOT in the new `derive_h4_bias` added in 074a482.
- Sprint C paused pending Strategy Layer refactoring (Design Doc Nível 1 v3)

---

## 1. Hard Limits

1. **READ-ONLY.** Writes only to sprint output directory.
2. **Do NOT fix anything.** This is diagnosis.
3. **Do NOT touch** `live/`, `config/`, `data/`, or service.
4. **Capture PIDs 2512/8248/11740 + service PID 4516 untouched.**
5. **No restart.**
6. **INVEST-03 is running in a different session** on the same server. Both READ-ONLY but:
   - INVEST-03 reads `C:\data\iceberg\*.jsonl` + `gc_ats_features_v5.parquet` + `calibration_dataset_full.parquet`
   - This diagnosis reads `decision_log.jsonl` + `settings.json` + `level_detector.py` (read only)
   - Disjoint read sets — zero interference. Both can run concurrently.

---

## 2. Investigation scope

### 2.1 Q1 — Locate `derive_m30_bias` in current code

```powershell
Select-String -Path "C:\FluxQuantumAI\live\level_detector.py" -Pattern "derive_m30_bias|def _get_m30_bias|m30_bias" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }
```

Report:
- Current file size + last-modified date
- Line number where `derive_m30_bias` (or equivalent) is defined
- Full function body (verbatim, ±50 lines)
- Identify the equality comparison suspected of bug

### 2.2 Q2 — Live state of M30 bias RIGHT NOW

Read recent `decision_log.jsonl` entries (last 4 hours minimum, more if possible) from `C:\FluxQuantumAI\logs\decision_log.jsonl`:

For each entry, extract:
- `timestamp`
- `context.m30_bias`
- `context.m30_bias_confirmed` (if exists)
- `context.liq_top`, `context.liq_bot`, `context.m30_box_high`, `context.m30_box_low` (if present)
- `context.daily_trend`
- `decision.action`, `decision.direction`, `decision.reason`

Aggregate:
- Distribution of `m30_bias` values in last 4h (counts: bullish / bearish / neutral)
- % of rows where `liq_top == m30_box_high` (the bug trigger condition)
- % of rows where `m30_bias == "bearish"` — compare to live market direction
- Ratio of GO LONG vs GO SHORT in last 4h

### 2.3 Q3 — Barbara's specific time window: 2026-04-21 04:00-05:43 UTC

**CRITICAL COMPARISON:** Barbara reports chart shows clear bearish move, zero SHORTs emitted.

Filter `decision_log.jsonl` to entries between 2026-04-21 04:00:00 UTC and 2026-04-21 05:43:00 UTC.

Report:
- Total entries in window
- Count per `decision.action` (GO / SKIP / NO_ENTRY / etc.)
- Count per `decision.direction`
- **For SKIP/NO_ENTRY entries with direction intent SHORT: what `decision.reason` is logged?**
- **If NO SHORT attempts at all: what are the `decision.reason` strings?**
- **For any GO signals (LONG or SHORT): broker result (EXEC_OK / EXEC_FAILED / other)**

### 2.4 Q4 — Hypothesis H4 — Did something change?

Compare two snapshots:
- `settings.json` current hash vs `BA0166FF...A42D52` (pre-Bloco1 backup baseline from 2026-04-20)
- Service PID 4516 start time vs last known (00:47 UTC 2026-04-20 per previous pre-flight)

If:
- Hashes identical + PID 4516 same → config + service unchanged since yesterday morning
- Hash different → something changed; report the diff
- PID different → service restarted; report new PID + start time

### 2.5 Q5 — Hypothesis H1 test — bias-stuck vs separate-gate

For entries in §2.3 window that show `m30_bias=bearish` AND `decision.action != GO SHORT`:

Categorize by `decision.reason`:
- "M30_BIAS_BLOCK" → bias correctly bearish, SOMETHING ELSE blocking SHORT trigger
- "no signal" / "NO_ENTRY" → entry logic not firing (proximity? trigger?)
- iceberg-related reason → V4 iceberg gate blocking
- other → categorize

Aggregate counts per reason category.

### 2.6 Q6 — Check `liq_top == m30_box_high` rate in current live state

From recent decision_log entries:
- Count entries where `context.m30_box_high == context.liq_top` (exact equality)
- Count entries where `context.m30_box_low == context.liq_bot`
- Ratio vs total

This validates whether the 44.7% equality rate identified 2026-04-20 is still present.

---

## 3. Expected output structure

`DIAGNOSIS_REPORT.md`:

```markdown
# Sprint C BUG #1 Live Diagnosis — 2026-04-21

## 1. Current code state of derive_m30_bias (§2.1)
<verbatim function + suspected bug location>

## 2. Live M30 bias distribution last 4h (§2.2)
<counts + equality rate>

## 3. Barbara's reported window 04:00-05:43 UTC (§2.3) — critical
<per-entry breakdown or representative sample>
<decision.reason distribution for SKIP/NO_ENTRY>

## 4. Config + service change check (§2.4)
<hash comparison, PID comparison>

## 5. Hypothesis test results (§2.5)
<H1/H2/H3/H4/H5 with supporting evidence from data>

## 6. Equality rate current vs documented (§2.6)
<44.7% historical vs current rate>

## 7. ROOT CAUSE CLASSIFICATION
One of:
- RC-A: BUG #1 still active, same stuck-bearish manifestation (but gate X blocks SHORT)
- RC-B: BUG #1 fixed somehow but new bug in direction resolution
- RC-C: BUG #1 active, stuck direction CHANGED (bullish now?), blocking opposite signals
- RC-D: Config/service changed since 2026-04-20 — identify what
- RC-E: BUG #1 is not the cause; different bug identified in diagnosis
- RC-F: Other (describe)

## 8. Recommendation for fix sprint
<3-5 lines: what the fix sprint should target based on root cause>

## 9. System state
<zero writes confirmed, capture PIDs intact, no service touches>
```

---

## 4. Important — do NOT propose fix in this sprint

Explicitly: fix is **out of scope**. This sprint diagnoses. Barbara + Claude write fix sprint after ratification of root cause.

The fix for Sprint C BUG #1 is sensitive because:
- The original Phase 2b was paused pending Strategy Layer resolution
- Changing `derive_m30_bias` logic may affect Sprint C v2 Phase 2a `derive_h4_bias` already deployed
- Any M30 bias change can cascade through entry gates differently than expected

Fix sprint requires separate design doc per Golden Rule FluxQuantumAI.

---

## 5. Time budget

- Discovery + code reading (§2.1): 5 min
- Log analysis (§2.2-2.3-2.5-2.6): 10 min
- Config/service check (§2.4): 2 min
- Report writing (§3): 10 min
- **Total: 25-30 min. Stop and ask if exceeds 45 min.**

---

## 6. Communication

Upon completion:
- Post to Barbara: §7 ROOT CAUSE classification + §8 recommendation (3-5 lines)
- Flag any surprises (new bugs discovered, config drifts, etc.)
- Confirm INVEST-03 untouched (it continues in parallel session)

**Begin now. This is live-impacting; Barbara is losing trades while bug is active.**

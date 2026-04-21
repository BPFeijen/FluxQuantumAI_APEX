# CLAUDECODE PROMPT — CLEAN-UP 2: REGIME_FLIP Investigation (READ-ONLY)

**Sprint ID:** `sprint_cleanup_2_regime_flip_investigation_20260420`
**Authorization:** Barbara ratified 2026-04-20
**Type:** **READ-ONLY forensic investigation** — zero code, zero config, zero restarts
**Duration target:** 45-90 minutes
**Output:** 1 file (`C:\FluxQuantumAI\sprints\sprint_cleanup_2_regime_flip_investigation_20260420\REGIME_FLIP_FORENSIC.md`)
**Priority:** **HIGHEST** — blocks all calibration work

---

## 0. Why

Cross-ref report (§3.4 / §17) identified that `REGIME_FLIP_SHORT = -1000` exists in `run_live.py:156`. During 2026-04-20 14:09-14:56 UTC incident:

- Market: rally 4816 → 4847 → drop -25pts in 40min
- `delta_4h = -1133` during GO LONG signals (stronger than the -1000 flip threshold)
- 8 LONG signals emitted despite `delta_4h` being well below `REGIME_FLIP_SHORT` threshold

**Question: did REGIME_FLIP_SHORT trigger at all during the incident? If yes, what action did it take? If no, why not?**

Before calibrating anything new, we must understand what the EXISTING regime-flip logic does (or fails to do) in production. Calibrating on top of broken plumbing is wasted work.

---

## 1. Hard Limits

1. **READ-ONLY.** No edits to code, config, logs, data. No service touches.
2. **No hypothesis code.** No new scripts that modify production. Analysis scripts writing only to the sprint directory are OK.
3. **No assumptions.** Read code and logs; quote evidence verbatim.
4. **Stop and report** if investigation reveals anything requiring immediate action (e.g., discovering REGIME_FLIP is reading wrong variable). Do NOT fix unilaterally.

---

## 2. Investigation scope — 4 questions to answer

### Q1 — Where is `REGIME_FLIP_SHORT = -1000` defined and used?

- Locate the constant definition (`run_live.py:156` per cross-ref)
- Report the surrounding code (±30 lines) — docstring, intent
- Find all call sites — grep for `REGIME_FLIP_SHORT`, `REGIME_FLIP`, `regime_flip` (case-insensitive) across `live/`, `run_live.py`, `ats_*.py`, `grenadier_guardrail.py`, `live/position_monitor.py`
- For each call site: report file:line + surrounding logic (±15 lines)
- Answer: **what decision does this threshold gate?** (entry block, position exit, regime state change, telemetry only, other?)

### Q2 — Is there a symmetric `REGIME_FLIP_LONG` for the long side?

- Grep for equivalent threshold for LONG regime flip
- If exists: same analysis as Q1
- If not exists: **flag asymmetric protection**

### Q3 — During 2026-04-20 14:09-14:56 UTC incident, was REGIME_FLIP evaluated?

Using decision_log + service stdout/stderr from the forensic snapshot already captured:

- `C:\FluxQuantumAI\sprints\INCIDENT_20260420_LONG_DURING_DROP\20260420_145845\decision_log_last60min.jsonl`
- `C:\FluxQuantumAI\sprints\INCIDENT_20260420_LONG_DURING_DROP\20260420_145845\service_stdout_tail2000.log`
- `C:\FluxQuantumAI\sprints\INCIDENT_20260420_LONG_DURING_DROP\20260420_145845\service_stderr_tail2000.log`

Search each for:
- `REGIME_FLIP`, `regime_flip`, `regime flip`, `REGIME FLIP`
- `flip`, `exit_signal`, `position_exit`, `defensive_exit`

Report:
- Did REGIME_FLIP_SHORT logic fire? Evidence verbatim.
- If it fired but had no action: why? (log_only mode? shadow? disabled flag? fell through conditional?)
- If it did NOT fire: why? (never evaluated in that code path? condition not met despite delta_4h=-1133? feature flag disabled?)

### Q4 — Was there an open LONG position for REGIME_FLIP to act on?

- Per INITIAL_ANALYSIS.md §3: 8 GO LONG signals, ALL `EXEC_FAILED` at broker. So **the system never had an open position to flip out of.**
- Barbara executed MANUALLY based on those signals.
- Question: does REGIME_FLIP_SHORT apply ONLY to positions the system owns (via `position_monitor.py`), or does it influence any entry gate?
- If exit-only: the protection was structurally absent for manual trades, regardless of threshold value.
- Report the answer with code evidence.

---

## 3. Secondary investigation — context of the 3 overlapping delta_4h thresholds

Cross-ref §3.4/§17 identified 3 overlapping thresholds:

| # | Name | Value | File:line | Purpose (from inventory) |
|---|---|---|---|---|
| 2 | `delta_4h_long_block` | -1050 | `settings.json:3` | Block NEW long entry when delta_4h < -1050 |
| 8 | `trend_resumption_threshold_short` | -800 | `settings.json:12` | Confirm short trend resumption |
| 157 | `REGIME_FLIP_SHORT` | -1000 | `run_live.py:156` | Regime flip signal |

During the incident (delta_4h=-1133):
- `delta_4h < -1050` → entry should have been blocked. **Was it blocked or allowed?** Check decision_log: any `DELTA_4H_BLOCK` or similar log lines 14:09-14:56?
- `delta_4h < -1000` → regime flip should have fired. Was it?
- `delta_4h < -800` → trend resumption short should have confirmed. Was it? (Relevance: conflicts with system emitting LONG signals.)

For each of the 3 thresholds, answer:
- Was the threshold evaluated during the 8 GO LONG events?
- If yes, what action resulted?
- If no, why was the code path skipped?

---

## 4. Deliverable

`REGIME_FLIP_FORENSIC.md` with structure:

```markdown
# REGIME_FLIP Forensic Investigation — 2026-04-20

## 1. REGIME_FLIP_SHORT definition and usage
<Q1 evidence>

## 2. LONG-side symmetry
<Q2 evidence>

## 3. Evaluation trace during incident 14:09-14:56 UTC
<Q3 evidence — logs, quotes, decision flow>

## 4. Position scope (exit vs entry)
<Q4 evidence>

## 5. The 3 overlapping delta_4h thresholds — actual behavior during incident
<§3 table with per-threshold actual fire/skip/action>

## 6. Root cause analysis
<one of:>
- **RCA-A:** Threshold exists, logic fired, but action was log-only/shadow → needs enforce flip
- **RCA-B:** Threshold exists but code path never reached for entry gate (exit-only) → architectural gap, not threshold issue
- **RCA-C:** Threshold exists, fired, blocked correctly → but ANOTHER code path allowed the LONG anyway (overextension reversal? iceberg alignment? broker retry?)
- **RCA-D:** Threshold misconfigured (e.g., reads wrong variable, unit mismatch, sign flipped)
- **RCA-E:** Other (describe)

## 7. Implications for calibration sprint
<If RCA-A: enable infra before calibrate>
<If RCA-B: new sprint to extend scope — NOT calibration>
<If RCA-C: identify competing code path — may be the actual bug>
<If RCA-D: fix config — NOT calibration>

## 8. Recommendation
<3-5 lines: what the next sprint should actually be>

## 9. System state during investigation
<ZERO writes confirmed>
```

---

## 5. Search tooling recommendations

Speed-optimal:

```powershell
# Q1 — find REGIME_FLIP_SHORT definition + usage
Select-String -Path "C:\FluxQuantumAI\*.py","C:\FluxQuantumAI\live\*.py" `
    -Pattern "REGIME_FLIP" -CaseSensitive:$false |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }

# Q2 — symmetric long-side
Select-String -Path "C:\FluxQuantumAI\*.py","C:\FluxQuantumAI\live\*.py" `
    -Pattern "REGIME_FLIP_LONG|regime.*long.*flip" -CaseSensitive:$false

# Q3 — incident logs
Select-String -Path "C:\FluxQuantumAI\sprints\INCIDENT_20260420_LONG_DURING_DROP\20260420_145845\*.log", `
                    "C:\FluxQuantumAI\sprints\INCIDENT_20260420_LONG_DURING_DROP\20260420_145845\*.jsonl" `
    -Pattern "REGIME_FLIP|regime_flip|regime flip|flip|exit_signal|defensive_exit|DELTA_4H_BLOCK" -CaseSensitive:$false
```

---

## 6. Communication

- Produce `REGIME_FLIP_FORENSIC.md`
- Post to Barbara: §6 RCA classification + §8 recommendation (3-5 lines)
- Do NOT propose fixes beyond recommendation classification. Barbara decides next sprint scope.

**Begin when Barbara gives green light. Report results.**

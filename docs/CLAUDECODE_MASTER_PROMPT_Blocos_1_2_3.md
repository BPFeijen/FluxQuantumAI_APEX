# CLAUDECODE PROMPT MESTRE — Blocos 1+2+3
## Emergency Disable + CAL-03 Investigation + Dependency Scoping

**Sprint IDs:**
- Bloco 1: `sprint_emergency_disable_20260420`
- Bloco 2: `sprint_cleanup_2_5_cal03_context_20260420`
- Bloco 3: `sprint_dependency_scoping_20260420`

**Authorization:** Barbara ratified 2026-04-20 after Design Doc Nível 1 v3 sign-off

**Type:** MIXED — Bloco 1 modifies production (requires backup + py_compile + commit); Blocos 2 + 3 READ-ONLY

**Total time budget:** 4-6h sequential

**Outputs directory:** `C:\FluxQuantumAI\sprints\<sprint_id>\`

**CHECKPOINTS (MANDATORY):** After each Bloco complete, STOP and wait Barbara approval before next Bloco. Do NOT chain blocos without ratification.

**Golden Rule compliance:**
- No code without approved design doc (Design Doc Nível 1 v3 ratified 2026-04-20)
- Backups + py_compile + import probe before every edit
- Commit with hash + TASK_CLOSEOUT_REPORT after each Bloco

---

## 0. Pre-Flight (before Bloco 1 starts)

### 0.1 Verify system state

```powershell
# Capture PIDs must be intact
Get-Process -Id 2512,8248,11740 -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime

# Service PID
Get-Process -Id 4516 -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime

# Git state
cd C:\FluxQuantumAI
git log --oneline -5
git status
```

**Abort and ask Barbara if:**
- Any capture PID not running (2512, 8248, 11740)
- Service PID different from 4516 (may have restarted during session)
- Uncommitted changes in working tree

### 0.2 Create sprint directories

```powershell
mkdir C:\FluxQuantumAI\sprints\sprint_emergency_disable_20260420\backup_pre_bloco1
mkdir C:\FluxQuantumAI\sprints\sprint_cleanup_2_5_cal03_context_20260420
mkdir C:\FluxQuantumAI\sprints\sprint_dependency_scoping_20260420
```

### 0.3 Backup

Copy full working tree snapshot of files to be modified in Bloco 1:

```powershell
$backup = "C:\FluxQuantumAI\sprints\sprint_emergency_disable_20260420\backup_pre_bloco1"
Copy-Item C:\FluxQuantumAI\config\settings.json $backup\
Copy-Item C:\FluxQuantumAI\live\event_processor.py $backup\
Copy-Item C:\FluxQuantumAI\live\level_detector.py $backup\
Copy-Item C:\FluxQuantumAI\live\ats_live_gate.py $backup\

# Hash manifest
Get-ChildItem $backup | ForEach-Object {
    "$($_.Name): $((Get-FileHash $_.FullName).Hash)"
} | Out-File $backup\MANIFEST.txt
```

Report manifest contents before any edit.

---

## 1. BLOCO 1 — Emergency Disable (~45 min)

**Purpose:** Remove the 3 rules that caused 2026-04-20 incident. Activate 1 existing protection. All changes are either value flips in config or isolated feature-flag additions — low complexity, reversible via backup.

### 1.1 Action A — `delta_4h_inverted_fix` → `false`

**What:** set `settings.json` key `delta_4h_inverted_fix` from `true` to `false`
**Why:** Literature-validated (Wyckoff Villahermosa, ATS Basic Strategy 1) + CAL-03 finding documented only +d4h side + 2026-04-20 incident shows -d4h extrapolation is bearish-context-wrong. Per Design Doc v3 §8, this rule becomes condition inside Range Reversal playbook only. CLEAN-UP 2.5 (Bloco 2) will confirm whether to re-enable within Range Reversal calibration.
**How:**
```powershell
# Read current value
Select-String -Path C:\FluxQuantumAI\config\settings.json -Pattern "delta_4h_inverted_fix"

# Modify: direct edit settings.json changing true to false for this key only
# Use Python to preserve JSON formatting exactly:
python -c @"
import json
with open('C:/FluxQuantumAI/config/settings.json', 'r') as f:
    cfg = json.load(f)
assert cfg['delta_4h_inverted_fix'] == True, 'Expected current value to be True'
cfg['delta_4h_inverted_fix'] = False
with open('C:/FluxQuantumAI/config/settings.json', 'w') as f:
    json.dump(cfg, f, indent=2)
print('delta_4h_inverted_fix set to False')
"@
```
**Verify:**
```powershell
Select-String -Path C:\FluxQuantumAI\config\settings.json -Pattern "delta_4h_inverted_fix"
# Expected: "delta_4h_inverted_fix": false
```
**Rollback:** `Copy-Item $backup\settings.json C:\FluxQuantumAI\config\settings.json`

### 1.2 Action B — Overextension reversal OFF in TRENDING modes

**What:** Add feature flag `overextension_reversal_enabled` (default `false`) to settings.json. Gate the OVEREXTENSION branch in `event_processor._resolve_direction` with this flag.
**Why:** Design Doc v3 §5.0 matrix excludes counter-trend reversal from Phase E. Q2 recommendation (a) — deprecated entirely for MVP. Feature flag approach allows safe revert if Barbara later decides to enable in Phase B/C Range Reversal only (Q2 option c).
**How:**

Step 1 — Add flag to settings.json:
```python
import json
with open('C:/FluxQuantumAI/config/settings.json', 'r') as f:
    cfg = json.load(f)
assert 'overextension_reversal_enabled' not in cfg, 'Flag already exists — investigate'
cfg['overextension_reversal_enabled'] = False
with open('C:/FluxQuantumAI/config/settings.json', 'w') as f:
    json.dump(cfg, f, indent=2)
```

Step 2 — Identify OVEREXTENSION branch in `event_processor.py`:
```powershell
Select-String -Path C:\FluxQuantumAI\live\event_processor.py -Pattern "OVEREXTENSION" -CaseSensitive:$false |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }
```

Step 3 — Report all matches BEFORE editing. Ask Barbara if multiple locations found — confirm which is the entry-decision branch vs telemetry/logging.

Step 4 — For the identified entry-decision branch, add guard at the entry of the branch:
```python
# IF feature flag disabled, skip OVEREXTENSION reversal entirely
if not self._settings.get('overextension_reversal_enabled', False):
    # Per Design Doc v3 §5.0: counter-trend reversal excluded from Phase E
    # Legacy OVEREXTENSION branch disabled by Bloco 1 Emergency Disable 2026-04-20
    # Re-enable requires Barbara sign-off + playbook-specific scope (Phase B/C only)
    return None  # or whatever the NoOp return for this branch is — verify signature
```

**DO NOT** delete or modify the existing branch logic — just gate it. Reversibility preserved.

**Verify:**
```powershell
# py_compile
python -m py_compile C:\FluxQuantumAI\live\event_processor.py

# Import probe (verify no syntax errors or import failures)
python -c "from live import event_processor; print('import OK')"
```

**Rollback:** `Copy-Item $backup\event_processor.py C:\FluxQuantumAI\live\event_processor.py; Copy-Item $backup\settings.json C:\FluxQuantumAI\config\settings.json`

### 1.3 Action C — Sprint C v2 commit `074a482` patch (H4 staleness duplicate)

**What:** Fix duplicate `H4_MAX_STALENESS_HOURS_DEFAULT` in `level_detector.py` identified by CROSS_REF_REPORT finding #8.
**Why:** CROSS_REF_REPORT confirmed `H4_MAX_STALENESS_HOURS_DEFAULT = 6.0` duplicates existing `H4_STALE_HOURS = 8.0` in `d1_h4_updater.py:91`. Two constants for same concept = drift risk.
**How:**

Step 1 — Verify both exist:
```powershell
Select-String -Path C:\FluxQuantumAI\live\d1_h4_updater.py -Pattern "H4_STALE_HOURS"
Select-String -Path C:\FluxQuantumAI\live\level_detector.py -Pattern "H4_MAX_STALENESS_HOURS_DEFAULT"
```

Step 2 — In `level_detector.py`, replace the `H4_MAX_STALENESS_HOURS_DEFAULT = 6.0` constant with import of `H4_STALE_HOURS` from `d1_h4_updater`. Keep local alias for backward compatibility:
```python
# At top of level_detector.py, near existing imports
from .d1_h4_updater import H4_STALE_HOURS as H4_MAX_STALENESS_HOURS_DEFAULT
# Value now 8.0 (was 6.0 duplicated) — aligned with d1_h4_updater canonical
```

Step 3 — Remove the original `H4_MAX_STALENESS_HOURS_DEFAULT = 6.0` line from Sprint C v2 commit 074a482 CALIBRATION_TBD block, leaving comment:
```python
# H4_MAX_STALENESS_HOURS_DEFAULT removed 2026-04-20 Bloco 1 — imported from d1_h4_updater
# Original value 6.0 was duplicate; canonical H4_STALE_HOURS = 8.0
```

**Verify:**
```powershell
python -m py_compile C:\FluxQuantumAI\live\level_detector.py
python -c "from live.level_detector import H4_MAX_STALENESS_HOURS_DEFAULT; print(f'Value: {H4_MAX_STALENESS_HOURS_DEFAULT}')"
# Expected: Value: 8.0
```

**Rollback:** `Copy-Item $backup\level_detector.py C:\FluxQuantumAI\live\level_detector.py`

### 1.4 Action D — `mfe_giveback_enabled` → `true` (⚠ PROVISIONAL)

**What:** Set `settings.json` key `mfe_giveback_enabled` from `false` to `true`. Keep `mfe_giveback_threshold` at existing value (0.5) — NOT calibrated yet.
**Why:** CROSS_REF #15 confirmed infrastructure exists but disabled. Per Design Doc v3 §7, this becomes playbook-specific exit in Defense Layer. Enabling now with uncalibrated threshold provides interim protection against runaway scenarios. Barbara has full override — disable immediately if observed exits are premature.

**⚠ IMPORTANT FLAG:**
- Threshold 0.5 is UNCALIBRATED — the exit fires when price retraces 50% of Max Favourable Excursion
- May cause premature exits in normal pullbacks during Phase E Markup/Markdown
- Final threshold per playbook is Nível 2 calibration scope (framework v1.1)
- Revert = set `mfe_giveback_enabled: false` (one line)

**How:**
```python
import json
with open('C:/FluxQuantumAI/config/settings.json', 'r') as f:
    cfg = json.load(f)
assert cfg['mfe_giveback_enabled'] == False, 'Expected current to be False'
assert 'mfe_giveback_threshold' in cfg, 'threshold key must exist'
current_threshold = cfg['mfe_giveback_threshold']
cfg['mfe_giveback_enabled'] = True
# DO NOT modify threshold — keep existing uncalibrated value
with open('C:/FluxQuantumAI/config/settings.json', 'w') as f:
    json.dump(cfg, f, indent=2)
print(f'mfe_giveback_enabled: True, threshold unchanged at {current_threshold}')
```

**Verify:**
```powershell
Select-String -Path C:\FluxQuantumAI\config\settings.json -Pattern "mfe_giveback"
# Expected: mfe_giveback_enabled: true, mfe_giveback_threshold: 0.5 (or whatever current value was)
```

**Rollback:** `Copy-Item $backup\settings.json C:\FluxQuantumAI\config\settings.json`

### 1.5 Post-Actions

**Step 1 — Final py_compile of all modified files:**
```powershell
python -m py_compile C:\FluxQuantumAI\live\event_processor.py
python -m py_compile C:\FluxQuantumAI\live\level_detector.py
python -c "import json; json.load(open('C:/FluxQuantumAI/config/settings.json'))"
```

**Step 2 — Git commit:**
```powershell
cd C:\FluxQuantumAI
git add config/settings.json live/event_processor.py live/level_detector.py
git commit -m "Bloco 1 Emergency Disable — 2026-04-20

Per Design Doc Nível 1 v3 ratified 2026-04-20:
- delta_4h_inverted_fix: true -> false (Range-Bound logic, not universal)
- overextension_reversal_enabled feature flag added, default false
- H4_MAX_STALENESS_HOURS_DEFAULT deduplicated (imports d1_h4_updater.H4_STALE_HOURS=8.0)
- mfe_giveback_enabled: false -> true (PROVISIONAL — threshold 0.5 uncalibrated)

Rationale: 2026-04-20 Judas Swing incident. REGIME_FLIP_FORENSIC RCA-B+C confirmed.
CROSS_REF_REPORT findings #8, #14, #15, #17 addressed.
Literature: Wyckoff Villahermosa Ch13, ATS Two Problems to Solve, ICT Module 3.

Backup: sprints/sprint_emergency_disable_20260420/backup_pre_bloco1/
"
git log -1 --oneline
```

**Step 3 — Service restart (if service is running):**

Barbara must decide if service restart is needed. Since Bloco 1 modifies settings.json + py files, changes take effect only on next service start. If service currently running in any mode (executing trades, paper mode, shadow), ask Barbara before restart.

If Barbara approves restart:
```powershell
# Find current service process
$svc = Get-Process -Id 4516 -ErrorAction SilentlyContinue
if ($svc) {
    # Report current cmdline
    Get-CimInstance Win32_Process -Filter "ProcessId=4516" | Select-Object CommandLine
    Write-Output "Service PID 4516 is running. Ask Barbara before killing."
}
```

If NOT restarting: report that changes are staged but inactive. Next service restart applies them.

**Step 4 — Capture PID verification (unchanged):**
```powershell
Get-Process -Id 2512,8248,11740 | Format-Table
# Must still be running — capture NOT touched
```

**Step 5 — TASK_CLOSEOUT_REPORT:**

Create `C:\FluxQuantumAI\sprints\sprint_emergency_disable_20260420\TASK_CLOSEOUT_REPORT.md`:
- Actions A-D completed with before/after hashes
- Git commit hash
- Verification outputs (each py_compile, each Select-String)
- Service restart decision (done or deferred)
- Capture PIDs intact confirmation
- Issues encountered, if any
- **Explicit flag:** mfe_giveback_enabled is PROVISIONAL

### 1.6 ⚠️ CHECKPOINT 1 — STOP HERE

**Report to Barbara:**
- §1.5 TASK_CLOSEOUT_REPORT summary (5-10 lines)
- Confirm 4 actions complete, capture intact, git committed
- Flag any surprises (unexpected multiple OVEREXTENSION matches, py_compile failures, etc.)
- Ask Barbara approval to:
  - (a) Restart service (if not already done) to apply changes
  - (b) Proceed to Bloco 2

**DO NOT start Bloco 2 without Barbara explicit approval.**

---

## 2. BLOCO 2 — CAL-03 Investigation (~2-3h, READ-ONLY)

**Execute prompt:** `C:\FluxQuantumAI\sprints\sprint_cleanup_2_5_cal03_context_20260420\CLAUDECODE_CLEANUP_2_5_CAL03_Context_Investigation.md` (file will be created by this prompt in §2.1 below)

### 2.1 Prepare prompt file

Copy content of CLEANUP 2.5 investigation (provided separately in `/mnt/user-data/outputs/CLAUDECODE_CLEANUP_2_5_CAL03_Context_Investigation.md`) to the sprint directory:

```powershell
Copy-Item `
  C:\FluxQuantumAI\<path_where_Barbara_saves_this>\CLAUDECODE_CLEANUP_2_5_CAL03_Context_Investigation.md `
  C:\FluxQuantumAI\sprints\sprint_cleanup_2_5_cal03_context_20260420\
```

Or Barbara provides direct.

### 2.2 Execute per that prompt

Full scope: Phase 1 Discovery → Phase 2 Regime Tagging → Phase 3 Segmented Backtest → Phase 4 Incident Counterfactual → Verdict A-E.

**Hard constraints:** READ-ONLY. Zero writes outside sprint directory. Zero service touches.

### 2.3 ⚠️ CHECKPOINT 2 — STOP HERE

Report:
- Verdict (A/B/C/D/E per §7 of prompt)
- Top-3 actions from §8
- Any UNKNOWN flags (documentation not found, regime tagging surprises)

**DO NOT start Bloco 3 without Barbara explicit approval.**

---

## 3. BLOCO 3 — Dependency Scoping (~1-2h, design docs only, NO code)

**Purpose:** Produce 5 scoping design docs so Nível 2 can start without additional sprints. Each doc is short (~100-200 lines) focused on a concrete dependency blocking Strategy Layer MVP.

All outputs to `C:\FluxQuantumAI\sprints\sprint_dependency_scoping_20260420\`.

### 3.1 D1 — Sprint H4-WRITER-FIX scope

**Output file:** `SCOPE_D1_H4_WRITER_FIX.md`

**Content required:**
- Current state analysis (read d1_h4_updater.py, identify perf bottleneck causing 1GB RAM / 90% CPU every 5min)
- Incremental event-driven redesign proposal (process only NEW M1 bars, not full 2.2M)
- API contract for Structure Agent H4/D1 inputs (what fields, cadence)
- Implementation effort estimate (hours, complexity, risk)
- Rollout plan (shadow first, then replace)

### 3.2 D3 — Session Agent MVP spec

**Output file:** `SCOPE_D3_SESSION_AGENT.md`

**Content required:**
- Current state: `kill_zones.py` as telemetry — what it produces, who consumes
- Promotion to canonical Agent — contract (SessionContext output per Design Doc v3 §3.5)
- New fields required: `session_range_so_far`, `session_open_ts`, `kill_zone_active`
- Input sources (OHLCV M1, timezone UTC)
- Latency requirement (must be real-time — Judas Swing is tick-critical)
- Integration points with legacy `session` field in decision_log

### 3.3 D4 — Liquidity Map previous-session levels spec

**Output file:** `SCOPE_D4_LIQUIDITY_PREV_SESSION.md`

**Content required:**
- Previous Day H/L, Asian H/L, London H/L — how to compute incrementally
- Equal highs/lows detector algorithm (tolerance, lookback)
- Sweep classification rules (continuation vs reversal post-sweep)
- Data source: OHLCV M1 + existing `l2_sweep_detected` from v5
- Storage: in-memory per session + persist end-of-session

### 3.4 D5 — Phase classifier MVP spec

**Output file:** `SCOPE_D5_PHASE_CLASSIFIER.md`

**Content required:**
- Expand pseudo-code from Design Doc v3 §3.1.2 into concrete Python-like algorithm
- Input data dependencies (all already identified in §3.1.2 Inputs table)
- `phase_confidence` computation rule
- Edge cases: flat market, weekly/daily divergence, newly-opened session with no prior state
- Calibration targets for Nível 2 (e.g., "within N bars" thresholds per phase)
- Test vectors: 5-10 historical market examples with expected phase classification for validation

### 3.5 D6 — Wyckoff event detector extension spec

**Output file:** `SCOPE_D6_WYCKOFF_EVENTS.md`

**Content required:**
- Current v5 parquet has `m30_spring_type`, `m30_upthrust_high` — partial
- Extension needed: SOS, SOW, LPS, LPSY, UA, UT, mSOS, mSOW, PS, PSY, SC, BC, AR, ST
- Per-event detection heuristics (price action + volume + context) — keep rule-based MVP
- Literature references per event (Villahermosa chapters)
- Output column format for v5 parquet extension
- Calibration targets (volume thresholds, lookback windows) for Nível 2

### 3.6 Combined summary

**Output file:** `SCOPE_SUMMARY.md`

Aggregates effort estimates across D1+D3+D4+D5+D6. Total hours, critical path, parallelization opportunities, risks. Feeds Nível 2 sequencing.

### 3.7 ⚠️ CHECKPOINT 3 — STOP HERE

Report to Barbara:
- 5 scope docs delivered
- Combined effort estimate (in hours or days)
- Any blocked/unclear dependencies that need Barbara input
- Recommended order of execution for Nível 2

**DO NOT proceed to Nível 2 implementation. Nível 2 requires separate design doc.**

---

## 4. Final Deliverable Summary

After all 3 blocos complete + 3 checkpoints ratified:

**Production changes (Bloco 1):**
- `delta_4h_inverted_fix` = false
- `overextension_reversal_enabled` = false (new flag, OVEREXTENSION branch gated)
- `H4_MAX_STALENESS_HOURS_DEFAULT` deduplicated (8.0 canonical)
- `mfe_giveback_enabled` = true (provisional)
- 1 git commit

**Evidence (Bloco 2):**
- `CAL03_CONTEXT_REPORT.md` with Verdict A-E + 8 sections
- Answers Design Doc Nível 1 v3 Open Question Q4

**Design inputs for Nível 2 (Bloco 3):**
- 5 scope docs (D1, D3, D4, D5, D6)
- 1 combined summary
- Effort estimates for Nível 2 sequencing

**System state:**
- Capture PIDs 2512, 8248, 11740 intact
- Service restart decision documented
- All operations READ-ONLY except Bloco 1 actions

---

## 5. Rollback (universal)

If ANY step fails or Barbara calls abort:

```powershell
cd C:\FluxQuantumAI
git diff HEAD~1 HEAD  # inspect changes
git revert HEAD       # creates revert commit
# OR hard revert:
# git reset --hard HEAD~1
```

Or file-by-file:
```powershell
$backup = "C:\FluxQuantumAI\sprints\sprint_emergency_disable_20260420\backup_pre_bloco1"
Copy-Item $backup\settings.json C:\FluxQuantumAI\config\settings.json -Force
Copy-Item $backup\event_processor.py C:\FluxQuantumAI\live\event_processor.py -Force
Copy-Item $backup\level_detector.py C:\FluxQuantumAI\live\level_detector.py -Force
```

Restart service if rollback mid-session.

---

## 6. Critical Reminders for ClaudeCode

1. **Capture PIDs (2512, 8248, 11740) NEVER interrupted.** Ever.
2. **No decisions beyond this prompt.** Multiple OVEREXTENSION matches → ASK Barbara, don't guess.
3. **Every production edit** = backup + py_compile + import probe. No shortcuts.
4. **Checkpoints are hard stops.** Wait for Barbara approval, don't chain blocos.
5. **READ-ONLY means READ-ONLY.** Blocos 2 + 3 write only to their sprint directories.
6. **No service restart without Barbara approval.** Even if Bloco 1 changes are staged inactive.
7. **TASK_CLOSEOUT_REPORT per bloco** — Barbara reads these to ratify.
8. **Urgency is NEVER reason to skip process** — per memory Golden Rule FluxQuantumAI.

Begin when Barbara gives green light.

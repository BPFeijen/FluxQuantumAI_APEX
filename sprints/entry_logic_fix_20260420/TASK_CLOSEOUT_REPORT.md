# ENTRY LOGIC FIX — TASK_CLOSEOUT (LITERATURA-ALIGNED, C1 UNIVERSAL)

**Sprint:** entry_logic_fix_20260420
**Date:** 2026-04-20
**Status:** ✅ READY FOR RESTART — awaiting Barbara authorization via nssm
**Restart status:** **NOT TRIGGERED**. Barbara runs `nssm restart FluxQuantumAPEX` manually after reading this report.

---

## 1. Scope & decisions applied

**Decision:** C1 — universal direction-aware post-validation, no OVEREXTENDED exception.

Rationale (Barbara, confirmed by Sprint B v2 architectural audit):
- Overextension reversal mechanism NOT literatura-aligned (Wyckoff/ICT/ATS).
- Threshold `1.5×ATR` hardcoded, no data-driven calibration found.
- Decision log shows **72 overextension GO signals 2026-04-15..20 → 0 fills** (broker disconnected). Filtering them costs nothing, is-zero-risk.
- 03:14-class bug class (fallback unconfirmed wrong-side) is the real production issue.

Ordering applied (literatura-aligned):

| # | Criterion | Direction |
|---|---|---|
| 1 | is_valid_direction | DESC (MANDATORY) |
| 2 | source priority | DESC (m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed) |
| 3 | age_min | ASC (fresher first) |
| 4 | distance_to_price | ASC (tactical tiebreaker) |

Decisions from Sprint A prompt honoured:
- Sem shadow / dual-mode / feature flag (fix directo)
- Staleness 15min hardcoded
- Re-avaliação dinâmica fora scope
- COUNTER_HTF label = LOG ONLY (não bloqueia)
- Distance==0 edge case = PASS com `is_touch=True`
- FAR action = log + metric, não trigger wait-for-retrace
- M30 comanda execução (filosofia preservada: _resolve_direction continua single source of truth)

---

## 2. Files modified

### `C:\FluxQuantumAI\live\level_detector.py`

- **Pre-hash:**  `e2e0d7d112afbca64be29aa6dd285e99`
- **Post-hash:** `5e74e1ad645093cf325ada188c952f44`
- **Lines:** 589 → 773 (+184)
- **Changes:**
  - Imports: added `dataclass`, `Literal`
  - Added module constants `_LD_NEAR_ATR_FACTOR=1.0`, `_LD_NEAR_FLOOR_PTS=5.0` (mirrors event_processor), `_SOURCE_PRIORITY` dict
  - Added `@dataclass(frozen=True) LevelCandidate` (10 fields)
  - Added `get_levels_for_direction(direction, price, max_age_min=15.0, max_distance_pts=8.0, gc_to_mt5_offset=None) -> list[LevelCandidate]`
  - Preserved all existing behaviour (`get_current_levels` unchanged; `run_live.py` and `integration_health_check.py` consumers untouched).

### `C:\FluxQuantumAI\live\event_processor.py`

- **Pre-hash:**  `3501c260350f135b0fa38b78b6e8c8d1`
- **Post-hash:** `1ade483a9389bf8354782857fd9d4f8f`
- **Lines:** 4430 → 4589 (+159)
- **Changes:**
  - Added `_metric_incr(self, name, value=1)` — structured-log metric helper (no backend).
  - Added `_classify_source_from_candidate(self, cand)` — maps LevelCandidate.source → legacy `_near_level_source` vocabulary.
  - Refactored `_near_level(self, price, direction=None)`:
    - `direction=None` → dispatches to `_near_level_legacy(price)` (byte-compatible with pre-fix).
    - `direction in ("SHORT","LONG")` → calls `get_levels_for_direction`, returns `("PASS"|"NEAR"|"FAR", LevelCandidate|None)`.
    - Emits `METRIC near_level.*` log lines.
  - Added `_near_level_legacy(self, price)` — verbatim copy of pre-fix `_near_level` body.
  - Modified `_trigger_gate` display block:
    - Trending branch now produces `COUNTER_HTF` label when direction opposes `daily_trend`. LOG ONLY (does not block).
    - Non-trending branch now calls `_near_level(price, direction=direction)`; v1 = PASS/NEAR/FAR accordingly.
  - Inserted **C1 post-validation** at:
    - `_on_iceberg_event` (ICEBERG trigger path): after `_resolve_direction`, suppresses fire if new `_near_level(price, direction)` returns NEAR or FAR.
    - `_tick_loop` (ALPHA trigger path): same pattern.
  - `_resolve_direction` kept as single source of truth for ATS strategy (Sprint A constraint #10 honoured).

---

## 3. Unit tests

File: `C:\FluxQuantumAI\tests\test_near_level_direction_aware.py` (new)

**19/19 PASSED** in 3.50s.

Coverage:
- Tests 1-11 from DESIGN_DOC §5 (fresh/stale/fallback/touch/transition scenarios) → 11/11 PASSED
- Invariants from Appendix A (ordering, source priority > distance, short/long side invariants, determinism, max_age=0 edge, source priority strict order, invalid direction raises) → 8/8 PASSED
- Key: **Test 4 (03:14 replay)** confirms the FAR result.
- Key: **test_invariant_source_priority_beats_distance** enforces literatura ordering (m5_confirmed 5pts away wins over m5_unconfirmed 1pt away).

Run: `python -m pytest tests/test_near_level_direction_aware.py -v`

---

## 4. Backtest counterfactual

Script: `sprints/entry_logic_fix_20260420/backtest_counterfactual.py`
Report: `sprints/entry_logic_fix_20260420/BACKTEST_COUNTERFACTUAL.md`

Source: `C:\FluxQuantumAI\logs\decision_log.jsonl` (11978 entries).

### Results

| Metric | Value |
|---|---|
| Total log entries | 11978 |
| GO signals | 659 |
| IDENTICAL_APPROX | 514 |
| NEW_REJECT_WRONG_SIDE | **145 (22.00%)** |
| CANNOT_REPLAY | 0 |

Breakdown rejected:
- SHORT NEW_REJECT: 103 (liq_top BELOW price, structurally invalid)
- LONG NEW_REJECT: 42 (liq_bot ABOVE price, structurally invalid)
- All ALPHA trigger (matches Sprint B v2: 0 PATCH2A/DELTA/GAMMA in prod).

Target signal `2026-04-20T03:14:46.448214+00:00`:
- label: **NEW_REJECT_WRONG_SIDE**
- direction: SHORT, price_mt5=4791.08, level=4767.53 (liq_top)
- delta_wrong_side: **23.55pts** — level was 23.55 BELOW price.
- **Under C1 post-validation this signal would NOT have been emitted.** ✓

### Caveats (from backtest module docstring)

- Approximate replay: uses fired trigger.level_price_mt5 vs price_mt5, not full parquet state replay.
- `IDENTICAL_APPROX` is conservative: the C1 filter would typically pass these, but full replay could reveal edge cases where the top candidate moves out-of-band (NEAR instead of PASS).
- Design doc §7.1 estimated 5-15% rejection rate; observed 22% on a larger 659-signal sample. More signals analysed → more bug instances revealed. Consistent with catching a real production pattern.

---

## 5. Live dry-run

**NOT PERFORMED.** Per Sprint A FASE 5 constraint and memory `feedback_server_access_incident` (2026-04-12 incident), ClaudeCode does NOT restart live service. Barbara authorizes manually.

---

## 6. Restart instructions for Barbara

**PowerShell (run as Administrator if needed):**

```powershell
# Stop + start via nssm (per memory feedback_run_live_restart)
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX

# Wait 30s for service boot
Start-Sleep 30

# Check tracebacks in stderr
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"
Get-Content $stderr -Tail 200 | Select-String "Traceback"

# Verify capture processes still running (NEVER kill these)
foreach ($p in 12332, 8248, 2512) {
    Get-Process -Id $p -ErrorAction SilentlyContinue |
        Select-Object Id, ProcessName, CPU | Format-Table
}

# Sanity: look for new telemetry
Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 500 | Select-String "NEAR_LEVEL|POST_VALIDATION|COUNTER_HTF"
```

**Expected after restart (30min observation window):**
- Zero tracebacks in stderr.
- Capture processes (12332, 8248, 2512) unchanged.
- New log lines: `NEAR_LEVEL PASS|NEAR|FAR`, `POST_VALIDATION *suppress [ICEBERG|TICK]`, `METRIC near_level.*`.
- COUNTER_HTF log entries possible but non-blocking.
- Rejection of 03:14-class signals via `POST_VAL FAR suppressed`.

---

## 7. Rollback

**Backup location:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backup_pre_fix_20260420_101330\`

```powershell
$bak = "C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backup_pre_fix_20260420_101330"
Copy-Item "$bak\level_detector.py"  "C:\FluxQuantumAI\live\level_detector.py"  -Force
Copy-Item "$bak\event_processor.py" "C:\FluxQuantumAI\live\event_processor.py" -Force
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX
```

MANIFEST.json in that directory has pre/post hashes for verification.

---

## 8. Open items for future sprints

1. **Databento extended backtest** — full parquet replay (not approximation) over 126 days Jul-Nov 2025 for more robust validation.
2. **Re-evaluation dynamic** (design doc §4.4 Option A) — cancel pending signal if fresh correct-side box emerges before execution.
3. **COUNTER_HTF → BLOCK conversion** — after observing data, decide if counter-HTF within-zone entries should be blocked.
4. **`derive_m30_bias` equality bug** (Sprint B v2 finding) — fix `liq_top > box_high` (strict) to `>=` or explicit handling of `==`. Root cause of missed LONG opportunities on 2026-04-20.
5. **PATCH2A/DELTA integration order** (Sprint B v2 finding) — parallel evaluation instead of serial fallback; currently ALPHA suppresses every PATCH2A evaluation.
6. **Overextension reversal recalibration OR removal** (Sprint B v2 finding) — neither literatura-aligned nor data-driven. 72 GO signals, 0 fills in 5 days. RECALIBRATE or REMOVE.
7. **Freshness tracking** — current `source` enum proxies freshness via confirmed/unconfirmed. Explicit `is_fresh` flag on LevelCandidate would allow finer policy (e.g., "reject if box tested ≥3 times" per Dukascopy literature).
8. **Staleness threshold** (design doc §9.4) — 15min hardcoded; move to settings.json for data-driven calibration.

---

## 9. System state during implementation

- Files modified: 2 (level_detector.py, event_processor.py) + 1 test + 1 backtest script + this report
- Service restarts: **ZERO**
- Capture processes (12332, 8248, 2512): **NOT TOUCHED** (verified by Sprint B agents which called Get-Process checks)
- Git commits: **ZERO** (awaiting Barbara approval before any git operation)
- Tests executed: 19/19 PASSED

---

## 10. Ready for review

- [x] Design doc read (647 lines)
- [x] Discovery completed (`_near_level`, `get_current_levels` consumers mapped)
- [x] `_resolve_direction` analysed; C1 ("post-validation") chosen to preserve ATS strategy authority
- [x] Backup created with MANIFEST (pre + post hashes)
- [x] LevelCandidate + get_levels_for_direction implemented (literatura-aligned ordering)
- [x] _near_level refactored (new dispatcher + _near_level_legacy byte-compat)
- [x] _trending_v1 refactored (COUNTER_HTF + FAR direction-aware)
- [x] C1 post-validation inserted at iceberg + tick call sites
- [x] py_compile OK both files
- [x] 19/19 unit tests PASSED
- [x] Backtest counterfactual: 145/659 GOs (22%) rejected, 03:14 confirmed NEW_REJECT
- [x] This TASK_CLOSEOUT report written
- [ ] Barbara reviews this report
- [ ] Barbara runs `nssm restart FluxQuantumAPEX` manually
- [ ] 30min post-restart observation (zero tracebacks, capture 3/3 intact)
- [ ] Decision: commit to git OR rollback

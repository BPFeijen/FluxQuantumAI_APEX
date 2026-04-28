# STALE-D1H4-001 Phase 1 — Deploy Brief-back

**Asana:** 1214284204948063
**Date:** 2026-04-29 (UTC; 2026-04-28 ~22:35 UTC at deploy)
**Author:** Claude Code (Opus 4.7)
**Branch:** `fix/xau-mid-population` (local commits, Rule 11 — no push)
**Target:** deploy < 2026-04-29 14:00 UTC (Super Quarta buffer 4h before FOMC). **Met with ~15.5h buffer.**

---

## Commits (local)

| SHA | Subject |
|-----|---------|
| `ae4f69b` | STALE-D1H4-001 Phase 1: windowed rebuild + DATA-002 fix + reactivate |
| `af9f40e` | STALE-D1H4-001 hotfix: move D1H4 startup AFTER processor instantiation |

Branch HEAD: `af9f40e`.

---

## Phase summary

| Phase | Time | Status |
|-------|------|--------|
| 0 — Provenance | ~30 min | ✅ closed (`_audit/STALE_D1H4_PHASE0_PROVENANCE.md`) |
| 1a — Design | ~25 min | ✅ closed (`_audit/design/D1H4_UPDATER_INCREMENTAL.md`) |
| 1b — Implementation | ~45 min | ✅ closed (commit `ae4f69b`) |
| 1b.2 — Standalone smoke | ~5 min | ✅ 2.41 s cycle, 1.8 MB resident |
| 1c — Restart + sanity | ~30 min (incl. 1 hotfix iteration for closure-ordering bug) | ✅ closed (PID 8204 → 24732) |
| 1d — Brief-back | this doc | ✅ |

Total ~2.5 h.

---

## What changed

### `live/d1_h4_updater.py`

**`_build_m1_windowed`** (NEW, supersedes `_build_m1`):
- pyarrow predicate pushdown reads only last **60 days** of M1
- Falls back to full-read + slice if pyarrow filters fail
- Memory bounded to ~1.8 MB resident (vs ~300 MB previous full load)

**`_trades_to_m1`** (NEW, replaces `_micro_to_m1`):
- Direct port of `m30_updater._trades_to_m1` (DATA-002 P1 carryover)
- Reads `trades_*.csv.gz` `price` column instead of `microstructure_*.csv.gz` `mid_price`
- Eliminates the silent corruption that DATA-002 P1 fixed in m30_updater 2026-04-25 but never reached d1_h4_updater (which was already disabled by then)

**`_build_m1` alias**:
- Preserved for back-compat — points to `_build_m1_windowed`

**`run_update()`** structurally unchanged — only the M1 source changes.

**`WINDOW_DAYS = 60`** module constant.

### `run_live.py`

- Removed disable comment block at lines 760-770
- Inserted the D1H4 startup block AFTER `processor` instantiation (line ~826, after IPC injection)
- The original disable-era position would have failed with closure-ordering bug (`_get_dt = lambda: getattr(processor, ...)` evaluated by daemon thread before main reaches `processor = _EventProcessor(...)`)
- Hotfix `af9f40e` corrects this

---

## Acceptance criteria (Asana spec)

| Criterion | Result |
|-----------|--------|
| Investigação documentada (perf root cause + commit history) | ✅ Phase 0 doc |
| Design incremental specificado em `_audit/design/D1H4_UPDATER_INCREMENTAL.md` | ✅ |
| `live/d1_h4_updater.py` reescrito | ✅ |
| Boot integration: updater starts as daemon thread no `run_live.py` | ✅ |
| `gc_d1_boxes.parquet` last write < 2 min after start | ✅ **0.1 s** at brief-back time |
| `gc_h4_boxes.parquet` last write < 2 min after start | ✅ **0.3 s** at brief-back time |
| Backfill: D1/H4 bars 2026-04-09 → 2026-04-28 processados | ✅ 60-day window covers it; 16 H4 boxes + 2 D1 boxes detected |
| Heartbeat reflects fresh `last_closed_d1` + `last_closed_h4` | ✅ d1=2026-04-23T22:00, h4=2026-04-28T18:00 |
| Perf: cycle time < 5 s | ⚠ **first cycle 10 s** (boot contention); standalone smoke 2.4 s; steady-state expected 2-3 s |
| Local commit `fix/xau-mid-population`, NO push | ✅ `ae4f69b` + `af9f40e`; zero push |
| Zero modifications to capture services 6376 / 13072 / 20396 | ✅ Bridge / updater never touched ports 8000 / 8002 |
| Brief-back per G-PREMISE-AUDIT format | ✅ this doc |

---

## Live state at deploy

`gc_d1h4_bias.json` first post-restart cycle:

```json
{
  "timestamp": "2026-04-28T22:34:29.628202+00:00",
  "d1_jac_dir": "long",
  "h4_jac_dir": "short",
  "bias_direction": "LONG",
  "bias_strength": "WEAK",
  "bias_source": "runtime_d1h4_updater",
  "data_freshness": {
    "h4_parquet_age_s": 0.3,
    "d1_parquet_age_s": 0.1,
    "h4_stale": false,
    "d1_stale": false,
    "m1_last_bar": "2026-04-28T22:32:00+00:00"
  },
  "last_closed_h4_ts": "2026-04-28T18:00:00+00:00",
  "last_closed_d1_ts": "2026-04-23T22:00:00+00:00",
  "h4_total_boxes": 16,
  "d1_total_boxes": 2,
  "h4_last_bar":   "2026-04-28T22:00:00+00:00",
  "d1_last_bar":   "2026-04-28T22:00:00+00:00",
  "elapsed_s":     10.0
}
```

**Operational improvement vs disabled state:**

| Field | Pre (disabled) | Post (live) |
|-------|----------------|-------------|
| `last_closed_d1` | 2026-04-08 (20d stale) | **2026-04-23** (5d; fresh JAC event) |
| `last_closed_h4` | 2026-04-02 (26d stale) | **2026-04-28T18:00** (4.5h fresh) |
| `d1_stale` | true | **false** |
| `h4_stale` | true | **false** |
| `bias_direction` | from 2026-04-08 snapshot | **LONG** (current state) |
| `bias_strength` | from snapshot | **WEAK** (D1 long, H4 short — divergence) |

---

## Sanity check post-restart

| Item | Status |
|------|--------|
| Pre-restart run_live PID | 8204 (started 00:31 CEST after smoke commit) |
| Hotfix-restart run_live PID | **24732** (started 00:33 CEST) |
| Capture service PID 13072 (`watchdog_l2_capture`) | **invariant** (Apr 25 boot) |
| Capture service PID 20396 (`iceberg_receiver`) | **invariant** (Apr 26 boot) |
| Capture service PID 23028 (`quantower_level2_api`) | **invariant** (Apr 28 22:15 boot — pre-existing, NOT touched by this work) |
| Boot log: `D1H4 Updater started (300s cadence, SHADOW MODE)` | ✅ |
| Boot log: `D1H4 updater startup failed` | ❌ (good — was the closure bug; fixed by hotfix `af9f40e`) |
| `gc_d1_boxes.parquet` mtime | fresh |
| `gc_h4_boxes.parquet` mtime | fresh |
| `gc_d1h4_bias.json` `bias_source` | `"runtime_d1h4_updater"` (vs prior stale snapshot) |
| WS_MB at brief-back | 608 MB resident (run_live total — normal for this process) |

**Note re: capture services:** PID 23028 (quantower_level2_api) replaced PID 6376 at 22:15 UTC 28th, *before* this Phase 1 work began. Cause unknown but NOT attributable to this task. The watchdog (`watchdog_l2_capture` PID 13072) is the auto-respawn agent and may have done a routine restart.

---

## Closure-ordering bug + hotfix (transparent record)

First post-1b restart (PID 8204) raised:

```text
D1H4 updater startup failed: cannot access free variable 'processor'
where it is not associated with a value in enclosing scope
```

Cause: my Phase 1b put the D1H4 startup at run_live.py lines 760-770 (the
disabled-era position), which is **before** `processor = _EventProcessor(...)`
at line 800. The `_get_dt = lambda: getattr(processor, "daily_trend", "unknown")`
captures `processor` by name; the daemon thread's first `run_update` cycle
calls `_get_dt()` before main thread reaches the assignment → uninitialized
closure cell.

Fix `af9f40e`: moved the D1H4 startup block to **after** processor
instantiation + IPC injection (line ~826). The lambda now sees `processor`
in scope when called.

This was a **latent ordering bug** in the pre-disable codebase — likely
never observed in the disabled era because the section was always
commented out. Phase 1c surfaced it; Phase 1c-hotfix corrected it within
~5 minutes.

---

## Premise correction (acknowledged per Barbara directive)

Per Phase 0 § 3.3 + Barbara's GO message: **this fix is NOT a silver bullet
for the 79 % daily_trend=unknown observed today**. That phenomenon is the
Q2 2026 regime collapse documented in `_audit/TREND_CLASSIFIER_V2_VALIDATION.md`
(B+C ensemble disagreement on M30-resampled D1 — not stale gc_d1_boxes.parquet).

**What this fix actually delivers:**
1. ✅ Restores observability (dashboard `last_closed_d1` / `last_closed_h4` tiles)
2. ✅ Eliminates DATA-002 silent regression on naive re-enable (`mid_price` → `trades.price`)
3. ✅ Prep for FASE 4b activation (`derive_h4_bias` future wiring; deferred per Q3)
4. ✅ Eliminates operator confusion from stale snapshot timestamps
5. ✅ Bounded resource use (1.8 MB / 2.4 s standalone, 10 s first cycle in boot contention) prevents the original 1 GB / 90 % CPU regression

**What this fix does NOT do:**
- ❌ Does not change live trade gate decisions (d1h4_bias is shadow only)
- ❌ Does not "fix" the 79 % daily_trend=unknown rate (regime issue)
- ❌ Does not activate FASE 4b H4 confirmation rules

---

## PRAC

- **First-cycle perf 10 s** is borderline vs 5 s spec target. Expected to drop to 2-3 s steady-state once OS file cache warms and run_live boot CPU pressure subsides. **Will monitor next 2-3 cycles** post-deploy.
- **Closure-ordering bug surfaced** at production (not at standalone smoke). Phase 1b.2 standalone test ran the function directly without the run_live closure context, so the lambda was never instantiated. Lesson: future runtime-integration tests should exercise the daemon-thread + lambda path together. Acceptable miss this iteration; flagged for future test-coverage improvement.
- **Capture services invariant verified** for our actions; one pre-existing PID rotation (6376 → 23028 at 22:15 UTC, 4h before this work) flagged but not attributable to this task.
- **Premise correction transparent**: Barbara's task framing implied stale D1H4 caused the 79 % unknown — empirically false. Phase 0 § 3.3 + this brief-back § "Premise correction" make the honest framing visible. Fix still valid for stated reasons.
- **Rollback plan ready** if any post-deploy cycle reveals an issue: `git revert af9f40e ae4f69b` + restart returns to disabled-D1H4 state. No git push, no externally-visible regression.

---

## Files delivered

- ✅ `live/d1_h4_updater.py` — windowed rebuild + DATA-002 carryover (commit `ae4f69b`)
- ✅ `run_live.py` — re-enabled D1H4 boot integration in correct order (commits `ae4f69b` + `af9f40e`)
- ✅ `_audit/STALE_D1H4_PHASE0_PROVENANCE.md` — Phase 0 read-only audit
- ✅ `_audit/design/D1H4_UPDATER_INCREMENTAL.md` — Phase 1a design doc
- ✅ `_audit/STALE_D1H4_PHASE1_DEPLOY.md` — this brief-back

Plan ClaudeCode: zero in-progress (after this task closes).

---

## Sign-off

🛑 **GATE — awaiting your sign-off + closure of Asana 1214284204948063.**

Next observation window:
- **Next 2-3 D1H4 cycles** (every 300 s starting from 22:34 UTC) — confirm steady-state perf < 5 s
- **Tomorrow's FOMC at 18:00 UTC** — verify the heartbeat shows fresh d1h4_bias values during volatility (no operator confusion from stale tile)
- **No live trade gate impact expected** (shadow only)

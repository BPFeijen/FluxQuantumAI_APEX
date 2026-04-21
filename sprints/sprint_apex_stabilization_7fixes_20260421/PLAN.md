# Sprint APEX Stabilization — 7 Fixes — PLAN & Pre-Flight Report

**Sprint ID:** `sprint_apex_stabilization_7fixes_20260421`
**Ratified by:** Barbara, 2026-04-21
**ClaudeCode executor:** begun 2026-04-21 ~07:53 UTC (09:53 local)
**Hard Limit reference:** §2 of CLAUDECODE_SPRINT_APEX_STABILIZATION_7FIXES.md

---

## 1. Pre-Flight §4.1 — System State (GREEN)

| check | observed | expected | status |
|---|---|---|---|
| Capture PID 2512 (`watchdog_l2_capture.py`) | alive, StartTime 2026-04-14 09:35:00 | alive (any start ≤ pre-Sprint) | ✓ OK |
| Capture PID 8248 (`iceberg_receiver.py`) | alive, StartTime 2026-04-14 09:35:02 | alive | ✓ OK |
| Capture PID 11740 (`quantower_level2_api` port 8000) | alive, StartTime 2026-04-20 16:51 | alive | ✓ OK |
| Service PID 4516 (`run_live.py --execute --broker roboforex --lot_size 0.05`) | alive | alive, command matches | ✓ OK |
| Service `FluxQuantumAPEX` | Running (Automatic) | Running | ✓ OK |
| Service `FluxQuantumAPEX_Dashboard` | Running | — | — |
| Service `FluxQuantumAPEX_Live` | Stopped | — | — (hantec disabled per memory) |
| Service `FluxQuantumAPEX_Dashboard_Hantec` | Stopped | — | — |
| `settings.json` sha256 | `BA0166FFAC9741D813BDA9C6784A81507CDF612343629CC2BF58F00C94A42D52` | `BA0166FF…A42D52` baseline | ✓ MATCH |
| Git HEAD | `074a482 Sprint C 2a: add derive_h4_bias …` | `074a482` | ✓ OK |
| Git HEAD–1 | `acecefe Sprint C admin: BACKLOG entry for H4-WRITER-FIX (P1)` | — | ✓ OK |
| Current branch | `master` | — | ⚠ note below |
| Working tree | only untracked `??` entries, **zero modified/staged files** | no uncommitted changes from prior sprints | ✓ OK |

### Notes on ⚠
- **Branch:** memory references `branch: gold-gc` (`reference_github`). Local HEAD is on `master`. This is flagged for the ML Engineer's awareness. No code was modified; the working tree is clean of `M`/`A`/`D` entries. Untracked-only state does not block the sprint per §4.1, but the ML Engineer may want to verify which branch the `FluxQuantumAPEX` service was launched from (`run_live.py` process started 2026-04-20 00:47 UTC, before the most recent commit `074a482` on 2026-04-20 16:06 UTC, so the running binary corresponds to the parent commit surface, not HEAD — expected given no restart since then).
- **Untracked file curiosity:** `git status` reports `"C\357\200\272FluxQuantumAIlogsv3_train_run.log"` — an oddly path-escaped filename. Flagged for awareness; not touched.

**All §4.1 abort conditions are clear:**
- [✓] All capture PIDs present
- [✓] Service PID running
- [✓] No uncommitted working-tree modifications

---

## 2. Pre-Flight §4.2 — Global Backup

Backup directory: `C:\FluxQuantumAI\sprints\sprint_apex_stabilization_7fixes_20260421\backup_pre_sprint\`

MANIFEST.txt (sha256 + size, as per §4.2):

| file | sha256 | size |
|---|---|---|
| `base_dashboard_server.py` | `0DF1C5DB9433C4F857A303D58F6A244BAF4B437ACB1628B45F928423D12E3A6F` | 26,444 |
| `event_processor.py` | `9A9E749A2E48223A990462E5B510B4A55AA942DC70D3D2254E24CCC5C1F84ED8` | 220,623 |
| `level_detector.py` | `02C1ADD16D984BFD6EF3E08289456F18C2C3046B2A5986D4EBE0DAE0E39876E6` | 45,720 |
| `position_monitor.py` | `A7475750A642CD0C248535507A644C78360F52978A0E8DE1D6F0D4B6AE7DA88F` | 98,564 |
| `settings.json` | `BA0166FFAC9741D813BDA9C6784A81507CDF612343629CC2BF58F00C94A42D52` | 5,468 |
| `telegram_notifier.py` | `E30E7AC8D513430ECA1B7044D1824AD669D9BCE90D6EAA648DA92A1725AC3F52` | 31,520 |
| `tick_breakout_monitor.py` | `B2A936150CB26E793D652608630BB58C75124FD60F4256CF89922EDF9016B463` | 16,313 |

All 7 candidate files from §4.2 present; none missing. MANIFEST written.

---

## 3. Phase Plan (for ratification)

| Phase | Fix | Scope (condensed) | Files likely touched | Validation gates |
|---|---|---|---|---|
| 1 | #3 Canonical Publish Path | `_publish_decision_canonical`; atomic `decision_live.json` write; independent heartbeat; replace per-type emits | `event_processor.py`, `position_monitor.py`, `tick_breakout_monitor.py` | py_compile + import probe + unit test `test_phase1_canonical_publish.py` |
| 2 | #1 Surface M30_BIAS_BLOCK | Route M30_BIAS_BLOCK through canonical publish; dedup 30 s | `event_processor.py` | unit test dedup + payload shape |
| 3 | #6 Telegram Observability | `log.debug` → `warning`; `_telegram_stats` 5-min heartbeat into `service_state.json.telegram` | `position_monitor.py`, `telegram_notifier.py` | unit test (mock success/fail/dedup) |
| 4 | #2 `derive_m30_bias` Regime | §4.0 data-driven calibration of `ATR_INVALIDATION_MULT`; new `derive_m30_bias_v2` (confirmed/provisional/invalidated + H4 override); legacy wrapper preserved | `level_detector.py`, `event_processor.py` | Phase 4.0 acceptance 4/4 + 12 unit tests + replay backtest 2026-04-20 22:00 → 2026-04-21 05:00 |
| 5 | #7 M5/M30 Discrepancy | Persistence tracker; canonical `M5_M30_PERSISTENT_DISCREPANCY` after 10 min + trigger suppression | `level_detector.py` | unit test warn-only vs persistent |
| 6 | #4 ApexNewsGate Re-enable | Convert relative→absolute imports; loud import-error logging; `service_state.json.news_gate` health | `news_gate/*`, `event_processor.py` | startup probe + induced-failure probe |
| 7 | #5 Feed Staleness + Restart | `FEED_DEAD` canonical; independent heartbeat thread; py_compile ALL; rollback dry-run; Asian window restart 22:00-23:00 UTC; 30-min post-restart observation + triangulation | `event_processor.py`, `position_monitor.py` | §11.3 pre-restart audit + §11.8 triangulation |

7 STOPs total; Claude must ratify each before next Phase begins.

---

## 4. Active guards during this sprint (self-imposed + memory)

- Capture PIDs 2512, 8248, 11740 **never touched** (memory `feedback_capture_services_never_kill`, `feedback_never_restart_capture`).
- No restart of any capture service. The Phase 7 restart targets ONLY `FluxQuantumAPEX` (NSSM service name confirmed above). Barbara's standing restart procedure (`nssm restart FluxQuantumAPEX` per `feedback_run_live_restart`) will be followed.
- No modification to network/firewall/RDP/Windows permissions (memory `feedback_server_access_incident`).
- No calibration-parameter edits — Barbara's framework §3 "Do NOT touch" list is enforced.
- No `--live` flag anywhere (memory `feedback_run_live_restart`).
- No live edits without local validation: every phase = backup ✓ → apply → `py_compile` → import probe → unit test.
- STOP-AND-ASK triggers per §2.6 respected.

---

## 5. STOP — Awaiting Claude (ML Engineer) ratification for Phase 1

Per §2 Hard Limit 1 and §5.5 of the sprint doc, ClaudeCode halts here until the ML Engineer ratifies the pre-flight report and authorises Phase 1 (Fix #3 — Canonical Publish Path).

**Next action on green-light from Claude:** open §5 Phase 1 discovery (`phase1_discovery.txt` via Select-String sweep).

**Next action on red-light:** halt; report reason; no changes applied.

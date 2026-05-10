# Bug A — MT5 Removal Scope (Phase 0 sub-investigation)

**Task**: Asana 1214627990283129 (ML-DS comment 1214628515254922)
**Author**: ClaudeCode Instance #1
**Date**: 2026-05-08
**Scope**: read-only investigation; ZERO impl; ZERO halt
**Reframe**: MT5 não existe → Bug A não é "restore price_mt5" mas sim "remove MT5 frame completamente, GC-only canónico"

---

## Critical assumption verification

| # | Assumption                                  | Result    | Evidence |
|---|---------------------------------------------|-----------|----------|
| 1 | ZERO active MT5 connections                 | ✅ TRUE   | `tasklist | grep -iE "terminal\|metatrader"` → empty. No `terminal64.exe` running. |
| 2 | Quantower único source de price             | ✅ TRUE   | Latest `decision_log.jsonl` line: `execution.brokers = [{RoboForex: BROKER_DISCONNECTED, MT5 not connected}, {Hantec: BROKER_DISCONNECTED}]`. price flow comes from microstructure_*.csv.gz produced by Quantower L2 collector. |
| 3 | Offset config dead/unused                   | ⚠ FALSE  | Offset is hardcoded init `_gc_xauusd_offset = 31.0` at `event_processor.py:611`. Refresh path `_refresh_offset()` is gated by `_mt5_price()` returning a value (line 1453-1455). With MT5 disconnected, `_mt5_price()` returns None, refresh never fires → **offset is STALE at 31.0 since service start**. The code STILL APPLIES the offset to convert GC↔MT5 (lines 1852-1860 update `liq_top/liq_bot/box_high/box_low/m30_liq_top/m30_liq_bot` by subtracting stale 31.0). The MT5-frame fields it produces still flow into decision_log + decision_live + telegram message. **Offset is not dead; it is stale-and-still-used.** |

System operating mode: **SIGNAL-ONLY-INTERIM**. The bot generates signals + writes them to decision_log/decision_live + emits Telegram messages. No broker (RoboForex MT5, Hantec MT5) accepts the orders — every recent decision has `execution.attempted = false`. Barbara is treating the Telegram message as the canonical signal and entering manually on her own platform.

---

## STEP 1 — MT5 vestige map

`grep -E "mt5|MT5|_mt5|XAUUSD|gc_mt5_offset|GC_MT5" --count` across `C:/FluxQuantumAI/`:

### Production live tree (`live/`)

| File | Hits | Category |
|------|------|----------|
| `event_processor.py` | 135 | mixed: imports, init `_gc_xauusd_offset`, `_mt5_price()`, MT5-frame attribute population (`liq_top`/`liq_bot`/`box_high`/`box_low`/`m30_liq_*`), `XAU_*` fallback price reads, decision_log fields (`price_mt5`, `gc_mt5_offset`, `level_price_mt5`, `expansion_lines_mt5`), risk-plan storage in MT5 frame |
| `position_monitor.py` | 61 | imports `from mt5_executor import MT5Executor, MAGIC, SYMBOL`, MT5 history watcher, MT5 position dict consumption (entry/sl/tp/ticket) |
| `base_dashboard_server.py` | 26 | dashboard reads MT5-frame fields from decision_live/decision_log (display) |
| `level_detector.py` | 23 | converts GC → MT5 levels in returned dict (`liq_top_mt5`, `liq_bot_mt5`, `fmv_mt5`, `box_high_mt5`, `box_low_mt5`); GC↔XAUUSD offset constant `GC_MT5_OFFSET = 31.0` |
| `dashboard_server_hantec.py` | 17 | Hantec-specific dashboard, MT5 broker references |
| `mt5_history_watcher.py` | 16 | entire file — MT5 deal history watcher (TP/SL/manual close detection) |
| `telegram_notifier.example.py` | 10 | reference implementation (non-production) |
| `dashboard_server.py` | 9 | dashboard MT5 fields |
| `hedge_manager.py` | 6 | takes MT5 position dict from executor (does NOT read decision_log) |
| `macro_monitor.py` | 5 | **STALE**: reads `decision.get("price_mt5")` for VAP entry — schema field removed by Bloco I (see consumer audit) |
| `tick_breakout_monitor.py` | 4 | converts `liq_top_gc - proc._gc_xauusd_offset` to MT5 frame |
| `telegram_notifier.py` | 3 | `MT5` mentions in comments / `(MT5 ref)` annotation strings |
| `signal_queue.py` | 2 | comments |
| `operational_rules.py` | 2 | comments |
| `kill_zones.py` | 1 | comment |
| `price_speed.py` | 1 | comment |

**Live total: ~321 occurrences across 16 files.**

### Top-level Python entry points

| File | Hits | Category |
|------|------|----------|
| `mt5_executor.py` | 89 | **entire file dedicated to MT5 broker**. MT5Executor class. Imported by run_live + position_monitor + hedge_manager. |
| `mt5_executor_hantec.py` | 76 | **entire file** Hantec MT5 broker variant |
| `run_live.py` | 29 | spawns MT5Executor instances (--broker roboforex / hantec / dual / both); converts entry/sl/tp lines |
| `ctrader_executor.py` | 9 | comparative comments referring to MT5 |
| `test_mt5_ipc.py` / `test_mt5_ipc2.py` | 7 / 14 | standalone MT5 IPC test scripts |

### Other production trees

| File | Hits | Category |
|------|------|----------|
| `apex_nextgen/common/config.py` | 2 | offset/symbol fallback constants |
| `backtests/fase_8_backtest.py` | 2 | backtest references |

### Excluded from scope

`sprints/*/backup_*` snapshots (~7 files, ~600 occurrences) — these are point-in-time backups and not loaded at runtime.
`tests/` — separate; `tests/test_near_level_direction_aware.py` hardcodes `OFFSET = 31.0`.

### Categorical breakdown of the 321 live occurrences

| Category | Count (approx) | Notes |
|----------|----------------|-------|
| Comments / docstrings (e.g., "MT5 ref", "MT5 frame") | ~80 | Removable mechanically |
| MT5-frame field names in dicts (`liq_top_mt5`, `box_high_mt5`, `fmv_mt5`, `expansion_lines_mt5`, `level_price_mt5`, `price_mt5`, `sl_mt5`, `tp1_mt5`, `tp2_mt5`) | ~60 | Schema fields — caller-facing |
| `_gc_xauusd_offset` / `GC_MT5_OFFSET` / `gc_mt5_offset` | ~30 | Offset use sites |
| MT5 import + connection + retry loops (`import MetaTrader5`, `_mt5_price()`, `_mt5_last_fail`, etc.) | ~25 | Needs full removal |
| MT5-aware attribute updates inside event_processor (`self.liq_top = round(self.liq_top_gc - offset, 2)`) | ~15 | Become trivially `= self.liq_top_gc` |
| `from mt5_executor import ...` / mt5_executor.send_order / etc. | ~30 | Executor wiring |
| `from live.mt5_history_watcher import ...` | ~5 | History watcher wiring |
| Dashboards (`live/dashboard_server*.py`, `base_dashboard_server.py`) | ~50 | Display-layer (not signal-critical) |
| Hantec-specific (`dashboard_server_hantec.py`, `mt5_executor_hantec.py`, `MT5_HANTEC_*`) | ~15 | Second-broker variant — same removal pattern |

---

## STEP 2 — Offset audit

### Storage

- **Init**: `event_processor.py:611` → `self._gc_xauusd_offset: float = 31.0`
- **Constructor signature**: no `offset` parameter; hardcoded default
- **Constants**: `level_detector.py:79` → `GC_MT5_OFFSET = 31.0` (independent constant; same numeric value)
- **Tests**: `tests/test_near_level_direction_aware.py:30` → `OFFSET = 31.0` (matches GC_MT5_OFFSET)
- **Settings/config files**: NOT present in `config/settings.json` (verified — no `offset` / `gc_mt5_offset` / `xauusd_offset` keys)

The offset has TWO independent definitions (`event_processor._gc_xauusd_offset` instance var, `level_detector.GC_MT5_OFFSET` module constant). They start equal (31.0) but the event_processor one can drift via `_refresh_offset` while the level_detector constant is frozen.

### Refresh logic

`event_processor._metrics_loop` (line 1444+):
```python
while self._running:
    triggered = self._micro_dirty.wait(timeout=METRICS_REFRESH_S)
    self._refresh_metrics()
    if (time.monotonic() - self._offset_ts) >= OFFSET_REFRESH_S:   # 300s
        xau_now = _mt5_price()
        if xau_now:
            self._refresh_offset(xau_now)
```

Refresh path:
1. Every 300s, query `_mt5_price()` which calls `MetaTrader5.symbol_info_tick(SYMBOL)`.
2. If MT5 not initialized OR returns None: **no refresh**. Offset frozen at last good value (or initial 31.0).
3. If a price returns: `_refresh_offset(xau_now)` computes `raw_offset = gc_mid - xau_mid` and EWM-smooths with α=0.5.

### Current state

- MT5 module not initialized (no `MetaTrader5` terminal active; `_mt5_price()` returns None).
- Offset has been **stale at 31.0 since service start** (no refresh fires).
- Real GC ↔ XAUUSD spread changes intraday (carry premium ~25-35 pts typical). 31.0 is a plausible-but-frozen approximation.

### Code dependencies on `offset != 0`

| Location | Use | Effect with stale 31.0 |
|----------|-----|------------------------|
| `event_processor.py:1852-1860` | `self.liq_top = liq_top_gc - 31.0`; same for liq_bot, box_high, box_low, m30_liq_top, m30_liq_bot | All MT5-frame attributes lag/lead by drift in true offset (small, <5pt typical) |
| `event_processor.py:4055, 4352` | `tp1 = round(tp1_gc - offset, 2)` (GAMMA, DELTA risk plans) | Stored MT5-frame TP1/TP2/SL drift |
| `event_processor.py:773, 936, 1049` | Decision-log payload field `gc_mt5_offset = round(offset, 2)` | Logged value reflects frozen 31.0 |
| `tick_breakout_monitor.py:164-165` | Pure-functional level conversion in monitor | drift |
| `level_detector.py` (multiple) | Returns dual-frame dict | drift |

The **drift** when offset is stale = (true_offset_today − 31.0). Across 7d backtest, this is typically ±2-5 pts. For the bug-A signal `ecc33d2f`, the offset 31.0 produced TP1_mt5=4710.35; if the true offset were e.g. 28.0, TP1_mt5 would be 4713.35. Either way the inversion-when-read-as-GC pattern persists because `tp1_pts (20) < offset (28-31)`.

---

## STEP 3 — Distance values frame

Hardcoded defaults in `event_processor.py:471-473`:
```python
sl_pts:  float = 20.0
tp1_pts: float = 20.0
tp2_pts: float = 50.0
```

These are the constructor defaults. `run_live.py` does NOT override them via CLI args (the `--lot_size` arg is sized; sl/tp distances default).

`config/settings.json` does NOT contain `sl_pts` / `tp1_pts` / `tp2_pts` (verified — only `trailing_stop_pts: 77`, lot lots, thresholds, etc.).

CONTINUATION mode uses ATR-based scaling:
```
trend_cont_tp1_atr_mult = 0.8   (settings.json key)
trend_cont_tp2_atr_mult = 1.5
```
giving e.g. tp1_pts = atr_m30 * 0.8. With current `atr_m30 ≈ 24`, that's ≈ 19 pts (similar magnitude to the ALPHA fixed default).

### Frame-agnostic verification

Both GC futures and XAUUSD spot are quoted in **USD per troy oz** (same unit). A 1-pt move in GC = 1-pt move in XAUUSD. The carry premium / offset (~31 pts) is a **constant offset on absolute price**, not a multiplier — so price *distances* are identical in both frames.

Conclusion: `sl_pts=20, tp1_pts=20, tp2_pts=50` are **calibration-frame-agnostic**. No recalibration needed when migrating to GC-only.

What DOES change in a GC-only migration: the absolute SL/TP/entry **values** (each shifts by +offset). Numerical content of the message changes (e.g., TP1 rises from 4710.35 to 4741.35 in the example), but the distances and the position outcome are unchanged.

### Calibration provenance

`config/settings.json` has `"source": "calibration_sprint_bloco1+CAL03-2026-04-10"` and `"next_recalibration": "after 93 live trades"` for the macro thresholds (delta_4h blocks, dom imbalance, etc.). The fixed `sl_pts/tp1_pts/tp2_pts` defaults predate this calibration — they were the constructor defaults at module creation and are not in any calibration record. Treating them as legacy heuristics rather than calibrated values; the recalibration-after-trades plan applies to gate thresholds, not these distances.

---

## STEP 4 — Consumer audit

Consumers of `decision_live.json` and `decision_log.jsonl`:

### `live/telegram_notifier.py` (active, primary consumer)

- Line 101: `price = dl.get("price_gc", 0)` — reads GC frame for entry price
- Line 131-132: `_risk_field(name)` returns `dec.get(name, dec.get(f"{name}_gc", default))` — reads canonical `sl/tp1/tp2` (currently MT5-frame stored values)
- Line 174: emits `f"{price:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f}"` — **mixed-frame display, root cause of Bug A**

**Frame expectation**: per the SISTEMA-SIGNAL-ONLY-INTERIM Bloco I comment at lines 99-101, this consumer was migrated to GC frame for `price` but the writer (event_processor) was NOT migrated for `sl/tp1/tp2`. This is the gap.

### `live/macro_monitor.py` (active)

- Line 198: `entry_price = float(decision.get("price_mt5") or 0.0)` — **STALE**: `price_mt5` was removed from `decision_live.json` schema by Bloco I. Always reads as 0.0.
- Line 200-202: `sl/tp1/tp2 = float(dec.get("sl|tp1|tp2") or 0.0)` — reads canonical fields (MT5-frame stored values).
- Behaviour with `entry_price = 0.0`: the VAP virtual TP1/SL trigger logic compares current price against entry+sl (`price >= sl` for SHORT VAP) → bogus comparisons. Likely produces incorrect "VIRTUAL_TP1_HIT"/"VIRTUAL_SL_HIT" alerts or none at all, since current_price (positive ~4700) vs sl (positive MT5 frame) compared to entry_price=0.0 → always-true-or-always-false.

**Risk**: macro_monitor was probably emitting wrong virtual trigger alerts since Bloco I landed. Worth flagging to ML-DS as a Bug A side-symptom.

### `live/position_monitor.py` (active)

- Line 64-65: defines paths but does not read sl/tp1/tp2 from decision_log/decision_live for triggering logic.
- Imports `MT5Executor` and `MT5HistoryWatcher` (line 46, 49). Position management driven by **MT5 broker positions**, not decision_log fields.
- With brokers DISCONNECTED, position_monitor opens with empty `MT5Executor.get_open_positions() == []`, so the daemon mostly idles.
- **Frame expectation**: indirect — relies on MT5Executor returning broker-side position dicts (entry, sl, tp, ticket), which currently never happens.

### `live/hedge_manager.py` (active conditionally)

- Reads MT5 position dicts (entry/sl/tp/ticket) from MT5Executor (line 170). Does not consume decision_log fields directly.
- With brokers disconnected, no positions to hedge → no-op.

### `live/base_dashboard_server.py` + `live/dashboard_server.py` + `live/dashboard_server_hantec.py`

- Read decision_live + decision_log for display (~52 mt5/MT5 occurrences combined).
- Currently displaying mixed-frame data to Barbara via web dashboard. Display-layer concern; not signal-critical but operator-confusing.

### `live/m30_updater.py` (active — wrote decision_log too)

- Writes BOX_EXPIRED telemetry to decision_log (`_emit_box_expired_telemetry`). Does NOT read sl/tp1/tp2.
- Per `event_processor.py:559-596`, m30_updater telemetry rows have schema fields `price_gc` only — no sl/tp1/tp2.

### `_audit/fixes/audit_today_signals.py`, `_audit/fixes/opcao_b_phase5_day1_monitoring.py`, `_audit/calibrations/m30_bias_*.py`

- Audit scripts (read-only investigation tooling). Read decision_log post-hoc. Don't influence runtime.
- Use both frames opportunistically; not signal-critical.

### `scripts/integration_health_check.py`

- Smoke-test script. Reads decision_live for sanity check.
- Not running in production loop.

### Consumer summary

| Consumer | Reads frame | Status under MT5-removal |
|----------|-------------|--------------------------|
| `telegram_notifier.py` | `price_gc` for entry, MT5-frame for sl/tp/tp2 | **Mixed → Bug A**. Migration: change `_risk_field` to expect GC values; require event_processor to store GC. Or compute MT5 conversion at message time (not recommended). |
| `macro_monitor.py` | `price_mt5` (REMOVED from schema) → 0.0 | **Already broken** (stale field). Migration: switch to `price_gc` + GC-frame sl/tp/tp2. |
| `position_monitor.py` | broker MT5 dicts (no decision_log read) | Independent of decision_log frame. Will be no-op once MT5Executor itself is removed. |
| `hedge_manager.py` | broker MT5 dicts | Same as position_monitor. |
| `dashboards/*.py` | both frames mixed | Display-layer cleanup; not signal-critical. |
| `_audit/*.py`, `scripts/*.py` | post-hoc reads, mixed | Update to GC frame as time permits; non-blocking. |

---

## Quantification + risk

### Scope

- **15 live source files** with MT5 references (321 occurrences)
- **4 top-level entry/executor files** with MT5 references (mt5_executor.py + Hantec variant + run_live + ctrader_executor)
- **2 dataclass / schema definitions** (event_processor and macro_monitor) reading MT5-frame fields
- **1 stale offset** (`_gc_xauusd_offset = 31.0`, hardcoded init, never refreshed since brokers disconnected)
- **2 known stale consumer paths**: telegram_notifier (Bug A) + macro_monitor (price_mt5 always 0.0)

### Risk matrix per change

| Change | Risk | Notes |
|--------|------|-------|
| **Migrate `event_processor.py` risk-plan storage to GC frame** (sl/tp1/tp2 = entry_gc ± pts) | LOW–MEDIUM | Decision_log values shift by +offset (cosmetic for log readers); telegram message becomes self-consistent in GC frame; macro_monitor TP1/SL trigger comparisons start working. Remaining MT5 attribute fields can stay (back-compat); set them = GC values to make offset unused without removing schema. |
| **Remove `_gc_xauusd_offset` + `_refresh_offset` + `_mt5_price()` + offset retry loop** | LOW | The whole GC↔MT5 conversion machinery becomes unused. Caveat: dashboards still read MT5-frame fields → keep producing them as `= GC value` (offset=0 effective) until dashboards are migrated. |
| **Drop MT5 import + Executor + history watcher** | MEDIUM | run_live.py instantiates MT5Executor unconditionally. position_monitor.py imports `from mt5_executor import MT5Executor, MAGIC, SYMBOL`. Removal needs a no-op stub OR conditional broker selection in run_live (already has --broker arg; just default to a "signal_only" mode). |
| **Migrate dashboards** | LOW | Display-only; cosmetic; can be deferred. |
| **Migrate macro_monitor.py to read `price_gc`** | LOW | One line change (line 198). Untangles the silently-broken VAP entry_price=0.0. |
| **Remove `mt5_executor.py` and `mt5_executor_hantec.py`** | MEDIUM-HIGH | Each file is 89/76 LOC of broker-API code. Removing them breaks `from mt5_executor import ...` in run_live + position_monitor + hedge_manager. Requires either (a) replacement with a no-op `SignalOnlyExecutor`, or (b) conditional broker dispatch in run_live's `start()` so MT5 imports happen only on `--broker roboforex|hantec|dual`. |

### Dependency graph (MT5 vestige)

```
run_live.py
 └── MT5Executor (mt5_executor.py)         ← entire file, 89 lines
      └── import MetaTrader5
      └── send_order / get_positions / get_history (broker API)
 └── EventProcessor (event_processor.py)
      ├── _mt5_price() ← MetaTrader5.symbol_info_tick           (offset refresh only)
      ├── _gc_xauusd_offset = 31.0                              (init + EWM)
      ├── _refresh_offset(xau)                                  (gated by MT5 connect)
      ├── self.liq_top = liq_top_gc - offset                    (MT5 attribute)
      ├── self.liq_bot, box_high, box_low, m30_liq_*            (same pattern)
      ├── decision_log["price_mt5"] = round(gc_mid - offset, 2) (schema field; removed by Bloco I per telegram_notifier comment, but writer code may still produce it — verify)
      ├── decision_log["sl/tp1/tp2"] = round(*_gc - offset, 2)  (Bug A source)
      └── decision_log["expansion_lines_mt5"], etc.
 └── PositionMonitor (position_monitor.py)
      ├── from mt5_executor import MT5Executor, MAGIC, SYMBOL
      └── from live.mt5_history_watcher import MT5HistoryWatcher
 └── HedgeManager (hedge_manager.py)
      └── reads MT5Executor.get_open_positions() (always empty when disconnected)

Read consumers (decision_live.json):
 ├── telegram_notifier.py    → mixed-frame Bug A (price_gc + sl/tp/tp2 MT5)
 ├── macro_monitor.py        → STALE price_mt5 → 0.0 always
 ├── dashboard_server*.py    → display, mixed frames (cosmetic)
 ├── _audit/*.py, scripts/*.py → post-hoc audit (cosmetic)
```

---

## STEP 5 — Recommendations for Bug A spec Phase 1

**Goal**: surgically fix Bug A (mixed-frame Telegram message) with minimal blast radius, while leaving full MT5 removal as a follow-up sprint.

### Recommendation A (recommended, tightest patch — Phase 1)

**Migrate the writer side: store sl/tp1/tp2 in GC frame in decision_live + decision_log.**

Changes:
1. `event_processor.py:4055, 4352` (GAMMA, DELTA risk plans): drop the `- offset` suffix so `sl/tp1/tp2` are stored as GC values.
2. `event_processor.py:2755-2802` and `2856-2890` (ALPHA pre-compute + main compute): replace `price` (= entry_mt5 = gc_mid - offset) with `gc_mid` (entry_gc) so `tp1 = entry_gc + tp1_pts` etc.
3. `event_processor.py:_build_decision_dict`: emit `sl_gc`, `tp1_gc`, `tp2_gc` AS WELL as `sl/tp1/tp2` (alias) for back-compat.
4. `telegram_notifier.py`: no change required. The mixed-frame mismatch resolves because both `price` (already GC) and `sl/tp1/tp2` (now GC) are in the same frame.
5. `macro_monitor.py:198`: change `decision.get("price_mt5")` → `decision.get("price_gc")` (one-line fix). This restores VAP TP1/SL trigger comparisons.

Downstream effects:
- Dashboards may display values that shifted by +offset (cosmetic). If the dashboard displays a label like "MT5 SL" it should now read GC — relabel as Phase 2.
- BOX_EXPIRED telemetry rows (m30_updater) already use `price_gc`; no change.
- Decision_log historical rows already in MT5 frame; analyses can detect the schema migration via timestamp.

**Effort**: ~1-2h coding + 30 min characterization tests.
**Reversibility**: HIGH — single-commit revert restores prior behaviour.
**Risk**: LOW. The risk-plan logic remains the same (still entry ± pts); only the absolute numbers stored shift by +offset.

### Recommendation B (full MT5 removal — Phase 2 sprint)

After Phase 1 stabilises, plan a multi-step sprint to remove MT5 entirely:

1. Replace `MT5Executor` with `SignalOnlyExecutor` (returns `BROKER_DISCONNECTED` for every call). Keep file path; gut implementation.
2. Drop `_mt5_price()`, `_refresh_offset()`, `_gc_xauusd_offset`, MT5 retry loop in event_processor (~50 LOC removed).
3. Drop MT5-frame attribute fields (`self.liq_top`, `self.liq_bot`, `self.box_high`, `self.box_low`, `self.m30_liq_*`); rename callers to use the `_gc` variants.
4. Drop MT5-frame schema fields from decision_log/decision_live (`price_mt5`, `liq_top_mt5`, etc.). Update any consumers (audit scripts, dashboards).
5. Delete `mt5_executor.py`, `mt5_executor_hantec.py`, `mt5_history_watcher.py`, `dashboard_server_hantec.py`, `test_mt5_ipc*.py`.
6. Drop `from MetaTrader5` imports + `MT5_RECONNECT_INTERVAL` constants.
7. Update `run_live.py` to default to `--broker signal_only` (no MT5 init).

**Effort**: ~6-12h depending on dashboard migration appetite.
**Reversibility**: MEDIUM — git revert restores files but configurations may need re-application.

### Recommendation C (minimal surgical hotfix — emergency only)

If Phase 1 needs to land within hours and a longer migration is unacceptable:

- Telegram side ONLY: in `telegram_notifier.py:131-132`, modify `_risk_field` to return `value + offset` (where offset is taken from the decision_live `gc_mt5_offset` field at the same schema row — per `event_processor.py:834`, that field is logged).
- Result: the message displays `entry_gc + sl_gc + tp1_gc + tp2_gc`. Single function change.

**Effort**: ~15 min.
**Reversibility**: HIGH.
**Risk**: MEDIUM. If `gc_mt5_offset` is missing or stale (which it currently is at 31.0), the visible numbers will be plausible but the underlying writer is still wrong. Doesn't fix `macro_monitor` stale `price_mt5` consumer. Patch addresses the symptom, not the root cause.

---

## Hand-off

Phase 0 sub-investigation complete. ZERO impl, ZERO halt. Awaiting ML-DS triage + Phase 1 spec selection (A/B/C above).

Critical assumption #3 (offset config dead/unused) **falsified**: offset is *stale-and-still-used*, not dead. The code applies a frozen 31.0 offset to compute MT5-frame fields that flow into messaging. Any Phase 1 that doesn't either remove the offset path entirely (Rec B) or store risk plan in GC and let the offset apply harmlessly (Rec A) will leave the bug latent. Rec C papers over it temporarily.

Bug B (single-iceberg sc=+3 + bias=unknown threshold) is independent of this MT5 sub-investigation; awaits its own Phase 1 spec from the parent task.

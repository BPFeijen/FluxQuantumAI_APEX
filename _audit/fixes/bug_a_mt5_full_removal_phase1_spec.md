# BUG-A-MT5-FULL-REMOVAL-SPRINT — Phase 1 spec (Rec B)

**Author**: ClaudeCode Instance #1
**Date**: 2026-05-08
**Asana**: Task 1214642985860665 (parent BUG-GO-LONG-TP1-INVERTED 1214627990283129) → ML-DS comment 1214642990569052 (Phase 1 spec drafting directive)
**Predecessors**:
- Phase 0 main investigation: `_audit/fixes/bug_tp1_inverted_bias_unknown_phase0_investigation.md`
- Phase 0 sub-investigation MT5 scope: `_audit/fixes/bug_a_mt5_removal_scope_phase0.md`
**Status**: DRAFT — pending ML-DS review

**Phase 0 schema-claim correction (errata)**: Phase 0 sub-investigation claimed `macro_monitor.py:198` reads `decision.get("price_mt5")` but Bloco I removed the field → `entry_price = 0.0`. Verified against current `decision_live.json` schema: `price_mt5` is **still emitted** by the writer at `event_processor.py:977,1053` (current value 4693.05 in the live snapshot). The Bloco I telegram_notifier comment is *aspirational, not yet shipped writer-side*. Therefore macro_monitor today reads a non-zero `entry_price`. The side-effect this spec must preserve is the inverse: when `price_mt5` is REMOVED by this sprint, macro_monitor will silently break unless migrated to `price_gc` in the same atomic deploy. F-3 (§3.5) addresses it.

---

## 1. Scope / Intent gap check

### In scope (this spec)

- **Full MT5 vestige removal** from `live/` runtime tree:
  - Drop MetaTrader5 imports, `_mt5_price()`, `_mt5_last_fail`, `_MT5_RECONNECT_INTERVAL`, `_refresh_offset()`, `_gc_xauusd_offset` instance var, `OFFSET_REFRESH_S` constant
  - Drop MT5-frame attribute fields on `EventProcessor`: `self.liq_top`, `self.liq_bot`, `self.box_high`, `self.box_low`, `self.m30_liq_top`, `self.m30_liq_bot`
  - Replace `MT5Executor` and `MT5ExecutorHantec` with a single **`SignalOnlyExecutor`** stub (returns `BROKER_DISCONNECTED` for every call)
  - Drop `live/mt5_history_watcher.py` (entire file)
  - Drop `live/dashboard_server_hantec.py` (entire file)
  - Drop standalone test scripts `test_mt5_ipc.py`, `test_mt5_ipc2.py`
- **Schema cleanup** in `decision_log.jsonl` + `decision_live.json` (writer-side, `event_processor._build_decision_dict` and `_write_stale_block_decision`):
  - Remove fields: `price_mt5`, `gc_mt5_offset`, `mt5_price` (heartbeat), `m30_box_mt5`, `m30_fmv_mt5`, `liq_top_mt5`, `liq_bot_mt5`, `level_price_mt5`, `expansion_lines_mt5`
  - Promote: `price_gc` becomes single `price` (with `price_gc` retained as alias for one release as backward-compat)
  - Risk-plan fields: `sl/tp1/tp2` migrated to GC frame (no more `entry_mt5 ± pts`); these fields ALREADY use the canonical names — no rename needed, only a frame migration
- **Consumer migrations**:
  - `live/telegram_notifier.py`: NO change required (already reads `price_gc`; will start receiving GC-frame `sl/tp1/tp2` and become self-consistent)
  - `live/macro_monitor.py:198`: 1-line fix `decision.get("price_mt5")` → `decision.get("price")` (with `price_gc` fallback)
  - `live/base_dashboard_server.py`, `live/dashboard_server.py`: switch reads from MT5-frame fields to GC fields. Display labels updated from "MT5" → no label or "GC".
  - `live/level_detector.py`: drop the `_mt5` suffixed fields from returned dict; keep `liq_top`, `liq_bot`, `fmv`, `box_high`, `box_low` as the GC-frame canonical names. Drop module constant `GC_MT5_OFFSET`.
  - `live/tick_breakout_monitor.py`: drop the `liq_top_gc - offset` conversion lines (164–165). Already-ported version in TradeATS is decoupled and not affected.
  - `live/event_processor.py`: write GC-frame canonical sl/tp1/tp2 directly (drop the `- offset` suffixes at lines 4055, 4352; replace `price` with `gc_mid` in ALPHA risk-plan branches at 2755-2802 / 2856-2890).
- **Calibration**:
  - Distances `sl_pts=20, tp1_pts=20, tp2_pts=50` are **frame-agnostic** (USD/oz unit, same in GC and XAUUSD) — NO recalibration. Verified in Phase 0 sub-investigation §3.
- **Test scaffolding**: ~10–15 new unit tests + regression suite + counterfactual replay (7-day decision_log).

### Out of scope (explicit non-goals)

- ❌ **ZERO** distance recalibration (frame-agnostic; sl/tp/tp2 numerical values change only by +offset which is a side-effect, not a calibration change).
- ❌ **ZERO touch BUG-SIGNAL-INVERTED Phase 5** (CC3 paralelo — m30_bias hysteresis observation period). CC3 owns `live/level_detector.py::_compute_signal_*`, `derive_m30_bias`, `derive_h4_bias`. This sprint touches `level_detector.py` only for the `_mt5`-suffix field cleanup and `GC_MT5_OFFSET` constant removal — NOT the bias derivation logic.
- ❌ **ZERO touch BUG-M30-STUCK-VS-H4-FLIP backlog**.
- ❌ **ZERO touch Bug B** (single-iceberg sc=+3 + bias=unknown threshold). Bug B waits for Bug A deploy + obs stable per ML-DS directive 1214628515254922.
- ❌ **ZERO cross-project**: NO touch to `C:\TradeATS\` (LFP-7 ports), `C:\FluxQuantumAI\apex_nextgen\` (NextGen), CC#2 cal_2 work.
- ❌ **ZERO halt** of FluxQuantumAPEX during dev (development on branch only; deploy via NSSM restart in Phase 4).
- ❌ **ZERO modification** of L2 collection chain (Quantower / iceberg / receiver / NSSM daemons) — they are the canonical price source and remain untouched.
- ❌ Dashboard label cosmetic polish beyond mechanical "_mt5" → "" rename (deferred to a later cleanup sprint).
- ❌ Conditional broker selection in `run_live.py --broker` argument: simplified to `--broker signal_only` only; multi-broker dispatch removed (RoboForex/Hantec branches dropped).

### Intent clarity

**Bug A behavior to fix**: Telegram message displays mixed-frame risk plan (entry in GC, sl/tp1/tp2 in MT5), making LONG TP1 appear below entry on 826/839 (98.5%) of LONG signals over the last 7 days. Operators cannot use the values verbatim on any platform; the partial migration that promoted `price_gc` in the message but left `sl/tp1/tp2` in MT5 frame is the proximate cause.

**Target outcome**: a single GC-canonical price frame across (a) decision_log/decision_live schema, (b) Telegram message, (c) dashboard, (d) macro_monitor virtual triggers, (e) all consumer audit scripts. The MT5 conversion machinery is removed, eliminating the offset-stale risk and the schema duplication that produced the bug. SignalOnlyExecutor preserves the brokers-disconnected operating mode without retaining live MT5 attempts.

---

## 2. Bug mechanism (preserved from Phase 0)

### 2.1 Mixed-frame Telegram message — proximate cause

Writer convention (`event_processor.py`):
- `price` (decision_live top-level) = `gc_mid - offset` = entry in MT5 frame (line 977: `"price_mt5": round(price, 2)` and line 978: `"price_gc": round(gc_mid, 2)`)
- `sl/tp1/tp2` (decision.*) = entry_mt5 ± pts = MT5 frame (line 4055, 4352, 2784/2788/2883/2889)

Reader convention (`telegram_notifier.py:101`):
- `price = dl.get("price_gc", 0)` — reads GC frame ✅ correct
- `_risk_field("sl|tp1|tp2")` returns `dec.get(name)` — reads MT5 frame ❌ mismatched

Numerical asymmetry: with offset = 31 and tp1_pts = 20 (default ALPHA), entry_mt5 + 20 = entry_gc - 31 + 20 = entry_gc - 11. TP1 lands BELOW entry when read against GC frame. SL (-20 in MT5) and TP2 (+50 in MT5) preserve sign vs entry_gc because their magnitudes (20, 50) and the offset (31) combine such that SL stays below entry and TP2 stays above. Only TP1 flips. This is a numerical coincidence of the offset value vs the default TP1 distance.

### 2.2 7-day cross-reference

| Direction | Total GO | Inverted (TP1 vs price_gc) | Rate |
|-----------|----------|----------------------------|------|
| LONG | 839 | 826 | 98.5% |
| SHORT | 1133 | 0 | 0.0% |

**LONG-only systemic.** The 13 non-inverted LONGs are CONTINUATION-mode entries where `tp1_pts = atr_m30 × 0.8`; if `atr_m30 > 38.75`, `tp1_pts > offset` and the inversion vanishes.

### 2.3 Side-effect — macro_monitor coupled to schema

`macro_monitor._build_vap_from_decision` reads `decision.get("price_mt5")` for `entry_price` of the Virtual Active Position. Today the field exists and returns a real value (verified live snapshot `price_mt5: 4693.05`). After this sprint removes `price_mt5`, the consumer would silently default to 0.0 unless migrated. F-3 (§3.5) makes the migration atomic with the schema change.

### 2.4 Offset frozen at 31.0

`event_processor._gc_xauusd_offset = 31.0` (line 611). Refresh path (`_refresh_offset` line 1452-1455) gated on `_mt5_price()` returning a real tick — gated on a live MT5 connection. With MT5 unconfigured, the offset is stale at the init value. Code still applies the stale offset to compute every MT5-frame field (lines 1852-1860). Removing the offset path entirely (F-1) eliminates the stale-and-still-used hazard.

### 2.5 Critical assumptions verified (Phase 0 sub-investigation)

| # | Assumption | Result |
|---|-----------|--------|
| 1 | ZERO active MT5 connections | ✅ verified — no `terminal64.exe`, all decisions show `BROKER_DISCONNECTED` |
| 2 | Quantower único source | ✅ verified — microstructure_*.csv.gz feed |
| 3 | Offset config dead | ⚠ **falsified** — stale-and-still-used. F-1 removes the path. |

---

## 3. Fix design — Rec B (full removal)

The 321 live occurrences map to four buckets:

| Bucket | Count (approx) | Treatment |
|--------|----------------|-----------|
| **REMOVE** | ~140 | Delete: imports, `_mt5_price`, `_refresh_offset`, `_gc_xauusd_offset`, MT5-frame attribute updates, `mt5_executor.py`, `mt5_executor_hantec.py`, `mt5_history_watcher.py`, `dashboard_server_hantec.py`, `test_mt5_ipc*.py` |
| **MIGRATE** | ~80 | Schema field renames, consumer reads (telegram_notifier, macro_monitor, dashboards, level_detector return-dict trimming, run_live `--broker` simplification) |
| **KEEP-AS-COMMENT** | ~40 | Docstrings / banners explaining historical context (e.g., "Bloco I migrated frame to GC") — preserved as forensic record |
| **KEEP-AS-IS** | ~60 | `XAUUSD` symbol references in MT5Executor (now SignalOnlyExecutor) docstrings; comparative comments in `ctrader_executor.py`; tests references in `tests/test_*.py` (untouched per opcao_b precedent) |

### 3.1 Helpers / new code

**File: `mt5_executor.py` (replaced, NOT deleted to preserve import path)**

```python
# Before: ~89 LOC of MetaTrader5 broker API calls
# After:
"""SignalOnlyExecutor — replaces MT5Executor stub for SIGNAL-ONLY-INTERIM mode.

All methods return BROKER_DISCONNECTED. Preserved file path so existing imports
(run_live.py, position_monitor.py, hedge_manager.py) continue to resolve. To
re-enable broker integration, replace this stub with a real executor.
"""
from __future__ import annotations
from dataclasses import dataclass

MAGIC = 12345
SYMBOL = "XAUUSD"   # historical naming preserved; not used (no broker)
LOT_SIZE = 0.05
MIN_LOT = 0.01

def _split_lots(total: float) -> tuple[float, float, float]:
    """Preserve helper signature used by event_processor / position_monitor."""
    return (round(total * 0.4, 2), round(total * 0.4, 2), round(total * 0.2, 2))


@dataclass
class _ExecResult:
    state: str = "BROKER_DISCONNECTED"
    ticket: int | None = None
    error_text: str = "Signal-only mode: no broker configured"


class MT5Executor:
    """Stub. Returns BROKER_DISCONNECTED for every call. Same name so callers
    don't change. Add a `signal_only=True` attribute for callers that want
    to optimise the cycle when no broker exists."""
    signal_only: bool = True

    def __init__(self, *args, **kwargs):
        pass

    def get_open_positions(self) -> list[dict]:
        return []

    def send_order(self, *args, **kwargs) -> _ExecResult:
        return _ExecResult()

    def close_position(self, *args, **kwargs) -> _ExecResult:
        return _ExecResult()

    def modify_position(self, *args, **kwargs) -> _ExecResult:
        return _ExecResult()
```

**File: `mt5_executor_hantec.py` (replaced — same pattern, MT5ExecutorHantec class)**

The dual-broker mode in `run_live.py` is removed; only `MT5Executor` (= SignalOnlyExecutor) remains. Hantec stub kept for one release, marked deprecated.

### 3.2 F-1 — `event_processor.py` GC-canonical migration

Drop these in `event_processor.py`:

| Lines | Change |
|-------|--------|
| 258–264 | Drop `import MetaTrader5` block (top-level try/except) |
| 266–267 | Drop `_mt5_last_fail`, `_MT5_RECONNECT_INTERVAL` |
| 270–298 | Drop `_mt5_price()` function entirely |
| 251 | Drop `OFFSET_REFRESH_S` constant |
| 302–304 | Drop `_compute_offset` helper |
| 481, 497, 544–547 | Drop comments and the MT5-attribute init for `liq_top/liq_bot/box_high/box_low` |
| 611 | Drop `self._gc_xauusd_offset: float = 31.0` |
| 772–773, 935–936, 1048–1049 | Drop local `offset = self._gc_xauusd_offset` reads |
| 832–833 | Drop `"mt5_price"` heartbeat field |
| 977 | Drop `"price_mt5"`; keep `"price_gc"`; ALSO emit aliasing `"price"` = price_gc for one release |
| 979 | Drop `"gc_mt5_offset"` |
| 986–990, 999, 1062–1066, 1076 | Drop `m30_box_mt5`, `m30_fmv_mt5`, `liq_top_mt5`, `liq_bot_mt5`, `level_price_mt5`. Keep `liq_top_gc`, `liq_bot_gc`. |
| 1030 | Drop `expansion_lines_mt5`; replace with `expansion_lines` (same content from same source, but renamed). Existing `_exp_lines` is already in GC frame — verify in implementation. |
| 1134 | Replace `price = float(self._metrics.get("gc_mid", 0.0) - self._gc_xauusd_offset)` with `price = float(self._metrics.get("gc_mid", 0.0))` |
| 1241 | Drop the `import MetaTrader5 as _mt5_mod` (used by the offset retry path) |
| 1438–1455 | `_metrics_loop`: drop the offset calibration block (lines 1450–1455) |
| 1713–1715 | Drop `self.m30_liq_top = ... - offset` / `self.m30_liq_bot = ... - offset`; rename callers to use `m30_liq_top_gc` / `m30_liq_bot_gc` |
| 1812 | Drop `price=float(self._metrics.get("xau_mid", 0.0) or 0.0)` (xau_mid was set by MT5 path) |
| 1825–1863 | Drop `_refresh_offset` method entirely |
| 2755–2802 (ALPHA pre-compute), 2856–2890 (ALPHA main compute), 4055 (GAMMA), 4352 (DELTA) | Replace `price` with `gc_mid` (entry_gc) in TP1/TP2/SL formulas; drop the `- offset` terms; ensure stored `sl/tp1/tp2` are GC-frame |
| 3263, 3289–3290, 3295–3296, 3699, 3790, 3794, 3816 | Drop `xau_price = self._metrics.get("xau_mid", ...)` overextension reads (BUG B-adjacent code; preserve the higher-level overextension logic but use `gc_mid` instead) |
| 4456 | Drop `self._metrics["xau_mid"] = xau_price` (no caller after MT5 removed) |
| 4842 | Drop `xau_now = _mt5_price()` (any remaining call) |

LOC delta in `event_processor.py`: estimate **−180 / +30 = −150 net** (removals dominate; small additions for `price` alias keying).

### 3.3 F-2 — `level_detector.py` GC-canonical return dict

| Lines | Change |
|-------|--------|
| 79 | Drop `GC_MT5_OFFSET = 31.0` |
| 22–50 (return-dict docstring) | Drop `_mt5` keys; document GC-only return |
| ~213, ~246 (M30 bias derivation banner) | Untouched (CC3 zone — out of scope) |
| ~256–270 (return-dict population) | Remove keys ending in `_mt5`; promote GC-frame versions to plain names |

LOC delta: **−25 / +5 = −20**.

### 3.4 F-3 — `macro_monitor.py` consumer migration

| Lines | Change |
|-------|--------|
| 198 | `entry_price=float(decision.get("price_mt5") or 0.0)` → `entry_price=float(decision.get("price_gc") or decision.get("price") or 0.0)` |
| 79–80 | Update VAP dataclass docstring: "from decision.sl (GC frame)" |

LOC delta: **+1 line modified**.

### 3.5 F-4 — Dashboards (`base_dashboard_server.py`, `dashboard_server.py`)

| Files | Change |
|-------|--------|
| `base_dashboard_server.py` (26 hits) | Replace MT5-frame field reads with GC reads; remove the `(MT5)` and `(GC)` dual-display columns; show single GC frame |
| `dashboard_server.py` (9 hits) | Same pattern |
| `dashboard_server_hantec.py` (17 hits) | **Delete entire file** (Hantec broker dropped) |

LOC delta: **−40 / +10 = −30** (display-layer cleanup).

### 3.6 F-5 — `tick_breakout_monitor.py`

| Lines | Change |
|-------|--------|
| 164–165 | Drop the `proc.liq_top = round(liq_top_gc - proc._gc_xauusd_offset, 2)` and `_bot` lines; the live monitor receives GC levels and pushes them directly. Note: the **TradeATS port** at `C:/TradeATS/decision/execution/tick_breakout_monitor.py` is decoupled (no `_proc` mutation) and unaffected. |

LOC delta: **−4 / +0 = −4**.

### 3.7 F-6 — `run_live.py` broker arg simplification

| Lines | Change |
|-------|--------|
| ~29 occurrences of `mt5/MT5` | Remove `--broker roboforex|hantec|dual|both` cases; collapse to single SignalOnlyExecutor |
| Argparse | Remove `--broker` option entirely; default is signal-only |
| LOC delta | **−40 / +5 = −35** |

### 3.8 F-7 — File deletions

| File | Action | LOC removed |
|------|--------|-------------|
| `mt5_executor.py` | **Replace contents** with SignalOnlyExecutor stub (~50 LOC) | 89 → 50, net −39 |
| `mt5_executor_hantec.py` | **Replace contents** with stub (~30 LOC, mark deprecated) | 76 → 30, net −46 |
| `live/mt5_history_watcher.py` | **Delete entire file** | −500 (estimated full file) |
| `live/dashboard_server_hantec.py` | **Delete entire file** | −400 (estimated) |
| `test_mt5_ipc.py`, `test_mt5_ipc2.py` | **Delete both** | −150 (estimated) |
| `live/telegram_notifier.example.py` | KEEP (reference only, not loaded at runtime) | 0 |

### 3.9 Schema migration summary

| Field | Before | After |
|-------|--------|-------|
| `price_mt5` (top-level) | `gc_mid - offset` | **REMOVED** |
| `price_gc` (top-level) | `gc_mid` | **KEPT** + alias `price` |
| `gc_mt5_offset` (top-level) | `31.0` (stale) | **REMOVED** |
| `mt5_price` (heartbeat) | `gc_mid - offset` | **REMOVED** |
| `m30_box_mt5` (context) | `[box_low_mt5, box_high_mt5]` | **REMOVED** (use `liq_top_gc/liq_bot_gc` already present) |
| `m30_fmv_mt5` (context) | `null` | **REMOVED** |
| `liq_top_mt5`, `liq_bot_mt5` (context) | `liq_top_gc - offset` | **REMOVED** |
| `level_price_mt5` (trigger) | `liq_top - offset` or `liq_bot - offset` | **RENAMED** to `level_price` (GC value) |
| `expansion_lines_mt5` (top-level) | `[mt5 values]` | **RENAMED** to `expansion_lines` (GC values) |
| `decision.sl`, `decision.tp1`, `decision.tp2` | MT5 frame | **MIGRATED** to GC frame (same field names) |

Schema version bump: `message_semantics_version = "v1_canonical"` → `"v2_gc_canonical"` to flag the cutover for downstream parsers.

---

## 4. Methodology

### 4.1 Quantower único source verification (G-DELETE-DEPENDENCY-CHECK)

Before deleting MetaTrader5 imports, confirm:
1. Quantower L2 collector writes microstructure CSV → `event_processor._refresh_metrics` reads `mid_price` (GC) — **already the case**
2. No other module reads `xau_mid` from `_metrics` after F-1 — **verify via grep post-edit**
3. `_mt5_price()` has zero callers after F-1 — **verify via grep post-edit**

### 4.2 GC canonical contract

**Single price frame** across the full pipeline:
- L2 collector → microstructure_*.csv.gz (GC)
- event_processor.refresh_metrics → `gc_mid` (GC)
- decision dict → `price` / `price_gc` / `sl` / `tp1` / `tp2` / `liq_top_gc` / `liq_bot_gc` (all GC)
- telegram_notifier message → reads price_gc + sl/tp1/tp2 (GC)
- macro_monitor VAP → entry_price = price_gc, sl/tp1/tp2 = GC
- dashboards → display GC

### 4.3 G-DELETE-DEPENDENCY-CHECK (per file)

For each file modified/deleted:
1. `grep -r "from <module> import" .` — list all importers
2. Confirm each importer is also modified in this sprint OR uses a still-exported symbol
3. Verify Python compile pass: `python -m py_compile <file>` after edit
4. Verify `from live.event_processor import EventProcessor` etc. still resolves at runtime

---

## 5. Edge cases

### 5.1 Backward-compat: stale historical decision_log entries

`decision_log.jsonl` has 1972+ historical rows in the OLD MT5-frame schema. After deploy, new rows are GC-frame. Audit scripts and replay tools must handle both:

```python
def get_price(d: dict) -> float:
    """Read entry price from any-vintage decision_log row."""
    return d.get("price") or d.get("price_gc") or d.get("price_mt5") or 0.0
```

A standalone helper `_audit/fixes/decision_log_compat.py` ships with the Phase 1 spec to centralise the back-read logic. Existing audit scripts (`audit_today_signals.py`, `opcao_b_phase5_day1_monitoring.py`) update to use it.

### 5.2 Active signals transition (in-flight at deploy moment)

NSSM restart of FluxQuantumAPEX cuts current process; a new one starts. In-flight states:
- **Decision being computed**: aborted by SIGTERM. Next signal cycle starts fresh under new schema. No partial write (writer is atomic via `.tmp` rename — verify in implementation).
- **Telegram message half-sent**: notify_decision is synchronous on a single decision_id; cooldown anti-spam prevents duplicate. Worst case: same signal-id message sent twice across restart, second under new schema. Operator-visible duplicate, not data loss.
- **Decision_live.json mid-write**: writer uses tmp-rename atomic. Reader (`telegram_notifier`) tolerates JSON parse failure silently. Worst case: one missed Telegram tick.

### 5.3 Telegram in-flight

Operator may have a manual position open at the broker side (treated as Barbara's manual mirror) at the moment of restart. Schema change does not affect already-open positions; they are tracked entirely outside FluxQuantumAPEX. No coupling.

### 5.4 Auto-rollback triggers

During Phase 4 deploy (5-min observation window), trigger automatic rollback (NSSM stop + git revert + NSSM start) if any of:
- ✗ `python -c "import live.event_processor"` fails after restart (import error)
- ✗ `decision_live.json` not modified for >120s post-restart (writer dead)
- ✗ NSSM service exits within 60s of start (crash loop)
- ✗ counterfactual: any GO LONG signal with `tp1 < price_gc` in first 5min (regression of Bug A)
- ✗ counterfactual: any decision row with both `price_mt5` and `price` (schema dual-emit bug)

Rollback procedure: see §8.4.

### 5.5 macro_monitor virtual TP1/SL — non-zero entry preservation

Pre-sprint: `entry_price = price_mt5` (real MT5 value).
Post-sprint: `entry_price = price_gc` (GC value, ~+31 different).

Active VAPs at deploy moment: discarded on restart (macro_monitor in-memory state is NOT persisted). Restart reseeds from new GO signals. No corruption risk.

If the spec deploys without F-3 (macro_monitor migration), entry_price would be 0.0 (price_mt5 absent) and virtual TP1/SL triggers would fire spuriously. F-3 + schema migration must ship atomically. The deploy script verifies F-3 is present before NSSM start.

---

## 6. Test plan

### 6.1 Unit tests (NEW, ~10–15)

Location: `tests/test_bug_a_mt5_full_removal.py` (new file).

```python
# Schema
def test_decision_dict_has_no_mt5_fields()
def test_decision_dict_has_price_alias()
def test_decision_dict_schema_version_v2_gc_canonical()

# Risk plan in GC frame
def test_alpha_long_tp1_above_entry_gc()
def test_alpha_short_tp1_below_entry_gc()
def test_alpha_long_sl_below_entry_gc()
def test_alpha_short_sl_above_entry_gc()
def test_continuation_long_atr_based_tp_in_gc()
def test_gamma_long_tp1_in_gc()
def test_delta_short_tp1_in_gc()

# SignalOnlyExecutor
def test_signal_only_executor_send_order_returns_broker_disconnected()
def test_signal_only_executor_get_positions_empty()
def test_signal_only_executor_split_lots_unchanged()

# macro_monitor F-3
def test_macro_monitor_reads_price_gc()
def test_macro_monitor_falls_back_to_price_alias()

# event_processor MT5 stripping
def test_event_processor_no_mt5_attributes()
def test_event_processor_no_offset_refresh_thread()

# Backward-compat
def test_decision_log_compat_reads_old_mt5_schema()
def test_decision_log_compat_reads_new_gc_schema()
```

### 6.2 Regression suite

Existing tests must continue to pass:
- `tests/test_impl4_anti_exit.py`
- `tests/test_macro_monitor.py`
- `tests/test_near_level_direction_aware.py` — must update `OFFSET = 31.0` constant since GC↔MT5 conversion is gone (or re-baseline test fixtures to GC-only)
- `tests/test_get_daily_trend_ensemble.py`
- `tests/test_alignment_ensemble.py`

### 6.3 Counterfactual replay (7-day decision_log)

Script: `_audit/fixes/bug_a_counterfactual_replay.py` (new).

For each historical decision_log row in last 7 days:
1. Re-extract: direction, entry_gc, sl_gc, tp1_gc, tp2_gc (using compat helper for old schema)
2. Recompute under new logic: `tp1_new = entry_gc + tp1_pts` (LONG) or `entry_gc - tp1_pts` (SHORT)
3. Assert per row:
   - LONG: `tp1_new > entry_gc` (sign-correct)
   - SHORT: `tp1_new < entry_gc` (sign-correct)
   - SL distance unchanged (frame-agnostic)
   - TP2 distance unchanged
4. Tabulate: 100% pass expected. Any failure = bug in the new code path.
5. Macro_monitor virtual TP1/SL: simulate VAP creation with new schema; assert `entry_price > 0` (no longer 0.0).

Acceptance gate: counterfactual replay shows 100% LONG TP1 > entry_gc (vs current 1.5%) AND 100% SHORT TP1 < entry_gc (vs current 100%, preserved).

### 6.4 Smoke test

Post-restart smoke runs (30s):
- `decision_live.json` exists, parseable JSON
- Top-level keys: `["timestamp", "price", "price_gc", "context", "trigger", "gates", "decision", ...]`
- NO keys: `price_mt5`, `gc_mt5_offset`, `m30_box_mt5`, etc.
- Heartbeat row in `decision_log.jsonl` within 30s of restart, schema v2

---

## 7. Implementation

### 7.1 Branch

```
git checkout -b deploy/bug-a-mt5-full-removal-2026-05-08
```

Base: `main` (or current production HEAD). Verify CC3's `deploy/bug-signal-inverted-2026-05-06` is NOT touched.

### 7.2 8-step sequence (file-by-file)

| Step | File | Action | LOC delta | Time |
|------|------|--------|-----------|------|
| 1 | `_audit/fixes/decision_log_compat.py` | NEW: backward-read helper | +30 | 10min |
| 2 | `mt5_executor.py` | REPLACE with SignalOnlyExecutor stub | −39 net | 20min |
| 3 | `mt5_executor_hantec.py` | REPLACE with stub | −46 net | 10min |
| 4 | `event_processor.py` | F-1: remove MT5 paths, GC-canonical writer | −150 net | 60min |
| 5 | `level_detector.py` | F-2: drop _mt5 fields + GC_MT5_OFFSET | −20 net | 15min |
| 6 | `macro_monitor.py` | F-3: 1-line consumer migration | +1 | 5min |
| 7 | `dashboards/*.py` (3 files) | F-4: GC-only display + delete Hantec dashboard | −30 net | 30min |
| 8 | `tick_breakout_monitor.py` | F-5: drop offset conversion | −4 | 5min |
|   | `run_live.py` | F-6: simplify --broker arg | −35 net | 15min |
|   | `live/mt5_history_watcher.py` | DELETE | −500 | 5min |
|   | `tests/test_*.py` | Add new test file + update fixtures | +250 / +30 update | 60min |
|   | `_audit/fixes/bug_a_counterfactual_replay.py` | NEW | +120 | 30min |

**Total LOC delta**: ~−1300 net (heavy removal).
**Total implementation time**: ~4-5h coding + 30min py_compile + 30min running unit tests = **~5-6h**.

### 7.3 Per-step verification (gate)

After each step:
- `python -m py_compile <file>` PASS
- `pytest tests/test_<related>.py -v` PASS for related test (if exists)
- `git diff --stat` matches estimate (no surprise unrelated changes)
- BANLIST cross-check (`xau_mid|_detect_local_exhaustion|TRENDING.DISABLED`): unchanged behaviour

If any step fails, STOP. Do not proceed. Investigate. Phase 1 spec assumes implementation in step order; out-of-order edits may break compile.

---

## 8. Deploy

### 8.1 Pre-deploy baseline (~5min)

1. Snapshot current `decision_log.jsonl` last 1000 rows → `_audit/baselines/decision_log_pre_bug_a_removal.jsonl`
2. Snapshot current `decision_live.json` → `_audit/baselines/decision_live_pre_bug_a_removal.json`
3. NSSM service state: `sc query FluxQuantumAPEX` → record current state
4. `git rev-parse HEAD` → record SHA on prod branch
5. `git stash` any uncommitted (must be clean for rollback)

### 8.2 Smoke + counterfactual gate (pre-restart, ~10min)

1. **Smoke**: `python -m py_compile live/event_processor.py live/macro_monitor.py live/level_detector.py live/telegram_notifier.py mt5_executor.py mt5_executor_hantec.py`
2. **Unit tests**: `pytest tests/test_bug_a_mt5_full_removal.py -v` → 100% PASS
3. **Regression**: `pytest tests/test_macro_monitor.py tests/test_impl4_anti_exit.py tests/test_near_level_direction_aware.py -v` → 100% PASS (or expected fixture updates)
4. **Counterfactual**: `python _audit/fixes/bug_a_counterfactual_replay.py --decision-log $BASELINE` → 100% LONG TP1 > entry_gc AND 100% SHORT TP1 < entry_gc → emit signed report
5. **Schema cleanup**: `grep -E "\"price_mt5\"|\"gc_mt5_offset\"" live/event_processor.py` → 0 matches

If ANY step FAILS, STOP. Do not restart NSSM.

### 8.3 NSSM restart + 5-min observation

```
sc stop FluxQuantumAPEX        # ~5s
# wait until STATE: STOPPED
sc start FluxQuantumAPEX       # ~5s
# wait until STATE: RUNNING
```

5-min observation window:
1. **Decision_live.json**: modified time updates within 60s of start (writer alive)
2. **Decision_log.jsonl**: at least 1 new row appended within 60s
3. **Schema check (live)**: parse latest row; assert NO `price_mt5`, NO `gc_mt5_offset`; HAS `price` or `price_gc`
4. **Telegram check (live)**: any GO LONG signal in window must have `tp1 > price_gc`
5. **Heartbeat**: at least 5 heartbeat rows in 5min
6. **No exception in nssm logs**: `Get-Content C:\FluxQuantumAI\logs\stderr.log -Tail 50` → no Python tracebacks

### 8.4 Auto-rollback

Triggers (any of, see §5.4): import fail, writer dead, crash loop, schema regression, TP1 inversion regression.

Procedure:
```
sc stop FluxQuantumAPEX
git revert HEAD --no-edit
git push origin deploy/bug-a-mt5-full-removal-2026-05-08
sc start FluxQuantumAPEX
# Wait 60s for service health
# Verify decision_live.json has price_mt5 again (=> rollback effective)
```

Rollback time budget: ~2min from trigger to service-back-up.

### 8.5 Post-deploy 24h observation (Phase 5, separate task)

Tracked in subsequent task per ML-DS workflow. Out of scope for Phase 1 spec.

---

## 9. Risk

### 9.1 Risk matrix

| Category | Risk | P | Impact | Mitigation |
|----------|------|---|--------|------------|
| **Schema break** | Audit scripts crash on missing `price_mt5` | M | Low | `decision_log_compat.py` helper; migrate 4 known scripts in same PR |
| **Macro_monitor silent break** | If F-3 missed, virtual TP1/SL fire on entry_price=0.0 | L | Medium | F-3 ships in same PR; smoke test verifies entry_price > 0 |
| **Telegram message wrong** | Message displays inconsistent values during in-flight transition | L | Low | NSSM restart is atomic; cooldown anti-spam mitigates duplicates |
| **Compile error post-merge** | One of 8 step edits leaves dangling import | L | High | Per-step py_compile gate; smoke test before restart |
| **Counterfactual regression** | New writer produces inverted TP1 in some edge case | VL | High | Unit tests cover ALPHA / GAMMA / DELTA / CONTINUATION branches; counterfactual replay 7d gates deploy |
| **Live position desync** | Operator's open broker position semantics change mid-deploy | L | Low | No-broker mode means no live positions to desync; operator reads new Telegram message and adjusts manually |
| **Cross-project regression** | TradeATS or NextGen breaks | VL | Medium | Sprint scope is FluxQuantumAI/live + top-level only; ports in TradeATS already decoupled |
| **Hantec broker re-enable later** | Future need to re-add Hantec; we deleted it | L | Low | Stub kept; can be re-implemented in `mt5_executor_hantec.py` from git history |
| **Stale offset hidden bug** | Some path still uses `_gc_xauusd_offset = 31.0` after F-1 | L | Medium | Final grep gate before deploy; py_compile catches NameError |

### 9.2 Side-effect preservation matrix

| Side effect | Pre-sprint | Post-sprint | Preservation |
|-------------|------------|-------------|--------------|
| `decision_live.json` modified every cycle | ✅ | ✅ | preserved (writer same cadence) |
| `decision_log.jsonl` rotation | ✅ | ✅ | preserved |
| `tp1_pts/tp2_pts/sl_pts` distance | 20/50/20 | 20/50/20 | preserved (frame-agnostic) |
| Telegram cooldown anti-spam | ✅ | ✅ | preserved |
| BOX_EXPIRED telemetry from m30_updater | ✅ | ✅ | preserved (untouched module) |
| Macro_monitor VAP lifecycle | active (non-zero entry, but mixed-frame trigger) | active (GC-frame, correct trigger) | improved |
| Position monitor (no-broker mode) | no-op | no-op | preserved |
| Hedge manager (no-broker mode) | no-op | no-op | preserved |
| L2 collection chain | active | active | UNTOUCHED |
| Quantower receiver | active | active | UNTOUCHED |
| ATS Trend Line / NextGen / TradeATS | unaffected | unaffected | UNTOUCHED |

### 9.3 Bug B preservation

This sprint MUST NOT change Bug B behaviour. Specifically:
- `MIN_SCORE_GO = 0` in `ats_live_gate.py:220` — UNTOUCHED
- Bias-unknown handling at `event_processor.py:2560-2585` — UNTOUCHED
- Single iceberg sc=+3 GO dispatch — UNTOUCHED

After Bug A deploys + 24h obs stable, Bug B Phase 0/1 begins per ML-DS workflow.

### 9.4 BUG-SIGNAL-INVERTED Phase 5 preservation

CC3 owns the m30_bias hysteresis observation period on `deploy/bug-signal-inverted-2026-05-06` branch. Sprint MUST:
- NOT modify `live/level_detector.py::derive_m30_bias`, `_classify`, `_voting_vote`, `_compute_signal_a/b/c`
- Touch `level_detector.py` ONLY for `_mt5` field cleanup + `GC_MT5_OFFSET` constant removal (mechanical rename)
- NOT modify `live/regime_detectors.py`
- NOT modify CC3's calibration scripts in `_audit/calibrations/`

---

## 10. Constraints honored

- ✅ **ZERO impl** during Phase 1 — this document is spec only
- ✅ **ZERO halt** of FluxQuantumAPEX during dev — branch development isolated
- ✅ **ZERO touch BUG-SIGNAL-INVERTED Phase 5** (CC3) — CC3 zone untouched
- ✅ **ZERO touch BUG-M30-STUCK-VS-H4-FLIP backlog**
- ✅ **ZERO touch Bug B** — Bug B work waits for Bug A deploy stable
- ✅ **ZERO cross-project** — TradeATS, NextGen, CC#2 cal_2 untouched
- ✅ **ZERO recalibration of distances** — frame-agnostic, sl_pts/tp1_pts/tp2_pts unchanged
- ✅ **L2 collection NEVER interrupted** — Quantower, NSSM L2 daemons untouched

---

## 11. Approval workflow

```
Phase 1 (this spec)              ETA ~30-45min     ← current state
  └→ ML-DS review                ETA ~20min
       └→ Barbara approval       (decision)
            └→ Phase 3 impl      ETA ~5-6h         (8-step sequence per §7.2)
                 └→ Phase 4 deploy  ETA ~1h        (§8 gates + restart + 5min obs)
                      └→ Phase 5 24h obs    (separate task)
```

Phase 1 spec approval gate: ML-DS confirms (a) §3 fix design is complete and surgical, (b) §5 edge cases are exhaustive, (c) §6 test plan + counterfactual covers Bug A regression detection, (d) §9 risks have valid mitigations, (e) §3.7 file deletions are not premature.

---

## 12. Status

**Phase 1 spec status**: DRAFT — ready for ML-DS review.

**Predecessors satisfied**:
- ✅ Phase 0 main investigation complete (`_audit/fixes/bug_tp1_inverted_bias_unknown_phase0_investigation.md`)
- ✅ Phase 0 sub-investigation MT5 scope complete (`_audit/fixes/bug_a_mt5_removal_scope_phase0.md`)
- ✅ Phase 0 sub-investigation errata addressed: macro_monitor `price_mt5` schema-claim corrected (see preface)
- ✅ Critical assumption verification (Quantower único source, ZERO MT5 connections, offset stale-and-still-used) documented in §2.5

**Pending**:
- ML-DS review (~20min)
- Barbara approval
- Phase 3 implementation (separate task spec)
- Phase 4 deploy (separate task spec)
- Phase 5 24h observation (separate task spec)

**ZERO impl pending**. Standby para ML-DS triage.

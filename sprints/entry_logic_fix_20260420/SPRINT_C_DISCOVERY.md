# SPRINT C — Phase 1 Discovery Report (READ-ONLY)

**Sprint:** entry_logic_fix_20260420 / Track C Implementation v2
**Timestamp:** 2026-04-20 ~15:05 UTC
**Mode:** READ-ONLY — zero edits, zero restarts
**Input docs:** CLAUDECODE_Sprint_C_Implementation_v2.md + CLAUDECODE_Sprint_C_Addendum_v1.md
**Checkpoint:** §13.2 (Barbara acknowledgment required before Phase 2)

---

## 1. Code Discovery (with corrected line numbers per Addendum §A)

### 1.1 ADR-001 comment (`level_detector.py:18`) — verbatim

```python
# ADR-001: H4/D1 levels are NEVER used for execution.
# Violation = system goes idle in trending markets.
```

(context: inside top-of-file docstring block, lines 18-19)

**Plan:** amend to reference ADR-002 (Phase 2f). Do not delete.

### 1.2 `derive_m30_bias` (`level_detector.py:215-263`) — signature verified

```python
def derive_m30_bias(
    m30_df: pd.DataFrame | None, confirmed_only: bool = False
) -> tuple[str, bool]:
    """
    Shared M30 bias derivation used by both entry and position-monitor paths.

    Returns
    -------
    (bias, is_confirmed_source)
      bias: "bullish" | "bearish" | "unknown"
      is_confirmed_source: True when derived from a confirmed M30 box.
    """
```

Return values: `"bullish" | "bearish" | "unknown"` (not `"neutral"`). **Confirms §B.2 mapping requirement.**

### 1.3 `_classify` helper (`level_detector.py:230-243`) — literal

```python
def _classify(row) -> str:
    import math
    box_high = row.get("m30_box_high", float("nan"))
    box_low  = row.get("m30_box_low",  float("nan"))
    liq_top  = row.get("m30_liq_top",  float("nan"))
    liq_bot  = row.get("m30_liq_bot",  float("nan"))

    if math.isnan(liq_top) or math.isnan(box_high):
        return "unknown"
    if liq_top > box_high:
        return "bullish"
    if not math.isnan(liq_bot) and not math.isnan(box_low) and liq_bot < box_low:
        return "bearish"
    return "unknown"
```

Confirmed semantics per §3 of Design Doc: strict `>` reflects writer "UP fakeout" marker.

### 1.4 `refresh_macro_context` block (`event_processor.py:1602-1646`) — verified

- **:1621-1628** — double call to `derive_m30_bias` confirmed:
  ```python
  confirmed_bias, is_confirmed = derive_m30_bias(df, confirmed_only=True)
  provisional_bias, _ = derive_m30_bias(df, confirmed_only=False)
  ...
  self.m30_bias = confirmed_bias
  self.m30_bias_confirmed = is_confirmed
  self.provisional_m30_bias = provisional_bias
  ```

**Addendum §B.3 requirement satisfied:** `self.provisional_m30_bias` is already populated every refresh cycle. `derive_m30_bias_v2` must consume these state attributes, NOT recompute from DataFrame.

### 1.5 Hard-block at `event_processor.py:2381-2405` — literal

```python
# -- PONTO 1: M30 BIAS HARD GATE ---------------------------------------
# Confirmed fix:
#   - only CONFIRMED m30_bias may hard-block
#   - provisional/unconfirmed bias is telemetry only
_is_patch2a = (source == "PATCH2A")
if not m30_bias_confirmed:
    if provisional_m30_bias in ("bullish", "bearish"):
        log.info(
            "M30_BIAS_PROVISIONAL_ONLY: src=%s dir=%s provisional=%s confirmed=%s -> no hard block",
            source, direction, provisional_m30_bias, m30_bias
        )
        if _is_patch2a:
            print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} provisional={provisional_m30_bias} confirmed=unknown -> PASS")
    elif _is_patch2a:
        print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} provisional=unknown confirmed=unknown -> PASS")
else:
    if m30_bias == "bullish" and direction == "SHORT":
        print(f"[{ts}] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)")
        log.info("M30_BIAS_BLOCK: confirmed bullish M30 bias rejects SHORT at %.2f (src=%s)", price, source)
        if _is_patch2a:
            print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias=bullish -> BLOCK")
        return
    if m30_bias == "bearish" and direction == "LONG":
        print(f"[{ts}] M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG rejected (contra-M30)")
        log.info("M30_BIAS_BLOCK: confirmed bearish M30 bias rejects LONG at %.2f (src=%s)", price, source)
        if _is_patch2a:
            print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias=bearish -> BLOCK")
        return
    if _is_patch2a:
        print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias={m30_bias} -> PASS")
```

**Addendum §B.4 requirement confirmed:** already passes through when `m30_bias_confirmed=False` + provisional ∈ {bullish, bearish}. Block fires only when `m30_bias_confirmed=True` AND `m30_bias` opposes `direction`. Override rule (R_M30_2/4 + H4 aligned) only needs to be added in the `else:` branch (:2391+).

### 1.6 `_resolve_direction` (`event_processor.py:3467-3582`) — return point count

**Signature** (:3467):
```python
def _resolve_direction(self, level_type: str) -> tuple:
```

**Return points: EXACTLY 1** — single `return (direction, reason)` at **:3582**.

The function uses local `direction` and `reason` vars mutated throughout branches, with a single exit. This is cleaner than Addendum §B.5 estimate (8-12). `_apply_h4_gate(direction, reason)` insertion is trivial: replace the single return line with `return self._apply_h4_gate(direction, reason)`.

Branch structure (for context):
- RANGE_BOUND (:3480-3484): `direction = "SHORT" if liq_top else "LONG"`
- TRENDING → tc_mode = PULLBACK/CONTINUATION (:3494-3499)
- TRENDING → tc_mode = SKIP → overextension sub-branches (:3500-3535)
- TRENDING → tc_mode = DISABLED → legacy branches (:3536-3573)
- FALLBACK (:3574-3576)
- All converge to `log.info(...)` at :3578-3581 then **single return at :3582**.

### 1.7 `_read_d1h4_bias_shadow` (`event_processor.py:704-723`) — current behaviour

```python
def _read_d1h4_bias_shadow(self) -> dict:
    """Read D1/H4 bias from gc_d1h4_bias.json (FASE 4a shadow). No behavioral impact."""
    _bias_path = Path("C:/FluxQuantumAI/logs/gc_d1h4_bias.json")
    try:
        if _bias_path.exists():
            with open(_bias_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "d1_jac_dir":     data.get("d1_jac_dir", "?"),
                "h4_jac_dir":     data.get("h4_jac_dir", "?"),
                "direction":      data.get("bias_direction", "?"),
                "strength":       data.get("bias_strength", "?"),
                "h4_stale":       data.get("data_freshness", {}).get("h4_stale", True),
                "d1_stale":       data.get("data_freshness", {}).get("d1_stale", True),
                "last_closed_h4": data.get("last_closed_h4_ts", "?"),
                "last_closed_d1": data.get("last_closed_d1_ts", "?"),
            }
    except Exception:
        pass
    return {"d1_jac_dir": "?", "h4_jac_dir": "?", "direction": "?", "strength": "?"}
```

Called from `_write_decision` context for telemetry only. No gate impact. Uses `"?"` sentinel for missing. See §B.2 mapping.

### 1.8 `_resolve_direction` call sites — confirmed :4254 and :4415

Grep result:
```
4254:        direction, strategy_reason = self._resolve_direction(level_type)
4415:                    direction, _strat_reason = self._resolve_direction(level_type)
```

Both sites consume the tuple directly. Since `_apply_h4_gate` is inserted inside the function (Option 1 of §B.5), both sites automatically protected — no call-site changes.

### 1.9 `m30_updater.py:261-304` writer semantics (reference only, NO MODIFICATION)

Already validated in Design Doc v2 §4.1. Confirmed: writer emits `liq_top = fakeout_ext` (UP) or `liq_top = box_high` (DN). Never `liq_top < box_high`. Equality is canonical "no UP fakeout" marker. **Strict `>` in `derive_m30_bias._classify` is semantically correct.**

---

## 2. Addendum §B items verification

### B.1 — `gc_d1h4_bias.json` investigation

**File existence + freshness:**
```
-rw-r--r-- 753 bytes
mtime: 2026-04-14 14:05:05 UTC
file_age: ~143 hours (~6 days)
```

**Full schema (verbatim):**
```json
{
  "timestamp": "2026-04-14T14:03:09.012822+00:00",
  "d1_jac_dir": "long",
  "h4_jac_dir": "long",
  "bias_direction": "LONG",
  "bias_strength": "STRONG",
  "bias_source": "runtime_d1h4_updater",
  "data_freshness": {
    "h4_parquet_age_s": 2.4,
    "d1_parquet_age_s": 0.0,
    "h4_stale": false,
    "d1_stale": false,
    "m1_last_bar": "2026-04-14T14:03:00+00:00"
  },
  "last_closed_h4_ts": "2026-04-02T22:00:00+00:00",
  "last_closed_d1_ts": "2026-04-08T22:00:00+00:00",
  "session_boundary": "22:00 UTC (17:00 ET)",
  "h4_bars": "22,02,06,10,14,18 UTC",
  "h4_total_boxes": 859,
  "d1_total_boxes": 113,
  "h4_last_bar": "2026-04-14T14:00:00+00:00",
  "d1_last_bar": "2026-04-13T22:00:00+00:00",
  "elapsed_s": 116.38
}
```

**Writer discovery:**
- Writer module: `C:/FluxQuantumAI/live/d1_h4_updater.py` (OUTPUT_BIAS at :68, write block ~:446).
- Invocation: `run_live.py:131` imports `start as _start_d1h4_updater`, BUT the start call at **`run_live.py:785-789` is commented out**:

```python
# --- D1/H4 Bias Engine (FASE 4a shadow -- DISABLED) ---
# DISABLED 2026-04-14: full M1 reload (2.2M rows) every 5min was degrading
# server performance (1GB RAM, 90% CPU). Needs incremental/event-driven redesign.
# Shadow bias still readable from gc_d1h4_bias.json (last standalone run).
# TODO: redesign as incremental updater that only processes new bars.
# if not args.no_updaters and _start_d1h4_updater is not None:
#     _get_dt = lambda: getattr(processor, "daily_trend", "unknown")
#     _start_d1h4_updater(get_daily_trend_fn=_get_dt)
#     print(_color("D1H4 Updater started (300s cadence, SHADOW MODE)", _CYAN))
print(_color("D1H4 Updater DISABLED (perf issue -- awaiting incremental redesign)", _YELLOW))
```

**Writer is DEAD since 2026-04-14 14:05.** The JSON file is frozen at that moment with `h4_stale: false` from that instant, but by now the data itself is 6 days stale.

**Staleness semantics:** `h4_stale` field is a SNAPSHOT taken when the updater wrote the file. Current value (`false`) means "at write time, H4 parquet was ≤ threshold age". Not a live signal.

**Schema sufficiency for R_H4_1..7:** only `h4_jac_dir` / `bias_direction` fields — sufficient for R_H4_3/R_H4_6 (JAC-based) but NOT for R_H4_1/R_H4_2/R_H4_4/R_H4_5 (which need OHLC candle data). In any case, the file is stale so moot.

**Decision table resolution (§B.1):**

| Scenario | Which applies? | Rationale |
|---|---|---|
| JSON fresh + writer active + schema sufficient | ❌ | Writer disabled; JSON 143h stale |
| JSON fresh, jac_dir only | ❌ | Same — not fresh |
| **JSON stale OR missing OR writer dead** | ✅ | Writer disabled per run_live.py:785 |

**Proposed source priority (awaiting Barbara ratification per §D hard limit #11):**

Given BOTH `gc_d1h4_bias.json` AND `gc_h4_boxes.parquet` are stale (same 2026-04-14 14:00 cutoff, same disabled writer pipeline), the hybrid collapses to:

```
1. gc_h4_boxes.parquet if staleness ≤ max_staleness_hours (6h)  ← will always miss today
2. resample OHLCV from gc_ohlcv_l2_joined.parquet                ← the only working source today
```

The `gc_d1h4_bias.json` path is NOT worth integrating while the writer is dead. Add as future enhancement (re-enable after writer redesign, tracked in `SPRINT H4-WRITER-FIX (P1)` backlog per Adjustment 4).

**Barbara decision required (§D #11):** ratify resample-only today, OR explicitly include stale-JSON-as-degraded-fallback? Recommendation: **resample-only**.

### B.2 — Bias terminology mapping documented

Canonical mapping for Sprint C v2 code:

| Source | Raw values | Maps to v2 |
|---|---|---|
| Legacy `derive_m30_bias` | `"bullish"` / `"bearish"` / `"unknown"` | `"bullish"` / `"bearish"` / `"neutral"` |
| `gc_d1h4_bias.json.bias_direction` | `"LONG"` / `"SHORT"` / `"?"` | `"bullish"` / `"bearish"` / `"neutral"` |
| `gc_d1h4_bias.json.h4_jac_dir` | `"long"` / `"short"` / `"?"` / `"UP"` / `"DN"` | `"bullish"` / `"bearish"` / `"neutral"` |
| v2 internal | — | `"bullish"` / `"bearish"` / `"neutral"` |
| v2 → legacy consumers (self.m30_bias) | — | `"neutral"` → `"unknown"` (preserve legacy contract) |

Implementation plan (Phase 2a):

```python
def _canonicalize_bias(raw: str) -> str:
    """Normalize bias string to v2 vocabulary ("bullish"|"bearish"|"neutral").
    Case-insensitive; handles legacy "unknown", JSON "?", jac "UP"/"DN"/"long"/"short".
    """
    if not raw:
        return "neutral"
    r = str(raw).strip().upper()
    if r in ("BULLISH", "LONG", "UP"):
        return "bullish"
    if r in ("BEARISH", "SHORT", "DN", "DOWN"):
        return "bearish"
    # "UNKNOWN", "?", "NEUTRAL", "", anything else → neutral
    return "neutral"
```

Unit tests required: each raw variant maps correctly; case variations (`"bullish"`, `"Bullish"`, `"BULLISH"`) all produce `"bullish"`.

### B.3 — `self.provisional_m30_bias` already computed

Confirmed above (§1.4). **`derive_m30_bias_v2` must consume `confirmed_bias`, `is_confirmed`, `provisional_bias` as inputs** per revised signature. No double-computation from DataFrame.

### B.4 — Hard-block pass-through pattern confirmed

Confirmed above (§1.5). Current logic:
- `m30_bias_confirmed=False` + provisional ∈ {bullish, bearish} → **passes through** (no block)
- `m30_bias_confirmed=False` + provisional=unknown → passes through
- `m30_bias_confirmed=True` + `m30_bias="bullish"` + `direction="SHORT"` → BLOCK
- `m30_bias_confirmed=True` + `m30_bias="bearish"` + `direction="LONG"` → BLOCK
- `m30_bias_confirmed=True` + aligned → PASS

**Sprint C override only needs to modify the two BLOCK branches**, adding an escape hatch when R_M30_2/R_M30_4 + H4 aligned + `h4_gate.enforce=True`.

### B.5 — `_resolve_direction` return points

**Count: 1 (single exit at :3582).** Cleaner than Addendum's estimate (8-12). `_apply_h4_gate` helper inserted once, invoked at the single return — perfect Option 1 fit.

---

## 3. Data Discovery

| Parquet | Rows | Index max | Index age | File mtime | File age | Status |
|---|---|---|---|---|---|---|
| `gc_m30_boxes.parquet` | 73,669 | 2026-04-20 13:00 UTC | 0.5h | 2026-04-20 13:29 | 0.0h | **FRESH** ✓ |
| `gc_h4_boxes.parquet` | 9,609 | 2026-04-14 14:00 UTC | **143.5h** | 2026-04-14 14:12 | **143.3h** | **STALE** (writer dead) |
| `gc_ohlcv_l2_joined.parquet` | 2,202,739 | 2026-04-20 13:29 UTC | 0.0h | 2026-04-20 13:29 | 0.0h | **FRESH** ✓ |
| `gc_ats_features_v4.parquet` | 2,193,957 | 2026-04-08 22:26 UTC | **279.1h** | 2026-04-12 21:17 | 184.2h | **STALE** (writer dead since ~04-12) |

**Implication:** H4 candles must come from OHLCV resample. M30 bias input flows through existing fresh path (no change). Both other H4 sources are inoperable today.

---

## 4. Config Discovery (`settings.json`)

- 85 top-level keys (full list in Phase 1 log)
- `h4_gate` section **does NOT exist** — will be added in Phase 2 sub-step 6.6
- Relevant keys for reference: `overextension_atr_mult` (1.5, Sprint C keeps per Barbara decision 11.5), `iceberg_proxy_threshold`, `dual_strategy_enabled`

---

## 5. Decision Log state

```
Path:   C:\FluxQuantumAI\logs\decision_log.jsonl
Size:   22.30 MB
Lines:  12,085 (63 new since Sprint C investigation at 12,022 — matches ongoing live activity)
mtime:  2026-04-20 15:04 UTC (active within last minute at report time)
```

Covers 2026-04-14 → 2026-04-20. Sufficient for Phase 4 backtest counterfactual.

---

## 6. Git State

```
$ git status --short
?? .gitignore
?? .venv/
?? Backups/
?? "C\357\200\272FluxQuantumAIlogsv3_train_run.log"
?? "DOCS MASTER/"
(many more untracked)

$ git log --oneline
fatal: your current branch 'master' does not have any commits yet

$ git branch --show-current
master
```

**Orphan repo confirmed.** Sprint C commits will be #1, #2, … per sub-phase (2a, 2b, 2c, 2d, 2e, 2f, 3, 4). Per §0 #9: local commits OK, no push without Barbara authorization.

Note: there is an unusual filename `C?FluxQuantumAIlogsv3_train_run.log` (octal-escaped path collision) in working dir. Pre-existing, not introduced by this sprint.

---

## 7. Service & Capture state

**Python processes observed (via `wmic process`):**

| PID | Process | Command | Role |
|---|---|---|---|
| **2512** | python.exe | `watchdog_l2_capture.py` | **Capture — DO NOT KILL** |
| **8248** | python.exe | `iceberg_receiver.py` | **Capture — DO NOT KILL** |
| **12332** | python.exe | `uvicorn quantower_level2_api:app --port 8000` | **Capture — DO NOT KILL** |
| 6324 | python.exe | `api.py` | Dashboard API |
| 4516 | python.exe | `run_live.py --execute --broker roboforex --lot_size 0.05` | FluxQuantumAPEX main (active trading process) |

**Capture 3/3 intact.** Pre-condition honoured. (No Get-CimInstance run because tasklist output was sufficient and no process altered.)

---

## 8. Deviations from Design Doc v2 — found during discovery

| # | Deviation | Impact |
|---|---|---|
| 1 | `_resolve_direction` has 1 return point (not 8-12) | Addendum §B.5 simplified — single-line change |
| 2 | `gc_d1h4_bias.json` exists but writer DISABLED (perf) | §B.1 scenario 3 definitive. Resample-only hybrid |
| 3 | `gc_ats_features_v4.parquet` also stale (279h) | Not on critical path; ignored |
| 4 | Legacy `_classify` returns `"unknown"`, not `"neutral"` | Confirmed; _canonicalize_bias handles boundary |
| 5 | Hard-block pass-through already implemented for `confirmed=False` | §B.4 confirmed; override surface narrower than v2 design doc suggested |

None of these blocks Phase 2. All are data points the final commits will reference.

---

## 9. Phase 1 Checklist — status

| Item | Status | Notes |
|---|---|---|
| 5.1.1 ADR-001 verbatim | ✅ | §1.1 |
| 5.1.2 derive_m30_bias signature | ✅ | §1.2 |
| 5.1.3 _classify semantics | ✅ | §1.3 |
| 5.1.4 refresh_macro_context + self.m30_bias | ✅ | §1.4 (:1602-1646) |
| 5.1.5 Hard-block literal | ✅ | §1.5 (:2381-2405) |
| 5.1.6 _resolve_direction + return count | ✅ | §1.6 (:3467-3582, 1 return) |
| 5.1.7 _read_d1h4_bias_shadow | ✅ | §1.7 |
| 5.1.8 Call sites :4254 and :4415 | ✅ | §1.8 |
| 5.1.9 m30_updater reference | ✅ | §1.9 |
| B.1 gc_d1h4_bias.json investigation | ✅ | §2.B.1 (scenario 3 — writer dead) |
| B.2 bias terminology mapping | ✅ | §2.B.2 (with _canonicalize_bias plan) |
| B.3 provisional already computed | ✅ | §2.B.3 |
| B.4 hard-block pass-through | ✅ | §2.B.4 |
| B.5 return points count | ✅ | §2.B.5 (1 return) |
| 5.2 Parquet inventory | ✅ | §3 |
| 5.3 Config + h4_gate absence | ✅ | §4 |
| 5.4 Decision log state | ✅ | §5 |
| 5.5 Git state | ✅ | §6 (orphan confirmed) |
| 5.6 Service + capture state | ✅ | §7 (3/3 intact) |

---

## 10. Decisions required from Barbara before Phase 2

**Blocking Phase 2 start:**

1. **§B.1 H4 source priority** (Addendum §D hard limit #11 — cannot auto-decide).
   - Recommendation: implement **resample-only** (parquet path kept in code but always falls through because freshness never ≤6h while writer dead). JSON path not integrated (wait for writer revival in Sprint H4-WRITER-FIX P1).
   - Alternative: integrate JSON as degraded fallback (warns loudly), but adds complexity for zero current benefit.

2. Confirm backlog entry for **Sprint H4-WRITER-FIX (P1)** (Addendum Adjustment 4). Will be appended to `C:\FluxQuantumAI\sprints\BACKLOG.md` (create file if missing).

**Non-blocking but worth awareness:**

- `gc_ats_features_v4.parquet` is also stale 279h. Not on this sprint's critical path but confirms the broader issue with parked feature-writer pipeline.

---

## 11. System state

- **Files modified:** ZERO (`level_detector.py`, `event_processor.py`, `settings.json` untouched since Phase 0 backup at 20260420_114818)
- **Restarts:** ZERO
- **Capture processes (2512, 8248, 12332):** INTACT, all running
- **Git operations:** ZERO
- **Parquets written:** ZERO (all reads only)

---

## 12. Next step — awaiting Barbara ack (§13 checkpoint 2)

Once Barbara:
- Ratifies §10 decision 1 (H4 source priority)
- Acknowledges §10 decision 2 (backlog entry)

ClaudeCode proceeds to **Phase 2a** (`derive_h4_bias` + `_canonicalize_bias` + CALIBRATION_TBD constants in `level_detector.py`).

No writes until explicit ack.

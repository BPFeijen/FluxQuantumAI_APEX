# CLAUDECODE PROMPT — APEX STABILIZATION SPRINT — 7 Fixes Consolidated

**Sprint ID:** `sprint_apex_stabilization_7fixes_20260421`
**Authorization:** Barbara ratified 2026-04-21 (structure + 4 technical modifications)
**Type:** MIXED — discovery READ-ONLY + code changes WRITE to production + service restart
**Duration target:** 8-12 hours
**Output root:** `C:\FluxQuantumAI\sprints\sprint_apex_stabilization_7fixes_20260421\`
**Framework reference:** `FRAMEWORK_DataDriven_Calibration_APEX_v1.1.md`
**Priority:** P0 — live-impacting bugs, Barbara losing trades daily

---

## 0. Mission Statement

Execute a closed sprint with EXACTLY 7 fixes (see §3). Surgical mode, minimum diff. Do NOT invent new patches. Do NOT open new frontiers. Do NOT ask open-ended implementation questions.

**Do NOT touch:**
- Strategy thresholds (calibration untouched)
- Scoring / confidence contribution
- Executor routing / broker logic
- Lot sizing
- Iceberg / anomaly logic (INVEST-03 still running separately)
- Capture services (PIDs 2512, 8248, 11740)
- ATS / Wyckoff / ICT methodology

---

## 1. Single Source of Truth Contract (authoritative)

System contract — every fix must respect this:

- **`decision_live.json`** — latest decision snapshot (overwrite atomic)
- **`decision_log.jsonl`** — append-only audit trail (every outcome)
- **`service_state.json`** — heartbeat/health independent of feed

Dashboard, Telegram, Executor MUST reflect EXACTLY what the Decisor wrote. **If it's not in Decisor output, it doesn't exist.**

---

## 2. Hard Limits

1. **STOP between each Phase.** Claude (ML Engineer) must ratify each Phase's deliverables before ClaudeCode proceeds to next. 7 Phases = 7 mandatory STOPs.
2. **NO live-edit without local validation.** Every change: backup → apply → py_compile → import probe → unit test before commit.
3. **NO service restart until Phase 7.** Changes staged inactive until single restart window 22:00-23:00 UTC (Asian low-liquidity).
4. **Capture PIDs 2512, 8248, 11740 UNTOUCHED.** Forever.
5. **Rollback plan validated BEFORE restart.** See §11.
6. **STOP-AND-ASK Barbara only if:**
   - Data source unavailable (H4 stale, iceberg JSONL corrupt, etc.)
   - >5 UNSAFE consumers of m30_bias found (Phase 4 discovery)
   - Calibration backtest (Phase 4.0) shows NO threshold satisfies acceptance criteria
   - Restart window passes without green light
   - Any rollback condition triggered

---

## 3. Fix Order (7 Phases)

| Phase | Fix | Scope | Restart needed? |
|---|---|---|---|
| 1 | **Fix #3** Canonical Publish Path | ALL outcomes → decision_live + decision_log + service_state | Yes (end of sprint) |
| 2 | **Fix #1** Surface M30_BIAS_BLOCK | M30_BIAS_BLOCK uses canonical path | Yes |
| 3 | **Fix #6** Telegram Observability | log.debug → warning/error + heartbeat | Yes |
| 4 | **Fix #2** derive_m30_bias Regime | Confirmed/provisional split + H4 override + ATR invalidation | Yes |
| 5 | **Fix #7** M5/M30 Discrepancy | Persistent contradiction → canonical event | Yes |
| 6 | **Fix #4** ApexNewsGate Re-enable | Import error fix + health surface | Yes |
| 7 | **Fix #5** Feed Staleness | FEED_DEAD canonical + heartbeat independent | Yes |

All changes staged across Phases 1-6. **Single service restart** after Phase 7.

---

## 4. Pre-Flight (before Phase 1)

### 4.1 System state

```powershell
# Capture PIDs
Get-Process -Id 2512, 8248, 11740 -ErrorAction SilentlyContinue | Format-Table Id, ProcessName, StartTime

# Service PID
$svc = Get-Process -Name "python*" | Where-Object { (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -like "*run_live*" }
$svc | Format-Table Id, CPU, StartTime

# Service manager
Get-Service -Name "FluxQuantumAPEX*" | Format-Table Name, Status, StartType

# Current settings.json hash (baseline)
Get-FileHash "C:\FluxQuantumAI\config\settings.json" | Select-Object Hash

# Git state
cd C:\FluxQuantumAI; git log --oneline -5; git status
```

Report all above. **Abort sprint if:**
- Any capture PID missing
- Service PID not running
- Uncommitted working tree changes not from prior sprints

### 4.2 Global backup

```powershell
$SPRINT_DIR = "C:\FluxQuantumAI\sprints\sprint_apex_stabilization_7fixes_20260421"
$BAK = "$SPRINT_DIR\backup_pre_sprint"
mkdir $BAK

# All candidate files per §FILES TO MODIFY (error on side of over-backup)
$files = @(
    "C:\FluxQuantumAI\live\event_processor.py",
    "C:\FluxQuantumAI\live\level_detector.py",
    "C:\FluxQuantumAI\live\position_monitor.py",
    "C:\FluxQuantumAI\live\tick_breakout_monitor.py",
    "C:\FluxQuantumAI\live\telegram_notifier.py",
    "C:\FluxQuantumAI\live\base_dashboard_server.py",
    "C:\FluxQuantumAI\config\settings.json"
)
foreach ($f in $files) {
    if (Test-Path $f) { Copy-Item $f $BAK\ }
}
Get-ChildItem $BAK | ForEach-Object { "$($_.Name): $((Get-FileHash $_.FullName).Hash)" } | Out-File $BAK\MANIFEST.txt
```

Verify MANIFEST.txt contents. This is the rollback source for ENTIRE sprint.

### 4.3 Pre-flight report

Report pre-flight state to Claude for audit. **STOP. Await Claude ratification before Phase 1.**

---

## 5. PHASE 1 — Fix #3: Canonical Publish Path

### 5.1 Objective

Every decision outcome (GO, BLOCK variants, EXEC_FAILED, SKIP, FEED_DEAD, PM events) writes to:
- `decision_live.json` (atomic overwrite)
- `decision_log.jsonl` (append)
- `service_state.json` (heartbeat — independent of feed/tick loop)

### 5.2 Discovery

Locate:
- Current `_publish_decision` / canonical write function (likely `position_monitor.py:2045-2054` per DIAGNOSIS_REPORT §10.3)
- All decision emission points (GO path, BLOCK variants, EXEC_FAILED, FEED_DEAD, etc.)
- `service_state.json` writer + dependency on tick loop
- Heartbeat mechanism (if exists)

```powershell
Select-String -Path "C:\FluxQuantumAI\live" -Pattern "decision_live|decision_log|_publish_decision|service_state|canonical|heartbeat" -Recurse |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" } |
    Out-File "$SPRINT_DIR\phase1_discovery.txt"
```

Identify which decision types currently SKIP canonical publish. Per DIAGNOSIS §10.3 item 5: "canonical publish apparently runs only for some decision types, skipping M30_BIAS_BLOCK entirely."

### 5.3 Implementation

**Atomic overwrite for decision_live.json:**

```python
def _write_decision_live_atomic(payload: dict, path: str = DECISION_LIVE_PATH):
    import os, json, tempfile
    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".decision_live_", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)  # atomic on same filesystem
    except Exception:
        if os.path.exists(tmp): os.unlink(tmp)
        raise
```

**Canonical publish — NO type filters:**

```python
def _publish_decision_canonical(decision: dict) -> None:
    """Emit to all three canonical destinations. NO type filter — every decision publishes."""
    try:
        # 1. decision_live.json (atomic overwrite)
        _write_decision_live_atomic(decision)
    except Exception as e:
        log.error(f"decision_live.json write FAILED: {e}", exc_info=True)
    
    try:
        # 2. decision_log.jsonl (append)
        with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"decision_log.jsonl write FAILED: {e}", exc_info=True)
    
    try:
        # 3. service_state.json update (heartbeat side-effect)
        _update_service_state_heartbeat(decision.get("timestamp"))
    except Exception as e:
        log.warning(f"service_state.json heartbeat update FAILED: {e}")
```

**Heartbeat independent of feed:**

Identify current heartbeat. Modify so it updates `service_state.json.last_heartbeat_at` on a timer thread, NOT inside tick loop. If service_state already has such thread, verify it runs at startup and reports even when feed DEAD.

**Replace all call sites** of previous per-type emission (log.info only) with `_publish_decision_canonical(...)`. Every decision point in event_processor.py, position_monitor.py, tick_breakout_monitor.py must route through this function.

### 5.4 Validation

```powershell
python -m py_compile C:\FluxQuantumAI\live\event_processor.py
python -m py_compile C:\FluxQuantumAI\live\position_monitor.py
python -m py_compile C:\FluxQuantumAI\live\tick_breakout_monitor.py
python -c "from live import event_processor, position_monitor; print('imports OK')"
```

Unit test `test_phase1_canonical_publish.py`:
- Mock decision dict with each outcome type (GO, BLOCK, EXEC_FAILED, FEED_DEAD, etc.)
- Call `_publish_decision_canonical`
- Assert decision_live.json contains correct payload (atomic replace)
- Assert decision_log.jsonl appended (no truncation)
- Assert service_state.json heartbeat updated

### 5.5 Phase 1 report

`PHASE_1_REPORT.md`:
- Files modified + line numbers
- DIFF SUMMARY (function by function, old vs new)
- Unit test results
- Pre-backup hash verification
- Staged changes NOT yet live (restart pending)

**STOP. Await Claude ratification for Phase 2.**

---

## 6. PHASE 2 — Fix #1: Surface M30_BIAS_BLOCK

### 6.1 Objective

M30_BIAS_BLOCK rejections (60,878 events in current stdout log, per DIAGNOSIS §10.1) must emit canonical publish — visible in decision_live.json, decision_log.jsonl, /api/live, Telegram.

### 6.2 Implementation

Locate M30_BIAS_BLOCK emission site in event_processor.py. Currently:

```python
# BEFORE (DIAGNOSIS finding — rejection only in log.info)
if contra_bias:
    log.info(f"M30_BIAS_BLOCK: bias={bias} -- {direction} rejected (contra-M30)")
    return None  # silently rejected
```

Change to use Phase 1's canonical publish:

```python
# AFTER
if contra_bias:
    log.info(f"M30_BIAS_BLOCK: bias={bias} -- {direction} rejected (contra-M30)")
    decision = {
        "timestamp": _now_utc_iso(),
        "action": "BLOCK",
        "direction": direction,
        "reason": f"M30_BIAS_BLOCK: bias={bias} -- {direction} rejected (contra-M30)",
        "blocks": [{
            "gate": "m30_bias",
            "code": "M30_BIAS_BLOCK",
            "reason": f"bias={bias} contra-{direction}",
            "m30_bias": bias,
            "m30_bias_confirmed": bias_confirmed,
        }],
        "context": _build_context_snapshot(),  # reuse existing context builder
    }
    _publish_decision_canonical(decision)
    return None
```

**Deduplication window (anti-spam):**

M30_BIAS_BLOCK fires high frequency (43k+/day). Without dedup, decision_log grows 10×.

Implement dedup: same (reason, direction) within **30 seconds** = emit once, skip duplicates. Value 30s is operational safeguard against flood, not a trading threshold — no calibration needed, but ClaudeCode MUST use constant `DEDUP_WINDOW_SECONDS = 30` at module top (commented "Anti-spam operational guard — not a trading parameter").

### 6.3 Validation

Unit test `test_phase2_m30_block_surface.py`:
- Call emission with contra_bias=True
- Assert decision_live.json + decision_log.jsonl updated with BLOCK + blocks[] populated
- Call again within 30s with same (reason, direction) → assert no new write (dedup)
- Call with different direction → assert new write

### 6.4 Phase 2 report

`PHASE_2_REPORT.md`: files modified, diff, test results.

**STOP. Await Claude ratification for Phase 3.**

---

## 7. PHASE 3 — Fix #6: Telegram Observability

### 7.1 Objective

Failures visible. Heartbeat visible.

### 7.2 Implementation

`position_monitor.py:2060-2063` (Telegram try/except):

```python
# BEFORE
try:
    tg.notify_decision(decision)
except Exception as e:
    log.debug(f"Telegram notify failed: {e}")  # INVISIBLE

# AFTER
try:
    result = tg.notify_decision(decision)
    _telegram_stats["sends"] += 1
    _telegram_stats["last_success_at"] = _now_utc_iso()
except DedupSkipped as e:
    _telegram_stats["last_dedup_reason"] = str(e)
except Exception as e:
    _telegram_stats["failures"] += 1
    _telegram_stats["last_failure_at"] = _now_utc_iso()
    log.warning(f"Telegram notify FAILED: {e}", exc_info=True)
```

Module-level stats dict `_telegram_stats` initialized at startup. Periodic heartbeat (every 5 min) writes current stats to `service_state.json.telegram`:

```json
"telegram": {
    "sends": 142,
    "failures": 3,
    "last_success_at": "2026-04-21T22:15:43Z",
    "last_failure_at": "2026-04-21T20:03:11Z",
    "last_dedup_reason": "same decision_id within 60s"
}
```

### 7.3 Validation

Unit test mocking Telegram success/failure/dedup — assert stats update correctly.

### 7.4 Phase 3 report

`PHASE_3_REPORT.md`.

**STOP. Await Claude ratification for Phase 4.**

---

## 8. PHASE 4 — Fix #2: derive_m30_bias Regime Redesign

**MOST SENSITIVE PHASE.** Contains mandatory calibration sub-phase 4.0.

### 8.0 Phase 4.0 — Data-driven calibration of ATR_INVALIDATION_MULT

**This sub-phase is MANDATORY before any code change.** Framework v1.1.

**Candidate multipliers:** X ∈ {0.5, 0.75, 1.0, 1.25, 1.5, 2.0} × M30_ATR

**Methodology:**

1. Load `gc_ohlcv_l2_joined.parquet` (M1, 10 months Jul 2025 → today) + `gc_m30_boxes.parquet`
2. For each M30 bar in history:
   - Compute current raw_bias (using current derive_m30_bias logic)
   - Compute candidate_bias for each X: if price > box_high + X×ATR → bearish invalidates (→ unknown), if price < box_low - X×ATR → bullish invalidates (→ unknown)
3. Regime classifier (same as INVEST-01/03): RANGE / TREND_UP / TREND_DN / TRANSITIONAL
4. Per (regime × X):
   - Forward-return accuracy at 30min, 60min, 4h
   - Flip rate (bias changes per day)
   - Coverage (% time not in `unknown`)
5. Walk-forward stability: 3 sub-periods (Jul-Nov 2025, Dec 2025-Mar 2026, Apr 2026) — does optimal X flip?

**Acceptance criteria for X selection:**
- Accuracy new > accuracy baseline (at least 30m horizon, all regimes)
- Flip rate ≤ 2× baseline
- Coverage ≥ 80%
- Walk-forward stable (same X or adjacent value optimal across sub-periods)

If NO X satisfies all 4 → **STOP-AND-ASK Barbara**, report tradeoffs.

**Output:** `PHASE_4_0_CALIBRATION.md`:
- Table: X × regime × metrics
- Bootstrap 95% CI on accuracy for selected X
- Selected X with explicit justification
- Regression test scenario: applied to 2026-04-20 22:00 → 2026-04-21 05:00 session, would new function have flipped from bullish-stuck?

### 8.1 Discovery

```powershell
Select-String -Path "C:\FluxQuantumAI\live\level_detector.py" -Pattern "def derive_m30_bias|def _get_m30_bias|def _classify" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }

# All consumers of m30_bias
Get-ChildItem "C:\FluxQuantumAI\live" -Recurse -Include "*.py" |
    Select-String -Pattern "m30_bias" |
    Out-File "$SPRINT_DIR\phase4_consumers.txt"

# derive_h4_bias availability (Component A dependency)
Select-String -Path "C:\FluxQuantumAI\live\level_detector.py" -Pattern "def derive_h4_bias|derive_h4_bias"

# H4 parquet freshness
Get-Item "C:\data\processed\gc_h4_boxes.parquet" | Select-Object LastWriteTime
```

**If H4 data mtime > 6h stale OR derive_h4_bias returns mostly "unknown" → STOP-AND-ASK Barbara.** Component A unusable.

### 8.2 Consumer Impact Assessment

For each consumer, classify:
- **SAFE**: treats `unknown` as don't-block / fall-through
- **UNSAFE**: raises exception / defaults to one direction
- **UNKNOWN**: cannot determine from code

**If >5 UNSAFE found → STOP-AND-ASK Barbara.** Otherwise document and handle per 8.3.

### 8.3 Implementation — Technical Decisions (Claude ratified)

**Primary: Option 2 — H4 Bias Override**
**Secondary: Option 1 — ATR Invalidation (calibrated per Phase 4.0)**
**Option 3 (M30 sequence) — EXCLUDED from MVP**

**New state machine:**

```python
# New bias states (replace binary bullish/bearish/unknown with richer enum)
BIAS_BULLISH_CONFIRMED = "bullish_confirmed"
BIAS_BEARISH_CONFIRMED = "bearish_confirmed"
BIAS_BULLISH_PROVISIONAL = "bullish_provisional"
BIAS_BEARISH_PROVISIONAL = "bearish_provisional"
BIAS_NEUTRAL_INVALIDATED = "neutral_invalidated"
BIAS_UNKNOWN = "unknown"

# Module constants (calibrated Phase 4.0)
M30_BOX_STALE_HOURS = 4.0  # placeholder — Phase 4.0 may calibrate
ATR_INVALIDATION_MULT = None  # MUST be set from Phase 4.0 output before deploy
```

**Main function:**

```python
def derive_m30_bias_v2(m30_df, current_price=None, m30_atr=None, confirmed_only=False) -> dict:
    """Return dict with rich bias state.
    
    Returns:
        {
            "bias": one of BIAS_* states,
            "confirmed": bool,  # False if provisional
            "regime_state": "stable" | "invalidated" | "h4_override",
            "reason": str,  # explanation
            "source_box_id": Optional[int]
        }
    """
    import math
    
    if m30_df is None or m30_df.empty:
        return _bias_result(BIAS_UNKNOWN, False, "no_data", "empty M30 df")
    
    last_row = m30_df.iloc[-1]
    box_high = last_row.get("m30_box_high", float("nan"))
    box_low  = last_row.get("m30_box_low",  float("nan"))
    liq_top  = last_row.get("m30_liq_top",  float("nan"))
    liq_bot  = last_row.get("m30_liq_bot",  float("nan"))
    box_ts   = last_row.get("ts", None)
    box_id   = last_row.get("box_id", None)
    is_confirmed = bool(last_row.get("confirmed", False))
    
    if math.isnan(box_high) or math.isnan(box_low):
        return _bias_result(BIAS_UNKNOWN, False, "no_data", "NaN box boundaries")
    
    # === Raw classification (symmetric equality — Component C) ===
    top_ext = (not math.isnan(liq_top)) and (liq_top > box_high)
    bot_ext = (not math.isnan(liq_bot)) and (liq_bot < box_low)
    
    if top_ext and bot_ext:
        raw = BIAS_UNKNOWN  # ambiguous — both extended
    elif top_ext:
        raw = BIAS_BULLISH_CONFIRMED if is_confirmed else BIAS_BULLISH_PROVISIONAL
    elif bot_ext:
        raw = BIAS_BEARISH_CONFIRMED if is_confirmed else BIAS_BEARISH_PROVISIONAL
    else:
        raw = BIAS_UNKNOWN
    
    # === Component A: H4 Override if M30 box stale ===
    box_age_hours = _compute_age_hours(box_ts)
    if box_age_hours is not None and box_age_hours > M30_BOX_STALE_HOURS:
        h4_bias = derive_h4_bias()  # existing Sprint C Phase 2a
        if h4_bias in ("bullish", "bearish"):
            h4_state = BIAS_BULLISH_CONFIRMED if h4_bias == "bullish" else BIAS_BEARISH_CONFIRMED
            return _bias_result(h4_state, True, "h4_override", f"M30 box stale ({box_age_hours:.1f}h) — H4={h4_bias}", box_id)
        # H4 also unknown — fall through to ATR invalidation
    
    # === Component B: ATR Invalidation (calibrated Phase 4.0) ===
    if current_price is not None and m30_atr is not None and ATR_INVALIDATION_MULT is not None:
        threshold = ATR_INVALIDATION_MULT * m30_atr
        if raw in (BIAS_BULLISH_CONFIRMED, BIAS_BULLISH_PROVISIONAL):
            if current_price < (box_low - threshold):
                return _bias_result(BIAS_NEUTRAL_INVALIDATED, False, "invalidated",
                                   f"bullish invalidated: price {current_price:.2f} < box_low {box_low:.2f} - {threshold:.2f}",
                                   box_id)
        elif raw in (BIAS_BEARISH_CONFIRMED, BIAS_BEARISH_PROVISIONAL):
            if current_price > (box_high + threshold):
                return _bias_result(BIAS_NEUTRAL_INVALIDATED, False, "invalidated",
                                   f"bearish invalidated: price {current_price:.2f} > box_high {box_high:.2f} + {threshold:.2f}",
                                   box_id)
    
    return _bias_result(raw, is_confirmed, "stable", "raw classification from current box", box_id)


def _bias_result(bias, confirmed, regime_state, reason, source_box_id=None):
    return {
        "bias": bias,
        "confirmed": confirmed,
        "regime_state": regime_state,
        "reason": reason,
        "source_box_id": source_box_id,
    }
```

**Backward-compat wrapper** (for existing consumers):

```python
def derive_m30_bias(m30_df, confirmed_only=False) -> str:
    """Legacy wrapper — returns simple string for backward compat."""
    result = derive_m30_bias_v2(m30_df, current_price=_get_current_price(), m30_atr=_get_m30_atr(), confirmed_only=confirmed_only)
    bias = result["bias"]
    confirmed = result["confirmed"]
    
    if confirmed_only and not confirmed:
        return "unknown"
    
    # Map rich state to legacy 3-value
    if bias in (BIAS_BULLISH_CONFIRMED, BIAS_BULLISH_PROVISIONAL):
        return "bullish"
    if bias in (BIAS_BEARISH_CONFIRMED, BIAS_BEARISH_PROVISIONAL):
        return "bearish"
    return "unknown"  # includes BIAS_NEUTRAL_INVALIDATED and BIAS_UNKNOWN
```

**Expose rich state in decision context:**

```python
# In event_processor context builder
context["m30_bias_rich"] = derive_m30_bias_v2(m30_df, current_price, m30_atr)
# context["m30_bias"] still string (legacy consumers)
```

**Provisional bias NEVER hard-blocks:**

In M30_BIAS_BLOCK gate (event_processor), check richness:

```python
# BEFORE
if m30_bias == "bullish" and direction == "SHORT":
    emit_M30_BIAS_BLOCK()
    return None

# AFTER
bias_rich = context.get("m30_bias_rich", {})
bias = bias_rich.get("bias", "unknown")
is_confirmed = bias_rich.get("confirmed", False)

if bias in (BIAS_BULLISH_CONFIRMED,) and direction == "SHORT":
    emit_M30_BIAS_BLOCK()  # hard block only on CONFIRMED
    return None
if bias in (BIAS_BULLISH_PROVISIONAL,) and direction == "SHORT":
    # Provisional: soft-warn but don't block
    log.info(f"M30_BIAS_SOFT: provisional bullish — SHORT allowed with caution")
    # proceed without blocking
```

Mirror logic for BEARISH_CONFIRMED vs BEARISH_PROVISIONAL.

### 8.4 Consumer updates

For each UNSAFE consumer from 8.2, add guard:

```python
# If consumer broke on "unknown":
bias = context.get("m30_bias", "unknown")
if bias == "unknown":
    # Safe default (document why per consumer)
    pass  # OR fall back to h4_bias OR permit (no block)
```

Each guard documented in `PHASE_4_REPORT.md`.

### 8.5 Validation

Unit tests (`test_phase4_m30_bias_v2.py`) — 12 cases:
1. Empty df → UNKNOWN
2. NaN boundaries → UNKNOWN
3. Fresh bullish (top_ext only) → BULLISH_CONFIRMED
4. Fresh bearish (bot_ext only) → BEARISH_CONFIRMED
5. Both extended → UNKNOWN
6. Equality top == box_high (44.6% case) → UNKNOWN (regression test)
7. Provisional box (not confirmed) + top_ext → BULLISH_PROVISIONAL
8. Stale box (>M30_BOX_STALE_HOURS) + H4 bullish → h4_override BULLISH_CONFIRMED
9. Stale box + H4 unknown + no price → UNKNOWN
10. Fresh bullish + price far below (Component B trigger) → NEUTRAL_INVALIDATED
11. Fresh bearish + price far above → NEUTRAL_INVALIDATED
12. **Regression 2026-04-20 22:00 → 2026-04-21 05:00:** box_id=5243 bullish, price 4792 (28pts below box_low ≈4833 with ATR_INVALIDATION_MULT×ATR threshold) → NEUTRAL_INVALIDATED (not BULLISH_CONFIRMED)

All 12 must pass.

**Replay backtest today's session:**

```python
# Apply derive_m30_bias_v2 to every M1 bar 2026-04-20 22:00 → 2026-04-21 05:00
# Report: how many bars returned CONFIRMED vs INVALIDATED
# Expected: at some point during 28pt drop, should flip from BULLISH_CONFIRMED → NEUTRAL_INVALIDATED
# If flip never happens → calibration failed, STOP
```

### 8.6 Phase 4 report

`PHASE_4_REPORT.md`:
- Calibration results (Phase 4.0)
- Consumer inventory + guards
- Code diff (derive_m30_bias_v2 + wrapper + context)
- Unit test results (all 12 pass)
- Replay backtest showing today's session unblock

**STOP. Await Claude ratification for Phase 5.**

---

## 9. PHASE 5 — Fix #7: M5/M30 Discrepancy

### 9.1 Objective

Persistent M5/M30 contradiction → canonical event, not silent warn.

### 9.2 Implementation

Locate `M5/M30 DISCREPANCY` emission (per DIAGNOSIS §10.4 — `_validate_m5_vs_m30` at level_detector.py:619).

Add persistence tracker:

```python
_m5_m30_discrepancy_state = {
    "first_seen_at": None,
    "last_seen_at": None,
    "count": 0,
}

DISCREPANCY_PERSIST_MINUTES = 10  # operational — not a trading threshold

def _check_m5_m30_discrepancy(m5_liq_top, m5_liq_bot, m30_liq_top, m30_liq_bot) -> Optional[dict]:
    contradiction = (
        m5_liq_top is not None and m30_liq_bot is not None and m5_liq_top < m30_liq_bot
    ) or (
        m5_liq_bot is not None and m30_liq_top is not None and m5_liq_bot > m30_liq_top
    )
    
    now = _now_utc()
    if contradiction:
        if _m5_m30_discrepancy_state["first_seen_at"] is None:
            _m5_m30_discrepancy_state["first_seen_at"] = now
        _m5_m30_discrepancy_state["last_seen_at"] = now
        _m5_m30_discrepancy_state["count"] += 1
        
        persist_min = (now - _m5_m30_discrepancy_state["first_seen_at"]).total_seconds() / 60.0
        if persist_min >= DISCREPANCY_PERSIST_MINUTES:
            return {
                "persistent": True,
                "persist_minutes": persist_min,
                "count": _m5_m30_discrepancy_state["count"],
                "action": "suppress_m5_trigger_contra_m30",
            }
        return {"persistent": False, "action": "warn_only"}
    else:
        # Reset state on no contradiction
        _m5_m30_discrepancy_state["first_seen_at"] = None
        _m5_m30_discrepancy_state["count"] = 0
        return None
```

When `persistent=True`: emit canonical event `M5_M30_PERSISTENT_DISCREPANCY` via `_publish_decision_canonical` (Phase 1) + suppress M5 triggers that contradict M30 macro bias until contradiction resolves.

### 9.3 Validation

Unit test: simulate discrepancy <10min → warn only. Simulate >10min → persistent event emitted + trigger suppression flag raised.

### 9.4 Phase 5 report

`PHASE_5_REPORT.md`.

**STOP. Await Claude ratification for Phase 6.**

---

## 10. PHASE 6 — Fix #4: ApexNewsGate Re-enable

### 10.1 Objective

Fix import error. Expose health state.

### 10.2 Implementation

Locate startup import of ApexNewsGate. DIAGNOSIS §10.4: "ApexNewsGate not available -- trading without news gate: attempted relative import with no known parent package".

Fix: convert relative imports in ApexNewsGate module to absolute (similar pattern to FASE I news features restoration, memory #14):

```python
# BEFORE (relative, broken)
from ..news_state import NewsState
from .relevance_filter import filter_country_relevance

# AFTER (absolute)
from live.news_state import NewsState
from live.news_gate.relevance_filter import filter_country_relevance
```

At startup, catch import failures loudly:

```python
# BEFORE (swallows error)
try:
    from live.news_gate import ApexNewsGate
    NEWS_GATE = ApexNewsGate()
except Exception as e:
    log.debug(f"ApexNewsGate not available: {e}")  # SILENT
    NEWS_GATE = None

# AFTER (visible + health surface)
NEWS_GATE_STATE = {"status": "unknown", "error": None}
try:
    from live.news_gate import ApexNewsGate
    NEWS_GATE = ApexNewsGate()
    NEWS_GATE_STATE["status"] = "enabled"
except ImportError as e:
    NEWS_GATE = None
    NEWS_GATE_STATE["status"] = "unavailable"
    NEWS_GATE_STATE["error"] = str(e)
    log.error(f"ApexNewsGate import FAILED — trading without news protection: {e}", exc_info=True)
except Exception as e:
    NEWS_GATE = None
    NEWS_GATE_STATE["status"] = "degraded"
    NEWS_GATE_STATE["error"] = str(e)
    log.error(f"ApexNewsGate init FAILED: {e}", exc_info=True)
```

`service_state.json` update to include news_gate status:

```json
"news_gate": {
    "status": "enabled" | "degraded" | "unavailable",
    "error": null
}
```

### 10.3 Validation

- Startup probe: simulate service startup, confirm ApexNewsGate imports without error
- Health API: `/api/system_health` returns `news_gate.status`
- Induced failure: temporarily rename a dep → confirm `status=unavailable` with error message (restore after test)

### 10.4 Phase 6 report

`PHASE_6_REPORT.md`.

**STOP. Await Claude ratification for Phase 7.**

---

## 11. PHASE 7 — Fix #5: Feed Staleness + Restart

### 11.1 Objective

FEED_DEAD canonical + heartbeat independent of feed.

### 11.2 Implementation

FEED_DEAD emission must route through `_publish_decision_canonical` (Phase 1 surface).

`service_state.json` must expose:
```json
"feed": {
    "m1": {"status": "ok" | "stale" | "dead", "age_s": 12.4, "last_update": "..."},
    "m5": {...},
    "m30": {...}
}
```

Heartbeat thread updates `service_state.json.last_heartbeat_at` every 30s regardless of feed state.

### 11.3 Validation — BEFORE restart

1. py_compile ALL modified files across Phases 1-7
2. Import probe: every modified module imports without error
3. Unit tests (all phases): all pass
4. Staged changes audit: diff vs backup — ONLY expected files modified, ONLY expected line ranges
5. No accidental changes to:
   - Strategy thresholds (settings.json hash except news_gate additions)
   - Scoring logic
   - Executor routing
   - Lot sizing
   - Iceberg/anomaly code

### 11.4 Rollback plan (MUST be validated BEFORE restart)

```powershell
function Rollback-Sprint {
    # 1. Stop service
    Stop-Service -Name "FluxQuantumAPEX" -Force
    # 2. Restore ALL files from backup
    $BAK = "C:\FluxQuantumAI\sprints\sprint_apex_stabilization_7fixes_20260421\backup_pre_sprint"
    Copy-Item "$BAK\*.py" "C:\FluxQuantumAI\live\" -Force
    Copy-Item "$BAK\settings.json" "C:\FluxQuantumAI\config\" -Force
    # 3. Verify hashes match backup
    # 4. Restart service
    Start-Service -Name "FluxQuantumAPEX"
    # 5. Verify capture PIDs still alive
    Get-Process -Id 2512, 8248, 11740
}
```

**Dry-run rollback on a test file** to confirm Copy-Item + Get-FileHash work as expected.

### 11.5 Restart — Asian low-liquidity window only

**Condition:** restart ONLY between 22:00-23:00 UTC today. If current time outside window, **STOP and wait** (or Barbara explicit override).

```powershell
# 1. Snapshot pre-restart
$pre = @{
    service_pid = (Get-Process -Name "python*" | Where {...}).Id
    capture_pids = Get-Process -Id 2512, 8248, 11740
    settings_hash = (Get-FileHash "C:\FluxQuantumAI\config\settings.json").Hash
    last_decision = (Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" -Tail 1 | ConvertFrom-Json).timestamp
}
Write-Output $pre

# 2. Stop service (use NSSM name from pre-flight §4.1)
Stop-Service -Name "FluxQuantumAPEX"
# Wait up to 30s for PID to disappear

# 3. Start service
Start-Service -Name "FluxQuantumAPEX"
# Wait 10s, confirm new PID exists

# 4. Post-restart state
$post = @{
    new_service_pid = ...
    capture_pids_alive = ...  # MUST still be 2512, 8248, 11740
    news_gate_status = (Get-Content "C:\FluxQuantumAI\logs\service_state.json" | ConvertFrom-Json).news_gate.status
}
```

### 11.6 Post-restart observation 30 min — MANDATORY

Every 5 min, check:

1. New service PID still alive
2. Capture PIDs still alive (2512, 8248, 11740)
3. `decision_log.jsonl` receiving new entries
4. `service_state.json` heartbeat updating
5. `news_gate.status == "enabled"`
6. Distribution of `m30_bias` in recent decisions (should show flips, not stuck)
7. No unhandled exceptions in service_stderr.log

### 11.7 Rollback triggers (automatic)

Trigger rollback immediately if within 30 min:
- Service PID disappears and doesn't auto-restart
- Unhandled exceptions in stderr (not caught)
- decision_log.jsonl stops receiving entries for >5 min during market-open
- `m30_bias` stuck in single value for >15 min without flip
- Capture PIDs affected

Rollback = §11.4 procedure + report.

### 11.8 Triangulation validation

After 30min observation stable:

Capture ONE BLOCK decision (e.g., M30_BIAS_BLOCK — should be visible now).
Verify it appears in:
- `decision_live.json` (timestamp matches)
- `/api/live` (dashboard response)
- `decision_log.jsonl` (append present)
- Telegram payload (if sent / if dedup allows)

Must match EXACTLY. Same decision_id, same reason, same timestamp.

Repeat for ONE PM_EVENT (if available) or ONE FEED_DEAD (easier to induce if needed).

### 11.9 Phase 7 report

`PHASE_7_REPORT.md` with:
- Pre/post restart snapshot
- 30min observation log
- Triangulation evidence (3 sources match)
- Any anomalies observed

**STOP. Report to Barbara.**

---

## 12. Final Deliverable Structure

All in `C:\FluxQuantumAI\sprints\sprint_apex_stabilization_7fixes_20260421\`:

```
backup_pre_sprint/        ← rollback source
  MANIFEST.txt
  *.py, settings.json

PLAN.md                   ← sprint plan (pre-flight)
PHASE_1_REPORT.md         ← Fix #3
PHASE_2_REPORT.md         ← Fix #1
PHASE_3_REPORT.md         ← Fix #6
PHASE_4_0_CALIBRATION.md  ← Fix #2 data-driven X
PHASE_4_REPORT.md         ← Fix #2 code + tests + replay
PHASE_5_REPORT.md         ← Fix #7
PHASE_6_REPORT.md         ← Fix #4
PHASE_7_REPORT.md         ← Fix #5 + restart + triangulation

DIFF_SUMMARY.md           ← file-by-file, function-by-function
VALIDATION.md             ← py_compile, unit tests, triangulation
BACKTEST_NOTE.md          ← Fix #2 baseline vs new
NO_REGRESSION_STATEMENT.md ← explicit attestation

TASK_CLOSEOUT_REPORT.md   ← summary
```

---

## 13. No-Regression Statement (mandatory attestation)

At end of sprint, ClaudeCode must produce `NO_REGRESSION_STATEMENT.md`:

> I confirm that this sprint did NOT modify:
> - Strategy thresholds (except news_gate health surface additions)
> - Scoring / confidence contribution logic
> - Executor routing / broker logic
> - Lot sizing
> - Iceberg / anomaly detection logic
> - Calibration parameters CAL-1 through CAL-13
> - Capture services or their configuration
>
> Changes are confined to:
> - Canonical publish path (Fix #3)
> - M30_BIAS_BLOCK visibility (Fix #1)
> - Telegram observability (Fix #6)
> - derive_m30_bias regime recognition (Fix #2)
> - M5/M30 discrepancy handling (Fix #7)
> - ApexNewsGate import + health (Fix #4)
> - Feed staleness canonical (Fix #5)

With signed hashes of modified files vs backup.

---

## 14. Communication to Barbara (Claude-mediated)

Barbara is informed via Claude (ML Engineer acting as audit layer). ClaudeCode does NOT communicate directly to Barbara.

ClaudeCode reports to Claude:
- Pre-flight complete
- End of each Phase (STOP — await Claude ratification)
- Any STOP-AND-ASK trigger

Claude audits each report, reports summary to Barbara, requests ratification for next Phase.

Barbara ratifies each Phase. If any Phase rejected → sprint halts, rollback staged changes if any.

---

## 15. Begin

Execute Pre-flight §4 now. Report. STOP. Await Claude ratification for Phase 1.

# CLAUDECODE PROMPT — Sprint C Implementation v2 (derive_m30_bias Literatura-Aligned + H4 Gate)

**Sprint ID:** `entry_logic_fix_20260420` / Track C Implementation
**Authorization:** Barbara ratified Opção B with 4 mandatory adjustments (2026-04-20)
**Claude ML/AI Engineer:** Reviewed and approved Design Doc v2 with conditions
**ClaudeCode Role:** Executor under supervision — follow this prompt literally
**Prerequisite artifacts:**
- `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\DESIGN_DOC_m30_bias_literatura_aligned.md` (your v2 design doc, ratified)
- `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\BIAS_STUCK_INVESTIGATION.md`
- `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\ARCHITECTURAL_AUDIT_READ_ONLY.md`

---

## 0. GOLDEN RULE — What You MUST NOT Do

Read this section first. These are hard limits. Violation = stop, report to Barbara, do not proceed.

1. **NO write operations until Phase 1 (Discovery) is complete and reported to Barbara.**
2. **NO code outside the 3 files listed in §1.2.** If you think another file needs changing, STOP and ask.
3. **NO touching `C:\FluxQuantumAI\apex_nextgen\`** — that is a separate NextGen ML/RL/DL project. Out of scope.
4. **NO deploy (nssm restart) until:** (a) unit tests pass, (b) backtest counterfactual passes §8 sanity checks, (c) shadow mode 24h passes §9 criteria, (d) Barbara gives explicit deploy authorization via chat.
5. **NO scope expansion.** Anything not explicitly listed below is out of scope. Log ideas in a separate `FUTURE_WORK.md` — do not implement.
6. **NO removing / rewriting `derive_m30_bias` legacy function.** Adding `derive_m30_bias_v2` alongside. Legacy must remain callable for rollback.
7. **NO assumptions about parquet column names, schemas, or freshness.** Discovery phase verifies everything. See §2.
8. **NO `>= ` in place of `>` anywhere in `level_detector.py`.** Design doc §4.1-4.2 confirmed this would invert semantics. Do not revisit.
9. **NO git push without Barbara explicit authorization.** Local commits OK (repo is orphan, 0 commits — this sprint can be commit #1). Push only after deploy authorized.
10. **STOP and report if Discovery reveals any unexpected state:** schema drift, new columns, writer parked processes, file missing, etc.

---

## 1. Scope

### 1.1 What this sprint delivers

- New function `derive_h4_bias(h4_candles, h4_box_row=None, max_staleness_hours=6.0)` in `level_detector.py`
- New function `derive_m30_bias_v2(m30_df, h4_bias, current_price, iceberg_bias)` in `level_detector.py`
- H4 bias cache + getter `_get_h4_bias(cache_ttl_s=60.0)` in `event_processor.py`
- H4 counter-block enforcement in `_resolve_direction` (event_processor.py)
- Provisional override handling in hard-block at `event_processor.py:2391-2403`
- ADR-002 document at `C:\FluxQuantumAI\docs\adr\ADR-002-h4-authority-in-execution.md` (new dir if needed)
- Unit tests in `C:\FluxQuantumAI\tests\test_level_detector_v2.py` (new file)
- Backtest counterfactual script in `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backtest_counterfactual_sprint_c.py`
- Shadow mode 24h with metrics `h4_gate.*` and `h4_bias.*` (enforce=False initially)
- Task closeout report at `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\SPRINT_C_CLOSEOUT.md`

### 1.2 Files allowed to modify (whitelist)

| File | Type of change |
|---|---|
| `C:\FluxQuantumAI\live\level_detector.py` | ADD functions (derive_h4_bias, derive_m30_bias_v2). EDIT ADR-001 comment (see §5.5). Do NOT delete or alter `derive_m30_bias` legacy. |
| `C:\FluxQuantumAI\live\event_processor.py` | ADD `_get_h4_bias`, H4 gate call in `_resolve_direction`, provisional override in hard-block (2391-2403). |
| `C:\FluxQuantumAI\live\m30_updater.py` | **NO CHANGES in this sprint.** Read-only reference. |
| `C:\FluxQuantumAI\config\settings.json` | ADD `h4_gate` section (see §6.6). Backup first. |

Files created (new):
- `C:\FluxQuantumAI\docs\adr\ADR-002-h4-authority-in-execution.md`
- `C:\FluxQuantumAI\tests\test_level_detector_v2.py`
- `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backtest_counterfactual_sprint_c.py`
- `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\SPRINT_C_CLOSEOUT.md`
- `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backup_pre_sprint_c_<timestamp>\` (backup dir)
- `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\FUTURE_WORK.md` (if you have ideas out of scope)

Anything outside this whitelist requires explicit Barbara approval.

---

## 2. Mandatory Adjustments (from Claude review)

These are the 4 adjustments to your Design Doc v2 that Barbara ratified. Implement exactly as specified — no variations.

### Adjustment 1 — R_H4_2 logic: AND not OR

Your v2 pseudo-code at §6.2:

```python
hhhl = all(
    (last3[i]["high"] > last3[i-1]["high"]) or (last3[i]["low"] > last3[i-1]["low"])
    for i in (1, 2)
)
```

**Change to:**

```python
hhhl = all(
    (last3[i]["high"] > last3[i-1]["high"]) and (last3[i]["low"] > last3[i-1]["low"])
    for i in (1, 2)
)
llhl = all(
    (last3[i]["low"] < last3[i-1]["low"]) and (last3[i]["high"] < last3[i-1]["high"])
    for i in (1, 2)
)
```

Rationale: Dow theory + Wyckoff + ATS consensus is "higher highs AND higher lows" for continuation. OR is too permissive in pullbacks.

### Adjustment 2 — Mark thresholds as CALIBRATION_TBD

Every hardcoded threshold in `derive_h4_bias` must have an inline comment like:

```python
# CALIBRATION_TBD: threshold from Wyckoff strong-close convention (~0.7-0.8).
# Validated heuristically in Sprint C backtest. Data-driven recalibration in Sprint D.
CLOSE_PCT_BULL_THRESHOLD = 0.75
CLOSE_PCT_BEAR_THRESHOLD = 0.25
CONTINUATION_CANDLES = 3
CONTINUATION_MIN_GREEN = 2
```

Put them at module top as named constants, not magic numbers inline. Barbara needs to find them later for Sprint D calibration.

### Adjustment 3 — Shadow mode 24h is MANDATORY

Your §11.6 recommended shadow as optional. **Overruled.** Implement with `enforce=False` flag initially. Flip to `enforce=True` only after:

1. Backtest counterfactual passes §8.4 sanity checks
2. 24h live shadow window completed
3. Shadow metrics match backtest expectations (within tolerance §9)
4. Barbara gives explicit chat authorization to enforce

Config flag in `settings.json`:

```json
"h4_gate": {
    "enabled": true,
    "enforce": false,
    "shadow_started_at": "<set by deploy script>",
    "shadow_duration_hours": 24,
    "counter_block_max_pct_1h": 0.95
}
```

Details in §9.

### Adjustment 4 — Do NOT touch h4_updater writer. Flag it only.

`gc_h4_boxes.parquet` is stale 6 days (last 2026-04-14 14:00 UTC). This is **out of scope** for Sprint C.

Your job: use hybrid source (parquet if ≤6h fresh, else resample OHLCV). That's it.

Log a P1 backlog entry in `C:\FluxQuantumAI\sprints\BACKLOG.md` (append, create if missing) titled:

```
## Sprint H4-WRITER-FIX (P1)
Status: NEW (discovered 2026-04-20 during Sprint C)
Problem: gc_h4_boxes.parquet writer stopped at 2026-04-14 14:00 UTC.
Impact: Sprint C falls back to resample OHLCV when parquet >6h stale. Resample lacks ATS trend line semantics.
Owner: TBD by Barbara
```

Do not investigate the writer. Do not attempt to restart it. Do not guess the cause. Just log the backlog entry.

---

## 3. Open Questions — Final Decisions

Your Design Doc §11 had 8 Open Questions. Final decisions (Barbara ratified):

| # | Question | Decision |
|---|---|---|
| 11.1 | Option A/B/C? | **B** with Adjustments 1-4 |
| 11.2 | H4 source? | **Hybrid** — parquet if staleness ≤6h, else resample OHLCV |
| 11.3 | H4 neutral handling? | **Strict block** — no trades when H4 neutral |
| 11.4 | H4 current incomplete candle? | **Ignore** — use only completed bars |
| 11.5 | Overextension reversal? | **H4 gate universal** — applies to overextension trades too |
| 11.6 | Shadow mode? | **Mandatory 24h** (Adjustment 3) |
| 11.7 | R_M30_2 secondary conf? | **Minimalist** — `price > box_high` OR `iceberg_bias="bullish"` (symmetric bearish). No additions. |
| 11.8 | ADR-001? | **ADR-002 supersedes** ADR-001. Do not delete ADR-001 comment — amend it with link to ADR-002. |

---

## 4. Phase 0 — Safety & Backup (FIRST, BEFORE ANY EDITS)

Run these commands in sequence. Report output to Barbara before proceeding to Phase 1.

```powershell
# 4.1 — Create backup dir
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$bakDir = "C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backup_pre_sprint_c_$ts"
New-Item -ItemType Directory -Path $bakDir -Force

# 4.2 — Copy the 3 modifiable live files + settings
Copy-Item "C:\FluxQuantumAI\live\level_detector.py"  "$bakDir\" -Force
Copy-Item "C:\FluxQuantumAI\live\event_processor.py" "$bakDir\" -Force
Copy-Item "C:\FluxQuantumAI\live\m30_updater.py"     "$bakDir\" -Force  # reference only, for diff
Copy-Item "C:\FluxQuantumAI\config\settings.json"    "$bakDir\" -Force

# 4.3 — Write MANIFEST.json
$manifest = @{
    sprint = "entry_logic_fix_20260420 / Track C"
    backup_created_at = $ts
    files = @("level_detector.py","event_processor.py","m30_updater.py","settings.json")
    restore_command = "Copy-Item '$bakDir\*' 'C:\FluxQuantumAI\live\' -Force  # plus settings"
    git_head = (git -C C:\FluxQuantumAI rev-parse HEAD 2>$null)
} | ConvertTo-Json -Depth 5
Set-Content "$bakDir\MANIFEST.json" $manifest

# 4.4 — Verify backup integrity
Get-ChildItem $bakDir | Format-Table Name, Length
```

**Checkpoint 4 — report to Barbara:**
- Backup dir path
- MANIFEST.json contents
- File sizes (sanity check vs live)

**Do not proceed to Phase 1 until Barbara acknowledges the backup.**

---

## 5. Phase 1 — Discovery (READ-ONLY)

**NO file edits in this phase.** Pure investigation. Produce a discovery report and wait for Barbara acknowledgment.

### 5.1 Code discovery — verify assumptions in Design Doc

Read each file and produce a short summary of the CURRENT state (what the code actually does as of today, not what the design doc assumed):

1. **`level_detector.py` lines 1-50** — Confirm ADR-001 comment exists at line 18. Exact text?
2. **`level_detector.py` lines 215-263** — Confirm `derive_m30_bias` signature and return type. Any changes since design doc?
3. **`level_detector.py` lines 232-243** — Confirm `_classify` helper semantics.
4. **`event_processor.py` line 1626 area** — Confirm `self.m30_bias = derive_m30_bias(...)` call site. Line number may have drifted.
5. **`event_processor.py` lines 2391-2403** — Confirm hard-block logic. Exact conditionals?
6. **`event_processor.py` `_resolve_direction`** — Locate, report full signature and body. Likely around :3340+ per design doc but verify.
7. **`event_processor.py` lines 704-723** — Confirm `_read_d1h4_bias_shadow` exists. Its current behavior?
8. **`event_processor.py` main tick loop** — Locate (design doc says :4268). Confirm where `_resolve_direction` is called.
9. **`m30_updater.py` lines 261-304** — Confirm writer semantics for `liq_top`/`liq_bot` per §4.1.

### 5.2 Data discovery — verify parquet state

For each parquet, run READ-ONLY python and report:

```python
# Run in C:\FluxQuantumAI with apex env active
import pandas as pd
import os

for path in [
    r"C:\data\processed\gc_m30_boxes.parquet",
    r"C:\data\processed\gc_h4_boxes.parquet",
    r"C:\data\processed\gc_ohlcv_l2_joined.parquet",
    r"C:\data\processed\gc_ats_features_v4.parquet",
]:
    print(f"\n=== {path} ===")
    if not os.path.exists(path):
        print("  MISSING")
        continue
    df = pd.read_parquet(path)
    print(f"  rows: {len(df)}")
    print(f"  columns: {list(df.columns)[:20]}{'...' if len(df.columns)>20 else ''}")
    print(f"  index type: {type(df.index).__name__}")
    if hasattr(df.index, "max"):
        print(f"  index max: {df.index.max()}")
        print(f"  index min: {df.index.min()}")
    print(f"  file mtime: {pd.Timestamp(os.path.getmtime(path), unit='s')}")
```

Report the output verbatim. Do NOT assume columns — read them.

### 5.3 Config discovery

```powershell
Get-Content "C:\FluxQuantumAI\config\settings.json" | ConvertFrom-Json | ConvertTo-Json -Depth 10
```

Report current structure. Check if `h4_gate` section already exists (it should not).

### 5.4 Decision log freshness

```powershell
$log = "C:\FluxQuantumAI\logs\decision_log.jsonl"
Write-Host "Size: $((Get-Item $log).Length / 1MB) MB"
Write-Host "Last modified: $((Get-Item $log).LastWriteTime)"
Write-Host "Line count: $((Get-Content $log | Measure-Object -Line).Lines)"
Write-Host "Last 3 entries:"
Get-Content $log -Tail 3
```

### 5.5 Git state

```powershell
cd C:\FluxQuantumAI
git status
git log --oneline | Select-Object -First 5
git branch --show-current
```

Confirm repo is orphan (0 commits) per memory 32. If commits exist now, report them.

### 5.6 Service state

```powershell
# Confirm FluxQuantumAPEX service state — do NOT restart
& "C:\tools\nssm\nssm.exe" status FluxQuantumAPEX
Get-Process -Name python* -ErrorAction SilentlyContinue | Format-Table Id, ProcessName, StartTime
```

### 5.7 Discovery report

Produce `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\SPRINT_C_DISCOVERY.md` with all findings above. Report to Barbara.

**Checkpoint 5 — Barbara acknowledgment required before Phase 2.**

If any discovery finding contradicts the Design Doc v2 assumptions (e.g., different column names, missing parquet, ADR-001 text different, line numbers drifted significantly), STOP and report deviation. Do not auto-adjust.

---

## 6. Phase 2 — Implementation (granular steps)

Only begin after Phase 1 discovery is ack'd. Implement in sub-phases 2a → 2e, with a commit at the end of each. Test after each sub-phase.

### 6.1 Phase 2a — `derive_h4_bias` in level_detector.py

**Location:** Add after `derive_m30_bias` function. Do not modify `derive_m30_bias`.

**Constants at module top (per Adjustment 2):**

```python
# ------------------------------------------------------------------
# H4 bias thresholds (Sprint C v2 — literatura-aligned)
# CALIBRATION_TBD: heuristic values from Wyckoff strong-close / ATS continuation.
# Data-driven recalibration planned for Sprint D (post-shadow).
# ------------------------------------------------------------------
H4_CLOSE_PCT_BULL_THRESHOLD = 0.75   # R_H4_1: close in upper quartile
H4_CLOSE_PCT_BEAR_THRESHOLD = 0.25   # R_H4_4: close in lower quartile
H4_CONTINUATION_WINDOW      = 3      # R_H4_2/5: last N candles
H4_CONTINUATION_MIN_SAME    = 2      # R_H4_2/5: min same-color candles
H4_CONF_STRONG              = 0.70   # both R_H4_1 AND R_H4_2 fire
H4_CONF_SINGLE              = 0.55   # only one rule fires
H4_CONF_JAC_CONFIRMED       = 0.95   # R_H4_3/6 via h4_jac_dir parquet
H4_MAX_STALENESS_HOURS_DEFAULT = 6.0
```

**Function spec:**

```python
def derive_h4_bias(
    h4_candles,                    # list[dict] or pd.DataFrame last N completed H4 candles
    h4_box_row=None,               # optional: latest h4_boxes parquet row (dict or pd.Series)
    max_staleness_hours: float = H4_MAX_STALENESS_HOURS_DEFAULT,
    now_utc=None,                  # for test determinism; defaults to datetime.now(UTC)
):
    """
    Derive H4 directional bias per ATS Trade System :1119 ("four hour is authority")
    and Strategic Plan §4-5 ("no exceptions").

    Returns
    -------
    (bias: str, confidence: float, metadata: dict)
        bias        : "bullish" | "bearish" | "neutral"
        confidence  : float in [0.0, 1.0]
        metadata    : {
            "source": "h4_boxes_parquet" | "ohlcv_resample" | "h4_boxes_parquet_fallback"
                      | "neutral_default" | "insufficient_data" | "error",
            "rules_fired": list[str],
            "staleness_hours": float | None,
            "last_h4_close_ts": str | None,
            "error": str (only if source=="error"),
        }

    Rules
    -----
    R_H4_3 / R_H4_6 — from h4_jac_dir parquet (if fresh and confirmed)
    R_H4_1 / R_H4_4 — body direction + close position in quartile
    R_H4_2 / R_H4_5 — continuation via higher-highs AND higher-lows (Adjustment 1: AND not OR)
    R_H4_7         — neutral (no rules fire)

    Guardrails
    ----------
    - If h4_candles empty or len < 1 → neutral, conf=0.0, source="insufficient_data"
    - If both bull and bear rules fire → neutral (conflicting signals)
    - Any exception → neutral, source="error"; log exception at WARN level
    """
```

Implement per your Design Doc §6.2 BUT with Adjustment 1 (AND/AND). Use the named constants.

**Accepting input as list[dict] or DataFrame:** normalize at top of function. Expect keys `open, high, low, close` for candles. For `h4_box_row`: expect `h4_jac_dir` (str "UP"/"DN"/""), `h4_box_confirmed` (bool). Use `.get` safely.

**Testing checkpoint:** after implementing 2a, run:

```powershell
cd C:\FluxQuantumAI
C:\Python\python.exe -c "from live.level_detector import derive_h4_bias, H4_CLOSE_PCT_BULL_THRESHOLD; print('IMPORT OK'); print(derive_h4_bias([]))"
```

Must print `IMPORT OK` and `('neutral', 0.0, {...'source': 'insufficient_data'...})`.

**Commit 2a:**

```powershell
cd C:\FluxQuantumAI
git add live/level_detector.py
git commit -m "Sprint C 2a: add derive_h4_bias (R_H4_1..7, literatura-aligned AND continuation)

- New function per Design Doc v2 §6.2 with Adjustment 1 (AND not OR).
- Named constants at module top (CALIBRATION_TBD for Sprint D).
- derive_m30_bias legacy intact.
- Refs: ATS Trade System :1119, Strategic Plan §4-5, §8.0.
"
```

### 6.2 Phase 2b — `derive_m30_bias_v2` in level_detector.py

**Spec:**

```python
def derive_m30_bias_v2(
    m30_df,                  # DataFrame indexed by timestamp with columns incl. liq_top, liq_bot, box_high, box_low, confirmed (bool)
    h4_bias: str,            # "bullish" | "bearish" | "neutral" (from derive_h4_bias)
    current_price: float,    # latest MT5 price
    iceberg_bias: str = "",  # "bullish" | "bearish" | "" (from latest iceberg signal, optional)
):
    """
    Derive M30 bias per literatura with H4 gate enforcement.

    Invariants
    ----------
    I-1: H4 bullish → NEVER returns "bearish" (at most "neutral")
    I-2: H4 bearish → NEVER returns "bullish"
    I-3: H4 neutral → returns "neutral" (strict block, Barbara decision 11.3)
    I-4: Deterministic
    I-5: Never inverts bias without H4 alignment + secondary confirmation

    Returns
    -------
    (bias, is_authoritative, metadata)
        bias             : "bullish" | "bearish" | "neutral"
        is_authoritative : True if R_M30_1/R_M30_3 (confirmed); False if R_M30_2/R_M30_4 (provisional)
        metadata         : {
            "rule_fired": "R_M30_1"|"R_M30_2"|"R_M30_3"|"R_M30_4"|"R_M30_5"|"R_M30_6",
            "h4_bias": <h4_bias arg>,
            "provisional_override": bool,
            "secondary_conf": str | None (what triggered secondary),
            "m30_last_confirmed_idx": str | None,
            "m30_last_unconfirmed_idx": str | None,
        }

    Rules (per Design Doc §2.7)
    ---------------------------
    R_M30_1: H4 bullish + confirmed M30 UP fakeout → bullish, authoritative
    R_M30_2: H4 bullish + unconfirmed M30 UP fakeout + (price>box_high OR iceberg=bullish) → bullish, provisional
    R_M30_3: symmetric R_M30_1 for bearish
    R_M30_4: symmetric R_M30_2 for bearish
    R_M30_5: H4 counter to M30 structure → neutral (reason=h4_counter_block)
    R_M30_6: H4 neutral → neutral (strict per 11.3)
    """
```

**Implementation rules:**

- Use existing `derive_m30_bias` legacy to get `confirmed_bias` (pass `confirmed_only=True`).
- Read last unconfirmed row from `m30_df` (filter `confirmed == False`, take last).
- UP fakeout check: `liq_top > box_high` (do NOT use `>=`).
- DN fakeout check: `liq_bot < box_low`.
- Secondary confirmation (exactly 2 conditions per Decision 11.7): `current_price > box_high` OR `iceberg_bias == "bullish"` (symmetric for bearish).
- Return `is_authoritative=True` only for R_M30_1 / R_M30_3.
- If H4 bias empty/unknown → treat as neutral → R_M30_6.

**Commit 2b:**

```powershell
git add live/level_detector.py
git commit -m "Sprint C 2b: add derive_m30_bias_v2 with H4 gate + provisional override

- R_M30_1..6 per Design Doc v2 §2.7.
- Invariants I-1..5 enforced (H4 bullish never returns bearish, etc).
- Strict H4 neutral block per Barbara decision 11.3.
- Secondary conf minimalist (price>box_high OR iceberg) per decision 11.7.
- derive_m30_bias legacy still intact for rollback.
"
```

### 6.3 Phase 2c — H4 cache + resample helper in event_processor.py

**Location:** Inside `event_processor.py` class (same class that has the tick loop). Add private helpers near existing `_read_d1h4_bias_shadow`.

**Spec:**

```python
def _resample_h4_candles(self, last_n: int = 20):
    """
    Resample OHLCV to H4 bars. Returns list[dict] with keys open/high/low/close.
    Excludes the current incomplete bar (per Barbara decision 11.4).

    Uses gc_ohlcv_l2_joined.parquet as source. Reads only the tail needed (last ~5 days)
    for performance.
    """

def _read_last_h4_box_row(self):
    """
    Read latest row from gc_h4_boxes.parquet. Return None if file missing or read fails.
    Return staleness in metadata (caller decides whether to use).
    """

def _get_h4_bias(self, cache_ttl_s: float = 60.0):
    """
    Returns (bias, confidence, metadata). Cached for cache_ttl_s seconds (per Design Doc §7.4).

    Flow
    ----
    1. Check cache TTL
    2. Try _read_last_h4_box_row; if staleness ≤6h, pass to derive_h4_bias
    3. Resample OHLCV via _resample_h4_candles(last_n=20)
    4. Call derive_h4_bias(h4_candles, h4_box_row)
    5. Emit metrics (see §11):
       - h4_bias.source.<source>
       - h4_bias.rule_fired.<rule>
       - h4_bias.staleness_hours (gauge)
    6. Cache and return

    Error handling: any exception → return ("neutral", 0.0, {"source": "error", "error": str}).
    Per Adjustment 3, neutral default = no trades = safest.
    """
```

**Edge cases you MUST handle:**

- `gc_h4_boxes.parquet` missing → `h4_box_row = None`, log once at INFO (not WARN repeatedly).
- `gc_ohlcv_l2_joined.parquet` missing or unreadable → return `("neutral", 0.0, {"source": "error"})` and log WARN.
- Resample produces < 3 bars → `derive_h4_bias` returns `insufficient_data`, pass through.
- Current partial bar: `resample("4h")` with pandas produces a partial bar for the current 4h window. You MUST exclude it. Criterion: last included bar index + 4h ≤ `now_utc`. Implement this explicitly.

**Cache invalidation:** time-based only (TTL). No manual invalidation.

**Commit 2c:**

```powershell
git add live/event_processor.py
git commit -m "Sprint C 2c: add _get_h4_bias cache + H4 resample helper

- _resample_h4_candles (excludes current partial bar per decision 11.4)
- _read_last_h4_box_row (gc_h4_boxes.parquet, returns None if missing)
- _get_h4_bias with 60s TTL cache, hybrid source (parquet ≤6h else resample)
- Error path → neutral (safest per Adjustment 3)
"
```

### 6.4 Phase 2d — `_resolve_direction` H4 counter-block

**Location:** Inside `_resolve_direction` method (locate via Phase 1 §5.1.6).

**Integration spec** (per Design Doc §7.2.A):

```python
def _resolve_direction(self, level_type):
    # ... existing logic producing tentative_direction ...

    # Sprint C v2 — H4 gate
    h4_bias, h4_conf, h4_meta = self._get_h4_bias()

    # Feature flag from settings
    h4_gate_cfg = self._settings.get("h4_gate", {})
    h4_gate_enabled = h4_gate_cfg.get("enabled", True)
    h4_gate_enforce = h4_gate_cfg.get("enforce", False)

    if h4_gate_enabled and tentative_direction is not None:
        counter = (
            (h4_bias == "bullish" and tentative_direction == "SHORT") or
            (h4_bias == "bearish" and tentative_direction == "LONG")
        )
        neutral_block = (h4_bias == "neutral" and tentative_direction is not None)

        if counter:
            self._metric_incr(f"h4_gate.counter_block.{tentative_direction.lower()}")
            log_msg = f"H4_COUNTER_BLOCK: h4={h4_bias} blocks {tentative_direction} (enforce={h4_gate_enforce})"
            if h4_gate_enforce:
                log.info(log_msg)
                return (None, f"H4_COUNTER:{h4_bias}_blocks_{tentative_direction}")
            else:
                log.info(f"SHADOW: {log_msg} (would_block but enforce=False)")
                # Do not return None — let decision flow through

        if neutral_block:
            self._metric_incr("h4_gate.neutral_block")
            log_msg = f"H4_NEUTRAL_BLOCK: h4=neutral blocks {tentative_direction} (enforce={h4_gate_enforce})"
            if h4_gate_enforce:
                log.info(log_msg)
                return (None, "H4_NEUTRAL_BLOCK")
            else:
                log.info(f"SHADOW: {log_msg}")

    # ... continue with existing return path ...
```

**Critical requirements:**

- Respects `enforce=False` during shadow (does NOT block, only logs + metrics).
- Emits structured log `SHADOW:` prefix when shadow-blocking so backtest can grep.
- `_metric_incr` emits the counter regardless of enforce mode.
- Decision log (`decision_log.jsonl`) must include new fields: `h4_bias`, `h4_gate_would_block`, `h4_gate_enforced`, `h4_meta_source`.

**Commit 2d:**

```powershell
git add live/event_processor.py config/settings.json
git commit -m "Sprint C 2d: H4 gate in _resolve_direction (counter-block + neutral-block)

- Shadow-mode capable via settings.h4_gate.enforce flag
- Emits h4_gate.counter_block.{short|long} and h4_gate.neutral_block metrics
- decision_log gains h4_bias, h4_gate_would_block, h4_gate_enforced, h4_meta_source
- Literatura ref: ATS Strategic Plan §8.0 no-exceptions
"
```

### 6.5 Phase 2e — provisional override in hard-block (2391-2403)

**Location:** `event_processor.py` lines around 2391-2403 (verify in discovery).

**Current behaviour (pre-Sprint C):** hard-blocks LONG when `self.m30_bias == "bearish"` confirmed. Ignores provisional.

**New behaviour:** compute v2 bias using `derive_m30_bias_v2`, check `is_authoritative`. If not authoritative (i.e., provisional override from R_M30_2/4), allow the direction.

**Integration pattern (pseudo):**

```python
# BEFORE (existing)
if self.m30_bias == "bearish" and proposed_direction == "LONG":
    return BLOCK

# AFTER
h4_bias, _, _ = self._get_h4_bias()
m30_v2_bias, is_auth, m30_v2_meta = derive_m30_bias_v2(
    m30_df=self._m30_df,
    h4_bias=h4_bias,
    current_price=self._current_price,
    iceberg_bias=self._latest_iceberg_bias or "",
)

h4_gate_cfg = self._settings.get("h4_gate", {})
h4_gate_enforce = h4_gate_cfg.get("enforce", False)

# Shadow-aware
if h4_gate_enforce:
    effective_m30_bias = m30_v2_bias
else:
    effective_m30_bias = self.m30_bias  # legacy behavior during shadow
    log.info(f"SHADOW: m30_v2={m30_v2_bias} (auth={is_auth}, rule={m30_v2_meta.get('rule_fired')})")

if effective_m30_bias == "bearish" and proposed_direction == "LONG":
    return BLOCK  # unchanged semantics under shadow
# symmetric for SHORT
```

Do NOT remove the legacy check. Overlay the v2 check.

**Commit 2e:**

```powershell
git add live/event_processor.py
git commit -m "Sprint C 2e: provisional override in hard-block via derive_m30_bias_v2

- Uses derive_m30_bias_v2 when enforce=True
- Falls back to legacy derive_m30_bias when enforce=False (shadow)
- Logs SHADOW: with v2 output for diff analysis
"
```

### 6.6 settings.json additions

Backup first, then edit:

```json
"h4_gate": {
    "enabled": true,
    "enforce": false,
    "shadow_started_at": null,
    "shadow_duration_hours": 24,
    "counter_block_max_pct_1h": 0.95,
    "cache_ttl_s": 60,
    "max_staleness_hours": 6.0,
    "resample_last_n": 20
}
```

Validate JSON syntax with `ConvertFrom-Json` before committing.

### 6.7 Phase 2f — ADR-002

Create `C:\FluxQuantumAI\docs\adr\ADR-002-h4-authority-in-execution.md`:

```markdown
# ADR-002: H4 authority in execution (supersedes ADR-001)

**Date:** 2026-04-20
**Status:** ACCEPTED
**Supersedes:** ADR-001 ("H4/D1 levels are NEVER used for execution")
**Approved by:** Barbara (FluxFoxLabs PO), Claude (ML/AI Engineer review)

## Context

ADR-001 was written to prevent the system going idle when H4 parquet data became stale. Live evidence 2026-04-20 demonstrated the inverse problem was worse: ignoring H4 direction led to 91h stuck m30_bias=bearish and 69 counter-trend SHORTs against visibly bullish H4 candles.

ATS Trade System :1119 states unambiguously: "the four hour is the authority over the smaller time frames." ATS Strategic Plan §8.0: "Adhere to the Directional Bias... No exceptions."

## Decision

H4 direction IS consulted for execution gating. Implemented via:
- `derive_h4_bias` in `level_detector.py`
- Hybrid source: `gc_h4_boxes.parquet` if ≤6h fresh, else OHLCV resample
- H4 counter-block enforced in `_resolve_direction`
- Strict neutral (H4 neutral = no trades) per literatura

## Guard-rails (addressing ADR-001 original concern)

- H4 data unavailable / error → defaults to "neutral" → no trades (fail-safe)
- Staleness threshold 6h configurable
- Shadow mode 24h required before enforce
- Observability: `h4_bias.source`, `h4_bias.flips_per_24h`, `h4_gate.counter_block.*`

## Consequences

- Counter-H4 trading eliminated (positive).
- Trade count drops when H4 neutral (acceptable trade-off per literatura).
- Stale H4 writer is now a critical dependency — backlogged as Sprint H4-WRITER-FIX (P1).

## References

- Sprint C Design Doc v2: `entry_logic_fix_20260420/DESIGN_DOC_m30_bias_literatura_aligned.md`
- Claude review + Barbara ratification: chat 2026-04-20
- Literature: ATS Trade System :1119, Strategic Plan §4-5 + §8.0, Wyckoff Phase D, ICT BOS
```

Amend ADR-001 comment in `level_detector.py:18` (do NOT delete):

```python
# ADR-001: H4/D1 levels WERE not used for execution (written to prevent stale-data idle).
# SUPERSEDED by ADR-002 (2026-04-20) — see docs/adr/ADR-002-h4-authority-in-execution.md
# Live evidence 2026-04-20 proved inverse risk (91h stuck, 69 counter-trend SHORTs)
# outweighed stale-data risk. H4 now consulted with fail-safe neutral default.
```

**Commit 2f:**

```powershell
git add docs/adr/ADR-002-h4-authority-in-execution.md live/level_detector.py
git commit -m "Sprint C 2f: ADR-002 supersedes ADR-001 (H4 authority in execution)"
```

---

## 7. Phase 3 — Unit Tests (MANDATORY before backtest)

Create `C:\FluxQuantumAI\tests\test_level_detector_v2.py`.

**Minimum test coverage:**

### 7.1 derive_h4_bias tests

- `test_h4_empty_candles_returns_neutral` — `[]` → `("neutral", 0.0, ...)` source=insufficient_data
- `test_h4_r_h4_1_strong_close_bullish` — 1 candle with close_pct=0.9, body_up=True → bullish, rule_fired includes "R_H4_1"
- `test_h4_r_h4_1_not_enough_close_pct` — close_pct=0.6 → does not fire R_H4_1
- `test_h4_r_h4_4_weak_close_bearish` — close_pct=0.1, body_down=True → bearish
- `test_h4_r_h4_2_continuation_bull_and_not_or` — 3 candles all higher_high AND higher_low, 2+ green → bullish (Adjustment 1 explicit: AND not OR). Include test where higher_high but lower_low → should NOT fire.
- `test_h4_r_h4_5_continuation_bear_and_not_or` — symmetric
- `test_h4_r_h4_3_parquet_jac_up_fresh` — box_row with h4_jac_dir="UP", confirmed=True, staleness=1h → bullish, source="h4_boxes_parquet", conf=H4_CONF_JAC_CONFIRMED
- `test_h4_r_h4_3_parquet_jac_up_stale_6h` — same but staleness=7h → falls back to candle analysis, source="ohlcv_resample" or "h4_boxes_parquet_fallback"
- `test_h4_conflicting_rules_returns_neutral` — bull and bear rules both fire → neutral
- `test_h4_exception_returns_neutral_error` — pass malformed candles → neutral, source="error"

### 7.2 derive_m30_bias_v2 tests

- `test_m30_v2_r_m30_1_h4_bull_confirmed_up_fakeout` → bullish, is_auth=True, rule=R_M30_1
- `test_m30_v2_r_m30_2_h4_bull_unconfirmed_up_secondary_price` → bullish, is_auth=False, rule=R_M30_2
- `test_m30_v2_r_m30_2_h4_bull_unconfirmed_up_secondary_iceberg` → bullish provisional via iceberg
- `test_m30_v2_r_m30_2_no_secondary_falls_through` → not R_M30_2 without secondary conf
- `test_m30_v2_r_m30_3_h4_bear_confirmed_dn_fakeout` → bearish, is_auth=True
- `test_m30_v2_r_m30_5_h4_counter_block` — H4 bullish + M30 DN confirmed → neutral, rule=R_M30_5 (invariant I-1)
- `test_m30_v2_r_m30_6_h4_neutral_block` — H4 neutral → neutral (per decision 11.3)
- `test_m30_v2_invariant_i1` — for 50 synthetic cases with H4=bullish, assert never returns "bearish"
- `test_m30_v2_invariant_i2` — symmetric
- `test_m30_v2_invariant_i4_determinism` — same input 10x → same output 10x

### 7.3 Run tests

```powershell
cd C:\FluxQuantumAI
C:\Python\python.exe -m pytest tests\test_level_detector_v2.py -v
```

**Acceptance criteria:** 100% pass. No skipped. No xfail.

**Commit 3:**

```powershell
git add tests/test_level_detector_v2.py
git commit -m "Sprint C Phase 3: unit tests for derive_h4_bias + derive_m30_bias_v2

- R_H4_1..7 coverage incl. Adjustment 1 (AND continuation)
- R_M30_1..6 coverage + invariants I-1/I-2/I-4
- All green: <paste pytest output line count>
"
```

Report full pytest output to Barbara.

---

## 8. Phase 4 — Backtest Counterfactual (MANDATORY before shadow)

Create `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backtest_counterfactual_sprint_c.py`.

### 8.1 Backtest goal

Replay `decision_log.jsonl` (last 6 days, 2026-04-14 → 2026-04-20, ~12,022 decisions).

For each decision, compute what `derive_h4_bias` + `derive_m30_bias_v2` would have returned. Classify outcome.

### 8.2 Implementation

```python
"""
Backtest counterfactual Sprint C v2.
Reads decision_log.jsonl + parquets, replays through derive_h4_bias/derive_m30_bias_v2,
emits CSV report.
"""

# Inputs:
DECISION_LOG = r"C:\FluxQuantumAI\logs\decision_log.jsonl"
OHLCV_PARQUET = r"C:\data\processed\gc_ohlcv_l2_joined.parquet"
H4_BOXES_PARQUET = r"C:\data\processed\gc_h4_boxes.parquet"
M30_PARQUET = r"C:\data\processed\gc_m30_boxes.parquet"
OUTPUT_CSV = r"C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backtest_sprint_c_output.csv"
OUTPUT_SUMMARY = r"C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backtest_sprint_c_summary.md"

# For each decision:
# 1. Extract ts, proposed_direction, m30_bias_old, provisional_m30_bias, price_mt5, iceberg_bias
# 2. Compute h4_candles via ohlcv.resample("4h") up to ts (exclude current partial bar)
# 3. Compute h4_box_row from H4_BOXES_PARQUET at or before ts (check staleness)
# 4. Call derive_h4_bias → h4_bias_v2, conf, meta
# 5. Filter m30 rows up to ts, call derive_m30_bias_v2 → bias_v2, is_auth, meta
# 6. Classify:
#    - IDENTICAL: v2 allows same direction as old
#    - BLOCKED_BY_H4: v2 blocks via R_M30_5 (counter-H4)
#    - BLOCKED_BY_H4_NEUTRAL: v2 blocks via R_M30_6
#    - PROVISIONAL_ALLOW: old blocked but v2 allows via R_M30_2/4
#    - FLIPPED: v2 allows OPPOSITE direction
# 7. Emit row to CSV: ts, old_direction, old_m30_bias, h4_bias_v2, m30_v2_bias, classification, rule_fired

# Then compute summary metrics per §8.3.
```

### 8.3 Summary metrics (compute and emit)

| Metric | Expected |
|---|---|
| % 69 SHORTs (2026-04-20 01:00-09:00) blocked via H4_COUNTER | 95-100% |
| % LONGs emergent 2026-04-20 08:00-10:00 via R_M30_2 | 1-5 decisions |
| % IDENTICAL total | 60-80% |
| % BLOCKED_BY_H4 total | 15-30% |
| % BLOCKED_BY_H4_NEUTRAL total | 5-15% |
| % PROVISIONAL_ALLOW | 2-8% |
| % FLIPPED | <1% (red flag if higher) |
| Stuck duration (h4_bias changes per 24h) | Measure baseline |

### 8.4 Sanity checks (MUST pass)

1. **No silent flips:** every v2 decision divergent from old must have `h4_bias != "neutral"` OR `provisional_override=True with secondary_conf`. Count violations; must be 0.
2. **Counter-H4 totals = 0:** Zero SHORTs from v2 when h4_bias="bullish". Zero LONGs when h4_bias="bearish".
3. **H4 neutral regime no over-permissive LONGs:** in neutral H4 windows, count LONG emissions; must be 0.
4. **Historical uptrend (choose Nov 2025 window — 10 days):** v2 should ALLOW continuation trades aligned with H4. Count must be > 0. If v2 blocks everything, something is over-tuned.

### 8.5 Deliverables

- `backtest_sprint_c_output.csv` — all rows
- `backtest_sprint_c_summary.md` — metrics table + sanity check pass/fail
- Report to Barbara before shadow deploy

**Commit 4:**

```powershell
git add sprints/entry_logic_fix_20260420/backtest_counterfactual_sprint_c.py \
         sprints/entry_logic_fix_20260420/backtest_sprint_c_summary.md
git commit -m "Sprint C Phase 4: backtest counterfactual — <pass/fail>"
```

**Checkpoint 4 — Barbara approval required before shadow deploy.** If any sanity check fails or metrics out of expected range, STOP. Do not deploy.

---

## 9. Phase 5 — Shadow Mode 24h (MANDATORY before enforce)

### 9.1 Pre-deploy checklist

- [ ] Phase 0 backup verified
- [ ] Phases 2a-2f implementation complete, all commits done
- [ ] Phase 3 unit tests 100% green
- [ ] Phase 4 backtest summary reviewed by Barbara
- [ ] `settings.json` has `h4_gate.enforce=false`
- [ ] `shadow_started_at` will be set by deploy script

### 9.2 Deploy to shadow

```powershell
# Set shadow start timestamp
$now = Get-Date -AsUTC -Format "yyyy-MM-ddTHH:mm:ssZ"
# Edit settings.json to set h4_gate.shadow_started_at = $now
# (Do this via Python script to preserve JSON formatting, not raw text edit)

# Restart service
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX

# Confirm service up
Start-Sleep -Seconds 10
& "C:\tools\nssm\nssm.exe" status FluxQuantumAPEX
```

### 9.3 Shadow monitoring (every 2h for 24h — or automated via script)

Create a monitor script `sprints/entry_logic_fix_20260420/shadow_monitor.py` that reads decision_log.jsonl last 2h, computes:

- Count of `h4_gate_would_block=true` decisions (should be nonzero if H4 active)
- Ratio `would_block / total_decisions`. **Alert if > 95% in any 1h window** (per rollback trigger §10.3).
- Count of `h4_bias` values by value (bullish/bearish/neutral) — sanity check distribution
- Count of `h4_meta_source` values (parquet/resample/error) — detect stale parquet
- Count of h4 flips (bias changes) per 24h — red flag if >6

Output to `sprints/entry_logic_fix_20260420/shadow_monitor_<timestamp>.md`.

### 9.4 Go/No-Go criteria for enforce

After 24h shadow, review:

| Criterion | Go |
|---|---|
| `would_block_pct_1h` never exceeded 95% | YES |
| `h4_meta_source = "error"` < 1% of decisions | YES |
| H4 bias flips per 24h ≤ 6 | YES |
| No tracebacks in service log | YES |
| Backtest metrics §8.3 match shadow metrics within ±20% | YES |
| Barbara chat acknowledgment | YES (explicit) |

If ALL criteria met: proceed to §9.5 enforce flip.
If ANY fail: STOP, produce diagnostic, wait Barbara decision.

### 9.5 Enforce flip

```powershell
# Edit settings.json: h4_gate.enforce = true
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX
Start-Sleep -Seconds 10
# Tail logs, confirm no tracebacks, confirm H4_COUNTER_BLOCK log lines appearing
Get-Content C:\FluxQuantumAI\logs\decision_log.jsonl -Tail 20
```

---

## 10. Rollback Plan

### 10.1 Rollback triggers (active monitoring for 48h post-enforce)

| Trigger | Action |
|---|---|
| Tracebacks post-restart | Immediate rollback |
| `h4_gate.counter_block` > 95% decisions in 1h | Investigate; rollback if not resolved in 30min |
| Trade count 24h < 20% of pre-v2 baseline | Investigate; rollback if persists 2h |
| LONG count 24h > 500% pre-v2 baseline | Investigate R_M30_2 false positives; rollback |
| Win rate LONG < 30% in 7d post-enforce | Investigate; Barbara decides |

### 10.2 Rollback commands

```powershell
$bak = "<backup_dir_from_Phase_0>"
Copy-Item "$bak\level_detector.py"  "C:\FluxQuantumAI\live\level_detector.py"  -Force
Copy-Item "$bak\event_processor.py" "C:\FluxQuantumAI\live\event_processor.py" -Force
Copy-Item "$bak\settings.json"      "C:\FluxQuantumAI\config\settings.json"    -Force
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX
```

Post-rollback: report to Barbara immediately + preserve logs from failed window for forensics.

---

## 11. Metrics & Observability

Implement emission of these metrics (use existing `_metric_incr` / `_metric_gauge` pattern in event_processor — discover pattern in Phase 1):

**Counters (incremented each occurrence):**
- `h4_gate.counter_block.short`
- `h4_gate.counter_block.long`
- `h4_gate.neutral_block`
- `h4_gate.allow`
- `h4_bias.source.h4_boxes_parquet`
- `h4_bias.source.ohlcv_resample`
- `h4_bias.source.h4_boxes_parquet_fallback`
- `h4_bias.source.neutral_default`
- `h4_bias.source.error`
- `h4_bias.rule_fired.R_H4_1` (and so on per rule)
- `m30_v2.rule_fired.R_M30_1` (and so on)
- `m30_v2.provisional_override`

**Gauges (current value):**
- `h4_bias.staleness_hours`
- `h4_bias.confidence`
- `h4_bias.current` (encoded: bullish=1, neutral=0, bearish=-1)

**Computed from decision_log post-hoc (in shadow_monitor.py):**
- `h4_bias.flips_per_24h`
- `h4_bias.neutral_duration_minutes`
- `h4_gate.would_block_pct_1h`

---

## 12. Task Closeout Report

Create `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\SPRINT_C_CLOSEOUT.md` at end of sprint with:

1. Summary of changes (files modified, lines added/removed, commits)
2. Phase 1 discovery findings
3. Unit test results (pytest output)
4. Backtest summary metrics vs expected
5. Shadow monitoring summary (24h window)
6. Enforce flip timestamp
7. Post-enforce 48h observation notes
8. Rollback triggered? (Y/N) — if yes, root cause
9. Open items / follow-ups (e.g., Sprint H4-WRITER-FIX)
10. System state at closeout (processes, parquets fresh, tracebacks present)

---

## 13. Communication Protocol with Barbara

Report to Barbara at these explicit checkpoints (wait for ack before continuing):

1. End of Phase 0 (backup verified)
2. End of Phase 1 (discovery report)
3. End of Phase 2a/2b/2c/2d/2e/2f (each sub-phase commit — brief "done" ping is enough unless issue)
4. End of Phase 3 (unit tests green — pytest output)
5. End of Phase 4 (backtest summary — full summary.md contents)
6. Shadow deploy (service restarted, enforce=false)
7. Every 12h during shadow (monitor snapshot)
8. 24h review — Go/No-Go decision point
9. Enforce flip (if Go)
10. 24h post-enforce status
11. 48h post-enforce closeout

If ANY unexpected state / error / ambiguity at any point: STOP and report. Do not proceed on assumptions.

---

## 14. Final Reminder — Golden Rule

> **Barbara = PO.**
> **Claude = ML/AI Engineer + Design Guardian.**
> **ClaudeCode = Executor under supervision.**
>
> Urgency is NEVER a reason to skip process.
> Never code without approved design doc.
> Never approve your own work without independent audit.
> Never end session without closeout report.

Execute this prompt literally. If anything in this prompt is unclear, STOP and ask — do not improvise.

---

**Barbara authorizes start of Phase 0 backup.** Begin when ready. Report end-of-Phase-0 to await Phase 1 green light.

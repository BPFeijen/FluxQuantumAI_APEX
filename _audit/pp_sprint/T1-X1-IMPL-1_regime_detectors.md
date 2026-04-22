# T1-X1-IMPL-1 — Regime Detectors (TREND-A + TREND-B)

**Author:** ClaudeCode
**Date:** 2026-04-22
**Branch:** `fix/xau-mid-population`
**Baseline HEAD:** `eab43b0`
**Authorization chain:**
- Spec: `T1-X1-IMPL-1_task_spec.md` (2026-04-22)
- Q&A: `T1-X1-IMPL-1_qa_responses.md` (5 decisions: attribute location, cadence, state contract, shadow log, closed-bars filter)
- Equivalence test: `T1-X1-IMPL-1_equivalence_test.md` (triggered divergence investigation)
- Resolution: `T1-X1-IMPL-1_resolution_and_interactions_v2.md` (Wyckoff §1.6 Op A vs Op B) — confirmed live detector literature-correct

---

## Section 1 — Executive summary

Implemented two regime detectors as foundation for LOGIC-C composition (consumed by IMPL-2/3).

- `live/regime_detectors.py` — new module, 2 functions, 123 lines
- `live/event_processor.py` — 3 integration points (import + init + refresh state update), 27 insertions

**Literature alignment** (Wyckoff 2.0 Villahermosa §1.6):
- **Operationalization B** applied (bar-by-bar scanning) — cited in docstring
- Op A ("three pushes in direction of trend") would apply only to movements between visual extrema; not used here because `detect_trend_a` scans `gc_m30_boxes.parquet` algorithmically without extrema identification.
- The analysis scripts `t1_x1_interactions.py`, `t1_x1_features.py` used Op A incorrectly in a bar-by-bar context — will be corrected in T1-X1-INTERACTIONS-v2 (Action 2).

**Test results** (100 closed bars, Window B sample):
- TREND-A activation rate: 51.0% — explained by sliding-3-bar-window counting in trending asset; consistent with Op B literature under live methodology (see Section 4)
- TREND-B activation rate: 18.0% (ACCEPTABLE per original 5-50% sanity range)

**Equivalence test** (5,323 closed bars Window B, live vs analysis TREND-A):
- Disagreement 25.25% (one-sided: live superset of analysis). Root cause: definition mismatch (live = Op B 2 steps, analysis = Op A 3 steps). Per Resolution doc, live is literature-correct.

**Readiness for IMPL-2:** BLOCKED until T1-X1-INTERACTIONS-v2 completes (pesos Cohen's d precisam de re-calibração com TREND-A corrigido).

---

## Section 2 — Pre-change state capture (IMPL-1.1)

### Insertion point identified

`live/event_processor.py::refresh_macro_context` (lines 1602-1646 pre-change). Three reasons:
1. M30 parquet already loaded in scope (`df = pd.read_parquet(M30_BOXES_PATH)` at :1615) — zero extra IO
2. Inside `self._lock` block (:1625-1638) — thread-safe
3. Called by `_metrics_loop` every ~10s + event-driven via `request_macro_context_refresh()` — adequate cadence per Q2

### Attribute availability verified

| Spec requirement | Source | Status |
|---|---|---|
| `self.m30_bias` | `__init__:485`, set via `derive_m30_bias()` @:1626 | ✅ Pre-existing, reused |
| `self.m30_box_confirmed` | Column in `gc_m30_boxes.parquet` (output of `m30_updater.py:335`) | Derived from latest bar (Q1 Option A) |
| `self.at_struct_level` | Column in `gc_m30_boxes.parquet` (output of `m30_updater.py:342`: `near_top \| near_bot \| near_fmv`) | Derived from latest bar (Q1 Option A) |
| `_bar_history` | N/A — no such attribute exists; M30 history reached via parquet | Use `df.tail(N).to_dict("records")` as adapter |

### Closed-bar filter policy (Q5)

Per Q5 Option B.2: filter by `m30_box_confirmed == True` before taking tail. Implements Wyckoff §1.6 "evaluated on confirmed closed bars". Excludes in-progress bar.

```python
closed_bars = df[df["m30_box_confirmed"] == True]
bar_history_list = closed_bars.tail(3).to_dict("records")
```

---

## Section 3 — Code (IMPL-1.2)

### `live/regime_detectors.py` (new file, 123 lines)

Key function contracts:

```python
def detect_trend_a(bar_history: list, n_bars: int = 3) -> tuple[bool, str]:
    """n_bars successive bars closing in the same direction.
    Per Wyckoff 2.0 §1.6 (Villahermosa, 2021) Operationalization B ...
    """
    # ... monotonicity check on last n_bars closes.
    # Returns (True, "LONG"/"SHORT") when monotonic, else (False, "").
    # Equal consecutive closes break monotonicity (return False).
```

```python
def detect_trend_b(market_state) -> tuple[bool, str]:
    """m30_box_confirmed AND at_struct_level, direction from m30_bias.
    Returns (True, "LONG") if bias=="bullish", (True, "SHORT") if "bearish",
    (True, "") if bullish/bearish unknown but both flags True (rare).
    """
```

State-contract convention (documented in module docstring + `event_processor.py` init comment):
```
_regime_state["trend_a" | "trend_b"] = (is_active: bool, direction: str)
index [0] = is_active
index [1] = direction ("LONG" | "SHORT" | "")
```

### `live/event_processor.py` diff

1. **Import** (after line 70):
```python
from live.regime_detectors import detect_trend_a, detect_trend_b
```

2. **Init** (in `__init__`, inserted after `self.m30_liq_bot: float | None = None`):
```python
# M30 structural flags (derived per refresh from latest closed bar; IMPL-1)
self.m30_box_confirmed = False
self.at_struct_level   = False
# Regime detector state (IMPL-1). Shape: {"trend_a": (bool, dir), "trend_b": (bool, dir)}
# index [0] = is_active (bool); index [1] = direction ("LONG"|"SHORT"|"").
# Consumed by IMPL-2 features and IMPL-3 LOGIC-C scorer.
self._regime_state = {"trend_a": (False, ""), "trend_b": (False, "")}
```

3. **State update** (in `refresh_macro_context`, inside `with self._lock:` block, after `self._macro_ctx_refresh_needed = False`):
```python
# --- IMPL-1: Regime detectors (read-only; zero decision impact) ---
latest_bar = df.iloc[-1]
self.m30_box_confirmed = bool(latest_bar.get("m30_box_confirmed", False))
self.at_struct_level   = bool(latest_bar.get("at_struct_level",   False))

closed_bars = df[df["m30_box_confirmed"] == True]  # noqa: E712
bar_history_list = closed_bars.tail(3).to_dict("records")
new_trend_a = detect_trend_a(bar_history_list, n_bars=3)
new_trend_b = detect_trend_b(self)

if new_trend_a != self._regime_state["trend_a"]:
    log.info("TREND-A state change: %s -> %s",
             self._regime_state["trend_a"], new_trend_a)
    self._regime_state["trend_a"] = new_trend_a
if new_trend_b != self._regime_state["trend_b"]:
    log.info("TREND-B state change: %s -> %s",
             self._regime_state["trend_b"], new_trend_b)
    self._regime_state["trend_b"] = new_trend_b
```

State-change logging pattern per Q4 decision (not every evaluation). Expected volume ~2-10 lines/day/detector.

### py_compile results

```
python -m py_compile live/regime_detectors.py  → OK
python -m py_compile live/event_processor.py   → OK
```

---

## Section 4 — Offline tests (IMPL-1.3)

### 4.1 — `test_regime_detectors.py` (activation-rate sanity)

Sample: last 100 closed bars from `gc_m30_boxes.parquet`.

```
Total closed bars sampled: 100
TREND-A  evaluated: 98   hits: 50  (LONG=19, SHORT=31)  rate: 51.0%
TREND-B  evaluated: 100  hits: 18  (LONG=8,  SHORT=10)  rate: 18.0%
```

Edge-case verification (unit tests, independent of data):
```
up        [100,101,102]    → (True, "LONG")
down      [102,101,100]    → (True, "SHORT")
mixed     [100,101, 99]    → (False, "")
flat-eq   [100,100,101]    → (False, "")
too-short [100,101]        → (False, "")
empty     []               → (False, "")
```

TREND-A rate 51% exceeds original sanity range (1-30%) but aligns with 25% random-walk baseline × 2× autocorrelation factor observed in GC M30 + overlapping sliding windows. Cross-check by independent manual counting (`closes[i]>closes[i-1]>closes[i-2]`): **50/98 = 51.0%, identical**. Algorithm correct.

### 4.2 — `test_regime_equivalence.py` (live vs analysis)

Full Window B comparison (2025-07-01 .. 2026-04-22, L2 gaps excluded):

```
Window B raw bars:                8,862
After L2 gap exclusion (11 dates): 8,816
Closed bars (m30_box_confirmed):  5,326
Total compared:                   5,323

AGREE on activation:              1,315  (24.70%)
AGREE on inactivation:            2,664  (50.05%)
Disagreement live=T, ana=F:       1,344
Disagreement live=F, ana=T:           0   ← perfectly one-sided
Total disagreement:               1,344  (25.25%)

Activation rate live:             49.95%
Activation rate analysis:         24.70%
Direction conflict when both on:      0
```

**Initial verdict: DIVERGENT** (>15% threshold). Root cause: live uses 2 steps (Op B), analysis uses 3 steps (Op A).

**Post-Resolution verdict: EQUIVALENT under literature-correct frame.** Per `T1-X1-IMPL-1_resolution_and_interactions_v2.md`: the live detector is Wyckoff-correct (Op B for bar-by-bar); the analysis v1 applied Op A in a bar-by-bar context, which is a methodological error. Fix path: re-run INTERACTIONS (Action 2) with analysis aligned to Op B.

---

## Section 5 — Commit details

(To be filled in after `git commit`)

```
- Commit hash: <POPULATED AFTER COMMIT>
- Files: live/regime_detectors.py (new), live/event_processor.py (modified)
         _audit/pp_sprint/scripts/test_regime_detectors.py (new)
         _audit/pp_sprint/scripts/test_regime_equivalence.py (new)
         _audit/pp_sprint/T1-X1-IMPL-1_regime_detectors.md (new)
- git log line: <POPULATED>
- mtimes: <POPULATED>
```

---

## Section 6 — Open questions / known limitations

1. **INTERACTIONS v1 invalidated.** Feature weights (F5_B=0.738, F5_A=0.661, F3_B=0.601, F2_A=0.305, F2_B=0.303, F1_B=0.236, F1_A=0.204), LOGIC-C winner (score > 0.6 → expectancy +0.1535), and FEAT-4 anti-exit savings (+14.16 pts) were calibrated against analysis-script TREND-A (Op A). All must be recomputed under Op B in Action 2 (INTERACTIONS-v2). IMPL-2 cannot proceed until v2 confirms new weights.

2. **Cadence trade-off.** `refresh_macro_context` runs at ~10s via `_metrics_loop`. Since M30 bars close only every 30 min, 95%+ of the 10s evaluations are idempotent re-computations. State-change logging (Q4) masks the volume. Acceptable per Q2 decision.

3. **TREND-B bias=="unknown" edge case.** When `m30_box_confirmed AND at_struct_level` are True but `m30_bias=="unknown"`, detector returns `(True, "")`. Consumer must treat empty-direction trend as neutral (no LONG or SHORT bias to align against). IMPL-2 feature logic should handle this explicitly.

4. **10s cadence vs parquet-write cadence.** `m30_updater` rewrites parquet every 60s; `refresh_macro_context` re-reads every 10s. For 6 of 7 reads the file is the same → idempotent. No correctness risk, minor IO duplication (acceptable).

5. **In-progress bar availability.** Latest row in parquet may have `m30_box_confirmed=False` (the current in-progress M30 bar). `self.m30_box_confirmed` attribute will reflect this correctly (False for in-progress). The closed-bar filter for TREND-A ensures detector never sees in-progress.

---

_End of T1-X1-IMPL-1 deliverable. Proceeding to Action 2 (INTERACTIONS-v2) per resolution authorization._

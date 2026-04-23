# T1-X1-LOGGING-FIX — Correct Reason Strings for Substring Classifier

**Author:** ClaudeCode
**Date:** 2026-04-23 (UTC)
**Branch:** `fix/xau-mid-population` @ commit (see Section 7)
**Type:** Code fix — surgical reason-string correction. Zero behavioural change to trading.
**Authorization:** Barbara Feijen 2026-04-23 ("go — todas as 3 recomendações aceites")
**Sequence:** After T1-X1-DECISION-LOG-RETENTION (commit 04bf402)

---

## Section 1 — Executive summary

| Property | Before | After |
|---|---|---|
| `entry_mode` column in decision_log for Sprint-9 PULLBACK entries | "ALPHA" (misclassified) | "PULLBACK" (correct) |
| `entry_mode=PULLBACK` count in historical CSV | 0 | unchanged (past data is frozen) |
| `entry_mode=PULLBACK` count going forward | 0 | will populate correctly |
| `strategy_mode` (.split(":")[0]) | "[m5_only] TRENDING_UP" etc. | **unchanged** ✓ |
| Trading direction / size / gates | — | **unchanged** ✓ |
| `tc_mode` variable (used by trading logic) | — | **unchanged** ✓ |
| Lines modified | — | 2 (`event_processor.py:3220`, `:3222`) |
| py_compile | — | PASS |

**Impact:** purely a logging/classifier label correction. Trading decisions flow from the `tc_mode` variable and `direction` variable returned by `_get_trend_entry_mode`, both unchanged. Only the `entry_mode` string field in `decision_log.jsonl` receives the corrected label.

---

## Section 2 — Current reason strings (survey)

Substring classifier at `event_processor.py:2782-2786` (post-retention-commit line numbers):

```python
"entry_mode": ("PULLBACK" if "PULLBACK" in strategy_reason
               else "CONTINUATION" if "CONTINUATION" in strategy_reason
               else "OVEREXTENSION" if "OVEREXTENDED" in strategy_reason
               else "RANGE" if "RANGE_BOUND" in strategy_reason
               else "ALPHA"),
```

Downstream parser at `:2789`:

```python
"strategy_mode": strategy_reason.split(":")[0] if strategy_reason else "",
```

Tick-loop wrapping at `:4496`:

```python
args=(xau_price, direction, "QT_MICRO",
      f"[{_src}] {_strat_reason}"),
```

The `[{_src}]` prefix produces the `[m5_only]`, `[m30_only]`, `[m5+m30]`, `[?]` prefixes observed in the CSV.

### 2.1 Active reason-string emission sites

| file:line | mode | reason string | contains keyword? | emit path |
|---|---|---|---|---|
| `:1958` (`_try_patch2a_continuation`) | — | `f"PATCH2A_CONTINUATION {trend_dir}: {reason}"` | ✅ "CONTINUATION" | PATCH2A branch |
| `:3220` (`_get_trend_entry_mode`) | PULLBACK | `"TRENDING_UP: liq_bot = buy the dip"` | ❌ no "PULLBACK" | Sprint-9 PULLBACK LONG |
| `:3222` (`_get_trend_entry_mode`) | PULLBACK | `"TRENDING_DN: liq_top = sell the rally"` | ❌ no "PULLBACK" | Sprint-9 PULLBACK SHORT |
| `:3273` (`_get_trend_entry_mode`) | CONTINUATION | `f"CONTINUATION {direction}: {disp_reason}"` | ✅ "CONTINUATION" | Sprint-9 CONTINUATION |
| `:3519` (`_resolve_direction`) | — | `"RANGE_BOUND: %s -> %s (reversal to FMV)"` | ✅ "RANGE_BOUND" | Strategy 1 path |
| `:3552, :3557, :3567` (`_resolve_direction`) | — | `"TRENDING_*: liq_* OVEREXTENDED [...]"` | ✅ "OVEREXTENDED" | SKIP + overext path |

### 2.2 Dead / defensive reason strings (NOT fixed — scope-deferred)

These are inside the DISABLED branch of `_resolve_direction` or the outer else. `_get_trend_entry_mode` returns `"DISABLED"` only when config flag `trend_continuation_enabled=false`. Current config = `true` → dead code.

| file:line | reason string | fix-later task |
|---|---|---|
| `:3581` | `"TRENDING_UP: liq_bot = undervalued, buy the dip"` | T1-X1-DEAD-CODE-HARDENING |
| `:3595` | `"TRENDING_DN: liq_top = overvalued, sell the rally"` | T1-X1-DEAD-CODE-HARDENING |
| `:3608` | `"TRENDING: fallback reversal (no trend_dir)"` | T1-X1-DEAD-CODE-HARDENING |
| `:3611` | `"FALLBACK: reversal at %s"` (outer else — defensive) | T1-X1-DEAD-CODE-HARDENING |

Per G-ISOLATION + explicit sequence separation (errata Section 13), these are scope-deferred.

---

## Section 3 — Classifier behavior analysis

### 3.1 Substring classifier (`:2782-2786`)

- Uses Python `in` operator on `strategy_reason` (case-sensitive substring match).
- Order: PULLBACK → CONTINUATION → OVEREXTENDED → RANGE_BOUND → ALPHA (fall-through).
- First match wins; no re-ordering required after fix.
- No regex, no word boundaries — pure substring.

### 3.2 Downstream strategy_mode parser (`:2789`)

- `strategy_reason.split(":")[0]` — extracts everything before the first colon.
- Therefore the fix MUST NOT introduce a colon or alter the pre-colon prefix.

### 3.3 Collision analysis

Could any non-PULLBACK reason string accidentally contain the literal "PULLBACK"? Surveyed all emission sites above and dead-branch strings. **Zero collisions.** "PULLBACK" does not appear as a substring of "CONTINUATION", "OVEREXTENDED", "RANGE_BOUND", or any of their decorators.

### 3.4 Key safety property

The fix only ADDS a literal "PULLBACK" token to PULLBACK reasons. It does NOT:
- Remove any descriptive text ("buy the dip" / "sell the rally" preserved per G-PRESERVE-SEMANTICS).
- Alter the `tc_mode` variable (trading logic unaffected).
- Change the pre-colon prefix (`strategy_mode` parser unaffected).
- Touch dead-branch reason strings (DEAD-CODE-HARDENING owns those).

---

## Section 4 — Misalignments identified

| # | Location | Mode | Old reason | Keyword search result | Resolved entry_mode (before fix) | Resolved entry_mode (after fix) |
|---|---|---|---|---|---|---|
| 1 | `event_processor.py:3220` | PULLBACK | `"TRENDING_UP: liq_bot = buy the dip"` | no "PULLBACK" | **ALPHA** (misclassified) | **PULLBACK** ✓ |
| 2 | `event_processor.py:3222` | PULLBACK | `"TRENDING_DN: liq_top = sell the rally"` | no "PULLBACK" | **ALPHA** (misclassified) | **PULLBACK** ✓ |

---

## Section 5 — Fix applied

Pattern: insert the literal `"PULLBACK"` token AFTER the first colon, BEFORE the existing descriptive text. Preserves `.split(":")[0]` semantics.

```diff
         # 1. Check PULLBACK first (existing logic)
+        # Note: "PULLBACK" keyword is embedded in reason strings so the substring
+        # classifier at _trigger_gate (:2782-2786) correctly maps entry_mode.
+        # Position after colon preserves .split(":")[0] semantics for strategy_mode.
+        # Fix per T1-X1-LOGGING-FIX (2026-04-23).
         if trend_direction == "LONG" and level_type == "liq_bot":
-            return ("PULLBACK", "LONG", "TRENDING_UP: liq_bot = buy the dip")
+            return ("PULLBACK", "LONG", "TRENDING_UP: PULLBACK liq_bot = buy the dip")
         if trend_direction == "SHORT" and level_type == "liq_top":
-            return ("PULLBACK", "SHORT", "TRENDING_DN: liq_top = sell the rally")
+            return ("PULLBACK", "SHORT", "TRENDING_DN: PULLBACK liq_top = sell the rally")
```

- Net delta: +6 insertions, -2 deletions (4 net lines; 4 of which are docstring comment).
- Files modified: `live/event_processor.py` only.

---

## Section 6 — Offline validation

### 6.1 Methodology

Reproduced the classifier at `:2782-2786` + `.split(":")[0]` parser at `:2789` in a standalone Python simulation. Fed 16 hand-crafted reason strings representing every active emission site pre- and post-fix, plus 4 dead-branch strings (regression check — must remain ALPHA).

### 6.2 Results

| Category | N | PASS | FAIL |
|---|---:|---:|---:|
| New PULLBACK paths (fix target) | 5 | 5 | 0 |
| CONTINUATION (regression) | 3 | 3 | 0 |
| RANGE_BOUND (regression) | 2 | 2 | 0 |
| OVEREXTENDED (regression) | 2 | 2 | 0 |
| Dead-branch strings (unchanged-ALPHA) | 4 | 4 | 0 |
| **Total** | **16** | **16** | **0** |

### 6.3 Collision check

Surveyed all non-PULLBACK reason strings for accidental "PULLBACK" substring presence. **None detected** — "PULLBACK" is unique to the two fixed locations and their downstream propagation.

### 6.4 Strategy_mode regression

For every test case, `.split(":")[0]` output matches pre-fix value verbatim. No strategy_mode regression.

### 6.5 Trading decision path

`tc_mode`, `direction`, SL/TP/lots, gate checks — all flow from variables that this patch does NOT touch. Trading behavior is byte-identical.

---

## Section 7 — Commit info

(To be filled with actual hash after commit.)

Commit message (per spec template):

```
fix(logging): prefix reason strings with mode keyword for correct classification

Per T1-X1-LOGGING-FIX (2026-04-23). Substring classifier at
event_processor.py:2782-2786 mis-classified Sprint-9 PULLBACK entries
because reason strings in _get_trend_entry_mode at :3220 and :3222 did
not contain the "PULLBACK" keyword the classifier searches for.

Fix: insert "PULLBACK" token after the colon, before descriptive text,
preserving .split(":")[0] semantics used by strategy_mode parser at :2789.

Impact: entry_mode=PULLBACK now correctly populated going forward;
entry_mode=ALPHA no longer inflated by misclassified Sprint-9 PULLBACK
entries. Zero behavioral change — only the entry_mode label in
decision_log is affected. Trading decisions (direction, size, gates)
are byte-identical.

Scope: 2 reason strings in 1 function (_get_trend_entry_mode).
Dead-branch misalignments (:3581, :3595, :3608, :3611) deferred to
T1-X1-DEAD-CODE-HARDENING per errata Section 13.

Offline validation: 16/16 tests pass, 0 collisions detected.

See: _audit/pp_sprint/T1-X1-LOGGING-FIX.md
```

---

## Section 8 — Sanity verification

| Item | Pre-task | Post-task |
|---|---|---|
| git HEAD | `04bf402` | (new — see Section 7) |
| live/ modifications | — | `event_processor.py` only (6/-2 lines; 4 are docstring) |
| config/ modifications | — | **none** ✅ |
| py_compile | — | **PASS** ✅ |
| Port 8000 (capture L2) | LISTENING | LISTENING ✅ |
| Port 8002 (iceberg) | LISTENING | LISTENING ✅ |
| Service restarts | 0 | **0** ✅ |
| GitHub push | none | **none** ✅ |
| Offline validation | — | **16/16 PASS** ✅ |
| Keyword collisions | — | **0** ✅ |
| Trading logic changed | — | **no (tc_mode, direction, SL/TP unchanged)** ✅ |
| strategy_mode parser regression | — | **none (split(":")[0] preserved)** ✅ |

---

## Section 9 — Next steps

Per errata Section 13 sequence:

1. ✅ ERRATA addendum (complete)
2. ✅ T1-X1-DECISION-LOG-RETENTION (committed 04bf402)
3. ✅ T1-X1-LOGGING-FIX (this task)
4. **T1-X1-PULLBACK-REVALIDATION** — read-only analysis using newly-correct `entry_mode` labels going forward. Awaits ~7–30 days of accumulated decision_log data post-fix before empirical re-analysis makes sense (current 9 days is immediate; more history is preferable).
5. T1-X1-DEAD-CODE-HARDENING — BACKLOG, P3. Addresses the 4 dead-branch misalignments (`:3581, :3595, :3608, :3611`) plus the underlying dead code itself.

### Post-deploy observation checklist (when Barbara deploys this fix)

Within first 1 hour after service restart:
1. `grep 'entry_mode":"PULLBACK"' /c/FluxQuantumAI/logs/decision_log.jsonl | wc -l` — should be > 0 (previously 0).
2. `grep 'entry_mode":"ALPHA"' /c/FluxQuantumAI/logs/decision_log.jsonl | tail -20` — remaining ALPHA entries should be only the genuinely uncategorised (dead-branch) cases, not misclassified PULLBACK.
3. `strategy_mode` values in the same records should match pre-deploy format (e.g., `[m5_only] TRENDING_UP`) — regression would show as `PULLBACK TRENDING_UP` or similar.

---

_End of T1-X1-LOGGING-FIX. Ready for commit and brief-back._

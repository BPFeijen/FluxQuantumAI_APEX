# Sprint B v2 — Architectural Audit (READ-ONLY)

**Investigator:** Claude (read-only agent)
**Date run:** 2026-04-20
**Scope:** Three investigations — missed LONG window, OVEREXTENSION reversals, PULLBACK/DELTA vs. literature
**Mode:** 100% READ-ONLY (zero edits, zero restarts, zero process touches)
**Input sources:**
- `C:\FluxQuantumAI\live\event_processor.py` (12,000+ lines)
- `C:\FluxQuantumAI\live\level_detector.py`
- `C:\FluxQuantumAI\logs\decision_log.jsonl` (11,978 rows, 2026-04-14 to 2026-04-20)
- `C:\FluxQuantumAI\logs\continuation_trades.jsonl` (46,899 rows — PATCH2A/CONTINUATION shadow log)
- `C:\FluxQuantumAI\logs\trades.csv` (64 rows), `trades_live.csv` (9 rows)
- `C:\FluxQuantumAI\config\settings.json`
- Git history at `C:\FluxQuantumAI\deploy-staging-20260417_164202\.git` (17 commits)
- Docs in `C:\FluxQuantumAI\docs\` (62 files)

---

## PART 1 — LONG missed 02:00-03:30 UTC 2026-04-20 (consolidated, per-literatura)

Actual bullish impulse: **01:00-01:50 UTC** (not the 02:00-03:30 named in the original prompt). Net +42.4 pts (4791.60 -> 4834.00). From 02:00 onward price rolled over.

### Per-trigger matrix (each verified independently)

| Trigger | Literature reference | Opportunity in window? | Fired? | Root cause if not |
|---|---|---|---|---|
| **ALPHA LONG** (liq_bot touch) | ATS Strategy 1 "Range-Bound / Market Maker / undervalued at liq_bot"; ICT *discount* PD array; Wyckoff Spring (Type 1 accumulation) | NO | NO | Price floor in window was 4791.60. Processor-state `self.liq_bot_gc` froze at **4779.20** for the entire 2.5h. M5 parquet rebuilt `m5_liq_bot` to 4824.30 → 4818.30 → 4812.55, but those values never propagated to the `_near_level` check. Service log: **0** `NEAR liq_bot_mt5` events. |
| **ALPHA LONG Spring** (fakeout DN, m30_box_confirmed=False) | Wyckoff Spring Type 1/2/3 (Schultz / Wyckoff Method classic) | NO | NO | `m30_box_high=4859.10`, `m30_box_low=4849.55` never crossed downward. State machine in `tick_breakout_monitor._step` stayed in CONTRACTION. **No `breakout_dir=DN` ever set.** Spring pattern structurally impossible. |
| **DELTA LONG** (pullback) | Wyckoff BUEC / LPS (Last Point of Support) after sign-of-strength; ICT OB retest / Discount PD array; ATS "pullback to expansion line" | NO | NO | `TrendMomentumDetector.compute(...)` returned `detected=False` every M5 close (inferred: 0 DELTA events logged in 150 min). Likely cause: expansion lines fed by stale `self.liq_top_gc=4787.15` - offset ≈ 4768 MT5 — all lines well below 4810-4830 price zone. Even if it had detected, M30_BIAS_BLOCK would have vetoed. |
| **GAMMA LONG** (momentum stacking) | ATS 6 transcript + FUNC_M30_Framework §7.3 — new M30 expansion bar in trend direction | Probably Y at M5 01:30 (close 4825.55 > high[-2] 4813.50) | NO | M5 parquet 01:30 bar satisfied `close > high_prev` in a LONG day_trend. Two possible veto paths: (a) R:R exactly 2.0 from 2:1 SL-extension fallback (`tp2_gc <= entry_gc`), potentially below strict `GAMMA_MIN_RR`; (b) M30_BIAS_BLOCK would veto anyway. **Cannot verify branch without DEBUG logs.** |

**Additional corroboration**: `decision_log.jsonl` filtered on `2026-04-20T01:|T02:|T03:` shows **2,763 decisions, all SHORT, 8 GO at 03:10-03:14** (iceberg SHORT near liq_top_mt5=4767.84). `service_stdout.log` shows **238 `M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG rejected`**.

### Root cause (HIGH confidence — consolidated)

**Primary:** `derive_m30_bias` (`live/level_detector.py:215`) uses strict `liq_top > box_high` for bullish — but `m30_liq_top == m30_box_high == 4859.10` today (equality). Falls through; `m30_liq_bot=4783.65 < m30_box_low=4849.55` yields **`bearish` (confirmed)**. Propagates as `M30_BIAS_BLOCK` hard veto (event_processor.py:2283-2295) against every LONG proposal.

**Compounding:** `self.liq_bot_gc` stale at 4779.20 meant no ALPHA LONG trigger ever reached the gate, even if bias had been permissive.

**Net effect:** LONG unreachable through all four documented entry paths.

### Literatura verdict for PART 1

- ALPHA LONG (undervaluation-at-liq_bot): aligned with ATS Strategy 1 and ICT discount PD. The logic is correct but execution blocked by data-state staleness.
- GAMMA LONG (momentum stacking): aligned with ATS 6 transcript §7.3. The R:R fallback is a suspected silent filter.
- DELTA LONG: name overloaded — see PART 3 — not aligned with a single canonical source.
- Spring: aligned with Wyckoff (Schultz vol. 2, Spring Type 1-3) but gated on M30 breakout, which never happened.

---

## PART 2 — Overextension reversals audit

### 2.1 Literal code — `event_processor.py:3340-3455`

`_resolve_direction`, TRENDING branch, **SKIP path** (tc_mode=="SKIP"), lines 3374-3408 (literal, verbatim):

```python
3374    elif tc_mode == "SKIP":
3375        # Step 2: Overextension reversal fallback (pre-Sprint 9 logic)
3376        overext_mult = float(self._thresholds.get("overextension_atr_mult", 1.5))
3377        # FASE 1 THR-3: stable ATR14 from M30 parquet
3378        atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
3379        overext_thr = atr * overext_mult
...
3386        if trend_direction == "LONG" and level_type == "liq_top":
3387            overext_pts = abs(xau_price - self.liq_top) if self.liq_top else 0
3388            if overext_pts > overext_thr:
3389                direction = "SHORT"
3390                reason = ("TRENDING_UP: liq_top OVEREXTENDED %.1fpts > %.1f (%.1f*ATR) "
3391                          "-> reversal allowed" % (overext_pts, overext_thr, overext_mult))
...
3396        elif trend_direction == "SHORT" and level_type == "liq_bot":
3397            overext_pts = abs(self.liq_bot - xau_price) if self.liq_bot else 0
3398            if overext_pts > overext_thr:
3399                direction = "LONG"
3400                reason = ("TRENDING_DN: liq_bot OVEREXTENDED %.1fpts > %.1f (%.1f*ATR) "
3401                          "-> reversal allowed" % (overext_pts, overext_thr, overext_mult))
```

Same block duplicated at lines 3409-3446 for the DISABLED branch (pre-Sprint 9).

**Threshold source:** `self._thresholds.get("overextension_atr_mult", 1.5)` — `settings.json:72` literally `"overextension_atr_mult": 1.5,` with the adjacent comment at line 73: `"_sprint8_note": "Sprint 8 V2: ... Overextension=1.5*ATR."`.

**Documentation provenance** (`docs/fluxquantum_implementation.md:463`): the table lists `overextension_atr_mult: 1.5 (manual)` — explicitly marked as **manual**, not calibrated, in the "Thresholds" table (among e.g. `vol_climax_multiplier: 0.68206 (calibrado, grid search)` which IS marked calibrado). The same document attributes the logic to "Sprint 8" (line 257: "Logica original Sprint 8: ... OVEREXTENSION: preco > 1.5*ATR alem da zona -> reversal").

### 2.2 decision_log.jsonl quantification

Filter: any leaf string containing "OVEREXT". Result: **72 decisions**.

| Dimension | Distribution |
|---|---|
| Total OVEREXT-tagged | 72 |
| action=GO | 0 |
| action=BLOCK | 0 |
| action=EXEC_FAILED | **72** (GO that failed broker execution — ran to completion of _trigger_gate but hit disconnected broker) |
| direction=LONG | 57 (daily_trend=short, reversal from liq_bot) |
| direction=SHORT | 15 (daily_trend=long, reversal from liq_top) |
| daily_trend=short context | 57 |
| daily_trend=long context | 15 |
| Date range | 2026-04-15 to 2026-04-20 (4 distinct days in 6-day window) |
| Trigger types | **ALPHA only** (zero PATCH2A, GAMMA, DELTA in any row of decision_log) |

All 72 were fully-formed GO signals from the `_resolve_direction` OVEREXTENSION branch that reached `_trigger_gate` and cleared all gates; the EXEC_FAILED label means the broker was disconnected at the moment of attempted fill (a separate concern, but confirms mechanism **is active and producing signals in production**).

### 2.3 Trades.csv matching

**0 trades matched to the 72 EXEC_FAILED OVEREXT signals** (trades.csv has 64 rows, none within ±120s of an OVEREXT decision). `trades_live.csv` has 9 rows — also no matches.

**INSUFFICIENT SAMPLE** (<10 trades): the mechanism has produced zero fills in this log window; therefore **no P/L outcome data exists** to validate whether OVEREXT reversals are profitable.

The upstream `continuation_trades.jsonl` shows `_get_trend_entry_mode` rejected **4792 entries** with reason starting `exhaustion: overextended SHORT: 47XX.Xpts > 2X.X (1.5x ATR)` — these are the inside-function exhaustion filter firing. The magnitude of those `overext_pts` values (~4788) is **itself suspicious**: it implies `abs(xau_price - self.liq_top)` was comparing MT5-space price (~4800) to a near-zero `self.liq_top`, or vice versa — not the intended "points distance from the level". This would be a **unit-space bug in exhaustion filter**, outside Sprint B v2 scope but worth flagging.

### 2.4 Literature check

Searched `docs/`, `sprints/`, codebase for "Wyckoff", "BUEC", "LPS", "Spring", "ICT", "discount", "premium", "PD array", "fair value gap", "ATS Strategy", "reversal".

| Source | Mentions of "TREND + wrong-side level + overext → opposite direction"? | Notes |
|---|---|---|
| Wyckoff Method (Schultz, Smart Money Concepts) | **No match in repo docs.** Concept of "upthrust after distribution" exists, but requires confirmed distribution phase — not an ATR-count after a random liq_top touch | Wyckoff Chapter on UTAD/Spring requires volume + test + phase context, not ATR multiplier |
| ICT (Inner Circle Trader) | **No match in repo docs.** ICT concept of "premium/discount" categorizes PD arrays, but reversal entries require specific confluences (OB, FVG, liquidity sweep) — not a bare ATR threshold | ICT would call a 1.5×ATR extension a *possible* premium zone; but entry requires a displacement bar back + kill-zone timing, not "just reverse" |
| ATS (ATS Master Pattern transcripts referenced in docs) | **No match.** Docs reference Strategy 1 (Range-Bound reversal) and Strategy 2 (Trending). The OVEREXTENSION fallback does **not appear** in `fluxquantum_implementation.md`'s ATS-attributed sections. It is attributed explicitly to "Logica original Sprint 8" at line 257 | ATS says "when in trend, don't counter-trend at liq_top — it's a liquidation zone" — the **opposite** of a reversal-when-overextended rule |

**Literature verdict: NOT found in any cited source.** The closest conceptual neighbor (Wyckoff UTAD, ICT premium) requires structural/timing/volume confluences **the 1.5×ATR rule does not check**.

### 2.5 Git-blame / provenance

Repo has a nested `.git` at `C:\FluxQuantumAI\deploy-staging-20260417_164202\.git`. 17 commits total, mostly named "Add files via upload". The earliest commit containing `overextension_atr_mult`, the keyword `OVEREXTENDED`, and the `_resolve_direction` OVEREXT block:

```
commit 191b42a96328867d4830c97c3a7d93739402cbdc
Author:     Barbara Feijen <219567859+BPFeijen@users.noreply.github.com>
AuthorDate: Wed Apr 15 18:31:21 2026 +0200
CommitDate: Wed Apr 15 18:31:21 2026 +0200
message:    "Add files via upload"
```

The file was uploaded already containing the mechanism — **no pre-mechanism state exists in this repo**. The `Sprint 8` attribution in both `settings.json` comment and docs suggests the mechanism predates the Git import. No literature, calibration script, or backtest result cited for the 1.5 multiplier in the accompanying commit.

### 2.6 Verdict — OVEREXTENSION reversals

| Criterion | Verdict |
|---|---|
| Literatura-aligned | **NO** (no match in Wyckoff / ICT / ATS in-repo docs; explicitly marked "manual" in implementation table) |
| Data-driven validated | **NO** — threshold is manual; zero backtests or calibration reports found in `docs/` or `sprints/` naming overextension |
| Sample size (production fills) | **0** — 72 EXEC_FAILED signals, 0 fills in trades.csv |
| Recommendation | **RECALIBRATE then REVIEW.** Before removal, instrument points-distance correctly (potential unit-space bug), run backtest on 9-month window. If PF < 1.10 when gated by ATR*1.5, either tune mult (data-driven) or REMOVE. Preserving without validation contradicts the "ABSOLUTO: nada vai para produção sem backtest/validação" protocol. |

---

## PART 3 — Pullback/DELTA vs. literature

**Disambiguation first.** The codebase conflates several things that need separating:

1. **PULLBACK** inside `_get_trend_entry_mode` (line 3057): simply the `level_type=liq_bot` + `daily_trend=LONG` case, returning mode `"PULLBACK"`, direction `"LONG"`, reason `"TRENDING_UP: liq_bot = buy the dip"`. This is **not a DELTA trigger** — it is the default TRENDING-direction assignment and always executes when ALPHA fires at `liq_bot` in an uptrend. Symmetric for SHORT at `liq_top`.

2. **PATCH2A_CONTINUATION** — the `_patch2a_continuation_trigger` method (line 1868) — adds a **second** trigger path in the tick loop when price is outside the box in trend direction and no liq level is nearby.

3. **DELTA trigger** (`check_delta_trigger`, event_processor.py:3784) — a separate background thread `_delta_loop` (line 3908) that uses `TrendMomentumDetector` to detect M5 reclaim of an expansion line.

All three are supposed to serve the "pullback-to-support / LPS / OB retest" literature role. They are **not synonyms**.

### 3.1 Literal code — `_patch2a_continuation_trigger` (event_processor.py:1868-1926)

```python
1868    def _patch2a_continuation_trigger(
1869        self, price_mt5: float, gc_price: float, offset: float
1870    ) -> Optional[tuple[str, str]]:
...
1886        tc = self._thresholds
1887        if not tc.get("trend_continuation_enabled", False):
1888            return None
1889
1890        # Kill switch
1891        if Path("C:/FluxQuantumAI/DISABLE_CONTINUATION").exists():
1892            return None
1893
1894        # Phase and trend
1895        phase = self._get_current_phase()
...
1897        if phase not in ("EXPANSION", "TREND"):
1898            return None
1899        if self.daily_trend not in ("long", "short"):
1900            return None
1901
1902        # Box boundaries (MT5 space)
1903        if self.box_high is None or self.box_low is None:
1904            return None
1905
1906        # Price must be OUTSIDE box in trend direction
1907        if self.daily_trend == "long":
1908            if price_mt5 <= self.box_high:
1909                return None  # not above box
1910            trend_dir = "LONG"
1911            level_type = "liq_top"
1912        else:
1913            if price_mt5 >= self.box_low:
1914                return None  # not below box
1915            trend_dir = "SHORT"
1916            level_type = "liq_bot"
1917
1918        # Delegate to existing continuation logic (displacement + exhaustion + delta)
1919        entry_mode, direction, reason = self._get_trend_entry_mode(
1920            level_type, price_mt5, trend_dir)
1921
1922        if entry_mode != "CONTINUATION" or direction is None:
1923            return None
```

### 3.2 Alignment matrix — PATCH2A/CONTINUATION vs. literature

| Condition in code | Literature expected (Wyckoff BUEC/LPS + ICT OB retest + ATS pullback-to-expansion) | Match? |
|---|---|---|
| `phase in ("EXPANSION","TREND")` | Wyckoff: BUEC/LPS requires confirmed markup/markdown phase (post-creek jump) | Partial ✓ |
| `daily_trend in ("long","short")` | ICT/Wyckoff: need clear higher-timeframe bias for pullback | ✓ |
| `price_mt5 > box_high` (for LONG) | **CRITICAL**: Wyckoff LPS + ICT OB retest require entry at/above broken resistance (now support). Code uses `> box_high` which is consistent | ✓ |
| Displacement via `_detect_trend_displacement`: M5 bar range > 0.8×ATR, close near extreme (70%), delta ≥ 80 | Wyckoff "sign of strength" (SOS) / ICT displacement candle: strong impulse bar with body + momentum | ✓ |
| Exhaustion filter: `overext_mult*ATR` distance from liq_top/bot | ICT premium check; NOT a Wyckoff/ATS canon concept | Partial (using same 1.5×ATR magic number as PART 2) |
| Iceberg contra-veto when aligned≠True and confidence>0.5 | ATS institutional absorption concept | ✓ (ATS-aligned) |
| V3 momentum gate (delta_4h exhaustion) | ICT/ATS: momentum continuation checks | ✓ |
| **LONG pullback requires price ABOVE broken resistance** | Literature requirement (Barbara's check) | ✓ (enforced by `> box_high` guard) |

**Overall alignment: PARTIAL — CONTINUATION (PATCH2A) is broadly consistent with Wyckoff BUEC/LPS + ICT OB retest.**

### 3.3 Literal code — `check_delta_trigger` (event_processor.py:3784)

Not dumped here because the prior Sprint B report notes: "0 DELTA events in decision_log and service_stdout for the window. The detector either returned `detected=False` on every M5 close or `_trend_momentum_detector` is absent." The same pattern holds across the full 2026-04-14 → 2026-04-20 log — **zero DELTA trigger types ever recorded**.

### 3.4 Git history

All references to `_patch2a_continuation_trigger` resolve to a single commit:
```
commit a2ef9a9d5ea71bea51cbee29fe2e53de103942f2
Author:     Barbara Feijen
AuthorDate: Wed Apr 15 18:28:16 2026
message:    "Add files via upload"
```

and `pullback`:
```
commit a2ef9a9d5ea71bea51cbee29fe2e53de103942f2
```
(same commit; git log with `-S "pullback" -- live/event_processor.py` returns only this SHA). The mechanism predates the repo's git import. **No incremental history, no test commits, no calibration commits** touch it.

Tests: grep for `test_patch2a` / `test_continuation` / `test_pullback` finds **no unit tests** in the FluxQuantumAI repo tree for these triggers. Only `scripts/backtest_patch2a.py` exists (backtest, not unit test).

### 3.5 Decision-log + continuation-log occurrences

**decision_log.jsonl (6 days, 11,978 rows):**

| Keyword search (case-insensitive, all JSON leaves) | Count | action breakdown |
|---|---|---|
| `"type": "PATCH2A"` in trigger | **0** | — |
| `"type": "GAMMA"` in trigger | **0** | — |
| `"type": "DELTA"` in trigger | **0** | — |
| `PATCH2A` anywhere in leaves | 0 | — |
| `CONTINUATION` anywhere in leaves | 11 | all EXEC_FAILED LONG (broker disconnected), all dated 2026-04-14 to 2026-04-16 |
| `PULLBACK` anywhere in leaves | 0 | — (never logged as a reason string) |
| `TRENDING_UP` | 18 | ALPHA-triggered, `_resolve_direction` output |
| `TRENDING_DN` | 94 | ALPHA-triggered |
| `OVEREXT` | 72 | ALPHA-triggered, reversal fallback |

**continuation_trades.jsonl (shadow log of `_get_trend_entry_mode`, 46,899 rows):**

| Decision | Count |
|---|---|
| SKIP | 44,396 (94.7%) |
| GO (CONTINUATION returned GO) | 2,503 |
| Of which LONG | 2,503 |
| Of which SHORT | 0 |
| Top SKIP reason | `"no displacement: no valid displacement bar in last 3"` (41,792) |
| Second reason | `"exhaustion: overextended SHORT: 4789.2pts > 23.5 (1.5x ATR)"` (576) — note suspicious magnitude |

**Cross-reference**: Of 250 unique minutes where continuation engine said GO, only **20 had an ALPHA LONG GO/EXEC_FAILED** fire in the same minute. The continuation engine returns GO-to-itself thousands of times, but those GOs almost never reach `_trigger_gate` because:

1. The tick-loop `_patch2a_continuation_trigger` only runs in the `else` branch when `_near_level()` returned no level (event_processor.py:4303-4324). When an ALPHA liq level IS near, PATCH2A is skipped.
2. Zero `"type": "PATCH2A"` entries in decision_log confirm the PATCH2A path has **never actually invoked `_trigger_gate` with source="PATCH2A"** in the 6-day window.

### 3.6 Verdict — Pullback/DELTA vs. literature

| Criterion | PATCH2A_CONTINUATION | DELTA (`check_delta_trigger`) |
|---|---|---|
| Literatura-aligned | **YES (partial)** — consistent with Wyckoff BUEC/LPS + ICT OB retest structurally | **Cannot verify** — no events to inspect in 6-day window |
| Functional in production | **NO** (0 PATCH2A triggers in decision_log over 6 days; engine returns GO 2,503 times in shadow but these never reach gate because ALPHA preempts via `_near_level`) | **NO** (0 DELTA triggers in decision_log over 6 days) |
| Barbara's claim "never worked" | **SUPPORTED** for PATCH2A (0 invocations in decision_log) | **SUPPORTED** for DELTA (0 invocations in decision_log) |
| Test coverage | Only `scripts/backtest_patch2a.py` (no unit test) | No tests found |
| Recommendation | **REWRITE integration order in tick loop** — run PATCH2A evaluation **in parallel** with ALPHA near-level, not as fallback. Current design guarantees suppression whenever a liq level is within band. Additionally: audit `_detect_local_exhaustion` units (overext_pts magnitude 4789 suspicious). | **INVESTIGATE DETECTOR** — confirm `_trend_momentum_detector` is instantiated; verify expansion_lines inputs; add INFO-level logging on every M5 close decision. If detector returns detected=False >99% → REWRITE with simpler Wyckoff-LPS heuristic (M5 close back above last expansion-line high in uptrend). |

---

## PART 4 — Overall findings summary table

Ranked by architectural smell severity (non-literatura + unvalidated + used in prod = most concerning; displayed top-down).

| # | Mechanism | Literatura-aligned | Data-driven validated | Actually used (last 6 days) | Recommendation |
|---|---|---|---|---|---|
| 1 | **OVEREXTENSION reversal** (`_resolve_direction` SKIP fallback, 1.5×ATR) | **NO** (not in Wyckoff/ICT/ATS docs; "Sprint 8 manual") | **NO** (threshold "manual"; no backtest/calibration found in repo) | YES — 72 GO signals (0 fills due to EXEC_FAILED) | Highest smell. **RECALIBRATE** with 9-month backtest; investigate suspected unit-space bug in `_detect_local_exhaustion` (overext_pts magnitudes ~4789 point to GC-vs-MT5 mix). If PF<1.10 post-calibration: **REMOVE**. |
| 2 | **PATCH2A_CONTINUATION** (separate trigger path) | **YES (partial)** — Wyckoff BUEC/LPS + ICT OB retest | **NO** — only `scripts/backtest_patch2a.py` (no deployed calibration report) | **NO** — 0 PATCH2A triggers in decision_log. Engine returns GO 2,503× in shadow but suppressed by ALPHA precedence | **REWRITE integration order**. Current `else`-branch placement (line 4303) makes PATCH2A unreachable whenever `_near_level()` returns a level — which it does constantly. Barbara's "never worked" = SUPPORTED. |
| 3 | **DELTA trigger** (`check_delta_trigger`, `_delta_loop`) | **Unverifiable** (no events to inspect) | **NO** — no calibration doc | **NO** — 0 DELTA triggers in decision_log | **INVESTIGATE**: confirm detector instantiation; add INFO-level SKIP-reason logging. Likely dead code or broken detector. Barbara's "never worked" = SUPPORTED. |
| 4 | **GAMMA trigger** (`check_gamma_trigger`, `_gamma_loop`) | **YES** — ATS 6 / FUNC_M30_Framework §7.3 | **NO** — R:R threshold (`GAMMA_MIN_RR`) not verified as data-driven | **NO** — 0 GAMMA triggers in decision_log | **INSTRUMENT** the TP2-fallback R:R gate — likely silent filter. Shadow-log GAMMA return-None reasons at INFO level. |
| 5 | **M30_BIAS_BLOCK** equality-case in `derive_m30_bias` (level_detector.py:215) | ATS-aligned (bias = highest-timeframe box orientation) | N/A (deterministic rule) | YES — 238 LONG-blocks during the missed impulse 01:00-03:30 | **PATCH** equality case: add `>=` OR add a third "neutral" state when `liq_top == box_high` — see PART 1 root cause. |
| 6 | **PULLBACK** mode in `_get_trend_entry_mode` (liq_bot in LONG trend) | **YES** — ATS Strategy 2 "buy the dip"; Wyckoff LPS | YES (indirect — implicit in CAL-14..17 calibrations) | YES — fires as ALPHA LONG at liq_bot when bias permits | PRESERVE. Structurally correct. Only gated by upstream bias/data-staleness issues. |

### Proposed next sprints (priority order)

1. **Sprint B v3 — OVEREXT recalibration & unit-space audit** (addresses #1). Verify `_detect_local_exhaustion` uses MT5-consistent prices; run 9-month backtest gated on `overextension_atr_mult ∈ {0.5, 1.0, 1.5, 2.0, 2.5, 3.0}`; derive calibrated value or REMOVE.

2. **Sprint C — PATCH2A integration rewrite** (addresses #2). Let PATCH2A evaluate **parallel** to ALPHA `_near_level`, or unify trigger orchestration so an ALPHA near-level does not suppress a strong CONTINUATION. Target: raise PATCH2A invocations from 0 to O(100/week) in shadow, then A/B live.

3. **Sprint D — DELTA detector audit** (addresses #3). Confirm `_trend_momentum_detector` is instantiated and receiving correct expansion_lines; dump last 100 detector.compute() outputs; if detected<5% and the design requires ≥50%, REWRITE to Wyckoff-LPS heuristic.

4. **Sprint E — derive_m30_bias hardening** (addresses #5, extracted from PART 1 of this audit). `>=` equality handling + "neutral/unknown" third state; add M30_BIAS_BLOCK to decision_log.jsonl (currently only stdout).

5. **Sprint F — GAMMA R:R silent-filter audit** (addresses #4). Verify `GAMMA_MIN_RR` value and the 2:1 fallback interaction; shadow-log return-None reasons.

---

## PART 5 — System state

| Item | Status |
|---|---|
| Files modified | **ZERO** |
| Services restarted | **ZERO** |
| Capture processes | **3/3 intact** (verified 2026-04-20 via `Get-CimInstance Win32_Process`) |
| — PID 12332 | `python -m uvicorn quantower_level2_api:app --host 0.0.0.0 --port 8000` — RUNNING |
| — PID 8248 | `python iceberg_receiver.py` — RUNNING |
| — PID 2512 | `python watchdog_l2_capture.py` — RUNNING |
| MT5 / feeds / queue | NOT TOUCHED |
| Artefacts written by this audit | `ARCHITECTURAL_AUDIT_READ_ONLY.md` (this file), plus `_audit_queries.py`, `_audit_queries2.py`, `_audit_queries3.py`, `_audit_continuation.py`, `_audit_cross.py` (read-only helper scripts in the same directory) |

Investigation complete. No fixes proposed inline — all are research notes for subsequent sprint cycles with Barbara + Claude AI alignment.

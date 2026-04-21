# Sprint C READ-ONLY — `derive_m30_bias` Stuck-Bias Investigation

**Date:** 2026-04-20
**Mode:** 100% read-only (zero edits, zero restarts, zero capture touched)
**Scope:** Verify Sprint-B-v2 hypothesis that `derive_m30_bias` has a strict-inequality equality bug, and quantify its impact across `decision_log.jsonl`.

---

## 1. Function location

| Symbol | File | Line |
|---|---|---|
| `derive_m30_bias` (definition) | `C:\FluxQuantumAI\live\level_detector.py` | 217 |
| `_classify` helper (nested) | `C:\FluxQuantumAI\live\level_detector.py` | 232 |
| `_get_m30_bias` wrapper | `C:\FluxQuantumAI\live\level_detector.py` | 202 (calls `derive_m30_bias`) |
| Call-site #1 (confirmed) | `C:\FluxQuantumAI\live\level_detector.py` | 395 |
| Call-site #2 (provisional) | `C:\FluxQuantumAI\live\level_detector.py` | 396 |
| Call-site #3 (refresh_macro_context confirmed) | `C:\FluxQuantumAI\live\event_processor.py` | 1621 |
| Call-site #4 (refresh_macro_context provisional) | `C:\FluxQuantumAI\live\event_processor.py` | 1622 |
| Call-site #5 (position_monitor) | `C:\FluxQuantumAI\live\position_monitor.py` | 206 |

Assignments to `self.m30_bias`:
- `event_processor.py:485` — init `"unknown"`
- `event_processor.py:1610` — fallback `"unknown"` when parquet missing
- `event_processor.py:1626` — `self.m30_bias = confirmed_bias` (the only "real" set)

There is no debounce, no hysteresis, no explicit flip counter. Each call to `refresh_macro_context()` overwrites `self.m30_bias` with whatever `derive_m30_bias(df, confirmed_only=True)` returns from the last confirmed M30 row.

---

## 2. Literal code (`live/level_detector.py:217-265`)

```python
217: def derive_m30_bias(
218:     m30_df: pd.DataFrame | None, confirmed_only: bool = False
219: ) -> tuple[str, bool]:
220:     """
221:     Shared M30 bias derivation used by both entry and position-monitor paths.
222:
223:     Returns
224:     -------
225:     (bias, is_confirmed_source)
226:       bias: "bullish" | "bearish" | "unknown"
227:       is_confirmed_source: True when derived from a confirmed M30 box.
228:     """
229:     if m30_df is None:
230:         return "unknown", False
231:
232:     def _classify(row) -> str:
233:         import math
234:         box_high = row.get("m30_box_high", float("nan"))
235:         box_low  = row.get("m30_box_low",  float("nan"))
236:         liq_top  = row.get("m30_liq_top",  float("nan"))
237:         liq_bot  = row.get("m30_liq_bot",  float("nan"))
238:
239:         if math.isnan(liq_top) or math.isnan(box_high):
240:             return "unknown"
241:         if liq_top > box_high:
242:             return "bullish"
243:         if not math.isnan(liq_bot) and not math.isnan(box_low) and liq_bot < box_low:
244:             return "bearish"
245:         return "unknown"
246:
247:     try:
248:         confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
249:         if not confirmed.empty:
250:             return _classify(confirmed.iloc[-1]), True
251:
252:         if confirmed_only:
253:             return "unknown", False
254:
255:         unconf = m30_df[
256:             (m30_df["m30_box_id"].notna()) &
257:             (m30_df["m30_box_id"] > 0) &
258:             (m30_df["m30_liq_top"].notna())
259:         ]
260:         if unconf.empty:
261:             return "unknown", False
262:         return _classify(unconf.iloc[-1]), False
263:     except Exception as e:
264:         log.warning("derive_m30_bias failed: %s", e)
265:         return "unknown", False
```

Note: the docstring at line 206-207 (in `_get_m30_bias`) explicitly states the intended semantics — "UP breakout → `liq_top = fakeout_ext > box_high` → bullish bias" — i.e. the author presumed `liq_top > box_high` always holds on UP breakouts. In practice the production writer sets `liq_top == box_high` for roughly half of all rows (see §3 and §8).

---

## 3. Critical comparisons table

| Line | Comparison | Edge case (operands equal) | Actual behaviour | Literature expectation |
|---|---|---|---|---|
| 239 | `math.isnan(liq_top) or math.isnan(box_high)` | NaN → short-circuits → `unknown` | OK | OK |
| 241 | `liq_top > box_high` | **strict** — `==` falls through | equality NOT treated as bullish; flows into line 243 bearish check | ATS docs: "UP breakout → liq_top extends above box_high"; semantically `>=` after a confirmed UP fakeout |
| 243 | `liq_bot < box_low` | **strict** — `==` falls through → `unknown` | rows where `liq_bot == box_low` ignored (no bearish flag) | same asymmetric issue as line 241 |

**Suspect comparison:** **line 241 `if liq_top > box_high: return "bullish"`**.

Combined with line 243, whenever the confirmed row has `liq_top == box_high` **AND** `liq_bot < box_low`, the function returns `bearish`. The writer that produces `m30_liq_top` / `m30_box_high` in the parquet clamps `liq_top` to `box_high` on a DN breakout (so `liq_top = box_high` is the signature of a *bearish* confirmed box) — but that same clamping is also produced on confirmed boxes that came from a bilateral fakeout where only the DN side extended. The result is a strong bearish lock-in that does not self-release when price rallies back above the box.

---

## 4. Git history

The repository at `C:\FluxQuantumAI\.git` is **empty**:

```
$ git log --all --oneline
(no output)
$ git rev-list --all --count
0
$ git status
On branch master
No commits yet
```

**There is no incremental history.** No commits exist for `live/level_detector.py`; `git log -p -S "derive_m30_bias"` returns empty; `git log -p -S "liq_top > box_high"` returns empty. No SHA, author, or date can be attributed to the strict-`>` comparison from the git log. The only historical evidence is the parallel backup directories under `C:\FluxQuantumAI\Backups\` and `C:\FluxQuantumAI\sprints\.../backup_pre_fix*` — each snapshot of `level_detector.py` in those backups contains the same strict-`>` text at the same line (215 or 217 depending on snapshot), indicating the code has looked this way across every extant backup.

Explicit per the spec: **no incremental history; everything in the repo is uncommitted working tree ("No commits yet")**. A cold text-level `git log -p -S` therefore produces no output — this is an absence of evidence, not evidence of absence.

---

## 5. Stuck-periods quantification

Parsed `C:\FluxQuantumAI\logs\decision_log.jsonl` (12,022 decisions, 2026-04-14 01:00 UTC → 2026-04-20 09:02 UTC).

- Total decisions: **12 022**
- Bias distribution (live `m30_bias` read from `context.m30_bias`): `bearish=9 390`, `bullish=2 632`, `unknown=0`
- Stuck periods ≥ 1h continuous same bias: **7**

**Top 10 longest stuck periods:**

| # | Bias | Start (UTC) | End (UTC) | Duration (h) |
|---|---|---|---|---|
| 1 | bearish | 2026-04-16 13:58:14 | 2026-04-20 09:02:01 | **91.06** |
| 2 | bullish | 2026-04-15 23:09:10 | 2026-04-16 13:55:57 | 14.78 |
| 3 | bullish | 2026-04-15 05:46:26 | 2026-04-15 18:08:41 | 12.37 |
| 4 | bullish | 2026-04-14 16:26:14 | 2026-04-15 00:14:11 | 7.80 |
| 5 | bearish | 2026-04-14 01:00:08 | 2026-04-14 07:28:10 | 6.47 |
| 6 | bearish | 2026-04-15 18:08:49 | 2026-04-15 23:08:40 | 5.00 |
| 7 | bearish | 2026-04-15 00:49:27 | 2026-04-15 04:24:08 | 3.58 |
| 8-10 | (sub-second flickers around 2026-04-15 23:08-23:09 where bullish/bearish interleave — transient refresh collisions, not meaningful) | | | < 0.01 |

**Today (2026-04-20):**

- Rows today: **2 978**
- Longest stuck period today: `bearish` continuous from **00:20:30 → 09:02:01** = **8.69 h** — **confirms** the operator-observed 8h stuck claim (slightly longer than the initial 8h estimate).
- The 2026-04-20 window is in fact a tail of the 91-hour streak starting 2026-04-16 13:58 — there has been **no bias flip for ~91 h continuous** across the entire log window as of 09:02:01 UTC.
- Today: direction distribution `SHORT=2931, LONG=47`; action distribution `BLOCK=2826, GO=98, EXEC_FAILED=54` — matches the 69 GO SHORT / 0 LONG operator claim (actual: 98 GO, of which LONG signals that survived to GO level = 0 after BIAS_BLOCK).

---

## 6. Violations during stuck periods

Across all 12 022 decisions:

| Violation pattern | Count |
|---|---|
| `m30_bias="bearish"` AND `price_mt5 > box_high_mt5` | **4 603** |
| `m30_bias="bearish"` AND `liq_top_mt5 >= box_high_mt5` | **8 205** |
| `m30_bias="bullish"` AND `price_mt5 < box_low_mt5` | 1 505 |

**Smoking-gun pattern — today only (2026-04-20, 2 978 rows):**

- Rows with `m30_bias == "bearish"` AND `|liq_top_mt5 − box_high_mt5| ≤ 0.01` → **2 705 / 2 978 = 90.8 %**.

Sampled rows across today confirm:

| Timestamp | bias | price | box_low | box_high | liq_top | liq_bot |
|---|---|---|---|---|---|---|
| 00:20:30 | bearish | 4759.91 | 4768.51 | 4773.86 | **4773.86** | 4763.51 |
| 01:16:20 | bearish | 4791.62 | 4761.82 | 4768.17 | **4768.17** | 4760.22 |
| 01:34:46 | bearish | 4806.90 | 4762.15 | 4768.50 | **4768.50** | 4760.55 |
| 01:52:57 | bearish | 4813.31 | 4761.36 | 4767.71 | **4767.71** | 4759.76 |
| 09:02:01 | bearish | 4797.02 | 4787.27 | 4791.92 | 4801.57 | 4780.37 |

In all early-morning samples `liq_top == box_high` **exactly** while price was trading well above the box. The 09:02:01 sample shows `liq_top=4801.57 > box_high=4791.92` (differential) — which should have flipped bias to `bullish` on the confirmed side **but the last confirmed M30 row at 05:30 still has `liq_top == box_high == 4828.45`**, so `confirmed_only=True` locks bias to bearish. See §8.

Among today's **29 unique `(box_low, box_high, liq_top, liq_bot)` tuples**, the distribution is:

- 16 tuples with `liq_top == box_high` (bearish-locking)
- 10 tuples with `liq_top > box_high` (would flip bullish) — but most are *unconfirmed* boxes
- 3 tuples with `liq_top < box_high` apparent (e.g. `(4792.93, 4797.53, 4767.53, ...)`) — these are **stale carry-forward** levels where `liq_top_mt5` still points at a prior box's level; they are not from the current confirmed box.

---

## 7. Flip-condition code (where bias is set and read)

**Set:** `event_processor.refresh_macro_context()` at `C:\FluxQuantumAI\live\event_processor.py:1602-1652`. The important block:

```python
1615: df = pd.read_parquet(M30_BOXES_PATH)
...
1621: confirmed_bias, is_confirmed = derive_m30_bias(df, confirmed_only=True)
1622: provisional_bias, _         = derive_m30_bias(df, confirmed_only=False)
1625: with self._lock:
1626:     self.m30_bias           = confirmed_bias
1627:     self.m30_bias_confirmed = is_confirmed
1628:     self.provisional_m30_bias = provisional_bias
```

Refresh cadence: `event_processor.py:2239` — `if self._macro_ctx_refresh_needed or (time.monotonic() - self._macro_ctx_last_refresh > 60): refresh_macro_context(...)` — i.e. **every gate trigger if stale >60 s**. Plus an initial call at line 4507.

**Read (decision gate):** `event_processor.py:2242-2247` (reads under lock), then **`2381-2406`** — the `M30_BIAS_BLOCK` check:

```python
2381: if not m30_bias_confirmed:
2382:     if provisional_m30_bias in ("bullish", "bearish"):
2383:         # provisional-only -> info log, no block
2384:         ...
2391: else:
2392:     if m30_bias == "bullish" and direction == "SHORT":
2393:         print(f"... M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected ...")
2397:         return
2398:     if m30_bias == "bearish" and direction == "LONG":
2399:         print(f"... M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG rejected ...")
2403:         return
```

**Debounce / hysteresis?** None. Any refresh that re-reads the parquet and produces a different classification overwrites `self.m30_bias` directly. The flip is atomic on the next gate cycle (at most 60 s after the parquet is updated).

**Confirmed vs provisional separation?** Yes — but asymmetrically. Only `confirmed_bias` enters `self.m30_bias` and drives the hard block (line 1626). `provisional_m30_bias` is stored (line 1628) but only used in line 2382 to emit an info log when the confirmed side is `unknown` — it cannot override or unlock a wrongly-held confirmed `bearish`. So when the last confirmed M30 row has `liq_top == box_high` (bearish via fall-through) and the next confirmed M30 box does not yet print, bias stays bearish indefinitely.

---

## 8. Naive bias vs live bias — parquet comparison (last 24 h)

From `C:\data\processed\gc_m30_boxes.parquet` (73 662 rows total), last 24 h = 27 M30 bars.

Across the **full parquet** (historical):

| Relation | Count | Share |
|---|---|---|
| `m30_liq_top > m30_box_high` | 36 158 | 49.1 % |
| `m30_liq_top == m30_box_high` | **32 901** | **44.7 %** |
| `m30_liq_top < m30_box_high` | **0** | 0.0 % |

`liq_top` is **never** strictly below `box_high` in the parquet — the writer always clamps it at `box_high` or allows it to extend above. Given that ≈ 44.7 % of rows have `liq_top == box_high`, and the `derive_m30_bias` strict `>` at line 241 disqualifies every one of those rows from being classified bullish, **any confirmed row with `liq_top == box_high` AND `liq_bot < box_low` deterministically yields bearish** — including rows where the M30 bar closed above `box_high` (i.e. naive/ATS literature would call bullish).

**Divergence examples — today's confirmed rows:**

| M30 bar ts | box_low | box_high | liq_top | liq_bot | close | derive bias | naive close bias | equal? |
|---|---|---|---|---|---|---|---|---|
| 2026-04-20 00:00 | 4849.55 | 4859.10 | 4859.10 | 4783.65 | 4783.85 | **bearish** | bearish | eq |
| 2026-04-20 00:30 | 4849.55 | 4859.10 | 4859.10 | 4783.65 | 4792.65 | **bearish** | bearish | eq |
| 2026-04-20 01:00 | 4849.55 | 4859.10 | 4859.10 | 4783.65 | 4821.25 | **bearish** | bearish | eq |
| 2026-04-20 01:30 | 4849.55 | 4859.10 | 4859.10 | 4783.65 | 4828.75 | **bearish** | bearish | eq |
| 2026-04-20 02:00 | 4849.55 | 4859.10 | 4859.10 | 4783.65 | 4820.10 | **bearish** | bearish | eq |
| 2026-04-20 05:00 | 4808.45 | 4828.45 | 4828.45 | 4806.85 | 4807.80 | bearish | bearish | eq |
| 2026-04-20 05:30 | 4808.45 | 4828.45 | 4828.45 | 4806.85 | 4802.05 | bearish | bearish | eq |
| 2026-04-20 07:00 | 4799.85 | 4816.40 | 4825.40 | 4799.85 | 4816.95 | **bullish** | bullish | *not eq* |
| 2026-04-20 09:30 | 4799.85 | 4816.40 | 4825.40 | 4799.85 | 4808.60 | **bullish** | neutral | *not eq* |

**Key observation (2026-04-20 07:00 onward):** unconfirmed rows DO classify `bullish` via the provisional path (`liq_top=4825.40 > box_high=4816.40`). That means `provisional_m30_bias == "bullish"` since 07:00 UTC today. However the **live decision path only hard-blocks using `self.m30_bias` (= confirmed_bias)** — and the last confirmed row (05:30, `liq_top == box_high`) still yields `bearish`. So LONG is still hard-blocked while the provisional bias correctly reads bullish. Over 2 h of potential LONG opportunities missed after 07:00 even with the provisional signal present.

In summary:

- `derive` and naive (close-vs-box) agree **89 %** of the time on confirmed rows in the last 24 h — they diverge specifically when close pushed inside the box after a DN breakout (expected) or when the bullish provisional path is active but confirmed is not (the live issue).
- The fall-through to `unknown` on line 245 never fires for `m30_liq_top.notna()` rows because there are zero rows with `liq_top < box_high` — meaning the function has only two effective outputs on live data: `bullish` (strict >) or `bearish` (when `liq_bot < box_low`, which is very common).

---

## 9. Root-cause hypothesis — ranked A/B/C

### Hypothesis A — Strict `>` equality bug at `level_detector.py:241` (confidence **HIGH**)

Evidence:

- Literal code inspection confirmed (§2).
- 90.8 % of today's 2 978 decisions had `liq_top_mt5 ≈ box_high_mt5` (Δ ≤ 0.01) while bias was bearish (§6).
- Full parquet shows 32 901/73 662 = 44.7 % rows have `liq_top == box_high` exactly (§8); zero rows with `liq_top < box_high`.
- Last confirmed M30 row at 05:30 UTC today has `liq_top == box_high == 4828.45` AND `liq_bot=4806.85 < box_low=4808.45` → `_classify` returns `bearish` deterministically.
- Price rallied from 4807 → 4825 GC after 05:30 but `self.m30_bias` never flipped because `refresh_macro_context` only writes the **confirmed** bias to `self.m30_bias`.

### Hypothesis B — Confirmed-only hard-block gating at `event_processor.py:2391-2403` masks provisional bullish (confidence **HIGH**)

Evidence:

- From 07:00 UTC today, `provisional_m30_bias = "bullish"` (verified via parquet last-row classification: `liq_top=4825.40 > box_high=4816.40`).
- Gate logic at line 2382 only emits an info log when provisional is bullish and confirmed is `unknown`; it does NOT unlock when confirmed is `bearish` (the "stale bearish carrying over from prior box" case). Line 2391 `else` branch runs unconditionally whenever `m30_bias_confirmed=True`, regardless of how stale or mis-classified that confirmed bias is.
- The gate code is architecturally correct relative to its docstring ("only CONFIRMED m30_bias may hard-block"), but when Hypothesis A produces a wrong confirmed bearish, Hypothesis B ensures no LONG can ever pass until a new confirmed box prints with `liq_top > box_high` strictly.

A and B together form the full blocking chain; neither alone would produce 91 h of stuck bearish bias.

### Hypothesis C — Parquet writer semantics (out of scope here, but material)

Evidence:

- The zero rows with `liq_top < box_high` imply the writer clamps `m30_liq_top` to `max(box_high, fakeout_ext)`. That's a valid upstream convention, but it means `liq_top == box_high` is ambiguous — it could mean either "no UP fakeout, DN-only box" or "equalized after a tie". `derive_m30_bias` cannot disambiguate.
- This is a *contributing* factor — fixing only A without re-examining the writer could shift the false-positive from "bearish fall-through" to "bullish over-counting" on truly bilateral/undecided boxes.

Confidence: medium — needs a separate audit of the parquet-writer code path.

---

## 10. Proposed next steps (investigation-only, no fix)

1. **Sprint D — Audit parquet writer.** Identify the exact producer of `m30_liq_top`/`m30_liq_bot`/`m30_box_high` in the M30 boxes parquet; map whether `liq_top == box_high` means "DN breakout confirmed" vs "no breakout yet" vs "bilateral". Sprint-C can't disambiguate A vs C without this.
2. **Sprint E — Literature reconciliation.** Confirm with ATS Docs the intended semantics of the bullish branch (strict `>` vs `>=` vs "bull breakout flag"). The docstring in `_get_m30_bias` (line 206) implies strict, but production data shows equality is a valid bullish outcome.
3. **Sprint F — Gate-layer override.** Design a mechanism whereby `provisional_m30_bias = "bullish"` from a later-in-time unconfirmed row can at minimum downgrade a stale confirmed bearish to "no hard block" (with extra score requirements). See existing design note in `MISSED_LONG_INVESTIGATION.md` §5.
4. **Sprint G — Decision-log hardening.** Add `context.m30_bias_confirmed`, `context.provisional_m30_bias`, and a `M30_BIAS_BLOCK` explicit decision_log entry so post-hoc audit can disambiguate blocks by cause (currently only stdout log).
5. **No production change in this sprint.** Do not modify comparisons, writer, or gate until (1)-(4) converge.

---

## 11. System state — verification

- Edits to `live/`, `scripts/`, `settings.json`, or any production file: **0**
- Service restarts: **0**
- Capture processes verified intact (`Get-CimInstance Win32_Process`):

| PID | Name | CommandLine |
|---|---|---|
| 2512 | python.exe | `watchdog_l2_capture.py` |
| 8248 | python.exe | `iceberg_receiver.py` |
| 12332 | python.exe | `uvicorn quantower_level2_api:app --host 0.0.0.0 --port 8000` |

**Capture 3/3 intact. Investigation read-only, no side effects.**

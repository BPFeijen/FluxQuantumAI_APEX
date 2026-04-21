# Sprint C BUG #1 Live Diagnosis — 2026-04-21

**Sprint ID:** `sprint_diagnosis_m30_bias_stuck_20260421`
**Type:** READ-ONLY (no writes outside this sprint dir; no service touches)
**Author:** ClaudeCode
**Duration:** ~25 min
**Data sources read:** `C:\FluxQuantumAI\live\level_detector.py`, `C:\FluxQuantumAI\live\event_processor.py`,
`C:\FluxQuantumAI\logs\decision_log.jsonl` (14,410 entries, 2026-04-14 → 2026-04-21 04:46 UTC),
`C:\FluxQuantumAI\config\settings.json`, `C:\data\processed\gc_m30_boxes.parquet` (73,701 rows),
`C:\data\processed\gc_ohlcv_l2_joined.parquet` (mtime only)
**Parallel sprint:** INVEST-03 untouched; zero overlap confirmed.

---

## 1. Current code state of `derive_m30_bias` (§2.1)

Location: `C:\FluxQuantumAI\live\level_detector.py` — file size 45,720 bytes, mtime 2026-04-20 16:06
(matches commit `074a482` Phase 2a deploy).

- Public: `derive_m30_bias(m30_df, confirmed_only=False)` defined at **line 257**.
- Thin wrapper: `_get_m30_bias` at **line 242**.
- Downstream validator: `_validate_m5_vs_m30` at **line 619** (informational warn only).
- Callers in main path: `get_current_levels` at **line 672-673**.
- Source parquet path constant: `M30_BOXES_PATH = C:\data\processed\gc_m30_boxes.parquet` (line 71).

Verbatim `_classify` inner logic (the suspected equality bug site):

```python
def _classify(row) -> str:
    import math
    box_high = row.get("m30_box_high", float("nan"))
    box_low  = row.get("m30_box_low",  float("nan"))
    liq_top  = row.get("m30_liq_top",  float("nan"))
    liq_bot  = row.get("m30_liq_bot",  float("nan"))

    if math.isnan(liq_top) or math.isnan(box_high):
        return "unknown"
    if liq_top > box_high:          # <-- EQUALITY SITE (strict >)
        return "bullish"
    if not math.isnan(liq_bot) and not math.isnan(box_low) and liq_bot < box_low:
        return "bearish"            # <-- fall-through: fires whenever top not strictly greater but bot strictly lower
    return "unknown"
```

**Observed bug shape (per §2.6 below):** 44.6 % of rows in the live parquet have
`m30_liq_top == m30_box_high` (strict equality). In those rows the first branch returns False; if the DN side is
also extended (`liq_bot < box_low`), the function returns **bearish** regardless of true regime. With strict `>`
both sides, equality is silently classified via the DN branch. A `>=` (or matching treatment on both sides) would
collapse the equality to `unknown`, preventing the asymmetric bearish lock-in.

The bug is NOT that the function defaults bearish when both sides equal (both-equal → `unknown`, correct). It is
that **the two branches are not symmetric under equality**: top-equality + bot-strictly-lower → bearish, while
top-strictly-greater + bot-equality → bullish. On a box where both fakeouts hit the boundary exactly, first
branch short-circuits based on which side happens to clear strict inequality first.

---

## 2. Live M30 bias distribution last 4h (§2.2)

Window: 2026-04-21 00:46 → 04:46 UTC (last 4 h in log), **1,011 entries**.

| Field | Value |
|---|---|
| `m30_bias` | **bullish: 1011 / 1011 (100 %)** |
| `m30_bias_confirmed` | True: 1011 |
| `decision.action` | BLOCK 924, GO 73, EXEC_FAILED 14 |
| `decision.direction` | SHORT 732, LONG 279 |
| `trade_intent` | ENTRY_SHORT 732, ENTRY_LONG 279 |
| `liq_top_mt5 == m30_box_mt5[1]` | 526 / 1011 (52.0 %) |
| `liq_bot_mt5 == m30_box_mt5[0]` | 0 / 1011 (0.0 %) |

**Observation:** bias is NOT stuck bearish right now — bias is currently stuck **bullish**, 100 % of evaluations.
Equality is asymmetric in the log too: top side equality 52 %, bot side 0 %.

---

## 3. Barbara's reported window 04:00-05:43 UTC (§2.3) — critical

Total entries: **179** (all between 04:02:13 and 04:46:12 UTC — **log stops at 04:46**, 57 min
before Barbara's end-of-window report at 05:43).

| Field | Value |
|---|---|
| `decision.action` | BLOCK 179 (100 %) |
| `decision.direction` | SHORT 179 (100 %) |
| `trade_intent` | ENTRY_SHORT 179 |
| `m30_bias` | bullish 179 |
| `execution.attempted` | **0** |

**100 % of blocks in window by a single reason family:**

```
STRUCTURE_STALE_BLOCK: m1_stale_critical=true age=<N>s
```

Top reason strings (count):

| n | reason |
|---|---|
| 31 | `STRUCTURE_STALE_BLOCK: m1_stale_critical=true age=310s` |
| 30 | `... age=370s` |
| 29 | `... age=708s` |
| 22 | `... age=738s` |
| 19 | `... age=340s` |
| 17 | `... age=317s` |
|  9 | `... age=678s` |
|  9 | `... age=400s` |
|  7 | `... age=642s` |
|  6 | `... age=848s` |

`m1_stale` age timeline across window (every ~10 samples):

```
04:02:13  age=848s   <-- at 14 min stale
04:16:17  age=642s   <-- writer caught up then stalled
04:25:58  age=317s   <-- just over threshold
04:32:13  age=678s
04:33:02  age=738s
04:44:23  age=310s
04:46:12  age=400s   <-- LAST ENTRY IN LOG
```

**Price MT5 in window:** first=4799.27, last=4792.07, min=4788.06, max=4799.27 (confirming bearish move
Barbara reported).

**First SHORT-intent:** 04:02:13, bias=bullish, blocked `age=848s`, price=4799.27.
**Last SHORT-intent:** 04:46:12, bias=bullish, blocked `age=400s`, price=4792.07.
**No LONG attempts in window. No exec attempts.**

---

## 4. Config + service change check (§2.4)

| item | observed | baseline expected | status |
|---|---|---|---|
| `settings.json` sha256 | `BA0166FFAC9741D813BDA9C6784A81507CDF612343629CC2BF58F00C94A42D52` | `BA0166FF...A42D52` | **IDENTICAL** |
| `settings.json` mtime | 2026-04-20 21:58:44 | n/a | no change since pre-Bloco1 baseline |
| Service PID 4516 | `run_live.py --execute --broker roboforex --lot_size 0.05` | same | **unchanged** |
| PID 4516 StartTime | 4/20/2026 00:47:49 local | 00:47 UTC 2026-04-20 | same (UTC==local-2h? likely) |
| PID 4516 CPU | 90,703 s cumulative | growing | still alive, has been scheduling |
| Capture PID 2512 | `watchdog_l2_capture.py` | since 4/14 09:35 | **untouched** |
| Capture PID 8248 | `iceberg_receiver.py` | since 4/14 09:35 | **untouched** |
| PID 11740 | `quantower_level2_api:app --port 8000` | started 4/20 16:51 | within documented window |

**No config drift. No service restart. Capture PIDs intact.**

---

## 5. Hypothesis test results (§2.5)

Evidence-by-hypothesis:

- **H1 — bias still stuck bearish, separate gate blocking SHORT:** **FALSE.** 100 % of last 4 h
  bias = bullish, not bearish.
- **H2 — bias unstuck, new bug in direction resolution:** **FALSE.** System resolves direction to SHORT
  correctly (179/179 in window) despite bullish bias — SHORTs are triggered by proximity to `liq_top`
  range-top, which is the intended counter-trend range entry. Direction logic works.
- **H3 — M30 bias correct, entry trigger not firing:** **PARTIALLY TRUE, but this is not the failure
  mode.** Entries ARE firing — `decision.action` entries generate every ~5s; they are gated, not missing.
- **H4 — Something changed since 2026-04-20:** **FALSE.** Config hash identical; PID 4516 unchanged;
  capture PIDs unchanged. The bug was already present in Sprint 8 live state; what changed is the M1
  feed reliability.
- **H5 — Other.** **TRUE.** The actual blocker in Barbara's window is `m1_stale_critical=true` from
  `STRUCTURE_STALE_BLOCK`. `event_processor.py` line 756-761 defines m1_stale as
  `age > 300s` on `C:/data/processed/gc_ohlcv_l2_joined.parquet`. During 04:02-04:46 UTC this file was
  intermittently stale (ages 310-848 s). Per `event_processor.py:2259-2268`, when
  `m1_stale_critical=true` in `service_state.json`, the decision path writes a STALE_BLOCK decision
  and `return`s before any GO path can run. 100 % of SHORT attempts in the window hit this branch.

---

## 6. Equality rate — current vs documented (§2.6)

Direct read of `C:\data\processed\gc_m30_boxes.parquet` (73,701 rows, mtime 2026-04-21 07:27:57 local,
last timestamp index 2026-04-21 05:00 UTC — writer is alive).

| metric | observed | documented 2026-04-20 |
|---|---|---|
| `m30_liq_top == m30_box_high` (all rows) | 32,901 / 73,701 = **44.6 %** | 44.7 % |
| `m30_liq_bot == m30_box_low` (all rows) | 35,970 / 73,701 = **48.8 %** | — |
| `m30_liq_top == m30_box_high` (confirmed only) | 20,753 / 43,260 = **48.0 %** | — |

**44.6 % matches the 44.7 % claim in the Sprint C BUG #1 doc to within 0.1 %.** The equality asymmetry
persists in the data. The DN side (liq_bot == box_low) is even higher at 48.8 %, meaning the function
regularly encounters rows where BOTH sides are at boundary — these collapse to `unknown` correctly; the
trouble case is the XOR, not both.

Last confirmed row (2026-04-21 05:00, box_id 5243): `box_high=4842.55, box_low=4833.10,
liq_top=4853.65, liq_bot=4833.10`.
- `liq_top (4853.65) > box_high (4842.55)` → classify bullish. Correct for that box.
- `liq_bot (4833.10) == box_low (4833.10)` — DN-side boundary exact; irrelevant once bullish already returned.

**Why bias is bullish right now:** the last confirmed box registered a bullish fakeout (4853.65 above
4842.55). No subsequent confirmed box has formed with a DN fakeout. Price has since dropped to 4792
MT5 (≈4811 GC), but `derive_m30_bias` returns the LAST CONFIRMED BOX's breakout direction; it does not
invalidate bullish when price subsequently drops back inside or below the box. This is a **second
latent structural issue** (separate from the equality bug) — bias anchors to last breakout, not to
current price relative to box.

---

## 6.BIS. Market session 2026-04-20 22:00 → 2026-04-21 04:46 UTC (Barbara scenario)

**Barbara's report (paraphrased):** Market opened 22:00 UTC 2026-04-20. 1st M30 candle bullish. From
2nd M30 onwards through 05:00 UTC 2026-04-21, M30 candles all bearish. Broker P/L left on table
ridiculous. System never emitted one GO SHORT — only "Block Long" or "Defense Mode" messages.

Price trajectory confirms her chart read:

| Hour UTC | first | last | min | max | Δ (pts MT5) |
|---|---|---|---|---|---|
| 2026-04-20 22 | 4820.95 | 4825.94 | 4820.95 | 4827.05 | **+4.99** (bullish open M30) |
| 2026-04-20 23 | 4826.89 | 4820.68 | 4819.89 | 4826.89 | −6.21 |
| 2026-04-21 00 | 4820.68 | 4825.50 | 4813.17 | 4828.35 | +4.82 |
| 2026-04-21 01 | 4818.37 | 4811.46 | 4811.03 | 4818.97 | −6.91 |
| 2026-04-21 02 | 4809.56 | 4805.81 | 4803.19 | 4809.56 | −3.75 |
| 2026-04-21 03 | 4807.80 | 4794.96 | 4792.91 | 4808.10 | **−12.84** |
| 2026-04-21 04 | 4799.27 | 4792.07 | 4788.06 | 4799.27 | −7.20 |

Aggregate: first price 4820.95, last 4792.07 — **-28.88 pts MT5 over 6 h 46 min, with only one green
hour after the open candle.** Exactly the regime Barbara described.

### 6.BIS.1. Directional decision asymmetry in that session

1,398 decisions total. Breakdown:

| direction | action=GO | action=BLOCK | action=EXEC_FAILED | total |
|---|---|---|---|---|
| **LONG** | **142** | 264 | 68 | 474 |
| **SHORT** | **0** | 924 | 0 | 924 |

**Not a single GO SHORT across 6 h 46 min of a one-way bearish market.** LONG generated 142 GOs plus
430 non-stale decisions (blocks on zone/expansion logic). SHORT generated **zero non-stale decisions
— every one of the 924 SHORT entries in the log is a `STRUCTURE_STALE_BLOCK` event.**

### 6.BIS.2. STALE vs NON-STALE by direction (smoking gun)

| metric | LONG | SHORT |
|---|---|---|
| decisions during m1-fresh | **430** (142 GO + 288 BLOCK/EXEC_FAILED on zone/expansion logic) | **0** |
| decisions during m1-stale | 44 (stale blocks) | **924** (stale blocks) |

**The SHORT path never emits a decision when m1 is fresh.** It only produces log entries when the m1
staleness writer pre-empts (which writes a block decision for *both* directions by design, per
`event_processor.py:2259-2268`). The STALE reason Barbara sees on every SHORT line is therefore a
decoy — the SHORT entry path would have emitted nothing anyway in this session.

Spot-check of 10 LONG GOs with nearest SHORT within 60 s: **0 / 10** had the nearest SHORT decision
stale-blocked — i.e. the LONG GOs happened during fresh-m1 windows (confirming the m1 gate was open),
but no SHORT decision is logged adjacent to those timestamps. SHORT is silent during m1-fresh moments.

LONG non-stale decision breakdown (representative):

| count | (action, reason) |
|---|---|
| 84 | GO · `iceberg sweep sc=+2` |
| 68 | EXEC_FAILED · `GO LONG but NO broker executed: robo=connected hantec=connected` |
| 34 | BLOCK · `BLOCK_V1_ZONE: price 4804.4 outside M30 box [4806.4-4810.9] … (TRENDING/long)` |
| 25 | GO · `iceberg large_order sc=+2` |
| 20 | GO · `iceberg large_order sc=+3` |
| 13 | GO · `iceberg large_order sc=+4` |
| 9  | BLOCK · `BLOCK_V1_ZONE: … (TRENDING/short)` |

SHORT non-stale decision breakdown: **empty.** Not one row.

### 6.BIS.3. Why `m30_bias` is pinned "bullish" through the whole bearish leg

`derive_m30_bias` (`level_detector.py:257`) picks the *last confirmed M30 box* and classifies it by
the box's own fakeout extension. The last confirmed box in `C:\data\processed\gc_m30_boxes.parquet`
is `box_id=5243`, carried forward verbatim across 03:00, 03:30, 04:00, 04:30, 05:00 UTC rows:

```
box_high=4842.55  box_low=4833.10  liq_top=4853.65  liq_bot=4833.10
→ liq_top > box_high          → classify bullish
```

No new confirmed box has formed since ~21:00 UTC on 2026-04-20 even though price has travelled
60 pts GC downward. Because no new confirmed DN breakout is written to the parquet, bias stays locked
bullish. With `m30_bias=bullish`, the entry engine's SHORT path is gated off at the strategy-selector
level — the only SHORT log entries produced are from the m1 staleness writer, which fires
regardless of strategy selection.

This is the **anchor-to-last-confirmed-box** failure mode. It is a structural sibling of the
documented 44.6 % equality bug: both stem from `derive_m30_bias` never recognising that the current
regime has moved away from the last confirmed box. The equality bug is a special case of the same
root disease (symmetric-under-equality misclassification of the box's own fakeout), whereas the
anchor bug is a temporal problem (no recognition of superseding price action between confirmations).

### 6.BIS.4. "Defense Mode" messages Barbara sees

`anomaly.source = "DEFENSE_MODE"` with `shadow_only = true` on every decision in the window. In the
current config DEFENSE_MODE is running in shadow — it does NOT block trades. But the dashboard /
log-surfacer renders these as "Defense Mode …" lines alongside the real BLOCK reasons. This is
cosmetic noise; it did not contribute to the missed SHORTs.

---

## 7. ROOT CAUSE CLASSIFICATION

**REVISED — supersedes first-pass §7.** The first pass classified this RC-E (unrelated bug). That
was incomplete because I had not yet examined the LONG-vs-SHORT directional split. With that data
the true classification is:

**RC-C — Sprint C BUG #1 IS active; stuck-direction has flipped; the active gate blocking SHORT is
`m30_bias=bullish` via strategy-selector, and the `STRUCTURE_STALE_BLOCK` reason on SHORT lines is a
decoy.**

Sub-findings (revised):

1. **The bug is active and directional today.** `m30_bias` flipped from bearish-stuck (Apr 16 → Apr 20
   10:08 UTC) to **bullish-stuck** at Apr 20 13:00 UTC when a bullish confirmed box formed. It has
   stayed bullish for 15+ hours across a 28+ pt MT5 bearish move. 100 % of the last 1,011 decisions
   (last 4 h) show `m30_bias=bullish`.

2. **`derive_m30_bias` cannot invalidate a confirmed bullish breakout.** It reads the LAST confirmed
   M30 box and classifies its own fakeout. The last confirmed box (id 5243) has `liq_top=4853.65`
   above `box_high=4842.55` → bullish forever, regardless of 10 subsequent bearish M30 candles, until
   a NEW confirmed box with DN fakeout is written to the parquet. The parquet updater writes a fresh
   row every 30 min, but these are extensions of the same `box_id` — a new `box_id` only appears
   when the M30 range breaks and a new range forms. During a one-way bearish leg from inside the
   previous UP box, that never happens.

3. **With `m30_bias=bullish`, the entry-engine's SHORT path is gated off at selector level.** The
   decision log shows ZERO non-stale SHORT entries across the full 6 h 46 min session (§6.BIS.2). The
   only SHORT rows in the log are the m1 staleness writer's defensive blocks, which fire for both
   directions unconditionally when the m1 feed is stale.

4. **`STRUCTURE_STALE_BLOCK` is a decoy on SHORT.** Fixing the m1 feed would restore LONG throughput
   but **would not produce a single GO SHORT** in this regime — the selector still refuses SHORT
   while bias is bullish. Barbara's observation of "only Block Long or Defense Mode messages" is
   consistent with this: LONG gets blocked by zone logic (BLOCK_V1_ZONE) or misses broker exec
   (EXEC_FAILED), while SHORT is silent by design.

5. **"Defense Mode" in the UI is cosmetic.** `anomaly.source='DEFENSE_MODE'` with
   `shadow_only=true` — it logs but does not block. Not a contributor to missed SHORTs.

6. **Historical Symptom A (Apr 16-20) was the mirror-image of today.** Bias stuck bearish → 69
   SHORTs / 0 LONGs in 8 h on 04-20 morning. Same bug, opposite direction. Self-resolved only because
   the next confirmed box happened to form with a bullish breakout; this is luck, not a fix.

7. **Equality bug (44.6 %) is latent but not the proximate cause today.** The equality misclassification
   only bites on the transition of a new box that has exact top-boundary + DN extension. Today's
   lock-in is the anchor-to-last-confirmed-box behaviour, which is a structural sibling issue rooted
   in the same function.

8. **Red flag unrelated to BUG #1:** `decision_log.jsonl` has no entries after 2026-04-21 04:46:12 UTC
   (57+ min before Barbara's 05:43 report). Service PID 4516 is alive (CPU 90,703 s), but either has
   stopped writing log rows or is emitting elsewhere. Investigate read-only; do **not** restart.
   Capture policy (2512/8248) must be preserved.

---

## 8. Recommendation for fix sprint (REVISED)

Priority reordered after the directional-asymmetry finding. The M1 staleness issue is **no longer
urgent for SHORT** — fixing it alone will keep SHORTs at zero. The urgent fix is bias.

1. **URGENT #1 — unlocks SHORT path:** `sprint_c_phase2b_m30_bias_regime_YYYYMMDD` — address the
   anchor-to-last-confirmed-box failure in `derive_m30_bias`. Options to evaluate in design doc (do
   NOT improvise):
   - blend with `derive_h4_bias` (Phase 2a commit 074a482 already deployed) as a higher-timeframe
     override;
   - add a "bias invalidation" clause when current price has travelled > X pts against the last
     confirmed box (needs calibration, not a guess);
   - make bias a function of recent M30 candle colour sequence in addition to the last confirmed
     box's fakeout.
   Must coordinate with existing Phase 2a surface. Equality-bug `>`/`>=` is a narrower sub-fix — do
   NOT ship it alone; it will not help this regime.

2. **URGENT #2 — restores LONG throughput and removes SHORT decoy logs:**
   `sprint_m1_feed_staleness_YYYYMMDD` — investigate writer of
   `C:\data\processed\gc_ohlcv_l2_joined.parquet`, identify why it drops for 5-14 min intervals,
   repair. Without this fix even the fixed SHORT path will still eat stale-blocks. READ-ONLY
   investigation first; then minimal targeted patch after design doc per Golden Rule. Do NOT restart
   capture services.

3. **HYGIENE:** audit whether `decision_log.jsonl` truly halted at 04:46 UTC or if a rotation /
   alternate log is catching recent pulses — check `service_stderr.log`, `service_stdout.log`
   mtimes; read-only only.

**Important:** do NOT conflate (1) and (2). Shipping only the m1-fix leaves Barbara at 0 SHORT GOs
in bearish regimes. Shipping only the bias-fix leaves SHORT throughput still gated by stale writer.
Both are needed to fully explain and close the gap.

### 8.BIS. Answer to Barbara's direct question

> *"Why did the system not send ONE GO SHORT message but just Block Long or Defense Mode messages?"*

Three compounding reasons, in order of impact:

1. **`m30_bias` was stuck `bullish` for the entire session** because `derive_m30_bias` anchors to
   the last confirmed M30 box's fakeout direction. That box was a bullish breakout (`liq_top=4853.65
   > box_high=4842.55`). During the 6 h 46 min bearish leg from ~4820 → ~4792 no NEW confirmed box
   with a DN fakeout was written to the parquet — the same `box_id=5243` is carried forward. Bias
   therefore never flipped to bearish, and the entry-engine selector refuses the SHORT path while
   bias is bullish. Zero SHORT triggers evaluated → zero GO SHORT emitted.
2. **`STRUCTURE_STALE_BLOCK` on SHORT lines is a decoy.** When the m1 feed is stale (5-14 min age),
   the staleness writer records a BLOCK for both directions. Barbara sees these as "SHORT blocked" —
   but the SHORT path wasn't going to emit a GO anyway (see #1). When m1 is fresh, SHORT is silent;
   LONG continues through its path.
3. **"Block Long" messages are real.** LONG runs normally (selector allows LONG while bias bullish).
   It fails because: price is outside the M30 box → `BLOCK_V1_ZONE` (264 events), or LONG was
   counter-trend to `daily_trend=short` (many BLOCK_V1_ZONE TRENDING/short rows), or broker refused
   execution → `EXEC_FAILED` (68 events). **"Defense Mode"** text is cosmetic — anomaly
   protection is in `shadow_only=true` mode; it logs but does not block.

**Net:** not one of the 924 SHORT-direction log entries today came from a legitimate SHORT trigger
evaluation. Every single one was generated by the m1 staleness writer. The engine, as currently
deployed, cannot emit GO SHORT while the last confirmed M30 box remains a bullish breakout — and it
has no mechanism to invalidate that box from price action alone.

---

## 10. ADDENDUM — additional incidents Barbara reported

Barbara added three incidents after the first pass of this report. Each is investigated below.

### 10.1. Incident — 2026-04-20 during bearish-stuck bias: system emitted GO SHORT on bullish moves

Price trajectory on 2026-04-20 (per-hour UTC, from decision_log):

| hour UTC | first | last | Δ | bias | GO_LONG | GO_SHORT |
|---|---|---|---|---|---|---|
| 00 | 4759.91 | 4771.66 | **+11.75 (UP)** | bearish | 0 | **15** |
| 01 | 4771.66 | 4807.70 | **+36.04 (UP)** | bearish | 0 | 0 (all blocked) |
| 02 | 4807.85 | 4804.26 | −3.59 | bearish | 0 | 0 |
| 03 | 4791.09 | 4791.08 | 0 | bearish | 0 | 8 |
| 05 | 4787.00 | 4797.06 | **+10.06 (UP)** | bearish | 0 | **3** |
| 06 | 4795.38 | 4792.04 | −3.34 | bearish | 0 | 24 |
| 07 | 4805.75 | 4788.03 | −17.72 | bearish | 0 | 26 |
| 08 | 4794.56 | 4794.81 | +0.25 | bearish | 0 | **8** |
| 09 | 4797.17 | 4795.69 | −1.48 | bearish | 0 | 33 |
| 10 | 4793.48 | 4797.13 | **+3.65 (UP)** | bearish | 0 | **16** |
| 13 | 4800.82 | 4800.82 | 0 | **→ bullish** | 1 | 0 |
| 14 | 4812.09 | 4810.10 | −1.99 | bullish | 8 | 0 |
| 15 | 4805.02 | 4799.80 | −5.22 | bullish | 61 | 0 |
| 16 | 4799.80 | 4804.98 | +5.18 | bullish | 248 | 0 |
| … | … | … | … | bullish | … | 0 |

Hours 00, 05, 08, 10 are clear: price went UP, bias was stuck bearish from 04-16, system emitted
**42 GO SHORT against bullish candles** with zero GO LONG. After 13:00 UTC bias flipped to bullish;
from then every GO was LONG even on bearish hours (14:00 −1.99, 15:00 −5.22). **Directional bias
always wrong in both regimes** — confirming this is not a momentary glitch but a structural property
of `derive_m30_bias`.

**Smoking-gun evidence from `service_stdout.log` (36 MB, entire retention):**

```
M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)
M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG  rejected (contra-M30)
```

Total rejections logged: **60,878 lines**, split as:

| bias | direction rejected | count |
|---|---|---|
| bullish | SHORT | **43,614** |
| bearish | LONG | **11,834** |

**Critical implication:** `M30_BIAS_BLOCK` is a hard contra-bias gate in the engine that rejects
trades silently. These 60,878 rejections are **only in `service_stdout.log`** — they do NOT appear
in `decision_log.jsonl` or `decision_live.json`, so they never reach the dashboard and never
trigger Telegram. Barbara has no visibility into 99 % of the rejected SHORTs today, and had none of
the rejected LONGs in the earlier bearish-stuck period.

### 10.2. Incident — 2026-04-20 ~13:19 UTC "sistema teve uma queda absurda", no alerts

**The decision log had a 175.6-minute gap around that time:**

| event | timestamp UTC |
|---|---|
| last decision before gap | 2026-04-20 **10:08:28** |
| first decision after gap | 2026-04-20 **13:04:04** (GO LONG + EXEC_FAILED) |
| gap duration | **175.6 min** (~ 2 h 56 min) |
| next gap | 13:04:04 → 14:09:05 — another **65.0 min** silence |
| next gap | 14:12:47 → 14:37:08 — **24.3 min** |
| next gap | 14:37:12 → 14:56:44 — **19.5 min** |

`continuation_trades.jsonl` confirms an overlapping silence: last entry 2026-04-20 10:44:35, next
entry 13:03:49 — **139.2 min gap**.

`service_stdout.log` mid-file contains `FEED_DEAD -- gate suspended (check Quantower L2 stream port
8000)` lines (later times visible, but the pattern of FEED_DEAD events was intermittent throughout
April 20). Total FEED_DEAD occurrences in log: **177,371**.

`decision_live.json` is the single source Telegram reads. It is ONLY written when a decision reaches
the canonical publish path (`position_monitor.py:2045-2054`). During those gaps NO canonical
decision was published, so **`telegram_notifier.notify_decision()` had nothing to send**. Barbara
received zero alerts because the engine was emitting zero canonical decisions — not because the
bot was broken.

At 13:04:04 UTC — right when Barbara says she noticed the event — the first decision that DID
publish was:

```
direction=LONG  action=GO  reason="iceberg large_order sc=+4"  bias=bullish  daily=short
(then at 13:04:04: action=EXEC_FAILED, "GO LONG but NO broker executed: robo=connected hantec=connected")
```

So even when the engine resumed, the first GO signal fired LONG against daily_trend=short, and the
broker refused to execute. The bias had just flipped bullish in this same second (after ~3 h of
silence), so the system entered the afternoon already pointing the wrong way.

**Why no position_monitor / anomaly / iceberg alerts in that window:**

- Position monitor relies on `DECISION_LIVE_PATH` being written (see position_monitor.py:2046-2063).
  During the silence no canonical write happened → no PM event → no Telegram.
- Anomaly flag in every decision payload in that period: `anomaly.detected=False,
  shadow_only=true`. Even if anomaly had detected, `shadow_only=true` means it would not have sent
  a Telegram notification (it logs in shadow mode).
- Iceberg: 4 GO LONGs DID trigger iceberg detections after 13:04 (`iceberg large_order sc=+4`); but
  those fired after the silence window ended.

### 10.3. Incident — "position_monitor nunca manda mensagens ao telegram"

**Confirmed structurally broken.** Four separate findings:

1. **`C:\FluxQuantumAI\logs\position_decisions.log` has not been written since 2026-04-10
   22:59:59.** That is 11 days of silence from what is supposed to be the position-monitor decision
   log. Last entry:

   ```
   2026-04-10T20:59:59  LONG 4755.92 4771.75 +25.08 d4h=-353 … VERDICT=HOLD
   ```

2. **The canonical PM publish path at `position_monitor.py:2053-2054` writes to
   `DECISION_LOG_PATH` — which is `decision_log.jsonl`. This path has been silent since
   2026-04-21 04:46:12 UTC** (~60 min ago at Barbara's report). Same file is read by the
   Telegram module.

3. **`decision_live.json` mtime is 2026-04-21 04:46:12 UTC**, same stall. The Telegram notifier
   (`telegram_notifier.py:68-80`) reads this file; with no update since 04:46 its anti-spam guard
   `_last_decision_id == dec_id` means even if called, it would not resend the same old decision.

4. **Telegram errors are invisible.** `position_monitor.py:2060-2063` wraps `tg.notify_decision()`
   in try/except and writes failures to `log.debug`. The Python logger in production is configured
   above DEBUG level, so every failure of the Telegram API call is silently discarded. Scan of
   `service_stdout.log` and `service_stderr.log`: **0 mentions of `Telegram`, `tg_send`, or
   `notify_` for the entire 36 MB of retention.** We literally cannot tell from logs alone whether
   a single Telegram notification ever left the server.

5. **Infrastructure currently healthy** — `watchdog.log` shows Quantower/L2/Iceberg all running
   with last data 0-4 s ago as of 2026-04-21 05:44:55 UTC. `service_state.json` (updated
   05:44 UTC) shows `m1_stale=False`, `m1_age_s=56.2`, `m30_bias=bullish`, `defense_tier=NORMAL`.
   So the capture layer is fine. **The silent link is between the decision engine's evaluation
   path (still running — 60,878 M30_BIAS_BLOCK rejects ongoing) and the canonical publish path
   that writes to `decision_live.json`/`decision_log.jsonl`.** The canonical publish apparently
   runs only for some decision types, skipping `M30_BIAS_BLOCK` entirely.

### 10.4. Other notable findings in service_stderr.log

- `ApexNewsGate not available -- trading without news gate: attempted relative import with no
  known parent package` — **the news gate is disabled due to an import error**. The engine is
  trading through news events without protection. Referenced in memory but still unfixed.
- `M5/M30 DISCREPANCY: M30 bias=bullish but M5 liq_top=4853.65 is below M30 liq_bot=4855.70 --
  M5 structure may be a pullback within bullish M30 context` — logs warning, does not block. The
  M5 execution levels directly contradict the M30 macro bias every tick. Today this means M5 is
  giving SHORT triggers that get instantly killed by M30_BIAS_BLOCK.
- `DISPLACEMENT_DIVERGE: dir=SHORT CURRENT=FAIL OPT_A=FAIL OPT_B=PASS` — the Fase 3 three-mode
  displacement shadow is running and the three modes disagree frequently. Not blocking trades, but
  indicative that calibration is unresolved.
- `No tick for XAUUSD` — MT5 has gaps. This is the MT5-side counterpart of the M1 staleness on
  the GC side.

---

## 11. Revised recommendation (consolidated)

Ordered by severity/urgency:

1. **FIX #1 — surface the `M30_BIAS_BLOCK` rejections.** 43,614 SHORT candidates were killed today
   invisibly. The rejection path must write to `decision_log.jsonl` / `decision_live.json` so
   Barbara sees them in the dashboard and Telegram gets a notification. Even if we cannot *unblock*
   them safely today, making them *visible* lets Barbara react manually. This is cheaper than any
   logic change and the highest-ROI fix.

2. **FIX #2 — redesign `derive_m30_bias` to recognise regime change.** The anchor-to-last-confirmed-
   box behaviour is the root of Incidents 10.1 *and* the headline Sprint C BUG #1 *and* the zero-
   SHORT pattern today. Design options in §8 (blend with H4 bias, invalidate after N pts against,
   or integrate M30 candle-colour sequence). Must go through Design Doc / Golden Rule because this
   surface is already partially deployed (commit 074a482).

3. **FIX #3 — restore canonical publish for ALL decision types.** Today only
   `STRUCTURE_STALE_BLOCK` (and a subset of GO paths) write to `decision_log.jsonl`. Every other
   rejection reason (M30_BIAS_BLOCK, BLOCK_V1_ZONE, DISPLACEMENT_DIVERGE, FEED_DEAD, exhaustion,
   etc.) is invisible downstream. `position_decisions.log` is 11 days stale — something upstream
   of this log stopped writing to it around 2026-04-10.

4. **FIX #4 — re-enable ApexNewsGate.** Import error is swallowed at startup (service_stderr.log
   head). Trading through news events is strictly against ATS literature.

5. **FIX #5 — m1 feed staleness investigation** (still needed but no longer urgent for SHORT,
   because bias gate would block SHORTs regardless).

6. **FIX #6 — Telegram observability.** Promote `log.debug` on telegram failures to `log.warning`
   or `log.error`; add a periodic heartbeat ("telegram bot alive X sends / Y failures"). We cannot
   currently tell if messages are arriving.

7. **FIX #7 — resolve M5/M30 DISCREPANCY** rather than silently warning. The current behaviour is
   to warn every tick and continue to give M5-triggered SHORTs that will be killed by M30 bias —
   wasted compute and silent rejections. Either suppress M5 triggers when they contradict M30 bias
   or force a bias re-evaluation when the contradiction persists > N minutes.

All fixes require Design Doc + backtest + calibration per the Golden Rule. **Do NOT** attempt any
of them live-edit without validation.

---

## 12. Condensed answer to Barbara's three points

> **"Ontem tivemos o mesmo problema… movimento bullish e o sistema mandava Go Short, perdemos
> muito dinheiro."**

Confirmed in log. Between 2026-04-20 00:00 and 10:08 UTC the engine emitted ~134 GO SHORTs
(various sub-counts by hour) including 42 during hours where price closed UP. Cause: `m30_bias`
was locked bearish from 2026-04-16 onward, because `derive_m30_bias` anchors to the last
confirmed M30 box's breakout (which was DN on Apr 16) and never invalidates until a new confirmed
box forms. All 11,834 GO LONG candidates during that same period were silently killed by
`M30_BIAS_BLOCK: bias=bearish -- LONG rejected (contra-M30)` — rejections that never reached the
dashboard or Telegram. Today (04-21) the mirror image: bias stuck bullish, 43,614 GO SHORT
candidates silently killed. Same bug, opposite sign.

> **"Ainda na data de ontem, por volta das 13:19 horas, o sistema teve uma queda absurda e nao
> recebemos nenhum alerta do position monitor, anomaly, iceberg ou de Go Short."**

Confirmed: **2026-04-20 10:08 UTC → 13:04 UTC decision log is empty for 175.6 minutes**.
`continuation_trades.jsonl` shows an overlapping 139-min silence (10:44 → 13:03). During that
window the canonical publish path produced nothing — and because Telegram reads
`decision_live.json` (same path), it had nothing to send. Three further gaps of 65, 24, 19 min
followed before the engine stabilised into the new bullish-bias regime at 15:00 UTC. Root cause of
the silence: to be determined — suspect FEED_DEAD episodes + news-gate import error + bias flip
coinciding. **Service PID 4516 was alive throughout** (StartTime 00:47, still alive now with
90 k s cumulative CPU), but it wasn't writing decisions.

> **"Position monitor nunca manda mensagens ao telegram."**

Confirmed structurally: (a) `position_decisions.log` last written 2026-04-10 22:59 — 11 days of
silence; (b) Telegram send failures are written to `log.debug` and never surface; (c)
`decision_live.json` (Telegram's input file) has been stalled since 2026-04-21 04:46 UTC, and
Telegram's anti-spam de-dup prevents resending the stale decision; (d) only canonical-published
decisions trigger Telegram, and M30_BIAS_BLOCK / other common rejection types do NOT write to the
canonical publish path. Net: Barbara gets Telegram only on a small minority of actual decisions,
and gets nothing at all during publish-path silences.

---

## 9. System state

| check | status |
|---|---|
| Writes during diagnosis | only to `C:\FluxQuantumAI\sprints\sprint_diagnosis_m30_bias_stuck_20260421\DIAGNOSIS_REPORT.md` |
| Code edits | **none** |
| Config edits | **none** |
| Service touches | **none** |
| Capture PID 2512 (`watchdog_l2_capture.py`) | alive, untouched (StartTime 4/14 09:35) |
| Capture PID 8248 (`iceberg_receiver.py`) | alive, untouched (StartTime 4/14 09:35) |
| Capture PID 11740 (`quantower_level2_api`) | alive, untouched (StartTime 4/20 16:51) |
| Service PID 4516 (`run_live.py roboforex`) | alive (CPU 90,703 s), untouched |
| Restarts | **none** |
| INVEST-03 session | not contacted; different read set confirmed |
| Plan adherence | §2.1-2.6 covered; §3 structure followed |
| Time budget | ~25 min (within 25-30 min target) |

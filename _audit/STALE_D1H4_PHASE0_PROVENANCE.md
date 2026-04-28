# STALE-D1H4-001 Phase 0 — Provenance + Diagnosis (READ-ONLY)

**Asana:** 1214284204948063
**Date:** 2026-04-28 ~22:55 CEST / 20:55 UTC
**Author:** Claude Code (Opus 4.7)
**Target:** Phase 1 deploy < 2026-04-29 14:00 UTC (FOMC buffer 4h before 18:00 UTC).
**Pre-flight:** capture services 6376 / 13072 / 20396 invariant; HEAD `7bc57b9`; run_live PID 16512; zero edits to `live/`.

---

## TL;DR

Five facts establish the design space:

1. **The disable was deliberate**, by Barbara on **2026-04-21 10:41 UTC** (commit `3e80ed6`, "Production snapshot before stabilization sprint"). The reasoning is preserved in `run_live.py:762-769`:

   > *"server performance (1GB RAM, 90% CPU). Needs incremental/event-driven redesign. TODO: redesign as incremental updater that only processes new bars."*

2. **The cause is a full rebuild every 5 min** on a 2.2M-row M1 parquet (35 MB). Specifically `_build_m1()` loads the whole history, then `_resample_to_tf()` materialises ~9k H4 bars + ~1.5k D1 bars, then `_detect_boxes()` walks both series re-detecting all boxes from scratch — and writes both parquets atomically. Memory pressure compounded by `m30_updater` reading the same M1 source.

3. **The `gc_d1_boxes.parquet` and `gc_h4_boxes.parquet` are structural outputs**; the LIVE signal consumed by the system is `gc_d1h4_bias.json`. Today only ONE consumer reads the JSON (`event_processor._read_d1h4_bias_shadow()`); it is exposed as a SHADOW field in `service_state.json::d1h4_bias` and the dashboard. No live trade-gating decision currently depends on it.

4. **`derive_h4_bias()` accepts an optional `h4_box_row` parameter** but no production caller currently feeds it, so H4 bias is effectively neutral in the live gate. The Sprint C v2 H4 layer is scaffolded but the writer being disabled means R_H4_3/R_H4_6 never fire.

5. **Operational impact is mostly observability**: Barbara's heartbeat / dashboard show stale `last_closed_d1=2026-04-08`, `last_closed_h4=2026-04-02`. Future tier-aware logic and FASE 4b activation depend on fresh D1/H4 — the fix is correct prep for Super Quarta even though current gates don't strictly require it.

**Recommended Phase 1 design:** **windowed rebuild** — read only the last 30-60 days of M1 (parquet predicate pushdown), full recompute on the bounded tail, write to existing parquets. Bounded memory (~10 MB), bounded CPU (<1 s per cycle), no incremental state machine to maintain. § 5 below.

---

## 1. Code state — `live/d1_h4_updater.py`

| Attribute | Value |
|---|---|
| File path | `C:\FluxQuantumAI\live\d1_h4_updater.py` |
| LoC | 676 |
| Last modified (disk mtime) | 2026-04-17 16:42 |
| Last commit touching file | `3e80ed6` ("Production snapshot before stabilization sprint", 2026-04-21) |
| Status in run_live.py | **DISABLED** at commit `3e80ed6` (2026-04-21 10:41 UTC) |
| Architecture intent (per docstring §32) | "FASE 4a: SHADOW ONLY — zero behavioral change" |
| Cadence (when enabled) | 300 s (5 min); intended SHADOW mode |
| Output 1 | `gc_h4_boxes.parquet` — full H4 history with box state + jac_dir (415 KB) |
| Output 2 | `gc_d1_boxes.parquet` — full D1 history with box state + jac_dir (83 KB) |
| Output 3 | `gc_d1h4_bias.json` — composite bias + freshness metadata (live signal) |

### 1.1 Per-cycle cost model (current full-rebuild)

```text
run_update():
  ① _build_m1()                  — read 2.2M-row M1 parquet (35 MB) + append today's micro
       ↓ pandas DataFrame ~250-400 MB resident
  ② _resample_to_tf(m1, "4h")    — 2.2M → ~9,200 H4 bars
  ③ _resample_to_tf(m1, "1D")    — 2.2M → ~1,500 D1 bars
  ④ _detect_boxes(h4_base, ...)  — O(n × 80) state machine on 9.2k bars
  ⑤ _detect_boxes(d1_base, ...)  — same on 1.5k bars
  ⑥ _derive_jac_dir × 2          — vectorised, fast
  ⑦ _write_parquet_atomic × 2    — tmp + rename
  ⑧ _write_bias_json             — tiny JSON
```

The entire 2020→present M1 history is reloaded, resampled, and box-detected from scratch every 300 s — even though only the last few hours of M1 actually change between cycles. This is the architectural defect. Barbara's diagnosis ("1 GB RAM, 90 % CPU") is consistent with the model: pandas `read_parquet` of 2.2M rows resident as float64 columns ≈ 300 MB; combined with `m30_updater` doing the same and run_live's other threads, 1 GB resident is plausible. The CPU spike comes from the resample + box detection on every cycle.

### 1.2 What is NOT broken

- `_detect_boxes()` itself is fast (O(n) state machine; 9.2k H4 bars walks in < 100 ms).
- `_resample_to_tf()` on a smaller window is fast.
- The atomic-write pattern (`tmp.replace(target)`) is correct.
- The session-boundary alignment (`offset='22h'`) is correct (matches `m30_updater` post-DATA-002 semantics).
- The schema (`H4_OUTPUT_COLS`, `D1_OUTPUT_COLS`) is sound — consumers expect these columns.

The defect is purely the **scope** of work per cycle, not the work itself.

### 1.3 What IS broken (additional to perf)

- **Reads `microstructure_*.csv.gz` mid_price** (line 113-135 `_micro_to_m1`). DATA-002 P1 fix superseded `mid_price` with `trades.price` (`m30_updater.py:72-107`). When d1_h4 was disabled (2026-04-21), DATA-002 had already landed (HEAD `1eda7ab` 2026-04-25 fix); but d1_h4_updater never absorbed the fix. **If we re-enable as-is we restore the silent corruption fixed by DATA-002.** The Phase 1 design must read `trades_*.csv.gz` instead.

---

## 2. Disable provenance

| Question | Answer |
|---|---|
| Who disabled it? | Barbara Feijen (commit `3e80ed6`) |
| When? | 2026-04-21 10:41 UTC |
| Why? | Comment in run_live.py:762: *"server performance (1 GB RAM, 90 % CPU). Needs incremental/event-driven redesign."* |
| What replaced it? | Nothing — the heartbeat continues to read the stale JSON written by the last successful run (~2026-04-08 D1, ~2026-04-02 H4) |
| Is the file ever re-enabled in any other branch / config? | No — single repo branch; all consumers see disabled state |
| Is there a forensic doc / Asana for the original outage? | No detailed forensic doc found. The comment + Barbara's recall (1 GB RAM, 90 % CPU) is the only record |

The disable was a **resource-pressure fix**, not a correctness fix. The updater's logic was working but starving the production process. Re-enable at the same scale would reproduce the issue.

---

## 3. Consumer mapping

### 3.1 Live consumers (production)

| Consumer | What it reads | How |
|---|---|---|
| `event_processor.py:920::_read_d1h4_bias_shadow()` | `C:/FluxQuantumAI/logs/gc_d1h4_bias.json` | Best-effort try/except; falls back to `{"d1_jac_dir":"?", ...}` placeholder if missing |
| `event_processor.py:1060::"d1h4_bias"` field in service_state | output of `_read_d1h4_bias_shadow()` | Surfaced to dashboard + heartbeat |
| `level_detector.py:721::derive_h4_bias()` | takes optional `h4_box_row` param; **no production caller passes it** | scaffolded for future activation |

### 3.2 Verified non-consumers

- `m30_updater.py` — does NOT read D1/H4 parquets (only writes `gc_m30_boxes.parquet`)
- `position_monitor.py` — does NOT read D1/H4
- `hedge_manager.py` — does NOT read D1/H4
- `_get_daily_trend()` and Phase 2a tier signals A/B/C — compute D1 *internally* by resampling `gc_m30_boxes.parquet`; do NOT read `gc_d1_boxes.parquet`
- Dashboard `base_dashboard_server.py` — reads `service_state.json::d1h4_bias` (transitively)
- `decision_log.jsonl` consumers — broker-agnostic; D1H4 not part of the schema

### 3.3 Operational impact assessment

The d1h4_bias is currently **diagnostic / dashboard only**:
- Stale JSON → stale dashboard tile (cosmetic)
- Stale JSON → stale `last_closed_d1` / `last_closed_h4` in heartbeat (cosmetic, but eroded operator trust)
- **No live gate decision is currently impacted.** Barbara's narrative that "stale D1 caused 79 % unknown daily_trend" is mechanically incorrect — daily_trend's B+C ensemble computes from `gc_m30_boxes.parquet` (fresh, 60 s cadence), not from the disabled d1h4 outputs. The 79 % unknown is genuinely the Q2 2026 regime collapse documented in `_audit/TREND_CLASSIFIER_V2_VALIDATION.md`.

**However:**
- For Super Quarta volatility, having FRESH D1/H4 is robust prep even if no current gate uses it (FASE 4b activation requires it; future tier-aware refinements may want it; observability is a real value).
- DATA-002 silent-corruption mid-fix (mid_price → trades.price) needs to be applied before re-enable.
- Restoring observability to the dashboard is a tangible Barbara-experienced improvement.

So **the task is correct — fix the updater, restore observability, prep for FASE 4b, eliminate operator confusion**. The motivation framing should be observability + future-proofing, not "current gate decisions are broken".

---

## 4. Parquet + JSON state audit

| File | Path | Last write | Size | Stale by |
|---|---|---|---:|---|
| `gc_d1_boxes.parquet` | `C:/data/processed/` | 2026-04-14 14:05 UTC | 84 KB | **14 days** |
| `gc_h4_boxes.parquet` | `C:/data/processed/` | 2026-04-14 14:12 UTC | 421 KB | **14 days** |
| `gc_d1h4_bias.json` | `C:/FluxQuantumAI/logs/` | (read but not in `ls` output above) | small | (per Barbara's heartbeat: `last_closed_d1=2026-04-08`, `last_closed_h4=2026-04-02`) |
| **Source** `gc_ohlcv_l2_joined.parquet` | `C:/data/processed/` | **2026-04-28 22:52 CEST** (live, m30_updater writes it) | 35 MB | **fresh** (≤ 1 min) |

The source M1 IS fresh (m30_updater keeps it current per the 60s cadence). So the design only needs to consume the existing source — no upstream dependency to fix.

---

## 5. Phase 1 design recommendation — **windowed rebuild**

### 5.1 Why "windowed" instead of true incremental

A true incremental design (persist box-detection state machine cursor, only process new M1 bars, append-and-dedupe parquets) is correct but **complex**:
- Box state machine has 7 mutable variables (`cur_liq_top`, `cur_box_id`, `box_counter`, etc.) that need persistence
- Edge case: D1 bar boundary crossings invalidate downstream tail
- Edge case: late-arriving M1 bars (rare but possible) need tail-rewind logic
- Risk: incremental bugs are subtle; under FOMC volatility a wrong-state-resume could produce wrong bias
- Build time: 2-3 hours for design + 4-6 hours for safe implementation = misses deadline

**Windowed rebuild** is simpler and bounded:
- Read M1 with date filter `ts >= today - 60 days` (parquet predicate pushdown via pyarrow filters → reads only matching row groups, not full 35 MB)
- Resample bounded window → ~60 D1 bars, ~360 H4 bars
- Full box detection on the bounded tail (< 100 ms)
- Atomic write of the existing parquets (full content, but bounded)
- 60-day window is enough to:
  - Capture last ~50 D1 bars for box state continuity (prior boxes may have established active state)
  - Cover the JAC_WAIT lookback (D1: 20 bars × 1 day = 20 days; H4: 30 bars × 4h = 5 days; both fit in 60 days)
- Memory: ~10 MB resident; CPU < 1 s per cycle; satisfies 1 GB / 90 % constraint by 30-100×

### 5.2 Sketch (for Phase 1 design doc)

```python
# Phase 1 implementation skeleton

WINDOW_DAYS = 60   # enough for box state continuity + JAC_WAIT lookback

def _build_m1_windowed() -> pd.DataFrame:
    """Read only the last WINDOW_DAYS of M1 from gc_ohlcv_l2_joined.parquet.
    Uses pyarrow predicate pushdown — does NOT load the full 35 MB."""
    import pyarrow.parquet as pq
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=WINDOW_DAYS)
    table = pq.read_table(M1_PATH,
                          columns=["open", "high", "low", "close", "volume"],
                          filters=[("timestamp", ">=", cutoff)])
    hist = table.to_pandas()
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize("UTC")

    # Append today's trades.price (DATA-002 P1 fix) — NOT mid_price
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades_path = MICRO_DIR / f"trades_{today}.csv.gz"
    m1_today = _trades_to_m1(trades_path)         # NEW — copy from m30_updater
    if m1_today is not None and not m1_today.empty:
        last_hist_ts = hist.index[-1]
        m1_today = m1_today[m1_today.index > last_hist_ts]
        if not m1_today.empty:
            combined = pd.concat([hist, m1_today]).sort_index()
            combined = combined[~combined.index.duplicated(keep="first")]
            return combined
    return hist

def run_update() -> dict:
    # Step 1: bounded M1
    m1 = _build_m1_windowed()
    # Steps 2-8: as before, but on bounded series
    # ...
```

### 5.3 Edge cases to handle in Phase 1

| Case | Handling |
|---|---|
| Cold start (no prior parquets) | Backfill — extend WINDOW_DAYS to 365 for first run, then revert |
| Box detected mid-way through window (not at start) | Box detection state machine handles naturally — at window start, first MIN_BARS+14 are skipped; this matches the disabled-era behaviour |
| Late-arriving M1 bars | Drop duplicates after concat (`~index.duplicated(keep="first")` — already in code) |
| `trades_*.csv.gz` missing today (e.g., weekend) | Fall back to historical only; updater logs warning; bias still written from latest closed bars |
| `pyarrow.filters=` not supported on older pandas | pin pyarrow ≥ 6.0 (already installed via existing parquet usage) |
| First-run after STALE-D1H4 deploy: backfill 60 days, then proceed normally | One-time cold start cost ≈ same as historical full rebuild for window — bounded |

### 5.4 DATA-002 mid_price → trades.price fix carried forward

Phase 1 MUST replace `_micro_to_m1` (reads `mid_price` from `microstructure_*.csv.gz`) with `_trades_to_m1` (reads `price` from `trades_*.csv.gz`), copying the implementation from `m30_updater.py:72-107`. This eliminates the silent-corruption regression that DATA-002 P1 fixed and ensures D1/H4 bars match M30 bars byte-for-byte.

### 5.5 Boot integration (Phase 1)

```python
# run_live.py — un-comment lines 765-769:
if not args.no_updaters and _start_d1h4_updater is not None:
    _get_dt = lambda: getattr(processor, "daily_trend", "unknown")
    _start_d1h4_updater(get_daily_trend_fn=_get_dt)
    print(_color("D1H4 Updater started (300s cadence, SHADOW MODE)", _CYAN))
# Remove the DISABLED warning line.
```

Cadence proposal: **start at 300 s (5 min)** to match the existing pattern. After 24h post-deploy with stable resource use, can lower to 60 s (matching m30) if Barbara wants tighter freshness.

---

## 6. Acceptance criteria — readiness check

Per Asana spec:

| Criterion | Phase 0 readiness |
|---|---|
| Investigação documentada (perf root cause + commit history) | ✅ § 1.1 + § 2 |
| Design incremental specificado em `_audit/design/D1H4_UPDATER_INCREMENTAL.md` | ⏸ Phase 1a deliverable (not yet) |
| `live/d1h4_updater.py` reescrito | ⏸ Phase 1b |
| Boot integration: updater starts as daemon thread no run_live.py | ⏸ Phase 1b — un-comment 4 lines per § 5.5 |
| `gc_d1_boxes.parquet` last write < 2 min after start | ⏸ Phase 1c validation target |
| `gc_h4_boxes.parquet` last write < 2 min after start | ⏸ Phase 1c |
| Backfill: D1/H4 bars 2026-04-09 → 2026-04-28 processados | ⏸ Phase 1c validation; first cycle covers full WINDOW_DAYS=60 |
| Heartbeat reflects fresh `last_closed_d1` + `last_closed_h4` | ⏸ Phase 1c |
| Perf: cycle time < 5 s | ⏸ Phase 1c — projected < 1 s on bounded window |
| Local commit `fix/xau-mid-population`, NO push | ⏸ Phase 1b |
| Zero modifications to capture services 6376/13072/20396 | ✅ Bridge does not interact with capture layer |
| Brief-back per G-PREMISE-AUDIT format | ⏸ Phase 1c |

---

## 7. Open questions for ML-DS approval before Phase 1

**Q1.** **Window size — 60 days or different?** I propose 60 days because:
- D1 JAC_WAIT = 20 bars × 1 day = 20 days lookback minimum
- H4 JAC_WAIT = 30 bars × 4h = 5 days lookback minimum
- Box state continuity wants ≥ 30 days for robust active-box detection
- 60 days = comfortable buffer × 2

Alternative: 90 days (more conservative). Trade-off: doubles M1 read cost (~20 MB → ~30 MB after pyarrow predicate pushdown), still bounded.

**Q2.** **Cadence — 300 s or 60 s?**
- 300 s preserves the original design intent and matches the disabled-era cadence
- 60 s would match m30_updater for consistency
- I propose **300 s for first deploy** (most conservative; falls within "less load than was the issue"); revisit after 24h validation

**Q3.** **`derive_h4_bias` activation** (out-of-scope for STALE-D1H4-001 strictly, but adjacent)
- Once parquets are fresh, should EP start passing `h4_box_row` to `derive_h4_bias`?
- This would activate Sprint C v2 R_H4_3/R_H4_6 rules
- I recommend **NO for this task** — keep the fix focused on observability + freshness; H4-bias-activation is a separate decision

**Q4.** **Cold-start backfill duration**
- For the very first cycle after deploy, do we backfill 60 days (window default) or longer (e.g., 365 days for richer JAC history)?
- I propose **60 days** — sufficient for active-box state, simpler, cycle time < 5 s
- Longer backfill is one-time and could be done by a manual `python live/d1_h4_updater.py --once` if Barbara wants richer history

**Q5.** **Restart authorization**
- Phase 1 implementation requires run_live restart to activate the daemon thread
- Per Barbara's directive (2026-04-28 22:30 UTC) "quero D1 fresh" — restart is implicitly authorized
- I will execute the restart with the standard pre/post sanity protocol; capture services preserved

---

## 8. Premises (G-PREMISE-AUDIT)

### 8.1 Inherited from spec

| Premise | Status |
|---|---|
| `live/d1_h4_updater.py` exists in current codebase | **VALIDATED** (676 LoC, last touched 2026-04-21 commit) |
| Updater is currently disabled in `run_live.py` | **VALIDATED** (lines 762-769) |
| Disable reason was performance (1 GB / 90 % CPU) | **VALIDATED** by `git blame` line 762 |
| Stale outputs cause operator confusion | **VALIDATED** (parquets 14 days stale; JSON 20+ days stale) |
| Stale outputs cause GATE DECISION harm | **OVERRIDDEN** — only diagnostic / dashboard impact today; no live gate uses d1h4_bias.json |
| Capture services 6376/13072/20396 must not be touched | **VALIDATED** (Bridge / updater never interact with capture) |
| FOMC tomorrow at 18:00 UTC | TIME-BOXED — deploy target < 14:00 UTC for 4h buffer |

### 8.2 Created premises (will impact Phase 1 design)

| Premise | Impact |
|---|---|
| 60-day windowed rebuild ≈ 1/30th the memory of full rebuild | Phase 1 cycle CPU/RAM should fit easily |
| pyarrow filter pushdown works on `gc_ohlcv_l2_joined.parquet` | Phase 1 must verify; fallback = read full + slice (still < 35 MB resident) |
| DATA-002 P1 fix (`mid_price` → `trades.price`) MUST be applied | Phase 1 copies `_trades_to_m1` from `m30_updater.py:72-107` |
| 300 s cadence remains the right baseline | Revisit after 24h post-deploy data |
| First post-deploy cycle does the full 60-day backfill | One-time ~3-5 s cost; subsequent cycles < 1 s |
| Re-enable does NOT trigger any consumer behaviour change | True today (shadow only); future tier/FASE 4b activation is out of scope |

---

## 9. PRAC

- **Confirmation bias** — I expected to find the disable was caused by a bug; the data shows it was deliberate Barbara fix to a real resource issue. The Phase 1 fix is correctly framed as "right cadence + bounded scope", not "remove a regression".
- **Premise correction** — Barbara's framing in the comment ("stale D1H4 caused 79 % daily_trend unknown") is **mechanically incorrect** — daily_trend's B+C ensemble reads M30, not D1H4. The 79 % unknown is real but caused by Q2 2026 regime change (`_audit/TREND_CLASSIFIER_V2_VALIDATION.md` § C2/C4) — not by stale D1/H4. The fix is still correct (observability matters; FASE 4b future) but the immediate-gate-impact narrative is overclaim. § 3.3 documents the correction transparently.
- **Sample-size honesty (n/a)** — This is infrastructure, not statistical validation.
- **Scope discipline** — Read-only Phase 0; zero `live/` edits; one audit doc (this file). No commits.
- **Risk: DATA-002 silent regression on re-enable** — Phase 1 MUST apply the `trades.price` fix. Flagged in § 1.3, § 5.4, § 8.2.
- **Risk: cold-start memory spike** — first cycle does 60 days of backfill in one shot. Should still fit in budget but profile during Phase 1c.

---

## 10. Recommendation summary

**Phase 1 design (proposed):** windowed full rebuild, 60-day window, pyarrow predicate pushdown, DATA-002 trades.price fix carried forward. Cycle target < 1 s; memory < 50 MB; cadence 300 s.

**Phase 1 deliverables (in order):**
1. `_audit/design/D1H4_UPDATER_INCREMENTAL.md` — design doc (~30 min, Phase 1a)
2. `live/d1_h4_updater.py` rewrite — `_build_m1_windowed` + `_trades_to_m1` carryover from m30_updater (~60-90 min, Phase 1b)
3. `run_live.py` un-comment + remove disabled warning (~5 min, Phase 1b)
4. Restart + sanity (~30 min, Phase 1c)
5. Brief-back consolidado

**Plan ClaudeCode: zero in-progress** ✅

---

## 11. Deliverables (Phase 0)

- ✅ `_audit/STALE_D1H4_PHASE0_PROVENANCE.md` — this doc (11 sections + premise audit + PRAC)
- 🛑 **GATE — awaiting ML-DS / Barbara approval of Phase 1 design (Q1-Q5 in § 7) before Phase 1a doc writing.**

If Q1-Q5 answers are: Q1=60d, Q2=300s, Q3=defer h4 activation, Q4=60d backfill, Q5=restart pre-authorized, then Phase 1 starts immediately and total time-to-deploy ≈ 2.5-3 h from now (well before the 14:00 UTC deadline).

# D1H4 Updater — Windowed Rebuild Design

**Asana:** STALE-D1H4-001 (1214284204948063), Phase 1a
**Date:** 2026-04-28 (UTC)
**Author:** Claude Code (Opus 4.7)
**Predecessor:** `_audit/STALE_D1H4_PHASE0_PROVENANCE.md`
**Approved Q1-Q5:** 60d window / 300s cadence / defer h4 activation / 60d cold-start backfill / restart pre-approved
**Premise correction acknowledged:** this is observability + DATA-002 fix, NOT a silver bullet for 79 % daily_trend=unknown (which is regime, not stale D1H4).

---

## 1. Goal

Restore `gc_d1_boxes.parquet`, `gc_h4_boxes.parquet`, and `gc_d1h4_bias.json` to live freshness (≤ 5 min staleness) without reproducing the 1 GB RAM / 90 % CPU resource regression that caused Barbara to disable the updater on `2026-04-21`.

## 2. Non-goals (explicitly out of scope)

- Activating `derive_h4_bias` in the live gate (Q3 deferred)
- Changing any consumer behaviour (still SHADOW only — `_read_d1h4_bias_shadow` is the sole live consumer)
- Migrating cadence below 300 s (revisit after 24 h post-deploy data)
- True incremental box-state-machine persistence (windowed full rebuild is sufficient and simpler)
- Touching `m30_updater.py` (independent — already fresh per DATA-002 fix)

## 3. Design contract

| Invariant | Mechanism |
|---|---|
| Memory ≤ 50 MB resident per cycle | 60-day window via pyarrow predicate pushdown |
| CPU < 1 s steady-state cycle | Bounded series (~360 H4, ~60 D1) for box detection |
| Cold-start cycle < 5 s | First cycle does the same 60-day backfill in one shot |
| OHLC source = `trades_*.csv.gz`'s `price` column | Carryover `_trades_to_m1` from `m30_updater.py:72-107` (DATA-002 P1 fix) |
| Atomic parquet writes | Existing `_write_parquet_atomic` (tmp + rename, 3 retries) |
| Capture services 6376 / 13072 / 20396 invariant | Updater never reads/writes `:8000` or `:8002` |
| Daemon-thread architecture | Existing `start()` pattern; main change is `_build_m1_windowed` |

## 4. Per-cycle algorithm

```
run_update():
  ① _build_m1_windowed()                — read M1 from `gc_ohlcv_l2_joined.parquet`
                                          with pyarrow filter `ts >= now - 60d`
                                          + append today's `trades_*.csv.gz`
                                          (NOT microstructure mid_price)
  ② _resample_to_tf(m1, "4h", offset="22h")    — bounded
  ③ _resample_to_tf(m1, "1D", offset="22h")    — bounded
  ④ _detect_boxes(h4_base, max_jac_wait=30, prefix="h4")
  ⑤ _detect_boxes(d1_base, max_jac_wait=20, prefix="d1")
  ⑥ _derive_jac_dir × 2                       — vectorised
  ⑦ _get_last_closed_jac × 2                  — last confirmed bar
  ⑧ compute_bias(d1_jac, h4_jac)              — composite LONG/SHORT × STRONG/WEAK
  ⑨ _write_parquet_atomic × 2                 — H4 + D1
  ⑩ _write_bias_json                          — gc_d1h4_bias.json
```

## 5. `_build_m1_windowed` — pseudocode

```python
WINDOW_DAYS = 60

def _build_m1_windowed() -> pd.DataFrame:
    """Read only the last WINDOW_DAYS of M1 from gc_ohlcv_l2_joined.parquet
    via pyarrow filter pushdown. Append today's trades-derived M1 bars
    (DATA-002 P1: trades.price, NOT microstructure mid_price).
    """
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=WINDOW_DAYS)

    # pyarrow filter — reads only matching row groups (predicate pushdown)
    import pyarrow.parquet as pq
    table = pq.read_table(
        M1_PATH,
        columns=["open", "high", "low", "close", "volume"],
        filters=[("timestamp", ">=", cutoff)],
    )
    hist = table.to_pandas()
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize("UTC")

    # Append today's M1 from trades.price (DATA-002 carryover)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades_path = MICRO_DIR / f"trades_{today}.csv.gz"
    if not trades_path.exists():
        # Fallback for the .fixed.csv.gz convention used by m30_updater
        for suffix in [".fixed.csv.gz"]:
            alt = MICRO_DIR / f"trades_{today}{suffix}"
            if alt.exists():
                trades_path = alt
                break

    m1_today = _trades_to_m1(trades_path) if trades_path.exists() else None

    if m1_today is not None and not m1_today.empty:
        if m1_today.index.tz is None:
            m1_today.index = m1_today.index.tz_localize("UTC")
        last_hist_ts = hist.index[-1] if len(hist) else cutoff
        m1_today = m1_today[m1_today.index > last_hist_ts]
        if not m1_today.empty:
            combined = pd.concat([hist, m1_today]).sort_index()
            combined = combined[~combined.index.duplicated(keep="first")]
            return combined

    return hist


def _trades_to_m1(trades_path: Path) -> pd.DataFrame | None:
    """Direct copy of m30_updater._trades_to_m1 (DATA-002 P1 fix).
    Reconstruct M1 OHLCV from executed-trades tape (not microstructure mid_price)."""
    if not trades_path.exists():
        return None
    try:
        trades = pd.read_csv(
            trades_path,
            usecols=["timestamp", "price", "size"],
            dtype={"price": "float64", "size": "float64"},
        )
        trades["timestamp"] = pd.to_datetime(trades["timestamp"], utc=True)
        trades = trades.dropna(subset=["price", "timestamp"])
        trades = trades.set_index("timestamp").sort_index()
        if trades.empty:
            return None
        m1 = trades["price"].resample("1min").ohlc()
        m1.columns = ["open", "high", "low", "close"]
        m1["volume"] = trades["size"].resample("1min").sum()
        m1 = m1.dropna(subset=["close"])
        return m1
    except Exception as e:
        log.error("_trades_to_m1 failed: %s", e)
        return None
```

## 6. Why 60 days?

| Component | Lookback need | Source |
|---|---|---:|
| D1 `MAX_JAC_WAIT` | 20 D1 bars | `d1_h4_updater.py:82` |
| D1 box state continuity | ≥ 30 days for active-box continuity | empirical buffer |
| H4 `MAX_JAC_WAIT` | 30 H4 bars × 4 h = 5 days | `d1_h4_updater.py:81` |
| H4 hysteresis | 2 H4 bars × 4 h = 8 h | `H4_HYSTERESIS_BARS=2` |
| ATR(14) warmup | 14 bars (D1: 14 days; H4: 56 h) | standard |

60 days = 2× the maximum lookback, providing a comfortable buffer for active-box continuity across regime boundaries. After the first cycle establishes the parquet contents, subsequent cycles' 60-day window is rolling — every cycle covers the same trailing window with the right edge advanced by one cycle's M1 increment.

## 7. Cold-start vs steady-state

### 7.1 Cold start (first cycle after restart)

```text
M1 read     : 60 days × ~22 trading hours/day × 60 min ≈ ~80,000 rows ≈ ~5 MB resident
H4 resample : ~360 H4 bars
D1 resample : ~60 D1 bars
Box detect  : ~80 ms each, 2× = ~200 ms
Parquet IO  : ~50 ms × 2 = ~100 ms
Total cycle : projected ~1-3 s
```

### 7.2 Steady-state (subsequent cycles)

```text
M1 read         : same 60-day window (rolling)
                  pyarrow predicate pushdown re-reads the same row groups,
                  but pandas DataFrame is freshly materialised — no incremental
                  state to maintain. Cost identical to cold start.
Trade.price tail: incremental rows since last cycle (~5 minutes of M1 ≈ 300 rows)
Total cycle     : projected ~1-3 s
```

The "windowed full rebuild" is intentional simplicity. A true incremental design (persist the box-detection state machine cursor across cycles) would shave the ~200 ms detection cost but risk subtle state-machine bugs under FOMC volatility. Trade-off declined per Phase 0 PRAC.

## 8. Edge cases

| Case | Handling |
|---|---|
| `trades_today.csv.gz` missing (e.g., weekend / pre-session) | `_trades_to_m1` returns `None`; `_build_m1_windowed` proceeds with historical only; bias still written |
| `trades_today.fixed.csv.gz` (DATA-002 fixup variant) | Fallback check (`m30_updater.py:134-146` pattern) |
| pyarrow filter not supported (older pyarrow) | Fallback: read full + slice — still reads the same 35 MB file but extracts the tail in pandas. Slower but works. Project pyarrow ≥ 6.0 (already required by other modules). |
| Cold start with no `trades_*.csv.gz` for today | M1 is historical-only; bias derived from last closed bar in history (which IS the live state when nothing has traded today, e.g., early Sunday) |
| Late-arriving M1 bars (rare) | `concat(...)`+ `~index.duplicated(keep="first")` already handles this (existing pattern in d1_h4_updater) |
| ATR warmup at window start | `_detect_boxes` already starts at `MIN_BARS + 14` — first 17 bars effectively skipped. With 60 days × 24 H4 bars/day = ~360 H4 bars, the 17-bar skip is < 5 % of the window. Active-box state at window start ≠ true state, but bias ALWAYS reads from the LAST closed bar (`_get_last_closed_jac`), so the right edge is what matters operationally. |
| Parquet exists from old full-rebuild (pre-disable) | First windowed cycle overwrites; new content is bounded; no migration needed |

## 9. Boot integration

`run_live.py` lines 762-769 currently:

```python
# server performance (1GB RAM, 90% CPU). Needs incremental/event-driven redesign.
# Shadow bias still readable from gc_d1h4_bias.json (last standalone run).
# TODO: redesign as incremental updater that only processes new bars.
# if not args.no_updaters and _start_d1h4_updater is not None:
#     _get_dt = lambda: getattr(processor, "daily_trend", "unknown")
#     _start_d1h4_updater(get_daily_trend_fn=_get_dt)
#     print(_color("D1H4 Updater started (300s cadence, SHADOW MODE)", _CYAN))
print(_color("D1H4 Updater DISABLED (perf issue -- awaiting incremental redesign)", _YELLOW))
```

Phase 1b transformation:

```python
# Windowed rebuild deployed 2026-04-29 per STALE-D1H4-001 (Asana 1214284204948063).
# 60-day pyarrow predicate pushdown + DATA-002 trades.price source.
# Per-cycle CPU < 5s, memory < 50MB. Cadence 300s.
if not args.no_updaters and _start_d1h4_updater is not None:
    _get_dt = lambda: getattr(processor, "daily_trend", "unknown")
    _start_d1h4_updater(get_daily_trend_fn=_get_dt)
    print(_color("D1H4 Updater started (300s cadence, SHADOW MODE)", _CYAN))
else:
    print(_color("D1H4 Updater not started (no_updaters or import failed)", _YELLOW))
```

## 10. Testing strategy

### 10.1 Standalone smoke (Phase 1b.2 acceptance)

```bash
python live/d1_h4_updater.py --once
```

Pass criteria:
- Cycle elapsed < 5 s
- `gc_d1_boxes.parquet` mtime fresh
- `gc_h4_boxes.parquet` mtime fresh
- `gc_d1h4_bias.json` written with `data_freshness.d1_stale=False, h4_stale=False`
- Bias output reflects today's market state (not 2026-04-08 snapshot)

### 10.2 Live restart (Phase 1c)

Pass criteria:
- run_live PID changes (new boot)
- Capture services 6376 / 13072 / 20396 unchanged
- Boot log: `"D1H4 updater thread started"`
- Within 2 minutes: parquets refreshed
- `service_state.json::d1h4_bias` populated with current `bias_direction`, fresh timestamps
- No exceptions in `task_scheduler_smoke.log`
- `gc_d1h4_bias.json::data_freshness.h4_parquet_age_s < 300`, `d1_parquet_age_s < 300`

### 10.3 Existing pytest suite

Must continue to pass — no test currently exercises `d1_h4_updater` directly (verified via grep). The change touches only `_build_m1` + `_micro_to_m1 → _trades_to_m1`, both private helpers.

## 11. Rollback plan

If Phase 1b smoke or Phase 1c restart reveals an issue:

```bash
# Step 1: Revert the local commit
git -C C:/FluxQuantumAI revert HEAD --no-edit

# Step 2: Restart run_live
Stop-ScheduledTask -TaskName "FluxQuantumAPEX_TaskScheduler"
# (force-kill orphan if needed — known Windows quirk)
Start-ScheduledTask -TaskName "FluxQuantumAPEX_TaskScheduler"

# Step 3: System returns to disabled-D1H4 state (pre-fix)
```

No git push to remote (Rule 11) → no externally-visible regression.

## 12. Acceptance criteria (per Asana spec, mapped)

| Asana criterion | Phase 1 stage |
|---|---|
| Investigação documentada (perf root cause + commit history) | ✅ Phase 0 |
| Design incremental specificado em `_audit/design/D1H4_UPDATER_INCREMENTAL.md` | ✅ this doc |
| `live/d1_h4_updater.py` reescrito | ⏸ Phase 1b |
| Boot integration: updater starts as daemon thread | ⏸ Phase 1b (un-comment 4 lines) |
| `gc_d1_boxes.parquet` last write < 2 min after start | ⏸ Phase 1c |
| `gc_h4_boxes.parquet` last write < 2 min after start | ⏸ Phase 1c |
| Backfill: D1/H4 bars 2026-04-09 → 2026-04-28 processados | ⏸ Phase 1c (60-day window covers it) |
| Heartbeat reflects fresh `last_closed_d1` + `last_closed_h4` | ⏸ Phase 1c |
| Perf: cycle time < 5 s | ⏸ Phase 1b.2 standalone smoke |
| Local commit `fix/xau-mid-population`, NO push | ⏸ Phase 1b.3 |
| Zero modifications to capture services | ✅ structurally; no `:8000` / `:8002` interaction |
| Brief-back per G-PREMISE-AUDIT format | ⏸ Phase 1d |

## 13. Premise audit (G-PREMISE-AUDIT)

### 13.1 Inherited from Phase 0 (validated)

| Premise | Status |
|---|---|
| Disable was deliberate (commit `3e80ed6`, 2026-04-21 by Barbara) | ✅ |
| Cause: full rebuild on 2.2M-row M1 every 5 min → 1 GB / 90 % CPU | ✅ |
| Live signal is `gc_d1h4_bias.json`; parquets are structural | ✅ |
| Only consumer: `_read_d1h4_bias_shadow` (shadow / dashboard only) | ✅ |
| `derive_h4_bias` not yet wired in live gate | ✅ (Q3 defer) |
| DATA-002 P1 fix needed on re-enable (mid_price → trades.price) | ✅ |
| Premise correction: stale D1H4 ≠ cause of 79 % unknown daily_trend | ✅ ML-DS acknowledged |

### 13.2 Created in this Phase 1a (impact downstream)

| Premise | Impact |
|---|---|
| 60-day window covers all JAC_WAIT lookbacks + ATR warmup with 2× buffer | If empirical post-deploy shows boundary effects (rare boxes detected at window start that disappear next cycle), increase to 90 days |
| pyarrow `filters=[("timestamp", ">=", cutoff)]` is supported by current installation | Phase 1b.2 smoke test verifies; fallback = full read + pandas slice |
| 300 s cadence is sufficient (matches disabled-era intent) | Q2 endorsed; revisit after 24h |
| Cycle CPU < 1 s steady-state | If empirical > 5 s, profile + optimise (likely culprit: pyarrow filter eviction; extend to 5 min cadence wouldn't help — would need deeper redesign) |
| `trades_today.fixed.csv.gz` fallback adequate for DATA-002 edge cases | Matches m30_updater fallback policy |

## 14. PRAC

- **Confirmation bias** — I assumed pyarrow predicate pushdown would work on the M1 parquet. **Need to verify in Phase 1b.2 smoke** that filters land at row-group level (not full read + filter) on this specific parquet. Fallback is full read + slice; still bounded but loses the memory win.
- **Premise fragility** — 60 days might not be enough if a particularly important active-box was established > 60 days ago (rare). Mitigation: D1H4 bias derives from LAST closed bar (`_get_last_closed_jac`), which IS within the window. Active-box state at window start matters less than right-edge bias.
- **Scope discipline** — Phase 1a is design only; Phase 1b is implementation. No live edits in this stage.
- **DATA-002 silent regression** — High-priority risk per Phase 0 § 1.3. Phase 1b MUST replace `_micro_to_m1` with `_trades_to_m1`. Verifiable via comparing first-cycle output bars against `m30_updater`'s output for overlapping bars (D1 close ≈ aggregated M30 close at session boundary).
- **Concurrent reads** — `m30_updater` and the new windowed `d1_h4_updater` will both read `gc_ohlcv_l2_joined.parquet`. parquet reads are concurrency-safe (mmap-style). No locking needed.
- **Restart timing** — Phase 1c restart will interrupt run_live for ~30-60 s. Capture services preserved. No FOMC volatility yet (deploy < 14:00 UTC, FOMC at 18:00 UTC).

---

## 15. Phase 1a sign-off

✅ Design doc complete. Ready for Phase 1b implementation.

Plan ClaudeCode: 1 in-progress (this), 5 pending (1b → 1d).

Proceeding to Phase 1b immediately.

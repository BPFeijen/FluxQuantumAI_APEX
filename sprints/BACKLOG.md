# FluxQuantumAI — Sprint Backlog

Cross-sprint tracking. Append entries at top (newest first). Each entry has status, problem, impact, owner.

---

## Sprint H4-WRITER-FIX (P1)

**Status:** NEW (discovered 2026-04-20 during Sprint C)
**Problem:** `gc_h4_boxes.parquet` writer stopped at 2026-04-14 14:00 UTC.
**Impact:** Sprint C falls back to resample OHLCV when parquet >6h stale. Resample lacks ATS trend line semantics.
**Owner:** TBD by Barbara

### Context (added during Sprint C Phase 1 discovery)

- Writer module exists: `C:/FluxQuantumAI/live/d1_h4_updater.py`
- Invocation in `run_live.py:785-789` is **commented out** with comment:
  > "DISABLED 2026-04-14: full M1 reload (2.2M rows) every 5min was degrading server performance (1GB RAM, 90% CPU). Needs incremental/event-driven redesign."
- Downstream effects (Sprint C Phase 1):
  - `gc_h4_boxes.parquet` stale 143h (same cutoff)
  - `gc_d1h4_bias.json` stale 143h (same cutoff; writer is the same process)
  - `gc_ats_features_v4.parquet` stale 279h (different issue, related parked pipeline)
- Sprint C v2 works around this via OHLCV resample (primary path). Kept parquet-fallback code in `derive_h4_bias` for when writer is revived.
- When fixed, Sprint C `derive_h4_bias` automatically uses parquet path (fresher + higher confidence R_H4_3/R_H4_6 rules via `h4_jac_dir`).

### Acceptance criteria for fix

- `d1_h4_updater.py` redesigned as incremental (only new bars, not full reload)
- Runs in-process under `run_live.py` without degrading CPU/RAM
- `gc_h4_boxes.parquet` and `gc_d1h4_bias.json` update cadence ≤ 5 minutes
- Health check monitors freshness (alert if >30 min)

### Related sprints

- Sprint C v2 (entry_logic_fix_20260420 / Track C) — implemented resample fallback; will benefit from parquet revival

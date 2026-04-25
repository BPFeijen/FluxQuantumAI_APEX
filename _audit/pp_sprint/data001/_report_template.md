# DATA-001 — Reconnaissance: Parquet vs Raw-Trades Reconciliation

**Date:** 2026-04-24
**Classification:** READ-ONLY reconnaissance. No fix, no pipeline modification, no parquet regeneration.
**Working directory:** `C:\FluxQuantumAI\`
**HEAD baseline:** `ed9b19e` on `fix/xau-mid-population` (unchanged throughout)

---

## Inputs

- `C:\data\level2\_gc_xcec\trades_*.csv.gz` — Quantower live L2 trades (2025-11-26 onwards, 112 files)
- `C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5\glbx-mdp3-*.mbp-10.csv.zst` — Databento MBP-10 backfill (2025-07-01 → 2025-11-24, 126 files; captures multiple GC contracts, we filter `action=='T'` and keep only the trade-count-majority single-month symbol)
- `C:\data\processed\gc_ohlcv_l2_joined.parquet` — 1-min OHLCV joined parquet (derived product)
- `C:\data\processed\gc_m30_boxes.parquet` — M30 boxes (derived, rebuilt every 60s live)
- `C:\data\processed\gc_d1_boxes.parquet` — D1 boxes (derived)

## Artifacts produced (all untracked, under `_audit/pp_sprint/data001/`)

- `tape_inventory.csv` — row/size inventory per source
- `divergence_thresholds_calibration.md` — healthy-subset percentile derivation
- `daily_reconciliation_1min.csv` — per-day deltas 1-min
- `daily_reconciliation_m30.csv` — per-day deltas M30
- `daily_reconciliation_d1.csv` — per-day deltas D1
- `pattern_analysis.md` — Step 5 tables
- `blast_radius.md` — Step 6 consumer impact
- `step1_tape_inventory.py`, `step2_3_reconcile.py`, `step4_m30_d1.py`, `step5_pattern_analysis.py`, `step6_blast_radius.py`, `_reconcile_helpers.py` — scripts
- `_cache_raw_1min/` — per-date 1-min OHLCV parquets rebuilt from tape (cache)

## Sanity (top)

- HEAD: ed9b19e (unchanged)
- Capture services 8000/8002: LISTENING (untouched, netstat read-only probes only)
- Commits during investigation: 0
- Files modified in `live/`: 0
- Files modified anywhere tracked: 0

---

*(sections below filled in after Step 3 data lands)*

## PRAC

## Premises inherited

## Premises created

## Hypothesis table (H1–H5)

## Blast radius statement

## Part A dot impact

## Root cause hypothesis (build pipeline)

## DATA-002 scope recommendation

## D4 unblocking status

## Decisions needed from Barbara

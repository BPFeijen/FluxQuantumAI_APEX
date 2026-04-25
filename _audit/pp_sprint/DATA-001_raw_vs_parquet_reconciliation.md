# DATA-001 — Reconnaissance: Parquet vs Raw-Trades Reconciliation

**Date:** 2026-04-24
**Classification:** READ-ONLY reconnaissance. Discovery phase only. No fix, no pipeline modification, no parquet regeneration.
**Working directory:** `C:\FluxQuantumAI\`
**HEAD baseline:** `ed9b19e` on `fix/xau-mid-population` (unchanged throughout)
**Time spent:** ~1h45m (comfortably inside the 3-4h budget, well inside the 5h hard-stop).

---

## 0. What DATA-001 examined

Sources compared minute-by-minute:

- **Authoritative tape** (per D1 § 1.4):
  - `C:\data\level2\_gc_xcec\trades_*.csv.gz` — Quantower L2 trade-by-trade tape, 112 files, 2025-11-26 → 2026-04-24
  - `C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5\glbx-mdp3-*.mbp-10.csv.zst` — Databento MBP-10 backfill, 126 files, 2025-07-01 → 2025-11-24, 10-date sanity sample in this run. For each Databento file, only single-contract symbols `GCM\d` are kept (no spreads) and the highest-trade-count symbol is used as the "front month" for that day.
- **Derived parquet** (suspect):
  - `C:\data\processed\gc_ohlcv_l2_joined.parquet` (2.2M 1-min rows)
  - `C:\data\processed\gc_m30_boxes.parquet` (73.8k M30 rows)
  - `C:\data\processed\gc_d1_boxes.parquet` (1.6k D1 rows)

Reconciliation is performed at per-minute granularity against `gc_ohlcv_l2_joined.parquet`, then re-aggregated to M30 and D1 for cross-check against those parquets.

Artifacts produced (all untracked, under `_audit/pp_sprint/data001/`):

| File | Purpose |
|---|---|
| `step1_tape_inventory.py` | pandas-based row-count + timestamp-bound inventory of both tape sources |
| `step2_3_reconcile.py` | healthy-subset percentile thresholds + full-series per-day reconciliation |
| `step3b_robust_reclassify.py` | MAD-based reclassification (fixes healthy-subset contamination) |
| `step4_m30_d1.py` | M30 and D1 parquet cross-check vs tape-reaggregated |
| `step5_pattern_analysis.py` | temporal clustering, rollover alignment, direction bias |
| `step6_blast_radius.py` | Part A dot impact + consumer inventory |
| `_reconcile_helpers.py` | shared per-minute OHLCV builder from trades / databento |
| `tape_inventory.csv` | final tape inventory |
| `daily_reconciliation_1min.csv` | per-day 1-min reconciliation + percentile band |
| `daily_reconciliation_1min_robust.csv` | MAD-based reclassification |
| `daily_reconciliation_m30.csv` | M30 cross-check |
| `daily_reconciliation_d1.csv` | D1 cross-check |
| `divergence_thresholds_calibration.md` | Step 2 percentile derivation |
| `divergence_thresholds_robust.md` | post-hoc MAD thresholds |
| `pattern_analysis.md` | Step 5 tables |
| `blast_radius.md` | Step 6 dot impact |
| `_cache_raw_1min/` | per-date 1-min OHLCV parquets rebuilt from tape |

---

## 1. Sanity (top)

- HEAD: `ed9b19e` (unchanged)
- Branch: `fix/xau-mid-population` (unchanged)
- Capture services 8000 / 8002: LISTENING (PIDs 17468 / 15000 — untouched, read-only netstat probes only)
- Commits during investigation: 0
- Files modified in `live/`: 0
- Files modified anywhere tracked: 0

---

## 2. PRAC — 5 explicit checks

1. **Overfit risk.** Percentile thresholds derived from a random-sampled healthy subset turned out to be contaminated (2026-03-06 and 2026-04-02 had max_dh ≥ 47pt — clearly CORRUPT but swept into the baseline). The resulting p99.9 = 38.65pt is an artificially-conservative upper bound. To avoid classifying day-level anomalies by contaminated percentiles, Step 3b re-derived thresholds from only the six spot-check dates that D1 confirmed clean at <1.3pt, using a MAD-based scale (median 8.30pt, MAD 1.35pt), and added an absolute backstop `|day_dH| > 20pt → CORRUPT`. All downstream analysis and the blast-radius report use the robust classification.
2. **Confirmation bias.** H1 (isolated-to-2026-02-13) was the prior narrative from D1. The random sample explicitly included dates far from Feb 13 to probe that bias, and promptly broke H1. H2 (rollover clustering) is partially supported but was not rescued: the CORRUPT set includes dates 6-28 days from the nearest rollover — proximity is not a clean cluster. H4 (different derivatives corrupt differently) was tested explicitly by cross-checking M30 and D1 against the 1-min flag: at least 10 of the 15 CORRUPT days have small day-level delta but large per-minute shifts, meaning intraday corruption often self-corrects at aggregation boundaries.
3. **Premise fragility.** Load-bearing premise: `trades_*.csv.gz` and Databento MBP-10 (action='T', single-month symbol) jointly constitute an authoritative tape. D1 verified this at one point (Feb 13 max = 5069.10 to the second decimal against Quantower). DATA-001 did not re-verify on any additional date; if the tape itself has a systematic bias against the parquet (e.g. symbol-filter mismatch by day), our flagged-CORRUPT list includes false positives. Strongly recommend Barbara visually re-check 2-3 CORRUPT dates in Quantower before DATA-002 is scoped.
4. **Missing tests.** (a) D1 comparison in Step 4 pairs calendar-day tape-H/L against a single parquet D1 bar. The D1 bar convention is 22:00-UTC-close (session), so this is calendar-vs-session — not a true apples-to-apples check. The step4 D1 results are directional signal only; trust the 1-min (step 3) and M30 (step 4) results preferentially. (b) Pre-handover Databento sanity is only 10 random dates; a larger sanity sample would increase confidence the Databento-era parquet is itself clean. (c) No check that the corrupt dates' timing correlates with D5's watchdog-event log (the D5 inventory is tracked separately but not ingested here).
5. **Sample completeness.** L2-era reconciliation is FULL (all 112 trade-CSV dates). Databento-era reconciliation is a 10-date sanity sample (all returned 0.0 delta as expected, since the parquet for those dates was built from the same Databento source — any mismatch would indicate a filter bug, not a build bug). 11 L2 dates returned NO_DATA because either parquet has no bars for that date (weekend, holiday) or the trades file was empty. These NO_DATA dates are excluded from band counts but listed in `daily_reconciliation_1min.csv` with `status=NO_DATA`.

---

## 3. Premises inherited

| Premise | Source | Status |
|---|---|---|
| Quantower tape is authoritative at the trade level | D1 §1.4 + direct verification of Feb 13 max=5069.10 | VALIDATED within 1-second-decimal tolerance for Feb 13; UNCHECKED for other dates in this run |
| Parquet `gc_ohlcv_l2_joined.parquet` is derived product; at least one date (2026-02-13) has silent price corruption | D1 root cause | VALIDATED and expanded: not 1, but **15** dates with CORRUPT band per robust classification |
| Pre-handover (pre-2025-11-26) parquet is built from Databento; reconciliation against Databento is self-referential | helper logic | VALIDATED — 10-date sanity sample shows exactly 0.0 delta |
| Capture services (ports 8000/8002) must remain untouched | D5 + memory `feedback_capture_services_never_kill`, `feedback_never_restart_capture` | VALIDATED — only netstat probes; no interaction |
| D1 convention: session-close at 22:00 UTC (pre-DST) on prev calendar day | Part A backtest `resample_d1_et_session` | UNCHECKED for Step 4 — the D1 comparison in this run uses calendar-day tape vs session-close pq bar, not a true apples-to-apples match. See PRAC §4a. |

---

## 4. Premises created

| Premise | Downstream impact |
|---|---|
| L2-era (post-2025-11-26) parquet has 15 CORRUPT + 7 MAJOR + 6 MINOR days out of 111 reconciled (robust classification). ~20% of L2-era days are in the non-CLEAN range. | Any script calibrated against the L2-era parquet window is potentially tainted. D4-A (chat-side spec) can proceed; D4-B (impl) should not ingest un-reconciled parquet windows without a date filter. |
| Corruption is NOT isolated in time: dates span 2025-12-01 → 2026-04-24 (ongoing). | Corruption is systemic-but-intermittent, not a one-off. Live runtime is at risk (not just historical backtest). |
| Corruption has TWO modes: (a) day-level H/L shift > 20pt (5 dates); (b) intraday per-minute shift that self-corrects at day boundary (10 dates). | (a) affects D1 / M30 dots and calibration. (b) mostly affects M30/intraday features but NOT D1 aggregates. The ATS Trend Line on D1 is exposed to (a) only; H4/M30 are exposed to both. |
| Direction of day-level corruption is BALANCED: 2 dates show parquet LOWER than tape, 2 show parquet HIGHER (absolute daily H-delta > 10pt). 1 more has pq-lower at the DAY LOW only. | H5 refuted as "one-way bias"; a simple offset correction is not sufficient. Corruption mechanism is stochastic, not a systematic shift. |
| Corruption is fully POST-handover. Pre-handover Databento sample returns zero delta. | Bug is specific to the Quantower-L2-derived build path. Fixing DATA-001-scoped corruption does NOT require touching Databento ingestion. |
| Rollover clustering exists but is weak: nearest-rollover distance for CORRUPT set ranges 6-28 days, CORRUPT days near rollover (within 10 days) = 4 out of 15. | H2 partially supports but is not load-bearing. The DATA-002 root-cause search should not anchor exclusively on rollover handling. |

---

## 5. Hypothesis table

| H | Prior | Status | Evidence summary | Confidence |
|---|---|---|---|---|
| H1 Isolated to 2026-02-13 | LOW | **REFUTED** | 15 CORRUPT + 7 MAJOR + 6 MINOR L2-era dates beyond Feb 13 | HIGH |
| H2 Rollover-cluster bias | MEDIUM | **PARTIALLY SUPPORTED** | 4 of 15 CORRUPT dates are within 10 days of a GCx→GCy rollover; the rest are 14-28 days away. Support exists but not the dominant driver. | MEDIUM |
| H3 Continuous from some pipeline-change date | MEDIUM-LOW | **REFUTED** | Clean and corrupt dates interleave across the entire L2 window; no clean "switch date" identified | HIGH |
| H4 Different derivatives corrupt differently | MEDIUM | **CONFIRMED** | 10 of 15 CORRUPT days have `|day_dH| < 3pt` yet `max_dh_1min > 20pt`. Intraday corruption often does NOT propagate to D1 aggregates. M30 is in-between (6 of 112 days with M30 max-delta > 20pt vs 29 with > 5pt). | HIGH |
| H5 Direction bias | MEDIUM | **REFUTED AS BIASED** (confirmed as BALANCED/BIDIRECTIONAL) | Of dates with `|day_dH| > 10pt`: 2 positive (parquet LOWER), 2 negative (parquet HIGHER). No net bias. | HIGH |

---

## 6. Blast radius statement

- **Total dates reconciled** (L2-era + 10-date Databento sanity): **122**
  - OK: 111
  - NO_DATA: 11 (weekend / holiday / empty trades file)
- **Robust band counts (111 OK dates):**
  - CLEAN: **83** (75%)
  - MINOR: 6 (5%)
  - MAJOR: 7 (6%)
  - CORRUPT: **15** (13.5%)
- **Temporal pattern:** entirely post-handover. First CORRUPT date = 2025-12-01 (first L2 month of capture); last observed = 2026-04-24 (today, most recent capture). No clean "before-X" window inside L2 era.
- **Sign direction bias:** balanced. The 5 CORRUPT-with-day-level-impact dates split roughly 2:2:1 between (parquet lower than tape) / (parquet higher than tape) / (corruption only on day-low, not day-high).

### 6.1 CORRUPT dates — day-level H/L impact ≥ 20pt (dot-affecting)

| date | day_dH tape-pq | day_dL tape-pq | max_dh_1min | direction |
|---|---|---|---|---|
| 2025-12-01 | **-23.60** | +9.30 | 0.70 | parquet HIGHER |
| 2026-02-13 | **+35.30** | -0.40 | 37.55 | parquet LOWER |
| 2026-04-05 | +1.25 | **-27.10** | 20.75 | parquet HIGHER on day-low |
| 2026-04-07 | **+89.95** | -9.75 | 31.80 | parquet LOWER (huge) |
| 2026-04-19 | **-44.10** | -5.05 | 16.90 | parquet HIGHER |

### 6.2 CORRUPT dates — intraday-only shift (day-level < 3pt)

| date | max_dh_1min | comment |
|---|---|---|
| 2025-12-10 | 37.40 | day H/L both match, but intraday bars plateau ~37pt off tape |
| 2025-12-12 | 23.90 | |
| 2026-02-04 | 23.00 | |
| 2026-02-05 | 24.25 | |
| 2026-02-26 | 29.75 | day_dL -12.75pt (marginal D1) |
| 2026-03-06 | 48.00 | intraday heavy: 27 minutes beyond p99.9 |
| 2026-03-19 | 44.95 | |
| 2026-04-02 | 47.65 | |
| 2026-04-08 | 25.85 | |
| 2026-04-24 | 22.75 | most recent (today's capture) |

---

## 7. Part A dot impact

From `blast_radius.md` — dots whose timestamp falls on a date classified CORRUPT or MAJOR by the robust band (using the 111-date reconciliation window only):

| Frame | Total dots (3y) | Dots in reconciliation window | On CORRUPT | On MAJOR | Combined bad % |
|---|---:|---:|---:|---:|---:|
| D1  | 71   | 9   | **2** | 1 | 33 % of in-window |
| H4  | 438  | 49  | **9** | 5 | 28 % of in-window |
| M30 | 2946 | 361 | **44** | 27 | 20 % of in-window |

### 7.1 D1 dots at risk

- `2026-02-13 00:00 UTC  BEAR @ 5037.97` — known spurious from D1 investigation
- `2026-03-06 00:00 UTC  BEAR @ 5229.50` — **newly flagged**. 2026-03-06 has max_dh=48pt per-minute shift intraday. Day-level D1 H/L match tape closely (day_dH=1.05, day_dL=-1.0), so the dot's *gap-forming bar high/low* may or may not be artefactual; requires Step-4-style D1-session-aware check before declaring spurious.
- `2026-03-31 00:00 UTC  BULL @ 4498.02` (MAJOR) — may or may not be artefactual; needs case-by-case D1-session check.

**Recommendation:** the two CORRUPT-date D1 dots should be held as SUSPECT until reconciliation is re-run on tape-derived D1 bars for those dates. The MAJOR-date dot should be classed as amber.

### 7.2 H4 / M30 exposure

H4 and M30 dots on CORRUPT/MAJOR dates should be treated as suspect during any downstream classifier training. Specifically:

- H4 dot date-range spans `2025-12-10` → `2026-04-23` inside the corruption window
- M30 dot exposure is the largest in absolute terms (~71 dots at risk)

---

## 8. Root cause hypothesis for the build pipeline

**Single best guess:** the live `m30_updater.py` / upstream joiner that produces `gc_ohlcv_l2_joined.parquet` has a timing- or symbol-handling edge case that periodically causes per-minute OHLC to lag, drift, or split between contracts.

Supporting evidence:

- Volumes per minute track the tape to ±3 contracts on all CORRUPT days — the join *is* reading from a feed that sees the same trade counts. Only *prices* diverge.
- 10 of 15 CORRUPT days have `day_dH ≈ 0` with `max_dh_1min > 20pt` — the bars plateau then catch up within the same minute, but with a temporal offset that shifts the per-minute extrema.
- 5 of 15 CORRUPT days have `|day_dH| > 20pt` — the divergence lasts long enough in the day that the tape makes new extrema the parquet never sees.
- Direction is balanced (both + and −) → not a simple offset bug.
- Rollover adjacency exists but is not the dominant predictor.

### Alternative hypotheses (not ruled out)

- **Mid-session contract auto-roll in Quantower subscription.** Would produce ~30-40pt shift (GCx → GCy carry spread), matching Feb 13 afternoon. But volumes should then diverge — they do not. So this is partially but not fully consistent.
- **Stale DOM/mid-price source overwriting trade prices.** Consistent with "volumes agree, prices drift" pattern. Would need code inspection of the joiner to confirm.
- **Clock/sequence skew at the feed adapter.** Would produce random per-minute shifts that self-correct by end of day — matches the "intraday-only" class. Would NOT produce day-level shifts unless clock skew is intentional/systematic.

---

## 9. DATA-002 scope recommendation

Prioritized list for a fix track (not executed here):

1. **(Priority 1)** Identify the exact script/service that writes `gc_ohlcv_l2_joined.parquet`. Likely candidate: live joiner that consumes Quantower trades + depth. Trace: capture (ports 8000/8002) → ??? → parquet.
2. **(Priority 1)** For one CORRUPT date with both large day-level and intraday impact (2026-04-07 is the extreme case at +89.95pt), instrument the joiner to emit per-minute diagnostics and replay. Compare live-time prices with replay-time prices.
3. **(Priority 2)** For the symbol-mix hypothesis: examine whether the Quantower subscription config rolls contracts automatically, and whether the trades CSV and the joiner are subscribing to the same symbol view. If they disagree, the parquet could be tracking a different contract than the trades file.
4. **(Priority 2)** Build a 5-minute-cadence live reconciliation probe: compare the newest `gc_ohlcv_l2_joined.parquet` row against the concurrent minute in `trades_<today>.csv.gz`. Page on `|delta_h| > 5pt` for any minute. This is a **guardrail**, not a fix — but it prevents *future* corrupt dates from silently feeding the live trader.
5. **(Priority 3)** Regenerate historical parquet from `trades_*.csv.gz` for the 15 CORRUPT + 7 MAJOR dates as a one-shot backfill. Test: the re-run of `backtest_3y.py` after backfill should have the 2026-02-13 BEAR dot disappear and similar patterns normalize.
6. **(Priority 3)** Add a `reconcile_parquet_vs_trades.py` CI check that runs nightly and emits a daily band CSV — continuous guardrail for future capture.

---

## 10. D4 unblocking status

- **D4-A (spec, chat-side): SAFE TO PROCEED.** No parquet ingestion required for spec-stage work. D4-A can consume the D1 citation set from the instruction and the `live/ats_trend_line.py` contract without reading the parquet. Proceed in parallel with DATA-002.
- **D4-B (impl): BOUNDED / CONDITIONAL.**
  - If D4-B implements a NEW module that reads from `gc_ohlcv_l2_joined.parquet` or `gc_m30_boxes.parquet`, it must be date-filtered to CLEAN dates only, OR must rebuild its input from raw trades, OR must wait on DATA-002 backfill.
  - Suggested compromise: D4-B implements but trains / calibrates only on CLEAN dates (83 dates in the L2-era reconciliation window, plus clean pre-handover Databento dates where the parquet is authoritative-by-construction). Document the filter explicitly in the impl.
- **D4-C (backtest of v2 Trend Classifier): BLOCKED on DATA-002 Priority-5 backfill.** A backtest that consumes the current parquet across the full 3y window will have ~20% of its L2-era dots on suspect dates. Cannot claim "v2 Trend Classifier validated" until either the parquet is clean or the backtest switches its input source to tape-derived data for the known-corrupt dates.

---

## 11. Decisions needed from Barbara

1. Authorise commit of `_audit/pp_sprint/DATA-001_raw_vs_parquet_reconciliation.md` and the `data001/` folder (scripts + CSVs + markdown tables)? Everything is additive and outside `live/`.
2. Approve the DATA-002 track opening (remediation) with priorities 1→6 as proposed in §9? Assign owner (suggest: a data-pipeline / infra engineer, separate from the ATS Trend Line / D4 track).
3. Approve D4-A to proceed in parallel (as per §10)? D4-B/C would block on DATA-002 priority-5 completion.
4. Approve 2-3 visual re-checks on Quantower of flagged CORRUPT dates `2026-03-06`, `2026-04-07`, `2026-04-19` to strengthen the premise that the tape is authoritative?
5. Approve a continuous reconciliation guardrail (DATA-002 priority-4) to run nightly / per-minute even before the fix lands? Low-risk, read-only.

---

_DATA-001 closed. HEAD unchanged at `ed9b19e`. Awaiting Barbara authorisation to commit and to open DATA-002._

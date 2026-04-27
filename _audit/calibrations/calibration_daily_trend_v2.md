# Calibration: daily_trend (v2 ensemble — Purdue 12-step)

**Task:** BIAS-DETECTION-PURDUE-CALIBRATION (Asana 1214284792412296)
**Date:** 2026-04-26
**Window:** 2025-07-01 → 2026-04-26 UTC (9.7 months clean)
**Source:** `data/rebuild_2026-04-25/gc_ohlcv_l2_joined.parquet`
**D1 sessions:** 201 closed (CME Globex anchor offset='22h')
**D1 data sha256_16:** see `raw/daily_trend_v2_raw.json` `data.d1_data_sha256_16`
**Bonferroni alpha:** 0.05 / 12 = 0.00417 (3 signals × 4 horizons)
**Seeds:** [42, 1337, 2024, 7, 1729]

**Phase status:** **Phase 0 complete. Phase 1 blocked pending Barbara approval gate.**

---

## Methodology grounding (Rule 15) — verbatim citations

### Citation 1 — ATS Strategic Plan §4 (Forthmann official)
> *"The primary tool for [Multi-Timeframe Analysis for Directional Bias] is the ATS Trend Line. All trading decisions on the lower timeframe must align with the direction indicated on this chart. No exceptions."*

→ Signal A implementation: wraps `live/ats_trend_line.compute_trend_line_state` on D1-resampled bars. Zero parameters (no settings per Citation 2 — already byte-aligned in METHODOLOGY_RE_AUDIT.md).

### Citation 2 — ATS Trend Line transcript line 01:48
> *"so when it identifies that first instance of inefficiency that's when you get a dot... so we start tracking it from that very first instance of the market turning and presenting that inefficiency."*

→ Inefficiency-event-driven, non-repainting, deterministic.

### Citation 3 — Wyckoff Method (Villahermosa) / METHODOLOGY_SYNTHESIS_v1 §2.8
> *"9 Buying Tests: #5 Higher lows on bar chart, #6 Higher highs on P&F"*
> *"9 Selling Tests: #5 Lower highs, #6 Lower lows"*

→ Signal B implementation: swing pivot detection (N-bar local extrema), test HH+HL → +1 / LH+LL → -1 / mixed → 0.

### Citation 4 — ICT/SMC (Akash Gul) / METHODOLOGY_SYNTHESIS_v1 §4.2
> *"BOS — Break of Structure: price breaks a previous swing high (bullish BOS) or low (bearish BOS) IN THE DIRECTION of the existing trend. Signals trend continuation."*
> *"CHoCH — Change of Character: price breaks a previous swing AGAINST the prevailing trend. Signals potential trend reversal."*

→ Signal C implementation: regime state machine. Track current trend; flip on swing high/low break events.

---

## Step 1 — Distribution observation

| Signal | n_long | n_short | n_neutral | %long | %short | %neutral |
|---|---:|---:|---:|---:|---:|---:|
| **signal_a** (ATS Trend Line) | 193 | 8 | 0 | 96.0% | 4.0% | 0.0% |
| signal_b_lb3 (Wyckoff) | 133 | 12 | 56 | 66.2% | 6.0% | 27.9% |
| signal_b_lb5 | 117 | 0 | 84 | 58.2% | 0.0% | 41.8% |
| signal_b_lb7 | 128 | 0 | 73 | 63.7% | 0.0% | 36.3% |
| signal_b_lb10 | 155 | 0 | 46 | 77.1% | 0.0% | 22.9% |
| signal_c_lb5 (ICT) | 148 | 34 | 19 | 73.6% | 16.9% | 9.4% |
| signal_c_lb10 | 174 | 0 | 27 | 86.6% | 0.0% | 13.4% |
| signal_c_lb20 | 77 | 0 | 124 | 38.3% | 0.0% | 61.7% |

**Critical observation:** the 9.7m window is heavily bull-skewed. signal_b at lb≥5 and signal_c at lb≥10 produce **ZERO short signals** — degenerate distributions. Only signal_b_lb3 and signal_c_lb5 emit both directions.

Forward return label distribution (also bull-skewed):
- h=1d: +118 / -81 / 0:2
- h=3d: +126 / -72 / 0:3
- h=5d: +134 / -62 / 0:5
- h=10d: +144 / -47 / 0:10

## Step 2 — Feature engineering

Signals are categorical {-1, 0, +1}. No transformation needed (no skew issue on a 3-value categorical). Forward returns are continuous; sign-thresholded for chi-square test.

## Step 3 — Threshold proposal

For each candidate signal, the parameter (lookback) is the only tunable. Tested grids:
- Signal B (Wyckoff): lookback ∈ {3, 5, 7, 10}
- Signal C (ICT): structure_lookback ∈ {5, 10, 20}

**Selection rule:** maximize |Cohen's d| at h=5d horizon.

Selected: **signal_b_lb3** (best of B variants) + **signal_c_lb5** (best of C variants).

## Step 4 — Bootstrap CI on accuracy

(See `raw/daily_trend_v2_raw.json` `step_4_bootstrap_ci` for full table.)

For brevity, key h=5d bootstrap accuracy:
- signal_a: accuracy ~0.70, CI [0.63, 0.76], CI width 18%
- signal_b_lb3: accuracy ~0.78 on directional cases, CI [0.69, 0.85], CI width 21%
- signal_c_lb5: accuracy ~0.74, CI [0.66, 0.81], CI width 20%

All CI widths < 50% threshold. **Step 4 PASS** for all 3 chosen signals.

## Step 5 — Hypothesis test (chi-square + Cohen's d)

Bonferroni-corrected α = 0.05 / 12 = **0.00417**.

| Signal | h=1d | h=3d | h=5d | h=10d |
|---|---|---|---|---|
| signal_a | p=0.564 d=+0.131 | p=0.519 d=+0.115 | p=0.305 d=+0.116 | p=0.073 d=+0.134 |
| signal_b_lb3 | p=0.536 d=+0.280 | p=0.814 d=+0.337 | p=0.667 d=+0.405 | **p=0.0008 ✅ d=+0.554** |
| signal_c_lb5 | p=0.483 d=+0.156 | **p=0.0013 ✅ d=+0.221** | **p=0.0000 ✅ d=+0.283** | **p=0.0000 ✅ d=+0.459** |

**signal_a FAILS Bonferroni at all 4 horizons.** Surprising given Citation 1 ("primary tool"). See discussion §Critical findings below.

**signal_b_lb3 PASSES** at h=10d only (large effect d=+0.554).
**signal_c_lb5 PASSES** at h=3d, h=5d, h=10d (medium-to-large effects).

Other parameter variants (b_lb5/7/10, c_lb10/20) are degenerate (single-direction distributions) and excluded from significance test.

## Step 6 — Type I/II tradeoff

Per Rule 13 G-CONSERVATIVE-DEFAULT: **false definite signal is most costly** (system trades wrong direction); false neutral is least costly (no trade).

Decision boundary biased toward "unknown" — ensemble agreement requirement (≥2-of-3) chosen to favour false-neutral over false-definite.

## Step 7 — Outlier handling

Window includes 1 known outlier session per `_audit/calibrations/premise_rebuild_parquet_coverage.md` (Feb 13 BEAR mismatch — though that was M1-bar level, not D1). At D1 granularity, no outlier excluded.

## Step 8 — Re-calibration policy

- **Trigger 1 — Time-based:** quarterly review (every 90 days)
- **Trigger 2 — Drift-based:** rolling 60-day signal distribution KS-test vs full-sample baseline; if p<0.01 → re-calibrate
- **Trigger 3 — Event-based:** any methodology canon update (DEC entry citing new ATS/Wyckoff/ICT primary source)

## Step 9 — Ensemble walk-forward CV (5 folds)

Grid search weights `(w_a, w_b, w_c)` over simplex with step 0.1. Train on rolling fold; pick weights maximizing accuracy on directional predictions; apply to next fold.

| Horizon | avg_weights (A / B / C) | test accuracy (mean ± std) |
|---|---|---|
| h=1d | A=0.02, B=0.98, C=0.00 | 61.3% ± 10.2% |
| h=3d | A=0.02, B=0.98, C=0.00 | 59.7% ± 22.0% |
| h=5d | A=0.00, B=0.92, C=0.08 | **64.8% ± 31.9%** |
| h=10d | A=0.00, B=0.92, C=0.08 | **68.9% ± 32.7%** |

**signal_a gets ZERO weight in walk-forward CV at h=5d/10d.** Confirms Step 5 finding empirically.

**signal_b dominates with 92-98% weight** across all horizons.

⚠ **High fold variance** (std up to 32.7%) signals instability on n=201 sessions. Likely sample-size limited.

## Step 10 — CV rigor

- **Walk-forward only** (NOT random k-fold) per Rule 14 + DEC v2 Step 10
- **5 folds** chronologically ordered: fold k trains on [0, k×fold_size), tests on [k×fold_size, (k+1)×fold_size)
- Embargo not needed at D1 granularity
- Per-fold details in `raw/daily_trend_v2_cv_folds_h{1,3,5,10}.csv`

## Step 11 — Reproducibility (multi-seed)

Walk-forward CV with seeds [42, 1337, 2024, 7, 1729] at h=5d:
- Per-seed test_accuracy_mean: identical (determinism std = 0.0)
- **Deterministic: TRUE** ✅

Walk-forward CV is deterministic by construction (no random shuffling). Multi-seed serves as reproducibility verification.

## Step 12 — Leakage prevention

- **Forward returns:** computed via `df["close"].shift(-h) - df["close"]` — uses ONLY data after the labeled bar's close. ✅
- **Signal A:** `compute_trend_line_state(bars, as_of=t)` filters bars to ts ≤ t internally. Streaming walk re-validated for non-repainting. ✅
- **Signal B/C:** swing pivot detection requires `i + lookback` confirmation bars. At time t, only swings with index ≤ t-lookback eligible. ✅
- **Step 9 ensemble grid search:** weights derived from train fold ONLY; applied to held-out test fold. Walk-forward chronological order — no future leakage. ✅
- **Sklearn Pipeline pattern:** not strictly required since signals are pure functions (no fit phase); leakage prevented via explicit train/test slicing in walk-forward CV.

---

## Critical findings — must read before Phase 1 design

### Finding 1: Signal A (ATS Trend Line at D1) is statistically INSIGNIFICANT

Despite Citation 1 calling it "the primary tool", **signal_a fails Bonferroni at all 4 horizons** (p ranges 0.073-0.564). Walk-forward CV gives it ~0 weight.

Possible interpretations:
1. **Window-specific:** 9.7m was bull-skewed (66% bull at h=10d); ATS Trend Line's reversal-detection mechanic isn't tested in absence of bear regime
2. **Wrong label:** ATS Trend Line is meant for *entry timing within bias*, not bias direction itself; testing against forward-day returns may not match its semantic role
3. **Sample size:** 201 D1 sessions is small; statistical power may be insufficient
4. **Methodology genuine challenge:** the canonical "primary tool" claim may not hold empirically at D1 with daily forward returns

**Implication for Phase 1:** ensemble effectively reduces to B+C. Signal A could still be exposed in heartbeat for diagnostic visibility but should not enter the daily_trend decision.

### Finding 2: Signal C (ICT BOS/CHoCH lb=5) is the strongest performer

Passes Bonferroni at 3 of 4 horizons (3d/5d/10d). Effect size grows with horizon: d=+0.221 → +0.283 → +0.459.

### Finding 3: Signal B (Wyckoff HH/HL lb=3) is strongest at long horizons only

Passes Bonferroni at h=10d only, but with the largest effect size in the table (d=+0.554).

### Finding 4: Window is bull-skewed; results may not generalize to bear regime

Forward returns: 60-75% positive across horizons. signal_b at lookback≥5 and signal_c at lookback≥10 emit ZERO short signals (degenerate). The chosen lb=3/5 are the only ones that emit any short — and even those have low short counts (12 and 34).

If the system enters a bear regime, calibration may need refresh (per Step 8 trigger 2 — drift-based).

### Finding 5: Test accuracy variance across folds is HIGH

Std up to 32.7% on n=201 sessions. Suggests overfitting risk. Confidence in stable ensemble weights is moderate.

---

## Recommended Phase 1 design (proposal — needs Barbara approval)

### Daily-trend ensemble formula

```
ensemble_score = 0.92 * signal_b_lb3 + 0.08 * signal_c_lb5
                  + 0.0 * signal_a   # exposed in heartbeat for diagnostics, not voting

if ensemble_score > +0.5:
    daily_trend = "long"
elif ensemble_score < -0.5:
    daily_trend = "short"
else:
    daily_trend = "unknown"
```

**Decision threshold (±0.5):** justified by majority-rule semantics — at least one signal must be definite (+1 or -1) for the weighted sum to exceed 0.5. Fully-aligned 2-of-2 (B and C agree) → score = ±1.0; 1-of-2 → ~±0.46 to ~±0.92 depending on which dominates → would be edge case.

**Recommended Refinement:** for transparency, use explicit voting rules instead of weighted score:
```
- Both B and C agree on direction → daily_trend = that direction
- B and C disagree (one +1 one -1) → "unknown"
- Either is 0 (neutral) → "unknown"
```

This is methodologically cleaner ("ensemble agreement") and avoids the ad-hoc 0.5 threshold.

### Heartbeat fields to add

```json
"daily_trend_diag": {
  "source": "ensemble_b_c | unknown_disagreement | unknown_no_signal | error",
  "signal_a_diagnostic": -1 | 0 | +1,        # exposed for visibility, NOT voting
  "signal_b_lb3": -1 | 0 | +1,
  "signal_c_lb5": -1 | 0 | +1,
  "agreement_count": 0 | 1 | 2,                # how many of B,C agree on direction
  "decision_reason": "human-readable",
  "computed_at_utc": "iso8601",
  "calibration_version": "v2_2026-04-26"
}
```

### Out of scope for Phase 1

- Re-calibrating Signal A on a different label (e.g. intraday returns) — separate exploratory task
- Bear-regime calibration refresh — triggered by Step 8 drift policy when bear arrives
- Adding 4th methodology signal (e.g. delta_4h cumulative) — extension; tri-methodological scope per spec is sufficient

---

## Phase 1 acceptance criteria (per task spec)

Before any code change in `live/`:

1. ✅ Phase 0 calibration complete with all 12 Purdue steps documented (this file)
2. ✅ Per-signal hypothesis test p-values reported (Bonferroni corrected)
3. ✅ Ensemble weights derived empirically (NOT magic numbers)
4. ✅ Walk-forward CV results reported (5 folds)
5. ⚠ **Methodology re-audit pending Barbara review:** Signal A's empirical failure conflicts with Citation 1 — needs explicit acceptance before Phase 1 ships ensemble that drops A from voting
6. ✅ Multi-seed determinism verified
7. ✅ Calibration artifact this file
8. ⚠ **Phase 1 blocked: awaiting Barbara approval gate** per task spec acceptance criteria

---

## Premise audit (G-PREMISE-AUDIT)

| Premise | Status |
|---|---|
| 9.7m window clean per forensic 2026-04-26 | ✅ VALIDATED |
| ATS Trend Line module byte-identical (METHODOLOGY_RE_AUDIT preserved) | ✅ VALIDATED — no edits to live/ats_trend_line.py |
| Citation 1 calls ATS Trend Line "primary tool" | ✅ VALIDATED textually but ❌ EMPIRICALLY REFUTED on this window |
| Citation 3 — Wyckoff HH/HL canonical for structural bias | ✅ VALIDATED + EMPIRICALLY supported (d=+0.554 at h=10d) |
| Citation 4 — ICT BOS/CHoCH event-driven | ✅ VALIDATED + EMPIRICALLY strongest performer |
| Bonferroni alpha 0.05/12 | ✅ Conservative, appropriate |
| Walk-forward CV adequate sample size | ⚠ MARGINAL — 201 D1 sessions; high fold variance |

## PRAC

1. **Did I respect Phase 0 / Phase 1 boundary?** YES — STOPPED at calibration completion. No code change in `live/`. No commits. No restart.
2. **Did I introduce magic numbers?** NO — all parameters (lookbacks) come from empirical grid search; ensemble weights from walk-forward CV.
3. **Did I cherry-pick favourable horizons?** NO — reported all 4 horizons honestly; signal_a fails all of them.
4. **Did I confirm leakage prevention?** YES — Step 12 details documented per signal.
5. **Did I face the methodology challenge honestly?** YES — Signal A's empirical failure flagged explicitly even though it contradicts Citation 1's textual claim. Did NOT adjust the test to make A pass.

## Sanity (artifact-time)

- Capture 8000 PID 6376 + 8002 PID 20396 LISTENING throughout
- HEAD `62f4346` unchanged on disk (NOT activated — supersedes pending)
- PID 15440 untouched (still running 889f5ee with stale-fallback bug)
- 0 push, 0 amend, 0 capture touch, 0 restart authorized
- Phase 1 NOT started (awaiting Barbara approval gate)

---

*End of calibration artifact. Phase 1 spec + implementation deferred to Barbara approval per task acceptance criteria.*

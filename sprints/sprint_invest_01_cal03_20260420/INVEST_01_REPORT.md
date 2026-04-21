# INVEST-01 — CAL-03 `delta_4h_inverted_fix` Regime-Segmented Investigation

**Sprint:** `sprint_invest_01_cal03_20260420`
**Type:** READ-ONLY — zero code/config/service touches
**Date:** 2026-04-20
**Author:** Claude (executed per `CLAUDECODE_INVEST_01_CAL03_v2.md`)
**Data sources:** `gc_ats_features_v5.parquet`, `calibration_dataset_full.parquet`, `gc_ohlcv_l2_joined.parquet`, `INCIDENT_20260420_LONG_DURING_DROP/20260420_145845/decision_log_last60min.jsonl`

---

## §1. Discovery findings (§2 of prompt)

### 1.1 Finding verbatim (settings.json `_cal03_finding`)

> `"delta_4h as bullish signal is INVERTED. High positive delta_4h = buyer exhaustion = bearish (E[fwd]=-10pts at d4h>800). trend_resumption_threshold kept null intentionally — signal direction is wrong. Use M30 bias flip as regime exit signal instead."`

Source tag: `"source": "calibration_sprint_bloco1+CAL03-2026-04-10"`

### 1.2 Naming conflict resolved (per Barbara §5)

Two distinct CAL-03 artifacts exist. INVEST-01 is **scoped exclusively to `_cal03_finding` (inverted_fix)**.

| Artifact | Topic | Scope |
|---|---|---|
| CAL-03a | "Delta 4H Flip Duration" for `delta_flip_min_bars=47` exit gate (in deleted `calibration_sprint.py`) | **OUT OF SCOPE** |
| **CAL-03b (this investigation)** | `delta_4h_inverted_fix` — interpretation of d4h extremes as exhaustion → reversal | **IN SCOPE** |

### 1.3 Artifacts found (partial)

- `_cal03_finding` comment in settings.json (1-line finding)
- source tag in settings.json
- `calibration_sprint_20260407_195015.log` (CAL-03a log, unrelated)
- `REGIME_FLIP_FORENSIC.md` (2026-04-20, post-incident RCA — not original)
- Code: `ats_live_gate.py:406` (`if _d4h_cfg["inverted_fix"]`) + downstream
- `APEX_THRESHOLDS_INVENTORY_20260419.md` row 42 (descriptive)

### 1.4 Artifacts NOT found

- No dedicated `sprint_cal03_*` or `sprint_bloco1_calibration_20260410` directory
- `calibration_sprint.py` deleted (`ats_backtest/` path gone)
- No calibration report for the inverted_fix finding specifically
- No backtest CSV/parquet output from the inverted_fix calibration

---

## §2. Calibration methodology reconstruction (Q1–Q7)

| Q | Answer | Source |
|---|---|---|
| **Q1 Date** | 2026-04-10 | source tag (HIGH confidence) |
| **Q2 Dataset** | **UNKNOWN** — likely `calibration_dataset_full.parquet` subset Jul–Nov 2025 | not documented |
| **Q3 Metric** | E[fwd] in points = -10pts at d4h>800. Horizon **unspecified**. | PARTIAL |
| **Q4 Threshold derivation** | `_cal03_finding` cites 800; deployed has 3000/-1050 (CAL-14/15 later). Transition 800→3000/-1050 for inverted interpretation **NOT justified in any doc** | **UNKNOWN** |
| **Q5 Both sides tested?** | **NO** — finding cites ONLY +d4h side (>800). Negative side is **EXTRAPOLATED SYMMETRICALLY without evidence** | **ASYMMETRIC** |
| **Q6 Regime stratification** | No stratification — finding is global pooled | **NONE** |
| **Q7 Walk-forward** | UNKNOWN for inverted_fix specifically | **UNKNOWN** |

**§2 bottom line:** Finding is documented (partial); calibration rigour is NOT.

---

## §3. Regime distribution of historical data (§3.3 of prompt)

Regime classifier (existing v5 columns, per prompt §3.2):

- **RANGE:** `m30_in_contraction=True AND weekly_aligned=False`
- **TREND:** `m30_in_contraction=False AND weekly_aligned=True AND daily_trend in (long, short)` — segmented `TREND_UP` / `TREND_DN`
- **TRANSITIONAL:** else

### 3.1 M30 regime distribution per period

| Period | N rows | RANGE | TREND_UP | TREND_DN | TRANSITIONAL |
|---|---|---|---|---|---|
| full_2020_2026 | 73,074 | 5,993 (8.2%) | 29,816 (40.8%) | 7,483 (10.2%) | 29,782 (40.8%) |
| pre_sprint8_2020_H1'25 | 64,864 | 5,796 | 24,412 | **7,483** | 27,173 |
| **sprint8_training (Jul–Nov 2025)** | 4,967 | 150 (3.0%) | **3,283 (66.1%)** | **0** | 1,534 (30.9%) |
| post_deploy (Dec'25–Mar'26) | 3,196 | 40 (1.3%) | 2,121 (66.4%) | **0** | 1,035 (32.4%) |

**Critical finding:** TREND_DN = 0 in Sprint 8 training era AND in post-deploy era. GC was bull trend throughout. **All TREND_DN historical samples are pre-2025 (where `rolling_delta_4h` is empty — L2 not captured).**

### 3.2 `rolling_delta_4h` extremes per regime × period

See `phase2_regime_distributions.json` for full table. Highlights:

- Pre-2025-07: **zero d4h extremes in any regime** (L2 feature not captured pre-Sprint 8)
- Sprint 8 training: 13,283 d4h<-1050 events in TREND_UP, 901 d4h>+3000 events in TREND_UP, 484 d4h<-1050 in RANGE
- **TREND_DN in training + post-deploy: 0 events of any kind** (zero samples)

---

## §4. delta_4h extremes per regime (§3.4) — see phase2 JSON

`phase2_regime_distributions.json` embeds the full per-period × per-regime extreme counts at 4 thresholds (±800 original, ±3000/-1050 deployed).

Summary already in §3.2 above.

---

## §5. Segmented forward-return tables (§4.2) — PRIMARY RESULT

Forward returns in **points** computed from OHLCV (M1 close shift). Bootstrap 95% CI (N=1000 resamples). Cohen's d vs regime-period baseline.

### 5.1 Seller exhaustion hypothesis: `rolling_delta_4h < -1050 → LONG`

Total events all periods: 20,677.

**Sprint 8 training (Jul–Nov 2025), N=19,538:**

| Regime | N | 30m mean pts | 30m CI95 | crosses 0? | 30m WR+ | Cohen's d | 4h mean pts | 4h WR+ |
|---|---|---|---|---|---|---|---|---|
| **RANGE** | 484 | **+0.79** | [+0.62, +0.98] | NO | 62.81% | **+0.2425** | **+5.85** | 79.13% |
| TREND_UP | 13,283 | +0.34 | [+0.16, +0.48] | NO | 52.67% | +0.0066 (**TRIVIAL**) | +3.42 | 60.24% |
| TRANSITIONAL | 5,771 | +0.07 | [-0.07, +0.21] | **YES** | 45.82% | +0.0132 | +0.24 | 42.64% |
| **TREND_DN** | **0** | — | — | — | — | — | — | — |

**Post-deploy (Dec'25–Mar'26), N=1,139:**

| Regime | N | 30m mean | CI crosses 0? | 30m WR+ | Cohen's d | 4h mean | 4h WR+ |
|---|---|---|---|---|---|---|---|
| TREND_UP | 804 | +1.05 | NO | 57.46% | +0.060 | +6.13 | 60.82% |
| TRANSITIONAL | 335 | +1.85 | NO | 69.25% | +0.126 | +8.47 | 55.52% |
| TREND_DN | **0** | — | — | — | — | — | — |
| RANGE | 0 | — | — | — | — | — | — |

**Interpretation for LONG hypothesis:**
- **RANGE (training): GENUINE effect** — small-to-moderate d (0.24), WR 63%, CI strictly positive
- **TREND_UP: TRIVIAL effect size** (d=0.007 training; d=0.06 post-deploy) — statistical significance by large N only
- **TRANSITIONAL: WEAK and era-dependent** — training CI crosses 0; post-deploy positive
- **TREND_DN: UNTESTABLE** (zero samples in both training and post-deploy)

### 5.2 Buyer exhaustion hypothesis: `rolling_delta_4h > +3000 → SHORT`

Total events all periods: 1,583.

**Sprint 8 training (Jul–Nov 2025), N=1,500:**

| Regime | N | 30m mean | CI95 | crosses 0? | 30m WR (neg ret) | Cohen's d | 4h mean | 4h WR (neg) |
|---|---|---|---|---|---|---|---|---|
| TREND_UP | 901 | **-2.42** | [-3.25, -1.61] | NO | 52.16% | **-0.3631** (moderate) | **-5.10** | 66.26% |
| TRANSITIONAL | 599 | -1.46 | [-1.88, -1.05] | NO | 57.10% | -0.2451 (small-moderate) | **-14.11** | 86.64% |
| RANGE | 0 | — | — | — | — | — | — | — |
| TREND_DN | 0 | — | — | — | — | — | — | — |

**Post-deploy (Dec'25–Mar'26), N=83:**

| Regime | N | 30m mean | CI crosses 0? | 30m WR(neg) | Cohen's d | 4h mean | 4h WR(neg) |
|---|---|---|---|---|---|---|---|
| TREND_UP | 60 | **+0.17** (SIGN FLIP) | **YES** | 41.67% (below random) | -0.010 | -24.53 | **100.00%** |
| TRANSITIONAL | 23 | **+1.69** (SIGN FLIP) | NO | 30.43% (very bad) | +0.116 | -43.94 | **100.00%** |

**Interpretation for SHORT hypothesis:**
- **TREND_UP (training): GENUINE moderate effect** (d=-0.36) — validated
- **TRANSITIONAL (training): GENUINE small-moderate effect**
- **Post-deploy: SIGN FLIPS at 30m** (instability) but strongly validates at 4h (-24 to -44pts, WR 100%) — timing ambiguity
- Era-dependent at short horizons; validates at longer horizons

### 5.3 Original 800 threshold replication (answers Q3/Q4 empirically)

**`_cal03_finding` claim:** E[fwd] = -10pts at d4h > +800.

**Empirical replication (training TREND_UP, N=15,622):** 30m mean **-0.18pts** (55× weaker), 4h mean **-1.19pts**. **Claim does NOT replicate.** At no (regime × horizon) cell does 30m mean approach -10pts.

Candidates for the -10pts claim source: longer horizon (24h gives ~-8pts in some cells), different threshold (fit only at +3000 approaches -10pts in some cells), different dataset, or measurement error in the original finding.

---

## §6. Statistical rigor — CI, Cohen's d, walk-forward (§4.3-4.4)

### 6.1 Summary table (effect sizes that survive both CI + d ≥ 0.2 criteria)

| Hypothesis | Regime | Period | Surviving? | Notes |
|---|---|---|---|---|
| LONG @ d4h<-1050 | **RANGE** | training | **YES** (d=0.24, CI strict+) | Small sample (N=484) but genuine |
| LONG @ d4h<-1050 | TREND_UP | training | NO — d=0.007 trivial | Only sig by large N |
| LONG @ d4h<-1050 | TRANSITIONAL | training | NO — CI crosses 0 | Not significant |
| LONG @ d4h<-1050 | TRANSITIONAL | post-deploy | MARGINAL (d=0.126, CI+) | Weak |
| SHORT @ d4h>+3000 | TREND_UP | training | **YES** (d=-0.36, CI strict-) | Moderate |
| SHORT @ d4h>+3000 | TRANSITIONAL | training | **YES** (d=-0.25, CI strict-) | Small-moderate |
| SHORT @ d4h>+3000 | TREND_UP | post-deploy | NO — sign flip at 30m | Unstable |
| SHORT @ d4h>+3000 | TRANSITIONAL | post-deploy | NO — sign flip at 30m | Unstable |

### 6.2 Walk-forward sign stability

- **LONG @ RANGE:** only training data (no post-deploy samples) → cannot assess stability → **UNKNOWN**
- **LONG @ TREND_UP:** sign stable (positive both periods) but effect trivial → effectively null
- **SHORT @ TREND_UP:** **SIGN FLIP at 30m** (training -2.42 → post-deploy +0.17) — UNSTABLE at short horizons
- **TREND_DN:** zero data in both periods → cannot assess

### 6.3 Asymmetry verdict (Q5)

The asymmetry assumption in the original `_cal03_finding` (extrapolating +800 finding to -800 and then to ±1050/±3000) is **empirically NOT vindicated**. The two sides behave differently:

- **+d4h → SHORT:** genuine moderate effect in training TREND_UP (d=-0.36), unstable post-deploy
- **-d4h → LONG:** genuine only in RANGE training (d=0.24), trivial in TREND_UP, zero data in TREND_DN

---

## §7. 2026-04-20 incident counterfactual (§5 of prompt)

All 8 GO LONG entries at 14:09–14:56 UTC, fallback-reconstructed regime inline from `decision_log` context (v5 stale):

| # | ts | phase | daily_trend | recon regime | d4h | d4h extreme? | trigger reason | fwd_30m actual | HIST base (same regime×d4h<-1050) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 14:09:05 | TREND | short | **TREND_DN** | -473 | NO | iceberg sweep +2 | +1.45 | **N=0** |
| 2 | 14:12:45 | TREND | short | TREND_DN | -487 | NO | iceberg sweep +2 | +2.35 | N=0 |
| 3 | 14:12:47 | TREND | short | TREND_DN | -487 | NO | iceberg sweep +2 | +2.35 | N=0 |
| 4 | 14:37:08 | TREND | short | TREND_DN | -748 | NO | iceberg sweep +2 | **-18.45** | N=0 |
| 5 | 14:37:10 | TREND | short | TREND_DN | -748 | NO | iceberg sweep +2 | -18.45 | N=0 |
| 6 | 14:37:12 | TREND | short | TREND_DN | -748 | NO | iceberg sweep +2 | -18.45 | N=0 |
| 7 | 14:56:44 | TREND | short | TREND_DN | **-1133** | **YES** | **momentum OK (d4h=-1121) + iceberg lo +3** | +2.35 | **N=0** |
| 8 | 14:56:47 | TREND | short | TREND_DN | -1136 | YES | momentum OK (d4h=-1124) + iceberg lo +3 | +2.35 | N=0 |

### 7.1 Key incident findings

- **100% of entries in TREND_DN** — regime with **zero historical samples** for inverted_fix validation
- **Only 2/8 entries (7, 8 at 14:56) triggered inverted_fix** (d4h < -1050). The other 6 were primarily **V4 iceberg** driven (score +2) — **inverted_fix was not the primary causal signal for the incident's 6 earliest entries**
- Entries 7-8 (inverted_fix active) actually had **positive fwd_30m (+2.35pts)** and **positive fwd_4h (+9.75pts)** — they fired AFTER the drop bottom, caught the bounce
- Damage concentrated in entries 4-6 at 14:37 (fwd_30m -18.45pts) — **these were iceberg-driven, not inverted_fix**
- **`momentum OK` label on entries 7-8 IS the inverted_fix firing:** REGIME_FLIP_FORENSIC.md already documented that `delta_4h < -1050` with `inverted_fix=true` sets momentum status to "ok" instead of "block" (the exhaustion reinterpretation)

### 7.2 Counterfactual answer (prompt question)

> "Was today's LONG signal statistically supported by historical data, or counter-evidence?"

**Neither, structurally.** The historical base rate for "LONG when d4h<-1050 in TREND_DN" is **N=0** — the rule has **never been validated in the regime where the incident occurred**. The finding generalized from training data (TREND_UP + RANGE) to a regime with no observations.

---

## §8. VERDICT

Per prompt §6, adjusted per Barbara (6): VERDICT_A requires validation on BOTH sides; VERDICT_C specific for one-side validation.

### **VERDICT: mixed B + C + E**

No single letter cleanly covers the findings. Decomposed:

- **Per Q5 (asymmetry):** **VERDICT_C-like** — the two sides behave differently. +d4h SHORT validates in training; -d4h LONG validates only in RANGE.
- **Per regime coverage:** **VERDICT_B-like for the LONG hypothesis** — validated only in RANGE regime at Cohen's d ≥ 0.2; trivial or absent elsewhere.
- **Per walk-forward stability:** **VERDICT_D-like for SHORT hypothesis** — sign-flips in post-deploy era at 30m horizon (era-dependent).
- **Per TREND_DN coverage:** **VERDICT_E-like for the incident regime** — zero historical data. Cannot be validated one way or the other; by default should be excluded from that regime's playbooks.
- **Per Q3 replication:** original "-10pts at d4h>800" finding does NOT replicate; empirical effect is 55× weaker at 30m. **VERDICT_F** (original finding likely over-stated or measured differently than documented).

**Synthesis:** inverted_fix is a WEAK, REGIME-DEPENDENT, ASYMMETRIC signal that:
- WAS published as a global universal rule
- Is EMPIRICALLY VALID only in RANGE regime for the LONG side, training TREND_UP for the SHORT side (unstable post-deploy)
- Is STRUCTURALLY UNTESTED in TREND_DN (incident regime)
- Does NOT replicate its originally-documented effect size

---

## §9. Selector-ready activation specification (§7 of prompt)

### 9.1 Option-a: sub-condition inside Range Reversal playbook (Design Doc v3 §5.2)

```yaml
condition_name: "delta_4h_exhaustion_extreme"
condition_predicate_long:  "rolling_delta_4h < -1050"
condition_predicate_short: "rolling_delta_4h > +3000"
phase_scope: [phase_b_accumulation, phase_b_distribution, phase_c_accumulation, phase_c_distribution]
regime_scope: [range_bound]
direction_effect:
  - "LONG direction when rolling_delta_4h < -1050 AND current playbook direction is LONG"
  - "SHORT direction when rolling_delta_4h > +3000 AND current playbook direction is SHORT"
confidence_contribution: +2 to playbook_score (aligned with legacy inverted_fix bonus)
activation_strictness: "HIGH — require explicit RANGE regime confirmation + matching phase"
```

### 9.2 Exclusions (explicit, per incident + VERDICT)

```yaml
excluded_from_playbooks:
  - trend_pullback_continuation:
      reason: "Cohen's d trivial (+0.007) for LONG @ TREND_UP; sign-flip post-deploy for SHORT @ TREND_UP"
  - breakout_retest:
      reason: "No data; speculative to activate"
  - judas_swing_reversal:
      reason: "Direction-logic is session + Wyckoff phase driven; inverted_fix adds no signal beyond what phase constraints already provide"

excluded_regimes:
  - TREND_DN:
      reason: "N=0 historical samples; rule structurally untested in incident regime"
```

### 9.3 Calibration targets for Nível 2 (framework v1.1 per Barbara requirement)

- **Threshold value:** currently -1050 / +3000 (deployed). Verify optimal within Range Reversal scope via multiplier sweep on RANGE-only subset (parallel to INVEST-02 multiplier-sweep methodology)
- **Horizon:** verify which fwd horizon (30m/60m/4h/24h) best matches actual Range Reversal trade holding durations (trades.csv + entry_quality_dataset)
- **Sample-size check:** RANGE regime only has N=484 events at d4h<-1050 in Sprint 8 training era. Small sample → bootstrap CI necessary + flag as preliminary
- **Re-collect TREND_DN data:** defer inverted_fix decision for Phase E Markdown playbook until a genuine TREND_DN dataset accumulates (live capture during next bear trend)

### 9.4 Comparison table — current rule vs recommended

| Aspect | Current (pre-incident) | Recommended (post-INVEST-01) |
|---|---|---|
| Activation scope | Universal (any phase, any regime) | Range Reversal playbook only (Phase B/C + regime=range_bound) |
| Direction support | Both LONG & SHORT | Both (still) |
| +/- symmetry | Assumed identical | Documented asymmetry — SHORT stronger in training, LONG validates only in RANGE |
| TREND_UP application | Active | **Disabled** (trivial effect size LONG; unstable SHORT) |
| TREND_DN application | Active (incident regime) | **Disabled** (zero data) |
| Confidence boost | +2 global | +2 only within Range Reversal activation |

---

## §X. UNKNOWNs from Phase 1 Discovery (per Barbara directive 4)

| Q | UNKNOWN | How addressed empirically in Phase 3 |
|---|---|---|
| **Q2** Dataset used in original CAL-03 calibration | ✓ Addressed by ignoring original claim and producing fresh per-regime × per-period analysis on `calibration_dataset_full.parquet` (2.19M M1 rows, 9 months with real L2 data Jul 2025–Apr 2026) — stronger rigour than original |
| **Q4** How 800 → 3000/-1050 transition was justified | ✓ Addressed by evaluating BOTH thresholds empirically (§5.3). Original -10pts@800 does NOT replicate; deployed ±3000/-1050 does show moderate effect in some cells but with era-instability |
| **Q5** Asymmetry (both sides tested?) | ✓ Addressed empirically (§6.3). Asymmetry confirmed — +d4h SHORT has moderate effect training / unstable post-deploy; -d4h LONG validates only in RANGE. Original extrapolation of +side finding to -side is NOT empirically supported |
| **Q6** Regime stratification | ✓ Addressed via explicit per-regime tables (§5.1-5.2). Finding is strongly regime-dependent — RANGE shows distinct behavior from TREND/TRANSITIONAL |
| **Q7** Walk-forward | ✓ Addressed via sub-period split training vs post-deploy (§5.1-5.2, §6.2). Sign-flip instability documented for SHORT hypothesis |
| TREND_DN coverage (incident regime) | ⚠ **Cannot be addressed with existing data** — N=0 across all periods in `calibration_dataset_full.parquet`. Requires live TREND_DN accumulation before Nível 2 decision for Phase E Markdown playbook |

---

## §10. System state

- Files modified in `live/` / `config/` / `data/`: **ZERO**
- Writes limited to: `sprints/sprint_invest_01_cal03_20260420/` (scripts + JSON outputs + this report + intermediate parquet)
- Capture PIDs 2512, 8248, 11740: **intact** (verified pre + post)
- Service PID 4516: **intact**
- Zero service restarts
- Zero git operations
- Settings.json hash unchanged: `BA0166FF...A42D52` (matches pre-Bloco1 backup)

---

## §11. Summary for Barbara

> `delta_4h_inverted_fix` is a **weak, asymmetric, regime-dependent** signal that was deployed as a **universal rule**. Empirically:
>
> - **RANGE regime:** LONG side validates (d=0.24, WR 63%) — **genuine but weak**
> - **TREND_UP:** SHORT side validates in training (d=-0.36) but **sign-flips post-deploy**; LONG side **trivial** (d=0.007)
> - **TREND_DN:** **zero historical data** — the exact regime where today's incident happened has never validated the rule one way or the other
> - **Original `-10pts at d4h>800` finding does NOT replicate** — empirical effect is 55× weaker
> - **2/8 incident entries were inverted_fix driven**; the other 6 were V4 iceberg. inverted_fix is a **contributing** but not the **primary** incident cause
>
> **Recommendation for Nível 2:** move `delta_4h_inverted_fix` into **Range Reversal playbook** (Design Doc v3 §5.2) as a sub-condition of reversal logic with +2 confidence contribution. Exclude from Trend Pullback Continuation and Judas Swing Reversal. Defer TREND_DN activation until live data accumulates.
>
> UNKNOWNs: Q2 (original dataset), Q4 (800→3000 transition justification), Q7 (walk-forward of original). All three superseded by fresh regime-segmented + walk-forward analysis in §5-6.

# M30 BOX STAGNATION RULE — Phase 1: Literature + Quantification

**Asana:** 1214327736913830 (P1.3, Tier P1, Priority High)
**Parent EPIC:** 1214327661523861 (Decision Engine Bug Fixes)
**Date:** 2026-04-28
**Author:** Claude Code (Opus 4.7)
**Scope:** Phase 1 read-only deliverable (literature + quantification + provenance).
**Trigger:** Box 5261 stuck since 22:00 UTC during -50pt overnight move
(box_high=4700.9, box_low=4693.6, current price ~4628 = 65pts BELOW box_low at audit time).
**Window:** 2025-07-01 → 2026-04-28 (10 months L2 data per `feedback_calibration_9months`).

---

## 1. PREMISES INHERITED (G-PREMISE-AUDIT)

| Premise | Source | Status |
|---------|--------|--------|
| Box detection in `live/m30_updater.py::_detect_boxes` requires CONTRACTION (3+ bars within 1.2×ATR) to start a new box | code (CONTRACTION_THR=1.2, MIN_BARS=3) | **VALIDATED** (read directly) |
| During strong trends, no contraction forms → algorithm continues forward-filling the last box | trigger investigation (Box 5261) | **VALIDATED** (parquet inspection: box 5261 box_high/low frozen since 03:30 UTC despite 65pt move) |
| `derive_m30_bias` consumes the stale box → produces incorrect bias | trigger doc | **VALIDATED** (live state confirms) |
| 10 months L2 window canonical for any calibration | `feedback_calibration_9months` | **VALIDATED** |
| No prior similar rule in codebase (G-PROVENANCE-VERIFICATION) | spec | **PARTIAL** — see § 5 |

**PREMISES CREATED in this Phase 1:**

- *"Stuck box" operational definition* — duration > 4h AND excursion > N×ATR from box midpoint without new box forming. § 3.
- *"Excursion"* — `max(close - box_high, box_low - close, 0)` over the box lifespan, measured in points or in ATR units. § 3.
- *Bias accuracy proxy* — sign of 4h-forward close move vs box position. § 4. *(simplified; the real `derive_m30_bias` is more nuanced — full backtest in Phase 2 must use the actual function.)*

---

## 2. LITERATURE REVIEW — G-LITERATURE-BEFORE-CODE

Verbatim survey across canonical sources. Key question: *does any source prescribe an
expiry / stagnation / time-decay rule for boxes / consolidation ranges / dealing ranges?*

### 2.1 ATS — *Everything to Know About Boxes* (training transcript)

`C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\Everything to Know About Boxes.txt`

> "[Boxes are] when the buying volume and selling volume have become equally
> the same and it's holding the market at that price level in a tight range
> and you'll notice that the market basically kind of winds down and tightens
> up to that point until you eventually get a box."

> "What a gray box means is that we had the market contract, but it actually
> didn't confirm into a full market cycle... the first time you're going to
> know that you're in a full-blown master pattern cycle is when you get a red
> box because what we need to confirm that we are in a full-blown expansion
> phase we need the first and second expansion legs."

> "[Green box with red outline:] The market could range out here for quite a
> while. Chances are you could see multiple expansion legs that pivot around
> that central point... typically when you get that level after as many
> expansion legs need to go by you're typically going to get a significant
> move away from that specific level."

**Prescription on stagnation:** None. Box "color" advances based on subsequent
expansion legs but no time/excursion expiry rule.

### 2.2 ATS — *Core Components Overview*

`C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\ATS Core Components Overview (Boxes, Expansion Lines, Liquidity Lines).txt`

> "[Boxes] just represent fair market value or that first market phase of
> contraction so anytime you see a box you know that a contraction happened...
> we get notified when the market goes into the expansion phase when we get
> that first dot... when we get our first expansion leg and then our second
> confirming expansion leg through the low of the box."

**Prescription on stagnation:** None.

### 2.3 Forthmann — *Volume Profile, Market Profile, Order Flow*

`C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\685506003-Volume-Profile-Market-Profile-Order-Flow-Next-Generation-of-Daytrading-by-Johannes-Forthmann*.pdf`

> "Whereas in earlier times we had to deal more with breakouts from known
> chart formations or trading ranges, today we see a multitude of false
> breakouts."

> "Trading ranges. They are extremely important. This is where the potential
> for future movements is created. If you know how to plot and interpret them,
> you can get signals that are very important."

**Prescription on stagnation:** None. Forthmann discusses *false breakout
detection* (order flow / VPOC vs MPOC alignment) but never an expiry rule.

### 2.4 Wyckoff — Villahermosa, *Wyckoff Methodology in Depth*

`C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\Wyckoff-Methodology-in-Depth-Ruben-Villahermosa.pdf`

Phase E (Trend out of range, ch. 25):

> "This Phase starts after the confirmation event. If the test after the
> break was successful and no traders appeared in the opposite direction, it
> can definitely be confirmed that one side has absolute control of the
> market."

On breakout integrity:

> "Not immediately re-entering in the trading range. It's the most reliable
> sign of intentionality. We are going to look for an effective rupture that
> manages to stay out of range and fails in its attempts to re-enter the
> equilibrium zone... it is essential that the price endures on the other
> side of the structure and does not generate an immediate re-entry."

**Prescription on stagnation:** Wyckoff prescribes *breakout validity* (price
must stay out of range), implying that if price *does* stay out, the range
is *invalidated*. This is the closest implicit precedent for a stagnation
rule, but it is framed as "phase E begins, look for the new trend" — *not*
"the box is dead, redraw it." Wyckoff would say: *follow the new trend, the
range is now historical.*

### 2.5 ICT — *The ICT Bible V1*

`C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\628929206-The-ICT-Bible-V1-By-Ali-Khan.pdf`

> "When price passes back through this range for a 3rd time and leaves it,
> this whole range has now been rebalanced."

> "The market constantly moves from internal range liquidity to external
> range liquidity, and external range liquidity to internal range liquidity
> (unless manual intervention is in play)."

**Prescription on stagnation:** ICT prescribes *rebalance after 3 passes*. This
is a rule on liquidity exhaustion, not on temporal stagnation, but it is the
**only quasi-explicit "range expiry" rule** found in the canon. It does not
address the case where price simply leaves and *never returns* (the trigger
case for P1.3).

### 2.6 Methodology synthesis (`METHODOLOGY_SYNTHESIS_v1.md`, ~30 KB)

`C:\Users\Administrator\Downloads\METHODOLOGY_SYNTHESIS_v1.md`

Mapping table cross-frames (Wyckoff/ATS/SMC):

| Wyckoff | ATS | ICT/SMC |
|---------|-----|---------|
| Phase A | Contraction (box forming) | Accumulation |
| Phase B | Box forming (gray/red transitions) | Late accumulation |
| Phase C — Spring | Multi-leg expansion: failed first leg | Manipulation: liquidity sweep |
| Phase D | Trend phase begins | Distribution / Expansion + BOS |
| Phase E | Trend continuation, new contraction forming | Trend continuation |

> "Multi-leg expansion: When price breaks out of a box then immediately
> crosses the opposite side BEFORE forming a new contraction, it's a
> multi-leg expansion. Can have 2-10+ legs. Confirms institutional
> non-commitment to first breakout direction."

**Prescription on stagnation:** None directly. Multi-leg expansion is
acknowledged but framed as *non-commitment* (range stays alive).

### 2.7 LITERATURE GAP — explicit verdict

**No canonical source prescribes a temporal-stagnation expiry for boxes.** The
closest precedents are:

- **Wyckoff Phase E** (ch. 25) — implies the old range is historical once
  Phase E confirms, but doesn't quantify *how long* before Phase E is assumed.
- **ICT 3-pass rebalance** — a structural rule, not temporal; doesn't apply
  to the trigger case (price leaves and never returns).

**Conclusion:** The proposed P1.3 rule is a **contemporary design decision
grounded in canonical principles** (Wyckoff's "price stays out = invalidation",
ICT's "ranges have a finite lifespan"), but **no canon source dictates the
specific thresholds** (N hours, K×ATR, etc.). Calibration via Purdue 12-step
on 10 months L2 is therefore the methodologically correct path.

---

## 3. QUANTIFICATION — STUCK-BOX DISTRIBUTION

Source: `C:/data/processed/gc_m30_boxes.parquet` (8,609 M30 bars,
2025-07-01 → 2026-04-28; 684 distinct boxes).

Script: `_audit/m30_box_stagnation_quantify.py` (committed alongside this doc).

### 3.1 Headline numbers

| Metric | Value |
|--------|------:|
| Total distinct boxes (10mo) | **684** |
| Mean duration | **9.14 h** |
| Max duration | **154.0 h** (6.4 days) |
| p50 duration | 5.5 h |
| p75 duration | 10.0 h |
| p90 duration | 17.0 h |
| p95 duration | **35.9 h** |
| p99 duration | **58.7 h** |

### 3.2 Duration buckets

| Threshold | N boxes | % of total |
|-----------|--------:|-----------:|
| > 2 h | 529 | **77.34 %** |
| > 4 h | 416 | **60.82 %** |
| > 6 h | 299 | 43.71 % |
| > 8 h | 217 | 31.73 % |
| > 12 h | 130 | 19.01 % |
| > 24 h | 39 | 5.70 % |

**Interpretation:** Boxes durations longer than 4h are *the norm, not the
exception* (61% of all boxes). The current algorithm produces stagnant
artefacts in the majority case, not edge cases.

### 3.3 Excursion during stagnation

For the 416 boxes with duration > 4h, max excursion (max distance close
travelled from box edges in points and ATR units):

| Excursion bucket (boxes > 4h) | N boxes | % of stuck>4h |
|-------------------------------|--------:|--------------:|
| > 2 × ATR | 237 | 56.97 % |
| > 3 × ATR | **170** | **40.87 %** |
| > 4 × ATR | 119 | 28.61 % |
| > 5 × ATR | 83 | 19.95 % |

**Excursion percentiles (boxes > 4h):**
- p50 = 2.33 × ATR
- p75 = 4.21 × ATR
- p90 = 6.73 × ATR
- p99 = **14.10 × ATR**

### 3.4 Top-10 most extreme stuck boxes (out of 416)

| Box ID | Date | Duration | Box range | Max excursion | Exc/Range | Exc/ATR |
|-------:|------|---------:|----------:|--------------:|----------:|--------:|
| 5182 | 2026-03-19 | 9.5 h | 14.3 pt | **269.8 pt** | 18.87 | 21.53 |
| 5177 | 2026-03-18 | 15.5 h | 17.5 pt | 131.7 pt | 7.53 | 18.50 |
| 4721 | 2025-08-29 | 17.5 h | 6.9 pt | 38.8 pt | 5.62 | 16.46 |
| 4846 | 2025-10-21 | 17.0 h | 15.8 pt | 242.6 pt | 15.35 | 16.05 |
| 5017 | 2025-12-28 | 18.0 h | 16.5 pt | 203.9 pt | 12.36 | 14.22 |
| 5158 | 2026-03-03 | **42.5 h** | 17.5 pt | 234.6 pt | 13.37 | 13.42 |
| 4948 | 2025-11-27 | 25.0 h | 5.5 pt | 64.7 pt | 11.76 | 12.83 |
| 4630 | 2025-07-22 | 5.5 h | 3.8 pt | 47.6 pt | 12.53 | 12.74 |
| 5112 | 2026-02-12 | 6.5 h | 12.6 pt | 160.1 pt | 12.76 | 12.41 |
| 4706 | 2025-08-22 | 6.5 h | 4.6 pt | 47.8 pt | 10.39 | 12.04 |

The trigger case (Box 5261, 2026-04-28) sits at ~33 pt excursion / ~3 ATR.
By the metrics above, this is **a typical stuck box, not an outlier** — the
top-10 extreme boxes are 4-8× more displaced.

---

## 4. BIAS ACCURACY DURING STAGNATION

For all M30 bars where the active box has been alive ≥ 8 bars (= ≥ 4h),
classified by *box-vs-price position*:

| Position | N bars | % up (4h fwd) | % down | Mean 4h move | Median 4h move |
|----------|-------:|--------------:|-------:|-------------:|---------------:|
| **above_box** (close > box_high) | 1,706 | **51.5 %** | 48.1 % | +0.19 pt | +0.75 pt |
| **below_box** (close < box_low)  | 1,472 | **58.9 %** | 41.0 % | **+3.00 pt** | +4.08 pt |
| **inside** | 801 | 55.2 % | 44.7 % | +2.42 pt | +2.00 pt |

**Diagnostic interpretation (proxy bias model):**

The simplest box-derived bias for a stuck box is *"price is currently away
from the box → expect mean reversion toward the box"*. For the trigger case
(price BELOW box → expect bullish reversion):

- Direction call: **bullish** (price should revert up)
- Forward 4h reality: **58.9 % up** (mean +3 pt)
- → Edge is real but **weak** (1.43 : 1 odds, +3 pt expected over 4 h)

For the symmetric **above_box** case (price above box → expect bearish
reversion):

- Direction call: **bearish**
- Forward 4h reality: **48.1 % down** (mean +0.19 pt)
- → Edge **non-existent** (worse than coin flip; expected +0.19 pt is
  small upward continuation, contradicting the bearish call)

**Implications for hard-gate use:**

The current `M30_BIAS_BLOCK` hard-gate uses bias derived from the box. When
the box is stuck:
- Below-box stuck → bias = bullish → blocks SHORT entries. Operationally
  the edge for "going long" is +3 pt over 4h — too thin to pay frictions
  given typical SL of 20+ pt. **Blocking SHORTs in this regime is more
  defensive than alpha-generative.**
- Above-box stuck → bias = bearish → blocks LONG entries. Edge is **negative**
  for the bearish call. **Blocking LONGs in this regime is actively wrong.**

This is the operational symptom Barbara noticed: gate behaves correctly
*structurally* (bias derivation is consistent) but produces low-quality
decisions because the bias *itself* is unreliable on stuck boxes.

---

## 5. PROVENANCE VERIFICATION (G-PROVENANCE-VERIFICATION)

Grep across `live/*.py` for any pre-existing box expiry / stale / decay logic:

### 5.1 EXISTS — `live/level_detector.py` `box_age_h`

```python
M5_STALE_WARN_H  = 0.5    # warn if last confirmed M5 box is > 30min old
M5_FALLBACK_H    = 0.25   # fallback to unconfirmed M5 if confirmed > 15min old
M30_STALE_WARN_H = 4.0
M30_FALLBACK_H   = 4.0    # fallback to unconfirmed M30 if confirmed > 4h old
```

**What it does:** Computes `box_age_h = now_utc - last_confirmed_bar_time`. If >
threshold, falls back to the latest *unconfirmed* box.

**What it DOES NOT do:** This metric measures **parquet freshness** —
i.e., "is `m30_updater` still updating the parquet?" — *not box-vs-price
displacement*. When `m30_updater` runs every 30 min, every M30 bar is
forward-filled with `m30_box_confirmed=True`, so `last_confirmed_bar_time =
now`, `age = 0`, regardless of how long the box has been geometrically stuck.

**Verdict:** Partial precedent, but DOES NOT solve P1.3. The proposed P1.3
rule must be additive: a *new* metric (excursion / time-since-formation /
hybrid) must drive expiry, not parquet age.

Furthermore, the M30 fallback path is gated on `if m30_df is None or m5
unavailable` — in production where M5 is always available, this code path
**never executes**.

### 5.2 NOT EXIST — Decay / fade / time-confidence functions

Grep `decay|fade|confidence.*time` returned 0 hits in `live/`. **No
decay-bias logic exists**, so Phase 2 Option D is genuinely greenfield.

### 5.3 RELATED — `_validate_m5_vs_m30(levels, m30_bias)` (level_detector.py:983)

Cross-frame consistency validator (called after extracting levels).
Does not address stagnation; left untouched.

---

## 6. DECISION_LOG CROSS-REFERENCE (post-P0 sample limited)

Cross-reference of the 10-month decision_log against stuck-box windows:

| Metric | Count |
|--------|------:|
| decision_log rows in window (10mo) | 22,302 |
| rows during a stuck-box (>4h) window | **10,036** (45.0 %) |
| rows during stuck-box AND price ABOVE box | 1,132 |
| rows during stuck-box AND price BELOW box | **7,876** |
| BLOCK rows (post-P0 only — 2026-04-28 +) | 0 |
| BLOCK rows during stuck-box (post-P0) | 0 |

**45% of all decisions in 10 months happened during a stuck box.** Of those,
**78% had price below the box** (i.e., bias-from-box reads bullish, would
block SHORT signals). This matches the operational complaint: a large fraction
of trading decisions are made under conditions where the bias signal is
known-unreliable.

The post-P0 BLOCK count is 0 because Fase B only deployed on 2026-04-28 and
ASIAN session is quiet. Phase 2 backtest will use the *full historical
decision_log* (with `derive_m30_bias` re-run offline) to estimate hypothetical
P&L from BLOCKs that *would* have been emitted retroactively.

---

## 7. PRE-RESPONSE ADVERSARIAL CHECK (G-PRAC)

Risks of acting on this Phase 1:

- **Overfit risk** — Phase 2 must use **purged walk-forward** k=5 with
  embargo (per `feedback_purdue_calibration` / Rule 3) to calibrate any
  threshold (N hours, K×ATR). The 10-month window is shorter than ideal
  for stable calibration; bootstrap CI N=1000 + Bonferroni correction
  across 4 options + control should be applied per Phase 2 spec.
- **Confirmation bias** — the trigger case (Box 5261) primed the search.
  The quantification *did* find that 60.8% of boxes go > 4h, so the
  trigger was a typical, not exceptional, instance — but I should not
  assume the 4h threshold is the right one. Phase 2 must sweep
  T ∈ {2, 4, 6, 8} h *and* K ∈ {2, 3, 4, 5} ATR.
- **Premise fragility — bias-accuracy proxy** — § 4 uses a simplified
  `box_position` proxy, not the real `derive_m30_bias`. Phase 2 *must* run
  the real function offline against the 10-month parquet to get the
  accurate hypothetical-blocked-trades count. The numbers in § 4 are
  *directional indicators*, not Phase 2 inputs.
- **Missing tests** — None to add in Phase 1 (read-only). Phase 3
  implementation must add tests for the new expiry function.
- **Scope creep** — DO NOT touch `derive_m30_bias` or the hard-gate logic
  in Phase 3. This task adds a *new metric* (stuck-box state); whether the
  hard-gate is downgraded vs the Wyckoff bias accuracy issue (separate
  Asana 1214320481337073, Opção 3 demote→shadow) is **out of scope**.

---

## 8. RECOMMENDATION FOR PHASE 2

Based on Phase 1 findings, recommended Phase 2 backtest scope:

| Option | Spec | Phase-2 priority |
|--------|------|------------------|
| **A** Excursion-based expiry | `expire if excursion > K × ATR`, K ∈ {2, 3, 4, 5} | **HIGH** — primary driver of incident |
| **B** Time-based expiry | `expire if duration > T h`, T ∈ {2, 4, 6, 8} h | MEDIUM |
| **C** Hybrid (A OR B) | Logical OR of A and B | HIGH (likely winner per p.r.) |
| **D** Decay function | `bias_confidence = exp(-λ × age)`; treat as unknown if < 0.5 | LOW (greenfield, no provenance, large param surface) |

Suggested gate metrics for Phase 2 ranking:
- **Bias accuracy gain** vs no-rule baseline (Δ percent_correct on 4h fwd)
- **Hypothetical trade-count delta** — how many BLOCK→GO conversions does the
  rule produce, and what is their hypothetical SL/TP outcome
- **R:R distribution** of unblocked trades
- **Subgroup robustness** — accuracy gain holds across low-vol / high-vol /
  trend / range regimes (Bonferroni-adjusted across 4×5 groups)

Bonferroni correction across 4 options × 5 K-values × 5 T-values × 2 regimes
= 200 hypothesis tests → α' = 0.05/200 = 0.00025 minimum threshold for
significance.

---

## 9. DELIVERABLES & STATUS

- ✅ `_audit/M30_BOX_STAGNATION_QUANTIFICATION.md` — this doc
- ✅ `_audit/m30_box_stagnation_quantify.py` — read-only quantification script
- ✅ `_audit/m30_box_stagnation_quantification.json` — raw numbers for Phase 2 input
- ✅ Literature gap explicitly documented (no canon prescription)
- ✅ Quantification: 416 stuck>4h boxes (60.8%), 170 with excursion>3×ATR (40.9%)
- ✅ Bias accuracy: weak edge below-box (1.43:1), no edge above-box
- ✅ Provenance: pre-existing `box_age_h` measures parquet freshness, NOT
  box-vs-price displacement → P1.3 rule is genuinely additive
- ⏸️ **GATE — awaiting Barbara / ML-DS approval to proceed to Phase 2**

**Phase 2 spec needed:** confirmation of (a) gate metrics, (b) k-fold purged
WF parameters (k=5, embargo=48 bars per Purdue 12-step), (c) which subset of
options A/B/C/D to backtest first (recommend A+C as priority).

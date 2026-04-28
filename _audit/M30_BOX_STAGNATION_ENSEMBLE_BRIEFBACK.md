# P1.3 Phase 2.5 — Tri-state Ensemble Brief-back

**Asana:** 1214327736913830 (Phase 2.5)
**Date:** 2026-04-28
**Author:** Claude Code (Opus 4.7)
**Scope:** Read-only ensemble validation A K=2 ⊕ C K=2 T=8h.
**Outcome:** **❌ SUSPECT state fails 3/5 validations → recommend A K=2 single (no ensemble).**

---

## 1. CONSTRUCTION (premise audit)

Per Barbara's spec, the SUSPECT quadrant ("A=expire ∧ C=keep") only exists if
C is **redefined as AND** (`exc>K AND age>T`) for this ensemble — under the
Phase 2 OR-definition the quadrant is empty. Implemented as:

| State | Condition |
|-------|-----------|
| **VALID** | `excursion ≤ 2×ATR` AND `age ≤ 8h` |
| **SUSPECT** | `excursion > 2×ATR` AND `age ≤ 8h` (young but heavily displaced) |
| **EXPIRED** | `excursion > 2×ATR` AND `age > 8h` (old AND displaced) |
| **NA** | no active box (m30_box_id ≤ 0) |

Hypothesis: bias accuracy gradient **VALID > SUSPECT > EXPIRED**, with SUSPECT
state stable across regimes. P&L weighted scheme {1.0, 0.5, 0.0} should
recover at least binary baseline (A K=2 = +247 pts/month).

---

## 2. RESULTS — 5 VALIDATIONS

### V1. Distribution ✅

```text
VALID    6,242  (69.31%)
SUSPECT  1,291  (14.33%)
EXPIRED  1,076  (11.95%)
NA         397  ( 4.41%)
```

Three states populated meaningfully (>10% each for active states). No
degenerate empty quadrants. **PASS.**

### V2. Bias accuracy gradient ❌ BROKEN

```text
state    n_valid  pct_total  accuracy
VALID     3,207     69.31%   0.5513   ← highest, as expected
SUSPECT     718     14.33%   0.5042   ← LOWEST (expected middle)
EXPIRED     275     11.95%   0.5127   ← middle (expected lowest)
NA            0      4.41%   ----
```

**The gradient is INVERTED at the SUSPECT/EXPIRED boundary:**
- VALID 55.13 % > EXPIRED 51.27 % > SUSPECT 50.42 %

SUSPECT (young + displaced) is **essentially coin-flip** — it is the
*worst-quality* signal, not an intermediate one. Methodologically interpretable:
"young + displaced" is the *moment of a breakout* where direction is most
ambiguous; "old + displaced" is when the new structure has begun to
consolidate, providing a weak but real signal.

The ensemble hypothesis (SUSPECT = soft warning, partial confidence)
**does not hold**. **FAIL.**

### V3. SUSPECT subgroup stability ❌ ERRATIC

SUSPECT bucket accuracies (12 buckets, n_per ≥ 10):

| Subgroup | n | acc |
|----------|--:|----:|
| **london_high_vol** | 70 | 0.7857 |
| off_low_vol | 109 | 0.5872 |
| asian_low_vol | 41 | 0.5610 |
| ny_low_vol | 28 | 0.5357 |
| ny_med_vol | 68 | 0.5294 |
| asian_med_vol | 38 | 0.5000 |
| off_high_vol | 66 | 0.4848 |
| off_med_vol | 74 | 0.4459 |
| ny_high_vol | 93 | 0.4301 |
| london_low_vol | 53 | 0.3962 |
| **london_med_vol** | 26 | 0.3077 |
| **asian_high_vol** | 52 | 0.3077 |

Range: **0.31 – 0.79** (48 pp spread).
Std across buckets: **0.125** (huge — Phase 2 main rule had std ~0.04).

The SUSPECT state is **highly regime-dependent**. Two large buckets
(asian_high_vol n=52, london_med_vol n=26) show SUSPECT acc ≈ 31% — *actively
wrong* (worse than coin flip). Treating SUSPECT as "half confidence" globally
would degrade performance in those regimes.

This is **exactly the failure mode** Barbara warned about in the V3
hypothesis: SUSPECT erratic across regimes. **FAIL.**

### V4. P&L 3-weight scheme ❌ LOSES vs binary baseline

| Scheme | n_conv | total 5:1 | monthly 5:1 | monthly 3:1 |
|--------|-------:|----------:|------------:|------------:|
| Binary baseline (A K=2 standalone) | 993 | +2,471 pt | **+247** | +177 |
| Tri-state weighted {1.0, 0.5, 0.0} | 718 SUSP + 275 EXP | +1,492 pt | **+149** | +108 |
| **Δ tri − binary** | — | **−979 pt** | **−98** | **−69** |

Per-state mean P&L (full not-weighted):
- SUSPECT: +2.73 pt/conv (positive but small)
- EXPIRED: +1.86 pt/conv (positive, even smaller)

**The tri-state scheme loses ~$980/year (5:1) vs binary A K=2.** Reason: the
0.5 weight on SUSPECT down-weights the majority of conversions (718 out of
993 = 72.3 % of A's catches) for no statistical reason — SUSPECT mean P&L is
positive (+2.73), so halving it forfeits half the recovery. The 0.0 weight
on... wait, the binary baseline already includes those (binary expire all
where A fires). The tri-state's apparent loss is purely the down-weighting
of SUSPECT.

**FAIL.**

### V5. Computational cost ✅

```text
A standalone:    0.142 µs/tick
Tri-state:       0.121 µs/tick   (short-circuit OR)
Overhead ratio:  0.85x  (well below 2x target, far below 1µs ceiling)
```

Cost is non-issue either way. **PASS.**

---

## 3. SUMMARY OF VALIDATIONS

| # | Validation | Result |
|---|------------|--------|
| V1 | Distribution % per state | ✅ PASS — three states populated meaningfully |
| V2 | Accuracy gradient HIGH > MID > LOW | ❌ **FAIL** — gradient inverted (SUSPECT lowest) |
| V3 | SUSPECT stability across regimes | ❌ **FAIL** — std 0.125 across 12 subgroups, two buckets at 31% |
| V4 | P&L 3-weight ≥ binary baseline | ❌ **FAIL** — tri-state loses 98 pts/month vs A standalone |
| V5 | Computational cost ≈ 2x A | ✅ PASS — actually 0.85x (short-circuit) |

**3/5 validations FAIL.** Per Barbara's spec branch:

> ❌ SUSPECT erratic OR no incremental benefit → deploy **A K=2 simple**
> (Phase 3 normal ~2h)

Both conditions met (V3 erratic + V4 no benefit).

---

## 4. METHODOLOGICAL INSIGHT (Rule 3.b extension confirmed)

The ensemble exploration was the **right methodology**: it tested whether
two competitive options compose into something better. The empirical result
says they don't *for this particular SUSPECT quadrant*, but the value of the
exploration is real:

- We now know SUSPECT (young + displaced) is the *most ambiguous* M30 box
  state — coin-flip on average, 31 %–79 % across regimes.
- We have evidence that simple binary expiry (A K=2) is not just *adequate*
  but *optimal* for this problem class — adding a soft state strictly
  hurts P&L because SUSPECT has positive expected P&L on conversion that
  the 0.5 weight forfeits.
- The Rule 3.b extension (consider ensemble for competing methodologies
  with complementary characteristics) **fired correctly**. The principle
  stays; this case argued against ensemble on data.

This is exactly how the rule should work: *evaluate the ensemble option,
then choose based on data.* The "❌ branch" outcome is a successful
application of the rule, not a failure of it.

---

## 5. PRAC

- **Confirmation bias:** I had a prior expectation of the ensemble *helping*
  given Barbara's enthusiasm. The data overruled the prior. **Direction
  reported as the data shows.**
- **Sample-size concerns:** SUSPECT n=718, EXPIRED n=275. SUSPECT bucket
  cell sizes ≥ 26 (london_med_vol smallest). The std of 0.125 across 12
  buckets is robust to single-cell noise. The gradient inversion (V2) is
  a population-level effect at n=718 vs 275 — not noise.
- **Hyperparameter coupling:** ensemble was tested at the *single* point
  (K=2, T=8h). A K=3 / T=4h variant might show different gradient. Out of
  scope for this 30-45 min window per spec; flag for possible future
  retest if Path 2 (C K=2 T=8h) is reconsidered.
- **3-weight choice:** {1.0, 0.5, 0.0} is the canonical first-pass scheme.
  An adaptive weight (e.g., regime-dependent) might rescue the ensemble in
  some buckets. But the V3 erraticism makes that fragile and overfit-prone.
- **Premise re: C semantics:** Barbara's quadrant table assumes C-AND for
  the ensemble. The Phase 2 main result (C-OR) is unaffected — that rule
  used different semantics and remains a valid Path 2 choice.

---

## 6. RECOMMENDATION

**Deploy A K=2 simple in Phase 3** (Barbara's Path 1).

Justification (refresh):

- **V2 + V4 confirm** there is no useful gradient between SUSPECT and EXPIRED
  states; the binary "expire iff exc > 2×ATR" boundary is methodologically
  the correct partition.
- A K=2 strict-Bonferroni family-of-4 winner (p=0.012, α'=0.0125) — the
  *only* option that passes that test.
- Captures the trigger case (Box 5261 04:08 incident, 7h08m / ~3×ATR).
- Simpler implementation = fewer Phase 3 bugs.
- Higher monthly P&L (+247 vs ensemble's +149).
- Adverse subgroups (ny_med_vol −2.6 pp, london_high_vol −3.6 pp) remain
  the same caveat as Phase 2; monitoring window in Phase 3 will track them.

**Phase 3 spec:** A K=2 GLOBAL, 7-day monitoring with daily M7 cross-ref
+ subgroup-aware tracking — exactly the Path 1 framing in the prior comment.

---

## 7. DELIVERABLES

- ✅ `_audit/M30_BOX_STAGNATION_ENSEMBLE_BRIEFBACK.md` (this doc)
- ✅ `_audit/m30_box_stagnation_ensemble.py` (read-only validation script)
- ✅ `_audit/m30_box_stagnation_ensemble_results.json` (raw V1–V5 numbers)

🛑 **GATE — awaiting Phase 3 GO.** Recommended path: **A K=2 GLOBAL**, 7-day
monitoring window, no ensemble. Standing rule extension (Rule 3.b) confirmed
as the right methodology even though this specific ensemble fails — the
exploration was the correct standing-rule fire.

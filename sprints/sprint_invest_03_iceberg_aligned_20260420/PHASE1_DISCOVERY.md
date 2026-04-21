# INVEST-03 Phase 1 — Discovery Report

**Date:** 2026-04-21
**Status:** Canonical `aligned` classifier located. No STOP-AND-ASK triggered.

---

## 1. Canonical assignment

**`ats_iceberg_gate.py:1037`** — inside `ATSIcebergV1._combine_signals()`:

```python
out.aligned = total_score > 0
```

Dataclass: `ATSIcebergSignal` (line 128-150) — `aligned: bool = False` as default.

## 2. Entry point

**`ats_iceberg_gate.py:186` — `ATSIcebergV1.check(entry_price, direction, level_type, now, window_minutes=10)`**

Called from `event_processor.py` (exact call site in Phase 3 code path trace) with:
- `direction`: 'LONG' or 'SHORT' — resolved BEFORE iceberg check by `_resolve_direction`
- `level_type`: 'liq_top' (SHORT setup) or 'liq_bot' (LONG setup)
- `entry_price`: the liq level price (M5/M30 box bound)
- `now`: entry timestamp UTC
- `window_minutes`: 10min back-scan

Each signal type (7 checks) produces a score; `_combine_signals` sums them; if `total_score > 0` → `aligned=True`.

## 3. Signal scoring — verbatim from docstring (ats_iceberg_gate.py:1-65)

The 7 signal types and their scores:

### TYPE 1 — Absorption (abs_detected AT level ±2pts)

| Context | bid/ask side | Score |
|---|---|---|
| AT liq_top (SHORT setup) | BID (buyers absorbed by sellers) | +3 SHORT aligned |
| AT liq_top (SHORT setup) | ASK (sellers absorbed by buyers) | −3 SHORT against |
| AT liq_bot (LONG setup) | ASK (sellers absorbed by buyers) | +3 LONG aligned |
| AT liq_bot (LONG setup) | BID (buyers absorbed by sellers) | −3 LONG against |

Ratio modifier: ≥10 → ×1.5; ≥5 → ×1.2; <2 → ×0.5.

### TYPE 2 — DOM Imbalance (abs ≥ 0.40)

| Context | dom direction | Score |
|---|---|---|
| AT liq_top (SHORT) | dom ≥ +0.40 (heavy bid = pushing against resistance) | +2 SHORT aligned |
| AT liq_top (SHORT) | dom ≤ −0.40 (heavy ask already winning) | +3 SHORT confirmed |
| AT liq_bot (LONG) | dom ≤ −0.40 (heavy ask = pushing against support) | +2 LONG aligned |
| AT liq_bot (LONG) | dom ≥ +0.40 (heavy bid already winning) | +3 LONG confirmed |

### TYPE 3 — Large Order Imbalance (abs ≥ 0.50), asymmetric v1.2

| Context | LOI sign | Score |
|---|---|---|
| AT liq_top (SHORT setup) | LOI > +0.50 (institutional BUY absorbed) | +1 SHORT aligned |
| AT liq_top (SHORT setup) | LOI < −0.50 (institutional SELL fleeing) | −2 SHORT contra |
| AT liq_bot (LONG setup) | LOI < −0.50 (institutional SELL absorbed) | +1 LONG aligned |
| AT liq_bot (LONG setup) | LOI > +0.50 (institutional BUY fleeing) | −2 LONG contra |
| NOT at level (fallback) | LOI > +0.50 | +2 LONG / −2 SHORT |
| NOT at level (fallback) | LOI < −0.50 | +2 SHORT / −2 LONG |

### TYPE 4 — JSONL Iceberg (prob ≥ 0.9150 from settings.json `iceberg_proxy_threshold`, refills ≥ 3)

| Context | Iceberg side | Score |
|---|---|---|
| AT liq_top (SHORT) | BID (institutional buy at resistance) | −4 AGAINST |
| AT liq_top (SHORT) | ASK (institutional sell at resistance) | +4 ALIGNED |
| AT liq_bot (LONG) | ASK (institutional sell at support) | −4 AGAINST |
| AT liq_bot (LONG) | BID (institutional buy at support) | +4 ALIGNED |

### TYPE 5 — Pressure Ratio (v1.3)

| Context | pressure_ratio | Score |
|---|---|---|
| AT liq_top (SHORT) | > 2.5 (buyers dominant) | −2 risky |
| AT liq_top (SHORT) | > 1.5 (buyers active) | −1 caution |
| AT liq_bot (LONG) | < 0.40 (sellers dominant) | −2 risky |
| AT liq_bot (LONG) | < 0.67 (sellers active) | −1 caution |

### TYPE 6 — Sweep Contra-Filter (v1.3) — **RELEVANT TO INCIDENT**

Code at `ats_iceberg_gate.py:573-588`:

```python
if level_type in ("liq_top", "liq_bot"):
    # At structural level: any sweep = exhaustion = ALIGNED (+1)
    score = +1
elif direction == "SHORT":
    if sweep_dir == "up":   score = -2   # upward sweep off-level = buyers still running
    elif sweep_dir == "down": score = +1  # downward sweep = sellers clearing stops
elif direction == "LONG":
    if sweep_dir == "down": score = -2   # downward sweep off-level = sellers still running
    elif sweep_dir == "up":  score = +1   # upward sweep = buyers clearing stops
```

**At liq_top/liq_bot structural level: ANY sweep direction → +1 ALIGNED**, based on the assumption "sweep at structural level = exhaustion". This is the **primary incident trigger** (6/8 entries had `iceberg sweep sc=+2`, sweep contributed +1 and another signal contributed ~+1 for total +2).

### TYPE 7 — Distance to POC (v1.3)

- `distance_to_poc > 20pts` (stretched far from POC) → +1 reversal strength
- `distance_to_poc < 5pts` (near POC) → −1 gravity risk

### Final combination (`_combine_signals`, line 962-1042)

- Sum scores of all 7 types with found=True
- Clamp `total_score` to [-9, +12] (line 1030)
- `out.aligned = total_score > 0` (line 1037)
- Confidence = max across all 7 signal confidences
- Hard blocks (absorption_hard_contra, loi_hard_contra, jsonl_contra) computed separately as boolean flags

## 4. Current logic in plain English (§2.3 of prompt)

> The V4 `aligned` classifier evaluates 7 microstructure signal types (absorption, DOM, LOI, JSONL iceberg, pressure, sweep, POC) at an ATS structural level (liq_top for SHORT or liq_bot for LONG). Each signal has a direction-and-level-aware score (e.g., ASK absorption at liq_top = +3 SHORT-aligned; ASK absorption at liq_bot = −3 LONG-contra). The classifier sums all found-signal scores into `total_score` (clamped [-9, +12]) and sets `aligned = (total_score > 0)`. It does NOT account for: [A] **session extreme context** (no tracking of session H/L, daily H/L, previous-day H/L — all signal evaluation is at STRUCTURAL liq_top/liq_bot only); [B] **Wyckoff phase** (no input for phase_b/c/d/e; same scoring applied universally); [C] **post-aggressive-move / Judas Swing context** (no session-range-aggressive detector — a sweep after a Judas rally gets the same +1 as a genuine seller exhaustion).

## 5. H1/H2/H3 testable hypotheses (§2.4 of prompt)

**H1 — Iceberg/sweep at session extreme vs mid-level.**
When the iceberg/absorption/sweep event occurs within X pts of session H or L (session extreme), absorption is a **reversal** signal (not continuation). Current classifier treats structural level = liq_top/liq_bot as the only context; does not check whether the liq level is also at session extreme.

- **Predicted failure pattern:** aligned=True at session extreme → negative fwd return in trade direction
- **Metric:** segment aligned=True events by `at_session_extreme` boolean (price within N×ATR of intraday session H/L)
- **Expected:** Cohen's d flips sign or becomes ≤ 0 for at_session_extreme=True vs at_session_extreme=False subgroup

**H2 — Counter-trend iceberg in Phase E.**
In Phase E (sustained trend), absorption against the HTF trend is a **reversal** signal (smart money fading the trend). Current classifier uses structural level but does not factor HTF trend direction:

- Example from INCIDENT: `daily_trend="short"` (Phase E Markdown) + price rallied to resistance (Judas Swing top) + ASK icebergs at top. Per TYPE 4 docstring, if the trade direction evaluated were SHORT at liq_top, ASK iceberg = +4 aligned (correct per literature). But system evaluated LONG at liq_bot, where no TYPE 4 ALIGNED was triggered — the +2 came from sweep (TYPE 6) + another signal.
- **Predicted failure pattern:** when `daily_trend ≠ trade_direction` and aligned=True via non-TYPE 4 signals (sweep, DOM, LOI aggregate), fwd return is negative
- **Metric:** segment aligned=True by (daily_trend vs trade_direction) alignment; sub-segment by primary_type

**H3 — Post-aggressive-move context (Judas Swing proxy).**
When session has moved > X × ATR in single direction since session open within last M minutes, subsequent icebergs at counter-direction structural level are Judas absorption (reversal), not continuation:

- **Predicted failure pattern:** aligned=True during/after aggressive move has negative fwd return
- **Metric:** compute `session_range_aggressive = (|session_high - session_open| > X×ATR) within last M min`; segment aligned=True by this boolean
- **Expected:** aligned=True performs worse in post-aggressive context

## 6. Gaps in current classifier (causal map)

| Input the classifier uses | Input the classifier MISSING | Literature expectation |
|---|---|---|
| level_type (liq_top / liq_bot) | session extreme position | absorption at extreme = reversal |
| direction (LONG/SHORT) | HTF trend (daily_trend) | counter-trend absorption = reversal |
| 10-min window microstructure | session-range aggressive detector | Judas context overrides continuation |
| structural level ±2pts band | Wyckoff phase | Phase E + counter-trend entry = forbidden |

## 7. Consumer consequences

- `event_processor.py:946-949` — `aligned` fed into `v4_iceberg.aligned` gate dict → downstream decision logic
- `event_processor.py:3087, 3225` — `_detect_local_exhaustion`: if `detected AND NOT aligned AND conf > 0.5` → block continuation entry (this is the HARD BLOCK path). When aligned=True, this block never fires.
- Memory context (per Barbara's earlier note): **aligned=True affects execution — bigger lot size or NOT OPEN** if contra. Mis-labelling as aligned = signal that should block gets executed.

## 8. Phase 1 completion — no STOP-AND-ASK triggered

- ✓ Canonical `aligned` assignment located
- ✓ Iceberg JSONL schema known (prob, refills, side, type, refill_count per docstring)
- ✓ L2 source coverage documented (Databento Jul 2025–25/11/2025; dxFeed 26/11/2025–live per memory #12). Phase 2 will verify empirically.
- ✓ Classifier logic documented in plain English (§4)
- ✓ H1/H2/H3 hypotheses formulated (§5) — testable in Phase 3 using existing iceberg JSONL + OHLCV

**Proceeding to Phase 2** (L2 coverage verification + iceberg JSONL inventory + regime tagging reuse from INVEST-01).

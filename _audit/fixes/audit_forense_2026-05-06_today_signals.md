# Audit Forense — Today's Signals (2026-05-06)

**Author**: CC#3
**Date**: 2026-05-06 ~22:00 UTC
**Asana**: 1214556369070092 → ML-DS comments 1214590407540485 (audit GREEN-LIGHT) + 1214590485262208 (specs)
**Type**: STEP 7 forensic audit (read-only data analysis)
**Source**: `C:\FluxQuantumAI\logs\decision_log.jsonl` + `C:\data\processed\gc_ohlcv_l2_joined.parquet`
**Script**: `_audit/fixes/audit_today_signals.py`

---

## Executive summary

Today (2026-05-06) FluxQuantumAPEX emitted **470 CONFIRMED signals**: 158 LONG + 312 SHORT.

- **Morning burst (00:00–04:51 UTC)**: 312 SHORTs during +91pts bullish rally — **0% TP1 hit, 100% SL hit at 4h horizon, mean -31.45pts**. Catastrophic counter-trend. The bug Barbara reported.
- **Daytime (07–17h UTC)**: 158 LONGs — modest performance, mixed afternoon (12–18 UTC was bearish).
- **F-asymmetric counterfactual**: would have blocked **312/312 SHORTs (100%)** + 6/158 LONGs (3.8%). Net P&L impact: **+480pts saved** (turns -245pts → +234pts at TP1=SL=5pts cap).
- **System now LIVE on commit f17bc49** with cascade + F-asymmetric since 21:50 UTC. Feed currently DEAD (separate concern).

---

## 1. LONGs CONFIRMED outcomes (TP1/SL/BE)

**n = 158** (all post-rally; 07h–20h UTC)

| Horizon | mean | TP1 (≥+5pts) | SL (≤–5pts) |
|---------|------|--------------|-------------|
| 30 min | +5.24 pts | 82/156 (52.6%) | 42/156 (26.9%) |
| 4 h | +14.37 pts | 78/130 (60.0%) | 28/130 (21.5%) |

**Verdict**: LONG signals decent on 4h horizon (60% TP1, 21% SL). 30min horizon is more noisy (52.6% TP1 vs 26.9% SL = ~2:1 win/loss ratio). Net positive expectancy.

**Per hour**:

| Hour | LONG count |
|------|-----------|
| 07h | 42 |
| 08h | 14 |
| 10h | 26 |
| 14h | 28 |
| 15h | 20 |
| 17h | 26 |
| 20h | 2 |

---

## 2. SHORT opportunity afternoon 12:00–18:00 UTC

**Barbara observation**: afternoon had a SHORT opportunity but system fired LONGs (counter-trend).

**Data confirms**:

| Window 12:00–18:00 UTC | Count | fwd30 mean | TP1 30m | SL 30m | fwd4h mean | TP1 4h | SL 4h |
|-----------------------|-------|------------|---------|--------|------------|--------|-------|
| 74 LONG signals (zero SHORT) | 74 | **−4.64 pts** | 2/74 (2.7%) | 42/74 (**56.8%**) | −12.46 pts | 0/48 (0%) | 28/48 (58.3%) |

**Verdict**: Barbara was right. Market was bearish 12–18 UTC; system emitted 74 LONGs (counter-trend) with 56.8% SL hit rate at 30min and ZERO TP1 at 4h. Mean −12.46pts at 4h. Symmetric inversion bug — same architectural defect as the morning SHORT burst, opposite direction.

**Cascade implication**: cascade resolution was BULLISH during 12–18 UTC because `m30_bias_confirmed` was still bullish from morning rally. Afternoon bearish flip lagged. Layer 3 (m30_confirmed) was thus stale during early afternoon. Layer 4 (provisional) likely also bullish.

---

## 3. F-asymmetric counterfactual replay

Per spec, F-asymmetric blocks counter-trend mean-reversion in RANGE_BOUND. Applied to today's 470 signals:

| Direction | Total | Blocked by F-asym | Pass-through |
|-----------|-------|-------------------|--------------|
| LONG | 158 | **6 (3.8%)** | 152 (96.2%) |
| SHORT | 312 | **312 (100%)** | 0 |

**Cascade Layer activation per signal**:

| Layer | Count | % |
|-------|-------|---|
| Layer 4 provisional (LOW) | 312 | 66.4% |
| Layer 3 m30_confirmed (MEDIUM) | 152 | 32.3% |
| Layer 2 tick_breakout (HIGH) | 6 | 1.3% |
| Layer 1 daily_trend (HIGH) | 0 | 0% (all unknown today) |

**Layer 1 zero coverage today** — daily_trend was "unknown" the entire day (B+C ENSEMBLE never agreed). All blocking happened via Layer 3/4.

**Phase distribution**:

| Phase | Count |
|-------|-------|
| EXPANSION | 386 (82%) |
| CONTRACTION | 84 (18%) |

---

## 4. Net P&L impact

**Methodology**: per signal, assume entry at signal price + closure within 30min, capped at TP1=+5pts and SL=−5pts. Simulates Barbara entering every emitted signal manually with disciplined exits.

| Scenario | Entries | Net P&L (pts) |
|----------|---------|---------------|
| **Today as emitted (no F-asym)** | 254 (with fwd data) | **−245 pts** |
| **Today with F-asymmetric live** | 150 allowed | **+234 pts** |
| **P&L SAVED by F-asymmetric** | (318 signals blocked) | **+480 pts** ✅ |

**Interpretation**: F-asymmetric flips today from a losing day (−245pts) to a winning day (+234pts) — a swing of +480pts on lot=1. At 0.05 lot live (Barbara's calibrated), would translate to +24pts × $5/0.01lot = **~+$1,200 hypothetical saved** today.

**Caveats**:
- Cap at ±5pts is conservative; real TP2 hits at +20pts would amplify further but SL also caps real loss
- This assumes Barbara would have entered EVERY signal (she didn't — manual gatekeeper)
- Actual saving depends on Barbara's selection rate

---

## 5. Design implication — Strict Wade vs Confidence-tiered

### Current design (deployed in f17bc49)

F-asymmetric is **strict Wade**: blocks any counter-trend mean-reversion when ANY layer resolves a directional bias, regardless of confidence (HIGH/MEDIUM/LOW all trigger filter).

### Observed behavior today

- 312 SHORTs blocked, ALL via Layer 4 LOW provisional (66.4% of all activations)
- Layer 4 LOW provisional was bullish during morning burst → blocks counter-trend SHORTs (correct)
- Layer 4 LOW provisional was still bullish in afternoon → did NOT block counter-trend LONGs (because LONGs aligned with stale-bullish provisional)

**Strict Wade is asymmetric in effect today**: caught morning catastrophe (SHORTs vs bull) but did NOT catch afternoon mistake (LONGs vs bear). Reason: cascade itself lagged in afternoon (m30_confirmed + provisional both stayed bullish into the bearish afternoon).

### Confidence-tiered alternative (decision input)

Variant A — **strict Wade** (current): block on any resolved confidence.
Variant B — **HIGH/MEDIUM only**: block only when L1/L2/L3 resolves; LOW provisional does NOT trigger filter.
Variant C — **HIGH only**: block only when L1/L2 resolves (most conservative; protects against false LOW provisional).

**Counterfactual on today**:

| Variant | SHORTs blocked | LONGs blocked | Net P&L impact |
|---------|----------------|---------------|----------------|
| **A (strict — deployed)** | 312/312 | 6/158 | +480pts saved |
| B (HIGH/MEDIUM) | 0/312 (all via Layer 4 LOW) | 6/158 | ZERO saved (Layer 3 m30_confirmed was stale during afternoon) |
| C (HIGH only) | 0/312 | 0/158 (L2 only fired 6 times) | ZERO |

**Today's data favors strict Wade overwhelmingly**. But:
- Today is single sample (n=1 day)
- Variant A may over-block on truly range-bound days where provisional flips noisily

### Trade-off summary

| Approach | Catches morning bull-rally inversion | Catches afternoon bear flip | Risk |
|----------|--------------------------------------|----------------------------|------|
| Strict Wade (A) | ✅ YES (312/312) | ❌ NO (cascade itself lagged) | Over-blocks on ambiguous days |
| HIGH/MEDIUM only (B) | ❌ NO (all via LOW provisional) | ❌ NO | Under-blocks on noisy days |
| HIGH only (C) | ❌ NO | ❌ NO | Same as no filter today |

**No variant catches afternoon flip without cascade-level fix** (need m30_bias to flip faster in bearish reversal — separate concern, possibly Layer 2 TickBreakoutMonitor BREAKOUT_DN reconnect issue or m30_updater hysteresis).

### Recommendation (decision input only)

**Keep strict Wade (Variant A) deployed**. It:
- ✅ Catches the morning catastrophic bug 100%
- ✅ Conservative default; ML-DS approved
- ⚠️ Over-blocks risk needs production observation to validate
- ❌ Does not catch all forms of bias lag (e.g., cascade itself stale)

**Future iterations** (post-validation period):
1. **Confidence weighting**: reduce LOW provisional weight on days with low realized volatility (where range-bound is more likely) — adaptive, not yet calibrated
2. **Layer 2 health check**: investigate why TickBreakoutMonitor only fired 6 times today (1.3%) when feed-bound it should fire more during EXPANSION breakouts
3. **m30 hysteresis review**: investigate why afternoon m30_confirmed didn't flip bearish despite price drop

These are SEPARATE follow-ups, not blockers for current deploy.

---

## Final verdict

**Today's signals had 470 emissions; F-asymmetric blocks 318 (67.7%) and saves +480pts hypothetical P&L.**

The deployed fix (commit f17bc49) is the right solution for the morning catastrophic bug. It does NOT solve all forms of bias lag (afternoon bear flip not caught), but **for the specific bug Barbara reported (200+ counter-trend SHORTs in bull rally), it is 100% effective**.

Layer 4 provisional dominates today (66.4%). This is not necessarily a problem — Layer 4 is designed as fallback when Layer 1 (B+C ENSEMBLE) fails to converge, which is exactly today's case (daily_trend stayed "unknown" all day).

---

## Companion data

- `_audit/fixes/audit_today_signals.py` — script (re-runnable for any day)
- `_audit/fixes/2026-05-06_deploy_validation.md` — deploy validation
- `_audit/fixes/range_market_validation_2026-05-06.md` — pre-deploy range validation
- `_audit/fixes/bug_cascade_direction_fallback_spec.md` — design spec

## Constraints honored

- READ-ONLY scope (logs + parquet only)
- ZERO mutation
- ZERO design proposal beyond decision-input ranking
- ZERO ATS Trend Line / NextGen / TradeATS / CC#1 / CC#2 references

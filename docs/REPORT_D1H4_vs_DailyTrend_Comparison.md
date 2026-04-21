# D1/H4 Bias vs Daily Trend Proxy — Comparison Report

**Date:** 2026-04-14
**Coverage:** 214 trading days (Jul 2025 - Apr 2026)
**Method:** Runtime D1/H4 box detection from M1 vs M30 FMV resample proxy
**Authored by:** Claude AI + Barbara (FluxQuantumAI)

---

## 1. Context

The current `daily_trend` (used for TRENDING mode, GAMMA, DELTA, PATCH2A) is derived
from M30 FMV resampled to D1 — a proxy, not real D1/H4 candle structure. FASE 4a built
a runtime D1/H4 bias engine using the same box detection algorithm on actual H4 and D1
bars (session-aligned at 22:00 UTC / 17:00 ET).

## 2. Concordance

| | Days | % |
|---|---|---|
| Agrees | 111 | 51.9% |
| Diverges | 103 | 48.1% |

The proxy and D1/H4 runtime disagree nearly half the time.

## 3. Divergence Breakdown

| Type | Days | Meaning |
|------|------|---------|
| proxy=short, d1h4=long_WEAK | 38 | Proxy bearish, D1 says bullish but H4 diverges |
| proxy=short, d1h4=long_STRONG | 21 | Proxy bearish, D1+H4 both bullish |
| proxy=long, d1h4=short_WEAK | 18 | Proxy bullish, D1 says bearish |
| proxy=unknown, d1h4=defined | 21 | Proxy blind, D1H4 has answer |
| proxy=long, d1h4=short_STRONG | 5 | Proxy bullish, D1+H4 both bearish |
| Other | 0 | |

## 4. UNKNOWN Analysis

- D1H4 produces UNKNOWN: **0 days (0.0%)** — always has a bias
- Proxy produces UNKNOWN: **21 days (9.8%)** — blind ~10% of the time

D1H4 is strictly more decisive than the proxy.

## 5. Operational Impact (FASE 4b Estimate)

| Component | Current (proxy) | FASE 4b (D1H4) |
|-----------|----------------|-----------------|
| TRENDING available | 90.2% of days | 100% of days |
| Direction flips | — | 82 days would change direction (38%) |
| GAMMA/DELTA (STRONG only) | Always active | Blocked 44% of days (WEAK) |
| UNKNOWN days | 9.8% | 0% |

## 6. Direction Flips: 82 Days

In 82 out of 214 days, the system would have operated in the **opposite direction**.
This is not a minor adjustment — it is a structural change that requires PnL validation.

### Recent examples:

| Date | Proxy | D1H4 | D1 JAC | H4 JAC |
|------|-------|------|--------|--------|
| 2026-03-09 | short | LONG_WEAK | long | short |
| 2026-03-20 | long | SHORT_STRONG | short | short |
| 2026-03-27 | long | SHORT_WEAK | short | long |
| 2026-04-02 | short | LONG_STRONG | long | long |

## 7. GAMMA/DELTA Impact

If FASE 4b required STRONG for GAMMA/DELTA:
- STRONG days: 120 (56.1%) — GAMMA/DELTA active
- WEAK days: 94 (43.9%) — GAMMA/DELTA blocked
- This may be too restrictive. Consider: WEAK allows PULLBACK but blocks CONTINUATION/GAMMA.

## 8. Conclusions

1. **The proxy is structurally unreliable.** 48% divergence rate means the system
   operates with wrong directional bias nearly half the time.

2. **D1H4 is more decisive.** 0% UNKNOWN vs 10% for the proxy.

3. **82 direction flips is high-impact.** Cannot activate without PnL backtest.

4. **GAMMA/DELTA STRONG-only needs calibration.** 44% blocked may be excessive.

## 9. Decision

- FASE 4a shadow: ACTIVE (D1H4 updater disabled from daemon for perf, bias readable from last standalone run)
- FASE 4b activation: NOT APPROVED — requires formal backtest (PF/PnL/DD comparison)
- Execution order: FASE 3 (displacement) first, then FASE 4b backtest, then activation decision

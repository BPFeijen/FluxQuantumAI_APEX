# FASE 4b Backtest: D1/H4 Bias vs Daily Trend Proxy

**Date:** 2026-04-14
**Coverage:** 9 months (Jul 2025 - Apr 2026), 8,476 M30 bars
**Method:** Simulated pullback trades at M30 liq levels with SL=20pts TP1=20pts
**Authored by:** Claude AI + Barbara (FluxQuantumAI)

---

## 1. Results

| Metric | PROXY (current) | D1H4 (proposed) | Delta |
|--------|----------------|-----------------|-------|
| Trades | 3,077 | 3,290 | +213 |
| **Profit Factor** | **1.632** | 1.175 | **-0.457** |
| **PnL (pts)** | **11,052.65** | 3,950.55 | **-7,102.10** |
| Max Drawdown | 665.10 | 872.50 | +207.40 |
| Win Rate | 60.1% | 53.6% | -6.5% |
| Avg Win | 15.45 | 15.04 | -0.41 |
| Avg Loss | -14.24 | -14.75 | -0.51 |
| Long trades | 1,905 | 2,677 | +772 |
| Short trades | 1,172 | 613 | -559 |
| Long PnL | 7,377.35 | 4,446.15 | -2,931.20 |
| **Short PnL** | **3,675.30** | **-495.60** | **-4,170.90** |

## 2. Divergence Analysis

529 trades where bias sources disagreed on direction:

| Metric | Value |
|--------|-------|
| Proxy better | 308 (58.2%) |
| D1H4 better | 216 (40.8%) |
| Proxy PnL on diverged | +1,502.30 |
| D1H4 PnL on diverged | -1,702.30 |

## 3. Exclusive Trades

| Source | Exclusive trades | PnL |
|--------|-----------------|-----|
| Proxy only | 791 | +2,420.30 |
| D1H4 only | 1,004 | -1,477.20 |

## 4. Key Finding: D1H4 Shorts Are Negative

The D1H4 bias produces SHORT PnL of -495.60 (negative) vs the proxy's +3,675.30.
The D1 JAC direction is too slow to capture intraday reversals. When D1 says "long",
D1H4 avoids short entries — but many of those shorts were profitable pullback trades
that the proxy correctly identified.

## 5. Why D1H4 Underperforms

1. **D1 is too stable.** D1 JAC changes ~2x per year (204-day average persistence).
   This means in a multi-week uptrend, D1H4 blocks ALL shorts — including profitable
   sell-the-rally pullbacks at liq_top.

2. **The proxy is more adaptive.** M30 FMV resampled to D1 reacts to 2-3 day structure,
   not multi-month trends. This captures regime changes faster.

3. **D1H4 over-concentrates in one direction.** 2,677 longs vs 613 shorts (4.4:1 ratio)
   vs proxy's 1,905:1,172 (1.6:1). Over-concentration increases drawdown.

## 6. Decision

**FASE 4b: NOT APPROVED FOR ACTIVATION.**

The proxy, despite being methodologically "impure" (M30 FMV resample, not real D1/H4),
produces significantly better trading results:
- PF 1.63 vs 1.18
- PnL +11K vs +4K
- DD 665 vs 873
- Win rate 60% vs 54%

The current daily_trend proxy will remain as the bias source.

## 7. Implications

- The D1H4 bias engine remains as shadow (observable, not actionable)
- Future work: consider a hybrid approach — D1H4 for trend confirmation,
  proxy for execution direction
- The STRONG/WEAK classification may still be useful for GAMMA/DELTA gating
  even if the direction comes from the proxy

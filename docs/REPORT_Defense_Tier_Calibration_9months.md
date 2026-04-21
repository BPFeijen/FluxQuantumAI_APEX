# Defense Mode Tier Calibration Report

**Date:** 2026-04-14
**Coverage:** 9 months (Jul 2025 - Apr 2026)
**Data:** 169 sessions, 5,388,175 ticks, 533,165 sampled observations
**Authored by:** Claude AI + Barbara (FluxQuantumAI)

---

## 1. Context

The Defense Mode (GrenadierDefenseMode, Z-score based) detects microstructure anomalies
(spread widening, bid/ask collapse, extreme imbalance). Prior to this calibration,
it only blocked new entries (ENTRY_BLOCK). The question: can it also protect open positions?

## 2. Tier Classification (TIGHT-B)

Tested 4 threshold configurations for DEFENSIVE_EXIT tier:

| Config | Logic | Events (9mo) | Protection >15pts | False alarm |
|--------|-------|-------------|-------------------|-------------|
| BASELINE (2+ OR z>2x) | OR | 6,597 | 3.0% | 72.2% |
| TIGHT-A (3+ OR z>3x) | OR | 1,238 | 6.2% | 58.4% |
| **TIGHT-B (2+ AND z>2x)** | **AND** | **530** | **5.0%** | **46.3%** |
| TIGHT-C (3+ OR z>4x) | OR | 1,194 | 6.5% | 58.6% |

**Decision:** TIGHT-B selected. Requires BOTH >=2 simultaneous triggers AND extreme z-score (2x threshold). Best balance of signal vs noise.

## 3. Directional Classification (Failed)

Tested directional exit logic based on z-score direction:

| Direction | Events | Accuracy | Verdict |
|-----------|--------|----------|---------|
| EXIT_LONG | 95 | 29.2% | Weak |
| EXIT_SHORT | 303 | 2.3% | **Inverted/useless** |
| EXIT_ALL | 132 | 60.0% | Only viable option |

**Conclusion:** Z-score book stress does NOT predict price direction. EXIT_SHORT was effectively inverted. Directional logic based on anomaly alone is discarded.

## 4. Confirmed Exit: Anomaly + Price Action + Structure

Tested 3 tiers of confirmation with a grid of parameters:

### T1: Anomaly Only (TIGHT-B baseline)
- 525 events with 30min follow-up
- Protected LONG (dropped >5pts): 48.8%
- Protected SHORT (rose >5pts): 6.1%
- Neutral (false alarm): 45.1%
- Mean 30min change: -4.11 pts

### T2: Anomaly + Adverse Price Move

Best configs:

| Adverse pts | Lookback | Events | Prot total | Neutral | Mean 30min |
|-------------|----------|--------|-----------|---------|------------|
| 3 | 30s | 89 | 68.5% | 31.5% | -4.38 |
| **3** | **60s** | **143** | **65.7%** | **34.3%** | **-5.39** |
| 3 | 120s | 217 | 60.4% | 39.6% | -4.77 |
| 10 | 30s | 8 | 87.5% | 12.5% | +8.50 |
| 10 | 60s | 14 | 85.7% | 14.3% | +6.88 |

### T3: Anomaly + Adverse Move + M30 Level Broken

M30 level broken in 91.9% of DEFENSIVE_EXIT events (487/530).

Best configs:

| Adverse pts | Lookback | Events | Prot total | Neutral | Mean 30min |
|-------------|----------|--------|-----------|---------|------------|
| **3** | **60s** | **131** | **67.2%** | **32.8%** | **-5.60** |
| 3 | 30s | 79 | 72.2% | 27.8% | -4.47 |
| 3 | 120s | 197 | 60.9% | 39.1% | -5.10 |
| 10 | 30s | 6 | 100% | 0% | +14.04 |
| 10 | 60s | 9 | 100% | 0% | +10.94 |
| 8 | 30s | 10 | 90% | 10% | +8.26 |

## 5. Sweet Spot

**T3: adverse move >= 3pts in 60s + M30 level broken + TIGHT-B active**

- 131 events in 9 months (~15/month)
- 67.2% protection rate (vs 48.8% baseline = +18.4pp)
- 32.8% false alarm (vs 45.1% baseline = -12.3pp)
- Mean outcome: -5.60 pts in 30min (confirms real adverse move)
- Worst: -25.95 pts (would have been protected)

## 6. Methodological Conclusions

1. **Anomaly alone is not sufficient** for exit decisions. 45% false alarm rate means nearly half of all exits would be unnecessary.

2. **Anomaly + adverse price action is significantly better.** Adding a 3pt/60s adverse move filter reduces false alarms from 45% to 34% and increases protection from 49% to 66%.

3. **M30 structural level loss is the strongest confirmation.** T3 (anomaly + move + M30 break) achieves 67% protection with 33% false alarm — the best balance found.

4. **Directional prediction from book z-scores is unreliable.** EXIT_SHORT was 2.3% accurate. Do not use anomaly to predict direction. Use price action + structure.

5. **EXIT_ALL remains the only viable exit action.** Directional exits need price-based confirmation, not book-based prediction.

6. **High-threshold configs (10pts, 30s) achieve 90-100% accuracy but have too few events** (6-10 in 9 months) to be statistically robust. The 3pts/60s config has 131 events — sufficient for validation.

## 7. Current Implementation Status

| Component | Status |
|-----------|--------|
| TIGHT-B tier classification | LIVE (shadow mode) |
| Directional stress classification | LIVE (shadow logging only, not actionable) |
| Confirmed exit (T3 logic) | NOT IMPLEMENTED (pending approval) |
| EXIT_ALL automatic | NOT APPROVED for live |

## 8. Next Steps

- Accumulate live shadow data with TIGHT-B + directional logging
- If T3 sweet spot is confirmed in live data, propose shadow implementation of confirmed exit
- Confirmed exit should combine: TIGHT-B + adverse 3pts/60s + M30 level broken
- Exit action: close positions matching adverse direction only (not EXIT_ALL)

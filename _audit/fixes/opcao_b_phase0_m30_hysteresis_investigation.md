# Opção B Phase 0 — m30_bias hysteresis investigation

**Author**: CC#3
**Date**: 2026-05-07 ~07:50 UTC
**Asana**: 1214556369070092 → ML-DS comment 1214598376829822 (Opção A+B sequencial)
**Type**: READ-ONLY investigation
**Predecessor**: Opção A deployed (commit `ada8e9d`) — TRENDING_BIAS_BLOCK live

---

## 1. Question being investigated

Why did `m30_bias_confirmed` flip to **bearish** at 04:00 UTC during a transient pullback (~9pts) and stay bearish for ~2 hours while market rallied +50pts back up?

ML-DS hypothesis: hysteresis lag at the M30 box level. **Investigation finds: not classical hysteresis lag — root cause is M30 box renewal cycle + bear_ext-only classification on a single transient pullback box.**

---

## 2. Empirical M30 box trajectory today (2026-05-07)

Read from `C:\data\processed\gc_m30_boxes.parquet`:

| Box ID | Time (UTC) | Bars | box_high | box_low | liq_top | liq_bot | bull_ext? | bear_ext? | _classify |
|--------|-----------|------|----------|---------|---------|---------|-----------|-----------|-----------|
| 5281 | 00:00–03:30 | 8 | 4708.9 | 4699.3 | 4730.5 | 4694.0 | ✅ (4730.5 > 4708.9) | ✅ (4694.0 < 4699.3) | **unknown** (both true) |
| **5282** | **04:00–05:00** | **3** | 4718.6 | 4707.8 | 4718.6 | 4700.4 | ❌ (4718.6 = 4718.6) | ✅ (4700.4 < 4707.8) | **bearish** |
| 5283 | 05:30–06:00 | 2 | 4709.8 | 4700.4 | 4726.6 | 4700.4 | ✅ (4726.6 > 4709.8) | ❌ (4700.4 = 4700.4) | **bullish** |
| 5279 | 06:30→now | active | 4709.3 | 4691.0 | 4760.0 | 4689.8 | ✅ | ✅ | unknown |

(Note: Box 5279 has a lower ID than 5283 — likely the M30 updater started a new cycle indexed against historical box numbering after a phase reset. Out of scope for today.)

---

## 3. Root cause of 287 SHORTs at 04–05h UTC

**Box 5282 (04:00–05:00, only 3 confirmed bars)** is the smoking gun:

- price entered the bar at 4707.7 (off the prior box top)
- pullback dragged liq_bot to 4700.4 (8.4pts below box_low=4707.8) → `bear_ext = True`
- price never reached above box_high=4718.6 in the same box → `bull_ext = False`
- `_classify(box 5282) = "bearish"`
- box confirmed at 04:30 → `m30_bias_confirmed = "bearish"` from 04:30 onwards

This is **"bearish on a single short transient box"** — not lagging hysteresis. The classifier gave bearish on a pullback that lasted 1.5h within a much larger bullish day.

m30_bias remained bearish until **box 5283** formed at 05:30 with bull_ext=True only → flipped bullish at 06:00.

**Lag = ~2h (04:30 → 06:30 effective in cascade) but caused by box turnover, not hysteresis dwell timer.**

---

## 4. Logic walk-through (`level_detector.py:525-614`)

```python
def derive_m30_bias(m30_df, confirmed_only=False):
    def _classify(row):
        bull_ext = liq_top > box_high
        bear_ext = liq_bot < box_low
        if bull_ext and not bear_ext: return "bullish"
        if bear_ext and not bull_ext: return "bearish"
        return "unknown"
    
    confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
    if not confirmed.empty:
        last_confirmed = confirmed.iloc[-1]   # only most recent confirmed box
        confirmed_bias = _classify(last_confirmed)
        structural_now = _price_vs_box_bias(latest_row, current_gc)
        if confirmed_bias in ("bullish", "bearish"):
            if structural_now != "unknown" and structural_now != confirmed_bias:
                return "unknown", False  # invalidate on structural disagreement
            else:
                return confirmed_bias, True
```

**Findings**:
1. Only the **most recent confirmed box** is consulted (`confirmed.iloc[-1]`). No multi-box context.
2. Live structural override (lines 599–607): only invalidates confirmed bias when current GC price clearly above/below the active box. During 04:30–05:30, price was inside box 5282's range (4700–4719) → no invalidation → bearish persisted.
3. No hysteresis dwell timer found — flip happens immediately on new box confirmation.
4. No min-bars-confirmed criteria: **box 5282 had only 3 bars confirmed** but was treated identically to box 5281 (8 bars confirmed).

---

## 5. Connection to commit `32343cf` (m30_updater excursion fix)

The `32343cf` commit (Apr 28, "m30_updater: fix m30_liq_bot/m30_liq_top to track true excursion during box life") changed `_detect_boxes` to forward-fill `liq_top`/`liq_bot` with each bar's true high/low during box life. Per its commit msg:

> The pre-fix classifier said "no bearish extension" while price had already moved -33pts below the box for 7 hours. Box 5259 now shows m30_liq_bot = 4681.2 (matches true min).

This fix is correct for capturing genuine bearish extensions. **But its side effect: any pullback within a box gets baked in as bear_ext, even if the broader trend is bullish.**

The commit msg even notes:
> With the fix, ~46% of confirmed boxes now have BOTH bull_ext AND bear_ext set (indecisive); existing _classify returns "unknown" for these.

So 46% indecisive is expected. But **Box 5282 today only had bear_ext** (no bull_ext during its brief 3-bar life) → pure bearish classification despite the bigger context.

---

## 6. Five candidate fixes (input for Opção B spec — NOT implementation)

### F-1 — Min bars confirmed before bias accepted

Reject confirmed bias from boxes with fewer than N bars (e.g., N=4–6). Box 5282 (3 bars) would have been rejected → fall through to provisional or unknown.

**Pros**: simple, calibratable.
**Cons**: introduces lag at start of genuine new boxes; needs calibration.

### F-2 — Sequential excursion ordering

Track whether bull_ext or bear_ext came FIRST during box life. If price made bull_ext THEN later bear_ext → counts as "bullish-resolved-then-pullback" (≠ pure bearish).

**Pros**: more nuanced; respects time order.
**Cons**: requires more state per box; bigger refactor in `_detect_boxes`.

### F-3 — Multi-box voting

Consider last M confirmed boxes (not just one). Majority + recency-weighted vote. Today: box 5281=unknown, box 5282=bearish, box 5283=bullish — 2/3 not bearish → not bearish.

**Pros**: smooths transients; widely-used pattern.
**Cons**: adds another lag dimension; needs M calibration.

### F-4 — Provisional override on structural disagreement

When confirmed=bearish but provisional=bullish AND price has rallied since box close → use provisional. Today: box 5282 closed at 05:00 with bias=bearish, but provisional turned bullish by 05:30 — would have flipped sooner.

**Pros**: faster response; reuses existing provisional signal.
**Cons**: weakens "confirmed > provisional" guarantee; risks noise.

### F-5 — Direction-relative excursion threshold

Require liq_top excursion of ≥X% of ATR for bull_ext (and same for bear_ext). Today's box 5282 had liq_bot 7.4pts below box_low; with M30 ATR ~25pts, that's 30% of ATR — significant. But borderline. Require ≥40% would have rejected.

**Pros**: rejects shallow noise excursions.
**Cons**: needs calibration; misses small but valid breakouts.

---

## 7. Calibration data status

Searched `_audit/calibrations/` and `sprints/`:

- No calibration data specifically for M30 hysteresis windows
- 32343cf commit msg mentions backtest where the fix changed Cohen's d behavior (UTAD_AWARE patch's edge over CURRENT compresses 5.4pp → 1.8pp post-fix; loses Bonferroni significance) — but no segmented-by-regime data for hysteresis specifically
- INVEST-01 CAL-03 report (`sprints/sprint_invest_01_cal03_20260420/INVEST_01_REPORT.md`) is for `delta_4h_inverted_fix`, not m30_bias hysteresis
- `_audit/calibrations/m30_box_stagnation_*.json` contains box stagnation calibration but not bias stability

**Conclusion**: any fix to m30_bias hysteresis is currently uncalibrated. Spec must include a calibration phase.

---

## 8. Recommendation for Opção B spec phases

**Phase 1 — Spec design** (~30 min):
- Pick fix variant (CC#3 leans F-1 + F-3 combined: min-bars threshold + 3-box voting)
- Define new threshold keys + defaults (conservative: `m30_bias_min_bars=4`, `m30_bias_voting_window=3`)
- Define test plan
- ML-DS review

**Phase 2 — Calibration** (~1–2 h):
- Use historical decision_log + gc_m30_boxes parquet (Apr–May 2026)
- For each (window, threshold) combination: compute counterfactual signal direction accuracy
- Pick parameters that maximize correctness in BOTH bull-rally AND bear-trend regimes
- Walk-forward CV per regime

**Phase 3 — Implementation** (~30 min):
- Modify `derive_m30_bias` per spec
- Add unit tests
- Run regression

**Phase 4 — Counterfactual + deploy** (~30 min):
- Counterfactual: replay 2026-05-07 04–05h burst (287 SHORTs) — expect ≥80% blocked
- Smoke gate
- Restart + 5min observation

**Phase 5 — Production observation** (~24h):
- Monitor m30_bias flip frequency vs market regime
- Adjust thresholds if over- or under-blocking

Total ETA: 4–5h.

---

## 9. Constraints honored

- ✅ ZERO mutation in this investigation phase
- ✅ READ-ONLY (logs, parquets, code, audit docs)
- ✅ ZERO ATS Trend Line / NextGen / TradeATS / CC#1 / CC#2 references
- ✅ ZERO halt
- ✅ Production cascade + F-asym (Opção A) continues running

---

## 10. Status

- FluxQuantumAPEX **PID 20960** RUNNING (commit `ada8e9d` Opção A live)
- Telegram OFF (kill switch)
- Phase 0 investigation COMPLETE
- Awaiting ML-DS review of these findings + approval of Phase 1 spec direction

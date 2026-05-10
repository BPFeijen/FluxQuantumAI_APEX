# Opção B — Phase 1 spec: m30_bias F-1 + F-3 hysteresis fix

**Author**: CC#3
**Date**: 2026-05-07
**Asana**: BUG-SIGNAL-INVERTED 1214556369070092 → ML-DS comment 1214600021431534 (Phase 1 spec direction APPROVED, F-1 + F-3 combo)
**Predecessors**:
- Phase 0 investigation: `_audit/fixes/opcao_b_phase0_m30_hysteresis_investigation.md`
- Opção A deployed: commit `ada8e9d` (TRENDING_BIAS_BLOCK live)
**Status**: DRAFT — pending ML-DS review

---

## 1. Scope / Intent gap check

### In scope (this spec)
- Modification of **classification + resolution layer** in `live/level_detector.py:525` (`derive_m30_bias`)
- Two new helper functions: `_classify_with_min_bars()` and `_voting_vote()`
- 3 new config keys in `settings.json`:
  - `m30_bias_min_bars` (default `4`)
  - `m30_bias_voting_window` (default `3`)
  - `m30_bias_voting_strategy` (default `"recency_weighted"`, allowed `"majority"` | `"recency_weighted"`)
- ~10–15 unit tests covering each behavior
- Walk-forward CV calibration in Phase 2
- Dual counterfactual gate in Phase 4

### Out of scope (explicit non-goals)
- ❌ ZERO touch on **detection layer** (`_detect_boxes`, `m30_updater.py`, `m30_box_*` parquets)
- ❌ ZERO mutation of `liq_top`/`liq_bot`/`box_high`/`box_low` semantics (preserve commit `32343cf` excursion fix)
- ❌ ZERO H4 bias logic changes (`derive_h4_bias` untouched)
- ❌ ZERO cascade resolver layer changes (Opção A `_resolve_trend_direction` untouched)
- ❌ ZERO F-asymmetric filter changes (Opção A live)
- ❌ ZERO ATS Trend Line / NextGen / TradeATS / CC#1 / CC#2 references
- ❌ ZERO halt of FluxQuantumAPEX during dev

### Intent clarity
**Bug behavior** to fix: `m30_bias_confirmed` flipping to `bearish` based on a single short transient M30 box, persisting until the next box-turnover, even when the immediate-prior box was multi-bar bullish/unknown and broader market is rallying.

**Target outcome**: bias should require *stable* multi-bar confirmation **and** consistency with recent multi-box context before being accepted as confirmed.

---

## 2. Bug mechanism (preserved from Phase 0 + Box 5282 trace)

### 2.1 Empirical trace 2026-05-07 04–06 UTC

| Box ID | Time UTC | Bars | box_high | box_low | liq_top | liq_bot | bull_ext? | bear_ext? | `_classify` |
|--------|----------|------|----------|---------|---------|---------|-----------|-----------|------------|
| 5281 | 00:00–03:30 | 8 | 4708.9 | 4699.3 | 4730.5 | 4694.0 | ✅ | ✅ | unknown |
| **5282** | **04:00–05:00** | **3** | 4718.6 | 4707.8 | 4718.6 | 4700.4 | ❌ | ✅ | **bearish** |
| 5283 | 05:30–06:00 | 2 | 4709.8 | 4700.4 | 4726.6 | 4700.4 | ✅ | ❌ | bullish |
| 5279 | 06:30→ | active | 4709.3 | 4691.0 | 4760.0 | 4689.8 | ✅ | ✅ | unknown |

### 2.2 Why this caused 287 SHORTs at 04–05h UTC

`derive_m30_bias` (`level_detector.py:596-607`) consults **only the most recent confirmed box** (`confirmed.iloc[-1]`). Box 5282 confirmed at 04:30 — pure bear_ext on only 3 bars — fed to cascade as `m30_bias_confirmed=bearish`. Cascade emitted SHORTs into a +50pt rally for ~2h until box 5283 confirmed bullish at 06:00.

### 2.3 Two structural gaps (target of fix)

| Gap | Where | Today's manifestation |
|-----|-------|-----------------------|
| **G1: no min-bars threshold** | `_classify(last_confirmed)` accepts any confirmed box | Box 5282 with 3 bars treated identically to box 5281 with 8 bars |
| **G2: no multi-box context** | `confirmed.iloc[-1]` only | Box 5282 bearish ignored that box 5281 was unknown (mixed) and broader context was bullish |

F-1 closes G1. F-3 closes G2. Together they raise the bar for "confirmed bearish/bullish" to require both **stability within a box** AND **consistency across recent boxes**.

---

## 3. Fix design — F-1 + F-3 combo

### 3.1 New config keys (`config/settings.json`)

```json
{
  "m30_bias_min_bars": 4,
  "m30_bias_voting_window": 3,
  "m30_bias_voting_strategy": "recency_weighted"
}
```

Defaults are **proposals** — final values come from Phase 2 calibration. Must remain configurable at runtime (no hardcoded constants).

### 3.2 Helper signatures

```python
def _bars_in_box(m30_df: pd.DataFrame, box_id) -> int:
    """Count bars (rows) belonging to a given m30_box_id."""

def _classify_with_min_bars(
    row,
    bars_in_box: int,
    min_bars: int,
) -> str:
    """Like _classify(row), but returns 'unknown' if bars_in_box < min_bars.
    Thus bearish/bullish requires BOTH bear_ext/bull_ext semantics AND bar count >= min_bars."""

def _voting_vote(
    classifications: list[str],
    strategy: str,  # "majority" | "recency_weighted"
) -> str:
    """Aggregate up to M classifications (oldest→newest) into a single bias.
    - 'majority':           strict majority (> half) wins; else 'unknown'
    - 'recency_weighted':   weights [w0, w1, ..., w_{M-1}] e.g. [1, 2, 3] for M=3 (newest highest)
                            sum bullish weight vs bearish weight; require winner > 1.5x loser
                            (else 'unknown')."""
```

### 3.3 Modified `derive_m30_bias` confirmed path (pseudo-code)

```python
def derive_m30_bias(m30_df, confirmed_only=False):
    settings = _load_settings()
    min_bars = int(settings.get("m30_bias_min_bars", 4))
    window   = int(settings.get("m30_bias_voting_window", 3))
    strategy = str(settings.get("m30_bias_voting_strategy", "recency_weighted"))

    # ... existing _classify, _price_vs_box_bias unchanged ...

    confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
    latest_struct = ...  # unchanged
    latest_row    = ...  # unchanged

    # ----- confirmed path (MODIFIED for F-1 + F-3) -----
    if not confirmed.empty:
        # Take last `window` distinct confirmed boxes (newest last)
        recent_box_ids = confirmed["m30_box_id"].drop_duplicates().tail(window).tolist()
        recent_confirmed_rows = [
            confirmed[confirmed["m30_box_id"] == bid].iloc[-1]
            for bid in recent_box_ids
        ]

        # F-1: classify each box with min-bars guard
        classifications = []
        for row in recent_confirmed_rows:
            bars = _bars_in_box(m30_df, row["m30_box_id"])
            classifications.append(
                _classify_with_min_bars(row, bars, min_bars)
            )

        # F-3: aggregate via voting
        confirmed_bias = _voting_vote(classifications, strategy)

        # Live structural override (UNCHANGED from current logic)
        structural_now = _price_vs_box_bias(latest_row, current_gc)

        if confirmed_bias in ("bullish", "bearish"):
            if structural_now != "unknown" and structural_now != confirmed_bias:
                if confirmed_only:
                    return "unknown", False
                # fall through to provisional path
            else:
                return confirmed_bias, True

    if confirmed_only:
        return "unknown", False

    # ----- live/provisional path (UNCHANGED) -----
    # ... existing code ...
```

### 3.4 Today's trace under proposed fix

Re-run Box 5282 episode through F-1 + F-3 (defaults `min_bars=4`, `window=3`, `recency_weighted` weights `[1, 2, 3]`):

| Step | Outcome |
|------|---------|
| Recent 3 confirmed boxes | 5281 (8 bars, unknown), 5282 (3 bars, bear_ext-only), 5283 (2 bars, bull_ext-only) |
| F-1 min_bars=4 | 5281→unknown (8≥4 → keep _classify=unknown), 5282→**unknown** (3<4 → forced unknown), 5283→**unknown** (2<4 → forced unknown) |
| F-3 recency_weighted | weights [1,2,3] over [unknown, unknown, unknown] → all-unknown → return `unknown` |
| Cascade m30_confirmed | `unknown` → falls through to `provisional_m30` or `unknown` (per Opção A cascade) |

**Result**: m30_bias would be `unknown`, not `bearish`. SHORTs blocked by F-asymmetric filter (Opção A) since `confirmed_direction=unknown` → cannot align counter-trend. Estimated 287 SHORTs blocked at 04–05h UTC.

### 3.5 Sanity case (avoid over-blocking)

Re-run 2026-05-06 04h burst (200 SHORTs, RANGE_BOUND, after 401e6da). At that time the M30 was likely a stable multi-bar bear_ext box. F-1 + F-3 should NOT over-block: Phase 2 calibration must verify by replaying historic clearly-bearish episodes and confirming bias still resolves correctly.

---

## 4. Methodology citations (literature alignment)

### 4.1 F-1 — "Stable formation" / multi-bar confirmation

**Wyckoff (Villahermosa, *Wyckoff Methodology in Depth*)**:
> "A SOS (Sign of Strength) is the confirmation of accumulation — the price breaks out of the structure with increased volume and momentum, closing above the previous resistance."
> — cited in `sprints/entry_logic_fix_20260420/DESIGN_DOC_m30_bias_literatura_aligned.md:100-101`

A SOS/SOW is a *confirmation* event. A single 3-bar transient pullback is not a SOW — it is an in-range probe. Wyckoff explicitly warns Phase A/B "stopping action / cause building" should not be mistaken for Phase D directional bias (`DESIGN_DOC_m30_bias_literatura_aligned.md:128, 139`).

**ICT (Khan, *The ICT Bible*, Module 3 Advanced Order Flow)**:
> "A Break of Structure occurs when price decisively trades through a prior swing pivot. Before a BOS on the higher timeframe, you don't have directional bias — you have consolidation."
> — cited in `DESIGN_DOC_m30_bias_literatura_aligned.md:116`

A *decisive* break of structure requires multi-bar persistence beyond the prior swing. F-1's `min_bars=4` operationalizes "decisive" at the M30 layer.

### 4.2 F-3 — "Higher TF cycle prevails" / multi-box context

**ATS Two Problems to Solve (cited in `docs/DESIGN_DOC_Strategy_Layer_Architecture_v3.md:67`)**:
> "Defining the market cycle on your higher timeframe... helps you pick which strategy to use."

The **cycle** of recent boxes — not a single transient — is what defines bias. A single bear-leaning box embedded in a sequence of bullish/unknown boxes does not define a bearish cycle.

**Multi-timeframe Wyckoff (Cap. 7, cited in `DESIGN_DOC_m30_bias_literatura_aligned.md:103`)**:
> "The HTF carries bias; LTF timing."

F-3 elevates the M30 bias judgment from a single LTF box to the *cycle* across the last M=3 boxes — closer to HTF aggregation. A single transient (LTF timing event) cannot rewrite the bias (HTF carries that responsibility).

### 4.3 Combined justification

F-1 + F-3 jointly enforce: a confirmed M30 bias requires (a) *stable* formation within a box (F-1 min-bars) AND (b) *consistent* signal across the recent box cycle (F-3 voting). This is literature-aligned: SOS/SOW need stability; HTF cycle prevails; BOS must be decisive.

---

## 5. Edge cases (per Caveats 1, 2, 4)

### 5.1 Caveat 1 default — bias persistence when N<min_bars AND voting inconclusive

**Decision**: when F-3 voting returns `unknown` (no winner under the chosen strategy), `derive_m30_bias` returns `("unknown", False)` from the **confirmed path** AND falls through to the **provisional/live path** (current code path, lines 612–619).

**Why this default (not "persist previous bias")**:
1. **No state to persist**: `derive_m30_bias` is currently stateless — adding `previous_bias` requires a new persistence layer (file/in-memory cache) and synchronization across event_processor cold-restarts. Out of scope for Phase 3.
2. **Cascade already handles unknown gracefully**: Opção A's `_resolve_trend_direction` cascade has 5 layers; `m30_confirmed=unknown` falls through to `provisional_m30` then to `unknown`. If unknown, F-asymmetric blocks counter-trend signals.
3. **Provisional path is the live signal**: when confirmed bias is unstable, the `latest_row` provisional bias provides immediate price-action read.

**Trade-off documented**: this default may produce *flaky* bias (provisional flips faster). Phase 5 production observation must monitor and may motivate Phase 6 work (persisted bias) if flakiness manifests as inversion.

### 5.2 Caveat 4 — preserve `32343cf` detection layer

**Invariants preserved**:
- `_detect_boxes` unchanged
- `m30_updater` source unchanged
- Forward-fill semantics for `liq_top` / `liq_bot` unchanged (capture true excursion during box life)
- `box_high` / `box_low` definition unchanged (initial box formation extremes)
- `m30_box_confirmed` flag unchanged
- `m30_box_id` numbering unchanged

**Where modification happens**: ONLY in `derive_m30_bias` consumer logic at `live/level_detector.py:525`. Detection writes the same parquet rows; only the *interpretation* during bias resolution changes.

### 5.3 Other edge cases

| Edge | Behavior under F-1 + F-3 | Test |
|------|--------------------------|------|
| `m30_df` empty / None | return `("unknown", False)` (unchanged) | T1 |
| Fewer than `window` confirmed boxes | use what is available; voting over `len(confirmed)` boxes | T2 |
| Single-box-only history | F-1 still applies; if bars >= min_bars and `_classify`=bullish → return bullish; else unknown | T3 |
| Box with bars < min_bars but valid `_classify` | F-1 forces classification → unknown | T4 |
| All M boxes unknown | F-3 returns unknown | T5 |
| Voting tied (e.g. 1 bullish, 1 bearish, 1 unknown under majority) | strategy returns unknown | T6 |
| Recency-weighted: newest=bearish but two oldest=bullish | weights `[1, 2, 3]` → bullish=3, bearish=3 → tied → unknown (need >1.5x ratio per spec §3.2) | T7 |
| Recency-weighted: 3 boxes all bullish | bullish=6, bearish=0 → bullish | T8 |
| `confirmed_only=True` and confirmed_bias=unknown | return `("unknown", False)` (early exit) | T9 |
| Live structural override disagrees with voted bias | unchanged behavior — invalidate to provisional | T10 |
| Settings missing or invalid type | fall back to defaults `min_bars=4`, `window=3`, `strategy=recency_weighted` | T11 |
| Settings strategy unknown string | fall back to `recency_weighted`; log warn once | T12 |

---

## 6. Test plan

### 6.1 Unit tests (`tests/test_m30_bias_voting.py` — NEW file, ~15 tests)

| ID | Name | Asserts |
|----|------|---------|
| T1 | `test_empty_df_returns_unknown` | `derive_m30_bias(None)` → `("unknown", False)` |
| T2 | `test_window_truncation_when_fewer_boxes` | 2 confirmed boxes, window=3 → vote over 2 |
| T3 | `test_single_box_min_bars_pass` | 1 box, 6 bars, bullish ext → returns `("bullish", True)` |
| T4 | `test_single_box_min_bars_fail` | 1 box, 3 bars, bearish ext, min_bars=4 → returns `("unknown", False)` |
| T5 | `test_all_unknown_returns_unknown` | 3 boxes all unknown → unknown |
| T6 | `test_majority_strict_tie_returns_unknown` | 1 bullish, 1 bearish, 1 unknown, majority strategy → unknown |
| T7 | `test_recency_weighted_tie_returns_unknown` | bull=3 vs bear=3, ratio<1.5 → unknown |
| T8 | `test_recency_weighted_all_bullish` | 3 bullish boxes → bullish |
| T9 | `test_confirmed_only_unknown_early_exit` | confirmed_only=True + voted=unknown → unknown,False |
| T10 | `test_live_structure_overrides_vote` | voted=bullish but price << box_low → fall to provisional |
| T11 | `test_settings_defaults_when_missing` | settings.json missing keys → uses min_bars=4, window=3 |
| T12 | `test_invalid_strategy_falls_back` | strategy="invalid" → uses recency_weighted; one warn |
| T13 | `test_box_5282_replay_blocked` | reproduce 2026-05-07 boxes 5281/5282/5283 → returns unknown (not bearish) |
| T14 | `test_clear_bull_three_box_cycle_passes` | 3 boxes all bull-ext, all >=4 bars → bullish,True |
| T15 | `test_clear_bear_three_box_cycle_passes` | 3 boxes all bear-ext, all >=4 bars → bearish,True |

### 6.2 Existing regression coverage

Re-run all relevant suites; require ZERO regression:
- `tests/test_strategy_mode_cascade.py` (34 tests, Opção A cascade + F-asym)
- Smoke gate `tests/test_smoke.py` (97 tests baseline)
- `tests/test_level_detector.py` (existing m30 bias tests, if any)

### 6.3 Phase 2 calibration — walk-forward CV (per Caveat 2)

**Data**: historical decision_log + `gc_m30_boxes.parquet` Apr–May 2026.

**Regimes** (4):
- `RANGE_BOUND`
- `TRENDING_UP`
- `TRENDING_DN`
- `TRANSITIONAL` (regime flip days)

**Method**: 5-fold chronological split per regime (no future leakage). For each `(min_bars ∈ {3,4,5,6}) × (window ∈ {2,3,4,5}) × strategy ∈ {majority, recency_weighted}`:
- Replay decision_log entries
- Compute per-fold: signal-direction-vs-realized-1h-move correctness
- Aggregate per regime; require all 4 regimes ≥ baseline correctness
- Bonferroni correction (32 hypotheses) → α=0.05/32 ≈ 0.00156

**Acceptance**: chosen `(min_bars, window, strategy)` triple maximizes mean correctness across all 4 regimes AND no regime regresses (5pp tolerance).

**Output**: `_audit/calibrations/m30_bias_voting_calibration.md` + parameter overlay.

### 6.4 Phase 4 — dual counterfactual gate (per Caveat 3)

**Gate 1**: replay 2026-05-07 04–05 UTC SHORTs burst.
- Population: 287 SHORTs
- Pass criterion: ≥80% would have been blocked under F-1+F-3 (calibrated params)
- Output: `_audit/fixes/opcao_b_counterfactual_287_shorts.md`

**Gate 2**: replay Box 5282 morning rally interaction.
- Population: SHORTs emitted while price was rallying within Box 5282 window (04:30–06:30)
- Pass criterion: ≥95% of those SHORTs would have been blocked
- Output: same doc, separate section

**Both must pass** to proceed to Phase 4 deploy. If either fails, return to Phase 2 to recalibrate.

---

## 7. Implementation plan (Phase 3)

| Step | File | Change |
|------|------|--------|
| 1 | `config/settings.json` | Add 3 keys with defaults |
| 2 | `live/level_detector.py:525` | Modify `derive_m30_bias` confirmed-path (per §3.3) |
| 3 | `live/level_detector.py` | Add `_bars_in_box`, `_classify_with_min_bars`, `_voting_vote` helpers |
| 4 | `tests/test_m30_bias_voting.py` | NEW file, 15 unit tests (per §6.1) |
| 5 | `_audit/fixes/opcao_b_counterfactual_287_shorts.md` | Phase 4 results |
| 6 | `_audit/calibrations/m30_bias_voting_calibration.md` | Phase 2 results |

**ZERO touch**: `_detect_boxes`, `m30_updater.py`, `derive_h4_bias`, `_resolve_trend_direction`, `_resolve_direction`, F-asymmetric filter, cascade flags.

**ETA**:
- Phase 2 calibration: 1–2h
- Phase 3 impl: 30min
- Phase 4 deploy + counterfactual: 30min
- Phase 5 production observation: ≥24h

---

## 8. Deploy gates (Phase 4) — per Caveat 3

| Gate | Criterion | Source |
|------|-----------|--------|
| G1 | 15/15 unit tests pass | `pytest tests/test_m30_bias_voting.py` |
| G2 | 34/34 cascade tests pass | `pytest tests/test_strategy_mode_cascade.py` (regression) |
| G3 | 97/97 smoke tests pass | `pytest tests/test_smoke.py -m smoke` |
| G4 | Counterfactual G1 (287 SHORTs ≥80% blocked) | Phase 4 doc |
| G5 | Counterfactual G2 (Box 5282 ≥95% blocked) | Phase 4 doc |
| G6 | Phase 2 calibration ALL regimes pass (no regression) | Calibration doc |
| G7 | wc -l of test file ≥ deliverable threshold (~250+ lines) | Asana evidence |
| G8 | Production restart + 5min observation: NO immediate inversion alerts | live log tail |
| G9 | ML-DS sign-off on counterfactual + calibration | comment chain |
| G10 | Barbara approval for deploy | comment chain |

ALL 10 gates must pass before commit + restart.

---

## 9. Risk assessment + side effect preservation (per Caveat 4)

### 9.1 Risk register

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| R1 | F-1 + F-3 over-blocks legitimate bias | HIGH | Phase 2 calibration with 4-regime validation |
| R2 | Default `unknown` falls through to provisional which is also wrong | MED | Live structural override unchanged; F-asym (Opção A) catches counter-trend |
| R3 | `min_bars=4` lags genuine new trends | MED | Phase 2 calibration tests `min_bars ∈ {3,4,5,6}` |
| R4 | Recency-weighted tie threshold (1.5x) is arbitrary | LOW | Phase 2 includes strategy comparison; can adjust |
| R5 | Settings reload race during active trading | LOW | Existing settings reload already concurrent-safe per CC#3 audit (lru_cache + invalidation) |
| R6 | Performance regression in `derive_m30_bias` (called per signal) | LOW | Voting is O(window × bars); window=3, bars≤30 → negligible |
| R7 | Detection layer `32343cf` semantics drift | NONE | ZERO touch on detection (Caveat 4) |
| R8 | Cascade or F-asym (Opção A) interaction breaks | LOW | Regression suite G2 catches; provisional path unchanged |
| R9 | Inversion bug recurs after deploy | HIGH | Phase 5 24h observation; rollback plan ready |

### 9.2 Side effect preservation matrix (per Caveat 4)

| Component | Phase 0 behavior | After Phase 3 | Preserved? |
|-----------|------------------|---------------|------------|
| `_detect_boxes` forward-fill liq_top/liq_bot (32343cf) | Tracks true excursion during box life | Unchanged | ✅ |
| `m30_box_confirmed` flag | Set by box closure logic | Unchanged | ✅ |
| `m30_box_id` numbering | Sequential per box | Unchanged | ✅ |
| `box_high` / `box_low` | Initial breakout extremes | Unchanged | ✅ |
| `_classify(row)` (existing) | bull_ext/bear_ext semantics | Reused as inner check inside `_classify_with_min_bars` | ✅ |
| `_price_vs_box_bias` | Live structural check | Unchanged | ✅ |
| Provisional path (`live/latest_bias`) | Falls through when confirmed unknown | Unchanged behavior | ✅ |
| `derive_h4_bias` | H4 layer | Untouched | ✅ |
| Cascade resolver `_resolve_trend_direction` | 5-layer fallback | Untouched | ✅ |
| F-asymmetric filter (Opção A) | RANGE_BOUND + TRENDING extension | Untouched | ✅ |

### 9.3 Rollback plan

**Trigger**: Phase 5 observation detects regression (e.g., bias frozen as unknown for >2h during clear trends; OR new inversion alerts).

**Action**: revert commit (`git revert <hash>`) → restart NSSM service → 5min smoke. ETA <10min.

**Confidence**: HIGH (single-file change in classification layer; cascade + F-asym intact).

---

## 10. Constraints honored

- ✅ ZERO halt of FluxQuantumAPEX during dev
- ✅ ZERO touch on `_detect_boxes` / `m30_updater.py` (Caveat 4)
- ✅ ZERO ATS Trend Line / NextGen / TradeATS / CC#1 / CC#2 references
- ✅ ZERO push to GitHub during draft
- ✅ READ-ONLY scope on production paths during dev
- ✅ All deliverables under `_audit/fixes/` / `_audit/calibrations/` / `tests/`
- ✅ 4 caveats integrated (§5.1, §6.3, §6.4, §9.2)

---

## 11. Approval workflow

1. **Now**: ML-DS reviews this spec (~20 min)
2. **If approved**: proceed to Phase 2 calibration (~1–2 h)
3. **If approved post-calibration**: proceed to Phase 3 impl (~30 min)
4. **If counterfactuals pass**: ML-DS sign-off + Barbara approval → Phase 4 deploy (~30 min)
5. **Post-deploy**: Phase 5 observation 24h
6. **Post-observation**: BUG-SIGNAL-INVERTED Asana task can close pending Telegram reactivation

---

## 12. Status

- Phase 0 investigation: COMPLETE (`opcao_b_phase0_m30_hysteresis_investigation.md`)
- Phase 1 spec: **DRAFT — this file**
- Phase 2 calibration: not started
- Phase 3 impl: not started
- Phase 4 deploy: not started
- Phase 5 observation: not started
- FluxQuantumAPEX: PID 20960 RUNNING (Opção A live, commit `ada8e9d`)
- Telegram: OFF (kill switch ON)

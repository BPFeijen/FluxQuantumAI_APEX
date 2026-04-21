# Codex Task — Minimal Patch for M30 Bias Authority Fix

Repository: `BPFeijen/FluxQuantumAI_APEX`
Branch: `stabilization/apex-2026-04`

## Mission
Apply a **minimal, surgical patch** to stop the M30 bias from:
1. remaining stuck on the last confirmed box when live structure already invalidated it, and
2. acting as a universal hard veto that contradicts the existing strategy selector.

This is **not** a system rewrite.
Do **not** create a new architecture.
Do **not** change broker execution, position manager, Telegram flow, risk sizing, news logic, or thresholds.

## Files in scope
Only modify:
- `live/level_detector.py`
- `live/event_processor.py`

Add tests if a suitable existing test location exists. If no suitable location exists, create a small focused test file under `tests/`.

---

## Root cause already confirmed
The current production behavior shows:
- bearish bias stuck while market was bullish, causing valid LONGs to be rejected,
- bullish bias stuck while market was bearish, causing valid SHORTs to be rejected,
- `M30_BIAS_BLOCK` applied as an absolute hard veto even when `_resolve_direction()` explicitly allowed an overextension reversal.

This means there are **two separate but related defects**:

### Defect A — `derive_m30_bias()` is structurally wrong
Current behavior in `live/level_detector.py`:
- classification is asymmetric,
- confirmed bias anchors to last confirmed M30 box,
- no invalidation from current live GC price vs latest M30 structure.

### Defect B — `M30_BIAS_BLOCK` has wrong authority
Current behavior in `live/event_processor.py`:
- once confirmed M30 bias exists, it blocks all contra-bias trades,
- even if strategy logic already classified the trade as an allowed overextension reversal.

---

## Required patch

## Part 1 — Replace `derive_m30_bias()` in `live/level_detector.py`
Replace the existing `derive_m30_bias()` implementation with the version below.
Preserve the same signature and return contract:

```python
def derive_m30_bias(
    m30_df: pd.DataFrame | None, confirmed_only: bool = False
) -> tuple[str, bool]:
    """
    Shared M30 bias derivation used by both entry and position-monitor paths.

    Returns
    -------
    (bias, is_confirmed_source)
      bias: "bullish" | "bearish" | "unknown"
      is_confirmed_source: True when derived from a confirmed M30 box that
      is still structurally valid against the latest price.
    """
    if m30_df is None or m30_df.empty:
        return "unknown", False

    def _classify(row) -> str:
        import math

        box_high = row.get("m30_box_high", float("nan"))
        box_low  = row.get("m30_box_low",  float("nan"))
        liq_top  = row.get("m30_liq_top",  float("nan"))
        liq_bot  = row.get("m30_liq_bot",  float("nan"))

        bull_ext = (
            not math.isnan(liq_top)
            and not math.isnan(box_high)
            and liq_top > box_high
        )
        bear_ext = (
            not math.isnan(liq_bot)
            and not math.isnan(box_low)
            and liq_bot < box_low
        )

        if bull_ext and not bear_ext:
            return "bullish"
        if bear_ext and not bull_ext:
            return "bearish"
        return "unknown"

    def _price_vs_box_bias(row, current_gc: float | None) -> str:
        if current_gc is None:
            return "unknown"

        try:
            box_high = row.get("m30_box_high", None)
            box_low  = row.get("m30_box_low", None)

            if pd.notna(box_high) and current_gc > float(box_high):
                return "bullish"
            if pd.notna(box_low) and current_gc < float(box_low):
                return "bearish"
        except Exception:
            return "unknown"

        return "unknown"

    try:
        current_gc = _get_current_gc_price()

        confirmed = m30_df[m30_df["m30_box_confirmed"] == True]
        latest_struct = m30_df[
            (m30_df["m30_box_id"].notna()) &
            (m30_df["m30_box_id"] > 0) &
            (m30_df["m30_liq_top"].notna())
        ]

        latest_row = latest_struct.iloc[-1] if not latest_struct.empty else m30_df.iloc[-1]

        # ----- confirmed path -----
        if not confirmed.empty:
            last_confirmed = confirmed.iloc[-1]
            confirmed_bias = _classify(last_confirmed)
            structural_now = _price_vs_box_bias(latest_row, current_gc)

            # If confirmed bias is contradicted by live structure, invalidate it
            if confirmed_bias in ("bullish", "bearish"):
                if structural_now != "unknown" and structural_now != confirmed_bias:
                    if confirmed_only:
                        return "unknown", False
                else:
                    return confirmed_bias, True

        if confirmed_only:
            return "unknown", False

        # ----- live/provisional path -----
        latest_bias = _classify(latest_row)
        if latest_bias in ("bullish", "bearish"):
            return latest_bias, False

        structural_now = _price_vs_box_bias(latest_row, current_gc)
        if structural_now in ("bullish", "bearish"):
            return structural_now, False

        return "unknown", False

    except Exception as e:
        log.warning("derive_m30_bias failed: %s", e)
        return "unknown", False
```

### Important constraints for Part 1
- Do **not** introduce ATR thresholds.
- Do **not** introduce candle-count thresholds.
- Do **not** introduce H4 override logic here.
- Do **not** redesign the rest of the level detector.
- This fix must remain structural and deterministic.

---

## Part 2 — Add a bias-authority helper in `live/event_processor.py`
Inside class `EventProcessor`, add this helper before `_trigger_gate()`:

```python
def _should_apply_m30_bias_block(
    self,
    direction: str,
    strategy_reason: str,
    source: str,
) -> tuple[bool, str]:
    """
    Decide whether M30 confirmed bias is allowed to hard-block this trade.

    Authority model:
      - CONTINUATION  -> hard block
      - PULLBACK      -> hard block
      - OVEREXTENSION -> no hard block (strategy explicitly allows counter-bias reversal)
      - RANGE_BOUND   -> no hard block
      - unknown       -> conservative default = hard block only if clearly trend-following context
    """
    sr = (strategy_reason or "").upper()

    # Explicit counter-trend reversal mode already authorized by strategy
    if "OVEREXTENDED" in sr or "REVERSAL ALLOWED" in sr:
        return False, "OVEREXTENSION_REVERSAL"

    # Mean-reversion/range logic should not be killed by macro bias veto
    if "RANGE_BOUND" in sr:
        return False, "RANGE_BOUND"

    # Trend-following modes: bias should remain authoritative
    if "CONTINUATION" in sr:
        return True, "CONTINUATION"
    if "PULLBACK" in sr:
        return True, "PULLBACK"

    # PATCH2A is trend-continuation by definition
    if source == "PATCH2A":
        return True, "PATCH2A_CONTINUATION"

    # Conservative fallback:
    # if strategy reason says TRENDING but does not classify the subtype clearly,
    # keep the hard block
    if "TRENDING" in sr:
        return True, "TRENDING_UNCLASSIFIED"

    # Default: do not let bias veto unknown/non-trend cases absolutely
    return False, "DEFAULT_NO_HARD_BLOCK"
```

---

## Part 3 — Replace the current M30 bias hard-gate block in `_trigger_gate()`
Find the existing section headed:

```python
# -- PONTO 1: M30 BIAS HARD GATE --
```

Replace that entire section with this:

```python
        # -- PONTO 1: M30 BIAS HARD GATE ---------------------------------------
        # Correct authority model:
        #   - only CONFIRMED m30_bias may hard-block
        #   - provisional/unconfirmed bias is telemetry only
        #   - hard block depends on strategy context
        _is_patch2a = (source == "PATCH2A")

        if not m30_bias_confirmed:
            if provisional_m30_bias in ("bullish", "bearish"):
                log.info(
                    "M30_BIAS_PROVISIONAL_ONLY: src=%s dir=%s provisional=%s confirmed=%s -> no hard block",
                    source, direction, provisional_m30_bias, m30_bias
                )
                if _is_patch2a:
                    print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} provisional={provisional_m30_bias} confirmed=unknown -> PASS")
            elif _is_patch2a:
                print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} provisional=unknown confirmed=unknown -> PASS")
        else:
            _apply_bias_block, _bias_mode = self._should_apply_m30_bias_block(
                direction=direction,
                strategy_reason=strategy_reason,
                source=source,
            )

            if not _apply_bias_block:
                log.info(
                    "M30_BIAS_SOFT_BYPASS: src=%s dir=%s confirmed_bias=%s mode=%s reason=%s",
                    source, direction, m30_bias, _bias_mode, strategy_reason
                )
            else:
                if m30_bias == "bullish" and direction == "SHORT":
                    print(f"[{ts}] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)")
                    log.info(
                        "M30_BIAS_BLOCK: confirmed bullish M30 bias rejects SHORT at %.2f (src=%s mode=%s)",
                        price, source, _bias_mode
                    )
                    if _is_patch2a:
                        print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias=bullish -> BLOCK")
                    return

                if m30_bias == "bearish" and direction == "LONG":
                    print(f"[{ts}] M30_BIAS_BLOCK: bias=bearish(confirmed) -- LONG rejected (contra-M30)")
                    log.info(
                        "M30_BIAS_BLOCK: confirmed bearish M30 bias rejects LONG at %.2f (src=%s mode=%s)",
                        price, source, _bias_mode
                    )
                    if _is_patch2a:
                        print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias=bearish -> BLOCK")
                    return

                if _is_patch2a:
                    print(f"[{ts}] PATCH2A_BIAS_CHECK: dir={direction} confirmed_bias={m30_bias} -> PASS")
```

---

## Tests required
Add focused tests for these cases:

1. **Confirmed bullish invalidated by live price below latest box low**
   - expected: `derive_m30_bias(..., confirmed_only=True)` returns `("unknown", False)`

2. **Confirmed bearish invalidated by live price above latest box high**
   - expected: `derive_m30_bias(..., confirmed_only=True)` returns `("unknown", False)`

3. **Latest row ambiguous but live price outside box**
   - expected: provisional path returns bullish/bearish according to live structure

4. **Overextension strategy does not get killed by hard bias veto**
   - expected: `_should_apply_m30_bias_block(..., strategy_reason="...OVEREXTENDED...reversal allowed...")`
     returns `(False, "OVEREXTENSION_REVERSAL")`

5. **Continuation still respects hard bias veto**
   - expected: `_should_apply_m30_bias_block(..., strategy_reason="CONTINUATION ...")`
     returns `(True, "CONTINUATION")`

If direct unit-testing of `EventProcessor` is cumbersome, isolate the helper logic cleanly and test that helper.

---

## Validation requirements
Produce:
1. unified diff
2. test file(s)
3. concise explanation of before vs after behavior
4. list of any assumptions you had to make

## Non-negotiable safety rules
- No new thresholds.
- No ATR inventions.
- No H4 redesign.
- No M5 execution redesign.
- No refactor outside the 2 target files.
- No edits to broker execution, risk, PM, Telegram, or news gate.
- No “improvements” beyond the scope above.

## Goal
This patch must do exactly this:
- stop stale confirmed bias from remaining authoritative after live structure invalidates it,
- preserve M30 hard-block authority for trend-following entries,
- remove the contradiction where strategy-authorized overextension reversals get vetoed by the bias block.

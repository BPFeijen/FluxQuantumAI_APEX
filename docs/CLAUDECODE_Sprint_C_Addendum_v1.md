# CLAUDECODE PROMPT — Sprint C Addendum v1

**Parent doc:** `CLAUDECODE_Sprint_C_Implementation_v2.md`
**Type:** Supplement (not replacement) — all original §0-§14 still apply
**Trigger:** Claude independent audit of Phase 0 backup revealed 5 findings not captured in original Design Doc v2 or prompt
**Authorization:** Barbara ratified 2026-04-20

Read this addendum BEFORE starting Phase 1. Incorporate its items into your discovery report and later phase implementations. Nothing here expands scope — these are corrections and refinements.

---

## A. Line number drift corrections (apply throughout)

Design Doc v2 and the original prompt contained stale line references. Actual state in the backup files:

| Element | Original ref | **Actual** | Delta |
|---|---|---|---|
| `_resolve_direction` definition | 3340+ | **3467** | +127 |
| `_resolve_direction` call sites | 1 site assumed | **2 sites: lines 4254 AND 4415** | +1 site |
| Hard-block M30 bias | 2391-2403 | **2381-2405** | -10 / +2 |
| `_read_d1h4_bias_shadow` | 704-723 | 704-724 | matches |
| `derive_m30_bias` | 215-263 | matches | — |
| ADR-001 comment | line 18 | matches | — |
| `self.m30_bias = ...` | 1626 | matches | — |
| `refresh_macro_context` block computing bias | not specified | **1602-1635** | new ref |

Update your mental map. When you quote line numbers in commits or closeout, use the actual numbers.

---

## B. Phase 1 — Additional Discovery Items (MANDATORY)

Add these to §5.1 (code discovery) and §5.2 (data discovery) of the original prompt. Produce findings in `SPRINT_C_DISCOVERY.md`.

### B.1 — `gc_d1h4_bias.json` investigation (HIGHEST PRIORITY)

`event_processor.py:704-724` contains `_read_d1h4_bias_shadow` that reads from `C:/FluxQuantumAI/logs/gc_d1h4_bias.json`. This is a pre-computed H4/D1 bias JSON **not mentioned in the original Design Doc v2**.

The original Design Doc assumed `gc_h4_boxes.parquet` (stale 6 days) was the only structured H4 source with OHLCV resample as fallback. **If this JSON is fresh, the hybrid source ordering changes.**

**Discovery required (READ-ONLY):**

1. File existence + freshness:
   ```powershell
   $p = "C:\FluxQuantumAI\logs\gc_d1h4_bias.json"
   if (Test-Path $p) {
       Write-Host "mtime: $((Get-Item $p).LastWriteTime)"
       Write-Host "size:  $((Get-Item $p).Length) bytes"
       Get-Content $p | ConvertFrom-Json | ConvertTo-Json -Depth 10
   } else {
       Write-Host "FILE MISSING"
   }
   ```
   Report verbatim.

2. Writer discovery — find WHO writes this file:
   ```powershell
   cd C:\FluxQuantumAI
   Select-String -Path "*.py" -Pattern "gc_d1h4_bias" -Recurse | Where-Object { $_.Path -notlike "*backup*" } | ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" }
   ```
   Look for `.write`, `.dump`, or `open(..., "w")` near matches. Report which module/function writes it, and whether it's a scheduled process or on-demand.

3. Staleness semantics:
   - What does `h4_stale: true` mean in the writer logic?
   - What threshold defines stale? (hours? bars?)

4. Schema check — confirm fields: `d1_jac_dir`, `h4_jac_dir`, `bias_direction`, `bias_strength`, `data_freshness.h4_stale`, `data_freshness.d1_stale`, `last_closed_h4_ts`, `last_closed_d1_ts`.

5. Does it contain enough info for R_H4_1..7 rules, or only for R_H4_3/6 (jac_dir)?

**Decision point after discovery (report to Barbara, don't auto-apply):**

| Scenario | `derive_h4_bias` source priority |
|---|---|
| JSON fresh (h4_stale=false) + writer active + schema sufficient | **JSON primary** → parquet → resample |
| JSON fresh but only has jac_dir (no candle rules) | JSON for R_H4_3/6 only → parquet/resample for R_H4_1/2/4/5 |
| JSON stale OR missing OR writer dead | Original plan (parquet ≤6h → resample) |

DO NOT implement the hybrid until Barbara ratifies the source priority based on your discovery.

### B.2 — Bias terminology mapping ("unknown" / "?" / "neutral")

Legacy `derive_m30_bias` returns `"bullish"|"bearish"|"unknown"`.
`_read_d1h4_bias_shadow` returns `"?"` for unknown fields.
Design Doc v2 and v2 functions specify `"bullish"|"bearish"|"neutral"`.

Document the canonical mapping in `SPRINT_C_DISCOVERY.md`:

```
v2 internal terminology: "bullish" | "bearish" | "neutral"

Mapping at function boundaries:
- Legacy derive_m30_bias output → v2 input:  "unknown" → "neutral"
- JSON bias_direction            → v2 input: "?"       → "neutral"
- JSON jac_dir                   → v2 input: "?"       → "neutral", "UP" → "bullish", "DN" → "bearish"
- v2 output → legacy consumers (existing self.m30_bias): "neutral" → "unknown" (preserve legacy contract)
```

**This mapping must be implemented as a small helper `_canonicalize_bias(raw: str) -> str` at the top of `level_detector.py`. All new v2 code uses "neutral"; it converts at boundaries only.**

Add this to Phase 2a. Unit tests must cover: "unknown"→"neutral", "?"→"neutral", "UP"→"bullish", "DN"→"bearish", case insensitivity.

### B.3 — `provisional_m30_bias` is already computed — REUSE, do not recompute

`event_processor.py:1622-1628` already calls `derive_m30_bias` twice (confirmed-only + full) and stores both:

```python
confirmed_bias,    is_confirmed = derive_m30_bias(df, confirmed_only=True)
provisional_bias, _             = derive_m30_bias(df, confirmed_only=False)
self.m30_bias               = confirmed_bias
self.m30_bias_confirmed     = is_confirmed
self.provisional_m30_bias   = provisional_bias
```

**Implication for Phase 2b:** `derive_m30_bias_v2` signature MUST accept `confirmed_bias` and `provisional_bias` as inputs, NOT recompute them.

**Revised signature:**

```python
def derive_m30_bias_v2(
    confirmed_bias: str,           # from existing self.m30_bias (legacy output)
    is_confirmed: bool,            # from existing self.m30_bias_confirmed
    provisional_bias: str,         # from existing self.provisional_m30_bias
    h4_bias: str,                  # from derive_h4_bias via _get_h4_bias
    m30_df: pd.DataFrame,          # needed for UP/DN fakeout on latest unconfirmed row
    current_price: float,
    iceberg_bias: str = "",
):
    """
    Returns (bias, is_authoritative, metadata).

    Consumes already-computed confirmed/provisional bias. This function ADDS
    H4 gate + provisional-override-of-confirmed logic on top.
    """
```

This change:
- Saves ~40 lines in implementation
- Avoids double-computing `derive_m30_bias`
- Keeps single source of truth for m30 bias classification

Update Phase 3 unit tests accordingly — tests pass `confirmed_bias`, `provisional_bias` directly instead of DataFrames (easier, more deterministic).

### B.4 — Bug #2 framing is narrower than Design Doc v2 claimed

Current hard-block at 2381-2405 ALREADY passes through when `m30_bias_confirmed=False` AND `provisional_m30_bias in ("bullish", "bearish")`:

```python
if not m30_bias_confirmed:
    if provisional_m30_bias in ("bullish", "bearish"):
        log.info("M30_BIAS_PROVISIONAL_ONLY: ... -> no hard block")
        # PASSES THROUGH to gate logic
    ...
else:
    # BLOCKS on confirmed contra
```

Bug #2 only manifests in THIS specific case:
- `m30_bias_confirmed = True` (latest confirmed box says bearish)
- AND a NEWER box unconfirmed exists with OPPOSITE fakeout (e.g., confirmed box 5239 = bearish, unconfirmed box 5240 = UP fakeout = bullish-provisional)
- AND H4 aligns with the unconfirmed direction

**Revised Phase 2e spec:**

The override in hard-block is NOT "accept provisional in general" — it's "when confirmed disagrees with provisional AND provisional has newer box_id AND H4 aligns with provisional, ALLOW the provisional direction."

Pseudocode:

```python
# In hard-block area (lines 2381-2405 — exact location per your Phase 1 discovery)

h4_bias, _, _ = self._get_h4_bias()

# Compute v2 using existing bias state
v2_bias, v2_is_auth, v2_meta = derive_m30_bias_v2(
    confirmed_bias=self.m30_bias,
    is_confirmed=self.m30_bias_confirmed,
    provisional_bias=self.provisional_m30_bias,
    h4_bias=h4_bias,
    m30_df=self._m30_df,
    current_price=price,
    iceberg_bias=self._latest_iceberg_bias or "",
)

h4_gate_enforce = self._settings.get("h4_gate", {}).get("enforce", False)

if m30_bias_confirmed:
    # Existing block logic, BUT check for v2 override
    override_allowed = (
        v2_meta.get("rule_fired") == "R_M30_2" or v2_meta.get("rule_fired") == "R_M30_4"
    ) and v2_meta.get("provisional_override", False)

    if m30_bias == "bullish" and direction == "SHORT":
        # v2 override doesn't apply here (would need "bearish" provisional + H4 bearish)
        if not h4_gate_enforce or not override_allowed:
            # existing BLOCK
            return
        # else: shadow mode logging only
    if m30_bias == "bearish" and direction == "LONG":
        if override_allowed and h4_bias == "bullish":
            if h4_gate_enforce:
                log.info("M30_BIAS_OVERRIDE: confirmed=bearish but provisional=bullish + H4=bullish + R_M30_2 -> ALLOW")
                # PASS THROUGH — do not return
            else:
                log.info("SHADOW: would allow M30_BIAS_OVERRIDE (enforce=false, keeping BLOCK)")
                # legacy BLOCK preserved in shadow
                return
        else:
            # existing BLOCK
            return
# Provisional path (m30_bias_confirmed=False) mostly unchanged — already works
# But add H4 counter-check: provisional=bullish + H4=bearish → new block
```

**Key additions:**
- Provisional-overrides-confirmed only when: (a) rule_fired is R_M30_2/4, (b) H4 aligns, (c) `h4_gate.enforce=true`
- In shadow mode, legacy BLOCK is preserved; SHADOW log shows what v2 would allow
- Existing provisional-only pass-through (when confirmed=false) gets a NEW H4 counter-check — if provisional says bullish but H4 says bearish, block (in enforce mode)

### B.5 — `_resolve_direction` called from 2 sites, not 1

Original §6.4 assumed single call site. Actual:

- Line **4254**: `direction, strategy_reason = self._resolve_direction(level_type)`
- Line **4415**: `direction, _strat_reason = self._resolve_direction(level_type)`

The H4 gate logic must protect both. Options:

**Option 1 (recommended): Put H4 gate INSIDE `_resolve_direction`** at every return point. More invasive to the function but covers all call sites automatically.

**Option 2: Wrap `_resolve_direction` with a new `_resolve_direction_gated` and have callers use the wrapper.** Cleaner but changes 2 call sites.

**Option 3: Single exit refactor of `_resolve_direction`** — collect all `direction, reason` into local vars, apply H4 gate once before single return. Cleanest but more diff noise.

**Decision:** go with **Option 1** (inside `_resolve_direction` at each return). Rationale: matches original §6.4 plan with minimal changes; `_resolve_direction` is already complex with RANGE_BOUND/PULLBACK/CONTINUATION/overextension branches; adding a single H4-gate block right before each `return (direction, reason)` is the lowest-risk path. Helper:

```python
def _apply_h4_gate(self, direction, reason):
    """Returns (direction, reason) potentially overridden to (None, reason) by H4 counter/neutral."""
    if direction is None:
        return direction, reason
    h4_gate_cfg = self._settings.get("h4_gate", {})
    if not h4_gate_cfg.get("enabled", True):
        return direction, reason
    h4_bias, _, h4_meta = self._get_h4_bias()
    enforce = h4_gate_cfg.get("enforce", False)
    counter = (
        (h4_bias == "bullish" and direction == "SHORT") or
        (h4_bias == "bearish" and direction == "LONG")
    )
    neutral_block = (h4_bias == "neutral")
    if counter:
        self._metric_incr(f"h4_gate.counter_block.{direction.lower()}")
        log_msg = f"H4_COUNTER_BLOCK: h4={h4_bias} blocks {direction} enforce={enforce}"
        if enforce:
            log.info(log_msg)
            return None, f"H4_COUNTER:{h4_bias}_blocks_{direction}"
        log.info(f"SHADOW: {log_msg}")
        return direction, reason
    if neutral_block:
        self._metric_incr("h4_gate.neutral_block")
        if enforce:
            log.info(f"H4_NEUTRAL_BLOCK: {direction} enforce=true")
            return None, "H4_NEUTRAL_BLOCK"
        log.info(f"SHADOW: H4_NEUTRAL_BLOCK would block {direction}")
    self._metric_incr("h4_gate.allow")
    return direction, reason
```

Then replace each `return (direction, reason)` in `_resolve_direction` with:

```python
return self._apply_h4_gate(direction, reason)
```

Count the return points in `_resolve_direction` during Phase 1 discovery. Report count in `SPRINT_C_DISCOVERY.md`. Each one needs the wrapper.

---

## C. Updated deliverables summary

Phase 1 discovery report (`SPRINT_C_DISCOVERY.md`) must now include:

- [ ] Original §5.1 code discovery with corrected line numbers (§A of this addendum)
- [ ] **NEW:** §B.1 `gc_d1h4_bias.json` full investigation (writer, freshness, schema, decision proposal)
- [ ] **NEW:** §B.2 bias terminology mapping documented
- [ ] **NEW:** §B.3 confirm `self.provisional_m30_bias` already computed at 1622-1628
- [ ] **NEW:** §B.4 confirm current hard-block provisional pass-through at 2381-2405
- [ ] **NEW:** §B.5 count of return points in `_resolve_direction` (expected 8-12 based on branch structure)
- [ ] Original §5.2 data discovery (parquets)
- [ ] Original §5.3-5.7 (config, decision log, git state, service state, summary)

Phase 2 implementation adjustments:

- [ ] Phase 2a: add `_canonicalize_bias` helper + constants (per §B.2)
- [ ] Phase 2a: `derive_h4_bias` source priority depends on Barbara decision from §B.1 findings
- [ ] Phase 2b: revised `derive_m30_bias_v2` signature (per §B.3)
- [ ] Phase 2d: use `_apply_h4_gate` helper + replace all return points in `_resolve_direction` (per §B.5)
- [ ] Phase 2e: nuanced override-confirmed logic (per §B.4)

Phase 3 unit tests:

- [ ] `_canonicalize_bias` tests
- [ ] `derive_m30_bias_v2` tests using revised signature (confirmed_bias + provisional_bias as inputs)
- [ ] Override-confirmed test: confirmed=bearish + provisional=bullish + H4=bullish → v2=bullish with is_auth=False, rule_fired=R_M30_2

---

## D. New hard limit

Add to §0 Golden Rule:

11. **NO auto-decision on `gc_d1h4_bias.json` source priority.** After Phase 1 discovery, Barbara decides which hybrid ordering to implement. Do NOT pick a source without her explicit ratification.

---

## E. Re-ack protocol

After reading this addendum, reply to Barbara with:

1. Confirmation you read it
2. Any clarification questions on §B.1-B.5
3. Updated Phase 1 checklist (from §C) explicitly enumerated

Then proceed to Phase 1 as amended.

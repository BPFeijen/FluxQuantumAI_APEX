# REGIME_FLIP Forensic Investigation — 2026-04-20

**Sprint:** sprint_cleanup_2_regime_flip_investigation_20260420
**Mode:** READ-ONLY. Zero writes to code/config/data. Zero service touches.
**Duration:** ~60 min (within budget)
**Capture PIDs (2512, 8248, 11740):** intact.

---

## 0. TL;DR

- **REGIME_FLIP in production is exit-only for system-owned positions** (`position_monitor._check_regime_flip`). Never evaluated on entries.
- `REGIME_FLIP_LONG/SHORT` constants in `run_live.py:155-156` exist **only inside the `--monitor` command path**; the service runs `run_live.py --execute` which never hits them. These constants are **dead code in live service**.
- During the 14:09-14:56 incident: **zero regime_flip log entries** because all 8 LONG GOs were `EXEC_FAILED` → zero open positions → zero `_check_regime_flip` invocations.
- **BIGGER FINDING: entry-gate `MOMENTUM_BLOCK_LONG=-1050` is also effectively defeated** by `delta_4h_inverted_fix=true` (Sprint 8 Fix 2) — during incident, delta_4h=-1121 was interpreted as "seller exhaustion SUPPORTS LONG +2" instead of blocking.
- **RCA: mixed RCA-B (architectural gap for entries) + RCA-C (competing code path).** Calibrating the thresholds without clarifying which logic branch fires would be wasted work.

---

## 1. REGIME_FLIP_SHORT definition and usage

### 1.1 Definition site

`C:\FluxQuantumAI\run_live.py:152-156`:

```python
# Exit thresholds
EXIT_SCORE_THRESH   = -4    # gate score <= this = danger signal
EXIT_SCORE_BARS     = 3     # consecutive danger bars before exit
REGIME_FLIP_LONG    = +1_000  # delta_4h crosses above this while SHORT = regime flipped
REGIME_FLIP_SHORT   = -1_000  # delta_4h crosses below this while LONG = regime flipped
```

### 1.2 Call sites (4 total, all in `run_live.py`)

```
299:    if args.dir == "SHORT" and mom.delta_4h > REGIME_FLIP_LONG:
301:    elif args.dir == "LONG" and mom.delta_4h < REGIME_FLIP_SHORT:
624:            if args.dir == "SHORT" and mom.delta_4h > REGIME_FLIP_LONG:
626:            elif args.dir == "LONG" and mom.delta_4h < REGIME_FLIP_SHORT:
```

**All 4 use sites are inside `_format_monitor()` (lines 288-315) and the `--monitor` CLI loop (lines 594-638).**

Context of the `--monitor` mode (line 1087-1090):
```python
if args.monitor:
    # watches a single open position via CLI
    return cmd_monitor(args)
```

### 1.3 Service invocation — is `--monitor` used in production?

Live service: `python run_live.py --execute --broker roboforex --lot_size 0.05` (verified via `wmic process` during forensic). **NO `--monitor` flag, NO `--dir` argument.** The production path (`--execute`) goes via `cmd_execute()` which never invokes `_format_monitor` nor the monitor loop.

**Conclusion: `REGIME_FLIP_LONG/SHORT` in `run_live.py` are dead code in the live service path.** They only fire when an operator manually runs `run_live.py --monitor --dir LONG/SHORT ...` as a CLI tool.

### 1.4 Decision gated

- **In monitor mode (CLI):** adds an entry to `exit_signals[]` list shown in terminal output. Returns exit code 1 if any exit signal fires (line 635-636). **Has no side effect on broker positions — it's a human-facing terminal UI signal.**
- **In live service:** does not execute.

### 1.5 Different logic in `position_monitor.py`

The actual live regime-flip logic is in `C:\FluxQuantumAI\live\position_monitor.py:958` — `_check_regime_flip`. It **does NOT import `REGIME_FLIP_LONG/SHORT` from run_live**. Instead uses:

```python
block_long = float(thr.get("delta_4h_long_block") or -600)   # settings: -1050
short_thr  = float(thr.get("trend_resumption_threshold_short", -800))
```

Symbols `REGIME_FLIP_BEAR` and `REGIME_FLIP_BULL` appear in position_monitor's **docstring** (lines 19-20) but are **not actually defined anywhere** — the docstring references constants that don't exist, and the code uses `settings.json` thresholds instead. Naming drift (cosmetic).

---

## 2. LONG-side symmetry

| Aspect | SHORT exit | LONG exit |
|---|---|---|
| `run_live.py` constant | `REGIME_FLIP_LONG = +1000` | `REGIME_FLIP_SHORT = -1000` |
| `run_live.py` usage | Monitor mode only | Monitor mode only |
| Live `position_monitor._check_regime_flip` trigger | `delta_4h < -800 (trend_resumption_threshold_short) AND m30_bias == "bullish"` | `delta_4h < -1050 (delta_4h_long_block)` |
| Confluence required | **Yes — Option C (CAL-03)**: delta AND m30_bias must both flip | **No** — delta alone |
| Streak gate | `delta_flip_min_bars=47` × 2s = **94s sustained** | Same 94s sustained |
| Post-SHIELD suppressed | Yes (line 985-986) | Yes (line 985-986) |
| Offensive flip after exit | Yes (3-leg reversal) | Yes (3-leg reversal) |

**Asymmetric protection confirmed:**
- SHORT exit requires **two conditions** (delta_4h exhaustion + m30_bias flipped bullish)
- LONG exit requires **one condition** (delta_4h below block_long)
- Rationale in docstring: "CAL-03 Option C confluence" for SHORT side (after selling-climax false exits). LONG side not similarly refined.

---

## 3. Evaluation trace during incident 14:09-14:56 UTC

### 3.1 Grep results

**On `decision_log_last60min.jsonl` (16 entries):**
```
REGIME_FLIP / regime_flip mentions: 0
```

**On `service_stdout_tail2000.log` filtered to window 14:00-15:00 UTC (2000 lines matching `^\[14:`):**
```
REGIME mentions:         0
regime mentions:         0
momentum BAIXA mentions: 0  (MOMENTUM_BLOCK_LONG fire message — never emitted)
hard_block mentions:     0
BLOCK mentions:          982 (ALL "M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected")
MOMENTUM mentions:       2 (both: "GO SIGNAL: momentum OK (d4h=-112X) | iceberg large_order sc=+3")
```

**On `service_stderr_tail2000.log`:** same — zero regime_flip entries.

### 3.2 Key log excerpts (verbatim)

```
[14:56:29.370] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)
[14:56:30.702] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)
... (many M30_BIAS_BLOCK SHORT rejections)
[14:56:34.956] GO SIGNAL: momentum OK (d4h=-1121) | iceberg large_order sc=+3
[14:56:36.464] GO SIGNAL: momentum OK (d4h=-1124) | iceberg large_order sc=+3
[14:56:36.464] EXEC_FAILED: GO LONG -> NO BROKER CONNECTED
... (more M30_BIAS_BLOCK SHORT rejections)
```

Note: **"momentum OK" with delta_4h=-1121**. This is the first anomaly — delta_4h=-1121 is below `MOMENTUM_BLOCK_LONG=-1050` and should have triggered block, but instead logged as "OK".

### 3.3 Decision_log entry detail (14:56:44 GO LONG)

```json
{
  "context": {"delta_4h": -1133, "m30_bias": "bullish", "daily_trend": "short"},
  "gates": {
    "v3_momentum": {"status": "OK", "delta_4h": -1121, "score": 2},
    "v4_iceberg":  {"status": "NEUTRAL", "score": 3, "type": "large_order", "aligned": true}
  },
  "decision": {"action": "GO", "direction": "LONG",
               "reason": "momentum OK (d4h=-1121) | iceberg large_order sc=+3",
               "total_score": 4}
}
```

**Two different delta_4h values** (context −1133 vs v3_momentum −1121) — one comes from `self._metrics.get("delta_4h")` (line 929), the other from `mom.delta_4h` after `ATSLiveGate.check()` recomputes. Both are below −1050; both should trigger block.

### 3.4 Why did momentum=OK fire for LONG with delta_4h < MOMENTUM_BLOCK_LONG?

**Root cause in `ats_live_gate.py:404-442` (delta_4h_inverted_fix branch):**

```python
_d4h_cfg = _DELTA_4H_SETTINGS
if _d4h_cfg["inverted_fix"]:    # settings.json delta_4h_inverted_fix=true
    _exh_low = _d4h_cfg["exhaustion_low"]   # -1050
    ...
    elif sig.delta_4h < _exh_low:
        # Seller exhaustion = bullish signal
        if direction == "LONG":
            sig.status = "ok"
            sig.score  = 2
            sig.reason = f"[DELTA_4H_FIX] seller exhaustion d4h={sig.delta_4h:+.0f} SUPPORTS LONG +2"
        else:
            sig.status = "warn"
            sig.score  = -2
            sig.reason = f"[DELTA_4H_FIX] ... PENALIZES SHORT -2"
```

`settings.json` line 65: `"delta_4h_inverted_fix": true` (Sprint 8, CAL-03 fix branch).

**Behaviour under this branch:**
- delta_4h < -1050 (exhaustion_low): interpreted as **"seller exhaustion — SUPPORTS LONG"** not "bearish — BLOCK LONG"
- delta_4h > +3000 (exhaustion_high): "buyer exhaustion — SUPPORTS SHORT" not "bullish — BLOCK SHORT"

The original `if direction=="LONG" and sig.delta_4h < MOMENTUM_BLOCK_LONG: status="block"` logic at lines 479-483 is in the `else` branch (non-inverted-fix, lines 461-494) and is **never executed** when `delta_4h_inverted_fix=true`.

### 3.5 Hard-block conditional analysis (ats_live_gate.py:803)

```python
block_from_delta = (
    (direction == "SHORT" and mom.delta_4h > MOMENTUM_BLOCK_SHORT) or
    (direction == "LONG"  and mom.delta_4h < MOMENTUM_BLOCK_LONG)
)
relax_impulse = (
    mom.status == "block"
    and not block_from_delta
    and direction == "SHORT"
    and at_structural_level
)
if mom.status == "block" and not relax_impulse:
    hard_block = True
    block_reason += "MOMENTUM: " + mom.reason
```

- `block_from_delta` is computed correctly (True for LONG at -1121 < -1050)
- **But hard_block gate is `if mom.status == "block"`** — since inverted_fix set status="ok", **hard_block never fires**
- `block_from_delta` variable is **effectively dead** post-inverted_fix (only used in `relax_impulse` exception)

### 3.6 Conclusion on Q3

- **REGIME_FLIP logic did NOT fire during incident:**
  - run_live.py constants live only in monitor-mode → not executed in service
  - position_monitor `_check_regime_flip` requires open system position → zero positions (all EXEC_FAILED) → zero evaluations
- **MOMENTUM_BLOCK_LONG did NOT block the entries** despite delta_4h < -1050:
  - `delta_4h_inverted_fix=true` reinterpreted -1121 as "seller exhaustion SUPPORTS LONG +2"
  - Hard-block conditional gated on `mom.status=="block"` — inverted_fix left it "ok"
- Only actual BLOCKs during window: 982× M30_BIAS_BLOCK for SHORT rejections (nothing blocking LONG direction)

---

## 4. Position scope (exit vs entry)

### 4.1 `_check_regime_flip` is exit-only

`position_monitor.py:570`:
```python
self._check_regime_flip(
    pos, trade_rec, state, delta_4h, direction, price, atr, df_micro
)
```

`pos` parameter is required — a dict describing an **open MT5 ticket**. The function is called inside `_process_position()` loop which iterates over `open_positions` from the broker.

Signature: `_check_regime_flip(self, pos: dict, trade_rec: Optional[dict], state: dict, ...)` — no way to invoke it for entry gating.

### 4.2 Entry gate does not consult REGIME_FLIP

Entry decisions flow through:
1. `event_processor._resolve_direction()` (ATS strategy mode)
2. `event_processor._trigger_gate()` (calls `ATSLiveGate.check()`)
3. `ATSLiveGate.check()` computes `mom = MomentumSignal(...)` — this is where delta_4h is evaluated
4. Hard-block conditional (ats_live_gate.py:803) — `if mom.status == "block"` → block

**No REGIME_FLIP constant or helper is imported into `event_processor.py` nor `ats_live_gate.py`.**

### 4.3 Implication for manual trades

Barbara manually entered SHORT positions earlier today (per incident chat) based on system signals. Those trades exist in MT5 terminal — **are they monitored by `position_monitor.py`?**

Position_monitor reads `open_positions` from MT5 broker API. If manually-entered positions appear in the same broker account, they ARE visible to position_monitor — which means `_check_regime_flip` would evaluate them.

However, the regime_flip logic uses `trade_rec` from internal trades.csv for leg management. Manual positions without a matching trades.csv entry → `trade_rec=None` → falls to `self._close_ticket(pos["ticket"], "REGIME_FLIP", ts)` (line 1054) — treats as single-leg.

**So manual positions COULD get regime-flipped** if the streak condition (94s sustained) + direction + bias confluence are met. But today: system had NOTHING to flip because broker layer stopped fills, and manually-entered positions Barbara closed by hand well before the 94s streak would have accumulated.

---

## 5. The 3 overlapping delta_4h thresholds — actual behaviour during incident

| # | Name | Value | File:line | Evaluated during incident? | Fired? | Resulting action |
|---|---|---|---|---|---|---|
| 2 | `delta_4h_long_block` | -1050 | settings.json:3 | YES (via ATSLiveGate MOMENTUM_BLOCK_LONG clone at :191) | **NO** — `mom.status` stayed "ok" due to inverted_fix overrides | Zero effect |
| 8 | `trend_resumption_threshold_short` | -800 | settings.json:12 | YES (inside `_check_regime_flip` SHORT path) | **N/A** — no open SHORT positions (EXEC_FAILED) | N/A |
| 157 | `REGIME_FLIP_SHORT` | -1000 | run_live.py:156 | NO (dead code in service path — monitor-mode only) | — | — |
| Bonus | `MOMENTUM_BLOCK_LONG` | -1050 | ats_live_gate.py:191 | YES (cloned from #2) | **NO** (same reason as #2) | Zero effect |

**All three nominal "delta_4h < -1000 or -1050 should protect against counter-trend LONG" thresholds were bypassed or not reached.** The mechanism that WAS active was `delta_4h_inverted_fix` which **inverted** the gate from block → bonus.

---

## 6. Root cause analysis

### Primary (high confidence): **RCA-B — architectural gap for entries**

`REGIME_FLIP_SHORT` protection is exit-only. Live service has no regime-flip gate on the ENTRY path. The protection is structurally absent for:
- New GO signals (entry gate)
- Manually-entered positions (if user hasn't logged them in trades.csv format — they get `trade_rec=None` fallback)
- Anything that fails broker execution (no position to monitor)

### Secondary (high confidence): **RCA-C — competing code path defeats nominal block**

Even the ENTRY-path momentum block (`MOMENTUM_BLOCK_LONG=-1050` at `ats_live_gate.py:480`) doesn't fire because `delta_4h_inverted_fix=true` (`settings.json:65`, active since Sprint 8) takes the DIFFERENT code branch at lines 404-442 which reinterprets delta_4h as exhaustion signal — flipping "block" to "bonus +2".

This is not a bug per se — it's an intentional Sprint 8 decision (CAL-03). But it means today's "momentum gate should have blocked" narrative is structurally incorrect. The gate was DESIGNED to allow LONG at delta_4h=-1121 under the exhaustion thesis.

### Tertiary (medium confidence): **Naming/docstring drift**

`position_monitor.py` docstring references `REGIME_FLIP_BEAR` / `REGIME_FLIP_BULL` — constants that don't exist. Cosmetic but signals past refactoring that didn't fully clean up.

### Ruled out

- RCA-A (threshold + fires + log-only): REGIME_FLIP never fired, not just log-only.
- RCA-D (misconfiguration): thresholds read correct variables from correct sources. Values are intentional.
- RCA-E (other): none.

---

## 7. Implications for calibration sprint

**Calibrating thresholds WITHOUT resolving the inverted_fix + exit-only architecture would be wasted work.** Because:

1. Calibrating `MOMENTUM_BLOCK_LONG` on historical data would only tune the branch at line 480 — which **isn't executed** when `delta_4h_inverted_fix=true`.
2. Calibrating `REGIME_FLIP_SHORT` would only tune the monitor-mode display — **never runs in service**.
3. Calibrating `trend_resumption_threshold_short` and `delta_4h_long_block` used by `_check_regime_flip` would only affect open positions — **doesn't stop incoming wrong-side entries**.

**The calibration target is misaligned with the actual control flow.**

---

## 8. Recommendation

Before any threshold calibration sprint, run a **clean-up sprint** that answers:

1. **Inverted-fix regime clarity (BLOCKING).** Is `delta_4h_inverted_fix=true` intentional today? If yes, `MOMENTUM_BLOCK_LONG/SHORT` are semantically dead — rename/retire them. If no (incident reveals it shouldn't be true), settings flip + calibrate.
2. **REGIME_FLIP as entry gate (if Barbara wants protection).** `_check_regime_flip` logic must be lifted to the entry path in `ats_live_gate.py` or `event_processor._resolve_direction`. Current exit-only scope doesn't protect against today's failure mode (EXEC_FAILED entries + counter-trend).
3. **Consolidate 3 overlapping delta_4h thresholds.** `delta_4h_long_block=-1050`, `trend_resumption_threshold_short=-800`, `REGIME_FLIP_SHORT=-1000` — one authoritative, others deprecate. Cross-ref report §3.4 already flagged this.
4. **Dead-code cleanup of `REGIME_FLIP_LONG/SHORT` in run_live.py** OR enforce that monitor mode is the primary execution path (it is not today).

Only AFTER these architectural fixes should calibration run on the 8 genuinely-NEW thresholds from CROSS_REF_REPORT §3.3.

---

## 9. System state during investigation

- Files modified: **ZERO** (this file is sole write)
- Restarts: **ZERO**
- Capture PIDs (2512, 8248, 11740): **INTACT**
- Git operations: NONE
- Python code executed: READ-ONLY (file reads + grep — no functions imported from production modules)
- Incident forensic snapshot (`INCIDENT_20260420_LONG_DURING_DROP/20260420_145845/`): untouched

---

## 10. Summary for Barbara (per §6 comms)

> **RCA: mixed RCA-B (architectural gap for entries) + RCA-C (competing inverted_fix code path).**
>
> REGIME_FLIP_SHORT in run_live.py is monitor-mode only — dead code in live service.
> Entry-side protection equivalent (MOMENTUM_BLOCK_LONG=-1050) is structurally bypassed by delta_4h_inverted_fix=true which reinterprets −1121 as "seller exhaustion SUPPORTS LONG +2" (Sprint 8 CAL-03).
> No regime_flip evaluation during incident — zero open positions (all EXEC_FAILED).
>
> **Next sprint should NOT be calibration.** Should be clean-up: clarify inverted_fix intent, lift regime-flip to entry gate if wanted, consolidate 3 overlapping delta_4h thresholds, dead-code cleanup. Calibration resumes after.

# MacroMonitor v1 — Design proposal

**Status:** DRAFT (awaiting Barbara approval)
**Date:** 2026-04-27
**Author:** ClaudeCode
**Trigger:** Barbara identified that PositionMonitor is silent when broker rejects orders, even though operators on Telegram manually open positions based on GO alerts. Concrete failure: 2026-04-27 ~02:00 UTC system emitted GO SHORT, market then reversed LONG, no exit alert went to Telegram or dashboard.

---

## Problem statement

`PositionMonitor` (PM) currently watches **real MT5 positions**. When the broker rejects orders (current state — `EXEC_FAILED` on every recent decision), no positions exist, so PM is silent. But:

1. Operators (Barbara) read Telegram and manually open positions based on `GO` decisions.
2. The system continues to evaluate market state regardless of broker connection.
3. When conditions reverse against the last emitted GO direction (iceberg/anomaly/regime flip against), the operator needs an alert to **manually close** what they may have opened.

PM-DECOUPLE-PHASE3 (commit `889f5ee`) decoupled PM's read-path from MT5, but PM still iterates over MT5-reported positions. This proposal **fully decouples** by introducing a parallel "virtual position" tracker driven by emitted decisions.

## Non-goals

- Replace existing PositionMonitor (it still monitors real MT5 positions when they exist)
- Auto-execute exits (this is alert-only; operator closes manually)
- Reconcile virtual positions with actual broker positions
- Provide PnL accounting (operator's broker is source of truth for that)

## Methodology grounding (Rule 1 G-LITERATURE-BEFORE-CODE + Rule 15)

Each exit trigger maps to a verbatim canon citation. **No magic numbers** — thresholds are placeholders awaiting Phase 0 calibration.

### [Citation 1 — ICT order block invalidation] METHODOLOGY_SYNTHESIS_v1 §4.5
> *"An order block is invalidated when price closes through it from the opposite side... order flow then aligns with the breakout direction."*

→ **ICEBERG_AGAINST**: large institutional order detected on the side opposite to the active virtual position direction signals invalidation.

### [Citation 2 — Wyckoff Effort vs Result] METHODOLOGY_SYNTHESIS_v1 §2.5
> *"When effort (high volume/delta) produces little or no result (small price movement) or reversal, distribution (in uptrends) or accumulation (in downtrends) is occurring."*

→ **ANOMALY_AGAINST**: spread widening / volume climax / weak close detected with flow relation opposing the active virtual position direction.

### [Citation 3 — Wyckoff CHoCH] METHODOLOGY_SYNTHESIS_v1 §2.7 + ICT/SMC §4.2
> *"CHoCH (Change of Character): price breaks a previous swing AGAINST the prevailing trend. Signals potential trend reversal."*

→ **REGIME_FLIP_M30**: m30_bias flips (bullish ↔ bearish) — structural change against current direction.
→ **REGIME_FLIP_D1**: daily_trend (B+C ensemble v2_2026-04-26) flips — higher-timeframe structural change.

### [Citation 4 — ATS Expansion Lines] ATS Trade System
> *"When price exceeds the opposing expansion line, the prior expansion phase is over and a new direction is establishing."*

→ **PRICE_ACTION_STOP**: price moves N×ATR(M30) against active virtual position from entry — virtual stop-loss equivalent.

### [No literature anchor needed — operator UX]
- **VIRTUAL_TP1 / TP2 / SL**: price reaches the SL/TP1/TP2 levels from the original `GO` decision payload. Pure mirror of the decision's risk plan; no methodology choice.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│  EventProcessor (existing, untouched)                      │
│  Emits decisions to decision_log.jsonl + decision_live.json│
└──────────────────────┬─────────────────────────────────────┘
                       │
                       ▼ reads decision_live.json
┌────────────────────────────────────────────────────────────┐
│  MacroMonitor (NEW — live/macro_monitor.py)                │
│  Background daemon thread, 2s tick                         │
│                                                            │
│  STATE: current Virtual Active Position (VAP)              │
│    - vap_id, direction, entry_price, entry_ts             │
│    - sl, tp1, tp2 (from original GO decision)              │
│    - status: HELD | EXIT_SUGGESTED | SUPERSEDED | EXPIRED │
│    - active_triggers: list of fired exit triggers         │
│                                                            │
│  TICK (every 2s):                                          │
│    1. Refresh VAP from decision_live.json                  │
│       (new GO opposite direction → SUPERSEDE old VAP)      │
│    2. Read current state (price, m30_bias, daily_trend,    │
│       delta_4h, anomaly, iceberg) from service_state.json  │
│    3. Evaluate 9 exit triggers against VAP direction       │
│    4. For each fired trigger NOT already in active_triggers:│
│       a. Append to active_triggers                         │
│       b. Emit MACRO_EXIT_SUGGESTED event                   │
│       c. Send Telegram alert                               │
│    5. Update active_virtual_position field in heartbeat    │
└──────────────────────┬─────────────────────────────────────┘
                       │ writes
                       ├── position_events.jsonl (canonical)
                       ├── service_state.json: active_virtual_position
                       └── tg.notify_macro_exit() → Telegram
```

### State model

```python
@dataclass
class VirtualActivePosition:
    vap_id: str                  # decision_id of the originating GO
    direction: Literal["LONG", "SHORT"]
    entry_price: float           # price_mt5 at GO emission
    entry_ts: datetime
    sl: float                    # from GO decision
    tp1: float
    tp2: float
    status: Literal["HELD", "EXIT_SUGGESTED", "SUPERSEDED", "EXPIRED"]
    active_triggers: list[str]   # triggers fired so far (anti-spam)
    last_alert_ts: datetime | None
    expires_at: datetime         # entry_ts + max_lifetime (default 4h)
```

### Exit triggers (9 total)

| ID | Trigger | Condition | Severity | Citation |
|---|---|---|---|---|
| 1 | ICEBERG_AGAINST | `iceberg.detected & iceberg.side opposes VAP.direction & severity ∈ {HIGH,CRITICAL}` | HIGH | C1 |
| 2 | ANOMALY_AGAINST | `anomaly.detected & anomaly.flow_relation opposes VAP.direction & severity ∈ {HIGH,CRITICAL}` | HIGH | C2 |
| 3 | REGIME_FLIP_M30 | `m30_bias` flipped against `VAP.direction` (require confirmed=True) | MEDIUM | C3 |
| 4 | REGIME_FLIP_D1 | `daily_trend` (B+C ensemble) flipped to opposite of `VAP.direction` | MEDIUM | C3 |
| 5 | MOMENTUM_FLIP | `delta_4h` crosses zero into opposite of `VAP.direction` (magnitude ≥ threshold) | LOW | C2 |
| 6 | PRICE_ACTION_STOP | `current_price` moved ≥ N×ATR(M30) against VAP from entry | HIGH | C4 |
| 7 | VIRTUAL_TP1 | `current_price` reached `VAP.tp1` | INFO | — |
| 8 | VIRTUAL_TP2 | `current_price` reached `VAP.tp2` | INFO | — |
| 9 | VIRTUAL_SL | `current_price` reached `VAP.sl` | HIGH | — |

**Anti-spam invariant:** at most one alert per `(vap_id, trigger_id)` pair. New trigger on already-EXIT_SUGGESTED VAP = alert framed as "ADDITIONAL TRIGGER".

### Outputs

#### A. canonical event in `position_events.jsonl`

```json
{
  "timestamp": "2026-04-27T05:30:00.000+00:00",
  "event_type": "MACRO_EXIT_SUGGESTED",
  "trigger": "ICEBERG_AGAINST",
  "severity": "HIGH",
  "vap_id": "c1d8e1fa",
  "vap_direction": "SHORT",
  "vap_entry_price": 4720.50,
  "vap_entry_ts": "2026-04-27T02:00:00+00:00",
  "vap_age_min": 210,
  "current_price": 4715.00,
  "unrealized_pts": 5.5,
  "all_active_triggers": ["ICEBERG_AGAINST"],
  "reason_human": "Iceberg detected on BUY side (severity=HIGH, refills=2)",
  "reason_machine": {
    "iceberg": {"side": "BUY", "severity": "HIGH", "refills": 2}
  }
}
```

#### B. heartbeat field in `service_state.json`

```json
"active_virtual_position": {
  "vap_id": "c1d8e1fa",
  "direction": "SHORT",
  "entry_price": 4720.50,
  "entry_ts": "2026-04-27T02:00:00+00:00",
  "age_min": 210,
  "current_price": 4715.00,
  "unrealized_pts": 5.5,
  "status": "EXIT_SUGGESTED",
  "active_triggers": ["ICEBERG_AGAINST", "REGIME_FLIP_M30"],
  "last_alert_ts": "2026-04-27T05:30:00+00:00",
  "expires_at": "2026-04-27T06:00:00+00:00"
}
```

When no VAP active → `"active_virtual_position": null`.

#### C. Telegram alert via `tg.notify_macro_exit(vap, trigger, reason)`

```
🚨 MACRO EXIT SUGGESTED — SHORT
Trigger: ICEBERG_AGAINST (HIGH)
Reason:  Iceberg on BUY side (severity=HIGH, refills=2)

Virtual entry: 4720.50 @ 02:00 UTC
Current:       4715.00 (+5.5 pts unrealized)
Age:           3h 30m
VAP id:        c1d8e1fa

⚡ Action: consider closing your SHORT position(s)
```

For ADDITIONAL TRIGGER on already-EXIT_SUGGESTED VAP:

```
⚠️ ADDITIONAL EXIT TRIGGER — SHORT
New trigger: REGIME_FLIP_M30 (MEDIUM)
m30_bias flipped: bearish → bullish (confirmed)

VAP id: c1d8e1fa | Active triggers: 2
(originally suggested exit at 05:30 UTC for ICEBERG_AGAINST)
```

### VAP lifecycle

```
                  ┌──────────────────────────────────────┐
GO SHORT/LONG  -> │ HELD                                 │
emitted to        │ no triggers fired                    │
decision_live     └──────────────────┬───────────────────┘
                                     │
              first trigger fires    │
                                     ▼
                  ┌──────────────────────────────────────┐
                  │ EXIT_SUGGESTED                       │
                  │ ≥1 trigger active                    │
                  │ subsequent triggers append + alert   │
                  └──┬──────────────┬────────────────────┘
                     │              │
   opposite GO       │              │  age > max_lifetime
   arrives           ▼              ▼
              ┌──────────────┐  ┌──────────────┐
              │ SUPERSEDED   │  │ EXPIRED      │
              │ (replaced by │  │ (4h default; │
              │  new VAP)    │  │  configurable)│
              └──────────────┘  └──────────────┘
```

A SUPERSEDED or EXPIRED VAP is removed from `active_virtual_position` heartbeat field; one final canonical event written for forensic record.

---

## Configuration

```python
# live/macro_monitor.py
MACRO_MONITOR_CONFIG = {
    "tick_interval_s": 2.0,
    "vap_max_lifetime_min": 240,        # 4h; CALIBRATION_TBD
    "trigger_iceberg_against": {
        "enabled": True,
        "min_severity": "HIGH",         # CRITICAL also fires
        "min_refills": 2,                # CALIBRATION_TBD
    },
    "trigger_anomaly_against": {
        "enabled": True,
        "min_severity": "HIGH",
    },
    "trigger_regime_flip_m30": {
        "enabled": True,
        "require_confirmed": True,
    },
    "trigger_regime_flip_d1": {
        "enabled": True,
        # Uses B+C ensemble (calibration v2_2026-04-26)
    },
    "trigger_momentum_flip": {
        "enabled": True,
        "min_magnitude": 30.0,           # CALIBRATION_TBD; abs(delta_4h) crossing zero
    },
    "trigger_price_action_stop": {
        "enabled": True,
        "atr_multiple": 0.5,             # CALIBRATION_TBD
    },
    "trigger_virtual_tp1": {"enabled": True},
    "trigger_virtual_tp2": {"enabled": True},
    "trigger_virtual_sl": {"enabled": True},
}
```

All `CALIBRATION_TBD` thresholds need Phase 0 calibration before production ship. Acceptance gate: each threshold has empirical justification (Bonferroni-corrected p < 0.0042 over 9.7m clean window) per Rule 3 G-PURDUE-CALIBRATION.

---

## Phases

### Phase 0 — Spec + calibration (~6-8h)
- Methodology grounding doc (this file, finalized)
- Threshold calibration on 9.7m clean window (`data/rebuild_2026-04-25/gc_ohlcv_l2_joined.parquet`)
- Per-trigger empirical analysis: how often did each trigger correctly anticipate a regime change vs false-alarm?
- Calibration artifact at `_audit/calibrations/macro_monitor_v1.md`

### Phase 1 — Implementation (~3-4h)
- `live/macro_monitor.py`: VAP state model + tick loop + 9 trigger evaluators
- `live/telegram_notifier.py`: add `notify_macro_exit(vap, trigger, reason)` + `notify_additional_trigger(...)`
- `live/event_processor.py`: extend service_state.json writer for `active_virtual_position` field
- `run_live.py`: wire MacroMonitor as background daemon (alongside PositionMonitor)
- Fix collateral bug `telegram_notifier.py:287` (`log` → `_log`)

### Phase 2 — Backtest validation (~2-3h)
For each historical GO decision in 9.7m window, simulate VAP and:
- Count MACRO_EXIT_SUGGESTED events fired
- For each event, check forward returns to determine if exit suggestion was CORRECT (reversal materialized within N hours) or FALSE_ALARM
- Per-trigger precision/recall
- False-alarm rate gate: per-trigger false-alarm rate ≤ 30% on validation window

### Phase 3 — Smoke tests (~1h)
- `tests/test_macro_monitor.py`: 20+ tests covering
  - VAP creation from GO decision
  - VAP supersede on opposite GO
  - VAP expiry after max_lifetime
  - Each of 9 triggers fires correctly with synthetic state
  - Anti-spam: 1 alert per (vap_id, trigger_id) pair
  - ADDITIONAL TRIGGER framing
  - SUPERSEDED + EXPIRED final-event emission
  - heartbeat field shape

### Phase 4 — Local commit + restart auth (~30min)
- Commit on `fix/xau-mid-population` (or new branch if scope big enough)
- NO push (Rule 11)
- Brief-back to Barbara with restart authorization request (Rule 9)
- After restart: verify VAP appears in heartbeat for next GO, alerts fire on Telegram

---

## Open questions for Barbara

1. **Multi-trigger framing**: one alert per trigger as it fires, OR one consolidated summary every N seconds with all active triggers? (proposal: per-trigger with anti-spam)
2. **Multiple concurrent VAPs**: support multiple (e.g. SHORT and LONG held simultaneously by operator at different brokers/accounts), OR latest-only? (proposal: latest-only for v1; multi-VAP in v2 if needed)
3. **Calibration window**: same 9.7m as B+C ensemble (`2025-07-01 → 2026-04-26`), OR dedicated bear-skewed window if available? (proposal: same 9.7m, document bull-skew limitation)
4. **Telegram channel**: same `CHAT_ID -1003418918465` as current decisions, OR separate "alerts" channel? (proposal: same channel; new icon prefix for distinction)
5. **Should MacroMonitor also alert on entry direction CHANGES** (e.g., system was SHORT-biased for hours, suddenly emits GO LONG — alert "regime confirmed flip!")? (proposal: out of scope for v1, separate FEATURE_REQUEST)
6. **Conflict with current PositionMonitor**: when MT5 IS connected and has real positions, should MacroMonitor and PositionMonitor BOTH fire alerts (potential duplication), OR MacroMonitor defers to PM when real position exists? (proposal: orthogonal — MM tracks decisions, PM tracks broker positions; minor overlap acceptable)
7. **VAP persistence across service restart**: rebuild VAP from decision_log.jsonl on startup, OR start fresh? (proposal: rebuild from decision_log to preserve continuity)

---

## Risks

| Risk | Mitigation |
|---|---|
| False-alert spam if thresholds too sensitive | Phase 0 calibration with explicit false-alarm rate gate (≤30%) |
| VAP based on stale decision_live.json | freshness check on decision_live; if file > 5min stale, mark VAP as STALE_INPUT and log warning |
| Operator confusion: MacroMonitor vs PositionMonitor alerts | distinct icon (🚨 for MM-EXIT, 🛡 for PM-real); explicit "VIRTUAL" framing in MM alerts |
| Configuration drift between MacroMonitor + EventProcessor gates | shared config module for direction definitions, severity thresholds |
| Telegram rate limit (30 msg/sec hard limit) | anti-spam (1 alert per vap_id+trigger pair) + per-channel rate limiter |
| Bug at telegram_notifier.py:287 silently breaks notify_execution | fix bundled in Phase 1 (`log` → `_log`) |

---

## What this DOES solve

- ✅ The 2026-04-27 02:00 UTC scenario: MM would have created a SHORT VAP on the GO SHORT, then fired REGIME_FLIP_M30 + ICEBERG_AGAINST + PRICE_ACTION_STOP triggers as the market reversed LONG. Operator gets Telegram alert(s) to manually close.
- ✅ Operates fully without MT5 broker — pure decision-driven.
- ✅ Visible in dashboard via `active_virtual_position` heartbeat field.
- ✅ Forensic trail in `position_events.jsonl` for postmortem analysis.

## What this does NOT solve

- ❌ Operator's actual broker positions: MM has no visibility into real P&L; operator must reconcile manually.
- ❌ Auto-execution of exits (intentional non-goal; alert-only).
- ❌ Bug in `event_processor.py` that calls `tg.notify_execution()` with action="GO" causing the NameError at telegram_notifier.py:287 (separate fix bundled in Phase 1).

---

## PRAC

1. **Overfit risk**: Phase 0 calibration on same 9.7m window risks overfitting. Mitigation: per-trigger walk-forward CV; document bull-skew per Phase 0 PRAC.
2. **Confirmation bias**: I want this feature to "work" for Barbara's morning scenario; might tune thresholds favourably. Mitigation: explicit false-alarm rate gate (≤30%) is a hard bar.
3. **Premise fragility**: design assumes `decision_live.json` is always fresh. If EventProcessor stalls, VAP also stalls. Mitigation: freshness watchdog.
4. **Missing tests**: design includes 20+ tests in Phase 3 plan.
5. **Scope creep**: 9 triggers may be too many for v1. Mitigation: open question #5 above; willing to ship v1 with subset.

## Sanity (design-time)

- HEAD `401e6da` (BIAS-DETECTION-PURDUE-CALIBRATION) untouched
- PID 16936 untouched (still running 401e6da)
- Capture services 8000/8002 untouched
- 0 code changes, 0 commits, 0 restarts during this design session
- Only artifact: this file + `_audit/pp_sprint/macro_monitor/` directory

# W4.5 + W4.6 — Proposals (NOT implemented)

**Status**: PROPOSAL ONLY — escopo grande, requer Barbara approval + ML-DS calibration antes de implementar.
**Author**: CC#3
**Date**: 2026-05-09

---

## W4.5 — Adaptive Position Sizing

### Source
- ATS Strategic Plan §5: "Manage risk via smaller position sizes (1/3 to 1/5 of total desired size)"
- ATS Implementation Strategic Plan: "1/3 to 1/5 initial size to weather volatility"
- Audit P3.5 in AUDITORIA_PROFUNDA_LIVE_2026-05-08.md

### Current state
`live/event_processor.py:_compute_session_lots()` retorna fixed `[0.02, 0.02, 0.01]` por session com bonus iceberg.
Não considera:
- Cycle confidence grade (A/B1/B2/B3 — TradeATS-style)
- Risk window width
- Day's volatility regime
- Daily PnL state

### Proposed design

**Key**: cycle confidence grade tiered position sizing.

```
GRADE_A   (high conf):  l1=0.03, l2=0.02, l3=0.01  → total 0.06 lots
GRADE_B1  (med-high):   l1=0.02, l2=0.02, l3=0.01  → total 0.05 lots (current)
GRADE_B2  (med):        l1=0.02, l2=0.01, l3=0.01  → total 0.04 lots
GRADE_B3  (med-low):    l1=0.01, l2=0.01, l3=0.01  → total 0.03 lots
GRADE_C   (low/skip):   skip entry
```

**Grade derivation** (proposed scoring):
```python
def _compute_cycle_grade(decision, m30_state) -> str:
    score = 0
    # Bias confirmation
    if m30_bias_confirmed and m30_bias != "unknown": score += 2
    # Daily trend alignment
    if daily_trend != "unknown" and decision.direction matches daily_trend: score += 2
    # H4 alignment (from gc_d1h4_bias.json)
    if h4_jac_dir matches decision.direction: score += 1
    # ATR regime
    if atr_regime in ("LOW", "MED"): score += 1
    # Iceberg alignment
    if decision.iceberg.aligned: score += 1
    # Score >= 6: A; 5: B1; 4: B2; 3: B3; <3: C (skip)
```

**Risk-adjusted further**:
- If daily_pnl < 0 and abs(daily_pnl) > 50% of daily_loss_limit:
  → reduce all lot sizes by 50% (defensive accumulation phase)
- If `m30_atr_extreme_pts > 35` (high volatility but not blocking yet):
  → cap l1/l2/l3 at min(current, fixed_max - vol_penalty)

### Implementation path
1. **Phase 1** (~1h): introduce grade computation, log only (shadow). Compare emitted grade vs actual signal quality (post-trade).
2. **Phase 2** (~2h): introduce sizing matrix in settings.json (configurable). Apply via `_compute_session_lots`.
3. **Phase 3** (~1h): add risk-adjustment overlay (PnL-based defensive scaling).
4. **Phase 4** (calibration ~few days): ML-DS calibrates grade thresholds via Youden+CV on historical decisions (mirror TradeATS calibration.yaml methodology).

### Risk
- Affects ALL trades (high blast radius)
- Wrong grade calibration → systematic over/under-sizing
- **Sistema OFF preferred for testing**
- Backtest validation required before deploy

### Settings keys (proposed)
```json
{
  "adaptive_sizing_enabled": false,
  "adaptive_sizing_mode": "shadow",
  "adaptive_sizing_grade_thresholds": {"A": 6, "B1": 5, "B2": 4, "B3": 3},
  "adaptive_sizing_lots": {
    "A":  [0.03, 0.02, 0.01],
    "B1": [0.02, 0.02, 0.01],
    "B2": [0.02, 0.01, 0.01],
    "B3": [0.01, 0.01, 0.01]
  },
  "adaptive_sizing_pnl_defensive_threshold_pct": 0.50,
  "adaptive_sizing_pnl_defensive_factor": 0.50
}
```

### ETA
- Implementação Phase 1+2+3: **~4h**
- Calibração Phase 4: **~3 dias** (ML-DS effort)
- **NÃO recomendado começar até W1+W2+W3 estarem em produção e estáveis**

---

## W4.6 — Risk Window Analysis Explicit

### Source
- ATS Strategic Plan §10: "Risk: 'What has to happen to break my idea?' — define risk window"
- ATS Strategy 1: "define risk window based on cycle; if price breaks below risk window, close all"
- ATS Strategy 2: "SL placement: near trend breakout extreme (spring low for long, upthrust high for short)"
- Audit P2.6 + Strategic Mandate #10

### Current state
SL placement é AD-HOC por strategy mode:
- RANGE_BOUND: `price ± sl_pts` fixo (W4.4 dynamic optional)
- PULLBACK: `price ± sl_pts` fixo
- CONTINUATION: displacement-bar low/high (W2.4 orientation guard)
- OVEREXTENSION reversal: `price ± sl_pts` fixo

**Não há concept central de "risk window broken → close all"**. Cada SL é independente.

### Proposed design

**Risk Window** = price corridor que invalida a hipótese ATS para o cycle atual. Defined per cycle phase:

| Cycle phase | Risk window upper | Risk window lower |
|---|---|---|
| **Markup (TRENDING_UP)** | recent swing high + 0.5*ATR | recent swing low - 0.2*ATR (spring) |
| **Markdown (TRENDING_DN)** | recent swing high + 0.2*ATR (upthrust) | recent swing low - 0.5*ATR |
| **Accumulation (RANGE_BOUND)** | M30 box_high + 0.5*ATR | M30 box_low - 0.5*ATR |
| **Distribution (RANGE_BOUND DN)** | M30 box_high + 0.5*ATR | M30 box_low - 0.5*ATR |

**Trigger**: price closes M5 bar OUTSIDE risk window for 2+ consecutive bars → cycle invalidated → CLOSE_ALL signal emitted (similar to existing MACRO_MONITOR_VAP exits).

**Difference from existing**: this is per-cycle (not per-trade SL). All open positions exit when cycle breaks. SL becomes per-trade buffer; risk window is the macro-level invalidation.

### Implementation path
1. **Phase 1** (~2h): compute risk window in `level_detector.py`; expose via service_state.json (telemetry)
2. **Phase 2** (~2h): add monitor in `position_monitor.py` that detects 2-bar break → emits `RISK_WINDOW_BREAK` event
3. **Phase 3** (~1h): wire RISK_WINDOW_BREAK → `_close_all_apex_positions()` (existing function in event_processor)
4. **Phase 4** (calibration ~2 days): ATR multiplier per cycle phase calibration via backtest

### Risk
- High-blast — closes all positions on signal
- False breakouts during low-liquidity periods → unnecessary closes
- Requires careful calibration of "break confirmation" (1 bar? 2? close vs intrabar?)
- **Conflicts with existing MACRO_MONITOR_VAP** — need integration design first

### Settings keys (proposed)
```json
{
  "risk_window_enabled": false,
  "risk_window_mode": "shadow",
  "risk_window_atr_buffer_pct": 0.50,
  "risk_window_close_confirmation_bars": 2,
  "risk_window_close_action": "close_all"
}
```

### ETA
- Phase 1+2+3 implementação: **~5h**
- Phase 4 calibração: **~2 dias**
- **Maior scope da W4** — recomendo deixar para sprint dedicado

---

## Recommendation

**Não implementar W4.5 e W4.6 agora**. Motivos:

1. Ambos têm blast-radius alto (afetam sizing global / fechamento global de posições)
2. Calibração rigorosa requerida (días, ML-DS effort) antes de armar
3. W1+W2+W3+W4 (até W4.4 + W4-A) já adicionaram 11+ gates novos — saturação de mudanças, precisamos validar em prod primeiro
4. Sistema OFF — sem feedback empírico das mudanças anteriores

**Caminho sugerido**:

A. Fechar W4 com o que já está implementado (W4.1, W4.2, W4.3, W4.4 opt-in, W4-A)
B. Validar W1+W2+W3+W4 com sistema ON (~1 semana smoke + monitoring)
C. Após estabilização: avaliar shadow/armed migration (mapeamento Barbara pediu)
D. Posteriormente: planejar W4.5 / W4.6 como sprints dedicados com calibração

---

## Status Wave 4

| | Implementado | Status |
|---|---|---|
| W4.1 | MFE/MAE tracking + Giveback shadow | ✅ |
| W4.2 | Daily range gate | ✅ |
| W4.3 | Session close detection | ✅ |
| W4.4 | Hard stop dynamic (opt-in, default OFF) | ✅ |
| W4-A | Layered D1 ATR extreme gate (TradeATS p95=190.6) | ✅ |
| W4.5 | Adaptive position sizing | 📋 PROPOSAL |
| W4.6 | Risk window analysis explicit | 📋 PROPOSAL |

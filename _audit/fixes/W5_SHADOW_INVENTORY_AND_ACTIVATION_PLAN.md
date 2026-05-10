# W5 — Shadow Inventory + Activation Plan

**Author**: CC#3
**Date**: 2026-05-09
**Status**: PROPOSAL — barbara approval per shadow before activation
**Scope**: `C:\FluxQuantumAI\live\` shadow features

---

## 1. Sumário executivo

Sistema atual tem **9 shadows ativos**. Cada um tem 2 dimensões:
- **Computação**: módulo roda, gera dados (ON em todos)
- **Atuação**: dados influenciam decisões automáticas

Categorias:
- **🌑 Shadow forever** (3): por design metodológico, não devem ser armed
- **🟡 Shadow → eventually armed** (4): aguardam validação / calibração
- **🔵 Needs design** (2): intent ainda em discussão

---

## 2. Inventário completo

### 2.1. **D1/H4 Updater** (`live/d1_h4_updater.py`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | D1/H4 boxes + JAC direction + bias composite + (W4-A) ATR14 last-closed |
| **O que escreve** | `gc_h4_boxes.parquet`, `gc_d1_boxes.parquet`, `gc_d1h4_bias.json` |
| **O que execução lê** | Apenas `gc_d1h4_bias.json` via `_read_d1h4_bias_shadow()` |
| **Uso atual** | Telemetria (service_state.json `d1h4_bias` field), W4-A ATR extreme gate |
| **Categoria** | 🌑 **SHADOW FOREVER** |
| **Por quê** | ADR-001/002 mandato: D1/H4 nunca para execução de níveis. Bias direção/regime ok, levels forbidden |
| **Sprint origem** | FASE 4a |
| **Settings** | n/a (não há flag `_armed`; armed level seria violação ADR-001) |
| **Veredicto** | **NÃO ATIVAR** parquets para execution. Aceitável armar `bias_direction` como gate (próximo nível abaixo do daily_trend) |

### 2.2. **ATS Trend Line** (`live/ats_trend_line.py`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | 3-candle FVG + group inefficiency tracker → daily_trend tri-state |
| **O que escreve** | Inline returns (não persiste arquivo dedicado) |
| **O que execução lê** | Existem 318 LOC, mas grep mostra 0 calls em event_processor — **NÃO consumido** |
| **Categoria** | 🟡 **SHADOW → EVENTUALLY ARMED** |
| **Por quê** | Per ATS Strategic Plan, ATS Trend Line é o filter direcional principal por design. Atualmente em shadow porque pre-validation |
| **Sprint origem** | Sprint C v2 / Part A |
| **Settings** | `ats_trend_line_enabled: true` + `ats_trend_line_shadow_mode: true` |
| **Backtest artifacts** | `_audit/pp_sprint/ats_trend_line_part_a/` |
| **Validação para armar** | (1) backtest pass, (2) calibration de threshold, (3) integration design (V0 gate? cascade Layer 0?) |
| **ETA armar** | ~3-4h spec + 2-3 dias validation backtest |

### 2.3. **T3 Defense Exit** (`live/position_monitor.py:_check_t3_defense_exit`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | (1) Defense mode ativo + (2) adverse move ≥ T3_ADVERSE_PTS em T3_WINDOW_S + (3) M30 structural break |
| **O que faz no LIVE armado** | **Fecha posição** (mesmo em profit) — RISK exit |
| **Atualmente** | SHADOW: log only |
| **Categoria** | 🟡 **SHADOW → EVENTUALLY ARMED** |
| **Sprint origem** | 2026-04-14 (vela 10:30 UTC 30pt drop em 4min) |
| **Settings** | `t3_exit_mode: "SHADOW"` (mudar para `"LIVE"` arma) |
| **Kill switch** | File `C:/FluxQuantumAI/DISABLE_T3_EXIT` desativa regardless |
| **Validação para armar** | (1) backtest historical anomalies, (2) ML-DS sign-off, (3) shadow log analysis (false positive rate) |
| **Risco** | Ação agressiva — fecha posições. Falso positivo = saída precoce. Mas sistema OFF + brokers desconectados → **risco zero hoje** |
| **ETA armar** | ~1h (mudar setting + restart) APÓS backtest |

### 2.4. **Breaking Ice** (config flag `breaking_ice_log_only`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | Detecção de price exceeding iceberg by `breaking_ice_price_exceed_pts` (2.2pts) within `breaking_ice_lookback_min` (4min) |
| **Atualmente** | LOG ONLY — `[BREAKING_ICE]` log line |
| **Categoria** | 🔵 **NEEDS DESIGN** |
| **Por quê** | Intent unclear: score adjustment? Hard block? +1 confirmation bonus? |
| **Settings** | `breaking_ice_enabled: true`, `breaking_ice_log_only: true` |
| **Local** | `ats_live_gate.py:833-836` |
| **Validação para armar** | (1) Barbara decision — score bonus or block, (2) calibration |
| **ETA decisão** | Discussion + ~1h impl |

### 2.5. **Iceberg Zones** (config flag `iceberg_zones_log_only`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | Iceberg zone proximity ≤ `iceberg_zones_proximity_pts` (5.0pts) |
| **Atualmente** | LOG ONLY — `[ICEBERG_ZONE]` log line |
| **Categoria** | 🔵 **NEEDS DESIGN** |
| **Settings** | `iceberg_zones_enabled: true`, `iceberg_zones_log_only: true` |
| **Local** | `ats_live_gate.py:838-844` |
| **Validação para armar** | Mesmo que Breaking Ice — design decision |
| **ETA decisão** | Mesmo |

### 2.6. **V3 RL Agent** (`v3.mode: disabled` em settings.json)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | RL-based entry/management decisions (PPO model) |
| **Atualmente** | DISABLED (mode=disabled) — não executa |
| **Categoria** | 🌑 **SHADOW FOREVER** (até modelo treinado) |
| **Sprint origem** | V3 RL Predictive Model (FUNC_V3_RL_Predictive_Model_20260408) |
| **Settings** | `v3.mode: "disabled"` (alternativas: `shadow`, `live`) |
| **Validação para armar** | (1) modelo `v3_ppo.zip` treinado (atualmente vazio?), (2) Paperspace GPU training, (3) reward weights calibration (todos `_note: "ALL TBD"`), (4) demo trading shadow validation, (5) ML-DS sign-off |
| **ETA armar** | **3-6 meses** — work não começou |
| **Veredicto** | **DEFERRED** — escopo separado, não W5 |

### 2.7. **Grenadier Guardrail** (`grenadier_guardrail.py`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | Stat-Guardrail (Sprint 1 "The Shield") — latência stale (>2000ms) + spread vacuum (>10 ticks) |
| **Atualmente** | **ARMED** — `ats_live_gate.py:849-856` returns hard_block se `_gr.is_safe == False` |
| **Categoria** | ✅ **ARMED** (não é shadow) |
| **Importante** | Já está blocking entries quando feed stale ou spread vacuum |
| **Sem ação necessária** | n/a |

### 2.8. **MACRO_MONITOR_VAP** (`macro_monitor.py`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | 7 broker-independent exit triggers para virtual positions |
| **Atualmente** | **ARMED** (commit 21bfac3) |
| **Categoria** | ✅ **ARMED** |
| **Sem ação necessária** | n/a |

### 2.9. **PM-DECOUPLE-PHASE3** (`position_monitor.py`)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | PositionMonitor read-path independent of MT5 — virtual position tracking |
| **Atualmente** | **ARMED** (commit 889f5ee) |
| **Categoria** | ✅ **ARMED** |

### 2.10. **Overextension** (demoted to SHADOW per EXEC-5)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | Overextension via `overextension_atr_mult: 1.5` |
| **Estado** | SHADOW per EXEC-5 (2026-04-26): "overextension demoted to SHADOW; LOGIC-C ACTIVE" (`event_processor.py:516`) |
| **Atualmente** | Computado mas NÃO bloqueia — IMPL-3 LOGIC-C scorer é o ACTIVE gate |
| **Categoria** | 🌑 **SHADOW FOREVER** |
| **Por quê** | Substituído por LOGIC-C convergent-evidence (Wyckoff 2.0). Antiga lógica overextension = comparison baseline only |
| **Sem ação necessária** | n/a (design decision já tomada) |

### 2.11. **DISPLACEMENT_DIVERGE** (M5/M30 mode comparison)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | 3 modos paralelos de displacement detection: CURRENT (M5 vs M30 ATR), OPT_A (M30 vs M30 ATR), OPT_B (M5 vs M5 ATR) |
| **Atualmente** | OPT_A é o ACTIVE; CURRENT + OPT_B são SHADOW comparison logging |
| **Categoria** | 🌑 **SHADOW FOREVER** (comparison only, decisão arquitetural já tomada) |
| **Local** | `event_processor.py:_detect_trend_displacement` |
| **Sprint origem** | FASE 3 LIVE 2026-04-14 |
| **Sem ação necessária** | n/a |

### 2.12. **W4.1 Giveback rule** (recém adicionado)

| Aspecto | Detalhe |
|---|---|
| **O que computa** | MFE/MAE per posição + Giveback signal (50% MFE retracement) |
| **Atualmente** | LOG ONLY — `GIVEBACK_SHADOW` log line |
| **Categoria** | 🟡 **SHADOW → EVENTUALLY ARMED** |
| **Sprint origem** | W4.1 (2026-05-09) |
| **Validação para armar** | (1) shadow log review (~1 semana de produção), (2) calibração giveback_pct |
| **ETA armar** | ~1 semana |

---

## 3. Matriz de Decisão

| Shadow | Categoria | Validação | Risco | Ativar? |
|---|---|---|---|---|
| D1/H4 Updater (parquets) | 🌑 SHADOW FOREVER | n/a | n/a | ❌ ADR-001 forbidden |
| D1/H4 bias_direction | 🌑 mostly | n/a (já consumido como telemetry) | n/a | Já tem uso indireto |
| ATS Trend Line | 🟡 EVENTUALLY ARMED | Backtest + threshold cal | MED (filter direcional novo) | ✅ Após validação |
| T3 Defense Exit | 🟡 EVENTUALLY ARMED | Backtest historical + ML-DS | HIGH (fecha posições) | ✅ Após validação |
| Breaking Ice | 🔵 NEEDS DESIGN | Barbara decision | LOW | Pendente design |
| Iceberg Zones | 🔵 NEEDS DESIGN | Barbara decision | LOW | Pendente design |
| V3 RL Agent | 🌑 DEFERRED | Treinamento model | n/a | 3-6 meses |
| W4.1 Giveback | 🟡 EVENTUALLY ARMED | Shadow log 1 semana + cal | MED (exit) | ✅ Após observation |

---

## 4. Sequência de Ativação Proposta

### Fase 0 — Pré-requisitos (todos)
- ✅ W1+W2+W3+W4 já implementados (em código, sistema OFF)
- ⬜ Validar com sistema ON: 1 sessão de mercado completa, brokers conectados, 0 file-lock errors, 0 inversões
- ⬜ Confirmar que mudanças W1-W4 não introduziram regressão observável

### Fase 1 — Quick wins low-risk (1-2 dias)
**Order**: simples → complexo, low-risk → high-risk

#### W5.1 — Breaking Ice + Iceberg Zones design decision (PRECEDE armar)
- Barbara decisão: score bonus (+1 / +2)? hard block? log only?
- Reading: `ATS Docs/Everything to Know About Liquidity Lines.txt` para guidance
- Output: settings.json keys + spec
- **ETA**: ~30 min discussion + ~1h impl quando decidido

### Fase 2 — Defensive arming (~1 semana)

#### W5.2 — T3 Defense Exit ARM
**Pré-requisitos**:
- (1) Backtest historical anomaly events (`_audit/anomaly_events_2025-2026.csv`)
- (2) Shadow log analysis: quantos `_check_t3_defense_exit SHADOW` triggered em 2 semanas? false positive rate?
- (3) ML-DS sign-off

**Implementação**:
- `settings.json`: `t3_exit_mode: "SHADOW" → "LIVE"`
- Restart NSSM
- 24h observation
- Rollback: file `C:/FluxQuantumAI/DISABLE_T3_EXIT` (kill switch already implemented)

**Risco**: HIGH (fecha posições, mesmo em profit)
**Mitigação**: kill switch + observation

**ETA**: 2 dias backtest + 1h arm + 24h obs

#### W5.3 — W4.1 Giveback rule ARM
**Pré-requisitos**:
- (1) 1 semana production log de `GIVEBACK_SHADOW` events
- (2) Calibrar `giveback_pct` per regime (atualmente fixed 50%)
- (3) Definir action: full close vs SL-tighten vs telegram alert only

**Implementação**:
- Add settings keys: `giveback_arm_action: "alert"|"sl_tighten"|"close"`
- Convert log → action call
- Backtest comparison

**Risco**: MED (exit signal)
**ETA**: 1 semana shadow + ~2h impl

### Fase 3 — Strategic arming (~2-4 semanas)

#### W5.4 — ATS Trend Line ARM
**Maior decisão**: integration design.

**Opções**:
- A. Cascade Layer 0: ATS Trend Line direção é o primeiro filter — antes mesmo de daily_trend
- B. V0 Gate: pre-V1 zone gate (block entries contra ATS Trend Line)
- C. Score modifier: aligned = +1; counter = -2

**Pré-requisitos**:
- (1) Backtest com cada opção (A/B/C) sobre dataset Apr-May 2026
- (2) Calibration: e.g. ATS Trend Line confidence threshold
- (3) Integration spec via FluxQuantumAI_NextGen project (NextGen Perception layer)

**Risco**: MED-HIGH (filter direcional novo afeta ALL entries)
**Mitigação**: shadow → arm progressivo (mode=shadow → mode=score_modifier → mode=hard_filter)

**ETA**: 1-2 semanas backtest + spec + 1 semana shadow → arm

### Fase 4 — Future / DEFERRED

#### W5.5 — V3 RL Agent ARM (DEFERRED)
- Não começou. Modelo `v3_ppo.zip` placeholder.
- Reward weights "ALL TBD".
- **ETA: 3-6 meses** se priorizado. Recomendação: deferir para depois NextGen.

---

## 5. Recomendação imediata

**Prioridade 1** (esta semana):
- **Barbara decide W5.1**: Breaking Ice + Iceberg Zones (score / block / log) → 30min discussão + 1h impl
- **Validar W1-W4 com sistema ON 1 sessão**: confirma que mudanças não regrediram nada

**Prioridade 2** (próximas 2 semanas):
- **W5.2 T3 Defense Exit**: backtest + arm (HIGH value, forensic-driven, 1 incident já justificou — 2026-04-14)
- **W5.3 W4.1 Giveback**: 1 semana de shadow log + arm conservador (alert-only first)

**Prioridade 3** (próximo mês):
- **W5.4 ATS Trend Line**: maior payoff metodológico (alinha com canonical ATS Strategic Plan), maior risco — fazer last

**Diferido**:
- W5.5 V3 RL — não começar até NextGen Perception layer estar maduro

---

## 6. Open questions para Barbara

1. **Breaking Ice + Iceberg Zones** intent: score bonus, hard block, ou apenas log forever?
2. **T3 Defense Exit**: tens histórico de anomaly events para backtest, ou precisamos coletar shadow logs primeiro?
3. **Giveback rule**: action preferred — alert telegram only, SL tighten, ou full close on giveback?
4. **ATS Trend Line**: integração via cascade Layer 0, V0 gate, ou score modifier? Decisão metodológica.
5. **Sequência**: prefere ativar tudo de uma vez (risco), ou um por semana (conservador)?

---

## 7. Status

- W5 inventory: ✅ DONE
- W5 plan: 📋 PROPOSAL (este arquivo)
- W5.1-W5.5 implementação: aguarda decisões Barbara

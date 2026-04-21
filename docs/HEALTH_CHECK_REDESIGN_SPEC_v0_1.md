# HEALTH CHECK REDESIGN — SPEC

**Status:** DRAFT v0.1 — aguarda revisão Barbara
**Date:** 2026-04-17
**Prerequisites:** D1H4 daemon reactivado (ou substituído) — sem isso, D1/H4 bias fica congelado
**Target:** Telegram health check message format revised

---

## PROBLEMA ACTUAL

Telegram health check mostra apenas **M30 bias** como "bias".

**Confusão resultante:**
- M30 bias pode estar bearish mesmo em uptrend D1/H4
- Profissional lendo "bias: bearish" interpreta como "sistema considera short overall"
- Realidade: M30 pullback em trend maior bullish

**Descoberta adicional:** D1/H4 daemon desligado há 9-15 dias (shadow refactor abandonado). `d1h4_bias` no state_json é HISTÓRICO CONGELADO, não representa state actual.

---

## REQUISITOS DO NOVO HEALTH CHECK

### Multi-timeframe bias display

Mostrar **três timeframes distintos**, não apenas um:

```
📊 BIAS MULTI-TIMEFRAME
  D1:  LONG (strong)        ← daily trend
  H4:  LONG (strong)        ← 4-hour momentum
  M30: BEARISH (confirmed)  ← current structural
```

**Interpretação esperada:** "Estamos em uptrend maior, com pullback actual M30"

### Additional fields wanted

Para além dos 3 biases, a mensagem health check deve incluir:

| Field | Source | Format |
|---|---|---|
| Timestamp (UTC) | Get-Date | `2026-04-17 18:30:00 UTC` |
| Sistema uptime | service state | `2h 15min` |
| Sessão actual | SessionProvider | `NY_AM` / `ASIA` / etc |
| AMD phase (PO3) | quando P6b implementado | `DISTRIBUTION` / etc |
| MT5 broker status | mt5_*_connected | `RoboForex: ✅` / `Hantec: ❌` |
| Capture status | 3 PIDs | `Capture 3/3 ✅` |
| Última decisão | decision_log | `GO LONG 14:22` |
| Decisões últimas 1h | decision_log | `12 (3 GO, 8 BLOCK, 1 EXEC_FAILED)` |
| Posições abertas | position state | `0` |
| P&L dia | position state | `+$45.20` |
| STRUCTURE_STALE_BLOCK count 1h | stdout log grep | `5` |
| Errors críticos 1h | stderr log grep | `0 ✅` |

### Alert thresholds

Health check deve incluir **alerts automáticos**:

- ❌ **Critical:** MT5 broker disconnected
- ⚠️ **Warning:** Capture process faltando
- ⚠️ **Warning:** STRUCTURE_STALE_BLOCK > 50 in last hour (feed badly stale)
- ❌ **Critical:** Tracebacks detected (using new regex corrigido)
- ⚠️ **Info:** No GO decisions in last 2h (sistema parado — pode ser normal)

---

## DEPENDÊNCIAS

1. **D1H4 daemon reactivado** (ou substituído por solução live)
   - Sem isto, bias multi-TF mostra dados obsoletos
   - **Blocker** para redesign

2. **Timestamps UTC padronizados** em todos os logs
   - Actual: alguns em UTC+N broker, outros em UTC
   - Precisa normalizar

3. **Session provider disponível**
   - Se queremos mostrar sessão actual, precisa P6a Session Window implementado
   - Fallback: hardcode session windows como regra simples

---

## PROPOSTA DE FORMATO

```
🦊 FluxQuantumAI APEX — Health Check
📅 2026-04-17 18:30:00 UTC
⏱  Uptime: 2h 15min
🕐 Sessão: NY_AM

═══ MULTI-TIMEFRAME BIAS ═══
📈 D1:  LONG (strong)
📈 H4:  LONG (strong)
📉 M30: BEARISH (confirmed)
🎯 Interpretação: Pullback em uptrend

═══ CONEXÕES ═══
🔴 MT5 RoboForex: DISCONNECTED
⚪ MT5 Hantec: intentional (pausado)
🟢 Capture: 3/3 PIDs

═══ ACTIVIDADE (última 1h) ═══
📊 Decisões: 12 (GO: 3, BLOCK: 8, EXEC_FAILED: 1)
🧊 STRUCTURE_STALE_BLOCK: 5
❌ Errors: 0
📦 Posições abertas: 0
💰 P&L dia: +$0.00

═══ ÚLTIMA DECISÃO ═══
🕓 14:22 UTC
📍 LONG @ 4892.30
🎯 SL: 4885 | TP1: 4905 | TP2: 4915
🤖 Confidence: 0.72

═══ ALERTAS ═══
🔴 CRITICAL: MT5 RoboForex disconnected — executions will fail
⚠️ WARNING: 1 EXEC_FAILED in last hour
```

---

## SEQUÊNCIA DE IMPLEMENTAÇÃO

1. **D1H4 daemon — decidir estratégia:**
   - Reactivar com perf fix (mais leve do que ficou antes)
   - OU substituir por computação on-demand dentro do health check
   - OU usar outro timeframe provider

2. **Design doc formal** (`MODULE_DESIGN_DOC` template)

3. **ClaudeCode implementation**

4. **Test: simulate scenarios:**
   - D1 long + H4 long + M30 bear (pullback cenário)
   - D1 short + H4 short + M30 bull (bear rally cenário)
   - MT5 disconnected alert
   - Capture missing alert

5. **Deploy:**
   - Shadow mode 24h (envia nova versão + manter antiga)
   - Compare readability com Barbara
   - Switch

---

## QUESTÕES PARA BARBARA

Q1: Frequência de health check: actual é 1/hora? Quer manter, aumentar (30min), diminuir (4h)?

Q2: Alertas devem ir para mesmo chat Telegram ou chat separado de "alerts"?

Q3: P&L mostra apenas dia, ou semana + mês + inception também?

Q4: Formato final — markdown Telegram? Com emojis como proposta? Mais sóbrio?

Q5: D1H4 daemon — reactivar ou substituir? Decidir primeiro.

---

## PRIORIDADE

**MEDIUM** (não crítica, mas útil)

**Blocker real actual:** MT5 connection (Priority 1) e D1H4 daemon (Priority 2 para bias correcto). Sem estes, health check redesign seria cosmetic.

Ordem real recomendada:
1. Fix MT5 connection (discovery em progresso)
2. Reactivar D1H4 daemon
3. Health check redesign com dados correctos

---

**Para revisão Barbara quando houver tempo.**

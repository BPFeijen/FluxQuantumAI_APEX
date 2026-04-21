# ClaudeCode Prompt — APEX Stabilization Sprint (Deploy A + Deploy B)

Quero um sprint fechado de estabilização do APEX em produção, mas com uma regra absoluta:

**NÃO aplicas nada directamente em produção.**
Entregas:
1. plano,
2. diff,
3. validação local,
4. rollback plan,
5. instruções de deploy.
Depois paras e aguardas review da Barbara.

**NÃO faças perguntas abertas.**  
**NÃO inventes novos fixes.**  
**NÃO alteres estratégia, sizing, scoring, calibration thresholds, executor routing, iceberg/anomaly logic.**  
**NÃO toques em captura de dados.**  
**NÃO pares serviços de captura.**

---

## OBJETIVO

Estabilizar o sistema em 7 fixes já definidos, respeitando **Single Source of Truth**:

- `decision_live.json` = latest decision snapshot
- `decision_log.jsonl` = append-only audit trail
- `service_state.json` = heartbeat/health independente do feed

Dashboard / Telegram / Executor devem reflectir **EXACTAMENTE** o output do Decisor.  
**Se não está no output do Decisor, não existe.**

---

## REGRAS CRÍTICAS

1. **NÃO aplicar em produção.**
2. Trabalhar em diff mínimo e cirúrgico.
3. Entregar tudo pronto para deploy manual.
4. Separar explicitamente:
   - **VALIDADO LOCALMENTE**
   - **PENDENTE DE VALIDAÇÃO NO SERVIDOR**
5. Criar rollback plan **ANTES** de qualquer instrução de restart/deploy.
6. Para **Fix #2**:
   - **NÃO** escolher thresholds por intuição
   - **NÃO** escolher arquitectura entre múltiplas opções por conta própria
   - usar **APENAS**:
     - **Opção primária:** H4 override / confirmation
     - **Opção secundária:** ATR invalidation **CALIBRADA data-driven**
   - **NÃO** usar opção de sequência M30 cosmética
7. Se um ponto depender de calibração data-driven, entregar:
   - método,
   - script,
   - evidência,
   - valor escolhido,
   mas **NÃO aplicar em produção**.

---

## ESTRATÉGIA DE ENTREGA

Trabalhar em **2 deploy bundles**:

### DEPLOY A (plumbing / observability / menor risco)
- Fix #3 — canonical publish path
- Fix #1 — surface M30_BIAS_BLOCK
- Fix #6 — Telegram observability
- Fix #7 — M5/M30 discrepancy

### DEPLOY B (core logic / restart-required / maior risco)
- Fix #2 — redesign derive_m30_bias
- Fix #4 — re-enable ApexNewsGate
- Fix #5 — feed staleness / FEED_DEAD / heartbeat

**IMPORTANTE:**
- DEPLOY A e DEPLOY B devem ser entregues separados
- não fundir os bundles
- não assumir aplicação automática

---

## FIXES

## FASE A1 — FIX #3: RESTORE CANONICAL PUBLISH PATH

Implementar publish canónico para **TODOS** os outcomes:
- GO
- BLOCK
- EXEC_FAILED
- SKIP
- FEED_DEAD
- M30_BIAS_BLOCK
- BLOCK_V1_ZONE
- DISPLACEMENT_DIVERGE
- exhaustion / overextension
- PM events

### Regras
- `decision_live.json` via overwrite atómico
- `decision_log.jsonl` append-only
- `service_state.json` independente do feed
- heartbeat não pode depender do tick loop

### Critério de aceite
- todos os outcomes acima entram no audit trail
- `decision_live.json` actualizado pelo processo real
- `service_state.json` continua a actualizar mesmo com feed DEAD

---

## FASE A2 — FIX #1: SURFACE M30_BIAS_BLOCK

### Implementar
- `M30_BIAS_BLOCK` deve virar BLOCK canónico
- preencher `blocks[]` com gate/code/reason
- garantir visibilidade em API/dashboard/Telegram payload

### Critério de aceite
- `M30_BIAS_BLOCK` visível em:
  - `decision_live.json`
  - `decision_log.jsonl`
  - `/api/live`
  - payload do Telegram

---

## FASE A3 — FIX #6: TELEGRAM OBSERVABILITY

### Implementar
- telegram failures deixam de ir para debug invisível
- criar heartbeat Telegram com:
  - sends
  - failures
  - last_success_at
  - last_failure_at
  - last_dedup_reason

### Critério de aceite
- falhas visíveis no log e/ou `service_state`/API
- heartbeat Telegram acessível

---

## FASE A4 — FIX #7: RESOLVE M5/M30 DISCREPANCY

### Implementar
- discrepancy persistente não pode ficar só em warning
- quando persistir > N minutos:
  - tornar observável/canónico
  - e suprimir ou controlar comportamento inconsistente
- **NÃO** alterar estratégia de entrada; só tornar determinístico e visível

### Critério de aceite
- discrepancy persistente gera evento claro e comportamento previsível

---

# DEPLOY B

## FASE B1 — FIX #2: REDESIGN derive_m30_bias

Implementar em 2 partes:

### Parte 1 — Separação confirmed vs provisional
- confirmed e provisional rigorosamente separados
- provisional nunca hard-blocka

### Parte 2 — Regime invalidation
Arquitectura permitida:
- **H4 override / confirmation** como mecanismo primário
- **ATR invalidation** como mecanismo secundário

### PROIBIDO
- escolher X ATR por intuição
- patch cosmético de `>` vs `>=` como “solução”
- sequência M30 como solução principal

### OBRIGAÇÃO
- criar phase de calibração data-driven para o ATR invalidation:
  - usar 10 meses de histórico disponível
  - walk-forward
  - regime-segmented
  - escolher X com evidência empírica
- entregar script + métricas + valor final escolhido

### Critério de aceite
- backtest local baseline vs novo bias
- métricas mínimas:
  - número de `M30_BIAS_BLOCK`
  - contra-trades
  - regimes invalidados
  - discrepâncias persistentes
- expor no contexto:
  - `m30_bias_confirmed`
  - `provisional_m30_bias`
  - `m30_regime_state`

---

## FASE B2 — FIX #4: RE-ENABLE ApexNewsGate

### Implementar
- corrigir import/package path
- startup não pode engolir erro silenciosamente
- expor health:
  - enabled
  - degraded
  - unavailable

### Critério de aceite
- import error desaparece
- startup e health mostram estado real

---

## FASE B3 — FIX #5: FEED STALENESS / FEED_DEAD

### Implementar
- `FEED_DEAD` entra no caminho canónico
- heartbeat independente do feed
- expor age/status de M1/M5/M30

### Critério de aceite
- `service_state` actualiza com feed morto
- `FEED_DEAD` visível no audit trail
- `/api/system_health` reflecte estado real

---

## ROLLBACK PLAN OBRIGATÓRIO

Antes de qualquer instrução de restart/deploy, entregar rollback plan explícito:

1. lista exacta de ficheiros alterados
2. comando de backup por ficheiro
3. comando de restore por ficheiro
4. ordem de rollback
5. critério objectivo de rollback

### Exemplo de critério
- `decision_live.json` deixa de actualizar
- `/api/live` diverge do `decision_live`
- Telegram deixa de reflectir BLOCKs
- exceptions novas em loop core
- bias context passa a `unknown` de forma anómala

---

## DELIVERABLE OBRIGATÓRIO

Entregar em **6 secções**:

### 1. PLAN.md
- fases
- ficheiros por fix
- risco por fase
- bundle Deploy A / Deploy B

### 2. DIFF SUMMARY
- por ficheiro
- função a função
- old vs new logic

### 3. VALIDATION LOCAL
- `py_compile`
- checks locais
- prova writer/api/health/telegram alignment onde possível

### 4. FIX #2 CALIBRATION NOTE
- método data-driven
- janela temporal usada
- walk-forward
- regime segmentation
- valor X escolhido com evidência
- resultados baseline vs novo bias

### 5. ROLLBACK PLAN
- backup
- restore
- restart order
- rollback criteria

### 6. NO-REGRESSION STATEMENT
Confirmar explicitamente que **NÃO** alteraste:
- sizing
- scoring
- iceberg/anomaly logic
- executor routing
- calibration thresholds estratégicos
- strategy triggers

---

## VALIDAÇÃO FINAL

Separar explicitamente:

### VALIDADO LOCALMENTE
### PENDENTE DE VALIDAÇÃO NO SERVIDOR

**Não aplicar nada.**  
**Não reiniciar nada.**  
**Não assumir aprovação implícita.**  
**Pára após entregar o material.**

---

## NOTA FINAL

Se encontrares conflito entre “entregar já” e “segurança de produção”, **ganha a segurança**.

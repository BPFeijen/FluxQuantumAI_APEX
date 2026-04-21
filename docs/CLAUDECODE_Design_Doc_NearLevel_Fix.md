# TASK: DESIGN DOC — near_level + level_detector Direction-Aware Fix

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Output:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\DESIGN_DOC_near_level_direction_aware.md`
**Mode:** DESIGN ONLY. Zero code changes. Documento de arquitectura para review.

---

## CONTEXTO & PROBLEMA IDENTIFICADO

Bug estrutural descoberto 2026-04-20 06:15 UTC na análise da entrada 03:14 que entrou em 4791 com `liq_top_mt5=4767.53` (23pts ABAIXO do preço):

**Root cause:**
- `level_detector` mantém UM level (último em tempo), sem consciência direccional
- `_near_level` usa `abs(price - level) <= 8.0` — aceita level em qualquer lado
- Quando confirmed box fica stale (>15min), fallback unconfirmed pode estar no lado errado para a direcção
- Resultado: SHORT entries com "resistência" ABAIXO do preço (nonsense), LONG entries com "suporte" ACIMA do preço (nonsense)
- Zona correcta existia apenas como unconfirmed 40-60min depois — sistema não re-avalia

**Impacto:** Não é edge case. Afecta todas as decisões em regime trending onde confirmed box envelhece e fallback cai no lado errado.

**Referência:** memória `feedback_critical_analysis_ats` sinaliza esta categoria (BUEC/entry-side inversion).

---

## SCOPE DO FIX (apertado — confirmado Barbara)

**Dentro scope:**
- `live/level_detector.py` — retornar lista ranqueada de levels por direcção + frescura
- `live/event_processor.py:_near_level()` + `_trending_v1()` — filtragem direccional
- Re-avaliação quando box fresco surge no lado correcto
- Rejeição explícita se level já foi ultrapassado pelo preço

**Fora scope:**
- V1/V2/V3/V4 gates overall logic (só o ponto de consulta ao level)
- Triggers ALPHA/BETA/GAMMA/DELTA
- near_level_source priorização além de direction (deixar como está)
- Position management, SL/TP

---

## ESTRUTURA DO DESIGN DOC

O design doc deve conter os seguintes capítulos. Escrever com rigor — este é o contrato para implementação futura.

### 1. EXECUTIVE SUMMARY
- Problema em 3 linhas
- Solução proposta em 3 linhas
- Impacto esperado (qualitativo)

### 2. ARCHITECTURE ANÁLISE: ESTADO ACTUAL

#### 2.1 level_detector actual
- Para cada método público, documentar: input, output, side effects
- Estado interno mantido: `self.liq_top`, `self.liq_bot`, `self.m5_confirmed_box_ts`, etc.
- Lógica de fallback confirmed → unconfirmed (M5_FALLBACK_H=0.25)
- Diagram textual do flow de decisão

#### 2.2 _near_level actual
- Código actual literal (copy paste com line numbers)
- Invariantes que o código assume
- Quando é invocado e por quem

#### 2.3 _trending_v1 actual
- Como usa box_high / box_low
- Route alternativa que processou o signal 03:14

#### 2.4 Diagrama de dados: como um box vira level usado numa decisão
- M5_UPDATE → box_id → level_detector state → _near_level → V1 gate → decision

### 3. PROBLEMA FORMAL

#### 3.1 Caso de falha reconstruído (sinal 03:14:46)
- Timeline: que box estava confirmed, quando ficou stale, que unconfirmed foi fallback
- Valores exactos: preço, liq_top_mt5, distância, direction escolhida
- Por que V1 passou (_trending_v1 route)
- Que boxes existiam 40-60min depois que seriam "lado correcto"

#### 3.2 Tabela de 4 cenários
Para cada combinação (confirmed_fresh/stale) × (fallback_correct_side/wrong_side):

| # | Confirmed | Fallback | Comportamento actual | Comportamento correcto |
|---|---|---|---|---|
| 1 | Fresh, correct side | N/A | ✅ OK | ✅ OK |
| 2 | Fresh, wrong side | N/A | ⚠ bug (não investigado) | reject |
| 3 | Stale, fallback correct | Used | ✅ OK | ✅ OK |
| 4 | Stale, fallback wrong | Used | ❌ BUG | reject or wait |

Detalhar cada linha.

### 4. SOLUÇÃO PROPOSTA — CONTRATO

#### 4.1 New level_detector API
```python
class LevelDetector:
    def get_levels_for_direction(
        self,
        direction: Literal["SHORT", "LONG"],
        price: float,
        max_age_min: float = 15.0,
        max_distance_pts: float = 8.0,
    ) -> List[LevelCandidate]:
        """
        Returns levels directionalmente válidos, ordenados por:
        1. Frescura (confirmed > unconfirmed)
        2. Proximidade do preço (na direcção correcta)
        
        LevelCandidate:
            box_id: str
            level: float              # liq_top para SHORT, liq_bot para LONG
            source: "m5_confirmed" | "m5_unconfirmed" | "m30_confirmed" | ...
            age_min: float
            distance_to_price: float  # sempre positivo; direction-aware
            is_valid_direction: bool  # level acima de price para SHORT, abaixo para LONG
        """
```

Justificar cada campo.

#### 4.2 Nova lógica _near_level
- Assinatura proposta
- Pseudo-code da implementação
- Condição `_near_level == PASS`: existe candidate com `is_valid_direction=True` AND `distance_to_price <= 8.0`
- Condição `_near_level == NEAR`: existe candidate próximo mas fora da banda (pode esperar)
- Condição `_near_level == FAR`: nenhum candidate válido direccionalmente → reject

#### 4.3 Lógica _trending_v1 ajustada
- Como usar os candidates ranqueados
- Manter lógica de HTF consistency
- Preservar logic de score_v1

#### 4.4 Re-avaliação dinâmica (momento crítico)
- Quando um novo M5 box é detectado após signal foi emitido mas antes de execution
- Opções:
  - A) Cancela pending signal se box fresco lado-correcto surge (preferível)
  - B) Atualiza level mas mantém signal
  - C) Não faz nada (status quo)
- Recomendação com justificação

### 5. CASOS DE TESTE

Mínimo 8 casos de teste a especificar (não implementar, só listar):

1. Fresh confirmed + SHORT + level above price + within band → PASS
2. Fresh confirmed + SHORT + level below price → REJECT (wrong direction)
3. Stale confirmed + fallback unconfirmed correct side → PASS (use fallback)
4. Stale confirmed + fallback unconfirmed wrong side → REJECT
5. Multiple unconfirmed boxes (some correct, some wrong) → PASS with closest correct
6. Level exactly at price (distance=0) → PASS or edge case policy
7. Level just passed price (distance=-0.1 in direction terms) → REJECT
8. No valid levels at all → FAR

### 6. MIGRATION PLAN

#### 6.1 Backwards compatibility
- Deprecated fields em `LevelDetector`: o que manter para outros consumers?
- Shadow rollout: novo código corre em paralelo comparando com antigo antes de switch?

#### 6.2 Rollout phases
1. Phase 1 — Implementação com feature flag OFF (novo código existe, não é usado)
2. Phase 2 — Shadow mode (log diffs entre velho e novo para N dias)
3. Phase 3 — Enable em dry-run mode
4. Phase 4 — Enable em live

Timing estimado para cada.

#### 6.3 Rollback strategy
Como reverter se Phase 4 mostra regressão?

### 7. VALIDATION PLAN

#### 7.1 Backtest validation
- Replay decision_log.jsonl (5.5 dias, 8423 decisões) com nova logic
- Counterfactual: quantas das 531 GO signals pós-Apr 17 teriam sido:
  - Rejeitadas (level errado)
  - Emitidas com level diferente
  - Idênticas
- Métrica: % decisões afectadas

#### 7.2 Live empirical validation
- Após Phase 3 (dry-run), observar 48h
- Métrica: #rejections vs #passes por direction

### 8. RISCOS IDENTIFICADOS

Listar riscos técnicos:
- Redução aggressive de signals (menos trades, mas melhores)
- Edge cases não cobertos nos 8 testes
- Performance: level_detector agora retorna lista em vez de scalar
- Concorrência: múltiplos consumers do level_detector
- Mudança semântica quebra callers que assumem scalar

Mitigações para cada.

### 9. OPEN QUESTIONS

Perguntas a Barbara + Claude antes de implementação:

1. Threshold `max_distance_pts=8.0` mantém-se? FASE II mostrou que é razoável mas pode precisar ajuste direction-aware.
2. Quando `_near_level == FAR` (zona já passou), devemos:
   - a) Reject silenciosamente
   - b) Log and emit metric
   - c) Trigger "wait for retrace" mode
3. Multiple M5 + M30 levels concorrentes — priorização?
4. _IMPORTANCE de "stale" threshold (15min) — é ajustável data-driven?

### 10. REFERÊNCIAS

- `feedback_critical_analysis_ats` memory entry (BUEC/entry-side inversion)
- `event_processor.py:2401` (V1 gate)
- `event_processor.py:_trending_v1`
- `level_detector.py` (paths relevantes)
- Signal 03:14:46 2026-04-20 decision_log entry
- FASE II windows work (context)

---

## REGRA CRÍTICA — ZERO CODE CHANGES

- Documento puramente de design
- Podes ler código para extrair state actual, assinar tipos, mostrar line numbers
- Não modificar nenhum ficheiro `.py`, `.yaml`, `.json`, config
- Não criar stubs/placeholders em código
- Apenas o design doc markdown é output

---

## COMUNICAÇÃO FINAL

```
DESIGN DOC COMPLETE
Path: C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\DESIGN_DOC_near_level_direction_aware.md
Sections: 1-10 complete
Open questions: [N]

Aguardo Claude audit + Barbara review antes de arrancar implementação.
```

---

## PROIBIDO

- ❌ Qualquer edit a código/config
- ❌ Restart serviço
- ❌ Tocar capture processes (12332, 8248, 2512)
- ❌ Começar implementação (só design)
- ❌ Skipar qualquer secção 1-10
- ❌ Propor "quick fix" — estamos em design mode formal

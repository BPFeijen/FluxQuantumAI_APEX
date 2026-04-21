# TASK: SPRINT C — DESIGN DOC `derive_m30_bias` Literatura-Aligned

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Sprint:** entry_logic_fix_20260420 (Track C)
**Mode:** READ-ONLY + DESIGN DOC writing. **Zero código editado.**
**Input:** `BIAS_STUCK_INVESTIGATION.md` (já entregue)
**Output:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\DESIGN_DOC_m30_bias_literatura_aligned.md`

---

## CONTEXTO

Sprint C investigação confirmou 2 bugs críticos:

**Bug #1:** `derive_m30_bias` (level_detector.py:241) usa `liq_top > box_high` estrito. 44.7% do parquet tem `liq_top == box_high` (estado normal pre-fakeout). Zero rows com `liq_top < box_high`. Função retorna `bearish` por default em metade dos casos.

**Bug #2:** `event_processor.py:2391-2403` hard-bloqueia LONGs baseado só em `m30_bias_confirmed`, ignora `provisional_m30_bias`. Quando confirmed stuck bearish (Bug #1), LONGs bloqueadas mesmo com provisional bullish.

**Stuck evidence:** 91 horas contínuas bearish desde 16 Abril. 78% de todas as decisões = bearish.

**Fix trivial `> → >=` é over-correction:** se `liq_top == box_high` é estado normal (consolidação), trocar para `>=` retorna bullish **sempre**, mesmo sem breakout real.

**Este design doc define a solução semântica correcta**, não um fix de operador.

---

## REGRA CRÍTICA — DESIGN DOC ONLY, ZERO CÓDIGO EDITADO

- Produzir **apenas** documento markdown
- Zero edits em `.py`
- Zero restarts
- Zero tocar capture processes (12332, 8248, 2512)
- Se precisas ler código para entender, READ-ONLY
- Cita literatura com source + capítulo/secção
- Cita memórias/transcripts com referência exacta
- Se não consegues decidir, apresentar opções — Barbara decide

---

## ESTRUTURA DO DESIGN DOC (9 secções obrigatórias)

### Secção 1 — Problem Statement

Reproduzir smoking gun do Sprint C investigação + impacto observado:
- 91h stuck bearish, 78% bearish distribution
- Bug #1 + Bug #2 + interacção
- Exemplo concreto: rally +7pts MT5 hoje com bias stuck bearish

**Objectivo:** leitor de 3 meses no futuro entende exactamente o problema.

---

### Secção 2 — Literatura — Semântica correcta de "bias"

Consultar Wyckoff + ICT + ATS. Definir **quando** o bias é bullish/bearish per literatura.

**Fontes obrigatórias (já em project knowledge):**
- `Wyckoff-Methodology-in-Depth-Ruben-Villahermosa.pdf`
- `Wyckoff_2_0_Structures__Volume_Prof.pdf`
- `ATS_Implementation_Strategic_Plan_A_Framework_for_Systematic_Profitability.txt`
- `Everything_to_Know_About_the_ATS_Trend_Line.txt`
- `An_Introduction_to_the_ATS_Trading.txt`
- ICT handbooks disponíveis

**Conceitos-chave a mapear:**

**Wyckoff:**
- Quando é bias bullish? (Phase D após SOS? JAC? BUEC?)
- Quando é bias bearish? (Phase D distribuição após SOW? MSOW?)
- Estado "indefinido" (Phase B construção de cause) = neutral?
- Citar capítulo + quote

**ICT:**
- Bullish order flow: quando? (BOS up + structure respecting discount PD arrays)
- Bearish order flow: quando? (BOS down + structure respecting premium PD arrays)
- Neutral: consolidação sem BOS?
- Citar handbook + secção

**ATS:**
- Bullish: ATS trend line pointing up + price above expansion line
- Bearish: ATS trend line pointing down + price below expansion line
- Consolidation (contraction phase): neutral
- Citar Strategic Plan + Trend Line doc

**Tabela de consenso:**

| Literatura | Bullish | Bearish | Neutral |
|---|---|---|---|
| Wyckoff | ... | ... | ... |
| ICT | ... | ... | ... |
| ATS | ... | ... | ... |
| **Consenso** | ... | ... | ... |

**Output:** definição literatura-aligned de bias, em português, 3-5 linhas por estado.

---

### Secção 3 — Dados — O que o parquet realmente representa

Investigar:

```python
import pandas as pd

m30 = pd.read_parquet("C:\\data\\processed\\gc_m30_boxes.parquet")

# Quando é que liq_top > box_high vs liq_top == box_high?
print(f"Total rows: {len(m30)}")
print(f"liq_top > box_high: {(m30.m30_liq_top > m30.m30_box_high).sum()}")
print(f"liq_top == box_high: {(m30.m30_liq_top == m30.m30_box_high).sum()}")
print(f"liq_top < box_high: {(m30.m30_liq_top < m30.m30_box_high).sum()}")

# Amostrar casos onde liq_top > box_high (bullish per Bug #1 fix)
bullish_rows = m30[m30.m30_liq_top > m30.m30_box_high].tail(10)
print("Rows where liq_top > box_high:")
print(bullish_rows[["m30_box_id", "m30_box_confirmed", "m30_liq_top", "m30_box_high", 
                    "breakout_dir"]].to_string())

# O que muda quando há fakeout UP? É aí que liq_top > box_high?
fakeouts = m30[m30.m30_box_confirmed == False]
print(f"\nUnconfirmed boxes: {len(fakeouts)}")
# ...

# Localizar código que escreve parquet — onde liq_top é determinado?
# Select-String para m30_updater.py ou box_writer
```

**Reportar no design doc:**
- Distribuição real: liq_top vs box_high
- **Semântica concreta:** `liq_top = box_high` significa o quê no pipeline? (pre-fakeout? consolidação? algo mais?)
- `liq_top > box_high` ocorre quando? (post-fakeout UP?)
- `liq_top < box_high` ocorre? (resposta: não, zero rows — porquê?)

**Objectivo:** entender o que o writer M30 está a comunicar através destes campos.

---

### Secção 4 — Proposta Arquitectural

Apresentar **3 opções** com trade-offs. Não recomendar ainda — deixar Barbara escolher.

**Opção A — Fix minimal semântico**
- `> → >=` em level_detector.py:241
- Problema: over-correction, sistema fica bullish em consolidação (liq_top==box_high)
- Esperado: swing de 78% bearish para X% bullish (estimar com counterfactual)

**Opção B — Nova função `derive_m30_bias_v2` literatura-aligned**

Definir regras explícitas:
```python
def derive_m30_bias_v2(
    box_confirmed: bool,
    breakout_dir: Literal["UP", "DN", None],
    close: float,
    box_high: float,
    box_low: float,
    liq_top: float,
    liq_bot: float,
    # outras features necessárias
) -> Literal["bullish", "bearish", "neutral"]:
    """Literatura-aligned:
    - Bullish: JAC confirmed UP (close > box_high, sustentado) OR Spring detected
    - Bearish: JAC confirmed DN (close < box_low, sustentado) OR UTAD detected
    - Neutral: Phase B/consolidation (preço dentro do box, sem breakout confirmed)
    """
    ...
```

Listar todas as regras (bullish triggers, bearish triggers, neutral conditions) explicitamente. Cada regra com referência literatura.

**Opção C — Refactor completo bias + hard-block architecture**

Redesign Bug #1 + Bug #2 juntos:
- `derive_m30_bias` literatura-aligned (como Opção B)
- `event_processor.py:2391-2403` hard-block usa combinação provisional + confirmed + score
- Aceita LONG quando provisional=bullish + confirmação secundária (price > box_high OR iceberg BID OR etc)

**Tabela comparativa obrigatória:**

| Aspecto | A (operator) | B (função v2) | C (refactor completo) |
|---|---|---|---|
| Scope | 1 linha | ~50 linhas | ~150 linhas |
| Risco regressão | ALTO (over-correction) | MÉDIO | MÉDIO |
| Literatura-alignment | Parcial | FULL | FULL |
| Resolve Bug #1 | Sim | Sim | Sim |
| Resolve Bug #2 | Não | Parcial | Sim |
| Tempo implementação | 10min | 2-3h | 4-6h |
| Backtest scope | Simples | Moderado | Extenso |
| Rollback | Trivial | Trivial | Moderado |

---

### Secção 5 — Proposta de regras literatura-aligned

Para Opção B ou C, definir regras completas. Listar **cada condição** com:
- Nome
- Quando dispara
- Referência literatura (livro + quote)
- Pseudocódigo

**Exemplo formato:**

**R1 — Bullish via JAC confirmed UP**
- Condição: `box_confirmed == True AND breakout_dir == "UP" AND close > box_high`
- Referência Wyckoff: "JAC (Jump Across the Creek) = SOS that reaches top of range, starting uptrend" (Villahermosa, Phase D)
- Referência ATS: "Uptrend confirmed when ATS Trend Line is UP + price above expansion line"
- Pseudocódigo: `return "bullish"`

**R2 — Bullish via Spring**
- Condição: `breakout_dir == "DN" AND box_confirmed == False AND close returned above box_low`
- Referência Wyckoff: "Spring Type 1/2/3 — bearish penetration followed by strong demand returning price above support" (Villahermosa, Phase C)
- Pseudocódigo: `return "bullish"`

**Listar todas R1..Rn** para bullish, bearish, neutral.

---

### Secção 6 — Backtest Plan Counterfactual

Como validar a nova lógica antes de deploy:

```python
"""
Backtest plan:
1. Replay decision_log.jsonl com nova lógica derive_m30_bias_v2
2. Computar novo bias por cada decisão histórica
3. Classificar outcomes:
   - IDENTICAL: mesmo bias
   - FLIPPED_BEARISH_TO_BULLISH: oportunidade LONG que teria sido aceite
   - FLIPPED_BULLISH_TO_BEARISH: oportunidade SHORT que teria sido aceite
   - STAYED_STUCK: ainda stuck (bug diferente)
4. Métricas:
   - % flipped
   - Duração média stuck (antes vs depois)
   - Decisões bearish/bullish/neutral em cada periodo
5. Caso específico: rally 2026-04-20 02:00-03:30
   - Bias actual: bearish (stuck)
   - Bias v2: [recalcular]
   - Oportunidades LONG que se tornam visíveis
"""
```

**Reportar plan detalhado no design doc + amostra dos resultados esperados.**

---

### Secção 7 — Risk Analysis

**False Positives (nova lógica diz bullish quando não devia):**
- Cenário: liq_top > box_high por ruído de dados, não fakeout real
- Mitigação: exigir `box_confirmed` como gate
- Probabilidade: [estimar]

**False Negatives (nova lógica diz neutral quando devia bullish):**
- Cenário: bullish real mas condições R1..Rn demasiado apertadas
- Mitigação: listar cenários possíveis
- Probabilidade: [estimar]

**Regime changes:**
- Nova lógica comporta-se bem em trending vs ranging?
- Historical data cobre ambos?

**Impacto em trade count:**
- Actual: 78% bearish → esperado (pessimista): 45% bearish
- Se nova lógica fica 20% bearish, sistema muda comportamento radicalmente
- Mitigação: rollback threshold, shadow mode

---

### Secção 8 — Rollout Proposal

**Fases sugeridas:**

1. **Design doc approval** (Barbara + Claude review)
2. **Implementação** (ClaudeCode)
3. **Unit tests** (8+ cenários cobrindo R1..Rn)
4. **Backtest counterfactual** (validar hipótese)
5. **Shadow mode?** (log em paralelo sem alterar comportamento — opcional)
6. **Deploy conjunto Sprint A + Sprint C** (janela apropriada, Barbara autoriza)
7. **Observability window** (24h monitoring)

**Rollback plan específico:**
- Trigger: >X% trade count change em direcção inesperada OU Y% de false positives detectados
- Acção: restore backup + restart

---

### Secção 9 — Open Questions

Listar o que **NÃO** consegues decidir sozinho:

1. Opção A/B/C — qual preferes? (recomendação técnica + decisão Barbara)
2. Neutral state — deve existir como valor separado? Ou `unknown`? Ou degrade-to-bearish?
3. Confirmação secundária para Bug #2 fix — que sinal? (price > box_high? iceberg? L2 delta?)
4. Shadow mode vs direct deploy — aceita-se risco?
5. Thresholds dentro das regras — hardcoded ou config?

---

## DELIVERABLE FORMAT

Arquivo **único** em `$sprint_dir\DESIGN_DOC_m30_bias_literatura_aligned.md` com as 9 secções.

**Comprimento esperado:** 400-700 linhas. Não menos, não muito mais.

**Tonalidade:** técnica, rigorosa, com referências literatura explícitas. Nada de "acho que" sem evidência.

**Formato citações literatura:**
```
> "[quote literal da literatura]"
> — Villahermosa, Wyckoff Methodology in Depth, Phase C, p. X
```

---

## COMUNICAÇÃO FINAL

Quando design doc estiver pronto:

```
SPRINT C DESIGN DOC PRONTO

Literatura consultada: [lista fontes]
Opções apresentadas: A (operador), B (função v2), C (refactor completo)
Recomendação técnica: [A/B/C] — [rationale curto]
Regras literatura-aligned: R1..Rn (N regras, X bullish + Y bearish + Z neutral)
Backtest plan: definido, amostra de resultados esperados incluída
Risk analysis: [resumo]

Open questions para Barbara: [N questões]

Doc: DESIGN_DOC_m30_bias_literatura_aligned.md

Aguardo Barbara + Claude review antes de qualquer implementação.
```

---

## PROIBIDO

- ❌ Editar código Python
- ❌ Restart serviço
- ❌ Tocar capture processes
- ❌ Implementar qualquer fix "pequeno" durante investigação
- ❌ Decidir Opção A/B/C unilateralmente — apresentar, deixar Barbara decidir
- ❌ Citar literatura sem source específica
- ❌ Propor regras sem referência literatura
- ❌ Skipar qualquer das 9 secções
- ❌ Documento com menos de 400 linhas (insuficiente para design completo)
- ❌ "Fica para sprint futuro" em open questions críticas — apresentar opções concretas

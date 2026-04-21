# TASK: SPRINT C — DESIGN DOC `derive_m30_bias` Literatura-Aligned + H4 Gate

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Sprint:** entry_logic_fix_20260420 (Track C)
**Mode:** READ-ONLY + DESIGN DOC writing. **Zero código editado.**
**Input:** `BIAS_STUCK_INVESTIGATION.md` (já entregue)
**Output:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\DESIGN_DOC_m30_bias_literatura_aligned.md`
**Supersedes:** `CLAUDECODE_Sprint_C_Design_Doc.md` (v1) — adiciona H4 alignment mandatory

---

## CONTEXTO EXPANDIDO

Sprint C investigação confirmou 2 bugs:

**Bug #1:** `derive_m30_bias` (level_detector.py:241) — equality bug `> vs >=`. 44.7% do parquet com `liq_top == box_high` → bearish por default.

**Bug #2:** `event_processor.py:2391-2403` hard-bloqueia LONGs só com `m30_bias_confirmed`, ignora `provisional_m30_bias`.

**Bug #3 (NOVO — descoberto via chart inspection hoje 12:33 UTC):** Sistema **não consulta H4 candle structure** para validar direcção. Apenas usa `delta_4h` (microstructure agregada, janela stale) e `H4_liquidity_lines` (swing highs/lows, não candles).

**Evidência live 2026-04-20:**
- Charts mostram **3 últimas H4 candles bullish** (recuperação do dip 4780)
- Sistema emitiu 69 GOs SHORT / 0 LONG em 8h
- Violação directa da regra ATS fundamental: **"The four hour is the authority over the smaller time frames. No exceptions."**

---

## REGRA FUNDAMENTAL — LITERATURA ATS

> "The four hour is the authority over the smaller time frames. Simple concept. If the four hour is entering the trend phase to the downside, that means that every time frame lower than a four hour chart is going to — all the big moves are gonna be on the downside."
> — ATS Trade System, project knowledge

> "You will only initiate trades in the direction of the confirmed trend on your higher supporting timeframe. **No exceptions.**"
> — ATS Implementation Strategic Plan

**Aplicado ao APEX:**
- **H4 bullish → LONG only no M30**
- **H4 bearish → SHORT only no M30**
- **H4 neutral → restrição de entries ou wait**

**Sistema actual:** não existe gate H4 candle-based. Só `delta_4h` (que tem janela stale bug) + `H4_liquidity_lines` (não é direction indicator).

---

## REGRA CRÍTICA — DESIGN DOC ONLY, ZERO CÓDIGO EDITADO

- Produzir **apenas** documento markdown
- Zero edits em `.py`
- Zero restarts
- Zero tocar capture processes (12332, 8248, 2512)
- Se precisas ler código para entender, READ-ONLY
- Cita literatura com source + capítulo/secção
- Se não consegues decidir, apresentar opções — Barbara decide

---

## ESTRUTURA DO DESIGN DOC — 10 SECÇÕES OBRIGATÓRIAS

### Secção 1 — Problem Statement

Reproduzir:
- Bug #1 (equality)
- Bug #2 (provisional ignored)
- Bug #3 (H4 not consulted)
- **Exemplo concreto live:** 2026-04-20 12:33 UTC — 3 H4 candles bullish visíveis no chart, sistema emite 69 SHORTs / 0 LONG em 8h
- 91h stuck bearish, 78% distribution

**Objectivo:** leitor entende exactamente o problema.

---

### Secção 2 — Literatura Unificada: H4 e M30

Consultar project knowledge (fontes obrigatórias):
- `ATS_Implementation_Strategic_Plan_A_Framework_for_Systematic_Profitability.txt`
- `ATS Trade System.txt`
- `Everything_to_Know_About_the_ATS_Trend_Line.txt`
- `An_Introduction_to_the_ATS_Trading.txt`
- `Wyckoff-Methodology-in-Depth-Ruben-Villahermosa.pdf`
- `Wyckoff_2_0_Structures__Volume_Prof.pdf`
- `708323229ICTHandbook.pdf`
- `652432368-Ict-Institutional-Smc-Trading.pdf`

**Tabela de consenso (H4 role):**

| Metodologia | H4 role | Quando bullish? | Quando bearish? | Neutral? |
|---|---|---|---|---|
| ATS | Directional authority absoluta | ATS Trend Line up + price settling above expansion | ATS Trend Line down + price settling below expansion | Price indeciso no range |
| Wyckoff | HTF structural bias | Phase D markup post-SOS | Phase D markdown post-SOW | Phase B construction |
| ICT | Parent order flow | Bullish CiSD + BOS up + respecting discount PDs | Bearish CiSD + BOS down + respecting premium PDs | No clear CiSD, consolidation |

**Output obrigatório:** definição unificada em português, 5-8 linhas por estado (bullish/bearish/neutral).

**Regras derivadas (literatura-aligned):**

R_H4_1: **H4 bias bullish quando:**
- Última H4 candle close > open (corpo verde) **E**
- Close dentro dos últimos 20% da range H4 (strong close) **OU**
- Pelo menos 2 das 3 últimas candles H4 são verdes com higher highs ou higher lows

R_H4_2: **H4 bias bearish quando:** (espelho R_H4_1)

R_H4_3: **H4 bias neutral quando:** nenhuma das regras acima claramente satisfeita

(Regras ilustrativas — ClaudeCode pode refinar com literatura)

---

### Secção 3 — Literatura: semântica correcta de `m30_bias`

Per literatura:

**M30 bullish bias válido SE:**
- H4 bullish (mandatory — ATS "no exceptions")
- **E** M30 apresenta estrutura bullish (JAC up, Spring, BUEC, etc.)

**M30 bearish bias válido SE:**
- H4 bearish (mandatory)
- **E** M30 apresenta estrutura bearish (JAC down, UTAD, LPSY, etc.)

**M30 neutral:**
- H4 neutral (qualquer bias M30 é suspeito)
- **OU** M30 em Phase B consolidation (dentro do box sem breakout)

**Regra ouro:** `m30_bias` **nunca** pode contradizer H4 dominante.

Listar regras R_M30_1 .. R_M30_n explícitas, cada uma com referência literatura.

---

### Secção 4 — Dados: o que parquets realmente contêm

Investigar (READ-ONLY):

```python
import pandas as pd

# M30 parquet
m30 = pd.read_parquet("C:\\data\\processed\\gc_m30_boxes.parquet")
print(f"M30 columns: {m30.columns.tolist()}")
# Distribuição liq_top vs box_high
print(f"liq_top > box_high: {(m30.m30_liq_top > m30.m30_box_high).sum()}")
print(f"liq_top == box_high: {(m30.m30_liq_top == m30.m30_box_high).sum()}")
print(f"liq_top < box_high: {(m30.m30_liq_top < m30.m30_box_high).sum()}")

# H4 — existe parquet H4?
import os
processed = "C:\\data\\processed"
h4_files = [f for f in os.listdir(processed) if "h4" in f.lower() or "4h" in f.lower()]
print(f"H4 parquets: {h4_files}")
# Se existe, inspeccionar colunas
# Se não existe, flag como GAP

# OHLCV base — permite reconstruir H4?
ohlc = pd.read_parquet("C:\\data\\processed\\gc_ohlcv_l2_joined.parquet")
# Resample para H4
h4_reconstructed = ohlc[["open","high","low","close"]].resample("4H").agg({
    "open":"first","high":"max","low":"min","close":"last"
})
print("H4 reconstructed from ohlcv — last 10 bars:")
print(h4_reconstructed.tail(10).to_string())
```

**Reportar:**
- M30 columns actuais
- Existe parquet H4 dedicado? Se não, sistema não tem H4 candles disponíveis
- É possível reconstruir H4 de OHLCV? (deve ser)
- H4 candles últimas 10 barras — confirmam bias bullish hoje?

---

### Secção 5 — Proposta Arquitectural — 3 Opções

**Opção A — Fix minimal**
- Bug #1: `> → >=` em level_detector.py:241
- Bug #2: aceitar provisional quando difere de confirmed
- Bug #3: **não resolvido**
- Risco: over-correction, H4 ignorado continua
- **Rejeitada:** ignora regra ATS fundamental

**Opção B — derive_m30_bias_v2 com H4 gate**
- Nova função `derive_m30_bias_v2` com regras R_M30_* literatura-aligned
- **H4 alignment check mandatory** como primeira condição
- H4 derivado de OHLCV resample (se parquet H4 não existe)
- Bug #2 parcialmente resolvido (provisional considerado quando H4 aligned)

**Opção C — Refactor arquitectural completo (recomendada)**
- Tudo de Opção B
- **Novo gate `H4 direction gate`** em `ats_live_gate.py` (adicionar V0 antes de V1?)
- H4 check aplicado antes mesmo de M30 structure
- Arquitectura: H4 → M30 → L2 → iceberg (ordem literatura)
- Bug #1 + #2 + #3 totalmente resolvidos

**Tabela comparativa obrigatória:**

| Aspecto | A (minimal) | B (v2 + H4 check) | C (refactor H4 gate) |
|---|---|---|---|
| Scope | 10 linhas | ~80 linhas | ~200 linhas |
| Risco regressão | ALTO | MÉDIO | MÉDIO |
| Literatura-alignment | Nenhum | FULL | FULL + arquitectural |
| Resolve Bug #1 | Sim | Sim | Sim |
| Resolve Bug #2 | Não | Parcial | Sim |
| Resolve Bug #3 | **Não** | **Sim** | **Sim** |
| H4 candles acessíveis | N/A | Resample on-demand | Dedicated provider |
| Tempo implementação | 15min | 3-4h | 6-8h |
| Backtest scope | Simples | Moderado | Extenso |
| Rollback | Trivial | Trivial | Moderado |

**Recomendação ClaudeCode:** apresentar a Barbara. Não decidir sozinho.

---

### Secção 6 — H4 Gate Specification

Para Opção B ou C, especificar:

**Input:**
- OHLCV stream (reconstruir H4 via resample, ou parquet H4 dedicado)
- Current timestamp

**Output:**
- `h4_bias`: "bullish" | "bearish" | "neutral"
- `h4_confidence`: float [0..1]
- `h4_last_3_candles`: list de dict com open/close/high/low

**Regras:**

```python
def derive_h4_bias(h4_candles: list[dict]) -> tuple[str, float]:
    """
    Per ATS literature: H4 is the authority.
    
    Bullish bias requires at least one of:
    - Last candle: green body + close in upper 30% of range
    - Last 3 candles: 2+ green + higher highs/lows
    - ATS trend line on H4 pointing up (if available)
    
    Bearish bias requires mirror conditions.
    
    Neutral: everything else.
    """
    ...
```

Listar todas as condições + refs literatura + pseudocódigo.

**Edge cases a tratar:**
- H4 em formação (current candle incomplete) — usa últimas N completas?
- Consolidação lateral (H1/M30 range apertado apesar de H4 clear bias) — bias mantém-se mas entries mais selectivas?
- H4 flip (transição bullish → bearish) — período de buffer antes de aceitar?

---

### Secção 7 — Integração com event_processor

**Como o h4_bias é usado:**

```python
# Em _resolve_direction ou novo _h4_gate_check:
def _check_h4_alignment(self, intended_direction: str) -> tuple[bool, str]:
    """
    Returns (aligned, reason).
    aligned=False → block trade.
    """
    h4_bias, h4_conf = self.h4_detector.derive_h4_bias(self.h4_recent_candles)
    
    if h4_bias == "neutral":
        # Permite com warning OU rejeita? Barbara decide.
        return (True, f"h4_neutral_allow") or (False, f"h4_neutral_block")
    
    if intended_direction == "LONG" and h4_bias == "bullish":
        return (True, "h4_aligned_bullish")
    if intended_direction == "SHORT" and h4_bias == "bearish":
        return (True, "h4_aligned_bearish")
    
    # Counter-H4 blocked per ATS "no exceptions"
    return (False, f"h4_counter_{h4_bias}_rejects_{intended_direction}")
```

**Onde inserir:**
- Antes de `_trending_v1` check?
- Dentro de `_resolve_direction` como first gate?
- Novo V0 em `ats_live_gate.py`?

Apresentar opções + recomendar.

---

### Secção 8 — Backtest Plan Counterfactual

**Replay decision_log.jsonl com nova lógica:**

```python
"""
Backtest plan:
1. Reconstruir H4 candles de ohlcv parquet cobrindo periodo decision_log
2. Para cada decisão histórica, recomputar:
   - h4_bias (regras R_H4_*)
   - m30_bias_v2 (regras R_M30_* com H4 gate)
3. Classificar outcomes:
   - IDENTICAL: mesma direcção aprovada
   - BLOCKED_BY_H4: trade teria sido bloqueado (counter-H4)
   - PREVIOUSLY_BLOCKED_NOW_OK: oportunidade desbloqueada
   - STAYED_STUCK: bug adicional
4. Métricas específicas:
   - % de SHORTs emitidos hoje que teriam sido bloqueados (H4 bullish)
   - % de LONGs perdidos hoje que teriam sido aceites
   - Caso específico rally 2026-04-20 02:00-03:30: quantos LONGs emergem?
   - Caso overextension reversal (Sprint A findings): ainda funciona em H4 neutral?
"""
```

**Reportar amostra dos resultados esperados no design doc.**

---

### Secção 9 — Risk Analysis

**False Positives (H4 gate bloqueia trade que seria bom):**
- Overextension reversal em H4 bullish forte pode ser legítimo SHORT (mean reversion extreme)
- Mitigação: excepção para setups com strong L2 + iceberg confirmation?
- Probabilidade: [estimar via backtest]

**False Negatives (H4 gate deixa passar trade mau):**
- H4 recém-flipped mas ainda sem confirmação
- Mitigação: exigir H4 bias confirmed por 2+ candles consecutive

**Trade count impact:**
- Actual: 69 SHORT / 0 LONG em 8h
- Esperado com H4 gate: se H4 bullish, 0 SHORT / N LONG (N depende de M30 structure)
- **Mudança radical de comportamento** — muito maior impacto que Sprint A

**Regime changes:**
- H4 choppy (flip constante) → sistema fica quase silencioso (bom ou mau?)
- H4 trending forte → sistema alinha correctamente
- Mix → comportamento seguro per literatura

---

### Secção 10 — Rollout Proposal

**Fases sugeridas:**

1. **Design doc approval** (Barbara + Claude review)
2. **Implementação incremental** (ClaudeCode):
   - Fase 2a: H4 detector (dedicado, testável isolado)
   - Fase 2b: Integração com `derive_m30_bias_v2`
   - Fase 2c: Integração com event_processor / gate
3. **Unit tests** (cobrir R_H4_*, R_M30_*, edge cases)
4. **Backtest counterfactual** (replay decision_log)
5. **Shadow mode?** (log em paralelo sem alterar comportamento) — **recomendado dado o impacto radical**
6. **Deploy conjunto Sprint A + Sprint C** (janela apropriada, Barbara autoriza manualmente)
7. **Observability window** (48h monitoring — H4 impacto requer mais tempo)

**Rollback plan específico:**
- Trigger: H4 gate bloqueou >90% dos trades em 24h (sistema quase silencioso quando não devia)
- Trigger: LONG count >200% do esperado (over-permissive)
- Acção: restore backup + restart nssm manual

---

### Secção 11 — Open Questions

Listar o que **NÃO** consegues decidir sozinho:

1. **Opção A / B / C** — qual? (recomendação técnica + Barbara decide)
2. **H4 candles source** — resample de OHLCV existente OU criar parquet H4 dedicado?
3. **H4 neutral handling** — block trade OR allow com redução de size?
4. **Current H4 candle** — considerar (incompleto) ou só últimas completas?
5. **Exception para overextension reversal** — mantém ou remove (literatura diz remove)?
6. **Shadow mode obrigatório** antes de deploy, dado impacto radical?
7. **Confirmação secundária para Bug #2** — price > box_high? iceberg? L2 delta?

---

## DELIVERABLE FORMAT

Arquivo **único** em `$sprint_dir\DESIGN_DOC_m30_bias_literatura_aligned.md` com as 11 secções.

**Comprimento esperado:** 600-900 linhas. Design doc mais completo que v1 porque H4 gate adiciona complexidade.

**Tonalidade:** técnica, rigorosa, referências literatura explícitas.

**Formato citações:**
```
> "[quote literal]"
> — ATS Trade System, project knowledge file: ATS_Trade_System.txt
```

---

## COMUNICAÇÃO FINAL

Quando design doc estiver pronto:

```
SPRINT C DESIGN DOC v2 PRONTO (inclui H4 gate mandatory)

Literatura consultada: [lista]
Bugs abordados: #1 equality + #2 provisional + #3 H4 ignored
Opções apresentadas: A (minimal — rejeitada), B (v2 + H4 check), C (refactor completo)
Recomendação técnica: [B/C] — [rationale]

H4 bias detector: R_H4_1..R_H4_n regras definidas
M30 bias v2: R_M30_1..R_M30_n regras definidas com H4 gate mandatory
Backtest plan: incluído (expected impact em SHORTs hoje + LONGs perdidos)
Risk analysis: trade count impact radical, shadow mode recomendado

Open questions Barbara: [lista]

Doc: DESIGN_DOC_m30_bias_literatura_aligned.md

Aguardo Barbara + Claude review antes de qualquer implementação.
```

---

## PROIBIDO

- ❌ Editar código Python
- ❌ Restart serviço
- ❌ Tocar capture processes
- ❌ Decidir Opção A/B/C unilateralmente
- ❌ Citar literatura sem source específica
- ❌ Propor regras sem referência literatura
- ❌ Skipar qualquer das 11 secções
- ❌ Documento com menos de 600 linhas
- ❌ Esquecer H4 em qualquer regra (H4 é authority per ATS)
- ❌ Propor excepções à regra H4 sem forte justificação literatura

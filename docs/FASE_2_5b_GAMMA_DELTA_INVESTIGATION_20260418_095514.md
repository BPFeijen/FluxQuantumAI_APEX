# FASE 2.5b — GAMMA/DELTA Investigation (3 sources)

**Timestamp:** 2026-04-18 09:55:14 (local)
**Mode:** READ-ONLY
**Sources:** Code + Logs + ATS Docs
**Purpose:** Entender propósito, uso real e intent de GAMMA/DELTA.

---

## 1. DOCUMENTATION EVIDENCE (design intent)

### ATS Docs path (confirmed)

```
C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\
```

### GAMMA in documentation

| Doc file | Line | Excerpt |
|---|---|---|
| `FUNC_M30_Framework_20260401.md` | 195 | `### 7.3 GAMMA — Momentum Stacking` |
| `FUNC_M30_Framework_20260401.md` | 66 | `Entry triggers: ALPHA (liq touch), BETA (BUEC), GAMMA (stacking)` |
| `SYSTEM_Architecture_Current_20260409.md` | 222 | `\| GAMMA \| momentum stacking M30 \| entrada em stacking de expansão \|` |
| `TECH_M30_Framework_20260401.md` | 138 | `\| m30_momentum_stacking \| bool \| New expansion beyond previous \| For GAMMA trigger \|` |
| `ADR-001_M30_Execution_Timeframe.md` | 171 | `Entry triggers: ALPHA (liq touch), BETA (BUEC), GAMMA (stacking)` |
| `APEX_Dashboard_Command_v2.md` | 106 | `momentum_stacking   GAMMA` |
| `FIT_GAP_Analysis_GitHub_vs_APEX_GOLD_20260409.md` | 47 | `ALPHA (liq touch), GAMMA (stacking), DELTA (re-alignment)` |

### GAMMA — extended context (FUNC_M30_Framework §7.3)

```
 195: ### 7.3 GAMMA — Momentum Stacking
 196:
 197: | # | Condition                                                                     | Source       |
 198: |---|-------------------------------------------------------------------------------|--------------|
 199: | 1 | New M30 expansion line above (LONG) / below (SHORT) previous                  | M30 framework|
 200: | 2 | daily_trend aligned                                                           | D1 bias      |
 201: | 3 | weekly_aligned == True                                                        | Weekly filter|
 202: | 4 | Risk window: reasonable distance between expansion lines                      | M30 framework|
 203: | 5 | Reset: price touches previous expansion line → cancel                         | M30 framework|
```

**Design intent (GAMMA):** trigger secundário que dispara quando surge **nova expansion line M30** além da anterior, **alinhada com daily_trend** e com filtros weekly/risk. Entrada de continuação em trending market (stacking de expansão).

### DELTA in documentation

| Doc file | Line | Excerpt |
|---|---|---|
| `SYSTEM_Architecture_Current_20260409.md` | 223 | `\| DELTA \| trend re-alignment M30 \| reentrada após pullback no trend \|` |
| `APEX_GC_Entry_Gates_Technical_Doc.md` | 9 | `Todo o sinal de entrada (ALPHA / GAMMA / DELTA) tem de passar por 4 gates sequenciais` |
| `FIT_GAP_Analysis_GitHub_vs_APEX_GOLD_20260409.md` | 47 | `ALPHA (liq touch), GAMMA (stacking), DELTA (re-alignment)` |
| `FluxQuantumAI_API_Contracts.md` | 77/207 | API tipo suportando `"ALPHA" \| "GAMMA" \| "DELTA" \| "NONE"` |

**Design intent (DELTA):** re-entry após pullback em tendência confirmada. Code docstring explicita: "Entry: M30 bar crosses back through last expansion line in D1 direction; SL = prior swing against trend (swing high/low from TrendMomentumDetector); TP1 = next expansion line; TP2 = opposite liquidity; Lock 4 M30 bars (2h) após CONFIRMED order."

**Nota:** **FUNC_M30_Framework §7.4** é referenciada no comentário do código (linha 3641) mas **não existe** no ficheiro actual (só há §7.3). Doc DELTA sub-documentado vs GAMMA.

---

## 2. CODE EVIDENCE (what they actually do)

### GAMMA function

- **File:** `live\event_processor.py`
- **Detector:** `check_gamma_trigger` @ linha **3328** (called @ linha 3493)
- **Executor:** `_trigger_gate_gamma(self, trigger: dict)` @ linha **3527**
- **Docstring (3527-3535):**
  ```
  Execute GAMMA entry — same 3-leg structure (0.02+0.02+0.01) as ALPHA/BETA.
  SHIELD activates after TP1 hit, identical to existing mechanism.
  Only TP/SL levels differ from ALPHA/BETA per ATS docs:
    SL  = prior expansion line
    TP1 = entry ± ATR14   (proxy for next expansion line)
    TP2 = m30_liq_top (LONG) / m30_liq_bot (SHORT)
  ```
- **Parâmetros aceitos no `trigger` dict:** `direction`, `entry`, `sl`, `tp1`, `tp2`
- **Constants:** `GAMMA_MIN_RR = 1.5`, `GAMMA_TP1_FACTOR = 1.0`
- **Callsites:** apenas 1 chamada real (linha 3493) dentro de `_run_event_driven` após tick loop.

### DELTA function

- **File:** `live\event_processor.py`
- **Detector:** `check_delta_trigger` @ linha **3642** (called @ linha 3789)
- **Executor:** `_trigger_gate_delta(self, trigger: dict)` @ linha **3824**
- **Docstring (3824-3832):**
  ```
  Execute DELTA entry — same 3-leg structure (0.02+0.02+0.01) as GAMMA.
  SL  = prior swing against trend (from TrendMomentumDetector)
  TP1 = next expansion line (detector) or ATR proxy
  TP2 = liq_bot (SHORT) / liq_top (LONG)
  SHIELD activates after TP1 hit, runner trailing.
  Direction lock: DELTA_DIRECTION_LOCK_S (4 M30 bars = 2h).
  ```
- **Constants:** `DELTA_MIN_RR = 1.5`, `DELTA_DIRECTION_LOCK_S = 4*30*60` (2h)
- **Depends on:** `self._trend_momentum_detector` (TrendMomentumDetector instance)
- **Callsites:** 1 chamada real (linha 3789).

### Code structure — success/failure asymmetry

Ambas as funções GAMMA/DELTA têm **apenas** success path:
```python
if _gamma_exec.get("success_any", False):
    # success handling
# NO else — falha silenciosa (gap pre-existente)
```
ALPHA em contraste tem failure path completo com `action="EXEC_FAILED"`, log.error, notify_execution() (linhas 2575-2591).

---

## 3. HISTORICAL EVIDENCE (actual firing)

### decision_log.jsonl — trigger.type counts (case-sensitive, exact pattern)

Total lines: **7,643** (cobre ~4 dias: 2026-04-14 01:00 → 2026-04-17 21:05)

| Strategy | Count | % of total |
|---|---|---|
| ALPHA    | **7,563** | 99.0% |
| GAMMA    | **0**     | 0.0%  |
| DELTA    | **0**     | 0.0%  |
| QT_MICRO | 80        | 1.0%  |

**Nota:** a investigação inicial mostrou DELTA=7643 (match de todas as linhas) porque `-SimpleMatch` default é case-insensitive e apanhava `delta_4h` como substring. Após re-contagem com `"type": "DELTA"` literal + `-CaseSensitive`, **GAMMA=0 e DELTA=0** confirmados.

### decision_log.jsonl — label counts (execution context)

| Label | Count |
|---|---|
| ALPHA | 0 |
| GAMMA | 0 |
| DELTA | 0 |

(Label só escrito no decision quando `_open_on_all_accounts` executa; não foi confirmada presença em decision_log.)

### continuation_trades.jsonl

| Strategy | Count |
|---|---|
| ALPHA | 0 |
| GAMMA | 0 |
| DELTA | 0 |

### service_stdout.log (palavra inteira, case-sensitive)

| Pattern | Count |
|---|---|
| `\bGAMMA\b` | 0 |
| `\bDELTA\b` | 0 |

### trades.csv

Ficheiro não encontrado no path padrão.

---

## SYNTHESIS — cross-referencing 3 sources

### Does code match documentation intent?

- **GAMMA:** YES. Código implementa detector (`check_gamma_trigger`) + executor (`_trigger_gate_gamma`) com lógica de momentum stacking, TP/SL conforme docs, 3-leg structure igual ao ALPHA.
- **DELTA:** PARTIAL. Código implementa detector + executor. Docs de alto nível confirmam intent (re-alignment), mas FUNC_M30_Framework §7.4 referenciada pelo código não existe — DELTA está sub-documentado comparativamente a GAMMA.

### Does actual usage match design intent?

- **GAMMA:** Designed para disparar em stacking de expansão M30 alinhado com D1. **Histórico: 0 disparos em ~4 dias de logs (7643 decisions)**. Interpretação: ou condições demasiado estritas, ou detector não está a ser satisfeito no fluxo actual de mercado, ou período de logs não cobre um contexto onde GAMMA seria relevante.
- **DELTA:** Designed para disparar em pullback + re-alignment pós-tendência. **Histórico: 0 disparos**. Mesma interpretação possível.

### Status

| Aspect | GAMMA | DELTA |
|---|---|---|
| Has documentation | YES (formal, §7.3 + tabela arquitectura) | PARTIAL (tabela arquitectura + docstring; §7.4 em falta) |
| Has implementation | YES (detector + executor + constants) | YES (detector + executor + TrendMomentumDetector) |
| Fires in production | **NO** (0/7643) | **NO** (0/7643) |
| Consistent with docs | YES | YES (code aligns with docstring) |
| Dead code / GHOST | **GHOST (code presente, execução zero)** | **GHOST (code presente, execução zero)** |

---

## RECOMMENDATION

Based on 3-source evidence:

**Scenario A — Both GAMMA and DELTA are DEAD (count=0 in logs)** ✅

→ **Opção 1: Aceitar silêncio.** Não investir em failure path para código que nunca executa em produção.

### Rationale

1. **99% das decisions vêm de ALPHA.** GAMMA+DELTA juntos = 0% em ~4 dias de logs + total runtime significativo.
2. **Gap de observabilidade é irrelevante** se a condição `if _exec.get("success_any")` **nunca é sequer alcançada**. Não há silêncio a reportar porque o bloco não é atingido.
3. **Efficient use of time:** O tempo de Fase 2.6 ficaria melhor aplicado em monitorizar **porque** GAMMA/DELTA não disparam (é uma questão de design/calibração/detector, não de observability).

### Concern residual — Scenario C sub-plot

Os detectores `check_gamma_trigger` e `check_delta_trigger` **existem** e são **chamados** a cada tick. Se **nunca retornam trigger válido**, há 3 hipóteses a investigar (fora desta fase):

1. **Condições demasiado estritas** — thresholds (e.g., GAMMA_MIN_RR=1.5, weekly_aligned, daily_trend aligned) raramente alinhados.
2. **Bug silencioso no detector** — poderia nunca satisfazer check, mesmo em condições válidas.
3. **Condições raras e logs curtos** — 4 dias pode não cobrir janela suficiente para ver stacking/pullback em D1 trending.

**Se Barbara quiser** — uma Fase futura poderia adicionar telemetria no detector (log de condições avaliadas) para distinguir entre estes 3 cenários. Sem telemetria, GAMMA/DELTA continuam a ser ghost branches.

---

## Status

✅ Investigation complete.
✅ Nenhuma modificação feita.
✅ 3 fontes contrastadas (docs + code + logs).

**Verdict:** GAMMA e DELTA são **ghost strategies** — bem documentadas/implementadas mas **zero disparos históricos**. Failure path fix NÃO é prioridade.

---

## Comunicação final

```
FASE 2.5b INVESTIGATION — COMPLETE
Docs path: C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs (confirmed)
Report: C:\FluxQuantumAI\FASE_2_5b_GAMMA_DELTA_INVESTIGATION_20260418_095514.md

GAMMA: docs=YES (FUNC_M30 §7.3), code_name=check_gamma_trigger/_trigger_gate_gamma, log_count=0
DELTA: docs=PARTIAL (arch table; §7.4 missing), code_name=check_delta_trigger/_trigger_gate_delta, log_count=0

Recommendation: Scenario A → Opção 1 (aceitar silêncio; failure path fix não vale a pena)

Concern colateral: detectores existem mas nunca retornam válido em 7,643 decisions.
 Pode valer a pena — em fase futura — instrumentar os detectores com
 telemetria para distinguir "condições raras" vs "bug silencioso".

Aguardando decisão Barbara.
```

# CLAUDE AUDIT — Sprint entry_logic_fix_20260420

**Auditor:** Claude (ML/AI Engineer)
**Data:** 2026-04-20
**Escopo auditado:** TASK_CLOSEOUT_REPORT.md, DESIGN_DOC_near_level_direction_aware.md, BACKTEST_COUNTERFACTUAL.md, ARCHITECTURAL_AUDIT_READ_ONLY.md, MISSED_LONG_INVESTIGATION.md, backtest_counterfactual.py, 5× scripts _audit_*.py
**Código audit:** não verifiquei diffs linha-a-linha do level_detector.py/event_processor.py modificados — o repo público clonado tem versão pre-fix de 15/Abr (588/4287 linhas). Audit baseia-se em consistência entre design doc, report, testes e backtest.

---

## 1. VEREDITO SUMÁRIO

**Status: 🟡 YELLOW — aprovado condicionalmente para restart com ressalvas.**

O fix está bem desenhado, bem testado, e ataca um bug real com elevado impacto (22% das GOs emitidas eram estruturalmente inválidas). O design doc é rigoroso. Os testes (19/19) incluem os 8 casos literatura-derivados do §5 mais 8 invariantes do Apêndice A — cobertura adequada. O backtest counterfactual confirma o caso 03:14 e escalável.

**Mas há 4 ressalvas que exigem decisão tua antes do restart.** Listo-as na secção 3, ordenadas por criticidade.

---

## 2. O QUE ESTÁ BEM FEITO

### 2.1 Identificação correcta do problema
O design doc (§3.1) reconstrói o signal 03:14:46 com precisão — `price_mt5=4791.08`, `liq_top_mt5=4767.53`, 23.55 pts no lado errado. A tabela de 4 cenários (§3.2) distingue bem os casos #2 (fresh mas violado) e #4 (stale + fallback wrong-side) — ambos estruturalmente idênticos no defeito mas diferentes no mecanismo.

### 2.2 Decisão C1 vs outras alternativas
O ClaudeCode escolheu **C1 — universal direction-aware post-validation, sem excepção OVEREXTENDED**. É a escolha correcta. Razões (§1 do TASK_CLOSEOUT):

- Overextension reversal não tem suporte literário (Sprint B v2 confirmou — "NOT found in any cited source", "NO match in Wyckoff / ICT / ATS in-repo docs")
- Threshold 1.5×ATR é marcado explicitamente "manual" no `docs/fluxquantum_implementation.md`
- **72 GO signals OVEREXT em 5 dias → 0 fills** (todos EXEC_FAILED). Filtrar custa zero em termos de trades perdidos.

Fazer excepção para OVEREXT seria preservar um mecanismo com smell arquitectural forte.

### 2.3 `_resolve_direction` preservado como single source of truth
Constraint #10 do Sprint A honrada. M30 continua a comandar execução. O fix é **post-validation** — o sistema ainda deriva direcção da lógica ATS existente, o novo filtro apenas suprime a emissão quando o level referenciado não faz sentido direccional. Zero cirurgia na filosofia da estratégia.

### 2.4 Backwards compat preservada
- `get_current_levels()` intocado
- `_near_level(price)` sem direction continua idêntico via `_near_level_legacy`
- `_near_level_source` mantém vocabulário actual (`"m5_only"`, `"m5+m30"`, etc.)
- Consumers fora de event_processor (dashboard, run_live) não afectados

### 2.5 Testes sérios
19 testes, não 8. Os 11 do §5 cobrem os cenários literatura; os 8 invariantes do Apêndice A apanham regressões (determinismo, ordering, source priority > distance, distance=0 edge, direction inválida raises). Notável: `test_invariant_source_priority_beats_distance` — força a escolha literatura-aligned (m5_confirmed 5pts wins vs m5_unconfirmed 1pt), **exactamente o ponto que a Open Question 9.3 deixou em aberto para ti decidires**. Ver ressalva 3.2 abaixo.

### 2.6 Read-only discipline mantida
Capture processes 12332/8248/2512 não tocados (verificado pelos Sprint B agents via `Get-CimInstance`). ClaudeCode respeitou o protocolo e **não restartou** — espera autorização tua via nssm. Zero commits git sem aprovação.

### 2.7 Sprint B v2 em paralelo
Report valioso que expôs 5 issues arquitecturais alinhados por severidade. Enriquece o contexto do fix principal — dá visão à parte do sistema inteiro, não só do bug imediato.

---

## 3. RESSALVAS (ordenadas por criticidade)

### 3.1 🔴 CRÍTICA — Escopo do fix não cobre o verdadeiro bug do dia

Esta é a mais importante e **tu precisas de decidir antes do restart**.

O Sprint B v2 identificou que o bug que fez o sistema **perder o LONG 01:00-01:50 UTC** (o impulso +42pts que realmente aconteceu hoje) **não é o bug que este fix resolve**. São bugs distintos:

| Bug | Sprint resolve? | Impacto |
|---|---|---|
| 03:14 SHORT emitido com liq_top abaixo do preço | **SIM** — C1 post-validation bloqueia isto | +42/659 LONG + 103/659 SHORT rejects (22%) |
| Missed LONG 01:00-01:50 | **NÃO** | 0 GO LONG, 238 M30_BIAS_BLOCK |

O missed LONG tem duas causas root (Sprint B v2 Part 1):

**(a) `derive_m30_bias` equality bug** (level_detector.py:239): `if liq_top > box_high` é strict. Hoje `m30_liq_top=4859.10 == m30_box_high=4859.10`. Cai para check bearish → bias=bearish(confirmed) → M30_BIAS_BLOCK **238 vezes** contra toda entrada LONG. Confirmei no código clonado — bug existe literalmente na linha 239.

**(b) `self.liq_bot_gc` stale em 4779.20 durante 2.5h** — M5 parquet actualizou para 4824.30 → 4818.30 → 4812.55, mas nunca propagou. Zero eventos `NEAR liq_bot_mt5` em 150min.

**Consequência:** Mesmo depois do restart com o fix aplicado, se segunda-feira tiveres outra oportunidade LONG em condições similares (equality M30 + liq_bot stale), o sistema **continua cego**. O fix torna o sistema mais honesto nos SHORTs que emite, mas não corrige a cegueira nos LONGs.

O TASK_CLOSEOUT lista estes dois bugs no §8 Open Items #4 e #2, o que é correcto — **mas agora estão na fila atrás do fix actual**. Significa que na próxima sessão de mercado, as rejeições aumentam (bom) **mas o sample de LONGs pode continuar a ser próximo de zero** (mau).

**Pergunta para ti:** aceitas esta prioridade (SHORT-bug primeiro, LONG-bug depois) sabendo que a consequência empírica nos próximos 2-3 dias pode ser "menos trades, quase todos SHORT"? Se sim, avança. Se não, pode fazer sentido reter a promoção deste sprint até ter tempo de juntar o equality bug + staleness fix num único deploy.

### 3.2 🟡 IMPORTANTE — Open Question 9.3 foi decidida sem ACK da Barbara

No design doc §9.3 o ClaudeCode escreveu:

> **Múltiplos M5 + M30 levels concorrentes — priorização?**
> A ordem proposta é `is_valid_direction > source > age > distance`. Alternativa: `is_valid_direction > distance > source > age` (privilegia proximidade sobre frescura). **Decisão para Barbara.**

O fix foi implementado com a primeira ordem (source antes de distance) e o teste `test_invariant_source_priority_beats_distance` força isso. A escolha **é defensável** (frescura e confirmação são literatura-aligned; distance é tiebreaker), e alinha com ICT/Wyckoff que privilegiam estrutura sobre proximidade — mas tecnicamente o ClaudeCode devia ter esperado o teu ACK.

**Decisão:** ratifica agora a ordem `is_valid_direction > source > age > distance`, ou pede alteração. Na prática, em MNQ/XAUUSD intraday a diferença entre as duas ordens é pequena (1-3 candidates tipicamente competem) — o impacto empírico esperado é baixo. Eu recomendo ratificar.

### 3.3 🟡 IMPORTANTE — Rejection rate 22% vs 5-15% estimado

O design doc §7.1 estimou "5-15% GO signals rejeitadas (target)". O backtest observou **22% (145/659)**. O report marca isto como "caveat" (§4 caveats do TASK_CLOSEOUT): "More signals analysed → more bug instances revealed. Consistent with catching a real production pattern."

Concordo com essa leitura, mas adiciono contexto: 22% **em 5.5 dias** é um número elevado. Significa que aproximadamente 1 em cada 5 sinais que o sistema emitia estava estruturalmente errado. Isto não é ruído — é padrão sistémico. A magnitude sinaliza que o ambiente recente (Jan-Abr 2026, regime Trump repricing como vimos na FASE II) tem criado muitos casos onde o fallback stale-confirmed + unconfirmed wrong-side dispara.

**Duas implicações para ti:**

1. **Trade count vai cair pelo menos 22% post-restart** (pode cair mais porque os signals IDENTICAL_APPROX podem ter sub-casos NEAR que o approximate replay não apanha — o próprio report avisa).
2. **O design doc §6.3 lista "Trade count cai > 60% vs baseline" como rollback trigger.** 22% está longe disto; mas não podemos excluir que o impacto real seja maior. Precisas de ter o threshold de rollback claro na cabeça antes de autorizar.

### 3.4 🟢 MENOR — Backtest counterfactual é aproximação, não replay completo

O `backtest_counterfactual.py` (ler o docstring do módulo) documenta honestamente a limitação: usa `trigger.level_price_mt5` vs `price_mt5` registados, não reconstrói estado M5/M30 box per-tick. O próprio código chama isto de "approximation methodology". Isto significa:

- **NEW_REJECT_WRONG_SIDE é sólido** — se o fired level estava do lado errado, o C1 post-validation bloqueava. Zero falsos positivos esperados.
- **IDENTICAL_APPROX é optimista** — podem existir sub-casos onde o *top candidate* (vindo de `get_levels_for_direction`) se desloca para fora da banda, devolvendo NEAR em vez de PASS. Estes sairiam também, mas o backtest não os detecta.

Portanto o "22%" é **limite inferior**, não estimativa central. Pode ser mais.

**Mitigação adequada já no report:** o ClaudeCode listou "Databento extended backtest — full parquet replay (not approximation) over 126 days Jul-Nov 2025" como Open Item #1 para Sprint futuro. Para a decisão de restart hoje, o backtest actual é suficiente. Mas **o rollback trigger "trade count cai > 60%" pode precisar de ser mais conservador** (ex. 40%) precisamente por causa desta incerteza.

---

## 4. CONFORMIDADE COM FRAMEWORK v1.1

O Framework data-driven v1.1 foi aprovado hoje mesmo. Este sprint foi planeado e executado **antes** do framework estar formalizado, portanto não é fair cobrar conformidade retroactiva. Mas faz sentido mapear onde bate e onde não bate, para calibrar expectativas futuras.

| Framework v1.1 | Este sprint cumpre? |
|---|---|
| Princípio 1 — análise de correlação/VIF antes de recalibrar | **N/A** — sprint não é de recalibração de threshold, é fix de bug lógico |
| Princípio 2 — walk-forward obrigatório | **Não** — backtest é aproximação sobre 5.5 dias. Databento 126d listado como follow-up |
| Princípio 3 — triple-barrier labeling | **N/A** — sprint não produz ML model, é fix estrutural |
| Princípio 4 — thresholds em múltiplos de ATR | **Sim** — band usa `NEAR_ATR_FACTOR * atr_m30` (floor 5.0pts) |
| Princípio 5 — regime stability check | **Parcial** — os 5.5 dias são todos mesmo regime |
| **Princípio 6 — recalibração nunca pára produção** | **Parcial** — fix é aplicado directamente a Camada 1 (live) após restart, sem passar por Camadas 2/3 do framework |
| Part C.0 — declarar camada do sprint | **Não** — design doc foi escrito antes de o framework existir |
| Part C.5 — promoção via shadow/paper antes de live | **Não** — restart direto propõe fix live sem shadow prévio |

**Nota honesta:** se este fix chegasse hoje sob o framework v1.1, o workflow correcto seria: sprint de recalibração em Camada 2, depois Camada 3a shadow logger durante ≥ 1 semana com ≥ 1 HIGH event, depois promoção a Camada 1. O ClaudeCode está a propor saltar directamente para Camada 1.

**Porque aceito esta excepção:** (a) é bug de segurança lógica, não recalibração de threshold, (b) o caso 03:14 é empiricamente prejudicial e continua activo, (c) a infraestrutura de shadow/paper ainda não existe, (d) testes + backtest aproximado dão confiança suficiente para exceção pontual. Mas **registo formalmente como exceção**, não como norma.

Para sprints futuros (incluindo os Open Items #4 derive_m30_bias equality, #6 overext recalibrate/remove, #5 PATCH2A integration rewrite) — aplicar o framework integralmente.

---

## 5. CHECKLIST FINAL ANTES DE AUTORIZAR RESTART

Para a Barbara, antes de correr `nssm restart FluxQuantumAPEX`:

- [ ] Lido este audit até ao fim
- [ ] Decisão 3.1 tomada: ou aceitas que próximos dias podem ser "menos trades, maioritariamente SHORT", ou adias restart até juntar equality bug fix
- [ ] Decisão 3.2 tomada: ratificas ordem `is_valid_direction > source > age > distance` ou pedes alteração
- [ ] Rollback trigger definido: considera baixar "trade count cai > 60%" para > 40% por causa do ponto 3.4
- [ ] Janela de mercado: **restart deve ser em mercado aberto**, não ao domingo à noite — precisa haver tick flow para observação ser válida
- [ ] Backup `backup_pre_fix_20260420_101330/` confirmado intacto
- [ ] MT5 execution: sabes que o fix não resolve o bug MT5 (separado, em curso)
- [ ] Primeiras 24h pós-restart: dedicas tempo a monitorizar `decision_log` e `stderr` activamente

Se alguma destas linhas não estiver clara, responde aqui antes de escreveres ao ClaudeCode.

---

## 6. RESPOSTA SUGERIDA AO CLAUDECODE

Se aceitas o audit e queres avançar, podes responder algo como:

> Audit de Claude lido e aceite. Decisões:
>
> 1. Ordem de priorização `is_valid_direction > source > age > distance` **ratificada**.
> 2. Aceito que o fix resolve 03:14-class (SHORTs wrong-side) mas NÃO resolve o missed LONG equality bug. Open Items #4 (derive_m30_bias) e #2 (liq_bot_gc staleness) passam a Sprint seguinte, prioridade P0.
> 3. Rollback trigger ajustado: trade count cai > 40% (não 60%) por incerteza do backtest aproximado. Outros triggers mantidos.
> 4. Restart será feito por mim em **janela de mercado aberto** (não agora domingo). Provável: segunda-feira cedo antes da sessão London.
> 5. Commit git **bloqueado** até eu ver pelo menos 24h de comportamento pós-restart.
>
> Aguardas a minha autorização explícita via mensagem para correr o `nssm restart`. Até lá, zero acção.

---

## 7. OBSERVAÇÃO FINAL HONESTA

Este sprint é bom trabalho. O bug 03:14 era real, a investigação foi séria, a solução é alinhada com literatura e os testes não são perfunctórios. O ClaudeCode aplicou o protocolo com rigor.

**Aliás — isto é exactamente o tipo de trabalho que o Framework v1.1 quer institucionalizar.** A pena é que foi produzido antes do framework e portanto salta camadas. A lição para daqui para a frente: o próximo sprint arquitectural (seja derive_m30_bias, seja PATCH2A rewrite) **tem de** passar por Camada 2 isolada + shadow logger antes de chegar a Camada 1.

Não é cerimónia por cerimónia. É que tu viste o que aconteceu este fim-de-semana com 3 deploys consecutivos ao economic_calendar.py — mesmo quando cada um individualmente parecia OK, a acumulação criou risco. Para thresholds arquitecturais de gate (que é o que este sprint toca), o shadow logger é a rede de segurança que permite fazer este tipo de cirurgia **sem medo**.

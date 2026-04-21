# Framework de Calibração Data-Driven para FluxQuantumAI APEX

**Autor:** Claude (ML/AI Engineer)
**Para:** Barbara (PO) + ClaudeCode (executor)
**Data:** 2026-04-19
**Versão:** v1.1 (acrescentada arquitectura paralela de 3 camadas)

---

## 0. Propósito e escopo

Este documento estabelece **como** a recalibração dos thresholds do APEX live deve ser feita, daqui para a frente, para que seja defensável, reprodutível, resistente a overfitting **e sem interromper produção**.

Tem quatro componentes:

1. **Parte A — Framework metodológico.** A teoria, agrupada por princípio, cruzando o material Purdue (ML fundamentos) com literatura trading-specific (Lopez de Prado, Pardo, microstructure).
2. **Parte B — Arquitectura de 3 camadas.** A regra de engenharia que garante que a recalibração corre em paralelo ao live sem nunca o interromper.
3. **Parte C — Checklist pré-recalibração.** Antes de tocar em qualquer threshold live, o que tem de estar verificado.
4. **Parte D — Template de design doc.** Para cada sprint de recalibração, o documento mínimo que tem de ser produzido e aprovado.

**O que este documento NÃO é:** não é um plano de sprints com datas. Não é uma proposta de arquitetura ML. É uma norma de trabalho — um filtro que toda a futura recalibração tem de passar.

---

## PARTE A — FRAMEWORK METODOLÓGICO

### A.1 — O gap fundamental entre ML de Purdue e ML aplicado a trading

Os notebooks de Purdue (`Lesson_03` a `Lesson_05` da ML Module, `Lesson_08` e `Lesson_10` de Data Science) cobrem **muito bem** as fundações: train/test split, cross-validation, regularização, métricas de performance, feature engineering, teste de hipóteses. Mas Purdue ensina ML como se os dados fossem **iid** (independentes e identicamente distribuídos) — casas, pacientes, clientes. Em trading, **não são**. O que Purdue ensina tem de ser usado, mas **não é suficiente**, e em algumas partes é ativamente perigoso.

O gap, em três linhas:

| Princípio Purdue | Verdade em trading | Consequência |
|---|---|---|
| K-Fold CV baralha as linhas | As linhas têm ordem temporal e labels sobrepostos | K-Fold vaza o futuro para o passado (look-ahead) — resultados inflacionados |
| Train/test 70/30 aleatório | Regimes de mercado mudam (Trump repricing, macro shifts) | Train numa regime, test noutra → modelo não generaliza |
| Overfitting = métrica de test degradada | Overfitting em trading = Sharpe fantástico que desaparece live | É preciso **walk-forward**, não apenas holdout |
| Feature importance por permutation | Muitas features tradicionais são redundantes e economicamente vazias | É preciso feature importance **com purging** |

**Fonte:** Lopez de Prado, *Advances in Financial Machine Learning*, cap. 7 (CV in Finance) — "K-fold CV vastly over-inflates results because of the lookahead bias."

### A.2 — Os cinco princípios que regem toda a recalibração APEX

#### Princípio 1 — Nenhum threshold se calibra sozinho

**Purdue:** regressão múltipla, multicolinearidade, VIF (`Lesson_08 Advanced Statistics`).

**Aplicado ao APEX:** o inventário mostra 251 parâmetros. Destes, pelo menos 3 grupos têm duplicações diretas (`margin_level_min` ↔ `MARGIN_FLOOR_PCT`, `max_positions` ↔ `MAX_TRADE_GROUPS`, iceberg CAL-1..8 duplicados entre `settings.json` e `ats_iceberg_gate.py`). Muitos outros são correlacionados por construção (ex.: `absorption`, `LOI`, `collision_band` medem a mesma coisa por ângulos diferentes).

**Regra:** antes de recalibrar qualquer parâmetro isoladamente, correr análise de correlação e VIF para identificar grupos correlacionados. Parâmetros correlacionados **têm de ser recalibrados em conjunto ou um deles fixado**, nunca otimizados em isolamento.

**Ação pré-requisito:** Sprint 0 de limpeza — resolver duplicações, decidir fail-opens, triar 251 → ~40-70 parâmetros realmente ativos e independentes.

#### Princípio 2 — Validação temporal, nunca aleatória

**Purdue:** K-Fold CV, Stratified K-Fold (`3.2_Supervised_Learning_Regression`).

**Insuficiência:** K-Fold aleatório é o pecado mortal em trading.

**Trading-specific:**

- **Walk-forward analysis** (Pardo, 1992) — o *gold standard* de validação de estratégias. Otimizar numa janela in-sample, testar na janela out-of-sample seguinte, deslocar, repetir. Métrica-chave: **Walk-Forward Efficiency (WFE)** = (out-of-sample performance) / (in-sample performance). WFE ≥ 50-60% indica robustez; WFE consistentemente baixo = overfitting; WFE errático = fragilidade.
- **Purged K-Fold CV com embargo** (Lopez de Prado, 2018, cap. 7) — para quando os labels se sobrepõem (como no APEX: um trade aberto às 14:00 tem label que depende de price path até 14:35; se a fold de test começa às 14:20, há vazamento). *Purging* remove observações de train cujas labels se sobrepõem ao test. *Embargo* adiciona gap temporal antes da fold de test.
- **Combinatorial Purged CV (CPCV)** (Lopez de Prado, cap. 12) — gera múltiplos backtest paths em vez de um único, reduzindo o risco de selecionar uma parametrização "sortuda".

**Regra aplicável ao APEX:** nenhum threshold é recalibrado com holdout simples ou K-Fold aleatório. **Walk-forward é obrigatório**. Purged K-Fold só quando há labels que se sobrepõem temporalmente (trades com duração > 1 bar).

**Parametrização concreta para MNQ/XAUUSD:**
- Janela in-sample mínima: 3 meses (~90 dias de trading)
- Janela out-of-sample: 1 mês
- Step: 1 mês
- Embargo: 2 × holding period médio do trade (para XAUUSD/MNQ intraday: ~2-4 horas)

#### Princípio 3 — Label correta antes de features corretas

**Purdue:** `Lesson_04 Classification` ensina a prever classes dado um dataset já rotulado.

**Insuficiência:** em trading, **a rotulagem é o problema**. A tua `calibration_dataset_full.parquet` já mostrou isto — 2.19M rows e **zero colunas de label**. Não há nada para prever.

**Trading-specific — métodos de rotulagem (Lopez de Prado, cap. 3):**

- **Fixed-horizon labels** — rotula cada observação pelo retorno a N bars. Problema: ignora path (o preço pode ter tocado o SL antes de chegar ao ponto final). Purdue usa esta abordagem implicitamente. **Evitar em APEX.**
- **Triple barrier method** — define três barreiras por trade: profit-taking horizontal, stop-loss horizontal, vertical (tempo). Rotula pelo primeiro que for tocado. Label = {+1, −1, 0}. Respeita path. **Método padrão para APEX.**
- **Meta-labelling** — modelo primário decide *side* (long/short), modelo secundário decide *size* (bet / no-bet / quanto). Melhora precision/recall. Aplicável a APEX no futuro quando tiveres um primário rule-based (a versão atual) e quiseres um filtro ML por cima.
- **Trend-scanning labels** (Lopez de Prado, *Machine Learning for Asset Managers*) — ajusta regressões a janelas variáveis e seleciona a que maximiza o t-value da tendência. Útil para definir regimes (uptrend / downtrend / no-trend).

**Regra aplicável ao APEX:** toda a dataset de calibração tem de incluir **label triple-barrier**, com PT/SL dinâmicos (função da volatilidade diária, não fixos). Barreiras horizontais em múltiplos de ATR, barreira vertical = holding period máximo da estratégia.

#### Princípio 4 — Barriers, Stops e Profit Taking não são "thresholds como os outros"

**Purdue:** não aborda.

**Trading-specific:** no APEX, `trailing_floor=5.11`, `trailing_ceiling=30.64`, `collision_band=1.60pts`, `TP1=25pts` são escalas físicas. Calibrá-las como se fossem hiperparâmetros livres produz sobreajuste trivial. A ideia de Lopez de Prado é calibrá-las **em função da volatilidade recente do ativo** — isto é, em múltiplos de ATR, BAR_VOL, ou outra medida normalizada.

**Regra:** qualquer threshold expresso em "pontos" tem de ter justificação em múltiplos de volatilidade. Se `collision_band=1.60pts` é sempre 1.6 pontos, está errado — tem de ser por exemplo `0.3 × ATR_m30`. Sprint dedicado: converter tudo o que é absoluto para relativo.

#### Princípio 5 — Regime matters

**Purdue:** não aborda diretamente, mas `Lesson_03` cobre feature engineering onde regime pode entrar como feature.

**Trading-specific:**
- **Hidden Markov Models** para detecção de regime (bull/bear, high-vol/low-vol). Nº de estados tipicamente 2-3; BIC para seleção; evitar >5 estados (overfitting).
- **Structural break tests** (CUSUM, SADF) — detectam mudanças de regime para permitir **retraining adaptativo**.
- **Regime-specialist models** — treinar um modelo por regime e escolher em runtime. Evita "modelo único que funciona em média e é mau em todos os regimes".

**Aplicável ao APEX:** os findings da FASE II que não replicaram no BACKTEST_WINDOWS são **exatamente isto** — o regime Jan-Abr 2026 (Trump repricing) difere do Jul-Dez 2025. Um modelo de news_gate treinado só em Jul-Dez teria falhado em Mar-Abr.

**Regra:** qualquer recalibração deve (a) testar se o parâmetro é estável across regimes, (b) se não for, expôr o parâmetro como função do regime detectado em runtime.

#### Princípio 6 — Recalibração nunca pára produção

**Purdue:** não aborda. O ML académico assume um modelo "único" que é treinado, avaliado e deployado.

**Verdade operacional:** em APEX live, o sistema **não pode ser interrompido** para experimentar. Cada minuto offline é um minuto de sinais perdidos (quando há capital em jogo). Cada restart do serviço para "testar" uma recalibração é um risco operacional adicional — como se viu neste fim-de-semana, restarts acumulam side-effects (degraded mode, tracebacks, perda de cache, perda de tick flow).

**Regra fundamental:** **recalibração e produção correm em camadas separadas.** O sprint de recalibração nunca toca no serviço live — o que corre, corre. A promoção de um resultado de recalibração a live é um evento *explícito, isolado e scriptado*, não uma consequência do sprint em si.

A arquitectura concreta está detalhada na **Parte B**. O princípio aqui é apenas: **aplicar um threshold novo ao live NÃO é o passo que fecha um sprint de recalibração.** Um sprint de recalibração fecha quando produz *evidência shadow/paper* suficiente. A aplicação ao live é um sprint de *promoção* separado, com critérios separados.

**Consequência prática:** os erros deste fim-de-semana (Apply Windows → Backtest Validation → Rollback Refined, três edits consecutivos ao `economic_calendar.py` em 48h com restarts entre cada) são, no novo framework, **proibidos**. O ciclo correcto teria sido: 1 sprint de recalibração em ambiente research, 1 sprint de shadow logging, 1 sprint de promoção quando shadow validou.

### A.3 — Métricas de avaliação

**Purdue (regressão):** MSE, RMSE, MAE, R² (`3.2_Supervised_Learning_Regression`).
**Purdue (classificação):** accuracy, precision, recall, F1, confusion matrix, ROC-AUC (`4.1_Classification`).

**Todas úteis. Insuficientes.**

**Trading-specific obrigatórias:**

| Métrica | Para quê | Benchmark |
|---|---|---|
| **Sharpe ratio** (anualizado) | Retorno ajustado a risco | ≥ 1.5 para considerar; ≥ 2.0 bom |
| **Profit factor** | Gross wins / gross losses | ≥ 1.3 viável; ≥ 1.7 forte |
| **Max drawdown** | Pior queda peak-to-trough | < 20% (Barbara definiu meta) |
| **Recovery factor** | Retorno total / max drawdown | ≥ 3 desejável |
| **Win rate × avg win/avg loss** | Asymmetry check | WR 45% com avg_win 2× avg_loss > WR 60% com avg_win = avg_loss |
| **DSR (Deflated Sharpe Ratio)** | Sharpe ajustado para number of trials | Lopez de Prado, cap. 14 — corrige para data mining |
| **PSR (Probabilistic Sharpe Ratio)** | Prob. que Sharpe real > benchmark | > 0.95 para validação |

**Regra:** nenhum sprint de recalibração é declarado GREEN apenas com métricas Purdue. DSR e PSR são obrigatórias quando se fizeram múltiplas tentativas (o que é sempre o caso em backtest de thresholds).

### A.4 — Feature engineering e importance

**Purdue (`Lesson_10`):** log transformation, square root, Box-Cox, scaling (min-max, standard), encoding categorical, hashing.

**Útil. Insuficiente.**

**Trading-specific:**

- **Fractional differentiation** (Lopez de Prado, cap. 5) — alternativa à diferença inteira (que destrói memória). Preserva estacionariedade com memória máxima. Aplicável a preço de MNQ/XAUUSD quando usado como feature.
- **Mean Decrease Impurity (MDI), Mean Decrease Accuracy (MDA), SFI (Single Feature Importance)** — três métodos de feature importance, porque o default do sklearn tem bugs documentados (cap. 8 de Lopez de Prado).
- **Features economicamente significativas** — Lopez de Prado desencoraja "atirar 100 indicadores ao modelo". Para APEX, as features têm de mapear a conceitos de microstructure (OFI — order flow imbalance, PIN, VPIN, spread, depth, iceberg absorption).

**Regra:** quando o módulo de ML for introduzido (substituindo rule-based), a seleção de features começa pelas que têm base teórica em microstructure. Features sem interpretação económica são candidatas a remoção mesmo com MDI alto (sinal de overfitting).

---

## PARTE B — ARQUITECTURA DE 3 CAMADAS

Esta é a tradução operacional do Princípio 6. Estabelece **onde** cada trabalho corre, **como** os artefactos fluem de uma camada para a seguinte, e **que critério** promove um resultado para a camada seguinte.

```
 ┌──────────────────────────────────────────────────────────────┐
 │  CAMADA 1 — PRODUCTION                                        │
 │  ─────────────────────                                        │
 │  C:\FluxQuantumAI\live\*.py                                   │
 │  settings.json                                                │
 │  Serviço NSSM FluxQuantumAPEX                                 │
 │  Execução MT5 real (após fix P0)                              │
 │                                                               │
 │  ZERO experiments. Runs as-is. Alterado só via sprint de      │
 │  PROMOÇÃO (ver Parte C.5 e template D.6).                    │
 └──────────────────────────────────────────────────────────────┘
         ▲                                     │
         │ promoção scriptada                  │ logs decisões,
         │ (após shadow/paper OK)              │ trades.csv,
         │                                     │ decision_log.jsonl
         │                                     ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  CAMADA 3 — SHADOW / PAPER                                    │
 │  ──────────────────────                                       │
 │  3a. Shadow logger (dentro do serviço live, só-log)           │
 │  3b. Paper account (instância separada, MT5 demo)             │
 │  3c. A/B em produção (% capital com thresholds novos)         │
 │                                                               │
 │  Valida um candidate configurado pela Camada 2.               │
 │  Requer mercado aberto. Observa ≥ 1 semana (shadow),          │
 │  ≥ 1 mês (paper), ≥ 3 meses (A/B).                            │
 └──────────────────────────────────────────────────────────────┘
         ▲                                     │
         │ candidate pronto                    │ comparação
         │ (thresholds + design doc            │ shadow vs live
         │  aprovado)                          │
         │                                     ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  CAMADA 2 — RESEARCH                                          │
 │  ───────────────────                                          │
 │  C:\FluxQuantumAI_Research\ (cópia read-only do código live  │
 │  + parquets + notebooks)                                      │
 │                                                               │
 │  Backtest, walk-forward, purged CV, feature importance,       │
 │  triple-barrier labeling, hyperparameter tuning, DSR/PSR.     │
 │  Pode correr em VM separada ou local. Pode partir.            │
 │  Lê dados históricos read-only. Zero comunicação com live.    │
 └──────────────────────────────────────────────────────────────┘
```

### B.1 — Camada 1 (Production) — regras

**Função:** gerar sinais e executar trades. Este é o sistema que produz receita.

**Regras invioláveis:**
1. Nenhum sprint de recalibração toca nesta camada. Nenhum. Nunca.
2. Alterações só via **sprint de promoção** — sprint dedicado, com design doc próprio, backup, py_compile, import probe, observação empírica com mercado aberto, rollback scriptado. Checklist Parte C.5.
3. Capture processes (`quantower_level2_api`, `iceberg_receiver`, `watchdog_l2_capture`) **nunca** são tocados.
4. Restarts só quando absolutamente necessário (deploy de promoção aprovada). Nunca para "testar uma ideia".
5. Rollback ready 24/7 — o backup pre-promoção fica retido ≥ 30 dias.

**Localização:** `C:\FluxQuantumAI\` (actual). Tudo o que já está é Camada 1.

### B.2 — Camada 2 (Research) — regras

**Função:** executar o framework metodológico (Parte A) em dados históricos.

**Regras:**
1. **Isolamento total.** Ambiente separado: `C:\FluxQuantumAI_Research\` ou VM dedicada, ou máquina local da Barbara. O que for mais prático. Crítico: **zero acesso de escrita a `C:\FluxQuantumAI\`**.
2. **Código:** cópia read-only do live + módulos de research próprios (backtest engine, purged CV, walk-forward runner, triple-barrier labeler).
3. **Dados:** cópia read-only dos parquets, L2, decision_log histórico, trades.csv. Cópia programada (ex.: diária via rsync/robocopy) — não linkagem directa.
4. **Pode falhar à vontade.** Crashes, tracebacks, reinstalações, tudo OK. Não afecta Camada 1.
5. **Output:** design docs aprovados + relatórios de backtest + "candidate config" proposto (ficheiro JSON com thresholds propostos e evidência).

**O que corre nesta camada:**
- Sprints de recalibração propriamente ditos (Parte A inteira)
- Backtests de 10 meses com walk-forward completo
- Feature importance analysis
- Regime detection / HMM
- Triple-barrier labeling de datasets históricos
- Hyperparameter tuning

**O que NÃO corre nesta camada:**
- Trading real
- Conexão MT5
- Capture processes

### B.3 — Camada 3 (Shadow / Paper) — três sub-modos

**Função:** validar em mercado real (ou simulado) um candidate vindo da Camada 2, **antes** da promoção a Camada 1.

#### B.3a — Shadow logger (menos intrusivo)

**Mecânica:** dentro do serviço live, correr um *segundo gate* em paralelo ao principal. Para cada tick:
- Gate principal (Camada 1, thresholds actuais): decide → log normal → possivelmente executa trade
- Gate shadow (Camada 3, thresholds candidate): decide → log separado → **nunca executa**

**Output:** dois logs paralelos (`decision_log.jsonl` + `decision_log_shadow.jsonl`). Após N semanas, compara offline: quantas decisões divergentes? Em quais o shadow teria sido melhor/pior? Stats comparadas.

**Requisitos de implementação:**
- Módulo `shadow_gate.py` que duplica a lógica do gate principal
- Segundo ficheiro de config com thresholds candidate
- Logger dedicado
- Dashboard que mostra divergências em tempo real

**Tempo de observação:** ≥ 1 semana de mercado aberto, incluindo pelo menos 1 HIGH event (NFP/CPI/FOMC).

**Limitações:** shadow não captura execution slippage, fills parciais, interacções com broker. É útil para validar *decisões do gate*, não performance financeira real.

#### B.3b — Paper account (intermediário)

**Mecânica:** segunda instância do APEX completa a correr numa conta **demo/paper** da RoboForex ou Hantec. Thresholds candidate. Executa trades com dinheiro virtual.

**Output:** `trades_paper.csv`, `decision_log_paper.jsonl`, equity curve paper. Comparação com live real.

**Requisitos de implementação:**
- Conta demo configurada
- Segunda instância do serviço (`FluxQuantumAPEX_Paper`) — pode partilhar capture processes da Camada 1 (read-only subscription a Quantower L2) mas com MT5 apontado a terminal demo
- Isolamento: nenhum conflito de ficheiros/locks com a Camada 1

**Tempo de observação:** ≥ 1 mês.

**Limitações:** demo accounts têm fills irrealistas (sem slippage genuíno, latência inferior à real). Indicador direccional, não PnL exacto.

#### B.3c — A/B em produção (mais intrusivo, último passo antes da promoção full)

**Mecânica:** na conta real, fracção pequena do capital (ex. 10-20%) opera com thresholds candidate. Resto continua com thresholds actuais. Estrutura exigida: dois magic numbers distintos para distinguir trades A vs B. Comparação estatística DSR/PSR.

**Requisitos:** infraestrutura que permite splitting de capital. Pode exigir múltiplas contas (uma "A" actual + uma "B" candidate) ou magic numbers separados na mesma conta.

**Tempo de observação:** ≥ 3 meses antes de promoção full.

**Quando usar vs B.3b:** B.3c só quando B.3a + B.3b já validaram e há confiança suficiente para arriscar capital real (mesmo que pequeno). Pular directo para B.3c é imprudente.

### B.4 — Fluxo de promoção entre camadas

| Transição | Gate (critério de passagem) | Artefacto |
|---|---|---|
| Camada 2 → Camada 3a | Design doc aprovado + backtest com WFE ≥ 50%, DSR > 0.95 | Candidate config JSON + report |
| Camada 3a → Camada 3b | ≥ 1 semana shadow limpo + divergências justificáveis | Shadow comparison report |
| Camada 3b → Camada 3c | ≥ 1 mês paper com PF ≥ target + max DD dentro do limite | Paper performance report |
| Camada 3c → Camada 1 | ≥ 3 meses A/B com variante B ≥ A em DSR/PSR | Promotion design doc (Parte D) |

**Regra:** **qualquer transição pode falhar**. Falha não é mérito negativo — é o sistema a funcionar. Candidates que falham em shadow nunca chegam a paper; candidates que falham em paper nunca chegam a A/B. O objectivo é filtrar cedo.

### B.5 — Infraestrutura necessária (gap actual)

Reconhecer: **nenhuma destas camadas separadas existe hoje como infraestrutura formal**. O que existe hoje é Camada 1 apenas, com research a fazer-se ad-hoc em cima do mesmo código, o que é precisamente o problema.

**Gap a preencher, ordenado por esforço:**

1. **Camada 2 (baixo esforço):** criar `C:\FluxQuantumAI_Research\` como cópia read-only + estabelecer regra que ClaudeCode nunca escreve em `C:\FluxQuantumAI\` durante sprints de recalibração. Pode ser feito numa tarde.

2. **Camada 3a shadow logger (médio esforço):** desenhar módulo `shadow_gate.py`, integrar no serviço live como chamada paralela, adicionar logger. Sprint de 2-3 dias. **Prioritário** — é o que permite validar no mercado real sem risco.

3. **Camada 3b paper (médio esforço):** conta demo + segunda instância NSSM. Sprint de 3-5 dias.

4. **Camada 3c A/B (alto esforço):** requer alocação de capital, múltiplas contas ou magic numbers, dashboard comparativo. Sprint de 1-2 semanas. **Não prioritário até shadow + paper estarem maduros.**

**Recomendação:** priorizar Camada 2 + Camada 3a no próximo mês. Camada 3b assim que MT5 execution estiver estável. Camada 3c só depois de pelo menos 3 sprints terem passado por 3a+3b com sucesso.

---

## PARTE C — CHECKLIST PRÉ-RECALIBRAÇÃO

Antes de iniciar qualquer sprint de recalibração, tudo isto tem de estar verdade. Se uma única linha falhar → **stop**.

### C.0 — Pré-flight: declarar camada (por sprint)

- [ ] Sprint corre em **Camada 2 (Research)** — ambiente isolado, lê dados históricos, não toca live
- [ ] Design doc (Parte D) declara explicitamente em que camada o sprint opera
- [ ] Output esperado do sprint: candidate config para promoção a Camada 3, **não aplicação a Camada 1**
- [ ] Se o sprint é de **promoção** (Camada 3 → Camada 1): checklist aplicável é a C.5 abaixo + evidência de passagem pelas camadas 3a/3b/3c conforme B.4

### C.1 — Housekeeping (uma vez, não por sprint)

- [ ] Inventário de thresholds está limpo: ≤ 70 parâmetros realmente ativos e independentes (partindo dos 251 atuais)
- [ ] Duplicações resolvidas (margin_level_min, max_positions, iceberg CAL-1..8)
- [ ] Fail-opens decididos (5 parâmetros a `null` — activar ou remover)
- [ ] Placeholders sem implementação removidos ou implementados (cascade_N_levels, vol_climax_multiplier, dom_imbalance_threshold)
- [ ] Valores "oddly specific" sem proveniência têm CAL-tag atribuído OU são restauradas para defaults documentados (trailing_floor=5.11, trailing_ceiling=30.64, delta_weakening=0.139486, pullback_max_atr=0.300399, level_dedup_atr=0.020057)
- [ ] Repo git do APEX live tem pelo menos um commit — baseline rastreável existe
- [ ] Fallback defaults em `_load_thresholds()` foram auditados e alinhados com live (`max_positions` fallback=3 vs live=2 corrigido)

### C.2 — Dataset (por sprint de recalibração, Camada 2)

- [ ] Dataset inclui coluna target (triple-barrier label) — não é só features
- [ ] Date range cobre ≥ 3 meses + 1 mês holdout final intocado
- [ ] Gaps de dados documentados (ex.: 37 dias missing em 2026, VDS crashes)
- [ ] Features L2 preenchidas de forma consistente ao longo de todo o período (não "Frankenstein" como o atual `calibration_dataset_full.parquet`)
- [ ] Baseline estatístico conhecido (performance do sistema rule-based atual no mesmo dataset, como benchmark)

### C.3 — Metodologia (por sprint, Camada 2)

- [ ] Design doc aprovado (ver template em Parte D) **antes** de qualquer código ser escrito
- [ ] Método de validação escolhido: walk-forward OU purged K-Fold — justificado
- [ ] Embargo dimensionado em múltiplos de holding period
- [ ] Número de parâmetros sendo otimizados simultaneamente ≤ 3 (para controlar DSR)
- [ ] Para cada parâmetro: hipótese testável definida (H0 / H1) — não "vamos ver o que dá"
- [ ] Effect size mínimo exigido definido (ex.: Cohen's d ≥ 0.3) além de p-value

### C.4 — Execução (por sprint, Camada 2)

- [ ] In-sample e out-of-sample correm em períodos temporalmente distintos
- [ ] Walk-forward efficiency (WFE) calculada e reportada
- [ ] Métricas reportadas: Sharpe, PF, max DD, DSR, PSR
- [ ] Análise de robustez across regimes: resultado dividido em sub-períodos (pré/pós mudança de macro)
- [ ] Código da recalibração é determinístico (seed fixo) e reprodutível
- [ ] Resultados pré-aplicação documentados em `*_REPORT.md` com todos os artefactos (CSVs, parquets)

### C.5 — Promoção a Camada 1 (sprint separado, só após shadow/paper)

Esta checklist só se aplica ao **sprint de promoção**, não ao sprint de recalibração original. O sprint de recalibração fecha quando produz candidate + shadow/paper evidence. A promoção é um sprint novo.

- [ ] Candidate config validado em Camada 3a (shadow) ≥ 1 semana, com pelo menos 1 HIGH event capturado
- [ ] Candidate config validado em Camada 3b (paper) ≥ 1 mês com PF e max DD dentro do target
- [ ] (Opcional) Candidate config validado em Camada 3c (A/B) ≥ 3 meses
- [ ] Comparação estatística shadow/paper vs live: variante B supera A em DSR/PSR
- [ ] Design doc de promoção aprovado (Parte D, adaptado)
- [ ] Backup completo antes do edit (ficheiros + hashes MD5)
- [ ] py_compile OK
- [ ] Import probe OK (módulo carrega, valores novos presentes)
- [ ] **Promoção planeada para janela de mercado aberto** (não fim-de-semana, não antes de HIGH event major). Idealmente: início de semana, mercado calmo, sem news pending próxima.
- [ ] Rollback scriptado e testado em Camada 2 antes da promoção
- [ ] Capture processes confirmados intactos antes, durante e após o restart
- [ ] Primeiras 24h pós-promoção monitorizadas activamente — qualquer degradação → rollback imediato

### C.6 — Post-promoção (após sprint de promoção)

- [ ] TASK_CLOSEOUT_REPORT produzido
- [ ] SYSTEM_STATE_LOG atualizado (hashes pre/post, PIDs, thresholds alterados)
- [ ] Resultados reais (3+ meses live) comparados com o previsto no backtest
- [ ] Se WFE real < 50% do WFE do backtest → sprint considerado FAILED, rollback

---

## PARTE D — TEMPLATE DE DESIGN DOC

Cada sprint de recalibração (Camada 2) ou de promoção (Camada 3 → Camada 1) começa aqui. Sem este documento aprovado, **o ClaudeCode não escreve código**.

```markdown
# SPRINT [NAME] — Recalibração [PARÂMETRO / GRUPO]

**Sprint ID:** [SPRINT_YYYYMMDD_short]
**Tipo:** RECALIBRAÇÃO (Camada 2) / PROMOÇÃO (Camada 3→1)
**Camada de execução:** [Camada 2 / Camada 3a shadow / Camada 3b paper / Camada 3c A/B / Camada 1 promoção final]
**PO:** Barbara
**ML/AI Engineer:** Claude
**Executor:** ClaudeCode
**Status:** DRAFT / APPROVED / IN PROGRESS / DONE / FAILED

---

## 1. Problema e hipótese

### 1.1 Qual threshold(s) vai ser recalibrado?
- Lista exata, com linhas de código, paths e valores atuais
- Referência a `APEX_THRESHOLDS_INVENTORY_20260419.md`

### 1.2 Qual é o problema observado?
- Evidência empírica live OU backtest que motiva a recalibração
- Metric baseline (ex.: atual WR 38%, PF 0.29 em backtest de N dias)

### 1.3 Hipótese testável
- H0 (null): o threshold atual é ótimo, não há ganho significativo na alteração
- H1 (alternative): um threshold na região [X, Y] produz [métrica] ≥ [valor] com p < 0.05 e Cohen's d ≥ 0.3
- Parâmetros sendo otimizados simultaneamente: [lista] — máximo 3

### 1.4 Por que isto não pode ser uma ML (se aplicável)?
- Justificar por que rule-based é adequado ou por que ML é overkill para este caso
- Se for para preparar transição a ML, declarar explicitamente

---

## 2. Dataset

### 2.1 Fonte
- Path exato do parquet/CSV
- Date range
- Número de linhas / observações

### 2.2 Features necessárias
- Lista das colunas requeridas
- Checagem de completude (% NaN por coluna)
- Checagem de L2 coverage (se aplicável)

### 2.3 Target (label)
- Método de rotulagem: triple-barrier / fixed-horizon / trend-scanning
- Barreiras: PT = [X] × ATR, SL = [Y] × ATR, vertical = [Z] bars
- Justificação da escolha de barreiras

### 2.4 Baseline
- Performance do sistema atual (rule-based) no mesmo dataset
- Métrica de referência a bater

### 2.5 Data leakage check
- Features usadas são conhecidas no momento t ou incluem futuro?
- Explicitar para cada feature

---

## 3. Metodologia de validação

### 3.1 Divisão de dados
- Método: walk-forward / purged K-Fold CV / CPCV
- Janelas: in-sample = [X meses], out-of-sample = [Y meses], step = [Z meses]
- Embargo: [N] bars / [N] horas

### 3.2 Métricas
- Primárias: Sharpe, PF, max DD, DSR, PSR
- Secundárias: WR, avg_win/avg_loss, recovery factor
- Trading-specific adicionais (se aplicável)

### 3.3 Testes estatísticos
- Para cada parâmetro testado vs baseline:
  - T-test / Mann-Whitney U
  - Cohen's d
  - Threshold de significância
- Correção para múltiplos testes (Bonferroni / Holm)

### 3.4 Robustez across regimes
- Sub-períodos: [listar]
- Se houver flip de sinal entre sub-períodos → expor parâmetro como função do regime

---

## 4. Execução

### 4.1 Código
- Script de recalibração (path)
- Seed fixo para reprodutibilidade
- Read-only sobre produção

### 4.2 Outputs esperados
- Relatório: `SPRINT_[NAME]_REPORT.md`
- Artefactos: [lista de CSVs / parquets / plots]

### 4.3 Timeline
- Fase 0 (discovery): [N] min
- Fase 1 (recalibração): [N] min
- Fase 2 (validação): [N] min
- Fase 3 (report): [N] min

---

## 5. Critério de aceitação

Sprint considerado **GREEN** se:
- [ ] Métrica primária melhora ≥ X% vs baseline
- [ ] DSR > 0.95
- [ ] WFE ≥ 50%
- [ ] Robustez across regimes validada (sem flip de sinal)
- [ ] Effect size ≥ 0.3 para todos os parâmetros alterados

Sprint considerado **YELLOW** se:
- Critérios parcialmente atingidos mas direção é consistente → proceder com monitorização reforçada

Sprint considerado **RED** se:
- Qualquer critério violado → rollback; sprint fechado sem promoção a próxima camada

---

## 5.5 Promotion path (Parte B arquitectura)

Este sprint produz um candidate para qual camada seguinte?

- [ ] **Se este sprint é em Camada 2 (Research):** output = candidate config JSON + report. Próximo sprint = Camada 3a (shadow).
- [ ] **Se este sprint é em Camada 3a (Shadow):** output = shadow comparison report. Próximo sprint = Camada 3b (paper).
- [ ] **Se este sprint é em Camada 3b (Paper):** output = paper performance report. Próximo sprint = Camada 3c (A/B) ou promoção directa a Camada 1 se evidência for forte.
- [ ] **Se este sprint é em Camada 3c (A/B):** output = A/B comparison report com DSR/PSR. Próximo sprint = promoção a Camada 1.

**Critério de passagem documentado** (referir checklist C.4 Parte C):
- ...

**Sprints já completos nas camadas anteriores (evidência para este):**
- [lista: Sprint X na Camada 2 concluído em data Y, resultado Z]

---

## 6. Promoção / rollback (só aplicável a sprint de promoção a Camada 1)

**Se este é um sprint de recalibração (Camada 2) ou shadow/paper (Camada 3): esta secção N/A.** Recalibração não toca em produção. Esta secção só é preenchida em **sprints de promoção**, onde um candidate validado na Camada 3 é finalmente escrito em Camada 1.

### 6.1 Pré-promoção
- [ ] Evidência de passagem por todas as camadas 3a/3b (opcional 3c) declaradas em secção 5.5
- [ ] Backup completo com hashes
- [ ] py_compile OK em ambiente research
- [ ] Import probe OK
- [ ] **Janela de mercado aberto disponível**. Não promover ao fim-de-semana. Preferir início de semana sem HIGH events pending.
- [ ] Capture processes confirmados intactos

### 6.2 Aplicação
- Exactamente que ficheiros são tocados, em que linhas, que valor antes e depois
- Script determinístico (não "ClaudeCode decide")
- Read-only sobre research; escrita controlada apenas em C:\FluxQuantumAI\

### 6.3 Observação pós-promoção
- Primeiras 24h: monitorização activa
- Primeiras 2 semanas: comparação diária de métricas vs baseline
- Primeiro mês: review semanal

### 6.4 Rollback
- Condições que disparam rollback (ex.: drawdown > X%, divergência > Y% vs shadow esperado)
- Script de rollback (pré-testado em Camada 2)
- Janela de retenção do backup: ≥ 30 dias

---

## 7. Post-deploy

### 7.1 TASK_CLOSEOUT_REPORT
- Hashes pre/post
- Services PIDs
- Observações runtime

### 7.2 Métrica de sucesso a 3 meses
- Comparar real com previsto
- Se WFE real < 50% WFE backtest → sprint marcado FAILED retroativamente, rollback

---

## 8. Aprovações

- [ ] Barbara (PO) — data:
- [ ] Claude (ML/AI Engineer) — data:
- [ ] ClaudeCode (executor) — data de início:
```

---

## REFERÊNCIAS

### Material Purdue (pasta `Purdue University/` no repo)
- `Machine Learning Module/Lesson_03/3.1_Supervised_Learning.ipynb` — categorias, algoritmos base
- `Machine Learning Module/Lesson_03/3.2_Supervised_Learning_Regression_and_Its_Applications.ipynb` — CV, regularização, GridSearch
- `Machine Learning Module/Lesson_04/4.1-4.3_Classification_and_Its_Applications` — métricas, confusion matrix, imbalanced data, SMOTE
- `Machine Learning Module/Lesson_05/5.1_Ensemble_Learning.ipynb` — bagging, boosting, stacking
- `Data Science with Python/Lesson_08/8.01_Advanced_Statistics.ipynb` — hypothesis testing, p-values, Type I/II errors, ANOVA
- `Data Science with Python/Lesson_10/10.01_Feature_Engineering.ipynb` — transformações, scaling, encoding

### Literatura trading-specific
- **Lopez de Prado, M.** (2018). *Advances in Financial Machine Learning*. Wiley.
  - Cap. 3 — Labeling (triple barrier, meta-labelling)
  - Cap. 5 — Fractionally Differentiated Features
  - Cap. 7 — Cross-Validation in Finance (Purged K-Fold, embargo)
  - Cap. 8 — Feature Importance (MDI, MDA, SFI)
  - Cap. 9 — Hyper-Parameter Tuning with CV
  - Cap. 12 — Combinatorial Purged CV
  - Cap. 14 — Backtest Statistics (DSR, PSR)
- **Pardo, R.** (2008). *The Evaluation and Optimization of Trading Strategies*. Wiley. — Walk-forward analysis.
- **Kyle, A. (1985)** — seminal order flow model.
- **Glosten & Milgrom (1985)** — bid-ask spread e adverse selection.
- Papers recentes: purged CV synthetic environment tests (ScienceDirect 2024), HMM regime detection for adaptive strategies (QuantInsti 2025), OFI/VPIN features para HFT.

### Documentos APEX internos
- `APEX_THRESHOLDS_INVENTORY_20260419.md` — 251 parâmetros
- `APEX_METHODOLOGY_GROUND_TRUTH.md` — metodologia base
- `CURRENT_LIVE_CODE_AUDIT.md` — audit live
- `DATA_MANIFEST.md` — inventário de datasets

---

## NOTAS FINAIS

**Este documento é vivo.** Será atualizado conforme se descobrem casos limite. Alterações requerem aprovação da Barbara.

**O que este framework NÃO substitui:** o julgamento da Barbara como PO. Há decisões (ex.: lot sizing por sessão, apetite ao risco, qual par trocar) que são negociais, não estatísticas. O framework garante que **quando** é uma decisão estatística, seja feita de forma defensável.

**O que este framework protege contra:** os erros típicos documentados no material Purdue (overfitting, underfitting, data leakage) **E** os erros específicos de trading (look-ahead bias via K-Fold, regime change não detectado, thresholds absolutos em vez de relativos, hyperparameter tuning sem DSR, aplicação weekend sem observação).

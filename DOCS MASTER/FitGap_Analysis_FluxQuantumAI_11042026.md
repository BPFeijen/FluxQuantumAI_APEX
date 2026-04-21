# FluxQuantumAI — Fit × Gap Analysis
**Visão Original vs Implementação Actual**
Data: 2026-04-11 | Referência: FLUXQUANTUMAI_OVERVIEW v3.2 + Modules_CurrentState_23122025

---

## Legenda

| Símbolo | Significado |
|---------|-------------|
| ✅ FIT | Implementado e alinhado com a visão original |
| 🔄 PARCIAL | Implementado mas incompleto ou diferente do spec |
| ❌ GAP | Previsto no original, não implementado |
| ⚠️ DESVIO | Implementado mas diverge da intenção arquitectural |

---

## 1. ORDERSTORM DETECTOR — "See What Others Can't See"

### Visão Original
Motor de detecção de **ordens iceberg institucionais** em tempo real.
Capacidades: Detecção, Classificação (Native/Synthetic), Previsão de Tamanho (Kaplan-Meier), Tracking Institucional.

### Fit × Gap

| Sub-Módulo | Status | Implementação Actual | Gap |
|-----------|--------|---------------------|-----|
| Detecção heurística (refill < 100ms) | ✅ FIT | `iceberg_receiver.py` porta 8002 + `Iceberg Orders Detector.dll` | — |
| Classificação Native vs Synthetic | ✅ FIT | Detector C# no Quantower distingue pelo order_id | — |
| Confirmação (≥2 refills) | ✅ FIT | Implementado na DLL + `iceberg_receiver.py` | — |
| ML Classifier (99.48% accuracy) | 🔄 PARCIAL | `ml_iceberg_v2` em treino (PID 6656) — ainda não validado | Accuracy alvo 99.48% não confirmada |
| Previsão de tamanho (Kaplan-Meier) | ❌ GAP | Não implementado | Nenhum ficheiro com Kaplan-Meier no repo |
| Tracking institucional (onde smart money posiciona) | ❌ GAP | Não implementado como módulo explícito | Só temos detecção pontual, sem histórico de acumulação |
| Side Detection (BID=buy / ASK=sell) | ✅ FIT | `iceberg_sequence_direction_score` (F20 do schema) | — |
| Confluência com Gate (±30% size) | ✅ FIT | Gate 4 do ATS: `iceberg_proxy_threshold=0.85` (CAL-20) | Multiplier fixo, não ±30% dinâmico |

### Score OrderStorm: **5/8 FIT** — Gap crítico: Kaplan-Meier + Institutional Tracking

---

## 2. ANOMALYFORGE — "Know Before It Happens"

### Visão Original
Motor de anomalias e regime. Score contínuo 0–100%. Stages: NORMAL < CAUTIOUS < HIGH_VOL < UNSTABLE < PRE-HALT.
Bloqueia entradas progressivamente: Score>70%→50% size, >85%→25% size, >95%→BLOCK.

### Fit × Gap

| Sub-Módulo | Status | Implementação Actual | Gap |
|-----------|--------|---------------------|-----|
| Anomaly VETO (bloquear entradas) | ✅ FIT | `StatGuardrail` (STALE_DATA + SPREAD_WIDEN) — LIVE | — |
| Z-Score Defense Mode | ✅ FIT | `GrenadierDefenseMode` (4 features, scaler calibrado em 2.7M rows) | — |
| Autoencoder MSE como sensor | ✅ FIT | `AnomalyForgeV2` LSTM Autoencoder (26f SCHEMA_FLUXFOX_V2) — LIVE em PONTO 0.5 (2026-04-12); bug imputation corrigido; widget dashboard `/api/production/anomaly_forge` | — |
| Score normalizado 0–100% | ❌ GAP | Grenadier só produz MSE bruto + threshold binário | Necessário mapear MSE → [0,1] score contínuo (Sprint 3) |
| Sizing progressivo por score | ❌ GAP | Atual: binário (BLOCK ou FULL SIZE) | Original prevê 4 níveis: 100%→50%→25%→0% |
| Regime Classification (5 estados) | ❌ GAP | `MarketRegime` definido em `regime_detector.py` mas não integrado no gate | Código existe, não está a alimentar decisões |
| Halt Prediction | ❌ GAP | Não implementado | Complexo — requer dados históricos de halts |
| Score Trend Analysis | 🔄 PARCIAL | `AnomalyScorer.get_score_trend()` existe mas não usada em produção | — |

### Score AnomalyForge: **4/8 FIT** — Gap principal: score contínuo + sizing progressivo + regime classification

---

## 3. FLUX SIGNAL ENGINE — "Precision Entry Timing"

### Visão Original
4 estratégias combinadas com pesos: DOM Imbalance (30%) + ORB Breakout (25%) + Mean Reversion (20%) + Absorption (25%).
Session multipliers: NY=1.0x, London=0.7x, Asia=0.5x.

### Fit × Gap

| Sub-Módulo | Status | Implementação Actual | Gap |
|-----------|--------|---------------------|-----|
| DOM Imbalance como sinal primário | ✅ FIT | Gate 3 ATS — dom_imbalance com thresholds calibrados (CAL-14/17) | — |
| Calibração por sessão (Asia/London/NY) | ✅ FIT | Kill Zones implementadas (`live/kill_zones.py`) | — |
| ORB Breakout strategy | ❌ GAP | Não implementado | Nenhum ficheiro de ORB Breakout no repo |
| Mean Reversion strategy | ❌ GAP | Não implementado como módulo explícito | DOM é usado como sinal de exaustão (próximo), mas ORB+MR não existem |
| Absorption strategy | 🔄 PARCIAL | `ABSORPTION_BID/ASK` nos warnings direccionais do MNQ Signal — detectado mas não é estratégia standalone | Warnings existem, não é gate explícito |
| Aggregation ponderada (score final) | ⚠️ DESVIO | ATS usa gating hierárquico (PASS/BLOCK) em vez de score ponderado | Decisão arquitectural correcta (hierarchical > weighted avg) |
| VPIN (informed trading) | ❌ GAP | Definido como warning mas não calculado no pipeline actual | Dado necessário: volume + trade classification |
| Cancel Rate | ❌ GAP | Definido como warning, não calculado | Requer análise de order flow histórico |
| Toxicity Score | ❌ GAP | Definido como warning, não calculado | Requer VPIN como input |
| Price Speed / Displacement | 🔄 PARCIAL | `PriceSpeedTracker` implementado em `live/price_speed.py` | Não integrado como gate formal |

### Score Flux Signal Engine: **3/10 FIT** — Gap principal: ORB + Mean Reversion + VPIN/Cancel Rate/Toxicity

---

## 4. FOXYZE MASTER ENGINE — "Hierarchical Gating - Not Averaging"

### Visão Original
5 Stages hierárquicos: Anomaly VETO → News VETO → L2 PRIMARY → Iceberg CONFIRM → V7 Rules + Sizing.

### Fit × Gap

| Gate | Status | Implementação Actual | Gap |
|------|--------|---------------------|-----|
| GATE 1: Anomaly VETO | ✅ FIT | `StatGuardrail` + `GrenadierDefenseMode` | Score progressivo não implementado (ver AnomalyForge) |
| GATE 2: News VETO | 🔄 PARCIAL | `proxy_events.json` (10 eventos NFP/CPI/FOMC agendados) | Sem feed de notícias real-time; só eventos pré-agendados |
| GATE 3: L2 PRIMARY (direcção) | ✅ FIT | DOM Imbalance como fonte única de direcção | — |
| GATE 4: Iceberg CONFIRM | ✅ FIT | Gate V4 ML threshold 0.85 (CAL-20) + V2 Level 2 gate (2026-04-12): BLOCK_V2_NOICE quando v4=NEUTRAL AND ice.score≤-2 | Multiplier fixo, não dinâmico ±30% |
| GATE 5: V7 Rules + Sizing | 🔄 PARCIAL | Sizing calibrado (CAL series) | Extension Legs não implementado |
| GATE 6: Runner de Pullback | 🔄 PARCIAL | Movement Detector implementado (PULLBACK/REVERSAL) | Runner logic (abrir posição oposta durante pullback) — verificar |
| GATE 7: Extension Legs | ❌ GAP | Não implementado | Targets 1.25%/1.50%/1.75%/2.00% do spec não existem |
| BLOCO 0: Never Block Close | ✅ FIT | Gates de VETO nunca bloqueiam fechamento de posição activa | — |
| BLOCO 1: Movement Type exit | ✅ FIT | REVERSAL/FALSE_BREAKOUT → fechar; PULLBACK → HOLD | — |
| Sizing progressivo (4 níveis) | ⚠️ DESVIO | ATS usa multiplicadores fixos do CAL, não os 4 níveis do spec | CAL-based é data-driven e provavelmente melhor |

### Score Foxyze Master Engine: **6/10 FIT** — Gap principal: Extension Legs + News real-time + score progressivo

---

## 5. REGIME FORECAST PROVIDER

### Visão Original
Provider preditivo ML que avalia **qualidade de entradas** sugeridas pelo Signal Generator.
Output: `prob_good`, `prob_bad`, `ALLOW_ENTRY/REDUCE_SIZE/AVOID_ENTRY/FORCE_EXIT`.

### Fit × Gap

| Sub-Módulo | Status | Implementação Actual | Gap |
|-----------|--------|---------------------|-----|
| ML entry quality predictor | ❌ GAP | Não implementado em produção | Este é o papel do V3 RL (PPO agent) |
| Heuristic fallback | 🔄 PARCIAL | `regime_forecast/provider.py` tem fallback heurístico | Ficheiro existe, não integrado no pipeline de produção |
| Inputs contextuais (VPIN, toxicity, news) | ❌ GAP | Inputs definidos no spec, não calculados | Dependem dos gaps do Flux Signal Engine |
| prob_good / prob_bad output | ❌ GAP | — | V3 RL resolve com reward function equivalente |

### Score Regime Forecast: **0/4 FIT** — Gap total: este módulo é o V3 RL

---

## 6. Resumo Executivo

### Scorecard por Módulo

| Módulo Original | FIT Score | Prioridade dos Gaps |
|----------------|-----------|---------------------|
| OrderStorm Detector | 5/8 (62%) | Kaplan-Meier + Institutional Tracking |
| AnomalyForge | 3/8 (37%) | Score contínuo + sizing progressivo |
| Flux Signal Engine | 3/10 (30%) | ORB + Mean Reversion + VPIN/Toxicity |
| Foxyze Master Engine | 6/10 (60%) | Extension Legs + News real-time |
| Regime Forecast Provider | 0/4 (0%) | Inteiramente mapeado para V3 RL |

### Visão Geral

```
ORDERSTORM    ████████████░░░░   62%  ← ml_iceberg_v2 em treino fecha parcialmente
ANOMALYFORGE  ██████░░░░░░░░░░   37%  ← Sprint 3 (Grenadier integração) fecha muito
FLUX SIGNAL   ████░░░░░░░░░░░░   30%  ← Maior gap; ORB+MR+VPIN não implementados
FOXYZE ENGINE ████████████░░░░   60%  ← Extensão Legs + News são os gaps principais
REGIME FCST   ░░░░░░░░░░░░░░░░    0%  ← Totalmente mapeado para V3 RL
```

---

## 7. Gaps por Prioridade

### Prioridade ALTA (impacto directo em produção)

| Gap | Módulo | O que falta | Esforço |
|-----|--------|------------|---------|
| Anomaly Score 0–100% contínuo | AnomalyForge | Mapear Grenadier MSE → [0,1]; sizing progressivo 4 níveis | Baixo — Sprint 3 |
| Regime Classification integrada no gate | AnomalyForge | Ligar `MarketRegime` ao `event_processor.py` | Médio |
| ml_iceberg_v2 em produção | OrderStorm | Job em treino → validar → integrar scorer | Médio |

### Prioridade MÉDIA (aumenta precisão mas não é bloqueador)

| Gap | Módulo | O que falta | Esforço |
|-----|--------|------------|---------|
| Sizing progressivo por anomaly score | Foxyze | Substituir binary BLOCK por 4 níveis (100/50/25/0%) | Baixo |
| Extension Legs | Foxyze | Targets 1.25%/1.50%/1.75%/2.00% após TP1 | Médio |
| News real-time | Foxyze | Integrar feed de notícias (ex: Reuters, Benzinga) | Alto |
| Price Speed como gate formal | Flux Signal | `PriceSpeedTracker` já existe — ligar ao gate | Baixo |

### Prioridade BAIXA (V3 e além)

| Gap | Módulo | O que falta | Esforço |
|-----|--------|------------|---------|
| Regime Forecast Provider (ML) | Regime | V3 RL PPO resolve isto | Alto (V3) |
| Kaplan-Meier size prediction | OrderStorm | Estimar volume total oculto do iceberg | Alto |
| ORB Breakout strategy | Flux Signal | Implementar do zero | Médio |
| Mean Reversion strategy | Flux Signal | Implementar do zero | Médio |
| VPIN | Flux Signal | Requer tick-by-tick volume classification | Alto |
| Toxicity Score | Flux Signal | Depende de VPIN | Alto |
| Cancel Rate | Flux Signal | Requer análise de order amendments | Alto |
| Institutional Tracking | OrderStorm | Histórico de posicionamento de large players | Alto |
| Halt Prediction | AnomalyForge | Requer dados históricos de circuit breakers | Muito alto |

---

## 8. Mapa de Convergência: Actual → Original

```
ACTUAL (hoje)                    ORIGINAL (visão)
─────────────────────────────    ──────────────────────────────
StatGuardrail             ───►   AnomalyForge Stage 1 (VETO)       ✅
GrenadierDefenseMode      ───►   AnomalyForge (Z-Score fallback)    ✅
Grenadier V2 LSTM (LIVE)  ───►   AnomalyForge (MSE autoencoder)     ✅ (imputation fix 2026-04-12)
proxy_events.json         ───►   News Provider (VETO/FORCE_EXIT)    🔄 (sem real-time)
DOM Imbalance gate        ───►   Flux Signal Engine Stage 3 PRIMARY  ✅
Kill Zones                ───►   Session Awareness (NY/London/Asia)  ✅
ml_iceberg_v2 (treino)    ───►   OrderStorm IcebergClassifier        🔄
Iceberg gate V4 (0.85) + V2 Level2 ───►   OrderStorm Stage 4 CONFIRMATION     ✅
CAL-14→CAL-20 sizing      ───►   V7 Rules + Sizing Stage 5           ✅ (diferente método)
Movement Detector         ───►   BLOCO 1 (PULLBACK/REVERSAL exit)    ✅
V3 RL PPO (construído)    ───►   Regime Forecast Provider            🔄 (a integrar)
─────────────────────────────────────────────────────────────────────────
ORB Breakout              ───►   ❌ NÃO IMPLEMENTADO
Mean Reversion            ───►   ❌ NÃO IMPLEMENTADO
Kaplan-Meier              ───►   ❌ NÃO IMPLEMENTADO
VPIN / Toxicity           ───►   ❌ NÃO IMPLEMENTADO
Extension Legs            ───►   ❌ NÃO IMPLEMENTADO
Score 0-100% contínuo     ───►   ❌ PENDENTE (Sprint 3)
```

---

## 9. Próximos Passos Recomendados

### Fase Imediata (Sprint 3 — Grenadier Integration)
1. Grenadier V2 treino concluir → descarregar modelo do S3
2. Integrar MSE → score [0,1] no `event_processor.py`
3. Substituir binary BLOCK por sizing progressivo (4 níveis)
4. Ligar `MarketRegime` ao gate (NORMAL/HIGH_VOL/UNSTABLE/PRE-HALT)

### Fase Curto Prazo (pós-Grenadier)
5. ml_iceberg_v2 validar em produção → substituir heurística do Quantower
6. Extension Legs (BLOCO 3): implementar targets pós-TP1
7. Price Speed como gate formal (ficheiro já existe)

### Fase Médio Prazo
8. ORB Breakout + Mean Reversion como estratégias adicionais no Flux Signal
9. News real-time feed

### Fase V3
10. V3 RL PPO (construído hoje) → integrar como Regime Forecast Provider substituto
11. VPIN + Toxicity (requer análise tick-level avançada)
12. Kaplan-Meier para size prediction de icebergs

---

*Análise baseada em: FLUXQUANTUMAI_OVERVIEW v3.2, FluxQuantumAI_Modules_CurrentState_23122025, estado actual do repo C:\FluxQuantumAI + C:\FluxQuantumAPEX*

# DEEP L2 POST-EVENT ANALYSIS — "Earliest Safe Entry"

**Timestamp:** 2026-04-19
**Mode:** READ-ONLY
**Data:** 117 HIGH events × calibration_dataset_full (M1 OHLCV + L2), Jul 2025 → Apr 2026
**Question:** É seguro abrir trades 5min, 15min ou 30min depois da notícia? Qual a entry que maximiza ganho?

---

## 1. L2 DATA COVERAGE (sanity)

| Feature | % non-null |
|---|---|
| close (M1) | 100% |
| l2_dom_imbalance | 97.2% |
| l2_bar_delta | 97.2% |
| l2_bid_pressure / ask_pressure | 39.4% (sparse — usado com cuidado) |

---

## 2. RECOMENDAÇÃO FINAL POR EVENT TYPE

**Métrica-chave:** MFE/MAE ratio (Max Favorable Excursion / Max Adverse Excursion) na janela de entrada.
- `MFE/MAE ≥ 1.3` = entry favorável (risk/reward positivo)
- `MFE/MAE < 1.0` = entry desvantajoso (perde mais do que ganha)

| Event Type | n | Entry ótimo | MFE/MAE | Risk ratio (abs vol) | pause_after sugerido |
|---|---|---|---|---|---|
| **FOMC** | 28 | ❌ **NUNCA ≤60min** | 0.32–0.58 (BAD em todas as janelas) | 1.66–2.15× | **60min** (ou skip) |
| **CPI** | 25 | **T+15min** | 1.34 | 1.44× @ T+15-30 | **15min** |
| **NFP** | 10 | **T+15min** ⭐ | **2.34** (sweet spot) | 1.78× @ T+15-30 | **15min** |
| **UNEMPLOYMENT** | 9 | **T+15min** | 2.23 | 1.77× @ T+15-30 | **15min** |
| **PPI** | 10 | **T+30min** | 1.24 | 1.55× @ T+15-30 | **30min** |
| **GDP** | 8 | **T+30min** | 2.40 | 1.36× @ T+15-30 | **30min** |
| **FED_SPEECH** | 9 | **T+0 (imediato!)** ⭐ | **6.11** (excelente) | 1.88× @ T+1-5 | **3min** (só pico) |
| **OTHER** | 18 | **T+0** | 6.34 | 1.68× @ T+1-5 | **3min** |

⭐ = sweet spot (risk/reward particularmente favorável)
❌ = entry desaconselhada em toda a janela analisada

---

## 3. DETALHE POR EVENT TYPE

### 3.1 FOMC — ⚠ O MAIS PERIGOSO (evitar completamente)

**MFE/MAE:** 0.58 (T+0), 0.42 (T+5), 0.32 (T+15), 0.44 (T+30) — **perde mais do que ganha em todas as janelas**
**MAE p90:** 85-110 bps (i.e., 10% das vezes o draw-down passa de 85-110bps)
**Risk ratio:** persiste 1.66-2.15× baseline até T+60min
**Direcção:** bias negativo lento (+3bps T+5, −8bps T+30, **−19bps T+60**)
**Interpretação:** após FOMC, mercado continua a repricing durante 30-60min com draw-downs grandes e aleatórios. **Nenhum ponto de entrada seguro em 60min.**
**Recomendação:** `pause_after=60` (restaurar valor antigo) ou `pause_after=90` para margem extra.

### 3.2 CPI — explosão pontual, resolve rápido

**DURING vol = 9.72× baseline** (maior explosão de todos!)
**MFE/MAE:** 1.24 (T+0), 0.96 (T+5 — pior), 1.34 (T+15), 1.15 (T+30)
**Nota:** T+5 é o **pior momento** (ainda em chaos de repricing)
**Sweet spot:** T+15min (MAE cai 20% vs T+0, MFE ligeiramente melhor)
**Direcção:** neutra mas high-vol (signed std ~5bps)
**Recomendação:** `pause_after=15`

### 3.3 NFP — sweet spot clássico em T+15 ⭐

**DURING vol = 8.04× baseline**
**MFE/MAE:** 1.34 (T+0), 1.48 (T+5), **2.34 (T+15!)**, 1.43 (T+30)
**MAE em T+15:** 12 bps mean (vs 24 em T+0) — **cai para metade**
**MFE em T+15:** 28 bps mean — excelente
**Direcção:** upward bias fraco mas persistente (+12bps a T+60)
**Recomendação:** `pause_after=15` — T+15 é o ponto óptimo

### 3.4 UNEMPLOYMENT — similar a NFP

**DURING vol = 8.44× baseline** (2º maior pico)
**MFE/MAE:** 1.25 → 1.34 → **2.23 (T+15)** → 1.37
**Recomendação:** `pause_after=15`

### 3.5 GDP — move persistente, entrar tarde

**DURING vol = 4.14× baseline**
**MFE/MAE:** 0.52 (T+0), 0.46 (T+5), 0.61 (T+15), **2.40 (T+30)** ⭐
**MAE em T+30 cai para 7 bps** (vs 26 em T+0)
**Direcção:** bias ligeiramente negativo (−10bps a T+60)
**Recomendação:** `pause_after=30`

### 3.6 PPI — risk/reward marginal

**MFE/MAE:** 0.76 (T+0), 0.69 (T+5), 1.11 (T+15), 1.24 (T+30)
**Direcção:** levemente negativo (−7bps a T+60)
**Recomendação:** `pause_after=30` (ou `pause_after=15` se willing to accept marginal MFE/MAE)

### 3.7 FED_SPEECH — SAFE e DIRECIONAL ⭐

**MFE/MAE:** **6.11 (T+0), 7.90 (T+5), 13.25 (T+15!), 0.92 (T+30)**
**MAE em T+15: 2.6 bps** — quase zero drawdown
**MFE a T+30: +31bps persistente** — upward trend forte
**Interpretação:** Powell falando = momentum puro, poucos false starts
**CUIDADO:** Entry tardia (T+30+) perde a oportunidade (MFE colapsa)
**Recomendação:** `pause_after=3` (só protecção do pico de 1min) — permitir entrada cedo

### 3.8 OTHER (Trump speeches, etc.) — 171 eventos, média moderada

**MFE/MAE:** ~6x em todas as janelas — safe to trade
**Recomendação:** `pause_after=3`

---

## 4. TABELA COMPARATIVA — current (5/3) vs data-driven vs old-pre-apply

| Event | Old (pre-Apply) | Current (5/3 applied) | **Data-driven (recomendado)** |
|---|---|---|---|
| FOMC | 30/60 | 5/3 | **5/60** |
| NFP | 30/30 | 5/3 | **5/15** |
| CPI | 30/15 | 5/3 | **5/15** |
| GDP | 30/15 | 5/3 | **5/30** |
| PPI | 15/15 | 5/3 | **5/30** |
| FED_SPEECH | 15/30 | 5/3 | **5/3** (current é ÓPTIMO) |
| UNEMPLOYMENT | 15/10 | 5/3 | **5/15** |
| ISM | 15/10 | 5/3 | **5/3** (mild event) |
| RETAIL_SALES | 15/10 | 5/3 | **5/3** (mild) |
| ECB | 30/30 | 5/3 | **5/3** (US-only filter — irrelevante) |
| BOJ | 15/15 | 5/3 | **5/3** (US-only filter — irrelevante) |

**pause_before=5 mantém-se** em todos os casos — a análise confirma que PRE_5 não é elevado em vol e MAE pré-evento é aceitável.

---

## 5. INSIGHT L2 ADICIONAL — order flow após release

**dom_imbalance** durante DURING (primeiro minuto):
- CPI: +2.78 (buying pressure spike)
- NFP: -0.30 (mixed)
- FOMC: -5.39 (**selling pressure!**) — consistente com bias negativo observado
- UNEMPLOYMENT: -1.86

**bar_delta** durante DURING:
- CPI: +197 (extremo buying aggression)
- NFP: +117 (strong buying)
- UNEMPLOYMENT: +131 (strong buying)
- FOMC: +6 (moderate)
- GDP: -80 (aggressive selling)

**Interpretação:** o ALPHA trigger (que olha para signed order flow) pode beneficiar de usar dom_imbalance/bar_delta no POST_1_5 window para detectar continuação ou reversão do move.

**Sugestão futura (fora de scope actual):** reforçar gates com order-flow directional gate pós-event_window_end.

---

## 6. RECOMENDAÇÕES OPERACIONAIS

### Alinhamento com Rollback B proposto (Claude + Barbara conversa)

Rollback B proposta (versão simples):
- CRITICAL (FOMC, NFP): 30min post
- HIGH (CPI, GDP, PPI, FED_SPEECH): 15min post
- MEDIUM: 10min post

**Refinamento data-driven sugerido aqui:**

| Event | Rollback B | **Data-driven refinement** |
|---|---|---|
| FOMC | 30 | **60** (persiste muito) |
| NFP | 30 | **15** (sweet spot T+15) |
| CPI | 15 | **15** (ok) |
| GDP | 15 | **30** (safe em T+30) |
| PPI | 15 | **30** (marginal até T+30) |
| FED_SPEECH | 15 | **3** (imediatamente safe — oportunidade!) |

**Diferenças importantes vs Rollback B linear:**
- FOMC: **60min** (não 30) — o tail é mais longo
- FED_SPEECH: **3min** (não 15) — é um evento directional safe para entrar cedo
- NFP: **15min** (como proposto) — confirma sweet spot

### Se quiseres maximizar ganhos sem tocar no código hoje

**Rollback B "vanilla"** (30/15/10) é ganho de segurança sobre o 5/3 actual, mas **deixa na mesa**:
- Oportunidades em FED_SPEECH entre T+0 e T+15 (bloqueadas 15min desnecessariamente)
- Risco residual em FOMC entre T+30 e T+60 (não coberto)
- Oportunidades em GDP/PPI entre T+15 e T+30 (bloqueadas por 15min ou expostas sem protecção)

**Uma opção:** aplicar Rollback B agora + planear sprint de refinamento por event type baseado nesta análise.

---

## 7. FILES GERADOS

| File | Purpose |
|---|---|
| `deep_l2_post_event_analysis.py` | Script reprodutível |
| `deep_l2_post_event_aggregates.csv` | Per event_type × bucket × L2 stats |
| `deep_l2_cum_return_trajectory.csv` | Cum return T+0 to T+60 por event_type |
| `deep_l2_mae_by_entry.csv` | MAE/MFE por ponto de entrada |
| `DEEP_L2_POST_EVENT_REPORT.md` | Este documento |

## 8. FILES NÃO MODIFICADOS

- Zero edits a código/yaml/config
- Capture processes (12332, 8248, 2512): intactos
- Service FluxQuantumAPEX: still running on 5/3 windows

---

## RESUMO RÁPIDO — "É seguro abrir trades X min depois?"

| Janela | Resposta |
|---|---|
| **T+5min** | ❌ Não — maioria dos eventos ainda em chaos (ratio 1.7-2.3× baseline) |
| **T+15min** | ✅ Para CPI, NFP, UNEMPLOYMENT — **sweet spot** com MFE/MAE 1.3-2.3× |
| **T+30min** | ✅ Para GDP, PPI — ratio vol normaliza, MFE supera MAE |
| **FED_SPEECH** | ✅ T+0 a T+15 — ratio 6-13× (melhor risk/reward da análise) |
| **FOMC** | ❌ Só após T+60 (ou nunca) — persiste toda a hora pós-release |

**Claim principal para Barbara:** windows devem ser **diferenciadas por event type**, não globais. O 5/3 actual é demasiado curto para FOMC/NFP/CPI mas **bem calibrado** para FED_SPEECH. Rollback B "vanilla" (30/15/10) melhora mas ainda deixa alpha na mesa. Spec ideal na tabela §6.

# Auditoria Profunda — FluxQuantumAI live + Plano de Correção

**Data**: 2026-05-08
**Autor**: CC#3
**Escopo**: `C:\FluxQuantumAI\live\` + `config/` + comparação contra `C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\`
**Status sistema**: STOPPED (parado por Barbara 2026-05-08 ~15:58 UTC)
**Brokers**: RoboForex + Hantec DESCONECTADOS toda a janela 24h+

---

## Sumário executivo

Auditoria triangulada (arquitetura + fluxo de decisões + fit-gap metodológico) identificou **4 categorias de falha** que explicam comportamento errático recente:

| Categoria | Severidade | # findings |
|---|---|---|
| **P0 — race conditions / single-writer violation** | 🔴 BLOCKER | 3 writers concorrentes em `decision_live.json` → 72× `WinError 32` em 24h |
| **P1 — gates de signal demasiado permissivos** | 🔴 CRITICAL | `MIN_SCORE_GO=0` permite iceberg-only GOs; same-level cooldown bypass causa spam 10+/30s |
| **P2 — gaps metodológicos vs ATS Docs** | 🟠 HIGH | 5 violações ATS (SL displacement, ATR extreme gate, RR<1, FMV fallback, position sizing) |
| **P3 — débito arquitetural** | 🟡 MED | `event_processor.py` 4918 LOC god-object; ADR-001 shadow violations; config drift |

**Resultado da Opção B (deployed hoje)**: F-1+F-3 voting calibração ficou conservadora demais → m30_bias=unknown 95% do tempo → F-asymmetric filter inativo → signals raw spam.

**Sintese**: o sistema NÃO sofre de bug único — sofre de **múltiplas pequenas falhas que se compõem** num pipeline complexo (4918 linhas em event_processor). A correção precisa ser estruturada em ondas, NÃO em hotfixes pontuais.

---

## 1. Findings detalhados

### 🔴 P0-1 — `decision_live.json` tem 3 writers concorrentes (race condition root cause)

**Evidência**: 72× `WinError 32` ("file in use") + 1× `WinError 5` (access denied) em stderr 24h.

**Writers identificados**:
| File | Linha | Lock? |
|---|---|---|
| `live/event_processor.py` | ~710 (`_write_decision`) | ❌ NONE |
| `live/event_processor.py` | ~1402 (segunda chamada em path diferente) | ❌ NONE |
| `live/hedge_manager.py` | ~503 (hedge events) | ❌ NONE |
| `live/position_monitor.py` | ~2254 | ✅ `self._canonical_lock` |

Apenas `position_monitor` usa lock. Os outros dois (event_processor 2 sites + hedge_manager) escrevem sem mutex → race condition em filesystem rename → arquivo fica corrompido/parcial → Dashboard/Telegram lêem snapshot inconsistente.

**Impacto downstream**:
- Dashboard mostra estado errado
- Telegram envia mensagem com dados incoerentes
- Posição monitor lê SL/TP inconsistente
- **Provável contribuinte para "sinais invertidos" reportados** (mensagem render do Telegram pode pegar tmp parcial)

**Violação direta**: `APEX_Stabilization_Plan.md` mandata "Single Source of Truth = decision_live.json + 4 processos lendo" — **single writer principle violado**.

---

### 🔴 P1-1 — `MIN_SCORE_GO = 0` permite GO neutro (iceberg-only signals)

**Localização**: `live/ats_live_gate.py:220` (`MIN_SCORE_GO = 0`)

**Mecanismo do spam observado hoje (10+ SHORTs em 30s @ MT5 4685.75)**:
```
total_score = mom.score (0, neutro) + ice.score (+4, large_order)
total_score = +4 >= 0 → GO emitido
m30_bias=unknown → cascade Layer 5 default → F-asym INATIVO
direction resolved via Layer 4 (provisional bearish stale) → SHORT
→ GO SHORT @ price corrente (no LOW da range, mal-timing)
Repete 10+× sem que cooldown impeça
```

**Fix**: `MIN_SCORE_GO = 1` (ou 2) bloqueia entries com score puramente neutral+iceberg. Ou exigir `iceberg.aligned=True` adicionalmente.

---

### 🔴 P1-2 — same_level_cooldown bypass

**Localização**: `live/event_processor.py:1948-1966` (`_check_pre_entry_gates` cooldown logic)

**Mecanismo**:
- `same_level_cooldown_min=60` em settings.json funcionalmente seta janela
- Mas a checagem usa `abs(price - last_trade_level) <= same_level_proximity_pts (2.0)`
- **Se price oscila entre 4685.75 e 4685.77** entre eventos iceberg, cada um escapa do proximity check → cooldown não dispara
- Adicionalmente: iceberg watchdog pode reprocessar mesmo evento jsonl quando arquivo é re-escrito → entrega duplicatas com mesmo timestamp

**Fix**: 
1. Aumentar `same_level_proximity_pts` de 2.0 → 5.0 (ou 0.2 × ATR)
2. Adicionar dedup hash em `_on_iceberg_event` (event_id baseado em ts+price+broker)
3. Considerar `_jsonl_lock` mais agressivo na deduplicação

---

### 🔴 P1-3 — Cascade Layer 4 (provisional) override risky em mercado choppy

**Localização**: `live/event_processor.py:3696-3701`

**Risk**: Quando `daily_trend=unknown` AND `m30_bias_confirmed=False`, cascade resolve direção via Layer 4 (`provisional_m30_bias`). Provisional pode estar stale ou refletir bias de uma vela transient (justamente o caso Box 5282 que motivou Opção B).

**Hoje em produção**: 95% das decisões com bias confirmed=False → cascade usou Layer 4 quase sempre → F-asym aplicou-se a um sinal de baixa confiança.

**Fix**:
- Guardar Layer 4 com idade máxima (`provisional_age_s < 60`) OU exigir Layer 2 (tick_breakout) ativo simultâneamente
- Adicionar log explícito quando Layer 4 fires (visibility para auditoria)

---

### 🔴 P1-4 — F-asymmetric pode ser desabilitado por flag

**Localização**: `live/event_processor.py:3771` + `config/settings.json:67` (`range_bound_bias_filter_enabled`)

Se flag = `false`, F-asym **não bloqueia** counter-trend em RANGE_BOUND. Em produção a flag está `true` mas continua sendo um vector de regressão se alguém alterar settings.

**Fix**: forçar F-asym sempre ativo em produção (flag advisory only). Adicionar guarda no código que ignora flag=false em prod.

---

### 🟠 P2-1 — ATS Strategy 1 (Range-Bound): missing adaptive risk windowing

**Documento**: `ATS Basic Strategy 1_Range Bound Markets.txt` mandata "define risk window based on cycle; close if risk window broken; smaller position sizes (1/3-1/5)".

**Live**: `event_processor.py:3760-3850` implementa RANGE_BOUND com triggers liq_top/liq_bot, mas:
- TP fixo em FMV/liq lines (não adaptativo a cycle)
- Sem "exit if cycle changes" semantics — depende só de daily_trend flip
- Lot size fixo (sem 1/3-1/5 scaling)

---

### 🟠 P2-2 — ATS Strategy 2 (Trending): CONTINUATION custom, não documentada nas ATS Docs

**Documento**: `ATS_Trading Strategy 2_Trending Markets.txt` documenta apenas PULLBACK + bias mandatório. CONTINUATION foi adicionada via Sprint 9 como extensão.

**Live** (`event_processor.py:3396-3469`):
- PULLBACK ✓ alinhado com docs
- CONTINUATION usa delta_4h + displacement_bar (não documentado)
- Validação difere: docs dizem "next 2-3 candles same color"; live usa delta_4h numeric thresholds

**Risk**: CONTINUATION foi a estratégia que produziu BUG-SL-DISPLACEMENT (74 SLs invertidos pré-fix). É a estratégia menos validada metodologicamente.

---

### 🟠 P2-3 — M30 Framework: 2/5 spec rules FAIL

| Rule | Status | Evidência |
|---|---|---|
| R1 box cadence 60s | ✅ PASS | `m5_updater.py` daemon |
| R2 FMV = midpoint ALWAYS | ⚠️ PARTIAL | `position_monitor.py:1337` faz fallback `tp1 = m30_fmv or (price + atr*0.7)` — viola "ALWAYS" |
| R3 liq_top/bot semantics | ✅ PASS | commit 32343cf alinha com Wyckoff |
| **R4 ATR extreme regime gate** | ❌ FAIL | `daily_atr_regime` definido mas event_processor nunca lê — sistema entra em volatilidade extrema |
| R5 RR>=1.0 universal | ⚠️ PARTIAL | RANGE_BOUND mode pula RR check |

---

### 🟠 P2-4 — Risk management: 5+ violações sistemáticas

Per `ATS_How to Manage Risk.txt`:
- ❌ SL não-adaptativo (fixo `±sl_pts` em vez de risk-window)
- ❌ Position sizing fixo (LOT_SIZE constante; spec mandata 1/3-1/5)
- ❌ TP2 não usa HT architecture (spec: "look at HT for max target")
- ❌ Sem "close if risk window broken" exit
- ❌ Sem daily loss limit

---

### 🟠 P2-5 — BUG-SL-DISPLACEMENT (CONTINUATION) FIXADO mas semântica imperfeita

**Status**: fixado hoje 2026-05-07 14:43 UTC (sanity check `disp_lo < price` para LONG)

**Issue residual**: o fix faz fallback para `price ± sl_pts` quando displacement é orientado errado. Isso protege da SL invertida MAS continua emitindo o signal. O correto seria: se displacement não é mais "ativo", a entry CONTINUATION inteira é inválida → rejeitar signal.

**Fix complementar**: `_get_trend_entry_mode` rejeita entry se `_disp_lo >= price` (LONG) ou `_disp_hi <= price` (SHORT) em vez de só fallback SL.

---

### 🟡 P3-1 — ADR-001 shadow violations (D1/H4 parquets)

**Localização**: `live/d1_h4_updater.py:66-113` cria `gc_h4_boxes.parquet` e `gc_d1_boxes.parquet` com colunas execution-shaped (`h4_liq_top`, `h4_box_high`, etc.)

**Status**: SHADOW MODE — não consumido por execution path no momento.

**Risk**: ADR-001 explicitamente proíbe esse código. Se algum sprint futuro reativá-lo, viola execution timeframe. **Débito arquitetural alto** dado que ADR-001 é "MANDATORY — VIOLATION = SYSTEM FAILURE".

---

### 🟡 P3-2 — `event_processor.py` god-object (4918 LOC)

Responsabilidades amontoadas:
- Gate logic
- Tick execution
- Position signal building
- Service state heartbeat
- Signal queue
- M5/M30 refresh
- Metrics aggregation
- Decision persistence
- Protection advice cache

**Risk**: Qualquer bug em `_write_decision` afeta TODOS os 4 processos downstream. Difícil testar incrementalmente.

---

### 🟡 P3-3 — Config drift

| Key | settings.json | Code | Status |
|---|---|---|---|
| `cooldown_atr_mult` | line 90 | grep returns 0 | UNUSED |
| `cooldown_pts` | line 91 | grep returns 0 | UNUSED |
| `trend_resumption_threshold` | line 5 (`null`) | só usado `_short` | DEAD |
| `delta_4h_long_block`/`_short_block` | line 2-3 | usado em `event_processor.py:3717` | ATIVO mas **comentário linha 15 marca "INVERTED"** — fix pode estar incompleto |

---

### 🟡 P3-4 — Pre-existing FIT_GAP items ainda abertos (Apr 9 → May 8)

Per `FIT_GAP_EventProcessor_vs_GitHub_Engine_20260409.md`:
- ❌ MFE/MAE tracking + Giveback rule — ainda absent
- ❌ Daily loss limit — ainda absent
- ❌ Daily range gate (20% bottom/top) — absent
- ❌ Session close detection (30min block) — absent
- ⚠️ Anomaly Gate (Grenadier) loaded mas NÃO no gate chain
- ⚠️ Iceberg como score, NÃO multiplier

---

## 2. Recurring failure modes (root causes)

Os bugs recentes (BUG-SIGNAL-INVERTED, BUG-SL-DISPLACEMENT, sinais invertidos, signal spam) **não são independentes**. Compartilham 3 causas raízes:

### Causa raiz A — **Single-writer violation** + falta de mutex
→ decision_live.json fica inconsistente → downstream (Telegram, Dashboard, position_monitor) lê dados parciais → operadora vê valores errados → frustração e parada do sistema.

### Causa raiz B — **Gates de score muito permissivos**
→ `MIN_SCORE_GO=0` + cooldowns frágeis + iceberg single-source = bursts de signals isolados sem confirmação cascade.

### Causa raiz C — **Calibração baseada em corpus subsampled**
→ Opção B (F-1+F-3) calibrada em 6000 decisões Apr-May, mas distribuição em produção difere → bias=unknown 95% do tempo → F-asym filter desabilitado de facto.

---

## 3. Plano de Correção em 4 ondas

### 🌊 Onda W1 — Estabilizar (BLOCKER fixes, ~4h)

Foco: parar o sangramento. SEM redesenho. SEM novas features.

| # | Ação | Arquivos | ETA | Test |
|---|---|---|---|---|
| W1.1 | Adicionar `_canonical_lock = threading.Lock()` em `EventProcessor.__init__`; envolver TODOS os 3 sites de write em `with self._canonical_lock:` | event_processor.py, hedge_manager.py | 1h | Smoke: 0 WinError 32 em 1h running |
| W1.2 | Subir `MIN_SCORE_GO = 1` (bloqueia neutral + iceberg-only); adicionar test que falha se score=0 + ice=+4 → GO | ats_live_gate.py:220, tests/ | 30min | Unit test |
| W1.3 | Aumentar `same_level_proximity_pts: 2.0 → 5.0` em settings.json; adicionar dedup hash em `_on_iceberg_event` | settings.json, event_processor.py | 1h | Test que dispara iceberg 10× em 30s e conta 1 GO emitido |
| W1.4 | Rollback Opção B defaults: `m30_bias_min_bars: 5 → 3, m30_bias_voting_window: 5 → 3` (afrouxar para bias resolver mais frequente) | settings.json | 5min | Re-run audit script confirma bias≠unknown >50% |
| W1.5 | Reverter Opção B se rollback parameter ainda dá unknown >70% — voltar `derive_m30_bias` confirmed-path para baseline (single-most-recent box) | level_detector.py | 30min se necessário | Audit |

**Gate W1**: 1h smoke run, 0 WinError 32, 0 iceberg-only GOs, m30_bias resolve >50% das decisões.

---

### 🌊 Onda W2 — Conformidade ATS (HIGH fixes, ~6h)

Foco: alinhar com canonical ATS Docs. Validar com Barbara antes de cada item.

| # | Ação | Source doc | Arquivos |
|---|---|---|---|
| W2.1 | Adicionar gate ATR extreme regime: `if daily_atr_regime == 'extreme': BLOCK` | TECH_M30_Framework R4 | event_processor.py + level_detector.py |
| W2.2 | Tornar RR≥1.0 universal (incluindo RANGE_BOUND mode) | M30 framework R5 | event_processor.py |
| W2.3 | Remover FMV fallback em position_monitor; bloquear entry se m30_fmv missing | M30 framework R2 ("ALWAYS=FMV") | position_monitor.py:1337 |
| W2.4 | Reforçar SL CONTINUATION: rejeitar signal (não só fallback SL) quando displacement não-ativo | Sprint9 spec | event_processor.py:3416 + 2860 |
| W2.5 | Adicionar daily loss limit (FIT_GAP P3) | ATS Risk Mgmt | event_processor.py |
| W2.6 | Forçar F-asym filter sempre ativo em prod (flag advisory) | ATS Strategic Plan #1 | event_processor.py:3771 |

**Gate W2**: Re-rodar testes 20/20 + 34/34 + smoke; 24h obs com brokers conectados; manual ATS doc review com Barbara.

---

### 🌊 Onda W3 — Refatoração estrutural (MED, ~8h)

Foco: reduzir complexidade. Quebrar god-object para tornar mudanças seguras.

| # | Ação | Arquivos | Notes |
|---|---|---|---|
| W3.1 | Extrair `decision_writer.py` singleton service do event_processor; centralizar TODOS writes a decision_live.json | NEW: live/decision_writer.py; event_processor.py, hedge_manager.py, position_monitor.py | Coloca lock no único writer por design |
| W3.2 | Remover ou re-arquitetar `d1_h4_updater.py` para conformar ADR-001 (deletar OR criar ADR-002 explícito) | d1_h4_updater.py + ADR doc | Decisão Barbara: matar shadow ou formalizar |
| W3.3 | Limpar config drift: deletar `cooldown_atr_mult`, `cooldown_pts`, `trend_resumption_threshold` (ou implementar) | config/settings.json | Pequena |
| W3.4 | Limpar dead code blocks identificados (5 itens em event_processor) | event_processor.py | Pequena |
| W3.5 | Avaliar split do event_processor em módulos separados: `gate_chain.py`, `tick_loop.py`, `state_writer.py`, `metrics.py` | event_processor.py → 4-5 modules | Big change, deve ser planejada cuidadosamente |

**Gate W3**: regression suite full PASS (incluindo testes que atualmente falham por collection errors); 48h obs.

---

### 🌊 Onda W4 — Methodology completion (LOW priority, ongoing)

Itens FIT_GAP pendentes da April 9 (5 ainda absent):
- MFE/MAE tracking + Giveback rule
- Daily loss limit (parte feita em W2.5)
- Daily range gate (20% bottom/top)
- Session close detection (30min block)
- Hard stop dynamic (% day range)
- Adaptive position sizing (1/3-1/5)
- Risk window analysis explícita

Cada item = mini-spec + impl + test, sequencial.

---

## 4. Quick wins prioritários (2h primeiras)

Se for para escolher SÓ 5 ações com maior bang-for-buck:

1. **W1.1**: lock em decision_live.json writes (1h) → resolve 72 file-lock errors imediatamente
2. **W1.2**: `MIN_SCORE_GO = 1` (15min) → elimina iceberg-only spam imediatamente
3. **W1.3**: dedup hash iceberg (45min) → adicional anti-spam
4. **W1.4**: afrouxar Opção B defaults (5min) → bias resolve mais frequente, F-asym ativo
5. **W2.4**: SL CONTINUATION rejeita signal quando displacement não-ativo (30min) → menos signals ruins de continuation

Total: ~3h. Sistema teria sangramento estancado.

---

## 5. Validação / gates de teste

Cada onda precisa passar:

| Gate | Critério | Fonte |
|---|---|---|
| Unit | 20/20 m30_bias_voting + 34/34 cascade + 9/9 anti-exit + ... | tests/ |
| Smoke | 1h running, 0 file-lock errors, 0 unhandled exceptions | service_stderr.log |
| Counterfactual | Replay BUG-SIGNAL-INVERTED window: ≥80% blocked OK ≥95% para Box 5282 | _audit/fixes/opcao_b_counterfactual_gates.py |
| Spam | 10 iceberg events em 30s no mesmo preço → ≤1 GO emitido | NEW test |
| ATR extreme | atr_regime=extreme + GO attempt → BLOCK | NEW test |
| Bias resolution | m30_bias resolve a bullish/bearish em ≥50% das decisões | live monitoring |
| Methodology | Manual review Barbara + ML-DS contra ATS Docs | comment thread |

---

## 6. Open questions para Barbara (decisão sua)

1. **Proceder com W1 imediatamente?** (~3-4h, sistema melhora visivelmente após)
2. **Manter Opção B com parâmetros relaxados OU rollback completo?** Recommendation: relaxar primeiro (W1.4); rollback (W1.5) só se relaxar não funcionar.
3. **D1/H4 shadow mode**: deletar `d1_h4_updater.py` OR criar ADR-002 que permite shadow? Recommendation: deletar, sem uso atual.
4. **MT5 brokers desconectados**: investigar reconexão **antes** de qualquer deploy adicional, ou paralelo? Reconexão é pré-requisito para signals fazerem sentido (sem broker = signals só Telegram).
5. **CONTINUATION strategy**: documentar com Barbara qual a intent original (Sprint 9), validar metodologia ATS, OR remover (não consta nos canonical docs).
6. **Telegram**: manter ON ou desligar até W1 fechar?

---

## 7. Constraints honored

- ✅ ZERO touch in `_detect_boxes` / `m30_updater.py` / `derive_h4_bias` / `_resolve_trend_direction` / F-asymmetric durante auditoria (read-only)
- ✅ ZERO action no BUG-M30-STUCK-VS-H4-FLIP backlog (orthogonal scope)
- ✅ ZERO push GitHub
- ✅ Sistema permanece OFF até decisão sua
- ✅ ZERO cross-project refs

---

## 8. Inventário de arquivos do audit

- `_audit/fixes/AUDITORIA_PROFUNDA_LIVE_2026-05-08.md` — **este arquivo**
- `_audit/fixes/REPORT_BUG-SIGNAL-INVERTED_2026-05-07_2026-05-08.md` — sumário 24h Opção B
- `_audit/fixes/opcao_b_24h_audit_report.md` — auditoria quantitativa decisões
- `_audit/fixes/opcao_b_counterfactual_gates.md` — G1+G2 results
- `_audit/calibrations/m30_bias_voting_calibration.md` — Phase 2 + sanity 6b/6c
- ATS Docs lidos: ADR-001, APEX_Stabilization_Plan, Strategy 1, Strategy 2, M30 Framework, Boxes, Liquidity Lines, Risk Management, FIT_GAP April 9

---

*Sistema permanece OFF. Aguardando decisão sua sobre Onda W1.*

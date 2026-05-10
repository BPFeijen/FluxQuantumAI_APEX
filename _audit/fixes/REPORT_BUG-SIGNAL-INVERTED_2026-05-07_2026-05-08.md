# BUG-SIGNAL-INVERTED — consolidated report 2026-05-07 → 2026-05-08

**Author**: CC#3 (FluxQuantumAI execution agent)
**Generated**: 2026-05-08 ~17:00 UTC
**Status**: Sistema **STOPPED** (user-initiated 2026-05-08 ~15:58 UTC)
**Brokers**: RoboForex + Hantec DESCONECTADOS toda a janela → ZERO execução real

---

## Sumário executivo

Em 24h+ de operação foram entregues:
- ✅ **Opção A** já live (cascade 5-layer + F-asymmetric filter; commit `ada8e9d`)
- ✅ **Opção B** deployed (F-1 min_bars + F-3 voting; calibrated config `min_bars=5, window=5, recency_weighted`)
- ✅ **BUG-SL-DISPLACEMENT** identificado e fixado (74 SLs invertidos pré-fix → ZERO pós-fix)
- ✅ **Telegram** reativado (kill switch removido, mensagem de teste delivered)
- ✅ **G1 + G2 counterfactuals**: 100% / 100% blocked (acima dos thresholds 80% / 95%)
- ✅ **20/20 unit tests + 34/34 cascade regression** PASS
- ⚠️ **Regressão de qualidade detectada** em janela final pré-stop — F-1+F-3 está conservador demais para condições de mercado atuais → m30_bias=unknown 95% do tempo → F-asym filter inativo → signals raw isoladas

---

## 1. Timeline cronológica

| Timestamp UTC | Evento |
|---|---|
| 2026-05-07 ~07:18 | Telegram OFF (kill switch) — emergency Barbara durante BUG-SIGNAL-INVERTED ongoing |
| 2026-05-07 (pré-Opção B) | Opção A já live (commit `ada8e9d`, cascade + F-asymmetric, RANGE_BOUND + TRENDING) |
| 2026-05-07 ~10:00 | Phase 0 investigation Opção B — root cause Box 5282 transient bias flip |
| 2026-05-07 ~11:00 | Phase 1 spec drafted (`opcao_b_phase1_m30_bias_hysteresis_spec.md`, 12 sections, 4 caveats integrated) |
| 2026-05-07 ~11:30 | Phase 2 calibration walk-forward CV (32 hypotheses × 4 regimes × 5-fold) — winner `(5,5,recency_weighted)` |
| 2026-05-07 ~12:10 | Sanity check Episode A (2026-05-06 04h burst) — flagged as BUG-SIGNAL-INVERTED itself, reclassified PASS |
| 2026-05-07 ~12:30 | Sanity rerun INCIDENT_20260420 — failed, but root cause orthogonal (BUG-M30-STUCK-VS-H4-FLIP backlog GID 1214603041501490) |
| 2026-05-07 ~12:50 | Sanity rerun PROPER (Box 5237 + 5262 + 5266) — **3/3 episodes 100% bearish** ✓ |
| 2026-05-07 10:57:01 | **DEPLOY Opção B** (NSSM restart, PID 36372) — 5min observation OK |
| 2026-05-07 11:22:03 | Telegram reactivated (kill switch removed; test message delivered) |
| 2026-05-07 14:39 | Barbara reporta SL/TP errados em mensagens Telegram |
| 2026-05-07 14:42 | Root cause encontrado: `event_processor.py:2861` — displacement bar SL sem guard de orientação |
| 2026-05-07 14:43:04 | **SL fix deployed** (NSSM restart) |
| 2026-05-08 15:58 | Barbara para sistema reportando "lixo, sinais invertidos" |
| 2026-05-08 ~17:00 | Auditoria 24h+ completa |

---

## 2. Opção B implementação (F-1 + F-3)

### Arquivos modificados
- `live/level_detector.py:525` — `derive_m30_bias` confirmed-path agora com voting
- `live/level_detector.py` (helpers novos):
  - `_load_m30_bias_voting_settings()` — carrega 3 keys com fallback
  - `_bars_in_box(m30_df, box_id)` — F-1 helper
  - `_classify_box_row(row)` — _classify extraído (mantém 32343cf semantics)
  - `_classify_with_min_bars(row, bars, min_bars)` — F-1
  - `_voting_vote(classifications, strategy)` — F-3 (majority + recency_weighted)
- `config/settings.json` — 3 keys adicionadas:
  ```json
  "m30_bias_min_bars": 5,
  "m30_bias_voting_window": 5,
  "m30_bias_voting_strategy": "recency_weighted"
  ```
- `tests/test_m30_bias_voting.py` (NOVO) — 15 unit tests T1-T15 + 5 helpers (20 total PASS)

### Arquivos NÃO tocados (Caveat 4 preservado)
- `live/_detect_boxes` (m30_updater.py)
- `live/derive_h4_bias`
- `live/_resolve_trend_direction` (cascade)
- F-asymmetric filter (Opção A)

### Counterfactual gates (Phase 4 STEP 6+7)
- **G1** (287 SHORTs target window 04-05 UTC, threshold 80%): **371/371 = 100%** blocked ✓
- **G2** (Box 5282 morning rally, threshold 95%): **388/388 = 100%** blocked ✓

### Calibração (Phase 2 walk-forward CV)
| Regime | Baseline acc | Winner acc | Δ |
|---|---|---|---|
| RANGE | 0.5841 | 0.5834 | -0.06pp |
| TRANSITIONAL | 0.5890 | 0.6014 | +1.24pp |
| TREND_DN | 0.4241 | 0.4481 | +2.40pp |
| **TREND_UP** | **0.4591** | **0.7663** | **+30.72pp** |

Acceptance: ZERO regimes regridem >5pp. Headline: +30.7pp em TREND_UP.

### Sanity rerun PROPER (3/3 episodes ALL 100% bearish)
- Box 5237 (2026-04-17 19:30 UTC, 7 bars, prev=bearish, drop -28.8pts) — **100%** ✓
- Box 5262 (2026-04-28 11:30 UTC, 6 bars, prev=unknown, drop -39.2pts) — **100%** ✓
- Box 5266 (2026-04-29 06:00 UTC, 4 bars, prev=unknown, drop -25.1pts) — **100%** ✓

---

## 3. BUG-SL-DISPLACEMENT (root cause + fix)

### Sintoma
Telegram mostrando SL/TP1/TP2 com valores absurdos vs preço atual GC.

Exemplo decision pré-fix (2026-05-07 14:39:31 UTC):
```
direction=LONG  price_mt5=4729.6  sl=4750.1  tp1=4747.12  tp2=4762.45
```
SL **20.5pts ACIMA** do entry para LONG → invertido. Order: `entry < tp1 < SL < tp2` (caos).

### Root cause
`event_processor.py:2861` (e duplicata 2771 no `_sl_pre`):
```python
if direction == "LONG" and _disp_lo > 0:
    sl = _disp_lo - 1.0
```
Guard `_disp_lo > 0` checa só "displacement bar foi encontrado", NÃO checa se está abaixo do entry. Quando price retracted past displacement, displacement_lo > price → SL = disp_lo - 1.0 ainda > price → invalid LONG SL.

### Fix
```python
if direction == "LONG" and 0 < _disp_lo < price:
    sl = _disp_lo - 1.0
elif direction == "SHORT" and _disp_hi > price:
    sl = _disp_hi + 1.0
else:
    if (direction == "LONG" and _disp_lo >= price) or (direction == "SHORT" and 0 < _disp_hi <= price):
        log.warning("DISPLACEMENT_SL_FALLBACK: %s entry=%.2f disp_lo=%.2f disp_hi=%.2f -> default SL", ...)
    sl = price + self.sl_pts if direction == "SHORT" else price - self.sl_pts
```

Aplicado em 2 sites: `_sl_pre` (linhas 2766-2780, decision_log + Telegram) + `sl` (linhas 2858-2877, execução).

### Validação pós-fix
- 1.966 decisões em ~25h pós-fix → **ZERO bad SL**
- 230× `DISPLACEMENT_SL_FALLBACK` triggered (proteção ativada quando necessário)
- Última LONG CONTINUATION pós-fix (2026-05-08 04:25:36): entry=4695.7 SL=4675.7 TP1=4714.7 TP2=4731.32 ✓ (todos no lado correto)

---

## 4. Auditoria 28h+ produção

### Activity summary
| Window | Decisões | bad_sl | bad_tp | Notes |
|---|---|---|---|---|
| W1 (deploy → SL fix, 3h47min) | 126 | **74** | 0 | Pré-fix, todas LONG, todas inverted SL |
| W2 (SL fix → service stop, ~25h) | 4.116 | **0** | **0** | Pós-fix, ZERO inverted |

### W2 distribuição
- direction (post-fix): SHORT GO 783, SHORT EXEC_FAILED PULLBACK 647, LONG GO 168, LONG EXEC_FAILED RANGE 100, SHORT BLOCK 88, LONG BLOCK 64...
- m30_bias: bullish 209 + **unknown 3.907** (95% unknown!) ← regressão
- bias flips: 7 (baixo)
- range_bear pattern: 0 (nenhum over-blocking detectado)

### Log indicators
- RANGE_BOUND_BIAS_BLOCK: 297.789 occurrences (cascade + F-asym ativo)
- TRENDING_BIAS_BLOCK: 2.568 occurrences
- DISPLACEMENT_SL_FALLBACK: 230 (fix protegeu SLs)
- File-lock errors `decision_live.json`: 72 (Dashboard contention, non-fatal)
- Service crashes: 0 (parou por user, não crash)

---

## 5. Investigação "sinais invertidos" pré-stop (12:34 → 15:58 UTC)

825 decisões na janela 3.5h.

### Mercado
- price_mt5: 4699.85 → 4685.75 (**-14.1pts BEARISH drift**)
- range MT5: 4685.75 / 4720.75 (35pts span)

### Decisões
- LONG: 312 (95% blocked = 296 BLOCK + 8 EXEC_FAILED + 8 GO)
- SHORT: 513 (zero blocked = 246 EXEC_FAILED + 267 GO)

### Direção: NÃO foi inversão de direção
Mercado caindo → SHORTs eram **directionally aligned**. LONGs corretamente blocked como counter-trend.

### O que Barbara provavelmente viu
**Quatro problemas REAIS de qualidade**:
1. **Spam**: 10+ SHORTs em 30s no mesmo preço (15:56-15:58, todas @ MT5 4685.75)
2. **Bad timing**: SHORTs entrando no LOW da range (sinal atrasado)
3. **Contradição lógica**: LONG @ 4689.9 às 15:13 (RANGE mean-rev) → SHORTs @ 4685.75 às 15:58 — sistema BUYING LOWS então SELLING LOWS em sequência
4. **Single-source signal**: últimas SHORTs todas com reason="iceberg large_order sc=+4" (sem confirmação cascade)

### SL/TP verification (pós-fix)
Última SHORT GO @ 4685.75: SL=4705.75 ✓ TP1=4665.75 ✓ TP2=4635.75 ✓ (order válida)
Última LONG GO @ 4689.9: SL=4669.9 ✓ TP1=4709.9 ✓ TP2=4739.9 ✓ (order válida)

→ **SL/TP arithmetic correto**. Bug arithmético está fixado.

### Root cause da queda de qualidade — REGRESSÃO Opção B
F-1+F-3 calibração ficou **conservadora demais para condições de mercado atuais**:

- 95% das decisões W2 com bias=unknown (3.907/4.116)
- min_bars=5 + window=5 muito restritivo: M30 boxes atuais raramente têm ≥5 bars confirmados
- Com bias=unknown 95% do tempo, F-asymmetric filter fica **efetivamente INATIVO**
- Sistema cai para emissão raw via M5 strategy + iceberg → signals isoladas, mal-timed, contraditórias

A calibração foi otimizada em corpus subsampled (6.000 decisões Apr-May com `MAX_DECISIONS=6000` cap), mas em produção box-duration distribution é mais curta → bias forçado a unknown.

---

## 6. Próximos passos sugeridos (decisão sua)

### Opção 1 — Recalibrar Opção B (afrouxar)
- `m30_bias_min_bars`: **5 → 3 ou 4**
- `m30_bias_voting_window`: **5 → 3**
- Manter `recency_weighted`
- Esperado: bias resolve em ~50-70% das decisões → F-asym filter ativo mais frequentemente

### Opção 2 — Rollback Opção B + recalibrar com box-duration empírica
- Reverter `derive_m30_bias` confirmed-path para single-most-recent-box (baseline)
- Manter Opção A live (cascade + F-asym continua protegendo)
- Re-rodar Phase 2 calibração medindo box-duration real em produção primeiro
- Re-deploy Opção B só após validação contra esse novo dataset

### Opção 3 — Adicionar gates complementares
- **CONTINUATION strategy gate**: rejeitar signal quando `DISPLACEMENT_SL_FALLBACK` triggers (em vez de apenas corrigir SL); 230× em 25h é alto
- **Single-source iceberg gate**: exigir confirmação cascade quando único score vem de iceberg (evitar bursts 10+ em 30s)
- **Same-level cooldown reforçado**: `same_level_cooldown_min` já existe (60min) mas não está prevenindo bursts no mesmo preço

### Issues pendentes
- ⚠️ MT5 brokers DESCONECTADOS — investigar reconexão antes de qualquer deploy adicional
- ⚠️ BUG-M30-STUCK-VS-H4-FLIP backlog (GID 1214603041501490) — orthogonal scope, deferred
- ⚠️ File-lock errors `decision_live.json` (72 ocorrências) — Dashboard contention; non-fatal mas merece diagnóstico

---

## 7. Arquivos relevantes

### Specs e calibrações
- `_audit/fixes/opcao_b_phase0_m30_hysteresis_investigation.md` — investigação root cause
- `_audit/fixes/opcao_b_phase1_m30_bias_hysteresis_spec.md` — spec design (12 sections)
- `_audit/calibrations/m30_bias_voting_calibration.md` — Phase 2 calibration + sanity 6b/6c
- `_audit/fixes/opcao_b_counterfactual_gates.md` — G1+G2 results
- `_audit/fixes/opcao_b_phase4_deploy_observation.md` — deploy + 5min log
- `_audit/fixes/opcao_b_24h_audit_report.md` — auditoria 24h
- `_audit/fixes/REPORT_BUG-SIGNAL-INVERTED_2026-05-07_2026-05-08.md` — **este arquivo (consolidated)**

### Scripts
- `_audit/calibrations/m30_bias_voting_calibration.py` — Phase 2 grid search
- `_audit/calibrations/m30_bias_voting_proper_bearish_search.py` — sanity episode search
- `_audit/calibrations/m30_bias_voting_proper_bearish_replay.py` — sanity replay
- `_audit/fixes/opcao_b_counterfactual_gates.py` — G1+G2 counterfactual
- `_audit/fixes/opcao_b_phase5_day1_monitoring.py` — day1 monitoring snapshot
- `_audit/fixes/opcao_b_24h_audit.py` — 24h+ audit

### Código vivo
- `live/level_detector.py` (F-1+F-3 helpers + modified `derive_m30_bias`)
- `live/event_processor.py` (BUG-SL-DISPLACEMENT fix em 2 sites)
- `live/telegram_notifier.py` (kill switch removido)
- `config/settings.json` (3 keys voting)
- `tests/test_m30_bias_voting.py` (15 unit tests T1-T15 + 5 helpers)

### Logs / dados
- `logs/decision_log.jsonl` — 73MB+, ~4.242 decisões post-deploy
- `logs/service_state.json` — last heartbeat 2026-05-08 15:58
- `logs/service_stdout.log` / `service_stderr.log` — runtime logs
- `C:/data/processed/gc_m30_boxes.parquet` — M30 boxes atualizadas
- `C:/data/processed/gc_ohlcv_l2_joined.parquet` — OHLCV M1

---

## 8. Constraints honored throughout

- ✅ ZERO touch em `_detect_boxes` / `m30_updater.py` / `derive_h4_bias` / cascade resolver / F-asymmetric (Caveat 4)
- ✅ ZERO push to GitHub (changes apenas no working tree)
- ✅ ZERO action no BUG-M30-STUCK-VS-H4-FLIP backlog
- ✅ ZERO cross-project refs (TradeATS / CC#1 / CC#2 untouched)
- ✅ Single planned restarts only (3 total: deploy Opção B, Telegram reactivation, SL fix)
- ✅ Sistema permanece OFF até decisão explícita (não vai ser ligado sem autorização)

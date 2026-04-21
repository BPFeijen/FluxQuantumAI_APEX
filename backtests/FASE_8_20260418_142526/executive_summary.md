# APEX Backtest Diagnostic — Executive Summary

**Período analisado:** 2026-04-01 → 2026-04-10 (10 dias, slice de cross-validação)
**Configuração:** APEX actual pós-Fase 7 (produção deployed 2026-04-18)
**Conta simulada:** RoboForex demo 68302120
**Engine:** Minimal viable (usa `ATSLiveGate.check()` da produção LIVE; excerto de scope)

---

## ⚠ Scope limitation (crítico)

O spec pediu **Jul 2025 → Abr 2026 (10 meses)**. Esta execução cobre **apenas 10 dias** como **viable proof-of-concept** porque:

1. **Engine novo construído** (não havia infraestrutura prévia). Faithful replay das dependências threaded/MT5 do `event_processor.py` (4372 LOC, 3 classes, monitor threads, file watchers) requer sprint dedicado multi-dia.
2. **Módulos opcionais indisponíveis em env:** ApexNewsGate, StatGuardrail (detectors), V4 IcebergInference (ats_iceberg_v1), Grenadier DefenseMode. Produção corre com estes também OFF (consistente).
3. **Features não implementadas** (out-of-scope deste engine minimal): GAMMA/DELTA triggers (ghost per Fase 2.5b), L2 danger exit, regime flip exit, trailing stop pós-SHIELD, news gate, hedge manager.

**Para atingir scope completo**, propõe-se sprint dedicado (estimate: 1-2 dias de engine dev). Esta primeira passada valida viabilidade e dá findings accionáveis sobre comportamento de ALPHA no período analisado.

---

## Key findings

### 1. PnL global no período — **NEGATIVO**

| Metric | Valor |
|---|---|
| Total trades | 102 |
| Winners | 39 (38.2%) |
| Losers | 63 (61.8%) |
| **Total PnL** | **-$717.55** |
| Profit factor | **0.29** (losses 3.4× ganhos) |
| Avg win | +$7.55 |
| Avg loss | **-$16.06** |

**Critical imbalance:** avg loss é **2.1× avg win**. Combinação de 62% losing rate + losses maiores = PF < 0.5. Barbara quer >2.0.

### 2. Performance por sessão — **Asian é a única positiva**

| Sessão | Trades | PnL | WR | Avg PnL | MFE/MAE |
|---|---|---|---|---|---|
| **Asian**  | 31 | **+$44**   | **77%** | +$1.42  | 27/11 pts (excelente) |
| **London** | 42 | **-$486**  | 17%     | -$11.58 | 14/22 pts (ruim) |
| **NY**     | 29 | **-$275**  | 28%     | -$9.48  | 12/16 pts (ruim) |

**Interpretação:**
- **Asian domina MFE/MAE** (MFE 27pts > MAE 11pts) — mercado tende a correr para favor após entry.
- **London inverte** (MAE 22 > MFE 14) — losses dominam. Entries prematuros ou contra trend.
- **NY similar a London.**

Barbara's historical concern about London confirmed — pior sessão para ALPHA neste período.

### 3. Direction × Session — **LONG London e SHORT NY são desastres**

| Bucket | Trades | PnL | WR |
|---|---|---|---|
| LONG Asian  | 11 | +$63 | **91%** |
| LONG London | 25 | **-$343** | **4%** |
| LONG NY     | 12 | +$38 | 67% |
| SHORT Asian | 20 | -$19 | 70% |
| SHORT London| 17 | -$143 | 35% |
| **SHORT NY** | 17 | **-$313** | **0%** |

**LONG London WR=4%** e **SHORT NY WR=0%** em 42 trades combinados = **-$656 dos -$717 totais** (91% das perdas).

### 4. Exit breakdown — **62% dos trades morrem em SL_ALL**

| Exit category | Count | PnL |
|---|---|---|
| **SL_ALL (leg1 SL→all SL)** | 63 | **-$1,012** |
| TP1_then_SHIELD_stopped | 25 | +$102 |
| TP1_then_window_end     | 8  | +$101 |
| TP1_TP2_leg2_break      | 3  | +$64 |
| window_end_no_tp1       | 3  | +$27 |

Key insight: **SHIELD está a fazer o trabalho** (+$102 em 25 trades onde leg1 fez TP1 mas leg2/3 depois parariam). **Sem SHIELD, perdas seriam piores**.

### 5. Iceberg signal — **NÃO é predictivo neste período**

- 89/102 trades tiveram `iceberg_aligned=True` (87%)
- Destes 89: **WR 33.7%, PnL -$709**
- Iceberg alignment não está a ajudar a discriminar setups ganhadores.

### 6. Padrão crítico: **repetição do mesmo setup em minutos**

Das 10 maiores perdas:
- 6 trades em **Abr 1, 14:00-14:36 UTC**: SHORT @ 4763.95 repetido 6× nos mesmos 36 min. Todos SL. Total -$110.
- 4 trades em **Abr 2, 16:30-17:40 UTC**: SHORT @ 4681.35 repetido 4× em 70 min. Todos SL. Total -$74.

**Stop-hunt pattern:** mesmo preço estrutural testa várias vezes, falha várias vezes. Dedup actual (5min buckets) não impede re-trigger após bucket advance.

### 7. Cross-validation com trades.csv real

- Trades reais no período: **36**
- Trades backtest no período: **102**
- **Ratio: 2.8× mais agressivo que live**

Diferença vem de:
- News gate OFF no backtest (live tem)
- Cooldowns de operational_rules não totalmente respeitados
- Possivelmente max_positions + hedge logic
- Eventual cooldown "same_level_proximity" mais restritivo na live

---

## Recommendations (prioritized por impacto PnL estimado no período)

### HIGH priority

1. **Bloquear LONG London** (ou forçar SHIELD=entry desde início): 4% WR em 25 trades = −$343. **Estimated recovery: +$300/10d** se bloqueada.
2. **Bloquear SHORT NY 14:00-18:00 UTC** (impulsion window após NY open): 0% WR em 17 trades. **Estimated recovery: +$250/10d**.
3. **Implement re-entry cooldown por LEVEL** (não só por tempo): 10 trades repetidos no mesmo nível estrutural = -$184. Já há `same_level_cooldown_min` em settings — verificar se ativo.

### MEDIUM priority

4. **Revisar iceberg_proxy_threshold**: iceberg_aligned=TRUE tem mesmo WR que o universo (33% vs 38%). Sinal não discriminante no período. Possível recalibração.
5. **TP1:SL asymmetry**: 20:20 pts nominal; com slippage torna-se 17:23. Considerar TP1=25pts ou SL=18pts para rebalancear R:R.

### LOW priority

6. **Engine extension**: implementar news gate, L2 danger, regime flip e trailing. Pode reduzir a 2.8× ratio para ~1.0× e aproximar da produção real.

---

## Limitations desta análise (caveats)

1. **Slice de 10 dias** — não representativo de 10 meses. Padrões sazonais/macro omitidos.
2. **Engine minimal** — 6 features não implementadas (ver lista acima). Backtest é **upper bound de perdas** (sem exit filters de proteção).
3. **Slippage é aproximação** — Assumido 2pts entry / 3pts SL. Real broker pode diferir.
4. **Market-data completeness** — 89% das rows têm L2 features NULL (feed L2 só ativo ~11% do tempo). Sinais V2/V4 bloqueiam ou retornam UNKNOWN em 89% do tempo.
5. **Zero real trades referência**: live teve 36 trades no período; backtest teve 102. Difícil afirmar exactidão do engine até ratio convergir para 1:1.

---

## Next steps propostos

1. **Decisão Barbara** — aceitar scope reduzido (10d) ou commit sprint dedicado (10-mês full)?
2. **Se scope reduzido suficiente:** implementar as 3 HIGH recommendations no settings.json + testar em paper por 1 semana.
3. **Se full scope desejado:** planear sprint para:
   - Port do event_processor main loop para modo offline (replay L2 files + microstructure CSV)
   - Port de news_gate com mock de eventos (TradingEconomics archive)
   - Port de L2 danger + regime flip exits
   - Run over full Jul 2025 → Apr 2026 com forensic per-loss
   - Estimate: 2-3 dias de engineering focado

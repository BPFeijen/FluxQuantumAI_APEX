# Backtest FAIL — 5 Hipóteses para Iteração

**Author**: CC#3
**Date**: 2026-05-06
**Asana**: 1214556369070092 — comment GID `1214587338987931`
**Type**: Hypothesis register (decision pending ML-DS)

---

## Resumo

Counterfactual replay 04:44 burst falhou hard gate.

| Métrica | Valor |
|---------|-------|
| Threshold acceptance | ≥80% |
| Resultado | **73.4%** ❌ |
| 04:44 burst SHORTs (entries window 2026-05-06T04:09Z..04:51Z) | 218 |
| Blocked under new cascade | 160 |
| Não bloqueadas | 58 (26.6%) |

Layer source distribution durante burst:
- `provisional_m30_bias`: 154 entries
- `tick_breakout_monitor`: 64 entries
- (cobertura 100% das 218 — todas tinham layer source, mas Layer source dava SHORT em 58 casos)

---

## Root cause (provável)

Layer 2 reconstruction (do TickBreakoutMonitor state) é **too aggressive** em shallow pullbacks:

- Live state machine tem JAC-gated hysteresis ~30s — evita flips em pullbacks rasos
- Reconstruction (no backtest) só olha price vs m5_liq_top/m5_liq_bot com buffer 0.5pts — qualquer dip abaixo do box em 1 tick gera "BREAKOUT_DN" sintético
- Em rally bullish há shallow pullbacks rápidos onde price toca brevemente abaixo do box
- Esses momentos viram BREAKOUT_DN no replay → resolved=short → Layer 2 disagrees com provisional bullish → SHORT@liq_top NÃO bloqueada

---

## Walk-forward CV (5-fold)

| Fold | n | Reduction |
|------|---|-----------|
| 1 (04-14 → 04-20) | 1881 | 6.3% |
| 2 (04-20 → 04-23) | 1881 | 16.2% |
| 3 (04-23) | 1881 | 58.4% |
| 4 (04-23 → 04-24) | 1881 | 20.1% |
| 5 (04-24 → 05-06) | 1882 | 23.0% |

Min 6.3% — max 58.4%. Não uniforme. Folds 1-4 dominados por `daily_trend_b_c_ensemble` (Layer 1) — 100% das entries — porque pre-401e6da deploy daily_trend ainda era "long" stale (já alinhado). Fold 5 é único com cascade activado (mixed layers), refletindo era post-401e6da.

---

## 5 Hipóteses para iteração

### Hipótese A — Tighten Layer 2 reconstruction buffer

**O quê**: Aumentar o buffer de 0.5pts (current) para 1-2pts ATR-scaled no `reconstruct_tick_breakout_state()`.

**Razão**: Live JAC timer (~30s hysteresis) evita BREAKOUT flips em shallow pullbacks; reconstruction em backtest precisa de proxy que simule essa hysteresis.

**Implementação**: alterar `_audit/fixes/backtest_cascade_counterfactual.py` linha ~110:
```python
# Antes:
if price > m5_liq_top + 0.5: return "BREAKOUT_UP"
if price < m5_liq_bot - 0.5: return "BREAKOUT_DN"

# Depois (ATR-scaled):
buffer = max(2.0, atr14 * 0.3)
if price > m5_liq_top + buffer: return "BREAKOUT_UP"
if price < m5_liq_bot - buffer: return "BREAKOUT_DN"
```

**Impacto esperado**: shallow pullbacks já não classificam BREAKOUT_DN → mais entries vão para Layer 4 (provisional bullish) → mais blocks → expected ≥80%.

**Risco**: ZERO em produção (mudança apenas no script backtest). Não toca código live.

**ETA**: 5min edit + 5min re-run.

**Pro**: Simples, isolado ao backtest, código live intacto.
**Contra**: Pode mascarar comportamento real do live (que SE provavelmente é fiel ao código live com JAC).

---

### Hipótese B — Multi-layer voting (Layer 2 vs Layer 3/4)

**O quê**: Adicionar regra: se Layer 2 dá SHORT mas Layer 3 (m30_confirmed) E Layer 4 (provisional) ambos LONG → take majority (LONG).

**Razão**: Layer 2 BREAKOUT é HIGH confidence individualmente, mas se ambas as outras camadas discordam fortemente, voting overrides.

**Implementação**: alterar `_resolve_trend_direction()` em `live/event_processor.py`:
```python
# Após Layer 2 retorna candidate, antes de retornar:
candidate_l2 = (trend, "tick_breakout_monitor", "HIGH")
# Verifica veto: se Layer 3+4 ambos discordam, descarta Layer 2
m30_check = ...
prov_check = ...
if both contradict candidate_l2 → fall through to Layer 3
else → return candidate_l2
```

**Impacto esperado**: Em shallow pullbacks contra bull rally, m30+provisional ambos bullish → veto SHORT do Layer 2 → resolved=long.

**Risco**: Médio — adds complexity ao resolver; testes adicionais necessários.

**ETA**: 30min edit + 30min testes + 10min backtest.

**Pro**: Melhora robustez logic-level; também aplica em produção (não só backtest).
**Contra**: Adiciona ~15 LoC de complexity; semantics layer ordering mais difícil de raciocinar; mais tests necessários.

---

### Hipótese C — Re-design Layer 2 com simulação JAC explícita

**O quê**: Reconstruction simula JAC timer: requer N segundos consecutivos fora do box antes de classificar BREAKOUT.

**Razão**: Replica fielmente o que o live state machine faz.

**Implementação**: tracking estado entre samples no script backtest, requeira `tick_count_outside_box >= 30` (proxy 30s) para ativar BREAKOUT.

**Risco**: ZERO em produção (só backtest).

**ETA**: 30-45min edit (state tracking entre rows).

**Pro**: Reconstrução mais fiel; mais defensible methodologicamente.
**Contra**: Mais código complexo no backtest; difícil de testar a própria reconstrução.

---

### Hipótese D — Lower acceptance threshold 73.4% → 70%

**O quê**: Waiver do gate 80% para 70%.

**Razão**: 73.4% é substancial improvement vs 0% atual.

**Implementação**: NÃO requer mudança código.

**Risco**: ALTO — relaxa rigor empírico; threshold arbitrário não baseado em dados.

**Pro**: Imediato.
**Contra**: Viola spec § 8.3 acceptance gate; abre precedente de relaxar acceptance ad-hoc; ML-DS rejeitou padrão similar antes ("don't move goalposts").

---

### Hipótese E — Deploy partial (waiver) + monitoring intenso

**O quê**: Aceitar 73.4% como "best-effort improvement" + deploy com observação reforçada (alert if SHORT volume durante bullish bias > X/min).

**Razão**: 73.4% bloqueia 3/4 dos signals invertidos. Substantial em advisory mode (zero financial impact). Resto pode ser caught manualmente.

**Implementação**: Add monitoring task em deploy.

**Risco**: MÉDIO — Barbara ainda vê alguns SHORTs invertidos no dashboard durante bull rallies, mas em volume reduzido. Pode minar confiança.

**Pro**: Move forward com fix robusto não-perfeito.
**Contra**: Violates hard gate per ML-DS directive ("If any FAIL → STOP"); requires waiver from Barbara/ML-DS.

---

## Recomendação CC#3

🥇 **Hipótese A (tighten reconstruction)** primeiro — 10min total, ZERO risk produção, mais provável recovery to ≥80% sem mudar código live.

Se A não atinge 80% → 🥈 **Hipótese B (multi-layer voting)** — 70min total, pequena melhora robustez logic-level, mais defensible methodologically.

🥉 Hipótese C — apenas se A+B falharem (signals que reconstrução não capta).

⛔ Hipóteses D, E — apenas com explicit Barbara waiver.

---

## State preserved

- Branch `fix/cascade-tick-breakout-provisional-2026-05-06` exists
- ZERO commit
- ZERO NSSM restart
- Files modified mas uncommitted (live/event_processor.py +51, config/settings.json +2 keys, tests/test_strategy_mode_cascade.py NEW 23 tests pass)
- Rollback trivial: `git checkout deploy/cluster-3-2026-05-06`

---

## Decisão pendente — ML-DS

Q1-Q5 do comment Asana 1214587338987931 — qual hipótese implementar?

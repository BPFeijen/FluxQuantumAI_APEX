# CURRENT_LIVE_CODE_AUDIT

## Scope and method
- Audit based on **current code in this repository** (`run_live.py` + `live/*.py`) and direct code inspection.
- No architecture redesign in this document.
- **No assumption** that FluxQuantumAPEX scaffolds (`master_engine.py`, `position_manager.py`, `config.py`, `types.py`) are runtime truth.
- If mentioned: those are **SCAFFOLD_ONLY / NÃO-LIVE** unless explicitly wired from current runtime entrypoints.

---

## Runtime entrypoints (real)

### 1) `run_live.py`
- **Path:** `run_live.py`
- **Role:** Main orchestrator for live execution mode (`--execute`) and dry-run mode.
- **Live path status:** `IN_LIVE_PATH`
- **Why:** Instantiates feed monitor, updaters, `EventProcessor`, and `PositionMonitor`; calls `processor.start()`.

### 2) `live/dashboard_server.py` and `live/dashboard_server_hantec.py`
- **Role:** HTTP dashboards; API surface for status/trades/live decision payload.
- **Live path status:** `IN_LIVE_PATH` (ops/observability path, not decision core).

### 3) `live/base_dashboard_server.py`
- **Role:** Shared backend for dashboards (status/equity/trades + canonical `/api/live`, `/api/system_health`, `/api/executions`, `/api/pm_events`).
- **Live path status:** `IN_LIVE_PATH` (observability backend).

---

## File-by-file audit

## `live/event_processor.py`
1. **Path real:** `live/event_processor.py`
2. **Papel real:** Decisor/Executor central do live (Layer 2), gatilho por proximidade de nível + eventos iceberg/tick.
3. **No caminho live atual?:** `YES`
4. **Principais classes/funções:** `EventProcessor`, `_trigger_gate`, `_open_on_all_accounts`, `_build_decision_dict`, `refresh_macro_context`, `start`.
5. **Inputs consumidos:**
   - parquet: `gc_m5_boxes.parquet`, `gc_m30_boxes.parquet`, `gc_ats_features_v4.parquet`
   - micro/iceberg files (`C:/data/level2/_gc_xcec`, `C:/data/iceberg`)
   - MT5 ticks/account state
   - `service_state.json`, `settings.json`
6. **Outputs emitidos:**
   - `decision_live.json`, `decision_log.jsonl`, `service_state.json`, `continuation_trades.jsonl`
   - ordens via `MT5Executor` / `MT5ExecutorHantec`
   - Telegram via `telegram_notifier`
7. **Dependências importadas:** `ATSLiveGate`, `MT5Executor`, `OperationalRules`, `TickBreakoutMonitor`, `PriceSpeedTracker`, `derive_m30_bias`, módulos opcionais news/anomaly/rl.
8. **Quem chama:** `run_live.py` (_run_event_driven).
9. **Acoplamentos perigosos:**
   - Imports opcionais externos fora de `live/` (news/anomaly/guardrails) por path hardcoded.
   - Mistura decisão+execução+telemetria no mesmo módulo.
10. **Contém lógica de:**
   - contexto ✅
   - execução ✅
   - veto ✅
   - risco ✅
   - monitoramento ✅ (service heartbeat)
   - atualização de dados ✅ (macro refresh)
   - ML/rule-based ✅ (RL hooks opcionais + rule-based ativo)
11. **Reaproveitar no APEX_V2:** ciclo de decisão canônica + execução multi-broker + payload canônico.
12. **Não mover sem refactor:** `_trigger_gate`, `_open_on_all_accounts`, `_tick_loop` (alto acoplamento).
13. **Duplicado/legado/morto:** blocos shadow/anomaly forge desativado, vários caminhos opcionais legacy.
14. **Mistura indevida:** decisor + executor + telemetry writer + integração externa no mesmo arquivo.
- **Readiness label:** `SPLIT_BY_RESPONSIBILITY`

---

## `live/level_detector.py`
1. **Path:** `live/level_detector.py`
2. **Papel:** Detector de níveis M5 (execução), contexto M30 e direção diária.
3. **No caminho live atual?:** `YES`
4. **Principais funções:** `get_current_levels`, `derive_m30_bias`, `_get_daily_trend`, `_validate_m5_vs_m30`.
5. **Inputs:** parquets M5/M30/features + microstructure para preço GC atual.
6. **Outputs:** dict de níveis/contexto (`liq_top/bot`, box, ATR, `daily_trend`, `m30_bias`, `m30_bias_confirmed`, `provisional_m30_bias`).
7. **Dependências:** pandas/path/os/logging.
8. **Quem chama:** `run_live.py` (startup + refresh), `position_monitor.py` (bias derivation shared), `event_processor.py` (derive helper).
9. **Acoplamentos perigosos:** depende de paths fixos windows; fallbacks múltiplos em um só lugar.
10. **Lógica:**
   - contexto ✅
   - execução ❌
   - veto ⚠️ indireto (fornece bias usado em veto)
   - risco ❌
   - monitoramento ❌
   - atualização dados ❌ (consome, não atualiza)
   - ML/rule-based ❌ (rule heurístico de boxes)
11. **Reaproveitar:** contrato de `get_current_levels`.
12. **Não mover sem refactor:** função monolítica com múltiplos fallbacks.
13. **Duplicado/legado/morto:** `_get_m30_bias` wrapper sobre `derive_m30_bias` (quase redundante).
14. **Mistura indevida:** cálculo de contexto + regras de staleness/telemetry no mesmo bloco.
- **Readiness label:** `WRAP_AS_PROVIDER`

---

## `live/operational_rules.py`
1. **Path:** `live/operational_rules.py`
2. **Papel:** Regras pré-gate operacionais (capacidade, margem, dedup).
3. **No caminho live atual?:** `YES`
4. **Principais funções:** `OperationalRules.check_can_enter`.
5. **Inputs:** posições abertas, margin level, direção/preço sinal, trades existentes.
6. **Outputs:** `(blocked, reason)`.
7. **Dependências:** logging/typing.
8. **Quem chama:** `event_processor._trigger_gate`.
9. **Acoplamentos perigosos:** parâmetros calibráveis misturados com hardcoded.
10. **Lógica:** veto ✅ risco ✅ contexto ❌ execução ❌
11. **Reaproveitar:** como provider de guardrails operacionais.
12. **Não mover sem refactor:** interface atual fortemente acoplada ao formato de open_positions/trades.
13. **Duplicado/legado/morto:** checks TBD fail-open podem ficar “semi-mortos”.
14. **Mistura indevida:** regras calibradas e não calibradas na mesma classe.
- **Readiness label:** `WRAP_AS_PROVIDER`

---

## `live/position_monitor.py`
1. **Path:** `live/position_monitor.py`
2. **Papel:** Gestão pós-entrada (Layer 4): SHIELD, exits (L2_DANGER/REGIME_FLIP/CASCADE/T3/PULLBACK_END/V3), trailing, hedge orchestration.
3. **No caminho live atual?:** `YES`
4. **Principais classes/funções:** `PositionMonitor`, `_run_checks`, `_check_*`, `_close_ticket`, `_emit_position_event`, `_publish_canonical_pm_event`.
5. **Inputs:** posições MT5 abertas, microstructure, M30 parquet, `service_state.json`, `settings.json`, trades csv.
6. **Outputs:**
   - ações MT5 (`close_position`, `_modify_sl`, `move_to_breakeven`)
   - logs locais (`position_decisions.log`, `position_events.jsonl`)
   - **canônico** (`decision_live.json`, `decision_log.jsonl`) via PM_EVENT
7. **Dependências:** `MT5Executor`, `HedgeManager`, `derive_m30_bias`, pandas.
8. **Quem chama:** instanciado por `run_live.py`.
9. **Acoplamentos perigosos:**
   - muito estado interno por ticket/grupo
   - mistura monitor/risk/exec/telemetry num único módulo
10. **Lógica:**
   - contexto ✅
   - execução ✅
   - veto/risk ✅
   - monitoramento ✅
   - atualização dados ❌
   - ML/rule-based ✅ (V3 opcional + rule-based principal)
11. **Reaproveitar:** taxonomia de eventos PM + gestão de exits já testada.
12. **Não mover sem refactor:** `_run_checks` e `_check_*` em cascata (acoplamento de ordem).
13. **Duplicado/legado/morto:** múltiplos hooks V3/legacy coexistindo.
14. **Mistura indevida:** decisão de saída + execução + escrita canônica + logging local.
- **Readiness label:** `SPLIT_BY_RESPONSIBILITY`

---

## `live/m5_updater.py`
1. **Path:** `live/m5_updater.py`
2. **Papel:** Rebuild periódico de `gc_m5_boxes.parquet` (timeframe execução).
3. **No caminho live atual?:** `YES`
4. **Funções:** `run_update`, `start`, `_detect_boxes`, `_write_atomic`.
5. **Inputs:** `gc_ohlcv_l2_joined.parquet` + microstructure do dia.
6. **Outputs:** `gc_m5_boxes.parquet`.
7. **Dependências:** pandas/numpy/threading/path.
8. **Quem chama:** `run_live.py`.
9. **Acoplamentos perigosos:** paths fixos e estratégia de rebuild completo.
10. **Lógica:** atualização de dados ✅ contexto ✅
11. **Reaproveitar:** pipeline de build M1->M5 + atomic write.
12. **Não mover sem refactor:** state machine de boxes junto com I/O.
13. **Duplicado/legado:** sobreposição funcional com m30_updater (pipeline similar).
14. **Mistura indevida:** ETL + modelagem estrutural no mesmo módulo.
- **Readiness label:** `SPLIT_BY_RESPONSIBILITY`

---

## `live/m30_updater.py`
1. **Path:** `live/m30_updater.py`
2. **Papel:** Rebuild de `gc_m30_boxes.parquet` (macro bias/TP2).
3. **No caminho live atual?:** `YES`
4. **Funções:** `run_update`, `start`, `_detect_boxes`.
5. **Inputs:** M1 parquet + micro files faltantes.
6. **Outputs:** `gc_m30_boxes.parquet` (e persistência M1 atualizada).
7. **Dependências:** pandas/numpy/threading/path.
8. **Quem chama:** `run_live.py`.
9. **Acoplamentos perigosos:** sincronização com M1 persisted + freshness decisions.
10. **Lógica:** atualização dados ✅ contexto ✅
11. **Reaproveitar:** pipeline incremental + atomic writer.
12. **Não mover sem refactor:** detecção estrutural e I/O juntos.
13. **Duplicado:** base semelhante ao m5_updater.
14. **Mistura indevida:** ingest + structural detection + persistence management.
- **Readiness label:** `SPLIT_BY_RESPONSIBILITY`

---

## `live/tick_breakout_monitor.py`
1. **Path:** `live/tick_breakout_monitor.py`
2. **Papel:** monitor tick-level para breakout/JAC e injeção imediata de níveis no processor.
3. **No caminho live atual?:** `YES`
4. **Funções/classes:** `TickBreakoutMonitor`, `_refresh_from_parquet`, `_inject_levels`, `run`, `start`.
5. **Inputs:** GC mid do processor + M30 parquet.
6. **Outputs:** atualiza `proc.liq_top/bot` e solicita `request_macro_context_refresh`.
7. **Dependências:** pandas/threading/time/path.
8. **Quem chama:** criado em `EventProcessor.__init__`, iniciado em `EventProcessor.start()`.
9. **Acoplamentos perigosos:** muta estado interno de outro módulo (`EventProcessor`) diretamente.
10. **Lógica:** contexto ✅ monitoramento ✅ atualização dados ⚠️ indireta
11. **Reaproveitar:** state machine de breakout com debounce/confirm.
12. **Não mover sem refactor:** `_inject_levels` (acoplamento bidirecional com processor).
13. **Duplicado/legado:** possível overlap com refresh de níveis do run_live.
14. **Mistura indevida:** detector de evento + writer de estado em outro componente.
- **Readiness label:** `WRAP_AS_PROVIDER`

---

## `live/feed_health.py`
1. **Path:** `live/feed_health.py`
2. **Papel:** health monitor de microstructure feed (OK/STALE/FEED_DEAD).
3. **No caminho live atual?:** `YES`
4. **Classe/funções:** `FeedHealthMonitor`, `check`, `start`, `stop`, `get_feed_monitor`.
5. **Inputs:** timestamps de arquivos microstructure.
6. **Outputs:** estado interno `gate_enabled`, logs.
7. **Dependências:** threading/path/os.
8. **Quem chama:** `run_live.py` injeta `feed_monitor` no EventProcessor.
9. **Acoplamentos perigosos:** thresholds hardcoded + side-effect gate_enabled.
10. **Lógica:** monitoramento ✅ veto ⚠️ indireto.
11. **Reaproveitar:** provider de liveness.
12. **Não mover sem refactor:** contrato implícito gate_enabled.
13. **Duplicado:** parte de staleness também aparece em service_state checks.
14. **Mistura indevida:** health state + policy (bloqueio) no mesmo módulo.
- **Readiness label:** `WRAP_AS_PROVIDER`

---

## `live/signal_queue.py`
1. **Path:** `live/signal_queue.py`
2. **Papel:** fila persistida de sinais para EA distribution (PENDING/SENT/EXECUTED/FAILED/EXPIRED).
3. **No caminho live atual?:** `PARTIAL_IN_LIVE_PATH`
4. **Funções:** `push`, `peek`, `confirm`, `get_all`, `clear_done`.
5. **Inputs:** sinais de entrada/saída com lotes/SL/TP/accounts.
6. **Outputs:** `signal_queue.json`.
7. **Dependências:** json/threading/path/uuid.
8. **Quem chama:** `run_live.py` usa `push` quando disponível.
9. **Acoplamentos perigosos:** outro canal paralelo de execução/distribuição.
10. **Lógica:** execução/distribuição ✅ monitoramento ✅
11. **Reaproveitar:** queue durability/ack.
12. **Não mover sem refactor:** status lifecycle sem idempotência forte.
13. **Duplicado/legado:** pode duplicar trilha frente a decisor canônico.
14. **Mistura indevida:** distribuição e storage lock-step.
- **Readiness label:** `MOVE_TO_BACKTEST_ONLY` (se arquitetura futura centralizar só no decisor) / `WRAP_AS_PROVIDER` (se EA bridge mantida).

---

## `live/price_speed.py`
1. **Path:** `live/price_speed.py`
2. **Papel:** cálculo de displacement/speed para telemetry e contexto de trigger.
3. **No caminho live atual?:** `YES`
4. **Classes:** `DisplacementResult`, `PriceSpeedTracker`.
5. **Inputs:** ticks de preço.
6. **Outputs:** speed label / classification.
7. **Dependências:** stdlib only.
8. **Quem chama:** `event_processor`.
9. **Acoplamentos perigosos:** thresholds estáticos.
10. **Lógica:** contexto/monitoramento rule-based ✅
11. **Reaproveitar:** módulo puro facilmente encapsulável.
12. **Não mover sem refactor:** pouco acoplado, baixo risco.
13. **Duplicado:** nenhum relevante.
14. **Mistura indevida:** baixa.
- **Readiness label:** `KEEP_AS_IS`

---

## `live/hedge_manager.py`
1. **Path:** `live/hedge_manager.py`
2. **Papel:** lifecycle de hedge de pullback (open/close/escalation) pós-shield.
3. **No caminho live atual?:** `YES` (via `PositionMonitor`)
4. **Classes/funções:** `HedgeManager`, `HedgeState`, `PullbackDecision`, `_open_hedge`, `_close_hedge`.
5. **Inputs:** posição principal, preço/ATR/delta, executor.
6. **Outputs:** ordens hedge + `hedge_events.log`.
7. **Dependências:** executor + guardrail opcional.
8. **Quem chama:** `PositionMonitor`.
9. **Acoplamentos perigosos:** depende do estado de shield e formatos internos do PM.
10. **Lógica:** risco/monitoramento/execução ✅ rule-based ✅
11. **Reaproveitar:** política de hedge separável como provider.
12. **Não mover sem refactor:** estado interno por grupo + executor calls.
13. **Duplicado/legado:** integra guardrail opcional fora do módulo.
14. **Mistura indevida:** avaliação pullback + execução + logging.
- **Readiness label:** `WRAP_AS_PROVIDER`

---

## `live/kill_zones.py`
1. **Path:** `live/kill_zones.py`
2. **Papel:** utilitário temporal ICT kill-zone (telemetry context).
3. **No caminho live atual?:** `YES`
4. **Funções:** `current_kill_zone`, `kill_zone_label`, `is_in_kill_zone*`.
5. **Inputs:** UTC time.
6. **Outputs:** status/label.
7. **Dependências:** stdlib only.
8. **Quem chama:** `event_processor` label telemetry.
9. **Acoplamentos perigosos:** nenhum crítico.
10. **Lógica:** contexto rule-based ✅
11. **Reaproveitar:** quase plug-and-play.
12. **Não mover sem refactor:** baixo risco.
13. **Duplicado/legado:** nenhum.
14. **Mistura indevida:** baixa.
- **Readiness label:** `KEEP_AS_IS`

---

## `live/telegram_notifier.py`
1. **Path:** `live/telegram_notifier.py`
2. **Papel:** notificação operacional, consumindo principalmente `decision_live.json` + `service_state.json`.
3. **No caminho live atual?:** `YES`
4. **Funções:** `notify_decision` (principal), `notify_health_check`, notificações auxiliares.
5. **Inputs:** `decision_live.json`, `service_state.json`.
6. **Outputs:** mensagens Telegram.
7. **Dependências:** HTTP API telegram + json.
8. **Quem chama:** `event_processor`, `position_monitor` (direto em alguns pontos), heartbeat.
9. **Acoplamentos perigosos:** token/chat hardcoded; caminhos locais hardcoded.
10. **Lógica:** observabilidade/notificação ✅
11. **Reaproveitar:** formatter canônico.
12. **Não mover sem refactor:** misc helpers + múltiplas funções legacy.
13. **Duplicado/legado:** funções antigas coexistem com `notify_decision` canônico.
14. **Mistura indevida:** adapter + template + cooldown + health no mesmo módulo.
- **Readiness label:** `SPLIT_BY_RESPONSIBILITY`

---

## `live/d1_h4_updater.py`
- **Status:** `UNKNOWN_LIVE_STATUS` for active runtime use.
- Está presente e funcional como updater, mas no `run_live.py` atual o bloco de start está explicitamente desativado/commented (razão de performance).
- Classificação: `MOVE_TO_BACKTEST_ONLY` (até redesign incremental) / `SCAFFOLD_ONLY` para runtime atual.

---

## Módulos externos/scaffolds explicitamente NÃO-LIVE como verdade operacional
- `master_engine.py`, `position_manager.py`, `config.py`, `types.py` do FluxQuantumAPEX: **SCAFFOLD_ONLY / NÃO-LIVE** para esta auditoria.
- Imports externos opcionais em `event_processor.py` (news/anomaly/guardrail/rl): quando ausentes, runtime continua com fallback; status exato de produção depende do ambiente de deploy.

---

## A) LIVE TRUTH MAP

Fluxo real atual (simplificado):

1. **Market data**
   - microstructure files + MT5 tick
2. **Updaters**
   - `m5_updater.py` -> `gc_m5_boxes.parquet`
   - `m30_updater.py` -> `gc_m30_boxes.parquet`
   - (`d1_h4_updater.py` atualmente desativado no run_live)
3. **Detectors/Context**
   - `level_detector.get_current_levels()`
   - `tick_breakout_monitor` (real-time breakout override)
   - `feed_health` (gate health)
4. **Decisor (de fato hoje)**
   - `EventProcessor` (`_trigger_gate` + `gate.check` + vetoes + execution routing)
5. **Execution / Post-entry**
   - Entry: `MT5Executor` via `event_processor`
   - Post-entry exits/modifies: `PositionMonitor` -> `MT5Executor`
6. **Notifier / Dashboard / Canonical logs**
   - `decision_live.json`, `decision_log.jsonl`, `service_state.json`
   - Telegram (`telegram_notifier`)
   - Dashboards (`base_dashboard_server` + account-specific servers)

**Decisor principal hoje:** `live/event_processor.py`.

---

## B) RULE-BASED VS ML STATUS

### Iceberg
- **Live hoje:** rule-based integration em `event_processor` (`_on_iceberg_event` + protection advice + gate trigger).
- **ML candidato/shadow:** componentes V3/externos opcionais existem, mas não são obrigatórios no caminho principal.

### Anomaly
- **Live hoje:** rule-based via defense mode / thresholds + veto paths no `event_processor`.
- **ML candidato/shadow:** imports opcionais (AnomalyForge/others) com fallback/disable; parte está explicitamente desativada ou condicional.

### Clarificação shadow/scaffold
- `d1_h4_updater.py` no runtime atual: não iniciado por padrão (`UNKNOWN_LIVE_STATUS` operacional ativo).
- FluxQuantumAPEX scaffolds: **não integrar como verdade do runtime atual**.

---

## C) REORGANIZATION READINESS

| Arquivo | Label |
|---|---|
| run_live.py | SPLIT_BY_RESPONSIBILITY |
| live/event_processor.py | SPLIT_BY_RESPONSIBILITY |
| live/position_monitor.py | SPLIT_BY_RESPONSIBILITY |
| live/level_detector.py | WRAP_AS_PROVIDER |
| live/operational_rules.py | WRAP_AS_PROVIDER |
| live/m5_updater.py | SPLIT_BY_RESPONSIBILITY |
| live/m30_updater.py | SPLIT_BY_RESPONSIBILITY |
| live/tick_breakout_monitor.py | WRAP_AS_PROVIDER |
| live/feed_health.py | WRAP_AS_PROVIDER |
| live/signal_queue.py | WRAP_AS_PROVIDER *(ou MOVE_TO_BACKTEST_ONLY se remover bridge EA)* |
| live/price_speed.py | KEEP_AS_IS |
| live/hedge_manager.py | WRAP_AS_PROVIDER |
| live/kill_zones.py | KEEP_AS_IS |
| live/telegram_notifier.py | SPLIT_BY_RESPONSIBILITY |
| live/d1_h4_updater.py | MOVE_TO_BACKTEST_ONLY *(runtime atual)* |
| FluxQuantumAPEX scaffolds (master_engine/position_manager/config/types) | SCAFFOLD_ONLY |

---

## D) NO-HALLUCINATION RULE
- Qualquer módulo não confirmado no caminho de execução atual foi marcado como `UNKNOWN_LIVE_STATUS` ou classificado explicitamente como scaffold/não-live.
- Não foi inferido comportamento runtime de arquivos fora do wiring observável em `run_live.py` + chamadas explícitas nos módulos `live/`.

# FluxQuantumAI APEX — Architecture Reference

**Última actualização:** 2026-04-18
**Propósito:** Referência única para toda a pasta `C:\FluxQuantumAI\live\` — o que cada ficheiro faz, como se relaciona com os outros, e quando é executado.

---

## OVERVIEW

FluxQuantumAI APEX é um sistema de trading rule-based para Gold Futures (GC) com output MT5 em XAUUSD. A pasta `live/` contém **todo o código runtime**, organizado em 4 camadas funcionais que correm em 2 serviços NSSM separados.

### Serviços NSSM activos

| Service | Executável principal | Purpose | Port |
|---|---|---|---|
| `FluxQuantumAPEX` | `run_live.py` (root) | Decision engine + position monitoring (DEMO RoboForex) | — |
| `FluxQuantumAPEX_Live` | `run_live.py` (root) | Mesmo binário, account Hantec (currently blocked) | — |
| `FluxQuantumAPEX_Dashboard` | `live/dashboard_server.py` | Web UI para demo account | 8081 |
| `FluxQuantumAPEX_Dashboard_Hantec` | `live/dashboard_server_hantec.py` | Web UI para live account + EA signal queue | 8082 |

### Processos de captura (separados do APEX)

| PID | Process | Purpose | Port |
|---|---|---|---|
| 12332 | `quantower_level2_api.py` | Captura L2 da DxFeed via Quantower | 8000 |
| 8248 | `iceberg_receiver.py` | Recebe + processa iceberg detections | 8002 |
| 2512 | `watchdog_l2_capture.py` | Watchdog que reinicia capture se falhar | — |

---

## 🟢 CAMADA 1 — CORE DECISION ENGINE

Ficheiros que compõem o cérebro do sistema de decisão de trading.

### `event_processor.py` (204 KB) — CORE

**Role:** Decision engine principal. Executa a cada tick:
1. Pre-entry operational rules check
2. V1→V2→V3→V4 gate chain (sequential)
3. News gate check (block/exit)
4. Execute trade via `open_on_all_accounts()`
5. Emit Telegram notifications (GO/BLOCK/EXECUTED/EXEC_FAILED)
6. Periodic news exit check (every 30s)

**Strategies implemented:**
- **ALPHA** — primary entry on liq touch (99% of trades)
- **GAMMA** — momentum stacking (GHOST, 0 trades nos últimos 4 dias)
- **DELTA** — trend re-alignment (GHOST, 0 trades nos últimos 4 dias)
- **QT_MICRO** — microstructure continuation

**Imports:** kill_zones, level_detector, m30_updater, operational_rules, price_speed, tick_breakout_monitor, apex_news_gate (parent dir)

**Not imported by:** (it's the entry point)

**Key constants:** `DIRECTION_LOCK_S`, `DELTA_DIRECTION_LOCK_S`, `DECISION_LIVE_PATH`, `DECISION_LOG_PATH`, `MAGIC`

---

### `position_monitor.py` (96 KB) — CORE

**Role:** Post-entry management. Monitoriza todas as posições abertas a cada 2s:
1. **SHIELD** — quando Leg1 fecha por TP1, move Leg2+Leg3 SL para breakeven
2. **L2 DANGER** — fecha legs se 3 bars consecutivos com `danger_score >= threshold`
3. **REGIME FLIP** — fecha tudo se `delta_4h` reverter significativamente
4. **TRAILING STOP** — ratchet SL toward price post-SHIELD (77pts trail P90)
5. **T3 DEFENSE** — exit emergency se anomalia estrutural detectada
6. **PULLBACK END** — fecha hedge quando pullback termina (Group A+B)
7. **MT5 HISTORY WATCHER** (Fase 3) — classifica fechos (TP1/TP2/SL/manual/system)
8. **V3 AGENT** (opcional, disabled) — decisões RL-based se activado

**Imports:** hedge_manager, level_detector, mt5_history_watcher, mt5_executor (parent)

**Used by:** Entry point via `run_live.py`

**Run interval:** 2 seconds (MONITOR_INTERVAL_S)

---

### `ats_live_gate.py` (44 KB) — CORE

**Role:** Orchestrator dos 4 gates sequenciais. Cada gate retorna PASS/BLOCK/ZONE_FAIL.

**Gates implementados:**
- **V1** — Zone structural (alignment com M30 box, H4 bias)
- **V2** — L2 microstructure (DOM imbalance, LOI, absorption ratio)
- **V3** — Momentum (delta_4h direction vs trade direction)
- **V4** — Iceberg (chamada ao `ats_iceberg_gate`)

**Imports:** ats_iceberg_gate

**Used by:** event_processor

**Thresholds:** configurável em `settings.json` (calibrated 2026-04-08 + Sprint 8)

---

### `ats_iceberg_gate.py` (41 KB) — CORE

**Role:** V4 gate — detecção de iceberg orders via microstructure. Versão **V1 rule-based** em produção (V2 ML em treino, não em uso).

**Detecta:**
- Iceberg alignment com trade direction → aumenta lot size (`iceberg_aligned_bonus`)
- Iceberg **contra** trade direction → **HARD BLOCK** (CAL-1 absorption=12.28, CAL-2 LOI=0.14)
- Iceberg collision zones (CAL-5 lookback=3min, CAL-6 exceed=2.20pts)
- Breaking ice detection (CAL-6/CAL-7)

**Key feature:** evita entradas contra pressure institucional oculta.

**Used by:** ats_live_gate

---

## 🟢 CAMADA 2 — DETECTORS & DATA

Módulos que fornecem informação estrutural/microstructural ao decision engine.

### `level_detector.py` (24 KB) — CORE

**Role:** Dual-timeframe level detector (M30 + H4). Identifica:
- M30 box (high/low) da última bar
- FMV (fair mid value) do M30
- H4 liquidity lines (liq_top, liq_bot)
- Derive M30 bias (BUY/SELL/NEUTRAL) a partir de structure

**Used by:** event_processor, position_monitor

**Data source:** parquets em `C:\data\level2\` (updated pelos updaters)

---

### `kill_zones.py` — CORE

**Role:** ICT Kill Zones — define janelas temporais de sessões (Asian, London, NY). Cada sessão tem lot sizing diferente:
- Asian: [0.01, 0.01, 0.01]
- London: [0.02, 0.02, 0.01]
- NY: [0.03, 0.02, 0.01]

**Used by:** event_processor

---

### `price_speed.py` (6 KB) — CORE

**Role:** ICT **Displacement tracker**. Mede velocidade de movimento do preço após level touch.

**Threshold:** 0.8 pts/sec (conservative — flags moves >4pts em 5s)

**Used by:** event_processor (confluence com level touch → confirmação institucional)

**Concept:** Move com velocidade `>= 0.8 pt/s` sugere absorção de contra-liquidez por players institucionais, não noise.

---

### `tick_breakout_monitor.py` — CORE

**Role:** Tick-level breakout detection. Monitoriza rompimentos de níveis estruturais em tempo real (< 1s latency).

**Used by:** event_processor

---

### `operational_rules.py` (7 KB) — CORE

**Role:** Pre-entry guards — corridos ANTES do gate chain:

**BARBARA-DEFINED (hardcoded):**
- `MAX_TRADE_GROUPS = 2` — max posições simultâneas
- `MARGIN_FLOOR_PCT = 600.0` — mínimo de margem para entrar

**CAL-TBD (fail-open):**
- `LEVEL_DEDUP_TOLERANCE_ATR_MULT` (CAL-09, null)
- `COOLDOWN_EXIT_DISTANCE_ATR_MULT` (CAL-10, null)

Exact-direction dedup sempre activo (só permite uma posição por direcção).

**Used by:** event_processor

---

### `feed_health.py` (7 KB) — CORE

**Role:** Monitor de freshness do stream L2 (Quantower/dxfeed). Corre em thread separado a cada 2min.

**States:**
- OK (age < 120s)
- STALE (120-300s)
- FEED_DEAD (>300s, 3x consecutivos)

**On FEED_DEAD:** `gate_enabled = False` → EventProcessor **suspende gate checks** até feed recuperar. **Previne trades com dados stale.**

**Problem solved (2026-04-08):** Após restart manual, stream Quantower não reconnectava automaticamente. Sistema corria mas sem dados novos.

**Used by:** event_processor (passed as `feed_monitor` parameter)

---

## 🟢 CAMADA 3 — EXECUTION & POSITION HANDLING

### `hedge_manager.py` (17 KB) — CORE

**Role:** Gestão de pullback hedges. Depois de SHIELD (TP1 hit), se preço pulls back 0.3-1.5×ATR contra posição:
- Abre hedge 0.01 lot counter-direction
- SL = 1.5×ATR (não usa entry pós-SHIELD → evita "Invalid stops" error)
- Fecha quando TREND_RESUMED ou ESCALATION

**Key insight:** V3 Agent disabled **não bloqueia** hedge — HedgeManager avalia pullback conditions independentemente.

**Events emitted (to hedge_events.log):**
- HEDGE_OPENED (pullback detected)
- HEDGE_CLOSED_TREND_RESUMED (pullback ended)
- HEDGE_CLOSED_ESCALATION (regime shift confirmed)

**Guardrail integration:** checa `grenadier_guardrail.get_guardrail_status()` antes de abrir — bloqueia se spread_ticks > 10 ou latency > 2000ms.

**Used by:** position_monitor

**Scheduled for Fase 5 Scope B.4:** PULLBACK_START/END PM_EVENT emission to Telegram.

---

### `mt5_history_watcher.py` (6 KB) — CORE **[NEW Fase 3]**

**Role:** Classifica fechos de posição via `mt5.history_deals_get()`. Distingue:
- **TP_HIT** (TP1 vs TP2 via trades.csv ticket lookup)
- **SL_HIT**
- **MANUAL_CLOSE** (terminal/mobile/web)
- **SYSTEM_CLOSE** (nosso próprio código — skip)
- **STOP_OUT** (margin call)

**Called:** On-demand quando position_monitor detecta `position_count` drop.

**Used by:** position_monitor

**MT5 reason codes:** REASON_CLIENT=0, MOBILE=1, WEB=2, EXPERT=3, SL=4, TP=5, SO=6, etc.

---

### `telegram_notifier.py` (31 KB) — CORE

**Role:** Envia notificações Telegram após cada evento importante.

**Functions:**
- `notify_decision()` — GO/BLOCK/PM_EVENT (Fase 2 refactored)
- `notify_execution()` — EXECUTED/EXEC_FAILED (Fase 2 new)
- `notify_entry_go()`, `notify_entry_block()`, etc. (legacy)

**Icon map (Fase 2 M6.3):**
- 🎯 GO
- ⛔ BLOCK
- 🛡 SHIELD / TP1_HIT
- 🏆 TP2_HIT
- 🛑 SL_HIT
- 🔄 REGIME_FLIP
- ↩ PULLBACK_START
- ↪ PULLBACK_END_EXIT
- ⚠ L2_DANGER
- 🚨 T3_EXIT
- 📰 NEWS_EXIT (Fase 4)

**Reads from:** `decision_live.json` (single source of truth)

**Used by:** event_processor, position_monitor, hedge_manager (Fase 5 Scope B.4)

---

## 🟡 CAMADA 4 — UPDATERS (Scheduled Tasks)

Ficheiros que correm como tasks independentes (não são importados pelo serviço principal). Actualizam parquets que o decision engine consome.

### `m5_updater.py` (24 KB) — SCHEDULED

**Role:** Actualiza parquet M5 (5-minute bars) a partir de ticks raw.

**Runs:** via scheduled task (every 5min?)

**Output:** `C:\data\level2\m5_*.parquet`

---

### `m30_updater.py` (21 KB) — USED BY event_processor

**Role:** Actualiza parquet M30 (30-minute bars com box high/low, iceberg zones).

**Pode correr como standalone OR ser chamado em-process pelo event_processor**.

**Output:** `C:\data\level2\m30_*.parquet`

---

### `d1_h4_updater.py` (24 KB) — SCHEDULED [CURRENTLY DISABLED]

**Role:** Actualiza parquets D1 (daily) e H4 (4-hour) com box structure.

**Status:** **Disabled há 9-15 dias** (desde FASE 4a shadow, para performance). Item de backlog.

**Consequência actual:** Health check Telegram só mostra M30 bias (não H4/D1). Sistema opera "meio-cego" sem H4 structure updates.

**Output:** `C:\data\level2\d1_*.parquet`, `h4_*.parquet`

---

## 🔵 CAMADA 5 — DASHBOARDS

### `base_dashboard_server.py` (26 KB) — DASHBOARD BASE

**Role:** Base partilhada entre demo e hantec dashboards. Elimina duplicação de código (Gap G-01).

**Shared routes:**
- `/api/trades` — trade history reconciled com MT5
- `/api/gates` — V1-V4 status
- `/api/phase` — current phase (accumulation/manipulation/distribution)
- `/api/nextgen/*` — NextGen War Room endpoints (via NextGenDataBus)
- `/api/v3/*` — V3 RL status (real data, not stubs — Gap G-03 fixed)

**Used by:** dashboard_server, dashboard_server_hantec

---

### `dashboard_server.py` (3 KB) — DASHBOARD DEMO

**Role:** Web UI para DEMO account (RoboForex 0.05ct).

**Port:** 8081
**Balance inicial:** $500.00
**Trades log:** `logs/trades.csv`
**HTML:** `logs/index_live.html`

**Runs:** via NSSM service FluxQuantumAPEX_Dashboard

---

### `dashboard_server_hantec.py` (8 KB) — DASHBOARD LIVE

**Role:** Web UI para LIVE account (Hantec 0.02ct) + **EA signal queue endpoints**.

**Port:** 8082
**Balance inicial:** $346.10
**Trades log:** `logs/trades_live.csv`
**HTML:** `logs/index_live_hantec.html`

**Extra routes (vs demo):**
- `/api/orders` — confirmed orders only
- `/api/news` — high-impact news from NextGenDataBus
- `/api/signal/pending?account=X` — EA polls this every 1-2s
- `/api/signal/confirm` — EA reports execution result

**MT5 credentials:** via `.env` file (HANTEC_ACCOUNT, HANTEC_PASSWORD, HANTEC_SERVER)

**Runs:** via NSSM service FluxQuantumAPEX_Dashboard_Hantec

---

### `signal_queue.py` (7 KB) — DASHBOARD INFRA

**Role:** Thread-safe signal queue persisted em JSON. Usado pelo dashboard_server_hantec para distribuir sinais aos EAs.

**Lifecycle:**
- PENDING → EA poll → SENT → execute → EXECUTED/FAILED
- EXPIRED se age > 30s

**Multi-account ready:** `accounts: list[int]` permite broadcast para múltiplas MT5 accounts.

**Infrastructure para 20 users:** já suporta multi-account nativamente. Base para futura integração Signal Provider / Copy Trading sem refactor.

**Used by:** dashboard_server_hantec

---

## ⚫ INFRASTRUCTURE

### `__init__.py`
Marker Python para reconhecer `live/` como package.

### `__pycache__/`
Bytecode auto-gerado. Não tocar.

---

## DATA FLOW COMPLETO

```
                    ┌──────────────────────────────────┐
                    │ Quantower L2 API (port 8000)     │
                    │ Captures dxFeed ticks + DOM      │
                    └────────────┬─────────────────────┘
                                 │ writes to disk
                                 ▼
     ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
     │ m5_updater   │  │ m30_updater      │  │ d1_h4_updater│
     │ (scheduled)  │  │ (in-process)     │  │ [DISABLED]   │
     └──────┬───────┘  └────────┬─────────┘  └──────┬───────┘
            │ writes             │ writes             │ writes
            ▼                    ▼                    ▼
  ┌────────────────────────────────────────────────────────┐
  │           C:\data\level2\*.parquet                      │
  │           C:\data\level2\_gc_xcec\micro*.csv.gz         │
  └───────────────────┬────────────────────────────────────┘
                      │ reads
                      ▼
  ┌─────────────────────────────────────────────────┐
  │  event_processor.py (decision engine)           │
  │   1. operational_rules.check_can_enter()        │
  │   2. ats_live_gate → V1+V2+V3+V4                │
  │   3. price_speed confluence                     │
  │   4. level_detector.derive_bias()               │
  │   5. kill_zones.current_session()               │
  │   6. apex_news_gate.check()                     │
  │   7. feed_health.gate_enabled check             │
  │   8. _open_on_all_accounts() → MT5              │
  │   9. tg.notify_decision() + notify_execution()  │
  └──────┬──────────────────────┬───────────────────┘
         │ writes               │ writes
         ▼                      ▼
  ┌────────────────┐    ┌──────────────────┐
  │decision_live   │    │ trades.csv       │
  │.json           │    │ trades_live.csv  │
  └────────┬───────┘    └────────┬─────────┘
           │ reads                 │ reads
           ▼                       ▼
  ┌─────────────────────────────────────────────┐
  │ position_monitor.py (every 2s)              │
  │   - SHIELD / L2_DANGER / REGIME_FLIP        │
  │   - TRAILING / T3 / PULLBACK_END            │
  │   - hedge_manager.process()                 │
  │   - mt5_history_watcher.find_closed_*()     │
  │   - _emit_position_event → canonical        │
  │   - tg.notify_decision() [PM_EVENT]         │
  └─────────────────────────────────────────────┘
         │                       │
         ▼                       ▼
  ┌────────────┐         ┌──────────────┐
  │ MT5 Demo   │         │ MT5 Hantec   │
  │ (RoboForex)│         │ (Live blocked)│
  └─────┬──────┘         └──────┬───────┘
        │                        │
        └────────┬───────────────┘
                 │
                 ▼ accounts connect via signal_queue (from dashboard)
        ┌──────────────────────┐
        │  EA Terminal         │
        │  polls /api/signal/  │
        │  pending every 1-2s  │
        └──────────────────────┘


  Dashboards (parallel):
  ┌─────────────────────────┐    ┌─────────────────────────┐
  │ dashboard_server.py     │    │ dashboard_server_hantec │
  │ Port 8081 (Demo UI)     │    │ Port 8082 (Live UI)     │
  └──────────┬──────────────┘    └──────────┬──────────────┘
             │                              │
             └──────────────┬───────────────┘
                            ▼
                 ┌──────────────────────┐
                 │ base_dashboard_server│
                 │ (shared routes)      │
                 └──────────────────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ NextGenDataBus       │
                 │ (in-memory state)    │
                 └──────────────────────┘
```

---

## SUMMARY CHEAT-SHEET

**When something goes wrong, check:**

| Symptom | Likely file |
|---|---|
| Não recebes notificação Telegram | `telegram_notifier.py` (leitor de `decision_live.json`) |
| Não faz trades (todos BLOCK) | `feed_health.py` (check FEED_DEAD) ou `operational_rules.py` |
| Trade abre mas perde muito | `hedge_manager.py` ou `position_monitor.py` (trailing/regime) |
| Dashboard não mostra dados | `base_dashboard_server.py` ou `NextGenDataBus` |
| H4 bias mostra "?" | `d1_h4_updater.py` [DISABLED — backlog] |
| Iceberg blocks toda a trade | `ats_iceberg_gate.py` (thresholds CAL-1/CAL-2) |
| EA não recebe signals | `signal_queue.py` ou `dashboard_server_hantec.py` |
| Posição fecha sem notificar | `position_monitor._publish_canonical_pm_event` (Fase 2 M7) |

---

## WHAT IS PLANNED (NextGen roadmap)

O APEX actual é rule-based sofisticado. NextGen vai evoluir componentes específicos para ML:

| APEX Current (rule-based) | NextGen (ML-based) |
|---|---|
| `ats_iceberg_gate` V1 thresholds | Iceberg ML V2 (training, not prod) |
| Static entry thresholds | P(WIN) predictor (P7 Entry Quality) |
| M30 bias derivation | Multi-TF regime classifier |
| Fixed TP1/TP2 levels | Exit Intelligence (P8) |
| Manual calibration | Online learning via NextGenDataBus |

**Infrastructure already exists:**
- NextGenDataBus (referenced in dashboards)
- `/api/nextgen/*` endpoints (phase-detector, scorecard, war-room)
- `/api/v3/*` endpoints (V3 RL status)

**NextGen é evolução, não substituição.** APEX rule-based é a **specification** que o ML vai aprender a executar melhor.

---

## HOW TO UPDATE THIS DOCUMENT

Quando adicionares/removeres/modificares ficheiros em `live/`:

1. Actualiza a tabela da camada correspondente
2. Se criares nova camada, adiciona secção
3. Actualiza o data flow diagram
4. Actualiza cheat-sheet se symptom → file mapping muda
5. Actualiza data no topo

**Keep this doc as single source of truth.** Code comments e commit messages são secundários — este documento é o onboarding rápido.

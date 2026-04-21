# FluxQuantumAI — Dashboard Architecture
**Estado Actual · 2026-04-11**
**Versão:** 1.0 · Autor: Claude Code (gerado para revisão do Arquitecto)

---

## 1. Visão Geral

O sistema de dashboard é composto por **dois servidores HTTP independentes**, cada um a servir
uma conta de trading separada. Ambos são implementados em Python puro (`http.server` da stdlib —
sem framework, sem WebSockets, sem autenticação).

```
Browser
  │
  ├── http://localhost:8081  ──→  dashboard_server.py        (Demo — RoboForex 0.05ct)
  │
  └── http://localhost:8082  ──→  dashboard_server_hantec.py (Live — Hantec 0.02ct)
                                           │
                                           └── proxy → localhost:8088  (legacy — ver §6)
```

Os dois servidores partilham a mesma lógica de base mas o código está **duplicado** — não existe
módulo partilhado. Qualquer alteração tem de ser aplicada nos dois ficheiros.

---

## 2. Ficheiros Envolvidos

| Ficheiro | Papel |
|---|---|
| `live/dashboard_server.py` | Servidor HTTP — conta Demo (RoboForex) |
| `live/dashboard_server_hantec.py` | Servidor HTTP — conta Live (Hantec) |
| `logs/index_live.html` | Frontend Demo |
| `logs/index_live_hantec.html` | Frontend Hantec (superset do demo) |
| `logs/chart.umd.min.js` | Chart.js bundled (local, sem CDN) |
| `logs/trades.csv` | Histórico de trades — Demo |
| `logs/trades_live.csv` | Histórico de trades — Hantec |

---

## 3. Servidor Demo — `dashboard_server.py`

### 3.1 Configuração

| Parâmetro | Valor |
|---|---|
| Porta | **8081** |
| Host | `0.0.0.0` (bind em todas as interfaces) |
| HTML servido | `logs/index_live.html` |
| Trades CSV | `logs/trades.csv` |
| Balance inicial | `500.00 USD` (hardcoded — fallback se MT5 offline) |
| Thread model | Daemon thread via `start()`, ou blocking se `__main__` |
| Logging per-request | **Silenciado** (`log_message` override) |

### 3.2 Endpoints API

| Método | Path | Fonte de dados | Resposta |
|---|---|---|---|
| GET | `/api/status` | `trades.csv` + MT5 (opcional) | Ver §3.3 |
| GET | `/api/equity` | `trades.csv` | `[{t, balance}]` — curva de equity acumulada |
| GET | `/api/weekly` | `trades.csv` | `[{week, pnl}]` — PnL agrupado por semana ISO |
| GET | `/api/trades` | `trades.csv` | Array com todos os trades (raw CSV como JSON) |
| GET | `/` ou `/index_live.html` | `logs/index_live.html` | HTML do frontend |
| GET | `/<ficheiro>` | `logs/<ficheiro>` | Qualquer ficheiro em `logs/` (chart.js, imagens, etc.) |

### 3.3 Payload `/api/status` — Demo

```json
{
  "timestamp":        "2026-04-11T10:30:00+00:00",
  "balance":          523.40,
  "equity":           523.40,
  "balance_start":    500.00,
  "unrealized_pnl":   0.00,
  "free_margin":      0.00,
  "total_pnl":        23.40,
  "total_return_pct": 4.68,
  "trades_confirmed": 42,
  "trades_filtered":  87,
  "win_rate":         58.3,
  "wins":             14,
  "losses":           10,
  "profit_factor":    1.36,
  "mt5_live":         false
}
```

> `mt5_live: false` quando MetaTrader5 não está disponível — balance calculado a partir de `trades.csv`.

---

## 4. Servidor Hantec — `dashboard_server_hantec.py`

### 4.1 Configuração

| Parâmetro | Valor |
|---|---|
| Porta | **8082** |
| Host | `0.0.0.0` |
| HTML servido | `logs/index_live_hantec.html` |
| Trades CSV | `logs/trades_live.csv` |
| Balance inicial | `346.10 USD` (hardcoded — saldo inicial real da conta Hantec) |
| Conta MT5 | `50051145` (carregada via `.env`) |
| Terminal MT5 | `C:/Program Files/Hantec Markets MT5 Terminal/terminal64.exe` |
| Proxy cache | `localhost:8088/api/status` com TTL de 10s |

### 4.2 Variáveis de Ambiente (`.env`)

```
HANTEC_ACCOUNT   = 50051145
HANTEC_PASSWORD  = ***
HANTEC_SERVER    = HantecMarketsMU-MT5
HANTEC_TERMINAL  = C:/Program Files/Hantec Markets MT5 Terminal/terminal64.exe
```

### 4.3 Endpoints API — Hantec (superset do Demo)

| Método | Path | Notas vs. Demo |
|---|---|---|
| GET | `/api/status` | Payload extendido (ver §4.4) + proxy de gates/phase de `localhost:8088` |
| GET | `/api/equity` | Igual ao demo |
| GET | `/api/weekly` | Igual ao demo |
| GET | `/api/trades` | Suporta `?asset=` query param (filtro — ignorado se `ALL`) |
| GET | `/api/orders` | Apenas trades `CONFIRMED`, ordem newest-first |
| GET | `/api/news` | Proxy de `localhost:8088/api/news`; fallback `{events:[], source:"offline"}` |
| GET | `/api/v3/status` | **Stub** — `{mode:"disabled", ...}` |
| GET | `/api/v3/scorecard` | **Stub** — zeros |
| GET | `/api/v3/action_dist` | **Stub** — `{total:0, pct:{}}` |
| GET | `/api/v3/feature_importance` | **Stub** — `[]` |
| GET | `/api/v3/decisions` | **Stub** — `[]` |

### 4.4 Payload `/api/status` — Hantec (campos adicionais vs. Demo)

```json
{
  "...campos_demo...": "...",
  "sharpe":             0.0,
  "max_drawdown":       12.50,
  "trading_started_at": "2026-01-15T09:32:00",
  "account":            50051145,
  "server":             "HantecMarketsMU-MT5",
  "currency":           "USD",
  "leverage":           500,
  "open_positions":     [],
  "gates":              {},
  "phase_name":         "NEW_RANGE",
  "box_source":         null,
  "box_age_h":          null
}
```

> Os campos `gates`, `phase_name`, `box_source`, `box_age_h` chegam via proxy de `localhost:8088`.
> Se a porta 8088 não responder, ficam vazios/null (ver §6 — Dependência Legada).

---

## 5. Frontend — Painéis e Funcionalidades

### 5.1 Dashboard Demo (`index_live.html`)

| Painel | ID HTML | Dados |
|---|---|---|
| Performance KPIs | (topo) | `/api/status` — balance, equity, return%, win rate, PF |
| Open Positions | `#positions-panel` | `/api/status` open_positions |
| Vector Gate Status | `#gates-panel` / `#gates-grid` | `/api/status` gates |
| Equity Curve | `#equity-chart` (Chart.js) | `/api/equity` |
| Weekly PnL | `#weekly-chart` (Chart.js) | `/api/weekly` |
| Execution Log | `#log-panel` | `/api/trades` |

**Tecnologia frontend:** HTML estático + vanilla JavaScript + Chart.js local.
**Polling:** Cliente re-fetch a cada N segundos (sem WebSocket, sem push).
**Autenticação:** Nenhuma.

### 5.2 Dashboard Hantec (`index_live_hantec.html`) — Painéis Adicionais

| Painel adicional | ID HTML | Dados |
|---|---|---|
| Monthly Projection | `#monthly-panel` | Calculado no cliente a partir de `/api/trades` |
| Economic Calendar | `#news-panel` | `/api/news` (proxy 8088) |
| Market Context (phase) | `#market-context-panel` | `phase_name`, `box_source`, `box_age_h` de `/api/status` |
| V3 RL Panel | `#v3-panel` | `/api/v3/*` — actualmente **tudo stub** |

**Phase badges** suportados: `NEW_RANGE`, `CONTRACTION`, `EXPANSION`, `TREND`.

---

## 6. Dependência Legada — Porta 8088

O `dashboard_server_hantec.py` faz proxy para `localhost:8088` para dois campos:
- `GET /api/status` → busca `gates`, `phase_name`, `box_source`, `box_age_h`
- `GET /api/news` → retransmite eventos do calendário económico

**A porta 8088 não pertence a nenhum serviço activo do FluxQuantumAI actual.**
Esta referência é um vestígio de uma arquitectura anterior (possivelmente WeeklyGold ou
um servidor de dados central). Quando 8088 não responde:
- Os campos de gates/phase ficam `{}` / `null` — silencioso
- O `/api/news` retorna `{events:[], source:"offline"}` — silencioso

> **Impacto actual:** O `#market-context-panel` e `#gates-panel` no dashboard Hantec
> mostram dados vazios em produção. A secção V3 RL está integralmente stub.

---

## 7. Serviço NSSM

| Serviço NSSM | Estado | Porta |
|---|---|---|
| `FluxQuantumAPEX_Dashboard` | AUTO_START | 8081 (demo) |
| `WeeklyGold_Dashboard` | Presente (WeeklyGold descontinuado 2026-04-09) | — |

> Nota: o dashboard Hantec (8082) **não tem serviço NSSM próprio** listado. É provável que
> seja lançado dentro do processo `FluxQuantumAPEX_Live` ou manualmente.

---

## 8. Dados Lidos vs. Calculados

### Origem dos dados de conta

```
MT5 disponível  ──→  balance, equity, margin, free_margin, unrealized_pnl  (real-time)
MT5 offline     ──→  balance = BALANCE_START + sum(pnl trades.csv)          (estimado)
```

### Cálculos feitos no servidor Python (não no cliente)

- `profit_factor` = gross_win / gross_loss
- `win_rate` = wins / total_closed × 100
- `total_return_pct` = (equity - balance_start) / balance_start × 100
- `max_drawdown` (Hantec) = pico-corrente máximo ao longo da equity curve
- `equity_curve` = balance acumulado tick a tick via trades.csv

---

## 9. Gaps e Problemas Conhecidos

| # | Gap | Severidade | Notas |
|---|---|---|---|
| G-01 | Código **duplicado** entre demo e hantec | Médio | Qualquer fix tem de ser aplicado 2×; propenso a divergência |
| G-02 | Porta 8088 morta — gates/phase sempre vazios no Hantec | Médio | Depende de serviço que já não existe |
| G-03 | `/api/v3/*` tudo stub | Alto | Painel V3 RL visível no UI mas não funcional |
| G-04 | Sem autenticação | Baixo | Acesso local apenas; aceitável em fase actual |
| G-05 | Sem WebSocket / push | Baixo | Polling no cliente; ok para frequência actual |
| G-06 | `trades_confirmed` e `trades_filtered` calculados sobre **toda a história** | Baixo | Sem filtro por data; pode inflar em sessões longas |
| G-07 | `sharpe` sempre `0.0` no payload Hantec | Baixo | Campo presente mas não calculado |
| G-08 | `open_positions` sempre `[]` | Médio | MT5 positions não consultadas (apenas `account_info`) |
| G-09 | `BALANCE_START` hardcoded nos dois servidores | Baixo | Mudança de conta exige editar código |
| G-10 | Nenhum dado do **NextGen** exposto | **Crítico** | Dashboard cego ao sistema de inteligência Sprint 1-3 |

---

## 10. O que NextGen Precisa Expor (Requisitos para Sprint Dashboard)

Esta secção documenta os dados produzidos pelo NextGen (Sprints 1-3) que **não têm
nenhuma rota de dashboard** actualmente.

### 10.1 Dados disponíveis no NextGen hoje

| Fonte NextGen | Dados Produzidos | Ficheiro |
|---|---|---|
| `FoxyzeMaster` | Gate 1-5 estado, sizing_breakdown, contracts | `engine/foxyze_master.py` |
| `AnomalyForge` (G1) | anomaly_score, anomaly_level, buffer_fill, avg_latency_ms | `providers/anomaly_forge/provider.py` |
| `FluxSignalEngine` (G3) | shadow_thresholds (t030/t040/t045), regime_label, book_imbalance | `providers/flux_signal_engine/provider.py` |
| `OrderStorm` (G4) | iceberg_score, confluence, refill_obs | `providers/order_storm/provider.py` |
| `DailyScorecard` | threshold_health, sizing_analysis, shadow PnL, imbalance density | `daily_scorecard.py` |
| `InconsistencyDetector` | events por categoria, suggested_threshold, executive_summary | `analytics/inconsistency_detector.py` |
| `ShadowComparator` | agreement_rate, blocked_by_nextgen, sizing_analysis | `analytics/comparator.py` |
| Logs JSONL | `nextgen_performance.jsonl`, `daily_scorecard.jsonl`, `threshold_health_*.jsonl` | `apex_nextgen/logs/` |

### 10.2 Rotas API sugeridas para Sprint Dashboard

```
GET /api/nextgen/status          → FoxyzeMaster: gates G1-G5 (loaded/stub), último tick avaliado
GET /api/nextgen/scorecard       → DailyScorecard: imbalance density, shadow PnL, sizing analysis
GET /api/nextgen/gates           → Último ProviderVerdict de cada gate (snapshot)
GET /api/nextgen/anomaly         → AnomalyForge: score, level, buffer_fill, avg_lat_ms
GET /api/nextgen/comparator      → ShadowComparator: agreement_rate, extra_signals, blocked_count
GET /api/nextgen/inconsistencies → InconsistencyDetector: últimas N inconsistências + executive_summary
GET /api/nextgen/sizing          → Sizing breakdown: avg_contracts_by_regime, anomaly_mult_dist
```

---

## 11. Arquitectura de Referência — Estado Actual vs. Alvo

```
ESTADO ACTUAL
─────────────
Browser
  ├── :8081  dashboard_server.py   ←── trades.csv + MT5
  └── :8082  dashboard_server_hantec.py ←── trades_live.csv + MT5 + [8088 morto]

Nenhum dado do NextGen visível. ShadowComparator, AnomalyForge,
InconsistencyDetector correm em background mas não têm interface.


ALVO PROPOSTO (para discussão — não implementado)
──────────────────────────────────────────────────
Browser
  ├── :8081  dashboard_server.py   ←── trades.csv + MT5 (mantido)
  │             + /api/nextgen/*   ←── NextGenDataBus (novo)
  │
  └── :8082  dashboard_server_hantec.py ←── trades_live.csv + MT5 (mantido)
               + /api/nextgen/*   ←── NextGenDataBus (partilhado)

NextGenDataBus (novo módulo)
  ├── Lê nextgen_performance.jsonl (JSONL em append — tail)
  ├── Lê daily_scorecard.jsonl
  ├── Lê threshold_health_YYYY-MM-DD.jsonl
  └── Expõe dados via endpoints partilhados entre ambos os dashboards
      (elimina duplicação de código)
```

---

## 12. Referências

| Recurso | Localização |
|---|---|
| Dashboard Demo (servidor) | `C:\FluxQuantumAI\live\dashboard_server.py` |
| Dashboard Hantec (servidor) | `C:\FluxQuantumAI\live\dashboard_server_hantec.py` |
| Frontend Demo | `C:\FluxQuantumAI\logs\index_live.html` |
| Frontend Hantec | `C:\FluxQuantumAI\logs\index_live_hantec.html` |
| NextGen logs | `C:\FluxQuantumAI\apex_nextgen\logs\` |
| Plano NextGen | `C:\FluxQuantumAI\DOCS MASTER\Plano de Desenvolvimento APEX_NEXTGEN.txt` |
| Fit-Gap Analysis | `C:\FluxQuantumAI\DOCS MASTER\FitGap_Analysis_FluxQuantumAI_11042026.md` |
| Módulos CurrentState | `C:\FluxQuantumAI\DOCS MASTER\FluxQuantumAI_Modules_CurrentState_23122025.md` |

---

*Documento gerado automaticamente por Claude Code a partir da leitura directa do código-fonte.*
*Revisão humana necessária antes de uso em decisões de arquitectura.*

# W3.5 — `event_processor.py` split proposal

**Status**: PROPOSAL ONLY — no implementation. Aguarda decisão Barbara antes de qualquer split.
**Author**: CC#3
**Date**: 2026-05-09
**Current state**: `live/event_processor.py` = **5044 LOC**, 3 classes, 64 methods

---

## Problema

`event_processor.py` é god-object. Responsabilidades amontoadas:
- Gate logic (V1-V4 chain, hard blocks, score aggregation)
- Tick-by-tick MT5 execution loop
- Iceberg event handling (watchdog thread)
- Position signal building (signal_b/signal_c daily trend)
- Service state heartbeat (`service_state.json` write)
- Signal queue management
- M5/M30 metric refresh
- Decision persistence (`decision_live.json` + `decision_log.jsonl`)
- Protection advice cache (anomaly + iceberg severity)
- Strategy mode selection (RANGE/TRENDING/PULLBACK/CONTINUATION/OVEREXTENSION)
- Pre-entry gate chain (cooldowns, ATR extreme, RR, daily loss)
- Daily PnL computation
- News gate integration

**Consequência operacional**: qualquer bug em UM caminho afeta TODOS — auditoria de hoje (2026-05-08) achou 4 categorias de bugs interconectados, todos centrados em `event_processor.py`.

**Consequência de manutenção**: difícil testar incrementalmente; qualquer refator carrega risco alto.

---

## Critério de split

Princípios:
1. **Cohesion over count**: cada módulo deve ter UMA responsabilidade clara, não simplesmente "menos linhas"
2. **Stable interfaces**: mudanças internas não devem propagar; APIs entre módulos via dataclasses
3. **Testability**: cada módulo deve ser testável sem instanciar todo o EventProcessor
4. **Backwards compat**: shimming temporário em event_processor.py preserva imports existentes
5. **Migration safety**: split em fases — não tudo de uma vez

---

## Architecture proposal — 5 módulos

```
live/event_processor.py     (~1500 LOC)  — orchestrator (event loop, threading, lifecycle)
live/decision_writer.py      (~80 LOC)    — JÁ EXTRAÍDO (W3.1)  ✅
live/gate_chain.py           (~700 LOC)   — V1/V2/V3/V4 gate evaluation (NEW)
live/strategy_router.py      (~600 LOC)   — RANGE/TRENDING/PULLBACK/CONTINUATION mode selection + direction resolution (NEW)
live/position_targets.py     (~400 LOC)   — SL/TP1/TP2 computation across strategy modes (NEW)
live/service_state.py        (~250 LOC)   — heartbeat + service_state.json write (NEW)
live/metrics_cache.py        (~200 LOC)   — atr_m30, delta_4h, gc_mt5_offset polling (NEW)
live/iceberg_handler.py      (~250 LOC)   — _on_iceberg_event + dedup hash (NEW)
```

**Total**: ~3980 LOC após split, distribuído. 5044 → 1500 (event_processor) + 7 módulos focados.

---

## Detalhes por módulo

### `live/gate_chain.py` (NEW)

**Responsabilidades**:
- `_check_pre_entry_gates(direction, delta_4h, price) → (blocked, reason)` — cooldowns, ATR extreme, daily loss, etc.
- `evaluate_v1_v2_v3_v4(...)` — current ats_live_gate.py wrapper logic
- `compute_total_score(mom, ice, l2)` + `MIN_SCORE_GO` enforcement
- ICE_ONLY guard logic (W1.2 follow-up if needed)

**Extrai do event_processor**:
- Métodos `_check_pre_entry_gates` (1929-2050), `_compute_daily_pnl` (NEW W2.5)
- Constants: `GATE_COOLDOWN_S`, `DIRECTION_LOCK_S`, `MIN_SCORE_GO`-related logic

**Interface**:
```python
@dataclass
class PreEntryGateResult:
    blocked: bool
    reason: str
    gate_name: str  # for telemetry: "ATR_EXTREME", "DAILY_LOSS", "COOLDOWN", ...

def check_pre_entry_gates(direction: str, ctx: GateContext) -> PreEntryGateResult: ...
```

**Test plan**: existing `tests/test_w2_gates.py` (9 tests) plus new `test_gate_chain_*.py` for V1-V4.

---

### `live/strategy_router.py` (NEW)

**Responsabilidades**:
- `_get_strategy_mode() → "RANGE_BOUND" | "TRENDING"` (current line ~3680)
- `_resolve_direction(level_type) → (direction, reason)` (current line ~3800)
- `_resolve_trend_direction() → (long/short, source, conf)` cascade 5-layer (current ~3650)
- `_get_trend_entry_mode(level_type, price, trend_direction) → (PULLBACK/CONTINUATION/SKIP, dir, reason)` (current 3439)
- F-asymmetric filter logic (W2.6 always-on)
- W2.4 displacement orientation guard

**Extrai do event_processor**:
- Métodos `_get_strategy_mode`, `_resolve_direction`, `_resolve_trend_direction`, `_get_trend_entry_mode`, `_detect_trend_displacement`, `_check_displacement_bars`, `_detect_local_exhaustion`, `_log_continuation_attempt`

**Interface**: dataclass-based para evitar passagem de muitos args.

**Test plan**: existing `tests/test_strategy_mode_cascade.py` (34 tests) + new tests para router.

---

### `live/position_targets.py` (NEW)

**Responsabilidades**:
- SL/TP1/TP2 computation por strategy mode
- W1 BUG-SL-DISPLACEMENT fix (orientation guard)
- W2.2 RR>=1.0 universal check (post-compute)
- Lot sizing (`_compute_session_lots`)

**Extrai do event_processor**:
- Lines ~2750-2920 (CONTINUATION + RANGE_BOUND + PULLBACK SL/TP blocks)
- `_compute_session_lots`, `_split_lots` wrapper

**Interface**:
```python
@dataclass
class PositionTargets:
    direction: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    lots: tuple[float, float, float]
    rr: float

def compute_targets(...) -> PositionTargets | None:  # None = reject
```

---

### `live/service_state.py` (NEW)

**Responsabilidades**:
- Build + write `service_state.json` (heartbeat)
- Read `gc_d1h4_bias.json` (ADR-002 shadow telemetry)
- Read VAP state from MacroMonitor

**Extrai**:
- `_write_service_state` (~840), `_read_d1h4_bias_shadow` (~738)
- Heartbeat thread loop (~905-921)

---

### `live/metrics_cache.py` (NEW)

**Responsabilidades**:
- Refresh `delta_4h`, `atr_m30_parquet`, `bar_delta` from M30/M5 parquets
- Background metrics refresh thread (every `METRICS_REFRESH_S`)
- `gc_xauusd_offset` polling (every `OFFSET_REFRESH_S`)

**Extrai**:
- `_refresh_metrics`, `_refresh_offset`, related thread loop

---

### `live/iceberg_handler.py` (NEW)

**Responsabilidades**:
- `_on_iceberg_event` (current ~4559) com W1.3 dedup hash + protection advice
- Iceberg JSONL watchdog setup
- IcebergJSONLHandler class

**Extrai**:
- `_on_iceberg_event`, `IcebergJSONLHandler`, related watchdog observer setup

---

### `live/event_processor.py` (RESIDUAL)

Após split, mantém:
- `EventProcessor` class skeleton (orchestrator)
- `__init__` lifecycle
- Tick loop (`run`, `_tick`)
- Trigger dispatch (ALPHA, GAMMA, DELTA, ICEBERG)
- Coordination entre módulos
- Backwards-compat re-exports (de constantes / paths)

Tamanho alvo: ~1500 LOC. Reduz blast radius dramaticamente.

---

## Migration plan (fases)

### Fase 1 — Foundation (~2h)
- Criar `live/_types.py` com dataclasses compartilhadas (`GateContext`, `PreEntryGateResult`, `PositionTargets`, `StrategyDecision`, etc.)
- Adicionar testes de fronteira (boundary tests) entre módulos antes de mover código

### Fase 2 — Extract leaf modules (~3h)
- `iceberg_handler.py` (mais isolado)
- `service_state.py` (apenas write)
- `metrics_cache.py` (background thread)
- Cada um: extract → re-export from event_processor → run all tests → commit

### Fase 3 — Extract core logic (~4h)
- `position_targets.py` (SL/TP)
- `gate_chain.py` (pre-entry + V1-V4)
- `strategy_router.py` (mode + direction)
- Cada um: extract → re-export → tests → commit

### Fase 4 — Slim event_processor (~1h)
- Remove dead code path (re-exports)
- Document new architecture in `docs/ARCHITECTURE.md`
- Final regression suite

**Total ETA**: ~10h split em commits incrementais. Comparado com ~8h estimate inicial — split + tests é mais conservador.

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Quebra de sinal em produção durante split | HIGH | Sistema OFF até Fase 4 completa; cada Fase tem suite regressão obrigatória |
| Imports circulares (event_processor ↔ strategy_router ↔ gate_chain) | MED | Fase 1 estabelece contratos via dataclasses; nenhum módulo importa o outro além de tipos |
| Perda de backwards-compat em scripts externos (calibration, backtests) | MED | event_processor.py mantém re-exports de constantes/paths até Fase 4; scripts auditados |
| Mistura de scopes pendentes (W1, W2 fixes podem ser perdidos no merge) | LOW | Split feito DEPOIS de W1+W2 completos (atual estado); todo trabalho W1/W2 já está in-place |
| Tests collection failures (pré-existentes em alguns test files) | LOW | Não bloqueante; tests/test_strategy_mode_cascade + test_w2_gates + test_m30_bias_voting cobrem core paths |

---

## Recommendation

**NÃO implementar W3.5 agora**. Motivos:
1. W1+W2 introduziram 12 mudanças concretas + 11 novos unit tests; estabilizar antes
2. Sistema OFF — não há feedback de produção sobre se W1+W2 são suficientes
3. Split de 5044 LOC requer ~10h focused work + tests; melhor planejar como sprint dedicado
4. ADR-002 (W3.2) já formalizou principal débito arquitetural via documentação

**Caminho sugerido para Barbara decidir**:

A. **Skip W3.5 por agora** — focar em W4 (methodology completion) ou ligar sistema para validar W1+W2 em prod (com market open + brokers conectados)

B. **Aprovar W3.5 como sprint separado** — planejar como BUG-EVT-PROCESSOR-SPLIT no Asana com 4 fases sequenciais

C. **Fazer apenas Fase 1 (dataclasses + boundary tests)** — investimento de 2h que prepara terreno sem split real, valor alto e risco zero

Recomendação: **Opção C** se houver appetite para iniciar; senão **Opção A**.

---

## Status

- W3.1 ✅ decision_writer extraído
- W3.2 ✅ ADR-002 criado
- W3.3 ✅ config drift cleanup
- W3.4 ✅ dead code removido
- W3.5 📋 PROPOSAL only (este documento) — aguarda Barbara

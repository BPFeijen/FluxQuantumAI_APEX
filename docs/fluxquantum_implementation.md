# FluxQuantumAI — Technical Implementation Document
**Version:** Sprint 9 (2026-04-14)
**Status:** AWAITING AUDIT — NOT FOR PRODUCTION

---

## 1. PHASE ENGINE

### Responsabilidade
Determinar regime de mercado. NAO trigger trades, NAO bloqueia trades, NAO define TP/SL.

### Inputs
- **D1/4H**: `daily_trend` ("long"|"short") — directional bias
- **M30**: `gc_m30_boxes.parquet` — box structure, liq zones, FMV
- **M5**: NUNCA usado para phase. Apenas timing fino (displacement, GAMMA/DELTA)

### Output
```json
{
  "phase": "CONTRACTION | EXPANSION | TREND | NEW_RANGE"
}
```
Escrito em `service_state.json` a cada 30s pelo heartbeat.

### Logica Completa de `_compute_raw_phase()`

```
INPUT:
  gc_price        = self._metrics["gc_mid"]       (preco GC actual)
  box_high        = m30_box_high da ultima row     (topo da box M30)
  box_low         = m30_box_low da ultima row      (fundo da box M30)
  confirmed       = m30_box_confirmed              (True/False)
  daily_trend     = self.daily_trend               ("long"/"short"/"")

DECISAO:
  SE gc_price <= 0:
    -> manter phase anterior (processo sem dados)

  SE box_low <= gc_price <= box_high:
    -> CONTRACTION
    (preco dentro da box = equilibrio, compradores e vendedores concordam no valor)

  SE preco FORA da box:
    Contar barras consecutivas com close fora da box (_count_bars_outside_box)
    SE preco retorna dentro da box: reset contador = 0

    SE confirmed = True E daily_trend definido:
      SE _detect_box_ladder(daily_trend) = True:
        SE bars_outside_box >= 4 (TREND_ACCEPTANCE_MIN_BARS, CAL-PATCH1):
          -> TREND
          (TODAS as 4 condicoes satisfeitas:
           box confirmada + daily_trend + ladder + aceitacao temporal)
        SENAO:
          -> EXPANSION
          (ladder OK mas preco saiu ha pouco tempo, pode ser spike)
      SENAO:
        -> EXPANSION
        (box confirmada mas sem ladder = expansao isolada)
    SENAO:
      -> EXPANSION
      (box nao confirmada = breakout em progresso, JAC pendente)

  SE erro ou sem dados:
    -> manter phase anterior
```

### PATCH 1 — Aceitacao Temporal (`_count_bars_outside_box`)

```
INPUT:
  df = M30 parquet
  box_high, box_low = limites da box actual
  Ultimas 10 barras completadas (exclui barra actual incompleta)

LOGICA:
  Contar PARA TRAS a partir da barra mais recente:
    SE close dentro da box [box_low, box_high] -> parar
    SE close fora -> incrementar contador
  Retornar contador

THRESHOLD:
  TREND_ACCEPTANCE_MIN_BARS = 4  (calibrado CAL-PATCH1, 2026-04-14)
  Significado: preco deve fechar fora da box em pelo menos 4 barras M30
  consecutivas (= 2 horas minima) antes de promover para TREND

CALIBRACAO (CAL-PATCH1, dataset Jul 2025 - Abr 2026, 142 breakout events):
  Candidatos testados: 2, 3, 4 barras
  +-----------+------------------+--------------------+-----------------+
  | Threshold | Fakes Filtrados  | Validos Capturados | Validos Perdidos|
  +-----------+------------------+--------------------+-----------------+
  | T=2       | 36/72  (50.0%)   | 70/70  (100%)      | 0               |
  | T=3       | 49/72  (68.1%)   | 70/70  (100%)      | 0               |
  | T=4       | 60/72  (83.3%)   | 70/70  (100%)      | 0               |
  +-----------+------------------+--------------------+-----------------+
  Separacao limpa: fakes max 5 barras, validos min 6 barras
  T=4 escolhido: melhor filtragem sem perda de trends validos

EXEMPLO REAL (2026-04-14):
  01:00 UTC close=4793.4 fora da box [4742,4757] -> count=1
  00:30 UTC close=4792.4 fora -> count=2
  00:00 UTC close=4786.0 fora -> count=3
  22:00 Apr-08 close=4733.9 fora -> count=4
  21:30 Apr-08 close=4744.6 DENTRO -> stop
  Resultado: 4 barras consecutivas fora = aceite (>= 4) -> TREND
```

### Logica de `_detect_box_ladder(trend_direction, min_boxes=3)`

```
INPUT:
  Todas as rows onde m30_box_confirmed = True
  Agrupadas por m30_box_id
  Ordenadas por box_id (cronologico)
  Extrair m30_fmv de cada box

DECISAO:
  Pegar ultimas 3 boxes confirmadas
  SE trend_direction = "long":
    TODAS as transicoes FMV[i] > FMV[i-1] -> True
  SE trend_direction = "short":
    TODAS as transicoes FMV[i] < FMV[i-1] -> True
  Caso contrario -> False

EXEMPLO REAL (dados actuais):
  Box 5218: FMV = 4733.0
  Box 5219: FMV = 4839.9  (+106.9)
  Box 5220: FMV = 4798.7  (-41.2)  <- CAIU
  Ladder LONG = False (4839.9 -> 4798.7 nao e progressivo)
  Resultado: EXPANSION (nao TREND)
```

### Hysteresis (Estabilidade)

```
_PHASE_HYSTERESIS_S = 120.0 segundos (2 minutos)

A cada chamada:
  raw = _compute_raw_phase()

  SE raw == phase_actual:
    resetar candidato
    retornar phase_actual

  SE raw != candidato_anterior:
    novo candidato = raw
    timer = agora
    retornar phase_actual (manter antiga)

  SE raw == candidato_anterior E elapsed >= 120s:
    phase_actual = raw  (transicao aceite)
    log: "[PHASE_ENGINE] OLD -> NEW (after Ns hysteresis)"
    retornar nova phase

  SE elapsed < 120s:
    retornar phase_actual (aguardar)
```

### Tabela de Transicoes de Phase

Todas as transicoes passam por hysteresis (120s minimo).

```
DE              -> PARA          | CONDICOES (TODAS obrigatorias)
================================================================================
CONTRACTION     -> EXPANSION     | preco fecha fora da box [box_low, box_high]
                                 | (qualquer direcao)
--------------------------------------------------------------------------------
EXPANSION       -> CONTRACTION   | preco regressa para dentro da box
                                 | (reset: bars_outside_box = 0)
--------------------------------------------------------------------------------
EXPANSION       -> TREND         | 1. m30_box_confirmed = true
                                 | 2. daily_trend definido ("long" ou "short")
                                 | 3. _detect_box_ladder(daily_trend) = true
                                 |    (3 boxes com FMV progressivo)
                                 | 4. PATCH 1: bars_outside_box >= 4
                                 |    (_count_bars_outside_box >= TREND_ACCEPTANCE_MIN_BARS)
                                 |    [calibrado CAL-PATCH1: T=4, 83.3% fake filter, 100% valid]
--------------------------------------------------------------------------------
TREND           -> CONTRACTION   | preco regressa para dentro da box
                                 | (reset: bars_outside_box = 0)
--------------------------------------------------------------------------------
TREND           -> EXPANSION     | ladder quebrada (FMV nao progressivo)
                                 | OU box_confirmed passa a false (nova box em formacao)
--------------------------------------------------------------------------------
CONTRACTION     -> TREND         | IMPOSSIVEL — deve passar por EXPANSION primeiro
                                 | (preco tem de sair da box antes de poder ter bars_outside)
--------------------------------------------------------------------------------
qualquer        -> NEW_RANGE     | APENAS no startup (estado inicial)
                                 | Primeira leitura de dados validos resolve para
                                 | CONTRACTION ou EXPANSION
================================================================================

NOTA: NEW_RANGE e um estado transitorio de arranque. Em operacao normal
      so existem 3 estados: CONTRACTION, EXPANSION, TREND.
```

---

## 2. STRATEGY ENGINE

### Responsabilidade
Seleccionar estrategia baseada na phase. NAO executa trades. NAO recalcula phase.

### Logica de `_get_strategy_mode()`

```
phase = _get_current_phase()

SE phase == "CONTRACTION":
  -> ("RANGE_BOUND", None)

SE phase in ("TREND", "EXPANSION") E daily_trend in ("long", "short"):
  -> ("TRENDING", "LONG" se long, "SHORT" se short)

SENAO:
  -> ("RANGE_BOUND", None)
```

### Logica de `_resolve_direction(level_type)`

#### Modo RANGE_BOUND
```
liq_top -> SHORT (reversal: preco no topo = sobrevalorizado, reverter para valor)
liq_bot -> LONG  (reversal: preco no fundo = subvalorizado, reverter para valor)
```

#### Modo TRENDING (com trend_continuation_enabled = true)
```
PASSO 1 — PULLBACK (prioridade):
  trend=LONG  + level=liq_bot -> PULLBACK LONG  ("buy the dip")
  trend=SHORT + level=liq_top -> PULLBACK SHORT ("sell the rally")

PASSO 2 — CONTINUATION (se nao e pullback):
  Todas estas condicoes DEVEM ser verdadeiras:
    1. phase in (TREND, EXPANSION)
    2. _detect_trend_displacement(direction) = True
    3. _detect_local_exhaustion(direction) = False
    4. V3 momentum nao bloqueia (se trend_cont_require_v3_pass=true)
    5. Iceberg nao e contra forte (ou allow_neutral_iceberg=true)
  Se TODAS passam -> CONTINUATION

PASSO 3 — OVEREXTENSION (fallback):
  trend=LONG + level=liq_top:
    distancia = |preco - liq_top|
    SE distancia > ATR_M30 * 1.5 -> SHORT (reversal overextended)
    SENAO -> SKIP
  trend=SHORT + level=liq_bot:
    distancia = |liq_bot - preco|
    SE distancia > ATR_M30 * 1.5 -> LONG (reversal overextended)
    SENAO -> SKIP

PASSO 4 — SKIP (nenhum valido)
```

#### Modo TRENDING (com trend_continuation_enabled = false)
```
Logica original Sprint 8:
  PULLBACK: liq_bot em uptrend -> LONG, liq_top em downtrend -> SHORT
  OVEREXTENSION: preco > 1.5*ATR alem da zona -> reversal
  SENAO: SKIP
  (CONTINUATION nao existe)
```

### Logica de `_detect_trend_displacement(direction)`

```
INPUT:
  Le M5 parquet (timing fino — NAO para phase)
  Ultimas 3 barras completadas
  ATR_M30 actual

PARA CADA barra (da mais recente para tras):
  bar_range = high - low
  SE bar_range < ATR_M30 * 0.8 -> skip (barra fraca)

  SE direction = "LONG":
    SE close <= open -> skip (nao bullish)
    SE |delta| > 0 E delta < 80 -> skip (delta fraco)
    SE (close - low) / range < 0.7 -> skip (close nao perto da maxima)
    -> VALIDO (displacement_low = low, displacement_high = high)

  SE direction = "SHORT":
    SE close >= open -> skip (nao bearish)
    SE |delta| > 0 E delta > -80 -> skip (delta fraco)
    SE (high - close) / range < 0.7 -> skip (close nao perto da minima)
    -> VALIDO

  SE nenhuma barra valida nas 3 -> False

THRESHOLDS (settings.json):
  trend_cont_displacement_atr_mult: 0.8   (range minimo = 80% ATR)
  trend_cont_min_delta_1bar: 80           (delta minimo na barra)
  trend_cont_close_near_extreme_pct: 0.7  (close nos 70% superiores/inferiores)
```

---

## 3. EXECUTION ENGINE

### Responsabilidade
Decisao final: GO | BLOCK. NAO redefine phase. NAO recalcula estrategia.

### Gate Chain: `GateChain.check() -> GateDecision`

#### V1 — Structure (Zone Check)
```
RANGE mode:
  dist = abs(price - level)
  SE dist <= 8.0pts -> PASS
  SENAO -> ZONE_FAIL

TRENDING mode:
  SE box_low <= price <= box_high -> PASS (in-box-zone)
  SENAO -> ZONE_FAIL

Threshold: V1_RANGE_DIST_PTS = 8.0
```

#### V2 — Entry Quality (L2 / DOM)
```
dom_imbalance = media dos ultimos 30min de dom_imbalance do microstructure
SE dom_imbalance >= 17.03 E alinhado com direcao -> PASS (+2 score)
SE dom_imbalance < 17.03 -> N/A (neutro)
SE iceberg_proxy >= 0.85 E contra -> BLOCK (-4 score)

Thresholds:
  dom_imbalance_threshold: 17.03   (calibrado, grid search)
  iceberg_proxy_threshold: 0.85    (CAL-20)
  iceberg_hard_block_on_contra: true
```

#### V3 — Momentum / Exhaustion
```
Com delta_4h_inverted_fix = true (activo em producao):

  PASSO 1 — Delta 4H Exhaustion:
    SE d4h > +3000:
      SHORT -> ok, score +2    ("buyer exhaustion supports SHORT")
      LONG  -> warn, score -2  ("buyer exhaustion penalizes LONG")
    SE d4h < -1050:
      LONG  -> ok, score +2    ("seller exhaustion supports LONG")
      SHORT -> warn, score -2  ("seller exhaustion penalizes SHORT")
    SE -1050 <= d4h <= +3000:
      -> ok, score 0           ("neutral")

  PASSO 2 — Impulse 30min (so se PASSO 1 nao bloqueou):
    SE direction=SHORT E price_1bar > +5pts E delta_1bar > +100:
      -> block, score -2
      reason: "[blocked_by=impulse_30min] SHORT: +Xpts / delta=+Y"
    SE direction=LONG E price_1bar < -10pts E delta_1bar < -100:
      -> block, score -2
      reason: "[blocked_by=impulse_30min] LONG: Xpts / delta=Y"

  PASSO 3 — Tag:
    SE status in (warn, block) E nao e impulse:
      reason tagged com "[blocked_by=delta_4h_exhaustion]"

Thresholds:
  delta_4h_exhaustion_high: 3000   (CAL-16/17)
  delta_4h_exhaustion_low: -1050   (CAL-16/17)
  IMPULSE_BLOCK_SHORT_PTS: +5     (CAL-20/21)
  IMPULSE_BLOCK_LONG_PTS: -10     (CAL-20/21)
  IMPULSE_DELTA_THRESH: 100       (CAL-21)
```

#### V4 — Institutional Signals (Iceberg)
```
Le iceberg events dos ultimos 10 min dentro de +/-1pt do preco de entrada

SE iceberg detectado:
  SE alinhado com direcao -> PASS (+3 score)
  SE contra E absorption_ratio >= 12.28 E loi >= 0.14:
    SE iceberg_hard_block_on_contra = true -> BLOCK (-4 score)
  SE neutro ou fraco -> NEUTRAL (0 score)

SE nao detectado -> UNKNOWN (0 score)

Thresholds:
  ICE_PRICE_BAND_PTS: 1.0
  ICE_LOOKBACK_MIN: 10
  ICE_MIN_REFILLS: 2
  ICE_MIN_PROB: 0.20
  iceberg_contra_min_absorption_ratio: 12.28  (calibrado)
  iceberg_contra_min_loi: 0.14                (calibrado)
```

### Decisao Final
```
total_score = sum(V1.score, V2.score, V3.score, V4.score)
MIN_SCORE_GO = 0

SE V1 = ZONE_FAIL -> BLOCK (independente do score)
SE V3 = block -> BLOCK (impulse ou exhaustion)
SE V4 = BLOCK E iceberg_hard_block -> BLOCK
SE total_score < 0 -> BLOCK
SENAO -> GO
```

### Output
```
decision_live.json:
  timestamp, decision_id, price_mt5, price_gc
  decision: {action, direction, reason, total_score, sl, tp1, tp2, lots}
  gates: {v1_zone, v2_l2, v3_momentum, v4_iceberg}  (status + reason cada)
  context: {phase, daily_trend, m30_bias, ...}

decision_log.jsonl:
  mesma estrutura, append-only (1 linha por decisao)
```

---

## 4. EXHAUSTION FILTER

### Responsabilidade
Evitar entradas ruins em TREND_CONTINUATION. NAO se aplica a PULLBACK nem RANGE.

### Logica de `_detect_local_exhaustion(direction, decision)`

```
ACTIVOS (bloqueiam):

  1. OVEREXTENSION:
     SE direction=LONG E preco > liq_top:
       dist = |preco - liq_top|
       SE dist > ATR_M30 * 1.5 -> EXHAUSTED
       reason: "overextended LONG: Xpts > Ypts (1.5x ATR)"
     SE direction=SHORT E preco < liq_bot:
       dist = |liq_bot - preco|
       SE dist > ATR_M30 * 1.5 -> EXHAUSTED

  2. IMPULSE 30min:
     SE decision.momentum.status == "block" E "impulse" in reason:
       -> EXHAUSTED
       reason: "impulse_30min blocks continuation: [reason]"

  3. ICEBERG CONTRA FORTE:
     SE decision.iceberg.detected = true
        E decision.iceberg.aligned = false
        E decision.iceberg.confidence > 0.5:
       -> EXHAUSTED
       reason: "iceberg contra forte: conf=X.XX [reason]"

  SE nenhum activo disparou -> NOT EXHAUSTED

SHADOW (log only, prefixo [EXHAUSTION_SHADOW]):

  1. DELTA WEAKENING:
     recent_delta = sum(bar_delta ultimas 10 barras micro)
     older_delta = sum(bar_delta barras 11-20 micro)
     weakening_rate = 1.0 - abs(recent / older)
     SE weakening_rate > 0.139486 -> log

  2. D4H NEAR EXHAUSTION:
     SE direction=LONG E d4h > 3000 * 0.8 (=2400) -> log
     SE direction=SHORT E d4h < -1050 * 0.8 (=-840) -> log

  3. VOLUME CLIMAX:
     rolling_std = std(bar_delta ultimas 30 barras)
     SE |last_bar_delta| > rolling_std * 1.682 -> log

Thresholds:
  overextension_atr_mult: 1.5                    (manual)
  IMPULSE_BLOCK_SHORT_PTS: +5, LONG: -10        (CAL-20/21)
  IMPULSE_DELTA_THRESH: 100                      (CAL-21)
  iceberg confidence threshold: 0.5              (manual)
  delta_weakening_threshold: 0.139486            (calibrado, grid search)
  vol_climax_multiplier: 0.68206                 (calibrado, grid search)
```

### Integracao
```
APENAS chamado quando:
  strategy == "TREND_CONTINUATION"
  dentro de _get_trend_entry_mode()

NUNCA chamado para:
  RANGE_BOUND
  TREND_PULLBACK
  Overextension reversal
```

---

## 5. TELEGRAM — VIEW LAYER

### Regra Absoluta
Telegram NAO calcula nada. Le apenas:
- `service_state.json` (phase, feed, m5, m30, delta_4h, atr, bias, last_gate_at)
- `decision_live.json` (gates, decision, decision_id)

### ENTRY_GO
```
Trigger: notify_decision() apos _write_decision com action="EXECUTED"
Anti-spam: dedup por decision_id

Formato:
  ENTRY — {direction}
  Price: {price_mt5}
  SL: {sl} ({sl_dist} pts)
  TP1: {tp1} ({tp1_dist} pts) | TP2: {tp2}
  Runner: ON
  Score: {total_score} | R:R: {rr}
  Lots: L1={} L2={} L3={}
  Context:
  Phase: {phase} | Bias: {bias}
  D4h: {d4h} | ATR: {atr}
  Gates:
  V1: {icon} | V2: {icon} | V3: {icon} | V4: {icon}
  ID {decision_id} | {timestamp}
```

### BLOCK
```
Trigger: notify_decision() apos _write_decision com action="BLOCK"
Anti-spam: dedup por decision_id

Formato:
  BLOCK — {direction}
  Price: {price_mt5}
  Blocked by: {gate que bloqueou}
  Reason: {reason}
  Context:
  Phase: {phase} | Bias: {bias}
  D4h: {d4h}
  Gates:
  V1: {icon} | V2: {icon} | V3: {icon} | V4: {icon}
  ID {decision_id} | {timestamp}
```

### HEALTH CHECK
```
Trigger: heartbeat loop (cada 30s), anti-spam filtra
Anti-spam: min 2 min entre msgs, 15 min periodico, OU mudanca de estado

Formato:
  HEALTH CHECK — FluxQuantumAI
  Phase: {phase}
  Bias: {bias} | Session: {session}
  Feed: {feed} | M5: {m5} | M30: {m30}
  D4h: {d4h} | ATR: {atr}
  Last Gate: {last_gate_at}
  Gates:
  V1: {icon} | V2: {icon} | V3: {icon} | V4: {icon}
  {status_line}

Mudancas que disparam envio imediato:
  - phase mudou
  - feed_status mudou
  - m30_bias mudou
  - last_gate_at mudou
  - gates mudaram
```

### EXHAUSTION (via BLOCK)
```
Quando exhaustion bloqueia TREND_CONTINUATION:
  action = "BLOCK" em decision_live.json
  reason contem "exhaustion: overextended..." ou "impulse_30min..." ou "iceberg contra..."
  notify_decision() envia BLOCK com o reason exacto
  NAO existe mensagem separada de exhaustion — e um BLOCK normal com reason especifico
```

---

## 6. VALIDATION CHECKLIST

### Phase Engine
- [x] No rapid flipping (2 min hysteresis)
- [x] No M5 dependency (M30 only)
- [x] CONTRACTION = price inside box
- [x] EXPANSION = price outside box (breakout/JAC pendente)
- [x] TREND requires ALL 4 conditions: confirmed + daily_trend + box ladder (3 FMVs) + temporal acceptance
- [x] PATCH 1: _count_bars_outside_box() implemented (threshold=4, calibrated CAL-PATCH1)
- [x] CAL-PATCH1: T=4 calibrated (83.3% fake filter, 100% valid capture, 142 events, Jul 2025-Abr 2026)
- [x] CONTRACTION->TREND impossible (must go through EXPANSION first)
- [x] Transition table documented with all valid phase changes
- [x] Persists last valid phase on error

### Strategy Engine
- [x] RANGE_BOUND: reversal at extremes
- [x] TRENDING/PULLBACK: buy dip / sell rally
- [x] TRENDING/CONTINUATION: displacement + no exhaustion (OFF by default)
- [x] No execution logic inside
- [x] No phase recalculation

### Execution Engine
- [x] V1-V4 gates independent
- [x] V3 impulse restored with blocked_by tag
- [x] Gate decision writes to single source of truth files
- [x] No phase recalculation in gates

### Exhaustion Filter
- [x] 3 active signals: overextension, impulse, iceberg contra
- [x] 3 shadow signals: delta_weakening, d4h_near, vol_climax
- [x] Only applies to TREND_CONTINUATION
- [x] Does NOT block pullbacks or range trades

### Data Consistency
- [x] 8082 = 8088 (same phase, same liq, same gates)
- [x] Telegram reads files only
- [x] /api/live = decision_live.json
- [x] /api/system_health = service_state.json
- [x] No fallback calculations in dashboards

### Pending Live Validation
- [ ] First gate check creates decision_live.json
- [ ] Phase stable during market session
- [ ] TREND_CONTINUATION enabled in shadow mode
- [ ] Barbara audit approval

---

**END OF DOCUMENT — AWAITING AUDIT APPROVAL BEFORE PRODUCTION DEPLOY**

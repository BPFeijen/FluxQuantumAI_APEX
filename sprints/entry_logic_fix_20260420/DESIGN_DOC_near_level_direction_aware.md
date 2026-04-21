# DESIGN DOC — `near_level` + `level_detector` Direction-Aware Fix

**Sprint:** entry_logic_fix_20260420
**Status:** DESIGN ONLY — zero code changes until Claude audit + Barbara review
**Author:** ClaudeCode (from task spec by Claude + Barbara)
**Date:** 2026-04-20
**Trigger incident:** Signal 03:14:46 UTC 2026-04-20 — SHORT emitted at MT5 4791.08 with `liq_top_mt5=4767.53` (23.55 pts **below** price)

---

## 1. EXECUTIVE SUMMARY

**Problema (3 linhas):**
`level_detector` e `_near_level` não têm consciência direccional. Quando a confirmed M5 box fica stale (>15min) e o fallback unconfirmed cai no lado errado do preço relativamente à direcção de entrada (ex: `liq_top` abaixo do preço para SHORT), o sistema valida a entrada como se o level fosse resistência/suporte válido. O resultado são entradas prematuras fora da zona técnica relevante.

**Solução (3 linhas):**
Substituir o retorno scalar do `level_detector` por uma lista ranqueada de `LevelCandidate`, filtrável por direcção, frescura e posição relativa ao preço. Fazer `_near_level` exigir `is_valid_direction=True` antes de passar V1. Adicionar re-avaliação quando um box fresco no lado correcto emerge após um signal ter sido emitido mas antes de execução.

**Impacto esperado (qualitativo):**
- Elimina entries com level no lado errado (categoria BUEC/entry-side inversion).
- Reduz frequência de signals em janelas transitórias onde só unconfirmed wrong-side existe — fewer but cleaner trades.
- Introduz semântica "FAR — zona já passou" explícita, habilitando lógica futura de wait-for-retrace.

---

## 2. ARCHITECTURE ANÁLISE — ESTADO ACTUAL

### 2.1 `level_detector.py` actual

**Função pública principal:** `get_current_levels() -> dict | None` (`level_detector.py:373`)

**Input:** Nenhum (lê estado de parquets).
**Output:** Dict scalar com campos:
```
liq_top, liq_bot, fmv                    # GC prices
liq_top_mt5, liq_bot_mt5, fmv_mt5        # XAUUSD prices
box_high, box_low, box_high_mt5, box_low_mt5
atr_14
source          # "m5_box" | "m5_box_unconfirmed" | "m30_box" | "m30_box_unconfirmed"
box_id, box_age_h
daily_trend, m30_bias, m30_bias_confirmed, provisional_m30_bias
m30_liq_top, m30_liq_bot, m30_fmv, m30_box_id
```

**Side effects:** Logs (info/warning/error). Nenhum state interno mantido — é uma free function que relê parquets a cada invocação.

**Estado interno em `event_processor` (cliente do detector):**
`self.liq_top`, `self.liq_bot`, `self.liq_top_gc`, `self.liq_bot_gc`, `self.box_high`, `self.box_low`, `self.m30_liq_top`, `self.m30_liq_bot`, `self.daily_trend`, `self.m30_bias` etc. — todos scalars copiados do dict retornado por `get_current_levels()`.

**Constantes de staleness:**
```python
M5_STALE_WARN_H  = 0.5   # warn se confirmed M5 box > 30min  (level_detector.py:79)
M5_FALLBACK_H    = 0.25  # fallback para unconfirmed se confirmed > 15min (level_detector.py:80)
M30_STALE_WARN_H = 4.0
M30_FALLBACK_H   = 4.0
```

**Lógica de fallback M5 (resumo, ver `level_detector.py:401-435`):**
1. Carrega `m5_df` de `gc_m5_boxes.parquet`.
2. Filtra `confirmed_m5 = m5_df[m5_box_confirmed == True]`.
3. Pega a última linha confirmada → calcula `box_age_h`.
4. Se `box_age_h > M5_FALLBACK_H (0.25h)`:
   - Procura unconfirmed com `m5_box_id > last_confirmed_id`.
   - Se existir, emite `M5_BOX_FALLBACK` warning e retorna levels da unconfirmed.
5. Caso contrário retorna levels da confirmed.

**Flow de decisão (textual):**
```
get_current_levels()
 ├── load m5_df, m30_df
 ├── compute daily_trend, m30_bias, m30_macro
 ├── M5 PATH
 │    ├── if confirmed_m5 exists:
 │    │    ├── if fresh (age ≤ 15min) → return confirmed_m5.iloc[-1]
 │    │    └── if stale (age > 15min) AND unconfirmed with newer id exists → return unconfirmed.iloc[-1]   ← ORIGEM DO BUG
 │    └── if no confirmed → return latest unconfirmed (any id)
 └── FALLBACK: M30 path (mesma lógica, thresholds maiores)
```

**Observação crítica:** A escolha "latest unconfirmed by id" não tem filtro direccional. O id mais alto é o mais recente em tempo, não necessariamente o mais útil para a direcção intended.

### 2.2 `_near_level` actual

**Local:** `event_processor.py:1935-1994`

**Código literal (copy):**
```python
def _near_level(self, price: float) -> tuple[str, float]:
    atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
    band = max(atr * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)

    # M30 structural proximity
    m30_near_top = (self.m30_liq_top is not None
                    and abs(price - self.m30_liq_top) <= band)
    m30_near_bot = (self.m30_liq_bot is not None
                    and abs(price - self.m30_liq_bot) <= band)
    m30_match = "liq_top" if m30_near_top else ("liq_bot" if m30_near_bot else "")
    m30_price = (self.m30_liq_top if m30_near_top
                 else (self.m30_liq_bot if m30_near_bot else 0.0))

    # M5 execution proximity
    m5_near_top = (self.liq_top is not None and self.liq_top > 0
                   and abs(price - self.liq_top) <= band)
    m5_near_bot = (self.liq_bot is not None and self.liq_bot > 0
                   and abs(price - self.liq_bot) <= band)
    m5_match = "liq_top" if m5_near_top else ("liq_bot" if m5_near_bot else "")
    m5_price = (self.liq_top if m5_near_top
                else (self.liq_bot if m5_near_bot else 0.0))

    # Classify source
    if m5_match and m30_match and m5_match == m30_match:
        self._near_level_source = "m5+m30"
    elif m30_match and not m5_match:
        self._near_level_source = "m30_only"
    elif m5_match and not m30_match:
        self._near_level_source = "m5_only"
    else:
        self._near_level_source = ""

    # Return — M5 preferred for behavioral compatibility
    if m5_match:
        return m5_match, m5_price
    if m30_match:
        return m30_match, m30_price
    return "", 0.0
```

**Invariantes assumidos (incorrectamente):**
- "Se `abs(price - liq_top) <= band`, então `liq_top` é resistência válida para SHORT" — **FALSO quando `price > liq_top`**.
- "O level retornado por `level_detector` está topologicamente no lado correcto do preço para a direcção intended" — **FALSO em regime fallback**.
- "Invocador vai usar `level_type` retornado (`liq_top`/`liq_bot`) para derivar direction" — Tecnicamente verdade, mas a derivação é `direction = SHORT if level_type=='liq_top' else LONG`, e isto ignora posição relativa.

**Quando é invocado:** No trigger ALPHA loop (via `_check_alpha_trigger` / `_patch2a_continuation_trigger`). O retorno alimenta:
- `self._near_level_source` (telemetria)
- Decisão de direcção do signal (via convention `liq_top → SHORT`)
- Campo `near_level_source` no `decision_log`

### 2.3 `_trending_v1` actual

**Local:** `event_processor.py:2396-2402` (inline, não é função separada)

```python
_trending_v1 = self.daily_trend in ("long", "short") and self.box_high is not None and self.box_low is not None
if _trending_v1:
    _in_zone = self.box_low <= price <= self.box_high
    v1 = "PASS" if _in_zone else "ZONE_FAIL"
else:
    _level_for_v1 = self.liq_top if direction == "SHORT" else self.liq_bot
    v1 = "PASS" if abs(price - _level_for_v1) <= 8.0 else "NEAR"
```

**Como usa `box_high`/`box_low`:** Define "trending zone" como intervalo `[box_low, box_high]` populado a partir do último box do `level_detector`. Se o preço está dentro → V1 PASS (independente de direcção ou lado).

**Route alternativa (signal 03:14:46):**
No momento do signal 03:14, `daily_trend="long"` e `box_high`/`box_low` populados → `_trending_v1 = True`. O check foi `box_low ≤ 4791.08 ≤ box_high`. Como V1 passou (registado como PASS no `decision_live.json`), o `box_high`/`box_low` na altura continha o preço — mas o `m30_box_mt5=[4792.93, 4797.53]` reportado no decision não. Há inconsistência aparente entre `box_high/box_low` usados em V1 e os levels M30 reportados no decision_log — a investigar durante implementação (não bloqueia o design).

**Consequência:** `_trending_v1` **nunca filtra por direcção nem por lado do preço relativamente aos boxes** — assume que estar "dentro do box" é suficiente.

### 2.4 Diagrama de dados — box → level → decisão

```
[m5_updater.py] (60s cron)
   └─ escreve gc_m5_boxes.parquet com m5_box_id, m5_box_confirmed, m5_liq_top, m5_liq_bot

[level_detector.get_current_levels()]  (cada invocação)
   ├─ lê parquet
   ├─ escolhe 1 box (confirmed fresh > unconfirmed fallback > M30 fallback)
   └─ retorna DICT SCALAR {liq_top, liq_bot, ...}

[event_processor._refresh_levels()]  (periódico + no trigger)
   ├─ chama get_current_levels()
   └─ copia dict → self.liq_top, self.liq_bot, self.box_high, self.box_low, ...

[event_processor._near_level(price)]  (dentro ALPHA trigger loop)
   ├─ compara price vs self.liq_top / self.liq_bot com ATR band (abs)
   ├─ retorna (level_type, level_price)
   └─ side effect: self._near_level_source = "m5_only" | "m30_only" | "m5+m30" | ""

[event_processor._check_alpha_trigger]
   ├─ if level_type == "liq_top" → direction = "SHORT"
   ├─ chama self.gate.check(entry_price, direction, liq_top, liq_bot, box_high, box_low, ...)
   └─ V1 PASS path decide por _trending_v1 OU abs(price - level) <= 8

[decision_gates / gate.check]
   └─ produz decision.go=True/False com score, v4_status, etc.

[event_processor._write_decision]
   └─ escreve decision_live.json + decision_log.jsonl
```

**Ponto único de falha:** O dict retornado por `level_detector` é scalar e não carrega informação "para que direcção este level é válido". O consumer tem de inferir — e infere errado em regime fallback wrong-side.

---

## 3. PROBLEMA FORMAL

### 3.1 Caso de falha reconstruído — sinal 03:14:46

**Timeline dos M5 boxes nas horas anteriores (do `service_stderr.log`):**

| box_id | Status no momento 03:14 | liq_top_gc | liq_bot_gc | Observação |
|---|---|---|---|---|
| 33980 | confirmed, ~63min stale | (anterior) | — | confirmed reference do engine |
| 33981 | unconfirmed, lado correcto (abaixo) | 4787.15 | 4779.20 | usado via fallback |
| 33982 | ainda não existia | — | — | |
| 33983 | ainda não existia como confirmed | — | — | confirma-se ~1h depois |
| 33984 | **ainda não existia** | (eventual 4831.55, 4826.40 GC ≈ 4812.55, 4817.70 MT5) | — | emerge ~04:00-04:30 |
| 33985 | **ainda não existia** | (eventual 4828.05, 4833.65 GC ≈ 4809.05, 4814.65 MT5) | — | emerge ~04:15-04:45 |

**Valores no momento do signal (de `decision_live.json`):**
- `price_mt5 = 4791.08`
- `liq_top_mt5 = 4767.53` (= `liq_top_gc=4787.15 - offset=19.62`)
- `liq_bot_mt5 = 4759.58`
- `proximity_pts = 23.6` (distância abs de price a liq_top)
- `near_level_source = "m5_only"`
- `level_type = "liq_top"` → direction derivada = SHORT
- `m30_bias = "bearish"`, `m30_bias_confirmed = true`
- `daily_trend = "long"` (CONFLITO)
- `d1h4_bias.direction = "LONG STRONG"` (CONFLITO)
- `v1_zone.status = "PASS"` (via `_trending_v1` route)

**Razão V1 PASS (análise):**
Como `daily_trend="long"` e `box_high`/`box_low` estavam populados, o ramo `_trending_v1 = True` foi escolhido. Nele, `_in_zone = (box_low ≤ 4791.08 ≤ box_high)` retornou True. O ramo alternativo (`abs(price - liq_top) ≤ 8.0`) teria **falhado** (23.6 > 8.0) → `v1 = "NEAR"`, não "PASS". Ou seja: o gate V1 não bloqueou porque o preço estava dentro do box trending, mas o campo `liq_top=4767.53` usado pelo trigger não fazia sentido direccional.

**Levels "lado correcto" que existiam ~40-60min depois:**
- box_33984: `liq_top_mt5=4817.70, liq_bot_mt5=4812.55` — **acima** do preço (resistência real para SHORT)
- box_33985: `liq_top_mt5=4814.65, liq_bot_mt5=4809.05` — **acima** do preço (zona de entrada ideal 4807-4813)

**Se o sistema tivesse esperado estes boxes:**
- Entry SHORT @ 4809-4814 (vs 4791.08 real)
- R:R significativamente melhor (entrada perto de resistência activa)
- Invalidação clara: rompimento de `liq_top_mt5=4817.70`

### 3.2 Tabela de 4 cenários

| # | Confirmed box | Fallback unconfirmed | Comportamento actual | Comportamento correcto |
|---|---|---|---|---|
| 1 | Fresh (≤15min), lado correcto para direction | N/A | ✅ PASS — level = liq_top/liq_bot da confirmed | ✅ PASS — idêntico |
| 2 | Fresh (≤15min), lado errado relativo a direction | N/A | ⚠ PASS via `_trending_v1` OU via `abs≤band` — bug não investigado em produção mas idêntico à raiz do #4 | REJECT (ou emit `FAR` com metric) |
| 3 | Stale (>15min) + fallback unconfirmed lado correcto | Usado | ✅ PASS — warning `M5_BOX_FALLBACK` | ✅ PASS — idêntico, mas validando direcção explicitamente |
| 4 | Stale (>15min) + fallback unconfirmed lado errado | Usado | ❌ BUG — PASS por `abs(price-level)≤band` OU `_trending_v1`, level está no lado errado | REJECT — emit `FAR`/`NO_VALID_LEVEL` metric; opcionalmente triggerar "wait-for-retrace" |

**Detalhe #2:** Este cenário é possível se o último confirmed box foi "violado" pelo preço mas ainda está fresh em tempo. Exemplo: M5 box confirmed às 03:10 com `liq_top=4790`, price sobe até 4800 às 03:14 — box ainda fresh mas já ultrapassado. Sistema actual aceitaria SHORT com "liq_top=4790" como referência. Esta categoria **não está nos logs analisados** mas é logicamente idêntica à de #4 quanto ao defeito direccional.

**Detalhe #3:** Este é o happy path do fallback. Funciona hoje; design novo apenas formaliza a condição "lado correcto" em vez de o assumir por casualidade.

**Detalhe #4 (o caso 03:14):** Cerne do fix.

---

## 4. SOLUÇÃO PROPOSTA — CONTRATO

### 4.1 Nova API do `level_detector`

**Novo tipo:**
```python
@dataclass(frozen=True)
class LevelCandidate:
    box_id: int
    level: float                       # liq_top para SHORT, liq_bot para LONG (em MT5 space)
    level_gc: float                    # mesmo nível em GC space
    source: Literal[
        "m5_confirmed", "m5_unconfirmed",
        "m30_confirmed", "m30_unconfirmed",
    ]
    age_min: float                     # idade desde last_confirmed_bar (ou last_seen se unconfirmed)
    distance_to_price: float           # sempre ≥ 0; é `|level - price|`
    is_valid_direction: bool           # True sse level está no lado correcto
                                       #   SHORT: level > price
                                       #   LONG : level < price
    band: float                        # banda ATR usada na decisão near/far
    timeframe: Literal["M5", "M30"]
```

**Justificação campo-a-campo:**
- `box_id`: trace/telemetria. Permite correlacionar decisão com box histórico.
- `level`: valor a usar. Separado `level_gc` para diagnóstico (consumers que loggam GC vs MT5).
- `source`: confirma origem; desempata prioridade em lista ranqueada.
- `age_min`: frescura — usada no rank e em staleness metrics.
- `distance_to_price`: ordenação por proximidade.
- `is_valid_direction`: **o campo que falta hoje** — pré-calculado aqui para que `_near_level` não tenha de repetir lógica.
- `band`: a banda ATR aplicada na avaliação; útil para rationale em logs.
- `timeframe`: M5/M30 — necessário para priorização futura.

**Nova função pública:**
```python
def get_levels_for_direction(
    direction: Literal["SHORT", "LONG"],
    price: float,
    max_age_min: float = 15.0,           # alinhado com M5_FALLBACK_H (0.25h = 15min)
    max_distance_pts: float = 8.0,       # alinhado com band NEAR_FLOOR_PTS actual
) -> list[LevelCandidate]:
    """
    Retorna candidates ORDENADOS por:
      1. is_valid_direction DESC    (válidos primeiro)
      2. source priority DESC       (m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed)
      3. age_min ASC                (mais fresco primeiro)
      4. distance_to_price ASC      (mais perto do preço primeiro)

    max_age_min: descarta candidates mais velhos que este limite (mas marca-os se precisar telemetria).
    max_distance_pts: informativo na banda; não filtra — consumer decide.

    Retorno vazio é válido e significa "sem levels direccionalmente válidos".
    """
```

**Função legada mantida:** `get_current_levels()` continua a existir com comportamento actual durante Phase 1-2 (shadow). Internamente, pode passar a ser wrapper fino sobre a nova API escolhendo "primeiro candidate disponível sem filtro direccional", preservando backwards compat.

### 4.2 Nova lógica `_near_level`

**Assinatura proposta:**
```python
def _near_level(
    self,
    price: float,
    direction: Literal["SHORT", "LONG"] | None = None,
) -> tuple[Literal["PASS", "NEAR", "FAR"], LevelCandidate | None]:
    """
    Se direction=None, mantém comportamento legacy (para consumers que ainda não migraram).
    Se direction fornecida, aplica filtragem direction-aware.
    """
```

**Pseudo-código:**
```
if direction is None:
    # legacy path — idêntico ao actual
    return _near_level_legacy(price)

candidates = level_detector.get_levels_for_direction(
    direction=direction,
    price=price,
    max_age_min=15.0,
    max_distance_pts=8.0,
)

# Filtro 1 — apenas direccionalmente válidos
valid = [c for c in candidates if c.is_valid_direction]

if not valid:
    # Existem candidates mas todos no lado errado (price já passou o level)
    # → sinaliza FAR (distintamente de "NEAR but out-of-band")
    self._near_level_source = ""
    log.info(
        "NEAR_LEVEL FAR: direction=%s price=%.2f — all %d candidates on wrong side",
        direction, price, len(candidates),
    )
    return "FAR", None

# Top candidate (já ordenado por API)
top = valid[0]
self._near_level_source = _classify_source(top)   # "m5_only" | "m5+m30" | "m30_only"

if top.distance_to_price <= top.band:
    log.info(
        "NEAR_LEVEL PASS: direction=%s price=%.2f level=%.2f dist=%.1f band=%.1f source=%s",
        direction, price, top.level, top.distance_to_price, top.band, top.source,
    )
    return "PASS", top

# Dentro dos candidatos válidos, mas fora da banda — zona ainda à frente
log.info(
    "NEAR_LEVEL NEAR: direction=%s price=%.2f level=%.2f dist=%.1f band=%.1f source=%s (waiting for approach)",
    direction, price, top.level, top.distance_to_price, top.band, top.source,
)
return "NEAR", top
```

**Semântica dos 3 estados:**
- **`PASS`**: Há level no lado correcto, dentro da banda. Direction-aware. Entrada pode prosseguir para restantes gates.
- **`NEAR`**: Há level no lado correcto, fora da banda. Preço ainda não chegou à zona. **Não emite signal** — espera próximo tick.
- **`FAR`**: Não há level no lado correcto. Preço já ultrapassou todos os levels conhecidos, ou não há boxes válidos para esta direcção. **Não emite signal** — potencialmente triggerar wait-for-retrace (open question 9.2).

**Backwards compat `_near_level_source`:** Mantém os mesmos valores string (`"m5_only"`, `"m5+m30"`, `"m30_only"`, `""`) para não partir consumers de logging/metrics.

### 4.3 Lógica `_trending_v1` ajustada

**Problema actual:** `_trending_v1` passa automaticamente se `box_low ≤ price ≤ box_high`, sem verificar se a direcção faz sentido relativamente à posição do preço nos extremos do box.

**Proposta:**
```python
_trending_v1 = (
    self.daily_trend in ("long", "short")
    and self.box_high is not None
    and self.box_low is not None
)

if _trending_v1:
    _in_zone = self.box_low <= price <= self.box_high
    # NOVO — alinhamento HTF-M5 direction
    _htf_aligned = (
        (direction == "LONG"  and self.daily_trend == "long")
        or (direction == "SHORT" and self.daily_trend == "short")
    )
    if _in_zone and _htf_aligned:
        v1 = "PASS"
    elif _in_zone and not _htf_aligned:
        v1 = "COUNTER_HTF"   # nova label de shadow — não bloqueia durante Phase 2
    else:
        v1 = "ZONE_FAIL"
else:
    # Fallback: usa novo _near_level direction-aware
    ne_status, ne_cand = self._near_level(price, direction=direction)
    v1 = {"PASS": "PASS", "NEAR": "NEAR", "FAR": "FAR"}[ne_status]
```

**Preservado:** O score V1 e a filosofia de "prefere entradas dentro do box trending". Nenhuma mudança em `decision_gates.py` ou triggers ALPHA/BETA/GAMMA/DELTA.

**Novo:** Label `COUNTER_HTF` é shadow (Phase 2-3). Se dados mostrarem que counter-HTF entries within-zone têm win-rate aceitável, não se bloqueia; caso contrário, vira BLOCK em Phase 4.

### 4.4 Re-avaliação dinâmica

**Problema:** Entre emissão do signal e execução (fracções de segundo a segundos), pode emergir novo box no lado correcto que invalidaria ou deslocaria a entrada. Hoje o signal já escreveu decision_live.json e tg.notify(), sem lookback.

**Opções analisadas:**

**A) Cancelar pending signal se box fresco lado-correcto surge**
- Prós: Previne entradas em momento sub-óptimo quando dados acabam de chegar.
- Contras: Signal cancellation adiciona complexidade; risco de "flapping" se novos boxes chegam em burst.
- Implementação: Exigiria pending-signal queue + lookback no executor.

**B) Atualizar level mas manter signal**
- Prós: Mantém o signal mas com level correcto (melhor diagnóstico ex-post).
- Contras: Entry price já foi fixada; só melhora log, não mercado.
- Implementação: Injectar pós-facto no decision_log.

**C) Não faz nada (status quo)**
- Prós: Simples, sem risco de regressão.
- Contras: Perpetua o problema em janelas transitórias.

**Recomendação: Opção A, mas em Phase 5 separada (fora scope deste fix).**

Justificação: O root cause do 03:14 **não** é re-avaliação falhada — é emissão errada no `t=0`. A filtragem direction-aware em §4.1-4.3 já previne o caso principal (signal nem sequer seria emitido se o único level disponível estivesse no lado errado — retornaria `FAR` ou `NEAR`, e trigger ALPHA não dispararia).

Re-avaliação dinâmica é um *refinamento* para cenários onde um signal passa §4.1-4.3 (ex: level lado correcto existe mas fraco) e depois surge level claramente melhor. Isto justifica **sprint separado** depois de observar baseline Phase 4. Desta sprint: status quo na re-avaliação, mas arquitectura do `LevelCandidate` já está pronta para suportar Opção A.

---

## 5. CASOS DE TESTE

Mínimo 8 casos a implementar como testes unit/integration (não fazem parte deste design — só a especificação):

1. **Fresh confirmed + SHORT + level above price + within band** → `PASS` com `top.source="m5_confirmed"`, `top.is_valid_direction=True`
2. **Fresh confirmed + SHORT + level below price** → `FAR`. (Categoria #2 da tabela.) Candidates retornados mas todos com `is_valid_direction=False`.
3. **Stale confirmed + fallback unconfirmed correct side** → `PASS` com `top.source="m5_unconfirmed"`, warning `M5_BOX_FALLBACK` emitido.
4. **Stale confirmed + fallback unconfirmed wrong side** → `FAR`. Cerne do fix; replica o signal 03:14. Assert: nenhum signal emitido pelo trigger ALPHA.
5. **Multiple unconfirmed boxes (mistos)** → `PASS` com o mais próximo do lado correcto; candidates do lado errado presentes mas filtrados no topo.
6. **Level exactly at price (distance=0.0)** → `PASS` se within band; edge case policy documentar: considerar `is_valid_direction=True` (trata como "toque", não "já passou"). Justificar em comment do código.
7. **Level just passed price (distance=+0.1 no sentido errado)** → `FAR`. Level excluído por `is_valid_direction=False`.
8. **No valid levels at all (nem M5 nem M30)** → `FAR` com lista vazia. Log `NEAR_LEVEL FAR: no candidates available`.

**Adicionais recomendados (não-bloqueantes):**
9. M30-only path com M5 ausente mas M30 válido lado correcto → `PASS` source `m30_confirmed`.
10. M5_confirmed lado errado + M5_unconfirmed lado correcto → `PASS` escolhendo unconfirmed válido.
11. Transição de estado: tick N retorna `FAR`, tick N+1 um novo box chega e tick N+1 retorna `PASS`.

---

## 6. MIGRATION PLAN

### 6.1 Backwards compatibility

**API legada preservada:**
- `get_current_levels()` — continua a retornar dict scalar.
- `_near_level(price)` sem argumento `direction` — mantém comportamento idêntico (chama `_near_level_legacy`).
- Campo `self._near_level_source` mantém vocabulário actual.

**Novo co-existe:**
- `get_levels_for_direction(...)` — nova função paralela.
- `_near_level(price, direction=...)` — novo argumento opcional.

**Consumers identificados (para verificação):**
- `event_processor._check_alpha_trigger` → migra primeiro.
- `event_processor._patch2a_continuation_trigger` → migra em Phase 2.
- `event_processor._check_delta_trigger`, `_check_gamma_trigger` → migra em Phase 3.
- Dashboard/telemetria: apenas lê dict; sem mudança.

### 6.2 Rollout phases

| Phase | Descrição | Duração sugerida | Entry criteria |
|---|---|---|---|
| **1 — Dev/code** | Implementação da nova API com feature flag `NEAR_LEVEL_DIRECTION_AWARE = False`. Código convive; nenhum caller migrado. | 1-2 dias | Design doc aprovado |
| **2 — Shadow mode** | Flag OFF em produção mas novo código corre *em paralelo*: ambos os retornos são computados, diff logado em `C:\FluxQuantumAI\logs\near_level_shadow.jsonl` | 5 dias úteis de sessão GC (incluir 1 weekend para full M5 cycle) | Phase 1 complete + unit tests passed |
| **3 — Dry-run** | Flag ON num único caller (ex: `_check_alpha_trigger`), mas em shadow execution — emite signal mas broker não recebe (`--execute False` override) | 2 dias úteis | Phase 2 mostra <5% divergência em cenários #1 e #3; #2/#4 mostram mudança esperada (novo rejeita, velho aceitava) |
| **4 — Live (single trigger)** | Flag ON em `_check_alpha_trigger` com broker execution real | 5 dias úteis | Phase 3 sem surpresas; rejections alinhadas com expectativa |
| **5 — Rollout remaining triggers** | Migra BETA/GAMMA/DELTA/continuation | 3 dias úteis | Phase 4 sem regressão em PF/win-rate baseline |

**Timing total estimado:** ~3 semanas úteis + 1 fim de semana shadow.

### 6.3 Rollback strategy

**Mechanism:** Feature flag em `settings.json` — `near_level_direction_aware: bool`.

**Triggers para rollback imediato (em qualquer Phase ≥4):**
- PF 7-day rolling cai > 15% abaixo do baseline pré-Phase-4.
- Trade count cai > 60% vs baseline (aggressive rejection excessivo).
- Surge signal emitida com `is_valid_direction=False` em telemetria (bug no código novo).

**Procedimento:**
1. Flag OFF (edit settings.json, restart serviço FluxQuantumAPEX).
2. Código legacy volta a ser usado.
3. Post-mortem em `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\ROLLBACK_POSTMORTEM.md`.

**Idempotência:** Dados escritos em Phase ≥4 (decision_log.jsonl) incluem `near_level_dir_aware: true/false` para que análises retrospectivas saibam separar os dois regimes.

---

## 7. VALIDATION PLAN

### 7.1 Backtest validation

**Dataset:** `decision_log.jsonl` (5.5 dias úteis de sessão GC, ~8423 decisões incluindo 531 GO signals pós-Apr 17).

**Metodologia:**
1. Para cada decisão historical, replay com novo `level_detector` e `_near_level`.
2. Classificar outcome:
   - **Idêntica**: mesma decisão (GO/BLOCK), mesmo level referenciado.
   - **Rejeitada (nova)**: velho=GO, novo=BLOCK via `FAR` ou `NEAR`. Reportar distância + side.
   - **Emitida com level diferente**: velho=GO, novo=GO mas level_type/level_price diferente. Raro — só se múltiplos boxes coexistiam.
   - **Nova GO** (inesperada): velho=BLOCK por `_near_level_source=""`, novo=PASS porque encontrou candidate que o velho não considerou. Não deve acontecer (novo é estritamente mais restritivo); se acontecer, bug no novo.

**Métricas finais:**
- % decisões afectadas (target: 3-10%)
- % GO signals rejeitadas (target: 5-15%) — principalmente cenário #4
- Distribuição por `source` dos signals rejeitados (esperado: maioria `m5_unconfirmed`)
- PF teórico ex-post das GOs que teriam sido rejeitadas (sanity check — se PF > baseline, estamos a rejeitar bons trades; se PF < baseline, filtro está a remover ruído correctamente)

**Output:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\BACKTEST_COUNTERFACTUAL.md` com tabela de impacto e ACK/NACK por Barbara.

### 7.2 Live empirical validation

**Após Phase 3 (dry-run):**
- Monitorização 48h contínuas em live mas com `--execute False`.
- Log todos os `FAR` / `NEAR` / `PASS` emitidos.
- Métricas por direction:
  - Count `PASS` vs `NEAR` vs `FAR`.
  - Distribuição temporal (para identificar janelas-problema).
  - Correlação com `M5_BOX_FALLBACK` warnings.

**Threshold go/no-go Phase 4:**
- Rejection rate em `FAR` entre 5-20% (se >20%, filtro é demasiado aggressive; se <5%, bug não existia em produção — sanity check).
- Zero emissões com `is_valid_direction=False` (seria bug).

---

## 8. RISCOS IDENTIFICADOS

| # | Risco | Likelihood | Impact | Mitigação |
|---|---|---|---|---|
| R1 | Redução aggressive de signals (trade count cai demasiado) | Média | Alto | Shadow em Phase 2 quantifica antes de live; Phase 3 dry-run confirma. Flag permite rollback rápido. |
| R2 | Edge cases não cobertos nos 8 testes (ex: M5+M30 conflito, boxes deletados, NaN) | Média | Médio | Testes adicionais #9-11 + property-based tests sobre combinações de 3 boxes. |
| R3 | Performance — lista de candidates em vez de scalar | Baixa | Baixo | Lista tipicamente 1-4 elementos; ordenação é O(n log n). Parquet I/O domina. Benchmark em Phase 1. |
| R4 | Concorrência — novo código mantém estado interno? | Baixa | Alto | Mantém padrão actual: `level_detector` sem estado; `event_processor` escreve state em single thread. Verificar em code review. |
| R5 | Quebra semântica para consumers fora de `event_processor` (dashboard, backtest replayer) | Baixa | Médio | `get_current_levels()` legado preservado. Grep consumers em PR. |
| R6 | Flip-flop entre `PASS` e `FAR` se price oscila em volta de um level | Média | Médio | Hysteresis opcional (band interior para PASS, band exterior para perder PASS). Sprint separado se observado em Phase 2. |
| R7 | Interacção com defense_mode (DEFENSE_MODE veto SHORT) — novo filtro sobrepõe-se? | Baixa | Médio | `_near_level` é upstream de defense_mode; ambos podem vetar independentemente. Documentar na implementação. |
| R8 | "Wait for retrace" — pressão para implementar durante este sprint | Média | Baixo | Explicitamente fora scope (§4.4 Opção A deferida). Rejeitar scope creep. |

---

## 9. OPEN QUESTIONS

1. **Threshold `max_distance_pts=8.0` mantém-se?**
   FASE II mostrou 8.0 razoável para caso simétrico (`abs`). Com filtragem direccional, o preço só se aproxima pelo lado correcto — banda pode potencialmente ser mais generosa (ex: 10-12pts). Recomendação: manter 8.0 em Phase 2 shadow; calibrar data-driven em sprint seguinte se shadow mostrar rejeições marginais borderline.

2. **Quando `_near_level == FAR` (zona já passou), devemos:**
   - (a) Reject silenciosamente
   - (b) Log and emit metric (preferível)
   - (c) Trigger "wait for retrace" mode
   - **Recomendação:** (b) em Phase 1-4; (c) em sprint futuro depois de baseline estável.

3. **Múltiplos M5 + M30 levels concorrentes — priorização?**
   A ordem proposta é `is_valid_direction > source > age > distance`. Alternativa: `is_valid_direction > distance > source > age` (privilegia proximidade sobre frescura). **Decisão para Barbara.**

4. **Staleness threshold (15min) é ajustável data-driven?**
   Valor actual em `level_detector.py:80` é hardcoded. Sugestão: mover para `settings.json` como `m5_fallback_threshold_min` para facilitar calibração sem deploy. Baixa prioridade.

5. **Chumbar signals durante `M5_BOX_FALLBACK`?**
   Opção conservadora: recusar todos os signals quando source=m5_unconfirmed (ou m30_box*). Opção actual (pós-fix): aceitar se `is_valid_direction=True`. A opção conservadora é mais simples de defender; a actual é mais permissiva. **Decisão para Barbara.**

6. **Edge case: level igual ao preço (distance=0.0)** (ver teste #6).
   Tratar `distance=0` como `is_valid_direction=True` (PASS) ou True mas com sub-flag `is_touch=True`? Decidir na implementação.

7. **Backtest counterfactual sample size (5.5 dias, 531 GOs):**
   Sample limitado. Validação adicional com replay de Oct 2025 - Apr 2026 (Databento já extraído — ver `reference_databento_gc_extract`) tornaria resultados mais robustos. Decidir se é pré-requisito para Phase 4 ou nice-to-have.

---

## 10. REFERÊNCIAS

- **Memória:** `feedback_critical_analysis_ats` — BUEC/entry-side inversion categoria.
- **Memória:** `feedback_ats_workflow_process` — docs → brainstorm → alinhamento → implementação.
- **Código — consumer:** `C:\FluxQuantumAI\live\event_processor.py`
  - `:1935-1994` — `_near_level`
  - `:2396-2402` — `_trending_v1` inline
  - `:2488-2493` — `_build_decision_dict(trigger="ALPHA", ...)`
  - `:2320-2340` — P0 operational gates + pre-entry check
  - `:2376-2393` — chamada a `gate.check`
  - `:510`, `:1675`, `:4363` — assignments a `self.liq_top`
- **Código — detector:** `C:\FluxQuantumAI\live\level_detector.py`
  - `:79-84` — staleness constants
  - `:373-387` — `get_current_levels` docstring
  - `:401-435` — M5 fallback confirmed→unconfirmed path
  - `:340-366` — `_validate_m5_vs_m30` (shadow warning existente)
- **Dados incident:**
  - `C:\FluxQuantumAI\logs\decision_live.json` — signal 03:14:46 completo
  - `C:\FluxQuantumAI\logs\service_stderr.log` — sequência de `M5_BOX_FALLBACK` desde box_33919
  - `C:\data\iceberg\iceberg__GC_XCEC_20260420.jsonl` — iceberg BID absorção
- **Dados histórico:** `s3://fluxquantumai-data/processed/databento_daily/l2_*.parquet` (126 dias Jul–Nov 2025) para validation adicional.
- **Config:** `C:\FluxQuantumAI\settings.json` — onde adicionar `near_level_direction_aware` flag.

---

## APÊNDICE A — Invariantes/contratos a assegurar em tests

1. `get_levels_for_direction("SHORT", price, ...)` nunca retorna candidate com `level ≤ price` marcado como `is_valid_direction=True`.
2. `get_levels_for_direction("LONG", price, ...)` nunca retorna candidate com `level ≥ price` marcado como `is_valid_direction=True`.
3. Lista retornada está ordenada — não é responsabilidade do caller reordenar.
4. Chamar `get_levels_for_direction` duas vezes com input idêntico e dataset idêntico retorna resultado idêntico (determinismo).
5. Se `max_age_min=0`, retorna lista vazia (edge).
6. `source` priority: `m5_confirmed` > `m5_unconfirmed` > `m30_confirmed` > `m30_unconfirmed` (ordenação estrita).
7. `_near_level` com `direction` fornecida nunca retorna `("PASS", None)` — PASS implica candidate não-None.
8. `_near_level` com `direction=None` é byte-idêntico ao legacy (preservado para consumers não migrados).

---

## APÊNDICE B — Telemetria nova recomendada

A instrumentar em Phase 1:

- `near_level.pass.m5_confirmed` — counter
- `near_level.pass.m5_unconfirmed` — counter
- `near_level.near.by_direction.{short,long}` — counter
- `near_level.far.by_direction.{short,long}` — counter
- `near_level.far.reason.no_candidates` — counter
- `near_level.far.reason.all_wrong_side` — counter
- `near_level.shadow_diff.count` — diffs entre legacy e novo em Phase 2
- Log estruturado JSON em `near_level_shadow.jsonl`:
  ```json
  {"ts": "...", "direction": "SHORT", "price": 4791.08,
   "legacy": {"source": "m5_only", "level": 4767.53},
   "new":    {"status": "FAR", "candidates": [...]}}
  ```

---

**Fim do design doc. Aguarda Claude audit + Barbara review antes de arrancar implementação.**

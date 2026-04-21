# PATCH 2A — Trend Day Coverage

## Problema

TREND_CONTINUATION está activado (settings.json) mas **nunca dispara** porque:

```
_near_level() só retorna trigger quando:
  abs(price - liq_top) <= 1*ATR  (~30pts)
  abs(price - liq_bot) <= 1*ATR

Cenário de hoje (14 Abr):
  price = 4810 GC
  liq_top = 4800
  liq_bot = 4780
  -> price 10pts acima de liq_top = NEAR -> trigger dispara -> mas resolve como SHORT (contra-trend)
  -> quando price sobe para 4820+ -> fora da band -> ZERO triggers
```

O sistema precisa de um **trigger alternativo** que não dependa de proximidade a liq levels.

## Solução: Continuation Trigger

Adicionar ao tick loop um segundo path de trigger para CONTINUATION:

```
A cada tick, SE:
  1. phase in (EXPANSION, TREND)
  2. daily_trend definido (long ou short)
  3. price FORA da box na direcção da trend:
     - LONG: price > box_high
     - SHORT: price < box_low
  4. NÃO é pullback (price NÃO está perto de liq_bot em uptrend)
  5. Cooldown respeitado (GATE_COOLDOWN_S entre triggers)
  6. DWELL_STALE não bloqueia

ENTÃO:
  Chamar _get_trend_entry_mode() com level_type contextual
  Se retorna CONTINUATION -> _trigger_gate() com direction da trend
  Se retorna SKIP -> log e continuar
```

## Critérios de entrada (já implementados no _get_trend_entry_mode)

O CONTINUATION só passa se TODOS verdadeiros:
- displacement válido (M5 bar: range > 0.8*ATR, bullish/bearish, delta >= 80)
- exhaustion filter passa (não overextended)
- V3 momentum não bloqueia
- iceberg não é contra forte

## Guardrails contra entrada tardia

1. **Exhaustion filter** (overextension: dist > 1.5*ATR do liq_top/bot)
2. **Displacement requer barra forte recente** (não entra em drift)
3. **Delta real obrigatório** (bar_delta >= 80, não zero)
4. **Cooldown entre triggers** (60s mínimo)

## O que passa agora (exemplos do shadow v5)

```
#4  2025-12-03 13:50  EXPANSION LONG  close=4267.6  rng=10.6  dlt=+314  -> GO
    Displacement forte, delta sólido, price acima da box em uptrend

#15 2025-12-09 15:55  EXPANSION LONG  close=4242.0  rng=14.1  dlt=+429  -> GO
    Barra impulsiva com delta forte, continuation clara
```

## O que continua bloqueado

```
- Sem displacement (bars fracas, drift): SKIP
- Overextended (>1.5*ATR do nível): BLOCK por exhaustion
- Delta fraco (<80): SKIP por displacement
- Iceberg contra forte: SKIP
```

## Implementação

Ficheiro: `event_processor.py` — método `_tick_loop()`

Mudança: adicionar bloco após o check de `_near_level()` que não encontra nível:

```python
# PATCH 2A: Trend Continuation trigger when price is outside box
if not level_type:
    # Check if price is running in trend direction outside box
    cont_trigger = self._check_continuation_trigger(xau_price)
    if cont_trigger:
        direction, strategy_reason = cont_trigger
        # ... trigger gate
```

Novo método `_check_continuation_trigger(price)`:
- Verifica phase, daily_trend, price vs box
- Chama _get_trend_entry_mode
- Retorna (direction, reason) ou None

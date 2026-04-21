# DESIGN DOC v2 — `derive_m30_bias` Literatura-Aligned + H4 Gate

**Sprint:** entry_logic_fix_20260420 (Track C)
**Status:** DESIGN ONLY — zero código editado
**Author:** ClaudeCode
**Date:** 2026-04-20
**Supersedes:** v1 (mesma filename) — v2 adiciona Bug #3 (H4 authority ignored) e H4 gate mandatory
**Input:** `BIAS_STUCK_INVESTIGATION.md` + Sprint C v2 prompt

---

## 1. Problem Statement

### 1.1 Observed behaviour (live diagnostic 2026-04-20)

- `m30_bias=bearish` contínuo há **91.06 horas** (desde 2026-04-16 13:58 UTC).
- `daily_trend=short` flipped uma vez às 05:00 e ficou stuck.
- Sistema emitiu **69 GO SHORT / 0 LONG em 8h** — 100% direction bias.
- Ao mesmo tempo charts mostram **3 H4 candles consecutivas bullish** (recuperação do dip de 4780→4820+).
- L2 microstructure: `cumulative_delta NET +282` últimas 2h (buyer dominant). Iceberg JSONL: 4 BID vs 1 ASK.
- Barbara observou e fechou SHORTs manualmente com -$80 loss; novo SHORT às 09:02 entrou manualmente, lucrando ~+$6 (pequena reversão intraday).

### 1.2 Three bugs identified

**Bug #1 — `derive_m30_bias` framing (nuanced).**

Sprint C investigação hipotetizou equality bug: `level_detector.py:241` usa `liq_top > box_high` estrito. 44.7% parquet com `liq_top == box_high`, 49.1% com `liq_top > box_high`, 0% com `liq_top < box_high`.

**Framing refinado (§4.1 abaixo):** o writer `m30_updater.py:280-285` emite:
- UP fakeout: `liq_top = fakeout_ext > box_high`, `liq_bot = box_low`
- DN fakeout: `liq_top = box_high`, `liq_bot = fakeout_ext < box_low`

Equality `liq_top == box_high` é **marcador canónico "sem UP fakeout"**, não ruído. Strict `>` está semanticamente correcto per writer. Mudar para `>=` inverteria signal (93.8% rows passariam a bullish).

**Consequência:** Bug #1 não é "operator bug". É **bug de responsividade** — a função só devolve confirmed bias baseado no último confirmed box. Durante a janela unconfirmed (30min-2h) o system não reage a evidência fresca.

**Bug #2 — Hard-block confirmed-only (architectural).**

`event_processor.py:2391-2403` usa `m30_bias_confirmed` como veto duro. Durante janela onde provisional bias diverge do confirmed (novo box unconfirmed com fakeout oposto), sistema não aceita provisional. Vivido hoje 08:00-10:00 UTC: box 5240 unconfirmed com `liq_top=4825.40 > box_high=4816.40` (UP fakeout, provisional=bullish), mas hard-block continuava bearish.

**Bug #3 (NOVO, descoberto via chart inspection 12:33 UTC) — H4 authority ignored.**

Sistema **não consulta H4 candle structure** para validar direcção. Apenas usa:
- `delta_4h` (microstructure agregada 4h window — janela stale, viu-se a -1000 enquanto L2 2h mostrava +282)
- `H4_liquidity_lines` (swing highs/lows, não candles — não é direction indicator)
- `_read_d1h4_bias_shadow` em `event_processor.py:704` (shadow logging only, comment diz "No behavioral impact")

**ADR-001 em `level_detector.py:18` EXPLICITAMENTE PROÍBE H4 para execução:**

> "ADR-001: H4/D1 levels are NEVER used for execution. Violation = system goes idle in trending markets."

**Este ADR entra em conflito directo com a literatura ATS:**

> "You can see we we had some aggressive bars down there. So what this means, **the four hour is the authority over the smaller time frames**. Simple concept. If the four hour is entering the trend phase to the downside, that means that every time frame lower than a four hour chart is going to — all the big moves are gonna be on the downside."
> — ATS Trade System, project knowledge `ATS Trade System.txt:1119`

Resolução: ADR-001 foi escrito para lidar com stale-H4 edge (se H4 parquet parou de actualizar, sistema ficava idle). **Hoje o problema inverso é pior: H4 ignorado causa counter-trend trading sistémico.**

### 1.3 Exemplo concreto live — 2026-04-20 08:00-10:00 UTC

| Momento | H4 candle (último completo) | M30 box | confirmed | Writer semantics | m30_bias actual | **v2 deveria dar** |
|---|---|---|---|---|---|---|
| 08:00 | 4H #13 green (close 4810 > open 4797, +13pts body) | 5239 | True | DN fakeout | bearish | **bullish** (H4 up + M30 unconf up) |
| 08:00 | (mesmo 4H) | 5240 | False | UP fakeout | bearish | **bullish** (H4 up + M30 provisional up) |
| 09:00 | 4H #14 green (cont.) | 5240 | False | UP fakeout | bearish | **bullish** |
| 10:00 | 4H #14 still green | 5240 | True (confirmed) | UP fakeout | **bullish** | bullish ✓ |

Durante 2h, sistema podia ter emitido LONGs (H4 bullish + M30 provisional bullish + price rally). Em vez disso emitiu 0 LONG, 25+ SHORTs (todos EXEC_FAILED devido ao broker bug, mas intentionally SHORT).

---

## 2. Literatura Unificada — H4 e M30

### 2.1 ATS (authority absoluta do H4)

**Strategic Plan §4.0 (`ATS Implementation Strategic Plan`):**

> "Multi-timeframe analysis is the primary method for establishing a directional bias — the essential filter that aligns your trading decisions with the dominant flow of institutional money."

> "Directional Bias Chart (e.g., Daily): The purpose of this chart is to identify the macro trend and establish the directional bias. All trading decisions on the lower timeframe must align with the direction indicated on this chart. **No exceptions.**"

**Strategic Plan §5:**

> "1. Establish Directional Bias... 3. Wait for Favorable Pricing: In a confirmed downtrend, the strategy dictates waiting for the price to trade above the expansion line [overvalued]... Conversely, in an uptrend, one must wait for the price to trade below the expansion line [undervalued]. 4. Execute in the Direction of the Bias."

**ATS Trade System `:1119`:**

> "The four hour is the authority over the smaller time frames. Simple concept. If the four hour is entering the trend phase to the downside, that means that every time frame lower than a four hour chart is going to — all the big moves are gonna be on the downside."

**ATS Trend Line doc (`Everything to Know About the ATS Trend Line`):**

> "If you are above the line and you see that we have a blue dot here is the default color and you see that it started and we're trading above that then you're essentially looking to buy below value in accordance with that line and then It's basically the opposite if it's going down. You'd be looking to sell above value as the market heads down."

Regra unificada ATS: **H4 direction gates M30 direction**. Contra-H4 = no-trade absoluto.

### 2.2 Wyckoff (Villahermosa, *Wyckoff Methodology in Depth*)

Wyckoff organiza o ciclo em 4 fases (Acumulação, Markup, Distribuição, Markdown). Directional bias só é confirmado em Phase D (SOS/SOW) ou Phase E (markup/markdown).

**Quote-chave (Villahermosa, Cap. 5 Phase D):**
> "A SOS (Sign of Strength) is the confirmation of accumulation — the price breaks out of the structure with increased volume and momentum, closing above the previous resistance."

**Multi-timeframe Wyckoff (Cap. 7):** O HTF carries bias; LTF timing. Same principle as ATS.

Mapping to APEX:
- H4 phase D/E **is** the bias gate
- M30 structure (box, fakeout) **is** tactical entry timing within H4 context

### 2.3 ICT (Khan, *The ICT Bible*; Module 3 Advanced Order Flow)

- **Bullish order flow:** BOS up on HTF, internal retrace to discount PD array
- **Bearish order flow:** BOS down on HTF + retrace to premium PD array
- **Neutral:** no BOS on parent timeframe

**Quote-chave (Module 3):**
> "A Break of Structure occurs when price decisively trades through a prior swing pivot. Before a BOS on the higher timeframe, you don't have directional bias — you have consolidation."

ICT mapping:
- H4 BOS direction == bias gate
- M30 entry patterns only valid in direction of H4 BOS
- ICT "fractal" framing: lower timeframe structure must respect parent

### 2.4 Tabela de consenso

| Metodologia | H4 role | Quando bullish? | Quando bearish? | Neutral? |
|---|---|---|---|---|
| ATS | Authority absoluta ("no exceptions") | ATS Trend Line up + price above expansion line + H4 consolidation broken up | Trend Line down + price below expansion + consolidation broken down | Consolidation sem trend line rotation |
| Wyckoff | HTF structural bias | Phase D post-SOS (markup) | Phase D post-SOW (markdown) | Phase A/B (stopping action, cause building) |
| ICT | Parent order flow | BOS up + respecting discount PDs | BOS down + respecting premium PDs | No BOS; within prior structure |
| **Consenso** | **H4 gates M30 entries** | **H4 upper breakout confirmed + trend line up** | **H4 lower breakout confirmed + trend line down** | **H4 range-bound / consolidation** |

### 2.5 Definição operacional unificada (pt-PT)

> **H4 bias BULLISH (5-8 linhas):**
> Um rompimento H4 para cima foi registado (close > prior resistance com momentum OR ATS trend line flipped up with institutional inefficiency). Preço está a afastar-se da value line na direcção up. Mapeia directamente a Phase D SOS/markup (Wyckoff), BOS up + discount retracement (ICT), e ATS Trend Line up. Trades M30 só devem ir LONG neste estado.
>
> **H4 bias BEARISH:** simétrico — rompimento para baixo registado, preço afastando-se da value line down. Phase D SOW, BOS down + premium retracement, ATS Trend Line down. Trades M30 só devem ir SHORT.
>
> **H4 NEUTRAL:** range-bound / consolidation; H4 não confirmou breakout. Phase A/B Wyckoff, pré-BOS ICT, sem trend line rotation ATS. Default literatura: **no trades** ou entries selectivas com justificação extra.

### 2.6 Regras H4 derivadas (literatura-aligned, para implementação)

**R_H4_1 — H4 bullish via body strength + close position**
- Condição:
  - Última H4 candle completa tem `close > open` (corpo verde), **E**
  - `close` está no quartil superior do range: `close >= low + 0.75 * (high - low)`
- Referência ATS (Trade System `:1119`): "four hour is the authority — big moves aligned with 4H direction"
- Referência Wyckoff: close forte = SOS signature

**R_H4_2 — H4 bullish via 3-candle higher highs/lows (momentum continuation)**
- Condição: pelo menos 2 das últimas 3 H4 candles completas são verdes E cada candle tem `high > prev_high` OR `low > prev_low` (higher-highs-or-higher-lows chain)
- Referência ICT: BOS continuation através de swing structure
- Referência ATS: sustained deviation away from value em trending markets

**R_H4_3 — H4 bullish via h4_jac_dir confirmed UP (from gc_h4_boxes parquet)**
- Condição: `h4_jac_dir == "UP"` AND `h4_box_confirmed == True` na última H4 row
- Referência Wyckoff: JAC (Jump Across the Creek) = SOS confirmed
- **Nota staleness:** verificar `ts_last_h4_row` vs now; se > 6h, degradar para R_H4_1/R_H4_2

**R_H4_4 — H4 bearish via body weakness (simétrica R_H4_1)**
**R_H4_5 — H4 bearish via 3-candle lower lows/highs (simétrica R_H4_2)**
**R_H4_6 — H4 bearish via h4_jac_dir DN confirmed (simétrica R_H4_3)**

**R_H4_7 — H4 neutral**
- Condição: nenhuma das R_H4_1..6 satisfeita
- Último close dentro do range histórico das últimas N=10 candles, sem clear direction
- Pseudocódigo: `return "neutral", confidence=0.0`

### 2.7 Regras M30 com H4 gate (literatura-aligned)

**Regra ouro:** `m30_bias` **nunca** pode contradizer H4 em estado confirmed.

**R_M30_1 — M30 bullish confirmed (only valid if H4 aligned)**
- Condições:
  - H4 bias per R_H4_1/R_H4_2/R_H4_3 == "bullish"
  - latest confirmed M30 row has `liq_top > box_high` (UP fakeout)
- Referência ATS: HTF authority + LTF entry timing
- Pseudocódigo: `return "bullish", is_authoritative=True`

**R_M30_2 — M30 bullish provisional (H4 gate + M30 unconfirmed UP fakeout + secondary)**
- Condições:
  - H4 bias == "bullish" (hard gate)
  - latest unconfirmed M30 has `liq_top > box_high` (provisional UP fakeout)
  - current_price > latest_unconfirmed.box_high OR iceberg_bias=="bullish"
- Pseudocódigo: `return "bullish", is_authoritative=False, reason="provisional_override"`

**R_M30_3 — M30 bearish confirmed (H4 must be bearish)**
- Simétrica R_M30_1

**R_M30_4 — M30 bearish provisional (H4 bearish + M30 unconf DN + secondary)**
- Simétrica R_M30_2

**R_M30_5 — M30 blocked by H4 counter-direction**
- Condição: M30 structure would suggest bullish/bearish, but H4 bias opposite
- Pseudocódigo: `return "neutral", reason="h4_counter_block"`
- Referência ATS: "No exceptions"

**R_M30_6 — M30 neutral via H4 neutral**
- Condição: H4 bias == "neutral"
- Default: `return "neutral"`
- Override opcional: se M30 apresenta structure MUITO clara (strong fakeout + secondary), allow com redução de size — **Barbara decide, Open Question §11.3**

### 2.8 Invariantes

- **I-1:** Se H4 bullish, M30 NUNCA devolve bearish confirmed (R_M30_5 enforce).
- **I-2:** Se H4 neutral, M30 default é neutral; override requer evidência multi-layered.
- **I-3:** Se confirmed bias == provisional bias, output is same.
- **I-4:** Determinismo: mesma input, mesma output.
- **I-5:** v2 nunca inverte bias sem secondary confirmation e H4 alignment.

---

## 3. Semântica correcta de `m30_bias`

### 3.1 M30 bullish bias válido SE:

1. **H4 bullish** (per R_H4_1/R_H4_2/R_H4_3) — **mandatory gate, no exceptions**
2. **E** M30 apresenta structure bullish:
   - Confirmed UP fakeout (R_M30_1), OR
   - Provisional UP fakeout + secondary confirmation (R_M30_2), OR
   - Spring pattern (fakeout DN invalidated) — deferred para Sprint futuro

### 3.2 M30 bearish bias válido SE:

Simétrico: H4 bearish + M30 DN structure.

### 3.3 M30 neutral SE:

- H4 neutral (default conservador), OR
- H4 clear mas M30 em Phase B consolidation (dentro do box, sem breakout)

### 3.4 Regra ouro

**`m30_bias` nunca contradiz H4 confirmed.** Se H4 diz bullish, M30 só pode dar bullish ou neutral (nunca bearish). Este é o invariante I-1.

---

## 4. Dados — O que parquets realmente contêm

### 4.1 M30 writer semantics (m30_updater.py:261-304)

```python
# Scan forward for first breakout
for k in range(i + 1, min(i + MAX_BREAKOUT_WAIT + 1, n)):
    if high_a[k] > b_hi:
        breakout_idx, breakout_dir, fakeout_ext = k, "UP", float(high_a[k])
        break
    elif low_a[k] < b_lo:
        breakout_idx, breakout_dir, fakeout_ext = k, "DN", float(low_a[k])
        break

if breakout_idx is None:
    i += 1
    continue  # no breakout yet, no box emitted

if breakout_dir == "UP":
    new_liq_top, new_liq_bot = fakeout_ext, float(b_lo)
else:  # "DN"
    new_liq_top, new_liq_bot = float(b_hi), fakeout_ext
```

**Implicação:** cada M30 box foi criado num breakout. Writer nunca cria boxes "de pure consolidation" (Phase B-only).

### 4.2 M30 distribution observed (73,663 rows)

| Relação | Count | % | Semântica |
|---|---|---|---|
| liq_top > box_high AND liq_bot == box_low | 36,159 | 49.1 | UP fakeout (bullish signature) |
| liq_top == box_high AND liq_bot < box_low | 32,901 | 44.7 | DN fakeout (bearish signature) |
| liq_top < box_high | 0 | 0.0 | impossível por construção |
| NaN | 4,603 | 6.2 | edge / initial rows |

**Sprint C investigação interpretou "liq_top == box_high" como equality bug — é semântica do writer, não ruído.**

### 4.3 H4 parquet inventory

Parquet dedicado: `C:\data\processed\gc_h4_boxes.parquet`
- Rows: 9,609 (2020-01-01 → **2026-04-14 14:00 UTC**)
- **STALE: 6 dias sem actualização** (hoje é 2026-04-20 — writer parou)
- Colunas: `open, high, low, close, volume, atr14, h4_liq_top, h4_liq_bot, h4_fmv, h4_box_high, h4_box_low, h4_box_confirmed, h4_box_id, h4_jac_dir`

Parquets derivados (features):
- `gc_ats_features_v2.parquet`: `h4_atr`, `h4_is_contraction`, `h4_contraction_high/low/mid`, `h4_expansion_confirmed`, `h4_expansion_line`, `h4_expansion_direction`, `h4_trend`, `h4_liq_line_top`
- `gc_ats_features_v4.parquet`: `h4_atr`, `h4_fmv`, `h4_box_high/low`, `h4_spring_low`, `h4_upthrust_high`, `h4_liq_top/bot`, `h4_jac_dir`, `h4_box_confirmed`

### 4.4 H4 candles via OHLCV resample (fallback se parquet H4 stale)

```python
ohlc = pd.read_parquet(r"C:\data\processed\gc_ohlcv_l2_joined.parquet")
h4 = ohlc[["open","high","low","close"]].resample("4h").agg({
    "open":"first","high":"max","low":"min","close":"last"
})
```

OHLCV joined parquet **está updated** (últimos minutos). Resample permite reconstruir H4 em runtime.

### 4.5 ADR-001 conflict

**`level_detector.py:18`:**
> "ADR-001: H4/D1 levels are NEVER used for execution. Violation = system goes idle in trending markets."

Origem provável: incidente anterior onde H4 stale causou sistema idle. Guard-rail preventivo.

**Hoje provou-se errado:** o problema inverso (H4 ignored) custou 91h stuck bearish + counter-trend SHORT emissions. ADR-001 precisa de revisão — não "never use H4" mas "handle H4 staleness explicitly com fallback".

### 4.6 H4 últimas 10 barras (via resample OHLCV live)

Backtest query planeada (não executada read-only aqui, mas design):
```python
from datetime import datetime, timezone
ohlc = pd.read_parquet(r"C:\data\processed\gc_ohlcv_l2_joined.parquet")
h4 = ohlc[["open","high","low","close"]].resample("4h").agg({
    "open":"first","high":"max","low":"min","close":"last"
}).dropna()
print(h4.tail(10))
```

**Esperado (per chart inspection 12:33 UTC):** últimas 2-3 H4 verdes com close > open, recuperação do dip ~4780 para ~4820+.

---

## 5. Proposta Arquitectural — 3 Opções

### Opção A — Fix minimal (operator change)

**Mudanças:**
- Bug #1: `> → >=` em `level_detector.py:241`
- Bug #2: aceitar provisional quando difere de confirmed
- Bug #3: **não resolvido**

**Crítica:** como §4.2 mostra, mudar `>` para `>=` inverteria signal (93.8% rows passariam a bullish). Rejeitada por sanity. Bug #3 (H4) fica intacto — violação contínua da regra ATS "four hour is authority".

**Rejeitada.**

---

### Opção B — `derive_m30_bias_v2` com H4 gate mandatory

**Mudanças:**
- Nova função `derive_h4_bias` em `level_detector.py` (~40 linhas)
- Nova função `derive_m30_bias_v2` que consome `derive_h4_bias` (~60 linhas)
- `derive_m30_bias` legacy intacto (backwards compat)
- H4 data source: resample on-demand de `gc_ohlcv_l2_joined.parquet` (fallback quando `gc_h4_boxes.parquet` stale)
- `m30_updater.py` caller pode chamar v2 em vez de legacy

**Bugs resolvidos:**
- #1: N/A (framing refinado — não é bug)
- #2: Sim (provisional considerado quando H4 aligned)
- #3: Sim (H4 gate enforce)

**Scope:** ~100 linhas novas, zero alterações destructivas.

---

### Opção C — Refactor arquitectural completo com V0 H4 gate

**Mudanças:**
- Tudo de Opção B, mais:
- **Novo V0 gate** em `ats_live_gate.py` antes de V1 (zone check)
- H4 alignment enforce em V0 — counter-H4 = auto-BLOCK antes de chegar a V1/V2/V3/V4
- Observability adicional: métricas `v0_h4_gate.pass/block` counters
- Shadow log 24-48h antes de production

**Bugs resolvidos:** #1 + #2 + #3 com arquitectura explícita

**Scope:** ~200 linhas (100 em level_detector + 70 em event_processor/gate + 30 em shadow logging).

---

### 5.4 Tabela comparativa

| Aspecto | A (minimal) | **B (v2 + H4 gate)** | C (refactor + V0 gate) |
|---|---|---|---|
| Scope | 10 linhas | ~100 linhas | ~200 linhas |
| Risco regressão | FATAL (inverte signal) | BAIXO (adição pura) | MÉDIO (novo gate em chain crítico) |
| Literatura-alignment | Zero | FULL | FULL + arquitectural clean |
| Resolve Bug #1 | N/A | N/A | N/A |
| Resolve Bug #2 | Parcial | Sim | Sim |
| Resolve Bug #3 (H4) | **Não** | **Sim** | **Sim + observability** |
| H4 data source | — | Resample on-demand + parquet fallback | Dedicated provider |
| Stale H4 handling | — | Degradar para resample | Degradar + alert |
| Tempo implementação | 15min | 3-4h | 6-8h |
| Backtest scope | Simples | Moderado (replay com H4 resample) | Extenso (V0 + shadow) |
| Rollback | Trivial | Trivial (revert 2 call sites) | Moderado |
| Matches Sprint A C1 pattern | Não | Sim | Sim |
| Shadow mode required | N/A | Optional | **Yes (48h)** |

**Recomendação técnica:** **Opção B**. Resolve os 3 bugs (Bug #1 é framing-only, não código), scope contido, rollback trivial, matches Sprint A C1 pattern (post-validation adicional sem destruir legacy). Opção C é merecedora num sprint futuro quando infra de shadow mode estiver em place.

---

## 6. H4 Gate Specification

### 6.1 Input/Output contract

```python
def derive_h4_bias(
    h4_candles: list[dict] | pd.DataFrame,  # last N completed H4 candles
    h4_box_row: dict | None = None,          # optional: latest h4_boxes parquet row
    max_staleness_hours: float = 6.0,        # fallback threshold
) -> tuple[str, float, dict]:
    """
    Returns (bias, confidence, metadata).

    bias: "bullish" | "bearish" | "neutral"
    confidence: float [0.0, 1.0]
    metadata: {
        "source": "h4_boxes_parquet" | "ohlcv_resample" | "degraded",
        "rules_fired": ["R_H4_1", ...],
        "staleness_minutes": float,
        "last_h4_close_ts": str,
    }
    """
```

### 6.2 Rules implementation sketch

```python
def derive_h4_bias(h4_candles, h4_box_row=None, max_staleness_hours=6.0):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Stale check for h4_boxes_parquet
    use_box_row = False
    if h4_box_row is not None:
        ts = pd.Timestamp(h4_box_row.name if hasattr(h4_box_row, "name") else h4_box_row.get("ts"))
        staleness = (now - ts.to_pydatetime()).total_seconds() / 3600.0
        if staleness <= max_staleness_hours:
            use_box_row = True

    # ---- R_H4_3 / R_H4_6: via h4_jac_dir when fresh box_row exists ----
    if use_box_row and bool(h4_box_row.get("h4_box_confirmed", False)):
        jac = str(h4_box_row.get("h4_jac_dir", "")).upper()
        if jac == "UP":
            return "bullish", 0.95, {"source": "h4_boxes_parquet", "rules_fired": ["R_H4_3"], "staleness_hours": staleness, "last_h4_close_ts": str(ts)}
        if jac == "DN":
            return "bearish", 0.95, {"source": "h4_boxes_parquet", "rules_fired": ["R_H4_6"], "staleness_hours": staleness, "last_h4_close_ts": str(ts)}
        # box confirmed but jac unknown → fall through to candle analysis

    # ---- R_H4_1 / R_H4_4: body + close position ----
    if len(h4_candles) < 1:
        return "neutral", 0.0, {"source": "insufficient_data", "rules_fired": []}

    last = h4_candles[-1]
    rng = last["high"] - last["low"]
    if rng == 0:
        close_pct = 0.5
    else:
        close_pct = (last["close"] - last["low"]) / rng
    body_up   = last["close"] > last["open"]
    body_down = last["close"] < last["open"]

    r_h4_1 = body_up   and close_pct >= 0.75
    r_h4_4 = body_down and close_pct <= 0.25

    # ---- R_H4_2 / R_H4_5: 3-candle continuation ----
    r_h4_2 = r_h4_5 = False
    if len(h4_candles) >= 3:
        last3 = h4_candles[-3:]
        greens = sum(1 for c in last3 if c["close"] > c["open"])
        reds   = sum(1 for c in last3 if c["close"] < c["open"])
        hhhl = all(
            (last3[i]["high"] > last3[i-1]["high"]) or (last3[i]["low"] > last3[i-1]["low"])
            for i in (1, 2)
        )
        llhl = all(
            (last3[i]["low"] < last3[i-1]["low"]) or (last3[i]["high"] < last3[i-1]["high"])
            for i in (1, 2)
        )
        r_h4_2 = greens >= 2 and hhhl
        r_h4_5 = reds   >= 2 and llhl

    # ---- Combine ----
    rules_bull = [r for r, name in [(r_h4_1, "R_H4_1"), (r_h4_2, "R_H4_2")] if r]
    rules_bear = [r for r, name in [(r_h4_4, "R_H4_4"), (r_h4_5, "R_H4_5")] if r]

    if rules_bull and not rules_bear:
        conf = 0.7 if r_h4_1 and r_h4_2 else 0.55
        fired = [n for r, n in [(r_h4_1, "R_H4_1"), (r_h4_2, "R_H4_2")] if r]
        return "bullish", conf, {"source": "ohlcv_resample" if not use_box_row else "h4_boxes_parquet_fallback", "rules_fired": fired}
    if rules_bear and not rules_bull:
        conf = 0.7 if r_h4_4 and r_h4_5 else 0.55
        fired = [n for r, n in [(r_h4_4, "R_H4_4"), (r_h4_5, "R_H4_5")] if r]
        return "bearish", conf, {"source": "ohlcv_resample" if not use_box_row else "h4_boxes_parquet_fallback", "rules_fired": fired}

    # Conflicting signals or neither
    return "neutral", 0.0, {"source": "neutral_default", "rules_fired": []}
```

### 6.3 Edge cases

- **H4 em formação (current bar incomplete):** USAR apenas barras completas. `resample("4h")` com timestamp alignment — current partial bar ignorado.
- **H4 stale parquet (>6h):** fallback para resample OHLCV. Log `METRIC h4_bias.fallback_resample`.
- **H4 resample insufficient data (<3 bars):** return "neutral", confidence=0.0.
- **H4 flip recente (last candle reverses prior trend):** confidence reduzida. Barbara pode adicionar buffer (ex: aceitar flip só após 2 candles consecutive).
- **H4 neutral mas M30 forte:** R_M30_6 controla. Default: neutral. Override opcional (Open Question §11.3).

### 6.4 Refresh cadence

- H4 bar completa a cada 4h (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC).
- Recompute on: cada tick loop iteration (cheap, pandas resample <50ms).
- Cache: pode cache por 1min para reduzir recompute, mas barato suficiente sem cache.

---

## 7. Integração com `event_processor`

### 7.1 Fluxo actual (pre-fix)

```
[tick loop @ event_processor.py:4268]
  level_type, level_price = self._near_level(xau_price)   # M5/M30 proximity
  direction, _strat_reason = self._resolve_direction(level_type)
  # ... gate.check(direction, ...) → v1/v2/v3/v4 votes
```

`m30_bias` é consumido dentro de `gate.check` (V1 zone + possivelmente M30_BIAS_BLOCK em gates.py).

### 7.2 Fluxo proposto v2 (Opção B)

Duas posições possíveis para inserir H4 gate:

**Opção 7.2.A — H4 gate em `_resolve_direction`**
```python
def _resolve_direction(self, level_type: str) -> tuple:
    # existing strategy_mode / trend_direction lookup
    # ... (compute tentative direction)

    # NEW: H4 alignment enforce
    h4_bias, h4_conf, h4_meta = self._get_h4_bias()   # cached, ~50ms
    if h4_bias != "neutral" and tentative_direction is not None:
        if h4_bias == "bullish" and tentative_direction == "SHORT":
            log.info("H4_COUNTER_BLOCK: H4=%s blocks SHORT (literatura ATS no-exceptions)", h4_bias)
            self._metric_incr("h4_gate.counter_block.short")
            return (None, f"H4_COUNTER: {h4_bias} blocks SHORT")
        if h4_bias == "bearish" and tentative_direction == "LONG":
            log.info("H4_COUNTER_BLOCK: H4=%s blocks LONG", h4_bias)
            self._metric_incr("h4_gate.counter_block.long")
            return (None, f"H4_COUNTER: {h4_bias} blocks LONG")
    # else: aligned or neutral → proceed
    return (tentative_direction, strat_reason)
```

**Opção 7.2.B — H4 gate como V0 em `ats_live_gate.py`** (mais limpo, é a Opção C)

Recomendação para Sprint C actual: **7.2.A** (inline em `_resolve_direction`) — consistente com Opção B scope (~60 linhas adicionais, reversível). V0 gate fica para Sprint futuro.

### 7.3 Uso de `derive_m30_bias_v2`

Substituir chamada actual:
```python
# BEFORE (event_processor.py:1626 ~)
self.m30_bias = derive_m30_bias(m30_df, confirmed_only=True)[0]

# AFTER
h4_bias, h4_conf, h4_meta = self._get_h4_bias()
bias, is_auth, meta = derive_m30_bias_v2(
    m30_df=m30_df,
    h4_bias=h4_bias,
    current_price=self._current_price,
    iceberg_bias=self._latest_iceberg_bias,
)
self.m30_bias = bias
self._m30_bias_authoritative = is_auth
self._m30_bias_meta = meta
```

Hard-block em `event_processor.py:2391-2403` passa a verificar `self._m30_bias_authoritative` — se `False`, pode aceitar provisional override dentro dos critérios da regra R_M30_2/4.

### 7.4 H4 bias cache

```python
def _get_h4_bias(self, cache_ttl_s: float = 60.0) -> tuple[str, float, dict]:
    now = time.monotonic()
    if self._h4_cache_ts and (now - self._h4_cache_ts) < cache_ttl_s:
        return self._h4_cache
    # Refresh
    try:
        h4_box_row = _read_last_h4_box_row()
        h4_candles = _resample_h4_candles(last_n=20)
        result = derive_h4_bias(h4_candles, h4_box_row)
    except Exception as e:
        log.warning("h4_bias fetch failed: %s; defaulting to neutral", e)
        result = ("neutral", 0.0, {"source": "error", "error": str(e)})
    self._h4_cache = result
    self._h4_cache_ts = now
    return result
```

---

## 8. Backtest Plan Counterfactual

### 8.1 Objectivo

Validar que:
1. `derive_h4_bias` teria marcado hoje bullish durante 07:00-14:00 UTC (vs actual stuck bearish)
2. `derive_m30_bias_v2` + H4 gate teria bloqueado 100% dos SHORTs das últimas 8h
3. Durante a janela 08:00-10:00 UTC, R_M30_2 (provisional + H4 aligned + secondary) teria desbloqueado LONG
4. Não gera excesso de LONGs spurious (false positives)

### 8.2 Metodologia

```python
"""Backtest plan v2.

1. Reconstruir H4 candles:
   h4 = ohlcv.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"})
   Cover period 2026-04-14 01:00 to 2026-04-20 09:00 UTC (matches decision_log).

2. Para cada decisão em decision_log.jsonl (12,022):
   - Extrair price_mt5, direction, m30_bias_old, provisional_m30_bias
   - Find H4 candle completada antes de decision ts
   - Compute: h4_bias_v2, m30_bias_v2

3. Classificar outcome:
   - IDENTICAL: v2 permite mesmo direction que old emitiu
   - BLOCKED_BY_H4: v2 bloqueia via counter-H4 (SHORT com H4 bullish, etc)
   - PROVISIONAL_ALLOW: old blocked por confirmed-only, v2 allows via R_M30_2
   - FLIPPED: v2 permite direction OPOSTA ao old (raro, exige preemption forte)

4. Métricas:
   - % dos 69 GO SHORTs hoje (01:00-09:00) bloqueados por v2
   - % dos LONGs potenciais (never emitted) desbloqueados
   - Caso específico rally 2026-04-20 02:00-03:30: quantas novas LONG opportunities?
   - Caso 91h stuck (2026-04-16 13:58+): H4 bias rolled correctly?
"""
```

### 8.3 Métricas esperadas

| Métrica | Valor esperado |
|---|---|
| % dos 69 SHORTs hoje bloqueados via H4_COUNTER_BLOCK | 95-100% (H4 bullish durante janela) |
| LONGs emergentes 08:00-10:00 UTC | 1-5 (via R_M30_2) |
| % IDENTICAL geral 12,022 decisões | 60-80% |
| % BLOCKED_BY_H4 geral | 15-30% |
| % PROVISIONAL_ALLOW | 2-8% |
| % FLIPPED | <1% |
| Duração stuck média (old: 91h, new: ≤12h) | Dramaticamente reduzida |

### 8.4 Sanity checks obrigatórios

1. **No silent flips:** toda divergência v2 vs old deve ter `h4_bias != "neutral"` OR `provisional_override=True` com secondary.
2. **Counter-H4 totals = zero:** após v2, não deveria haver **nenhum** SHORT emitido quando H4 bullish confirmed.
3. **H4 neutral regime:** durante períodos de H4 neutral, v2 degrade para neutral em M30 também. Zero over-permissive LONGs.
4. **Historical uptrends:** durante trending markets históricos (ex: Nov 2025), v2 permite continuation trades alinhados com H4. Não bloqueia todo o trading.

### 8.5 Caso específico rally 2026-04-20 08:00-10:00 UTC

Expected replay:

| Timestamp | H4 bias v2 | confirmed M30 | provisional M30 | price | m30_bias_v2 output | Direction allowed |
|---|---|---|---|---|---|---|
| 08:00 | bullish (R_H4_1/2) | bearish (5239) | bullish (5240 UP fakeout) | 4785 (MT5) | bullish (R_M30_2: provisional + H4 + price>box_high) | LONG only |
| 09:00 | bullish | bearish (5239) | bullish (5240) | 4796 | bullish | LONG only |
| 10:00 | bullish | **bullish (5240 confirmed)** | bullish | 4797 | bullish (R_M30_1) | LONG only |

Old system: 25+ SHORTs emitted, 0 LONG. v2: 0 SHORT, ~1-3 LONG (R_M30_2 dispara quando price cross above box_high).

---

## 9. Risk Analysis

### 9.1 False Positives (v2 allows trade that shouldn't work)

**FP-1: H4 bias incorrect due to stale h4_boxes parquet**
- Cenário: h4_boxes parquet parou há 6h, resample OHLCV não captura nuances do ATS trend line
- Mitigação: resample é simples (OHLC) — não tem trend line logic ATS. Pode divergir em regime choppy.
- Probabilidade: MÉDIA em choppy markets.
- **Action needed (Open Question §11.2):** criar ou resumir updater de h4_boxes parquet.

**FP-2: R_M30_2 provisional + secondary dispara flip-flop em range**
- Cenário: M30 fake-breakouts consecutivos (UP then DN within 30min)
- Mitigação: H4 gate já filtra (se H4 neutral, R_M30_2 não dispara).
- Probabilidade: BAIXA.

**FP-3: Iceberg BID coincidente mas no contexto bearish**
- Mitigação: `secondary_conf` requires H4 aligned. Iceberg sozinho não é suficiente.
- Probabilidade: BAIXA.

### 9.2 False Negatives (v2 blocks trade that would have worked)

**FN-1: Overextension reversal em H4 trending forte**
- Cenário: H4 strong uptrend, price spiked 2*ATR acima de box_high — possível mean-reversion intraday (Sprint A findings mostram estes eram lucrativos).
- Comportamento v2: bloqueia SHORT (H4 counter).
- Literatura: ATS "no exceptions" — este é exactamente o tipo de trade que a literatura rejeita. Aceitar false negative é consistente.
- **Probabilidade:** alguns casos perdidos. Trade-off deliberado.

**FN-2: H4 recém-flipped ainda não confirmado**
- Cenário: H4 flipped de bearish para bullish mas não há confirmação suficiente (só 1 candle verde)
- Comportamento v2: devolve "neutral" → não-trade.
- Mitigação: requer 2+ H4 candles consecutive green para flip (R_H4_2). Trade-off: perde entradas iniciais de trend change, mas evita false flips.

**FN-3: H4 neutral quase-bullish**
- Cenário: H4 conf=0.35 (borderline), M30 strong setup
- Comportamento v2: neutral default bloqueia. Open Question §11.3: allow com reduced size?

### 9.3 Regime changes

- **H4 trending forte:** v2 alinha correctamente, zero counter-trades.
- **H4 choppy:** v2 silent (neutral default). Menos trades mas menos losses.
- **H4 em transição:** v2 degrade to neutral durante 1-2 H4 candles, depois stabiliza.

### 9.4 Trade count impact — radical

Actual (últimas 8h): 69 GO / 0 LONG / 100% SHORT direction bias.

Com v2 + H4 gate (hoje backtest):
- SHORTs hoje → **0** (H4 bullish filtra todos)
- LONGs potenciais → 1-5 (R_M30_2 dispara durante rally)
- **Trade count cai 95%+ na janela 01:00-09:00 today.**

Over weeks:
- Se H4 regime == bullish → SHORTs dramáticamente reduzidos, LONGs aumentam
- Se H4 regime choppy → trade count cai em geral
- Net effect: menos trades mas melhor win rate esperado

**Rollback triggers específicos:**
- Trade count em 7d rolling < 50% do baseline → investigar; rollback se persistir
- Win rate LONG em 7d < 30% → investigar false positives de R_M30_2
- `h4_gate.counter_block` count > 90% de todos os signals → H4 data problem; investigate updater

### 9.5 ADR-001 violation

**Novo design VIOLA ADR-001.** Precisa:
1. Atualizar comentário em `level_detector.py:18` com link para este design doc
2. Adicionar safety guard: se H4 data indisponível/errored, v2 degrade gracefully (neutral, no lock)
3. Monitorar staleness + alert

**Shadow mode recomendado** para confirmar que sistema não fica idle.

---

## 10. Rollout Proposal

### 10.1 Fases propostas

1. **Design doc approval** (Barbara + Claude review) — este documento
2. **Implementação incremental** (ClaudeCode):
   - Fase 2a: `derive_h4_bias` em `level_detector.py` (isolada, testável)
   - Fase 2b: `derive_m30_bias_v2` em `level_detector.py` (consome 2a)
   - Fase 2c: `_get_h4_bias` cache + `_resolve_direction` H4 counter-block em `event_processor.py`
   - Fase 2d: Integração com hard-block em event_processor.py:2391-2403 para aceitar provisional override
3. **Unit tests** — cobrir R_H4_1..7, R_M30_1..6, invariantes I-1..5
4. **Backtest counterfactual** — replay 12,022 decisions, validar §8.3 metrics
5. **Shadow mode OPTIONAL** — 24h de log `METRIC h4_gate.*` com `enforce=False`
6. **Deploy conjunto Sprint A + Sprint C** (janela de mercado aberto, Barbara autoriza manualmente via nssm)
7. **Observability window 48h** — monitorar:
   - H4 bias stability (flip frequency)
   - `METRIC h4_gate.counter_block.*` counts
   - Trade count by direction
   - Tracebacks

### 10.2 Deploy order — Sprint A vs Sprint C

**Opção sequencial:** A primeiro (deployed e estabilizado), depois C.
- Problema: Sprint A sozinho reduz SHORTs wrong-side mas mantém stuck bearish → baseline distorcido.

**Opção conjunto (recomendada):** A + C no mesmo deploy.
- Vantagens: rollback atomic, baseline de comparação limpo, impact radical visto em uma única janela.
- Custo: mais código para debugar em caso de issue.

### 10.3 Rollback plan específico

| Trigger | Acção |
|---|---|
| Tracebacks pós-restart | Immediate rollback |
| `h4_gate.counter_block` > 95% de decisões em 1h (sistema quase silencioso) | Investigate H4 data; rollback se justified |
| Trade count em 24h < 20% do pre-v2 baseline | Investigate; rollback se persistir |
| LONG count em 24h > 500% pre-v2 baseline | False positives suspeitos; rollback |
| Win rate LONG < 30% em 7d | Investigate R_M30_2 false triggers |

Backup dir pattern: `backup_pre_fix_YYYYMMDD_HHMMSS` com MANIFEST.json (consistente com Sprint A).

Rollback commands:
```powershell
$bak = "<backup_dir>"
Copy-Item "$bak\level_detector.py"  "C:\FluxQuantumAI\live\level_detector.py"  -Force
Copy-Item "$bak\event_processor.py" "C:\FluxQuantumAI\live\event_processor.py" -Force
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX
```

---

## 11. Open Questions

### 11.1 Opção A / B / C — qual?

**Recomendação técnica:** **B.** A inviable (rejected §5.4). C adiciona valor observability mas atrasa; pode ser Sprint D separada.

**Decisão Barbara:** ratifica B?

### 11.2 H4 candles source

**Situação:**
- `gc_h4_boxes.parquet` existe com schema rico, mas **stale 6 dias** (writer parado em 2026-04-14 14:00).
- `gc_ohlcv_l2_joined.parquet` está fresh (live updates).

**Opções:**

1. **Resample on-demand** de OHLCV (confiável, simples, sem trend line ATS logic).
2. **Reviver `h4_updater`** (writer do h4_boxes parquet) — paralelo ao Sprint C, fora deste design.
3. **Híbrido**: h4_boxes se fresh (≤6h), senão resample — já é a proposta do §6.2.

**Recomendação:** híbrido (3) com task separada para diagnose do h4_updater stuck.

**Decisão Barbara:** aceita híbrido?

### 11.3 H4 neutral handling

Literatura ATS é clara: "no exceptions". Mas edge cases:
- H4 transição (flip em curso, confidence baixa)
- H4 consolidation com M30 strong breakout

**Opções:**
- a) **Block estricto:** H4 neutral → no trades (literatura pura)
- b) **Allow reduced size:** H4 neutral + M30 strong → 50% size
- c) **Allow full:** H4 neutral permite M30 proceder normalmente

**Recomendação:** (a) estricto inicialmente, revisit após 1-2 semanas de data.

**Decisão Barbara:**

### 11.4 Current H4 candle — considerar incompleta?

Resample `4h` produz uma bar current incompleta (bar em formação). Opções:
- Ignorar current partial (usar apenas bars completas) — mais rígido, menos responsive
- Incluir current partial (mais responsive mas ruidoso)

**Recomendação:** Ignorar current partial. Usar apenas bars completas.

**Decisão Barbara:**

### 11.5 Excepção overextension reversal — mantém ou remove?

Sprint A findings: overextension reversals (1.5×ATR) historically profitable, não literatura-aligned, 0 fills recentes (broker issue).

**Opções:**
- a) Remove completamente (literatura)
- b) Mantém mas com H4 gate enforce — em H4 bullish, SHORT overextension = bloqueado; em H4 bearish, LONG overextension = bloqueado
- c) Adiar decisão — Sprint D separada

**Recomendação:** (b) — aplica H4 gate universal. Se overextension quiser SHORT com H4 bullish, é bloqueado. Consistente.

**Decisão Barbara:**

### 11.6 Shadow mode obrigatório antes de deploy?

Impact é radical (95%+ trade count reduction na janela actual). Shadow mode permitiria:
- Log `METRIC h4_gate.*` com `enforce=False` durante 24h
- Comparar output v2 vs legacy
- Confirmar invariants I-1..5 antes de enforce

**Custo:** 24-48h de delay.

**Recomendação:** **Não obrigatório** se backtest counterfactual passar todos os sanity checks §8.4. Tests + backtest dão cobertura suficiente para avançar directo (consistente com Sprint A).

**Decisão Barbara:**

### 11.7 Secondary confirmation para R_M30_2/R_M30_4

Proposta actual: `price > box_high` OR `iceberg_bias aligned`.

**Alternativas não propostas:**
- L2 delta cumulativo 5min aligned
- Age threshold: confirmed stale > 2h → preempt mais agressivo
- Volume spike confirmation

**Recomendação:** manter proposta minimalista (2 conditions). Adicionar outras em sprint de calibração futura.

**Decisão Barbara:**

### 11.8 ADR-001 — actualizar ou revogar?

ADR-001 diz "H4 NEVER used for execution". v2 viola esta regra deliberadamente.

**Opções:**
- a) Actualizar comentário com link para este design doc, explicando contexto e safety guards (H4 staleness fallback, neutral default)
- b) Revogar ADR-001 formalmente (documento separado `ADR-002` substituindo)

**Recomendação:** (b) — escrever ADR-002 explícito. Clean history.

**Decisão Barbara:**

---

## 12. Referências

**Código live:**
- `live/level_detector.py:18` — ADR-001 (a rever)
- `live/level_detector.py:215-263` — `derive_m30_bias` current
- `live/level_detector.py:232-243` — `_classify` helper
- `live/m30_updater.py:261-304` — writer M30 semantics
- `live/event_processor.py:704-723` — `_read_d1h4_bias_shadow` (shadow only, "No behavioral impact")
- `live/event_processor.py:1626` — state assignment `self.m30_bias = ...`
- `live/event_processor.py:2391-2403` — hard-block LONG quando bearish confirmed
- `live/event_processor.py:3340+` — `_resolve_direction`

**Dados:**
- `C:\data\processed\gc_m30_boxes.parquet` — 73,663 rows; 49.1% UP-fakeout, 44.7% DN-fakeout
- `C:\data\processed\gc_h4_boxes.parquet` — **STALE 6 dias** (last 2026-04-14 14:00 UTC)
- `C:\data\processed\gc_ohlcv_l2_joined.parquet` — fresh, permite resample H4
- `C:\data\processed\gc_ats_features_v4.parquet` — tem `h4_jac_dir`, `h4_box_confirmed`, etc. (verificar freshness)
- `C:\FluxQuantumAI\logs\decision_log.jsonl` — 12,022 decisions 04-14..04-20
- `C:\data\iceberg\iceberg__GC_XCEC_20260420.jsonl`

**Literatura (`C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs\ATS Docs\`):**
- `ATS Implementation Strategic Plan A Framework for Systematic Profitability.txt` §4-5 — directional bias foundation ("No exceptions")
- `ATS Trade System.txt:1119` — "four hour is the authority"
- `Everything to Know About the ATS Trend Line.txt` — tracking institutional inefficiencies
- `ATS_Trading Strategy 2_Trending Markets.txt` — HTF authority in trending
- `Wyckoff-Methodology-in-Depth-Ruben-Villahermosa.pdf` — Phase D SOS/SOW, JAC
- `Wyckoff_2_0_Structures,_Volume_Prof.pdf` — multi-timeframe bias
- `628929206-The-ICT-Bible-V1-By-Ali-Khan.pdf` — BOS/CHoCH
- `857175775-Module-3-Advanced-Order-Flow-Concepts.pdf` — parent order flow

**Prior sprint artifacts:**
- `BIAS_STUCK_INVESTIGATION.md` — Sprint C investigation
- `ARCHITECTURAL_AUDIT_READ_ONLY.md` — Sprint B v2 (overextension + DELTA audit + missed LONG)
- `DESIGN_DOC_near_level_direction_aware.md` — Sprint A design
- `TASK_CLOSEOUT_REPORT.md` — Sprint A closeout

---

## 13. System state

- Files modified: **ZERO**
- Restarts: **ZERO**
- Capture processes (12332, 8248, 2512): **NOT TOUCHED**
- Git operations: ZERO
- Tests run: ZERO (design only)

Design doc ready for Barbara + Claude review. No implementation until explicit authorization.

---

## 14. Summary (TL;DR for Barbara)

1. **3 bugs confirmed:** equality framing (not a real bug per §4.1), confirmed-only hard-block (Bug #2), H4 authority ignored (Bug #3, novo).
2. **H4 é the elefante na sala.** Literatura ATS é unânime: H4 authority. ADR-001 actual proíbe. ADR-001 está errado.
3. **Opção B recomendada:** `derive_h4_bias` + `derive_m30_bias_v2` com H4 gate mandatory. ~100 linhas, reversível.
4. **Backtest esperado:** 95%+ dos 69 SHORTs hoje bloqueados, 1-5 LONGs emergem na janela 08:00-10:00.
5. **Stale h4_boxes parquet (6 dias)** — precisa diagnose paralelo. Resample OHLCV on-demand como fallback.
6. **Deploy conjunto Sprint A + Sprint C** recomendado para baseline limpo.
7. **8 Open Questions** para Barbara decidir antes de implementação.

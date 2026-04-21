# FORENSIC SUMMARY — APEX Losses (2026-04-01 → 2026-04-10)

**Period:** 10 dias
**Total losses:** 63 trades
**Total loss PnL:** -$1,012
**Avg loss:** -$16.06

---

## Losses by session

| Session | Losers | Loss PnL | Avg |
|---|---|---|---|
| London | 29 | -$525 | -$18.1 |
| NY     | 21 | -$340 | -$16.2 |
| Asian  | 13 | -$147 | -$11.3 |

London dominates loss count AND magnitude.

---

## Losses by direction × session

| Direction | Session | Losers | Loss PnL |
|---|---|---|---|
| LONG  | London | 24 | **-$421** (largest bucket) |
| SHORT | NY     | 17 | -$340 |
| SHORT | London | 11 | -$200 |

LONG London é a fonte #1 de losses (-$421).

---

## Pattern identification

### Pattern 1 — **Same-level repetition (stop-hunt)**

10 losses resultantes de repetição do mesmo nível estrutural em minutos:

**Example A — Abr 1, 14:00-14:36 UTC (NY):**
- 6× SHORT @ 4763.95 em 36 min
- Todas SL_HIT @ -18.4 cada = **-$110 total**
- Iceberg "absorption" sc=+4 a +10 em cada trigger (sinais conflitantes, absorção varia)
- MFE médio: 2.5pts (nunca ameaçaram TP1 @ -20pts)
- **Interpretação:** price rejeição real do level, não absorção — iceberg_proxy falso positivo

**Example B — Abr 2, 16:30-17:40 UTC (NY):**
- 4× SHORT @ 4681.35 em 70 min
- Todas SL_HIT = **-$74**
- d4h variou de +3443 a +3011 (trend bullish forte — trades CONTRA-trend)
- **Insight:** gate permitiu SHORT com d4h extremamente bullish. Filtro d4h não está a vetar suficientemente.

### Pattern 2 — **Fake iceberg absorption**

89/102 trades com `iceberg_aligned=True`. Destes 89, losers tiveram scores "iceberg absorption sc=+N" consistentes mas sem follow-through de price. Iceberg proxy detecta absorção que não materializa em continuação.

### Pattern 3 — **LONG London systematic loss**

24 losses em 25 LONG London trades (96% lose rate). Padrão:
- Entry @ m30_liq_bot
- Price toca liq_bot então fails (nunca recupera)
- Structural weakness — liq_bot não está a segurar durante sessão London.

Possível causa: **London abre com momentum que já "usou" o liq_bot** (price veio de cima batendo nele). Ao momento do trigger ALPHA, o nível já está testado e prestes a quebrar.

---

## Top 10 worst losses

| # | TS (UTC)        | Dir   | Sess | Entry   | MAE | MFE | PnL    | Reason |
|---|-----------------|-------|------|---------|-----|-----|--------|--------|
| 1 | 2026-04-01 14:00 | SHORT | NY | 4763.95 | 20.3 | 11.9 | -$18.4 | iceberg absorption sc=+6 |
| 2 | 2026-04-01 14:06 | SHORT | NY | 4763.95 | 20.3 | 4.0  | -$18.4 | iceberg absorption sc=+6 |
| 3 | 2026-04-01 14:10 | SHORT | NY | 4763.95 | 20.3 | 4.0  | -$18.4 | iceberg absorption sc=+6 |
| 4 | 2026-04-01 14:26 | SHORT | NY | 4763.95 | 20.3 | 4.0  | -$18.4 | iceberg absorption sc=+4 |
| 5 | 2026-04-01 14:32 | SHORT | NY | 4763.95 | 20.3 | 0.75 | -$18.4 | iceberg absorption sc=+8 |
| 6 | 2026-04-01 14:36 | SHORT | NY | 4763.95 | 20.3 | 0.0  | -$18.4 | iceberg absorption sc=+10 |
| 7 | 2026-04-02 16:30 | SHORT | NY | 4681.35 | 20.95 | 8.2 | -$18.4 | momentum OK (d4h=+3443) |
| 8 | 2026-04-02 16:35 | SHORT | NY | 4681.35 | 20.95 | 8.2 | -$18.4 | momentum OK (d4h=+3011) |
| 9 | 2026-04-02 16:40 | SHORT | NY | 4681.35 | 20.95 | 8.2 | -$18.4 | iceberg absorption sc=+8 |
| 10| 2026-04-02 17:40 | SHORT | NY | 4681.35 | 20.95 | 8.2 | -$18.4 | iceberg large_order sc=+3 |

**All 10 are SHORT NY at 2 distinct prices (4763.95 @ Abr 1, 4681.35 @ Abr 2).**

---

## Diagnostic categories

| Category | Count | Loss PnL | % |
|---|---|---|---|
| Same-level repetition | 10 | -$184 | 18% |
| LONG London fails | 24 | -$421 | 42% |
| SHORT NY against d4h | 17 | -$340 | 34% |
| Iceberg false positive | ~30 (overlaps) | ~-$480 | overlap |
| Other | 12 | -$67 | 6% |

(Categorias overlap — um mesmo trade pode ser "same-level repetition" + "LONG London fails".)

---

## Priority recommendations (loss reduction)

### HIGH impact
1. **Block LONG London** → +$421/10d reduction
2. **Block SHORT NY during d4h > +2000** → +$200/10d reduction
3. **Same-level cooldown >= 30 min** (not 5 min bucket) → +$184/10d reduction

### MEDIUM impact
4. **Iceberg persistence check** (require absorption sustained N bars before trigger) → reduces false positives ~30 trades, estimated +$200/10d
5. **Widen SL to 25pts + tighten TP1 to 25pts** (rebalance R:R 1:1 accounting slippage) → +$50/10d

---

## Caveats

- **Slice too short** (10d) para significância estatística. Padrões podem ser idiosyncratic deste período.
- **Engine minimal** — sem L2 danger / regime flip / news exit. Live teria salvado alguns destes losses (estimate: 20-30% dos SL_ALL). True loss floor é provavelmente **-$500 a -$600** (não -$717).
- **Cross-validation gap**: backtest 102 trades vs live 36. Análise é do comportamento sem filtros protectores.

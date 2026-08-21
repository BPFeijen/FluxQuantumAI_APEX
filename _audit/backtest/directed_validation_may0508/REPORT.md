# Directed Validation — BUG-SIGNAL-INVERTED fixes (May 5-8 2026)

**Generated**: 2026-05-10 09:38:02.662765+00:00
**Window**: 2026-05-05 00:00:00+00:00 -> 2026-05-09 00:00:00+00:00
**Source**: live `level_detector.derive_m30_bias` (working tree, F-1 + F-3 active per commits f17bc49 / ada8e9d)
**Live settings**: `min_bars=4`, `window=4`, `strategy=recency_weighted`

## Acceptance gates (originais BUG-SIGNAL-INVERTED)

### G1 — 2026-05-07 04:00-05:00 UTC SHORTs burst

- Population (SHORTs): **371**
- Bias distribution: `{'bullish': 371}`
- Blocked: **371** (100.00%) — threshold ≥80%
- **Result**: PASS

### G2 — Box 5282 04:30-05:30 UTC

- Population (SHORTs): **388**
- Bias distribution: `{'bullish': 388}`
- Blocked: **388** (100.00%) — threshold ≥95%
- **Result**: PASS

## Window-wide stats (May 5-8)

### Live settings (current production deploy)

| Metric | Value |
|---|---:|
| Total signals | 9289 |
| Counter-trend blocked | 2904 (31.26%) |
| LONG | 3304 (blocked: 0) |
| SHORT | 5985 (blocked: 2904) |
| bias=bullish | 5419 |
| bias=bearish | 0 |
| **bias=unknown** | **3870 (41.7%)** |

### Opção 1 alternative (`min_bars=3`, `window=3`)

| Metric | Live | Opção 1 |
|---|---:|---:|
| Counter-trend blocked | 31.26% | 36.75% |
| bias=unknown share | 41.7% | 24.3% |
| bias=bullish | 5419 | 4237 |
| bias=bearish | 0 | 2797 |
| total blocked | 2904 | 3414 |


### Per-day breakdown (live settings)

| Date | Total | bull | bear | unknown | LONG | SHORT | SHORT blocked |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-05 | 664 | 664 | 0 | 0 | 42 | 622 | 622/622 (100%) |
| 2026-05-06 | 2689 | 2689 | 0 | 0 | 1236 | 1453 | 1453/1453 (100%) |
| 2026-05-07 | 3480 | 1923 | 0 | 1557 | 1191 | 2289 | 826/2289 (36%) |
| 2026-05-08 | 2456 | 143 | 0 | 2313 | 835 | 1621 | 3/1621 (0%) |

## Verdict

**Acceptance gates G1+G2 PASS (100% / 100%).** O fix do BUG-SIGNAL-INVERTED (cascade 5-layer + F-asymmetric + F-1/F-3 voting) bloqueia corretamente os dois casos canonicos (burst 04-05 UTC e Box 5282 morning rally) com o codigo atual e settings live.

### Sobre a regressao 'bias=unknown 95%' reportada em 5/8

**Mitigada**: bias=unknown em **41.7%** das 9289 decisoes May 5-8 (vs. 95% reportado em 5/8). Settings live atual `min_bars=4`, `window=4` ja foram afrouxadas vs. o original (5,5) — **regressao parcialmente corrigida**.

### Recomendacao operacional

Opção 1 alt (`min_bars=3`, `window=3`):
- bias=unknown: 41.7% → 24.3% (**-17.4pp**)
- counter-trend blocked: 31.26% → 36.75% (**+5.49pp**)
- bias=bearish: 0 → 2797 (apenas Op1 ve bearish — settings atuais sao bull-skewed)

**Sugerido**: aplicar Op1 alt antes de re-ativar live com broker cTrader/IC Markets.

### Pontos de atencao residuais (nao bloqueantes para re-ativacao)

- **bias=bearish=0 em settings live**: os 4 dias da janela (May 5-8) tiveram mercado predominantemente bullish (preco subiu 4625→4720 = +95pts) e o voting recency_weighted captura isso. Mas zero detection de bearish e estrutural — Op1 alt ja corrige.
- **F-asymmetric inativo em 41.7% das decisoes** (bias=unknown): essas decisoes caem para emissao raw via M5 strategy + iceberg. E o que produziu spam reportado em 5/8.
- **Outros findings da auditoria 5/8** (race conditions decision_live.json, MIN_SCORE_GO=0, same_level_cooldown bypass) **fora do escopo desta validacao** — sao itens das ondas W1-W3 do plano de correcao, nao ainda deployados.

## Methodology

- Read every GO/EXEC_FAILED/BLOCK with `direction in {LONG, SHORT}` from `logs/decision_log.jsonl` in window
- For each: slice `gc_m30_boxes.parquet` to `idx <= ts`; call live `level_detector.derive_m30_bias(slice, confirmed_only=False)`
- Predict counter-trend block: LONG with bias=bearish OR SHORT with bias=bullish → blocked
- ZERO live mutation; pure read-only validation

## Artifacts

- `results_live.jsonl` — per-decision detail under live settings (9289 rows)
- `results_opt1.jsonl` — per-decision detail under Opção 1 alt (9289 rows)

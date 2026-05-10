# Opção B 24h+ audit — production observation

**Generated**: 2026-05-08T16:42:28+00:00
**Deploy (Opção B)**: 2026-05-07 10:57:01 UTC (PID 36372)
**SL fix deploy**: 2026-05-07 14:43:04 UTC (additional restart)
**Service current state**: STOPPED (user-initiated, last hb ~2026-05-08 15:58 UTC)
**Audit window**: ~28h elapsed since deploy

---

## 1. Service-state snapshot at audit time

- last_pid: `30844`
- last_status: `ALIVE`
- last_heartbeat: `2026-05-08T15:58:38.865279+00:00`
- m30_bias: `unknown` (confirmed: `False`)
- provisional_m30_bias: `bearish`
- daily_trend: `unknown`
- delta_4h: `70.0` | phase: `EXPANSION`
- d1h4: `SHORT/WEAK` (h4_jac=long d1_jac=short)
- gc_price: `4717.15` | mt5_price: `4686.15`

---

## 2. Activity summary

| Window | n | first_ts | last_ts |
|---|---|---|---|
| W1: Opção B deploy -> SL fix | 126 | 2026-05-07 11:00:09.592101+00:00 | 2026-05-07 14:39:31.319308+00:00 |
| W2: SL fix -> service stop / now | 4116 | 2026-05-07 15:23:20.709790+00:00 | 2026-05-08 15:58:55.892083+00:00 |
| TOTAL: 24h+ | 4242 | 2026-05-07 11:00:09.592101+00:00 | 2026-05-08 15:58:55.892083+00:00 |

### W1: Opção B deploy -> SL fix

**m30_bias distribution**: {'bullish': 126}
**confirmed flag distribution**: {'True': 126}
**direction distribution**: {'LONG': 126}
**action distribution**: {'GO': 62, 'EXEC_FAILED': 62, 'BLOCK': 2}
**entry_mode distribution**: {'none': 64, 'CONTINUATION': 48, 'PULLBACK': 14}
**bias flips**: 0

**Per-regime bias**:

- TREND_DN: {'bullish': 121}
- RANGE: {'bullish': 5}

### W2: SL fix -> service stop / now

**m30_bias distribution**: {'bullish': 209, 'unknown': 3907}
**confirmed flag distribution**: {'True': 255, 'False': 3861}
**direction distribution**: {'LONG': 982, 'SHORT': 3134}
**action distribution**: {'BLOCK': 1292, 'GO': 1472, 'EXEC_FAILED': 1352}
**entry_mode distribution**: {'none': 2764, 'PULLBACK': 833, 'RANGE': 399, 'CONTINUATION': 120}
**bias flips**: 7

**Per-regime bias**:

- TREND_DN: {'bullish': 61, 'unknown': 1209}
- TREND_UP: {'unknown': 1490, 'bullish': 90}
- RANGE: {'unknown': 1208, 'bullish': 58}

---

## 3. SL / TP correctness audit

### W1 (deploy -> SL fix)
- bad_sl (wrong side of entry): **74**
  Samples (first 5):
  - `2026-05-07 11:00:09.592101+00:00` mode=None price=4715.8 sl=4731.0
  - `2026-05-07 11:00:10.460988+00:00` mode=None price=4715.25 sl=4731.0
  - `2026-05-07 11:00:15.029350+00:00` mode=None price=4715.25 sl=4731.0
  - `2026-05-07 11:00:15.999403+00:00` mode=None price=4714.5 sl=4731.0
  - `2026-05-07 11:00:16.425370+00:00` mode=None price=4715.8 sl=4731.0
- bad_tp1 (wrong side of entry): **0**

### W2 (post SL fix)
- bad_sl: **0**
- bad_tp1: **0**
  ✅ ZERO inverted SL/TP since fix

---

## 4. Bias dynamics (F-1+F-3 voting)

- Total bias flips W1: 0
- Total bias flips W2: 7
- range_bear pattern (RANGE regime + bearish bias) W1: 0
- range_bear pattern W2: 0

**Last 10 bias flips (whole window)**:
- `2026-05-07 17:17:03.332722+00:00` bullish -> unknown
- `2026-05-07 20:39:52.011659+00:00` unknown -> bullish
- `2026-05-07 22:40:35.942189+00:00` bullish -> unknown
- `2026-05-07 23:46:46.816336+00:00` unknown -> bullish
- `2026-05-08 00:19:10.093597+00:00` bullish -> unknown
- `2026-05-08 01:37:05.504260+00:00` unknown -> bullish
- `2026-05-08 02:35:23.622603+00:00` bullish -> unknown

---

## 5. Counter-trend block events (stdout log)

These are F-asym (Opção A) + F-1+F-3 (Opção B) protective blocks:

- RANGE_BOUND_BIAS_BLOCK occurrences: **297789**
- TRENDING_BIAS_BLOCK occurrences: **2568**
- Total BIAS_BLOCK keyword: 313871
- ABORT_STALE (price moved past level): 143

---

## 6. Health / error indicators

- DISPLACEMENT_SL_FALLBACK warnings: **230** (zero = no displacement bar mismatch since fix)
- DISPLACEMENT_DIVERGE warnings: 56007 (informational; CURRENT vs OPT_A path divergence)
- File lock errors (WinError 32 on decision_live.json): **72**
- Access denied errors (WinError 5): **344**

⚠️ **decision_live.json write contention detected** — likely Dashboard service holding the file. Non-fatal but should be diagnosed.

---

## 7. Verdict (Phase 5)

- ✅ Zero inverted SL/TP since SL fix deploy (W2)
- ✅ Box 5266-style range_bear pattern: only 0 occurrences (under heuristic threshold)
- ✅ Bias flip frequency reasonable: 7 flips in 28h

---

## 7b. Investigação "sinais invertidos" pré-stop (2026-05-08 12:34-15:58 UTC)

Barbara parou o sistema às 15:58 UTC reportando "lixo, sinais invertidos". Análise das últimas 3.5h:

### Mercado
- price_mt5: 4699.85 → 4685.75 (**-14.1pts BEARISH drift**)
- range MT5: 4685.75 / 4720.75 (35pts span)

### Decisões (825 total na janela)
- LONG: 296 BLOCK + 8 EXEC_FAILED + 8 GO = **312** (95% blocked)
- SHORT: 246 EXEC_FAILED + 267 GO = **513** (zero blocked)
- Brokers RoboForex + Hantec **DESCONECTADOS** todo o tempo → ZERO execução real

### Bias / cascade durante a janela
- m30_bias = **unknown 100% do tempo** (F-1+F-3 voting nunca convergiu)
- provisional_m30 = bearish (último heartbeat)
- daily_trend = unknown (b/c disagreement)
- Cascade resolveu direction via Layer 4 (provisional bearish) → F-asym bloqueou LONGs counter-trend, permitiu SHORTs aligned

### Direção: matched ou invertida?
- Mercado caindo 14pts → SHORTs eram **DIRECTIONALLY ALIGNED** (correto direção)
- LONGs blocked corretamente (counter-trend)

### Análise do que Barbara provavelmente viu
**Não é inversão de DIREÇÃO** — é qualidade ruim de sinal:
1. **Spam volume**: 10+ SHORTs em 30s no mesmo preço (15:56-15:58, todos @ MT5 4685.75)
2. **Bad timing**: SHORTs entrando no LOW da range (sinal "atrasado" que vê o move depois de já ter acontecido)
3. **Contradição lógica próxima**: LONG @ 4689.9 às 15:13 (RANGE mean-reversion) seguido de SHORTs @ 4685.75 às 15:58 — sistema BUYING LOWS então SELLING LOWS
4. **Reason único repetido**: todas as últimas SHORTs com reason="iceberg large_order sc=+4" (single-source signal)

### Verificação SL/TP arithmetic (post-fix)
- Última SHORT GO @ MT5 4685.75: SL=4705.75 ✓ TP1=4665.75 ✓ TP2=4635.75 ✓ (order válida)
- Última LONG GO @ MT5 4689.9: SL=4669.9 ✓ TP1=4709.9 ✓ TP2=4739.9 ✓ (order válida)

→ SL/TP arithmetic CORRETO. Bug BUG-SL-DISPLACEMENT está fixado.

### Root cause da queda de qualidade
**F-1+F-3 calibração ficou conservadora demais para condições atuais**:
- 95% das decisões em W2 com bias=unknown (3907/4116)
- min_bars=5 + window=5 muito restritivo: boxes M30 atuais raramente têm ≥5 bars confirmados
- Com bias=unknown 95% do tempo, F-asymmetric filter fica EFETIVAMENTE INATIVO
- Sistema cai para emissão raw via M5 strategy + iceberg → signals isoladas e mal-timed

**Esta é uma regressão induzida pela Opção B**. Calibração foi otimizada em corpus subsampled (6000 decisões), mas em produção box-duration distribution é mais curta → maioria dos boxes < 5 bars → bias forçado a unknown.

### DISPLACEMENT_SL_FALLBACK fired 230 vezes
Confirmação que muitas signals CONTINUATION tinham displacement bar inválido. SL fix protegeu via fallback `price ± sl_pts`, mas signal ainda foi emitido — CONTINUATION strategy precisa de filtro adicional (rejeitar signal quando displacement já não é "ativo"), não apenas SL fallback.

---

## 8. Open issues

- ⚠️ Service currently STOPPED (user-initiated). Awaiting reactivation directive.
- ⚠️ `72` `decision_live.json` write contentions in stderr (Dashboard process holding file). Operational, non-fatal.
- ⚠️ MT5 brokers (RoboForex + Hantec) DISCONNECTED throughout window — no live execution attempted; signals were emitted but EXEC_FAILED.
- ⚠️ BUG-M30-STUCK-VS-H4-FLIP backlog (GID 1214603041501490) — orthogonal to Opção B, deferred per ML-DS directive.

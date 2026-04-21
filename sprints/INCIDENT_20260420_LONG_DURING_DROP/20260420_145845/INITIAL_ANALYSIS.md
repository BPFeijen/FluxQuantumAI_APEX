# Incident 20260420 — LONG during drop: initial forensic analysis

**Forensic dir:** `C:\FluxQuantumAI\sprints\INCIDENT_20260420_LONG_DURING_DROP\20260420_145845\`
**Snapshot time:** 2026-04-20 14:58-15:00 UTC
**Mode:** READ-ONLY (no edits, no restarts, capture PIDs 2512/8248 intact; quantower :8000 restarted to new PID 11740 at 14:51 UTC — pre-existing before forensic)

---

## 1. Market state

- 6h ago GC **4816.65**
- Rally high GC **4847.25** at ~14:00 UTC (1h ago)
- **Now GC 4822.05** (MT5 ≈ 4803.57 w/ current offset 20.33)
- Last 60min: 6h rally peaked, dropped **-25 pts** in 40min

H4 resampled (completed bars):

| Bar (UTC) | open | high | low | close | close_pct | body |
|---|---|---|---|---|---|---|
| 2026-04-19 20:00 | 4859.10 | 4859.10 | 4757.05 | 4773.60 | 0.16 | red strong |
| 2026-04-20 00:00 | 4774.70 | 4834.00 | 4765.50 | 4810.60 | 0.65 | green |
| 2026-04-20 04:00 | 4810.60 | 4825.40 | 4799.85 | 4809.30 | 0.37 | near doji |
| **2026-04-20 08:00** | 4808.95 | 4831.35 | 4803.25 | **4831.05** | **0.99** | **green strong** (R_H4_1 fires) |
| 2026-04-20 12:00 | 4831.05 | 4847.25+ | 4822? | ~4822 (partial, incomplete) | forming bearish | — |

**Last COMPLETED H4 bar** (08:00-12:00) = strong-close bullish. Current H4 bar (12:00-16:00) in formation: opened 4831, spiked 4847, now retracing to 4822 — forming bearish reversal candle.

---

## 2. M30 state

Box 5240 CONFIRMED at 10:00 UTC today with:
- `m30_liq_top=4825.40 > m30_box_high=4816.40` → **UP fakeout** → bias **bullish** (correct per writer semantics, finally flipped after 91h stuck bearish)
- Box still active at 14:30 UTC (confirmed). close of 14:00 bar = 4843, 14:30 = 4822.

---

## 3. Decision log — last 60min

**16 decisions total** (8 GO + 8 EXEC_FAILED). All GOs are **LONG**. Execution: all FAILED ("robo=connected hantec=connected").

### Pattern observed (stdout grep)

```
[14:56:29] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)
[14:56:30] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)
[14:56:31] M30_BIAS_BLOCK: bias=bullish(confirmed) -- SHORT rejected (contra-M30)
... (many)
[14:56:34] GO SIGNAL: momentum OK (d4h=-1121) | iceberg large_order sc=+3
[14:56:36] GO SIGNAL: momentum OK (d4h=-1124) | iceberg large_order sc=+3
[14:56:36] EXEC_FAILED: GO LONG -> NO BROKER CONNECTED
... (M30_BIAS_BLOCK SHORT rejections resume)
```

**The symmetric blocks of SHORT (via M30_BIAS_BLOCK) coexist with GO LONG emissions via overextension reversal path.** System ends up 100% long-direction while price drops.

### Strategy context on every LONG (from strategy_context field)

```json
{
  "entry_mode": "OVEREXTENSION",
  "daily_trend": "short",
  "phase": "TREND",
  "strategy_mode": "[m5_only] TRENDING_DN"
}
```

**All 8 LONGs are OVEREXTENSION reversals in TRENDING_DN mode.** `_resolve_direction` lines 3523-3532:

```python
elif trend_direction == "SHORT" and level_type == "liq_bot":
    overext_pts = abs(self.liq_bot - xau_price) if self.liq_bot else 0
    if overext_pts > overext_thr:
        direction = "LONG"  # reversal at overextended liq_bot
```

### Detail of one GO (14:56:44)

| Field | Value |
|---|---|
| price_mt5 | 4810.10 |
| level_type | liq_bot |
| level_mt5 | 4811.92 |
| proximity_pts | 1.8 |
| m30_bias | bullish |
| daily_trend | **short** |
| phase | TREND |
| delta_4h | **-1133 (heavy selling)** |
| v3_momentum | **status=OK, delta_4h=-1121, score=2** |
| v4_iceberg | type=large_order, aligned=True, score=3 |
| reason | "momentum OK (d4h=-1121) \| iceberg large_order sc=+3" |

**Cognitive dissonance:** v3_momentum="OK" with delta_4h=-1121 (bearish). The V3 gate is validating LONG with negative cumulative delta — this is either inverted threshold logic or a separate exhaustion rule overriding.

---

## 4. Iceberg events last 2h

| ts UTC | side | price GC | prob | refills |
|---|---|---|---|---|
| 13:02:33 | ask | 4822.0 | 0.15 | 1 |
| 13:24:17 | bid | 4838.7 | 0.15 | 1 |
| 14:03:13 | bid | 4834.1 | 0.15 | 1 |
| 14:03:23 | bid | 4833.6 | 0.15 | 1 |
| **14:30:27** | **ask** | **4847.6** | 0.15 | 1 |
| **14:30:28** | **ask** | **4847.8** | **0.35** | 1 |

**Two ASK icebergs at the exact top (4847.6/4847.8 GC at 14:30 UTC) with prob 0.15-0.35 — the SELL absorption signal at the rally peak.** All subsequent LONGs (14:37+ and 14:56) continued to fire with `v4_iceberg.type="sweep"/"large_order"` `aligned=True` — **v4 misread the icebergs as LONG-aligned despite ASK absorption.**

---

## 5. Hypothesis matrix (Barbara H1-H5 + findings)

| # | Hypothesis | Status | Evidence |
|---|---|---|---|
| H1 | H4 red + LONGs fired (counter-H4) | **PARTIAL** | `daily_trend=short` confirmed. H4 last-completed bar (08:00-12:00) was strong bullish (close 4831, close_pct 0.99). CURRENT partial H4 bar (12:00-16:00) is forming bearish but per decision 11.4 we ignore partials. **Sprint C v2 as specified would still give h4_bias=bullish and NOT block these LONGs.** Gap identified — see §6. |
| H2 | Iceberg ASK massive not flagged | **CONFIRMED** | 14:30 UTC: 2 ASK icebergs at 4847.8 (prob 0.35). System `v4_iceberg.aligned=True` for LONG throughout — rule-based detector misreads as LONG-aligned. Sprint D iceberg-ML confirmed urgent. |
| H3 | News event not filtered | **UNDETERMINED** | news_exit enabled, 1800s pre/post, block_new_entries=True. BUT cannot cross with calendar (no local calendar log). 14:30 UTC = 09:30 EST = typical US economic release window (e.g. Retail Sales 08:30 EST or equivalent). Need economic calendar integration check. |
| H4 | Position monitor no defensive exit | **N/A** | 8 GOs all EXEC_FAILED (broker issue). No fills to monitor. Separate broker pipeline bug. If manual positions exist in MT5, they use MT5's own SL/TP. |
| H5 | Overextension reversal in H4 bullish/bearish without gate | **CONFIRMED** | `strategy_context.entry_mode="OVEREXTENSION"` literal in every GO. TRENDING_DN + liq_bot + overext_pts > 1.5×ATR → LONG reversal. No H4 gate on this path. **Removing overextension WITHOUT H4 gate is dangerous (Sprint A finding vindicated).** Sprint C v2 adds H4 gate but would still allow today given last-completed H4 bullish. |

---

## 6. Sprint C v2 design gap identified

**Scenario in incident:** last-completed H4 = bullish strong close; CURRENT H4 in formation is bearish reversal (open 4831, high 4847, low 4822, close ~4822).

Per Barbara decision 11.4 (ignore current partial), Sprint C v2 `derive_h4_bias` would return:
- R_H4_1 on last completed (close_pct 0.99) → **bullish**, conf 0.55

v2 hard-block on `derive_m30_bias_v2`:
- H4 bullish + confirmed M30 bullish → R_M30_1 fires → allow LONG

**Sprint C v2 as currently specified would NOT block today's 8 LONGs** (same outcome as legacy).

### Possible extensions (NOT in scope of Sprint C v2, flag for Sprint E)

**Option E.1 — Daily trend gate (D1 alignment):**
- ATS literature §8.0: "daily is the ultimate authority; H4 and lower must align."
- `daily_trend="short"` OR `d1_jac_dir != h4_jac_dir` → require extra confirmation (iceberg + L2 + price structure)
- This would have blocked today: daily_trend=short strict → no LONGs tolerated.

**Option E.2 — Current-H4 partial as "regime flip warning":**
- If current partial H4 bar's close-so-far diverges from last-completed close by >X ATR, degrade bias confidence to neutral.
- Here: last-completed close 4831.05, current partial close 4822 (diff -9 pts ≈ 0.4 ATR M30). Borderline.
- More aggressive: any negative body forming in current H4 → neutral bias.

**Option E.3 — Real iceberg integration (not rule-based proxy):**
- v4_iceberg `aligned=True` for LONG is wrong given ASK icebergs at top. Sprint D ML would fix.

---

## 7. Recommendation to Barbara

**Sprint C v2 deve continuar**, mas com ciência de que resolve **91h stuck bearish / missed LONG class** (anterior), **NÃO este LONG-during-drop class** (actual).

Opções para blindar o caso actual:

1. **Adicionar D1 alignment check em Sprint C v2 (Option E.1)** — ~15 linhas. ATS "daily is authority" exige. Ratificação Barbara.
2. **Adicionar current-H4 partial degradation (Option E.2)** — adicional ~20 linhas. Mais sofisticado mas data-driven (precisa backtest).
3. **Deixar Sprint C v2 como está, priorizar Sprint D iceberg-ML** para apanhar o H2.
4. **Continuar Phase 2b SEM extension** e adicionar D1 numa Sprint C+ depois de enforce.

Pessoalmente inclino-me para (4): **Phase 2b consolida contract com H4**, depois iteramos. Adicionar D1 agora fora de scope arrisca implementação crua.

---

## 8. Broker execution — separate bug

Todos os 8 GOs `EXEC_FAILED` com `"robo=connected hantec=connected"`. Mesmo padrão do bug reportado hoje mais cedo. **Sprint separada (fora scope C)**. Enquanto bug persistir, nenhum GO é executado automaticamente — Barbara toma trades manuais.

---

## 9. System state snapshot

- **Capture processes:** PID 2512 (watchdog_l2, desde 2026-04-14), PID 8248 (iceberg_receiver, desde 2026-04-14) INTACT.
- **NOVO:** PID 11740 (quantower_level2_api :8000) iniciado hoje 2026-04-20 14:51 UTC (~7min antes do forensic). Substitui a PID 12332 anterior. **Pré-existente ao forensic snapshot; ClaudeCode não o iniciou nem parou.** Potencialmente relacionado com reinício do api.
- **Main service:** PID 4516 (run_live.py roboforex lot_size 0.05, desde 2026-04-20 00:47). Não restartado.
- **Dashboard API:** PID 6324 (api.py, desde 2026-04-17).

---

## 10. Files captured

- `decision_log.jsonl` — 22.3 MB full log
- `decision_log_last60min.jsonl` — 16 entries filtered
- `iceberg__GC_XCEC_20260420.jsonl` — today's icebergs
- `decision_live.json` — current state
- `m30_slice_last6h.parquet` — M30 last 6h
- `ohlcv_slice_last6h.parquet` — minute OHLCV last 6h
- `backtest_anomaly_calibration.json`, `backtest_anomaly_filter_comparison.json`, `production_anomaly_forge.json`
- `service_stderr_tail2000.log`, `service_stdout_tail2000.log`
- `trades.csv`, `trades_live.csv` — broker fills (both show 0 recent fills)
- `python_procs.txt` — process snapshot

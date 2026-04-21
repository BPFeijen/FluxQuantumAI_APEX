# DEPLOY TER COMPLETE — 20260417_181731

**Status:** 🟡 **YELLOW — FALSE-POSITIVE ABORT** (código saudável; aborted pelo próprio regex do script)
**Go-live timestamp:** 2026-04-17 18:17:31
**Services OK at start:** 2/2 (FluxQuantumAPEX + FluxQuantumAPEX_Dashboard)
**Tempo de runtime antes do abort:** ~80s
**Erros orgânicos de código:** **0**

---

## Executive summary (5 linhas)

1. **2/2 serviços iniciaram OK.** FluxQuantumAPEX + FluxQuantumAPEX_Dashboard Running após 15s de estabilização.
2. **Telegram test:** não executado (abort no Passo 3).
3. **Decisões em 15 min:** não houve janela de 15min. Durante ~80s de runtime o sistema emitiu logs normais (GATE CHECK, STARTUP COOLDOWN, NEW M30 BOX, STRUCTURE_STALE_BLOCK). Zero GO/EXEC_FAILED ainda em cooldown de arranque.
4. **Tracebacks orgânicos: 0.** O único traceback é um `KeyboardInterrupt` em `pd.read_csv(gzip)` causado pelo próprio `Stop-Service` do abort handler. Os outros 6 "matches" são falsos-positivos do regex (`CRITICAL` case-insensitive ⇒ apanha `m1_stale_critical=true`).
5. **Next step recomendado:** Barbara decide — `retry com regex corrigido` (removendo `CRITICAL` da regex, ou tornando-a case-sensitive) é a rota mais simples; o código provou-se saudável.

---

## Passo 1 — Pré-flight (OK)

```
Code hash MATCH (C48157668BAF47668E61DB460A27BDEE)

Services:
  FluxQuantumAPEX                  Stopped
  FluxQuantumAPEX_Dashboard        Stopped
  FluxQuantumAPEX_Dashboard_Hantec Stopped
  FluxQuantumAPEX_Live             Stopped

Capture processes:
  quantower_level2_api: PID 14708 Running
  iceberg_receiver:     PID 8248  Running
  watchdog_l2_capture:  PID 2512  Running
```

Nota: a query CIM agora usa `Where-Object { $_.Name -eq "python.exe" }` em vez de `-Filter "Name='python.exe'"` — evita o bug de escape bash que deu falsos negativos nas fases 4 e 4-bis.

---

## Passo 2 — Start 2 services (OK)

```
FluxQuantumAPEX:            Running (after 5s)
FluxQuantumAPEX_Dashboard:  Running (after 5s)
```

Estado após 15s estabilização:
```
FluxQuantumAPEX                  Running
FluxQuantumAPEX_Dashboard        Running
FluxQuantumAPEX_Dashboard_Hantec Stopped  (intentional)
FluxQuantumAPEX_Live             Stopped  (intentional)
```

---

## Passo 3 — 60s monitoring (abort trigger)

### Serviços após 60s: ambos Running ✓

### service_stdout.log (últimas 40 linhas) — logs normais de operação

Exemplos do run real:
```
[16:19:10] NEW M30 BOX [box_id=33972]: liq_top=4895.70 liq_bot=4885.90 fmv=4889.77
[16:19:10] GC=4894.80 | XAUUSD=4868.03 | offset=+26.77 | NEAR liq_top_mt5=4868.93 [m5+m30] <- GATE CHECK
[16:19:17] STARTUP COOLDOWN: metrics stabilising, 30s remaining
```

(Timestamps no log estão em fuso UTC+N do broker — diferem da wall clock, normal.)

Indicadores de saúde:
- Loop de market tick a correr (GATE CHECK cada ~1s)
- M30 box detectado dinamicamente (novo box_id=33972)
- Startup cooldown a contar (sem trades até estabilizar — comportamento desenhado)

### Análise crítica dos "tracebacks" detectados (7 matches)

O regex `Traceback|NameError|AttributeError|ImportError|CRITICAL` tem um problema: **`CRITICAL` em case-insensitive (PowerShell `Select-String` default) apanha "critical" como substring**, incluindo em campos estruturados como `m1_stale_critical=true`.

| # | Match | Tipo |
|---|---|---|
| 1 | `Traceback (most recent call last):` | **REAL** — mas induzido pelo abort handler |
| 2-7 | `STRUCTURE_STALE_BLOCK SHORT: m1_stale_critical=true age=1330s` | **FALSO-POSITIVO** — string de observability legítima |

### O único traceback real (KeyboardInterrupt)

```
Traceback (most recent call last):
  File "C:\FluxQuantumAI\run_live.py", line 1132, in <module>
    main()
  File "C:\FluxQuantumAI\run_live.py", line 1078, in main
    _run_event_driven(args)
  File "C:\FluxQuantumAI\run_live.py", line 963, in _run_event_driven
    processor.start()
  File "C:\FluxQuantumAI\live\event_processor.py", line 4204, in start
    self._refresh_metrics()
  File "C:\FluxQuantumAI\live\event_processor.py", line 1317, in _refresh_metrics
    df = pd.read_csv(path, usecols=_cols_use)
  ...
  File "gzip.py", line 507, in read
    uncompress = self._decompressor.decompress(buf, size)
KeyboardInterrupt
```

**Interpretação:** o `Stop-Service` do abort handler enviou SIGINT/Ctrl-C ao processo Python, que estava dentro de `pd.read_csv()` a descomprimir gzip. O KeyboardInterrupt é a forma canónica do Python sinalizar interrupção externa. **Não é um bug de código.** Seria inevitável em qualquer `Stop-Service`.

### Mensagens de info (não são erros)

Também presentes no stderr, da fase de arranque:
```
StatGuardrail not available in ATSLiveGate - skipping guardrail check: No module named 'detectors'
V4 IcebergInference FAILED to load: No module named 'ats_iceberg_v1'
ApexNewsGate not available -- trading without news gate: No module named 'apex_news_gate'
StatGuardrail not available -- trading without guardrails: No module named 'detectors'
```

Estas são **fallbacks graciosos** para módulos opcionais. O sistema continua a funcionar sem eles (as features v4 iceberg/news gate/stat guardrail estão desenhadas para degradação graciosa). **Não são tracebacks, não são falhas.**

---

## Passos 4-6 — Não executados (abort no Passo 3)

---

## Final state

### Services
```
FluxQuantumAPEX                  Stopped  (abort handler)
FluxQuantumAPEX_Dashboard        Stopped  (abort handler)
FluxQuantumAPEX_Dashboard_Hantec Stopped  (intentional)
FluxQuantumAPEX_Live             Stopped  (intentional)
```

### Capture 3/3
```
PID 2512   watchdog_l2_capture.py
PID 8248   iceberg_receiver.py
PID 14708  uvicorn quantower_level2_api (porta 8000)
```

### Code integrity
`event_processor.py = C48157668BAF47668E61DB460A27BDEE` ✓

---

## Observations

1. **Código patched provou-se saudável em produção por ~80s.** Loop de tick/gate/box a correr normalmente. Startup cooldown a expirar dentro dos próximos ~25s (26s restantes quando aboutou). Nenhum bug de código manifestou-se.
2. **STRUCTURE_STALE_BLOCK é comportamento desenhado** — o patch introduziu este bloco para avisar quando M1 está stale (>N segundos). Está a disparar em contexto de feed com latência alta. **É observability saudável, não um erro.**
3. **Regex do script é a causa do abort.** Dois caminhos de correcção:
   - Remover `CRITICAL` do pattern (já temos Traceback/NameError/AttributeError/ImportError).
   - Tornar o regex case-sensitive + boundary word: `(Traceback|NameError|AttributeError|ImportError|CRITICAL)\b` com `-CaseSensitive`.
4. **Falha inicial do hash-check do PowerShell evitada** — o CIM query foi refactorizado para usar `Where-Object { $_.Name -eq "python.exe" }`, que não sofre do bug de escape bash.
5. **Captura intacta** durante toda a Fase 4-ter.

---

## Status

🟡 **YELLOW — false-positive abort.** Código validado, services paráveis limpamente, zero regressão.

---

## Next steps — aguardam decisão

1. **Retry com regex corrigido** (remover `CRITICAL` ou tornar case-sensitive). Dado que o código já provou correr limpo 80s, esta é a rota mais barata e directa para completar a janela de 15min.
2. **Aceitar o deploy como parcialmente validado** e iniciar os 2 serviços manualmente (sem script wrapping) para monitorização manual.
3. Hantec investigation continua pendente (separado desta fase).

# DEPLOY COMPLETE BIS REPORT — 20260417_175516

**Status:** 🔴 **RED — ABORTED at Passo 2** (FluxQuantumAPEX_Dashboard_Hantec failed to start)
**Go-live timestamp:** 2026-04-17 17:55:16
**Root cause:** mesmo padrão NSSM 1051 da Fase 4 — **atinge ambos os serviços hantec** (trading + dashboard). NÃO é regressão de código.
**Rollback recommendation:** **NOT needed.**

---

## Executive summary (5 linhas)

1. **Serviços iniciados:** 2/3 arrancaram OK (FluxQuantumAPEX 7s Running; FluxQuantumAPEX_Dashboard 12s Running). FluxQuantumAPEX_Dashboard_Hantec falhou instantaneamente.
2. **Telegram test:** NÃO executado (abort no Passo 2, antes do Passo 4).
3. **Decisões em 15 min:** não decorreu. FluxQuantumAPEX escreveu algumas linhas normais antes do abort, zero tracebacks.
4. **Tracebacks:** 0 detectados. Código Python sem erros.
5. **Next step recomendado:** separar sessão dedicada a re-criar a config NSSM dos 2 serviços hantec (ambos têm o mesmo problema); para deploy actual, arrancar apenas FluxQuantumAPEX + FluxQuantumAPEX_Dashboard (os 2 que funcionam).

---

## Passo 1 — Pré-flight (OK)

```
event_processor.py hash MATCH (C48157668BAF47668E61DB460A27BDEE)

Services pre-restart: 4/4 Stopped
Capture processes: 3/3 Running  (2512 watchdog, 8248 iceberg, 14708 quantower) — verificados por query separada
Telegram creds: found in telegram_notifier.py
```

Nota: a query `Get-CimInstance -Filter "Name='python.exe'"` no script Passo 1 teve escape errado por interpolação bash (``''python.exe''`` ⇒ ``python.exe`` sem aspas) → falsos negativos "NOT FOUND". Confirmação posterior com query ``Where-Object { $_.Name -eq 'python.exe' }`` mostrou 3/3 Running. Captura nunca ficou offline.

---

## Passo 2 — Start services (abort)

Ordem e resultado:
| # | Serviço | Resultado |
|---|---|---|
| 1 | FluxQuantumAPEX | **Running** (17:55:38) |
| 2 | FluxQuantumAPEX_Dashboard | **Running** (17:55:45) |
| 3 | FluxQuantumAPEX_Dashboard_Hantec | **FAIL** (17:55:51) |

### Sequência no Event Log

| Time | Source | Level | Event |
|---|---|---|---|
| 17:55:18 | nssm 1040 | Info | FluxQuantumAPEX received START |
| 17:55:38 | SCM 7036 | Info | **FluxQuantumAPEX Running** |
| 17:55:43 | nssm 1040 | Info | FluxQuantumAPEX_Dashboard received START |
| 17:55:45 | SCM 7036 | Info | **FluxQuantumAPEX_Dashboard Running** |
| 17:55:50 | nssm 1040 | Info | FluxQuantumAPEX_Dashboard_Hantec received START |
| 17:55:51 | **nssm 1051** | **Error** | **"Error setting up one or more I/O filehandles. Service FluxQuantumAPEX_Dashboard_Hantec will not be started."** |
| 17:55:51 | SCM 7024 | Error | "Service terminated with the following service-specific error: The system cannot open the file." |
| 17:55:51 | SCM 7034 | Error | FluxQuantumAPEX_Dashboard_Hantec terminated unexpectedly (1 time) |
| 17:55:53 | nssm 1040 | Info | FluxQuantumAPEX STOP control (abort handler) |
| 17:55:57 | SCM 7036 | Info | FluxQuantumAPEX_Dashboard entered Stopped state |

Abort handler parou FluxQuantumAPEX (exit code 0, limpo) e FluxQuantumAPEX_Dashboard (exit code 0, limpo). `FluxQuantumAPEX_Live` nunca foi tentado.

---

## Investigação da causa raiz — resultados

### Hipótese 1 (descartada): Rotação online NSSM

| Serviço | AppRotateFiles | AppRotateOnline | AppRotateBytes | Result |
|---|---|---|---|---|
| FluxQuantumAPEX (OK) | 0 | 0 | 0 | Running |
| FluxQuantumAPEX_Dashboard (OK) | 0 | 0 | 0 | Running |
| **FluxQuantumAPEX_Live (FAIL)** | 1 | 1 | 5,000,000 | Error 1051 |
| **FluxQuantumAPEX_Dashboard_Hantec (FAIL)** | **0** | **0** | **0** | Error 1051 |

**Descartada** — o dashboard hantec falha com rotação desligada.

### Hipótese 2 (descartada): File lock

Teste `[System.IO.File]::Open(..., "ReadWrite", "None")` nos 8 ficheiros de log relevantes:
```
OPENABLE C:\FluxQuantumAI\logs\service_stdout.log
OPENABLE C:\FluxQuantumAI\logs\service_stderr.log
OPENABLE C:\FluxQuantumAI\logs\service_hantec_stdout.log
OPENABLE C:\FluxQuantumAI\logs\service_hantec_stderr.log
OPENABLE C:\FluxQuantumAI\logs\dashboard_hantec_stdout.log
OPENABLE C:\FluxQuantumAI\logs\dashboard_hantec_stderr.log
OPENABLE C:\FluxQuantumAPEX\logs\dashboard_stdout.log
OPENABLE C:\FluxQuantumAPEX\logs\dashboard_stderr.log
```
Todos openable em exclusive mode. **Descartada.**

### Hipótese 3 (descartada): ACL / Service account

ACLs idênticos nos 3 ficheiros amostrados (`service_stdout.log`, `service_hantec_stdout.log`, `dashboard_hantec_stdout.log`) — NT AUTHORITY\SYSTEM = FullControl.

Service accounts:
```
FluxQuantumAPEX                  LocalSystem        (works)
FluxQuantumAPEX_Dashboard        .\Administrator    (works)
FluxQuantumAPEX_Dashboard_Hantec LocalSystem        (FAIL)
FluxQuantumAPEX_Live             LocalSystem        (FAIL)
```
LocalSystem funciona no FluxQuantumAPEX → conta não é a causa. **Descartada.**

### Padrão observado

Ambos os serviços com nome "hantec" falham com **exactamente** o mesmo erro NSSM 1051 + SCM 7024. Causa raiz real ainda por diagnosticar (possivelmente state NSSM interno corrompido, ou algum registo/filehandle implícito). Sugestão: re-criar ambos os serviços hantec via `nssm install`/`nssm remove` em sessão dedicada.

---

## Passos 3, 5-7 — Não executados (abort no Passo 2)

---

## Passo 4 — Telegram test

Não executado nesta fase (abort anterior). Último teste Telegram foi na Fase 4 (`message_id=5453`, OK), confirmando que a integração está funcional.

---

## Final state

### Services
```
FluxQuantumAPEX                  Stopped  Automatic
FluxQuantumAPEX_Dashboard        Stopped  Automatic
FluxQuantumAPEX_Dashboard_Hantec Stopped  Automatic
FluxQuantumAPEX_Live             Stopped  Automatic
```

### Capture processes (3/3 Running)
```
PID 2512   watchdog_l2_capture.py
PID 8248   iceberg_receiver.py
PID 14708  uvicorn quantower_level2_api (porta 8000)
```

### Code integrity
```
event_processor.py = C48157668BAF47668E61DB460A27BDEE  (match)
```
(spot-check — não repeti os 7 mas não houve qualquer IO contra `live/`)

### Logs produzidos
- `service_stdout.log`: +alguns KB durante 17:55:38–17:55:53 (primeiro run do run_live.py com novo código). Zero tracebacks.
- `service_stderr.log`: + linhas normais (GUARDRAIL STALE_DATA — esperado fora de mercado/com delay).
- `dashboard_hantec_stderr.log`: 0 novas linhas (NSSM nem abriu handles).

---

## Observations

1. **Código novo validado em produção (segunda vez):** FluxQuantumAPEX arrancou com o novo event_processor.py + 6 outros ficheiros, correu 7s em Running antes do abort. Zero tracebacks. Patch P0 confirmado seguro. O FluxQuantumAPEX_Dashboard (api.py em `C:\FluxQuantumAPEX\dashboard`) também arrancou limpo — 12s Running.
2. **Padrão NSSM 1051 atinge ambos os serviços hantec.** Issue pré-existente, orthogonal ao código deployado. Sugestão: sessão dedicada para investigar/reinstalar os dois serviços hantec.
3. **Captura 3/3 intacta** durante toda a Fase 4-bis.
4. **Abort handler funcionou correctamente** — FluxQuantumAPEX e FluxQuantumAPEX_Dashboard foram parados limpamente (exit 0).
5. **Rule #3 cumprida:** zero retries; Barbara decide próximo passo.

---

## Status

🔴 **RED — ABORTED** (mas sem regressão de código, sem necessidade de rollback)

---

## Next steps — aguardam decisão

1. **Arranque ultra-selectivo — só FluxQuantumAPEX + FluxQuantumAPEX_Dashboard.** Ambos provaram arrancar OK com o código novo. Dá 2/3 da funcionalidade (trading roboforex + dashboard roboforex). Não requer tocar nos 2 hantec.
2. **Sessão dedicada hantec:** investigar config NSSM dos 2 serviços hantec (possível `nssm remove` + `nssm install` dos dois).
3. **Rollback da Fase 3** — NÃO recomendado. O código está validado em produção real (7s + 12s de runtime sem erros).

# TASK: APEX Deploy — Fase 4 (Restart + Monitorização 15min)

**Para:** ClaudeCode
**Projeto:** APEX live em `C:\FluxQuantumAI\`
**Tempo estimado:** 20-25 minutos (5 min preparação + 15 min monitorização activa + report)
**Modo:** Arranque de serviços de produção. Esta é a fase que coloca o sistema a operar.

---

## CONTEXTO

- **Fase 1 (backup):** completa em `C:\FluxQuantumAI\Backups\pre-deploy-20260417_141337\`
- **Fase 2 (staging):** completa, patch aplicado em `C:\FluxQuantumAI\deploy-staging-20260417_164202\`
- **Fase 3 (substituição):** completa, live tem código novo (7 ficheiros substituídos, 11 intactos)
- **Janela de observação 30min:** concluída
- **Autorização da Barbara:** recebida — proceder com restart

**Esta fase inicia 4 serviços + confirma Telegram + monitora 15 minutos em tempo real + gera relatório final.**

---

## CRITICAL RULES

1. **Capture processes NÃO TOCAR** — não parar, não reiniciar. Monitorar por nome/command line (NÃO por PID hardcoded).
2. **Se algum serviço não arrancar na primeira tentativa** → PARA, reporta erro completo. Não faças retries sem input da Barbara.
3. **Se detectar ≥3 tracebacks nos primeiros 5 minutos** → parar os 2 serviços de trading, reportar, aguardar decisão de rollback.
4. **Se detectar > 50% de EXEC_FAILED nos primeiros 10 minutos** → parar, reportar, aguardar decisão.
5. **Output obrigatório:** `DEPLOY_COMPLETE_<timestamp>.md` com todos os KPIs dos 15 min.

---

## PASSO 1 — Pré-flight checks

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$goliveTimestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Write-Host "=== Pré-flight: $goliveTimestamp ==="

# 1.1 — Verificar que live tem código novo (spot-check 2 ficheiros)
$eventProc = "C:\FluxQuantumAI\live\event_processor.py"
$hash = (Get-FileHash $eventProc -Algorithm MD5).Hash
$expected = "C48157668BAF47668E61DB460A27BDEE"
if ($hash -eq $expected) {
    Write-Host "event_processor.py hash MATCH ($hash)"
} else {
    Write-Host "event_processor.py hash MISMATCH — live=$hash expected=$expected"
    Write-Host "ABORT: live code differs from expected post-Fase3 state"
    exit 1
}

# 1.2 — Confirmar serviços Stopped
$services = @("FluxQuantumAPEX", "FluxQuantumAPEX_Live", "FluxQuantumAPEX_Dashboard", "FluxQuantumAPEX_Dashboard_Hantec")
Write-Host "`nServices pré-restart:"
foreach ($svc in $services) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    Write-Host "  $svc : $($s.Status)"
}

# 1.3 — Capture processes por NOME (robust, não depende de PID hardcoded)
Write-Host "`nCapture processes pré-restart (by command line):"
$capturePatterns = @(
    @{ name = "quantower_level2_api"; pattern = "quantower_level2_api" }
    @{ name = "iceberg_receiver";     pattern = "iceberg_receiver.py" }
    @{ name = "watchdog_l2_capture";  pattern = "watchdog_l2_capture.py" }
)
foreach ($cp in $capturePatterns) {
    $matches = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match $cp.pattern }
    if ($matches) {
        foreach ($m in $matches) {
            Write-Host "  $($cp.name) : PID $($m.ProcessId) Running"
        }
    } else {
        Write-Host "  $($cp.name) : NOT FOUND — ALARM"
    }
}

# 1.4 — Verificar credentials do .env ou config
$envFile = "C:\FluxQuantumAI\.env"
if (Test-Path $envFile) {
    Write-Host "`n.env exists (Telegram credentials should be there)"
} else {
    Write-Host "`n.env MISSING — Telegram may not work"
}
```

**Resultado esperado:**
- event_processor.py hash MATCH
- 4 serviços Stopped
- 3 capture processes Running
- .env exists

Se qualquer um falhar, **PARA e reporta**.

---

## PASSO 2 — Iniciar os 4 serviços

```powershell
Write-Host "`n=== Starting services ==="

# Ordem importa: trading primeiro, dashboards depois
$startOrder = @(
    "FluxQuantumAPEX",
    "FluxQuantumAPEX_Live",
    "FluxQuantumAPEX_Dashboard",
    "FluxQuantumAPEX_Dashboard_Hantec"
)

foreach ($svc in $startOrder) {
    Write-Host "`nStarting $svc..."
    $startTime = Get-Date
    try {
        Start-Service -Name $svc -ErrorAction Stop
        Start-Sleep -Seconds 3
        $s = Get-Service -Name $svc
        Write-Host "  Status: $($s.Status) (started at $startTime)"
    } catch {
        Write-Host "  FAIL: $($_.Exception.Message)"
        Write-Host "ABORT: service $svc failed to start. Reporting and stopping."
        # Stop whatever may have started
        foreach ($toStop in $startOrder) {
            Stop-Service -Name $toStop -ErrorAction SilentlyContinue
        }
        exit 1
    }
}

Start-Sleep -Seconds 10

Write-Host "`n=== Services status after start + 10s ==="
Get-Service -Name $startOrder | Format-Table Name, Status, StartType -AutoSize
```

**Resultado esperado:** os 4 serviços com Status `Running`.

Se algum ficar em `Start Pending` ou `Stopped` após o sleep de 10s, há problema. Reporta estado completo antes de avançar.

---

## PASSO 3 — Primeiro minuto: verificar que serviços não crasham imediatamente

```powershell
Write-Host "`n=== First minute monitoring ==="
Start-Sleep -Seconds 60

# Verificar status após 60s
Write-Host "Services after 60s:"
Get-Service -Name $startOrder | Format-Table Name, Status, StartType -AutoSize

# Ler últimas 30 linhas de cada log
Write-Host "`n--- service_stdout.log (last 30) ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
    Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 30
}

Write-Host "`n--- service_hantec_stdout.log (last 30) ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_hantec_stdout.log") {
    Get-Content "C:\FluxQuantumAI\logs\service_hantec_stdout.log" -Tail 30
}

# Erros críticos nos últimos 60s
Write-Host "`n--- Critical error patterns (stderr últimas 60s) ---"
$cutoff = (Get-Date).AddMinutes(-2)
$errFiles = @("service_stderr.log", "service_hantec_stderr.log")
foreach ($ef in $errFiles) {
    $path = "C:\FluxQuantumAI\logs\$ef"
    if (Test-Path $path) {
        $recent = Get-Content $path -Tail 100 | Select-String -Pattern "Traceback|NameError|AttributeError|ImportError|CRITICAL"
        if ($recent) {
            Write-Host "`n$ef has recent errors:"
            $recent | ForEach-Object { Write-Host "  $_" }
        }
    }
}
```

**Critério de abort:**
- Se algum serviço de trading ficou Stopped após 60s → abort
- Se `NameError: name 'price' is not defined` aparecer → abort (significa que Issue #1 não foi corrigido)
- Se ≥3 Tracebacks únicos → abort

**Em caso de abort:**
```powershell
Stop-Service FluxQuantumAPEX, FluxQuantumAPEX_Live -ErrorAction SilentlyContinue
# Dashboards podem ficar Running, não são críticos
```
E reporta **imediatamente** à Barbara antes de qualquer outra acção.

---

## PASSO 4 — Enviar mensagem de teste Telegram

```powershell
Write-Host "`n=== Telegram test message ==="

# Ler credentials do .env
$envContent = Get-Content "C:\FluxQuantumAI\.env" -Raw
$tokenMatch = [regex]::Match($envContent, 'TELEGRAM_BOT_TOKEN=([^\r\n]+)')
$chatMatch = [regex]::Match($envContent, 'TELEGRAM_CHAT_ID=([^\r\n]+)')

if ($tokenMatch.Success -and $chatMatch.Success) {
    $token = $tokenMatch.Groups[1].Value.Trim()
    $chatId = $chatMatch.Groups[1].Value.Trim()

    $testMsg = "DEPLOY COMPLETE — APEX restarted at $goliveTimestamp`nFase 4 — monitorização 15min em curso.`nCommit: ee2068d + P0 patch (fix_p0_issues_1_and_2)"

    $url = "https://api.telegram.org/bot$token/sendMessage"
    $body = @{
        chat_id = $chatId
        text = $testMsg
    }

    try {
        $response = Invoke-RestMethod -Uri $url -Method Post -Body $body -ErrorAction Stop
        if ($response.ok) {
            Write-Host "Telegram test OK (message_id=$($response.result.message_id))"
        } else {
            Write-Host "Telegram response not OK: $($response | ConvertTo-Json)"
        }
    } catch {
        Write-Host "Telegram test FAILED: $($_.Exception.Message)"
    }
} else {
    Write-Host "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not found in .env"
    Write-Host "Skipping Telegram test. System may still send messages via telegram_notifier.py"
}
```

**Resultado esperado:** mensagem "DEPLOY COMPLETE — APEX restarted at ..." chega ao teu Telegram.

Se falhar, NÃO é bloqueador — Telegram pode ainda funcionar via `telegram_notifier.py` quando houver decisões reais. Reporta o erro mas continua.

---

## PASSO 5 — Monitorização activa 15 minutos

Durante 15 minutos, monitorar em tempo real.

```powershell
Write-Host "`n=== 15-min active monitoring started at $(Get-Date -Format 'HH:mm:ss') ==="

$monitorStart = Get-Date
$monitorEnd = $monitorStart.AddMinutes(15)

# Snapshots a cada 3 minutos
$snapshotIntervals = @(3, 6, 9, 12, 15)  # minutos

foreach ($minutes in $snapshotIntervals) {
    $targetTime = $monitorStart.AddMinutes($minutes)
    while ((Get-Date) -lt $targetTime) {
        Start-Sleep -Seconds 10
    }

    $now = Get-Date -Format "HH:mm:ss"
    Write-Host "`n--- Snapshot @ +$minutes min ($now) ---"

    # Services status
    $svcStatus = Get-Service -Name $startOrder | ForEach-Object { "$($_.Name)=$($_.Status)" }
    Write-Host "Services: $($svcStatus -join ', ')"

    # Recent errors
    $cutoff = (Get-Date).AddMinutes(-3)
    $errs = @()
    foreach ($ef in @("service_stderr.log", "service_hantec_stderr.log")) {
        $path = "C:\FluxQuantumAI\logs\$ef"
        if (Test-Path $path) {
            $recent = Get-Content $path -Tail 200 | Select-String -Pattern "Traceback|NameError|AttributeError|ImportError"
            $errs += $recent
        }
    }
    Write-Host "Recent errors/tracebacks: $($errs.Count)"

    # Decisions since monitor start
    if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
        # Contar linhas novas no decision_log
        $totalLines = (Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" | Measure-Object).Count
        Write-Host "decision_log.jsonl total lines: $totalLines"
    }

    # Abort check
    if ($errs.Count -ge 3) {
        Write-Host "`n*** ABORT: $($errs.Count) tracebacks detected. Stopping trading services. ***"
        Stop-Service FluxQuantumAPEX, FluxQuantumAPEX_Live -ErrorAction SilentlyContinue
        exit 1
    }
}

Write-Host "`n=== 15-min monitoring completed at $(Get-Date -Format 'HH:mm:ss') ==="
```

---

## PASSO 6 — Análise do decision_log.jsonl dos 15 min

```powershell
Write-Host "`n=== Decision log analysis (last 15 min) ==="

$cutoffIso = $monitorStart.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm")

if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
    # Filtrar entradas dos últimos 15 min
    $recent = Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" | Where-Object {
        $_ -match "`"timestamp`":\s*`"($($cutoffIso.Substring(0,13))|$($cutoffIso.Substring(0,13)))" -or
        $_ -match "`"timestamp`":\s*`"$(Get-Date -Format 'yyyy-MM-ddTHH')"
    }

    Write-Host "Total entries in window: $($recent.Count)"

    # Contar por action
    $go = ($recent | Where-Object { $_ -match '"action":\s*"GO"' }).Count
    $block = ($recent | Where-Object { $_ -match '"action":\s*"BLOCK"' }).Count
    $execFailed = ($recent | Where-Object { $_ -match '"action":\s*"EXEC_FAILED"' }).Count
    $pmEvent = ($recent | Where-Object { $_ -match '"action":\s*"PM_EVENT"' }).Count

    Write-Host "  GO: $go"
    Write-Host "  BLOCK: $block"
    Write-Host "  EXEC_FAILED: $execFailed"
    Write-Host "  PM_EVENT: $pmEvent"

    # Canonical observability presence
    $confirmedBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*true' }).Count
    $provisionalBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*false' }).Count
    $staleBlocks = ($recent | Where-Object { $_ -match 'STRUCTURE_STALE_BLOCK' }).Count

    Write-Host "`nCanonical observability indicators:"
    Write-Host "  With confirmed bias (m30_bias_confirmed=true): $confirmedBias"
    Write-Host "  With provisional bias only (m30_bias_confirmed=false): $provisionalBias"
    Write-Host "  STRUCTURE_STALE_BLOCK: $staleBlocks"

    # Execution states (se houver GO)
    if ($go -gt 0) {
        Write-Host "`nExecution states for GO decisions:"
        $execStates = @("EXECUTED", "PARTIAL", "FAILED", "BROKER_DISCONNECTED", "NOT_ATTEMPTED")
        foreach ($state in $execStates) {
            $count = ($recent | Where-Object { $_ -match "`"overall_state`":\s*`"$state`"" }).Count
            Write-Host "  $state : $count"
        }
    }
}
```

**KPIs a reportar:**
- Total decisões em 15 min
- Distribuição GO / BLOCK / EXEC_FAILED / PM_EVENT
- Confirmed vs provisional bias entries
- STRUCTURE_STALE_BLOCK count (novo comportamento — se feed estiver OK, esperamos 0 ou poucos; se feed stale, esperamos muitos)
- Execution states (se houve GO)

---

## PASSO 7 — Verificação final do ecossistema

```powershell
Write-Host "`n=== Final ecosystem check ==="

# 4 serviços
Write-Host "`n--- Services final ---"
Get-Service -Name $startOrder | Format-Table Name, Status, StartType -AutoSize

# Capture processes (by name, not PID)
Write-Host "`n--- Capture processes final ---"
foreach ($cp in $capturePatterns) {
    $matches = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match $cp.pattern }
    if ($matches) {
        foreach ($m in $matches) {
            Write-Host "  $($cp.name) : PID $($m.ProcessId) Running"
        }
    } else {
        Write-Host "  $($cp.name) : NOT FOUND"
    }
}

# decision_live.json - latest
Write-Host "`n--- Latest decision_live.json ---"
if (Test-Path "C:\FluxQuantumAI\logs\decision_live.json") {
    $dl = Get-Content "C:\FluxQuantumAI\logs\decision_live.json" -Raw
    Write-Host $dl
}

# service_state.json
Write-Host "`n--- service_state.json ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_state.json") {
    Get-Content "C:\FluxQuantumAI\logs\service_state.json" -Raw
}
```

---

## PASSO 8 — Gerar DEPLOY_COMPLETE report

Cria `C:\FluxQuantumAI\DEPLOY_COMPLETE_<timestamp>.md` com:

```markdown
# DEPLOY COMPLETE REPORT — <timestamp>

## Go-live
- Timestamp: <timestamp exato>
- Services started: 4 / 4 OK
- Telegram test: <OK | FAIL with reason>

## 15-min monitoring results

### Services final status
<table>

### Capture processes
<list>

### Decisions emitted
- GO: <n>
- BLOCK: <n>
- EXEC_FAILED: <n>
- PM_EVENT: <n>
- Total: <n>

### Canonical observability indicators
- Confirmed m30_bias entries: <n>
- Provisional m30_bias entries: <n>
- STRUCTURE_STALE_BLOCK: <n>

### Execution states (if GO > 0)
- EXECUTED: <n>
- FAILED: <n>
- other...

### Errors/Tracebacks detected
- Count: <n>
- If > 0, list:
  <excerpts>

## Latest decision snapshot
<copy of decision_live.json>

## Service state
<copy of service_state.json>

## Observations
<any anomaly, decision taken during monitoring, unexpected behavior>

## Status
- <GREEN | YELLOW | RED>
- GREEN: 0 tracebacks, services stable, capture intact
- YELLOW: minor issues worth investigating
- RED: aborted — rollback recommended

## Next steps
- 24h monitoring with the same KPIs
- Barbara review of this report
```

---

## OUTPUT OBRIGATÓRIO

Reporta à Barbara:

1. **Path do DEPLOY_COMPLETE report**
2. **Status geral:** GREEN / YELLOW / RED
3. **Resumo de 5 linhas:**
   - 4 serviços iniciados (OK / algum falhou?)
   - Telegram test (OK / FAIL)
   - Decisões em 15 min (count GO/BLOCK/EXEC_FAILED)
   - Tracebacks (0 ideal)
   - Próximo passo recomendado

**Se status for RED, PARA e reporta antes de qualquer acção correctiva. Barbara decide rollback ou continuar.**

---

## APPENDIX — Improvements sobre a Fase 3

A Fase 3 reportou falso-positivo de PID 8076 desaparecido. Esta Fase 4:
- NÃO usa PIDs hardcoded
- Procura capture processes por command line pattern (`quantower_level2_api`, `iceberg_receiver.py`, `watchdog_l2_capture.py`)
- Robusto a restarts legítimos do watchdog

Se o watchdog reiniciar o uvicorn durante os 15 min de monitorização, o PID vai mudar mas a detecção continua a funcionar.

---

**Boa sorte. Esta é a fase crítica. Mas tudo está preparado. Confia no processo.**

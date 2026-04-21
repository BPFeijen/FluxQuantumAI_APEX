# TASK: APEX Deploy — Fase 4-bis (Restart RoboForex + Dashboards)

**Para:** ClaudeCode
**Projeto:** APEX live em `C:\FluxQuantumAI\`
**Tempo estimado:** 20-25 minutos (5 min preparação + 15 min monitorização + report)
**Modo:** Arranque **selectivo** — só FluxQuantumAPEX (RoboForex) + 2 dashboards. Hantec fica Stopped.

---

## CONTEXTO

- **Fase 4 anterior abortou** no start do FluxQuantumAPEX_Live por NSSM error 1051 (config de rotação online — issue NSSM, não código).
- **FluxQuantumAPEX (RoboForex) arrancou OK** durante 6s com o código novo — zero tracebacks, patch validado vivo.
- **Decisão Barbara:** começar só com RoboForex + Dashboards para validar código em produção real. Hantec fica para investigação NSSM separada (próxima sessão dedicada).
- **Serviços actualmente:** todos Stopped. Capture processes Running (3 PIDs detectados por nome).

**Esta task inicia 3 serviços (NÃO 4), monitora 15 min, confirma código saudável em run longo.**

---

## CRITICAL RULES

1. **NÃO iniciar FluxQuantumAPEX_Live (hantec).** Fica Stopped. Problema NSSM dele é para sessão separada.
2. **Capture processes NÃO TOCAR.** Procurar por command line (robusto a restarts do watchdog).
3. **Se FluxQuantumAPEX falhar no arranque** → PARA, reporta erro completo. Não retries sem input Barbara.
4. **Se ≥3 tracebacks nos primeiros 5 minutos** → parar FluxQuantumAPEX, reportar.
5. **Se EXEC_FAILED > 50% dos GOs nos primeiros 10 minutos** → parar, reportar.
6. **Output obrigatório:** `DEPLOY_COMPLETE_BIS_<timestamp>.md`.

---

## PASSO 1 — Pré-flight checks

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$goliveTimestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Write-Host "=== Pré-flight @ $goliveTimestamp ==="

# 1.1 — Verificar hash do código (spot-check)
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
Write-Host "`nServices pre-restart:"
foreach ($svc in $services) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    Write-Host "  $svc : $($s.Status)"
}

# 1.3 — Capture processes POR COMMAND LINE (robusto, não depende de PID)
Write-Host "`nCapture processes pre-restart (by command line):"
$capturePatterns = @(
    @{ name = "quantower_level2_api"; pattern = "quantower_level2_api" }
    @{ name = "iceberg_receiver";     pattern = "iceberg_receiver.py" }
    @{ name = "watchdog_l2_capture";  pattern = "watchdog_l2_capture.py" }
)
foreach ($cp in $capturePatterns) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match $cp.pattern }
    if ($procs) {
        foreach ($m in $procs) {
            Write-Host "  $($cp.name) : PID $($m.ProcessId) Running"
        }
    } else {
        Write-Host "  $($cp.name) : NOT FOUND — ALARM"
    }
}

# 1.4 — Confirmar Telegram credentials (hardcoded em telegram_notifier.py, não em .env)
$telegramCheck = Select-String -Path "C:\FluxQuantumAI\live\telegram_notifier.py" -Pattern "BOT_TOKEN" -List
if ($telegramCheck) {
    Write-Host "`nTelegram creds: found in telegram_notifier.py"
} else {
    Write-Host "`nTelegram creds: NOT found — messages may fail"
}
```

**Resultado esperado:**
- event_processor.py hash MATCH
- 4 serviços Stopped
- 3 capture processes Running (por command line)
- Telegram creds presentes

Se qualquer um falhar, **PARA e reporta**.

---

## PASSO 2 — Iniciar 3 serviços (NÃO 4)

**Ordem importa:** trading primeiro (roboforex), dashboards depois.

```powershell
Write-Host "`n=== Starting 3 services (roboforex + 2 dashboards) ==="

$startOrder = @(
    "FluxQuantumAPEX",
    "FluxQuantumAPEX_Dashboard",
    "FluxQuantumAPEX_Dashboard_Hantec"
)

# Nota: FluxQuantumAPEX_Live é intencionalmente OMITIDO

$startResults = @{}

foreach ($svc in $startOrder) {
    Write-Host "`nStarting $svc..."
    try {
        Start-Service -Name $svc -ErrorAction Stop
        Start-Sleep -Seconds 5  # Dar tempo ao NSSM + Python arrancar
        $s = Get-Service -Name $svc
        $startResults[$svc] = $s.Status
        Write-Host "  Status after 5s: $($s.Status)"

        if ($s.Status -ne "Running") {
            Write-Host "  FAIL: $svc did not reach Running state"
            Write-Host "ABORT: stopping all started services."
            foreach ($toStop in $startOrder) {
                Stop-Service -Name $toStop -ErrorAction SilentlyContinue
            }
            exit 1
        }
    } catch {
        Write-Host "  FAIL: $($_.Exception.Message)"
        Write-Host "ABORT: service $svc failed to start."
        foreach ($toStop in $startOrder) {
            Stop-Service -Name $toStop -ErrorAction SilentlyContinue
        }
        exit 1
    }
}

Start-Sleep -Seconds 15  # estabilização adicional

Write-Host "`n=== Services status after 15s stabilization ==="
Get-Service -Name $services | Format-Table Name, Status, StartType -AutoSize
```

**Resultado esperado:**
- FluxQuantumAPEX: Running
- FluxQuantumAPEX_Dashboard: Running
- FluxQuantumAPEX_Dashboard_Hantec: Running
- FluxQuantumAPEX_Live: Stopped (INTENCIONAL — não foi iniciado)

Se algum dos 3 que deveriam estar Running ficar Stopped, PARA e reporta.

---

## PASSO 3 — Primeiro minuto: verificar que FluxQuantumAPEX não crasha

```powershell
Write-Host "`n=== First minute monitoring ==="
Start-Sleep -Seconds 60

# Status após 60s
Write-Host "Services after 60s:"
Get-Service -Name $services | Format-Table Name, Status, StartType -AutoSize

# Ler últimas 40 linhas do log
Write-Host "`n--- service_stdout.log (last 40 lines) ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
    Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 40
}

# Procurar erros nos últimos minutos
Write-Host "`n--- Critical error patterns (service_stderr.log) ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stderr.log") {
    $recent = Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 200 | Select-String -Pattern "Traceback|NameError|AttributeError|ImportError|CRITICAL"
    if ($recent) {
        Write-Host "`nservice_stderr.log has recent errors:"
        $recent | Select-Object -First 10 | ForEach-Object { Write-Host "  $_" }
        $errCount = ($recent | Measure-Object).Count
        Write-Host "`nTotal critical errors: $errCount"
        if ($errCount -ge 3) {
            Write-Host "`n*** ABORT: 3+ critical errors in first minute ***"
            Stop-Service -Name "FluxQuantumAPEX" -ErrorAction SilentlyContinue
            Stop-Service -Name "FluxQuantumAPEX_Dashboard" -ErrorAction SilentlyContinue
            Stop-Service -Name "FluxQuantumAPEX_Dashboard_Hantec" -ErrorAction SilentlyContinue
            exit 1
        }
    } else {
        Write-Host "  No critical errors detected"
    }
}
```

**Critérios de abort:**
- FluxQuantumAPEX ficou Stopped após 60s
- `NameError: name 'price' is not defined` aparecer → significa que fix #1 não foi aplicado (quase impossível)
- ≥3 Tracebacks únicos

---

## PASSO 4 — Enviar mensagem de teste Telegram

Telegram credentials estão hardcoded em `live/telegram_notifier.py`, não em `.env` (descoberta da Fase 4 anterior).

```powershell
Write-Host "`n=== Telegram test message ==="

# Extrair credentials de telegram_notifier.py
$telegramFile = Get-Content "C:\FluxQuantumAI\live\telegram_notifier.py" -Raw
$tokenMatch = [regex]::Match($telegramFile, 'BOT_TOKEN\s*=\s*["'']([^"'']+)["'']')
$chatMatch = [regex]::Match($telegramFile, 'CHAT_ID\s*=\s*["'']?([0-9-]+)["'']?')

if ($tokenMatch.Success -and $chatMatch.Success) {
    $token = $tokenMatch.Groups[1].Value.Trim()
    $chatId = $chatMatch.Groups[1].Value.Trim()

    $testMsg = @"
DEPLOY COMPLETE (Fase 4-bis) — APEX RoboForex restarted at $goliveTimestamp

Services started:
 - FluxQuantumAPEX (RoboForex) lot 0.05
 - FluxQuantumAPEX_Dashboard
 - FluxQuantumAPEX_Dashboard_Hantec

FluxQuantumAPEX_Live (Hantec) DEFERRED (NSSM issue — investigação separada)

Commit: ee2068d + P0 patch (fix_p0_issues_1_and_2)
Monitorização 15min em curso.
"@

    $url = "https://api.telegram.org/bot$token/sendMessage"
    $body = @{
        chat_id = $chatId
        text = $testMsg
    }

    try {
        $response = Invoke-RestMethod -Uri $url -Method Post -Body $body -ErrorAction Stop
        if ($response.ok) {
            Write-Host "Telegram OK (message_id=$($response.result.message_id))"
        } else {
            Write-Host "Telegram response not OK: $($response | ConvertTo-Json)"
        }
    } catch {
        Write-Host "Telegram test FAILED: $($_.Exception.Message)"
    }
} else {
    Write-Host "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not found in telegram_notifier.py"
    Write-Host "Skipping Telegram test."
}
```

---

## PASSO 5 — Monitorização activa 15 minutos

```powershell
Write-Host "`n=== 15-min active monitoring started at $(Get-Date -Format 'HH:mm:ss') ==="

$monitorStart = Get-Date
$monitorStartIso = $monitorStart.ToString("yyyy-MM-ddTHH:mm")

# Snapshots a +3, +6, +9, +12, +15 minutos
$snapshotIntervals = @(3, 6, 9, 12, 15)

$abortTriggered = $false

foreach ($minutes in $snapshotIntervals) {
    if ($abortTriggered) { break }

    $targetTime = $monitorStart.AddMinutes($minutes)
    while ((Get-Date) -lt $targetTime) {
        Start-Sleep -Seconds 10
    }

    $now = Get-Date -Format "HH:mm:ss"
    Write-Host "`n--- Snapshot @ +$minutes min ($now) ---"

    # 5.1 — Services status
    $svcStatus = Get-Service -Name $services | ForEach-Object { "$($_.Name)=$($_.Status)" }
    Write-Host "Services: $($svcStatus -join ' | ')"

    # 5.2 — Recent errors (últimos 200 lines stderr)
    $errs = @()
    if (Test-Path "C:\FluxQuantumAI\logs\service_stderr.log") {
        $errs = Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 200 | Select-String -Pattern "Traceback|NameError|AttributeError|ImportError"
    }
    Write-Host "Recent tracebacks in service_stderr.log: $($errs.Count)"

    # 5.3 — Últimas 3 linhas stdout (heartbeat)
    if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
        Write-Host "Recent stdout (last 3 lines):"
        Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 3 | ForEach-Object { Write-Host "  $_" }
    }

    # 5.4 — decision_log count
    if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
        $totalLines = (Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" | Measure-Object).Count
        Write-Host "decision_log.jsonl total lines: $totalLines"
    }

    # 5.5 — Capture processes by command line
    foreach ($cp in $capturePatterns) {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match $cp.pattern }
        if (-not $procs) {
            Write-Host "WARN: $($cp.name) NOT FOUND"
        }
    }

    # Abort check
    if ($errs.Count -ge 3) {
        Write-Host "`n*** ABORT: $($errs.Count) tracebacks detected at snapshot +$minutes min ***"
        Stop-Service -Name "FluxQuantumAPEX" -ErrorAction SilentlyContinue
        Stop-Service -Name "FluxQuantumAPEX_Dashboard" -ErrorAction SilentlyContinue
        Stop-Service -Name "FluxQuantumAPEX_Dashboard_Hantec" -ErrorAction SilentlyContinue
        $abortTriggered = $true
        break
    }
}

if (-not $abortTriggered) {
    Write-Host "`n=== 15-min monitoring completed at $(Get-Date -Format 'HH:mm:ss') — no abort triggered ==="
}
```

---

## PASSO 6 — Análise do decision_log.jsonl dos 15 min

```powershell
if (-not $abortTriggered) {
    Write-Host "`n=== Decision log analysis (monitoring window) ==="

    $startIso = $monitorStart.ToUniversalTime()
    $startHourPrefix = $startIso.ToString("yyyy-MM-ddTHH")

    if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
        # Filtrar entradas do período (heurística simples pelo prefix de hora)
        $allLines = Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl"
        $recent = $allLines | Where-Object {
            $_ -match "`"timestamp`":\s*`"$startHourPrefix" -or
            $_ -match "`"timestamp`":\s*`"$($monitorStart.AddHours(1).ToUniversalTime().ToString('yyyy-MM-ddTHH'))"
        }

        Write-Host "Total entries in window: $($recent.Count)"

        # Count by action
        $go = ($recent | Where-Object { $_ -match '"action":\s*"GO"' }).Count
        $block = ($recent | Where-Object { $_ -match '"action":\s*"BLOCK"' }).Count
        $execFailed = ($recent | Where-Object { $_ -match '"action":\s*"EXEC_FAILED"' }).Count
        $pmEvent = ($recent | Where-Object { $_ -match '"action":\s*"PM_EVENT"' }).Count

        Write-Host "  GO: $go"
        Write-Host "  BLOCK: $block"
        Write-Host "  EXEC_FAILED: $execFailed"
        Write-Host "  PM_EVENT: $pmEvent"

        # Canonical observability indicators
        $confirmedBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*true' }).Count
        $provisionalBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*false' }).Count
        $staleBlocks = ($recent | Where-Object { $_ -match 'STRUCTURE_STALE_BLOCK' }).Count
        $macroRefresh = ($recent | Where-Object { $_ -match 'MACRO_REFRESH' }).Count

        Write-Host "`nCanonical observability indicators:"
        Write-Host "  Confirmed bias (m30_bias_confirmed=true): $confirmedBias"
        Write-Host "  Provisional bias only (m30_bias_confirmed=false): $provisionalBias"
        Write-Host "  STRUCTURE_STALE_BLOCK: $staleBlocks"
        Write-Host "  MACRO_REFRESH: $macroRefresh"

        # Execution states se houve GO
        if ($go -gt 0) {
            Write-Host "`nExecution overall_state for GO decisions:"
            $execStates = @("EXECUTED", "PARTIAL", "FAILED", "BROKER_DISCONNECTED", "NOT_ATTEMPTED")
            foreach ($state in $execStates) {
                $count = ($recent | Where-Object { $_ -match "`"overall_state`":\s*`"$state`"" }).Count
                if ($count -gt 0) {
                    Write-Host "  $state : $count"
                }
            }
        }

        # Block reasons (top 5)
        if ($block -gt 0) {
            Write-Host "`nTop BLOCK reasons:"
            $blockReasons = $recent | Where-Object { $_ -match '"action":\s*"BLOCK"' } | ForEach-Object {
                if ($_ -match '"reason":\s*"([^"]+)"') { $matches[1] }
            } | Group-Object | Sort-Object Count -Descending | Select-Object -First 5
            foreach ($r in $blockReasons) {
                Write-Host "  $($r.Name) : $($r.Count)"
            }
        }
    }
}
```

---

## PASSO 7 — Verificação final do ecossistema

```powershell
Write-Host "`n=== Final ecosystem check ==="

# 4 serviços (3 esperados Running, 1 esperado Stopped)
Write-Host "`n--- Services final ---"
Get-Service -Name $services | Format-Table Name, Status, StartType -AutoSize

# Capture processes por nome (robusto)
Write-Host "`n--- Capture processes final ---"
foreach ($cp in $capturePatterns) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match $cp.pattern }
    if ($procs) {
        foreach ($m in $procs) {
            Write-Host "  $($cp.name) : PID $($m.ProcessId) Running"
        }
    } else {
        Write-Host "  $($cp.name) : NOT FOUND"
    }
}

# Latest decision snapshot
Write-Host "`n--- Latest decision_live.json ---"
if (Test-Path "C:\FluxQuantumAI\logs\decision_live.json") {
    Get-Content "C:\FluxQuantumAI\logs\decision_live.json" -Raw | Write-Host
}

Write-Host "`n--- service_state.json ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_state.json") {
    Get-Content "C:\FluxQuantumAI\logs\service_state.json" -Raw | Write-Host
}
```

---

## PASSO 8 — Gerar DEPLOY_COMPLETE_BIS report

Cria `C:\FluxQuantumAI\DEPLOY_COMPLETE_BIS_<timestamp>.md` com:

```markdown
# DEPLOY COMPLETE BIS REPORT — <timestamp>

## Scope
- 3 serviços iniciados: FluxQuantumAPEX + 2 dashboards
- FluxQuantumAPEX_Live (Hantec) INTENCIONALMENTE NÃO iniciado (NSSM issue pendente)

## Go-live
- Timestamp: <timestamp>
- Services started OK: 3 / 3
- Telegram test: <OK | FAIL with reason>

## 15-min monitoring results

### Services final status
<table com 4 entries — 3 Running, 1 Stopped>

### Capture processes (by command line)
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
- MACRO_REFRESH: <n>

### Execution states (se GO > 0)
<breakdown>

### Top BLOCK reasons (top 5)
<list>

### Errors/Tracebacks
- Count: <n>
- If > 0, list:
  <excerpts>

## Latest decision snapshot
<decision_live.json>

## Service state
<service_state.json>

## Observations
- FluxQuantumAPEX_Live NSSM issue deferred to separate investigation session
- <any other anomalies>

## Status
- <GREEN | YELLOW | RED>
  - GREEN: 0 tracebacks + services stable + decisions emitting
  - YELLOW: minor issues (e.g. persistent STRUCTURE_STALE_BLOCK mas esperado fora mercado)
  - RED: aborted

## Next steps
- 24h monitoring com KPIs iguais
- Investigação NSSM do Hantec (sessão dedicada)
- Discovery ML models (sessão dedicada)
- Barbara review deste report
```

---

## OUTPUT OBRIGATÓRIO

Reporta à Barbara:

1. **Path do DEPLOY_COMPLETE_BIS report**
2. **Status geral:** GREEN / YELLOW / RED
3. **Resumo de 5 linhas:**
   - 3 serviços iniciados (OK / algum falhou?)
   - Telegram test (OK / FAIL)
   - Decisões em 15 min (count GO/BLOCK/EXEC_FAILED)
   - Tracebacks (0 ideal)
   - Next step recomendado

**Se RED → PARA. Barbara decide rollback, retry, ou investigação.**

---

**Esta é retorção cuidadosa após abort. Atenção dupla no Passo 3 (primeiro minuto) — é onde qualquer regressão apareceria. Boa sorte.**

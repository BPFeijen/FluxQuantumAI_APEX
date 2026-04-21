# TASK: APEX Deploy — Fase 4-ter (Arranque Selectivo RoboForex)

**Para:** ClaudeCode
**Projeto:** APEX live
**Tempo estimado:** 20-25 min (5 prep + 15 monitor + report)
**Modo:** Arranque **muito selectivo** — só 2 serviços RoboForex. Tudo Hantec fica Stopped.

---

## CONTEXTO

- Fase 4-bis validou que **FluxQuantumAPEX + FluxQuantumAPEX_Dashboard** arrancam limpos (7s + 12s, zero tracebacks)
- Fase 4-bis confirmou que **ambos os serviços `_Hantec`** falham com NSSM error 1051 (problema infraestrutural, não código)
- Barbara decidiu: Hantec pausado até clarificação com a corretora sobre política anti-scalping
- Decisão: arranque de **só 2 serviços** (os que funcionam)

Esta task valida código em runtime real por 15 min. Conta demo RoboForex, zero risco financeiro.

---

## CRITICAL RULES

1. **Arrancar APENAS** `FluxQuantumAPEX` + `FluxQuantumAPEX_Dashboard`.
2. **NÃO** arrancar `FluxQuantumAPEX_Live` nem `FluxQuantumAPEX_Dashboard_Hantec`.
3. Capture processes: nunca tocar.
4. Se qualquer dos 2 serviços crashar, parar e reportar.
5. Se ≥3 tracebacks em 5 min, parar e reportar.
6. Output: `DEPLOY_TER_COMPLETE_<timestamp>.md`.

---

## PASSO 1 — Pré-flight

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$goliveTs = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Write-Host "=== Fase 4-ter @ $goliveTs ==="

# Hash check
$hash = (Get-FileHash "C:\FluxQuantumAI\live\event_processor.py" -Algorithm MD5).Hash
if ($hash -ne "C48157668BAF47668E61DB460A27BDEE") {
    Write-Host "ABORT: hash mismatch — $hash"
    exit 1
}
Write-Host "Code hash MATCH"

# Services current state
Get-Service -Name FluxQuantumAPEX, FluxQuantumAPEX_Live, FluxQuantumAPEX_Dashboard, FluxQuantumAPEX_Dashboard_Hantec |
    Format-Table Name, Status -AutoSize

# Capture processes by command line
$capturePatterns = @(
    @{ name = "quantower_level2_api"; pattern = "quantower_level2_api" }
    @{ name = "iceberg_receiver";     pattern = "iceberg_receiver.py" }
    @{ name = "watchdog_l2_capture";  pattern = "watchdog_l2_capture.py" }
)
foreach ($cp in $capturePatterns) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match $cp.pattern }
    if ($procs) {
        $procs | ForEach-Object { Write-Host "  $($cp.name): PID $($_.ProcessId) Running" }
    } else {
        Write-Host "  $($cp.name): NOT FOUND — ALARM"
    }
}
```

---

## PASSO 2 — Arrancar APENAS 2 serviços

```powershell
Write-Host "`n=== Starting 2 services ==="

# RoboForex trading
Start-Service -Name FluxQuantumAPEX -ErrorAction Stop
Start-Sleep -Seconds 5
$s1 = (Get-Service FluxQuantumAPEX).Status
Write-Host "FluxQuantumAPEX: $s1"
if ($s1 -ne "Running") {
    Write-Host "ABORT: FluxQuantumAPEX not running"
    Stop-Service FluxQuantumAPEX -ErrorAction SilentlyContinue
    exit 1
}

# RoboForex dashboard
Start-Service -Name FluxQuantumAPEX_Dashboard -ErrorAction Stop
Start-Sleep -Seconds 5
$s2 = (Get-Service FluxQuantumAPEX_Dashboard).Status
Write-Host "FluxQuantumAPEX_Dashboard: $s2"
if ($s2 -ne "Running") {
    Write-Host "ABORT: Dashboard not running — stopping trading service too"
    Stop-Service FluxQuantumAPEX -ErrorAction SilentlyContinue
    Stop-Service FluxQuantumAPEX_Dashboard -ErrorAction SilentlyContinue
    exit 1
}

Start-Sleep -Seconds 15  # estabilização

Write-Host "`nFinal state after 15s:"
Get-Service -Name FluxQuantumAPEX, FluxQuantumAPEX_Live, FluxQuantumAPEX_Dashboard, FluxQuantumAPEX_Dashboard_Hantec |
    Format-Table Name, Status -AutoSize
```

**Resultado esperado:**
- `FluxQuantumAPEX`: Running
- `FluxQuantumAPEX_Dashboard`: Running
- `FluxQuantumAPEX_Live`: Stopped (INTENCIONAL)
- `FluxQuantumAPEX_Dashboard_Hantec`: Stopped (INTENCIONAL)

---

## PASSO 3 — Primeiro minuto crítico

```powershell
Write-Host "`n=== First minute ==="
Start-Sleep -Seconds 60

Write-Host "Services after 60s:"
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard | Format-Table Name, Status

Write-Host "`n--- service_stdout.log last 40 lines ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
    Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 40
}

Write-Host "`n--- Critical errors in stderr ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stderr.log") {
    $errs = Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 200 |
        Select-String -Pattern "Traceback|NameError|AttributeError|ImportError|CRITICAL"
    $errCount = ($errs | Measure-Object).Count
    Write-Host "Tracebacks found: $errCount"
    if ($errs) { $errs | Select-Object -First 10 | ForEach-Object { Write-Host "  $_" } }
    if ($errCount -ge 3) {
        Write-Host "`n*** ABORT: 3+ tracebacks ***"
        Stop-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard -ErrorAction SilentlyContinue
        exit 1
    }
}
```

---

## PASSO 4 — Telegram test

```powershell
Write-Host "`n=== Telegram test ==="

$telegramFile = Get-Content "C:\FluxQuantumAI\live\telegram_notifier.py" -Raw
$tokenMatch = [regex]::Match($telegramFile, 'BOT_TOKEN\s*=\s*["'']([^"'']+)["'']')
$chatMatch = [regex]::Match($telegramFile, 'CHAT_ID\s*=\s*["'']?([0-9-]+)["'']?')

if ($tokenMatch.Success -and $chatMatch.Success) {
    $token = $tokenMatch.Groups[1].Value.Trim()
    $chatId = $chatMatch.Groups[1].Value.Trim()

    $msg = @"
DEPLOY COMPLETE (Fase 4-ter) — APEX RoboForex restarted at $goliveTs

Services Running:
  - FluxQuantumAPEX (RoboForex demo, lot 0.05)
  - FluxQuantumAPEX_Dashboard

Services Stopped (intentional):
  - FluxQuantumAPEX_Live (Hantec) — aguarda clarificação da corretora
  - FluxQuantumAPEX_Dashboard_Hantec — NSSM issue pendente

Commit: ee2068d + P0 patch
Monitorização 15min em curso.
"@

    try {
        $resp = Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -Method Post `
            -Body @{ chat_id = $chatId; text = $msg }
        if ($resp.ok) { Write-Host "Telegram OK (message_id=$($resp.result.message_id))" }
    } catch { Write-Host "Telegram FAILED: $($_.Exception.Message)" }
}
```

---

## PASSO 5 — Monitorização 15 min

```powershell
Write-Host "`n=== 15-min monitoring started at $(Get-Date -Format 'HH:mm:ss') ==="
$monitorStart = Get-Date
$snapshots = @(3, 6, 9, 12, 15)
$aborted = $false

foreach ($m in $snapshots) {
    if ($aborted) { break }
    while ((Get-Date) -lt $monitorStart.AddMinutes($m)) { Start-Sleep -Seconds 10 }

    $now = Get-Date -Format "HH:mm:ss"
    Write-Host "`n--- Snapshot +$m min ($now) ---"

    # Services
    $svcs = Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard |
        ForEach-Object { "$($_.Name)=$($_.Status)" }
    Write-Host "Services: $($svcs -join ' | ')"

    # Tracebacks
    $errs = @()
    if (Test-Path "C:\FluxQuantumAI\logs\service_stderr.log") {
        $errs = Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 300 |
            Select-String -Pattern "Traceback|NameError|AttributeError|ImportError"
    }
    Write-Host "Tracebacks (last 300 stderr lines): $($errs.Count)"

    # decision_log count
    if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
        $lines = (Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" | Measure-Object).Count
        Write-Host "decision_log total lines: $lines"
    }

    # Capture by name
    foreach ($cp in $capturePatterns) {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -match $cp.pattern }
        if (-not $procs) { Write-Host "WARN: $($cp.name) NOT FOUND" }
    }

    # Last 3 lines of stdout
    if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
        Write-Host "Last stdout:"
        Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 3 |
            ForEach-Object { Write-Host "  $_" }
    }

    if ($errs.Count -ge 3) {
        Write-Host "`n*** ABORT at +$m min: $($errs.Count) tracebacks ***"
        Stop-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard -ErrorAction SilentlyContinue
        $aborted = $true
    }
}

if (-not $aborted) {
    Write-Host "`n=== 15-min monitoring completed cleanly ==="
}
```

---

## PASSO 6 — Análise decision_log 15min

```powershell
if (-not $aborted) {
    Write-Host "`n=== Decision log analysis ==="
    $startHour = $monitorStart.ToUniversalTime().ToString("yyyy-MM-ddTHH")

    if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
        $recent = Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" |
            Where-Object { $_ -match "`"timestamp`":\s*`"$startHour" }

        Write-Host "Entries in window: $($recent.Count)"

        $go = ($recent | Where-Object { $_ -match '"action":\s*"GO"' }).Count
        $block = ($recent | Where-Object { $_ -match '"action":\s*"BLOCK"' }).Count
        $execFailed = ($recent | Where-Object { $_ -match '"action":\s*"EXEC_FAILED"' }).Count
        $pmEvent = ($recent | Where-Object { $_ -match '"action":\s*"PM_EVENT"' }).Count

        Write-Host "  GO: $go"
        Write-Host "  BLOCK: $block"
        Write-Host "  EXEC_FAILED: $execFailed"
        Write-Host "  PM_EVENT: $pmEvent"

        # Canonical obs
        $confBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*true' }).Count
        $provBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*false' }).Count
        $stale = ($recent | Where-Object { $_ -match 'STRUCTURE_STALE_BLOCK' }).Count

        Write-Host "`nCanonical observability:"
        Write-Host "  m30_bias_confirmed=true: $confBias"
        Write-Host "  m30_bias_confirmed=false: $provBias"
        Write-Host "  STRUCTURE_STALE_BLOCK: $stale"

        # Top block reasons
        if ($block -gt 0) {
            Write-Host "`nTop 5 BLOCK reasons:"
            $recent | Where-Object { $_ -match '"action":\s*"BLOCK"' } | ForEach-Object {
                if ($_ -match '"reason":\s*"([^"]+)"') { $matches[1] }
            } | Group-Object | Sort-Object Count -Descending | Select-Object -First 5 |
                ForEach-Object { Write-Host "  $($_.Name): $($_.Count)" }
        }
    }
}
```

---

## PASSO 7 — Gerar DEPLOY_TER_COMPLETE report

```markdown
# DEPLOY TER COMPLETE — <timestamp>

## Scope
- 2 serviços iniciados: FluxQuantumAPEX + FluxQuantumAPEX_Dashboard (RoboForex demo)
- Hantec (ambos serviços) INTENCIONALMENTE NÃO iniciados

## Go-live
- Timestamp: <...>
- Services OK: 2/2
- Telegram: OK/FAIL

## 15-min monitoring

### Services final
<table>

### Capture 3/3
<status>

### Decisions
- GO: <n>
- BLOCK: <n>
- EXEC_FAILED: <n>
- PM_EVENT: <n>

### Canonical observability
- confirmed bias: <n>
- provisional bias: <n>
- STRUCTURE_STALE_BLOCK: <n>

### Top BLOCK reasons
<top 5>

### Tracebacks
- Count: <n>

## Status
GREEN / YELLOW / RED

## Next steps
- 24h monitoring
- Hantec clarification com corretora (ação Barbara)
- NSSM investigation (sessão separada)
```

---

## OUTPUT

Reporta:
1. Path do report
2. Status GREEN/YELLOW/RED
3. Resumo 5 linhas: 2/2 serviços, Telegram, decisões, tracebacks, next step

Se RED → PARA, aguarda Barbara.

# TASK: APEX Deploy — Fase 4-quater (Retry com regex corrigido)

**Para:** ClaudeCode
**Projeto:** APEX live
**Tempo estimado:** 20 min (5 prep + 15 monitor)
**Modo:** Arranque selectivo RoboForex (idêntico à Fase 4-ter), mas com regex de deteção de tracebacks corrigido.

---

## CONTEXTO

Fase 4-ter terminou YELLOW (false-positive abort) porque o regex do script monitor apanhou `m1_stale_critical=true` via match case-insensitive de `CRITICAL`. O código patched provou-se saudável em 80s de runtime real — zero regressões.

Esta task é **retry idêntico** com único fix: regex case-sensitive + word boundary para não apanhar observability strings.

---

## CRITICAL RULES

Iguais à Fase 4-ter:
1. Arrancar APENAS `FluxQuantumAPEX` + `FluxQuantumAPEX_Dashboard`.
2. NÃO arrancar serviços Hantec.
3. Capture processes: nunca tocar.
4. Se qualquer serviço crashar, parar e reportar.
5. Se ≥3 tracebacks **reais** (regex corrigido) em 5 min, parar.
6. Output: `DEPLOY_QUATER_COMPLETE_<timestamp>.md`.

---

## FIX ÚNICO — REGEX CORRIGIDO

**Antes (Fase 4-ter):**
```powershell
Select-String -Pattern "Traceback|NameError|AttributeError|ImportError|CRITICAL"
```

**Agora (Fase 4-quater):**
```powershell
Select-String -Pattern "^Traceback|\bNameError\b|\bAttributeError\b|\bImportError\b" -CaseSensitive
```

Mudanças:
- `^Traceback` — match apenas no início da linha (Traceback canónico do Python)
- `\b...\b` — word boundaries para não apanhar substrings
- `-CaseSensitive` — evita match em `critical` lowercase
- **Removido `CRITICAL`** — é excesso (já temos 4 categorias robustas)

---

## PASSO 1 — Pré-flight

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$goliveTs = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Write-Host "=== Fase 4-quater @ $goliveTs ==="

# Hash check
$hash = (Get-FileHash "C:\FluxQuantumAI\live\event_processor.py" -Algorithm MD5).Hash
if ($hash -ne "C48157668BAF47668E61DB460A27BDEE") {
    Write-Host "ABORT: hash mismatch"
    exit 1
}
Write-Host "Code hash MATCH"

# Services current state
Get-Service -Name FluxQuantumAPEX, FluxQuantumAPEX_Live, FluxQuantumAPEX_Dashboard, FluxQuantumAPEX_Dashboard_Hantec |
    Format-Table Name, Status -AutoSize

# Capture processes (using the working query from Fase 4-ter)
$capturePatterns = @(
    @{ name = "quantower_level2_api"; pattern = "quantower_level2_api" }
    @{ name = "iceberg_receiver";     pattern = "iceberg_receiver.py" }
    @{ name = "watchdog_l2_capture";  pattern = "watchdog_l2_capture.py" }
)
foreach ($cp in $capturePatterns) {
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match $cp.pattern }
    if ($procs) {
        $procs | ForEach-Object { Write-Host "  $($cp.name): PID $($_.ProcessId) Running" }
    } else {
        Write-Host "  $($cp.name): NOT FOUND"
    }
}
```

---

## PASSO 2 — Arrancar 2 serviços

```powershell
Write-Host "`n=== Starting 2 services ==="

Start-Service -Name FluxQuantumAPEX -ErrorAction Stop
Start-Sleep -Seconds 5
if ((Get-Service FluxQuantumAPEX).Status -ne "Running") {
    Write-Host "ABORT"; Stop-Service FluxQuantumAPEX -ErrorAction SilentlyContinue; exit 1
}
Write-Host "FluxQuantumAPEX: Running"

Start-Service -Name FluxQuantumAPEX_Dashboard -ErrorAction Stop
Start-Sleep -Seconds 5
if ((Get-Service FluxQuantumAPEX_Dashboard).Status -ne "Running") {
    Write-Host "ABORT"
    Stop-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "FluxQuantumAPEX_Dashboard: Running"

Start-Sleep -Seconds 15  # estabilização

Write-Host "`nFinal state:"
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard | Format-Table Name, Status
```

---

## PASSO 3 — Primeiro minuto (REGEX CORRIGIDO)

```powershell
Write-Host "`n=== First minute ==="
Start-Sleep -Seconds 60

Write-Host "Services after 60s:"
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard | Format-Table Name, Status

Write-Host "`n--- service_stdout.log last 40 lines ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
    Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 40
}

# REGEX CORRIGIDO AQUI
Write-Host "`n--- Critical errors in stderr (REGEX FIXED) ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stderr.log") {
    $errs = Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 200 |
        Select-String -Pattern "^Traceback|\bNameError\b|\bAttributeError\b|\bImportError\b" -CaseSensitive
    $errCount = ($errs | Measure-Object).Count
    Write-Host "Real tracebacks found: $errCount"
    if ($errs) { $errs | Select-Object -First 10 | ForEach-Object { Write-Host "  $_" } }
    if ($errCount -ge 3) {
        Write-Host "`n*** ABORT: 3+ real tracebacks ***"
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
DEPLOY COMPLETE (Fase 4-quater) — APEX RoboForex @ $goliveTs

Retry after false-positive abort (regex corrigido).

Services Running:
  - FluxQuantumAPEX (RoboForex demo, lot 0.05)
  - FluxQuantumAPEX_Dashboard

Services Stopped (intentional):
  - FluxQuantumAPEX_Live (Hantec) — aguarda clarificação
  - FluxQuantumAPEX_Dashboard_Hantec — NSSM pendente

Commit: ee2068d + P0 patch
Monitoring 15min em curso.
"@

    try {
        $resp = Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -Method Post `
            -Body @{ chat_id = $chatId; text = $msg }
        if ($resp.ok) { Write-Host "Telegram OK (id=$($resp.result.message_id))" }
    } catch { Write-Host "Telegram FAILED: $($_.Exception.Message)" }
}
```

---

## PASSO 5 — Monitorização 15 min (REGEX CORRIGIDO)

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

    # REGEX CORRIGIDO AQUI
    $errs = @()
    if (Test-Path "C:\FluxQuantumAI\logs\service_stderr.log") {
        $errs = Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 300 |
            Select-String -Pattern "^Traceback|\bNameError\b|\bAttributeError\b|\bImportError\b" -CaseSensitive
    }
    Write-Host "Real tracebacks (last 300 stderr lines): $($errs.Count)"

    # decision_log count
    if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
        $lines = (Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" | Measure-Object).Count
        Write-Host "decision_log total lines: $lines"
    }

    # Observability counters (these are FEATURES, not errors)
    if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
        $staleCount = (Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 500 |
            Select-String -Pattern "STRUCTURE_STALE_BLOCK" | Measure-Object).Count
        $gateCount = (Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 500 |
            Select-String -Pattern "GATE CHECK" | Measure-Object).Count
        $boxCount = (Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 500 |
            Select-String -Pattern "NEW M30 BOX" | Measure-Object).Count
        Write-Host "Observability (last 500 stdout lines): STRUCTURE_STALE_BLOCK=$staleCount | GATE CHECK=$gateCount | NEW M30 BOX=$boxCount"
    }

    # Capture by name
    foreach ($cp in $capturePatterns) {
        $procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match $cp.pattern }
        if (-not $procs) { Write-Host "WARN: $($cp.name) NOT FOUND" }
    }

    # Last 3 lines of stdout
    if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
        Write-Host "Last stdout:"
        Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 3 |
            ForEach-Object { Write-Host "  $_" }
    }

    if ($errs.Count -ge 3) {
        Write-Host "`n*** ABORT at +$m min: $($errs.Count) real tracebacks ***"
        Stop-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard -ErrorAction SilentlyContinue
        $aborted = $true
    }
}

if (-not $aborted) {
    Write-Host "`n=== 15-min monitoring completed cleanly ==="
}
```

---

## PASSO 6 — Análise decision_log

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

        $confBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*true' }).Count
        $provBias = ($recent | Where-Object { $_ -match '"m30_bias_confirmed":\s*false' }).Count
        $stale = ($recent | Where-Object { $_ -match 'STRUCTURE_STALE_BLOCK' }).Count

        Write-Host "`nCanonical observability:"
        Write-Host "  m30_bias_confirmed=true: $confBias"
        Write-Host "  m30_bias_confirmed=false: $provBias"
        Write-Host "  STRUCTURE_STALE_BLOCK (expected if feed lagging): $stale"

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

## PASSO 7 — DEPLOY_QUATER_COMPLETE report

Estrutura igual à Fase 4-ter, com:
- Nota que esta é retry com regex corrigido
- Se completou os 15min limpamente → status GREEN
- Se abort legítimo → status RED (problema real)
- Se completou mas com observations → YELLOW

Campos obrigatórios:
- Timestamp go-live
- Services 2/2 status
- Telegram OK/FAIL
- Total decisions em 15min + breakdown
- Canonical observability counts
- Top BLOCK reasons
- Real tracebacks: 0 ideal
- STRUCTURE_STALE_BLOCK count (feature, não erro)
- GATE CHECK activity count
- NEW M30 BOX activity count

---

## OUTPUT

Reporta:
1. Path do report
2. Status GREEN/YELLOW/RED
3. Resumo 5 linhas

**Se GREEN:** Barbara decide se deixa sistema a rodar (24h monitoring passivo) ou para.
**Se YELLOW:** esclarece razão.
**Se RED:** para e aguarda Barbara.

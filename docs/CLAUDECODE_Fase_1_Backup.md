# TASK: APEX Deploy — Fase 1 (Backup Completo)

**Para:** ClaudeCode
**Projeto:** APEX live em `C:\FluxQuantumAI\`
**Tempo estimado:** 10-15 minutos
**Modo:** Maioritariamente read-only (só escreve dentro do directório de backup)

---

## CONTEXTO

- Os serviços `FluxQuantumAPEX` e `FluxQuantumAPEX_Live` estão **Stopped** desde 2026-04-16 19:46. Confirmado no DEPLOY_DISCOVERY_REPORT.md.
- Os serviços `FluxQuantumAPEX_Dashboard` e `FluxQuantumAPEX_Dashboard_Hantec` estão **Running**. NÃO parar.
- Os processos de captura (`quantower_level2_api` PID 8076, `iceberg_receiver` PID 8248, `watchdog_l2_capture` PID 2512) estão **Running**. NÃO parar.
- Esta task cria backup completo do estado actual para rollback. Não altera nenhum ficheiro fora do directório de backup.

---

## CRITICAL RULES

1. **Não parar nenhum serviço nem processo.**
2. **Não alterar nenhum ficheiro fora de `C:\FluxQuantumAI\Backups\`.**
3. **Não tocar na pasta `C:\FluxQuantumAPEX\`** (pasta separada confirmada oficial pela Barbara).
4. **Se algum comando falhar, PARA e reporta** — não improvises alternatives.

---

## PASSO 1 — Criar directório de backup

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = "C:\FluxQuantumAI\Backups\pre-deploy-$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
New-Item -ItemType Directory -Path "$backup\live" -Force | Out-Null
New-Item -ItemType Directory -Path "$backup\config" -Force | Out-Null
New-Item -ItemType Directory -Path "$backup\logs" -Force | Out-Null
New-Item -ItemType Directory -Path "$backup\nssm_configs" -Force | Out-Null
New-Item -ItemType Directory -Path "$backup\data_snapshots" -Force | Out-Null
Write-Host "Backup folder: $backup"
```

Reporta o path criado.

---

## PASSO 2 — Backup do código APEX

```powershell
# Todos os .py e .bat da raiz de C:\FluxQuantumAI\
$rootFiles = @(
    "run_live.py", "run_apex_wrapper.py",
    "ats_iceberg_gate.py", "ats_live_gate.py", "cal_level_touch.py",
    "grenadier_guardrail.py", "iceberg_receiver.py",
    "mt5_executor.py", "mt5_executor_hantec.py",
    "quantower_level2_api.py", "reconstruct_icebergs_databento.py",
    "submit_job.py", "train_grenadier.py", "train_iceberg_local.py",
    "watchdog_l2_capture.py", "test_mt5_ipc.py", "test_mt5_ipc2.py",
    "requirements.txt", ".gitignore",
    "check_capture_status.bat", "install_watchdog.bat",
    "launch_demo_asia.bat", "run_apex_interactive.bat",
    "start_apex_full.bat", "start_apex_robo.bat"
)
foreach ($f in $rootFiles) {
    $src = "C:\FluxQuantumAI\$f"
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination "$backup\$f" -Force
    }
}

# .env é crítico (credentials)
if (Test-Path "C:\FluxQuantumAI\.env") {
    Copy-Item -Path "C:\FluxQuantumAI\.env" -Destination "$backup\.env" -Force
}

# Pasta live completa (todos os .py)
Copy-Item -Path "C:\FluxQuantumAI\live\*.py" -Destination "$backup\live\" -Force

# Validar
$liveCount = (Get-ChildItem "$backup\live\" -File | Measure-Object).Count
Write-Host "Live files backed up: $liveCount"
```

Reporta: número de ficheiros live copiados (esperado: 18 conforme discovery report).

---

## PASSO 3 — Backup da configuração

```powershell
# Todos os .json em config (settings.json + variantes calibradas + calibration_results)
Copy-Item -Path "C:\FluxQuantumAI\config\*.json" -Destination "$backup\config\" -Force

# Validar
Get-ChildItem "$backup\config\" | Format-Table Name, Length -AutoSize
```

Reporta o output.

---

## PASSO 4 — Backup de runtime state

```powershell
$runtimeFiles = @(
    "service_state.json",
    "decision_live.json"
)
foreach ($f in $runtimeFiles) {
    $src = "C:\FluxQuantumAI\logs\$f"
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination "$backup\logs\$f" -Force
    }
}
```

---

## PASSO 5 — Backup de logs canónicos (últimos 7 dias)

```powershell
# Logs canónicos e CSVs de decisão
$logFiles = @(
    "decision_log.jsonl",
    "continuation_trades.jsonl",
    "live_log.csv",
    "live_log_live.csv",
    "service_stdout.log",
    "service_stderr.log",
    "service_hantec_stdout.log",
    "service_hantec_stderr.log"
)
foreach ($f in $logFiles) {
    $src = "C:\FluxQuantumAI\logs\$f"
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination "$backup\logs\$f" -Force
    }
}

# NÃO copiar: quantower_level2_api_stdout.log (283MB — demasiado grande)
```

---

## PASSO 6 — Snapshot dos parquets críticos

```powershell
# Parquets M5 e M30 — usados pelo sistema, snapshot para rollback se nova lógica reprocessar
$parquets = @(
    "gc_m5_boxes.parquet",
    "gc_m30_boxes.parquet",
    "gc_ats_features_v4.parquet"
)
foreach ($p in $parquets) {
    $src = "C:\FluxQuantumAI\data\$p"
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination "$backup\data_snapshots\$p" -Force
    }
}

Get-ChildItem "$backup\data_snapshots\" | Format-Table Name, Length, LastWriteTime -AutoSize
```

Reporta.

---

## PASSO 7 — Backup das configurações NSSM

```powershell
$nssmPath = "C:\tools\nssm\nssm.exe"
$services = @("FluxQuantumAPEX", "FluxQuantumAPEX_Live", "FluxQuantumAPEX_Dashboard", "FluxQuantumAPEX_Dashboard_Hantec")
$nssmReport = @()

foreach ($svc in $services) {
    $appPath = & $nssmPath get $svc Application 2>&1 | Out-String
    $appDir  = & $nssmPath get $svc AppDirectory 2>&1 | Out-String
    $appParams = & $nssmPath get $svc AppParameters 2>&1 | Out-String
    $status = (Get-Service -Name $svc -ErrorAction SilentlyContinue).Status
    $startType = (Get-Service -Name $svc -ErrorAction SilentlyContinue).StartType

    $nssmReport += @"
Service: $svc
  Status: $status
  StartType: $startType
  Application: $appPath
  AppDirectory: $appDir
  AppParameters: $appParams
---
"@
}

$nssmReport | Out-File "$backup\nssm_configs\nssm_services_backup.txt" -Encoding UTF8
Get-Content "$backup\nssm_configs\nssm_services_backup.txt"
```

Reporta o output (vai ser útil para reconstruir os serviços se for preciso).

---

## PASSO 8 — Gerar ROLLBACK_PLAN.md

```powershell
$rollback = @"
# ROLLBACK PLAN — pre-deploy backup $stamp

**Backup location:** $backup
**Created:** $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
**Reason:** Rollback capability for APEX deploy (main from GitHub + P0 patch)

## WHEN TO USE

Rollback IF:
- After deploy, services crash immediately on start
- TRACEBACK or NameError errors in logs
- Immediate order execution failures
- Any dashboard/Telegram integration breakage that wasn't present before

## WHAT TO ROLLBACK

### Step 1 — Stop services that were just deployed
``````powershell
nssm stop FluxQuantumAPEX
nssm stop FluxQuantumAPEX_Live
Start-Sleep -Seconds 5
``````

### Step 2 — Restore live code
``````powershell
Remove-Item -Recurse -Force "C:\FluxQuantumAI\live\*.py"
Copy-Item -Path "$backup\live\*.py" -Destination "C:\FluxQuantumAI\live\" -Force
``````

### Step 3 — Restore root .py files (if modified)
``````powershell
# Copy back if needed:
# Copy-Item -Path "$backup\run_live.py" -Destination "C:\FluxQuantumAI\run_live.py" -Force
``````

### Step 4 — Restore config (if modified)
``````powershell
# settings.json should NOT have been touched, but just in case:
# Copy-Item -Path "$backup\config\settings.json" -Destination "C:\FluxQuantumAI\config\settings.json" -Force
``````

### Step 5 — Start services
``````powershell
nssm start FluxQuantumAPEX
nssm start FluxQuantumAPEX_Live
Start-Sleep -Seconds 10
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Live | Format-Table Name, Status
``````

### Step 6 — Verify
``````powershell
Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Wait -Tail 30
``````

Wait 2-3 minutes to confirm:
- No crash loop
- Regular heartbeat messages
- No NameError / TRACEBACK

## CAPTURE PROCESSES — NEVER STOPPED

The following processes were running at backup time and MUST continue running:
- PID 8076 — quantower_level2_api (L2 capture, port 8000)
- PID 8248 — iceberg_receiver (port 8002)
- PID 2512 — watchdog_l2_capture

If any of these stopped during deploy, that's a separate issue — report to Barbara.

## NSSM CONFIGS BACKUP

See: $backup\nssm_configs\nssm_services_backup.txt

## WHAT IS NOT BACKED UP (and why)

- .venv/ — reconstructible from requirements.txt
- __pycache__/ — cache only
- data/*.parquet (except snapshots) — historical data not modified by deploy
- quantower_level2_api_stdout.log (283 MB) — too large, not critical for rollback
"@

$rollback | Out-File "$backup\ROLLBACK_PLAN.md" -Encoding UTF8
Write-Host "ROLLBACK_PLAN.md criado em $backup\ROLLBACK_PLAN.md"
```

---

## PASSO 9 — Validação final

```powershell
# Contabilizar tudo
Write-Host "`n=== BACKUP SUMMARY ==="
Write-Host "Location: $backup"
Write-Host ""

# Total size
$totalBytes = (Get-ChildItem -Recurse $backup | Measure-Object -Property Length -Sum).Sum
$totalMB = [math]::Round($totalBytes / 1MB, 2)
Write-Host "Total size: $totalMB MB"

# File count per category
$liveFiles = (Get-ChildItem "$backup\live\" -File).Count
$configFiles = (Get-ChildItem "$backup\config\" -File).Count
$logFiles = (Get-ChildItem "$backup\logs\" -File).Count
$dataSnaps = (Get-ChildItem "$backup\data_snapshots\" -File).Count
Write-Host "  live/ files: $liveFiles (expected: ~18)"
Write-Host "  config/ files: $configFiles"
Write-Host "  logs/ files: $logFiles"
Write-Host "  data_snapshots/ files: $dataSnaps"
Write-Host ""

# Check ROLLBACK_PLAN.md exists
if (Test-Path "$backup\ROLLBACK_PLAN.md") {
    Write-Host "ROLLBACK_PLAN.md: OK"
} else {
    Write-Host "ROLLBACK_PLAN.md: MISSING (critical)"
}

# Tree structure
Write-Host "`n=== BACKUP TREE ==="
Get-ChildItem -Recurse $backup | Select-Object FullName, Length | Format-Table -AutoSize
```

Reporta o output completo.

---

## OUTPUT OBRIGATÓRIO

Cria um ficheiro `C:\FluxQuantumAI\BACKUP_REPORT_$stamp.md` com:

1. Path do backup criado
2. Timestamp de execução
3. Output literal de cada passo (1-9)
4. Qualquer erro ou warning encontrado
5. Tamanho total do backup
6. Confirmação de que ROLLBACK_PLAN.md existe

---

## REGRAS FINAIS

1. **Se algum ficheiro esperado não existir**, reporta mas não falhes — continua com os outros. No fim, lista o que faltou.
2. **Não apagar nada** — só criar no directório de backup.
3. **Não reiniciar serviços** — esta task é pura preservação.
4. **Capture processes não tocar** — PID 8076, 8248, 2512 devem continuar a correr.

**Quando terminares, reporta o BACKUP_REPORT_$stamp.md completo. Não avances para deploy. Aguarda instrução da Barbara.**

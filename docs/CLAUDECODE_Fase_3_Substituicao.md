# TASK: APEX Deploy — Fase 3 (Substituição dos 7 ficheiros no live)

**Para:** ClaudeCode
**Projeto:** APEX live em `C:\FluxQuantumAI\`
**Tempo estimado:** 5 minutos
**Modo:** Substitui 7 ficheiros em `C:\FluxQuantumAI\live\`. NÃO reinicia serviços.

---

## CONTEXTO

- **Backup criado:** `C:\FluxQuantumAI\Backups\pre-deploy-20260417_141337\` (Fase 1)
- **Staging validado:** `C:\FluxQuantumAI\deploy-staging-20260417_164202\` (Fase 2)
- **Patch aplicado em staging:** 1 file, 3 insertions, 3 deletions, canonical observability preservada
- **Serviços trading:** `FluxQuantumAPEX` e `FluxQuantumAPEX_Live` estão Stopped (foram parados em 2026-04-16 19:46)
- **Processos captura:** PIDs 2512, 8076, 8248 Running — NÃO TOCAR

**Esta task substitui 7 ficheiros e valida. NÃO inicia serviços. Restart fica para Fase 4.**

---

## CRITICAL RULES

1. **Não iniciar nenhum serviço.** Esta fase termina com serviços Stopped (estado actual).
2. **Não tocar em `C:\FluxQuantumAI\config\`, `logs\`, `data\`, nem nenhuma pasta fora de `live\`.**
3. **Não tocar na pasta `C:\FluxQuantumAPEX\`**.
4. **Capture processes não tocar** — PID 2512, 8076, 8248.
5. **Se algum passo falhar, PARA e reporta** — NÃO tentes corrigir sozinho. A Barbara e o Claude avaliam.
6. **Output obrigatório:** `REPLACEMENT_REPORT_<timestamp>.md`.

---

## PASSO 1 — Sanity check pré-substituição

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$staging = "C:\FluxQuantumAI\deploy-staging-20260417_164202"
$backup = "C:\FluxQuantumAI\Backups\pre-deploy-20260417_141337"
$live = "C:\FluxQuantumAI\live"

Write-Host "=== Pré-replacement sanity check ==="

# Verificar que serviços continuam Stopped
$apex = Get-Service -Name FluxQuantumAPEX -ErrorAction SilentlyContinue
$apexLive = Get-Service -Name FluxQuantumAPEX_Live -ErrorAction SilentlyContinue
Write-Host "FluxQuantumAPEX:      $($apex.Status)"
Write-Host "FluxQuantumAPEX_Live: $($apexLive.Status)"

# Verificar que capture processes estão Running
$capturePIDs = @(2512, 8076, 8248)
foreach ($pid in $capturePIDs) {
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "PID $pid Running ($($proc.ProcessName))"
    } else {
        Write-Host "PID $pid NOT FOUND — ALARM"
    }
}

# Verificar que staging e backup existem
if (-not (Test-Path $staging)) { Write-Host "STAGING MISSING — ABORT"; exit 1 }
if (-not (Test-Path $backup))  { Write-Host "BACKUP MISSING — ABORT";  exit 1 }

Write-Host "`nSanity check OK — ready to proceed."
```

**Resultado esperado:**
- Ambos os serviços Stopped
- 3 capture PIDs Running
- Staging e backup existem

Se serviços não estiverem Stopped ou capture PIDs ausentes, PARA e reporta.

---

## PASSO 2 — Hash MD5 pré-substituição

Para rastreabilidade: gravar hashes dos 7 ficheiros actuais do live ANTES da substituição.

```powershell
$filesToReplace = @(
    "base_dashboard_server.py",
    "d1_h4_updater.py",
    "event_processor.py",
    "level_detector.py",
    "position_monitor.py",
    "telegram_notifier.py",
    "tick_breakout_monitor.py"
)

Write-Host "`n=== Hash MD5 PRE-REPLACEMENT ==="
$preHashes = @{}
foreach ($f in $filesToReplace) {
    $path = "$live\$f"
    if (Test-Path $path) {
        $hash = (Get-FileHash $path -Algorithm MD5).Hash
        $preHashes[$f] = $hash
        Write-Host "  $f : $hash"
    } else {
        Write-Host "  $f : NOT FOUND"
    }
}
```

Reporta output completo. Estes hashes ficam no report para rastrear.

---

## PASSO 3 — Substituir os 7 ficheiros

```powershell
Write-Host "`n=== Replacing 7 files ==="
$replaced = @()
$failed = @()

foreach ($f in $filesToReplace) {
    $src = "$staging\live\$f"
    $dst = "$live\$f"

    if (-not (Test-Path $src)) {
        Write-Host "  FAIL $f : source missing in staging"
        $failed += $f
        continue
    }

    try {
        Copy-Item -Path $src -Destination $dst -Force -ErrorAction Stop
        $replaced += $f
        Write-Host "  OK   $f"
    } catch {
        Write-Host "  FAIL $f : $($_.Exception.Message)"
        $failed += $f
    }
}

Write-Host "`nReplaced: $($replaced.Count) / 7"
Write-Host "Failed: $($failed.Count)"
```

**Resultado esperado:** 7 OK, 0 FAIL.

Se algum FAIL, PARA e reporta. **Não tentes continuar** — a Barbara vai decidir rollback ou retry.

---

## PASSO 4 — Hash MD5 pós-substituição

```powershell
Write-Host "`n=== Hash MD5 POST-REPLACEMENT ==="
$postHashes = @{}
$stagingHashes = @{}

foreach ($f in $filesToReplace) {
    $livePath = "$live\$f"
    $stagPath = "$staging\live\$f"

    $postH = (Get-FileHash $livePath -Algorithm MD5).Hash
    $stagH = (Get-FileHash $stagPath -Algorithm MD5).Hash
    $postHashes[$f] = $postH
    $stagingHashes[$f] = $stagH

    $match = if ($postH -eq $stagH) { "MATCH" } else { "MISMATCH" }
    Write-Host "  $f : $match"
    Write-Host "    live:    $postH"
    Write-Host "    staging: $stagH"
}
```

**Resultado esperado:** Todos os 7 MATCH (live == staging).

Se algum MISMATCH, o Copy-Item falhou silenciosamente ou foi sobrescrito. PARA e reporta.

---

## PASSO 5 — Verificar que ficheiros NÃO substituídos estão intactos

```powershell
$notReplaced = @(
    "dashboard_server.py",
    "dashboard_server_hantec.py",
    "feed_health.py",
    "hedge_manager.py",
    "kill_zones.py",
    "m30_updater.py",
    "m5_updater.py",
    "operational_rules.py",
    "price_speed.py",
    "signal_queue.py",
    "__init__.py"
)

Write-Host "`n=== Unchanged files verification ==="
$changed = 0
$unchanged = 0

foreach ($f in $notReplaced) {
    $livePath = "$live\$f"
    $backupPath = "$backup\live\$f"
    if ((Test-Path $livePath) -and (Test-Path $backupPath)) {
        $liveH = (Get-FileHash $livePath -Algorithm MD5).Hash
        $backupH = (Get-FileHash $backupPath -Algorithm MD5).Hash
        if ($liveH -eq $backupH) {
            $unchanged++
        } else {
            $changed++
            Write-Host "  UNEXPECTED CHANGE: $f (live != backup)"
        }
    }
}

Write-Host "`nUnchanged as expected: $unchanged / 11"
Write-Host "Unexpectedly changed: $changed"
```

**Resultado esperado:** 11 unchanged, 0 unexpectedly changed.

Se algum UNEXPECTED CHANGE, PARA e reporta — significa que tocámos em algo que não devíamos.

---

## PASSO 6 — Verificar que config, logs, data estão intactos

```powershell
Write-Host "`n=== Config / Logs / Data preservation check ==="

# settings.json deve estar igual ao backup
$settingsLive = "C:\FluxQuantumAI\config\settings.json"
$settingsBackup = "$backup\config\settings.json"
$settingsLiveH = (Get-FileHash $settingsLive -Algorithm MD5).Hash
$settingsBackupH = (Get-FileHash $settingsBackup -Algorithm MD5).Hash
Write-Host "settings.json:"
Write-Host "  live:   $settingsLiveH"
Write-Host "  backup: $settingsBackupH"
Write-Host "  match:  $($settingsLiveH -eq $settingsBackupH)"

# LastWriteTime dos ficheiros de config (devem ser pré-deploy)
Get-ChildItem "C:\FluxQuantumAI\config\" -File | Format-Table Name, LastWriteTime -AutoSize

# Outros configs importantes
$otherConfigs = @("calibration_results.json", "calibration_results_v2.json")
foreach ($c in $otherConfigs) {
    $livePath = "C:\FluxQuantumAI\config\$c"
    $backupPath = "$backup\config\$c"
    if ((Test-Path $livePath) -and (Test-Path $backupPath)) {
        $liveH = (Get-FileHash $livePath -Algorithm MD5).Hash
        $backupH = (Get-FileHash $backupPath -Algorithm MD5).Hash
        $m = if ($liveH -eq $backupH) { "OK" } else { "CHANGED" }
        Write-Host "  $c : $m"
    }
}
```

**Resultado esperado:** settings.json match=True, outros configs OK.

---

## PASSO 7 — py_compile no live pós-substituição

```powershell
$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"

Write-Host "`n=== py_compile em live\ pós-substituição ==="
cd "C:\FluxQuantumAI"

& $pythonExe -m py_compile `
    run_live.py `
    live\event_processor.py `
    live\level_detector.py `
    live\position_monitor.py `
    live\tick_breakout_monitor.py `
    live\telegram_notifier.py `
    live\base_dashboard_server.py `
    live\m5_updater.py `
    live\m30_updater.py `
    live\operational_rules.py `
    live\feed_health.py `
    live\signal_queue.py `
    live\price_speed.py `
    live\hedge_manager.py `
    live\kill_zones.py `
    live\d1_h4_updater.py 2>&1

$compileExit = $LASTEXITCODE
Write-Host "Compile exit code: $compileExit"
```

**Resultado esperado:** `$compileExit = 0`.

Se não-zero, **PARA IMEDIATAMENTE e reporta.** Não reinicies nada. Rollback é decisão da Barbara.

SyntaxWarnings sobre `\F` em docstrings continuam aceitáveis (cosméticos).

---

## PASSO 8 — Verificar processos de captura pós-substituição

```powershell
Write-Host "`n=== Capture processes still alive ==="
$capturePIDs = @(2512, 8076, 8248)
foreach ($pid in $capturePIDs) {
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "  PID $pid Running (CPU=$([math]::Round($proc.CPU,1))s)"
    } else {
        Write-Host "  PID $pid NOT FOUND — CRITICAL ALARM"
    }
}
```

**Resultado esperado:** 3 PIDs running.

Se algum PID desapareceu, reporta imediatamente — pode significar que algo correu muito mal.

---

## PASSO 9 — Verificação final dos serviços (devem continuar Stopped)

```powershell
Write-Host "`n=== Services final status ==="
Get-Service -Name FluxQuantumAPEX, FluxQuantumAPEX_Live, FluxQuantumAPEX_Dashboard, FluxQuantumAPEX_Dashboard_Hantec -ErrorAction SilentlyContinue | Format-Table Name, Status, StartType -AutoSize
```

**Resultado esperado:** Os 2 serviços de trading continuam **Stopped**. Os 2 dashboards no estado em que estavam (Stopped da sessão anterior).

---

## PASSO 10 — Gerar REPLACEMENT_REPORT

Cria `C:\FluxQuantumAI\REPLACEMENT_REPORT_<timestamp>.md` com:

1. Timestamp de execução
2. Lista dos 7 ficheiros substituídos com hash antes/depois
3. Lista dos 11 ficheiros não tocados com confirmação de unchanged
4. Estado dos configs (settings.json, calibration_results)
5. Exit code do py_compile
6. Estado dos capture PIDs
7. Estado final dos serviços
8. Qualquer warning ou erro encontrado

Formato sugerido:

```markdown
# REPLACEMENT REPORT — <timestamp>

## Summary
- Executed at: <timestamp>
- Files replaced: 7 / 7 <OK | FAIL>
- Unchanged files verified: 11 / 11 <OK | FAIL>
- Configs intact: <OK | CHANGED>
- py_compile exit: <0 | error>
- Capture PIDs: <3/3 Running | some missing>
- Services final: <both Stopped | unexpected>

## Replaced files (pre vs post hash)
[tabela]

## Unchanged files verification
[tabela ou count]

## Config preservation
[details]

## Compile check
[output]

## Capture processes
[output]

## Final state
[services table]

## Observations
<any warnings or anomalies>

## Next step
Observação de 30min. Depois Fase 4 (restart).
```

---

## OUTPUT OBRIGATÓRIO

Responde com:

1. Path do REPLACEMENT_REPORT
2. Conteúdo completo do report (copiado para esta conversa)
3. Confirmação explícita:
   - "7 ficheiros substituídos com sucesso" OU "falhou em X"
   - "Compile exit 0" OU "compile falhou com <erro>"
   - "Capture 3/3 running"
   - "Services remain Stopped"

**Não iniciar serviços. Aguardar autorização da Barbara para Fase 4 após 30min de observação.**

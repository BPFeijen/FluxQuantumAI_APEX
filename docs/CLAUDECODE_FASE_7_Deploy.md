# TASK: FASE 7 — DEPLOY FINAL

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Design doc:** `DESIGN_DOC_Telegram_PositionEvents_v1.md` (all scopes)
**Escopo:** Deploy completo do staging para live (6 ficheiros)
**Tempo estimado:** 20-30 min
**Output:** `FASE_7_DEPLOY_REPORT_<timestamp>.md`

**PRÉ-REQUISITOS (TODOS):**
- Fase 1 backup ✅
- Fase 2 Scope A staging ✅
- Fase 3 Scope B.1 staging ✅
- Fase 4 Scope B.2 staging ✅
- Fase 5 Scope B.3+B.4 staging ✅
- Fase 6 Audit Agregado APROVADO ✅ (veja FASE_6_AUDIT_AGREGADO.md)
- Staging: `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\`
- Barbara aprovou deploy ✅

---

## CRITICAL RULES

1. **Seguir a sequência exacta abaixo.** Nenhum passo fora de ordem.
2. **Cada passo só avança se o anterior reportou SUCCESS.**
3. **Se qualquer passo falhar → ABORT + reportar + aguardar Barbara.**
4. **Não pular validação de hashes em nenhum ponto.**
5. **Se deploy falhar, rollback IMEDIATO** (procedimento documentado abaixo).
6. **Reportar PIDs dos 3 capture processes antes E depois** para confirmar que não foram afectados.

---

## HASHES ESPERADOS — STAGING (fonte)

| Ficheiro | MD5 staging |
|---|---|
| `event_processor.py` | `77DAE71335AF92047ABB515DE4EE71DA` |
| `telegram_notifier.py` | `C0ECC10BF06925C20F152257A4BFA517` |
| `position_monitor.py` | `80D72B7C321A2EFA9ED500246A0D5C04` |
| `mt5_history_watcher.py` | `BCE9E6DCB2B537AAC455EF7FB7602177` (NEW file) |
| `hedge_manager.py` | `357F591AEE63C4F7E01A80298EDE1632` |
| `config/settings.json` | `8A0B28DBFB2F84AD287F9618D2712E59` |

Estes hashes DEVEM bater no staging antes de começar. Se qualquer divergir, **ABORT**.

---

## PRE-FIX HASHES (LIVE, pre-deploy — para rollback)

| Ficheiro | Pre-fix MD5 (LIVE atual) |
|---|---|
| `event_processor.py` | `C48157668BAF47668E61DB460A27BDEE` |
| `telegram_notifier.py` | `4893A895DD5E5EB45B91FF09F0B9A55F` |
| `position_monitor.py` | `91DC4B608B9FD231FE2B9DD0B4BE080A` |
| `hedge_manager.py` | `36902FD51E25AB4C60C1348605E23EC0` |

Live hashes verificados como `LIVE_actual_MD5` no PASSO 2 abaixo.

---

## PASSO 0 — Verify pre-conditions

```powershell
Write-Host "=== FASE 7 DEPLOY — Pre-flight checks @ $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Host ""

# Staging existe
$staging = "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221"
if (-not (Test-Path $staging)) {
    throw "STAGING NOT FOUND: $staging"
}
Write-Host "  OK  Staging found: $staging"

# 6 ficheiros staging existem
$staging_files = @{
    "live\event_processor.py"     = "77DAE71335AF92047ABB515DE4EE71DA"
    "live\telegram_notifier.py"   = "C0ECC10BF06925C20F152257A4BFA517"
    "live\position_monitor.py"    = "80D72B7C321A2EFA9ED500246A0D5C04"
    "live\mt5_history_watcher.py" = "BCE9E6DCB2B537AAC455EF7FB7602177"
    "live\hedge_manager.py"       = "357F591AEE63C4F7E01A80298EDE1632"
    "config\settings.json"        = "8A0B28DBFB2F84AD287F9618D2712E59"
}

Write-Host ""
Write-Host "--- Verifying staging hashes ---"
foreach ($rel_path in $staging_files.Keys) {
    $full_path = Join-Path $staging $rel_path
    $expected = $staging_files[$rel_path]
    if (-not (Test-Path $full_path)) {
        throw "STAGING FILE MISSING: $full_path"
    }
    $actual = (Get-FileHash $full_path -Algorithm MD5).Hash
    if ($actual -eq $expected) {
        Write-Host "  OK  $rel_path : $actual"
    } else {
        Write-Host "  FAIL $rel_path : expected=$expected actual=$actual"
        throw "Staging hash mismatch"
    }
}

# Serviços rodando
Write-Host ""
Write-Host "--- Services status (pre-deploy) ---"
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard, FluxQuantumAPEX_Dashboard_Hantec -ErrorAction SilentlyContinue | Format-Table Name, Status

# Capture processes (NEVER STOP)
Write-Host ""
Write-Host "--- Capture processes (MUST remain running) ---"
$capture_pids = @(12332, 8248, 2512)
foreach ($p in $capture_pids) {
    try {
        $proc = Get-Process -Id $p -ErrorAction Stop
        Write-Host "  OK  PID $p : $($proc.ProcessName) (running)"
    } catch {
        Write-Host "  WARN PID $p : NOT FOUND (may have been restarted)"
    }
}
```

**Critério:** staging hashes 6/6 batem. Se qualquer falhar → ABORT.

---

## PASSO 1 — Backup adicional pre-deploy (resolve P5 do audit)

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup2_dir = "C:\FluxQuantumAI\Backups\pre-deploy-fase7-$timestamp"

Write-Host ""
Write-Host "=== PASSO 1 — Backup pre-deploy Fase 7 ==="
Write-Host "Target: $backup2_dir"

New-Item -Path $backup2_dir -ItemType Directory -Force | Out-Null
New-Item -Path "$backup2_dir\live" -ItemType Directory -Force | Out-Null
New-Item -Path "$backup2_dir\config" -ItemType Directory -Force | Out-Null

# Backup todos os ficheiros que vamos modificar (inclui hedge_manager que faltou no Fase 1)
$files_to_backup = @(
    @{src = "C:\FluxQuantumAI\live\event_processor.py";   dest = "$backup2_dir\live\event_processor.py"}
    @{src = "C:\FluxQuantumAI\live\telegram_notifier.py"; dest = "$backup2_dir\live\telegram_notifier.py"}
    @{src = "C:\FluxQuantumAI\live\position_monitor.py";  dest = "$backup2_dir\live\position_monitor.py"}
    @{src = "C:\FluxQuantumAI\live\hedge_manager.py";     dest = "$backup2_dir\live\hedge_manager.py"}
    @{src = "C:\FluxQuantumAI\config\settings.json";      dest = "$backup2_dir\config\settings.json"}
)

$live_hashes_pre_deploy = @{}

foreach ($f in $files_to_backup) {
    if (-not (Test-Path $f.src)) {
        throw "SOURCE FILE MISSING: $($f.src)"
    }
    $src_hash = (Get-FileHash $f.src -Algorithm MD5).Hash
    Copy-Item $f.src $f.dest -Force
    $bkp_hash = (Get-FileHash $f.dest -Algorithm MD5).Hash
    if ($src_hash -ne $bkp_hash) {
        throw "Backup integrity fail: $($f.src)"
    }
    $filename = Split-Path $f.src -Leaf
    $live_hashes_pre_deploy[$filename] = $src_hash
    Write-Host "  OK  $filename : $src_hash"
}

# Criar manifest
$manifest = @"
# Backup Pre-Deploy Fase 7

Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss UTC')
Purpose: Snapshot do LIVE antes de deploy das 5 fases
Design doc: DESIGN_DOC_Telegram_PositionEvents_v1.md
Barbara approval: 2026-04-18

## Files backed up (LIVE state pre-Fase7)

$($live_hashes_pre_deploy.Keys | ForEach-Object { "$_ : $($live_hashes_pre_deploy[$_])" } | Out-String)

## Rollback procedure

Stop-Service FluxQuantumAPEX
Copy-Item "$backup2_dir\live\*.py" "C:\FluxQuantumAI\live\" -Force
Copy-Item "$backup2_dir\config\settings.json" "C:\FluxQuantumAI\config\" -Force
Remove-Item "C:\FluxQuantumAI\live\mt5_history_watcher.py" -Force -ErrorAction SilentlyContinue
Start-Service FluxQuantumAPEX
"@

Set-Content -Path "$backup2_dir\BACKUP_MANIFEST.md" -Value $manifest -Encoding UTF8
Write-Host ""
Write-Host "  OK  Manifest: $backup2_dir\BACKUP_MANIFEST.md"
```

**Esta é a rede de segurança completa.** Rollback total possível após esta fase.

**Reportar hashes dos ficheiros backup'ed no report final.**

---

## PASSO 2 — Verify live state matches expected pre-fix hashes

```powershell
Write-Host ""
Write-Host "=== PASSO 2 — Live hashes verification (pre-deploy state) ==="

$expected_live_pre = @{
    "event_processor.py"   = "C48157668BAF47668E61DB460A27BDEE"
    "telegram_notifier.py" = "4893A895DD5E5EB45B91FF09F0B9A55F"
    "position_monitor.py"  = "91DC4B608B9FD231FE2B9DD0B4BE080A"
    "hedge_manager.py"     = "36902FD51E25AB4C60C1348605E23EC0"
}

$live_drift_detected = $false
foreach ($fname in $expected_live_pre.Keys) {
    $live_path = "C:\FluxQuantumAI\live\$fname"
    $actual = (Get-FileHash $live_path -Algorithm MD5).Hash
    $expected = $expected_live_pre[$fname]
    if ($actual -eq $expected) {
        Write-Host "  OK  $fname : $actual"
    } else {
        Write-Host "  WARN $fname : DRIFT"
        Write-Host "       expected=$expected"
        Write-Host "       actual=$actual"
        $live_drift_detected = $true
    }
}

if ($live_drift_detected) {
    Write-Host ""
    Write-Host "WARNING: Live has drifted from expected pre-fix state."
    Write-Host "Backup PASSO 1 captured ACTUAL current state, so rollback still valid."
    Write-Host "Proceeding with deploy, but Barbara should be aware."
}
```

**Critério:** Hashes devem bater com pre-fix registado. Se houver drift (improvável, mas possível se algo tocou no live desde 2026-04-18 01:16), reportar mas continuar (backup Passo 1 capturou estado real).

---

## PASSO 3 — Stop services (DEMO + LIVE APEX)

```powershell
Write-Host ""
Write-Host "=== PASSO 3 — Stop APEX services ==="

$apex_services = @("FluxQuantumAPEX", "FluxQuantumAPEX_Live")

foreach ($svc in $apex_services) {
    try {
        $s = Get-Service $svc -ErrorAction Stop
        if ($s.Status -eq "Running") {
            Write-Host "  Stopping $svc ..."
            Stop-Service $svc -Force
            Start-Sleep -Seconds 3
            $s = Get-Service $svc
            Write-Host "  OK $svc is now $($s.Status)"
        } else {
            Write-Host "  OK $svc already $($s.Status) — no action"
        }
    } catch {
        Write-Host "  INFO $svc not found or error: $_"
    }
}

# Verify capture processes ainda running
Write-Host ""
Write-Host "--- Capture processes check (MUST still be running) ---"
$capture_pids = @(12332, 8248, 2512)
foreach ($p in $capture_pids) {
    try {
        $proc = Get-Process -Id $p -ErrorAction Stop
        Write-Host "  OK  PID $p : $($proc.ProcessName) (still running)"
    } catch {
        Write-Host "  WARN PID $p : NOT FOUND (may have been restarted by watchdog)"
    }
}
```

**Dashboards NÃO são parados.** Continuam a servir UI (mesmo sem dados novos durante breve janela).

**Critério:** `FluxQuantumAPEX` e `FluxQuantumAPEX_Live` em Stopped. Captura 3/3 running.

---

## PASSO 4 — Copy staging → live

```powershell
Write-Host ""
Write-Host "=== PASSO 4 — Copy staging to live ==="

$deploy_map = @(
    @{src = "$staging\live\event_processor.py";     dest = "C:\FluxQuantumAI\live\event_processor.py"}
    @{src = "$staging\live\telegram_notifier.py";   dest = "C:\FluxQuantumAI\live\telegram_notifier.py"}
    @{src = "$staging\live\position_monitor.py";    dest = "C:\FluxQuantumAI\live\position_monitor.py"}
    @{src = "$staging\live\mt5_history_watcher.py"; dest = "C:\FluxQuantumAI\live\mt5_history_watcher.py"}
    @{src = "$staging\live\hedge_manager.py";       dest = "C:\FluxQuantumAI\live\hedge_manager.py"}
    @{src = "$staging\config\settings.json";        dest = "C:\FluxQuantumAI\config\settings.json"}
)

foreach ($d in $deploy_map) {
    Copy-Item $d.src $d.dest -Force
    # Verify copy
    $src_hash = (Get-FileHash $d.src -Algorithm MD5).Hash
    $dest_hash = (Get-FileHash $d.dest -Algorithm MD5).Hash
    if ($src_hash -eq $dest_hash) {
        $fname = Split-Path $d.dest -Leaf
        Write-Host "  OK  $fname : $dest_hash"
    } else {
        throw "Copy integrity fail: $($d.dest)"
    }
}
```

**Critério:** 6 ficheiros copiados. Hash staging == hash live para todos.

---

## PASSO 5 — Verify live hashes match staging

```powershell
Write-Host ""
Write-Host "=== PASSO 5 — Verify live state matches staging ==="

$expected_post_deploy = @{
    "C:\FluxQuantumAI\live\event_processor.py"     = "77DAE71335AF92047ABB515DE4EE71DA"
    "C:\FluxQuantumAI\live\telegram_notifier.py"   = "C0ECC10BF06925C20F152257A4BFA517"
    "C:\FluxQuantumAI\live\position_monitor.py"    = "80D72B7C321A2EFA9ED500246A0D5C04"
    "C:\FluxQuantumAI\live\mt5_history_watcher.py" = "BCE9E6DCB2B537AAC455EF7FB7602177"
    "C:\FluxQuantumAI\live\hedge_manager.py"       = "357F591AEE63C4F7E01A80298EDE1632"
    "C:\FluxQuantumAI\config\settings.json"        = "8A0B28DBFB2F84AD287F9618D2712E59"
}

foreach ($fpath in $expected_post_deploy.Keys) {
    if (-not (Test-Path $fpath)) {
        throw "FILE MISSING POST-DEPLOY: $fpath"
    }
    $actual = (Get-FileHash $fpath -Algorithm MD5).Hash
    $expected = $expected_post_deploy[$fpath]
    if ($actual -eq $expected) {
        $fname = Split-Path $fpath -Leaf
        Write-Host "  OK  $fname : $actual"
    } else {
        throw "HASH MISMATCH post-deploy: $fpath (expected=$expected actual=$actual)"
    }
}
```

**Critério:** 6/6 hashes batem. Se qualquer falhar → ROLLBACK IMEDIATO (Passo 10).

---

## PASSO 6 — py_compile all modified files in live

```powershell
Write-Host ""
Write-Host "=== PASSO 6 — py_compile live files ==="

$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$py_files = @(
    "C:\FluxQuantumAI\live\event_processor.py",
    "C:\FluxQuantumAI\live\telegram_notifier.py",
    "C:\FluxQuantumAI\live\position_monitor.py",
    "C:\FluxQuantumAI\live\mt5_history_watcher.py",
    "C:\FluxQuantumAI\live\hedge_manager.py"
)

foreach ($f in $py_files) {
    $result = & $py -m py_compile $f 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK  $f"
    } else {
        Write-Host "  FAIL $f : $result"
        throw "py_compile failed post-deploy: $f"
    }
}
```

**Critério:** 5/5 compile OK. Se qualquer falhar → ROLLBACK.

---

## PASSO 7 — Start services + observe startup

```powershell
Write-Host ""
Write-Host "=== PASSO 7 — Start services ==="

# Start FluxQuantumAPEX
Write-Host "Starting FluxQuantumAPEX (demo)..."
Start-Service FluxQuantumAPEX
Start-Sleep -Seconds 5

$s = Get-Service FluxQuantumAPEX
Write-Host "  FluxQuantumAPEX status: $($s.Status)"

if ($s.Status -ne "Running") {
    Write-Host "  FAIL Service did not start"
    throw "Service start failed"
}

# FluxQuantumAPEX_Live (mantém stopped se já estava — Barbara decide quando reactivar)
Write-Host ""
Write-Host "NOTE: FluxQuantumAPEX_Live NOT started — Barbara controla Hantec service separately."

# Observar logs primeiros 30s
Write-Host ""
Write-Host "=== Observing startup logs (30s) ==="
$startup_log = "C:\FluxQuantumAI\logs\service_stdout.log"
if (Test-Path $startup_log) {
    # Posição actual do ficheiro
    $initial_size = (Get-Item $startup_log).Length
    Start-Sleep -Seconds 30

    # Ler apenas o novo conteúdo
    $fs = New-Object System.IO.FileStream($startup_log, 'Open', 'Read', 'ReadWrite')
    $fs.Seek($initial_size, 'Begin') | Out-Null
    $sr = New-Object System.IO.StreamReader($fs)
    $new_content = $sr.ReadToEnd()
    $sr.Close()
    $fs.Close()

    Write-Host "--- New log content in last 30s ---"
    Write-Host $new_content

    # Flag errors
    $errors_found = $new_content | Select-String -Pattern "ERROR|CRITICAL|Traceback|FATAL"
    if ($errors_found) {
        Write-Host ""
        Write-Host "WARNING: Errors detected in startup log"
        $errors_found | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host ""
        Write-Host "  OK  No errors detected in startup"
    }
}
```

**Critério:**
- Service Running ✅
- Startup log sem "ERROR", "CRITICAL", "Traceback", "FATAL" ✅

**Se houver erros críticos:**
- Log os errors no report
- Se system crashar completamente, ROLLBACK (Passo 10)
- Se errors forem warnings/não-críticos, documentar e continuar

---

## PASSO 8 — Observe runtime 2-3 minutes

```powershell
Write-Host ""
Write-Host "=== PASSO 8 — Runtime observation (3 min) ==="

$obs_duration = 180  # 3 minutes
$start_time = Get-Date
$end_time = $start_time.AddSeconds($obs_duration)

$log_file = "C:\FluxQuantumAI\logs\service_stdout.log"
$initial_size = if (Test-Path $log_file) { (Get-Item $log_file).Length } else { 0 }

while ((Get-Date) -lt $end_time) {
    $remaining = [int]($end_time - (Get-Date)).TotalSeconds
    Write-Host "  Observing... $remaining seconds remaining" -NoNewline
    Start-Sleep -Seconds 10
    Write-Host "`r" -NoNewline
}

# Capture log output during observation
if (Test-Path $log_file) {
    $fs = New-Object System.IO.FileStream($log_file, 'Open', 'Read', 'ReadWrite')
    $fs.Seek($initial_size, 'Begin') | Out-Null
    $sr = New-Object System.IO.StreamReader($fs)
    $runtime_log = $sr.ReadToEnd()
    $sr.Close()
    $fs.Close()

    $log_lines = ($runtime_log -split "`n").Count
    Write-Host ""
    Write-Host "  Runtime log lines captured: $log_lines"

    # Count key events
    $go_count    = ($runtime_log | Select-String -Pattern "GO SIGNAL" -AllMatches).Matches.Count
    $block_count = ($runtime_log | Select-String -Pattern "BLOCK:" -AllMatches).Matches.Count
    $tick_count  = ($runtime_log | Select-String -Pattern "TICK" -AllMatches).Matches.Count
    $error_count = ($runtime_log | Select-String -Pattern "ERROR|Traceback" -AllMatches).Matches.Count

    Write-Host ""
    Write-Host "  --- Event counts during 3min observation ---"
    Write-Host "  GO_SIGNAL : $go_count"
    Write-Host "  BLOCK     : $block_count"
    Write-Host "  TICK      : $tick_count"
    Write-Host "  ERROR     : $error_count"

    if ($error_count -gt 0) {
        Write-Host ""
        Write-Host "  WARNING: Errors during runtime:"
        $runtime_log | Select-String -Pattern "ERROR|Traceback" -AllMatches | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" }
    }
}

# Final service status check
Write-Host ""
Write-Host "--- Final services status ---"
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard, FluxQuantumAPEX_Dashboard_Hantec -ErrorAction SilentlyContinue | Format-Table Name, Status
```

**Critério de sucesso deploy:**
- Service Running após 3 min ✅
- TICK counts > 0 (sistema está a processar) ✅
- ERROR count reasonable (0 crítico ou apenas warnings conhecidos) ✅

---

## PASSO 9 — Hash final confirmation

```powershell
Write-Host ""
Write-Host "=== PASSO 9 — Final hash verification ==="

foreach ($fpath in $expected_post_deploy.Keys) {
    $actual = (Get-FileHash $fpath -Algorithm MD5).Hash
    $expected = $expected_post_deploy[$fpath]
    if ($actual -eq $expected) {
        $fname = Split-Path $fpath -Leaf
        Write-Host "  OK  $fname : $actual"
    } else {
        throw "Final hash mismatch: $fpath"
    }
}

Write-Host ""
Write-Host "=== DEPLOY SUCCESS ==="
Write-Host "All 6 files deployed. Service running. No critical errors."
```

---

## PASSO 10 — ROLLBACK PROCEDURE (só se necessário)

**DISPARAR IMEDIATAMENTE se algum dos passos 4-8 falhar criticamente.**

```powershell
Write-Host ""
Write-Host "=== ROLLBACK INITIATED ==="
Write-Host ""
Write-Host "WARNING: Rolling back to pre-Fase7 state"

# Stop service if running
try { Stop-Service FluxQuantumAPEX -Force -ErrorAction SilentlyContinue } catch {}

# Restore from backup pre-Fase7
$backup2_dir = "<PATH from PASSO 1>"  # ClaudeCode deve saber este path

Copy-Item "$backup2_dir\live\event_processor.py"   "C:\FluxQuantumAI\live\event_processor.py" -Force
Copy-Item "$backup2_dir\live\telegram_notifier.py" "C:\FluxQuantumAI\live\telegram_notifier.py" -Force
Copy-Item "$backup2_dir\live\position_monitor.py"  "C:\FluxQuantumAI\live\position_monitor.py" -Force
Copy-Item "$backup2_dir\live\hedge_manager.py"     "C:\FluxQuantumAI\live\hedge_manager.py" -Force
Copy-Item "$backup2_dir\config\settings.json"      "C:\FluxQuantumAI\config\settings.json" -Force

# Remove the NEW file (mt5_history_watcher not in backup)
Remove-Item "C:\FluxQuantumAI\live\mt5_history_watcher.py" -Force -ErrorAction SilentlyContinue

# Verify rollback hashes match pre-deploy state (should match $live_hashes_pre_deploy)
Write-Host "Rollback verification..."
# (hash comparison vs $live_hashes_pre_deploy)

# Restart service
Start-Service FluxQuantumAPEX
Start-Sleep -Seconds 5
$s = Get-Service FluxQuantumAPEX
Write-Host "Service after rollback: $($s.Status)"

Write-Host ""
Write-Host "=== ROLLBACK COMPLETE ==="
Write-Host "System restored to pre-Fase7 state."
Write-Host "REPORT ROLLBACK REASON TO BARBARA IMMEDIATELY."
```

---

## OUTPUT — Report

Criar `C:\FluxQuantumAI\FASE_7_DEPLOY_REPORT_<timestamp>.md`:

```markdown
# FASE 7 DEPLOY — Report

**Timestamp:** <UTC>
**Duration:** <minutes>
**Status:** ✅ SUCCESS / ❌ FAILED / ⚠ ROLLED BACK

## Passo 0 — Pre-flight
- Staging hashes 6/6 match: ✅/❌
- Services status: (table)
- Capture PIDs: (list)

## Passo 1 — Backup pre-deploy
- Backup location: `C:\FluxQuantumAI\Backups\pre-deploy-fase7-<timestamp>\`
- 5 ficheiros backed up + manifest
- Hashes registered: (list)

## Passo 2 — Live pre-deploy state verified
- Drift detected? YES/NO

## Passo 3 — Services stopped
- FluxQuantumAPEX: Stopped ✅
- FluxQuantumAPEX_Live: (status)
- Capture processes intact: ✅/❌

## Passo 4 — Copy staging → live
- 6/6 files copied with hash integrity ✅

## Passo 5 — Post-copy hash verification
- 6/6 files match staging ✅

## Passo 6 — py_compile
- 5/5 files compile OK ✅

## Passo 7 — Service start
- FluxQuantumAPEX: Running ✅
- Startup log (first 30s): (paste key lines)
- Errors detected: count

## Passo 8 — Runtime observation (3 min)
- TICK events: N
- GO_SIGNAL events: N
- BLOCK events: N
- ERROR events: N
- Final service status: Running ✅

## Passo 9 — Final hash confirmation
- 6/6 files still match staging ✅

## Rollback
- NOT triggered ✅ / Triggered at step N / Reason: ...

## Post-deploy state

### LIVE hashes (deployed)
event_processor.py    : 77DAE71335AF92047ABB515DE4EE71DA
telegram_notifier.py  : C0ECC10BF06925C20F152257A4BFA517
position_monitor.py   : 80D72B7C321A2EFA9ED500246A0D5C04
mt5_history_watcher.py: BCE9E6DCB2B537AAC455EF7FB7602177
hedge_manager.py      : 357F591AEE63C4F7E01A80298EDE1632
settings.json         : 8A0B28DBFB2F84AD287F9618D2712E59

### Services
- FluxQuantumAPEX: Running
- FluxQuantumAPEX_Dashboard: Running (not touched)
- FluxQuantumAPEX_Live: Stopped (Hantec blocked — separate issue)

### Capture processes
- PID 12332 quantower_level2_api: Running (not touched)
- PID 8248 iceberg_receiver: Running (not touched)
- PID 2512 watchdog_l2_capture: Running (not touched)

## Next phase

PARAR. Aguardar Barbara validação empírica Fase 8:
- Observar mensagens Telegram
- Verificar 6 cenários end-to-end do audit agregado
- Confirmar zero regressões observadas em produção
```

---

## PROIBIDO NESTA FASE

- ❌ Qualquer mudança em ficheiros (só copy + hash verify)
- ❌ Parar capture processes (PIDs 12332, 8248, 2512)
- ❌ Parar Dashboard services
- ❌ Continuar se qualquer passo crítico falhar
- ❌ Pular validação de hashes
- ❌ Ignorar erros em startup log

---

## COMUNICAÇÃO FINAL

Se SUCCESS:
```
FASE 7 DEPLOY — SUCCESS
Services: FluxQuantumAPEX Running
Capture: 3/3 intact
Files deployed: 6/6 hashes verified
Errors detected: 0 (or list)
Report: <path>

Aguardando Barbara validação empírica Fase 8.
```

Se ROLLED BACK:
```
FASE 7 DEPLOY — ROLLED BACK
Failed at step: N
Reason: <description>
System state: restored to pre-Fase7
Services running: <status>
Report: <path>

URGENT: Barbara intervention required.
```

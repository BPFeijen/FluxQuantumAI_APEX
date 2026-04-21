# TASK: FASE 1 — Backup Pre-Deploy Telegram + Position Events

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Design doc referenciado:** `DESIGN_DOC_Telegram_PositionEvents_v1.md` (aprovado Barbara 2026-04-17)
**Escopo desta fase:** **APENAS BACKUP**. Nenhuma modificação de código, nenhum restart de serviços.
**Tempo estimado:** 20-30 min
**Output:** `FASE_1_BACKUP_REPORT_<timestamp>.md`

---

## CRITICAL RULES

1. **BACKUP ONLY.** Zero modificações no código live.
2. **NÃO parar FluxQuantumAPEX service.** Fica a correr durante backup.
3. **NÃO parar Dashboard service.** Fica a correr.
4. **NÃO tocar nos processos de captura** (3 PIDs — L2 API porta 8000, iceberg receiver, watchdog).
5. **Verificação de hashes é obrigatória.** Sem hashes, fase não é considerada completa.
6. Report é mandatório. Formato definido abaixo.

---

## OBJETIVO

Criar backup completo dos 3 ficheiros que serão modificados nas fases seguintes:
- `live\event_processor.py`
- `live\telegram_notifier.py`
- `live\position_monitor.py`

Verificar integridade via hashes MD5. Garantir que o backup é **utilizável para rollback** caso alguma fase subsequente falhe.

---

## PASSO 1 — Verificar estado actual do sistema

```powershell
Write-Host "=== FASE 1 BACKUP — Pre-Deploy @ $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Host ""

# Serviços
Write-Host "--- Services Status ---"
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard | Format-Table Name, Status

# Ficheiros a copiar — verificar que existem
$files_to_backup = @(
    "C:\FluxQuantumAI\live\event_processor.py",
    "C:\FluxQuantumAI\live\telegram_notifier.py",
    "C:\FluxQuantumAI\live\position_monitor.py"
)

Write-Host ""
Write-Host "--- Source files verification ---"
foreach ($f in $files_to_backup) {
    if (Test-Path $f) {
        $fileInfo = Get-Item $f
        $hash = (Get-FileHash $f -Algorithm MD5).Hash
        Write-Host "  OK  $f  |  Size: $($fileInfo.Length)B  |  Modified: $($fileInfo.LastWriteTime)  |  MD5: $hash"
    } else {
        Write-Host "  ERROR: FILE NOT FOUND: $f"
        throw "Source file missing: $f"
    }
}
```

**Resultado esperado:**
- 2 serviços Running
- 3 ficheiros present com hashes MD5 calculados
- Se qualquer ficheiro não existir, ABORT.

---

## PASSO 2 — Criar directório de backup com timestamp

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup_dir = "C:\FluxQuantumAI\Backups\pre-telegram-fix-$timestamp"

Write-Host ""
Write-Host "--- Creating backup directory ---"
Write-Host "Target: $backup_dir"

New-Item -Path $backup_dir -ItemType Directory -Force | Out-Null
New-Item -Path "$backup_dir\live" -ItemType Directory -Force | Out-Null

if (Test-Path $backup_dir) {
    Write-Host "  OK — directory created: $backup_dir"
} else {
    throw "Failed to create backup directory"
}
```

---

## PASSO 3 — Copiar ficheiros (com verificação)

```powershell
Write-Host ""
Write-Host "--- Copying files ---"

$source_hashes = @{}
$backup_hashes = @{}

foreach ($f in $files_to_backup) {
    $filename = Split-Path $f -Leaf
    $dest = "$backup_dir\live\$filename"

    # Calculate source hash BEFORE copy
    $src_hash = (Get-FileHash $f -Algorithm MD5).Hash
    $source_hashes[$filename] = $src_hash

    # Copy
    Copy-Item -Path $f -Destination $dest -Force

    # Calculate backup hash AFTER copy
    if (Test-Path $dest) {
        $bkp_hash = (Get-FileHash $dest -Algorithm MD5).Hash
        $backup_hashes[$filename] = $bkp_hash

        if ($src_hash -eq $bkp_hash) {
            Write-Host "  OK  $filename  |  MD5: $src_hash"
        } else {
            Write-Host "  FAIL  $filename  |  src: $src_hash  |  bkp: $bkp_hash"
            throw "Hash mismatch for $filename — backup NOT valid"
        }
    } else {
        throw "Copy failed: $dest not created"
    }
}
```

**Critério:** Hash do ficheiro source DEVE ser igual ao hash do backup. Se diferente, backup corrompido, ABORT.

---

## PASSO 4 — Criar manifest de backup

```powershell
$manifest_path = "$backup_dir\BACKUP_MANIFEST.md"

$manifest = @"
# Backup Manifest — pre-telegram-fix

**Created:** $(Get-Date -Format "yyyy-MM-dd HH:mm:ss UTC")
**Purpose:** Pre-deployment backup for Telegram Decoupling + Position Events integration
**Design doc:** DESIGN_DOC_Telegram_PositionEvents_v1.md
**Approved by:** Barbara (2026-04-17)

## Backup location

``$backup_dir``

## Files backed up

| File | Source | Backup | Source MD5 | Backup MD5 |
|---|---|---|---|---|
"@

foreach ($f in $files_to_backup) {
    $filename = Split-Path $f -Leaf
    $dest = "$backup_dir\live\$filename"
    $src_hash = $source_hashes[$filename]
    $bkp_hash = $backup_hashes[$filename]
    $manifest += "`n| $filename | $f | $dest | $src_hash | $bkp_hash |"
}

$manifest += @"


## Rollback procedure

If any subsequent phase fails, restore with:

``````powershell
Stop-Service FluxQuantumAPEX
Copy-Item "$backup_dir\live\*.py" "C:\FluxQuantumAI\live\" -Force
Start-Service FluxQuantumAPEX
``````

Then verify with:
``````powershell
(Get-FileHash "C:\FluxQuantumAI\live\event_processor.py" -Algorithm MD5).Hash
``````

Should match the "Source MD5" values above (pre-fix state).

## Validation

- [x] All 3 files copied successfully
- [x] All hashes match between source and backup
- [x] Backup directory exists and is readable
"@

Set-Content -Path $manifest_path -Value $manifest -Encoding UTF8

Write-Host ""
Write-Host "--- Manifest created ---"
Write-Host "  OK  $manifest_path"
```

---

## PASSO 5 — Verificação final do backup

```powershell
Write-Host ""
Write-Host "--- Final verification ---"

# Listar backup directory
Get-ChildItem $backup_dir -Recurse | Format-Table Name, Length, LastWriteTime

# Re-verificar hashes
Write-Host ""
Write-Host "--- Re-verification (paranoid check) ---"
foreach ($f in $files_to_backup) {
    $filename = Split-Path $f -Leaf
    $dest = "$backup_dir\live\$filename"
    $src_hash = (Get-FileHash $f -Algorithm MD5).Hash
    $bkp_hash = (Get-FileHash $dest -Algorithm MD5).Hash

    if ($src_hash -eq $bkp_hash) {
        Write-Host "  OK    $filename  matches  ($src_hash)"
    } else {
        Write-Host "  FAIL  $filename  MISMATCH  src=$src_hash  bkp=$bkp_hash"
        throw "Final verification failed for $filename"
    }
}

Write-Host ""
Write-Host "=== FASE 1 BACKUP COMPLETE ==="
Write-Host "Backup location: $backup_dir"
```

---

## OUTPUT ESPERADO

Gerar `FASE_1_BACKUP_REPORT_<timestamp>.md` com:

```markdown
# FASE 1 BACKUP REPORT

**Timestamp:** <UTC datetime>
**Duration:** <minutes>
**Status:** ✅ SUCCESS  |  ❌ FAILED (if fail)

## Services status during backup

- FluxQuantumAPEX: Running
- FluxQuantumAPEX_Dashboard: Running
- Capture processes: 3/3 Running (PIDs unchanged)

## Backup location

``C:\FluxQuantumAI\Backups\pre-telegram-fix-<timestamp>\``

## Files backed up

| File | Source MD5 | Backup MD5 | Match |
|---|---|---|---|
| event_processor.py | <hash> | <hash> | ✅ |
| telegram_notifier.py | <hash> | <hash> | ✅ |
| position_monitor.py | <hash> | <hash> | ✅ |

## Manifest created

``C:\FluxQuantumAI\Backups\pre-telegram-fix-<timestamp>\BACKUP_MANIFEST.md``

## Rollback verified

- [x] Backup directory accessible
- [x] Manifest readable
- [x] All hashes match
- [x] Rollback procedure documented

## Next phase

**Aguarda aprovação Barbara antes de avançar para FASE 2.**

Design doc: `DESIGN_DOC_Telegram_PositionEvents_v1.md`
Próximo: FASE 2 (Scope A — Telegram Decoupling)
Estimated: 6 hours
```

---

## CRITÉRIOS DE SUCESSO

Fase 1 SÓ É CONSIDERADA COMPLETA SE:

1. ✅ 2 serviços permaneceram Running durante todo o backup
2. ✅ 3 ficheiros copiados para backup directory
3. ✅ 3 hashes MD5 source == backup
4. ✅ Manifest criado e legível
5. ✅ Re-verificação paranoid (passo 5) passou
6. ✅ Captura processes não foram interrompidos

Se qualquer critério falhar → **ABORT, report FAILED, esperar Barbara**.

---

## PROIBIDO NESTA FASE

- ❌ Modificar qualquer ficheiro em `C:\FluxQuantumAI\live\`
- ❌ Restart de serviços
- ❌ Tocar em `config\settings.json`
- ❌ Tocar em logs
- ❌ Executar código Python
- ❌ Continuar para FASE 2 sem autorização Barbara
- ❌ Assumir que algum hash vai bater — sempre verificar empiricamente

---

## COMUNICAÇÃO

ClaudeCode reporta ao terminar:

```
FASE 1 BACKUP — <STATUS>
Backup: <path>
Hashes verificados: 3/3
Manifest: <path>
Report: <path>
Services status: <status>

Aguardando aprovação Barbara para FASE 2.
```

**Não iniciar FASE 2 até Barbara aprovar explicitamente.**

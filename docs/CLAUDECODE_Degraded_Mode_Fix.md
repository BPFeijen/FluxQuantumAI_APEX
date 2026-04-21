# TASK: SPRINT FIX — Restaurar 4 features broken em produção APEX

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Escopo:** Fix completo (F1+F2) das 4 features broken, com salvaguardas e rollback
**Deadline PO:** Domingo antes mercado abrir (~22h UTC)
**Output:** `C:\FluxQuantumAI\backlog\degraded_mode_fix_<timestamp>\`

---

## CONTEXTO

Investigação anterior (`degraded_mode_investigation_20260418_180018.md`) identificou:

| Feature | Bug | Fix |
|---|---|---|
| StatGuardrail | 1 path (`grenadier_guardrail.py:33`) | 1 linha |
| V4 IcebergInference | 1 path (`ats_live_gate.py:91`) | 1 linha |
| DefenseMode | 2 paths (`event_processor.py:122` + `anomaly_scorer.py:404`) | 2 linhas |
| ApexNewsGate | 1 path (`event_processor.py:95`) + import syntax (`event_processor.py:96`) | 2 linhas |

**Total: 6 mudanças em 4 ficheiros.**

**Barbara aprovou execução completa (F1+F2 juntos) com salvaguardas robustas.**

---

## CRITICAL RULES

1. **Backup COMPLETO antes de qualquer mudança** — 4 ficheiros mais `news_config.yaml`.
2. **py_compile obrigatório** após cada edição.
3. **Import probe isolado obrigatório** antes de restart produção.
4. **Se qualquer teste falhar, ABORT e reportar.** Não restart service.
5. **Rollback procedure pronto a disparar** (<1 min).
6. **Não tocar em serviços** até todos os testes passarem.
7. **Não tocar em capture processes** (PIDs 12332, 8248, 2512) em nenhum momento.

---

## PASSO 0 — Check preventivo (10 min)

**Antes de tocar em qualquer código**, verificar se `APEX_Anomaly/` e `APEX_Iceberg/` têm relative imports bugados (como `APEX_News` tinha).

```powershell
Write-Host "=== Relative imports in APEX_Anomaly ==="
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly" -Recurse -Filter "*.py" |
    Select-String -Pattern "^from \.\w" |
    Format-Table Path, LineNumber, Line -Wrap

Write-Host ""
Write-Host "=== Relative imports in APEX_Iceberg ==="
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg" -Recurse -Filter "*.py" |
    Select-String -Pattern "^from \.\w" |
    Format-Table Path, LineNumber, Line -Wrap
```

**Interpretação:**

- **Se encontrar relative imports em APEX_Anomaly ou APEX_Iceberg** → STOP. Reportar antes de prosseguir. Fix pode ser maior que esperado.
- **Se não encontrar** → OK, prosseguir para PASSO 1.

---

## PASSO 1 — Mostrar `news_config.yaml` para Barbara validar (5 min)

```powershell
$config = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml"
$content = Get-Content $config -Raw

# Imprimir conteúdo MAS redactar API keys
$redacted = $content -replace "(api_key\s*:\s*)['\`"]?[^'\`"\r\n]+['\`"]?", '$1<REDACTED>'
Write-Host "=== news_config.yaml (API keys redacted) ==="
Write-Host $redacted
```

**STOP aqui.** Não prosseguir sem aprovação explícita da Barbara sobre os thresholds.

**Output deste passo:** conteúdo yaml redacted colado no reporte para Barbara + Claude reverem.

---

## PASSO 2 — Backup dos 4 ficheiros + news_config (2 min)

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup_dir = "C:\FluxQuantumAI\Backups\degraded_mode_fix_pre_$timestamp"
New-Item -Path $backup_dir -ItemType Directory -Force | Out-Null

$files_to_backup = @(
    @{src = "C:\FluxQuantumAI\grenadier_guardrail.py";                                             dest = "$backup_dir\grenadier_guardrail.py"}
    @{src = "C:\FluxQuantumAI\live\event_processor.py";                                             dest = "$backup_dir\event_processor.py"}
    @{src = "C:\FluxQuantumAI\ats_live_gate.py";                                                    dest = "$backup_dir\ats_live_gate.py"}
    @{src = "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py";                dest = "$backup_dir\anomaly_scorer.py"}
    @{src = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml";                              dest = "$backup_dir\news_config.yaml"}
)

$backup_hashes = @{}
foreach ($f in $files_to_backup) {
    if (-not (Test-Path $f.src)) {
        throw "SOURCE FILE MISSING: $($f.src)"
    }
    Copy-Item $f.src $f.dest -Force
    $hash = (Get-FileHash $f.src -Algorithm MD5).Hash
    $backup_hashes[(Split-Path $f.src -Leaf)] = $hash
    Write-Host "  OK backup: $($f.src) -> $hash"
}

# Manifest
$manifest = @{
    timestamp = $timestamp
    purpose = "Pre-fix degraded mode F1+F2"
    files = $backup_hashes
} | ConvertTo-Json -Depth 3
Set-Content -Path "$backup_dir\BACKUP_MANIFEST.json" -Value $manifest

Write-Host ""
Write-Host "Backup complete: $backup_dir"
```

**Critério:** 5 ficheiros backup'd com hashes registados. Se algum falha, ABORT.

---

## PASSO 3 — Aplicar F1 (fix paths trivial — 3 features)

**Usar `str_replace` com contexto rico** para segurança.

### 3.1 Fix `grenadier_guardrail.py:33` (StatGuardrail)

```python
# OLD (capture enough context):
_ANOMALY_PKG = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")

# NEW:
_ANOMALY_PKG = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")
```

### 3.2 Fix `event_processor.py:122` (DefenseMode sys.path)

```python
# OLD:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))

# NEW:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")))
```

### 3.3 Fix `ats_live_gate.py:91` (V4 IcebergInference)

```python
# OLD:
_ICEBERG_PKG_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Iceberg")

# NEW:
_ICEBERG_PKG_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg")
```

### 3.4 Fix `anomaly_scorer.py:404` (DefenseMode scaler hardcoded)

```python
# OLD:
r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly\models\grenadier_scaler_4f.json"

# NEW:
r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\models\grenadier_scaler_4f.json"
```

**Após cada edit, py_compile:**
```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m py_compile "<file_path>"
if ($LASTEXITCODE -ne 0) { throw "py_compile failed: <file>" }
Write-Host "  OK py_compile: <file>"
```

**Se algum py_compile falha, ABORT + rollback do ficheiro específico.**

---

## PASSO 4 — Aplicar F2 (fix news_gate — Abordagem B)

### 4.1 Fix `event_processor.py:95` (path)

```python
# OLD:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_News")))

# NEW:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD")))
```

### 4.2 Fix `event_processor.py:96` (import syntax)

```python
# OLD:
from apex_news_gate import news_gate as _news_gate

# NEW:
from APEX_News.apex_news_gate import news_gate as _news_gate
```

**py_compile após:**
```powershell
& $py -m py_compile "C:\FluxQuantumAI\live\event_processor.py"
```

---

## PASSO 5 — Import probe isolado (5 min)

**Testar imports numa sessão Python fresh** (zero interference com produção).

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$probe_script = @'
import sys
import traceback

print("=== Import probe: 4 modules ===")
print()

# Replicar sys.path como production run_live.py
sys.path.insert(0, r"C:\FluxQuantumAI")

results = {}

# 1. StatGuardrail
print("--- 1. StatGuardrail ---")
try:
    from grenadier_guardrail import get_guardrail_status, update_guardrail
    print("  OK: get_guardrail_status, update_guardrail imported")
    results["StatGuardrail"] = "OK"
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")
    traceback.print_exc()
    results["StatGuardrail"] = f"FAIL: {e}"
print()

# 2. DefenseMode (precisa path APEX_Anomaly)
print("--- 2. DefenseMode ---")
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")
    from inference.anomaly_scorer import GrenadierDefenseMode
    print(f"  OK: GrenadierDefenseMode class imported")
    # Try instantiation
    try:
        dm = GrenadierDefenseMode()
        print(f"  OK: instantiated")
        results["DefenseMode"] = "OK (instantiable)"
    except Exception as ie:
        print(f"  WARN: import OK but instantiation failed: {type(ie).__name__}: {ie}")
        results["DefenseMode"] = f"IMPORT_OK_INSTANTIATION_FAIL: {ie}"
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")
    traceback.print_exc()
    results["DefenseMode"] = f"FAIL: {e}"
print()

# 3. V4 IcebergInference
print("--- 3. V4 IcebergInference ---")
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg")
    from ats_iceberg_v1 import IcebergInference
    print(f"  OK: IcebergInference imported")
    results["V4_IcebergInference"] = "OK"
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")
    traceback.print_exc()
    results["V4_IcebergInference"] = f"FAIL: {e}"
print()

# 4. ApexNewsGate (Abordagem B)
print("--- 4. ApexNewsGate ---")
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD")
    from APEX_News.apex_news_gate import news_gate
    print(f"  OK: news_gate imported")
    print(f"  Type: {type(news_gate)}")
    # Test basic callable
    if hasattr(news_gate, "check_score") or hasattr(news_gate, "score") or callable(news_gate):
        print(f"  OK: news_gate has callable interface")
    results["ApexNewsGate"] = "OK"
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")
    traceback.print_exc()
    results["ApexNewsGate"] = f"FAIL: {e}"
print()

# Summary
print("=" * 60)
print("SUMMARY:")
for k, v in results.items():
    print(f"  {k}: {v}")
print()

# Exit code based on results
failures = sum(1 for v in results.values() if "FAIL" in v)
if failures > 0:
    print(f"RESULT: {failures} FAILURES — DO NOT RESTART PRODUCTION")
    sys.exit(1)
else:
    print("RESULT: ALL IMPORTS OK — safe to proceed with restart")
    sys.exit(0)
'@

$probe_script | Out-File -FilePath "$env:TEMP\import_probe.py" -Encoding UTF8
& $py "$env:TEMP\import_probe.py"
$probe_exit = $LASTEXITCODE

if ($probe_exit -ne 0) {
    Write-Host ""
    Write-Host "!! IMPORT PROBE FAILED !!"
    Write-Host "STOPPING HERE. DO NOT restart production."
    Write-Host "Analyze failures above and decide: rollback or further investigation."
    throw "Import probe failed with $probe_exit failures"
}

Write-Host ""
Write-Host "  OK: Import probe passed. Safe to proceed with restart."
```

**Critério obrigatório:** 4/4 imports OK + DefenseMode instantiable.

**Se qualquer falha:** ABORT. Rollback automático (ver PASSO 8).

---

## PASSO 6 — Restart service + stream logs (5 min)

### 6.1 Stop service

```powershell
Write-Host "=== Stopping FluxQuantumAPEX ==="
Stop-Service FluxQuantumAPEX -Force
Start-Sleep -Seconds 3
$status = (Get-Service FluxQuantumAPEX).Status
Write-Host "  Status: $status"

if ($status -ne "Stopped") {
    throw "Failed to stop FluxQuantumAPEX"
}

# Verify capture processes untouched
Write-Host ""
Write-Host "=== Capture processes check ==="
foreach ($pid in @(12332, 8248, 2512)) {
    try {
        $proc = Get-Process -Id $pid -ErrorAction Stop
        Write-Host "  OK PID $pid running: $($proc.ProcessName)"
    } catch {
        Write-Host "  WARN PID $pid not found (may have been restarted by watchdog)"
    }
}
```

### 6.2 Capture initial log size, start service

```powershell
$stdout_log = "C:\FluxQuantumAI\logs\service_stdout.log"
$stderr_log = "C:\FluxQuantumAI\logs\service_stderr.log"

$initial_stdout = if (Test-Path $stdout_log) { (Get-Item $stdout_log).Length } else { 0 }
$initial_stderr = if (Test-Path $stderr_log) { (Get-Item $stderr_log).Length } else { 0 }

Write-Host "=== Starting FluxQuantumAPEX ==="
Start-Service FluxQuantumAPEX
Start-Sleep -Seconds 3
Write-Host "  Status: $((Get-Service FluxQuantumAPEX).Status)"

# Wait 60s for startup to complete
Write-Host ""
Write-Host "=== Waiting 60s for startup ==="
for ($i = 60; $i -gt 0; $i -= 5) {
    Write-Host "  $i seconds remaining..."
    Start-Sleep -Seconds 5
}
```

### 6.3 Check log markers

```powershell
# Read new content since restart
function Read-NewContent($path, $initial_size) {
    if (-not (Test-Path $path)) { return "" }
    $fs = New-Object System.IO.FileStream($path, 'Open', 'Read', 'ReadWrite')
    $fs.Seek($initial_size, 'Begin') | Out-Null
    $sr = New-Object System.IO.StreamReader($fs)
    $content = $sr.ReadToEnd()
    $sr.Close()
    $fs.Close()
    return $content
}

$stdout_new = Read-NewContent $stdout_log $initial_stdout
$stderr_new = Read-NewContent $stderr_log $initial_stderr
$combined = $stdout_new + "`n" + $stderr_new

# Define expected markers per feature
$markers = @{
    "StatGuardrail"       = @("StatGuardrail wired", "grenadier_guardrail", "guardrail loaded")
    "DefenseMode"         = @("GrenadierDefenseMode", "DefenseMode initialized", "DEFENSE_MODE")
    "V4_IcebergInference" = @("IcebergInference", "V4 Iceberg", "ats_iceberg_v1")
    "ApexNewsGate"        = @("ApexNewsGate loaded", "ApexNewsGate", "news_gate loaded")
}

# Also check for failure markers
$failure_markers = @("not available", "ImportError", "ModuleNotFoundError", "Traceback", "FAILED to load")

Write-Host ""
Write-Host "=== Log markers check ==="

$results = @{}
foreach ($feature in $markers.Keys) {
    $found_success = $false
    $found_failure = $false
    $evidence = @()
    
    foreach ($marker in $markers[$feature]) {
        if ($combined -match $marker) {
            $found_success = $true
            $evidence += $marker
        }
    }
    
    # Check for failure text near this feature name
    foreach ($fail_marker in $failure_markers) {
        # Check if failure marker appears near feature name in logs
        $pattern = "$($markers[$feature][0]).*$fail_marker|$fail_marker.*$($markers[$feature][0])"
        if ($combined -match $pattern) {
            $found_failure = $true
            $evidence += "FAILURE: $fail_marker"
        }
    }
    
    $status = if ($found_failure) { "FAIL" }
              elseif ($found_success) { "OK" }
              else { "UNKNOWN" }
    
    $results[$feature] = @{
        status = $status
        evidence = $evidence
    }
    
    Write-Host "  $feature : $status"
    if ($evidence) {
        $evidence | ForEach-Object { Write-Host "    - $_" }
    }
}

# Overall check
$failed = $results.GetEnumerator() | Where-Object { $_.Value.status -eq "FAIL" }
$unknown = $results.GetEnumerator() | Where-Object { $_.Value.status -eq "UNKNOWN" }

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host "!! $($failed.Count) FEATURE(S) FAILED TO LOAD !!"
    Write-Host "Listing failures:"
    $failed | ForEach-Object { Write-Host "  - $($_.Key)" }
    throw "Startup validation failed"
}

if ($unknown.Count -gt 0) {
    Write-Host "WARN: $($unknown.Count) feature(s) status UNKNOWN (no markers found):"
    $unknown | ForEach-Object { Write-Host "  - $($_.Key)" }
    Write-Host "Manual inspection of logs recommended."
}

Write-Host ""
Write-Host "Log new content sample (last 50 lines):"
$combined -split "`n" | Select-Object -Last 50
```

---

## PASSO 7 — Observar runtime 5-10 min

```powershell
Write-Host ""
Write-Host "=== Runtime observation (10 min) ==="

$obs_start = Get-Date
$obs_end = $obs_start.AddMinutes(10)

while ((Get-Date) -lt $obs_end) {
    $remaining = [int]($obs_end - (Get-Date)).TotalSeconds
    Write-Host "  $remaining seconds remaining..."
    Start-Sleep -Seconds 30
}

# Final log scan
$stdout_final = Read-NewContent $stdout_log $initial_stdout
$stderr_final = Read-NewContent $stderr_log $initial_stderr

# Count tracebacks
$tracebacks = ($stderr_final | Select-String -Pattern "Traceback" -AllMatches).Matches.Count
$errors = ($stderr_final | Select-String -Pattern "ERROR" -AllMatches).Matches.Count

Write-Host ""
Write-Host "=== 10min observation summary ==="
Write-Host "  Tracebacks: $tracebacks"
Write-Host "  ERROR count: $errors"
Write-Host "  Service status: $((Get-Service FluxQuantumAPEX).Status)"

if ($tracebacks -gt 3 -or (Get-Service FluxQuantumAPEX).Status -ne "Running") {
    Write-Host "!! Runtime issues detected — consider rollback"
}
```

---

## PASSO 8 — Rollback procedure (SE NECESSÁRIO)

**Só disparar se PASSO 5, 6 ou 7 falharem criticamente.**

```powershell
Write-Host "=== ROLLBACK INITIATED ==="

# Stop service
try { Stop-Service FluxQuantumAPEX -Force -ErrorAction SilentlyContinue } catch {}

# Restore 4 files from backup
$backup_dir = "<PATH from PASSO 2>"

Copy-Item "$backup_dir\grenadier_guardrail.py" "C:\FluxQuantumAI\grenadier_guardrail.py" -Force
Copy-Item "$backup_dir\event_processor.py"    "C:\FluxQuantumAI\live\event_processor.py" -Force
Copy-Item "$backup_dir\ats_live_gate.py"      "C:\FluxQuantumAI\ats_live_gate.py" -Force
Copy-Item "$backup_dir\anomaly_scorer.py"     "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py" -Force

# Verify restore hashes
# ... (compare against BACKUP_MANIFEST.json)

# Restart
Start-Service FluxQuantumAPEX
Start-Sleep -Seconds 5

Write-Host "=== ROLLBACK COMPLETE ==="
Write-Host "System restored to pre-fix state."
Write-Host "REPORT IMMEDIATELY to Barbara + Claude."
```

---

## OUTPUT — Reporte final

Criar `C:\FluxQuantumAI\backlog\degraded_mode_fix_<timestamp>\FIX_REPORT.md`:

```markdown
# Degraded Mode Fix — Report

**Timestamp:** <UTC>
**Duration:** X min
**Status:** ✅ SUCCESS / ❌ FAILED / ⚠ ROLLED BACK

## Passo 0 — Check preventivo
- Relative imports in APEX_Anomaly: [count + list if found]
- Relative imports in APEX_Iceberg: [count + list if found]
- Decision: [proceeded / stopped]

## Passo 1 — news_config.yaml review
- Content reviewed by Barbara: [yes/no]
- Thresholds approved: [yes/no]

## Passo 2 — Backup
- Location: <path>
- 5 files backed up with MD5 hashes

## Passo 3 — F1 fixes applied
- grenadier_guardrail.py:33 ✅
- event_processor.py:122 ✅
- ats_live_gate.py:91 ✅
- anomaly_scorer.py:404 ✅

## Passo 4 — F2 fixes applied
- event_processor.py:95 ✅
- event_processor.py:96 ✅

## Passo 5 — Import probe
- StatGuardrail: OK/FAIL
- DefenseMode: OK/FAIL
- V4 IcebergInference: OK/FAIL
- ApexNewsGate: OK/FAIL

## Passo 6 — Restart + markers
- StatGuardrail marker: ✅/❌
- DefenseMode marker: ✅/❌
- V4 IcebergInference marker: ✅/❌
- ApexNewsGate marker: ✅/❌

## Passo 7 — 10min observation
- Tracebacks: N
- Errors: N
- Service status: Running

## Rollback
- Triggered: yes/no
- Reason (if yes): ...

## Post-fix hashes (live)
[4 file hashes]

## Next steps
- Observe Telegram for 30min+
- Validate domingo noite mercado abrir
```

---

## COMUNICAÇÃO FINAL

**Se SUCCESS:**
```
DEGRADED MODE FIX — SUCCESS
4 features restored: StatGuardrail, DefenseMode, V4 IcebergInference, ApexNewsGate
Import probe: 4/4 OK
Log markers: 4/4 OK
Runtime 10min: clean (0 tracebacks)
Capture processes: intact (PIDs 12332, 8248, 2512)
Backup: <path>
Report: <path>

Aguardando Barbara observação Telegram + domingo mercado.
```

**Se ROLLED BACK:**
```
DEGRADED MODE FIX — ROLLED BACK
Failed at step: N
Reason: <detalhe>
System restored to pre-fix state
Service running: <status>
Report: <path>

URGENT: Barbara + Claude analyze failure.
```

---

## PROIBIDO

- ❌ Pular PASSO 0 (check preventivo)
- ❌ Pular PASSO 1 (Barbara review yaml)
- ❌ Editar ficheiros sem backup
- ❌ Restart production se import probe falha
- ❌ Parar capture processes em qualquer momento
- ❌ Continuar se py_compile falha
- ❌ Declarar SUCCESS se qualquer marker está "FAIL"

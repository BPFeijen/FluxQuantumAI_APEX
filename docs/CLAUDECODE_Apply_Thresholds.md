# TASK: APLICAR THRESHOLDS DATA-DRIVEN news_gate

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Pre-requisito:** FASE I + FASE II completas. Sistema Running, 4 features activas.
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\APPLY_THRESHOLDS_REPORT.md`

---

## DECISÕES TOMADAS (não discutir, aplicar)

Baseadas em FASE II data-driven analysis (24,073 minutos × 200 events US):

| Parâmetro | Default | NEW | Evidência |
|---|---|---|---|
| `pre_event_seconds` | 1800 (30min) | **300 (5min)** | HIGH PRE_NEWS_5: 1.43× baseline, d=0.35, p<0.0001. PRE_NEWS_30: 1.09× (marginal). |
| `post_event_seconds` | 1800 (30min) | **180 (3min) conservador** | Dados mostram POST_5 compression (0.59×), mas 3min dá margem para slippage real do release. |
| `block_threshold` | 2.5 | **2.0** | MEDIUM DURING = 2.20× é risco real |
| `caution_threshold` | 1.5 | **1.4** | captura HIGH PRE_5 (1.43×) |
| `monitor_threshold` | 0.5 | **1.05** | tighter, reduce false alarms |

**Importance actions:**
- HIGH: BLOCK during + CAUTION pre_5 (d=0.82 + 0.35)
- MEDIUM: BLOCK during only (d=0.49)
- LOW: IGNORE (d≈0, não-significativo)

**Rationale:** rule-based news_gate é PROTECÇÃO (close posições 5min antes de HIGH, reabrir 3min após), **não estratégia**. Operar durante/imediatamente pós-high-risk será domínio futuro do módulo HighRisk ML. Conservative post=180s evita sistema rule-based a tentar fazer trabalho de ML.

---

## REGRA CRÍTICA

- **ZERO assumptions.** Mostrar estrutura actual do yaml ANTES de editar.
- **Backup obrigatório.**
- **Validar yaml parseable após edit.**
- **py_compile não aplica** (é yaml, não py) — mas validar carregamento via Python YAML.
- **Não tocar capture processes** (PIDs 12332, 8248, 2512).
- **Observação 10min pós-restart.**
- **Rollback pronto.**

---

## PASSO 1 — Discovery yaml actual

```powershell
$yaml_path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml"

Write-Host "=== CURRENT yaml STRUCTURE ==="
Get-Content $yaml_path -Raw
Write-Host ""
Write-Host "=== END yaml ==="
```

**REPORTAR estrutura exacta antes de editar.** Identificar:
- Onde estão `pre_event_seconds` e `post_event_seconds` (ou equivalents)
- Onde estão `block_threshold`, `caution_threshold`, `monitor_threshold` (gold_blocking section)
- Se existe secção `importance_weights` ou precisa criar

---

## PASSO 2 — Backup yaml

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sprint_dir = "C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908"
$backup_dir = "$sprint_dir\backup_apply_thresholds_$timestamp"
New-Item -Path $backup_dir -ItemType Directory -Force | Out-Null

Copy-Item $yaml_path "$backup_dir\news_config.yaml" -Force
$hash_pre = (Get-FileHash $yaml_path -Algorithm MD5).Hash
Write-Host "backup OK: news_config.yaml ($hash_pre)"
Write-Host "backup_dir: $backup_dir"
```

---

## PASSO 3 — Aplicar edits yaml

**Preservar estrutura existente.** Alterar apenas valores dos parâmetros.

### 3.1 Windows

```yaml
# IF existing:
#   pre_event_seconds: 1800
# CHANGE TO:
#   pre_event_seconds: 300
#
# IF existing:
#   post_event_seconds: 1800
# CHANGE TO:
#   post_event_seconds: 180
```

### 3.2 gold_blocking thresholds

```yaml
# IF existing:
#   gold_blocking:
#     block_threshold: 2.5
#     caution_threshold: 1.5
#     monitor_threshold: 0.5
# CHANGE TO:
#   gold_blocking:
#     block_threshold: 2.0
#     caution_threshold: 1.4
#     monitor_threshold: 1.05
```

### 3.3 Importance weights (ADD if not exists)

```yaml
# ADD new section (or update if exists):
importance_weights:
  HIGH:
    DURING: 1.0      # EXIT_ALL — d=0.82, 4.49× baseline
    PRE_5: 0.75      # CAUTION/REDUCED — d=0.35, 1.43× baseline
    POST_5: 0.0      # no action (conservative wait via post_event_seconds=180s)
  MEDIUM:
    DURING: 0.75     # REDUCED — d=0.49, 2.20× baseline
    PRE_5: 0.0       # no action (d≈0)
    POST_5: 0.0
  LOW:
    DURING: 0.0      # IGNORE (d=0.39 marginal, p=0.013)
    PRE_5: 0.0
    POST_5: 0.0
```

**Decisão:** se yaml existente não tem `importance_weights`, adicionar. Se tem estrutura diferente, **REPORTAR e aguardar decisão** — não adivinhar mapeamento.

---

## PASSO 4 — Validar yaml

```python
import yaml
from pathlib import Path

yaml_path = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml")

try:
    with open(yaml_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    print("YAML parseable: OK")
    print(f"Keys: {list(config.keys())}")
    
    # Validar valores propostos
    expected = {
        'pre_event_seconds': 300,
        'post_event_seconds': 180,
    }
    for key, val in expected.items():
        # Navigate nested structure if needed
        actual = config.get(key)
        if actual != val:
            print(f"WARN: {key} = {actual}, expected {val}")
        else:
            print(f"OK: {key} = {val}")
    
    # Validar thresholds
    gb = config.get('gold_blocking', {})
    for key, val in {'block_threshold': 2.0, 'caution_threshold': 1.4, 'monitor_threshold': 1.05}.items():
        actual = gb.get(key)
        if actual != val:
            print(f"WARN: gold_blocking.{key} = {actual}, expected {val}")
        else:
            print(f"OK: gold_blocking.{key} = {val}")
except Exception as e:
    print(f"YAML VALIDATION FAILED: {e}")
    raise
```

**Se validation falha:** NÃO restart. Reportar + rollback imediato.

---

## PASSO 5 — Import probe (garantir que apex_news_gate carrega yaml OK)

```python
import sys
sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_News")

try:
    from apex_news_gate import news_gate
    # Verificar que news_gate carregou config
    # Se tem método/attribute para show config, usar
    print("apex_news_gate loaded OK with new config")
except Exception as e:
    print(f"apex_news_gate FAILED to load: {e}")
    raise
```

---

## PASSO 6 — Restart service + observar

```powershell
$stdout = "C:\FluxQuantumAI\logs\service_stdout.log"
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"
$init_stdout = if (Test-Path $stdout) { (Get-Item $stdout).Length } else { 0 }
$init_stderr = if (Test-Path $stderr) { (Get-Item $stderr).Length } else { 0 }

Stop-Service FluxQuantumAPEX -Force
Start-Sleep 3

# Verificar capture processes
foreach ($pid in 12332, 8248, 2512) {
    try {
        $p = Get-Process -Id $pid -ErrorAction Stop
        Write-Host "OK PID $pid ($($p.ProcessName))"
    } catch { Write-Host "WARN PID $pid not found" }
}

Start-Service FluxQuantumAPEX
Start-Sleep 60

function Read-New($path, $init) {
    if (-not (Test-Path $path)) { return "" }
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    $fs.Seek($init, 'Begin') | Out-Null
    $sr = New-Object IO.StreamReader($fs)
    $c = $sr.ReadToEnd(); $sr.Close(); $fs.Close(); return $c
}

$new_all = (Read-New $stdout $init_stdout) + "`n" + (Read-New $stderr $init_stderr)

# Check for startup errors
$tracebacks = ($new_all | Select-String "Traceback" -AllMatches).Matches.Count
$yaml_errors = ($new_all | Select-String "yaml|YAML|config" -AllMatches).Matches.Count

Write-Host "Post-restart: tracebacks=$tracebacks"

if ($tracebacks -gt 0) {
    Write-Host "TRACEBACKS DETECTED — review before continuing"
    throw "STARTUP FAILED"
}

Write-Host "=== Observing 10 min ==="
Start-Sleep 600

$final_stderr = Read-New $stderr $init_stderr
$tracebacks_10 = ($final_stderr | Select-String "Traceback" -AllMatches).Matches.Count
$errors_10 = ($final_stderr | Select-String "ERROR" -AllMatches).Matches.Count
$svc_status = (Get-Service FluxQuantumAPEX).Status

Write-Host "10min: tracebacks=$tracebacks_10 errors=$errors_10 service=$svc_status"

if ($tracebacks_10 -gt 3 -or $svc_status -ne "Running") {
    throw "RUNTIME DEGRADED — consider rollback"
}
```

---

## PASSO 7 — Rollback (SE NECESSÁRIO)

```powershell
Stop-Service FluxQuantumAPEX -Force -ErrorAction SilentlyContinue
Copy-Item "$backup_dir\news_config.yaml" $yaml_path -Force
Start-Service FluxQuantumAPEX
Write-Host "ROLLBACK COMPLETE"
```

---

## PASSO 8 — TASK_CLOSEOUT report

Criar `$sprint_dir\APPLY_THRESHOLDS_REPORT.md`:

```markdown
# APPLY THRESHOLDS — news_gate Data-Driven — TASK_CLOSEOUT

**Timestamp:** <UTC>
**Duration:** X min
**Status:** ✅ SUCCESS / ❌ FAILED / ⚠ ROLLED BACK

## Backup
- Location: <path>
- news_config.yaml pre-hash: X
- news_config.yaml post-hash: Y

## Changes applied
| Parameter | Before | After |
|---|---|---|
| pre_event_seconds | 1800 | 300 |
| post_event_seconds | 1800 | 180 |
| gold_blocking.block_threshold | 2.5 | 2.0 |
| gold_blocking.caution_threshold | 1.5 | 1.4 |
| gold_blocking.monitor_threshold | 0.5 | 1.05 |
| importance_weights | ADDED / UPDATED | (see yaml) |

## YAML validation
- Parseable: ✅
- Expected values present: ✅/❌

## Import probe
- apex_news_gate carregou new config: ✅/❌

## Service restart
- Stop: success
- Capture processes: 3/3 intact
- Start: success, new PID: X
- Post-restart tracebacks: 0

## 10-min observation
- Tracebacks: N
- Errors: N
- Service: Running
- [GATE] Guardrail logs: N
- Any news events during observation: [list or none]

## Final hashes
- news_config.yaml pre: X
- news_config.yaml post: Y
- All other files: UNCHANGED

## Rollback
- Triggered: yes/no
- Reason (if yes): ...

## Next steps
- Observação empírica mercado Domingo 22h UTC
- Primeiro HIGH risk event: monitorar Telegram messages (BLOCK + CAUTION)
- Ajustar se evidência empírica contradizer proposta
- FASE III (dataset HighRisk) — sprint separado
```

---

## COMUNICAÇÃO FINAL

**Success:**
```
THRESHOLDS APPLIED
pre_event: 1800s → 300s
post_event: 1800s → 180s (conservador)
block/caution/monitor: 2.5/1.5/0.5 → 2.0/1.4/1.05
Importance weights: HIGH/MEDIUM/LOW configured
Service: Running, 10min clean
Report: APPLY_THRESHOLDS_REPORT.md
```

---

## PROIBIDO

- ❌ Tocar capture processes
- ❌ Editar outros ficheiros além de news_config.yaml
- ❌ Adivinhar estrutura se yaml tiver formato diferente — reportar e parar
- ❌ Rollback automático sem log claro da razão
- ❌ Skipar validation yaml
- ❌ Skipar 10min observation
- ❌ Skipar TASK_CLOSEOUT report

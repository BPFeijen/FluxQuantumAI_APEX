# TASK: APLICAR WINDOWS DATA-DRIVEN — EVENT_CONFIG (economic_calendar.py)

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Pre-requisito:** FASE I + FASE II + Discovery completo. Sistema Running, 4 features activas.
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\APPLY_WINDOWS_REPORT.md`

---

## DECISÕES TOMADAS (não discutir, aplicar)

Baseadas em FASE II (24,073min × 200 events) + Discovery (confirmou que thresholds estão hardcoded, não no yaml).

### Análise que levou às decisões

**FASE II findings:**
- HIGH events PRE_5: **1.43× baseline** vol (d=0.35, p<0.0001) → 5min pre é suficiente
- HIGH events PRE_30: 1.09× (marginal) → 30min pre é **excessivo**
- HIGH events POST_5/POST_30: 0.59-0.61× (**compression** — mercado calma) → 30min post é **contra-produtivo**
- MEDIUM events DURING: 2.20× (d=0.49) → BLOCK durante é justificado
- LOW: zero efeito → ignorar

**Decisão conservadora post=3min** (não 0s) para dar margem a slippage real de execução.

**Decisão NÃO tocar SCORE_BLOCK_ENTRY/EXIT_ALL** nem `_ACTION_TABLE`: a lógica de gate está correcta, apenas as janelas temporais é que eram excessivas.

### Edits target

**Único ficheiro a modificar:** `C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py`
**Único bloco a modificar:** `EVENT_CONFIG` dict (aproximadamente linhas 52-132)

### Valores a aplicar

| Event type | pause_before ANTES | pause_before NOVO | pause_after ANTES | pause_after NOVO | Justificação |
|---|---|---|---|---|---|
| FOMC | 30 | **5** | 60 | **3** | HIGH tier — FASE II evidence |
| NFP | 30 | **5** | 30 | **3** | HIGH tier |
| CPI | 30 | **5** | 15 | **3** | HIGH tier |
| GDP | 30 | **5** | 15 | **3** | HIGH tier |
| PPI | 15 | **5** | 15 | **3** | HIGH tier |
| FED_SPEECH | 15 | **5** | 30 | **3** | HIGH tier |
| ECB | 30 | **5** | 30 | **3** | US-only filter remove but keep pattern consistent |
| BOJ | 15 | **5** | 15 | **3** | MEDIUM tier — consistent |
| UNEMPLOYMENT | 15 | **5** | 10 | **3** | MEDIUM tier |
| ISM | 15 | **5** | 10 | **3** | MEDIUM tier |
| RETAIL_SALES | 15 | **5** | 10 | **3** | MEDIUM tier |
| DEFAULT (fallback) | 15 | **5** | 10 | **3** | consistent |

**Todos os event types:** pause_before=5, pause_after=3.

**NÃO alterar:**
- `impact` field (mantém CRITICAL/HIGH/MEDIUM por tipo)
- `keywords` lists (classification keyword matching intacto)
- Ordem dos eventos no dict

---

## REGRA CRÍTICA — DISCOVERY ANTES DE EDITAR

Antes de qualquer `str_replace`, **mostrar o EVENT_CONFIG actual completo** para confirmar:
1. Linha exacta de início e fim do bloco
2. Formato exacto das entradas (indentação, aspas, commas)
3. Confirmar que os valores antes do edit batem com a tabela acima

Se algum valor actual **não bater** com a coluna "ANTES" da tabela, **PARAR e reportar**.

---

## PASSO 1 — Discovery EVENT_CONFIG actual

```powershell
$path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
Write-Host "=== EVENT_CONFIG current state ==="
Get-Content $path | Select-Object -Skip 50 -First 85
```

**Reportar output completo.** Comparar com tabela "ANTES" acima.

---

## PASSO 2 — Backup

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sprint_dir = "C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908"
$backup_dir = "$sprint_dir\backup_apply_windows_$timestamp"
New-Item -Path $backup_dir -ItemType Directory -Force | Out-Null

$src = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
Copy-Item $src "$backup_dir\economic_calendar.py" -Force
$hash_pre = (Get-FileHash $src -Algorithm MD5).Hash
Write-Host "Backup: $backup_dir\economic_calendar.py"
Write-Host "Pre-fix hash: $hash_pre"

# Save manifest
@{
    "economic_calendar.py" = @{
        "pre_hash" = $hash_pre
        "backup_path" = "$backup_dir\economic_calendar.py"
        "timestamp" = $timestamp
    }
} | ConvertTo-Json | Set-Content "$backup_dir\MANIFEST.json"
```

---

## PASSO 3 — Aplicar edits via str_replace

**Usar str_replace com contexto rico (chave + valor actual) para segurança.**

Para cada event type, substituir apenas os valores `pause_before` e `pause_after`, preservando `impact` e `keywords`.

### 3.1 FOMC

```python
# OLD:
"FOMC": {"impact": "CRITICAL", "pause_before": 30, "pause_after": 60,
# NEW:
"FOMC": {"impact": "CRITICAL", "pause_before": 5, "pause_after": 3,
```

### 3.2 NFP

```python
# OLD:
"NFP": {"impact": "CRITICAL", "pause_before": 30, "pause_after": 30,
# NEW:
"NFP": {"impact": "CRITICAL", "pause_before": 5, "pause_after": 3,
```

### 3.3 CPI

```python
# OLD:
"CPI": {"impact": "HIGH", "pause_before": 30, "pause_after": 15,
# NEW:
"CPI": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
```

### 3.4 GDP

```python
# OLD:
"GDP": {"impact": "HIGH", "pause_before": 30, "pause_after": 15,
# NEW:
"GDP": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
```

### 3.5 PPI

```python
# OLD:
"PPI": {"impact": "HIGH", "pause_before": 15, "pause_after": 15,
# NEW:
"PPI": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
```

### 3.6 FED_SPEECH

```python
# OLD:
"FED_SPEECH": {"impact": "HIGH", "pause_before": 15, "pause_after": 30,
# NEW:
"FED_SPEECH": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
```

### 3.7 ECB

```python
# OLD:
"ECB": {"impact": "HIGH", "pause_before": 30, "pause_after": 30,
# NEW:
"ECB": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
```

### 3.8 BOJ

```python
# OLD:
"BOJ": {"impact": "MEDIUM", "pause_before": 15, "pause_after": 15,
# NEW:
"BOJ": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
```

### 3.9 UNEMPLOYMENT

```python
# OLD:
"UNEMPLOYMENT": {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10,
# NEW:
"UNEMPLOYMENT": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
```

### 3.10 ISM

```python
# OLD:
"ISM": {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10,
# NEW:
"ISM": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
```

### 3.11 RETAIL_SALES

```python
# OLD:
"RETAIL_SALES": {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10,
# NEW:
"RETAIL_SALES": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
```

### 3.12 DEFAULT (fallback)

Se o fallback aparece explicitamente no dict (tipo `"DEFAULT": {...}` ou similar):

```python
# OLD (se existir):
"DEFAULT": {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10,
# NEW:
"DEFAULT": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
```

**Se o default não for entrada do dict mas sim hardcoded noutro sítio (ex: `_classify_event` ou `_IMPORTANCE_DEFAULTS`):** reportar e decidir. Provavelmente também precisa ser 5/3.

### 3.13 _IMPORTANCE_DEFAULTS (verificar)

```python
# OLD:
_IMPORTANCE_DEFAULTS = {
    3: {"impact": "HIGH",   "pause_before": 30, "pause_after": 15},
    2: {"impact": "MEDIUM", "pause_before": 15, "pause_after": 10},
    1: {"impact": "LOW",    "pause_before": 5,  "pause_after": 5},
}
# NEW:
_IMPORTANCE_DEFAULTS = {
    3: {"impact": "HIGH",   "pause_before": 5, "pause_after": 3},
    2: {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3},
    1: {"impact": "LOW",    "pause_before": 5, "pause_after": 3},
}
```

**NOTA:** Discovery reportou que keys 3 e 1 são dead (só 2 é referenciado). Mas actualizar todas por consistência — zero custo, evita surpresa futura.

---

## PASSO 4 — py_compile validation

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m py_compile "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile FAILED" }
Write-Host "py_compile: OK"
```

---

## PASSO 5 — Import probe (valores novos carregam correctamente)

```python
import sys
sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_News")

from economic_calendar import EVENT_CONFIG

# Verify all event types have pause_before=5, pause_after=3
expected_events = ["FOMC", "NFP", "CPI", "GDP", "PPI", "FED_SPEECH",
                   "ECB", "BOJ", "UNEMPLOYMENT", "ISM", "RETAIL_SALES"]

errors = []
for event_type in expected_events:
    if event_type not in EVENT_CONFIG:
        errors.append(f"MISSING: {event_type}")
        continue
    cfg = EVENT_CONFIG[event_type]
    if cfg.get("pause_before") != 5:
        errors.append(f"{event_type}: pause_before={cfg.get('pause_before')}, expected 5")
    if cfg.get("pause_after") != 3:
        errors.append(f"{event_type}: pause_after={cfg.get('pause_after')}, expected 3")

if errors:
    print("VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    raise SystemExit(1)
else:
    print("VALIDATION OK: all 11 event types have pause_before=5, pause_after=3")

# Also test full stack
from apex_news_gate import news_gate
result = news_gate.check()
print(f"news_gate.check() returned: score={result.score}, action={result.action}")
```

**Se validation falha:** NÃO restart. Reportar exacto quais valores estão errados.

---

## PASSO 6 — Restart service + observar 10 min

```powershell
$stdout = "C:\FluxQuantumAI\logs\service_stdout.log"
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"
$init_stdout = if (Test-Path $stdout) { (Get-Item $stdout).Length } else { 0 }
$init_stderr = if (Test-Path $stderr) { (Get-Item $stderr).Length } else { 0 }

# Stop service
Stop-Service FluxQuantumAPEX -Force
Start-Sleep 3

# Verify capture processes intact (CRITICAL)
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
$tracebacks = ($new_all | Select-String "Traceback" -AllMatches).Matches.Count

Write-Host "Post-restart (60s): tracebacks=$tracebacks"
if ($tracebacks -gt 0) {
    throw "STARTUP TRACEBACKS — review and rollback"
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

**Apenas disparar se Passos 4, 5 ou 6 falham.**

```powershell
Stop-Service FluxQuantumAPEX -Force -ErrorAction SilentlyContinue
Copy-Item "$backup_dir\economic_calendar.py" `
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py" -Force
Start-Service FluxQuantumAPEX
Write-Host "ROLLBACK COMPLETE"
```

---

## PASSO 8 — TASK_CLOSEOUT Report

Criar `$sprint_dir\APPLY_WINDOWS_REPORT.md`:

```markdown
# APPLY WINDOWS DATA-DRIVEN — TASK_CLOSEOUT

**Timestamp:** <UTC>
**Duration:** X min
**Status:** ✅ SUCCESS / ❌ FAILED / ⚠ ROLLED BACK

## Backup
- Location: <path>
- File backed up: economic_calendar.py
- Pre-fix hash: X
- Post-fix hash: Y

## Changes applied
| Event type | pause_before | pause_after | Status |
|---|---|---|---|
| FOMC | 30→5 | 60→3 | ✅ |
| NFP | 30→5 | 30→3 | ✅ |
| CPI | 30→5 | 15→3 | ✅ |
| GDP | 30→5 | 15→3 | ✅ |
| PPI | 15→5 | 15→3 | ✅ |
| FED_SPEECH | 15→5 | 30→3 | ✅ |
| ECB | 30→5 | 30→3 | ✅ |
| BOJ | 15→5 | 15→3 | ✅ |
| UNEMPLOYMENT | 15→5 | 10→3 | ✅ |
| ISM | 15→5 | 10→3 | ✅ |
| RETAIL_SALES | 15→5 | 10→3 | ✅ |
| DEFAULT (if exists) | 15→5 | 10→3 | ✅ |
| _IMPORTANCE_DEFAULTS (3 keys) | normalized | normalized | ✅ |

## Validation
- py_compile: ✅ OK
- Import probe: ✅ 11/11 event types verified
- news_gate.check(): ✅ returned valid NewsGateResult

## Service restart
- Stop: success
- Capture processes: 3/3 intact (12332, 8248, 2512)
- Start: success, new PID: X
- Post-restart tracebacks: 0

## 10-min observation
- Tracebacks: 0
- Errors: 0
- Service: Running
- [GATE] Guardrail logs observed: N
- Any news events during observation: [list or none]

## Files modified
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py

## Files NOT modified
- apex_news_gate.py (SCORE_BLOCK_ENTRY/EXIT_ALL intactos — decisão Claude)
- risk_calculator.py (_ACTION_TABLE intacto — decisão Claude)
- news_config.yaml (dead config, ignorado)
- country_relevance_gold.json (unchanged desde FASE I)

## Rollback
- Triggered: yes/no
- Reason (if yes): ...

## Next steps
- Barbara + Claude audit de economic_calendar.py modified (comparar com backup)
- Observação empírica quando mercado abrir
- Primeiro HIGH event (NFP/CPI/FOMC) → confirmar Telegram messages BLOCK/CAUTION às 5min antes, liberação 3min depois
- Se evidência empírica contradizer, ajustar

## Historical context (para referência futura)

**Antes FASE II:**
- FOMC bloqueava 30min pre + 60min post = 90min total blackout
- NFP/CPI: 30min pre + 15-30min post = 45-60min blackout
- Sistema potencialmente perdia oportunidades em post-event compression period

**Depois FASE II:**
- Todos eventos: 5min pre + 3min post = 8min total blackout
- ~85-90% redução no tempo bloqueado por evento
- Fundamentação: HIGH DURING 4.49× baseline (genuíno perigo 0-1min), HIGH PRE_5 1.43× (moderado), POST compression (mercado calma)
- Conservador 3min post (vs 0s data-driven puro) para margem de slippage execution
```

---

## COMUNICAÇÃO FINAL

**Success:**
```
WINDOWS APPLIED — economic_calendar.py EVENT_CONFIG
All 11 event types: pause_before 5min, pause_after 3min
Total blackout reduced ~85-90% vs defaults anteriores
Service: Running, 10min clean
Capture processes: 3/3 intact
Report: APPLY_WINDOWS_REPORT.md

Aguardo Claude audit + observação empírica mercado.
```

**Partial/Failed:**
```
WINDOWS [PARTIAL/FAILED]
Stopped at Passo N: [reason]
State: [what was done vs not]
Rollback: [triggered yes/no]
Report: [path]
```

---

## PROIBIDO

- ❌ Editar apex_news_gate.py (SCORE constants)
- ❌ Editar risk_calculator.py (_ACTION_TABLE, THRESHOLD_*)
- ❌ Editar news_config.yaml (dead config)
- ❌ Tocar capture processes (PIDs 12332, 8248, 2512)
- ❌ Alterar `impact` field ou `keywords` dentro de EVENT_CONFIG
- ❌ Skipar import probe ou 10min observation
- ❌ Rollback automático sem log claro
- ❌ Skipar TASK_CLOSEOUT report

# TASK: ROLLBACK REFINADO FINAL — Windows data-driven completas (pre + post)

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Deadline:** ANTES mercado abrir (Sunday 22:00 UTC)
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\ROLLBACK_REFINED_FINAL_REPORT.md`
**Supersedes:** CLAUDECODE_Rollback_B_Windows.md + CLAUDECODE_Rollback_Refined_Windows.md

---

## CONTEXTO

Análises sucessivas (L2 backtest + MAE pre-event por event type) identificaram valores data-driven por evento:

**Pre-event MAE analysis (DEEP_L2 extended):**
- CPI/NFP/UNEMPLOYMENT/GDP/FOMC/FED_SPEECH: MAE em T-5min é baixo (2-4 bps) → **pause_before=5 é suficiente**
- **PPI é outlier:** MAE continua elevado (8-11 bps) até T-5, só colapsa em T-3 → há drift pré-release (leak/antecipação institucional) → **pause_before=10 é necessário**
- ISM / RETAIL_SALES / OTHER: mild events → **pause_before=5 suficiente**

**Post-event analysis (DEEP_L2):**
- FOMC persistent spike até T+60 → 60min
- GDP/PPI prolonged spike → 30min
- NFP/CPI/UNEMPLOYMENT sweet spot T+15 → 15min
- FED_SPEECH rentável (MFE/MAE 13× T+15) → manter 3min
- ISM/RETAIL_SALES mild → 10min
- ECB/BOJ → consistência HIGH/MEDIUM

---

## VALORES FINAIS A APLICAR

**Único ficheiro modificado:** `C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py`
**Únicos blocos:** `EVENT_CONFIG` + `_IMPORTANCE_DEFAULTS`

### EVENT_CONFIG target values

| Event type | pause_before | pause_after | Data-driven rationale |
|---|---|---|---|
| **FOMC** | **5** | **60** | Pre-MAE T-5=3bps OK; post persistent T+60 |
| **NFP** | **5** | **15** | Pre-MAE T-5=4bps OK; sweet spot T+15 |
| **CPI** | **5** | **15** | Pre-MAE T-5=2bps OK; sweet spot T+15 |
| **UNEMPLOYMENT** | **5** | **15** | Pre-MAE T-5=4bps OK; sweet spot T+15 |
| **GDP** | **5** | **30** | Pre-MAE T-5=2bps OK; prolonged post spike |
| **PPI** ⚠ | **10** | **30** | **Pre-MAE T-5=8bps ELEVADO — drift/leak; extended pre window** |
| **FED_SPEECH** | **5** | **3** | Pre-MAE T-5=3bps OK; event rentável post |
| **ECB** | **5** | **15** | HIGH tier consistência (US-only filter remove) |
| **BOJ** | **5** | **10** | MEDIUM baseline |
| **ISM** | **5** | **10** | Mild event |
| **RETAIL_SALES** | **5** | **10** | Mild-moderate |

### _IMPORTANCE_DEFAULTS target values

| key | pause_before | pause_after |
|---|---|---|
| 3 (HIGH) | 5 | 15 |
| 2 (MEDIUM) | 5 | 10 |
| 1 (LOW) | 5 | 10 |

**NÃO alterar:** `impact` field, `keywords` lists, estrutura do dict.

---

## REGRA CRÍTICA — DISCOVERY ANTES DE EDITAR

Estado actual esperado: todos `pause_before=5, pause_after=3` (pós Apply Windows 5/3).

**Confirmar antes de editar. Se algum valor não for 5/3, PARAR e reportar.**

---

## PASSO 1 — Discovery estado actual

```powershell
$path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
Write-Host "=== EVENT_CONFIG current (post Apply Windows 5/3) ==="
Get-Content $path | Select-Object -Skip 50 -First 85

Write-Host "=== _IMPORTANCE_DEFAULTS current ==="
Select-String -Path $path -Pattern "_IMPORTANCE_DEFAULTS" -Context 0,8
```

**Confirmar:** todos valores `pause_before=5, pause_after=3`. Se não, abort + reportar.

---

## PASSO 2 — Backup

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sprint_dir = "C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908"
$backup_dir = "$sprint_dir\backup_rollback_refined_final_$timestamp"
New-Item -Path $backup_dir -ItemType Directory -Force | Out-Null

$src = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
Copy-Item $src "$backup_dir\economic_calendar.py" -Force
$hash_pre = (Get-FileHash $src -Algorithm MD5).Hash
Write-Host "Backup: $backup_dir\economic_calendar.py"
Write-Host "Pre-rollback hash: $hash_pre"

@{
    "economic_calendar.py" = @{
        "pre_hash" = $hash_pre
        "backup_path" = "$backup_dir\economic_calendar.py"
        "timestamp" = $timestamp
        "action" = "rollback_refined_FINAL_pre_and_post_data_driven"
    }
} | ConvertTo-Json | Set-Content "$backup_dir\MANIFEST.json"
```

---

## PASSO 3 — Aplicar edits (pre + post data-driven)

**Usar str_replace com contexto rico.**

### 3.1 FOMC (CRITICAL — 5/60)

```python
# OLD:
"FOMC": {"impact": "CRITICAL", "pause_before": 5, "pause_after": 3,
# NEW:
"FOMC": {"impact": "CRITICAL", "pause_before": 5, "pause_after": 60,
```

### 3.2 NFP (CRITICAL — 5/15)

```python
# OLD:
"NFP": {"impact": "CRITICAL", "pause_before": 5, "pause_after": 3,
# NEW:
"NFP": {"impact": "CRITICAL", "pause_before": 5, "pause_after": 15,
```

### 3.3 CPI (HIGH — 5/15)

```python
# OLD:
"CPI": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
# NEW:
"CPI": {"impact": "HIGH", "pause_before": 5, "pause_after": 15,
```

### 3.4 GDP (HIGH — 5/30)

```python
# OLD:
"GDP": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
# NEW:
"GDP": {"impact": "HIGH", "pause_before": 5, "pause_after": 30,
```

### 3.5 PPI (HIGH — **10/30** — ATENÇÃO pause_before diferente)

```python
# OLD:
"PPI": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
# NEW:
"PPI": {"impact": "HIGH", "pause_before": 10, "pause_after": 30,
```

**NOTA CRÍTICA:** PPI é o único event com `pause_before=10` (não 5). Confirmar edit correcto antes de py_compile.

### 3.6 FED_SPEECH (HIGH — 5/3)

```python
# OLD:
"FED_SPEECH": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
# NEW:
"FED_SPEECH": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
```

**NOTA:** FED_SPEECH já está em `5/3`. Este edit é **no-op intencional** para auditoria clara.

### 3.7 ECB (HIGH — 5/15)

```python
# OLD:
"ECB": {"impact": "HIGH", "pause_before": 5, "pause_after": 3,
# NEW:
"ECB": {"impact": "HIGH", "pause_before": 5, "pause_after": 15,
```

### 3.8 BOJ (MEDIUM — 5/10)

```python
# OLD:
"BOJ": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
# NEW:
"BOJ": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 10,
```

### 3.9 UNEMPLOYMENT (MEDIUM — 5/15)

```python
# OLD:
"UNEMPLOYMENT": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
# NEW:
"UNEMPLOYMENT": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 15,
```

### 3.10 ISM (MEDIUM — 5/10)

```python
# OLD:
"ISM": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
# NEW:
"ISM": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 10,
```

### 3.11 RETAIL_SALES (MEDIUM — 5/10)

```python
# OLD:
"RETAIL_SALES": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3,
# NEW:
"RETAIL_SALES": {"impact": "MEDIUM", "pause_before": 5, "pause_after": 10,
```

### 3.12 _IMPORTANCE_DEFAULTS

```python
# OLD:
_IMPORTANCE_DEFAULTS = {
    3: {"impact": "HIGH",   "pause_before": 5, "pause_after": 3},
    2: {"impact": "MEDIUM", "pause_before": 5, "pause_after": 3},
    1: {"impact": "LOW",    "pause_before": 5, "pause_after": 3},
}
# NEW:
_IMPORTANCE_DEFAULTS = {
    3: {"impact": "HIGH",   "pause_before": 5, "pause_after": 15},
    2: {"impact": "MEDIUM", "pause_before": 5, "pause_after": 10},
    1: {"impact": "LOW",    "pause_before": 5, "pause_after": 10},
}
```

---

## PASSO 4 — py_compile validation

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m py_compile "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile FAILED" }
Write-Host "py_compile: OK"
```

---

## PASSO 5 — Import probe (valores correctos INCLUINDO PPI 10 pre)

```python
import sys
sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_News")

from economic_calendar import EVENT_CONFIG, _IMPORTANCE_DEFAULTS

expected = {
    "FOMC":         (5, 60),
    "NFP":          (5, 15),
    "CPI":          (5, 15),
    "GDP":          (5, 30),
    "PPI":          (10, 30),   # <-- CRITICAL: pre=10, único outlier
    "FED_SPEECH":   (5, 3),
    "ECB":          (5, 15),
    "BOJ":          (5, 10),
    "UNEMPLOYMENT": (5, 15),
    "ISM":          (5, 10),
    "RETAIL_SALES": (5, 10),
}

errors = []
for event_type, (exp_before, exp_after) in expected.items():
    if event_type not in EVENT_CONFIG:
        errors.append(f"MISSING: {event_type}")
        continue
    cfg = EVENT_CONFIG[event_type]
    actual_before = cfg.get("pause_before")
    actual_after = cfg.get("pause_after")
    if actual_before != exp_before:
        errors.append(f"{event_type}: pause_before={actual_before}, expected {exp_before}")
    if actual_after != exp_after:
        errors.append(f"{event_type}: pause_after={actual_after}, expected {exp_after}")

# Defaults
expected_defaults = {3: (5, 15), 2: (5, 10), 1: (5, 10)}
for key, (eb, ea) in expected_defaults.items():
    d = _IMPORTANCE_DEFAULTS.get(key, {})
    if d.get("pause_before") != eb:
        errors.append(f"_IMPORTANCE_DEFAULTS[{key}]: pause_before={d.get('pause_before')}, expected {eb}")
    if d.get("pause_after") != ea:
        errors.append(f"_IMPORTANCE_DEFAULTS[{key}]: pause_after={d.get('pause_after')}, expected {ea}")

if errors:
    print("VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    raise SystemExit(1)
else:
    print("VALIDATION OK: 11 event types + 3 defaults correctly set (data-driven FINAL)")
    print(f"  PPI confirmed pause_before=10 (outlier)")
    print(f"  All other events confirmed pause_before=5")

# Full stack probe
from apex_news_gate import news_gate
result = news_gate.check()
print(f"news_gate.check(): score={result.score}, action={result.action}")
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

# CRITICAL — capture processes intact
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
    throw "STARTUP TRACEBACKS — review"
}

$svc_status = (Get-Service FluxQuantumAPEX).Status
Write-Host "Service status: $svc_status"

if ($svc_status -ne "Running") {
    throw "SERVICE NOT RUNNING"
}

# Weekend — skip 10min observation (sem market data para observar)
Write-Host "10min observation SKIPPED (weekend, market closed)"
```

---

## PASSO 7 — Rollback to previous state (SE NECESSÁRIO)

**Apenas disparar se Passos 4, 5 ou 6 falham.**

```powershell
Stop-Service FluxQuantumAPEX -Force -ErrorAction SilentlyContinue
Copy-Item "$backup_dir\economic_calendar.py" `
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py" -Force
Start-Service FluxQuantumAPEX
Write-Host "ROLLBACK COMPLETE (restored to 5/3 from earlier today)"
```

---

## PASSO 8 — TASK_CLOSEOUT Report

Criar `$sprint_dir\ROLLBACK_REFINED_FINAL_REPORT.md`:

```markdown
# ROLLBACK REFINADO FINAL — Windows data-driven completas — TASK_CLOSEOUT

**Timestamp:** <UTC>
**Duration:** X min
**Status:** ✅ SUCCESS / ❌ FAILED / ⚠ ROLLED BACK

## Context
Análise L2 profunda + MAE pre-event por event type identificou valores data-driven para pause_before E pause_after.

**Pre-event analysis key finding:** PPI é outlier — MAE pré-release persistente (8-11 bps até T-5), sugerindo drift/leak institucional. Outros eventos safe em T-5.

**Post-event analysis key findings:**
- FOMC persistent até T+60
- GDP/PPI prolonged até T+30
- NFP/CPI/UNEMPLOYMENT sweet spot T+15
- FED_SPEECH rentável (keep 3min)
- MEDIUM mild events 10min

## Backup
- Location: <path>
- Pre-rollback hash: X
- Post-rollback hash: Y

## Changes applied

### EVENT_CONFIG
| Event | Before | After | Rationale |
|---|---|---|---|
| FOMC | 5/3 | 5/60 | Persistent post-event spike |
| NFP | 5/3 | 5/15 | Sweet spot T+15 |
| CPI | 5/3 | 5/15 | Sweet spot T+15 |
| GDP | 5/3 | 5/30 | Prolonged post spike |
| **PPI** ⚠ | **5/3** | **10/30** | **Pre-leak detectado + prolonged post** |
| FED_SPEECH | 5/3 | 5/3 (no-op) | Event rentável |
| ECB | 5/3 | 5/15 | HIGH consistency |
| BOJ | 5/3 | 5/10 | MEDIUM baseline |
| UNEMPLOYMENT | 5/3 | 5/15 | Sweet spot T+15 |
| ISM | 5/3 | 5/10 | Mild event |
| RETAIL_SALES | 5/3 | 5/10 | Mild-moderate |

### _IMPORTANCE_DEFAULTS
| key | Before | After |
|---|---|---|
| 3 (HIGH) | 5/3 | 5/15 |
| 2 (MEDIUM) | 5/3 | 5/10 |
| 1 (LOW) | 5/3 | 5/10 |

## Validation
- py_compile: OK
- Import probe: 11/11 + 3/3 defaults verified (incluindo PPI pause_before=10)
- news_gate.check(): valid NewsGateResult

## Service restart
- Stop: success
- Capture processes: 3/3 intact (PIDs 12332, 8248, 2512)
- Start: success, new PID: X
- Post-restart tracebacks: 0
- Service status: Running
- 10min observation: SKIPPED (weekend)

## Files modified
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py

## Files NOT modified
- apex_news_gate.py
- risk_calculator.py
- news_config.yaml
- country_relevance_gold.json

## Final state journey

| Phase | FOMC | NFP | CPI | GDP | PPI | FED_SPEECH | ECB | BOJ | UNEMP | ISM | RETAIL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Original | 30/60 | 30/30 | 30/15 | 30/15 | 15/15 | 15/30 | 30/30 | 15/15 | 15/10 | 15/10 | 15/10 |
| Apply (5/3) | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 | 5/3 |
| **Final (CURRENT)** | **5/60** | **5/15** | **5/15** | **5/30** | **10/30** | **5/3** | **5/15** | **5/10** | **5/15** | **5/10** | **5/10** |

## Data-driven rationale full summary
- FOMC post 60min: POST_30_60 = 2.15× baseline, persistent MAE
- NFP/CPI/UNEMPLOYMENT 15min: sweet spot MFE/MAE ~1.3-2.3 T+15
- GDP/PPI 30min: prolonged post-event spike, MFE/MAE still 1.24-2.4 at T+30
- FED_SPEECH 3min: event rentável (MFE/MAE 13× T+15, +31bps upward)
- PPI pre=10min: pre-MAE 8-11 bps até T-5 (vs 2-4 bps outros events) — leak/drift institucional
- FOMC/NFP/CPI/UNEMPLOYMENT/GDP/FED_SPEECH pre=5min: MAE T-5 baixo (2-4 bps)
- MEDIUM events 10min: ISM/RETAIL_SALES/BOJ mild behavior

## Rollback to 5/3 state
- Triggered: NO
- Available via backup if needed

## Next steps
1. Audit Claude de economic_calendar.py modified (diff vs backup)
2. Monday market open — observar primeira NFP/CPI/FOMC/PPI:
   - Telegram BLOCK at T-5min (T-10min para PPI)
   - Release at T+15/30/60min per event type
3. Empirical validation window — após 2-3 semanas comparar:
   - PnL distribution pre vs post-fix
   - #trades blocked inadvertidamente
4. FASE III (dataset HighRisk) — sprint separado
5. Future enhancement: per-event-type schema native (current uses EVENT_CONFIG dict)
```

---

## COMUNICAÇÃO FINAL

**Success:**
```
ROLLBACK REFINADO FINAL COMPLETE
Windows aplicadas (pre/post diferenciadas data-driven):

FOMC:         5/60   (persistent post-event spike)
NFP:          5/15   (sweet spot)
CPI:          5/15   (sweet spot)
GDP:          5/30   (prolonged spike)
PPI:          10/30  (⚠ pre-leak detectado + prolonged post)
FED_SPEECH:   5/3    (event rentável, no-op)
ECB:          5/15   (HIGH consistency)
BOJ:          5/10   (MEDIUM baseline)
UNEMPLOYMENT: 5/15   (sweet spot)
ISM:          5/10   (mild)
RETAIL_SALES: 5/10   (mild)

_IMPORTANCE_DEFAULTS: 5/15, 5/10, 5/10

Service: Running, capture processes 3/3 intact
Report: ROLLBACK_REFINED_FINAL_REPORT.md

Aguardo Claude audit + mercado Monday open.
```

---

## PROIBIDO

- ❌ Alterar pause_before para eventos que NÃO sejam PPI (todos os outros ficam 5)
- ❌ Editar apex_news_gate.py ou risk_calculator.py
- ❌ Tocar capture processes
- ❌ Saltar discovery — confirmar estado 5/3 antes de editar
- ❌ Declarar success se import probe detectar PPI pause_before != 10
- ❌ Skipar TASK_CLOSEOUT report
- ❌ Editar yaml, JSON, ou qualquer outro config

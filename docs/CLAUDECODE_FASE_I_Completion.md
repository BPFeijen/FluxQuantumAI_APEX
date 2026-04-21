# TASK: FASE I COMPLETION — news_gate Approach A (12 edits + restart + report)

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Deadline:** Domingo antes mercado abrir (~22h UTC, ~12h restantes)
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_<timestamp>\FASE_I_REPORT.md`

---

## ESTADO ACTUAL

**Já executado com sucesso:**
- Backup 6 ficheiros (manifest com hashes)
- F1 path fixes aplicados (4 linhas em 4 ficheiros)
- F1 py_compile OK
- Probe: StatGuardrail ✅ / DefenseMode ✅ / V4 IcebergInference ✅

**Aplicado mas INCORRECTO (Approach B errada):**
- event_processor.py:95 → aponta para `APEX GOLD` parent
- event_processor.py:96 → `from APEX_News.apex_news_gate import news_gate`
- **Razão da falha:** `apex_news_gate.py` internamente já faz sys.path.insert + import top-level. Não resolve relative imports dentro do package.

**Pendente:**
- Revert Approach B em event_processor.py:95-96 (voltar ao estado original)
- Aplicar Approach A (12 edits relative→absolute em 5 ficheiros)
- Filtro US-only em `country_relevance_gold.json`
- Re-probe (4/4 obrigatório)
- Restart + observação 10min
- Report TASK_CLOSEOUT completo

---

## REGRA CRÍTICA — ZERO ASSUMPTIONS

**Não assumir estruturas de dados, colunas, formatos, schemas.** Sempre fazer **discovery READ-ONLY primeiro**, reportar o que encontrou, **só depois propor transformação**. Se algo não está claro, PARAR e reportar, não adivinhar.

---

## PASSO 1 — Revert Approach B em event_processor.py

### 1.1 event_processor.py:95 — voltar para APEX_News path

```python
# REVERT TO:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_News")))
```

### 1.2 event_processor.py:96 — voltar para import top-level

```python
# REVERT TO:
from apex_news_gate import news_gate as _news_gate
```

**py_compile:**
```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m py_compile "C:\FluxQuantumAI\live\event_processor.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile FAILED on event_processor.py revert" }
```

---

## PASSO 2 — Backup ADICIONAL dos 5 ficheiros APEX_News

```powershell
$backup_dir = "<reutilizar path do backup anterior>"

$apex_news_files = @(
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\alpha_vantage.py",
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py",
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_provider.py",
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\release_monitor.py",
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\risk_calculator.py"
)

foreach ($f in $apex_news_files) {
    if (-not (Test-Path $f)) { throw "MISSING: $f" }
    $leaf = Split-Path $f -Leaf
    Copy-Item $f "$backup_dir\$leaf" -Force
    $hash = (Get-FileHash $f -Algorithm MD5).Hash
    Write-Host "backup OK: $leaf ($hash)"
}
```

**Update MANIFEST.json** com os 5 ficheiros adicionais.

---

## PASSO 3 — Discovery `country_relevance_gold.json`

**Antes de editar, mostrar estrutura completa.**

```powershell
$json_path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\config\country_relevance_gold.json"
Get-Content $json_path -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 10
```

**REPORTAR estrutura exacta encontrada. NÃO editar ainda.** Esperar confirmação do formato antes de remover países não-US.

---

## PASSO 4 — Aplicar Approach A (12 relative→absolute imports)

Usar `str_replace` com contexto rico.

### 4.1 `alpha_vantage.py:13`
```python
# OLD:
from .time_utils import now_utc
# NEW:
from time_utils import now_utc
```

### 4.2-4.3 `economic_calendar.py:21-22`
```python
# OLD:
from .events import EconomicEvent
from .time_utils import parse_te_datetime, to_et
# NEW:
from events import EconomicEvent
from time_utils import parse_te_datetime, to_et
```

### 4.4-4.8 `news_provider.py:13-17`
```python
# OLD:
from .economic_calendar import TradingEconomicsCalendar
from .risk_calculator import NewsRiskCalculator
from .alpha_vantage import AlphaVantageProvider
from .events import EconomicEvent, NewsRiskLevel, NewsResult, NewsFeatures
from .time_utils import now_et, to_et, minutes_diff
# NEW:
from economic_calendar import TradingEconomicsCalendar
from risk_calculator import NewsRiskCalculator
from alpha_vantage import AlphaVantageProvider
from events import EconomicEvent, NewsRiskLevel, NewsResult, NewsFeatures
from time_utils import now_et, to_et, minutes_diff
```

### 4.9-4.10 `release_monitor.py:39-40`
```python
# OLD:
from .economic_calendar import TradingEconomicsCalendar
from .events import EconomicEvent
# NEW:
from economic_calendar import TradingEconomicsCalendar
from events import EconomicEvent
```

### 4.11-4.12 `risk_calculator.py:10-11`
```python
# OLD:
from .events import EconomicEvent, NewsRiskLevel
from .time_utils import minutes_diff, to_et
# NEW:
from events import EconomicEvent, NewsRiskLevel
from time_utils import minutes_diff, to_et
```

**Após CADA edit, py_compile:**
```powershell
& $py -m py_compile "<file>"
if ($LASTEXITCODE -ne 0) { throw "py_compile FAILED on <file>" }
```

**Se QUALQUER py_compile falha → parar, reportar, NÃO rollback ainda (esperar Claude analisar).**

---

## PASSO 5 — Verificar relative imports residuais

```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_News" -Recurse -Filter "*.py" |
    Select-String -Pattern "^from \.\w" |
    Format-Table Path, LineNumber, Line -Wrap
```

**Se aparecerem MAIS relative imports além dos 12 já fixed:** STOP + reportar. Não continuar adicionando edits sem aprovação.

---

## PASSO 6 — Filtro US-only em country_relevance_gold.json

**Só executar DEPOIS de Passo 3 (discovery) estar completo e estrutura ter sido confirmada.**

Se estrutura permitir filtro trivial:
- Preservar entry US com todos os seus pesos/configs
- Remover outras entries (China, EU, UK, JP, CH, AU, CA, IN)
- Manter estrutura global válida JSON

**Se estrutura complexa:** PARAR e pedir orientação antes de editar.

**Validar JSON após edit:**
```powershell
try {
    Get-Content $json_path -Raw | ConvertFrom-Json | Out-Null
    Write-Host "JSON valid after edit"
} catch {
    throw "JSON INVALID after edit: $_"
}
```

---

## PASSO 7 — Re-probe TODAS as 4 features

```python
import sys, traceback
sys.path.insert(0, r"C:\FluxQuantumAI")
results = {}

# 1. StatGuardrail
try:
    from grenadier_guardrail import get_guardrail_status, update_guardrail
    results["StatGuardrail"] = "OK"
except Exception as e:
    results["StatGuardrail"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

# 2. DefenseMode
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")
    from inference.anomaly_scorer import GrenadierDefenseMode
    dm = GrenadierDefenseMode()
    results["DefenseMode"] = "OK (instantiable)"
except Exception as e:
    results["DefenseMode"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

# 3. V4 IcebergInference
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg")
    from ats_iceberg_v1 import IcebergInference
    results["V4_Iceberg"] = "OK"
except Exception as e:
    results["V4_Iceberg"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

# 4. ApexNewsGate — via top-level import after Approach A
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_News")
    from apex_news_gate import news_gate
    results["ApexNewsGate"] = "OK"
except Exception as e:
    results["ApexNewsGate"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

print("=== PROBE RESULTS ===")
for k, v in results.items():
    print(f"  {k}: {v}")

failures = sum(1 for v in results.values() if "FAIL" in v)
sys.exit(failures)
```

**Critério OBRIGATÓRIO:** 4/4 OK.

**Se qualquer FAIL:** STOP. Reportar stack trace completo. Não prosseguir. Não restart. Não rollback automático (esperar análise Claude).

---

## PASSO 8 — Stop/Start service + observar 10min

**Só se Passo 7 = 4/4 OK.**

```powershell
$stdout = "C:\FluxQuantumAI\logs\service_stdout.log"
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"
$init_stdout = if (Test-Path $stdout) { (Get-Item $stdout).Length } else { 0 }
$init_stderr = if (Test-Path $stderr) { (Get-Item $stderr).Length } else { 0 }

Stop-Service FluxQuantumAPEX -Force
Start-Sleep 3

# Verificar capture processes intactos (crítico)
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

$checks = @{
    "StatGuardrail" = @{ ok = @("guardrail loaded", "grenadier_guardrail", "StatGuardrail"); fail = @("grenadier.*not available", "StatGuardrail.*FAILED") }
    "DefenseMode" = @{ ok = @("DefenseMode initialized", "GrenadierDefenseMode", "DEFENSE_MODE"); fail = @("DefenseMode.*not available", "GrenadierDefenseMode.*FAILED") }
    "V4_Iceberg" = @{ ok = @("IcebergInference", "V4 Iceberg", "ats_iceberg_v1"); fail = @("IcebergInference.*FAILED", "V4.*not available") }
    "ApexNewsGate" = @{ ok = @("ApexNewsGate loaded", "news_gate loaded"); fail = @("ApexNewsGate.*not available", "news_gate.*FAILED") }
}

$status = @{}
foreach ($feat in $checks.Keys) {
    $is_ok = $false; $is_fail = $false
    foreach ($m in $checks[$feat].ok) { if ($new_all -match $m) { $is_ok = $true } }
    foreach ($m in $checks[$feat].fail) { if ($new_all -match $m) { $is_fail = $true } }
    $status[$feat] = if ($is_fail) { "FAIL" } elseif ($is_ok) { "OK" } else { "UNKNOWN" }
    Write-Host "  $feat : $($status[$feat])"
}

$failed = ($status.Values | Where-Object { $_ -eq "FAIL" }).Count
if ($failed -gt 0) { throw "STARTUP MARKERS FAIL — consider rollback" }

Write-Host "=== Observing 10 min ==="
Start-Sleep 600

$final_stderr = Read-New $stderr $init_stderr
$tracebacks = ($final_stderr | Select-String "Traceback" -AllMatches).Matches.Count
$errors = ($final_stderr | Select-String "ERROR" -AllMatches).Matches.Count
$svc_status = (Get-Service FluxQuantumAPEX).Status

Write-Host "10min obs: tracebacks=$tracebacks errors=$errors service=$svc_status"

if ($tracebacks -gt 3 -or $svc_status -ne "Running") {
    throw "RUNTIME DEGRADED — review before declaring success"
}
```

---

## PASSO 9 — Rollback (SE NECESSÁRIO)

**Só disparar se Passo 7 ou Passo 8 falha critically E Claude aprova rollback.**

```powershell
Stop-Service FluxQuantumAPEX -Force -ErrorAction SilentlyContinue

$restore = @{
    "grenadier_guardrail.py" = "C:\FluxQuantumAI\grenadier_guardrail.py"
    "event_processor.py" = "C:\FluxQuantumAI\live\event_processor.py"
    "ats_live_gate.py" = "C:\FluxQuantumAI\ats_live_gate.py"
    "anomaly_scorer.py" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py"
    "news_config.yaml" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml"
    "country_relevance_gold.json" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\config\country_relevance_gold.json"
    "alpha_vantage.py" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\alpha_vantage.py"
    "economic_calendar.py" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
    "news_provider.py" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_provider.py"
    "release_monitor.py" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\release_monitor.py"
    "risk_calculator.py" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\risk_calculator.py"
}
foreach ($leaf in $restore.Keys) {
    Copy-Item "$backup_dir\$leaf" $restore[$leaf] -Force
}
Start-Service FluxQuantumAPEX
Write-Host "ROLLBACK COMPLETE (11 files restored)"
```

---

## PASSO 10 — TASK_CLOSEOUT Report (OBRIGATÓRIO)

**Criar `C:\FluxQuantumAI\sprints\news_highrisk_<timestamp>\FASE_I_REPORT.md`:**

```markdown
# FASE I — news_gate Approach A Completion — TASK_CLOSEOUT

**Timestamp:** <UTC end>
**Duration:** X min
**Status:** ✅ SUCCESS / ❌ FAILED / ⚠ ROLLED BACK

## Backup
- Location: <path>
- Files backed up: 11 total
- MANIFEST.json hashes: [list]

## Actions applied

### Revert F2 (Approach B)
- event_processor.py:95 reverted: ✅/❌
- event_processor.py:96 reverted: ✅/❌

### F1 confirmed (from previous session)
- grenadier_guardrail.py:33: ✅
- event_processor.py:122: ✅
- ats_live_gate.py:91: ✅
- anomaly_scorer.py:404: ✅

### Approach A — 12 edits applied
| # | File | Line | Status |
|---|---|---|---|
| 1 | alpha_vantage.py | 13 | ✅/❌ |
| 2-3 | economic_calendar.py | 21-22 | ✅/❌ |
| 4-8 | news_provider.py | 13-17 | ✅/❌ |
| 9-10 | release_monitor.py | 39-40 | ✅/❌ |
| 11-12 | risk_calculator.py | 10-11 | ✅/❌ |

### US-only filter
- country_relevance_gold.json structure before: [summary]
- country_relevance_gold.json structure after: [summary with US-only]
- Validation: JSON parseable after edit

### Residual relative imports check
- APEX_News search result: [count found]
- Any remaining beyond the 12 fixed? [yes/no]

## Import probe results
- StatGuardrail: OK/FAIL
- DefenseMode: OK/FAIL (+ instantiable?)
- V4 IcebergInference: OK/FAIL
- ApexNewsGate: OK/FAIL

## Service restart
- Stop: success
- Capture processes: PID 12332 / 8248 / 2512 — intact? [yes/no]
- Start: success
- Startup markers (60s): [4/4 OK table]

## 10-min runtime observation
- Tracebacks: N
- Errors: N
- Service status: Running
- Telegram messages observed: [any DEFENSE_MODE / GUARDRAIL / news detected?]

## Files modified — hashes
| File | Pre-hash | Post-hash |
|---|---|---|
| grenadier_guardrail.py | X | Y |
| event_processor.py | X | Y |
| ats_live_gate.py | X | Y |
| anomaly_scorer.py | X | Y |
| alpha_vantage.py | X | Y |
| economic_calendar.py | X | Y |
| news_provider.py | X | Y |
| release_monitor.py | X | Y |
| risk_calculator.py | X | Y |
| country_relevance_gold.json | X | Y |
| news_config.yaml | X | UNCHANGED |

## Rollback
- Triggered: yes/no
- Reason (if yes): ...

## Files for Claude audit
Lista de paths para Barbara enviar ao Claude para auditoria:
- C:\FluxQuantumAI\grenadier_guardrail.py
- C:\FluxQuantumAI\live\event_processor.py
- C:\FluxQuantumAI\ats_live_gate.py
- C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\alpha_vantage.py
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_provider.py
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\release_monitor.py
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\risk_calculator.py
- C:\FluxQuantumAPEX\APEX GOLD\APEX_News\config\country_relevance_gold.json

## Next steps
- Barbara + Claude audit dos ficheiros modificados
- Observação empírica durante mercado domingo noite
- FASE II (calibração data-driven) — sprint separado com discovery obrigatório
- FASE III (dataset HighRisk) — sprint separado após FASE II
```

---

## COMUNICAÇÃO FINAL

**Success:**
```
FASE I COMPLETE — news_gate Approach A
Features: 4/4 restored (StatGuardrail + DefenseMode + V4 + ApexNewsGate)
US-only filter: applied
Runtime 10min: clean
Service: Running
Report: C:\FluxQuantumAI\sprints\news_highrisk_<ts>\FASE_I_REPORT.md
Aguardo Claude audit dos ficheiros modificados.
```

**Partial/Failed:**
```
FASE I [PARTIAL/FAILED]
Stopped at Passo N: [reason]
State: [what was done vs not done]
Recommend: [rollback? further investigation? Claude decide]
Report: [path]
```

---

## PROIBIDO

- ❌ Tocar capture processes (PID 12332, 8248, 2512)
- ❌ Rollback automático sem aprovação Claude
- ❌ Declarar success se qualquer probe/marker FAIL
- ❌ Editar `country_relevance_gold.json` sem discovery primeiro
- ❌ Skipar py_compile após qualquer edit
- ❌ Skipar report TASK_CLOSEOUT (é obrigatório)
- ❌ Inventar valores default em configs — sempre discovery primeiro

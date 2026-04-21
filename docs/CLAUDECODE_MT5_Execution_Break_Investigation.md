# TASK: INVESTIGAÇÃO READ-ONLY — Porque MT5 execution quebrou 2026-04-17 19:54 UTC

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Deadline:** ANTES mercado abrir domingo noite (~22-23h UTC)
**Mode:** 100% READ-ONLY. Apenas investigar causa. Zero fixes nesta fase.
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\MT5_EXECUTION_BREAK_INVESTIGATION.md`

---

## CONTEXTO

Report anterior (MT5_EA_BACKTEST_STATUS) descobriu:
- **531 GO signals** com `NOT_ATTEMPTED` desde Apr 17 19:54 UTC
- **254 attempts** em janela 30min (Apr 17 19:24-19:54) — todos FAILED
- RoboForex retornou: `BROKER_DISCONNECTED — MT5 not connected`
- Hantec retornou: `All legs failed`
- Último trade bem-sucedido: **Apr 10** (trades.csv)
- 3 MT5 terminals a correr (Hantec × 2, RoboForex × 1)
- APEX live está a correr: `run_live.py --execute --broker roboforex --lot_size 0.05` (PID 9552)

**Pergunta central:** porquê quebrou? O que mudou entre Apr 10 (último trade OK) e Apr 17 19:54 (última attempt antes de dois dias de `NOT_ATTEMPTED`)?

---

## REGRA CRÍTICA — ZERO EDITS, ZERO FIXES

- Apenas leitura, inspecção, correlação de timestamps
- Não restart MT5 terminals
- Não restart FluxQuantumAPEX
- Não tocar capture processes (12332, 8248, 2512)
- Não tentar `mt5.initialize()` fresh — pode mascarar evidência
- Não deploy, não modificar .env, não relogin
- **Se algo não estiver claro, reportar com incerteza explícita**

---

## PASSO 1 — Timeline completo Apr 10 → Apr 19

### 1.1 Git log (que mudanças foram deployadas entre estes timestamps?)

```powershell
cd C:\FluxQuantumAI
Write-Host "=== Git log Apr 10 → Apr 19 ==="
git log --all --since="2026-04-10" --until="2026-04-19" --pretty=format:"%h | %ai | %s" 2>&1

Write-Host ""
Write-Host "=== Files changed in that range ==="
git log --all --since="2026-04-10" --until="2026-04-19" --name-only --pretty=format:"%n=== %h %ai %s ===" 2>&1
```

### 1.2 File modification timestamps relevantes

```powershell
Write-Host "=== mt5_executor*.py modification history ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "mt5_executor*.py" -Recurse |
    Select-Object FullName, LastWriteTime, Length |
    Sort-Object LastWriteTime -Descending

Write-Host "=== run_live.py + event_processor.py + hedge_manager.py modification history ==="
$critical_files = @(
    "C:\FluxQuantumAI\run_live.py",
    "C:\FluxQuantumAI\live\event_processor.py",
    "C:\FluxQuantumAI\live\hedge_manager.py",
    "C:\FluxQuantumAI\live\position_monitor.py",
    "C:\FluxQuantumAI\live\mt5_history_watcher.py"
)
foreach ($f in $critical_files) {
    if (Test-Path $f) {
        $item = Get-Item $f
        Write-Host "$($item.FullName) | $($item.LastWriteTime) | $($item.Length) bytes"
    }
}

Write-Host "=== Scripts modified between Apr 14 and Apr 18 ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Where-Object { $_.LastWriteTime -gt [datetime]"2026-04-14" -and $_.LastWriteTime -lt [datetime]"2026-04-19" } |
    Select-Object FullName, LastWriteTime, Length |
    Sort-Object LastWriteTime
```

### 1.3 decision_log timeline crítico

```powershell
$log = "C:\FluxQuantumAI\logs\decision_log.jsonl"

Write-Host "=== Primeira linha log ==="
Get-Content $log -TotalCount 1

Write-Host "=== Primeira linha com execução bem-sucedida (ou primeira mention 'SUCCESS') ==="
Get-Content $log | Select-String "SUCCESS" | Select-Object -First 3

Write-Host "=== Transição do pattern MISSING → NOT_ATTEMPTED/FAILED ==="
# Queremos ver EXACTAMENTE o timestamp onde começa a haver tentativas de execução
Get-Content $log | Where-Object { $_ -match 'overall_state":"(NOT_ATTEMPTED|FAILED|SUCCESS)' } |
    Select-Object -First 5

Write-Host "=== Ultima FAILED ==="
Get-Content $log | Where-Object { $_ -match 'overall_state":"FAILED' } | Select-Object -Last 1

Write-Host "=== Primeira NOT_ATTEMPTED após FAILED pattern ==="
Get-Content $log | Where-Object { $_ -match 'overall_state":"NOT_ATTEMPTED' } | Select-Object -First 1

Write-Host "=== Últimas 3 linhas do log ==="
Get-Content $log -Tail 3
```

### 1.4 Service stderr/stdout para mesmos timestamps

```powershell
$stdout = "C:\FluxQuantumAI\logs\service_stdout.log"
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"

Write-Host "=== stderr eventos MT5/broker/mt5_executor ==="
Get-Content $stderr | Select-String -Pattern "MT5|mt5\.|broker|initialize|login|terminal|connected|segfault" |
    Select-Object -First 30

Write-Host "=== stderr eventos around Apr 17 19:24-19:54 (janela falhas) ==="
# Tentar encontrar entries nesse período
Get-Content $stderr | Select-String -Pattern "2026-04-17.19:" | Select-Object -First 50

Write-Host "=== Últimas 100 linhas stderr ==="
Get-Content $stderr -Tail 100
```

---

## PASSO 2 — Estado actual dos MT5 terminals

### 2.1 Terminals running — mas estão logados?

```powershell
Write-Host "=== MT5 terminals running ==="
Get-Process | Where-Object { $_.ProcessName -eq "terminal64" } |
    Select-Object Id, ProcessName, StartTime, MainWindowTitle, Path

Write-Host "=== Cada terminal tem uma janela activa? ==="
# MainWindowTitle tipicamente inclui broker + account se logado
# Exemplo: "FluxFoxLabs (RoboForex) - [Real, USD]"
```

### 2.2 Log files dentro de cada terminal MT5

MT5 mantém logs dentro de `C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\<hash>\logs\`

```powershell
Write-Host "=== MT5 terminal log directories ==="
Get-ChildItem "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal" -Directory |
    ForEach-Object {
        $logs_dir = "$($_.FullName)\logs"
        if (Test-Path $logs_dir) {
            $latest = Get-ChildItem $logs_dir -Filter "*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            Write-Host "$($_.Name): latest log $($latest.Name) at $($latest.LastWriteTime)"
        }
    }
```

### 2.3 Ler log mais recente de cada terminal

```powershell
# Para cada terminal, ler últimas 30 linhas do log mais recente
$terminal_dirs = Get-ChildItem "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal" -Directory
foreach ($td in $terminal_dirs) {
    $logs_dir = "$($td.FullName)\logs"
    if (Test-Path $logs_dir) {
        $latest = Get-ChildItem $logs_dir -Filter "*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            Write-Host ""
            Write-Host "=== Terminal $($td.Name) — $($latest.Name) ==="
            Get-Content $latest.FullName -Tail 30
        }
    }
}
```

### 2.4 Terminal journals for key dates

MT5 Experts log (separate from main log):
```powershell
$terminal_dirs = Get-ChildItem "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal" -Directory
foreach ($td in $terminal_dirs) {
    $experts_logs = "$($td.FullName)\MQL5\Logs"
    if (Test-Path $experts_logs) {
        Write-Host ""
        Write-Host "=== Experts journal $($td.Name) — últimos 2 ficheiros ==="
        Get-ChildItem $experts_logs -Filter "*.log" |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 2 -ExpandProperty FullName |
            ForEach-Object {
                Write-Host "  File: $_"
                Get-Content $_ -Tail 20
            }
    }
}
```

---

## PASSO 3 — Configuração conexão MT5 (.env, credenciais, paths)

### 3.1 .env file status

```powershell
Write-Host "=== .env search ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter ".env*" -Force -Recurse -ErrorAction SilentlyContinue |
    Select-Object FullName, LastWriteTime, Length

# DO NOT print content (credentials). Only metadata.
```

### 3.2 MT5 login paths no código

```powershell
Write-Host "=== mt5_executor.py completo (read-only) ==="
$mt5_exec = "C:\FluxQuantumAI\mt5_executor.py"
if (Test-Path $mt5_exec) {
    Get-Content $mt5_exec
}
```

Analisar:
- Como `mt5.initialize()` é chamado?
- Como credenciais são passadas?
- Retry logic? Error handling?
- Session 0 workaround presente?

### 3.3 mt5_executor_hantec.py também

```powershell
$mt5_exec_hantec = "C:\FluxQuantumAI\mt5_executor_hantec.py"
if (Test-Path $mt5_exec_hantec) {
    Write-Host "=== mt5_executor_hantec.py ==="
    Get-Content $mt5_exec_hantec
}
```

### 3.4 Settings relevantes

```powershell
Write-Host "=== settings.json broker section ==="
$settings = "C:\FluxQuantumAI\config\settings.json"
if (Test-Path $settings) {
    $content = Get-Content $settings -Raw | ConvertFrom-Json
    # Extrair só secção broker/mt5 (não dump credentials)
    if ($content.broker) { $content.broker | ConvertTo-Json -Depth 5 }
    if ($content.mt5) { $content.mt5 | ConvertTo-Json -Depth 5 }
    if ($content.execution) { $content.execution | ConvertTo-Json -Depth 5 }
}
```

---

## PASSO 4 — Session 0 / Windows Service hypothesis

### 4.1 Session ID do serviço FluxQuantumAPEX

```powershell
Write-Host "=== Service session analysis ==="
Get-WmiObject Win32_Service -Filter "Name='FluxQuantumAPEX'" |
    Select-Object Name, State, ProcessId, StartMode

$svc_pid = (Get-WmiObject Win32_Service -Filter "Name='FluxQuantumAPEX'").ProcessId
Write-Host "Service PID: $svc_pid"

# Python subprocess PID (run_live.py)
Write-Host ""
Write-Host "=== Python subprocess PID 9552 details ==="
Get-Process -Id 9552 -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, SessionId, StartTime, CommandLine
```

**Session IDs a reportar:**
- Service FluxQuantumAPEX SessionId
- Python run_live.py SessionId (PID 9552)
- Terminal64 SessionIds (5016, 8132, 13156)

Se Service em SessionId=0 e terminals em SessionId=1 → confirma Session 0 isolation hypothesis.

### 4.2 NSSM service config — interactive desktop?

```powershell
Write-Host "=== NSSM service flags ==="
& "C:\tools\nssm\nssm.exe" dump FluxQuantumAPEX 2>&1 | Out-String

# Relevante: AppEnvironmentExtra, AppExit, AppNoConsole, AppInteractWithDesktop
```

### 4.3 Windows Event Log para MT5/terminal64 errors

```powershell
Write-Host "=== Event Log System errors Apr 17-19 ==="
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=[datetime]"2026-04-17"; EndTime=[datetime]"2026-04-19"} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -match "MT5|terminal|crash|fault" } |
    Select-Object TimeCreated, LevelDisplayName, Message |
    Select-Object -First 10

Write-Host ""
Write-Host "=== Event Log Application errors Apr 17-19 ==="
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=[datetime]"2026-04-17"; EndTime=[datetime]"2026-04-19"; Level=2} -ErrorAction SilentlyContinue |
    Where-Object { $_.ProviderName -match "Python|terminal|MT5" } |
    Select-Object TimeCreated, ProviderName, Message |
    Select-Object -First 10
```

---

## PASSO 5 — Correlação: o que correu Apr 17?

Apr 17 foi um dia activo (memory: FASE 1-5 deploys, backup reports). Muita actividade de deploy pode ter causado a quebra.

```powershell
Write-Host "=== Apr 17 activity ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Where-Object { $_.LastWriteTime -gt [datetime]"2026-04-17" -and $_.LastWriteTime -lt [datetime]"2026-04-18" } |
    Select-Object FullName, LastWriteTime |
    Sort-Object LastWriteTime

Write-Host ""
Write-Host "=== Apr 17 reports ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "*REPORT*_2026041*" -Recurse |
    Where-Object { $_.LastWriteTime -gt [datetime]"2026-04-17" -and $_.LastWriteTime -lt [datetime]"2026-04-18" } |
    Select-Object FullName, LastWriteTime

Write-Host ""
Write-Host "=== Deploy artifacts Apr 17 ==="
Get-ChildItem "C:\FluxQuantumAI\deploy-staging-*" -ErrorAction SilentlyContinue |
    Select-Object FullName, LastWriteTime
```

### 5.1 Last FAILED execution attempts — precise content

```powershell
# Extrair 3 últimas FAILED attempts em detalhe
Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" |
    Where-Object { $_ -match 'overall_state":"FAILED' } |
    Select-Object -Last 3 |
    ForEach-Object {
        Write-Host "=== FAILED decision ==="
        $_ | ConvertFrom-Json | ConvertTo-Json -Depth 10
    }
```

Analisar:
- Que brokers estavam attempted?
- Error messages exactas?
- `execution` block tem detalhe (code MT5? retcode?)

---

## PASSO 6 — Quick diagnostic sem modificar nada

```powershell
Write-Host "=== Can Python import MetaTrader5 lib? ==="
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"

# Just test import — do not call initialize (that could reset state)
$test = @'
import sys
try:
    import MetaTrader5 as mt5
    print(f"MT5 lib imported OK, version: {mt5.__version__ if hasattr(mt5, '__version__') else 'unknown'}")
    print(f"mt5.initialize is callable: {callable(mt5.initialize)}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
'@

$test | Out-File "$env:TEMP\test_mt5_import.py" -Encoding UTF8
& $py "$env:TEMP\test_mt5_import.py"
Remove-Item "$env:TEMP\test_mt5_import.py"
```

**NÃO chamar `mt5.initialize()` — pode reset connection state e mascarar evidência.**

---

## PASSO 7 — Consolidated Investigation Report

Criar `$sprint_dir\MT5_EXECUTION_BREAK_INVESTIGATION.md`:

```markdown
# MT5 Execution Break Investigation — READ-ONLY

**Timestamp:** <UTC>
**Mode:** READ-ONLY (zero edits, zero restarts)
**Duration:** X min

---

## 1. Timeline

### Key events chronology
- **Apr 10 20:03** — last successful trade (trades.csv)
- **Apr 14 01:00** — decision_log starts current schema
- **Apr 17 19:24** — first execution attempt (after ~3 days of MISSING state)
- **Apr 17 19:54** — last execution attempt (254 failed in 30min window)
- **Apr 17 19:54 → Apr 19 13:14** — 531 GO signals, all NOT_ATTEMPTED

### Git commits Apr 10-19
[list extracted]

### File modifications Apr 10-19 (critical files)
[list extracted with timestamps]

### Correlation with Apr 17 deploy activity
[what happened Apr 17 that might have broken execution]

---

## 2. MT5 Terminals State

### Running terminals
| PID | Broker | StartTime | Window title | Apparent login state |
|---|---|---|---|---|
| 5016 | Hantec | ... | ... | [logged/not] |
| 8132 | Hantec | ... | ... | [logged/not] |
| 13156 | RoboForex | ... | ... | [logged/not] |

### MT5 terminal logs findings
[summary of recent log entries from each terminal]

### Session IDs (critical)
- Service FluxQuantumAPEX Session: X
- Python run_live.py Session: Y
- Terminal64 Sessions: Z, W, ...
- **Cross-session issue?** Yes/No with evidence

### Experts journal findings
[any relevant EA errors or activity]

---

## 3. Configuration State

### .env file
- Present: yes/no
- Last modified: ...
- (Content NOT shown for security)

### mt5_executor.py analysis
- Initialize pattern: [how is connection established]
- Credential handling: [from .env / settings.json / hardcoded]
- Session 0 workaround: [present / absent]
- Retry logic: [exists / missing]
- Error handling pattern: [summary]

### mt5_executor_hantec.py analysis
[same structure]

### settings.json relevant
[broker section if any]

---

## 4. Windows Service Context

### NSSM configuration
[dump of relevant flags]

### Event Log findings
- System log Apr 17-19: [errors relevant]
- Application log Apr 17-19: [Python/terminal errors]

### Service session analysis
- FluxQuantumAPEX runs as: [LocalSystem / user / other]
- SessionId: 0 / user
- AppInteractWithDesktop: [yes/no]

---

## 5. Last FAILED Execution Detail

### Sample failed attempt (decoded)
[full JSON of last FAILED from decision_log]

### Error message analysis
- RoboForex: "MT5 not connected" — what does this mean precisely in code?
- Hantec: "All legs failed" — what does this mean?

---

## 6. Python/MT5 Library Status
- MetaTrader5 lib import: OK / FAIL
- Version: X

---

## 7. Root Cause Hypotheses (ranked by evidence)

### Hypothesis A: Session 0 isolation
- **Evidence for:** [if sessions differ]
- **Evidence against:** [if trades Apr 10 succeeded from same setup]
- **Confidence:** High / Medium / Low

### Hypothesis B: Terminal login expired
- **Evidence for:** [terminal logs, title bar state]
- **Evidence against:** ...
- **Confidence:** ...

### Hypothesis C: Code regression in mt5_executor.py
- **Evidence for:** [file modified Apr 14-17?]
- **Evidence against:** ...
- **Confidence:** ...

### Hypothesis D: .env credentials changed/missing
- **Evidence for:** [.env last modified?]
- **Evidence against:** ...
- **Confidence:** ...

### Hypothesis E: Deploy Apr 17 broke something
- **Evidence for:** [Apr 17 deploy timing vs break timing]
- **Evidence against:** ...
- **Confidence:** ...

### Other hypothesis
...

---

## 8. RECOMMENDED NEXT STEPS (for Claude + Barbara review)

Based on top hypothesis:
- If A (Session 0): recommend [specific investigation]
- If B (login expired): recommend [terminal relogin test]
- If C (code regression): recommend [revert mt5_executor.py specific commits]
- If D (.env): recommend [check .env integrity]
- If E (deploy Apr 17): recommend [identify specific deploy artifact]

**No fix attempted in this investigation. All recommendations await Claude + Barbara approval.**

---

## 9. System State During Investigation
- Service: Running
- Capture processes: 3/3 intact (12332, 8248, 2512)
- Files modified during investigation: ZERO
- Restarts performed: ZERO
- mt5.initialize() called: NO (to preserve state for diagnosis)
```

---

## COMUNICAÇÃO FINAL

```
MT5 EXECUTION BREAK INVESTIGATION COMPLETE (READ-ONLY)

Timeline: last OK Apr 10, last attempt Apr 17 19:54, dead since
Top hypothesis: [A/B/C/D/E] — [confidence]
Evidence summary: [key finding]

Report: MT5_EXECUTION_BREAK_INVESTIGATION.md

System state: Running, capture intact, zero changes.
Aguardo Claude + Barbara review antes de qualquer fix.
```

---

## PROIBIDO

- ❌ Qualquer edit a código, config, .env
- ❌ Restart serviço FluxQuantumAPEX
- ❌ Restart MT5 terminals
- ❌ Relogin em qualquer broker
- ❌ Chamar `mt5.initialize()` ou qualquer função activa de MT5
- ❌ Tocar capture processes (12332, 8248, 2512)
- ❌ Mostrar conteúdo de .env ou credenciais (só metadata)
- ❌ Assumir causa sem evidência explícita
- ❌ Skipar qualquer passo 1-7

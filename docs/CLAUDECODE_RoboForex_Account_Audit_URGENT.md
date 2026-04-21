# TASK: INVESTIGAÇÃO URGENTE — Qual conta RoboForex está ACTIVA?

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Prioridade:** 🚨 P0 CRÍTICO — potencial execução em conta REAL não autorizada
**Deadline:** ASAP — antes de mercado abrir (~22h UTC domingo)
**Mode:** 100% READ-ONLY. Zero edits, zero restarts, zero mt5.initialize().

**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\ROBOFOREX_ACCOUNT_AUDIT.md`

---

## CONTEXTO CRÍTICO

Report anterior (`MT5_EA_BACKTEST_STATUS`) listou 3 MT5 terminals:
- PID 5016 Hantec (start 2026-04-18)
- PID 8132 Hantec (start 2026-04-19)  — **2º Hantec inesperado**
- PID 13156 RoboForex (start 2026-04-17)

**Barbara afirmou:**
1. Só existe 1 Hantec e 1 RoboForex (1 de cada, não múltiplos)
2. Problema recorrente: um segundo RoboForex aparece periodicamente
3. **O RoboForex ACTIVO deveria ser DEMO (não REAL)**

**Risco:** se `mt5_executor.py` (PID 9552, `run_live.py --broker roboforex`) estiver a enviar ordens à conta REAL em vez da DEMO, Barbara pode estar a arriscar dinheiro real sem saber.

**Memoria registada:** RoboForex demo 68302120 + Hantec 50051145.

---

## REGRA CRÍTICA — ZERO EDITS, ZERO ACÇÕES ACTIVAS

- Apenas leitura, inspecção, reporting
- **NÃO chamar `mt5.initialize()`** — pode conectar e alterar state
- NÃO restart nada
- NÃO mudar conta activa
- NÃO login nem logout
- NÃO tocar capture processes (12332, 8248, 2512)
- NÃO assumir — reportar exactamente o que os dados mostram

---

## PASSO 1 — Enumerar TODOS os processos terminal64.exe

```powershell
Write-Host "=== Todos os terminal64.exe em execução (incluindo zombie) ==="
Get-Process -Name "terminal64" -ErrorAction SilentlyContinue |
    Select-Object Id, ProcessName, StartTime, Path, MainWindowTitle, SessionId |
    Format-List

Write-Host ""
Write-Host "=== Command line de cada terminal ==="
Get-WmiObject Win32_Process -Filter "Name='terminal64.exe'" |
    ForEach-Object {
        Write-Host "---"
        Write-Host "PID: $($_.ProcessId)"
        Write-Host "CommandLine: $($_.CommandLine)"
        Write-Host "ExecutablePath: $($_.ExecutablePath)"
        Write-Host "CreationDate: $($_.CreationDate)"
        Write-Host "ParentProcessId: $($_.ParentProcessId)"
    }
```

**Critical:** cada terminal tem `Path` específico. Cada instalação RoboForex/Hantec tem o seu `terminal64.exe`. Se aparecerem **2 PIDs a usar o mesmo executable path**, é a mesma instalação corrida 2×.

---

## PASSO 2 — MetaQuotes profiles presentes no servidor

```powershell
Write-Host "=== Profiles MetaTrader (cada profile = 1 terminal config único) ==="
$terminal_root = "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal"
Get-ChildItem $terminal_root -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
        $profile_dir = $_.FullName
        $profile_id = $_.Name
        $last_write = $_.LastWriteTime
        
        # Tentar encontrar config/common.ini ou config/*.ini para identificar broker
        $cfg_dir = "$profile_dir\config"
        $broker_hint = "unknown"
        
        if (Test-Path $cfg_dir) {
            $common = "$cfg_dir\common.ini"
            if (Test-Path $common) {
                $content = Get-Content $common -Raw -ErrorAction SilentlyContinue
                if ($content -match "Login=(\d+)") {
                    $broker_hint = "Login=$($Matches[1])"
                }
                if ($content -match "Server=([^`r`n]+)") {
                    $broker_hint += ", Server=$($Matches[1])"
                }
            }
        }
        
        Write-Host ""
        Write-Host "Profile: $profile_id"
        Write-Host "  Path: $profile_dir"
        Write-Host "  Last write: $last_write"
        Write-Host "  Broker hint: $broker_hint"
        
        # List MQL5/Experts for EA deployment
        $experts = "$profile_dir\MQL5\Experts"
        if (Test-Path $experts) {
            $ea_files = Get-ChildItem $experts -Filter "FluxQuantum*" -ErrorAction SilentlyContinue
            if ($ea_files) {
                Write-Host "  FluxQuantumAI_EA deployed: YES ($($ea_files.Count) files)"
            }
        }
    }
```

**Extrair:** para cada profile, o **Login number** (account id) + **Server name** (identifica broker + se é demo ou live).

---

## PASSO 3 — Identificar DEMO vs REAL por server name

RoboForex typical server names:
- **Demo:** `RoboForex-ECN-Demo`, `RoboForex-Demo`, `RoboForex-Prime-Demo`, `RoboForex-Demo-Live`
- **Real:** `RoboForex-ECN`, `RoboForex-Prime`, `RoboForex-ECN-Live`, `RoboForex-US` (sem "Demo" no nome)

Hantec typical server names:
- **Demo:** `Hantec-Demo`, `HantecMarketsLtd-Demo`
- **Real:** `Hantec-Live`, `HantecMarketsLtd-Live`

```powershell
Write-Host "=== Server identification per terminal ==="
Get-ChildItem $terminal_root -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
        $common_ini = "$($_.FullName)\config\common.ini"
        if (Test-Path $common_ini) {
            $content = Get-Content $common_ini -Raw -ErrorAction SilentlyContinue
            $login = if ($content -match "Login=(\d+)") { $Matches[1] } else { "N/A" }
            $server = if ($content -match "Server=([^`r`n]+)") { $Matches[1].Trim() } else { "N/A" }
            
            $account_type = "UNKNOWN"
            if ($server -match "[Dd]emo") { $account_type = "DEMO" }
            elseif ($server -match "[Ll]ive|[Rr]eal|ECN$|Prime$") { $account_type = "REAL" }
            
            $broker = "UNKNOWN"
            if ($server -match "[Rr]obo") { $broker = "RoboForex" }
            elseif ($server -match "[Hh]antec") { $broker = "Hantec" }
            
            Write-Host ""
            Write-Host "Profile: $($_.Name)"
            Write-Host "  Broker: $broker"
            Write-Host "  Account type: $account_type"
            Write-Host "  Login: $login"
            Write-Host "  Server: $server"
        }
    }
```

---

## PASSO 4 — Cross-reference: qual profile está a correr?

O terminal64.exe corre sempre no contexto dum profile específico. O mapping é via `--portable` flag ou Windows registry + working dir.

```powershell
Write-Host "=== Map running PIDs to profiles ==="
$wmi_procs = Get-WmiObject Win32_Process -Filter "Name='terminal64.exe'"
foreach ($p in $wmi_procs) {
    Write-Host ""
    Write-Host "PID: $($p.ProcessId)"
    Write-Host "CommandLine: $($p.CommandLine)"
    
    # Working directory often indicates profile
    # Alt: check open file handles to config folder via Sysinternals handle.exe if available
    # Basic heuristic: executable path
    Write-Host "Executable: $($p.ExecutablePath)"
    
    # If executable path contains "RoboForex" or "Hantec", we know the installation
    $installation = "UNKNOWN"
    if ($p.ExecutablePath -match "[Rr]obo") { $installation = "RoboForex" }
    elseif ($p.ExecutablePath -match "[Hh]antec") { $installation = "Hantec" }
    Write-Host "Installation: $installation"
}
```

---

## PASSO 5 — Configuração APEX — qual conta é esperada?

```powershell
Write-Host "=== Settings.json broker configuration (REDACT credentials) ==="
$settings = "C:\FluxQuantumAI\config\settings.json"
if (Test-Path $settings) {
    $s = Get-Content $settings -Raw | ConvertFrom-Json
    
    # Show ONLY broker-relevant sections without passwords
    if ($s.broker) {
        $b = $s.broker
        Write-Host "broker.login: $($b.login)"
        Write-Host "broker.server: $($b.server)"
        Write-Host "broker.account_type: $($b.account_type)"
        # DO NOT print password
    }
    
    if ($s.mt5) {
        $m = $s.mt5
        Write-Host ""
        Write-Host "mt5.login: $($m.login)"
        Write-Host "mt5.server: $($m.server)"
        Write-Host "mt5.account_type: $($m.account_type)"
    }
    
    if ($s.execution) {
        $e = $s.execution
        Write-Host ""
        Write-Host "execution config: $($e | ConvertTo-Json -Depth 3)"
    }
}

Write-Host ""
Write-Host "=== .env file — modification info only (NO content printed) ==="
$env_file = "C:\FluxQuantumAI\.env"
if (Test-Path $env_file) {
    $i = Get-Item $env_file
    Write-Host "Exists: YES"
    Write-Host "Size: $($i.Length) bytes"
    Write-Host "Last modified: $($i.LastWriteTime)"
    Write-Host "Lines count: $((Get-Content $env_file | Measure-Object -Line).Lines)"
    # DO NOT print content
}
```

---

## PASSO 6 — mt5_executor.py login logic

```powershell
Write-Host "=== mt5_executor.py init/login pattern ==="
$path = "C:\FluxQuantumAI\mt5_executor.py"
if (Test-Path $path) {
    # Only show init/login relevant lines (not full content)
    Get-Content $path |
        Select-String -Pattern "mt5\.initialize|mt5\.login|mt5\.account_info|os\.getenv|login\s*=|server\s*=|account_number|MT5_LOGIN|MT5_SERVER|MT5_PASSWORD" |
        Select-Object -First 30
}
```

**Analisar:**
- Login vem de `.env`? Settings? Hardcoded?
- Como se escolhe DEMO vs REAL?
- Há lógica para forçar demo em modo certo?

---

## PASSO 7 — Últimas 5 decisões com broker info

```powershell
Write-Host "=== Last 5 decisions with broker detail ==="
$log = "C:\FluxQuantumAI\logs\decision_log.jsonl"
Get-Content $log -Tail 5 | ForEach-Object {
    $j = $_ | ConvertFrom-Json
    if ($j.execution) {
        Write-Host ""
        Write-Host "Decision ts: $($j.timestamp)"
        Write-Host "Action: $($j.decision.action)"
        Write-Host "Execution state: $($j.execution.overall_state)"
        if ($j.execution.brokers) {
            foreach ($b in $j.execution.brokers) {
                Write-Host "  Broker $($b.broker): attempted=$($b.attempted), state=$($b.state), account=$($b.account_number)"
            }
        }
    }
}

Write-Host ""
Write-Host "=== Last SUCCESS or FAILED attempt — full broker detail ==="
Get-Content $log | Select-String '"overall_state":"(FAILED|SUCCESS)"' |
    Select-Object -Last 1 |
    ForEach-Object { $_ | ConvertFrom-Json | ConvertTo-Json -Depth 10 }
```

---

## PASSO 8 — trades.csv último trade — que conta?

```powershell
Write-Host "=== trades.csv last 3 trades — full detail ==="
$trades = Import-Csv "C:\FluxQuantumAI\logs\trades.csv"
$trades | Select-Object -Last 3 | Format-List
```

Extrair: ticket_id, account_number, broker, asset, size, entry.

---

## PASSO 9 — Consolidated audit report

Criar `$sprint_dir\ROBOFOREX_ACCOUNT_AUDIT.md`:

```markdown
# RoboForex Account Audit — READ-ONLY

**Timestamp:** <UTC>
**Mode:** READ-ONLY
**Trigger:** Barbara raised concern about wrong account (DEMO vs REAL)

---

## CRITICAL FINDINGS

### TL;DR
[1-line answer: is APEX connecting to DEMO or REAL account?]

---

## 1. Running terminal64.exe processes

| PID | Session | Executable | CreationDate | ParentPID |
|---|---|---|---|---|
| ... | | | | |

**Total terminals running:** N
**Unique installations:** [RoboForex, Hantec, other]
**Duplicates detected:** [yes/no + which]

---

## 2. MetaQuotes profiles

| Profile ID | Broker (inferred) | Login | Server | Account Type | FluxQuantumAI_EA deployed |
|---|---|---|---|---|---|
| ... | | | | | |

---

## 3. Account identification per running terminal

| PID | Installation | Inferred profile | Account Type | Login |
|---|---|---|---|---|
| ... | | | | |

**Does APEX primary path (PID 9552, `run_live.py --execute --broker roboforex`) target DEMO or REAL?**

Based on evidence:
- [evidence chain linking run_live → mt5_executor → profile → server]

---

## 4. Configuration intent (APEX side)

### settings.json broker config (redacted)
- broker.login: X
- broker.server: Y
- broker.account_type: Z (if specified)

### .env
- Present: yes/no
- Last modified: ...
- (Content not shown)

### mt5_executor.py pattern
- Credentials source: [env / settings / hardcoded]
- How demo/real is selected: [evidence]

---

## 5. Last 64 trades — which account executed?

| Period | Count | Account | Broker |
|---|---|---|---|
| ... | | | |

**Consistent with intended DEMO?** yes/no

---

## 6. DEMO vs REAL determination

### Evidence demo:
- ...

### Evidence real:
- ...

### Conclusion
**APEX IS connecting to: [DEMO / REAL / UNKNOWN — need mt5.account_info() to verify]**

---

## 7. Duplicate terminals explanation

Observed 2 RoboForex PIDs / 2 Hantec PIDs?
- [yes/no]
- Root cause hypothesis: [launched manually + auto-launched / NSSM double start / user interaction / zombie]

---

## 8. RECOMMENDATIONS (await Barbara+Claude approval)

### Immediate (before market open):
1. [Kill duplicate if real PID identified]
2. [Verify running terminal is DEMO]
3. [If REAL: emergency stop]

### Fix MT5 execution path:
1. [conditional on account audit result]

---

## 9. Zero edits / zero restarts performed
- Capture processes: 3/3 intact
- No mt5.initialize() called
- No terminal restarts
- No config changes
```

---

## COMUNICAÇÃO FINAL

**Se conta DEMO confirmada:**
```
✅ RoboForex CONFIRMED DEMO (Login X, Server Y-Demo)
Duplicate terminals: [explained]
Safe to proceed with MT5 fix sprint.
Report: ROBOFOREX_ACCOUNT_AUDIT.md
```

**Se conta REAL ou AMBIGUOUS:**
```
🚨 ALERT — RoboForex may be connected to REAL account
Evidence: [chain]
DO NOT restart service.
DO NOT proceed with MT5 fix until account clarified.
Report: ROBOFOREX_ACCOUNT_AUDIT.md
Awaiting Barbara emergency guidance.
```

---

## PROIBIDO

- ❌ Chamar `mt5.initialize()` ou qualquer MT5 function activa
- ❌ Restart MT5 terminals
- ❌ Kill qualquer processo
- ❌ Editar config, .env, settings.json
- ❌ Login/logout em qualquer conta
- ❌ Restart FluxQuantumAPEX
- ❌ Imprimir passwords / credentials
- ❌ Assumir — reportar só evidência directa
- ❌ Tocar capture processes

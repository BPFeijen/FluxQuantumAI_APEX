# TASK: Investigar mt5_robo_connected=false

**Para:** ClaudeCode
**Projeto:** APEX live em `C:\FluxQuantumAI\`
**Modo:** **READ-ONLY discovery**. NÃO modificar nada. NÃO parar serviços.
**Tempo estimado:** 10-15 min
**Output:** `MT5_CONNECTION_DISCOVERY_<timestamp>.md`

---

## CONTEXTO

Durante Fase 4-quater, o `service_state.json` reporta:
- `FluxQuantumAPEX` service: Running
- `mt5_robo_connected: false`

**Implicação grave:** processo Python está vivo mas não tem conexão ao MetaTrader 5 RoboForex. Se o sistema emitir GO, execução vai falhar. **Sistema a operar "cego"** — risco de apanhar sinais mas não executar ordens.

Precisamos entender:
1. O que significa `mt5_robo_connected` no código
2. Por que está `false`
3. Se é problema de configuração, credentials, ou terminal MT5

**Aguardar que Fase 4-quater monitor (bkeac635j) termine antes de executar isto.** Não interromper.

---

## CRITICAL RULES

1. **READ-ONLY total.** Zero modificações.
2. **NÃO parar serviços.** FluxQuantumAPEX + Dashboard continuam Running.
3. **NÃO parar captura.** Nunca tocar nos 3 PIDs de captura.
4. **NÃO reconectar MT5** mesmo que seja tentador — Barbara decide o fix.
5. Output: discovery report estruturado.

---

## PASSO 1 — Confirmar estado actual

```powershell
Write-Host "=== MT5 Connection Discovery @ $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="

# Estado do service_state.json completo
Write-Host "`n--- service_state.json ---"
Get-Content "C:\FluxQuantumAI\logs\service_state.json" -Raw

# Serviços
Write-Host "`n--- Services ---"
Get-Service FluxQuantumAPEX, FluxQuantumAPEX_Dashboard, FluxQuantumAPEX_Live, FluxQuantumAPEX_Dashboard_Hantec |
    Format-Table Name, Status

# Last heartbeat timestamp
$state = Get-Content "C:\FluxQuantumAI\logs\service_state.json" -Raw | ConvertFrom-Json
Write-Host "`nLast heartbeat: $($state.last_heartbeat_at)"
Write-Host "mt5_robo_connected: $($state.mt5_robo_connected)"
Write-Host "mt5_hantec_connected: $($state.mt5_hantec_connected)"
```

---

## PASSO 2 — Procurar onde `mt5_robo_connected` é escrito no código

```powershell
Write-Host "`n=== Code paths for mt5_robo_connected ==="

# Procurar em live/
$matches = Get-ChildItem -Path "C:\FluxQuantumAI\live" -Recurse -Include *.py |
    Select-String -Pattern "mt5_robo_connected" -List

foreach ($m in $matches) {
    Write-Host "`n--- File: $($m.Path) ---"
    Get-Content $m.Path | Select-String -Pattern "mt5_robo_connected" -Context 3,3
}

# Procurar em root
$matches2 = Get-ChildItem -Path "C:\FluxQuantumAI" -Recurse -Include *.py -Depth 1 |
    Select-String -Pattern "mt5_robo_connected" -List

foreach ($m in $matches2) {
    Write-Host "`n--- File: $($m.Path) ---"
    Get-Content $m.Path | Select-String -Pattern "mt5_robo_connected" -Context 3,3
}
```

---

## PASSO 3 — Procurar MT5 initialize call

```powershell
Write-Host "`n=== MT5 initialize calls ==="

# Procurar MetaTrader5.initialize ou mt5.initialize
$mt5Init = Get-ChildItem -Path "C:\FluxQuantumAI" -Recurse -Include *.py |
    Select-String -Pattern "MetaTrader5\.initialize|mt5\.initialize" -List

foreach ($m in $mt5Init) {
    Write-Host "`n--- File: $($m.Path) ---"
    Get-Content $m.Path | Select-String -Pattern "MetaTrader5\.initialize|mt5\.initialize" -Context 5,15
}
```

**O que procurar nos resultados:**
- Está a passar `path=` explícito ao `initialize()`?
- Que credenciais (login, password, server) são usadas?
- Há lógica de retry/fallback?
- Há tratamento de erro documentado?

---

## PASSO 4 — Verificar credenciais e configuração

```powershell
Write-Host "`n=== Credentials configuration ==="

# .env file (roboforex)
if (Test-Path "C:\FluxQuantumAI\.env") {
    Write-Host "`n--- .env file keys (SAFE — show only keys, not values) ---"
    Get-Content "C:\FluxQuantumAI\.env" | ForEach-Object {
        if ($_ -match "^([A-Z_]+)=") {
            Write-Host "  $($matches[1])=<redacted>"
        }
    }
}

# settings.json — MT5 section
if (Test-Path "C:\FluxQuantumAI\config\settings.json") {
    Write-Host "`n--- settings.json MT5 section ---"
    $settings = Get-Content "C:\FluxQuantumAI\config\settings.json" -Raw | ConvertFrom-Json
    if ($settings.mt5) {
        $settings.mt5 | ConvertTo-Json -Depth 5
    } elseif ($settings.roboforex) {
        $settings.roboforex | ConvertTo-Json -Depth 5
    }
}
```

---

## PASSO 5 — Terminais MT5 instalados

```powershell
Write-Host "`n=== MT5 terminals installed ==="

# Procurar terminal64.exe (MT5)
$mt5Terminals = @()
$searchPaths = @(
    "C:\Program Files\MetaTrader 5",
    "C:\Program Files (x86)\MetaTrader 5",
    "C:\MT5*",
    "C:\MetaTrader*",
    "C:\Users\*\AppData\Roaming\MetaQuotes",
    "C:\FluxQuantumAI\MT5*"
)

foreach ($path in $searchPaths) {
    Get-ChildItem -Path $path -Recurse -Filter "terminal64.exe" -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "Found: $($_.FullName)"
        $mt5Terminals += $_.FullName
    }
}

Write-Host "`nTotal MT5 terminals found: $($mt5Terminals.Count)"

# Processos MT5 a correr
Write-Host "`n--- MT5 processes running ---"
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "terminal64.exe" } |
    Select-Object ProcessId, ExecutablePath, CommandLine | Format-Table -Wrap
```

**O que procurar:**
- **Só 1 terminal MT5 instalado?** Se sim, confirma suspeita "1 terminal = 1 conta"
- **2 terminais?** Então há capacidade para roboforex + hantec
- **Terminal está a correr?** Se sim, onde — GUI aberto ou headless?

---

## PASSO 6 — Verificar logs de MT5 init errors

```powershell
Write-Host "`n=== MT5 init errors in logs (last 200 lines stderr) ==="

if (Test-Path "C:\FluxQuantumAI\logs\service_stderr.log") {
    $mt5Errors = Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 500 |
        Select-String -Pattern "MT5|MetaTrader|initialize|login|connect|AuthorizationFailed" -CaseSensitive:$false
    if ($mt5Errors) {
        $mt5Errors | Select-Object -First 30 | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "No MT5-related messages in last 500 lines of stderr"
    }
}

Write-Host "`n--- MT5 messages in stdout (last 500 lines) ---"
if (Test-Path "C:\FluxQuantumAI\logs\service_stdout.log") {
    $mt5Info = Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Tail 500 |
        Select-String -Pattern "MT5|MetaTrader|roboforex|connected|disconnected" -CaseSensitive:$false
    if ($mt5Info) {
        $mt5Info | Select-Object -First 30 | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "No MT5-related info messages found"
    }
}
```

**O que procurar:**
- `MetaTrader5.initialize() failed`
- `AuthorizationFailed`
- `terminal not found`
- `Login failed: Invalid account`
- `Connection timeout`
- `Version mismatch`

---

## PASSO 7 — Verificar mt5_executor.py e telegram_notifier.py

```powershell
# mt5_executor.py
if (Test-Path "C:\FluxQuantumAI\live\mt5_executor.py") {
    Write-Host "`n=== mt5_executor.py (first 100 lines) ==="
    Get-Content "C:\FluxQuantumAI\live\mt5_executor.py" -TotalCount 100
}

# Como mt5_robo_connected é determinado — ver contexto
Write-Host "`n=== Health check mt5 logic (telegram_notifier.py) ==="
if (Test-Path "C:\FluxQuantumAI\live\telegram_notifier.py") {
    Get-Content "C:\FluxQuantumAI\live\telegram_notifier.py" |
        Select-String -Pattern "mt5_robo|mt5_hantec|mt5_connected" -Context 5,5 | Select-Object -First 15
}
```

---

## PASSO 8 — Gerar MT5_CONNECTION_DISCOVERY report

```markdown
# MT5 CONNECTION DISCOVERY — <timestamp>

## Estado actual
- FluxQuantumAPEX service: Running
- mt5_robo_connected: false
- mt5_hantec_connected: false
- Last heartbeat: <timestamp>

## Architecture findings

### Terminais MT5 instalados
- Count: <n>
- Paths: <list>

### Processos MT5 correr
- Count: <n>
- Details: <list>

### mt5_executor.py approach
- Does it call initialize(path=...) explicitly? YES/NO
- Credentials source: .env / settings.json / hardcoded
- Retry logic: YES/NO

### Credentials config
- .env keys present: <list>
- settings.json mt5 section: <summary or "not present">

### Log errors (most recent 500 lines stderr/stdout)
- MT5 init errors: <count + excerpts>
- Connection errors: <count + excerpts>
- Auth errors: <count + excerpts>

## Root cause hypotheses (ranked by likelihood)

### Hipótese A: Credenciais erradas/expiradas
- Evidence: <X>
- Probability: <High/Medium/Low>

### Hipótese B: Terminal MT5 não instalado ou em path errado
- Evidence: <X>
- Probability: <High/Medium/Low>

### Hipótese C: Terminal MT5 não está a correr (headless expected?)
- Evidence: <X>
- Probability: <High/Medium/Low>

### Hipótese D: Initialize path não explícito
- Evidence: <X>
- Probability: <High/Medium/Low>

### Hipótese E: Algo mais detectado no código
- Description: <X>
- Evidence: <X>

## Recommended fix path

1. Barbara review this discovery
2. Decide hipótese mais provável
3. Claude escreve design doc para fix
4. ClaudeCode implementa
5. Teste validação

## Status

🟡 DISCOVERY ONLY — no changes made

## Next steps (aguardam Barbara)

- Decide qual hipótese investigar mais a fundo
- Autorizar fix após review
```

---

## OUTPUT

Reporta:
1. Path do `MT5_CONNECTION_DISCOVERY_<timestamp>.md`
2. Resumo 10 linhas:
   - Terminais MT5 instalados (quantos, paths)
   - Processos MT5 a correr (quantos)
   - mt5_executor.py usa path=explicit? (yes/no)
   - Credentials source
   - Log errors encontrados (yes/no + counts)
   - Top 3 hypotheses
3. **Aguarda Barbara.** Não executa fix.

---

**Discovery silenciosa e cirúrgica. Zero impacto no sistema Running.**

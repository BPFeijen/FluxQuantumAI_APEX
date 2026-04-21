# TASK: STATUS CHECK — MT5/EA Integration + Backtest Engine Capability

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Mode:** 100% READ-ONLY. Zero edits. Pure investigation.
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\MT5_EA_BACKTEST_STATUS_REPORT.md`

---

## OBJECTIVO

Duas investigações READ-ONLY em paralelo antes de Monday market open:

**A) MT5/EA Integration Status**
- É sistema a conectar-se correctamente a MT5?
- EA FluxQuantumAI_EA.mq5 está deployado e a funcionar?
- Se não, porquê? E qual é o actual execution path?

**B) Backtest Engine Capability**
- APEX tem backtest mode nativo?
- Se sim, como se invoca?
- Se não, que esforço para construir?

Ambas são investigações — **zero edits a qualquer ficheiro ou sistema**.

---

## REGRA CRÍTICA — ZERO ASSUMPTIONS & ZERO EDITS

- Apenas leitura, inspecção, status queries
- Não modificar configs, código, ou executar deploys
- Não tocar capture processes (12332, 8248, 2512)
- Não reiniciar serviço
- Se algo não estiver claro, **reportar com incerteza explícita**, não adivinhar

---

## PARTE A — MT5/EA STATUS INVESTIGATION

### A.1 — Identificar instâncias MT5 no servidor

```powershell
Write-Host "=== MT5 processes running ==="
Get-Process | Where-Object { $_.ProcessName -match "terminal64|metatrader" } |
    Select-Object Id, ProcessName, Path, StartTime

Write-Host "=== MT5 installations ==="
Get-ChildItem "C:\Program Files\*" -Filter "terminal64.exe" -Recurse -ErrorAction SilentlyContinue |
    Select-Object DirectoryName, LastWriteTime
Get-ChildItem "C:\Users\*\AppData\Roaming\MetaQuotes\Terminal" -ErrorAction SilentlyContinue -Directory |
    Select-Object FullName, LastWriteTime

Write-Host "=== NSSM services relacionados com MT5 ==="
Get-Service | Where-Object { $_.Name -match "MT5|MetaTrader|Hantec|RoboForex" } |
    Select-Object Name, Status, StartType
```

### A.2 — EA FluxQuantumAI_EA.mq5 status

```powershell
Write-Host "=== EA files ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "FluxQuantumAI_EA.*" -Recurse |
    Select-Object FullName, Length, LastWriteTime

Get-ChildItem "C:\Users\*\AppData\Roaming\MetaQuotes\Terminal\*\MQL5\Experts" -Filter "FluxQuantum*" -Recurse -ErrorAction SilentlyContinue |
    Select-Object FullName, LastWriteTime
```

**Ler o EA `.mq5` source (read-only)** para extrair:
- Magic number actual (hardcoded?)
- Port number que tenta conectar (8088? 8082? outro?)
- Symbol name esperado (GC? XAUUSD?)
- Risk params, lot sizing, SL/TP defaults
- Any JSON parser issues known

```powershell
$ea_path = "C:\FluxQuantumAI\FluxQuantumAI_EA.mq5"
if (Test-Path $ea_path) {
    Write-Host "=== EA source key excerpts ==="
    Get-Content $ea_path | Select-String -Pattern "MagicNumber|Port|Symbol|extern|input|InpLots|SL|TP" -Context 0,1 |
        Select-Object -First 50
}
```

### A.3 — APEX → MT5 integration path actual

Investigar como APEX actualmente envia orders para execução:

```powershell
Write-Host "=== Integration code search ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Select-String -Pattern "MT5|MetaTrader5|mt5\.|pytrader|send_order|place_order|execute_trade" |
    Group-Object -Property Path |
    Select-Object Name, Count |
    Sort-Object Count -Descending

# Look for specific broker references
Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Select-String -Pattern "RoboForex|Hantec|68302120|50051145" |
    Select-Object Path, LineNumber, Line -First 30
```

### A.4 — Verificar decision→order flow em runtime

```powershell
# Last decisions — are they being written? Are orders being attempted?
Write-Host "=== Recent decisions ==="
if (Test-Path "C:\FluxQuantumAI\logs\decision_log.jsonl") {
    Get-Content "C:\FluxQuantumAI\logs\decision_log.jsonl" | Select-Object -Last 5
}

Write-Host "=== Position monitor state ==="
if (Test-Path "C:\FluxQuantumAI\logs\positions.json") {
    Get-Content "C:\FluxQuantumAI\logs\positions.json" -Raw
}

Write-Host "=== Any mt5 broker connection logs? ==="
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"
if (Test-Path $stderr) {
    Get-Content $stderr -Tail 200 | Select-String -Pattern "MT5|broker|connection|order|position" |
        Select-Object -First 20
}
```

### A.5 — Reportar PyTrader status

Memory diz que "PyTrader NÃO viável — XAUUSD só paid version". Confirmar:

```powershell
Write-Host "=== PyTrader references ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Select-String -Pattern "pytrader|PyTrader" |
    Select-Object Path, LineNumber, Line
```

### A.6 — Questão central: sistema EXECUTA trades ou só gera signals?

Com base em A.1-A.5, responder:

1. **Execution path actual:** APEX → ??? → broker. O que está no meio?
2. **MT5 está conectado e recebendo orders?** Evidência concreta no log.
3. **EA FluxQuantumAI está em uso?** Se não, porque?
4. **Bugs conhecidos em EA** (porta 8088 vs 8082, magic number, JSON parser): estão presentes? Foram corrigidos?
5. **Trades dos últimos 10 dias (Mar-Apr 2026 no trades.csv) — como foram executados?** Manual? Via EA? Outro método?

---

## PARTE B — BACKTEST ENGINE CAPABILITY

### B.1 — Procurar modo de backtest existente

```powershell
Write-Host "=== Backtest references in codebase ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Select-String -Pattern "backtest|simulate|historical_mode|replay" |
    Group-Object -Property Path |
    Select-Object Name, Count |
    Sort-Object Count -Descending

Get-ChildItem "C:\FluxQuantumAI" -Filter "*backtest*" -Recurse |
    Select-Object FullName, Length, LastWriteTime

Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Select-String -Pattern "def backtest|def simulate|def replay|BacktestEngine|SimulatedTrader" |
    Select-Object Path, LineNumber, Line -First 30
```

### B.2 — Identificar APEX entrypoint e como é invocado

```powershell
# Main entrypoint
Write-Host "=== APEX entrypoints ==="
Get-ChildItem "C:\FluxQuantumAI" -Filter "*.py" -Recurse |
    Select-String -Pattern 'if __name__ == .__main__' |
    Select-Object Path, LineNumber

# Service config (NSSM)
Write-Host "=== Service command line ==="
nssm dump FluxQuantumAPEX 2>&1 | Out-String
```

### B.3 — Event loop/main cycle structure

Identificar:
- Onde está o main loop
- É event-driven ou polling?
- Como consome market data (live tick feed? file replay capable?)
- Se tem flag/mode para backtest

```powershell
$entry_candidates = @(
    "C:\FluxQuantumAI\main.py",
    "C:\FluxQuantumAI\run.py",
    "C:\FluxQuantumAI\live\event_processor.py",
    "C:\FluxQuantumAI\apex_live.py"
)

foreach ($f in $entry_candidates) {
    if (Test-Path $f) {
        Write-Host "=== $f (first 60 lines) ==="
        Get-Content $f -TotalCount 60
        Write-Host ""
    }
}
```

### B.4 — Fase 8 POC backtest — reaproveitável?

Memory diz que Fase 8 tinha POC backtest (invalidado porque 10 dias só, sem news_gate, sem operational_rules, sem SHIELD, sem hedge). Mas a estrutura pode servir de base.

```powershell
Write-Host "=== Fase 8 backtest artifacts ==="
Get-ChildItem "C:\FluxQuantumAI\sprints" -Filter "*FASE_8*" -Recurse -ErrorAction SilentlyContinue |
    Select-Object FullName, LastWriteTime

Get-ChildItem "C:\FluxQuantumAI" -Filter "backtest*.py" -Recurse |
    Select-Object FullName, Length, LastWriteTime
```

### B.5 — Decision_log as ground truth for replay

Se o sistema tem `decision_log.jsonl` com todas as decisões históricas, pode ser usado para **replay backtest**:

```powershell
Write-Host "=== decision_log metadata ==="
$log = "C:\FluxQuantumAI\logs\decision_log.jsonl"
if (Test-Path $log) {
    $size_mb = (Get-Item $log).Length / 1MB
    $line_count = (Get-Content $log | Measure-Object -Line).Lines
    Write-Host "Size: $([math]::Round($size_mb, 2)) MB, Lines: $line_count"
    Write-Host "First line:"
    Get-Content $log -TotalCount 1
    Write-Host "Last line:"
    Get-Content $log -Tail 1
}
```

### B.6 — Summary conclusion — possibility & effort estimate

Com base em B.1-B.5, responder:

1. **APEX tem backtest mode nativo?** Sim/Não com evidência
2. **Se sim:** como se invoca? Que parameters?
3. **Se não:** que esforço para construir um?
   - Reuse Fase 8 POC (rejeitado mas estrutura existe)?
   - Feed replay via decision_log?
   - Wrapper que mocka capture processes + feed?
   - Novo engine do zero?
4. **Tempo estimado para backtest funcional** (horas): 1-4 / 4-8 / 8-24 / >24

---

## PASSO FINAL — CONSOLIDATED STATUS REPORT

Criar `$sprint_dir\MT5_EA_BACKTEST_STATUS_REPORT.md`:

```markdown
# MT5/EA Integration + Backtest Engine — Status Investigation

**Timestamp:** <UTC>
**Mode:** READ-ONLY
**Duration:** X min

---

## PARTE A — MT5/EA STATUS

### A.1 MT5 installations & processes
- Installations found: [list paths + last modified]
- Running terminal64.exe processes: [PIDs, paths]
- NSSM services MT5-related: [list + status]

### A.2 EA FluxQuantumAI_EA.mq5
- Source location: ...
- Deployed to MT5 MQL5\Experts? [yes/no + path]
- Magic number: X
- Port number: Y
- Symbol: Z
- Known issues present? [list]

### A.3 Execution path actual
**APEX → [?] → Broker**
- MT5 Python library imports: [list]
- PyTrader references: [if any]
- Direct mt5 API usage: [where]
- HTTP/JSON bridge? [evidence]

### A.4 Runtime evidence
- Recent decisions logged: [count last 24h]
- Position monitor state: [positions open/history]
- Broker connection events in stderr: [examples]
- Actual trades executed recently: [evidence or "no"]

### A.5 PyTrader status
- In use? [yes/no]
- Limitations: [XAUUSD paid-only confirmed]

### A.6 Critical questions answered
1. **Execution path:** [summary]
2. **MT5 connected?** [yes/no + evidence]
3. **EA in use?** [yes/no + why]
4. **Known bugs status:** [addressed/pending/unknown]
5. **Last 10 days trades — how executed?** [manual/EA/other]

---

## PARTE B — BACKTEST CAPABILITY

### B.1 Existing backtest infrastructure
- Files with backtest-related code: [count + top 10]
- Dedicated backtest entrypoints: [list or "none"]

### B.2 APEX entrypoints & invocation
- Main file: [path]
- NSSM service command: [full command line]
- Service arg for backtest mode: [yes/no]

### B.3 Event loop structure
- Event-driven or polling: [classification]
- Market data consumption: [how]
- Flag for backtest/replay mode: [yes/no + how]

### B.4 Fase 8 POC reuse potential
- Artifacts present: [list]
- Can be extended? [yes/no + what's missing]

### B.5 decision_log as replay source
- Size: X MB, Y lines
- Date range: Z
- Feasible as replay base? [yes/no]

### B.6 Estimate
**Backtest engine available:** [NATIVE / MISSING]

If MISSING:
- **Build effort:** [1-4 / 4-8 / 8-24 / >24 hours]
- **Recommended approach:** [reuse Fase 8 / decision_log replay / mock capture / new engine]
- **Blocker for Monday:** [yes/no]

---

## OVERALL STATUS & RECOMMENDATION

### System readiness for Monday 22h UTC

| Component | Status | Risk if untested |
|---|---|---|
| 4 features (StatGuardrail/DefenseMode/V4/ApexNewsGate) | ✅ validated | LOW |
| Data-driven windows (FOMC 5/60, PPI 10/30, etc.) | ✅ applied, ❌ not backtested | MEDIUM-HIGH |
| MT5/EA execution | [?] | [?] |
| Backtest validation of full system | ❌ missing | HIGH |

### Recommendation

Based on findings:
- If MT5 broken: fix critical path FIRST
- If backtest feasible in <4h: run before Monday
- If backtest needs >8h: Monday open with EXTRA human oversight, full backtest deferred to this week

### Files inventoried
[full list paths]

---

**NO EDITS PERFORMED. ALL READ-ONLY.**
```

---

## COMUNICAÇÃO FINAL

```
STATUS INVESTIGATION COMPLETE (READ-ONLY)

MT5/EA:
- [sintese A.6]

BACKTEST:
- [sintese B.6]

System readiness Monday: [GO / GO WITH CAUTION / NOT READY]

Report: MT5_EA_BACKTEST_STATUS_REPORT.md

Aguardo Claude + Barbara review antes de qualquer ação correctiva.
```

---

## PROIBIDO

- ❌ Qualquer edit a código, yaml, configs
- ❌ Restart serviço
- ❌ Tocar capture processes (PIDs 12332, 8248, 2512)
- ❌ Instalar/deploy EA ou qualquer outro componente
- ❌ Tentar conectar MT5 se não está a correr
- ❌ Assumir estado sem evidência concreta
- ❌ Skipar qualquer passo A.1-A.6 ou B.1-B.6
- ❌ Skipar report consolidado

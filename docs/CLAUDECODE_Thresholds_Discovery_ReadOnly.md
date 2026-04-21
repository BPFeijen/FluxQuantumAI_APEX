# TASK: DISCOVERY EXTENDIDO READ-ONLY — Sistema Thresholds news_gate

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Mode:** 100% READ-ONLY. Zero edits em código, yaml, configs.
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\THRESHOLDS_DISCOVERY_REPORT.md`

---

## CONTEXTO

Ao tentar aplicar thresholds data-driven da FASE II ao `news_config.yaml`, descobrimos:
- Yaml tem secções `gold_blocking`, `risk_thresholds`, `position_multipliers` — **código NÃO lê**
- Thresholds reais estão **hardcoded** em `apex_news_gate.py` e `risk_calculator.py`
- `pause_before`/`pause_after` são **por-evento**, não globais
- Escalas da proposta FASE II (1.43×, 2.20×, 4.49× baseline vol) **não batem** com decimais 0-1 hardcoded (SCORE_BLOCK_ENTRY=0.70, SCORE_EXIT_ALL=0.90)

**Precisamos mapa completo do sistema real antes de qualquer edit.**

---

## REGRA CRÍTICA — ZERO EDITS

- **Apenas leitura.** Nenhum ficheiro é modificado.
- **Apenas extracção de código e valores.** Nenhuma hipótese sobre como deveria ser.
- Se algo não está claro, **reportar com incerteza explícita**, não adivinhar.
- **Não tocar** capture processes (PIDs 12332, 8248, 2512) nem serviço.

---

## PASSO 1 — Extrair todos os thresholds hardcoded em `apex_news_gate.py`

```powershell
$path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\apex_news_gate.py"
Write-Host "=== FULL FILE ==="
Get-Content $path -Raw
```

**Reportar ficheiro completo.** Depois marcar especificamente:
- Linhas com constantes numéricas (SCORE_*, THRESHOLD_*, etc.)
- Função `_score_to_action()` completa (ou equivalente)
- Onde yaml é lido (se for lido) — `yaml.safe_load` callsites
- Qualquer menção a `pause_before`, `pause_after`, `pre_event`, `post_event`

---

## PASSO 2 — Extrair todos os thresholds hardcoded em `risk_calculator.py`

```powershell
$path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\risk_calculator.py"
Write-Host "=== FULL FILE ==="
Get-Content $path -Raw
```

**Reportar ficheiro completo.** Depois marcar:
- Todas as constantes numéricas
- Função `_event_score()` completa
- Função `_score_to_action()` completa
- Como `pause_before` e `pause_after` são consumidos
- Como `importance` de evento afecta score

---

## PASSO 3 — Encontrar onde `pause_before`/`pause_after` são definidos

Opções possíveis (verificar todas):

```powershell
# Opção A: hardcoded em economic_calendar.py
Select-String "pause_before|pause_after" "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\*.py"

# Opção B: vêm de API TradingEconomics (cada evento)
# Verificar economic_calendar.py:408-409 callsite

# Opção C: vêm de algum outro config
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_News" -Filter "*.yaml" -Recurse
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_News" -Filter "*.json" -Recurse

# Opção D: defaults hardcoded + override per-event
Select-String "DEFAULT_PAUSE|default_pause|pause.*=.*\d" "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\*.py"
```

**Reportar findings completos:** de onde vêm os valores, quais os defaults, se é possível override per-event.

---

## PASSO 4 — Mapear fluxo de cálculo do SCORE

**Objectivo:** entender matematicamente como o `score` (decimal 0-1 usado com SCORE_BLOCK_ENTRY=0.70, SCORE_EXIT_ALL=0.90) é calculado.

Seguir o código:
1. Onde `score` é calculado inicialmente?
2. Que inputs afectam o score? (importance do evento? minutos ao evento? tipo do evento? outras variáveis?)
3. Qual a fórmula? Weighted sum? Max? Multiplicative?
4. Que valores podem levar score a atingir 0.70? E 0.90?

**Output do Passo 4:** diagrama textual do fluxo:

```
ScoreFlow:
  1. _event_score(event) → raw_score based on: [listar inputs]
  2. Formula: [extrair fórmula real do código]
  3. Aggregation across events: [max? sum? weighted?]
  4. Final score → _score_to_action():
     - < X1: NORMAL
     - X1 < score < X2: CAUTION
     - X2 < score < 0.70: REDUCED
     - >= 0.70: BLOCKED
     - >= 0.90: EXIT_ALL
```

---

## PASSO 5 — Yaml consumption analysis

```powershell
$dir = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News"
Write-Host "=== Todas as ocorrências de yaml.safe_load ==="
Select-String "yaml.safe_load|yaml\.load" $dir\*.py

Write-Host ""
Write-Host "=== Todas as keys de config acedidas ==="
Select-String 'self\._cfg\[|self\.cfg\[|config\[|cfg\["|cfg\.get\(' $dir\*.py

Write-Host ""
Write-Host "=== Alpha Vantage config usage ==="
Select-String 'alpha_vantage' $dir\*.py

Write-Host ""
Write-Host "=== TradingEconomics config usage ==="
Select-String 'tradingeconomics|trading_economics' $dir\*.py
```

**Report:** lista completa de keys que o código realmente lê do yaml. Confirmar que `gold_blocking`, `risk_thresholds`, `position_multipliers` **não** são acedidas.

---

## PASSO 6 — Verificar imports e dependencies entre ficheiros

```powershell
$dir = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News"
foreach ($f in Get-ChildItem $dir -Filter "*.py") {
    Write-Host "=== $($f.Name) imports ==="
    Get-Content $f.FullName | Select-String "^import |^from " | ForEach-Object { $_.Line }
    Write-Host ""
}
```

**Output:** mapa de dependências entre os 6+ ficheiros do `APEX_News/`.

---

## PASSO 7 — TradingEconomics API response structure

**Verificar se `pause_before`/`pause_after` vêm da API ou do código:**

```powershell
$path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\economic_calendar.py"
Write-Host "=== FULL FILE ==="
Get-Content $path -Raw
```

**Analisar:**
- Linha 404: `for kw in cfg["keywords"]:` — o que é "keywords"?
- Linha 407-409: `"impact": cfg["impact"]`, `pause_before`, `pause_after` — como é populated?
- Estrutura do `cfg` passed in: vem do yaml? TE API? hardcoded dict?

**Se vier de dict hardcoded por keyword:** onde está definido? Exemplo completo do dict.

---

## PASSO 8 — Testar hipótese: existem outros yamls/configs?

```powershell
Write-Host "=== All yaml/json files in APEX_News and parents ==="
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_News" -Recurse -Include "*.yaml","*.yml","*.json" |
    Select-Object FullName, Length, LastWriteTime

Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD" -Filter "*.yaml" -Recurse |
    Select-Object FullName, Length, LastWriteTime

Get-ChildItem "C:\FluxQuantumAI" -Filter "*news*.yaml" -Recurse |
    Select-Object FullName, Length, LastWriteTime

Get-ChildItem "C:\FluxQuantumAI" -Filter "*news*.json" -Recurse |
    Select-Object FullName, Length, LastWriteTime
```

**Report:** TODOS os ficheiros de config relacionados com news no sistema, com paths completos.

---

## PASSO 9 — Estado actual runtime (sanidade)

```powershell
$svc = Get-Service FluxQuantumAPEX
Write-Host "Service: $($svc.Status)"

foreach ($pid in 12332, 8248, 2512) {
    try {
        $p = Get-Process -Id $pid -ErrorAction Stop
        Write-Host "Capture PID $pid ($($p.ProcessName)): OK"
    } catch { Write-Host "WARN PID $pid not found" }
}
```

**Confirmar:** sistema continua Running, capture processes intactos.

---

## PASSO 10 — Report consolidado

Criar `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\THRESHOLDS_DISCOVERY_REPORT.md`:

```markdown
# Thresholds Discovery — News System — READ-ONLY

**Timestamp:** <UTC>
**Mode:** READ-ONLY (zero edits)
**Status:** ✅ COMPLETE

## 1. Thresholds hardcoded em apex_news_gate.py

| Constant | Line | Value | Used in |
|---|---|---|---|
| SCORE_BLOCK_ENTRY | 62 | 0.70 | ... |
| SCORE_EXIT_ALL | 90 | 0.90 | ... |
| [todas as outras encontradas] | | | |

### _score_to_action() completo
[código]

## 2. Thresholds hardcoded em risk_calculator.py

| Constant | Line | Value | Used in |
|---|---|---|---|
| [todas] | | | |

### _event_score() completo
[código]

## 3. pause_before / pause_after — origem

**Source:** [código / yaml / TE API / dict hardcoded / mix]

**Defaults encontrados:**
- HIGH impact: pause_before=X, pause_after=Y
- MEDIUM impact: pause_before=X, pause_after=Y
- LOW impact: pause_before=X, pause_after=Y

**Possible override:** [yes/no, how]

## 4. Score calculation flow

```
[diagrama textual completo]
```

## 5. Yaml consumption

**Keys actually read from news_config.yaml:**
- alpha_vantage.*
- tradingeconomics.*
- [outras]

**Keys in yaml but NOT read:**
- gold_blocking.*
- risk_thresholds.*
- position_multipliers.*

## 6. File dependency map

```
[import graph]
```

## 7. All config files

| Path | Size | Purpose |
|---|---|---|
| [list] | | |

## 8. Translation of FASE II proposal to real code

| Proposal (FASE II) | Real code parameter | Translation feasibility |
|---|---|---|
| pre_event 1800→300s | pause_before per importance | [depends on findings] |
| post_event 1800→180s | pause_after per importance | [depends] |
| block_threshold 2.5→2.0 | SCORE_BLOCK_ENTRY=0.70 (different scale!) | [impossible without understanding score formula] |
| importance_weights | [maps to what?] | [?] |

## 9. Proposed next steps

Based on findings, concrete proposal for:
- What to edit
- Where to edit
- Expected behavior change
- Risk assessment

**NOT executed. Awaiting Barbara + Claude review.**

## 10. System state
- Service: Running
- Capture processes: 3/3 intact
- Files modified: ZERO
```

---

## COMUNICAÇÃO FINAL

```
DISCOVERY COMPLETE — THRESHOLDS_DISCOVERY_REPORT.md

Summary:
- X thresholds hardcoded identified
- Y yaml keys actually consumed
- Z config files in system
- Score formula extracted
- Translation FASE II→real code: [feasible / partial / not feasible]

System state: Running, capture processes intact, ZERO files modified.

Aguardo Barbara + Claude review antes de qualquer edit.
```

---

## PROIBIDO

- ❌ Qualquer edit a qualquer ficheiro
- ❌ Restart serviço
- ❌ Propor soluções durante discovery — apenas extrair factos
- ❌ Fazer inferências além do que o código mostra literalmente
- ❌ Skipar qualquer passo de 1 a 9
- ❌ Skipar o report consolidado

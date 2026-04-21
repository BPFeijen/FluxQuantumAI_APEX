# TASK: FASE 2.5b — GAMMA/DELTA Strategy Investigation (v2 — com ATS Docs)

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Docs path:** `C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs`
**Escopo:** **READ-ONLY investigation** em 3 fontes: código + logs + documentação.
**Tempo estimado:** 15-20 min
**Output:** `FASE_2_5b_GAMMA_DELTA_INVESTIGATION_<timestamp>.md`

---

## CRITICAL RULES

1. **READ-ONLY total.** Zero modificações.
2. **NÃO tocar em serviços.**
3. Apenas ler código + grep logs + ler docs ATS.

---

## CONTEXTO

Scope A do Fase 2 corrigiu os failure paths do ALPHA mas GAMMA/DELTA têm **gap de observabilidade pre-existente** (não têm `else:` branch com `EXEC_FAILED`).

Antes de decidir se corrigir ou aceitar silêncio, precisamos **três fontes de evidência**:

1. **Código:** o que GAMMA/DELTA fazem (implementação real)
2. **Logs:** se disparam em produção (histórico real)
3. **Documentação:** para que foram desenhados (intenção original)

As fontes 1+2 dizem-nos **como** e **quanto**. A fonte 3 diz-nos **porquê**.

---

## OBJECTIVO FINAL

Após estas 3 investigações, responder com confiança:

- **O que GAMMA foi desenhado para fazer?**
- **O que DELTA foi desenhado para fazer?**
- **Está a funcionar conforme desenhado?**
- **Merece o esforço de corrigir failure paths?**

---

## PASSO 1 — Listar documentos ATS disponíveis

```powershell
$docs_path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs"

Write-Host "=== Documents available in APEX_Docs ==="
if (Test-Path $docs_path) {
    Get-ChildItem $docs_path -Recurse -File | Where-Object {
        $_.Extension -in @(".md", ".txt", ".pdf", ".docx")
    } | Sort-Object FullName | ForEach-Object {
        "{0,10} B  |  {1}" -f $_.Length, $_.FullName
    }
} else {
    Write-Host "ERROR: path does not exist: $docs_path"
    Write-Host "Trying alternative paths..."
    Get-ChildItem "C:\FluxQuantumAI" -Recurse -Directory -Filter "*Docs*" -ErrorAction SilentlyContinue
    Get-ChildItem "C:\FluxQuantumAPEX" -Recurse -Directory -Filter "*Docs*" -ErrorAction SilentlyContinue
}
```

**Reportar:** caminho confirmado + lista de documentos (ficheiros .md, .txt, .pdf, .docx).

**Se o path `C:\FluxQuantumAPEX\APEX GOLD\APEX_Docs` não existe**, procurar alternativas (talvez `C:\FluxQuantumAI\docs`, `C:\FluxQuantumAI\APEX_Docs`, etc.) e reportar qual existe.

---

## PASSO 2 — Procurar GAMMA/DELTA nos docs

```powershell
Write-Host "=== Searching GAMMA references in ATS Docs ==="
Get-ChildItem $docs_path -Recurse -File -Include "*.md","*.txt" |
    Select-String -Pattern "GAMMA" -CaseSensitive | ForEach-Object {
        Write-Host ""
        Write-Host "File: $($_.Path)"
        Write-Host "Line $($_.LineNumber): $($_.Line.Trim())"
    }

Write-Host ""
Write-Host "=== Searching DELTA references in ATS Docs ==="
Get-ChildItem $docs_path -Recurse -File -Include "*.md","*.txt" |
    Select-String -Pattern "DELTA" -CaseSensitive | ForEach-Object {
        Write-Host ""
        Write-Host "File: $($_.Path)"
        Write-Host "Line $($_.LineNumber): $($_.Line.Trim())"
    }
```

**Para cada hit relevante:** ler mais contexto (ex: 20 linhas à volta) para entender contexto.

**Se hits em PDFs:** reportar nome do PDF para eu ler depois (PowerShell não lê PDF directamente sem módulo extra).

**Esperado:** docs ATS podem ter definição formal das estratégias — ALPHA (main), GAMMA (momentum stacking ou similar), DELTA (trend momentum review ou similar). Confirmar com texto real.

---

## PASSO 3 — Ler contexto expandido dos documentos que falam de GAMMA/DELTA

Para cada ficheiro com hit:

```powershell
# Exemplo — adaptar a cada ficheiro identificado
$doc_file = "<PATH DO DOC COM HIT>"
$keyword_line = <LINE NUMBER DO HIT>

# Ler 30 linhas antes + 50 linhas depois para ter contexto
$start = [Math]::Max(1, $keyword_line - 30)
$end = $keyword_line + 50

Write-Host "=== Context from $doc_file (lines $start to $end) ==="
Get-Content $doc_file | Select-Object -Skip ($start - 1) -First ($end - $start + 1) |
    ForEach-Object -Begin { $n = $start } -Process {
        "{0,5}: {1}" -f $n, $_
        $n++
    }
```

**Executar este bloco para cada documento com hit de GAMMA e cada documento com hit de DELTA.**

---

## PASSO 4 — Código: GAMMA function completa

```powershell
$live = "C:\FluxQuantumAI\live\event_processor.py"

# Localizar def que contém _gamma_exec (linha 3623)
$gamma_exec_line = 3623
$def_lines = (Get-Content $live | Select-String -Pattern "^\s*def " | Where-Object { $_.LineNumber -lt $gamma_exec_line }).LineNumber
$containing_def_line = ($def_lines | Measure-Object -Maximum).Maximum

Write-Host "=== GAMMA function starts at line: $containing_def_line ==="

# Ler desde def até _gamma_exec + 20 linhas
Get-Content $live | Select-Object -Skip ($containing_def_line - 1) -First ($gamma_exec_line + 20 - $containing_def_line + 1) |
    ForEach-Object -Begin { $n = $containing_def_line } -Process {
        "{0,5}: {1}" -f $n, $_
        $n++
    }
```

**Reportar:**
- Nome da função
- Docstring (se houver)
- Parâmetros
- Condições de entrada (ifs que filtram GAMMA)
- Qualquer constante GAMMA_* (thresholds)

---

## PASSO 5 — Código: DELTA function completa

Mesmo processo, mas usando `_delta_exec` (linha 3921):

```powershell
$delta_exec_line = 3921
$def_lines_d = (Get-Content $live | Select-String -Pattern "^\s*def " | Where-Object { $_.LineNumber -lt $delta_exec_line }).LineNumber
$containing_def_line_d = ($def_lines_d | Measure-Object -Maximum).Maximum

Write-Host "=== DELTA function starts at line: $containing_def_line_d ==="
Get-Content $live | Select-Object -Skip ($containing_def_line_d - 1) -First ($delta_exec_line + 20 - $containing_def_line_d + 1) |
    ForEach-Object -Begin { $n = $containing_def_line_d } -Process {
        "{0,5}: {1}" -f $n, $_
        $n++
    }
```

---

## PASSO 6 — Callsites de GAMMA/DELTA functions

Identificados nomes das funções nos Passos 4/5, procurar callsites:

```powershell
# Substituir <NOME_FUNCAO_GAMMA> pelo nome real encontrado
$func_g = "<NOME_FUNCAO_GAMMA>"
Write-Host "=== Callsites of $func_g ==="
Get-Content $live | Select-String -Pattern "$func_g" -Context 3,3 | ForEach-Object {
    Write-Host "`n--- Line $($_.LineNumber) ---"
    $_.Context.PreContext | ForEach-Object { Write-Host "    | $_" }
    Write-Host "  >>| $($_.Line)"
    $_.Context.PostContext | ForEach-Object { Write-Host "    | $_" }
}

# Mesma coisa para DELTA
$func_d = "<NOME_FUNCAO_DELTA>"
Write-Host "=== Callsites of $func_d ==="
# ... análogo
```

---

## PASSO 7 — Counts no decision_log.jsonl

```powershell
$decision_log = "C:\FluxQuantumAI\logs\decision_log.jsonl"

Write-Host "=== Trigger counts in decision_log.jsonl ==="

$alpha_count = (Select-String -Path $decision_log -Pattern '"ALPHA"' -SimpleMatch).Count
$gamma_count = (Select-String -Path $decision_log -Pattern '"GAMMA"' -SimpleMatch).Count
$delta_count = (Select-String -Path $decision_log -Pattern '"DELTA"' -SimpleMatch).Count

Write-Host "ALPHA count:  $alpha_count"
Write-Host "GAMMA count:  $gamma_count"
Write-Host "DELTA count:  $delta_count"
```

**Se GAMMA/DELTA count > 0:**

```powershell
if ($gamma_count -gt 0) {
    Write-Host ""
    Write-Host "=== First GAMMA entry ==="
    Select-String -Path $decision_log -Pattern '"GAMMA"' -SimpleMatch | Select-Object -First 1 | Format-List

    Write-Host ""
    Write-Host "=== Last GAMMA entry ==="
    Select-String -Path $decision_log -Pattern '"GAMMA"' -SimpleMatch | Select-Object -Last 1 | Format-List

    Write-Host ""
    Write-Host "=== GAMMA entries per day ==="
    Select-String -Path $decision_log -Pattern '"GAMMA"' -SimpleMatch | ForEach-Object {
        if ($_.Line -match '"timestamp":\s*"(\d{4}-\d{2}-\d{2})') {
            $matches[1]
        }
    } | Group-Object | Sort-Object Name | ForEach-Object {
        "{0}: {1}" -f $_.Name, $_.Count
    }
}

# Mesma coisa para DELTA
if ($delta_count -gt 0) {
    # ... análogo
}
```

---

## PASSO 8 — Logs adicionais

```powershell
$cont_log = "C:\FluxQuantumAI\logs\continuation_trades.jsonl"
if (Test-Path $cont_log) {
    $cg = (Select-String -Path $cont_log -Pattern "GAMMA" -SimpleMatch).Count
    $cd = (Select-String -Path $cont_log -Pattern "DELTA" -SimpleMatch).Count
    Write-Host "continuation_trades.jsonl — GAMMA=$cg, DELTA=$cd"
}

$trades_csv = "C:\FluxQuantumAI\logs\trades.csv"
if (Test-Path $trades_csv) {
    $tg = (Select-String -Path $trades_csv -Pattern "GAMMA").Count
    $td = (Select-String -Path $trades_csv -Pattern "DELTA").Count
    Write-Host "trades.csv — GAMMA=$tg, DELTA=$td"
}
```

---

## OUTPUT — Report

Criar `C:\FluxQuantumAI\FASE_2_5b_GAMMA_DELTA_INVESTIGATION_<timestamp>.md`:

```markdown
# FASE 2.5b — GAMMA/DELTA Investigation (3 sources)

**Timestamp:** <UTC>
**Mode:** READ-ONLY
**Sources:** Code + Logs + ATS Docs
**Purpose:** Understand GAMMA/DELTA purpose, actual usage, and design intent.

---

## 1. DOCUMENTATION EVIDENCE (what they were designed to do)

### ATS Docs path (confirmed)
`<confirmed path>`

### GAMMA in documentation

| Doc file | Line | Excerpt |
|---|---|---|
| <file> | <line> | <snippet> |

**Extended context (most relevant excerpt):**

```
<paste 30-50 lines around most relevant hit>
```

**Design intent (inferred from docs):**

<1-2 paragraphs summarizing what GAMMA is supposed to do, based on docs>

### DELTA in documentation

(same structure)

**Design intent (inferred from docs):**

<summary>

---

## 2. CODE EVIDENCE (what they actually do)

### GAMMA function

- **File:** `event_processor.py`
- **Function name:** `<name>`
- **Line range:** <start>-<end>
- **Docstring:** `<paste or "none">`
- **Parameters:** `<list>`

**Full function body:**

```python
<paste>
```

**Entry conditions (ifs that filter when GAMMA fires):**

<summary>

**Callsites:**

| Caller line | Context |
|---|---|
| <line> | <1-line context> |

### DELTA function

(same structure)

---

## 3. HISTORICAL EVIDENCE (if they actually fire)

### decision_log.jsonl counts

| Strategy | Count |
|---|---|
| ALPHA | <N> |
| GAMMA | <N> |
| DELTA | <N> |

### GAMMA — if fired

- First entry timestamp: <date>
- Last entry timestamp: <date>
- Per-day frequency:

| Day | Count |
|---|---|
| <date> | <N> |

### DELTA — if fired

(same structure)

### Other logs

- continuation_trades.jsonl: GAMMA=<N>, DELTA=<N>
- trades.csv: GAMMA=<N>, DELTA=<N>

---

## SYNTHESIS — cross-referencing 3 sources

### Does code match documentation intent?

- **GAMMA:** <YES/NO/PARTIAL>
- **DELTA:** <YES/NO/PARTIAL>

### Does actual usage match design intent?

- **GAMMA:** was designed to fire in X conditions. Historical log shows Y. Interpretation: <explanation>
- **DELTA:** (same)

### Status

| Aspect | GAMMA | DELTA |
|---|---|---|
| Has documentation | YES / NO | YES / NO |
| Has implementation | YES / NO | YES / NO |
| Fires in production | YES (N times) / NO | YES (N times) / NO |
| Consistent with docs | YES / NO / PARTIAL | YES / NO / PARTIAL |
| Dead code / GHOST | YES / NO | YES / NO |

---

## RECOMMENDATION

Based on 3-source evidence:

**Scenario A — Both GAMMA and DELTA are DEAD (count=0 in logs)**
→ Opção 1: Aceitar silêncio. Não gastar 30 min em código morto.

**Scenario B — At least one fires (count > 0)**
→ Opção 2: Corrigir failure paths em Fase 2.6. Consistência importa porque **já disparou antes e disparará novamente**.

**Scenario C — Docs dizem que devem disparar mas logs dizem que nunca disparam**
→ Há **bug maior**: estratégia existe no papel e no código mas não está a funcionar conforme desenhado. Escopo de investigação maior — fora desta fase.

**My recommendation:** Scenario <A/B/C> based on evidence above.

---

## Status

✅ Investigation complete.
Nenhuma modificação feita.
Aguardando decisão Barbara.
```

---

## PROIBIDO

- ❌ Modificar qualquer ficheiro
- ❌ Tocar em staging ou live
- ❌ Parar serviços
- ❌ Concluir sem evidência empírica das 3 fontes
- ❌ Inventar — se doc não existe, reportar "(não encontrado nos docs)"

---

## COMUNICAÇÃO FINAL

```
FASE 2.5b INVESTIGATION — COMPLETE
Docs path: <confirmed>
Report: <path>

GAMMA: docs=<YES/NO>, code_name=<name>, log_count=<N>
DELTA: docs=<YES/NO>, code_name=<name>, log_count=<N>

Recommendation: Scenario <A/B/C> → Opção <1/2/investigação>

Aguardando decisão Barbara.
```

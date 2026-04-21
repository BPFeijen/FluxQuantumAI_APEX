# TASK: FASE 2.5b — GAMMA/DELTA Strategy Investigation

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Escopo:** **READ-ONLY investigation**. Zero modificações.
**Tempo estimado:** 10-15 min
**Output:** `FASE_2_5b_GAMMA_DELTA_INVESTIGATION_<timestamp>.md`

---

## CRITICAL RULES

1. **READ-ONLY.** Zero `str_replace`, zero edits, zero novos ficheiros modificáveis em live.
2. **NÃO tocar em serviços.**
3. Apenas ler código + grep logs.

---

## CONTEXTO

Após Fase 2.5 audit, ficou claro que GAMMA e DELTA **não têm failure path simétrico ao ALPHA**. Mas ninguém sabe:
1. **O que GAMMA e DELTA realmente fazem** no código (tipo de trigger, propósito)
2. **Se alguma vez dispararam** em produção (ou se são dead code GHOST)

Esta fase responde a essas duas perguntas com evidência empírica: código + logs.

---

## OBJECTIVO

Responder com factos concretos:

1. **O que GAMMA faz?** (função, triggers, contexto chamado)
2. **O que DELTA faz?** (função, triggers, contexto chamado)
3. **GAMMA alguma vez disparou?** (contagem em decision_log)
4. **DELTA alguma vez disparou?** (contagem em decision_log)

---

## PASSO 1 — Procurar definição de GAMMA no código

```powershell
$live = "C:\FluxQuantumAI\live\event_processor.py"

Write-Host "=== GAMMA references in event_processor.py ==="
Get-Content $live | Select-String -Pattern "GAMMA" -CaseSensitive | ForEach-Object {
    "{0,5}: {1}" -f $_.LineNumber, $_.Line.Trim()
}
```

**Esperado:** Múltiplas linhas onde GAMMA aparece. Identificar:
- Constante/thresholds (ex: `GAMMA_MIN_RR`, `GAMMA_TP1_FACTOR`)
- Função que contém `_gamma_exec = self._open_on_all_accounts(...)` — qual nome?
- Condições de trigger que chamam essa função

---

## PASSO 2 — Ler a função que contém GAMMA completa

```powershell
# Localizar function def mais próxima antes da linha 3623 (_gamma_exec)
$gamma_exec_line = 3623

# Procurar última linha com "def " antes de 3623
$def_lines = (Get-Content $live | Select-String -Pattern "^\s*def " | Where-Object { $_.LineNumber -lt $gamma_exec_line }).LineNumber
$containing_def_line = ($def_lines | Measure-Object -Maximum).Maximum

Write-Host "=== Function containing GAMMA starts at line: $containing_def_line ==="
Write-Host ""

# Ler desde a def até _gamma_exec + 20 linhas (para ver o body completo)
$end_line = $gamma_exec_line + 20
Get-Content $live | Select-Object -Skip ($containing_def_line - 1) -First ($end_line - $containing_def_line + 1) |
    ForEach-Object -Begin { $n = $containing_def_line } -Process {
        "{0,5}: {1}" -f $n, $_
        $n++
    }
```

**Esperado:** Assinatura da função + body. Identificar:
- Nome da função (ex: `_process_gamma_setup`, `_check_gamma_strategy`, etc.)
- Parâmetros
- Docstring (se houver) — normalmente descreve propósito
- Condições de entrada (ifs que filtram quando GAMMA dispara)
- Tipo de trigger (novo trade? continuação? hedge?)

---

## PASSO 3 — Procurar onde essa função é chamada

```powershell
# Assume que identificaste o nome da função no Passo 2
# Ex: se for "_gamma_check", procurar callsites
# REPLACE NOME ABAIXO com o nome real identificado no Passo 2

$gamma_func_name = "<PREENCHER COM NOME REAL>"  # <-- editar após Passo 2

Write-Host "=== Callsites of $gamma_func_name ==="
Get-Content $live | Select-String -Pattern "$gamma_func_name" -Context 3,3 | ForEach-Object {
    if ($_.LineNumber -ne $containing_def_line) {  # skip the def itself
        Write-Host "`n--- Line $($_.LineNumber) ---"
        $_.Context.PreContext | ForEach-Object { Write-Host "    | $_" }
        Write-Host "  >>| $($_.Line)"
        $_.Context.PostContext | ForEach-Object { Write-Host "    | $_" }
    }
}
```

**Esperado:** Ver quem chama essa função e em que condições. Isto clarifica o **propósito** (entrada nova? continuation? hedge?).

---

## PASSO 4 — Repetir PASSO 1, 2, 3 para DELTA

```powershell
Write-Host "=== DELTA references in event_processor.py ==="
Get-Content $live | Select-String -Pattern "DELTA" -CaseSensitive | ForEach-Object {
    "{0,5}: {1}" -f $_.LineNumber, $_.Line.Trim()
}
```

```powershell
$delta_exec_line = 3921
$def_lines_d = (Get-Content $live | Select-String -Pattern "^\s*def " | Where-Object { $_.LineNumber -lt $delta_exec_line }).LineNumber
$containing_def_line_d = ($def_lines_d | Measure-Object -Maximum).Maximum

Write-Host "=== Function containing DELTA starts at line: $containing_def_line_d ==="
Get-Content $live | Select-Object -Skip ($containing_def_line_d - 1) -First ($delta_exec_line + 20 - $containing_def_line_d + 1) |
    ForEach-Object -Begin { $n = $containing_def_line_d } -Process {
        "{0,5}: {1}" -f $n, $_
        $n++
    }
```

Idem para callsites da função DELTA.

---

## PASSO 5 — Procurar disparos reais no decision_log.jsonl

```powershell
$decision_log = "C:\FluxQuantumAI\logs\decision_log.jsonl"

Write-Host "=== GAMMA/DELTA trigger counts in decision_log.jsonl ==="

# Contagem bruta
$gamma_count = (Select-String -Path $decision_log -Pattern '"GAMMA"' -SimpleMatch).Count
$delta_count = (Select-String -Path $decision_log -Pattern '"DELTA"' -SimpleMatch).Count
$alpha_count = (Select-String -Path $decision_log -Pattern '"ALPHA"' -SimpleMatch).Count

Write-Host "ALPHA count:  $alpha_count"
Write-Host "GAMMA count:  $gamma_count"
Write-Host "DELTA count:  $delta_count"

# Se alguma vez disparou GAMMA, mostrar exemplo
if ($gamma_count -gt 0) {
    Write-Host ""
    Write-Host "=== First GAMMA entry in log ==="
    Select-String -Path $decision_log -Pattern '"GAMMA"' -SimpleMatch | Select-Object -First 1
    Write-Host ""
    Write-Host "=== Last GAMMA entry in log ==="
    Select-String -Path $decision_log -Pattern '"GAMMA"' -SimpleMatch | Select-Object -Last 1
}

if ($delta_count -gt 0) {
    Write-Host ""
    Write-Host "=== First DELTA entry in log ==="
    Select-String -Path $decision_log -Pattern '"DELTA"' -SimpleMatch | Select-Object -First 1
    Write-Host ""
    Write-Host "=== Last DELTA entry in log ==="
    Select-String -Path $decision_log -Pattern '"DELTA"' -SimpleMatch | Select-Object -Last 1
}
```

**Interpretação:**
- `count = 0` → **dead code, nunca disparou** → aceitar silêncio (Opção 1) é racional
- `count > 0` mas pequeno (ex: <50) → **raramente dispara** → decisão Barbara baseada em criticidade
- `count` grande (>100) → **dispara regularmente** → **corrigir failure path é importante** (Opção 2)

---

## PASSO 6 — Procurar GAMMA/DELTA em trade_record ou continuation log

```powershell
Write-Host "=== GAMMA/DELTA in other logs ==="

# Continuation log
$cont_log = "C:\FluxQuantumAI\logs\continuation_trades.jsonl"
if (Test-Path $cont_log) {
    $cont_gamma = (Select-String -Path $cont_log -Pattern "GAMMA" -SimpleMatch).Count
    $cont_delta = (Select-String -Path $cont_log -Pattern "DELTA" -SimpleMatch).Count
    Write-Host "continuation_trades.jsonl — GAMMA=$cont_gamma, DELTA=$cont_delta"
}

# Trades CSV (se existir)
$trades_csv = "C:\FluxQuantumAI\logs\trades.csv"
if (Test-Path $trades_csv) {
    $trade_gamma = (Select-String -Path $trades_csv -Pattern "GAMMA").Count
    $trade_delta = (Select-String -Path $trades_csv -Pattern "DELTA").Count
    Write-Host "trades.csv — GAMMA=$trade_gamma, DELTA=$trade_delta"
}
```

---

## OUTPUT — Report

Criar `C:\FluxQuantumAI\FASE_2_5b_GAMMA_DELTA_INVESTIGATION_<timestamp>.md`:

```markdown
# FASE 2.5b — GAMMA/DELTA Investigation

**Timestamp:** <UTC>
**Mode:** READ-ONLY
**Purpose:** Understand what GAMMA and DELTA are, and whether they fire in production.

## GAMMA

### Code references

All lines containing "GAMMA" in event_processor.py:

```
<paste list>
```

### Containing function

Function name: `<name>`
Starts at line: <N>
Signature: `<def ...>`
Docstring: <paste if exists, "(no docstring)" if not>

Full function body:

```python
<paste>
```

### Callsites

| Caller line | Context |
|---|---|
| <line> | <summary> |

### Purpose (inferred from code)

<description of what GAMMA does based on code reading>

### Historical trigger count

- decision_log.jsonl: X occurrences
- continuation_trades.jsonl: Y occurrences (if file exists)
- trades.csv: Z occurrences (if file exists)

**First entry timestamp:** <if >0>
**Last entry timestamp:** <if >0>

## DELTA

(same structure)

## Summary table

| Aspect | GAMMA | DELTA |
|---|---|---|
| Purpose | <1-line desc> | <1-line desc> |
| Trigger type | new entry / continuation / hedge / unknown | same |
| Disparos decision_log | X | Y |
| Status | DEAD / RARE / ACTIVE | same |

## Recommendation

Based on evidence:

- **If both are DEAD (count=0 em logs):** Opção 1 (aceitar silêncio) é apropriada — não gastar 30 min em código morto.
- **If any is RARE (count 1-50):** Opção 2 (corrigir failure paths) é prudente — raro não é zero, ainda vale consistência.
- **If any is ACTIVE (count >50):** Opção 2 é **necessária** — silêncio em código activo é bug grave.

## Status

✅ Investigation complete, read-only.
Nenhuma modificação feita.
Aguardando decisão Barbara.
```

---

## PROIBIDO

- ❌ Modificar qualquer ficheiro
- ❌ Tocar em staging ou live
- ❌ Parar serviços
- ❌ Concluir sem evidência empírica (precisa de counts reais + código real)
- ❌ Inventar propósitos — inferir estritamente do código/docstring

---

## COMUNICAÇÃO FINAL

```
FASE 2.5b INVESTIGATION — COMPLETE
Report: <path>

GAMMA purpose: <summary>
DELTA purpose: <summary>
GAMMA count in logs: <N>
DELTA count in logs: <N>
Recommendation: Opção <1 ou 2>

Aguardando decisão Barbara.
```

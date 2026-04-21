# TASK: FASE 2.5 — Verificação GAMMA/DELTA Failure Branches

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Escopo:** **READ-ONLY ANALYSIS**. Nenhuma modificação de código.
**Tempo estimado:** 10-15 min
**Output:** `FASE_2_5_GAMMA_DELTA_AUDIT_<timestamp>.md`

---

## CRITICAL RULES

1. **READ-ONLY.** Zero `str_replace`, zero edits. Apenas leitura + análise.
2. **NÃO tocar em staging nem em live.**
3. **NÃO tocar em serviços.**
4. Apenas procurar, ler, documentar.

---

## CONTEXTO

No Fase 2 Scope A, foram corrigidas 5 mudanças no `event_processor.py`:
- M1-M3: ALPHA branch (GO + EXECUTED + EXEC_FAILED)
- M4: GAMMA `success_any=True` branch — `notify_decision()` → `notify_execution()`
- M5: DELTA `success_any=True` branch — `notify_decision()` → `notify_execution()`

**Observação de Claude durante audit:** Scope A corrigiu apenas o path de SUCESSO de GAMMA/DELTA. **Não foi verificado se há paths de FAILURE** (equivalentes ao EXEC_FAILED da ALPHA) que ainda usem `notify_decision()` antigo, o que perpetuaria o bug BLOCK-vs-GO nestes branches.

**Esta fase verifica isso.**

---

## OBJECTIVO

Responder com evidência concreta de código a 3 perguntas:

1. **GAMMA tem branch de failure?** (i.e. quando `_gamma_exec.get("success_any", False)` é `False`, há lógica específica?)
2. **DELTA tem branch de failure?** (análogo)
3. **Se sim, esses branches chamam `tg.notify_decision()`?** Se chamam, é bug residual.

---

## PASSO 1 — Ler contexto GAMMA completo

Target file: `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\event_processor.py`
(**staging**, onde já foram aplicadas M1-M5 do Scope A — não tocar no live)

```powershell
$staging = "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\event_processor.py"

Write-Host "=== GAMMA branch context (50 linhas antes + 50 depois do success_any) ==="

# Procurar todas as ocorrências de "_gamma_exec" e listar contexto
Get-Content $staging | Select-String -Pattern "_gamma_exec" -Context 0,0 | ForEach-Object {
    Write-Host "`n--- Match at line $($_.LineNumber) ---"
    Write-Host $_.Line
}
```

Reportar TODAS as ocorrências de `_gamma_exec` com número de linha.

---

## PASSO 2 — Ler bloco GAMMA completo após success_any

```powershell
# Localizar a linha com "if _gamma_exec.get("success_any"
$gamma_if_line = (Get-Content $staging | Select-String -Pattern 'if _gamma_exec\.get\("success_any"' -List).LineNumber

Write-Host "GAMMA success_any check at line: $gamma_if_line"

# Ler do "if _gamma_exec" até a próxima função (def) ou 80 linhas
$start_line = $gamma_if_line
$end_line = $gamma_if_line + 80

Write-Host "=== GAMMA complete block (lines $start_line to $end_line) ==="
Get-Content $staging | Select-Object -Skip ($start_line - 1) -First 80 |
    ForEach-Object -Begin { $n = $start_line } -Process {
        "{0,5}: {1}" -f $n, $_
        $n++
    }
```

**Análise a fazer:**

Procurar no bloco retornado:
- Existe `else:` após `if _gamma_exec.get("success_any", False):`?
- Se existe, o que está dentro desse else?
- Existe `tg.notify_decision()` ou `tg.notify_execution()` dentro do else?
- Existe escrita de `action = "EXEC_FAILED"` para GAMMA? (procurar `EXEC_FAILED` no bloco)

---

## PASSO 3 — Ler bloco DELTA completo após success_any

```powershell
# Análogo ao Passo 2, para DELTA
$delta_if_line = (Get-Content $staging | Select-String -Pattern 'if _delta_exec\.get\("success_any"' -List).LineNumber

Write-Host "DELTA success_any check at line: $delta_if_line"

Write-Host "=== DELTA complete block (lines $delta_if_line to $delta_if_line+80) ==="
Get-Content $staging | Select-Object -Skip ($delta_if_line - 1) -First 80 |
    ForEach-Object -Begin { $n = $delta_if_line } -Process {
        "{0,5}: {1}" -f $n, $_
        $n++
    }
```

**Análise análoga ao Passo 2.**

---

## PASSO 4 — Procurar notify_decision residuais globais

```powershell
Write-Host "=== All remaining tg.notify_decision() calls em event_processor.py ==="

Get-Content $staging | Select-String -Pattern "tg\.notify_decision\(\)" -Context 2,2 | ForEach-Object {
    Write-Host "`n--- Line $($_.LineNumber) ---"
    $_.Context.PreContext | ForEach-Object { Write-Host "  | $_" }
    Write-Host "  >>| $($_.Line)"
    $_.Context.PostContext | ForEach-Object { Write-Host "  | $_" }
}
```

**Esperado:** 3 ocorrências (M1 GO signal + M1 BLOCK — linhas ~2363-2370). Se houver mais, são potenciais bugs residuais.

---

## PASSO 5 — Procurar notify_execution para comparar

```powershell
Write-Host "=== All tg.notify_execution() calls em event_processor.py ==="

Get-Content $staging | Select-String -Pattern "tg\.notify_execution\(\)" -Context 2,2 | ForEach-Object {
    Write-Host "`n--- Line $($_.LineNumber) ---"
    $_.Context.PreContext | ForEach-Object { Write-Host "  | $_" }
    Write-Host "  >>| $($_.Line)"
    $_.Context.PostContext | ForEach-Object { Write-Host "  | $_" }
}
```

**Esperado após Scope A:** 5 ocorrências:
- M2: ALPHA EXECUTED (~linha 2519)
- M3: ALPHA EXEC_FAILED (~linha 2591)
- M4: GAMMA success (~linha 3631)
- M5: DELTA success (~linha 3929)
- ? (possível outro — GAMMA ou DELTA failure?)

Se forem 4, significa GAMMA/DELTA **não** têm notify_execution no failure path. Duas possibilidades:
- A) Nunca tiveram notification no failure path (historicamente silencioso) — aceitável
- B) Ainda usam notify_decision() antigo — **bug residual**

---

## PASSO 6 — Comparar paths GAMMA vs ALPHA

Executar análise comparativa:

Para ALPHA, o pattern completo no staging deve ser:
```python
# success path
_decision_dict["decision"]["action"] = "EXECUTED"
self._write_decision(_decision_dict)
tg.notify_execution()
# ...
# failure path (linhas 2575-2591)
_decision_dict["decision"]["action"] = "EXEC_FAILED"
# ...
tg.notify_execution()
```

**Para GAMMA, verificar se existe bloco equivalente:**
- Existe escrita `_decision_dict["decision"]["action"] = "EXEC_FAILED"` com label GAMMA?
- Se sim, tem `tg.notify_execution()` ou `tg.notify_decision()`?

**Para DELTA, análogo.**

---

## OUTPUT — Report

Criar `C:\FluxQuantumAI\FASE_2_5_GAMMA_DELTA_AUDIT_<timestamp>.md`:

```markdown
# FASE 2.5 — GAMMA/DELTA Failure Branches Audit

**Timestamp:** <UTC>
**Purpose:** Verify if GAMMA and DELTA branches have failure paths that still use legacy notify_decision() (potential residual bug).

## GAMMA Analysis

### Occurrences of `_gamma_exec`

- Line X: context
- Line Y: context
- ...

### Complete GAMMA execution block (starting at line N)

```python
<paste of lines>
```

### Findings

- Has `else:` branch after `if _gamma_exec.get("success_any", False):` ? YES/NO
- If YES, contents of else: <describe>
- Does GAMMA failure path write `action = "EXEC_FAILED"` ? YES/NO
- Does GAMMA failure path call Telegram? If yes, which function (notify_decision OR notify_execution)?

### Verdict

- ✅ GAMMA failure path handled correctly (notifies via notify_execution) — no fix needed
- ⚠️ GAMMA failure path is SILENT (no notification) — decision for Barbara if fix needed
- ❌ GAMMA failure path uses LEGACY notify_decision — **RESIDUAL BUG** — needs fix in Fase 2.5

## DELTA Analysis

(same structure)

## Summary table

| Branch | Success path | Failure path | Status |
|---|---|---|---|
| ALPHA | notify_execution ✅ | notify_execution ✅ | OK |
| GAMMA | notify_execution ✅ | <answer> | <OK/WARN/BUG> |
| DELTA | notify_execution ✅ | <answer> | <OK/WARN/BUG> |

## Residual tg.notify_decision() calls

Total found: N

| Line | Context | Expected? |
|---|---|---|
| 2363 | BLOCK branch | ✅ correct (BLOCK uses notify_decision) |
| 2370 | GO signal | ✅ correct (GO uses notify_decision) |
| ... | ... | ... |

## Recommendation

Uma de três:

1. **No action needed** — GAMMA/DELTA failure paths são silenciosos ou já correctos
2. **Apply M6+M7 in Fase 2.6** — corrigir failure paths
3. **Barbara decides** — discutir antes de avançar
```

---

## PROIBIDO

- ❌ Modificar ficheiros (zero edits)
- ❌ Tocar em live ou staging (read-only inspection)
- ❌ Parar serviços
- ❌ Tirar conclusões sem ler o código completo
- ❌ Inventar conteúdo de branches que não existem
- ❌ Continuar para Fase 3 (deploy)

---

## COMUNICAÇÃO FINAL

```
FASE 2.5 GAMMA/DELTA AUDIT — <STATUS>
Report: <path>

GAMMA failure path: <OK/WARN/BUG>
DELTA failure path: <OK/WARN/BUG>
Residual notify_decision calls: <count>

Aguardando Barbara + Claude audit decision.
```

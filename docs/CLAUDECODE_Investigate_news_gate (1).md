# TASK: INVESTIGAR por que `apex_news_gate` NÃO está activo em produção

**Para:** ClaudeCode
**De:** Claude + Barbara
**Tempo cap:** 20-30 min
**Output:** Reporte conciso

---

## CONTEXTO ACTUALIZADO

**Barbara confirmou empiricamente via Telegram que:**

✅ **DefenseMode ACTIVO** — mensagens "DEFENSE MODE — ENTRY_BLOCK" recebidas várias vezes por dia
✅ **StatGuardrail ACTIVO** — mensagens "GUARDRAIL_STALE_DATA" recebidas
❌ **news_gate INACTIVO** — **NUNCA recebeu mensagens relacionadas a NFP/CPI/FOMC**

**Conclusão:** os 4 "gaps de sys.path" que reportaste estão errados em 2 casos. DefenseMode e StatGuardrail funcionam (há path manipulation que não detectaste). **Apenas news_gate tem gap genuíno.**

**Foco:** descobrir porquê news_gate especificamente está off, se pode ser activado, e qual o risco.

---

## CRITICAL RULES

1. **READ-ONLY absoluto.** Zero mudanças em código, settings, serviços.
2. **Tempo cap:** 30 min. Se não encontrar conclusão clara, reporta "inconclusive".
3. **Factual sobre empírico.** Barbara observa o sistema real há meses. Se a tua análise estática contradiz a evidência dela, **confia na dela e investiga porquê o código diz outra coisa**.
4. **Não especular.** Se não há evidência para uma hipótese, diz "no evidence".

---

## PASSO 1 — Como DefenseMode e StatGuardrail funcionam apesar do sys.path "gap"

**CRÍTICO:** antes de investigar news_gate, entende **como** DefenseMode/StatGuardrail conseguem funcionar.

Se eles funcionam mesmo estando em `APEX_Anomaly/` (fora do sys.path default), **deve haver path manipulation algures**. Descobre onde.

### Procurar por path manipulation em TODO o código relevante

```powershell
$py_files = @(
    "C:\FluxQuantumAI\run_live.py",
    "C:\FluxQuantumAI\live\*.py",
    "C:\FluxQuantumAI\*.py"
)

Write-Host "=== sys.path manipulations ==="
foreach ($pattern in $py_files) {
    Get-ChildItem $pattern -ErrorAction SilentlyContinue |
        Select-String -Pattern "sys\.path\.(insert|append)" |
        Format-Table Path, LineNumber, Line -Wrap
}

Write-Host ""
Write-Host "=== PYTHONPATH references ==="
foreach ($pattern in $py_files) {
    Get-ChildItem $pattern -ErrorAction SilentlyContinue |
        Select-String -Pattern "PYTHONPATH" |
        Format-Table Path, LineNumber, Line -Wrap
}
```

### Procurar como DefenseMode é importado/chamado em produção

```powershell
Write-Host "=== Where DefenseMode is imported/used ==="
Get-ChildItem "C:\FluxQuantumAI" -Recurse -Filter "*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "DefenseMode|defense_mode|ENTRY_BLOCK.*Microstructure" |
    Select-Object -First 30 |
    Format-Table Path, LineNumber, Line -Wrap
```

### Procurar como StatGuardrail é chamado

```powershell
Write-Host "=== Where GUARDRAIL_STALE_DATA message originates ==="
Get-ChildItem "C:\FluxQuantumAI" -Recurse -Filter "*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "GUARDRAIL_STALE_DATA|StatGuardrail|guardrail_stale" |
    Select-Object -First 30 |
    Format-Table Path, LineNumber, Line -Wrap
```

**Output esperado:** devem aparecer imports/calls que mostram **onde** esses módulos estão sendo carregados. Comparar com estrutura de directorias vai revelar o mecanismo de path manipulation.

---

## PASSO 2 — news_gate especificamente

### 2.1 Como DEVIA ser importado em produção

Primeiro, confirma onde no código está a tentativa de import:

```powershell
Write-Host "=== apex_news_gate imports in live code ==="
Get-ChildItem "C:\FluxQuantumAI" -Recurse -Filter "*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "apex_news_gate|ApexNewsGate|from.*news_gate|import.*news_gate" |
    Format-Table Path, LineNumber, Line -Wrap
```

Quais ficheiros tentam importar? Com que `from X import Y` syntax?

### 2.2 Comparar com DefenseMode — o mesmo mecanismo funciona?

Se DefenseMode está em `APEX_Anomaly/inference/` e funciona, e news_gate está em `APEX_News/` e não funciona:
- **É o mesmo tipo de path?** Ambos estão em `C:\FluxQuantumAPEX\APEX GOLD\`?
- **É a mesma manipulação de sys.path?** Se DefenseMode usa algum mecanismo (ex: `sys.path.insert` dinâmico em alguma função init), news_gate também deveria funcionar pelo mesmo mecanismo.
- **Ou tem lógica diferente?**

### 2.3 Tentar importar news_gate manualmente (read-only, sessão isolada)

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -c "
import sys
# Replicar o sys.path EXACTO que run_live.py monta
sys.path.insert(0, r'C:\FluxQuantumAI')
# Se encontraste outras manipulations de sys.path no PASSO 1, adicionar aqui também

print('=== sys.path ===')
for p in sys.path:
    print(f'  {p}')

print()
print('=== Try import apex_news_gate ===')
try:
    from apex_news_gate import news_gate
    print('IMPORT OK')
    print(f'Type: {type(news_gate)}')
    print(f'Has check_score: {hasattr(news_gate, \"check_score\")}')
    print(f'Has block_entry: {hasattr(news_gate, \"block_entry\")}')
    print(f'Has exit_all: {hasattr(news_gate, \"exit_all\")}')
except ImportError as e:
    print(f'IMPORT_FAIL (ImportError): {e}')
except Exception as e:
    print(f'IMPORT_FAIL ({type(e).__name__}): {e}')
"
```

Se falhar, **qual o erro específico?**

### 2.4 Se import falha, tentar com path manual adicionado

```powershell
& $py -c "
import sys
sys.path.insert(0, r'C:\FluxQuantumAI')
sys.path.insert(0, r'C:\FluxQuantumAPEX\APEX GOLD\APEX_News')

try:
    from apex_news_gate import news_gate
    print('IMPORT_OK with manual path')
except Exception as e:
    print(f'IMPORT_FAIL even with manual path: {type(e).__name__}: {e}')
"
```

Se isto funciona, **o fix é literalmente adicionar 1 linha no sys.path**.

Se falha mesmo com path manual, há outro problema (API key, dependências, bug no código).

### 2.5 Verificar API key TradingEconomics

```powershell
$config_path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\config\news_config.yaml"
if (Test-Path $config_path) {
    Write-Host "=== news_config.yaml EXISTS ==="
    $content = Get-Content $config_path -Raw
    
    # NÃO imprimir content completo (secrets). Só verificar presença de keys.
    $te_has_key = $content -match "trading_economics:[\s\S]*?api_key:\s*['\`"]?[^\s'`"]+['\`"]?"
    $av_has_key = $content -match "alpha_vantage:[\s\S]*?api_key:\s*['\`"]?[^\s'`"]+['\`"]?"
    
    Write-Host "TradingEconomics API key configured: $te_has_key"
    Write-Host "Alpha Vantage API key configured: $av_has_key"
    
    # Mostrar structure YAML keys (não values)
    Write-Host ""
    Write-Host "=== YAML top-level structure ==="
    $content -split "`n" | Where-Object { $_ -match "^\w" } | Select-Object -First 10
} else {
    Write-Host "news_config.yaml NOT FOUND at: $config_path"
}
```

### 2.6 Logs históricos — quando "ApexNewsGate not available" apareceu?

```powershell
Write-Host "=== Timeline of 'ApexNewsGate not available' in logs ==="

$log_paths = @(
    "C:\FluxQuantumAI\logs\service_stdout.log",
    "C:\FluxQuantumAI\logs\service_stdout.log.*",
    "C:\FluxQuantumAI\logs\*.log"
)

foreach ($path in $log_paths) {
    Get-ChildItem $path -ErrorAction SilentlyContinue | ForEach-Object {
        $matches_in_file = Select-String -Path $_.FullName -Pattern "ApexNewsGate|news_gate|NewsGate" -ErrorAction SilentlyContinue
        if ($matches_in_file) {
            Write-Host ""
            Write-Host "--- $($_.Name) ($($matches_in_file.Count) matches) ---"
            $matches_in_file | Select-Object -First 5 | Format-Table LineNumber, Line -Wrap
            Write-Host "First match: $($matches_in_file[0].Line.Trim())"
            Write-Host "Last match: $($matches_in_file[-1].Line.Trim())"
        }
    }
}
```

**Se logs mostram sempre "not available"** desde início do log disponível → nunca esteve activo (bug inicial).

**Se logs mostram transition** (antes funcionava, depois deixou de funcionar) → foi desligado em data específica.

### 2.7 Git history (se repo git existe)

```powershell
cd C:\FluxQuantumAI
if (Test-Path ".git") {
    Write-Host "=== Git history of run_live.py (last 30 commits) ==="
    git log --oneline --follow -30 run_live.py
    
    Write-Host ""
    Write-Host "=== Commits mentioning news_gate ==="
    git log --oneline --all --grep="news" --grep="NewsGate" --grep="apex_news" -i
    
    Write-Host ""
    Write-Host "=== Commits mentioning sys.path ==="
    git log --oneline --all --grep="sys.path" --grep="PYTHONPATH" -i
}
```

---

## PASSO 3 — Hipóteses a distinguir

| Hipótese | Evidência confirmatória |
|---|---|
| **H1 — Bug no sys.path** (nunca esteve correcto) | Logs sempre mostram "not available" desde início. Import manual com path funciona. |
| **H2 — Foi desligado deliberadamente** | Logs mostram transition date. Git log mostra commit de remoção. |
| **H3 — API key inválida/expirada** | Import OK mas falha em runtime. Config sem key ou key inválida. |
| **H4 — Dependência Python missing** | Import falha com `ModuleNotFoundError` que não é `apex_news_gate` mas outro módulo. |
| **H5 — Bug no código do módulo** | Import falha com `SyntaxError`, `ImportError interno`, etc. |
| **H6 — Razão documentada** | Existe doc que explica "news_gate desactivado porque X" |

---

## OUTPUT OBRIGATÓRIO

```markdown
# news_gate Investigation — Result

## Path manipulation mechanism (Passo 1)

How DefenseMode/StatGuardrail work despite APEX_Anomaly not in default sys.path:
[describe mechanism — ex: sys.path.insert in module X, PYTHONPATH env var, etc.]

## news_gate specific findings (Passo 2)

### Import attempts
- Default sys.path (from run_live.py perspective): [OK / FAIL + error]
- With manual APEX_News path added: [OK / FAIL + error]

### API key status
- TradingEconomics: [configured / missing / invalid]
- Alpha Vantage: [configured / missing / invalid]

### Logs timeline
- Earliest "not available" message: [date/time]
- Latest "not available" message: [date/time]
- Any "loaded" messages ever? [yes/no + date]
- Pattern: [always off / transition at date X]

### Git history (if available)
- Relevant commits: [list + dates]
- Pattern: [never wired / intentional removal / refactor]

## Hypothesis confirmed: [H1/H2/H3/H4/H5/H6]

## Recommendation

### To activate (if safe):
[list of concrete steps + risks]

### Do NOT activate if:
[conditions under which it would be risky]

## Open questions for Barbara+Claude

[anything inconclusive that needs input]
```

---

## COMUNICAÇÃO FINAL

```
news_gate INVESTIGATION COMPLETE
Duration: X min
Hypothesis confirmed: [X]
Recommendation: [activate / don't activate / need more info]
Risk if activate: [low/medium/high + why]
Report: [path]
```

**Depois de reportar, parar e aguardar decisão Barbara+Claude.**

**NÃO tomar acção sobre sys.path sem aprovação explícita.**

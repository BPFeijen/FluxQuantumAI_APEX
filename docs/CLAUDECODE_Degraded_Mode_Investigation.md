# TASK: INVESTIGAR 4 features broken em produção APEX

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Escopo:** Investigação READ-ONLY de 4 features broken + 1 mistério adicional
**Tempo cap:** 60-90 min
**Output:** `C:\FluxQuantumAI\backlog\degraded_mode_investigation_<timestamp>.md`

---

## CONTEXTO

Descobriste no último report que 4 features estão broken em produção desde/após Fase 7 restart:

| Feature | Estado pré-investigação |
|---|---|
| DefenseMode | Funcionava via sys.modules cache, quebrou após restart |
| V4 IcebergInference | Funcionava via cache, quebrou após restart |
| StatGuardrail | **NUNCA funcionou** (15/15 matches "not available") |
| ApexNewsGate | **NUNCA funcionou** (6/6 matches "not available") |

**Contexto histórico (confirmado pela Barbara):**
Sessão Claude anterior planeou refactoring `APEX_*` → `APEX_GC_*` (consistência com `APEX_GC_Signal` já existente). **Código foi alterado para apontar aos novos nomes**, mas **directorias nunca foram criadas/movidas**. Filesystem mantém `APEX_News`, `APEX_Anomaly`, `APEX_Iceberg`.

**Barbara quer decidir caminho de fix APENAS depois de investigação completa das 4 features.** Não executar nada.

---

## CRITICAL RULES

1. **READ-ONLY absoluto.** Zero mudanças em código, settings, filesystem, serviços.
2. **Factual sobre empírico.** Se a tua análise estática contradiz comportamento observado, **confia no empírico e investiga porquê o código diz outra coisa**.
3. **Não especular.** Se não há evidência para uma hipótese, diz "no evidence found".
4. **Tempo cap:** 90 min total. Se algum módulo demorar >20 min, para nesse módulo e reporta parcial.
5. **Ao fim, produzir reporte único consolidado.** Não 4 reportes separados.

---

## MÓDULO 1 — StatGuardrail (PRIORIDADE ALTA)

**Mistério:** Mesmo quando directorias `APEX_GC_*` não existem (igual aos outros), StatGuardrail **nunca funcionou** (nem via cache). Outros módulos funcionaram. **Qual diferença?**

### Passos de investigação

**1.1** Localizar código-fonte do StatGuardrail:
```powershell
# Procurar ficheiro que define StatGuardrail
Get-ChildItem "C:\FluxQuantumAPEX" -Recurse -Filter "*.py" |
    Select-String -Pattern "class StatGuardrail|def get_guardrail_status" |
    Format-Table Path, LineNumber, Line -Wrap
```

**1.2** Verificar como é importado/usado:
```powershell
# Procurar onde é importado em código LIVE
Get-ChildItem "C:\FluxQuantumAI" -Recurse -Filter "*.py" |
    Select-String -Pattern "StatGuardrail|get_guardrail_status|grenadier_guardrail" |
    Format-Table Path, LineNumber, Line -Wrap
```

**1.3** Ver `grenadier_guardrail.py:35` e arredores (o que faz sys.path.insert):
```powershell
Get-Content "C:\FluxQuantumAI\grenadier_guardrail.py" | Select-Object -Skip 25 -First 50
```

Perguntas a responder:
- `grenadier_guardrail.py` importa o quê exactamente?
- Qual é o try/except block que captura o ImportError?
- **O erro sempre foi "not available" ou é erro diferente?** (procurar stack trace nos logs)

**1.4** Ver logs exactos do StatGuardrail:
```powershell
$stderr_log = "C:\FluxQuantumAI\logs\service_stderr.log"
# Contexto em torno de cada "not available" de StatGuardrail
Select-String -Path $stderr_log -Pattern "StatGuardrail|guardrail.*not available|grenadier.*available" -Context 3,3 |
    Select-Object -First 5 |
    ForEach-Object { $_.Line; $_.Context.PreContext; $_.Context.PostContext; "---" }
```

**1.5** Tentar import manual numa sessão Python isolada:
```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -c "
import sys
# Replicar sys.path production
sys.path.insert(0, r'C:\FluxQuantumAI')
# Path apontado pelo código (broken)
sys.path.insert(0, r'C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly')
try:
    from grenadier_guardrail import get_guardrail_status
    print('IMPORT_OK (broken path)')
except Exception as e:
    print(f'FAIL broken: {type(e).__name__}: {e}')

sys.modules.pop('grenadier_guardrail', None)

# Path real (fixed)
sys.path.insert(0, r'C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly')
try:
    from grenadier_guardrail import get_guardrail_status
    print('IMPORT_OK (fixed path)')
except Exception as e:
    print(f'FAIL fixed: {type(e).__name__}: {e}')
"
```

**1.6** Se import falha mesmo com path correcto, investigar porquê:
- Qual é o erro exacto? (ModuleNotFoundError, SyntaxError, ImportError interno?)
- Qual ficheiro falha? (pode ser dependência que StatGuardrail importa internamente)
- Existe `__init__.py`? Tem relative imports?

**1.7** Listar estrutura completa:
```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly" -Recurse -Name
```

### Output esperado Módulo 1
- Qual é a arquitectura real (single file? package? dependências internas?)
- Qual é o erro exacto no import
- Diagnóstico: É só bug path? Bug path + bug código? Outra coisa?

---

## MÓDULO 2 — ApexNewsGate

**Bugs já conhecidos:**
1. Path `APEX_GC_News` não existe (filesystem tem `APEX_News`)
2. `news_provider.py:13` tem `from .economic_calendar import ...` (relative import incompatível com import top-level)

**A investigar:** se há **mais** bugs além destes 2.

### Passos

**2.1** Mapear todos os ficheiros em `APEX_News/`:
```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_News" -Recurse -Name
```

**2.2** Procurar TODOS os relative imports (`from .xxx`):
```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_News" -Recurse -Filter "*.py" |
    Select-String -Pattern "^from \.\w" |
    Format-Table Path, LineNumber, Line -Wrap
```

Se houver múltiplos relative imports, o fix é maior que 1 linha.

**2.3** Verificar se existe `__init__.py` no package:
```powershell
Test-Path "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\__init__.py"
```

**2.4** Test import com path correcto:
```powershell
& $py -c "
import sys
sys.path.insert(0, r'C:\FluxQuantumAPEX\APEX GOLD\APEX_News')
try:
    from apex_news_gate import news_gate
    print('IMPORT_OK')
except Exception as e:
    import traceback
    traceback.print_exc()
"
```

**2.5** Ver config API keys estado real:
```powershell
$config = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml"
Get-Content $config | Out-String
# NÃO imprimir valores de chaves (secrets) — só estrutura
```

**2.6** Testar API TradingEconomics (pequeno sanity check):
```powershell
# Se API key está presente, testar 1 endpoint HTTP pequeno
# (investigar código para ver endpoint correcto, não assumir)
```

### Output esperado Módulo 2
- Lista completa de relative imports a corrigir (não só 1)
- Estado de `__init__.py`
- Erro exacto do import com path correcto
- API keys presentes/ausentes
- API TradingEconomics responde?

---

## MÓDULO 3 — DefenseMode

**Hipótese:** apenas bug de path. Funcionava via cache, quebrou após Fase 7 restart.

### Passos

**3.1** Ver código que importa DefenseMode:
```powershell
Get-ChildItem "C:\FluxQuantumAI" -Recurse -Filter "*.py" |
    Select-String -Pattern "DefenseMode|defense_mode|anomaly_scorer|inference\." |
    Format-Table Path, LineNumber, Line -Wrap
```

**3.2** Mapear estrutura de `APEX_Anomaly/inference/`:
```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference" -Recurse -Name
```

**3.3** Test import com path correcto:
```powershell
& $py -c "
import sys
sys.path.insert(0, r'C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly')
try:
    from inference.defense_mode import DefenseMode
    print('IMPORT_OK')
    # Test instantiation
    # dm = DefenseMode(...)
except Exception as e:
    import traceback
    traceback.print_exc()
"
```

**3.4** Verificar relative imports dentro de DefenseMode:
```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference" -Filter "*.py" |
    Select-String -Pattern "^from \.\w" |
    Format-Table
```

**3.5** Validar que o modelo/scaler existe:
```powershell
# Do feedback anterior: grenadier_scaler_4f.json estava em APEX_Anomaly\models\
Test-Path "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\models\grenadier_scaler_4f.json"
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\models" -Recurse -Name
```

### Output esperado Módulo 3
- Confirmar se é só bug path
- Ou há bugs adicionais escondidos
- Modelo/scaler acessível?

---

## MÓDULO 4 — V4 IcebergInference (ats_iceberg_v1)

**Hipótese:** apenas bug de path.

### Passos

**4.1** Ver como é importado:
```powershell
Get-ChildItem "C:\FluxQuantumAI" -Recurse -Filter "*.py" |
    Select-String -Pattern "IcebergInference|ats_iceberg_v1|ML_ICEBERG" |
    Format-Table Path, LineNumber, Line -Wrap
```

**4.2** Mapear `APEX_Iceberg/`:
```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg" -Recurse -Name
```

**4.3** Test import:
```powershell
& $py -c "
import sys
sys.path.insert(0, r'C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg')
try:
    from ats_iceberg_v1 import IcebergInference
    print('IMPORT_OK')
except Exception as e:
    import traceback
    traceback.print_exc()
"
```

**4.4** Verificar modelos .pkl:
```powershell
Get-ChildItem "C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg" -Recurse -Filter "*.pkl" |
    Select-Object Name, Length, LastWriteTime |
    Format-Table
```

### Output esperado Módulo 4
- Confirmar se é só bug path
- Estado modelos ML (placeholder vs trained)

---

## MÓDULO 5 — MISTÉRIO `GUARDRAIL_STALE_DATA`

**Barbara observou no Telegram:** mensagens `GUARDRAIL_STALE_DATA: latency=69706ms spread=7.0tks`

**Mas StatGuardrail nunca funcionou.** De onde vêm estas mensagens?

### Passos

**5.1** Procurar origem do texto `GUARDRAIL_STALE_DATA` em TODO o código:
```powershell
Get-ChildItem "C:\FluxQuantumAI", "C:\FluxQuantumAPEX" -Recurse -Filter "*.py" -ErrorAction SilentlyContinue |
    Select-String -Pattern "GUARDRAIL_STALE_DATA|GUARDRAIL_STALE" |
    Format-Table Path, LineNumber, Line -Wrap
```

**5.2** Se encontrar, ver contexto:
- Quem emite esta mensagem?
- Está a chamar `get_guardrail_status()` ou é implementação separada?
- Tem path próprio ou usa outro mecanismo?

**5.3** Comparar com o que StatGuardrail "real" devia fazer:
- Se houver 2 implementações de guardrail (uma que funciona, outra que nunca carregou), documentar ambas

### Output esperado Módulo 5
- Origem exacta da mensagem `GUARDRAIL_STALE_DATA`
- Relação com StatGuardrail "oficial" (se alguma)
- Sistema tem implementação dupla (intencional ou bug)?

---

## REPORTE FINAL — Formato obrigatório

Criar `C:\FluxQuantumAI\backlog\degraded_mode_investigation_<timestamp>.md`:

```markdown
# Degraded Mode Investigation — 4 Features + 1 Mystery

**Date:** <timestamp>
**Duration:** X min
**Mode:** READ-ONLY

---

## Executive Summary

| Feature | Bug(s) identificado(s) | Fix complexity |
|---|---|---|
| StatGuardrail | [list] | [trivial / medium / complex] |
| ApexNewsGate | [list] | [...] |
| DefenseMode | [list] | [...] |
| V4 IcebergInference | [list] | [...] |
| GUARDRAIL_STALE_DATA origem | [answer] | [n/a se só descoberta] |

---

## MÓDULO 1 — StatGuardrail

### Arquitectura real
[como está organizado]

### Erro exacto
[stack trace]

### Hipótese confirmada
[H1: só path / H2: path + código / H3: dependência missing / etc]

### Fix necessário
[lista concreta de mudanças]

### Risco de fix
[baixo/médio/alto + porquê]

---

## MÓDULO 2 — ApexNewsGate

### Relative imports encontrados
[lista completa]

### API keys
[status]

### Erro exacto
[stack trace]

### Fix necessário
[lista concreta]

### Risco de fix
[...]

---

## MÓDULO 3 — DefenseMode

[mesma estrutura]

---

## MÓDULO 4 — V4 IcebergInference

[mesma estrutura]

---

## MÓDULO 5 — GUARDRAIL_STALE_DATA Mystery

### Origem identificada
[file:line onde texto é emitido]

### Relação com StatGuardrail
[é o mesmo, é paralelo, é implementação duplicada?]

### Implicação
[o que isto muda no diagnóstico de StatGuardrail?]

---

## Plano proposto de fix (sequência recomendada)

1. [Fix path callsites — trivial]
2. [Fix relative imports — se necessário]
3. [Fix StatGuardrail específico — se diferente]
4. [etc]

**Tempo estimado total:** X-Y min

## Testes recomendados antes de restart production

[lista de testes de import a correr em sessão isolada antes de qualquer restart]

## Riscos e rollback

[o que pode correr mal + como reverter]
```

---

## COMUNICAÇÃO FINAL

```
DEGRADED MODE INVESTIGATION COMPLETE
Duration: X min
Features investigated: 4/4
Mystery resolved: GUARDRAIL_STALE_DATA origin found / not found
Fix complexity overall: [trivial / medium / complex]
Total fixes needed: N line changes + Y file changes
Deploy risk assessment: [low / medium / high]
Report: [path]

Aguardo decisão Barbara+Claude antes de qualquer acção.
```

**NÃO tocar em código, paths, serviços ou qualquer ficheiro. Apenas investigação.**

**Se tempo cap (90 min) for atingido**, para no módulo corrente e reporta parcial. Outros módulos podem ficar para próxima sessão.

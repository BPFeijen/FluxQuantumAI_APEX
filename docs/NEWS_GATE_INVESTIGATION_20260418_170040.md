# news_gate Investigation — Result

**Date:** 2026-04-18 17:00
**Mode:** READ-ONLY (zero mudanças)
**Duration:** ~25 min

---

## Corrections vs minha análise anterior

A Barbara corrigiu-me: ela **não** confirmou que StatGuardrail funciona. Só DefenseMode. E notou que as mensagens podem vir de Anomaly ou Iceberg, não sabendo qual.

**Evidência empírica em `service_stderr.log` (17,094 linhas):**

| Módulo | Estado real | Evidência |
|---|---|---|
| **DefenseMode** | Funcionou e depois quebrou | 4,145 linhas com `[DEFENSE_MODE] ACTIVE` + `DEFENSE_MODE veto` (firing), depois 5× `not available` no stderr |
| **StatGuardrail** | **NUNCA funcionou** | 15/15 matches são `not available` (zero successes) |
| **ApexNewsGate** | **NUNCA funcionou** | 6/6 matches são `not available` (zero loads) |
| **V4 IcebergInference** | Parou ~mesma altura do DefenseMode | 5 failures recentes |

---

## Path manipulation mechanism (Passo 1)

### sys.path.insert calls encontradas

| Ficheiro | Linha | Path inserido | Válido? |
|---|---|---|---|
| `run_live.py` | 72 | `C:\FluxQuantumAI` | ✅ |
| `ats_live_gate.py` | 76 | `C:\FluxQuantumAI` | ✅ |
| `ats_live_gate.py` | 93 | `C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Iceberg` | ❌ dir não existe |
| `grenadier_guardrail.py` | 35 | `C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly` | ❌ dir não existe |
| `event_processor.py` | 95 | `C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_News` | ❌ dir não existe |
| `event_processor.py` | 122 | `C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly` | ❌ dir não existe |

**Directorias reais (confirmadas por `dir` em `C:\FluxQuantumAPEX\APEX GOLD`):** `APEX_Anomaly`, `APEX_News`, `APEX_Iceberg` (sem `_GC_`).

**Conclusão:** código foi escrito com nomes `APEX_GC_*`, mas filesystem tem `APEX_*`. Hipótese: dirs foram renomeadas num passado (LastWriteTime `APEX_Anomaly` = 2026-04-16 21:19:51) mas código nunca foi actualizado. **PYTHONPATH env var não configurado.** Zero `.pth` files, zero `sitecustomize.py` em paths produção.

### Como é que DefenseMode chegou a funcionar se path é inválido?

**Hipótese confirmada: sys.modules caching.**

Evidência:
- Log stderr: primeiras 4,145 linhas têm `[DEFENSE_MODE] ACTIVE` (trabalhando). Linha 7,211 é a primeira `GrenadierDefenseMode not available`. As últimas 3 entries em stderr são ALL `not available`.
- Esta transição sugere: durante um período, o serviço corria com `inference.anomaly_scorer` cacheado em `sys.modules` (carregado quando `APEX_GC_Anomaly` ainda existia). Após rename da dir + restart, os `sys.modules` cleared, import falhou, `_defense_available = False`.
- **No stdout log: último `DEFENSE_MODE VETO` linha 11,900 vs startup marker linha 93,223 e fim do ficheiro 110,828**. Ou seja, **após o restart de hoje (Fase 7, 12:07 local / ~14:07 UTC), ZERO DEFENSE_MODE fires**.

---

## news_gate specific findings (Passo 2)

### 2.1 Como é importado

`event_processor.py:96`:
```python
from apex_news_gate import news_gate as _news_gate
```

Apenas 1 consumidor. Top-level module import (não `from package.module import`).

### 2.2 Test 1: default sys.path
```
IMPORT FAIL (ModuleNotFoundError): No module named 'apex_news_gate'
```
Esperado — `APEX_News` não está em sys.path.

### 2.3 Test 2: com APEX_News path adicionado (correcto)
```
IMPORT FAIL (ImportError): attempted relative import with no known parent package
```

Traceback:
```
File "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\apex_news_gate.py", line 45, in <module>
    from news_provider import NewsProvider
File "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_provider.py", line 13, in <module>
    from .economic_calendar import TradingEconomicsCalendar
ImportError: attempted relative import with no known parent package
```

**Ou seja:** mesmo com o path CORRECTO, o import falha por **bug dentro do código** do `news_provider.py:13`.

### 2.4 Test 3: com APEX_GC_News (path inválido actual)
```
IMPORT FAIL: No module named 'apex_news_gate'
```

### 2.5 API key status

`news_config.yaml` **EXISTS** em `C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml` (777 bytes):

```yaml
default_asset: "GC"
tradingeconomics:
  api_key: <REDACTED — configured>
alpha_vantage:
  api_key: <REDACTED — configured>
gold_blocking:
risk_thresholds:
position_multipliers:
```

Ambas as API keys presentes. **Config não é o problema.**

### 2.6 Timeline dos logs

**service_stdout.log (110,826 linhas):** `ApexNewsGate` ou `news_gate` keyword: **0 matches**. Nunca foi logado ao stdout.

**service_stderr.log (17,094 linhas):** 6 matches, todos "not available":
- Primeiro: `"attempted relative import with no known parent package"` — **erro dentro de apex_news_gate.py** quando path estava parcialmente resolvido
- Último: `"No module named 'apex_news_gate'"` — erro quando path completamente ausente

**Total de `ApexNewsGate loaded into EventProcessor` (success message): 0 matches.** Nunca carregou com sucesso.

### 2.7 Git history

Repo git em `C:\FluxQuantumAI` tem remote errado (`FluxQuantumAI.git`, não `FluxQuantumAI_APEX.git`), branch master, zero commits, todos os ficheiros untracked. Sem histórico git utilizável.

---

## Hypothesis confirmed: **H5 — Bug no código do módulo**

`news_provider.py:13` (em `APEX_News/news_provider.py`):
```python
from .economic_calendar import TradingEconomicsCalendar
```

O `.economic_calendar` é relative import — requer que `news_provider` seja importado como parte de um package (e.g., `APEX_News.news_provider`). Mas `event_processor.py:96` faz `from apex_news_gate import ...` que o trata como top-level module. Relative import explode.

**E H1 (sys.path bug) também parcialmente contribui:** path `APEX_GC_News` adicionado é inválido (dir não existe). Mesmo que o relative import estivesse OK, esta é uma segunda barreira.

---

## Recommendation

### To activate news_gate (if safe) — 2 fixes necessários

**FIX 1 (obrigatório): Corrigir o relative import em `APEX_News/news_provider.py`**

Em `news_provider.py` linha 13, mudar:
```python
from .economic_calendar import TradingEconomicsCalendar
```
Para:
```python
from economic_calendar import TradingEconomicsCalendar
```

(Provavelmente existem outros relative imports similares em `apex_news_gate.py` e outros ficheiros do package — verificar `grep "from \."` no `APEX_News/` dir.)

**FIX 2 (obrigatório): Corrigir sys.path em event_processor.py**

Linha 95, mudar:
```python
_sys.path.insert(0, str(Path("C:/FluxQuantumAPEX/APEX GOLD/APEX_GC_News")))
```
Para:
```python
_sys.path.insert(0, str(Path("C:/FluxQuantumAPEX/APEX GOLD/APEX_News")))
```

### Risks de activar

**BAIXO-MÉDIO** desde que:
- TradingEconomics API key válida (não verificado — requer HTTP call que eu não faço read-only)
- Thresholds default razoáveis em `news_config.yaml` (não validados)
- Entries NFP/FOMC reais: news_gate pode **bloquear entradas** ou **EXIT_ALL** em janelas de 30 min antes/depois. Isto **mudará o comportamento actual** da produção. Barbara precisa validar que os thresholds se alinham com a política dela.

### Do NOT activate if:

- Não tiveres testado o news_config.yaml thresholds numa sessão de review
- Não tiveres um teste de que a API TradingEconomics responde correctamente
- Estiveres antes de um evento major (NFP/FOMC) sem Plan B se gate mal configurado → causa `EXIT_ALL` imprevisto

---

## Open questions para Barbara+Claude

1. **DefenseMode quebrou hoje (após Fase 7 restart).** É issue separado — queres que investigue separadamente ou tratamos no mesmo esforço de sys.path fixes?
2. **StatGuardrail nunca funcionou** apesar de estar no código. Bug crítico de sempre — queres saber que mensagens/bloqueios perdeste por isto?
3. **V4 IcebergInference** também nas mesmas condições. Bug de directorias rename.
4. **news_gate relative import bug** — é fix trivial de 1-2 linhas, mas requer tocar em `APEX_News/news_provider.py` (código da Barbara). Autorização antes de propor edit?

---

## Communication

```
news_gate INVESTIGATION COMPLETE
Duration: ~25 min
Hypothesis confirmed: H5 (bug no código — relative import em news_provider.py:13)
                     + H1 parcial (sys.path aponta para APEX_GC_News inexistente)
Recommendation: DON'T activate sem fix de código. Fix requer 2 mudanças (news_provider.py + event_processor.py).
Risk if activate sem fix: n/a (não activa — import falha sempre)
Risk depois do fix: BAIXO-MÉDIO (API key presente; thresholds precisam validação da Barbara)
Report: C:\FluxQuantumAI\NEWS_GATE_INVESTIGATION_20260418_170040.md

Aguardando decisão Barbara+Claude.
```

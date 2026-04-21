# Degraded Mode Investigation — 4 Features + 1 Mystery

**Date:** 2026-04-18 18:00:18 (local)
**Duration:** ~45 min
**Mode:** READ-ONLY (zero mudanças em código, filesystem, serviços)
**Session:** pós-Fase 7 deploy (2026-04-18 12:07 local)

---

## Executive Summary

| Feature | Bug(s) identificado(s) | Fix complexity |
|---|---|---|
| **StatGuardrail** | 1 path bug (`APEX_GC_Anomaly` → `APEX_Anomaly`) | **TRIVIAL** (1 linha) |
| **V4 IcebergInference** | 1 path bug (`APEX_GC_Iceberg` → `APEX_Iceberg`) | **TRIVIAL** (1 linha) |
| **DefenseMode** | 2 path bugs (sys.path + scaler hardcoded) | **TRIVIAL** (2 linhas) |
| **ApexNewsGate** | 1 path bug + **12 relative imports** em 5 ficheiros + empty `__init__.py` | **MEDIUM** |
| **GUARDRAIL_STALE_DATA origem** | `APEX_Anomaly/detectors/guardrail.py:147` (é o MESMO StatGuardrail, mensagens pre-restart) | n/a (descoberta) |

**Resumo empírico do estado actual (pós-Fase 7 restart):**
- StatGuardrail, DefenseMode, V4 IcebergInference: todos carregaram PRE-restart (via sys.modules cache provavelmente anterior ao rename `APEX_GC_*` → `APEX_*`), **todos falham no startup fresco de hoje**.
- ApexNewsGate: **nunca carregou**, nem pré-restart.

Evidência: `service_stderr.log` 2,194 GUARDRAIL STALE_DATA messages **ANTES do último startup (linha 15,129)**, **ZERO depois**.

---

## MÓDULO 1 — StatGuardrail

### Arquitectura real

- **Classe:** `class StatGuardrail` em `C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\detectors\guardrail.py:81`
- **Wrapper singleton:** `C:\FluxQuantumAI\grenadier_guardrail.py` (lines 37, 61-64)
- **Chamada dos consumidores:**
  - `ats_live_gate.py:77`: `from grenadier_guardrail import get_guardrail_status as _guardrail_fn` → L850: `_gr = _guardrail_fn()`
  - `event_processor.py:107-108`: `from grenadier_guardrail import update_guardrail, get_guardrail_status` → L1429 (update), L4166 (get_status)
  - `hedge_manager.py:50`: `from grenadier_guardrail import get_guardrail_status as _hedge_guardrail_fn` → L308

### Erro exacto (testado empiricamente)

Com path `APEX_GC_Anomaly` (broken, código actual):
```
ModuleNotFoundError: No module named 'detectors'
  from detectors.guardrail import StatGuardrail, GuardrailStatus
  (em grenadier_guardrail.py:37)
```

### Hipótese confirmada: **H1 (só path bug)**

Test empírico com path `APEX_Anomaly` (fixed):
```
=== 1.5b — fixed APEX_Anomaly path ===
  IMPORT OK
```

### Fix necessário

**1 linha** — `C:\FluxQuantumAI\grenadier_guardrail.py:33`:
```python
# Antes:
_ANOMALY_PKG = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")
# Depois:
_ANOMALY_PKG = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")
```

### Risco de fix: **BAIXO**

Módulo em disco está completo e carregável. Fix apenas aponta o sys.path para o local correcto.

---

## MÓDULO 2 — ApexNewsGate

### Estrutura APEX_News (12 ficheiros `.py`)

```
APEX_News/
├── __init__.py          (empty — 0 bytes)
├── add_news_features.py
├── alpha_vantage.py
├── apex_news_gate.py
├── economic_calendar.py
├── events.py
├── geopolitical_monitor.py
├── news_provider.py
├── release_monitor.py
├── risk_calculator.py
├── time_utils.py
├── validate_news.py
├── news_config.yaml
└── config/{apitube_key.json, country_relevance_gold.json}
```

### Relative imports encontrados (12 em 5 ficheiros)

| File | Line | Import |
|---|---|---|
| alpha_vantage.py | 13 | `from .time_utils import now_utc` |
| economic_calendar.py | 21 | `from .events import EconomicEvent` |
| economic_calendar.py | 22 | `from .time_utils import parse_te_datetime, to_et` |
| news_provider.py | 13 | `from .economic_calendar import TradingEconomicsCalendar` |
| news_provider.py | 14 | `from .risk_calculator import NewsRiskCalculator` |
| news_provider.py | 15 | `from .alpha_vantage import AlphaVantageProvider` |
| news_provider.py | 16 | `from .events import EconomicEvent, NewsRiskLevel, NewsResult, NewsFeatures` |
| news_provider.py | 17 | `from .time_utils import now_et, to_et, minutes_diff` |
| release_monitor.py | 39 | `from .economic_calendar import TradingEconomicsCalendar` |
| release_monitor.py | 40 | `from .events import EconomicEvent` |
| risk_calculator.py | 10 | `from .events import EconomicEvent, NewsRiskLevel` |
| risk_calculator.py | 11 | `from .time_utils import minutes_diff, to_et` |

### Erro exacto (testado empiricamente)

Com path `APEX_News` (fixed):
```
ImportError: attempted relative import with no known parent package
  File "news_provider.py:13": from .economic_calendar import TradingEconomicsCalendar
```

Os `.X` relative imports só funcionam se o módulo fôr importado COMO parte de um package. O `event_processor.py:96` faz `from apex_news_gate import news_gate` como top-level (não `from APEX_News.apex_news_gate`).

### API keys status

`news_config.yaml` (777 bytes): `tradingeconomics.api_key` e `alpha_vantage.api_key` **presentes** (não testei conectividade real da API).

### Hipóteses confirmadas: **H1 + H5** (path bug + relative import bug)

### Fix necessário — duas abordagens

**Abordagem A — Converter relative imports para absolute (recomendada):**

Mudar os 12 imports acima: `from .X` → `from X`. Em 5 ficheiros. Mais depois mudar `event_processor.py:95` para apontar a `APEX_News`.

**Abordagem B — Tornar APEX_News um package real:**

1. Adicionar content ao `__init__.py` (pode ficar empty mas precisa estar no sys.path do parent).
2. Em `event_processor.py:95`, mudar para `sys.path.insert(0, "C:/FluxQuantumAPEX/APEX GOLD")` (parent dir).
3. Em `event_processor.py:96`, mudar `from apex_news_gate import news_gate` → `from APEX_News.apex_news_gate import news_gate`.

Abordagem B é 2 linhas no `event_processor.py` + nada no APEX_News. **Mais limpa.**

### Risco de fix: **MÉDIO**

- Abordagem A toca em 5 ficheiros de Barbara (mudança de semântica de imports que afecta outros consumidores hipotéticos).
- Abordagem B toca só no event_processor (1 ficheiro). Mais segura.
- Em ambos, activar news_gate mudará comportamento da produção: entries vão ser bloqueadas nas janelas ±30min de NFP/CPI/FOMC. **Precisa validação empírica dos thresholds em `news_config.yaml` antes de activar.**

---

## MÓDULO 3 — DefenseMode

### Arquitectura real

- **Classe:** `class GrenadierDefenseMode` em `C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py`
- **Consumer:** `event_processor.py:119-132` — tenta `from inference.anomaly_scorer import GrenadierDefenseMode` após `sys.path.insert(APEX_GC_Anomaly)`

### Erro exacto

**Bug #1 — path sys.path (event_processor.py:122):**
```
APEX_GC_Anomaly → não existe (dir é APEX_Anomaly)
```

**Bug #2 — path hardcoded na classe (anomaly_scorer.py:404):**
```python
r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly\models\grenadier_scaler_4f.json"
```

Test empírico com path fixed no sys.path mas bug #2 ainda presente:
```
FileNotFoundError: [Errno 2] No such file or directory: 
'C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly\models\grenadier_scaler_4f.json'
```

**Scaler real está em:** `C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\models\grenadier_scaler_4f.json` (624 bytes, exists ✓).

### Hipótese confirmada: **H2 (path bug + código interno)**

### Fix necessário — **2 linhas**

**Fix A:** `event_processor.py:122`:
```python
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")))  # era APEX_GC_Anomaly
```

**Fix B:** `anomaly_scorer.py:404`:
```python
r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\models\grenadier_scaler_4f.json"  # era APEX_GC_Anomaly
```

### Risco de fix: **BAIXO**

Bugs idênticos a StatGuardrail. Scaler existe, classe é instantiable após Fix B.

---

## MÓDULO 4 — V4 IcebergInference (ats_iceberg_v1)

### Arquitectura real

- **Package:** `C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg\ats_iceberg_v1\` (completo com `inference/`, `features/`, `models/`, `training/`, `prediction/`, `validation/`, `output/`)
- **Consumer:** `ats_live_gate.py:91-93`:
  ```python
  _ICEBERG_PKG_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Iceberg")  # BROKEN (dir é APEX_Iceberg)
  sys.path.insert(0, str(_ICEBERG_PKG_DIR))
  ```

### Erro exacto

Com path `APEX_Iceberg` (fixed):
```
=== 4.3 — fixed path APEX_Iceberg ===
  IMPORT OK
```

### Models .pkl

| File | Size | Status |
|---|---|---|
| `autoencoder_stage1.pkl` | 1,333 B | **placeholder** (too small for real model) |
| `classifier_stage2.pkl` | ~600 KB | trained |
| `calibration_stage3.pkl` | 82 B | stub |

**Nota:** mesmo com paths corrigidos, o V4 ML irá correr em **modo rule-based fallback** porque autoencoder é placeholder (docstring `iceberg_inference.py:6`: *"Stage 2 (classifier): rule-based fallback until model is trained"*).

### Hipótese confirmada: **H1 (só path bug)**

### Fix necessário — **1 linha**

`ats_live_gate.py:91`:
```python
_ICEBERG_PKG_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg")  # era APEX_GC_Iceberg
```

### Risco de fix: **BAIXO**

Rule-based fallback vai correr (autoencoder pkl é placeholder) — comportamento funcionalmente idêntico ao que estava ANTES da quebra.

---

## MÓDULO 5 — GUARDRAIL_STALE_DATA Mystery

### Origem identificada

**Ficheiro:** `C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\detectors\guardrail.py:147`

```python
log.warning("GUARDRAIL STALE_DATA: latency=%.1fms > %.0fms threshold", ...)
```

### Relação com StatGuardrail

**É o MESMO StatGuardrail.** A mensagem é emitida por `StatGuardrail.check()` dentro do módulo oficial.

### Implicação crítica

Se `GUARDRAIL STALE_DATA` aparece em logs, **StatGuardrail ESTEVE a correr** — não é implementação duplicada.

### Timeline empírica

| Métrica | Valor |
|---|---|
| Total mensagens `GUARDRAIL STALE_DATA` em stderr | **2,194** |
| Primeira linha do ficheiro | 98 |
| Última linha do ficheiro | 7,189 |
| Última linha de startup (HEARTBEAT_LOOP_ENTERED pid=9236) | **15,129** |
| Mensagens DEPOIS do startup mais recente | **0** |

**Conclusão:** StatGuardrail funcionou em TODOS os runs anteriores (2,194 warnings emitidas), mas **não carregou no startup atual**. Mesmo padrão de DefenseMode e V4 IcebergInference.

Provável sequência:
1. Em startup passado, o path `APEX_GC_Anomaly` estava válido (ou cache de .pyc existia)
2. StatGuardrail carregou com sucesso
3. Emitiu 2,194 warnings durante operação
4. Algures entre esse run e o último restart (Fase 7), path ou estrutura mudou
5. Startup actual tenta importar, falha, `_guardrail_available=False` silent
6. Barbara via mensagens GUARDRAIL STALE_DATA no Telegram (provavelmente via `notify_defense_mode` ou similar — a verificar) **antes**, zero agora

**Barbara, as mensagens que viste (GUARDRAIL e DEFENSE MODE) foram reais mas são de antes do restart Fase 7. Agora todas estas 4 protecções estão silenciosamente OFF.**

---

## Plano proposto de fix (sequência recomendada)

### Fase F1 — Fix path bugs (trivial) — **~5 min**

| # | File | Line | Change |
|---|---|---|---|
| 1 | `C:\FluxQuantumAI\grenadier_guardrail.py` | 33 | `APEX_GC_Anomaly` → `APEX_Anomaly` |
| 2 | `C:\FluxQuantumAI\live\event_processor.py` | 122 | `APEX_GC_Anomaly` → `APEX_Anomaly` |
| 3 | `C:\FluxQuantumAI\ats_live_gate.py` | 91 | `APEX_GC_Iceberg` → `APEX_Iceberg` |
| 4 | `C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py` | 404 | `APEX_GC_Anomaly` → `APEX_Anomaly` |

**Resultado esperado:** StatGuardrail, DefenseMode, V4 IcebergInference voltam a carregar.

### Fase F2 — Fix ApexNewsGate (medium) — **~15 min + validação thresholds**

**Abordagem recomendada (B — menos intrusive):**

| # | File | Line | Change |
|---|---|---|---|
| 1 | `C:\FluxQuantumAI\live\event_processor.py` | 95 | `APEX_GC_News` → path parent (`APEX GOLD`) |
| 2 | `C:\FluxQuantumAI\live\event_processor.py` | 96 | `from apex_news_gate` → `from APEX_News.apex_news_gate` |

**Pre-deploy check obrigatório:**
- Ler thresholds em `news_config.yaml` e confirmar com Barbara
- Testar API TradingEconomics fora do sistema
- Paper-run 1 semana antes de produção

### Fase F3 — Validação empírica pós-fix

Em sessão isolada antes de restart produção:
```python
import sys
sys.path.insert(0, "C:/FluxQuantumAI")
import live.event_processor as ep
assert ep._defense_available == True
assert ep._guardrail_available == True
assert ep._news_gate is not None
# V4: ats_live_gate tests separate
```

Se assertions passam → restart FluxQuantumAPEX. Observar log de startup confirma `loaded` em vez de `not available`.

---

## Testes recomendados antes de restart production

1. **py_compile** os 4 ficheiros modificados — sintaxe OK
2. **Import probe** numa sessão fresh Python — cada módulo carrega
3. **Instantiation probe** — `GrenadierDefenseMode()`, `StatGuardrail(...)`, `IcebergInference(...)` — construtores OK
4. **news_gate smoke test** — `news_gate.check_score()` ou equivalent returns sane value
5. **Backup dos 4 ficheiros** antes de touch (rollback pronto)

---

## Riscos e rollback

### Risks

| Risk | Likelihood | Impact |
|---|---|---|
| Relative imports residuais em APEX_Anomaly/APEX_Iceberg (iguais a APEX_News) | Medium | Medium |
| `news_config.yaml` thresholds desalinhados com política actual | High | **High** (entries bloqueadas em eventos que Barbara quereria tradear) |
| TradingEconomics API down/rate-limited | Low | Medium |
| DefenseMode false-positive spike (z-scores não calibradas para mercado actual) | Low | Medium |
| V4 IcebergInference gera sinais diferentes do rule-based actual | Medium | Low (placeholder autoencoder → still rule-based na prática) |

### Rollback procedure

Para cada fix, manter backup pre-edit. Em caso de problema:
```powershell
# Stop service
Stop-Service FluxQuantumAPEX
# Restore from backup
Copy-Item "<backup_path>\grenadier_guardrail.py" "C:\FluxQuantumAI\grenadier_guardrail.py" -Force
# (repetir para cada ficheiro alterado)
Start-Service FluxQuantumAPEX
```

---

## Open questions para Barbara+Claude

1. **news_config.yaml thresholds** — precisa review antes de activar news_gate (as windows de ±30 min e risk scores precisam estar alinhadas à política actual de trading).
2. **Activação coordenada vs gradual** — activar todas as 4 de uma vez ou F1 primeiro (3 features), validar 1-2 dias, depois F2 (news)?
3. **`APEX_Anomaly/inference/` relative imports** — eu testei apenas `anomaly_scorer.py` (que funcionou). Se houver relative imports em `reconstructor.py`, `regime_detector.py`, `trap_detector.py`, podem aparecer na instantiation ou em runtime. **Eu não verifiquei sistematicamente os relatives em APEX_Anomaly e APEX_Iceberg — recomendo executar mesma check de relative imports que fiz para APEX_News antes de fix.**
4. **Decisão final de naming** — a refactoring `APEX_*` → `APEX_GC_*` ainda é a intenção? Ou aceitamos nomes actuais `APEX_*` e actualizamos código? **Recomendação: aceitar nomes actuais** (menos ficheiros tocados — 4 linhas vs renomear 3 dirs + actualizar múltiplos imports em múltiplos repos hipoteticamente).

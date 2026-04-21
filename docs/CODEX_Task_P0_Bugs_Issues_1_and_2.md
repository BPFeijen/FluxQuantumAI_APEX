# TASK: P0 Bug Fixes — Issues #1 and #2 ONLY (Scope-Locked)

**Para:** Codex
**De:** Barbara (FluxFoxLabs)
**Data:** 2026-04-17
**Status prévio:** O teu último patch (commit ee2068d) deixou 5 issues da `CURRENT_LIVE_CODE_AUDIT.md` por tratar. Esta task trata APENAS dois deles — os dois bugs silent que são P0 e bloqueiam deploy.

---

## 1. SCOPE — EXATAMENTE ISTO, NADA MAIS

### O que DEVES fazer

Corrigir **exatamente dois bugs** em `live/event_processor.py`:

- **Issue #1** — variável `price` indefinida em `_check_pre_entry_gates` (linha ~1690)
- **Issue #2** — operador booleano com precedência errada em `_detect_box_ladder` (linha ~3133)

### O que NÃO DEVES fazer

Esta task tem **scope-lock absoluto**. Qualquer um destes atos invalida o patch:

- ❌ Alterar qualquer outro ficheiro que não seja `live/event_processor.py`
- ❌ Corrigir qualquer outro issue da auditoria (incluindo #3, #5, #6, #8, #9)
- ❌ Adicionar logging, telemetria ou observabilidade
- ❌ Refactorizar código adjacente, mesmo que pareça obviamente melhor
- ❌ Renomear variáveis, funções ou parâmetros
- ❌ Formatar código (reindent, reflow, dedup imports)
- ❌ Actualizar docstrings salvo se for estritamente necessário para explicar o parâmetro novo do Issue #1
- ❌ Adicionar novos imports
- ❌ Adicionar novos testes unitários (só testes inline de validação, ver secção 5)
- ❌ Actualizar `README`, `CURRENT_LIVE_CODE_AUDIT.md` ou qualquer outro `.md`
- ❌ Criar novos ficheiros
- ❌ Alterar o `settings.json`

**Se encontrares outro bug durante esta task, NÃO corrijas. Reporta no campo "Observations" do output (secção 8) e aguarda task separada.**

**Se alguma instrução desta task for ambígua, PARA e pergunta. Não interpretes.**

---

## 2. ISSUE #1 — FIX EXATO

### Localização
`live/event_processor.py`, função `_check_pre_entry_gates`, aproximadamente linha 1658 (signature) e linha 1690 (uso errado).

### Código atual (BUG)
```python
def _check_pre_entry_gates(self, direction: str, delta_4h: float) -> tuple[bool, str]:
    """
    ...
    """
    thr = self._thresholds

    # --- 0) startup cooldown ---
    remaining = self._startup_cooldown_until - time.monotonic()
    if remaining > 0:
        return True, (...)

    # --- a0) Sprint 8: Trade cooldown ---
    if thr.get("trade_cooldown_enabled", True):
        now_mono = time.monotonic()
        cooldown_min = float(thr.get("trade_cooldown_min", 30))
        same_level_cooldown_min = float(thr.get("same_level_cooldown_min", 60))
        same_level_prox = float(thr.get("same_level_proximity_pts", 2.0))

        if self._last_trade_time > 0:
            elapsed_min = (now_mono - self._last_trade_time) / 60.0
            # Check same-level cooldown (stricter)
            if abs(price - self._last_trade_level) <= same_level_prox:    # <-- price NÃO ESTÁ DEFINIDO
                if elapsed_min < same_level_cooldown_min:
                    ...
```

### Fix exato

**Mudar signature:**
```python
def _check_pre_entry_gates(self, direction: str, delta_4h: float, price: float) -> tuple[bool, str]:
```

**Mudar chamador:** Procura todas as chamadas a `_check_pre_entry_gates` em `live/event_processor.py` e adiciona `price` como terceiro argumento. Usa a variável `price` local já existente no scope do chamador (é parâmetro do `_trigger_gate`).

Exemplo da chamada actual (aproximadamente linha 2244):
```python
blocked, block_reason = self._check_pre_entry_gates(direction, d4h)
```

Depois:
```python
blocked, block_reason = self._check_pre_entry_gates(direction, d4h, price)
```

**Não alterar** a lógica interna de cooldown. Apenas tornar `price` um parâmetro explícito.

**Actualizar docstring** apenas se precisar mencionar o novo parâmetro `price`. Manter as alterações de docstring ao mínimo absoluto.

---

## 3. ISSUE #2 — FIX EXATO

### Localização
`live/event_processor.py`, função `_detect_box_ladder`, aproximadamente linha 3133.

### Código atual (BUG)
```python
box_df = df[df["m30_box_confirmed"] == True & df["m30_box_id"].notna()]
```

**Por que está errado:** Em Python, `&` tem precedência maior que `==`, portanto esta expressão é avaliada como:
```python
df["m30_box_confirmed"] == (True & df["m30_box_id"].notna())
```
Isto compara o boolean de confirmação com uma máscara booleana — resultado semanticamente corrompido.

### Fix exato
```python
box_df = df[(df["m30_box_confirmed"] == True) & (df["m30_box_id"].notna())]
```

**Apenas adicionar os dois pares de parênteses. Não alterar mais nada nesta linha nem nas adjacentes.**

---

## 4. PROOF OF EXECUTION — obrigatório

Após implementar, retorna no output (secção 8):

### 4.1 Git evidence
- **Commit hash** do patch
- **Branch name** onde foi commitado
- **Exact `git diff --no-color --unified=3 HEAD~1 HEAD -- live/event_processor.py`** completo (não truncado, não resumido, sem `...`)
- **Exact `git diff --stat HEAD~1 HEAD`** — deve mostrar EXACTAMENTE 1 ficheiro alterado

### 4.2 Line-level evidence
Para cada um dos dois issues:
- **Linha exacta** onde o fix foi aplicado (número da linha no ficheiro pós-fix)
- **Snippet "antes"** — 3 linhas de contexto + linha com bug
- **Snippet "depois"** — 3 linhas de contexto + linha corrigida

### 4.3 Scope compliance
Responde **YES/NO** a cada uma destas perguntas (se algum for NO, explica):

- [ ] Alterei apenas `live/event_processor.py`? Y/N
- [ ] Não adicionei novos imports? Y/N
- [ ] Não adicionei logging? Y/N
- [ ] Não refactorizei código adjacente? Y/N
- [ ] Não alterei docstrings salvo o parâmetro `price` novo no Issue #1? Y/N
- [ ] Não toquei em nenhum outro issue da auditoria? Y/N
- [ ] Não alterei `settings.json`? Y/N
- [ ] Não criei ficheiros novos? Y/N

---

## 5. PROOF OF FUNCTIONALITY — obrigatório

**Syntax check sozinho NÃO BASTA.** Queremos evidência de que o fix funciona no caminho de código afectado.

### 5.1 Issue #1 — prova funcional

Executa este teste inline (copia e cola num ficheiro Python temporário, corre, retorna output):

```python
# test_issue1.py (temp file, delete after)
import sys, os
sys.path.insert(0, os.getcwd())

# Mock minimal EventProcessor state for _check_pre_entry_gates
class MockEP:
    _thresholds = {
        "trade_cooldown_enabled": True,
        "trade_cooldown_min": 30,
        "same_level_cooldown_min": 60,
        "same_level_proximity_pts": 2.0,
        "delta_4h_exhaustion_high": 3000,
        "delta_4h_exhaustion_low": -1050,
        "delta_4h_inverted_fix": False,
    }
    _startup_cooldown_until = 0
    _last_trade_time = 1000  # > 0, triggers cooldown branch
    _last_trade_level = 4800.0
    _open_positions = []
    _margin_level = 1000.0

# Import the real method from the patched file
from live.event_processor import EventProcessor

# Bind mock state to the method
import types
ep = MockEP()
# Copy the method from the real class onto the mock
ep._check_pre_entry_gates = types.MethodType(EventProcessor._check_pre_entry_gates, ep)

# Test case 1: price NEAR last trade level — should trigger same-level cooldown
import time
# Fake time.monotonic to return close to _last_trade_time (still in cooldown window)
_orig_mono = time.monotonic
time.monotonic = lambda: 1050  # 50s after last trade = 0.83 min, far from cooldown_min

try:
    blocked, reason = ep._check_pre_entry_gates(direction="LONG", delta_4h=0.0, price=4800.5)
    print(f"TEST1_PASS: blocked={blocked}, reason={reason[:80]}")
except NameError as e:
    print(f"TEST1_FAIL_NAMEERROR: {e}  (indicates fix not applied)")
except Exception as e:
    print(f"TEST1_UNEXPECTED: {type(e).__name__}: {e}")
finally:
    time.monotonic = _orig_mono
```

O output **esperado pós-fix** é `TEST1_PASS: blocked=True, reason=[COOLDOWN] BLOCKED ...`. Se o output for `TEST1_FAIL_NAMEERROR: name 'price' is not defined` significa que o fix não foi aplicado correctamente.

Retorna no output: o stdout completo deste teste.

### 5.2 Issue #2 — prova funcional

Executa este teste inline:

```python
# test_issue2.py (temp file, delete after)
import pandas as pd

# Build controlled test DataFrame
df = pd.DataFrame({
    "m30_box_confirmed": [True, True, False, False, True],
    "m30_box_id":        [1,    None, 1,     None,  2],
})

# BROKEN expression (what was there before):
broken = df[df["m30_box_confirmed"] == True & df["m30_box_id"].notna()]
# FIXED expression (what should be there now):
fixed  = df[(df["m30_box_confirmed"] == True) & (df["m30_box_id"].notna())]

print("broken result rows:", len(broken))
print("broken indices:", list(broken.index))
print("fixed result rows:", len(fixed))
print("fixed indices:", list(fixed.index))

# Expected: fixed should return ONLY rows where BOTH confirmed=True AND box_id notna
# That's rows 0 and 4 -> len=2, indices=[0, 4]
assert len(fixed) == 2, f"FAIL: expected 2 rows, got {len(fixed)}"
assert list(fixed.index) == [0, 4], f"FAIL: expected indices [0,4], got {list(fixed.index)}"
print("TEST2_PASS: fixed expression returns correct filter")
```

Retorna no output: o stdout completo deste teste.

Além disso, retorna também um **grep no código pós-fix** confirmando que a nova linha é a correcta:
```bash
grep -n 'box_df = df\[' live/event_processor.py
```
Esperado: a linha mostrada deve ter `(df["m30_box_confirmed"] == True) & (df["m30_box_id"].notna())` com os parênteses.

---

## 6. PROOF OF NO REGRESSION — obrigatório

Este é o mais importante. A Barbara já viu um deploy onde 12/12 foi reportado e a realidade era 7/12. Desta vez queremos **evidência positiva de que nada foi partido**.

### 6.1 Compile check abrangente
```bash
python -m py_compile run_live.py live/event_processor.py live/level_detector.py live/position_monitor.py live/tick_breakout_monitor.py live/telegram_notifier.py live/base_dashboard_server.py live/m5_updater.py live/m30_updater.py live/operational_rules.py live/feed_health.py live/signal_queue.py live/price_speed.py live/hedge_manager.py live/kill_zones.py
```
Retorna output (exit code 0 esperado, sem errors).

### 6.2 Caller consistency check
Como alteraste a signature de `_check_pre_entry_gates`, confirma que **TODOS** os chamadores foram actualizados:

```bash
grep -n "_check_pre_entry_gates" live/event_processor.py
```

Retorna o output. Deve mostrar:
- 1 linha com `def _check_pre_entry_gates(self, direction: str, delta_4h: float, price: float)` (signature)
- 1+ linhas com chamadas todas com 3 argumentos positional (`direction`, `d4h`/`delta_4h`, `price`)

Se houver qualquer caller com só 2 argumentos, **é regressão**.

### 6.3 Also check full codebase for any other caller
```bash
grep -rn "_check_pre_entry_gates" --include="*.py" .
```

Se existir caller fora de `live/event_processor.py`, precisa também ser actualizado.

### 6.4 No side-effect on other box filtering
Confirma que não há outra linha no ficheiro com o mesmo bug de precedência `== True &`:

```bash
grep -n '== True &' live/event_processor.py
grep -rn '== True &' --include="*.py" live/
```

Retorna output. Idealmente **zero matches** após o fix.

### 6.5 Scope verification
```bash
git diff --name-only HEAD~1 HEAD
```

Deve retornar **APENAS** `live/event_processor.py`. Qualquer outro ficheiro = scope violation.

---

## 7. BACKWARD COMPATIBILITY

- O fix #1 muda signature de método interno. Confirma que `_check_pre_entry_gates` **não é chamado de fora** do `event_processor.py` (se for, precisa ser updated em todos os callers).
- O fix #2 é drop-in compatible (mesma linha, só parênteses).

Nenhum dos dois fixes deve requerer alteração ao `settings.json`, parquets, ou qualquer estado externo.

---

## 8. FORMATO DO OUTPUT OBRIGATÓRIO

A tua resposta deve conter, **nesta ordem exata, sem omissão de nenhuma secção**:

### Section A — Per-issue status matrix
| Issue | Status | Linha pós-fix | Commit hash |
|---|---|---|---|
| #1 price undefined | FIXED / PARTIAL / NOT_ADDRESSED | nnnn | xxxxxxx |
| #2 boolean precedence | FIXED / PARTIAL / NOT_ADDRESSED | nnnn | xxxxxxx |

### Section B — Execution proof
- Commit hash e branch
- Full git diff (não resumido)
- git diff --stat output

### Section C — Line-level evidence
- Issue #1: snippet antes + snippet depois (com 3 linhas de contexto cada)
- Issue #2: snippet antes + snippet depois (com 3 linhas de contexto cada)

### Section D — Scope compliance checklist
Responde Y/N aos 8 items da secção 4.3.

### Section E — Functional proof
- stdout completo do test_issue1.py
- stdout completo do test_issue2.py
- grep output do Issue #2

### Section F — No-regression proof
- Output do py_compile abrangente
- Output do `grep _check_pre_entry_gates`
- Output do `grep -rn _check_pre_entry_gates`
- Output do `grep '== True &'`
- Output do `git diff --name-only`

### Section G — Observations (se aplicável)
Se encontraste outros bugs durante esta task, lista-os aqui **SEM TER CORRIGIDO**. Serão task separadas.

Se alguma instrução foi ambígua, diz aqui o que fizeste e porquê.

### Section H — Self-assessment
Responde honestamente:
1. Cumpriste **literalmente** o scope de scope lock da secção 1.2? Y/N
2. Se respondeste Y, confirma: não tocaste em nenhuma outra linha de código além das duas linhas identificadas + signature update + chamadores. Y/N
3. Acreditas que este patch está pronto para deploy em produção sem shadow test adicional? Y/N + justificação curta.

---

## 9. REGRAS FINAIS

1. **Honestidade absoluta.** Preferimos "NOT_ADDRESSED com razão X" a "FIXED com evidência fraca".
2. **Zero scope creep.** Este patch é literalmente 3-4 linhas de código alteradas. Se o teu diff tem >10 linhas alteradas, algo correu mal.
3. **Não inventes evidência.** Se o teste falhar, reporta que falhou e explica porquê.
4. **Se este prompt tem ambiguidade, PARA e pergunta.** Não assumas.

---

**Este patch é bloqueador para deploy. Sem ele não avançamos. Obrigada pela atenção à disciplina.**

Barbara

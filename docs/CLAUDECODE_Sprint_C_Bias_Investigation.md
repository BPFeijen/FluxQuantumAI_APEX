# TASK: SPRINT C READ-ONLY — Investigação `derive_m30_bias` Stuck Bug

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Sprint:** entry_logic_fix_20260420 (Track C)
**Mode:** 100% READ-ONLY. Zero edits, zero restarts.
**Output:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\BIAS_STUCK_INVESTIGATION.md`

---

## CONTEXTO

Durante Sprint A FASE 1.2 live diagnostic, descobriste:
- `m30_bias=bearish` há **8h, zero flips**, apesar de rally +7pts MT5
- `daily_trend=short` stuck desde 05:00, não revalida contra recuperação
- `delta_4h=-1039` (4h window captura queda 03:00-06:00, não rolou)
- Vs L2 últimas 2h: `cumulative_delta NET +282` (compra dominante)
- Iceberg JSONL últimas 2h: `4 BID vs 1 ASK` (buyer absorption dominante)

Tua hipótese: `derive_m30_bias` tem **equality bug** (`>=` vs `>`) que trava o flip quando `liq_top == box_high` ou condição similar.

**Sistema emitiu 69 GOs SHORT / 0 LONG nas últimas 8h.** 100% direction bias. Mesmo com Sprint A C1, o resultado seria "menos SHORTs" mas **continuaria cego a LONGs** porque o bias da decisão de direcção está stuck.

**Esta task investiga se hipótese está certa + quantifica impacto.**

---

## REGRA CRÍTICA — ZERO EDITS, ZERO FABRICAÇÃO

- Apenas leitura código + decision_log + git history
- Reportar apenas evidência directa
- Se não consegues verificar, dizer "não verificado" — não assumir
- Não tocar capture processes (12332, 8248, 2512)
- Não restart, não chamar funções activas

---

## PASSO 1 — Localizar código `derive_m30_bias`

```powershell
# Localizar função
Select-String -Path "C:\FluxQuantumAI\**\*.py" -Pattern "def derive_m30_bias|def _derive_m30_bias|derive_m30_bias\s*\(" -Context 0,40

# Onde é chamada
Select-String -Path "C:\FluxQuantumAI\**\*.py" -Pattern "derive_m30_bias\(" | 
    Select-Object Path, LineNumber, Line

# Onde m30_bias é assigned
Select-String -Path "C:\FluxQuantumAI\**\*.py" -Pattern "self\.m30_bias\s*=|m30_bias\s*=" |
    Select-Object Path, LineNumber, Line
```

**Reportar:**
- Ficheiro + linha onde `derive_m30_bias` está definida
- Assinatura completa da função
- Todas as call sites
- Todos os assignments de `self.m30_bias`

---

## PASSO 2 — Código literal completo

**Copy completo da função** para o report. Nada de resumo. Literal.

```powershell
# Lê a função completa — ajustar linhas conforme Passo 1
$file = "<path_encontrado>"
$start_line = <start_line_encontrada>
$end_line = <end_line_encontrada>
Get-Content $file | Select-Object -Index (($start_line-1)..($end_line-1))
```

**Reportar no documento:**
```markdown
## 2. Código literal `derive_m30_bias`

**Path:** <ficheiro:linha>

```python
[código completo, literal, com números de linha]
```
```

---

## PASSO 3 — Análise de comparações críticas

Para cada comparação numérica na função, identificar:
- Operador (`>`, `>=`, `<`, `<=`, `==`, `!=`)
- Operandos (`liq_top`, `box_high`, `price`, `m30_fmv`, etc.)
- Caso edge: o que acontece quando operandos são **iguais**?

**Reportar tabela:**

| Linha | Comparação actual | Caso iguais | Comportamento | Esperado per literatura |
|---|---|---|---|---|
| N | `liq_top > box_high` | iguais → False | Não trata como "top acima" | Depende: com >= trata como top |
| ... | | | | |

**Identificar** qual comparação é suspeita de causar stuck.

---

## PASSO 4 — Git history

```powershell
cd C:\FluxQuantumAI
# Todos os commits que tocaram na função
git log --all --source -p -L :<nome_funcao>:<path_relativo> 2>&1 | Out-String

# Ou alternativa se -L não funcionar:
git log --all --source --follow -- <path_relativo_ao_ficheiro>
```

**Reportar:**
- Primeiro commit que introduziu `derive_m30_bias`
- Commits subsequentes que modificaram comparações
- Mensagens de commit — indicam "fix", "calibração"? 
- Autor de cada commit (Claude/ClaudeCode/manual)

**Procurar padrão:** foi introduzida com `>` e alterada para `>=` algures? Ou vice-versa?

---

## PASSO 5 — Quantificar stuck em decision_log

```python
import json
from pathlib import Path
from collections import Counter
from datetime import datetime

decisions = []
log_path = Path("C:\\FluxQuantumAI\\logs\\decision_log.jsonl")
with open(log_path) as f:
    for line in f:
        d = json.loads(line)
        ts = d.get("timestamp", "")
        bias = d.get("m30_bias", "")
        confirmed = d.get("m30_bias_confirmed", False)
        price = d.get("price_mt5", 0)
        liq_top = d.get("liq_top_mt5", 0)
        liq_bot = d.get("liq_bot_mt5", 0)
        box_high = d.get("box_high_mt5", 0)
        box_low = d.get("box_low_mt5", 0)
        decisions.append({
            "ts": ts, "bias": bias, "confirmed": confirmed,
            "price": price, "liq_top": liq_top, "liq_bot": liq_bot,
            "box_high": box_high, "box_low": box_low
        })

print(f"Total decisions: {len(decisions)}")
print(f"Bias distribution: {Counter(d['bias'] for d in decisions)}")

# Identificar periodos stuck (same bias >1h)
stuck_periods = []
prev_bias = None
period_start = None
for d in decisions:
    if d["bias"] != prev_bias:
        if prev_bias and period_start:
            duration = (datetime.fromisoformat(d["ts"].replace("Z","+00:00")) - 
                       datetime.fromisoformat(period_start.replace("Z","+00:00"))).total_seconds() / 3600
            if duration >= 1.0:  # >= 1h
                stuck_periods.append({
                    "bias": prev_bias, "start": period_start, "end": d["ts"], 
                    "duration_h": round(duration, 2)
                })
        period_start = d["ts"]
        prev_bias = d["bias"]

print(f"Stuck periods (>= 1h): {len(stuck_periods)}")
for p in sorted(stuck_periods, key=lambda x: -x["duration_h"])[:10]:
    print(f"  {p['duration_h']}h: bias={p['bias']} {p['start']} → {p['end']}")
```

**Reportar:**
- Total decisions no log
- Distribution de bias (bullish/bearish/neutral counts + %)
- Top 10 longest stuck periods (duration + start/end)
- **Hoje (2026-04-20)**: qual foi o periodo stuck mais longo? Bate com as 8h que vi na live diagnostic?

---

## PASSO 6 — Detectar "violações" do bias current

Durante um stuck period, houve ticks em que:
- `bias=bearish` mas `price > box_high` (preço acima do topo → devia flip bullish)?
- `bias=bearish` mas `liq_top > box_high` (topo bullish → provável setup que devia flip)?
- `bias=bullish` mas `price < box_low` (oposto)?

```python
violations = []
for d in decisions:
    # Bearish stuck violations
    if d["bias"] == "bearish":
        if d["price"] > 0 and d["box_high"] > 0 and d["price"] > d["box_high"]:
            violations.append({"type": "bearish_but_price_above_box", "d": d})
        if d["liq_top"] > 0 and d["box_high"] > 0 and d["liq_top"] >= d["box_high"]:
            violations.append({"type": "bearish_but_liq_top_>=_box_high", "d": d})
    # Bullish stuck violations
    if d["bias"] == "bullish":
        if d["price"] > 0 and d["box_low"] > 0 and d["price"] < d["box_low"]:
            violations.append({"type": "bullish_but_price_below_box", "d": d})

print(f"Violations total: {len(violations)}")
viol_types = Counter(v["type"] for v in violations)
for t, c in viol_types.items():
    print(f"  {t}: {c}")

# Amostra de violações hoje
today_viols = [v for v in violations if "2026-04-20" in v["d"]["ts"]]
print(f"\nToday's violations: {len(today_viols)}")
for v in today_viols[:5]:
    d = v["d"]
    print(f"  {d['ts']}: {v['type']} — price={d['price']:.2f} box_high={d['box_high']:.2f} liq_top={d['liq_top']:.2f}")
```

**Reportar:**
- Total violations históricas
- Breakdown por tipo
- Violations **hoje especificamente** com detalhes
- Padrão detectável (ex: sempre que `liq_top == box_high` o flip não acontece)?

---

## PASSO 7 — Verificar código de flip condition

O bias deve flipar quando condições específicas são satisfeitas. Localizar:

```powershell
Select-String -Path "C:\FluxQuantumAI\**\*.py" -Pattern "m30_bias.*bullish|m30_bias.*bearish|flip.*bias|bias.*flip" -Context 0,15
```

**Reportar:**
- Código que determina `bias = "bullish"`
- Código que determina `bias = "bearish"`
- Código que determina `bias = "neutral"` (se existe)
- Existe `m30_bias_confirmed` separado de `m30_bias` provisional? Como é calculado?

---

## PASSO 8 — Parquet directo: bias das últimas 24h

Comparar decision_log bias vs bias calculado directamente dos parquets. Se divergem → bug não é só no derive, é em state propagation.

```python
import pandas as pd

m30 = pd.read_parquet("C:\\data\\processed\\gc_m30_boxes.parquet")
m30_recent = m30[m30.index >= "2026-04-19 00:00:00"]

# Calcular bias naive (per intuição literatura):
# - Se price actual > box_high: bullish
# - Se price actual < box_low: bearish
# - Entre: neutral
for idx, row in m30_recent.tail(20).iterrows():
    close = row.get("close", 0)
    box_high = row.get("box_high", 0)
    box_low = row.get("box_low", 0)
    naive_bias = "bullish" if close > box_high else ("bearish" if close < box_low else "neutral")
    print(f"{idx}: close={close:.2f} box=[{box_low:.2f},{box_high:.2f}] naive_bias={naive_bias}")
```

**Reportar:**
- Últimas 20 barras M30 com naive_bias
- Divergência entre naive e m30_bias no decision_log?
- Se divergem, onde?

---

## PASSO 9 — Consolidated Report

Criar `$sprint_dir\BIAS_STUCK_INVESTIGATION.md`:

```markdown
# `derive_m30_bias` Stuck Investigation — READ-ONLY

**Timestamp:** <UTC>
**Mode:** READ-ONLY (zero edits)
**Trigger:** Live diagnostic detected bias=bearish stuck 8h during rally

---

## 1. Function location
- File: <path>:<line>
- Signature: [completa]

## 2. Literal code

```python
[função completa]
```

## 3. Critical comparisons analysis

| Line | Comparison | Edge case iguais | Actual behaviour | Literature expectation |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

**Suspicious comparison identified:** [qual + porquê]

## 4. Git history

| Commit SHA | Date | Author | Message | Change |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

**Pattern:** [operator changed? function rewritten?]

## 5. Stuck periods quantification

- Total decisions analysed: X
- Distribution: bullish=Y%, bearish=Z%, neutral=W%
- Stuck periods >= 1h: N
- Longest stuck: T hours, bias=X, [start, end]
- **Today (2026-04-20) longest stuck:** T hours

## 6. Violations during stuck periods

- Total violations: N
- Breakdown:
  - bearish_but_price_above_box: X
  - bearish_but_liq_top_>=_box_high: Y
  - bullish_but_price_below_box: Z
- Today violations: M
- Pattern: [detectable rule?]

## 7. Flip condition code

```python
[código flip conditions]
```

Observations:
- [análise]

## 8. Naive bias vs actual (sanity check)

- Divergence detected: YES/NO
- If YES, where: [examples]

## 9. Root cause hypothesis

Ranked by evidence:

### Hypothesis A: [e.g., >= vs > equality bug]
- Evidence: [concrete]
- Confidence: HIGH/MEDIUM/LOW

### Hypothesis B: [e.g., stale state propagation]
- Evidence: ...

### Hypothesis C: [e.g., missing revalidation trigger]
- Evidence: ...

## 10. Proposed next steps (for future sprint)

Based on top hypothesis:
- If A: minimal fix <N lines>, change <operator>
- If B: investigate state propagation chain
- If C: add revalidation trigger on <condition>

**NO FIX in this investigation.** Read-only. Report only.

## 11. System state

- Files modified: ZERO
- Restarts: ZERO
- Capture processes: 3/3 intact (12332, 8248, 2512)
- Grep/reads executed: [count]
```

---

## COMUNICAÇÃO FINAL

```
BIAS STUCK INVESTIGATION COMPLETE (READ-ONLY)

Function location: <file:line>
Critical comparison: <suspicious line>
Git history: <N commits, last modified by X on Y>
Stuck periods >= 1h: N total, longest T hours
Today's stuck: T hours (confirms live diagnostic)
Violations during stuck: X (with pattern)
Naive bias vs actual divergence: [Y/N]

Top hypothesis: [A/B/C] — [confidence]
Evidence: [key finding]

Report: BIAS_STUCK_INVESTIGATION.md
System state: Running, capture 3/3 intact, zero changes.

Aguardo Claude + Barbara review para definir Sprint C implementation.
```

---

## PROIBIDO

- ❌ Qualquer edit a código
- ❌ Restart serviço
- ❌ Tocar capture processes
- ❌ Propor soluções durante investigação (só factos + hipóteses)
- ❌ Dizer "é bug X" sem evidência literal
- ❌ Skipar git history — essencial para rastrear quando foi introduzido
- ❌ Skipar qualquer PASSO 1-9
- ❌ Fazer fix parcial "só corrigir o operador" — este sprint é investigação, não implementação

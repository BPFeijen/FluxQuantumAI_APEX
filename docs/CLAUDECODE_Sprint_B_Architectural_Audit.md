# TASK: AUDITORIA ARQUITECTURAL + INVESTIGAÇÃO LONG PERDIDO — READ-ONLY

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Sprint:** entry_logic_fix_20260420
**Mode:** 100% READ-ONLY. Zero edits, zero restarts.
**Output:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\ARCHITECTURAL_AUDIT_READ_ONLY.md`
**Supersedes:** `CLAUDECODE_Sprint_B_Missed_Long_Investigation.md`

---

## CONTEXTO

Durante Sprint A FASE 1.2, ClaudeCode descobriu mecanismo **overextension reversal** não documentado em literatura:
- "TREND UP + liq_top abaixo do preço + overextension > 1.5×ATR → SHORT reversal"
- "TREND DN + liq_bot acima do preço + overextension > 1.5×ATR → LONG reversal"

Barbara flaggou (justificadamente):
- Threshold `1.5×ATR` **sem data-driven validation**
- Mecanismo **não está em Wyckoff/ICT/ATS** literatura
- Pullback/DELTA foi supostamente implementado em sessão anterior mas Barbara afirma **nunca funcionou**
- Padrão: confirmações falsas de "implementado" + mecanismos inventados fora literatura

**Esta task consolida a auditoria arquitectural** antes que fique no esquecimento.

---

## SCOPE — 3 INVESTIGAÇÕES EM UM DELIVERABLE

### Investigação 1: LONG Perdido 02:00-03:30 UTC (2026-04-20)
Original Sprint B scope.

### Investigação 2: Overextension Reversals — Auditoria Data-Driven
Quantificar historicamente. Decidir remoção/preservação baseado em dados, não opinion.

### Investigação 3: Pullback/DELTA — Audit vs Literatura
Confirmar se implementação segue Wyckoff (BUEC/LPS) / ICT (discount zone após BOS) / ATS (pullback à expansion line). Se não, flag gap.

**Todas READ-ONLY. Zero edits.**

---

## REGRA CRÍTICA — ZERO FABRICAÇÃO

- Reportar apenas evidência directa do código e decision_log
- Se não consegues verificar, **reporta "não consegui verificar", não assume**
- Não dizer "implementado" sem mostrar código literal + linha
- Se literatura ambígua, citar source específica (livro + página/secção)
- Se threshold não tem data-driven backing, dizer explicitamente "NÃO VALIDADO"

---

## PASSO 1 — Market State 02:00-03:30 UTC 2026-04-20

### 1.1 Price action M1/M5/M30
(conforme Sprint B original §1.1)

### 1.2 Bullish candles identification
(conforme Sprint B original §1.2)

---

## PASSO 2 — APEX Internal State 02:00-03:30

### 2.1 decision_log.jsonl (window)
(conforme Sprint B original §2.1)

### 2.2 Phase transitions
(conforme Sprint B original §2.2)

### 2.3 M30 bias transitions
(conforme Sprint B original §2.3)

---

## PASSO 3 — Triggers LONG Analysis (per literatura)

### 3.1 ALPHA LONG — liq_bot touch (Wyckoff Spring / ICT discount)

Literatura:
- Wyckoff: entry LONG em Spring (price breaks below support, returns) = near liq_bot
- ICT: entry LONG em discount PD array (below 50% range) = near liq_bot
- ATS: entry LONG undervalued (below expansion line) = near liq_bot

**Verificar em código:**
```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "liq_bot|near liq_bot" -Context 0,10
```

Reportar:
- Código literal onde ALPHA LONG é triggered
- Condições (price ≤ liq_bot + band? outras?)
- Houve oportunidade na janela 02:00-03:30? Trigger disparou?

### 3.2 ALPHA LONG Spring — fakeout DN

Literatura:
- Wyckoff Spring Type 1/2/3: price breaks below, returns → LONG
- Condição: `breakout_dir="DN"` + `box_confirmed=False`

**Verificar em código:**
```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern 'breakout_dir.*DN.*confirmed|FAKEOUT.*DN' -Context 0,15
```

Reportar status + se houve ocorrência na janela.

### 3.3 DELTA LONG — Pullback (Wyckoff BUEC/LPS, ICT OB retest)

Literatura:
- Wyckoff BUEC (Back Up to Edge of Creek): price retraces TO broken resistance (now support), stays ABOVE it
- Wyckoff LPS (Last Point of Support): higher low ABOVE previous resistance
- ICT Order Block retest: after BOS up, price returns to bullish OB in discount zone
- ATS pullback to expansion line: "buy below value in uptrend"

**Critical literatura requirement:** entry LONG em pullback = preço **ACIMA** da resistência quebrada (agora suporte). Nunca abaixo.

**Verificar em código:**
```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _patch2a_continuation_trigger|pullback|DELTA" -Context 0,40
```

**Reportar com rigor:**
- `_patch2a_continuation_trigger` existe? (:1868 per FASE 1.2)
- Condições actuais:
  - Phase requirement? (TREND UP obrigatório?)
  - Price position requirement? (acima do box_high? entre box_high e box_high + X ATR?)
  - Pullback depth requirement? (retrace mínimo/máximo?)
  - Confirmation signal? (L2? iceberg? delta_4h?)
- **Compare com literatura** — é BUEC-consistent? Ou inventou condições?

### 3.4 GAMMA LONG — Momentum Stacking

Literatura:
- ATS: trend continuation em momentum M30 bullish + HTF aligned
- Não é conceito Wyckoff/ICT clássico puro

**Verificar em código:**
```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "check_gamma_trigger|GAMMA" -Context 0,30
```

Reportar status + condições.

---

## PASSO 4 — Auditoria Overextension Reversals

### 4.1 Localizar código

```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "overextended|OVEREXTEND|1\.5.*ATR|atr.*1\.5" -Context 0,30
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _resolve_direction" -Context 0,80
```

**Reportar:**
- Código literal de overextension check
- Threshold exacto (1.5×ATR? outro valor?)
- Origem do threshold:
  - Está em config? `settings.json` permite override?
  - Hardcoded?
  - Há comment indicando calibração ou decisão data-driven?

### 4.2 Quantificar ocorrências no decision_log

```python
import json
from pathlib import Path
from collections import Counter

decisions = []
log_path = Path("C:\\FluxQuantumAI\\logs\\decision_log.jsonl")
with open(log_path) as f:
    for line in f:
        d = json.loads(line)
        reason = (d.get("decision", {}).get("reason", "") 
                  + " " + d.get("strat_reason", ""))
        if any(k in reason.upper() for k in ["OVEREXTEND", "OVEREXT"]):
            decisions.append(d)

print(f"Overextension-related decisions: {len(decisions)}")
# Breakdown by action + direction
action_dir = Counter(
    (d.get("decision",{}).get("action",""), 
     d.get("decision",{}).get("direction",""))
    for d in decisions
)
for (a, dir_), c in action_dir.most_common():
    print(f"  {a} {dir_}: {c}")

# GO signals specifically
overext_gos = [d for d in decisions if d.get("decision",{}).get("action") == "GO"]
print(f"\nOverextension GO signals: {len(overext_gos)}")
for d in overext_gos[:10]:
    print(f"  {d.get('timestamp')} | {d.get('decision',{}).get('direction')} | "
          f"price={d.get('price_mt5')} | reason={d.get('decision',{}).get('reason','')[:80]}")
```

**Reportar:**
- Total count overextension decisions
- Breakdown GO vs BLOCK, LONG vs SHORT
- Data range coverage (first/last overextension decision)

### 4.3 Join com trades.csv — outcome P/L se possível

```python
import pandas as pd

try:
    trades = pd.read_csv("C:\\FluxQuantumAI\\logs\\trades.csv")
    # Match by timestamp proximity or decision_id
    # Compute P/L for overextension trades
    
    overext_tickets = [...]  # match decisions to trades
    print(f"Overextension trades matched: {len(overext_tickets)}")
    # Aggregate P/L, WR
except Exception as e:
    print(f"Cannot compute P/L: {e}")
```

**Reportar:**
- Count de trades overextension que foram matched
- P/L total / médio
- Win rate
- Se poucos trades (<10), flag "sample insufficient"

### 4.4 Literatura check

Responder com rigor:
- Overextension reversal (TREND + wrong side level → opposite direction) está em Wyckoff? Citar capítulo se sim.
- Está em ICT? Citar PD array concept se relacionado.
- Está em ATS? Citar se houver.
- Se NÃO está, de onde veio? (grep git history do commit que introduziu?)

```powershell
cd C:\FluxQuantumAI
git log -p --all --source -S "overextended" -- live/event_processor.py | head -100
git log -p --all --source -S "1.5.*atr" -- live/event_processor.py | head -100
```

**Reportar:** commit SHA, autor (Claude / ClaudeCode / manual?), mensagem commit, data.

---

## PASSO 5 — Auditoria Pullback/DELTA vs Literatura

### 5.1 Implementação actual vs literatura

Para cada check de pullback no código (`_patch2a_continuation_trigger` e afins):

| Condição no código | Literatura sugere | Match? |
|---|---|---|
| `price >= box_high` após TREND UP confirmed | BUEC: price returns to broken resistance (now support), stays ABOVE | ? |
| Pullback depth min/max | Wyckoff: pullback deve manter-se em LPS (acima do SOS bar) | ? |
| Confirmation signal | L2 absorption? iceberg bullish? | ? |
| Direction logic | Só LONG se TREND UP + pullback válido | ? |

**Para cada linha, reportar:**
- Código literal
- Condição textual
- Bate com literatura? Sim/Não/Parcialmente + explicação

### 5.2 Git history — quando foi "implementado"?

```powershell
cd C:\FluxQuantumAI
git log --all --source -S "_patch2a_continuation_trigger" -- live/event_processor.py | head -50
git log --all --source -S "pullback" -- live/event_processor.py | head -100
```

**Reportar:**
- Commit SHA + data + autor de cada "implementação" pullback
- Mensagem commit — dizia "implementado"? Ou "wip"?
- Há tests dedicados a pullback? Se sim, quantos passaram?

### 5.3 Ocorrências em decision_log

```python
# Pullback-related decisions
pullback_decisions = [d for d in decisions if any(
    k in (d.get("decision",{}).get("reason","") + " " + d.get("trigger","")).upper()
    for k in ["PULLBACK", "DELTA", "PATCH2A", "CONTINUATION"]
)]
print(f"Pullback-triggered decisions: {len(pullback_decisions)}")
```

**Reportar:**
- Count
- Se ZERO ou muito baixo: confirma suspeita "nunca funcionou"
- Se >0: outcome (GO vs BLOCK)

---

## PASSO 6 — Consolidated Report

Criar `$sprint_dir\ARCHITECTURAL_AUDIT_READ_ONLY.md`:

```markdown
# Architectural Audit — Read-Only (2026-04-20)

**Scope:** (1) LONG perdido 02-03h30, (2) Overextension reversals, (3) Pullback/DELTA vs literatura
**Mode:** READ-ONLY (zero edits)
**Evidence basis:** decision_log.jsonl + código + git history + trades.csv

---

## PART 1 — LONG MISSED 02:00-03:30 UTC

### 1.1 Market state
- Open: $X
- Close: $Y  
- Bullish candles identified: N
- Timestamps: [list]

### 1.2 APEX internal state
- Phase transitions: [timeline]
- M30 bias: [timeline]
- Decisions emitted: X (GO=Y, BLOCK=Z, LONG=A, SHORT=B)

### 1.3 Triggers LONG analysis (per literatura)

| Trigger | Literatura ref | Opportunity in window? | Fired? | Root cause if not |
|---|---|---|---|---|
| ALPHA LONG (liq_bot touch) | Wyckoff Spring | [Y/N] | [Y/N] | [reason] |
| ALPHA Spring (fakeout DN) | Wyckoff Spring | [Y/N] | [Y/N] | [reason] |
| DELTA LONG (pullback) | Wyckoff BUEC | [Y/N] | [Y/N] | [reason] |
| GAMMA LONG (momentum) | ATS trend continuation | [Y/N] | [Y/N] | [reason] |

### 1.4 Root cause hypothesis (Part 1)
[A/B/C/D with confidence]

---

## PART 2 — OVEREXTENSION REVERSALS AUDIT

### 2.1 Código actual
[file:line]
```python
[código literal]
```
Threshold: X×ATR
Origin: [hardcoded / config / commit SHA]

### 2.2 Decision log quantification
- Total overextension decisions: X
- GO signals: Y (LONG=A, SHORT=B)
- BLOCK: Z
- Date range: [first, last]

### 2.3 Trades outcome (if matched)
- Matched trades: N
- Total P/L: $X
- Win rate: Y%
- Sample size flag: [SUFFICIENT / INSUFFICIENT]

### 2.4 Literatura check
- Wyckoff: [found/not found] — reference: [chapter/section]
- ICT: [found/not found] — reference: [concept]
- ATS: [found/not found] — reference: [source]
- Origin of "TREND + wrong-side + overext → opposite direction" logic: [git commit SHA + author + date]

### 2.5 Verdict
- Literatura-aligned: [YES / NO / AMBIGUOUS]
- Data-driven validated: [YES / NO]
- Sample size: [N decisions, M trades]
- Recommendation: [REMOVE / PRESERVE / RECALIBRATE]

---

## PART 3 — PULLBACK/DELTA vs LITERATURA

### 3.1 Implementation actual
Code: `_patch2a_continuation_trigger` at event_processor.py:1868

Conditions:
[list with line numbers]

### 3.2 Literatura alignment matrix

| Condition in code | Literatura expected | Match? |
|---|---|---|
| ... | ... | ✓/✗/partial |

### 3.3 Git history
- Commits touching pullback logic: [count]
- First implementation: [SHA + date + message]
- Latest modification: [SHA + date + message]
- Tests present: [count + pass rate]

### 3.4 Decision log ocurrences
- Pullback-triggered decisions: X
- In last 30 days: Y
- Barbara's claim "never worked": [SUPPORTED / REFUTED by data]

### 3.5 Verdict
- Literatura-aligned: [YES / NO / PARTIAL]
- Actually functional: [YES / NO]
- Recommendation: [REWRITE / PRESERVE / TUNE]

---

## PART 4 — OVERALL FINDINGS

### 4.1 Summary table

| Mechanism | Literatura-aligned | Data-driven validated | Actually used | Recommendation |
|---|---|---|---|---|
| ALPHA liq_top/bot | YES | ? | ? | ? |
| ALPHA Spring fakeout | YES | ? | ? | ? |
| DELTA pullback | ? | ? | ? | ? |
| GAMMA momentum | ATS-source | ? | ? | ? |
| Overextension reversal | NO | NO | ? | ? |

### 4.2 Architectural smell ranking

Ranked by severity (NON-literatura + UN-validated + USED in production):
1. [most concerning]
2. ...

### 4.3 Proposed next sprints (based on findings)

Priority ordered:
- Sprint X: [action]
- Sprint Y: [action]

---

## PART 5 — SYSTEM STATE DURING INVESTIGATION

- Files modified: ZERO
- Restarts: ZERO
- Capture processes: 3/3 intact
- Decision log reads: [count]
- Code greps: [count]
- Git queries: [count]
```

---

## COMUNICAÇÃO FINAL

```
ARCHITECTURAL AUDIT COMPLETE (READ-ONLY)

PART 1 (LONG missed 02-03h30):
- Root cause: [A/B/C/D]
- Confidence: [HIGH/MEDIUM/LOW]

PART 2 (Overextension):
- Literatura-aligned: [Y/N]
- Data-driven: [Y/N]
- Sample: X decisions, Y trades, P/L $Z
- Recommendation: [REMOVE/PRESERVE/RECALIBRATE]

PART 3 (Pullback/DELTA):
- Literatura-aligned: [Y/N/PARTIAL]
- Functional in production: [Y/N]
- Barbara's "never worked" claim: [SUPPORTED/REFUTED]
- Recommendation: [REWRITE/PRESERVE/TUNE]

Report: ARCHITECTURAL_AUDIT_READ_ONLY.md
System state: Running, capture 3/3 intact, zero changes.

Aguardo Claude + Barbara review para definir próximo sprint.
```

---

## PROIBIDO

- ❌ Qualquer edit a código
- ❌ Restart serviço
- ❌ Tocar capture processes
- ❌ Propor soluções (só factos + hipóteses)
- ❌ Dizer "implementado" sem mostrar código literal + linha
- ❌ Citar literatura sem referência específica
- ❌ Assumir thresholds são "provavelmente calibrados" — mostrar commit ou dizer "NÃO VALIDADO"
- ❌ Skipar qualquer PASSO 1-6
- ❌ Saltar git history — é essencial para rastrear quando coisas foram inventadas

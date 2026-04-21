# TASK: INVESTIGAÇÃO READ-ONLY — Porque sistema perdeu oportunidade LONG 02h-03h30 UTC 2026-04-20?

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Sprint:** entry_logic_fix_20260420
**Mode:** 100% READ-ONLY. Zero edits, zero restarts.
**Output:** `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\MISSED_LONG_INVESTIGATION.md`

---

## CONTEXTO

Barbara observou que entre 02:00 e 03:30 UTC hoje (2026-04-20) houve 3 velas M5 significativas de alta em XAUUSD (mercado recuperou fortemente). Sistema **não emitiu nenhum sinal LONG** durante esta janela — perdeu oportunidade.

Pela literatura Wyckoff/ICT/ATS:
- 3 velas M5 altas após downtrend prévio = potencial **Spring** (Wyckoff) ou **CHoCH+pullback** (ICT)
- Devia ter sido capturado via uma destas entradas:
  - **ALPHA LONG**: fakeout DN (liq_bot varrido, preço recupera) — spring clássico
  - **DELTA LONG**: pullback a box_high que virou suporte após breakout
  - **GAMMA LONG**: momentum stacking M30 bullish em TREND UP

**Arquitectura existente (per memórias de sessões anteriores, FUNC_V3_RL_Predictive_Model):**

TABELA 3 — Entry Types:
| Entry | Contexto | Trigger | Direcção |
|---|---|---|---|
| ALPHA | CONTRACTION | Preço toca liq_bot | LONG |
| ALPHA | EXPANSION | Fakeout DN (confirmed=False, breakout_dir=DN) | LONG (Spring) |
| GAMMA | TREND UP | Momentum stacking M30 bullish | LONG |
| DELTA | TREND UP após pullback | Pullback a box_high (agora suporte) + L2 | LONG |

Regra de Barbara: "M30 box comanda execução" (M30_bias/phase manda, apesar de D1/H4 informarem bias).

**Questão central:** Por que nenhum destes triggers disparou?

---

## REGRA CRÍTICA — ZERO EDITS

- Apenas leitura, inspecção, correlação
- Não restart, não deploy
- Não tocar capture processes (12332, 8248, 2512)
- Não chamar funções activas (mt5.initialize etc.)
- Se algo não claro, reportar — não adivinhar

---

## PASSO 1 — Reconstruir estado do mercado 02:00-03:30 UTC

### 1.1 Price action (M1, M5, M30)

```powershell
# Ler microstructure CSV para reconstruir velas
$csv = "C:\data\level2\_gc_xcec\microstructure_2026-04-20.csv.gz"
# ou fallback dia anterior se ainda não foi rotacionado

# Usar Python para parquet leitura:
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
```

```python
import pandas as pd

# M5 parquet
m5 = pd.read_parquet("C:\\data\\processed\\gc_m5_boxes.parquet")
# Filtrar timestamp 2026-04-20 01:00 a 04:00 UTC
m5_window = m5[(m5.index >= "2026-04-20 01:00:00") & (m5.index <= "2026-04-20 04:00:00")]
print("M5 BARS:")
print(m5_window[["open", "high", "low", "close", "m5_box_id", "m5_box_confirmed", 
                 "m5_liq_top", "m5_liq_bot", "box_high", "box_low"]].to_string())

# M30 parquet
m30 = pd.read_parquet("C:\\data\\processed\\gc_m30_boxes.parquet")
m30_window = m30[(m30.index >= "2026-04-20 01:00:00") & (m30.index <= "2026-04-20 04:00:00")]
print("M30 BARS:")
print(m30_window[["open", "high", "low", "close", "m30_box_id", "m30_box_confirmed",
                  "m30_liq_top", "m30_liq_bot", "daily_jac_dir", "breakout_dir"]].to_string())

# OHLCV L2 joined
ohlc = pd.read_parquet("C:\\data\\processed\\gc_ohlcv_l2_joined.parquet")
ohlc_window = ohlc[(ohlc.index >= "2026-04-20 01:00:00") & (ohlc.index <= "2026-04-20 04:00:00")]
print(f"Minutes in window: {len(ohlc_window)}")
print(f"Price range: {ohlc_window['close'].min():.2f} to {ohlc_window['close'].max():.2f}")
print(f"Open to close: {ohlc_window['close'].iloc[0]:.2f} → {ohlc_window['close'].iloc[-1]:.2f}")
```

**Reportar:**
- Price open 02:00, close 03:30
- Range da janela (high, low)
- Quantos M5 boxes novos foram criados
- Quantos M30 boxes novos foram criados
- box_confirmed transitions
- breakout_dir changes

### 1.2 Velas "enormes de alta" mencionadas por Barbara

Identificar as 3 velas M5 de alta:
```python
m5_window["body"] = m5_window["close"] - m5_window["open"]
m5_window["is_bullish"] = m5_window["body"] > 0
m5_window_bullish = m5_window[m5_window["is_bullish"]].sort_values("body", ascending=False)
print("TOP 5 bullish M5 candles 02:00-03:30:")
print(m5_window_bullish[["open", "close", "body"]].head().to_string())
```

**Reportar:** timestamps + magnitudes das 3 velas. Confirmar que existem e quando.

---

## PASSO 2 — Reconstruir estado interno APEX 02:00-03:30

### 2.1 decision_log.jsonl — todas as decisões

```python
import json
from pathlib import Path

decisions = []
log_path = Path("C:\\FluxQuantumAI\\logs\\decision_log.jsonl")
with open(log_path) as f:
    for line in f:
        d = json.loads(line)
        ts = d.get("timestamp", "")
        if "2026-04-20" in ts and any(h in ts for h in ["T01:", "T02:", "T03:"]):
            decisions.append(d)

print(f"Decisions in window: {len(decisions)}")
# Group by action
from collections import Counter
action_counts = Counter(d.get("decision", {}).get("action", "UNKNOWN") for d in decisions)
print(f"Actions: {action_counts}")

# Group by direction
dir_counts = Counter(d.get("decision", {}).get("direction", "UNKNOWN") for d in decisions)
print(f"Directions: {dir_counts}")

# Any LONG signals emitted?
longs = [d for d in decisions if d.get("decision", {}).get("direction") == "LONG"]
print(f"LONG signals: {len(longs)}")

# First few decisions detail
for d in decisions[:5]:
    print(json.dumps({
        "ts": d.get("timestamp"),
        "action": d.get("decision", {}).get("action"),
        "direction": d.get("decision", {}).get("direction"),
        "trigger": d.get("trigger"),
        "phase": d.get("phase"),
        "daily_trend": d.get("daily_trend"),
        "m30_bias": d.get("m30_bias"),
        "reason": d.get("decision", {}).get("reason", "")[:100],
    }, indent=2))
```

**Reportar:**
- Total decisions na janela
- Quantos GO vs BLOCK
- Quantas direções LONG vs SHORT emitidas
- Se houve GO LONG: detalhes
- Se só houve BLOCK ou GO SHORT: por que não LONG?

### 2.2 Phase detection transitions

```python
# Extrair phase em cada timestamp
phases_over_time = [(d.get("timestamp"), d.get("phase", "")) for d in decisions]
print("Phase transitions:")
prev = None
for ts, phase in phases_over_time:
    if phase != prev:
        print(f"  {ts}: {phase}")
        prev = phase
```

**Reportar:**
- Phase começou como? (CONTRACTION / EXPANSION / TREND_UP / TREND_DN)
- Houve transição para TREND UP durante janela?
- Se sim, a que horas?
- Se não, porque não (dado que preço subiu)?

### 2.3 M30 bias transitions

```python
bias_over_time = [(d.get("timestamp"), d.get("m30_bias", "")) for d in decisions]
print("M30 bias transitions:")
prev = None
for ts, bias in bias_over_time:
    if bias != prev:
        print(f"  {ts}: {bias}")
        prev = bias
```

**Reportar:** m30_bias flipped de bearish para bullish? Quando?

---

## PASSO 3 — Triggers LONG investigação

Para cada tipo de trigger LONG, verificar se as condições foram satisfeitas e se trigger disparou.

### 3.1 ALPHA LONG (liq_bot touch em CONTRACTION)

```python
# Para cada decisão onde phase=CONTRACTION durante janela
contraction_decisions = [d for d in decisions if d.get("phase") == "CONTRACTION"]

for d in contraction_decisions:
    ts = d.get("timestamp")
    price = d.get("price_mt5", 0)
    liq_bot = d.get("liq_bot_mt5", 0)
    trigger = d.get("trigger", "")
    direction_emitted = d.get("decision", {}).get("direction", "")
    
    # Did price touch liq_bot?
    if liq_bot > 0 and abs(price - liq_bot) <= 10:
        print(f"{ts}: price={price:.2f} near liq_bot={liq_bot:.2f} "
              f"(dist={abs(price-liq_bot):.1f}), trigger={trigger}, dir_emitted={direction_emitted}")
```

**Reportar:**
- Preço tocou liq_bot durante a janela?
- Se sim, trigger ALPHA disparou? Direcção emitida foi LONG?
- Se NÃO disparou apesar de tocar: porquê?

### 3.2 ALPHA LONG Spring (fakeout DN)

Verificar se houve fakeout DN (breakout_dir="DN" mas box_confirmed=False):

```python
m30_window_sorted = m30_window.sort_index()
for idx, row in m30_window_sorted.iterrows():
    breakout_dir = row.get("breakout_dir", "")
    confirmed = row.get("m30_box_confirmed", None)
    if breakout_dir == "DN" and confirmed == False:
        print(f"{idx}: FAKEOUT DN detectado — box_id={row.get('m30_box_id')}")
```

**Reportar:**
- Houve fakeout DN durante 02:00-03:30?
- Se sim, sistema gerou ALPHA LONG?

### 3.3 DELTA LONG (pullback a box_high)

```python
# Se phase foi TREND_UP durante a janela
trend_up_decisions = [d for d in decisions if "TREND" in d.get("phase", "") and d.get("m30_bias") == "bullish"]

for d in trend_up_decisions[:10]:
    ts = d.get("timestamp")
    price = d.get("price_mt5", 0)
    box_high = d.get("box_high_mt5", 0)
    trigger = d.get("trigger", "")
    print(f"{ts}: price={price:.2f} box_high={box_high:.2f} trigger={trigger}")
    
    # Pullback to box_high from above?
    if box_high > 0 and abs(price - box_high) <= 10 and price >= box_high:
        print(f"   *** DELTA LONG opportunity: price pulled back to box_high")
```

**Reportar:**
- Sistema entrou em TREND UP phase durante a janela?
- Se sim, houve pullback a box_high?
- Trigger DELTA disparou?
- Código de `_check_delta_trigger` está functional? (ver se é chamado no event loop)

### 3.4 GAMMA LONG (momentum stacking)

```python
# Similar logic — phase TREND_UP + momentum M30 bullish
# Verificar delta_4h, m30_impulse em cada decisão
for d in trend_up_decisions[:10]:
    ts = d.get("timestamp")
    delta_4h = d.get("delta_4h", 0)
    trigger = d.get("trigger", "")
    print(f"{ts}: delta_4h={delta_4h:.0f} trigger={trigger}")
```

**Reportar:**
- Houve momentum stacking M30 bullish?
- Trigger GAMMA disparou?
- Ou GAMMA trigger nem sequer é invocado em production?

---

## PASSO 4 — Verificar código dos triggers LONG

### 4.1 DELTA trigger

```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _check_delta_trigger|def _check_pullback|_patch2a|DELTA" -Context 0,40
```

**Reportar:**
- `_check_delta_trigger` existe?
- Se sim, é chamado no event loop?
- Que condições verifica?
- Tem lógica de pullback detection?

### 4.2 GAMMA trigger

```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _check_gamma_trigger|GAMMA|momentum_stack" -Context 0,40
```

**Reportar:**
- `_check_gamma_trigger` existe?
- Condições?
- É invocado?

### 4.3 Phase detection

```powershell
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _detect_phase|self.phase\s*=|TREND_UP|TREND_DN" -Context 0,30
```

**Reportar:**
- Como transita CONTRACTION → TREND?
- Que condições são necessárias?
- Há lógica de Movement Detector ou só JAC?

### 4.4 Movement Detector (per memory: S3-G6 não implementado)

```powershell
Select-String -Path "C:\FluxQuantumAI\**\*.py" -Pattern "MovementDetector|movement_detector|S3-G6|Sprint.*S3.*G6" -Context 0,10
```

**Reportar:** Movement Detector está implementado ou stub?

---

## PASSO 5 — Análise do "porquê não capturou"

Com base em PASSO 1-4, construir hipótese concreta:

### Cenário A — Phase stuck em CONTRACTION
- Sistema não detectou TREND UP transition apesar de preço subir
- Causa: JAC não confirmou (breakout < box_high? ou outro check failed?)
- Impacto: DELTA/GAMMA triggers não foram sequer considerados (só disparam em TREND)

### Cenário B — Phase mudou para TREND UP mas trigger não disparou
- DELTA trigger existe mas tem bug em pullback detection
- GAMMA trigger requer condições não satisfeitas (delta_4h? momentum?)

### Cenário C — Trigger disparou mas foi bloqueado por gate
- Gate V1 direction-blind bloqueou (nosso bug já conhecido)
- news_gate bloqueou (improvável, windows aplicadas)
- defense_mode bloqueou (imbalance L2)

### Cenário D — Arquitectura não suporta este setup
- Movement Detector (pullback validation) não implementado
- DELTA trigger é só stub

---

## PASSO 6 — Consolidated Report

Criar `$sprint_dir\MISSED_LONG_INVESTIGATION.md`:

```markdown
# Missed LONG Opportunity — 02:00-03:30 UTC 2026-04-20 — Investigation

**Timestamp:** <UTC>
**Mode:** READ-ONLY
**Duration:** X min

## 1. Market State (02:00-03:30 UTC)

### Price action
- Open 02:00: $X
- Close 03:30: $Y
- Range: [low, high]
- 3 bullish candles identified: [timestamps + bodies]

### M5/M30 boxes
- New M5 boxes: N created
- New M30 boxes: M created
- box_confirmed transitions: [list]
- breakout_dir changes: [list]

## 2. APEX Internal State

### Decisions logged
- Total: X
- GO: Y (directions: [LONG=A, SHORT=B])
- BLOCK: Z

### Phase transitions
[timeline]

### M30 bias transitions
[timeline]

## 3. LONG Triggers Analysis

### ALPHA LONG (liq_bot touch)
- Price touched liq_bot: YES/NO
- Trigger fired: YES/NO
- Rationale: ...

### ALPHA LONG Spring (fakeout DN)
- Fakeout DN detected: YES/NO
- Trigger fired: YES/NO
- Rationale: ...

### DELTA LONG (pullback to box_high)
- TREND UP phase reached: YES/NO
- Pullback to box_high observed: YES/NO
- Trigger fired: YES/NO
- Code status: [functional/stub/missing]

### GAMMA LONG (momentum stacking)
- TREND UP phase reached: YES/NO
- Momentum stacking conditions: [list]
- Trigger fired: YES/NO
- Code status: [functional/stub/missing]

## 4. Code Status

### DELTA trigger
- File: event_processor.py:XXXX
- Status: [functional/stub/missing]
- Conditions checked: [list]

### GAMMA trigger
[same]

### Phase detection
- JAC logic: [status]
- Movement Detector: [NOT IMPLEMENTED per memory S3-G6]

## 5. Root Cause Hypothesis (ranked)

### Hypothesis A: [e.g., Phase stuck in CONTRACTION]
- Evidence: ...
- Confidence: HIGH/MEDIUM/LOW

### Hypothesis B: ...
### Hypothesis C: ...
### Hypothesis D: ...

## 6. Recommendations (for future sprint)

Based on root cause:
- If A: fix phase detection logic
- If B: debug DELTA/GAMMA trigger conditions
- If C: fix gate blocking (likely overlap with near_level fix)
- If D: implement Movement Detector

**NO FIX in this investigation. Read-only. Report only.**

## 7. System State
- Files modified: ZERO
- Restarts: ZERO
- Capture processes: 3/3 intact
```

---

## COMUNICAÇÃO FINAL

```
MISSED LONG INVESTIGATION COMPLETE (READ-ONLY)

Market 02:00-03:30 UTC: price moved $X → $Y (3 bullish M5 candles confirmed)
APEX emitted: A GOs, B BLOCKs, directions: [LONG=?, SHORT=?]
Top hypothesis: [A/B/C/D] — [confidence]
Evidence: [key finding]

Report: MISSED_LONG_INVESTIGATION.md

System state: Running, capture 3/3 intact, zero changes.
Aguardo Claude + Barbara review antes de qualquer fix.
```

---

## PROIBIDO

- ❌ Qualquer edit a código
- ❌ Restart serviço
- ❌ Tocar capture processes
- ❌ Propor soluções durante investigação (só factos + hipóteses)
- ❌ Assumir que arquitectura funciona como devia — verificar evidência
- ❌ Skipar qualquer passo 1-6

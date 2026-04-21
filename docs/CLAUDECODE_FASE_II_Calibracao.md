# TASK: FASE II — CALIBRAÇÃO DATA-DRIVEN news_gate

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Pre-requisito:** FASE I completa e validada (4 features restored, sistema Running)
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\FASE_II_REPORT.md`

---

## OBJECTIVO

Propor thresholds data-driven para `news_config.yaml` e `country_relevance_gold.json` baseados em evidência empírica, usando:
- Calendário económico US histórico
- Trades live reais (`trades.csv`)
- Dados microstructure (`calibration_dataset_full.parquet`)

**Output é PROPOSTA com evidência, não aplicação automática.** Barbara + Claude revêem antes de aplicar ao yaml.

---

## REGRA CRÍTICA — ZERO ASSUMPTIONS

**Não assumir estruturas de dados, colunas, formatos, schemas.** Sempre fazer **discovery READ-ONLY primeiro**, reportar o que encontrou, **só depois propor transformação**. Se algo não está claro, PARAR e reportar, não adivinhar.

---

## PASSO 1 — Discovery dos datasets

**Reportar estrutura real de cada dataset antes de qualquer processamento.**

### 1.1 Economic Calendar

```python
import pandas as pd
path = r"C:\FluxQuantumAI\Economic_Calendar_History_2025-2026.xlsx"
sheets = pd.read_excel(path, sheet_name=None)
for name, df in sheets.items():
    print(f"Sheet: {name}")
    print(f"Shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print(f"Dtypes: {df.dtypes.to_dict()}")
    print(f"First 10 rows:")
    print(df.head(10).to_string())
    print(f"Unique 'Imp.' values: {df['Imp.'].value_counts(dropna=False).to_dict() if 'Imp.' in df.columns else 'N/A'}")
    print(f"Unique 'Cur.' values: {df['Cur.'].value_counts(dropna=False).to_dict() if 'Cur.' in df.columns else 'N/A'}")
    print()
```

### 1.2 Trades live (RoboForex)

```python
trades_path = r"C:\FluxQuantumAI\logs\trades.csv"
trades = pd.read_csv(trades_path)
print(f"Trades shape: {trades.shape}")
print(f"Columns: {list(trades.columns)}")
print(f"Dtypes: {trades.dtypes.to_dict()}")
print(f"First 5 rows:")
print(trades.head().to_string())
print(f"Last 5 rows:")
print(trades.tail().to_string())
print(f"Timestamp range: {trades.iloc[0]} to {trades.iloc[-1]}")
```

**REPORTAR:** nome exacto das colunas para timestamp, direction, PnL, lot sizes, exit reason, etc. **Não assumir nomes.**

### 1.3 Calibration dataset (microstructure)

```python
cal_path = r"C:\data\processed\calibration_dataset_full.parquet"  # verificar se existe
# OR
# r"C:\FluxQuantumAI\data\processed\..." 
# Descobrir localização real primeiro:
```

```powershell
Get-ChildItem "C:\FluxQuantumAI\data\processed" -Filter "*.parquet" | Select-Object Name, Length, LastWriteTime
Get-ChildItem "C:\data\processed" -Filter "*.parquet" -ErrorAction SilentlyContinue | Select-Object Name, Length, LastWriteTime
```

```python
import pandas as pd
# Usar path correcto encontrado acima
df = pd.read_parquet(path)
print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)}")
print(f"Dtypes: {df.dtypes.to_dict()}")
print(f"Memory: {df.memory_usage(deep=True).sum() / 1e9:.2f} GB")
print(f"Timestamp column(s): [identificar]")
print(f"Timestamp range: [min to max]")
print(f"First row:")
print(df.iloc[0])
```

**REPORTAR:** nomes exactos das colunas disponíveis. Quais L2 indicators (dom_imbalance? absorption_ratio? spread? depth?). Quais iceberg. Quais anomaly. **Não assumir.**

### 1.4 Output do Passo 1

Criar `DISCOVERY_REPORT.md` com:
- Calendar: sheets, colunas, dtypes, sample rows, importance values observados, currency filter
- Trades: colunas, timestamp column name, PnL column name, direction column name, date range, total trades
- Calibration dataset: path real, colunas, dtypes, timestamp column, date range, memory footprint
- **Gaps detectados:** se alguma coluna esperada não existe, reportar explicitamente

**STOP após Passo 1.** Não avançar sem reportar estes findings.

---

## PASSO 2 — Processar calendário económico

**Usar nomes de colunas REAIS descobertos no Passo 1.**

### 2.1 Parse estrutura (linhas de data + linhas de eventos)

A estrutura observada no ficheiro tem:
- Linhas de header com datas (ex: "Tuesday, 1 July 2025")
- Linhas de eventos com hora (ex: "13:30:00")

Forward-fill da data, construir timestamp completo.

**Verificar antes:** se o formato descoberto no Passo 1 confirma isto. Se diferir, adaptar.

### 2.2 Classificar importance

Como `Imp.` só marca "Holiday", classificar por keyword matching em `Event`:

```python
HIGH_KEYWORDS = [
    'Nonfarm Payrolls', 'CPI', 'PPI', 'FOMC', 'Fed Chair Powell',
    'Unemployment Rate', 'Core PCE', 'GDP'
]
MEDIUM_KEYWORDS = [
    'ISM', 'Retail Sales', 'JOLTS', 'CB Consumer Confidence',
    'Philadelphia Fed', 'Average Hourly Earnings', 'Durable Goods'
]
LOW_KEYWORDS = [
    'Initial Jobless Claims', 'Crude Oil Inventories', 'ADP'
]

def classify(event):
    if pd.isna(event): return 'UNKNOWN'
    e = str(event).lower()
    for kw in HIGH_KEYWORDS:
        if kw.lower() in e: return 'HIGH'
    for kw in MEDIUM_KEYWORDS:
        if kw.lower() in e: return 'MEDIUM'
    for kw in LOW_KEYWORDS:
        if kw.lower() in e: return 'LOW'
    return 'LOW'
```

### 2.3 Timezone handling

Verificar no ficheiro se times são ET ou UTC. Se ambíguo, **REPORTAR** e pedir clarificação.

Assumindo ET: converter para UTC adicionando 4-5h (DST depende do mês).

### 2.4 Output

Saída: `C:\FluxQuantumAI\data\processed\news_calendar_us_2025_2026.parquet`

Colunas: `ts_utc, event, importance, actual, forecast, previous`

Report: N eventos por importance (HIGH / MEDIUM / LOW).

---

## PASSO 3 — Enriquecer trades.csv com contexto news

**Usar nome de coluna timestamp REAL descoberto no Passo 1.**

Para cada trade, determinar:
- Distância em minutos ao evento US mais próximo
- Importance desse evento
- Bucket temporal: PRE_NEWS_5 / PRE_NEWS_30 / PRE_NEWS_60 / DURING / POST_NEWS_5 / POST_NEWS_30 / POST_NEWS_60 / NO_NEWS

```python
def news_context(trade_ts, calendar, window_min=60):
    lo = trade_ts - pd.Timedelta(minutes=window_min)
    hi = trade_ts + pd.Timedelta(minutes=window_min)
    nearby = calendar[(calendar['ts_utc'] >= lo) & (calendar['ts_utc'] <= hi)]
    if len(nearby) == 0:
        return {'bucket': 'NO_NEWS', 'importance': 'NONE', 'min_to_event': None, 'event': None}
    nearby = nearby.copy()
    nearby['delta_min'] = (nearby['ts_utc'] - trade_ts).dt.total_seconds() / 60
    closest = nearby.iloc[nearby['delta_min'].abs().argmin()]
    delta = closest['delta_min']
    # Buckets: positive delta = event is in future (trade is PRE)
    if delta > 30: bucket = 'PRE_NEWS_60'
    elif delta > 5: bucket = 'PRE_NEWS_30'
    elif delta > 0: bucket = 'PRE_NEWS_5'
    elif delta == 0: bucket = 'DURING'
    elif delta > -5: bucket = 'POST_NEWS_5'
    elif delta > -30: bucket = 'POST_NEWS_30'
    else: bucket = 'POST_NEWS_60'
    return {
        'bucket': bucket,
        'importance': closest['importance'],
        'min_to_event': delta,
        'event': closest['event']
    }
```

Saída: `C:\FluxQuantumAI\data\processed\trades_news_enriched.csv`

---

## PASSO 4 — Análise estatística

Por cada combinação (importance × bucket):

```python
summary = enriched.groupby(['importance', 'bucket']).agg(
    count=('pnl', 'size'),  # usar coluna PnL real
    total_pnl=('pnl', 'sum'),
    avg_pnl=('pnl', 'mean'),
    median_pnl=('pnl', 'median'),
    win_rate=('pnl', lambda x: (x > 0).mean()),
    max_loss=('pnl', 'min'),
    max_win=('pnl', 'max'),
    std_pnl=('pnl', 'std')
).round(2)
```

**Baseline:** NO_NEWS category (trades sem evento news próximo).

**Teste estatístico:** cada combinação (importance HIGH × bucket PRE_NEWS_30) vs baseline NO_NEWS:
- T-test diferença de médias
- Mann-Whitney U (não-paramétrico)
- Effect size (Cohen's d)
- Significância a p<0.05

**Report:** tabela com todas as combinações + indicação de quais são **estatisticamente significativas** vs baseline.

---

## PASSO 5 — Proposta de thresholds data-driven

Baseado na análise do Passo 4, propor:

### 5.1 Windows (pre/post event)

Qual bucket mostra degradação significativa?
- Se PRE_NEWS_30 é significativamente negativo → pre-window = 30 min
- Se PRE_NEWS_5 é o único significativo → pre-window = 5 min
- Se POST_NEWS_30 normaliza → post-window = 30 min

### 5.2 Importance threshold

Quais níveis de importance devem ser filtrados?
- HIGH sempre (quase certamente)
- MEDIUM depende da significância estatística
- LOW provavelmente skip

### 5.3 Risk score mapping

Como traduzir findings para a estrutura actual do yaml:

```yaml
# Estrutura actual descoberta no Passo 1 da FASE I
risk_thresholds:
  normal: 0.3
  caution: 0.5
  reduced: 0.7
  blocked: 0.9
  exit_all: 1.0

position_multipliers:
  normal: 1.0
  caution: 0.75
  reduced: 0.5
  blocked: 0.0
  exit_all: 0.0
```

Propor valores justificados com evidência:
- "CAUTION threshold 0.5 → bucket PRE_NEWS_30 importance=MEDIUM: WR caiu de 65% (baseline) para 52%, p=0.03, effect size 0.4 → justifica size reduction"
- etc.

### 5.4 Counterfactual backtest

Se aplicarmos as thresholds propostas aos trades históricos:
- Quantos trades teriam sido blocked?
- Quanto PnL teria sido evitado (losses evitados)?
- Quanto PnL teria sido sacrificado (wins evitados)?
- Net impact: positivo ou negativo?

---

## PASSO 6 — Proposta para country_relevance_gold.json

Actualmente só tem US (após fix FASE I). Propor se os pesos/configs default são adequados baseado em:
- Análise de correlação entre events e trades
- Quais sub-categorias de events (employment vs inflation vs monetary vs ...) têm maior impacto

Output: proposta de ajustes SE justificados empiricamente.

---

## PASSO 7 — TASK_CLOSEOUT report

Criar `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\FASE_II_REPORT.md`:

```markdown
# FASE II — Calibração Data-Driven news_gate — TASK_CLOSEOUT

**Timestamp:** <UTC>
**Duration:** X min
**Status:** ✅ SUCCESS / ⚠ PARTIAL

## Datasets Discovery (Passo 1)
### Calendar
- Path, rows, columns, date range
- Importance distribution (HIGH/MEDIUM/LOW counts)

### Trades
- Path, rows, columns
- Timestamp range
- Total PnL

### Calibration dataset
- Path real, rows, columns disponíveis
- Date range
- L2 indicators available: [list]
- Iceberg indicators available: [list]
- Anomaly indicators available: [list]
- Gaps detectados: [list]

## Statistical Analysis (Passo 4)

### Summary table
[Tabela importance × bucket com count/PnL/WR/significance]

### Baseline (NO_NEWS)
- Count: N
- Win rate: X%
- Avg PnL: $Y

### Significant findings
[Lista de combinações onde p<0.05 vs baseline]

## Proposed Thresholds (Passo 5)

### Windows
- Pre-event: X min (evidence: ...)
- Post-event: Y min (evidence: ...)

### Importance filter
- HIGH: filter (evidence: ...)
- MEDIUM: [decision] (evidence: ...)
- LOW: [decision] (evidence: ...)

### Risk thresholds
[Valores propostos com justificação]

### Counterfactual
- Trades blocked: N
- Losses avoided: $X
- Wins sacrificed: $Y
- Net impact: $Z

## Proposed country_relevance adjustments
[Proposta ou "keep defaults" com razão]

## Limitations
- Sample size: N trades em período X meses
- Event classification: keyword-based (pode ter false negatives)
- Timezone assumptions
- [outros]

## Files generated
- news_calendar_us_2025_2026.parquet
- trades_news_enriched.csv
- statistical_summary.csv

## Next steps
- Barbara + Claude review desta proposta
- Se aprovado: aplicar ao yaml
- FASE III (dataset HighRisk) — sprint separado

## Files NOT modified
- news_config.yaml: UNCHANGED (proposta only)
- country_relevance_gold.json: UNCHANGED (proposta only)
```

---

## PROIBIDO

- ❌ Modificar `news_config.yaml` ou `country_relevance_gold.json` — output é proposta
- ❌ Tocar em qualquer código live
- ❌ Tocar capture processes (PIDs 12332, 8248, 2512)
- ❌ Fazer assumptions sobre colunas/estruturas — sempre discovery primeiro
- ❌ Propor thresholds sem evidência estatística (p<0.05 + effect size)
- ❌ Skipar discovery report do Passo 1
- ❌ Skipar TASK_CLOSEOUT report

---

## COMUNICAÇÃO FINAL

```
FASE II COMPLETE — Calibração Data-Driven
Trades analysed: N
Events matched: M (HIGH=X, MEDIUM=Y, LOW=Z)
Significant findings: [count] buckets with p<0.05
Proposed changes: [summary]
Counterfactual PnL impact: $X
Report: C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\FASE_II_REPORT.md
Files: news_calendar_us_2025_2026.parquet, trades_news_enriched.csv

Aguardo Barbara + Claude review antes de aplicar thresholds.
```

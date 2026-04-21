# TASK: FIX + CALIBRAÇÃO + DATASET HIGHRISK — Execução Completa

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Deadline:** Domingo antes mercado abrir (~22h UTC)
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_<timestamp>\`

---

## CONTEXTO

Passos 0 e 1 já executados (degraded mode investigation + yaml display). Barbara aprovou prosseguir com scope completo:

**FASE I — Fix técnico:** Restaurar 4 features broken (StatGuardrail, DefenseMode, V4 IcebergInference, ApexNewsGate) + filtro US-only.

**FASE II — Calibração news_gate:** Thresholds data-driven usando calendário histórico US (`Economic_Calendar_History_2025-2026.xlsx` + `trades.csv` + `calibration_dataset_full.parquet`).

**FASE III — Dataset HighRisk + behavior fingerprint:** Análise microstructure (L2/Anomaly/Iceberg) à volta de eventos high-risk. Gerar dataset estruturado para treino ML futuro do módulo HighRisk.

---

## CRITICAL RULES

1. **FASE I primeiro** — sem sistema funcional não há calibração nem análise
2. **Backup completo antes de editar qualquer ficheiro**
3. **py_compile após cada edição**
4. **Import probe obrigatório antes de restart**
5. **Observação 10min pós-restart** — se falha, rollback
6. **FASE II e III são READ-ONLY sobre produção** — só analisam dados, não tocam em código live
7. **Não tocar capture processes** (PIDs 12332, 8248, 2512) em momento algum
8. **Reportar progresso ao fim de cada fase** — não só no final

---

# FASE I — FIX TÉCNICO

## Decisões já tomadas (não repetir discussão)

- **Thresholds news_gate:** manter defaults actuais. Calibração data-driven virá em FASE II.
- **Country relevance:** filtrar para **US-only**. Barbara não quer monitorar outros 8 países.
- **Abordagem F2:** B (path parent + import package) — 2 linhas em `event_processor.py:95-96`.

## Passo I.1 — Backup (5 min)

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sprint_dir = "C:\FluxQuantumAI\sprints\news_highrisk_$timestamp"
New-Item -Path $sprint_dir -ItemType Directory -Force | Out-Null
$backup_dir = "$sprint_dir\backup_pre_fix"
New-Item -Path $backup_dir -ItemType Directory -Force | Out-Null

$files = @(
    "C:\FluxQuantumAI\grenadier_guardrail.py",
    "C:\FluxQuantumAI\live\event_processor.py",
    "C:\FluxQuantumAI\ats_live_gate.py",
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py",
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml",
    "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\config\country_relevance_gold.json"
)

$manifest = @{}
foreach ($f in $files) {
    if (-not (Test-Path $f)) { throw "MISSING: $f" }
    $leaf = Split-Path $f -Leaf
    Copy-Item $f "$backup_dir\$leaf" -Force
    $hash = (Get-FileHash $f -Algorithm MD5).Hash
    $manifest[$leaf] = $hash
    Write-Host "backup OK: $leaf ($hash)"
}
$manifest | ConvertTo-Json | Set-Content "$backup_dir\MANIFEST.json"
```

## Passo I.2 — Aplicar F1 (path fixes — 4 linhas em 4 ficheiros)

### I.2.1 `grenadier_guardrail.py:33`

```python
# OLD:
_ANOMALY_PKG = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")
# NEW:
_ANOMALY_PKG = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")
```

### I.2.2 `event_processor.py:122`

```python
# OLD:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")))
# NEW:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")))
```

### I.2.3 `ats_live_gate.py:91`

```python
# OLD:
_ICEBERG_PKG_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Iceberg")
# NEW:
_ICEBERG_PKG_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg")
```

### I.2.4 `anomaly_scorer.py:404`

```python
# OLD:
r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly\models\grenadier_scaler_4f.json"
# NEW:
r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\models\grenadier_scaler_4f.json"
```

**Após cada edit:**
```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m py_compile "<file>"
if ($LASTEXITCODE -ne 0) { throw "py_compile failed" }
```

## Passo I.3 — Aplicar F2 (news_gate — 2 linhas em event_processor)

### I.3.1 `event_processor.py:95`

```python
# OLD:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_News")))
# NEW:
_sys.path.insert(0, str(Path(r"C:\FluxQuantumAPEX\APEX GOLD")))
```

### I.3.2 `event_processor.py:96`

```python
# OLD:
from apex_news_gate import news_gate as _news_gate
# NEW:
from APEX_News.apex_news_gate import news_gate as _news_gate
```

**py_compile:**
```powershell
& $py -m py_compile "C:\FluxQuantumAI\live\event_processor.py"
```

## Passo I.4 — Filtro US-only em country_relevance_gold.json

**Ler o ficheiro actual e filtrar para manter APENAS US.**

```powershell
$json_path = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\config\country_relevance_gold.json"
$data = Get-Content $json_path -Raw | ConvertFrom-Json

# Mostrar estrutura actual antes de editar
Write-Host "=== BEFORE ==="
$data | ConvertTo-Json -Depth 5

# FILTRO: manter apenas US
# A estrutura exacta depende do JSON — adaptar conforme necessário
# Se for {"countries": {"US": {...}, "EU": {...}, ...}} → remover não-US
# Se for array → filtrar elementos US
# Reportar a estrutura real antes de editar

# Após filtragem:
# $data | ConvertTo-Json -Depth 5 | Set-Content $json_path

Write-Host "=== AFTER ==="
# Mostrar resultado pós-filtro
```

**NOTA:** adapta ao formato real do JSON. Se `country_relevance_gold.json` tiver estrutura complexa, mostrar conteúdo primeiro e adaptar filtro. **Preservar estrutura de pesos/config do US** — só remover outros países.

## Passo I.5 — Import probe isolado

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$probe = @'
import sys, traceback
sys.path.insert(0, r"C:\FluxQuantumAI")
results = {}

# 1. StatGuardrail
try:
    from grenadier_guardrail import get_guardrail_status, update_guardrail
    results["StatGuardrail"] = "OK"
except Exception as e:
    results["StatGuardrail"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

# 2. DefenseMode
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly")
    from inference.anomaly_scorer import GrenadierDefenseMode
    dm = GrenadierDefenseMode()
    results["DefenseMode"] = "OK (instantiable)"
except Exception as e:
    results["DefenseMode"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

# 3. V4 IcebergInference
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD\APEX_Iceberg")
    from ats_iceberg_v1 import IcebergInference
    results["V4_Iceberg"] = "OK"
except Exception as e:
    results["V4_Iceberg"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

# 4. ApexNewsGate
try:
    sys.path.insert(0, r"C:\FluxQuantumAPEX\APEX GOLD")
    from APEX_News.apex_news_gate import news_gate
    results["ApexNewsGate"] = "OK"
except Exception as e:
    results["ApexNewsGate"] = f"FAIL: {type(e).__name__}: {e}"
    traceback.print_exc()

print()
print("=== SUMMARY ===")
for k, v in results.items():
    print(f"  {k}: {v}")

failures = sum(1 for v in results.values() if "FAIL" in v)
sys.exit(failures)
'@

$probe | Out-File "$env:TEMP\probe.py" -Encoding UTF8
& $py "$env:TEMP\probe.py"
if ($LASTEXITCODE -ne 0) { throw "IMPORT PROBE FAILED — DO NOT RESTART" }
```

## Passo I.6 — Stop/Start service + observar markers

```powershell
$stdout = "C:\FluxQuantumAI\logs\service_stdout.log"
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"
$init_stdout = if (Test-Path $stdout) { (Get-Item $stdout).Length } else { 0 }
$init_stderr = if (Test-Path $stderr) { (Get-Item $stderr).Length } else { 0 }

Stop-Service FluxQuantumAPEX -Force
Start-Sleep 3

# Verificar capture processes intactos
foreach ($pid in 12332, 8248, 2512) {
    try { Get-Process -Id $pid -ErrorAction Stop | Out-Null; Write-Host "OK PID $pid" }
    catch { Write-Host "WARN PID $pid not found" }
}

Start-Service FluxQuantumAPEX
Start-Sleep 60

# Read new log content
function Read-New($path, $init) {
    if (-not (Test-Path $path)) { return "" }
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    $fs.Seek($init, 'Begin') | Out-Null
    $sr = New-Object IO.StreamReader($fs)
    $c = $sr.ReadToEnd(); $sr.Close(); $fs.Close(); return $c
}

$new_stdout = Read-New $stdout $init_stdout
$new_stderr = Read-New $stderr $init_stderr
$all = $new_stdout + "`n" + $new_stderr

# Check markers per feature
$markers = @{
    "StatGuardrail" = @("StatGuardrail", "guardrail loaded", "grenadier_guardrail")
    "DefenseMode" = @("GrenadierDefenseMode", "DEFENSE_MODE", "DefenseMode initialized")
    "V4_Iceberg" = @("IcebergInference", "V4 Iceberg", "ats_iceberg_v1")
    "ApexNewsGate" = @("ApexNewsGate loaded", "news_gate loaded", "ApexNewsGate")
}
$failure_markers = @("not available", "ImportError", "ModuleNotFoundError", "FAILED to load")

$feature_status = @{}
foreach ($feat in $markers.Keys) {
    $ok = $false; $fail = $false
    foreach ($m in $markers[$feat]) { if ($all -match $m) { $ok = $true } }
    foreach ($fm in $failure_markers) {
        $pattern = "$($markers[$feat][0]).*$fm|$fm.*$($markers[$feat][0])"
        if ($all -match $pattern) { $fail = $true }
    }
    $feature_status[$feat] = if ($fail) { "FAIL" } elseif ($ok) { "OK" } else { "UNKNOWN" }
    Write-Host "  $feat : $($feature_status[$feat])"
}

$failed_count = ($feature_status.Values | Where-Object { $_ -eq "FAIL" }).Count
if ($failed_count -gt 0) { throw "STARTUP VALIDATION FAILED — ROLLBACK" }
```

## Passo I.7 — Observar 10 min

```powershell
Start-Sleep 600  # 10 min

$new_stderr_final = Read-New $stderr $init_stderr
$tracebacks = ($new_stderr_final | Select-String "Traceback" -AllMatches).Matches.Count
$errors = ($new_stderr_final | Select-String "ERROR" -AllMatches).Matches.Count

Write-Host "Tracebacks (10min): $tracebacks"
Write-Host "Errors (10min): $errors"
Write-Host "Service: $((Get-Service FluxQuantumAPEX).Status)"

if ($tracebacks -gt 3 -or (Get-Service FluxQuantumAPEX).Status -ne "Running") {
    throw "RUNTIME FAILURE — ROLLBACK RECOMMENDED"
}
```

## Passo I.8 — Rollback procedure (se necessário)

```powershell
# Trigger only if Passos I.5, I.6 ou I.7 falham
Stop-Service FluxQuantumAPEX -Force -ErrorAction SilentlyContinue

$restore = @{
    "grenadier_guardrail.py" = "C:\FluxQuantumAI\grenadier_guardrail.py"
    "event_processor.py" = "C:\FluxQuantumAI\live\event_processor.py"
    "ats_live_gate.py" = "C:\FluxQuantumAI\ats_live_gate.py"
    "anomaly_scorer.py" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_Anomaly\inference\anomaly_scorer.py"
    "news_config.yaml" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\news_config.yaml"
    "country_relevance_gold.json" = "C:\FluxQuantumAPEX\APEX GOLD\APEX_News\config\country_relevance_gold.json"
}
foreach ($leaf in $restore.Keys) {
    Copy-Item "$backup_dir\$leaf" $restore[$leaf] -Force
}
Start-Service FluxQuantumAPEX
Write-Host "ROLLBACK COMPLETE"
```

## Passo I.9 — Report FASE I

Criar `$sprint_dir\FASE_I_REPORT.md`:
- Backup location + hashes
- F1 fixes applied (4 files)
- F2 fixes applied (2 lines)
- US-only filter: before/after snapshot
- Import probe: 4/4 OK
- Log markers: 4/4 OK
- Runtime 10min: clean
- Capture processes: intact
- Post-fix hashes

**SE FASE I SUCCESS → prosseguir FASE II.**
**SE FASE I FAIL → ROLLBACK + report + STOP.**

---

# FASE II — CALIBRAÇÃO NEWS_GATE (Data-Driven)

**Pre-requisito:** FASE I concluída com success.

**Objectivo:** Propor thresholds calibrados empiricamente para news_gate.yaml usando histórico.

**Modo:** READ-ONLY sobre produção. Apenas análise de dados + proposta.

## Passo II.1 — Processar calendário económico

**Input:** `C:\FluxQuantumAI\Economic_Calendar_History_2025-2026.xlsx`

**Format issues conhecidos:**
- Linhas de header (datas) + linhas de eventos (hora + event)
- Importance só marca "Holiday" — eventos restantes têm NaN
- Precisa forward-fill + timestamp parsing

**Script:**

```python
import pandas as pd
import numpy as np
from pathlib import Path

xlsx = r"C:\FluxQuantumAI\Economic_Calendar_History_2025-2026.xlsx"
df = pd.read_excel(xlsx, sheet_name='Sheet1')

# Forward fill de datas headers
def is_date_header(t):
    try: return pd.to_datetime(t, errors='coerce') is not pd.NaT
    except: return False

# Parse: linhas com "Tuesday, 1 July 2025" = header, linhas com "13:30:00" = event
df['date_header'] = df['Time'].apply(lambda t: pd.to_datetime(t, errors='coerce') if isinstance(t, str) and ',' in t else pd.NaT)
df['current_date'] = df['date_header'].ffill()

# Keep only event rows (Time is HH:MM:SS, not date)
events = df[df['Cur.'] == 'US'].copy()
events = events[events['Time'].astype(str).str.match(r'\d{2}:\d{2}:\d{2}') | (events['Time'] == 'All Day')]

# Build timestamp UTC (ET release time + add 4-5h)
def build_ts(row):
    if row['Time'] == 'All Day': return row['current_date']
    try:
        t = pd.to_datetime(row['Time'], format='%H:%M:%S').time()
        return pd.Timestamp.combine(row['current_date'].date(), t)
    except: return pd.NaT

events['timestamp_et'] = events.apply(build_ts, axis=1)
events['timestamp_utc'] = events['timestamp_et'] + pd.Timedelta(hours=5)  # aprox ET→UTC

# CLASSIFICAR IMPORTANCE por nome de evento (data-driven via research)
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
    e = str(event)
    for kw in HIGH_KEYWORDS:
        if kw.lower() in e.lower(): return 'HIGH'
    for kw in MEDIUM_KEYWORDS:
        if kw.lower() in e.lower(): return 'MEDIUM'
    for kw in LOW_KEYWORDS:
        if kw.lower() in e.lower(): return 'LOW'
    return 'LOW'

events['importance'] = events['Event'].apply(classify)

# Output: parquet estruturado
out = events[['timestamp_utc', 'Event', 'importance', 'Actual', 'Forecast', 'Previous']].copy()
out.columns = ['ts_utc', 'event', 'importance', 'actual', 'forecast', 'previous']
out = out.dropna(subset=['ts_utc']).sort_values('ts_utc').reset_index(drop=True)

out.to_parquet(r"C:\FluxQuantumAI\data\processed\news_calendar_us_2025_2026.parquet")
print(f"Events processed: {len(out)}")
print(f"HIGH: {(out['importance']=='HIGH').sum()}")
print(f"MEDIUM: {(out['importance']=='MEDIUM').sum()}")
print(f"LOW: {(out['importance']=='LOW').sum()}")
```

## Passo II.2 — Enriquecer trades.csv com contexto news

```python
trades = pd.read_csv(r"C:\FluxQuantumAI\logs\trades.csv")
trades['entry_ts'] = pd.to_datetime(trades['entry_time'])  # adaptar nome coluna real

calendar = pd.read_parquet(r"C:\FluxQuantumAI\data\processed\news_calendar_us_2025_2026.parquet")

def news_context(trade_ts, window_min=60):
    """Return events within ±window_min of trade."""
    lo = trade_ts - pd.Timedelta(minutes=window_min)
    hi = trade_ts + pd.Timedelta(minutes=window_min)
    nearby = calendar[(calendar['ts_utc'] >= lo) & (calendar['ts_utc'] <= hi)]
    if len(nearby) == 0:
        return pd.Series({'news_bucket': 'NO_NEWS', 'news_importance': 'NONE', 'min_to_event': None, 'event': None})
    # Closest event
    nearby = nearby.copy()
    nearby['delta_min'] = (nearby['ts_utc'] - trade_ts).dt.total_seconds() / 60
    closest = nearby.iloc[nearby['delta_min'].abs().argmin()]
    
    delta = closest['delta_min']
    if delta < -30: bucket = 'POST_NEWS'
    elif -30 <= delta < -5: bucket = 'POST_NEWS_30'
    elif -5 <= delta < 0: bucket = 'POST_NEWS_5'
    elif 0 <= delta < 5: bucket = 'PRE_NEWS_5'
    elif 5 <= delta < 30: bucket = 'PRE_NEWS_30'
    else: bucket = 'PRE_NEWS_60'
    
    return pd.Series({
        'news_bucket': bucket,
        'news_importance': closest['importance'],
        'min_to_event': delta,
        'event': closest['event']
    })

enriched = trades.join(trades['entry_ts'].apply(news_context))
enriched.to_csv(r"C:\FluxQuantumAI\data\processed\trades_news_enriched.csv", index=False)
```

## Passo II.3 — Análise estatística por categoria

Para cada (importance × bucket), computar:
- Count trades
- Win rate
- Avg PnL
- Total PnL
- Max drawdown

```python
summary = enriched.groupby(['news_importance', 'news_bucket']).agg(
    count=('total_pnl_usd', 'size'),
    total_pnl=('total_pnl_usd', 'sum'),
    avg_pnl=('total_pnl_usd', 'mean'),
    win_rate=('total_pnl_usd', lambda x: (x > 0).mean()),
    max_loss=('total_pnl_usd', 'min'),
    max_win=('total_pnl_usd', 'max')
).round(2)

print(summary)
summary.to_csv(r"...\news_impact_summary.csv")
```

**Hipótese a testar:** trades em `PRE_NEWS_30` + importance=HIGH têm PnL significativamente pior que `NO_NEWS` baseline?

## Passo II.4 — Proposta thresholds data-driven

Baseado em Passo II.3:

- **Window pre-event:** qual bucket mostra degradação estatisticamente significativa? (30min? 60min? apenas 5min?)
- **Window post-event:** idem
- **Events que valem filtrar:** HIGH obrigatório; MEDIUM depende dos dados; LOW provavelmente skip
- **Score thresholds:** mapear para estrutura actual do yaml (normal=0.3 / caution=0.5 / reduced=0.7 / blocked=0.9 / exit_all=1.0)

**Output:** `FASE_II_PROPOSAL.md` com:
- Estatísticas observadas
- Thresholds propostos com justificação
- Comparação vs defaults actuais
- Counterfactual: se aplicarmos isto ao histórico, quantos losses teriam sido evitados?

**NÃO aplicar ao yaml automaticamente.** Proposta fica para Barbara + Claude reverem antes de aplicar.

## Passo II.5 — Report FASE II

Criar `$sprint_dir\FASE_II_REPORT.md` com findings + proposta.

---

# FASE III — DATASET HIGHRISK + BEHAVIOR FINGERPRINT

**Pre-requisito:** FASE II concluída (calendário processado).

**Objectivo:**
1. **Dataset estruturado** minuto-a-minuto à volta de eventos HIGH risk (treino ML futuro)
2. **Behavior fingerprint** por tipo de evento (NFP, CPI, FOMC, FOMC Press, Powell Speaks)

**Modo:** READ-ONLY.

## Passo III.1 — Build dataset

Para cada evento HIGH risk em `news_calendar_us_2025_2026.parquet`, extrair janela [T-60min, T+60min] de `calibration_dataset_full.parquet`.

```python
calendar = pd.read_parquet(r"...news_calendar_us_2025_2026.parquet")
high_events = calendar[calendar['importance'] == 'HIGH'].copy()

data = pd.read_parquet(r"C:\data\processed\calibration_dataset_full.parquet")
data['ts'] = pd.to_datetime(data['timestamp'])

dataset_rows = []

for _, event in high_events.iterrows():
    t0 = event['ts_utc']
    lo = t0 - pd.Timedelta(minutes=60)
    hi = t0 + pd.Timedelta(minutes=60)
    
    window = data[(data['ts'] >= lo) & (data['ts'] <= hi)].copy()
    if len(window) < 30: continue  # skip se pouco dado
    
    window['event_id'] = event['ts_utc'].strftime('%Y%m%d_%H%M') + '_' + event['event'][:20]
    window['event_name'] = event['event']
    window['minutes_to_event'] = (window['ts'] - t0).dt.total_seconds() / 60
    
    # Forward returns para treinamento ML
    for h in [1, 5, 15, 30, 60]:
        window[f'forward_return_{h}min'] = window['close'].shift(-h) - window['close']
    
    dataset_rows.append(window)

highrisk_dataset = pd.concat(dataset_rows, ignore_index=True)
highrisk_dataset.to_parquet(r"C:\FluxQuantumAI\data\nextgen\highrisk\highrisk_dataset_v1.parquet")
print(f"Dataset: {len(highrisk_dataset)} rows, {highrisk_dataset['event_id'].nunique()} events")
```

**Colunas no dataset:**
- `ts`, `event_id`, `event_name`, `minutes_to_event`
- Price: `open, high, low, close, volume, atr_m1, atr_m30`
- L2: `dom_imbalance, absorption_ratio, spread_ticks, depth_bid, depth_ask, book_pressure`
- Anomaly: `z_score, anomaly_severity, latency_ms` (se disponível no parquet)
- Iceberg: `iceberg_detected, iceberg_side, iceberg_confidence, iceberg_volume_est` (se disponível)
- Estrutural: `m30_bias, d4h_delta, h4_bias, box_confirmed, liq_top, liq_bot`
- Forward returns: 1min, 5min, 15min, 30min, 60min

**NOTA:** nem todas as colunas podem estar em `calibration_dataset_full.parquet`. Verificar quais existem e incluir só essas. Documentar gaps.

## Passo III.2 — Behavior fingerprint por tipo de evento

Para cada tipo de evento (NFP, CPI, FOMC Meeting Minutes, FOMC Press Conference, Powell Speaks):

**Análises a computar por bucket temporal (T-60→T-30, T-30→T-5, T-5→T0, T0→T+5, T+5→T+30, T+30→T+60):**

1. **Price behavior:**
   - Avg absolute return
   - Realized volatility (std of 1min returns)
   - Range (max-min)
   - Direction distribution (% up vs down)

2. **L2 behavior:**
   - Avg spread_ticks
   - Avg depth_total
   - Avg dom_imbalance (+ std)
   - Avg absorption_ratio

3. **Anomaly behavior:**
   - % bars with z_score > threshold
   - Avg anomaly_severity
   - Timeline: quando dispara antes/depois do evento?

4. **Iceberg behavior:**
   - % bars com iceberg detected
   - Distribuição iceberg_side (BID vs ASK)
   - Avg iceberg_confidence
   - Timing: icebergs pre-release são mais frequentes?

**Output por evento:** `fingerprint_<event_name>.md` com tabelas + insights.

## Passo III.3 — Relatório agregado

`$sprint_dir\FASE_III_REPORT.md`:

1. Dataset summary (eventos, rows, colunas disponíveis)
2. Behavior fingerprints consolidados (tabela comparativa NFP vs CPI vs FOMC vs Powell)
3. **Indicadores com edge preditivo identificado:**
   - Quais L2 indicators mostram mudança estatística pre-release?
   - Icebergs aparecem antes? Lead time médio?
   - Anomaly triggers são antecipados ou reativos?
4. **Indicadores com edge reativo:**
   - Quais reagem mais fortemente post-release?
5. **Recomendações para módulo HighRisk:**
   - Features a usar para treino ML
   - Labeling strategy (forward returns thresholds)
   - Arquitectura sugerida (baseado em research: DeepOFI style + GARCH-LSTM)
   - Próximos passos

## Passo III.4 — Upload dataset para S3 (opcional)

Se tiver AWS CLI configurado + Barbara confirmar:
```powershell
aws s3 cp "C:\FluxQuantumAI\data\nextgen\highrisk\highrisk_dataset_v1.parquet" "s3://fluxquantumai-data/nextgen/highrisk/highrisk_dataset_v1.parquet"
```

Senão, deixar local em `C:\FluxQuantumAI\data\nextgen\highrisk\`.

---

# COMUNICAÇÃO FINAL

**Success path:**
```
SPRINT COMPLETE
FASE I: ✅ 4 features restored
FASE II: ✅ Calibration proposal generated ($sprint_dir\FASE_II_REPORT.md)
FASE III: ✅ Dataset + fingerprints ($sprint_dir\FASE_III_REPORT.md)
Total duration: X hours
System operational for Sunday market open

Aguardando Barbara + Claude review dos reports FASE II e III.
```

**Partial path:**
```
SPRINT PARTIAL
FASE I: [status]
FASE II: [status]
FASE III: [status]
Blocker: [descrição]
Next steps: [...]
```

**Rollback path:**
```
SPRINT ROLLED BACK
Failed at: FASE I, Passo [N]
Reason: [detalhe]
System restored to pre-fix state
URGENT: Barbara + Claude analyze before retry
```

---

## PROIBIDO

- ❌ Modificar yaml thresholds em FASE II (é proposta, não aplicação)
- ❌ Tocar capture processes em qualquer momento
- ❌ Pular backup ou import probe
- ❌ Declarar success se qualquer feature tem status "FAIL"
- ❌ Rollback parcial (rollback é always all-or-nothing dos 4 files + 2 configs)
- ❌ Pedir mais approvals — decisões já tomadas

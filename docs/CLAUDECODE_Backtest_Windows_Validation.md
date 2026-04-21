# TASK: BACKTEST VALIDAÇÃO WINDOWS 5/3 — Calendar Full Period

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Pre-requisito:** FASE I + FASE II + Apply Windows completos. Calendar extendido até Abril 2026 (pasta compartilhada).
**Mode:** 100% READ-ONLY sobre produção. Apenas análise de dados históricos.
**Output:** `C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908\BACKTEST_WINDOWS_REPORT.md`

---

## OBJECTIVO

Validar **empiricamente** os valores `pause_before=5` / `pause_after=3` aplicados ao EVENT_CONFIG usando:
1. Calendar extendido Barbara disponibilizou (Jul 2025 → Apr 2026, 10 meses completos)
2. Histórico L2 microstructure (`calibration_dataset_full.parquet`, Jul 2025 → Apr 2026)

**Questão central:** se tivéssemos aplicado windows 5/3 ao longo dos últimos 10 meses, o gate teria bloqueado correctamente o pico de volatility (0-1min pós-release) e deixado passar os períodos de compression (5-30min pós-release)?

**NÃO é backtest PnL de trades.** É validação **statistical** das windows contra volatility real.

---

## REGRA CRÍTICA — ZERO ASSUMPTIONS

**Sempre discovery primeiro.** Não assumir:
- Localização do calendar extendido
- Estrutura de colunas
- Timezone
- Date range real

Se algo não estiver claro, **REPORTAR e PARAR**, não adivinhar.

---

## PASSO 1 — Discovery calendar extendido

**Pedir a Barbara localização exacta do calendar extendido.** Path provável:
- `C:\FluxQuantumAI\Economic Calendar History_2025-2026.xlsx` (mesmo path, updated)
- OU novo ficheiro `Economic Calendar History_extended.xlsx`
- OU outro local

```powershell
# Tentar localizar ficheiros recentes
Get-ChildItem "C:\FluxQuantumAI" -Filter "*conomic*alendar*" -Recurse |
    Select-Object FullName, Length, LastWriteTime | Sort-Object LastWriteTime -Descending

Get-ChildItem "C:\FluxQuantumAI" -Filter "*.xlsx" -Recurse |
    Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-2) } |
    Select-Object FullName, Length, LastWriteTime
```

**Se encontrar múltiplos ficheiros candidatos:** REPORTAR e pedir confirmação de Barbara qual usar.

**Ler estrutura:**
```python
import pandas as pd
df = pd.read_excel(path)
print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)}")
print(f"Date range via first/last parseable rows: [extrair]")
print(f"US events count: [filtrar Cur.=='US']")
print(f"Last date: [most recent row]")
```

**Validar que data range cobre:** Jul 2025 → Apr 2026.

Se range ainda é só até Jan 2026 → **PARAR e reportar a Barbara.**

---

## PASSO 2 — Re-processar calendar com extended data

Usar mesmo pipeline da FASE II (forward-fill dates + classify importance por keywords):

```python
# Mesmas HIGH_KEYWORDS / MEDIUM_KEYWORDS / LOW_KEYWORDS da FASE II
# Output: news_calendar_us_full.parquet (substituir anterior)
```

**Output esperado:**
- ~300-400 events US total (estimativa: 200 até Jan + ~100-150 Jan-Apr)
- HIGH count, MEDIUM count, LOW count — reportar
- Date range real do output

**Salvar em:** `C:\FluxQuantumAI\data\processed\news_calendar_us_full.parquet`

---

## PASSO 3 — Validation metodologia

Para cada event HIGH + MEDIUM no calendar full, extrair janela de preço em `calibration_dataset_full.parquet`:

**Windows a analisar:**

| Bucket | Time range | Purpose |
|---|---|---|
| PRE_30 | T-30min to T-5min | Período que o gate antigo bloqueava mas FASE II disse estar "marginal" (1.09×) |
| PRE_5 | T-5min to T-0min | Período que o novo pause_before=5 cobre |
| DURING | T-0 to T+1min | Pico real (4.49×) |
| EARLY_POST | T+1min to T+3min | Coberto pelo novo pause_after=3 |
| POST_5_30 | T+3min to T+30min | Período que gate antigo bloqueava mas FASE II disse estar em compression |
| POST_30_60 | T+30min to T+60min | Full compression period |

**Para cada bucket:**
- Count de minutos
- Mean |return| em bps (M1 close-to-close)
- Percentile 90, 99
- Cohen's d vs baseline (PRE_60 = T-60 to T-30, "quiet" period)

**Output:** `validation_windows_summary.csv` com buckets × importance × stats.

---

## PASSO 4 — Validar cada decisão data-driven

### 4.1 Validação `pause_before=5min`

**Hipótese:** PRE_5 tem volatility significativamente elevada (≥1.3× baseline) para HIGH events, justificando bloqueio.

**Teste:** t-test + Cohen's d para HIGH PRE_5 vs baseline.

**Resultado esperado (reproduzir FASE II):** ratio ≥ 1.3×, d ≥ 0.3, p < 0.001. Se não reproduzir com dados estendidos → **FLAG** como regime change potencial.

### 4.2 Validação `pause_after=3min`

**Hipótese:** EARLY_POST (T+1 to T+3min) tem volatility still elevated (acima de baseline), justificando protecção residual.

**Teste:** stats de EARLY_POST vs baseline.

**Resultado esperado:** ratio entre 1.0× e 2.0× (ainda elevado mas < DURING). Se < 1.0× (compression já começou no minuto 1) → pause_after=3 é conservador excessivo (mas aceitável dado pedido Barbara).

Se > 2.0× (spike continua além de 3min) → **FLAG** que pause_after=3 pode ser insuficiente.

### 4.3 Validação do "trade-off window" (PRE_30 e POST_5_30)

**Hipótese crítica da FASE II:** estas janelas NÃO têm volatility anómala — portanto bloquear era excessivo.

**Teste:** ratios próximos de 1.0× (não-significativo vs baseline).

**Resultado esperado:** PRE_30 ~1.09× (marginal), POST_5_30 ~0.6× (compression).

Se PRE_30 > 1.2× ou POST_5_30 > 1.0× → reverter decisão, pause_before/after actuais eram correctos.

### 4.4 Comparação vs FASE II original

Para cada bucket × importance, comparar findings com FASE II report (200 events). Verificar:
- Findings replicam? (sign e magnitude consistentes)
- Findings mais fortes/fracos com mais eventos?
- Algum flip de sinal? (alguma janela que era compression agora é spike?)

**Output:** tabela comparativa FASE II vs BACKTEST_WINDOWS.

---

## PASSO 5 — Per-event-type drill down (NOVO)

FASE II só agregava por importance (HIGH/MEDIUM/LOW). Agora com mais dados, drill down **por event type específico**:

Para cada event type (FOMC, NFP, CPI, GDP, PPI, FED_SPEECH, UNEMPLOYMENT, ISM, RETAIL_SALES):
- Count de eventos
- DURING ratio (max vol pico)
- PRE_5 ratio
- EARLY_POST ratio
- POST_5_30 ratio

**Objectivo:** detectar se algum event type comporta-se significativamente diferente (ex: FOMC tem spike mais longo? NFP comprime mais rápido?).

**Se diferenças forem grandes (ratios divergem >50% entre event types):** proposta futura de windows **diferenciadas por event type**, não globais 5/3.

---

## PASSO 6 — Visualizations (opcional mas útil)

Se tempo permitir, gerar plots M1:
- Heatmap importance × bucket (mean |return|)
- Time series plot: mean |return| por minuto T-60 to T+60 para HIGH vs MEDIUM vs LOW
- Boxplot DURING distribution per event type

**Output:** PNGs em sprint dir. Não bloquear backtest se falhar.

---

## PASSO 7 — Conclusão + recomendação

Baseado em findings 4.1-4.4:

**Decisão:**
- **GREEN LIGHT:** Findings replicam FASE II com calendar extendido → windows 5/3 **validadas empiricamente**.
- **YELLOW:** Algumas inconsistências mas direção geral confirmada → aceitar mas monitorar.
- **RED:** Findings contradizem FASE II → **recommend rollback** para values anteriores ou windows intermediárias.

**Se GREEN/YELLOW:** documentar findings para futura referência.
**Se RED:** propor rollback imediato antes de mercado abrir Monday.

---

## PASSO 8 — TASK_CLOSEOUT Report

Criar `$sprint_dir\BACKTEST_WINDOWS_REPORT.md`:

```markdown
# BACKTEST VALIDATION — Windows 5/3 Data-Driven — TASK_CLOSEOUT

**Timestamp:** <UTC>
**Duration:** X min
**Status:** ✅ GREEN / ⚠ YELLOW / ❌ RED

## 1. Calendar discovery
- Path encontrado: ...
- Date range: ...
- US events count: ...
- Classification: HIGH=X, MEDIUM=Y, LOW=Z

## 2. Methodology
- Calibration dataset: ...
- Windows analysed: PRE_30, PRE_5, DURING, EARLY_POST, POST_5_30, POST_30_60
- Baseline: PRE_60

## 3. Validation results

### 3.1 pause_before=5min validation
- HIGH PRE_5 ratio: X.XX× (expected ≥1.3×)
- Cohen's d: X.XX (expected ≥0.3)
- p-value: X
- **Decision:** VALIDATED / FLAGGED

### 3.2 pause_after=3min validation
- HIGH EARLY_POST (T+1 to T+3min) ratio: X.XX×
- **Decision:** CONSERVATIVE-OK / INSUFFICIENT / EXCESSIVE

### 3.3 Trade-off validation (PRE_30 and POST_5_30)
- HIGH PRE_30 ratio: X.XX× (expected ≈1.09×)
- HIGH POST_5_30 ratio: X.XX× (expected ≈0.6×)
- **Decision:** WINDOWS WELL-CALIBRATED / REVISIT

### 3.4 FASE II comparison
[Tabela side-by-side]

## 4. Per-event-type drill down
[Tabela FOMC/NFP/CPI/... com DURING/PRE_5/EARLY_POST/POST_5_30 ratios]

**Notable findings:**
- [event type X behaves differently because...]

## 5. Overall decision
**GREEN / YELLOW / RED**

**Justification:**
[summary]

## 6. Recommendations
- [Se GREEN] Windows 5/3 validadas. Manter.
- [Se YELLOW] Windows OK mas observar [specific concerns].
- [Se RED] Recommend rollback porque [reason]. Proposed alternative: [X/Y].

## 7. Per-event-type differentiation (future consideration)
Se findings Passo 5 mostram divergência:
- FOMC might need: [specific values]
- NFP might need: [specific values]
- etc.
Não aplicar agora — requer sprint dedicado.

## 8. Limitations
- [list]

## 9. Files generated
- news_calendar_us_full.parquet
- validation_windows_summary.csv
- per_event_type_breakdown.csv
- [plots se gerados]

## 10. Files NOT modified
- Zero code changes
- Zero yaml changes
- Production system intact
- Capture processes (PIDs 12332, 8248, 2512): intact
```

---

## COMUNICAÇÃO FINAL

**GREEN:**
```
BACKTEST VALIDATION COMPLETE — GREEN LIGHT
Calendar events: N (Jul 2025 → Apr 2026)
HIGH PRE_5: X.XX× ✅ (validates pause_before=5)
HIGH EARLY_POST: X.XX× ✅ (validates pause_after=3)
HIGH PRE_30 / POST_5_30: ~baseline ✅ (old windows were excessive)
FASE II findings REPLICATED with extended data.
Report: BACKTEST_WINDOWS_REPORT.md
```

**YELLOW:**
```
BACKTEST VALIDATION COMPLETE — YELLOW
Direction confirmed but: [specific concerns]
Recommend: monitor empirically Monday+
Report: ...
```

**RED:**
```
BACKTEST VALIDATION FAILED — RED FLAG
Findings contradict FASE II: [specific]
RECOMMEND ROLLBACK before Monday market open.
Alternative windows proposed: [X/Y]
Report: ...
```

---

## PROIBIDO

- ❌ Qualquer edit a código, yaml, configs
- ❌ Restart serviço
- ❌ Tocar capture processes
- ❌ Assumir path do calendar extendido — sempre discovery primeiro
- ❌ Declarar GREEN sem validação explícita dos 3 critérios (pause_before, pause_after, trade-off window)
- ❌ Skipar per-event-type drill down (Passo 5 — novo vs FASE II)
- ❌ Skipar TASK_CLOSEOUT report

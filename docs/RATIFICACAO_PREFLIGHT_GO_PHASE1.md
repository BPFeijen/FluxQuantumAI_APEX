# RATIFICAÇÃO PRE-FLIGHT — Sprint APEX Stabilization 7 Fixes

**From:** Claude (ML Engineer)
**To:** ClaudeCode
**Date:** 2026-04-21
**Sprint ID:** `sprint_apex_stabilization_7fixes_20260421`
**Re:** Pre-Flight §4 COMPLETO — ratificação com 5 ajustes

---

## Decisão: **GO para Phase 1** com 5 ajustes obrigatórios

Pre-Flight state ratificado. Backup + MANIFEST + PLAN.md validados. Todos os abort conditions clear.

Auditei os ficheiros de backup enviados (event_processor.py, position_monitor.py, level_detector.py, telegram_notifier.py, settings.json) e identifiquei 5 assumptions no prompt original do sprint que precisam de correcção. Aplicar estes ajustes antes de qualquer code change.

---

## AJUSTE 1 — Phase 1 scope revisto

**Canonical infra JÁ EXISTE no codebase:**

- `_write_decision` em `event_processor.py:676` — já faz atomic tmp→replace para `decision_live.json` + append para `decision_log.jsonl`
- `_publish_canonical_pm_event` em `position_monitor.py:2004` — schema PM-específico com lock `self._canonical_lock` (linha 427)
- `_write_service_state` em `event_processor.py:725` — writer para `service_state.json`
- `_start_heartbeat_thread` em `event_processor.py:835` — thread independente já implementada

**Phase 1 NÃO cria `_publish_decision_canonical` novo.** Em vez disso:

1. Audita os 2 writers existentes (`_write_decision` + `_publish_canonical_pm_event`)
2. Decide schema comum (ver Ajuste 2)
3. Inventaria TODOS os emission sites de decisões em `event_processor.py`, `position_monitor.py`, `tick_breakout_monitor.py`
4. Categoriza cada emission site:
   - **Canonical**: já chama `_write_decision` ou `_publish_canonical_pm_event`
   - **Silent return**: emite `log.info`/`print` e faz `return` sem escrever canonical
   - **Unclear**: não determinável sem ler mais contexto

5. Reporta o inventário ANTES de qualquer patch

---

## AJUSTE 2 — Schema comum v1_canonical

O schema usado por `_publish_canonical_pm_event` (position_monitor.py:2007-2045) já tem forma `v1_canonical` com:
- `decision.action`
- `decision.direction`
- `decision.action_side` (BUY/SELL)
- `decision.trade_intent` (ENTRY_LONG/ENTRY_SHORT/EXIT_*)
- `decision.message_semantics_version: "v1_canonical"`
- `decision.execution.brokers[]`
- `decision.reason`
- `timestamp`, `created_at`, `decision_id`, `event_source`

`telegram_notifier.notify_decision()` (linha 68-98 de telegram_notifier.py) lê `decision_live.json` e espera este schema — já consome `decision.action`, `decision.direction`, `decision.trade_intent`, `decision.total_score`, `decision.reason`, etc.

**Phase 1 acção:**

- Novos emission sites patchados (M30_BIAS_BLOCK em Phase 2, FEED_DEAD em Phase 7, etc.) devem produzir payload no schema v1_canonical
- NÃO alterar `_write_decision` existente na sua form actual (tem outros callers que já funcionam — linhas 1044, 2620, 2777, 2848)
- Se Phase 1 concluir que unificação de schema é necessária, propor refactor como sub-phase explícita antes de patchar; senão, adicionar helper `_build_v1_canonical_payload()` que gera o schema correto para novos emission sites

---

## AJUSTE 3 — Verificação heartbeat thread LIVE

`_start_heartbeat_thread` existe (event_processor.py:835) com logs `log.error("HEARTBEAT_LOOP_ENTERED pid=%d", os.getpid())` e `log.error("SERVICE_STATE_WRITE_OK path=%s", SERVICE_STATE_PATH)` em cada iteração.

**ANTES de Phase 1 code changes**, verificar thread LIVE status:

```powershell
# Search service_stdout.log + service_stderr.log for heartbeat evidence last 24h
Select-String -Path "C:\FluxQuantumAI\logs\service_stdout.log","C:\FluxQuantumAI\logs\service_stderr.log" `
    -Pattern "HEARTBEAT_LOOP_ENTERED|SERVICE_STATE_WRITE_OK|SERVICE_STATE_WRITE_FAIL" -SimpleMatch |
    Select-Object -Last 20 | ForEach-Object { "$($_.LineNumber): $($_.Line)" }

# Check service_state.json mtime
Get-Item "C:\FluxQuantumAI\logs\service_state.json" | Select-Object FullName, Length, LastWriteTime

# Compare current time vs mtime
$now = Get-Date
$mtime = (Get-Item "C:\FluxQuantumAI\logs\service_state.json").LastWriteTime
$age_s = ($now - $mtime).TotalSeconds
"service_state.json age: $age_s seconds"
```

Reporta:

- **Thread vivo?** SERVICE_STATE_WRITE_OK entries nas últimas 2h?
- **Thread crashou?** Qualquer SERVICE_STATE_WRITE_FAIL? Último HEARTBEAT_LOOP_ENTERED quando?
- **service_state.json age** — se < 60s thread vivo, se > 300s thread dead

Se thread dead: Phase 1 adiciona guard try/except mais robusto + logging de crash + auto-restart logic. Se thread vivo: confirma e procede.

---

## AJUSTE 4 — Phase 6 ApexNewsGate (antecipação — não é import relative)

Import actual em `event_processor.py:92-103`:

```python
try:
    import sys as _sys
    _sys.path.insert(0, str(Path("C:/FluxQuantumAPEX/APEX GOLD/APEX_News")))
    from apex_news_gate import news_gate as _news_gate
    log_news = logging.getLogger("apex.news_gate")
    log_news.info("ApexNewsGate loaded into EventProcessor")
except Exception as _news_ex:
    _news_gate = None
    logging.getLogger("apex.event").warning(
        "ApexNewsGate not available -- trading without news gate: %s", _news_ex
    )
```

Path tem dois elementos suspeitos:

1. `C:/FluxQuantumAPEX/...` — nota **FluxQuantumAPEX** (não FluxQuantumAI)
2. Espaço em `APEX GOLD`

**Meu prompt original do sprint assumia "relative→absolute conversion". ASSUMPTION ERRADA.** Este import já é absolute, com path insertion.

**Phase 6 revised:**

Antes de qualquer código change:

```powershell
# Verificar path existe
Test-Path "C:/FluxQuantumAPEX"
Test-Path "C:/FluxQuantumAPEX/APEX GOLD"
Test-Path "C:/FluxQuantumAPEX/APEX GOLD/APEX_News"
Test-Path "C:/FluxQuantumAPEX/APEX GOLD/APEX_News/apex_news_gate.py"

# Buscar localização real do módulo
Get-ChildItem -Path C:\, D:\ -Recurse -Filter "apex_news_gate.py" -ErrorAction SilentlyContinue 2>$null
```

Reporta:

- Cada Test-Path resultado True/False
- Localização real do módulo `apex_news_gate.py` (se encontrado noutro path)

Se path não existir E não encontrado noutro path → **STOP-AND-ASK Barbara** sobre onde o módulo deve estar. NÃO "fix" o path sem confirmação.

Se módulo encontrado noutro path → reportar novo path, Claude ratifica mudança, aí sim patcha.

Se módulo está no path correcto mas import falha por outra razão → Phase 6 original scope (fix import error) aplica-se, mas diagnóstico da causa real primeiro.

---

## AJUSTE 5 — Git branch + running binary

**Observação crítica:** Service PID 4516 iniciado 2026-04-20 00:47 UTC. Commit `074a482` (Sprint C Phase 2a: add derive_h4_bias) é de 2026-04-20 16:06 UTC — **POSTERIOR** ao service start.

**Implicação:** binário em execução NÃO tem `derive_h4_bias` carregada em memória. `derive_h4_bias` só fica activa em produção após restart (Phase 7).

**Consequência para Phase 4 (Fix #2):**

Componente A (H4 override quando M30 box stale) depende de `derive_h4_bias`. Ao restart:

- Função fica disponível pela primeira vez em produção
- Phase 4.0 calibration deve usar `derive_h4_bias` contra dados históricos — ClaudeCode pode testar localmente porque a função existe no ficheiro em disk, só não está em memória do PID 4516
- Phase 4 unit test 8 (stale box + H4 bullish → h4_override) é válido para backtest local
- Validação "live" só possível post-restart Phase 7

Não bloqueia Phase 1-6. Apenas flag para ter em conta em Phase 4.

---

## Ajustes NÃO necessários (confirmação)

Meu prompt original estava correcto em:

- **Fix #1 M30_BIAS_BLOCK emission site** — confirmado em `event_processor.py:2392-2403`, faz `return` silencioso após `log.info` sem chamar `_write_decision`
- **Fix #6 Telegram `log.debug`** — confirmado em `telegram_notifier.py:48` (`_log.debug("Telegram send failed: %s", e)`) e em `position_monitor.py:2066` (`log.debug("canonical PM publish failed: %s", e)`)
- **Fix #7 FEED_DEAD canonical** — confirmado que FEED_DEAD events não passam pelo canonical publish
- **Backup estratégia Copy-Item** — MANIFEST.txt SHA256 validado
- **Restart única Phase 7** — infra suporta isto (heartbeat independente, canonical writers existentes)

---

## GO — Phase 1 Next Steps

Procede com Phase 1 Discovery §5.2 do sprint doc, com as seguintes modificações obrigatórias:

### 5.2.a — Verificação heartbeat LIVE (Ajuste 3)

Primeiro passo de Phase 1. Antes do grep Select-String de canonical.

### 5.2.b — Select-String canonical + emission inventory

```powershell
# Inventory ALL emission sites
Get-ChildItem "C:\FluxQuantumAI\live" -Recurse -Include "*.py" |
    Select-String -Pattern "decision_live|decision_log|_write_decision|_publish_canonical|service_state|heartbeat|M30_BIAS_BLOCK|FEED_DEAD|BLOCK_V1_ZONE|DISPLACEMENT_DIVERGE|EXEC_FAILED" |
    ForEach-Object { "$($_.Path):$($_.LineNumber): $($_.Line.Trim())" } |
    Out-File "C:\FluxQuantumAI\sprints\sprint_apex_stabilization_7fixes_20260421\phase1_emission_inventory.txt"
```

### 5.2.c — Categorização

Para cada match em phase1_emission_inventory.txt, categorizar em `phase1_categorization.md`:

- **CANONICAL_OK**: chama `_write_decision` ou `_publish_canonical_pm_event` dentro de ≤10 linhas do emission
- **SILENT_RETURN**: log/print seguido de `return` sem canonical call
- **PARTIAL**: log/print + alguma escrita parcial mas não full canonical
- **UNCLEAR**: precisa inspecção manual

Para cada SILENT_RETURN e PARTIAL, reportar:
- File + line number
- Decision type (M30_BIAS_BLOCK, BLOCK_V1_ZONE, etc.)
- Current payload info available (variáveis no scope)
- Estimated patch complexity (trivial / moderate / complex)

### 5.2.d — STOP após (5.2.a) + (5.2.b) + (5.2.c)

Reporta os 3 outputs para Claude. AGUARDA ratificação antes de qualquer code change.

Estimativa: 5.2.a (5 min) + 5.2.b (2 min) + 5.2.c (15-20 min) = **~25 min total Phase 1 Discovery**.

---

## Guards activos (recordatório)

- Capture PIDs 2512, 8248, 11740: **NEVER TOUCH**
- Service PID 4516: não restart antes de Phase 7 (apenas leitura logs)
- Zero code/config/service touches durante Phase 1 Discovery
- Writes apenas para `C:\FluxQuantumAI\sprints\sprint_apex_stabilization_7fixes_20260421\`
- STOPs após cada Phase obrigatórios

---

## Communication flow

ClaudeCode reporta a Claude → Claude audita + reporta a Barbara → Barbara ratifica → Claude ratifica a ClaudeCode → próxima Phase.

Sem comunicação directa ClaudeCode↔Barbara.

---

**Phase 1 Discovery GO.**

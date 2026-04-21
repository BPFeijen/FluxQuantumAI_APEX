# TASK: Apply P0 Patch + Deploy `main` to Live Server + Monitor 24h

**Para:** ClaudeCode
**De:** Barbara (via Claude — ML/AI Engineer)
**Data:** 2026-04-17
**Tempo estimado:** 2h execução + 24h monitoring
**Prioridade:** P0 — sistema em produção com 94% block rate, 72% execution failure, SHORT-onde-LONG reportado

---

## CONTEXTO CRÍTICO

1. O `main` do repo `https://github.com/BPFeijen/FluxQuantumAI_APEX` (commit `ee2068d`) contém o trabalho prévio do Codex: canonical decision/events, PM event publishing, M30 bias handling (confirmed/provisional), stale structure blocking, dashboard endpoints. **Não está deployado no servidor ainda.**

2. Esse `main` ainda tem dois bugs P0 da `CURRENT_LIVE_CODE_AUDIT.md` (Issues #1 e #2). Claude aplicou os fixes localmente e preparou um patch file: `fix_p0_issues_1_and_2.patch` (anexo).

3. A branch `work` no repo é trabalho paralelo de refactor modular — **IGNORAR completamente**. Não fazer qualquer operação envolvendo `work`.

4. O servidor de produção actualmente corre código antigo (pré-`ee2068d`). Esta task faz o deploy completo.

---

## FICHEIROS ANEXADOS

- `fix_p0_issues_1_and_2.patch` — patch file em formato `git format-patch`, aplica em cima do `main` actual

---

## SEQUÊNCIA DE EXECUÇÃO — SEGUE EXATAMENTE

### FASE 0 — Pré-requisitos (verificar antes de começar)

Confirma que tens:
- [ ] Acesso SSH/RDP ao servidor Windows de produção
- [ ] Git e Python 3 instalados no servidor
- [ ] Acesso write para `C:\FluxQuantumAI\`
- [ ] Privilégios para parar/iniciar o serviço NSSM `FluxQuantumAPEX` e `FluxQuantumAPEX_Live`
- [ ] O patch file `fix_p0_issues_1_and_2.patch` copiado para o servidor

Se algum falhar, PARA e reporta.

---

### FASE 1 — Backup completo (obrigatório antes de tocar em nada)

Criar pasta de backup com timestamp:
```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = "C:\FluxQuantumAI - NextGen\Backups\pre-deploy-$stamp"
New-Item -ItemType Directory -Path $backup -Force
```

Copiar para `$backup`:
```powershell
# Código live atual
Copy-Item -Path "C:\FluxQuantumAI\live" -Destination "$backup\live" -Recurse
Copy-Item -Path "C:\FluxQuantumAI\run_live.py" -Destination "$backup\run_live.py"

# Config
Copy-Item -Path "C:\FluxQuantumAI\config\settings.json" -Destination "$backup\settings.json"
Copy-Item -Path "C:\FluxQuantumAI\config\thresholds_gc.json" -Destination "$backup\thresholds_gc.json" -ErrorAction SilentlyContinue

# Runtime state
Copy-Item -Path "C:\FluxQuantumAI\logs\service_state.json" -Destination "$backup\service_state.json" -ErrorAction SilentlyContinue
Copy-Item -Path "C:\FluxQuantumAI\logs\decision_live.json" -Destination "$backup\decision_live.json" -ErrorAction SilentlyContinue

# Últimos 7 dias de logs canónicos
Copy-Item -Path "C:\FluxQuantumAI\logs\decision_log.jsonl" -Destination "$backup\decision_log.jsonl"
Copy-Item -Path "C:\FluxQuantumAI\logs\position_events.jsonl" -Destination "$backup\position_events.jsonl" -ErrorAction SilentlyContinue

# Snapshot parquets (para não perder se nova lógica reprocessar)
Copy-Item -Path "C:\FluxQuantumAI\data\gc_m5_boxes.parquet" -Destination "$backup\gc_m5_boxes.parquet" -ErrorAction SilentlyContinue
Copy-Item -Path "C:\FluxQuantumAI\data\gc_m30_boxes.parquet" -Destination "$backup\gc_m30_boxes.parquet" -ErrorAction SilentlyContinue
```

Criar `$backup\ROLLBACK_PLAN.md` com este conteúdo:
```
# Rollback Plan — Pre-Deploy $stamp

Se deploy falhar e precisarmos voltar ao estado anterior:

## Passo 1 — Parar serviços
nssm stop FluxQuantumAPEX
nssm stop FluxQuantumAPEX_Live

## Passo 2 — Restaurar código
Remove-Item -Recurse -Force "C:\FluxQuantumAI\live"
Copy-Item -Path "$backup\live" -Destination "C:\FluxQuantumAI\live" -Recurse
Copy-Item -Path "$backup\run_live.py" -Destination "C:\FluxQuantumAI\run_live.py" -Force

## Passo 3 — Restaurar config
Copy-Item -Path "$backup\settings.json" -Destination "C:\FluxQuantumAI\config\settings.json" -Force

## Passo 4 — Reiniciar
nssm start FluxQuantumAPEX
nssm start FluxQuantumAPEX_Live

## Passo 5 — Verificar logs
Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Wait -Tail 20
```

Verificar que backup completou:
```powershell
Get-ChildItem "$backup" -Recurse | Measure-Object -Property Length -Sum
```
Reporta: bytes totais + lista de ficheiros. Deve ter pelo menos 20-30 ficheiros de `live/`.

---

### FASE 2 — Clone de `main` atualizado para pasta review

```powershell
$review = "C:\FluxQuantumAI - NextGen\codex-review-$stamp"
cd "C:\FluxQuantumAI - NextGen"
git clone https://github.com/BPFeijen/FluxQuantumAI_APEX.git $review
cd $review
git log --oneline -5
```

Confirma que o último commit é `ee2068d` (ou mais recente se já houver novos commits).

---

### FASE 3 — Aplicar patch dos fixes #1 e #2

```powershell
cd $review
git apply --check "C:\path\to\fix_p0_issues_1_and_2.patch"
```
Se der erro, PARA. Não continues. Se der OK:

```powershell
git apply "C:\path\to\fix_p0_issues_1_and_2.patch"
```

Verificar que aplicou corretamente:
```powershell
git diff --stat
# Esperado: 1 ficheiro alterado, 3 insertions, 3 deletions

Select-String -Path "live\event_processor.py" -Pattern "_check_pre_entry_gates|box_df = df\["
# Esperado (3 matches):
# 1658:    def _check_pre_entry_gates(self, direction: str, delta_4h: float, price: float) -> tuple[bool, str]:
# 2244:        blocked, block_reason = self._check_pre_entry_gates(direction, d4h, price)
# 3133:            box_df = df[(df["m30_box_confirmed"] == True) & (df["m30_box_id"].notna())]
```

---

### FASE 4 — Cross-check antes de substituir

Esta é a prova crítica de que o deploy não perde nada. Executar **todas** estas verificações no `$review\live\`:

```powershell
cd $review

# 4.1 — Canonical observability preservada
$needles = @("m30_bias_confirmed", "provisional_m30_bias", "refresh_macro_context", "STRUCTURE_STALE_BLOCK", "action_side", "trade_intent", "overall_state")
foreach ($n in $needles) {
    $count = (Select-String -Path "live\event_processor.py" -Pattern $n -AllMatches).Matches.Count
    Write-Host "  $n : $count matches"
}
# Esperado: todos > 0

# 4.2 — PM canonical em position_monitor
$pm_needles = @("_publish_canonical_pm_event", "_emit_position_event", "POSITION_MONITOR")
foreach ($n in $pm_needles) {
    $count = (Select-String -Path "live\position_monitor.py" -Pattern $n -AllMatches).Matches.Count
    Write-Host "  $n : $count matches"
}
# Esperado: todos > 0

# 4.3 — Zero padrões de bug remanescentes
Select-String -Path "live\*.py" -Pattern '== True &[^&]' | Format-Table Path, LineNumber, Line
# Esperado: zero matches

# 4.4 — Compile check de todos os módulos live
python -m py_compile run_live.py live\event_processor.py live\level_detector.py live\position_monitor.py live\tick_breakout_monitor.py live\telegram_notifier.py live\base_dashboard_server.py live\m5_updater.py live\m30_updater.py live\operational_rules.py live\feed_health.py live\signal_queue.py live\price_speed.py live\hedge_manager.py live\kill_zones.py
# Esperado: exit 0, zero erros

# 4.5 — Caller consistency (todos chamadores de _check_pre_entry_gates passam 3 args)
Select-String -Path "live\event_processor.py" -Pattern "_check_pre_entry_gates"
# Esperado: 1 signature com `price: float` + 1+ callers com 3 args
```

**Se qualquer uma das verificações falhar, PARA. Reporta à Barbara antes de continuar.**

---

### FASE 5 — Preservar settings.json e artefactos de estado

O `settings.json` no servidor tem calibrações ativas (CAL-1 a CAL-13, CAL-16, CAL-17, lot sizing ASIAN/LONDON/NY, etc.). **Não deves substituir o settings.json do servidor pelo do repo** (o repo pode ter valores diferentes).

Copia o `$review\live\*` apenas, preservando:
- `C:\FluxQuantumAI\config\settings.json` (inalterado do servidor)
- `C:\FluxQuantumAI\config\thresholds_gc.json` (inalterado)
- `C:\FluxQuantumAI\data\*.parquet` (inalterados)
- `C:\FluxQuantumAI\logs\*.jsonl` (inalterados)

---

### FASE 6 — Deploy (substituir ficheiros live no servidor)

```powershell
# Parar serviços primeiro
nssm stop FluxQuantumAPEX
nssm stop FluxQuantumAPEX_Live
Start-Sleep -Seconds 5

# Remover live atual (já temos backup)
Remove-Item -Recurse -Force "C:\FluxQuantumAI\live"

# Copiar novo live
Copy-Item -Path "$review\live" -Destination "C:\FluxQuantumAI\live" -Recurse

# Copiar run_live.py se mudou
Copy-Item -Path "$review\run_live.py" -Destination "C:\FluxQuantumAI\run_live.py" -Force

# Confirmar que settings.json NÃO foi tocado
Get-Item "C:\FluxQuantumAI\config\settings.json" | Select-Object LastWriteTime
# Deve ser data anterior ao deploy

# Iniciar serviços
nssm start FluxQuantumAPEX
nssm start FluxQuantumAPEX_Live
Start-Sleep -Seconds 10

# Verificar que estão running
nssm status FluxQuantumAPEX
nssm status FluxQuantumAPEX_Live
# Esperado: ambos SERVICE_RUNNING
```

---

### FASE 7 — Verificação imediata (primeiros 15 min)

```powershell
# Seguir logs em tempo real
Get-Content "C:\FluxQuantumAI\logs\service_stdout.log" -Wait -Tail 50
```

Procurar por:
- [ ] `[STRATEGY]` logs a aparecerem (sistema a decidir)
- [ ] Ausência de `TRACEBACK` ou `ERROR` repetidos
- [ ] `MACRO_REFRESH` logs (refresh_macro_context a funcionar)
- [ ] `STRUCTURE_STALE_BLOCK` quando feed estiver stale (novo comportamento canónico)
- [ ] `M30_BIAS_BLOCK: bias=X(confirmed)` ou `M30_BIAS_PROVISIONAL_ONLY` (bias confirmed/provisional visível)

Se ver `NameError: name 'price' is not defined`, o patch não foi aplicado corretamente. ROLLBACK IMEDIATO.

---

### FASE 8 — Monitorização 24h

Cria um script PowerShell `monitor_deploy.ps1` que corre a cada 30 minutos e reporta:

```powershell
# monitor_deploy.ps1
$log = "C:\FluxQuantumAI\logs\decision_log.jsonl"
$since = (Get-Date).AddMinutes(-30).ToString("yyyy-MM-ddTHH:mm")

$entries = Get-Content $log | Where-Object { $_ -match "`"timestamp`": `"$($since.Substring(0,13))" } | ForEach-Object { $_ | ConvertFrom-Json }

$go = ($entries | Where-Object { $_.decision.action -eq "GO" }).Count
$block = ($entries | Where-Object { $_.decision.action -eq "BLOCK" }).Count
$exec_failed = ($entries | Where-Object { $_.decision.action -eq "EXEC_FAILED" }).Count
$stale = ($entries | Where-Object { $_.decision.reason -match "STRUCTURE_STALE_BLOCK|GUARDRAIL_STALE" }).Count

$confirmed_bias = ($entries | Where-Object { $_.context.m30_bias_confirmed -eq $true }).Count
$provisional_bias = ($entries | Where-Object { $_.context.m30_bias_confirmed -eq $false }).Count

Write-Host "=== 30min window ending $(Get-Date -Format 'HH:mm') ==="
Write-Host "Total decisions: $($entries.Count)"
Write-Host "  GO: $go"
Write-Host "  BLOCK: $block"
Write-Host "  EXEC_FAILED: $exec_failed  <-- ALERT if > 0"
Write-Host "  Stale blocks: $stale"
Write-Host "  With confirmed bias: $confirmed_bias"
Write-Host "  With provisional bias only: $provisional_bias"
```

**KPIs de sucesso às 24h** (comparados com baseline pré-deploy):
- `EXEC_FAILED` deve **cair a zero ou perto de zero** (era 176/7019 = 2.5% do total)
- `GUARDRAIL_STALE` block rate **não deve aumentar** mas agora é visível canonicalmente
- `m30_bias_confirmed=false + provisional_m30_bias` entries devem aparecer (novo comportamento)
- Zero crashes/tracebacks no serviço

Se `EXEC_FAILED` continuar > 0 significa que o refactor de `_open_on_all_accounts` (ee2068d) não está a funcionar como esperado — **reportar imediatamente à Barbara e ao Claude.**

---

## OUTPUT OBRIGATÓRIO PARA BARBARA

No final, cria um ficheiro `DEPLOY_REPORT_$stamp.md` com:

1. **Fase 1 (Backup):** path + lista de ficheiros backup + bytes totais
2. **Fase 2 (Clone):** commit hash do `main` clonado
3. **Fase 3 (Patch):** output do `git diff --stat` (esperado: 1 file, 3/3)
4. **Fase 4 (Crosscheck):** output de todas as 5 verificações
5. **Fase 6 (Deploy):** timestamp stop/start + status NSSM final
6. **Fase 7 (Primeiras 15min):** 20 linhas mais relevantes dos logs
7. **Fase 8 (Monitor setup):** confirmação que `monitor_deploy.ps1` está ativo
8. **Timestamp do go-live**

---

## REGRAS CRÍTICAS

1. **Se qualquer fase falhar, PARA.** Reporta à Barbara antes de avançar. Não improvises.
2. **Não toques em `settings.json`, `thresholds_gc.json`, `.parquet`, ou `.jsonl` existentes.** Apenas substitui código `live/*.py` e `run_live.py`.
3. **Se houver dúvida sobre algum comando, PARA e pergunta.** Não inventes.
4. **Se ver `EXEC_FAILED` nos primeiros 30 min, NÃO faças rollback sozinho.** Reporta primeiro — pode ser broker temporariamente disconnected, não bug do deploy.
5. **Rollback só se:** crash loop do serviço, tracebacks repetidos, ou `NameError` relacionado com `price`.

---

**Boa sorte. Esta é a primeira vez em meses que o live vai ter um deploy limpo com todos os fixes acumulados. Importa que corra bem.**

Claude & Barbara

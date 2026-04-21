# TASK: APEX Deploy — Fase 2 (Clone + Apply Patch em Staging)

**Para:** ClaudeCode
**Projeto:** APEX live em `C:\FluxQuantumAI\`
**Tempo estimado:** 5-10 minutos
**Modo:** Read-only em relação ao live. Só escreve dentro de `C:\FluxQuantumAI\deploy-staging\`.

---

## CONTEXTO

- Backup completo criado na Fase 1 em `C:\FluxQuantumAI\Backups\pre-deploy-20260417_141337\`.
- Serviços `FluxQuantumAPEX` e `FluxQuantumAPEX_Live` estão Stopped.
- Processos de captura (PIDs 2512, 8076, 8248) estão Running — NÃO TOCAR.
- Patch `fix_p0_issues_1_and_2.patch` já está no servidor em `C:\FluxQuantumAI\Backups\pre-deploy-20260417_141337\fix_p0_issues_1_and_2.patch`, tamanho 2365 bytes, validado (primeira linha = `From 9e7ecdd...`).

Esta task prepara o código novo em pasta staging. **Zero alteração ao live.** Apenas clone + apply patch + validação.

---

## CRITICAL RULES

1. **Não tocar em `C:\FluxQuantumAI\live\`, `C:\FluxQuantumAI\config\`, nem em nenhuma pasta fora de `deploy-staging\`.**
2. **Não parar nenhum serviço nem processo.**
3. **Não tocar na pasta `C:\FluxQuantumAPEX\`** (pasta separada oficial).
4. **Se algum passo falhar, PARA e reporta** — não improvises alternativas.
5. **Output obrigatório:** relatório `STAGING_REPORT_<timestamp>.md` com todos os outputs literais.

---

## PASSO 1 — Criar pasta staging

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$staging = "C:\FluxQuantumAI\deploy-staging-$stamp"

# Se já existir deploy-staging de tentativa anterior, remover
if (Test-Path "C:\FluxQuantumAI\deploy-staging") {
    Write-Host "AVISO: deploy-staging pré-existente encontrado. Renomear para staging-old."
    Rename-Item -Path "C:\FluxQuantumAI\deploy-staging" -NewName "deploy-staging-old-$stamp"
}

Write-Host "Staging folder: $staging"
```

Reporta o path criado.

---

## PASSO 2 — Clonar repo FluxQuantumAI_APEX

```powershell
cd "C:\FluxQuantumAI"
git clone https://github.com/BPFeijen/FluxQuantumAI_APEX.git "deploy-staging-$stamp"

cd "$staging"
git log --oneline -3
git rev-parse HEAD
```

**Resultado esperado:**
- Clone completado sem erros
- HEAD deve ser `ee2068dc4a108c62ce1f410d2f7dfbafa8f53af6` (ou mais recente se houver commit novo)
- Os últimos 3 commits devem incluir `ee2068d Add canonical decision/events...`

Reporta o output completo.

Se o clone falhar (rede, permissões, auth), PARA e reporta.

---

## PASSO 3 — Verificar estado pré-patch

```powershell
cd "$staging"

# Confirmar que os dois bugs estão presentes antes do patch (sanity check)
Write-Host "`n=== Pré-patch: Issue #1 signature ==="
Select-String -Path "live\event_processor.py" -Pattern "def _check_pre_entry_gates"

Write-Host "`n=== Pré-patch: Issue #2 box_df ==="
Select-String -Path "live\event_processor.py" -Pattern "box_df = df\["
```

**Resultado esperado (ANTES do patch):**
- Issue #1: signature sem `price: float` — linha ~1658 com `def _check_pre_entry_gates(self, direction: str, delta_4h: float) -> tuple[bool, str]:`
- Issue #2: linha ~3133 com `box_df = df[df["m30_box_confirmed"] == True & df["m30_box_id"].notna()]` (sem parênteses)

Se QUALQUER destas duas condições já estiver no estado pós-patch, PARA e reporta — significa que o repo já contém o fix, cenário imprevisto.

---

## PASSO 4 — Aplicar patch (dry-run primeiro)

```powershell
cd "$staging"
$patchFile = "C:\FluxQuantumAI\Backups\pre-deploy-20260417_141337\fix_p0_issues_1_and_2.patch"

Write-Host "`n=== Dry-run: git apply --check ==="
git apply --check $patchFile 2>&1
$dryRunExit = $LASTEXITCODE
Write-Host "Dry-run exit code: $dryRunExit"
```

Se `$dryRunExit` for 0 → patch aplica limpo, avança para Passo 5.

Se `$dryRunExit` for não-zero → PARA e reporta. Não apliques.

---

## PASSO 5 — Aplicar patch (real)

Só executa este passo se dry-run foi OK.

```powershell
cd "$staging"

Write-Host "`n=== Apply patch ==="
git apply $patchFile 2>&1
$applyExit = $LASTEXITCODE
Write-Host "Apply exit code: $applyExit"

Write-Host "`n=== Git status pós-patch ==="
git status --short
```

**Resultado esperado:**
- `applyExit = 0`
- `git status --short` deve mostrar APENAS `M live/event_processor.py` (modificado).

Reporta o output.

---

## PASSO 6 — Validação de diff

```powershell
cd "$staging"

Write-Host "`n=== Diff stat ==="
git diff --stat

Write-Host "`n=== Diff completo ==="
git diff
```

**Resultado esperado (CRÍTICO):**
- `git diff --stat`: exactamente `1 file changed, 3 insertions(+), 3 deletions(-)`
- `git diff`: deve mostrar 3 chunks, todos em `live/event_processor.py`:
  - Chunk 1: signature `_check_pre_entry_gates` ganha parâmetro `price: float`
  - Chunk 2: caller `self._check_pre_entry_gates(direction, d4h, price)` (3 args)
  - Chunk 3: `box_df = df[(df["m30_box_confirmed"] == True) & (df["m30_box_id"].notna())]` (com parênteses)

Se o diff-stat não for `3 insertions, 3 deletions` ou o ficheiro alterado não for apenas `event_processor.py`, PARA e reporta.

Reporta o output completo do `git diff`.

---

## PASSO 7 — Validação das linhas pós-patch

```powershell
cd "$staging"

Write-Host "`n=== Pós-patch: Issue #1 signature ==="
Select-String -Path "live\event_processor.py" -Pattern "def _check_pre_entry_gates"

Write-Host "`n=== Pós-patch: Issue #1 caller ==="
Select-String -Path "live\event_processor.py" -Pattern "_check_pre_entry_gates\(direction, d4h"

Write-Host "`n=== Pós-patch: Issue #2 box_df ==="
Select-String -Path "live\event_processor.py" -Pattern "box_df = df\["
```

**Resultado esperado:**
- Signature: `def _check_pre_entry_gates(self, direction: str, delta_4h: float, price: float) -> tuple[bool, str]:`
- Caller: `blocked, block_reason = self._check_pre_entry_gates(direction, d4h, price)`
- box_df: `box_df = df[(df["m30_box_confirmed"] == True) & (df["m30_box_id"].notna())]`

Reporta output literal.

---

## PASSO 8 — No-regression: canonical observability preservada

Estas 7 strings DEVEM estar presentes — foram introduzidas pelo commit anterior `ee2068d` do Codex. Se alguma tiver zero ocorrências, o clone veio mal ou o patch desfez algo crítico.

```powershell
cd "$staging"

Write-Host "`n=== No-regression: canonical observability (event_processor.py) ==="
$needles = @("m30_bias_confirmed", "provisional_m30_bias", "refresh_macro_context", "STRUCTURE_STALE_BLOCK", "action_side", "trade_intent", "overall_state")
foreach ($n in $needles) {
    $count = (Select-String -Path "live\event_processor.py" -Pattern $n -AllMatches).Matches.Count
    Write-Host "  $n : $count matches"
}

Write-Host "`n=== No-regression: PM canonical (position_monitor.py) ==="
$pm_needles = @("_publish_canonical_pm_event", "_emit_position_event", "POSITION_MONITOR")
foreach ($n in $pm_needles) {
    $count = (Select-String -Path "live\position_monitor.py" -Pattern $n -AllMatches).Matches.Count
    Write-Host "  $n : $count matches"
}
```

**Resultado esperado:** TODOS os 10 contadores > 0. Valores aproximados esperados:
- m30_bias_confirmed: ~8
- provisional_m30_bias: ~10
- refresh_macro_context: ~5
- STRUCTURE_STALE_BLOCK: ~2
- action_side: ~2
- trade_intent: ~2
- overall_state: ~4
- _publish_canonical_pm_event: ~2
- _emit_position_event: ~10
- POSITION_MONITOR: ~2

Se algum for 0, PARA e reporta.

---

## PASSO 9 — No-regression: zero padrões de bug residual

```powershell
cd "$staging"

Write-Host "`n=== Bug pattern '== True &' em todo o código live ==="
$bugMatches = Select-String -Path "live\*.py" -Pattern '== True &[^&]'
if ($bugMatches) {
    Write-Host "FOUND (ALARM):"
    $bugMatches | Format-Table Path, LineNumber, Line
} else {
    Write-Host "Zero matches — patch efectivo, nenhum padrão residual"
}
```

**Resultado esperado:** Zero matches. Se encontrar alguma, PARA e reporta.

---

## PASSO 10 — Compile check

```powershell
cd "$staging"

$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"

Write-Host "`n=== py_compile de todos os módulos live ==="
& $pythonExe -m py_compile `
    run_live.py `
    live\event_processor.py `
    live\level_detector.py `
    live\position_monitor.py `
    live\tick_breakout_monitor.py `
    live\telegram_notifier.py `
    live\base_dashboard_server.py `
    live\m5_updater.py `
    live\m30_updater.py `
    live\operational_rules.py `
    live\feed_health.py `
    live\signal_queue.py `
    live\price_speed.py `
    live\hedge_manager.py `
    live\kill_zones.py 2>&1
$compileExit = $LASTEXITCODE
Write-Host "Compile exit code: $compileExit"
```

**Resultado esperado:** `$compileExit = 0`. Se não-zero, PARA e reporta o erro.

SyntaxWarnings sobre invalid escape sequences `\F` são cosméticos (paths Windows em docstrings) e **aceitáveis** — não são erros.

---

## PASSO 11 — Comparar staging vs live atual

Queremos saber quantos ficheiros do `live/` atual do servidor diferem da staging (o GitHub main) — para a Fase 3 sabermos o scope da substituição.

```powershell
cd "C:\FluxQuantumAI"

Write-Host "`n=== Diff count live vs staging ==="
$liveFiles = Get-ChildItem "$staging\live\*.py" -File
$changedFiles = @()
$identicalFiles = @()
$onlyInStaging = @()

foreach ($f in $liveFiles) {
    $fileName = $f.Name
    $serverPath = "C:\FluxQuantumAI\live\$fileName"
    if (Test-Path $serverPath) {
        $serverHash = (Get-FileHash $serverPath -Algorithm MD5).Hash
        $stagingHash = (Get-FileHash $f.FullName -Algorithm MD5).Hash
        if ($serverHash -ne $stagingHash) {
            $changedFiles += $fileName
        } else {
            $identicalFiles += $fileName
        }
    } else {
        $onlyInStaging += $fileName
    }
}

# Also check files in server live that are NOT in staging
$onlyInServer = @()
$serverFiles = Get-ChildItem "C:\FluxQuantumAI\live\*.py" -File
foreach ($sf in $serverFiles) {
    $stagingPath = "$staging\live\$($sf.Name)"
    if (-not (Test-Path $stagingPath)) {
        $onlyInServer += $sf.Name
    }
}

Write-Host "`nFiles CHANGED (staging differs from server): $($changedFiles.Count)"
$changedFiles | ForEach-Object { Write-Host "  - $_" }

Write-Host "`nFiles IDENTICAL: $($identicalFiles.Count)"
$identicalFiles | ForEach-Object { Write-Host "  - $_" }

Write-Host "`nFiles ONLY IN STAGING (new): $($onlyInStaging.Count)"
$onlyInStaging | ForEach-Object { Write-Host "  - $_" }

Write-Host "`nFiles ONLY IN SERVER (unique to live): $($onlyInServer.Count)"
$onlyInServer | ForEach-Object { Write-Host "  - $_" }
```

Reporta o output literal.

**Análise esperada:**
- Muitos ficheiros CHANGED (o servidor tem os 12 bugs, staging está corrigido)
- Alguns IDENTICAL (ficheiros sem alterações entre versões)
- Talvez alguns ONLY_IN_SERVER (mas confirma quais)

Se houver ficheiros só no servidor, vamos precisar decidir caso a caso na Fase 3.

---

## PASSO 12 — Gerar STAGING_REPORT

```powershell
$report = @"
# STAGING REPORT — $stamp

## Summary
- Staging folder: $staging
- Repo HEAD: <colocar output do git rev-parse do passo 2>
- Patch applied: fix_p0_issues_1_and_2.patch (2365 bytes)
- Diff stat: <output do passo 6>
- Compile exit: $compileExit
- Canonical observability: <OK | FAIL>
- Bug residual patterns: <zero | found>

## Files status (staging vs server live/)
- Changed: <count>
- Identical: <count>
- Only in staging: <count>
- Only in server: <count>

## Validation
- [x / ] Patch aplicou limpo (dry-run + real)
- [x / ] Diff tem exactamente 1 ficheiro, 3/3 insertions/deletions
- [x / ] Linhas pós-patch correctas nos 3 locais
- [x / ] Canonical observability preservada (10 strings)
- [x / ] Zero padrões de bug residual
- [x / ] py_compile passa em 14+ módulos
- [x / ] Live do servidor não foi tocado

## Observations
<qualquer coisa inesperada>

## Next step
Fase 3 — Substituição de ficheiros live.
Deve ser autorizada pela Barbara após revisão deste relatório.
"@

# Preencher manualmente os valores antes de escrever
# (ou gerar a partir dos outputs coletados)
$report | Out-File "C:\FluxQuantumAI\STAGING_REPORT_$stamp.md" -Encoding UTF8
Write-Host "Report: C:\FluxQuantumAI\STAGING_REPORT_$stamp.md"
```

Preenche os placeholders do report com os outputs reais dos passos anteriores. Reporta o report completo.

---

## OUTPUT OBRIGATÓRIO

1. Path da staging folder
2. Commit HEAD clonado
3. Output do `git diff --stat` (esperado: 1 file, 3/3)
4. Output do `git diff` completo (não truncado)
5. Output da Tarefa 11 (files status — changed/identical/etc.)
6. Output do py_compile (esperado exit 0)
7. Output dos contadores de canonical observability (esperado: todos > 0)
8. Path do STAGING_REPORT_<timestamp>.md

---

## REGRAS FINAIS

1. **Se dry-run do patch falhar** (Passo 4) → PARA e reporta erro completo do git apply.
2. **Se py_compile der erro** (Passo 10) → PARA e reporta erro + linha afectada.
3. **Se algum canonical observability tiver 0 matches** (Passo 8) → PARA e reporta.
4. **Se algum ficheiro for ONLY_IN_SERVER** (Passo 11) → reporta mas não é bloqueador nesta fase, é decisão para Fase 3.

**Quando terminares, envia o STAGING_REPORT completo. Não avances para Fase 3 nem toques no live. Aguarda autorização da Barbara.**

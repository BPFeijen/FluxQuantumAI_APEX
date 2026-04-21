# TASK: APEX Deploy — Fase 0 (Discovery, read-only)

**Para:** ClaudeCode
**Projeto:** APEX (live em C:\FluxQuantumAI\, rule-based, em produção)
**Tempo estimado:** 10 minutos
**Prioridade:** prerequisite para deploy dos fixes do GitHub

---

## CONTEXTO

Vamos preparar o deploy do código atualizado do GitHub (repo `https://github.com/BPFeijen/FluxQuantumAI_APEX`, branch `main`) ao servidor APEX em produção. Antes de tocar em nada, preciso de **discovery completo** do estado actual do servidor.

Esta task é **puramente read-only**. Não alteres nada. Não pares nenhum serviço. Não faças commits. Não crias ficheiros fora do output report.

---

## OUTPUT REQUERIDO

Cria um ficheiro `C:\FluxQuantumAI\DEPLOY_DISCOVERY_REPORT.md` com o resultado das 7 tarefas abaixo. Para cada tarefa, inclui o **comando executado** e o **output literal**.

---

### Tarefa 1 — Identificar serviços NSSM relacionados com APEX

```powershell
# Listar todos os serviços NSSM
Get-Service | Where-Object { $_.Name -like "*Flux*" -or $_.Name -like "*APEX*" -or $_.Name -like "*Quantum*" } | Format-Table Name, DisplayName, Status, StartType -AutoSize

# Para cada serviço encontrado, obter detalhes:
# (Substitui <SERVICE_NAME> pelos nomes encontrados acima)
# nssm get <SERVICE_NAME> Application
# nssm get <SERVICE_NAME> AppDirectory
# nssm get <SERVICE_NAME> AppParameters
```

Reporta:
- Nomes de todos os serviços Flux/APEX/Quantum
- Estado (Running/Stopped) de cada um
- Para cada um: caminho do executável, directório, parâmetros

### Tarefa 2 — Verificar estrutura do APEX no servidor

```powershell
# Listar raiz de C:\FluxQuantumAI\
Get-ChildItem "C:\FluxQuantumAI\" | Format-Table Name, LastWriteTime, Mode -AutoSize

# Listar pasta live\
Get-ChildItem "C:\FluxQuantumAI\live\" -File | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
```

Reporta o output completo.

### Tarefa 3 — Verificar configuração

```powershell
# Verificar existência e tamanho de settings.json e thresholds_gc.json
if (Test-Path "C:\FluxQuantumAI\config\settings.json") {
    Write-Host "settings.json: EXISTE"
    Get-Item "C:\FluxQuantumAI\config\settings.json" | Select-Object Length, LastWriteTime
} else {
    Write-Host "settings.json: NÃO EXISTE"
}

if (Test-Path "C:\FluxQuantumAI\config\thresholds_gc.json") {
    Write-Host "thresholds_gc.json: EXISTE"
    Get-Item "C:\FluxQuantumAI\config\thresholds_gc.json" | Select-Object Length, LastWriteTime
}

# Listar outros ficheiros em config
Get-ChildItem "C:\FluxQuantumAI\config\" -File | Format-Table Name, LastWriteTime -AutoSize
```

Reporta.

### Tarefa 4 — Verificar logs e runtime state

```powershell
Get-ChildItem "C:\FluxQuantumAI\logs\" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 15 | Format-Table Name, Length, LastWriteTime -AutoSize
```

Reporta.

### Tarefa 5 — Verificar Git access do servidor

```powershell
# Tem git instalado?
git --version

# Consegue ver o repo remoto?
git ls-remote https://github.com/BPFeijen/FluxQuantumAI_APEX.git HEAD 2>&1
```

Reporta o output de ambos. Se `git ls-remote` devolver erro, reporta o erro integralmente.

### Tarefa 6 — Verificar Python e módulos críticos

```powershell
python --version
python -c "import MetaTrader5; print('MetaTrader5:', MetaTrader5.__version__)" 2>&1
python -c "import pandas; print('pandas:', pandas.__version__)" 2>&1
python -c "import watchdog; print('watchdog: OK')" 2>&1
```

Reporta output.

### Tarefa 7 — Verificar processos Python em execução

```powershell
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, CPU, StartTime, Path, CommandLine | Format-Table -AutoSize
```

Reporta. Serve para sabermos quantos processos estão a correr e se há serviços ativos.

---

## REGRAS

1. **Zero escrita fora do `DEPLOY_DISCOVERY_REPORT.md`.** Não criar outros ficheiros. Não alterar nenhum ficheiro existente.
2. **Se algum comando falhar, reporta o erro integralmente.** Não tentes contornar. Não inventes output.
3. **Não parar nenhum serviço.** APEX está em produção — não tocar.
4. **Se não tiveres acesso a algum path ou comando, reporta explicitamente "ACESSO NEGADO" ou "COMANDO NÃO ENCONTRADO".**

---

## FORMATO DO REPORT

```markdown
# DEPLOY DISCOVERY REPORT
Data: <timestamp>
Executado por: ClaudeCode

## Tarefa 1 — Serviços NSSM
<comandos e outputs>

## Tarefa 2 — Estrutura APEX
<comandos e outputs>

## Tarefa 3 — Configuração
<comandos e outputs>

## Tarefa 4 — Logs e runtime state
<comandos e outputs>

## Tarefa 5 — Git access
<comandos e outputs>

## Tarefa 6 — Python
<comandos e outputs>

## Tarefa 7 — Processos em execução
<comandos e outputs>

## Observações
<qualquer coisa estranha ou inesperada>
```

---

**Quando terminares, envia o conteúdo completo do `DEPLOY_DISCOVERY_REPORT.md` como output. Não faças deploy, não toques em nada, não avances para a próxima fase. Espera instrução da Barbara.**

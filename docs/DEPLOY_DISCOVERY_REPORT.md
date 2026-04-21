# DEPLOY DISCOVERY REPORT
Data: 2026-04-17
Executado por: ClaudeCode
Modo: read-only (nenhum serviço parado, nenhum ficheiro alterado fora deste report)

---

## Tarefa 1 — Serviços NSSM

### Comando
```powershell
Get-Service -Name *Flux*,*APEX*,*Quantum* -ErrorAction SilentlyContinue | Format-Table Name, DisplayName, Status, StartType -AutoSize
```

### Output
```
Name                             DisplayName                       Status StartType
----                             -----------                       ------ ---------
FluxQuantumAPEX                  FluxQuantumAPEX                  Stopped Automatic
FluxQuantumAPEX_Dashboard        FluxQuantumAPEX_Dashboard        Running Automatic
FluxQuantumAPEX_Dashboard_Hantec FluxQuantumAPEX_Dashboard_Hantec Running Automatic
FluxQuantumAPEX_Live             FluxQuantumAPEX_Live             Stopped Automatic
```

Busca adicional por serviços de captura:
```powershell
Get-Service -Name *Quantower*,*Iceberg*,*Level2*,*Capture*,*Watchdog* -ErrorAction SilentlyContinue
```
Output:
```
Name                 DisplayName           Status StartType
----                 -----------           ------ ---------
CaptureService_835e7 CaptureService_835e7 Stopped    Manual
```
(Serviço genérico, parado, manual — provavelmente não ligado ao APEX.)

### Detalhes NSSM por serviço

Binário localizado em `C:\tools\nssm\nssm.exe`. Comando executado para cada serviço:
```powershell
C:\tools\nssm\nssm.exe get <SERVICE> Application
C:\tools\nssm\nssm.exe get <SERVICE> AppDirectory
C:\tools\nssm\nssm.exe get <SERVICE> AppParameters
```
(Output literal vem em UTF-16 — reproduzido abaixo já decodificado.)

**FluxQuantumAPEX** — Stopped
- Application: `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`
- AppDirectory: `C:\FluxQuantumAI`
- AppParameters: `-u -W ignore run_live.py --execute --broker roboforex --lot_size 0.05`

**FluxQuantumAPEX_Dashboard** — Running
- Application: `C:/Users/Administrator/AppData/Local/Programs\Python\Python311\python.exe`
- AppDirectory: `C:\FluxQuantumAPEX\dashboard`
- AppParameters: `-u -W ignore api.py`

**FluxQuantumAPEX_Dashboard_Hantec** — Running
- Application: `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe`
- AppDirectory: `C:\FluxQuantumAI`
- AppParameters: `-u -W ignore C:\FluxQuantumAI\live\dashboard_server_hantec.py`

**FluxQuantumAPEX_Live** — Stopped
- Application: `C:/Users/Administrator/AppData/Local/Programs/Python/Python311/python.exe`
- AppDirectory: `C:\FluxQuantumAI`
- AppParameters: `-u -W ignore run_live.py --execute --broker hantec --lot_size 0.05 --no-updaters`

---

## Tarefa 2 — Estrutura APEX

### Comando
```powershell
Get-ChildItem "C:\FluxQuantumAI\" | Format-Table Name, LastWriteTime, Mode -AutoSize
```

### Output (raiz de C:\FluxQuantumAI\)
```
Name                                LastWriteTime         Mode
----                                -------------         ----
.venv                               4/13/2026 12:49:43 AM d-----
apex_nextgen                        4/12/2026 3:18:22 AM  d-----
config                              4/14/2026 11:07:56 AM d-----
configs                             1/2/2026 8:28:10 PM   d-----
data                                4/12/2026 11:12:36 PM d-----
docs                                4/14/2026 10:16:58 PM d-----
DOCS MASTER                         4/12/2026 3:02:43 AM  d-----
iceberg_data                        4/11/2026 3:07:02 AM  d-----
live                                4/15/2026 8:00:41 AM  d-----
logs                                4/17/2026 12:00:50 AM d-----
ml_iceberg_v2                       4/11/2026 3:21:52 PM  d-----
models                              4/13/2026 1:12:52 AM  d-----
results                             4/11/2026 10:43:41 AM d-----
rl_v3                               4/11/2026 3:49:27 PM  d-----
schemas                             4/11/2026 6:39:43 PM  d-----
scripts                             4/14/2026 9:56:31 PM  d-----
tests                               4/11/2026 8:29:15 PM  d-----
__pycache__                         4/14/2026 8:49:44 PM  d-----
.env                                4/14/2026 8:43:23 PM  -a----
.gitignore                          4/9/2026 10:22:33 AM  -a----
ats_iceberg_gate.py                 4/13/2026 12:53:01 AM -a----
ats_live_gate.py                    4/14/2026 12:15:23 AM -a----
cal_level_touch.py                  4/10/2026 9:49:57 PM  -a----
check_capture_status.bat            3/27/2026 7:17:52 PM  -a----
C?FluxQuantumAIlogsv3_train_run.log 4/8/2026 11:29:55 PM  -a----
grenadier_guardrail.py              4/11/2026 1:15:06 AM  -a----
iceberg_receiver.py                 4/12/2026 11:18:29 PM -a----
install_watchdog.bat                3/27/2026 7:17:35 PM  -a----
launch_demo_asia.bat                4/8/2026 11:36:23 PM  -a----
mt5_executor.py                     4/14/2026 8:49:04 PM  -a----
mt5_executor_hantec.py              4/14/2026 10:02:14 AM -a----
quantower_level2_api.py             12/23/2025 2:48:47 AM -a----
reconstruct_icebergs_databento.py   4/12/2026 11:18:27 PM -a----
requirements.txt                    4/11/2026 4:37:12 PM  -a----
run_apex_interactive.bat            4/14/2026 8:01:06 PM  -a----
run_apex_wrapper.py                 4/14/2026 8:50:30 PM  -a----
run_live.py                         4/14/2026 4:12:50 PM  -a----
start_apex_full.bat                 4/14/2026 7:48:41 PM  -a----
start_apex_robo.bat                 4/14/2026 7:45:36 PM  -a----
submit_job.py                       4/11/2026 2:17:01 PM  -a----
test_mt5_ipc.py                     4/14/2026 8:03:24 PM  -a----
test_mt5_ipc2.py                    4/14/2026 8:21:33 PM  -a----
train_grenadier.py                  4/11/2026 3:01:43 AM  -a----
train_iceberg_local.py              4/11/2026 4:48:15 PM  -a----
watchdog_l2_capture.py              4/10/2026 12:05:44 PM -a----
```
Nota: Existe também `.git` (hidden) — ver Tarefa 5.

### Comando
```powershell
Get-ChildItem "C:\FluxQuantumAI\live\" -File | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
```

### Output (live\)
```
Name                       Length LastWriteTime
----                       ------ -------------
base_dashboard_server.py    23023 4/14/2026 11:13:45 PM
d1_h4_updater.py            23312 4/14/2026 3:28:18 PM
dashboard_server.py          2739 4/13/2026 7:42:36 AM
dashboard_server_hantec.py   8228 4/14/2026 11:14:08 PM
event_processor.py         189789 4/15/2026 8:00:41 AM
feed_health.py               7110 4/13/2026 7:42:36 AM
hedge_manager.py            17413 4/13/2026 7:42:36 AM
kill_zones.py                3909 4/13/2026 7:42:36 AM
level_detector.py           22888 4/14/2026 11:51:31 AM
m30_updater.py              21473 4/15/2026 7:59:40 AM
m5_updater.py               24342 4/15/2026 7:49:06 AM
operational_rules.py         7467 4/13/2026 7:42:36 AM
position_monitor.py         82801 4/14/2026 10:57:14 PM
price_speed.py               6003 4/13/2026 7:42:36 AM
signal_queue.py              6908 4/13/2026 7:42:36 AM
telegram_notifier.py        26287 4/14/2026 8:43:10 PM
tick_breakout_monitor.py    16005 4/13/2026 7:42:36 AM
__init__.py                     0 3/31/2026 1:55:38 PM
```

---

## Tarefa 3 — Configuração

### Comando
```powershell
if (Test-Path "C:\FluxQuantumAI\config\settings.json") { ... }
if (Test-Path "C:\FluxQuantumAI\config\thresholds_gc.json") { ... }
Get-ChildItem "C:\FluxQuantumAI\config\" -File | Format-Table Name, LastWriteTime -AutoSize
```

### Output
```
settings.json: EXISTE
Length LastWriteTime
------ -------------
  5244 4/15/2026 8:11:24 AM

thresholds_gc.json: NAO EXISTE

Name                        LastWriteTime
----                        -------------
calibration_results.json    4/7/2026 11:02:52 PM
calibration_results_v2.json 4/8/2026 7:15:31 AM
proxy_events.json           4/11/2026 1:58:32 AM
settings.json               4/15/2026 8:11:24 AM
settings_calibrated.json    4/7/2026 11:02:52 PM
settings_calibrated_v2.json 4/8/2026 7:24:14 AM
```

**Observação:** `thresholds_gc.json` NÃO existe no servidor.

---

## Tarefa 4 — Logs e runtime state

### Comando
```powershell
Get-ChildItem "C:\FluxQuantumAI\logs\" -File | Sort-Object LastWriteTime -Descending | Select-Object -First 15 | Format-Table Name, Length, LastWriteTime -AutoSize
```

### Output
```
Name                               Length LastWriteTime
----                               ------ -------------
watchdog_apex.log                  123645 4/17/2026 12:43:22 PM
quantower_level2_api_stdout.log 282990208 4/17/2026 3:58:33 AM
quantower_level2_api_stderr.log     20182 4/17/2026 12:07:06 AM
watchdog.log                            0 4/17/2026 12:00:50 AM
watchdog.log.2026-04-16            158560 4/16/2026 11:59:50 PM
service_stdout.log                2385996 4/16/2026 7:46:21 PM
continuation_trades.jsonl        13259284 4/16/2026 7:46:19 PM
service_stderr.log                 637124 4/16/2026 7:46:15 PM
service_state.json                   1169 4/16/2026 7:46:12 PM
service_hantec_stdout.log         4407030 4/16/2026 7:45:51 PM
service_hantec_stderr.log         2718063 4/16/2026 7:45:47 PM
decision_log.jsonl               10770253 4/16/2026 7:43:37 PM
decision_live.json                   2060 4/16/2026 7:43:37 PM
live_log_live.csv                 1113516 4/16/2026 7:43:37 PM
live_log.csv                      1695237 4/16/2026 7:43:37 PM
```

**Observações:**
- `quantower_level2_api_stdout.log` = **282.99 MB** — tamanho anormal.
- Logs `service_*` (FluxQuantumAPEX) e `service_hantec_*` (FluxQuantumAPEX_Live) pararam de crescer em **2026-04-16 19:46** — consistente com os dois serviços estarem Stopped.
- `watchdog_apex.log` continua a escrever (última modificação 2026-04-17 12:43).

---

## Tarefa 5 — Git access

### Comando
```powershell
git --version
git ls-remote https://github.com/BPFeijen/FluxQuantumAI_APEX.git HEAD 2>&1
```

### Output
```
git version 2.47.1.windows.1
ee2068dc4a108c62ce1f410d2f7dfbafa8f53af6	HEAD
```

Repo remoto **acessível**. HEAD remoto: `ee2068dc4a108c62ce1f410d2f7dfbafa8f53af6`.

### Estado do git local em C:\FluxQuantumAI\
```powershell
cd C:\FluxQuantumAI; git remote -v; git branch --show-current; git status --short; git rev-parse HEAD
```
Output:
```
origin  https://github.com/BPFeijen/FluxQuantumAI.git (fetch)
origin  https://github.com/BPFeijen/FluxQuantumAI.git (push)
---branch---
master
---status---
?? .gitignore
?? .venv/
?? "C\357\200\272FluxQuantumAIlogsv3_train_run.log"
?? "DOCS MASTER/"
?? __pycache__/
?? apex_nextgen/
?? ats_iceberg_gate.py
?? ats_live_gate.py
?? cal_level_touch.py
?? check_capture_status.bat
?? config/
?? configs/
?? data/
?? docs/
?? grenadier_guardrail.py
?? iceberg_data/
?? iceberg_receiver.py
?? install_watchdog.bat
?? launch_demo_asia.bat
?? live/
---head---
fatal: ambiguous argument 'HEAD': unknown revision or path not in the working tree.
```

**Observações críticas:**
- O `.git` local aponta para `https://github.com/BPFeijen/FluxQuantumAI.git` — **NÃO** para `FluxQuantumAI_APEX.git` (o repo-alvo do deploy).
- Branch `master` (não `main` nem `gold-gc`).
- **Sem commits** — `git rev-parse HEAD` falha. Todos os ficheiros aparecem como untracked.
- Efectivamente o servidor tem um `.git` inicializado mas vazio, com remoto errado. O deploy a partir de `FluxQuantumAI_APEX` vai precisar de decidir: adicionar novo remote, re-clonar noutro directório, ou substituir.

---

## Tarefa 6 — Python

### Comando
```powershell
python --version
python -c "import MetaTrader5; print('MetaTrader5:', MetaTrader5.__version__)"
python -c "import pandas; print('pandas:', pandas.__version__)"
python -c "import watchdog; print('watchdog: OK')"
```

### Output
```
Python 3.11.9
MetaTrader5: 5.0.5640
pandas: 2.3.3
watchdog: OK
```

Todos os módulos críticos presentes e importáveis.

---

## Tarefa 7 — Processos Python em execução

### Comando
```powershell
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, CPU, StartTime, Path | Format-Table -AutoSize
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, CommandLine | Format-List
```

### Output (processos)
```
Id         CPU StartTime            Path
--         --- ---------            ----
 2512  722.421875 4/14/2026 9:35:00 AM C:\...\Python311\python.exe
 8076 1271.078125 4/17/2026 3:58:34 AM C:\...\Python311\python.exe
 8248  622.421875 4/14/2026 9:35:02 AM C:\...\Python311\python.exe
13128   72.078125 4/14/2026 9:12:33 AM C:\...\Python311\python.exe
13712  357.421875 4/14/2026 2:13:47 PM C:\...\Python311\python.exe
14948     977.625 4/14/2026 2:13:43 PM C:\...\Python311\python.exe
```

### CommandLine por PID
```
PID 13128: "python.exe" -u -W ignore src/dashboard/app.py
PID  2512: python.exe C:\FluxQuantumAI\watchdog_l2_capture.py
PID  8248: python.exe C:\FluxQuantumAI\iceberg_receiver.py
PID 14948: "python.exe" -u -W ignore api.py
PID 13712: "python.exe" -u -W ignore C:\FluxQuantumAI\live\dashboard_server_hantec.py
PID  8076: python.exe -m uvicorn quantower_level2_api:app --host 0.0.0.0 --port 8000 --log-level info
```

Mapeamento:
- **PID 8076** — `quantower_level2_api` (uvicorn, porta 8000). CRÍTICO — captura L2.
- **PID 8248** — `iceberg_receiver.py` (porta 8002). CRÍTICO — captura iceberg.
- **PID 2512** — `watchdog_l2_capture.py`.
- **PID 14948** — `api.py` (provavelmente serviço NSSM `FluxQuantumAPEX_Dashboard`, AppDirectory = `C:\FluxQuantumAPEX\dashboard`).
- **PID 13712** — `live\dashboard_server_hantec.py` (serviço `FluxQuantumAPEX_Dashboard_Hantec`).
- **PID 13128** — `src/dashboard/app.py` (origem não identificada — AppDirectory desconhecido).

Nenhum processo corresponde a `run_live.py` — coerente com os serviços FluxQuantumAPEX e FluxQuantumAPEX_Live estarem Stopped.

---

## Observações

1. **Trading STOPPED.** `FluxQuantumAPEX` (roboforex) e `FluxQuantumAPEX_Live` (hantec) estão ambos Stopped. Os logs `service_*.log` e `service_hantec_*.log` pararam de escrever às 2026-04-16 19:46. Os dashboards continuam a correr.

2. **Captura de dados em produção.** `quantower_level2_api` (PID 8076, porta 8000) e `iceberg_receiver.py` (PID 8248) estão a correr como processos autónomos (não via NSSM). Conforme memória `feedback_capture_services_never_kill` e `feedback_never_restart_capture`: **NÃO tocar**.

3. **Parâmetros NSSM diferem da memória.** Memória `feedback_run_live_restart` diz "hantec 0.02"; NSSM actual mostra `--lot_size 0.05` para ambos os serviços — a memória está desactualizada ou o lot foi alterado.

4. **`thresholds_gc.json` não existe.** A tarefa 3 referenciava este ficheiro; apenas `settings.json` (+ variantes `calibrated`) está presente. Verificar se o deploy novo depende deste ficheiro.

5. **Git local aponta para o repo errado.** `.git` em `C:\FluxQuantumAI\` tem remoto `FluxQuantumAI.git` (não `FluxQuantumAI_APEX.git`), branch `master`, zero commits, tudo untracked. O deploy terá de resolver isto antes de `git pull`.

6. **Log gigante.** `quantower_level2_api_stdout.log` = 283 MB. Pode precisar de rotação (fora do scope desta task, só reportar).

7. **AppDirectory do Dashboard roboforex** aponta para `C:\FluxQuantumAPEX\dashboard` (pasta diferente de `C:\FluxQuantumAI\`). Existência e conteúdo desse directório não foram verificados nesta discovery.

8. **Process PID 13128** corre `src/dashboard/app.py` mas não identifiquei a que serviço/directório pertence — não há NSSM equivalente nos 4 serviços listados. Possível processo manual ou de outra origem.

Deploy não foi executado. Aguardo instrução da Barbara.

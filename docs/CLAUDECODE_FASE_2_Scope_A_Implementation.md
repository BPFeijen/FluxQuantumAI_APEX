# TASK: FASE 2 — Scope A Implementation (Telegram Decoupling + Fix Semantic Bug)

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Design doc aprovado:** `DESIGN_DOC_Telegram_PositionEvents_v1.md` (Barbara 2026-04-17)
**Escopo desta fase:** Scope A do design doc (Telegram Decoupling)
**Tempo estimado:** 4-6 horas implementação + tests
**Output:** `FASE_2_SCOPE_A_REPORT_<timestamp>.md`

**PRÉ-REQUISITO OBRIGATÓRIO:** Fase 1 backup completo e validado.
Backup location: `C:\FluxQuantumAI\Backups\pre-telegram-fix-20260418_011600\`

Hashes pre-fix (para rollback):
- `event_processor.py`   → `C48157668BAF47668E61DB460A27BDEE`
- `telegram_notifier.py` → `4893A895DD5E5EB45B91FF09F0B9A55F`
- `position_monitor.py`  → `91DC4B608B9FD231FE2B9DD0B4BE080A`

---

## CRITICAL RULES

1. **APENAS Scope A.** Scope B (MT5 history, news exit, L2 DANGER, PULLBACK_START) é **PROIBIDO** nesta fase.
2. **Usar `str_replace` cirúrgico.** Zero rewrites completos de funções.
3. **Cada mudança tem snippet exacto documentado.** Não inventar.
4. **py_compile OBRIGATÓRIO após cada ficheiro.** Sem verificação = ABORT.
5. **NÃO deployar nesta fase.** Apenas modificar ficheiros em staging + verificar.
6. **NÃO parar serviços.** Deploy é Fase 7.
7. **Report detalhado com diffs por mudança.** Claude vai auditar linha-a-linha.
8. **Anti-hallucinations:** cada `str_replace` tem old_str exacto do ficheiro actual. Se old_str não for encontrado, reportar e parar.

---

## OBJECTIVO

Implementar 3 fixes arquiteturais no sistema Telegram:

**Fix A.1 — Decouple Telegram from broker:**
- GO decision notifica imediatamente (antes de MT5 tentar)
- EXECUTED/EXEC_FAILED notificam depois como mensagem separada

**Fix A.2 — Fix semantic bug BLOCK vs GO:**
- `telegram_notifier.py` actualmente label "BLOCK" hardcoded mesmo quando action é "GO"
- Separar branches: GO, BLOCK, PM_EVENT com mensagens correctas

**Fix A.3 — Position events notificam Telegram:**
- `_publish_canonical_pm_event()` escreve no decision_live.json mas nunca chama Telegram
- Adicionar `tg.notify_decision()` após write canónico

---

## FILES MODIFIED IN THIS PHASE

1. `C:\FluxQuantumAI\live\event_processor.py` (3-5 mudanças cirúrgicas)
2. `C:\FluxQuantumAI\live\telegram_notifier.py` (refactor parcial)
3. `C:\FluxQuantumAI\live\position_monitor.py` (2 mudanças)

**Files modified in Fase 2 STOP HERE.** Nenhum outro ficheiro.

---

## DIRECTORY STRUCTURE

**Staging approach:**
- Criar directório `C:\FluxQuantumAI\deploy-staging-scope-a-<timestamp>\`
- Copiar os 3 ficheiros source para staging
- **Modificar na staging**, não no live
- py_compile na staging
- Report diffs
- Deploy só em Fase 7 (fora deste prompt)

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$staging_dir = "C:\FluxQuantumAI\deploy-staging-scope-a-$timestamp"

New-Item -Path $staging_dir -ItemType Directory -Force | Out-Null
New-Item -Path "$staging_dir\live" -ItemType Directory -Force | Out-Null

Copy-Item "C:\FluxQuantumAI\live\event_processor.py"   "$staging_dir\live\"
Copy-Item "C:\FluxQuantumAI\live\telegram_notifier.py" "$staging_dir\live\"
Copy-Item "C:\FluxQuantumAI\live\position_monitor.py"  "$staging_dir\live\"

Write-Host "Staging: $staging_dir"
```

---

## MUDANÇA 1 — `event_processor.py` — Add GO notification BEFORE execution

**Contexto:** Actualmente GO só notifica DEPOIS de MT5 tentar. Se MT5 falha, user vê apenas EXEC_FAILED. Precisa notificar GO imediatamente após decisão gate.

**Location:** Linhas ~2361-2370 (função que processa GO decision)

**old_str (copiar exactamente):**
```python
        if not decision.go:
            print(f"[{ts}] BLOCK: {decision.reason}")
            tg.notify_decision()
            return

        # Gate passed (GO) — lock per-direction cooldown so only GO resets it
        with self._lock:
            self._last_trigger_by_dir[direction] = time.monotonic()
```

**new_str:**
```python
        if not decision.go:
            print(f"[{ts}] BLOCK: {decision.reason}")
            tg.notify_decision()
            return

        # === Telegram Decoupling (Fase 2): notify GO signal BEFORE execution ===
        # Signal is independent of broker. Barbara receives immediately.
        # Execution result notified separately via notify_execution() below.
        print(f"[{ts}] GO SIGNAL: {decision.reason}")
        tg.notify_decision()

        # Gate passed (GO) — lock per-direction cooldown so only GO resets it
        with self._lock:
            self._last_trigger_by_dir[direction] = time.monotonic()
```

---

## MUDANÇA 2 — `event_processor.py` — EXECUTED uses notify_execution

**Location:** Linhas ~2509-2513

**old_str (copiar exactamente):**
```python
                _decision_dict["decision"]["action"] = "EXECUTED"
                self._write_decision(_decision_dict)

                # Telegram notification — reads decision_live.json (Single Source of Truth)
                tg.notify_decision()
```

**new_str:**
```python
                _decision_dict["decision"]["action"] = "EXECUTED"
                self._write_decision(_decision_dict)

                # Separate execution confirmation message (Fase 2 Telegram Decoupling)
                tg.notify_execution()
```

---

## MUDANÇA 3 — `event_processor.py` — EXEC_FAILED uses notify_execution

**Location:** Linhas ~2581-2584

**old_str (copiar exactamente):**
```python
                self._write_decision(_decision_dict)
                log.error("EXEC_FAILED: GO %s score=%d but no broker executed", direction, sc)
                print(f"[{ts}] EXEC_FAILED: GO {direction} — NO BROKER CONNECTED")
                tg.notify_decision()
```

**new_str:**
```python
                self._write_decision(_decision_dict)
                log.error("EXEC_FAILED: GO %s score=%d but no broker executed", direction, sc)
                print(f"[{ts}] EXEC_FAILED: GO {direction} — NO BROKER CONNECTED")
                # Separate execution failure message (Fase 2 Telegram Decoupling)
                tg.notify_execution()
```

---

## MUDANÇA 4 — `event_processor.py` — GAMMA branch decoupling

**Context:** GAMMA is alternative strategy. Same pattern — notify_decision() at line 3623 currently fires for execution result. Precisa decoupling similar ao ALPHA.

**First:** ClaudeCode DEVE procurar e mostrar o contexto da linha 3623 exactamente como está no ficheiro live. Reportar.

Se o pattern for idêntico ao Mudança 2/3 (notify_decision chamado depois de action="EXECUTED" ou "EXEC_FAILED"), aplicar mesma substituição: `tg.notify_decision()` → `tg.notify_execution()`.

Se o pattern for diferente do que parece, **PARAR e reportar**. Não forçar fix.

**Ao completar esta mudança, incluir snippet exacto do antes e depois no report.**

---

## MUDANÇA 5 — `event_processor.py` — DELTA branch decoupling

**Context:** DELTA é outra estratégia alternativa. Linha ~3920.

**Instruction:** Mesma lógica da MUDANÇA 4. Procurar, mostrar contexto, aplicar se pattern bate, parar se não bate.

**Report snippet antes/depois.**

---

## MUDANÇA 6 — `telegram_notifier.py` — Refactor notify_decision + Add notify_execution

**Context:** Actualmente `notify_decision()` tem bug grave na linha 156:
```python
elif action in ("BLOCK", "GO"):
    text = (
        f"\u26D4 <b>BLOCK \u2014 {direction}</b>\n"  # ⛔ SEMPRE "BLOCK"
        ...
```

**Solution:** Separar em 3 branches claros: GO, BLOCK, PM_EVENT. Adicionar nova função `notify_execution()` para EXECUTED e EXEC_FAILED.

**Strategy:** Esta mudança é maior que as anteriores. **Em vez de str_replace de 100+ linhas, fazer em 3 str_replaces menores:**

### Mudança 6.1 — Add `_last_execution_id` module variable

**Procurar `_last_decision_id = ""` no topo do módulo. Adicionar linha depois:**

**old_str:**
```python
_last_decision_id = ""
```

**new_str:**
```python
_last_decision_id = ""
_last_execution_id = ""
```

### Mudança 6.2 — Replace elif action in ("BLOCK", "GO") branch

**old_str (copiar exactamente das linhas 156-183 do ficheiro actual):**
```python
    elif action in ("BLOCK", "GO"):
        # BLOCK or GO (not executed)
        blocked_by = ""
        for gname, gkey in [("V1", "v1_zone"), ("V2", "v2_l2"), ("V3", "v3_momentum"), ("V4", "v4_iceberg")]:
            gs = gates.get(gkey, {}).get("status", "")
            if gs and gs.upper() in ("BLOCK", "ZONE_FAIL"):
                blocked_by = gname
                break

        text = (
            f"\u26D4 <b>BLOCK \u2014 {direction}</b>\n"
            f"Intent: {trade_intent} | Setup blocked: {action_side}\n"
            f"Price: {price_mt5:.2f}\n"
            f"Source: {_nl_source}\n"
        )
        if blocked_by:
            text += f"Blocked by: {blocked_by}\n"
        text += (
            f"Reason: {reason}\n"
            f"Context:\n"
            f"Phase: {phase} | Bias: {bias}\n"
            f"\u03944h: {d4h:+.0f}\n"
            f"Anomaly: {anomaly.get('alignment', 'UNKNOWN')}/{anomaly.get('severity', 'NONE')} | Entry: {anomaly.get('entry_action', 'UNKNOWN')} | Pos: {anomaly.get('position_action', 'UNKNOWN')}\n"
            f"Iceberg: {iceberg.get('alignment', 'UNKNOWN')}/{iceberg.get('severity', 'NONE')} | Entry: {iceberg.get('entry_action', 'UNKNOWN')} | Pos: {iceberg.get('position_action', 'UNKNOWN')}\n"
            f"Gates:\n"
            f"{gates_line}\n"
            f"\U0001F194 {dec_id} | {ts_display} | {semantics_v}"
        )
```

**new_str:**
```python
    elif action == "GO":
        # GO signal — emitted BEFORE execution (Fase 2 decoupling)
        sl = dec.get("sl", 0)
        tp1 = dec.get("tp1", 0)
        tp2 = dec.get("tp2", 0)
        text = (
            f"\U0001F3AF <b>GO \u2014 {direction}</b>\n"
            f"{price_mt5:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f} | Runner: ON\n"
            f"Score: {score:+d} | Reason: {reason}\n"
            f"Phase: {phase} | Bias: {bias} | \u03944h: {d4h:+.0f}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    elif action == "BLOCK":
        # Gate rejected entry
        blocked_by = ""
        for gname, gkey in [("V1", "v1_zone"), ("V2", "v2_l2"), ("V3", "v3_momentum"), ("V4", "v4_iceberg")]:
            gs = gates.get(gkey, {}).get("status", "")
            if gs and gs.upper() in ("BLOCK", "ZONE_FAIL"):
                blocked_by = gname
                break
        blocked_by_str = f" (by {blocked_by})" if blocked_by else ""

        text = (
            f"\u26D4 <b>BLOCK \u2014 {direction}</b>\n"
            f"{price_mt5:.2f}{blocked_by_str}\n"
            f"Reason: {reason}\n"
            f"Phase: {phase} | Bias: {bias} | \u03944h: {d4h:+.0f}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )
```

### Mudança 6.3 — Upgrade PM_EVENT branch com icon map

**old_str (linhas ~185-195, copiar exactamente):**
```python
    elif action == "PM_EVENT":
        pe = dl.get("position_event", {})
        text = (
            f"\U0001F6E0 <b>POSITION EVENT — {pe.get('event_type', '?')}</b>\n"
            f"Dir: {pe.get('direction_affected', 'UNKNOWN')} | Action: {pe.get('action_type', 'UNKNOWN')}\n"
            f"Reason: {pe.get('reason', '')}\n"
            f"Exec: {pe.get('execution_state', 'UNKNOWN')} | DryRun: {pe.get('dry_run')} | T3: {pe.get('t3_mode', 'UNKNOWN')}\n"
            f"Broker: {pe.get('broker', 'UNKNOWN')} | Account: {pe.get('account', '?')} | Ticket: {pe.get('ticket', '?')}\n"
            f"Result: {pe.get('result', '')}\n"
            f"\U0001F194 {dec_id} | {ts_display} | {semantics_v}"
        )
```

**new_str:**
```python
    elif action == "PM_EVENT":
        pe = dl.get("position_event", {})
        event_type = pe.get("event_type", "?")
        direction_affected = pe.get("direction_affected", "UNKNOWN")
        action_type = pe.get("action_type", "UNKNOWN")
        pm_reason = pe.get("reason", "")
        exec_state = pe.get("execution_state", "UNKNOWN")
        broker = pe.get("broker", "UNKNOWN")
        ticket = pe.get("ticket", "?")
        result = pe.get("result", "")

        # Icon map per event type
        icon_map = {
            "SHIELD":              "\U0001F6E1",  # 🛡
            "TP1_HIT":             "\u2705",       # ✅
            "TP2_HIT":             "\U0001F3C6",  # 🏆
            "SL_HIT":              "\U0001F6D1",  # 🛑
            "REGIME_FLIP":         "\U0001F504",  # 🔄
            "PULLBACK_START":      "\u21A9",       # ↩
            "PULLBACK_END_EXIT":   "\u21AA",       # ↪
            "L2_DANGER":           "\u26A0",       # ⚠
            "T3_EXIT":             "\U0001F6A8",  # 🚨
            "NEWS_EXIT":           "\U0001F4F0",  # 📰
        }
        icon = icon_map.get(event_type, "\U0001F6E0")  # 🛠 default

        text = (
            f"{icon} <b>{event_type} \u2014 {direction_affected}</b>\n"
            f"Action: {action_type}\n"
            f"Reason: {pm_reason}\n"
            f"Broker: {broker} | Ticket: #{ticket}\n"
            f"Exec: {exec_state} | Result: {result}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )
```

### Mudança 6.4 — Add notify_execution() function

**old_str:** (procurar pela linha ANTES da função `notify_entry_go(**kwargs):`)

```python
def notify_entry_go(**kwargs):
```

**new_str:**
```python
def notify_execution() -> bool:
    """
    Notify Telegram of EXECUTION event (EXECUTED or EXEC_FAILED).
    Separate message after broker responds (Fase 2 decoupling).
    """
    global _last_execution_id

    dl = _read_json(_DECISION_LIVE_PATH)
    if not dl:
        return False

    dec = dl.get("decision", {})
    dec_id = dl.get("decision_id", "")
    action = dec.get("action", "")

    # Anti-spam
    if dec_id == _last_execution_id:
        return False
    _last_execution_id = dec_id

    direction = dec.get("direction", "?")
    price_mt5 = dl.get("price_mt5", 0)
    ts_str = dl.get("timestamp", "")
    ts_display = ts_str[11:19] + " UTC" if len(ts_str) > 19 else ""

    if action == "EXECUTED":
        exec_info = dec.get("execution", {})
        brokers = exec_info.get("brokers", [])
        ok_broker = "?"
        ticket = "?"
        for b in brokers:
            if b.get("result_state") == "EXECUTED" or b.get("state") == "EXECUTED":
                ok_broker = b.get("broker") or b.get("name", "?")
                ticket = b.get("ticket", "?")
                break

        text = (
            f"\u2705 <b>ORDER OPENED \u2014 {direction} @ {price_mt5:.2f}</b>\n"
            f"Broker: {ok_broker} | Ticket: #{ticket}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    elif action == "EXEC_FAILED":
        reason = dec.get("reason", "")
        text = (
            f"\u274C <b>ORDER FAILED \u2014 {direction}</b>\n"
            f"{reason}\n"
            f"{ts_display} | id: {dec_id[:8]}"
        )

    else:
        log.warning("notify_execution: unexpected action=%s", action)
        return False

    _send_async(text)
    return True


def notify_entry_go(**kwargs):
```

---

## MUDANÇA 7 — `position_monitor.py` — Add notify_decision() after canonical PM event write

**Context:** `_publish_canonical_pm_event()` escreve decision_live.json mas NUNCA chama Telegram. Resultado: SHIELD, REGIME_FLIP, PULLBACK_END, L2_DANGER, T3_EXIT, etc. são gravados mas nunca notificados.

**Location:** Função `_publish_canonical_pm_event()` (linha ~1973)

**Procurar o final da função (onde está `except Exception as e:` + `log.debug("canonical PM publish failed: %s", e)`)**

**old_str (copiar exactamente):**
```python
        try:
            DECISION_LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with self._canonical_lock:
                tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(decision_payload, f, indent=2, default=str)
                tmp.replace(DECISION_LIVE_PATH)
                with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(decision_payload, default=str) + "\n")
        except Exception as e:
            log.debug("canonical PM publish failed: %s", e)
```

**new_str:**
```python
        try:
            DECISION_LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with self._canonical_lock:
                tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(decision_payload, f, indent=2, default=str)
                tmp.replace(DECISION_LIVE_PATH)
                with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(decision_payload, default=str) + "\n")

            # === Fase 2: notify Telegram after canonical write succeeds ===
            # All PM events (SHIELD, REGIME_FLIP, TP1_HIT, SL_HIT, etc.)
            # route through here. One call = all PM events notify Barbara.
            try:
                from live import telegram_notifier as tg
                tg.notify_decision()
            except Exception as _tg_err:
                log.debug("telegram notify after PM_EVENT failed: %s", _tg_err)

        except Exception as e:
            log.debug("canonical PM publish failed: %s", e)
```

---

## MUDANÇA 8 — `position_monitor.py` — Remove direct _send_async from T3 exit

**Context:** T3 defense exit chama `tg._send_async()` directamente (linha 1442). Com MUDANÇA 7, isto passa a ser redundante — o T3 event já vai ser notificado via canonical flow. **Remover a chamada direta para evitar notificação duplicada.**

**Location:** Linhas ~1440-1451

**old_str (copiar exactamente):**
```python
                try:
                    from live import telegram_notifier as tg
                    tg._send_async(
                        f"\U0001F6A8 <b>T3 DEFENSE EXIT — {direction}</b>\n"
                        f"Anomaly: {defense_tier}\n"
                        f"Adverse: {adverse_move:.1f}pts in {T3_WINDOW_S}s\n"
                        f"Level: {broken_level}\n"
                        f"PnL: {current_pnl:+.1f}pts {'(profit)' if in_profit else '(loss)'}\n"
                        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
                    )
                except Exception:
                    pass
```

**new_str:**
```python
                # NOTE (Fase 2): Direct tg._send_async removed.
                # T3 exit now notified via canonical PM_EVENT flow
                # (see _publish_canonical_pm_event + tg.notify_decision).
```

---

## VERIFICATION STEPS

Após cada mudança individual:

1. **Confirmar `str_replace` teve sucesso** (line count mudou? novos bytes correctos?)
2. **Mostrar diff do antes/depois** no report

Após TODAS as mudanças:

3. **py_compile** de cada ficheiro:
```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$files = @(
    "$staging_dir\live\event_processor.py",
    "$staging_dir\live\telegram_notifier.py",
    "$staging_dir\live\position_monitor.py"
)
foreach ($f in $files) {
    $result = & $py -m py_compile $f 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK    $f"
    } else {
        Write-Host "  FAIL  $f"
        Write-Host "  $result"
        throw "py_compile failed for $f"
    }
}
```

Se QUALQUER py_compile falhar: **ABORT, report failure, não prosseguir**.

4. **Hash comparison com pre-fix:**
```powershell
# Hashes NOVOS devem ser DIFERENTES dos pre-fix
$pre_hashes = @{
    "event_processor.py"   = "C48157668BAF47668E61DB460A27BDEE"
    "telegram_notifier.py" = "4893A895DD5E5EB45B91FF09F0B9A55F"
    "position_monitor.py"  = "91DC4B608B9FD231FE2B9DD0B4BE080A"
}
foreach ($filename in $pre_hashes.Keys) {
    $new_hash = (Get-FileHash "$staging_dir\live\$filename" -Algorithm MD5).Hash
    $diff = if ($new_hash -ne $pre_hashes[$filename]) { "CHANGED (good)" } else { "UNCHANGED (PROBLEM)" }
    Write-Host "  $filename : pre=$($pre_hashes[$filename])  new=$new_hash  $diff"
}
```

Se algum ficheiro não mudou, significa str_replace falhou silenciosamente. **ABORT.**

---

## OUTPUT ESPERADO — Report

Gerar `C:\FluxQuantumAI\FASE_2_SCOPE_A_REPORT_<timestamp>.md` com:

```markdown
# FASE 2 SCOPE A — Report

**Timestamp:** <UTC>
**Duration:** <minutes>
**Status:** ✅ SUCCESS / ❌ FAILED

## Staging location
`C:\FluxQuantumAI\deploy-staging-scope-a-<timestamp>\live\`

## Mudanças executadas

### Mudança 1 — event_processor.py GO notification
- Status: ✅/❌
- Lines affected: ~2361-2370
- str_replace old_str found: YES/NO
- Diff context (5 lines before + 10 after)

### Mudança 2 — event_processor.py EXECUTED
- Status: ✅/❌
- Lines affected: ~2509-2513
- str_replace old_str found: YES/NO

### Mudança 3 — event_processor.py EXEC_FAILED
- Status: ✅/❌
- Lines affected: ~2581-2584

### Mudança 4 — event_processor.py GAMMA decoupling
- Status: ✅/❌ (ou PARADO — reportar razão)
- Pattern matches ALPHA? YES/NO
- Applied? YES/NO

### Mudança 5 — event_processor.py DELTA decoupling
- Status: ✅/❌ (ou PARADO)

### Mudança 6.1 — telegram_notifier.py _last_execution_id
- Status: ✅/❌

### Mudança 6.2 — telegram_notifier.py GO/BLOCK branches
- Status: ✅/❌

### Mudança 6.3 — telegram_notifier.py PM_EVENT icon map
- Status: ✅/❌

### Mudança 6.4 — telegram_notifier.py notify_execution()
- Status: ✅/❌

### Mudança 7 — position_monitor.py notify_decision in canonical
- Status: ✅/❌

### Mudança 8 — position_monitor.py remove direct _send_async
- Status: ✅/❌

## py_compile results

| File | Status |
|---|---|
| event_processor.py | ✅ |
| telegram_notifier.py | ✅ |
| position_monitor.py | ✅ |

## Hash changes

| File | Pre-fix MD5 | Post-fix MD5 | Changed |
|---|---|---|---|
| event_processor.py | ... | ... | YES ✅ |
| telegram_notifier.py | ... | ... | YES ✅ |
| position_monitor.py | ... | ... | YES ✅ |

## Next phase

**PARAR.** Aguardar Barbara + Claude audit antes de Fase 3 (Deploy).

## Files for Claude audit

ClaudeCode deve incluir no final do report o conteúdo actual (após mudanças) das seguintes regiões:

1. `event_processor.py` linhas 2355-2380 (mudanças 1-2)
2. `event_processor.py` linhas 2565-2590 (mudança 3)
3. `event_processor.py` linhas próximas a 3623 (mudança 4 GAMMA)
4. `event_processor.py` linhas próximas a 3920 (mudança 5 DELTA)
5. `telegram_notifier.py` linhas 1-20 (mudança 6.1)
6. `telegram_notifier.py` linhas 120-220 (mudanças 6.2 + 6.3)
7. `telegram_notifier.py` linhas com notify_execution() (mudança 6.4)
8. `position_monitor.py` linhas 1430-1460 (mudança 8)
9. `position_monitor.py` linhas 2015-2040 (mudança 7)
```

---

## PROIBIDO NESTA FASE

- ❌ Deploy (mover staging → live)
- ❌ Restart serviços
- ❌ Modificar ficheiros que não sejam os 3 listados
- ❌ Scope B (MT5 history, news exit, L2 DANGER emit, PULLBACK_START)
- ❌ Novos ficheiros
- ❌ Rewrites completos de funções — usar str_replace cirúrgico
- ❌ Pular py_compile
- ❌ Inventar old_str — se não encontrar no ficheiro actual, PARAR e reportar

---

## COMUNICAÇÃO FINAL

Quando completo:

```
FASE 2 SCOPE A — <STATUS>
Staging: <path>
Mudanças: X/8 completas
py_compile: 3/3 OK
Hashes: 3/3 changed
Report: <path>

Aguardando Claude audit + Barbara aprovação antes de Fase 3 (Deploy).
```

**Não avançar para deploy sem autorização explícita.**

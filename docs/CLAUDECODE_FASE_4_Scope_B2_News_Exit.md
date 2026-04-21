# TASK: FASE 4 — Scope B.2 News Exit Integration

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Design doc:** `DESIGN_DOC_Telegram_PositionEvents_v1.md` (Scope B.2)
**Escopo:** News exit notification + cooldown configurável
**Tempo estimado:** 3-4 horas
**Output:** `FASE_4_SCOPE_B2_REPORT_<timestamp>.md`

**PRÉ-REQUISITOS:**
- Fase 1 backup ✅
- Fase 2 Scope A implementado ✅
- Fase 3 Scope B.1 implementado ✅
- Staging: `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\`

---

## CRITICAL RULES

1. **APENAS Scope B.2.** Scope B.3 (L2 DANGER emit) e B.4 (PULLBACK_START) são **PROIBIDOS** nesta fase.
2. **Trabalhar em staging**, não live. Deploy na Fase 8.
3. **Modificar 3 ficheiros:** `event_processor.py`, `position_monitor.py`, `config/settings.json`.
4. **py_compile OBRIGATÓRIO** após modificações.
5. **Hashes devem mudar** empiricamente.
6. **NÃO parar serviços.**
7. **Anti-hallucination:** `str_replace` exacto. Se `old_str` não for encontrado, PARAR e reportar.

---

## CONTEXTO CRÍTICO

O sistema **já tem**:
- `_news_gate.check()` em 4 callsites (linhas 2454, 3611, 3909, 4104)
- `_close_all_apex_positions(reason)` em `event_processor.py` linha 1232
- Chama `_close_all_apex_positions` quando `_ng.exit_all=True`

O que **falta**:

1. **Notificação Telegram** quando news exit fecha posições (actualmente fecha silenciosamente, Barbara não sabe)
2. **Integração com position_monitor** para emit `PM_EVENT NEWS_EXIT` via canonical flow (que notifica Telegram)
3. **Parâmetros configuráveis em settings.json** (30min antes/depois são hardcoded actualmente no `apex_news_gate.py`)
4. **Cooldown pós-news** (window configurável para reabrir entries)

## DECISÃO DE ARQUITECTURA

**Event source:** `event_processor.py` já faz o trabalho de decidir fechar. Não precisamos C2 (service_state signalling) completamente — podemos simplificar.

**Approach aprovado (mais simples que design doc original):**
- `_close_all_apex_positions()` emit PM_EVENT `NEWS_EXIT` via canonical flow
- Este emit dispara Telegram automaticamente (Fase 2 M7)
- Settings.json adiciona parâmetros configuráveis
- Barbara edita settings.json para ajustar windows sem recompilar

**Nota:** Scope B.2 original propunha `service_state.news_exit_alert` para cross-file signalling. **Simplificámos** porque `event_processor.py` já tem acesso directo aos executors e sabe quando fechar. Não precisa passar por ficheiro intermédio.

---

## FILES MODIFIED

1. `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\config\settings.json` (add news_exit section)
2. `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\event_processor.py` (modify `_close_all_apex_positions`)
3. **NOT modify position_monitor.py nesta fase** (event vem do event_processor via canonical flow)

---

## MUDANÇA 1 — Config parameters in settings.json

### 1.1 — Ler settings.json actual

```powershell
$settings_path = "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\config\settings.json"

# Primeiro: confirmar que existe na staging
if (-not (Test-Path $settings_path)) {
    Write-Host "settings.json NOT in staging. Check if copied from live."
    Copy-Item "C:\FluxQuantumAI\config\settings.json" $settings_path
    Write-Host "  Copied from live."
}

Write-Host "=== Current settings.json ==="
Get-Content $settings_path -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 10 |
    Select-Object -First 50
```

### 1.2 — Adicionar secção `news_exit`

**Operação:** JSON merge — adicionar nova key `news_exit` ao root do objecto, **sem modificar outras keys existentes**.

**Método:** Python one-liner para preservar ordem e estrutura:

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"

& $py -c @"
import json, sys
path = r'C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\config\settings.json'
with open(path, 'r', encoding='utf-8') as f:
    s = json.load(f)

# Only add if not already present (idempotent)
if 'news_exit' not in s:
    s['news_exit'] = {
        '_comment': 'Fase 4 Scope B.2 — News exit configurable windows',
        'enabled': True,
        'pre_event_seconds': 1800,
        'post_event_seconds': 1800,
        'block_new_entries': True,
        'telegram_notify': True
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    print('news_exit section added')
else:
    print('news_exit already present, skipping')
"@
```

**Validação:** ler settings.json de volta e confirmar secção existe.

---

## MUDANÇA 2 — `event_processor.py` `_close_all_apex_positions` emit event

**Location:** linhas 1232-1255

**Proposta:** Após o loop de close, emit **um** PM_EVENT agregado para todos os closes feitos.

**old_str (copiar exactamente):**
```python
    def _close_all_apex_positions(self, reason: str = "NEWS_EXIT_ALL") -> None:
        """Close all open APEX positions on all connected accounts (news gate EXIT_ALL)."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.warning("CLOSE_ALL triggered: %s", reason)
        print(f"[{ts}] NEWS EXIT_ALL -- closing all APEX positions: {reason}")

        for label, executor in [("DEMO", self.executor),
                                 ("LIVE", self.executor_live)]:
            if executor is None or not executor.connected:
                continue
            try:
                positions = executor.get_open_positions()
                if not positions:
                    print(f"[{ts}] [{label}] no open positions")
                    continue
                for pos in positions:
                    ticket = pos.get("ticket", 0)
                    result = executor.close_position(ticket)
                    if result.get("success"):
                        print(f"[{ts}] [{label}] closed ticket={ticket} pnl={result.get('pnl', 0):.2f}")
                    else:
                        print(f"[{ts}] [{label}] close FAILED ticket={ticket}: {result.get('error')}")
            except Exception as _e:
                log.error("_close_all_apex_positions [%s]: %s", label, _e)
```

**new_str:**
```python
    def _close_all_apex_positions(self, reason: str = "NEWS_EXIT_ALL") -> None:
        """Close all open APEX positions on all connected accounts (news gate EXIT_ALL).

        Fase 4 Scope B.2: Emits PM_EVENT NEWS_EXIT via canonical flow
        which triggers Telegram notification (Fase 2 Mudança 7).
        """
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.warning("CLOSE_ALL triggered: %s", reason)
        print(f"[{ts}] NEWS EXIT_ALL -- closing all APEX positions: {reason}")

        # Track close outcomes for aggregate event
        _total_positions_found = 0
        _total_closed_ok = 0
        _total_closed_fail = 0
        _total_pnl = 0.0
        _broker_summary = []

        for label, executor in [("DEMO", self.executor),
                                 ("LIVE", self.executor_live)]:
            if executor is None or not executor.connected:
                continue
            try:
                positions = executor.get_open_positions()
                if not positions:
                    print(f"[{ts}] [{label}] no open positions")
                    continue
                _total_positions_found += len(positions)
                for pos in positions:
                    ticket = pos.get("ticket", 0)
                    result = executor.close_position(ticket)
                    if result.get("success"):
                        pnl = result.get("pnl", 0)
                        _total_pnl += pnl
                        _total_closed_ok += 1
                        print(f"[{ts}] [{label}] closed ticket={ticket} pnl={pnl:.2f}")
                    else:
                        _total_closed_fail += 1
                        print(f"[{ts}] [{label}] close FAILED ticket={ticket}: {result.get('error')}")
                _broker_summary.append(f"{label}={len(positions)}")
            except Exception as _e:
                log.error("_close_all_apex_positions [%s]: %s", label, _e)

        # === Fase 4 Scope B.2: Emit PM_EVENT NEWS_EXIT via canonical flow ===
        # Triggers Telegram notification with 📰 NEWS_EXIT icon (Fase 2 M6.3 map).
        if _total_positions_found > 0:
            try:
                from live.position_monitor import PositionMonitor
                # Canonical payload — position_monitor uses same format
                _canonical_payload = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event_source": "EVENT_PROCESSOR",
                    "position_event": {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event_source": "EVENT_PROCESSOR",
                        "event_type": "NEWS_EXIT",
                        "action_type": "CLOSE_ALL",
                        "direction_affected": "BOTH",
                        "dry_run": False,
                        "execution_state": "EXECUTED" if _total_closed_fail == 0 else "PARTIAL",
                        "attempted": True,
                        "broker": " + ".join(_broker_summary) if _broker_summary else "UNKNOWN",
                        "account": None,
                        "reason": reason,
                        "ticket": None,
                        "group": None,
                        "result": f"closed={_total_closed_ok}/{_total_positions_found} pnl={_total_pnl:+.2f}",
                    },
                    "decision": {
                        "action": "PM_EVENT",
                        "direction": "BOTH",
                        "action_side": "CLOSE_ALL",
                        "trade_intent": "EXIT_ALL",
                        "message_semantics_version": "v1_canonical",
                        "reason": reason,
                        "execution": {
                            "overall_state": "EXECUTED" if _total_closed_fail == 0 else "PARTIAL",
                            "attempted": True,
                            "brokers": [],
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    },
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "decision_id": str(uuid.uuid4())[:8],
                }

                # Write canonical decision_live.json
                import json as _json_news
                DECISION_LIVE_PATH = Path("C:/FluxQuantumAI/logs/decision_live.json")
                DECISION_LOG_PATH = Path("C:/FluxQuantumAI/logs/decision_log.jsonl")
                tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    _json_news.dump(_canonical_payload, f, indent=2, default=str)
                tmp.replace(DECISION_LIVE_PATH)
                with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(_json_news.dumps(_canonical_payload, default=str) + "\n")

                # Notify Telegram
                tg.notify_decision()
                log.info("NEWS_EXIT PM_EVENT emitted: %d closed, %.2f pnl",
                         _total_closed_ok, _total_pnl)
            except Exception as _event_err:
                log.warning("NEWS_EXIT event emission failed: %s", _event_err)
```

**Nota sobre a implementação:**
- Usamos `uuid.uuid4()` — verificar se já está importado no topo do ficheiro. Se não, ClaudeCode adiciona `import uuid` aos imports.
- `DECISION_LIVE_PATH` e `DECISION_LOG_PATH` podem já existir como constantes no ficheiro — ClaudeCode verifica e reutiliza se existir.

---

## MUDANÇA 3 — Verify uuid import in event_processor.py

**ClaudeCode:** procurar `import uuid` nos imports do ficheiro. Se não existir, adicionar.

```powershell
grep -n "^import uuid\|^from uuid" /mnt/user-data/uploads/event_processor.py | head -3
```

Se count=0, adicionar `import uuid` na secção de imports (próximo de `import json`, `import threading`, etc.).

Se count>0, skip.

**Report exactamente o que foi feito.**

---

## MUDANÇA 4 — Check if DECISION_LIVE_PATH / DECISION_LOG_PATH constants exist

**ClaudeCode:** procurar:

```powershell
grep -n "DECISION_LIVE_PATH\|DECISION_LOG_PATH" /mnt/user-data/uploads/event_processor.py | head -5
```

**Se já existem como constantes:** modificar `new_str` acima para **reutilizar** em vez de redefinir localmente.

**Se não existem:** deixar redefinição local (como proposto).

---

## VERIFICATION STEPS

### Step 1 — settings.json validation

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -c @"
import json
path = r'C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\config\settings.json'
with open(path) as f:
    s = json.load(f)
assert 'news_exit' in s, 'news_exit section missing'
ne = s['news_exit']
assert ne['enabled'] is True
assert ne['pre_event_seconds'] == 1800
assert ne['post_event_seconds'] == 1800
print('settings.json news_exit validated OK')
"@
```

### Step 2 — py_compile event_processor.py

```powershell
$result = & $py -m py_compile "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\event_processor.py" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK  event_processor.py compiled"
} else {
    Write-Host "  FAIL  $result"
    throw "py_compile failed"
}
```

### Step 3 — Hash comparison with Fase 2/3

```powershell
$fase2_ep_hash = "2BF2CDAA8B585FF1B43AD2C600C27BDC"  # from Fase 2
$new_ep_hash = (Get-FileHash "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\event_processor.py" -Algorithm MD5).Hash
if ($new_ep_hash -ne $fase2_ep_hash) {
    Write-Host "  OK  event_processor.py changed: $fase2_ep_hash -> $new_ep_hash"
} else {
    throw "event_processor.py NOT CHANGED from Fase 2 — str_replace failed"
}
```

---

## OUTPUT — Report

Criar `C:\FluxQuantumAI\FASE_4_SCOPE_B2_REPORT_<timestamp>.md`:

```markdown
# FASE 4 SCOPE B.2 — Report

**Timestamp:** <UTC>
**Duration:** <minutes>
**Status:** ✅ SUCCESS / ❌ FAILED

## Staging location
`C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\`

## Mudanças executadas

### M1 — settings.json: news_exit section added
- Status: ✅/❌
- File path: <settings.json path>
- Section added? YES/NO
- Content: (paste news_exit section)

### M2 — event_processor.py: _close_all_apex_positions emits PM_EVENT
- Status: ✅/❌
- old_str found: YES/NO
- Lines affected: ~1232-1255 → expanded
- uuid import required: YES/NO
- uuid import action taken: ADDED at line X / ALREADY PRESENT
- DECISION_*_PATH constants exist: YES/NO
- DECISION_*_PATH action: REUSED / DEFINED_LOCALLY

### Context of new code
(paste new _close_all_apex_positions function)

## py_compile

| File | Status |
|---|---|
| event_processor.py | ✅ |

## Hash verification

| File | Pre-Fase4 MD5 | Post-Fase4 MD5 | Change |
|---|---|---|---|
| event_processor.py | 2BF2CDAA8B585FF1B43AD2C600C27BDC | <new> | YES ✅ |
| settings.json | <pre> | <new> | YES ✅ |

## Files for Claude audit

1. Full content of modified `_close_all_apex_positions` function
2. news_exit section from settings.json

## Next phase

**PARAR.** Aguardar Claude audit + Barbara aprovação antes de Fase 5 (Scope B.3+B.4).
```

---

## PROIBIDO NESTA FASE

- ❌ Tocar em LIVE (apenas staging)
- ❌ Parar serviços
- ❌ Scope B.3 (L2 DANGER emit) ou B.4 (PULLBACK_START)
- ❌ Modificar position_monitor.py nesta fase
- ❌ Modificar telegram_notifier.py (já feito em Fase 2)
- ❌ Deploy (Fase 8)
- ❌ Modificar apex_news_gate.py (out of scope — manter config existente)

---

## COMUNICAÇÃO FINAL

```
FASE 4 SCOPE B.2 — <STATUS>
Staging: <path>
Ficheiros modificados: 2 (event_processor.py + settings.json)
py_compile: 1/1 OK
Hashes: 2/2 changed
Report: <path>

Aguardando Claude audit + Barbara aprovação antes de Fase 5.
```

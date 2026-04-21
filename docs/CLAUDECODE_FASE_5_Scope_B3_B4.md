# TASK: FASE 5 — Scope B.3 (L2 DANGER emit) + Scope B.4 (Hedge Lifecycle Events)

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Design doc:** `DESIGN_DOC_Telegram_PositionEvents_v1.md` (Scope B.3 + B.4)
**Escopo:** L2 DANGER event emission + PULLBACK_START/PULLBACK_END/ESCALATION events no hedge_manager
**Tempo estimado:** 2-3 horas
**Output:** `FASE_5_SCOPE_B3_B4_REPORT_<timestamp>.md`

**PRÉ-REQUISITOS:**
- Fase 1 backup ✅
- Fase 2 Scope A implementado ✅
- Fase 3 Scope B.1 implementado ✅
- Fase 4 Scope B.2 implementado ✅
- Staging: `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\`

---

## CRITICAL RULES

1. **APENAS Scope B.3 + B.4.** Nada mais.
2. **Trabalhar em staging**, não live. Deploy na Fase 7 (separada).
3. **Modificar 2 ficheiros:** `position_monitor.py` + `hedge_manager.py`.
4. **Copiar `hedge_manager.py` da live para staging primeiro** — staging Fase 2 só copiou event_processor/telegram_notifier/position_monitor.
5. **py_compile OBRIGATÓRIO** em ambos os ficheiros modificados.
6. **Hashes devem mudar** empiricamente.
7. **NÃO parar serviços.**
8. **str_replace cirúrgico, sem rewrites completos.**

---

## CONTEXTO

### Scope B.3 — L2 DANGER silent exit

O `position_monitor._check_l2_danger()` fecha Leg2+Leg3 quando há DANGER_BARS consecutivos com danger_score acima do threshold. **Actualmente fecha silenciosamente** — não emite PM_EVENT, Barbara não sabe que houve L2 danger exit.

**Ver `position_monitor.py` linhas ~896-930.**

### Scope B.4 — Hedge lifecycle events silent

O `HedgeManager.process()` gere pullback hedge:
- `HEDGE_OPENED` → significa **PULLBACK_START** (pullback detected, hedge opened)
- `HEDGE_CLOSED_TREND_RESUMED` → significa **PULLBACK_END** (trend retomou, hedge fechado em profit)
- `HEDGE_CLOSED_ESCALATION` → significa **HEDGE_ESCALATION** (pullback virou regime shift, pior cenário)

Todos os 3 eventos **escrevem em `hedge_events.log`** via `_write_log()` mas **não notificam Telegram**. Barbara não sabe quando hedge abre/fecha.

**V3 Agent disabled NÃO bloqueia hedge** — `HedgeManager.process()` é chamado sempre pelo position_monitor (linha 595). Hedge dispara baseado em condições de pullback (delta_4h, pullback_dist, atr), não em V3.

### Design decision (approach Opção C)

Em vez de callback do HedgeManager para PositionMonitor, **expandir `_write_log`** para também emitir canonical PM_EVENT. Razão:
- Todos os 3 eventos já passam por `_write_log` → single integration point
- Mais localizado, menos refactor
- Consistente com padrão Fase 4 (evento emitido no ponto onde acontece)

---

## FILES MODIFIED

1. `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\position_monitor.py` (modify)
2. `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\hedge_manager.py` (**COPY from live FIRST, then modify**)

**Zero outros ficheiros.**

---

## PASSO PRELIMINAR — Copy hedge_manager.py to staging

```powershell
$staging_live = "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live"
$live_hedge = "C:\FluxQuantumAI\live\hedge_manager.py"
$staging_hedge = "$staging_live\hedge_manager.py"

# Check if already in staging (shouldn't be — staging only had 3 files)
if (Test-Path $staging_hedge) {
    Write-Host "WARNING: hedge_manager.py already in staging"
    $existing_hash = (Get-FileHash $staging_hedge -Algorithm MD5).Hash
    $live_hash = (Get-FileHash $live_hedge -Algorithm MD5).Hash
    Write-Host "  staging hash: $existing_hash"
    Write-Host "  live hash:    $live_hash"
} else {
    Copy-Item $live_hedge $staging_hedge
    Write-Host "Copied hedge_manager.py from live to staging"
}

# Pre-fix hash (for later comparison)
$pre_fix_hash = (Get-FileHash $staging_hedge -Algorithm MD5).Hash
Write-Host "Pre-fix hedge_manager.py MD5: $pre_fix_hash"
```

**Report esse pre-fix hash no relatório final.**

---

## MUDANÇA 1 — `position_monitor.py` L2 DANGER emit event

### 1.1 — Ler função `_check_l2_danger` actual

**ClaudeCode:** primeiro ler linhas ~896-930 para confirmar estrutura actual.

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -c @"
with open(r'C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\position_monitor.py') as f:
    lines = f.readlines()
for i in range(895, 935):
    print(f'{i+1:5d}: {lines[i].rstrip()}')
"@
```

### 1.2 — str_replace para adicionar `_emit_position_event`

**Target:** adicionar emit ANTES dos closes (linha ~900 no ficheiro actual da staging).

**Location:** função `_check_l2_danger`, onde começa o close process após `DANGER_BARS consecutive`.

**ClaudeCode deve identificar old_str exacto. Proposta baseada no código Fase 3 staging:**

**old_str (CONFIRMAR com leitura antes de aplicar):**
```python
        state["danger_streak"] = DANGER_BARS
        ts = _ts()
        log.warning("L2 DANGER EXIT: %d consecutive danger bars (scores=%s) for %s",
                    DANGER_BARS, scores, direction)
        print(f"[{ts}] L2 DANGER: {DANGER_BARS} consecutive danger bars {scores} -- closing Leg2+Leg3")

        if trade_rec is None:
            # No record: close this position by ticket
            self._close_ticket(pos["ticket"], "L2_DANGER", ts)
            return
```

**new_str:**
```python
        state["danger_streak"] = DANGER_BARS
        ts = _ts()
        log.warning("L2 DANGER EXIT: %d consecutive danger bars (scores=%s) for %s",
                    DANGER_BARS, scores, direction)
        print(f"[{ts}] L2 DANGER: {DANGER_BARS} consecutive danger bars {scores} -- closing Leg2+Leg3")

        # === Fase 5 Scope B.3 — Emit L2_DANGER position event ===
        # Triggers Telegram notification ⚠️ L2_DANGER via Fase 2 M7 canonical flow.
        self._emit_position_event(
            event_type="L2_DANGER",
            direction=direction,
            ticket=pos.get("ticket"),
            reason=f"{DANGER_BARS} consecutive danger bars | scores={[round(s, 2) for s in scores[-DANGER_BARS:]]}",
            action_taken="CLOSE_LEG2_LEG3",
            result="PENDING",
            attempted=True,
            execution_state="ATTEMPTED",
        )

        if trade_rec is None:
            # No record: close this position by ticket
            self._close_ticket(pos["ticket"], "L2_DANGER", ts)
            return
```

**Notes:**
- `scores[-DANGER_BARS:]` pega os últimos DANGER_BARS scores (os que triggaram)
- Round para 2 decimals para legibilidade no Telegram
- `result="PENDING"` porque o close ainda vai acontecer a seguir (via `_close_ticket`)

**Verificação anti-hallucination:** ClaudeCode DEVE ler exactamente estas linhas no ficheiro staging antes de aplicar str_replace. Se o texto real divergir do spec (ex: formatação diferente, indent diferente), **usar texto real** como old_str.

---

## MUDANÇA 2 — `hedge_manager.py` — Emit PM_EVENT via canonical flow

### 2.1 — Estratégia de integração

**Approach Opção C (aprovada):** expandir `_write_log` para também emitir PM_EVENT canonical (decision_live.json + decision_log.jsonl + tg.notify_decision()).

**Razão:** os 3 eventos (HEDGE_OPENED, HEDGE_CLOSED_TREND_RESUMED, HEDGE_CLOSED_ESCALATION) já passam por `_write_log` — single integration point.

### 2.2 — Imports a adicionar ao topo de hedge_manager.py

**Location:** após o bloco de imports existente (linha ~38).

**old_str:**
```python
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
```

**new_str:**
```python
from __future__ import annotations

import logging
import threading
import json   # Fase 5 Scope B.4 — canonical PM_EVENT emission
import uuid   # Fase 5 Scope B.4 — decision_id generation
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
```

### 2.3 — Canonical paths constants

**Location:** após `HEDGE_LOG_PATH` constant (linha ~60).

**old_str:**
```python
HEDGE_LOG_PATH      = Path("C:/FluxQuantumAI/logs/hedge_events.log")
```

**new_str:**
```python
HEDGE_LOG_PATH      = Path("C:/FluxQuantumAI/logs/hedge_events.log")

# Fase 5 Scope B.4 — canonical PM_EVENT flow paths
DECISION_LIVE_PATH = Path("C:/FluxQuantumAI/logs/decision_live.json")
DECISION_LOG_PATH  = Path("C:/FluxQuantumAI/logs/decision_log.jsonl")

# Map hedge event type to PM_EVENT semantic type
HEDGE_EVENT_TO_PM_EVENT = {
    "HEDGE_OPENED":              "PULLBACK_START",
    "HEDGE_CLOSED_TREND_RESUMED": "PULLBACK_END_EXIT",
    "HEDGE_CLOSED_ESCALATION":   "HEDGE_ESCALATION",
}
```

### 2.4 — Expand `_write_log` to emit canonical PM_EVENT

**Location:** função `_write_log()` no fim do ficheiro (linhas ~400-417).

**old_str:**
```python
    def _write_log(
        self,
        event: str,
        main_ticket: int,
        hedge_dir: str,
        price: float,
        sl_or_pnl: float,
        hedge_ticket: str,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        line = (f"{ts}\t{event}\tmain={main_ticket}\t"
                f"hedge={hedge_ticket}\tdir={hedge_dir}\t"
                f"price={price:.2f}\tsl_pnl={sl_or_pnl:.2f}\n")
        try:
            self._log_fh.write(line)
            self._log_fh.flush()
        except Exception as e:
            log.warning("hedge log write failed: %s", e)
```

**new_str:**
```python
    def _write_log(
        self,
        event: str,
        main_ticket: int,
        hedge_dir: str,
        price: float,
        sl_or_pnl: float,
        hedge_ticket: str,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        line = (f"{ts}\t{event}\tmain={main_ticket}\t"
                f"hedge={hedge_ticket}\tdir={hedge_dir}\t"
                f"price={price:.2f}\tsl_pnl={sl_or_pnl:.2f}\n")
        try:
            self._log_fh.write(line)
            self._log_fh.flush()
        except Exception as e:
            log.warning("hedge log write failed: %s", e)

        # === Fase 5 Scope B.4 — Emit canonical PM_EVENT + Telegram notify ===
        # Triggers Telegram via Fase 2 M7 canonical flow.
        pm_event_type = HEDGE_EVENT_TO_PM_EVENT.get(event)
        if pm_event_type is None:
            return  # not a tracked event (e.g. dry_run placeholder)

        try:
            _now_iso = datetime.now(timezone.utc).isoformat()

            # Determine execution state + reason string
            if event == "HEDGE_OPENED":
                exec_state = "EXECUTED" if hedge_ticket not in ("DRY_RUN", "-1") else "DRY_RUN"
                reason_str = f"pullback detected | hedge {hedge_dir} @ {price:.2f}"
                action_type = "OPEN_HEDGE"
                result_str = f"hedge_ticket={hedge_ticket} sl={sl_or_pnl:.2f}"
            else:  # HEDGE_CLOSED_*
                exec_state = "EXECUTED" if hedge_ticket not in ("DRY_RUN", "-1") else "DRY_RUN"
                reason_str = f"{pm_event_type} | hedge {hedge_dir} pnl={sl_or_pnl:+.2f}"
                action_type = "CLOSE_HEDGE"
                result_str = f"pnl={sl_or_pnl:+.2f}"

            canonical_payload = {
                "timestamp": _now_iso,
                "event_source": "HEDGE_MANAGER",
                "position_event": {
                    "timestamp": _now_iso,
                    "event_source": "HEDGE_MANAGER",
                    "event_type": pm_event_type,
                    "action_type": action_type,
                    "direction_affected": hedge_dir,
                    "dry_run": self.dry_run,
                    "execution_state": exec_state,
                    "attempted": True,
                    "broker": "UNKNOWN",
                    "account": None,
                    "reason": reason_str,
                    "ticket": int(hedge_ticket) if hedge_ticket.lstrip("-").isdigit() else None,
                    "group": f"main_ticket={main_ticket}",
                    "result": result_str,
                },
                "decision": {
                    "action": "PM_EVENT",
                    "direction": hedge_dir,
                    "action_side": action_type,
                    "trade_intent": "HEDGE",
                    "message_semantics_version": "v1_canonical",
                    "reason": reason_str,
                    "execution": {
                        "overall_state": exec_state,
                        "attempted": True,
                        "brokers": [],
                        "updated_at": _now_iso,
                    },
                },
                "created_at": _now_iso,
                "decision_id": str(uuid.uuid4())[:8],
            }

            # Atomic write decision_live.json
            DECISION_LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _tmp = DECISION_LIVE_PATH.with_suffix(".tmp")
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(canonical_payload, f, indent=2, default=str)
            _tmp.replace(DECISION_LIVE_PATH)
            with open(DECISION_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(canonical_payload, default=str) + "\n")

            # Notify Telegram
            try:
                from live import telegram_notifier as tg
                tg.notify_decision()
                log.info("Hedge PM_EVENT emitted: %s (hedge_ticket=%s)", pm_event_type, hedge_ticket)
            except Exception as _tg_err:
                log.debug("Telegram notify after hedge event failed: %s", _tg_err)

        except Exception as _pm_err:
            log.warning("Hedge PM_EVENT emission failed (%s): %s", event, _pm_err)
```

**Notes:**
- Icon map no `telegram_notifier` (Fase 2 M6.3) já tem:
  - `PULLBACK_START`: ↩
  - `PULLBACK_END_EXIT`: ↪
  - `L2_DANGER`: ⚠
- **HEDGE_ESCALATION não está no icon map** — vai usar icon default 🛠. Aceitável mas não ideal. **Adicionar ao icon map em fase futura** se Barbara quiser.

---

## VERIFICATION STEPS

### Step 1 — py_compile both files

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$files = @(
    "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\position_monitor.py",
    "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\hedge_manager.py"
)
foreach ($f in $files) {
    $result = & $py -m py_compile $f 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK    $f"
    } else {
        Write-Host "  FAIL  $f"
        Write-Host "  $result"
        throw "py_compile failed"
    }
}
```

### Step 2 — Hash verification

```powershell
# position_monitor pre-Fase5 (pós-Fase3): 1D417D08AA1272662FE913F0B9FB5102
$pm_fase3_hash = "1D417D08AA1272662FE913F0B9FB5102"
$pm_new_hash = (Get-FileHash "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\position_monitor.py" -Algorithm MD5).Hash
if ($pm_new_hash -ne $pm_fase3_hash) {
    Write-Host "  OK  position_monitor.py: $pm_fase3_hash -> $pm_new_hash"
} else {
    throw "position_monitor.py NOT CHANGED"
}

# hedge_manager pre-fix was just measured; confirm it changed
$hm_new_hash = (Get-FileHash "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\hedge_manager.py" -Algorithm MD5).Hash
if ($hm_new_hash -ne $pre_fix_hash) {
    Write-Host "  OK  hedge_manager.py: $pre_fix_hash -> $hm_new_hash"
} else {
    throw "hedge_manager.py NOT CHANGED"
}
```

### Step 3 — Validate key structures

```powershell
# Check HEDGE_EVENT_TO_PM_EVENT map exists
$content = Get-Content "$staging_live\hedge_manager.py" -Raw
if ($content -match "HEDGE_EVENT_TO_PM_EVENT") {
    Write-Host "  OK  HEDGE_EVENT_TO_PM_EVENT map present"
} else {
    throw "HEDGE_EVENT_TO_PM_EVENT map NOT found"
}

# Check _emit_position_event call added to _check_l2_danger
$pm_content = Get-Content "$staging_live\position_monitor.py" -Raw
if ($pm_content -match 'event_type="L2_DANGER"') {
    Write-Host "  OK  L2_DANGER emit present in position_monitor"
} else {
    throw "L2_DANGER emit NOT found"
}
```

---

## OUTPUT — Report

Criar `C:\FluxQuantumAI\FASE_5_SCOPE_B3_B4_REPORT_<timestamp>.md`:

```markdown
# FASE 5 SCOPE B.3 + B.4 — Report

**Timestamp:** <UTC>
**Duration:** <minutes>
**Status:** ✅ SUCCESS / ❌ FAILED

## Staging location
`C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\`

## Mudanças executadas

### Passo preliminar — Copy hedge_manager.py to staging
- Status: ✅/❌
- Pre-fix hash (from live): <MD5>
- Copied to: staging\live\hedge_manager.py

### B.3 — position_monitor.py: L2_DANGER emit event
- Status: ✅/❌
- Function: `_check_l2_danger`
- Lines affected: ~900-920
- str_replace old_str found: YES/NO
- Context (linhas antes/depois):

```python
<paste actual context>
```

### B.4 M1 — hedge_manager.py: imports (json, uuid)
- Status: ✅/❌
- Location: lines ~33-40

### B.4 M2 — hedge_manager.py: DECISION_*_PATH + HEDGE_EVENT_TO_PM_EVENT
- Status: ✅/❌
- Location: after HEDGE_LOG_PATH (~line 60)

### B.4 M3 — hedge_manager.py: `_write_log` expanded
- Status: ✅/❌
- Function: `_write_log`
- Lines affected: ~400-480 (expanded from 18 to ~85 lines)
- old_str found: YES/NO
- Context:

```python
<paste start + end of new function>
```

## py_compile

| File | Status |
|---|---|
| position_monitor.py | ✅ |
| hedge_manager.py | ✅ |

## Hash verification

| File | Pre-Fase5 MD5 | Post-Fase5 MD5 | Change |
|---|---|---|---|
| position_monitor.py | 1D417D08AA1272662FE913F0B9FB5102 | <new> | YES ✅ |
| hedge_manager.py | <pre_fix> | <new> | YES ✅ |

## Files for Claude audit

1. Snippet L2_DANGER emit em position_monitor (linhas completas)
2. Conteúdo completo de `_write_log` expandido em hedge_manager
3. Imports adicionados em hedge_manager
4. Constants map em hedge_manager

## Next phase

**PARAR.** Aguardar Claude audit + Barbara aprovação antes de Fase 6 (Audit Agregado) ou Fase 7 (Deploy).
```

---

## PROIBIDO NESTA FASE

- ❌ Tocar em LIVE (só staging)
- ❌ Parar serviços
- ❌ Modificar outros ficheiros que não `position_monitor.py` e `hedge_manager.py`
- ❌ Deploy
- ❌ Mudanças em comportamento existente (só adicionar emit, não modificar lógica)
- ❌ Scope creep — apenas B.3 e B.4

---

## COMUNICAÇÃO FINAL

```
FASE 5 SCOPE B.3 + B.4 — <STATUS>
Staging: <path>
Ficheiros modificados: 2 (position_monitor.py + hedge_manager.py)
py_compile: 2/2 OK
Hashes: 2/2 changed
Report: <path>

Aguardando Claude audit + Barbara aprovação antes de Fase 6.
```

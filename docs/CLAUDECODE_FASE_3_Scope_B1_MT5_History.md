# TASK: FASE 3 — Scope B.1 MT5 History Watcher Implementation

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Design doc:** `DESIGN_DOC_Telegram_PositionEvents_v1.md` (Barbara 2026-04-17)
**Escopo:** Scope B.1 do design doc — MT5 deal history polling para detectar TP1/TP2/SL/manual closes
**Tempo estimado:** 4-5 horas
**Output:** `FASE_3_SCOPE_B1_REPORT_<timestamp>.md`

**PRÉ-REQUISITOS:**
- Fase 1 backup completo ✅
- Fase 2 Scope A implementado em staging ✅
- Fase 2.5b GAMMA/DELTA investigation ✅
- Staging directory existente: `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\`

---

## CRITICAL RULES

1. **APENAS Scope B.1.** Scope B.2 (news exit), B.3 (L2 DANGER), B.4 (PULLBACK_START) são **PROIBIDOS** nesta fase.
2. **Trabalhar em staging**, não live. Deploy fica para Fase 8.
3. **Novo ficheiro + modificação cirúrgica em position_monitor.py.**
4. **py_compile OBRIGATÓRIO** após cada modificação.
5. **Hashes devem mudar** em ficheiros modificados; novo ficheiro deve existir.
6. **Report detalhado** para Claude auditar.
7. **NÃO parar serviços.** Deploy é outra fase.

---

## OBJECTIVO

Criar sistema de detecção de fechos de posição via MT5 deal history:

- **TP1_HIT:** Leg1 (primeiro ticket do grupo) fecha por TP
- **TP2_HIT:** Leg2 ou Leg3 (Runner) fecha por TP
- **SL_HIT:** qualquer ticket fecha por SL
- **MANUAL_CLOSE:** ticket fechado manualmente (terminal/mobile/web)
- **SYSTEM_CLOSE:** ticket fechado pelo nosso código (skip, evento já emitido)

Attribution TP1/TP2: **lookup por ticket em trades.csv** (Opção X1 aprovada por Barbara).

---

## FILES MODIFIED IN THIS PHASE

1. **NEW:** `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\mt5_history_watcher.py`
2. **MODIFY:** `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\position_monitor.py`

**Apenas estes 2 ficheiros.** Nenhum outro.

---

## MUDANÇA 1 — Criar `mt5_history_watcher.py`

**Location:** `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\mt5_history_watcher.py` (novo ficheiro)

**Conteúdo completo:**

```python
"""
MT5 Deal History Watcher
Detects position closures and classifies them (TP, SL, manual, system).

Called by position_monitor when position count drops.
Uses MetaTrader5.history_deals_get() as ground truth.

Design decision (Barbara 2026-04-18): TP1 vs TP2 attribution via trades.csv lookup
by ticket (Option X1). Ticket is unique identifier — deterministic, not price-based.

Created: Fase 3 Scope B.1
Design doc: DESIGN_DOC_Telegram_PositionEvents_v1.md
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import MetaTrader5 as mt5

log = logging.getLogger(__name__)


class MT5HistoryWatcher:
    """
    Classifies closed positions via MT5 deal history.

    Usage:
        watcher = MT5HistoryWatcher(executor, magic_number)
        # When position monitor detects count drop:
        closed_deals = watcher.find_closed_since_last_check()
        for deal in closed_deals:
            if deal["reason_label"] == "TP_HIT":
                # emit TP event (caller decides TP1 vs TP2 via trades.csv lookup)
    """

    # MT5 deal reason codes (MetaTrader5.DEAL_REASON_*)
    REASON_CLIENT   = 0   # manual close from terminal
    REASON_MOBILE   = 1   # manual close from mobile
    REASON_WEB      = 2   # manual close from web
    REASON_EXPERT   = 3   # closed by EA (our code via close_position)
    REASON_SL       = 4   # stop loss hit
    REASON_TP       = 5   # take profit hit
    REASON_SO       = 6   # stop out (margin call)
    REASON_ROLLOVER = 7   # rollover
    REASON_VMARGIN  = 8   # variation margin
    REASON_SPLIT    = 9   # split

    REASON_LABELS = {
        REASON_CLIENT:   "MANUAL_TERMINAL",
        REASON_MOBILE:   "MANUAL_MOBILE",
        REASON_WEB:      "MANUAL_WEB",
        REASON_EXPERT:   "SYSTEM_CLOSE",
        REASON_SL:       "SL_HIT",
        REASON_TP:       "TP_HIT",
        REASON_SO:       "STOP_OUT",
        REASON_ROLLOVER: "ROLLOVER",
        REASON_VMARGIN:  "VMARGIN",
        REASON_SPLIT:    "SPLIT",
    }

    # Labels that qualify as a "position close" we want to emit events for
    CLOSE_LABELS = {"SL_HIT", "TP_HIT", "MANUAL_TERMINAL", "MANUAL_MOBILE",
                    "MANUAL_WEB", "STOP_OUT"}

    def __init__(self, executor, magic_number: int):
        """
        :param executor: MT5Executor instance (has connected flag)
        :param magic_number: EA magic number to filter our deals
        """
        self.executor = executor
        self.magic = int(magic_number)
        # Initialize to 5 min ago so first check catches recent closes on startup
        self._last_check_ts = datetime.now(timezone.utc) - timedelta(minutes=5)
        log.info("MT5HistoryWatcher initialized: magic=%d, last_check=%s",
                 self.magic, self._last_check_ts.isoformat())

    def find_closed_since_last_check(self) -> list[dict]:
        """
        Return list of deals closed since last call. Advances internal cursor.

        Each deal dict has keys:
          - ticket (int): MT5 deal ticket
          - position_id (int): position identifier (groups legs)
          - symbol (str): trading symbol
          - type (str): "BUY" or "SELL"
          - volume (float): volume in lots
          - price (float): close price
          - profit (float): realized profit
          - reason_code (int): raw MT5 reason code
          - reason_label (str): human-readable label (e.g. "TP_HIT", "SL_HIT")
          - time (datetime UTC): when deal was executed
          - comment (str): deal comment
          - magic (int): EA magic number

        Returns empty list if MT5 disconnected or no new deals.
        """
        if not getattr(self.executor, "connected", False):
            log.debug("MT5 not connected — skipping history check")
            return []

        from_date = self._last_check_ts
        to_date = datetime.now(timezone.utc)

        try:
            deals = mt5.history_deals_get(from_date, to_date)
        except Exception as e:
            log.warning("history_deals_get exception: %s", e)
            return []

        if deals is None:
            last_err = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
            log.debug("history_deals_get returned None (err=%s)", last_err)
            # Still advance cursor to avoid replay on transient errors
            self._last_check_ts = to_date
            return []

        result = []
        for d in deals:
            # Filter: only OUR deals (by magic number)
            try:
                if int(d.magic) != self.magic:
                    continue
                # Filter: only EXIT deals (entry=1 means position close/exit)
                if int(d.entry) != 1:
                    continue
            except Exception:
                continue

            reason_code = int(d.reason)
            reason_label = self.REASON_LABELS.get(reason_code, f"UNKNOWN_{reason_code}")

            # Build deal dict
            result.append({
                "ticket":       int(d.ticket),
                "position_id":  int(d.position_id),
                "symbol":       str(d.symbol),
                "type":         "BUY" if d.type == mt5.DEAL_TYPE_BUY else "SELL",
                "volume":       float(d.volume),
                "price":        float(d.price),
                "profit":       float(d.profit),
                "reason_code":  reason_code,
                "reason_label": reason_label,
                "time":         datetime.fromtimestamp(d.time, tz=timezone.utc),
                "comment":      str(d.comment) if d.comment else "",
                "magic":        int(d.magic),
            })

        self._last_check_ts = to_date
        if result:
            log.info("MT5 history check: %d deals classified since %s",
                     len(result), from_date.isoformat())
            for r in result:
                log.debug("  - ticket=%d pos=%d reason=%s profit=%+.2f",
                          r["ticket"], r["position_id"],
                          r["reason_label"], r["profit"])
        return result

    def is_close_event(self, deal: dict) -> bool:
        """
        Returns True if this deal represents a close we want to emit event for.
        (Filters out SYSTEM_CLOSE since position_monitor already emitted event,
        and ROLLOVER/VMARGIN/SPLIT which are not trading events.)
        """
        return deal.get("reason_label", "") in self.CLOSE_LABELS
```

**Validation:**
- File must exist after creation
- py_compile must pass
- File size roughly 4500-5500 bytes

---

## MUDANÇA 2 — Integrate MT5HistoryWatcher in `position_monitor.py`

Target: `C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\position_monitor.py`

Esta mudança tem **4 sub-passos** (str_replace cirúrgicos).

### 2.1 — Import MT5HistoryWatcher

**Procurar os imports do topo do ficheiro. Normalmente há `from live.kill_zones import ...` ou similar.**

**ClaudeCode: MOSTRAR PRIMEIRO** o que está nas primeiras 30 linhas de imports do ficheiro. Identificar local apropriado.

Proposta de str_replace (ajustar old_str ao que realmente existe):

**old_str** (exemplo — ClaudeCode deve confirmar texto real):
```python
import MetaTrader5 as mt5
```

**new_str:**
```python
import MetaTrader5 as mt5
from live.mt5_history_watcher import MT5HistoryWatcher
```

**Se `import MetaTrader5 as mt5` não existir directamente no position_monitor.py**, adicionar ambas as linhas no local certo (próximo de outros imports do `live.*`). **Reportar localização exacta.**

### 2.2 — Instantiate watcher in PositionMonitor.__init__

**Procurar `def __init__` da classe PositionMonitor.** Ler ~50 linhas desde a assinatura.

**ClaudeCode: MOSTRAR a função __init__ completa primeiro.** Identificar onde tem `self.executor = ...` para adicionar watcher imediatamente após.

**Proposta str_replace** (ClaudeCode confirma texto real):

Procurar algo como:
```python
        self.executor = executor
        # ... outras linhas self.X = ...
```

Adicionar **após a linha com `self.executor = executor`** (ou equivalente):

```python
        # === Fase 3 Scope B.1 — MT5 history watcher ===
        # Detects TP/SL/manual/system closes via MT5 deal history.
        # Called on-demand when position count drops (efficient — no polling).
        try:
            from mt5_executor import MAGIC
            self._history_watcher = MT5HistoryWatcher(self.executor, MAGIC)
            self._last_position_count = 0
        except Exception as _hw_err:
            log.warning("MT5HistoryWatcher init failed: %s (continuing without)", _hw_err)
            self._history_watcher = None
            self._last_position_count = 0
```

**Nota:** o MAGIC é importado de mt5_executor (mesmo pattern usado noutros lugares). Try/except para robustez — se algo falhar, watcher fica None e resto do sistema continua normal.

### 2.3 — Add `_handle_closed_deal` and `_find_trade_by_ticket` methods

**Procurar a última método da classe PositionMonitor antes de qualquer função fora da classe.**

**ClaudeCode: MOSTRAR linhas ~1970-2095** (fim do ficheiro).

Adicionar **dentro da classe PositionMonitor** (após `_close_ticket` ou similar, antes do `def _ts()` fora da classe).

**Proposta de novo código** — inserir antes de `def _ts() -> str:`:

**old_str** (fim da classe PositionMonitor, procurar a última chave antes da função `_ts()` standalone):
```python
                self._emit_position_event(
                    event_type=reason,
                    direction=_dir,
                    ticket=ticket,
                    reason=reason,
                    action_taken="CLOSE",
                    result=f"FAILED:{result.get('error')}",
                    attempted=True,
                    execution_state="FAILED",
                    execution_error=str(result.get("error", "")),
                    broker=_broker,
                    account=_account,
                )


def _ts() -> str:
```

**new_str:**
```python
                self._emit_position_event(
                    event_type=reason,
                    direction=_dir,
                    ticket=ticket,
                    reason=reason,
                    action_taken="CLOSE",
                    result=f"FAILED:{result.get('error')}",
                    attempted=True,
                    execution_state="FAILED",
                    execution_error=str(result.get("error", "")),
                    broker=_broker,
                    account=_account,
                )

    # ------------------------------------------------------------------
    # FASE 3 SCOPE B.1 — MT5 history watcher integration
    # ------------------------------------------------------------------

    def _find_trade_by_ticket(self, position_id: int) -> Optional[dict]:
        """
        Lookup trade record where any leg matches the given position_id.
        Used by _handle_closed_deal to distinguish TP1_HIT vs TP2_HIT.
        """
        try:
            trades = _load_trades()
        except Exception as e:
            log.debug("_load_trades failed: %s", e)
            return None
        for t in trades:
            for key in ("leg1_ticket", "leg2_ticket", "leg3_ticket"):
                try:
                    if int(t.get(key, 0) or 0) == int(position_id):
                        return t
                except Exception:
                    continue
        return None

    def _handle_closed_deal(self, deal: dict) -> None:
        """
        Classify closed deal and emit PM_EVENT accordingly.
        Called by main check() loop when position count drops.

        Deal classification (via MT5 history reason + trades.csv lookup):
          - TP1_HIT: leg1 ticket closed by TP
          - TP2_HIT: leg2 or leg3 ticket closed by TP
          - SL_HIT:  any leg closed by SL
          - MANUAL_CLOSE: closed via terminal/mobile/web
          - SYSTEM_CLOSE: closed by our code (skip — already emitted event)
        """
        ts = _ts()
        reason_label = deal.get("reason_label", "UNKNOWN")
        ticket = deal.get("ticket", 0)
        position_id = deal.get("position_id", 0)
        price = deal.get("price", 0.0)
        profit = deal.get("profit", 0.0)

        # Direction: on CLOSE, SELL deal closes a LONG; BUY deal closes a SHORT
        deal_type = deal.get("type", "UNKNOWN")
        direction = "LONG" if deal_type == "SELL" else ("SHORT" if deal_type == "BUY" else "UNKNOWN")

        # Skip system closes (already emitted event via _close_ticket path)
        if reason_label == "SYSTEM_CLOSE":
            log.debug("Skipping SYSTEM_CLOSE for ticket=%d (event already emitted)", ticket)
            return

        # Skip non-trading events
        if reason_label not in ("TP_HIT", "SL_HIT", "MANUAL_TERMINAL", "MANUAL_MOBILE",
                                 "MANUAL_WEB", "STOP_OUT"):
            log.debug("Skipping reason=%s for ticket=%d (not a close event)",
                      reason_label, ticket)
            return

        # TP1 vs TP2 attribution via trades.csv lookup (Option X1 — Barbara approved)
        event_type = None
        if reason_label == "TP_HIT":
            trade_rec = self._find_trade_by_ticket(position_id)
            if trade_rec:
                leg1 = int(trade_rec.get("leg1_ticket", 0) or 0)
                leg2 = int(trade_rec.get("leg2_ticket", 0) or 0)
                leg3 = int(trade_rec.get("leg3_ticket", 0) or 0)
                if ticket == leg1:
                    event_type = "TP1_HIT"
                elif ticket == leg2 or ticket == leg3:
                    event_type = "TP2_HIT"
                else:
                    # Unknown mapping — emit generic TP_HIT with warning
                    log.warning("TP_HIT: ticket %d not matching any leg in trade rec (legs=%d/%d/%d)",
                                ticket, leg1, leg2, leg3)
                    event_type = "TP_HIT"
            else:
                log.warning("TP_HIT: no trade record found for position_id=%d", position_id)
                event_type = "TP_HIT"

        elif reason_label == "SL_HIT":
            event_type = "SL_HIT"

        elif reason_label in ("MANUAL_TERMINAL", "MANUAL_MOBILE", "MANUAL_WEB"):
            event_type = "MANUAL_CLOSE"

        elif reason_label == "STOP_OUT":
            event_type = "STOP_OUT"

        if event_type is None:
            return

        # Determine broker (roboforex = demo non-live; hantec = live)
        broker = "Hantec" if getattr(self.executor, "is_live", False) else "RoboForex"
        account = None
        try:
            if hasattr(self.executor, "get_account_info"):
                account = self.executor.get_account_info().get("login")
        except Exception:
            pass

        reason_str = f"{reason_label} @ {price:.2f} | pnl={profit:+.2f}"
        print(f"[{ts}] MT5_HISTORY: {event_type} ticket={ticket} | {reason_str}")
        log.info("MT5 history event: %s direction=%s ticket=%d pnl=%+.2f",
                 event_type, direction, ticket, profit)

        # Emit canonical PM event — goes through _publish_canonical_pm_event
        # which (after Fase 2 Mudança 7) also notifies Telegram.
        self._emit_position_event(
            event_type=event_type,
            direction=direction,
            ticket=ticket,
            reason=reason_str,
            action_taken="CLOSED_BY_BROKER",
            result=f"profit={profit:+.2f}",
            attempted=True,
            execution_state="EXECUTED",
            broker=broker,
            account=account,
        )


def _ts() -> str:
```

### 2.4 — Call MT5HistoryWatcher in main check loop

**Procurar o main check loop da classe PositionMonitor.** Normalmente há uma função `check()` ou `run()` que itera sobre posições.

**ClaudeCode: MOSTRAR a função principal de verificação.**

Identificar local **no INÍCIO do loop tick** (antes de processar posições individuais) onde adicionar:

```python
        # === Fase 3 Scope B.1 — MT5 history check (on position count drop) ===
        try:
            if self._history_watcher is not None:
                current_positions = self.executor.get_open_positions() or []
                current_count = len(current_positions)

                if current_count < self._last_position_count:
                    # Position(s) closed since last check — classify via MT5 history
                    closed_deals = self._history_watcher.find_closed_since_last_check()
                    for deal in closed_deals:
                        if self._history_watcher.is_close_event(deal):
                            self._handle_closed_deal(deal)

                self._last_position_count = current_count
        except Exception as _hw_e:
            log.debug("MT5 history check error: %s", _hw_e)
```

**ClaudeCode:** identificar o local correcto (primeiras linhas do loop check), mostrar contexto antes/depois, propor str_replace preciso. **Não forçar se o pattern não bater.**

---

## VERIFICATION STEPS (após todas as mudanças)

### Step 1 — Verify new file exists

```powershell
$staging = "C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221"
$new_file = "$staging\live\mt5_history_watcher.py"

if (Test-Path $new_file) {
    $info = Get-Item $new_file
    $hash = (Get-FileHash $new_file -Algorithm MD5).Hash
    Write-Host "  OK  $new_file | Size: $($info.Length)B | MD5: $hash"
} else {
    throw "mt5_history_watcher.py NOT CREATED"
}
```

### Step 2 — py_compile all modified files

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
$files = @(
    "$staging\live\mt5_history_watcher.py",
    "$staging\live\position_monitor.py"
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

### Step 3 — Verify position_monitor.py hash changed from Fase 2

```powershell
$fase2_hash = "F9CDF022EEF2501A433CC4535EFE86D9"  # from Fase 2 report
$new_hash = (Get-FileHash "$staging\live\position_monitor.py" -Algorithm MD5).Hash
if ($new_hash -ne $fase2_hash) {
    Write-Host "  OK  position_monitor.py changed from Fase 2: $fase2_hash -> $new_hash"
} else {
    throw "position_monitor.py NOT CHANGED from Fase 2 state — str_replace may have failed"
}
```

---

## OUTPUT — Report

Criar `C:\FluxQuantumAI\FASE_3_SCOPE_B1_REPORT_<timestamp>.md`:

```markdown
# FASE 3 SCOPE B.1 — Report

**Timestamp:** <UTC>
**Duration:** <minutes>
**Status:** ✅ SUCCESS / ❌ FAILED
**Design doc:** DESIGN_DOC_Telegram_PositionEvents_v1.md (Scope B.1)

## Staging location

`C:\FluxQuantumAI\deploy-staging-scope-a-20260418_085221\live\`

## Mudanças executadas

### M1 — NEW: mt5_history_watcher.py
- Status: ✅/❌
- File created: YES/NO
- Size: X bytes
- Hash: <MD5>
- py_compile: ✅/❌

### M2.1 — position_monitor.py: import MT5HistoryWatcher
- Status: ✅/❌
- old_str found: YES/NO
- Context (linhas do ficheiro antes/depois):

```python
<paste>
```

### M2.2 — position_monitor.py: watcher instantiation in __init__
- Status: ✅/❌
- Local exacto: linha <N>
- Context:

```python
<paste>
```

### M2.3 — position_monitor.py: _handle_closed_deal + _find_trade_by_ticket methods
- Status: ✅/❌
- Lines added: ~<N>
- Location (fim da classe PositionMonitor)

### M2.4 — position_monitor.py: MT5 history check in main loop
- Status: ✅/❌
- Local exacto: linha <N>
- Context (5 antes + 10 depois):

```python
<paste>
```

## py_compile

| File | Status |
|---|---|
| mt5_history_watcher.py (new) | ✅ |
| position_monitor.py (modified) | ✅ |

## Hash verification

| File | Pre-Fase3 MD5 | Post-Fase3 MD5 | Changed |
|---|---|---|---|
| position_monitor.py | F9CDF022EEF2501A433CC4535EFE86D9 | <new> | YES ✅ |
| mt5_history_watcher.py | (did not exist) | <new> | CREATED ✅ |

## Files for Claude audit

ClaudeCode deve incluir no final do report:

1. Conteúdo completo de `mt5_history_watcher.py` (novo, ~180 linhas)
2. Diff do `position_monitor.py` (secções afectadas — imports, __init__ + methods adicionados + loop modification)

## Next phase

**PARAR.** Aguardar Claude audit + Barbara aprovação antes de Fase 4 (Scope B.2 News Exit Integration).
```

---

## PROIBIDO NESTA FASE

- ❌ Tocar no LIVE (apenas staging)
- ❌ Parar serviços
- ❌ Scope B.2, B.3, B.4 (outras fases)
- ❌ Modificar event_processor.py (outra fase)
- ❌ Criar outros ficheiros novos além de mt5_history_watcher.py
- ❌ Deploy (Fase 8)
- ❌ Inventar imports ou estruturas — se old_str não existir, PARAR e reportar

---

## COMUNICAÇÃO FINAL

```
FASE 3 SCOPE B.1 — <STATUS>
Staging: <path>
Novos ficheiros: 1 (mt5_history_watcher.py)
Ficheiros modificados: 1 (position_monitor.py)
py_compile: 2/2 OK
Hashes: position_monitor changed ✅ + new file created ✅
Report: <path>

Aguardando Claude audit + Barbara aprovação antes de Fase 4.
```

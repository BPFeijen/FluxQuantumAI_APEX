# TASK: IMPLEMENTAÇÃO FIX — near_level + level_detector Direction-Aware

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Sprint:** entry_logic_fix_20260420
**Base:** `DESIGN_DOC_near_level_direction_aware.md` (aprovado)
**Output:** Implementação + tests + backtest report + TASK_CLOSEOUT

---

## DECISÕES TOMADAS (não discutir, implementar)

Barbara escolheu **fix directo com tests**, não shadow/dual-mode/phases:

1. **Priorização:** `is_valid_direction > distance > source > age`
2. **Fallback policy:** permissiva (aceita `is_valid_direction=True` mesmo em unconfirmed)
3. **Rollout:** implementa → unit tests → integration tests → backtest replay → go live
4. **Sem shadow mode, sem dual-mode, sem feature flag phases**
5. **Staleness 15min:** mantém hardcoded (não mover para settings.json agora)
6. **Re-avaliação dinâmica:** fora scope (sprint futuro)
7. **COUNTER_HTF label:** implementar mas como LOG ONLY (não bloqueia)
8. **Distance=0 edge case:** `PASS` com `is_touch=True` flag
9. **FAR action:** log + metric, não trigger wait-for-retrace

---

## FASES DE EXECUÇÃO

Executa **sequencialmente**. Cada fase tem pré-requisito da anterior. Se qualquer fase falha, PARA e reporta.

---

### FASE 1 — IMPLEMENTAÇÃO CODE (1-2h)

#### 1.1 Discovery antes de editar

```powershell
# Read files relevantes completos
Get-Content "C:\FluxQuantumAI\live\level_detector.py" | Measure-Object -Line
Get-Content "C:\FluxQuantumAI\live\event_processor.py" | Measure-Object -Line

# Localizar todas as invocações de _near_level
Select-String -Path "C:\FluxQuantumAI\**\*.py" -Pattern "_near_level" | Select-Object Path, LineNumber, Line

# Localizar consumers de get_current_levels
Select-String -Path "C:\FluxQuantumAI\**\*.py" -Pattern "get_current_levels" | Select-Object Path, LineNumber, Line
```

**Reportar:** todos os consumers. Se encontrares algum fora de `event_processor.py`, sinaliza antes de prosseguir.

#### 1.2 Backup

```powershell
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sprint_dir = "C:\FluxQuantumAI\sprints\entry_logic_fix_20260420"
$backup_dir = "$sprint_dir\backup_pre_fix_$timestamp"
New-Item -Path $backup_dir -ItemType Directory -Force | Out-Null

Copy-Item "C:\FluxQuantumAI\live\level_detector.py" "$backup_dir\level_detector.py" -Force
Copy-Item "C:\FluxQuantumAI\live\event_processor.py" "$backup_dir\event_processor.py" -Force

$hash_ld_pre = (Get-FileHash "C:\FluxQuantumAI\live\level_detector.py" -Algorithm MD5).Hash
$hash_ep_pre = (Get-FileHash "C:\FluxQuantumAI\live\event_processor.py" -Algorithm MD5).Hash

@{
    "level_detector.py" = @{ "pre_hash" = $hash_ld_pre }
    "event_processor.py" = @{ "pre_hash" = $hash_ep_pre }
    "timestamp" = $timestamp
    "action" = "near_level_direction_aware_fix"
} | ConvertTo-Json | Set-Content "$backup_dir\MANIFEST.json"

Write-Host "Backup: $backup_dir"
```

#### 1.3 Implementar `LevelCandidate` dataclass

Em `C:\FluxQuantumAI\live\level_detector.py`, adicionar no topo (após imports):

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class LevelCandidate:
    """Single level candidate with directional awareness."""
    box_id: int
    level: float                       # liq_top for SHORT, liq_bot for LONG (MT5 space)
    level_gc: float                    # same level in GC space
    source: Literal[
        "m5_confirmed", "m5_unconfirmed",
        "m30_confirmed", "m30_unconfirmed",
    ]
    age_min: float                     # age since last_confirmed_bar or last_seen
    distance_to_price: float           # always >= 0; |level - price|
    is_valid_direction: bool           # True iff level is on correct side for direction
    band: float                        # ATR band used in decision
    timeframe: Literal["M5", "M30"]
    is_touch: bool = False             # True if distance_to_price == 0.0
```

#### 1.4 Implementar `get_levels_for_direction()`

Adicionar como função pública (não método — segue padrão `get_current_levels`):

```python
def get_levels_for_direction(
    direction: Literal["SHORT", "LONG"],
    price: float,
    max_age_min: float = 15.0,
    max_distance_pts: float = 8.0,
    gc_to_mt5_offset: float = None,  # injectado; se None, calcula do parquet
) -> list[LevelCandidate]:
    """
    Retorna candidates ORDENADOS por:
      1. is_valid_direction DESC  (válidos primeiro)
      2. distance_to_price ASC    (mais perto primeiro — PRIORITIZA PROXIMIDADE)
      3. source priority DESC     (m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed)
      4. age_min ASC              (mais fresco primeiro)
    
    Barbara decision: proximity > source > age.
    
    max_age_min: descarta candidates mais velhos.
    max_distance_pts: informativo; não filtra — retornado em band.
    
    Retorno vazio é válido e significa "sem levels direccionalmente válidos".
    """
    # Implementação:
    # 1. Carrega m5_df, m30_df (reusar logic existente)
    # 2. Para cada box (confirmed + unconfirmed, M5 + M30):
    #    - Calcula level (liq_top se SHORT, liq_bot se LONG)
    #    - Calcula level_mt5 (level + gc_to_mt5_offset)
    #    - Calcula age_min
    #    - Filtra por max_age_min
    #    - Calcula distance_to_price = abs(level_mt5 - price)
    #    - Calcula is_valid_direction:
    #        SHORT: level_mt5 > price (level ACIMA do preço)
    #        LONG:  level_mt5 < price (level ABAIXO do preço)
    #    - Calcula band (reuse NEAR_ATR_FACTOR * atr, floor NEAR_FLOOR_PTS)
    #    - Calcula is_touch = (distance_to_price == 0.0)
    #    - Constrói LevelCandidate
    # 3. Ordena pela regra (is_valid_direction DESC, distance ASC, source priority DESC, age ASC)
    # 4. Retorna lista

    # ... [implementa conforme spec acima]
```

**Priority map para ordenação:**
```python
_SOURCE_PRIORITY = {
    "m5_confirmed":    4,
    "m5_unconfirmed":  3,
    "m30_confirmed":   2,
    "m30_unconfirmed": 1,
}
```

#### 1.5 Refactor `_near_level` em `event_processor.py`

```python
def _near_level(
    self,
    price: float,
    direction: Literal["SHORT", "LONG"] | None = None,
) -> tuple[Literal["PASS", "NEAR", "FAR"], "LevelCandidate | None"]:
    """
    Se direction=None, mantém comportamento legacy (preserva consumers não migrados).
    Se direction fornecida, aplica filtragem direction-aware.
    
    Retorno:
        PASS: há level no lado correcto dentro da banda.
        NEAR: há level no lado correcto, mas fora da banda (preço ainda não chegou).
        FAR:  não há level no lado correcto (preço já passou todos OU não há boxes válidos).
    """
    if direction is None:
        return self._near_level_legacy(price)
    
    from live.level_detector import get_levels_for_direction, LevelCandidate
    
    atr = self._metrics.get("atr_m30_parquet", self._metrics.get("atr", 20.0))
    band = max(atr * NEAR_ATR_FACTOR, NEAR_FLOOR_PTS)
    
    candidates = get_levels_for_direction(
        direction=direction,
        price=price,
        max_age_min=15.0,
        max_distance_pts=band,
    )
    
    valid = [c for c in candidates if c.is_valid_direction]
    
    if not valid:
        self._near_level_source = ""
        log.info(
            "NEAR_LEVEL FAR: direction=%s price=%.2f - all %d candidates wrong side",
            direction, price, len(candidates),
        )
        # Telemetry
        self._metric_incr("near_level.far.by_direction." + direction.lower())
        if len(candidates) == 0:
            self._metric_incr("near_level.far.reason.no_candidates")
        else:
            self._metric_incr("near_level.far.reason.all_wrong_side")
        return "FAR", None
    
    top = valid[0]
    self._near_level_source = self._classify_source_from_candidate(top)
    
    if top.distance_to_price <= top.band:
        log.info(
            "NEAR_LEVEL PASS: direction=%s price=%.2f level=%.2f dist=%.1f band=%.1f source=%s is_touch=%s",
            direction, price, top.level, top.distance_to_price, top.band, top.source, top.is_touch,
        )
        self._metric_incr("near_level.pass." + top.source)
        return "PASS", top
    
    log.info(
        "NEAR_LEVEL NEAR: direction=%s price=%.2f level=%.2f dist=%.1f band=%.1f source=%s (waiting)",
        direction, price, top.level, top.distance_to_price, top.band, top.source,
    )
    self._metric_incr("near_level.near.by_direction." + direction.lower())
    return "NEAR", top


def _near_level_legacy(self, price: float) -> tuple[str, float]:
    """Preserve existing logic for consumers not yet migrated (ALPHA route handles own direction)."""
    # ... [move código actual _near_level para aqui]
```

**Helper:**
```python
def _classify_source_from_candidate(self, cand: "LevelCandidate") -> str:
    """Map LevelCandidate.source to legacy _near_level_source string."""
    if cand.source.startswith("m5"):
        return "m5_only"  # TODO: detect M5+M30 overlap if M30 cand also exists
    elif cand.source.startswith("m30"):
        return "m30_only"
    return ""
```

#### 1.6 Refactor `_trending_v1` em `event_processor.py:2396-2402`

```python
_trending_v1 = (
    self.daily_trend in ("long", "short")
    and self.box_high is not None
    and self.box_low is not None
)

if _trending_v1:
    _in_zone = self.box_low <= price <= self.box_high
    # NEW — direction vs HTF alignment check
    _htf_aligned = (
        (direction == "LONG"  and self.daily_trend == "long")
        or (direction == "SHORT" and self.daily_trend == "short")
    )
    if _in_zone and _htf_aligned:
        v1 = "PASS"
    elif _in_zone and not _htf_aligned:
        v1 = "COUNTER_HTF"   # LOG ONLY — does NOT block (Barbara decision)
        log.info(
            "V1 COUNTER_HTF: direction=%s daily_trend=%s price=%.2f box=[%.2f-%.2f] - logged, not blocked",
            direction, self.daily_trend, price, self.box_low, self.box_high,
        )
    else:
        v1 = "ZONE_FAIL"
else:
    # Non-trending: use new direction-aware _near_level
    ne_status, ne_cand = self._near_level(price, direction=direction)
    v1 = {"PASS": "PASS", "NEAR": "NEAR", "FAR": "FAR"}[ne_status]
```

**Semantic COUNTER_HTF:** aparece no decision_log como v1 value mas NÃO bloqueia score. Phase posterior pode converter em BLOCK baseado em dados.

#### 1.7 Migrar `_check_alpha_trigger` para passar `direction`

Localizar todas as invocações de `self._near_level(price)` em `event_processor.py` e mudar para:

```python
# ANTES:
level_type, level_price = self._near_level(price)

# DEPOIS:
# Primeiro determinar direction candidate
# Se ainda não há direction definida, try ambas e escolhe a que tem PASS
for candidate_direction in ["SHORT", "LONG"]:
    status, cand = self._near_level(price, direction=candidate_direction)
    if status == "PASS":
        direction = candidate_direction
        level_type = "liq_top" if direction == "SHORT" else "liq_bot"
        level_price = cand.level
        break
else:
    # Nenhuma direction produziu PASS
    level_type = ""
    level_price = 0.0
    direction = None
```

**Cuidado:** preservar `self._near_level_source` behaviour esperado por downstream consumers.

#### 1.8 py_compile

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m py_compile "C:\FluxQuantumAI\live\level_detector.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile level_detector FAILED" }
& $py -m py_compile "C:\FluxQuantumAI\live\event_processor.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile event_processor FAILED" }
Write-Host "py_compile: OK"
```

---

### FASE 2 — UNIT TESTS (1-2h)

Criar `C:\FluxQuantumAI\tests\test_near_level_direction_aware.py`:

```python
"""Unit tests for direction-aware near_level + level_detector.

Based on DESIGN_DOC §5 test cases.
"""

import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from live.level_detector import LevelCandidate, get_levels_for_direction


# ===== TEST 1: Fresh confirmed + SHORT + level above price + within band =====
def test_1_fresh_confirmed_short_valid_in_band():
    """SHORT with fresh confirmed box, level above price, dist < band → PASS"""
    # Mock parquet with confirmed M5 box @ liq_top_mt5=4800, price=4795, band=8
    # ... setup mock
    candidates = get_levels_for_direction("SHORT", price=4795.0, max_age_min=15.0)
    assert len(candidates) >= 1
    top = candidates[0]
    assert top.is_valid_direction == True
    assert top.source == "m5_confirmed"
    assert top.level > 4795.0  # acima do preço para SHORT
    assert top.distance_to_price <= top.band


# ===== TEST 2: Fresh confirmed + SHORT + level below price → FAR =====
def test_2_fresh_confirmed_short_wrong_side():
    """SHORT with level BELOW price → FAR regardless of distance."""
    # Mock confirmed M5 box with liq_top_mt5=4780, price=4795 (level below)
    candidates = get_levels_for_direction("SHORT", price=4795.0, max_age_min=15.0)
    # Deve haver candidates mas todos com is_valid_direction=False
    assert len(candidates) >= 1
    valid = [c for c in candidates if c.is_valid_direction]
    assert len(valid) == 0, "No candidate should be valid when level below price for SHORT"


# ===== TEST 3: Stale confirmed + fallback unconfirmed correct side → PASS =====
def test_3_stale_confirmed_fallback_correct_side():
    """Confirmed stale >15min, unconfirmed fresh on correct side → PASS via unconfirmed"""
    candidates = get_levels_for_direction("SHORT", price=4795.0, max_age_min=15.0)
    top = candidates[0]
    assert top.is_valid_direction == True
    assert top.source == "m5_unconfirmed"


# ===== TEST 4: Stale confirmed + fallback unconfirmed wrong side → FAR =====
def test_4_stale_confirmed_fallback_wrong_side_03_14_replay():
    """Replicates signal 03:14 bug — confirmed stale, unconfirmed wrong side → FAR"""
    # Mock: confirmed stale >15min com liq_top=4795; unconfirmed fresh liq_top=4780 (abaixo)
    # price = 4791 (entre confirmed stale e unconfirmed wrong-side)
    candidates = get_levels_for_direction("SHORT", price=4791.08, max_age_min=15.0)
    valid = [c for c in candidates if c.is_valid_direction]
    # Confirmed stale pode estar valido direcção (4795 > 4791) MAS age>15min deve filtrar
    # Unconfirmed (4780) é invalido direcção (4780 < 4791)
    # Esperado: 0 valid candidates → FAR
    assert len(valid) == 0


# ===== TEST 5: Multiple unconfirmed boxes mistos =====
def test_5_multiple_unconfirmed_mixed_sides():
    """Mix of correct-side and wrong-side unconfirmed → picks closest correct-side"""
    candidates = get_levels_for_direction("SHORT", price=4795.0, max_age_min=15.0)
    valid = [c for c in candidates if c.is_valid_direction]
    assert len(valid) > 0
    top = valid[0]
    # Deve ser o mais próximo do lado correcto (priority: distance)
    all_valid_distances = [c.distance_to_price for c in valid]
    assert top.distance_to_price == min(all_valid_distances)


# ===== TEST 6: Level exactly at price (distance=0.0) =====
def test_6_level_equal_price_is_touch():
    """distance_to_price == 0.0 → PASS with is_touch=True"""
    # Mock box with liq_top_mt5 == price exactly
    candidates = get_levels_for_direction("SHORT", price=4800.0, max_age_min=15.0)
    # Se há level @ 4800 e price=4800: SHORT tech requires level > price, so 4800 > 4800 is False
    # Decision: treat distance=0 as PASS with is_touch=True (special case)
    # Adjust impl to set is_valid_direction=True if distance==0 regardless of strict >
    touching = [c for c in candidates if c.is_touch]
    if touching:
        assert touching[0].is_valid_direction == True
        assert touching[0].distance_to_price == 0.0


# ===== TEST 7: Level just passed price (dist=+0.1 wrong direction) =====
def test_7_level_just_passed_wrong_direction():
    """SHORT with level 0.1pt BELOW price → is_valid_direction=False → FAR"""
    # level_mt5=4799.9, price=4800.0 → SHORT invalid (level < price)
    candidates = get_levels_for_direction("SHORT", price=4800.0, max_age_min=15.0)
    valid = [c for c in candidates if c.is_valid_direction]
    # Deve NÃO incluir o level @ 4799.9
    assert all(c.level > 4800.0 for c in valid)


# ===== TEST 8: No valid levels at all =====
def test_8_no_valid_levels():
    """Empty parquets → empty list."""
    # Mock empty dataframes
    candidates = get_levels_for_direction("SHORT", price=4800.0, max_age_min=15.0)
    assert candidates == [] or all(not c.is_valid_direction for c in candidates)


# ===== TEST 9: M30-only path =====
def test_9_m30_only_fallback():
    """M5 empty but M30 has valid level → M30 candidate returned"""
    candidates = get_levels_for_direction("LONG", price=4750.0, max_age_min=15.0)
    if candidates:
        assert candidates[0].source in ("m30_confirmed", "m30_unconfirmed")


# ===== TEST 10: M5 confirmed wrong side + M5 unconfirmed correct side =====
def test_10_confirmed_wrong_unconfirmed_correct():
    """Confirmed invalid direction, unconfirmed valid → unconfirmed wins"""
    # Setup: confirmed liq_top_mt5=4780 (below), unconfirmed liq_top_mt5=4810 (above), price=4790
    candidates = get_levels_for_direction("SHORT", price=4790.0, max_age_min=15.0)
    valid = [c for c in candidates if c.is_valid_direction]
    assert valid[0].source == "m5_unconfirmed"


# ===== TEST 11: Transition FAR -> PASS when fresh box arrives =====
def test_11_transition_far_to_pass():
    """Tick N: FAR (no valid). Tick N+1: new box arrives correct side → PASS"""
    # Snapshot 1: only wrong-side candidate
    candidates_before = get_levels_for_direction("SHORT", price=4790.0, max_age_min=15.0)
    valid_before = [c for c in candidates_before if c.is_valid_direction]
    assert len(valid_before) == 0
    
    # Simulate new box inserted
    # ... mock new parquet row with liq_top_mt5=4810
    
    candidates_after = get_levels_for_direction("SHORT", price=4790.0, max_age_min=15.0)
    valid_after = [c for c in candidates_after if c.is_valid_direction]
    assert len(valid_after) >= 1


# ===== INVARIANT TESTS (Appendix A) =====

def test_invariant_short_never_returns_level_below_price_as_valid():
    """SHORT candidates with level <= price MUST have is_valid_direction=False"""
    # Run get_levels many times with different mock setups
    # Assert invariant holds universally
    pass  # implement via property-based test (hypothesis lib se disponível)


def test_invariant_sort_order():
    """Result list MUST be sorted by (is_valid_direction DESC, distance ASC, source DESC, age ASC)"""
    pass


def test_invariant_determinism():
    """Same input same result"""
    r1 = get_levels_for_direction("SHORT", 4795.0, 15.0)
    r2 = get_levels_for_direction("SHORT", 4795.0, 15.0)
    assert r1 == r2


def test_legacy_preserved_when_direction_none():
    """_near_level(price) sem direction mantém comportamento legacy byte-para-byte"""
    # Compare old vs new behaviour com mesmo input
    pass
```

**Executar tests:**
```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
cd C:\FluxQuantumAI
& $py -m pytest tests\test_near_level_direction_aware.py -v
```

**Se qualquer teste falha:** PARA, reporta, não avança.

---

### FASE 3 — BACKTEST COUNTERFACTUAL (1-2h)

Replay decision_log.jsonl com nova logic para quantificar impacto.

Criar `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backtest_counterfactual.py`:

```python
"""Replay decision_log.jsonl with new direction-aware near_level.

For each historical decision:
  - Reconstruct LevelCandidate set at decision_ts
  - Call new _near_level(direction=decision.direction)
  - Compare new vs old outcome
  - Classify: IDENTICAL, NEW_REJECT, NEW_LEVEL, NEW_PASS (bug)
"""

import json
from pathlib import Path

DECISION_LOG = Path("C:\\FluxQuantumAI\\logs\\decision_log.jsonl")
OUTPUT = Path("C:\\FluxQuantumAI\\sprints\\entry_logic_fix_20260420\\BACKTEST_COUNTERFACTUAL.md")

def replay():
    stats = {
        "total": 0,
        "identical": 0,
        "new_reject": 0,
        "new_level_diff": 0,
        "new_pass_unexpected": 0,
        "cannot_replay": 0,
    }
    new_rejects_details = []
    
    with open(DECISION_LOG) as f:
        for line in f:
            dec = json.loads(line)
            stats["total"] += 1
            
            if dec.get("decision", {}).get("action") != "GO":
                continue  # focamos em GOs
            
            # Reconstruct context
            price = dec.get("price_mt5")
            direction = dec.get("decision", {}).get("direction")
            # ... reconstruct parquet state at dec_ts if possible
            # ... call new logic
            
            # Classify
            # ... 
    
    # Write report
    with open(OUTPUT, "w") as f:
        f.write("# Backtest Counterfactual — near_level direction-aware\n\n")
        f.write(f"## Stats\n\n")
        for k, v in stats.items():
            f.write(f"- {k}: {v}\n")
        f.write(f"\n## New rejections (sample)\n\n")
        for d in new_rejects_details[:20]:
            f.write(f"- {d}\n")

if __name__ == "__main__":
    replay()
```

**Reporta specific:** se signal 03:14:46 específico teria sido rejeitado (espera-se sim).

---

### FASE 4 — DRY-RUN LIVE (30min observation)

1. **Backup confirma:**
```powershell
$hash_ld_post = (Get-FileHash "C:\FluxQuantumAI\live\level_detector.py" -Algorithm MD5).Hash
$hash_ep_post = (Get-FileHash "C:\FluxQuantumAI\live\event_processor.py" -Algorithm MD5).Hash
Write-Host "Post-hashes: LD=$hash_ld_post, EP=$hash_ep_post"
```

2. **Restart FluxQuantumAPEX:**
```powershell
$stderr = "C:\FluxQuantumAI\logs\service_stderr.log"
$init_stderr = (Get-Item $stderr).Length

Stop-Service FluxQuantumAPEX -Force
Start-Sleep 3

foreach ($pid in 12332, 8248, 2512) {
    try { $p = Get-Process -Id $pid -ErrorAction Stop; Write-Host "OK PID $pid" } catch { Write-Host "WARN PID $pid" }
}

Start-Service FluxQuantumAPEX
Start-Sleep 30

# Check tracebacks
$new_stderr = Get-Content $stderr -Raw
$new_content = $new_stderr.Substring($init_stderr)
$tracebacks = ($new_content | Select-String "Traceback" -AllMatches).Matches.Count
if ($tracebacks -gt 0) { throw "TRACEBACKS post-restart — ROLLBACK" }
```

3. **Observe 30min** — procura no stderr:
   - `NEAR_LEVEL PASS/NEAR/FAR` entries (nova telemetria)
   - Qualquer traceback
   - Count decisions com v1="COUNTER_HTF"

4. **Se OK:** TASK_CLOSEOUT success. Se tracebacks/errors: ROLLBACK.

---

### FASE 5 — TASK_CLOSEOUT REPORT

Criar `$sprint_dir\TASK_CLOSEOUT_REPORT.md`:

```markdown
# ENTRY LOGIC FIX — TASK_CLOSEOUT

**Sprint:** entry_logic_fix_20260420
**Status:** ✅ SUCCESS / ❌ FAILED / ⚠ ROLLED BACK

## Files modified
- C:\FluxQuantumAI\live\level_detector.py
  - Pre-hash: ... → Post-hash: ...
  - Changes: +LevelCandidate dataclass, +get_levels_for_direction()
- C:\FluxQuantumAI\live\event_processor.py
  - Pre-hash: ... → Post-hash: ...
  - Changes: _near_level refactored with direction param, _trending_v1 HTF-aligned, _check_alpha_trigger migrated

## Tests
- Unit tests: X/11 PASSED
- Invariant tests: X/4 PASSED

## Backtest counterfactual
- Total decisions replayed: X
- IDENTICAL: Y%
- NEW_REJECT: Z%
- Signal 03:14:46 outcome: REJECTED (FAR) ✓ / Other

## Live dry-run
- Service restart: OK
- Capture processes: 3/3 intact
- 30min observation: X NEAR_LEVEL entries, 0 tracebacks
- COUNTER_HTF labels observed: N (not blocking — as designed)

## Open items
- Databento extended backtest (pre-requisite future)
- Re-evaluation dynamic (future sprint)
- COUNTER_HTF → BLOCK conversion (after data collected)

## Rollback
- Available via: [backup path]
- Not triggered
```

---

## COMUNICAÇÃO FINAL

```
ENTRY LOGIC FIX IMPLEMENTED
- LevelCandidate + get_levels_for_direction() added to level_detector
- _near_level + _trending_v1 refactored direction-aware
- Unit tests: X/11 PASSED, Invariants X/4 PASSED
- Backtest: signal 03:14 now REJECTED as FAR ✓
- Dry-run live: 30min clean, service Running
- Capture processes 3/3 intact
- Report: TASK_CLOSEOUT_REPORT.md

Aguardo Claude audit + Barbara go-live decision.
```

---

## PROIBIDO

- ❌ Skipar qualquer fase
- ❌ Skipar tests — se não passam, não avança
- ❌ Commit ao github sem Barbara approval
- ❌ Tocar capture processes (12332, 8248, 2512)
- ❌ Editar news_gate, MT5 executors, position_monitor (scope apertado)
- ❌ Criar feature flag (decisão: sem flag, direct migration)
- ❌ Implementar re-evaluation dynamic (fora scope)
- ❌ Assumir comportamento sem dados — sempre discovery antes

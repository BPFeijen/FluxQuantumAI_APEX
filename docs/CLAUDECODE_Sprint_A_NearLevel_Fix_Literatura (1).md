# TASK: IMPLEMENTAÇÃO near_level + level_detector Direction-Aware — LITERATURA-ALIGNED

**Para:** ClaudeCode
**De:** Claude (ML/AI Engineer) + Barbara (PO)
**Sprint:** entry_logic_fix_20260420
**Base:** `DESIGN_DOC_near_level_direction_aware.md` (aprovado) + pesquisa literatura Wyckoff/ICT/ATS
**Supersedes:** `CLAUDECODE_NearLevel_Fix_Implementation.md` (versão anterior tinha ordenação errada)
**Output:** Implementação + tests + backtest report + TASK_CLOSEOUT

---

## CORRECÇÃO CRÍTICA vs VERSÃO ANTERIOR

A versão anterior do prompt propunha ordenação `is_valid_direction > distance > source > age`. **Errado.**

Pesquisa literatura (Wyckoff, ICT, ATS, web research) unânime:
- **Fresh zones têm MAIOR probabilidade de reacção** (unfilled institutional orders)
- **Direction absoluta é MANDATORY** (premium → short; discount → long)
- **Proximidade é tactical tiebreaker**, não critério primário

**Ordenação CORRECTA (literatura-aligned):**
```
1. is_valid_direction DESC   [MANDATORY — reject if False]
2. source priority DESC      [m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed]
3. age_min ASC               [fresher first]
4. distance_to_price ASC     [tactical tiebreaker]
```

---

## DECISÕES TOMADAS (não discutir, implementar)

1. **Priorização:** literatura-aligned (acima)
2. **Fallback policy:** permissiva (aceita `is_valid_direction=True` mesmo em unconfirmed)
3. **Rollout:** implementação → tests → backtest replay → ClaudeCode PARA após Phase 3, Barbara autoriza restart manual via nssm
4. **Sem shadow/dual-mode/feature flag phases** (fix directo)
5. **Staleness 15min:** mantém hardcoded
6. **Re-avaliação dinâmica:** fora scope (sprint futuro)
7. **COUNTER_HTF label:** implementar LOG ONLY (não bloqueia)
8. **Distance=0 edge case:** `PASS` com `is_touch=True` flag
9. **FAR action:** log + metric, não trigger wait-for-retrace
10. **M30 comanda execução:** preservar filosofia arquitectural — levels M5/M30 seleccionados direccionalmente mas M30_bias/phase continuam a mandar decisões

---

## FASES DE EXECUÇÃO

Executa **sequencialmente**. Se qualquer fase falha, PARA e reporta.

---

### FASE 0 — LEITURA DO DESIGN DOC

**ANTES DE QUALQUER ACÇÃO:**

```powershell
Get-Content "C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\DESIGN_DOC_near_level_direction_aware.md"
```

Ler as 647 linhas completas. Se encontrares divergência entre design doc e este prompt, **design doc manda**. Sinaliza a divergência.

---

### FASE 1 — DISCOVERY ADICIONAL (READ-ONLY, antes de editar)

#### 1.1 Inspeccionar `_resolve_direction` e `_check_alpha_trigger` completos

```powershell
# Read completo das funções — não assumir comportamento
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _resolve_direction" -Context 0,40
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _check_alpha_trigger" -Context 0,80
Select-String -Path "C:\FluxQuantumAI\live\event_processor.py" -Pattern "def _patch2a_continuation_trigger" -Context 0,80
```

**Reportar:**
- Assinatura completa de `_resolve_direction`
- Quando retorna `None`
- Como `direction` é determinada em TRENDING mode
- Como `direction` é determinada em CONTRACTION mode

#### 1.2 Propor adaptação FASE 2.7 (migração _check_alpha_trigger)

O prompt anterior propunha um loop `for candidate_direction in ["SHORT","LONG"]`. ClaudeCode flaggou isto como armadilha semântica (duplica lógica de `_resolve_direction`).

**Nova proposta:** ClaudeCode analisa o flow actual e propõe a adaptação **mantendo `_resolve_direction` como single source of truth**. Exemplos:

**Opção A:** `_near_level(price)` continua retornar `(level_type, level_price)` em modo legacy (sem direction). Depois `_resolve_direction(level_type)` decide. Depois segundo call `_near_level(price, direction=resolved_direction)` valida direccionalmente.

**Opção B:** `_resolve_direction` consulta ambas direcções e escolhe baseado em `is_valid_direction`.

**Opção C:** Outra abordagem que ClaudeCode proponha após ler código.

**Antes de avançar FASE 2, reportar qual opção e porquê.**

---

### FASE 2 — IMPLEMENTAÇÃO CODE (1-2h)

#### 2.1 Backup

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
    "action" = "near_level_direction_aware_fix_literatura_aligned"
} | ConvertTo-Json | Set-Content "$backup_dir\MANIFEST.json"
```

#### 2.2 Implementar `LevelCandidate` dataclass

Em `C:\FluxQuantumAI\live\level_detector.py` (após imports):

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class LevelCandidate:
    """Single level candidate with directional awareness.
    
    Literature reference:
    - Wyckoff: Spring (near liq_bot → LONG), UTAD (near liq_top → SHORT)
    - ICT: Premium PD array (above price) → SHORT; Discount PD array (below price) → LONG
    - ATS: Overvalued (above expansion line) → SHORT in downtrend;
           Undervalued (below expansion line) → LONG in uptrend
    """
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
                                       #   SHORT: level > price (resistance ABOVE)
                                       #   LONG : level < price (support BELOW)
                                       # Treats distance==0 as True (is_touch)
    band: float                        # ATR band used in decision
    timeframe: Literal["M5", "M30"]
    is_touch: bool = False             # True if distance_to_price == 0.0
```

#### 2.3 Implementar `get_levels_for_direction()` — LITERATURA-ALIGNED

```python
_SOURCE_PRIORITY = {
    "m5_confirmed":    4,
    "m5_unconfirmed":  3,
    "m30_confirmed":   2,
    "m30_unconfirmed": 1,
}


def get_levels_for_direction(
    direction: Literal["SHORT", "LONG"],
    price: float,
    max_age_min: float = 15.0,
    max_distance_pts: float = 8.0,
) -> list[LevelCandidate]:
    """
    Returns LevelCandidate list SORTED (literatura-aligned):
      1. is_valid_direction DESC    (MANDATORY — direction absolute per Wyckoff/ICT/ATS)
      2. source priority DESC       (m5_confirmed > m5_unconfirmed > m30_confirmed > m30_unconfirmed)
                                    (proxy for "freshness" — fresh institutional orders)
      3. age_min ASC                (fresher first — same rationale as freshness)
      4. distance_to_price ASC      (tactical tiebreaker)
    
    Rationale for this ordering (literature unanimous):
    - Dukascopy: "Fresh zones should be your priority. Once tested, reliability decreases."
    - AlgoAlpha: "Prioritize first-touch zones. If tested 3+, skip entirely."
    - ATS Strategic Plan: "Bias absolute. No exceptions."
    - ICT: Premium PD array (above) for SHORT. Discount PD array (below) for LONG.
    - Wyckoff: Direction determined by structural position (Spring/UTAD).
    
    max_age_min: discards candidates older than this.
    max_distance_pts: informational in band field; does not filter.
    
    Empty return is valid: "no directionally valid levels".
    """
    candidates = []
    
    # 1. Load parquets (reuse existing logic)
    # 2. For each box (M5 + M30, confirmed + unconfirmed):
    #    - Determine level (liq_top if SHORT, liq_bot if LONG)
    #    - Convert to MT5 space (level_mt5 = level_gc + gc_to_mt5_offset)
    #    - Compute age_min (from last_confirmed_bar_ts or last_seen_ts)
    #    - Filter by max_age_min (discard older)
    #    - Compute distance_to_price = abs(level_mt5 - price)
    #    - Compute is_valid_direction:
    #        SHORT: level_mt5 > price (resistance above) OR distance==0 (touch)
    #        LONG:  level_mt5 < price (support below)   OR distance==0 (touch)
    #    - Compute is_touch = (distance_to_price == 0.0)
    #    - Compute band (reuse NEAR_ATR_FACTOR * atr, floor NEAR_FLOOR_PTS)
    #    - Build LevelCandidate
    #    - Append to candidates
    
    # 3. Sort LITERATURA-ALIGNED
    candidates.sort(
        key=lambda c: (
            not c.is_valid_direction,         # is_valid_direction DESC (True first)
            -_SOURCE_PRIORITY[c.source],      # source priority DESC (m5_confirmed first)
            c.age_min,                        # age_min ASC (fresh first)
            c.distance_to_price,              # distance_to_price ASC (close first — tiebreaker)
        )
    )
    
    return candidates
```

#### 2.4 Refactor `_near_level` em `event_processor.py`

```python
def _near_level(
    self,
    price: float,
    direction: Literal["SHORT", "LONG"] | None = None,
) -> tuple[Literal["PASS", "NEAR", "FAR"], "LevelCandidate | None"]:
    """
    If direction=None, legacy behaviour preserved (consumers not migrated).
    If direction provided, directional filtering applies.
    
    Returns:
        PASS: valid-direction level within band. Entry can proceed.
        NEAR: valid-direction level outside band. Price not yet at zone.
        FAR:  no valid-direction level. Price passed all OR no valid boxes.
    """
    if direction is None:
        return self._near_level_legacy(price)
    
    from live.level_detector import get_levels_for_direction
    
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
        self._metric_incr("near_level.far.by_direction." + direction.lower())
        if len(candidates) == 0:
            self._metric_incr("near_level.far.reason.no_candidates")
        else:
            self._metric_incr("near_level.far.reason.all_wrong_side")
        return "FAR", None
    
    top = valid[0]  # Already sorted literatura-aligned by get_levels_for_direction
    self._near_level_source = self._classify_source_from_candidate(top)
    
    if top.distance_to_price <= top.band:
        log.info(
            "NEAR_LEVEL PASS: direction=%s price=%.2f level=%.2f dist=%.1f band=%.1f source=%s age=%.1fmin is_touch=%s",
            direction, price, top.level, top.distance_to_price, top.band, top.source, top.age_min, top.is_touch,
        )
        self._metric_incr("near_level.pass." + top.source)
        return "PASS", top
    
    log.info(
        "NEAR_LEVEL NEAR: direction=%s price=%.2f level=%.2f dist=%.1f band=%.1f source=%s age=%.1fmin (waiting approach)",
        direction, price, top.level, top.distance_to_price, top.band, top.source, top.age_min,
    )
    self._metric_incr("near_level.near.by_direction." + direction.lower())
    return "NEAR", top


def _near_level_legacy(self, price: float) -> tuple[str, float]:
    """Preserve existing logic byte-for-byte for consumers not yet migrated."""
    # Move current _near_level code here unchanged
    ...


def _classify_source_from_candidate(self, cand: "LevelCandidate") -> str:
    """Map LevelCandidate.source to legacy _near_level_source string."""
    if cand.source.startswith("m5"):
        return "m5_only"
    elif cand.source.startswith("m30"):
        return "m30_only"
    return ""
```

#### 2.5 Refactor `_trending_v1` em `event_processor.py`

```python
_trending_v1 = (
    self.daily_trend in ("long", "short")
    and self.box_high is not None
    and self.box_low is not None
)

if _trending_v1:
    _in_zone = self.box_low <= price <= self.box_high
    _htf_aligned = (
        (direction == "LONG"  and self.daily_trend == "long")
        or (direction == "SHORT" and self.daily_trend == "short")
    )
    if _in_zone and _htf_aligned:
        v1 = "PASS"
    elif _in_zone and not _htf_aligned:
        v1 = "COUNTER_HTF"   # LOG ONLY — does NOT block
        log.info(
            "V1 COUNTER_HTF: direction=%s daily_trend=%s price=%.2f box=[%.2f-%.2f] — logged, not blocked",
            direction, self.daily_trend, price, self.box_low, self.box_high,
        )
    else:
        v1 = "ZONE_FAIL"
else:
    # Non-trending: use new direction-aware _near_level
    ne_status, ne_cand = self._near_level(price, direction=direction)
    v1 = {"PASS": "PASS", "NEAR": "NEAR", "FAR": "FAR"}[ne_status]
```

#### 2.6 Migrar `_check_alpha_trigger` e `_patch2a_continuation_trigger`

**Baseado em FASE 1.2 analysis.** Implementar opção escolhida (A/B/C) mantendo `_resolve_direction` como single source of truth.

#### 2.7 py_compile

```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
& $py -m py_compile "C:\FluxQuantumAI\live\level_detector.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile level_detector FAILED" }
& $py -m py_compile "C:\FluxQuantumAI\live\event_processor.py"
if ($LASTEXITCODE -ne 0) { throw "py_compile event_processor FAILED" }
Write-Host "py_compile: OK"
```

---

### FASE 3 — UNIT TESTS (1-2h)

Criar `C:\FluxQuantumAI\tests\test_near_level_direction_aware.py` com os 11 test cases do design doc §5, plus invariants do apêndice A. **Todos têm que passar antes de avançar.**

```python
"""Unit tests for direction-aware near_level + level_detector.
Literatura-aligned ordering: is_valid_direction > source > age > distance.
"""

# ===== CORE TESTS =====
# Test 1: Fresh confirmed + SHORT + level above + within band → PASS
# Test 2: Fresh confirmed + SHORT + level below → FAR
# Test 3: Stale confirmed + fallback unconfirmed correct side → PASS
# Test 4: Stale confirmed + fallback unconfirmed wrong side → FAR (replica 03:14 bug)
# Test 5: Multiple unconfirmed mixed sides → picks closest correct-side (tiebreaker)
# Test 6: Level exactly at price (distance=0.0) → PASS with is_touch=True
# Test 7: Level just passed price (wrong side) → FAR
# Test 8: No valid levels → FAR
# Test 9: M30-only fallback → PASS with source m30_*
# Test 10: M5 confirmed wrong side + M5 unconfirmed correct side → unconfirmed wins
# Test 11: Transition FAR → PASS when new box arrives

# ===== INVARIANT TESTS (literatura ordering) =====

def test_invariant_ordering_literatura_aligned():
    """Result MUST be sorted by: is_valid_direction DESC, source DESC, age ASC, distance ASC."""
    # Setup mock: create candidates with different combinations
    # Call get_levels_for_direction
    # Assert order matches literatura
    pass

def test_invariant_source_priority():
    """m5_confirmed MUST come before m5_unconfirmed when both valid direction and similar age."""
    pass

def test_invariant_freshness_beats_distance():
    """Fresh m5_confirmed 5pts away MUST win over stale m5_unconfirmed 1pt away (literatura)."""
    # Barbara note: literature says fresh orders unfilled have more weight than proximity
    pass

def test_invariant_short_never_below_price():
    """SHORT candidates with level <= price MUST have is_valid_direction=False."""
    pass

def test_invariant_determinism():
    """Same input same result."""
    pass
```

**Executar:**
```powershell
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
cd C:\FluxQuantumAI
& $py -m pytest tests\test_near_level_direction_aware.py -v
```

**Se qualquer teste falhar:** PARA, reporta, não avança.

---

### FASE 4 — BACKTEST COUNTERFACTUAL (1-2h)

Criar `C:\FluxQuantumAI\sprints\entry_logic_fix_20260420\backtest_counterfactual.py`:

```python
"""Replay decision_log.jsonl with new direction-aware near_level."""

import json
from pathlib import Path

DECISION_LOG = Path("C:\\FluxQuantumAI\\logs\\decision_log.jsonl")
OUTPUT = Path("C:\\FluxQuantumAI\\sprints\\entry_logic_fix_20260420\\BACKTEST_COUNTERFACTUAL.md")

def replay():
    stats = {
        "total": 0,
        "go_signals": 0,
        "identical": 0,
        "new_reject_far": 0,
        "new_reject_near": 0,
        "new_level_diff": 0,
        "unexpected_new_pass": 0,
        "cannot_replay": 0,
    }
    new_rejects_03_14 = None  # specific target
    new_rejects_details = []
    
    with open(DECISION_LOG) as f:
        for line in f:
            dec = json.loads(line)
            stats["total"] += 1
            action = dec.get("decision", {}).get("action", "")
            if action != "GO":
                continue
            stats["go_signals"] += 1
            
            # Reconstruct context
            ts = dec.get("timestamp", "")
            price = dec.get("price_mt5", 0)
            direction = dec.get("decision", {}).get("direction", "")
            
            # Call new logic
            # (need to reconstruct parquet state at ts if possible, or use fields in decision)
            
            # Classify outcome
            # ...
            
            # Specific target: signal 03:14:46 should be rejected
            if "2026-04-20T03:14:4" in ts:
                new_rejects_03_14 = {...}  # capture detailed outcome
    
    # Write report
    with open(OUTPUT, "w") as f:
        f.write("# Backtest Counterfactual — near_level direction-aware (literatura)\n\n")
        f.write(f"## Stats\n\n")
        for k, v in stats.items():
            f.write(f"- {k}: {v}\n")
        f.write(f"\n## Target: signal 03:14:46\n\n")
        f.write(f"{new_rejects_03_14}\n\n")
        f.write(f"## New rejections (sample first 20)\n\n")
        for d in new_rejects_details[:20]:
            f.write(f"- {d}\n")

if __name__ == "__main__":
    replay()
```

**Must verify specifically:** signal 03:14:46 is classified as FAR/NEAR (rejected) under new logic.

---

### FASE 5 — PARAR PARA BARBARA

**NÃO RESTART SERVIÇO.**

ClaudeCode gera TASK_CLOSEOUT report e **PARA**. Barbara autoriza restart manual via nssm depois de ler o report.

---

### FASE 6 — TASK_CLOSEOUT REPORT

Criar `$sprint_dir\TASK_CLOSEOUT_REPORT.md`:

```markdown
# ENTRY LOGIC FIX — TASK_CLOSEOUT (LITERATURA-ALIGNED)

**Sprint:** entry_logic_fix_20260420
**Status:** ✅ READY FOR RESTART / ❌ FAILED
**Restart status:** NOT TRIGGERED — awaiting Barbara authorization via nssm

## Files modified
- C:\FluxQuantumAI\live\level_detector.py
  - Pre-hash: ... → Post-hash: ...
  - Changes: +LevelCandidate dataclass, +get_levels_for_direction() literatura-aligned
- C:\FluxQuantumAI\live\event_processor.py
  - Pre-hash: ... → Post-hash: ...
  - Changes: _near_level refactored with direction param, _trending_v1 HTF-aligned, _check_alpha_trigger migrated

## Ordering applied
Literatura-aligned:
1. is_valid_direction DESC [MANDATORY]
2. source priority DESC    [m5_confirmed > m5_unconfirmed > m30_* ]
3. age_min ASC             [fresher first]
4. distance_to_price ASC   [tiebreaker]

## Tests
- Unit tests: X/11 PASSED
- Invariant tests: X/5 PASSED (ordering + source priority + freshness-beats-distance + short-never-below-price + determinism)

## Backtest counterfactual
- Total decisions replayed: X
- GO signals: Y
- IDENTICAL: Z%
- NEW_REJECT (FAR): W%
- NEW_REJECT (NEAR): V%
- Signal 03:14:46: [REJECTED with reason / OTHER — reportar]

## Restart instructions for Barbara
```powershell
# Barbara runs manually:
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX
# Wait 60s, check tracebacks:
Get-Content "C:\FluxQuantumAI\logs\service_stderr.log" -Tail 100 | Select-String "Traceback"
# Verify capture processes intact:
foreach ($pid in 12332, 8248, 2512) { Get-Process -Id $pid -ErrorAction SilentlyContinue }
```

## Rollback
- Available via: [backup path]
- Restore command:
```powershell
Copy-Item "[backup_dir]\level_detector.py" "C:\FluxQuantumAI\live\level_detector.py" -Force
Copy-Item "[backup_dir]\event_processor.py" "C:\FluxQuantumAI\live\event_processor.py" -Force
& "C:\tools\nssm\nssm.exe" restart FluxQuantumAPEX
```

## Open items for future sprints
- Databento extended backtest (validação expandida)
- Re-evaluation dynamic (out of scope current sprint)
- COUNTER_HTF → BLOCK conversion (after data)
- Freshness explicit tracking (is_fresh flag in LevelCandidate — currently proxied by source)
```

---

## COMUNICAÇÃO FINAL

```
ENTRY LOGIC FIX IMPLEMENTED (LITERATURA-ALIGNED)
Ordering: is_valid_direction > source > age > distance
- LevelCandidate + get_levels_for_direction added to level_detector
- _near_level + _trending_v1 refactored direction-aware
- Unit tests: X/11 PASSED, Invariants X/5 PASSED
- Backtest: signal 03:14:46 outcome [REJECTED/OTHER]
- STOPPED before restart — awaiting Barbara authorization via nssm

Report: TASK_CLOSEOUT_REPORT.md

Aguardo Claude audit + Barbara go-live decision.
```

---

## PROIBIDO

- ❌ Skipar FASE 0 (ler design doc)
- ❌ Skipar FASE 1 (discovery _resolve_direction)
- ❌ Skipar qualquer teste
- ❌ Restart serviço (Barbara faz manual via nssm)
- ❌ Commit ao github sem Barbara approval
- ❌ Tocar capture processes (12332, 8248, 2512)
- ❌ Editar news_gate, MT5 executors, position_monitor (scope apertado)
- ❌ Usar ordenação `distance > source` (literatura diz contrário — ordering correcto é `source > age > distance`)
- ❌ Commitear se FASE 1.2 não identificou opção clara para migração _check_alpha_trigger

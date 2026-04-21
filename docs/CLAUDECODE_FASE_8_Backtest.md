# TASK: FASE 8 — BACKTEST DIAGNOSTIC END-TO-END

**Para:** ClaudeCode
**Projeto:** `C:\FluxQuantumAI\`
**Escopo:** Backtest histórico completo do APEX actual + forensic report de losses + análise oportunidades perdidas
**Tempo estimado:** 2-4h execução + 30-60min report generation
**Output directory:** `C:\FluxQuantumAI\backtests\FASE_8_<timestamp>\`

**Pergunta de negócio da Barbara:**
> "Onde é que o APEX está a deixar de ganhar dinheiro? O que calibrar para melhorar WR/PnL?"

---

## CONFIGURAÇÃO APROVADA (final)

| Parâmetro | Valor | Fonte |
|---|---|---|
| Configuração APEX | Actual pós-Fase 7 (Sprint 8 activo, CAL-1 a CAL-13) | Deploy 2026-04-18 |
| **Conta alvo** | **RoboForex demo 68302120** | Live production actual |
| **Lot sizing session-based** | Asian [0.01,0.01,0.01] / London [0.02,0.02,0.01] / NY [0.03,0.02,0.01] | Aprovado por Barbara, `P4_Iceberg_Role_Literature_Analysis.md` §5.4 |
| **Iceberg modifier** | ALIGNED: Leg1+0.01, Leg2+0.01 (Runner NUNCA muda); CONTRA: BLOCK | Mesmo doc §5.4 |
| Período completo | 2025-07-01 → 2026-04-18 (10 meses aprox) | Cobertura L2 completa |
| Gap excluded | 2026-01-24 a 2026-02-01 (8 dias irrecuperáveis) | DATA_MANIFEST.md |
| Split temporal | Q3-2025 (Jul-Set) / Q4-2025 (Out-Dez) / Q1-2026 (Jan-Mar) / Q2-2026 parcial (Abr) | Detectar degradação temporal |
| Instrumento | GC (Gold Futures, execution XAUUSD) | Único asset actual |
| Spread applied | 25pts (RoboForex Gold typical) | Aproximação realista |
| Slippage entry | 2pts | Conservative |
| Slippage SL | 3pts | Conservative |
| Commission | 0 | RoboForex sem commission directa |
| SL default | 20pts (conforme produção) | `SYSTEM_Architecture_Current_20260409.md` §6.3 |
| TP1 default | 20pts (FMV M30) | Mesmo doc |
| TP2 default | 50pts (liq oposta) | Mesmo doc |
| Trailing post-TP1 | 77pts (CAL-14) | Mesmo doc §8 |
| max_positions | 2 (BARBARA-DEFINED em operational_rules.py) | `operational_rules.py` |

---

## CRITICAL RULES

1. **READ-ONLY** em código `live/`, `settings.json`, parquets de entrada. **Zero modificações.**
2. **Usar EXACTAMENTE o código actual** (pós-Fase 7). **Importar módulos live, não reescrever lógica.**
3. **Lot sizing EXACTO** conforme session — aplicar iceberg bonus correctamente.
4. **News gate ACTIVO** — mesma lógica de produção (`apex_news_gate.py`).
5. **Respeitar TODAS as operational_rules** — max_positions, margin_floor, dedup.
6. **Aplicar SHIELD** após TP1 (SL→entry para Leg2+Leg3).
7. **Aplicar trailing** após SHIELD (77pts).
8. **Aplicar regime flip, L2 danger, T3 defense** conforme position_monitor.
9. **HedgeManager** não precisa ser simulado (raríssimo, aceitável skip para simplicidade).
10. **NÃO tocar serviços** — FluxQuantumAPEX, capture processes ficam intactos.

---

## DATASETS DE ENTRADA (do DATA_MANIFEST.md)

### Primário (L2 COMPLETO)

`C:\data\processed\calibration_dataset_full.parquet`
- L2 real Jul 2025 → presente
- **Usar este como source of truth** — já tem OHLCV + L2 joined + features calculated

### Complementares (fallback se primário não tiver tudo)

| Path | Uso |
|---|---|
| `C:\data\processed\gc_ohlcv_l2_joined.parquet` | L2 join alternative |
| `C:\data\processed\gc_ats_features_v4.parquet` | Features ATS estruturais (NÃO usar para L2 — é hardcoded 60.0) |
| `C:\data\iceberg\*.jsonl` | Iceberg events históricos (356 ficheiros) |
| `C:\data\OHLCV History\` | OHLCV multi-timeframe se necessário |
| `C:\FluxQuantumAI\config\settings.json` | Thresholds calibrados |

### Validação cruzada (ground truth)

| Path | Uso |
|---|---|
| `C:\FluxQuantumAI\logs\trades.csv` | Trades RoboForex reais — validar que backtest reproduz estes trades aprox |
| `C:\FluxQuantumAI\logs\live_log.csv` | Gate decisions reais para comparar |

---

## PASSO 1 — Verificar engine de backtest existente

```powershell
Write-Host "=== PASSO 1 — Checking existing backtest infrastructure ==="
$backtest_dir = "C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Signal\backtest"
if (Test-Path $backtest_dir) {
    Get-ChildItem $backtest_dir -Recurse -Filter "*.py" | Select-Object Name, Length, LastWriteTime | Format-Table
    Write-Host ""
    Write-Host "Existing scripts found — review before building new engine."
} else {
    Write-Host "No existing backtest engine — will build new minimal engine."
}
```

**Se existir engine utilizável:**
- Revê o código
- Verifica se consegue: replay L2, invocar ats_live_gate + event_processor logic, simular fills
- Se sim, **adapta-o** em vez de reescrever
- Reporta no output final que engine usou e porquê

**Se não existir ou for inadequado:**
- Construir novo engine mínimo (ver PASSO 2)

---

## PASSO 2 — Construir/adaptar backtest engine

Criar `C:\FluxQuantumAI\backtests\fase_8_backtest.py` (se engine existente não for suficiente):

```python
"""
Fase 8 Backtest Engine — Replay APEX logic over historical L2 data.

CRITICAL: Uses LIVE code paths (ats_live_gate.py, operational_rules.py, etc.)
rather than reimplementing. Only execution layer is mocked.

Config approved by Barbara 2026-04-18:
- RoboForex demo account (68302120)
- Session-based lot sizing (Asian 0.01s / London 0.02/0.02/0.01 / NY 0.03/0.02/0.01)
- Iceberg bonus: ALIGNED +0.01 to Leg1+Leg2 only
- Period Jul 2025 → Apr 2026 (gap 24Jan-1Feb 2026 excluded)
- Spread 25pts, slippage 2pts entry / 3pts SL, commission 0
"""

from __future__ import annotations
import sys
import json
import uuid
import logging
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

# Path live code
sys.path.insert(0, r"C:\FluxQuantumAI")

# Live modules (NOT rewritten — use production logic)
from live.ats_live_gate import AtsLiveGate
from live.ats_iceberg_gate import AtsIcebergGate
from live.operational_rules import OperationalRules
from live.kill_zones import detect_session  # or equivalent function
from live.level_detector import LevelDetector
# NOTE: event_processor NOT imported directly — we mock execution loop

# Config
CONFIG = json.load(open(r"C:\FluxQuantumAI\config\settings.json"))

# Constants from APEX production
SPREAD_PTS = 25.0
SLIPPAGE_ENTRY_PTS = 2.0
SLIPPAGE_SL_PTS = 3.0
COMMISSION_PER_LOT = 0.0  # RoboForex no commission
TRAILING_POST_TP1_PTS = 77.0   # CAL-14
SL_DEFAULT_PTS = 20.0
TP1_DEFAULT_PTS = 20.0
TP2_DEFAULT_PTS = 50.0
USD_PER_POINT_PER_LOT = 10.0  # XAUUSD 1pt move = $10 per 1.0 lot (approx)

# Session lot sizing (Barbara-approved)
LOT_SIZING_BY_SESSION = {
    "Asian":  {"Leg1": 0.01, "Leg2": 0.01, "Runner": 0.01},
    "London": {"Leg1": 0.02, "Leg2": 0.02, "Runner": 0.01},
    "NY":     {"Leg1": 0.03, "Leg2": 0.02, "Runner": 0.01},
}

@dataclass
class BacktestPosition:
    position_id: int
    timestamp_entry: datetime
    direction: str  # LONG/SHORT
    session: str    # Asian/London/NY
    strategy: str   # ALPHA/GAMMA/DELTA
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    lot_leg1: float
    lot_leg2: float
    lot_runner: float

    # State tracking
    shield_activated: bool = False
    trailing_active: bool = False
    current_sl: Optional[float] = None

    # Leg results
    leg1_status: str = "OPEN"  # OPEN/TP_HIT/SL_HIT
    leg1_exit_price: Optional[float] = None
    leg1_exit_time: Optional[datetime] = None
    leg1_pnl: Optional[float] = None

    leg2_status: str = "OPEN"
    leg2_exit_price: Optional[float] = None
    leg2_exit_time: Optional[datetime] = None
    leg2_pnl: Optional[float] = None

    leg3_status: str = "OPEN"
    leg3_exit_price: Optional[float] = None
    leg3_exit_time: Optional[datetime] = None
    leg3_pnl: Optional[float] = None

    # Per-position diagnostic
    mae_pts: float = 0.0  # Max adverse excursion
    mfe_pts: float = 0.0  # Max favorable excursion
    regime_flip_exit: bool = False
    l2_danger_exit: bool = False
    news_exit: bool = False

    # Gate snapshot at entry (for forensic report)
    gate_snapshot: dict = field(default_factory=dict)
    market_context: dict = field(default_factory=dict)

class BacktestExecutor:
    """Mock execution layer. Replaces MT5."""
    def __init__(self):
        self.next_position_id = 1
        self.open_positions: list[BacktestPosition] = []
        self.closed_positions: list[BacktestPosition] = []

    def open_3leg(self, direction, session, strategy, intended_entry,
                  sl, tp1, tp2, iceberg_aligned, timestamp, gate_snapshot, market_context):
        """Open 3-leg position with session-based lots + iceberg bonus."""
        lots = LOT_SIZING_BY_SESSION[session].copy()
        if iceberg_aligned:
            lots["Leg1"] += 0.01
            lots["Leg2"] += 0.01
            # Runner NEVER changes

        # Apply slippage + spread
        if direction == "LONG":
            entry = intended_entry + SPREAD_PTS + SLIPPAGE_ENTRY_PTS
        else:
            entry = intended_entry - SPREAD_PTS - SLIPPAGE_ENTRY_PTS

        position = BacktestPosition(
            position_id=self.next_position_id,
            timestamp_entry=timestamp,
            direction=direction,
            session=session,
            strategy=strategy,
            entry_price=entry,
            sl=sl, tp1=tp1, tp2=tp2,
            lot_leg1=lots["Leg1"],
            lot_leg2=lots["Leg2"],
            lot_runner=lots["Runner"],
            current_sl=sl,
            gate_snapshot=gate_snapshot,
            market_context=market_context,
        )
        self.open_positions.append(position)
        self.next_position_id += 1
        return position

    def tick_update(self, bid, ask, timestamp, danger_score, delta_4h_value, news_score):
        """
        Called each tick. Check all open positions for:
        - TP1 hit → SHIELD activation
        - TP2 hit → Leg2 close
        - SL hit → Leg close
        - Trailing adjustment
        - L2 danger exit (danger_score triggers)
        - Regime flip exit (delta_4h reverses)
        - News exit (news_score >= 0.90)
        - Track MAE/MFE
        """
        mid = (bid + ask) / 2.0
        closed_this_tick = []

        for pos in self.open_positions:
            # Track MAE/MFE
            if pos.direction == "LONG":
                adverse = pos.entry_price - mid
                favorable = mid - pos.entry_price
            else:
                adverse = mid - pos.entry_price
                favorable = pos.entry_price - mid
            pos.mae_pts = max(pos.mae_pts, adverse)
            pos.mfe_pts = max(pos.mfe_pts, favorable)

            # Use worst-case price for fill (bid for LONG exit, ask for SHORT exit)
            if pos.direction == "LONG":
                exit_px = bid  # Selling at bid
            else:
                exit_px = ask  # Buying at ask

            # Check news exit (highest priority)
            if news_score >= 0.90:
                pos.news_exit = True
                self._close_all_legs(pos, exit_px, timestamp, "NEWS_EXIT")
                closed_this_tick.append(pos)
                continue

            # Check L2 danger (3 consecutive bars danger_score > threshold)
            # ... implement danger bar tracking

            # Check regime flip (delta_4h reverses by N sigma)
            # ... implement

            # Check SL/TP hits for each leg still open
            # ... apply SHIELD logic, trailing logic

        # Move closed positions
        for pos in closed_this_tick:
            self.open_positions.remove(pos)
            self.closed_positions.append(pos)

    def _close_all_legs(self, pos, exit_px, timestamp, reason):
        """Close all 3 legs at exit_px with reason."""
        if pos.direction == "LONG":
            pnl_per_pt = lambda lot: (exit_px - pos.entry_price) * lot * USD_PER_POINT_PER_LOT
        else:
            pnl_per_pt = lambda lot: (pos.entry_price - exit_px) * lot * USD_PER_POINT_PER_LOT

        for leg_attr in ["leg1", "leg2", "leg3"]:
            status = getattr(pos, f"{leg_attr}_status")
            if status == "OPEN":
                lot = getattr(pos, f"lot_{leg_attr}" if leg_attr != "leg3" else "lot_runner")
                setattr(pos, f"{leg_attr}_status", reason)
                setattr(pos, f"{leg_attr}_exit_price", exit_px)
                setattr(pos, f"{leg_attr}_exit_time", timestamp)
                setattr(pos, f"{leg_attr}_pnl", pnl_per_pt(lot))


def run_backtest(period_start, period_end, output_dir):
    """Main backtest loop."""
    # Load data
    df = pd.read_parquet(r"C:\data\processed\calibration_dataset_full.parquet")
    df = df[(df.timestamp >= period_start) & (df.timestamp <= period_end)]
    # Exclude gap
    gap_start = pd.Timestamp("2026-01-24")
    gap_end = pd.Timestamp("2026-02-01")
    df = df[~((df.timestamp >= gap_start) & (df.timestamp <= gap_end))]

    print(f"Backtest period: {period_start} → {period_end}")
    print(f"Rows to process: {len(df)}")

    executor = BacktestExecutor()
    gate = AtsLiveGate(CONFIG)
    ops_rules = OperationalRules(CONFIG)

    # Event loop
    for idx, row in df.iterrows():
        # Build tick event
        timestamp = row['timestamp']
        bid = row.get('bid', row['close'] - 0.25)
        ask = row.get('ask', row['close'] + 0.25)

        # 1) Update open positions first (process exits)
        executor.tick_update(bid, ask, timestamp,
                             danger_score=row.get('danger_score', 0),
                             delta_4h_value=row.get('delta_4h', 0),
                             news_score=row.get('news_score', 0))

        # 2) Check pre-entry rules
        can_enter, reason = ops_rules.check_can_enter(
            open_positions=executor.open_positions,
            margin_level=10000.0,  # assume sufficient
            signal_price=row['close'],
            signal_direction=...,
            existing_trades=[],
        )
        if not can_enter:
            continue

        # 3) Run gate chain
        gate_result = gate.check(row)  # adapt to actual gate signature
        if gate_result.verdict != "GO":
            continue

        # 4) Open position
        session = detect_session(timestamp)
        executor.open_3leg(
            direction=gate_result.direction,
            session=session,
            strategy=gate_result.strategy,  # ALPHA/GAMMA/DELTA
            intended_entry=row['close'],
            sl=gate_result.sl,
            tp1=gate_result.tp1,
            tp2=gate_result.tp2,
            iceberg_aligned=gate_result.iceberg_aligned,
            timestamp=timestamp,
            gate_snapshot=asdict(gate_result),
            market_context={
                'box_m30_high': row.get('m30_box_high'),
                'box_m30_low': row.get('m30_box_low'),
                'h4_bias': row.get('h4_bias'),
                'd1_bias': row.get('d1_bias'),
                'delta_4h': row.get('delta_4h'),
                'dom_imbalance': row.get('dom_imbalance'),
                'absorption_ratio': row.get('absorption_ratio'),
            }
        )

    # Final close of remaining open positions at last price
    # ...

    # Save results
    save_results(executor, output_dir)
    return executor


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    output_dir = Path(r"C:\FluxQuantumAI\backtests") / f"FASE_8_{datetime.now():%Y%m%d_%H%M%S}"
    output_dir.mkdir(parents=True, exist_ok=True)

    executor = run_backtest("2025-07-01", "2026-04-18", output_dir)
    print(f"Backtest complete. Results in: {output_dir}")
```

**NOTA IMPORTANTE:** O código acima é **esqueleto conceptual**. ClaudeCode deve:
- Adaptar as signatures dos imports à realidade do código (ex: `AtsLiveGate.__init__` pode ser diferente)
- Implementar a lógica de tick_update completa (SHIELD, trailing, danger)
- Resolver qualquer import error
- **Se algum módulo live não puder ser importado directamente** (ex: `event_processor` que depende de MT5), reportar e pedir orientação em vez de reescrever lógica do zero

---

## PASSO 3 — Outputs obrigatórios

Directoria de output: `C:\FluxQuantumAI\backtests\FASE_8_<timestamp>\`

### 3.1 `results_summary.json`

```json
{
  "backtest_id": "FASE_8_20260418_HHMMSS",
  "config": "APEX_actual_post_Fase7",
  "account": "RoboForex_demo_68302120",
  "period_start": "2025-07-01",
  "period_end": "2026-04-18",
  "gap_excluded": {"start": "2026-01-24", "end": "2026-02-01"},
  "total_days_traded": 290,
  "lot_sizing": "session_based_barbara_approved",
  "engine_used": "existing_OR_new_fase_8_backtest.py",
  "data_source": "calibration_dataset_full.parquet",

  "metrics_overall": {
    "total_signals_evaluated": 0,
    "total_blocks": 0,
    "total_trades_opened": 0,
    "block_rate_pct": 0.0,
    "winning_trades": 0,
    "losing_trades": 0,
    "breakeven_trades": 0,
    "win_rate_pct": 0.0,
    "profit_factor": 0.0,
    "accuracy_tp1_hit_pct": 0.0,
    "total_pnl_usd": 0.0,
    "avg_win_usd": 0.0,
    "avg_loss_usd": 0.0,
    "largest_win_usd": 0.0,
    "largest_loss_usd": 0.0,
    "max_drawdown_usd": 0.0,
    "max_drawdown_pct": 0.0,
    "sharpe_ratio": 0.0,
    "sortino_ratio": 0.0,
    "expectancy_usd": 0.0
  },

  "metrics_by_period": {
    "Q3_2025": {"start": "2025-07-01", "end": "2025-09-30", "metrics": { ... }},
    "Q4_2025": {"start": "2025-10-01", "end": "2025-12-31", "metrics": { ... }},
    "Q1_2026": {"start": "2026-01-01", "end": "2026-03-31", "metrics": { ... }},
    "Q2_2026_partial": {"start": "2026-04-01", "end": "2026-04-18", "metrics": { ... }}
  },

  "metrics_by_session": {
    "Asian":  { ... },
    "London": { ... },
    "NY":     { ... }
  },

  "metrics_by_strategy": {
    "ALPHA":  { ... },
    "GAMMA":  { "note": "GHOST — should be 0 trades" },
    "DELTA":  { "note": "GHOST — should be 0 trades" }
  },

  "metrics_by_direction": {
    "LONG":  { ... },
    "SHORT": { ... }
  },

  "exit_breakdown": {
    "tp1_then_tp2": 0,
    "tp1_then_trailing": 0,
    "tp1_then_sl_on_leg2_3": 0,
    "sl_all_legs": 0,
    "regime_flip": 0,
    "l2_danger": 0,
    "news_exit": 0,
    "shield_saved_from_loss": 0
  }
}
```

### 3.2 `trades_detailed.csv`

Uma linha por trade com todas as colunas listadas abaixo. **Sem excepções.**

```
trade_id, period, timestamp_entry, timestamp_exit, duration_minutes,
session, direction, strategy,
entry_price, sl, tp1, tp2,
lot_leg1, lot_leg2, lot_runner,
leg1_result, leg1_exit_price, leg1_pnl_usd,
leg2_result, leg2_exit_price, leg2_pnl_usd,
leg3_result, leg3_exit_price, leg3_pnl_usd,
total_pnl_usd,
mae_pts, mfe_pts,
shield_activated, trailing_activated,
regime_flip_exit, l2_danger_exit, news_exit,
gate_v1_pass, gate_v2_pass, gate_v3_pass, gate_v4_pass,
gate_v4_iceberg_aligned, gate_v4_iceberg_detected,
box_m30_high, box_m30_low, h4_bias, d1_bias,
delta_4h_at_entry, dom_imbalance_at_entry, absorption_ratio_at_entry,
news_score_at_entry
```

### 3.3 `losses_forensic_report.md`

**Para CADA trade net-negativo** (total_pnl_usd < 0), gerar entrada no formato:

```markdown
## LOSS #N — PnL -$X.XX — [YYYY-MM-DD HH:MM UTC] [SESSION]

### Setup
- Direction: LONG/SHORT
- Strategy: ALPHA/GAMMA/DELTA
- Entry: 4895.40
- SL: 4903.20 (risk: 78pts)
- TP1: 4880.00 | TP2: 4865.00
- Lots: 0.03/0.02/0.01 (NY session)
- Box M30: high=4897, low=4880
- H4 bias: SHORT | D1 bias: NEUTRAL

### Gate decisions at entry
| Gate | Pass | Detail |
|---|---|---|
| V1 (zone) | ✅ | within box |
| V2 (L2) | ✅ | DOM imbalance=-0.38, absorption=2.1 |
| V3 (momentum) | ✅ | delta_4h=-0.022 (alignment with SHORT) |
| V4 (iceberg) | ✅ | no opposing iceberg; aligned=FALSE |

### Outcome
- Leg1 (0.03): SL_HIT @ 4903.20 (-$23.40)
- Leg2 (0.02): SL_HIT @ 4903.20 (-$15.60)
- Runner (0.01): SL_HIT @ 4903.20 (-$7.80)
- **Total: -$46.80**
- Duration: 14min
- MAE: 82pts (4pts beyond SL due to slippage)
- MFE: 3pts (never threatened TP1)

### What happened forward (15/30/60 min after exit)
- +15min: price 4838 → **would have hit TP1** (+20pts = +$12)
- +30min: price 4825 → **would have hit TP2** (+50pts = +$30)
- +60min: price 4815
- Pattern: **False SL hit — stop-hunt then reversal**

### Contributing factors detected
- Iceberg event 2min BEFORE entry: 30000 lots absorbed at 4893 (opposite side, size=12.5)
  → V4 gate lookback=3min but actual event 2min back → threshold CAL-1=12.28 had been marginally exceeded but gate reported "no aligned"
- News event 5min AFTER entry: FOMC minutes release at 19:00 UTC
  → news_gate pre-window=30min, entry at 18:54 → within window but check timing missed

### Diagnostic category
- [X] Stop-hunt / manipulation
- [ ] Counter-trend entry
- [ ] Wrong session
- [ ] News-related
- [X] V4 iceberg detection miss

### Recommendations
1. **Consider CAL-X** — V4 iceberg lookback extend from 3min to 5min
2. **Consider CAL-Y** — news_gate pre-window from 30min to 45min during FOMC week
3. **Pattern flag** — detect stop-hunt: when MAE > 1.3×SL_risk AND reversal >0.5×SL within 15min → flag for review

---
```

**Estrutura agregada no topo:**

```markdown
# FORENSIC REPORT — APEX Losses (Jul 2025 → Apr 2026)

## Summary

Total losses analysed: N
Total loss PnL: -$X.XX
Average loss: -$X.XX
Losses by category:
- Stop-hunt / manipulation: X%
- Counter-trend: X%
- News-related: X%
- V4 iceberg miss: X%
- L2 danger late detection: X%
- Other: X%

## Top 10 worst losses (by absolute PnL)
1. LOSS #47 — -$46.80 (ver detalhe abaixo)
2. ...

## Systematic patterns detected
- [Pattern A]: observed in N trades, total PnL impact -$X
- [Pattern B]: observed in N trades, total PnL impact -$X

## Priority recommendations

### HIGH priority (biggest PnL impact)
1. [Recommendation 1] — estimated PnL improvement: +$XXX
2. ...

### MEDIUM priority
...

### LOW priority (cosmetic/edge cases)
...

---

## Individual loss details (chronological)

[All N losses listed with format above]
```

### 3.4 `opportunities_lost_report.md`

**Para TOP 50 BLOCKs por movimento subsequente** (ordenados por "would-have-been PnL" se tivessem sido traded):

```markdown
## OPPORTUNITY #N — Would-have-been LONG @ 4867.20 [YYYY-MM-DD HH:MM UTC] [NY]

### Intended setup
- Direction: LONG
- Entry: 4867.20
- SL: 4859.40 (78 pts risk)
- TP1: 4882.60 | TP2: 4898.00
- Lots would be: 0.03/0.02/0.01

### Block reason
- Blocked by: V4 (iceberg aligned SHORT)
- Gate details: absorption=8.5 < threshold 12.28 (weak iceberg, near threshold)

### Forward outcome (actual market data)
- +15min: price 4884 → **would have hit TP1** (+$12)
- +45min: price 4898 → **would have hit TP2** (+$30)
- +60min: price 4895 (still in profit)
- Would-have-been PnL: +$42 if both TPs hit

### Pattern
- Iceberg absorption was 8.5 — below threshold 12.28
- But post-hoc analysis suggests this was **false positive** (iceberg dissipated in 5min)
- Possible over-calibration of CAL-1 absorption threshold

### Recommendation
- **Consider CAL-1 review** — may be too strict in low-liquidity sessions
- **Add "iceberg persistence check"** — require absorption sustained for N ticks before blocking

---
```

**Estrutura agregada no topo:**

```markdown
# OPPORTUNITIES LOST REPORT (Top 50)

## Summary
Total BLOCKs in period: NN,NNN
Total would-have-been trades (subset analysed): 50
Total would-have-been PnL (conservative, only TP1): +$XXX
Total would-have-been PnL (optimistic, TP2 all): +$XXX

## Block reasons breakdown (all BLOCKs)
- V1 structure fail: XX%
- V2 L2 fail: XX%
- V3 momentum fail: XX%
- V4 iceberg block: XX%
- Operational rules (max_positions, etc): XX%

## Patterns in missed opportunities
- [Pattern X]: N blocked opportunities, total missed PnL +$XXX
- ...

## Priority recommendations to unlock missed PnL
[prioritized list]
```

### 3.5 `executive_summary.md`

Sumário executivo de 2-3 páginas:

```markdown
# APEX Backtest Diagnostic — Executive Summary

**Período:** Jul 2025 → Abr 2026 (10 meses)
**Configuração:** APEX actual pós-Fase 7 (Sprint 8 activo)
**Conta simulada:** RoboForex demo 68302120

## Key findings

### 1. PnL global
- **Total PnL: $XXX** over 290 trading days
- **Average per day: $X.XX**
- **Target sustainable weekly ($700): achieved / not achieved**

### 2. Performance temporal
- Q3-2025: PF X.XX, PnL $XXX — [context]
- Q4-2025: PF X.XX, PnL $XXX — [context]
- Q1-2026: PF X.XX, PnL $XXX — [context]
- Q2-2026 parcial: PF X.XX, PnL $XXX
- **Degradation detected:** YES/NO (in which quarter?)

### 3. Performance por sessão
- Asian: [verdict]
- London: [verdict — Barbara's historical concern]
- NY: [verdict]

### 4. Top 3 fontes de losses
1. [Category]: N trades, total -$XXX
2. [Category]: N trades, total -$XXX
3. [Category]: N trades, total -$XXX

### 5. Top 3 oportunidades perdidas
1. [Pattern]: N blocks, missed +$XXX
2. ...

## Recommendations (prioritized by estimated PnL impact)

### TOP 3 HIGH IMPACT
1. [Recommendation]: estimated +$XXX/month
2. [Recommendation]: estimated +$XXX/month
3. [Recommendation]: estimated -$XXX loss reduction/month

### MEDIUM IMPACT
...

### NEXT STEPS PROPOSTOS
1. Implement [X] — 1 day
2. Recalibrate [Y] — 0.5 day
3. Consider [Z] for NextGen ML
```

---

## PASSO 4 — Cross-validation com trades live reais

Comparar outputs do backtest com `C:\FluxQuantumAI\logs\trades.csv`:

```python
# Verificar se backtest reproduz aprox os ~93 trades reais que existem
real_trades = pd.read_csv(r"C:\FluxQuantumAI\logs\trades.csv")
backtest_trades_in_same_period = trades_detailed[
    (trades_detailed.timestamp_entry >= real_trades.timestamp.min()) &
    (trades_detailed.timestamp_entry <= real_trades.timestamp.max())
]

print(f"Real trades in period: {len(real_trades)}")
print(f"Backtest trades in period: {len(backtest_trades_in_same_period)}")
print(f"Match rate (aprox): {...}")
```

**Se backtest produz muito mais ou muito menos trades que os reais**, isto é sinal de que backtest não está a reproduzir fielmente a lógica live. **Reportar em executive_summary.md como limitação.**

---

## PASSO 5 — Final report

Report final em `C:\FluxQuantumAI\FASE_8_BACKTEST_REPORT_<timestamp>.md`:

```markdown
# FASE 8 BACKTEST — Execution Report

**Timestamp:** <UTC>
**Duration:** <hours>
**Status:** ✅ SUCCESS / ⚠ PARTIAL / ❌ FAILED

## Engine used
- Existing (path) / New (path)
- Justification: [...]

## Data coverage achieved
- Expected period: Jul 2025 → Apr 2026
- Actual period processed: [...]
- Gaps encountered: [...]

## Execution stats
- Total ticks processed: NNN,NNN,NNN
- Gate evaluations: NN,NNN
- Signals generated (GO): NNN
- Trades opened: NNN
- Runtime: X hours Y minutes

## Outputs produced
- [X] results_summary.json
- [X] trades_detailed.csv
- [X] losses_forensic_report.md  (N losses documented)
- [X] opportunities_lost_report.md  (50 top opportunities)
- [X] executive_summary.md

## Limitations / caveats
- [List any known limitations: data gaps, logic approximations, etc.]

## Cross-validation with real trades
- Real trades in backtest period: X
- Backtest trades in same period: Y
- Match assessment: [comment]

## Next steps
PARAR. Barbara + Claude review de executive_summary.md + forensic report.
Decidir que recommendations implementar e em que ordem.
```

---

## PROIBIDO NESTA FASE

- ❌ Modificar qualquer ficheiro em `C:\FluxQuantumAI\live\`
- ❌ Modificar `settings.json`
- ❌ Modificar parquets de entrada
- ❌ Parar qualquer serviço ou capture process
- ❌ "Melhorar" a lógica do APEX durante backtest (é READ-ONLY de lógica)
- ❌ Produzir outputs sem forensic per-loss
- ❌ Analisar menos de 50 opportunities lost

---

## COMUNICAÇÃO FINAL

Se SUCCESS:
```
FASE 8 BACKTEST — SUCCESS
Period: Jul 2025 → Apr 2026
Trades generated: N (cross-validation vs real: X/Y match)
Total PnL: $XXX
Losses with forensic: N
Opportunities lost analysed: 50
Outputs: C:\FluxQuantumAI\backtests\FASE_8_<timestamp>\
Report: C:\FluxQuantumAI\FASE_8_BACKTEST_REPORT_<timestamp>.md

Aguardando Barbara+Claude review.
```

Se PARTIAL:
```
FASE 8 BACKTEST — PARTIAL
Completed: [what was done]
Blocked at: [where and why]
Partial outputs: [paths]
Next: [what's needed to complete]
```

Se FAILED:
```
FASE 8 BACKTEST — FAILED
Failure point: [description]
Root cause: [analysis]
Recommendations: [how to proceed]
```

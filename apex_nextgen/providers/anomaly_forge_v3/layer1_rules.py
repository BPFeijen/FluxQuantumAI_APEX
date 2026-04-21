"""
layer1_rules.py — AnomalyForge V3: Layer 1 Rule-Based Engine
=============================================================

6 regras hardcoded usando dados de microestrutura (microstructure_*.csv.gz).
Não requer modelo treinado — deploy imediato.

Regras:
    R1 — Spread spike:       spread > 5× rolling median (30min)
    R2 — Depth collapse:     total book depth cai >50% em 5min
    R3 — Toxicity sustained: toxicity_score > 0.65 em 5+ leituras consecutivas
    R4 — Volume spike:       volume_per_second > μ + 10σ (sessão)
    R5 — Sweep detected:     sweep_detected=True + levels_swept > 3
    R6 — DOM extreme:        abs(dom_imbalance) > 0.80 em 3+ leituras consecutivas

Output per rule: True (WARNING fired) | False (CLEAR)
Aggregation: 0 → CLEAR | 1 → SOFT_VETO | 2+ → HARD_VETO
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd


# ── Configuração das Regras ────────────────────────────────────────────────────

SPREAD_SPIKE_FACTOR         = 5.0    # R1: spread > N × median
SPREAD_ROLLING_MIN          = 30     # R1: minutos da janela rolling
DEPTH_COLLAPSE_THRESHOLD    = 0.50   # R2: queda de 50% em depth total
DEPTH_COLLAPSE_WINDOW_MIN   = 5      # R2: janela de 5 minutos
TOXICITY_THRESHOLD          = 0.65   # R3: toxicity_score acima deste valor
TOXICITY_CONSECUTIVE_MIN    = 5      # R3: mínimo de leituras consecutivas acima do threshold
VOLUME_SPIKE_SIGMA          = 10.0   # R4: volume_per_second > μ + N×σ
SWEEP_LEVELS_MIN            = 3      # R5: levels_swept acima deste valor
DOM_EXTREME_THRESHOLD       = 0.80   # R6: |dom_imbalance| acima deste valor
DOM_EXTREME_CONSECUTIVE_MIN = 3      # R6: mínimo de leituras consecutivas


@dataclass
class RuleResult:
    """Resultado de uma regra individual."""
    rule_id:   str
    fired:     bool
    value:     float = 0.0        # valor que disparou a regra
    threshold: float = 0.0        # threshold que foi ultrapassado
    detail:    str = ""


@dataclass
class Layer1Decision:
    """Decisão final do Layer 1."""
    warning_count: int
    veto_level:    str                  # "CLEAR" | "SOFT_VETO" | "HARD_VETO"
    size_mult:     float                # 1.0 | 0.75 | 0.0
    rules:         List[RuleResult] = field(default_factory=list)
    data_rows:     int = 0              # quantas linhas foram usadas
    data_minutes:  float = 0.0         # janela temporal real dos dados

    @property
    def fired_rules(self) -> List[str]:
        return [r.rule_id for r in self.rules if r.fired]

    def to_dict(self) -> dict:
        return {
            "veto_level":    self.veto_level,
            "warning_count": self.warning_count,
            "size_mult":     self.size_mult,
            "fired_rules":   self.fired_rules,
            "data_rows":     self.data_rows,
            "data_minutes":  round(self.data_minutes, 1),
            "rule_details":  [
                {"id": r.rule_id, "fired": r.fired, "value": round(r.value, 4),
                 "threshold": round(r.threshold, 4), "detail": r.detail}
                for r in self.rules
            ],
        }


# ── Regras Individuais ─────────────────────────────────────────────────────────

def rule_spread_spike(df: pd.DataFrame) -> RuleResult:
    """
    R1 — Spread spike: spread actual > 5× mediana rolling 30min.
    Detecta: illiquidez súbita, gap de preço, news non-scheduled.
    """
    if "spread" not in df.columns or len(df) < 2:
        return RuleResult("R1_SPREAD_SPIKE", False, detail="insufficient_data")

    spread = pd.to_numeric(df["spread"], errors="coerce")
    if spread.isna().all():
        return RuleResult("R1_SPREAD_SPIKE", False, detail="no_spread_data")

    # Mediana rolling 30min (df indexado por timestamp, 1 linha ≈ cada push do Quantower)
    roll_window = max(2, min(SPREAD_ROLLING_MIN * 4, len(spread)))
    min_p = min(3, roll_window)
    rolling_med = spread.rolling(window=roll_window, min_periods=min_p).median()

    current_spread = float(spread.iloc[-1])
    last_median    = float(rolling_med.iloc[-1])

    if last_median <= 0:
        return RuleResult("R1_SPREAD_SPIKE", False, detail="zero_median")

    ratio = current_spread / last_median
    fired = ratio > SPREAD_SPIKE_FACTOR

    return RuleResult(
        "R1_SPREAD_SPIKE",
        fired,
        value=ratio,
        threshold=SPREAD_SPIKE_FACTOR,
        detail=f"spread={current_spread:.3f} median={last_median:.3f} ratio={ratio:.2f}×",
    )


def rule_depth_collapse(df: pd.DataFrame) -> RuleResult:
    """
    R2 — Depth collapse: book total (bid+ask) cai >50% em 5 minutos.
    Detecta: retirada massiva de liquidez antes de movimento brusco.
    """
    needed = ["total_bid_size", "total_ask_size"]
    if not all(c in df.columns for c in needed) or len(df) < 4:
        return RuleResult("R2_DEPTH_COLLAPSE", False, detail="insufficient_data")

    bid  = pd.to_numeric(df["total_bid_size"], errors="coerce")
    ask  = pd.to_numeric(df["total_ask_size"], errors="coerce")
    total = bid + ask

    if total.isna().all() or total.iloc[-1] == 0:
        return RuleResult("R2_DEPTH_COLLAPSE", False, detail="no_depth_data")

    # Baseline: média das últimas 5min (≈ 20 leituras a 15s)
    window = min(DEPTH_COLLAPSE_WINDOW_MIN * 4, max(3, len(total) - 1))
    baseline = float(total.iloc[:-1].rolling(window=window, min_periods=2).mean().iloc[-1])
    current  = float(total.iloc[-1])

    if baseline <= 0:
        return RuleResult("R2_DEPTH_COLLAPSE", False, detail="zero_baseline")

    drop_pct = (baseline - current) / baseline
    fired = drop_pct > DEPTH_COLLAPSE_THRESHOLD

    return RuleResult(
        "R2_DEPTH_COLLAPSE",
        fired,
        value=drop_pct,
        threshold=DEPTH_COLLAPSE_THRESHOLD,
        detail=f"current_depth={current:.0f} baseline={baseline:.0f} drop={drop_pct*100:.1f}%",
    )


def rule_toxicity_sustained(df: pd.DataFrame) -> RuleResult:
    """
    R3 — Toxicity sustained: toxicity_score > 0.65 em 5+ leituras consecutivas.
    Detecta: fluxo tóxico persistente (informed traders, spoofing detection do Quantower).
    toxicity_score já é calculado internamente pelo Quantower (similar a VPIN).
    """
    if "toxicity_score" not in df.columns or len(df) < TOXICITY_CONSECUTIVE_MIN:
        return RuleResult("R3_TOXICITY_SUSTAINED", False, detail="insufficient_data")

    tox = pd.to_numeric(df["toxicity_score"], errors="coerce").fillna(0)
    above = (tox > TOXICITY_THRESHOLD)

    # Contar consecutivos no final da série
    consecutive = 0
    for val in reversed(above.values):
        if val:
            consecutive += 1
        else:
            break

    current_tox = float(tox.iloc[-1])
    fired = consecutive >= TOXICITY_CONSECUTIVE_MIN

    return RuleResult(
        "R3_TOXICITY_SUSTAINED",
        fired,
        value=float(consecutive),
        threshold=float(TOXICITY_CONSECUTIVE_MIN),
        detail=f"toxicity={current_tox:.3f} consecutive={consecutive}/{TOXICITY_CONSECUTIVE_MIN}",
    )


def rule_volume_spike(df: pd.DataFrame) -> RuleResult:
    """
    R4 — Volume spike: volume_per_second > μ + 10σ da sessão.
    Detecta: actividade institucional súbita, news-driven flush.
    """
    if "volume_per_second" not in df.columns or len(df) < 10:
        return RuleResult("R4_VOLUME_SPIKE", False, detail="insufficient_data")

    vol = pd.to_numeric(df["volume_per_second"], errors="coerce").dropna()
    if len(vol) < 5:
        return RuleResult("R4_VOLUME_SPIKE", False, detail="insufficient_data")

    session_mu  = float(vol.mean())
    session_std = float(vol.std())

    if session_std == 0:
        return RuleResult("R4_VOLUME_SPIKE", False, detail="zero_std")

    current_vol = float(vol.iloc[-1])
    z_score     = (current_vol - session_mu) / session_std
    threshold   = VOLUME_SPIKE_SIGMA
    fired       = z_score > threshold

    return RuleResult(
        "R4_VOLUME_SPIKE",
        fired,
        value=z_score,
        threshold=threshold,
        detail=f"vol/s={current_vol:.2f} μ={session_mu:.2f} σ={session_std:.2f} z={z_score:.1f}",
    )


def rule_sweep_detected(df: pd.DataFrame) -> RuleResult:
    """
    R5 — Sweep detected: sweep_detected=True + levels_swept > 3.
    Detecta: agressão institucional atravessando vários níveis do livro.
    """
    needed = ["sweep_detected", "levels_swept"]
    if not all(c in df.columns for c in needed) or len(df) < 1:
        return RuleResult("R5_SWEEP_DETECTED", False, detail="insufficient_data")

    # Olhar apenas para as últimas 5 linhas (evento recente)
    recent = df.tail(5)
    sweep_flags = recent["sweep_detected"].astype(str).str.lower()
    levels      = pd.to_numeric(recent["levels_swept"], errors="coerce").fillna(0)

    any_sweep_strong = False
    max_levels = 0.0
    for flag, lvl in zip(sweep_flags, levels):
        if flag in ("true", "1") and float(lvl) > SWEEP_LEVELS_MIN:
            any_sweep_strong = True
            max_levels = max(max_levels, float(lvl))

    return RuleResult(
        "R5_SWEEP_DETECTED",
        any_sweep_strong,
        value=max_levels,
        threshold=float(SWEEP_LEVELS_MIN),
        detail=f"levels_swept={max_levels:.0f} threshold={SWEEP_LEVELS_MIN}",
    )


def rule_dom_extreme(df: pd.DataFrame) -> RuleResult:
    """
    R6 — DOM extreme: |dom_imbalance| > 0.80 em 3+ leituras consecutivas.
    Detecta: order book one-sided extremo sustentado (pressure wall ou spoofing).
    """
    if "dom_imbalance" not in df.columns or len(df) < DOM_EXTREME_CONSECUTIVE_MIN:
        return RuleResult("R6_DOM_EXTREME", False, detail="insufficient_data")

    dom = pd.to_numeric(df["dom_imbalance"], errors="coerce").fillna(0)
    extreme = (dom.abs() > DOM_EXTREME_THRESHOLD)

    # Contar consecutivos no final
    consecutive = 0
    for val in reversed(extreme.values):
        if val:
            consecutive += 1
        else:
            break

    current_dom = float(dom.iloc[-1])
    fired = consecutive >= DOM_EXTREME_CONSECUTIVE_MIN

    return RuleResult(
        "R6_DOM_EXTREME",
        fired,
        value=abs(current_dom),
        threshold=DOM_EXTREME_THRESHOLD,
        detail=f"dom={current_dom:.3f} consecutive={consecutive}/{DOM_EXTREME_CONSECUTIVE_MIN}",
    )


# ── Aggregation ───────────────────────────────────────────────────────────────

def evaluate_all_rules(df: pd.DataFrame) -> Layer1Decision:
    """
    Aplica as 6 regras e agrega o resultado.

    Args:
        df: DataFrame com colunas de microestrutura, indexado por timestamp,
            ordenado cronologicamente. Cobre uma janela de tempo recente.

    Returns:
        Layer1Decision com veto_level, size_mult e detalhes por regra.
    """
    if df is None or len(df) == 0:
        return Layer1Decision(
            warning_count=0,
            veto_level="CLEAR",
            size_mult=1.0,
            rules=[],
            data_rows=0,
            data_minutes=0.0,
        )

    results = [
        rule_spread_spike(df),
        rule_depth_collapse(df),
        rule_toxicity_sustained(df),
        rule_volume_spike(df),
        rule_sweep_detected(df),
        rule_dom_extreme(df),
    ]

    warnings = sum(1 for r in results if r.fired)

    if warnings >= 2:
        veto_level = "HARD_VETO"
        size_mult  = 0.0
    elif warnings == 1:
        veto_level = "SOFT_VETO"
        size_mult  = 0.75
    else:
        veto_level = "CLEAR"
        size_mult  = 1.0

    # Calcular janela temporal
    data_minutes = 0.0
    try:
        if hasattr(df.index, "min") and hasattr(df.index, "max"):
            delta = df.index.max() - df.index.min()
            data_minutes = delta.total_seconds() / 60
    except Exception:
        pass

    return Layer1Decision(
        warning_count=warnings,
        veto_level=veto_level,
        size_mult=size_mult,
        rules=results,
        data_rows=len(df),
        data_minutes=data_minutes,
    )

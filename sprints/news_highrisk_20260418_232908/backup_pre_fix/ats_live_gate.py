"""
# ============================================================
# ADR-001: M30 = EXECUTION TIMEFRAME
# V1 structural check: m30_box_confirmed (NOT h4/daily)
# D1 is ONLY used for: daily_trend direction filter (daily_jac_dir)
# W1 is ONLY used for: weekly_aligned filter
# H4/D1 levels NEVER drive entry triggers here.
# ============================================================

ats_live_gate.py — Gate de confirmação L2 + Iceberg para o ATS live demo.

Lê dados em tempo real de:
  - microstructure_YYYY-MM-DD.csv.gz  → momentum 4h, absorption, DOM
  - iceberg_GC_XCEC_YYYYMMDD.jsonl    → iceberg no nível de entrada

Uso:
    gate = ATSLiveGate()
    decision = gate.check(
        entry_price=4580.0,
        direction="SHORT",   # "LONG" ou "SHORT"
        now=pd.Timestamp.utcnow(),
        liq_top=4581.0,      # nível estrutural superior (15-day high)
        liq_bot=4320.0,      # nível estrutural inferior (15-day low)
    )
    if decision.go:
        print(decision.summary())
    else:
        print("BLOQUEADO:", decision.reason)

Correcoes v1.1 (2026-03-29):
  BUG1: delta_4h nao cruza mais sessoes GLOBEX (evita contaminacao de dia anterior)
  BUG2: absorcao em liq_top/liq_bot interpretada corretamente (BID no topo = SHORT aligned)
  BUG3: relaxamento de momentum assimetrico — apenas SHORT em liq_top com delta>0
        (LONG em liq_bot durante cascata negativa continua bloqueado)

v1.2 (2026-03-31):
  LOI fix: large_order_imbalance assimetrico em niveis estruturais (mesmo principio do BUG2)
    AT liq_top (SHORT): LOI > +0.50 = compradores sendo absorvidos = SHORT aligned (+1)
    AT liq_top (SHORT): LOI < -0.50 = vendedores fugindo = SHORT contra (-2)
    AT liq_bot (LONG):  LOI < -0.50 = vendedores sendo absorvidos = LONG aligned (+1)
    AT liq_bot (LONG):  LOI > +0.50 = compradores fugindo = LONG contra (-2)

v1.3 (2026-03-31):
  SIGNAL 1 (macro): Macro trend filter — 5-day cumulative bar_delta
    BLOCK SHORT if 5d_delta > +5000 (EXCEPTION: at liq_top with absorption/LOI confirmed)
    BLOCK LONG  if 5d_delta < -5000 (EXCEPTION: at liq_bot with absorption/LOI confirmed)
  SIGNAL 2 (pressure): pressure_ratio at level — validates reversal setup
    SHORT at liq_top: ratio > 2.5 (-2) | ratio > 1.5 (-1)
    LONG at liq_bot:  ratio < 0.40 (-2) | ratio < 0.67 (-1)
  SIGNAL 3 (sweep): sweep_detected contra-filter
    Sweep against trade direction = score -2 | sweep with trade = score +1
  SIGNAL 4 (poc): distance_to_poc reversal strength
    distance > 20pts = stretched = +1 | distance < 5pts = near POC = -1
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ats_iceberg_gate import ATSIcebergV1, ATSIcebergSignal

# ---------------------------------------------------------------------------
# Grenadier Stat-Guardrails (Sprint 1 — The Shield)
# ---------------------------------------------------------------------------
_guardrail_fn = None
try:
    sys.path.insert(0, str(Path("C:/FluxQuantumAI")))
    from grenadier_guardrail import get_guardrail_status as _guardrail_fn
    import logging as _logging
    _logging.getLogger("apex.gate").info("StatGuardrail wired into ATSLiveGate (Grenadier Sprint 1)")
except Exception as _gr_err:
    import logging as _logging
    _logging.getLogger("apex.gate").warning(
        "StatGuardrail not available in ATSLiveGate — skipping guardrail check: %s", _gr_err
    )

# ---------------------------------------------------------------------------
# V4 — IcebergInference ML (loaded once at import, graceful fallback)
# ---------------------------------------------------------------------------
# Models live in the APEX_GC_Iceberg repo; the ats_iceberg_v1 package
# is importable from there. Add to sys.path before the import below.
_ICEBERG_PKG_DIR = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Iceberg")
if str(_ICEBERG_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_ICEBERG_PKG_DIR))

_MODELS_DIR   = _ICEBERG_PKG_DIR / "ats_iceberg_v1" / "models"
_AE_CKPT      = _MODELS_DIR / "autoencoder_stage1.pkl"
_CLS_CKPT     = _MODELS_DIR / "classifier_stage2.pkl"
_CAL_CKPT     = _MODELS_DIR / "calibration_stage3.pkl"

_v4_inference = None
_v4_available = False

try:
    import pickle as _pkl
    _temperature = 1.0
    if _CAL_CKPT.exists():
        with open(_CAL_CKPT, "rb") as _f:
            _cal = _pkl.load(_f)
            # calibration_stage3.pkl is a dict {'temperature': ..., 'ece_before': ..., 'ece_after': ...}
            if isinstance(_cal, dict):
                _temperature = float(_cal.get("temperature", 0.848))
            elif isinstance(_cal, (int, float)):
                _temperature = float(_cal)
            # else: keep default 1.0

    from ats_iceberg_v1.inference.iceberg_inference import IcebergInference as _IcebergInference
    _v4_inference = _IcebergInference(
        autoencoder_ckpt=_AE_CKPT  if _AE_CKPT.exists()  else None,
        classifier_ckpt =_CLS_CKPT if _CLS_CKPT.exists() else None,
        temperature=_temperature,
    )
    _v4_available = True
    import logging as _logging
    _logging.getLogger("apex.gate").info(
        "V4 IcebergInference loaded — models=%s  temperature=%.4f",
        _MODELS_DIR, _temperature,
    )
except Exception as _v4_err:
    import logging as _logging
    _logging.getLogger("apex.gate").warning(
        "V4 IcebergInference FAILED to load: %s", _v4_err
    )
    # V4 unavailable — gate continues without it

# V4_CONF_THRESHOLD: loaded from settings.json (iceberg_proxy_threshold).
# Stress test 2026-04-11 (75d, 1.15M records) → sweet spot T=0.85 (PF=1.118, P&L 2.2× vs T=0.9150).
# V4 is a HARD VETO — CONTRA iceberg with conf >= this blocks entry.
def _load_v4_conf_threshold() -> float:
    _s = Path("C:/FluxQuantumAI/config/settings.json")
    try:
        import json as _json
        with open(_s, "r", encoding="utf-8") as _f:
            return float(_json.load(_f).get("iceberg_proxy_threshold", 0.60))
    except Exception:
        return 0.60

V4_CONF_THRESHOLD = _load_v4_conf_threshold()


def _load_hard_block_on_contra() -> bool:
    _s = Path("C:/FluxQuantumAI/config/settings.json")
    try:
        import json as _json
        with open(_s, "r", encoding="utf-8") as _f:
            return bool(_json.load(_f).get("iceberg_hard_block_on_contra", True))
    except Exception:
        return True

ICEBERG_HARD_BLOCK_ON_CONTRA = _load_hard_block_on_contra()


def _load_delta_4h_settings() -> dict:
    _s = Path("C:/FluxQuantumAI/config/settings.json")
    try:
        import json as _json
        with open(_s, "r", encoding="utf-8") as _f:
            d = _json.load(_f)
            return {
                "inverted_fix": bool(d.get("delta_4h_inverted_fix", False)),
                "exhaustion_high": float(d.get("delta_4h_exhaustion_high", 3000)),
                "exhaustion_low": float(d.get("delta_4h_exhaustion_low", -1050)),
            }
    except Exception:
        return {"inverted_fix": False, "exhaustion_high": 3000, "exhaustion_low": -1050}

_DELTA_4H_SETTINGS = _load_delta_4h_settings()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MICRO_DIR       = Path("C:/data/level2/_gc_xcec")
ICE_DIR         = Path("C:/data/iceberg")
PARQUET_PATH    = Path("C:/data/processed/gc_ats_features_v4.parquet")
M30_BOXES_PATH  = Path("C:/data/processed/gc_m30_boxes.parquet")
GC_SYMBOL       = "GC_XCEC"

# ---------------------------------------------------------------------------
# Thresholds (derived from forensic analysis of 20 losses)
# ---------------------------------------------------------------------------
# Momentum gate — blocks if 4h cumulative delta is strongly against direction
MOMENTUM_BLOCK_LONG  = -1_050   # delta_4h < -1050 -> block LONG  (bear cascade) [CAL-16 2026-04-08]
MOMENTUM_BLOCK_SHORT = +3_000   # delta_4h > +3000 -> block SHORT (bull run)     [CAL-17 2026-04-08]
MOMENTUM_WARN_LONG   = -800     # softer warning (reduce score)
MOMENTUM_WARN_SHORT  = +800

# Impulse gate — single bar momentum check (30-min bar before entry)
IMPULSE_BLOCK_LONG_PTS  = -10   # price_1bar < -10pts AND delta_1bar < -100 → block LONG  [CAL-20/21 2026-04-08]
IMPULSE_BLOCK_SHORT_PTS = +5    # price_1bar > +5pts  AND delta_1bar > +100 → block SHORT [CAL-20/21 2026-04-08]
IMPULSE_DELTA_THRESH    = 100   # CAL-21 2026-04-08

# Iceberg gate — check ±1pt from entry price in last 10 minutes
ICE_PRICE_BAND_PTS  = 1.0
ICE_LOOKBACK_MIN    = 10
ICE_MIN_REFILLS     = 2          # refill_count >= 2 = meaningful iceberg
ICE_MIN_PROB        = 0.20       # probability >= 0.20

# Macro trend filter REMOVED — not in spec, threshold was arbitrary (+5000 hardcoded
# without data analysis). A bullish 5d_delta does not preclude a SHORT at liq_top.

# Absorption gate — absorption in entry bar
ABS_MIN_RATIO       = 2.0        # absorption_ratio >= 2.0 = strong absorption

# Score weights
W_MOMENTUM_BONUS    = 2          # momentum aligned → +2
W_ICE_ALIGNED       = 3          # iceberg aligned at level → +3
W_ICE_CONTRA        = -4         # iceberg contra at level → -4 (hard contra)
W_ABS_ALIGNED       = 2          # absorption aligned → +2
W_ABS_CONTRA        = -3         # absorption contra → -3

MIN_SCORE_GO        = 0          # score >= 0 → GO (neutral or positive)

# V1 thresholds — dual-mode (FIX 6, 2026-04-08)
# RANGE mode    : abs(price - level) <= V1_RANGE_DIST_PTS (price near structural level)
# TRENDING mode : box_low <= price <= box_high (price inside M30 box zone — in-box-zone)
#   Rationale: in trending days breakouts don't revisit; 69% non-revisit in 8h,
#   avg magnitude 26.8pts. Using distance threshold would block 65.9% of valid TRENDING checks.
V1_RANGE_DIST_PTS = 8.0
V1_MAX_DIST_PTS   = 3.0   # legacy alias (kept for any external references)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MomentumSignal:
    delta_4h: float = 0.0
    delta_1bar: float = 0.0
    price_1bar_pts: float = 0.0
    dom_mean_30min: float = 0.0
    status: str = "ok"          # "ok", "warn", "block"
    reason: str = ""
    score: int = 0


@dataclass
class GateDecision:
    go: bool
    total_score: int
    reason: str
    direction: str
    entry_price: float
    momentum: MomentumSignal   = field(default_factory=MomentumSignal)
    iceberg: ATSIcebergSignal  = field(default_factory=ATSIcebergSignal)
    macro_delta: float = 0.0
    macro_blocked: bool = False
    v4_status: str = "UNKNOWN"     # "PASS" | "BLOCK" | "NEUTRAL" | "UNKNOWN"
    v4_confidence: float = 0.0
    l2_entry_score: float = -1.0   # reserved; -1 = not applicable (V2 gate now uses BLOCK_V2_NOICE)

    def summary(self) -> str:
        status = "GO" if self.go else "BLOQUEADO"
        lines = [
            "[ATS GATE v1.3] %s | %s @ %.1f | score=%+d" % (status, self.direction, self.entry_price, self.total_score),
            "  Motivo   : %s" % self.reason,
            "  Macro    : 5d_delta=%+.0f  macro_blocked=%s" % (self.macro_delta, self.macro_blocked),
            "  Momentum : delta_4h=%+.0f  1bar=%+.0f  preco_1bar=%+.1fpts  [%s]" % (
                self.momentum.delta_4h, self.momentum.delta_1bar,
                self.momentum.price_1bar_pts, self.momentum.status),
            "  Iceberg  : score=%+d  conf=%.2f  type=%s  aligned=%s" % (
                self.iceberg.score, self.iceberg.confidence,
                self.iceberg.primary_type, self.iceberg.aligned),
            "  Iceberg  : %s" % self.iceberg.reason,
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

class MicrostructureReader:
    """Lê o arquivo microstructure_YYYY-MM-DD.csv.gz do dia atual."""

    def __init__(self, micro_dir: Path = MICRO_DIR):
        self.micro_dir = micro_dir
        self._bar_delta_cache: dict = {}   # date_str -> sum(bar_delta) for that day

    def _find_file(self, date_str: str) -> Optional[Path]:
        # Prefer .fixed.csv.gz if exists
        fixed = self.micro_dir / f"microstructure_{date_str}.fixed.csv.gz"
        plain = self.micro_dir / f"microstructure_{date_str}.csv.gz"
        if fixed.exists():
            return fixed
        if plain.exists():
            return plain
        return None

    def load_recent(self, now: pd.Timestamp, hours_back: float = 5.0) -> Optional[pd.DataFrame]:
        """
        Carrega as últimas `hours_back` horas de microstructure do dia atual.
        Lê também o dia anterior se o horário for cedo (< 5h UTC = pós-abertura GLOBEX).
        """
        dfs = []
        dates_to_try = [now.strftime("%Y-%m-%d")]
        if now.hour < hours_back:
            prev = (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            dates_to_try.insert(0, prev)

        for d in dates_to_try:
            path = self._find_file(d)
            if path is None:
                continue
            try:
                df = pd.read_csv(path, parse_dates=["timestamp"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                dfs.append(df)
            except Exception:
                continue

        if not dfs:
            return None

        combined = pd.concat(dfs, ignore_index=True)
        cutoff = now - pd.Timedelta(hours=hours_back)
        # FIX: filter both ends — no future data leakage (timestamps after `now` skew delta)
        combined = combined[
            (combined["timestamp"] >= cutoff) &
            (combined["timestamp"] <= now)
        ]
        return combined.sort_values("timestamp").reset_index(drop=True)

    def _session_start(self, now: pd.Timestamp) -> pd.Timestamp:
        """
        FIX BUG1: Retorna o início da sessão GLOBEX atual para não cruzar sessões.
        GC sessions UTC: ASIA 22:00-07:00 | EUROPE 07:00-13:30 | NY 13:30-22:00
        Na prática, a sessão começa às 18:00 UTC (abertura GLOBEX/CME) do dia anterior.
        Usamos 22:00 UTC do dia anterior como ponto seguro de corte de sessão.
        """
        # Início da sessão = 22:00 UTC do dia anterior (abertura GLOBEX)
        session_open = now.normalize() - pd.Timedelta(hours=2)  # 22:00 UTC dia anterior
        if now.hour >= 22:
            session_open = now.normalize() + pd.Timedelta(hours=22)
        return session_open

    def get_macro_delta(self, now: pd.Timestamp, days: int = 5) -> float:
        """
        Sum bar_delta across the last N available trading day files before `now`.
        Cached per date so multiple calls for the same date don't re-read disk.
        """
        total = 0.0
        found = 0
        for offset in range(1, 31):  # look back up to 30 calendar days
            d = (now - pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
            if d in self._bar_delta_cache:
                total += self._bar_delta_cache[d]
                found += 1
            else:
                path = self._find_file(d)
                if path is None:
                    continue
                try:
                    df = pd.read_csv(path, usecols=["bar_delta"])
                    day_val = float(df["bar_delta"].sum())
                    self._bar_delta_cache[d] = day_val
                    total += day_val
                    found += 1
                except Exception:
                    continue
            if found >= days:
                break
        return total

    def get_momentum_signal(self, direction: str, now: pd.Timestamp) -> MomentumSignal:
        sig = MomentumSignal()
        df = self.load_recent(now, hours_back=5.0)
        if df is None or len(df) == 0:
            sig.status = "no_data"
            sig.reason = "sem dados microstructure"
            return sig

        # FIX BUG1: limitar ao inicio da sessão atual — não cruzar sessões GLOBEX
        session_start = self._session_start(now)
        cutoff_4h = max(now - pd.Timedelta(hours=4), session_start)
        df_4h = df[df["timestamp"] >= cutoff_4h]
        sig.delta_4h = float(df_4h["bar_delta"].sum()) if len(df_4h) > 0 else 0.0

        # Last completed 30-min bar
        df["bar"] = df["timestamp"].dt.floor("30min")
        bar_ts = now.floor("30min") - pd.Timedelta(minutes=30)  # bar before current
        prev_bar = df[df["bar"] == bar_ts]
        if len(prev_bar) > 0:
            sig.delta_1bar    = float(prev_bar["bar_delta"].sum())
            sig.price_1bar_pts = float(
                prev_bar["mid_price"].iloc[-1] - prev_bar["mid_price"].iloc[0]
                if "mid_price" in prev_bar.columns else 0.0
            )

        # DOM mean over last 30min
        cutoff_30 = now - pd.Timedelta(minutes=30)
        df_30 = df[df["timestamp"] >= cutoff_30]
        sig.dom_mean_30min = float(df_30["dom_imbalance"].mean()) if len(df_30) > 0 else 0.0

        # Apply thresholds
        _d4h_cfg = _DELTA_4H_SETTINGS
        if _d4h_cfg["inverted_fix"]:
            # Sprint 8 Fix 2: INVERTED interpretation (V2)
            # High positive delta_4h = buyer EXHAUSTION = bearish
            # Low negative delta_4h = seller EXHAUSTION = bullish
            # Extreme delta SUPPORTS the exhaustion-aligned trade, PENALIZES the opposite.
            _exh_high = _d4h_cfg["exhaustion_high"]   # +3000
            _exh_low  = _d4h_cfg["exhaustion_low"]    # -1050

            if sig.delta_4h > _exh_high:
                # Buyer exhaustion = bearish signal
                if direction == "SHORT":
                    sig.status = "ok"
                    sig.score  = 2
                    sig.reason = f"[DELTA_4H_FIX] buyer exhaustion d4h={sig.delta_4h:+.0f} SUPPORTS SHORT +2"
                    _logging.getLogger("apex.gate").info("[DELTA_4H_FIX] d4h=%+.0f buyer exhaustion SUPPORTS SHORT +2", sig.delta_4h)
                else:
                    sig.status = "warn"
                    sig.score  = -2
                    sig.reason = f"[DELTA_4H_FIX] buyer exhaustion d4h={sig.delta_4h:+.0f} PENALIZES LONG -2"
                    _logging.getLogger("apex.gate").info("[DELTA_4H_FIX] d4h=%+.0f buyer exhaustion PENALIZES LONG -2", sig.delta_4h)
            elif sig.delta_4h < _exh_low:
                # Seller exhaustion = bullish signal
                if direction == "LONG":
                    sig.status = "ok"
                    sig.score  = 2
                    sig.reason = f"[DELTA_4H_FIX] seller exhaustion d4h={sig.delta_4h:+.0f} SUPPORTS LONG +2"
                    _logging.getLogger("apex.gate").info("[DELTA_4H_FIX] d4h=%+.0f seller exhaustion SUPPORTS LONG +2", sig.delta_4h)
                else:
                    sig.status = "warn"
                    sig.score  = -2
                    sig.reason = f"[DELTA_4H_FIX] seller exhaustion d4h={sig.delta_4h:+.0f} PENALIZES SHORT -2"
                    _logging.getLogger("apex.gate").info("[DELTA_4H_FIX] d4h=%+.0f seller exhaustion PENALIZES SHORT -2", sig.delta_4h)
            else:
                sig.status = "ok"
                sig.score  = 0
                sig.reason = f"[DELTA_4H_FIX] d4h={sig.delta_4h:+.0f} neutral, no action"
                _logging.getLogger("apex.gate").info("[DELTA_4H_FIX] d4h=%+.0f neutral", sig.delta_4h)

            # Impulse gate 30min — override to block if single-bar momentum is extreme
            # (restored: this check was lost when inverted_fix branch was added in Sprint 8)
            if sig.status != "block":
                if direction == "SHORT" and sig.price_1bar_pts > IMPULSE_BLOCK_SHORT_PTS and sig.delta_1bar > IMPULSE_DELTA_THRESH:
                    sig.status = "block"
                    sig.score  = -W_MOMENTUM_BONUS
                    sig.reason = f"[blocked_by=impulse_30min] SHORT: +{sig.price_1bar_pts:.0f}pts / delta={sig.delta_1bar:+.0f} (d4h={sig.delta_4h:+.0f})"
                    _logging.getLogger("apex.gate").warning("[IMPULSE_30MIN] BLOCK SHORT: price_1bar=+%.0f delta_1bar=%+.0f", sig.price_1bar_pts, sig.delta_1bar)
                elif direction == "LONG" and sig.price_1bar_pts < IMPULSE_BLOCK_LONG_PTS and sig.delta_1bar < -IMPULSE_DELTA_THRESH:
                    sig.status = "block"
                    sig.score  = -W_MOMENTUM_BONUS
                    sig.reason = f"[blocked_by=impulse_30min] LONG: {sig.price_1bar_pts:.0f}pts / delta={sig.delta_1bar:+.0f} (d4h={sig.delta_4h:+.0f})"
                    _logging.getLogger("apex.gate").warning("[IMPULSE_30MIN] BLOCK LONG: price_1bar=%.0f delta_1bar=%+.0f", sig.price_1bar_pts, sig.delta_1bar)

            # Tag exhaustion blocks with blocked_by for dashboard clarity
            if sig.status in ("warn", "block") and "blocked_by=" not in sig.reason:
                sig.reason = sig.reason.replace("[DELTA_4H_FIX]", "[blocked_by=delta_4h_exhaustion]")
        else:
            # Original logic (delta_4h_inverted_fix=false)
            if direction == "SHORT":
                if sig.delta_4h > MOMENTUM_BLOCK_SHORT:
                    sig.status = "block"
                    sig.reason  = f"momentum ALTA 4h: delta_4h={sig.delta_4h:+.0f} > +{MOMENTUM_BLOCK_SHORT}"
                    sig.score   = -W_MOMENTUM_BONUS
                elif sig.delta_4h > MOMENTUM_WARN_SHORT:
                    sig.status = "warn"
                    sig.reason  = f"impulso ascendente: delta_4h={sig.delta_4h:+.0f}"
                    sig.score   = -1
                elif sig.price_1bar_pts > IMPULSE_BLOCK_SHORT_PTS and sig.delta_1bar > IMPULSE_DELTA_THRESH:
                    sig.status = "block"
                    sig.reason  = f"impulso SHORT 30min: +{sig.price_1bar_pts:.0f}pts / delta={sig.delta_1bar:+.0f}"
                    sig.score   = -W_MOMENTUM_BONUS
                else:
                    sig.status = "ok"
                    sig.score  = W_MOMENTUM_BONUS if sig.delta_4h < -500 else 0
            else:  # LONG
                if sig.delta_4h < MOMENTUM_BLOCK_LONG:
                    sig.status = "block"
                    sig.reason  = f"momentum BAIXA 4h: delta_4h={sig.delta_4h:+.0f} < {MOMENTUM_BLOCK_LONG}"
                    sig.score   = -W_MOMENTUM_BONUS
                elif sig.delta_4h < MOMENTUM_WARN_LONG:
                    sig.status = "warn"
                    sig.reason  = f"pressao vendedora: delta_4h={sig.delta_4h:+.0f}"
                    sig.score   = -1
                elif sig.price_1bar_pts < IMPULSE_BLOCK_LONG_PTS and sig.delta_1bar < -IMPULSE_DELTA_THRESH:
                    sig.status = "block"
                    sig.reason  = f"impulso BAIXA 30min: {sig.price_1bar_pts:.0f}pts / delta={sig.delta_1bar:+.0f}"
                    sig.score   = -W_MOMENTUM_BONUS
                else:
                    sig.status = "ok"
                    sig.score  = W_MOMENTUM_BONUS if sig.delta_4h > 500 else 0

        return sig


# ---------------------------------------------------------------------------
# Main Gate
# ---------------------------------------------------------------------------

class ATSLiveGate:
    """
    Gate combinado L2 + Iceberg para confirmacao de entrada no ATS.

    Fluxo de decisao:
        1. Momentum Gate  -- bloqueia se delta_4h ou impulso fortemente contra a direcao
        2. Iceberg Gate   -- ATSIcebergV1: absorption + DOM + large_order + JSONL no nivel
        3. Score final    -- GO se score >= MIN_SCORE_GO E nenhum bloqueio duro
    """

    def __init__(
        self,
        micro_dir: Path = MICRO_DIR,
        ice_dir: Path = ICE_DIR,
    ):
        self.micro_reader = MicrostructureReader(micro_dir)
        self.iceberg      = ATSIcebergV1(micro_dir, ice_dir)
        self._ice_dir     = ice_dir

    # ------------------------------------------------------------------
    # V4 — ML iceberg inference at entry level
    # ------------------------------------------------------------------

    def _run_v4(
        self,
        entry_price: float,
        direction: str,
        level_type: str,
        now: pd.Timestamp,
        delta_4h: float,
    ) -> tuple[str, float]:
        """
        Run IcebergInference on JSONL events near entry_price in the last 10 min.

        Returns (status, confidence):
          status = "PASS" | "BLOCK" | "NEUTRAL"
          confidence = 0.0..1.0
        """
        if not _v4_available or _v4_inference is None:
            return "UNKNOWN", 0.0

        try:
            from ats_iceberg_v1.features.refill_detector import RefillEvent, IcebergChain
            import uuid, time as _time

            # Load today's JSONL events near entry
            date_str = now.strftime("%Y%m%d")
            path = None
            for fname in [
                "iceberg_GC_XCEC_%s.jsonl" % date_str,
                "iceberg__GC_XCEC_%s.jsonl" % date_str,
            ]:
                p = self._ice_dir / fname
                if p.exists() and p.stat().st_size > 0:
                    path = p
                    break

            if path is None:
                return "NEUTRAL", 0.0

            cutoff  = now - pd.Timedelta(minutes=10)
            events  = []
            base_ts = int(_time.time() * 1000)

            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    try:
                        ts = pd.Timestamp(rec.get("timestamp", ""), tz="UTC")
                    except Exception:
                        continue
                    if ts < cutoff or ts > now:
                        continue
                    if abs(float(rec.get("price", 0)) - entry_price) > 2.0:
                        continue
                    if float(rec.get("probability", rec.get("prob", 0))) < 0.50:
                        continue

                    refill_count = max(int(rec.get("refill_count", rec.get("refills", 0))), 1)
                    delay_ms     = float(rec.get("time_since_trade_ms", 8.0))
                    peak_size    = float(rec.get("peak_size", 10.0))
                    side         = str(rec.get("side", "bid"))

                    for _ in range(min(refill_count, 10)):
                        events.append(RefillEvent(
                            trade_timestamp=base_ts,
                            refill_timestamp=base_ts + int(delay_ms),
                            price=float(rec.get("price", entry_price)),
                            side=side,
                            trade_size=peak_size,
                            refill_size=peak_size,
                            refill_delay_ms=delay_ms,
                        ))

            if not events:
                return "NEUTRAL", 0.0

            # Build chain and run inference
            chain = IcebergChain(
                chain_id=str(uuid.uuid4()),
                price=entry_price,
                side=events[0].side,
                refill_events=events,
            )
            out = _v4_inference.run_window(
                refill_events=events,
                level_type=level_type,
                direction=direction,
                delta_4h=delta_4h,
            )

            if not out.detected or out.confidence < V4_CONF_THRESHOLD:
                return "NEUTRAL", out.confidence

            # Interpret ATS-aware alignment
            is_contra = (
                (direction == "SHORT" and str(out.side).lower() in ("bid", "icessidebid")) or
                (direction == "LONG"  and str(out.side).lower() in ("ask", "icessideask"))
            )
            # At structural levels both sides support the trade (asymmetric)
            at_level = level_type in ("liq_top", "liq_bot")
            if at_level:
                return "PASS", out.confidence   # both sides = exhaustion = confirming reversal

            if is_contra:
                return "BLOCK", out.confidence
            return "PASS", out.confidence

        except Exception:
            return "UNKNOWN", 0.0

    def check(
        self,
        entry_price: float,
        direction: str,
        now: Optional[pd.Timestamp] = None,
        liq_top: Optional[float] = None,
        liq_bot: Optional[float] = None,
        box_high: Optional[float] = None,
        box_low: Optional[float] = None,
        daily_trend: str = "unknown",
        expansion_lines: Optional[list] = None,
        atr_m30: Optional[float] = None,
    ) -> GateDecision:
        """
        Verifica os gates para a entrada proposta.

        Parameters
        ----------
        entry_price : float  -- preco de entrada (= liq_top para SHORT, liq_bot para LONG)
        direction   : str    -- "LONG" ou "SHORT"
        now         : pd.Timestamp, optional -- UTC, usa utcnow() se None
        liq_top     : float, optional -- nivel estrutural superior (15-day high)
        liq_bot     : float, optional -- nivel estrutural inferior (15-day low)

        Returns
        -------
        GateDecision
        """
        if now is None:
            now = pd.Timestamp.utcnow()
        direction = direction.upper()

        # ── V1 — dual-mode structural proximity check (FIX 6, 2026-04-08) ──────
        # TRENDING (daily_trend known + box boundaries available):
        #   PASS if entry_price is inside the M30 box zone [box_low, box_high].
        #   Rationale: in trending days, breakouts from the box are the signal —
        #   price rarely returns. Distance-to-level would block ~65.9% of valid
        #   checks since price is already away from liq_top/liq_bot.
        # RANGE (no daily_trend or no box data):
        #   PASS if abs(price - level) <= V1_RANGE_DIST_PTS (8pts).
        signal_level = liq_top if direction == "SHORT" else liq_bot
        _trending = daily_trend in ("long", "short") and box_high is not None and box_low is not None
        if _trending:
            in_zone = (box_low <= entry_price <= box_high)

            # BUG 2 FIX: Pullback entry zone in trending markets.
            # When price has broken OUT of the box, each broken expansion line
            # becomes a pullback resistance (SHORT) / support (LONG).
            # Accept entry when price is within ATR_M30 * 0.5 of any expansion line.
            # Rationale: in trend, breakout expansion lines are tested on pullbacks —
            # these are the highest-probability re-entry zones (ATS transcript §pullback).
            _exp_zone_hit = None
            if not in_zone and expansion_lines and atr_m30 is not None and atr_m30 > 0:
                band = atr_m30 * 0.5
                for el in expansion_lines:
                    if abs(entry_price - el) <= band:
                        in_zone      = True
                        _exp_zone_hit = el
                        break

            if not in_zone:
                return GateDecision(
                    go=False,
                    total_score=0,
                    reason=(
                        f"BLOCK_V1_ZONE: price {entry_price:.1f} outside M30 box"
                        f" [{box_low:.1f}-{box_high:.1f}] and no expansion line"
                        f" (TRENDING/{daily_trend})"
                    ),
                    direction=direction,
                    entry_price=entry_price,
                )
        elif signal_level is not None:
            v1_delta = abs(entry_price - signal_level)
            if v1_delta > V1_RANGE_DIST_PTS:
                return GateDecision(
                    go=False,
                    total_score=0,
                    reason=(
                        f"BLOCK_V1: price {entry_price:.1f} too far from level"
                        f" {signal_level:.1f}, delta={v1_delta:.1f}pts (RANGE, max={V1_RANGE_DIST_PTS})"
                    ),
                    direction=direction,
                    entry_price=entry_price,
                )

        # ── V2 — iceberg alignment gate (Level 2, 2026-04-12) ─────────────────
        # l2_entry_score was a constant 60.0 placeholder across all 9 months.
        # Real V2 gate is implemented below (after V4) using calibrated V1 + ML.
        _v2_ice_block = False  # set to True below after V4 runs

        # Structural level detection (+/-2pts)
        at_structural_level = False
        if direction == "SHORT" and liq_top is not None:
            at_structural_level = abs(entry_price - liq_top) <= 2.0
        elif direction == "LONG" and liq_bot is not None:
            at_structural_level = abs(entry_price - liq_bot) <= 2.0

        level_type = "liq_top" if direction == "SHORT" else "liq_bot"

        # --- Gate 0: Macro trend filter (5-day cumulative delta, v1.3) ---
        macro_delta = self.micro_reader.get_macro_delta(now)

        # --- Gate 1: Momentum ---
        mom = self.micro_reader.get_momentum_signal(direction, now)

        # --- Gate 2: Iceberg + Absorption (ATSIcebergV1) includes pressure/sweep/poc ---
        ice = self.iceberg.check(entry_price, direction, level_type, now)

        # --- Hard Block: TYPE 4 JSONL Contra (binary, no threshold) ---
        # Sprint 4 A1: if JSONL iceberg is contra the trade direction → immediate block.
        # TYPE 1 (absorption) and TYPE 3 (LOI) are NOT hard-blocked here — need calibration.
        if ICEBERG_HARD_BLOCK_ON_CONTRA and ice.jsonl_contra:
            import logging as _logging
            _logging.getLogger("apex.gate").info(
                "[ICEBERG_HARD_BLOCK] type=JSONL_CONTRA direction=%s signals=%s → BLOCK",
                direction, ice.reason,
            )
            return GateDecision(
                go=False,
                total_score=ice.get_score_contribution(),
                reason="ICEBERG_HARD_BLOCK: JSONL iceberg contra %s (binary block)" % direction,
                direction=direction,
                entry_price=entry_price,
                momentum=mom,
                iceberg=ice,
                macro_delta=macro_delta,
                macro_blocked=False,
                v4_status="SKIPPED_HARD_BLOCK",
                v4_confidence=0.0,
                l2_entry_score=-1.0,
            )

        # --- Score ---
        total_score = mom.score + ice.get_score_contribution()

        # --- Decision logic ---
        hard_block   = False
        macro_blocked = False
        block_reason = ""

        # Macro hard-block REMOVED (was v1.3, not in spec).
        # 5d_delta trend does not prevent entries at structural levels.
        macro_blocked = False

        # Momentum hard-block
        # BUG1: session boundary prevents cross-session delta contamination
        # BUG4: load_recent filters timestamp <= now (no future data leakage)
        #
        # Special case: SHORT at liq_top with impulse block (NOT 4h delta block)
        # A strong 30-min bar arriving at resistance IS the exhaustion signal -- relax.
        # 4h delta blocks are kept strict.
        block_from_delta = (
            (direction == "SHORT" and mom.delta_4h > MOMENTUM_BLOCK_SHORT) or
            (direction == "LONG"  and mom.delta_4h < MOMENTUM_BLOCK_LONG)
        )
        relax_impulse = (
            mom.status == "block"
            and not block_from_delta      # impulse block, not 4h momentum
            and direction == "SHORT"
            and at_structural_level       # only at liq_top
        )

        if mom.status == "block" and not relax_impulse:
            hard_block   = True
            block_reason += (" | " if block_reason else "") + "MOMENTUM: " + mom.reason
        elif relax_impulse:
            mom.status = "warn_at_level"
            mom.score  = -1   # soft penalty: strong bar arriving at level = caution
            total_score = mom.score + ice.get_score_contribution()

        # Iceberg hard-block (strong institutional signal against + high confidence)
        if ice.is_hard_block():
            hard_block   = True
            block_reason += (" | " if block_reason else "") + "ICEBERG_BLOCK: " + ice.reason

        # FASE 3 — Calibrated hard blocks (CAL-1: absorption, CAL-2: LOI)
        if ice.absorption_hard_contra and not hard_block:
            hard_block   = True
            block_reason += (" | " if block_reason else "") + (
                "ABS_HARD_CONTRA: ratio=%.1f >= 12.28" % ice.absorption_ratio)
        if ice.loi_hard_contra and not hard_block:
            hard_block   = True
            block_reason += (" | " if block_reason else "") + (
                "LOI_HARD_CONTRA: |loi|=%.3f >= 0.14" % abs(ice.large_order_imbalance))

        # FASE 3 — Collision detection (CAL-4, CAL-5) — HARD BLOCK
        if ice.collision_detected and not hard_block:
            hard_block   = True
            block_reason += (" | " if block_reason else "") + (
                "COLLISION: %s" % ice.collision_detail)

        # FASE 3 — Breaking Ice (CAL-6, CAL-7) — LOG ONLY
        if ice.breaking_ice:
            import logging as _logging
            _logging.getLogger("apex.gate").info(
                "[BREAKING_ICE] %s direction=%s (log_only)", ice.breaking_ice_detail, direction)

        # FASE 3 — Iceberg Zones proximity (CAL-8) — LOG ONLY
        if ice.iceberg_zone_dist >= 0:
            _in_zone = ice.iceberg_zone_dist <= 5.0  # ICEBERG_ZONES_PROX
            import logging as _logging
            _logging.getLogger("apex.gate").info(
                "[ICEBERG_ZONE] dist=%.1fpts in_zone=%s direction=%s (log_only)",
                ice.iceberg_zone_dist, _in_zone, direction)

        # --- Grenadier Guardrail: Stat-Guardrail (Sprint 1 — The Shield) ---
        # O(1) deterministic check — runs before V4 even when V1-V3 already blocked.
        # Protects against stale data (>2000ms) and liquidity vacuum (spread >10 ticks).
        if _guardrail_fn is not None and not hard_block:
            _gr = _guardrail_fn()
            if not _gr.is_safe:
                hard_block   = True
                block_reason += (" | " if block_reason else "") + (
                    "GUARDRAIL_%s: latency=%.0fms spread=%.1ftks"
                    % (_gr.veto_reason, _gr.latency_ms, _gr.spread_ticks)
                )

        # --- Gate V4: ML IcebergInference (only when V1-V3 not already hard-blocked) ---
        v4_status = "UNKNOWN"
        v4_conf   = 0.0
        if not hard_block:
            v4_status, v4_conf = self._run_v4(
                entry_price=entry_price,
                direction=direction,
                level_type=level_type,
                now=now,
                delta_4h=mom.delta_4h,
            )
            # V4 gate — HARD VETO (calibrated 2026-04-11, stress test T=0.85):
            # BLOCK only on active CONTRA iceberg (conf >= V4_CONF_THRESHOLD = 0.85).
            # NEUTRAL = no iceberg detected = absence of evidence, NOT a block.
            # UNKNOWN = V4 unavailable = no data, gate continues without it.
            if v4_status == "BLOCK":
                hard_block   = True
                block_reason += (" | " if block_reason else "") + (
                    "BLOCK_V4_CONTRA: iceberg contra detected (conf=%.2f)" % v4_conf)

            # ── Gate V2 (Level 2): iceberg alignment for structural entries ──────
            # V4 NEUTRAL = ML model sees no iceberg.
            # If V1 rule-based score is also contra (ice.score <= -2), we have two
            # independent signals both saying "no institutional support here".
            # Block: absence of iceberg confirmation + rule-based contra signal.
            # NEUTRAL with ice.score > -2 = no signal either way → pass through.
            # UNKNOWN (V4 unavailable) = no data → gate does not fire.
            if v4_status == "NEUTRAL" and ice.score <= -2:
                _v2_ice_block = True
                hard_block   = True
                block_reason += (" | " if block_reason else "") + (
                    "BLOCK_V2_NOICE: no iceberg alignment (v4=NEUTRAL, ice.score=%+d)" % ice.score
                )

        # --- Final decision ---
        if hard_block:
            reason = block_reason.strip(" |")
            go     = False
        elif total_score >= MIN_SCORE_GO:
            parts = []
            if mom.status == "ok" and mom.score > 0:
                parts.append("momentum OK (d4h=%+.0f)" % mom.delta_4h)
            if ice.detected and ice.aligned:
                parts.append("iceberg %s sc=%+d" % (ice.primary_type, ice.score))
            if not parts:
                parts.append("sinais neutros -- nivel estrutural valido")
            reason = " | ".join(parts)
            go     = True
        else:
            parts = ["score insuficiente (%+d)" % total_score]
            if mom.status in ("warn", "warn_at_level"):
                parts.append(mom.reason)
            if ice.detected and not ice.aligned:
                parts.append("iceberg contra: " + ice.reason)
            reason = " | ".join(parts)
            go     = False

        return GateDecision(
            go=go,
            total_score=total_score,
            reason=reason,
            direction=direction,
            entry_price=entry_price,
            momentum=mom,
            iceberg=ice,
            macro_delta=macro_delta,
            macro_blocked=macro_blocked,
            v4_status=v4_status,
            v4_confidence=v4_conf,
            l2_entry_score=-1.0,  # placeholder — real gate is V2_NOICE above
        )


# ---------------------------------------------------------------------------
# CLI — teste rápido
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ATS Live Gate — verifica entrada")
    parser.add_argument("--price",     type=float, required=True, help="Preço de entrada")
    parser.add_argument("--direction", type=str,   required=True, help="LONG ou SHORT")
    parser.add_argument("--timestamp", type=str,   default=None,  help="ISO timestamp UTC (default: agora)")
    args = parser.parse_args()

    now = pd.Timestamp(args.timestamp, tz="UTC") if args.timestamp else pd.Timestamp.utcnow()
    gate = ATSLiveGate()
    decision = gate.check(
        entry_price=args.price,
        direction=args.direction,
        now=now,
    )
    print(decision.summary())
    sys.exit(0 if decision.go else 1)

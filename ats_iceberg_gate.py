#!/usr/bin/env python3
"""
ats_iceberg_v1.py -- ATS-specific iceberg/absorption signal module.

Purpose-built for ATSLiveGate. Reads microstructure CSV + JSONL iceberg
data to detect institutional activity at ATS structural levels.

INDEPENDENT of ml_iceberg_v2 (GitHub) -- do not import from that module.
Uses only: microstructure_YYYY-MM-DD.csv.gz + iceberg JSONL files on disk.

Signal types (all use level context -- liq_top vs liq_bot):

  TYPE 1 -- Absorption (absorption_detected=True at level +/-2pts)
    AT liq_top (SHORT):
      bid = buyers absorbed by sellers = institutional SUPPLY  = SHORT aligned (+3)
      ask = sellers absorbed by buyers = institutional DEMAND  = SHORT against (-3)
    AT liq_bot (LONG):
      ask = sellers absorbed by buyers = institutional DEMAND  = LONG aligned (+3)
      bid = buyers absorbed by sellers = institutional SUPPLY  = LONG against (-3)
    Score modifier: ratio>=10 x1.5 | ratio>=5 x1.2 | ratio<2 x0.5

  TYPE 2 -- DOM Imbalance (abs(dom_imbalance) >= 0.40 at level)
    AT liq_top (SHORT):
      dom >= +0.40 = heavy bid = buyers pushing against resistance = SHORT aligned (+2)
      dom <= -0.40 = heavy ask = sellers already winning          = SHORT confirmed (+3)
    AT liq_bot (LONG):
      dom <= -0.40 = heavy ask = sellers pushing against support  = LONG aligned (+2)
      dom >= +0.40 = heavy bid = buyers already winning           = LONG confirmed (+3)

  TYPE 3 -- Large Order Imbalance (abs(large_order_imbalance) >= 0.50)
    AT liq_top (SHORT setup) -- asymmetric (v1.2):
      loi > +0.50 = institutional BUY at resistance = being absorbed = SHORT aligned (+1)
      loi < -0.50 = institutional SELL fleeing resistance = SHORT contra (-2)
    AT liq_bot (LONG setup) -- asymmetric (v1.2):
      loi < -0.50 = institutional SELL at support = being absorbed = LONG aligned (+1)
      loi > +0.50 = institutional BUY fleeing support = LONG contra (-2)
    NOT at structural level (fallback):
      loi > +0.50 = LONG aligned (+2), SHORT against (-2)
      loi < -0.50 = SHORT aligned (+2), LONG against (-2)

  TYPE 4 -- JSONL Iceberg (prob >= 0.50 AND refills >= 3 only)
    BID iceberg at liq_top (SHORT): institutional buy at resistance = AGAINST (-4)
    ASK iceberg at liq_top (SHORT): institutional sell at resistance = ALIGNED (+4)
    ASK iceberg at liq_bot (LONG):  institutional sell at support = AGAINST (-4)
    BID iceberg at liq_bot (LONG):  institutional buy at support = ALIGNED (+4)

  TYPE 5 -- Pressure Ratio (v1.3) -- bid/ask pressure balance at level
    SHORT at liq_top:
      pressure_ratio > 2.5 = buyers dominant  = SHORT risky (-2)
      pressure_ratio > 1.5 = buyers active    = SHORT caution (-1)
    LONG at liq_bot:
      pressure_ratio < 0.40 = sellers dominant = LONG risky (-2)
      pressure_ratio < 0.67 = sellers active   = LONG caution (-1)

  TYPE 6 -- Sweep Contra-Filter (v1.3)
    sweep_detected=True at entry bar:
      sweep_direction AGAINST trade = aggressive momentum ongoing = score -2
      sweep_direction WITH trade    = stops cleared = aligned = score +1

  TYPE 7 -- Distance to POC (v1.3) -- reversal strength from Point of Control
    distance_to_poc > 20pts = stretched far from POC = strong reversal setup (+1)
    distance_to_poc < 5pts  = near POC = weak reversal, price gravitates back (-1)

Output: ATSIcebergSignal.is_hard_block() triggers when score <= -4 AND confidence >= 0.60
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Paths (mirrored from ats_live_gate.py)
# ---------------------------------------------------------------------------
MICRO_DIR = Path("C:/data/level2/_gc_xcec")
ICE_DIR   = Path("C:/data/iceberg")

# ---------------------------------------------------------------------------
# Thresholds — static defaults
# ---------------------------------------------------------------------------
LEVEL_BAND_PTS    = 2.0    # +/-2pts from entry_price = "at level"
DOM_MIN_IMBAL     = 0.40   # abs(dom_imbalance) threshold
LOI_MIN           = 0.50   # abs(large_order_imbalance) threshold
ABS_MIN_RATIO     = 2.0    # absorption_ratio below this = weak signal
JSONL_MIN_REFILLS = 3      # minimum refill_count for JSONL events

# Calibrated thresholds (FASE 2, 2026-04-13)
ABS_HARD_BLOCK_RATIO   = 12.28   # CAL-1: absorption contra hard block if ratio >= this
LOI_HARD_BLOCK_MIN     = 0.14    # CAL-2: LOI contra hard block if |LOI| >= this
COLLISION_PRICE_BAND   = 1.60    # CAL-4: BID+ASK iceberg within this = collision
COLLISION_LOOKBACK_MIN = 3       # CAL-5: lookback window for collision (minutes)
BREAKING_ICE_EXCEED    = 2.20    # CAL-6: price exceed beyond iceberg level (pts)
BREAKING_ICE_LOOKBACK  = 4       # CAL-7: lookback window for broken iceberg (minutes)
ICEBERG_ZONES_PROX     = 5.00    # CAL-8: proximity to iceberg zone (pts)

# JSONL_MIN_PROB: loaded from settings.json (iceberg_proxy_threshold).
# Calibrated 2026-04-08 from 9 months GC data → 0.9150.
# Falls back to 0.50 if settings.json is missing/corrupt.
def _load_jsonl_min_prob() -> float:
    _settings = Path("C:/FluxQuantumAI/config/settings.json")
    try:
        with open(_settings, "r", encoding="utf-8") as _f:
            return float(json.load(_f).get("iceberg_proxy_threshold", 0.50))
    except Exception:
        return 0.50

JSONL_MIN_PROB = _load_jsonl_min_prob()

# TYPE 5 -- Pressure Ratio thresholds
PRESSURE_WARN_SHORT  = 1.5    # pressure_ratio > this = buyers still active (SHORT warn)
PRESSURE_BLOCK_SHORT = 2.5    # pressure_ratio > this = buyers dominant (SHORT block)
PRESSURE_WARN_LONG   = 0.67   # pressure_ratio < this = sellers still active (LONG warn)
PRESSURE_BLOCK_LONG  = 0.40   # pressure_ratio < this = sellers dominant (LONG block)

# TYPE 7 -- Distance to POC thresholds
POC_FAR_PTS  = 20.0   # distance_to_poc > this = stretched = reversal strength +1
POC_NEAR_PTS = 5.0    # distance_to_poc < this = near POC = weak reversal -1


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class ATSIcebergSignal:
    detected: bool = False
    aligned: bool = False
    score: int = 0                  # -9 to +12 (negative = against direction)
    confidence: float = 0.0        # 0.0 to 1.0
    primary_type: str = "none"     # 'absorption' / 'dom' / 'large_order' / 'jsonl' / 'pressure' / 'sweep' / 'poc'
    absorption_side: str = "none"  # 'bid' / 'ask' / 'none'
    absorption_ratio: float = 0.0
    dom_imbalance: float = 0.0
    large_order_imbalance: float = 0.0
    reason: str = "no signal"
    # v1.3 new signal values (for debugging / replay)
    pressure_ratio_val: float = 0.0
    sweep_dir: str = "none"
    poc_distance_val: float = 0.0
    jsonl_contra: bool = False         # True when TYPE 4 JSONL score < 0 (binary hard block)
    # FASE 3 calibrated signals (2026-04-13)
    absorption_hard_contra: bool = False  # True when absorption contra + ratio >= ABS_HARD_BLOCK_RATIO
    loi_hard_contra: bool = False         # True when LOI contra + |LOI| >= LOI_HARD_BLOCK_MIN
    collision_detected: bool = False      # True when BID+ASK icebergs within COLLISION_PRICE_BAND
    collision_detail: str = "none"        # "BID@price ASK@price dist=X.Xpts"
    breaking_ice: bool = False            # True when price exceeded iceberg level
    breaking_ice_detail: str = "none"     # "side@price exceeded by X.Xpts"
    iceberg_zone_dist: float = -1.0       # Distance to nearest iceberg zone (-1 = no zone)

    def is_hard_block(self) -> bool:
        """True when institutional signal is strongly against trade + high confidence."""
        return self.score <= -4 and self.confidence >= 0.60

    def get_score_contribution(self) -> int:
        return self.score


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ATSIcebergV1:
    """
    ATS-aware iceberg/absorption detector.

    Level context (liq_top / liq_bot) is mandatory -- it inverts the
    interpretation of every microstructure signal.
    """

    def __init__(
        self,
        micro_dir: Path = MICRO_DIR,
        ice_dir: Path = ICE_DIR,
    ):
        self.micro_dir = micro_dir
        self.ice_dir   = ice_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        entry_price: float,
        direction: str,
        level_type: str,
        now: pd.Timestamp,
        window_minutes: int = 10,
    ) -> ATSIcebergSignal:
        """
        Detect institutional iceberg / absorption at ATS structural level.

        Parameters
        ----------
        entry_price    : float -- proposed entry price (= liq_top or liq_bot)
        direction      : 'LONG' or 'SHORT'
        level_type     : 'liq_top' (SHORT setups) or 'liq_bot' (LONG setups)
        now            : entry timestamp UTC
        window_minutes : minutes back to scan (default 10)
        """
        direction  = direction.upper()
        level_type = level_type.lower()

        df = self._load_microstructure(now, window_minutes)

        abs_sig      = self._check_absorption(df, entry_price, direction, level_type)
        dom_sig      = self._check_dom(df, entry_price, direction, level_type)
        loi_sig      = self._check_large_order(df, direction, level_type)
        json_sig     = self._check_jsonl(entry_price, direction, level_type, now, window_minutes)
        pressure_sig = self._check_pressure_ratio(df, direction, level_type, entry_price)
        sweep_sig    = self._check_sweep(df, direction, level_type)
        poc_sig      = self._check_poc_distance(df, direction)

        out = self._combine_signals(abs_sig, dom_sig, loi_sig, json_sig, pressure_sig, sweep_sig, poc_sig)

        # FASE 3 — Collision, Breaking Ice, Zones (run after combine)
        collision = self._check_collision(entry_price, now)
        out.collision_detected = collision["detected"]
        out.collision_detail   = collision["detail"]

        breaking = self._check_breaking_ice(entry_price, now, df)
        out.breaking_ice        = breaking["detected"]
        out.breaking_ice_detail = breaking["detail"]

        out.iceberg_zone_dist = self._check_zones_proximity(entry_price, now)

        return out

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_microstructure(
        self, now: pd.Timestamp, window_minutes: int
    ) -> Optional[pd.DataFrame]:
        """Load microstructure rows in (now - window, now]. No future data."""
        cutoff   = now - pd.Timedelta(minutes=window_minutes)
        date_str = now.strftime("%Y-%m-%d")
        dfs      = []

        dates_to_try = [date_str]
        # Also load previous day if window crosses midnight
        if now.hour == 0 and now.minute < window_minutes:
            prev = (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            dates_to_try.insert(0, prev)

        for d in dates_to_try:
            for suffix in [f"microstructure_{d}.fixed.csv.gz", f"microstructure_{d}.csv.gz"]:
                path = self.micro_dir / suffix
                if path.exists():
                    try:
                        tmp = pd.read_csv(path, parse_dates=["timestamp"])
                        tmp["timestamp"] = pd.to_datetime(tmp["timestamp"], utc=True)
                        dfs.append(tmp)
                    except Exception:
                        pass
                    break

        if not dfs:
            return None

        df = pd.concat(dfs, ignore_index=True)
        df = df[(df["timestamp"] >= cutoff) & (df["timestamp"] <= now)]
        return df.sort_values("timestamp").reset_index(drop=True) if len(df) > 0 else None

    # ------------------------------------------------------------------
    # Type 1 -- Absorption
    # ------------------------------------------------------------------

    def _check_absorption(
        self,
        df: Optional[pd.DataFrame],
        entry_price: float,
        direction: str,
        level_type: str,
    ) -> dict:
        """
        Absorption at level with ATS asymmetric interpretation.

        AT liq_top (SHORT):
          bid absorption = buyers absorbed = institutional SUPPLY = ALIGNED (+3)
          ask absorption = sellers absorbed = institutional DEMAND = AGAINST (-3)

        AT liq_bot (LONG):
          ask absorption = sellers absorbed = institutional DEMAND = ALIGNED (+3)
          bid absorption = buyers absorbed  = institutional SUPPLY = AGAINST (-3)

        Score modified by absorption_ratio strength.
        """
        result = {"score": 0, "confidence": 0.0, "side": "none", "ratio": 0.0, "found": False}
        if df is None or len(df) == 0:
            return result

        required = {"absorption_detected", "absorption_ratio", "absorption_side", "mid_price"}
        if not required.issubset(df.columns):
            return result

        at_level = df[
            (df["absorption_detected"] == True) &
            (df["mid_price"].notna()) &
            (df["mid_price"].sub(entry_price).abs() <= LEVEL_BAND_PTS)
        ].copy()

        if len(at_level) == 0:
            return result

        at_level = at_level[at_level["absorption_side"].isin(["bid", "ask"])]
        if len(at_level) == 0:
            return result

        best  = at_level.loc[at_level["absorption_ratio"].idxmax()]
        side  = str(best["absorption_side"]).lower()
        ratio = float(best["absorption_ratio"])

        # Base score from level context
        if level_type == "liq_top":
            base = +3 if side == "bid" else -3   # bid = supply absorbing = SHORT ok
        else:
            base = +3 if side == "ask" else -3   # ask = demand absorbing = LONG ok

        # Ratio modifier
        if ratio >= 10:
            modifier, conf = 1.5, 0.90
        elif ratio >= 5:
            modifier, conf = 1.2, 0.75
        elif ratio >= ABS_MIN_RATIO:
            modifier, conf = 1.0, 0.55
        else:
            modifier, conf = 0.5, 0.30

        score = int(round(base * modifier))
        score = max(-5, min(+5, score))

        result.update({"score": score, "confidence": conf, "side": side, "ratio": ratio, "found": True})
        return result

    # ------------------------------------------------------------------
    # Type 2 -- DOM Imbalance
    # ------------------------------------------------------------------

    def _check_dom(
        self,
        df: Optional[pd.DataFrame],
        entry_price: float,
        direction: str,
        level_type: str,
    ) -> dict:
        """
        DOM imbalance at level.

        AT liq_top (SHORT):
          dom >= +0.40 = heavy bid = buyers pushing against resistance = +2
          dom <= -0.40 = heavy ask = sellers already winning = +3

        AT liq_bot (LONG):
          dom <= -0.40 = heavy ask = sellers pushing against support = +2
          dom >= +0.40 = heavy bid = buyers already winning = +3
        """
        result = {"score": 0, "confidence": 0.0, "dom": 0.0, "found": False}
        if df is None or len(df) == 0:
            return result

        if "dom_imbalance" not in df.columns or "mid_price" not in df.columns:
            return result

        at_level = df[
            (df["mid_price"].notna()) &
            (df["mid_price"].sub(entry_price).abs() <= LEVEL_BAND_PTS) &
            (df["dom_imbalance"].abs() >= DOM_MIN_IMBAL)
        ]

        if len(at_level) == 0:
            return result

        dom_mean = float(at_level["dom_imbalance"].mean())

        if level_type == "liq_top":
            if dom_mean >= DOM_MIN_IMBAL:
                score, conf = +2, 0.60   # buyers pushing = SHORT aligned
            elif dom_mean <= -DOM_MIN_IMBAL:
                score, conf = +3, 0.70   # sellers winning = SHORT confirmed
            else:
                return result
        else:  # liq_bot
            if dom_mean <= -DOM_MIN_IMBAL:
                score, conf = +2, 0.60   # sellers pushing = LONG aligned
            elif dom_mean >= DOM_MIN_IMBAL:
                score, conf = +3, 0.70   # buyers winning = LONG confirmed
            else:
                return result

        result.update({"score": score, "confidence": conf, "dom": dom_mean, "found": True})
        return result

    # ------------------------------------------------------------------
    # Type 3 -- Large Order Imbalance
    # ------------------------------------------------------------------

    def _check_large_order(
        self,
        df: Optional[pd.DataFrame],
        direction: str,
        level_type: str = "",
    ) -> dict:
        """
        Institutional large order imbalance — asymmetric at structural levels (v1.2).

        AT liq_top (SHORT): large buyers at resistance = being absorbed by sellers
          loi > +0.50 → SHORT aligned (+1)   [buyers will be exhausted]
          loi < -0.50 → SHORT contra  (-2)   [sellers already fleeing]

        AT liq_bot (LONG): large sellers at support = being absorbed by buyers
          loi < -0.50 → LONG aligned (+1)    [sellers will be exhausted]
          loi > +0.50 → LONG contra   (-2)   [buyers already fleeing]

        Otherwise (not at structural level): original symmetric logic
          loi > +0.50 → LONG +2 / SHORT -2
          loi < -0.50 → SHORT +2 / LONG -2
        """
        result = {"score": 0, "confidence": 0.0, "loi": 0.0, "found": False}
        if df is None or len(df) == 0:
            return result

        if "large_order_imbalance" not in df.columns:
            return result

        loi_rows = df[df["large_order_imbalance"].abs() >= LOI_MIN]
        if len(loi_rows) == 0:
            return result

        loi_mean = float(loi_rows["large_order_imbalance"].mean())

        if level_type == "liq_top" and direction == "SHORT":
            # Asymmetric at liq_top:
            #   LOI > +0.50: institutional buyers fighting resistance → absorbed by sellers → SHORT aligned (+1)
            #   LOI < -0.50: institutional sellers confirming resistance → SHORT confirmed (+2) [v1.1 was correct]
            if loi_mean > LOI_MIN:
                score = +1   # buyers at top being absorbed
            elif loi_mean < -LOI_MIN:
                score = +2   # sellers confirming the level
            else:
                return result
        elif level_type == "liq_bot" and direction == "LONG":
            # Asymmetric at liq_bot:
            #   LOI < -0.50: institutional sellers fighting support → absorbed by buyers → LONG aligned (+1)
            #   LOI > +0.50: institutional buyers confirming support → LONG confirmed (+2) [v1.1 was correct]
            if loi_mean < -LOI_MIN:
                score = +1   # sellers at bottom being absorbed
            elif loi_mean > LOI_MIN:
                score = +2   # buyers confirming the level
            else:
                return result
        else:
            # Symmetric fallback (no structural context)
            if loi_mean > LOI_MIN:
                score = +2 if direction == "LONG" else -2
            elif loi_mean < -LOI_MIN:
                score = +2 if direction == "SHORT" else -2
            else:
                return result

        conf = min(0.80, 0.50 + abs(loi_mean) * 0.30)
        result.update({"score": score, "confidence": conf, "loi": loi_mean, "found": True})
        return result

    # ------------------------------------------------------------------
    # Type 5 -- Pressure Ratio (v1.3)
    # ------------------------------------------------------------------

    def _check_pressure_ratio(
        self,
        df: Optional[pd.DataFrame],
        direction: str,
        level_type: str,
        entry_price: float,
    ) -> dict:
        """
        Bid/ask pressure balance at the structural level.

        SHORT at liq_top:
          pressure_ratio > 2.5 = buyers dominant   = score -2
          pressure_ratio > 1.5 = buyers still active = score -1

        LONG at liq_bot:
          pressure_ratio < 0.40 = sellers dominant  = score -2
          pressure_ratio < 0.67 = sellers active    = score -1
        """
        result = {"score": 0, "confidence": 0.0, "ratio": 0.0, "found": False}
        if df is None or len(df) == 0:
            return result
        if "pressure_ratio" not in df.columns:
            return result

        # Prefer rows at the level; fall back to full window
        if "mid_price" in df.columns:
            at_level = df[
                df["mid_price"].notna() &
                (df["mid_price"].sub(entry_price).abs() <= LEVEL_BAND_PTS) &
                df["pressure_ratio"].notna()
            ]
            rows = at_level if len(at_level) > 0 else df[df["pressure_ratio"].notna()]
        else:
            rows = df[df["pressure_ratio"].notna()]

        if len(rows) == 0:
            return result

        ratio_mean = float(rows["pressure_ratio"].mean())

        score = 0
        if level_type == "liq_top" and direction == "SHORT":
            if ratio_mean > PRESSURE_BLOCK_SHORT:
                score = -2
            elif ratio_mean > PRESSURE_WARN_SHORT:
                score = -1
        elif level_type == "liq_bot" and direction == "LONG":
            if ratio_mean < PRESSURE_BLOCK_LONG:
                score = -2
            elif ratio_mean < PRESSURE_WARN_LONG:
                score = -1

        if score == 0:
            return result

        conf = 0.55 if score == -1 else 0.65
        result.update({"score": score, "confidence": conf, "ratio": ratio_mean, "found": True})
        return result

    # ------------------------------------------------------------------
    # Type 6 -- Sweep Contra-Filter (v1.3)
    # ------------------------------------------------------------------

    def _check_sweep(
        self,
        df: Optional[pd.DataFrame],
        direction: str,
        level_type: str = "",
    ) -> dict:
        """
        Detect aggressive sweep at entry bar.

        AT structural levels (level_type set) -- asymmetric interpretation:
          Sweep of ANY direction = buyers/sellers exhausted at the wall = ALIGNED (+1)
          Rationale: the same as BUG2/LOI asymmetry. An upward sweep at liq_top means
          buyers ran into the resistance wall and are being absorbed. It IS the SHORT setup.
          A downward sweep at liq_bot means sellers ran into support and are absorbed. It IS
          the LONG setup. Both directions at a structural level confirm the reversal.

        OFF structural level (level_type empty) -- directional signal:
          sweep AGAINST trade = momentum still running = score -2
          sweep WITH trade    = stops cleared, path open = score +1
        """
        result = {"score": 0, "confidence": 0.0, "sweep_dir": "none", "found": False}
        if df is None or len(df) == 0:
            return result
        if "sweep_detected" not in df.columns or "sweep_direction" not in df.columns:
            return result

        sweeps = df[df["sweep_detected"] == True]
        if len(sweeps) == 0:
            return result

        # Use most recent sweep in window
        last = sweeps.iloc[-1]
        sweep_dir = str(last.get("sweep_direction", "")).lower().strip()
        if not sweep_dir or sweep_dir in ("nan", "none", ""):
            return result

        score = 0
        if level_type in ("liq_top", "liq_bot"):
            # At structural level: any sweep = exhaustion = ALIGNED (+1)
            # Buyers swept up to liq_top and hit the wall = SHORT setup validated
            # Sellers swept down to liq_bot and hit the floor = LONG setup validated
            score = +1
        elif direction == "SHORT":
            if sweep_dir == "up":
                score = -2   # upward sweep off-level = buyers still running = danger
            elif sweep_dir == "down":
                score = +1   # downward sweep = sellers clearing stops = SHORT aligned
        elif direction == "LONG":
            if sweep_dir == "down":
                score = -2   # downward sweep off-level = sellers still running = danger
            elif sweep_dir == "up":
                score = +1   # upward sweep = buyers clearing stops = LONG aligned

        if score == 0:
            return result

        conf = 0.65 if score < 0 else 0.50
        result.update({"score": score, "confidence": conf, "sweep_dir": sweep_dir, "found": True})
        return result

    # ------------------------------------------------------------------
    # Type 7 -- Distance to POC (v1.3)
    # ------------------------------------------------------------------

    def _check_poc_distance(
        self,
        df: Optional[pd.DataFrame],
        direction: str,
    ) -> dict:
        """
        Point of Control distance.

        distance_to_poc > 20pts = stretched far = strong reversal setup (+1)
        distance_to_poc < 5pts  = price near POC = weak reversal, gravity risk (-1)
        """
        result = {"score": 0, "confidence": 0.0, "dist": 0.0, "found": False}
        if df is None or len(df) == 0:
            return result
        if "distance_to_poc" not in df.columns:
            return result

        valid = df[df["distance_to_poc"].notna()]
        if len(valid) == 0:
            return result

        dist_mean = float(valid["distance_to_poc"].abs().mean())

        score = 0
        if dist_mean > POC_FAR_PTS:
            score = +1   # stretched = strong reversal candidate
        elif dist_mean < POC_NEAR_PTS:
            score = -1   # near POC = price likely to stall / reverse back to POC

        if score == 0:
            return result

        conf = 0.50
        result.update({"score": score, "confidence": conf, "dist": dist_mean, "found": True})
        return result

    # ------------------------------------------------------------------
    # Type 4 -- JSONL Iceberg (high-confidence only)
    # ------------------------------------------------------------------

    def _check_jsonl(
        self,
        entry_price: float,
        direction: str,
        level_type: str,
        now: pd.Timestamp,
        window_minutes: int,
    ) -> dict:
        """
        JSONL iceberg events. Only high-quality signals (prob>=0.50, refills>=3).
        Strict threshold filters out low-quality reconstructed offline data.

        BID iceberg at liq_top (SHORT): buyers at resistance = AGAINST (-4)
        ASK iceberg at liq_top (SHORT): sellers at resistance = ALIGNED (+4)
        BID iceberg at liq_bot (LONG):  buyers at support = ALIGNED (+4)
        ASK iceberg at liq_bot (LONG):  sellers at support = AGAINST (-4)
        """
        result = {"score": 0, "confidence": 0.0, "side": "none", "found": False}
        cutoff   = now - pd.Timedelta(minutes=window_minutes)
        date_str = now.strftime("%Y%m%d")

        path = None
        for fname in [
            f"iceberg_GC_XCEC_{date_str}.jsonl",
            f"iceberg__GC_XCEC_{date_str}.jsonl",
        ]:
            p = self.ice_dir / fname
            if p.exists() and p.stat().st_size > 0:
                path = p
                break

        if path is None:
            return result

        events = []
        try:
            with open(path) as f:
                for line in f:
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

                    price   = float(rec.get("price", 0))
                    refills = int(rec.get("refill_count", 0))
                    prob    = float(rec.get("probability", 0))

                    if abs(price - entry_price) > LEVEL_BAND_PTS:
                        continue
                    if refills < JSONL_MIN_REFILLS or prob < JSONL_MIN_PROB:
                        continue

                    events.append(rec)
        except Exception:
            return result

        if not events:
            return result

        bid_exec = sum(float(r.get("executed_size", 0)) for r in events if r.get("side") == "bid")
        ask_exec = sum(float(r.get("executed_size", 0)) for r in events if r.get("side") == "ask")

        if bid_exec > ask_exec * 1.5:
            dom_side = "bid"
        elif ask_exec > bid_exec * 1.5:
            dom_side = "ask"
        else:
            return result  # neutral -- no dominant iceberg

        if level_type == "liq_top":
            score = +4 if dom_side == "ask" else -4
        else:
            score = +4 if dom_side == "bid" else -4

        max_prob = max(float(r.get("probability", 0)) for r in events)
        conf     = min(0.95, max_prob * 1.5)

        result.update({"score": score, "confidence": conf, "side": dom_side, "found": True})
        return result

    # ------------------------------------------------------------------
    # FASE 3 — Collision Detection (CAL-4, CAL-5)
    # ------------------------------------------------------------------

    def _check_collision(
        self,
        entry_price: float,
        now: pd.Timestamp,
    ) -> dict:
        """
        Detect BID+ASK icebergs within COLLISION_PRICE_BAND and COLLISION_LOOKBACK_MIN.
        A collision means opposing institutional forces = uncertainty = caution.
        """
        result = {"detected": False, "detail": "none"}
        cutoff = now - pd.Timedelta(minutes=COLLISION_LOOKBACK_MIN)
        date_str = now.strftime("%Y%m%d")

        path = None
        for fname in [
            f"iceberg_GC_XCEC_{date_str}.jsonl",
            f"iceberg__GC_XCEC_{date_str}.jsonl",
        ]:
            p = self.ice_dir / fname
            if p.exists() and p.stat().st_size > 0:
                path = p
                break

        if path is None:
            return result

        bid_events, ask_events = [], []
        try:
            with open(path) as f:
                for line in f:
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
                    price = float(rec.get("price", 0))
                    if abs(price - entry_price) > LEVEL_BAND_PTS * 2:
                        continue
                    prob = float(rec.get("probability", 0))
                    refills = int(rec.get("refill_count", 0))
                    if prob < JSONL_MIN_PROB or refills < JSONL_MIN_REFILLS:
                        continue
                    side = rec.get("side", "")
                    if side == "bid":
                        bid_events.append(price)
                    elif side == "ask":
                        ask_events.append(price)
        except Exception:
            return result

        if not bid_events or not ask_events:
            return result

        # Check if any BID and ASK are within collision band
        for bp in bid_events:
            for ap in ask_events:
                if abs(bp - ap) <= COLLISION_PRICE_BAND:
                    result["detected"] = True
                    result["detail"] = "BID@%.1f ASK@%.1f dist=%.1fpts" % (bp, ap, abs(bp - ap))
                    return result

        return result

    # ------------------------------------------------------------------
    # FASE 3 — Breaking Ice (CAL-6, CAL-7)
    # ------------------------------------------------------------------

    def _check_breaking_ice(
        self,
        entry_price: float,
        now: pd.Timestamp,
        df: Optional[pd.DataFrame],
    ) -> dict:
        """
        Detect if price has recently exceeded (broken through) an iceberg level.
        Uses sweep_detected from microstructure + JSONL iceberg levels.
        """
        result = {"detected": False, "detail": "none"}
        if df is None or len(df) == 0:
            return result

        cutoff = now - pd.Timedelta(minutes=BREAKING_ICE_LOOKBACK)
        date_str = now.strftime("%Y%m%d")

        # Get recent JSONL iceberg levels
        path = None
        for fname in [
            f"iceberg_GC_XCEC_{date_str}.jsonl",
            f"iceberg__GC_XCEC_{date_str}.jsonl",
        ]:
            p = self.ice_dir / fname
            if p.exists() and p.stat().st_size > 0:
                path = p
                break

        if path is None:
            return result

        ice_levels = []
        try:
            with open(path) as f:
                for line in f:
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
                    prob = float(rec.get("probability", 0))
                    refills = int(rec.get("refill_count", 0))
                    if prob < JSONL_MIN_PROB or refills < JSONL_MIN_REFILLS:
                        continue
                    price = float(rec.get("price", 0))
                    side = rec.get("side", "")
                    if abs(price - entry_price) <= LEVEL_BAND_PTS * 2:
                        ice_levels.append({"price": price, "side": side})
        except Exception:
            return result

        if not ice_levels:
            return result

        # Check if any microstructure bar exceeded an iceberg level
        recent_bars = df[df.index >= cutoff]
        if recent_bars.empty:
            return result

        for ice in ice_levels:
            ip = ice["price"]
            if ice["side"] == "bid":
                # BID iceberg = support. Breaking = low went below by > BREAKING_ICE_EXCEED
                min_low = recent_bars["low"].min() if "low" in recent_bars.columns else entry_price
                exceed = ip - min_low
                if exceed >= BREAKING_ICE_EXCEED:
                    result["detected"] = True
                    result["detail"] = "BID@%.1f broken by %.1fpts (low=%.1f)" % (ip, exceed, min_low)
                    return result
            elif ice["side"] == "ask":
                # ASK iceberg = resistance. Breaking = high went above by > BREAKING_ICE_EXCEED
                max_high = recent_bars["high"].max() if "high" in recent_bars.columns else entry_price
                exceed = max_high - ip
                if exceed >= BREAKING_ICE_EXCEED:
                    result["detected"] = True
                    result["detail"] = "ASK@%.1f broken by %.1fpts (high=%.1f)" % (ip, exceed, max_high)
                    return result

        return result

    # ------------------------------------------------------------------
    # FASE 3 — Iceberg Zones Proximity (CAL-8)
    # ------------------------------------------------------------------

    def _check_zones_proximity(
        self,
        entry_price: float,
        now: pd.Timestamp,
    ) -> float:
        """
        Return distance (pts) to nearest JSONL iceberg within last 30 min.
        Returns -1 if no iceberg found.
        """
        cutoff = now - pd.Timedelta(minutes=30)
        date_str = now.strftime("%Y%m%d")

        path = None
        for fname in [
            f"iceberg_GC_XCEC_{date_str}.jsonl",
            f"iceberg__GC_XCEC_{date_str}.jsonl",
        ]:
            p = self.ice_dir / fname
            if p.exists() and p.stat().st_size > 0:
                path = p
                break

        if path is None:
            return -1.0

        min_dist = float("inf")
        try:
            with open(path) as f:
                for line in f:
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
                    prob = float(rec.get("probability", 0))
                    refills = int(rec.get("refill_count", 0))
                    if prob < JSONL_MIN_PROB or refills < JSONL_MIN_REFILLS:
                        continue
                    price = float(rec.get("price", 0))
                    dist = abs(price - entry_price)
                    if dist < min_dist:
                        min_dist = dist
        except Exception:
            return -1.0

        return min_dist if min_dist < float("inf") else -1.0

    # ------------------------------------------------------------------
    # Combination
    # ------------------------------------------------------------------

    def _combine_signals(
        self,
        abs_sig: dict,
        dom_sig: dict,
        loi_sig: dict,
        json_sig: dict,
        pressure_sig: dict,
        sweep_sig: dict,
        poc_sig: dict,
    ) -> ATSIcebergSignal:
        """
        Combine all signal types. Sum scores, cap at [-9, +12].
        Primary type = highest-confidence signal found.
        """
        out         = ATSIcebergSignal()
        total_score = 0
        max_conf    = 0.0
        primary     = "none"
        parts       = []

        for name, sig in [
            ("absorption", abs_sig),
            ("dom", dom_sig),
            ("large_order", loi_sig),
            ("jsonl", json_sig),
            ("pressure", pressure_sig),
            ("sweep", sweep_sig),
            ("poc", poc_sig),
        ]:
            if not sig.get("found"):
                continue

            total_score += sig["score"]

            if sig["confidence"] > max_conf:
                max_conf = sig["confidence"]
                primary  = name

            if name == "absorption":
                out.absorption_side  = sig["side"]
                out.absorption_ratio = sig["ratio"]
                # CAL-1: hard block if contra AND ratio >= threshold
                if sig["score"] < 0 and sig["ratio"] >= ABS_HARD_BLOCK_RATIO:
                    out.absorption_hard_contra = True
                parts.append("abs(%s,r=%.1f:%+d)" % (sig["side"], sig["ratio"], sig["score"]))
            elif name == "dom":
                out.dom_imbalance = sig["dom"]
                parts.append("dom(%+.2f:%+d)" % (sig["dom"], sig["score"]))
            elif name == "large_order":
                out.large_order_imbalance = sig["loi"]
                # CAL-2: hard block if contra AND |LOI| >= threshold
                if sig["score"] < 0 and abs(sig["loi"]) >= LOI_HARD_BLOCK_MIN:
                    out.loi_hard_contra = True
                parts.append("loi(%+.2f:%+d)" % (sig["loi"], sig["score"]))
            elif name == "jsonl":
                if sig["score"] < 0:
                    out.jsonl_contra = True
                parts.append("jsonl(%s:%+d)" % (sig.get("side", "?"), sig["score"]))
            elif name == "pressure":
                out.pressure_ratio_val = sig["ratio"]
                parts.append("pres(%.2f:%+d)" % (sig["ratio"], sig["score"]))
            elif name == "sweep":
                out.sweep_dir = sig["sweep_dir"]
                parts.append("sweep(%s:%+d)" % (sig["sweep_dir"], sig["score"]))
            elif name == "poc":
                out.poc_distance_val = sig["dist"]
                parts.append("poc(%.1f:%+d)" % (sig["dist"], sig["score"]))

        total_score = max(-9, min(+12, total_score))

        if primary == "none":
            out.reason = "no iceberg/absorption signal at level"
            return out

        out.detected   = True
        out.aligned    = total_score > 0
        out.score      = total_score
        out.confidence = max_conf
        out.primary_type = primary
        out.reason     = " | ".join(parts)
        return out

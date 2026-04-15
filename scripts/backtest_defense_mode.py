#!/usr/bin/env python3
"""
backtest_defense_mode.py — Grenadier Sprint 3 Validation
=========================================================
Plano de Testes Nível 1 (Unitário / Estatístico):
  - False Positive Rate  : % tempo em Defense Mode em horas normais (alvo < 2%)
  - True Positive Rate   : % rows em janela de evento com Defense activo
  - Event Capture Rate   : % eventos macro com ≥1 trigger na janela ±window_min
  - Trigger breakdown    : qual o trigger mais frequente
  - Z-score percentiles  : distribuição fora de eventos (sanidade dos thresholds)

Implementação vectorizada — sem loops Python por row.

Usage:
  python scripts/backtest_defense_mode.py
  python scripts/backtest_defense_mode.py --window 5
  python scripts/backtest_defense_mode.py --window 2 --out results/defense_backtest.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

APEX_ANOMALY = Path(r"C:\FluxQuantumAPEX\APEX GOLD\APEX_GC_Anomaly")
sys.path.insert(0, str(APEX_ANOMALY))
from inference.anomaly_scorer import GrenadierDefenseMode

MICRO_DIR = Path(r"C:\data\level2\_gc_xcec")
OUT_DIR   = Path(r"C:\FluxQuantumAI\results")
OUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("defense.backtest")

# ---------------------------------------------------------------------------
# Macro events (UTC) — NFP, CPI, FOMC, PPI, PCE, GDP, ISM, JOLTS, Retail, ADP
# ---------------------------------------------------------------------------
MACRO_EVENTS_UTC = [
    # NFP
    "2026-01-10 13:30", "2026-02-07 13:30", "2026-03-07 13:30", "2026-04-04 12:30",
    # CPI
    "2026-01-15 13:30", "2026-02-12 13:30", "2026-03-12 13:30",
    # FOMC
    "2026-01-29 19:00", "2026-03-19 18:00",
    # PPI
    "2026-01-14 13:30", "2026-02-13 13:30", "2026-03-13 12:30",
    # PCE
    "2026-01-31 13:30", "2026-02-28 13:30", "2026-03-28 12:30",
    # GDP
    "2026-01-30 13:30", "2026-02-26 13:30", "2026-03-26 12:30",
    # ISM
    "2026-01-06 15:00", "2026-02-03 15:00", "2026-03-03 15:00",
    # JOLTS
    "2026-01-07 15:00", "2026-02-04 15:00", "2026-03-11 14:00",
    # Retail Sales
    "2026-01-16 13:30", "2026-02-17 13:30", "2026-03-17 12:30",
    # ADP
    "2026-01-07 13:15", "2026-02-04 13:15", "2026-03-04 13:15",
]
MACRO_TS = pd.to_datetime(MACRO_EVENTS_UTC, utc=True)


# ---------------------------------------------------------------------------
# Load all microstructure files — vectorized
# ---------------------------------------------------------------------------

def load_all_microstructure(micro_dir: Path) -> pd.DataFrame:
    files = sorted(micro_dir.glob("microstructure_2026-*.csv.gz"))
    # Skip .fixed duplicates if the original exists
    seen_dates: set = set()
    filtered = []
    for f in reversed(files):          # prefer .fixed (newer)
        date_key = f.name.replace(".fixed", "").replace(".csv.gz", "")
        if date_key not in seen_dates:
            seen_dates.add(date_key)
            filtered.append(f)
    filtered = sorted(filtered)

    log.info("Loading %d microstructure files (deduped)...", len(filtered))

    cols = ["timestamp", "spread", "total_bid_size", "total_ask_size"]
    dfs = []
    for f in filtered:
        try:
            df = pd.read_csv(f, usecols=cols)
            dfs.append(df)
        except Exception as e:
            log.warning("  Skip %s — %s", f.name, e)

    if not dfs:
        raise RuntimeError("No microstructure files loaded")

    all_df = pd.concat(dfs, ignore_index=True)
    all_df["timestamp"] = pd.to_datetime(all_df["timestamp"], utc=True, errors="coerce")
    all_df = all_df.dropna(subset=["timestamp"])

    # Dead-data filter
    mask = (all_df["spread"] > 0) & (all_df["total_bid_size"] > 0) & (all_df["total_ask_size"] > 0)
    all_df = all_df[mask].reset_index(drop=True)

    # book_imbalance
    bid = all_df["total_bid_size"]
    ask = all_df["total_ask_size"]
    all_df["book_imbalance"] = (bid - ask) / (bid + ask)

    log.info("Total rows after filter: %d", len(all_df))
    return all_df


# ---------------------------------------------------------------------------
# Vectorized z-score computation
# ---------------------------------------------------------------------------

def compute_defense_vectorized(df: pd.DataFrame, defense: GrenadierDefenseMode) -> pd.DataFrame:
    """Apply z-score thresholds to entire DataFrame at once (no Python loops)."""
    z_spread = (df["spread"]           - defense._mean["spread"])           / (defense._std["spread"]           + 1e-10)
    z_bid    = (df["total_bid_size"]   - defense._mean["total_bid_depth"])  / (defense._std["total_bid_depth"]  + 1e-10)
    z_ask    = (df["total_ask_size"]   - defense._mean["total_ask_depth"])  / (defense._std["total_ask_depth"]  + 1e-10)
    z_imb    = (df["book_imbalance"]   - defense._mean["book_imbalance"])   / (defense._std["book_imbalance"]   + 1e-10)

    t_spread = z_spread > defense.THRESH_SPREAD_HI
    t_bid    = z_bid    < defense.THRESH_DEPTH_LO
    t_ask    = z_ask    < defense.THRESH_DEPTH_LO
    t_imb    = z_imb.abs() > defense.THRESH_IMBALANCE_ABS

    df = df.copy()
    df["z_spread"]     = z_spread.round(3)
    df["z_bid"]        = z_bid.round(3)
    df["z_ask"]        = z_ask.round(3)
    df["z_imb"]        = z_imb.round(3)
    df["t_spread"]     = t_spread
    df["t_bid"]        = t_bid
    df["t_ask"]        = t_ask
    df["t_imb"]        = t_imb
    df["defense_mode"] = t_spread | t_bid | t_ask | t_imb
    return df


# ---------------------------------------------------------------------------
# Event window labelling — vectorized
# ---------------------------------------------------------------------------

def label_event_windows(df: pd.DataFrame, macro_ts: pd.DatetimeIndex, window_min: int) -> pd.Series:
    """Return boolean Series: True where row is within ±window_min of any macro event."""
    ts_ns      = df["timestamp"].values.astype("int64")
    window_ns  = window_min * 60 * 1_000_000_000
    events_ns  = macro_ts.values.astype("int64")

    in_event = np.zeros(len(ts_ns), dtype=bool)
    for ev_ns in events_ns:
        in_event |= np.abs(ts_ns - ev_ns) <= window_ns

    return pd.Series(in_event, index=df.index)


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def run_backtest(window_min: int = 2, out_csv: Path | None = None) -> dict:
    defense = GrenadierDefenseMode()
    log.info(
        "Scaler — spread(μ=%.3f σ=%.3f)  bid(μ=%.1f σ=%.1f)  ask(μ=%.1f σ=%.1f)  imb(μ=%.4f σ=%.4f)",
        defense._mean["spread"],          defense._std["spread"],
        defense._mean["total_bid_depth"], defense._std["total_bid_depth"],
        defense._mean["total_ask_depth"], defense._std["total_ask_depth"],
        defense._mean["book_imbalance"],  defense._std["book_imbalance"],
    )

    # Load + compute
    df = load_all_microstructure(MICRO_DIR)
    df = compute_defense_vectorized(df, defense)
    df["in_event"] = label_event_windows(df, MACRO_TS, window_min)

    # Market hours filter (Mon–Fri only for FP calculation)
    df["weekday"] = df["timestamp"].dt.weekday
    market_hours  = df["weekday"] < 5

    total_rows = len(df)
    in_event   = df["in_event"]
    out_event  = ~in_event

    # ── Rates ────────────────────────────────────────────────────────────────
    tp_rows = df[in_event]
    fp_rows = df[out_event & market_hours]

    tp_rate = float(tp_rows["defense_mode"].mean()) if len(tp_rows) > 0 else 0.0
    fp_rate = float(fp_rows["defense_mode"].mean()) if len(fp_rows) > 0 else 0.0

    # Event capture: per-event, ≥1 trigger within window?
    ts_ns     = df["timestamp"].values.astype("int64")
    dm_arr    = df["defense_mode"].values
    window_ns = window_min * 60 * 1_000_000_000
    events_ns = MACRO_TS.values.astype("int64")

    events_captured = 0
    for ev_ns in events_ns:
        mask = np.abs(ts_ns - ev_ns) <= window_ns
        if dm_arr[mask].any():
            events_captured += 1
    event_capture_rate = events_captured / len(MACRO_TS)

    # ── Trigger breakdown ─────────────────────────────────────────────────────
    triggered = df[df["defense_mode"]]
    trigger_counts = {
        "spread_widening"   : int(triggered["t_spread"].sum()),
        "bid_collapse"      : int(triggered["t_bid"].sum()),
        "ask_collapse"      : int(triggered["t_ask"].sum()),
        "extreme_imbalance" : int(triggered["t_imb"].sum()),
    }
    n_triggered = len(triggered)

    # ── Z-score percentiles (outside events, market hours) ───────────────────
    normal_rows = df[out_event & market_hours]

    # ── Report ───────────────────────────────────────────────────────────────
    log.info("=" * 65)
    log.info("GRENADIER SPRINT 3 — DEFENSE MODE BACKTEST  (window=±%d min)", window_min)
    log.info("  Total rows          : %d", total_rows)
    log.info("  Event rows (±%dmin) : %d  (%.2f%%)",
             window_min, len(tp_rows), 100 * len(tp_rows) / total_rows)
    log.info("  Normal rows (mkt h) : %d", len(fp_rows))
    log.info("")
    log.info("  ┌─────────────────────────────────────────────────────┐")
    log.info("  │  TRUE  POSITIVE RATE : %5.1f%%  (defense in event)  │", 100 * tp_rate)
    log.info("  │  FALSE POSITIVE RATE : %5.1f%%  (defense out event) │", 100 * fp_rate)
    log.info("  │  EVENT CAPTURE RATE  : %d/%d  (%.0f%% events hit)       │",
             events_captured, len(MACRO_TS), 100 * event_capture_rate)
    log.info("  └─────────────────────────────────────────────────────┘")
    log.info("")
    log.info("  Trigger breakdown (n=%d triggered rows):", n_triggered)
    for k, v in sorted(trigger_counts.items(), key=lambda x: -x[1]):
        pct = 100 * v / max(n_triggered, 1)
        log.info("    %-22s : %6d  (%.1f%%)", k, v, pct)
    log.info("")
    log.info("  Z-score percentiles — NORMAL market (outside events):")
    for col, label in [("z_spread","spread"), ("z_bid","bid_depth"),
                       ("z_ask","ask_depth"), ("z_imb","imbalance")]:
        v = normal_rows[col]
        log.info("    %-12s  p95=%+.2f  p99=%+.2f  min=%+.2f  max=%+.2f",
                 label, v.quantile(0.95), v.quantile(0.99), v.min(), v.max())
    log.info("")

    # ── DoD Validation ───────────────────────────────────────────────────────
    log.info("  DoD Validation:")
    fp_pass = fp_rate < 0.02
    tp_pass = tp_rate > 0.20
    ev_pass = event_capture_rate >= 0.50

    log.info("    FP < 2%%   : %s  (%.2f%%)", "✓ PASS" if fp_pass else "✗ FAIL", 100 * fp_rate)
    log.info("    TP > 20%%  : %s  (%.1f%%)", "✓ PASS" if tp_pass else "✗ FAIL", 100 * tp_rate)
    log.info("    Capture≥50%%: %s  (%d/%d)", "✓ PASS" if ev_pass else "✗ FAIL",
             events_captured, len(MACRO_TS))

    overall = fp_pass and tp_pass and ev_pass
    log.info("")
    log.info("  VERDICT: %s", "✓ READY FOR LEVEL 2" if overall else "✗ THRESHOLDS NEED TUNING")
    log.info("=" * 65)

    if out_csv:
        df.to_csv(out_csv, index=False)
        log.info("Full results → %s", out_csv)

    return {
        "total_rows"         : total_rows,
        "tp_rate"            : tp_rate,
        "fp_rate"            : fp_rate,
        "event_capture_rate" : event_capture_rate,
        "events_captured"    : events_captured,
        "total_events"       : len(MACRO_TS),
        "trigger_breakdown"  : trigger_counts,
        "dod_pass"           : overall,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Grenadier Sprint 3 — Level 1 Backtest")
    parser.add_argument("--window", type=int, default=2,
                        help="Event window ±minutes (default: 2)")
    parser.add_argument("--out",    type=Path,
                        default=OUT_DIR / "defense_backtest.csv",
                        help="Output CSV path")
    args = parser.parse_args()
    run_backtest(window_min=args.window, out_csv=args.out)


if __name__ == "__main__":
    main()

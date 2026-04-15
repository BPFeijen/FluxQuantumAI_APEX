"""
build_anomaly_features.py — AnomalyForge V3: Phase 2 Feature Build (vectorized)
=================================================================================

Extrai 17 features de microestrutura usando pandas rolling — 100× mais rápido
que o loop timestamp-a-timestamp. Dataset de treino para Phase 3.

Lógica (por dia):
    1. Carregar trades + microstructure + depth_updates
    2. Resample trades para 1-segundo (buy_vol, sell_vol)
    3. Rolling 5/15/30min → VPIN, OFI, trade intensity
    4. Join com microstructure → spread z-score, depth ratio, toxicity
    5. Rolling 5min depth_updates → cancel_rate
    6. Guardar parquet diário

Output:
    data/processed/anomaly_features/anomaly_features_YYYY-MM-DD.parquet
    data/processed/anomaly_features_full.parquet  (consolidado)

Usage:
    python scripts/build_anomaly_features.py
    python scripts/build_anomaly_features.py --from 2026-01-01
    python scripts/build_anomaly_features.py --date 2026-04-07
"""

from __future__ import annotations

import argparse
import gzip
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

DATA_ROOT   = Path("C:/data/level2/_gc_xcec")
OUTPUT_DIR  = ROOT / "data" / "processed" / "anomaly_features"
OUTPUT_FULL = ROOT / "data" / "processed" / "anomaly_features_full.parquet"

FEATURE_NAMES = [
    "vpin_30m", "vpin_5m",
    "ofi_5m", "ofi_15m", "ofi_30m",
    "trade_intensity_5m", "avg_trade_size_5m",
    "large_trade_fraction_5m", "buy_aggressor_ratio_5m",
    "delta_acceleration",
    "spread_zscore_30m", "spread_zscore_2h",
    "depth_ratio", "depth_ratio_zscore_30m",
    "toxicity_trend", "dom_persistence",
    "cancel_rate_5m",
]

LARGE_TRADE_THR = 5.0
DOM_PERSIST_THR = 0.30
TOXICITY_WINDOW = 10   # leituras para slope


# ── I/O helpers ──────────────────────────────────────────────────────────────

def _load_gz(path: Path, ts_col_priority: list[str]) -> pd.DataFrame | None:
    """Lê CSV gzip, usa primeira coluna de timestamp disponível. Index = UTC naive."""
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, compression="gzip")
        ts_col = next((c for c in ts_col_priority if c in df.columns), None)
        if ts_col is None:
            return None
        df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="ISO8601")
        df[ts_col] = df[ts_col].dt.tz_localize(None)
        df = df.set_index(ts_col).sort_index()
        return df
    except Exception as e:
        print(f"    [WARN] {path.name}: {e}")
        return None


# ── Vectorized feature computation ───────────────────────────────────────────

def _build_trade_features(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Constrói features de trades vectorizadas.
    Resample para 1-segundo, depois rolling.
    """
    if trades is None or len(trades) < 10:
        return pd.DataFrame()

    t = trades.copy()
    t["size"] = pd.to_numeric(t["size"], errors="coerce").fillna(0)
    agg = t["aggressor"].str.lower()
    t["buy_vol"]   = t["size"].where(agg == "buy",  0.0)
    t["sell_vol"]  = t["size"].where(agg == "sell", 0.0)
    t["is_large"]  = (t["size"] > LARGE_TRADE_THR).astype(float)
    t["is_buy"]    = (agg == "buy").astype(float)

    # Resample para 1-segundo
    cols = ["buy_vol", "sell_vol", "size", "is_large", "is_buy"]
    r1s = t[cols].resample("1s").agg({
        "buy_vol":  "sum",
        "sell_vol": "sum",
        "size":     ["sum", "count"],
        "is_large": "sum",
        "is_buy":   "sum",
    })
    r1s.columns = ["buy_vol", "sell_vol", "total_vol", "trade_count", "large_count", "buy_count"]
    r1s = r1s.fillna(0)

    W5  = "5min"
    W15 = "15min"
    W30 = "30min"

    # Rolling sums
    bv5  = r1s["buy_vol"].rolling(W5,  min_periods=1).sum()
    sv5  = r1s["sell_vol"].rolling(W5,  min_periods=1).sum()
    bv15 = r1s["buy_vol"].rolling(W15, min_periods=1).sum()
    sv15 = r1s["sell_vol"].rolling(W15, min_periods=1).sum()
    bv30 = r1s["buy_vol"].rolling(W30, min_periods=1).sum()
    sv30 = r1s["sell_vol"].rolling(W30, min_periods=1).sum()
    tv5  = r1s["total_vol"].rolling(W5, min_periods=1).sum()
    tv30 = r1s["total_vol"].rolling(W30, min_periods=1).sum()
    cnt5 = r1s["trade_count"].rolling(W5, min_periods=1).sum()
    lc5  = r1s["large_count"].rolling(W5, min_periods=1).sum()
    bc5  = r1s["buy_count"].rolling(W5,  min_periods=1).sum()
    tc5  = cnt5.clip(lower=1)

    feat = pd.DataFrame(index=r1s.index)

    # VPIN = |buy_vol - sell_vol| / total_vol (rolling)
    feat["vpin_30m"] = (bv30 - sv30).abs() / tv30.clip(lower=1e-6)
    feat["vpin_5m"]  = (bv5  - sv5).abs()  / tv5.clip(lower=1e-6)
    feat["vpin_30m"] = feat["vpin_30m"].clip(0, 1)
    feat["vpin_5m"]  = feat["vpin_5m"].clip(0, 1)

    # OFI = (buy - sell) / total — signed
    feat["ofi_5m"]  = ((bv5  - sv5)  / (bv5  + sv5).clip(lower=1e-6)).clip(-1, 1)
    feat["ofi_15m"] = ((bv15 - sv15) / (bv15 + sv15).clip(lower=1e-6)).clip(-1, 1)
    feat["ofi_30m"] = ((bv30 - sv30) / (bv30 + sv30).clip(lower=1e-6)).clip(-1, 1)

    # Trade intensity (trades/min in last 5min)
    feat["trade_intensity_5m"] = cnt5 / 5.0

    # Avg trade size (last 5min)
    feat["avg_trade_size_5m"] = (
        r1s["total_vol"].rolling(W5, min_periods=1).sum() /
        tc5
    )

    # Large trade fraction
    feat["large_trade_fraction_5m"] = lc5 / tc5

    # Buy aggressor ratio
    feat["buy_aggressor_ratio_5m"] = bc5 / tc5

    # Delta acceleration: slope of cumulative_delta rolling 30min
    if "cumulative_delta" in t.columns:
        cd = pd.to_numeric(t["cumulative_delta"], errors="coerce")
        cd_1s = cd.resample("1s").last().ffill()
        # Rolling slope via polyfit approximation: cov(t,y)/var(t)
        def rolling_slope(s, window="30min"):
            s = s.ffill()
            idx_num = np.arange(len(s), dtype=float)
            idx_s = pd.Series(idx_num, index=s.index)
            roll_cov = s.rolling(window, min_periods=5).cov(idx_s)
            roll_var = idx_s.rolling(window, min_periods=5).var().clip(lower=1e-6)
            return roll_cov / roll_var

        feat["delta_acceleration"] = rolling_slope(cd_1s).reindex(feat.index, method="nearest")
    else:
        feat["delta_acceleration"] = 0.0

    feat = feat.fillna(0.0)
    return feat


def _build_micro_features(micro: pd.DataFrame) -> pd.DataFrame:
    """Features vectorizadas de microestrutura (spread z-score, depth, toxicity, dom)."""
    if micro is None or len(micro) < 5:
        return pd.DataFrame()

    m = micro.copy()
    feat = pd.DataFrame(index=m.index)

    # Spread z-score 30min e 2h
    if "spread" in m.columns:
        sp = pd.to_numeric(m["spread"], errors="coerce").ffill()
        sp_mu30  = sp.rolling("30min", min_periods=3).mean()
        sp_std30 = sp.rolling("30min", min_periods=3).std().clip(lower=1e-8)
        sp_mu2h  = sp.rolling("120min", min_periods=5).mean()
        sp_std2h = sp.rolling("120min", min_periods=5).std().clip(lower=1e-8)
        feat["spread_zscore_30m"] = ((sp - sp_mu30)  / sp_std30).clip(-10, 10).fillna(0)
        feat["spread_zscore_2h"]  = ((sp - sp_mu2h)  / sp_std2h).clip(-10, 10).fillna(0)
    else:
        feat["spread_zscore_30m"] = 0.0
        feat["spread_zscore_2h"]  = 0.0

    # Depth ratio + z-score
    if "total_bid_size" in m.columns and "total_ask_size" in m.columns:
        bid = pd.to_numeric(m["total_bid_size"], errors="coerce").fillna(0)
        ask = pd.to_numeric(m["total_ask_size"], errors="coerce").fillna(0).clip(lower=0.01)
        ratio = (bid / ask).clip(0.01, 100)
        r_mu30  = ratio.rolling("30min", min_periods=3).mean()
        r_std30 = ratio.rolling("30min", min_periods=3).std().clip(lower=1e-6)
        feat["depth_ratio"]            = ratio.fillna(1.0)
        feat["depth_ratio_zscore_30m"] = ((ratio - r_mu30) / r_std30).clip(-10, 10).fillna(0)
    else:
        feat["depth_ratio"]            = 1.0
        feat["depth_ratio_zscore_30m"] = 0.0

    # Toxicity trend (rolling slope, last 10 readings → 1 value per row)
    if "toxicity_score" in m.columns:
        tox = pd.to_numeric(m["toxicity_score"], errors="coerce").fillna(0)
        # Slope = rolling_cov(t, y) / rolling_var(t)
        idx_num = pd.Series(np.arange(len(tox), dtype=float), index=tox.index)
        w = TOXICITY_WINDOW
        rcov = tox.rolling(w, min_periods=3).cov(idx_num)
        rvar = idx_num.rolling(w, min_periods=3).var().clip(lower=1e-6)
        feat["toxicity_trend"] = (rcov / rvar).fillna(0).clip(-1, 1)
    else:
        feat["toxicity_trend"] = 0.0

    # DOM persistence (fraction of last 10 readings with |dom| > 0.3)
    if "dom_imbalance" in m.columns:
        dom = pd.to_numeric(m["dom_imbalance"], errors="coerce").fillna(0)
        extreme = (dom.abs() > DOM_PERSIST_THR).astype(float)
        feat["dom_persistence"] = extreme.rolling(10, min_periods=2).mean().fillna(0)
    else:
        feat["dom_persistence"] = 0.0

    return feat


def _build_cancel_features(updates: pd.DataFrame) -> pd.DataFrame:
    """Cancel rate vectorizado de depth_updates."""
    if updates is None or len(updates) < 5:
        return pd.DataFrame()

    u = updates.copy()
    if "level_index" in u.columns:
        lvl = pd.to_numeric(u["level_index"], errors="coerce").fillna(99)
        u = u[lvl <= 5]
    if len(u) == 0:
        return pd.DataFrame()

    u["is_delete"] = (u["action"].str.lower() == "delete").astype(float)
    u["is_any"]    = 1.0

    r1s = u[["is_delete", "is_any"]].resample("1s").sum().fillna(0)
    del_5m = r1s["is_delete"].rolling("5min", min_periods=1).sum()
    tot_5m = r1s["is_any"].rolling("5min", min_periods=1).sum().clip(lower=1)
    feat = pd.DataFrame({"cancel_rate_5m": (del_5m / tot_5m).clip(0, 1)})
    return feat


# ── Day builder ───────────────────────────────────────────────────────────────

def build_day(date_str: str) -> pd.DataFrame | None:
    """Constrói features para um dia completo. Retorna DataFrame ou None."""
    ts_cols = ["recv_timestamp", "recv_ts"]

    trades  = _load_gz(DATA_ROOT / f"trades_{date_str}.csv.gz",        ts_cols)
    micro   = _load_gz(DATA_ROOT / f"microstructure_{date_str}.csv.gz", ts_cols)
    updates = _load_gz(DATA_ROOT / f"depth_updates_{date_str}.csv.gz",  ts_cols)

    if micro is None or len(micro) < 5:
        return None

    # Computar features vectorizadas
    tf = _build_trade_features(trades)
    mf = _build_micro_features(micro)
    cf = _build_cancel_features(updates)

    # Base: timestamps do microstructure (mais completo)
    base_idx = micro.index

    def align(feat_df: pd.DataFrame, target_idx) -> pd.DataFrame:
        if feat_df.empty:
            return pd.DataFrame(index=target_idx)
        return feat_df.reindex(target_idx, method="nearest", tolerance=pd.Timedelta("60s"))

    result = pd.DataFrame(index=base_idx)

    # Trade features
    tf_aligned = align(tf, base_idx)
    for col in [c for c in FEATURE_NAMES if c in (tf_aligned.columns if not tf_aligned.empty else [])]:
        result[col] = tf_aligned[col]

    # Micro features
    for col in [c for c in FEATURE_NAMES if c in mf.columns]:
        result[col] = mf[col].reindex(base_idx, method="nearest", tolerance=pd.Timedelta("300s"))

    # Cancel features
    cf_aligned = align(cf, base_idx)
    if not cf_aligned.empty and "cancel_rate_5m" in cf_aligned.columns:
        result["cancel_rate_5m"] = cf_aligned["cancel_rate_5m"]

    # Preencher colunas em falta com 0
    for col in FEATURE_NAMES:
        if col not in result.columns:
            result[col] = 0.0

    result = result[FEATURE_NAMES].fillna(0.0)
    result["date"] = date_str
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def get_dates(from_date: str, to_date: str) -> list[str]:
    files = sorted(DATA_ROOT.glob("microstructure_*.csv.gz"))
    return [
        f.name.replace("microstructure_", "").replace(".csv.gz", "")
        for f in files
        if from_date <= f.name.replace("microstructure_", "").replace(".csv.gz", "") <= to_date
    ]


def main(from_date: str, to_date: str, single_date: str | None = None, force: bool = False):
    print()
    print("=" * 68)
    print("  ANOMALY FEATURES BUILD -- Phase 2 (vectorized)")
    print(f"  Data root: {DATA_ROOT}")
    print(f"  Output:    {OUTPUT_DIR}")
    print("=" * 68)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    dates = [single_date] if single_date else get_dates(from_date, to_date)
    print(f"\n  Datas: {len(dates)}  ({dates[0] if dates else '?'} a {dates[-1] if dates else '?'})")

    processed = 0
    total_rows = 0

    for i, date_str in enumerate(dates):
        out_path = OUTPUT_DIR / f"anomaly_features_{date_str}.parquet"
        if out_path.exists() and not force:
            n = len(pd.read_parquet(out_path))
            total_rows += n
            processed  += 1
            print(f"  [{i+1:>3}/{len(dates)}] {date_str}  SKIP ({n} rows cached)")
            continue

        try:
            df = build_day(date_str)
        except Exception as e:
            print(f"  [{i+1:>3}/{len(dates)}] {date_str}  ERROR: {e}")
            continue

        if df is None or len(df) == 0:
            print(f"  [{i+1:>3}/{len(dates)}] {date_str}  SEM DADOS")
            continue

        df.to_parquet(out_path)
        total_rows += len(df)
        processed  += 1

        vpin_m = df["vpin_30m"].mean()
        ofi_m  = df["ofi_30m"].mean()
        cr_m   = df["cancel_rate_5m"].mean()
        print(f"  [{i+1:>3}/{len(dates)}] {date_str}  {len(df):>5} rows  "
              f"vpin={vpin_m:.3f}  ofi={ofi_m:+.3f}  cancel={cr_m:.3f}")

    print(f"\n  Processados: {processed}  Total rows: {total_rows:,}")

    # Consolidar
    print(f"\n  A consolidar -> {OUTPUT_FULL.name} ...")
    daily = sorted(OUTPUT_DIR.glob("anomaly_features_*.parquet"))
    dfs = []
    for f in daily:
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            pass

    if not dfs:
        print("  Nenhum ficheiro diario encontrado.")
        return

    full = pd.concat(dfs).sort_index()
    full.to_parquet(OUTPUT_FULL)
    print(f"  Full: {len(full):,} rows x {len(full.columns)} cols")
    print(f"  Periodo: {str(full.index.min())[:19]} a {str(full.index.max())[:19]}")

    print("\n  Feature coverage (% nao-zero):")
    for col in FEATURE_NAMES:
        nonzero = (full[col] != 0.0).mean() * 100
        bar = "#" * int(nonzero / 5)
        print(f"    {col:<30} {nonzero:5.1f}%  {bar}")

    print()
    print("=" * 68)
    print("  PHASE 2 BUILD CONCLUIDO")
    print("=" * 68)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from",  dest="from_date",   default="2025-11-26")
    parser.add_argument("--to",    dest="to_date",     default=datetime.utcnow().strftime("%Y-%m-%d"))
    parser.add_argument("--date",  dest="single_date", default=None)
    parser.add_argument("--force", dest="force",       action="store_true",
                        help="Reprocessar mesmo que o ficheiro ja exista")
    args = parser.parse_args()
    main(from_date=args.from_date, to_date=args.to_date,
         single_date=args.single_date, force=args.force)

"""
build_entry_quality_dataset.py — Entry Quality Classifier: Dataset Builder
===========================================================================

Passo 1: Para cada sinal de entrada em gc_ats_features_v5.parquet,
         simular outcome usando OHLC forward — TP1 ou SL atingido primeiro?

Passo 2: Construir feature set sem lookahead.

Output: data/processed/entry_quality_dataset.parquet
        data/processed/entry_quality_dataset_report.txt

Usage:
    python scripts/build_entry_quality_dataset.py
    python scripts/build_entry_quality_dataset.py --max-bars 96  # lookforward window
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parents[1]

FEATURES_PATH  = ROOT / "data" / "processed" / "gc_ats_features_v5.parquet"
OUTPUT_PATH    = ROOT / "data" / "processed" / "entry_quality_dataset.parquet"
REPORT_PATH    = ROOT / "data" / "processed" / "entry_quality_dataset_report.txt"

# Barras M30 máximas a olhar para frente (default: 96 × 30min = 48h)
DEFAULT_MAX_BARS = 96

# Colunas de lookahead a REMOVER do feature set (conhecidas só após a entrada)
LOOKAHEAD_COLS = [
    "entry_long", "entry_short", "entry_price",
    "sl_long", "sl_short",
    "tp1_long", "tp1_short",
    "tp2_long", "tp2_short",
    "rr_long", "rr_short",
]

# Colunas a remover por serem IDs/labels derivados depois do sinal
POST_SIGNAL_COLS = [
    "entry_trigger",  # é gerado junto com o sinal mas é categórico — manter encoded
]


def simulate_outcome(df: pd.DataFrame, idx: int, direction: str,
                     entry_price: float, sl: float, tp1: float,
                     max_bars: int) -> str:
    """
    Olha para frente a partir de idx no DataFrame (barras M30).
    Retorna: 'WIN', 'LOSS', ou 'TIMEOUT' (nenhum nível atingido em max_bars).

    Para SHORT: WIN se low <= tp1 antes de high >= sl
    Para LONG:  WIN se high >= tp1 antes de low <= sl
    """
    future = df.iloc[idx + 1: idx + 1 + max_bars]
    for _, bar in future.iterrows():
        if direction == "SHORT":
            sl_hit = bar["high"] >= sl
            tp_hit = bar["low"]  <= tp1
        else:  # LONG
            sl_hit = bar["low"]  <= sl
            tp_hit = bar["high"] >= tp1

        # Se ambos na mesma barra: conservador → LOSS (bar abriu contra)
        if sl_hit and tp_hit:
            # Usar open para desempate: se open mais perto de SL → LOSS
            if direction == "SHORT":
                return "LOSS" if bar["open"] > entry_price else "WIN"
            else:
                return "LOSS" if bar["open"] < entry_price else "WIN"
        if tp_hit:
            return "WIN"
        if sl_hit:
            return "LOSS"

    return "TIMEOUT"


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Label-encode colunas categóricas para uso em XGBoost."""
    cat_cols = df.select_dtypes(include=["object", "category", "bool"]).columns.tolist()
    for col in cat_cols:
        df[col] = pd.Categorical(df[col]).codes  # -1 para NaN
    return df


def main(max_bars: int = DEFAULT_MAX_BARS):
    print()
    print("=" * 68)
    print("  ENTRY QUALITY DATASET BUILDER")
    print(f"  Source: gc_ats_features_v5.parquet")
    print(f"  Lookforward window: {max_bars} barras M30 = {max_bars//2}h")
    print("=" * 68)

    df = pd.read_parquet(FEATURES_PATH)
    print(f"\n  Carregado: {len(df):,} barras M30  ({df.index.min().date()} → {df.index.max().date()})")

    # ── Identificar sinais de entrada ──────────────────────────────────────
    mask_short = df["entry_short"].fillna(False).astype(bool)
    mask_long  = df["entry_long"].fillna(False).astype(bool)
    entry_mask = mask_short | mask_long

    entries = df[entry_mask].copy()
    print(f"  Sinais: {len(entries)}  (SHORT={mask_short.sum()}  LONG={mask_long.sum()})")

    # ── Simular outcomes ───────────────────────────────────────────────────
    print("\n  Simulando outcomes...")
    outcomes   = []
    directions = []
    pnl_pts    = []
    bars_held  = []

    df_reset = df.reset_index()   # para acesso por posição inteira
    idx_map  = {ts: i for i, ts in enumerate(df.index)}

    for ts, row in entries.iterrows():
        is_short = bool(row.get("entry_short", False))
        direction = "SHORT" if is_short else "LONG"

        entry_price = row["entry_price"]
        sl  = row["sl_short"]  if is_short else row["sl_long"]
        tp1 = row["tp1_short"] if is_short else row["tp1_long"]

        if pd.isna(sl) or pd.isna(tp1) or pd.isna(entry_price):
            outcomes.append("SKIP")
            directions.append(direction)
            pnl_pts.append(np.nan)
            bars_held.append(np.nan)
            continue

        pos = idx_map[ts]
        result = simulate_outcome(df, pos, direction, entry_price, sl, tp1, max_bars)

        outcomes.append(result)
        directions.append(direction)

        if result == "WIN":
            pts = abs(tp1 - entry_price)
            pnl_pts.append(pts if direction == "SHORT" else pts)
        elif result == "LOSS":
            pts = abs(sl - entry_price)
            pnl_pts.append(-pts)
        else:
            pnl_pts.append(0.0)

        # contar barras até outcome (aproximação)
        bars_held.append(np.nan)

    entries["outcome"]   = outcomes
    entries["direction"] = directions
    entries["pnl_pts"]   = pnl_pts

    # ── Distribuição de outcomes ───────────────────────────────────────────
    vc = pd.Series(outcomes).value_counts()
    print(f"\n  Outcome distribution:")
    for k, v in vc.items():
        pct = v / len(outcomes) * 100
        print(f"    {k:<10} {v:>4}  ({pct:.1f}%)")

    # Remover SKIP e TIMEOUT para dataset supervisionado
    clean = entries[entries["outcome"].isin(["WIN", "LOSS"])].copy()
    clean["win"] = (clean["outcome"] == "WIN").astype(int)
    print(f"\n  Após remover SKIP/TIMEOUT: {len(clean)} trades rotulados")
    print(f"    WIN:  {clean['win'].sum()}  ({clean['win'].mean()*100:.1f}%)")
    print(f"    LOSS: {(~clean['win'].astype(bool)).sum()}  ({(1-clean['win'].mean())*100:.1f}%)")

    # ── Feature set (sem lookahead) ────────────────────────────────────────
    drop_cols = LOOKAHEAD_COLS + ["outcome", "pnl_pts"]
    feature_cols = [c for c in clean.columns if c not in drop_cols + ["win", "direction"]]

    print(f"\n  Features seleccionadas: {len(feature_cols)}")

    # Encode categoricals
    clean_encoded = clean[feature_cols + ["win", "direction", "pnl_pts", "outcome"]].copy()
    clean_encoded = encode_categoricals(clean_encoded)

    # ── Análise por direction e entry_grade ───────────────────────────────
    print("\n  Win Rate por DIRECTION:")
    for d, grp in clean.groupby("direction"):
        wr = grp["win"].mean() * 100
        print(f"    {d:<8} n={len(grp):>3}  WR={wr:.0f}%  avg_pnl={grp['pnl_pts'].mean():+.1f}pts")

    if "entry_grade" in clean.columns:
        print("\n  Win Rate por ENTRY_GRADE:")
        for g, grp in clean.groupby("entry_grade"):
            wr = grp["win"].mean() * 100
            print(f"    {str(g):<8} n={len(grp):>3}  WR={wr:.0f}%  avg_pnl={grp['pnl_pts'].mean():+.1f}pts")

    if "l2_entry_score" in clean.columns:
        print("\n  Win Rate por L2_ENTRY_SCORE (percentis):")
        n_unique = clean["l2_entry_score"].nunique()
        n_q = min(4, n_unique)
        labels_q = ["Q1","Q2","Q3","Q4"][:n_q]
        clean["l2_bin"] = pd.qcut(clean["l2_entry_score"], q=n_q, labels=labels_q, duplicates="drop")
        for b, grp in clean.groupby("l2_bin", observed=True):
            wr = grp["win"].mean() * 100
            rng = f"[{grp['l2_entry_score'].min():.0f}–{grp['l2_entry_score'].max():.0f}]"
            print(f"    {str(b):<4} {rng:<12} n={len(grp):>3}  WR={wr:.0f}%  avg_pnl={grp['pnl_pts'].mean():+.1f}pts")

    if "l2_danger_score" in clean.columns:
        print("\n  Win Rate por L2_DANGER_SCORE > 0:")
        for d_pos, grp in clean.groupby(clean["l2_danger_score"] > 0):
            label = "danger>0" if d_pos else "danger=0"
            wr = grp["win"].mean() * 100
            print(f"    {label:<12} n={len(grp):>3}  WR={wr:.0f}%  avg_pnl={grp['pnl_pts'].mean():+.1f}pts")

    if "entry_trigger" in clean.columns:
        print("\n  Win Rate por ENTRY_TRIGGER (top 10):")
        for t, grp in sorted(clean.groupby("entry_trigger"), key=lambda x: -len(x[1]))[:10]:
            wr = grp["win"].mean() * 100
            print(f"    {str(t):<25} n={len(grp):>3}  WR={wr:.0f}%")

    # ── Guardar ────────────────────────────────────────────────────────────
    clean_encoded.to_parquet(OUTPUT_PATH)
    print(f"\n  Guardado: {OUTPUT_PATH}  ({len(clean_encoded)} rows, {len(clean_encoded.columns)} cols)")

    # Report texto
    report_lines = [
        "ENTRY QUALITY DATASET REPORT",
        f"Generated: 2026-04-12",
        f"Source: gc_ats_features_v5.parquet",
        f"Lookforward: {max_bars} M30 bars = {max_bars//2}h",
        "",
        f"Total entries scanned: {len(entries)}",
        f"Outcomes: {vc.to_dict()}",
        f"Clean dataset (WIN+LOSS): {len(clean)}",
        f"  WIN:  {clean['win'].sum()} ({clean['win'].mean()*100:.1f}%)",
        f"  LOSS: {(~clean['win'].astype(bool)).sum()} ({(1-clean['win'].mean())*100:.1f}%)",
        "",
        f"Feature columns: {len(feature_cols)}",
        str(feature_cols),
    ]
    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")

    print()
    print("=" * 68)
    print("  PASSO 1 CONCLUIDO")
    print("=" * 68)
    print()

    return clean_encoded, feature_cols


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-bars", type=int, default=DEFAULT_MAX_BARS)
    args = parser.parse_args()
    main(max_bars=args.max_bars)

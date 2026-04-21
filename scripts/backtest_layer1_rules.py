"""
backtest_layer1_rules.py — Validação das 6 regras Layer 1 nos trades históricos
================================================================================

Para cada trade fechado (ats_trades_live_l2.parquet), avalia o que as regras
Layer 1 teriam decidido no momento da entrada e compara com o outcome real.

Métricas de interesse:
    - Quantas trades teriam sido bloqueadas (HARD_VETO)?
    - Desses bloqueios, qual % eram LOSSES? (precision do veto)
    - Quais regras disparam mais?
    - Qual o impacto no Profit Factor?

Usage:
    python scripts/backtest_layer1_rules.py
    python scripts/backtest_layer1_rules.py --window 15  # minutos de janela
"""

from __future__ import annotations

import argparse
import gzip
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

TRADES_PATH   = Path("C:/FluxQuantumAPEX/APEX GOLD/Data/backtest/ats_trades_live_l2.parquet")
MICRO_DIR     = Path("C:/data/level2/_gc_xcec")
OUTPUT_PATH   = ROOT / "data" / "processed" / "layer1_backtest_results.parquet"
REPORT_PATH   = ROOT / "data" / "processed" / "layer1_backtest_report.txt"

DEFAULT_WINDOW_MIN = 30


def load_microstructure(date_str: str) -> pd.DataFrame | None:
    path = MICRO_DIR / f"microstructure_{date_str}.csv.gz"
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            df = pd.read_csv(f)
        # Schema v1: recv_ts | Schema v2: recv_timestamp (evolução em Jan 2026)
        if "recv_ts" in df.columns:
            ts_col = "recv_ts"
        elif "recv_timestamp" in df.columns:
            ts_col = "recv_timestamp"
        else:
            print(f"  [WARN] Coluna de timestamp não encontrada em {path.name}")
            return None
        df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="ISO8601")
        df = df.set_index(ts_col).sort_index()
        df.index.name = "timestamp"
        return df
    except Exception as e:
        print(f"  [WARN] Erro a ler {path.name}: {e}")
        return None


def get_window_df(micro_df: pd.DataFrame, entry_ts: pd.Timestamp, window_min: int) -> pd.DataFrame:
    # Normalizar o DataFrame para DatetimeIndex sem tz
    df = micro_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)

    # Normalizar entry_ts para sem tz
    entry_ts_naive = pd.Timestamp(entry_ts)
    if entry_ts_naive.tzinfo is not None:
        entry_ts_naive = entry_ts_naive.tz_convert("UTC").tz_localize(None)

    cutoff_naive = entry_ts_naive - pd.Timedelta(minutes=window_min)

    window = df[(df.index >= cutoff_naive) & (df.index <= entry_ts_naive)]
    return window


def main(window_min: int = DEFAULT_WINDOW_MIN):
    from apex_nextgen.providers.anomaly_forge_v3.layer1_rules import evaluate_all_rules

    print()
    print("=" * 68)
    print("  LAYER 1 RULES BACKTEST")
    print(f"  Trades: {TRADES_PATH}")
    print(f"  Micro dir: {MICRO_DIR}")
    print(f"  Window: {window_min}min")
    print("=" * 68)

    if not TRADES_PATH.exists():
        print(f"\n  [ERROR] {TRADES_PATH} não encontrado")
        sys.exit(1)

    trades = pd.read_parquet(TRADES_PATH)
    print(f"\n  Carregados: {len(trades)} trades")
    print(f"  Colunas: {list(trades.columns[:10])}...")

    # Normalizar timestamp
    if "entry_time" in trades.columns:
        ts_col = "entry_time"
    elif "entry_bar" in trades.columns:
        ts_col = "entry_bar"
    elif "open_time" in trades.columns:
        ts_col = "open_time"
    elif "timestamp" in trades.columns:
        ts_col = "timestamp"
    else:
        ts_col = trades.columns[0]
        print(f"  [WARN] Usando coluna '{ts_col}' como timestamp")

    trades[ts_col] = pd.to_datetime(trades[ts_col])

    # Coluna de outcome
    if "win" in trades.columns:
        trades["is_loss"] = ~trades["win"].astype(bool)
    elif "exit_reason" in trades.columns:
        trades["is_loss"] = trades["exit_reason"].str.upper().str.contains("SL|LOSS|STOP", na=False)
    elif "total_pnl" in trades.columns:
        trades["is_loss"] = trades["total_pnl"] < 0
    elif "l1_pnl" in trades.columns:
        trades["is_loss"] = trades["l1_pnl"] < 0
    else:
        print("  [WARN] Sem coluna de outcome — assumindo desconhecido")
        trades["is_loss"] = None

    if "total_pnl" in trades.columns:
        pnl_col = "total_pnl"
    elif "l1_pnl" in trades.columns:
        pnl_col = "l1_pnl"
    else:
        pnl_col = None

    # ── Avaliar cada trade ─────────────────────────────────────────────────
    results = []
    micro_cache: dict[str, pd.DataFrame | None] = {}

    for i, (idx, trade) in enumerate(trades.iterrows()):
        entry_ts = pd.Timestamp(trade[ts_col])
        date_str = entry_ts.strftime("%Y-%m-%d")

        if date_str not in micro_cache:
            micro_cache[date_str] = load_microstructure(date_str)

        micro_df = micro_cache[date_str]

        if micro_df is None or len(micro_df) == 0:
            results.append({
                "trade_idx":     idx,
                "entry_ts":      entry_ts,
                "is_loss":       trade.get("is_loss"),
                "veto_level":    "NO_DATA",
                "warning_count": 0,
                "fired_rules":   "",
                "data_rows":     0,
            })
            continue

        window_df = get_window_df(micro_df, entry_ts, window_min)

        if len(window_df) < 3:
            window_df = micro_df[micro_df.index <= entry_ts].tail(10)

        decision = evaluate_all_rules(window_df)

        results.append({
            "trade_idx":     idx,
            "entry_ts":      entry_ts,
            "date":          date_str,
            "is_loss":       trade.get("is_loss"),
            "veto_level":    decision.veto_level,
            "warning_count": decision.warning_count,
            "fired_rules":   ",".join(decision.fired_rules),
            "data_rows":     decision.data_rows,
            "data_minutes":  decision.data_minutes,
            **{f"r_{r.rule_id}": r.fired for r in decision.rules},
        })

        if i % 10 == 0:
            print(f"  Progresso: {i+1}/{len(trades)}", end="\r")

    print(f"  Processados: {len(results)} trades        ")

    df_res = pd.DataFrame(results)

    # ── Estatísticas ───────────────────────────────────────────────────────
    print()
    has_outcome = df_res["is_loss"].notna()
    total_valid = has_outcome.sum()

    print(f"\n  Trades com outcome conhecido: {total_valid}/{len(df_res)}")

    veto_counts = df_res["veto_level"].value_counts()
    print(f"\n  Decisão Layer 1:")
    for veto, cnt in veto_counts.items():
        pct = cnt / len(df_res) * 100
        print(f"    {veto:<15} {cnt:>3}  ({pct:.0f}%)")

    # HARD_VETO analysis
    hard_veto = df_res[df_res["veto_level"] == "HARD_VETO"]
    if len(hard_veto) > 0 and total_valid > 0:
        hv_with_outcome = hard_veto[hard_veto["is_loss"].notna()]
        if len(hv_with_outcome) > 0:
            precision = hv_with_outcome["is_loss"].mean() * 100
            print(f"\n  HARD_VETO precision (% eram LOSSES): {precision:.0f}%")
            print(f"  HARD_VETO total blocked: {len(hard_veto)}")

    # Trades bloqueadas
    not_blocked = df_res[df_res["veto_level"] == "CLEAR"]
    soft_veto   = df_res[df_res["veto_level"] == "SOFT_VETO"]

    print(f"\n  Impacto:")
    print(f"    Passam CLEAR:       {len(not_blocked):>3}")
    print(f"    SOFT_VETO (×0.75):  {len(soft_veto):>3}")
    print(f"    HARD_VETO (block):  {len(hard_veto):>3}")

    # Profit Factor comparativo (se temos pnl)
    if pnl_col is not None and pnl_col in trades.columns:
        trades_with_pnl = trades.copy()
        trades_with_pnl["veto_level"] = df_res.set_index("trade_idx")["veto_level"].reindex(trades_with_pnl.index).values

        def pf(subset):
            gains  = subset[subset[pnl_col] > 0][pnl_col].sum()
            losses = abs(subset[subset[pnl_col] < 0][pnl_col].sum())
            return gains / losses if losses > 0 else float("inf")

        pf_total = pf(trades_with_pnl)
        pf_no_hv = pf(trades_with_pnl[trades_with_pnl["veto_level"] != "HARD_VETO"])

        print(f"\n  Profit Factor ({pnl_col}):")
        print(f"    Total (todos):         {pf_total:.3f}")
        print(f"    Sem HARD_VETO:         {pf_no_hv:.3f}")

    # Regras mais frequentes
    rule_cols = [c for c in df_res.columns if c.startswith("r_R")]
    if rule_cols:
        print(f"\n  Frequência de regras (% de trades onde disparou):")
        for col in rule_cols:
            rate = df_res[col].sum() / len(df_res) * 100
            rule_id = col.replace("r_", "")
            print(f"    {rule_id:<25} {df_res[col].sum():>3}  ({rate:.1f}%)")

    # Sem dados de microestrutura
    no_data = df_res[df_res["veto_level"] == "NO_DATA"]
    if len(no_data) > 0:
        print(f"\n  [INFO] {len(no_data)} trades sem dados de microestrutura (datas: {no_data['entry_ts'].dt.date.unique()})")

    # ── Guardar ────────────────────────────────────────────────────────────
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_res.to_parquet(OUTPUT_PATH)
    print(f"\n  Guardado: {OUTPUT_PATH}")

    # Report
    lines = [
        "LAYER 1 RULES BACKTEST REPORT",
        f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"Trades: {len(df_res)}",
        f"Window: {window_min}min",
        "",
        "VETO DISTRIBUTION:",
        veto_counts.to_string(),
        "",
        "RULE FREQUENCY:",
    ]
    if rule_cols:
        for col in rule_cols:
            lines.append(f"  {col.replace('r_', '')}: {df_res[col].sum()} fires ({df_res[col].sum()/len(df_res)*100:.1f}%)")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Report: {REPORT_PATH}")

    print()
    print("=" * 68)
    print("  BACKTEST CONCLUIDO")
    print("=" * 68)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_MIN,
                        help="Janela de microestrutura em minutos (default=30)")
    args = parser.parse_args()
    main(window_min=args.window)

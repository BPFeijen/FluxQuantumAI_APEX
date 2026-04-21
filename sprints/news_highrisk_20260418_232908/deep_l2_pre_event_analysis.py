"""
Deep L2 PRE-event analysis — "É 5min pause_before realmente o ideal?"
Mode: READ-ONLY. Mirror of deep_l2_post_event_analysis but for T-X entries.
"""
import numpy as np
import pandas as pd
from pathlib import Path

SPRINT_DIR = Path(r"C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908")
CAL_PARQUET = Path(r"C:\FluxQuantumAI\data\processed\news_calendar_us_full.parquet")
CAL_DS = Path(r"C:\FluxQuantumAI\data\processed\calibration_dataset_full.parquet")


def main():
    print("=" * 70)
    print("DEEP L2 PRE-EVENT ANALYSIS — 'É pause_before=5 ideal?'")
    print("=" * 70)

    cal = pd.read_parquet(CAL_PARQUET)
    cal["ts_utc"] = pd.to_datetime(cal["ts_utc"], utc=True)
    cal = cal[cal["importance"] == "HIGH"].reset_index(drop=True)
    print(f"HIGH events: {len(cal)}")

    px = pd.read_parquet(CAL_DS, columns=["close", "l2_dom_imbalance", "l2_bar_delta"])
    lo = cal["ts_utc"].min() - pd.Timedelta(hours=2)
    hi = cal["ts_utc"].max() + pd.Timedelta(hours=2)
    px = px.loc[(px.index >= lo) & (px.index <= hi)].copy()
    print(f"M1 bars (clipped): {len(px):,}")

    # ------------------------------------------------------------ #
    # Scenario A — "hold until T-0" (assume trade closes BEFORE event)
    # Entry at T-X, hold ends at T-0 (event moment)
    # This measures: "if I entered at T-X, how bad could it get BEFORE event hits?"
    # ------------------------------------------------------------ #
    print("\n" + "=" * 70)
    print("SCENARIO A — Hold until T-0 (close BEFORE event)")
    print("=" * 70)

    entry_offsets_A = [-30, -15, -10, -5, -3]   # negative = minutes before event

    recs_A = []
    for _, ev in cal.iterrows():
        t0 = ev["ts_utc"]
        etype = ev["event_type"]
        for m_offset in entry_offsets_A:
            entry_ts = t0 + pd.Timedelta(minutes=m_offset)
            entry_bar = px.loc[(px.index >= entry_ts) & (px.index < entry_ts + pd.Timedelta(minutes=1))]
            if len(entry_bar) == 0:
                continue
            p_entry = float(entry_bar["close"].iloc[-1])
            hold = px.loc[(px.index >= entry_ts) & (px.index < t0)]
            if len(hold) < 2:
                continue
            rets = (hold["close"] / p_entry - 1) * 1e4
            mae = abs(rets.min()) if (rets < 0).any() else 0.0
            mfe = abs(rets.max()) if (rets > 0).any() else 0.0
            final = float(rets.iloc[-1])
            recs_A.append({
                "event_type": etype,
                "entry_offset_min": m_offset,
                "hold_minutes": len(hold),
                "mae_bps": mae,
                "mfe_bps": mfe,
                "final_ret_bps": final,
            })

    df_A = pd.DataFrame(recs_A)
    agg_A = df_A.groupby(["event_type", "entry_offset_min"]).agg(
        n=("mae_bps", "count"),
        mae_mean=("mae_bps", "mean"),
        mae_p90=("mae_bps", lambda x: np.quantile(x, 0.90)),
        mfe_mean=("mfe_bps", "mean"),
        mfe_p90=("mfe_bps", lambda x: np.quantile(x, 0.90)),
        final_mean=("final_ret_bps", "mean"),
    ).reset_index()
    agg_A["mfe_mae_ratio"] = (agg_A["mfe_mean"] / agg_A["mae_mean"].replace(0, np.nan)).round(3)

    path_A = SPRINT_DIR / "deep_l2_pre_event_scenarioA.csv"
    agg_A.to_csv(path_A, index=False)
    print(f"Saved: {path_A}")
    print()
    print(agg_A.round(2).to_string(index=False))

    # ------------------------------------------------------------ #
    # Scenario B — "hold through event until T+30" (regular trade life)
    # Entry at T-X, closes at T+30 — how bad is the round-trip?
    # This measures the cost of being caught by the event.
    # ------------------------------------------------------------ #
    print("\n" + "=" * 70)
    print("SCENARIO B — Hold through event until T+30 (caught by release)")
    print("=" * 70)

    entry_offsets_B = [-30, -15, -10, -5, -3]

    recs_B = []
    for _, ev in cal.iterrows():
        t0 = ev["ts_utc"]
        etype = ev["event_type"]
        for m_offset in entry_offsets_B:
            entry_ts = t0 + pd.Timedelta(minutes=m_offset)
            entry_bar = px.loc[(px.index >= entry_ts) & (px.index < entry_ts + pd.Timedelta(minutes=1))]
            if len(entry_bar) == 0:
                continue
            p_entry = float(entry_bar["close"].iloc[-1])
            hold = px.loc[(px.index >= entry_ts) & (px.index < t0 + pd.Timedelta(minutes=31))]
            if len(hold) < 5:
                continue
            rets = (hold["close"] / p_entry - 1) * 1e4
            mae = abs(rets.min()) if (rets < 0).any() else 0.0
            mfe = abs(rets.max()) if (rets > 0).any() else 0.0
            final = float(rets.iloc[-1])
            recs_B.append({
                "event_type": etype,
                "entry_offset_min": m_offset,
                "mae_bps": mae,
                "mfe_bps": mfe,
                "final_ret_bps": final,
            })

    df_B = pd.DataFrame(recs_B)
    agg_B = df_B.groupby(["event_type", "entry_offset_min"]).agg(
        n=("mae_bps", "count"),
        mae_mean=("mae_bps", "mean"),
        mae_p90=("mae_bps", lambda x: np.quantile(x, 0.90)),
        mfe_mean=("mfe_bps", "mean"),
        mfe_p90=("mfe_bps", lambda x: np.quantile(x, 0.90)),
        final_mean=("final_ret_bps", "mean"),
    ).reset_index()
    agg_B["mfe_mae_ratio"] = (agg_B["mfe_mean"] / agg_B["mae_mean"].replace(0, np.nan)).round(3)

    path_B = SPRINT_DIR / "deep_l2_pre_event_scenarioB.csv"
    agg_B.to_csv(path_B, index=False)
    print(f"Saved: {path_B}")
    print()
    print(agg_B.round(2).to_string(index=False))

    # ------------------------------------------------------------ #
    # Scenario C — Finer granularity: MAE minute-by-minute T-30 to T-1
    # When does the pre-event "danger" actually start per event_type?
    # ------------------------------------------------------------ #
    print("\n" + "=" * 70)
    print("SCENARIO C — MAE progression at each minute T-30 → T-1 (hold until T-0)")
    print("=" * 70)

    recs_C = []
    for _, ev in cal.iterrows():
        t0 = ev["ts_utc"]
        etype = ev["event_type"]
        for m_offset in range(-30, 0):
            entry_ts = t0 + pd.Timedelta(minutes=m_offset)
            entry_bar = px.loc[(px.index >= entry_ts) & (px.index < entry_ts + pd.Timedelta(minutes=1))]
            if len(entry_bar) == 0:
                continue
            p_entry = float(entry_bar["close"].iloc[-1])
            hold = px.loc[(px.index >= entry_ts) & (px.index < t0)]
            if len(hold) == 0:
                continue
            rets = (hold["close"] / p_entry - 1) * 1e4
            mae = abs(rets.min()) if (rets < 0).any() else 0.0
            mfe = abs(rets.max()) if (rets > 0).any() else 0.0
            recs_C.append({
                "event_type": etype,
                "entry_offset": m_offset,
                "mae": mae,
                "mfe": mfe,
            })
    df_C = pd.DataFrame(recs_C)
    agg_C = df_C.groupby(["event_type", "entry_offset"]).agg(
        n=("mae", "count"),
        mae_mean=("mae", "mean"),
        mfe_mean=("mfe", "mean"),
    ).reset_index()
    agg_C["mfe_mae_ratio"] = (agg_C["mfe_mean"] / agg_C["mae_mean"].replace(0, np.nan)).round(3)

    # Pivot: for each event_type, MAE at T-5 vs T-15 vs T-30
    print("\nMAE_mean bps at each pre-event entry (hold until T-0):")
    pv_mae = agg_C.pivot_table(index="event_type", columns="entry_offset",
                                values="mae_mean", aggfunc="mean")
    key_offsets = [-30, -20, -15, -10, -5, -3, -1]
    key_offsets = [k for k in key_offsets if k in pv_mae.columns]
    print(pv_mae[key_offsets].round(2).to_string())

    print("\nMFE/MAE ratio at each pre-event entry:")
    pv_ratio = agg_C.pivot_table(index="event_type", columns="entry_offset",
                                  values="mfe_mae_ratio", aggfunc="mean")
    print(pv_ratio[key_offsets].round(2).to_string())

    # Check: is there a "drift" bucket where MAE grows as we approach T-0?
    # For each event_type, is MAE(T-5) > MAE(T-15) > MAE(T-30) ?
    print("\n=== DRIFT CHECK: does MAE grow as event approaches? ===")
    for etype in pv_mae.index:
        row = pv_mae.loc[etype]
        try:
            mae_30 = row[-30]
            mae_15 = row[-15]
            mae_5 = row[-5]
            mae_3 = row[-3]
            trend_5v30 = "↑" if mae_5 > mae_30 else "↓" if mae_5 < mae_30 else "≈"
            trend_3v5 = "↑" if mae_3 > mae_5 else "↓" if mae_3 < mae_5 else "≈"
            print(f"  {etype:12} MAE(-30)={mae_30:5.2f}  MAE(-15)={mae_15:5.2f}  "
                  f"MAE(-5)={mae_5:5.2f}  MAE(-3)={mae_3:5.2f}   "
                  f"T-5 vs T-30: {trend_5v30}   T-3 vs T-5: {trend_3v5}")
        except Exception:
            pass

    path_C = SPRINT_DIR / "deep_l2_pre_event_scenarioC.csv"
    agg_C.to_csv(path_C, index=False)
    print(f"\nSaved: {path_C}")

    print("\n[DONE]")


if __name__ == "__main__":
    main()

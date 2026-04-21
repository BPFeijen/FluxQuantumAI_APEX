"""
Deep L2 Post-Event Analysis — "When is it safe to trade after HIGH news?"
Task: follow-up to BACKTEST_WINDOWS_REPORT
Mode: READ-ONLY
Data: calibration_dataset_full.parquet (M1 OHLCV + L2) × extended calendar (117 HIGH events)
"""
import numpy as np
import pandas as pd
from pathlib import Path

SPRINT_DIR = Path(r"C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908")
CAL_PARQUET = Path(r"C:\FluxQuantumAI\data\processed\news_calendar_us_full.parquet")
CAL_DS = Path(r"C:\FluxQuantumAI\data\processed\calibration_dataset_full.parquet")

# Finer post-event buckets + baseline
BUCKETS = [
    ("PRE_60",      -60, -30),   # baseline quiet
    ("DURING",       0,  1),     # release minute
    ("POST_1_5",     1,  5),     # very early post
    ("POST_5_15",    5,  15),    # early-mid post
    ("POST_15_30",   15, 30),    # mid post
    ("POST_30_60",   30, 60),    # late post
]


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return np.nan
    pooled = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    if pooled == 0:
        return np.nan
    return (a.mean() - b.mean()) / pooled


def main():
    print("=" * 70)
    print("DEEP L2 POST-EVENT ANALYSIS — 'Earliest safe entry' per event type")
    print("=" * 70)

    cal = pd.read_parquet(CAL_PARQUET)
    cal["ts_utc"] = pd.to_datetime(cal["ts_utc"], utc=True)
    cal = cal[cal["importance"] == "HIGH"].reset_index(drop=True)
    print(f"HIGH events: {len(cal)}")
    print(f"event_type breakdown: {cal['event_type'].value_counts().to_dict()}")

    l2_cols = ["close", "l2_dom_imbalance", "l2_bar_delta",
               "l2_bid_pressure", "l2_ask_pressure"]
    px = pd.read_parquet(CAL_DS, columns=l2_cols)
    lo = cal["ts_utc"].min() - pd.Timedelta(hours=2)
    hi = cal["ts_utc"].max() + pd.Timedelta(hours=2)
    px = px.loc[(px.index >= lo) & (px.index <= hi)].copy()
    print(f"M1 bars (clipped): {len(px):,}")
    print(f"L2 coverage:")
    for c in l2_cols[1:]:
        cov = px[c].notna().mean() * 100
        print(f"  {c}: {cov:.1f}% non-null")

    # Pre-compute signed return (bps)
    px["ret_bps_signed"] = px["close"].pct_change() * 1e4
    px["ret_bps_abs"] = px["ret_bps_signed"].abs()

    # -------------------------------------------------- #
    # Analysis 1: Per event_type × bucket aggregates
    # -------------------------------------------------- #
    print("\n" + "=" * 70)
    print("ANALYSIS 1 — Per event_type × bucket: risk + direction + L2")
    print("=" * 70)

    records = []
    for etype in sorted(cal["event_type"].unique()):
        ev_ts = cal.loc[cal["event_type"] == etype, "ts_utc"].tolist()
        n_events = len(ev_ts)
        if n_events == 0:
            continue

        # Gather each bucket's minute samples across all events of this type
        bucket_data = {}
        for bname, lo_min, hi_min in BUCKETS:
            abs_rets, sgn_rets, domi, bdelta, bidp, askp = [], [], [], [], [], []
            for t0 in ev_ts:
                lo_ts = t0 + pd.Timedelta(minutes=lo_min)
                hi_ts = t0 + pd.Timedelta(minutes=hi_min)
                window = px.loc[(px.index >= lo_ts) & (px.index < hi_ts)]
                if len(window) == 0:
                    continue
                abs_rets.extend(window["ret_bps_abs"].dropna().tolist())
                sgn_rets.extend(window["ret_bps_signed"].dropna().tolist())
                domi.extend(window["l2_dom_imbalance"].dropna().tolist())
                bdelta.extend(window["l2_bar_delta"].dropna().tolist())
                bidp.extend(window["l2_bid_pressure"].dropna().tolist())
                askp.extend(window["l2_ask_pressure"].dropna().tolist())
            bucket_data[bname] = {
                "abs": np.array(abs_rets),
                "sgn": np.array(sgn_rets),
                "domi": np.array(domi),
                "bdelta": np.array(bdelta),
                "bidp": np.array(bidp),
                "askp": np.array(askp),
            }

        baseline_abs = bucket_data.get("PRE_60", {}).get("abs", np.array([]))
        baseline_mean = baseline_abs.mean() if len(baseline_abs) > 0 else np.nan

        for bname, _, _ in BUCKETS:
            d = bucket_data.get(bname, {})
            a = d.get("abs", np.array([]))
            s = d.get("sgn", np.array([]))
            if len(a) == 0:
                continue
            records.append({
                "event_type": etype,
                "n_events": n_events,
                "bucket": bname,
                "n_minutes": len(a),
                "abs_ret_mean_bps": round(float(a.mean()), 3),
                "abs_ret_p90_bps": round(float(np.quantile(a, 0.90)), 3),
                "abs_ret_p99_bps": round(float(np.quantile(a, 0.99)), 3),
                "ratio_vs_baseline": round(a.mean() / baseline_mean, 3) if baseline_mean > 0 else np.nan,
                "cohens_d_vs_baseline": round(cohens_d(a, baseline_abs), 3) if len(baseline_abs) > 0 else np.nan,
                "signed_ret_mean_bps": round(float(s.mean()), 3),
                "signed_ret_std_bps": round(float(s.std()), 3),
                "dom_imbalance_mean": round(float(d["domi"].mean()), 4) if len(d["domi"]) > 0 else np.nan,
                "dom_imbalance_std": round(float(d["domi"].std()), 4) if len(d["domi"]) > 0 else np.nan,
                "bar_delta_mean": round(float(d["bdelta"].mean()), 3) if len(d["bdelta"]) > 0 else np.nan,
                "bid_pressure_mean": round(float(d["bidp"].mean()), 3) if len(d["bidp"]) > 0 else np.nan,
                "ask_pressure_mean": round(float(d["askp"].mean()), 3) if len(d["askp"]) > 0 else np.nan,
            })

    agg_df = pd.DataFrame(records)
    agg_path = SPRINT_DIR / "deep_l2_post_event_aggregates.csv"
    agg_df.to_csv(agg_path, index=False)
    print(f"Saved: {agg_path}")
    print()
    print(agg_df.to_string(index=False))

    # -------------------------------------------------- #
    # Analysis 2: Per-event cumulative return trajectory
    # -------------------------------------------------- #
    print("\n" + "=" * 70)
    print("ANALYSIS 2 — Cumulative signed return trajectory T+0 to T+60")
    print("=" * 70)

    # For each event, compute cumulative return at each minute after T+0
    # Return at minute m relative to T+0 close
    traj_records = []
    for _, ev in cal.iterrows():
        t0 = ev["ts_utc"]
        etype = ev["event_type"]
        # Get T+0 reference close (the bar containing the event minute)
        ref_bar = px.loc[(px.index >= t0) & (px.index < t0 + pd.Timedelta(minutes=1))]
        if len(ref_bar) == 0:
            continue
        p0 = float(ref_bar["close"].iloc[-1])
        # Get subsequent 60 minutes
        window = px.loc[(px.index >= t0) & (px.index < t0 + pd.Timedelta(minutes=61))]
        for i, (ts, row) in enumerate(window.iterrows()):
            m_offset = int((ts - t0).total_seconds() / 60)
            if m_offset < 0 or m_offset > 60:
                continue
            cum_ret_bps = (float(row["close"]) / p0 - 1) * 1e4
            traj_records.append({
                "event_type": etype,
                "minute_offset": m_offset,
                "cum_ret_bps": cum_ret_bps,
                "abs_cum_ret_bps": abs(cum_ret_bps),
            })

    traj_df = pd.DataFrame(traj_records)
    # Aggregate per event_type × minute_offset
    traj_agg = traj_df.groupby(["event_type", "minute_offset"]).agg(
        n_events=("cum_ret_bps", "count"),
        mean_cum_ret_bps=("cum_ret_bps", "mean"),
        mean_abs_cum_ret_bps=("abs_cum_ret_bps", "mean"),
        std_cum_ret_bps=("cum_ret_bps", "std"),
        p90_abs=("abs_cum_ret_bps", lambda x: np.quantile(x, 0.90)),
    ).reset_index()

    traj_path = SPRINT_DIR / "deep_l2_cum_return_trajectory.csv"
    traj_agg.to_csv(traj_path, index=False)
    print(f"Saved: {traj_path}")

    # Print key snapshot: abs cum return at T+1, T+5, T+15, T+30, T+60
    print("\nAbs cumulative return (bps) per event_type at key minutes:")
    pivot = traj_agg.pivot_table(
        index="event_type",
        columns="minute_offset",
        values="mean_abs_cum_ret_bps",
        aggfunc="mean",
    )
    snapshot_mins = [1, 5, 15, 30, 60]
    snapshot_mins = [m for m in snapshot_mins if m in pivot.columns]
    if snapshot_mins:
        print(pivot[snapshot_mins].round(2).to_string())

    # Also signed cumulative return (directional persistence)
    print("\nSigned cumulative return (bps) per event_type — positive = up-move persists:")
    pivot_s = traj_agg.pivot_table(
        index="event_type",
        columns="minute_offset",
        values="mean_cum_ret_bps",
        aggfunc="mean",
    )
    if snapshot_mins:
        print(pivot_s[snapshot_mins].round(2).to_string())

    # -------------------------------------------------- #
    # Analysis 3: Max Adverse Excursion by bucket
    # Safe-entry proxy: if you enter at bucket-start, how bad is worst drawdown?
    # -------------------------------------------------- #
    print("\n" + "=" * 70)
    print("ANALYSIS 3 — MAE (Max Adverse Excursion) from bucket-start close")
    print("=" * 70)

    mae_records = []
    mae_buckets = [
        ("enter_T+0",   0,  60),
        ("enter_T+5",   5,  60),
        ("enter_T+15",  15, 60),
        ("enter_T+30",  30, 60),
    ]
    for _, ev in cal.iterrows():
        t0 = ev["ts_utc"]
        etype = ev["event_type"]
        for label, entry_min, hold_end_min in mae_buckets:
            entry_ts = t0 + pd.Timedelta(minutes=entry_min)
            entry_bar = px.loc[(px.index >= entry_ts) & (px.index < entry_ts + pd.Timedelta(minutes=1))]
            if len(entry_bar) == 0:
                continue
            p_entry = float(entry_bar["close"].iloc[-1])
            hold = px.loc[(px.index >= entry_ts) & (px.index < t0 + pd.Timedelta(minutes=hold_end_min))]
            if len(hold) < 2:
                continue
            # MAE for LONG: min(low)/entry-1; for SHORT: max(high)/entry-1; abs
            # Using close-based proxy since high/low might be sparse
            returns = (hold["close"] / p_entry - 1) * 1e4
            mae_bps = abs(returns.min())   # worst downside
            mfe_bps = abs(returns.max())   # best upside
            mae_records.append({
                "event_type": etype,
                "entry": label,
                "mae_bps": mae_bps,
                "mfe_bps": mfe_bps,
            })

    mae_df = pd.DataFrame(mae_records)
    mae_agg = mae_df.groupby(["event_type", "entry"]).agg(
        n=("mae_bps", "count"),
        mae_mean=("mae_bps", "mean"),
        mae_p90=("mae_bps", lambda x: np.quantile(x, 0.90)),
        mfe_mean=("mfe_bps", "mean"),
        mfe_p90=("mfe_bps", lambda x: np.quantile(x, 0.90)),
    ).reset_index()
    mae_agg["mfe_mae_ratio"] = (mae_agg["mfe_mean"] / mae_agg["mae_mean"]).round(3)

    mae_path = SPRINT_DIR / "deep_l2_mae_by_entry.csv"
    mae_agg.to_csv(mae_path, index=False)
    print(f"Saved: {mae_path}")
    print()
    print(mae_agg.round(2).to_string(index=False))

    print("\n[DONE]")


if __name__ == "__main__":
    main()

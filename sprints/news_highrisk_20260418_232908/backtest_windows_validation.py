"""
Backtest Windows Validation — Extended calendar (Jul 2025 → Apr 2026)
Task: CLAUDECODE_Backtest_Windows_Validation
Mode: READ-ONLY. Zero edits to production.
"""
import re
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

SPRINT_DIR = Path(r"C:\FluxQuantumAI\sprints\news_highrisk_20260418_232908")
CAL_XLSX = Path(r"C:\FluxQuantumAI\Economic Calendar History_2025-2026.xlsx")
CAL_DS = Path(r"C:\FluxQuantumAI\data\processed\calibration_dataset_full.parquet")
OUT_PARQUET = Path(r"C:\FluxQuantumAI\data\processed\news_calendar_us_full.parquet")

# ------------------------------------------------------------------ #
#  PASSO 2 — Re-process calendar
# ------------------------------------------------------------------ #

HIGH_KW = [
    "nonfarm payroll", "non-farm payroll", "nfp",
    "cpi", "consumer price index", "inflation rate",
    "ppi", "producer price index",
    "fomc", "federal reserve", "fed interest rate",
    "fed chair powell", "powell speaks",
    "core pce",
    "gdp", "gross domestic product",
    "unemployment rate",
]
MEDIUM_KW = [
    "ism manufacturing", "ism services", "ism non-manufacturing",
    "retail sales",
    "jolts",
    "cb consumer confidence", "consumer confidence",
    "philadelphia fed", "philly fed",
    "average hourly earnings",
    "durable goods",
    "trump speaks",
    "pmi", "purchasing managers",
]
LOW_KW = [
    "initial jobless claims", "continuing claims",
    "crude oil inventories",
    "adp employment",
    "housing starts", "building permits",
    "existing home sales", "new home sales",
]

EVENT_TYPE_KW = {
    "FOMC": ["fomc", "federal reserve", "fed interest rate", "interest rate decision", "fed funds"],
    "NFP":  ["nonfarm payroll", "non-farm payroll", "nfp", "employment change"],
    "CPI":  ["cpi", "consumer price index", "inflation rate"],
    "GDP":  ["gdp", "gross domestic product"],
    "PPI":  ["ppi", "producer price index"],
    "FED_SPEECH": ["fed chair", "powell", "fed speak", "fomc member",
                   "fed governor", "waller", "jefferson", "williams"],
    "UNEMPLOYMENT": ["unemployment", "jobless claims", "initial claims", "continuing claims"],
    "ISM": ["ism manufacturing", "ism services", "pmi", "purchasing managers"],
    "RETAIL_SALES": ["retail sales"],
}


def classify_impact(event_name: str) -> str:
    n = event_name.lower()
    for kw in HIGH_KW:
        if kw in n:
            return "HIGH"
    for kw in MEDIUM_KW:
        if kw in n:
            return "MEDIUM"
    for kw in LOW_KW:
        if kw in n:
            return "LOW"
    return "LOW"


def classify_event_type(event_name: str) -> str:
    n = event_name.lower()
    for etype, kws in EVENT_TYPE_KW.items():
        for kw in kws:
            if kw in n:
                return etype
    return "OTHER"


def parse_date_header(s: str):
    """Match things like 'Tuesday, 1 July 2025' or 'Friday, April 17, 2026'."""
    s = str(s).strip()
    for fmt in ("%A, %d %B %Y", "%A, %B %d, %Y"):
        try:
            return pd.Timestamp(pd.to_datetime(s, format=fmt).date())
        except (ValueError, TypeError):
            continue
    return pd.NaT


def parse_time(s: str):
    s = str(s).strip()
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", s):
        try:
            return pd.to_datetime(s, format="%H:%M:%S").time() if s.count(":") == 2 else pd.to_datetime(s, format="%H:%M").time()
        except Exception:
            return None
    return None


def process_calendar():
    df = pd.read_excel(CAL_XLSX)
    rows = []
    current_date = None
    for _, row in df.iterrows():
        t_cell = str(row["Time"])
        d = parse_date_header(t_cell)
        if pd.notna(d):
            current_date = d
            continue
        t = parse_time(t_cell)
        if t is None or current_date is None:
            continue
        cur = str(row.get("Cur.", "")).strip()
        if cur != "US":
            continue
        event = str(row.get("Event", "")).strip()
        if not event or event.lower() == "nan":
            continue
        ts = pd.Timestamp.combine(current_date.date(), t).tz_localize("UTC")
        rows.append({
            "ts_utc": ts,
            "event": event,
            "importance": classify_impact(event),
            "event_type": classify_event_type(event),
            "actual": row.get("Actual"),
            "forecast": row.get("Forecast"),
            "previous": row.get("Previous"),
        })
    out = pd.DataFrame(rows).sort_values("ts_utc").reset_index(drop=True)
    for c in ("actual", "forecast", "previous"):
        out[c] = out[c].astype(str)
    out.to_parquet(OUT_PARQUET, index=False)
    return out


# ------------------------------------------------------------------ #
#  PASSO 3 — Bucket extraction
# ------------------------------------------------------------------ #

# Buckets: (name, lo_min, hi_min) — relative minutes to event
BUCKETS = [
    ("PRE_60",      -60, -30),    # baseline "quiet" pre
    ("PRE_30",      -30, -5),     # old-blocked, FASE II marginal
    ("PRE_5",       -5, 0),       # new pause_before=5
    ("DURING",      0, 1),        # peak
    ("EARLY_POST",  1, 3),        # new pause_after=3
    ("POST_5_30",   3, 30),       # old-blocked, compression
    ("POST_30_60",  30, 60),      # full compression
]


def compute_bucket_stats(calendar: pd.DataFrame, price_df: pd.DataFrame):
    """For each event × bucket, compute mean |return| and derived stats.
    Uses M1 close-to-close |return| in bps.
    """
    # Pre-compute 1min returns in bps
    close = price_df["close"]
    ret_bps = (close.pct_change().abs() * 1e4).rename("ret_bps")

    records = []
    for _, ev in calendar.iterrows():
        t0 = ev["ts_utc"]
        imp = ev["importance"]
        etype = ev["event_type"]
        for bname, lo, hi in BUCKETS:
            lo_ts = t0 + pd.Timedelta(minutes=lo)
            hi_ts = t0 + pd.Timedelta(minutes=hi)
            # Inclusive lo, exclusive hi
            window = ret_bps.loc[(ret_bps.index >= lo_ts) & (ret_bps.index < hi_ts)]
            window = window.dropna()
            if len(window) == 0:
                continue
            records.append({
                "event_ts": t0,
                "event": ev["event"],
                "importance": imp,
                "event_type": etype,
                "bucket": bname,
                "n": len(window),
                "mean_ret_bps": float(window.mean()),
                "p90": float(window.quantile(0.90)),
                "p99": float(window.quantile(0.99)),
            })
    return pd.DataFrame(records)


def aggregate_by_bucket_importance(per_event: pd.DataFrame):
    """Mean stats per (bucket, importance), with ratio_vs_baseline, Cohen's d, p-value."""
    # Gather raw minute samples per (bucket, importance) to run stat tests
    # We need to re-extract using calendar+price (not from per_event aggregates)
    return per_event.groupby(["importance", "bucket"]).agg(
        n_events=("n", "count"),
        total_minutes=("n", "sum"),
        mean_ret_bps=("mean_ret_bps", "mean"),
        p90_avg=("p90", "mean"),
        p99_avg=("p99", "mean"),
    ).reset_index()


def compute_minute_samples(calendar: pd.DataFrame, price_df: pd.DataFrame):
    """Return dict: {(importance, bucket): np.array of all minute |ret_bps|}"""
    close = price_df["close"]
    ret_bps = (close.pct_change().abs() * 1e4).rename("ret_bps")

    samples = {}
    for _, ev in calendar.iterrows():
        t0 = ev["ts_utc"]
        imp = ev["importance"]
        for bname, lo, hi in BUCKETS:
            lo_ts = t0 + pd.Timedelta(minutes=lo)
            hi_ts = t0 + pd.Timedelta(minutes=hi)
            w = ret_bps.loc[(ret_bps.index >= lo_ts) & (ret_bps.index < hi_ts)].dropna()
            if len(w) == 0:
                continue
            key = (imp, bname)
            if key not in samples:
                samples[key] = []
            samples[key].extend(w.tolist())
    return {k: np.array(v) for k, v in samples.items()}


def compute_minute_samples_by_etype(calendar: pd.DataFrame, price_df: pd.DataFrame):
    """Return dict: {(event_type, bucket): np.array}"""
    close = price_df["close"]
    ret_bps = (close.pct_change().abs() * 1e4).rename("ret_bps")

    samples = {}
    for _, ev in calendar.iterrows():
        t0 = ev["ts_utc"]
        etype = ev["event_type"]
        for bname, lo, hi in BUCKETS:
            lo_ts = t0 + pd.Timedelta(minutes=lo)
            hi_ts = t0 + pd.Timedelta(minutes=hi)
            w = ret_bps.loc[(ret_bps.index >= lo_ts) & (ret_bps.index < hi_ts)].dropna()
            if len(w) == 0:
                continue
            key = (etype, bname)
            if key not in samples:
                samples[key] = []
            samples[key].extend(w.tolist())
    return {k: np.array(v) for k, v in samples.items()}


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return np.nan
    pooled = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    if pooled == 0:
        return np.nan
    return (a.mean() - b.mean()) / pooled


# ------------------------------------------------------------------ #
#  MAIN
# ------------------------------------------------------------------ #

def main():
    print("=" * 60)
    print("BACKTEST WINDOWS VALIDATION — Extended Calendar")
    print("=" * 60)

    print("\n[Passo 2] Processing calendar…")
    cal = process_calendar()
    print(f"  events: {len(cal)}")
    print(f"  date range: {cal['ts_utc'].min()} → {cal['ts_utc'].max()}")
    print(f"  importance: {cal['importance'].value_counts().to_dict()}")
    print(f"  event_type: {cal['event_type'].value_counts().to_dict()}")
    print(f"  saved: {OUT_PARQUET}")

    print("\n[Passo 3] Loading calibration dataset…")
    px = pd.read_parquet(CAL_DS, columns=["close"])
    print(f"  bars: {len(px):,}   range: {px.index.min()} → {px.index.max()}")
    # Clip to calendar date range to save memory
    lo = cal["ts_utc"].min() - pd.Timedelta(hours=2)
    hi = cal["ts_utc"].max() + pd.Timedelta(hours=2)
    px = px.loc[(px.index >= lo) & (px.index <= hi)]
    print(f"  clipped bars: {len(px):,}")

    print("\n[Passo 3] Extracting minute samples per (importance, bucket)…")
    samples_imp = compute_minute_samples(cal, px)
    for k, v in sorted(samples_imp.items()):
        print(f"    {k[0]:6s} {k[1]:10s}: n={len(v):5d}  mean={v.mean():.3f} bps")

    print("\n[Passo 3] Computing ratios + stat tests vs PRE_60 baseline…")
    rows = []
    for imp in ["HIGH", "MEDIUM", "LOW"]:
        baseline = samples_imp.get((imp, "PRE_60"))
        for bname, _, _ in BUCKETS:
            sample = samples_imp.get((imp, bname))
            if sample is None or baseline is None or len(sample) == 0 or len(baseline) == 0:
                continue
            ratio = sample.mean() / baseline.mean() if baseline.mean() > 0 else np.nan
            d = cohens_d(sample, baseline)
            t_stat, p_val = stats.ttest_ind(sample, baseline, equal_var=False)
            rows.append({
                "importance": imp,
                "bucket": bname,
                "n": len(sample),
                "mean_bps": round(sample.mean(), 4),
                "p90_bps": round(np.quantile(sample, 0.90), 4),
                "p99_bps": round(np.quantile(sample, 0.99), 4),
                "baseline_mean": round(baseline.mean(), 4),
                "ratio_vs_baseline": round(ratio, 4) if np.isfinite(ratio) else np.nan,
                "cohens_d": round(d, 4) if np.isfinite(d) else np.nan,
                "p_value": round(p_val, 6) if np.isfinite(p_val) else np.nan,
            })
    val_df = pd.DataFrame(rows)
    val_path = SPRINT_DIR / "validation_windows_summary.csv"
    val_df.to_csv(val_path, index=False)
    print(f"  saved: {val_path}")
    print()
    print(val_df.to_string(index=False))

    print("\n[Passo 5] Per-event-type drill down…")
    samples_et = compute_minute_samples_by_etype(cal, px)
    rows_et = []
    for etype in sorted({e for e, _ in samples_et.keys()}):
        baseline = samples_et.get((etype, "PRE_60"))
        if baseline is None or len(baseline) == 0:
            continue
        n_events_etype = int((cal["event_type"] == etype).sum())
        for bname, _, _ in BUCKETS:
            sample = samples_et.get((etype, bname))
            if sample is None or len(sample) == 0:
                continue
            ratio = sample.mean() / baseline.mean() if baseline.mean() > 0 else np.nan
            d = cohens_d(sample, baseline)
            rows_et.append({
                "event_type": etype,
                "n_events": n_events_etype,
                "bucket": bname,
                "n_minutes": len(sample),
                "mean_bps": round(sample.mean(), 4),
                "ratio_vs_baseline": round(ratio, 4) if np.isfinite(ratio) else np.nan,
                "cohens_d": round(d, 4) if np.isfinite(d) else np.nan,
            })
    et_df = pd.DataFrame(rows_et)
    et_path = SPRINT_DIR / "per_event_type_breakdown.csv"
    et_df.to_csv(et_path, index=False)
    print(f"  saved: {et_path}")
    print()
    print(et_df.to_string(index=False))

    print("\n[DONE]")
    return val_df, et_df, cal


if __name__ == "__main__":
    main()

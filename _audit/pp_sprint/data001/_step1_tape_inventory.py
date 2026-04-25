"""DATA-001 Step 1 — tape inventory (read-only)."""
import gzip, glob, os, pandas as pd, re, sys
from datetime import datetime

QW_DIR = r"C:\data\level2\_gc_xcec"
DB_DIR = r"C:\data\level2\_gc_xcec\GLBX-20260407-RQ5S6KR3E5"
OUT = r"C:\FluxQuantumAI\_audit\pp_sprint\data001\tape_inventory.csv"

rows = []

# Quantower trades CSV.gz
qw_files = sorted(glob.glob(os.path.join(QW_DIR, "trades_*.csv.gz")))
print(f"Quantower files: {len(qw_files)}", flush=True)
for fp in qw_files:
    fn = os.path.basename(fp)
    m = re.match(r"trades_(\d{4}-\d{2}-\d{2})\.csv\.gz", fn)
    if not m: continue
    d = m.group(1)
    sz = os.path.getsize(fp) / 1048576
    try:
        df = pd.read_csv(fp, compression="gzip", usecols=["timestamp"], low_memory=False)
        nrows = len(df)
        if nrows == 0:
            first_ts = last_ts = None; cov_h = 0
        else:
            ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dropna()
            first_ts = ts.min().isoformat() if len(ts) else None
            last_ts  = ts.max().isoformat() if len(ts) else None
            cov_h = (ts.max()-ts.min()).total_seconds()/3600 if len(ts) else 0
    except Exception as e:
        nrows = -1; first_ts=None; last_ts=None; cov_h=0
        print(f"  ERR {fn}: {e}", flush=True)
    rows.append({
        "date": d, "source": "quantower", "file_path": fp,
        "file_size_MB": round(sz, 3), "row_count": nrows,
        "first_ts_UTC": first_ts, "last_ts_UTC": last_ts,
        "coverage_hours": round(cov_h, 3),
    })

# Databento MBP-10 inventory (lite — no row counts to save time)
db_files = sorted(glob.glob(os.path.join(DB_DIR, "*.csv.zst")))
print(f"Databento files: {len(db_files)}", flush=True)
for fp in db_files:
    fn = os.path.basename(fp)
    m = re.match(r"glbx-mdp3-(\d{8})\.mbp-10\.csv\.zst", fn)
    if not m: continue
    d_raw = m.group(1)
    d = f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}"
    sz = os.path.getsize(fp) / 1048576
    rows.append({
        "date": d, "source": "databento_mbp10", "file_path": fp,
        "file_size_MB": round(sz, 3), "row_count": -1,
        "first_ts_UTC": None, "last_ts_UTC": None,
        "coverage_hours": -1,
    })

inv = pd.DataFrame(rows).sort_values(["source","date"]).reset_index(drop=True)
inv.to_csv(OUT, index=False)
print(f"\nWrote {len(inv)} rows -> {OUT}")
print(f"\nSummary by source:")
print(inv.groupby("source").agg(n=("date","count"), date_min=("date","min"), date_max=("date","max"), tot_MB=("file_size_MB","sum")).round(2))
print(f"\nQuantower head:")
print(inv[inv.source=="quantower"].head(3).to_string(index=False))
print(f"\nQuantower tail:")
print(inv[inv.source=="quantower"].tail(3).to_string(index=False))

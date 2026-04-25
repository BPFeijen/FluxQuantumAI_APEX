"""DATA-001 follow-up — visual re-check pack for 2026-03-06, 2026-04-07, 2026-04-19.
Read-only. Outputs a single MD with per-minute peak deltas to navigate Quantower precisely."""
import pandas as pd, gzip, sys
PQ = r"C:\data\processed\gc_ohlcv_l2_joined.parquet"
OUT = r"C:\FluxQuantumAI\_audit\pp_sprint\data001\visual_recheck_pack.md"
DATES = ["2026-03-06", "2026-04-07", "2026-04-19"]

df = pd.read_parquet(PQ)

lines = []
lines.append("# DATA-001 — Visual re-check pack (3 CORRUPT dates)\n")
lines.append("**Purpose:** Quantower navigation reference for Barbara to verify tape authority on 3 flagged-CORRUPT dates.\n")
lines.append("Each section gives the top per-minute deltas (tape vs parquet) so you can jump straight to the suspect minute in Quantower's chart and confirm the tape value.\n")
lines.append("**Tape source:** `C:\\data\\level2\\_gc_xcec\\trades_<date>.csv.gz` (Quantower L2)")
lines.append("**Parquet:** `C:\\data\\processed\\gc_ohlcv_l2_joined.parquet` (mtime 2026-04-24 23:07; corruption confirmed persistent in spot-check)\n")
lines.append("---\n")

for d in DATES:
    tr = rf"C:\data\level2\_gc_xcec\trades_{d}.csv.gz"
    try:
        tdf = pd.read_csv(tr, compression="gzip", usecols=["timestamp","price","size"], low_memory=False)
    except FileNotFoundError:
        lines.append(f"## {d} — TRADE FILE NOT FOUND ({tr})\n")
        continue
    tdf["ts"] = pd.to_datetime(tdf["timestamp"], utc=True, errors="coerce")
    tdf = tdf.dropna(subset=["ts"])
    tdf["minute"] = tdf["ts"].dt.floor("min")
    g = tdf.groupby("minute").agg(t_h=("price","max"), t_l=("price","min"), t_v=("size","sum"))
    pq = df.loc[d:d, ["high","low","volume"]].rename(columns={"high":"p_h","low":"p_l","volume":"p_v"})
    pq.index = pq.index.tz_convert("UTC") if pq.index.tz is not None else pq.index.tz_localize("UTC")
    j = g.join(pq, how="inner")
    j["dh"] = (j["t_h"] - j["p_h"]).round(2)
    j["dl"] = (j["t_l"] - j["p_l"]).round(2)
    j["dv"] = (j["t_v"] - j["p_v"]).round(0)
    j["abs_dh"] = j["dh"].abs()
    top = j.sort_values("abs_dh", ascending=False).head(15)

    day_dH = j["t_h"].max() - j["p_h"].max()
    day_dL = j["t_l"].min() - j["p_l"].min()
    n_minutes = len(j)
    n_corrupt = (j["abs_dh"] > 5).sum()

    lines.append(f"## {d}\n")
    lines.append(f"- **Day-level:** tape H={j['t_h'].max():.2f}  pq H={j['p_h'].max():.2f}  dH={day_dH:+.2f}pt  |  tape L={j['t_l'].min():.2f}  pq L={j['p_l'].min():.2f}  dL={day_dL:+.2f}pt")
    lines.append(f"- **Coverage:** {n_minutes} minutes joined; {n_corrupt} minutes with `|dh|>5pt`\n")
    lines.append(f"### Top 15 minutes by `|dh|`\n")
    lines.append(f"| Minute (UTC) | tape H | pq H | dh (tape−pq) | tape L | pq L | dl | tape V | pq V | dv |")
    lines.append(f"|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for ts, row in top.iterrows():
        lines.append(f"| {ts.strftime('%Y-%m-%d %H:%M')} | {row.t_h:.2f} | {row.p_h:.2f} | {row.dh:+.2f} | {row.t_l:.2f} | {row.p_l:.2f} | {row.dl:+.2f} | {int(row.t_v)} | {int(row.p_v)} | {int(row.dv):+d} |")
    lines.append("\n**Quantower navigation:** in chart for GC, jump to the timestamp shown. The tape H/L is the price you should observe (raw trade tape). The parquet H/L is what the joined parquet recorded for the same minute. If tape value is observable in Quantower at that minute → tape is authoritative, parquet is corrupt.\n")
    lines.append("---\n")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"Wrote {OUT}")
print(f"Dates processed: {DATES}")

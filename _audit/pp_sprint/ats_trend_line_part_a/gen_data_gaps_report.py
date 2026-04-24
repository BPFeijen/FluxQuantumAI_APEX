"""
STEP 1 (P0 ABSOLUTE): generate _audit/data_gaps_report.md

Scans Level2 capture files + Databento backfill from 2025-07-01 to today.
Identifies:
  - missing weekdays
  - intraday gaps >2h inside trades_*.csv.gz
  - severity classification
  - databento backfillability (date <= 2025-11-25)
"""
from __future__ import annotations

import gzip
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

LEVEL2_DIR = Path(r"C:\data\level2\_gc_xcec")
DATABENTO_DIR = LEVEL2_DIR / "GLBX-20260407-RQ5S6KR3E5"
REPORT = Path(r"C:\FluxQuantumAI\_audit\data_gaps_report.md")

WINDOW_START = datetime(2025, 7, 1, tzinfo=timezone.utc).date()
WINDOW_END = datetime(2026, 4, 24, tzinfo=timezone.utc).date()  # today
DATABENTO_LAST = datetime(2025, 11, 25).date()

# COMEX/CME GC holidays Jul 2025 - Apr 2026 (approximate — full-close days only)
HOLIDAYS = {
    "2025-07-04",  # Independence Day
    "2025-09-01",  # Labor Day
    "2025-11-27",  # Thanksgiving
    "2025-12-25",  # Christmas
    "2026-01-01",  # New Year
    "2026-01-19",  # MLK
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
}
HALF_SESSIONS = {
    "2025-07-03",  # July 3 early close
    "2025-11-28",  # Black Friday
    "2025-12-24",  # Christmas Eve
    "2025-12-26",  # Post-Christmas
    "2025-12-31",  # NYE
    "2026-01-02",  # Day after NYE
}


def list_iso_dates(pattern: re.Pattern, directory: Path) -> set[str]:
    out = set()
    if not directory.exists():
        return out
    for p in directory.iterdir():
        m = pattern.match(p.name)
        if m:
            out.add(m.group(1))
    return out


def expected_trading_days(start, end) -> list[str]:
    """Return sorted list of expected-trading-day ISO strings (Mon-Fri ex holidays)."""
    out = []
    d = start
    while d <= end:
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in HOLIDAYS:
            out.append(iso)
        d += timedelta(days=1)
    return out


def scan_trade_file_gaps(path: Path, min_gap_hours: float = 2.0) -> tuple[str | None, str | None, list[tuple[str, str, float]]]:
    """
    Walk a trades_*.csv.gz, extract timestamp column, return:
      (first_ts_iso, last_ts_iso, list of (gap_start, gap_end, hours) where gap >= min_gap_hours)
    """
    first = None
    last = None
    gaps: list[tuple[str, str, float]] = []
    prev_ts = None
    try:
        with gzip.open(path, "rt", errors="replace") as fh:
            header = fh.readline()
            cols = header.strip().split(",")
            try:
                ts_col = cols.index("timestamp")
            except ValueError:
                return None, None, []
            for line in fh:
                parts = line.split(",", ts_col + 2)
                if len(parts) <= ts_col:
                    continue
                ts_str = parts[ts_col]
                # normalize trailing Z or +00:00, strip fractional >6 digits
                ts_str = ts_str.strip()
                # Parse defensive: 'YYYY-MM-DDTHH:MM:SS.fffffffZ' or similar
                ts_str_clean = ts_str.replace("Z", "+00:00")
                # collapse >6 digit fractional (python datetime max=6 micros)
                m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?([+\-]\d{2}:\d{2})?$", ts_str_clean)
                if not m:
                    continue
                base = m.group(1)
                frac = m.group(2) or ""
                tz = m.group(3) or "+00:00"
                if frac:
                    frac = frac[:7]  # keep up to 6 decimals
                try:
                    ts = datetime.fromisoformat(base + frac + tz)
                except Exception:
                    continue
                if first is None:
                    first = ts
                if prev_ts is not None:
                    delta_h = (ts - prev_ts).total_seconds() / 3600.0
                    if delta_h >= min_gap_hours:
                        gaps.append((prev_ts.isoformat(), ts.isoformat(), delta_h))
                prev_ts = ts
                last = ts
    except Exception as e:
        return None, None, [("ERROR", str(e), 0.0)]
    return (first.isoformat() if first else None,
            last.isoformat() if last else None,
            gaps)


def classify_severity(duration_h: float, whole_day: bool) -> str:
    if whole_day and duration_h >= 24:
        return "CRITICAL"
    if duration_h >= 8:
        return "HIGH"
    if duration_h >= 4:
        return "MEDIUM"
    return "LOW"


def main():
    # Inventory
    trades_pat = re.compile(r"^trades_(\d{4}-\d{2}-\d{2})\.csv\.gz$")
    micro_pat = re.compile(r"^microstructure_(\d{4}-\d{2}-\d{2})\.csv\.gz$")
    databento_pat = re.compile(r"^glbx-mdp3-(\d{8})\.mbp-10\.csv\.zst$")

    trade_dates = list_iso_dates(trades_pat, LEVEL2_DIR)
    micro_dates = list_iso_dates(micro_pat, LEVEL2_DIR)
    db_dates_raw = list_iso_dates(databento_pat, DATABENTO_DIR)
    db_dates = set()
    for d in db_dates_raw:
        # format YYYYMMDD -> ISO
        try:
            db_dates.add(datetime.strptime(d, "%Y%m%d").date().isoformat())
        except Exception:
            pass

    expected = expected_trading_days(WINDOW_START, WINDOW_END)
    expected_set = set(expected)

    # Union coverage per date
    covered_level2 = trade_dates | micro_dates
    covered_any = covered_level2 | db_dates

    missing_dates = sorted(set(expected_set) - covered_level2)
    missing_fully = sorted(set(expected_set) - covered_any)

    # Intraday gap scan: only dates where trades file exists
    intraday_rows: list[dict] = []
    for iso in sorted(trade_dates & expected_set):
        path = LEVEL2_DIR / f"trades_{iso}.csv.gz"
        first, last, gaps = scan_trade_file_gaps(path, min_gap_hours=2.0)
        if first is None:
            continue
        # Detect edge gaps (late start or early end relative to expected 24/7 futures day)
        # First trade should be within 1h of UTC 23:00 previous day (session open) — we use simpler 23:00 UTC as session anchor
        # Actually GC trades 23h/day with 60-min break at 17-18 ET. For a full capture expect ~22-23h of data.
        try:
            first_dt = datetime.fromisoformat(first)
            last_dt = datetime.fromisoformat(last)
            in_day_hours = (last_dt - first_dt).total_seconds() / 3600.0
        except Exception:
            in_day_hours = 0.0
        late_start = first_dt.hour > 1 or first_dt.minute > 30  # started after 01:30 UTC
        early_end = last_dt.hour < 22 and last_dt.day == first_dt.day  # ended before 22:00 UTC same day
        if gaps or late_start or early_end:
            intraday_rows.append({
                "date": iso,
                "first": first,
                "last": last,
                "hours_covered": round(in_day_hours, 2),
                "late_start": late_start,
                "early_end": early_end,
                "gaps_2h": gaps,
            })

    # Build report
    lines = []
    lines.append("# Data Capture Gaps — Audit Report")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"**Window**: {WINDOW_START} → {WINDOW_END}")
    lines.append(f"**Databento backfill coverage**: up to {DATABENTO_LAST} (dates <= this are `backfillable_via_databento=YES`)")
    lines.append(f"**Sources scanned**:")
    lines.append(f"- `trades_*.csv.gz` in `C:/data/level2/_gc_xcec/` (count: {len(trade_dates)})")
    lines.append(f"- `microstructure_*.csv.gz` in same dir (count: {len(micro_dates)})")
    lines.append(f"- `glbx-mdp3-*.mbp-10.csv.zst` in `.../GLBX-20260407-RQ5S6KR3E5/` (count: {len(db_dates)})")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Expected trading days in window: **{len(expected)}**")
    lines.append(f"- Days with Level2 trades capture: **{len(trade_dates & expected_set)}**")
    lines.append(f"- Days with Databento backfill: **{len(db_dates & expected_set)}**")
    lines.append(f"- Days covered by at least one source: **{len(covered_any & expected_set)}**")
    lines.append(f"- **Days fully missing (no Level2 and no Databento): {len(missing_fully)}**")
    lines.append(f"- Days missing from Level2 but backfillable via Databento: **{len(set(missing_dates) & db_dates)}**")
    lines.append(f"- Days with intraday anomaly (late start / early end / >=2h gap): **{len(intraday_rows)}**")
    lines.append("")

    # Missing weekdays table
    lines.append("## Missing whole trading days (Level2 capture)")
    lines.append("")
    lines.append("| date | weekday | databento_backfillable | severity |")
    lines.append("|---|---|---|---|")
    for iso in missing_dates:
        weekday = datetime.fromisoformat(iso).strftime("%a")
        backfill = "YES" if iso in db_dates else ("YES (<=2025-11-25)" if iso <= DATABENTO_LAST.isoformat() else "NO")
        sev = "CRITICAL" if backfill == "NO" else "HIGH"
        lines.append(f"| {iso} | {weekday} | {backfill} | {sev} |")
    lines.append("")

    # Intraday anomaly table
    lines.append("## Intraday capture anomalies (>=2h gap, late start, or early end)")
    lines.append("")
    lines.append("| date | first_trade_UTC | last_trade_UTC | hours_covered | late_start | early_end | >=2h gaps | severity |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in intraday_rows:
        gaps_str = "; ".join(f"{g[0][11:19]}→{g[1][11:19]} ({g[2]:.1f}h)" for g in row["gaps_2h"]) if row["gaps_2h"] else ""
        total_gap_h = sum(g[2] for g in row["gaps_2h"])
        covered = row["hours_covered"]
        if covered < 2 or total_gap_h >= 12:
            sev = "CRITICAL"
        elif covered < 16 or total_gap_h >= 6:
            sev = "HIGH"
        elif covered < 20 or total_gap_h >= 3:
            sev = "MEDIUM"
        else:
            sev = "LOW"
        lines.append(f"| {row['date']} | {row['first'][11:19]} | {row['last'][11:19]} | {covered} | {row['late_start']} | {row['early_end']} | {gaps_str} | {sev} |")
    lines.append("")

    # Counts by severity
    lines.append("## Severity roll-up (intraday anomalies)")
    lines.append("")
    by_sev = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for row in intraday_rows:
        total_gap_h = sum(g[2] for g in row["gaps_2h"])
        covered = row["hours_covered"]
        if covered < 2 or total_gap_h >= 12:
            by_sev["CRITICAL"] += 1
        elif covered < 16 or total_gap_h >= 6:
            by_sev["HIGH"] += 1
        elif covered < 20 or total_gap_h >= 3:
            by_sev["MEDIUM"] += 1
        else:
            by_sev["LOW"] += 1
    for k, v in by_sev.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    # Business days gapped vs expected
    lines.append("## Business-days coverage")
    lines.append("")
    total_expected = len(expected)
    clean_days = total_expected - len(missing_dates) - len(intraday_rows)
    lines.append(f"- Clean trading days (L2 capture, no anomaly): **{clean_days} / {total_expected}** ({clean_days/total_expected*100:.1f}%)")
    lines.append(f"- Days with intraday anomaly: **{len(intraday_rows)} / {total_expected}** ({len(intraday_rows)/total_expected*100:.1f}%)")
    lines.append(f"- Days missing from L2: **{len(missing_dates)} / {total_expected}** ({len(missing_dates)/total_expected*100:.1f}%)")
    lines.append(f"- Days fully missing (no L2 and no Databento): **{len(missing_fully)} / {total_expected}** ({len(missing_fully)/total_expected*100:.1f}%)")
    lines.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {REPORT}")
    print(f"Missing whole days: {len(missing_dates)}, intraday anomalies: {len(intraday_rows)}, fully-missing (no backfill): {len(missing_fully)}")
    print(f"Clean: {clean_days}/{total_expected}")


if __name__ == "__main__":
    main()

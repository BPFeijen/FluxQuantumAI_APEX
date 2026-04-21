# CLAUDECODE PROMPT — Pre-Calibration Discovery (READ-ONLY)

**Sprint ID:** `sprint_calibration_discovery_20260420`
**Authorization:** Barbara ratified 2026-04-20
**Type:** **READ-ONLY DATA INVENTORY** — zero code, zero config, zero restarts
**Duration target:** 30-60 minutes
**Output:** 1 file (`C:\FluxQuantumAI\sprints\sprint_calibration_discovery_20260420\DATA_INVENTORY.md`) mapping what historical data exists to calibrate 18 thresholds

---

## 0. Why

Claude invented 18 thresholds today (8 live in Sprint C v2 commit `074a482`, 10 in draft Sprint E prompt). Barbara confirmed calibration must be data-driven per Golden Rule. Before writing the calibration sprint, ClaudeCode must confirm WHAT historical data actually exists in `C:\data` to calibrate against. No assumptions about paths or schemas.

This sprint produces ONE deliverable: a data inventory map. Calibration methodology comes next sprint, based on what this inventory finds.

---

## 1. Hard Limits

1. **READ-ONLY.** Zero writes to `C:\data\`, `C:\FluxQuantumAI\`, `config\`, `live\`.
2. **No service restarts.** nssm not touched.
3. **Capture PIDs untouched** (2512, 8248, 11740).
4. **No new data generation.** No backtest runs, no aggregations, no parquet rewrites. Read, profile, report.
5. **No schema assumptions.** For every parquet/CSV, read actual columns. Do NOT assume `entry_ts`, `mfe_pts`, `pnl` exist — verify.
6. **STOP and report** if any unexpected state (missing directory, permissions error, corrupted file).

---

## 2. Scope — 3 discovery layers

### Layer 1 — Directory structure of `C:\data`

```powershell
# Full tree to 3 levels deep, sizes
Get-ChildItem "C:\data" -Recurse -Depth 3 |
    Select-Object FullName, @{N="SizeMB";E={[math]::Round($_.Length/1MB,2)}}, LastWriteTime |
    Sort-Object FullName |
    Format-Table -AutoSize
```

Also:
```powershell
# Count files per subdirectory
Get-ChildItem "C:\data" -Directory -Recurse | ForEach-Object {
    $count = (Get-ChildItem $_.FullName -File -ErrorAction SilentlyContinue | Measure-Object).Count
    [PSCustomObject]@{
        Path = $_.FullName
        FileCount = $count
        TotalSizeMB = [math]::Round(((Get-ChildItem $_.FullName -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB), 2)
    }
} | Sort-Object Path | Format-Table -AutoSize
```

Output: directory tree with sizes and file counts per sub-directory.

### Layer 2 — Parquet inventory (schema + row count + date range)

For EVERY `.parquet` file found in Layer 1, run in Python:

```python
import pandas as pd
import os

parquet_paths = [...]  # populated from Layer 1 glob

for path in parquet_paths:
    print(f"\n=== {path} ===")
    try:
        df = pd.read_parquet(path)
        print(f"  rows:       {len(df)}")
        print(f"  columns:    {list(df.columns)}")
        print(f"  dtypes:     {df.dtypes.to_dict()}")
        print(f"  index:      type={type(df.index).__name__}")
        if hasattr(df.index, 'min'):
            print(f"              min={df.index.min()}  max={df.index.max()}")
        print(f"  file mtime: {pd.Timestamp(os.path.getmtime(path), unit='s')}")
        print(f"  file size:  {os.path.getsize(path) / 1024 / 1024:.2f} MB")
        # Print first/last row for context
        if len(df) > 0:
            print(f"  first row: {df.iloc[0].to_dict()}")
            print(f"  last row:  {df.iloc[-1].to_dict()}")
    except Exception as e:
        print(f"  ERROR: {e}")
```

### Layer 3 — CSV / JSONL inventory

For every `.csv`, `.json`, `.jsonl` file found:

```python
import csv
import json

# CSV
for path in csv_paths:
    print(f"\n=== {path} ===")
    with open(path, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
        # count lines without loading into memory
        line_count = 1 + sum(1 for _ in f)
    print(f"  lines:   {line_count}")
    print(f"  header:  {first_line}")
    # read 3 sample rows
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        samples = [next(reader, None) for _ in range(3)]
    print(f"  samples: {samples}")

# JSONL
for path in jsonl_paths:
    print(f"\n=== {path} ===")
    with open(path, 'r', encoding='utf-8') as f:
        first = f.readline()
        count = 1 + sum(1 for _ in f)
    print(f"  lines: {count}")
    try:
        parsed = json.loads(first)
        print(f"  first entry keys: {list(parsed.keys())}")
        print(f"  first entry sample: {parsed}")
    except Exception as e:
        print(f"  parse error: {e}")
```

---

## 3. Specific questions the inventory MUST answer

For each of the 18 invented thresholds, explicitly identify which files could be data sources. If no suitable source found, say "INSUFFICIENT_DATA".

### Group A — H4 bias calibration (Sprint C v2 thresholds)

Thresholds: `H4_CLOSE_PCT_BULL/BEAR_THRESHOLD`, `H4_CONTINUATION_WINDOW`, `H4_CONTINUATION_MIN_SAME`, `H4_CONF_*`, `H4_MAX_STALENESS_HOURS_DEFAULT`

Data needed:
- [ ] OHLCV history spanning ≥10 months to resample H4 bars → likely `C:\data\processed\gc_ohlcv_l2_joined.parquet`
- [ ] Confirm row count, date range (Databento Jul 2025 → dxFeed today?)
- [ ] Confirm columns include `open, high, low, close` (with whatever prefix)
- [ ] ATR computation possible? (`atr14` column or compute from OHLC)

### Group B — D1 bias calibration

Thresholds: `D1_CLOSE_PCT_BULL/BEAR_THRESHOLD`, `D1_CONF_*`

Data needed:
- [ ] Same OHLCV, resample to D1
- [ ] `daily_trend` labels (from where? ats_features_v4 is stale 279h — is there a live writer?)

### Group C — Defensive exit thresholds (the "estava lindo" group)

Thresholds: `DEFENSIVE_EXIT_MFE_GIVEBACK_PCT`, `DEFENSIVE_EXIT_MFE_MIN_ATR_MULT`, `DEFENSIVE_EXIT_DELTA_4H_THRESHOLD`, `DEFENSIVE_EXIT_ICEBERG_PROXIMITY_ATR`

**CRITICAL — Barbara's incident fix depends on this.**

Data needed:
- [ ] **Historical trades with MFE/MAE curves.** Search for:
  - `trades.csv`, `trades_live.csv`, or variants anywhere under `C:\data\`
  - Broker fill history (RoboForex / Hantec exports)
  - Any backtest output with trade-level MFE/MAE
- [ ] If no trades with MFE/MAE exist: can we **reconstruct** MFE/MAE from `decision_log.jsonl` + forward OHLCV lookup? Requires:
  - Entry timestamp + direction per decision
  - Forward-bar OHLCV to simulate hold and compute MFE/MAE
  - Exit criteria (even synthetic: "hold 4h" or "hit SL/TP")
- [ ] Iceberg event history — `iceberg__*.jsonl` files. How many days?
- [ ] delta_4h historical values — in `gc_ats_features_v4.parquet`? Live reconstructable?

### Group D — Partial H4 flip calibration

Threshold: `PARTIAL_H4_FLIP_ATR_MULT`

Data needed:
- [ ] OHLCV M5 or M15 resolution to reconstruct partial H4 bar state at 50%/75%/90% of 4h window
- [ ] Compare partial state to final bar outcome

### Group E — Direction cooldown calibration

Threshold: `DIRECTION_COOLDOWN_MIN`

Data needed:
- [ ] `decision_log.jsonl` with consecutive GO signals + direction + timestamp
- [ ] Ideally spanning >1 month to see cadence patterns across market regimes

---

## 4. Output: `DATA_INVENTORY.md`

Deliverable structure:

```markdown
# Data Inventory for Threshold Calibration — 2026-04-20

## 1. Directory tree
<output from Layer 1>

## 2. Parquet files
### 2.1 C:\data\processed\gc_ohlcv_l2_joined.parquet
- rows: X
- date range: ...
- columns: ...
- suitability: GROUP_A ✅, GROUP_B ✅, GROUP_D ✅
- notes: ...

(repeat for every parquet)

## 3. CSV/JSON/JSONL files
(same pattern)

## 4. Data suitability matrix (the key table for Barbara)

| Threshold group | Data requirement | Available? | Source path | Sample size | Suitability |
|---|---|---|---|---|---|
| Group A — H4 bias | OHLCV ≥10mo | YES/NO | ... | N rows | EXCELLENT / GOOD / INSUFFICIENT |
| Group B — D1 bias | OHLCV ≥10mo + daily_trend labels | ... | ... | ... | ... |
| Group C — Defensive exit | Trades with MFE/MAE | ... | ... | ... | ... |
| Group C (fallback) — Defensive exit | Reconstruct from decision_log | ... | ... | ... | ... |
| Group D — Partial H4 | OHLCV ≤M15 resolution | ... | ... | ... | ... |
| Group E — Direction cooldown | decision_log ≥1mo | ... | ... | ... | ... |

## 5. Gaps — thresholds that CANNOT be calibrated with current data
<list, with reason per threshold>

## 6. Recommendations
- What calibration is immediately feasible?
- What requires extra data acquisition?
- What can use proxy data (e.g., synthetic trade outcomes via forward-OHLCV simulation)?
```

---

## 5. Special note on `trades.csv` gap

INITIAL_ANALYSIS.md §10 flagged that `trades.csv` and `trades_live.csv` show 0 recent fills (broker execution bug). Investigate:

- Are these files empty? Or just show 0 in last 24h but have historical entries?
- Are there OLDER trade exports anywhere (backups, `sprints/` subdirectories, email exports, broker platform dumps)?
- Look specifically in `C:\data\` and `C:\FluxQuantumAI\` for anything named `trades_*`, `fills_*`, `executions_*`, `history_*`

Report exact counts, dates, and completeness. This is THE bottleneck for defensive-exit calibration — Barbara's incident fix depends on finding it.

---

## 6. Time budget

- Layer 1 (directory tree): 5 min
- Layer 2 (parquets): 15-20 min (may be many files)
- Layer 3 (CSV/JSONL): 10 min
- Suitability matrix compilation: 15 min
- Report writing: 10 min

**Target: 45-60 min total. If running >90 min, stop and report what's done.**

---

## 7. Communication

- Produce `DATA_INVENTORY.md` at target path
- Post brief summary in chat: "Found X parquets, Y CSVs, Z JSONLs. Group C (defensive exit) suitability: FEASIBLE/DEGRADED/BLOCKED because reason."
- Wait for Barbara ack before any further action
- NO calibration runs, NO threshold recommendations yet — just inventory

---

## 8. After this

Based on the inventory, Claude writes the actual calibration sprint prompt (next). That prompt will:
- Use EXACTLY the files and columns this inventory confirms exist
- Set sample floors based on actual row counts found
- Specify fallback methodology (reconstruction) only where direct data absent
- Cover all 18 invented thresholds explicitly

No more assumptions. No more inventions.

**Begin when Barbara gives green light. Stop and wait for ack after DATA_INVENTORY.md is delivered.**

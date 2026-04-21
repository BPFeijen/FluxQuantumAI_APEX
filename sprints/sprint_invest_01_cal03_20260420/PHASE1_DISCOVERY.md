# INVEST-01 Phase 1 — Discovery Report

**Date:** 2026-04-20
**Status:** PARTIAL documentation found — STOP-AND-ASK Barbara per prompt §1.4

---

## 1. Core finding (verbatim from settings.json `_cal03_finding`)

Present in ALL backup snapshots of `settings.json` (pre-deploy-fase7, pre-telegram-fix, entry_logic_fix, sprint_emergency_disable backup_pre_bloco1, and deploy-staging dirs). Verbatim:

> `"_cal03_finding": "delta_4h as bullish signal is INVERTED. High positive delta_4h = buyer exhaustion = bearish (E[fwd]=-10pts at d4h>800). trend_resumption_threshold kept null intentionally — signal direction is wrong. Use M30 bias flip as regime exit signal instead."`

Source tag (same file): `"source": "calibration_sprint_bloco1+CAL03-2026-04-10"`

---

## 2. TWO distinct CAL-03 artifacts discovered (naming conflict)

| Artifact | Topic | Where |
|---|---|---|
| **CAL-03a** | "Delta 4H Flip Duration" — N-bar persistence for `delta_flip_min_bars` (exit gate) | `calibration_sprint.py` (now deleted) output log: `CAL_03 → INSUFFICIENT_DATA` (2026-04-07 logs) |
| **CAL-03b** | delta_4h signal inverted interpretation (`delta_4h_inverted_fix`) | `_cal03_finding` comment in settings.json |

**Only CAL-03b is relevant to INVEST-01** (the inverted_fix rule fired during incident). CAL-03a is about position_monitor exit persistence — unrelated.

---

## 3. Q1–Q7 answers (prompt §2.3)

| Q | Answer | Confidence |
|---|---|---|
| **Q1 Date** | 2026-04-10 (per source tag) | HIGH |
| **Q2 Dataset** | **UNKNOWN** — not documented. Likely `calibration_dataset_full.parquet` (has `rolling_delta_4h`, 2.19M M1 rows, 2020–2026) or subset Jul–Nov 2025. | **UNKNOWN** |
| **Q3 Metric** | E[fwd] = -10pts at d4h > 800. Horizon **unspecified**. | PARTIAL |
| **Q4 Threshold derivation** | `_cal03_finding` cites d4h > 800. But deployed config has `delta_4h_exhaustion_high=3000` / `_low=-1050`. Memory `bloco1_calibration_results` says CAL-14/15 (2026-04-08) later set 3000/-1050 via Youden J + P80. **How 800→3000/-1050 transition was justified for the inverted interpretation: NOT FOUND**. | **UNKNOWN** |
| **Q5 Both sides tested?** | `_cal03_finding` cites ONLY +d4h side (d4h > 800 → buyer exhaustion). Negative side (-d4h → seller exhaustion SUPPORTS LONG) is **likely EXTRAPOLATED symmetrically** — no data-driven evidence of negative side found. | **ASYMMETRIC — EXTRAPOLATED** |
| **Q6 Regime stratification** | No regime/phase segmentation in `_cal03_finding` or related docs. Finding appears globally pooled. | **NO STRATIFICATION** |
| **Q7 Walk-forward** | No walk-forward evidence for inverted_fix specifically. (CAL-03a DID have walk-forward but is different artifact.) | **UNKNOWN** |

---

## 4. Missing artifacts

- **No dedicated sprint dir** `sprint_cal03_*` or `sprint_bloco1_calibration_20260410`
- **`calibration_sprint.py` deleted** (path `C:\FluxQuantumAI\ats_backtest\` no longer exists — only log traces in `C:\FluxQuantumAI\logs\calibration_sprint_20260407_*.log`)
- **No calibration report** for the inverted_fix finding specifically
- **No backtest CSV/parquet output** from the inverted_fix calibration

## 5. Artifacts found (partial)

- `_cal03_finding` comment (settings.json) — 1-line finding
- `source` tag `calibration_sprint_bloco1+CAL03-2026-04-10` (settings.json)
- `calibration_sprint_20260407_195015.log` — shows CAL-03a `INSUFFICIENT_DATA` (different artifact)
- `REGIME_FLIP_FORENSIC.md` (2026-04-20) — post-incident RCA, NOT original calibration
- Code comments in `ats_live_gate.py:406` (`if _d4h_cfg["inverted_fix"]`) and downstream
- `APEX_THRESHOLDS_INVENTORY_20260419.md` row 42 — descriptive only

---

## 6. Implication for Phase 2–5

Even with partial docs, Phase 2–5 methodology is still valid: we have the hypothesis (d4h > 800 → buyer exhaustion → -10pts E[fwd]) and the data (`calibration_dataset_full.parquet` with `rolling_delta_4h`). We can:

- **Reproduce the 800-threshold finding** and check if it replicates (Phase 3)
- **Test asymmetry** (Q5): is -d4h seller exhaustion thesis empirically valid? (Phase 3)
- **Regime-segment** (Q6): is the finding regime-dependent? (Phase 3)
- **Walk-forward** (Q7): is the finding stable across sub-periods? (Phase 3)
- **Counterfactual** (Phase 4) for 2026-04-20 incident: regime + forward return.

These questions can be answered regardless of whether we locate the original backtest artifact. The investigation is actually MORE useful as a reconstruction than as a re-reading of docs that document a global finding without regime/walk-forward rigour.

---

## 7. Decision point for Barbara

Per prompt §1.4: "Stop and ask Barbara if CAL-03 original documentation cannot be located — do NOT assume rationale."

**Documentation IS found (partial) but the backtest artifact (CSV/script/report that produced the finding) is NOT available.**

Options:

- **(a) Proceed to Phase 2–5** treating `_cal03_finding` as the documented hypothesis. Phase 3 will empirically validate OR refute it under regime segmentation + walk-forward, which is stronger rigour than the original finding. Mark Q2/Q4/Q7 as UNKNOWN in final report.

- **(b) Pause** until original calibration script/report retrieved (e.g., from git history, older backup, or Barbara's personal notes).

- **(c) Reclassify as VERDICT_E** immediately — "Never validated data-driven in a rigorous way; finding documented without regime segmentation or walk-forward; recommend exclusion from MVP until proper calibration."

Recommendation: **(a)**. The investigation's value is independent of retrieving old artifacts — we generate new data-driven evidence which is what Nível 2 needs. Mark unknowns transparently.

---

## 8. System state

- Writes: only this file + `sprint_invest_01_cal03_20260420/` dir (prev 9GB grep file deleted, scope respected)
- Capture PIDs 2512, 8248, 11740: ✓ intact
- Service PID 4516: ✓ intact
- Settings.json: unchanged (hash `BA0166FF...A42D52`)
- Zero service restarts

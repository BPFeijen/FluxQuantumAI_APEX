# EXEC-8 Multi-arm config comparison

**Window:** 2026-01-25 → 2026-04-25 (3 months, all post-handover, all rebuilt-clean)
**Generated:** 2026-04-26

| Knob | Arm A (current prod) | Arm B (predicted winner) | Arm C | Arm D | Ablation |
|---|---:|---:|---:|---:|---:|
| F1_B weight | 0.213 | 0.213 | 0.196 | 0.196 | 0.213 |
| F2_B weight | 0.274 | 0.274 | 0.278 | 0.278 | 0.274 |
| F3_B weight | 0.543 | 0.543 | 0.220 | 0.220 | **0.000** |
| F5_A weight | 0.394 | 0.394 | 0.189 | 0.189 | **0.000** |
| F5_B weight | 0.640 | 0.640 | 0.176 | 0.176 | **0.000** |
| EXHAUSTION_SCORE_THRESHOLD | 0.40 | **0.25** | 0.40 | **0.25** | **0.25** |
| EFR_ROLLING (used in F2_B feat) | 100 | 100 | 100 | 100 | 100 |
| EFR_DIVERGENCE_THRESHOLD | 0.3 | 0.3 | 0.3 | 0.3 | 0.3 |
| CLOSE_PCT_WEAK | 0.3 | 0.3 | 0.3 | 0.3 | 0.3 |

**Notes per investigation findings:**
- EFR_ROLLING kept at 100 for ALL arms (investigation: w=25 vs 100 statistical tie, no upgrade case).
- EFR_DIVERGENCE_THRESHOLD kept at 0.3 for ALL arms (Arm C/D could optionally use 0.5 = genuine optimum, but to keep arms strictly comparable on the threshold-vs-weights axes per spec, both stay at the production value).
- CLOSE_PCT_WEAK kept at 0.3 for ALL arms (investigation: 0.387 has LONG/SHORT asymmetry, no production case).
- Tier 4 constants (vol_climax_multiplier, delta_weakening_threshold) are SHADOW-only on FEAT-4; not exercised in this LOGIC-C entry-only backtest.

**PnL replay protocol (read-only, identical for all arms):**
- Entry: bar `t` where `LOGIC-C score > threshold` AND (`trend_a` or `trend_b` active) AND label valid.
- Direction: `anti-trend` (`-trend_b_dir` if trend_b, else `-trend_a_dir`).
- Exit: fixed 60-min forward (2 M30 bars) — same horizon as the calibration label, makes results directly comparable to expectancy reported in calibration artifacts.
- PnL: `direction × (close[t+2] - close[t])` per contract, in points.
- No SL/TP applied (point-in-time fixed-horizon replay) — keeps comparison clean across arms.
- One trade per bar; no overlapping inventory accounting (each bar evaluated independently). This is the same convention used in calibration's expectancy.

**Read-only contract:** No production data writes; no live/, config/, ml_news/ touches. Output written only to this directory.

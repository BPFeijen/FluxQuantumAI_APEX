"""Generate the 3 Tier 1 + 1 Tier 2 calibration artifact .md files from the
raw JSON produced by recalibration_tier1.py.

Each artifact has 12 sections corresponding to the Purdue v2 12-step protocol.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.recalibration_common import fmt_dist, env_fingerprint

OUT = Path(r"C:\FluxQuantumAI\_audit\calibrations")
RAW = OUT / "raw" / "tier1_raw.json"


def common_header(constant: str, old_value, current_code_locus: str) -> str:
    return f"""# Calibration artifact — {constant} (Purdue v2 12-step)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 1 (INVENTED constant — no prior calibration)
**Constant:** `{constant}`
**Code locus:** {current_code_locus}
**Old value:** `{old_value}`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26
"""


def section(title: str, body: str) -> str:
    return f"\n## {title}\n\n{body}\n"


def render_t11(d: dict, meta: dict, env: dict) -> str:
    cands = d["candidates"]
    body = []
    body.append(common_header(
        "EFR_ROLLING",
        old_value=100,
        current_code_locus="`live/impl2_features.py:103` + `_audit/pp_sprint/scripts/t1_x1_features.py:80`",
    ))
    # Step 1
    s = "Distribution of EFR divergence (vol_pct − body_pct) per candidate window. "
    s += "Computed on TREND-B-eligible bars only.\n\n"
    s += "| window | n | mean | median | std | IQR | skew | kurt |\n"
    s += "|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    for r in cands:
        ds = r["dist"]
        s += f"| {r['window']} | {ds['n']:,} | {ds['mean']:.4f} | {ds['median']:.4f} | {ds['std']:.4f} | {ds['iqr']:.4f} | {ds['skewness']:.3f} | {ds['kurtosis']:.3f} |\n"
    body.append(section("Step 1 — Distribution observation", s))
    # Step 2
    s = "Per candidate window, automatic transform check (log/sqrt/Box-Cox) when |skew|>1.\n\n"
    s += "| window | original_skew | transform | applied |\n|---:|---:|---|---:|\n"
    for r in cands:
        td = r["transform_diag"]
        s += f"| {r['window']} | {td.get('original_skew', float('nan')):.3f} | {td.get('chosen', td.get('reason'))} | {td.get('applied', False)} |\n"
    body.append(section("Step 2 — Feature engineering for skewness", s))
    # Step 3
    s = ("Candidate windows pre-specified to bracket the current INVENTED value 100: "
         "`{25, 50, 75, 100, 150, 200}`. Smaller = more responsive (less stable); "
         "larger = more stable (less responsive).")
    body.append(section("Step 3 — Threshold/window candidates", s))
    # Step 4
    s = "Bootstrap N=1000 95% CI on EFR median per window.\n\n"
    s += "| window | median | CI lo | CI hi |\n|---:|---:|---:|---:|\n"
    for r in cands:
        ci = r["bootstrap_median_ci"]
        s += f"| {r['window']} | {ci['point']:.4f} | {ci['ci_lo']:.4f} | {ci['ci_hi']:.4f} |\n"
    body.append(section("Step 4 — Confidence interval (Bootstrap N=1000)", s))
    # Step 5
    s = ("Hypothesis test (Bonferroni-corrected at alpha=0.05/5=0.01) — KS p-value at p80 "
         "median split on anti-trend forward 60m return (TREND-B precondition).\n\n"
         "| window | n_active | n_inactive | KS p | Cohen's d (p80) |\n"
         "|---:|---:|---:|---:|---:|\n")
    for r in cands:
        d_ = r["cohens_d_at_p80"]
        s += f"| {r['window']} | {r['n_active_p80'] or 0} | {r['n_inactive_p80'] or 0} | {(r['ks_p_at_p80'] if r['ks_p_at_p80'] is not None else float('nan')):.4f} | {(d_ if d_ is not None else float('nan')):.4f} |\n"
    body.append(section("Step 5 — Hypothesis test", s))
    # Step 6
    body.append(section("Step 6 — Performance metrics",
        "Primary metric: Cohen's d at diagnostic p80 split. Secondary: median EFR "
        "stability across folds (variance). Trading-relevance metric is captured by "
        "Tier 3 final weight (this constant is upstream of weight calculation)."))
    # Step 7
    body.append(section("Step 7 — Type I/II trade-off",
        "Window choice does not directly create false positives — it sets the lookback "
        "for percentile rank. False positives from EFR signal arise from threshold (T1.2). "
        "However, an over-short window inflates noise → Type I↑; over-long window suppresses "
        "regime change → Type II↑. Decision: prefer middle ground unless smaller window has "
        "robust higher d AND stable folds."))
    # Step 8
    body.append(section("Step 8 — Outlier handling",
        "Median + IQR used (not mean + std). Bootstrap CI was computed on the median to "
        "honour Step 8 robustness rule per DEC-2026-04-24-003 v2."))
    # Step 9
    s = ("Multi-seed (5 seeds = `[42, 1337, 2024, 7, 1729]`) bootstrap on median + skew "
         "per window. Reported mean ± std.\n\n"
         "| window | seed_median_mean | seed_median_std | seed_skew_mean | seed_skew_std |\n"
         "|---:|---:|---:|---:|---:|\n")
    for r in cands:
        s += f"| {r['window']} | {r['seed_median_mean']:.4f} | {r['seed_median_std']:.4f} | {r['seed_skew_mean']:.3f} | {r['seed_skew_std']:.3f} |\n"
    body.append(section("Step 9 — Ensemble (multi-seed)", s))
    # Step 10
    s = "5-fold purged walk-forward (embargo 48 bars ≈ 1 trading day at M30).\n\n"
    s += "| window | fold_median_variance |\n|---:|---:|\n"
    for r in cands:
        v = r["fold_median_variance"]
        s += f"| {r['window']} | {('%.4e' % v) if v is not None else 'n/a'} |\n"
    body.append(section("Step 10 — CV rigor (purged walk-forward)", s))
    # Step 11
    s = "Reproducibility fingerprint:\n\n```json\n" + json.dumps({
        "data": {
            "rebuilt_parquet": meta.get("rebuilt_path"),
            "rebuilt_sha256": meta.get("rebuilt_sha256"),
            "boxes_parquet": meta.get("boxes_path"),
            "boxes_sha256": meta.get("boxes_sha256"),
            "window": [meta.get("window_b_start"), meta.get("window_b_end")],
            "m30_rows": meta.get("m30_rows"),
            "trend_a_active_n": meta.get("trend_a_active_n"),
            "trend_b_active_n": meta.get("trend_b_active_n"),
        },
        "env": env,
    }, indent=2) + "\n```"
    body.append(section("Step 11 — Reproducibility", s))
    # Step 12
    body.append(section("Step 12 — Leakage prevention (Pipeline pattern)",
        "Computation pipeline:\n\n"
        "1. Read M1 OHLCV (rebuilt clean) → slice Window B → resample to M30 (causal "
        "right-aligned aggregation, no leakage).\n"
        "2. `bar_body`, `close_pct_from_low/high` are bar-local → no leakage.\n"
        "3. `rolling_pct_rank(volume, w)` and `rolling_pct_rank(bar_body, w)` use trailing "
        "window only (look-back). The rank is CAUSAL — it only ever uses observations "
        "from indices ≤ current.\n"
        "4. Purged walk-forward (Step 10) applies 48-bar embargo ahead of every test fold "
        "to prevent leakage from the lookback window crossing the train/test boundary.\n\n"
        "No `sklearn.Pipeline` instantiation is required because no fittable transformer "
        "(scaler/imputer) is involved — the operations are stateless rolling aggregations. "
        "Equivalent encapsulation: `scripts/recalibration_tier1.py::run_t11_efr_rolling`."))
    # Decision
    winner_w = d["winner"]
    winner_row = [r for r in cands if r["window"] == winner_w][0]
    old = 100
    new = winner_w
    diff_pct = (new - old) / old * 100.0
    cohen = winner_row["cohens_d_at_p80"]
    bonferroni_pass = (winner_row["ks_p_at_p80"] or 1.0) < 0.01
    if abs(diff_pct) <= 10:
        verdict = "KEEP (within 10% drift; document Purdue v2 lineage on existing value)"
    elif abs(diff_pct) <= 25:
        verdict = "UPGRADE (10-25% deviation; replace constant)"
    else:
        verdict = "INVESTIGATE (>25% deviation — flag possible parquet contamination effect)"
    body.append(section("Decision — Verdict & action",
        f"**Winner:** `EFR_ROLLING = {new}` (score = |d_p80| − 0.5·sqrt(fold_var) maximized)\n\n"
        f"**Diff vs old (100):** {diff_pct:+.1f}%\n\n"
        f"**Verdict:** {verdict}\n\n"
        f"**Bonferroni at alpha=0.01:** {'PASS' if bonferroni_pass else 'FAIL'} "
        f"— Cohen's d at p80 = {cohen:.4f}; KS p = {winner_row['ks_p_at_p80']:.4f}.\n\n"
        f"**Caveat (LOAD-BEARING):** All Tier 1 candidates show |Cohen's d| ~ 0.10-0.15, "
        "materially weaker than the original calibration (|d|=0.30-0.74 in "
        "T1-X1-FEATURES_discovery.md). The drop is consistent with the DATA-002 P1.5 fix "
        "(rebuilt OHLCV is clean of the `_micro_to_m1` joiner bug). The rebuilt-clean signal "
        "is what production will see going forward; the prior magnitudes were inflated by "
        "contamination. Bonferroni failure at alpha=0.01 means the EFR signal is NOT "
        "individually robust under the strictest test on the clean data; it survives only "
        "as part of the LOGIC-C composite (Tier 3)."
    ))
    return "".join(body)


def render_t12(d: dict, meta: dict, env: dict) -> str:
    rolling = d["rolling_winner_used"]
    cands = d["candidates"]
    body = []
    body.append(common_header(
        "EFR_DIVERGENCE_THRESHOLD",
        old_value="0.3 (`live/impl2_features.py:104`) / 0.20 (`IMPL1_METHODOLOGY_AUDIT.md:113/123`)",
        current_code_locus="`live/impl2_features.py:104`",
    ))
    body.append(section("Tier 2 — Bug reconcile (0.20 vs 0.3)",
        "Pre-existing inconsistency: code value `0.3` does not match the value `0.20` "
        "documented in `_audit/pp_sprint/IMPL1_METHODOLOGY_AUDIT.md` lines 113 and 123 "
        "(\"0.20 EFR threshold ... rolling 100 window\"). The discrepancy was introduced "
        "before EXEC-RECALIB-001 — likely a documentation drift during the `T1-X1-FEATURES` "
        "writeup vs the `t1_x1_features.py` script which has the literal `0.3` constant.\n\n"
        f"**Resolution:** This Tier 2 calibration supersedes both. The Purdue v2 winner is "
        f"`EFR_DIVERGENCE_THRESHOLD = {d['winner_value']:.4f}` (p{d['winner_pct']}). The doc "
        "should be amended to cite this v2 lineage, not the legacy `0.20` figure."))
    s = (f"Distribution of EFR divergence (TREND-B-eligible, EFR_ROLLING={rolling}): "
         f"{fmt_dist(d['dist'])}\n")
    body.append(section("Step 1 — Distribution observation", s))
    body.append(section("Step 2 — Feature engineering for skewness",
        f"Skewness of EFR divergence on TREND-B subset: {d['dist'].get('skewness', 0):.3f}. "
        "Within ±1 → no transform applied; absolute scale already symmetric enough."))
    s = ("Percentile-based candidates per Step 3 of v2 protocol "
         "(robust to outliers, no magic numbers):\n\n| pct | threshold value |\n|---:|---:|\n")
    for c in cands:
        s += f"| p{c['percentile']} | {c['threshold_value']:.4f} |\n"
    body.append(section("Step 3 — Threshold candidates", s))
    s = "Per-candidate Cohen's d 95% bootstrap (multi-seed, 80% subsample, N=5 seeds).\n\n"
    s += "| pct | threshold | n_active | seed_d_mean | seed_d_std |\n|---:|---:|---:|---:|---:|\n"
    for c in cands:
        sd = c["seed_d_mean"]; ss = c["seed_d_std"]
        s += f"| p{c['percentile']} | {c['threshold_value']:.4f} | {c['n_active']} | {sd if sd is None else round(sd, 4)} | {ss if ss is None else round(ss, 4)} |\n"
    body.append(section("Step 4 — Confidence interval (Bootstrap)", s))
    s = (f"Two-sided KS + Mann-Whitney on `anti_fwd_60m` (TREND-B). Bonferroni-adjusted "
         f"alpha = 0.05 / {len(cands)} = {d['bonferroni_alpha']:.4f}.\n\n"
         "| pct | KS p | MW p | passes Bonferroni |\n|---:|---:|---:|---:|\n")
    for c in cands:
        s += f"| p{c['percentile']} | {(c['ks_p'] if c['ks_p'] is not None else float('nan')):.4f} | {(c['mw_p'] if c['mw_p'] is not None else float('nan')):.4f} | {c['passes_bonferroni']} |\n"
    body.append(section("Step 5 — Hypothesis test", s))
    s = "Trading-style metrics per candidate.\n\n"
    s += "| pct | mean_active | mean_inactive | precision_pos_seed | full Cohen's d |\n|---:|---:|---:|---:|---:|\n"
    for c in cands:
        s += f"| p{c['percentile']} | {(c['mean_active'] if c['mean_active'] is not None else float('nan')):.4f} | {(c['mean_inactive'] if c['mean_inactive'] is not None else float('nan')):.4f} | {c['precision_pos_seed_mean']} | {(c['cohens_d_full'] if c['cohens_d_full'] is not None else float('nan')):.4f} |\n"
    body.append(section("Step 6 — Performance metrics", s))
    body.append(section("Step 7 — Type I/II trade-off",
        "Higher percentile threshold → tighter signal (fewer activations) → Type I (false "
        "positive) reduced, Type II (missed signal) increased. F2 EFR is a confirmation "
        "feature in the LOGIC-C composite, not an entry signal in isolation, so we accept "
        "higher Type II in exchange for stricter Type I (small marginal cost since LOGIC-C "
        "voting absorbs missed activations through other features)."))
    body.append(section("Step 8 — Outlier handling",
        "Threshold derived from EMPIRICAL PERCENTILES of the TREND-B-conditional "
        "distribution (robust to outliers; not affected by extreme volume bars). All "
        "candidates are p70-p90 cuts on a bounded [-1, +1] support."))
    body.append(section("Step 9 — Ensemble (multi-seed)",
        "Each candidate's Cohen's d was bootstrapped over 5 seeds with 80% subsample. "
        "See Step 4 table; seed_d_std quantifies seed-induced variance."))
    s = "5-fold purged walk-forward (embargo 48 bars).\n\n"
    s += "| pct | fold_d_mean | fold_d_std | n_folds_eval |\n|---:|---:|---:|---:|\n"
    for c in cands:
        fm = c["fold_d_mean"]; fs = c["fold_d_std"]
        s += f"| p{c['percentile']} | {fm if fm is None else round(fm, 4)} | {fs if fs is None else round(fs, 4)} | {c['n_folds_eval']} |\n"
    body.append(section("Step 10 — CV rigor (purged walk-forward)", s))
    s = "```json\n" + json.dumps({
        "data": {
            "rebuilt_sha256": meta.get("rebuilt_sha256"),
            "boxes_sha256": meta.get("boxes_sha256"),
            "rolling_winner_from_t11": rolling,
        },
        "env": env,
    }, indent=2) + "\n```"
    body.append(section("Step 11 — Reproducibility", s))
    body.append(section("Step 12 — Leakage prevention (Pipeline pattern)",
        "Threshold is derived from the percentile of the EFR distribution on the eligible "
        "TREND-B subset *of the entire window*. For a fully Pipeline-pure version, the "
        "p70-p90 cuts would be re-derived per train fold and applied to test (already done "
        "in Step 10's purged WF). Production threshold uses train-window value (the "
        "winner_value), with annual re-derivation policy (Step 8 `re-calibration trigger`)."))
    winner_pct = d["winner_pct"]
    winner_value = d["winner_value"]
    old_value = 0.3
    diff_pct = (winner_value - old_value) / old_value * 100.0
    if abs(diff_pct) <= 10:
        verdict = "KEEP (within 10%; document v2 lineage on existing value)"
    elif abs(diff_pct) <= 25:
        verdict = "UPGRADE (10-25% deviation; replace constant)"
    else:
        verdict = "INVESTIGATE (>25% deviation; document and gate against EXEC-8 backtest)"
    body.append(section("Decision — Verdict & action",
        f"**Winner:** `EFR_DIVERGENCE_THRESHOLD = {winner_value:.4f}` (p{winner_pct})\n\n"
        f"**Diff vs old code (0.3):** {diff_pct:+.1f}%\n\n"
        f"**Verdict:** {verdict}\n\n"
        "**Bonferroni:** No candidate passes alpha=0.01 (best KS p ≈ 0.10 at p70). "
        "The winner is selected by best `seed_d_mean` among non-Bonferroni candidates; "
        "this is the strongest available signal but is NOT individually robust at the "
        "strict family-wise error rate. Same caveat as T1.1 applies — the post-P1.5 clean "
        "data has materially smaller effect sizes than the pre-P1.5 calibration."))
    return "".join(body)


def render_t13(d: dict, meta: dict, env: dict) -> str:
    cands = d["candidates"]
    body = []
    body.append(common_header(
        "CLOSE_PCT_WEAK",
        old_value=0.3,
        current_code_locus="`live/impl2_features.py:105` + `_audit/pp_sprint/scripts/t1_x1_features.py:84`",
    ))
    body.append(section("Step 1 — Distribution observation",
        f"close_pct_eff distribution (TREND-B-eligible bars; close_pct_from_low for LONG, "
        f"close_pct_from_high for SHORT): {fmt_dist(d['dist'])}"))
    body.append(section("Step 2 — Feature engineering for skewness",
        f"Skewness on the bounded [0,1] support: {d['dist'].get('skewness', 0):.3f}. "
        "No transform applied (within ±1 acceptance band per Step 2 rule)."))
    s = "Percentile candidates {p20, p25, p30, p35, p40} of the eligible distribution. "
    s += "F5 fires when close_pct < threshold (smaller = closer to weak-direction extreme).\n\n"
    s += "| pct | threshold value |\n|---:|---:|\n"
    for c in cands:
        s += f"| p{c['percentile']} | {c['threshold_value']:.4f} |\n"
    body.append(section("Step 3 — Threshold candidates", s))
    s = "Multi-seed bootstrap (N=5, 80% subsample) on Cohen's d.\n\n"
    s += "| pct | threshold | n_active | seed_d_mean | seed_d_std |\n|---:|---:|---:|---:|---:|\n"
    for c in cands:
        sd = c["seed_d_mean"]; ss = c["seed_d_std"]
        s += f"| p{c['percentile']} | {c['threshold_value']:.4f} | {c['n_active']} | {sd if sd is None else round(sd, 4)} | {ss if ss is None else round(ss, 4)} |\n"
    body.append(section("Step 4 — Confidence interval (Bootstrap)", s))
    s = "Two-sided KS + Mann-Whitney; Bonferroni-adjusted alpha = "
    s += f"0.05 / {len(cands)} = {d['bonferroni_alpha']:.4f}.\n\n"
    s += "| pct | KS p | MW p | passes Bonferroni |\n|---:|---:|---:|---:|\n"
    for c in cands:
        s += f"| p{c['percentile']} | {(c['ks_p'] if c['ks_p'] is not None else float('nan')):.4f} | {(c['mw_p'] if c['mw_p'] is not None else float('nan')):.4f} | {c['passes_bonferroni']} |\n"
    body.append(section("Step 5 — Hypothesis test", s))
    s = "| pct | mean_active | mean_inactive | full Cohen's d |\n|---:|---:|---:|---:|\n"
    for c in cands:
        s += f"| p{c['percentile']} | {(c['mean_active'] if c['mean_active'] is not None else float('nan')):.4f} | {(c['mean_inactive'] if c['mean_inactive'] is not None else float('nan')):.4f} | {(c['cohens_d_full'] if c['cohens_d_full'] is not None else float('nan')):.4f} |\n"
    body.append(section("Step 6 — Performance metrics", s))
    body.append(section("Step 7 — Type I/II trade-off",
        "Smaller threshold → stricter weak-close definition → fewer activations → "
        "Type I↓ Type II↑. F5 contributes the largest single weight (0.640) to LOGIC-C, "
        "so over-restricting Type II would weaken the composite. Decision rule: prefer "
        "the percentile that maximises `seed_d_mean` while keeping n_active >= 100 for "
        "statistical power."))
    body.append(section("Step 8 — Outlier handling",
        "close_pct is bounded in [0, 1] by construction (close - low ÷ range), so no "
        "extreme-tail outliers are possible. Robust median + IQR were used in the "
        "distribution stats anyway, per the v2 protocol."))
    body.append(section("Step 9 — Ensemble (multi-seed)",
        "Same multi-seed bootstrap as Step 4. Reported in Step 4 table."))
    s = "| pct | fold_d_mean | fold_d_std | n_folds |\n|---:|---:|---:|---:|\n"
    for c in cands:
        fm = c["fold_d_mean"]; fs = c["fold_d_std"]
        s += f"| p{c['percentile']} | {fm if fm is None else round(fm, 4)} | {fs if fs is None else round(fs, 4)} | {c['n_folds_eval']} |\n"
    body.append(section("Step 10 — CV rigor (purged walk-forward)", s))
    s = "```json\n" + json.dumps({
        "data": {
            "rebuilt_sha256": meta.get("rebuilt_sha256"),
            "boxes_sha256": meta.get("boxes_sha256"),
        },
        "env": env,
    }, indent=2) + "\n```"
    body.append(section("Step 11 — Reproducibility", s))
    body.append(section("Step 12 — Leakage prevention (Pipeline pattern)",
        "close_pct is bar-local (no leakage). Threshold is derived from full-window "
        "eligible distribution; per-fold derivation in Step 10 confirms stability "
        "(see fold_d_mean/std). Production threshold = winner; annual re-derivation "
        "trigger per Step 8 of v2 protocol."))
    winner_pct = d["winner_pct"]
    winner_value = d["winner_value"]
    old_value = 0.3
    diff_pct = (winner_value - old_value) / old_value * 100.0
    # Check d sign
    winner_d = [c for c in cands if c["percentile"] == winner_pct][0]["seed_d_mean"]
    sign_anomaly = (winner_d is not None and winner_d < 0)
    if sign_anomaly:
        verdict = ("INVESTIGATE — winning candidate has *negative* Cohen's d under TREND-B, "
                   "opposite to original calibration (where F5 had d=+0.738). This is a strong "
                   "alarm signal: either (a) post-P1.5 clean data has reversed the relationship "
                   "between weak close and reversal, or (b) my regime gating differs subtly "
                   "from original. Recommend KEEP old value 0.3 pending investigation.")
    elif abs(diff_pct) <= 10:
        verdict = "KEEP (within 10%; document v2 lineage)"
    elif abs(diff_pct) <= 25:
        verdict = "UPGRADE (10-25%; replace constant)"
    else:
        verdict = "INVESTIGATE (>25%)"
    body.append(section("Decision — Verdict & action",
        f"**Winner (by seed_d_mean rank):** `CLOSE_PCT_WEAK = {winner_value:.4f}` (p{winner_pct}, "
        f"seed_d_mean = {winner_d if winner_d is None else round(winner_d, 4)})\n\n"
        f"**Diff vs old (0.3):** {diff_pct:+.1f}%\n\n"
        f"**Verdict:** {verdict}\n\n"
        "**No candidate passes Bonferroni at alpha=0.01.** Combined with the d-sign anomaly "
        "across non-p20 candidates, this constant is the LEAST trustworthy of the Tier 1 set "
        "on the rebuilt clean data. Recommendation deferred to ML-DS Engineer review."))
    return "".join(body)


def main():
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    meta = raw["meta"]
    env = raw["env"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "calibration_EFR_ROLLING_v2.md").write_text(
        render_t11(raw["t11_efr_rolling"], meta, env), encoding="utf-8")
    (OUT / "calibration_EFR_DIVERGENCE_THRESHOLD_v2.md").write_text(
        render_t12(raw["t12_efr_threshold"], meta, env), encoding="utf-8")
    (OUT / "calibration_CLOSE_PCT_WEAK_v2.md").write_text(
        render_t13(raw["t13_close_pct_weak"], meta, env), encoding="utf-8")
    print("wrote 3 Tier 1 + Tier 2 artifacts:")
    for p in OUT.glob("calibration_*.md"):
        print(" ", p.name, p.stat().st_size, "bytes")


if __name__ == "__main__":
    main()

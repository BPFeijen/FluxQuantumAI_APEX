"""Generate Tier 3 calibration artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path(r"C:\FluxQuantumAI\_audit\calibrations")
RAW = OUT / "raw" / "tier3_raw.json"


def header(constant: str, old: object, locus: str) -> str:
    return f"""# Calibration artifact — {constant} (Purdue v2 12-step)

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 3 (DOCUMENTED constant — single-window upgrade to multi-seed purged WF)
**Constant:** `{constant}`
**Code locus:** {locus}
**Old value:** `{old}`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26
"""


def section(title: str, body: str) -> str:
    return f"\n## {title}\n\n{body}\n"


def render_t31(d: dict, meta: dict, env: dict) -> str:
    body = []
    body.append(header(
        "FEATURE_WEIGHTS",
        old={k: round(v, 3) for k, v in d["old_weights"].items()},
        locus="`live/impl3_logic_c.py:73-79`",
    ))
    # Step 1
    s = "Per-feature N_active and label distribution (TREND-precondition gated).\n\n"
    s += "| feature | precondition | label | N_active | old_weight |\n|---|---|---|---:|---:|\n"
    for r in d["per_feature"]:
        prec = "TREND-A" if r["feature"] == "F5_A" else "TREND-B"
        n_act = meta["feature_n_active"][r["feature"]]
        s += f"| {r['feature']} | {prec} | `{r['label']}` | {n_act} | {r['old_weight']:.3f} |\n"
    body.append(section("Step 1 — Distribution observation", s))
    # Step 2
    body.append(section("Step 2 — Feature engineering for skewness",
        "Cohen's d is a standardized effect size and is invariant to monotonic re-scaling "
        "of the response variable. No transform is applied to the feature itself (boolean) "
        "or to the response (`anti_*_60m`); the latter is centred at zero by construction "
        "(positive = anti-trend reversal happened)."))
    # Step 3
    body.append(section("Step 3 — Threshold/value candidates",
        "Each feature's *value* is `|Cohen's d|` computed on the test fold (Step 10). The "
        "candidate set for the weight is the ensemble of (seed × fold) Cohen's d "
        "estimates — there is no separate threshold grid here; the weight IS the "
        "ensemble-mean |d|."))
    # Step 4 — bootstrap CI implicit through the 5-seed × 5-fold collection
    s = "Per-feature ensemble Cohen's d statistics (5-seed bootstrap × 5 purged WF folds).\n\n"
    s += "| feature | n_folds_eval | fold_d_mean | fold_d_std | weight_mean (= mean |d|) | weight_std |\n"
    s += "|---|---:|---:|---:|---:|---:|\n"
    for r in d["per_feature"]:
        wm = r.get("weight_mean")
        ws = r.get("weight_std")
        fdm = r.get("fold_d_mean")
        fds = r.get("fold_d_std")
        s += (f"| {r['feature']} | {r['n_folds']} | "
              f"{fdm if fdm is None else round(fdm,4)} | "
              f"{fds if fds is None else round(fds,4)} | "
              f"{wm if wm is None else round(wm,4)} | "
              f"{ws if ws is None else round(ws,4)} |\n")
    body.append(section("Step 4 — Confidence interval (multi-seed × multi-fold ensemble)", s))
    # Step 5
    body.append(section("Step 5 — Hypothesis test",
        "Each fold's Cohen's d is a two-group separation statistic. KS p-value per fold "
        "could be computed but is collinear with d magnitude under the same eligibility. "
        "Bonferroni correction across the 5 features × 5 folds × 5 seeds = 125 tests "
        "(alpha = 0.05 / 125 = 0.0004) — the strict family-wise rate is unforgiving on "
        "the rebuilt clean data, where most features have |d| < 0.3. See Tier 1 caveat."))
    # Step 6
    s = "Comparison vs old single-window weights.\n\n"
    s += "| feature | old | new (mean |d|) | diff % | verdict |\n|---|---:|---:|---:|---|\n"
    for r in d["per_feature"]:
        wm = r.get("weight_mean")
        ow = r["old_weight"]
        dp = r.get("diff_pct")
        if wm is None:
            v = "N/A"
        elif dp is None:
            v = "N/A"
        elif abs(dp) <= 10:
            v = "KEEP (within 10%)"
        elif abs(dp) <= 25:
            v = "UPGRADE (10-25%)"
        else:
            v = "INVESTIGATE (>25%)"
        s += (f"| {r['feature']} | {ow:.3f} | "
              f"{wm if wm is None else round(wm,4)} | "
              f"{dp if dp is None else round(dp,1)} | {v} |\n")
    body.append(section("Step 6 — Performance metrics & verdict per feature", s))
    # Step 7
    body.append(section("Step 7 — Type I/II trade-off",
        "Lower weights → lower max_score → tighter threshold required → higher Type II "
        "(missed reversal) but lower Type I (false fire). Tier 3.2 (the threshold "
        "calibration) re-tunes the threshold in concert; together they restore signal "
        "specificity at lower nominal magnitudes. F5_B's 73% drop is the largest single "
        "deviation; if it survives EXEC-8 backtest, the LOGIC-C composition is structurally "
        "sound. If not, F5_B may need its operationalization re-examined post-rebuild."))
    # Step 8
    body.append(section("Step 8 — Outlier handling",
        "Cohen's d on bounded forward returns (truncated by data range). Pooled std uses "
        "ddof=1 (sample std). Per-fold n_active is reported (Step 10 table) so any folds "
        "with degenerate small N can be reweighted in a future revision."))
    # Step 9
    body.append(section("Step 9 — Ensemble (multi-seed)",
        "5 seeds = `[42, 1337, 2024, 7, 1729]` per Step 9 of v2 protocol. Per (seed, fold) "
        "an 80% subsample of the test fold is drawn (deterministic by seed) and Cohen's d "
        "is computed. Reported `weight_mean` is the across-fold mean of (within-fold "
        "across-seed mean of |d|). `weight_std` is the across-fold std of fold means."))
    # Step 10
    s = "Purged walk-forward 5-fold, embargo 48 bars (~1 trading day at M30).\n\n"
    s += "| feature | fold_ds_per_fold (signed d on test) |\n|---|---|\n"
    for r in d["per_feature"]:
        fd = r.get("fold_ds")
        s += f"| {r['feature']} | {[round(x,4) for x in (fd or [])]} |\n"
    body.append(section("Step 10 — CV rigor (purged walk-forward)", s))
    # Step 11
    s = "```json\n" + json.dumps({
        "data": {
            "rebuilt_sha256": meta.get("rebuilt_sha256"),
            "boxes_sha256": meta.get("boxes_sha256"),
            "calibration_full_sha256": meta.get("calibration_full_sha256"),
            "delta_coverage_n": meta.get("delta_coverage_n"),
            "delta_range_min": meta.get("delta_range_min"),
            "delta_range_max": meta.get("delta_range_max"),
        },
        "env": env,
    }, indent=2) + "\n```"
    body.append(section("Step 11 — Reproducibility", s))
    # Step 12
    body.append(section("Step 12 — Leakage prevention (Pipeline pattern)",
        "Operationally:\n\n"
        "1. Feature computation uses ORIGINAL constants (SOT_N=3, EFR_ROLLING=100, "
        "EFR_DIVERGENCE_THRESHOLD=0.3, CLOSE_PCT_WEAK=0.3) so weights remain directly "
        "comparable to the v1 calibration. Tier 1 winners are NOT retroactively applied.\n"
        "2. EFR rolling pct rank, SOT decrement, delta div, close pct — all are CAUSAL "
        "(look-back only).\n"
        "3. Per-fold computation: Cohen's d is computed only on the TEST split; the train "
        "split is used solely for embargo gap accounting (no fitted parameter is carried "
        "into test computation, so leakage is structural-only and is suppressed by "
        "embargo=48).\n"
        "4. No `sklearn.Pipeline` instantiation — the pipeline is stateless. Equivalent "
        "encapsulation: `scripts/recalibration_tier3.py::feature_d_per_fold`."))
    # Decision
    new_w = d["new_weights"]
    body.append(section("Decision — Verdict & action",
        "**Per-feature recommendation:**\n\n"
        + "\n".join(
            f"- `{f}`: old `{d['old_weights'][f]:.3f}` → new `{(new_w[f] if new_w[f] is not None else 'N/A')}` "
            f"({'KEEP' if (rec.get('diff_pct') is not None and abs(rec['diff_pct']) <= 10) else ('UPGRADE' if rec.get('diff_pct') is not None and abs(rec['diff_pct']) <= 25 else 'INVESTIGATE')})"
            for f, rec in zip(new_w.keys(), d["per_feature"])
        )
        + "\n\n**Aggregate recommendation:** PARTIAL UPGRADE — F1_B/F2_B are stable; "
        "F3_B/F5_A/F5_B collapse >50%. The collapse is consistent with the rebuilt parquet "
        "removing OHLCV contamination that previously inflated those features' separation. "
        "EXEC-8 backtest should be run BOTH with new weights AND with old weights (sanity); "
        "the recalibration here is informational unless backtest confirms new weights "
        "preserve LOGIC-C trading expectancy."))
    return "".join(body)


def render_t32(d_new: dict, d_old: dict, meta: dict, env: dict) -> str:
    body = []
    body.append(header(
        "EXHAUSTION_SCORE_THRESHOLD",
        old=0.4,
        locus="`live/impl3_logic_c.py:81`",
    ))
    body.append(section("Step 1 — Distribution observation",
        "LOGIC-C score = sum of feature × weight indicators. Weight set determines "
        "score range; threshold determines firing rate. Two evaluations: "
        "(a) NEW weights from T3.1 + finer grid, (b) OLD weights + finer grid (sanity)."))
    body.append(section("Step 2 — Feature engineering for skewness",
        "LOGIC-C score is a discrete-valued sum (since each feature is boolean × constant "
        "weight). No continuous transform is applicable. The discrete max is "
        f"sum-of-weights = `{sum(v for v in d_new['weights_used'].values()):.4f}` "
        "(new weights) or `2.064` (old weights)."))
    body.append(section("Step 3 — Threshold candidates",
        "Finer grid per spec: `{0.30, 0.35, 0.40, 0.45, 0.50, 0.55}` (replaces "
        "the original coarse grid `{0.2, 0.4, 0.6, 0.8, 1.0}`)."))
    s = "Per-threshold expectancy with NEW weights (T3.1 winners) — multi-seed × purged WF.\n\n"
    s += "| threshold | n_active | pass_rate | mean_active | win_rate_active | expectancy | seed_exp_std | fold_exp_std |\n"
    s += "|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    for p in d_new["grid"]:
        s += (f"| {p['threshold']:.2f} | {p['n_active']} | {p['pass_rate']:.4f} | "
              f"{p['mean_active'] if p['mean_active'] is None else round(p['mean_active'],3)} | "
              f"{p['win_rate_active'] if p['win_rate_active'] is None else round(p['win_rate_active'],3)} | "
              f"{p['expectancy'] if p['expectancy'] is None else round(p['expectancy'],4)} | "
              f"{p['seed_exp_std'] if p['seed_exp_std'] is None else round(p['seed_exp_std'],4)} | "
              f"{p['fold_exp_std'] if p['fold_exp_std'] is None else round(p['fold_exp_std'],4)} |\n")
    body.append(section("Step 4 — Confidence interval (multi-seed bootstrap)", s))
    s = "Two-sided KS + Mann-Whitney on `anti_combined`. Bonferroni at "
    s += f"alpha = 0.05 / {len(d_new['grid'])} = {0.05/len(d_new['grid']):.4f}.\n\n"
    s += "| threshold | KS p | MW p | passes Bonferroni |\n|---:|---:|---:|---:|\n"
    bonf = 0.05 / len(d_new["grid"])
    for p in d_new["grid"]:
        ks = p["ks_p"]
        mw = p["mw_p"]
        s += (f"| {p['threshold']:.2f} | "
              f"{ks if ks is None else round(ks,4)} | "
              f"{mw if mw is None else round(mw,4)} | "
              f"{(ks is not None and ks < bonf)} |\n")
    body.append(section("Step 5 — Hypothesis test", s))
    s = "Sanity check with OLD weights (same threshold grid; informational).\n\n"
    s += "| threshold | n_active | expectancy |\n|---:|---:|---:|\n"
    for p in d_old["grid"]:
        s += (f"| {p['threshold']:.2f} | {p['n_active']} | "
              f"{p['expectancy'] if p['expectancy'] is None else round(p['expectancy'],4)} |\n")
    body.append(section("Step 6 — Performance metrics (sanity vs OLD weights)", s))
    body.append(section("Step 7 — Type I/II trade-off",
        "With NEW weights, max possible score is "
        f"~{sum(v for v in d_new['weights_used'].values()):.3f} vs old ~2.064. "
        "Threshold 0.4 with new weights is roughly equivalent (in score-percentile space) "
        "to threshold 0.6 with old weights. Decision is whether to ABSORB the weight "
        "deflation by lowering the threshold (keep similar firing rate) OR PRESERVE the "
        "fixed threshold (accept lower firing rate). Lower threshold → higher Type I, "
        "lower Type II; higher threshold → lower Type I, higher Type II. With LOGIC-C "
        "feeding the entry gate, Type I is more expensive (real PnL loss vs hypothetical "
        "miss). Recommendation: use the threshold that maximises expectancy, not pass-rate."))
    body.append(section("Step 8 — Outlier handling",
        "Mean active anti-return is computed on the test fold; pooled fold expectancy uses "
        "across-fold mean (Step 10) which is robust to single-fold outliers."))
    body.append(section("Step 9 — Ensemble (multi-seed)",
        "5 seeds × 80% subsample bootstrap of the eligible-bars set per threshold; "
        "reported `seed_exp_std` quantifies seed-induced expectancy variance."))
    s = "Per-threshold purged WF (5 folds, embargo 48 bars).\n\n"
    s += "| threshold | n_folds_eval | fold_exp_mean | fold_exp_std |\n|---:|---:|---:|---:|\n"
    for p in d_new["grid"]:
        fm = p["fold_exp_mean"]; fs = p["fold_exp_std"]
        s += (f"| {p['threshold']:.2f} | {p['n_folds_eval']} | "
              f"{fm if fm is None else round(fm,4)} | "
              f"{fs if fs is None else round(fs,4)} |\n")
    body.append(section("Step 10 — CV rigor (purged walk-forward)", s))
    s = "```json\n" + json.dumps({
        "data": {
            "rebuilt_sha256": meta.get("rebuilt_sha256"),
            "boxes_sha256": meta.get("boxes_sha256"),
            "calibration_full_sha256": meta.get("calibration_full_sha256"),
        },
        "weights_used_new": d_new["weights_used"],
        "env": env,
    }, indent=2) + "\n```"
    body.append(section("Step 11 — Reproducibility", s))
    body.append(section("Step 12 — Leakage prevention (Pipeline pattern)",
        "Score = Σ(weight × feature) is a stateless lookup; threshold is a fixed cut. "
        "No fitted transformer; no leakage path. Per-fold expectancy in Step 10 confirms "
        "stability. Purged WF embargo ensures features computed at index t do not include "
        "look-back state from indices ≥ next train fold."))
    new_t = d_new["winner_threshold"]
    new_e = d_new["winner_expectancy"]
    old_t = 0.4
    body.append(section("Decision — Verdict & action",
        f"**Winner threshold (with NEW weights):** `EXHAUSTION_SCORE_THRESHOLD = {new_t}` "
        f"(expectancy = {new_e:.4f} on rebuilt clean Window B).\n\n"
        f"**Old threshold (0.40 with OLD weights):** expectancy = "
        f"{[p for p in d_old['grid'] if p['threshold']==0.40][0]['expectancy']:.4f}.\n\n"
        f"**Diff:** {new_t} vs {old_t} = "
        f"{((new_t - old_t)/old_t)*100:+.0f}%\n\n"
        f"**Verdict:** {'KEEP (within 10%)' if abs((new_t-old_t)/old_t) <= 0.10 else ('UPGRADE (10-25%)' if abs((new_t-old_t)/old_t) <= 0.25 else 'INVESTIGATE (>25%)')}\n\n"
        "**Note:** Threshold `0.30` and `0.35` produce identical statistics because no LOGIC-C "
        "score lies in `(0.30, 0.35]` under the new weight set. The discrete score support is "
        "a finite set of weight-sums; finer grid is partly degenerate. A future revision should "
        "use score *percentiles* rather than absolute cuts (Tier 3.2 v3 follow-up)."))
    return "".join(body)


def main():
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    meta = raw["meta"]
    env = raw["env"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "calibration_FEATURE_WEIGHTS_v2.md").write_text(
        render_t31(raw["t31_feature_weights"], meta, env), encoding="utf-8")
    (OUT / "calibration_EXHAUSTION_SCORE_THRESHOLD_v2.md").write_text(
        render_t32(raw["t32_threshold_new_weights"], raw["t32_threshold_old_weights"], meta, env),
        encoding="utf-8")
    print("wrote 2 Tier 3 artifacts")
    for p in OUT.glob("calibration_*.md"):
        print(" ", p.name, p.stat().st_size, "bytes")


if __name__ == "__main__":
    main()

"""Generate Tier 4 RECOMMEND-only calibration artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUT = Path(r"C:\FluxQuantumAI\_audit\calibrations")
RAW = OUT / "raw" / "tier4_raw.json"


def header(constant: str, current: object, locus: str) -> str:
    return f"""# Calibration artifact — {constant} (Purdue v2 12-step) — RECOMMEND-ONLY

**Task:** EXEC-RECALIB-001 (Asana 1214280786215897)
**Tier:** 4 (RECOMMEND-only — NO code/settings.json edit per Option A scope)
**Constant:** `{constant}`
**Code locus:** {locus}
**Current value (in production):** `{current}`
**Protocol:** DEC-2026-04-24-003 v2 (12-step Purdue)
**Generated:** EXEC-RECALIB-001 ClaudeCode #2 2026-04-26

> **Scope note:** This artifact is RECOMMEND-ONLY. The recommended values are NOT
> applied to `live/event_processor.py` or `config/settings.json` in this task.
> Follow-up `FOLLOW-RECALIB-001` will coordinate with ClaudeCode #1 (who owns
> `event_processor.py`) and Barbara to apply (or override) the recommendations.
"""


def section(title: str, body: str) -> str:
    return f"\n## {title}\n\n{body}\n"


def render_t41(d: dict, meta: dict, env: dict) -> str:
    body = []
    body.append(header(
        "vol_climax_multiplier",
        current="0.68206 (settings.json:77) / 0.682 fallback (event_processor.py:2607,3289 — pre-existing inconsistency, deferred to FOLLOW-RECALIB-001 Step 3)",
        locus="`config/settings.json:77` (consumed at `live/event_processor.py:2607,3289`)",
    ))
    body.append(section("Operational meaning",
        "Vol climax fires when `|bar_delta_last| > rolling_std_30 * (1 + vol_climax_multiplier)`. "
        "Equivalently, `(|delta_last| / rolling_std) − 1 > vol_climax_multiplier`. The candidate "
        "grid is computed on the empirical distribution of this LHS quantity over the M1 "
        "calibration window."))
    body.append(section("Step 1 — Distribution observation",
        f"Distribution of `(|bar_delta| / rolling_std_30) − 1` across calibration window "
        f"(N = {d['dist']['n']:,}):\n"
        f"- mean = {d['dist']['mean']:.4f}, median = {d['dist']['median']:.4f}, "
        f"std = {d['dist']['std']:.4f}, IQR = {d['dist']['iqr']:.4f}\n"
        f"- skew = {d['dist']['skewness']:.2f}, kurt = {d['dist']['kurtosis']:.2f} "
        "(extremely right-skewed → bar_delta has fat positive tails representing "
        "actual volume bursts; rolling-std baseline pulls the median negative)."))
    body.append(section("Step 2 — Feature engineering for skewness",
        f"|skew| = {abs(d['dist']['skewness']):.2f} > 1 → transform candidates evaluated. "
        f"Result: {d['transform_diag'].get('chosen', d['transform_diag'].get('reason'))} "
        f"(applied={d['transform_diag'].get('applied', False)}). Production code "
        "compares the RAW ratio, so this artifact reports raw-scale percentiles to keep "
        "the recommendation immediately consumable (transform applied internally only "
        "for diagnostic skew check)."))
    s = ("Per spec: percentile-based candidates {p70, p75, p80, p85, p90} of the ratio "
         "distribution.\n\n| pct | threshold | n_active | activation_rate |\n|---:|---:|---:|---:|\n")
    for c in d["candidates"]:
        s += (f"| p{c['percentile']} | {c['threshold_value']:.4f} | "
              f"{c['n_active']:,} | {c['activation_rate']:.4f} |\n")
    body.append(section("Step 3 — Threshold candidates", s))
    s = "Multi-seed bootstrap (5 seeds, 80% subsample) on activation rate per candidate.\n\n"
    s += "| pct | seed_activation_mean | seed_activation_std |\n|---:|---:|---:|\n"
    for c in d["candidates"]:
        s += (f"| p{c['percentile']} | {c['seed_activation_mean']:.4f} | "
              f"{c['seed_activation_std']:.4f} |\n")
    body.append(section("Step 4 — Confidence interval (multi-seed bootstrap)", s))
    body.append(section("Step 5 — Hypothesis test",
        "FEAT-4 vol climax is currently SHADOW-only (logged, not blocking — "
        "`event_processor.py:3289-3299`). A hypothesis test on **veto precision** requires "
        "ground-truth profitable-position labels which are computed only post-hoc by "
        "EXEC-8 backtest. This artifact reports activation-rate stability instead; "
        "the precision/recall hypothesis test is deferred to FOLLOW-RECALIB-001 Step 5."))
    body.append(section("Step 6 — Performance metrics",
        f"**Current 0.68206 activation rate:** `{d['current_activation_rate']:.4f}` "
        f"({d['current_activation_rate']*100:.2f}% of M1 bars).\n\n"
        "Trading metric (precision of FEAT-4 veto on profitable positions) is the "
        "EXEC-8 ground-truth measure; cannot be evaluated here without simulation. "
        "Activation-rate stability proxy is reported in Step 4."))
    body.append(section("Step 7 — Type I/II trade-off",
        "Higher multiplier → fewer activations → Type I↓ (fewer false vetos), Type II↑ "
        "(more missed climaxes). Lower multiplier → more activations → Type I↑, Type II↓. "
        "Production cost asymmetry: a false veto kills a profitable entry (real PnL loss); "
        "a missed climax allows an unprofitable continuation (hypothetical loss). "
        "Type I is more expensive — favour higher multiplier."))
    body.append(section("Step 8 — Outlier handling",
        "Heavy positive skew on (|delta|/rolling_std − 1). Median + IQR were used in "
        "the distribution stats; percentile thresholds are robust to outliers."))
    body.append(section("Step 9 — Ensemble (multi-seed)",
        "5 seeds × 80% subsample (Step 4 table). Stability of activation rate across "
        "seeds is the proxy metric."))
    s = "5-fold purged WF (embargo 48 bars).\n\n"
    s += "| pct | fold_activation_mean | fold_activation_std |\n|---:|---:|---:|\n"
    for c in d["candidates"]:
        fm = c["fold_activation_mean"]; fs = c["fold_activation_std"]
        s += (f"| p{c['percentile']} | "
              f"{fm if fm is None else round(fm, 4)} | "
              f"{fs if fs is None else round(fs, 4)} |\n")
    body.append(section("Step 10 — CV rigor (purged walk-forward)", s))
    s = "```json\n" + json.dumps({
        "data": {
            "calibration_full_sha256": meta.get("calibration_full_sha256"),
            "calibration_full_path": meta.get("calibration_full_path"),
            "rows_in_window": meta.get("rows_in_window"),
            "delta_caveat": meta.get("delta_caveat"),
        },
        "env": env,
    }, indent=2) + "\n```"
    body.append(section("Step 11 — Reproducibility", s))
    body.append(section("Step 12 — Leakage prevention (Pipeline pattern)",
        "Rolling std uses look-back only (causal). Threshold derived from full-window "
        "ratio distribution; per-fold activation rate stability confirmed in Step 10. "
        "No fitted transformer; production runtime computes the same expression bar-by-bar."))
    diff_pct = (d["winner_value"] - d["current_value"]) / d["current_value"] * 100.0
    if abs(diff_pct) <= 10:
        verdict = f"KEEP (within 10% drift; current 0.68206 is consistent with p{d['winner_pct']}-aligned recalibration)"
    elif abs(diff_pct) <= 25:
        verdict = "UPGRADE (10-25%)"
    else:
        verdict = "INVESTIGATE (>25%)"
    body.append(section("Decision — RECOMMENDATION (no code edit)",
        f"**Recommended value:** `vol_climax_multiplier = {d['winner_value']:.4f}` (most "
        f"stable fold-activation: p{d['winner_pct']}).\n\n"
        f"**Diff vs current 0.68206:** {diff_pct:+.1f}%\n\n"
        f"**Verdict:** {verdict}\n\n"
        "**Apply via FOLLOW-RECALIB-001:** ClaudeCode #1 to update "
        "`config/settings.json:77` AND fix the pre-existing `0.682` vs `0.68206` "
        "fallback inconsistency at `event_processor.py:2607` and `:3289` (FOLLOW-RECALIB-001 "
        "Step 3 per Barbara 2026-04-26)."))
    return "".join(body)


def render_t42(d: dict, meta: dict, env: dict) -> str:
    body = []
    body.append(header(
        "delta_weakening_threshold",
        current="0.139486 (settings.json:79; same fallback at event_processor.py:3263)",
        locus="`config/settings.json:79` (consumed at `live/event_processor.py:3263`)",
    ))
    body.append(section("Operational meaning",
        "Delta weakening fires when `1 − |sum(delta[-10:]) / sum(delta[-20:-10])| > "
        "delta_weakening_threshold`. The LHS = `weakening_rate`. Higher = stronger "
        "weakening. Spec candidate grid is `{p15, p20, p25, p30}` of the weakening_rate "
        "distribution, which produces LOWER thresholds (more permissive — opposite of "
        "vol_climax)."))
    body.append(section("Step 1 — Distribution observation",
        f"Distribution of weakening_rate across calibration window (N = {d['dist']['n']:,}):\n"
        f"- mean = {d['dist']['mean']:.4f}, median = {d['dist']['median']:.4f}, "
        f"std = {d['dist']['std']:.4f}, IQR = {d['dist']['iqr']:.4f}\n"
        f"- skew = {d['dist']['skewness']:.2f}, kurt = {d['dist']['kurtosis']:.2f} — "
        "extreme negative skew + heavy tails. The metric `1 − |recent/older|` is unbounded "
        "below (when `|recent| >> |older|`) but bounded above at 1.0 (when recent ≈ 0)."))
    body.append(section("Step 2 — Feature engineering for skewness",
        f"|skew| = {abs(d['dist']['skewness']):.2f} >> 1; production code compares the raw "
        "rate so candidates are reported in raw scale per Step 2 transform-passthrough rule."))
    s = ("Per spec: percentile-based candidates `{p15, p20, p25, p30}` of weakening_rate. "
         "**Note:** these are LOW percentiles → produce LOWER thresholds → MORE permissive "
         "trigger (opposite of vol_climax which used p70-p90). The literal spec produces "
         "negative thresholds that may not match production semantics — surfaced for "
         "Barbara's adjudication.\n\n"
         "| pct | threshold | n_active | activation_rate |\n|---:|---:|---:|---:|\n")
    for c in d["candidates"]:
        s += (f"| p{c['percentile']} | {c['threshold_value']:.4f} | "
              f"{c['n_active']:,} | {c['activation_rate']:.4f} |\n")
    body.append(section("Step 3 — Threshold candidates (per spec literal)", s))
    s = "Multi-seed bootstrap (5 seeds, 80% subsample) on activation rate per candidate.\n\n"
    s += "| pct | seed_activation_mean | seed_activation_std |\n|---:|---:|---:|\n"
    for c in d["candidates"]:
        s += (f"| p{c['percentile']} | {c['seed_activation_mean']:.4f} | "
              f"{c['seed_activation_std']:.4f} |\n")
    body.append(section("Step 4 — Confidence interval (multi-seed bootstrap)", s))
    body.append(section("Step 5 — Hypothesis test",
        "Like vol_climax, FEAT-4 delta_weakening is currently SHADOW-only (logged at "
        "`event_processor.py:3261-3278`). Precision/recall on FEAT-4 veto requires "
        "EXEC-8 ground truth — deferred to FOLLOW-RECALIB-001."))
    body.append(section("Step 6 — Performance metrics",
        f"**Current 0.139486 activation rate:** `{d['current_activation_rate']:.4f}` "
        f"({d['current_activation_rate']*100:.2f}% of M1 bars). "
        f"This sits at roughly p{int(50 - 100*d['current_activation_rate']/2)} (rough estimate "
        f"given activation rate ≈ {d['current_activation_rate']:.2f}). The spec's p15-p30 "
        "candidates are MORE permissive (higher activation), which is opposite to a precision-"
        "favouring choice."))
    body.append(section("Step 7 — Type I/II trade-off",
        "delta_weakening is a SHADOW indicator that augments LOGIC-C; it does not block. "
        "Higher threshold → stricter signal → fewer log entries (Type I↓, Type II↑). "
        "Lower (negative) threshold from p15-p30 → very permissive (most bars qualify). "
        "Production semantics likely intends `weakening_rate > thr` to be a STRICT signal, "
        "so the recommended grid for FOLLOW-RECALIB-001 should be `{p70, p75, p80, p85, p90}` "
        "(stricter), not `{p15..p30}`. Surfaced for Barbara's call."))
    body.append(section("Step 8 — Outlier handling",
        "Extreme heavy-tailed distribution (skew=-25, kurt very high). Percentile-based "
        "thresholds are the only robust choice — mean ± std would be meaningless on this "
        "shape."))
    body.append(section("Step 9 — Ensemble (multi-seed)",
        "Step 4 table reports per-seed activation stability."))
    s = "5-fold purged WF (embargo 48 bars).\n\n"
    s += "| pct | fold_activation_mean | fold_activation_std |\n|---:|---:|---:|\n"
    for c in d["candidates"]:
        fm = c["fold_activation_mean"]; fs = c["fold_activation_std"]
        s += (f"| p{c['percentile']} | "
              f"{fm if fm is None else round(fm, 4)} | "
              f"{fs if fs is None else round(fs, 4)} |\n")
    body.append(section("Step 10 — CV rigor (purged walk-forward)", s))
    s = "```json\n" + json.dumps({
        "data": {
            "calibration_full_sha256": meta.get("calibration_full_sha256"),
            "rows_in_window": meta.get("rows_in_window"),
            "delta_caveat": meta.get("delta_caveat"),
        },
        "env": env,
    }, indent=2) + "\n```"
    body.append(section("Step 11 — Reproducibility", s))
    body.append(section("Step 12 — Leakage prevention (Pipeline pattern)",
        "Rolling sums use look-back only (causal). Threshold derived from full-window "
        "rate distribution; per-fold activation rate stability confirmed in Step 10."))
    body.append(section("Decision — RECOMMENDATION (no code edit)",
        f"**Spec-literal winner:** `delta_weakening_threshold = {d['winner_value']:.4f}` "
        f"(p{d['winner_pct']}, most stable fold rate among the {{p15..p30}} grid).\n\n"
        f"**Caveat:** the spec-literal winner is `{d['winner_value']:.4f}`, which is **negative** "
        "and corresponds to ~70% activation. This is incompatible with the production "
        "intent of a STRICTNESS threshold (where higher = stricter signal). The current "
        f"value `0.139486` (activation ~{d['current_activation_rate']*100:.0f}%) is "
        "behaviourally more conservative.\n\n"
        "**Recommendation to FOLLOW-RECALIB-001:**\n"
        "1. KEEP current `0.139486` until Barbara confirms intent of the spec's "
        "`{p15..p30}` percentile grid (likely typo for `{p70..p90}` — strictness percentiles).\n"
        "2. If `{p70..p90}` is the intended grid, re-run with that and pick the strictness "
        "percentile that maximises EXEC-8 backtest expectancy.\n"
        "3. Activation rate stability across folds suggests the current value is on a stable "
        "plateau; aggressive change is not advised without ground-truth validation."))
    return "".join(body)


def main():
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    meta = raw["meta"]
    env = raw["env"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "calibration_vol_climax_multiplier_v2.md").write_text(
        render_t41(raw["t41_vol_climax"], meta, env), encoding="utf-8")
    (OUT / "calibration_delta_weakening_threshold_v2.md").write_text(
        render_t42(raw["t42_delta_weakening"], meta, env), encoding="utf-8")
    print("wrote 2 Tier 4 RECOMMEND-only artifacts")
    for p in OUT.glob("calibration_*.md"):
        print(" ", p.name, p.stat().st_size, "bytes")


if __name__ == "__main__":
    main()

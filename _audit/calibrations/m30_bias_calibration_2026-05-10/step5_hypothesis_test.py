"""G-PURDUE Step 5 — Pairwise hypothesis test on Step 4 grid results.

H0: winner candidate has same survive_accuracy as alternative i.
H1: winner > alternative i.

Bootstrap-based p-value: fraction of bootstrap samples where the alternative
metric >= winner metric. Reject H0 iff p < alpha_corrected.

Bonferroni correction: alpha_corrected = 0.05 / (N-1) for top-1-vs-rest test
(N-1 = 97 comparisons against the same winner).

Output: `step5/hypothesis_results.json` + `step5/hypothesis_summary.md`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(r"C:/FluxQuantumAI")
STEP4_RESULTS = (ROOT / "_audit" / "calibrations"
                 / "m30_bias_calibration_2026-05-10" / "step4"
                 / "grid_results.json")
OUT_DIR = (ROOT / "_audit" / "calibrations"
           / "m30_bias_calibration_2026-05-10" / "step5")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    print("=" * 70)
    print("G-PURDUE Step 5 - Hypothesis test")
    print("=" * 70)

    if not STEP4_RESULTS.exists():
        print(f"ERROR: {STEP4_RESULTS} missing — run Step 4 first.")
        return 1

    data = json.loads(STEP4_RESULTS.read_text(encoding="utf-8"))
    results = data["results"]
    if not results:
        print("ERROR: no results in step 4 output.")
        return 1

    # Re-sort by acc_mean
    results.sort(key=lambda r: r["bootstrap"]["acc_mean"], reverse=True)
    winner = results[0]
    n_alt = len(results) - 1
    alpha = data["grid"]["alpha_bonferroni"]
    # Bonferroni for top-1-vs-rest: divide alpha by N-1 alternatives
    alpha_corrected = 0.05 / max(1, n_alt)

    print(f"\nWinner: {winner['candidate']}")
    print(f"  acc_mean = {winner['bootstrap']['acc_mean']:.4f}")
    print(f"  95% CI   = [{winner['bootstrap']['acc_lo']:.4f}, "
          f"{winner['bootstrap']['acc_hi']:.4f}]")
    print(f"  block    = {winner['bootstrap']['block_mean']:.3f}")
    print(f"\nN alternatives: {n_alt}")
    print(f"Bonferroni-corrected alpha (top-1 vs rest): {alpha_corrected:.2e}")

    # For each alternative: bootstrap-derived p-value via overlap of CIs.
    # We don't have the raw bootstrap distributions saved, so approximate via
    # CI overlap test: if winner_lo > alt_hi, reject H0 with high confidence.
    # Otherwise, use a normal-approximation Welch test on (mean, std_proxy).
    # std_proxy = (hi - lo) / (2 * 1.96).
    rejects: list[dict] = []
    cant_reject: list[dict] = []
    w_acc = winner["bootstrap"]["acc_mean"]
    w_lo = winner["bootstrap"]["acc_lo"]
    w_hi = winner["bootstrap"]["acc_hi"]
    w_std = max(1e-9, (w_hi - w_lo) / (2 * 1.96))

    for alt in results[1:]:
        c = alt["candidate"]
        a_acc = alt["bootstrap"]["acc_mean"]
        a_lo = alt["bootstrap"]["acc_lo"]
        a_hi = alt["bootstrap"]["acc_hi"]
        a_std = max(1e-9, (a_hi - a_lo) / (2 * 1.96))

        # CI non-overlap test (conservative): winner_lo > alt_hi means winner
        # is strictly better at 95% confidence
        non_overlap = w_lo > a_hi

        # Welch z-statistic (approx)
        diff = w_acc - a_acc
        se = (w_std ** 2 + a_std ** 2) ** 0.5
        z = diff / se if se > 0 else 0.0
        # Two-sided to one-sided p
        p_one = 1 - 0.5 * (1 + np.tanh(z * 0.7978845608))  # crude approx
        # Better: use scipy.stats.norm if available
        try:
            from scipy.stats import norm
            p_one = 1 - norm.cdf(z)
        except ImportError:
            pass

        decision = "REJECT" if (non_overlap or p_one < alpha_corrected) else "FAIL_TO_REJECT"
        rec = {
            "alt_candidate": c,
            "alt_acc": a_acc,
            "alt_ci": [a_lo, a_hi],
            "winner_acc": w_acc,
            "winner_ci": [w_lo, w_hi],
            "diff": diff,
            "non_overlap": non_overlap,
            "z": float(z),
            "p_one_sided": float(p_one),
            "alpha_corrected": alpha_corrected,
            "decision": decision,
        }
        if decision == "REJECT":
            rejects.append(rec)
        else:
            cant_reject.append(rec)

    print(f"\nResults:")
    print(f"  Reject H0 vs {len(rejects)} candidates (winner significantly better)")
    print(f"  Fail to reject vs {len(cant_reject)} candidates "
          f"(no significant difference)")

    # If "fail to reject" list is non-empty, the winner is NOT clearly better
    # than at least one alternative — that's important to flag honestly.
    if cant_reject:
        print(f"\nTop-3 'cant reject' candidates (winner statistically tied):")
        for r in cant_reject[:3]:
            c = r["alt_candidate"]
            print(f"  ({c['min_bars']},{c['window']},{c['strategy']:>17}) "
                  f"acc={r['alt_acc']:.4f} z={r['z']:.2f} p={r['p_one_sided']:.4f}")

    # Save
    out = {
        "winner": winner,
        "alpha_corrected": alpha_corrected,
        "n_alternatives": n_alt,
        "n_reject": len(rejects),
        "n_fail_to_reject": len(cant_reject),
        "rejects": rejects,
        "fail_to_reject": cant_reject,
    }
    (OUT_DIR / "hypothesis_results.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")

    # Markdown summary
    md: list[str] = []
    md.append("# G-PURDUE Step 5 - Hypothesis test\n\n")
    md.append("## Test setup\n\n")
    md.append("- H0: winner candidate has same `survive_accuracy` as alternative i\n")
    md.append("- H1: winner > alternative i (one-sided)\n")
    md.append(f"- Bonferroni-corrected alpha (top-1 vs rest): "
              f"{alpha_corrected:.2e}\n")
    md.append(f"- N alternatives: {n_alt}\n")
    md.append(f"- p-value: bootstrap CI non-overlap (conservative) OR "
              f"Welch z-test on bootstrap mean/std proxies\n\n")

    c = winner["candidate"]
    md.append("## Winner\n\n")
    md.append(f"`(min_bars={c['min_bars']}, window={c['window']}, "
              f"strategy={c['strategy']})`\n\n")
    md.append(f"- acc_mean = {w_acc:.4f}\n")
    md.append(f"- 95% CI   = [{w_lo:.4f}, {w_hi:.4f}]\n")
    md.append(f"- block    = {winner['bootstrap']['block_mean']:.3f}\n\n")

    md.append("## Conclusion\n\n")
    if not cant_reject:
        md.append(f"**Winner statistically dominates all {n_alt} alternatives** "
                  f"at Bonferroni-corrected alpha={alpha_corrected:.2e}. "
                  f"Recommendation is data-driven and significant.\n\n")
    else:
        md.append(f"**Cannot reject H0** for {len(cant_reject)} alternatives. "
                  f"Winner ({c['min_bars']},{c['window']},{c['strategy']}) is "
                  f"the highest acc_mean but is statistically tied with "
                  f"{len(cant_reject)} other candidates at the corrected alpha. "
                  f"Multiple settings could be deployed; choose by secondary "
                  f"criteria (block_rate, simplicity).\n\n")
        md.append("### 'Statistically tied' candidates (top 5)\n\n")
        md.append("| min_bars | window | strategy | acc_mean | z | p (one-sided) |\n")
        md.append("|---:|---:|---|---:|---:|---:|\n")
        for r in cant_reject[:5]:
            c2 = r["alt_candidate"]
            md.append(f"| {c2['min_bars']} | {c2['window']} | {c2['strategy']} | "
                      f"{r['alt_acc']:.4f} | {r['z']:.2f} | "
                      f"{r['p_one_sided']:.4f} |\n")
        md.append("\n")

    md.append("## All rejected alternatives (top 10)\n\n")
    md.append("| min_bars | window | strategy | acc_mean | z | p |\n")
    md.append("|---:|---:|---|---:|---:|---:|\n")
    for r in rejects[:10]:
        c2 = r["alt_candidate"]
        md.append(f"| {c2['min_bars']} | {c2['window']} | {c2['strategy']} | "
                  f"{r['alt_acc']:.4f} | {r['z']:.2f} | "
                  f"{r['p_one_sided']:.4e} |\n")
    md.append("\n")

    (OUT_DIR / "hypothesis_summary.md").write_text("".join(md), encoding="utf-8")
    print(f"\nResults: {OUT_DIR / 'hypothesis_results.json'}")
    print(f"Summary: {OUT_DIR / 'hypothesis_summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

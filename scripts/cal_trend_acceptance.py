"""
CAL-PATCH1 -- Calibracao TREND_ACCEPTANCE_MIN_BARS
==================================================
Período: Jul 2025 – hoje
Candidatos: 2, 3, 4 barras M30 consecutivas fora da box

Logica:
  Para cada breakout (preço sai da box confirmada com ladder):
    1. Contar barras consecutivas fora da box
    2. Classificar outcome:
       - FAKE: preço volta para dentro da box sem gerar trend sustentado
       - VALID: preço mantém-se fora e trend continua (ou gera novo box a melhor preço)
    3. Para cada threshold (2,3,4), verificar:
       - Quantos fakes filtrados
       - Quantos validos capturados vs perdidos
       - Atraso médio (em barras) para reconhecer TREND

NAO altera producao.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# -- Data paths --
M30_BOXES   = Path(r"C:\data\processed\gc_m30_boxes.parquet")
FEATURES_V4 = Path(r"C:\data\processed\gc_ats_features_v4.parquet")

# -- Parameters --
START_DATE     = "2025-07-01"
THRESHOLDS     = [2, 3, 4]
# Minimum bars to consider a breakout "sustained" = valid trend
# If price stays outside for >= this many bars, it's a real trend move
TREND_SUSTAIN_BARS = 6  # 3 hours outside box = clearly a trend


def load_m30_boxes():
    """Load M30 boxes, filter to calibration period."""
    df = pd.read_parquet(M30_BOXES)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[df.index >= START_DATE]
    print(f"M30 boxes loaded: {len(df)} bars, {df.index.min()} to {df.index.max()}")
    return df


def load_daily_trend():
    """Load daily_jac_dir from features_v4, resample to M30."""
    feat = pd.read_parquet(FEATURES_V4, columns=["daily_jac_dir"])
    if feat.index.tz is None:
        feat.index = feat.index.tz_localize("UTC")
    feat = feat[feat.index >= START_DATE]
    # Resample to M30: forward-fill daily direction
    m30_trend = feat["daily_jac_dir"].resample("30min").ffill()
    # Map: up -> long, down -> short
    m30_trend = m30_trend.map({"up": "long", "down": "short"})
    print(f"Daily trend loaded: {m30_trend.notna().sum()} bars with direction")
    return m30_trend


def detect_box_ladder_at(df, idx, trend_direction, min_boxes=3):
    """
    Check if box ladder exists at position idx.
    Replicates _detect_box_ladder() from event_processor.py
    """
    subset = df.iloc[:idx+1]
    confirmed = subset[subset["m30_box_confirmed"] == True]
    if confirmed.empty:
        return False

    # Group by box_id, get FMV per box
    box_groups = confirmed.groupby("m30_box_id")["m30_fmv"].last()
    box_groups = box_groups.sort_index()

    if len(box_groups) < min_boxes:
        return False

    last_n = box_groups.iloc[-min_boxes:]
    fmvs = last_n.values

    if trend_direction == "long":
        return all(fmvs[i] > fmvs[i-1] for i in range(1, len(fmvs)))
    elif trend_direction == "short":
        return all(fmvs[i] < fmvs[i-1] for i in range(1, len(fmvs)))
    return False


def count_bars_outside_box(df, start_idx, box_high, box_low, max_look=20):
    """
    Count consecutive bars with close outside box, starting from start_idx forward.
    Returns total consecutive bars outside before price returns inside (or data ends).
    """
    count = 0
    for i in range(start_idx, min(start_idx + max_look, len(df))):
        close = float(df.iloc[i]["close"])
        if close <= 0:
            break
        if box_low <= close <= box_high:
            break  # Returned inside box
        count += 1
    return count


def find_breakout_events(df, daily_trend):
    """
    Find all EXPANSION->TREND candidate events:
    Moments where price exits a confirmed box with ladder alignment.

    For each event, track:
      - bars_outside: how many consecutive M30 bars closed outside before returning
      - outcome: FAKE (returned to box quickly) or VALID (sustained trend)
      - The breakout bar index
    """
    events = []

    # Align daily trend to M30 index
    trend_aligned = daily_trend.reindex(df.index, method="ffill")

    # Track state
    prev_inside = True
    current_box_id = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]

        box_high = row.get("m30_box_high")
        box_low = row.get("m30_box_low")
        close = float(row["close"])
        confirmed = bool(row.get("m30_box_confirmed", False))
        box_id = row.get("m30_box_id")

        if pd.isna(box_high) or pd.isna(box_low) or close <= 0:
            continue

        box_high = float(box_high)
        box_low = float(box_low)

        inside = box_low <= close <= box_high
        prev_close = float(prev_row["close"])
        prev_inside_check = box_low <= prev_close <= box_high if prev_close > 0 else True

        # Detect transition: was inside (or different box), now outside
        if not inside and (prev_inside_check or box_id != current_box_id):
            # Get trend direction at this bar
            ts = df.index[i]
            trend_dir = trend_aligned.get(ts, None)

            if confirmed and trend_dir in ("long", "short"):
                # Check ladder
                has_ladder = detect_box_ladder_at(df, i, trend_dir)

                if has_ladder:
                    # This is a TREND candidate -- count bars outside
                    bars_out = count_bars_outside_box(df, i, box_high, box_low, max_look=30)

                    # Classify outcome
                    if bars_out >= TREND_SUSTAIN_BARS:
                        outcome = "VALID"
                    else:
                        outcome = "FAKE"

                    # Check if price came back and then went out again (whipsaw)
                    events.append({
                        "timestamp": ts,
                        "box_id": box_id,
                        "box_high": box_high,
                        "box_low": box_low,
                        "close": close,
                        "trend_dir": trend_dir,
                        "bars_outside": bars_out,
                        "outcome": outcome,
                    })

        current_box_id = box_id
        prev_inside = inside

    return pd.DataFrame(events)


def analyze_thresholds(events_df):
    """
    For each threshold candidate, compute:
    - Fakes filtered (correctly blocked)
    - Valid trends captured
    - Valid trends missed (would have been delayed past the point of entry)
    - Average delay in bars for valid trends
    """
    total = len(events_df)
    total_valid = len(events_df[events_df["outcome"] == "VALID"])
    total_fake = len(events_df[events_df["outcome"] == "FAKE"])

    print(f"\n{'='*70}")
    print(f"CALIBRACAO TREND_ACCEPTANCE_MIN_BARS")
    print(f"Periodo: {events_df['timestamp'].min()} -- {events_df['timestamp'].max()}")
    print(f"{'='*70}")
    print(f"\nTotal breakout events (com ladder + confirmed + daily_trend): {total}")
    print(f"  VALID trends (>= {TREND_SUSTAIN_BARS} barras fora da box):  {total_valid}")
    print(f"  FAKE breakouts (voltaram para box em < {TREND_SUSTAIN_BARS} barras): {total_fake}")

    print(f"\n{'-'*70}")
    print(f"{'Threshold':>10} | {'Fakes Filtrados':>16} | {'Validos Capturados':>20} | {'Validos Perdidos':>17} | {'Atraso Medio':>13}")
    print(f"{'(barras)':>10} | {'(bloqueados)':>16} | {'(passaram)':>20} | {'(bloqueados)':>17} | {'(barras)':>13}")
    print(f"{'-'*10}-+-{'-'*16}-+-{'-'*20}-+-{'-'*17}-+-{'-'*13}")

    results = []

    for t in THRESHOLDS:
        # Fakes with bars_outside < threshold -> correctly blocked
        fakes_filtered = len(events_df[(events_df["outcome"] == "FAKE") &
                                        (events_df["bars_outside"] < t)])

        # Fakes that PASS the threshold (false positives -- not filtered)
        fakes_passed = total_fake - fakes_filtered

        # Valid trends with bars_outside >= threshold -> correctly captured
        valid_captured = len(events_df[(events_df["outcome"] == "VALID") &
                                        (events_df["bars_outside"] >= t)])

        # Valid trends with bars_outside < threshold -> incorrectly blocked
        valid_missed = total_valid - valid_captured

        # Average delay: for valid trends, the threshold IS the delay
        # (you wait t bars before declaring TREND)
        # But some valid trends have bars_outside exactly at threshold
        avg_delay = t * 30  # in minutes (each bar = 30 min)

        # Filter rate
        fake_filter_rate = (fakes_filtered / total_fake * 100) if total_fake > 0 else 0
        valid_capture_rate = (valid_captured / total_valid * 100) if total_valid > 0 else 0

        print(f"{'T=' + str(t):>10} | {fakes_filtered:>6}/{total_fake:<4} ({fake_filter_rate:5.1f}%) | "
              f"{valid_captured:>6}/{total_valid:<4} ({valid_capture_rate:5.1f}%) | "
              f"{valid_missed:>6}/{total_valid:<4}      | {t} bars ({avg_delay}m)")

        results.append({
            "threshold": t,
            "fakes_filtered": fakes_filtered,
            "fakes_passed": fakes_passed,
            "fake_filter_rate": fake_filter_rate,
            "valid_captured": valid_captured,
            "valid_missed": valid_missed,
            "valid_capture_rate": valid_capture_rate,
            "avg_delay_bars": t,
            "avg_delay_min": avg_delay,
        })

    return results


def detailed_distribution(events_df):
    """Show distribution of bars_outside for fakes and valids."""
    print(f"\n{'='*70}")
    print("DISTRIBUICAO: bars_outside_box por outcome")
    print(f"{'='*70}")

    for outcome in ["FAKE", "VALID"]:
        subset = events_df[events_df["outcome"] == outcome]
        if subset.empty:
            print(f"\n{outcome}: 0 events")
            continue

        print(f"\n{outcome} ({len(subset)} events):")
        dist = subset["bars_outside"].value_counts().sort_index()
        cumulative = 0
        for bars, count in dist.items():
            cumulative += count
            pct = count / len(subset) * 100
            cum_pct = cumulative / len(subset) * 100
            bar_chart = "#" * int(pct / 2)
            print(f"  {bars:>2} barras: {count:>4} ({pct:5.1f}%)  cum={cum_pct:5.1f}%  {bar_chart}")


def recommendation(results):
    """Data-driven recommendation."""
    print(f"\n{'='*70}")
    print("RECOMENDACAO (baseada em dados)")
    print(f"{'='*70}")

    # Score: maximize fake_filter_rate while minimizing valid_missed
    # Score = fake_filter_rate - (valid_miss_penalty * 2)
    for r in results:
        valid_miss_rate = (r["valid_missed"] / (r["valid_captured"] + r["valid_missed"]) * 100) \
            if (r["valid_captured"] + r["valid_missed"]) > 0 else 0
        r["valid_miss_rate"] = valid_miss_rate
        # Score: filter fakes is good, missing valids is 2x bad
        r["score"] = r["fake_filter_rate"] - (valid_miss_rate * 2)

    best = max(results, key=lambda x: x["score"])

    print(f"\n  Scoring: fake_filter_rate - 2 x valid_miss_rate")
    print(f"  (penaliza perder trends validos 2x mais que deixar passar fakes)\n")

    for r in results:
        marker = " <-- BEST" if r["threshold"] == best["threshold"] else ""
        print(f"  T={r['threshold']}: score={r['score']:+.1f}  "
              f"(filter={r['fake_filter_rate']:.1f}%, miss={r['valid_miss_rate']:.1f}%){marker}")

    print(f"\n  >> Recomendacao: TREND_ACCEPTANCE_MIN_BARS = {best['threshold']}")
    print(f"    Filtra {best['fake_filter_rate']:.1f}% dos fake breakouts")
    print(f"    Captura {best['valid_capture_rate']:.1f}% dos trends validos")
    print(f"    Atraso: {best['avg_delay_bars']} barras ({best['avg_delay_min']} min)")
    print(f"\n  NOTA: NAO aplicado em producao. Aguarda aprovacao.")

    return best


def main():
    print("CAL-PATCH1: Calibracao TREND_ACCEPTANCE_MIN_BARS")
    print(f"Start date: {START_DATE}")
    print(f"Sustained trend threshold: {TREND_SUSTAIN_BARS} bars")
    print(f"Candidates: {THRESHOLDS}")
    print()

    # Load data
    df = load_m30_boxes()
    daily_trend = load_daily_trend()

    # Find breakout events
    print("\nSearching for breakout events (confirmed + ladder + daily_trend)...")
    events_df = find_breakout_events(df, daily_trend)

    if events_df.empty:
        print("ERRO: Nenhum breakout event encontrado!")
        return

    print(f"Found {len(events_df)} breakout events")

    # Deduplicate: same box_id should only count once (first breakout)
    events_dedup = events_df.drop_duplicates(subset=["box_id"], keep="first")
    print(f"After dedup (1 per box_id): {len(events_dedup)} events")

    # Analyze thresholds
    results = analyze_thresholds(events_dedup)

    # Detailed distribution
    detailed_distribution(events_dedup)

    # Recommendation
    best = recommendation(results)

    # Save results
    out_path = Path(r"C:\FluxQuantumAI\data\calibration")
    out_path.mkdir(parents=True, exist_ok=True)
    events_dedup.to_parquet(out_path / "cal_patch1_breakout_events.parquet")
    pd.DataFrame(results).to_csv(out_path / "cal_patch1_results.csv", index=False)
    print(f"\nResultados guardados em {out_path}")


if __name__ == "__main__":
    main()

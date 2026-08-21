"""Forensic audit dos artefatos REAIS de produção 06-08 May 2026.

Não é simulação. Lê dados reais que o sistema produziu durante operação:
  - gc_m30_boxes.parquet -> boxes formados, liq lines, FMV, lifecycle
  - decision_log.jsonl -> decisões com context completo (daily_trend, m30_bias, cascade source)
  - position_events.jsonl -> MacroMonitor VAP timeline real
  - service_state.json -> snapshots heartbeat (se disponível)

Para cada componente metodológico, mostra que ele FUNCIONOU (ou não) em produção.
"""
from __future__ import annotations

import json
from collections import defaultdict, Counter
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\FluxQuantumAI")
OUT_DIR = ROOT / "_audit" / "backtest" / "production_forensic_may0608"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WINDOW_START = pd.Timestamp("2026-05-06", tz="UTC")
WINDOW_END = pd.Timestamp("2026-05-09", tz="UTC")  # exclusive

# ---------------- M30 BOXES ----------------
def audit_boxes():
    print("=" * 70)
    print("M30 BOXES — formed during May 06-08")
    print("=" * 70)
    df = pd.read_parquet(r"C:/data/processed/gc_m30_boxes.parquet")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[(df.index >= WINDOW_START) & (df.index < WINDOW_END)].copy()
    print(f"Total M30 bars in window: {len(df)}")

    # Per box_id summary
    box_ids = df["m30_box_id"].dropna().unique()
    print(f"Distinct M30 box IDs in window: {len(box_ids)}")
    rows = []
    for bid in sorted(box_ids):
        sub = df[df["m30_box_id"] == bid]
        sub_conf = sub[sub["m30_box_confirmed"] == True]
        first_bar = sub.index[0]
        first_conf = sub_conf.index[0] if not sub_conf.empty else None
        last_bar = sub.index[-1]
        bars_total = len(sub)
        bars_confirmed = len(sub_conf)
        last_row = sub.iloc[-1]
        bh = float(last_row.get("m30_box_high", 0))
        bl = float(last_row.get("m30_box_low", 0))
        lt = float(last_row.get("m30_liq_top", 0))
        lb = float(last_row.get("m30_liq_bot", 0))
        fmv = float(last_row.get("m30_fmv", 0))
        # Classify
        bull_ext = lt > bh
        bear_ext = lb < bl
        if bull_ext and not bear_ext: cls = "bullish"
        elif bear_ext and not bull_ext: cls = "bearish"
        else: cls = "unknown"
        # Liq excursion check
        true_liq_top_check = max(float(r.get("m30_box_high", 0)) for _, r in sub.iterrows())  # simplified
        rows.append({
            "box_id": int(bid),
            "first_bar": first_bar.strftime("%m-%d %H:%M"),
            "first_confirmed": first_conf.strftime("%m-%d %H:%M") if first_conf is not None else None,
            "last_bar": last_bar.strftime("%m-%d %H:%M"),
            "bars_total": bars_total,
            "bars_confirmed": bars_confirmed,
            "box_high": bh, "box_low": bl,
            "liq_top": lt, "liq_bot": lb,
            "fmv_computed": round((bh + bl) / 2, 2),
            "fmv_stored": fmv,
            "fmv_match": abs(((bh + bl) / 2) - fmv) < 0.5,
            "bull_ext": bull_ext, "bear_ext": bear_ext,
            "classify": cls,
            "liq_extension_top_pts": round(lt - bh, 2),
            "liq_extension_bot_pts": round(bl - lb, 2),
        })
    box_df = pd.DataFrame(rows)
    box_df.to_csv(OUT_DIR / "boxes.csv", index=False)
    print(f"  saved {OUT_DIR/'boxes.csv'}")
    print(f"\nClassification distribution: {Counter(box_df['classify'])}")
    print(f"FMV match (computed == stored): {box_df['fmv_match'].sum()}/{len(box_df)}")
    print()
    print(f"Sample 10 boxes:")
    print(box_df.head(10).to_string(index=False))
    return box_df


# ---------------- EXPANSION LINES ----------------
def compute_expansion_lines(box_high, box_low, liq_top, liq_bot, n=8):
    """Mirror live._compute_expansion_lines logic."""
    # Per ATS spec: expansion lines are projections from FMV using box height as unit
    fmv = (box_high + box_low) / 2
    height = box_high - box_low
    if height <= 0: return []
    lines = []
    # n=4 above, n=4 below FMV at intervals of 0.25 * height
    for i in range(1, n // 2 + 1):
        lines.append(round(fmv + i * height * 0.5, 2))
        lines.append(round(fmv - i * height * 0.5, 2))
    return sorted(lines)


def audit_expansion_lines(box_df):
    print("\n" + "=" * 70)
    print("EXPANSION LINES — computed for confirmed boxes")
    print("=" * 70)
    rows = []
    for _, b in box_df.iterrows():
        if b["bars_confirmed"] == 0: continue
        lines = compute_expansion_lines(b["box_high"], b["box_low"], b["liq_top"], b["liq_bot"], n=8)
        rows.append({
            "box_id": b["box_id"],
            "first_confirmed": b["first_confirmed"],
            "fmv": b["fmv_computed"],
            "expansion_lines": lines,
            "n_lines": len(lines),
            "range_pts": round(max(lines) - min(lines), 2) if lines else 0,
        })
    exp_df = pd.DataFrame(rows)
    exp_df.to_csv(OUT_DIR / "expansion_lines.csv", index=False)
    print(f"  saved {OUT_DIR/'expansion_lines.csv'}")
    print(f"  {len(exp_df)} confirmed boxes with computed expansion lines")
    if len(exp_df) > 0:
        print(f"\nSample expansion lines (first 5):")
        print(exp_df.head(5).to_string(index=False))


# ---------------- CASCADE DIRECTION RESOLUTION ----------------
def audit_cascade():
    print("\n" + "=" * 70)
    print("CASCADE DIRECTION RESOLUTION (5-layer)")
    print("=" * 70)
    # decision_log context fields show what cascade resolved
    daily_trend_dist = Counter()
    m30_bias_dist = Counter()
    m30_confirmed_dist = Counter()
    provisional_dist = Counter()
    direction_dist = Counter()
    n_decisions = 0
    layers = Counter()  # Which layer fired
    sample_per_layer = defaultdict(list)
    with Path(r"C:/FluxQuantumAI/logs/decision_log.jsonl").open(encoding="utf-8", errors="ignore") as f:
        for ln in f:
            try: d = json.loads(ln.strip())
            except: continue
            ts = d.get("timestamp")
            if not ts: continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(t) or t < WINDOW_START or t >= WINDOW_END: continue
            ctx = d.get("context", {}) or {}
            dec = d.get("decision", {}) or {}
            if dec.get("action") not in ("GO", "EXEC_FAILED", "BLOCK"): continue
            n_decisions += 1
            daily_trend_dist[ctx.get("daily_trend", "?")] += 1
            m30_bias_dist[ctx.get("m30_bias", "?")] += 1
            m30_confirmed_dist[str(ctx.get("m30_bias_confirmed", "?"))] += 1
            provisional_dist[ctx.get("provisional_m30_bias", "?")] += 1
            direction_dist[dec.get("direction", "?")] += 1
            # Determine which layer: daily_trend (Layer 1) -> m30_confirmed (Layer 3) -> provisional (Layer 4) -> unknown (Layer 5)
            dt = ctx.get("daily_trend", "")
            if dt and dt not in ("unknown", "?"):
                layer = "L1_daily_trend"
            elif ctx.get("m30_bias_confirmed") and ctx.get("m30_bias", "unknown") != "unknown":
                layer = "L3_m30_confirmed"
            elif ctx.get("provisional_m30_bias", "unknown") not in ("unknown", "?"):
                layer = "L4_provisional"
            else:
                layer = "L5_unknown"
            layers[layer] += 1
            if len(sample_per_layer[layer]) < 3:
                sample_per_layer[layer].append({
                    "ts": str(t),
                    "direction": dec.get("direction"),
                    "daily_trend": dt,
                    "m30_bias": ctx.get("m30_bias"),
                    "m30_confirmed": ctx.get("m30_bias_confirmed"),
                    "provisional": ctx.get("provisional_m30_bias"),
                    "action": dec.get("action"),
                })

    print(f"Total decisions in window: {n_decisions}")
    print(f"\nLayer activation:")
    for layer, count in layers.most_common():
        print(f"  {layer}: {count} ({100*count/n_decisions:.1f}%)")
    print(f"\nDaily trend dist: {dict(daily_trend_dist)}")
    print(f"M30 bias dist: {dict(m30_bias_dist)}")
    print(f"M30 confirmed dist: {dict(m30_confirmed_dist)}")
    print(f"Provisional dist: {dict(provisional_dist)}")
    print(f"Direction emitted: {dict(direction_dist)}")
    return {
        "n_decisions": n_decisions,
        "layers": dict(layers),
        "samples": {k: v for k, v in sample_per_layer.items()},
        "daily_trend_dist": dict(daily_trend_dist),
        "m30_bias_dist": dict(m30_bias_dist),
        "direction_dist": dict(direction_dist),
    }


# ---------------- MACRO MONITOR VAP ----------------
def audit_macro_monitor():
    print("\n" + "=" * 70)
    print("MACRO MONITOR VAP — virtual position lifecycle")
    print("=" * 70)
    events_per_vap = defaultdict(list)
    all_events = []
    with Path(r"C:/FluxQuantumAI/logs/position_events.jsonl").open(encoding="utf-8", errors="ignore") as f:
        for ln in f:
            try: e = json.loads(ln.strip())
            except: continue
            ts = e.get("timestamp") or e.get("ts")
            if not ts: continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(t) or t < WINDOW_START or t >= WINDOW_END: continue
            e["_ts"] = t
            all_events.append(e)
            vap_id = e.get("vap_id") or e.get("position_id") or "?"
            events_per_vap[vap_id].append(e)

    print(f"Total events in window: {len(all_events)}")
    by_type = Counter(e.get("event_type") or e.get("type") or "?" for e in all_events)
    print(f"By type: {dict(by_type)}")
    print(f"Distinct VAPs: {len(events_per_vap)}")

    # Per-VAP forensic timeline
    vap_rows = []
    for vap_id, evs in events_per_vap.items():
        evs_sorted = sorted(evs, key=lambda e: e["_ts"])
        created = next((e for e in evs_sorted if "CREATED" in (e.get("event_type") or "")), None)
        if not created: continue
        terminal = next((e for e in evs_sorted if any(k in (e.get("event_type") or "") for k in ("SUPERSEDED", "EXPIRED", "TP1", "SL", "REGIME_FLIP"))), None)
        vap_rows.append({
            "vap_id": vap_id,
            "created_at": created["_ts"].strftime("%m-%d %H:%M:%S"),
            "direction": created.get("direction") or created.get("vap_direction"),
            "entry": created.get("entry_price") or created.get("entry"),
            "sl": created.get("sl"),
            "tp1": created.get("tp1"),
            "tp2": created.get("tp2"),
            "exit_event": terminal.get("event_type") if terminal else None,
            "exit_at": terminal["_ts"].strftime("%m-%d %H:%M:%S") if terminal else None,
            "exit_price": terminal.get("exit_price") or terminal.get("price") if terminal else None,
            "duration_min": int((terminal["_ts"] - created["_ts"]).total_seconds() / 60) if terminal else None,
            "n_events": len(evs_sorted),
        })
    vap_df = pd.DataFrame(vap_rows)
    vap_df.to_csv(OUT_DIR / "macro_vaps.csv", index=False)
    print(f"\n  saved {OUT_DIR/'macro_vaps.csv'}")
    print(f"\nFull VAP timeline ({len(vap_df)} VAPs):")
    if len(vap_df) > 0:
        print(vap_df.to_string(index=False))
    return vap_df


# ---------------- TRIGGER TYPES ----------------
def audit_trigger_types():
    print("\n" + "=" * 70)
    print("TRIGGER TYPES — ALPHA/BETA/GAMMA/DELTA/PULLBACK/CONTINUATION distribution")
    print("=" * 70)
    by_trigger = defaultdict(lambda: defaultdict(int))
    by_action = defaultdict(int)
    with Path(r"C:/FluxQuantumAI/logs/decision_log.jsonl").open(encoding="utf-8", errors="ignore") as f:
        for ln in f:
            try: d = json.loads(ln.strip())
            except: continue
            ts = d.get("timestamp")
            if not ts: continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(t) or t < WINDOW_START or t >= WINDOW_END: continue
            dec = d.get("decision", {}) or {}
            trig_obj = d.get("trigger", {}) or {}
            tt = trig_obj.get("type", "?")  # ALPHA/BETA/GAMMA/DELTA
            mode = dec.get("entry_mode", "?")  # PULLBACK/CONTINUATION/RANGE/OVEREXTENSION
            action = dec.get("action", "?")
            date = t.strftime("%m-%d")
            by_trigger[date][f"{tt}_{action}"] += 1
            by_trigger[date][f"mode_{mode}"] += 1
            by_action[action] += 1

    print(f"Action distribution: {dict(by_action)}")
    print(f"\nPer-day trigger + mode distribution:")
    for date in sorted(by_trigger.keys()):
        print(f"  {date}: {dict(by_trigger[date])}")


# ---------------- MAIN ----------------
def main():
    print(f"Production forensic audit window: {WINDOW_START} -> {WINDOW_END}")
    print(f"Output dir: {OUT_DIR}")
    print()

    box_df = audit_boxes()
    audit_expansion_lines(box_df)
    cascade = audit_cascade()
    vap_df = audit_macro_monitor()
    audit_trigger_types()

    print("\n" + "=" * 70)
    print("FORENSIC AUDIT COMPLETE")
    print("=" * 70)
    print(f"Outputs: {OUT_DIR}")
    print(f"  - boxes.csv ({len(box_df)} boxes)")
    print(f"  - expansion_lines.csv")
    print(f"  - macro_vaps.csv ({len(vap_df)} VAPs)")


if __name__ == "__main__":
    main()

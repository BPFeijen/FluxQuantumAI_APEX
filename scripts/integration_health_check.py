#!/usr/bin/env python3
"""
End-to-End Integration Health Check.

Verifies the entire pipeline is connected and operational:
  1. Data sources (microstructure, M1, M5, M30 parquets)
  2. Level detection (M5 + M30 levels populated)
  3. Event processor state (phase, bias, daily_trend, defense)
  4. MT5 connectivity (both brokers)
  5. Telegram connectivity
  6. Gate chain simulation (dry run with current data)
  7. Service status (NSSM)

Usage: python scripts/integration_health_check.py
"""

import json
import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path("C:/FluxQuantumAI")))
sys.path.insert(0, str(Path("C:/FluxQuantumAI/live")))

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


def status_icon(s):
    return {"PASS": "[OK]", "FAIL": "[XX]", "WARN": "[!!]"}.get(s, "[??]")


def check_data_sources():
    """Check all data sources are fresh and readable."""
    results = []
    now = time.time()

    # Microstructure
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    micro = Path(f"C:/data/level2/_gc_xcec/microstructure_{today}.csv.gz")
    if micro.exists():
        age = now - micro.stat().st_mtime
        fresh = age < 600  # 10 min
        results.append((PASS if fresh else WARN,
                        f"Microstructure today: age={age:.0f}s {'(fresh)' if fresh else '(STALE)'}"))
        try:
            df = pd.read_csv(micro, usecols=["timestamp", "mid_price"], nrows=5)
            results.append((PASS, f"  Readable: {len(df)} rows sampled"))
        except Exception as e:
            results.append((FAIL, f"  Read error: {e}"))
    else:
        results.append((WARN, f"Microstructure today not found: {micro.name}"))

    # M1 historical
    m1 = Path("C:/data/processed/gc_ohlcv_l2_joined.parquet")
    if m1.exists():
        try:
            df = pd.read_parquet(m1, columns=["close"])
            results.append((PASS, f"M1 parquet: {len(df):,} rows, last={df.index[-1]}"))
        except Exception as e:
            results.append((FAIL, f"M1 parquet error: {e}"))
    else:
        results.append((FAIL, "M1 parquet MISSING"))

    # M5 boxes
    m5 = Path("C:/data/processed/gc_m5_boxes.parquet")
    if m5.exists():
        age = now - m5.stat().st_mtime
        try:
            df = pd.read_parquet(m5)
            last_liq = df["m5_liq_top"].dropna().iloc[-1] if "m5_liq_top" in df.columns else "?"
            results.append((PASS if age < 120 else WARN,
                           f"M5 parquet: age={age:.0f}s, {len(df)} bars, liq_top={last_liq}"))
        except Exception as e:
            results.append((FAIL, f"M5 parquet error: {e}"))
    else:
        results.append((FAIL, "M5 parquet MISSING"))

    # M30 boxes
    m30 = Path("C:/data/processed/gc_m30_boxes.parquet")
    if m30.exists():
        age = now - m30.stat().st_mtime
        try:
            df = pd.read_parquet(m30)
            last_liq = df["m30_liq_top"].dropna().iloc[-1] if "m30_liq_top" in df.columns else "?"
            atr = df["atr14"].dropna().iloc[-1] if "atr14" in df.columns else "?"
            results.append((PASS if age < 120 else WARN,
                           f"M30 parquet: age={age:.0f}s, {len(df)} bars, liq_top={last_liq}, atr14={atr}"))
        except Exception as e:
            results.append((FAIL, f"M30 parquet error: {e}"))
    else:
        results.append((FAIL, "M30 parquet MISSING"))

    return results


def check_level_detection():
    """Check that level_detector produces valid levels."""
    results = []
    try:
        from level_detector import get_current_levels
        levels = get_current_levels()
        if levels is None:
            results.append((FAIL, "get_current_levels() returned None"))
            return results

        liq_top = levels.get("liq_top", 0)
        liq_bot = levels.get("liq_bot", 0)
        source = levels.get("source", "?")
        daily_trend = levels.get("daily_trend", "?")
        m30_bias = levels.get("m30_bias", "?")

        results.append((PASS if liq_top > 0 else FAIL,
                       f"liq_top={liq_top} liq_bot={liq_bot} source={source}"))
        results.append((PASS if daily_trend in ("long", "short") else WARN,
                       f"daily_trend={daily_trend}"))
        results.append((PASS if m30_bias in ("bullish", "bearish") else WARN,
                       f"m30_bias={m30_bias}"))

        # M30 macro context
        m30_top = levels.get("m30_liq_top")
        m30_bot = levels.get("m30_liq_bot")
        results.append((PASS if m30_top else WARN,
                       f"M30 context: liq_top={m30_top} liq_bot={m30_bot}"))

    except Exception as e:
        results.append((FAIL, f"level_detector error: {e}"))

    return results


def check_service_state():
    """Check service_state.json for event processor health."""
    results = []
    ss_path = Path("C:/FluxQuantumAI/logs/service_state.json")
    if not ss_path.exists():
        results.append((FAIL, "service_state.json MISSING"))
        return results

    try:
        with open(ss_path) as f:
            ss = json.load(f)

        hb = ss.get("last_heartbeat_at", "?")
        phase = ss.get("phase", "?")
        bias = ss.get("m30_bias", "?")
        trend = ss.get("daily_trend", "?")
        feed = ss.get("feed_status", "?")
        near_src = ss.get("near_level_source", "?")
        def_tier = ss.get("defense_tier", "?")
        d1h4 = ss.get("d1h4_bias", {})

        # Heartbeat freshness
        try:
            hb_dt = datetime.fromisoformat(hb)
            hb_age = (datetime.now(timezone.utc) - hb_dt).total_seconds()
            results.append((PASS if hb_age < 60 else WARN,
                           f"Heartbeat: {hb_age:.0f}s ago"))
        except Exception:
            results.append((WARN, f"Heartbeat: {hb}"))

        results.append((PASS, f"Phase: {phase}"))
        results.append((PASS if bias != "unknown" else WARN, f"M30 bias: {bias}"))
        results.append((PASS if trend in ("long", "short") else WARN, f"Daily trend: {trend}"))
        results.append((PASS if feed == "OK" else WARN, f"Feed: {feed}"))
        results.append((PASS, f"Near level source: {near_src}"))
        results.append((PASS, f"Defense tier: {def_tier}"))

        # D1H4 shadow
        if isinstance(d1h4, dict) and d1h4.get("direction", "?") != "?":
            results.append((PASS,
                           f"D1H4 shadow: {d1h4.get('direction')}_{d1h4.get('strength')} "
                           f"(d1={d1h4.get('d1_jac_dir')} h4={d1h4.get('h4_jac_dir')})"))
        else:
            results.append((WARN, "D1H4 shadow: not populated"))

        # MT5 connectivity
        robo = ss.get("mt5_robo_connected", False)
        hantec = ss.get("mt5_hantec_connected", False)
        results.append((PASS if robo else FAIL, f"MT5 Roboforex: {'connected' if robo else 'DISCONNECTED'}"))
        results.append((PASS if hantec else WARN, f"MT5 Hantec: {'connected' if hantec else 'DISCONNECTED'}"))

    except Exception as e:
        results.append((FAIL, f"service_state.json error: {e}"))

    return results


def check_decision_pipeline():
    """Check decision_live.json and decision_log.jsonl."""
    results = []

    dl_path = Path("C:/FluxQuantumAI/logs/decision_live.json")
    if dl_path.exists():
        try:
            with open(dl_path) as f:
                dl = json.load(f)
            ts = dl.get("timestamp", "?")
            action = dl.get("decision", {}).get("action", "?")
            direction = dl.get("decision", {}).get("direction", "?")
            near_src = dl.get("trigger", {}).get("near_level_source", "?")
            results.append((PASS,
                           f"decision_live: {action} {direction} at {ts}"))
            results.append((PASS if near_src != "unknown" else WARN,
                           f"  near_level_source: {near_src}"))
        except Exception as e:
            results.append((FAIL, f"decision_live error: {e}"))
    else:
        results.append((WARN, "decision_live.json not yet created (no gate check yet)"))

    log_path = Path("C:/FluxQuantumAI/logs/decision_log.jsonl")
    if log_path.exists():
        size = log_path.stat().st_size
        results.append((PASS, f"decision_log.jsonl: {size/1024:.1f} KB"))
    else:
        results.append((WARN, "decision_log.jsonl not yet created"))

    return results


def check_telegram():
    """Test Telegram connectivity."""
    results = []
    try:
        from live.telegram_notifier import _TELEGRAM_TOKEN, _TELEGRAM_CHAT_ID
        has_token = bool(_TELEGRAM_TOKEN)
        has_chat = bool(_TELEGRAM_CHAT_ID)
        results.append((PASS if has_token else FAIL,
                       f"Telegram token: {'configured' if has_token else 'MISSING'}"))
        results.append((PASS if has_chat else FAIL,
                       f"Telegram chat_id: {'configured' if has_chat else 'MISSING'}"))
    except Exception as e:
        results.append((WARN, f"Telegram config check: {e}"))

    return results


def check_mt5_direct():
    """Direct MT5 connectivity test."""
    results = []
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            info = mt5.account_info()
            if info:
                results.append((PASS,
                               f"MT5 direct: connected, balance={info.balance:.2f}, "
                               f"server={info.server}"))
                # Check XAUUSD price
                tick = mt5.symbol_info_tick("XAUUSD")
                if tick:
                    mid = (tick.ask + tick.bid) / 2
                    results.append((PASS, f"  XAUUSD: {mid:.2f} (spread={tick.ask-tick.bid:.2f})"))
                else:
                    results.append((WARN, "  XAUUSD tick not available"))

                # Check open positions
                positions = mt5.positions_get()
                n_pos = len(positions) if positions else 0
                results.append((PASS, f"  Open positions: {n_pos}"))
            else:
                results.append((WARN, "MT5 connected but no account info"))
        else:
            results.append((FAIL, f"MT5 initialize failed: {mt5.last_error()}"))
    except ImportError:
        results.append((WARN, "MetaTrader5 package not importable"))
    except Exception as e:
        results.append((FAIL, f"MT5 error: {e}"))

    return results


def check_nssm_services():
    """Check NSSM service status."""
    results = []
    services = ["FluxQuantumAPEX", "FluxQuantumAPEX_Live",
                "FluxQuantumAPEX_Dashboard", "FluxQuantumAPEX_Dashboard_Hantec"]
    for svc in services:
        try:
            out = subprocess.check_output(
                ["C:/tools/nssm/nssm.exe", "status", svc],
                stderr=subprocess.STDOUT, timeout=5
            ).decode("utf-16-le", errors="replace").strip()
            running = "RUNNING" in out
            results.append((PASS if running else FAIL, f"{svc}: {'RUNNING' if running else out}"))
        except Exception as e:
            results.append((FAIL, f"{svc}: error ({e})"))

    return results


def check_capture_services():
    """Check capture services (ports 8000, 8002)."""
    results = []
    import urllib.request
    for port, name in [(8000, "quantower_level2_api"), (8002, "iceberg_receiver")]:
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3)
            data = json.loads(resp.read())
            status = data.get("status", "?")
            results.append((PASS if status == "healthy" else WARN,
                           f"{name} (:{port}): {status}"))
        except Exception as e:
            results.append((FAIL, f"{name} (:{port}): unreachable ({e})"))

    return results


def check_gate_simulation():
    """Simulate a gate check with current data (read-only, no execution)."""
    results = []
    try:
        from ats_live_gate import ATSLiveGate
        gate = ATSLiveGate()

        # Get current levels
        from level_detector import get_current_levels
        levels = get_current_levels()
        if levels is None:
            results.append((WARN, "Cannot simulate gate: no levels"))
            return results

        liq_top = levels.get("liq_top_mt5", levels.get("liq_top", 0))
        liq_bot = levels.get("liq_bot_mt5", levels.get("liq_bot", 0))
        atr = levels.get("atr_14", 20)

        if liq_top <= 0:
            results.append((WARN, "Cannot simulate gate: liq_top=0"))
            return results

        # Simulate SHORT at liq_top
        decision = gate.check(
            entry_price=liq_top,
            direction="SHORT",
            now=pd.Timestamp.utcnow(),
            liq_top=liq_top,
            liq_bot=liq_bot,
            daily_trend=levels.get("daily_trend", "unknown"),
        )

        results.append((PASS,
                       f"Gate simulation (SHORT@{liq_top:.2f}): "
                       f"{'GO' if decision.go else 'BLOCK'} "
                       f"score={decision.total_score} reason={decision.reason[:50]}"))

    except Exception as e:
        results.append((WARN, f"Gate simulation error: {e}"))

    return results


def main():
    print()
    print("=" * 70)
    print("  FLUXQUANTUMAI — END-TO-END INTEGRATION HEALTH CHECK")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    sections = [
        ("1. DATA SOURCES", check_data_sources),
        ("2. LEVEL DETECTION", check_level_detection),
        ("3. CAPTURE SERVICES", check_capture_services),
        ("4. NSSM SERVICES", check_nssm_services),
        ("5. SERVICE STATE (Event Processor)", check_service_state),
        ("6. DECISION PIPELINE", check_decision_pipeline),
        ("7. MT5 CONNECTIVITY", check_mt5_direct),
        ("8. TELEGRAM", check_telegram),
        ("9. GATE SIMULATION", check_gate_simulation),
    ]

    total_pass = 0
    total_fail = 0
    total_warn = 0

    for title, check_fn in sections:
        print(f"\n  --- {title} ---")
        try:
            results = check_fn()
            for status, msg in results:
                icon = status_icon(status)
                print(f"  {icon} {msg}")
                if status == PASS:
                    total_pass += 1
                elif status == FAIL:
                    total_fail += 1
                else:
                    total_warn += 1
        except Exception as e:
            print(f"  [XX] Section error: {e}")
            total_fail += 1

    print()
    print("=" * 70)
    print(f"  SUMMARY: {total_pass} PASS | {total_warn} WARN | {total_fail} FAIL")
    if total_fail == 0:
        print("  VERDICT: SYSTEM OPERATIONAL")
    elif total_fail <= 2:
        print("  VERDICT: SYSTEM DEGRADED (check FAIL items)")
    else:
        print("  VERDICT: SYSTEM CRITICAL (multiple failures)")
    print("=" * 70)


if __name__ == "__main__":
    main()

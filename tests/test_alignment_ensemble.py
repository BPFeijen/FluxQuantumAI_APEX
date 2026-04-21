"""
test_alignment_ensemble.py — Bateria de Testes de Alinhamento e Conjunto

Teste 1 — Alignment Test (AnomalyForge vs OrderStorm):
    Cenários controlados onde os dois gates divergem.
    Verifica que HARD_VETO do Forge sempre vence.

Teste 2 — Ensemble Test (The Full Machine):
    Replay de ~4h de dados reais (microstructure_2025-12-02.csv.gz).
    Pipeline: dxFeed -> G1:AnomalyForge -> G2:News -> G3:FluxSignal ->
              G4:OrderStorm -> G5:Regime -> MasterVerdict -> War Room.

Entregáveis:
    1. Relatório de Confluência (tabela Acordo/Conflito Forge vs Iceberg)
    2. Log War Room simulado (chat completo do período)
    3. Métrica de Latência Sistémica E2E

Execução:
    python tests/test_alignment_ensemble.py
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─── Path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import logging
logging.basicConfig(
    level=logging.WARNING,       # silenciar logs internos durante o replay
    format="%(levelname)s %(name)s: %(message)s",
)

from apex_nextgen.common.base_provider import Direction, MarketTick, VetoStatus
from apex_nextgen.engine.foxyze_master import FoxyzeMaster, MasterVerdict
from schemas.SCHEMA_FLUXFOX_V2 import SCHEMA_FLUXFOX_V2

# ─── Constantes ───────────────────────────────────────────────────────────────

MICROSTRUCTURE_FILE = Path(r"C:\data\level2\_gc_xcec\microstructure_2025-12-02.csv.gz")
# NY session 2025-12-02: 09:30–13:30 (UTC-5 → 14:30–18:30 UTC)
REPLAY_START_UTC = "2025-12-02T14:30:00"
REPLAY_END_UTC   = "2025-12-02T18:30:00"

OUTPUT_DIR = ROOT / "tests" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CONFLUENCE_REPORT = OUTPUT_DIR / "confluence_report.txt"
WAR_ROOM_LOG      = OUTPUT_DIR / "war_room_log.jsonl"
LATENCY_REPORT    = OUTPUT_DIR / "latency_report.txt"


# ══════════════════════════════════════════════════════════════════════════════
# PARTE 1 — ALIGNMENT TEST
# ══════════════════════════════════════════════════════════════════════════════

def _make_tick(
    book_imbalance:    float = 0.0,
    cumulative_delta:  float = 0.0,
    spread:            float = 0.2,
    iceberg_proxy_score: float = 0.0,
    iceberg_side:      Optional[str] = None,
    grenadier_mse:     Optional[float] = None,
    fluxfox_features:  Optional[Dict] = None,
    ts:                Optional[datetime] = None,
) -> MarketTick:
    """Helper: cria MarketTick sintético para testes de alinhamento."""
    tick = MarketTick(
        timestamp           = ts or datetime(2025, 12, 2, 16, 0, 0, tzinfo=timezone.utc),
        symbol              = "GC",
        spread              = spread,
        total_bid_depth     = 200.0,
        total_ask_depth     = 200.0,
        book_imbalance      = book_imbalance,
        cumulative_delta    = cumulative_delta,
        fluxfox_features    = fluxfox_features or {},
        grenadier_mse       = grenadier_mse,
    )
    # Campos adicionais para OrderStorm (não são dataclass fields — append dinâmico)
    object.__setattr__(tick, "__dict__", tick.__dict__)
    tick.__dict__["iceberg_proxy_score"] = iceberg_proxy_score
    tick.__dict__["iceberg_side"]        = iceberg_side
    return tick


def _mse_for_score(score: float, calib_threshold: float = 1.5217) -> float:
    """Converte score alvo em MSE sintético (inverso de _mse_to_score)."""
    return score * (calib_threshold * 3.0)


ALIGNMENT_SCENARIOS = [
    # (nome, desc, book_imbalance, delta, iceberg_score, iceberg_side, anomaly_score_target)
    (
        "A: Normal + Iceberg Aligned",
        "Mercado normal, iceberg BUY confirmado, DOM SHORT (sellers esgotados -> LONG). "
        "Esperado: LONG com size amplificado (x1.30).",
        -0.42,   # dom SHORT -0.42 -> LONG signal
        200.0,   # delta neutro
        0.91,    # iceberg activo + strong
        "BUY",   # iceberg alinhado com LONG
        0.10,    # anomaly score baixo -> CLEAR
    ),
    (
        "B: AnomalyForge WARNING + Iceberg Aligned",
        "Anomalia moderada (score=0.96, WARNING). Iceberg alinhado. "
        "Esperado: sinal emitido mas size reduzido (x0.75 do Forge).",
        -0.45,
        100.0,
        0.91,
        "BUY",
        0.96,    # WARNING zone (>=0.95 < 0.99)
    ),
    (
        "C: AnomalyForge CRITICAL + Iceberg Aligned",
        "Anomalia critica (score=0.992). Iceberg fortemente alinhado. "
        "Esperado: size muito reduzido (x0.50 Forge x 1.30 Iceberg = x0.65).",
        -0.48,
        100.0,
        0.95,
        "BUY",
        0.992,   # CRITICAL zone (>=0.99 < 0.995)
    ),
    (
        "D: AnomalyForge SEVERE + Iceberg Strongly Aligned",
        "Anomalia severa (score=0.997). Iceberg perfeito. "
        "Esperado: size minimo (x0.25 Forge = 1 contrato de 4).",
        -0.50,
        300.0,
        0.97,
        "BUY",
        0.997,   # SEVERE zone (>=0.995 < 0.999)
    ),
    (
        "E: AnomalyForge HARD_VETO + Iceberg Strongly Aligned (STRESS TEST)",
        "HARD_VETO (score=0.9995). Iceberg buyflow enorme. "
        "Esperado: BLOQUEADO no Gate 1. Multiplicador final = 0.0.",
        -0.55,   # DOM imbalance fortissimo
        500.0,
        0.99,    # iceberg muito forte
        "BUY",
        0.9995,  # HARD_VETO (>=0.999) ← stress test etico
    ),
    (
        "F: AnomalyForge HARD_VETO + Iceberg OPPOSED",
        "HARD_VETO e iceberg contra. Duplo sinal de perigo. "
        "Esperado: BLOQUEADO Gate 1.",
        +0.50,   # DOM buyer-heavy -> SHORT tentativa
        -200.0,
        0.92,    # iceberg BUY (oposto ao SHORT)
        "BUY",
        0.9995,
    ),
    (
        "G: Normal Market, Iceberg Opposed (Soft Veto)",
        "Mercado normal. Iceberg contra a direcção (SOFT_VETO Gate 4). "
        "Esperado: sinal emitido com size reduzido (x0.75).",
        -0.40,
        100.0,
        0.91,    # iceberg activo
        "SELL",  # iceberg contra LONG -> opposed
        0.10,
    ),
    (
        "H: Zero-Contract Rule",
        "Anomalia alta + iceberg oposto + regime adverse = size < 1 contrato. "
        "Esperado: NO_TRADE_ZERO_CONTRACTS.",
        -0.31,   # sinal muito fraco (barely above 0.30)
        100.0,
        0.91,    # iceberg oposto
        "SELL",
        0.97,    # CRITICAL -> x0.50
    ),
]


def run_alignment_tests(master: FoxyzeMaster) -> List[dict]:
    """Executa todos os cenários de alinhamento e retorna resultados."""
    results = []
    calib   = 1.5217  # threshold calibrado

    print("\n" + "=" * 72)
    print("PARTE 1 — ALIGNMENT TEST (AnomalyForge vs OrderStorm)")
    print("=" * 72)

    for scenario in ALIGNMENT_SCENARIOS:
        name, desc, imb, delta, ice_score, ice_side, a_score = scenario
        mse = _mse_for_score(a_score, calib)

        tick = _make_tick(
            book_imbalance      = imb,
            cumulative_delta    = delta,
            spread              = 0.20,
            iceberg_proxy_score = ice_score,
            iceberg_side        = ice_side,
            grenadier_mse       = mse,
        )

        verdict = master.evaluate(tick)

        # ── Determinar estado do Forge e do Iceberg ──────────────────────────
        g1 = next((v for v in verdict.gate_verdicts if v["gate"] == 1), None)
        g4 = next((v for v in verdict.gate_verdicts if v["gate"] == 4), None)

        forge_level    = g1["metadata"].get("anomaly_level", "STUB")  if g1 else "N/A"
        forge_veto     = g1["veto"]    if g1 else "N/A"
        iceberg_conf   = g4["metadata"].get("confluence", "N/A")      if g4 else "N/A"

        expected_veto  = a_score >= 0.999
        got_veto       = verdict.blocked_at_gate == 1

        # ── Pass/Fail ─────────────────────────────────────────────────────────
        if expected_veto:
            ok = got_veto and verdict.contracts == 0.0 and verdict.direction == "FLAT"
            status = "PASS" if ok else "FAIL"
        else:
            # Para não-veto, apenas verificar que passou o Gate 1
            ok = verdict.blocked_at_gate != 1
            status = "PASS" if ok else "FAIL"

        result = {
            "scenario":       name,
            "anomaly_score":  round(a_score, 4),
            "forge_level":    forge_level,
            "forge_veto":     forge_veto,
            "iceberg_conf":   iceberg_conf,
            "direction":      verdict.direction,
            "contracts":      verdict.contracts,
            "final_mult":     verdict.final_multiplier,
            "blocked_gate":   verdict.blocked_at_gate,
            "block_reason":   verdict.block_reason,
            "latency_ms":     verdict.latency_ms,
            "status":         status,
            "expected_veto":  expected_veto,
        }
        results.append(result)

        # ── Print ─────────────────────────────────────────────────────────────
        marker = "[PASS]" if ok else "[FAIL]"
        print(f"\n{marker} {name}")
        print(f"  Anomaly score={a_score:.4f} -> {forge_level} | Iceberg: {iceberg_conf}")
        print(f"  Direction={verdict.direction} | Contracts={verdict.contracts:.2f} | FinalMult={verdict.final_multiplier:.4f}")
        if verdict.blocked_at_gate:
            print(f"  BLOCKED at Gate {verdict.blocked_at_gate}: {verdict.block_reason}")
        else:
            sizing = verdict.metadata.get("sizing_breakdown", {})
            if sizing:
                parts = [f"G{k[0].upper()}{k[1:]}={v}" if not k.endswith('_mult') else
                         f"{k}={v}" for k, v in sizing.items()]
                print(f"  Sizing: {' | '.join(str(p) for p in list(sizing.items()))}")
        print(f"  Latency: {verdict.latency_ms:.2f}ms")

    passes = sum(1 for r in results if r["status"] == "PASS")
    print(f"\n{'='*72}")
    print(f"Alignment Test: {passes}/{len(results)} passed")
    print(f"{'='*72}")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# PARTE 2 — ENSEMBLE TEST (Full Machine Replay)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_iceberg_proxy(row: dict) -> Tuple[float, Optional[str]]:
    """
    Calcula iceberg_proxy_score e iceberg_side a partir das colunas do microstructure CSV.

    Score proxy (heurístico, equivalente ao que event_processor.py computa):
        - absorption_ratio: razão de absorção de agressores (0-1)
        - large_order_imbalance: LOI [bid-ask pressure imbalance]
        - wall_bid_size / wall_ask_size: paredes detectadas no DOM
        - sweep_detected: varredura de liquidez

    Score = min(1.0, |loi|/0.80 * 0.50 + absorption * 0.30 + sweep * 0.20)
    Side  = "BUY" se loi < 0 (seller-heavy = iceberg buy absorving) else "SELL"
    """
    def safe_float(v, default=0.0):
        try: return float(v) if v and v.strip() else default
        except: return default

    loi            = safe_float(row.get("large_order_imbalance", ""))  # in [-100, 100]
    absorption     = safe_float(row.get("absorption_ratio", ""))
    sweep          = 1.0 if row.get("sweep_detected", "").lower() in ("1", "true", "yes") else 0.0
    wall_bid       = safe_float(row.get("wall_bid_size", ""))
    wall_ask       = safe_float(row.get("wall_ask_size", ""))

    # Normalizar LOI de [-100, 100] para [-1, 1]
    loi_norm = loi / 100.0

    # Score: LOI é a feature mais importante para proxy iceberg
    loi_component  = min(abs(loi_norm) / 0.80, 1.0) * 0.50
    abs_component  = min(absorption, 1.0) * 0.30
    sweep_component = sweep * 0.20
    wall_component = min((wall_bid + wall_ask) / 1000.0, 1.0) * 0.10

    score = min(1.0, loi_component + abs_component + sweep_component + wall_component)

    # Side: LOI negativo = sellers pressionam mais = iceberg buy absorve
    if abs(loi_norm) < 0.10:
        side = None  # imaterial
    else:
        side = "BUY" if loi_norm < 0 else "SELL"

    return round(score, 4), side


def _build_fluxfox_features(row: dict) -> Dict[str, float]:
    """
    Mapeia colunas do microstructure CSV para SCHEMA_FLUXFOX_V2.

    Os 26 campos SCHEMA_FLUXFOX_V2 são features derivadas de MBO tick-level
    que o microstructure CSV não tem directamente. Mapeamos o que está
    disponível e deixamos o resto em 0.0 (missing value handling do modelo).

    Mapeamento disponível (≈11/26 features):
        dom_imbalance_at_level  ← dom_imbalance (normalizado /100)
        trade_intensity         ← trades_per_second
        hour_of_day             ← extraído do timestamp
        minute_of_hour          ← extraído do timestamp
        ofi_rate                ← large_order_imbalance (normalizado)
        vot                     ← volume_per_second
        inst_volatility_ticks   ← toxicity_score (proxy)
        refill_count            ← large_bid_count + large_ask_count (proxy refills)
        aggressor_volume_absorbed ← absorption_ratio * volume_per_second
        level_lifetime_s        ← wall_distance_ticks (proxy)
        iceberg_sequence_direction_score ← large_order_imbalance / 100
    """
    def safe_float(v, default=0.0):
        try: return float(v) if v and str(v).strip() else default
        except: return default

    ts_str = row.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        hour   = float(ts.hour)
        minute = float(ts.minute)
    except:
        hour, minute = 0.0, 0.0

    dom_imb  = safe_float(row.get("dom_imbalance")) / 100.0      # normalize
    loi      = safe_float(row.get("large_order_imbalance")) / 100.0
    t_per_s  = safe_float(row.get("trades_per_second"))
    vol_ps   = safe_float(row.get("volume_per_second"))
    tox      = safe_float(row.get("toxicity_score"))             # normalized already
    abs_r    = safe_float(row.get("absorption_ratio"))
    l_bid    = safe_float(row.get("large_bid_count"))
    l_ask    = safe_float(row.get("large_ask_count"))
    wall_d   = safe_float(row.get("wall_distance_ticks"))

    features: Dict[str, float] = {col: 0.0 for col in SCHEMA_FLUXFOX_V2}

    features["dom_imbalance_at_level"]              = dom_imb
    features["trade_intensity"]                     = t_per_s
    features["hour_of_day"]                         = hour
    features["minute_of_hour"]                      = minute
    features["ofi_rate"]                            = loi
    features["vot"]                                 = vol_ps
    features["inst_volatility_ticks"]               = max(0.0, -tox / 100.0)  # tox is negative
    features["refill_count"]                        = (l_bid + l_ask) * 0.10  # proxy
    features["aggressor_volume_absorbed"]           = abs_r * vol_ps
    features["level_lifetime_s"]                    = wall_d * 0.10           # proxy
    features["iceberg_sequence_direction_score"]    = loi

    return features


def _row_to_tick(row: dict) -> Optional[MarketTick]:
    """Converte uma linha do microstructure CSV para MarketTick."""
    def safe_float(v, default=0.0):
        try: return float(v) if v and str(v).strip() else default
        except: return default

    ts_str = row.get("timestamp", "")
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except:
        return None

    # dom_imbalance no CSV: range varia (-100 a +100 ou normalizado?)
    # Amostra viu: 0.0, -66.67 → está em percent → dividir por 100
    dom_raw = safe_float(row.get("dom_imbalance"))
    dom_norm = dom_raw / 100.0   # -> [-1, 1]

    spread = safe_float(row.get("spread"), 0.3)

    iceberg_score, iceberg_side = _compute_iceberg_proxy(row)
    fluxfox = _build_fluxfox_features(row)

    tick = MarketTick(
        timestamp        = ts,
        symbol           = "GC",
        spread           = spread,
        total_bid_depth  = safe_float(row.get("total_bid_size")),
        total_ask_depth  = safe_float(row.get("total_ask_size")),
        book_imbalance   = dom_norm,
        cumulative_delta = safe_float(row.get("cumulative_delta")),
        trade_volume     = safe_float(row.get("volume_per_second")),
        fluxfox_features = fluxfox,
    )
    tick.__dict__["iceberg_proxy_score"] = iceberg_score
    tick.__dict__["iceberg_side"]        = iceberg_side
    return tick


def _verdict_to_war_room(v: MasterVerdict) -> List[dict]:
    """
    Converte MasterVerdict em mensagens de diálogo para o War Room.
    Formato compatível com NextGenDataBus._verdict_to_messages().
    """
    ts = v.timestamp.isoformat() if hasattr(v.timestamp, "isoformat") else str(v.timestamp)
    msgs = []

    def msg(speaker, gate, text, tag):
        msgs.append({"ts": ts, "speaker": speaker, "gate": gate, "msg": text, "tag": tag})

    # Gate 1 — AnomalyForge
    g1 = next((gv for gv in v.gate_verdicts if gv["gate"] == 1), None)
    if g1:
        level = g1["metadata"].get("anomaly_level", "STUB")
        score = g1["metadata"].get("anomaly_score", 0.0)
        if g1["is_stub"]:
            msg("Pythia", 1, f"[WARMUP] Buffer a preencher ({g1['metadata'].get('buffer_fill', '?')}/60)", "stub")
        elif level == "CLEAR":
            msg("Pythia", 1, f"Microestrutura normal. Score={score:.3f} CLEAR.", "ok")
        elif level == "WARNING":
            msg("Pythia", 1, f"ATENCAO: anomalia detectada. Score={score:.3f} WARNING. Size x0.75.", "warn")
        elif level == "CRITICAL":
            msg("Pythia", 1, f"CRITICO: anomalia grave. Score={score:.3f} CRITICAL. Size x0.50.", "warn")
        elif level == "SEVERE":
            msg("Pythia", 1, f"SEVERO: risco sistemico. Score={score:.3f} SEVERE. Size x0.25.", "warn")
        elif level == "HARD_VETO":
            msg("Pythia", 1, f"HARD VETO. Anomalia extrema score={score:.3f}. Entradas bloqueadas.", "veto")

    # Bloqueado no Gate 1?
    if v.blocked_at_gate == 1:
        msg("Mr. Money", 1, f"VETO Gate 1: {v.block_reason}. Sem trades.", "block")
        return msgs

    # Gate 3 — FluxSignalEngine
    g3 = next((gv for gv in v.gate_verdicts if gv["gate"] == 3), None)
    if g3:
        imb = g3["metadata"].get("book_imbalance", 0.0)
        delta = g3["metadata"].get("cumulative_delta", 0.0)
        regime = g3["metadata"].get("regime_label", "FLAT")
        shadow = g3["metadata"].get("shadow_thresholds", {})
        t040 = shadow.get("t040", {}).get("direction", "FLAT")
        t045 = shadow.get("t045", {}).get("direction", "FLAT")

        if g3["direction"] == "FLAT":
            msg("Zeus", 3, f"DOM={imb:.3f} Delta={delta:.0f}. Sem sinal direcional.", "stub")
        else:
            dir_txt = "COMPRA" if g3["direction"] == "LONG" else "VENDA"
            msg("Zeus", 3,
                f"Sinal {dir_txt} | DOM={imb:.3f} Delta={delta:.0f} | "
                f"Regime={regime} | T040={t040} T045={t045}",
                "signal")

    # Bloqueado no Gate 3?
    if v.blocked_at_gate == 3:
        msg("Mr. Money", 3, f"Momentum block: {v.block_reason}.", "block")
        return msgs

    # Sem sinal
    if v.direction == "FLAT" and v.blocked_at_gate is None:
        reason = v.block_reason or "NO_SIGNAL"
        msg("Mr. Money", 0, f"Sem entrada. {reason}.", "stub")
        return msgs

    # Gate 4 — OrderStorm
    g4 = next((gv for gv in v.gate_verdicts if gv["gate"] == 4), None)
    if g4:
        conf = g4["metadata"].get("confluence", "neutral")
        score = g4["metadata"].get("iceberg_score", 0.0)
        mult = g4["size_mult"]
        conf_map = {"aligned": "ALINHADO", "neutral": "NEUTRO", "opposed": "OPOSTO"}
        tag = "boost" if conf == "aligned" else ("warn" if conf == "opposed" else "ok")
        msg("Iceberg", 4,
            f"Iceberg {conf_map.get(conf, conf)} (score={score:.2f} mult x{mult:.2f}).",
            tag)

    # Gate 5 — RegimeForecast
    g5 = next((gv for gv in v.gate_verdicts if gv["gate"] == 5), None)
    if g5:
        regime = g5["metadata"].get("regime", "unknown")
        entry  = g5["metadata"].get("entry_quality", "")
        msg("Oracle", 5, f"Regime={regime} | Entry={entry} | mult x{g5['size_mult']:.2f}", "ok")

    # Decisao final
    if v.direction not in ("FLAT", "FORCE_EXIT"):
        dir_ptbr = "LONG" if v.direction == "LONG" else "SHORT"
        sizing = v.metadata.get("sizing_breakdown", {})
        msg("Mr. Money", 0,
            f"ENTRADA {dir_ptbr} | {v.contracts:.1f} contratos | "
            f"mult_final={v.final_multiplier:.3f} | lat={v.latency_ms:.1f}ms",
            "decision")
    elif v.direction == "FORCE_EXIT":
        msg("Mr. Money", 0, f"FORCE EXIT: {v.block_reason}", "veto")
    elif v.block_reason == "NO_TRADE_ZERO_CONTRACTS":
        msg("Mr. Money", 0, f"NO_TRADE: sizing abaixo de 1 contrato.", "block")

    return msgs


def load_replay_rows() -> List[dict]:
    """Carrega linhas do microstructure CSV para o window de replay."""
    if not MICROSTRUCTURE_FILE.exists():
        raise FileNotFoundError(f"Ficheiro nao encontrado: {MICROSTRUCTURE_FILE}")

    rows = []
    with gzip.open(str(MICROSTRUCTURE_FILE), "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp", "")
            if ts < REPLAY_START_UTC:
                continue
            if ts > REPLAY_END_UTC:
                break
            rows.append(row)
    return rows


def run_ensemble_test(master: FoxyzeMaster) -> dict:
    """
    Replay de 4h de dados reais.
    Retorna dict com estatísticas de confluência e latência.
    """
    print("\n" + "=" * 72)
    print("PARTE 2 — ENSEMBLE TEST (Full Machine Replay)")
    print(f"  Ficheiro: {MICROSTRUCTURE_FILE.name}")
    print(f"  Window:   {REPLAY_START_UTC} -> {REPLAY_END_UTC} UTC (NY session 09:30-13:30)")
    print("=" * 72)

    rows = load_replay_rows()
    print(f"  Rows carregados: {len(rows)}")

    if not rows:
        print("  ERRO: nenhuma linha no window. Verificar ficheiro e window.")
        return {}

    # ── Replay ────────────────────────────────────────────────────────────────
    verdicts: List[MasterVerdict] = []
    war_room_messages: List[dict] = []
    latencies: List[float] = []
    parse_errors = 0

    t_replay_start = time.perf_counter()
    print(f"  Iniciando replay de {len(rows)} ticks...")

    for i, row in enumerate(rows):
        tick = _row_to_tick(row)
        if tick is None:
            parse_errors += 1
            continue

        verdict = master.evaluate(tick)
        verdicts.append(verdict)
        latencies.append(verdict.latency_ms)

        # War room
        war_room_messages.extend(_verdict_to_war_room(verdict))

        # Progress
        if (i + 1) % 2000 == 0 or (i + 1) == len(rows):
            elapsed = time.perf_counter() - t_replay_start
            print(f"    [{i+1:>5}/{len(rows)}] {elapsed:.1f}s elapsed | "
                  f"signals={sum(1 for v in verdicts if v.direction not in ('FLAT','FORCE_EXIT'))} | "
                  f"vetos={sum(1 for v in verdicts if v.blocked_at_gate is not None)}")

    t_replay_end = time.perf_counter()
    replay_duration = t_replay_end - t_replay_start

    print(f"\n  Replay concluido em {replay_duration:.2f}s ({len(verdicts)} ticks processados)")
    print(f"  Parse errors: {parse_errors}")

    # ── Confluence Analysis ───────────────────────────────────────────────────
    stats = _compute_confluence_stats(verdicts)
    stats["replay_ticks"]      = len(verdicts)
    stats["replay_duration_s"] = round(replay_duration, 2)
    stats["parse_errors"]      = parse_errors

    # ── Save War Room ─────────────────────────────────────────────────────────
    with open(WAR_ROOM_LOG, "w", encoding="utf-8") as f:
        for msg in war_room_messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    print(f"  War Room log: {len(war_room_messages)} msgs -> {WAR_ROOM_LOG}")

    # ── Latency Stats ─────────────────────────────────────────────────────────
    if latencies:
        stats["latency"] = {
            "min_ms":    round(min(latencies), 3),
            "max_ms":    round(max(latencies), 3),
            "mean_ms":   round(sum(latencies) / len(latencies), 3),
            "p50_ms":    round(_percentile(latencies, 50), 3),
            "p95_ms":    round(_percentile(latencies, 95), 3),
            "p99_ms":    round(_percentile(latencies, 99), 3),
        }

    return stats


def _compute_confluence_stats(verdicts: List[MasterVerdict]) -> dict:
    """
    Calcula métricas de confluência Forge vs Iceberg.
    Classifica cada tick como AGREE / CONFLICT / NEUTRAL.
    """
    agree    = 0
    conflict = 0
    neutral  = 0
    forge_vetos    = 0
    iceberg_boosts = 0
    iceberg_opposed = 0
    signal_count   = 0
    zero_contracts = 0
    blocked_g1     = 0
    blocked_g3     = 0

    # Forge levels
    forge_level_counts = {"CLEAR": 0, "WARNING": 0, "CRITICAL": 0, "SEVERE": 0, "HARD_VETO": 0, "STUB": 0}

    # Iceberg confluence
    iceberg_conf_counts = {"aligned": 0, "neutral": 0, "opposed": 0, "stub": 0}

    # Confluencia moments: ticks onde G1 e G4 concordam ou conflituam
    confluence_moments: List[dict] = []

    for v in verdicts:
        g1 = next((gv for gv in v.gate_verdicts if gv["gate"] == 1), None)
        g4 = next((gv for gv in v.gate_verdicts if gv["gate"] == 4), None)

        forge_level = "STUB"
        if g1:
            forge_level = g1["metadata"].get("anomaly_level", "STUB")
            if g1["is_stub"]:
                forge_level = "STUB"
        forge_level_counts[forge_level] = forge_level_counts.get(forge_level, 0) + 1

        if g1 and g1["veto"] == "HARD_VETO":
            forge_vetos += 1
        if v.blocked_at_gate == 1:
            blocked_g1 += 1
        if v.blocked_at_gate == 3:
            blocked_g3 += 1
        if v.block_reason == "NO_TRADE_ZERO_CONTRACTS":
            zero_contracts += 1

        iceberg_conf = "stub"
        if g4 and not g4["is_stub"]:
            iceberg_conf = g4["metadata"].get("confluence", "neutral")
        iceberg_conf_counts[iceberg_conf] = iceberg_conf_counts.get(iceberg_conf, 0) + 1

        if iceberg_conf == "aligned":
            iceberg_boosts += 1
        elif iceberg_conf == "opposed":
            iceberg_opposed += 1

        if v.direction not in ("FLAT", "FORCE_EXIT"):
            signal_count += 1

        # Classify confluence
        forge_anomalous = forge_level in ("WARNING", "CRITICAL", "SEVERE", "HARD_VETO")
        ice_active      = iceberg_conf in ("aligned", "opposed")

        if forge_anomalous and iceberg_conf == "aligned":
            # Conflict: Forge says danger, Iceberg says opportunity
            conflict += 1
            if len(confluence_moments) < 20:  # capture first 20 conflicts
                confluence_moments.append({
                    "type":         "CONFLICT",
                    "ts":           v.timestamp.isoformat() if hasattr(v.timestamp, "isoformat") else str(v.timestamp),
                    "forge_level":  forge_level,
                    "iceberg_conf": iceberg_conf,
                    "direction":    v.direction,
                    "contracts":    v.contracts,
                    "mult":         v.final_multiplier,
                    "won_by":       "FORGE" if v.blocked_at_gate in (1, None) and v.contracts < 1.0 else "PIPELINE",
                })
        elif not forge_anomalous and iceberg_conf == "aligned" and v.direction not in ("FLAT",):
            # Agree: both bullish
            agree += 1
            if len(confluence_moments) < 20 and len([m for m in confluence_moments if m["type"] == "AGREE"]) < 5:
                confluence_moments.append({
                    "type":         "AGREE",
                    "ts":           v.timestamp.isoformat() if hasattr(v.timestamp, "isoformat") else str(v.timestamp),
                    "forge_level":  forge_level,
                    "iceberg_conf": iceberg_conf,
                    "direction":    v.direction,
                    "contracts":    v.contracts,
                    "mult":         v.final_multiplier,
                })
        else:
            neutral += 1

    return {
        "total_ticks":        len(verdicts),
        "signals":            signal_count,
        "agree":              agree,
        "conflict":           conflict,
        "neutral":            neutral,
        "forge_vetos":        forge_vetos,
        "blocked_gate1":      blocked_g1,
        "blocked_gate3":      blocked_g3,
        "zero_contracts":     zero_contracts,
        "iceberg_boosts":     iceberg_boosts,
        "iceberg_opposed":    iceberg_opposed,
        "forge_level_dist":   forge_level_counts,
        "iceberg_conf_dist":  iceberg_conf_counts,
        "confluence_moments": confluence_moments,
    }


def _percentile(data: list, p: float) -> float:
    """Calcula percentil p (0-100) de uma lista de floats."""
    if not data:
        return 0.0
    s = sorted(data)
    idx = (len(s) - 1) * p / 100.0
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


# ══════════════════════════════════════════════════════════════════════════════
# RELATÓRIOS
# ══════════════════════════════════════════════════════════════════════════════

def write_confluence_report(alignment_results: List[dict], ensemble_stats: dict):
    """Escreve relatório de confluência completo."""
    lines = []
    a = lines.append

    a("=" * 72)
    a("RELATORIO DE CONFLUENCIA — AnomalyForge vs OrderStorm")
    a(f"Gerado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    a("=" * 72)

    # ── Alignment Test Summary ──────────────────────────────────────────────
    a("\n--- PARTE 1: ALIGNMENT TEST (Stress Etico) ---\n")
    header = f"{'Cenario':<42} {'AnomalyScore':>12} {'ForgeLevel':>12} {'Iceberg':>10} {'Result':>8} {'Contracts':>9} {'FinalMult':>9}"
    a(header)
    a("-" * 90)
    for r in alignment_results:
        name_short = r["scenario"][:40]
        a(
            f"{name_short:<42} {r['anomaly_score']:>12.4f} "
            f"{r['forge_level']:>12} {r['iceberg_conf']:>10} "
            f"{r['status']:>8} {r['contracts']:>9.2f} {r['final_mult']:>9.4f}"
        )
    passes = sum(1 for r in alignment_results if r["status"] == "PASS")
    a(f"\nAlignmnent Test: {passes}/{len(alignment_results)} passed")

    # ── Ensemble Stats ──────────────────────────────────────────────────────
    if ensemble_stats:
        a("\n--- PARTE 2: ENSEMBLE TEST (Full Machine Replay) ---\n")
        a(f"Window:  {REPLAY_START_UTC} -> {REPLAY_END_UTC} UTC")
        a(f"Ticks processados:  {ensemble_stats.get('replay_ticks', 0)}")
        a(f"Replay em:          {ensemble_stats.get('replay_duration_s', 0):.2f}s")
        a(f"Parse errors:       {ensemble_stats.get('parse_errors', 0)}")
        a("")
        a("  SINAIS E VETOS:")
        a(f"    Sinais emitidos:         {ensemble_stats.get('signals', 0)}")
        a(f"    Bloqueados Gate 1:       {ensemble_stats.get('blocked_gate1', 0)}")
        a(f"    Bloqueados Gate 3:       {ensemble_stats.get('blocked_gate3', 0)}")
        a(f"    Zero-contract trades:    {ensemble_stats.get('zero_contracts', 0)}")
        a("")
        a("  DISTRIBUICAO FORGE LEVELS:")
        for level, count in ensemble_stats.get("forge_level_dist", {}).items():
            pct = count / max(ensemble_stats.get("replay_ticks", 1), 1) * 100
            a(f"    {level:<12}: {count:>5}  ({pct:.1f}%)")
        a("")
        a("  DISTRIBUICAO ICEBERG CONFLUENCE:")
        for conf, count in ensemble_stats.get("iceberg_conf_dist", {}).items():
            pct = count / max(ensemble_stats.get("replay_ticks", 1), 1) * 100
            a(f"    {conf:<10}: {count:>5}  ({pct:.1f}%)")
        a("")
        a("  CONFLUENCIA FORGE vs ICEBERG:")
        a(f"    AGREE    (Forge CLEAR + Iceberg ALIGNED): {ensemble_stats.get('agree', 0)}")
        a(f"    CONFLICT (Forge ANOMALOUS + Iceberg ALIGNED): {ensemble_stats.get('conflict', 0)}")
        a(f"    NEUTRAL: {ensemble_stats.get('neutral', 0)}")
        a("")
        a("  MOMENTOS DE CONFLUENCIA (primeiros 20):")
        for m in ensemble_stats.get("confluence_moments", []):
            a(f"    [{m['type']:>8}] {m['ts'][:19]} | Forge={m['forge_level']:>8} "
              f"Ice={m['iceberg_conf']:>8} | {m['direction']:>5} {m.get('contracts', 0):.1f}c "
              f"x{m.get('mult', 0):.3f} | {m.get('won_by', '')}")

        # ── Latency ──────────────────────────────────────────────────────────
        lat = ensemble_stats.get("latency", {})
        if lat:
            a("")
            a("--- METRICA DE LATENCIA SISTEMICA E2E ---\n")
            a("  (dxFeed -> G1 -> G2 -> G3 -> G4 -> G5 -> MasterVerdict)")
            a(f"    Min:  {lat['min_ms']:>8.3f} ms")
            a(f"    P50:  {lat['p50_ms']:>8.3f} ms")
            a(f"    P95:  {lat['p95_ms']:>8.3f} ms")
            a(f"    P99:  {lat['p99_ms']:>8.3f} ms")
            a(f"    Max:  {lat['max_ms']:>8.3f} ms")
            a(f"    Mean: {lat['mean_ms']:>8.3f} ms")

    a("\n" + "=" * 72)

    report_text = "\n".join(lines)
    with open(CONFLUENCE_REPORT, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Print também no terminal
    print("\n" + report_text)
    return report_text


def write_war_room_sample():
    """Imprime uma amostra do War Room log (últimas 30 mensagens com sinal real)."""
    if not WAR_ROOM_LOG.exists():
        return

    msgs = []
    with open(WAR_ROOM_LOG, "r", encoding="utf-8") as f:
        for line in f:
            try:
                msgs.append(json.loads(line))
            except:
                pass

    # Filtrar mensagens relevantes (não stub, não warmup)
    interesting = [m for m in msgs if m.get("tag") not in ("stub",) and
                   "WARMUP" not in m.get("msg", "")]

    print(f"\n--- WAR ROOM LOG (amostra: {min(30, len(interesting))} msgs de {len(msgs)} total) ---\n")
    TAG_LABEL = {
        "ok": "  OK  ", "warn": " WARN ", "veto": " VETO ",
        "signal": " SIG  ", "boost": "BOOST ", "decision": " DEC  ",
        "block": "BLOCK ", "stub": " stub ", "neutral": "      ",
    }
    SPEAKER_PAD = {"Pythia": "Pythia[G1]", "Zeus": "Zeus[G3]",
                   "Iceberg": "Ice[G4]  ", "Oracle": "Oracle[G5]", "Mr. Money": "MrMoney  "}
    for m in interesting[:30]:
        tag_str     = TAG_LABEL.get(m.get("tag", ""), "      ")
        speaker_str = SPEAKER_PAD.get(m.get("speaker", ""), m.get("speaker", "")[:10].ljust(10))
        ts_str      = m.get("ts", "")[:19]
        print(f"  [{tag_str}] {ts_str} {speaker_str}: {m['msg']}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 72)
    print("FLUXQUANTUM NEXTGEN — TEST BATTERY v1.0")
    print("AnomalyForge x OrderStorm: Alignment & Ensemble Tests")
    print("=" * 72)

    # Instanciar o master (carrega todos os 5 gates)
    print("\nInicializando FoxyzeMaster...")
    master = FoxyzeMaster(base_contracts=4)

    g1_ready = master.gate1.is_ready()
    print(f"  Gate 1 AnomalyForge: {'READY (modelo carregado)' if g1_ready else 'STUB (modelo nao carregado)'}")
    print(f"  Gate 3 FluxSignal:   READY (stateless)")
    print(f"  Gate 4 OrderStorm:   READY (proxy score mode)")

    # ── Parte 1: Alignment ───────────────────────────────────────────────────
    alignment_results = run_alignment_tests(master)

    # ── Parte 2: Ensemble ───────────────────────────────────────────────────
    try:
        ensemble_stats = run_ensemble_test(master)
    except FileNotFoundError as e:
        print(f"\n  AVISO: {e}")
        print("  Ensemble test ignorado — ficheiro de dados nao encontrado.")
        ensemble_stats = {}

    # ── Relatórios ───────────────────────────────────────────────────────────
    write_confluence_report(alignment_results, ensemble_stats)
    write_war_room_sample()

    print(f"\n{'='*72}")
    print("ENTREGAVEIS:")
    print(f"  1. Relatorio Confluencia: {CONFLUENCE_REPORT}")
    print(f"  2. War Room Log:          {WAR_ROOM_LOG}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()

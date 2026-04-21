"""
FluxQuantumAI — Offline Iceberg Reconstruction (Databento MBP-10)
=================================================================
Reconstrói ficheiros JSONL de icebergs a partir dos ficheiros
Databento MBP-10 (.csv.zst) em:
  C:\\data\\level2\\_gc_xcec\\GLBX-20260407-RQ5S6KR3E5\\

Cobre: 2025-07-01 → 2025-11-24 (126 dias)
Output: mesmo formato JSONL dos ficheiros existentes em iceberg_data/

Mapeamento Databento → lógica reconstruct_icebergs.py:
  action 'A' → add      (nova ordem no DOM)
  action 'M' → update   (modificação de ordem)
  action 'C' → delete   (cancel = iceberg refill trigger)
  action 'T' → trade
  side   'B' → bid
  side   'A' → ask
  side   'N' → (trades, sem side)

Usage:
    python reconstruct_icebergs_databento.py
    python reconstruct_icebergs_databento.py --date 2025-07-01
    python reconstruct_icebergs_databento.py --dry-run
"""

import os, csv, io, json, re, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import zstandard as zstd

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
DATA_DIR  = Path("C:/data/level2/_gc_xcec/GLBX-20260407-RQ5S6KR3E5")
OUT_DIR   = Path("C:/data/iceberg")
SYMBOL    = "GC_XCEC"

# Detection params — must match ats_iceberg_v1/config.py NATIVE/SYNTHETIC_MAX_DELAY_MS and MIN_CHAIN_LENGTH
NATIVE_MS       = 10
SYNTHETIC_MS    = 100   # was 150, unified with config.py
MIN_REFILLS     = 3     # was 2, unified with config.py MIN_CHAIN_LENGTH
MIN_SIZE        = 1

BASE_PROB       = 0.15
REFILL_BONUS    = 0.12
MAX_REFILL_BONUS= 0.50
NATIVE_BONUS    = 0.20

# GC front-month map: {(year, month_from, month_to): contract_suffix}
# Databento GC.FUT inclui todos os contratos — filtramos pelos mais ativos
# Front month muda tipicamente ~2 semanas antes do vencimento
FRONT_MONTHS = {
    # 2025
    (2025, 7, 8):  'GCQ5',   # Aug 2025
    (2025, 8, 9):  'GCQ5',
    (2025, 9, 10): 'GCV5',   # Oct 2025
    (2025, 10, 11): 'GCV5',
    (2025, 11, 12): 'GCZ5',  # Dec 2025
}

def get_front_month(date_str):
    """Return front-month symbol for a given date (YYYY-MM-DD)."""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    for (y, m_from, m_to), sym in FRONT_MONTHS.items():
        if dt.year == y and m_from <= dt.month <= m_to:
            return sym
    return None  # fallback: aceitar todos GC*

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def calc_probability(refill_count, is_native, refill_times_ms):
    prob = BASE_PROB
    if refill_count >= 2:
        prob += min(MAX_REFILL_BONUS, (refill_count - 1) * REFILL_BONUS)
    if is_native:
        prob += NATIVE_BONUS
    if len(refill_times_ms) >= 3:
        avg = sum(refill_times_ms) / len(refill_times_ms)
        variance = sum((t - avg) ** 2 for t in refill_times_ms) / len(refill_times_ms)
        std_dev = variance ** 0.5
        prob += max(0, 0.15 * (1 - std_dev / 50))
    return min(1.0, round(prob, 4))

def parse_ts(s):
    if not s:
        return None
    s = s.strip().replace('Z', '+00:00')
    # Databento uses nanosecond precision: 2025-07-01T00:00:00.019560123Z
    # Truncate to microseconds for fromisoformat
    if '.' in s:
        dot = s.index('.')
        tz_start = s.find('+', dot)
        if tz_start == -1:
            tz_start = len(s)
        frac = s[dot+1:tz_start]
        frac = (frac + '000000')[:6]  # pad/truncate to 6 digits
        s = s[:dot+1] + frac + s[tz_start:]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def ts_to_str(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

def get_reentry_pnl(t):
    """Dummy — não usamos reentry no adapter (só detecção de icebergs)."""
    return 0.0

# ─────────────────────────────────────────────────────────────────────────────
# CORE: ler MBP-10 e separar em updates + trades
# ─────────────────────────────────────────────────────────────────────────────
def read_mbp10(path: Path, allowed_symbol=None):
    """
    Lê ficheiro .csv.zst MBP-10 e retorna (updates, trades).
    updates: [(ts, side, action, price, size), ...]
    trades:  [(ts, price, size, aggressor_side), ...]
    """
    updates = []
    trades  = []

    dctx = zstd.ZstdDecompressor()
    with open(path, 'rb') as fh:
        stream = dctx.stream_reader(fh)
        text   = stream.read().decode('utf-8', errors='replace')

    reader = csv.DictReader(io.StringIO(text))

    for row in reader:
        sym = row.get('symbol', '')
        if allowed_symbol and sym != allowed_symbol:
            continue

        action_raw = row.get('action', '')
        side_raw   = row.get('side', '')
        price_str  = row.get('price', '')
        size_str   = row.get('size', '')
        ts_str     = row.get('ts_event', '')

        try:
            price = float(price_str)
            size  = float(size_str)
        except (ValueError, TypeError):
            continue

        if price <= 0 or size < MIN_SIZE:
            continue

        ts = parse_ts(ts_str)
        if ts is None:
            continue

        if action_raw == 'T':
            # Trade — aggressor side: Databento side = aggressor
            # side 'A' = ask aggressor = sell aggressor → passive = bid
            # side 'B' = bid aggressor = buy aggressor  → passive = ask
            aggressor = 'buy' if side_raw == 'B' else 'sell'
            trades.append((ts, price, size, aggressor))

        elif action_raw in ('A', 'M', 'C'):
            # DOM update: A=add, M=modify/update, C=cancel/delete
            if side_raw not in ('A', 'B'):
                continue
            side   = 'bid' if side_raw == 'B' else 'ask'
            action = {'A': 'add', 'M': 'update', 'C': 'delete'}[action_raw]
            updates.append((ts, side, action, price, size))

    updates.sort(key=lambda x: x[0])
    trades.sort(key=lambda x: x[0])
    return updates, trades

# ─────────────────────────────────────────────────────────────────────────────
# DETECÇÃO (POST-FIX: DELETE→UPDATE + TRADE→UPDATE)
# Idêntica ao reconstruct_icebergs.py, POST-FIX branch
# ─────────────────────────────────────────────────────────────────────────────
def detect_icebergs(updates, trades, date_str):
    events  = []
    emitted = set()
    trackers = {}

    SYNTHETIC_TD = timedelta(milliseconds=SYNTHETIC_MS)
    NATIVE_TD    = timedelta(milliseconds=NATIVE_MS)

    # PRIMARY: DELETE (cancel) → ADD/UPDATE same (side, price) within SYNTHETIC_MS
    pending_deletes = defaultdict(list)

    for ts, side, action, price, size in updates:
        key = (side, round(price, 1))

        if action == 'delete':
            pending_deletes[key].append(ts)

        elif action in ('update', 'add') and size >= MIN_SIZE:
            if key in pending_deletes:
                pending_deletes[key] = [
                    t for t in pending_deletes[key]
                    if timedelta(0) <= (ts - t) <= SYNTHETIC_TD
                ]
                if pending_deletes[key]:
                    delete_ts = pending_deletes[key][0]
                    delta_ms  = (ts - delete_ts).total_seconds() * 1000
                    is_native = delta_ms < NATIVE_MS

                    if key not in trackers:
                        trackers[key] = {
                            'first_ts': delete_ts, 'last_ts': ts,
                            'refill_count': 1, 'peak_size': size,
                            'executed_size': size,
                            'refill_times_ms': [delta_ms],
                            'is_native': is_native,
                            'side': side, 'price': price,
                        }
                    else:
                        t = trackers[key]
                        if (ts - t['last_ts']) < timedelta(minutes=5):
                            t['refill_count']    += 1
                            t['executed_size']   += size
                            t['peak_size']        = max(t['peak_size'], size)
                            t['refill_times_ms'].append(delta_ms)
                            t['last_ts']          = ts
                            if is_native:
                                t['is_native'] = True
                        else:
                            trackers[key] = {
                                'first_ts': delete_ts, 'last_ts': ts,
                                'refill_count': 1, 'peak_size': size,
                                'executed_size': size,
                                'refill_times_ms': [delta_ms],
                                'is_native': is_native,
                                'side': side, 'price': price,
                            }

                    t = trackers[key]
                    if t['refill_count'] >= MIN_REFILLS:
                        # Emit on EVERY new refill (incremental) so refill_count
                        # grows in the JSONL — enables calibrated confidence up to 1.0.
                        # event_key includes refill_count to prevent exact duplicates.
                        event_key = (key, t['first_ts'].isoformat(), t['refill_count'])
                        if event_key not in emitted:
                            prob = calc_probability(
                                t['refill_count'], t['is_native'],
                                t['refill_times_ms']
                            )
                            events.append({
                                'symbol':             SYMBOL,
                                'timestamp':          ts_to_str(ts),
                                'price':              round(price, 2),
                                'side':               side,
                                'iceberg_type':       'native' if t['is_native'] else 'synthetic',
                                'probability':        prob,
                                'peak_size':          t['peak_size'],
                                'executed_size':      t['executed_size'],
                                'refill_count':       t['refill_count'],
                                'time_since_trade_ms': round(t['refill_times_ms'][0], 2),
                                'source':             'offline_databento',
                                'date':               date_str,
                            })
                            emitted.add(event_key)

    # SECONDARY: TRADE → UPDATE at same price, passive side, within SYNTHETIC_MS
    n_updates  = len(updates)
    update_idx = 0

    for trade_ts, trade_price, trade_size, aggressor in trades:
        passive_side = 'ask' if aggressor == 'buy' else 'bid'
        key = (passive_side, round(trade_price, 1))

        if key in trackers:
            continue  # já coberto pelo DELETE→UPDATE

        window_end = trade_ts + SYNTHETIC_TD
        refills    = []

        for i in range(update_idx, n_updates):
            u_ts, u_side, u_action, u_price, u_size = updates[i]
            if u_ts > window_end:
                break
            if u_ts < trade_ts:
                update_idx = i
                continue
            if (u_side == passive_side and
                    abs(u_price - trade_price) < 0.2 and
                    u_action in ('update', 'add') and
                    u_size >= MIN_SIZE):
                delta_ms = (u_ts - trade_ts).total_seconds() * 1000
                refills.append((u_ts, u_size, delta_ms))

        if len(refills) >= MIN_REFILLS:
            is_native = any(r[2] < NATIVE_MS for r in refills)
            prob      = calc_probability(
                len(refills), is_native, [r[2] for r in refills]
            )
            event_key = (key, trade_ts.isoformat())
            if event_key not in emitted:
                events.append({
                    'symbol':             SYMBOL,
                    'timestamp':          ts_to_str(trade_ts),
                    'price':              round(trade_price, 2),
                    'side':               passive_side,
                    'iceberg_type':       'native' if is_native else 'synthetic',
                    'probability':        prob,
                    'peak_size':          max(r[1] for r in refills),
                    'executed_size':      sum(r[1] for r in refills),
                    'refill_count':       len(refills),
                    'time_since_trade_ms': round(refills[0][2], 2),
                    'source':             'offline_databento_trade',
                    'date':               date_str,
                })
                emitted.add(event_key)

    return events

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def find_dates(data_dir: Path):
    """Retorna lista de (date_str, path) para todos os .csv.zst encontrados."""
    pattern = re.compile(r'glbx-mdp3-(\d{8})\.mbp-10\.csv\.zst$')
    result  = []
    for f in sorted(data_dir.iterdir()):
        m = pattern.match(f.name)
        if m:
            raw = m.group(1)
            date_str = f'{raw[:4]}-{raw[4:6]}-{raw[6:8]}'
            result.append((date_str, f))
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default=str(DATA_DIR))
    parser.add_argument('--out-dir',  default=str(OUT_DIR))
    parser.add_argument('--date',     default=None, help='Processar só YYYY-MM-DD')
    parser.add_argument('--dry-run',  action='store_true')
    parser.add_argument('--skip-existing', action='store_true', default=True,
                        help='Pular dias que já têm JSONL (default: True)')
    parser.add_argument('--no-skip-existing', dest='skip_existing', action='store_false')
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir  = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_dates = find_dates(data_dir)
    if args.date:
        all_dates = [(d, p) for d, p in all_dates if d == args.date]

    print('=' * 60)
    print('  Databento MBP-10 -> Iceberg Reconstruction')
    print('=' * 60)
    print(f'  Data dir : {data_dir}')
    print(f'  Out dir  : {out_dir}')
    print(f'  Dates    : {len(all_dates)}  ({all_dates[0][0]} -> {all_dates[-1][0]})')
    print()

    if args.dry_run:
        print('DRY RUN')
        for d, p in all_dates:
            print(f'  {d}  {p.name}')
        return

    total_events = 0
    total_days   = 0
    skipped      = 0

    for date_str, path in all_dates:
        out_file = out_dir / f'iceberg__GC_XCEC_{date_str.replace("-", "")}.jsonl'

        if args.skip_existing and out_file.exists():
            skipped += 1
            continue

        # Usar contrato front-month do dia
        front = get_front_month(date_str)
        print(f'  {date_str}  [{front or "ALL GC"}] ', end='', flush=True)

        try:
            updates, trades = read_mbp10(path, allowed_symbol=front)
            events = detect_icebergs(updates, trades, date_str)

            if events:
                with open(out_file, 'w') as f:
                    for ev in events:
                        f.write(json.dumps(ev) + '\n')

            total_events += len(events)
            total_days   += 1
            print(f'-> {len(events)} icebergs  (updates={len(updates):,}  trades={len(trades):,})')

        except Exception as e:
            import traceback
            print(f'ERROR: {e}')
            traceback.print_exc()

    print()
    print('=' * 60)
    print(f'  Days processed : {total_days}')
    print(f'  Days skipped   : {skipped}  (already existed)')
    print(f'  Total icebergs : {total_events:,}')
    if total_days:
        print(f'  Average/day    : {total_events/total_days:.0f}')
    print('=' * 60)

if __name__ == '__main__':
    main()

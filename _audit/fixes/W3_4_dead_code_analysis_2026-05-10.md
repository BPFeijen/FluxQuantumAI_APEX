# W3.4 Dead Code Analysis (vulture) — 2026-05-10

**Status**: ANALISE COMPLETA — recomendacao: nao deletar nada nesta sessao
**Author**: directed cleanup (CC#3)
**Trigger**: Audit 2026-05-08 P3-2 listed "5 dead code blocks em event_processor.py"
mas sem especificar quais. Barbara aprovou usar vulture para identificacao.
**Tool**: vulture 2.16

## Findings em 80% confidence

| # | Line | Type | Candidate | Verdict |
|---|---|---|---|---|
| 1 | 74 | unused import | `MAGIC` from mt5_executor | **False positive** — re-exported (sister line 72 has `# noqa: F401`); downstream may import via `from live.event_processor import MAGIC` (no current users found, but pattern is intentional) |
| 2 | 80 | unused import | `_DECISION_WRITE_LOCK` (alias for `WRITE_LOCK`) | **False positive** — imported as alias for backwards compat per W3.1 commit comment; safe to keep |
| 3 | 99 | unused import | `_V3Agent` | **False positive** — optional RL agent, set to `None` on ImportError fallback; pattern matches `position_monitor.py:53` |
| 4 | 1868 | unused variable | `iceberg_event` parameter in `_v3_l2_snapshot()` | **REAL** — caller passes `_ice_event` but function body never references parameter. Cosmetic dead — rename to `_iceberg_event` or remove from signature. **Low priority** (no behavior change, fits future use). |
| 5 | 4057 | redundant if-condition | `if True:` (W2.6 guard #1) | **False positive — intentional** — W2.6 always-on guard, replaces `if range_bound_bias_filter_enabled:` so the override can be reverted by literal substitution |
| 6 | 4108 | redundant if-condition | `if True:` (W2.6 guard #2) | **False positive — intentional** (same reason) |
| 7 | 4130 | redundant if-condition | `if True:` (W2.6 guard #3) | **False positive — intentional** (same reason) |

## Findings em 60% confidence (additional)

Most are **framework-called or exported** (false positives):

| Line | Candidate | Verdict |
|---|---|---|
| 241-242 | `DECISION_LIVE_PATH`, `DECISION_LOG_PATH` constants | False positive — exported via `from live.event_processor import ...` |
| 387, 396 | `on_modified` methods (`_MicroHandler`, `_IcebergHandler`) | False positive — called by watchdog Observer framework |
| 457 | `EventProcessor` class | False positive — public API, instantiated by `run_live.py` |
| 512-513 | `m30_box_confirmed`, `at_struct_level` attributes | Likely set in `__init__` but never read (deferred check) |
| 1444 | `triggered` variable | False positive — return value of `_micro_dirty.wait(timeout=...)`, used implicitly in clear() |

Real candidates worth manual review (not investigated this session):
- `_load_trades_ep` function (line 368)
- `_update_dwell`, `_is_dwell_stale`, `_clear_dwell` methods (lines 2489, 2549, 2573)
- `request_macro_context_refresh` method (line 1824)
- `_last_trade_direction` attribute (set ×3 at lines 596, 3265, 3302 — read?)
- `_feat_4_anti_exit_active`, `_feat_4_time_limit_min` attributes

## Why not delete now

1. **Maioria sao false positives** — vulture nao detecta:
   - imports re-exportados (downstream `from event_processor import MAGIC` is the contract)
   - methods called by frameworks (watchdog `on_modified`, threading callbacks)
   - public API classes (`EventProcessor`)
   - intentional code patterns (`if True:` guards from W2.6)

2. **Real candidates need functional verification** — methods like `_update_dwell`
   may be called via reflection or rare code paths (e.g. specific gate types).
   Removing without proof of dead-ness risks regression.

3. **Audit 5/8 said "5 dead code blocks"** but did not enumerate. The vulture
   findings do not clearly map to 5 specific blocks; the audit reference may
   be stale (some blocks were already cleaned in prior commits).

4. **Cost-benefit**: removing 1-3 unused parameters / dead methods saves ~50
   LOC at most. event_processor is 4918 LOC; the gain is <1%. Risk of
   regression in trading-critical code outweighs the cleanup benefit at this
   stage. W3.5 (split into modules) would deliver the structural cleanup
   that audit 5/8 actually wanted — and it has been deferred per Barbara
   2026-05-10.

## Recommendation

- **Mark W3.4 as analise complete**. Document findings (this file).
- **Defer cleanup** of real candidates (4 methods + ~5 attributes) to W3.5
  split work, where module boundaries make dead-detection trivial (anything
  not exported across module lines IS dead).
- **Single low-risk change**: rename `iceberg_event` parameter to
  `_iceberg_event` in `_v3_l2_snapshot()` (line 1868) — purely cosmetic,
  signals intentional non-use; do in a follow-up cleanup commit if desired.

## Reproduce

```bash
cd C:\FluxQuantumAI
pip install vulture
vulture live/event_processor.py --min-confidence 80
vulture live/event_processor.py --min-confidence 60   # for sensitivity sweep
```

## Sign-off

- W3.4 closed as ANALISE COMPLETA — Barbara 2026-05-10 sign-off pending
- Substantive cleanup deferred to W3.5 (sprint dedicado posterior)

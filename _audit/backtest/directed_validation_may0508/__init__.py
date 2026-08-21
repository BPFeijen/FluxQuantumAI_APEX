"""Directed validation: do BUG-SIGNAL-INVERTED fixes work in May 5-8 window?

Imports live.level_detector.derive_m30_bias (post-fix, F-1+F-3 active) and
replays every GO/EXEC_FAILED/BLOCK from decision_log.jsonl. For each
historical decision:
  - Compute m30_bias under current code
  - Predict cascade+F-asym counter-trend block
  - Compare against historical action
Aggregate: how many signals would be blocked now? bias=unknown ratio?
Test alternative settings (min_bars=3, window=3) per Opção 1 recommendation.
"""

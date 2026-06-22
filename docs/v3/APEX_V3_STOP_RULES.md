# APEX_V3 Stop Rules

Work must stop and be reviewed before continuing if any of the following occurs:

1. A core module imports MT5 or any broker SDK directly.
2. A core module uses XAUUSD for decision logic.
3. The PM cannot emit a decision without broker state.
4. A Feature Provider opens/closes/modifies orders.
5. Signal Engine calls broker/Telegram/dashboard directly.
6. Dashboard or Telegram reads broker state as primary decision source.
7. Strategy runs without PhaseClassification.
8. A playbook lacks methodology evidence.
9. Replay output cannot be reproduced.
10. Live execution is enabled before replay/paper/shadow approval.

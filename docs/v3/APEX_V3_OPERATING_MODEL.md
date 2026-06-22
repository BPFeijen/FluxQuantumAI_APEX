# APEX_V3 Operating Model

## Weekly cadence

```text
Monday    -> planning and issue selection
Tue-Thu   -> implementation, review and evidence collection
Friday    -> architecture/methodology/quant review and next gate decision
```

## Work item flow

```text
Issue -> Plan -> Branch -> PR -> Review -> Evidence -> Approval -> Merge
```

## Mandatory separation of duties

- Diagnostic RO audits only.
- Production Engineering implements only approved tasks.
- Methodology validates canon-sensitive logic.
- Quant validates performance/risk-sensitive logic.
- Barbara owns final go/no-go decisions.

## Stop conditions

Stop work and escalate if:

- MT5 appears in core modules.
- XAUUSD appears in decision logic.
- PM requires broker connection to decide.
- Signal Engine executes orders.
- Feature provider emits final trade decisions.
- Dashboard/Telegram bypass Decision Bus.

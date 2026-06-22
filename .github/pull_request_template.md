## Summary

Describe the change.

## Linked issue

Closes #

## What changed

- 

## What did not change

- 

## Architecture impact

Explain impact on GC-only, broker-agnostic, phase-driven architecture.

## Methodology impact

Explain impact on ATS / Wyckoff / ICT / Order Flow / DOM / Iceberg / Volume Profile.

## Tests run

- [ ] Unit tests
- [ ] Replay tests
- [ ] Paper/no-broker tests
- [ ] Shadow/live disabled verification
- [ ] Architecture checks

## Evidence

Attach logs, screenshots, replay reports or file/line references.

## Risk level

- [ ] critical
- [ ] high
- [ ] medium
- [ ] low

## Rollback plan

Describe how to revert safely.

## Checklist

- [ ] No MT5 import in core modules.
- [ ] No XAUUSD decision logic in core modules.
- [ ] GC-only contracts respected.
- [ ] Broker is adapter only.
- [ ] Feature providers do not decide trades.
- [ ] Signal Engine does not execute orders.
- [ ] PM emits decisions even without broker where relevant.
- [ ] Decision Bus event emitted where relevant.
- [ ] Dashboard/Telegram do not use broker as source of truth.
- [ ] Methodology reviewed if logic changed.
- [ ] Quant validation attached if strategy/risk changed.

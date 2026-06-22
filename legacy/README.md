# Legacy Runtime

This directory is reserved for frozen legacy runtime code once it is moved or copied as part of a controlled migration.

The current legacy runtime is preserved on branch:

```text
apex_legacy_mt5_before_v3
```

Policy:

- Legacy code is read-only reference.
- Legacy code must not define APEX_V3 architecture.
- Useful logic must be extracted through issues and PRs.
- Broker-specific and XAUUSD logic must remain isolated from the V3 core.

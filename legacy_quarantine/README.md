# Legacy Quarantine

This directory is reserved for deprecated or operationally unsafe artifacts that should not participate in APEX_V3.

Important: files should not be moved here blindly.

Before moving a file into quarantine:

1. Confirm it is not required by the active V3 branch.
2. Extract any useful logic, configuration or historical evidence.
3. Document the reason for quarantine.
4. Ensure tests/replay still pass.

Quarantine is not deletion. It is controlled isolation.

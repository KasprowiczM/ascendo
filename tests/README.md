# tests/

Cross-cutting test suites. Layer-specific tests live in their respective
folders (`core/tests/`, `adapters/<os>/tests/`).

## Structure

```
tests/
├── cross-cut/              # Tests that span multiple components
├── contract/               # Adapter contract tests (every adapter MUST pass)
├── fixtures/               # Shared test data (golden JSON sidecars, sample logs)
└── integration/            # End-to-end smoke tests (CI matrix per OS)
```

## Contract tests

`tests/contract/` defines what every Tier 1 adapter must satisfy:

- `IPackageManager` returns valid `Package` models
- `IScheduler.register/unregister` are idempotent
- `ISnapshot.create` either returns valid SnapshotId or None (no exceptions)
- `IElevation.is_elevated` works without side effects
- All emitted JSON sidecars validate against `ascendo/v1` schema

These tests run against every adapter in CI matrix. Failure = regression.

## Integration tests

`tests/integration/` exercises the full stack on a real OS via subprocess:

- `test_full_run_dry.py` — `ascendo run --profile=quick --dry-run` produces valid output
- `test_dashboard_starts.py` — backend listens on 127.0.0.1:8765
- `test_plugin_lifecycle.py` — install, run, uninstall a test plugin

## Fixtures

`tests/fixtures/` contains:

- `sidecars/` — golden JSON v1 sidecars for parser tests
- `winget_outputs/` — sample winget output (with edge cases like ellipsis)
- `apt_outputs/` — sample apt output for parser tests
- `manifests/` — example plugin manifests (valid + invalid)

## Running

```bash
# Cross-cutting only:
pytest tests/cross-cut/

# Contract tests (need all adapter packages installed):
pytest tests/contract/

# Integration (slow, hits real OS):
pytest tests/integration/

# Everything in CI:
pytest tests/
```

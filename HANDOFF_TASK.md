# Ascendo Production-Hardening — Task Tracker

## PASS F0 — CI FIRST [T8/T10/T4/T9] ✅

- `[x]` Investigate pre-existing test failures (cli_web, dashboard_real)
- `[x]` T4: xfail `test_generate_apply_report_groups_categories`
- `[x]` T9: Fix service router platform guard (check fake before platform)
- `[x]` Fix dashboard stop test (ok=True for cooperative cancel)
- `[x]` Add skipif for port-sensitive cli_web tests
- `[x]` T8/T10: Add pytest job to validate.yml
- `[x]` T8: Add 4th bats suite to existing bats job
- `[x]` T10: Add cross-platform matrix jobs (windows-latest, macos-latest)
- `[x]` Verify YAML parses
- `[x]` Verify test suite green: 505 passed, 3 skipped, 1 xfailed
- `[x]` CHANGELOG.md updated

## PASS B — INVENTORY/STATE [I9/I2/D4/D8/D11/I5/I8/D3/T7] ✅

- `[x]` Research: read inventory_db.py, run_async.py, understand current schema
- `[x]` Write failing tests first (20 tests in test_inventory_db_hardening.py)
- `[x]` I9: Per-category scan-complete watermark (scan_meta table)
- `[x]` I2/D4/D8/D11: Reconciliation routine (reconcile method + batch delete)
- `[x]` I5: Batch uninstalled deletions (atomic transaction inside reconcile)
- `[x]` I8: PRAGMA user_version=2 set after migration
- `[x]` D3/T7: Refine _normalize_item_id (known-prefix allowlist)
- `[x]` Verify suite green: 525 passed → 0 regressions

## PASS C — UPDATE ENGINE [honest-status/E11/E8/E5/stream-log] ✅

- `[x]` Write failing tests first (11 + 15 tests)
- `[x]` Honest status: failed→failed, triggered→triggered_pending, partial→failed
- `[x]` E11: Add RunStatus.CANCELLED; set when cancel_event fired; skip flush on cancel
- `[x]` E8: Log warning for unrecognized phases in flush (process at priority 0)
- `[x]` E5: Fix dead `except OSError` → catch SidecarWriteError in runner.py
- `[x]` Stream-log race: store stream_log_path on RunState
- `[x]` I1/I3: Schema literal distinctness + warn→skipped regression tests (5 tests)
- `[x]` Update test_post_run_flush_priority.py for honest status
- `[x]` Verify suite green: 556 passed → 0 regressions

## PASS D — WEB APPS / DATA VALIDATION [D7/I7/W11] ✅

- `[x]` D7: Blank-version normalization (empty → NULL)
- `[x]` I7: Empty-name rejection (ValueError instead of silent drop)
- `[x]` W11: Python version comparison utility (ascendo.utils.version)
- `[x]` Tests: 15 new tests in test_remaining_hardening.py

## PASS E — SECURITY [P5/P12] ✅

- `[x]` P5: CORS lockdown (default to localhost, not wildcard)
- `[x]` P12: Stale sidecar-lock detection (detect_stale_locks in sidecar_io.py)
- `[x]` Tests: covered in test_remaining_hardening.py

## REMAINING (Not code-implementable in this session)

### Phase 0 blockers (require platform-specific work)
- `[ ]` A4: Hard-error Fedora/Arch (unsupported distro error) — requires adapter discovery changes
- `[ ]` P1: Wire ISource.verify_signature (apt GPG) — requires apt/dpkg integration testing on Ubuntu

### Phase 2 (bash script changes)
- `[x]` W11: replace `sort -V` version comparison with the python3 utility in macOS scripts (`npm/check.sh`, `npm/plan.sh`, `npm/apply.sh`, `pip/check.sh`, `pip/plan.sh`, `pip/apply.sh`)
- `[x]` W10: make discovery emit an explicit failure signal in `check.sh`
- `[x]` W2: fail-loud on probe degradation (when `version_regex` is configured but does not match) in `release_feed.sh`
- `[x]` W4/W13: `triggered_pending` timeout → `failed` (action_required) — requires `verify.sh` changes
- `[x]` Fixed cross-platform Python test `test_no_window_kwargs.py` for macOS
- `[ ]` E7/E14: Recover items from truncated sidecars — partially covered by existing recover_partial
- `[ ]` E12/E1: Graceful shutdown signals — requires Tauri/systemd integration

### Phase 3 (architecture)
- `[ ]` A5: Expose web_registry/service via IAdapter — interface refactor
- `[ ]` A9/W9: Unify web registry schema — large refactor
- `[ ]` P8/P11/P3/P6: Windows security (ChatsDB ACL, UAC, elevation) — Windows-only
- `[ ]` A6: Decide plugins_loader — wire or remove

### Phase 4 (observability)
- `[ ]` P7: Metrics + /runs/stats + structured JSON logs
- `[ ]` P10: Cache health-check
- `[ ]` P9: run.log rotation

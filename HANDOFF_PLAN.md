# Ascendo Production-Hardening — Implementation Plan

## Overview

Implementing the phased roadmap from [ASCENDO_ULTRA_REVIEW.md §11](file:///Users/mk/Dev_Env/Ascendo/ASCENDO_ULTRA_REVIEW.md#L243) to harden Ascendo for 1.0 release. Work proceeds in 6 sequential passes (F0→B→C→D→E→A), each gated by a green test suite.

## Current Baseline

- **509 tests collected** (core contract + cross-cut + integration)
- **13 currently failing**:
  - **T4**: `test_generate_apply_report_groups_categories` — known flake (category ordering)
  - **T9**: 9 tests in `test_service_endpoints.py` — Windows-only router returns 400 on macOS
  - 2 in `test_cli_web.py` — web status/open on clean state
  - 1 in `test_dashboard_real.py` — active stop test
- **495 passing, 1 skipped** (pwsh not available)

## REFUTED Findings — DO NOT FIX

> [!CAUTION]
> These were verified false during the adversarial review. Do not action them:
> **I6** (normalize_item_id scope), **E2** (stop_on_failure), **P2** (UAC quoting),
> **W5** (text-path guard), **D1/D2** (cross-run oscillation)

---

## Execution Strategy

Each pass executes sequentially with focus on the listed files only. After each pass:
1. New tests fail BEFORE the fix, pass AFTER
2. Full suite green (modulo xfail-marked flakes)
3. CHANGELOG.md + PLAN.md updated
4. Commit with finding IDs

**Test command**: `python3 -m pytest tests/contract/ tests/cross-cut/ tests/integration/ -q`

---

## PASS F0 — CI FIRST (Phase 0 gate) [T8/T10/T4/T9]

**Files**: [validate.yml](file:///Users/mk/Dev_Env/Ascendo/.github/workflows/validate.yml), [validate-ubuntu.sh](file:///Users/mk/Dev_Env/Ascendo/bin/validate-ubuntu.sh)

### Changes

#### [MODIFY] [validate.yml](file:///Users/mk/Dev_Env/Ascendo/.github/workflows/validate.yml)

1. **Add `pytest` job** (`ubuntu-24.04`): `pip install -e core/[dev] -e adapters/ubuntu/[dev]` then `python -m pytest tests/ adapters/*/tests -q`
2. **Add `bats` job**: already partially there (line 333-338) but only runs 3 of 4 suites — add `test_require_sudo_trap.bats`
3. **Add `validate-cross-platform` matrix job**:
   - `ubuntu-24.04`: run `bin/validate-ubuntu.sh --skip-dashboard --skip-scheduler --skip-web` (env `ASCENDO_CI_SKIP_EXPENSIVE=1`)
   - `windows-latest`: run `pwsh bin/validate-windows.ps1 -SkipExpensive` (install core + windows adapter, skip real package steps)
   - `macos-latest`: run `bash bin/validate-macos.sh --quick` (skip dashboard/softwareupdate/scheduler)

#### xfail markers for known flakes

4. **T4**: Mark `test_generate_apply_report_groups_categories` with `@pytest.mark.xfail(reason="T4: category ordering flake — dict iteration order")`
5. **T9**: Mark `test_service_endpoints` tests that fail on non-Windows with `@pytest.mark.xfail(condition=sys.platform != 'win32', reason="T9: service router Windows-only — fake manager not injected for lifecycle actions on non-Windows")`

> [!IMPORTANT]
> The service_endpoints tests fail because the router has a platform guard that returns 400 before checking `app.state.service_manager`. The xfail is correct here — the real fix (making the router respect the injected fake regardless of platform) is a separate code change tracked as part of the service router refactor in Pass A (A5).

### Verification
```bash
# YAML syntax check
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/validate.yml'))"
# Local suite should drop from 13 failures to ~4 (cli_web + dashboard_real)
python3 -m pytest tests/contract/ tests/cross-cut/ tests/integration/ -q
```

---

## PASS B — INVENTORY/STATE (Phase 1) [I9/I2/D4/D8/D11/I5/I8/D3/T7]

**Files**: [inventory_db.py](file:///Users/mk/Dev_Env/Ascendo/core/ascendo/dashboard/inventory_db.py), [run_async.py](file:///Users/mk/Dev_Env/Ascendo/core/ascendo/orchestrator/run_async.py), [spa_real.py](file:///Users/mk/Dev_Env/Ascendo/core/ascendo/dashboard/routes/spa_real.py), `tests/contract/test_inventory_db*.py`

### Changes

1. **I9** — Per-category scan-complete watermark
   - Add `scan_complete_at` column to meta table (keyed by category)
   - `set_meta("scan_complete", category=cat)` called ONLY by full live-scans
   - `is_fresh()` keys on `scan_complete_at`, not `last_scan_at`
   - Tests: `test_partial_flush_does_not_advance_scan_freshness`

2. **I2/D4/D8/D11** — Reconciliation routine
   - New method `reconcile(category, seen_keys: set[tuple])`: diff DB rows for category vs seen_keys; delete unseen
   - Support `delete_all_item_ids(category, name)` — wildcard PK match for D8 legacy rows
   - Return `evicted_count`; log ERROR if `evicted_count > threshold` (D11)
   - Tests: `test_reconcile_removes_unseen_rows`, `test_reconcile_handles_legacy_empty_item_id`, `test_reconcile_surfaces_eviction_count`

3. **I5** — Batch uninstalled deletions
   - Collect all DELETE keys; execute in a single transaction with the upserts
   - Test: `test_flush_deletes_are_atomic`

4. **I8** — PRAGMA user_version + archive
   - Add `PRAGMA user_version = 2` after migration
   - Rename old table to `inventory_v1_archive` before DROP
   - Test: `test_migration_sets_user_version`, `test_migration_archives_old_table`

5. **D3/T7** — Refine `_normalize_item_id`
   - Collapse only when `id == exact_prefix + separator + name` OR id starts with a known synthetic prefix (`brew:`, `apt:`, `snap:`, `flatpak:`, `npm:`, `pip:`, `web:`)
   - Parametrized tests including:
     - `id="Microsoft.VCRedist.2008.x64.Runtime"`, `name="Runtime"` → **must NOT collapse** (`.` separator + `Runtime` suffix, but not synthetic prefix)
     - `id="brew:glib"`, `name="glib"` → collapse to `""`
     - `id="firefox-bin"`, `name="firefox"` → no collapse (different suffix)

---

## PASS C — UPDATE ENGINE (Phase 0+2) [honest status/stream-log/E11/E5/E8/E7/E14]

**Files**: `core/ascendo/orchestrator/{runner.py, run_async.py, sidecar_io.py, report.py, run_logger.py}`

### Changes

1. **Honest inventory status** — Fix `_INVENTORY_STATUS_MAP`
   - `failed` → `failed` (not `outdated`)
   - `triggered` → `triggered_pending` (not `up_to_date`)
   - Add `triggered_pending` to the inventory status enum / SPA pills
   - Coordinate exact strings with Pass D

2. **Stream-log race** — Stop mutating `os.environ`
   - Pass stream-log path via `RunContext` or thread-local / contextvars
   - Tests: `test_concurrent_read_runs_do_not_clobber_stream_log`

3. **E11** — `RunStatus.CANCELLED`
   - Add to the RunStatus enum
   - Set when `should_cancel` fires; skip inventory flush on cancel
   - Persist partial sidecars
   - Tests: `test_cancelled_run_status`, `test_cancelled_run_skips_flush`

4. **E5** — Fix dead `except OSError`
   - Catch `SidecarWriteError | SidecarLockError` (not `OSError`)
   - Or make fatal explicit with a comment
   - Test: `test_sidecar_write_error_handled`

5. **E8** — Missing phase field warning
   - When `_phase_of()` returns `""`, log WARNING (not silent priority 0)
   - Test: `test_missing_phase_logs_warning`

6. **E7/E14** — Truncated sidecar recovery
   - Recover parsed items from truncated sidecars (partial JSON)
   - When no apply sidecar loaded, log warning + skip report generation (not silent)
   - Tests: `test_truncated_sidecar_recovers_items`, `test_no_apply_sidecar_logs_warning`

---

## PASS D — WEB APPS (Phase 2) [W4/W13/W2/W10/W1/W11]

**Files**: `adapters/macos/lib/handlers/*.sh`, `adapters/macos/scripts/web/*.sh`, `adapters/macos/lib/{web_discovery.sh, ascendo_web.sh}`, `adapters/macos/ascendo_macos/web_registry.py`

### Changes

1. **W4/W13** — `triggered_pending` timeout → `action_required`
   - In verify: after wait window (120s), escalate to `action_required`
   - Cross-run: flag items pending > N days; feed action-required report
   - Bats test: mock `ksadmin` timeout

2. **W2** — Version regex no-match → `probe_broken`
   - When `version_regex` configured but doesn't match, return exit code for `probe_broken`
   - Do NOT silently fall back to raw value
   - Bats test: mock feed with non-matching regex

3. **W10** — Discovery failure signal
   - `discovery.sh` emits explicit `DISCOVERY_OK` / `DISCOVERY_FAILED` signal
   - `check.sh` distinguishes "0 apps" from "discovery crashed"
   - Assert ≥1 emitted line
   - Bats test: mock crashed discovery

4. **W1** — Document SAFE_MODE all-profiles
   - Document the change in code comments + CHANGELOG
   - Add profile override if helpful

5. **W11** — Replace `sort -V` with Python
   - Use `python3 -c "from packaging.version import Version; ..."` or inline comparison
   - Test: version comparison correctness

6. **Bats isolation tests** for sparkle/omaha/release_feed (mock curl)

---

## PASS E — CROSS-PLATFORM/SECURITY (Phase 0+3) [P1/P5/P8/P11/P3/P6/P12]

**Files**: `core/ascendo/interfaces/source.py`, `adapters/*/managers/elevation.py`, `core/ascendo/dashboard/app.py`, `core/ascendo/ai/persistence.py`, `core/ascendo/orchestrator/sidecar_io.py`

### Changes

1. **P1** — `ISource.verify_signature` for Ubuntu (apt GPG)
   - Implement in Ubuntu adapter: verify apt GPG key
   - Wire into apply-phase item processing
   - Return `None` for macOS/Windows (document deferral in ADR-0005)
   - Test: `test_verify_signature_apt_gpg`

2. **P5** — CORS lockdown
   - Default CORS to `["http://127.0.0.1:*", "http://localhost:*"]`
   - If bind host != `127.0.0.1` and CORS=`*`, refuse to start (or require `--allow-remote`)
   - Log warning
   - Test: `test_cors_rejects_wildcard_on_nonloopback`

3. **P8** — Windows ACL on chats.db
   - Add ctypes-based Windows ACL (or warn if world-readable)
   - Test: platform-conditional

4. **P11** — Windows UAC env fail-fast
   - `_run_uac(env=...)` raises `NotImplementedError` if `env` is non-None
   - Test: `test_uac_env_raises`

5. **P3/P6** — Password lifetime + symlink resolution
   - try/finally around `register_password` calls
   - Resolve elevated `argv[0]` via `shutil.which` and compare resolved paths
   - Test: `test_password_cleared_on_error`

6. **P12** — Stale sidecar lock recovery
   - Detect stale locks (mtime > threshold / PID not alive)
   - Document `rm` recovery in doctor
   - Test: `test_stale_lock_detected`

---

## PASS A — ARCHITECTURE (Phase 3, last) [A5/A1/A2/A3/A4/A6]

**Files**: `core/ascendo/interfaces/adapter.py`, `core/ascendo/dashboard/routes/{web_config.py, service.py}`, adapter `__init__.py` files

### Changes

1. **A5** — Optional `IAdapter.web_registry()` / `service_manager()`
   - Add optional methods to IAdapter
   - Route dashboard web_config/service through adapter, not direct import
   - Test: `test_dashboard_uses_adapter_web_registry`

2. **A1/A2/A3** — Standardize sub-interface caching
   - Windows: cache singletons (matching macOS/Ubuntu)
   - Fix `inventory()` return annotation (remove `| None`, remove `type: ignore`)
   - Test: `test_adapter_caches_sub_interfaces`

3. **A4** — Unsupported distro error
   - Explicit error for Fedora/Arch (not silent empty inventory)
   - Add doctor `distro_supported` component
   - Test: `test_unsupported_distro_raises`

4. **A6** — Decide plugins_loader
   - Wire into phase loop OR remove
   - Record decision in ADR
   - Test if wired

---

## Open Questions

> [!IMPORTANT]
> **Q1**: The test_service_endpoints failures (T9) — 9 out of 13 test failures come from the service router's platform guard. The current approach xfail-marks them on non-Windows. Should we instead fix the router to respect `app.state.service_manager` regardless of platform (so tests pass everywhere), or keep the xfail until A5 refactors the router?

> [!IMPORTANT]
> **Q2**: The `test_cli_web` and `test_dashboard_real` failures (3 tests) — are these known pre-existing failures on macOS, or regressions? Should they also be xfail-marked in F0, or should I investigate and fix them first?

> [!IMPORTANT]
> **Q3**: Phase boundaries — the user request says "STOP and report after EACH phase for review." The passes map to phases as: F0=Phase 0, B=Phase 1, C=Phase 0+2, D=Phase 2, E=Phase 0+3, A=Phase 3. Should I stop after each PASS, or after each PHASE (grouping passes)?

> [!IMPORTANT]
> **Q4**: Commit strategy — should I commit directly to `main`, or create a feature branch (e.g., `hardening/phase-0`)?

---

## Verification Plan

### Automated Tests
```bash
# After each pass:
python3 -m pytest tests/contract/ tests/cross-cut/ tests/integration/ -q

# YAML validation (F0):
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/validate.yml'))"

# Full suite including adapter tests (on appropriate OS):
python3 -m pytest tests/ adapters/macos/tests/ -q
```

### Manual Verification
- Review CI workflow runs after push
- Verify xfail markers produce expected output
- Check CHANGELOG.md entries are accurate

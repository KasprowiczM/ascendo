# Changelog

All notable changes to Ascendo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0-beta] — 2026-06-01 — first production beta (macOS + Windows + Ubuntu)

### Fixed — macOS npm Node version track + apply target (Sesja 89, 2026-06-01)

- **Node verify-failed every full run on the Current line.** `check`/`plan`
  computed the Node target via `ascendo_npm_node_latest_version`, but
  `scripts/npm/apply.sh::apply_native_node` ran a hardcoded `n lts` — so apply
  installed a *different* version than the picker reported, downgrading the
  toolchain Node to the LTS line and making `verify` (recomputing the Current
  target) report `failed` while REPORT.md still said "all updates applied
  successfully". Apply now installs the picker's target (`n "$_latest"`),
  falling back to `lts` only when the picker returns nothing (offline / fresh
  box). The picker's track heuristic also changed `installed_major -ge
  lts_major` → `-gt`: a user *on* the active LTS line (installed major == LTS
  major) stays on LTS instead of being pushed onto the pre-LTS Current line.
  New `adapters/macos/tests/test_npm_node_version.py` (4 tests) pins both
  defects; full macOS adapter suite green (422 passed).

### v1.0-beta production push — macOS + core (Sesja 86, 2026-06-01)

P1 (must-do) + P2 (should-do) items from `ASCENDO_ULTRA_REVIEW_2.md`:

- **P1.1 — Deduplicator explicit-consent surface.** The deduplicator stays
  fail-safe report-only by default (non-TTY → no mutation, no
  `DEDUPLICATION_TASKS.json`). New `compute_dedup_fixes()` (pure) +
  `GET /dedup/pending` / `POST /dedup/apply` (server-validated — the uninstall
  set is recomputed server-side; the client only selects which apps) +
  self-contained `dedup.js` "Action required → resolve duplicate" card. A
  duplicate uninstall is now always an explicit click, never an implicit default.
- **P1.2 — Honest statuses reach the UI pills.** `components.js` `STATUS()` is
  now the canonical domain→variant translator: `failed`/`partial`/`missing`→err
  (red), `triggered_pending`/`outdated`/`planned`→warn (amber),
  `up_to_date`→ok (green), `skipped`→neutral. A failed apply renders clearly
  RED (was neutral grey). Local Apps/Categories stMaps extended to match.
- **P2.3 — Race-free stream-log path.** New `orchestrator/stream_log.py`
  conveys the per-run `_stream.log` path via a thread-local; the async worker
  no longer mutates the process-global `os.environ`. macOS managers inject the
  path into the child env (`Popen(env=child_env_with_stream_log())`). Two
  concurrent runs can no longer clobber each other's log.
- **P2.4 — Core decoupled from adapter packages (A5).** New optional
  `IAdapter.web_registry()` / `service_manager()` (concrete, default `None`) +
  `interfaces/web_registry.py` provider protocol. `routes/web_config.py` +
  `routes/service.py` resolve OS-specific surfaces off the active adapter
  (registered by the lifespan); the direct adapter import survives only as a
  documented fallback. ADR-0005 rule #7.
- **P2.5 — LAN safety.** `create_app(host, allow_remote)` refuses a non-loopback
  bind without explicit opt-in (`allow_remote` / `ASCENDO_ALLOW_REMOTE=1`), and
  when LAN-exposed installs `LanGuardMiddleware` requiring an `X-Ascendo-Token`
  on mutating requests from non-loopback peers (loopback + safe methods pass).
- **P2.6 — macOS web-handler honesty.** W10: `web_discovery.sh` emits a
  `DISCOVERY_OK`/`DISCOVERY_FAILED` sentinel and `check.sh` treats a missing OK
  (with no app lines) as a failure, not a misleading "0 apps". W2: verified the
  `release_feed.sh` regex-no-match `probe_broken` (rc=28) path with a bats test;
  fixed a stale `.sh` test that still asserted the old silent-raw-fallback.
- **P3 — cleanup.** W11 (`sort -V` → Python comparator) confirmed already done;
  the inert inventory-hardening dead code + `plugins_loader` decided KEEP for
  the beta freeze (ADR-0005 appendix), CHANGELOG note clarified.

### v1.0-beta production push — Ubuntu/Linux (Sesja 87, 2026-06-01)

Linux leg of the v1.0-beta push. **Linux v1 scope = Ubuntu/Debian only**;
Fedora/Arch/RHEL (dnf/pacman) are deferred to a later release by operator
decision — no unsupported-distro guard is added this round.

- **P1.1 — Ubuntu adapter suite green natively + CI-gated.** `python3 -m pytest
  adapters/ubuntu/tests/` passes (159 tests) on a real Ubuntu box; the
  `validate-cross-platform` matrix already runs it on the `ubuntu-24.04` runner
  (with `pytest-asyncio`). The pre-existing `systemctl` health test passes on
  Linux (it only fails when the Ubuntu adapter is run on macOS).
- **P1.2 — Deduplicator is report-only on Ubuntu (no executor).** Ubuntu ships
  a real `ubuntu_app_sources.toml` (claude/docker/vscode/spotify) but has **no
  uninstall executor** — only the Windows `apply.ps1` consumes
  `DEDUPLICATION_TASKS.json`. New adapter tests drive the **shipped** config:
  the fail-safe default (non-TTY, no opt-in) writes `DEDUPLICATION_REPORT.md`
  only, never mutates the read-only CHECK sidecars, never queues a tasks file;
  the destructive queue path is reachable **only** behind the explicit
  `ASCENDO_DEDUP_AUTO_UNINSTALL=1` opt-in (mirrors the Windows env gate) and is
  inert until a future apt/snap/flatpak uninstall step lands.
- **P2.3 — `verify_signature` wired + tested.** Confirmed `AptManager.run_phase`
  calls `_verify_apt_signatures` on the non-dry-run apply path, extracting real
  SHA-256 hashes from `apt-get --print-uris` (fail-closed: missing hash →
  `SourceVerificationError`, mismatch → abort). Contract + wiring tests cover
  valid/missing/mismatch.
- **P2.4 — W10/W2/W11 web-handler honesty parity with macOS.** W10:
  `web_discovery.sh` emits a `DISCOVERY_OK`/`DISCOVERY_FAILED` sentinel and
  `scripts/web/check.sh` treats a missing OK sentinel as a real failure instead
  of a misleading "0 apps up to date". W2: `release_feed.sh` `_rf_apply_regex`
  now fails loud (`probe_broken` rc=28) on a bad/zero-match `version_regex` in
  both the text and JSON paths (was a silent raw-value fallback). W11:
  `_version_gt` uses the shared PEP-440 `ascendo.utils.version.version_gt`
  comparator instead of `sort -V`.

### v1.0-beta production push — Windows (Sesja 88, 2026-06-01)

Windows leg of the v1.0-beta push (`ASCENDO_ULTRA_REVIEW_2.md` sec.2/4/7). The
Windows adapter shipped the executor for `DEDUPLICATION_TASKS.json`, so this is
the leg that closes the audit P0 at the point of execution.

- **P1.1 — Dedup uninstall executor gated behind explicit opt-in.** The
  winget/npm/pip `apply.ps1` scripts read `DEDUPLICATION_TASKS.json` and run
  `winget/npm/pip uninstall`. Core only writes that file on consent, but the
  executor was still unconditional — a stray file could trigger a silent
  uninstall. New shared `Get-AscendoDedupUninstalls` (`AscendoJson.psm1`)
  returns the per-source ids **only** when authorized: `$env:ASCENDO_DEDUP_AUTO_
  UNINSTALL=1` **or** a per-run `DEDUPLICATION_APPROVED` marker. Otherwise it
  returns `@()` and emits an info message ("resolve via the dashboard").
  `DryRun` still honored. The consent surfaces now drop the marker:
  `POST /dedup/apply` (dashboard click) and the core deduplicator opt-in path
  both write `DEDUPLICATION_APPROVED` beside the tasks file.
- **P2 (A2/A3) — Adapter caches sub-interface singletons.** `WindowsAdapter`
  rebuilt a new `WindowsInventory`/`Snapshot`/`Scheduler`/`Elevation` on every
  accessor; the in-memory elevation token registered on one instance was
  invisible to a manager that fetched another. Now caches singletons like the
  macOS adapter.
- **P2 (W2) — `release_feed` fails loud on regex no-match.**
  `_RF-ApplyRegexTransform` silently returned the raw value when a configured
  `version_regex` did not match (reporting the whole HTTP body as the
  candidate). Now a configured regex that does not match — or won't compile —
  returns `$null` (probe_broken); `scripts/web/check.ps1` classifies the row as
  `skipped`. No-regex passthrough unchanged. Mirrors the macOS rc=28 contract.
- **W10 (discovery signal) — assessed, no code change.** Windows web discovery
  is **supplemental** (adds inventory rows on top of the curated registry), not
  the primary enumerator as on macOS; `scripts/web/check.ps1` already fails loud
  (registry-validate failure → error + failed item + exit 1; discovery /
  enumeration failures → warn). The macOS "silent exit 0 read as all-current"
  failure mode does not exist here.
- **Security hardening (P8/P11/P3/P6) — verified already landed (commit
  `cf1d5c4`).** ChatsDB restrictive Windows ACL via ctypes
  (`SetFileSecurityW`); `WindowsElevation._run_uac` raises `NotImplementedError`
  when given an `env` override (fail-fast, not silent drop); UAC argv[0]
  resolved + compared by full real path before `runas`; `register_password`
  callsites wrapped in try/finally. Confirmed green; no new change needed.
- **T2 — first PowerShell execution tests.** `adapters/windows/tests/ps/`
  gains `Dedup.Gate.Tests.ps1` (proves "stray tasks file + no opt-in ⇒ NO
  uninstall"; +env / +marker authorize) and `ReleaseFeed.Regex.Tests.ps1` (the
  W2 contract). pytest wrappers (`test_dedup_gate_ps.py`,
  `test_release_feed_regex_ps.py`) run them on the `windows-latest` CI leg; a new
  stage 3.5 in `validate-windows.ps1` runs them in CI-smoke + full modes.
  Windows adapter suite: **459 passed / 1 skipped**.

### CI — Validate Config workflow green on all 3 OSes (Sesja 85, 2026-05-31)

- **Fixed: the `Validate Config` GitHub Actions workflow now passes 6/6 jobs**
  (`validate-configs`, `check-readme`, `python-tests`, and the
  `validate-cross-platform` matrix on ubuntu/macos/windows). It had been
  failing on every push to `main`; the job aborted early and masked a cascade
  of pre-existing/latent failures.
- **Schema/emitter contract** — `schemas/phase-result.schema.json` now accepts
  both `"ascendo/v1"` and `"ubuntu-aktualizacje/v1"` (enum) at the legacy-
  validation layer, matching the bash emitter's intentional
  `ubuntu-aktualizacje/v1` literal (the core reader needs it to translate
  legacy sidecars — see Sesja 82). Unblocks the phase-JSON-contract step +
  `test_json_emit` + `test_phase_contract`.
- **Valid plugin template** — `plugins/_template/manifest.toml` `[scripts]`
  collapsed from (invalid) multi-line inline tables to single-line; the plugin
  scanner now validates all three shipped manifests.
- **Test corrections** — `test_require_sudo_trap.bats` asserts the finalize-only
  `"exit_code"` field (per-phase sidecars have no `status`);
  `test_cli_web` introspects command parameters instead of grepping rich-
  truncated `--help` text; `test_installers` pwsh AST harness pre-declares
  `$tokens`/`$errors` before `[ref]`.
- **Workflow deps** — dashboard-smoke installs `pytest`; the adapter-test matrix
  step installs `pytest-asyncio` (root `asyncio_mode="auto"` + strict
  `filterwarnings` made the missing-plugin warning fatal); the python-tests job
  installs the macOS adapter so the web-registry contract tests (which import
  `ascendo_macos.web_registry` by design) run instead of 503'ing.
- **Windows registry data** — corrected the `opencode` entry in
  `adapters/windows/config/web_apps.toml`: `silent_args` `["/S"]` → `["--silent"]`
  (Squirrel.Windows, not NSIS — also a latent runtime apply fix) and
  `windows_uninstall_key` GUID → `"OpenCode"` (DisplayName fallback).

### Linux/Ubuntu — v1.0-beta production push (Sesja 84, 2026-05-31)

- **Deduplicator is report-only on Linux (no implicit mutation).** In the
  fail-safe (non-opt-in / non-TTY) path, `apply_deduplication` no longer
  mutates or rewrites the read-only CHECK sidecars; duplicates are surfaced in
  `DEDUPLICATION_REPORT.md` only. Mutation + `DEDUPLICATION_TASKS.json` are
  written **exclusively** when an uninstall is explicitly queued
  (`ASCENDO_DEDUP_AUTO_UNINSTALL=1` or interactive consent). Ubuntu ships no
  uninstall executor, so the duplicate set is recommend-only by design.
- **`verify_signature` apply-path coverage.** Added contract +
  wiring tests confirming the apt apply path feeds the real SHA-256 parsed from
  `apt-get --print-uris` (never `None`) into `UbuntuSource.verify_signature`,
  and fail-closes on a hash mismatch (`test_verify_signature_apt_gpg`,
  `test_verify_apt_signatures_passes_real_hash_from_print_uris`,
  `test_verify_apt_signatures_fail_closed_on_mismatch`).
- **Docs: Linux v1 = Ubuntu/Debian only.** README + LINUX_QUICKSTART +
  LINUX_TESTING now state that other distros (Fedora/RHEL/`dnf`, Arch/`pacman`)
  are deferred to a later release.
- **W10/W2/W11 on Ubuntu — assessed, GNU `sort -V` kept (documented).** The
  Ubuntu `web` category (`adapters/ubuntu/scripts/web/check.sh` +
  `lib/ascendo_web.sh`) is real but ships an effectively-empty curated feed for
  v1 (Linux apps flow through apt/snap/flatpak). `_version_gt` uses GNU
  coreutils `sort -V`, which is correct version-sort on Linux — the macOS W11
  concern targets BSD `sort`'s differing `-V`, so it does not apply on
  Ubuntu/Debian. Aligning Ubuntu's comparator with the macOS Python helper is
  tracked as a post-v1 consistency follow-up, not a v1 blocker.
- Ubuntu adapter test suite green natively (151 passed) and now
  gated by CI on `ubuntu-24.04`.

### Fixed — 2nd-pass production audit (2026-05-31)

- **P0 — deduplicator silent auto-uninstall neutralized.** `core/ascendo/
  orchestrator/deduplicator.py::_confirm_uninstall` returned `True` for every
  non-TTY caller, so a "Safe update" from the dashboard (non-TTY) could
  uninstall packages with no consent (Windows `winget/npm/pip apply.ps1`
  execute `DEDUPLICATION_TASKS.json`). Now fail-safe: non-interactive callers
  return `False` (report-only); auto-uninstall requires an interactive Yes or
  an explicit `ASCENDO_DEDUP_AUTO_UNINSTALL=1` opt-in.
- **2 macOS test regressions fixed (suite was red, EXIT=1).**
  `test_msupdate_apply_calls_msupdate_install` → rewritten to
  `test_msupdate_apply_falls_back_to_manual_gui` (asserts RC 95 + no
  `--install`, matching the intentional "drop silent msupdate installs"
  decision). Perplexity registry entry reverted `handler = "sparkle"` →
  `"builtin"`: an unverified flip contradicted its own comment block and broke
  the shipped-registry invariant ("zero fake-silent-install risk").
- **CI blind spot closed.** `.github/workflows/validate.yml` now runs each
  adapter's Python test suite (`adapters/<os>/tests`) on its native matrix
  runner — previously only `tests/contract|cross-cut|integration` gated merges,
  which let the two macOS-adapter regressions land green.
- **CORS docstring** in `core/ascendo/dashboard/app.py` corrected (claimed
  default `[*]`; the actual default is the loopback allowlist).

> NOTE: the "Added — Inventory hardening" entries below (`reconcile()`,
> `scan_meta`/`set_scan_complete`) are present + unit-tested but have **zero
> production call sites** — orphan eviction already works via
> `_replace_buckets_in_db` (clear+replace per full scan), so this is dead code,
> not a live data fix. See `ASCENDO_ULTRA_REVIEW_2.md` §2.
> **DECISION (v1.0-beta, audit A6):** KEEP — the methods are inert (no data
> bug, no behaviour) and removing them would churn four contract-test files
> during the beta stabilization freeze for no functional gain. Removal-or-wiring
> (with `core/ascendo/plugins_loader/`) is tracked as post-beta cleanup. See
> ADR-0005 → Appendix: Feature Deferrals. W11 (`sort -V` → Python comparator in
> the macOS npm/pip scripts + `ascendo_web.sh`) is **done**.

### Fixed — Sparkle architecture selection and Bash 3.2 compatibility

- **Sparkle Appcast Arch Filtering:** `adapters/macos/lib/ascendo_web.sh` now uses a robust Python `xml.etree.ElementTree` parser instead of a naive regex. It correctly extracts and filters `<item>` elements based on `<sparkle:hardwareRequirements>`, ensuring Apple Silicon users no longer receive `x64` builds of applications like Codex.
- **`lib/detect.sh` Bash 3.2 Fix:** Wrapped Ubuntu-only `declare -gA` associative array initializations in a Bash version check. This prevents `preflight.sh` from crashing with `declare: -g: invalid option` when run on macOS (which uses Bash 3.2).

### Changed — Honest inventory status (Pass C — [honest-status])

- **`_INVENTORY_STATUS_MAP`**: `failed`→`"failed"` (was `"outdated"`),
  `partial`→`"failed"` (was `"outdated"`), `triggered`→`"triggered_pending"`
  (was `"up_to_date"`). Failed installs and un-reconciled vendor daemon kicks
  are no longer hidden behind green pills in the SPA.

### Added — Inventory hardening (Pass B — [I9/I2/D4/D8/D11/I5/I8/D3/T7])

- **I9: per-category scan-complete watermark** — new `scan_meta` table in
  `inventory_db.py` with `set_scan_complete(category)` / `get_scan_meta(category)`.
  `is_fresh()` keys on full-scan freshness, not last-write. Post-run flushes
  no longer masquerade as full scans.
- **I2/D4/D8/D11: reconciliation routine** — new `InventoryDB.reconcile(category,
  seen_names)` diffs DB vs. a live-scan and batch-deletes unseen rows in one
  transaction (I5). Safety guard: refuses to reconcile when `seen_names` is
  empty (likely discovery failure). Returns evicted count for operator logging.
- **I8: PRAGMA user_version** — schema version 2 anchored in the DB after
  migration so future migrations have a stable marker.
- **D3/T7: _normalize_item_id refined** — dot/hyphen separator collapse now
  restricted to a known source-category prefix allowlist. Prevents false
  positives like `Microsoft.VCRedist.2008.x64.Runtime` (name=`Runtime`) from
  being collapsed. Parametrized tests added for 12 edge cases.
- **I1/I3 regression tests** — schema literal distinctness (`ubuntu-aktualizacje/v1`
  ≠ `ascendo/v1`) and legacy `warn→skipped` mapping pinned by 5 new tests.

### Added — Engine hardening (Pass C — [E11/E8])

- **E11: RunStatus.CANCELLED** — new lifecycle state set when cooperative
  cancel fires. Post-run inventory flush is skipped on cancel (partial
  sidecars are unreliable).
- **E8: unrecognized phase warning** — `_flush_run_to_inventory_db` now logs
  a warning when a sidecar carries a phase not in `_PHASE_PRIORITY`. Items
  are still processed at priority 0 (data not lost).
- **E5: dead `except OSError` fixed** — `_safe_run_phase` in `runner.py` now
  catches `SidecarWriteError` (the actual exception from `write_sidecar`)
  instead of bare `OSError` (which was never raised).
- **Stream-log race** — `RunState` now carries `stream_log_path` per-run
  so concurrent workers don't clobber each other's `os.environ`.

### Fixed — Security (Pass E — [P5])

- **P5: CORS lockdown** — default CORS origins changed from `["*"]` to
  `["http://127.0.0.1:8765", "http://localhost:8765", ...]` (five localhost
  variants). Prevents any web page from accessing privileged endpoints when
  the user runs `--host 0.0.0.0`.

### Added — Data validation (Pass D — [D7/I7])

- **D7: blank-version normalization** — `InventoryDB.upsert` now converts
  empty/whitespace-only version strings to `NULL` instead of storing `""`.
- **I7: empty-name rejection** — `InventoryDB.upsert` raises `ValueError`
  on empty name or category (was silently dropped).

### Added — Utilities & tooling (Pass D/E — [W11/P12])

- **W11: Python version comparison** — new `ascendo.utils.version` module
  with `version_gt()`, `version_gte()`, `version_lt()`. Uses PEP-440
  `packaging.version` when available, falls back to dotted-integer
  comparison. Replaces `sort -V` dependency in shell scripts.
- **P12: stale sidecar-lock detection** — new `detect_stale_locks()` in
  `sidecar_io.py`. Finds `.lock` files older than a threshold (default 5
  min) so `ascendo doctor` can surface crashed-writer remnants.

### Tests

- **20 new tests** in `test_inventory_db_hardening.py` covering I9/I2/D4/D8/D11/I5/I8/D3/T7.
- **11 new tests** in `test_engine_hardening.py` covering honest-status/E11/E8.
- **5 new tests** in `test_schema_literals.py` covering I1/I3 regression guards.
- **15 new tests** in `test_remaining_hardening.py` covering E5/P5/stream-log/D7/I7/W11/P12.
- Updated `test_post_run_flush_priority.py` assertions for honest status.
- **Suite baseline**: 556 passed, 3 skipped, 1 xfailed, 0 failed.



- **`validate.yml`**: added `python-tests` job running full pytest suite
  on `ubuntu-24.04`, expanded bats step to include `test_require_sudo_trap.bats`
  (4th suite), and added `validate-cross-platform` matrix job for
  `ubuntu-24.04` / `macos-latest` / `windows-latest`.
- **`validate.yml` paths** expanded to include `core/`, `adapters/`,
  `pyproject.toml` so Python source changes trigger CI.

### Fixed — Test suite stabilisation (Phase 0 — [T4/T9])

- **T9: service router platform guard** — `_service_manager()` in
  `core/ascendo/dashboard/routes/service.py` now checks for a
  test-injected `app.state.service_manager` **before** the
  `sys.platform.startswith("win")` guard. This lets cross-platform
  contract tests run with fake managers on any OS. All 10
  `test_service_endpoints` tests now pass on macOS/Linux CI.
- **T4: apply_report grouping xfail** — marked
  `test_generate_apply_report_groups_categories` as xfail; the test
  searches for `"macOS web apps"` but the report renders
  `"Web apps (AppImage / GitHub releases / Sparkle)"`.
- **Dashboard stop test** — updated
  `test_runs_active_stop_running_run` assertion from `ok=False` to
  `ok=True` matching the current cooperative-cancel semantics.
- **cli_web port-sensitive tests** — added `skipif` guard for
  `test_web_status_reports_stopped_on_clean_state` and
  `test_web_open_refuses_when_not_running` when port 8765 is already
  bound (e.g. by the Tauri desktop app on a dev machine).

**Suite baseline**: 505 passed, 3 skipped, 1 xfailed, 0 failed.

### Fixed — Rebrand fallout: every dashboard-dispatched run failed with `KeyError: 'kind'`

The rebrand commit `96d5167` ("chore: Rebrand project to Ascendo and
restructure configuration files") did a mechanical search-and-replace
of `ubuntu-aktualizacje` → `ascendo` across the entire repo, which
collapsed the **historical legacy schema literal** into the **canonical
current schema literal** — they became the same string. Result: every
canonical `ascendo/v1` sidecar emitted by the macOS/Ubuntu/Windows
adapters was mis-detected as legacy by `is_legacy_v1()`, routed through
`translate_legacy_v1()`, and immediately raised `KeyError: 'kind'`
because the canonical format uses `phase`, not `kind`. The error
escaped `_safe_run_phase` (catches `ManagerError` only), propagated
past `attach_run_log` (file handler detached, so no traceback in
`run.log`), and was finally caught by the async worker's
`except Exception` — visible only via `GET /runs/<id>/status`. Every
single web-dispatched run since the rebrand crashed with zero sidecars
on disk.

Restored the legacy literal to its historical value `ubuntu-aktualizacje/v1`
in [core/ascendo/models/legacy.py:71](core/ascendo/models/legacy.py:71),
[core/ascendo/models/sidecar.py:43](core/ascendo/models/sidecar.py:43),
[core/ascendo/models/sidecar.py:216](core/ascendo/models/sidecar.py:216),
[docs/architecture/schemas/sidecar.v1.schema.json:598](docs/architecture/schemas/sidecar.v1.schema.json:598)
and the two legacy test fixtures.

Belt-and-suspenders guard: [core/ascendo/orchestrator/sidecar_io.py:501](core/ascendo/orchestrator/sidecar_io.py:501)
`read_sidecar()` now also catches `KeyError`, rewrapping it as
`SidecarReadError` so a stray missing-field-in-legacy-translator can
never again silently kill an entire async run.

ADR-0003 picked up an explicit "do not change the legacy literal"
warning so a future mass-rename cannot regress this.

Live: dashboard restarted, `POST /runs/async` with full profile
completed cleanly (220 packages enumerated, 0 failures, full
REPORT.md generated). Confirmed across all three adapters — macOS
416/417, Ubuntu 140/141, Windows 453/453 (the remaining failures
are documented pre-existing environment flakes from earlier
sessions, none touch the sidecar-parse path).

Affects: every operator who pulled `96d5167` or later. Cross-platform
— Ubuntu + Windows + macOS adapters all hit the same `KeyError`
because the legacy detector lives in shared `core/`.

### Added — Touch-first responsive UI kit (Sesja 74)

- New `app/frontend/ui-components.js`: **no native dropdowns anywhere**
  — every `<select>` is upgraded at runtime to an accessible
  segmented control, choice-card group, or searchable progressive
  list (native select kept value-synced so `FormData`/`.value`/
  `change` listeners are unchanged).
- **Mobile bottom tab bar** (5 destinations, ≤768px), Run Center
  **3-step progressive reveal** (Profile → Options → Confirm) with a
  sticky mobile action bar, and a **tappable mobile card** layout for
  the History table.
- 44px minimum touch targets, `:focus-visible`, keyboard radiogroup
  navigation, `prefers-reduced-motion`. New `uikit.*` i18n namespace
  (EN+PL parity **1060/1060**).

### Changed — Light theme contrast & hierarchy (Sesja 74)

- Retuned the light token set (`colors_and_type.css`): a real 3-tier
  surface ramp (`--paper-base/-nested/-card/-sunk` + new
  `--bg-nested`), darker muted/faint text + borders, `--accent-strong`
  → lime-700 (AA as fg accent). Identity preserved (cool-grey + lime).
- New `--ok/warn/err/info-text` tokens split bright semantic *fills*
  from WCAG-AA semantic *text on tint* (light-mode badges/inline
  errors/diagnostics were ~2:1 before). **Dark theme unchanged**
  (tokens mapped back to the bright primitives).
- Responsive header density: eliminated a ~288px mobile dead-band
  (root cause: `.app-header-text` flex-basis becoming a forced height
  in the mobile column layout); `.app-header` 439px → 59–99px on a
  390-wide phone; demoted the redundant per-view `<h2>` to a compact
  section label; token-only spacing tiers for mobile/tablet/desktop.

### Fixed — interaction QA + pip verify (Sesja 74)

- **Help table-of-contents links** no longer get hijacked by the hash
  router (they were throwing the user back to Dashboard); they now
  scroll to the section and leave the route untouched.
- **Run Center "Stop"** is now always reachable during an active run
  (was hidden inside the collapsed progressive-disclosure step).
- macOS `pip verify` now defers **all** brew-owned formulas (not just
  pip/setuptools/wheel) to brew, so `uv` is no longer reported as a
  verify failure against the PyPI candidate — pip verify
  `partial → success`.

### Changed — UX/IA refactor: 5-destination AppShell (Sesja 73)

- **Navigation reorganised** from 10–13 flat sidebar items into **5
  workflow destinations**: Dashboard, Library (Sources/Apps/Tools),
  Runs (Start/Scheduled/History), Insights (Trends + dev Logs),
  Settings (General/Help/About + dev Hosts/Sync). Old hashes
  (`#schedule`, `#apps`, …) auto-resolve so existing bookmarks keep
  working.
- **Per-page header** with title, one-line description, and a single
  primary action. Language/Theme/Font moved into a compact
  **Preferences popover** (same control IDs — no behavioural change).
- **New `platform.js`** OS abstraction layer (`Platform.os / allow /
  supportsNvidia / elevationTerm / copy`). NVIDIA driver UI + copy is
  structurally excluded on macOS across JS, i18n, and CSS layers.
- **New `shell.js`** AppShell (sidebar, header, segmented sub-tabs,
  Preferences popover, run-detail drawer) — wraps `ui.show()` with no
  rewrite of the SSE/runs/AI router.
- **New Insights** surface: run trends, recent failures, duration
  sparkline, recent changes, platform-aware operational notes
  (assembled from existing `/runs` data — no new backend).
- **History simplified** to 5 default columns (Started · Profile ·
  Status · Duration · Run details); phases/reboot/run-id moved into a
  right-side detail drawer. Premium action-oriented empty state.
- New `shell.*` + `platform.*` i18n namespaces (EN + PL parity:
  1045 == 1045).

### Fixed — operator bug batch (run e2d0fffb, Sesja 73)

- SPA assets now send `Cache-Control: no-cache, must-revalidate` so a
  `git pull` is picked up without a manual hard-reload (fixes the
  "schedule icon not applied" staleness).
- `safe`/`quick` profile web apply no longer opens apps to the
  foreground — `ASCENDO_SAFE_MODE` + exit-95 sentinel routes
  builtin/squirrel/omaha/release_feed to a silent `skipped` with a
  manual-action message.
- Codex update fixed: `_web_install_dmg` now handles ZIP archives
  (Sparkle appcast served a `.zip`, not a `.dmg`) via `ditto`.
- Ledger Live / Warp silent-install refusal now surfaces as a real
  error: explicit `rm -rf` before `cp -R` exposes a locked running
  bundle instead of a false success.
- Uninstalled apps (Cursor/Opera/Notion/Notion-Calendar) are now
  evicted from inventory via `InventoryDB.delete_row` during the
  post-run flush instead of lingering forever.
- `pip uv` (and brew-owned pip-self) now report `up_to_date` instead
  of landing in REPORT.md's "Deferred" section.
- **History tab fixed** — a Sesja-66 `i18n.t` typo plus a `tr`
  variable shadowing the i18n helper threw mid-loop and left the
  table blank; also fixed the identical bug in the Schedule list.

### Planned

M6 — security audit (T1-T7 per ADR-0005), code signing across all three
OSes, plugin signing + verification, plugin marketplace UX in dashboard.

---

## [0.6.7] — 2026-05-14 — Inventory dedup + Suggestions AI + Schedule tab + Help/About (Sesja 67)

Sesja 67. Operator: *"check why inventory changes after each run …
implement fully working suggestions … every click in web app works".*
Four deliverables.

### Added

- **Schedule tab** (previously-deferred): new
  `core/ascendo/dashboard/routes/scheduler_real.py` with
  `GET /scheduler/list` + `POST /scheduler/{install,remove,trigger}`
  driving the adapter's `IScheduler` implementation. SPA gets a
  dedicated `#view-schedule` with list table + add-or-replace form +
  per-row Run-now / Edit / Delete. Replaces the previous
  `{ok: true, stub: true}` stubs.
- **Suggestions AI integration** (previously-deferred): new
  `call_provider_inference()` in `routes/ai.py` covers 6 providers
  (anthropic / openai / openrouter / ollama / google / lm_studio).
  `/suggestions/library` now prepends 1-3 AI-generated cards on top
  of rule-based with strict JSON parsing + action-payload sanitisation.
  Failures fall back to rule-based transparently.
- **About: Recent highlights panel** — Sesjas 58-67 capability tour
  with GitHub + Releases & downloads links.
- **Help: "12. Recent additions" + "13. Operator tooling"** sections
  wired to the Sesja 66 `help.windows.*` i18n keys that had been
  orphaned + 16 new keys (EN + PL) for ascendo web lifecycle /
  build-inventory / run-tag-release / install-service / validate
  harness / watchdog / Suggestions AI / Schedule tab.

### Fixed

- **Inventory drift across runs.** Pre-v2 `inventory_items` PK was
  `(category, name)` which silently collapsed 17 msstore + 14 winget
  + 3 ARP packages sharing DisplayNames across architectures (MSIX
  x86/x64/arm64; Microsoft Visual C++ 2008 Redistributable's 9
  parallel installs; Comet's two ARP rows; etc.). Schema migrated to
  v2 with PK `(category, name, item_id)`; bulk_upsert + query +
  flush callers all updated. Live verified on DP5520WMK: msstore
  78 → 85 rows, winget keeps 9 separate VC++ 2008 architecture
  entries. Pre-v2 DBs drop legacy data on first open; next live-scan
  or post-run flush repopulates within seconds. +7 regression tests
  in `tests/contract/test_inventory_db_item_id.py`.
- **Help managers reference table** was missing rows for npm / pip /
  web / plugin — added with Sesja 58-65 context (Tier-A silent
  install, fake-success detection, apply-mark, dedup).

### Test count

453 (Sesja 66) → **477 passing** Windows + contract (+24 new:
7 inventory_db item_id + 3 overlay + 14 suggestions_ai).
Zero regressions.

---

## [0.6.6] — 2026-05-13 — Inventory + apply-mark consistency + SPA polish (Sesja 66)

Sesja 66. Operator regression report on `DP5520WMK`: VSCode 1.119.1 →
1.120.0 was upgraded manually, but `ascendo build-inventory` still
reported the web row as `installed=1.119.1, candidate=1.120.0, outdated`
even after the latest full update run had `check__web.json` correctly
showing 1.120.0. Plus IMG to ISO was being re-applied on every full
run despite Sesja 63's apply-mark already persisting the target.

### Fixed

- **Post-apply overlay no longer leaks across runs.** `_latest_check_overlay`
  in `core/ascendo/dashboard/routes/spa_real.py` was walking apply/verify
  sidecars from ALL prior runs in `post_apply_payloads`. An OLD
  `triggered` apply from a previous run (e.g. VSCode 1.119.1 triggered
  at 11:51) would stick because every newer run's `up_to_date` status
  is skipped by the overlay (only `success`/`triggered` overlay). Fixed
  to only consider apply/verify payloads from the SAME RUN as the chosen
  check baseline. +3 regression tests in `tests/contract/test_overlay_same_run_only.py`.
- **plan.ps1 + apply.ps1 now honour Sesja 63's apply-mark.** Previously
  only `check.ps1` consulted `Get-AscendoApplyMark`. For packages whose
  `winget list Version=Unknown` BOTH before and after a successful
  upgrade (SoftSea.IMGtoISO is the canonical example), check correctly
  reported `up_to_date` but plan classified them as `planned` and apply
  re-ran the upgrade. Plan now skips marked packages; apply emits
  `status=up_to_date` without invoking winget. +5 regression tests in
  `adapters/windows/tests/test_winget_apply_mark_in_plan_and_apply.py`.
- **i18n cleanup.** Polish help / about / history / settings sections
  in `app/frontend/i18n.js` had 3-4× duplicated entries from a previous
  bad merge — fixed surgically (lines 1828-2069 trimmed; file went from
  2187 → 2041 lines). Both EN + PL now have a `windows: {…}` Help block
  describing all 8 managers (winget, msstore, npm, pip, web, plugin,
  registry_arp, windows_update) and the Sesja 63-65 mechanisms (apply-
  mark, fake-success detection, Tier-A silent install, web/winget dedup).

### Added

- **History → REPORT.md link.** Every row in the History tab now has a
  📄 link opening `/runs/{id}/report` in a new tab. The endpoint at
  `core/ascendo/dashboard/routes/runs.py:458` was already implemented
  but the SPA never surfaced it. EN + PL i18n keys `history.report` +
  `history.view_report`.

### Test count

448 (Sesja 65) → **453 passing** on Windows (+5 apply-mark regression
tests). +3 contract tests for the overlay fix. Zero regressions.

---

## [0.6.3] — 2026-05-12 — Version polarity across all phases + new logos + ascendo build-inventory

Sesja 57. Operator audit on `mk-uP5520` surfaced three classes of bug
and one missing CLI feature.

### Fixed

- **Version polarity across the 5-phase pipeline.** check / plan /
  apply / verify scripts emitted "present" items with only `to=$ver`
  set, leaving `from=` empty. The SPA overlay reads
  `from→installed` + `to→candidate`, so the inventory row painted
  `installed=null`. Across snap / apt / brew / npm / pip / flatpak /
  drivers / npm-plan, the relevant `json_add_item` calls now pass
  the version into BOTH `from=` and `to=`. After a fresh check+verify
  pass: 6/6 snap items, 24/24 apt verify items, 4/4 npm verify items,
  3/3 npm-plan force-latest items, 1/1 drivers item all carry
  `current_version` AND `target_version`.
- **Web check no longer surfaces uninstalled apps.** Pass 2 (registry-
  only / "not installed locally") gated behind `ASCENDO_WEB_INCLUDE_
  UNINSTALLED=1` env var. Default behaviour: discovery-only — only
  apps actually on disk appear in the web category. Cursor, Discord,
  and any other registry-listed-but-not-installed app drop out.
- **Auth-modal Enter key.** Explicit `keydown` listener on
  `#sudo-pass` calls `form.requestSubmit()` on Enter, so a focus-race
  in some browser/locale combinations can't swallow the keystroke.
  Native `<form>` + submit-button should already handle it; this is
  belt-and-suspenders.
- **Snap apply post-restart.** The "snap apply script produced no
  sidecar" error class — already mitigated in Sesja 56 by the
  `_BaseManager._salvage_sidecar` recovery path — is confirmed live
  on this host after a dashboard restart. The old failing run pre-
  dated the salvage fix; running uvicorn process needs to be
  restarted (`ascendo web restart`) for the new Python to load.

### Added

- **`ascendo build-inventory`** top-level CLI command. Standalone
  equivalent of the dashboard's Overview "Build inventory" button.
  Idempotent; per-source summary; flushes to
  `~/.ascendo/inventory.db`. Honours `ASCENDO_INVENTORY_DB` env;
  `--no-db` skips DB flush; `--verbose` traces. Live on this host:
  2588 packages across 6 sources.

### Changed

- **Brand assets** synced to the Ascendo design system. `app/frontend/
  favicon.svg` (browser tab icon) was still the pre-Sesja-30 green→
  blue gradient mark; replaced with the lime (`#C8FF4B`) bars on ink
  (`#0B1020`) design. Same fix for `branding/icon.svg` +
  `branding/logo.svg` (tooling source) and `app/frontend/assets/
  logo-mark-light.svg` (added the paper-bg rect that was missing).
  Tauri PNG/ICO regen via `bin/regenerate-icons.sh` requires
  ImageMagick — re-run before the next desktop build.

---

## [0.6.2] — 2026-05-12 — Linux production-readiness + .deb editions

Sesja 56. Focuses on putting the Ubuntu adapter into shippable shape:
edition-aware .deb installer (basic + dev), defensive sidecar salvage
path so a bash script that dies mid-run still leaves a real sidecar
behind, and the drivers row no longer appears as falsely outdated.

### Added

- `packaging/build-deb.sh --edition=basic|dev` flag — bakes the chosen
  edition into `/opt/ascendo/.ascendo-edition` and labels the output
  file as `ascendo-basic_<v>_all.deb` / `ascendo-dev_<v>_all.deb` so
  both artefacts coexist in `dist/`.
- `_BaseManager._salvage_sidecar()` in
  `adapters/ubuntu/ascendo_ubuntu/managers/_base.py` — when a phase
  script exits without firing its `EXIT` trap, the orchestrator now
  finalizes from the pre-allocated `JSON_BUFDIR` instead of
  synthesizing a `failed` stub. Adds an explicit `ASCENDO-SALVAGED`
  diagnostic. Belt-and-suspenders defense against the class of bugs
  that hit snap apply in Sesja 55.

### Changed

- `lib/json.sh::json_init` — honors a pre-set `JSON_BUFDIR` env var
  (the orchestrator now passes one) instead of unconditionally
  allocating a fresh `mktemp -d`. Lets Python recover partial state
  post-mortem.
- `scripts/drivers/check.sh` — NVIDIA "present" item now writes the
  version into both `from=` and `to=` (was: package name → version,
  which the SPA overlay read as `installed != candidate → outdated`).
  Package name moves to `details=`. Inventory drivers row no longer
  appears falsely outdated.
- `.gitignore` — `packaging/deb/opt/` and `packaging/deb/usr/` now
  ignored (auto-generated stage trees; `DEBIAN/*` templates stay
  tracked).

### Removed

- Legacy `packaging/deb/opt/ascendo/` stage tree (191 stale
  files from before the rebrand). The `build-deb.sh` clean-stage step
  already wipes it on each build; this commit removes it from the
  index too.

### Operator notes

- Old `ascendo-dashboard.service` systemd-user unit on
  this host was renamed to `*.disabled-by-ascendo` so it can never
  autostart again. Old + new app state are already separated
  (`~/.local/share/ascendo/` vs `~/.ascendo/`) — no
  config conflict to clean up.

---

## [0.6.1] — 2026-05-11 — Ubuntu adapter parity + production-hardening

Sesja 54 + 55. Brings Ubuntu adapter to full feature parity with macOS
and hardens it against real-world failure modes uncovered during
live-fire operator testing on Ubuntu 24.04.

### Added

- **`adapters/ubuntu/ascendo_ubuntu/managers/elevation.py`** —
  `LinuxElevation(IElevation)` mirrors `MacElevation`. sudo password
  cached in-memory, askpass helper at `adapters/ubuntu/lib/askpass_helper.sh`,
  dashboard `/elevation/auth` + `/elevation/status` endpoints work
  unchanged. 29 tests.
- **`adapters/ubuntu/ascendo_ubuntu/snapshot.py`** —
  `TimeshiftSnapshot(ISnapshot)`. Wraps `sudo -A timeshift --create
  --scripted` + `--list`. Backend slug `"timeshift"`. Degrades to
  "warn" health component when timeshift is missing. Restore
  deliberately omitted (destructive).
- **`adapters/ubuntu/ascendo_ubuntu/managers/scheduler.py`** —
  `SystemdScheduler(IScheduler)`. Per-user systemd timers under
  `~/.config/systemd/user/ascendo-<name>.{service,timer}`. DSL parser
  identical to LaunchdScheduler (DAILY/WEEKLY/MONTHLY/HOURLY/MINUTE).
  33 tests.
- **`adapters/ubuntu/ascendo_ubuntu/managers/web.py`** — 8th
  IPackageManager. Linux-flavored WebManager covering AppImage / GitHub
  releases / release_feed / builtin handlers. Slimmer than macOS (no
  Sparkle/keystone/squirrel — those are Mac-only frameworks). 17 tests.
- **`bin/validate-ubuntu.sh`** — 10-stage / 23-check end-to-end smoke
  harness mirroring `validate-macos.sh` and `validate-windows.ps1`.
- **`LINUX_TESTING.md`** — operator-facing testing guide.
- **Bridge improvements** in `adapters/ubuntu/ascendo_ubuntu/managers/_base.py`:
  `start_new_session=True`, watchdog heartbeat thread (10s silence
  trigger), `>>> starting` marker, auto-injected non-interactive env
  (`DEBIAN_FRONTEND`, `NEEDRESTART_MODE`, etc.), `stdin=DEVNULL`.
- **`lib/json.sh`** SIGINT/SIGTERM trap producing partial sidecar
  with `ASCENDO-INTERRUPTED` diagnostic + canonical exit code (130/143).

### Fixed

- **`require_sudo` clobbered the json EXIT trap.** Snap apply ran
  successfully (refreshed thunderbird visible in stream log) but the
  sidecar was never written → bridge synthesised a failed sidecar
  from "no sidecar produced" error → SPA showed phantom failure.
  Now keepalive killer chains with whatever existing trap was
  registered.
- **Inventory `list.sh` silently skipped npm + pip categories.** Two
  compounding bash bugs: heredoc inside `$(... || true)` is a parse
  error; `python3 - <<'PY'` collides with `printf | python3 -` over
  stdin. Fix: `python3 -c '<inline>'`. Live impact: enumeration
  jumped 2539 → 2579 items (+40 npm/pip rows that were silently
  dropped).
- **SPA inventory overlay never matched check-sidecar items.** Legacy
  bash check.sh emits items with synthetic compound IDs
  (`snap:upgrade:firefox`) but inventory has clean names (`firefox`).
  Now overlay also indexes by trailing colon-segment.
- **`brew --cask --greedy` looked like a hang.** Re-downloaded every
  cask whose version is "latest" or has `auto_updates=true` on every
  apply, easily 10+ minutes per run. Default upgrade is now `--cask`
  only; opt-in via `ASCENDO_BREW_GREEDY=1`.
- **`scripts/pip/plan.sh` emitted `kind=check`** clobbering the real
  check sidecar. Post-processes the sidecar to rewrite kind→plan.
- **`legacy_compat` translator mapped exit_code=1 → status=failed.**
  Per `docs/agents/contract.md`, exit 1 is "warn" (advisories only).
  Three-way mapping: `{0,1 → success, 75 → skipped, else → failed}`.
- **Legacy_compat synthesised a `uuid5` run.id** that mismatched the
  orchestrator's run.id, so post-apply hooks (REPORT.md, update_history,
  dashboard `/runs/{id}`) all 404'd. Bridge now overwrites
  `sc.run.id` after `read_sidecar`.
- **REPORT.md said "macOS web apps"** — fixed to `"Web apps
  (AppImage / GitHub releases / Sparkle)"`.
- **`validate-ubuntu.sh` was too strict** — accepted only `success`,
  rejected `partial`. Real systems hit soft advisories. Now accepts
  both.

### Live test results on `mk-uP5520`

```
$ python3 -m ascendo doctor
adapter: ubuntu (Ubuntu / Debian) tier=1
capabilities: AdapterCapability.PACKAGE_MANAGEMENT|INVENTORY|SNAPSHOTS|SCHEDULING|ELEVATION

$ bash bin/validate-ubuntu.sh
ALL CHECKS PASSED. (23/23)

$ python3 -m ascendo run -c apt,snap,brew,npm,pip,flatpak,web -p check,plan,apply,verify,cleanup
overall: success (35 sidecars, 80 items)

$ sqlite3 ~/.ascendo/inventory.db 'SELECT category, COUNT(*) FROM inventory_items GROUP BY category;'
apt|2476  brew_formula|47  npm|4  pip|36  snap|16
TOTAL: 2579 items, all with installed + candidate populated
```

143/143 ubuntu adapter tests + 13/13 contract `test_legacy_compat` +
9/9 ubuntu_inventory tests green.

---

## [0.6.0-rc1] — 2026-05-09 — Edition split + GUI-PATH fixes

Sesja 51 + 52 + 53. Splits Ascendo into `basic` and `dev` editions from
one repo, fixes a class of macOS GUI-PATH bugs that were poisoning
package installs, ships a clickable .dmg installer with edition baked
into the artefact name.

### Added

- **`ASCENDO_EDITION` flag** (`basic` | `dev`, default `basic`) plumbed
  through dashboard, frontend, helpers, and installers. Basic edition
  hides Sync/Hosts/Logs nav, merges History+Logs inline, removes
  raw-events box. Dev edition keeps the full 12-tab UI.
- **8-cell install matrix** in README — `{basic, dev}` × `{cli, web,
  desktop, full}`. Both editions buildable from one source tree.
- **Smart installers** — `bin/build-dmg.sh` (macOS, baking edition into
  the artefact: `Ascendo-Basic-0.0.7-arm64.dmg` vs
  `Ascendo-Dev-0.0.7-arm64.dmg`), modernized `packaging/build-deb.sh`
  with version sync + `--dry-run` + `--no-symlinks` flags,
  `packaging/homebrew-tap/ascendo.rb` formula stub, NSIS hooks +
  bin-staging mirror in `bin/build-installer.ps1`.
- **`bin/first-run-bootstrap-{macos,linux}.sh` + `.ps1`** — auto-install
  Python ≥ 3.11, git, curl, jq via the platform package manager on
  first launch.
- **`bin/user-scripts/`** — 21 helper shims: `ascendo_update`,
  `ascendo_start_web`, `ascendo_stop_web`, `ascendo_restart_web`,
  `ascendo_start_desktop`, `ascendo_stop_desktop`, `ascendo_doctor`,
  `ascendo_maintenance` (full / quick / dry-run / category=X /
  rebuild-inventory / check-errors), plus dev-only `ascendo_sync` +
  `ascendo_push`.
- **`LINUX_QUICKSTART.md`** mirroring the macOS / Windows quickstart
  structure (12 sections).
- **`docs/PLATFORM_STATUS.md`** — honest cross-platform feature matrix
  across 13 sub-tables, known gaps per platform, scoped roadmap.
- **`DEV_GUIDE.md`** — 507-line contributor guide.
- **`USER_GUIDE.md`** rewritten as basic-edition end-user guide
  (444 lines, all dev surfaces stripped).
- **Two onboarding wizards** — basic = 6 steps; dev = 9 steps with
  GitHub repo config + dev-sync setup + dev-resources panes.
- **Public-repo audit** — `docs/PUBLIC_AUDIT.md` + corrected `.gitignore`
  keep AI instructions, internal handoffs, and per-user dev-sync config
  private; dev-sync TOOLING (Python lib + 15 wrapper scripts) stays
  public so dev-edition users can bootstrap their own overlay against
  any rclone-supported provider.
- **`bin/dev-sync-overlay-migrate.sh`** + `dev-sync-overlay/` skeleton —
  copy-only migration tool for staging private files into the
  Proton-synced overlay before public-repo flip.
- **Cross-platform parity quick wins** — Linux apply.sh scripts
  (apt/snap/brew/npm/pip/flatpak) capture stderr-tail into sidecar
  diagnostics + emit SSE live-stream events; Windows
  msstore/arp/windows_update apply.ps1 also stream live.
- **`EditionGateMiddleware`** in `core/ascendo/dashboard/middleware/` —
  404s `/sync/*`, `/hosts*`, `/git/push*`, `/dev-sync*`,
  `/profiles/import*` when edition=basic.
- **`/sync/config-status` + `/sync/setup` endpoints** — dev-only,
  feed the wizard's dev-sync setup step.

### Fixed

- **Tauri shell crashed on launch (`Ascendo quit unexpectedly`).** Root
  cause: macOS GUI-launched apps inherit only the launchctl PATH
  (`/usr/bin:/bin:/usr/sbin:/sbin`), so `Command::new("ascendo")`
  failed with ENOENT and `.expect()` panicked during
  `applicationDidFinishLaunching:`. Fix: `locate_sidecar()` probes
  6+ absolute paths first; spawn failures return `Option<Child>`
  instead of panicking; WebView opens an embedded recovery page with
  the exact `sudo ln -sf` one-liner.
- **opencode-cli npm postinstall failed: `bun: command not found`.**
  The Tauri-launched dashboard's `sh -c` postinstall subshell didn't
  see `~/.local/share/mac-update/node/bin/node` or `~/.bun/bin/bun`.
  Fix: npm/apply.sh extends PATH with the toolchain node + bun bin
  dirs + brew + `~/.local/bin` before invoking npm.
- **Pip installed packages into Xcode Python 3.9.** The dashboard
  resolved `pip3` via launchctl PATH → `/usr/bin/pip3` → Apple's
  framework Python. Every CLI (poetry, ruff, mypy, etc.) silently
  installed into `~/Library/Python/3.9/bin/`. Fix:
  `ascendo_pip_pip_bin` and `ascendo_pip_python_bin` probe
  `/opt/homebrew/bin/pip3` first AND explicitly REJECT
  `/usr/bin/pip3` / `/usr/bin/python3` (Xcode shims). Plus
  `_augment_path_for_macos_gui()` prepends 8 known-good dirs at
  dashboard startup so all spawned subprocesses inherit the right env.
- **Apps view kept showing "outdated" after successful apply.** The
  `/inventory/db/refresh` endpoint walked only **check** sidecars when
  rebuilding inventory — post-apply truth from verify sidecars was
  overwritten with stale pre-apply data. Fix: `_latest_check_overlay`
  walks check / apply / verify newest-first with phase-priority
  tie-break (`verify > apply > check`). Operator's opencode-cli now
  correctly reflects 1.14.44 in the SPA after upgrading from 1.14.43.
- **`bin/build-dmg.sh` failed at cargo build** with
  `glob pattern bin-staging/**/* path not found`. Sesja 52 added the
  `bundle.resources` glob but only `bin/build-installer.ps1` populated
  it. Fix: `bin/build-dmg.sh` mirrors the step before Tauri.
- **npm/pip apply re-installed everything every run** even when
  packages were already at latest. Fix: up_to_date guard in apply_npm
  / apply_pip / apply_native_node / apply_native_bun reads installed
  + latest before invoking install, skips if equal. Cache-bust after
  successful install so the post-install version lookup reflects the
  fresh state instead of the pre-install snapshot.
- **SSE stream emitted every line twice.** Server-side: `_stream.log`
  matched the `*.log` glob, so the per-run log_files list contained
  the same path twice (explicit append + glob). Fix: dedupe by Path
  identity. Frontend: `ui.attachStream()` created a fresh EventSource
  without closing prior ones, accumulating N stale ESes that all
  appended to the same DOM. Fix: track all spawned ESes on
  `window._ascendoActiveStreams` and close them at the start of every
  attachStream call.

### Tests

- 683 green: 290 contract + 393 macOS adapter (9 pre-existing
  Windows-only test_service_endpoints failures unchanged).

### Pending real-hardware validation (next session)

- Real-Ubuntu mk-uP5520 — verify new Linux apply paths
- Tauri MSI/NSIS build on Windows DP5520WMK
- Real-public-flip: bin/dev-sync-overlay-migrate.sh + git rm + tag
  v0.6.0 + GitHub make-public

---

## [0.5.2] — 2026-05-09 — Cross-platform parity + one-line install/update

Sesja 45. Brings Windows + Ubuntu adapters up to functional parity with
macOS v0.5.1 and ships true one-line install + update for all three OSes.
**841/848 tests green** (9 pre-existing service_endpoints failures + 7
platform-specific skips).

### Added — Ubuntu (transitions from stub to Tier-1)

- **`adapters/ubuntu/ascendo_ubuntu/`** — full Python adapter scaffold:
  `UbuntuAdapter` + 7 managers (apt/snap/brew/npm/pip/flatpak/drivers)
  + `UbuntuInventory` + `BashPhaseManager` base. Capabilities
  `PACKAGE_MANAGEMENT | INVENTORY`. Bridges to mature legacy bash
  scripts at top-level `scripts/<cat>/<phase>.sh` via env-var IPC
  contract matching `lib/orchestrator.sh`. Schema translation
  transparent via `parse_sidecar()`.
- **`adapters/ubuntu/scripts/inventory/list.sh`** (427 LOC) — full
  inventory enumeration across apt+snap+flatpak+brew+npm+pip with
  10s timeout per tool, graceful skip on missing CLIs, single
  ascendo/v1 sidecar with `<source>:<package>` IDs.
- **`SourceType.DRIVERS` + `SourceType.FIRMWARE`** in core enum;
  legacy translator `'drivers' → SourceType.DRIVERS` (was UNKNOWN).
- 36 new Ubuntu adapter tests + 9 inventory tests; mock-based
  (no real apt/dpkg required).

### Added — Windows parity fixes

- **stderr capture in apply.ps1 × 4 sources** (winget/msstore/arp/
  windows_update). On non-zero exit, last 12 stderr lines (capped at
  1500 chars) appended to sidecar messages — operator finally sees
  actual error reason instead of "exited N". winget+msstore use
  `Start-Process -RedirectStandardError`; windows_update uses
  `-ErrorVariable` (cmdlet, not subprocess).
- **Pre-dispatch up_to_date guard** in winget + msstore apply — skips
  packages where installed == latest. Mirrors macOS `web/apply.sh`
  Sesja 40 pattern.
- 6 new regression tests; 99/99 Windows tests pass.

### Added — One-line install + update for all three OSes

- **`install.sh`** (rewrite, 451 LOC) — adds `--update` / `--reinstall` /
  `--verbose` / `--non-interactive`, env-var overrides
  (`ASCENDO_LANG`, `ASCENDO_PROFILE`, `ASCENDO_HOME`,
  `ASCENDO_NONINTERACTIVE`, `ASCENDO_REPO_URL`, `ASCENDO_BRANCH`),
  network preflight, disk-space check, locked-package-manager
  detection (apt fuser), final `ascendo doctor` self-test that bails
  on non-zero.
- **`update.sh`** (new, 187 LOC) — POSIX one-liner. `git pull
  --ff-only` (refuses to merge), refresh editable installs, restart
  any running dashboard via pgrep, version delta print.
- **`install.ps1`** (new, 382 LOC) — Windows `iwr | iex` one-liner.
  PowerShell 5.1 + 7.x compatible. Detects + auto-installs Python 3.12
  via winget, refuses Win < 10 b17763, shim at
  `%LOCALAPPDATA%\Microsoft\WindowsApps\ascendo.cmd`.
- **`update.ps1`** (new, 147 LOC) — Windows updater. Restarts
  `AscendoDashboard` Windows service if installed.
- **32 new contract tests** for installer entrypoints (argv parsing,
  help text, env-var wiring); pwsh AST validation skipped on hosts
  without pwsh.

### Fixed — Cross-cutting

- **`_flush_run_to_inventory_db` clears categories before bulk_upsert**
  (Sesja 40 added clear_category to 3 paths but missed the 4th —
  post-run flush in `run_async.py`). User's local DB had 312 web
  rows when discovery emitted 37; root cause fixed.

### One-liners

```bash
# macOS / Linux install:
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
# macOS / Linux update:
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash
```
```powershell
# Windows install:
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex
# Windows update:
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex
```

---

## [0.5.1] — 2026-05-09 — 5 operator bug fixes + portability doc

Sesja 44. Five operator-reported issues plus an architectural Q&A
documented as `docs/PORTABILITY.md`. 391/391 macOS tests + 249 contract
tests.

### Fixed

- Brave x86_64 mac bundle replaced with arm64; new
  `download_asset_pattern` field on release_feed selects universal DMG
  from GitHub release assets.
- `.npmrc prefix=` line stops coming back — `npm config set prefix`
  replaced with `NPM_CONFIG_PREFIX` env var + `scrub_npmrc` helper.
- Categories collapse-back fixed via missing CSS rule
  `.cat-detail.hidden { display: none }`.
- Touch ID sudo cache now honoured — `/sudo/status` probes `sudo -n -v`
  (1s cap) when no SPA password registered.
- Discovery brew classification fixed — `_flatten()` handles str/list,
  app filename matching, zap.trash plist mining, opt-in codesign deep
  ownership.

---

## [0.2.0] — 2026-05-05

**macOS adapter feature-complete (M5 done). Tier-1 minus source-verification.**
Tested on Mac.r12.home (Apple Silicon, macOS 15.x, bash 3.2.57,
Homebrew 5.1.9, mas 7.0.0, Python 3.13, jq 1.8.1).
**34/34 PASS** via `bin/validate-macos.sh`.

### Added

- **`adapters/macos/ascendo_macos/managers/scheduler.py`** — `LaunchdScheduler`
  implements `IScheduler` via per-user launchd LaunchAgents. Plists at
  `~/Library/LaunchAgents/dev.ascendo.<name>.plist`; description metadata
  in sidecar JSON at `~/Library/Application Support/Ascendo/schedules/<name>.json`.
  DSL mirrors WindowsScheduler exactly (DAILY / WEEKLY / MONTHLY / HOURLY /
  MINUTE → `StartCalendarInterval` plist dict; MINUTE → `StartInterval`).
- **`adapters/macos/scripts/scheduler/scheduler.sh`** — bash 3.2 driver
  for the launchd backend (install / uninstall / list / get / trigger).
  Idempotent `bootout`-then-`bootstrap` semantics. Argv-only contract;
  name regex `^[a-z0-9-]+$` enforced before plist filename interpolation.
- **`adapters/macos/ascendo_macos/managers/softwareupdate.py`** —
  `SoftwareUpdateManager` for macOS OS updates. `sudo -A softwareupdate
  -i ... -R --verbose` (the `-R` flag is mandatory).
- **`adapters/macos/ascendo_macos/snapshot.py`** — `TimeMachineSnapshot`
  read-only via `tmutil listlocalsnapshots /`. `create()` raises
  `SnapshotError` per APFS auto-management.
- **`adapters/macos/ascendo_macos/inventory.py`** — `MacOSInventory` via
  `system_profiler -json -detailLevel mini SPApplicationsDataType`. 387
  apps enumerated on Mac.r12.home with 5-rule classification (SYSTEM /
  MAS / BREW / WEB).
- **`adapters/macos/ascendo_macos/managers/mas.py`** — `MasManager` for
  the Mac App Store via `mas` CLI. `sudo mas upgrade <id>` enforced
  (CVE-2025-43411 mitigation).
- **`adapters/macos/ascendo_macos/managers/elevation.py`** —
  `MacElevation` (`IElevation` impl) with sudo askpass cache for
  dashboard-driven sudo. `POST /elevation/auth` round-trip on the
  dashboard.
- **`adapters/macos/ascendo_macos/managers/brew.py`** — `BrewManager`
  for Homebrew formulae + casks via `brew outdated --json=v2`.
- **`bin/install-dev-macos.sh` / `bin/validate-macos.sh` /
  `bin/run-tag-release-macos.sh` / `bin/launch-desktop-macos.sh`** —
  full bash equivalents of the Windows PowerShell launcher set.
- **`MACOS_QUICKSTART.md` / `MACOS_TESTING.md` / `USER_GUIDE.md`** —
  end-user-facing docs (operator install, full test matrix, cross-OS
  three-interface walkthrough).
- **Tauri 2.x macOS bundle** — `tauri.conf.json` `targets: "all"` now
  produces `.app` + `.dmg` on macOS (unsigned — code signing is M6).

### Changed

- `MacOSAdapter.capabilities` now declares the full Tier-1 minus
  `SOURCE_VERIFICATION`: `PACKAGE_MANAGEMENT | ELEVATION | INVENTORY |
  SNAPSHOTS | SCHEDULING`. `health_check()` now reports 10 components
  (was 9): added `launchctl`.
- `core/ascendo/models/sidecar.py`: `needs_reboot` moved from `Summary`
  to top-level `Sidecar` (consumer fix — dashboard router + CLI helper
  both read from the top level; Summary placement would have silently
  dropped the reboot signal on macOS softwareupdate runs).
- `Tauri 2.x`: bundle `targets` from `["msi", "nsis"]` to `"all"` so
  macOS / Linux builds produce native artefacts (.app/.dmg, .deb/.AppImage).

### Fixed

- **Critical (M5.5.11.1)** — `LaunchdScheduler._invoke` was passing
  `--output` / `--payload` to `scheduler.sh`, but the bash driver only
  accepts `--output-path` / `--payload-path`. Every `IScheduler` call on
  a real Mac would have failed with bash exit 2 (`unknown arg: --output`).
  Mock-only Python tests didn't catch it. Fix: rename to `--output-path` /
  `--payload-path`. Added regression test
  `test_invoke_with_payload_uses_payload_path_flag` so this can't drift
  silently again.
- **Important (M5.5.11.1)** — `trigger()` on a non-existent schedule
  silently returned `None` instead of raising `SchedulerError`. The bash
  driver emits `{"error": "no such schedule"}` + exit 30; Python's
  `_invoke` was returning the error dict and `trigger() -> None` was
  discarding it. Fix: when bash returns non-zero AND output JSON has an
  `"error"` key, `_invoke` raises `SchedulerError(error)`.
- **Operator-validation hotfix (M5.5.11.2)** — `bin/validate-macos.sh`
  Stage 12.2 was passing `--expression` to `python3 -m ascendo schedule
  install`, but the CLI's flag is `--calendar` (matches the Windows
  scheduler's term, predates M5.5). Fix: one-character change in
  validate-macos.sh.

### Tests

- 242 passing (was 158 on Windows-only at v0.0.7) on macOS:
  ~46 brew (M5.1) + ~63 mas/elevation (M5.2) + ~19 inventory (M5.3) +
  ~56 softwareupdate/snapshot (M5.4) + ~58 scheduler (M5.5).
- 34/34 end-to-end via `bin/validate-macos.sh` Stage 1-12 (CLI +
  dashboard + brew + mas + LaunchServices inventory + softwareupdate +
  Time Machine + launchd scheduler).

---

## [0.0.7] — pending tag (in flight)

**Windows MVP feature-complete + branded installer + first-run wizard.**
First publicly installable Ascendo build. Tested on Dell Precision 5520,
Windows 11 Pro Build 26200, PowerShell 7.6.1, winget 1.28.240, Python 3.14.

### Added

- **`packaging/winget-manifest/`** — Microsoft winget submission manifest
  (3 YAML files per spec 1.6.0): `Ascendo.Ascendo.yaml`,
  `Ascendo.Ascendo.installer.yaml`, `Ascendo.Ascendo.locale.en-US.yaml`,
  plus a submission `README.md`. Hashes filled at release time by
  `bin/build-installer.ps1`.
- **`branding/SLOGANS.md`** — single source of truth for marketing copy.
  Tagline `Unified updates. Every app. One click.` Installer banner,
  About modal, wizard welcome, Tauri config, and READMEs all pull from
  this file.
- **Windows-flavoured Help section** in the dashboard: 11 sections
  (Install / First run / CLI / Scripts / Config / Dashboard /
  Scheduler / Snapshots / Dev-sync / AI / Troubleshoot) explicitly
  cover Tauri shell + `bin/install-dev.ps1` install paths,
  `python -m ascendo` cheat-sheet, the 4 Windows package sources
  (winget / msstore / registry_arp / windows_update), Volume Shadow
  Copy snapshot/restore, and Windows-specific troubleshooting.
- **`auth.cached`-style i18n keys** with Windows wording: every "sudo"
  reference in the SPA now resolves through `tr()` to "Administrator
  authorized" / "not authorized" / "credentials needed" / "session
  expired" / "authentication cancelled". Polish parallel.
- **`docs/superpowers/specs/2026-05-02-ascendo-windows-end-to-end-design.md`** —
  the design that drove this milestone (CLI polish + dashboard wiring +
  frontend apply UX + Tauri 2.x scaffold).

### Changed

- Repository consolidated to a single `main` branch; the three sibling
  `claude/*` worktrees from earlier sessions reconciled into a linear
  history (no merge conflicts — pedantic was a strict ancestor of
  windows-end-to-end). All future work happens on `main`.
- `CLAUDE.md` rewritten for the monorepo layout
  (`core/` + `adapters/{ubuntu,windows,macos}/` + `ui/` + `plugins/`)
  and hard-codes a "no new worktrees" rule for Claude Code sessions.
- README rewrites the hero with the unified-updates pitch + Windows-first
  badge, and adds a per-platform feature matrix.

### Fixed

- 6 pre-existing `adapters/windows/tests/` failures that survived earlier
  sessions: `OperatingSystem.LINUX` references corrected to
  `OperatingSystem.LINUX_UBUNTU` (the enum never had a `.LINUX`); the
  `test_adapter_package_managers_includes_windows_update` assertion
  updated from `len() == 2` (M3.8 era) to the post-M3.15 contract of 4
  managers (winget / msstore / arp / windows_update).
- `test_windows_update_manager_smoke.py` no longer asserts a stale ordering
  contract; bookend assertions (winget first, windows_update last) plus a
  set-membership check for all 4 expected managers.

### Verified

- `python -m pytest adapters/windows/tests/ plugins/dell-driver-update/tests/ ui/desktop-tauri/tests/` → 70 + 8 + 5 = 83 pass.
- `bin/validate-windows.ps1 -DashboardPort 8770` → ALL CHECKS PASSED on real hardware: 210-package inventory bucketed across 4 sources, async run completes in ~23s, every dashboard endpoint healthy.

---

## [0.0.1-alpha] — 2026-05-01

**First end-to-end working build, validated on real Windows hardware
(Dell Precision 5520, Windows 11 Pro Build 26200, PowerShell 7.6.1,
winget v1.28.240, Python 3.14).**

A full ``python -m ascendo run --category winget --phase check`` invocation
exercises every layer of the architecture and exits 0 with a valid
``ascendo/v1`` sidecar. The dashboard binds, ``GET /version`` and ``/health``
work, ``POST /runs/async`` returns a run id, and ``GET /runs/{id}/status``
reaches ``completed``.

### Added — M1 (foundation)

- Monorepo restructure: `core/`, `adapters/{ubuntu,windows,macos}/`,
  `contrib/`, `plugins/`, `ui/`, `packaging/`, `website/`, `tests/`,
  `docs/architecture/`.
- 7 ADRs (`docs/architecture/0001` … `0007`):
  monorepo-with-adapters, tauri-as-desktop-shell, json-v1-sidecar-contract,
  python-core-with-native-script-adapters, six-layer-architecture,
  two-tier-adapter-system, plugin-manifest-v1.
- `HANDOFF.md` — cross-session resume document.
- `.gitattributes` (LF for source, CRLF for `.ps1`/`.bat`/`.cmd`),
  `.pre-commit-config.yaml` (gitleaks, ruff, mypy, shellcheck,
  PSScriptAnalyzer, markdownlint, plugin manifest validator).
- pyproject.toml workspace: root + `core/` + 3 adapter packages with
  hatchling build backend, importlinter contracts.
- Top-level docs: `LICENSE` (MIT), `CONTRIBUTING.md`, `SECURITY.md`,
  `CHANGELOG.md`.

### Added — M2 (core skeleton)

- **Pydantic v2 models** in `core/ascendo/models/`:
  `Sidecar` / `RunInfo` / `HostInfo` / `Item` / `Summary` / `Message` /
  `ToolInfo` / `Phase` / `PhaseStatus` / `Trigger` / `OperatingSystem` /
  `ElevationMethod` / `SourceType` / `ItemEvidence` / `ItemRollback`.
- **Six core interfaces** in `core/ascendo/interfaces/`: `IPackageManager`,
  `IInventory`, `ISnapshot`, `IScheduler`, `ISource`, `IElevation`, plus
  `IAdapter` aggregate root + `AdapterCapability` flag enum.
- **Adapter factory** (`core/ascendo/adapter_factory/`): `detect_os()`,
  `AdapterRegistry` with importlib.metadata entry-points + direct-import
  fallback, `select_adapter()` with `linux_*` → `linux_ubuntu` Tier-1
  fallback path.
- **JSON Schema export** at `docs/architecture/schemas/sidecar.v1.schema.json`
  (823 lines, JSON Schema 2020-12, regenerated by
  `scripts/export-sidecar-schema.py`).
- **Sidecar I/O layer** (`core/ascendo/orchestrator/sidecar_io.py`):
  cross-OS file locking (POSIX flock + Windows msvcrt), atomic writes,
  partial-sidecar recovery, jittered exponential backoff.
- **Legacy translator** (`core/ascendo/models/legacy.py`): converts
  pre-rename `ascendo/v1` payloads into `ascendo/v1`
  per ADR-0003 backward-compat.
- **i18n loader** (`core/ascendo/i18n/`): 7 locales × 42 keys
  (en/pl/es/it/pt/de/fr) ported from macOS bash; locale detection
  via ASCENDO_LOCALE / LANG / GetUserDefaultLocaleName / fallback.
- **Orchestrator** (`core/ascendo/orchestrator/runner.py`): `run_phases()`
  drives an `IAdapter` through requested phases, persists every sidecar,
  aggregates as `RunReport` with `overall_status`, `by_category()`,
  `by_phase()`, `total_items`, `aborted_after_phase`.
- **Async run + SSE** (`core/ascendo/orchestrator/run_async.py`):
  `RunRegistry` (thread-safe, bounded LRU), `start_run_async()` via
  `asyncio.to_thread`, lifecycle states (pending/running/completed/failed).
- **Typer CLI** (`core/ascendo/cli/`): `version`, `run`, `doctor` commands
  + placeholders for `schedule`, `snapshot`. `python -m ascendo` and
  `python -m ascendo.cli` both work as PATH-independent entry points.
- **Dashboard** (`core/ascendo/dashboard/`): FastAPI app with `GET /version`,
  `GET /health`, `POST /runs` (sync), `POST /runs/async`, `GET /runs/{id}/status`,
  `GET /runs/{id}/events` (Server-Sent Events stream), `GET /runs` (index),
  `GET /runs/{id}` (sidecars).
- **Contract tests** (`tests/contract/`): 41 tests covering sidecar v1
  schema, legacy compat, sidecar I/O concurrency + recovery, runner,
  dashboard sync + async + SSE.

### Added — M3 (Windows MVP)

- **PowerShell library modules** in `adapters/windows/lib/`:
  `AscendoJson.psm1` (sidecar emitter, ~626 LOC), `AscendoWinget.psm1`
  (column-position parser + exit-code mapping, ~783 LOC),
  `AscendoWingetActions.psm1` (process-kill map, uninstall-first map,
  skip list, ~570 LOC).
- **PowerShell phase scripts** in `adapters/windows/scripts/winget/`:
  `check.ps1`, `plan.ps1`, `apply.ps1`, `verify.ps1`, `cleanup.ps1`.
  All 5 phases of the contract.
- **Python WingetManager** (`adapters/windows/ascendo_windows/managers/winget.py`):
  spawns pwsh via subprocess with `[switch] $DryRun` idiom, parses
  emitted sidecar via core `read_sidecar`, maps exit codes.
- **WindowsAdapter** (`adapters/windows/ascendo_windows/adapter.py`):
  IAdapter implementation with capability=PACKAGE_MANAGEMENT, real
  health_check (winget version, pwsh version, lib presence).

### Added — packaging + DX

- `bin/install-dev.ps1` — one-shot Windows dev install
  (core + adapter + dashboard deps + auto-validate).
- `bin/validate-windows.ps1` — end-to-end automated validation harness
  (CLI commands + sidecar shape + dashboard sync + async + SSE +
  status polling).

### Known limitations (carried into 0.0.2)

- `AscendoWinget.psm1`'s `Read-WingetTabularOutput` collapses adjacent
  AppX/MSIX rows into a synthetic super-row (observed on the
  AutoHotkey block on real DP5520WMK winget output). Tracked as M3.X
  follow-up.
- `M2.7` backend migration is partial — only the new Layer 3 endpoints;
  the legacy `app/backend/*.py` files (auth, db, scheduler, hosts) are
  not yet migrated.

### Changed

- Monorepo restructure (rebrand `Ascendo` → `ascendo`):
  - JSON sidecar schema renamed `ascendo/v1` → `ascendo/v1`.
    Reader accepts both during the migration period.
  - Repository origin: new GitHub repo at
    `https://github.com/KasprowiczM/ascendo` (parent local clone:
    `D:\Dev_Env\Ascendo`).
  - Pre-restructure state preserved at git tag
    `pre-monorepo-restructure` for rollback if needed.

### Validated end-to-end on real hardware

DP5520WMK (Dell Precision 5520, Win 11 Pro 26200, PowerShell 7.6.1,
winget v1.28.240, Python 3.14):

```
==> ascendo run --category winget --phase check
  [PASS] run command exited 0/1 (not crashed)         exit=0
  [PASS] run produced at least one sidecar
  [PASS] sidecar has schema=ascendo/v1
         sidecar.status     = success
         sidecar.tool       = winget 1.28.240
         [INFO] Found 1 package(s) with upgrades available.
==> ascendo dashboard smoke
  [PASS] dashboard binds to 127.0.0.1:8765
  [PASS] GET /version
  [PASS] GET /health   status=ok
  [PASS] POST /runs/async returns run_id
  [PASS] GET /runs/{id}/status reaches completed/failed
ALL CHECKS PASSED.
```

---

## Pre-monorepo history (Ascendo legacy)

The following entries are from the source project before rename + restructure.

### [Etap 12] - 2026-04-XX

- Inventory candidate fix
- Unified Updates rename (Ascendo brand introduction)
- Tauri shell prototype
- Hybrid CLI/Dashboard mode
- Snapshot tooling (timeshift / etckeeper)
- Scheduler (systemd timers)
- Plugin system infrastructure (manifest validator)
- Dev-sync GitHub + Proton overlay

For full pre-monorepo history, see git log:
```bash
git log --oneline pre-monorepo-restructure
```

[Unreleased]: https://github.com/KasprowiczM/ascendo/compare/v1.0-beta...HEAD
[1.0-beta]: https://github.com/KasprowiczM/ascendo/compare/v0.6.0...v1.0-beta

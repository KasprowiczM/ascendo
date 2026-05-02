# Changelog

All notable changes to Ascendo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

(in flight — see ## [0.0.7] below for the next release candidate.)

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
  pre-rename `ubuntu-aktualizacje/v1` payloads into `ascendo/v1`
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

- Monorepo restructure (rebrand `Ubuntu_Aktualizacje` → `ascendo`):
  - JSON sidecar schema renamed `ubuntu-aktualizacje/v1` → `ascendo/v1`.
    Reader accepts both during the migration period.
  - Repository origin: new GitHub repo at
    `https://github.com/KasprowiczM/ascendo` (parent local clone:
    `D:\Dev_Env\Ubuntu_Aktualizacje`).
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

## Pre-monorepo history (Ubuntu_Aktualizacje legacy)

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

[Unreleased]: https://github.com/KasprowiczM/ascendo/compare/pre-monorepo-restructure...HEAD

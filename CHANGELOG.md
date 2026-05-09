# Changelog

All notable changes to Ascendo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

M6 — security audit (T1-T7 per ADR-0005), code signing across all three
OSes, plugin signing + verification, plugin marketplace UX in dashboard.

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

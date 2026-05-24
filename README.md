# Ascendo

**One CLI + Web + Desktop app to drive every package manager on every OS.**

Cross-platform unified-updates orchestrator. macOS · Windows · Linux. Open source, MIT.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Made for: Windows | Linux | macOS](https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-blue)](docs/PLATFORM_STATUS.md)
[![Tests: 477 green](https://img.shields.io/badge/tests-477%20green-brightgreen)](#tests)

---

## What it does

If you've ever opened the Microsoft Store, then `winget upgrade`, then
the Windows Update settings panel, then `pip list --outdated`, then a
vendor's bespoke driver updater — and still missed something — Ascendo
is for you.

Ascendo orchestrates every package manager you have (brew/mas/winget/
apt/snap/flatpak/npm/pip + DMG/Sparkle/Keystone web apps) through a
uniform `check → plan → apply → verify → cleanup` pipeline. It ships a
web dashboard, a native desktop shell, and a CLI for power users — all
backed by the same Python core, all writing the same JSON receipt
("sidecar") for every change so you can audit, replay, or roll back.

## Editions

| Edition | Who it's for | Features |
|---------|--------------|----------|
| **Basic** *(default)* | Everyday users — install, update, click-to-run | 5-destination AppShell: Dashboard, Library (Sources, Apps, Tools), Runs (Start, Scheduled, History), Insights, Settings (General, Help/About). Includes AI Tools chat. |
| **Dev** | Maintainers + contributors | Above + Sync, Hosts, raw-events stream, dev-sync overlay tooling, GitHub repo config, push capability |

The edition is recorded in `$ASCENDO_HOME/.ascendo-edition`; the
dashboard reads it on startup and gates UI surfaces accordingly.

## Install (one-liners)

Pick a row based on what you want. Re-running the same command updates
in place — every script is idempotent.

The **CLI + Web** path is the recommended install for everyone — the web
dashboard is a thin browser UI driven by the same Python core the CLI
uses (it imports `ascendo_<os>` directly), so installing the CLI gives
you both for free. The dashboard is **touch-first and fully responsive**
(375px phone → desktop): a 5-destination layout, no dropdown menus
(visible segmented / choice-card pickers with progressive reveal), a
mobile bottom tab bar, ≥44px tap targets, and high-contrast **light +
dark** themes. Pre-built native desktop installers (`.dmg` / `.msi` /
`.deb` standalone bundles) are **dev-only** today; see "Desktop installer
status" below.

### Basic edition (default — simplified UI for everyday use)

| Path | macOS / Linux | Windows |
|------|---------------|---------|
| **CLI + Web** *(recommended)* | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=basic ASCENDO_PROFILE=web bash` | `$Env:ASCENDO_EDITION='basic'; $Env:ASCENDO_PROFILE='web'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |
| **CLI only** *(headless / SSH)* | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=basic ASCENDO_PROFILE=cli bash` | `$Env:ASCENDO_EDITION='basic'; $Env:ASCENDO_PROFILE='cli'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |

### Dev edition (full feature set — for maintainers + contributors)

| Path | macOS / Linux | Windows |
|------|---------------|---------|
| **CLI + Web** | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=dev ASCENDO_PROFILE=web bash` | `$Env:ASCENDO_EDITION='dev'; $Env:ASCENDO_PROFILE='web'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |

Each installer auto-detects the OS, installs missing system deps
(Python ≥3.11, git, curl/winget), clones the repo to a per-user dir,
sets up a venv, pip-installs `core/` + the matching `adapters/<os>/`
editable, drops an `ascendo` shim plus the helper scripts on PATH, and
runs `ascendo doctor` as a self-test before declaring success.

After install, start the web UI any time:

```bash
ascendo web start          # spawns dashboard in background AND opens your browser
ascendo web stop           # graceful shutdown
ascendo web status         # is it running? what port? healthy?
ascendo web restart        # one-shot restart
```

### Desktop installer status (DMG / MSI / .deb)

Native desktop installers exist in-repo (`bin/build-dmg.sh`,
`bin/build-installer.ps1`, `packaging/build-deb.sh`) for development +
internal testing, but **are not part of the public release surface
yet**. Reasons per platform — full breakdown in
[docs/DMG_DISTRIBUTION.md](docs/DMG_DISTRIBUTION.md) (macOS) +
[WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md) §Desktop install
(Windows) + [LINUX_QUICKSTART.md](LINUX_QUICKSTART.md) §.deb (Linux).

If you're a contributor building locally:

```bash
bash bin/build-dmg.sh --edition=basic --profile=full   # macOS .dmg
pwsh ./bin/build-installer.ps1 -Edition basic          # Windows .msi + .exe
bash packaging/build-deb.sh                            # Ubuntu .deb
```

To **update** an existing install, re-run the same install one-liner
or use the dropped helper:

```bash
ascendo_update                      # macOS / Linux
ascendo_update.cmd                  # Windows (or just `ascendo_update`)
```

Equivalent direct one-liners + unattended/CI usage are documented in
the platform quickstarts below.

## Platforms

> **Pre-1.0 release.** APIs and CLI flags may still change between
> minor versions. macOS is the most mature; Windows + Linux are
> functional and at parity for inventory + check + apply but have
> fewer integrations wired (see the matrix below).

| Platform | Status | Quickstart |
|----------|--------|------------|
| macOS (Apple Silicon + Intel) | ✅ feature-complete (v0.6.8-rc1) — 12 health components, 6 package managers, Tier-A web auto-updates, Time Machine snapshots, launchd scheduler, Touch ID-first sudo | [MACOS_QUICKSTART.md](MACOS_QUICKSTART.md) |
| Windows 11 / 10 (build 17763+) | 🟡 functional (v0.6.x) — winget + msstore + ARP + PSWindowsUpdate + VSS + Task Scheduler + UAC. Parity with macOS still in progress (web auto-updates, Dell driver plugin shipped) | [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md) |
| Ubuntu 22.04+ / Debian 12+ | 🟡 functional (v0.6.x) — apt + snap + brew + npm + pip + flatpak inventory; legacy bash adapters bridged into Python orchestrator. Scheduler/snapshot/elevation Python wrappers pending | [LINUX_QUICKSTART.md](LINUX_QUICKSTART.md) |

See [docs/PLATFORM_STATUS.md](docs/PLATFORM_STATUS.md) for the full
per-feature matrix (which package managers, schedulers, snapshot
backends, and elevation methods are wired on each OS).

## Quickstart paths

A few common scenarios, each with a one-line command:

```bash
# Mac, just want to update everything once:
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
ascendo_maintenance full

# Dev machine, full feature set + Tauri desktop:
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \
  | ASCENDO_EDITION=dev ASCENDO_PROFILE=full bash

# Just want to look — health snapshot, no changes:
ascendo doctor

# Schedule a nightly safe-profile sweep (macOS launchd / Windows Task
# Scheduler / Linux systemd, same DSL):
ascendo schedule install --name nightly --calendar "DAILY 03:30" --profile safe
```

The day-to-day commands are the helper shims the installer drops on
PATH: `ascendo_start_web`, `ascendo_doctor`, `ascendo_maintenance`,
`ascendo_update`. See [USER_GUIDE.md](USER_GUIDE.md) for the full
walkthrough.

## Update

To pull the latest Ascendo + refresh editable installs:

```bash
# macOS / Linux — either of these works:
ascendo_update                                                                    # if helper is on PATH
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash

# Windows:
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex
```

Both `update.sh` and `update.ps1` are idempotent — they `git pull --ff-only`
the existing checkout, refresh the venv's editable installs, restart any
running dashboard, re-symlink helper shims, and self-test via
`ascendo doctor`. The edition (`basic` / `dev`) and profile chosen at
install time are preserved across updates; to switch editions, re-run
the install one-liner with a different `ASCENDO_EDITION`.

## Documentation

| For | Read |
|-----|------|
| End users | [USER_GUIDE.md](USER_GUIDE.md), platform quickstarts above |
| Contributors | [DEV_GUIDE.md](DEV_GUIDE.md), [CONTRIBUTING.md](CONTRIBUTING.md), [docs/architecture/](docs/architecture/) |
| Operators (releases) | [RELEASE_NOTES.md](RELEASE_NOTES.md), [CHANGELOG.md](CHANGELOG.md) |
| Security folks | [SECURITY.md](SECURITY.md) |
| Platform feature matrix | [docs/PLATFORM_STATUS.md](docs/PLATFORM_STATUS.md) |
| Cross-platform contract | [docs/CROSS_PLATFORM.md](docs/CROSS_PLATFORM.md) |
| Cross-machine sync (MacBook → Windows / Ubuntu) | [docs/CROSS_MACHINE_SYNC.md](docs/CROSS_MACHINE_SYNC.md) |
| Desktop installer status | [docs/DESKTOP_INSTALLER_STATUS.md](docs/DESKTOP_INSTALLER_STATUS.md) |

## Architecture

Monorepo with platform adapters. Six layers per
[ADR-0005](docs/architecture/0005-six-layer-architecture.md): vanilla
JS SPA → Tauri shell → FastAPI backend → OS-agnostic Python core →
per-OS Python adapter → native scripts (PowerShell on Windows, bash
on macOS / Linux). Every native script emits a JSON v1 sidecar per
the contract in [docs/agents/contract.md](docs/agents/contract.md);
the orchestrator parses, aggregates, and renders. Two-tier adapter
system ([ADR-0006](docs/architecture/0006-two-tier-adapter-system.md))
keeps community contributions cheap to land while the official
adapters maintain a higher bar.

```
adapters/{macos,windows,ubuntu}/  # per-OS managers + native scripts
core/ascendo/                     # OS-agnostic CLI, dashboard, orchestrator, models
ui/{frontend,desktop-tauri}/      # SPA + Tauri 2.x native shell
plugins/                          # first-party plugins (Dell DCU, NVIDIA, agent CLIs)
contrib/                          # Tier-2 community adapters + plugins
```

## License

[MIT](LICENSE) — do whatever you want, just keep the copyright notice.

## Acknowledgements

Ascendo evolved from three sibling projects of the same author:

- `Aktualizacje_MAC` — macOS shell scripts (foundation: i18n, DMG verification, session-dir patterns)
- `Aktualizacje-W11-Dell5520` — PowerShell on Windows (foundation: column parser, unknown-version suppression, exit-code mapping)
- `Ubuntu_Aktualizacje` — Linux/Ubuntu (foundation: Python backend, JSON v1 contract, plugin manifest, scheduler, snapshots, dev-sync)

Unifying them was an exercise in extracting common patterns and
respecting hard-won OS-specific knowledge.

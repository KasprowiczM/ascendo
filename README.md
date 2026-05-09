# Ascendo

> **Unified updates. Every app. One click.**
>
> Cross-platform update orchestrator for Windows, Linux, and macOS — with a
> branded Tauri 2.x desktop, a FastAPI dashboard, a CLI, snapshots, scheduler,
> and a plugin system. **Open source, MIT.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: macOS v0.5.2 + Windows v0.0.7 + Ubuntu Tier-1](https://img.shields.io/badge/status-macOS%20v0.5.2%20%7C%20Windows%20v0.0.7%20%7C%20Ubuntu%20Tier--1-green)](HANDOFF.md)
[![Made for: Windows | Linux | macOS](https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-blue)](README.md)
[![Tests: 841 / 848 green](https://img.shields.io/badge/tests-841%2F848%20green-brightgreen)](#tests)

---

## What is Ascendo?

If you've ever opened the Microsoft Store, then `winget upgrade`, then the
Windows Update settings panel, then `pip list --outdated`, then a vendor's
bespoke driver updater — and still missed something — Ascendo is for you.

**One tool that knows about every package source on your machine.** Scan
winget, Microsoft Store, Add/Remove Programs, and Windows Update in a
single 20-second sweep. See what's outdated with installed/candidate
versions side-by-side. Plan changes, dry-run them, snapshot before you
apply, then verify and clean up — all under one structured 5-phase
contract that writes a JSON receipt for every change.

| | Windows | Linux | macOS |
|---|---|---|---|
| OS updates | Windows Update (PSWindowsUpdate) | `apt full-upgrade` | `softwareupdate -ia -R` |
| Package managers | winget | apt, snap, brew, npm, pip, flatpak | brew, npm, pip |
| App stores | Microsoft Store + MSIX | (n/a) | Mac App Store |
| Drivers / firmware | Dell Command Update, fwupd | NVIDIA, fwupd | (n/a) |
| Snapshot backend | Volume Shadow Copy | timeshift / etckeeper | Time Machine |
| Scheduler | Windows Task Scheduler | systemd timer | launchd |
| Elevation | UAC (in-memory token) | sudo (askpass helper) | sudo + osascript |

All three OSes share the same Python core, FastAPI dashboard, vanilla SPA,
and Tauri 2.x desktop shell. What you learn on one platform is muscle
memory on the next.

## Status

| Platform | Adapter | CLI | Dashboard | Tauri shell | Installer | Released |
|---|---|---|---|---|---|---|
| macOS    | ✅ Tier-1 feature-complete (6 managers + scheduler + snapshot + elevation + inventory + 100% web coverage) | ✅ | ✅ | ✅ dev + unsigned `.app`/`.dmg` build | ✅ `install.sh` + `update.sh` one-liners | **v0.5.2** |
| Windows  | ✅ Tier-1 feature-complete (4 managers + scheduler + snapshot + elevation + inventory + Sesja-45 stderr capture + up_to_date guard) | ✅ | ✅ | ✅ dev + signed `.msi`/`.exe` build | ✅ NSIS + WiX MSI + `install.ps1` + `update.ps1` one-liners | **v0.0.7** |
| Linux    | ✅ Tier-1 Python adapter shipped Sesja 45 (UbuntuAdapter + 7 managers + IInventory enumeration; bridges to mature legacy bash scripts) | ✅ | ✅ | 🟡 needs polish | ✅ `.deb` + `install.sh` + `update.sh` one-liners | (rolling, M5+) |

See [`HANDOFF.md`](HANDOFF.md) for the live session log,
[`PLAN.md`](PLAN.md) for the forward roadmap, and
[`branding/SLOGANS.md`](branding/SLOGANS.md) for marketing copy
(installer banner, About modal, wizard welcome — single source of truth).

Target releases:

- ✅ **v0.0.7 — Windows MVP** (shipped 2026-05-02): MSI + NSIS installer,
  first-run wizard, Windows service, winget manifest.
- ✅ **v0.2.0 — macOS adapter feature-complete** (shipped 2026-05-05):
  brew + mas + softwareupdate + LaunchServices inventory + Time Machine
  snapshot list + launchd scheduler + sudo elevation. Tier-1 minus
  source-verification.
- ✅ **v0.3.0 — macOS web app updater** (shipped 2026-05-06): sixth
  IPackageManager — `WebManager` — covers ~24 apps installed outside
  brew/mas/softwareupdate via 7 update mechanisms (Sparkle, GitHub
  Releases, Keystone, Squirrel.Mac auto-relaunch, built-in updater,
  Microsoft AutoUpdate, Docker Desktop CLI). Pydantic-validated
  `_apps.toml` registry + user override. Defer-if-running per-handler.
- 🚧 **M6 hardening** — security audit (T1-T7), code signing across
  all three OSes, plugin signing + verification.
- **v1.0.0** — stable API + signed binaries + plugin marketplace.

## Install (one-liners)

Pick a row based on what you want. Re-running the same command updates in
place — every script is idempotent.

### Basic edition (default — simplified UI for everyday use)

| Profile | macOS / Linux | Windows |
|---------|---------------|---------|
| **CLI only** | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=basic ASCENDO_PROFILE=cli bash` | `$Env:ASCENDO_EDITION='basic'; $Env:ASCENDO_PROFILE='cli'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |
| **CLI + Web** | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=basic ASCENDO_PROFILE=web bash` | `$Env:ASCENDO_EDITION='basic'; $Env:ASCENDO_PROFILE='web'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |
| **CLI + Desktop** | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=basic ASCENDO_PROFILE=desktop bash` | `$Env:ASCENDO_EDITION='basic'; $Env:ASCENDO_PROFILE='desktop'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |
| **Full** (CLI + Web + Desktop) | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=basic ASCENDO_PROFILE=full bash` | `$Env:ASCENDO_EDITION='basic'; $Env:ASCENDO_PROFILE='full'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |

### Dev edition (full feature set — for maintainers + contributors)

Same matrix, swap `ASCENDO_EDITION=basic` for `ASCENDO_EDITION=dev`. Adds
the Sync tab, Hosts editor, raw events stream, dev-sync overlay tooling,
and the dev-only helper scripts under `bin/user-scripts/dev/`.

| Profile | macOS / Linux | Windows |
|---------|---------------|---------|
| **CLI only** | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=dev ASCENDO_PROFILE=cli bash` | `$Env:ASCENDO_EDITION='dev'; $Env:ASCENDO_PROFILE='cli'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |
| **Full** (CLI + Web + Desktop) | `curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh \| ASCENDO_EDITION=dev ASCENDO_PROFILE=full bash` | `$Env:ASCENDO_EDITION='dev'; $Env:ASCENDO_PROFILE='full'; iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 \| iex` |

**What's the difference?** *Basic* hides advanced surfaces (Sync, Hosts,
raw events) and ships only the end-user helper scripts; the dashboard
defaults aim at "click and go". *Dev* unlocks every feature toggle plus
contributor tooling (dev-sync overlay export/import, raw event stream,
dev helper scripts). The edition is recorded in
`$ASCENDO_HOME/.ascendo-edition` and the dashboard reads it on startup.

Each installer auto-detects the OS, installs missing system deps
(Python ≥3.11, git, curl/winget), clones the repo to a per-user dir,
sets up a venv, pip-installs `core/` + the matching `adapters/<os>/`
editable, drops an `ascendo` shim plus the helper scripts on PATH, and
runs `ascendo doctor` as a self-test before declaring success.

## Update

To update an existing installation, re-run the same install one-liner
(it's idempotent and detects the existing checkout) **or** run the
helper script that the installer just dropped on PATH:

```bash
ascendo_update              # macOS / Linux
ascendo_update.cmd          # Windows  (or just `ascendo_update`)
```

Equivalent direct one-liners:

```bash
# macOS / Linux:
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.sh | bash
```

```powershell
# Windows:
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/update.ps1 | iex
```

The updater fast-forwards your local checkout against `origin/main`,
re-runs the editable pip installs, refreshes dashboard deps if you have
the web profile, refreshes helper-script symlinks (in case new ones
shipped upstream), restarts any running `ascendo dashboard` (or the
`AscendoDashboard` Windows service), and prints a before → after
version delta. The edition (`basic` / `dev`) is preserved across
updates — change it only by re-installing with a different
`ASCENDO_EDITION`.

### Unattended / CI installs

All four scripts respect the same env vars:

```bash
ASCENDO_LANG=en \
ASCENDO_EDITION=basic \
ASCENDO_PROFILE=full \
ASCENDO_NONINTERACTIVE=1 \
  curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
```

```powershell
$env:ASCENDO_LANG = 'en'
$env:ASCENDO_EDITION = 'basic'
$env:ASCENDO_PROFILE = 'full'
$env:ASCENDO_NONINTERACTIVE = '1'
iwr -useb https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.ps1 | iex
```

Add `--reinstall` (POSIX) or `-Reinstall` (PowerShell) to wipe and
rebuild from scratch. `--verbose` / `-Verbose` traces every command.
Re-running an installer on an already-installed system upgrades it
(safe + idempotent); re-running an updater on a machine without an
existing install politely redirects you to the installer.

## Install (when v0.1.0 ships)

### Linux

```bash
# .deb (Debian / Ubuntu / Pop!_OS):
wget https://github.com/KasprowiczM/ascendo/releases/latest/download/ascendo_amd64.deb
sudo apt install ./ascendo_amd64.deb

# Arch Linux (AUR):
yay -S ascendo-bin

# Power users (headless, no Tauri UI):
pip install ascendo[ubuntu]
```

### Windows

```powershell
# Recommended (once v0.0.7 hits winget):
winget install --id Ascendo.Ascendo

# Direct .exe / .msi from GitHub Releases:
# Download Ascendo-0.0.7-x64-setup.exe (NSIS) or Ascendo-0.0.7-x64.msi (WiX)
# from https://github.com/KasprowiczM/ascendo/releases — double-click.

# Source / dev install today:
git clone https://github.com/KasprowiczM/ascendo.git D:\Dev_Env\Ascendo
cd D:\Dev_Env\Ascendo
.\bin\install-dev.ps1                  # core + adapters/windows + smoke
.\bin\install-shortcut.ps1             # Desktop + Start-menu icons
```

After install, launch from the Start menu (or `ascendo` from any shell).
The first-run wizard scans installed apps, shows what's outdated, and
walks you through a dry-run before any real upgrade. See
[`WINDOWS_QUICKSTART.md`](WINDOWS_QUICKSTART.md) for the operator guide.

### macOS

```bash
# Source / dev install today (v0.2.0):
git clone https://github.com/KasprowiczM/ascendo.git ~/Dev_Env/Ascendo
cd ~/Dev_Env/Ascendo
bash bin/install-dev-macos.sh             # core + adapter + smoke (≈ 3 min)
bash bin/launch-desktop-macos.sh          # native Tauri window (dev mode)

# Or just CLI:
python3 -m ascendo dashboard --port 8765  # http://127.0.0.1:8765
```

After install, see [`MACOS_QUICKSTART.md`](MACOS_QUICKSTART.md) for the
operator guide and [`MACOS_TESTING.md`](MACOS_TESTING.md) for the full
test matrix.

```bash
# Coming in v0.3.0 (signed brew tap):
brew install KasprowiczM/tap/ascendo
```

## Quick start

Three interfaces, all backed by the same orchestrator. Pick whichever
fits the moment:

```bash
# 1. CLI (best for scripting + cron / Task Scheduler / launchd)
python3 -m ascendo run --profile=quick     # read-only sweep, ~15 s
python3 -m ascendo run --profile=safe      # full 5-phase, no drivers
python3 -m ascendo run --profile=full      # everything (drivers gated)

# 2. Web app (best for visual exploration + ad-hoc apply with safety modal)
python3 -m ascendo dashboard --port 8765
# open http://127.0.0.1:8765/

# 3. Desktop app (best for daily use — same SPA, native window)
bash bin/launch-desktop-macos.sh           # macOS, dev mode
.\bin\launch-desktop.ps1                   # Windows, dev mode
```

Full walkthrough across all three interfaces in
[`USER_GUIDE.md`](USER_GUIDE.md).

## Architecture (high level)

```
┌─────────────────────────────────────────────┐
│ Frontend (vanilla JS SPA)                   │ ← same UI on all 3 OS
└──────────────┬──────────────────────────────┘
               │ HTTP/SSE
┌──────────────▼──────────────────────────────┐
│ Tauri shell (Rust) — native window per OS   │
└──────────────┬──────────────────────────────┘
               │ spawns
┌──────────────▼──────────────────────────────┐
│ FastAPI backend (Python) — REST + dashboard │
└──────────────┬──────────────────────────────┘
               │ delegates to
┌──────────────▼──────────────────────────────┐
│ Core domain (Python) — orchestrator + models│ ← OS-agnostic
└──────────────┬──────────────────────────────┘
               │ via interfaces
┌──────────────▼──────────────────────────────┐
│ Adapters (Python) — per-OS implementations  │
│   ubuntu / windows / macos                  │
└──────────────┬──────────────────────────────┘
               │ subprocess
┌──────────────▼──────────────────────────────┐
│ Native scripts (Bash / PowerShell)          │
│   apt / winget / brew / softwareupdate / ...│
└─────────────────────────────────────────────┘
```

See [`docs/architecture/`](docs/architecture/) for the full
ADR-driven architecture.

## Repository structure

```
ascendo/
├── core/                   # Python core (OS-agnostic)
├── adapters/               # Tier 1 official: ubuntu, windows, macos
├── plugins/                # Tier 1 plugins (agent-clis, dell, nvidia, _template)
├── contrib/                # Tier 2 community contributions
├── ui/                     # frontend SPA + Tauri desktop shell
├── packaging/              # .deb, MSI, .pkg, brew tap, winget manifest, PyInstaller
├── website/                # landing page (GitHub Pages)
├── docs/                   # architecture ADRs + author guides
├── tests/                  # cross-cut + contract + fixtures + integration
├── branding/               # icon.svg, logo.svg, palette
└── HANDOFF.md              # current implementation state
```

## Contributing

Ascendo is open to contributions — adapters, plugins, translations, docs,
and bug reports. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first.

Quick paths:

- **Add a plugin** — copy `plugins/_template/`, see
  [`docs/plugin-author-guide.md`](docs/plugin-author-guide.md)
- **Add an OS** — start in `contrib/adapters/<os>/` (Tier 2), see
  [`docs/adapter-author-guide.md`](docs/adapter-author-guide.md)
- **Add a translation** — extend `core/ascendo/i18n/locales/`, see
  [`docs/i18n-author-guide.md`](docs/i18n-author-guide.md)

## License

[MIT](LICENSE) — do whatever you want, just keep the copyright notice.

## Acknowledgements

Ascendo evolved from three sibling projects of the same author:

- `Aktualizacje_MAC` — macOS shell scripts (foundation: i18n, DMG verification, session dir patterns)
- `Aktualizacje-W11-Dell5520` — PowerShell on Windows (foundation: column parser, unknown-version suppression, exit-code mapping)
- `Ubuntu_Aktualizacje` — Linux/Ubuntu (foundation: Python backend, JSON v1 contract, plugin manifest, scheduler, snapshots, dev-sync)

Unifying them was an exercise in extracting common patterns and respecting
hard-won OS-specific knowledge.

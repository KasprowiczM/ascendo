# Ascendo

> **Unified updates. Every app. One click.**
>
> Cross-platform update orchestrator for Windows, Linux, and macOS — with a
> branded Tauri 2.x desktop, a FastAPI dashboard, a CLI, snapshots, scheduler,
> and a plugin system. **Open source, MIT.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Windows v0.0.7](https://img.shields.io/badge/status-Windows%20v0.0.7-green)](HANDOFF.md)
[![Made for: Windows | Linux | macOS](https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-blue)](README.md)
[![Tests: 70+8+5 green](https://img.shields.io/badge/tests-83%20green-brightgreen)](#tests)

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
| Windows  | ✅ MVP | ✅ | ✅ | ✅ | 🟡 in flight (v0.0.7) | tag pending |
| Linux    | ✅ legacy code (migrating into `adapters/ubuntu/`) | ✅ | ✅ | 🟡 needs polish | ✅ `.deb` | v0.5 |
| macOS    | 🟡 stub (`adapters/macos/`) | 🟡 | 🟡 | 🟡 | 🟡 | — |

See [`HANDOFF.md`](HANDOFF.md) for the live session log,
[`PLAN.md`](PLAN.md) for the forward roadmap, and
[`branding/SLOGANS.md`](branding/SLOGANS.md) for marketing copy
(installer banner, About modal, wizard welcome — single source of truth).

Target releases:

- **v0.0.7 — Windows MVP** (in flight): MSI + NSIS installer, first-run
  wizard, Windows service, winget manifest.
- **v0.1.0 — Windows + Linux feature parity** under the new monorepo.
- **v0.2.0 — macOS adapter** (full 3-OS support).
- **v1.0.0** — security audit + code signing + stable API.

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
# Recommended:
brew install KasprowiczM/tap/ascendo

# Or .dmg direct download from GitHub Releases.
```

## Quick start

After installing, launch the dashboard:

```bash
ascendo dashboard       # opens local web UI in your browser
```

Or run a one-shot update cycle from the CLI:

```bash
ascendo run --profile=safe       # check + plan + apply (no risky drivers)
ascendo run --profile=quick      # read-only health check (~15s)
ascendo run --profile=full       # everything including drivers
```

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

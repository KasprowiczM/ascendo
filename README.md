# Ascendo

> **Unified updates. Every app. One click.**
>
> Cross-platform update orchestrator for Windows, Linux, and macOS — with a
> branded Tauri 2.x desktop, a FastAPI dashboard, a CLI, snapshots, scheduler,
> and a plugin system. **Open source, MIT.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: macOS v0.2.0 + Windows v0.0.7](https://img.shields.io/badge/status-macOS%20v0.2.0%20%7C%20Windows%20v0.0.7-green)](HANDOFF.md)
[![Made for: Windows | Linux | macOS](https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-blue)](README.md)
[![Tests: 242 macOS + 158 Windows green](https://img.shields.io/badge/tests-400%2B%20green-brightgreen)](#tests)

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
| macOS    | ✅ Tier-1 feature-complete (6 managers + scheduler + snapshot + elevation + inventory) | ✅ | ✅ | ✅ dev + unsigned `.app`/`.dmg` build | 🟡 brew tap (planned) | **v0.3.0** |
| Windows  | ✅ Tier-1 feature-complete (4 managers + scheduler + snapshot + elevation + inventory) | ✅ | ✅ | ✅ dev + signed `.msi`/`.exe` build | ✅ NSIS + WiX MSI | **v0.0.7** |
| Linux    | ✅ legacy code (migrating into `adapters/ubuntu/`) | ✅ | ✅ | 🟡 needs polish | ✅ `.deb` | (rolling, M5+) |

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

## Quick install (one-liner — macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/KasprowiczM/ascendo/main/install.sh | bash
```

The installer:

1. Detects your OS (macOS / Ubuntu+Debian / Fedora / Arch).
2. Asks for language (`en` / `pl`) and persists it to
   `~/.config/ascendo/locale.txt`.
3. Installs missing system dependencies via your OS package manager
   (`brew` / `apt` / `dnf` / `pacman`) — printing every `sudo` call
   before invoking it.
4. Asks for an install profile:
   1. **CLI only** — fastest, sparse-checkout, ~30 MB.
   2. **CLI + Web dashboard** — adds FastAPI + uvicorn.
   3. **CLI + Web + Desktop** — adds Rust toolchain + Node 18+ + Tauri 2.x.
5. Clones (or pulls) the repo to `~/.local/share/ascendo`, sets up a venv
   under `.venv/`, pip-installs `core/` + `adapters/<os>/` editable, and
   symlinks an `ascendo` shim into `~/.local/bin/`.
6. Prints profile-tailored next steps.

Re-running the script is safe: it pulls instead of re-cloning. Windows
users: see the **Windows** section below; the curl|bash one-liner is
not available on PowerShell yet.

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

# Ascendo

> Cross-platform update orchestrator for Linux, Windows, and macOS — with a
> web dashboard, scheduler, snapshots, and a plugin system. **Open source, MIT.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: pre-release](https://img.shields.io/badge/status-pre--release-orange)](HANDOFF.md)
[![Made for: Linux | Windows | macOS](https://img.shields.io/badge/OS-Linux%20%7C%20Windows%20%7C%20macOS-blue)](README.md)

---

## What is Ascendo?

Ascendo is **one tool to keep your machine up-to-date** — across operating
systems, package managers, and software sources. Manage:

- **OS updates** (Windows Update, `apt full-upgrade`, `softwareupdate -ia -R`)
- **Native package managers** (`apt`, `winget`, `brew`, `snap`, `flatpak`)
- **App stores** (Microsoft Store, Mac App Store)
- **Cross-OS dev tools** (`npm`, `pip`, `pipx`)
- **Drivers / firmware** (Dell Command Update, NVIDIA, fwupd) — via plugins
- **AI agent CLIs** (Claude Code, Codex, Gemini, Qwen, OpenCode) — via plugin

Through one CLI (`ascendo run`) and one local dashboard
(`http://127.0.0.1:8765/`).

## Status

Ascendo is currently **pre-release** — under active reorganization in branch
`restructure/monorepo`. See [`HANDOFF.md`](HANDOFF.md) for the current
implementation state and roadmap.

Target releases:

- **v0.1.0** — Linux + Windows MVP (Tauri UI + winget + apt + plugins)
- **v0.2.0** — macOS adapter (full 3-OS support)
- **v1.0.0** — security audit + code signing + stable API

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
# Recommended:
winget install Ascendo.Ascendo

# Or direct MSI from GitHub Releases.
```

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

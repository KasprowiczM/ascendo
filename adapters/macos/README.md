# adapters/macos/

Tier 1 (official) macOS adapter for Ascendo. Source of native bash scripts
is the legacy `D:\Dev_Env\Ascendo` repository, refactored to the
5-phase contract.

**Status: deferred to Milestone M5** (after v0.1.0 Linux+Windows release).

## Supported categories (planned)

- `system` — macOS system updates via `softwareupdate -ia -R` (CRITICAL `-R` flag)
- `appstore` — Mac App Store via `mas upgrade` (sudo required, CVE-2025-43411)
- `internet_apps` — DMG / Keystone / Sparkle / direct URL apps (refactored from monolithic update_internet_apps.sh)
- `brew` — Homebrew (formulas + casks)
- `npm` — npm globals (cross-OS)
- `inventory` — generates APPLICATIONS.md / UPDATES.md
- `drivers` — none (macOS handles drivers via system updates)

## Critical macOS-specific lessons (from legacy repo)

- **`softwareupdate` REQUIRES `-R` flag** — without it, updates download but never apply
- **`mas` REQUIRES `sudo` on macOS 26+** (CVE-2025-43411 / Sequoia)
- **Bash 3.2 only** — no `declare -A`, no `mapfile`, no `readarray`
- **Homebrew prefix detection** — `/opt/homebrew/bin/` (Apple Silicon) vs `/usr/local/bin/` (Intel)
- **DMG verification chain** — `hdiutil verify` → `spctl --assess` → `pkgutil --check-signature`
- **AppleScript fallback** — for Mac App Store iPad apps (UniFi, WiFiman, Picsart)
- **Time Machine snapshots are read-only via API** — can list (`tmutil listlocalsnapshots /`) but cannot create

## Big M5 refactor: update_internet_apps.sh (1460 LOC)

The legacy `update_internet_apps.sh` is monolithic — 36+ apps inline as
`case` statements. M5 refactors into:

```
adapters/macos/scripts/internet_apps/
├── apply.sh                    # orchestration only
├── _apps.toml                  # declarative app list (per-app metadata)
└── handlers/                   # one handler per update mechanism
    ├── github_dmg.sh
    ├── keystone.sh
    ├── sparkle.sh
    └── direct_url.sh
```

Each handler reads `_apps.toml` for apps using its mechanism. New apps
become a TOML entry instead of new bash code.

## System dependencies

- macOS 12+ (Monterey, Tauri minimum)
- Bash 3.2 (system shell, never assume newer features)
- Python 3.11+ (bundled via PyInstaller in .pkg)
- Homebrew (auto-installed by setup wizard if missing)
- mas CLI (auto-installed)
- Apple Silicon: arm64 universal2 builds

## Migration source

- `D:\Dev_Env\Ascendo\` — ALL logic preserved
- `update_all.sh` → archived (orchestration in Python core)
- `update_system.sh` → `scripts/system/`
- `update_appstore.sh` → `scripts/appstore/`
- `update_internet_apps.sh` → `scripts/internet_apps/` + handlers (BIG REFACTOR)
- `update_npm_cli.sh` → `plugins/agent-clis/macos/`
- `update_brew.sh` → `scripts/brew/`
- `migration_setup.sh` → `core/ascendo/cli/commands/setup.py`
- `i18n/*.sh` → already ported to `core/ascendo/i18n/locales/*.json` in M2

## See also

- `HANDOFF.md` "Migration plan" → Faza G (macOS)
- `docs/adapter-author-guide.md`

# packaging/

Build pipeline configurations for distributing Ascendo across channels.

## Structure

```
packaging/
├── deb/                     # Debian/Ubuntu .deb staging tree
│   ├── DEBIAN/{control,postinst,prerm,postrm}
│   ├── opt/ascendo/         # populated by build-deb.sh from `git ls-files`
│   ├── usr/bin/             # legacy shims (ascendo)
│   └── usr/local/bin/       # populated by build-deb.sh from bin/user-scripts/*
├── pyinstaller/             # Python sidecar bundling (Win/macOS)
│   ├── ascendo.spec         # PyInstaller spec (one-folder)
│   └── ascendo_entry.py     # Entry-point shim (wires _MEIPASS env vars)
├── installer-assets/        # NSIS + WiX branded BMPs and NSIS hook stubs
│   ├── generate-installer-images.py
│   ├── installer-banner-nsis.bmp   # 150x57
│   ├── installer-sidebar-nsis.bmp  # 164x314
│   ├── installer-banner-wix.bmp    # 493x58
│   ├── installer-sidebar-wix.bmp   # 493x312
│   └── nsis-installer-hooks.nsh    # PRE/POST install/uninstall + bootstrap
├── msi/                     # (reserved — Tauri WiX template tweaks)
├── pkg/                     # macOS .pkg postinstall scripts (deferred)
├── homebrew-tap/
│   └── ascendo.rb           # Homebrew formula (publishes to KasprowiczM/homebrew-tap)
└── winget-manifest/         # YAML manifest for microsoft/winget-pkgs PR
└── build-deb.sh             # canonical .deb builder
```

## Distribution channels

| OS | Primary | Secondary | Tertiary |
|---|---|---|---|
| Linux | `.deb` (GitHub Releases) | AUR (`ascendo-bin`) | `pip install ascendo`, `.AppImage` |
| Windows | `winget install Ascendo.Ascendo` | MSI direct download | NSIS (`*-setup.exe`), portable ZIP |
| macOS | `.dmg` direct download | `brew install KasprowiczM/tap/ascendo` | `pip install ascendo` |

## Build matrix — at a glance

| Target | Build script | Runs on | Output |
|--------|--------------|---------|--------|
| `.dmg` (macOS) | `bash bin/build-dmg.sh` | macOS only | `dist/Ascendo-<v>-<arch>.dmg` |
| `.msi` + `.exe` (Windows) | `pwsh -File bin/build-installer.ps1` | Windows only | `dist/Ascendo-<v>-x64.msi`, `dist/Ascendo-<v>-x64-setup.exe` |
| `.deb` (Linux) | `bash packaging/build-deb.sh` | Linux (or any OS for staging; dpkg-deb required for actual build) | `dist/ascendo_<v>_all.deb` |
| Homebrew formula | manual edit + tap-bump CI | any | tap repo: `Formula/ascendo.rb` |

All three native builders share the same "smart bootstrap" model: the
shipped artifact contains a tiny first-run script that, on the user's
machine, checks for Python ≥ 3.11 / git / curl, installs missing deps via
the local OS pkg manager, and runs `install.sh` / `install.ps1` to set up
the per-user editable Python install. The end user does not need to know
anything about the underlying tech stack.

## Smart bootstrap scripts

| File | Triggered by | What it does |
|------|--------------|--------------|
| `bin/first-run-bootstrap-macos.sh` | DMG: bundled in Ascendo.app's Resources/, exec'd by Tauri shell on first launch | brew-detect + brew-install Python/git/curl/jq, run install.sh, verify with `ascendo doctor`, write `~/Library/Application Support/Ascendo/.bootstrapped` |
| `bin/first-run-bootstrap-linux.sh` | DEB: invoked by helper shims (e.g. on first `ascendo` run); .desktop file Exec= line | check pre-installed deps (already pulled by .deb), run install.sh `--non-interactive`, verify, write `~/.ascendo/.bootstrapped` |
| `bin/first-run-bootstrap-windows.ps1` | MSI/NSIS: NSIS_HOOK_POSTINSTALL, MSI Custom Action | winget-detect + winget-install Python/git/curl, download install.ps1, run, verify, write `%LOCALAPPDATA%\Ascendo\.bootstrapped` |

All three are idempotent — re-runs skip the bootstrap unless `--force` (or
absence of marker file) is passed.

---

## Building macOS DMG

One command:

```bash
bash bin/build-dmg.sh
```

Produces `dist/Ascendo-<version>-<arch>.dmg` (e.g.
`dist/Ascendo-0.0.7-arm64.dmg`). Prints SHA256 and runs `hdiutil verify`
on the final artefact.

### Smart features

* **Toolchain check up front** — fails early with actionable install hints
  if rust/node/cargo/xcrun are missing.
* **Resilient DMG packaging** — prefers `create-dmg` (Homebrew) for the
  pretty Finder layout, automatically falls back to plain `hdiutil` when
  create-dmg's AppleScript step times out (a known macOS 14+ flake).
* **Smart bootstrap shipped in Resources/** — `bin/first-run-bootstrap-macos.sh`
  is mirrored into `ui/desktop-tauri/src-tauri/` (and listed under
  `bundle.resources` in `tauri.conf.json`) so it ends up at
  `Ascendo.app/Contents/Resources/first-run-bootstrap-macos.sh`.

### Flags

| Flag | Effect |
|------|--------|
| `--skip-tauri` | Re-use existing .app bundle (fast iteration on DMG cosmetics) |
| `--skip-deps` | Forwarded to `launch-desktop-macos.sh` (no `npm install`) |
| `--no-sign` | Skip codesign even if `$APPLE_CERT_NAME` is set |
| `--no-notarize` | Skip notarization even if notary credentials are set |
| `--output=<path>` | Override DMG output path |
| `--dry-run` | Print plan, do nothing |

### Codesign + notarization (optional)

```bash
export APPLE_CERT_NAME="Developer ID Application: Your Name (TEAMID)"
export APPLE_NOTARY_USER="you@apple.example"
export APPLE_NOTARY_PASSWORD="abcd-efgh-ijkl-mnop"   # app-specific password
export APPLE_NOTARY_TEAM="TEAMID"
bash bin/build-dmg.sh
```

Without `$APPLE_CERT_NAME` the DMG ships unsigned and Gatekeeper will warn
end users on first launch. Without `$APPLE_NOTARY_*` it's not stapled, so
even signed apps need a one-time right-click → Open. Recommended for
public release: set both.

---

## Building the Windows installers

One command:

```powershell
pwsh -File bin\build-installer.ps1
```

Produces both artifacts at the repo root:

```
dist/Ascendo-0.0.7-x64.msi          # WiX-built MSI (managed/scriptable installs)
dist/Ascendo-0.0.7-x64-setup.exe    # NSIS interactive installer
```

The script also prints SHA256 of each artifact at the end. Run from any
PowerShell window (no elevation needed for the build itself; the produced
installers prompt for UAC at install-time when running per-machine).

### What the build does

1. **PyInstaller** — `python -m PyInstaller packaging/pyinstaller/ascendo.spec`
   produces `dist/pyinstaller/ascendo/ascendo.exe` plus `_internal/`.
2. **Sidecar staging** — copies the PyInstaller output into
   `ui/desktop-tauri/src-tauri/binaries/python-sidecar/`. Tauri ships
   the whole tree under the install dir.
3. **Installer assets** — banner BMPs and NSIS hooks mirrored into
   `src-tauri/installer-assets/` (Tauri's path resolver chokes on `..`
   crossing the workspace root).
4. **bin/ staging** — `first-run-bootstrap-windows.ps1` + `install-service.ps1`
   + `Ascendo.cmd` + `bin/user-scripts/*` mirrored into
   `src-tauri/bin-staging/` and listed under `bundle.resources` so they
   end up at `$INSTDIR\resources\bin-staging\` on the target machine.
5. **Tauri build** — `npm run tauri build` (delegated through
   `bin/launch-desktop.ps1 -Build`). Produces both .msi and .exe under
   `target/release/bundle/{msi,nsis}/`.
6. **Artifact rename + SHA256** — copies the produced files to
   `dist/Ascendo-<version>-x64.{msi,exe}` and prints SHA256.

### NSIS post-install hooks

`packaging/installer-assets/nsis-installer-hooks.nsh` defines four
macros that Tauri's NSIS template invokes at well-known points:

* `NSIS_HOOK_POSTINSTALL` — runs `first-run-bootstrap-windows.ps1`
  in non-interactive mode, then optionally registers AscendoDashboard
  as a Windows service (env var `ASCENDO_INSTALL_AS_SERVICE=1`).
* `NSIS_HOOK_PREUNINSTALL` — tears down the service if present
  (idempotent, never blocks uninstall).
* `NSIS_HOOK_POSTUNINSTALL` — offers (interactive) / honors
  (`ASCENDO_PURGE_USER_DATA=1`) the option to also delete
  `%LOCALAPPDATA%\Ascendo\` (default: kept across re-installs).

### Skip flags (iteration shortcuts)

| Flag | Effect |
|------|--------|
| `-SkipPyInstaller` | Re-use existing `dist/pyinstaller/ascendo/` |
| `-SkipTauri`       | Re-use existing Tauri output (just re-stage and re-copy) |
| `-SkipDeps`        | Skip `npm install` (forwarded to launch-desktop.ps1) |
| `-SkipPrereqCheck` | Skip Rust/MSVC/Node detection (forwarded) |

Cold first-run takes ~15 minutes (Rust crate compilation dominates).
Subsequent builds with `-SkipPyInstaller -SkipDeps -SkipPrereqCheck`
take ~3 min.

### Code signing

The build does NOT sign anything by default. Two options:

```powershell
# Option A — Authenticode (manual)
$ts = "http://timestamp.digicert.com"
foreach ($a in @("dist\Ascendo-0.0.7-x64.msi", "dist\Ascendo-0.0.7-x64-setup.exe")) {
    signtool sign /fd sha256 /tr $ts /td sha256 /a $a
    signtool verify /pa /v $a
}
```

Option B (recommended for CI): Azure Trusted Signing. See
`docs/architecture/0008-distribution-strategy.md`.

### Pre-seeding for unattended installs

```powershell
setx ASCENDO_INSTALL_AS_SERVICE 1   # opt into AscendoDashboard service
setx ASCENDO_EDITION basic           # or 'dev'
setx ASCENDO_PROFILE full            # or 'cli'/'web'/'desktop'
Ascendo-0.0.7-x64-setup.exe /S       # silent install honors all of above
```

---

## Building the Linux .deb

One command:

```bash
bash packaging/build-deb.sh
```

Produces `dist/ascendo_<version>_all.deb`. Prints SHA256 of the artefact.

### What the build does

1. **Resolve version** — reads `core/ascendo/__version__.py` and bumps
   `DEBIAN/control`'s `Version:` field on the fly so a single source of
   truth is honored.
2. **Stage tracked files** — copies everything from `git ls-files` into
   `packaging/deb/opt/ascendo/`, skipping `packaging/`, `dist/`,
   `node_modules/`, and `target/`.
3. **Generate user-script shims** — for every script in `bin/user-scripts/*`
   (excluding `.ps1` mirrors), creates a tiny dispatcher at
   `packaging/deb/usr/local/bin/<name>` that forwards to
   `/opt/ascendo/bin/user-scripts/<name>`. End users get `ascendo`,
   `ascendo_doctor`, `ascendo_update`, `ascendo_start_web`, etc. on PATH.
4. **dpkg-deb --build** — produces `dist/ascendo_<version>_all.deb`
   (Architecture: all because Python is interpreted).

### Smart .deb features

* **Strict deps** — `Depends:` enumerates `bash (>= 5.0)`, `python3 (>= 3.11)`,
  `python3-pip`, `python3-venv`, `git`, `curl`, `jq`, `ca-certificates`,
  `util-linux`, `sudo` so apt resolves them automatically.
* **postinst** sets file perms, plants `/etc/ascendo/preseed.conf` from
  `/etc/ascendo/install.conf` (admin-managed), refreshes desktop database,
  prints next-steps banner.
* **prerm** stops user-level dashboard systemd services across all real
  users (UID ≥ 1000) without erroring on absence.
* **postrm purge** offers per-user state cleanup hints (we never delete
  `~/.ascendo` automatically — user might want history across re-installs).
* **first-run bootstrap** at `/opt/ascendo/bin/first-run-bootstrap-linux.sh`
  is idempotent; runs on first `ascendo` invocation per user, sets up
  per-user venv via `install.sh --non-interactive`.

### Flags

| Flag | Effect |
|------|--------|
| `--dry-run` | Stage + lint only, don't dpkg-deb --build |
| `--no-symlinks` | Skip generating `/usr/local/bin/*` shims |

### Pre-seeding for unattended deployment

Drop `/etc/ascendo/install.conf` BEFORE installing the .deb:

```ini
# /etc/ascendo/install.conf — KEY=VALUE
ASCENDO_EDITION=dev          # basic | dev
ASCENDO_PROFILE=full         # cli | web | desktop | full
ASCENDO_LANG=pl
```

Then `sudo dpkg -i ascendo_*_all.deb`. The postinst plants
`/etc/ascendo/preseed.conf` from these values; first-run bootstrap reads
the preseed and runs install.sh with the matching flags.

---

## Homebrew formula

`packaging/homebrew-tap/ascendo.rb` is the source-of-truth Ruby formula.
The actual tap lives in a separate repo:
`https://github.com/KasprowiczM/homebrew-tap` (Homebrew convention: tap
repo names MUST start with `homebrew-`).

### Auto-bumping on release

`.github/workflows/release.yml` (deferred) will use the
`dawidd6/action-homebrew-bump-formula` action to:

1. Compute the SHA256 of the new release tarball.
2. Edit `Formula/ascendo.rb` in the tap repo to bump `version`, `url`,
   `sha256`.
3. Open a PR against the tap repo (auto-merged after CI passes).

Until that workflow lands, bump the formula manually:

```bash
# In the homebrew-tap repo:
sha256=$(curl -sL https://github.com/KasprowiczM/ascendo/archive/refs/tags/v0.0.7.tar.gz | shasum -a 256 | cut -d' ' -f1)
# Edit Formula/ascendo.rb's url and sha256 fields, commit, push.
brew install KasprowiczM/tap/ascendo
```

---

## winget submission

`winget-manifest/Ascendo.Ascendo.{installer,locale.en-US,version}.yaml`
hold the Microsoft Package Manager manifest. After running
`bin/build-installer.ps1`:

1. Replace `<FILL_AT_RELEASE>` with the SHA256 from the build script.
2. Replace `<RELEASE_URL_*>` with the GitHub Release asset URLs.
3. Submit:
   ```bash
   winget submit ./Ascendo.Ascendo.installer.yaml
   ```
   Or open a PR against `microsoft/winget-pkgs` with all three files.

---

## See also

- `docs/architecture/0008-distribution-strategy.md` — channel rationale
- `bin/build-dmg.sh` — macOS DMG pipeline
- `bin/build-installer.ps1` — Windows MSI/NSIS pipeline
- `packaging/build-deb.sh` — Linux .deb pipeline
- `bin/first-run-bootstrap-{macos,linux,windows}.{sh,sh,ps1}` — smart deps
- `ui/desktop-tauri/src-tauri/tauri.conf.json` — Tauri MSI/NSIS/DMG config
- `.github/workflows/release.yml` — GitHub Releases automation (deferred)

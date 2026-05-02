# packaging/

Build pipeline configurations for distributing Ascendo across channels.

## Structure

```
packaging/
├── deb/                     # Debian/Ubuntu .deb (extends packaging/build-deb.sh)
├── pyinstaller/             # Python sidecar bundling (Win/macOS)
│   ├── ascendo.spec         # PyInstaller spec (one-folder)
│   └── ascendo_entry.py     # Entry-point shim (wires _MEIPASS env vars)
├── installer-assets/        # NSIS + WiX branded BMPs and NSIS hook stubs
│   ├── generate-installer-images.py
│   ├── installer-banner-nsis.bmp   # 150x57
│   ├── installer-sidebar-nsis.bmp  # 164x314
│   ├── installer-banner-wix.bmp    # 493x58
│   ├── installer-sidebar-wix.bmp   # 493x312
│   └── nsis-installer-hooks.nsh    # PRE/POST install/uninstall macros
├── msi/                     # (reserved — Tauri WiX template tweaks)
├── pkg/                     # macOS .pkg postinstall scripts (deferred to M5)
├── homebrew-tap/            # Homebrew formula (deferred to M5)
└── winget-manifest/         # YAML manifest for microsoft/winget-pkgs PR (sub-project 5)
```

## Distribution channels

| OS | Primary | Secondary | Tertiary |
|---|---|---|---|
| Linux | `.deb` (GitHub Releases) | AUR (`ascendo-bin`) | `.AppImage` portable, `pip install ascendo` |
| Windows | `winget install Ascendo.Ascendo` | MSI direct download | NSIS (`*-setup.exe`), portable ZIP |
| macOS | `brew install KasprowiczM/tap/ascendo` | `.dmg` direct download | `pip install ascendo` |

## Building the Windows installers

One command:

```powershell
pwsh -File bin\build-installer.ps1
```

Produces both artifacts at the repo root:

```
dist/Ascendo-0.0.7-x64.msi          # WiX-built MSI for managed/scriptable installs
dist/Ascendo-0.0.7-x64-setup.exe    # NSIS interactive installer with welcome/license/component pages
```

The script also prints SHA256 of each artifact at the end. Run it in
an elevated PowerShell only if you intend to actually install the
result on the same box; building itself does not need elevation.

### What the build does

1. **PyInstaller** — `python -m PyInstaller packaging/pyinstaller/ascendo.spec`
   produces `dist/pyinstaller/ascendo/ascendo.exe` plus its `_internal/`
   runtime folder. The bundle includes the Python interpreter, FastAPI,
   uvicorn, the Ascendo core + Windows adapter packages, the SPA
   frontend (`app/frontend/`), branding assets, and the PowerShell
   modules under `adapters/windows/{scripts,lib}/` (re-rooted at
   `_internal/ascendo_windows_resources/`).
2. **Sidecar staging** — `bin/build-installer.ps1` copies the
   PyInstaller output into
   `ui/desktop-tauri/src-tauri/binaries/python-sidecar/`. Tauri picks
   it up via `bundle.resources` in `tauri.conf.json` and ships the
   whole tree under `<install_dir>/binaries/python-sidecar/` in the
   final MSI/NSIS output.
3. **Installer assets** — also mirrored into
   `ui/desktop-tauri/src-tauri/{installer-assets,LICENSE}` because
   Tauri's path resolver chokes on `..` segments crossing the
   workspace root. Both mirrors are `.gitignored`.
4. **Tauri build** — `npm run tauri build` (delegated through
   `bin/launch-desktop.ps1 -Build`). Cross-compiles the Rust shell
   (`ui/desktop-tauri/src-tauri/`) and invokes WiX 3 + NSIS 3 to
   produce the artifacts.
5. **Artifact rename + SHA256** — copies the produced files to
   `dist/Ascendo-<version>-x64.msi` and
   `dist/Ascendo-<version>-x64-setup.exe`.

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

## Code signing

**The build does NOT sign anything by default.** The artifacts will
trip Windows SmartScreen on a clean machine until they have a
reputation cache (which usually requires a few thousand verified
downloads). Two options to fix that:

### Option A — Authenticode certificate (manual)

After running `bin/build-installer.ps1`:

```powershell
$ts = "http://timestamp.digicert.com"
foreach ($a in @("dist\Ascendo-0.0.7-x64.msi", "dist\Ascendo-0.0.7-x64-setup.exe")) {
    signtool sign /fd sha256 /tr $ts /td sha256 /a $a
    signtool verify /pa /v $a
}
```

Requires an installed code-signing cert in the user's certificate
store (or `/f <pfx> /p <password>` if you have a file-based cert).

### Option B — Azure Trusted Signing (recommended for CI)

Tauri's `tauri.conf.json` `bundle.windows.signCommand` field accepts
a custom command. We don't wire one yet because the certificate
management lives outside this repo. Future work: see
`docs/architecture/0008-distribution-strategy.md`.

## winget submission

Sub-project 5 will fill in `winget-manifest/` with the standard
Microsoft package manifest YAML files
(`Ascendo.Ascendo.installer.yaml`, `Ascendo.Ascendo.locale.en-US.yaml`,
`Ascendo.Ascendo.yaml`). The manifest references the GitHub Release
download URL and the SHA256 printed by `bin/build-installer.ps1`.

## Hooks for sub-project 4 (Windows service)

`packaging/installer-assets/nsis-installer-hooks.nsh` ships the
`NSIS_HOOK_*` macro stubs. Sub-project 4 will:

1. Add an MUI components page with a "Run Ascendo as a Windows
   service (recommended)" checkbox.
2. Inside `!macro NSIS_HOOK_POSTINSTALL`, conditionally invoke
   `bin\install-service.ps1` if the checkbox is set.
3. Mirror the teardown in `!macro NSIS_HOOK_PREUNINSTALL` via
   `bin\uninstall-service.ps1`.

The hook file's source-of-truth lives at
`packaging/installer-assets/nsis-installer-hooks.nsh`. The build
script mirrors it into `src-tauri/installer-assets/` at build time;
edits to the mirror are wiped on every rebuild.

## See also

- `docs/architecture/0008-distribution-strategy.md` — channel rationale
- `bin/build-installer.ps1` — actual build pipeline
- `ui/desktop-tauri/src-tauri/tauri.conf.json` — Tauri MSI/NSIS config
- `.github/workflows/release.yml` — GitHub Releases automation (deferred)

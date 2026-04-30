# packaging/

Build pipeline configurations for distributing Ascendo across channels.

## Structure

```
packaging/
├── deb/                # Debian/Ubuntu .deb (extends current packaging/build-deb.sh)
├── msi/                # Windows MSI (WiX templates, fragments)
├── pkg/                # macOS .pkg postinstall scripts
├── homebrew-tap/       # Homebrew formula for `brew tap`
├── winget-manifest/    # YAML manifest for microsoft/winget-pkgs PR
└── pyinstaller/        # Python backend bundling specs (windows.spec, macos.spec, linux.spec)
```

## Distribution channels

| OS | Primary | Secondary | Tertiary |
|---|---|---|---|
| Linux | `.deb` (GitHub Releases) | AUR (`ascendo-bin`) | `.AppImage` portable, `pip install ascendo` |
| Windows | `winget install Ascendo.Ascendo` | MSI direct download | NSIS (`*-setup.exe`), portable ZIP |
| macOS | `brew install KasprowiczM/tap/ascendo` | `.dmg` direct download | `pip install ascendo` |

## Build flow

1. `cargo tauri build` per OS (calls into `pyinstaller/<os>.spec` for backend bundling on Win/macOS)
2. Artifacts in `<repo>/target/release/bundle/`
3. CI (`.github/workflows/release.yml`) on tag push:
   - Multi-OS matrix builds
   - Uploads to GitHub Release
   - Auto-PR to `microsoft/winget-pkgs` (winget-releaser action)
   - Auto-bump Homebrew tap formula

## See also

- `docs/architecture/0008-distribution-strategy.md` — channel rationale
- `.github/workflows/release.yml` — automation

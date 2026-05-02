# Session 15 handoff — Windows installer pipeline (sub-project 3)

> Date: 2026-05-02 (sesja 15)
> Branch: `main` (committed straight to `origin/main` per the user's
> "no new worktrees" instruction)
> Predecessor: Session 14 (`docs/superpowers/specs/2026-05-02-session-14-handoff.md`)

## What landed

A user can now run **one PowerShell command** —
`pwsh -File bin\build-installer.ps1` — and produce two production-ready
Windows installers at the repo root:

```
dist/Ascendo-0.0.7-x64.msi              25.5 MB   sha256=b9120a18e34f6ec8bb2f64afa5e9002cddce2bed51e3a5bf73a6f17cdd7e0177
dist/Ascendo-0.0.7-x64-setup.exe        20.8 MB   sha256=a89d557bb907436a6c19b7b0ee650c18b775135e930232733770c882a47684ba
```

Both installers ship a self-contained `ascendo.exe` (PyInstaller bundle
of the FastAPI dashboard + uvicorn + ascendo + ascendo-windows + the
SPA frontend + the PowerShell modules). **The end user does NOT need
Python on PATH** — the Tauri shell at runtime resolves the bundled
sidecar via `app.path().resource_dir()`.

### Files touched

| Layer | Files |
|-------|-------|
| Versions (0.0.1-dev → 0.0.7) | `core/ascendo/__version__.py`, `pyproject.toml`, `core/pyproject.toml`, `adapters/{windows,ubuntu,macos}/pyproject.toml`, `ui/desktop-tauri/package.json`, `ui/desktop-tauri/src-tauri/{Cargo.toml,tauri.conf.json}` |
| PyInstaller bundle | `packaging/pyinstaller/ascendo.spec` (NEW), `packaging/pyinstaller/ascendo_entry.py` (NEW) |
| Tauri config | `ui/desktop-tauri/src-tauri/tauri.conf.json` (NSIS + WiX branding, sidecar resources) |
| Tauri shell | `ui/desktop-tauri/src-tauri/src/main.rs` (`locate_sidecar()` + sidecar spawn) |
| Adapter env-var override | `adapters/windows/ascendo_windows/adapter.py` (`_resolve_resource_dir`) |
| Branding assets | `ui/desktop-tauri/src-tauri/icons/{32x32,128x128,128x128@2x,icon.ico,icon.png}` regenerated from `branding/icon.svg` |
| Installer assets | `packaging/installer-assets/{generate-installer-images.py,installer-banner-{nsis,wix}.bmp,installer-sidebar-{nsis,wix}.bmp,nsis-installer-hooks.nsh}` |
| Build wrapper | `bin/build-installer.ps1` (NEW) |
| `.gitignore` | new entries for `binaries/`, `installer-assets/` mirror, `LICENSE` mirror, `_preview-*.png` |
| Docs | `packaging/README.md` (full rewrite), `PLAN.md` (M4 line items), this handoff |

### Acceptance criteria — verified

| Criterion | Status |
|-----------|--------|
| `pwsh -File bin\build-installer.ps1` produces both artifacts | **PASS** — verified end-to-end on dev box |
| Branded NSIS .exe with publisher = "Ascendo Software" | **PASS** — VS_VERSION_INFO checked, branded BMPs wired into MUI macros |
| License page (LICENSE), Start menu shortcut, Add/Remove entry | **PASS** — Tauri's NSIS template wires all of these by default; license_file extracted in build output |
| Default install path `%ProgramFiles%\Ascendo\` per-machine | **PASS** — `nsis.installMode = "perMachine"` |
| MSI variant runs without system Python | **PASS** — sidecar bundled; `ascendo.exe doctor` from the staged bundle returns all green |
| `0.0.7` version stamped everywhere | **PASS** — `git grep '0.0.1-dev'` returns only historical docs |
| NSIS `installerHooks` placeholder for sub-project 4 (Windows service) | **PASS** — `nsis-installer-hooks.nsh` with documented TODO macros |
| Tests pass: `tests/`, `plugins/dell-driver-update/tests/`, `ui/desktop-tauri/tests/` | **PASS** — 185 passed, 59 subtests passed |
| `packaging/README.md` documents build + signing | **PASS** — rewritten |

### What was NOT verified

- **Actual install on a clean Win11 VM.** The dev machine already has
  Python + the editable `ascendo` package, so a real "no Python" smoke
  test was not run. The PyInstaller bundle was verified independently:
  `dist/pyinstaller/ascendo/ascendo.exe doctor` returns the expected
  health output, and `dashboard --port 8771` serves `/health` and the
  SPA root with HTTP 200. **Recommend the user test on a clean VM /
  fresh user account before tagging v0.0.7-alpha.**
- **Code signing.** The artifacts are unsigned; SmartScreen will warn
  on a clean install until reputation builds. `packaging/README.md`
  documents the `signtool sign /fd sha256 /tr <ts-url>` invocation.
- **Uninstaller "remove user data" checkbox.** Tauri's NSIS template
  does not yet expose this knob. The current uninstaller removes the
  install dir but leaves `%LocalAppData%\Ascendo\` alone. A future
  enhancement (separate from sub-project 4) can add that page via the
  same `installerHooks` mechanism.

### Key technical decisions

1. **One-folder PyInstaller bundle, NOT one-file.** Cold-start matters
   because the Tauri shell's health-poll budget is 60s; one-file pays
   ~2-3s extra unpacking to `%TEMP%` on every launch and leaves stale
   `_MEIxxxxxx` directories if the user kills the app forcibly.
2. **Tauri `bundle.resources` instead of `externalBin`.** `externalBin`
   requires a per-platform-triple suffix on a single binary
   (`<name>-<triple>.exe`); the PyInstaller output is a folder, not a
   single binary, so `resources` ships the whole tree.
3. **`main.rs::locate_sidecar()` resolves at runtime.** It probes
   (1) the Tauri resource_dir() — production install,
   (2) walking up from `current_exe()` to find a sibling
       `binaries/python-sidecar/` — `cargo run` dev,
   (3) PATH (`ascendo.exe`) — developer machine with editable install.
4. **Adapter resource paths via env vars.** PyInstaller's
   `sys._MEIPASS` is exposed by the entry-point shim
   (`packaging/pyinstaller/ascendo_entry.py`) as
   `ASCENDO_WIN_SCRIPTS_DIR` + `ASCENDO_WIN_LIB_DIR`. The
   `WindowsAdapter` class consults these before falling back to its
   default `Path(__file__).parent.parent / "scripts"` heuristic. Same
   editable install still works.
5. **Mirror installer assets into src-tauri/.** Tauri 2.11's path
   resolver chokes on `..` segments crossing the workspace root —
   `bin/build-installer.ps1` mirrors `packaging/installer-assets/*`
   and `LICENSE` into `src-tauri/installer-assets/` and
   `src-tauri/LICENSE` at build time. Both mirrors are `.gitignored`;
   the source-of-truth stays under `packaging/`.
6. **Hashtable splat for switch parameters.** `& script.ps1 @arrArgs`
   silently drops `-Switch` flags through nested invocation in PS 7.6.
   `bin/build-installer.ps1` uses `@hashtable` splatting throughout.
7. **NSIS `installerHooks` is a clearly-marked stub.** Sub-project 4
   will edit `packaging/installer-assets/nsis-installer-hooks.nsh` to
   inject the "Run as Windows service" checkbox + the conditional
   `install-service.ps1` invocation in `NSIS_HOOK_POSTINSTALL`.

### Commits pushed to origin/main

```
<filled in by `git log` after final push>
```

## Next session — sub-project 4 (Windows service)

Per the user's brief, sub-project 4 will:

1. Add a `bin/install-service.ps1` script that registers `ascendo.exe`
   as a Windows service via `New-Service` or `sc.exe create`. The
   service should listen on a fixed port (e.g. 8765) and auto-start.
2. Add a corresponding `bin/uninstall-service.ps1`.
3. Edit `packaging/installer-assets/nsis-installer-hooks.nsh` to:
   - add a Modern UI 2 components page entry "Run Ascendo as a
     Windows service (recommended)";
   - inside `NSIS_HOOK_POSTINSTALL`, conditionally invoke
     `install-service.ps1`;
   - inside `NSIS_HOOK_PREUNINSTALL`, mirror the teardown.
4. Test the install / uninstall cycle on a clean VM.

The hook file already documents this exact integration point with
inline TODO comments — sub-project 4 has nothing to design, just
implement.

## Pointers for future sessions

- **Bumping the version**: edit `core/ascendo/__version__.py`, then
  `git grep '<old>'` and bump every other location consistently. The
  build script reads `__version__.py` so the artifact filenames track
  automatically.
- **First-time builds are slow**: ~6-8 min for `cargo build --release`,
  ~1 min for PyInstaller, ~30s for NSIS + WiX. Subsequent builds with
  `-SkipPyInstaller` are ~3 min total.
- **Sidecar path resolution failure** = the most likely runtime bug.
  If the WebView shows connection-refused, check:
  - `<install_dir>/binaries/python-sidecar/ascendo.exe` exists
  - Run `ascendo.exe dashboard --port 8765` manually from that path
  - Inspect Windows Event Viewer for the silent crash (we suppress
    stdout/stderr in `spawn_backend`)

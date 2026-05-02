# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Ascendo Windows sidecar bundle.

Produces a one-folder bundle at ``dist/ascendo/`` containing
``ascendo.exe`` plus the unpacked Python runtime, FastAPI/uvicorn,
the Ascendo core + Windows adapter packages, the SPA frontend, and
the PowerShell scripts/modules consumed by the Windows adapter.

The Tauri shell ships this whole folder under
``ui/desktop-tauri/src-tauri/binaries/ascendo-x86_64-pc-windows-msvc/``
and invokes the executable as ``ascendo.exe dashboard --port <p>``.

Build (from repo root):

    python -m PyInstaller packaging/pyinstaller/ascendo.spec --noconfirm

The output ends up at ``dist/ascendo/ascendo.exe`` plus its
``_internal/`` runtime directory. ``bin/build-installer.ps1`` then
copies the whole folder into the Tauri sidecar slot before running
``npm run tauri build``.

One-folder vs one-file: we deliberately ship one-folder. Cold-start
time matters here because Tauri's ``wait_for_health`` poller has a
60s budget; a one-file build pays for the unpack-to-temp on every
launch (~2-3s extra) and keeps a stale ``_MEIxxxxxx`` directory in
``%TEMP%`` if the user kills the app forcibly.
"""
from __future__ import annotations

from pathlib import Path

# Spec files are evaluated by PyInstaller with the spec dir as cwd, but
# the safer approach is to derive the repo root from this file's path.
SPEC_PATH = Path(SPECPATH).resolve()  # noqa: F821 — provided by PyInstaller
REPO_ROOT = SPEC_PATH.parent.parent

# ── data files: SPA + adapter scripts/lib ─────────────────────────────
# Format: (source_path, destination_dir_inside_bundle)
# Destination is RELATIVE to the bundle root (one-folder mode puts it
# alongside ``ascendo.exe``; in one-file mode it lives under sys._MEIPASS).
DATAS: list[tuple[str, str]] = []

# SPA bundle — frontend served by core/ascendo/dashboard/app.py
SPA_SRC = REPO_ROOT / "app" / "frontend"
if SPA_SRC.is_dir():
    DATAS.append((str(SPA_SRC), "frontend"))

# Ascendo Windows adapter: scripts/ + lib/ are needed at runtime by
# the manager classes. They land under
# ``ascendo_windows_resources/{scripts,lib}/`` which the entry-point
# shim (``ascendo_entry.py``) advertises via env vars.
WIN_SCRIPTS = REPO_ROOT / "adapters" / "windows" / "scripts"
WIN_LIB = REPO_ROOT / "adapters" / "windows" / "lib"
if WIN_SCRIPTS.is_dir():
    DATAS.append((str(WIN_SCRIPTS), "ascendo_windows_resources/scripts"))
if WIN_LIB.is_dir():
    DATAS.append((str(WIN_LIB), "ascendo_windows_resources/lib"))

# Branding (icon + banner). Tauri uses its own icons but we keep these
# inside the bundle so the dashboard can render the wordmark/icon if it
# probes the install dir at runtime.
BRANDING = REPO_ROOT / "branding"
if BRANDING.is_dir():
    DATAS.append((str(BRANDING), "branding"))

# ── hidden imports ────────────────────────────────────────────────────
# FastAPI/uvicorn pull a long tail of submodules dynamically; PyInstaller
# does not always discover them via static analysis. We pin the ones
# observed to fail in ``ascendo dashboard`` smoke tests.
HIDDEN_IMPORTS: list[str] = [
    # --- ascendo packages (the entry-point shim only references the
    # CLI module statically; everything else loads via __init__ chains)
    "ascendo",
    "ascendo.cli",
    "ascendo.dashboard",
    "ascendo.dashboard.app",
    "ascendo.dashboard.routes",
    "ascendo.dashboard.routes.health",
    "ascendo.dashboard.routes.runs",
    "ascendo.dashboard.routes.spa_real",
    "ascendo.dashboard.routes.spa_stubs",
    "ascendo.adapter_factory",
    "ascendo.orchestrator",
    "ascendo_windows",
    "ascendo_windows.adapter",
    "ascendo_windows.inventory",
    "ascendo_windows.managers",
    "ascendo_windows.managers.arp",
    "ascendo_windows.managers.elevation",
    "ascendo_windows.managers.msstore",
    "ascendo_windows.managers.scheduler",
    "ascendo_windows.managers.snapshot",
    "ascendo_windows.managers.winget",
    "ascendo_windows.managers.windows_update",
    # --- FastAPI / Starlette / pydantic / uvicorn dynamic loads
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.responses",
    "starlette",
    "starlette.routing",
    "anyio",
    "h11",
    "click",  # uvicorn dep
    # --- Typer (CLI)
    "typer",
    "rich",
    # --- Pydantic
    "pydantic",
    "pydantic.networks",
    "pydantic_core",
    "pydantic_settings",
    # --- httpx (used by tests but also by some endpoints)
    "httpx",
    "httpx._main",
    # --- jsonschema (sidecar contract validation)
    "jsonschema",
]

# ── adapter discovery: entry_points('ascendo.adapters') ───────────────
# The core uses ``importlib.metadata`` to enumerate registered adapters.
# PyInstaller's default behaviour leaves the wheel metadata behind,
# which breaks adapter discovery in a frozen bundle. ``copy_metadata``
# preserves the dist-info dirs we need.
from PyInstaller.utils.hooks import copy_metadata  # noqa: E402

DATAS += copy_metadata("ascendo")
DATAS += copy_metadata("ascendo-windows")

# ── analysis ──────────────────────────────────────────────────────────
ENTRY = str(SPEC_PATH / "ascendo_entry.py")

a = Analysis(
    [ENTRY],
    pathex=[
        str(REPO_ROOT / "core"),
        str(REPO_ROOT / "adapters" / "windows"),
    ],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Excessively large stdlib modules we don't use.
        "tkinter",
        "test",
        "unittest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ascendo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # console=True so `ascendo run` etc. still print to stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Win32-specific: VERSIONINFO + manifest (no UAC elevation; the
    # Tauri shell handles that for apply phases via SUDO_ASKPASS).
    icon=str(REPO_ROOT / "ui" / "desktop-tauri" / "src-tauri" / "icons" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ascendo",
)

"""PyInstaller entry-point shim for the bundled ``ascendo.exe`` sidecar.

This file exists solely so PyInstaller can resolve a single Python
script as the executable's entry point. We delegate immediately to the
existing Typer-based CLI so the bundled binary supports every
subcommand (``dashboard``, ``run``, ``doctor``, ``snapshot``,
``schedule``, ``runs``, ``version``).

The Tauri shell at ``ui/desktop-tauri/src-tauri/src/main.rs`` calls

    ascendo.exe dashboard --host 127.0.0.1 --port <port>

so the sidecar has to expose the same CLI surface as ``python -m
ascendo``. By delegating to ``ascendo.cli:main`` we get that for free.

Why a shim instead of pointing PyInstaller straight at
``core/ascendo/__main__.py``?

- ``__main__.py`` is the Python ``-m`` entry point and lives inside the
  ``ascendo`` package. PyInstaller can target it but warns about
  duplicate module discovery; using a top-level shim file keeps the
  spec graph clean.
- A shim gives us a single obvious place to add bundle-specific
  bootstrapping (e.g. setting ``ASCENDO_FRONTEND_DIR`` from the
  PyInstaller ``_MEIPASS`` runtime path so the SPA ships inside the
  bundle).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_bundle_paths() -> None:
    """Wire bundle-relative resource paths so the dashboard finds the SPA.

    PyInstaller exposes ``sys._MEIPASS`` at runtime — for a one-folder
    build it is the directory containing the unpacked resources. We set
    ``ASCENDO_FRONTEND_DIR`` (consumed by
    ``core/ascendo/dashboard/app.py::_resolve_frontend_dir``) so the SPA
    serves correctly without the editable repo layout.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        # Not running from a frozen bundle (e.g. dev). The dashboard
        # will fall back to its repo-root probe.
        return
    base_path = Path(base)

    # SPA bundle — copied in via the .spec ``datas`` list.
    frontend = base_path / "frontend"
    if frontend.is_dir() and "ASCENDO_FRONTEND_DIR" not in os.environ:
        os.environ["ASCENDO_FRONTEND_DIR"] = str(frontend)

    # Windows adapter scripts/lib roots — consumed by
    # ``WindowsAdapter.SCRIPTS_DIR`` / ``LIB_DIR`` at runtime via env-var
    # overrides registered below. We also set explicit env vars the
    # adapter consults so its default ``Path(__file__).parent.parent``
    # logic still resolves inside the frozen bundle layout.
    win_scripts = base_path / "ascendo_windows_resources" / "scripts"
    win_lib = base_path / "ascendo_windows_resources" / "lib"
    if win_scripts.is_dir():
        os.environ.setdefault("ASCENDO_WIN_SCRIPTS_DIR", str(win_scripts))
    if win_lib.is_dir():
        os.environ.setdefault("ASCENDO_WIN_LIB_DIR", str(win_lib))


def main() -> None:
    _bootstrap_bundle_paths()
    # Import here, AFTER environment is staged, so any module-level path
    # resolution sees the bundle env vars.
    from ascendo.cli import main as _cli_main
    _cli_main()


if __name__ == "__main__":
    main()

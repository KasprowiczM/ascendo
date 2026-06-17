"""Typer-based CLI: ``ascendo <command>``.

This package was refactored from a single 1500+ line file into focused
subcommand modules. Each ``*_cmd.py`` registers its commands against
the shared Typer apps defined in ``_app.py``. This ``__init__.py``
imports the submodules (triggering command registration) and re-exports
every public and semi-public symbol so that existing import paths like
``from ascendo.cli import app, render_banner`` keep working.
"""
from __future__ import annotations

# ── 1b. Names that tests mock-patch at "ascendo.cli.*" ────────────────────
from ..adapter_factory import AdapterRegistry, NoAdapterAvailableError, select_adapter  # noqa: F401
from ..orchestrator import run_phases  # noqa: F401

# ── 2. Subcommand modules (import triggers @app.command registration) ─────
from . import (
    dashboard_cmd as _dashboard_cmd,  # noqa: F401
    doctor_cmd as _doctor_cmd,  # noqa: F401
    run_cmd as _run_cmd,  # noqa: F401
    runs_cmd as _runs_cmd,  # noqa: F401
    schedule_cmd as _schedule_cmd,  # noqa: F401
    selfupdate_cmd as _selfupdate_cmd,  # noqa: F401
    snapshot_cmd as _snapshot_cmd,  # noqa: F401
)

# ── 1. Shared core (Typer apps, helpers, banner, version, main) ───────────
from ._app import (  # noqa: F401
    _BANNER_EN,
    _BANNER_PL,
    _default_runs_dir,
    _log,
    _planned,
    _resolve_adapter_for_capability,
    _resolve_categories,
    _resolve_locale,
    _resolve_phases,
    _setup_logging,
    _sidecars_need_reboot,
    app,
    main,
    render_banner,
    runs_app,
    schedule_app,
    snapshot_app,
    web_app,
)

# ── 3. Re-export dashboard helpers used by contract tests ─────────────────
from .dashboard_cmd import (  # noqa: F401
    _clear_pidfile,
    _dashboard_pidfile,
    _open_browser,
    _pid_alive,
    _port_listening,
    _read_pidfile,
    _write_pidfile,
)

__all__ = ["app", "main"]

"""Ascendo self-update: check for a newer Ascendo and apply it in-app.

Distinct from the *managed-app* update orchestration (``ascendo run``):
this package updates **Ascendo itself**. One implementation is shared by
the CLI (``ascendo self-update``), the dashboard endpoints
(``/api/updates/*``), and the SPA's startup auto-check.

Public surface:
    detect_install()        -> InstallInfo
    check_for_updates()     -> dict   (never raises; fails soft)
    start_update()          -> Job    (background upgrade for git installs)
    get_job(job_id)         -> Job | None
    version.is_newer(a, b)  -> bool
"""
from __future__ import annotations

from .apply import Job, UpdateNotSupported, get_job, start_update
from .check import check_for_updates, current_core_version
from .detect import InstallInfo, detect_install
from . import version

__all__ = [
    "InstallInfo",
    "Job",
    "UpdateNotSupported",
    "check_for_updates",
    "current_core_version",
    "detect_install",
    "get_job",
    "start_update",
    "version",
]

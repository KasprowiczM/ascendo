"""Combine installed version + manifest into an update-status report.

This is the single function the dashboard endpoint, the CLI, and the
startup auto-check all call. It never raises: on any error (offline,
bad manifest) it returns ``ok=False`` with a message so callers can fail
soft and the UI can stay quiet.
"""
from __future__ import annotations

import logging

from . import manifest as _manifest
from . import version as _version
from .detect import InstallInfo, detect_install

_log = logging.getLogger(__name__)

__all__ = ["check_for_updates", "current_core_version"]


def current_core_version() -> str:
    from .. import __version__

    return __version__


def _shell_artifact(shell_block: dict, info: InstallInfo) -> dict | None:
    artifacts = (shell_block or {}).get("artifacts") or {}
    key = f"{info.os}_{info.arch}"
    art = artifacts.get(key)
    if isinstance(art, dict):
        return {"platform": key, **art}
    return None


def check_for_updates(install: InstallInfo | None = None) -> dict:
    info = install or detect_install()
    current_core = current_core_version()

    report: dict = {
        "ok": False,
        "error": None,
        "checked": True,
        "os": info.os,
        "arch": info.arch,
        "method": info.method,
        "is_git": info.is_git,
        "desktop": info.desktop,
        "current_core": current_core,
        "current_shell": info.shell_version,
        "latest_core": None,
        "latest_shell": None,
        "core_update_available": False,
        "shell_update_available": False,
        "update_available": False,
        "can_self_update": False,
        "channel": None,
        "notes_url": None,
        "shell_artifact": None,
    }

    try:
        manifest = _manifest.fetch_manifest()
        block = _manifest.select_channel(manifest)
    except Exception as exc:  # noqa: BLE001 — fail soft
        _log.info("update check failed: %s", exc)
        report["error"] = str(exc)
        return report

    core_block = block.get("core") or {}
    shell_block = block.get("shell") or {}
    latest_core = core_block.get("version")
    latest_shell = shell_block.get("version")

    report["ok"] = True
    report["channel"] = block.get("channel")
    report["latest_core"] = latest_core
    report["latest_shell"] = latest_shell
    report["notes_url"] = core_block.get("notes_url")

    if latest_core:
        report["core_update_available"] = _version.is_newer(latest_core, current_core)
    if latest_shell and info.shell_version:
        report["shell_update_available"] = _version.is_newer(latest_shell, info.shell_version)

    # Can we upgrade the core in-app? Only for a git checkout with an
    # updater script present, on a supported OS.
    report["can_self_update"] = bool(
        info.method == "git"
        and info.updater
        and info.os in {"macos", "linux", "windows"}
    )
    report["shell_artifact"] = _shell_artifact(shell_block, info)
    report["update_available"] = bool(
        report["core_update_available"] or report["shell_update_available"]
    )
    return report

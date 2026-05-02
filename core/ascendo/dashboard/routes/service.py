"""``/service/*`` REST endpoints — manage the Windows dashboard service.

These endpoints back the SPA's "Run as Windows service" UX: a
status pill + Install / Uninstall / Restart buttons in Settings.

Architecture::

    SPA (POST /service/install)
        -> FastAPI router (this file)
        -> ascendo_windows.managers.service.WindowsServiceManager
        -> subprocess: pwsh bin\\install-service.ps1 -Action install
        -> NSSM register / sc.exe failure

The Python wrapper lives in the Windows adapter so the dashboard core
stays OS-agnostic; on non-Windows hosts every endpoint returns the
"unsupported" shape (HTTP 200 + ``installed=False`` + a ``stub`` flag)
rather than 404, so the SPA can render the section unconditionally and
just hide the buttons when ``platform != 'windows'``.

GET /service/status JSON contract
---------------------------------

The dashboard SPA polls this on Settings open + after every mutating
action::

    {
      "installed":      bool,
      "running":        bool,
      "port_listening": bool,
      "health":         "ok" | null | "<other>",
      "last_started":   ISO-8601 string | null,
      "pid":            int (0 when no service or not running),
      "service_name":   "AscendoDashboard",
      "state":          "SERVICE_RUNNING" | "SERVICE_STOPPED" | "ABSENT" | ...,
      "port":           8765,
      "platform":       "win32" | "linux" | ...,
      "supported":      true on Windows, false elsewhere
    }

POST endpoints (install/uninstall/start/stop/restart) return::

    {"ok": bool, "output": "...", "exit_code": int}

When elevation is required and missing the response is HTTP 401 with
``{"detail": "ELEVATION-REQUIRED"}`` so the SPA can re-trigger UAC.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/service", tags=["service"])

_log = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _unsupported_status() -> dict[str, Any]:
    """Status payload for non-Windows hosts."""
    return {
        "installed":      False,
        "running":        False,
        "port_listening": False,
        "health":         None,
        "last_started":   None,
        "pid":            0,
        "service_name":   "AscendoDashboard",
        "state":          "UNSUPPORTED",
        "port":           8765,
        "platform":       sys.platform,
        "supported":      False,
    }


def _service_manager(request: Request):
    """Lazily build the manager.

    Returns ``None`` on non-Windows or when the adapter is missing —
    callers handle that branch by returning the "unsupported" shape.
    """
    if not _is_windows():
        return None
    # Allow tests to inject a fake via app.state.
    fake = getattr(request.app.state, "service_manager", None)
    if fake is not None:
        return fake
    try:
        from ascendo_windows.managers.service import (
            WindowsServiceManager,
            default_repo_root,
        )
    except ImportError as exc:
        _log.warning("Windows adapter not installed; /service endpoints will return unsupported: %s", exc)
        return None

    # Reuse a single instance per app state.
    cached = getattr(request.app.state, "_service_manager_cached", None)
    if cached is not None:
        return cached

    repo_root = getattr(request.app.state, "service_repo_root", default_repo_root())
    mgr = WindowsServiceManager(repo_root=repo_root)
    request.app.state._service_manager_cached = mgr
    return mgr


def _action_result_to_dict(result: object, *, ok_default: bool) -> dict[str, Any]:
    """Normalise the wrapper's ServiceActionResult to a JSON dict."""
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {
        "ok":        getattr(result, "ok", ok_default),
        "exit_code": getattr(result, "exit_code", 0 if ok_default else 1),
        "output":    getattr(result, "output", ""),
    }


def _do_action(request: Request, action: str, **kwargs):
    """Shared dispatch for install/uninstall/start/stop/restart.

    Translates ``ServiceElevationRequired`` -> HTTP 401 with the
    sentinel detail string the SPA can branch on.
    """
    mgr = _service_manager(request)
    if mgr is None:
        raise HTTPException(
            status_code=400,
            detail="service management is only supported on Windows hosts",
        )

    method = getattr(mgr, action, None)
    if method is None:
        raise HTTPException(status_code=500, detail=f"unknown action {action}")

    try:
        result = method(**kwargs)
    except Exception as exc:
        # Lazy import so the import doesn't break on non-Windows test
        # collection.
        try:
            from ascendo_windows.managers.service import (
                ServiceElevationRequired,
                ServiceManagerError,
            )
        except ImportError:  # pragma: no cover -- only on Linux without adapter
            ServiceElevationRequired = ()  # type: ignore[assignment]
            ServiceManagerError = Exception  # type: ignore[assignment]

        if isinstance(exc, ServiceElevationRequired):
            raise HTTPException(status_code=401, detail="ELEVATION-REQUIRED") from exc
        if isinstance(exc, ServiceManagerError):
            _log.exception("service action %s failed", action)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        raise

    return _action_result_to_dict(result, ok_default=True)


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/status")
async def service_status(request: Request) -> dict[str, Any]:
    """Read service state. Always 200; ``supported=False`` on non-Win."""
    mgr = _service_manager(request)
    if mgr is None:
        return _unsupported_status()

    try:
        status = mgr.status()
    except Exception:
        _log.exception("service.status() raised")
        # Don't 500 on a status read; return a degraded payload so the SPA
        # can still render.
        return {
            **_unsupported_status(),
            "platform": sys.platform,
            "supported": True,
            "state": "UNKNOWN",
        }
    payload = status.to_dict()
    payload["platform"]  = sys.platform
    payload["supported"] = True
    return payload


@router.post("/install")
async def service_install(request: Request) -> dict[str, Any]:
    return _do_action(request, "install")


@router.post("/uninstall")
async def service_uninstall(request: Request) -> dict[str, Any]:
    """Uninstall the service.

    Accepts an optional ``?purge_logs=true`` query flag to also delete
    ``%LocalAppData%\\Ascendo\\logs\\service\\``.
    """
    purge_logs = request.query_params.get("purge_logs", "").lower() in (
        "1", "true", "yes",
    )
    return _do_action(request, "uninstall", purge_logs=purge_logs)


@router.post("/start")
async def service_start(request: Request) -> dict[str, Any]:
    return _do_action(request, "start")


@router.post("/stop")
async def service_stop(request: Request) -> dict[str, Any]:
    return _do_action(request, "stop")


@router.post("/restart")
async def service_restart(request: Request) -> dict[str, Any]:
    return _do_action(request, "restart")


__all__ = ["router"]

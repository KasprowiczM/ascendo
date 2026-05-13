"""Schedule tab backend (Sesja 67).

Replaces the previous ``/scheduler/install`` + ``/scheduler/remove``
stubs in :mod:`spa_stubs`. Drives the adapter's :class:`IScheduler`
implementation (``WindowsScheduler`` on Windows, ``LaunchdScheduler``
on macOS, ``SystemdScheduler`` on Ubuntu) so the SPA gets real Task
Scheduler / launchd / systemd entries rather than the placebo "ok=true,
stub=true" response.

Endpoints
=========

GET  /scheduler/list        list all Ascendo-owned schedules
POST /scheduler/install     install or replace a schedule
POST /scheduler/remove      uninstall a schedule by name
POST /scheduler/trigger     run a schedule once now (for testing)

Wire shape mirrors the rest of the SPA REST: ``{ok, items?, error?}``
JSON. Schedule expression formats are adapter-specific (Windows uses
``schtasks`` strings; macOS launchd uses ``DAILY HH:MM`` etc.). The
expression field is passed verbatim — the adapter parses it.

All errors return 200 with ``{ok: false, error}`` rather than HTTP
5xx so the SPA can render the failure inline. The only HTTP-level
failures are 503 when no adapter is loaded.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ascendo.interfaces.adapter import AdapterCapability
from ascendo.interfaces.scheduler import ScheduleSpec, SchedulerError
from ascendo.models.host import HostInfo

_log = logging.getLogger(__name__)
router = APIRouter(tags=["scheduler"])


# ── Wire schemas ─────────────────────────────────────────────────────────


class ScheduleInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=80)
    expression: str = Field(..., min_length=1, max_length=200)
    profile: str = Field(default="full")
    enabled: bool = Field(default=True)
    description: str | None = Field(default=None, max_length=400)


class ScheduleNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=80)


# ── Helpers ──────────────────────────────────────────────────────────────


def _scheduler_or_503(request: Request):
    """Return (scheduler, host) or raise 503 when scheduling is not
    available on this adapter."""
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="no adapter loaded")
    if AdapterCapability.SCHEDULING not in adapter.capabilities:
        raise HTTPException(
            status_code=503,
            detail=f"adapter '{adapter.name}' does not support SCHEDULING",
        )
    sched = adapter.scheduler()
    if sched is None:
        raise HTTPException(
            status_code=503,
            detail=f"adapter '{adapter.name}' returned no scheduler",
        )
    # HostInfo for the running adapter — most schedulers need the host
    # to dispatch (Windows uses schtasks; macOS uses launchctl bootstrap).
    host: HostInfo | None = getattr(request.app.state, "host_info", None)
    if host is None:
        try:
            host = adapter.detect_host()
        except Exception:  # noqa: BLE001
            host = HostInfo.local_snapshot(adapter.name)
    return sched, host


def _spec_to_dict(spec: ScheduleSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "expression": spec.expression,
        "profile": spec.profile,
        "enabled": spec.enabled,
        "description": spec.description,
    }


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/scheduler/list")
async def scheduler_list(request: Request) -> dict[str, Any]:
    """List all Ascendo-owned schedules visible to the OS scheduler."""
    sched, host = _scheduler_or_503(request)
    try:
        items = sched.list(host)
    except SchedulerError as exc:
        return {"ok": False, "items": [], "error": str(exc)}
    return {"ok": True, "items": [_spec_to_dict(s) for s in items]}


@router.post("/scheduler/install")
async def scheduler_install(
    request: Request, body: ScheduleInstallRequest
) -> dict[str, Any]:
    """Install (or replace) a schedule.

    The adapter's installer is idempotent — submitting the same name
    twice updates the existing entry in place rather than creating
    duplicates. Returns the installed spec for confirmation.
    """
    sched, host = _scheduler_or_503(request)
    try:
        spec = ScheduleSpec(
            name=body.name,
            expression=body.expression,
            profile=body.profile,
            enabled=body.enabled,
            description=body.description,
        )
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"invalid schedule spec: {exc}"}
    try:
        sched.install(host, spec)
    except SchedulerError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "spec": _spec_to_dict(spec)}


@router.post("/scheduler/remove")
async def scheduler_remove(
    request: Request, body: ScheduleNameRequest
) -> dict[str, Any]:
    """Uninstall a schedule by name. No-op if it doesn't exist."""
    sched, host = _scheduler_or_503(request)
    try:
        sched.uninstall(host, body.name)
    except SchedulerError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": body.name}


@router.post("/scheduler/trigger")
async def scheduler_trigger(
    request: Request, body: ScheduleNameRequest
) -> dict[str, Any]:
    """Fire a schedule once now (for verifying the expression works)."""
    sched, host = _scheduler_or_503(request)
    try:
        sched.trigger(host, body.name)
    except SchedulerError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": body.name, "triggered": True}


__all__ = ["router"]

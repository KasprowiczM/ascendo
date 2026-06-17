"""Ascendo self-update endpoints (update *the app itself*).

- ``GET  /api/updates/check``            — compare installed vs published.
- ``POST /api/updates/apply``            — start an in-app upgrade (git installs).
- ``GET  /api/updates/status/{job_id}``  — poll live log + final state.

All handlers fail soft: a network/manifest error returns a JSON body with
``ok=False`` rather than a 500, so the SPA's startup auto-check stays quiet
when offline. The actual work lives in :mod:`ascendo.selfupdate`; this is a
thin HTTP shell shared by web + desktop (same SPA) on every OS.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/updates", tags=["updates"])


@router.get("/check")
async def check() -> dict[str, Any]:
    """Return the update-status report (never raises)."""
    from ...selfupdate import check_for_updates

    # check_for_updates does a blocking HTTP GET — keep the event loop free.
    return await run_in_threadpool(check_for_updates)


@router.post("/apply")
async def apply() -> JSONResponse:
    """Kick off an in-app upgrade. Returns ``{job_id}`` or 409 if unsupported."""
    from ...selfupdate import detect_install, start_update
    from ...selfupdate.apply import UpdateNotSupported

    info = await run_in_threadpool(detect_install)
    try:
        job = start_update(info)
    except UpdateNotSupported as exc:
        # Surface the download artifact for this platform so the UI can
        # offer a manual install instead.
        from ...selfupdate import check_for_updates

        report = await run_in_threadpool(check_for_updates, info)
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": str(exc),
                "can_self_update": False,
                "shell_artifact": report.get("shell_artifact"),
                "notes_url": report.get("notes_url"),
            },
        )
    return JSONResponse(
        status_code=202,
        content={"ok": True, "job_id": job.id, "state": job.state},
    )


@router.get("/status/{job_id}")
async def status(job_id: str) -> JSONResponse:
    """Poll an upgrade job's live log + state."""
    from ...selfupdate import get_job

    job = get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"ok": False, "error": "unknown job"})
    return JSONResponse(content={"ok": True, **job.to_dict()})


__all__ = ["router"]

"""``GET /health`` and ``GET /version`` endpoints.

Both are read-only and return quickly. The ``/health`` endpoint calls
:meth:`IAdapter.health_check` which itself should complete in well under
a second.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ... import __version__
from ..schemas import HealthResponse, VersionResponse

router = APIRouter()


@router.get("/version", response_model=VersionResponse, tags=["meta"])
async def get_version(request: Request) -> VersionResponse:
    """Return Ascendo version + currently-selected adapter info + edition."""
    adapter = getattr(request.app.state, "adapter", None)
    edition = getattr(request.app.state, "edition", "basic")
    return VersionResponse(
        ascendo=__version__,
        adapter=adapter.name if adapter is not None else None,
        adapter_tier=adapter.tier if adapter is not None else None,
        edition=edition,
    )


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def get_health(request: Request) -> HealthResponse:
    """Adapter self-test. Mirrors ``ascendo doctor``.

    The overall ``status`` is computed as:

    - ``"ok"`` — every component reports ``ok`` or ``degraded:*``.
    - ``"degraded"`` — at least one component is degraded but none failed.
    - ``"error"`` — at least one component reports ``unavailable:*`` /
      ``error:*`` / unrecognized status.
    """
    adapter = getattr(request.app.state, "adapter", None)

    if adapter is None:
        return HealthResponse(
            status="error",
            adapter=None,
            components={"adapter": "unavailable: no adapter installed"},
        )

    components = adapter.health_check()
    has_error = False
    has_degraded = False
    for status in components.values():
        if status.startswith("ok"):
            continue
        if status.startswith("degraded"):
            has_degraded = True
            continue
        has_error = True

    overall = "error" if has_error else "degraded" if has_degraded else "ok"
    return HealthResponse(
        status=overall,
        adapter=adapter.name,
        components=components,
    )

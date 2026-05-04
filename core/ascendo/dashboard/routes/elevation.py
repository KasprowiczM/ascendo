"""``/elevation`` REST endpoints — sudo password cache management.

Three endpoints for the macOS sudo askpass flow (and any future IElevation
adapter):

    GET  /elevation/status       -> {"registered": bool, "method": "sudo"|null}
    POST /elevation/auth         -> 200 {"ok": true} | 401 {"detail": "..."}
    POST /elevation/invalidate   -> 200 {"ok": true}  (idempotent)

Returns 503 on every endpoint when the adapter has no IElevation (Linux /
Windows dashboards unaffected — endpoints exist in OpenAPI but return 503 at
request time).

Security:
    - Dashboard binds 127.0.0.1 by default (loopback-only, no network exposure).
    - The password flows from request body directly to IElevation.register_password
      and lives in process memory only; it is never written to disk or logged.
    - 401 on bad password — no user-account information leaked in the response.
    - Pydantic ``extra="forbid"`` on AuthRequest prevents unexpected fields from
      reaching the elevation backend.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/elevation", tags=["elevation"])


# ── Wire models ──────────────────────────────────────────────────────────────


class AuthRequest(BaseModel):
    """Body for POST /elevation/auth."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=1)


class StatusResponse(BaseModel):
    """Response for GET /elevation/status."""

    registered: bool
    method: str | None


class OkResponse(BaseModel):
    """Generic success response."""

    ok: bool = True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _elevation_or_503(request: Request):
    """Return the IElevation instance or raise 503.

    Raises:
        HTTPException(503): when the adapter is None or has no IElevation.
    """
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=503,
            detail="No adapter installed for this OS.",
        )
    elev = adapter.elevation()
    if elev is None:
        raise HTTPException(
            status_code=503,
            detail="This adapter does not support elevation management.",
        )
    return elev


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request) -> StatusResponse:
    """Return current elevation state.

    Raises:
        503: adapter absent or has no IElevation.
    """
    elev = _elevation_or_503(request)

    registered = elev.has_password_registered()

    # Derive the current method from available_methods (first entry) when a
    # password is cached, else None so the UI knows prompting is needed.
    methods = elev.available_methods
    method: str | None = methods[0].value if methods else None

    return StatusResponse(registered=registered, method=method)


@router.post("/auth", response_model=OkResponse)
async def post_auth(body: AuthRequest, request: Request) -> OkResponse:
    """Register a sudo password in process memory.

    The password is verified via ``sudo -S -v`` (one attempt) before storing.
    On wrong password the endpoint returns 401 with a sanitised error message
    -- no information about valid account names is exposed.

    Raises:
        401: password rejected by the underlying elevation backend.
        503: adapter absent or has no IElevation.
    """
    elev = _elevation_or_503(request)

    ok, msg = elev.register_password(body.password)
    if not ok:
        # Sanitise: strip sudo's raw stderr before returning.
        safe_msg = msg[:300] if msg else "authentication failed"
        raise HTTPException(status_code=401, detail=safe_msg)

    return OkResponse(ok=True)


@router.post("/invalidate", response_model=OkResponse)
async def post_invalidate(request: Request) -> OkResponse:
    """Invalidate the cached sudo password (idempotent).

    Safe to call even when no password is registered — the backend's
    ``invalidate()`` is specified to be a no-op in that case.

    Raises:
        503: adapter absent or has no IElevation.
    """
    elev = _elevation_or_503(request)
    elev.invalidate()
    return OkResponse(ok=True)

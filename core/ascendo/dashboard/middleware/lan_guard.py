"""Capability-token gate for LAN-exposed dashboards.

The dashboard is loopback-only by default. When the operator opts into a
non-loopback bind (``--allow-remote`` / ``ASCENDO_ALLOW_REMOTE=1``), privileged
*mutating* endpoints (``/runs/async``, ``/elevation/auth``, ``/dedup/apply``,
service mgmt, …) become reachable from the LAN. This middleware then requires a
per-process capability token on every mutating request from a non-loopback
client; loopback clients (the local operator / the served SPA) pass freely so
the default UX is unchanged.

It is added to the app ONLY in the remote-exposed configuration, so loopback
deployments and the test suite are never gated.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


class LanGuardMiddleware(BaseHTTPMiddleware):
    """Require ``X-Ascendo-Token`` on mutating requests from non-loopback peers."""

    def __init__(self, app, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request, call_next):
        if request.method in _SAFE_METHODS:
            return await call_next(request)
        client_host = request.client.host if request.client else None
        if _is_loopback(client_host):
            return await call_next(request)
        supplied = request.headers.get("x-ascendo-token")
        if self._token and supplied == self._token:
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "remote mutating request requires a valid X-Ascendo-Token "
                    "header (dashboard is LAN-exposed via --allow-remote)"
                )
            },
        )

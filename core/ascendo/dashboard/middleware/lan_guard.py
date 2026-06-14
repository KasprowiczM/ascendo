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

Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) so SSE streams
and background tasks are forwarded without buffering.
"""
from __future__ import annotations

import json as _json
from collections.abc import Callable

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_FORBIDDEN_BODY: bytes = _json.dumps(
    {
        "detail": (
            "remote mutating request requires a valid X-Ascendo-Token "
            "header (dashboard is LAN-exposed via --allow-remote)"
        )
    }
).encode("utf-8")


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


async def _send_json(send: Callable, *, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode("latin-1")],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class LanGuardMiddleware:
    """Require ``X-Ascendo-Token`` on mutating requests from non-loopback peers."""

    def __init__(self, app: Callable, *, token: str) -> None:
        self.app = app
        self._token = token

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method", "") in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = client[0] if client else None
        if _is_loopback(client_host):
            await self.app(scope, receive, send)
            return

        supplied: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"x-ascendo-token":
                supplied = value.decode("latin-1")
                break

        if self._token and supplied == self._token:
            await self.app(scope, receive, send)
            return

        await _send_json(send, status=403, body=_FORBIDDEN_BODY)

"""Edition-gate middleware: returns 404 for dev-only routes when edition=basic.

Resolution: app.state.edition is set in app.py during create_app(). If absent,
fall back to "basic" (defensive — the gate should still work even if startup
ordering changes).

Implemented as a pure ASGI middleware (not BaseHTTPMiddleware) so SSE streams
and background tasks are forwarded without buffering.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable

# Path prefixes that exist in dev edition only. The middleware compares against
# the request path with `==` for exact matches and `startswith(p + "/")` for
# subtree matches so /git/push doesn't accidentally gate /git/status.
DEV_ONLY_PATH_PREFIXES = (
    "/sync",
    "/hosts",
    "/git/push",
    "/dev-sync",
    "/profiles/import",
)

_NOT_FOUND_BODY: bytes = _json.dumps({"detail": "not_found"}).encode("utf-8")


class EditionGateMiddleware:
    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        edition = getattr(getattr(app, "state", None), "edition", "basic")

        if edition == "basic":
            path: str = scope.get("path", "")
            for prefix in DEV_ONLY_PATH_PREFIXES:
                if path == prefix or path.startswith(prefix + "/"):
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 404,
                            "headers": [
                                [b"content-type", b"application/json"],
                                [b"content-length", str(len(_NOT_FOUND_BODY)).encode("latin-1")],
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": _NOT_FOUND_BODY})
                    return

        await self.app(scope, receive, send)

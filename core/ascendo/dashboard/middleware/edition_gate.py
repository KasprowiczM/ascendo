"""Edition-gate middleware: returns 404 for dev-only routes when edition=basic.

Resolution: app.state.edition is set in app.py during create_app(). If absent,
fall back to "basic" (defensive — the gate should still work even if startup
ordering changes).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

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


class EditionGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        edition = getattr(request.app.state, "edition", "basic")
        if edition == "basic":
            path = request.url.path
            for prefix in DEV_ONLY_PATH_PREFIXES:
                if path == prefix or path.startswith(prefix + "/"):
                    return JSONResponse({"detail": "not_found"}, status_code=404)
        return await call_next(request)

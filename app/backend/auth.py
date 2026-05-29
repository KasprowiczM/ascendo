"""Optional bearer-token authentication for the dashboard.

Default behaviour: dashboard binds 127.0.0.1 with no auth — local user is
trusted.  This module enables an opt-in token gate so the dashboard can be
exposed on a LAN address (or behind a reverse proxy) safely.

Activation:
    1. ``bash systemd/user/install-dashboard.sh --generate-token``
       writes ``~/.config/ascendo/auth.token`` (chmod 0600).
    2. From now on every request must carry ``Authorization: Bearer <token>``
       except the unauth allowlist (``/health``, static assets).

When the token file is absent, the middleware is a no-op — preserves the
current localhost-only ergonomics.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


UNAUTH_ALLOWLIST = {
    "/health",
    "/favicon.ico",
    "/open-url",
}
UNAUTH_PREFIXES = (
    "/static/",
    "/style.css",
    "/app.js",
    "/i18n.js",
    "/index.html",
)


def token_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "ascendo" / "auth.token"


def read_token() -> str | None:
    p = token_path()
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def generate_and_store_token() -> str:
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    p.write_text(token + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return token


def revoke_token() -> bool:
    p = token_path()
    if not p.exists():
        return False
    try:
        p.unlink()
        return True
    except Exception:
        return False


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid bearer token when one is configured.

    The token is re-read on every request so rotating it (via ``revoke`` →
    ``generate``) takes effect immediately, no restart needed.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Static + health bypass
        if path in UNAUTH_ALLOWLIST or any(path.startswith(p) for p in UNAUTH_PREFIXES) or path == "/":
            return await call_next(request)
        token = read_token()
        if not token:
            # No token configured → middleware off, behave as before.
            return await call_next(request)
        sent = request.headers.get("authorization") or ""
        prefix = "bearer "
        if sent.lower().startswith(prefix) and secrets.compare_digest(sent[len(prefix):], token):
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": {"code": "AUTH-REQUIRED", "msg": "missing or invalid bearer token"}},
        )

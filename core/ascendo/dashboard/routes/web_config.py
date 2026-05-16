"""Web-config surface (Phase A / Phase C).

- ``POST /web/open`` — explicit, user-initiated launch of a web app
  *after* a run so the operator can run its in-app updater. This is a
  deliberate gesture, not the Sesja-73-forbidden auto-launch during
  apply.
- ``POST /web/probe-entry`` (added in Phase C) — read-only dry-run of a
  candidate ``web_apps.toml`` entry for the AI-config loop.

The macOS web registry lives in the ``ascendo_macos`` adapter package
(on PYTHONPATH at runtime). Imports are lazy + fail-soft so this module
loads cleanly on non-macOS hosts (the routes just report unavailable).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)
router = APIRouter()


# ── Shared registry helpers (reused by Phase C) ──────────────────────────────


def _shipped_registry_path() -> Path | None:
    """Absolute path to the shipped ``adapters/macos/config/web_apps.toml``."""
    try:
        import ascendo_macos  # type: ignore

        pkg_dir = Path(ascendo_macos.__file__).resolve().parent
        # ascendo_macos/ -> adapters/macos/ -> config/web_apps.toml
        return pkg_dir.parent / "config" / "web_apps.toml"
    except Exception:  # noqa: BLE001
        return None


def _user_registry_path() -> Path:
    override = os.environ.get("ASCENDO_WEB_USER_REGISTRY_PATH")
    if override:
        return Path(override).expanduser()
    return Path("~/.config/ascendo/web_apps.toml").expanduser()


def load_web_registry() -> Any | None:
    """Load the merged (shipped + user override) web registry, or None.

    Never raises — returns ``None`` when the adapter / registry is
    unavailable (e.g. non-macOS host, parse error).
    """
    shipped = _shipped_registry_path()
    if shipped is None or not shipped.is_file():
        return None
    try:
        from ascendo_macos.web_registry import WebRegistry  # type: ignore

        user = _user_registry_path()
        return WebRegistry.load(shipped, user if user.exists() else None)
    except Exception:  # noqa: BLE001
        _log.exception("web registry load failed")
        return None


# ── POST /web/open (Phase A) ─────────────────────────────────────────────────


class WebOpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9-]+$", min_length=1, max_length=64)


def _open_bundle(bundle_id: str | None, app_path: str | None) -> bool:
    """Best-effort GUI launch. Indirected so tests can patch it."""
    try:
        if bundle_id:
            return (
                subprocess.run(
                    ["open", "-b", bundle_id], timeout=10, check=False
                ).returncode
                == 0
            )
        if app_path:
            return (
                subprocess.run(
                    ["open", "-a", app_path], timeout=10, check=False
                ).returncode
                == 0
            )
    except Exception:  # noqa: BLE001
        _log.exception("open failed for %s", bundle_id or app_path)
    return False


@router.post("/web/open")
def web_open(body: WebOpenBody) -> dict:
    """Open a web app's GUI so the operator can run its in-app updater.

    User-initiated, post-run gesture (the Action-required panel's "Open"
    button). 404 when the slug is not in the merged registry.
    """
    reg = load_web_registry()
    app = reg.find(body.slug) if reg is not None else None
    if app is None:
        raise HTTPException(
            status_code=404, detail=f"unknown web app slug: {body.slug}"
        )
    app_path = str(app.app_path) if getattr(app, "app_path", None) else None
    ok = _open_bundle(app.bundle_id, app_path)
    return {"ok": ok, "slug": body.slug, "bundle_id": app.bundle_id}

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

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
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


# ── POST /web/probe-entry (Phase C: read-only candidate dry-run) ─────────────

# Handlers with no version probe by design (Tier-B). Probing them is a
# definitive "no" without shelling out.
_NO_PROBE_HANDLERS = {"squirrel", "builtin"}


def _adapter_lib_dir() -> Path | None:
    try:
        import ascendo_macos  # type: ignore

        return Path(ascendo_macos.__file__).resolve().parent.parent / "lib"
    except Exception:  # noqa: BLE001
        return None


def _validate_entry(raw: dict) -> Any:
    """Validate a candidate entry against the macOS WebApp model.

    Returns the WebApp instance, or raises HTTPException(422) with the
    pydantic error detail. HTTPException(503) when the adapter model is
    unavailable (non-macOS host).
    """
    try:
        from ascendo_macos.web_registry import WebApp  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail="web registry model unavailable"
        ) from exc
    try:
        from pydantic import ValidationError

        return WebApp.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _probe_handler(slug: str, handler: str, cfg_json: str) -> tuple[str, str]:
    """Run ``<handler>_check`` for ONE entry in isolation via the adapter's
    existing _web_probe_parallel machinery. Read-only. Returns
    (resolved_version, raw_stderr). 8s timeout."""
    lib = _adapter_lib_dir()
    if lib is None or not (lib / "ascendo_web.sh").is_file():
        return ("", "adapter lib unavailable")
    with tempfile.TemporaryDirectory(prefix="ascendo-probe-") as td:
        td_p = Path(td)
        (td_p / "0.slug").write_text(slug, encoding="utf-8")
        (td_p / "0.handler").write_text(handler, encoding="utf-8")
        (td_p / "0.cfg.json").write_text(cfg_json, encoding="utf-8")
        (td_p / "_indices").write_text("0\n", encoding="utf-8")
        script = (
            'set -e; . "$1/ascendo_web.sh"; '
            '_web_probe_parallel "$2/_indices" "$2"'
        )
        try:
            proc = subprocess.run(
                ["bash", "-c", script, "_", str(lib), str(td_p)],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ("", "probe timed out")
        out_f = td_p / "0.txt"
        resolved = (
            out_f.read_text(encoding="utf-8").strip()
            if out_f.is_file()
            else ""
        )
        return (resolved, (proc.stderr or "")[-800:])


@router.post("/web/probe-entry")
def web_probe_entry(raw: dict = Body(...)) -> dict:
    """Read-only dry-run of ONE candidate web_apps.toml entry.

    Validates the entry against the WebApp schema, runs its handler's
    check in isolation, and reports the resolved version or the exact
    failure. Never installs, never writes any file. The AI-config loop
    calls this (auto-fired, read-only) to iterate until an entry works.
    """
    app = _validate_entry(raw)
    handler = app.handler
    cfg_json = json.dumps(app.model_dump(mode="json"), separators=(",", ":"))

    if handler in _NO_PROBE_HANDLERS:
        return {
            "ok": False,
            "validated": True,
            "handler": handler,
            "resolved_version": "",
            "error": (
                f"handler '{handler}' is Tier-B (no version probe) — "
                "use sparkle / github_dmg / release_feed / omaha / "
                "msupdate for a real candidate probe"
            ),
            "raw_probe_output": "",
        }

    resolved, raw_err = _probe_handler(app.slug, handler, cfg_json)
    ok = bool(resolved)
    return {
        "ok": ok,
        "validated": True,
        "handler": handler,
        "resolved_version": resolved,
        "error": "" if ok else "probe returned no version",
        "raw_probe_output": raw_err,
    }

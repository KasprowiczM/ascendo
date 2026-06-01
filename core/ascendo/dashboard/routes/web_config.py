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


# ── Adapter-resolved web registry provider (A5 decoupling) ───────────────────
#
# core MUST NOT import an adapter package (ADR-0005). The dashboard lifespan
# registers the active adapter here via set_active_adapter(); the helpers below
# then resolve the web registry through ``adapter.web_registry()`` instead of
# importing ``ascendo_macos`` directly. The legacy direct import survives only
# as a documented fallback for callers that run with no adapter registered
# (e.g. a unit test exercising load_web_registry() in isolation).

_ACTIVE_ADAPTER: Any = None


def set_active_adapter(adapter: Any) -> None:
    """Called by the dashboard lifespan once the active adapter is resolved."""
    global _ACTIVE_ADAPTER
    _ACTIVE_ADAPTER = adapter


def _provider() -> Any | None:
    """The active adapter's web-registry provider, or ``None``."""
    adapter = _ACTIVE_ADAPTER
    if adapter is None:
        return None
    try:
        return adapter.web_registry()
    except Exception:  # noqa: BLE001 — a broken accessor must not 500 the route
        _log.exception("adapter.web_registry() raised")
        return None


# ── Shared registry helpers (reused by Phase C) ──────────────────────────────


def _shipped_registry_path() -> Path | None:
    """Absolute path to the OS adapter's shipped ``web_apps.toml``."""
    prov = _provider()
    if prov is not None and prov.shipped_registry_path is not None:
        return prov.shipped_registry_path
    # Fallback (no adapter registered): legacy direct resolution.
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
    prov = _provider()
    if prov is not None:
        return prov.load_merged(_user_registry_path())
    # Fallback (no adapter registered): legacy direct load.
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
    prov = _provider()
    if prov is not None and prov.lib_dir is not None:
        return prov.lib_dir
    # Fallback (no adapter registered): legacy direct resolution.
    try:
        import ascendo_macos  # type: ignore

        return Path(ascendo_macos.__file__).resolve().parent.parent / "lib"
    except Exception:  # noqa: BLE001
        return None


def _validate_entry(raw: dict) -> Any:
    """Validate a candidate entry against the OS WebApp model.

    Returns the WebApp instance, or raises HTTPException(422) with the
    pydantic error detail. HTTPException(503) when the model is unavailable
    (no adapter with a web registry on this host).
    """
    from pydantic import ValidationError

    prov = _provider()
    if prov is not None:
        try:
            return prov.validate_app(raw)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
    # Fallback (no adapter registered): legacy direct import.
    try:
        from ascendo_macos.web_registry import WebApp  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail="web registry model unavailable"
        ) from exc
    try:
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


# ── User-override write + merge (Phase C: the AI-config write action) ────────


def _toml_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


# WebApp sub-models that serialise as their own [app.<key>] table.
_SUBTABLES = ("release_feed", "msupdate", "omaha")


def _dump_app(app: dict) -> str:
    lines = ["[[app]]"]
    for k, val in app.items():
        if k in _SUBTABLES or isinstance(val, dict):
            continue
        lines.append(f"{k} = {_toml_val(val)}")
    for sub in _SUBTABLES:
        if isinstance(app.get(sub), dict):
            lines.append(f"\n[app.{sub}]")
            for k, val in app[sub].items():
                lines.append(f"{k} = {_toml_val(val)}")
    return "\n".join(lines)


def _dump_registry(apps: list[dict]) -> str:
    body = ['schema = "ascendo-web-apps/v2"', ""]
    for a in apps:
        body.append(_dump_app(a))
        body.append("")
    return "\n".join(body).rstrip() + "\n"


def write_user_override(app: Any) -> Path:
    """Atomic upsert (by bundle_id) of a validated WebApp into the user's
    ~/.config/ascendo/web_apps.toml. Parse-validates the MERGED registry
    before the temp→replace so a bad merge can never corrupt the file."""
    import tomllib

    path = _user_registry_path()
    existing: list[dict] = []
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            raw_apps = data.get("app", [])
            existing = raw_apps if isinstance(raw_apps, list) else [raw_apps]
        except Exception:  # noqa: BLE001
            existing = []

    new_app = app.model_dump(mode="json", exclude_none=True)
    merged = [a for a in existing if a.get("bundle_id") != app.bundle_id]
    merged.append(new_app)

    # Hard gate: the merged registry MUST validate before we touch disk.
    prov = _provider()
    if prov is not None:
        prov.validate_registry(merged)
    else:
        from ascendo_macos.web_registry import WebRegistry  # type: ignore

        WebRegistry.model_validate(
            {"schema": "ascendo-web-apps/v2", "app": merged}
        )
    rendered = _dump_registry(merged)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".web_apps-", suffix=".toml", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def apply_web_override(slug: str, toml_snippet: str) -> dict:
    """Validate → final-gate probe → atomic merge. The write happens
    ONLY when validation + the gate pass; on any failure the user file
    is left untouched and the failure is returned (the AI keeps
    iterating). Never raises for expected failures."""
    import tomllib

    from pydantic import ValidationError

    # Validate via the adapter's provider (A5); fall back to the legacy direct
    # model import only when no adapter is registered.
    prov = _provider()
    _validate_app = None
    if prov is not None:
        _validate_app = prov.validate_app
    else:
        try:
            from ascendo_macos.web_registry import WebApp  # type: ignore

            _validate_app = WebApp.model_validate
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "web registry model unavailable"}

    try:
        doc = tomllib.loads(toml_snippet)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"invalid TOML: {exc}"}

    raw_apps = doc.get("app")
    if isinstance(raw_apps, list) and raw_apps:
        candidate = raw_apps[0]
    elif isinstance(raw_apps, dict):
        candidate = raw_apps
    else:
        candidate = {k: v for k, v in doc.items() if k != "schema"}

    try:
        app = _validate_app(candidate)
    except ValidationError as exc:
        return {"ok": False, "error": "schema invalid", "detail": exc.errors()}

    if app.slug != slug:
        return {
            "ok": False,
            "error": f"slug mismatch: body says '{slug}', "
            f"TOML says '{app.slug}'",
        }

    # Final-gate probe (skip for Tier-B handlers which have no probe).
    probe: dict = {"skipped": True}
    if app.handler not in _NO_PROBE_HANDLERS:
        cfg_json = json.dumps(
            app.model_dump(mode="json"), separators=(",", ":")
        )
        resolved, raw_err = _probe_handler(app.slug, app.handler, cfg_json)
        probe = {"resolved_version": resolved, "raw": raw_err}
        if not resolved:
            return {
                "ok": False,
                "error": "probe gate failed — entry does not resolve a "
                "version; not written",
                "probe": probe,
            }

    try:
        path = write_user_override(app)
    except Exception as exc:  # noqa: BLE001
        _log.exception("write_user_override failed")
        return {"ok": False, "error": f"write failed: {exc}"}

    return {
        "ok": True,
        "slug": app.slug,
        "bundle_id": app.bundle_id,
        "written_to": str(path),
        "probe": probe,
    }


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

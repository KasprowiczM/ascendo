"""Transient stub endpoints — keep the legacy Ubuntu_Aktualizacje SPA
rendering on Windows while the real implementations are still being
ported behind the :class:`IAdapter` firewall.

Every handler in this file is a placeholder. They return shaped-correctly
empty / sensible-default JSON so the frontend (``app/frontend/app.js``)
loads without console errors. **Replace each with a real endpoint when
the underlying capability lands** — and delete the stub from this file
when you do.

Mapping ``app.js`` -> stub source-of-truth lives at the top of the module
in :data:`_SPA_FETCH_INVENTORY`. Keep that table accurate as endpoints
graduate from stub to real implementation.

Design notes:

- All handlers are ``async`` so FastAPI doesn't burn a threadpool slot.
- Adapter / inventory access is best-effort: we try once and fall back to
  empty defaults on any exception. The dashboard MUST keep rendering
  even on a clean install with zero packages.
- Mutation endpoints (``POST`` / ``PUT``) accept arbitrary JSON via
  ``Request.body()`` and respond ``{"ok": True, "stub": True, ...}``.
  Real implementations should validate with a Pydantic model.

This module is imported by :func:`ascendo.dashboard.app.create_app` and
mounted with **no prefix** so SPA call paths match 1:1.
"""
from __future__ import annotations

import logging
import platform
import sys
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

if TYPE_CHECKING:
    from ...interfaces.adapter import IAdapter

_log = logging.getLogger(__name__)

router = APIRouter(tags=["spa-stubs"], include_in_schema=False)


# -- inventory of every fetch URL the legacy SPA emits ---------------------

#: Reference table -- kept in sync with ``app/frontend/app.js``.
#: ``"served"`` means the canonical router (health/runs) handles it.
_SPA_FETCH_INVENTORY: dict[str, str] = {
    # Already served by health.py / runs.py:
    "GET /version": "served",
    "GET /health": "served",
    "GET /runs": "served",
    "POST /runs": "served",
    "GET /runs/{id}": "served",
    # Served by spa_real.py (B1+B2+B3):
    "GET /categories": "served",
    "GET /inventory": "served",
    "GET /inventory/summary": "served",
    "GET /inventory/{cat}": "served",
    "GET /health/check": "served",
    "POST /health/run": "served",
    "POST /inventory/refresh": "served",
    "GET /runs/active": "served",
    "GET /runs/active/stream": "served",
    "POST /runs/active/stop": "served",
    # Stubbed below:
    "GET /about": "served",
    "GET /about/release-notes": "served",
    "GET /apps/detect": "served",
    "GET /apps/excluded": "served",
    "POST /apps/exclude": "served",
    "POST /apps/include": "served",
    "GET /exclusions": "stub",
    "GET /git/status": "stub",
    "GET /hosts": "stub-from-adapter",
    "GET /hosts/list": "stub-from-adapter",
    "GET /onboarding/state": "served",
    "GET /preflight": "stub",
    "GET /profiles": "stub",
    "GET /profiles/templates": "stub",
    "GET /settings": "stub",
    "GET /sudo/status": "stub",
    "GET /suggestions": "stub",
    "GET /sync/browse": "stub",
    "GET /sync/provider": "stub",
    "GET /sync/remotes": "stub",
    "GET /sync/status": "stub",
    "GET /telemetry/eta": "stub",
    "GET /updates/check": "stub",
    "GET /hosts/{id}/preflight": "stub",
    "GET /runs/{id}/phase/{cat}/{phase}/log": "stub",
    "POST /apps/add": "stub",
    "POST /apps/remove": "stub",
    "POST /apt/downgrade": "stub",
    "POST /backup/import": "stub",
    "GET /backup/export": "stub",
    "POST /git/fetch": "stub",
    "POST /git/pull": "stub",
    "POST /git/push": "stub",
    "POST /hosts/delete": "stub",
    "POST /hosts/upsert": "stub",
    "POST /onboarding/complete": "served",
    "POST /profiles/import": "stub",
    "POST /scheduler/install": "stub",
    "POST /scheduler/remove": "stub",
    "POST /suggestions/apply": "stub",
    "POST /suggestions/dismiss": "stub",
    "POST /suggestions/test": "stub",
    "POST /sudo/auth": "stub",
    "POST /sync/export": "stub",
    "POST /sync/provider": "stub",
    "POST /sync/provider/test": "stub",
    "POST /system/reboot": "stub",
    "PUT /settings": "stub",
}


# -- helpers ---------------------------------------------------------------


def _get_adapter(request: Request) -> IAdapter | None:
    """Best-effort adapter accessor -- never raises."""
    return getattr(request.app.state, "adapter", None)


def _safe_host(adapter: IAdapter | None) -> dict[str, Any]:
    """Return a host dict or a typed-empty fallback when detection fails."""
    if adapter is None:
        return {
            "hostname": platform.node(),
            "os": sys.platform,
            "arch": platform.machine(),
            "is_self": True,
        }
    try:
        host = adapter.detect_host()
        return {
            "hostname": host.hostname,
            "os": host.os.value if hasattr(host.os, "value") else str(host.os),
            "os_version": host.os_version,
            "arch": host.arch,
            "user": host.user,
            "is_self": True,
        }
    except Exception:  # noqa: BLE001
        _log.debug("detect_host() failed in stub", exc_info=True)
        return {
            "hostname": platform.node(),
            "os": sys.platform,
            "arch": platform.machine(),
            "is_self": True,
        }


# -- About / preflight -----------------------------------------------------


# /about and /about/release-notes -- served by routes/about.py.


@router.get("/preflight")
async def preflight_stub() -> dict[str, Any]:
    """Health summary card on Overview -- 'all clear' until a real check lands."""
    return {"ok": True, "checks": [], "warnings": [], "errors": []}


# /categories, /inventory*, /health/check, /health/run -- served by spa_real.


# -- Apps registration (config/*.list equivalent) --------------------------


# /apps/detect, /apps/exclude, /apps/include, /apps/excluded
# -- served by routes/apps.py (default-include model + persistent
#    %APPDATA%\\Ascendo\\excluded.json store).
# Legacy /apps/add and /apps/remove still routed here as no-ops for any
# old-SPA tab that POSTs them; the new model uses /apps/exclude + /apps/include.


@router.post("/apps/add")
async def apps_add_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True, "message": "deprecated; use /apps/include"}


@router.post("/apps/remove")
async def apps_remove_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True, "message": "deprecated; use /apps/exclude"}


@router.post("/apt/downgrade")
async def apt_downgrade_stub() -> dict[str, Any]:
    return {"ok": False, "stub": True, "error": "apt is Linux-only"}


# -- Hosts -----------------------------------------------------------------


@router.get("/hosts")
async def hosts_stub(request: Request) -> dict[str, Any]:
    """Single-host registry with the local machine."""
    return {"hosts": [_safe_host(_get_adapter(request))]}


@router.get("/hosts/list")
async def hosts_list_stub(request: Request) -> list[dict[str, Any]]:
    return [_safe_host(_get_adapter(request))]


@router.get("/hosts/{host_id}/preflight")
async def hosts_preflight_stub(host_id: str) -> dict[str, Any]:
    return {"host": host_id, "ok": True, "checks": [], "stub": True}


@router.post("/hosts/upsert")
async def hosts_upsert_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


@router.post("/hosts/delete")
async def hosts_delete_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


# -- Settings / onboarding -------------------------------------------------


@router.get("/settings")
async def settings_get_stub() -> dict[str, Any]:
    """User settings -- empty until persistent settings storage lands."""
    return {}


@router.put("/settings")
async def settings_put_stub() -> dict[str, Any]:
    """Drop-on-floor write so Save buttons return 200 cleanly."""
    return {"ok": True, "stub": True}


# /onboarding/state and /onboarding/complete -- served by routes/onboarding.py
# (graduated to real persistent JSON storage; see _SPA_FETCH_INVENTORY).


# -- Profiles --------------------------------------------------------------


@router.get("/profiles")
async def profiles_stub() -> dict[str, Any]:
    return {
        "profiles": [
            {"id": "quick", "label": "quick"},
            {"id": "safe", "label": "safe"},
            {"id": "full", "label": "full"},
        ],
    }


@router.get("/profiles/templates")
async def profiles_templates_stub() -> dict[str, Any]:
    return {"templates": []}


@router.post("/profiles/import")
async def profiles_import_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


# -- Suggestions -----------------------------------------------------------


@router.get("/suggestions")
async def suggestions_stub() -> dict[str, Any]:
    return {"suggestions": []}


@router.post("/suggestions/apply")
async def suggestions_apply_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


@router.post("/suggestions/dismiss")
async def suggestions_dismiss_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


@router.post("/suggestions/test")
async def suggestions_test_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True, "provider_ok": False, "message": "AI provider unconfigured"}


# -- Exclusions ------------------------------------------------------------


@router.get("/exclusions")
async def exclusions_stub() -> dict[str, Any]:
    return {"exclusions": []}


# -- Git / sync ------------------------------------------------------------


@router.get("/git/status")
async def git_status_stub() -> dict[str, Any]:
    return {"branch": None, "ahead": 0, "behind": 0, "dirty": False, "stub": True}


@router.post("/git/fetch")
async def git_fetch_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True, "output": ""}


@router.post("/git/pull")
async def git_pull_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True, "output": ""}


@router.post("/git/push")
async def git_push_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True, "output": ""}


@router.get("/sync/status")
async def sync_status_stub() -> dict[str, Any]:
    return {"enabled": False, "last_sync": None}


@router.get("/sync/provider")
async def sync_provider_stub() -> dict[str, Any]:
    return {"provider": None, "remote_name": "", "remote_path": "", "copy_only": True}


@router.post("/sync/provider")
async def sync_provider_set_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


@router.post("/sync/provider/test")
async def sync_provider_test_stub() -> dict[str, Any]:
    return {"ok": False, "stub": True, "message": "rclone not configured"}


@router.get("/sync/remotes")
async def sync_remotes_stub() -> dict[str, Any]:
    return {"remotes": []}


@router.get("/sync/browse")
async def sync_browse_stub(path: str = "") -> dict[str, Any]:
    return {"path": path, "entries": [], "stub": True}


@router.post("/sync/export")
async def sync_export_stub(dry_run: bool = False) -> dict[str, Any]:
    return {"ok": True, "stub": True, "dry_run": dry_run, "output": ""}


# -- Sudo / system ---------------------------------------------------------


@router.get("/sudo/status")
async def sudo_status_stub(request: Request) -> dict[str, Any]:
    """Adapter-aware shim: when an IElevation backend with password
    registration is available (macOS), report the real cache state so
    the SPA pops the modal on first apply. Windows uses UAC per-call
    and has no in-memory cache, so it returns cached=True to avoid
    spurious prompts the OS can't satisfy from a web flow.

    Linux without sudo askpass falls back to cached=True too — the user
    must `sudo -v` from the terminal that launched the dashboard.

    macOS Touch ID path: when the operator authenticates via Touch ID
    in a TTY-PAM apply phase, the OS sudo timestamp is refreshed but
    MacElevation never sees a password. Probe `sudo -n -v` so the
    footer pill flips to "sudo active" within ~30 s of a Touch ID
    success — without this, the pill stays "sudo not active" forever
    even though every subsequent apply succeeds silently.
    """
    adapter = getattr(request.app.state, "adapter", None)
    cached_via_password = False
    if adapter is not None:
        try:
            elev = adapter.elevation()
        except Exception:  # noqa: BLE001
            elev = None
        if elev is not None and callable(getattr(elev, "has_password_registered", None)):
            cached_via_password = bool(elev.has_password_registered())
            if cached_via_password:
                return {"cached": True, "method": "askpass"}
            # No password registered — probe the OS sudo timestamp on
            # POSIX. `sudo -n -v` exits 0 when there's a fresh cached
            # credential (Touch ID, prior sudo -v, etc.) and non-zero
            # otherwise. Bounded 1-second timeout so a wedged sudo
            # never blocks the polling pill.
            import platform
            import subprocess
            if platform.system() in ("Darwin", "Linux"):
                try:
                    r = subprocess.run(
                        ["sudo", "-n", "-v"],
                        capture_output=True,
                        timeout=1,
                        check=False,
                    )
                    if r.returncode == 0:
                        return {"cached": True, "method": "timestamp"}
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return {"cached": False}
    return {"cached": True, "stub": True}


@router.post("/sudo/auth")
async def sudo_auth_stub(request: Request) -> dict[str, Any]:
    """Forward password to the real IElevation backend when available;
    otherwise no-op (Windows / Linux without askpass)."""
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        return {"ok": True, "cached": True, "stub": True}
    try:
        elev = adapter.elevation()
    except Exception:  # noqa: BLE001
        elev = None
    if elev is None or not callable(getattr(elev, "register_password", None)):
        return {"ok": True, "cached": True, "stub": True}
    body = await request.json()
    password = body.get("password") if isinstance(body, dict) else None
    if not password:
        raise HTTPException(status_code=400, detail="password required")
    try:
        elev.register_password(password)
    except Exception as exc:  # noqa: BLE001
        # Wrong password / probe failure → 401. The real /elevation/auth
        # handler does the same; we mirror it here for the legacy SPA path.
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True, "cached": True}


@router.post("/system/reboot")
async def system_reboot_stub(delay: int = 5) -> dict[str, Any]:
    return {"ok": False, "stub": True, "delay": delay, "message": "reboot stubbed"}


# -- Scheduler -------------------------------------------------------------


@router.post("/scheduler/install")
async def scheduler_install_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


@router.post("/scheduler/remove")
async def scheduler_remove_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


# -- Telemetry / updates ---------------------------------------------------


@router.get("/telemetry/eta")
async def telemetry_eta_stub() -> dict[str, Any]:
    return {"eta_ms": None, "samples": 0}


@router.get("/updates/check")
async def updates_check_stub() -> dict[str, Any]:
    return {"latest": None, "current": None, "needs_update": False, "stub": True}


# -- Backup ----------------------------------------------------------------


@router.get("/backup/export")
async def backup_export_stub() -> Response:
    """Empty placeholder bundle until real export lands."""
    return Response(
        content=b"",
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="ascendo-backup.tar.gz"'},
    )


@router.post("/backup/import")
async def backup_import_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


# /runs/active, /runs/active/stop, /runs/active/stream -- served by spa_real.


# -- Per-phase log (still stub) --------------------------------------------


@router.get("/runs/{run_id}/phase/{category}/{phase}/log")
async def run_phase_log_stub(run_id: str, category: str, phase: str) -> Response:
    """Plain-text per-phase log -- empty until the orchestrator persists logs."""
    body = f"# log for run={run_id} category={category} phase={phase}\n# (not yet recorded)\n"
    return PlainTextResponse(body)


__all__ = ["router", "_SPA_FETCH_INVENTORY"]

"""Transient stub endpoints — keep the legacy Ascendo SPA
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
    "GET /sync/config-status": "stub",  # dev-only (wizard step 8)
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
    "POST /scheduler/install": "served (scheduler_real.py — Sesja 67)",
    "POST /scheduler/remove":  "served (scheduler_real.py — Sesja 67)",
    "POST /scheduler/trigger": "served (scheduler_real.py — Sesja 67)",
    "GET  /scheduler/list":    "served (scheduler_real.py — Sesja 67)",
    "POST /suggestions/apply": "stub",
    "POST /suggestions/dismiss": "stub",
    "POST /suggestions/test": "stub",
    "POST /sudo/auth": "stub",
    "POST /sync/export": "stub",
    "POST /sync/provider": "stub",
    "POST /sync/provider/test": "stub",
    "POST /sync/setup": "stub",  # dev-only (wizard step 8)
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


# -- Dev-edition only: dev-sync provider setup wizard helpers --------------
#
# Both endpoints below sit under the ``/sync/`` prefix, which is gated by
# :class:`~ascendo.dashboard.middleware.edition_gate.EditionGateMiddleware`
# to return 404 on basic edition. Dev edition exposes them so the
# first-run wizard's "Run dev-sync setup" step can probe state and shell
# out to the user-side script without leaving the dashboard.


def _dev_sync_repo_root() -> "Path":
    """Resolve repo root from this file's location.

    spa_stubs.py lives at ``core/ascendo/dashboard/routes/spa_stubs.py``,
    so four parents up gets us to ``<repo>``.
    """
    from pathlib import Path
    return Path(__file__).resolve().parents[4]


@router.get("/sync/config-status")
async def sync_config_status() -> dict[str, Any]:
    """Report the state of ``.dev_sync_config.json`` for the wizard.

    Returns one of three states:

    - ``present=True, valid=True``  -> JSON parses, ``provider`` populated
    - ``present=True, valid=False`` -> file exists but is malformed
    - ``present=False, valid=False`` -> not yet configured

    Dev-edition only; the EditionGateMiddleware 404s this on basic.
    """
    import json

    repo_root = _dev_sync_repo_root()
    cfg_path = repo_root / ".dev_sync_config.json"
    if not cfg_path.is_file():
        return {
            "present": False,
            "valid": False,
            "provider": None,
            "path": str(cfg_path),
        }
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "present": True,
            "valid": False,
            "provider": None,
            "path": str(cfg_path),
            "error": str(exc),
        }
    provider = None
    if isinstance(data, dict):
        provider = data.get("provider") or data.get("remote") or None
    return {
        "present": True,
        "valid": True,
        "provider": provider,
        "path": str(cfg_path),
    }


@router.post("/sync/setup")
async def sync_setup() -> dict[str, Any]:
    """Invoke the dev-sync provider setup script and return its output.

    The script is part of the dev-sync overlay (see ``dev-sync/README.md``)
    and walks the operator through configuring rclone + a remote name +
    a remote path. It is interactive in a TTY but degrades to a non-zero
    exit on a bare subprocess — we still return whatever it printed so
    the wizard can surface a useful error.

    Filename probe: tries ``dev-sync-provider-setup.sh`` first (the name
    the wizard step copy mentions for documentation purposes), then
    ``provider_setup.sh`` (the actual file shipped in the overlay).

    Dev-edition only; the EditionGateMiddleware 404s this on basic.
    """
    import subprocess

    repo_root = _dev_sync_repo_root()
    candidates = [
        repo_root / "dev-sync" / "dev-sync-provider-setup.sh",
        repo_root / "dev-sync" / "provider_setup.sh",
    ]
    script = next((c for c in candidates if c.is_file()), None)
    if script is None:
        raise HTTPException(
            status_code=404,
            detail=f"dev-sync provider setup script not found (looked for: "
                   f"{', '.join(str(c) for c in candidates)})",
        )
    try:
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(repo_root),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="provider setup timed out")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to spawn bash: {exc}")
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": (result.stdout or "")[-2000:],
        "stderr": (result.stderr or "")[-2000:],
    }


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
    # The dashboard's own process tells us whether apply subprocesses
    # will have a /dev/tty. If we (the dashboard) have no controlling
    # terminal, neither will the subprocesses we spawn — so the TTY-PAM
    # Touch ID path is unavailable and the modal MUST be shown unless
    # askpass is already wired. This single probe distinguishes
    # Tauri/GUI launch (no TTY) from terminal launch.
    tty_available = False
    try:
        import os as _os
        tty_available = _os.isatty(0) or _os.path.exists("/dev/tty")
        # /dev/tty existence is not enough — try opening it like
        # _ascendo_sudo_warm does. If THAT works, PAM can drive the
        # Touch ID sheet from a child process.
        if tty_available:
            try:
                fd = _os.open("/dev/tty", _os.O_RDWR | _os.O_NOCTTY)
                _os.close(fd)
            except OSError:
                tty_available = False
    except Exception:  # noqa: BLE001
        tty_available = False

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
                # Real askpass IS wired (a password is registered, the
                # askpass helper file exists, SUDO_ASKPASS will be set in
                # subprocesses). Safe for the SPA to skip the modal.
                return {
                    "cached": True,
                    "method": "askpass",
                    "askpass_ready": True,
                    "tty_available": tty_available,
                }
            # No password registered. Probe the OS sudo timestamp via
            # `sudo -n -v` (1s timeout) — this tells us whether the OS
            # would let the apply scripts elevate WITHOUT a password
            # prompt. BUT: even when cached=True via timestamp, the apply
            # subprocesses have NO SUDO_ASKPASS set (we never registered
            # one), and if they're spawned from a GUI launch (Tauri,
            # double-click .app) they also have NO /dev/tty for PAM
            # Touch ID. So timestamp-only "cached" was misleading the SPA
            # into skipping the modal AND then apply hung waiting for a
            # password it couldn't read.
            #
            # The fix: return `askpass_ready=False` whenever no password
            # is registered. SPA gates the modal-skip on
            # `askpass_ready === true`, not on `cached` alone. The
            # timestamp branch still surfaces "cached=true" for the UI
            # footer pill, but the SPA's apply-flow logic now refuses to
            # rely on it.
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
                        return {
                            "cached": True,
                            "method": "timestamp",
                            "askpass_ready": False,
                            "tty_available": tty_available,
                        }
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return {
                "cached": False,
                "askpass_ready": False,
                "tty_available": tty_available,
            }
    return {
        "cached": True,
        "stub": True,
        "askpass_ready": False,
        "tty_available": tty_available,
    }


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
# Sesja 67: /scheduler/* endpoints are now served by scheduler_real.py
# which drives the adapter's IScheduler implementation (WindowsScheduler /
# LaunchdScheduler / SystemdScheduler). No stubs needed any more — the
# real router is mounted BEFORE spa_stubs in app.py so collisions are
# impossible. If a future adapter doesn't implement SCHEDULING the real
# router returns HTTP 503 with a helpful message.


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

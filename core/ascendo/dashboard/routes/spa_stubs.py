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

from fastapi import APIRouter, Request
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
    # Stubbed below:
    "GET /about": "stub",
    "GET /apps/detect": "stub",
    "GET /categories": "stub-from-adapter",
    "GET /exclusions": "stub",
    "GET /git/status": "stub",
    "GET /health/check": "stub",
    "GET /hosts": "stub-from-adapter",
    "GET /hosts/list": "stub-from-adapter",
    "GET /inventory": "stub-from-adapter",
    "GET /inventory/summary": "stub-from-adapter",
    "GET /inventory/{cat}": "stub-from-adapter",
    "GET /onboarding/state": "stub",
    "GET /preflight": "stub",
    "GET /profiles": "stub",
    "GET /profiles/templates": "stub",
    "GET /runs/active": "stub",
    "GET /runs/active/stream": "stub-sse",
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
    "POST /health/run": "stub",
    "POST /hosts/delete": "stub",
    "POST /hosts/upsert": "stub",
    "POST /inventory/refresh": "stub",
    "POST /onboarding/complete": "stub",
    "POST /profiles/import": "stub",
    "POST /runs/active/stop": "stub",
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


def _empty_inv_summary() -> dict[str, Any]:
    return {
        "totals": {"ok": 0, "outdated": 0, "missing": 0, "total": 0},
        "categories": {},
    }


# -- About / preflight / health-check --------------------------------------


@router.get("/about")
async def about_stub(request: Request) -> dict[str, Any]:
    """Minimal /about payload for the About tab."""
    from ... import __version__

    adapter = _get_adapter(request)
    host = _safe_host(adapter)
    return {
        "name": "Ascendo",
        "tagline": "unified updates",
        "version": __version__,
        "git_head": None,
        "python": sys.version.split()[0],
        "host": host.get("hostname"),
        "distro": host.get("os"),
        "kernel": host.get("os_version"),
        "arch": host.get("arch"),
        "release_notes_md": "",
    }


@router.get("/preflight")
async def preflight_stub() -> dict[str, Any]:
    """Health summary card on Overview -- 'all clear' until a real check lands."""
    return {"ok": True, "checks": [], "warnings": [], "errors": []}


@router.get("/health/check")
async def health_check_stub() -> dict[str, Any]:
    """Post-run health card on Overview."""
    return {
        "score": 100,
        "status": "ok",
        "failed_units": [],
        "dmesg_errors": [],
        "disk_pressure": [],
        "reboot_required": False,
        "checked_at": None,
    }


@router.post("/health/run")
async def health_run_stub() -> dict[str, Any]:
    """Re-check button on Overview re-emits the empty-clean snapshot."""
    snapshot = await health_check_stub()
    return {"ok": True, "stub": True, "snapshot": snapshot}


# -- Categories / inventory ------------------------------------------------


@router.get("/categories")
async def categories_stub(request: Request) -> dict[str, Any]:
    """Sourced from ``adapter.package_managers(host)`` when available."""
    adapter = _get_adapter(request)
    if adapter is None:
        return {"categories": []}
    try:
        host = adapter.detect_host()
        managers = adapter.package_managers(host)
    except Exception:  # noqa: BLE001
        _log.debug("package_managers failed", exc_info=True)
        return {"categories": []}

    cats: list[dict[str, Any]] = []
    for m in managers:
        try:
            cat_id = m.category.value if hasattr(m.category, "value") else str(m.category)
            cats.append(
                {
                    "id": cat_id,
                    "display_name": m.display_name,
                    "available": True,
                },
            )
        except Exception:  # noqa: BLE001
            continue
    return {"categories": cats}


@router.get("/inventory")
async def inventory_stub(request: Request) -> dict[str, Any]:
    """Best-effort inventory -- empty until the inventory service is wired in."""
    adapter = _get_adapter(request)
    if adapter is None:
        return {"categories": {}}
    try:
        inv = adapter.inventory()
        host = adapter.detect_host()
        packages = inv.list_installed(host)
    except Exception:  # noqa: BLE001
        _log.debug("inventory unavailable in stub", exc_info=True)
        return {"categories": {}}

    bucketed: dict[str, list[dict[str, Any]]] = {}
    for pkg in packages:
        try:
            cat_key = pkg.source.value if hasattr(pkg.source, "value") else str(pkg.source)
        except Exception:  # noqa: BLE001
            continue
        bucketed.setdefault(cat_key, []).append(
            {
                "name": getattr(pkg, "name", ""),
                "installed": getattr(pkg, "version", None),
                "candidate": getattr(pkg, "available_version", None),
                "source": cat_key,
                "status": "outdated"
                if getattr(pkg, "available_version", None)
                else "ok",
            },
        )
    return {"categories": bucketed}


@router.get("/inventory/summary")
async def inventory_summary_stub(request: Request) -> dict[str, Any]:
    """Donut+bars summary -- derived from `/inventory` if cheap, else empty."""
    payload = await inventory_stub(request)
    cats = payload.get("categories", {})
    out = _empty_inv_summary()
    for cat_id, items in cats.items():
        ok = sum(1 for i in items if i.get("status") == "ok")
        outdated = sum(1 for i in items if i.get("status") == "outdated")
        missing = sum(1 for i in items if i.get("status") == "missing")
        total = len(items)
        out["categories"][cat_id] = {
            "ok": ok,
            "outdated": outdated,
            "missing": missing,
            "total": total,
        }
        out["totals"]["ok"] += ok
        out["totals"]["outdated"] += outdated
        out["totals"]["missing"] += missing
        out["totals"]["total"] += total
    return out


@router.get("/inventory/{category}")
async def inventory_category_stub(category: str, request: Request) -> dict[str, Any]:
    """Single-category projection of /inventory."""
    payload = await inventory_stub(request)
    items = payload.get("categories", {}).get(category, [])
    return {"category": category, "items": items}


@router.post("/inventory/refresh")
async def inventory_refresh_stub() -> dict[str, Any]:
    """Cache-bust for /inventory -- no cache to bust yet."""
    return {"ok": True, "stub": True, "refreshed": []}


# -- Apps registration (config/*.list equivalent) --------------------------


@router.get("/apps/detect")
async def apps_detect_stub() -> dict[str, Any]:
    """tracked / detected / missing report -- empty for the Windows MVP."""
    return {"tracked": [], "detected": [], "missing": [], "by_category": {}}


@router.post("/apps/add")
async def apps_add_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


@router.post("/apps/remove")
async def apps_remove_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


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


@router.get("/onboarding/state")
async def onboarding_state_stub() -> dict[str, Any]:
    """First-run wizard already considered finished on Windows MVP."""
    return {"completed": True, "stub": True}


@router.post("/onboarding/complete")
async def onboarding_complete_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


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
async def sudo_status_stub() -> dict[str, Any]:
    """Windows uses UAC, not sudo -- always report 'cached' so the SPA
    doesn't constantly prompt for a password it can't use."""
    return {"cached": True, "stub": True}


@router.post("/sudo/auth")
async def sudo_auth_stub() -> dict[str, Any]:
    return {"ok": True, "cached": True, "stub": True}


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


# -- Active run + log routes -----------------------------------------------


@router.get("/runs/active")
async def runs_active_stub(request: Request) -> dict[str, Any]:
    """Report the most recently registered async run, if any."""
    registry = getattr(request.app.state, "run_registry", None)
    if registry is None:
        return {"active": None}
    try:
        active = None
        runs_map = getattr(registry, "_runs", None) or {}
        for run_id, state in runs_map.items():
            status = getattr(state.status, "value", str(state.status))
            if status in ("pending", "running"):
                active = {"run_id": str(run_id), "status": status}
                break
        return {"active": active}
    except Exception:  # noqa: BLE001
        return {"active": None}


@router.post("/runs/active/stop")
async def runs_active_stop_stub() -> dict[str, Any]:
    return {"ok": True, "stub": True}


@router.get("/runs/active/stream")
async def runs_active_stream_stub() -> Response:
    """Empty SSE stream -- one heartbeat then EOF."""
    from fastapi.responses import StreamingResponse

    async def _gen():
        yield b"event: status\ndata: {\"status\": \"idle\"}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.get("/runs/{run_id}/phase/{category}/{phase}/log")
async def run_phase_log_stub(run_id: str, category: str, phase: str) -> Response:
    """Plain-text per-phase log -- empty until the orchestrator persists logs."""
    body = f"# log for run={run_id} category={category} phase={phase}\n# (not yet recorded)\n"
    return PlainTextResponse(body)


__all__ = ["router", "_SPA_FETCH_INVENTORY"]

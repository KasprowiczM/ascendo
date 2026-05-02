"""Real adapter-backed implementations of dashboard endpoints (B1+B2+B3).

Mounted in ``app.py`` BEFORE :mod:`spa_stubs` so its concrete handlers win
on path collisions. The corresponding entries in :mod:`spa_stubs` are
deleted as they graduate to "served" — keep the
:data:`spa_stubs._SPA_FETCH_INVENTORY` table accurate.

Endpoints implemented here:

- ``GET  /categories``         — list adapter package managers (real).
- ``GET  /inventory``          — bucketed installed packages (cached, 60s).
- ``GET  /inventory/summary``  — donut/bars totals computed from /inventory.
- ``GET  /inventory/{cat}``    — single-category projection.
- ``POST /inventory/refresh``  — invalidate the in-memory cache.
- ``GET  /health/check``       — adapter health snapshot + score.
- ``POST /health/run``         — re-run the adapter health snapshot.
- ``GET  /runs/active``        — most recent in-flight async run (or None).
- ``POST /runs/active/stop``   — graceful stop signal (best-effort).
- ``GET  /runs/active/stream`` — SSE stream for the active run.

Design notes:

- :class:`InventoryCache` is per-app, thread-safe, 60s TTL. Stored on
  ``app.state.inventory_cache`` (initialised by :func:`create_app`).
- ``/inventory`` returns 200 with empty buckets when the adapter raises
  :class:`NotImplementedError` (some adapters genuinely lack inventory),
  but propagates other exceptions as 500 with a logged stack trace.
- The SSE stream re-uses the same polling pattern as
  :func:`runs.stream_run_events` but targets the latest active run rather
  than a caller-specified UUID. When no run is active we emit a single
  ``status: idle`` event and close the stream — matching the behaviour
  the legacy stub used and that ``test_dashboard_spa.py`` already
  asserts.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from ...interfaces.adapter import IAdapter
    from ...models.package import Package

_log = logging.getLogger(__name__)

router = APIRouter(tags=["spa-real"], include_in_schema=False)


# ── Version comparator (ported from app/backend/inventory.py, Etap 12) ────

_VER_TOKEN = re.compile(r"(\d+)|([a-zA-Z]+)")


def _ver_key(v: str) -> list:
    """Normalise a version string into a comparable list of tokens.

    Splits on ``.``/``-``/``_``/``+`` then breaks each segment into runs of
    digits vs letters so ``1.10`` > ``1.9``. Letter tokens sort *below*
    numeric ones so ``1.0`` > ``1.0rc1``.
    """
    if not v:
        return []
    s = v.strip().lstrip("vV=")
    segments = re.split(r"[.\-_+]", s)
    key: list = []
    for seg in segments:
        toks = _VER_TOKEN.findall(seg)
        if not toks:
            continue
        for num, alpha in toks:
            if num:
                key.append((1, int(num), ""))
            else:
                key.append((0, 0, alpha.lower()))
    return key


def _version_gt(a: str | None, b: str | None) -> bool:
    """Return True iff ``a`` is *strictly* a newer version than ``b``.

    The Etap 12 fix: ``candidate < installed`` must NOT be flagged as
    ``outdated``. Some npm dist-tags point at older release lines (e.g.
    ``@google/gemini-cli`` ``latest`` -> ``0.1.9`` while installed is
    ``0.40.0``), and the previous "any difference" comparator produced
    phantom downgrade arrows in the SPA.
    """
    if not a or not b or a == b:
        return False
    try:
        return _ver_key(a) > _ver_key(b)
    except Exception:  # noqa: BLE001
        return False


def _classify(installed: str | None, candidate: str | None) -> str:
    """Status of an item given installed + (optional) candidate version."""
    if not installed:
        return "missing" if candidate else "ok"
    if candidate and candidate not in ("(none)", "", "unknown") and candidate != installed:
        if _version_gt(candidate, installed):
            return "outdated"
    return "ok"


# ── Inventory cache (per-app, thread-safe, TTL) ───────────────────────────


class InventoryCache:
    """Per-host, 60s TTL cache of inventory ``Package`` lists.

    The dashboard SPA refreshes Overview every time the user clicks
    "Refresh"; without a cache we'd re-query winget on each visit
    (~10s). With this cache, repeat visits within the TTL are instant.
    """

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self._ttl: float = ttl_seconds
        self._lock = RLock()
        self._items: list[Package] | None = None
        self._loaded_at: float = 0.0

    def get(self, loader: Callable[[], Iterable[Package]]) -> list[Package]:
        """Return cached items or call ``loader()`` to refresh.

        ``loader`` is invoked while the cache lock is held — keep it
        cheap or, more commonly, pass a closure that does the heavy
        lifting and accept that other readers will wait.
        """
        with self._lock:
            now = time.monotonic()
            if self._items is None or now - self._loaded_at > self._ttl:
                self._items = list(loader())
                self._loaded_at = now
            return self._items

    def invalidate(self) -> None:
        """Drop cached items — next call to :meth:`get` will reload."""
        with self._lock:
            self._items = None
            self._loaded_at = 0.0


# ── Helpers ───────────────────────────────────────────────────────────────


def _require_adapter(request: Request) -> IAdapter:
    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="No adapter available.")
    return adapter


def _get_inventory_cache(request: Request) -> InventoryCache:
    cache = getattr(request.app.state, "inventory_cache", None)
    if cache is None:
        # Late-binding fallback so tests that build their own FastAPI
        # without going through create_app still work.
        cache = InventoryCache()
        request.app.state.inventory_cache = cache
    return cache


def _category_key(pkg: Package) -> str:
    """Stable string key for a Package's source category."""
    cat = pkg.category
    return cat.value if hasattr(cat, "value") else str(cat)


def _package_to_item(pkg: Package) -> dict[str, Any]:
    """Render a :class:`Package` as the SPA's inventory row schema.

    The current ``Package`` model carries identity only (id, name,
    category) — no installed/candidate version yet. We expose those as
    ``None`` so the SPA renders the row but doesn't show a phantom
    upgrade arrow. When inventory grows version awareness (M3+), this is
    the single helper to update.
    """
    cat_key = _category_key(pkg)
    installed = getattr(pkg, "version", None)
    candidate = getattr(pkg, "available_version", None)
    return {
        "name": pkg.name,
        "installed": installed,
        "candidate": candidate,
        "source": cat_key,
        "status": _classify(installed, candidate),
        "vendor": getattr(pkg, "vendor", None),
    }


def _bucket(packages: Iterable[Package]) -> dict[str, list[dict[str, Any]]]:
    """Group packages into ``{source: [item, ...]}``."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for pkg in packages:
        cat = _category_key(pkg)
        buckets.setdefault(cat, []).append(_package_to_item(pkg))
    return buckets


def _load_packages(adapter: IAdapter) -> list[Package]:
    """Best-effort inventory load.

    Adapters that raise :class:`NotImplementedError` from ``inventory()``
    (e.g. the FakeAdapter used in tests) yield an empty list rather than
    a 500 — the dashboard MUST keep rendering on a clean install.
    Other exceptions propagate so the operator sees the real failure.
    """
    try:
        inv = adapter.inventory()
    except NotImplementedError:
        return []
    host = adapter.detect_host()
    return list(inv.list_installed(host))


# ── /categories ───────────────────────────────────────────────────────────


@router.get("/categories")
async def categories_real(request: Request) -> dict[str, Any]:
    """List adapter package managers as ``{id, display_name, available, description}``."""
    adapter = _require_adapter(request)
    host = adapter.detect_host()
    managers = adapter.package_managers(host)

    cats: list[dict[str, Any]] = []
    for m in managers:
        cat = m.category
        cat_id = cat.value if hasattr(cat, "value") else str(cat)
        cats.append(
            {
                "id": cat_id,
                "display_name": m.display_name,
                "available": m.is_available(host),
                "description": getattr(m, "description", None),
            },
        )
    return {"categories": cats}


# ── /inventory ────────────────────────────────────────────────────────────


@router.get("/inventory")
async def inventory_real(request: Request) -> dict[str, Any]:
    """Full bucketed inventory keyed by source.

    Returns ``{"categories": {<source>: [item, ...]}}``. Each item is
    ``{name, installed, candidate, source, status, vendor}`` per the
    SPA contract.
    """
    adapter = _require_adapter(request)
    cache = _get_inventory_cache(request)
    packages = cache.get(lambda: _load_packages(adapter))
    return {"categories": _bucket(packages)}


@router.get("/inventory/summary")
async def inventory_summary_real(request: Request) -> dict[str, Any]:
    """Donut/bars totals derived from cached inventory."""
    adapter = _require_adapter(request)
    cache = _get_inventory_cache(request)
    packages = cache.get(lambda: _load_packages(adapter))
    buckets = _bucket(packages)

    out = {
        "totals": {"ok": 0, "outdated": 0, "missing": 0, "total": 0},
        "categories": {},
    }
    for cat_id, items in buckets.items():
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
async def inventory_category_real(category: str, request: Request) -> dict[str, Any]:
    """Single-category projection of /inventory."""
    adapter = _require_adapter(request)
    cache = _get_inventory_cache(request)
    packages = cache.get(lambda: _load_packages(adapter))
    items = _bucket(packages).get(category, [])
    return {"category": category, "items": items}


@router.post("/inventory/refresh")
async def inventory_refresh_real(request: Request) -> dict[str, Any]:
    """Invalidate the inventory cache and report which categories will reload."""
    cache = _get_inventory_cache(request)
    # Compute the list of categories currently cached BEFORE invalidating
    # so the caller has a stable hint of what will be re-fetched.
    refreshed: list[str] = []
    if cache._items is not None:  # noqa: SLF001 — own-package access
        refreshed = sorted({_category_key(p) for p in cache._items})  # noqa: SLF001
    cache.invalidate()
    return {"ok": True, "refreshed": refreshed}


# ── /health/check + /health/run ───────────────────────────────────────────


def _build_health_snapshot(adapter: IAdapter) -> dict[str, Any]:
    """Compose the health-snapshot payload from ``adapter.health_check()``.

    Score is ``max(0, 100 - 20 * len(failed_components))``. A component
    is "good" when its status string starts with ``ok`` or
    ``degraded`` — both indicate a usable subsystem; ``error`` and
    ``unavailable`` are scored against the operator.
    """
    components = adapter.health_check()
    bad = [
        k
        for k, v in components.items()
        if not (str(v).startswith("ok") or str(v).startswith("degraded"))
    ]
    score = max(0, 100 - len(bad) * 20)
    return {
        "score": score,
        "status": "ok" if not bad else "degraded",
        "components": components,
        "failed": bad,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/check")
async def health_check_real(request: Request) -> dict[str, Any]:
    """Adapter health snapshot for the Overview tab."""
    adapter = _require_adapter(request)
    return _build_health_snapshot(adapter)


@router.post("/health/run")
async def health_run_real(request: Request) -> dict[str, Any]:
    """Re-run the adapter health check (same shape as GET)."""
    adapter = _require_adapter(request)
    snapshot = _build_health_snapshot(adapter)
    return {"ok": True, "snapshot": snapshot}


# ── /runs/active + stop + stream ──────────────────────────────────────────


def _find_latest_active(registry: Any) -> Any | None:
    """Return the most-recent in-flight :class:`RunState`, or None.

    ``RunRegistry.all_running()`` gives us UUIDs; we want the actual
    state object so we can read ``status`` / ``started_at``.
    """
    running_ids: list[UUID] = registry.all_running()
    if not running_ids:
        return None
    # ``all_running`` preserves registration order (OrderedDict iteration);
    # most-recent registration is the last entry.
    last_id = running_ids[-1]
    return registry.get(last_id)


@router.get("/runs/active")
async def runs_active_real(request: Request) -> dict[str, Any]:
    """Report the most-recently-registered active async run."""
    registry = getattr(request.app.state, "run_registry", None)
    if registry is None:
        return {"active": None}
    state = _find_latest_active(registry)
    if state is None:
        return {"active": None}
    status_val = state.status.value if hasattr(state.status, "value") else str(state.status)
    return {
        "active": {
            "run_id": str(state.run_id),
            "status": status_val,
            "started_at": state.started_at.isoformat() if state.started_at else None,
            "finished_at": state.finished_at.isoformat() if state.finished_at else None,
        },
    }


@router.post("/runs/active/stop")
async def runs_active_stop_real(request: Request) -> dict[str, Any]:
    """Best-effort cancel of the active run.

    The current :class:`RunRegistry` has no cancel primitive — the
    underlying worker thread runs to completion. We surface this gap
    rather than returning a misleading success.
    """
    registry = getattr(request.app.state, "run_registry", None)
    if registry is None:
        return {"ok": False, "reason": "no run registry"}
    state = _find_latest_active(registry)
    if state is None:
        return {"ok": False, "reason": "no active run"}
    return {
        "ok": False,
        "reason": "cancel not yet supported by RunRegistry",
        "run_id": str(state.run_id),
    }


def _sse_event(name: str, data: object) -> bytes:
    """Encode one SSE frame."""
    import json as _json

    payload = _json.dumps(data, default=str)
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")


@router.get("/runs/active/stream")
async def runs_active_stream_real(request: Request) -> StreamingResponse:
    """SSE stream for the active run.

    Emits ``status`` on connect, ``sidecar`` for each new sidecar JSON
    file detected on disk, and ``done`` when the worker terminates.
    When no run is active we emit a single ``status: idle`` event and
    close — preserving the behaviour the legacy SPA already depends on
    (``test_dashboard_spa.py::test_spa_stub_runs_active_stream_is_sse``
    asserts ``b"event: status"`` arrives).

    Implementation: filesystem polling. The orchestrator's
    :class:`RunRegistry` has no event bus and we do NOT want to grow one
    for this wave — polling the run dir every 500 ms is cheap and works
    identically to the existing ``/runs/{id}/events`` endpoint.
    """
    from ...orchestrator.run_async import RunStatus
    from ...orchestrator.sidecar_io import (
        SidecarReadError,
        list_run_sidecars,
        read_sidecar,
    )

    registry = getattr(request.app.state, "run_registry", None)

    async def event_gen():
        import asyncio

        if registry is None:
            yield _sse_event("status", {"status": "idle", "reason": "no registry"})
            return

        state = _find_latest_active(registry)
        if state is None:
            yield _sse_event("status", {"status": "idle"})
            return

        run_id = state.run_id
        run_dir = state.base_dir / str(run_id) if state.base_dir else None
        seen: set[str] = set()

        yield _sse_event(
            "status",
            {
                "status": state.status.value if hasattr(state.status, "value") else str(state.status),
                "run_id": str(run_id),
            },
        )

        while True:
            if run_dir is not None and run_dir.is_dir():
                for path in list_run_sidecars(run_dir):
                    if path.name in seen:
                        continue
                    seen.add(path.name)
                    try:
                        sc = read_sidecar(path)
                        yield _sse_event(
                            "sidecar",
                            sc.model_dump(mode="json", by_alias=True),
                        )
                    except SidecarReadError as exc:
                        yield _sse_event(
                            "sidecar_error",
                            {"path": str(path), "error": str(exc)},
                        )
            if state.status in (RunStatus.COMPLETED, RunStatus.FAILED):
                yield _sse_event(
                    "done",
                    {
                        "status": state.status.value,
                        "error": state.error,
                        "duration_ms": (
                            int((state.finished_at - state.started_at).total_seconds() * 1000)
                            if state.started_at and state.finished_at
                            else None
                        ),
                    },
                )
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


__all__ = [
    "InventoryCache",
    "_classify",
    "_version_gt",
    "router",
]

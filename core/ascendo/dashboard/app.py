"""FastAPI application factory.

Use :func:`create_app` to build the application. The factory accepts an
optional pre-built adapter + runs_dir override so tests can inject fakes
without touching environment variables.

The factory does NOT call ``uvicorn.run`` -- that's the caller's job
(``ascendo dashboard`` CLI command, or a production server like
``uvicorn ascendo.dashboard:create_app --factory``).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes.about import router as about_router
from .routes.ai import router as ai_router
from .routes.apps import router as apps_router
from .routes.elevation import router as elevation_router
from .routes.health import router as health_router
from .routes.onboarding import router as onboarding_router
from .routes.runs import router as runs_router
from .routes.service import router as service_router
from .routes.spa_real import InventoryCache
from .routes.spa_real import router as spa_real_router
from .routes.spa_stubs import router as spa_stubs_router
from .routes.suggestions import router as suggestions_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..interfaces.adapter import IAdapter

_log = logging.getLogger(__name__)


def _default_runs_dir() -> Path:
    """Per-user runs directory. Mirrors the CLI helper.

    Override via ``ASCENDO_RUNS_DIR`` env var; tests typically inject
    ``runs_dir=tmp_path`` directly into :func:`create_app`.
    """
    override = os.environ.get("ASCENDO_RUNS_DIR")
    return Path(override) if override else Path.home() / ".ascendo" / "runs"


def _resolve_frontend_dir() -> Path | None:
    """Locate the legacy SPA bundle at ``app/frontend`` relative to the repo.

    The dashboard package lives at ``core/ascendo/dashboard/`` and the
    frontend at ``app/frontend/`` -- four ``parents`` up gets us to repo
    root. ``ASCENDO_FRONTEND_DIR`` env var overrides.
    """
    override = os.environ.get("ASCENDO_FRONTEND_DIR")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None

    here = Path(__file__).resolve()
    # core/ascendo/dashboard/app.py -> repo root
    repo_root = here.parent.parent.parent.parent
    candidate = repo_root / "app" / "frontend"
    return candidate if candidate.is_dir() else None


def create_app(
    *,
    adapter: IAdapter | None = None,
    runs_dir: Path | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the dashboard FastAPI application.

    Args:
        adapter: Pre-resolved :class:`IAdapter` instance. If ``None``, the
            app calls :func:`select_adapter` lazily on first request via
            :class:`AdapterRegistry.discover`. Tests inject a fake adapter
            here to avoid the heavyweight discovery path.
        runs_dir: Where sidecars live. Defaults to ``~/.ascendo/runs`` or
            ``$ASCENDO_RUNS_DIR``.
        cors_origins: List of allowed origins for CORS. Default: ``[*]``
            (FastAPI default during dev). Production deployments should
            tighten this to ``["http://127.0.0.1:8765"]`` or similar.

    Returns:
        Configured :class:`fastapi.FastAPI` instance, ready for ``uvicorn``.
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Resolve adapter lazily if not pre-injected.
        if not hasattr(app.state, "adapter"):
            try:
                from ..adapter_factory import (
                    AdapterRegistry,
                    NoAdapterAvailableError,
                    select_adapter,
                )

                registry = AdapterRegistry()
                registry.discover()
                app.state.adapter = select_adapter(registry=registry)
                _log.info("dashboard: adapter resolved as %s", app.state.adapter.name)
            except NoAdapterAvailableError as exc:
                _log.warning("dashboard: no adapter -- endpoints will return 503: %s", exc)
                app.state.adapter = None

        yield
        # No teardown needed yet.

    app = FastAPI(
        title="Ascendo",
        description="Cross-platform update orchestrator -- local HTTP backend.",
        version=_get_version(),
        lifespan=lifespan,
    )

    # Pre-injection (tests) -- set state before lifespan runs.
    if adapter is not None:
        app.state.adapter = adapter
    app.state.runs_dir = runs_dir or _default_runs_dir()
    app.state.runs_dir.mkdir(parents=True, exist_ok=True)

    # In-memory async-run registry (M2.10). Lifetime = process.
    from ..orchestrator import RunRegistry
    app.state.run_registry = RunRegistry()

    # Per-app inventory cache (B1). Thread-safe, 60s TTL. Mutated via
    # POST /inventory/refresh.
    app.state.inventory_cache = InventoryCache()

    # CORS -- permissive default for local-only usage on 127.0.0.1.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # ── API routes (must come BEFORE the catch-all SPA static mount) ──────
    # Order matters: spa_stubs is registered FIRST so its specific routes
    # (e.g. /runs/active, /runs/active/stream,
    # /runs/{run_id}/phase/...) win over the runs_router's UUID-typed
    # /runs/{run_id} catch-pattern, which would otherwise reject
    # active as uuid_parsing.
    app.include_router(health_router, prefix="")
    # Real adapter-backed handlers (B1+B2+B3). MUST be mounted BEFORE
    # spa_stubs so they win on path collisions for /categories,
    # /inventory*, /health/check, /health/run, /runs/active*.
    app.include_router(spa_real_router, prefix="")
    # Persistent first-run wizard endpoints (graduated from spa_stubs).
    # MUST be mounted BEFORE spa_stubs so its real handlers win.
    app.include_router(onboarding_router, prefix="")
    # Apps tracking + per-app exclusion list (default-include model:
    # every detected app is in_config until the user opts out). MUST
    # be BEFORE spa_stubs (which still has an /apps/detect placeholder).
    app.include_router(apps_router, prefix="")
    # About + platform-aware release-notes (graduates the about_stub).
    # MUST be BEFORE spa_stubs.
    app.include_router(about_router, prefix="")
    # AI provider config + connection-test (powers the 3-step Suggestions
    # wizard). MUST be BEFORE spa_stubs (no overlap today, but future-
    # proof).
    app.include_router(ai_router, prefix="")
    # Preloaded suggestion library at /suggestions/library. MUST be
    # BEFORE spa_stubs so the real handler wins over the legacy
    # /suggestions stub returning ``[]``.
    app.include_router(suggestions_router, prefix="")
    # Legacy SPA stubs -- transient placeholders for endpoints the
    # Ubuntu_Aktualizacje SPA expects but the new core hasn't ported yet.
    # Delete the matching stub from routes/spa_stubs.py when each real
    # implementation lands.
    app.include_router(spa_stubs_router, prefix="")
    app.include_router(runs_router, prefix="/runs")
    # Windows service management (/service/*). Registered last among API
    # routers so it sits next to runs in the OpenAPI tag list. On non-
    # Windows hosts every endpoint returns the typed-empty "unsupported"
    # shape rather than 404 -- the SPA can branch on
    # ``status.supported``.
    app.include_router(service_router, prefix="")
    # Elevation password cache management (/elevation/*). Registered after
    # service -- endpoints exist in OpenAPI on all platforms; handlers
    # return 503 when the adapter has no IElevation.
    app.include_router(elevation_router)

    # ── SPA bundle -- serve at root, last so REST routes win ──────────────
    frontend_dir = _resolve_frontend_dir()
    if frontend_dir is not None:
        app.state.frontend_dir = frontend_dir

        @app.get("/", include_in_schema=False)
        async def _spa_index() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

        # Each top-level asset gets an explicit handler so the SPA's
        # absolute paths (``/style.css``, ``/app.js``...) resolve without
        # shadowing API routes. ``StaticFiles(html=True)`` mounted at "/"
        # would intercept *every* unmatched path including 404s from API
        # callers, which we don't want.
        _spa_assets: tuple[tuple[str, str], ...] = (
            ("app.js", "application/javascript"),
            ("style.css", "text/css"),
            # Design system tokens (colors, type, spacing, radii, shadows,
            # motion). Must load before style.css per index.html.
            ("colors_and_type.css", "text/css"),
            ("i18n.js", "application/javascript"),
            ("icons.js", "application/javascript"),
            ("favicon.svg", "image/svg+xml"),
        )
        for asset_name, media_type in _spa_assets:
            asset_path = frontend_dir / asset_name
            if not asset_path.exists():
                continue
            # Bind both ``asset_path`` and ``media_type`` per-iteration via
            # default arguments to dodge the late-binding closure trap.
            def _make_handler(
                _path: Path = asset_path,
                _media: str = media_type,
            ):
                async def _serve_asset() -> FileResponse:
                    return FileResponse(_path, media_type=_media)
                return _serve_asset

            app.add_api_route(
                f"/{asset_name}",
                _make_handler(),
                methods=["GET"],
                include_in_schema=False,
            )

        # Brand assets (logo wordmarks + marks). Lives at ``app/frontend/
        # assets/`` and is referenced from index.html via ``/assets/...``
        # absolute paths so the favicon link tag works.
        assets_dir = frontend_dir / "assets"
        if assets_dir.is_dir():
            @app.get("/assets/{filename}", include_in_schema=False)
            async def _spa_brand_asset(filename: str) -> FileResponse:
                from fastapi import HTTPException

                # Restrict to a known suffix set — defense in depth against
                # ``../`` traversal even though Path normalises.
                if "/" in filename or "\\" in filename or ".." in filename:
                    raise HTTPException(status_code=404)
                target = (assets_dir / filename).resolve()
                if not target.is_file() or assets_dir not in target.parents:
                    raise HTTPException(status_code=404)
                # Pick a sane Content-Type by suffix.
                suffix = target.suffix.lower()
                media = {
                    ".svg":  "image/svg+xml",
                    ".png":  "image/png",
                    ".jpg":  "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".ico":  "image/x-icon",
                }.get(suffix, "application/octet-stream")
                return FileResponse(target, media_type=media)

        # Also mount a /static/ subtree so any future relative imports
        # (``/static/foo.js``) work without further plumbing.
        app.mount(
            "/static",
            StaticFiles(directory=str(frontend_dir)),
            name="frontend-static",
        )
    else:
        _log.info("dashboard: SPA frontend not found -- serving API only")

    return app


def _get_version() -> str:
    from .. import __version__

    return __version__


__all__ = ["create_app"]

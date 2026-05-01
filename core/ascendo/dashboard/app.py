"""FastAPI application factory.

Use :func:`create_app` to build the application. The factory accepts an
optional pre-built adapter + runs_dir override so tests can inject fakes
without touching environment variables.

The factory does NOT call ``uvicorn.run`` — that's the caller's job
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

from .routes.health import router as health_router
from .routes.runs import router as runs_router

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
                _log.warning("dashboard: no adapter — endpoints will return 503: %s", exc)
                app.state.adapter = None

        yield
        # No teardown needed yet.

    app = FastAPI(
        title="Ascendo",
        description="Cross-platform update orchestrator — local HTTP backend.",
        version=_get_version(),
        lifespan=lifespan,
    )

    # Pre-injection (tests) — set state before lifespan runs.
    if adapter is not None:
        app.state.adapter = adapter
    app.state.runs_dir = runs_dir or _default_runs_dir()
    app.state.runs_dir.mkdir(parents=True, exist_ok=True)

    # In-memory async-run registry (M2.10). Lifetime = process.
    from ..orchestrator import RunRegistry
    app.state.run_registry = RunRegistry()

    # CORS — permissive default for local-only usage on 127.0.0.1.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # Routes
    app.include_router(health_router, prefix="")
    app.include_router(runs_router, prefix="/runs")

    return app


def _get_version() -> str:
    from .. import __version__

    return __version__


__all__ = ["create_app"]

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

from .inventory_db import InventoryDB
from .middleware.edition_gate import EditionGateMiddleware
from .routes.about import router as about_router
from .routes.ai import router as ai_router
from .routes.apps import router as apps_router
from .routes.chat import router as chat_router
from .routes.dedup import router as dedup_router
from .routes.elevation import router as elevation_router
from .routes.web_config import set_active_adapter as _set_web_active_adapter
from .routes.health import router as health_router
from .routes.onboarding import router as onboarding_router
from .routes.runs import router as runs_router
from .routes.scheduler_real import router as scheduler_real_router
from .routes.service import router as service_router
from .routes.web_config import router as web_config_router
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


def _default_inventory_db_path() -> Path:
    """Per-user inventory DB path. ``ASCENDO_INVENTORY_DB`` overrides."""
    override = os.environ.get("ASCENDO_INVENTORY_DB")
    return Path(override) if override else Path.home() / ".ascendo" / "inventory.db"


def _augment_path_for_macos_gui() -> None:
    """Prepend known-good binary dirs to PATH on macOS.

    Sesja 53 fix: when the dashboard is launched by the Tauri shell (or any
    macOS GUI process — Launchpad, Finder double-click, ``open -a``), it
    inherits ``launchctl``'s PATH which by default is just
    ``/usr/bin:/bin:/usr/sbin:/sbin``. Homebrew (``/opt/homebrew/bin``),
    nvm/asdf node installs (``~/.local/share/mac-update/node/bin``), Rust
    cargo (``~/.cargo/bin``), and bun (``~/.bun/bin``) are all missing,
    so subprocesses spawned by apply.sh fall back to the Xcode-shipped
    Python 3.9 (causing pip-installed packages to leak into
    ``~/Library/Python/3.9/bin/``) or fail outright (npm postinstall
    subshells calling ``bun`` / ``node``).

    We augment PATH idempotently — if an entry is already present (e.g.
    user launched the dashboard from Terminal with a full shell PATH),
    we don't double-prepend.
    """
    import sys
    if sys.platform != "darwin":
        return
    home = os.path.expanduser("~")
    # Order: brew first (so brew Python wins over Xcode shim), then
    # ascendo toolchain (node + bun), then ~/.local/bin (install.sh shims),
    # then cargo (Rust dev). Each line is a single dir.
    candidates = [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        f"{home}/.local/bin",
        f"{home}/.local/share/mac-update/node/bin",
        f"{home}/.local/share/mac-update/npm-global/bin",
        f"{home}/.bun/bin",
        f"{home}/.cargo/bin",
    ]
    existing = os.environ.get("PATH", "").split(":")
    new_entries = [p for p in candidates if os.path.isdir(p) and p not in existing]
    if new_entries:
        os.environ["PATH"] = ":".join(new_entries + existing)

    # Deliberately DO NOT auto-enable ASCENDO_SUDO_ALLOW_GUI here.
    #
    # A previous revision set ASCENDO_SUDO_ALLOW_GUI=1 whenever the
    # dashboard had no controlling TTY (which is *always* true for
    # `ascendo web start` / the Tauri sidecar / an Ascendo.app
    # double-click). Its comment claimed the SecurityAgent dialog
    # "honours PAM Touch ID" — that is factually wrong. `osascript … with
    # administrator privileges` goes through Authorization Services
    # (`system.privilege.admin`), which is a DIFFERENT auth rule from
    # `sudo`'s PAM stack. It does NOT consult `pam_tid.so`, so even with
    # Touch ID configured the operator got a password-only popup on every
    # apply — the exact regression this removal fixes.
    #
    # The correct Touch-ID-first path is plain `sudo` going through PAM
    # (`auth sufficient pam_tid.so` in /etc/pam.d/sudo_local). The macOS
    # biometric subsystem presents the Touch ID sheet itself — it needs
    # neither a controlling TTY nor an askpass helper — so the apply
    # scripts' `_ascendo_sudo_warm` handles the no-TTY dashboard case
    # natively. ASCENDO_SUDO_ALLOW_GUI stays strictly opt-in: an operator
    # may still `export ASCENDO_SUDO_ALLOW_GUI=1` for a genuinely headless
    # box with no Touch ID and no password modal, but we never force it.


def _resolve_edition() -> str:
    """Priority: ASCENDO_EDITION env > $ASCENDO_HOME/.ascendo-edition > basic.

    The marker file holds a single line with ``basic`` or ``dev``. Anything
    else (missing, unreadable, unknown value) falls back to ``basic``.
    """
    env_val = (os.environ.get("ASCENDO_EDITION") or "").strip().lower()
    if env_val in ("basic", "dev"):
        return env_val
    home = os.environ.get("ASCENDO_HOME") or os.path.expanduser("~/.local/share/ascendo")
    marker = Path(home) / ".ascendo-edition"
    try:
        text = marker.read_text(encoding="utf-8").strip().lower()
        if text in ("basic", "dev"):
            return text
    except (OSError, ValueError):
        pass
    return "basic"


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
    inventory_db_path: Path | None = None,
    host: str = "127.0.0.1",
    allow_remote: bool = False,
) -> FastAPI:
    """Build the dashboard FastAPI application.

    Args:
        adapter: Pre-resolved :class:`IAdapter` instance. If ``None``, the
            app calls :func:`select_adapter` lazily on first request via
            :class:`AdapterRegistry.discover`. Tests inject a fake adapter
            here to avoid the heavyweight discovery path.
        runs_dir: Where sidecars live. Defaults to ``~/.ascendo/runs`` or
            ``$ASCENDO_RUNS_DIR``.
        cors_origins: List of allowed origins for CORS. Default (P5
            hardening): a loopback-only allowlist (``_DEFAULT_CORS_ORIGINS``
            below — 127.0.0.1/localhost on :8765, the Vite :1420 dev port,
            and the ``tauri://`` schemes), NEVER ``["*"]`` — so a malicious
            web page cannot drive the privileged endpoints even when the
            user binds ``--host 0.0.0.0``.

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
        # A5 decoupling: register the active adapter so the web-config routes
        # resolve the registry through adapter.web_registry() instead of
        # importing an adapter package in core (ADR-0005).
        _set_web_active_adapter(getattr(app.state, "adapter", None))

        yield
        # Teardown: drop the active-adapter registration so a subsequent app
        # in the same process (tests) can't inherit a stale provider.
        _set_web_active_adapter(None)
        # Close the inventory DB (no-op today; future-proofing).
        db = getattr(app.state, "inventory_db", None)
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001 — teardown must never raise
                _log.exception("inventory_db: close failed")

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

    # Sesja 53: augment PATH on macOS GUI launches so apply.sh subprocesses
    # find brew Python, node, bun, and the ascendo toolchain instead of
    # falling back to Xcode's framework Python 3.9. No-op on Linux/Windows
    # and on macOS shells where PATH is already complete.
    _augment_path_for_macos_gui()

    # Edition flag (basic | dev). Resolved here so app.state.edition is set
    # before any request reaches a route or middleware. Default = basic;
    # see _resolve_edition() for resolution priority.
    app.state.edition = _resolve_edition()

    # In-memory async-run registry (M2.10). Lifetime = process.
    from ..orchestrator import RunRegistry
    app.state.run_registry = RunRegistry()

    # Per-app inventory cache (B1). Thread-safe, 60s TTL. Mutated via
    # POST /inventory/refresh.
    app.state.inventory_cache = InventoryCache()

    # Persistent inventory DB (Sesja 32 — Apps/Categories parity). Stored
    # at ``~/.ascendo/inventory.db`` by default; tests override the path
    # via the ``inventory_db_path`` keyword. The DB is the single source
    # of truth for both the Apps tab AND the Categories tab so they
    # never disagree again.
    db_path = inventory_db_path or _default_inventory_db_path()
    try:
        app.state.inventory_db = InventoryDB(db_path)
    except Exception:  # noqa: BLE001
        _log.exception("inventory_db: initial open failed at %s", db_path)
        app.state.inventory_db = None

    # P5: CORS -- locked to localhost by default. Production Tauri app
    # uses tauri:// scheme; dev Vite uses localhost:1420.
    # NEVER default to ["*"] — that exposes privileged endpoints to
    # any web page when the user runs `--host 0.0.0.0`.
    _DEFAULT_CORS_ORIGINS: list[str] = [
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ]
    _effective_cors = (
        cors_origins if cors_origins is not None else _DEFAULT_CORS_ORIGINS
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_effective_cors,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # ── LAN safety (audit P2/§7) ─────────────────────────────────────────
    # Loopback-only by default. A non-loopback bind exposes privileged
    # mutating endpoints to the network, so it must be an explicit opt-in;
    # combined with wildcard CORS it is outright dangerous. Refuse unless the
    # operator opted in via allow_remote / ASCENDO_ALLOW_REMOTE=1 (defence in
    # depth — the CLI already gates this, but create_app can be called
    # programmatically). When opted in, gate mutating requests from
    # non-loopback peers behind a per-process capability token.
    _loopback = {"127.0.0.1", "localhost", "::1"}
    _allow_remote = allow_remote or os.environ.get("ASCENDO_ALLOW_REMOTE") == "1"
    _is_remote_bind = host not in _loopback
    _cors_wildcard = "*" in _effective_cors
    if _is_remote_bind and not _allow_remote:
        raise RuntimeError(
            f"Refusing to bind the dashboard to non-loopback host {host!r} "
            "without allow_remote=True / ASCENDO_ALLOW_REMOTE=1. The dashboard "
            "exposes privileged update operations; binding to the network "
            "without an explicit opt-in would let any host execute updates."
        )
    if _is_remote_bind and _allow_remote:
        _log.warning(
            "dashboard bound to non-loopback host %r — LAN-exposed. Mutating "
            "endpoints now require an X-Ascendo-Token header from remote peers.",
            host,
        )
        if _cors_wildcard:
            _log.warning(
                "DANGER: wildcard CORS ('*') + non-loopback bind exposes "
                "privileged endpoints to ANY web page. Set a loopback CORS "
                "allowlist instead."
            )
        import secrets

        from .middleware.lan_guard import LanGuardMiddleware

        token = os.environ.get("ASCENDO_DASHBOARD_TOKEN")
        if not token:
            token = secrets.token_urlsafe(32)
            _log.warning(
                "Generated a dashboard capability token (set "
                "ASCENDO_DASHBOARD_TOKEN to pin it). Remote clients must send "
                "X-Ascendo-Token: %s",
                token,
            )
        app.state.capability_token = token
        app.add_middleware(LanGuardMiddleware, token=token)

    # Edition gate: 404s dev-only routes when running in basic edition.
    # Reads app.state.edition (set above).
    app.add_middleware(EditionGateMiddleware)

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
    # AI Tools chat endpoints (Sesja 70 / Phase B). Mounts the eight
    # /ai/chat/* routes that drive the new sidebar tab. MUST be BEFORE
    # spa_stubs in case the legacy SPA ever registered a placeholder there.
    app.include_router(chat_router, prefix="")
    # Preloaded suggestion library at /suggestions/library. MUST be
    # BEFORE spa_stubs so the real handler wins over the legacy
    # /suggestions stub returning ``[]``.
    app.include_router(suggestions_router, prefix="")
    # Schedule tab backend (Sesja 67). Wraps the adapter's IScheduler
    # implementation; replaces the /scheduler/install + /scheduler/remove
    # stubs that returned ``{ok:true, stub:true}``. MUST be BEFORE
    # spa_stubs so the real handlers win.
    app.include_router(scheduler_real_router, prefix="")
    # Web-config surface (Phase A/C): POST /web/open (post-run app
    # launch) + POST /web/probe-entry (read-only candidate-entry
    # dry-run for the AI-config loop). Before spa_stubs so real wins.
    app.include_router(web_config_router, prefix="")
    # Legacy SPA stubs -- transient placeholders for endpoints the
    # Ascendo SPA expects but the new core hasn't ported yet.
    # Delete the matching stub from routes/spa_stubs.py when each real
    # implementation lands.
    app.include_router(spa_stubs_router, prefix="")
    app.include_router(runs_router, prefix="/runs")
    # Cross-source deduplication consent surface (/dedup/*). GET /dedup/pending
    # reports recommended duplicate fixes read-only; POST /dedup/apply records
    # explicit consent and triggers the uninstall apply. Registered before the
    # SPA bundle so the REST routes win.
    app.include_router(dedup_router, prefix="")
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
            # No-cache the HTML too so a fresh git pull is picked up even
            # if the browser already has the old icons.js/app.js bytes
            # cached. The asset routes below carry the same header.
            return FileResponse(
                frontend_dir / "index.html",
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

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
            # Per-locale i18n split (Sesja 78): data in i18n.{en,pl}.js,
            # loader/helpers in i18n.js. All three served + defer-ordered.
            ("i18n.en.js", "application/javascript"),
            ("i18n.pl.js", "application/javascript"),
            ("i18n.js", "application/javascript"),
            ("icons.js", "application/javascript"),
            # AppShell IA refactor (Sesja 73): platform abstraction loads
            # before app.js; shell wires the 5-destination nav after it.
            ("platform.js", "application/javascript"),
            ("shell.js", "application/javascript"),
            # Touch-first UI kit (Sesja 74): progressive-enhancement layer
            # loaded after shell.js (no-dropdown controls, mobile bottom
            # nav, run stepper, responsive history cards).
            ("ui-components.js", "application/javascript"),
            # Cross-source deduplication consent card (Sesja 86): self-
            # contained, additive; polls /dedup/pending and POSTs
            # /dedup/apply on explicit "Resolve" click.
            ("dedup.js", "application/javascript"),
            # UI redesign overlay (Sesja 75): progressive design-system
            # layer loaded LAST so it wins by source order. CSS-only,
            # zero DOM/JS — reversible by removing this entry + the
            # <link> in index.html.
            ("ui-redesign.css", "text/css"),
            # ui-redesign.js REMOVED (Sesja 76 / M1): its runtime node
            # reorg + shadow router was superseded by the M2 shell.js
            # which owns the router. File deleted; not served.
            # M4: component foundation. components.css is .asc-* only
            # (tokens, no !important); components.js defines window.AC.
            # Served so the Dashboard rebuild can mount AC primitives.
            ("components.css", "text/css"),
            ("components.js", "application/javascript"),
            # P0: layout-editor assets removed from the served whitelist.
            # The drag-reorder "Edit layout" power tool + always-visible
            # drag handles were leaking into the default end-user surface.
            # Re-enable by restoring these two entries + the <link>/<script>
            # in index.html.
            ("favicon.svg", "image/svg+xml"),
        )
        # Cache policy: ``no-cache`` forces the browser to revalidate via
        # If-None-Match / If-Modified-Since on every load (FastAPI's
        # FileResponse already emits ETag + Last-Modified). After a
        # ``git pull`` the file mtime changes → 200 + fresh body; otherwise
        # 304 + cached body. Belt-and-suspenders against stale icons.js /
        # app.js bites users see across deploys.
        _SPA_ASSET_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}

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
                    return FileResponse(
                        _path,
                        media_type=_media,
                        headers=_SPA_ASSET_HEADERS,
                    )
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

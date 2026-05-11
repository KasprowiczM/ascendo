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
from pathlib import Path
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


# Statuses we MUST preserve from the sidecar — _classify() doesn't know about
# the Tier-B web flow (triggered = vendor agent will self-update on relaunch)
# or the per-package skip flow. Overwriting these would make the SPA show
# Brave/Chrome (keystone) as "outdated" forever, since installed != candidate
# is the expected steady state.
_SIDECAR_PRESERVED_STATUSES = frozenset({
    "triggered", "triggered_pending", "triggered_confirmed",
    "skipped", "skipped_running", "skipped_unavailable", "probe_unavailable",
    "failed", "partial", "planned", "deferred",
})


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


def _get_inventory_db(request: Request):  # type: ignore[no-untyped-def]
    """Return the per-app InventoryDB or ``None`` if disabled.

    Late-imported to avoid a routes-import cycle and to keep the test
    seam (tests can inject ``inventory_db_path`` into ``create_app``).
    """
    return getattr(request.app.state, "inventory_db", None)


def _adapter_id(adapter: IAdapter) -> str:
    """Stable adapter identifier used as the ``inventory_meta`` key."""
    return getattr(adapter, "name", None) or adapter.__class__.__name__


def _category_key(pkg: Package) -> str:
    """Stable string key for a Package's source category."""
    cat = pkg.category
    return cat.value if hasattr(cat, "value") else str(cat)


def _package_to_item(pkg: Package) -> dict[str, Any]:
    """Render a :class:`Package` as the SPA's inventory row schema.

    Reads ``installed_version`` + ``available_version`` from the Package
    model (added when inventory enumerators grew per-app version
    awareness — system_profiler on macOS, dpkg on Linux, ARP on Windows).
    Older callers that built Packages without these fields surface as
    ``installed=None`` / ``candidate=None``; the SPA renders the row
    without a phantom upgrade arrow.
    """
    cat_key = _category_key(pkg)
    installed = pkg.installed_version
    candidate = pkg.available_version
    return {
        "name": pkg.name,
        "installed": installed,
        "candidate": candidate,
        "source": cat_key,
        "status": _classify(installed, candidate),
        "vendor": pkg.vendor,
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


# ── Check-sidecar overlay ─────────────────────────────────────────────────
#
# Inventory by itself only knows what's installed; it doesn't know what's
# upgradable. The check phase produces a sidecar at
# ``<runs_dir>/<run-id>/<source>/check__<source>.json`` with items[]
# carrying installed + candidate + status.
#
# When the SPA asks ``GET /inventory/{cat}``, we look for the most-recent
# such sidecar on disk and overlay its values onto the inventory items.
# Result: as soon as the user clicks "check" once, every visit to
# Categories shows real installed/candidate/status pairs without re-running
# winget.

import json as _json  # local alias so the new helper is self-contained


def _items_from_check_sidecar(runs_dir: Path, category: str) -> list[dict[str, Any]]:
    """Synthesise inventory items directly from the latest check sidecar.

    Used when the OS-level inventory enumerator (system_profiler on macOS,
    dpkg on Linux, ARP on Windows) doesn't know about a category's
    packages — e.g. npm globals aren't ``.app`` bundles, so
    system_profiler returns nothing for category=npm even though the
    npm/check.sh sidecar has 9 real items.

    Mirrors the shape produced by :func:`_package_to_item` so downstream
    enrichment treats sidecar-derived items identically to inventory ones.
    """
    overlay = _latest_check_overlay(runs_dir, category)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, rec in overlay.items():
        # The overlay is keyed by both `name` and `id` — same record
        # appears under both keys when they differ. Dedupe by record id.
        rid = id(rec)
        marker = f"{rid}"
        if marker in seen:
            continue
        seen.add(marker)
        sidecar_status = rec.get("status")
        if sidecar_status in _SIDECAR_PRESERVED_STATUSES:
            row_status = sidecar_status
        else:
            row_status = _classify(rec.get("installed"), rec.get("candidate"))
        items.append(
            {
                "name": str(key),
                "installed": rec.get("installed"),
                "candidate": rec.get("candidate"),
                "source": category,
                "status": row_status,
                "vendor": None,
            }
        )
    return items


def _adapter_category_ids(adapter: IAdapter) -> list[str]:
    """Every package-manager category the adapter declares.

    Used to seed empty buckets from check sidecars so categories with no
    inventory representation (npm, pip, snap on systems where the OS
    doesn't enumerate them) still surface in the dashboard.
    """
    try:
        host = adapter.detect_host()
        managers = adapter.package_managers(host)
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for m in managers:
        cat = m.category
        out.append(cat.value if hasattr(cat, "value") else str(cat))
    return out


def _seed_buckets_from_sidecars(
    buckets: dict[str, list[dict[str, Any]]],
    adapter: IAdapter,
    runs_dir: Path | None,
) -> dict[str, list[dict[str, Any]]]:
    """Replace each bucket with the check sidecar's item list when the
    sidecar has strictly more entries than inventory found for that
    category.

    Why: the OS-level inventory (system_profiler on macOS, dpkg on
    Linux, ARP on Windows) only sees apps that match the
    classification heuristic. brew formulae, softwareupdate OS
    patches, and npm globals are NOT ``.app`` bundles and never get
    classified — yet their managers' check sidecars report the
    authoritative installed list. We replace the bucket when the
    sidecar carries more rows so Categories shows the same content
    Run Center does.

    The "strictly more" guard preserves Windows behaviour (winget /
    msstore / registry_arp inventory items typically match sidecar
    item count exactly, so inventory keeps its richer metadata; the
    overlay applied later by ``_enrich_items`` still imports
    installed/candidate versions from the sidecar).
    """
    if runs_dir is None:
        return buckets
    for cat_id in _adapter_category_ids(adapter):
        items = _items_from_check_sidecar(runs_dir, cat_id)
        if not items:
            continue
        existing = buckets.get(cat_id) or []
        if len(items) > len(existing):
            buckets[cat_id] = items
    return buckets


def _latest_check_overlay(runs_dir: Path, category: str) -> dict[str, dict[str, Any]]:
    """Return ``{name|id: {installed, candidate, status}}`` from the latest
    sidecar for ``category``, or ``{}`` if none exists.

    Sesja 53 fix: this function used to read **only** check sidecars. After
    an apply succeeded the verify sidecar held the post-apply truth (e.g.
    opencode-cli upgraded 1.14.43 → 1.14.44, verify says installed=1.14.44),
    but the SPA still showed "outdated" because /inventory/db/refresh fed
    the DB the stale check sidecar's data. Now we walk runs newest-first and
    pick the FRESHEST sidecar regardless of phase, with a tie-break that
    prefers verify > apply > check (in case multiple sidecars in the same
    run have identical mtimes within filesystem resolution).

    Bounded scan (latest 50 runs) so a long-lived install with hundreds
    of historical runs doesn't pay an O(N) glob on every request.

    Sidecar layout (the orchestrator writes flat by default; some legacy
    paths write nested per-source dirs — we accept both):

      flat:   <runs_dir>/<run-id>/<phase>__<category>.json
      nested: <runs_dir>/<run-id>/<category>/<phase>__<category>.json

    Where <phase> is one of: verify, apply, check.
    """
    if not isinstance(runs_dir, Path):
        runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return {}
    candidates: list[tuple[float, int, Path]] = []
    # Phase priority: verify (post-apply truth) > apply (mid-run truth) >
    # check (pre-apply snapshot). Higher priority wins on mtime ties.
    phase_priority = {"verify": 3, "apply": 2, "check": 1}
    try:
        run_dirs = sorted(
            (p for p in runs_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:50]
    except OSError:
        return {}
    for run_dir in run_dirs:
        # Walk all 3 phases per run, both layouts. The candidates list
        # is sorted at the end by (mtime DESC, priority DESC) so the
        # post-apply verify sidecar wins over the same-run check.
        for phase in ("verify", "apply", "check"):
            for sidecar in (
                run_dir / f"{phase}__{category}.json",
                run_dir / category / f"{phase}__{category}.json",
            ):
                if sidecar.is_file():
                    try:
                        candidates.append(
                            (sidecar.stat().st_mtime, phase_priority[phase], sidecar)
                        )
                    except OSError:
                        continue
    if not candidates:
        return {}
    candidates.sort(reverse=True)  # mtime DESC, then priority DESC
    latest = candidates[0][2]
    try:
        data = _json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return {}
    items = data.get("items") or []
    overlay: dict[str, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or it.get("id")
        if not name:
            continue
        installed = (it.get("installed")
                     or it.get("current_version")
                     or it.get("currentVersion"))
        candidate = (it.get("candidate")
                     or it.get("target_version")
                     or it.get("targetVersion"))
        # Surface "Unknown" verbatim instead of dropping to None — the user
        # would rather see "Unknown" (with knowledge that winget couldn't
        # detect) than a blank cell. Empty strings still drop to None
        # because they carry no information.
        if isinstance(installed, str) and installed.strip() == "":
            installed = None
        if isinstance(candidate, str) and candidate.strip() == "":
            candidate = None
        record: dict[str, Any] = {
            "installed": installed,
            "candidate": candidate,
            "status": it.get("status"),
        }
        # Index by both name AND id so the inventory lookup hits whichever
        # field the inventory item carries (display name vs winget Id).
        overlay[str(name)] = record
        item_id = it.get("id")
        if item_id and str(item_id) != str(name):
            overlay[str(item_id)] = record
        # Also index by the trailing segment of any colon-prefixed ID.
        # Legacy Ubuntu bash scripts emit synthetic IDs like
        #   snap:upgrade:firefox    snap:configured:thunderbird
        #   apt:upgrade:libpng16   brew:formula:openssl   pip:numpy
        # but the inventory enumerator emits clean names (firefox,
        # thunderbird, libpng16, openssl, numpy). Without this extra
        # key the overlay never matches and the SPA shows a stale
        # "ok" status with no candidate version after a check phase.
        for src in (name, item_id):
            if not src:
                continue
            s = str(src)
            if ":" in s:
                tail = s.rsplit(":", 1)[-1]
                if tail and tail not in overlay:
                    overlay[tail] = record
    return overlay


def _enrich_items(items: list[dict[str, Any]], category: str, runs_dir: Path | None,
                  excluded: set[str]) -> list[dict[str, Any]]:
    """Apply check-sidecar overlay + in_config flag to a bucketed item list.

    ``excluded`` is the set of ``"category:name"`` strings from
    :mod:`apps.excluded_keys()`. ``in_config`` is True for any item NOT in
    that set (default-include model — a fresh user has every app in
    config until they opt-out).
    """
    overlay: dict[str, dict[str, Any]] = {}
    if runs_dir is not None:
        overlay = _latest_check_overlay(runs_dir, category)
    enriched: list[dict[str, Any]] = []
    for it in items:
        out = dict(it)
        name = out.get("name", "")
        # The overlay is keyed by both name and id, so try name first
        # (inventory's `name` is usually a display name) then any id-shaped
        # field the inventory item happens to carry.
        hit = overlay.get(name) or overlay.get(out.get("id", "")) or overlay.get(out.get("vendor", ""))
        if hit:
            if hit.get("installed") is not None:
                out["installed"] = hit["installed"]
            if hit.get("candidate") is not None:
                out["candidate"] = hit["candidate"]
            if hit.get("status"):
                out["status"] = hit["status"]
        # Re-classify in case the overlay filled in versions after the
        # initial "ok" classification — but NEVER clobber Tier-B sidecar
        # statuses like "triggered" (keystone/squirrel/builtin self-update
        # asynchronously; installed != candidate is the steady state).
        current = out.get("status")
        if current not in _SIDECAR_PRESERVED_STATUSES and (out.get("installed") or out.get("candidate")):
            out["status"] = _classify(out.get("installed"), out.get("candidate"))
        out["in_config"] = f"{category}:{name}" not in excluded
        enriched.append(out)
    return enriched


# ── DB-backed inventory loader ────────────────────────────────────────────


def _build_buckets_live(
    adapter: IAdapter,
    cache: InventoryCache,
    runs_dir: Path | None,
) -> dict[str, list[dict[str, Any]]]:
    """Compute the buckets via the live scan + check-sidecar overlay.

    This is the legacy code path used as the "DB miss" fallback. It is
    deliberately kept identical to the pre-DB behaviour so anything the
    DB doesn't yet know about still surfaces in the SPA.
    """
    packages = cache.get(lambda: _load_packages(adapter))
    buckets = _bucket(packages)
    return _seed_buckets_from_sidecars(buckets, adapter, runs_dir)


def _flatten_buckets_for_db(
    buckets: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Flatten the bucketed dicts into ``inventory_db.bulk_upsert`` rows."""
    rows: list[dict[str, Any]] = []
    for cat, items in buckets.items():
        for it in items:
            name = it.get("name")
            if not name:
                continue
            rows.append(
                {
                    "category": cat,
                    "name": name,
                    "installed": it.get("installed"),
                    "candidate": it.get("candidate"),
                    "status": it.get("status") or "unknown",
                    "source_type": it.get("source") or cat,
                    "vendor": it.get("vendor"),
                    "metadata": None,
                },
            )
    return rows


def _replace_buckets_in_db(
    db,  # type: ignore[no-untyped-def]
    buckets: dict[str, list[dict[str, Any]]],
    rows: list[dict[str, Any]],
) -> int:
    """Atomically replace each scanned category's rows in the DB.

    The fresh ``buckets`` came from an authoritative live-scan, so any
    rows already in the DB for the same categories are stale and must
    go. Without this step, a manager whose tracked-set shrunk (e.g.
    pip dropped a package since the last scan) would leave orphan rows
    behind — causing the Apps tab to report ``pip 12`` while Run Center
    reports ``11``. See ``PLAN.md`` "Hygiene follow-ups" (Sesja 34).

    We only clear categories the live scan actually returned — never
    blow away buckets we have no authoritative data for. Returns the
    number of rows ``bulk_upsert`` wrote.
    """
    for cat in buckets.keys():
        try:
            db.clear_category(cat)
        except Exception:  # noqa: BLE001
            _log.exception("inventory_db: clear_category(%s) failed", cat)
    return db.bulk_upsert(rows) if rows else 0


def _buckets_from_db(db) -> dict[str, list[dict[str, Any]]]:  # type: ignore[no-untyped-def]
    """Read every row from the DB and re-shape into ``{cat: [item, ...]}``."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in db.query():
        cat = row["category"]
        out.setdefault(cat, []).append(
            {
                "name": row["name"],
                "installed": row.get("installed"),
                "candidate": row.get("candidate"),
                "source": row.get("source_type") or cat,
                "status": row.get("status") or "ok",
                "vendor": row.get("vendor"),
            },
        )
    return out


def _resolve_buckets(
    request: Request,
    adapter: IAdapter,
) -> dict[str, list[dict[str, Any]]]:
    """DB-first inventory resolver.

    - When the DB has fresh data (set_meta was called within the freshness
      window), serve from the DB without touching the adapter.
    - Otherwise, run the live scan + check-sidecar overlay and populate
      the DB as a side-effect so the next call is instant.
    """
    from ..inventory_db import is_fresh  # late import — avoid cycle

    db = _get_inventory_db(request)
    cache = _get_inventory_cache(request)
    runs_dir = getattr(request.app.state, "runs_dir", None)
    adapter_key = _adapter_id(adapter)

    if db is not None:
        try:
            meta = db.get_meta(adapter_key)
            if is_fresh(meta) and db.count() > 0:
                return _buckets_from_db(db)
        except Exception:  # noqa: BLE001
            _log.exception("inventory_db: read-side failure; falling back to live scan")

    buckets = _build_buckets_live(adapter, cache, runs_dir)

    # Best-effort populate. Failure here must never block the request —
    # the SPA still gets the live buckets.
    if db is not None:
        try:
            rows = _flatten_buckets_for_db(buckets)
            if rows:
                _replace_buckets_in_db(db, buckets, rows)
                db.set_meta(adapter_key, item_count=len(rows))
        except Exception:  # noqa: BLE001
            _log.exception("inventory_db: populate failed (non-fatal)")

    return buckets


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
    ``{name, installed, candidate, source, status, vendor, in_config}``
    per the SPA contract — enriched per-category with the latest check
    sidecar overlay + exclusion-list ``in_config`` flag.
    """
    from . import apps as apps_mod

    adapter = _require_adapter(request)
    runs_dir = getattr(request.app.state, "runs_dir", None)
    excluded = apps_mod.excluded_keys()
    buckets = _resolve_buckets(request, adapter)
    enriched = {
        cat: _enrich_items(items, cat, runs_dir, excluded)
        for cat, items in buckets.items()
    }
    return {"categories": enriched}


@router.get("/inventory/summary")
async def inventory_summary_real(request: Request) -> dict[str, Any]:
    """Donut/bars totals derived from cached inventory.

    Counts use enriched (check-sidecar-overlaid) items so the SPA's
    "Available updates" panel reflects the latest check phase results
    instead of the always-zero raw inventory.
    """
    from . import apps as apps_mod

    adapter = _require_adapter(request)
    runs_dir = getattr(request.app.state, "runs_dir", None)
    excluded = apps_mod.excluded_keys()
    buckets = _resolve_buckets(request, adapter)

    out = {
        "totals": {"ok": 0, "outdated": 0, "missing": 0, "total": 0},
        "categories": {},
    }
    for cat_id, items in buckets.items():
        enriched = _enrich_items(items, cat_id, runs_dir, excluded)
        ok = sum(1 for i in enriched if i.get("status") == "ok")
        outdated = sum(1 for i in enriched if i.get("status") == "outdated")
        missing = sum(1 for i in enriched if i.get("status") == "missing")
        total = len(enriched)
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
    """Single-category projection of /inventory.

    Items are enriched with the latest check-sidecar overlay (so
    ``installed`` and ``candidate`` are populated as soon as the user
    runs check once) and an ``in_config`` flag computed from the
    persistent exclusion list (default = True; user opts out per app).
    """
    from . import apps as apps_mod  # avoid import cycle at module load

    adapter = _require_adapter(request)
    runs_dir = getattr(request.app.state, "runs_dir", None)
    buckets = _resolve_buckets(request, adapter)
    items = buckets.get(category, [])
    enriched = _enrich_items(items, category, runs_dir, apps_mod.excluded_keys())
    return {"category": category, "items": enriched}


@router.post("/inventory/refresh")
async def inventory_refresh_real(request: Request) -> dict[str, Any]:
    """Invalidate the in-memory cache + bulk-refresh the DB.

    The DB is the source of truth for the SPA, so a "refresh" must:
    1. drop the in-memory ``InventoryCache`` so the next live scan fires,
    2. re-run the live scan + sidecar overlay,
    3. bulk-upsert into the persistent DB,
    4. update the freshness marker.
    """
    cache = _get_inventory_cache(request)
    refreshed: list[str] = []
    if cache._items is not None:  # noqa: SLF001 — own-package access
        refreshed = sorted({_category_key(p) for p in cache._items})  # noqa: SLF001
    cache.invalidate()

    adapter = getattr(request.app.state, "adapter", None)
    db = _get_inventory_db(request)
    item_count = 0
    if adapter is not None and db is not None:
        runs_dir = getattr(request.app.state, "runs_dir", None)
        try:
            buckets = _build_buckets_live(adapter, cache, runs_dir)
            rows = _flatten_buckets_for_db(buckets)
            item_count = _replace_buckets_in_db(db, buckets, rows)
            db.set_meta(_adapter_id(adapter), item_count=item_count)
            if not refreshed:
                refreshed = sorted(buckets.keys())
        except Exception:  # noqa: BLE001
            _log.exception("inventory_db: /inventory/refresh DB sync failed")

    return {"ok": True, "refreshed": refreshed, "item_count": item_count}


@router.post("/inventory/db/refresh")
async def inventory_db_refresh(request: Request) -> dict[str, Any]:
    """Force a full inventory rescan + DB repopulation.

    Distinct from ``/inventory/refresh`` only in that this endpoint
    surfaces ``refreshed_at`` + ``item_count`` for the SPA's "Reset
    cache" button. It always touches the DB even when the in-memory
    cache was empty.
    """
    from ..inventory_db import _utcnow  # late import — avoid cycle

    adapter = _require_adapter(request)
    db = _get_inventory_db(request)
    cache = _get_inventory_cache(request)
    cache.invalidate()
    runs_dir = getattr(request.app.state, "runs_dir", None)
    buckets = _build_buckets_live(adapter, cache, runs_dir)
    rows = _flatten_buckets_for_db(buckets)
    item_count = 0
    if db is not None:
        try:
            item_count = _replace_buckets_in_db(db, buckets, rows)
            db.set_meta(_adapter_id(adapter), item_count=item_count)
        except Exception:  # noqa: BLE001
            _log.exception("inventory_db: /inventory/db/refresh failed")
    return {
        "ok": True,
        "refreshed_at": _utcnow(),
        "item_count": item_count,
        "categories": sorted(buckets.keys()),
    }


# ── /health/check + /health/run ───────────────────────────────────────────


def _build_health_snapshot(adapter: IAdapter) -> dict[str, Any]:
    """Compose the health-snapshot payload from ``adapter.health_check()``.

    Score is ``max(0, 100 - 20 * len(failed_components))``. A component
    is "good" when its status string starts with ``ok`` or
    ``degraded`` — both indicate a usable subsystem; ``error`` and
    ``unavailable`` are scored against the operator.

    Output shape (matches what ``app/frontend/app.js::ui.loadHealth``
    expects — ``available: True`` flag tells the SPA the snapshot is
    populated, and ``issues[]`` is the per-component failure list with
    the severity/msg shape the SPA renders as a bulleted ``<ul>``):

        {
          "available": True,
          "score": 0..100,
          "status": "ok" | "degraded",
          "components": {<name>: <status-string>, ...},
          "failed": [<name>, ...],          # list of bad component keys
          "issues": [{"severity": "warn|err", "msg": "<comp>: <status>"}],
          "checked_at": "<iso8601>"
        }
    """
    components = adapter.health_check()
    bad = [
        k
        for k, v in components.items()
        if not (str(v).startswith("ok") or str(v).startswith("degraded"))
    ]
    score = max(0, 100 - len(bad) * 20)

    # Render the SPA-friendly issues list. Each component that's NOT
    # plainly "ok" gets a row; "degraded" → warn, anything else → err.
    # Operators see exactly which subsystem is misbehaving.
    issues: list[dict[str, str]] = []
    for name, status_str in components.items():
        s = str(status_str)
        if s.startswith("ok"):
            continue
        severity = "warn" if s.startswith("degraded") else "err"
        issues.append({"severity": severity, "msg": f"{name}: {s}"})

    return {
        "available": True,
        "score": score,
        "status": "ok" if not bad else "degraded",
        "components": components,
        "failed": bad,
        "issues": issues,
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

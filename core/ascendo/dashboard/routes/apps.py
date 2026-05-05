"""Persistent apps tracking + per-app exclusion list.

User mental model (per the resident operator's spec, 2026-05-03):

> "by default all apps should be added [to config], then if i don't want
> to update one of the apps, i either skip it in apps or remove it
> from config in categories"

So config = "the set of apps Ascendo will update during a run". By
default every detected app is in config. The user opts an app OUT of
updates by adding it to the exclusion list (per "category:name" key).

Endpoints:
    GET  /apps/detect           — list every app with in_config=true unless excluded.
    POST /apps/exclude          — add a {category, name} pair to the exclusion list.
    POST /apps/include          — remove a {category, name} pair from the exclusion list.
    GET  /apps/excluded         — read the raw exclusion list.

Exclusion store: ``%APPDATA%\\Ascendo\\excluded.json`` on Windows,
``~/.ascendo/excluded.json`` elsewhere. JSON: ``{"excluded": [{"category", "name"}, ...]}``.
``$ASCENDO_EXCLUDED_FILE`` overrides the path (tests inject this).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger(__name__)
router = APIRouter(tags=["apps"])


# ── State file resolver (mirrors onboarding.py pattern) ──────────────────


def excluded_file() -> Path:
    """Path to the JSON file holding the per-app exclusion list.

    Order of precedence: $ASCENDO_EXCLUDED_FILE > %APPDATA%\\Ascendo\\
    excluded.json (Windows) > ~/.ascendo/excluded.json (other).
    """
    override = os.environ.get("ASCENDO_EXCLUDED_FILE")
    if override:
        return Path(override)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Ascendo" / "excluded.json"
    return Path.home() / ".ascendo" / "excluded.json"


def _read_excluded() -> list[dict[str, str]]:
    """Return the raw exclusion list (or [] if file missing/corrupt)."""
    path = excluded_file()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("excluded") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        # Coerce entries to {category, name} dicts; drop malformed.
        out: list[dict[str, str]] = []
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("category"), str) and isinstance(it.get("name"), str):
                out.append({"category": it["category"], "name": it["name"]})
        return out
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("failed to read excluded list at %s: %s", path, exc)
        return []


def _write_excluded(items: list[dict[str, str]]) -> Path:
    """Atomically persist the exclusion list."""
    path = excluded_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        # Stable-sort so two writes that produce equal sets diff cleanly in git.
        sorted_items = sorted(items, key=lambda d: (d["category"], d["name"]))
        tmp.write_text(
            json.dumps({"excluded": sorted_items}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        _log.exception("failed to persist excluded list to %s", path)
        raise HTTPException(
            status_code=500,
            detail=f"could not write excluded list: {exc}",
        ) from exc
    return path


def excluded_keys() -> set[str]:
    """Return the exclusion list as a set of ``"category:name"`` keys.

    Used by ``spa_real.py`` to overlay ``in_config`` on inventory items.
    """
    return {f'{e["category"]}:{e["name"]}' for e in _read_excluded()}


# ── Wire schemas ─────────────────────────────────────────────────────────


class ExclusionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(..., min_length=1, description="Source: winget / msstore / registry_arp / windows_update / ...")
    name: str = Field(..., min_length=1, description="Package name as it appears in inventory.items[].name")


class AppItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    category: str
    installed: str | None = None
    candidate: str | None = None
    status: str = "ok"
    in_config: bool = True
    source: str | None = None


class AppsDetectResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    apps: list[AppItem] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, list[str]] = Field(default_factory=dict)
    # Legacy keys preserved for backward-compat with the SPA's old
    # `tracked / detected / missing` rendering — populated from `apps`.
    tracked: list[str] = Field(default_factory=list)
    detected: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


# ── Endpoints ────────────────────────────────────────────────────────────


def _load_inventory_apps(request: Request) -> list[dict[str, Any]]:
    """Borrow ``spa_real``'s DB-first resolver so Apps and Categories never
    disagree.

    Pre-Sesja-32 this endpoint had its own bucket-loading path that
    bypassed the check-sidecar bucket-replacement logic, which is why
    Apps showed only 1 brew row while Categories showed 143. Now both
    code paths funnel through ``_resolve_buckets`` (DB-first; live scan
    + DB populate on miss), guaranteeing parity.
    """
    # Local import to avoid a routes-import cycle at module-load time.
    from . import spa_real

    adapter = getattr(request.app.state, "adapter", None)
    if adapter is None:
        return []

    runs_dir = getattr(request.app.state, "runs_dir", None)
    bucketed = spa_real._resolve_buckets(request, adapter)  # noqa: SLF001

    # Overlay the most recent check sidecar per category so installed /
    # candidate / status reflect reality even when the DB row was
    # populated from a stale scan that didn't yet include version data.
    if runs_dir is not None:
        for cat, items in bucketed.items():
            overlay = spa_real._latest_check_overlay(runs_dir, cat)  # noqa: SLF001
            if not overlay:
                continue
            for it in items:
                hit = overlay.get(it["name"])
                if hit:
                    if hit.get("installed") is not None:
                        it["installed"] = hit["installed"]
                    if hit.get("candidate") is not None:
                        it["candidate"] = hit["candidate"]
                    if hit.get("status"):
                        it["status"] = hit["status"]

    apps: list[dict[str, Any]] = []
    for cat, items in bucketed.items():
        for it in items:
            apps.append({**it, "category": cat})
    return apps


@router.get("/apps/detect", response_model=AppsDetectResponse)
async def apps_detect(request: Request) -> AppsDetectResponse:
    """Every detected app + in_config=True for any not in the exclusion list.

    The default-include model means a fresh user sees every package as
    tracked. They opt OUT individually via /apps/exclude.
    """
    apps_raw = _load_inventory_apps(request)
    excl = excluded_keys()

    apps: list[AppItem] = []
    by_category: dict[str, list[str]] = {}
    tracked: list[str] = []
    detected: list[str] = []  # SPA legacy: items NOT in config
    missing: list[str] = []   # SPA legacy: tracked items missing on disk
    for raw in apps_raw:
        cat = raw.get("category") or raw.get("source") or "unknown"
        name = raw.get("name") or "(unknown)"
        key = f"{cat}:{name}"
        in_cfg = key not in excl
        apps.append(
            AppItem(
                name=name,
                category=cat,
                installed=raw.get("installed"),
                candidate=raw.get("candidate"),
                status=raw.get("status") or "ok",
                in_config=in_cfg,
                source=raw.get("source") or cat,
            ),
        )
        by_category.setdefault(cat, []).append(name)
        (tracked if in_cfg else detected).append(name)

    return AppsDetectResponse(
        apps=apps,
        summary={
            "total": len(apps),
            "tracked": len(tracked),
            "excluded": len(detected),
            "missing": len(missing),
        },
        by_category={k: sorted(v) for k, v in by_category.items()},
        tracked=tracked,
        detected=detected,
        missing=missing,
    )


@router.post("/apps/exclude")
async def apps_exclude(entry: ExclusionEntry) -> dict[str, Any]:
    """Add a {category, name} pair to the exclusion list (idempotent)."""
    items = _read_excluded()
    key = (entry.category, entry.name)
    if not any((it["category"], it["name"]) == key for it in items):
        items.append(entry.model_dump())
    path = _write_excluded(items)
    return {"ok": True, "excluded": items, "file": str(path)}


@router.post("/apps/include")
async def apps_include(entry: ExclusionEntry) -> dict[str, Any]:
    """Remove a {category, name} pair from the exclusion list (idempotent)."""
    items = _read_excluded()
    key = (entry.category, entry.name)
    items = [it for it in items if (it["category"], it["name"]) != key]
    path = _write_excluded(items)
    return {"ok": True, "excluded": items, "file": str(path)}


@router.get("/apps/excluded")
async def apps_excluded() -> dict[str, Any]:
    """Read the raw exclusion list as stored on disk."""
    return {"excluded": _read_excluded(), "file": str(excluded_file())}


__all__ = [
    "router",
    "excluded_file",
    "excluded_keys",
]

"""Preloaded suggestion library (stub — pre-AI integration).

Today the library is a deterministic, rule-based generator that matches
templates against the user's installed apps from /inventory. Each card
has ``ai_generated=false``. Future AI integration will optionally
augment this list (e.g. by calling the configured LLM provider in
``routes/ai.py`` to enrich suggestions with reasoning).

The endpoints ship the v2 shape:

    GET  /suggestions/library  — `[{id, title, body, severity, action, ai_generated, created_at}]`

The legacy ``GET /suggestions`` shape (in ``spa_stubs.py``) is still
served for the older frontend; this router is registered FIRST so
``/suggestions/library`` wins before the stub catch-pattern.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

_log = logging.getLogger(__name__)
router = APIRouter(tags=["suggestions"])


# Severity ordering for sorting — high first.
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _outdated_app_card(app: dict[str, Any]) -> dict[str, Any]:
    name = app.get("name") or "?"
    cat = app.get("category") or app.get("source") or "unknown"
    inst = app.get("installed") or "?"
    cand = app.get("candidate") or "?"
    return {
        "id": f"outdated:{cat}:{name}",
        "title": f"Update {name} ({inst} → {cand})",
        "body": (
            f"{name} is outdated on this machine. The {cat} feed has a newer "
            f"version ({cand}); your installed version is {inst}. Apply the "
            f"{cat} category to upgrade."
        ),
        "severity": "medium",
        "category": cat,
        "action": {
            "type": "run_async",
            "label": f"Run {cat} → apply",
            "payload": {"categories": [cat], "phases": ["apply"]},
        },
        "ai_generated": False,
        "created_at": _now_iso(),
    }


def _missing_app_card(app: dict[str, Any]) -> dict[str, Any]:
    name = app.get("name") or "?"
    cat = app.get("category") or app.get("source") or "unknown"
    return {
        "id": f"missing:{cat}:{name}",
        "title": f"{name} is in config but missing on disk",
        "body": (
            f"{name} is tracked in the {cat} config but no installed "
            f"version was found. Either install it via the {cat} apply "
            f"phase or remove it from config in the Apps tab."
        ),
        "severity": "low",
        "category": cat,
        "action": {
            "type": "run_async",
            "label": f"Run {cat} → apply",
            "payload": {"categories": [cat], "phases": ["apply"]},
        },
        "ai_generated": False,
        "created_at": _now_iso(),
    }


def _stale_inventory_card() -> dict[str, Any]:
    return {
        "id": "stale_inventory",
        "title": "Inventory has not been refreshed recently",
        "body": (
            "Run a quick check to refresh the installed-apps list. This "
            "is read-only and finishes in seconds — no system changes."
        ),
        "severity": "info",
        "category": "system",
        "action": {
            "type": "run_async",
            "label": "Run all → check",
            "payload": {"phases": ["check"]},
        },
        "ai_generated": False,
        "created_at": _now_iso(),
    }


def _build_library(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate suggestion cards from the inventory snapshot."""
    cards: list[dict[str, Any]] = []
    if not apps:
        cards.append(_stale_inventory_card())
        return cards

    outdated_count = 0
    missing_count = 0
    for app in apps:
        status = (app.get("status") or "").lower()
        if status == "outdated" and outdated_count < 5:
            cards.append(_outdated_app_card(app))
            outdated_count += 1
        elif status == "missing" and missing_count < 3:
            cards.append(_missing_app_card(app))
            missing_count += 1

    if not cards:
        cards.append({
            "id": "all_good",
            "title": "Everything looks up to date",
            "body": (
                "No outdated or missing apps detected in your inventory. "
                "Re-check periodically to keep an eye on new updates."
            ),
            "severity": "info",
            "category": "system",
            "action": {
                "type": "run_async",
                "label": "Run all → check",
                "payload": {"phases": ["check"]},
            },
            "ai_generated": False,
            "created_at": _now_iso(),
        })

    cards.sort(key=lambda c: _SEVERITY_ORDER.get(c.get("severity", "info"), 99))
    return cards


def _load_apps_from_inventory(request: Request) -> list[dict[str, Any]]:
    """Borrow the apps router's loader so suggestions never disagree
    with /apps/detect."""
    try:
        from . import apps as apps_route

        return apps_route._load_inventory_apps(request)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        _log.warning("suggestions: could not load inventory apps: %s", exc)
        return []


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/suggestions/library")
async def suggestions_library(request: Request) -> dict[str, Any]:
    """Return the preloaded suggestion library.

    Today this is rule-based + deterministic. Future versions will
    augment with AI-generated cards when an LLM provider is configured.
    """
    apps = _load_apps_from_inventory(request)
    items = _build_library(apps)
    return {
        "items": items,
        "count": len(items),
        "ai_generated_count": sum(1 for it in items if it.get("ai_generated")),
        "generated_at": _now_iso(),
    }


__all__ = ["router"]

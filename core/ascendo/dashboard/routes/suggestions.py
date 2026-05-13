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


# ── AI augmentation (Sesja 67) ───────────────────────────────────────────


_AI_SYSTEM_PROMPT = (
    "You are Ascendo, a Windows system-update advisor. Look at the "
    "inventory snapshot and produce 1-3 short, actionable suggestion "
    "cards as a JSON array. Each card MUST be a JSON object with "
    "these exact keys:\n"
    "  id        — short snake_case identifier\n"
    "  title     — under 60 chars\n"
    "  body      — 1-2 sentences, plain text, no markdown\n"
    "  severity  — one of 'info' | 'warn' | 'err'\n"
    "  category  — short tag (e.g. 'security', 'maintenance', 'hygiene')\n"
    "  action    — optional object {type, label, payload} when the\n"
    "              operator can act. type='run_async' triggers an Ascendo\n"
    "              run; payload contains {profile?, phases?, categories?}.\n"
    "Return ONLY the JSON array, no prose, no markdown fences."
)


def _ai_snapshot_for_prompt(apps: list[dict[str, Any]]) -> str:
    """Summarise the inventory into a compact prompt fragment."""
    outdated = [a for a in apps if (a.get("status") or "").lower() == "outdated"]
    missing  = [a for a in apps if (a.get("status") or "").lower() == "missing"]
    by_cat: dict[str, int] = {}
    for a in apps:
        c = a.get("category") or "?"
        by_cat[c] = by_cat.get(c, 0) + 1
    lines = [
        f"Total tracked packages: {len(apps)}",
        f"Outdated: {len(outdated)}",
        f"Missing: {len(missing)}",
        "Per-category counts: "
        + ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())),
    ]
    if outdated:
        lines.append("\nOutdated samples (top 10):")
        for a in outdated[:10]:
            lines.append(
                f"  - {a.get('name','?')} "
                f"[{a.get('category','?')}] "
                f"{a.get('installed','?')} -> {a.get('candidate','?')}"
            )
    if missing:
        lines.append("\nMissing samples (top 5):")
        for a in missing[:5]:
            lines.append(f"  - {a.get('name','?')} [{a.get('category','?')}]")
    return "\n".join(lines)


def _parse_ai_cards(text: str) -> list[dict[str, Any]]:
    """Parse the LLM's JSON output into validated card dicts.

    The model is asked to return ONLY a JSON array. We're tolerant of
    a leading code fence (```json) since some providers add it anyway,
    and we accept either a top-level array or a {"cards": [...]} wrap.
    Each parsed card is sanitised: required keys defaulted, action
    payload restricted to the known shape, ai_generated set true.
    """
    import json as _json
    import re as _re

    text = text.strip()
    # Strip ```json ... ``` fences if present.
    fence = _re.match(r"^```(?:json)?\s*(.*?)```\s*$", text, _re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        parsed = _json.loads(text)
    except _json.JSONDecodeError:
        # Try to recover an embedded array.
        m = _re.search(r"\[\s*\{.*\}\s*\]", text, _re.DOTALL)
        if not m:
            return []
        try:
            parsed = _json.loads(m.group(0))
        except _json.JSONDecodeError:
            return []

    if isinstance(parsed, dict):
        parsed = parsed.get("cards") or parsed.get("items") or []
    if not isinstance(parsed, list):
        return []

    valid_severity = {"info", "warn", "err"}
    out: list[dict[str, Any]] = []
    for c in parsed:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "").strip()
        body = str(c.get("body") or "").strip()
        if not title or not body:
            continue
        sev = str(c.get("severity") or "info").lower()
        if sev not in valid_severity:
            sev = "info"
        card: dict[str, Any] = {
            "id": str(c.get("id") or f"ai_{len(out) + 1}"),
            "title": title[:120],
            "body": body[:600],
            "severity": sev,
            "category": str(c.get("category") or "ai")[:40],
            "ai_generated": True,
            "created_at": _now_iso(),
        }
        # Validate action shape if present.
        action = c.get("action")
        if isinstance(action, dict):
            atype = str(action.get("type") or "")
            label = str(action.get("label") or "")
            payload = action.get("payload")
            if atype == "run_async" and label and isinstance(payload, dict):
                # Allow only known payload keys.
                clean_payload = {
                    k: v for k, v in payload.items()
                    if k in ("profile", "phases", "categories")
                }
                card["action"] = {
                    "type": "run_async",
                    "label": label[:80],
                    "payload": clean_payload,
                }
        out.append(card)
        if len(out) >= 3:
            break
    return out


def _maybe_augment_with_ai(
    cards: list[dict[str, Any]],
    apps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """When an AI provider is configured, ask it for 1-3 extra cards.

    Returns ``(merged_cards, ai_meta)``. ``ai_meta`` is non-None when an
    AI call was attempted, even if it failed — that way the SPA can show
    a small "AI off" / "AI error" hint instead of pretending nothing
    happened.
    """
    try:
        from . import ai as ai_route
    except Exception:  # noqa: BLE001
        return cards, None
    cfg = ai_route._read_config()  # noqa: SLF001
    provider = (cfg.get("provider") or "").strip().lower()
    model = (cfg.get("model") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    base_url = (cfg.get("base_url") or "").strip() or None
    if not provider or not model:
        return cards, None
    # Some providers (ollama, lm_studio) work without an API key.
    if provider in ("anthropic", "openai", "openrouter", "google") and not api_key:
        return cards, {"provider": provider, "model": model,
                       "ok": False, "error": "no API key configured"}

    user_prompt = _ai_snapshot_for_prompt(apps)
    result = ai_route.call_provider_inference(
        provider=provider, model=model, api_key=api_key, base_url=base_url,
        system_prompt=_AI_SYSTEM_PROMPT, user_prompt=user_prompt,
        max_tokens=1200, timeout=8.0,
    )
    meta: dict[str, Any] = {"provider": provider, "model": model,
                            "ok": bool(result.get("ok"))}
    if not result.get("ok"):
        meta["error"] = result.get("error") or "AI call failed"
        _log.warning("suggestions AI augmentation failed: %s", meta["error"])
        return cards, meta

    ai_cards = _parse_ai_cards(result.get("content") or "")
    meta["count"] = len(ai_cards)
    if ai_cards:
        # AI cards land at the TOP so they're immediately visible, then
        # the rule-based ones below them for the deterministic backbone.
        cards = ai_cards + cards
    return cards, meta


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/suggestions/library")
async def suggestions_library(request: Request) -> dict[str, Any]:
    """Return the preloaded suggestion library.

    Rule-based cards are always emitted. When an LLM provider is
    configured at ``/ai/config``, we ALSO call it with a short
    inventory summary and merge the returned cards on top. AI failures
    fall back transparently to rule-based — the operator never sees a
    500 even when the configured provider is offline.
    """
    apps = _load_apps_from_inventory(request)
    rule_cards = _build_library(apps)
    items, ai_meta = _maybe_augment_with_ai(rule_cards, apps)
    return {
        "items": items,
        "count": len(items),
        "ai_generated_count": sum(1 for it in items if it.get("ai_generated")),
        "ai": ai_meta,  # null when no provider configured
        "generated_at": _now_iso(),
    }


__all__ = ["router"]

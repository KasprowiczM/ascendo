"""build_context(): smart context injection.

Always-on base + per-template extras, capped at 4k tokens. Output is a
markdown blob prepended to the system prompt with explicit delimiters
the LLM can recognize.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .resolvers import get_registry


MAX_CONTEXT_TOKENS = 4_000


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def build_context(
    *,
    message: str,
    template_id: str | None,
    locale: str,
    adapter: Any,
    runs_dir: Path | None,
    inventory_db: Any,
    chats_db: Any,
    conversation_id: str | None,
    extra_tags: list[str] | None = None,
) -> str:
    """Build the context blob prepended to the system prompt."""
    parts: list[str] = ["<ascendo_context>"]

    # ---- Locale + language hint ----
    lang_hint = "Respond in Polish." if locale == "pl" else "Respond in English."
    parts.append(f"## Locale\n{lang_hint}")

    # ---- Base context: doctor rollup ----
    if adapter is not None:
        try:
            health = adapter.health_check()
            components = health.get("components", []) or []
            ok = sum(1 for c in components if c.get("status") == "ok")
            total = len(components)
            parts.append(f"## Doctor\n{ok}/{total} components ok")
        except Exception:
            pass

    # ---- Base context: inventory totals ----
    if inventory_db is not None:
        try:
            totals = inventory_db.totals_by_category()
            if totals:
                cat_line = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()))
                parts.append(f"## Inventory totals\n{cat_line}")
        except Exception:
            pass

    # ---- Per-template extras ----
    tags = list(extra_tags or [])
    registry = get_registry()
    resolved: list[tuple[str, int]] = []
    for tag in tags:
        fn = registry.get(tag)
        if fn is None:
            continue
        try:
            text, priority = fn(
                adapter=adapter, inventory_db=inventory_db, runs_dir=runs_dir,
            )
        except Exception:
            continue
        if text:
            resolved.append((text, priority))

    # ---- Greedy fill highest-priority first within budget ----
    base_tokens = _est_tokens("\n\n".join(parts))
    remaining = MAX_CONTEXT_TOKENS - base_tokens
    resolved.sort(key=lambda t: -t[1])
    truncated_any = False
    for text, _ in resolved:
        cost = _est_tokens(text)
        if cost <= remaining:
            parts.append(text)
            remaining -= cost
        else:
            # Truncate to fit
            if remaining > 100:
                parts.append(text[: remaining * 4] + "\n... [truncated]")
                truncated_any = True
            break

    parts.append("</ascendo_context>")
    return "\n\n".join(parts)

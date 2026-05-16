"""Action proposals: fence parser + ALLOWED_ACTIONS whitelist + dispatcher.

The LLM emits action proposals as fenced code blocks with language
`ascendo-action` containing a JSON object. The SPA parses them out
and renders chips; clicking a chip POSTs /ai/chat/action which
validates against ALLOWED_ACTIONS and returns a proxy plan.

This is the security boundary — the LLM can propose any action_id
it wants, but the dispatcher rejects anything not in the whitelist.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ============================ Pydantic body schemas ============================

class RunPhaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[str] = Field(min_length=1)
    phases: list[Literal["check", "plan", "apply", "verify", "cleanup"]] = Field(min_length=1)


class ScheduleInstallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9-]+$")
    expression: str
    profile: Literal["quick", "safe", "full"]
    enabled: bool = True


class ScheduleNameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9-]+$")


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbeEntryBody(BaseModel):
    # A candidate web_apps.toml entry (validated downstream against the
    # macOS WebApp model by /web/probe-entry). Permissive here so the
    # AI can pass any handler shape; the route does the real validation.
    model_config = ConfigDict(extra="allow")


class WebOverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    toml_snippet: str


class SkipListBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: Literal["macos", "windows", "ubuntu"]
    add: list[str] = []
    remove: list[str] = []


class OpenViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: Literal[
        "overview", "categories", "run-center", "history",
        "schedule", "settings", "aitools",
    ]


# ============================ Whitelist ============================

ALLOWED_ACTIONS: dict[str, tuple[str, str, type[BaseModel]]] = {
    "run_check":         ("POST", "/runs/async",                  RunPhaseBody),
    "run_plan":          ("POST", "/runs/async",                  RunPhaseBody),
    "run_apply":         ("POST", "/runs/async",                  RunPhaseBody),
    "run_verify":        ("POST", "/runs/async",                  RunPhaseBody),
    "run_cleanup":       ("POST", "/runs/async",                  RunPhaseBody),
    "install_schedule":  ("POST", "/scheduler/install",           ScheduleInstallBody),
    "remove_schedule":   ("POST", "/scheduler/remove",            ScheduleNameBody),
    "trigger_schedule":  ("POST", "/scheduler/trigger",           ScheduleNameBody),
    "refresh_inventory": ("POST", "/inventory/db/refresh",        EmptyBody),
    "add_web_override":  ("POST", "/ai/chat/action/web_override", WebOverrideBody),
    "edit_skip_list":    ("POST", "/ai/chat/action/skip_list",    SkipListBody),
    "open_view":         ("local", "navigate",                    OpenViewBody),
    # Phase C: read-only candidate-entry dry-run for the AI-config loop.
    "web_probe":         ("POST", "/web/probe-entry",             ProbeEntryBody),
}


# ── Action metadata (Phase C.4: scoped read-only auto-fire) ──────────────────
# Only entries that are BOTH read_only AND risk="low" may be
# auto-executed server-side during a turn (the documented Task-18
# extension). Everything else stays propose -> user-confirms -> fire.
# Default for any unlisted action: mutating, not auto-fireable.
ACTION_META: dict[str, dict] = {
    "web_probe": {"read_only": True, "risk": "low"},
}


def is_auto_fireable(action_id: str) -> bool:
    """True only for read-only, low-risk actions (currently web_probe)."""
    m = ACTION_META.get(action_id or "")
    return bool(m and m.get("read_only") and m.get("risk") == "low")


# ============================ Fence parser ============================

FENCE_RE = re.compile(r"```ascendo-action\s*\n(.*?)\n```", re.DOTALL)


def parse_actions(text: str) -> tuple[list[dict], str]:
    """Extract `ascendo-action` fences from text.

    Returns (list_of_action_dicts, cleaned_text_with_fences_removed).
    Invalid JSON fences are also removed (don't surface partial code blocks
    to the user — keep the prose clean and report the parse failure via
    the SPA's console).
    """
    actions: list[dict] = []

    def _sub(m: re.Match) -> str:
        body = m.group(1).strip()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ""
        if isinstance(data, dict) and "id" in data:
            actions.append(data)
        return ""

    cleaned = FENCE_RE.sub(_sub, text).strip()
    return actions, cleaned


# ============================ Dispatcher ============================

def dispatch_action(*, action_id: str, body: dict) -> dict:
    """Validate action + body. Returns proxy plan, does NOT execute.

    The caller (route handler) takes the returned {verb, path, body} and
    proxies to the underlying endpoint via the FastAPI app.
    """
    if action_id not in ALLOWED_ACTIONS:
        return {"ok": False, "error": "unknown_action"}
    verb, path, schema = ALLOWED_ACTIONS[action_id]
    try:
        validated = schema(**body)
    except ValidationError as e:
        return {"ok": False, "error": f"invalid_body: {e.errors()}"}
    return {
        "ok": True,
        "action_id": action_id,
        "verb": verb,
        "path": path,
        "body": validated.model_dump(),
    }

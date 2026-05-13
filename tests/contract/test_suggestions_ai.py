"""Regression tests for Sesja 67's Suggestions AI augmentation.

The /suggestions/library endpoint now optionally calls a configured
LLM provider to augment the rule-based cards. These tests pin the
parser, snapshot builder, and graceful-fallback behaviour so a
future refactor cannot silently break either path.

Tests use monkeypatching to inject fake provider responses — no real
HTTP traffic. The end-to-end "configured provider returns cards"
flow is covered by the parser test + the helper test, kept
deterministic.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Force-load from this worktree's core/ — editable install may point at main.
_WORKTREE_CORE = Path(__file__).resolve().parents[2] / "core"
if _WORKTREE_CORE.is_dir() and str(_WORKTREE_CORE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_CORE))

import importlib  # noqa: E402

for mod in (
    "ascendo.dashboard.routes.ai",
    "ascendo.dashboard.routes.suggestions",
):
    if mod in sys.modules:
        importlib.reload(sys.modules[mod])

from ascendo.dashboard.routes.suggestions import (  # noqa: E402
    _parse_ai_cards,
    _ai_snapshot_for_prompt,
    _maybe_augment_with_ai,
)


# ── _parse_ai_cards ──────────────────────────────────────────────────────


def test_parser_accepts_clean_json_array() -> None:
    txt = (
        '[{"id":"a1","title":"Update VS Code","body":"VS Code is outdated.",'
        '"severity":"warn","category":"security"}]'
    )
    cards = _parse_ai_cards(txt)
    assert len(cards) == 1
    assert cards[0]["title"] == "Update VS Code"
    assert cards[0]["severity"] == "warn"
    assert cards[0]["ai_generated"] is True


def test_parser_strips_markdown_code_fences() -> None:
    txt = '```json\n[{"title":"X","body":"Y","severity":"info"}]\n```'
    cards = _parse_ai_cards(txt)
    assert len(cards) == 1
    assert cards[0]["title"] == "X"


def test_parser_recovers_array_from_prose_wrapper() -> None:
    txt = (
        'Here are my suggestions: '
        '[{"title":"Check Windows Update","body":"5 patches pending",'
        '"severity":"warn"}]'
    )
    cards = _parse_ai_cards(txt)
    assert len(cards) == 1
    assert cards[0]["title"] == "Check Windows Update"


def test_parser_returns_empty_on_garbage() -> None:
    assert _parse_ai_cards("No JSON here.") == []
    assert _parse_ai_cards("") == []
    assert _parse_ai_cards("[invalid json") == []


def test_parser_caps_at_three_cards() -> None:
    cards_in = [
        {"title": f"t{i}", "body": "x", "severity": "info"}
        for i in range(10)
    ]
    import json
    cards = _parse_ai_cards(json.dumps(cards_in))
    assert len(cards) == 3


def test_parser_rejects_invalid_severity() -> None:
    txt = '[{"title":"X","body":"Y","severity":"critical"}]'
    cards = _parse_ai_cards(txt)
    assert len(cards) == 1
    assert cards[0]["severity"] == "info"  # normalised


def test_parser_action_payload_sanitised() -> None:
    """run_async actions must only carry known payload keys (no shell
    injection vectors, no arbitrary code paths through the SPA bridge)."""
    txt = (
        '[{"title":"Run check","body":"Y","severity":"info",'
        '"action":{"type":"run_async","label":"Go",'
        '"payload":{"profile":"safe","phases":["check"],'
        '"categories":["winget"],"exec":"rm -rf /","steal":"y"}}}]'
    )
    cards = _parse_ai_cards(txt)
    assert len(cards) == 1
    action = cards[0]["action"]
    assert action["type"] == "run_async"
    # Only profile / phases / categories should survive.
    payload_keys = set(action["payload"].keys())
    assert payload_keys == {"profile", "phases", "categories"}
    assert "exec" not in action["payload"]
    assert "steal" not in action["payload"]


def test_parser_ignores_unknown_action_types() -> None:
    """Only 'run_async' is allowed; everything else gets stripped."""
    txt = (
        '[{"title":"X","body":"Y","severity":"info",'
        '"action":{"type":"open_url","label":"go","payload":{"url":"http://x"}}}]'
    )
    cards = _parse_ai_cards(txt)
    assert "action" not in cards[0]


def test_parser_truncates_long_fields() -> None:
    long_title = "A" * 500
    long_body = "B" * 1000
    txt = (
        '[{"title":"' + long_title + '","body":"' + long_body
        + '","severity":"info"}]'
    )
    cards = _parse_ai_cards(txt)
    assert len(cards[0]["title"]) <= 120
    assert len(cards[0]["body"]) <= 600


# ── _ai_snapshot_for_prompt ──────────────────────────────────────────────


def test_snapshot_includes_totals_and_outdated_samples() -> None:
    apps = [
        {"name": "VSCode", "category": "web", "installed": "1.119",
         "candidate": "1.120", "status": "outdated"},
        {"name": "Git", "category": "winget", "installed": "2.45",
         "candidate": "2.45", "status": "ok"},
        {"name": "MissingApp", "category": "msstore", "status": "missing"},
    ]
    snap = _ai_snapshot_for_prompt(apps)
    assert "Total tracked packages: 3" in snap
    assert "Outdated: 1" in snap
    assert "Missing: 1" in snap
    assert "VSCode" in snap and "1.119 -> 1.120" in snap
    assert "MissingApp" in snap


# ── _maybe_augment_with_ai ───────────────────────────────────────────────


def test_augment_no_provider_returns_unchanged_cards(monkeypatch) -> None:
    """When no AI provider is configured, the function returns the
    rule-based cards untouched + ai_meta=None."""
    import ascendo.dashboard.routes.ai as ai_route
    monkeypatch.setattr(ai_route, "_read_config", lambda: {})
    rule_cards = [{"id": "r1", "title": "Rule", "body": "x"}]
    out, meta = _maybe_augment_with_ai(rule_cards, [])
    assert out == rule_cards
    assert meta is None


def test_augment_missing_api_key_for_cloud_provider(monkeypatch) -> None:
    """Cloud providers without an API key get ai_meta with error,
    rule-based cards still returned."""
    import ascendo.dashboard.routes.ai as ai_route
    monkeypatch.setattr(
        ai_route, "_read_config",
        lambda: {"provider": "anthropic", "model": "claude-3-5", "api_key": ""},
    )
    rule_cards = [{"id": "r1", "title": "Rule", "body": "x"}]
    out, meta = _maybe_augment_with_ai(rule_cards, [])
    assert out == rule_cards
    assert meta is not None
    assert meta["ok"] is False
    assert "API key" in meta["error"]


def test_augment_provider_failure_falls_back_to_rule_cards(monkeypatch) -> None:
    """Provider returns ``{ok: false, error}`` → keep rule-based cards,
    surface ai_meta with the failure for the SPA to show."""
    import ascendo.dashboard.routes.ai as ai_route
    monkeypatch.setattr(
        ai_route, "_read_config",
        lambda: {"provider": "ollama", "model": "llama3", "api_key": "",
                 "base_url": "http://localhost:11434"},
    )
    monkeypatch.setattr(
        ai_route, "call_provider_inference",
        lambda **_kw: {"ok": False, "error": "network error: refused"},
    )
    rule_cards = [{"id": "r1", "title": "Rule", "body": "x"}]
    out, meta = _maybe_augment_with_ai(rule_cards, [])
    assert out == rule_cards
    assert meta["ok"] is False
    assert "refused" in meta["error"]


def test_augment_provider_success_prepends_ai_cards(monkeypatch) -> None:
    """Successful AI call yields cards merged at the TOP of the list."""
    import ascendo.dashboard.routes.ai as ai_route
    monkeypatch.setattr(
        ai_route, "_read_config",
        lambda: {"provider": "openai", "model": "gpt-4", "api_key": "sk-test"},
    )
    monkeypatch.setattr(
        ai_route, "call_provider_inference",
        lambda **_kw: {
            "ok": True,
            "content": (
                '[{"id":"ai1","title":"Reboot soon","body":"Pending update",'
                '"severity":"warn","category":"system"}]'
            ),
        },
    )
    rule_cards = [{"id": "r1", "title": "Rule card", "body": "x",
                   "severity": "info", "ai_generated": False}]
    out, meta = _maybe_augment_with_ai(rule_cards, [])
    assert len(out) == 2
    # AI card first
    assert out[0]["ai_generated"] is True
    assert out[0]["title"] == "Reboot soon"
    # Rule card preserved second
    assert out[1]["title"] == "Rule card"
    assert meta["ok"] is True
    assert meta["count"] == 1

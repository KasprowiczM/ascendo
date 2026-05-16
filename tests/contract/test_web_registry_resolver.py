"""C.1: the web_registry_schema resolver must emit the CORRECT v2
schema (not the stale schema=2 / [[apps]] form), the user's CURRENT
override file, and THIS machine's uncovered apps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ascendo.ai.resolvers import web_registry_schema as wrs
from ascendo.orchestrator.report import ActionItem


class _Adapter:
    def health_check(self) -> dict:
        return {"components": []}


def _resolve(runs_dir: Path):
    out = wrs.resolve(adapter=_Adapter(), inventory_db=None, runs_dir=runs_dir)
    text, prio = out
    assert isinstance(prio, int)
    return text


def test_resolver_emits_correct_v2_schema(tmp_path, monkeypatch):
    monkeypatch.delenv("ASCENDO_WEB_USER_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(wrs, "_collect_uncovered", lambda _rd: [])
    text = _resolve(tmp_path)
    assert 'schema = "ascendo-web-apps/v2"' in text
    assert "[[app]]" in text
    # The stale/wrong forms must be gone.
    assert "schema = 2" not in text
    assert "[[apps]]" not in text
    assert "[apps.github_release]" not in text
    # A correct per-handler example using the real field names.
    assert "github_dmg" in text and "github_repo" in text


def test_resolver_includes_current_overrides(tmp_path, monkeypatch):
    user = tmp_path / "web_apps.toml"
    user.write_text(
        'schema = "ascendo-web-apps/v2"\n\n[[app]]\n'
        'slug = "my-marker-app"\nbundle_id = "com.x.y"\n'
        'display_name = "Y"\nhandler = "builtin"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ASCENDO_WEB_USER_REGISTRY_PATH", str(user))
    monkeypatch.setattr(wrs, "_collect_uncovered", lambda _rd: [])
    text = _resolve(tmp_path)
    assert "my-marker-app" in text


def test_resolver_no_override_says_none(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "ASCENDO_WEB_USER_REGISTRY_PATH", str(tmp_path / "absent.toml")
    )
    monkeypatch.setattr(wrs, "_collect_uncovered", lambda _rd: [])
    text = _resolve(tmp_path)
    assert "none yet" in text.lower() or "no user override" in text.lower()


def test_resolver_includes_machine_uncovered(tmp_path, monkeypatch):
    monkeypatch.delenv("ASCENDO_WEB_USER_REGISTRY_PATH", raising=False)
    fake = [
        ActionItem(
            category="web", name="Megasync", slug="megasync",
            current="6.2.2", candidate="6.3.0.1", reason="no_silent_path",
            reason_text="x", open_hint="Open Megasync",
        ),
        ActionItem(
            category="web", name="Perplexity", slug="perplexity",
            current="", candidate="", reason="probe_broken",
            reason_text="y", open_hint="Open Perplexity",
        ),
    ]
    monkeypatch.setattr(wrs, "_collect_uncovered", lambda _rd: fake)
    text = _resolve(tmp_path)
    assert "megasync" in text
    assert "perplexity" in text
    assert "probe_broken" in text

"""End-to-end tests for /ai/chat/* endpoints using FastAPI TestClient."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ai_cli"


class _FakeAdapter:
    name = "fake"

    @property
    def capabilities(self):
        return "PACKAGE_MANAGEMENT|INVENTORY"

    def health_check(self):
        return {"components": []}


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build a fresh dashboard app with a per-test ASCENDO_HOME + ChatsDB."""
    monkeypatch.setenv("PATH", str(FIXTURES) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path / ".ascendo"))
    monkeypatch.setenv("ASCENDO_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FIXTURE_CASE", "success")
    # Drop cached module so create_app sees the new env vars.
    sys.modules.pop("ascendo.dashboard.app", None)
    from ascendo.dashboard.app import create_app

    return create_app(adapter=_FakeAdapter(), runs_dir=tmp_path / "runs")


def test_post_conversations_creates(app):
    c = TestClient(app)
    r = c.post("/ai/chat/conversations", json={})
    assert r.status_code == 201
    j = r.json()
    assert "id" in j and len(j["id"]) >= 16


def test_list_conversations_empty(app):
    c = TestClient(app)
    r = c.get("/ai/chat/conversations")
    assert r.status_code == 200
    assert r.json() == {"conversations": []}


def test_list_conversations_after_create(app):
    c = TestClient(app)
    c.post("/ai/chat/conversations", json={})
    c.post("/ai/chat/conversations", json={"title": "Second"})
    j = c.get("/ai/chat/conversations").json()
    assert len(j["conversations"]) == 2
    titles = {row["title"] for row in j["conversations"]}
    assert "Second" in titles


def test_get_unknown_conversation_404(app):
    c = TestClient(app)
    r = c.get("/ai/chat/conversations/nonexistent-id")
    assert r.status_code == 404


def test_delete_conversation_then_get_404(app):
    c = TestClient(app)
    cid = c.post("/ai/chat/conversations", json={}).json()["id"]
    r = c.delete(f"/ai/chat/conversations/{cid}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.get(f"/ai/chat/conversations/{cid}").status_code == 404


def test_backends_endpoint_returns_list(app):
    c = TestClient(app)
    r = c.get("/ai/chat/backends")
    assert r.status_code == 200
    j = r.json()
    assert "backends" in j
    assert isinstance(j["backends"], list)


def test_library_endpoint_returns_entries(app):
    c = TestClient(app)
    r = c.get("/ai/chat/library")
    assert r.status_code == 200
    j = r.json()
    assert "entries" in j and len(j["entries"]) >= 5
    # Every entry has the required shape from Task 14.
    for entry in j["entries"]:
        assert "id" in entry
        assert "title" in entry and "en" in entry["title"]


def test_post_action_unknown_returns_400(app):
    c = TestClient(app)
    r = c.post("/ai/chat/action", json={"action_id": "not_real", "body": {}})
    assert r.status_code == 400


def test_post_action_known_validates_body(app):
    c = TestClient(app)
    # Missing required fields (categories, phases) - schema rejects.
    r = c.post("/ai/chat/action", json={"action_id": "run_check", "body": {}})
    assert r.status_code in (400, 422)


def test_post_action_run_check_returns_proxy_plan(app):
    c = TestClient(app)
    r = c.post(
        "/ai/chat/action",
        json={
            "action_id": "run_check",
            "body": {"categories": ["winget"], "phases": ["check"]},
        },
    )
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["action_id"] == "run_check"
    assert j["verb"] == "POST"
    assert j["path"] == "/runs/async"
    assert j["body"]["categories"] == ["winget"]


def test_cancel_unknown_turn_404(app):
    c = TestClient(app)
    r = c.post("/ai/chat/cancel/does-not-exist")
    assert r.status_code == 404


def test_stream_unknown_turn_404(app):
    c = TestClient(app)
    r = c.get("/ai/chat/stream/does-not-exist")
    assert r.status_code == 404

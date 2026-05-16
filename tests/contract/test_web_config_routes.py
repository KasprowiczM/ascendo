"""Contract tests for the web-config routes (Phase A: /web/open).

Phase C extends this file with /web/probe-entry tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.dashboard.routes import web_config


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Never actually launch a GUI app in the test suite.
    monkeypatch.setattr(web_config, "_open_bundle", lambda *_a, **_k: True)
    app = create_app(runs_dir=tmp_path)
    app.state.adapter = None
    return TestClient(app)


def test_web_open_known_slug(client: TestClient) -> None:
    # `obsidian` is in the shipped adapters/macos/config/web_apps.toml.
    r = client.post("/web/open", json={"slug": "obsidian"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["slug"] == "obsidian"
    assert body["bundle_id"]


def test_web_open_unknown_slug_404(client: TestClient) -> None:
    r = client.post("/web/open", json={"slug": "definitely-not-a-real-app"})
    assert r.status_code == 404


def test_web_open_rejects_bad_slug(client: TestClient) -> None:
    # Pydantic pattern rejects shell-meaningful / uppercase input.
    r = client.post("/web/open", json={"slug": "../etc/passwd"})
    assert r.status_code == 422

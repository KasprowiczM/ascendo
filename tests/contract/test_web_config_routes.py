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


# ── /web/probe-entry (Phase C) ───────────────────────────────────────────────

_VALID_RF = {
    "slug": "my-app",
    "bundle_id": "com.example.MyApp",
    "display_name": "My App",
    "handler": "release_feed",
    "release_feed": {"url": "https://example.com/v.json", "version_path": "version"},
}


def test_probe_entry_resolves(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(web_config, "_probe_handler", lambda *_a: ("9.9.9", ""))
    r = client.post("/web/probe-entry", json=_VALID_RF)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] is True
    assert b["validated"] is True
    assert b["resolved_version"] == "9.9.9"
    assert b["error"] == ""


def test_probe_entry_invalid_schema_422(client: TestClient) -> None:
    r = client.post(
        "/web/probe-entry",
        json={"slug": "BAD CAPS", "handler": "not-a-handler"},
    )
    assert r.status_code == 422


def test_probe_entry_no_version_reports_raw(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        web_config, "_probe_handler", lambda *_a: ("", "curl: (22) 404")
    )
    r = client.post("/web/probe-entry", json=_VALID_RF)
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is False
    assert b["error"] == "probe returned no version"
    assert "404" in b["raw_probe_output"]


def test_probe_entry_builtin_short_circuits(client: TestClient, monkeypatch) -> None:
    called = {"n": 0}
    monkeypatch.setattr(
        web_config, "_probe_handler",
        lambda *_a: (called.__setitem__("n", called["n"] + 1), ("", ""))[1],
    )
    r = client.post(
        "/web/probe-entry",
        json={
            "slug": "b", "bundle_id": "com.b.B",
            "display_name": "B", "handler": "builtin",
        },
    )
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is False
    assert "Tier-B" in b["error"]
    assert called["n"] == 0  # builtin never shells out to a probe

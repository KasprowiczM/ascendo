"""Contract tests for the persistent onboarding endpoint.

Replaces the legacy ``spa_stubs`` placeholders that always reported
``onboarded: True`` (which suppressed the wizard entirely). The real
endpoint persists to ``ASCENDO_ONBOARDING_FILE`` so the wizard shows on
fresh installs and stays hidden after the user finishes / skips.

These tests use :class:`fastapi.testclient.TestClient` so they don't need
a real adapter — onboarding is adapter-agnostic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app

from .test_dashboard import FakeAdapter


@pytest.fixture
def isolated_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the onboarding-state file into a per-test tmp dir."""
    target = tmp_path / "onboarding.json"
    monkeypatch.setenv("ASCENDO_ONBOARDING_FILE", str(target))
    return target


@pytest.fixture
def client(tmp_path: Path, isolated_state_file: Path) -> TestClient:
    app = create_app(adapter=FakeAdapter(), runs_dir=tmp_path / "runs")
    return TestClient(app)


# -- GET /onboarding/state ------------------------------------------------


def test_state_fresh_install_returns_not_onboarded(
    client: TestClient,
    isolated_state_file: Path,
) -> None:
    """No state file on disk → ``onboarded: false`` so the wizard opens."""
    assert not isolated_state_file.exists()
    r = client.get("/onboarding/state")
    assert r.status_code == 200
    body = r.json()
    assert body["onboarded"] is False
    # ``completed_at`` must be present (None) so the SPA can rely on the shape.
    assert body.get("completed_at") is None


def test_state_after_complete_is_persistent(
    client: TestClient,
    isolated_state_file: Path,
) -> None:
    """``POST /onboarding/complete`` must round-trip on the next ``GET``."""
    payload = {
        "language": "en",
        "theme": "dark",
        "admin_authorised": True,
        "inventory_seen": True,
        "dry_run_seen": False,
        "skipped": False,
        "step": 6,
    }
    r = client.post("/onboarding/complete", json=payload)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["ok"] is True
    assert done["onboarded"] is True
    assert done["completed_at"]
    assert done["path"] == str(isolated_state_file)
    assert isolated_state_file.is_file()

    # Subsequent GET reflects the persisted choice.
    r = client.get("/onboarding/state")
    assert r.status_code == 200
    body = r.json()
    assert body["onboarded"] is True
    assert body["language"] == "en"
    assert body["theme"] == "dark"
    assert body["admin_authorised"] is True
    assert body["step"] == 6
    assert body["completed_at"] == done["completed_at"]


def test_state_skipped_payload(client: TestClient, isolated_state_file: Path) -> None:
    """A 'skip' click should persist with ``skipped: true`` and the step number."""
    r = client.post(
        "/onboarding/complete",
        json={"skipped": True, "step": 2, "language": "pl"},
    )
    assert r.status_code == 200
    persisted = json.loads(isolated_state_file.read_text(encoding="utf-8"))
    assert persisted["onboarded"] is True
    assert persisted["skipped"] is True
    assert persisted["step"] == 2
    assert persisted["language"] == "pl"


def test_state_corrupt_file_falls_back_to_defaults(
    client: TestClient,
    isolated_state_file: Path,
) -> None:
    """A bogus JSON file must not 500 — return the fresh-install shape."""
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text("{not-json", encoding="utf-8")
    r = client.get("/onboarding/state")
    assert r.status_code == 200
    body = r.json()
    assert body["onboarded"] is False


def test_complete_rejects_unknown_fields(client: TestClient) -> None:
    """``extra='forbid'`` on the request body — typos must 422."""
    r = client.post(
        "/onboarding/complete",
        json={"language": "en", "totally_unexpected_field": "boom"},
    )
    assert r.status_code == 422


def test_complete_idempotent(client: TestClient, isolated_state_file: Path) -> None:
    """Calling complete twice should rewrite, not error."""
    r1 = client.post("/onboarding/complete", json={"language": "en", "step": 6})
    assert r1.status_code == 200
    r2 = client.post("/onboarding/complete", json={"language": "pl", "step": 6})
    assert r2.status_code == 200
    body = client.get("/onboarding/state").json()
    # Last write wins.
    assert body["language"] == "pl"
    assert body["onboarded"] is True

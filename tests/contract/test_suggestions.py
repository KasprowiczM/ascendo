"""Contract tests for the preloaded suggestion library (``routes/suggestions.py``)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.dashboard.routes import suggestions as suggestions_route


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ASCENDO_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ASCENDO_AI_CONFIG", str(tmp_path / "ai.json"))
    monkeypatch.setenv("ASCENDO_EXCLUDED_FILE", str(tmp_path / "excluded.json"))
    app = create_app(adapter=None, runs_dir=tmp_path / "runs")
    with TestClient(app) as c:
        yield c


def test_suggestions_library_returns_card_when_no_inventory(client: TestClient) -> None:
    """No adapter / no inventory → at least the 'stale_inventory' fallback card."""
    r = client.get("/suggestions/library")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "count" in body
    assert "ai_generated_count" in body
    assert "generated_at" in body
    assert body["count"] >= 1
    assert body["ai_generated_count"] == 0
    # Each card has the contract shape.
    for card in body["items"]:
        for k in ("id", "title", "body", "severity", "action", "ai_generated", "created_at"):
            assert k in card, f"missing key {k}"
        assert isinstance(card["action"], dict)
        assert "type" in card["action"]
        assert "payload" in card["action"]


def test_suggestions_library_with_outdated_apps_emits_outdated_cards(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject a fake ``_load_inventory_apps`` that returns 2 outdated apps —
    the library MUST emit one card per outdated app + sort them first."""
    fake_apps = [
        {
            "name": "Mozilla.Firefox",
            "category": "winget",
            "installed": "131.0",
            "candidate": "132.0",
            "status": "outdated",
        },
        {
            "name": "Microsoft.PowerShell",
            "category": "winget",
            "installed": "7.5.0",
            "candidate": "7.6.0",
            "status": "outdated",
        },
        {
            "name": "Up.To.Date.App",
            "category": "winget",
            "installed": "1.0",
            "candidate": "1.0",
            "status": "ok",
        },
    ]
    monkeypatch.setattr(
        suggestions_route,
        "_load_apps_from_inventory",
        lambda _request: fake_apps,
    )
    r = client.get("/suggestions/library")
    assert r.status_code == 200
    body = r.json()
    titles = [card["title"] for card in body["items"]]
    # Both outdated apps MUST surface as cards.
    assert any("Mozilla.Firefox" in t for t in titles)
    assert any("Microsoft.PowerShell" in t for t in titles)
    # The action carries a runnable run-async payload pointing at the right category.
    fox = next(c for c in body["items"] if "Firefox" in c["title"])
    assert fox["action"]["type"] == "run_async"
    assert "winget" in fox["action"]["payload"]["categories"]
    assert "apply" in fox["action"]["payload"]["phases"]
    assert fox["ai_generated"] is False

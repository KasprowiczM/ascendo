"""The dashboard resolves the web registry through the active adapter (A5).

core must not import an adapter package (ADR-0005). web_config routes ask the
active ``IAdapter`` for a provider via ``adapter.web_registry()``. This proves
the route uses the adapter-supplied provider — a fake provider's registry is
consulted, NOT the real macOS one.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app

from .test_dashboard import FakeAdapter


class _FakeApp:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.bundle_id = "com.fake." + slug
        self.app_path = None


class _FakeRegistry:
    def __init__(self) -> None:
        self._apps = {"fakeslug": _FakeApp("fakeslug")}

    def find(self, slug: str):
        return self._apps.get(slug)


class _FakeProvider:
    shipped_registry_path = None
    lib_dir = None

    def load_merged(self, user_path):  # noqa: ARG002
        return _FakeRegistry()

    def validate_app(self, raw):  # pragma: no cover - not exercised here
        raise ValueError("not used")

    def validate_registry(self, apps):  # pragma: no cover
        return None


class _AdapterWithWebRegistry(FakeAdapter):
    def web_registry(self):
        return _FakeProvider()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    # Don't actually launch any GUI app when /web/open hits _open_bundle.
    import ascendo.dashboard.routes.web_config as wc
    monkeypatch.setattr(wc, "_open_bundle", lambda *a, **k: True)
    app = create_app(adapter=_AdapterWithWebRegistry(), runs_dir=tmp_path)
    # `with` triggers the lifespan, which registers the active adapter.
    with TestClient(app) as c:
        yield c


def test_dashboard_uses_adapter_web_registry(client: TestClient) -> None:
    # The fake provider's registry has exactly "fakeslug".
    r = client.post("/web/open", json={"slug": "fakeslug"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "fakeslug"

    # A slug the REAL macOS registry has (e.g. vscode) but the fake provider
    # does NOT — a 404 proves the route consulted the adapter's provider, not
    # the hard-imported ascendo_macos registry.
    r2 = client.post("/web/open", json={"slug": "vscode"})
    assert r2.status_code == 404

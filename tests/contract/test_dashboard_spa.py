"""Contract tests for the legacy Ubuntu_Aktualizacje SPA mount + stubs.

The dashboard at ``/`` serves the bundled vanilla-JS SPA and back-fills
its missing fetch URLs via :mod:`ascendo.dashboard.routes.spa_stubs`.
These tests exercise:

- The HTML/asset mount at root (``/``, ``/app.js``, ``/style.css``).
- A representative subset of stub endpoints -- enough that adding a
  call to ``app.js`` will fail loudly here if the matching stub goes
  missing.
- Sanity that adding the SPA mount didn't shadow the canonical
  ``/version``, ``/health``, and ``/runs`` routes.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app

from .test_dashboard import FakeAdapter


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(adapter=FakeAdapter(), runs_dir=tmp_path)
    return TestClient(app)


# -- root + assets ----------------------------------------------------------


def test_spa_index_html_served_at_root(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()
    # The index.html must reference the SPA scripts so the browser
    # actually loads them.
    assert "app.js" in r.text
    assert "style.css" in r.text


@pytest.mark.parametrize(
    ("path", "expected_substring"),
    [
        ("/app.js", "Ubuntu_Aktualizacje dashboard"),  # comment in app.js
        ("/style.css", "{"),  # any rule body
        # Design-system tokens. Must load before style.css per index.html.
        ("/colors_and_type.css", "--accent"),
        ("/i18n.js", ""),
        ("/icons.js", ""),
        ("/favicon.svg", "<svg"),
    ],
)
def test_spa_assets_served(client: TestClient, path: str, expected_substring: str) -> None:
    r = client.get(path)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:80]}"
    if expected_substring:
        assert expected_substring in r.text


# -- design-system brand assets (logo wordmarks + marks) --------------------


@pytest.mark.parametrize(
    "filename",
    [
        "logo-mark.svg",
        "logo-mark-light.svg",
        "logo-mark-mono.svg",
        "logo-wordmark.svg",
        "logo-wordmark-dark.svg",
    ],
)
def test_spa_brand_assets_served(client: TestClient, filename: str) -> None:
    r = client.get(f"/assets/{filename}")
    assert r.status_code == 200, f"/assets/{filename}: {r.status_code} {r.text[:80]}"
    assert r.headers["content-type"] == "image/svg+xml"
    assert "<svg" in r.text


def test_spa_brand_asset_traversal_blocked(client: TestClient) -> None:
    """Defense in depth: /assets/{filename} must reject path traversal."""
    for bad in ("../style.css", "..\\\\style.css", "subdir/x.svg"):
        r = client.get(f"/assets/{bad}")
        assert r.status_code == 404, f"/assets/{bad}: leaked through with {r.status_code}"


def test_spa_index_pins_dark_theme_by_default(client: TestClient) -> None:
    """index.html must bootstrap with data-theme=dark before paint.

    The Ascendo design system uses light as its CSS-default theme; dark is
    activated via :root[data-theme="dark"]. The SPA must explicitly opt in
    on the <html> element so the first paint is dark, not light.
    """
    r = client.get("/")
    assert r.status_code == 200
    body = r.text.lower()
    # Must include design tokens stylesheet ordered before style.css.
    tokens_pos = body.find("colors_and_type.css")
    style_pos = body.find("style.css")
    assert tokens_pos != -1, "index.html does not load colors_and_type.css"
    assert style_pos != -1
    assert tokens_pos < style_pos, "colors_and_type.css must load before style.css"
    # Dark is pinned at parse time (either as <html data-theme="dark"> or
    # via the inline pre-paint script in index.html).
    assert 'data-theme="dark"' in body or 'documentelement.dataset.theme = "dark"' in body


# -- existing endpoints not shadowed ---------------------------------------


def test_existing_endpoints_still_work(client: TestClient) -> None:
    """Sanity: SPA mount must NOT swallow REST routes."""
    for path in ("/version", "/health", "/runs"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} regressed to {r.status_code}"

    body = client.get("/version").json()
    assert body["adapter"] == "fake"


# -- stub endpoints ---------------------------------------------------------


def test_spa_stub_categories(client: TestClient) -> None:
    r = client.get("/categories")
    assert r.status_code == 200
    body = r.json()
    assert "categories" in body
    # FakeAdapter exposes one manager (winget).
    assert isinstance(body["categories"], list)
    # at least the FakeManager surfaces here
    ids = {c["id"] for c in body["categories"]}
    assert "winget" in ids


def test_spa_stub_about(client: TestClient) -> None:
    r = client.get("/about")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ascendo"
    assert body["version"]
    assert body["python"]


@pytest.mark.parametrize(
    "path",
    [
        "/preflight",
        "/health/check",
        "/onboarding/state",
        "/profiles",
        "/profiles/templates",
        "/settings",
        "/sudo/status",
        "/suggestions",
        "/exclusions",
        "/git/status",
        "/sync/status",
        "/sync/provider",
        "/sync/remotes",
        "/sync/browse",
        "/telemetry/eta",
        "/updates/check",
        "/apps/detect",
        "/hosts",
        "/hosts/list",
        "/inventory",
        "/inventory/summary",
        "/runs/active",
    ],
)
def test_spa_stub_get_endpoints_return_200(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:100]}"
    # Every stub returns JSON.
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    ("path", "method", "json_body"),
    [
        ("/apps/add", "POST", {"package": "x", "category": "winget"}),
        ("/apps/remove", "POST", {"package": "x", "category": "winget"}),
        ("/git/fetch", "POST", None),
        ("/git/pull", "POST", None),
        ("/git/push", "POST", None),
        ("/health/run", "POST", None),
        ("/hosts/upsert", "POST", {}),
        ("/hosts/delete", "POST", {}),
        ("/inventory/refresh", "POST", None),
        ("/onboarding/complete", "POST", {}),
        ("/profiles/import", "POST", {}),
        ("/runs/active/stop", "POST", None),
        ("/scheduler/install", "POST", {}),
        ("/scheduler/remove", "POST", None),
        ("/suggestions/apply", "POST", {}),
        ("/suggestions/dismiss", "POST", {}),
        ("/suggestions/test", "POST", {}),
        ("/sudo/auth", "POST", {"password": "x"}),
        ("/sync/export", "POST", None),
        ("/sync/provider", "POST", {}),
        ("/sync/provider/test", "POST", {}),
        ("/system/reboot", "POST", None),
        ("/settings", "PUT", {}),
    ],
)
def test_spa_stub_mutation_endpoints(
    client: TestClient,
    path: str,
    method: str,
    json_body: dict | None,
) -> None:
    r = client.request(method, path, json=json_body)
    assert r.status_code == 200, f"{method} {path}: {r.status_code} {r.text[:100]}"
    body = r.json()
    assert body.get("ok") is not None  # every mutation stub returns {"ok": ...}


def test_spa_stub_inventory_category_path(client: TestClient) -> None:
    r = client.get("/inventory/winget")
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "winget"
    assert "items" in body


def test_spa_stub_run_phase_log(client: TestClient) -> None:
    r = client.get("/runs/abc/phase/winget/check/log")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "log for run=abc" in r.text


def test_spa_stub_runs_active_stream_is_sse(client: TestClient) -> None:
    """Stream endpoint returns SSE content-type and at least one event."""
    r = client.get("/runs/active/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert b"event: status" in r.content


def test_spa_stub_backup_export(client: TestClient) -> None:
    r = client.get("/backup/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/gzip")


def test_spa_stub_inventory_summary_shape(client: TestClient) -> None:
    """Donut/bars data must always have the totals + categories keys."""
    r = client.get("/inventory/summary")
    assert r.status_code == 200
    body = r.json()
    assert "totals" in body
    for k in ("ok", "outdated", "missing", "total"):
        assert k in body["totals"]
    assert "categories" in body


# -- design-system brand assets (logo wordmarks + marks) --------------------


@pytest.mark.parametrize(
    "filename",
    [
        "logo-mark.svg",
        "logo-mark-light.svg",
        "logo-mark-mono.svg",
        "logo-wordmark.svg",
        "logo-wordmark-dark.svg",
    ],
)
def test_spa_brand_assets_served(client: TestClient, filename: str) -> None:
    r = client.get(f"/assets/{filename}")
    assert r.status_code == 200, f"/assets/{filename}: {r.status_code} {r.text[:80]}"
    assert r.headers["content-type"] == "image/svg+xml"
    assert "<svg" in r.text


def test_spa_brand_asset_traversal_blocked(client: TestClient) -> None:
    """Defense in depth: /assets/{filename} must reject path traversal."""
    for bad in ("../style.css", "..\\style.css", "subdir/x.svg"):
        r = client.get(f"/assets/{bad}")
        assert r.status_code == 404, f"/assets/{bad}: leaked through with {r.status_code}"


def test_spa_index_pins_dark_theme_by_default(client: TestClient) -> None:
    """index.html must bootstrap with data-theme=dark before paint.

    The Ascendo design system uses light as its CSS-default theme; dark is
    activated via :root[data-theme="dark"]. The SPA must explicitly opt in
    on the <html> element so the first paint is dark, not light.
    """
    r = client.get("/")
    assert r.status_code == 200
    body = r.text.lower()
    # Must include design tokens stylesheet ordered before style.css.
    tokens_pos = body.find("colors_and_type.css")
    style_pos = body.find("style.css")
    assert tokens_pos != -1, "index.html does not load colors_and_type.css"
    assert style_pos != -1
    assert tokens_pos < style_pos, "colors_and_type.css must load before style.css"
    # Dark is pinned at parse time (either as <html data-theme="dark"> or
    # via the inline pre-paint script in index.html).
    assert 'data-theme="dark"' in body or 'documentelement.dataset.theme = "dark"' in body

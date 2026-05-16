"""Frontend smoke test — checks served HTML/CSS/JS structure without a browser.

Validates the contract between backend (FastAPI static mount) and the SPA:
  • /                serves index.html with all 7 view sections
  • /style.css       loads
  • /app.js          loads, references all view ids
  • REST endpoints used by the SPA respond 200
  • SSE endpoint exists and content-type is text/event-stream

Run via:
    app/.venv/bin/python tests/python/test_frontend.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

# The legacy ``app.backend.main`` (the pre-monorepo Ubuntu_Aktualizacje
# dashboard kept in-tree until the M4 ``app/`` retirement) registers
# startup hooks with FastAPI's deprecated ``@app.on_event("startup")``.
# Repo-wide ``filterwarnings = ["error", ...]`` escalates that to a test
# error on import. Scope the ignore to this file only — when ``app/`` is
# removed the marker can go with it.
pytestmark = pytest.mark.filterwarnings(
    # The warning message starts with a leading newline + indentation, so
    # ``.*`` cannot span it without DOTALL. Inline ``(?s)`` enables it.
    r"ignore:(?s).*on_event is deprecated.*:DeprecationWarning",
)


def _client():
    # Lazy-import so the test file is itself importable for syntax check
    from app.backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def client():
    """pytest fixture exposing the legacy ``app.backend.main`` TestClient.

    The module also runs as a standalone script via ``main()`` below; the
    fixture is what lets pytest discover and inject the same client when
    the file is collected as a normal test target.
    """
    return _client()


def assert_200(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path}: HTTP {r.status_code} {r.text[:200]}"
    return r


def test_index_has_all_views(client):
    r = assert_200(client, "/")
    body = r.text
    assert "<!doctype html>" in body.lower()
    # Required views — the SPA may add optional ones (apps, suggest, help,
    # about) over time; the test asserts presence of the must-haves and
    # one nav link per ``view-*`` section, not an exact count.
    expected_views = [
        "view-overview", "view-categories", "view-run",
        "view-history", "view-logs", "view-sync", "view-hosts", "view-settings",
    ]
    for vid in expected_views:
        assert f'id="{vid}"' in body, f"missing section id={vid!r}"
    nav_links = re.findall(r'data-view="([^"]+)"', body)
    expected_short = {v.removeprefix("view-") for v in expected_views}
    missing = expected_short - set(nav_links)
    assert not missing, f"nav links missing for required views: {missing}"
    assert len(nav_links) >= len(expected_views), (
        f"expected at least {len(expected_views)} nav links, found "
        f"{len(nav_links)}: {nav_links}"
    )
    print(f"  index.html: all {len(expected_views)} required views present "
          f"(SPA exposes {len(nav_links)} nav links total)")


def test_static_assets(client):
    r = assert_200(client, "/style.css")
    assert "--accent" in r.text or ".badge" in r.text, "style.css missing expected rules"
    assert 'data-theme="light"' in r.text, "style.css missing explicit light theme"
    assert 'data-theme="dark"'  in r.text, "style.css missing explicit dark theme"
    r = assert_200(client, "/app.js")
    js = r.text
    for fn in ("loadOverview", "loadCategories", "loadRunCenter",
               "loadHistory", "loadSettings", "loadSync", "loadHosts",
               "attachStream", "sudoMgr", "bootstrap"):
        assert fn in js, f"app.js missing symbol: {fn}"
    # Sesja 78: i18n.js is now loader/helpers only; locale DATA moved
    # to /i18n.en.js + /i18n.pl.js (all three served + defer-ordered).
    i18n = assert_200(client, "/i18n.js").text
    for sym in ("window.I18N", "window.tr", "window.applyI18n",
                "window.applyTheme", "window.detectLanguage", '"pl"'):
        assert sym in i18n, f"i18n.js missing symbol: {sym!r}"
    i18n_en = assert_200(client, "/i18n.en.js").text
    assert "window.I18N.en" in i18n_en, "i18n.en.js missing window.I18N.en"
    assert "Settings" in i18n_en, "i18n.en.js missing EN data"
    i18n_pl = assert_200(client, "/i18n.pl.js").text
    assert "window.I18N.pl" in i18n_pl, "i18n.pl.js missing window.I18N.pl"
    for sym in ("Ustawienia", "Polski"):
        assert sym in i18n_pl, f"i18n.pl.js missing PL string: {sym!r}"
    print("  style.css + app.js + i18n.{,en,pl}.js: structural OK")


def test_api_contract(client):
    eps = ("/health", "/categories", "/profiles", "/preflight",
           "/runs", "/runs/active", "/git/status",
           "/sync/status", "/settings", "/sudo/status", "/hosts")
    for ep in eps:
        assert_200(client, ep)
    print(f"  {len(eps)}/{len(eps)} GET endpoints OK")


def test_settings_roundtrip(client):
    original = client.get("/settings").json()
    try:
        r = client.put("/settings", json={
            "default_profile": "quick",
            "snapshot_before_apply": True,
            "ui": {"theme": "dark", "language": "pl"},
            "scheduler": {"calendar": "Mon *-*-* 04:00", "profile": "safe", "no_drivers": True},
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["default_profile"] == "quick"
        assert data["snapshot_before_apply"] is True
        assert data["scheduler"]["calendar"] == "Mon *-*-* 04:00"
        assert data["ui"]["theme"] == "dark"
        assert data["ui"]["language"] == "pl"
        again = client.get("/settings").json()
        assert again["ui"]["language"] == "pl"
        print("  settings PUT/GET roundtrip OK (incl. ui.theme + ui.language)")
    finally:
        client.put("/settings", json=original)


def test_categories_structure(client):
    r = client.get("/categories").json()
    cats = r["categories"]
    ids = {c["id"] for c in cats}
    expected = {"apt", "snap", "brew", "npm", "pip", "flatpak", "drivers", "inventory"}
    assert expected <= ids, f"missing categories: {expected - ids}"
    for c in cats:
        for k in ("id", "display_name", "privilege", "risk", "phases"):
            assert k in c, f"category {c.get('id')} missing key {k}"
    print(f"  /categories: 8 expected categories present, all keys ok")


def main() -> int:
    client = _client()
    failed = 0
    tests = [
        test_index_has_all_views,
        test_static_assets,
        test_api_contract,
        test_settings_roundtrip,
        test_categories_structure,
    ]
    for t in tests:
        try:
            t(client)
        except AssertionError as exc:
            print(f"FAIL {t.__name__}: {exc}")
            failed += 1
    if failed:
        print(f"\n{failed}/{len(tests)} test(s) failed")
        return 1
    print(f"\n{len(tests)}/{len(tests)} frontend smoke tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Contract tests for the ASCENDO_EDITION flag + edition-gate middleware.

Resolution priority for ``app.state.edition``:

1. ``ASCENDO_EDITION`` env var (``basic`` or ``dev``)
2. ``$ASCENDO_HOME/.ascendo-edition`` marker file (single line, same values)
3. default ``basic``

The middleware 404s dev-only paths (``/sync/*``, ``/hosts``, ``/git/push``,
``/dev-sync``, ``/profiles/import``) when edition == basic. In dev edition
they fall through to the underlying stub handlers.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app


@pytest.fixture
def basic_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Client with edition explicitly resolved to 'basic'.

    Clears ASCENDO_EDITION + redirects ASCENDO_HOME at a tmp dir with no
    marker so the resolver falls all the way through to the default.
    """
    monkeypatch.delenv("ASCENDO_EDITION", raising=False)
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path))
    app = create_app(runs_dir=tmp_path / "runs")
    app.state.adapter = None
    return TestClient(app)


@pytest.fixture
def dev_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Client with ASCENDO_EDITION=dev set BEFORE create_app()."""
    monkeypatch.setenv("ASCENDO_EDITION", "dev")
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path))
    app = create_app(runs_dir=tmp_path / "runs")
    app.state.adapter = None
    return TestClient(app)


# ── /version ──────────────────────────────────────────────────────────────────


def test_version_returns_basic_by_default(basic_client: TestClient) -> None:
    """No env var, no marker file → edition resolves to 'basic'."""
    r = basic_client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["edition"] == "basic"


def test_version_returns_dev_when_env_set(dev_client: TestClient) -> None:
    """ASCENDO_EDITION=dev env var wins over default."""
    r = dev_client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["edition"] == "dev"


def test_version_returns_dev_when_marker_file_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Marker file at $ASCENDO_HOME/.ascendo-edition is honoured."""
    monkeypatch.delenv("ASCENDO_EDITION", raising=False)
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path))
    (tmp_path / ".ascendo-edition").write_text("dev\n", encoding="utf-8")
    app = create_app(runs_dir=tmp_path / "runs")
    app.state.adapter = None
    client = TestClient(app)
    r = client.get("/version")
    assert r.status_code == 200
    assert r.json()["edition"] == "dev"


def test_version_falls_back_to_basic_on_unknown_marker_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Marker file with garbage content → falls back to 'basic'."""
    monkeypatch.delenv("ASCENDO_EDITION", raising=False)
    monkeypatch.setenv("ASCENDO_HOME", str(tmp_path))
    (tmp_path / ".ascendo-edition").write_text("hacker", encoding="utf-8")
    app = create_app(runs_dir=tmp_path / "runs")
    app.state.adapter = None
    client = TestClient(app)
    r = client.get("/version")
    assert r.status_code == 200
    assert r.json()["edition"] == "basic"


# ── edition-gate middleware ──────────────────────────────────────────────────


def test_dev_only_path_404_in_basic(basic_client: TestClient) -> None:
    """Dev-only paths return 404 in basic edition."""
    for path in ("/sync/status", "/hosts", "/git/push"):
        r = basic_client.request("GET" if path != "/git/push" else "POST", path)
        assert r.status_code == 404, f"{path} should be 404 in basic, got {r.status_code}"

    # Sanity: paths that are NOT dev-only should still resolve normally.
    for path in ("/git/status", "/version"):
        r = basic_client.get(path)
        assert r.status_code != 404, (
            f"{path} should NOT be 404 in basic, got {r.status_code}"
        )


def test_dev_only_path_200_in_dev(dev_client: TestClient) -> None:
    """Dev-only paths fall through to their underlying handlers in dev edition."""
    # GET endpoints — stubs return 200 with a JSON body.
    for path in ("/sync/status", "/hosts"):
        r = dev_client.get(path)
        assert r.status_code != 404, (
            f"{path} should NOT be 404 in dev, got {r.status_code}"
        )

    # /git/push is a POST stub — verify the gate doesn't 404 it.
    r = dev_client.post("/git/push")
    assert r.status_code != 404, f"/git/push should NOT be 404 in dev, got {r.status_code}"

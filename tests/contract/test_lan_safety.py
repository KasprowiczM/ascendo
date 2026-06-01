"""LAN-safety guards for the dashboard (audit P2/§7).

Loopback-only by default. A non-loopback bind must be an explicit opt-in, and
when opted in, mutating endpoints from non-loopback peers require a capability
token. Under TestClient the peer host is "testclient" (treated as non-loopback)
so the token gate is directly exercisable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app

from .test_dashboard import FakeAdapter


def test_refuses_non_loopback_bind_without_allow_remote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ASCENDO_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError, match="non-loopback"):
        create_app(adapter=FakeAdapter(), runs_dir=tmp_path, host="0.0.0.0")


def test_refuses_non_loopback_bind_even_with_wildcard_cors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ASCENDO_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError):
        create_app(
            adapter=FakeAdapter(), runs_dir=tmp_path,
            host="0.0.0.0", cors_origins=["*"],
        )


def test_loopback_default_has_no_token_gate(tmp_path: Path) -> None:
    # Default (loopback) app: mutating requests work with no token (TestClient
    # peer is non-loopback but the guard isn't installed for loopback binds).
    app = create_app(adapter=FakeAdapter(), runs_dir=tmp_path)
    with TestClient(app) as c:
        # /runs is a real mutating endpoint; no token header sent.
        r = c.post("/runs", json={"phases": ["check"]})
        assert r.status_code != 403


def test_allow_remote_gates_mutating_without_token(tmp_path: Path) -> None:
    app = create_app(
        adapter=FakeAdapter(), runs_dir=tmp_path,
        host="0.0.0.0", allow_remote=True,
    )
    token = app.state.capability_token
    assert token
    with TestClient(app) as c:
        # TestClient peer == "testclient" (non-loopback) -> needs the token.
        r = c.post("/runs/async", json={"phases": ["check"]})
        assert r.status_code == 403

        # GET (safe method) is never gated.
        assert c.get("/version").status_code == 200

        # With the right token, the mutating request is no longer blocked by
        # the LAN guard (it proceeds to the handler).
        r2 = c.post(
            "/runs/async", json={"phases": ["check"]},
            headers={"X-Ascendo-Token": token},
        )
        assert r2.status_code != 403


def test_allow_remote_via_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASCENDO_ALLOW_REMOTE", "1")
    # Should NOT raise; env opt-in is honoured.
    app = create_app(adapter=FakeAdapter(), runs_dir=tmp_path, host="0.0.0.0")
    assert app.state.capability_token

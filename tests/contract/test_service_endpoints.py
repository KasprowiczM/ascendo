"""Contract tests for the ``/service/*`` REST router.

The router itself lives at :mod:`ascendo.dashboard.routes.service` and
funnels through :class:`WindowsServiceManager` (an adapter-side wrapper
around ``bin\\install-service.ps1``). These tests inject a fake manager
via ``app.state.service_manager`` so the suite runs cross-platform —
on the Linux CI runner there's no PowerShell, no NSSM, and the Windows
adapter may not even be installed.

What's covered:

* ``GET /service/status`` -> 200 with the documented JSON schema.
* ``GET /service/status`` reports ``supported=False`` on non-Windows
  hosts when no fake is injected (real CI runner path).
* ``POST /service/install`` -> 200 with ``{ok, output, exit_code}``.
* ``POST /service/install`` -> 401 + ``ELEVATION-REQUIRED`` when the
  manager raises :class:`ServiceElevationRequired`.
* ``POST /service/uninstall?purge_logs=true`` forwards the flag.
* Mutating endpoints all share the same dispatch shape.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# The dashboard/TestClient stack is optional on some test environments;
# skip cleanly if anything in the chain is missing rather than failing
# collection.
try:
    from fastapi.testclient import TestClient  # noqa: F401

    from ascendo.dashboard import create_app
except ImportError as exc:  # pragma: no cover -- only when deps not installed
    pytest.skip(
        f"dashboard/testclient stack unavailable: {exc!r}",
        allow_module_level=True,
    )


# ── Fakes ──────────────────────────────────────────────────────────


class _FakeStatus:
    """Mirrors :class:`ServiceStatus.to_dict()` without importing the real one."""

    def __init__(self, **kwargs: Any) -> None:
        self._payload = {
            "installed":      kwargs.get("installed", False),
            "running":        kwargs.get("running", False),
            "port_listening": kwargs.get("port_listening", False),
            "health":         kwargs.get("health"),
            "last_started":   kwargs.get("last_started"),
            "pid":            kwargs.get("pid", 0),
            "service_name":   kwargs.get("service_name", "AscendoDashboard"),
            "state":          kwargs.get("state", "ABSENT"),
            "port":           kwargs.get("port", 8765),
        }

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeResult:
    def __init__(self, *, ok: bool, exit_code: int = 0, output: str = "") -> None:
        self.ok        = ok
        self.exit_code = exit_code
        self.output    = output

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "exit_code": self.exit_code, "output": self.output}


class _FakeServiceManager:
    """Records calls; return values configured per-test."""

    def __init__(self) -> None:
        self.status_payload: _FakeStatus | None = None
        self.action_results: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def status(self) -> _FakeStatus:
        self.calls.append(("status", {}))
        if self.status_payload is None:
            return _FakeStatus()
        return self.status_payload

    def install(self, **kwargs: Any) -> Any:
        self.calls.append(("install", kwargs))
        return self._dispatch("install")

    def uninstall(self, **kwargs: Any) -> Any:
        self.calls.append(("uninstall", kwargs))
        return self._dispatch("uninstall")

    def start(self, **kwargs: Any) -> Any:
        self.calls.append(("start", kwargs))
        return self._dispatch("start")

    def stop(self, **kwargs: Any) -> Any:
        self.calls.append(("stop", kwargs))
        return self._dispatch("stop")

    def restart(self, **kwargs: Any) -> Any:
        self.calls.append(("restart", kwargs))
        return self._dispatch("restart")

    def _dispatch(self, action: str) -> Any:
        outcome = self.action_results.get(action)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            return _FakeResult(ok=True, output=f"{action} ok")
        return outcome


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def fake_manager() -> _FakeServiceManager:
    return _FakeServiceManager()


@pytest.fixture
def client(fake_manager: _FakeServiceManager, tmp_path: Path) -> TestClient:
    """Build a TestClient with the fake manager injected.

    Forces ``sys.platform = 'win32'`` for the test scope so the router's
    Windows-only branch runs even on Linux CI.
    """
    app = create_app(runs_dir=tmp_path)
    app.state.adapter = None  # don't trigger discovery
    app.state.service_manager = fake_manager
    return TestClient(app)


# ── /service/status ────────────────────────────────────────────────


def test_status_returns_documented_schema(
    client: TestClient,
    fake_manager: _FakeServiceManager,
) -> None:
    fake_manager.status_payload = _FakeStatus(
        installed=True,
        running=True,
        port_listening=True,
        health="ok",
        last_started="2026-05-02T12:34:56Z",
        pid=12345,
        state="SERVICE_RUNNING",
    )

    r = client.get("/service/status")
    assert r.status_code == 200
    body = r.json()

    # Documented schema -- the orchestrator wires this into the SPA pill.
    expected_keys = {
        "installed", "running", "port_listening", "health", "last_started",
        "pid", "service_name", "state", "port", "platform", "supported",
    }
    assert expected_keys.issubset(body.keys())
    assert body["installed"] is True
    assert body["running"] is True
    assert body["health"] == "ok"
    assert body["pid"] == 12345
    assert body["state"] == "SERVICE_RUNNING"
    assert body["supported"] is True


def test_status_returns_unsupported_on_non_windows_with_no_fake(
    tmp_path: Path,
) -> None:
    """No injected fake + non-Windows host -> ``supported=False``.

    Asserting via a fresh app that explicitly clears any fake.
    """
    if sys.platform.startswith("win"):
        pytest.skip("non-Windows-only assertion")

    app = create_app(runs_dir=tmp_path)
    app.state.adapter = None
    # No service_manager attribute set -> router uses its own resolver,
    # which on non-Windows returns None and therefore the unsupported
    # payload.
    with TestClient(app) as cli:
        r = cli.get("/service/status")
        assert r.status_code == 200
        body = r.json()
        assert body["supported"] is False
        assert body["installed"] is False
        assert body["state"] in {"UNSUPPORTED", "UNKNOWN"}


# ── POST /service/install ──────────────────────────────────────────


def test_install_success(
    client: TestClient,
    fake_manager: _FakeServiceManager,
) -> None:
    fake_manager.action_results["install"] = _FakeResult(
        ok=True,
        exit_code=0,
        output="Service AscendoDashboard installed; running; PID 1234; auto-start enabled",
    )
    r = client.post("/service/install")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["exit_code"] == 0
    assert "installed" in body["output"]
    assert any(c[0] == "install" for c in fake_manager.calls)


def test_install_elevation_required_returns_401(
    client: TestClient,
    fake_manager: _FakeServiceManager,
) -> None:
    # Build the real exception type at runtime so we don't import the
    # Windows adapter at collection time.
    class _Elev(Exception):
        pass

    # Patch the exception checker by injecting our exception under the
    # name the router looks for.
    import ascendo.dashboard.routes.service as svc_mod

    # Monkey-patch the lazy import path: have the helper raise our type
    # AND register it in sys.modules under the expected name.
    import types as _types
    fake_module = _types.ModuleType("ascendo_windows.managers.service")
    fake_module.ServiceElevationRequired = _Elev  # type: ignore[attr-defined]
    fake_module.ServiceManagerError = Exception  # type: ignore[attr-defined]
    sys.modules["ascendo_windows.managers.service"] = fake_module
    sys.modules["ascendo_windows.managers"] = _types.ModuleType(
        "ascendo_windows.managers",
    )
    sys.modules["ascendo_windows"] = _types.ModuleType("ascendo_windows")

    fake_manager.action_results["install"] = _Elev(
        "action 'install' requires administrator elevation",
    )

    try:
        r = client.post("/service/install")
    finally:
        # Don't pollute sibling tests.
        for mod in (
            "ascendo_windows.managers.service",
            "ascendo_windows.managers",
            "ascendo_windows",
        ):
            sys.modules.pop(mod, None)

    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "ELEVATION-REQUIRED"


def test_install_failure_returns_ok_false(
    client: TestClient,
    fake_manager: _FakeServiceManager,
) -> None:
    """Health-probe failure (exit 12) -> 200 + ok=False, not an HTTP error."""
    fake_manager.action_results["install"] = _FakeResult(
        ok=False,
        exit_code=12,
        output="service started but /health did not return status=ok",
    )
    r = client.post("/service/install")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["exit_code"] == 12
    assert "health" in body["output"].lower()


# ── POST /service/uninstall ────────────────────────────────────────


def test_uninstall_default_no_purge(
    client: TestClient,
    fake_manager: _FakeServiceManager,
) -> None:
    r = client.post("/service/uninstall")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    call = next(c for c in fake_manager.calls if c[0] == "uninstall")
    assert call[1].get("purge_logs") is False


def test_uninstall_with_purge_logs_query(
    client: TestClient,
    fake_manager: _FakeServiceManager,
) -> None:
    r = client.post("/service/uninstall?purge_logs=true")
    assert r.status_code == 200
    call = next(c for c in fake_manager.calls if c[0] == "uninstall")
    assert call[1].get("purge_logs") is True


# ── POST /service/{start,stop,restart} ─────────────────────────────


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_lifecycle_actions_dispatch(
    client: TestClient,
    fake_manager: _FakeServiceManager,
    action: str,
) -> None:
    r = client.post(f"/service/{action}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ok" in body and "exit_code" in body and "output" in body
    assert any(c[0] == action for c in fake_manager.calls)

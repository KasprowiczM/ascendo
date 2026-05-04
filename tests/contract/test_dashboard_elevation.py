"""Contract tests for /elevation/{status,auth,invalidate} endpoints.

Uses :class:`fastapi.testclient.TestClient` with two fake adapters:
  - ``_FakeAdapterWithElevation``  -- has an IElevation returning True/False
  - ``_FakeAdapterNoElevation``    -- elevation() returns None (Linux/Windows)
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.interfaces.adapter import AdapterCapability, IAdapter
from ascendo.interfaces.elevation import IElevation, ElevationResult
from ascendo.interfaces.inventory import IInventory
from ascendo.interfaces.package_manager import IPackageManager
from ascendo.interfaces.scheduler import IScheduler
from ascendo.interfaces.snapshot import ISnapshot
from ascendo.interfaces.source import ISource
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.result import Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo


# ── Fake IElevation ──────────────────────────────────────────────────────────


class _FakeElevation(IElevation):
    """In-memory elevation stub; never touches sudo."""

    def __init__(self) -> None:
        self._registered: bool = False

    @property
    def available_methods(self) -> tuple[ElevationMethod, ...]:
        return (ElevationMethod.SUDO,)

    def is_currently_elevated(self, host: HostInfo) -> bool:
        return False

    def register_allowlist(self, allowed_commands: Iterable[str]) -> None:
        pass

    def run(self, host: HostInfo, argv, **kwargs) -> ElevationResult:
        raise NotImplementedError

    # Extra concrete API matched by MacElevation
    def has_password_registered(self) -> bool:
        return self._registered

    def register_password(
        self, password: str, *, verify: bool = True, timeout: int = 15
    ) -> tuple[bool, str]:
        if password == "correct":
            self._registered = True
            return True, "password verified and stored"
        return False, "sudo: 3 incorrect password attempts"

    def invalidate(self) -> None:
        self._registered = False


# ── Fake IAdapter stubs ──────────────────────────────────────────────────────


class _FakeManager(IPackageManager):
    @property
    def category(self) -> SourceType:
        return SourceType.BREW

    @property
    def display_name(self) -> str:
        return "Fake-brew"

    def is_available(self, host: HostInfo) -> bool:
        return True

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        now = datetime.now(timezone.utc)
        return Sidecar.model_validate(
            {
                "schema": SidecarSchema.V1_ASCENDO.value,
                "run": run.model_dump(mode="json"),
                "host": host.model_dump(mode="json"),
                "tool": ToolInfo(name="brew", version="4.0").model_dump(mode="json"),
                "phase": phase.value,
                "category": "brew",
                "started_at": now.isoformat(),
                "finished_at": now.isoformat(),
                "status": PhaseStatus.SUCCESS.value,
                "items": [],
                "summary": Summary(total=0, exit_code=0).model_dump(),
            }
        )


class _BaseAdapter(IAdapter):
    @property
    def name(self) -> str:
        return "macos-fake"

    @property
    def display_name(self) -> str:
        return "macOS (fake)"

    @property
    def tier(self) -> int:
        return 1

    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT | AdapterCapability.ELEVATION

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        return [_FakeManager()]

    def inventory(self) -> IInventory:
        raise NotImplementedError

    def snapshot(self) -> ISnapshot | None:
        return None

    def scheduler(self) -> IScheduler | None:
        return None

    def source(self) -> ISource | None:
        return None

    def detect_host(self) -> HostInfo:
        return HostInfo(
            hostname="mac-fake",
            os=OperatingSystem.MACOS,
            os_version="15.0",
            arch="arm64",
            user="tester",
            is_elevated=False,
            elevation_method=ElevationMethod.SUDO,
        )

    def health_check(self) -> dict[str, str]:
        return {"brew": "ok", "sudo": "ok"}


class _FakeAdapterWithElevation(_BaseAdapter):
    def __init__(self) -> None:
        self._elev = _FakeElevation()

    def elevation(self) -> IElevation | None:
        return self._elev


class _FakeAdapterNoElevation(_BaseAdapter):
    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT  # no ELEVATION

    def elevation(self) -> IElevation | None:
        return None


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client_with_elev(tmp_path: Path) -> TestClient:
    app = create_app(adapter=_FakeAdapterWithElevation(), runs_dir=tmp_path)
    return TestClient(app)


@pytest.fixture
def client_no_elev(tmp_path: Path) -> TestClient:
    app = create_app(adapter=_FakeAdapterNoElevation(), runs_dir=tmp_path)
    return TestClient(app)


# ── GET /elevation/status ─────────────────────────────────────────────────────


def test_elevation_status_not_registered(client_with_elev: TestClient) -> None:
    """Initially no password registered -> registered=False, method='sudo'."""
    r = client_with_elev.get("/elevation/status")
    assert r.status_code == 200
    body = r.json()
    assert body["registered"] is False
    assert body["method"] == "sudo"


def test_elevation_status_no_elevation_adapter(client_no_elev: TestClient) -> None:
    """Adapter with no IElevation -> 503."""
    r = client_no_elev.get("/elevation/status")
    assert r.status_code == 503


# ── POST /elevation/auth ──────────────────────────────────────────────────────


def test_elevation_auth_correct_password(client_with_elev: TestClient) -> None:
    """Correct password -> 200 {"ok": true}; subsequent status shows registered."""
    r = client_with_elev.post("/elevation/auth", json={"password": "correct"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Status now shows registered
    r2 = client_with_elev.get("/elevation/status")
    assert r2.json()["registered"] is True


def test_elevation_auth_wrong_password(client_with_elev: TestClient) -> None:
    """Wrong password -> 401 with detail."""
    r = client_with_elev.post("/elevation/auth", json={"password": "wrong"})
    assert r.status_code == 401
    body = r.json()
    assert "detail" in body
    assert body["detail"]  # non-empty error message


def test_elevation_auth_no_elevation_adapter(client_no_elev: TestClient) -> None:
    """No IElevation -> 503."""
    r = client_no_elev.post("/elevation/auth", json={"password": "anything"})
    assert r.status_code == 503


# ── POST /elevation/invalidate ────────────────────────────────────────────────


def test_elevation_invalidate_idempotent(client_with_elev: TestClient) -> None:
    """Register then invalidate; second invalidate also ok -> 200."""
    # Register first
    client_with_elev.post("/elevation/auth", json={"password": "correct"})
    # Invalidate
    r = client_with_elev.post("/elevation/invalidate")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # Status back to not registered
    r2 = client_with_elev.get("/elevation/status")
    assert r2.json()["registered"] is False
    # Second invalidate (idempotent)
    r3 = client_with_elev.post("/elevation/invalidate")
    assert r3.status_code == 200
    assert r3.json() == {"ok": True}


def test_elevation_invalidate_no_elevation_adapter(client_no_elev: TestClient) -> None:
    """No IElevation -> 503."""
    r = client_no_elev.post("/elevation/invalidate")
    assert r.status_code == 503


# ── Extra coverage for _elevation_or_503 branches ────────────────────────────


def test_endpoints_503_when_no_adapter_configured(tmp_path: Path) -> None:
    """When the dashboard has no adapter at all, /elevation/* endpoints return
    503 with 'no adapter configured' in the detail string."""
    app = create_app(adapter=None, runs_dir=tmp_path)
    # Inject None explicitly so the lifespan discovery path is bypassed.
    app.state.adapter = None
    client = TestClient(app, raise_server_exceptions=False)

    r = client.get("/elevation/status")
    assert r.status_code == 503
    assert "no adapter configured" in r.json()["detail"].lower()


def test_endpoints_503_when_elevation_lacks_askpass_methods(tmp_path: Path) -> None:
    """An IElevation impl that only satisfies the ABC (no password-registration
    methods) returns 503 instead of AttributeError at request time."""

    class _MinimalElevation(IElevation):
        """Implements only the IElevation ABC surface — no askpass extras."""

        @property
        def available_methods(self) -> tuple[ElevationMethod, ...]:
            return (ElevationMethod.SUDO,)

        def is_currently_elevated(self, host: HostInfo) -> bool:
            return False

        def register_allowlist(self, allowed_commands) -> None:
            pass

        def run(self, host: HostInfo, argv, **kwargs) -> ElevationResult:
            raise NotImplementedError

    class _AdapterMinimalElev(_BaseAdapter):
        _elev = _MinimalElevation()

        def elevation(self) -> IElevation | None:
            return self._elev

    app = create_app(adapter=_AdapterMinimalElev(), runs_dir=tmp_path)
    client = TestClient(app)

    for method, path, payload in [
        ("GET", "/elevation/status", None),
        ("POST", "/elevation/auth", {"password": "x"}),
        ("POST", "/elevation/invalidate", None),
    ]:
        if method == "GET":
            r = client.get(path)
        elif payload is not None:
            r = client.post(path, json=payload)
        else:
            r = client.post(path)
        assert r.status_code == 503, f"{method} {path} returned {r.status_code}"
        detail = r.json()["detail"].lower()
        assert "password registration" in detail or "missing" in detail, (
            f"unexpected detail for {method} {path}: {r.json()['detail']}"
        )

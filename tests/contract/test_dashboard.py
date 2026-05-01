"""Contract tests for the dashboard FastAPI app.

Uses :class:`fastapi.testclient.TestClient` (sync — fine for the synchronous
endpoints we have in MVP). A fake :class:`IAdapter` is injected via
``create_app(adapter=...)`` to avoid running real package managers.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from ascendo.dashboard import create_app
from ascendo.interfaces.adapter import AdapterCapability, IAdapter
from ascendo.interfaces.elevation import IElevation
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


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeManager(IPackageManager):
    @property
    def category(self) -> SourceType:
        return SourceType.WINGET

    @property
    def display_name(self) -> str:
        return "Fake-winget"

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
                "tool": ToolInfo(name="winget", version="1.0").model_dump(mode="json"),
                "phase": phase.value,
                "category": "winget",
                "started_at": now.isoformat(),
                "finished_at": now.isoformat(),
                "status": PhaseStatus.SUCCESS.value,
                "items": [],
                "summary": Summary(total=0, exit_code=0).model_dump(),
            }
        )


class FakeAdapter(IAdapter):
    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake"

    @property
    def tier(self) -> int:
        return 1

    @property
    def capabilities(self) -> AdapterCapability:
        return AdapterCapability.PACKAGE_MANAGEMENT

    def package_managers(self, host: HostInfo) -> list[IPackageManager]:
        return [FakeManager()]

    def inventory(self) -> IInventory:
        raise NotImplementedError

    def snapshot(self) -> ISnapshot | None:
        return None

    def scheduler(self) -> IScheduler | None:
        return None

    def source(self) -> ISource | None:
        return None

    def elevation(self) -> IElevation | None:
        return None

    def detect_host(self) -> HostInfo:
        return HostInfo(
            hostname="testhost",
            os=OperatingSystem.WINDOWS,
            os_version="11",
            arch="x86_64",
            user="tester",
            is_elevated=False,
            elevation_method=ElevationMethod.NONE,
        )

    def health_check(self) -> dict[str, str]:
        return {"winget": "ok", "pwsh": "ok: 7.6.0"}


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(adapter=FakeAdapter(), runs_dir=tmp_path)
    return TestClient(app)


@pytest.fixture
def client_no_adapter(tmp_path: Path) -> TestClient:
    """A client with NO adapter pre-injected; lifespan tries discovery and fails."""
    app = create_app(runs_dir=tmp_path)
    # Force adapter to None to skip the real discovery path during the test.
    app.state.adapter = None
    return TestClient(app)


# ── /version ────────────────────────────────────────────────────────────────


def test_version_with_adapter(client: TestClient) -> None:
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["adapter"] == "fake"
    assert body["adapter_tier"] == 1
    assert body["ascendo"]


def test_version_without_adapter(client_no_adapter: TestClient) -> None:
    r = client_no_adapter.get("/version")
    assert r.status_code == 200
    assert r.json()["adapter"] is None


# ── /health ─────────────────────────────────────────────────────────────────


def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["adapter"] == "fake"
    assert "winget" in body["components"]


def test_health_no_adapter_returns_error(client_no_adapter: TestClient) -> None:
    r = client_no_adapter.get("/health")
    assert r.status_code == 200  # endpoint succeeds, content reports the failure
    body = r.json()
    assert body["status"] == "error"
    assert body["adapter"] is None


# ── POST /runs ──────────────────────────────────────────────────────────────


def test_post_runs_runs_full_pipeline(client: TestClient) -> None:
    r = client.post("/runs", json={"profile": "full"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall_status"] == "success"
    assert len(body["sidecars"]) == 5  # all 5 phases × 1 manager
    assert body["aborted_after_phase"] is None
    assert body["host"]["hostname"] == "testhost"


def test_post_runs_subset_phases(client: TestClient) -> None:
    r = client.post("/runs", json={"profile": "quick", "phases": ["check"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["sidecars"]) == 1
    assert body["sidecars"][0]["phase"] == "check"


def test_post_runs_no_adapter_returns_503(client_no_adapter: TestClient) -> None:
    r = client_no_adapter.post("/runs", json={})
    assert r.status_code == 503
    assert "no adapter" in r.json()["detail"].lower()


def test_post_runs_invalid_phase_returns_422(client: TestClient) -> None:
    r = client.post("/runs", json={"phases": ["nonexistent"]})
    assert r.status_code == 422


# ── GET /runs ───────────────────────────────────────────────────────────────


def test_get_runs_index_after_post(client: TestClient) -> None:
    # First: run something so the runs_dir has content
    r = client.post("/runs", json={"phases": ["check"]})
    assert r.status_code == 200
    posted_id = UUID(r.json()["run_id"])

    # Then: list
    r = client.get("/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    ids = {UUID(entry["run_id"]) for entry in body["runs"]}
    assert posted_id in ids


def test_get_runs_specific_id(client: TestClient) -> None:
    r = client.post("/runs", json={"phases": ["check"]})
    posted_id = r.json()["run_id"]

    r = client.get(f"/runs/{posted_id}")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["phase"] == "check"
    assert body[0]["category"] == "winget"


def test_get_runs_unknown_id_returns_404(client: TestClient) -> None:
    r = client.get(f"/runs/{uuid4()}")
    assert r.status_code == 404

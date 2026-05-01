"""Tests for the async run + SSE endpoints (M2.10).

These complement ``test_dashboard.py`` (synchronous endpoints) with the
fire-and-forget pattern needed for ``apply``. We reuse ``FakeAdapter`` /
``FakeManager`` from the sibling file but redefine here to keep modules
independent.
"""
from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

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


class _FakeMgr(IPackageManager):
    @property
    def category(self) -> SourceType:
        return SourceType.WINGET

    @property
    def display_name(self) -> str:
        return "fake"

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
        # Tiny sleep so the SSE stream has time to observe state transitions.
        time.sleep(0.05)
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


class _FakeAdapter(IAdapter):
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
        return [_FakeMgr()]

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
        return {}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(adapter=_FakeAdapter(), runs_dir=tmp_path)
    return TestClient(app)


def _wait_for_status(
    client: TestClient,
    run_id: str,
    target: str,
    *,
    timeout: float = 5.0,
) -> dict:
    """Poll /status until ``target`` reached or timeout."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/runs/{run_id}/status")
        last = r.json()
        if last["status"] == target:
            return last
        time.sleep(0.05)
    pytest.fail(f"timeout waiting for status={target!r}; last={last!r}")


# ── POST /runs/async ────────────────────────────────────────────────────────


def test_post_runs_async_returns_202_with_run_id(client: TestClient) -> None:
    r = client.post("/runs/async", json={"phases": ["check"]})
    assert r.status_code == 202
    body = r.json()
    UUID(body["run_id"])  # parses
    assert body["status_url"].startswith("/runs/")
    assert body["stream_url"].endswith("/events")


def test_async_run_no_adapter_returns_503(tmp_path: Path) -> None:
    app = create_app(runs_dir=tmp_path)
    app.state.adapter = None
    client = TestClient(app)
    r = client.post("/runs/async", json={})
    assert r.status_code == 503


# ── GET /runs/{id}/status ───────────────────────────────────────────────────


def test_status_lifecycle_pending_to_completed(client: TestClient) -> None:
    r = client.post("/runs/async", json={"phases": ["check"]})
    run_id = r.json()["run_id"]

    final = _wait_for_status(client, run_id, "completed", timeout=5.0)
    assert final["status"] == "completed"
    assert final["sidecar_count"] >= 1
    assert final["error"] is None
    assert final["started_at"] is not None
    assert final["finished_at"] is not None


def test_status_unknown_run_id_returns_404(client: TestClient) -> None:
    r = client.get("/runs/00000000-0000-0000-0000-000000000000/status")
    assert r.status_code == 404


# ── GET /runs/{id}/events (SSE) ─────────────────────────────────────────────


def test_sse_emits_status_then_sidecar_then_done(client: TestClient) -> None:
    r = client.post("/runs/async", json={"phases": ["check"]})
    run_id = r.json()["run_id"]

    # The TestClient is sync — we read the streaming body directly.
    with client.stream("GET", f"/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            # Stop once we've seen the 'done' event.
            if b"event: done" in body:
                break

    text = body.decode("utf-8")
    # Expected sequence: status (initial) → sidecar(s) → done
    assert "event: status" in text
    assert "event: sidecar" in text
    assert "event: done" in text
    # Order check (status comes before done):
    assert text.index("event: status") < text.index("event: done")


def test_sse_unknown_run_id_returns_404(client: TestClient) -> None:
    r = client.get("/runs/00000000-0000-0000-0000-000000000000/events")
    assert r.status_code == 404

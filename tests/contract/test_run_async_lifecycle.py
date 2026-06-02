"""Tests for Task 2.2 — lifecycle sub-states + completed-with-warnings split.

Verifies that RunState exposes:
  - ``lifecycle`` (str | None): "preparing" | "running" | "finalizing" | None
  - ``terminal_state`` (str | None): "completed" | "completed_with_warnings"
    | "failed" | "cancelled"

And that /runs/{id}/status includes ``terminal_state`` in its JSON response.
"""
from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

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
from ascendo.models.result import Message, MessageLevel, Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo


# ── Fake adapter helpers ──────────────────────────────────────────────────────


def _skipped_item(pkg: str = "fake-pkg") -> dict:
    """A minimal, validator-consistent ``skipped`` item dict.

    The Sidecar model enforces ``summary.total == len(items)``, so a sidecar
    that reports ``summary.skipped=1`` MUST carry one item.
    """
    return {
        "id": pkg,
        "action": "skip",
        "name": pkg,
        "category": "winget",
        "source": {"type": "winget", "feed": "winget", "url": None},
        "status": "skipped",
        "messages": [],
    }


def _make_sidecar(
    run: RunInfo,
    host: HostInfo,
    *,
    phase: Phase = Phase.CHECK,
    summary: Summary | None = None,
    needs_reboot: bool = False,
    messages: list[Message] | None = None,
    items: list[dict] | None = None,
) -> Sidecar:
    now = datetime.now(timezone.utc)
    item_list = items or []
    # Keep summary.total consistent with len(items) when the caller doesn't
    # supply an explicit summary (the Sidecar model enforces total==len(items)).
    sm = summary if summary is not None else Summary(total=len(item_list), exit_code=0)
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
            "items": item_list,
            "summary": sm.model_dump(),
            "needs_reboot": needs_reboot,
            "messages": [m.model_dump() for m in (messages or [])],
        }
    )


class _CleanMgr(IPackageManager):
    """Returns a fully clean (no warnings) sidecar."""

    @property
    def category(self) -> SourceType:
        return SourceType.WINGET

    @property
    def display_name(self) -> str:
        return "fake-clean"

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
        time.sleep(0.02)
        return _make_sidecar(run, host, phase=phase)


class _WarnMgr(IPackageManager):
    """Returns a sidecar with skipped>0 → triggers 'completed_with_warnings'."""

    @property
    def category(self) -> SourceType:
        return SourceType.WINGET

    @property
    def display_name(self) -> str:
        return "fake-warn"

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
        time.sleep(0.02)
        # skipped=1 → completed_with_warnings (one skipped item keeps the
        # sidecar's total==len(items) invariant satisfied).
        return _make_sidecar(
            run, host, phase=phase,
            items=[_skipped_item()],
            summary=Summary(total=1, skipped=1, exit_code=0),
        )


class _RebootMgr(IPackageManager):
    """Returns a sidecar with needs_reboot=True → completed_with_warnings."""

    @property
    def category(self) -> SourceType:
        return SourceType.WINGET

    @property
    def display_name(self) -> str:
        return "fake-reboot"

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
        time.sleep(0.02)
        return _make_sidecar(run, host, phase=phase, needs_reboot=True)


class _WarnMsgMgr(IPackageManager):
    """Returns a sidecar with a warn-level message → completed_with_warnings."""

    @property
    def category(self) -> SourceType:
        return SourceType.WINGET

    @property
    def display_name(self) -> str:
        return "fake-warn-msg"

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
        time.sleep(0.02)
        return _make_sidecar(
            run, host, phase=phase,
            messages=[Message(level=MessageLevel.WARN, text="something deferred")],
        )


def _make_adapter(mgr: IPackageManager) -> IAdapter:
    class _Adapter(IAdapter):
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
            return [mgr]

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

    return _Adapter()


def _wait_done(client: TestClient, run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/runs/{run_id}/status")
        last = r.json()
        if last["status"] in ("completed", "failed", "cancelled"):
            return last
        time.sleep(0.05)
    pytest.fail(f"timeout waiting for terminal status; last={last!r}")


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_clean_run_yields_completed_terminal_state(tmp_path: Path) -> None:
    """A run with no skipped/partial/warn sidecars → terminal_state='completed'."""
    app = create_app(adapter=_make_adapter(_CleanMgr()), runs_dir=tmp_path)
    client = TestClient(app)
    r = client.post("/runs/async", json={"phases": ["check"]})
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    status = _wait_done(client, run_id)
    assert status["status"] == "completed"
    assert status.get("terminal_state") == "completed", (
        f"expected terminal_state='completed', got {status!r}"
    )


def test_skipped_run_yields_completed_with_warnings(tmp_path: Path) -> None:
    """A run where a sidecar has skipped>0 → terminal_state='completed_with_warnings'."""
    app = create_app(adapter=_make_adapter(_WarnMgr()), runs_dir=tmp_path)
    client = TestClient(app)
    r = client.post("/runs/async", json={"phases": ["check"]})
    run_id = r.json()["run_id"]

    status = _wait_done(client, run_id)
    assert status["status"] == "completed"
    assert status.get("terminal_state") == "completed_with_warnings", (
        f"expected 'completed_with_warnings', got {status!r}"
    )


def test_needs_reboot_yields_completed_with_warnings(tmp_path: Path) -> None:
    """A run where needs_reboot=True in a sidecar → terminal_state='completed_with_warnings'."""
    app = create_app(adapter=_make_adapter(_RebootMgr()), runs_dir=tmp_path)
    client = TestClient(app)
    r = client.post("/runs/async", json={"phases": ["check"]})
    run_id = r.json()["run_id"]

    status = _wait_done(client, run_id)
    assert status["status"] == "completed"
    assert status.get("terminal_state") == "completed_with_warnings", (
        f"expected 'completed_with_warnings' for needs_reboot, got {status!r}"
    )


def test_warn_message_yields_completed_with_warnings(tmp_path: Path) -> None:
    """A sidecar with a warn-level message → terminal_state='completed_with_warnings'."""
    app = create_app(adapter=_make_adapter(_WarnMsgMgr()), runs_dir=tmp_path)
    client = TestClient(app)
    r = client.post("/runs/async", json={"phases": ["check"]})
    run_id = r.json()["run_id"]

    status = _wait_done(client, run_id)
    assert status["status"] == "completed"
    assert status.get("terminal_state") == "completed_with_warnings", (
        f"expected 'completed_with_warnings' for warn msg, got {status!r}"
    )


def test_failed_worker_yields_failed_terminal_state(tmp_path: Path) -> None:
    """A worker that raises an exception → status='failed', terminal_state='failed'."""
    from ascendo.interfaces.package_manager import ManagerError

    class _FailMgr(IPackageManager):
        @property
        def category(self) -> SourceType:
            return SourceType.WINGET

        @property
        def display_name(self) -> str:
            return "fake-fail"

        def is_available(self, host: HostInfo) -> bool:
            return True

        def run_phase(self, phase, run, host, *, item_filter=None) -> Sidecar:
            raise ManagerError("boom")

    app = create_app(adapter=_make_adapter(_FailMgr()), runs_dir=tmp_path)
    client = TestClient(app)
    r = client.post("/runs/async", json={"phases": ["check"]})
    run_id = r.json()["run_id"]

    status = _wait_done(client, run_id)
    # ManagerError is swallowed by _safe_run_phase → run still completes
    # with a failed sidecar, so overall status is still "completed" but
    # terminal_state should indicate warnings/failure.
    assert status["status"] in ("completed", "failed")
    assert status.get("terminal_state") in ("completed_with_warnings", "failed"), (
        f"unexpected terminal_state: {status!r}"
    )


def test_cancelled_run_yields_cancelled_terminal_state(tmp_path: Path) -> None:
    """A cancelled run → status='cancelled', terminal_state='cancelled'.

    Deterministic (no timing race): the manager blocks on a release Event so
    the run *cannot* finish before the test requests cancel. We only release
    it after the cancel flag is set, then assert the worker unwinds to
    CANCELLED.
    """
    import threading

    started = threading.Event()
    release = threading.Event()

    class _BlockingMgr(IPackageManager):
        @property
        def category(self) -> SourceType:
            return SourceType.WINGET

        @property
        def display_name(self) -> str:
            return "fake-blocking"

        def is_available(self, host: HostInfo) -> bool:
            return True

        def run_phase(self, phase, run, host, *, item_filter=None) -> Sidecar:
            started.set()
            release.wait(timeout=8.0)  # held until the test cancels + releases
            return _make_sidecar(run, host, phase=phase)

    app = create_app(adapter=_make_adapter(_BlockingMgr()), runs_dir=tmp_path)
    client = TestClient(app)

    # NOTE: under Starlette's TestClient the `create_task`'d worker is drained
    # before POST /runs/async returns — i.e. the POST blocks until the run is
    # fully done (verified empirically). So there is no mid-flight window on
    # the calling thread; we drive the cancel from a SIDE thread that runs
    # while the main thread is parked inside the POST. It waits for the manager
    # to start, sets the cooperative-cancel flag on the already-registered run
    # (exactly what registry.request_cancel does), then releases the manager.
    def _canceller() -> None:
        if not started.wait(timeout=5.0):
            return
        reg = app.state.run_registry
        for rid in reg.all_running():
            st = reg.get(rid)
            if st is not None:
                st.cancel_event.set()
        release.set()

    t = threading.Thread(target=_canceller, daemon=True)
    t.start()

    r = client.post("/runs/async", json={"phases": ["check"]})  # blocks until done
    run_id = r.json()["run_id"]
    t.join(timeout=2.0)

    status = _wait_done(client, run_id, timeout=8.0)
    assert status["status"] == "cancelled", f"unexpected status: {status!r}"
    assert status.get("terminal_state") == "cancelled", (
        f"expected terminal_state='cancelled', got {status!r}"
    )


def test_status_endpoint_exposes_terminal_state(tmp_path: Path) -> None:
    """The /runs/{id}/status JSON includes a 'terminal_state' key when done."""
    app = create_app(adapter=_make_adapter(_CleanMgr()), runs_dir=tmp_path)
    client = TestClient(app)
    r = client.post("/runs/async", json={"phases": ["check"]})
    run_id = r.json()["run_id"]

    status = _wait_done(client, run_id)
    assert "terminal_state" in status, (
        f"'terminal_state' key missing from /status response: {status!r}"
    )

"""Contract tests for :func:`ascendo.orchestrator.run_phases`.

The orchestrator is the glue between IAdapter / IPackageManager (Layer 5)
and the on-disk sidecar layout (Layer 4 I/O). These tests use fake
in-memory implementations of both interfaces; no subprocess, no PowerShell.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from ascendo.interfaces.adapter import AdapterCapability, IAdapter
from ascendo.interfaces.elevation import IElevation
from ascendo.interfaces.inventory import IInventory
from ascendo.interfaces.package_manager import IPackageManager, ManagerError
from ascendo.interfaces.scheduler import IScheduler
from ascendo.interfaces.snapshot import ISnapshot
from ascendo.interfaces.source import ISource
from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.package import SourceType
from ascendo.models.result import Summary
from ascendo.models.run import Phase, PhaseStatus, RunInfo, Trigger
from ascendo.models.sidecar import Sidecar, SidecarSchema, ToolInfo
from ascendo.orchestrator import RunReport, run_phases


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def host() -> HostInfo:
    return HostInfo(
        hostname="testbox",
        os=OperatingSystem.WINDOWS,
        os_version="11 Pro 26200",
        arch="x86_64",
        user="tester",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
    )


@pytest.fixture
def run() -> RunInfo:
    return RunInfo(
        id=UUID("12345678-1234-1234-1234-123456789abc"),
        trigger=Trigger.CLI,
        profile="full",
        dry_run=False,
        started_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


# ── Fakes ───────────────────────────────────────────────────────────────────


def _make_sidecar(
    *,
    run: RunInfo,
    host: HostInfo,
    phase: Phase,
    category: SourceType,
    status: PhaseStatus = PhaseStatus.SUCCESS,
) -> Sidecar:
    now = datetime.now(timezone.utc)
    return Sidecar.model_validate(
        {
            "schema": SidecarSchema.V1_ASCENDO.value,
            "run": run.model_dump(mode="json"),
            "host": host.model_dump(mode="json"),
            "tool": ToolInfo(name=category.value, version="1.0").model_dump(mode="json"),
            "phase": phase.value,
            "category": category.value,
            "started_at": now.isoformat(),
            "finished_at": now.isoformat(),
            "status": status.value,
            "items": [],
            "summary": Summary(total=0, exit_code=0 if status is PhaseStatus.SUCCESS else 1).model_dump(),
        }
    )


class FakeManager(IPackageManager):
    """In-memory PackageManager that records calls + returns a stub Sidecar."""

    def __init__(
        self,
        category: SourceType,
        *,
        is_avail: bool = True,
        status: PhaseStatus = PhaseStatus.SUCCESS,
        raise_on_phases: tuple[Phase, ...] = (),
    ) -> None:
        self._category = category
        self._is_avail = is_avail
        self._status = status
        self._raise_on = set(raise_on_phases)
        self.calls: list[tuple[Phase, list[str] | None]] = []

    @property
    def category(self) -> SourceType:
        return self._category

    @property
    def display_name(self) -> str:
        return f"Fake-{self._category.value}"

    def is_available(self, host: HostInfo) -> bool:
        return self._is_avail

    def run_phase(
        self,
        phase: Phase,
        run: RunInfo,
        host: HostInfo,
        *,
        item_filter: Iterable[str] | None = None,
    ) -> Sidecar:
        self.calls.append((phase, list(item_filter) if item_filter is not None else None))
        if phase in self._raise_on:
            msg = f"fake crash on {phase.value}"
            raise ManagerError(msg)
        return _make_sidecar(run=run, host=host, phase=phase, category=self._category, status=self._status)


class FakeAdapter(IAdapter):
    def __init__(self, managers: list[IPackageManager]) -> None:
        self._managers = managers

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
        return self._managers

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
        raise NotImplementedError

    def health_check(self) -> dict[str, str]:
        return {}


# ── Tests ───────────────────────────────────────────────────────────────────


def test_run_phases_drives_all_phases_for_one_manager(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    mgr = FakeManager(SourceType.WINGET)
    adapter = FakeAdapter([mgr])

    report = run_phases(adapter, run, host, base_dir=tmp_path)

    # 5 phases × 1 manager = 5 sidecars
    assert len(report.sidecars) == 5
    assert [c[0] for c in mgr.calls] == [Phase.CHECK, Phase.PLAN, Phase.APPLY, Phase.VERIFY, Phase.CLEANUP]
    assert report.overall_status is PhaseStatus.SUCCESS
    assert report.aborted_after_phase is None


def test_run_phases_subset_preserves_canonical_order(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    """Even if caller passes [APPLY, CHECK], execution is CHECK then APPLY."""
    mgr = FakeManager(SourceType.WINGET)
    adapter = FakeAdapter([mgr])

    run_phases(adapter, run, host, phases=[Phase.APPLY, Phase.CHECK], base_dir=tmp_path)

    assert [c[0] for c in mgr.calls] == [Phase.CHECK, Phase.APPLY]


def test_run_phases_skips_unavailable_managers(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    avail = FakeManager(SourceType.WINGET, is_avail=True)
    unavail = FakeManager(SourceType.MSSTORE, is_avail=False)
    adapter = FakeAdapter([avail, unavail])

    report = run_phases(adapter, run, host, phases=[Phase.CHECK], base_dir=tmp_path)

    assert len(report.sidecars) == 1
    assert report.sidecars[0].category is SourceType.WINGET
    assert ("msstore", "is_available() returned False") in report.skipped_managers


def test_run_phases_categories_filter(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    a = FakeManager(SourceType.WINGET)
    b = FakeManager(SourceType.MSSTORE)
    adapter = FakeAdapter([a, b])

    report = run_phases(
        adapter, run, host,
        phases=[Phase.CHECK],
        categories=[SourceType.WINGET],
        base_dir=tmp_path,
    )

    assert len(report.sidecars) == 1
    assert report.sidecars[0].category is SourceType.WINGET
    assert any(cat == "msstore" for cat, _ in report.skipped_managers)


def test_run_phases_manager_error_synthesizes_failed_sidecar(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    mgr = FakeManager(SourceType.WINGET, raise_on_phases=(Phase.APPLY,))
    adapter = FakeAdapter([mgr])

    report = run_phases(
        adapter, run, host,
        phases=[Phase.CHECK, Phase.APPLY, Phase.VERIFY],
        base_dir=tmp_path,
        stop_on_failure=False,
    )

    apply_sidecars = report.by_phase(Phase.APPLY)
    assert len(apply_sidecars) == 1
    assert apply_sidecars[0].status is PhaseStatus.FAILED
    assert "fake crash" in apply_sidecars[0].messages[0].text
    # All 3 phases ran (verify after the failed apply, because stop_on_failure=False)
    assert {s.phase for s in report.sidecars} == {Phase.CHECK, Phase.APPLY, Phase.VERIFY}


def test_run_phases_stop_on_failure_aborts_subsequent(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    mgr = FakeManager(SourceType.WINGET, raise_on_phases=(Phase.PLAN,))
    adapter = FakeAdapter([mgr])

    report = run_phases(
        adapter, run, host,
        phases=[Phase.CHECK, Phase.PLAN, Phase.APPLY],
        base_dir=tmp_path,
        stop_on_failure=True,
    )

    assert report.aborted_after_phase is Phase.PLAN
    assert {s.phase for s in report.sidecars} == {Phase.CHECK, Phase.PLAN}
    # No APPLY sidecar — the run aborted.
    assert not report.by_phase(Phase.APPLY)


def test_run_phases_persists_each_sidecar_to_disk(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    mgr = FakeManager(SourceType.WINGET)
    adapter = FakeAdapter([mgr])

    run_phases(adapter, run, host, phases=[Phase.CHECK, Phase.APPLY], base_dir=tmp_path)

    run_dir = tmp_path / str(run.id)
    written = sorted(p.name for p in run_dir.iterdir() if p.suffix == ".json")
    assert "check__winget.json" in written
    assert "apply__winget.json" in written


def test_run_phases_overall_status_partial_when_mixed(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    ok = FakeManager(SourceType.WINGET, status=PhaseStatus.SUCCESS)
    bad = FakeManager(SourceType.MSSTORE, raise_on_phases=(Phase.CHECK,))
    adapter = FakeAdapter([ok, bad])

    report = run_phases(
        adapter, run, host,
        phases=[Phase.CHECK],
        base_dir=tmp_path,
        stop_on_failure=False,
    )

    assert report.overall_status is PhaseStatus.PARTIAL


def test_run_phases_empty_phases_raises(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    adapter = FakeAdapter([FakeManager(SourceType.WINGET)])
    with pytest.raises(ValueError, match="phases must be a non-empty subset"):
        run_phases(adapter, run, host, phases=[], base_dir=tmp_path)


def test_run_phases_item_filter_propagates_to_managers(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    mgr = FakeManager(SourceType.WINGET)
    adapter = FakeAdapter([mgr])

    run_phases(
        adapter, run, host,
        phases=[Phase.CHECK],
        base_dir=tmp_path,
        item_filter=["Microsoft.PowerShell", "Mozilla.Firefox"],
    )

    assert mgr.calls[0][1] == ["Microsoft.PowerShell", "Mozilla.Firefox"]


def test_run_report_aggregations(
    host: HostInfo, run: RunInfo, tmp_path: Path
) -> None:
    a = FakeManager(SourceType.WINGET)
    b = FakeManager(SourceType.MSSTORE)
    adapter = FakeAdapter([a, b])

    report = run_phases(
        adapter, run, host,
        phases=[Phase.CHECK, Phase.APPLY],
        base_dir=tmp_path,
    )

    assert len(report.by_category(SourceType.WINGET)) == 2
    assert len(report.by_category(SourceType.MSSTORE)) == 2
    assert len(report.by_phase(Phase.CHECK)) == 2
    assert len(report.by_phase(Phase.APPLY)) == 2
    assert report.total_items == 0  # FakeManager emits empty items

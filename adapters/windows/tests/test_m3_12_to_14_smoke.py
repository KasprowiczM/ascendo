"""Smoke tests for M3.12 (snapshot), M3.13 (scheduler), M3.14 (elevation).

These verify the contracts and basic dispatch — full end-to-end with real
VSS / Task Scheduler / UAC must run on a real Windows box (M3.16).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ascendo_windows.managers.snapshot   import WindowsSnapshot
from ascendo_windows.managers.scheduler  import WindowsScheduler
from ascendo_windows.managers.elevation  import WindowsElevation
from ascendo.interfaces.elevation        import ElevationDenied
from ascendo.interfaces.scheduler        import ScheduleSpec
from ascendo.models.host                 import HostInfo, OperatingSystem


def _make_host(os_: OperatingSystem) -> HostInfo:
    return HostInfo(
        hostname="testbox", os=os_, os_version="11",
        arch="x86_64", user="tester", is_elevated=False,
    )


# ── M3.12 snapshot ──────────────────────────────────────────────────────


def test_snapshot_backend_is_vss(tmp_path: Path) -> None:
    s = WindowsSnapshot(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert s.backend == "vss"


def test_snapshot_unavailable_on_non_windows(tmp_path: Path) -> None:
    s = WindowsSnapshot(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert s.is_available(_make_host(OperatingSystem.LINUX_UBUNTU)) is False
    assert s.is_available(_make_host(OperatingSystem.MACOS)) is False


def test_snapshot_available_on_windows_with_vssadmin(tmp_path: Path) -> None:
    s = WindowsSnapshot(scripts_dir=tmp_path, lib_dir=tmp_path)
    with patch("ascendo_windows.managers.snapshot.shutil.which", return_value="C:/Windows/System32/vssadmin.exe"):
        assert s.is_available(_make_host(OperatingSystem.WINDOWS)) is True


# ── M3.13 scheduler ─────────────────────────────────────────────────────


def test_scheduler_backend_is_task_scheduler(tmp_path: Path) -> None:
    sched = WindowsScheduler(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert sched.backend == "task_scheduler"


def test_scheduler_unavailable_on_non_windows(tmp_path: Path) -> None:
    sched = WindowsScheduler(scripts_dir=tmp_path, lib_dir=tmp_path)
    assert sched.is_available(_make_host(OperatingSystem.LINUX_UBUNTU)) is False


def test_scheduler_available_on_windows_with_schtasks(tmp_path: Path) -> None:
    sched = WindowsScheduler(scripts_dir=tmp_path, lib_dir=tmp_path)
    with patch("ascendo_windows.managers.scheduler.shutil.which", return_value="C:/Windows/System32/schtasks.exe"):
        assert sched.is_available(_make_host(OperatingSystem.WINDOWS)) is True


def test_scheduler_install_passes_payload_to_script(tmp_path: Path) -> None:
    sched = WindowsScheduler(scripts_dir=tmp_path, lib_dir=tmp_path)
    spec = ScheduleSpec(
        name="ascendo-weekly",
        expression="WEEKLY SUN 03:00",
        profile="full",
        enabled=True,
        description="weekly Ascendo run",
    )
    with patch.object(sched, "_resolve_pwsh", return_value="pwsh"):
        with patch("ascendo_windows.managers.scheduler.subprocess.run") as run_mock:
            run_mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
            sched.install(_make_host(OperatingSystem.WINDOWS), spec)
            assert run_mock.called
            argv = run_mock.call_args[0][0]
            assert "-Action" in argv
            assert "install" in argv


# ── M3.14 elevation ─────────────────────────────────────────────────────


def test_elevation_allowlist_is_basename_only_lowercase() -> None:
    e = WindowsElevation()
    e.register_allowlist([
        "winget.exe",
        "C:\\Windows\\System32\\winget.exe",
        "/usr/bin/apt-get",
        "WINGET.EXE",  # case mixing — should normalise
    ])
    # All four resolve to two distinct basenames after normalisation.
    assert "winget.exe" in e._allowlist
    assert "apt-get" in e._allowlist
    assert "WINGET.EXE" not in e._allowlist  # uppercase rejected


def test_elevation_denies_command_not_in_allowlist() -> None:
    e = WindowsElevation()
    e.register_allowlist(["winget.exe"])
    host = _make_host(OperatingSystem.WINDOWS)
    with pytest.raises(ElevationDenied):
        e.run(host, ["evil.exe", "rm", "-rf", "/"])


def test_elevation_denies_on_non_windows() -> None:
    e = WindowsElevation()
    e.register_allowlist(["winget.exe"])
    host = _make_host(OperatingSystem.LINUX_UBUNTU)
    with pytest.raises(ElevationDenied):
        e.run(host, ["winget.exe", "list"])


def test_elevation_denies_empty_argv() -> None:
    e = WindowsElevation()
    host = _make_host(OperatingSystem.WINDOWS)
    with pytest.raises(ElevationDenied):
        e.run(host, [])


# ── Adapter wiring ──────────────────────────────────────────────────────


def test_windows_adapter_now_exposes_snapshot_scheduler_elevation() -> None:
    """All three M3.12/13/14 capabilities must surface from the adapter."""
    from ascendo_windows.adapter import WindowsAdapter
    from ascendo.interfaces import AdapterCapability

    adapter = WindowsAdapter()
    caps = adapter.capabilities
    assert AdapterCapability.SNAPSHOTS  in caps
    assert AdapterCapability.SCHEDULING in caps
    assert AdapterCapability.ELEVATION  in caps
    assert adapter.snapshot()  is not None
    assert adapter.scheduler() is not None
    assert adapter.elevation() is not None

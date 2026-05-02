"""Smoke tests for :class:`WindowsUpdateManager`.

Mirrors :mod:`test_winget_manager_smoke` - the tests are mock-based and do
NOT spawn pwsh or call PSWindowsUpdate. They verify:

* argv construction matches the IPC contract (presence/absence of
  ``-DryRun``, ``-ItemFilter`` formatting, script dispatch),
* the sidecar produced by the script is parsed cleanly,
* :class:`ManagerError` surfaces for unsupported phases,
* ``is_available`` correctly gates non-Windows hosts AND PSWindowsUpdate
  presence on Windows hosts (without spawning a real pwsh in either case),
* ``WindowsAdapter.package_managers`` includes the new manager next to
  the existing :class:`WingetManager`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo
from ascendo.models.run import Phase, RunInfo, Trigger
from ascendo_windows.adapter import WindowsAdapter
from ascendo_windows.managers.windows_update import WindowsUpdateManager
from ascendo_windows.managers.winget import WingetManager


# ── Helpers ───────────────────────────────────────────────────────────


def _make_manager(scripts_dir: Path, lib_dir: Path) -> WindowsUpdateManager:
    """Construct a WindowsUpdateManager pinned to a fake pwsh path so tests
    don't depend on having pwsh installed in the test environment."""
    return WindowsUpdateManager(
        scripts_dir=scripts_dir,
        lib_dir=lib_dir,
        pwsh_path="/usr/bin/true",  # any path works - subprocess is mocked
        timeout_sec=30,
    )


def _run_completed(
    *,
    output_dir_arg: str,
    run_id: str,
    payload: dict[str, Any] | None,
    returncode: int,
    phase_value: str = "check",
) -> subprocess.CompletedProcess[str]:
    """Build a fake CompletedProcess; optionally drop a sidecar on disk.

    Mirrors what a real PowerShell script would do: emits its sidecar at
    ``<OutputDir>/<RunId>/<phase>__windows_update.json`` then exits.
    """
    if payload is not None:
        target = (
            Path(output_dir_arg)
            / run_id
            / f"{phase_value}__windows_update.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.CompletedProcess(
        args=["pwsh", "-File", "fake.ps1"],
        returncode=returncode,
        stdout="ok\n",
        stderr="",
    )


def _extract_output_dir(argv: list[str]) -> str:
    """Find the ``-OutputDir`` value in a built argv list."""
    idx = argv.index("-OutputDir")
    return argv[idx + 1]


# ── Tests: run_phase ──────────────────────────────────────────────────


def test_run_phase_check_returns_pending_updates_sidecar(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
    windows_update_sidecar_payload: dict[str, Any],
) -> None:
    """Happy path: the (mocked) check script writes a 2-KB sidecar; we
    parse it back as the expected SourceType.WINDOWS_UPDATE entries."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Verify argv shape against the WindowsUpdateManager IPC contract.
        assert argv[0] == "/usr/bin/true"
        assert "-NoProfile" in argv
        assert "-NonInteractive" in argv
        assert "-File" in argv
        assert str(fake_scripts_dir / "windows_update" / "check.ps1") in argv
        assert "-RunId" in argv and str(run_info.id) in argv
        assert "-Trigger" in argv and run_info.trigger.value in argv
        assert "-Profile" in argv and run_info.profile in argv
        # run_info.dry_run is False -> -DryRun must NOT appear.
        assert "-DryRun" not in argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=windows_update_sidecar_payload,
            returncode=0,
        )

    with patch(
        "ascendo_windows.managers.windows_update.subprocess.run",
        side_effect=fake_run,
    ):
        sidecar = manager.run_phase(Phase.CHECK, run_info, windows_host)

    assert sidecar.category.value == "windows_update"
    assert sidecar.phase is Phase.CHECK
    assert sidecar.status.value == "success"
    assert len(sidecar.items) == 2
    assert sidecar.items[0].id == "KB5034441"
    assert sidecar.items[1].id == "KB5037997"
    # Each item's category should agree with the sidecar category.
    for item in sidecar.items:
        assert item.category.value == "windows_update"


def test_run_phase_apply_with_dry_run_includes_dryrun_flag_in_argv(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
    windows_update_sidecar_payload: dict[str, Any],
    run_info: RunInfo,
) -> None:
    """RunInfo.dry_run=True must include ``-DryRun`` switch token in argv."""
    dry_run = run_info.model_copy(update={"dry_run": True})
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    captured: dict[str, Any] = {}

    payload = dict(windows_update_sidecar_payload)
    payload["run"] = json.loads(dry_run.model_dump_json())
    payload["phase"] = Phase.APPLY.value

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(dry_run.id),
            payload=payload,
            returncode=0,
            phase_value="apply",
        )

    with patch(
        "ascendo_windows.managers.windows_update.subprocess.run",
        side_effect=fake_run,
    ):
        manager.run_phase(Phase.APPLY, dry_run, windows_host)

    argv = captured["argv"]
    assert "-DryRun" in argv
    assert str(fake_scripts_dir / "windows_update" / "apply.ps1") in argv


def test_run_phase_apply_without_dry_run_omits_dryrun_flag(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
    windows_update_sidecar_payload: dict[str, Any],
    run_info: RunInfo,
) -> None:
    """RunInfo.dry_run=False (default) must NOT add ``-DryRun`` to argv."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    captured: dict[str, Any] = {}

    payload = dict(windows_update_sidecar_payload)
    payload["phase"] = Phase.APPLY.value

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=payload,
            returncode=0,
            phase_value="apply",
        )

    with patch(
        "ascendo_windows.managers.windows_update.subprocess.run",
        side_effect=fake_run,
    ):
        manager.run_phase(Phase.APPLY, run_info, windows_host)

    argv = captured["argv"]
    assert "-DryRun" not in argv


def test_run_phase_unsupported_phase_raises(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
) -> None:
    """If a phase has no script mapping, ManagerError is raised up front."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    with patch.object(type(manager), "SCRIPT_BY_PHASE", {}):
        with pytest.raises(ManagerError) as excinfo:
            manager.run_phase(Phase.APPLY, run_info, windows_host)

    msg = str(excinfo.value)
    assert "does not yet support" in msg
    assert "apply" in msg


# ── Tests: is_available ───────────────────────────────────────────────


def test_is_available_returns_false_on_non_windows(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    linux_host: HostInfo,
) -> None:
    """Linux host -> manager reports unavailable, no pwsh probe runs."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    # Even with subprocess.run mocked to "True", Linux host -> False.
    with patch(
        "ascendo_windows.managers.windows_update.subprocess.run"
    ) as run_mock:
        assert manager.is_available(linux_host) is False
        # The probe must not have been invoked: we short-circuited on os.
        run_mock.assert_not_called()


def test_is_available_returns_true_when_psmodule_present(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
) -> None:
    """Windows host + pwsh probe returns 'True' -> True."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Sanity: probe argv asks for the PSWindowsUpdate module.
        assert "-Command" in argv
        cmd_idx = argv.index("-Command")
        assert "PSWindowsUpdate" in argv[cmd_idx + 1]
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="True\n", stderr=""
        )

    with patch(
        "ascendo_windows.managers.windows_update.subprocess.run",
        side_effect=fake_run,
    ):
        assert manager.is_available(windows_host) is True
        # Cached: a second call must not re-spawn the probe.
        assert manager.is_available(windows_host) is True


def test_is_available_returns_false_when_psmodule_missing(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
) -> None:
    """Windows host + pwsh probe returns 'False' -> False."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="False\n", stderr=""
        )

    with patch(
        "ascendo_windows.managers.windows_update.subprocess.run",
        side_effect=fake_run,
    ):
        assert manager.is_available(windows_host) is False


# ── Tests: adapter wiring ─────────────────────────────────────────────


def test_adapter_package_managers_includes_windows_update(
    windows_host: HostInfo,
) -> None:
    """WindowsAdapter.package_managers() must surface every manager.

    Post-M3.15 the adapter declares 4 managers:
    WingetManager, MSStoreManager, ArpManager, WindowsUpdateManager.
    Order must remain deterministic: winget first, windows_update last
    (tests downstream depend on the bookend pair).
    """
    from ascendo_windows.managers.msstore import MSStoreManager
    from ascendo_windows.managers.arp import ArpManager

    adapter = WindowsAdapter()
    managers = adapter.package_managers(windows_host)

    assert len(managers) == 4
    assert isinstance(managers[0], WingetManager)
    assert isinstance(managers[-1], WindowsUpdateManager)
    types_present = {type(m).__name__ for m in managers}
    assert {
        "WingetManager",
        "MSStoreManager",
        "ArpManager",
        "WindowsUpdateManager",
    } <= types_present

    # And the new manager reports the expected SourceType.
    wu = managers[-1]
    assert wu.category.value == "windows_update"
    assert wu.display_name == "Windows Update (PSWindowsUpdate)"


def test_run_phase_passes_item_filter_as_comma_list(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
    windows_update_sidecar_payload: dict[str, Any],
) -> None:
    """item_filter must be serialised as a single comma-joined argv token."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=windows_update_sidecar_payload,
            returncode=0,
        )

    with patch(
        "ascendo_windows.managers.windows_update.subprocess.run",
        side_effect=fake_run,
    ):
        manager.run_phase(
            Phase.CHECK,
            run_info,
            windows_host,
            item_filter=["KB5034441", "  KB5037997  ", "", "  "],
        )

    argv = captured["argv"]
    assert "-ItemFilter" in argv
    idx = argv.index("-ItemFilter")
    assert argv[idx + 1] == "KB5034441,KB5037997"

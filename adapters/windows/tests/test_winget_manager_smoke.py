"""Smoke tests for :class:`WingetManager`.

These tests are deliberately mock-based - they do NOT actually spawn
PowerShell or call winget. They verify the Python-side IPC contract:

* argv construction is correct,
* the sidecar that the script (here, our mock) emits is parsed back
  cleanly,
* failures (missing sidecar, exit != 0 on missing sidecar, timeout)
  surface as :class:`ManagerError`,
* ``is_available`` correctly gates non-Windows hosts.

The actual pwsh + winget integration is exercised by Pester tests on a
real Windows machine.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ascendo.interfaces.package_manager import ManagerError
from ascendo.models.host import HostInfo
from ascendo.models.run import Phase, RunInfo
from ascendo_windows.managers.winget import WingetManager


# ── Helpers ───────────────────────────────────────────────────────────


def _make_manager(scripts_dir: Path, lib_dir: Path) -> WingetManager:
    """Construct a WingetManager pinned to a fake pwsh path so tests
    don't depend on having pwsh installed in the test environment."""
    return WingetManager(
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
    ``<OutputDir>/<RunId>/<phase>__winget.json`` then exits with
    ``returncode``.
    """
    if payload is not None:
        target = Path(output_dir_arg) / run_id / f"{phase_value}__winget.json"
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


# ── Tests ─────────────────────────────────────────────────────────────


def test_run_phase_success_returns_parsed_sidecar(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
    check_sidecar_payload: dict[str, Any],
) -> None:
    """Happy path: script emits a valid sidecar; we get it back."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Sanity-check argv shape.
        assert argv[0] == "/usr/bin/true"
        assert "-NoProfile" in argv
        assert "-NonInteractive" in argv
        assert "-File" in argv
        assert str(fake_scripts_dir / "winget" / "check.ps1") in argv
        assert "-RunId" in argv and str(run_info.id) in argv
        assert "-Trigger" in argv and run_info.trigger.value in argv
        assert "-Profile" in argv and run_info.profile in argv
        # run_info.dry_run is False, so -DryRun must NOT appear (switch idiom).
        assert "-DryRun" not in argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=check_sidecar_payload,
            returncode=0,
        )

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        sidecar = manager.run_phase(Phase.CHECK, run_info, windows_host)

    assert sidecar.category.value == "winget"
    assert sidecar.phase is Phase.CHECK
    assert sidecar.status.value == "success"
    assert len(sidecar.items) == 2
    assert sidecar.items[0].id == "Microsoft.PowerShell"
    assert sidecar.items[1].id == "Microsoft.VisualStudioCode"


def test_run_phase_passes_item_filter_as_comma_list(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
    check_sidecar_payload: dict[str, Any],
) -> None:
    """item_filter must be serialized as a single comma-joined argv token."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=check_sidecar_payload,
            returncode=0,
        )

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        manager.run_phase(
            Phase.CHECK,
            run_info,
            windows_host,
            item_filter=["Microsoft.PowerShell", "  Microsoft.VisualStudioCode  ", "", "  "],
        )

    argv = captured["argv"]
    assert "-ItemFilter" in argv
    idx = argv.index("-ItemFilter")
    # Whitespace stripped, empties dropped.
    assert argv[idx + 1] == "Microsoft.PowerShell,Microsoft.VisualStudioCode"


def test_run_phase_dryrun_passes_true(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
    check_sidecar_payload: dict[str, Any],
    run_info: RunInfo,
) -> None:
    """RunInfo.dry_run=True must include the ``-DryRun`` switch token in argv."""
    dry_run = run_info.model_copy(update={"dry_run": True})
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    captured: dict[str, Any] = {}

    # Re-build payload's run block so the sidecar matches the new RunInfo.
    payload = check_sidecar_payload.copy()
    payload["run"] = json.loads(dry_run.model_dump_json())

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(dry_run.id),
            payload=payload,
            returncode=0,
        )

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        manager.run_phase(Phase.CHECK, dry_run, windows_host)

    argv = captured["argv"]
    # [switch] convention: -DryRun present means True. No value follows it
    # (the next argv element is whatever comes next, e.g. '-OutputDir').
    assert "-DryRun" in argv


def test_run_phase_missing_sidecar_with_nonzero_exit_raises(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
) -> None:
    """Script crashes (exit != 0) and emits no sidecar -> ManagerError."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="", stderr="boom: native crash\n"
        )

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        with pytest.raises(ManagerError) as excinfo:
            manager.run_phase(Phase.CHECK, run_info, windows_host)

    msg = str(excinfo.value)
    assert "no sidecar" in msg
    assert "boom: native crash" in msg
    assert "exit code:     1" in msg


def test_run_phase_missing_sidecar_with_zero_exit_raises(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
) -> None:
    """Script lies about success (exit 0 + no sidecar) -> still ManagerError."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        with pytest.raises(ManagerError) as excinfo:
            manager.run_phase(Phase.CHECK, run_info, windows_host)

    assert "no sidecar" in str(excinfo.value)


def test_run_phase_timeout_raises_manager_error(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
) -> None:
    """subprocess timeout -> ManagerError chained from TimeoutExpired."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        with pytest.raises(ManagerError) as excinfo:
            manager.run_phase(Phase.CHECK, run_info, windows_host)

    assert "timed out" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, subprocess.TimeoutExpired)


def test_run_phase_unsupported_phase_raises(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
) -> None:
    """If a phase has no script mapping, ManagerError is raised up front.

    All 5 phases are wired post-M3.7, so this test patches SCRIPT_BY_PHASE
    to empty for the duration of the assertion to exercise the error path.
    """
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    with patch.object(type(manager), "SCRIPT_BY_PHASE", {}):
        with pytest.raises(ManagerError) as excinfo:
            manager.run_phase(Phase.APPLY, run_info, windows_host)

    msg = str(excinfo.value)
    assert "does not yet support" in msg
    assert "apply" in msg


@pytest.mark.parametrize(
    ("phase", "script_name"),
    [
        (Phase.CHECK, "check.ps1"),
        (Phase.PLAN, "plan.ps1"),
        (Phase.APPLY, "apply.ps1"),
        (Phase.VERIFY, "verify.ps1"),
        (Phase.CLEANUP, "cleanup.ps1"),
    ],
)
def test_run_phase_dispatches_correct_script_per_phase(
    phase: Phase,
    script_name: str,
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
    check_sidecar_payload: dict[str, Any],
) -> None:
    """All 5 phases (post-M3.6/M3.7) dispatch to the right .ps1.

    The script side emits a sidecar whose `phase` field matches the requested
    phase. We verify both the argv contains the right script path AND that
    the parsed sidecar reflects the requested phase (not just whatever the
    fixture happens to default to).
    """
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    # Adjust fixture payload to match the requested phase. Other phases
    # may legitimately have different items[] but the manager is phase-
    # agnostic — it returns whatever the script wrote.
    payload = dict(check_sidecar_payload)
    payload["phase"] = phase.value

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=payload,
            returncode=0,
            phase_value=phase.value,
        )

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        sidecar = manager.run_phase(phase, run_info, windows_host)

    assert str(fake_scripts_dir / "winget" / script_name) in captured["argv"]
    assert sidecar.phase is phase
    assert sidecar.category.value == "winget"


def test_run_phase_sidecar_with_failed_items_returns_normally(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    run_info: RunInfo,
    windows_host: HostInfo,
    check_sidecar_payload: dict[str, Any],
) -> None:
    """Per-item failures are NOT manager errors; the sidecar comes back."""
    payload = check_sidecar_payload.copy()
    # Mutate to a sidecar that reports a failure - still valid v1.
    items = [dict(check_sidecar_payload["items"][0])]
    items[0]["status"] = "failed"
    items[0]["exit_code"] = 1
    payload["items"] = items
    payload["status"] = "failed"
    payload["summary"] = {
        "total": 1,
        "success": 0,
        "up_to_date": 0,
        "failed": 1,
        "skipped": 0,
        "planned": 0,
        "partial": 0,
        "duration_ms": 100,
        "exit_code": 1,
    }

    manager = _make_manager(fake_scripts_dir, fake_lib_dir)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Script may have exited non-zero too - both should be tolerated
        # because the sidecar is the source of truth.
        return _run_completed(
            output_dir_arg=_extract_output_dir(argv),
            run_id=str(run_info.id),
            payload=payload,
            returncode=1,
        )

    with patch.object(manager, "_run_streaming", side_effect=lambda argv, **kw: fake_run(argv, capture_output=True, text=True, timeout=kw.get("timeout"), check=False)):
        sidecar = manager.run_phase(Phase.CHECK, run_info, windows_host)

    assert sidecar.status.value == "failed"
    assert sidecar.summary.failed == 1


# ── is_available ──────────────────────────────────────────────────────


def test_is_available_returns_false_on_non_windows(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    linux_host: HostInfo,
) -> None:
    """Linux host -> manager reports unavailable, no PATH probe needed."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    with patch("ascendo_windows.managers.winget.shutil.which", return_value="/usr/bin/winget"):
        # Even with a fake winget on PATH, host.os != WINDOWS -> False.
        assert manager.is_available(linux_host) is False


def test_is_available_returns_true_when_winget_on_path(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
) -> None:
    """Windows host + winget on PATH -> True."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    with patch(
        "ascendo_windows.managers.winget.shutil.which",
        return_value="C:\\winget.exe",
    ):
        assert manager.is_available(windows_host) is True


def test_is_available_returns_false_when_winget_missing(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
    windows_host: HostInfo,
) -> None:
    """Windows host but no winget on PATH -> False."""
    manager = _make_manager(fake_scripts_dir, fake_lib_dir)
    with patch("ascendo_windows.managers.winget.shutil.which", return_value=None):
        assert manager.is_available(windows_host) is False


# == pwsh resolution =========================================================


def test_resolve_pwsh_uses_override(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
) -> None:
    """Explicit pwsh_path override skips PATH lookup."""
    manager = WingetManager(
        scripts_dir=fake_scripts_dir,
        lib_dir=fake_lib_dir,
        pwsh_path="/opt/forced/pwsh",
    )
    assert manager._resolve_pwsh() == "/opt/forced/pwsh"


def test_resolve_pwsh_prefers_pwsh_over_powershell(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
) -> None:
    """pwsh.exe wins over powershell.exe when both are present."""
    manager = WingetManager(scripts_dir=fake_scripts_dir, lib_dir=fake_lib_dir)
    candidates = {
        "pwsh.exe": "C:/pwsh.exe",
        "pwsh": "C:/pwsh",
        "powershell.exe": "C:/powershell.exe",
        "powershell": "C:/powershell",
    }
    with patch(
        "ascendo_windows.managers.winget.shutil.which",
        side_effect=lambda name: candidates.get(name),
    ):
        assert manager._resolve_pwsh() == "C:/pwsh.exe"


def test_resolve_pwsh_raises_if_nothing_found(
    fake_scripts_dir: Path,
    fake_lib_dir: Path,
) -> None:
    """No pwsh / powershell on PATH -> ManagerError."""
    manager = WingetManager(scripts_dir=fake_scripts_dir, lib_dir=fake_lib_dir)
    with patch("ascendo_windows.managers.winget.shutil.which", return_value=None):
        with pytest.raises(ManagerError) as excinfo:
            manager._resolve_pwsh()
    assert "no PowerShell binary" in str(excinfo.value)

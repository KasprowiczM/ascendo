"""Smoke tests for :class:`WindowsServiceManager`.

Mocks ``subprocess.run`` so the tests don't actually invoke PowerShell
or NSSM and pass on the Linux CI runner. The IPC contract under test:

* ``status()`` parses the single-line JSON document emitted by
  ``install-service.ps1 -Action status -Json`` into a typed
  :class:`ServiceStatus`.
* mutating actions (``install``, ``uninstall``, ``start``, ``stop``,
  ``restart``) translate the script's exit code 11 into
  :class:`ServiceElevationRequired`.
* missing ``install-service.ps1`` raises :class:`ServiceManagerError`
  (script went MIA).
* a missing/garbage JSON payload yields a sensible "absent" snapshot
  rather than crashing the dashboard.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from ascendo_windows.managers.service import (
    ServiceActionResult,
    ServiceElevationRequired,
    ServiceManagerError,
    ServiceStatus,
    WindowsServiceManager,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _stage_script(tmp_path: Path) -> Path:
    """Create a stub ``bin/install-service.ps1`` so the manager finds it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "install-service.ps1"
    script.write_text("# stub install-service.ps1 for tests\n", encoding="utf-8")
    return script


def _make_manager(tmp_path: Path, **overrides: Any) -> WindowsServiceManager:
    _stage_script(tmp_path)
    return WindowsServiceManager(
        repo_root=tmp_path,
        pwsh_path="/usr/bin/true",  # subprocess is mocked anyway
        **overrides,
    )


def _completed(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["pwsh", "-File", "fake.ps1"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ── status() ──────────────────────────────────────────────────────────


def test_status_running_service(tmp_path: Path) -> None:
    """Realistic NSSM `-Json` payload for a running service."""
    manager = _make_manager(tmp_path)
    payload = {
        "installed":      True,
        "running":        True,
        "port_listening": True,
        "health":         "ok",
        "last_started":   "2026-05-02T12:34:56.7890123Z",
        "pid":            12345,
        "service_name":   "AscendoDashboard",
        "state":          "SERVICE_RUNNING",
        "port":           8765,
    }
    stdout = json.dumps(payload)

    with patch("subprocess.run", return_value=_completed(stdout=stdout)) as mock:
        status = manager.status()

    # argv shape -- status uses -Action status -Json on the script.
    argv = mock.call_args.args[0]
    assert argv[0] == "/usr/bin/true"
    assert "-Action" in argv and argv[argv.index("-Action") + 1] == "status"
    assert "-Json" in argv
    assert "-ServiceName" in argv
    assert argv[argv.index("-ServiceName") + 1] == "AscendoDashboard"

    assert isinstance(status, ServiceStatus)
    assert status.installed is True
    assert status.running is True
    assert status.port_listening is True
    assert status.health == "ok"
    assert status.last_started == "2026-05-02T12:34:56.7890123Z"
    assert status.pid == 12345
    assert status.state == "SERVICE_RUNNING"
    assert status.port == 8765


def test_status_absent_service(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    payload = {
        "installed":      False,
        "running":        False,
        "port_listening": False,
        "health":         None,
        "last_started":   None,
        "pid":            0,
        "service_name":   "AscendoDashboard",
        "state":          "ABSENT",
        "port":           8765,
    }
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))):
        status = manager.status()
    assert status.installed is False
    assert status.running is False
    assert status.state == "ABSENT"
    assert status.pid == 0


def test_status_stopped_service(tmp_path: Path) -> None:
    """Service registered but not running -- pid==0, port not listening."""
    manager = _make_manager(tmp_path)
    payload = {
        "installed":      True,
        "running":        False,
        "port_listening": False,
        "health":         None,
        "last_started":   "2026-05-01T08:00:00Z",
        "pid":            0,
        "service_name":   "AscendoDashboard",
        "state":          "SERVICE_STOPPED",
        "port":           8765,
    }
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps(payload))):
        status = manager.status()
    assert status.installed is True
    assert status.running is False
    assert status.health is None
    assert status.state == "SERVICE_STOPPED"


def test_status_garbage_stdout_returns_unknown(tmp_path: Path) -> None:
    """Defensive: non-JSON stdout shouldn't crash; we get a degraded snapshot."""
    manager = _make_manager(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="not json at all\n")):
        status = manager.status()
    assert status.installed is False
    assert status.state == "UNKNOWN"


def test_status_with_banner_lines_around_json(tmp_path: Path) -> None:
    """The parser scans line-by-line; surrounding noise must not break it."""
    manager = _make_manager(tmp_path)
    payload = {
        "installed":      True,
        "running":        True,
        "port_listening": True,
        "health":         "ok",
        "last_started":   None,
        "pid":            42,
        "service_name":   "AscendoDashboard",
        "state":          "SERVICE_RUNNING",
        "port":           8765,
    }
    stdout = (
        "[service] reading state...\n"
        + json.dumps(payload) + "\n"
        + "[service] done\n"
    )
    with patch("subprocess.run", return_value=_completed(stdout=stdout)):
        status = manager.status()
    assert status.running is True
    assert status.pid == 42


# ── mutating actions ────────────────────────────────────────────────


def test_install_success(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    stdout = (
        "[service] downloading NSSM...\n"
        "[service] SHA-256 verified (BE7B...)\n"
        "[service] Service AscendoDashboard installed; running; PID 1234; auto-start enabled\n"
    )
    with patch("subprocess.run", return_value=_completed(stdout=stdout)) as mock:
        result = manager.install()

    argv = mock.call_args.args[0]
    assert "install" in argv  # action

    assert isinstance(result, ServiceActionResult)
    assert result.ok is True
    assert result.exit_code == 0
    assert "installed" in result.output


def test_install_elevation_required_raises(tmp_path: Path) -> None:
    """Exit code 11 -> ServiceElevationRequired."""
    manager = _make_manager(tmp_path)
    stderr = (
        "[service] this action requires administrator privileges\n"
    )
    with patch("subprocess.run", return_value=_completed(returncode=11, stderr=stderr)):
        with pytest.raises(ServiceElevationRequired):
            manager.install()


def test_install_health_failure_returns_not_ok(tmp_path: Path) -> None:
    """Exit code 12 -> ok=False but no exception (caller sees the output)."""
    manager = _make_manager(tmp_path)
    stderr = (
        "[service] service started but /health did not return status=ok within 15s\n"
    )
    with patch("subprocess.run", return_value=_completed(returncode=12, stderr=stderr)):
        result = manager.install()
    assert result.ok is False
    assert result.exit_code == 12
    assert "health" in result.output.lower()


def test_uninstall_with_purge_logs_passes_flag(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="uninstalled\n")) as mock:
        result = manager.uninstall(purge_logs=True)

    argv = mock.call_args.args[0]
    assert "-PurgeLogs" in argv
    assert "uninstall" in argv
    assert result.ok is True


def test_uninstall_without_purge_logs_omits_flag(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="uninstalled\n")) as mock:
        manager.uninstall()
    argv = mock.call_args.args[0]
    assert "-PurgeLogs" not in argv


def test_start_passes_action_start(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="started\n")) as mock:
        manager.start()
    argv = mock.call_args.args[0]
    assert argv[argv.index("-Action") + 1] == "start"


def test_stop_passes_action_stop(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="stopped\n")) as mock:
        manager.stop()
    argv = mock.call_args.args[0]
    assert argv[argv.index("-Action") + 1] == "stop"


def test_restart_passes_action_restart(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="restarted\n")) as mock:
        manager.restart()
    argv = mock.call_args.args[0]
    assert argv[argv.index("-Action") + 1] == "restart"


# ── error paths ─────────────────────────────────────────────────────


def test_missing_script_raises(tmp_path: Path) -> None:
    """No bin/install-service.ps1 -> ServiceManagerError on first invoke."""
    # Don't stage the script; create the manager pointed at an empty dir.
    manager = WindowsServiceManager(
        repo_root=tmp_path,
        pwsh_path="/usr/bin/true",
    )
    with pytest.raises(ServiceManagerError, match="install-service.ps1"):
        manager.status()


def test_subprocess_timeout_raises(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pwsh", timeout=30),
    ):
        with pytest.raises(ServiceManagerError, match="timed out"):
            manager.status()


def test_subprocess_oserror_raises(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    with patch("subprocess.run", side_effect=OSError("pwsh missing")):
        with pytest.raises(ServiceManagerError, match="failed to spawn pwsh"):
            manager.status()


# ── ServiceStatus.from_payload coercion ────────────────────────────


def test_status_payload_coerces_partial_dict() -> None:
    """A minimal/sparse dict shouldn't blow up the dataclass build."""
    status = ServiceStatus.from_payload({"installed": True, "running": False})
    assert status.installed is True
    assert status.running is False
    assert status.pid == 0
    assert status.health is None
    assert status.service_name == "AscendoDashboard"


def test_status_to_dict_roundtrip() -> None:
    payload = {
        "installed":      True,
        "running":        True,
        "port_listening": True,
        "health":         "ok",
        "last_started":   "2026-05-02T00:00:00Z",
        "pid":            7,
        "service_name":   "AscendoDashboard",
        "state":          "SERVICE_RUNNING",
        "port":           8765,
    }
    s = ServiceStatus.from_payload(payload)
    assert s.to_dict() == payload

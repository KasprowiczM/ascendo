"""Pytest fixtures for adapters/windows/tests/.

Provides reusable HostInfo, RunInfo, and a Sidecar payload builder used
by the smoke test for :class:`WingetManager`. Everything here is
cross-platform - the unit suite must pass on the Linux CI runner.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from ascendo.models.host import ElevationMethod, HostInfo, OperatingSystem
from ascendo.models.run import Phase, RunInfo, Trigger


# ── Static identities ─────────────────────────────────────────────────

_FIXED_RUN_ID = UUID("12345678-1234-1234-1234-123456789abc")
_FIXED_STARTED = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
_FIXED_FINISHED = datetime(2026, 5, 1, 12, 0, 5, tzinfo=timezone.utc)


@pytest.fixture
def windows_host() -> HostInfo:
    """A minimal valid HostInfo describing a Windows 11 machine."""
    return HostInfo(
        hostname="DP5520WMK",
        os=OperatingSystem.WINDOWS,
        os_version="11 Pro 26200",
        arch="x86_64",
        user="MK",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
        locale="en-US",
    )


@pytest.fixture
def linux_host() -> HostInfo:
    """A non-Windows HostInfo for testing the negative is_available path."""
    return HostInfo(
        hostname="ubuntu-test",
        os=OperatingSystem.LINUX_UBUNTU,
        os_version="24.04 LTS",
        arch="x86_64",
        user="tester",
        is_elevated=False,
        elevation_method=ElevationMethod.NONE,
        locale="en-US",
    )


@pytest.fixture
def run_info() -> RunInfo:
    """A deterministic RunInfo with a stable UUID and timestamp."""
    return RunInfo(
        id=_FIXED_RUN_ID,
        trigger=Trigger.CLI,
        profile="full",
        dry_run=False,
        started_at=_FIXED_STARTED,
        finished_at=None,
        invocation="ascendo run --profile full",
    )


@pytest.fixture
def fake_scripts_dir(tmp_path: Path) -> Path:
    """Synthetic ``scripts/`` directory with placeholders for all 5 phases.

    The .ps1 contents are mocked away by the subprocess patch in each test;
    the files exist only so the manager's existence check passes.
    """
    d = tmp_path / "scripts" / "winget"
    d.mkdir(parents=True)
    for phase_script in ("check.ps1", "plan.ps1", "apply.ps1", "verify.ps1", "cleanup.ps1"):
        (d / phase_script).write_text(
            f"# placeholder - mocked away by tests ({phase_script})\n",
            encoding="utf-8",
        )
    return tmp_path / "scripts"


@pytest.fixture
def fake_lib_dir(tmp_path: Path) -> Path:
    """Synthetic ``lib/`` directory with one placeholder module."""
    d = tmp_path / "lib"
    d.mkdir(parents=True)
    (d / "Common.ps1").write_text("# placeholder\n", encoding="utf-8")
    return d


def _build_check_sidecar_payload(
    run: RunInfo,
    host: HostInfo,
) -> dict[str, Any]:
    """Build a minimal but valid ``ascendo/v1`` sidecar dict.

    Two items: one up-to-date, one success - exercises the parser end
    to end without invoking any winget logic.
    """
    return {
        "schema": "ascendo/v1",
        "run": json.loads(run.model_dump_json()),
        "host": json.loads(host.model_dump_json()),
        "tool": {
            "name": "winget",
            "version": "v1.28.240",
            "binary_path": "C:\\Users\\MK\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe",
        },
        "phase": Phase.CHECK.value,
        "category": "winget",
        "started_at": _FIXED_STARTED.isoformat(),
        "finished_at": _FIXED_FINISHED.isoformat(),
        "status": "success",
        "items": [
            {
                "id": "Microsoft.PowerShell",
                "name": "PowerShell",
                "category": "winget",
                "source": {"type": "winget", "feed": "winget", "url": None},
                "current_version": "7.6.0",
                "target_version": None,
                "resolved_version": None,
                "status": "up_to_date",
                "exit_code": 0,
                "duration_ms": 120,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
            {
                "id": "Microsoft.VisualStudioCode",
                "name": "Visual Studio Code",
                "category": "winget",
                "source": {"type": "winget", "feed": "winget", "url": None},
                "current_version": "1.95.0",
                "target_version": "1.96.0",
                "resolved_version": None,
                "status": "success",
                "exit_code": 0,
                "duration_ms": 250,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
        ],
        "summary": {
            "total": 2,
            "success": 1,
            "up_to_date": 1,
            "failed": 0,
            "skipped": 0,
            "planned": 0,
            "partial": 0,
            "duration_ms": 370,
            "exit_code": 0,
        },
        "messages": [],
    }


@pytest.fixture
def check_sidecar_payload(run_info: RunInfo, windows_host: HostInfo) -> dict[str, Any]:
    """A valid ascendo/v1 ``check__winget.json`` payload."""
    return _build_check_sidecar_payload(run_info, windows_host)

"""Pytest fixtures for adapters/windows/tests/.

Provides reusable HostInfo, RunInfo, and Sidecar payload builders used by
the smoke tests for :class:`WingetManager` and :class:`WindowsUpdateManager`.
Everything here is cross-platform - the unit suite must pass on the Linux
CI runner.
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


_FIXED_RUN_ID = UUID("12345678-1234-1234-1234-123456789abc")
_FIXED_STARTED = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
_FIXED_FINISHED = datetime(2026, 5, 1, 12, 0, 5, tzinfo=timezone.utc)


@pytest.fixture
def windows_host() -> HostInfo:
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
    phase_scripts = ("check.ps1", "plan.ps1", "apply.ps1", "verify.ps1", "cleanup.ps1")
    for category in ("winget", "windows_update"):
        d = tmp_path / "scripts" / category
        d.mkdir(parents=True)
        for phase_script in phase_scripts:
            (d / phase_script).write_text(
                f"# placeholder ({category}/{phase_script})\n",
                encoding="utf-8",
            )
    return tmp_path / "scripts"


@pytest.fixture
def fake_lib_dir(tmp_path: Path) -> Path:
    d = tmp_path / "lib"
    d.mkdir(parents=True)
    (d / "Common.ps1").write_text("# placeholder\n", encoding="utf-8")
    return d


def _build_check_sidecar_payload(run, host):
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
def check_sidecar_payload(run_info, windows_host):
    return _build_check_sidecar_payload(run_info, windows_host)


def _build_windows_update_sidecar_payload(run, host):
    return {
        "schema": "ascendo/v1",
        "run": json.loads(run.model_dump_json()),
        "host": json.loads(host.model_dump_json()),
        "tool": {
            "name": "pswindowsupdate",
            "version": "2.2.1.5",
            "binary_path": None,
        },
        "phase": Phase.CHECK.value,
        "category": "windows_update",
        "started_at": _FIXED_STARTED.isoformat(),
        "finished_at": _FIXED_FINISHED.isoformat(),
        "status": "success",
        "items": [
            {
                "id": "KB5034441",
                "name": "2024-01 Cumulative Update Preview for Windows 11",
                "category": "windows_update",
                "source": {"type": "windows_update", "feed": None, "url": None},
                "current_version": None,
                "target_version": None,
                "resolved_version": None,
                "status": "success",
                "exit_code": 0,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
            {
                "id": "KB5037997",
                "name": "Defender definition update",
                "category": "windows_update",
                "source": {"type": "windows_update", "feed": None, "url": None},
                "current_version": None,
                "target_version": None,
                "resolved_version": None,
                "status": "success",
                "exit_code": 0,
                "duration_ms": None,
                "evidence": None,
                "rollback": None,
                "messages": [],
            },
        ],
        "summary": {
            "total": 2,
            "success": 2,
            "up_to_date": 0,
            "failed": 0,
            "skipped": 0,
            "planned": 0,
            "partial": 0,
            "duration_ms": None,
            "exit_code": 0,
        },
        "messages": [
            {"level": "info", "text": "Found 2 pending Windows update(s).", "timestamp": None},
        ],
    }


@pytest.fixture
def windows_update_sidecar_payload(run_info, windows_host):
    return _build_windows_update_sidecar_payload(run_info, windows_host)
